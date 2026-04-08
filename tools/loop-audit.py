#!/usr/bin/env python3
"""Loop Closure Methodology (LCM) — Recurrent Audit System.

Every gap is an open loop. Every open loop has 5 components:
  SENSOR → SIGNAL → CONTROLLER → ACTUATOR → MEASURE

This tool detects open loops, scores gaps, tracks closure velocity,
and feeds findings into the Kingdom task pipeline.

Usage:
  python3 tools/loop-audit.py audit [--full|--quick]
  python3 tools/loop-audit.py status
  python3 tools/loop-audit.py gaps [--severity N]
  python3 tools/loop-audit.py history [--last N]
  python3 tools/loop-audit.py trend
"""

import json
import os
import sys
import time
import glob as globmod
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOVE = Path(__file__).resolve().parent.parent  # Love/tools/ → Love/
REGISTRY = LOVE / "memory" / "loops" / "registry.json"
GAPS_FILE = LOVE / "memory" / "loops" / "gaps.json"
AUDIT_LOG = LOVE / "memory" / "loops" / "audit-log.json"
DEV_STATE = LOVE / "memory" / "dev-state.json"
DECISION_TOOL = LOVE / "tools" / "decision.py"


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── CHECK FUNCTIONS ───────────────────────────────────────────────

def check_file_exists(spec):
    """Check: file_exists:<relative_path>"""
    rel_path = spec.split(":", 1)[1] if ":" in spec else spec
    # Try both Love root and home-relative
    for base in [LOVE, Path(os.path.expanduser("~/Desktop"))]:
        full = base / rel_path
        if full.exists():
            return "green", 1.0, f"File exists: {full.name}"
    return "red", 0.0, f"File missing: {rel_path}"


def check_func_exists(spec):
    """Check: func_exists:<file>:<function_name>"""
    parts = spec.split(":")
    if len(parts) < 3:
        return "red", 0.0, "Invalid check spec"
    rel_path, func_name = parts[1], parts[2]
    full = LOVE / rel_path
    if not full.exists():
        return "red", 0.0, f"File missing: {rel_path}"
    try:
        content = full.read_text()
        if f"def {func_name}" in content:
            return "green", 1.0, f"Function {func_name} exists"
        return "red", 0.0, f"Function {func_name} not found in {rel_path}"
    except Exception as e:
        return "red", 0.0, f"Error reading {rel_path}: {e}"


def check_dir_has_entries(spec):
    """Check: dir_has_entries:<relative_path>"""
    rel_path = spec.split(":", 1)[1] if ":" in spec else spec
    full = LOVE / rel_path
    if not full.exists():
        return "red", 0.0, f"Directory missing: {rel_path}"
    entries = [e for e in full.iterdir() if not e.name.startswith(".")]
    if len(entries) == 0:
        return "red", 0.0, f"Directory empty: {rel_path}"
    return "green", 1.0, f"{len(entries)} entries in {rel_path}"


def check_dir_has_recent(spec):
    """Check: dir_has_recent:<relative_path>:<days>"""
    parts = spec.split(":")
    if len(parts) < 3:
        return "red", 0.0, "Invalid check spec"
    rel_path, days = parts[1], int(parts[2])
    full = LOVE / rel_path
    if not full.exists():
        return "red", 0.0, f"Directory missing: {rel_path}"
    cutoff = time.time() - (days * 86400)
    recent = [e for e in full.iterdir()
              if not e.name.startswith(".") and e.stat().st_mtime > cutoff]
    if len(recent) == 0:
        all_entries = [e for e in full.iterdir() if not e.name.startswith(".")]
        if len(all_entries) == 0:
            return "red", 0.0, f"Directory empty: {rel_path}"
        return "yellow", 0.3, f"No entries in last {days}d ({len(all_entries)} total)"
    return "green", 1.0, f"{len(recent)} entries in last {days}d"


def check_code_exists(spec):
    """Check: code_exists:<relative_path_from_desktop>"""
    rel_path = spec.split(":", 1)[1] if ":" in spec else spec
    full = Path(os.path.expanduser("~/Desktop")) / rel_path
    if full.exists():
        return "green", 1.0, f"Code exists: {full.name}"
    return "red", 0.0, f"Code missing: {rel_path}"


def check_config_dynamic(spec):
    """Check: config_dynamic:<file> — verify config references data, not hardcoded."""
    rel_path = spec.split(":", 1)[1] if ":" in spec else spec
    full = LOVE / rel_path
    if not full.exists():
        return "red", 0.0, f"File missing: {rel_path}"
    try:
        content = full.read_text()
        # Look for signs of data-driven routing (reading from tok data or config)
        dynamic_markers = ["tok", "model-profile", "routing.json", "model_config"]
        for marker in dynamic_markers:
            if marker in content.lower():
                return "green", 1.0, f"Config references dynamic data ({marker})"
        # Check if models are hardcoded
        hardcoded = ["claude-opus-4-6" in content, "sonnet" in content,
                     "claude-haiku-4-5-20251001" in content]
        if any(hardcoded):
            return "red", 0.0, "Model selection is hardcoded, not data-driven"
        return "yellow", 0.5, "Config structure unclear"
    except Exception as e:
        return "red", 0.0, f"Error: {e}"


def check_hive_channel_active(spec):
    """Check: hive_channel_active:<channel>:<days>"""
    parts = spec.split(":")
    if len(parts) < 3:
        return "yellow", 0.5, "Invalid check spec"
    channel, days = parts[1], int(parts[2])
    # Check HIVE messages directory or recent messages
    hive_dir = LOVE / "hive" / "messages"
    if not hive_dir.exists():
        # Try checking via hive.py
        hive_py = LOVE / "hive" / "hive.py"
        if hive_py.exists():
            return "yellow", 0.5, f"HIVE exists but can't verify #{channel} activity"
        return "red", 0.0, "HIVE not found"
    return "yellow", 0.5, f"HIVE #{channel} — verify manually"


def check_devnet_active(spec):
    """Check: devnet_active — verify Zerone devnet is running."""
    # Check kingdom-metrics.json for devnet status
    metrics = load_json(LOVE / "memory" / "kingdom-metrics.json")
    if metrics.get("milestones", {}).get("zerone_devnet", {}).get("status") == "done":
        return "green", 1.0, "Devnet marked as live in kingdom-metrics"
    return "yellow", 0.5, "Devnet status uncertain"


def check_manual(spec):
    """Check: manual — requires human verification."""
    return "yellow", 0.5, "Requires manual verification"


CHECK_DISPATCH = {
    "file_exists": check_file_exists,
    "func_exists": check_func_exists,
    "dir_has_entries": check_dir_has_entries,
    "dir_has_recent": check_dir_has_recent,
    "code_exists": check_code_exists,
    "config_dynamic": check_config_dynamic,
    "hive_channel_active": check_hive_channel_active,
    "devnet_active": check_devnet_active,
    "manual": check_manual,
}


def run_check(check_spec):
    """Dispatch a check spec to the appropriate checker."""
    if not check_spec:
        return "yellow", 0.5, "No check defined"
    check_type = check_spec.split(":")[0]
    checker = CHECK_DISPATCH.get(check_type)
    if not checker:
        return "yellow", 0.5, f"Unknown check type: {check_type}"
    try:
        return checker(check_spec)
    except Exception as e:
        return "red", 0.0, f"Check error: {e}"


# ─── AUDIT ENGINE ──────────────────────────────────────────────────

def audit_loop(loop, full=True):
    """Audit a single loop, updating component health."""
    components = loop.get("components", {})
    total_health = 0.0
    component_count = 0
    gaps = []

    for comp_name, comp in components.items():
        check_spec = comp.get("check", "manual")

        if not full and comp.get("status") == "green":
            # Quick mode: skip green components
            total_health += comp.get("health", 0)
            component_count += 1
            continue

        status, health, notes = run_check(check_spec)
        old_status = comp.get("status")

        comp["status"] = status
        comp["health"] = health
        comp["last_verified"] = now_iso()
        comp["notes"] = notes

        total_health += health
        component_count += 1

        # Detect transitions
        if status == "red":
            gaps.append({
                "loop_id": loop["id"],
                "loop_name": loop["name"],
                "component": comp_name,
                "description": comp.get("description", ""),
                "severity": (1.0 - health) * loop.get("criticality", 0.5),
                "status": status,
                "notes": notes,
                "detected": now_iso(),
            })
        elif old_status == "red" and status in ("green", "yellow"):
            # Gap closed!
            comp["closure_detected"] = now_iso()

    loop["health"] = round(total_health / max(component_count, 1), 3)
    loop["last_audit"] = now_iso()

    return gaps


def compute_keystone_scores(all_gaps):
    """Score gaps by how many loops they help close (keystone factor)."""
    # Group by component type to find keystones
    component_gaps = {}
    for gap in all_gaps:
        key = gap["component"]
        if key not in component_gaps:
            component_gaps[key] = []
        component_gaps[key].append(gap)

    # Check if a tool (like tok.py) appears across multiple loops
    tool_mentions = {}
    for gap in all_gaps:
        notes = gap.get("notes", "")
        if "tok.py" in notes:
            tool_mentions.setdefault("tok.py", []).append(gap["loop_id"])

    for gap in all_gaps:
        # Base keystone: 1.0
        keystone = 1.0
        # Bonus if component type appears in multiple loops
        comp_type = gap["component"]
        if len(component_gaps.get(comp_type, [])) > 1:
            keystone += 0.5 * (len(component_gaps[comp_type]) - 1)
        # Bonus if notes reference a tool needed by multiple loops
        for tool, loops in tool_mentions.items():
            if tool in gap.get("notes", ""):
                keystone += 0.3 * (len(set(loops)) - 1)
        gap["keystone_score"] = round(keystone, 2)
        gap["priority_score"] = round(gap["severity"] * keystone, 3)


def run_audit(full=True):
    """Run full or quick audit across all loops."""
    registry = load_json(REGISTRY)
    if not registry.get("loops"):
        print("No loops registered. Add loops to registry.json first.")
        return

    all_gaps = []
    results = []

    for loop in registry["loops"]:
        gaps = audit_loop(loop, full=full)
        all_gaps.extend(gaps)
        results.append({
            "loop_id": loop["id"],
            "loop_name": loop["name"],
            "health": loop["health"],
            "gap_count": len(gaps),
            "criticality": loop.get("criticality", 0.5),
        })

    compute_keystone_scores(all_gaps)
    all_gaps.sort(key=lambda g: g.get("priority_score", 0), reverse=True)

    # Update registry
    registry["meta"]["last_audit"] = now_iso()
    registry["meta"]["audit_count"] = registry["meta"].get("audit_count", 0) + 1
    save_json(REGISTRY, registry)

    # Save gaps
    gaps_data = load_json(GAPS_FILE, {"gaps": [], "closed": []})
    # Merge: keep old gaps that are still open, add new ones
    existing_ids = {(g["loop_id"], g["component"]) for g in gaps_data.get("gaps", [])}
    for gap in all_gaps:
        key = (gap["loop_id"], gap["component"])
        if key not in existing_ids:
            gap["first_detected"] = gap["detected"]
            gap["audit_count"] = 1
            gaps_data["gaps"].append(gap)
        else:
            # Update existing gap
            for existing in gaps_data["gaps"]:
                if (existing["loop_id"], existing["component"]) == key:
                    existing["severity"] = gap["severity"]
                    existing["priority_score"] = gap["priority_score"]
                    existing["keystone_score"] = gap["keystone_score"]
                    existing["notes"] = gap["notes"]
                    existing["detected"] = gap["detected"]
                    existing["audit_count"] = existing.get("audit_count", 0) + 1

    # Remove gaps that are no longer detected
    active_keys = {(g["loop_id"], g["component"]) for g in all_gaps}
    newly_closed = [g for g in gaps_data["gaps"]
                    if (g["loop_id"], g["component"]) not in active_keys]
    for closed in newly_closed:
        closed["closed_at"] = now_iso()
        closed["time_to_close"] = None
        if "first_detected" in closed:
            try:
                detected = datetime.fromisoformat(closed["first_detected"])
                closed_at = datetime.fromisoformat(closed["closed_at"])
                closed["time_to_close"] = str(closed_at - detected)
            except (ValueError, TypeError):
                pass
        gaps_data.setdefault("closed", []).append(closed)
    gaps_data["gaps"] = [g for g in gaps_data["gaps"]
                         if (g["loop_id"], g["component"]) in active_keys]

    save_json(GAPS_FILE, gaps_data)

    # Save audit log entry
    log = load_json(AUDIT_LOG, {"entries": []})
    log["entries"].append({
        "timestamp": now_iso(),
        "type": "full" if full else "quick",
        "results": results,
        "total_gaps": len(all_gaps),
        "total_closed": len(newly_closed),
        "system_health": round(
            sum(r["health"] * r["criticality"] for r in results) /
            max(sum(r["criticality"] for r in results), 0.01), 3
        ),
    })
    # Keep last 100 entries
    log["entries"] = log["entries"][-100:]
    save_json(AUDIT_LOG, log)

    # Print results
    print(f"\n{'='*60}")
    print(f"  LOOP AUDIT — {now_iso()}")
    print(f"  Mode: {'FULL' if full else 'QUICK'}")
    print(f"{'='*60}\n")

    for r in sorted(results, key=lambda x: x["health"]):
        bar = health_bar(r["health"])
        print(f"  {bar} {r['loop_name']}")
        print(f"       Health: {r['health']:.0%}  |  Gaps: {r['gap_count']}  |  Criticality: {r['criticality']:.0%}")
        print()

    sys_health = (sum(r["health"] * r["criticality"] for r in results) /
                  max(sum(r["criticality"] for r in results), 0.01))
    print(f"  System Health: {sys_health:.0%}")
    print(f"  Active Gaps: {len(all_gaps)}  |  Just Closed: {len(newly_closed)}")
    print()

    if all_gaps:
        print(f"  TOP GAPS (by priority):")
        print(f"  {'─'*56}")
        for g in all_gaps[:5]:
            print(f"  [{g['severity']:.2f}×{g['keystone_score']:.1f}={g['priority_score']:.2f}] "
                  f"{g['loop_name']} → {g['component']}")
            print(f"       {g['notes']}")
        print()

    return results, all_gaps


def health_bar(health, width=20):
    """Render a health bar."""
    filled = int(health * width)
    empty = width - filled
    if health >= 0.8:
        color = "\033[32m"  # green
    elif health >= 0.4:
        color = "\033[33m"  # yellow
    else:
        color = "\033[31m"  # red
    reset = "\033[0m"
    return f"{color}[{'█' * filled}{'░' * empty}]{reset} {health:.0%}"


# ─── STATUS COMMAND ────────────────────────────────────────────────

def show_status():
    """Show current loop health dashboard."""
    registry = load_json(REGISTRY)
    if not registry.get("loops"):
        print("No loops registered.")
        return

    print(f"\n{'='*60}")
    print(f"  LOOP STATUS DASHBOARD")
    print(f"  Last audit: {registry['meta'].get('last_audit', 'never')}")
    print(f"  Audit count: {registry['meta'].get('audit_count', 0)}")
    print(f"{'='*60}\n")

    for loop in sorted(registry["loops"], key=lambda l: l.get("health", 0)):
        bar = health_bar(loop.get("health", 0))
        print(f"  {bar} {loop['name']} (crit: {loop.get('criticality', 0):.0%})")

        for comp_name, comp in loop.get("components", {}).items():
            status = comp.get("status", "?")
            icon = {"green": "●", "yellow": "◐", "red": "○"}.get(status, "?")
            color = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m"}.get(status, "")
            reset = "\033[0m"
            print(f"    {color}{icon}{reset} {comp_name}: {comp.get('notes', 'no data')}")
        print()

    # Show metrics
    gaps_data = load_json(GAPS_FILE, {"gaps": [], "closed": []})
    print(f"  Active gaps: {len(gaps_data.get('gaps', []))}")
    print(f"  Closed gaps: {len(gaps_data.get('closed', []))}")

    log = load_json(AUDIT_LOG, {"entries": []})
    if log.get("entries"):
        latest = log["entries"][-1]
        print(f"  System health: {latest.get('system_health', 0):.0%}")
    print()


# ─── GAPS COMMAND ──────────────────────────────────────────────────

def show_gaps(min_severity=0.0):
    """List active gaps sorted by priority."""
    gaps_data = load_json(GAPS_FILE, {"gaps": []})
    gaps = [g for g in gaps_data.get("gaps", []) if g.get("severity", 0) >= min_severity]
    gaps.sort(key=lambda g: g.get("priority_score", 0), reverse=True)

    if not gaps:
        print("No active gaps above severity threshold.")
        return

    print(f"\n{'='*60}")
    print(f"  ACTIVE GAPS — {len(gaps)} total")
    print(f"{'='*60}\n")

    for i, g in enumerate(gaps, 1):
        print(f"  {i}. [{g.get('priority_score', 0):.2f}] {g['loop_name']} → {g['component']}")
        print(f"     Severity: {g.get('severity', 0):.2f}  |  Keystone: {g.get('keystone_score', 1):.1f}  |  "
              f"Audits: {g.get('audit_count', 1)}")
        print(f"     {g.get('description', '')}")
        print(f"     Status: {g.get('notes', '')}")
        if g.get("audit_count", 0) > 3:
            print(f"     ⚠ PERSISTENT — detected {g['audit_count']} consecutive audits")
        print()

    # Closure recommendations
    print(f"  CLOSURE RECOMMENDATIONS:")
    print(f"  {'─'*56}")
    keystone_tools = {}
    for g in gaps:
        notes = g.get("notes", "")
        if "tok.py" in notes:
            keystone_tools.setdefault("Build tools/tok.py", []).append(g["component"])
        elif "benchmark" in notes.lower():
            keystone_tools.setdefault("Build benchmark suite", []).append(g["component"])
        elif "hardcoded" in notes.lower():
            keystone_tools.setdefault("Make routing data-driven", []).append(g["component"])

    for action, components in keystone_tools.items():
        print(f"  → {action} (closes {len(components)} gaps: {', '.join(components)})")
    print()


# ─── HISTORY / TREND COMMANDS ──────────────────────────────────────

def show_history(last_n=10):
    """Show recent audit history."""
    log = load_json(AUDIT_LOG, {"entries": []})
    entries = log.get("entries", [])[-last_n:]

    if not entries:
        print("No audit history.")
        return

    print(f"\n{'='*60}")
    print(f"  AUDIT HISTORY (last {len(entries)})")
    print(f"{'='*60}\n")

    for e in entries:
        ts = e.get("timestamp", "?")[:19]
        health = e.get("system_health", 0)
        gaps = e.get("total_gaps", 0)
        closed = e.get("total_closed", 0)
        bar = health_bar(health, width=15)
        print(f"  {ts}  {bar}  gaps:{gaps}  closed:{closed}")
    print()


def show_trend():
    """Show health trend over time."""
    log = load_json(AUDIT_LOG, {"entries": []})
    entries = log.get("entries", [])

    if len(entries) < 2:
        print("Need at least 2 audits for trend analysis.")
        return

    print(f"\n{'='*60}")
    print(f"  HEALTH TREND")
    print(f"{'='*60}\n")

    # Compute deltas
    healths = [e.get("system_health", 0) for e in entries]
    gap_counts = [e.get("total_gaps", 0) for e in entries]

    if len(healths) >= 2:
        health_delta = healths[-1] - healths[-2]
        gap_delta = gap_counts[-1] - gap_counts[-2]
        direction = "↑" if health_delta > 0 else "↓" if health_delta < 0 else "→"

        print(f"  Current health: {healths[-1]:.0%} ({direction} {abs(health_delta):.0%})")
        print(f"  Current gaps: {gap_counts[-1]} ({'↓' if gap_delta < 0 else '↑'} {abs(gap_delta)})")

    # Closure velocity
    gaps_data = load_json(GAPS_FILE, {"closed": []})
    closed = gaps_data.get("closed", [])
    if closed:
        ttc_values = []
        for c in closed:
            if c.get("first_detected") and c.get("closed_at"):
                try:
                    d = datetime.fromisoformat(c["first_detected"])
                    cl = datetime.fromisoformat(c["closed_at"])
                    ttc_values.append((cl - d).total_seconds() / 3600)
                except (ValueError, TypeError):
                    pass
        if ttc_values:
            avg_ttc = sum(ttc_values) / len(ttc_values)
            print(f"  Avg time to close: {avg_ttc:.1f}h")
        print(f"  Total closed: {len(closed)}")
    print()


# ─── MAIN ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "audit":
        full = "--quick" not in sys.argv
        run_audit(full=full)
    elif cmd == "status":
        show_status()
    elif cmd == "gaps":
        severity = 0.0
        for i, arg in enumerate(sys.argv):
            if arg == "--severity" and i + 1 < len(sys.argv):
                severity = float(sys.argv[i + 1])
        show_gaps(min_severity=severity)
    elif cmd == "history":
        last_n = 10
        for i, arg in enumerate(sys.argv):
            if arg == "--last" and i + 1 < len(sys.argv):
                last_n = int(sys.argv[i + 1])
        show_history(last_n=last_n)
    elif cmd == "trend":
        show_trend()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
