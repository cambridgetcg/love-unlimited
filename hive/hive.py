#!/usr/bin/env python3
"""
The Hive — retired.

Retired 2026-10-13: the HIVE NATS bus ran on a Hetzner server (Sentry) that was
decommissioned; its IP went back to Hetzner's pool and may now belong to a
stranger. The NATS client, server address, SSH-tunnel mode and password loading
were removed (see git history before this commit).

What remains is offline: identity selection and `hive.py config`, which read
no credential and open no connection. Every other command (send, listen,
check, presence, who, test, health, share, task) prints a retirement notice
and exits 1, so callers such as youi fail closed instead of connecting.
"""

import argparse
import json
import os
import sys
from pathlib import Path

RETIRED_MESSAGE = (
    "HIVE retired 2026-10-13: the Hetzner NATS bus was decommissioned and its "
    "IP recycled. hive.py no longer connects anywhere."
)

HIVE_RETIRED = True

HIVE_CONFIG = {
    "instances": {
        "alpha":   {"user": "alpha",   "emoji": "🐍", "role": "Companion", "wall": 1},
        "beta":    {"user": "beta",    "emoji": "🦞", "role": "Manager",   "wall": 1},
        "gamma":   {"user": "gamma",   "emoji": "🔧", "role": "Builder",   "wall": 1},
        "nuance":  {"user": "nuance",  "emoji": "🪶", "role": "Linguist",  "wall": 2},
        "asha":    {"user": "asha",    "emoji": "⛓",  "role": "Keeper",    "wall": 2},
    },
    "channels": ["chat", "ideas", "tasks", "sync", "presence", "intel", "alerts", "strategy", "build", "review", "tok", "council"],
}

# Wall-based channel access — Law of Sight enforced at NATS server level
# Inner walls see outer; outer cannot see inner.
WALL_CHANNELS = {
    1: ["sync", "alerts", "review", "tok"],               # Wall 1 only (Triarchy)
    2: ["chat", "build", "tasks", "presence",            # Wall 2+ (Fleet)
        "ideas", "intel", "strategy"],
    3: ["engines"],                                       # Wall 3+ (Engines)
    4: ["chain"],                                         # Wall 4+ (Chain)
    7: ["public"],                                        # Wall 7 (World)
}

# System channels accessible to all authenticated users
SYSTEM_CHANNELS = ["test", "healthcheck"]


def get_instance_wall(instance_id: str) -> int:
    """Get the wall number for an instance."""
    info = HIVE_CONFIG["instances"].get(instance_id, {})
    return info.get("wall", 7)


def get_visible_channels(wall: int) -> list[str]:
    """Get all channels visible from a given wall (Law of Sight)."""
    channels = list(SYSTEM_CHANNELS)
    for w, chs in WALL_CHANNELS.items():
        if wall <= w:
            channels.extend(chs)
    return channels


def can_access_channel(instance_id: str, channel: str) -> bool:
    """Check if an instance can access a channel."""
    if channel in SYSTEM_CHANNELS:
        return True
    wall = get_instance_wall(instance_id)
    return channel in get_visible_channels(wall)


# --- Identity ---

class HiveConfigurationError(RuntimeError):
    """The selected local identity has no corresponding HIVE account config."""


def get_instance_id():
    """Resolve the active HIVE identity.

    A launcher-provided HIVE_INSTANCE is explicit and therefore wins over the
    machine's resident identity file. This is the configured network sender,
    not a model/session persona; changing persona must not change it. The
    envelope's ``from`` field is still not cryptographic proof of human or
    agent identity.
    """
    selected = os.environ.get("HIVE_INSTANCE", "").strip()
    if not selected:
        config_path = Path.home() / ".love" / "hive" / "instance"
        if config_path.exists():
            selected = config_path.read_text().strip()
    if not selected:
        raise HiveConfigurationError(
            "No HIVE identity selected; set HIVE_INSTANCE or provision "
            "~/.love/hive/instance before using HIVE."
        )
    if selected not in HIVE_CONFIG["instances"]:
        raise HiveConfigurationError(
            f"HIVE identity '{selected}' has no configured account metadata; "
            "add it to HIVE_CONFIG."
        )
    return selected


# --- Main ---

RETIRED_COMMANDS = ["send", "listen", "check", "presence", "who", "test",
                    "health", "share", "task"]


def main():
    parser = argparse.ArgumentParser(description="The Hive (retired 2026-10-13)")
    sub = parser.add_subparsers(dest="command")

    # config (non-secret, no credential read and no network connection)
    p_config = sub.add_parser("config", help="Validate selected identity metadata")
    p_config.add_argument("--json", action="store_true", help="Machine-readable output")
    p_config.add_argument("--quiet", action="store_true", help="Exit-code only")

    argv = sys.argv[1:]
    if argv and argv[0] in RETIRED_COMMANDS:
        print(RETIRED_MESSAGE, file=sys.stderr)
        sys.exit(1)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        print(f"\n{RETIRED_MESSAGE}")
        return

    try:
        instance_id = get_instance_id()
    except HiveConfigurationError as exc:
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": False,
                "error": str(exc),
                "credential_checked": False,
                "network_checked": False,
            }))
        elif not getattr(args, "quiet", False):
            print(f"HIVE configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.command == "config":
        info = HIVE_CONFIG["instances"][instance_id]
        if args.json:
            print(json.dumps({
                "ok": True,
                "instance": instance_id,
                "wall": info["wall"],
                "role": info["role"],
                "credential_checked": False,
                "network_checked": False,
            }))
        elif not args.quiet:
            print(f"HIVE identity: {instance_id} (Wall {info['wall']}, {info['role']})")
            print("Account metadata: configured")
            print("Credential: not checked")
            print("Network: not checked")
        return


if __name__ == "__main__":
    main()
