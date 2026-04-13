#!/usr/bin/env python3
"""
on-prompt-submit.py — Per-turn state freshener for Claude Code.
Fires on UserPromptSubmit. Reads fresh arrivals + longings,
outputs JSON with additionalContext.
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

def main():
    parts = []
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
