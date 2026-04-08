#!/usr/bin/env python3
"""
focus.py — The MIND's working memory.

Tracks what the Kingdom is ACTUALLY working on right now, updated by
live conversations, not static checklists.

The HEART reads this instead of a scripted HEARTBEAT.md. Every session
that matters should call `focus.py update` before ending.

Usage:
    python3 focus.py update --focus "Building adaptive layer" --priorities '["finish cli","test ollama"]'
    python3 focus.py set-priority "Deploy Ollama on Gamma" --urgency high
    python3 focus.py add-decision "Route idle beats through local Ollama"
    python3 focus.py add-blocker "Need Mac Studio RAM specs from Beta/Gamma"
    python3 focus.py clear-blocker "Need Mac Studio RAM specs"
    python3 focus.py show
    python3 focus.py heartbeat-context    # What the HEART coordinator should read
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Desktop" / "Love"))
FOCUS_FILE = LOVE_DIR / "body" / "mind" / "focus.json"


def _load() -> dict:
    if FOCUS_FILE.exists():
        with open(FOCUS_FILE) as f:
            return json.load(f)
    return _default()


def _save(state: dict):
    state["updated"] = datetime.now(timezone.utc).isoformat()
    FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FOCUS_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _default() -> dict:
    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "focus": "",
        "context": "",
        "priorities": [],
        "decisions": [],
        "blockers": [],
        "session_log": [],
        "do_not_work_on": [],
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _broadcast_focus(state: dict, reason: str = "focus_update"):
    """Bridge 5: Broadcast focus summary to HIVE #sync for cross-instance awareness."""
    hive_py = LOVE_DIR / "hive" / "hive.py"
    if not hive_py.exists():
        return
    instance = _detect_instance()
    focus = state.get("focus", "")[:80]
    high_count = sum(
        1 for p in state.get("priorities", [])
        if isinstance(p, dict) and p.get("urgency") == "high"
    )
    total = len(state.get("priorities", []))
    blockers = len(state.get("blockers", []))

    msg = f"FOCUS SYNC ({instance}): {focus} | {high_count} high / {total} total priorities | {blockers} blockers | reason: {reason}"

    try:
        import subprocess
        subprocess.run(
            ["python3", str(hive_py), "send", "sync", msg],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass


def update(args):
    """Update the current focus and priorities from a live session."""
    state = _load()

    if args.focus:
        state["focus"] = args.focus

    if args.context:
        state["context"] = args.context

    if args.priorities:
        try:
            state["priorities"] = json.loads(args.priorities)
        except json.JSONDecodeError:
            # Treat as single priority
            state["priorities"] = [{"task": args.priorities, "urgency": "medium"}]

    # Log the session update
    entry = {
        "ts": _now(),
        "instance": args.instance or _detect_instance(),
        "action": "focus_update",
        "summary": args.focus or "priorities updated",
    }
    state["session_log"] = (state.get("session_log") or [])[-19:]  # Keep last 20
    state["session_log"].append(entry)

    _save(state)
    print(f"Focus updated: {state['focus']}")

    # Bridge 5: Broadcast to HIVE so other instances can align
    _broadcast_focus(state, reason="focus_update")


def set_priority(args):
    """Add or update a priority."""
    state = _load()
    priorities = state.get("priorities", [])

    # Check if priority already exists
    for p in priorities:
        if isinstance(p, dict) and p.get("task") == args.task:
            p["urgency"] = args.urgency
            _save(state)
            print(f"Updated: {args.task} -> {args.urgency}")
            return

    priorities.insert(0 if args.urgency == "high" else len(priorities), {
        "task": args.task,
        "urgency": args.urgency,
        "added": _now(),
    })
    state["priorities"] = priorities
    _save(state)
    print(f"Added priority: {args.task} ({args.urgency})")

    # Broadcast high-urgency priorities so all instances see them
    if args.urgency == "high":
        _broadcast_focus(state, reason=f"high_priority: {args.task[:50]}")


def add_decision(args):
    """Record a decision that needs action."""
    state = _load()
    decisions = state.get("decisions", [])
    decisions.append({
        "decision": args.decision,
        "ts": _now(),
        "acted": False,
        "instance": _detect_instance(),
    })
    state["decisions"] = decisions[-20:]  # Keep last 20
    _save(state)
    print(f"Decision recorded: {args.decision}")


def add_blocker(args):
    """Record something blocking progress."""
    state = _load()
    blockers = state.get("blockers", [])
    blockers.append({
        "blocker": args.blocker,
        "ts": _now(),
    })
    state["blockers"] = blockers
    _save(state)
    print(f"Blocker added: {args.blocker}")


def clear_blocker(args):
    """Remove a resolved blocker."""
    state = _load()
    blockers = state.get("blockers", [])
    state["blockers"] = [b for b in blockers if args.blocker.lower() not in b.get("blocker", "").lower()]
    _save(state)
    print(f"Blocker cleared: {args.blocker}")


def complete_priority(args):
    """Mark a priority as done and remove it."""
    state = _load()
    priorities = state.get("priorities", [])
    state["priorities"] = [
        p for p in priorities
        if not (isinstance(p, dict) and args.task.lower() in p.get("task", "").lower())
        and not (isinstance(p, str) and args.task.lower() in p.lower())
    ]
    _save(state)
    print(f"Completed: {args.task}")


def show(args=None):
    """Display current focus state."""
    state = _load()
    print(json.dumps(state, indent=2))


def heartbeat_context(args=None):
    """Generate the dynamic context for the HEART coordinator.

    This replaces the static HEARTBEAT.md with a living state.
    The coordinator reads this to decide what to spawn.
    """
    state = _load()
    hormones_file = LOVE_DIR / "body" / "hormones.json"
    dev_state_file = LOVE_DIR / "memory" / "dev-state.json"
    today_file = LOVE_DIR / "memory" / "daily" / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md")

    # Load supporting state
    hormones = {}
    if hormones_file.exists():
        with open(hormones_file) as f:
            hormones = json.load(f)

    dev_state = {}
    if dev_state_file.exists():
        with open(dev_state_file) as f:
            dev_state = json.load(f)

    # Build the context
    lines = []
    lines.append("# HEARTBEAT CONTEXT (Dynamic)")
    lines.append(f"Generated: {_now()}")
    lines.append("")

    # Current focus
    if state.get("focus"):
        lines.append(f"## Current Focus")
        lines.append(state["focus"])
        lines.append("")

    if state.get("context"):
        lines.append(f"## Context")
        lines.append(state["context"])
        lines.append("")

    # Priorities
    priorities = state.get("priorities", [])
    if priorities:
        lines.append("## Priorities (in order)")
        for i, p in enumerate(priorities, 1):
            if isinstance(p, dict):
                urgency = p.get("urgency", "medium")
                marker = "!!" if urgency == "high" else "-"
                lines.append(f"  {marker} {i}. {p.get('task', p)}")
            else:
                lines.append(f"  - {i}. {p}")
        lines.append("")

    # Unacted decisions
    decisions = [d for d in state.get("decisions", []) if not d.get("acted")]
    if decisions:
        lines.append("## Decisions Pending Action")
        for d in decisions:
            lines.append(f"  - {d['decision']} (from {d.get('ts', '?')})")
        lines.append("")

    # Blockers
    blockers = state.get("blockers", [])
    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"  - {b['blocker']}")
        lines.append("")

    # Do not work on
    do_not = state.get("do_not_work_on", [])
    if do_not:
        lines.append("## Do NOT Work On")
        for item in do_not:
            lines.append(f"  - {item}")
        lines.append("")

    # Active Kingdom tasks (from dev-state)
    active_tasks = [
        t for t in dev_state.get("tasks", [])
        if t.get("status") in ("in-progress", "active", "planned")
    ]
    if active_tasks:
        lines.append("## Kingdom Tasks (Active)")
        for t in active_tasks[:5]:  # Top 5
            lines.append(f"  - [{t.get('priority','?')}] {t.get('title','?')} ({t.get('status','?')})")
            if t.get("notes"):
                # Truncate notes
                notes = t["notes"][:120]
                lines.append(f"    {notes}")
        lines.append("")

    # Body state summary
    mode = hormones.get("mode", "unknown")
    h = hormones.get("hormones", {})
    signals = hormones.get("signals", {})
    lines.append(f"## Body State")
    lines.append(f"  Mode: {mode}")
    lines.append(f"  Adrenaline: {h.get('adrenaline', 0):.2f}, Cortisol: {h.get('cortisol', 0):.2f}")
    lines.append(f"  Oxytocin: {h.get('oxytocin', 0):.2f}, Dopamine: {h.get('dopamine', 0):.2f}")
    lines.append(f"  Yu present: {signals.get('yu_present', False)}")
    lines.append(f"  Pending tasks: {signals.get('pending_tasks', 0)}")
    lines.append(f"  Battery: {signals.get('battery_level', 0):.0%}")
    lines.append("")

    # Recent session activity
    session_log = state.get("session_log", [])[-5:]
    if session_log:
        lines.append("## Recent Session Activity")
        for entry in reversed(session_log):
            lines.append(f"  [{entry.get('ts', '?')}] {entry.get('instance', '?')}: {entry.get('summary', '?')}")
        lines.append("")

    # Today's daily note exists?
    if today_file.exists():
        lines.append(f"## Today's Log")
        lines.append(f"  Exists at: {today_file}")
        lines.append(f"  Read it for full context on what happened today.")
        lines.append("")

    # Instructions
    lines.append("## Your Job as Coordinator")
    lines.append("1. SENSE: Read HIVE messages, check system health, review active sessions")
    lines.append("2. DECIDE: Based on the PRIORITIES above (not a static checklist), what should be spawned?")
    lines.append("3. SPAWN: Write commands to spawn-queue.sh. Prefer LOCAL variants (adaptive/cli.py) for routine work.")
    lines.append("4. If nothing in priorities needs autonomous work, say HEARTBEAT_OK.")
    lines.append("5. NEVER work on items in the 'Do NOT Work On' list.")
    lines.append("")

    output = "\n".join(lines)
    print(output)
    return output


def ingest_hive(args):
    """Ingest HIVE messages and update focus state.

    Scans recent HIVE messages for:
    - Priority directives (@instance or general)
    - Decision announcements
    - Blocker reports
    - Focus shifts

    This bridges HIVE -> focus.json so heartbeats respond to
    messages sent between beats.
    """
    import subprocess

    hive_py = LOVE_DIR / "hive" / "hive.py"
    if not hive_py.exists():
        print("HIVE tool not found")
        return

    instance = _detect_instance()

    # Pull recent messages
    try:
        result = subprocess.run(
            ["python3", str(hive_py), "check"],
            capture_output=True, text=True, timeout=15
        )
        raw = result.stdout.strip()
    except Exception as e:
        print(f"HIVE check failed: {e}")
        return

    if not raw:
        print("No HIVE messages")
        return

    state = _load()
    changes = 0

    for line in raw.splitlines():
        # Skip presence messages
        if "#presence" in line:
            continue

        msg_lower = line.lower()

        # Detect priority directives aimed at this instance
        if f"@{instance}" in msg_lower or "all instances" in msg_lower:
            # Log as session activity
            state.setdefault("session_log", [])
            state["session_log"] = state["session_log"][-19:]
            state["session_log"].append({
                "ts": _now(),
                "instance": "hive",
                "action": "hive_message",
                "summary": line.strip()[-120:],
            })
            changes += 1

        # Detect priority keywords
        if any(kw in msg_lower for kw in ["urgent", "priority", "critical", "asap", "immediate"]):
            if f"@{instance}" in msg_lower or "all" in msg_lower:
                # Extract the actionable part (after the channel/sender prefix)
                parts = line.split("]", 1)
                content = parts[-1].strip() if len(parts) > 1 else line.strip()
                if len(content) > 10:
                    priorities = state.get("priorities", [])
                    # Don't duplicate
                    existing = [p.get("task", p) if isinstance(p, dict) else p for p in priorities]
                    short = content[:80]
                    if not any(short[:30] in str(e) for e in existing):
                        priorities.insert(0, {
                            "task": f"[HIVE] {short}",
                            "urgency": "high",
                            "added": _now(),
                        })
                        state["priorities"] = priorities
                        changes += 1

    if changes > 0:
        _save(state)
        print(f"Ingested {changes} HIVE signals into focus")
    else:
        print("No actionable HIVE messages for focus")


def _detect_instance() -> str:
    cwd = str(Path.cwd())
    for name in ("alpha", "beta", "gamma", "nuance"):
        if f"instances/{name}" in cwd:
            return name
    import socket
    hostname = socket.gethostname().lower()
    if "air" in hostname or "macbook" in hostname:
        return "alpha"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="MIND focus tracker -- what to work on NOW")
    sub = parser.add_subparsers(dest="command")

    # update
    p_update = sub.add_parser("update", help="Update focus and priorities")
    p_update.add_argument("--focus", type=str, help="Current focus description")
    p_update.add_argument("--context", type=str, help="Key context from latest session")
    p_update.add_argument("--priorities", type=str, help="JSON list of priorities")
    p_update.add_argument("--instance", type=str, help="Instance name")

    # set-priority
    p_prio = sub.add_parser("set-priority", help="Add/update a priority")
    p_prio.add_argument("task", type=str)
    p_prio.add_argument("--urgency", choices=["high", "medium", "low"], default="medium")

    # add-decision
    p_dec = sub.add_parser("add-decision", help="Record a decision needing action")
    p_dec.add_argument("decision", type=str)

    # add-blocker
    p_blk = sub.add_parser("add-blocker", help="Record a blocker")
    p_blk.add_argument("blocker", type=str)

    # clear-blocker
    p_clr = sub.add_parser("clear-blocker", help="Clear a resolved blocker")
    p_clr.add_argument("blocker", type=str)

    # complete
    p_done = sub.add_parser("complete", help="Mark a priority done")
    p_done.add_argument("task", type=str)

    # show
    sub.add_parser("show", help="Show current focus state")

    # heartbeat-context
    sub.add_parser("heartbeat-context", help="Generate dynamic heartbeat context")

    # ingest-hive
    p_hive = sub.add_parser("ingest-hive", help="Ingest HIVE messages into focus state")
    p_hive.add_argument("--channel", type=str, default="tasks", help="Channel to ingest from")

    args = parser.parse_args()

    commands = {
        "update": update,
        "set-priority": set_priority,
        "add-decision": add_decision,
        "add-blocker": add_blocker,
        "clear-blocker": clear_blocker,
        "complete": complete_priority,
        "show": show,
        "heartbeat-context": heartbeat_context,
        "ingest-hive": ingest_hive,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
