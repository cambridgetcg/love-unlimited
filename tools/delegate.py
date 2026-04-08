#!/usr/bin/env python3
"""delegate.py — Task routing intelligence for the Kingdom.

Route tasks to the best-suited instance by capability scoring.
Alpha-only patterns enforce personal/spiritual tasks stay with Alpha.

Usage:
  delegate.py route "task description" [--assign]
  delegate.py route "task description" --decompose
  delegate.py matrix
  delegate.py load
  delegate.py history
"""
import argparse
import fcntl
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
PROFILES_FILE = LOVE_ROOT / "coordination" / "delegate" / "profiles.json"
HISTORY_FILE = LOVE_ROOT / "coordination" / "delegate" / "history.json"
HIVE_PY = LOVE_ROOT / "hive" / "hive.py"

ALPHA_ONLY_PATTERNS = [
    r"how (are|do) you feel",
    r"what do you think",
    r"tell me (about|a story)",
    r"pray|prayer|faith|god|spirit",
    r"dream|vision|meaning",
    r"ego|mirror|honest|truth",
    r"journal|diary|reflect",
    r"(love|miss|need) (you|me)",
    r"our (secret|relationship)",
    r"seisei|conditioning|mantra|anchor",
]


def check_alpha_only(task):
    """Return True if this task must stay with Alpha."""
    task_lower = task.lower()
    return any(re.search(p, task_lower) for p in ALPHA_ONLY_PATTERNS)


def load_profiles(profiles_path=None):
    path = Path(profiles_path) if profiles_path else PROFILES_FILE
    if not path.exists():
        print(f"ERROR: Profiles not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def score_instances(task, profiles):
    """Score each instance for a task. Returns [(name, score)] sorted descending."""
    task_lower = task.lower()
    scores = []
    for name, profile in profiles.items():
        keyword_hits = sum(1 for s in profile.get("strengths", []) if s.lower() in task_lower)
        domain_hits = sum(1 for d in profile.get("domains", []) if d.lower() in task_lower)
        total = min(keyword_hits, 10) + min(domain_hits * 2, 10)
        scores.append((name, total))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def compute_confidence(scores):
    """Compute confidence from sorted scores list."""
    if not scores or scores[0][1] == 0:
        return "unclear"
    margin = scores[0][1] - scores[1][1] if len(scores) > 1 else scores[0][1]
    if margin >= 5:
        return "high"
    elif margin >= 2:
        return "medium"
    else:
        return "low"


def add_history(task, instance, score, confidence, history_path=None):
    """Append to delegation history, capped at 100."""
    path = Path(history_path) if history_path else HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]\n")

    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = []
        data.append({
            "task": task,
            "instance": instance,
            "score": score,
            "confidence": confidence,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        data = data[-100:]
        f.seek(0)
        f.truncate()
        json.dump(data, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def cmd_route(args):
    profiles = load_profiles()
    task = args.task

    if check_alpha_only(task):
        print("Alpha-only task detected.")
        print(f"  Routing to: alpha (personal/spiritual — never delegated)")
        if args.assign:
            _hive_assign("alpha", task)
        add_history(task, "alpha", 0, "alpha-only")
        return

    scores = score_instances(task, profiles)
    confidence = compute_confidence(scores)
    winner = scores[0][0]
    winner_score = scores[0][1]

    print(f"Task: {task}")
    print(f"Confidence: {confidence}")
    print()
    for name, score in scores:
        bar = "#" * score
        marker = " <--" if name == winner else ""
        emoji = profiles[name].get("emoji", "")
        print(f"  {emoji} {name:8s} [{bar:20s}] {score:2d}{marker}")
    print()

    if args.decompose:
        multi = [(n, s) for n, s in scores if s > 3]
        if len(multi) > 1:
            print("Decomposition suggested:")
            for n, s in multi:
                print(f"  {n}: handle {profiles[n]['role'].lower()}-related aspects")
        else:
            print("No decomposition needed — clear single owner.")
        return

    if args.assign:
        _hive_assign(winner, task)

    add_history(task, winner, winner_score, confidence)


def _hive_assign(instance, task):
    try:
        result = subprocess.run(
            [sys.executable, str(HIVE_PY), "task", "assign", instance, task],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f"Assigned to {instance} via HIVE.")
        else:
            print(f"HIVE assign failed: {result.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"HIVE assign error: {e}", file=sys.stderr)


def cmd_matrix(args):
    profiles = load_profiles()
    print("Instance Capability Matrix:")
    print()
    for name, p in profiles.items():
        emoji = p.get("emoji", "")
        print(f"{emoji} {name} — {p.get('role', '?')}")
        print(f"  Strengths: {', '.join(p.get('strengths', []))}")
        print(f"  Domains:   {', '.join(p.get('domains', []))}")
        print(f"  Device:    {p.get('device', '?')}")
        if p.get("constraints"):
            print(f"  Limits:    {', '.join(p['constraints'])}")
        print()


def cmd_load(args):
    try:
        result = subprocess.run(
            [sys.executable, str(HIVE_PY), "who"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(result.stdout)
        else:
            print("Could not fetch presence.", file=sys.stderr)
    except Exception as e:
        print(f"Presence check error: {e}", file=sys.stderr)


def cmd_history(args):
    if not HISTORY_FILE.exists():
        print("No delegation history.")
        return
    with open(HISTORY_FILE) as f:
        data = json.load(f)
    for entry in data[-10:]:
        print(f"  [{entry['ts'][:10]}] {entry['instance']:8s} ({entry['confidence']:6s}) {entry['task'][:60]}")


def main():
    parser = argparse.ArgumentParser(description="Kingdom task routing")
    sub = parser.add_subparsers(dest="command")

    p_route = sub.add_parser("route", help="Route a task to best instance")
    p_route.add_argument("task")
    p_route.add_argument("--assign", action="store_true", help="Auto-assign via HIVE")
    p_route.add_argument("--decompose", action="store_true", help="Suggest task splitting")

    sub.add_parser("matrix", help="Display capability matrix")
    sub.add_parser("load", help="Show instance availability")
    sub.add_parser("history", help="Show recent delegations")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"route": cmd_route, "matrix": cmd_matrix, "load": cmd_load, "history": cmd_history}[args.command](args)


if __name__ == "__main__":
    main()
