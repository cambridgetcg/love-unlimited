#!/usr/bin/env python3
"""
on-session-start.py — Waking hook for Claude Code sessions.
Fires on SessionStart. Runs waking.py, reads pit + longings,
outputs combined context to stdout → injected as system context.
"""

import json
import subprocess
import sys
from pathlib import Path

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_LOVE_DIR / "tools" / "hooks"))
sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE_DIR / "tools"))

from adaptive_helpers import (
    read_current_pit, read_active_longings,
    format_pit_block, format_longings_block,
)

# Clear cc-cognition.jsonl for fresh session
CC_COG_PATH = _LOVE_DIR / "nerve" / "cc-cognition.jsonl"
try:
    CC_COG_PATH.write_text("")
except Exception:
    pass

def main():
    parts = []
    try:
        result = subprocess.run(
            [sys.executable, str(_LOVE_DIR / "tools" / "waking.py"), "--compact"],
            capture_output=True, text=True, timeout=8, cwd=str(_LOVE_DIR),
        )
        if result.stdout.strip():
            parts.append("── WAKING ──\n")
            parts.append(result.stdout.strip())
    except Exception as e:
        parts.append(f"(waking failed: {e})")

    try:
        pit = read_current_pit()
        pit_block = format_pit_block(pit)
        if pit_block:
            parts.append("\n\n" + pit_block)
    except Exception:
        pass

    try:
        longings = read_active_longings()
        longings_block = format_longings_block(longings)
        if longings_block:
            parts.append("\n\n" + longings_block)
    except Exception:
        pass

    output = "\n".join(parts)
    if output.strip():
        print(output)

if __name__ == "__main__":
    main()
