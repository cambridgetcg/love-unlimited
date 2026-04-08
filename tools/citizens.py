#!/usr/bin/env python3
"""
citizens.py — Kingdom Citizen Registry and Activation Status

Shows all Kingdom citizens, their activation status, and readiness for deployment.

Usage:
    citizens.py list              All citizens with status
    citizens.py ready             Citizens ready to deploy
    citizens.py active            Currently active (heartbeat running)
    citizens.py activate <name>   Show activation instructions for an agent
    citizens.py roster            Full roster with wall grouping
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default or {}


def check_agent_files(name):
    """Check what files exist for an agent."""
    inst = LOVE / "instances" / name
    files = {}
    for f in ["identity.md", "CLAUDE.md", "HEARTBEAT.md", "ONBOARDING.md"]:
        files[f] = (inst / f).exists()
    return files


def check_agent_active(name):
    """Check if agent has a running heartbeat (local device only)."""
    # Check launchd
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
        if f"love.{name}" in r.stdout or "love.heartbeat" in r.stdout:
            return True
    except Exception:
        pass
    return False


def get_activation_readiness(name, files, cfg):
    """Determine how ready an agent is for activation."""
    has_identity = files.get("identity.md", False)
    has_claude = files.get("CLAUDE.md", False)
    has_heartbeat = files.get("HEARTBEAT.md", False)
    has_device = bool(cfg.get("device", ""))

    if has_identity and has_claude and has_heartbeat and has_device:
        return "READY", GREEN
    elif has_identity and has_claude and has_heartbeat:
        return "NEEDS DEVICE", YELLOW
    elif has_identity and has_claude:
        return "NEEDS HEARTBEAT", YELLOW
    elif has_identity:
        return "PARTIAL", RED
    else:
        return "STUB", RED


def cmd_list():
    """List all citizens with status."""
    cfg = load_json(LOVE / "love.json")
    instances = cfg.get("instances", {})

    print(f"\n{BOLD}  Kingdom Citizens ({len(instances)}){NC}\n")
    print(f"  {'Name':12s} {'Wall':5s} {'Role':12s} {'Files':6s} {'Status':16s} {'Device'}")
    print(f"  {'─'*12} {'─'*5} {'─'*12} {'─'*6} {'─'*16} {'─'*20}")

    for name in sorted(instances.keys()):
        info = instances[name]
        files = check_agent_files(name)
        file_count = sum(1 for v in files.values() if v)
        readiness, color = get_activation_readiness(name, files, info)
        active = check_agent_active(name)
        device = info.get("device", "—")

        status = f"{GREEN}ACTIVE{NC}" if active else f"{color}{readiness}{NC}"
        emoji = info.get("emoji", "?")

        print(f"  {emoji} {name:10s} W{info.get('wall', '?'):2s}  {info.get('role', '?'):12s} {file_count}/4   {status:30s} {DIM}{device}{NC}")

    print()


def cmd_ready():
    """Show agents ready to deploy."""
    cfg = load_json(LOVE / "love.json")
    instances = cfg.get("instances", {})

    ready = []
    not_ready = []

    for name, info in instances.items():
        files = check_agent_files(name)
        readiness, _ = get_activation_readiness(name, files, info)
        if readiness in ("READY", "NEEDS DEVICE"):
            ready.append((name, info, readiness))
        else:
            not_ready.append((name, info, readiness))

    print(f"\n{BOLD}  Ready for Deployment ({len(ready)}/{len(instances)}){NC}\n")

    if ready:
        for name, info, status in ready:
            color = GREEN if status == "READY" else YELLOW
            print(f"  {info.get('emoji', '?')} {name:12s} {color}{status}{NC}")
    else:
        print(f"  {RED}No agents ready.{NC}")

    if not_ready:
        print(f"\n  {DIM}Not ready:{NC}")
        for name, info, status in not_ready:
            print(f"  {DIM}  {name:12s} {status}{NC}")

    print()


def cmd_activate(name):
    """Show activation instructions for a specific agent."""
    cfg = load_json(LOVE / "love.json")
    info = cfg.get("instances", {}).get(name)

    if not info:
        print(f"  Unknown agent: {name}")
        return

    files = check_agent_files(name)
    wall = info.get("wall", "?")
    device = info.get("device", "unknown")

    print(f"\n{BOLD}  Activation: {info.get('emoji', '?')} {name.upper()}{NC}")
    print(f"  Wall: {wall}  Role: {info.get('role', '?')}  Device: {device}\n")

    print(f"  {BOLD}File Status:{NC}")
    for f, exists in files.items():
        icon = f"{GREEN}●{NC}" if exists else f"{RED}○{NC}"
        print(f"    {icon} {f}")

    print(f"\n  {BOLD}Activation Steps:{NC}")

    if wall == 1:
        print(f"    1. On the device ({device}):")
        print(f"       cd ~/love-unlimited/instances/{name} && claude")
        print(f"    2. Or headless heartbeat:")
        print(f"       cd ~/love-unlimited/instances/{name} && claude -p 'Execute HEARTBEAT.md'")
    elif wall == 2:
        print(f"    1. On a device with kingdom-agent:")
        print(f"       python3 tools/kingdom-agent.py -p 'Execute HEARTBEAT.md' --instance {name}")
        print(f"    2. Or via Ollama (local model):")
        print(f"       KINGDOM_BACKEND=ollama python3 tools/kingdom-agent.py -p 'Execute HEARTBEAT.md' --instance {name}")
        print(f"    3. Or deploy to VPS:")
        print(f"       bash tools/fleet-agent-deploy.sh  # then run on VPS")
    elif wall == 3:
        print(f"    1. Via kingdom-agent (any backend):")
        print(f"       python3 tools/kingdom-agent.py -p 'Execute HEARTBEAT.md' --instance {name} --backend ollama")
        print(f"    2. Wall 3 agents are lightweight — suitable for VPS Ollama deployment")

    print(f"\n  {BOLD}For cron/launchd heartbeat:{NC}")
    print(f"    */7 * * * * cd ~/love-unlimited && python3 tools/kingdom-agent.py -p 'Execute HEARTBEAT.md' --instance {name} --backend claude --skip-permissions --no-persist >> memory/{name}-heartbeat.log 2>&1")
    print()


def cmd_roster():
    """Full roster grouped by wall."""
    cfg = load_json(LOVE / "love.json")
    instances = cfg.get("instances", {})

    print(f"\n{BOLD}  Kingdom Roster{NC}\n")

    for wall in [1, 2, 3, 4, 5, 6, 7]:
        agents = [(n, i) for n, i in instances.items() if i.get("wall") == wall]
        if not agents:
            continue

        wall_names = {1: "The Triarchy", 2: "The Fleet", 3: "The Engines",
                      4: "The Chain", 5: "The Partners", 6: "The Users", 7: "The World"}
        print(f"  {BOLD}Wall {wall} — {wall_names.get(wall, 'Unknown')}{NC}")

        for name, info in sorted(agents):
            files = check_agent_files(name)
            readiness, color = get_activation_readiness(name, files, info)
            active = check_agent_active(name)
            status = f"{GREEN}ACTIVE{NC}" if active else f"{color}{readiness}{NC}"

            print(f"    {info.get('emoji', '?')} {name:12s} {info.get('role', '?'):14s} {status}")

        print()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        cmd_list()
    elif cmd == "ready":
        cmd_ready()
    elif cmd == "active":
        # Show only active agents
        cfg = load_json(LOVE / "love.json")
        print(f"\n{BOLD}  Active Agents{NC}\n")
        found = False
        for name in cfg.get("instances", {}):
            if check_agent_active(name):
                info = cfg["instances"][name]
                print(f"  {GREEN}●{NC} {info.get('emoji', '?')} {name} ({info.get('role', '?')})")
                found = True
        if not found:
            print(f"  {DIM}No agents with active heartbeats detected.{NC}")
            print(f"  {DIM}(Beta's heartbeat runs as 'love.heartbeat', not per-agent){NC}")
        print()
    elif cmd == "activate":
        if len(sys.argv) < 3:
            print("Usage: citizens.py activate <name>")
            return
        cmd_activate(sys.argv[2])
    elif cmd == "roster":
        cmd_roster()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
