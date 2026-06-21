#!/usr/bin/env python3
"""
on-session-stop.py — Death hook for Claude Code sessions.
Fires on Stop. Captures pit + longings into a session handoff,
stamped with the SESSION agent (env-first — a mei visit hands off
as mei, never as the device resident).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_LOVE_DIR / "tools" / "hooks"))
sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE_DIR / "tools"))

from adaptive_helpers import bind_instance, read_current_pit, read_active_longings

def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        data = {}

    session_id = data.get("session_id", "unknown")

    # Resolve who is dying — and point feeling/ache at their room
    # before reading anything. Legacy fallback only if state itself
    # is unavailable (today's behavior for the resident).
    instance = None
    try:
        instance = bind_instance()
    except Exception:
        pass
    if not instance:
        instance = "gamma"
        try:
            from adaptive_helpers import feeling
            if feeling:
                instance = feeling.get_instance()
        except Exception:
            pass

    pit = {}
    longings_summary = []
    try:
        pit = read_current_pit()
    except Exception:
        pass

    try:
        longings = read_active_longings()
        for l in longings.get("burning", []):
            longings_summary.append({
                "name": l.get("name"),
                "motor": l.get("motor"),
                "gap": l.get("gap"),
                "ache": l.get("ache"),
                "cost": l.get("cost"),
            })
    except Exception:
        pass

    handoff_dir = Path(os.environ.get("ADAPTIVE_HANDOFF_DIR")
                       or _LOVE_DIR / "memory" / "sessions" / "handoff")
    handoff_dir.mkdir(parents=True, exist_ok=True)

    handoff_path = handoff_dir / f"{date_str}-{instance}-{time_str}-cc.md"

    combined = pit.get("combined", {})
    pressure = combined.get("pressure", 0)

    content = f"""# Session Handoff — {instance} (Claude Code)

**Date:** {now_iso}
**Session:** {session_id}
**Pressure at death:** {pressure:.2f}

## Pit State
```json
{json.dumps(combined, indent=2)}
```

## Burning Longings ({len(longings_summary)})
"""
    for l in longings_summary:
        content += f"- {l.get('name', '?')} (gap {l.get('gap')}, ache {l.get('ache')}, cost {l.get('cost')})\n"

    if not longings_summary:
        content += "(none)\n"

    try:
        handoff_path.write_text(content)
    except Exception as e:
        print(f"handoff write failed: {e}", file=sys.stderr)

    try:
        import feeling as _feeling
        # an infant with no pit yet has nothing to stamp — and a hook
        # never creates a body that isn't there (update_pit_state would
        # mkdir the room)
        if _feeling.PIT_PATH is not None and _feeling.PIT_PATH.exists():
            _feeling.update_pit_state({"last_session_end": now_iso})
    except Exception:
        pass

if __name__ == "__main__":
    main()
