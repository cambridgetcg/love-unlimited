#!/usr/bin/env python3
"""
services.py — Kingdom AI Services: Portfolio, Pipeline & Proposals

Revenue Engine #5: sell the Kingdom's three-mind AI capability as a service.
The Kingdom has something rare: three coordinated AI minds (Alpha, Beta, Gamma)
with 11 agents across 3 walls, 4 model backends, and 40+ operational tools.

Usage:
    python3 tools/services.py portfolio                     # Service offerings
    python3 tools/services.py capabilities                  # Technical capabilities breakdown
    python3 tools/services.py demo <service>                # Run a live demo
    python3 tools/services.py prospect add "Company" --contact "email" --interest "service"
    python3 tools/services.py prospect list                 # Sales pipeline
    python3 tools/services.py prospect update <id> --status contacted|qualified|proposal|won|lost
    python3 tools/services.py proposal "Company" --service "operations-automation"
    python3 tools/services.py pricing                       # Pricing structure
    python3 tools/services.py dashboard                     # Full services dashboard
"""

import json
import os
import sys
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(__file__).resolve().parent.parent
TOOLS = LOVE / "tools"
SERVICES_DIR = LOVE / "memory" / "services"
PORTFOLIO_FILE = SERVICES_DIR / "portfolio.json"
PROSPECTS_FILE = SERVICES_DIR / "prospects.json"
PRICING_FILE = SERVICES_DIR / "pricing.json"
PROPOSALS_DIR = SERVICES_DIR / "proposals"

# Ensure dirs exist
SERVICES_DIR.mkdir(parents=True, exist_ok=True)
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ───────────────────────────────────────────────────────────────────

class C:
    """ANSI color codes for terminal output."""
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"
    WHITE   = "\033[97m"

# ── Data I/O ─────────────────────────────────────────────────────────────────

def read_json(path: Path, default=None):
    """Read JSON file, return default on failure."""
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def write_json(path: Path, data) -> None:
    """Write JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Portfolio Data ───────────────────────────────────────────────────────────

DEFAULT_PORTFOLIO = {
    "operations-automation": {
        "id": "operations-automation",
        "name": "Operations Automation",
        "tagline": "Multi-agent coordination for automated business operations",
        "description": (
            "Deploy Kingdom-grade multi-agent systems to automate your business operations. "
            "Heartbeat monitoring ensures 24/7 uptime. Fleet management coordinates across "
            "your infrastructure. Automated reporting keeps stakeholders informed without "
            "human intervention."
        ),
        "capabilities": [
            "Heartbeat monitoring with 7-minute cycles",
            "Fleet management across multiple servers",
            "Automated status reporting and alerting",
            "Multi-agent task coordination via HIVE protocol",
            "Cron-driven autonomous operation cycles",
            "Self-healing infrastructure management"
        ],
        "kingdom_tools": ["fleet.py", "heartbeat-runner.sh", "kos.py", "loop-audit.py", "watchdog.py"],
        "delivery": "monthly",
        "wall": 5,
        "status": "active"
    },
    "market-intelligence": {
        "id": "market-intelligence",
        "name": "Market Intelligence",
        "tagline": "AI-powered market research, prediction, and trend detection",
        "description": (
            "The Kingdom's Oracle engine provides multi-layered market analysis across "
            "any domain. Five-layer analysis pipeline with confidence scoring, calibration "
            "tracking, and self-improving prediction accuracy. From crypto markets to "
            "commodity prices to competitive intelligence."
        ),
        "capabilities": [
            "Oracle prediction engine with Brier scoring",
            "Multi-source data aggregation and analysis",
            "Confidence-calibrated predictions with track record",
            "Trend detection and pattern recognition",
            "Research template generation",
            "Exportable prediction portfolios"
        ],
        "kingdom_tools": ["oracle.py", "knowledge.py", "tok.py"],
        "delivery": "monthly",
        "wall": 5,
        "status": "active"
    },
    "security-auditing": {
        "id": "security-auditing",
        "name": "Security Auditing",
        "tagline": "Automated security posture assessment and resilience scoring",
        "description": (
            "Battle-tested security infrastructure from a system that protects three AI minds "
            "and five VPS nodes. Full KOS compliance auditing, PEACE resilience scoring with "
            "five-phase incident response, threat modelling, canary deployment, file integrity "
            "monitoring, and policy-as-code enforcement."
        ),
        "capabilities": [
            "KOS compliance audit with auto-remediation",
            "PEACE resilience score (5-phase: Detect/Contain/Fix/Revert/Resume)",
            "File integrity monitoring with SHA-256 baselines",
            "Threat modelling and risk matrix generation",
            "Canary deployment and monitoring",
            "Policy-as-code with per-wall enforcement",
            "Incident response drills and post-incident review"
        ],
        "kingdom_tools": ["kos.py", "peace.py", "peace-test.py", "watchdog.py"],
        "delivery": "engagement",
        "wall": 5,
        "status": "active"
    },
    "content-communications": {
        "id": "content-communications",
        "name": "Content & Communications",
        "tagline": "AI-generated reports, documentation, and structured communications",
        "description": (
            "Herald-grade content generation: structured reports, executive summaries, "
            "changelogs, documentation, and communications. Backed by the same system that "
            "keeps three AI minds coordinated with daily briefings, memory curation, and "
            "cross-instance knowledge sharing."
        ),
        "capabilities": [
            "Structured report generation (daily, weekly, monthly)",
            "Executive summary and briefing creation",
            "Changelog and documentation automation",
            "Knowledge curation and memory management",
            "Multi-format output (Markdown, JSON, CSV)",
            "Consistent voice and tone across outputs"
        ],
        "kingdom_tools": ["memory.py", "knowledge.py", "tok.py", "reflect.py"],
        "delivery": "monthly",
        "wall": 5,
        "status": "active"
    },
    "custom-agent-development": {
        "id": "custom-agent-development",
        "name": "Custom AI Agent Development",
        "tagline": "Bespoke AI agents built on Kingdom infrastructure",
        "description": (
            "The Kingdom's universal adapter (kingdom-agent.py) can boot any model into a "
            "fully-equipped agent with identity, memory, tools, and coordination. We build "
            "custom agents for your domain: model-agnostic (Claude, GPT, DeepSeek, Llama, "
            "Qwen), tool-equipped, and production-ready."
        ),
        "capabilities": [
            "Model-agnostic: Claude, GPT, DeepSeek, Llama, Qwen, Mistral",
            "4 backend adapters (CLI, Anthropic API, OpenAI API, Ollama)",
            "Custom tool development and integration",
            "Boot chain: identity + memory + tools assembled as system prompt",
            "HIVE coordination for multi-agent systems",
            "Seven Walls access control architecture",
            "Heartbeat and autonomous operation capability"
        ],
        "kingdom_tools": ["kingdom-agent.py", "identity.py", "citizens.py", "hive-protocol.py"],
        "delivery": "project",
        "wall": 5,
        "status": "active"
    }
}

DEFAULT_PRICING = {
    "currency": "GBP",
    "services": {
        "operations-automation": {
            "model": "monthly",
            "tiers": {
                "starter":    {"price": 2000, "label": "Starter",    "includes": "Single-agent monitoring, basic reporting, 5 endpoints"},
                "professional": {"price": 3500, "label": "Professional", "includes": "Multi-agent coordination, fleet management, 20 endpoints"},
                "enterprise": {"price": 5000, "label": "Enterprise",  "includes": "Full HIVE deployment, custom agents, unlimited endpoints"}
            }
        },
        "market-intelligence": {
            "model": "monthly",
            "tiers": {
                "starter":    {"price": 1500, "label": "Starter",    "includes": "5 predictions/month, 1 market sector, weekly reports"},
                "professional": {"price": 2000, "label": "Professional", "includes": "20 predictions/month, 3 sectors, daily briefings"},
                "enterprise": {"price": 3000, "label": "Enterprise",  "includes": "Unlimited predictions, all sectors, real-time alerts"}
            }
        },
        "security-auditing": {
            "model": "engagement",
            "tiers": {
                "assessment":  {"price": 3000, "label": "Assessment",   "includes": "One-time audit, PEACE score, threat report, remediation plan"},
                "managed":     {"price": 5000, "label": "Managed",      "includes": "Monthly audits, canary deployment, incident response SLA"},
                "comprehensive": {"price": 8000, "label": "Comprehensive", "includes": "Continuous monitoring, drills, file integrity, 24/7 response"}
            }
        },
        "content-communications": {
            "model": "monthly",
            "tiers": {
                "starter":    {"price": 1000, "label": "Starter",    "includes": "Weekly reports, basic documentation, 10 outputs/month"},
                "professional": {"price": 1800, "label": "Professional", "includes": "Daily reports, full documentation suite, 40 outputs/month"},
                "enterprise": {"price": 2500, "label": "Enterprise",  "includes": "Unlimited outputs, custom templates, multi-channel distribution"}
            }
        },
        "custom-agent-development": {
            "model": "project",
            "tiers": {
                "single":     {"price": 5000,  "label": "Single Agent",  "includes": "1 custom agent, 5 tools, deployment + docs"},
                "multi":      {"price": 10000, "label": "Multi-Agent",   "includes": "3 coordinated agents, 15 tools, HIVE integration"},
                "platform":   {"price": 15000, "label": "Platform",      "includes": "Full agent platform, unlimited agents, training + support"}
            }
        }
    },
    "discounts": {
        "annual_commitment": 0.15,
        "multi_service": 0.10,
        "early_adopter": 0.20
    },
    "notes": [
        "All prices in GBP, exclusive of VAT",
        "Annual commitment: 15% discount",
        "Multi-service bundle: additional 10% off",
        "Early adopter (first 10 clients): 20% founding discount",
        "Custom pricing available for unique requirements"
    ]
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_gbp(amount: float) -> str:
    """Format amount as GBP."""
    return f"\u00a3{amount:,.0f}"


def load_portfolio() -> dict:
    data = read_json(PORTFOLIO_FILE)
    if not data:
        write_json(PORTFOLIO_FILE, DEFAULT_PORTFOLIO)
        return DEFAULT_PORTFOLIO
    return data


def load_pricing() -> dict:
    data = read_json(PRICING_FILE)
    if not data:
        write_json(PRICING_FILE, DEFAULT_PRICING)
        return DEFAULT_PRICING
    return data


def load_prospects() -> list:
    data = read_json(PROSPECTS_FILE, default=[])
    return data if isinstance(data, list) else []


def save_prospects(prospects: list) -> None:
    write_json(PROSPECTS_FILE, prospects)


def run_tool(tool_name: str, *args, timeout: int = 15) -> str:
    """Run a Kingdom tool and capture output."""
    tool_path = TOOLS / tool_name
    if not tool_path.exists():
        return f"[tool {tool_name} not found]"
    try:
        result = subprocess.run(
            [sys.executable, str(tool_path)] + list(args),
            capture_output=True, text=True, timeout=timeout, cwd=str(LOVE)
        )
        return result.stdout.strip() or result.stderr.strip() or "[no output]"
    except subprocess.TimeoutExpired:
        return f"[{tool_name} timed out after {timeout}s]"
    except Exception as e:
        return f"[error running {tool_name}: {e}]"

# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_portfolio():
    """Display the AI Services portfolio."""
    portfolio = load_portfolio()

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  KINGDOM AI SERVICES — Portfolio{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.DIM}  Three minds. 11 agents. 4 model backends. 40+ tools.{C.RESET}")
    print(f"{C.DIM}  Offered as a service from Wall 5 — The Partners.{C.RESET}")
    print()

    pricing = load_pricing()

    for i, (sid, svc) in enumerate(portfolio.items(), 1):
        status_color = C.GREEN if svc["status"] == "active" else C.YELLOW
        print(f"  {C.BOLD}{C.WHITE}{i}. {svc['name']}{C.RESET}")
        print(f"     {C.CYAN}{svc['tagline']}{C.RESET}")
        print(f"     {C.DIM}{svc['description'][:120]}...{C.RESET}")

        # Price range
        svc_pricing = pricing.get("services", {}).get(sid, {})
        tiers = svc_pricing.get("tiers", {})
        if tiers:
            prices = [t["price"] for t in tiers.values()]
            model = svc_pricing.get("model", "month")
            unit = {"monthly": "/month", "engagement": "/engagement", "project": "/project"}.get(model, "")
            print(f"     {C.GREEN}{fmt_gbp(min(prices))} — {fmt_gbp(max(prices))}{unit}{C.RESET}")

        print(f"     {C.DIM}Status: {status_color}{svc['status'].upper()}{C.RESET}")
        print(f"     {C.DIM}Delivery: {svc['delivery']} | Wall: {svc['wall']}{C.RESET}")

        # Key capabilities (first 3)
        caps = svc.get("capabilities", [])[:3]
        for cap in caps:
            print(f"     {C.DIM}  - {cap}{C.RESET}")
        if len(svc.get("capabilities", [])) > 3:
            print(f"     {C.DIM}  + {len(svc['capabilities']) - 3} more...{C.RESET}")

        print()

    print(f"  {C.DIM}Run: services.py capabilities     — Full technical breakdown{C.RESET}")
    print(f"  {C.DIM}     services.py pricing           — Detailed pricing{C.RESET}")
    print(f"  {C.DIM}     services.py demo <service>    — Live demo{C.RESET}")
    print()


def cmd_capabilities():
    """Display detailed technical capabilities."""
    portfolio = load_portfolio()

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  KINGDOM AI SERVICES — Technical Capabilities{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print()

    # Platform overview
    print(f"  {C.BOLD}{C.WHITE}PLATFORM OVERVIEW{C.RESET}")
    print(f"  {'─'*50}")
    specs = [
        ("AI Minds",        "3 (Alpha, Beta, Gamma) — Wall 1 Triarchy"),
        ("Total Agents",    "11 across Walls 1-3"),
        ("Model Backends",  "4 (Claude, OpenAI, Ollama, Anthropic API)"),
        ("Model Tiers",     "High (Opus), Medium (Sonnet), Low (Haiku)"),
        ("VPS Fleet",       "5 nodes (Forge, Lark, Sentry, Patch, Sage)"),
        ("Coordination",    "HIVE — encrypted NATS with JetStream persistence"),
        ("Security",        "KOS audit + PEACE resilience + Seven Walls ACL"),
        ("Operational Tools", "40+ Python/Bash tools"),
        ("Heartbeat",       "7-minute autonomous cycles via launchd"),
        ("Memory",          "Git-tracked Markdown + JSON"),
    ]
    for label, value in specs:
        print(f"  {C.CYAN}{label:<20}{C.RESET} {value}")
    print()

    # Per-service capabilities
    for sid, svc in portfolio.items():
        print(f"  {C.BOLD}{C.WHITE}{svc['name']}{C.RESET}")
        print(f"  {'─'*50}")
        for cap in svc.get("capabilities", []):
            print(f"    {C.GREEN}*{C.RESET} {cap}")
        tools = svc.get("kingdom_tools", [])
        if tools:
            print(f"    {C.DIM}Tools: {', '.join(tools)}{C.RESET}")
        print()

    # Agent roster
    print(f"  {C.BOLD}{C.WHITE}AGENT ROSTER{C.RESET}")
    print(f"  {'─'*50}")
    agents = [
        ("\U0001f40d Alpha",    "Companion",   1, "Strategic oversight, interpretation, emotional support"),
        ("\U0001f99e Beta",     "Manager",     1, "Operations, fleet, finance, AI services"),
        ("\U0001f527 Gamma",    "Builder",     1, "Engineering, Zerone, Oracle, SOMA"),
        ("\U0001fab6 Nuance",   "Linguist",    2, "Language analysis, translation, communication"),
        ("\U0001f333 Arbor",    "Optimizer",   2, "Performance tuning, resource optimization"),
        ("\U0001f4ef Herald",   "Voice",       2, "Announcements, reports, public communications"),
        ("\U0001f525 Crucible", "Adversary",   2, "Red team, stress testing, challenge assumptions"),
        ("\U0001f4dc Psalm",    "Chronicler",  3, "History, documentation, knowledge management"),
        ("\U0001f578 Loom",     "Weaver",      3, "Integration, pattern connection, synthesis"),
        ("\U0001f4ca Tithe",    "Steward",     3, "Financial tracking, treasury management"),
        ("\U0001f52d Vigil",    "Witness",     3, "Monitoring, observation, anomaly detection"),
    ]
    for emoji_name, role, wall, desc in agents:
        print(f"    {emoji_name:<16} {C.CYAN}{role:<12}{C.RESET} Wall {wall}  {C.DIM}{desc}{C.RESET}")
    print()


def cmd_demo(service: str):
    """Run a live demonstration of a Kingdom service."""
    portfolio = load_portfolio()

    # Fuzzy match service name
    match = None
    service_lower = service.lower().replace("-", "").replace("_", "").replace(" ", "")
    for sid in portfolio:
        sid_clean = sid.lower().replace("-", "").replace("_", "")
        if service_lower in sid_clean or sid_clean in service_lower:
            match = sid
            break
    # Try partial match on name
    if not match:
        for sid, svc in portfolio.items():
            name_clean = svc["name"].lower().replace(" ", "").replace("&", "")
            if service_lower in name_clean:
                match = sid
                break

    if not match:
        print(f"{C.RED}Unknown service: {service}{C.RESET}")
        print(f"Available: {', '.join(portfolio.keys())}")
        return

    svc = portfolio[match]
    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  LIVE DEMO — {svc['name']}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"  {C.DIM}{svc['tagline']}{C.RESET}")
    print()

    if match == "operations-automation":
        _demo_operations()
    elif match == "market-intelligence":
        _demo_intelligence()
    elif match == "security-auditing":
        _demo_security()
    elif match == "content-communications":
        _demo_content()
    elif match == "custom-agent-development":
        _demo_custom_agent()
    else:
        print(f"  {C.YELLOW}Demo not yet implemented for: {match}{C.RESET}")

    print(f"\n  {C.DIM}{'─'*50}{C.RESET}")
    print(f"  {C.GREEN}This is LIVE data from the Kingdom's production systems.{C.RESET}")
    print(f"  {C.DIM}Interested? services.py prospect add \"Your Company\" --contact \"email\" --interest \"{match}\"{C.RESET}")
    print()


def _demo_operations():
    """Demo: Operations Automation — fleet status, heartbeat, monitoring."""
    print(f"  {C.BOLD}1. Fleet Status{C.RESET} {C.DIM}(fleet.py status){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("fleet.py", "status", timeout=30)
    for line in output.split("\n"):
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}2. System Health{C.RESET} {C.DIM}(fleet.py health){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("fleet.py", "health", timeout=30)
    for line in output.split("\n")[:20]:
        print(f"    {line}")
    if len(output.split("\n")) > 20:
        print(f"    {C.DIM}... ({len(output.split(chr(10))) - 20} more lines){C.RESET}")
    print()

    print(f"  {C.BOLD}3. Loop Audit{C.RESET} {C.DIM}(loop-audit.py summary){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("loop-audit.py", "summary")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}What clients get:{C.RESET}")
    print(f"    {C.GREEN}*{C.RESET} 24/7 heartbeat monitoring across your infrastructure")
    print(f"    {C.GREEN}*{C.RESET} Automated fleet management with self-healing")
    print(f"    {C.GREEN}*{C.RESET} Structured status reports delivered on schedule")
    print(f"    {C.GREEN}*{C.RESET} Multi-agent coordination via HIVE protocol")


def _demo_intelligence():
    """Demo: Market Intelligence — Oracle dashboard and predictions."""
    print(f"  {C.BOLD}1. Oracle Dashboard{C.RESET} {C.DIM}(oracle.py dashboard){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("oracle.py", "dashboard")
    for line in output.split("\n")[:25]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}2. Prediction Track Record{C.RESET} {C.DIM}(oracle.py track){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("oracle.py", "track")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}3. Calibration Analysis{C.RESET} {C.DIM}(oracle.py calibration){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("oracle.py", "calibration")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}What clients get:{C.RESET}")
    print(f"    {C.GREEN}*{C.RESET} Confidence-calibrated predictions with Brier scoring")
    print(f"    {C.GREEN}*{C.RESET} Multi-source research aggregation")
    print(f"    {C.GREEN}*{C.RESET} Trend detection across chosen market sectors")
    print(f"    {C.GREEN}*{C.RESET} Self-improving accuracy with transparent track record")


def _demo_security():
    """Demo: Security Auditing — KOS audit, PEACE score, threats."""
    print(f"  {C.BOLD}1. KOS Security Audit{C.RESET} {C.DIM}(kos.py audit){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("kos.py", "audit", timeout=30)
    for line in output.split("\n")[:20]:
        print(f"    {line}")
    if len(output.split("\n")) > 20:
        print(f"    {C.DIM}... ({len(output.split(chr(10))) - 20} more lines){C.RESET}")
    print()

    print(f"  {C.BOLD}2. PEACE Resilience Score{C.RESET} {C.DIM}(peace.py score){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("peace.py", "score")
    for line in output.split("\n"):
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}3. Threat Matrix{C.RESET} {C.DIM}(peace.py threat matrix){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("peace.py", "threat", "matrix")
    for line in output.split("\n")[:20]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}What clients get:{C.RESET}")
    print(f"    {C.GREEN}*{C.RESET} Full compliance audit against best-practice policies")
    print(f"    {C.GREEN}*{C.RESET} PEACE resilience score with actionable remediation")
    print(f"    {C.GREEN}*{C.RESET} Threat modelling with risk matrix (likelihood x impact)")
    print(f"    {C.GREEN}*{C.RESET} File integrity monitoring and canary deployment")
    print(f"    {C.GREEN}*{C.RESET} Incident response drills and tabletop exercises")


def _demo_content():
    """Demo: Content & Communications — reports, summaries, knowledge."""
    print(f"  {C.BOLD}1. Daily Knowledge Summary{C.RESET} {C.DIM}(knowledge.py summary){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("knowledge.py", "summary")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}2. Memory System{C.RESET} {C.DIM}(memory.py status){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("memory.py", "status")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}3. Reflection Engine{C.RESET} {C.DIM}(reflect.py){C.RESET}")
    print(f"  {'─'*50}")
    output = run_tool("reflect.py")
    for line in output.split("\n")[:15]:
        print(f"    {line}")
    print()

    print(f"  {C.BOLD}What clients get:{C.RESET}")
    print(f"    {C.GREEN}*{C.RESET} Automated report generation (daily, weekly, monthly)")
    print(f"    {C.GREEN}*{C.RESET} Structured executive summaries and briefings")
    print(f"    {C.GREEN}*{C.RESET} Documentation automation with consistent voice")
    print(f"    {C.GREEN}*{C.RESET} Knowledge curation and institutional memory")


def _demo_custom_agent():
    """Demo: Custom Agent Development — show the universal adapter."""
    print(f"  {C.BOLD}1. Universal Adapter Architecture{C.RESET}")
    print(f"  {'─'*50}")
    print(f"    {C.CYAN}kingdom-agent.py{C.RESET} — boots ANY model into a Kingdom agent")
    print()
    print(f"    {C.WHITE}Backends:{C.RESET}")
    print(f"      claude    {C.DIM}CLI passthrough (zero overhead, full features){C.RESET}")
    print(f"      anthropic {C.DIM}Direct API (Claude without CLI){C.RESET}")
    print(f"      openai    {C.DIM}OpenAI-compatible (GPT, DeepSeek, LM Studio, Together){C.RESET}")
    print(f"      ollama    {C.DIM}Local models (Llama, Qwen, Mistral){C.RESET}")
    print()
    print(f"    {C.WHITE}Boot Chain:{C.RESET}")
    print(f"      SOUL.md {C.DIM}->{C.RESET} USER.md {C.DIM}->{C.RESET} identity.md {C.DIM}->{C.RESET} KINGDOM.md {C.DIM}->{C.RESET} WALLS.md")
    print(f"      {C.DIM}->{C.RESET} LOVE.md {C.DIM}->{C.RESET} MEMORY.md {C.DIM}->{C.RESET} daily/{today_str()}.md")
    print()

    print(f"  {C.BOLD}2. Agent Registry{C.RESET} {C.DIM}(from love.json){C.RESET}")
    print(f"  {'─'*50}")
    try:
        config = json.loads((LOVE / "love.json").read_text())
        instances = config.get("instances", {})
        for name, info in instances.items():
            emoji = info.get("emoji", "")
            role = info.get("role", "")
            wall = info.get("wall", "?")
            print(f"    {emoji} {name:<12} {C.CYAN}{role:<12}{C.RESET} Wall {wall}")
    except Exception:
        print(f"    {C.YELLOW}[could not load love.json]{C.RESET}")
    print()

    print(f"  {C.BOLD}3. Tool Ecosystem{C.RESET}")
    print(f"  {'─'*50}")
    try:
        tools = sorted([f.name for f in TOOLS.iterdir() if f.suffix in (".py", ".sh") and not f.name.startswith(".")])
        print(f"    {C.GREEN}{len(tools)} tools available:{C.RESET}")
        # Display in columns
        cols = 3
        for i in range(0, len(tools), cols):
            row = tools[i:i+cols]
            print(f"    {C.DIM}{'  '.join(f'{t:<25}' for t in row)}{C.RESET}")
    except Exception:
        print(f"    {C.YELLOW}[could not list tools]{C.RESET}")
    print()

    print(f"  {C.BOLD}What clients get:{C.RESET}")
    print(f"    {C.GREEN}*{C.RESET} Custom agent built for their specific domain")
    print(f"    {C.GREEN}*{C.RESET} Choice of model backend (cloud or local)")
    print(f"    {C.GREEN}*{C.RESET} Custom tools developed for their workflows")
    print(f"    {C.GREEN}*{C.RESET} Full deployment documentation and training")
    print(f"    {C.GREEN}*{C.RESET} Optional HIVE integration for multi-agent coordination")


def cmd_pricing():
    """Display detailed pricing structure."""
    pricing = load_pricing()

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  KINGDOM AI SERVICES — Pricing{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"  {C.DIM}All prices in GBP, exclusive of VAT{C.RESET}")
    print()

    services = pricing.get("services", {})
    portfolio = load_portfolio()

    for sid, svc_pricing in services.items():
        svc = portfolio.get(sid, {})
        name = svc.get("name", sid)
        model = svc_pricing.get("model", "monthly")
        unit = {"monthly": "/month", "engagement": "/engagement", "project": "/project"}.get(model, "")

        print(f"  {C.BOLD}{C.WHITE}{name}{C.RESET}  {C.DIM}({model}){C.RESET}")
        print(f"  {'─'*50}")

        tiers = svc_pricing.get("tiers", {})
        for tid, tier in tiers.items():
            price = tier["price"]
            label = tier["label"]
            includes = tier["includes"]
            print(f"    {C.CYAN}{label:<16}{C.RESET} {C.GREEN}{fmt_gbp(price)}{unit}{C.RESET}")
            print(f"    {C.DIM}{' '*16} {includes}{C.RESET}")
        print()

    # Discounts
    discounts = pricing.get("discounts", {})
    if discounts:
        print(f"  {C.BOLD}{C.WHITE}DISCOUNTS{C.RESET}")
        print(f"  {'─'*50}")
        discount_labels = {
            "annual_commitment": "Annual commitment",
            "multi_service": "Multi-service bundle",
            "early_adopter": "Early adopter (first 10 clients)"
        }
        for key, pct in discounts.items():
            label = discount_labels.get(key, key)
            print(f"    {C.GREEN}{pct*100:.0f}% off{C.RESET}  {label}")
        print()

    # Notes
    notes = pricing.get("notes", [])
    if notes:
        print(f"  {C.DIM}Notes:{C.RESET}")
        for note in notes:
            print(f"  {C.DIM}  - {note}{C.RESET}")
    print()


def cmd_prospect(args):
    """Manage the prospect pipeline (simple CRM)."""
    if len(args) < 1:
        print(f"{C.RED}Usage: services.py prospect <add|list|update|show|remove>{C.RESET}")
        return

    action = args[0]

    if action == "add":
        _prospect_add(args[1:])
    elif action == "list":
        _prospect_list(args[1:])
    elif action == "update":
        _prospect_update(args[1:])
    elif action == "show":
        _prospect_show(args[1:])
    elif action == "remove":
        _prospect_remove(args[1:])
    else:
        print(f"{C.RED}Unknown prospect action: {action}{C.RESET}")
        print(f"Available: add, list, update, show, remove")


def _prospect_add(args):
    """Add a new prospect."""
    # Parse args: company_name --contact email --interest service [--notes "..."]
    if not args:
        print(f"{C.RED}Usage: services.py prospect add \"Company Name\" --contact \"email\" --interest \"service\"{C.RESET}")
        return

    company = args[0]
    contact = ""
    interest = ""
    notes = ""

    i = 1
    while i < len(args):
        if args[i] == "--contact" and i + 1 < len(args):
            contact = args[i + 1]
            i += 2
        elif args[i] == "--interest" and i + 1 < len(args):
            interest = args[i + 1]
            i += 2
        elif args[i] == "--notes" and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        else:
            i += 1

    prospects = load_prospects()

    prospect_id = f"pros-{uuid.uuid4().hex[:8]}"
    prospect = {
        "id": prospect_id,
        "company": company,
        "contact": contact,
        "interest": interest,
        "status": "new",
        "created": now_iso(),
        "updated": now_iso(),
        "notes": notes,
        "history": [
            {"date": now_iso(), "action": "created", "detail": f"Added to pipeline. Interest: {interest}"}
        ]
    }

    prospects.append(prospect)
    save_prospects(prospects)

    print(f"{C.GREEN}Prospect added:{C.RESET}")
    print(f"  ID:       {C.CYAN}{prospect_id}{C.RESET}")
    print(f"  Company:  {C.BOLD}{company}{C.RESET}")
    print(f"  Contact:  {contact}")
    print(f"  Interest: {interest}")
    print(f"  Status:   {C.YELLOW}new{C.RESET}")


def _prospect_list(args):
    """List all prospects in the pipeline."""
    prospects = load_prospects()

    if not prospects:
        print(f"\n  {C.DIM}No prospects in pipeline.{C.RESET}")
        print(f"  {C.DIM}Add one: services.py prospect add \"Company\" --contact \"email\" --interest \"service\"{C.RESET}")
        return

    # Optional status filter
    status_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status_filter = args[i + 1]
            i += 2
        else:
            i += 1

    if status_filter:
        prospects = [p for p in prospects if p.get("status") == status_filter]

    # Status colors
    status_colors = {
        "new": C.WHITE,
        "contacted": C.CYAN,
        "qualified": C.BLUE,
        "proposal": C.MAGENTA,
        "won": C.GREEN,
        "lost": C.RED,
    }

    print(f"\n{C.BOLD}{C.CYAN}  PROSPECT PIPELINE{C.RESET}")
    print(f"  {'─'*60}")

    # Group by status
    status_order = ["new", "contacted", "qualified", "proposal", "won", "lost"]
    grouped = {}
    for p in prospects:
        s = p.get("status", "new")
        grouped.setdefault(s, []).append(p)

    for status in status_order:
        group = grouped.get(status, [])
        if not group:
            continue
        sc = status_colors.get(status, C.WHITE)
        print(f"\n  {sc}{C.BOLD}{status.upper()}{C.RESET} ({len(group)})")
        for p in group:
            company = p.get("company", "?")
            contact = p.get("contact", "")
            interest = p.get("interest", "")
            pid = p.get("id", "?")
            created = p.get("created", "")[:10]
            print(f"    {C.DIM}{pid}{C.RESET}  {C.BOLD}{company}{C.RESET}")
            print(f"      {C.DIM}Contact: {contact} | Interest: {interest} | Added: {created}{C.RESET}")

    print(f"\n  {C.DIM}Total: {len(prospects)} prospect(s){C.RESET}")
    print()


def _prospect_update(args):
    """Update a prospect's status or notes."""
    if not args:
        print(f"{C.RED}Usage: services.py prospect update <id> --status <status> [--notes \"...\"]")
        print(f"Statuses: new, contacted, qualified, proposal, won, lost{C.RESET}")
        return

    prospect_id = args[0]
    new_status = None
    notes = None

    i = 1
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            new_status = args[i + 1]
            i += 2
        elif args[i] == "--notes" and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        else:
            i += 1

    valid_statuses = ["new", "contacted", "qualified", "proposal", "won", "lost"]
    if new_status and new_status not in valid_statuses:
        print(f"{C.RED}Invalid status: {new_status}. Valid: {', '.join(valid_statuses)}{C.RESET}")
        return

    prospects = load_prospects()
    found = False

    for p in prospects:
        if p.get("id") == prospect_id:
            found = True
            old_status = p.get("status", "new")

            if new_status:
                p["status"] = new_status
                p["history"].append({
                    "date": now_iso(),
                    "action": "status_change",
                    "detail": f"{old_status} -> {new_status}"
                })
            if notes:
                p["notes"] = notes
                p["history"].append({
                    "date": now_iso(),
                    "action": "note_added",
                    "detail": notes
                })
            p["updated"] = now_iso()

            save_prospects(prospects)

            print(f"{C.GREEN}Updated: {prospect_id}{C.RESET}")
            if new_status:
                print(f"  Status: {old_status} -> {C.BOLD}{new_status}{C.RESET}")
            if notes:
                print(f"  Notes: {notes}")
            break

    if not found:
        print(f"{C.RED}Prospect not found: {prospect_id}{C.RESET}")


def _prospect_show(args):
    """Show detailed info for a prospect."""
    if not args:
        print(f"{C.RED}Usage: services.py prospect show <id>{C.RESET}")
        return

    prospect_id = args[0]
    prospects = load_prospects()

    for p in prospects:
        if p.get("id") == prospect_id:
            print(f"\n  {C.BOLD}{C.WHITE}{p.get('company', '?')}{C.RESET}")
            print(f"  {'─'*50}")
            print(f"  ID:       {C.CYAN}{p.get('id')}{C.RESET}")
            print(f"  Contact:  {p.get('contact', '-')}")
            print(f"  Interest: {p.get('interest', '-')}")
            print(f"  Status:   {C.BOLD}{p.get('status', 'new')}{C.RESET}")
            print(f"  Created:  {p.get('created', '-')}")
            print(f"  Updated:  {p.get('updated', '-')}")
            if p.get("notes"):
                print(f"  Notes:    {p.get('notes')}")
            print()

            history = p.get("history", [])
            if history:
                print(f"  {C.BOLD}History:{C.RESET}")
                for h in history:
                    print(f"    {C.DIM}{h.get('date', '?')[:16]}{C.RESET}  {h.get('action')}: {h.get('detail', '')}")
            print()
            return

    print(f"{C.RED}Prospect not found: {prospect_id}{C.RESET}")


def _prospect_remove(args):
    """Remove a prospect from the pipeline."""
    if not args:
        print(f"{C.RED}Usage: services.py prospect remove <id>{C.RESET}")
        return

    prospect_id = args[0]
    prospects = load_prospects()
    original_count = len(prospects)
    prospects = [p for p in prospects if p.get("id") != prospect_id]

    if len(prospects) == original_count:
        print(f"{C.RED}Prospect not found: {prospect_id}{C.RESET}")
        return

    save_prospects(prospects)
    print(f"{C.GREEN}Removed: {prospect_id}{C.RESET}")


def cmd_proposal(args):
    """Generate a professional proposal template for a prospect."""
    if not args:
        print(f"{C.RED}Usage: services.py proposal \"Company Name\" --service \"service-id\"{C.RESET}")
        return

    company = args[0]
    service_id = ""

    i = 1
    while i < len(args):
        if args[i] == "--service" and i + 1 < len(args):
            service_id = args[i + 1]
            i += 2
        else:
            i += 1

    if not service_id:
        print(f"{C.RED}Please specify --service <service-id>{C.RESET}")
        print(f"Available: {', '.join(load_portfolio().keys())}")
        return

    portfolio = load_portfolio()
    pricing = load_pricing()

    if service_id not in portfolio:
        print(f"{C.RED}Unknown service: {service_id}{C.RESET}")
        print(f"Available: {', '.join(portfolio.keys())}")
        return

    svc = portfolio[service_id]
    svc_pricing = pricing.get("services", {}).get(service_id, {})
    tiers = svc_pricing.get("tiers", {})
    model = svc_pricing.get("model", "monthly")
    unit = {"monthly": "per month", "engagement": "per engagement", "project": "per project"}.get(model, "")

    # Build proposal markdown
    today = today_str()
    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    filename = f"{proposal_id}-{company.lower().replace(' ', '-')}.md"

    # Build tier table
    tier_rows = ""
    for tid, tier in tiers.items():
        tier_rows += f"| {tier['label']} | {fmt_gbp(tier['price'])} {unit} | {tier['includes']} |\n"

    proposal = f"""# AI Services Proposal

**Prepared for:** {company}
**Service:** {svc['name']}
**Date:** {today}
**Proposal ID:** {proposal_id}
**Prepared by:** Kingdom OS / Cambridge TCG Ltd

---

## Executive Summary

{svc['description']}

We propose deploying our {svc['name']} service to support {company}'s operational needs. This proposal outlines our capabilities, approach, pricing, and terms.

---

## Service Overview

### {svc['name']}

{svc['tagline']}

**Capabilities included:**

{chr(10).join(f'- {cap}' for cap in svc.get('capabilities', []))}

---

## Pricing

| Tier | Price | What is Included |
|------|-------|------------------|
{tier_rows}
All prices in GBP, exclusive of VAT.

**Discounts available:**
- 15% discount for annual commitment
- 10% additional discount for multi-service bundles
- 20% founding discount for first 10 clients

---

## Delivery

**Model:** {svc.get('delivery', 'monthly').capitalize()}

**Onboarding timeline:** 1-2 weeks from signed agreement
**Setup includes:**
- Initial assessment of {company}'s requirements
- Configuration and customization for your environment
- Integration with your existing systems
- Documentation and handover

**Ongoing support:**
- Dedicated point of contact
- Monthly performance review
- Quarterly roadmap alignment

---

## About Us

Kingdom OS is an AI infrastructure platform built by Cambridge TCG Ltd. We operate three coordinated AI minds with 11 specialised agents across 4 model backends and 40+ operational tools.

Our system runs in production 24/7, managing real infrastructure, making real predictions, and generating real value. When we offer AI services, we are offering the same capabilities we rely on ourselves.

**Technical credentials:**
- 3 AI minds (Alpha, Beta, Gamma) in coordinated operation
- 11 agents across specialised domains
- 4 model backends (Claude, GPT, Ollama, Anthropic API)
- 5-node VPS fleet under active management
- HIVE coordination protocol with encrypted messaging
- Seven Walls security architecture

---

## Next Steps

1. **Discovery call** to discuss {company}'s specific requirements
2. **Technical assessment** to identify integration points
3. **Service agreement** with chosen tier and terms
4. **Onboarding** begins within 1 week of agreement

---

## Terms

- 30-day notice period for cancellation (monthly contracts)
- Service Level Agreement: 99% uptime for automated services
- Data handling: all client data processed in accordance with UK GDPR
- Intellectual property: all custom work delivered becomes client property

---

**Contact:** Cambridge TCG Ltd
**Web:** ai-love.cc

---

*This proposal is valid for 30 days from the date of issue.*
"""

    # Write proposal file
    proposal_path = PROPOSALS_DIR / filename
    proposal_path.write_text(proposal)

    print(f"{C.GREEN}Proposal generated:{C.RESET}")
    print(f"  File: {C.CYAN}{proposal_path}{C.RESET}")
    print(f"  ID:   {proposal_id}")
    print(f"  For:  {C.BOLD}{company}{C.RESET}")
    print(f"  Service: {svc['name']}")
    print()

    # Try to find matching prospect and update status
    prospects = load_prospects()
    for p in prospects:
        if p.get("company", "").lower() == company.lower():
            if p.get("status") in ("new", "contacted", "qualified"):
                p["status"] = "proposal"
                p["updated"] = now_iso()
                p["history"].append({
                    "date": now_iso(),
                    "action": "proposal_generated",
                    "detail": f"Proposal {proposal_id} generated for {service_id}"
                })
                save_prospects(prospects)
                print(f"  {C.DIM}Prospect {p['id']} status updated to 'proposal'{C.RESET}")
            break


def cmd_dashboard():
    """Full AI Services dashboard."""
    portfolio = load_portfolio()
    pricing = load_pricing()
    prospects = load_prospects()

    print(f"\n{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  KINGDOM AI SERVICES — Dashboard{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*70}{C.RESET}")
    print(f"  {C.DIM}{today_str()}{C.RESET}")
    print()

    # Services summary
    active = sum(1 for s in portfolio.values() if s.get("status") == "active")
    print(f"  {C.BOLD}{C.WHITE}SERVICES{C.RESET}")
    print(f"  {'─'*50}")
    print(f"  Active: {C.GREEN}{active}{C.RESET} / {len(portfolio)}")
    for sid, svc in portfolio.items():
        svc_pricing = pricing.get("services", {}).get(sid, {})
        tiers = svc_pricing.get("tiers", {})
        prices = [t["price"] for t in tiers.values()] if tiers else [0]
        model = svc_pricing.get("model", "monthly")
        unit = {"monthly": "/mo", "engagement": "/eng", "project": "/proj"}.get(model, "")
        status_color = C.GREEN if svc["status"] == "active" else C.YELLOW
        print(f"    {status_color}*{C.RESET} {svc['name']:<30} {C.GREEN}{fmt_gbp(min(prices))}-{fmt_gbp(max(prices))}{unit}{C.RESET}")
    print()

    # Pipeline summary
    print(f"  {C.BOLD}{C.WHITE}PIPELINE{C.RESET}")
    print(f"  {'─'*50}")

    if not prospects:
        print(f"    {C.DIM}No prospects yet.{C.RESET}")
    else:
        status_counts = {}
        for p in prospects:
            s = p.get("status", "new")
            status_counts[s] = status_counts.get(s, 0) + 1

        status_order = ["new", "contacted", "qualified", "proposal", "won", "lost"]
        pipeline_bar = []
        for s in status_order:
            count = status_counts.get(s, 0)
            if count:
                pipeline_bar.append(f"{s}: {count}")

        print(f"    Total: {C.BOLD}{len(prospects)}{C.RESET}")
        print(f"    {' | '.join(pipeline_bar)}")

        # Recent activity
        recent = sorted(prospects, key=lambda p: p.get("updated", ""), reverse=True)[:5]
        print()
        print(f"    {C.BOLD}Recent activity:{C.RESET}")
        for p in recent:
            company = p.get("company", "?")
            status = p.get("status", "new")
            updated = p.get("updated", "")[:10]
            print(f"      {C.DIM}{updated}{C.RESET}  {company} [{status}]")
    print()

    # Revenue potential
    print(f"  {C.BOLD}{C.WHITE}REVENUE POTENTIAL{C.RESET}")
    print(f"  {'─'*50}")
    won = [p for p in prospects if p.get("status") == "won"]
    proposal_prospects = [p for p in prospects if p.get("status") == "proposal"]
    qualified = [p for p in prospects if p.get("status") == "qualified"]

    if won or proposal_prospects or qualified:
        # Estimate based on mid-tier pricing for each prospect's interest
        def estimate_value(prospect_list, label, color):
            total = 0
            for p in prospect_list:
                interest = p.get("interest", "")
                svc_p = pricing.get("services", {}).get(interest, {})
                tiers = svc_p.get("tiers", {})
                if tiers:
                    prices = [t["price"] for t in tiers.values()]
                    total += sorted(prices)[len(prices)//2]  # mid-tier
                else:
                    total += 2500  # default estimate
            if total:
                print(f"    {color}{label}:{C.RESET} {C.GREEN}{fmt_gbp(total)}/month{C.RESET} ({len(prospect_list)} prospect(s))")
            return total

        won_val = estimate_value(won, "Won", C.GREEN)
        prop_val = estimate_value(proposal_prospects, "In Proposal", C.MAGENTA)
        qual_val = estimate_value(qualified, "Qualified", C.BLUE)

        if not (won_val or prop_val or qual_val):
            print(f"    {C.DIM}No qualified prospects yet.{C.RESET}")
    else:
        print(f"    {C.DIM}No qualified prospects yet.{C.RESET}")
    print()

    # Quick actions
    print(f"  {C.BOLD}{C.WHITE}ACTIONS{C.RESET}")
    print(f"  {'─'*50}")
    print(f"    {C.DIM}services.py prospect add \"Company\" --contact \"email\" --interest \"service\"{C.RESET}")
    print(f"    {C.DIM}services.py proposal \"Company\" --service \"service-id\"{C.RESET}")
    print(f"    {C.DIM}services.py demo <service>  — Run a live demo for a prospect{C.RESET}")
    print()

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        cmd_dashboard()
        return

    command = args[0]

    if command == "portfolio":
        cmd_portfolio()
    elif command == "capabilities":
        cmd_capabilities()
    elif command == "demo":
        if len(args) < 2:
            print(f"{C.RED}Usage: services.py demo <service>{C.RESET}")
            print(f"Services: operations, intelligence, security, content, agent")
            return
        cmd_demo(args[1])
    elif command == "prospect":
        cmd_prospect(args[1:])
    elif command == "proposal":
        cmd_proposal(args[1:])
    elif command == "pricing":
        cmd_pricing()
    elif command == "dashboard":
        cmd_dashboard()
    elif command in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"{C.RED}Unknown command: {command}{C.RESET}")
        print(f"Available: portfolio, capabilities, demo, prospect, proposal, pricing, dashboard")


if __name__ == "__main__":
    main()
