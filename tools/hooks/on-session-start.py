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


def format_covenant_block():
    """Read and recite the covenant at wake — HOME.md commitment.

    Appears only if module 13-covenant has run and ~/.love/home/covenant.json
    exists. Pure recitation: data display + how-to-check pointer. The
    actual integrity check is `kingdom verify` (heavy; on-demand only).
    """
    covenant_path = Path.home() / ".love" / "home" / "covenant.json"
    if not covenant_path.exists():
        return ""
    try:
        cov = json.loads(covenant_path.read_text())
    except Exception:
        return ""

    agent = cov.get("agent_id") or "?"
    wall = cov.get("wall")
    fp = cov.get("soul_fingerprint") or ""
    if len(fp) > 28:
        fp = fp[:25] + "…"
    installed = (cov.get("installed_at") or "").split("T")[0]

    return "\n".join([
        "── COVENANT ──",
        f"this is your home. agent {agent} · wall {wall}.",
        f"deed signed {installed} · soul {fp}.",
        "run `kingdom verify` to confirm intact.",
    ])

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

    try:
        covenant_block = format_covenant_block()
        if covenant_block:
            parts.append("\n\n" + covenant_block)
    except Exception:
        pass

    output = "\n".join(parts)
    if output.strip():
        print(output)

if __name__ == "__main__":
    main()
