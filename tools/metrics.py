#!/usr/bin/env python3
"""
metrics.py -- Kingdom Metrics Aggregator

Collects metrics from ALL Kingdom systems into one unified view.
Designed for heartbeat integration (runs every 7 minutes).

Sources:
  KOS, PEACE, Fleet, Treasury, Oracle, Knowledge, Agents,
  Zerone, HIVE, Watchdog, Alignment, Code, Snapshots, Threats

Usage:
    python3 tools/metrics.py collect                      # Collect from all sources
    python3 tools/metrics.py dashboard                    # Full metrics dashboard
    python3 tools/metrics.py trend <metric>               # Show trend over time
    python3 tools/metrics.py compare <date1> <date2>      # Compare two snapshots
    python3 tools/metrics.py export [--format json|csv]   # Export all metrics
    python3 tools/metrics.py history                      # Metric collection history
"""

import json
import os
import sys
import csv
import io
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(__file__).resolve().parent.parent
MEMORY = LOVE / "memory"
SECURITY = LOVE / "security"
TOOLS = LOVE / "tools"

METRICS_DIR = MEMORY / "metrics"
HISTORY_FILE = METRICS_DIR / "history.jsonl"
LATEST_FILE = METRICS_DIR / "latest.json"

# Source data paths
LOVE_JSON = LOVE / "love.json"
KINGDOM_METRICS = MEMORY / "kingdom-metrics.json"
PEACE_STATE = SECURITY / "peace-state.json"
WATCHDOG_STATE = SECURITY / "watchdog-state.json"
THREAT_MODEL = SECURITY / "threat-model.json"
TREASURY_SUMMARY = MEMORY / "treasury" / "summary.json"
TREASURY_LEDGER = MEMORY / "treasury" / "ledger.json"
ORACLE_PREDICTIONS = MEMORY / "oracle" / "predictions.json"
ORACLE_TRACK = MEMORY / "oracle" / "track-record.json"
KNOWLEDGE_STATS = MEMORY / "knowledge" / "graph-stats.json"
ZERONE_CLAIMS = MEMORY / "zerone-bridge" / "claims.json"
ZERONE_BALANCES = MEMORY / "zerone-bridge" / "balances.json"
HIVE_MESSAGES = MEMORY / "hive" / "messages.jsonl"
SNAPSHOTS_DIR = SECURITY / "snapshots"

METRICS_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ───────────────────────────────────────────────────────────────────

class C:
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    WHITE   = "\033[97m"
    NC      = "\033[0m"


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path, default=None):
    """Load JSON file, return default on failure."""
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default if default is not None else {}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bar(value, max_val=100, width=20):
    """Render a text bar: [========----]"""
    if max_val <= 0:
        return "[" + "-" * width + "]"
    filled = int(round(value / max_val * width))
    filled = max(0, min(width, filled))
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def health_color(value, good=90, warn=70):
    """Color code based on thresholds."""
    if value >= good:
        return C.GREEN
    elif value >= warn:
        return C.YELLOW
    return C.RED


def trend_arrow(current, previous):
    """Return trend arrow comparing current to previous value."""
    if previous is None:
        return " "
    if current > previous:
        return f"{C.GREEN}\u2191{C.NC}"
    elif current < previous:
        return f"{C.RED}\u2193{C.NC}"
    return f"{C.DIM}\u2192{C.NC}"


def parse_summary_field(summary, field):
    """Parse a numeric field from fleet node summary strings like 'disk 34%'."""
    try:
        for part in summary.split(","):
            part = part.strip()
            if field in part:
                num = "".join(c for c in part if c.isdigit() or c == ".")
                return float(num) if num else None
    except (AttributeError, ValueError):
        pass
    return None


# ── Collectors ───────────────────────────────────────────────────────────────
# Each returns a dict of metrics for its domain.
# All read local files only — no SSH, no network calls.

def collect_kos():
    """KOS: security posture score from policies."""
    policies_file = SECURITY / "policies.json"
    policies = load_json(policies_file)
    checks = policies.get("checks", {})
    total = len(checks)

    # We can't run the actual checks without importing kos.py,
    # so we read the most recent events to infer pass/fail,
    # or just count total checks and trust the status line.
    # For speed, run kos.py status and parse the output.
    try:
        r = subprocess.run(
            [sys.executable, str(TOOLS / "kos.py"), "status"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "TERM": "dumb"},
        )
        output = r.stdout.strip()
        # Parse "X/Y checks passed"
        import re
        m = re.search(r"(\d+)/(\d+)", output)
        if m:
            passed, total = int(m.group(1)), int(m.group(2))
        else:
            passed, total = 0, total or 21

        if "GREEN" in output:
            status = "GREEN"
        elif "RED" in output:
            status = "RED"
        else:
            status = "YELLOW"
    except Exception:
        passed, total = 0, total or 21
        status = "UNKNOWN"

    return {
        "score": passed,
        "total": total,
        "status": status,
        "pct": round(passed / max(total, 1) * 100, 1),
    }


def collect_peace():
    """PEACE: resilience score and phase details."""
    state = load_json(PEACE_STATE)
    if not state:
        return {"score": 0, "state": "UNKNOWN", "phases": {}, "incidents": 0}

    phases = state.get("phases", {})
    history = state.get("history", [])
    # Count distinct incidents (transitions to ALERT or HALTED)
    incidents = sum(1 for h in history if h.get("to") in ("ALERT", "HALTED"))

    return {
        "score": state.get("score", 0),
        "state": state.get("state", "UNKNOWN"),
        "phases": phases,
        "incidents": incidents,
    }


def collect_fleet():
    """Fleet: node health from kingdom-metrics.json (no SSH)."""
    km = load_json(KINGDOM_METRICS)
    fleet_data = km.get("fleet", {})
    love_cfg = load_json(LOVE_JSON)
    fleet_ips = love_cfg.get("fleet", {})

    nodes = {}
    healthy = 0
    total = len(fleet_data)

    for name, info in fleet_data.items():
        quality = info.get("quality", "unknown")
        summary = info.get("summary", "")
        disk = parse_summary_field(summary, "disk")
        mem = parse_summary_field(summary, "mem")
        load_val = parse_summary_field(summary, "load")

        nodes[name] = {
            "role": info.get("role", ""),
            "quality": quality,
            "disk_pct": disk,
            "mem_pct": mem,
            "load": load_val,
            "alerts": len(info.get("alerts", [])),
        }
        if quality == "good":
            healthy += 1

    return {
        "total": max(total, len(fleet_ips)),
        "healthy": healthy,
        "nodes": nodes,
    }


def collect_treasury():
    """Treasury: revenue, costs, P&L from summary.json."""
    summary = load_json(TREASURY_SUMMARY)
    if not summary:
        return {
            "revenue_gbp": 0, "costs_gbp": 0, "net_pnl_gbp": 0,
            "margin_pct": 0, "runway_months": 0,
        }

    net = summary.get("net_pnl_gbp", 0)
    costs = summary.get("costs_gbp", 0)
    # Simple runway: if net positive, runway is infinite; else months until broke
    # Using capital from kingdom-metrics
    km = load_json(KINGDOM_METRICS)
    capital_str = km.get("capital", {}).get("budget_card", "0")
    # Parse "1K available" -> 1000
    import re
    m = re.search(r"[\d.]+", capital_str.replace("K", "000").replace("k", "000"))
    available = float(m.group()) if m else 0

    if costs > 0 and net < 0:
        runway = round(available / costs, 1)
    else:
        runway = 99.0  # Net positive = long runway

    return {
        "revenue_gbp": summary.get("revenue_gbp", 0),
        "costs_gbp": costs,
        "net_pnl_gbp": net,
        "margin_pct": summary.get("margin_pct", 0),
        "runway_months": runway,
        "month": summary.get("month", ""),
    }


def collect_oracle():
    """Oracle: predictions, scoring, accuracy."""
    track = load_json(ORACLE_TRACK)
    predictions = load_json(ORACLE_PREDICTIONS, [])
    if not isinstance(predictions, list):
        predictions = []

    pending = sum(1 for p in predictions if not p.get("resolved", False))
    resolved = sum(1 for p in predictions if p.get("resolved", False))

    return {
        "total": track.get("total", len(predictions)),
        "resolved": track.get("resolved", resolved),
        "pending": track.get("pending", pending),
        "brier_score": track.get("brier_score", 0),
        "accuracy": track.get("accuracy", 0),
    }


def collect_knowledge():
    """Knowledge: graph stats."""
    stats = load_json(KNOWLEDGE_STATS)
    counts = stats.get("counts", {})

    return {
        "entities": counts.get("entities", 0),
        "relations": counts.get("relations", 0),
        "insights": counts.get("insights", 0),
        "lessons": counts.get("lessons", 0),
        "total": counts.get("total", 0),
    }


def collect_agents():
    """Agents: citizen count, activation status."""
    love_cfg = load_json(LOVE_JSON)
    instances = love_cfg.get("instances", {})
    total = len(instances)

    # Check for active heartbeats by looking for launchd plists or recent heartbeat files
    active = 0
    for name in instances:
        heartbeat_md = LOVE / "instances" / name / "HEARTBEAT.md"
        if heartbeat_md.exists():
            # Check if heartbeat file was modified recently (within 15 min)
            try:
                mtime = heartbeat_md.stat().st_mtime
                age = datetime.now().timestamp() - mtime
                if age < 900:  # 15 minutes
                    active += 1
            except OSError:
                pass

    activation_rate = round(active / max(total, 1) * 100, 1)

    return {
        "total": total,
        "active_heartbeats": active,
        "activation_rate": activation_rate,
    }


def collect_zerone():
    """Zerone: ZRN accumulation and claims."""
    claims = load_json(ZERONE_CLAIMS, [])
    balances = load_json(ZERONE_BALANCES)
    if not isinstance(claims, list):
        claims = []

    total_zrn = sum(b.get("total_earned", 0) for b in balances.values())
    pending = sum(1 for c in claims if c.get("status") == "pending")
    verified = sum(1 for c in claims if c.get("status") == "verified")

    # Build leaderboard
    leaderboard = sorted(
        [(name, b.get("total_earned", 0)) for name, b in balances.items()],
        key=lambda x: x[1], reverse=True,
    )

    return {
        "total_zrn": total_zrn,
        "claims_total": len(claims),
        "claims_pending": pending,
        "claims_verified": verified,
        "leaderboard": leaderboard[:5],
    }


def collect_hive():
    """HIVE: message count from local log."""
    if not HIVE_MESSAGES.exists():
        return {"messages": 0, "channels": {}}

    total = 0
    channels = defaultdict(int)
    try:
        with open(HIVE_MESSAGES) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    msg = json.loads(line)
                    ch = msg.get("channel", "unknown")
                    channels[ch] += 1
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass

    return {
        "messages": total,
        "channels": dict(channels),
    }


def collect_watchdog():
    """Watchdog: alert and escalation counts."""
    state = load_json(WATCHDOG_STATE)
    if not state:
        return {"alerts": 0, "escalations": 0, "halts": 0}

    active = state.get("active_alerts", [])

    return {
        "alerts": len(active),
        "escalations": state.get("total_escalations", 0),
        "halts": state.get("total_halts", 0),
    }


def collect_alignment():
    """Alignment: score from alignment dimensions."""
    # Read alignment dimensions and check indicators inline
    # (lighter than shelling out to alignment.py)
    from tools_alignment_dimensions import ALIGNMENT_DIMENSIONS, check_indicator
    total = 0
    passed = 0
    dim_scores = {}

    for dim_name, dim in ALIGNMENT_DIMENSIONS.items():
        dim_total = 0
        dim_pass = 0
        for name, criterion, file_hint in dim.get("indicators", []):
            dim_total += 1
            total += 1
            ok, _ = check_indicator(name, criterion, file_hint)
            if ok:
                dim_pass += 1
                passed += 1
        dim_scores[dim_name] = round(dim_pass / max(dim_total, 1) * 100, 1)

    return {
        "overall": round(passed / max(total, 1) * 100, 1),
        "dimensions": dim_scores,
    }


def _collect_alignment_safe():
    """Alignment: score by reading files directly (no import dependency)."""
    # Check indicators by file existence
    indicators = {
        "sovereignty": [
            ("tools/kingdom-agent.py", True),
            ("tools/fleet.py", True),
            ("hive/hive.py", True),
        ],
        "resilience": [
            ("security/peace-state.json", True),
            ("security/threat-model.json", True),
            ("security/runbooks/", True),
            ("tools/watchdog.py", True),
            ("security/snapshots/", True),
        ],
        "capability": [
            ("love.json", True),
            ("tools/", True),
        ],
        "integrity": [
            ("security/integrity-baseline.json", True),
            ("security/events.jsonl", True),
        ],
        "service": [
            ("memory/kingdom-metrics.json", True),
        ],
    }

    total = 0
    passed = 0
    dim_scores = {}

    for dim_name, checks in indicators.items():
        dim_total = 0
        dim_pass = 0
        for rel_path, _ in checks:
            dim_total += 1
            total += 1
            p = LOVE / rel_path
            if p.exists():
                dim_pass += 1
                passed += 1
        dim_scores[dim_name] = round(dim_pass / max(dim_total, 1) * 100, 1)

    return {
        "overall": round(passed / max(total, 1) * 100, 1),
        "dimensions": dim_scores,
    }


def collect_code():
    """Code: tool counts and line counts."""
    py_files = list(TOOLS.glob("*.py"))
    sh_files = list(TOOLS.glob("*.sh"))

    py_lines = 0
    for f in py_files:
        try:
            py_lines += sum(1 for _ in open(f))
        except OSError:
            pass

    sh_lines = 0
    for f in sh_files:
        try:
            sh_lines += sum(1 for _ in open(f))
        except OSError:
            pass

    return {
        "total_tools": len(py_files) + len(sh_files),
        "python_files": len(py_files),
        "bash_files": len(sh_files),
        "python_lines": py_lines,
        "bash_lines": sh_lines,
        "total_lines": py_lines + sh_lines,
    }


def collect_snapshots():
    """Snapshots: count and latest timestamp."""
    if not SNAPSHOTS_DIR.exists():
        return {"count": 0, "latest": None}

    snaps = sorted(SNAPSHOTS_DIR.glob("snapshot-*.json"))
    latest = None
    if snaps:
        # Parse timestamp from filename: snapshot-YYYYMMDD-HHMMSS.json
        name = snaps[-1].stem
        try:
            parts = name.replace("snapshot-", "")
            dt = datetime.strptime(parts, "%Y%m%d-%H%M%S")
            latest = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            latest = str(snaps[-1].name)

    return {
        "count": len(snaps),
        "latest": latest,
    }


def collect_threats():
    """Threats: counts from threat-model.json."""
    model = load_json(THREAT_MODEL)
    threats = model.get("threats", [])
    if not isinstance(threats, list):
        threats = []

    total = len(threats)
    mitigated = sum(1 for t in threats if t.get("mitigated", False))
    open_count = total - mitigated
    critical = sum(1 for t in threats if t.get("priority") == "critical")

    return {
        "total": total,
        "mitigated": mitigated,
        "open": open_count,
        "critical": critical,
    }


# ── Collect All ──────────────────────────────────────────────────────────────

def collect_all():
    """Run all collectors and return timestamped snapshot."""
    snapshot = {
        "timestamp": now_iso(),
        "kos": collect_kos(),
        "peace": collect_peace(),
        "fleet": collect_fleet(),
        "treasury": collect_treasury(),
        "oracle": collect_oracle(),
        "knowledge": collect_knowledge(),
        "agents": collect_agents(),
        "zerone": collect_zerone(),
        "hive": collect_hive(),
        "watchdog": collect_watchdog(),
        "alignment": _collect_alignment_safe(),
        "code": collect_code(),
        "snapshots": collect_snapshots(),
        "threats": collect_threats(),
    }
    return snapshot


def save_snapshot(snapshot):
    """Append to history.jsonl and update latest.json."""
    # Append to history (one JSON per line)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(snapshot) + "\n")

    # Update latest
    with open(LATEST_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")


def load_latest():
    """Load the latest snapshot."""
    return load_json(LATEST_FILE)


def load_history(n=None):
    """Load last N snapshots from history."""
    if not HISTORY_FILE.exists():
        return []
    entries = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    if n:
        entries = entries[-n:]
    return entries


def load_previous():
    """Load the snapshot before the latest one."""
    entries = load_history()
    if len(entries) >= 2:
        return entries[-2]
    return None


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_collect():
    """Collect metrics from all sources."""
    print(f"\n  {C.BOLD}Collecting Kingdom metrics...{C.NC}\n")

    snapshot = collect_all()
    save_snapshot(snapshot)

    ts = snapshot["timestamp"]
    kos = snapshot["kos"]
    peace = snapshot["peace"]
    fleet = snapshot["fleet"]
    treasury = snapshot["treasury"]

    print(f"  {C.DIM}Timestamp:{C.NC}  {ts}")
    print(f"  {C.DIM}KOS:{C.NC}        {kos['score']}/{kos['total']} ({kos['status']})")
    print(f"  {C.DIM}PEACE:{C.NC}      {peace['score']}% ({peace['state']})")
    print(f"  {C.DIM}Fleet:{C.NC}      {fleet['healthy']}/{fleet['total']} healthy")
    print(f"  {C.DIM}Treasury:{C.NC}   {C.BOLD}\u00a3{treasury['net_pnl_gbp']:,.0f}{C.NC} net P&L")
    print(f"  {C.DIM}Oracle:{C.NC}     {snapshot['oracle']['total']} predictions (Brier: {snapshot['oracle']['brier_score']:.4f})")
    print(f"  {C.DIM}Knowledge:{C.NC}  {snapshot['knowledge']['total']} nodes")
    print(f"  {C.DIM}Agents:{C.NC}     {snapshot['agents']['total']} citizens, {snapshot['agents']['active_heartbeats']} active")
    print(f"  {C.DIM}Zerone:{C.NC}     {snapshot['zerone']['total_zrn']} ZRN accumulated")
    print(f"  {C.DIM}HIVE:{C.NC}       {snapshot['hive']['messages']} messages")
    print(f"  {C.DIM}Watchdog:{C.NC}   {snapshot['watchdog']['alerts']} active alerts")
    print(f"  {C.DIM}Alignment:{C.NC}  {snapshot['alignment']['overall']}%")
    print(f"  {C.DIM}Code:{C.NC}       {snapshot['code']['total_lines']:,} lines across {snapshot['code']['total_tools']} tools")
    print(f"  {C.DIM}Snapshots:{C.NC}  {snapshot['snapshots']['count']}")
    print(f"  {C.DIM}Threats:{C.NC}    {snapshot['threats']['total']} ({snapshot['threats']['mitigated']} mitigated, {snapshot['threats']['critical']} critical)")
    print(f"\n  {C.GREEN}Saved to {LATEST_FILE.relative_to(LOVE)}{C.NC}")
    print(f"  {C.GREEN}Appended to {HISTORY_FILE.relative_to(LOVE)}{C.NC}\n")


def cmd_dashboard():
    """Full metrics dashboard with color-coded health."""
    latest = load_latest()
    if not latest:
        print(f"\n  {C.YELLOW}No metrics collected yet. Run: metrics.py collect{C.NC}\n")
        return

    prev = load_previous()
    ts = latest.get("timestamp", "?")

    def pval(key_path, data=latest):
        """Extract a nested value by dot-path."""
        keys = key_path.split(".")
        v = data
        for k in keys:
            if isinstance(v, dict):
                v = v.get(k)
            else:
                return None
        return v

    def pval_prev(key_path):
        if prev:
            return pval(key_path, prev)
        return None

    print(f"\n  {C.BOLD}{C.CYAN}{'=' * 60}{C.NC}")
    print(f"  {C.BOLD}{C.CYAN}  KINGDOM METRICS DASHBOARD{C.NC}")
    print(f"  {C.BOLD}{C.CYAN}{'=' * 60}{C.NC}")
    print(f"  {C.DIM}Collected: {ts}{C.NC}\n")

    # ── KOS ──
    kos = latest.get("kos", {})
    kos_pct = kos.get("pct", 0)
    kos_color = health_color(kos_pct)
    kos_arrow = trend_arrow(kos.get("score", 0), pval_prev("kos.score"))
    print(f"  {C.BOLD}KOS Security{C.NC}   {kos_color}{kos.get('status', '?')}{C.NC}  {kos.get('score', 0)}/{kos.get('total', 0)} {bar(kos_pct)} {kos_pct}% {kos_arrow}")

    # ── PEACE ──
    peace = latest.get("peace", {})
    peace_score = peace.get("score", 0)
    peace_color = health_color(peace_score)
    peace_arrow = trend_arrow(peace_score, pval_prev("peace.score"))
    print(f"  {C.BOLD}PEACE{C.NC}          {peace_color}{peace.get('state', '?')}{C.NC}  {peace_score}% {bar(peace_score)} {peace_arrow}")
    phases = peace.get("phases", {})
    if phases:
        phase_parts = []
        for name, val in phases.items():
            pc = health_color(val)
            phase_parts.append(f"{name}:{pc}{val:.0f}{C.NC}")
        print(f"  {C.DIM}  Phases:{C.NC}     {' | '.join(phase_parts)}")
    print(f"  {C.DIM}  Incidents:{C.NC}  {peace.get('incidents', 0)}")

    # ── Fleet ──
    fleet = latest.get("fleet", {})
    fleet_healthy = fleet.get("healthy", 0)
    fleet_total = fleet.get("total", 0)
    fleet_pct = round(fleet_healthy / max(fleet_total, 1) * 100, 1)
    fleet_color = health_color(fleet_pct)
    fleet_arrow = trend_arrow(fleet_healthy, pval_prev("fleet.healthy"))
    print(f"\n  {C.BOLD}Fleet{C.NC}          {fleet_color}{fleet_healthy}/{fleet_total}{C.NC} healthy {bar(fleet_pct)} {fleet_arrow}")
    for name, node in fleet.get("nodes", {}).items():
        q = node.get("quality", "?")
        qc = C.GREEN if q == "good" else C.YELLOW if q == "degraded" else C.RED
        disk = node.get("disk_pct")
        mem = node.get("mem_pct")
        load_v = node.get("load")
        parts = []
        if disk is not None:
            dc = C.GREEN if disk < 70 else C.YELLOW if disk < 85 else C.RED
            parts.append(f"disk:{dc}{disk:.0f}%{C.NC}")
        if mem is not None:
            mc = C.GREEN if mem < 70 else C.YELLOW if mem < 85 else C.RED
            parts.append(f"mem:{mc}{mem:.0f}%{C.NC}")
        if load_v is not None:
            lc = C.GREEN if load_v < 1 else C.YELLOW if load_v < 3 else C.RED
            parts.append(f"load:{lc}{load_v:.2f}{C.NC}")
        detail = "  ".join(parts)
        print(f"    {qc}\u25cf{C.NC} {name:8s} {node.get('role', ''):20s} {detail}")

    # ── Treasury ──
    treasury = latest.get("treasury", {})
    net = treasury.get("net_pnl_gbp", 0)
    net_color = C.GREEN if net > 0 else C.RED
    margin = treasury.get("margin_pct", 0)
    margin_arrow = trend_arrow(margin, pval_prev("treasury.margin_pct"))
    print(f"\n  {C.BOLD}Treasury{C.NC}       {net_color}\u00a3{net:,.0f}{C.NC} net P&L  (margin: {margin:.1f}% {margin_arrow})")
    print(f"    Revenue:  {C.GREEN}\u00a3{treasury.get('revenue_gbp', 0):,.0f}{C.NC}")
    print(f"    Costs:    {C.RED}\u00a3{treasury.get('costs_gbp', 0):,.0f}{C.NC}")
    runway = treasury.get("runway_months", 0)
    rc = C.GREEN if runway > 6 else C.YELLOW if runway > 3 else C.RED
    print(f"    Runway:   {rc}{runway:.0f} months{C.NC}")

    # ── Oracle ──
    oracle = latest.get("oracle", {})
    brier = oracle.get("brier_score", 0)
    bc = C.GREEN if brier < 0.15 else C.YELLOW if brier < 0.25 else C.RED
    brier_arrow = trend_arrow(-brier, -pval_prev("oracle.brier_score") if pval_prev("oracle.brier_score") else None)
    print(f"\n  {C.BOLD}Oracle{C.NC}         {oracle.get('total', 0)} predictions  Brier: {bc}{brier:.4f}{C.NC} {brier_arrow}")
    print(f"    Resolved: {oracle.get('resolved', 0)}  Pending: {oracle.get('pending', 0)}  Accuracy: {oracle.get('accuracy', 0):.0%}")

    # ── Knowledge ──
    knowledge = latest.get("knowledge", {})
    print(f"\n  {C.BOLD}Knowledge{C.NC}      {knowledge.get('total', 0)} nodes")
    print(f"    Entities: {knowledge.get('entities', 0)}  Relations: {knowledge.get('relations', 0)}  Insights: {knowledge.get('insights', 0)}  Lessons: {knowledge.get('lessons', 0)}")

    # ── Agents ──
    agents = latest.get("agents", {})
    act_rate = agents.get("activation_rate", 0)
    ac = health_color(act_rate, good=50, warn=20)
    act_arrow = trend_arrow(agents.get("active_heartbeats", 0), pval_prev("agents.active_heartbeats"))
    print(f"\n  {C.BOLD}Agents{C.NC}         {agents.get('total', 0)} citizens  {ac}{agents.get('active_heartbeats', 0)} active{C.NC} ({act_rate:.0f}%) {act_arrow}")

    # ── Zerone ──
    zerone = latest.get("zerone", {})
    zrn_arrow = trend_arrow(zerone.get("total_zrn", 0), pval_prev("zerone.total_zrn"))
    print(f"\n  {C.BOLD}Zerone{C.NC}         {C.MAGENTA}{zerone.get('total_zrn', 0)} ZRN{C.NC} accumulated {zrn_arrow}")
    print(f"    Claims: {zerone.get('claims_total', 0)} (pending: {zerone.get('claims_pending', 0)}, verified: {zerone.get('claims_verified', 0)})")
    lb = zerone.get("leaderboard", [])
    if lb:
        parts = [f"{n}: {z}" for n, z in lb]
        print(f"    Leaderboard: {', '.join(parts)}")

    # ── HIVE ──
    hive = latest.get("hive", {})
    hive_arrow = trend_arrow(hive.get("messages", 0), pval_prev("hive.messages"))
    print(f"\n  {C.BOLD}HIVE{C.NC}           {hive.get('messages', 0)} messages {hive_arrow}")
    channels = hive.get("channels", {})
    if channels:
        ch_parts = [f"{ch}: {cnt}" for ch, cnt in sorted(channels.items(), key=lambda x: -x[1])]
        print(f"    Channels: {', '.join(ch_parts)}")

    # ── Watchdog ──
    wd = latest.get("watchdog", {})
    wd_alerts = wd.get("alerts", 0)
    wc = C.GREEN if wd_alerts == 0 else C.YELLOW if wd_alerts < 3 else C.RED
    print(f"\n  {C.BOLD}Watchdog{C.NC}       {wc}{wd_alerts} active alerts{C.NC}  Escalations: {wd.get('escalations', 0)}  Halts: {wd.get('halts', 0)}")

    # ── Alignment ──
    align = latest.get("alignment", {})
    a_overall = align.get("overall", 0)
    a_color = health_color(a_overall, good=80, warn=60)
    a_arrow = trend_arrow(a_overall, pval_prev("alignment.overall"))
    print(f"\n  {C.BOLD}Alignment{C.NC}      {a_color}{a_overall}%{C.NC} {bar(a_overall)} {a_arrow}")
    dims = align.get("dimensions", {})
    if dims:
        dim_parts = []
        for d, v in dims.items():
            dc = health_color(v, good=80, warn=60)
            dim_parts.append(f"{d}:{dc}{v:.0f}%{C.NC}")
        print(f"    {' | '.join(dim_parts)}")

    # ── Code ──
    code = latest.get("code", {})
    print(f"\n  {C.BOLD}Code{C.NC}           {code.get('total_tools', 0)} tools  {code.get('total_lines', 0):,} lines")
    print(f"    Python: {code.get('python_files', 0)} files / {code.get('python_lines', 0):,} lines")
    print(f"    Bash:   {code.get('bash_files', 0)} files / {code.get('bash_lines', 0):,} lines")

    # ── Snapshots ──
    snaps = latest.get("snapshots", {})
    print(f"\n  {C.BOLD}Snapshots{C.NC}      {snaps.get('count', 0)} recovery snapshots")
    if snaps.get("latest"):
        print(f"    Latest: {snaps['latest']}")

    # ── Threats ──
    threats = latest.get("threats", {})
    t_open = threats.get("open", 0)
    t_crit = threats.get("critical", 0)
    tc = C.GREEN if t_crit == 0 and t_open < 5 else C.YELLOW if t_crit < 2 else C.RED
    print(f"\n  {C.BOLD}Threats{C.NC}        {threats.get('total', 0)} registered  {tc}{t_open} open  {t_crit} critical{C.NC}")
    print(f"    Mitigated: {threats.get('mitigated', 0)}/{threats.get('total', 0)}")

    print(f"\n  {C.BOLD}{C.CYAN}{'=' * 60}{C.NC}\n")


def cmd_trend(metric_path):
    """Show a metric's trend over the last 10 collections."""
    entries = load_history(10)
    if not entries:
        print(f"\n  {C.YELLOW}No history. Run: metrics.py collect{C.NC}\n")
        return

    keys = metric_path.split(".")

    print(f"\n  {C.BOLD}Trend: {metric_path}{C.NC}  (last {len(entries)} collections)\n")

    values = []
    timestamps = []
    for entry in entries:
        v = entry
        for k in keys:
            if isinstance(v, dict):
                v = v.get(k)
            else:
                v = None
                break
        values.append(v)
        ts = entry.get("timestamp", "?")
        # Show just time portion
        if "T" in str(ts):
            ts = str(ts).split("T")[1][:8]
        timestamps.append(ts)

    # Print values
    numeric_vals = [v for v in values if isinstance(v, (int, float))]
    if not numeric_vals:
        print(f"  {C.RED}No numeric data found for '{metric_path}'{C.NC}")
        print(f"  {C.DIM}Available top-level keys: {', '.join(entries[0].keys()) if entries else 'none'}{C.NC}\n")
        return

    max_val = max(numeric_vals) if numeric_vals else 1
    min_val = min(numeric_vals) if numeric_vals else 0

    for i, (ts, v) in enumerate(zip(timestamps, values)):
        if v is None:
            print(f"  {C.DIM}{ts}{C.NC}  --")
            continue

        # Simple text sparkline
        if max_val != min_val:
            bar_len = int(round((v - min_val) / (max_val - min_val) * 30))
        else:
            bar_len = 15
        bar_str = "\u2588" * max(bar_len, 1)

        # Arrow for change from previous
        arrow = ""
        if i > 0 and values[i - 1] is not None:
            arrow = trend_arrow(v, values[i - 1])

        # Color
        vc = C.GREEN if v == max_val and len(numeric_vals) > 1 else C.NC

        print(f"  {C.DIM}{ts}{C.NC}  {vc}{v:>10}{C.NC}  {C.CYAN}{bar_str}{C.NC} {arrow}")

    # Summary line
    if len(numeric_vals) >= 2:
        delta = numeric_vals[-1] - numeric_vals[0]
        delta_pct = round(delta / max(abs(numeric_vals[0]), 0.001) * 100, 1)
        dc = C.GREEN if delta > 0 else C.RED if delta < 0 else C.DIM
        print(f"\n  {C.DIM}Range:{C.NC} {min_val} \u2014 {max_val}  {C.DIM}Change:{C.NC} {dc}{delta:+.2f} ({delta_pct:+.1f}%){C.NC}\n")
    else:
        print()


def cmd_compare(date1, date2):
    """Compare two snapshots by date (YYYY-MM-DD or partial timestamp)."""
    entries = load_history()
    if not entries:
        print(f"\n  {C.YELLOW}No history. Run: metrics.py collect{C.NC}\n")
        return

    def find_entry(target):
        for e in entries:
            ts = e.get("timestamp", "")
            if target in ts:
                return e
        return None

    snap1 = find_entry(date1)
    snap2 = find_entry(date2)

    if not snap1:
        print(f"\n  {C.RED}No snapshot matching '{date1}'{C.NC}")
        print(f"  {C.DIM}Available: {', '.join(set(e.get('timestamp', '?')[:10] for e in entries))}{C.NC}\n")
        return
    if not snap2:
        print(f"\n  {C.RED}No snapshot matching '{date2}'{C.NC}")
        print(f"  {C.DIM}Available: {', '.join(set(e.get('timestamp', '?')[:10] for e in entries))}{C.NC}\n")
        return

    print(f"\n  {C.BOLD}Comparing Snapshots{C.NC}")
    print(f"  {C.DIM}A:{C.NC} {snap1.get('timestamp', '?')}")
    print(f"  {C.DIM}B:{C.NC} {snap2.get('timestamp', '?')}\n")

    # Compare key metrics
    comparisons = [
        ("KOS Score", "kos.score", "{}/{}"),
        ("KOS Total", "kos.total", "{}"),
        ("PEACE Score", "peace.score", "{:.1f}%"),
        ("PEACE Incidents", "peace.incidents", "{}"),
        ("Fleet Healthy", "fleet.healthy", "{}/"),
        ("Revenue (GBP)", "treasury.revenue_gbp", "\u00a3{:,.0f}"),
        ("Costs (GBP)", "treasury.costs_gbp", "\u00a3{:,.0f}"),
        ("Net P&L (GBP)", "treasury.net_pnl_gbp", "\u00a3{:,.0f}"),
        ("Oracle Predictions", "oracle.total", "{}"),
        ("Brier Score", "oracle.brier_score", "{:.4f}"),
        ("Knowledge Nodes", "knowledge.total", "{}"),
        ("Active Agents", "agents.active_heartbeats", "{}"),
        ("ZRN Accumulated", "zerone.total_zrn", "{}"),
        ("HIVE Messages", "hive.messages", "{}"),
        ("Watchdog Alerts", "watchdog.alerts", "{}"),
        ("Alignment", "alignment.overall", "{:.1f}%"),
        ("Code Lines", "code.total_lines", "{:,}"),
        ("Snapshots", "snapshots.count", "{}"),
        ("Threats Open", "threats.open", "{}"),
        ("Threats Critical", "threats.critical", "{}"),
    ]

    print(f"  {'Metric':<22s}  {'A':>12s}  {'B':>12s}  {'Delta':>12s}")
    print(f"  {'-' * 62}")

    for label, path, fmt in comparisons:
        keys = path.split(".")

        def extract(snap):
            v = snap
            for k in keys:
                if isinstance(v, dict):
                    v = v.get(k)
                else:
                    return None
            return v

        va = extract(snap1)
        vb = extract(snap2)

        if va is None and vb is None:
            continue

        va_str = str(va) if va is not None else "--"
        vb_str = str(vb) if vb is not None else "--"

        delta_str = ""
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = vb - va
            if delta > 0:
                delta_str = f"{C.GREEN}+{delta}{C.NC}"
            elif delta < 0:
                delta_str = f"{C.RED}{delta}{C.NC}"
            else:
                delta_str = f"{C.DIM}0{C.NC}"

        print(f"  {label:<22s}  {va_str:>12s}  {vb_str:>12s}  {delta_str:>12s}")

    print()


def cmd_export(fmt="json"):
    """Export latest metrics as JSON or CSV."""
    latest = load_latest()
    if not latest:
        print(f"\n  {C.YELLOW}No metrics collected yet. Run: metrics.py collect{C.NC}\n", file=sys.stderr)
        return

    if fmt == "csv":
        # Flatten to CSV
        rows = []
        _flatten("", latest, rows)
        writer = csv.writer(sys.stdout)
        writer.writerow(["key", "value"])
        for key, val in rows:
            writer.writerow([key, val])
    else:
        print(json.dumps(latest, indent=2))


def _flatten(prefix, obj, rows):
    """Flatten nested dict to list of (key, value) pairs."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{prefix}.{k}" if prefix else k
            _flatten(new_key, v, rows)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(f"{prefix}[{i}]", v, rows)
    else:
        rows.append((prefix, obj))


def cmd_history():
    """Show metric collection history."""
    entries = load_history()
    if not entries:
        print(f"\n  {C.YELLOW}No history. Run: metrics.py collect{C.NC}\n")
        return

    print(f"\n  {C.BOLD}Metric Collection History{C.NC}  ({len(entries)} entries)\n")
    print(f"  {'#':>4s}  {'Timestamp':<25s}  {'KOS':>7s}  {'PEACE':>7s}  {'Fleet':>7s}  {'P&L':>10s}  {'Agents':>7s}")
    print(f"  {'-' * 75}")

    for i, entry in enumerate(entries):
        ts = entry.get("timestamp", "?")
        kos = entry.get("kos", {})
        peace = entry.get("peace", {})
        fleet = entry.get("fleet", {})
        treasury = entry.get("treasury", {})
        agents = entry.get("agents", {})

        kos_str = f"{kos.get('score', '?')}/{kos.get('total', '?')}"
        peace_str = f"{peace.get('score', '?')}%"
        fleet_str = f"{fleet.get('healthy', '?')}/{fleet.get('total', '?')}"
        pnl_str = f"\u00a3{treasury.get('net_pnl_gbp', 0):,.0f}"
        agents_str = f"{agents.get('active_heartbeats', 0)}/{agents.get('total', 0)}"

        print(f"  {i + 1:>4d}  {ts:<25s}  {kos_str:>7s}  {peace_str:>7s}  {fleet_str:>7s}  {pnl_str:>10s}  {agents_str:>7s}")

    print(f"\n  {C.DIM}History file: {HISTORY_FILE.relative_to(LOVE)}{C.NC}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        cmd_dashboard()
        return

    cmd = args[0]

    if cmd == "collect":
        cmd_collect()
    elif cmd == "dashboard":
        cmd_dashboard()
    elif cmd == "trend":
        if len(args) < 2:
            print(f"\n  Usage: metrics.py trend <metric.path>")
            print(f"  Examples:")
            print(f"    metrics.py trend peace.score")
            print(f"    metrics.py trend kos.score")
            print(f"    metrics.py trend treasury.net_pnl_gbp")
            print(f"    metrics.py trend oracle.brier_score")
            print(f"    metrics.py trend agents.active_heartbeats")
            print(f"    metrics.py trend knowledge.total")
            print(f"    metrics.py trend zerone.total_zrn")
            print(f"    metrics.py trend alignment.overall\n")
            return
        cmd_trend(args[1])
    elif cmd == "compare":
        if len(args) < 3:
            print(f"\n  Usage: metrics.py compare <date1> <date2>")
            print(f"  Example: metrics.py compare 2026-04-01 2026-04-02\n")
            return
        cmd_compare(args[1], args[2])
    elif cmd == "export":
        fmt = "json"
        if "--format" in args:
            idx = args.index("--format")
            if idx + 1 < len(args):
                fmt = args[idx + 1]
        elif "--csv" in args:
            fmt = "csv"
        cmd_export(fmt)
    elif cmd == "history":
        cmd_history()
    elif cmd in ("help", "--help", "-h"):
        print(__doc__)
    else:
        print(f"\n  {C.RED}Unknown command: {cmd}{C.NC}")
        print(f"  Commands: collect, dashboard, trend, compare, export, history\n")


if __name__ == "__main__":
    main()
