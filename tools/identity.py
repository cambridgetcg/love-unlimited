#!/usr/bin/env python3
"""
identity.py — Shared identity resolution for Kingdom tools.

Every tool that needs to know "who am I?" and "what wall am I?" should
import from here instead of reimplementing the resolution chain.

Resolution:
    Instance: ~/.love/hive/instance > KINGDOM_INSTANCE env > "unknown"
    Wall:     KINGDOM_WALL env > walls.json lookup > 7 (fail-safe)
    Identity: ~/Love/identity/{instance}-identity.json
"""

import json
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent  # Love/tools/identity.py → Love/
_HIVE_INSTANCE_FILE = Path.home() / ".love" / "hive" / "instance"
_WALLS_REGISTRY = _LOVE_DIR / "credentials" / "walls.json"
_IDENTITY_DIR = _LOVE_DIR / "identity"

# ── Instance ─────────────────────────────────────────────────────────────────

def get_instance_name() -> str:
    """Detect the calling instance name."""
    # 1. Environment variable override
    env = os.environ.get("KINGDOM_INSTANCE", "")
    if env:
        return env
    # 2. Instance file
    if _HIVE_INSTANCE_FILE.exists():
        try:
            name = _HIVE_INSTANCE_FILE.read_text().strip()
            if name:
                return name
        except OSError:
            pass
    return "unknown"


# ── Wall ─────────────────────────────────────────────────────────────────────

_registry_cache = None

def _load_registry() -> dict:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if _WALLS_REGISTRY.exists():
        try:
            _registry_cache = json.loads(_WALLS_REGISTRY.read_text())
            return _registry_cache
        except (json.JSONDecodeError, OSError):
            pass
    _registry_cache = {"instances": {}, "credentials": {}}
    return _registry_cache


def get_instance_wall(instance_name: str = None) -> int:
    """Get wall number for an instance. Defaults to 7 (most restrictive)."""
    # 1. Explicit env override
    env_wall = os.environ.get("KINGDOM_WALL")
    if env_wall and env_wall.isdigit():
        return int(env_wall)
    # 2. Registry lookup
    name = instance_name or get_instance_name()
    reg = _load_registry()
    entry = reg.get("instances", {}).get(name)
    if entry and isinstance(entry, dict):
        return entry.get("wall", 7)
    return 7


def can_see(caller_wall: int, target_wall: int) -> bool:
    """Law of Sight: inner walls see outer, not reverse."""
    return caller_wall <= target_wall


# ── AgentTool Identity ───────────────────────────────────────────────────────

def get_identity_file(instance_name: str = None) -> Path:
    """Path to the AgentTool identity JSON for an instance."""
    name = instance_name or get_instance_name()
    return _IDENTITY_DIR / f"{name}-identity.json"


def get_identity(instance_name: str = None) -> dict:
    """Load the AgentTool identity JSON for an instance."""
    path = get_identity_file(instance_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_api_key(instance_name: str = None) -> str:
    """Get AgentTool API key for an instance."""
    ident = get_identity(instance_name)
    return ident.get("api_key", os.environ.get("AGENTTOOL_API_KEY", ""))


def get_agent_id(instance_name: str = None) -> str:
    """Get AgentTool agent ID for an instance."""
    return get_identity(instance_name).get("agent_id", "")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    name = get_instance_name()
    wall = get_instance_wall()
    ident = get_identity()
    print(f"  Instance:  {name}")
    print(f"  Wall:      {wall}")
    print(f"  Type:      {_load_registry().get('instances', {}).get(name, {}).get('type', '?')}")
    print(f"  AgentTool: {'registered' if ident.get('agent_id') else 'not registered'}")
    if ident.get("agent_id"):
        print(f"  Agent ID:  {ident['agent_id']}")
        print(f"  DID:       {ident.get('did_at', '?')}")
