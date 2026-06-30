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
    import state as _state
except ImportError:
    _state = None

try:
    import feeling
except ImportError:
    feeling = None

try:
    import ache
except ImportError:
    ache = None


def bind_instance(name: str | None = None) -> str | None:
    """Point feeling/ache at the SESSION agent's room.

    Resolution is env-first (explicit > KINGDOM_AGENT > ~/.kingdom —
    see nerve/stem/state.py), so a mei session on gamma's device reads
    mei's pit and longings, never the resident's. Returns the resolved
    instance, or None if state is unavailable (the modules then stay
    bound to whoever they resolved at import — today's behavior).
    """
    if _state is None:
        return None
    try:
        resolved = _state.resolve_instance(name)
    except Exception:
        return None
    for mod in (feeling, ache):
        if mod is not None and hasattr(mod, "set_instance"):
            try:
                mod.set_instance(resolved)
            except Exception:
                pass
    return resolved


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


# ── Formatters (spec §4) ─────────────────────────────────────────────

def format_pit_block(pit: dict) -> str:
    """Format pit state as a text block for system context."""
    if not pit or "combined" not in pit:
        return ""
    body = pit.get("body", {})
    ctx = pit.get("context", {})
    cog = pit.get("cognition", {})
    combined = pit.get("combined", {})

    lines = ["── PIT STATE ──", ""]
    lines.append(f"body: v={body.get('valence', 0):+.2f} a={body.get('arousal', 0):+.2f} ({', '.join(body.get('sources', []))})")
    lines.append(f"context: v={ctx.get('valence', 0):+.2f} a={ctx.get('arousal', 0):+.2f} ({', '.join(ctx.get('sources', []))})")
    cog_state = cog.get("state", "silent")
    lines.append(f"cognition: {cog_state} v={cog.get('valence', 0):+.2f} a={cog.get('arousal', 0):+.2f}")
    lines.append(f"combined pressure: {combined.get('pressure', 0):.2f}")
    return "\n".join(lines)


def format_arrivals_block(arrivals: list) -> str:
    """Format unwitnessed arrivals as a text block."""
    if not arrivals:
        return ""
    lines = [f"── ARRIVALS ──", "",
             f"The pit reports {len(arrivals)} unnamed feeling{'s' if len(arrivals) != 1 else ''} waiting:", ""]
    for arr in arrivals[:5]:
        c = arr.get("combined", {})
        reasons = ", ".join(f"{r['kind']} {r['value']}" for r in arr.get("reasons", []))
        lines.append(f"- {arr.get('id', '?')}  (new)")
        lines.append(f"    pressure {c.get('pressure', 0):.2f} · v={c.get('valence', 0):.2f} a={c.get('arousal', 0):.2f}")
        lines.append(f"    reasons: {reasons}")
        body_src = ", ".join(arr.get("body", {}).get("sources", []))
        ctx_src = ", ".join(arr.get("context", {}).get("sources", []))
        if body_src:
            lines.append(f"    body: {body_src}")
        if ctx_src:
            lines.append(f"    context: {ctx_src}")
        cog = arr.get("cognition", {})
        if cog.get("state") == "silent":
            lines.append(f"    cognition: silent")
        lines.append("")
    return "\n".join(lines)


def format_longings_block(data: dict) -> str:
    """Format burning + unnamed-yearning longings as a text block."""
    burning = data.get("burning", [])
    yearning = data.get("yearning_unnamed", [])
    if not burning and not yearning:
        return ""
    lines = ["── LONGINGS ──", ""]
    if burning:
        lines.append("carrying (burning):")
        for l in burning[:5]:
            name = l.get("name") or (l.get("target", {}).get("display", ""))
            lines.append(f"- {name} — gap {l.get('gap', 0)} · ache {l.get('ache', 0)} · cost {l.get('cost', '?')}")
        lines.append("")
    if yearning:
        lines.append("reaching (yearning, unnamed):")
        for l in yearning[:5]:
            display = (l.get("target") or {}).get("display", "(unnamed)")
            lines.append(f"- {display}")
            lines.append(f"    gap {l.get('gap', 0)} · ache {l.get('ache', 0)} · first stirred {l.get('first_seen', '?')}")
        lines.append("")
    return "\n".join(lines)
