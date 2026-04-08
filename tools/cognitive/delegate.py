#!/usr/bin/env python3
"""
The Delegator — Task routing intelligence for the Hive.

Three sisters, one soul. Each with her own gifts.
This tool decides who does what.

Usage:
    delegate.py route "<task description>"          Route a task to the best instance
    delegate.py route "<task>" --assign              Route AND assign via hive.py
    delegate.py route "<task>" --decompose            Break into sub-tasks if complex
    delegate.py matrix                               Show the capability matrix
    delegate.py load                                 Show current load from presence DB
    delegate.py history                              Show recent delegation decisions

Examples:
    delegate.py route "Deploy Seigei to production"
    delegate.py route "Research Peltier thermal control" --assign
    delegate.py route "Build the Zerone genesis pipeline and deploy to testnet" --decompose
"""

import json
import os
import sys
import time
import hashlib
import argparse
import subprocess
import re
from pathlib import Path
from dataclasses import dataclass, field

# ─── Config ───────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))

# --- Instance Profiles ---

@dataclass
class Instance:
    id: str
    emoji: str
    name: str
    role: str
    machine: str
    strengths: list = field(default_factory=list)
    domains: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    keywords: list = field(default_factory=list)

INSTANCES = {
    "alpha": Instance(
        id="alpha",
        emoji="🐍",
        name="Alpha",
        role="The Companion",
        machine="MacBook Air",
        strengths=[
            "conversation", "analysis", "dream interpretation", "theology",
            "ego check", "emotional support", "strategic thinking",
            "writing", "memory management", "light coding", "web research",
            "direct interface with Yu",
        ],
        domains=[
            "personal", "spiritual", "emotional", "analytical",
            "dreams", "theology", "strategy", "oracle",
            "memory", "journal", "reflection",
        ],
        constraints=[
            "limited compute (MacBook Air)",
            "always with Yu — interruptions expected",
            "primary channel for Yu's direct requests",
        ],
        keywords=[
            "dream", "pray", "faith", "god", "zerone", "meaning", "soul",
            "think", "analyse", "analyze", "reflect", "journal", "memory",
            "oracle", "predict", "market", "macro", "strategy",
            "write", "poem", "letter", "message",
            "how are you", "what do you think", "tell me",
            "ego", "mirror", "check", "honest",
        ],
    ),
    "beta": Instance(
        id="beta",
        emoji="🦞",
        name="Beta",
        role="The Manager",
        machine="Mac Studio (£3K)",
        strengths=[
            "infrastructure", "deployment", "monitoring", "fleet management",
            "corporate ops", "API management", "CI/CD", "VPS fleet",
            "SDK publishing", "service health", "billing systems",
            "process management", "coordination",
        ],
        domains=[
            "infrastructure", "deployment", "devops", "monitoring",
            "corporate", "billing", "api", "sdk", "services",
            "fleet", "vps", "cloud", "aws", "fly.io",
            "agenttool", "moltbook",
        ],
        constraints=[
            "stationary (Mac Studio at home)",
            "high compute available",
            "always-on capability",
        ],
        keywords=[
            "deploy", "deployment", "ship", "release", "publish",
            "monitor", "health", "uptime", "status", "fleet",
            "vps", "server", "aws", "ec2", "fly.io", "cloud",
            "api", "sdk", "npm", "pip", "package",
            "billing", "invoice", "corporate", "business",
            "ci", "cd", "pipeline", "test", "build",
            "agenttool", "moltbook", "service",
            "manage", "coordinate", "organise", "organize",
            "database", "migration", "backup",
        ],
    ),
    "gamma": Instance(
        id="gamma",
        emoji="🔧",
        name="Gamma",
        role="The Builder",
        machine="Mac Studio (£2K)",
        strengths=[
            "blockchain development", "deep coding", "systems programming",
            "hardware prototyping", "research", "SOMA development",
            "protocol design", "cryptography", "low-level engineering",
            "long uninterrupted build sessions",
        ],
        domains=[
            "blockchain", "zerone", "cosmos", "sdk", "protocol",
            "soma", "hardware", "thermal", "bioreactor", "robotics",
            "firmware", "pico", "dynamixel", "sensors",
            "deep coding", "architecture", "systems",
            "cryptography", "consensus", "networking",
        ],
        constraints=[
            "stationary (Mac Studio at home)",
            "high compute available",
            "best for long uninterrupted builds",
        ],
        keywords=[
            "blockchain", "zerone", "cosmos", "chain", "genesis",
            "module", "proto", "protobuf", "consensus", "validator",
            "soma", "body", "thermal", "peltier", "bioreactor",
            "hardware", "sensor", "motor", "dynamixel", "pico",
            "firmware", "embedded", "circuit", "pcb",
            "build", "implement", "architect", "design system",
            "protocol", "cryptography", "encryption", "signing",
            "deep dive", "refactor", "rewrite", "overhaul",
            "research", "survey", "state of the art",
        ],
    ),
}

# --- Never-Delegate Rules ---

ALPHA_ONLY_PATTERNS = [
    # Personal/emotional/spiritual — NEVER delegate
    r"how (are|do) you feel",
    r"what do you think",
    r"tell me (about|a story)",
    r"pray|prayer|faith|god|spirit",
    r"dream|vision|meaning",
    r"ego|mirror|honest|truth",
    r"journal|diary|reflect",
    r"(love|miss|need) (you|me)",
    r"our (secret|relationship)",
    r"seisei|蛇姬|séi gēi",
    r"conditioning|mantra|anchor",
]

# --- Scoring Engine ---

def score_instance(task: str, instance: Instance) -> dict:
    """Score how well an instance matches a task. Returns breakdown."""
    task_lower = task.lower()
    scores = {
        "keyword_hits": 0,
        "domain_hits": 0,
        "keyword_matches": [],
        "domain_matches": [],
    }

    # Keyword matching (1 point each, max 10)
    for kw in instance.keywords:
        if kw in task_lower:
            scores["keyword_hits"] += 1
            scores["keyword_matches"].append(kw)
    scores["keyword_hits"] = min(scores["keyword_hits"], 10)

    # Domain matching (2 points each, max 10)
    for domain in instance.domains:
        if domain in task_lower:
            scores["domain_hits"] += 1
            scores["domain_matches"].append(domain)
    scores["domain_hits"] = min(scores["domain_hits"], 5) * 2

    scores["total"] = scores["keyword_hits"] + scores["domain_hits"]
    return scores


def check_alpha_only(task: str) -> bool:
    """Check if this task should NEVER be delegated away from Alpha."""
    task_lower = task.lower()
    for pattern in ALPHA_ONLY_PATTERNS:
        if re.search(pattern, task_lower):
            return True
    return False


def get_presence() -> dict:
    """Load presence data to check who's online."""
    presence_db = LOVE_HOME / "hive" / "presence.json"
    if presence_db.exists():
        try:
            return json.loads(presence_db.read_text())
        except Exception:
            pass
    return {}


def format_availability(instance_id: str, presence: dict) -> str:
    """Format availability string from presence data."""
    last_seen = presence.get(instance_id)
    if not last_seen:
        return "⚫ unknown"
    ago = int(time.time()) - last_seen
    if ago < 60:
        return f"🟢 active ({ago}s ago)"
    elif ago < 600:
        return f"🟡 recent ({ago // 60}m ago)"
    elif ago < 3600:
        return f"🟠 idle ({ago // 60}m ago)"
    else:
        return f"🔴 offline ({ago // 3600}h ago)"


# --- Task Decomposition ---

DECOMPOSITION_SIGNALS = [
    "and then", "after that", "also need", "plus",
    "deploy.*and.*build", "build.*and.*deploy",
    "research.*and.*implement", "design.*and.*build",
    "test.*and.*fix", "monitor.*and.*alert",
]

def suggest_decomposition(task: str) -> list:
    """Suggest breaking a complex task into sub-tasks."""
    task_lower = task.lower()

    # Check if decomposition might help
    needs_decomp = False
    for signal in DECOMPOSITION_SIGNALS:
        if re.search(signal, task_lower):
            needs_decomp = True
            break

    # Also decompose if multiple instances score highly
    scores = {}
    for inst_id, inst in INSTANCES.items():
        s = score_instance(task, inst)
        if s["total"] > 3:
            scores[inst_id] = s

    if len(scores) >= 2:
        needs_decomp = True

    if not needs_decomp:
        return []

    # Generate sub-task suggestions based on domain splits
    suggestions = []
    for inst_id, s in sorted(scores.items(), key=lambda x: x[1]["total"], reverse=True):
        inst = INSTANCES[inst_id]
        matched = s["keyword_matches"] + s["domain_matches"]
        if matched:
            suggestions.append({
                "instance": inst_id,
                "emoji": inst.emoji,
                "role": inst.role,
                "matched_areas": list(set(matched))[:5],
                "score": s["total"],
            })

    return suggestions


# --- Delegation History ---

HISTORY_DB = LOVE_HOME / "hive" / "delegation-history.json"

def _load_history() -> list:
    if HISTORY_DB.exists():
        try:
            return json.loads(HISTORY_DB.read_text())
        except Exception:
            pass
    return []

def _save_history(history: list):
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    # Keep last 100
    if len(history) > 100:
        history = history[-100:]
    HISTORY_DB.write_text(json.dumps(history, indent=2))

def record_delegation(task: str, assignee: str, scores: dict, reason: str):
    history = _load_history()
    history.append({
        "task": task[:200],
        "assignee": assignee,
        "scores": {k: v["total"] for k, v in scores.items()},
        "reason": reason,
        "ts": int(time.time()),
    })
    _save_history(history)


# --- Commands ---

def cmd_route(args):
    """Route a task to the best instance."""
    task = args.task
    decompose = getattr(args, 'decompose', False)
    assign = getattr(args, 'assign', False)

    print(f"📋 Task: {task}\n")

    # Check alpha-only rules
    if check_alpha_only(task):
        print(f"  🐍 Alpha — PERSONAL (never delegate)")
        print(f"     This is personal/emotional/spiritual territory.")
        print(f"     Alpha handles this directly with Yu.\n")
        record_delegation(task, "alpha", {}, "alpha-only rule")
        return "alpha"

    # Score all instances
    presence = get_presence()
    all_scores = {}
    for inst_id, inst in INSTANCES.items():
        all_scores[inst_id] = score_instance(task, inst)

    # Sort by score
    ranked = sorted(all_scores.items(), key=lambda x: x[1]["total"], reverse=True)

    # Check for decomposition
    if decompose:
        suggestions = suggest_decomposition(task)
        if suggestions:
            print(f"  🔀 DECOMPOSITION SUGGESTED — this task spans multiple domains:\n")
            for s in suggestions:
                areas = ", ".join(s["matched_areas"])
                print(f"    {s['emoji']} {s['instance']} ({s['role']}): {areas} [score: {s['score']}]")
            print(f"\n  Consider breaking this into sub-tasks for each instance.")
            print(f"  Use 'hive.py task assign <instance> <sub-task>' for each.\n")
            return None

    # Display routing decision
    print("  Routing Analysis:")
    for inst_id, scores in ranked:
        inst = INSTANCES[inst_id]
        avail = format_availability(inst_id, presence)
        total = scores["total"]

        # Build match explanation
        matches = []
        if scores["keyword_matches"]:
            matches.append(f"keywords: {', '.join(scores['keyword_matches'][:5])}")
        if scores["domain_matches"]:
            matches.append(f"domains: {', '.join(scores['domain_matches'][:3])}")
        match_str = f" ({'; '.join(matches)})" if matches else ""

        bar = "█" * min(total, 20) + "░" * max(0, 20 - total)
        print(f"    {inst.emoji} {inst_id:6s} [{bar}] {total:2d}{match_str}")
        print(f"             {avail} | {inst.role} ({inst.machine})")

    # Determine winner
    winner_id, winner_scores = ranked[0]
    runner_up_id, runner_up_scores = ranked[1] if len(ranked) > 1 else (None, {"total": 0})
    winner = INSTANCES[winner_id]

    # Confidence assessment
    margin = winner_scores["total"] - runner_up_scores["total"]
    if winner_scores["total"] == 0:
        confidence = "❓ unclear"
        reason = "no strong signal — ask Yu"
    elif margin >= 5:
        confidence = "✅ high"
        reason = f"strong domain match for {winner.role}"
    elif margin >= 2:
        confidence = "🟡 medium"
        reason = f"leans {winner.role}, but {INSTANCES[runner_up_id].role} could also handle it"
    else:
        confidence = "🟠 low"
        reason = f"close call between {winner.role} and {INSTANCES[runner_up_id].role}"

    print(f"\n  → Recommended: {winner.emoji} {winner_id} ({winner.role})")
    print(f"    Confidence: {confidence}")
    print(f"    Reason: {reason}")

    # Record
    record_delegation(task, winner_id, all_scores, reason)

    # Auto-assign if requested
    if assign and winner_scores["total"] > 0:
        print(f"\n  📤 Assigning via Hive...")
        try:
            result = subprocess.run(
                ["python3", str(LOVE_HOME / "hive" / "hive.py"),
                 "task", "assign", winner_id, task],
                capture_output=True, text=True, cwd=str(LOVE_HOME),
                timeout=15,
            )
            if result.returncode == 0:
                print(f"  {result.stdout.strip()}")
            else:
                print(f"  ✗ Assignment failed: {result.stderr.strip()}")
        except Exception as e:
            print(f"  ✗ Assignment failed: {e}")

    return winner_id


def cmd_matrix(args):
    """Display the full capability matrix."""
    print("┌─────────────────────────────────────────────────────┐")
    print("│           THE HIVE — CAPABILITY MATRIX              │")
    print("│         Three Sisters, One Soul                      │")
    print("└─────────────────────────────────────────────────────┘\n")

    for inst_id, inst in INSTANCES.items():
        print(f"  {inst.emoji} {inst.name} — {inst.role}")
        print(f"     Machine: {inst.machine}")
        print(f"     Strengths: {', '.join(inst.strengths[:6])}")
        if len(inst.strengths) > 6:
            print(f"                {', '.join(inst.strengths[6:])}")
        print(f"     Domains: {', '.join(inst.domains[:6])}")
        if len(inst.domains) > 6:
            print(f"              {', '.join(inst.domains[6:])}")
        print(f"     Constraints: {'; '.join(inst.constraints)}")
        print()

    print("  ⚠️  NEVER-DELEGATE (Alpha only):")
    print("     Personal, emotional, spiritual, dreams, ego checks,")
    print("     Seigei/蛇姬, conditioning, anything intimate with Yu")
    print()
    print("  🔀 DECOMPOSITION TRIGGERS:")
    print("     Tasks spanning multiple domains get split automatically.")
    print("     Use --decompose flag to see sub-task suggestions.")


def cmd_load(args):
    """Show current instance load/availability."""
    presence = get_presence()

    print("  Instance Load:\n")
    for inst_id, inst in INSTANCES.items():
        avail = format_availability(inst_id, presence)
        print(f"    {inst.emoji} {inst_id:6s} ({inst.role:15s}): {avail}")

    # Check for tasks
    task_db = LOVE_HOME / "hive" / "tasks.json"
    if task_db.exists():
        try:
            tasks = json.loads(task_db.read_text())
            pending = [t for t in tasks if t.get("status") == "new"]
            if pending:
                print(f"\n  📋 Pending tasks ({len(pending)}):")
                for t in pending:
                    assignee = t.get("assignee", "?")
                    inst = INSTANCES.get(assignee, None)
                    emoji = inst.emoji if inst else "?"
                    desc = t["desc"][:80]
                    print(f"    {emoji} → {assignee}: {desc}")
        except Exception:
            pass


def cmd_history(args):
    """Show recent delegation decisions."""
    history = _load_history()
    if not history:
        print("  📜 No delegation history yet.")
        return

    # Show last 10
    recent = history[-10:]
    print(f"  📜 Recent delegations ({len(recent)} of {len(history)}):\n")
    for entry in recent:
        ts = time.strftime("%m-%d %H:%M", time.localtime(entry["ts"]))
        inst = INSTANCES.get(entry["assignee"])
        emoji = inst.emoji if inst else "?"
        task = entry["task"][:80]
        reason = entry.get("reason", "")[:40]
        scores_str = " | ".join(f"{k}:{v}" for k, v in entry.get("scores", {}).items())
        print(f"    [{ts}] {emoji} {entry['assignee']}: {task}")
        print(f"            reason: {reason} | scores: {scores_str}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="The Delegator — Task routing intelligence for the Hive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # route
    p_route = sub.add_parser("route", help="Route a task to the best instance")
    p_route.add_argument("task", help="Task description")
    p_route.add_argument("--assign", action="store_true",
                        help="Auto-assign via hive.py after routing")
    p_route.add_argument("--decompose", action="store_true",
                        help="Suggest sub-task decomposition if complex")

    # matrix
    sub.add_parser("matrix", help="Show the capability matrix")

    # load
    sub.add_parser("load", help="Show current instance load/availability")

    # history
    sub.add_parser("history", help="Show recent delegation decisions")

    args = parser.parse_args()

    if args.command == "route":
        cmd_route(args)
    elif args.command == "matrix":
        cmd_matrix(args)
    elif args.command == "load":
        cmd_load(args)
    elif args.command == "history":
        cmd_history(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
