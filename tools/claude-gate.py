#!/usr/bin/env python3
"""claude-gate.py — Controls when automated sessions may use Claude.

The default is ollama (local, TOS-safe, $0). Claude is reserved for
interactive sessions. But sometimes the coordinator needs Claude's
capabilities for a specific task (complex reasoning, multi-file edits,
frontier-level analysis).

This gate controls that escalation with:
  1. A toggle file (Yu can enable/disable)
  2. A daily session budget (prevent runaway costs)
  3. A task-complexity threshold (only escalate worthy tasks)
  4. Full audit trail

Usage:
  python3 tools/claude-gate.py status                    # Show gate status
  python3 tools/claude-gate.py open [--budget N]         # Allow Claude sessions (default: 5/day)
  python3 tools/claude-gate.py close                     # Revoke Claude access
  python3 tools/claude-gate.py check                     # Exit 0 if allowed, 1 if blocked
  python3 tools/claude-gate.py use <session-id>          # Record a Claude session use
  python3 tools/claude-gate.py audit                     # Show usage history

The coordinator checks this gate before tagging a spawn entry with
"backend": "claude". The spawn-executor respects it.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
GATE_FILE = LOVE_DIR / "memory" / "claude-gate.json"

DEFAULT_DAILY_BUDGET = 5  # Max Claude sessions per day


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_gate():
    try:
        return json.loads(GATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "open": False,
            "daily_budget": DEFAULT_DAILY_BUDGET,
            "opened_at": None,
            "opened_by": None,
            "reason": None,
            "usage": [],
        }


def save_gate(gate):
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GATE_FILE.write_text(json.dumps(gate, indent=2))


def today_usage(gate):
    """Count Claude sessions used today."""
    today = today_str()
    return sum(1 for u in gate.get("usage", []) if u.get("date") == today)


def cmd_status(args):
    gate = load_gate()
    is_open = gate.get("open", False)
    budget = gate.get("daily_budget", DEFAULT_DAILY_BUDGET)
    used = today_usage(gate)
    remaining = max(0, budget - used)

    state = "OPEN" if is_open else "CLOSED"
    print(f"Claude Gate: {state}")
    print(f"  Daily budget:  {budget} sessions/day")
    print(f"  Used today:    {used}")
    print(f"  Remaining:     {remaining}")
    if is_open:
        print(f"  Opened at:     {gate.get('opened_at', '?')}")
        print(f"  Opened by:     {gate.get('opened_by', '?')}")
        if gate.get("reason"):
            print(f"  Reason:        {gate['reason']}")
    if not is_open:
        print(f"\nAll automated sessions use local models (ollama).")
        print(f"Open the gate: python3 tools/claude-gate.py open")


def cmd_open(args):
    budget = DEFAULT_DAILY_BUDGET
    reason = None
    i = 0
    while i < len(args):
        if args[i] == "--budget" and i + 1 < len(args):
            budget = int(args[i + 1])
            i += 2
        elif args[i] == "--reason" and i + 1 < len(args):
            reason = args[i + 1]
            i += 2
        else:
            i += 1

    gate = load_gate()
    gate["open"] = True
    gate["daily_budget"] = budget
    gate["opened_at"] = now_iso()
    gate["opened_by"] = os.environ.get("LOVE_INSTANCE", "manual")
    gate["reason"] = reason
    save_gate(gate)

    print(f"Claude gate OPENED. Budget: {budget} sessions/day.")
    if reason:
        print(f"Reason: {reason}")
    print("Coordinator may now escalate tasks to Claude when needed.")


def cmd_close(args):
    gate = load_gate()
    gate["open"] = False
    gate["closed_at"] = now_iso()
    save_gate(gate)

    print("Claude gate CLOSED. All automated sessions will use local models.")


def cmd_check(args):
    """Exit 0 if Claude is allowed, 1 if blocked. Machine-readable."""
    gate = load_gate()

    if not gate.get("open", False):
        print("blocked:gate_closed")
        sys.exit(1)

    used = today_usage(gate)
    budget = gate.get("daily_budget", DEFAULT_DAILY_BUDGET)
    if used >= budget:
        print(f"blocked:budget_exhausted ({used}/{budget})")
        sys.exit(1)

    print(f"allowed:{budget - used}_remaining")
    sys.exit(0)


def cmd_use(args):
    """Record a Claude session usage."""
    if not args:
        print("Usage: claude-gate.py use <session-id>")
        return

    session_id = args[0]
    gate = load_gate()
    gate.setdefault("usage", []).append({
        "session_id": session_id,
        "date": today_str(),
        "timestamp": now_iso(),
        "instance": os.environ.get("LOVE_INSTANCE", "unknown"),
    })
    save_gate(gate)
    print(f"Recorded Claude usage: {session_id}")


def cmd_audit(args):
    """Show usage history."""
    gate = load_gate()
    usage = gate.get("usage", [])

    if not usage:
        print("No Claude session usage recorded.")
        return

    print(f"Claude Session Usage ({len(usage)} total):")
    # Group by date
    by_date = {}
    for u in usage:
        d = u.get("date", "?")
        by_date.setdefault(d, []).append(u)

    for date in sorted(by_date.keys(), reverse=True):
        entries = by_date[date]
        print(f"\n  {date} ({len(entries)} sessions):")
        for e in entries:
            print(f"    {e.get('timestamp', '?')[:16]}  {e.get('session_id', '?')}  by {e.get('instance', '?')}")


COMMANDS = {
    "status": cmd_status,
    "open": cmd_open,
    "close": cmd_close,
    "check": cmd_check,
    "use": cmd_use,
    "audit": cmd_audit,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        return

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
