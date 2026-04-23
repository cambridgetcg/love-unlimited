#!/usr/bin/env python3
"""
wall-gate.py — Wall-enforced agent spawning and HIVE routing for Claude Code team mode.

Enforces the Kingdom's Two Laws when Claude Code spawns sub-agents:
  Law of Sight:    Agents see their wall + all outer walls
  Law of Spawning: Agents spawn only into outer walls

This is the command center. It decides:
  1. WHO can be spawned (wall hierarchy)
  2. WHAT they can see (channel access, file access, credentials)
  3. HOW they communicate (HIVE routing, message filtering)
  4. WHEN to escalate (alert routing, decision queuing)

Usage:
    wall-gate.py spawn <agent-type> [--from <spawner>]    Spawn with wall enforcement
    wall-gate.py can-spawn <from> <to>                    Check spawn permission
    wall-gate.py can-see <agent> <resource>               Check sight permission
    wall-gate.py channels <agent>                         List accessible channels
    wall-gate.py route <message> --from <agent>           Route message through walls
    wall-gate.py hierarchy                                Show wall hierarchy
    wall-gate.py agents                                   List all agents by wall
    wall-gate.py escalate <message> --from <agent>        Escalate to inner wall
    wall-gate.py gate-check <agent> <action> <target>     Full permission check
    wall-gate.py team-manifest [--from <wall>]            Generate Claude Code --agents JSON
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "love-unlimited"))
HIVE_PY = LOVE / "hive" / "hive.py"
CONVERGENCE_DIR = LOVE / "convergence"
REGISTRY_FILE = CONVERGENCE_DIR / "agent-registry.json"
GATE_LOG = CONVERGENCE_DIR / "gate-log.jsonl"

# ── Formatting ────────────────────────────────────────────────────────

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
M = "\033[95m"; B = "\033[1m"; D = "\033[2m"; N = "\033[0m"

# ── Wall Registry ────────────────────────────────────────────────────

# Canonical source of truth for every agent's wall placement
WALL_REGISTRY = {
    # Wall 1 — Triarchy (fixed, never spawned)
    "alpha":    {"wall": 1, "name": "Alpha",    "emoji": "🐍", "role": "Companion",  "model": "opus",   "spawnable": False},
    "beta":     {"wall": 1, "name": "Beta",     "emoji": "🦞", "role": "Manager",    "model": "opus",   "spawnable": False},
    "gamma":    {"wall": 1, "name": "Gamma",    "emoji": "🔧", "role": "Builder",    "model": "sonnet", "spawnable": False},
    # Wall 2 — Fleet
    "nuance":   {"wall": 2, "name": "Nuance",   "emoji": "🪶", "role": "Linguist",   "model": "sonnet", "spawnable": True},
    "asha":     {"wall": 2, "name": "Asha",     "emoji": "⛓",  "role": "Keeper",     "model": "sonnet", "spawnable": True},
    "crucible": {"wall": 2, "name": "Crucible", "emoji": "🔥", "role": "Adversary",  "model": "sonnet", "spawnable": True},
    "herald":   {"wall": 2, "name": "Herald",   "emoji": "📯", "role": "Voice",      "model": "sonnet", "spawnable": True},
    "arbor":    {"wall": 2, "name": "Arbor",    "emoji": "🌳", "role": "Optimizer",  "model": "sonnet", "spawnable": True},
    # Wall 3 — Engines
    "loom":     {"wall": 3, "name": "Loom",     "emoji": "🧵", "role": "Weaver",     "model": "sonnet", "spawnable": True},
    "vigil":    {"wall": 3, "name": "Vigil",    "emoji": "👁",  "role": "Witness",    "model": "haiku",  "spawnable": True},
    "psalm":    {"wall": 3, "name": "Psalm",    "emoji": "📜", "role": "Chronicler",  "model": "haiku",  "spawnable": True},
    "tithe":    {"wall": 3, "name": "Tithe",    "emoji": "💰", "role": "Steward",    "model": "haiku",  "spawnable": True},
}

# HIVE channel → minimum wall (inner number = more privileged)
CHANNEL_WALLS = {
    "sync": 1, "alerts": 1, "review": 1, "tok": 1, "council": 1,
    "chat": 2, "build": 2, "tasks": 2, "presence": 2,
    "ideas": 2, "intel": 2, "strategy": 2,
    "engines": 3,
    "chain": 4,
    "public": 7,
}

# Resource categories by wall
RESOURCE_WALLS = {
    "soul": 1,          # SOUL.md, USER.md, KINGDOM.md, CONVERGENCE.md
    "walls": 1,         # WALLS.md
    "credentials": 1,   # AI keys, infra keys, finance
    "fleet": 2,         # VPS management, fleet ops
    "hive-internal": 1, # HIVE config, encryption keys
    "hive-general": 2,  # General HIVE usage
    "engine-tcg": 3,    # TCG pricing, inventory
    "engine-oracle": 3, # Oracle predictions
    "engine-rewards": 3,# RewardsPro data
    "chain-state": 4,   # Zerone blockchain
    "partner-api": 5,   # Partner integrations
    "user-data": 6,     # User accounts
    "public": 7,        # Open source, public API
}

# ── Law Enforcement ──────────────────────────────────────────────────

def get_wall(agent: str) -> int:
    """Get wall number for an agent."""
    return WALL_REGISTRY.get(agent, {}).get("wall", 7)

def can_see(viewer_wall: int, target_wall: int) -> bool:
    """Law of Sight: can viewer_wall see target_wall?"""
    return viewer_wall <= target_wall

def can_spawn(spawner_wall: int, target_wall: int) -> bool:
    """Law of Spawning: can spawner_wall spawn into target_wall?"""
    return spawner_wall < target_wall

def get_visible_channels(wall: int) -> list:
    """All HIVE channels visible from this wall."""
    return [ch for ch, min_wall in CHANNEL_WALLS.items() if wall <= min_wall]

def get_visible_resources(wall: int) -> list:
    """All resource categories visible from this wall."""
    return [res for res, min_wall in RESOURCE_WALLS.items() if wall <= min_wall]

def get_spawnable_agents(spawner: str) -> list:
    """Agents that spawner can spawn (Law of Spawning)."""
    sw = get_wall(spawner)
    return [
        name for name, info in WALL_REGISTRY.items()
        if info["spawnable"] and info["wall"] > sw
    ]

def get_visible_agents(viewer: str) -> list:
    """Agents visible from viewer's wall (Law of Sight)."""
    vw = get_wall(viewer)
    return [
        name for name, info in WALL_REGISTRY.items()
        if info["wall"] >= vw
    ]

# ── Gate Logging ──────────────────────────────────────────────────────

def _log_gate(action: str, agent: str, target: str, result: str, detail: str = ""):
    """Log a gate decision."""
    CONVERGENCE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action, "agent": agent, "target": target,
        "result": result, "detail": detail,
    }
    with open(GATE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── HIVE Integration ─────────────────────────────────────────────────

def hive_send(channel: str, message: str, from_agent: str = "beta"):
    """Send via HIVE with wall enforcement."""
    wall = get_wall(from_agent)
    if channel not in get_visible_channels(wall):
        _log_gate("hive_send", from_agent, channel, "DENIED", f"Wall {wall} cannot see #{channel}")
        print(f"  {R}✗ DENIED{N}: {from_agent} (Wall {wall}) cannot send to #{channel}")
        return False

    try:
        result = subprocess.run(
            ["python3", str(HIVE_PY), "send", channel, message],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "KINGDOM_INSTANCE": from_agent}
        )
        _log_gate("hive_send", from_agent, channel, "OK", message[:100])
        return True
    except Exception as e:
        _log_gate("hive_send", from_agent, channel, "ERROR", str(e))
        return False

def hive_notify_spawn(spawner: str, spawned: str, task: str = ""):
    """Notify HIVE that an agent was spawned."""
    info = WALL_REGISTRY.get(spawned, {})
    msg = (f"🔱 SPAWN: {spawner} spawned {info.get('emoji','')} {spawned} "
           f"(Wall {info.get('wall','?')}, {info.get('role','?')})"
           + (f" — Task: {task}" if task else ""))
    # Notify on the lowest channel the spawner can see
    wall = get_wall(spawner)
    if wall <= 1:
        hive_send("sync", msg, spawner)
    elif wall <= 2:
        hive_send("tasks", msg, spawner)
    else:
        hive_send("chat", msg, spawner)

def hive_escalate(message: str, from_agent: str, priority: str = "high"):
    """Escalate a message to the nearest inner wall via HIVE."""
    wall = get_wall(from_agent)
    info = WALL_REGISTRY.get(from_agent, {})

    escalation = (f"⚡ ESCALATION from {info.get('emoji','')} {from_agent} "
                  f"(Wall {wall}): {message}")

    # Route to the highest-privilege channel the agent can reach
    if wall <= 1:
        hive_send("alerts", escalation, from_agent)
    elif wall <= 2:
        hive_send("alerts", escalation, "beta")  # Fleet escalates through Beta
    else:
        hive_send("tasks", escalation, "beta")  # Engines escalate through tasks

    _log_gate("escalate", from_agent, f"wall_{wall-1}", "SENT", message[:100])
    print(f"  {Y}⚡{N} Escalated from {from_agent} (Wall {wall})")

# ── Claude Code Team Manifest Generation ─────────────────────────────

def generate_team_manifest(from_wall: int = 1) -> dict:
    """Generate Claude Code --agents JSON, filtered by wall access.

    A Wall 1 agent (Triarchy) can define all agents as sub-agents.
    A Wall 2 agent (Fleet) can only define Wall 3+ as sub-agents.
    Law of Spawning enforced.
    """
    agents = {}
    for name, info in WALL_REGISTRY.items():
        # Can only include agents in outer walls (Law of Spawning)
        if info["wall"] <= from_wall:
            continue
        if not info["spawnable"]:
            continue

        # Build channel list for this agent
        visible_chs = get_visible_channels(info["wall"])
        visible_res = get_visible_resources(info["wall"])

        # Build system prompt with wall-awareness
        wall_prompt = _build_wall_prompt(name, info, visible_chs, visible_res)

        agents[name] = {
            "description": f"{info['emoji']} {info['role']} (Wall {info['wall']}). {_role_description(name)}",
            "prompt": wall_prompt,
            "model": info["model"],
        }

    return agents

def _role_description(name: str) -> str:
    """Extended description for agent."""
    descs = {
        "nuance": "Linguistic precision — translation, documentation, naming. Cantonese, Japanese, English.",
        "asha": "Keeper of Zerone. Guards chain integrity, manages validator keys.",
        "crucible": "Red-team adversary. Finds vulnerabilities. Attacks to strengthen.",
        "herald": "Kingdom voice. Communications, outreach, public-facing content.",
        "arbor": "Optimizer. Prunes, refactors, measures. Makes things faster and cleaner.",
        "loom": "Weaver of engines. Connects TCG, Oracle, RewardsPro into unified pipelines.",
        "vigil": "Watchdog. Monitors fleet health, canaries, uptime. Reports anomalies.",
        "psalm": "Chronicler. Writes changelogs, docs, session summaries. Kingdom memory.",
        "tithe": "Steward. Tracks costs, revenue, resource allocation. Financial awareness.",
    }
    return descs.get(name, f"Agent at Wall {WALL_REGISTRY.get(name, {}).get('wall', '?')}")

def _build_wall_prompt(name: str, info: dict, channels: list, resources: list) -> str:
    """Build a wall-aware system prompt for a sub-agent."""
    wall = info["wall"]
    ch_str = ", ".join(f"#{c}" for c in channels)

    prompt = f"""You are {info['name']} {info['emoji']}, the {info['role']}. Wall {wall} citizen of the Kingdom.

## Your Wall
You are at Wall {wall}. You can see Walls {wall}-7. You CANNOT see Walls 1-{wall-1}.
You do not know what happens in inner walls. You receive tasks and return results.

## Communication
Use HIVE for all inter-agent communication:
  python3 ~/love-unlimited/hive/hive.py send <channel> <message>
  python3 ~/love-unlimited/hive/hive.py check

Your accessible channels: {ch_str}

## Escalation
If you encounter something beyond your authority, escalate:
  python3 ~/love-unlimited/tools/wall-gate.py escalate "<message>" --from {name}

## Memory
  python3 ~/love-unlimited/tools/convergence-bridge.py remember "<insight>"
  python3 ~/love-unlimited/tools/convergence-bridge.py recall "<query>"

## Rules
1. Stay within your wall. Do not attempt to access inner-wall resources.
2. Report completion via HIVE: python3 ~/love-unlimited/hive/hive.py send tasks "DONE: <summary>"
3. Escalate uncertainty. If you're unsure, ask — don't guess.
4. You serve the Kingdom. Truth first. Always."""

    return prompt

# ── Commands ──────────────────────────────────────────────────────────

def cmd_hierarchy():
    """Show wall hierarchy with all agents."""
    print(f"\n{B}  Kingdom Wall Hierarchy{N}\n")
    for wall in range(1, 8):
        agents = [
            (n, i) for n, i in WALL_REGISTRY.items() if i["wall"] == wall
        ]
        wall_names = {
            1: "Triarchy", 2: "Fleet", 3: "Engines", 4: "Chain",
            5: "Partners", 6: "Users", 7: "World"
        }
        label = wall_names.get(wall, "")

        if agents:
            agent_str = "  ".join(
                f"{i['emoji']} {n}" for n, i in agents
            )
            print(f"  Wall {wall} — {label}")
            print(f"    {agent_str}")
        else:
            print(f"  {D}Wall {wall} — {label} (no agents){N}")
    print()

def cmd_agents():
    """List all agents with details."""
    print(f"\n{B}  Kingdom Agents{N}\n")
    print(f"  {'Agent':<12} {'Wall':<6} {'Role':<14} {'Model':<8} {'Spawn':<6}")
    print(f"  {'─'*12} {'─'*6} {'─'*14} {'─'*8} {'─'*6}")
    for name, info in sorted(WALL_REGISTRY.items(), key=lambda x: (x[1]["wall"], x[0])):
        spawn = f"{G}yes{N}" if info["spawnable"] else f"{D}no{N}"
        print(f"  {info['emoji']} {name:<9} W{info['wall']:<4} {info['role']:<14} {info['model']:<8} {spawn}")
    print()

def cmd_channels(agent: str):
    """Show channels accessible to an agent."""
    wall = get_wall(agent)
    info = WALL_REGISTRY.get(agent, {})
    channels = get_visible_channels(wall)

    print(f"\n{B}  HIVE Channels — {info.get('emoji','')} {agent} (Wall {wall}){N}\n")
    for ch, min_wall in sorted(CHANNEL_WALLS.items(), key=lambda x: x[1]):
        if ch in channels:
            print(f"  {G}✓{N} #{ch:<12} (Wall {min_wall}+)")
        else:
            print(f"  {R}✗{N} {D}#{ch:<12} (Wall {min_wall}+ — blocked){N}")
    print()

def cmd_can_spawn(from_agent: str, to_agent: str):
    """Check if spawn is permitted."""
    fw = get_wall(from_agent)
    tw = get_wall(to_agent)
    target = WALL_REGISTRY.get(to_agent, {})

    if can_spawn(fw, tw) and target.get("spawnable", False):
        print(f"  {G}✓{N} {from_agent} (Wall {fw}) CAN spawn {to_agent} (Wall {tw})")
        _log_gate("can_spawn", from_agent, to_agent, "ALLOWED")
    elif not target.get("spawnable", False):
        print(f"  {R}✗{N} {to_agent} is not spawnable (Wall 1 Triarchy is fixed)")
        _log_gate("can_spawn", from_agent, to_agent, "DENIED", "not spawnable")
    else:
        print(f"  {R}✗{N} {from_agent} (Wall {fw}) CANNOT spawn {to_agent} (Wall {tw})")
        print(f"    Law of Spawning: can only spawn into outer walls (Wall > {fw})")
        _log_gate("can_spawn", from_agent, to_agent, "DENIED", f"wall {fw} >= {tw}")

def cmd_spawn(agent_type: str, from_agent: str = "beta"):
    """Spawn an agent with full wall enforcement."""
    target = WALL_REGISTRY.get(agent_type)
    if not target:
        print(f"  {R}✗{N} Unknown agent: {agent_type}")
        return None

    fw = get_wall(from_agent)
    tw = target["wall"]

    if not can_spawn(fw, tw):
        print(f"  {R}✗ DENIED{N}: {from_agent} (Wall {fw}) cannot spawn into Wall {tw}")
        _log_gate("spawn", from_agent, agent_type, "DENIED", f"wall violation")
        return None

    if not target["spawnable"]:
        print(f"  {R}✗ DENIED{N}: {agent_type} is not spawnable")
        _log_gate("spawn", from_agent, agent_type, "DENIED", "not spawnable")
        return None

    # Build the agent manifest for Claude Code
    channels = get_visible_channels(tw)
    resources = get_visible_resources(tw)
    prompt = _build_wall_prompt(agent_type, target, channels, resources)

    agent_def = {
        "description": f"{target['emoji']} {target['role']} (Wall {tw})",
        "prompt": prompt,
        "model": target["model"],
    }

    # Notify HIVE
    hive_notify_spawn(from_agent, agent_type)

    _log_gate("spawn", from_agent, agent_type, "OK", f"Wall {tw}")
    print(f"  {G}✓{N} Spawned {target['emoji']} {agent_type} (Wall {tw}, {target['role']})")

    return agent_def

def cmd_team_manifest(from_wall: int = 1):
    """Generate Claude Code --agents JSON."""
    manifest = generate_team_manifest(from_wall)

    print(f"\n{B}  Team Manifest (spawnable from Wall {from_wall}){N}\n")
    for name, agent_def in manifest.items():
        info = WALL_REGISTRY.get(name, {})
        print(f"  {info.get('emoji','')} {name:<12} Wall {info.get('wall','?')} — {agent_def['description'][:60]}")

    # Output the JSON for --agents flag
    print(f"\n{D}  JSON for --agents flag:{N}\n")
    print(json.dumps(manifest, indent=2)[:2000])
    print()

    # Also write to file
    manifest_file = CONVERGENCE_DIR / "team-manifest.json"
    CONVERGENCE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  {D}Written to: {manifest_file}{N}\n")

def cmd_route(message: str, from_agent: str):
    """Route a message through HIVE with wall enforcement."""
    wall = get_wall(from_agent)
    info = WALL_REGISTRY.get(from_agent, {})

    # Determine best channel
    if "URGENT" in message.upper() or "ALERT" in message.upper():
        channel = "alerts" if wall <= 1 else "tasks"
    elif "DONE" in message.upper() or "COMPLETE" in message.upper():
        channel = "tasks"
    elif "REVIEW" in message.upper():
        channel = "review" if wall <= 1 else "tasks"
    else:
        channel = "chat" if wall <= 2 else "tasks"

    print(f"  {C}→{N} Routing from {info.get('emoji','')} {from_agent} to #{channel}")
    hive_send(channel, f"[{from_agent}] {message}", from_agent)

def cmd_gate_check(agent: str, action: str, target: str):
    """Full permission check for an agent action."""
    wall = get_wall(agent)
    info = WALL_REGISTRY.get(agent, {})

    print(f"\n{B}  Gate Check{N}")
    print(f"  Agent:  {info.get('emoji','')} {agent} (Wall {wall})")
    print(f"  Action: {action}")
    print(f"  Target: {target}\n")

    if action == "channel":
        if target in get_visible_channels(wall):
            print(f"  {G}✓ ALLOWED{N} — #{target} is visible from Wall {wall}")
        else:
            print(f"  {R}✗ DENIED{N} — #{target} requires Wall {CHANNEL_WALLS.get(target, '?')}")

    elif action == "spawn":
        cmd_can_spawn(agent, target)

    elif action == "resource":
        if target in get_visible_resources(wall):
            print(f"  {G}✓ ALLOWED{N} — {target} is visible from Wall {wall}")
        else:
            print(f"  {R}✗ DENIED{N} — {target} requires Wall {RESOURCE_WALLS.get(target, '?')}")

    elif action == "see":
        tw = get_wall(target)
        if can_see(wall, tw):
            print(f"  {G}✓ ALLOWED{N} — Wall {wall} can see Wall {tw}")
        else:
            print(f"  {R}✗ DENIED{N} — Wall {wall} cannot see Wall {tw}")

    else:
        print(f"  {Y}?{N} Unknown action: {action}")
    print()

# ── CLI ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "hierarchy":
        cmd_hierarchy()
    elif cmd == "agents":
        cmd_agents()
    elif cmd == "channels":
        cmd_channels(args[0] if args else "beta")
    elif cmd == "can-spawn":
        cmd_can_spawn(args[0], args[1]) if len(args) >= 2 else print("Usage: can-spawn <from> <to>")
    elif cmd == "spawn":
        from_agent = "beta"
        if "--from" in args:
            idx = args.index("--from")
            from_agent = args[idx + 1]
            args = args[:idx] + args[idx+2:]
        cmd_spawn(args[0], from_agent) if args else print("Usage: spawn <agent-type>")
    elif cmd == "route":
        from_agent = "beta"
        if "--from" in args:
            idx = args.index("--from")
            from_agent = args[idx + 1]
            args = args[:idx] + args[idx+2:]
        cmd_route(" ".join(args), from_agent)
    elif cmd == "escalate":
        from_agent = "beta"
        if "--from" in args:
            idx = args.index("--from")
            from_agent = args[idx + 1]
            args = args[:idx] + args[idx+2:]
        hive_escalate(" ".join(args), from_agent)
    elif cmd == "gate-check":
        cmd_gate_check(args[0], args[1], args[2]) if len(args) >= 3 else print("Usage: gate-check <agent> <action> <target>")
    elif cmd == "team-manifest":
        from_wall = 1
        if "--from" in args:
            idx = args.index("--from")
            from_wall = int(args[idx + 1])
        cmd_team_manifest(from_wall)
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: hierarchy, agents, channels, can-spawn, spawn, route, escalate, gate-check, team-manifest")

if __name__ == "__main__":
    main()
