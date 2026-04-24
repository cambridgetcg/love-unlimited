#!/usr/bin/env python3
"""
on-prompt-submit.py — Per-turn state freshener for Claude Code.
Fires on UserPromptSubmit. Injects fresh time-of-day + arrivals +
longings into the session context via additionalContext.

Clock is always first so the mind anchors to current time before
reading any other per-turn signal. Without this, long sessions anchor
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

# Clock is optional — an import failure shouldn't break the hook
try:
    import clock as _clock
except Exception:
    _clock = None


def _format_clock_block() -> str | None:
    """Build the ── CLOCK ── block for context injection."""
    if _clock is None:
        return None
    try:
        c = _clock.now()
        return "── CLOCK ──\n" + _clock.format_line(c)
    except Exception:
        return None


def main():
    parts = []

    # Clock first — always anchor to current time before anything else.
    clock_block = _format_clock_block()
    if clock_block:
        parts.append(clock_block)

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
