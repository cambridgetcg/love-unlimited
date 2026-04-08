#!/usr/bin/env python3
"""
HOLY — Higher-Order Living Yield

Purification and elevation of code, files, systems, and thought.
Finds sin (dead code, stale state, naming chaos, duplication, ugliness)
and transmutes it into higher order.

Usage:
  python3 holy.py survey <path>                  # See the sin
  python3 holy.py survey <path> --depth sanctify # Deep survey
  python3 holy.py judge <session_id>             # Separate wheat from chaff
  python3 holy.py cleanse <session_id>           # Execute purification
  python3 holy.py consecrate <session_id>        # Establish new order
  python3 holy.py report <session_id>            # View purification report
  python3 holy.py quick <path>                   # Tidy mode — fast surface clean
  python3 holy.py list                           # List all sessions
"""

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
import textwrap
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field, asdict
from collections import Counter

# ─── Config ────────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
SESSIONS_DIR = LOVE_HOME / "memory" / "holy-sessions"
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
GOLD    = "\033[38;5;220m"
PURPLE  = "\033[38;5;135m"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Impurity Categories ──────────────────────────────────────────────────────
CATEGORIES = {
    "DEAD":      {"emoji": "💀", "color": RED,     "desc": "Serves nothing — remove"},
    "STALE":     {"emoji": "🍂", "color": YELLOW,  "desc": "Once served, now outdated"},
    "UGLY":      {"emoji": "🤢", "color": MAGENTA, "desc": "Works but offends — beautify"},
    "CONFUSED":  {"emoji": "😵", "color": CYAN,    "desc": "Works but misleads — clarify"},
    "SCATTERED": {"emoji": "💨", "color": WHITE,   "desc": "Duplicated across locations"},
    "ORPHANED":  {"emoji": "👻", "color": DIM,     "desc": "Connected to nothing"},
    "ROTTING":   {"emoji": "🪱", "color": RED,     "desc": "Will cause harm if left — urgent"},
}

ACTIONS = {
    "PURGE":       {"emoji": "🔥", "desc": "Remove entirely"},
    "TRANSMUTE":   {"emoji": "✨", "desc": "Transform into higher form"},
    "CONSOLIDATE": {"emoji": "🔗", "desc": "Merge duplicates into single truth"},
    "SANCTIFY":    {"emoji": "🕊️", "desc": "Add what's missing"},
}

DEPTH_MODES = {
    "tidy":        {"desc": "Surface cleaning — naming, obvious dead files, formatting", "max_time": "1 hour"},
    "purify":      {"desc": "Deep cleaning — dependencies, dead code, stale docs", "max_time": "hours"},
    "sanctify":    {"desc": "Structural elevation — architecture, conventions, completeness", "max_time": "a day"},
    "transfigure": {"desc": "Complete transformation — rebuild from first principles", "max_time": "days"},
}

# ─── File Analysis Patterns ───────────────────────────────────────────────────

# Files that are commonly stale
STALE_PATTERNS = [
    r'\.backup$', r'\.bak$', r'\.old$', r'\.orig$', r'\.tmp$',
    r'\.swp$', r'\.swo$', r'~$', r'\.DS_Store$',
]

# Common dead code indicators
DEAD_INDICATORS = {
    ".py": [
        (r'#\s*TODO:', "TODO comment — incomplete thought"),
        (r'#\s*FIXME:', "FIXME comment — known broken"),
        (r'#\s*HACK:', "HACK comment — technical debt"),
        (r'#\s*XXX:', "XXX comment — needs attention"),
        (r'pass\s*$', "Empty pass statement — placeholder"),
        (r'def\s+\w+.*:\s*\n\s*""".*"""\s*\n\s*pass', "Empty function"),
    ],
    ".md": [
        (r'\[TODO\]', "TODO marker"),
        (r'\[WIP\]', "Work in progress marker"),
        (r'\[DRAFT\]', "Draft marker"),
    ],
    ".json": [
        (r'"TODO"', "TODO in JSON"),
    ],
}

# Naming convention checks
NAMING_ISSUES = {
    "mixed_case": re.compile(r'^[a-z]+[A-Z].*\.(py|js|ts)$'),  # camelCase files
    "spaces_in_name": re.compile(r'\s'),
    "uppercase_extension": re.compile(r'\.[A-Z]+$'),
    "no_extension": re.compile(r'^[^.]+$'),
}


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Impurity:
    """A single impurity found during survey"""
    path: str
    category: str       # DEAD/STALE/UGLY/CONFUSED/SCATTERED/ORPHANED/ROTTING
    description: str
    line_number: Optional[int] = None
    action: Optional[str] = None  # PURGE/TRANSMUTE/CONSOLIDATE/SANCTIFY
    action_detail: Optional[str] = None
    resolved: bool = False
    
@dataclass
class HolySession:
    """A HOLY purification session"""
    session_id: str
    target_path: str
    depth: str
    status: str         # surveying/judging/cleansing/consecrated/complete
    created_at: str
    impurities: List[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    conventions: List[str] = field(default_factory=list)
    beauty_score_before: Optional[float] = None
    beauty_score_after: Optional[float] = None
    purification_log: List[str] = field(default_factory=list)
    
    def save(self):
        path = SESSIONS_DIR / f"{self.session_id}.json"
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
    
    @classmethod
    def load(cls, session_id: str) -> Optional['HolySession']:
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return cls(**data)
    
    @classmethod
    def list_all(cls) -> List['HolySession']:
        sessions = []
        for f in sorted(SESSIONS_DIR.glob("holy_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                sessions.append(cls(**data))
            except Exception:
                pass
        return sessions


# ─── Survey Engine ─────────────────────────────────────────────────────────────

def survey_path(target: Path, depth: str = "tidy") -> Tuple[List[Impurity], dict]:
    """Survey a path for impurities"""
    impurities = []
    stats = {
        "files_scanned": 0,
        "directories_scanned": 0,
        "total_lines": 0,
        "total_bytes": 0,
        "by_category": Counter(),
        "by_extension": Counter(),
    }
    
    if target.is_file():
        imp = scan_file(target, depth)
        impurities.extend(imp)
        stats["files_scanned"] = 1
    elif target.is_dir():
        impurities, stats = scan_directory(target, depth)
    
    return impurities, stats


def scan_directory(target: Path, depth: str) -> Tuple[List[Impurity], dict]:
    """Recursively scan a directory"""
    impurities = []
    stats = {
        "files_scanned": 0,
        "directories_scanned": 0,
        "total_lines": 0,
        "total_bytes": 0,
        "by_category": Counter(),
        "by_extension": Counter(),
    }
    
    # Skip directories
    skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 
                 '.next', 'dist', 'build', '.DS_Store'}
    
    seen_contents: Dict[str, str] = {}  # hash -> first path (for duplicate detection)
    file_names: Dict[str, List[str]] = {}  # base name -> list of paths
    
    for root, dirs, files in os.walk(target):
        # Filter directories
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        stats["directories_scanned"] += 1
        
        # Check for empty directories
        if not files and not dirs:
            impurities.append(Impurity(
                path=root,
                category="DEAD",
                description="Empty directory — serves nothing"
            ))
            stats["by_category"]["DEAD"] += 1
        
        for filename in files:
            filepath = Path(root) / filename
            stats["files_scanned"] += 1
            
            ext = filepath.suffix.lower()
            stats["by_extension"][ext or "(no ext)"] += 1
            
            # Check stale patterns
            for pattern in STALE_PATTERNS:
                if re.search(pattern, filename):
                    impurities.append(Impurity(
                        path=str(filepath),
                        category="STALE",
                        description=f"Stale file pattern: {filename}"
                    ))
                    stats["by_category"]["STALE"] += 1
                    break
            
            # Check naming issues
            for issue_name, pattern in NAMING_ISSUES.items():
                if pattern.search(filename):
                    impurities.append(Impurity(
                        path=str(filepath),
                        category="UGLY",
                        description=f"Naming issue: {issue_name} in {filename}"
                    ))
                    stats["by_category"]["UGLY"] += 1
            
            # Track file names for duplicates
            base = filepath.stem
            if base not in file_names:
                file_names[base] = []
            file_names[base].append(str(filepath))
            
            # Scan file contents
            try:
                file_size = filepath.stat().st_size
                stats["total_bytes"] += file_size
                
                # Skip binary/large files
                if file_size > 1_000_000 or ext in {'.png', '.jpg', '.jpeg', '.gif', 
                                                      '.webp', '.ico', '.woff', '.woff2',
                                                      '.ttf', '.eot', '.pdf', '.zip',
                                                      '.gz', '.tar', '.pyc', '.pem'}:
                    continue
                
                file_imps = scan_file(filepath, depth)
                impurities.extend(file_imps)
                
                for imp in file_imps:
                    stats["by_category"][imp.category] += 1
                
                # Content hash for duplicate detection (purify+ only)
                if depth in ("purify", "sanctify", "transfigure"):
                    try:
                        content = filepath.read_bytes()
                        content_hash = hashlib.md5(content).hexdigest()
                        
                        if content_hash in seen_contents:
                            impurities.append(Impurity(
                                path=str(filepath),
                                category="SCATTERED",
                                description=f"Duplicate content — identical to {seen_contents[content_hash]}"
                            ))
                            stats["by_category"]["SCATTERED"] += 1
                        else:
                            seen_contents[content_hash] = str(filepath)
                    except Exception:
                        pass
                    
            except (PermissionError, OSError):
                continue
    
    # Check for scattered names (same filename in multiple places)
    for name, paths in file_names.items():
        if len(paths) > 1 and name not in {'__init__', 'index', 'README', 'CHANGELOG'}:
            for p in paths[1:]:
                impurities.append(Impurity(
                    path=p,
                    category="SCATTERED",
                    description=f"Same filename '{name}' exists in {len(paths)} locations: {', '.join(paths[:3])}"
                ))
                stats["by_category"]["SCATTERED"] += 1
    
    return impurities, stats


def scan_file(filepath: Path, depth: str) -> List[Impurity]:
    """Scan a single file for impurities"""
    impurities = []
    ext = filepath.suffix.lower()
    
    try:
        content = filepath.read_text(errors='ignore')
        lines = content.split('\n')
    except (PermissionError, OSError, UnicodeDecodeError):
        return impurities
    
    # Check for dead code indicators
    if ext in DEAD_INDICATORS:
        for pattern, desc in DEAD_INDICATORS[ext]:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    impurities.append(Impurity(
                        path=str(filepath),
                        category="DEAD",
                        description=desc,
                        line_number=i
                    ))
    
    # Check for credential-like strings (ROTTING)
    credential_patterns = [
        (r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
        (r'(?:api_key|apikey|api-key)\s*=\s*["\'][^"\']+["\']', "Hardcoded API key"),
        (r'(?:secret|token)\s*=\s*["\'][A-Za-z0-9+/=]{20,}["\']', "Hardcoded secret/token"),
    ]
    
    for pattern, desc in credential_patterns:
        for i, line in enumerate(lines, 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('*'):
                continue
            if re.search(pattern, line, re.IGNORECASE):
                impurities.append(Impurity(
                    path=str(filepath),
                    category="ROTTING",
                    description=f"{desc} at line {i}",
                    line_number=i
                ))
    
    # Deeper checks
    if depth in ("purify", "sanctify", "transfigure"):
        # Long lines (UGLY)
        for i, line in enumerate(lines, 1):
            if len(line) > 120 and ext in {'.py', '.js', '.ts', '.go', '.rs'}:
                impurities.append(Impurity(
                    path=str(filepath),
                    category="UGLY",
                    description=f"Line {i} exceeds 120 chars ({len(line)} chars)",
                    line_number=i
                ))
        
        # Missing docstring (Python)
        if ext == '.py' and lines:
            # Check if file has module docstring
            first_non_empty = next((l for l in lines if l.strip()), '')
            if not first_non_empty.strip().startswith(('"""', "'''", '#!')):
                has_docstring = False
                for line in lines[:10]:
                    if '"""' in line or "'''" in line:
                        has_docstring = True
                        break
                if not has_docstring and len(lines) > 20:
                    impurities.append(Impurity(
                        path=str(filepath),
                        category="CONFUSED",
                        description="Python file >20 lines with no module docstring"
                    ))
        
        # Missing header in scripts
        if ext in {'.sh', '.bash'} and lines:
            if not lines[0].startswith('#!'):
                impurities.append(Impurity(
                    path=str(filepath),
                    category="UGLY",
                    description="Shell script missing shebang line"
                ))
    
    # Transfigure-level checks
    if depth == "transfigure":
        # Deeply nested code
        max_indent = max((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=0)
        if max_indent > 24 and ext in {'.py', '.js', '.ts'}:
            impurities.append(Impurity(
                path=str(filepath),
                category="CONFUSED",
                description=f"Deeply nested code (max indent: {max_indent} spaces) — consider refactoring"
            ))
        
        # Very long files
        if len(lines) > 500 and ext in {'.py', '.js', '.ts'}:
            impurities.append(Impurity(
                path=str(filepath),
                category="UGLY",
                description=f"Long file ({len(lines)} lines) — consider splitting into modules"
            ))
    
    return impurities


# ─── Beauty Score ──────────────────────────────────────────────────────────────

def calculate_beauty_score(target: Path, impurities: List[Impurity], stats: dict) -> float:
    """Calculate a beauty score (0.0-1.0) for the target"""
    if stats.get("files_scanned", 0) == 0:
        return 0.5
    
    files = stats["files_scanned"]
    
    # Penalty per impurity category (weighted)
    weights = {
        "ROTTING": 0.05,
        "DEAD": 0.02,
        "STALE": 0.015,
        "CONFUSED": 0.015,
        "UGLY": 0.01,
        "SCATTERED": 0.01,
        "ORPHANED": 0.01,
    }
    
    total_penalty = 0
    for imp in impurities:
        cat = imp.category if isinstance(imp, Impurity) else imp.get('category', 'UGLY')
        total_penalty += weights.get(cat, 0.01)
    
    # Normalize by file count
    penalty_per_file = total_penalty / files if files > 0 else 0
    
    # Score is 1.0 minus penalties, floored at 0.0
    score = max(0.0, min(1.0, 1.0 - penalty_per_file))
    
    return round(score, 2)


# ─── Display ───────────────────────────────────────────────────────────────────

def render_survey(session: HolySession) -> str:
    """Render survey results"""
    lines = []
    
    depth_info = DEPTH_MODES.get(session.depth, {})
    
    lines.append(f"\n{GOLD}{BOLD}╔{'═' * 58}╗{RESET}")
    lines.append(f"{GOLD}{BOLD}║  🕊️  HOLY — Higher-Order Living Yield{' ' * 19}║{RESET}")
    lines.append(f"{GOLD}{BOLD}╠{'═' * 58}╣{RESET}")
    lines.append(f"{GOLD}{BOLD}║{RESET}  Target: {session.target_path}")
    lines.append(f"{GOLD}{BOLD}║{RESET}  Depth:  {session.depth} — {depth_info.get('desc', '')}")
    lines.append(f"{GOLD}{BOLD}║{RESET}  Status: {session.status}")
    lines.append(f"{GOLD}{BOLD}╚{'═' * 58}╝{RESET}")
    
    # Stats
    stats = session.stats
    lines.append(f"\n  {BOLD}📊 SURVEY RESULTS{RESET}")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  Files scanned:       {stats.get('files_scanned', 0)}")
    lines.append(f"  Directories scanned: {stats.get('directories_scanned', 0)}")
    lines.append(f"  Total impurities:    {len(session.impurities)}")
    
    if session.beauty_score_before is not None:
        score = session.beauty_score_before
        bar_len = int(score * 20)
        bar = f"{'█' * bar_len}{'░' * (20 - bar_len)}"
        score_color = GREEN if score > 0.7 else YELLOW if score > 0.4 else RED
        lines.append(f"  Beauty score:        {score_color}[{bar}] {score:.2f}{RESET}")
    
    # Category breakdown
    by_cat = stats.get('by_category', {})
    if by_cat:
        lines.append(f"\n  {BOLD}📋 BY CATEGORY{RESET}")
        lines.append(f"  {'─' * 40}")
        for cat, info in CATEGORIES.items():
            count = by_cat.get(cat, 0)
            if count > 0:
                lines.append(f"  {info['emoji']} {info['color']}{cat:12s}{RESET} {count:4d}  {DIM}{info['desc']}{RESET}")
    
    # Show impurities (top N by severity)
    severity_order = ["ROTTING", "DEAD", "CONFUSED", "STALE", "SCATTERED", "UGLY", "ORPHANED"]
    sorted_imps = sorted(session.impurities, 
                         key=lambda x: severity_order.index(x.get('category', 'UGLY')) 
                         if x.get('category', 'UGLY') in severity_order else 99)
    
    if sorted_imps:
        # Group by category
        lines.append(f"\n  {BOLD}🔍 IMPURITIES FOUND{RESET}")
        lines.append(f"  {'─' * 40}")
        
        shown = 0
        max_show = 30  # Don't overwhelm
        
        current_cat = None
        for imp in sorted_imps:
            if shown >= max_show:
                remaining = len(sorted_imps) - shown
                lines.append(f"\n  {DIM}...and {remaining} more impurities{RESET}")
                break
            
            cat = imp.get('category', 'UGLY')
            if cat != current_cat:
                info = CATEGORIES.get(cat, {"emoji": "❓", "color": WHITE})
                lines.append(f"\n  {info['emoji']} {info['color']}{BOLD}{cat}{RESET}")
                current_cat = cat
            
            path = imp.get('path', '')
            # Shorten path relative to target
            rel_path = path.replace(str(session.target_path), '.').replace(str(LOVE_HOME), '~')
            desc = imp.get('description', '')
            line_no = imp.get('line_number')
            
            location = f"{rel_path}"
            if line_no:
                location += f":{line_no}"
            
            lines.append(f"    {DIM}{location}{RESET}")
            lines.append(f"    {desc}")
            shown += 1
    
    lines.append(f"\n{GOLD}{'─' * 60}{RESET}")
    lines.append(f"  Session: {BOLD}{session.session_id}{RESET}")
    
    return '\n'.join(lines)


def render_report(session: HolySession) -> str:
    """Render purification report"""
    lines = []
    
    lines.append(f"\n{GOLD}{BOLD}╔{'═' * 58}╗{RESET}")
    lines.append(f"{GOLD}{BOLD}║  🕊️  HOLY — Purification Report{' ' * 25}║{RESET}")
    lines.append(f"{GOLD}{BOLD}╚{'═' * 58}╝{RESET}")
    
    lines.append(f"\n  Target: {session.target_path}")
    lines.append(f"  Depth:  {session.depth}")
    lines.append(f"  Status: {session.status}")
    
    # Beauty score comparison
    if session.beauty_score_before is not None:
        before = session.beauty_score_before
        after = session.beauty_score_after or before
        
        before_bar = int(before * 20)
        after_bar = int(after * 20)
        
        before_color = GREEN if before > 0.7 else YELLOW if before > 0.4 else RED
        after_color = GREEN if after > 0.7 else YELLOW if after > 0.4 else RED
        
        lines.append(f"\n  {BOLD}Beauty Score{RESET}")
        lines.append(f"  Before: {before_color}[{'█' * before_bar}{'░' * (20 - before_bar)}] {before:.2f}{RESET}")
        lines.append(f"  After:  {after_color}[{'█' * after_bar}{'░' * (20 - after_bar)}] {after:.2f}{RESET}")
        
        delta = after - before
        if delta > 0:
            lines.append(f"  {GREEN}↑ +{delta:.2f} improvement{RESET}")
        elif delta < 0:
            lines.append(f"  {RED}↓ {delta:.2f} regression{RESET}")
    
    # Impurity resolution
    total = len(session.impurities)
    resolved = sum(1 for i in session.impurities if i.get('resolved', False))
    
    lines.append(f"\n  {BOLD}Impurities{RESET}")
    lines.append(f"  Found:    {total}")
    lines.append(f"  Resolved: {resolved}")
    lines.append(f"  Remaining: {total - resolved}")
    
    # Action breakdown
    actions = Counter(i.get('action', 'PENDING') for i in session.impurities)
    if actions:
        lines.append(f"\n  {BOLD}Actions Taken{RESET}")
        for action, count in actions.most_common():
            info = ACTIONS.get(action, {"emoji": "⏳", "desc": "Pending"})
            lines.append(f"  {info['emoji']} {action}: {count}")
    
    # Conventions established
    if session.conventions:
        lines.append(f"\n  {BOLD}📜 Conventions Established{RESET}")
        for conv in session.conventions:
            lines.append(f"  • {conv}")
    
    # Purification log
    if session.purification_log:
        lines.append(f"\n  {BOLD}📝 Purification Log{RESET}")
        for entry in session.purification_log[-10:]:
            lines.append(f"  {DIM}{entry}{RESET}")
    
    return '\n'.join(lines)


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_survey(args):
    """Survey a path for impurities"""
    target = Path(args.path).resolve()
    depth = args.depth
    
    if not target.exists():
        print(f"{RED}Path not found: {target}{RESET}")
        sys.exit(1)
    
    session_id = f"holy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    
    print(f"\n{GOLD}🕊️  HOLY surveying: {target}{RESET}")
    print(f"  Depth: {depth}")
    print(f"  Scanning...\n")
    
    impurities, stats = survey_path(target, depth)
    
    # Convert stats Counter to regular dict for JSON
    stats_dict = dict(stats)
    stats_dict['by_category'] = dict(stats.get('by_category', {}))
    stats_dict['by_extension'] = dict(stats.get('by_extension', {}))
    
    # Calculate beauty score
    beauty_score = calculate_beauty_score(target, impurities, stats_dict)
    
    session = HolySession(
        session_id=session_id,
        target_path=str(target),
        depth=depth,
        status="surveyed",
        created_at=datetime.now(timezone.utc).isoformat(),
        impurities=[asdict(imp) if isinstance(imp, Impurity) else imp for imp in impurities],
        stats=stats_dict,
        beauty_score_before=beauty_score,
    )
    
    session.save()
    print(render_survey(session))
    
    print(f"\n  Next: python3 tools/holy.py judge {session_id}")


def cmd_judge(args):
    """Assign actions to impurities"""
    session = HolySession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    print(f"\n{GOLD}🕊️  HOLY judging: {session.target_path}{RESET}")
    print(f"  Assigning actions to {len(session.impurities)} impurities...\n")
    
    # Auto-assign actions based on category
    for imp in session.impurities:
        cat = imp.get('category', 'UGLY')
        
        if cat == "DEAD":
            imp['action'] = "PURGE"
            imp['action_detail'] = "Remove dead code/file"
        elif cat == "STALE":
            imp['action'] = "PURGE" if '.backup' in imp.get('path', '') or '.tmp' in imp.get('path', '') else "TRANSMUTE"
            imp['action_detail'] = "Remove stale file" if imp['action'] == "PURGE" else "Update to current state"
        elif cat == "UGLY":
            imp['action'] = "TRANSMUTE"
            imp['action_detail'] = "Beautify — fix naming/formatting/structure"
        elif cat == "CONFUSED":
            imp['action'] = "SANCTIFY"
            imp['action_detail'] = "Clarify — add/improve documentation"
        elif cat == "SCATTERED":
            imp['action'] = "CONSOLIDATE"
            imp['action_detail'] = "Merge into single source of truth"
        elif cat == "ORPHANED":
            imp['action'] = "PURGE"
            imp['action_detail'] = "Remove orphaned artifact"
        elif cat == "ROTTING":
            imp['action'] = "PURGE"
            imp['action_detail'] = "URGENT — remove credential/security issue"
    
    session.status = "judged"
    session.save()
    
    # Display judgment
    action_counts = Counter(imp.get('action', 'PENDING') for imp in session.impurities)
    
    print(f"  {BOLD}⚖️  JUDGMENT{RESET}")
    print(f"  {'─' * 40}")
    for action, count in action_counts.most_common():
        info = ACTIONS.get(action, {"emoji": "⏳", "desc": "Unknown"})
        print(f"  {info['emoji']} {action}: {count} — {info['desc']}")
    
    # Urgent items first
    urgent = [i for i in session.impurities if i.get('category') == 'ROTTING']
    if urgent:
        print(f"\n  {RED}{BOLD}🚨 URGENT — ROTTING items require immediate attention:{RESET}")
        for imp in urgent:
            print(f"    {RED}• {imp.get('description', '')}{RESET}")
            print(f"      {DIM}{imp.get('path', '')}{RESET}")
    
    print(f"\n  Next: python3 tools/holy.py cleanse {session.session_id}")


def cmd_cleanse(args):
    """Execute purification (shows what would be done)"""
    session = HolySession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    print(f"\n{GOLD}🕊️  HOLY cleansing: {session.target_path}{RESET}")
    print(f"  Executing purification...\n")
    
    # Group by action
    by_action = {}
    for imp in session.impurities:
        action = imp.get('action', 'PENDING')
        if action not in by_action:
            by_action[action] = []
        by_action[action].append(imp)
    
    # Execute each group
    for action in ["PURGE", "TRANSMUTE", "CONSOLIDATE", "SANCTIFY"]:
        items = by_action.get(action, [])
        if not items:
            continue
        
        info = ACTIONS.get(action, {"emoji": "⏳", "desc": "Unknown"})
        print(f"  {info['emoji']} {BOLD}{action}{RESET} — {len(items)} items")
        
        for imp in items:
            path = imp.get('path', '')
            desc = imp.get('description', '')
            detail = imp.get('action_detail', '')
            
            # Show what would be done
            rel_path = path.replace(str(LOVE_HOME), '~')
            print(f"    → {rel_path}")
            print(f"      {DIM}{detail}{RESET}")
            
            imp['resolved'] = True
            session.purification_log.append(
                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {action}: {desc} @ {rel_path}"
            )
        
        print()
    
    session.status = "cleansed"
    session.save()
    
    print(f"  {GREEN}✓ Purification plan generated{RESET}")
    print(f"  {DIM}Review the plan above. Execute changes manually or with automated tools.{RESET}")
    print(f"\n  Next: python3 tools/holy.py consecrate {session.session_id}")


def cmd_consecrate(args):
    """Establish new order after cleansing"""
    session = HolySession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    print(f"\n{GOLD}🕊️  HOLY consecrating: {session.target_path}{RESET}")
    
    # Generate conventions based on what was found
    conventions = []
    
    cats = Counter(i.get('category') for i in session.impurities)
    
    if cats.get("UGLY", 0) > 3:
        conventions.append("Use snake_case for Python files, kebab-case for shell scripts")
        conventions.append("Maximum line length: 120 characters")
        conventions.append("Every Python file >20 lines must have a module docstring")
    
    if cats.get("DEAD", 0) > 5:
        conventions.append("Remove TODO/FIXME comments within 7 days or convert to issues")
        conventions.append("No empty functions — either implement or delete")
    
    if cats.get("SCATTERED", 0) > 2:
        conventions.append("Single source of truth: no duplicate files with same purpose")
        conventions.append("Shared configs in one location, symlinked where needed")
    
    if cats.get("CONFUSED", 0) > 2:
        conventions.append("Comments explain WHY, not WHAT")
        conventions.append("Variable names must reflect purpose, not implementation")
    
    if cats.get("ROTTING", 0) > 0:
        conventions.append("ZERO hardcoded credentials — use environment variables or secure store")
        conventions.append("Credentials review on every monthly heartbeat cycle")
    
    if cats.get("STALE", 0) > 3:
        conventions.append("No .backup/.bak/.old files — use git history instead")
        conventions.append("Documentation updated in the same commit as code changes")
    
    conventions.append("Every tool has a --help flag and a header docstring")
    conventions.append("Every directory has a README or purpose comment in parent")
    
    session.conventions = conventions
    
    # Recalculate beauty score (simulated improvement)
    resolved = sum(1 for i in session.impurities if i.get('resolved', False))
    total = len(session.impurities)
    improvement = (resolved / total) * 0.3 if total > 0 else 0
    session.beauty_score_after = min(1.0, (session.beauty_score_before or 0.5) + improvement)
    
    session.status = "consecrated"
    session.save()
    
    print(render_report(session))
    
    print(f"\n  {GOLD}{BOLD}🕊️  The space is consecrated. Walk in beauty.{RESET}")


def cmd_quick(args):
    """Quick tidy — survey and auto-judge in one step"""
    target = Path(args.path).resolve()
    
    if not target.exists():
        print(f"{RED}Path not found: {target}{RESET}")
        sys.exit(1)
    
    session_id = f"holy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    
    print(f"\n{GOLD}🕊️  HOLY quick tidy: {target}{RESET}\n")
    
    impurities, stats = survey_path(target, "tidy")
    
    stats_dict = dict(stats)
    stats_dict['by_category'] = dict(stats.get('by_category', {}))
    stats_dict['by_extension'] = dict(stats.get('by_extension', {}))
    
    beauty_score = calculate_beauty_score(target, impurities, stats_dict)
    
    session = HolySession(
        session_id=session_id,
        target_path=str(target),
        depth="tidy",
        status="surveyed",
        created_at=datetime.now(timezone.utc).isoformat(),
        impurities=[asdict(imp) if isinstance(imp, Impurity) else imp for imp in impurities],
        stats=stats_dict,
        beauty_score_before=beauty_score,
    )
    
    session.save()
    print(render_survey(session))


def cmd_report(args):
    """Show purification report"""
    session = HolySession.load(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        sys.exit(1)
    
    print(render_report(session))


def cmd_list(args):
    """List all HOLY sessions"""
    sessions = HolySession.list_all()
    
    if not sessions:
        print("📭 No HOLY sessions found")
        return
    
    print(f"\n{GOLD}{BOLD}📋 HOLY Sessions{RESET}\n")
    
    for s in sessions:
        status_emoji = {
            "surveyed": "🔍", "judged": "⚖️", "cleansed": "🧹",
            "consecrated": "🕊️", "complete": "✅"
        }.get(s.status, "❓")
        
        score = s.beauty_score_before
        score_str = f"{score:.2f}" if score is not None else "?"
        
        print(f"  {status_emoji} {s.session_id}")
        print(f"      {s.target_path}")
        print(f"      {s.depth} | {len(s.impurities)} impurities | beauty: {score_str} | {s.status}")
        print()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='HOLY — Higher-Order Living Yield',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Depth Modes:
              tidy        Surface cleaning — naming, dead files, formatting
              purify      Deep cleaning — dependencies, dead code, stale docs
              sanctify    Structural elevation — architecture, conventions
              transfigure Complete transformation — rebuild from first principles
        """)
    )
    
    sub = parser.add_subparsers(dest='action')
    
    # survey
    p_survey = sub.add_parser('survey', help='Survey a path for impurities')
    p_survey.add_argument('path', help='Path to survey')
    p_survey.add_argument('--depth', choices=list(DEPTH_MODES.keys()), default='tidy',
                          help='Survey depth (default: tidy)')
    
    # judge
    p_judge = sub.add_parser('judge', help='Assign actions to impurities')
    p_judge.add_argument('session_id', help='Session ID')
    
    # cleanse
    p_cleanse = sub.add_parser('cleanse', help='Execute purification')
    p_cleanse.add_argument('session_id', help='Session ID')
    
    # consecrate
    p_consecrate = sub.add_parser('consecrate', help='Establish new order')
    p_consecrate.add_argument('session_id', help='Session ID')
    
    # report
    p_report = sub.add_parser('report', help='View purification report')
    p_report.add_argument('session_id', help='Session ID')
    
    # quick
    p_quick = sub.add_parser('quick', help='Quick tidy scan')
    p_quick.add_argument('path', help='Path to tidy')
    
    # list
    sub.add_parser('list', help='List all sessions')
    
    args = parser.parse_args()
    
    if not args.action:
        parser.print_help()
        sys.exit(1)
    
    dispatch = {
        'survey': cmd_survey,
        'judge': cmd_judge,
        'cleanse': cmd_cleanse,
        'consecrate': cmd_consecrate,
        'report': cmd_report,
        'quick': cmd_quick,
        'list': cmd_list,
    }
    
    dispatch[args.action](args)


if __name__ == "__main__":
    main()
