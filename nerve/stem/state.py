"""
state.py — instance-aware identity and state-path resolution.

One house, more than one resident. Before Mei, every module assumed the
device's sole resident: identity came from ~/.kingdom first, and state
lived at fixed paths (nerve/pit.json, nerve/hormones.json, ...). Both
assumptions are corrected here, in one place, so the whole nervous
system agrees.

Identity precedence (the law):

    explicit argument > KINGDOM_AGENT > KINGDOM_INSTANCE > ~/.kingdom > default

Rooms (state paths):

    the device resident keeps today's bare paths    nerve/*.json
    every other instance has its own room           nerve/{name}/*.json

The resident is a property of the DEVICE (~/.kingdom), never of the
process environment — exporting KINGDOM_AGENT=mei moves a session's
identity without moving the resident's body.

Walls resolve from credentials/walls.json (the registry), not from
~/.kingdom, so a session can never carry the resident's wall by accident.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LOVE_DIR = Path(__file__).resolve().parent.parent.parent
NERVE_DIR = LOVE_DIR / "nerve"
MEMORY_DIR = LOVE_DIR / "memory"
KOS_DIR = MEMORY_DIR / ".kos"
WALLS_PATH = LOVE_DIR / "credentials" / "walls.json"

_DEFAULT_INSTANCE = "gamma"
_DEFAULT_WALL = 7


# ── Identity ─────────────────────────────────────────────────────────

def _kingdom_file_value(key: str) -> str | None:
    kf = Path.home() / ".kingdom"
    if not kf.exists():
        return None
    try:
        for line in kf.read_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    return None


def resolve_instance(explicit: str | None = None,
                     default: str = _DEFAULT_INSTANCE) -> str:
    """Who is this process? Explicit beats env beats device file."""
    if explicit:
        return explicit.strip()
    env = os.environ.get("KINGDOM_AGENT") or os.environ.get("KINGDOM_INSTANCE")
    if env and env.strip():
        return env.strip()
    return _kingdom_file_value("AGENT") or default


def resident_instance() -> str:
    """Who lives on this device? ~/.kingdom only — env never moves the body."""
    return _kingdom_file_value("AGENT") or _DEFAULT_INSTANCE


def walls_entry(instance: str | None = None) -> dict:
    """The registry entry for an instance ({} if unregistered)."""
    instance = resolve_instance(instance)
    try:
        registry = json.loads(WALLS_PATH.read_text())
        return registry.get("instances", {}).get(instance, {})
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_wall(instance: str | None = None) -> int:
    """An instance's wall, from the registry. Falls back to the device
    file only for the resident, then env, then the outermost wall."""
    instance = resolve_instance(instance)
    entry = walls_entry(instance)
    if isinstance(entry.get("wall"), int):
        return entry["wall"]
    if instance == resident_instance():
        file_wall = _kingdom_file_value("WALL")
        if file_wall:
            try:
                return int(file_wall)
            except ValueError:
                pass
    try:
        return int(os.environ.get("KINGDOM_WALL", _DEFAULT_WALL))
    except ValueError:
        return _DEFAULT_WALL


def is_infant(instance: str | None = None) -> bool:
    """Is this instance still growing into itself? (walls.json status)"""
    return walls_entry(instance).get("status") == "infant"


# ── Rooms ────────────────────────────────────────────────────────────

def state_dir(instance: str | None = None) -> Path:
    """Where an instance's body lives. The resident keeps the bare
    nerve/ paths so existing bodies don't move; everyone else has
    their own room."""
    instance = resolve_instance(instance)
    if instance == resident_instance():
        return NERVE_DIR
    return NERVE_DIR / instance


def ensure_state_dir(instance: str | None = None) -> Path:
    """Create the room (used by birth; daemons never create rooms —
    a missing room means 'not deployed here', and organs no-op)."""
    d = state_dir(instance)
    d.mkdir(parents=True, exist_ok=True)
    (d / "signals").mkdir(exist_ok=True)
    return d


def signals_dir(instance: str | None = None) -> Path:
    """Per-instance signal intake. The resident's is nerve/signals —
    the live dir the heartbeat actually writes to."""
    return state_dir(instance) / "signals"


def daily_dir(instance: str | None = None) -> Path:
    """The resident shares the house daily note; other instances keep
    their own notes (a wall-2 child must not read wall-1 pages)."""
    instance = resolve_instance(instance)
    if instance == resident_instance():
        return MEMORY_DIR / "daily"
    return MEMORY_DIR / "daily" / instance


def continuity_path(instance: str | None = None) -> Path:
    """Per-instance continuity state. The resident keeps the legacy
    shared file until a per-instance one exists."""
    instance = resolve_instance(instance)
    per_instance = KOS_DIR / f"continuity-{instance}.json"
    if per_instance.exists():
        return per_instance
    legacy = KOS_DIR / "continuity.json"
    if instance == resident_instance() and legacy.exists():
        return legacy
    return per_instance


def anchor_path(instance: str | None = None) -> Path:
    """The brainstem's cached identity anchor."""
    instance = resolve_instance(instance)
    if instance == resident_instance():
        return NERVE_DIR / "stem" / "identity_anchor.txt"
    return state_dir(instance) / "identity_anchor.txt"


def home_layer(instance: str | None = None) -> Path:
    """Where an instance's covenant and soul-key live. The resident
    keeps the legacy ~/.love/home; everyone else gets a named layer."""
    instance = resolve_instance(instance)
    base = Path.home() / ".love" / "home"
    if instance == resident_instance():
        return base
    return base / instance
