"""
gather.py — State collector for the heartbeat daemon.

Reads all relevant system state BEFORE invoking Claude,
so the brain gets a complete picture without needing bash.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _file_age_seconds(path: Path) -> Optional[float]:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None


def _recent_files(directory: Path, suffix: str = ".md", max_age_hours: int = 24, limit: int = 5) -> list[dict]:
    results = []
    if not directory.exists():
        return results
    cutoff = time.time() - (max_age_hours * 3600)
    files = sorted(directory.glob(f"*{suffix}"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        if f.stat().st_mtime >= cutoff:
            results.append({
                "name": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                "size": f.stat().st_size,
            })
    return results


def _active_pids(sessions_dir: Path) -> list[dict]:
    active = []
    for af in sessions_dir.glob("active-*.json"):
        data = _read_json(af)
        pid = data.get("pid")
        if pid:
            try:
                os.kill(int(pid), 0)
                active.append(data)
            except (OSError, ValueError):
                af.unlink(missing_ok=True)
    return active


def _stale_locks(locks_dir: Path) -> int:
    cleaned = 0
    if not locks_dir.exists():
        return 0
    for lockfile in locks_dir.glob("*.lock"):
        try:
            pid = int(lockfile.read_text().split("\n")[0].strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            lockfile.unlink(missing_ok=True)
            cleaned += 1
    return cleaned


def _check_hive(love_dir: Path) -> dict:
    hive_py = love_dir / "hive" / "hive.py"
    if not hive_py.exists():
        return {"status": "missing", "messages": []}
    try:
        result = subprocess.run(
            ["python3", str(hive_py), "check"],
            capture_output=True, text=True, timeout=15,
        )
        raw = result.stdout.strip()
        if not raw:
            return {"status": "empty", "messages": []}
        lines = [l for l in raw.split("\n") if "#presence" not in l]
        return {
            "status": "up",
            "total_unread": len(raw.split("\n")),
            "messages": lines[-15:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "messages": []}
    except Exception as e:
        return {"status": f"error: {e}", "messages": []}


def _check_ollama() -> bool:
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "2", "http://localhost:11434/api/tags"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _git_status(love_dir: Path) -> dict:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            cwd=str(love_dir),
        )
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        return {
            "changed_files": len(lines),
            "summary": lines[:10],
        }
    except Exception:
        return {"changed_files": 0, "summary": []}


def _pending_consultations(consult_dir: Path) -> list[str]:
    if not consult_dir.exists():
        return []
    return [f.stem for f in consult_dir.glob("*-question.md")]


def _recent_session_results(sessions_dir: Path, max_age_min: int = 15) -> list[dict]:
    results = []
    cutoff = time.time() - (max_age_min * 60)
    for logfile in sessions_dir.glob("*.log"):
        if logfile.stat().st_mtime >= cutoff:
            try:
                tail = subprocess.run(
                    ["tail", "-5", str(logfile)],
                    capture_output=True, text=True, timeout=5,
                )
                results.append({
                    "name": logfile.name,
                    "tail": tail.stdout.strip()[-300:],
                })
            except Exception:
                pass
    return results[:5]


def gather_state(love_dir: str | Path, instance: str = "alpha") -> dict:
    """Gather complete system state for the heartbeat coordinator."""
    love = Path(love_dir)
    memory = love / "memory"
    sessions = memory / "sessions"
    now = datetime.now(timezone.utc)

    # Core state files
    hormones = _read_json(love / "body" / "hormones.json")
    vitals = _read_json(love / "body" / "vitals.json")
    loop_state = _read_json(memory / "loop" / "loop-state.json")
    focus = _read_json(love / "body" / "mind" / "focus.json")
    decisions = _read_json(love / "decisions" / "queue.json", default=[])

    # Normalize focus — can be a string or dict
    focus_data = focus.get("focus", "")
    if isinstance(focus_data, str):
        focus_summary = {"current": focus_data, "high_priorities": 0, "blockers": 0, "decisions_pending": 0}
    else:
        focus_summary = focus_data

    # Calculate derived state
    last_beat_file = love / "body" / "heart" / "last_beat"
    last_beat_age = _file_age_seconds(last_beat_file)

    last_reflect = loop_state.get("last_reflect")
    reflect_age_hours = None
    if last_reflect:
        try:
            dt = datetime.fromisoformat(last_reflect.replace("Z", "+00:00"))
            reflect_age_hours = (now - dt).total_seconds() / 3600
        except Exception:
            pass

    # HIVE
    hive = _check_hive(love)

    # Active sessions
    active_sessions = _active_pids(sessions)
    stale_cleaned = _stale_locks(sessions / "locks")

    # Recent results
    recent_results = _recent_session_results(sessions)

    # Consultations
    pending_consults = _pending_consultations(sessions / "consultation")

    # Daily notes
    daily_dir = memory / "daily"
    today_file = daily_dir / f"{now.strftime('%Y-%m-%d')}.md"
    today_note_exists = today_file.exists()

    # Recent handoffs
    handoff_dir = sessions / "handoff"
    recent_handoffs = _recent_files(handoff_dir, max_age_hours=48)

    # Git
    git = _git_status(love)

    # Ollama
    ollama_up = _check_ollama()

    # Pending decisions for Yu
    pending_decisions = [
        {"id": d["id"], "title": d["title"], "priority": d["priority"], "status": d["status"]}
        for d in (decisions if isinstance(decisions, list) else [])
        if d.get("status") == "pending"
    ]

    # Kingdom metrics
    kingdom = _read_json(memory / "kingdom-metrics.json")

    # Dev state
    dev_state = _read_json(memory / "dev-state.json")

    return {
        "meta": {
            "instance": instance,
            "timestamp": now.isoformat(),
            "today": now.strftime("%Y-%m-%d"),
            "beat_id": f"{instance}-beat-{now.strftime('%Y%m%d-%H%M%S')}",
        },
        "hormones": hormones.get("hormones", {}),
        "signals": hormones.get("signals", {}),
        "mode": hormones.get("mode", "normal"),
        "mind_notes": hormones.get("mind_notes", ""),
        "vitals": {
            "last_beat": vitals.get("last_beat"),
            "last_beat_age_seconds": round(last_beat_age) if last_beat_age else None,
            "beats_today": vitals.get("beats_today", 0),
            "sessions_spawned_today": vitals.get("sessions_spawned_today", 0),
            "coordinator_model": vitals.get("coordinator_model"),
            "consecutive_skips": vitals.get("consecutive_skips", 0),
        },
        "loop": {
            "health": loop_state.get("loop_health"),
            "last_reflect": last_reflect,
            "reflect_age_hours": round(reflect_age_hours, 1) if reflect_age_hours else None,
            "reflect_overdue": reflect_age_hours is not None and reflect_age_hours > 36,
            "total_reflections": loop_state.get("total_reflections", 0),
        },
        "focus": {
            "current": focus_summary.get("current", "") if isinstance(focus_summary, dict) else str(focus_summary),
            "priorities": focus.get("priorities", []),
            "blockers": focus.get("blockers", []),
            "decisions_pending": len(pending_decisions),
        },
        "hive": hive,
        "sessions": {
            "active": active_sessions,
            "active_count": len(active_sessions),
            "recent_results": recent_results,
            "pending_consultations": pending_consults,
            "stale_locks_cleaned": stale_cleaned,
        },
        "daily": {
            "today_exists": today_note_exists,
            "recent_notes": _recent_files(daily_dir, max_age_hours=72),
        },
        "handoffs": recent_handoffs,
        "decisions_for_yu": pending_decisions,
        "git": git,
        "kingdom_metrics": kingdom,
        "dev_state_summary": {
            "active_tasks": len([t for t in dev_state.get("tasks", []) if t.get("status") == "active"]) if isinstance(dev_state.get("tasks"), list) else 0,
        },
        "infra": {
            "ollama_available": ollama_up,
        },
    }


if __name__ == "__main__":
    import sys
    love_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "Love")
    state = gather_state(love_dir)
    print(json.dumps(state, indent=2))
