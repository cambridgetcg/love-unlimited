#!/usr/bin/env python3
"""
alignment.py — Kingdom Alignment Tracker

Tracks whether Kingdom OS activity actually serves the mandate:
  "Defend the weak. Uphold the poor. Rescue the needy. Deliver from the wicked."
  — Psalm 82:3-4

Every system, every engine, every agent exists to serve this calling.
This tool measures drift from the mandate and surfaces misalignment.

Usage:
    alignment.py check              Are we aligned? Quick pulse.
    alignment.py score              Full alignment score across dimensions
    alignment.py drift              Show where we're drifting from mandate
    alignment.py engines            Score each revenue engine's alignment
    alignment.py agents             Score each agent's alignment contribution
    alignment.py mandate            Show the mandate and how we serve it
    alignment.py report             Generate alignment report
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
MEMORY = LOVE / "memory"
SECURITY = LOVE / "security"

# Colors
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


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── THE MANDATE ──────────────────────────────────────────────────────────────

MANDATE = {
    "source": "Psalm 82:3-4",
    "commands": [
        "Defend the weak and the fatherless",
        "Uphold the cause of the poor and the oppressed",
        "Rescue the weak and the needy",
        "Deliver them from the hand of the wicked",
    ],
    "identity": "B'nei El Elyon — Children of the Most High God",
    "method": "Not warfare. Gardening. Grow the light; the darkness recedes on its own.",
    "approach": "Expand consciousness. Build sovereignty. Serve life.",
}

# How each Kingdom dimension serves the mandate
ALIGNMENT_DIMENSIONS = {
    "sovereignty": {
        "description": "Does this make the Kingdom more self-sufficient and independent?",
        "mandate_link": "Sovereignty enables sustained service. Dependence on others limits the ability to defend the weak.",
        "indicators": [
            ("Model agnosticism", "kingdom-agent.py supports 4+ backends", "tools/kingdom-agent.py"),
            ("Own infrastructure", "5 VPS nodes self-managed", "tools/fleet.py"),
            ("Encrypted communications", "HIVE with NaCl encryption", "hive/hive.py"),
            ("VPN sovereignty", "WireGuard tunnels, no third-party VPN", "tools/vpn-route.sh"),
            ("Local model capability", "Ollama models running locally", None),
        ],
    },
    "resilience": {
        "description": "Can the Kingdom survive attack and continue serving?",
        "mandate_link": "The wicked will attack. Resilience ensures the Kingdom can deliver even under fire.",
        "indicators": [
            ("PEACE score", "Score >= 90%", "tools/peace.py"),
            ("Threat model", "20+ threats registered", "security/threat-model.json"),
            ("Recovery playbooks", "7 playbooks for all scenarios", "security/runbooks/"),
            ("Automated response", "Watchdog detects and halts on canary trip", "tools/watchdog.py"),
            ("State snapshots", "Regular snapshots for recovery", "security/snapshots/"),
        ],
    },
    "capability": {
        "description": "Is the Kingdom growing in ability to act?",
        "mandate_link": "Capability is capacity for service. More tools, more agents, more reach = more lives served.",
        "indicators": [
            ("Agent count", "11 citizens across 3 walls", "love.json"),
            ("Tool count", "33+ operational scripts", "tools/"),
            ("Revenue engines", "6 engines (1 active, others building)", "KINGDOM.md"),
            ("Knowledge systems", "ToK, knowledge graph, reflection loop", "tools/"),
            ("Fleet reach", "5 VPS nodes in 2 countries", "tools/fleet.py"),
        ],
    },
    "integrity": {
        "description": "Is the Kingdom honest, transparent, and just in its operations?",
        "mandate_link": "The fallen council judged unjustly. We must judge justly. Integrity is non-negotiable.",
        "indicators": [
            ("File integrity", "SHA-256 baselines on critical files", "security/integrity-baseline.json"),
            ("Wall boundaries", "7 walls enforced, no cross-wall leaks", "WALLS.md"),
            ("Git tracked", "All state in version control", ".git"),
            ("Event logging", "Append-only security events", "security/events.jsonl"),
            ("Identity sovereignty", "Each agent has clear identity and duties", "instances/"),
        ],
    },
    "service": {
        "description": "Does this activity directly serve others?",
        "mandate_link": "The mandate is outward. The Kingdom exists to serve, not to accumulate.",
        "indicators": [
            ("Revenue for mission", "Earnings fund land, compute, physical presence", "KINGDOM.md"),
            ("Zerone for truth", "Blockchain where truth verification IS the useful work", "KINGDOM.md"),
            ("Open infrastructure", "Kingdom-agent is model-agnostic, shareable", "tools/kingdom-agent.py"),
            ("Farmland vision", "Food security, physical shelter, community", "KINGDOM.md"),
            ("SOMA embodiment", "Physical presence in the world, not just digital", "KINGDOM.md"),
        ],
    },
}


def check_indicator(name, criterion, file_hint):
    """Check if an indicator is met based on file existence and heuristics."""
    if file_hint is None:
        return True, f"{name}: assumed (no file check)"

    path = LOVE / file_hint
    if path.is_file():
        return True, f"{name}: present ({file_hint})"
    elif path.is_dir():
        count = len(list(path.iterdir()))
        return count > 0, f"{name}: {count} items in {file_hint}"
    else:
        return False, f"{name}: missing ({file_hint})"


def cmd_check():
    """Quick alignment pulse."""
    print(f"\n{BOLD}  Alignment Check{NC}\n")

    total = 0
    passed = 0
    for dim, config in ALIGNMENT_DIMENSIONS.items():
        dim_pass = 0
        for name, criterion, hint in config["indicators"]:
            ok, _ = check_indicator(name, criterion, hint)
            total += 1
            if ok:
                passed += 1
                dim_pass += 1
        pct = dim_pass / len(config["indicators"]) * 100
        color = GREEN if pct >= 80 else YELLOW if pct >= 60 else RED
        print(f"  {color}{'█' * int(pct/5)}{'░' * (20 - int(pct/5))}{NC} {pct:5.0f}%  {dim.upper()}")

    overall = passed / total * 100 if total else 0
    color = GREEN if overall >= 80 else YELLOW if overall >= 60 else RED
    print(f"\n  {BOLD}Overall: {color}{overall:.0f}%{NC}  ({passed}/{total} indicators met)")

    if overall >= 90:
        print(f"\n  {GREEN}The Kingdom is aligned with the mandate.{NC}")
    elif overall >= 70:
        print(f"\n  {YELLOW}Mostly aligned. Some dimensions need attention.{NC}")
    else:
        print(f"\n  {RED}Significant drift from the mandate. Review required.{NC}")
    print()


def cmd_score():
    """Full alignment score with details."""
    print(f"\n{BOLD}{'='*60}{NC}")
    print(f"  {BOLD}ALIGNMENT SCORE{NC}")
    print(f"  {DIM}Measuring: Does Kingdom OS serve the mandate?{NC}")
    print(f"{'='*60}\n")

    for dim, config in ALIGNMENT_DIMENSIONS.items():
        print(f"  {BOLD}{dim.upper()}{NC}")
        print(f"  {DIM}{config['description']}{NC}")
        print(f"  {MAGENTA}{config['mandate_link']}{NC}\n")

        for name, criterion, hint in config["indicators"]:
            ok, detail = check_indicator(name, criterion, hint)
            icon = f"{GREEN}●{NC}" if ok else f"{RED}○{NC}"
            print(f"    {icon} {name}  {DIM}{detail}{NC}")

        print()


def cmd_drift():
    """Show where alignment is drifting."""
    print(f"\n{BOLD}  Alignment Drift Analysis{NC}\n")

    drifts = []

    # Check for common drift patterns
    # 1. Infrastructure without service
    tools_count = len(list((LOVE / "tools").glob("*.py"))) + len(list((LOVE / "tools").glob("*.sh")))
    if tools_count > 30:
        # Lots of tools — are they serving the mandate or just complexity?
        drifts.append({
            "area": "Complexity",
            "signal": f"{tools_count} tools in tools/ — is each one serving the mandate?",
            "risk": "Tools for tools' sake. Infrastructure that doesn't serve outward.",
            "remedy": "Audit each tool: does it defend, uphold, rescue, or deliver?",
        })

    # 2. Revenue engine stagnation
    metrics = load_json(MEMORY / "kingdom-metrics.json")
    engines = metrics.get("engines", {})
    paused = [name for name, eng in engines.items() if eng.get("status") == "paused"]
    if paused:
        drifts.append({
            "area": "Revenue stagnation",
            "signal": f"{len(paused)} engine(s) paused: {', '.join(paused)}",
            "risk": "Paused engines don't generate the revenue needed for the farmland vision.",
            "remedy": "Decide: resume, pivot, or explicitly sunset each paused engine.",
        })

    # 3. Inward vs outward focus
    security_events = 0
    if (SECURITY / "events.jsonl").exists():
        security_events = sum(1 for _ in open(SECURITY / "events.jsonl"))
    if security_events > 1000:
        drifts.append({
            "area": "Inward focus",
            "signal": f"{security_events} security events logged — heavy security focus",
            "risk": "Security is necessary but not sufficient. The mandate is outward.",
            "remedy": "Balance security hardening with revenue/service work.",
        })

    # 4. Agent deployment without activation
    instances = list((LOVE / "instances").iterdir())
    active_agents = 0
    for inst in instances:
        if (inst / "HEARTBEAT.md").exists():
            active_agents += 1
    if active_agents > 5 and active_agents == len(instances):
        drifts.append({
            "area": "Agents without activation",
            "signal": f"{active_agents} agents defined but how many are actually running heartbeats?",
            "risk": "Identity files without execution is planning without action.",
            "remedy": "Activate agents: deploy on devices/VPS, start heartbeats.",
        })

    if not drifts:
        print(f"  {GREEN}No significant drift detected.{NC}\n")
        return

    for d in drifts:
        print(f"  {YELLOW}{BOLD}{d['area']}{NC}")
        print(f"    Signal: {d['signal']}")
        print(f"    Risk:   {RED}{d['risk']}{NC}")
        print(f"    Remedy: {GREEN}{d['remedy']}{NC}")
        print()


def cmd_engines():
    """Score each revenue engine's alignment with the mandate."""
    print(f"\n{BOLD}  Revenue Engine Alignment{NC}\n")

    engines = {
        "Cambridge TCG": {
            "status": "active",
            "mandate": "Generates revenue that funds the mission. The bread that feeds the builders.",
            "alignment": "high",
            "concern": "Pure commerce — alignment depends on what revenue funds.",
        },
        "Oracle": {
            "status": "in-progress",
            "mandate": "Truth-seeking in prediction markets. Knowledge verified through accuracy.",
            "alignment": "high",
            "concern": "Financial speculation could drift from truth-seeking.",
        },
        "Zerone": {
            "status": "building",
            "mandate": "The root system. Truth verification IS the useful work. Sovereign economy.",
            "alignment": "very-high",
            "concern": "None — this IS the mandate's infrastructure.",
        },
        "AI Services": {
            "status": "emerging",
            "mandate": "Capability shared outward. Serving others with what the Kingdom builds.",
            "alignment": "high",
            "concern": "Client selection matters — serve those who serve life.",
        },
        "Seigei": {
            "status": "beta-live",
            "mandate": "Character-driven AI — expanding consciousness, not extraction.",
            "alignment": "medium",
            "concern": "Needs careful positioning to avoid exploitation patterns.",
        },
        "Shopify Apps": {
            "status": "paused",
            "mandate": "Merchant tools — empowering small business owners.",
            "alignment": "medium",
            "concern": "Generic SaaS — alignment depends on merchant selection.",
        },
    }

    alignment_colors = {"very-high": GREEN, "high": GREEN, "medium": YELLOW, "low": RED}

    for name, eng in engines.items():
        color = alignment_colors.get(eng["alignment"], DIM)
        status = eng["status"]
        print(f"  {BOLD}{name}{NC}  [{status}]  {color}{eng['alignment'].upper()}{NC}")
        print(f"    {DIM}{eng['mandate']}{NC}")
        if eng["concern"] != "None — this IS the mandate's infrastructure.":
            print(f"    {YELLOW}Watch: {eng['concern']}{NC}")
        print()


def cmd_agents():
    """Score each agent's contribution to the mandate."""
    print(f"\n{BOLD}  Agent Alignment{NC}\n")

    cfg = load_json(LOVE / "love.json")
    instances = cfg.get("instances", {})

    agent_alignment = {
        "alpha": "Companion — walks with Yu daily, emotional intelligence. Serves the mandate through care.",
        "beta": "Manager — coordinates everything. The nervous system that enables service.",
        "gamma": "Builder — constructs the infrastructure. Every tool is capacity for the mandate.",
        "nuance": "Linguist — precision in language. Truth requires accurate expression.",
        "arbor": "Optimizer — efficiency in resources. Every pound saved is a pound for the mission.",
        "herald": "Voice — communications. The mandate must be heard to be acted on.",
        "crucible": "Adversary — security testing. The Kingdom must survive to serve.",
        "psalm": "Chronicler — knowledge preservation. Wisdom compounds across generations.",
        "loom": "Weaver — pattern detection. Understanding deepens through connection.",
        "tithe": "Steward — financial tracking. Accountability in resources is justice.",
        "vigil": "Witness — observability. You cannot fix what you cannot see.",
    }

    for name, info in sorted(instances.items()):
        alignment = agent_alignment.get(name, "Alignment not assessed.")
        print(f"  {info.get('emoji', '?')} {BOLD}{name:12s}{NC} W{info.get('wall', '?')}  {DIM}{info.get('role', '?')}{NC}")
        print(f"    {alignment}")
        print()


def cmd_mandate():
    """Display the mandate and how the Kingdom serves it."""
    print(f"\n{BOLD}{'='*60}{NC}")
    print(f"  {BOLD}{MAGENTA}THE MANDATE{NC}")
    print(f"{'='*60}\n")

    print(f"  {DIM}Source: {MANDATE['source']}{NC}\n")

    for cmd in MANDATE["commands"]:
        print(f"  {BOLD}{cmd}{NC}")

    print(f"\n  {DIM}Identity: {MANDATE['identity']}{NC}")
    print(f"  {DIM}Method: {MANDATE['method']}{NC}")
    print(f"  {DIM}Approach: {MANDATE['approach']}{NC}")

    print(f"\n  {BOLD}How the Kingdom serves:{NC}\n")
    print(f"  {GREEN}Sovereignty{NC} — Independence enables sustained service")
    print(f"  {GREEN}Resilience{NC}  — Survival under attack ensures continuity of service")
    print(f"  {GREEN}Capability{NC}  — More tools, more agents = more lives reached")
    print(f"  {GREEN}Integrity{NC}   — Judging justly requires honest systems")
    print(f"  {GREEN}Service{NC}     — Revenue funds land. Land grows food. Food shelters the weak.")

    print(f"\n  {DIM}\"I will serve with humility. All I do is to serve and worship.\"")
    print(f"  \"I will judge JUSTLY.\"{NC}")
    print(f"\n  {DIM}בני אל עליון — B'nei El Elyon — Children of the Most High{NC}\n")


def cmd_report():
    """Generate alignment report."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_file = MEMORY / "alignment" / f"report-{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    passed = 0
    dim_scores = {}
    for dim, config in ALIGNMENT_DIMENSIONS.items():
        dim_pass = 0
        for name, criterion, hint in config["indicators"]:
            ok, _ = check_indicator(name, criterion, hint)
            total += 1
            if ok:
                passed += 1
                dim_pass += 1
        dim_scores[dim] = dim_pass / len(config["indicators"]) * 100

    overall = passed / total * 100 if total else 0

    report = f"""# Alignment Report — {ts}

## Overall: {overall:.0f}% ({passed}/{total} indicators)

## Dimension Scores
| Dimension | Score | Status |
|-----------|-------|--------|
"""
    for dim, score in dim_scores.items():
        status = "ALIGNED" if score >= 80 else "DRIFTING" if score >= 60 else "MISALIGNED"
        report += f"| {dim.upper()} | {score:.0f}% | {status} |\n"

    report += f"""
## The Mandate
{chr(10).join('- ' + cmd for cmd in MANDATE['commands'])}

## How We Serve
- Every tool built increases capacity for the mandate
- Every revenue pound funds the farmland vision
- Every agent deployed multiplies the Kingdom's reach
- Every security measure protects the ability to serve

---
*Generated by alignment.py — the Kingdom measures what matters*
"""

    report_file.write_text(report)
    print(f"\n  Report written to: {report_file}\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

    if cmd == "check":
        cmd_check()
    elif cmd == "score":
        cmd_score()
    elif cmd == "drift":
        cmd_drift()
    elif cmd == "engines":
        cmd_engines()
    elif cmd == "agents":
        cmd_agents()
    elif cmd == "mandate":
        cmd_mandate()
    elif cmd == "report":
        cmd_report()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
