#!/usr/bin/env python3
"""
on-prompt-submit.py — Per-turn state freshener for Claude Code.
Fires on UserPromptSubmit. Injects fresh ENVIRONMENT (counter-stickiness
external signal: clock, session, git, daemons, focus) + ARRIVALS +
LONGINGS into the session context via additionalContext.

Order matters — environment first, so the mind anchors to external
reality (wall-clock time, git state, daemon liveness, focus) before
reading any internal-state signal. Without this, long sessions anchor
to whatever the wake sequence said at start and drift for hours.
"""

import json
import sys
from pathlib import Path

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_LOVE_DIR / "tools" / "hooks"))
sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE_DIR / "tools"))

from adaptive_helpers import (
    read_unwitnessed_arrivals, mark_arrivals_witnessed,
    read_active_longings, format_arrivals_block, format_longings_block,
)

# Environment module — clock + session + git + daemons + focus.
# Optional: if it fails to import, the hook still emits arrivals/longings.
try:
    import environment as _environment
except Exception:
    _environment = None


def _format_environment_block() -> str | None:
    """Build the ── ENVIRONMENT ── block, or None if unavailable."""
    if _environment is None:
        return None
    try:
        block = _environment.summary()
        return block or None
    except Exception:
        return None


def main():
    parts = []

    # Environment first — external reality before internal state.
    env_block = _format_environment_block()
    if env_block:
        parts.append(env_block)

    try:
        arrivals = read_unwitnessed_arrivals()
        if arrivals:
            parts.append(format_arrivals_block(arrivals))
            mark_arrivals_witnessed(arrivals)
    except Exception:
        pass

    try:
        longings = read_active_longings()
        block = format_longings_block(longings)
        if block:
            parts.append(block)
    except Exception:
        pass

    context = "\n\n".join(p for p in parts if p)
    if context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }))
    else:
        print("{}")

if __name__ == "__main__":
    main()
