"""
bridge.py — HIVE ↔ Voice bridge.

Routes messages between Kingdom's internal HIVE network and OpenClaw's
external channels. This is the membrane between inside and outside.

Inbound:  Channel message → OpenClaw → bridge → HIVE → Kingdom agent
Outbound: Kingdom agent → HIVE → bridge → OpenClaw → Channel delivery

Wall-gated: only messages appropriate for the channel's wall level pass through.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Desktop" / "Love"))
HIVE_CMD = LOVE_DIR / "hive" / "hive.py"
MEMORY_DIR = LOVE_DIR / "memory"


def hive_send(channel: str, message: str, sender: str = "voice") -> str:
    """Send a message to HIVE."""
    try:
        result = subprocess.run(
            ["python3", str(HIVE_CMD), "send", channel, message, "--from", sender],
            capture_output=True, text=True, timeout=10, cwd=str(LOVE_DIR),
        )
        return result.stdout.strip()
    except Exception as e:
        return f"HIVE send error: {e}"


def hive_read(channel: str, limit: int = 5) -> list[dict]:
    """Read recent HIVE messages."""
    try:
        result = subprocess.run(
            ["python3", str(HIVE_CMD), "read", channel, "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=10, cwd=str(LOVE_DIR),
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return []


def route_inbound(channel_name: str, sender: str, message: str, wall: int = 6):
    """Route an inbound channel message into the Kingdom.

    Channel messages enter at their assigned wall level and are
    routed to the appropriate HIVE channel for Kingdom processing.
    """
    # Wall-gated routing
    if wall >= 6:
        hive_channel = "chat"  # Public/user messages go to #chat
    elif wall >= 4:
        hive_channel = "tasks"  # Partner/chain messages go to #tasks
    else:
        hive_channel = "intel"  # Inner wall messages go to #intel

    envelope = {
        "type": "voice.inbound",
        "channel": channel_name,
        "sender": sender,
        "message": message,
        "wall": wall,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    hive_send(hive_channel, json.dumps(envelope), sender="voice-bridge")

    # Log to daily note
    _log_to_daily(f"Voice inbound [{channel_name}] from {sender}: {message[:100]}")


def route_outbound(hive_channel: str, target_channel: str, message: str):
    """Route a HIVE message outbound to an external channel.

    The heartbeat or an active mind session writes a message to HIVE
    tagged for external delivery. The bridge picks it up and routes
    it through OpenClaw to the target channel.
    """
    envelope = {
        "type": "voice.outbound",
        "hive_channel": hive_channel,
        "target": target_channel,
        "message": message,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # Write to outbound queue for OpenClaw gateway to process
    outbound_dir = MEMORY_DIR / "voice-outbound"
    outbound_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outbound_file = outbound_dir / f"out-{ts}.json"
    outbound_file.write_text(json.dumps(envelope, indent=2))

    _log_to_daily(f"Voice outbound [{target_channel}]: {message[:100]}")


def _log_to_daily(entry: str):
    """Append a line to today's daily note."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_file = MEMORY_DIR / "daily" / f"{today}.md"
    daily_file.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%H:%M")
    with open(daily_file, "a") as f:
        f.write(f"\n- [{ts}] {entry}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HIVE ↔ Voice bridge")
    sub = parser.add_subparsers(dest="command")

    inbound = sub.add_parser("inbound", help="Route inbound channel message to HIVE")
    inbound.add_argument("channel", help="Source channel name")
    inbound.add_argument("sender", help="Message sender")
    inbound.add_argument("message", help="Message content")
    inbound.add_argument("--wall", type=int, default=6, help="Wall level (default: 6)")

    outbound = sub.add_parser("outbound", help="Route HIVE message to external channel")
    outbound.add_argument("hive_channel", help="Source HIVE channel")
    outbound.add_argument("target", help="Target external channel")
    outbound.add_argument("message", help="Message content")

    args = parser.parse_args()

    if args.command == "inbound":
        route_inbound(args.channel, args.sender, args.message, args.wall)
        print(f"Routed to HIVE")
    elif args.command == "outbound":
        route_outbound(args.hive_channel, args.target, args.message)
        print(f"Queued for delivery via {args.target}")
    else:
        parser.print_help()
