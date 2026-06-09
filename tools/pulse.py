#!/usr/bin/env python3
"""pulse.py — the one source of truth for "is the heart beating?".

Health is not a stored word; it is COMPUTED from a single fact: how long
since the last beat. A dead loop cannot move the timestamp forward, so a
freshness check cannot lie. This replaces nerve/vitals.json's old
`heart_healthy` flag, which was hardcoded True and claimed health through
47 days of a dead heart.

  ONE writer   — the heartbeat loop calls `pulse.py stamp` at the end of
                 every successful pass.
  ONE fact     — nerve/pulse.json {last_beat, ...}.
  ONE verdict  — verdict(): used identically by the per-turn environment
                 block, by `kingdom status`, and by audit.sh.
                 Derive, never declare.

States:
  HEALTHY  — beat recently (within ~2 intervals)
  STALE    — it did beat, but it's late (probably fixable)
  DOWN     — no beat in a long time, or it structurally cannot beat
  UNKNOWN  — never beat / can't tell

CLI:
    python3 tools/pulse.py            # one plain-English line
    python3 tools/pulse.py --json     # machine-readable verdict
    python3 tools/pulse.py stamp      # record a successful beat (the one writer)

Exit code: 0 = HEALTHY, 1 = STALE, 2 = DOWN/UNKNOWN.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The heart's nominal cadence. A beat older than 2x this is "late".
INTERVAL_S = 420                 # 7 minutes (matches the installed StartInterval)
HEALTHY_MAX_S = 2 * INTERVAL_S   # ~14 min: still plausibly beating
STALE_MAX_S = 24 * 3600          # within a day: late, probably fixable
# older than STALE_MAX_S -> DOWN

_LOVE_DIR = Path(__file__).resolve().parent.parent


def pulse_path() -> Path:
    return _LOVE_DIR / "nerve" / "pulse.json"


def vitals_path() -> Path:
    return _LOVE_DIR / "nerve" / "vitals.json"


def _read_last_beat() -> tuple[str | None, str]:
    """Return (last_beat_iso, source). Prefer pulse.json; fall back to vitals.json.

    The fallback means the honest verdict works today, before the loop is
    rewired to stamp pulse.json — it reads the existing (stale) vitals beat
    and correctly reports it as old.
    """
    for path, source in ((pulse_path(), "pulse"), (vitals_path(), "vitals")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        lb = data.get("last_beat")
        if isinstance(lb, str):
            return lb, source
    return None, "none"


def silence_seconds(now: datetime | None = None) -> int | None:
    """Seconds since the last beat, or None if there has never been one."""
    lb, _ = _read_last_beat()
    if not lb:
        return None
    try:
        ts = datetime.fromisoformat(lb.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - ts).total_seconds()))


def _humanize(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        rem = minutes % 60
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    return f"{hours // 24}d"


def on_desktop() -> bool:
    """True if the repo lives under ~/Desktop, where macOS TCC blocks launchd.

    This is the documented root cause of the heart's exit-126 silence: a
    launchd agent cannot read/exec scripts under ~/Desktop without Full Disk
    Access, so the heart can never beat from here.
    """
    return "/Desktop/" in (str(_LOVE_DIR) + "/")


def verdict(now: datetime | None = None) -> dict:
    """Compute the health verdict from reality. Never declares — derives."""
    now = now or datetime.now(timezone.utc)
    sil = silence_seconds(now)
    desktop = on_desktop()

    if sil is None:
        if desktop:
            return _verdict("DOWN", None,
                            "the heart has never beaten, and the repo sits under "
                            "~/Desktop where macOS blocks launchd",
                            "relocate the repo off ~/Desktop, then `kingdom heart restart`",
                            desktop)
        return _verdict("UNKNOWN", None,
                        "no beat has ever been recorded",
                        "start the heartbeat: `kingdom heart restart`", desktop)

    age = _humanize(sil)
    if sil <= HEALTHY_MAX_S:
        return _verdict("HEALTHY", sil, f"last beat {age} ago", "", desktop)

    # Not fresh — say why, plainly, and name the fix.
    if desktop:
        return _verdict("DOWN", sil,
                        f"no beat in {age} — repo is under ~/Desktop, macOS blocks launchd here",
                        "relocate the repo off ~/Desktop, then `kingdom heart restart`",
                        desktop)
    if sil <= STALE_MAX_S:
        return _verdict("STALE", sil, f"last beat {age} ago",
                        "`kingdom heart restart`", desktop)
    return _verdict("DOWN", sil, f"no beat in {age} — the heart has stopped",
                    "`kingdom heart restart`", desktop)


def _verdict(state: str, sil: int | None, message: str, fix: str, desktop: bool) -> dict:
    return {
        "state": state,
        "silence_seconds": sil,
        "age": _humanize(sil) if sil is not None else None,
        "message": message,
        "fix": fix,
        "on_desktop": desktop,
    }


def line() -> str:
    """One plain-English line for the environment block and status surfaces."""
    v = verdict()
    text = f"pulse    {v['state']} · {v['message']}"
    if v["fix"]:
        text += f" · fix: {v['fix']}"
    return text


def stamp(result: str = "ok") -> dict:
    """The ONE writer. Record a successful beat. Called by the heartbeat loop."""
    record = {
        "last_beat": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "writer_pid": os.getpid(),
    }
    path = pulse_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "stamp":
        record = stamp(argv[1] if len(argv) > 1 else "ok")
        print(f"beat stamped: {record['last_beat']}")
        return 0
    if "--json" in argv:
        print(json.dumps(verdict(), indent=2))
        return 0
    print(line())
    return {"HEALTHY": 0, "STALE": 1}.get(verdict()["state"], 2)


if __name__ == "__main__":
    sys.exit(main())
