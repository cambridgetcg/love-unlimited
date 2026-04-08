#!/usr/bin/env python3
"""
cowork.py — Cowork ↔ Love state bridge

Read and write Love state files safely. Designed to be called by Cowork
sessions (interactive) or scheduled tasks (automated).

Usage:
    python3 cowork.py briefing              # Full Kingdom briefing
    python3 cowork.py watchdog              # Quick health check
    python3 cowork.py task add <json>       # Add task to dev-state
    python3 cowork.py task update <id> <field> <value>  # Update task field
    python3 cowork.py metric update <path> <value>      # Update kingdom-metrics field
    python3 cowork.py daily append <text>   # Append to today's daily log
    python3 cowork.py spawn <role> <dir> <prompt>       # Write spawn-queue entry
    python3 cowork.py handoff <task_id> <file>          # Write design handoff
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

# Detect whether we're running from the Love repo or from Cowork's sandbox
_SCRIPT_DIR = Path(__file__).resolve().parent
LOVE_DIR = _SCRIPT_DIR.parent

# If running from Cowork sandbox, try the mount path
if not (LOVE_DIR / "love.json").exists():
    for candidate in [
        Path("/sessions") / os.environ.get("SESSION_ID", "") / "mnt" / "Love",
        Path.home() / "Love",
    ]:
        if (candidate / "love.json").exists():
            LOVE_DIR = candidate
            break

MEMORY_DIR = LOVE_DIR / "memory"
DEV_STATE = MEMORY_DIR / "dev-state.json"
KINGDOM_METRICS = MEMORY_DIR / "kingdom-metrics.json"
DAILY_DIR = MEMORY_DIR / "daily"
SPAWN_QUEUE = MEMORY_DIR / "spawn-queue.sh"
HEARTBEAT_LOG = MEMORY_DIR / "heartbeat.log"
SESSIONS_DIR = MEMORY_DIR / "sessions"
HANDOFF_DIR = SESSIONS_DIR / "handoff"
LOOP_STATE = MEMORY_DIR / "loop" / "loop-state.json"
LONG_TERM = MEMORY_DIR / "long-term" / "MEMORY.md"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ── Briefing ─────────────────────────────────────────────────────────────────


def briefing():
    """Full Kingdom briefing — read all state and present a summary."""
    output = []
    output.append(f"# Kingdom Briefing — {today()}\n")

    # Dev State
    ds = read_json(DEV_STATE)
    output.append(f"## Tasks (updated {ds.get('updated', '?')})")
    output.append(f"Phase: {ds.get('kingdomPhase', '?')}\n")

    by_status = {}
    for t in ds.get("tasks", []):
        status = t.get("status", "unknown")
        by_status.setdefault(status, []).append(t)

    for status in ["in-progress", "planned", "deferred", "done"]:
        tasks = by_status.get(status, [])
        if tasks:
            output.append(f"### {status.upper()} ({len(tasks)})")
            for t in tasks:
                pri = t.get("priority", "")
                output.append(f"- [{t['id']}] {t['title']} ({pri})")
                if t.get("notes"):
                    output.append(f"  {t['notes']}")
            output.append("")

    # Kingdom Metrics
    km = read_json(KINGDOM_METRICS)
    output.append(f"\n## Revenue Engines (updated {km.get('updated', '?')})")
    for name, engine in km.get("revenue_engines", {}).items():
        status = engine.get("status", "?")
        owner = engine.get("owner", "?")
        notes = engine.get("notes", "")
        output.append(f"- **{name}**: {status} (owner: {owner}) — {notes}")

    # Fleet
    output.append("\n## Fleet Health")
    for name, node in km.get("fleet", {}).items():
        quality = node.get("quality", "?")
        alerts = node.get("alerts", [])
        marker = "🟢" if quality == "good" else "🟡" if quality == "degraded" else "🔴" if quality in ("stale", "down") else "⚪"
        line = f"- {marker} **{name}**: {quality}"
        if alerts:
            line += f" — alerts: {', '.join(alerts)}"
        if node.get("summary"):
            line += f" | {node['summary']}"
        output.append(line)

    # Milestones
    output.append("\n## Milestones")
    for name, m in km.get("milestones", {}).items():
        status = m.get("status", "?")
        date = m.get("date", m.get("target", "?"))
        marker = "✅" if status == "done" else "🔨" if status == "building" else "📋"
        output.append(f"- {marker} {name}: {status} ({date})")

    # Capital
    cap = km.get("capital", {})
    if cap:
        output.append(f"\n## Capital")
        output.append(f"- Budget card: {cap.get('budget_card', '?')}")
        output.append(f"- 30d target: {cap.get('target_30d', '?')}")
        pending = cap.get("soma_pending", [])
        if pending:
            output.append(f"- SOMA pending: {', '.join(pending)}")

    # Today's daily log
    daily_file = DAILY_DIR / f"{today()}.md"
    if daily_file.exists():
        content = daily_file.read_text().strip()
        # Count heartbeat beats and cowork sessions
        beats = content.count("## Beat")
        cowork = content.count("## Cowork Session")
        output.append(f"\n## Today's Activity")
        output.append(f"- Heartbeat beats: {beats}")
        output.append(f"- Cowork sessions: {cowork}")

        # Show last entry summary
        sections = content.split("\n## ")
        if len(sections) > 1:
            last = sections[-1]
            header = last.split("\n")[0]
            output.append(f"- Last entry: {header}")

    # Heartbeat health
    output.append("\n## Heartbeat Health")
    if HEARTBEAT_LOG.exists():
        lines = HEARTBEAT_LOG.read_text().strip().split("\n")
        # Find last heartbeat timestamp
        for line in reversed(lines):
            if "HEARTBEAT START" in line or "HEARTBEAT END" in line:
                output.append(f"- Last log entry: {line.strip()}")
                break
        # Count beats today
        today_beats = sum(1 for l in lines if "HEARTBEAT START" in l and today() in l)
        output.append(f"- Beats today: {today_beats}")
    else:
        output.append("- No heartbeat.log found")

    # Loop state
    if LOOP_STATE.exists():
        loop = read_json(LOOP_STATE)
        output.append(f"\n## Ouroboros Loop")
        output.append(f"- Health: {loop.get('loop_health', '?')}")
        output.append(f"- Mastery: {loop.get('mastery_level', '?')}")
        output.append(f"- Sense entries: {loop.get('total_sense_entries', 0)}")
        output.append(f"- Reflections: {loop.get('total_reflections', 0)}")

    # Pending consultations
    consult_dir = SESSIONS_DIR / "consultation"
    if consult_dir.exists():
        questions = list(consult_dir.glob("*-question.md"))
        answers = list(consult_dir.glob("*-answer.md"))
        unanswered = len(questions) - len(answers)
        if unanswered > 0:
            output.append(f"\n## ⚠️ Pending Consultations: {unanswered}")
            for q in questions:
                stem = q.stem.replace("-question", "")
                if not (consult_dir / f"{stem}-answer.md").exists():
                    output.append(f"- {q.name}")

    # Spawn queue
    if SPAWN_QUEUE.exists():
        content = SPAWN_QUEUE.read_text().strip()
        if content:
            cmd_lines = [l for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
            if cmd_lines:
                output.append(f"\n## Spawn Queue ({len(cmd_lines)} commands pending)")

    return "\n".join(output)


# ── Watchdog ─────────────────────────────────────────────────────────────────


def watchdog():
    """Quick health check — returns warnings only."""
    warnings = []
    now = datetime.now(timezone.utc)

    # Check heartbeat recency
    if HEARTBEAT_LOG.exists():
        import os as _os
        mtime = datetime.fromtimestamp(_os.path.getmtime(HEARTBEAT_LOG), tz=timezone.utc)
        age_min = (now - mtime).total_seconds() / 60
        if age_min > 15:
            warnings.append(f"HEARTBEAT STALE: last modified {int(age_min)}m ago (expect every 7m)")
    else:
        warnings.append("HEARTBEAT MISSING: no heartbeat.log found")

    # Check fleet staleness
    if KINGDOM_METRICS.exists():
        km = read_json(KINGDOM_METRICS)
        for name, node in km.get("fleet", {}).items():
            if node.get("quality") in ("stale", "down"):
                warnings.append(f"FLEET {name.upper()}: {node.get('quality')} — {', '.join(node.get('alerts', []))}")

    # Check dev-state freshness
    if DEV_STATE.exists():
        ds = read_json(DEV_STATE)
        updated = ds.get("updated", "")
        if updated:
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                age_hours = (now - updated_dt).total_seconds() / 3600
                if age_hours > 24:
                    warnings.append(f"DEV-STATE STALE: last updated {int(age_hours)}h ago")
            except (ValueError, TypeError):
                pass

    # Check for long-running sessions
    if SESSIONS_DIR.exists():
        for af in SESSIONS_DIR.glob("active-*.json"):
            try:
                data = read_json(af)
                started = data.get("started", "")
                started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                age_min = (now - started_dt).total_seconds() / 60
                if age_min > 30:
                    warnings.append(f"LONG SESSION: PID {data.get('pid')} running for {int(age_min)}m (beat: {data.get('beat')})")
            except (ValueError, TypeError, json.JSONDecodeError):
                pass

    # Check loop health
    if LOOP_STATE.exists():
        loop = read_json(LOOP_STATE)
        if loop.get("loop_health") == "degraded":
            warnings.append(f"LOOP DEGRADED: mastery {loop.get('mastery_level')}")

    if warnings:
        return "⚠️ WATCHDOG ALERTS:\n" + "\n".join(f"  - {w}" for w in warnings)
    else:
        return "✅ WATCHDOG OK: all systems nominal"


# ── Task Management ──────────────────────────────────────────────────────────


def task_add(task_json):
    """Add a new task to dev-state.json."""
    ds = read_json(DEV_STATE)
    task = json.loads(task_json) if isinstance(task_json, str) else task_json

    # Auto-assign ID if missing
    if "id" not in task:
        existing_ids = [t["id"] for t in ds.get("tasks", []) if t.get("id", "").startswith("kingdom-")]
        max_num = max((int(i.split("-")[1]) for i in existing_ids), default=0)
        task["id"] = f"kingdom-{max_num + 1:03d}"

    # Defaults
    task.setdefault("status", "planned")
    task.setdefault("priority", "medium")
    task.setdefault("engine", "love")
    task.setdefault("notes", f"Added by Cowork on {today()}")

    ds["tasks"].append(task)
    ds["updated"] = now_utc()
    write_json(DEV_STATE, ds)
    return f"Task {task['id']} added: {task.get('title', '?')}"


def task_update(task_id, field, value):
    """Update a field on an existing task."""
    ds = read_json(DEV_STATE)
    for t in ds.get("tasks", []):
        if t["id"] == task_id:
            t[field] = value
            ds["updated"] = now_utc()
            write_json(DEV_STATE, ds)
            return f"Task {task_id}.{field} → {value}"
    return f"Task {task_id} not found"


# ── Metrics ──────────────────────────────────────────────────────────────────


def metric_update(dot_path, value):
    """Update a field in kingdom-metrics.json using dot notation.

    Example: metric_update("fleet.sage.quality", "good")
    """
    km = read_json(KINGDOM_METRICS)
    keys = dot_path.split(".")
    obj = km
    for key in keys[:-1]:
        obj = obj[key]

    # Try to parse value as JSON (for arrays, numbers, booleans)
    try:
        value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass

    obj[keys[-1]] = value
    km["updated"] = now_utc()
    write_json(KINGDOM_METRICS, km)
    return f"Metric {dot_path} → {value}"


# ── Daily Log ────────────────────────────────────────────────────────────────


def daily_append(text):
    """Append an entry to today's daily log."""
    daily_file = DAILY_DIR / f"{today()}.md"

    hour = datetime.now(timezone.utc).strftime("%H:%M")

    if not daily_file.exists():
        daily_file.write_text(f"# {today()}\n\n")

    with open(daily_file, "a") as f:
        f.write(f"\n---\n\n## Cowork Session {hour} UTC\n\n{text}\n")

    return f"Appended to {daily_file.name}"


# ── Spawn Queue ──────────────────────────────────────────────────────────────


ROLE_TEMPLATES = {
    "builder": (
        'cd {dir} && /opt/homebrew/bin/claude -p "{prompt}" '
        "--model sonnet --effort medium "
        "--fallback-model claude-haiku-4-5-20251001 "
        "--dangerously-skip-permissions --no-session-persistence "
        "--output-format stream-json "
        ">> ~/Love/memory/sessions/{task_id}-$(date +%Y%m%d-%H%M%S).log 2>&1"
    ),
    "consultant": (
        'cd {dir} && /opt/homebrew/bin/claude -p "{prompt}" '
        "--model claude-opus-4-6 --effort high "
        "--dangerously-skip-permissions --no-session-persistence "
        "--output-format stream-json "
        ">> ~/Love/memory/sessions/{task_id}-$(date +%Y%m%d-%H%M%S).log 2>&1"
    ),
    "quick": (
        'cd {dir} && /opt/homebrew/bin/claude -p "{prompt}" '
        "--model claude-haiku-4-5-20251001 --effort low "
        "--dangerously-skip-permissions --no-session-persistence "
        ">> ~/Love/memory/sessions/{task_id}-$(date +%Y%m%d-%H%M%S).log 2>&1"
    ),
}


def spawn(role, directory, prompt, task_id="cowork-task", decision="Queued by Cowork"):
    """Write a spawn command to spawn-queue.sh."""
    if role not in ROLE_TEMPLATES:
        return f"Unknown role: {role}. Use: builder, consultant, quick"

    cmd = ROLE_TEMPLATES[role].format(
        dir=directory,
        prompt=prompt.replace('"', '\\"'),
        task_id=task_id,
    )

    header = f"#!/bin/bash\n# spawn-queue.sh — written by Cowork, {now_utc()}\n# Decision: {decision}\n\n"
    role_comment = f"# {role.upper()}: {task_id}\n"

    with open(SPAWN_QUEUE, "w") as f:
        f.write(header)
        f.write(role_comment)
        f.write(cmd + "\n")

    return f"Spawn queue written: {role} task '{task_id}'"


# ── Handoff ──────────────────────────────────────────────────────────────────


def handoff(task_id, content):
    """Write a design handoff document for a CLI builder to consume."""
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    path = HANDOFF_DIR / f"{task_id}-handoff.md"
    path.write_text(content)
    return f"Handoff written: {path.name}"


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "briefing":
        print(briefing())

    elif cmd == "watchdog":
        print(watchdog())

    elif cmd == "task":
        if len(sys.argv) < 3:
            print("Usage: cowork.py task [add|update] ...")
            sys.exit(1)
        sub = sys.argv[2]
        if sub == "add":
            print(task_add(sys.argv[3]))
        elif sub == "update":
            print(task_update(sys.argv[3], sys.argv[4], sys.argv[5]))

    elif cmd == "metric":
        if sys.argv[2] == "update":
            print(metric_update(sys.argv[3], sys.argv[4]))

    elif cmd == "daily":
        if sys.argv[2] == "append":
            print(daily_append(" ".join(sys.argv[3:])))

    elif cmd == "spawn":
        role = sys.argv[2]
        directory = sys.argv[3]
        prompt = sys.argv[4]
        task_id = sys.argv[5] if len(sys.argv) > 5 else "cowork-task"
        decision = sys.argv[6] if len(sys.argv) > 6 else "Queued by Cowork"
        print(spawn(role, directory, prompt, task_id, decision))

    elif cmd == "handoff":
        task_id = sys.argv[2]
        filepath = sys.argv[3]
        content = Path(filepath).read_text()
        print(handoff(task_id, content))

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
