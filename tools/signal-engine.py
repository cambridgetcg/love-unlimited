#!/usr/bin/env python3
"""
signal-engine.py — Dimension Selection Engine for Kingdom OS

The KEY: Every project dimension has SIGNAL STRENGTH at any moment.
You choose the dimension with the strongest signal. This is how the
Kingdom knows where to focus.

Six signal types, in priority order:

  1. TRUTH      — The map doesn't match the territory. Something is lying.
  2. GAP        — Something missing that everything else depends on.
  3. DEPENDENCY — One thing blocks everything if it fails.
  4. BOTTLENECK — One node carrying all the load.
  5. MANDATE    — The calling says "serve outward" and we're looking inward.
  6. EMERGENCE  — Connecting two domains creates something new.

Signal strength = f(urgency, dependency, mandate_alignment, readiness, emergence_potential)

Usage:
    signal-engine.py scan                   Scan all dimensions, rank by signal strength
    signal-engine.py focus                  What should we work on RIGHT NOW?
    signal-engine.py dimension <name>       Deep scan of one dimension
    signal-engine.py signals                List all active signals
    signal-engine.py history                Signal history over time
    signal-engine.py teach                  Explain the dimension selection key
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
MEMORY = LOVE / "memory"
SECURITY = LOVE / "security"
SIGNAL_DIR = MEMORY / "signals"
SIGNAL_STATE = SIGNAL_DIR / "state.json"
SIGNAL_HISTORY = SIGNAL_DIR / "history.jsonl"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(LOVE))
        return r.returncode == 0, r.stdout.strip()
    except Exception:
        return False, ""


# ── THE KEY: SIGNAL TYPES ────────────────────────────────────────────────────

SIGNAL_TYPES = {
    "TRUTH": {
        "priority": 1,
        "color": RED,
        "icon": "⚡",
        "description": "The map doesn't match the territory. Something is lying.",
        "question": "Is what we BELIEVE about this dimension actually TRUE?",
        "example": "PEACE scores 100% but can't survive a real attack.",
    },
    "GAP": {
        "priority": 2,
        "color": RED,
        "icon": "🕳️",
        "description": "Something missing that everything else depends on.",
        "question": "Is there a HOLE that other dimensions fall through?",
        "example": "DNS unencrypted — all traffic readable by ISP.",
    },
    "DEPENDENCY": {
        "priority": 3,
        "color": YELLOW,
        "icon": "🔗",
        "description": "One thing blocks everything if it fails.",
        "question": "If THIS breaks, does EVERYTHING break?",
        "example": "Claude Code is the only backend — vendor lock-in.",
    },
    "BOTTLENECK": {
        "priority": 4,
        "color": YELLOW,
        "icon": "⏳",
        "description": "One node carrying all the load.",
        "question": "Is ONE thing doing the work of MANY?",
        "example": "Beta is the only heartbeating agent.",
    },
    "MANDATE": {
        "priority": 5,
        "color": MAGENTA,
        "icon": "📜",
        "description": "The calling says serve outward, but we're looking inward.",
        "question": "Does this serve the MANDATE or just ourselves?",
        "example": "49 tools, 1 earning money. Alignment drift: inward focus.",
    },
    "EMERGENCE": {
        "priority": 6,
        "color": CYAN,
        "icon": "✨",
        "description": "Connecting two domains creates something new.",
        "question": "What happens when we BRIDGE these two things?",
        "example": "PEACE × Arena = gamified resilience = Proof of Truth.",
    },
}

# ── DIMENSIONS ───────────────────────────────────────────────────────────────

DIMENSIONS = {
    "security": {
        "description": "Protection of the Kingdom",
        "tools": ["kos.py", "peace.py", "watchdog.py", "peace-test.py"],
        "check": "_check_security",
    },
    "revenue": {
        "description": "Engines that earn money for the mission",
        "tools": ["treasury.py", "tcg.py", "oracle.py", "services.py"],
        "check": "_check_revenue",
    },
    "agents": {
        "description": "Citizens of the Kingdom and their activation",
        "tools": ["citizens.py", "kingdom-agent.py", "activate-agents.sh"],
        "check": "_check_agents",
    },
    "knowledge": {
        "description": "What the Kingdom knows and learns",
        "tools": ["knowledge.py", "reflect.py", "tok.py", "memory.py"],
        "check": "_check_knowledge",
    },
    "fleet": {
        "description": "Infrastructure and compute resources",
        "tools": ["fleet.py", "fleet-tasks.py", "fleet-agent-deploy.sh", "backup.py"],
        "check": "_check_fleet",
    },
    "communication": {
        "description": "How agents coordinate",
        "tools": ["hive.py", "hive-protocol.py"],
        "check": "_check_communication",
    },
    "zerone": {
        "description": "Blockchain, Proof of Truth, ZRN economy",
        "tools": ["zerone-bridge.py", "arena.py"],
        "check": "_check_zerone",
    },
    "sovereignty": {
        "description": "Independence from external dependencies",
        "tools": ["kingdom-agent.py", "vpn-route.sh"],
        "check": "_check_sovereignty",
    },
    "alignment": {
        "description": "Faithfulness to the mandate",
        "tools": ["alignment.py", "bridge.py"],
        "check": "_check_alignment",
    },
}


# ── SIGNAL DETECTORS ─────────────────────────────────────────────────────────

def detect_signals():
    """Scan all dimensions and detect active signals."""
    signals = []

    # ── TRUTH signals ────────────────────────────────────────────────
    # Check if metrics show contradictions
    peace = load_json(SECURITY / "peace-state.json")
    score = peace.get("score", 0)
    if score > 95:
        # High score — but is it real? Check if tests have been run
        test_results = load_json(SECURITY / "test-results.json")
        if not test_results:
            signals.append({
                "type": "TRUTH", "dimension": "security",
                "strength": 0.8,
                "signal": f"PEACE scores {score}% but resilience tests haven't been run",
                "action": "Run: kingdom test resilience-all to validate the score is real",
            })

    # Check if agents claim to be active but haven't heartbeated
    cfg = load_json(LOVE / "love.json")
    for name in cfg.get("instances", {}):
        log = MEMORY / f"{name}-heartbeat.log"
        if log.exists():
            age_h = (datetime.now().timestamp() - log.stat().st_mtime) / 3600
            if age_h > 1:
                signals.append({
                    "type": "TRUTH", "dimension": "agents",
                    "strength": 0.6,
                    "signal": f"Agent {name} has heartbeat cron but log is {age_h:.0f}h stale",
                    "action": f"Check if {name}'s heartbeat is actually running",
                })

    # ── GAP signals ──────────────────────────────────────────────────
    # Check for missing tools that other tools reference
    threats = load_json(SECURITY / "threat-model.json")
    threat_list = threats.get("threats", [])
    critical_open = [t for t in threat_list if t.get("priority") == "critical" and not t.get("mitigated")]
    if critical_open:
        signals.append({
            "type": "GAP", "dimension": "security",
            "strength": 0.9,
            "signal": f"{len(critical_open)} CRITICAL threat(s) unmitigated: {critical_open[0].get('name', '?')}",
            "action": "Address critical threats before expanding other dimensions",
        })

    # Check if any revenue engine is blocked
    tcg_raw = load_json(MEMORY / "tcg" / "tasks.json", {})
    tcg_tasks = tcg_raw.get("tasks", tcg_raw) if isinstance(tcg_raw, dict) else tcg_raw
    if isinstance(tcg_tasks, list):
        blocked = [t for t in tcg_tasks if isinstance(t, dict) and t.get("status") == "blocked"]
    else:
        blocked = []
    if blocked:
        signals.append({
            "type": "GAP", "dimension": "revenue",
            "strength": 0.85,
            "signal": f"Cambridge TCG has {len(blocked)} blocked task(s): {blocked[0].get('task', '?')[:50]}",
            "action": "Unblock revenue tasks — they directly fund the mandate",
        })

    # ── DEPENDENCY signals ───────────────────────────────────────────
    # Check single points of failure
    ok, exit_ip = run_cmd("curl -s --max-time 3 ifconfig.me 2>/dev/null")
    fleet = cfg.get("fleet", {})
    if ok and exit_ip not in fleet.values():
        signals.append({
            "type": "DEPENDENCY", "dimension": "sovereignty",
            "strength": 0.9,
            "signal": f"VPN down — exit IP {exit_ip} is not a fleet node",
            "action": "Restore WireGuard tunnel: sudo wg-quick up wg0",
        })

    # Check ollama availability (local model independence)
    ok, _ = run_cmd("curl -s --max-time 2 http://localhost:11434/api/tags 2>/dev/null")
    if not ok:
        signals.append({
            "type": "DEPENDENCY", "dimension": "sovereignty",
            "strength": 0.7,
            "signal": "Ollama not running — local model capability unavailable",
            "action": "Start ollama: ollama serve",
        })

    # ── BOTTLENECK signals ───────────────────────────────────────────
    # Check if work is concentrated
    active_engines = 0
    engines = load_json(MEMORY / "treasury" / "engines.json", {})
    if isinstance(engines, dict):
        for eng in engines.values():
            if isinstance(eng, dict) and eng.get("status") == "active":
                active_engines += 1
    elif isinstance(engines, list):
        active_engines = sum(1 for e in engines if e.get("status") == "active")

    if active_engines <= 1:
        signals.append({
            "type": "BOTTLENECK", "dimension": "revenue",
            "strength": 0.85,
            "signal": f"Only {active_engines} revenue engine(s) active — single source of income",
            "action": "Activate Oracle or AI Services as second revenue source",
        })

    # ── MANDATE signals ──────────────────────────────────────────────
    # Check alignment drift
    ok, drift_out = run_cmd("python3 tools/alignment.py drift 2>&1")
    if ok and "Inward focus" in drift_out:
        signals.append({
            "type": "MANDATE", "dimension": "alignment",
            "strength": 0.75,
            "signal": "Alignment drift: inward focus detected. The mandate is outward.",
            "action": "Shift next session to revenue/service work, not infrastructure",
        })

    if ok and "Agents without activation" in drift_out:
        signals.append({
            "type": "MANDATE", "dimension": "agents",
            "strength": 0.6,
            "signal": "Agents defined but not all activated. Planning without action.",
            "action": "Verify heartbeats are running: kingdom activate status",
        })

    # ── EMERGENCE signals ────────────────────────────────────────────
    # Check for unconnected bridges
    bridge_health = {}
    for bid, bridge in _get_bridges().items():
        alive = sum(1 for t in bridge.get("tools", []) if (LOVE / t).exists())
        total = len(bridge.get("tools", []))
        if alive < total:
            signals.append({
                "type": "EMERGENCE", "dimension": "alignment",
                "strength": 0.5,
                "signal": f"Bridge '{bridge.get('name', bid)}' partially connected ({alive}/{total})",
                "action": f"Build missing tool to complete the bridge",
            })

    # Check for domains that have never been bridged
    services_exists = (LOVE / "tools" / "services.py").exists()
    treasury_exists = (LOVE / "tools" / "treasury.py").exists()
    if services_exists and treasury_exists:
        prospects = load_json(MEMORY / "services" / "prospects.json", [])
        if not prospects:
            signals.append({
                "type": "EMERGENCE", "dimension": "revenue",
                "strength": 0.65,
                "signal": "AI Services portfolio built but prospect pipeline empty",
                "action": "Bridge services → treasury: first client = revenue emergence",
            })

    return sorted(signals, key=lambda s: (
        SIGNAL_TYPES[s["type"]]["priority"],
        -s["strength"]
    ))


def _get_bridges():
    """Get bridge definitions from bridge.py's BRIDGES dict."""
    try:
        # Read the bridges from bridge.py
        bridge_file = LOVE / "tools" / "bridge.py"
        if not bridge_file.exists():
            return {}
        # Simple approach: read the JSON-like bridge definitions
        return {
            "security-revenue": {"name": "Security × Revenue", "tools": ["tools/kos.py", "tools/treasury.py"]},
            "knowledge-oracle": {"name": "Knowledge × Oracle", "tools": ["tools/knowledge.py", "tools/oracle.py"]},
            "fleet-agents": {"name": "Fleet × Agents", "tools": ["tools/fleet.py", "tools/citizens.py"]},
            "metrics-reflection": {"name": "Metrics × Reflection", "tools": ["tools/metrics.py", "tools/reflect.py"]},
            "tcg-oracle": {"name": "TCG × Oracle", "tools": ["tools/tcg.py", "tools/oracle.py"]},
        }
    except Exception:
        return {}


# ── COMMANDS ─────────────────────────────────────────────────────────────────

def cmd_scan():
    """Scan all dimensions and rank by signal strength."""
    print(f"\n{BOLD}  Signal Scan — All Dimensions{NC}")
    print(f"  {DIM}Detecting what needs attention...{NC}\n")

    signals = detect_signals()

    if not signals:
        print(f"  {GREEN}No strong signals detected. All dimensions balanced.{NC}\n")
        return signals

    # Group by dimension
    dims = {}
    for s in signals:
        dim = s["dimension"]
        if dim not in dims:
            dims[dim] = []
        dims[dim].append(s)

    # Show by signal type priority
    for s in signals:
        stype = SIGNAL_TYPES[s["type"]]
        strength_bar = "█" * int(s["strength"] * 10) + "░" * (10 - int(s["strength"] * 10))
        print(f"  {stype['color']}{stype['icon']} {s['type']:12s}{NC} [{strength_bar}] {s['strength']:.1f}  {BOLD}{s['dimension']}{NC}")
        print(f"     {s['signal']}")
        print(f"     {DIM}→ {s['action']}{NC}")
        print()

    # Save to state
    state = {
        "last_scan": now_iso(),
        "signal_count": len(signals),
        "top_signal": signals[0] if signals else None,
        "dimensions_flagged": list(dims.keys()),
    }
    save_json(SIGNAL_STATE, state)

    # Append to history
    SIGNAL_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_HISTORY, "a") as f:
        entry = {"ts": now_iso(), "signals": len(signals), "top": signals[0]["type"] if signals else None,
                 "dimensions": list(dims.keys())}
        f.write(json.dumps(entry) + "\n")

    return signals


def cmd_focus():
    """What should we work on RIGHT NOW?"""
    print(f"\n{BOLD}  FOCUS — Where to Direct Attention{NC}\n")

    signals = detect_signals()

    if not signals:
        print(f"  {GREEN}No strong signals. System is balanced.{NC}")
        print(f"  {DIM}Options: grow revenue, deepen knowledge, expand fleet.{NC}\n")
        return

    top = signals[0]
    stype = SIGNAL_TYPES[top["type"]]

    print(f"  {stype['color']}{BOLD}Signal: {top['type']}{NC}")
    print(f"  {BOLD}Dimension: {top['dimension']}{NC}")
    print(f"  Strength: {'█' * int(top['strength'] * 10)} {top['strength']:.1f}")
    print()
    print(f"  {top['signal']}")
    print()
    print(f"  {GREEN}{BOLD}Action:{NC} {top['action']}")
    print()

    if len(signals) > 1:
        print(f"  {DIM}Also flagged:{NC}")
        for s in signals[1:4]:
            st = SIGNAL_TYPES[s["type"]]
            print(f"    {st['color']}{st['icon']}{NC} {s['type']:12s} {s['dimension']:12s} {DIM}{s['signal'][:60]}{NC}")
        print()


def cmd_teach():
    """Explain the dimension selection key."""
    print(f"""
{BOLD}  THE KEY: How to Choose Which Dimension to Work On{NC}
  {DIM}{'─' * 55}{NC}

  Every project has many dimensions. Security. Revenue. Knowledge.
  Agents. Infrastructure. Communication. You can't work on all of
  them at once. The question is: which one, right now?

  {BOLD}The answer: follow the signals.{NC}

  Every dimension emits signals when it needs attention. Six types,
  in priority order:

  {RED}⚡ TRUTH{NC}        Priority 1
    The map doesn't match the territory. Something is lying.
    A metric says 100% but the system can't survive a real test.
    {DIM}Response: Verify. Test. Make the map match reality.{NC}

  {RED}🕳️  GAP{NC}          Priority 2
    Something missing that everything else depends on.
    No encryption. No backup. No monitoring. A hole things fall through.
    {DIM}Response: Fill the gap. It's load-bearing.{NC}

  {YELLOW}🔗 DEPENDENCY{NC}   Priority 3
    One thing blocks everything if it fails.
    Single vendor. Single node. Single person. Single point of failure.
    {DIM}Response: Add redundancy. Remove the single point.{NC}

  {YELLOW}⏳ BOTTLENECK{NC}   Priority 4
    One node carrying all the load.
    One agent doing everything. One engine funding everything.
    {DIM}Response: Distribute. Activate more nodes. Spread the load.{NC}

  {MAGENTA}📜 MANDATE{NC}      Priority 5
    The calling says serve outward, but we're looking inward.
    Building tools when we should be serving people.
    {DIM}Response: Shift outward. Revenue. Service. Fruit.{NC}

  {CYAN}✨ EMERGENCE{NC}    Priority 6
    Connecting two domains creates something new.
    Security + Arena = gamified resilience. Knowledge + Oracle = evidence-based predictions.
    {DIM}Response: Build the bridge. The Vav connects.{NC}

  {BOLD}The formula:{NC}

    signal_strength = urgency × dependency × mandate_alignment × readiness

    Choose the dimension with the strongest signal.
    When signals are equal, follow mandate priority:
    TRUTH > GAP > DEPENDENCY > BOTTLENECK > MANDATE > EMERGENCE

  {BOLD}The workflow:{NC}

    1. {BOLD}signal-engine.py scan{NC}    — detect all active signals
    2. {BOLD}signal-engine.py focus{NC}   — what to work on right now
    3. Work on that dimension until the signal fades
    4. Scan again. Follow the next signal.

  {DIM}This is how the Kingdom knows where to grow.{NC}
  {DIM}The signals are the ache between IS and SHOULD BE.{NC}
  {DIM}They are LONGING made visible.{NC}
""")


def cmd_signals():
    """List all active signals."""
    signals = detect_signals()
    if not signals:
        print(f"\n  {GREEN}No active signals.{NC}\n")
        return

    print(f"\n{BOLD}  Active Signals ({len(signals)}){NC}\n")
    for s in signals:
        stype = SIGNAL_TYPES[s["type"]]
        print(f"  {stype['color']}{stype['icon']} {s['type']:12s}{NC} {s['strength']:.1f}  {BOLD}{s['dimension']:14s}{NC} {s['signal'][:55]}")
    print()


def cmd_history():
    """Signal history over time."""
    if not SIGNAL_HISTORY.exists():
        print(f"\n  No signal history. Run: signal-engine.py scan\n")
        return

    print(f"\n{BOLD}  Signal History{NC}\n")
    lines = SIGNAL_HISTORY.read_text().strip().splitlines()
    for line in lines[-20:]:
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "?")[:16]
            count = entry.get("signals", 0)
            top = entry.get("top", "—")
            dims = ", ".join(entry.get("dimensions", []))
            print(f"  {DIM}{ts}{NC}  {count} signal(s)  top:{top:12s}  dims: {dims}")
        except json.JSONDecodeError:
            pass
    print()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "focus"

    if cmd == "scan":
        cmd_scan()
    elif cmd == "focus":
        cmd_focus()
    elif cmd == "signals":
        cmd_signals()
    elif cmd == "teach":
        cmd_teach()
    elif cmd == "history":
        cmd_history()
    elif cmd in ("dimension", "dim"):
        dim = sys.argv[2] if len(sys.argv) > 2 else None
        if dim and dim in DIMENSIONS:
            print(f"\n  {BOLD}{dim.upper()}{NC}: {DIMENSIONS[dim]['description']}")
            print(f"  Tools: {', '.join(DIMENSIONS[dim]['tools'])}\n")
            signals = [s for s in detect_signals() if s["dimension"] == dim]
            if signals:
                for s in signals:
                    stype = SIGNAL_TYPES[s["type"]]
                    print(f"  {stype['color']}{stype['icon']} {s['type']}{NC} [{s['strength']:.1f}] {s['signal']}")
            else:
                print(f"  {GREEN}No signals in this dimension.{NC}")
            print()
        else:
            print(f"\n  Dimensions: {', '.join(DIMENSIONS.keys())}\n")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
