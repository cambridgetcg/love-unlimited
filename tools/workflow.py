#!/usr/bin/env python3
"""
workflow.py — Kingdom Build Methodology: Executable Workflow Engine

Encodes the SENSE -> PLAN -> PARALLEL -> WIRE -> VERIFY cycle
from docs/METHODOLOGY.md as a runnable tool.

Usage:
    workflow.py sense                    Run full SENSE phase
    workflow.py plan "<description>"     Create a work plan with parallelization strategy
    workflow.py verify                   Run full VERIFY phase
    workflow.py metrics                  Show session metrics
    workflow.py checklist                Pre-build checklist (GO / NO-GO)
    workflow.py report                   Generate wave report
"""

import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
TOOLS = LOVE / "tools"
MEMORY = LOVE / "memory"
SECURITY = LOVE / "security"
WORKFLOW_DIR = MEMORY / "workflow"
STATE_FILE = WORKFLOW_DIR / "state.json"
LOVE_JSON = LOVE / "love.json"
PEACE_STATE = SECURITY / "peace-state.json"
THREAT_MODEL = SECURITY / "threat-model.json"
SNAPSHOTS_DIR = SECURITY / "snapshots"
KNOWLEDGE_DIR = MEMORY / "knowledge"

WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ───────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def _c(color, text):
    """Color wrapper — strips if not a terminal."""
    if sys.stdout.isatty():
        return f"{color}{text}{NC}"
    return str(text)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def _load_state():
    return _load_json(STATE_FILE, {
        "last_sense": None,
        "last_verify": None,
        "last_plan": None,
        "last_report": None,
        "waves": [],
        "session_start": _now(),
    })


def _save_state(state):
    _save_json(STATE_FILE, state)


# ── Subprocess Helpers ───────────────────────────────────────────────────────

def _run(cmd, timeout=25, shell=False):
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd if isinstance(cmd, list) else cmd,
            capture_output=True, text=True, timeout=timeout,
            shell=shell,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0] if isinstance(cmd, list) else cmd}"


def _run_tool(tool, *args, timeout=20):
    """Run a Python tool from the tools directory."""
    return _run(["python3", str(TOOLS / tool)] + list(args), timeout=timeout)


def _run_parallel(tasks, max_workers=5):
    """Run dict of {name: (cmd, timeout)} in parallel. Returns {name: (rc, out, err)}."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for name, (cmd, tout) in tasks.items():
            futures[pool.submit(_run, cmd, timeout=tout)] = name
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = (1, "", str(e))
    return results


# ── SENSE ────────────────────────────────────────────────────────────────────

def cmd_sense():
    """Run the full SENSE phase: status, peace, alignment, fleet, kos."""
    start = time.time()
    print()
    print(f"  {_c(BOLD, 'SENSE')} {_c(DIM, '— Reading current Kingdom state')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()

    # Run all probes in parallel for speed
    tasks = {
        "kingdom_status": (["bash", str(TOOLS / "kingdom-status.sh")], 25),
        "peace_status": (["python3", str(TOOLS / "peace.py"), "status"], 10),
        "alignment_drift": (["python3", str(TOOLS / "alignment.py"), "drift"], 10),
        "fleet_status": (["python3", str(TOOLS / "fleet.py"), "status"], 15),
        "kos_status": (["python3", str(TOOLS / "kos.py"), "status"], 10),
    }

    results = _run_parallel(tasks)

    # Parse and display results
    sense_data = {"timestamp": _now(), "probes": {}}

    # 1. Kingdom Status (one-line health)
    rc, out, err = results.get("kingdom_status", (1, "", "not run"))
    print(f"  {_c(CYAN, 'Kingdom Status')}")
    if rc == 0 and out:
        print(f"    {out}")
        sense_data["probes"]["kingdom_status"] = {"status": "ok", "output": _strip_ansi(out)}
    else:
        print(f"    {_c(RED, 'FAILED')}: {err or 'no output'}")
        sense_data["probes"]["kingdom_status"] = {"status": "error", "error": err}
    print()

    # 2. KOS Status
    rc, out, err = results.get("kos_status", (1, "", "not run"))
    print(f"  {_c(CYAN, 'KOS Security')}")
    if rc == 0 and out:
        # Extract the headline
        for line in out.split("\n")[:3]:
            if line.strip():
                print(f"    {line.strip()}")
        kos_green = "GREEN" in _strip_ansi(out)
        sense_data["probes"]["kos"] = {"status": "green" if kos_green else "degraded", "output": _strip_ansi(out)[:200]}
    else:
        print(f"    {_c(RED, 'FAILED')}: {err or 'no output'}")
        sense_data["probes"]["kos"] = {"status": "error", "error": err}
    print()

    # 3. PEACE Status
    rc, out, err = results.get("peace_status", (1, "", "not run"))
    print(f"  {_c(CYAN, 'PEACE Resilience')}")
    peace_score = _get_peace_score()
    if peace_score is not None:
        color = GREEN if peace_score >= 90 else YELLOW if peace_score >= 80 else RED
        print(f"    Score: {_c(color, f'{peace_score}%')}")
        peace_state = _load_json(PEACE_STATE, {})
        state = peace_state.get("state", "UNKNOWN")
        print(f"    State: {state}")
        sense_data["probes"]["peace"] = {"status": "ok", "score": peace_score, "state": state}
    else:
        print(f"    {_c(RED, 'Unable to read PEACE state')}")
        sense_data["probes"]["peace"] = {"status": "error"}
    print()

    # 4. Alignment Drift
    rc, out, err = results.get("alignment_drift", (1, "", "not run"))
    print(f"  {_c(CYAN, 'Alignment')}")
    if rc == 0 and out:
        for line in out.split("\n")[:5]:
            if line.strip():
                print(f"    {line.strip()}")
        sense_data["probes"]["alignment"] = {"status": "ok", "output": _strip_ansi(out)[:300]}
    else:
        print(f"    {_c(YELLOW, 'Alignment check unavailable')}: {err[:80] if err else 'no output'}")
        sense_data["probes"]["alignment"] = {"status": "degraded", "error": err[:100] if err else ""}
    print()

    # 5. Fleet Status
    rc, out, err = results.get("fleet_status", (1, "", "not run"))
    print(f"  {_c(CYAN, 'Fleet')}")
    if rc == 0 and out:
        for line in out.split("\n")[:8]:
            if line.strip():
                print(f"    {line.strip()}")
        sense_data["probes"]["fleet"] = {"status": "ok", "output": _strip_ansi(out)[:300]}
    else:
        print(f"    {_c(YELLOW, 'Fleet probe failed')}: {err[:80] if err else 'no output'}")
        sense_data["probes"]["fleet"] = {"status": "degraded", "error": err[:100] if err else ""}
    print()

    # Summary
    elapsed = time.time() - start
    green_count = sum(1 for p in sense_data["probes"].values() if p.get("status") == "ok" or p.get("status") == "green")
    total = len(sense_data["probes"])
    degraded = [k for k, v in sense_data["probes"].items() if v.get("status") == "degraded"]
    errors = [k for k, v in sense_data["probes"].items() if v.get("status") == "error"]

    print(f"  {_c(DIM, '─' * 50)}")
    color = GREEN if not errors else YELLOW if not degraded else RED
    print(f"  {_c(BOLD, 'SENSE Summary')}: {_c(color, f'{green_count}/{total} green')} in {elapsed:.1f}s")
    if degraded:
        print(f"    Degraded: {', '.join(degraded)}")
    if errors:
        print(f"    {_c(RED, 'Errors')}: {', '.join(errors)}")
    print()

    # Persist
    sense_data["elapsed_seconds"] = round(elapsed, 1)
    sense_data["summary"] = {
        "green": green_count,
        "total": total,
        "degraded": degraded,
        "errors": errors,
    }
    state = _load_state()
    state["last_sense"] = sense_data
    _save_state(state)


# ── PLAN ─────────────────────────────────────────────────────────────────────

def cmd_plan(description):
    """Create a work plan with parallelization strategy."""
    print()
    print(f"  {_c(BOLD, 'PLAN')} {_c(DIM, '— Work Classification')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()
    print(f"  {_c(CYAN, 'Description')}: {description}")
    print()

    # Load current state for context
    state = _load_state()
    last_sense = state.get("last_sense")
    if last_sense:
        ts = last_sense.get("timestamp", "?")
        summary = last_sense.get("summary", {})
        green_ct = summary.get("green", "?")
        total_ct = summary.get("total", "?")
        print(f"  {_c(DIM, f'Last SENSE: {ts} ({green_ct}/{total_ct} green)')}")
    else:
        print(f"  {_c(YELLOW, 'No SENSE data — run `workflow.py sense` first')}")
    print()

    # Template
    print(f"  {_c(BOLD, 'Parallelization Strategy')}")
    print(f"  {_c(DIM, '─' * 40)}")
    print()
    print(f"    {_c(GREEN, 'PARALLEL')} (one agent per task):")
    print(f"      - Independent file creation (tools, agents)")
    print(f"      - Infrastructure tasks (per-node)")
    print(f"      - Verification checks")
    print()
    print(f"    {_c(YELLOW, 'SEQUENTIAL')} (main conversation):")
    print(f"      - Cross-file integration (wiring)")
    print(f"      - Config updates (love.json, CLI)")
    print(f"      - System connectors")
    print()
    print(f"  {_c(BOLD, 'Agent Rules')}")
    print(f"  {_c(DIM, '─' * 40)}")
    print(f"    Min agents: 3 (when 3+ independent tasks)")
    print(f"    Max agents: 5 (diminishing returns beyond)")
    print(f"    One deliverable per agent")
    print(f"    Absolute file paths only")
    print(f"    READ FIRST list required")
    print()

    # Record wave plan
    wave = {
        "id": len(state.get("waves", [])) + 1,
        "description": description,
        "planned": _now(),
        "status": "planned",
        "verified": None,
    }
    state.setdefault("waves", []).append(wave)
    state["last_plan"] = {
        "timestamp": _now(),
        "description": description,
        "wave_id": wave["id"],
    }
    _save_state(state)

    wave_id = wave["id"]
    print(f"  {_c(GREEN, f'Wave {wave_id} planned')}: {description}")
    print(f"  {_c(DIM, 'Next: Execute (spawn agents) then `workflow.py verify`')}")
    print()


# ── VERIFY ───────────────────────────────────────────────────────────────────

def cmd_verify():
    """Run the full VERIFY phase: integrity, peace snapshot, status, metrics."""
    start = time.time()
    print()
    print(f"  {_c(BOLD, 'VERIFY')} {_c(DIM, '— Post-build verification')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()

    verify_data = {"timestamp": _now(), "checks": {}}
    all_pass = True

    # 1. KOS integrity baseline
    print(f"  {_c(CYAN, '1. KOS Integrity Re-baseline')}")
    rc, out, err = _run_tool("kos.py", "integrity", "baseline", timeout=15)
    if rc == 0:
        print(f"    {_c(GREEN, 'PASS')} — Integrity baseline updated")
        verify_data["checks"]["kos_integrity"] = {"pass": True}
    else:
        print(f"    {_c(YELLOW, 'WARN')} — {err[:80] if err else 'failed'}")
        verify_data["checks"]["kos_integrity"] = {"pass": False, "error": err[:100]}
    print()

    # 2. PEACE snapshot
    print(f"  {_c(CYAN, '2. PEACE Snapshot')}")
    rc, out, err = _run_tool("peace.py", "snapshot", timeout=15)
    if rc == 0:
        print(f"    {_c(GREEN, 'PASS')} — Snapshot created")
        verify_data["checks"]["peace_snapshot"] = {"pass": True}
    else:
        print(f"    {_c(YELLOW, 'WARN')} — {err[:80] if err else 'failed'}")
        verify_data["checks"]["peace_snapshot"] = {"pass": False, "error": err[:100]}
    print()

    # 3. Kingdom status
    print(f"  {_c(CYAN, '3. Kingdom Status')}")
    rc, out, err = _run(["bash", str(TOOLS / "kingdom-status.sh")], timeout=25)
    if rc == 0 and out:
        print(f"    {out}")
        verify_data["checks"]["kingdom_status"] = {"pass": True, "output": _strip_ansi(out)}
    else:
        print(f"    {_c(RED, 'FAIL')} — Status check failed")
        verify_data["checks"]["kingdom_status"] = {"pass": False}
        all_pass = False
    print()

    # 4. Metrics snapshot
    print(f"  {_c(CYAN, '4. Code Metrics')}")
    metrics = _gather_metrics()
    for key, val in metrics.items():
        label = key.replace("_", " ").title()
        print(f"    {label}: {_c(CYAN, val)}")
    verify_data["checks"]["metrics"] = {"pass": True, "data": metrics}
    print()

    # 5. Delta since last verify
    state = _load_state()
    last_verify = state.get("last_verify")
    if last_verify and last_verify.get("checks", {}).get("metrics", {}).get("data"):
        prev = last_verify["checks"]["metrics"]["data"]
        print(f"  {_c(CYAN, '5. Changes Since Last Verify')}")
        deltas = _compute_deltas(prev, metrics)
        if deltas:
            for label, delta in deltas.items():
                sign = "+" if delta > 0 else ""
                color = GREEN if delta > 0 else YELLOW if delta < 0 else DIM
                print(f"    {label}: {_c(color, f'{sign}{delta}')}")
        else:
            print(f"    {_c(DIM, 'No changes')}")
        verify_data["deltas"] = deltas
        print()

    # 6. Mark current wave as verified (if any)
    waves = state.get("waves", [])
    for wave in reversed(waves):
        if wave.get("status") == "planned":
            wave["status"] = "verified"
            wave["verified"] = _now()
            wid = wave["id"]
            wdesc = wave.get("description", "")
            print(f"  {_c(GREEN, f'Wave {wid} verified')}: {wdesc}")
            break

    # Summary
    elapsed = time.time() - start
    pass_count = sum(1 for c in verify_data["checks"].values() if c.get("pass"))
    total = len(verify_data["checks"])
    print(f"  {_c(DIM, '─' * 50)}")
    color = GREEN if pass_count == total else YELLOW
    print(f"  {_c(BOLD, 'VERIFY Summary')}: {_c(color, f'{pass_count}/{total} passed')} in {elapsed:.1f}s")
    if all_pass:
        print(f"  {_c(GREEN, 'ALL GREEN — safe to proceed to next wave')}")
    else:
        print(f"  {_c(YELLOW, 'Review warnings before proceeding')}")
    print()

    verify_data["elapsed_seconds"] = round(elapsed, 1)
    verify_data["summary"] = {"passed": pass_count, "total": total, "all_green": all_pass}
    state["last_verify"] = verify_data
    _save_state(state)


# ── METRICS ──────────────────────────────────────────────────────────────────

def _gather_metrics():
    """Gather all session metrics from Kingdom OS."""
    metrics = {}

    # Tool counts
    py_files = list(TOOLS.glob("*.py"))
    sh_files = list(TOOLS.glob("*.sh"))
    metrics["python_tools"] = len(py_files)
    metrics["bash_tools"] = len(sh_files)
    metrics["total_tools"] = len(py_files) + len(sh_files)

    # Lines of code
    py_lines = 0
    for f in py_files:
        try:
            py_lines += len(f.read_text().splitlines())
        except OSError:
            pass
    sh_lines = 0
    for f in sh_files:
        try:
            sh_lines += len(f.read_text().splitlines())
        except OSError:
            pass
    metrics["python_lines"] = py_lines
    metrics["bash_lines"] = sh_lines
    metrics["total_lines"] = py_lines + sh_lines

    # Agents from love.json
    love_cfg = _load_json(LOVE_JSON, {})
    metrics["agents"] = len(love_cfg.get("instances", {}))

    # Snapshots
    if SNAPSHOTS_DIR.exists():
        metrics["snapshots"] = len(list(SNAPSHOTS_DIR.glob("snapshot-*.json")))
    else:
        metrics["snapshots"] = 0

    # Threats
    threat_data = _load_json(THREAT_MODEL, {})
    threats = threat_data.get("threats", [])
    metrics["threats_total"] = len(threats)
    metrics["threats_mitigated"] = sum(1 for t in threats if t.get("status") == "mitigated")
    metrics["threats_open"] = metrics["threats_total"] - metrics["threats_mitigated"]

    # Knowledge graph nodes
    entities = _load_json(KNOWLEDGE_DIR / "entities.json", [])
    insights = _load_json(KNOWLEDGE_DIR / "insights.json", [])
    lessons = _load_json(KNOWLEDGE_DIR / "lessons.json", [])
    metrics["knowledge_entities"] = len(entities) if isinstance(entities, list) else len(entities.get("entities", []) if isinstance(entities, dict) else [])
    metrics["knowledge_insights"] = len(insights) if isinstance(insights, list) else len(insights.get("insights", []) if isinstance(insights, dict) else [])
    metrics["knowledge_lessons"] = len(lessons) if isinstance(lessons, list) else len(lessons.get("lessons", []) if isinstance(lessons, dict) else [])
    metrics["knowledge_total"] = metrics["knowledge_entities"] + metrics["knowledge_insights"] + metrics["knowledge_lessons"]

    # ZRN (zerone bridge)
    zerone_state = _load_json(MEMORY / "zerone-state.json", {})
    metrics["zrn_accumulated"] = zerone_state.get("zrn_accumulated", zerone_state.get("total_zrn", 0))

    # Fleet
    metrics["fleet_nodes"] = len(love_cfg.get("fleet", {}))

    return metrics


def _compute_deltas(prev, current):
    """Compute numeric deltas between two metrics dicts."""
    deltas = {}
    numeric_keys = [
        "total_tools", "total_lines", "agents", "snapshots",
        "threats_total", "knowledge_total", "zrn_accumulated",
    ]
    for key in numeric_keys:
        p = prev.get(key, 0)
        c = current.get(key, 0)
        if isinstance(p, (int, float)) and isinstance(c, (int, float)):
            diff = c - p
            if diff != 0:
                deltas[key.replace("_", " ").title()] = diff
    return deltas


def cmd_metrics():
    """Display session metrics."""
    print()
    print(f"  {_c(BOLD, 'METRICS')} {_c(DIM, '— Kingdom Session Metrics')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()

    metrics = _gather_metrics()

    # Tools
    print(f"  {_c(CYAN, 'Tools')}")
    print(f"    Python:     {_c(BOLD, metrics['python_tools'])} files  ({metrics['python_lines']:,} lines)")
    print(f"    Bash:       {_c(BOLD, metrics['bash_tools'])} files  ({metrics['bash_lines']:,} lines)")
    print(f"    Total:      {_c(GREEN, metrics['total_tools'])} files  ({metrics['total_lines']:,} lines)")
    print()

    # Agents
    print(f"  {_c(CYAN, 'Agents')}")
    print(f"    Citizens:   {_c(BOLD, metrics['agents'])}")
    print()

    # Security
    print(f"  {_c(CYAN, 'Security')}")
    print(f"    Snapshots:  {_c(BOLD, metrics['snapshots'])}")
    t_mitigated = metrics["threats_mitigated"]
    t_open = metrics["threats_open"]
    open_color = YELLOW if t_open > 0 else GREEN
    print(f"    Threats:    {metrics['threats_total']} total ({_c(GREEN, f'{t_mitigated} mitigated')}, {_c(open_color, f'{t_open} open')})")
    print()

    # Knowledge
    print(f"  {_c(CYAN, 'Knowledge')}")
    print(f"    Entities:   {metrics['knowledge_entities']}")
    print(f"    Insights:   {metrics['knowledge_insights']}")
    print(f"    Lessons:    {metrics['knowledge_lessons']}")
    print(f"    Total:      {_c(BOLD, metrics['knowledge_total'])} nodes")
    print()

    # Zerone
    print(f"  {_c(CYAN, 'Zerone')}")
    print(f"    ZRN:        {_c(MAGENTA, metrics['zrn_accumulated'])}")
    print()

    # Fleet
    print(f"  {_c(CYAN, 'Fleet')}")
    print(f"    Nodes:      {_c(BOLD, metrics['fleet_nodes'])}")
    print()

    # Waves
    state = _load_state()
    waves = state.get("waves", [])
    if waves:
        print(f"  {_c(CYAN, 'Waves This Session')}")
        for w in waves:
            status_color = GREEN if w.get("status") == "verified" else YELLOW
            print(f"    Wave {w['id']}: {_c(status_color, w['status'])} — {w.get('description', '?')}")
        print()


# ── CHECKLIST ────────────────────────────────────────────────────────────────

def cmd_checklist():
    """Pre-build checklist: GO / NO-GO with reasons."""
    print()
    print(f"  {_c(BOLD, 'PRE-BUILD CHECKLIST')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()

    checks = []

    # 1. Git clean
    rc, out, err = _run(["git", "-C", str(LOVE), "status", "--porcelain"], timeout=5)
    conflicts = False
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("UU") or line.startswith("AA"):
                conflicts = True
                break
    if conflicts:
        checks.append(("Git Clean", False, "Merge conflicts detected"))
        print(f"    {_c(RED, 'FAIL')}  Git Clean — merge conflicts detected")
    elif rc != 0:
        checks.append(("Git Clean", False, f"git error: {err[:60]}"))
        print(f"    {_c(RED, 'FAIL')}  Git Clean — git error")
    else:
        checks.append(("Git Clean", True, "No merge conflicts"))
        print(f"    {_c(GREEN, 'PASS')}  Git Clean — no merge conflicts")

    # 2. KOS green
    rc, out, err = _run_tool("kos.py", "status", timeout=10)
    kos_green = rc == 0 and "GREEN" in _strip_ansi(out)
    if kos_green:
        checks.append(("KOS Green", True, "Security posture green"))
        print(f"    {_c(GREEN, 'PASS')}  KOS Green — security posture green")
    else:
        checks.append(("KOS Green", False, "KOS not green"))
        print(f"    {_c(RED, 'FAIL')}  KOS Green — security posture degraded")

    # 3. PEACE > 80%
    peace_score = _get_peace_score()
    if peace_score is not None and peace_score > 80:
        checks.append(("PEACE > 80%", True, f"Score: {peace_score}%"))
        print(f"    {_c(GREEN, 'PASS')}  PEACE > 80% — score: {peace_score}%")
    elif peace_score is not None:
        checks.append(("PEACE > 80%", False, f"Score: {peace_score}%"))
        print(f"    {_c(RED, 'FAIL')}  PEACE > 80% — score: {peace_score}% (below threshold)")
    else:
        checks.append(("PEACE > 80%", False, "Unable to read PEACE state"))
        print(f"    {_c(RED, 'FAIL')}  PEACE > 80% — unable to read state")

    # 4. Fleet reachable (quick ping via love.json fleet IPs)
    love_cfg = _load_json(LOVE_JSON, {})
    fleet = love_cfg.get("fleet", {})
    fleet_ok = 0
    fleet_total = len(fleet)
    if fleet:
        # Parallel ping (fast, non-SSH)
        ping_tasks = {}
        for name, ip in fleet.items():
            ping_tasks[name] = (["ping", "-c", "1", "-W", "2", str(ip)], 4)
        ping_results = _run_parallel(ping_tasks)
        for name, (prc, pout, perr) in ping_results.items():
            if prc == 0:
                fleet_ok += 1
    if fleet_ok == fleet_total and fleet_total > 0:
        checks.append(("Fleet Reachable", True, f"{fleet_ok}/{fleet_total} nodes"))
        print(f"    {_c(GREEN, 'PASS')}  Fleet Reachable — {fleet_ok}/{fleet_total} nodes responding")
    elif fleet_ok > 0:
        checks.append(("Fleet Reachable", False, f"{fleet_ok}/{fleet_total} nodes"))
        print(f"    {_c(YELLOW, 'WARN')}  Fleet Reachable — {fleet_ok}/{fleet_total} nodes responding")
    else:
        checks.append(("Fleet Reachable", False, f"0/{fleet_total} nodes"))
        print(f"    {_c(RED, 'FAIL')}  Fleet Reachable — no nodes responding")

    # 5. VPN active
    rc, out, err = _run(["curl", "-s", "--max-time", "3", "ifconfig.me"], timeout=5)
    vpn_ips = {ip for ip in fleet.values()}
    vpn_active = rc == 0 and out.strip() in vpn_ips
    if vpn_active:
        checks.append(("VPN Active", True, f"Exit: {out.strip()}"))
        print(f"    {_c(GREEN, 'PASS')}  VPN Active — exit IP: {out.strip()}")
    else:
        checks.append(("VPN Active", False, f"Exit: {out.strip() if rc == 0 else 'unknown'}"))
        exit_ip = out.strip() if rc == 0 else "unknown"
        print(f"    {_c(YELLOW, 'WARN')}  VPN Active — exit IP: {exit_ip} (not a fleet node)")

    # 6. DoH resolving
    rc, out, err = _run(["dig", "@127.0.0.1", "google.com", "+short", "+time=2"], timeout=5)
    doh_ok = rc == 0 and out.strip() != ""
    if doh_ok:
        checks.append(("DoH Resolving", True, "DNS over HTTPS working"))
        print(f"    {_c(GREEN, 'PASS')}  DoH Resolving — DNS over HTTPS working")
    else:
        checks.append(("DoH Resolving", False, "DoH not resolving"))
        print(f"    {_c(YELLOW, 'WARN')}  DoH Resolving — not available")

    # Verdict
    print()
    print(f"  {_c(DIM, '─' * 50)}")
    pass_count = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    # Critical checks: git clean, KOS green, PEACE > 80%
    critical_pass = all(ok for name, ok, _ in checks if name in ("Git Clean", "KOS Green", "PEACE > 80%"))

    if critical_pass:
        print(f"  {_c(BOLD + GREEN, 'GO')} — {pass_count}/{total} checks passed (all critical checks green)")
    else:
        failed_critical = [name for name, ok, reason in checks if not ok and name in ("Git Clean", "KOS Green", "PEACE > 80%")]
        print(f"  {_c(BOLD + RED, 'NO-GO')} — Critical checks failed: {', '.join(failed_critical)}")
    print()

    # Store in state
    state = _load_state()
    state["last_checklist"] = {
        "timestamp": _now(),
        "checks": {name: {"pass": ok, "reason": reason} for name, ok, reason in checks},
        "verdict": "GO" if critical_pass else "NO-GO",
    }
    _save_state(state)


# ── REPORT ───────────────────────────────────────────────────────────────────

def cmd_report():
    """Generate a structured wave report."""
    print()
    print(f"  {_c(BOLD, 'WAVE REPORT')} {_c(DIM, '— Kingdom Build Summary')}")
    print(f"  {_c(DIM, '═' * 50)}")
    print()

    state = _load_state()
    report = {"timestamp": _now()}

    # 1. New files since last snapshot
    print(f"  {_c(CYAN, 'New Files')}")
    last_verify = state.get("last_verify", {})
    # Use git to find recently added files
    rc, out, err = _run(["git", "-C", str(LOVE), "diff", "--name-only", "--diff-filter=A", "HEAD~5", "HEAD"], timeout=5)
    new_files = []
    if rc == 0 and out:
        new_files = [f for f in out.splitlines() if f.strip()]
    # Also check untracked
    rc2, out2, err2 = _run(["git", "-C", str(LOVE), "ls-files", "--others", "--exclude-standard"], timeout=5)
    untracked = []
    if rc2 == 0 and out2:
        untracked = [f for f in out2.splitlines() if f.strip()]

    if new_files:
        for f in new_files[:15]:
            print(f"    {_c(GREEN, '+')} {f}")
        if len(new_files) > 15:
            print(f"    {_c(DIM, f'... and {len(new_files) - 15} more')}")
    if untracked:
        print(f"  {_c(CYAN, 'Untracked (new, uncommitted)')}")
        for f in untracked[:10]:
            print(f"    {_c(YELLOW, '?')} {f}")
        if len(untracked) > 10:
            print(f"    {_c(DIM, f'... and {len(untracked) - 10} more')}")
    if not new_files and not untracked:
        print(f"    {_c(DIM, 'None detected')}")
    report["new_files"] = new_files
    report["untracked"] = untracked
    print()

    # 2. Modified files
    print(f"  {_c(CYAN, 'Modified Files')}")
    rc, out, err = _run(["git", "-C", str(LOVE), "diff", "--name-only", "--diff-filter=M", "HEAD~5", "HEAD"], timeout=5)
    modified = []
    if rc == 0 and out:
        modified = [f for f in out.splitlines() if f.strip()]
    # Also check working tree modifications
    rc2, out2, err2 = _run(["git", "-C", str(LOVE), "diff", "--name-only"], timeout=5)
    working_modified = []
    if rc2 == 0 and out2:
        working_modified = [f for f in out2.splitlines() if f.strip()]
    all_modified = sorted(set(modified + working_modified))
    if all_modified:
        for f in all_modified[:15]:
            print(f"    {_c(YELLOW, '~')} {f}")
        if len(all_modified) > 15:
            print(f"    {_c(DIM, f'... and {len(all_modified) - 15} more')}")
    else:
        print(f"    {_c(DIM, 'None')}")
    report["modified"] = all_modified
    print()

    # 3. Current metrics vs previous
    print(f"  {_c(CYAN, 'Metrics')}")
    current_metrics = _gather_metrics()
    for key, val in [
        ("Total Tools", current_metrics["total_tools"]),
        ("Total Lines", f"{current_metrics['total_lines']:,}"),
        ("Agents", current_metrics["agents"]),
        ("Snapshots", current_metrics["snapshots"]),
        ("Threats", f"{current_metrics['threats_mitigated']}/{current_metrics['threats_total']} mitigated"),
        ("Knowledge Nodes", current_metrics["knowledge_total"]),
        ("ZRN", current_metrics["zrn_accumulated"]),
    ]:
        print(f"    {key}: {_c(BOLD, val)}")

    # Deltas from last verify
    prev_metrics = None
    if last_verify:
        prev_metrics = last_verify.get("checks", {}).get("metrics", {}).get("data")
    if prev_metrics:
        deltas = _compute_deltas(prev_metrics, current_metrics)
        if deltas:
            print()
            print(f"  {_c(CYAN, 'Changes Since Last Verify')}")
            for label, delta in deltas.items():
                sign = "+" if delta > 0 else ""
                color = GREEN if delta > 0 else YELLOW
                print(f"    {label}: {_c(color, f'{sign}{delta}')}")
    report["metrics"] = current_metrics
    print()

    # 4. Alignment status
    print(f"  {_c(CYAN, 'Alignment')}")
    rc, out, err = _run_tool("alignment.py", "check", timeout=10)
    if rc == 0 and out:
        for line in out.split("\n")[:5]:
            if line.strip():
                print(f"    {line.strip()}")
        report["alignment"] = _strip_ansi(out)[:300]
    else:
        print(f"    {_c(YELLOW, 'Alignment check unavailable')}")
        report["alignment"] = "unavailable"
    print()

    # 5. Waves
    waves = state.get("waves", [])
    if waves:
        print(f"  {_c(CYAN, 'Waves')}")
        for w in waves:
            status_color = GREEN if w.get("status") == "verified" else YELLOW
            print(f"    Wave {w['id']}: {_c(status_color, w['status'])} — {w.get('description', '?')}")
        print()
    report["waves"] = waves

    # PEACE score
    peace_score = _get_peace_score()
    if peace_score is not None:
        color = GREEN if peace_score >= 90 else YELLOW if peace_score >= 80 else RED
        print(f"  PEACE: {_c(color, f'{peace_score}%')}")
    print()

    # Store
    state["last_report"] = report
    _save_state(state)

    print(f"  {_c(DIM, 'Report stored in memory/workflow/state.json')}")
    print()


# ── Utilities ────────────────────────────────────────────────────────────────

def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    import re
    return re.sub(r'\033\[[0-9;]*m', '', text)


def _get_peace_score():
    """Read PEACE score from state file."""
    peace_data = _load_json(PEACE_STATE, {})
    score = peace_data.get("score")
    if isinstance(score, (int, float)):
        return score
    return None


# ── CLI ──────────────────────────────────────────────────────────────────────

USAGE = f"""\
{BOLD}workflow.py{NC} — Kingdom Build Methodology Engine

{DIM}Encodes SENSE -> PLAN -> PARALLEL -> WIRE -> VERIFY{NC}

{CYAN}Commands:{NC}
  sense                     Run full SENSE phase (status, peace, alignment, fleet)
  plan "<description>"      Create a work plan with parallelization strategy
  verify                    Run full VERIFY phase (integrity, snapshot, status)
  metrics                   Show session metrics (tools, lines, agents, etc.)
  checklist                 Pre-build checklist (GO / NO-GO)
  report                    Generate wave report (what was built)

{DIM}Examples:{NC}
  workflow.py sense
  workflow.py plan "Build HOLYFRUIT revenue engine"
  workflow.py verify
  workflow.py checklist
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return

    cmd = sys.argv[1]

    if cmd == "sense":
        cmd_sense()
    elif cmd == "plan":
        if len(sys.argv) < 3:
            print(f"{_c(RED, 'Error')}: plan requires a description")
            print(f"  Usage: workflow.py plan \"description\"")
            sys.exit(1)
        cmd_plan(" ".join(sys.argv[2:]))
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "metrics":
        cmd_metrics()
    elif cmd == "checklist":
        cmd_checklist()
    elif cmd == "report":
        cmd_report()
    else:
        print(f"{_c(RED, 'Unknown command')}: {cmd}")
        print(f"Run 'workflow.py help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
