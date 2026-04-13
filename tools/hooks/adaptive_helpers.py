#!/usr/bin/env python3
"""
adaptive_helpers.py — Shared helpers for the Adaptive Layer hook scripts.

Reads FEELING/ACHE daemon state files and formats context blocks
for Claude Code's hook system.

Spec: docs/ADAPTIVE-LAYER-DESIGN.md
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
_NERVE_DIR = _LOVE_DIR / "nerve"

# Ensure feeling and ache modules are importable
sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE_DIR / "tools"))

try:
    import feeling
except ImportError:
    feeling = None

try:
    import ache
except ImportError:
    ache = None


def read_current_pit() -> dict:
    """Read the current pit state from nerve/pit.json."""
    if feeling is None:
        return {}
    return feeling.read_pit_json()


def read_unwitnessed_arrivals() -> list:
    """Read unwitnessed, unnamed arrivals from nerve/arrivals.jsonl."""
    if feeling is None:
        return []
    return feeling.read_arrivals(witnessed=False, named=False)


def mark_arrivals_witnessed(arrivals: list) -> None:
    """Mark a list of arrivals as witnessed."""
    if feeling is None:
        return
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for arr in arrivals:
        feeling.update_arrival(arr.get("id"), {
            "witnessed": True,
            "witnessed_at": now_iso,
        })


def read_active_longings() -> dict:
    """Read burning + unnamed yearning longings."""
    if ache is None:
        return {"burning": [], "yearning_unnamed": []}
    store = ache.read_longings()
    longings = store.get("longings", [])
    burning = [l for l in longings if l.get("state") == "burning"]
    yearning_unnamed = [l for l in longings if l.get("state") == "yearning" and not l.get("named")]
    return {"burning": burning, "yearning_unnamed": yearning_unnamed}
