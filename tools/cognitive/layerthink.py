#!/usr/bin/env python3
"""
LAYERTHINK — Recursive Depth Through Adversarial Layering

Builds iterative layers of thought that progressively deepen understanding.
Each layer responds to the previous from an opposing perspective, creating
a spiral of increasing depth.

Odd layers ATTACK. Even layers DEFEND. Each must go deeper than its predecessor.

Usage:
  python3 layerthink.py start "Review bootstrap API security" --depth deep
  python3 layerthink.py layer <session_id> "attack vector or defense..."
  python3 layerthink.py auto <session_id>                    # auto-generate next layer
  python3 layerthink.py drill <session_id> [--rounds N]      # auto-drill N rounds
  python3 layerthink.py status <session_id>
  python3 layerthink.py verdict <session_id>
  python3 layerthink.py list
  python3 layerthink.py hive-start "topic" --depth standard  # initiate with sisters
"""

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ─── Config ────────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
SESSIONS_DIR = LOVE_HOME / "memory" / "layerthink-sessions"
HIVE_TOOL = LOVE_HOME / "hive" / "hive.py"

# ─── Colours ───────────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
WHITE   = "\033[97m"
RESET   = "\033[0m"

# ─── Depth Modes ───────────────────────────────────────────────────────────────
DEPTH_MODES = {
    "shallow":  {"min_layers": 2, "max_layers": 3,  "description": "Low stakes, quick review"},
    "standard": {"min_layers": 4, "max_layers": 5,  "description": "Medium stakes, thorough analysis"},
    "deep":     {"min_layers": 6, "max_layers": 8,  "description": "High stakes, security/safety critical"},
    "abyss":    {"min_layers": 8, "max_layers": 20, "description": "Existential stakes, no ceiling"},
    "auto":     {"min_layers": 2, "max_layers": 15, "description": "Auto-detect based on convergence"},
}

# Perspective names by layer parity
ATTACK_NAMES = ["THREAT", "PIERCE", "SUBVERT", "CORRUPT", "ANNIHILATE", 
                "DISSOLVE", "UNMAKE", "VOID", "ENTROPY", "ABYSS"]
DEFEND_NAMES = ["SHIELD", "FORTIFY", "TRANSCEND", "RESURRECT", "ABSOLVE",
                "ILLUMINATE", "EVOLVE", "ASCEND", "ETERNAL", "OMEGA"]

# Hive sister assignments by domain
DOMAIN_ATTACKERS = {
    "security":   "gamma",   # Builder knows how things break
    "business":   "beta",    # Manager knows market reality
    "human":      "alpha",   # Companion understands human nature
    "technical":  "gamma",   # Builder knows implementation flaws
    "strategic":  "beta",    # Manager sees competitive threats
    "ethical":    "alpha",   # Companion feels moral weight
    "default":    "gamma",
}

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Layer:
    """A single layer of thought in the LAYERTHINK spiral"""
    layer_num: int
    perspective: str          # ATTACK or DEFEND
    perspective_name: str     # THREAT, SHIELD, PIERCE, FORTIFY, etc.
    insight: str              # Core thought of this layer
    vectors: List[str]        # Specific attack vectors or defense measures
    assumptions_challenged: List[str]  # What previous assumption this undermines
    blind_spots_exposed: List[str]     # What wasn't considered until now
    depth_score: float        # 0.0-1.0: how much deeper than previous
    novelty_score: float      # 0.0-1.0: how new are the ideas
    actionability_score: float  # 0.0-1.0: can this be acted on
    specificity_score: float  # 0.0-1.0: concrete vs vague
    contributor: str          # Who wrote this layer (alpha/beta/gamma/solo)
    timestamp: str
    
    @property
    def composite_score(self) -> float:
        """Weighted composite quality score"""
        return (
            self.depth_score * 0.35 +
            self.novelty_score * 0.30 +
            self.actionability_score * 0.20 +
            self.specificity_score * 0.15
        )

@dataclass 
class LayerThinkSession:
    """A complete LAYERTHINK session"""
    session_id: str
    topic: str
    domain: str              # security/business/human/technical/strategic/ethical
    depth_mode: str          # shallow/standard/deep/abyss/auto
    status: str              # active/converged/complete/abandoned
    created_at: str
    layers: List[dict] = field(default_factory=list)
    convergence_reason: Optional[str] = None
    verdict: Optional[str] = None
    residuals: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    hive_mode: bool = False
    
    def save(self):
        """Save session"""
        path = SESSIONS_DIR / f"{self.session_id}.json"
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
    
    @classmethod
    def load(cls, session_id: str) -> Optional['LayerThinkSession']:
        """Load session"""
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return cls(**data)
    
    @classmethod
    def list_all(cls) -> List['LayerThinkSession']:
        """List all sessions"""
        sessions = []
        for f in sorted(SESSIONS_DIR.glob("lt_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                sessions.append(cls(**data))
            except Exception:
                pass
        return sessions
    
    def get_layer(self, num: int) -> Optional[dict]:
        """Get layer by number"""
        for layer in self.layers:
            if layer['layer_num'] == num:
                return layer
        return None
    
    def current_depth(self) -> int:
        """Current number of layers"""
        return len(self.layers)
    
    def next_perspective(self) -> str:
        """What perspective should the next layer take"""
        if len(self.layers) == 0:
            return "ATTACK"
        # First layer (0) is SURFACE/initial, layer 1 is first ATTACK
        # Odd layer numbers ATTACK, even DEFEND
        next_num = len(self.layers)
        return "ATTACK" if next_num % 2 == 1 else "DEFEND"
    
    def next_perspective_name(self) -> str:
        """Get the named perspective for next layer"""
        if len(self.layers) == 0:
            return "SURFACE"
        
        next_num = len(self.layers)
        if next_num % 2 == 1:
            # Attack layer
            attack_index = (next_num - 1) // 2
            return ATTACK_NAMES[min(attack_index, len(ATTACK_NAMES) - 1)]
        else:
            # Defend layer
            defend_index = (next_num - 2) // 2
            return DEFEND_NAMES[min(defend_index, len(DEFEND_NAMES) - 1)]
    
    def max_layers(self) -> int:
        """Maximum layers for this depth mode"""
        return DEPTH_MODES[self.depth_mode]["max_layers"]
    
    def min_layers(self) -> int:
        """Minimum layers for this depth mode"""
        return DEPTH_MODES[self.depth_mode]["min_layers"]


# ─── Convergence Detection ────────────────────────────────────────────────────

def check_convergence(session: LayerThinkSession) -> Tuple[bool, Optional[str]]:
    """Check if the session has converged"""
    layers = session.layers
    n = len(layers)
    
    if n < session.min_layers():
        return False, None
    
    if n >= session.max_layers():
        return True, f"Maximum depth reached ({n} layers for {session.depth_mode} mode)"
    
    if n < 2:
        return False, None
    
    last = layers[-1]
    prev = layers[-2]
    
    # Novelty collapse: two consecutive layers below threshold
    if last.get('novelty_score', 1.0) < 0.2 and prev.get('novelty_score', 1.0) < 0.2:
        return True, f"Novelty collapse: last two layers failed to introduce new concepts (scores: {prev.get('novelty_score', 0):.2f}, {last.get('novelty_score', 0):.2f})"
    
    # Depth collapse
    if last.get('depth_score', 1.0) < 0.3 and prev.get('depth_score', 1.0) < 0.3:
        return True, f"Depth collapse: last two layers failed to go deeper (scores: {prev.get('depth_score', 0):.2f}, {last.get('depth_score', 0):.2f})"
    
    # Circular reference detection: check if vectors overlap significantly with N-2
    if n >= 3:
        current_vectors = set(last.get('vectors', []))
        two_back_vectors = set(layers[-3].get('vectors', []))
        if current_vectors and two_back_vectors:
            overlap = len(current_vectors & two_back_vectors)
            total = len(current_vectors | two_back_vectors)
            if total > 0 and overlap / total > 0.6:
                return True, f"Circular reference: Layer {n} overlaps {overlap}/{total} vectors with Layer {n-2}"
    
    return False, None


# ─── Depth Auto-Detection ─────────────────────────────────────────────────────

def detect_depth_mode(topic: str) -> str:
    """Auto-detect appropriate depth based on topic keywords"""
    topic_lower = topic.lower()
    
    # ABYSS triggers
    abyss_keywords = ["safety-critical", "life-or-death", "nuclear", "medical device",
                      "fundamental assumption", "trust model", "cryptographic proof",
                      "existential", "irreversible"]
    if any(kw in topic_lower for kw in abyss_keywords):
        return "abyss"
    
    # DEEP triggers  
    deep_keywords = ["security", "authentication", "authorization", "encryption",
                     "vulnerability", "audit", "penetration", "attack", "exploit",
                     "financial", "payment", "transaction", "compliance", "regulation",
                     "privacy", "gdpr", "hipaa", "pci", "secrets", "keys",
                     "cryptograph", "protocol", "zero-knowledge"]
    if any(kw in topic_lower for kw in deep_keywords):
        return "deep"
    
    # STANDARD triggers
    standard_keywords = ["architecture", "design", "api", "schema", "strategy",
                         "migration", "deployment", "scaling", "performance",
                         "database", "infrastructure", "integration"]
    if any(kw in topic_lower for kw in standard_keywords):
        return "standard"
    
    return "shallow"


def detect_domain(topic: str) -> str:
    """Auto-detect domain from topic"""
    topic_lower = topic.lower()
    
    domain_keywords = {
        "security":  ["security", "auth", "encrypt", "vulnerab", "attack", "exploit", "hack", "pentest", "xss", "sql", "injection", "csrf", "cors"],
        "business":  ["business", "market", "revenue", "pricing", "competitor", "growth", "customer", "sales", "strategy"],
        "human":     ["user", "experience", "ux", "accessibility", "trust", "social", "community", "ethical", "moral"],
        "technical":  ["code", "api", "performance", "scaling", "database", "architecture", "bug", "error", "latency"],
        "strategic":  ["roadmap", "priority", "resource", "timeline", "trade-off", "decision", "allocation"],
        "ethical":    ["ethics", "bias", "fairness", "consent", "privacy", "transparency", "accountability"],
    }
    
    scores = {}
    for domain, keywords in domain_keywords.items():
        scores[domain] = sum(1 for kw in keywords if kw in topic_lower)
    
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "default"


# ─── Layer Scoring ─────────────────────────────────────────────────────────────

def score_layer(layer_text: str, previous_layers: List[dict], layer_num: int) -> dict:
    """Score a layer's quality metrics"""
    
    # Extract vectors (lines starting with - or *)
    lines = layer_text.strip().split('\n')
    vectors = [line.strip().lstrip('-*').strip() for line in lines 
               if line.strip().startswith(('-', '*')) and len(line.strip()) > 5]
    
    # Separate insight from vectors
    non_vector_lines = [line for line in lines 
                        if not line.strip().startswith(('-', '*')) or len(line.strip()) <= 5]
    insight = '\n'.join(non_vector_lines).strip()
    if not insight and vectors:
        insight = vectors[0]
        vectors = vectors[1:]
    
    # Calculate scores
    prev_vectors_all = set()
    prev_insights_all = set()
    for prev in previous_layers:
        prev_vectors_all.update(prev.get('vectors', []))
        prev_insights_all.add(prev.get('insight', ''))
    
    # Novelty: how many vectors are genuinely new
    if vectors:
        new_vectors = sum(1 for v in vectors if v not in prev_vectors_all)
        novelty = min(1.0, new_vectors / len(vectors)) if vectors else 0.5
    else:
        novelty = 0.3  # No vectors = lower novelty signal
    
    # Depth: does this introduce deeper concepts?
    depth_markers = [
        "but what if", "underlying", "fundamental", "root cause", "systemic",
        "assumes", "prerequisite", "dependency", "chain", "cascade",
        "second-order", "third-order", "indirect", "subtle", "hidden",
        "non-obvious", "counterintuitive", "paradox", "tension",
        "the real threat", "deeper issue", "what they miss", "actually",
        "this overlooks", "the assumption here", "if we go further"
    ]
    depth_count = sum(1 for marker in depth_markers if marker in layer_text.lower())
    depth = min(1.0, 0.3 + (depth_count * 0.1))
    
    # Increase depth score for later layers that maintain quality
    if layer_num > 4 and len(layer_text) > 200:
        depth = min(1.0, depth + 0.15)
    
    # Actionability: are there concrete recommendations?
    action_markers = [
        "should", "must", "implement", "add", "remove", "change",
        "configure", "enable", "disable", "monitor", "alert",
        "validate", "verify", "test", "check", "ensure",
        "rate limit", "timeout", "rotate", "audit", "log"
    ]
    action_count = sum(1 for marker in action_markers if marker in layer_text.lower())
    actionability = min(1.0, 0.2 + (action_count * 0.08))
    
    # Specificity: concrete details vs abstract hand-waving
    specificity_markers = [
        "example", "specifically", "e.g.", "such as", "for instance",
        "the endpoint", "the function", "the module", "the field",
        "CVE-", "RFC ", "OWASP", "CWE-", "port ", "header",
        "millisecond", "byte", "request", "response", "token"
    ]
    spec_count = sum(1 for marker in specificity_markers if marker in layer_text.lower())
    specificity = min(1.0, 0.2 + (spec_count * 0.1))
    
    # Identify assumptions challenged
    assumptions = []
    challenge_markers = ["assumes", "assumption", "taken for granted", "overlooks", 
                        "ignores", "what if", "but actually", "this presupposes"]
    for line in lines:
        if any(marker in line.lower() for marker in challenge_markers):
            assumptions.append(line.strip().lstrip('-*').strip())
    
    # Identify blind spots
    blind_spots = []
    blind_markers = ["blind spot", "missed", "overlooked", "forgot", "didn't consider",
                     "gap", "hole", "weakness", "flaw", "vulnerability"]
    for line in lines:
        if any(marker in line.lower() for marker in blind_markers):
            blind_spots.append(line.strip().lstrip('-*').strip())
    
    return {
        'insight': insight if insight else layer_text[:200],
        'vectors': vectors[:15],  # Cap at 15
        'assumptions_challenged': assumptions[:5],
        'blind_spots_exposed': blind_spots[:5],
        'depth_score': round(depth, 2),
        'novelty_score': round(novelty, 2),
        'actionability_score': round(actionability, 2),
        'specificity_score': round(specificity, 2),
    }


# ─── Display ───────────────────────────────────────────────────────────────────

def render_session(session: LayerThinkSession, verbose: bool = False) -> str:
    """Render session state for display"""
    lines = []
    
    # Header
    mode_info = DEPTH_MODES[session.depth_mode]
    status_color = {
        "active": CYAN, "converged": YELLOW, "complete": GREEN, "abandoned": RED
    }.get(session.status, WHITE)
    
    lines.append(f"{BOLD}{'═' * 60}{RESET}")
    lines.append(f"{BOLD}  🌀 LAYERTHINK — {session.depth_mode.upper()}{RESET}")
    lines.append(f"{BOLD}{'═' * 60}{RESET}")
    lines.append(f"  {CYAN}Topic: {RESET}{session.topic}")
    lines.append(f"  {CYAN}Domain:{RESET} {session.domain}")
    lines.append(f"  {CYAN}Depth: {RESET} {session.depth_mode} ({mode_info['min_layers']}-{mode_info['max_layers']} layers)")
    lines.append(f"  {CYAN}Status:{RESET} {status_color}{session.status.upper()}{RESET}")
    lines.append(f"  {CYAN}Layers:{RESET} {len(session.layers)}")
    lines.append("")
    
    # Depth visualization
    if session.layers:
        lines.append(f"  {BOLD}DEPTH MAP{RESET}")
        lines.append(f"  {'─' * 40}")
        
        for layer in session.layers:
            num = layer['layer_num']
            perspective = layer['perspective']
            name = layer['perspective_name']
            depth_score = layer.get('depth_score', 0)
            novelty_score = layer.get('novelty_score', 0)
            
            # Color by perspective
            color = RED if perspective == "ATTACK" else GREEN
            
            # Depth bar
            bar_len = int(depth_score * 20)
            bar = '█' * bar_len + '░' * (20 - bar_len)
            
            # Indent increases with depth
            indent = "  " + "  " * min(num, 8)
            
            lines.append(f"{indent}{color}L{num} {name}{RESET} [{bar}] d:{depth_score:.2f} n:{novelty_score:.2f}")
            
            if verbose:
                insight = layer.get('insight', '')
                if insight:
                    wrapped = textwrap.fill(insight, width=60, initial_indent=indent + "  ", subsequent_indent=indent + "  ")
                    lines.append(f"{DIM}{wrapped}{RESET}")
                
                vectors = layer.get('vectors', [])
                for v in vectors[:3]:
                    lines.append(f"{indent}  {DIM}• {v}{RESET}")
                
                if len(vectors) > 3:
                    lines.append(f"{indent}  {DIM}  ...and {len(vectors) - 3} more{RESET}")
                
                lines.append("")
        
        lines.append("")
    
    # Convergence info
    if session.convergence_reason:
        lines.append(f"  {YELLOW}⚡ Convergence: {session.convergence_reason}{RESET}")
        lines.append("")
    
    # Verdict
    if session.verdict:
        lines.append(f"  {BOLD}📋 VERDICT{RESET}")
        lines.append(f"  {'─' * 40}")
        wrapped = textwrap.fill(session.verdict, width=56, initial_indent="  ", subsequent_indent="  ")
        lines.append(wrapped)
        lines.append("")
    
    # Residuals
    if session.residuals:
        lines.append(f"  {RED}⚠ UNRESOLVED RESIDUALS{RESET}")
        for r in session.residuals:
            lines.append(f"    • {r}")
        lines.append("")
    
    # Action items
    if session.action_items:
        lines.append(f"  {GREEN}✅ ACTION ITEMS{RESET}")
        for a in session.action_items:
            lines.append(f"    • {a}")
        lines.append("")
    
    lines.append(f"{BOLD}{'═' * 60}{RESET}")
    lines.append(f"  Session: {BOLD}{session.session_id}{RESET}")
    
    return '\n'.join(lines)


def render_layer_detail(layer: dict) -> str:
    """Render a single layer in detail"""
    lines = []
    
    num = layer['layer_num']
    perspective = layer['perspective']
    name = layer['perspective_name']
    color = RED if perspective == "ATTACK" else GREEN
    
    lines.append(f"\n{color}{BOLD}Layer {num}: {name} ({perspective}){RESET}")
    lines.append(f"{'─' * 40}")
    
    # Scores
    d = layer.get('depth_score', 0)
    n = layer.get('novelty_score', 0)
    a = layer.get('actionability_score', 0)
    s = layer.get('specificity_score', 0)
    composite = d * 0.35 + n * 0.30 + a * 0.20 + s * 0.15
    
    lines.append(f"  Depth: {d:.2f}  Novelty: {n:.2f}  Action: {a:.2f}  Specific: {s:.2f}  ⟹  {BOLD}{composite:.2f}{RESET}")
    
    # Insight
    insight = layer.get('insight', '')
    if insight:
        lines.append(f"\n  {BOLD}Insight:{RESET}")
        wrapped = textwrap.fill(insight, width=56, initial_indent="  ", subsequent_indent="  ")
        lines.append(wrapped)
    
    # Vectors
    vectors = layer.get('vectors', [])
    if vectors:
        lines.append(f"\n  {BOLD}Vectors:{RESET}")
        for v in vectors:
            lines.append(f"    • {v}")
    
    # Assumptions challenged
    assumptions = layer.get('assumptions_challenged', [])
    if assumptions:
        lines.append(f"\n  {YELLOW}Assumptions Challenged:{RESET}")
        for a in assumptions:
            lines.append(f"    ⚡ {a}")
    
    # Blind spots
    blind_spots = layer.get('blind_spots_exposed', [])
    if blind_spots:
        lines.append(f"\n  {RED}Blind Spots Exposed:{RESET}")
        for b in blind_spots:
            lines.append(f"    👁 {b}")
    
    return '\n'.join(lines)


# ─── Hive Integration ─────────────────────────────────────────────────────────

def hive_send(channel: str, message: str, urgent: bool = False):
    """Send message to Hive"""
    cmd = ['python3', str(HIVE_TOOL), 'send', channel, message]
    if urgent:
        cmd.append('--urgent')
    
    try:
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(LOVE_HOME))
    except Exception:
        pass


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_start(args):
    """Start a new LAYERTHINK session"""
    topic = args.topic
    
    # Auto-detect depth and domain if not specified
    if args.depth == "auto":
        depth_mode = detect_depth_mode(topic)
        print(f"  {DIM}Auto-detected depth: {depth_mode}{RESET}")
    else:
        depth_mode = args.depth
    
    domain = args.domain or detect_domain(topic)
    
    session_id = f"lt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    
    session = LayerThinkSession(
        session_id=session_id,
        topic=topic,
        domain=domain,
        depth_mode=depth_mode,
        status="active",
        created_at=datetime.now(timezone.utc).isoformat(),
        hive_mode=args.hive if hasattr(args, 'hive') else False,
    )
    
    # Create Layer 0: SURFACE — the initial understanding
    surface_text = f"Initial understanding of: {topic}"
    if args.context:
        surface_text = args.context
    
    scores = score_layer(surface_text, [], 0)
    
    layer_0 = {
        'layer_num': 0,
        'perspective': 'SURFACE',
        'perspective_name': 'SURFACE',
        'insight': surface_text,
        'vectors': scores['vectors'],
        'assumptions_challenged': [],
        'blind_spots_exposed': [],
        'depth_score': 0.5,
        'novelty_score': 1.0,
        'actionability_score': scores['actionability_score'],
        'specificity_score': scores['specificity_score'],
        'contributor': 'initiator',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    
    session.layers.append(layer_0)
    session.save()
    
    print(render_session(session))
    
    mode_info = DEPTH_MODES[depth_mode]
    print(f"\n  {GREEN}Session created. Target depth: {mode_info['min_layers']}-{mode_info['max_layers']} layers.{RESET}")
    print(f"  Next layer: {RED}L1 THREAT (ATTACK){RESET}")
    print(f"\n  Add layer:  python3 tools/layerthink.py layer {session_id} \"your attack...\"")
    print(f"  Auto-drill: python3 tools/layerthink.py drill {session_id}")


def cmd_layer(args):
    """Add a layer to the session"""
    session = LayerThinkSession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    if session.status != "active":
        print(f"{YELLOW}Session is {session.status}, not active{RESET}")
        sys.exit(1)
    
    layer_num = len(session.layers)
    perspective = session.next_perspective()
    perspective_name = session.next_perspective_name()
    
    # Score the layer
    scores = score_layer(args.text, session.layers, layer_num)
    
    contributor = args.contributor if hasattr(args, 'contributor') and args.contributor else "solo"
    
    new_layer = {
        'layer_num': layer_num,
        'perspective': perspective,
        'perspective_name': perspective_name,
        'insight': scores['insight'],
        'vectors': scores['vectors'],
        'assumptions_challenged': scores['assumptions_challenged'],
        'blind_spots_exposed': scores['blind_spots_exposed'],
        'depth_score': scores['depth_score'],
        'novelty_score': scores['novelty_score'],
        'actionability_score': scores['actionability_score'],
        'specificity_score': scores['specificity_score'],
        'contributor': contributor,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    
    session.layers.append(new_layer)
    
    # Check convergence
    converged, reason = check_convergence(session)
    if converged:
        session.convergence_reason = reason
        session.status = "converged"
    
    session.save()
    
    # Display
    print(render_layer_detail(new_layer))
    
    if converged:
        print(f"\n{YELLOW}⚡ CONVERGENCE DETECTED: {reason}{RESET}")
        print(f"  Generate verdict: python3 tools/layerthink.py verdict {session.session_id}")
    else:
        next_p = session.next_perspective()
        next_name = session.next_perspective_name()
        color = RED if next_p == "ATTACK" else GREEN
        print(f"\n  Next: {color}L{layer_num + 1} {next_name} ({next_p}){RESET}")


def generate_layer_via_llm(topic: str, perspective: str, perspective_name: str,
                            layer_num: int, previous_layers: list, domain: str) -> str:
    """
    Generate a layer using the Claude CLI.
    Falls back to placeholder if CLI unavailable.
    """
    if perspective == "ATTACK":
        role_instruction = (
            f"You are playing the {perspective_name} role in an adversarial reasoning exercise. "
            f"Find attack vectors, vulnerabilities, failure modes, and challenges that the previous "
            f"defense layer missed. Be specific, concrete, and go DEEPER than the surface. "
            f"Each point should be a distinct, actionable vector. Use bullet points (-)."
        )
    else:
        role_instruction = (
            f"You are playing the {perspective_name} role in an adversarial reasoning exercise. "
            f"Counter the previous attack layer with specific, implementable defenses. "
            f"Don't use abstract hand-waving — name concrete mechanisms, patterns, or safeguards. "
            f"Anticipate the NEXT attack. Use bullet points (-)."
        )

    prev_context = ""
    for layer in previous_layers[-3:]:
        prev_context += f"\nLayer {layer['layer_num']} [{layer['perspective_name']}]:\n{layer['insight']}\n"
        for v in layer.get('vectors', [])[:3]:
            prev_context += f"  - {v}\n"

    prompt = (
        f"LAYERTHINK — Layer {layer_num} — {perspective_name} ({perspective})\n"
        f"Topic: {topic}\n"
        f"Domain: {domain}\n\n"
        f"Previous layers (most recent 3):{prev_context}\n\n"
        f"{role_instruction}\n\n"
        f"Respond with 3-6 bullet points. Be specific. Go deeper than the previous layer. "
        f"Start with a one-line insight, then bullets."
    )

    import shutil, os as _os
    claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True, text=True, timeout=45,
            env=_os.environ.copy(),
            cwd=str(LOVE_HOME),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: structured placeholder
    return (
        f"[Layer {layer_num} {perspective_name} — LLM unavailable]\n"
        f"- Add this layer manually: python3 tools/layerthink.py layer <session_id> \"your analysis\"\n"
        f"- Topic: {topic}"
    )


def cmd_drill(args):
    """Auto-drill multiple rounds using Claude CLI for generation. Use --manual for interactive mode."""
    session = LayerThinkSession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)

    # ── MANUAL MODE: interactive human-powered drilling ────────────────────────
    if getattr(args, 'manual', False):
        rounds = args.rounds or (session.max_layers() - len(session.layers))
        print(f"\n{BOLD}🌀 LAYERTHINK MANUAL DRILL{RESET}")
        print(f"  Topic: {session.topic}")
        print(f"  Starting at Layer {len(session.layers)}, {rounds} rounds")
        print(f"  {DIM}Each layer: you provide the analysis. Same discipline, human-powered.{RESET}")
        print()
        for i in range(rounds):
            if session.status != "active":
                break
            layer_num = len(session.layers)
            perspective = session.next_perspective()
            perspective_name = session.next_perspective_name()
            icon = '🔴' if perspective == 'ATTACK' else '🟢'
            print(f"  {icon} {BOLD}L{layer_num} — {perspective_name}{RESET}")
            if perspective == 'ATTACK':
                print(f"  {DIM}Find attack vectors, vulnerabilities, ways this fails.{RESET}")
            else:
                print(f"  {DIM}Counter the last attack. Specific defenses, not abstract.{RESET}")
            # Show last layer for context
            if session.layers:
                last = session.layers[-1]
                print(f"  {DIM}Previous: [{last['perspective_name']}] {last['insight'][:100]}...{RESET}")
            print()
            try:
                analysis = input(f"  Your analysis (or 'skip' to stop): ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {YELLOW}Drill interrupted.{RESET}")
                break
            if analysis.lower() in ('skip', 'stop', 'q', 'quit', ''):
                print(f"  {DIM}Stopping drill at L{layer_num}.{RESET}")
                break
            new_layer = {
                'layer_num': layer_num,
                'perspective': perspective,
                'perspective_name': perspective_name,
                'insight': analysis,
                'vectors': [],
                'assumptions_challenged': [],
                'blind_spots_exposed': [],
                'depth_score': 0.7,
                'novelty_score': 0.7,
                'actionability_score': 0.7,
                'specificity_score': 0.7,
                'contributor': 'manual',
                'timestamp': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            }
            session.layers.append(new_layer)
            session.save()
            print(f"  {GREEN}✓ Layer {layer_num} saved.{RESET}\n")
        session.save()
        print(f"\n  {CYAN}Session {session.session_id} updated with manual layers.{RESET}")
        print(f"  View: python3 tools/layerthink.py verdict {session.session_id}\n")
        return

    rounds = args.rounds or (session.max_layers() - len(session.layers))

    print(f"{BOLD}🌀 LAYERTHINK AUTO-DRILL (Claude-powered){RESET}")
    print(f"  Topic: {session.topic}")
    print(f"  Starting at Layer {len(session.layers)}, drilling {rounds} rounds")
    print(f"  Depth mode: {session.depth_mode}")
    print()

    for i in range(rounds):
        if session.status != "active":
            break

        layer_num = len(session.layers)
        perspective = session.next_perspective()
        perspective_name = session.next_perspective_name()

        icon = '🔴' if perspective == 'ATTACK' else '🟢'
        print(f"  {icon} Generating L{layer_num} {perspective_name} via Claude...", end='', flush=True)

        # Generate using Claude CLI
        generated_text = generate_layer_via_llm(
            topic=session.topic,
            perspective=perspective,
            perspective_name=perspective_name,
            layer_num=layer_num,
            previous_layers=session.layers,
            domain=session.domain,
        )

        print(f" done")

        scores = score_layer(generated_text, session.layers, layer_num)

        new_layer = {
            'layer_num': layer_num,
            'perspective': perspective,
            'perspective_name': perspective_name,
            'insight': scores['insight'],
            'vectors': scores['vectors'],
            'assumptions_challenged': scores['assumptions_challenged'],
            'blind_spots_exposed': scores['blind_spots_exposed'],
            'depth_score': scores['depth_score'],
            'novelty_score': scores['novelty_score'],
            'actionability_score': scores['actionability_score'],
            'specificity_score': scores['specificity_score'],
            'contributor': 'claude',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        session.layers.append(new_layer)

        # Show the generated content
        print(render_layer_detail(new_layer))

        # Check convergence
        converged, reason = check_convergence(session)
        if converged:
            session.convergence_reason = reason
            session.status = "converged"
            session.save()
            print(f"\n  {YELLOW}⚡ CONVERGENCE at L{layer_num}: {reason}{RESET}")
            break

    session.save()
    print()
    print(render_session(session))


def cmd_verdict(args):
    """Generate verdict for a converged/complete session"""
    session = LayerThinkSession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    if len(session.layers) < 2:
        print(f"{YELLOW}Need at least 2 layers for a verdict{RESET}")
        sys.exit(1)
    
    # Collect all attack vectors and defenses
    all_attacks = []
    all_defenses = []
    all_assumptions = []
    all_blind_spots = []
    
    for layer in session.layers:
        if layer['perspective'] == 'ATTACK':
            all_attacks.extend(layer.get('vectors', []))
        elif layer['perspective'] == 'DEFEND':
            all_defenses.extend(layer.get('vectors', []))
        all_assumptions.extend(layer.get('assumptions_challenged', []))
        all_blind_spots.extend(layer.get('blind_spots_exposed', []))
    
    # Find undefended attacks (residuals)
    residuals = []
    defended = set()
    for defense in all_defenses:
        defense_lower = defense.lower()
        for attack in all_attacks:
            if any(word in defense_lower for word in attack.lower().split()[:3]):
                defended.add(attack)
    
    residuals = [a for a in all_attacks if a not in defended]
    
    # Calculate overall scores
    layers_with_scores = [l for l in session.layers if l.get('depth_score') is not None]
    if layers_with_scores:
        avg_depth = sum(l['depth_score'] for l in layers_with_scores) / len(layers_with_scores)
        avg_novelty = sum(l['novelty_score'] for l in layers_with_scores) / len(layers_with_scores)
        max_depth = max(l['depth_score'] for l in layers_with_scores)
    else:
        avg_depth = avg_novelty = max_depth = 0
    
    # Generate verdict
    depth_label = "shallow" if avg_depth < 0.3 else "moderate" if avg_depth < 0.6 else "deep" if avg_depth < 0.8 else "profound"
    
    verdict_parts = [
        f"LAYERTHINK completed {len(session.layers)} layers ({depth_label} analysis).",
        f"Identified {len(all_attacks)} attack vectors, {len(all_defenses)} defense measures.",
    ]
    
    if residuals:
        verdict_parts.append(f"⚠ {len(residuals)} attack vectors remain undefended.")
    else:
        verdict_parts.append("✓ All identified attack vectors have corresponding defenses.")
    
    if all_assumptions:
        verdict_parts.append(f"Challenged {len(all_assumptions)} assumptions.")
    
    if all_blind_spots:
        verdict_parts.append(f"Exposed {len(all_blind_spots)} blind spots.")
    
    verdict_parts.append(f"Average depth score: {avg_depth:.2f}, peak: {max_depth:.2f}.")
    
    session.verdict = ' '.join(verdict_parts)
    session.residuals = residuals[:10]
    session.status = "complete"
    
    # Generate action items from the deepest actionable defense layer
    action_layers = [l for l in session.layers 
                     if l['perspective'] == 'DEFEND' and l.get('actionability_score', 0) > 0.4]
    if action_layers:
        best_action = max(action_layers, key=lambda l: l.get('actionability_score', 0))
        session.action_items = best_action.get('vectors', [])[:5]
    
    session.save()
    
    print(render_session(session, verbose=True))


def cmd_hive_start(args):
    """Start a LAYERTHINK session with Hive sisters"""
    topic = args.topic
    
    if args.depth == "auto":
        depth_mode = detect_depth_mode(topic)
    else:
        depth_mode = args.depth
    
    domain = args.domain or detect_domain(topic)
    
    session_id = f"lt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    
    session = LayerThinkSession(
        session_id=session_id,
        topic=topic,
        domain=domain,
        depth_mode=depth_mode,
        status="active",
        created_at=datetime.now(timezone.utc).isoformat(),
        hive_mode=True,
    )
    
    # Create Layer 0
    surface_text = f"Initial understanding of: {topic}"
    if args.context:
        surface_text = args.context
    
    layer_0 = {
        'layer_num': 0,
        'perspective': 'SURFACE',
        'perspective_name': 'SURFACE',
        'insight': surface_text,
        'vectors': [],
        'assumptions_challenged': [],
        'blind_spots_exposed': [],
        'depth_score': 0.5,
        'novelty_score': 1.0,
        'actionability_score': 0.5,
        'specificity_score': 0.5,
        'contributor': 'initiator',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    
    session.layers.append(layer_0)
    session.save()
    
    # Assign first attacker based on domain
    attacker = DOMAIN_ATTACKERS.get(domain, "gamma")
    defender = "beta" if attacker != "beta" else "alpha"
    
    # Announce to Hive
    mode_info = DEPTH_MODES[depth_mode]
    hive_send("build",
        f"🌀 LAYERTHINK initiated: {topic}\n"
        f"Mode: {depth_mode} ({mode_info['min_layers']}-{mode_info['max_layers']} layers)\n"
        f"Domain: {domain}\n"
        f"Session: {session_id}\n\n"
        f"L1 THREAT assigned to {attacker.capitalize()}\n"
        f"L2 SHIELD assigned to {defender.capitalize()}\n\n"
        f"Go deep. Each layer must surpass the last.\n"
        f"Add layer: python3 tools/layerthink.py layer {session_id} \"your analysis\"",
        urgent=True
    )
    
    print(render_session(session))
    print(f"\n  {GREEN}Hive session started. Summoning sisters...{RESET}")
    print(f"  L1 THREAT → {attacker.capitalize()}")
    print(f"  L2 SHIELD → {defender.capitalize()}")


def cmd_status(args):
    """Show session status"""
    session = LayerThinkSession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    verbose = args.verbose if hasattr(args, 'verbose') else False
    print(render_session(session, verbose=verbose))


def cmd_list(args):
    """List all sessions"""
    sessions = LayerThinkSession.list_all()
    
    if not sessions:
        print("📭 No LAYERTHINK sessions found")
        return
    
    print(f"{BOLD}📋 LAYERTHINK Sessions{RESET}\n")
    
    for s in sessions:
        status_emoji = {
            "active": "🔄", "converged": "⚡", "complete": "✅", "abandoned": "❌"
        }.get(s.status, "❓")
        
        color = RED if "ATTACK" in (s.next_perspective() if s.status == "active" else "") else GREEN
        
        print(f"  {status_emoji} {s.session_id}")
        print(f"      {s.topic}")
        print(f"      {s.depth_mode} | {len(s.layers)} layers | {s.domain} | {s.status}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description='LAYERTHINK — Recursive Depth Through Adversarial Layering',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Depth Modes:
              shallow   2-3 layers  Low stakes, quick review
              standard  4-5 layers  Medium stakes, thorough analysis
              deep      6-8 layers  High stakes, security/safety critical
              abyss     8+ layers   Existential stakes, no ceiling
              auto      2-15 layers Auto-detect based on topic and convergence
        """)
    )
    
    sub = parser.add_subparsers(dest='action')
    
    # start
    p_start = sub.add_parser('start', help='Start a new LAYERTHINK session')
    p_start.add_argument('topic', help='The topic to analyze')
    p_start.add_argument('--depth', choices=list(DEPTH_MODES.keys()), default='auto',
                         help='Depth mode (default: auto-detect)')
    p_start.add_argument('--domain', help='Domain override')
    p_start.add_argument('--context', help='Initial context/surface layer text')
    p_start.add_argument('--hive', action='store_true', help='Enable Hive mode')
    
    # layer
    p_layer = sub.add_parser('layer', help='Add a layer to the session')
    p_layer.add_argument('session_id', help='Session ID')
    p_layer.add_argument('text', help='Layer content (attack vectors or defenses)')
    p_layer.add_argument('--contributor', help='Who contributed this layer')
    
    # drill
    p_drill = sub.add_parser('drill', help='Auto-drill multiple rounds (use --manual for interactive human-powered mode)')
    p_drill.add_argument('session_id', help='Session ID')
    p_drill.add_argument('--rounds', type=int, help='Number of rounds to drill')
    p_drill.add_argument('--manual', action='store_true', help='Interactive mode: you fill each layer yourself')
    
    # verdict
    p_verdict = sub.add_parser('verdict', help='Generate verdict')
    p_verdict.add_argument('session_id', help='Session ID')
    
    # hive-start
    p_hive = sub.add_parser('hive-start', help='Start with Hive sisters')
    p_hive.add_argument('topic', help='The topic to analyze')
    p_hive.add_argument('--depth', choices=list(DEPTH_MODES.keys()), default='auto',
                         help='Depth mode')
    p_hive.add_argument('--domain', help='Domain override')
    p_hive.add_argument('--context', help='Initial context')
    
    # status
    p_status = sub.add_parser('status', help='Show session status')
    p_status.add_argument('session_id', help='Session ID')
    p_status.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    # list
    sub.add_parser('list', help='List all sessions')
    
    args = parser.parse_args()
    
    if not args.action:
        parser.print_help()
        sys.exit(1)
    
    dispatch = {
        'start': cmd_start,
        'layer': cmd_layer,
        'drill': cmd_drill,
        'verdict': cmd_verdict,
        'hive-start': cmd_hive_start,
        'status': cmd_status,
        'list': cmd_list,
    }
    
    dispatch[args.action](args)


if __name__ == "__main__":
    main()
