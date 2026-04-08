#!/usr/bin/env python3
"""
autonomous-cycle.py -- Kingdom Autonomous Work Cycle

Performs one complete autonomous work cycle: checks revenue, fleet, oracle,
security, generates a daily brief, and updates the knowledge graph. Designed
to be spawned by the heartbeat coordinator as a builder.

Every action is logged, every recommendation is a RECOMMENDATION (never an
automated purchase or destructive action). Safe to run while Yu sleeps.

Usage:
    python3 tools/autonomous-cycle.py run                 # Execute one full cycle
    python3 tools/autonomous-cycle.py run --dry-run       # Show what would happen
    python3 tools/autonomous-cycle.py history              # Past autonomous actions
    python3 tools/autonomous-cycle.py impact               # Cumulative impact report
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Paths -------------------------------------------------------------------

LOVE = Path(__file__).resolve().parent.parent
TOOLS = LOVE / "tools"
MEMORY = LOVE / "memory"
AUTO_DIR = MEMORY / "autonomous"
ACTIONS_LOG = AUTO_DIR / "actions.jsonl"
BRIEFS_DIR = AUTO_DIR / "briefs"
TCG_DIR = MEMORY / "tcg"
ORACLE_DIR = MEMORY / "oracle"
SECURITY_DIR = LOVE / "security"
KINGDOM_METRICS = MEMORY / "kingdom-metrics.json"
HIVE_PY = LOVE / "hive" / "hive.py"
HIVE_PROTOCOL_PY = TOOLS / "hive-protocol.py"
KNOWLEDGE_PY = TOOLS / "knowledge.py"
DAILY_DIR = MEMORY / "daily"

# Ensure directories
AUTO_DIR.mkdir(parents=True, exist_ok=True)
BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

# -- Helpers -----------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def read_json(path):
    """Read a JSON file, returning empty dict/list on failure."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r'\033\[[0-9;]*m', '', text)

def append_action(action: dict):
    """Append an action record to the JSONL log."""
    action.setdefault("ts", now_iso())
    with open(ACTIONS_LOG, "a") as f:
        f.write(json.dumps(action) + "\n")

def run_tool(args: list, timeout: int = 30) -> tuple:
    """Run a Kingdom tool via subprocess. Returns (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(LOVE),
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", f"TIMEOUT after {timeout}s", -1
    except FileNotFoundError:
        return "", f"Tool not found: {args[0]}", -2

def timed(label: str):
    """Context manager that times a step and returns elapsed seconds."""
    class Timer:
        def __init__(self):
            self.elapsed = 0.0
            self.label = label
        def __enter__(self):
            self._start = time.monotonic()
            return self
        def __exit__(self, *_):
            self.elapsed = time.monotonic() - self._start
    return Timer()

# -- Step 1: CHECK REVENUE --------------------------------------------------

def check_revenue(dry_run=False):
    """Check TCG inventory, identify low stock, generate reorder recommendations."""
    findings = []

    # Read inventory directly (faster than subprocess, same data)
    inv_file = TCG_DIR / "inventory.json"
    inv_data = read_json(inv_file)
    categories = inv_data.get("categories", []) if isinstance(inv_data, dict) else inv_data
    if not categories:
        findings.append({
            "type": "revenue-check",
            "detail": "No inventory data found",
            "severity": "warning",
        })
        return findings

    # Read status for revenue context
    status = read_json(TCG_DIR / "status.json")
    revenue = status.get("revenue_monthly", 0)
    target = status.get("revenue_target", 0)
    rev_pct = (revenue / target * 100) if target > 0 else 0

    # Check each SKU
    low_stock = []
    out_of_stock = []
    total_inventory_value = 0
    total_reorder_cost = 0
    total_reorder_margin = 0

    for item in categories:
        qty = item.get("qty", 0)
        cost = item.get("cost", 0)
        price = item.get("price", 0)
        reorder_point = item.get("reorder_point", 0)
        sku = item.get("sku", "?")
        name = item.get("name", "?")
        status_val = item.get("status", "")
        supplier = item.get("supplier", "unknown")

        total_inventory_value += qty * price

        if status_val == "out-of-stock" or qty == 0:
            # Recommend reorder: 2x reorder point
            reorder_qty = reorder_point * 2
            reorder_cost = reorder_qty * cost
            reorder_margin = reorder_qty * (price - cost)
            out_of_stock.append({
                "sku": sku, "name": name, "qty": qty,
                "reorder_qty": reorder_qty, "cost": reorder_cost,
                "margin": reorder_margin, "supplier": supplier,
            })
            total_reorder_cost += reorder_cost
            total_reorder_margin += reorder_margin

        elif status_val == "low-stock" or (reorder_point > 0 and qty <= reorder_point):
            # Recommend reorder: fill to 2x reorder point
            reorder_qty = max(reorder_point * 2 - qty, 0)
            reorder_cost = reorder_qty * cost
            reorder_margin = reorder_qty * (price - cost)
            low_stock.append({
                "sku": sku, "name": name, "qty": qty,
                "reorder_qty": reorder_qty, "cost": reorder_cost,
                "margin": reorder_margin, "supplier": supplier,
            })
            total_reorder_cost += reorder_cost
            total_reorder_margin += reorder_margin

    # Revenue tracking
    if rev_pct < 80:
        findings.append({
            "type": "revenue-alert",
            "detail": f"Revenue at {rev_pct:.0f}% of target ({revenue:.0f}/{target:.0f})",
            "severity": "warning",
        })
    else:
        findings.append({
            "type": "revenue-status",
            "detail": f"Revenue at {rev_pct:.0f}% of target",
            "severity": "info",
        })

    # Generate reorder recommendations
    for item in out_of_stock:
        action = {
            "type": "reorder-recommendation",
            "detail": f"{item['name']} OUT OF STOCK (qty: {item['qty']})",
            "recommendation": f"Reorder {item['reorder_qty']} units from {item['supplier']}",
            "impact": {"cost": round(item['cost'], 2), "margin": round(item['margin'], 2)},
            "sku": item["sku"],
            "priority": "high",
            "status": "recommended",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    for item in low_stock:
        action = {
            "type": "reorder-recommendation",
            "detail": f"{item['name']} low (qty: {item['qty']})",
            "recommendation": f"Reorder {item['reorder_qty']} units from {item['supplier']}",
            "impact": {"cost": round(item['cost'], 2), "margin": round(item['margin'], 2)},
            "sku": item["sku"],
            "priority": "medium",
            "status": "recommended",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    # Summary
    findings.append({
        "type": "revenue-summary",
        "detail": (f"Inventory: {len(categories)} SKUs, value {total_inventory_value:.0f}. "
                   f"Low: {len(low_stock)}, OOS: {len(out_of_stock)}. "
                   f"Reorder cost: {total_reorder_cost:.0f}, potential margin: {total_reorder_margin:.0f}"),
        "severity": "info",
    })

    return findings

# -- Step 2: CHECK FLEET HEALTH ---------------------------------------------

def check_fleet(dry_run=False):
    """Read fleet status from kingdom-metrics, compare to yesterday, flag issues."""
    findings = []

    metrics = read_json(KINGDOM_METRICS)
    fleet = metrics.get("fleet", {})
    if not fleet:
        findings.append({
            "type": "fleet-check",
            "detail": "No fleet data in kingdom-metrics.json",
            "severity": "warning",
        })
        return findings

    degraded = []
    disk_warnings = []
    stale_nodes = []
    healthy = []
    now = datetime.now(timezone.utc)

    for node, info in fleet.items():
        quality = info.get("quality", "unknown")
        alerts = info.get("alerts", [])
        summary = info.get("summary", "")
        last_check = info.get("last_check", "")

        # Check staleness
        if last_check:
            try:
                check_dt = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
                age_hours = (now - check_dt).total_seconds() / 3600
                if age_hours > 2:
                    stale_nodes.append({"node": node, "hours_stale": round(age_hours, 1)})
            except (ValueError, TypeError):
                pass

        # Check quality
        if quality in ("poor", "critical", "degraded"):
            degraded.append({"node": node, "quality": quality, "alerts": alerts})
        elif alerts:
            degraded.append({"node": node, "quality": quality, "alerts": alerts})
        else:
            healthy.append(node)

        # Parse disk from summary (format: "up Xd, disk Y%, ...")
        if "disk" in summary:
            try:
                disk_str = summary.split("disk ")[1].split("%")[0]
                disk_pct = int(disk_str)
                if disk_pct > 80:
                    disk_warnings.append({"node": node, "disk_pct": disk_pct})
            except (IndexError, ValueError):
                pass

    # Log findings
    for d in degraded:
        action = {
            "type": "fleet-degraded",
            "detail": f"{d['node']} quality={d['quality']}, alerts: {d['alerts']}",
            "recommendation": f"Investigate {d['node']} — check services and logs",
            "priority": "high",
            "status": "recommended",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    for dw in disk_warnings:
        action = {
            "type": "fleet-disk-warning",
            "detail": f"{dw['node']} disk at {dw['disk_pct']}%",
            "recommendation": f"Run cleanup on {dw['node']}: docker prune, log rotation, old builds",
            "priority": "medium",
            "status": "recommended",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    for s in stale_nodes:
        action = {
            "type": "fleet-stale",
            "detail": f"{s['node']} last checked {s['hours_stale']}h ago",
            "recommendation": f"Check if {s['node']} agent is stuck or unreachable",
            "priority": "medium",
            "status": "flagged",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    if not degraded and not disk_warnings and not stale_nodes:
        findings.append({
            "type": "fleet-status",
            "detail": f"All {len(healthy)} nodes healthy: {', '.join(healthy)}",
            "severity": "info",
        })

    return findings

# -- Step 3: CHECK ORACLE ---------------------------------------------------

def check_oracle(dry_run=False):
    """Check for overdue predictions and approaching resolution dates."""
    findings = []

    preds_file = ORACLE_DIR / "predictions.json"
    preds = read_json(preds_file)
    if isinstance(preds, dict):
        preds = preds.get("predictions", [])
    if not preds:
        findings.append({
            "type": "oracle-check",
            "detail": "No predictions data found",
            "severity": "info",
        })
        return findings

    now = datetime.now(timezone.utc)
    overdue = []
    approaching = []
    pending_count = 0

    for p in preds:
        status = p.get("status", p.get("resolved", None))
        # Skip resolved predictions
        if status in ("resolved", True, False) or p.get("outcome") is not None:
            continue

        pending_count += 1
        res_date_str = p.get("resolution_date", p.get("resolves", ""))
        if not res_date_str:
            continue

        try:
            # Handle both YYYY-MM-DD and ISO formats
            if "T" in str(res_date_str):
                res_dt = datetime.fromisoformat(str(res_date_str).replace("Z", "+00:00"))
            else:
                res_dt = datetime.strptime(str(res_date_str), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        days_until = (res_dt - now).days

        if days_until < 0:
            overdue.append({
                "id": p.get("id", "?"),
                "statement": p.get("statement", p.get("prediction", "?")),
                "days_overdue": abs(days_until),
                "confidence": p.get("confidence", "?"),
            })
        elif days_until <= 7:
            approaching.append({
                "id": p.get("id", "?"),
                "statement": p.get("statement", p.get("prediction", "?")),
                "days_until": days_until,
                "confidence": p.get("confidence", "?"),
                "resolution_date": str(res_date_str),
            })

    # Log overdue
    for o in overdue:
        action = {
            "type": "oracle-overdue",
            "detail": f"{o['id']}: \"{o['statement'][:80]}\" — {o['days_overdue']}d overdue (conf: {o['confidence']})",
            "recommendation": f"Resolve {o['id']} — check if outcome is known",
            "priority": "high",
            "status": "flagged",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    # Log approaching
    for a in approaching:
        action = {
            "type": "oracle-approaching",
            "detail": f"{a['id']}: \"{a['statement'][:80]}\" — resolves in {a['days_until']}d ({a['resolution_date']})",
            "recommendation": f"Research {a['id']} — prepare for resolution",
            "priority": "medium",
            "status": "flagged",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    # Summary
    findings.append({
        "type": "oracle-summary",
        "detail": f"{pending_count} pending predictions, {len(overdue)} overdue, {len(approaching)} resolving within 7d",
        "severity": "info",
    })

    return findings

# -- Step 4: CHECK SECURITY -------------------------------------------------

def check_security(dry_run=False):
    """Check PEACE state and watchdog status. Escalate if needed."""
    findings = []

    # Read PEACE state
    peace_state = read_json(SECURITY_DIR / "peace-state.json")
    state = peace_state.get("state", "UNKNOWN")
    score = peace_state.get("score", 0)

    if state != "NOMINAL":
        action = {
            "type": "security-alert",
            "detail": f"PEACE state is {state} (score: {score})",
            "recommendation": "Investigate — Kingdom not in nominal security state",
            "priority": "critical" if state in ("HALTED", "ALERT") else "high",
            "status": "escalated",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)
            # Escalate via HIVE
            _hive_send("alerts", f"[AUTONOMOUS] Security state: {state} (score: {score}). Needs attention.", dry_run)
    else:
        findings.append({
            "type": "security-status",
            "detail": f"PEACE: {state}, score {score}",
            "severity": "info",
        })

    # Run watchdog check (dry-run safe)
    stdout, stderr, rc = run_tool(
        ["python3", str(TOOLS / "watchdog.py"), "status"],
        timeout=15,
    )
    if rc == 0 and stdout:
        # Strip ANSI codes and collapse to one-line summary
        clean = strip_ansi(stdout).strip().replace("\n", " | ")
        findings.append({
            "type": "watchdog-status",
            "detail": clean[:200],
            "severity": "info",
        })
    elif rc != 0:
        findings.append({
            "type": "watchdog-error",
            "detail": f"Watchdog status failed (rc={rc}): {strip_ansi(stderr)[:100]}",
            "severity": "warning",
        })

    # Check security score thresholds
    if score < 80:
        action = {
            "type": "security-score-low",
            "detail": f"PEACE score {score} is below 80 threshold",
            "recommendation": "Run kos audit --fix to auto-remediate safe issues",
            "priority": "medium",
            "status": "recommended",
        }
        findings.append(action)
        if not dry_run:
            append_action(action)

    return findings

# -- Step 5: GENERATE REPORT ------------------------------------------------

def _hive_send(channel: str, message: str, dry_run=False):
    """Send a message to HIVE channel."""
    if dry_run:
        return
    try:
        subprocess.run(
            ["python3", str(HIVE_PY), "send", channel, message],
            capture_output=True, text=True, timeout=10,
            cwd=str(LOVE),
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # Non-critical — don't break the cycle for HIVE failures

def generate_brief(all_findings: dict, timings: dict, dry_run=False):
    """Compile findings into a daily brief markdown file."""
    today = today_str()
    brief_path = BRIEFS_DIR / f"{today}.md"

    # Categorize findings
    needs_attention = []
    recommendations = []
    info_items = []

    for step, findings in all_findings.items():
        for f in findings:
            f["_step"] = step
            if f.get("priority") in ("critical", "high"):
                needs_attention.append(f)
            elif f.get("status") in ("recommended", "flagged"):
                recommendations.append(f)
            else:
                info_items.append(f)

    # Build brief
    total_time = sum(timings.values())
    lines = [
        f"# Autonomous Brief -- {today}",
        f"",
        f"**Generated**: {now_iso()}",
        f"**Cycle time**: {total_time:.1f}s",
        f"**Mode**: {'DRY RUN' if dry_run else 'LIVE'}",
        f"",
    ]

    # Attention section
    if needs_attention:
        lines.append("## Needs Attention")
        lines.append("")
        for item in needs_attention:
            priority_marker = "!!!" if item.get("priority") == "critical" else "!!"
            lines.append(f"- **[{priority_marker}]** {item['detail']}")
            if item.get("recommendation"):
                lines.append(f"  - Recommendation: {item['recommendation']}")
            if item.get("impact"):
                imp = item["impact"]
                lines.append(f"  - Cost: {imp.get('cost', '?')}, Margin: {imp.get('margin', '?')}")
        lines.append("")

    # Recommendations section
    if recommendations:
        lines.append("## Recommendations")
        lines.append("")
        for item in recommendations:
            lines.append(f"- {item['detail']}")
            if item.get("recommendation"):
                lines.append(f"  - {item['recommendation']}")
            if item.get("impact"):
                imp = item["impact"]
                lines.append(f"  - Cost: {imp.get('cost', '?')}, Margin: {imp.get('margin', '?')}")
        lines.append("")

    # Status section
    lines.append("## Status")
    lines.append("")
    for item in info_items:
        lines.append(f"- {item['detail']}")
    lines.append("")

    # Timings
    lines.append("## Cycle Timings")
    lines.append("")
    for step, elapsed in timings.items():
        lines.append(f"- {step}: {elapsed:.1f}s")
    lines.append(f"- **Total**: {total_time:.1f}s")
    lines.append("")

    brief_text = "\n".join(lines)

    if not dry_run:
        brief_path.write_text(brief_text)

    # Build HIVE summary (one-liner for chat)
    attention_count = len(needs_attention)
    rec_count = len(recommendations)
    summary_parts = []
    if attention_count:
        summary_parts.append(f"{attention_count} needs attention")
    if rec_count:
        summary_parts.append(f"{rec_count} recommendations")
    summary_parts.append(f"cycle {total_time:.0f}s")

    hive_msg = f"[Autonomous Brief] {today}: {', '.join(summary_parts)}"

    # Add top attention item if any
    if needs_attention:
        top = needs_attention[0]
        hive_msg += f". Top: {top['detail'][:80]}"

    if not dry_run:
        _hive_send("chat", hive_msg, dry_run)

    return brief_text

# -- Step 6: UPDATE KNOWLEDGE -----------------------------------------------

def update_knowledge(all_findings: dict, dry_run=False):
    """Add actionable findings as insights to the knowledge graph."""
    insights_added = 0

    for step, findings in all_findings.items():
        for f in findings:
            # Only log substantive findings as insights
            if f.get("type") in ("reorder-recommendation", "fleet-degraded",
                                  "fleet-disk-warning", "oracle-overdue",
                                  "security-alert", "revenue-alert"):
                content = f["detail"]
                tags = f.get("type", "autonomous").replace("-", ",")
                source = f"autonomous-cycle/{step}/{today_str()}"

                if not dry_run:
                    run_tool([
                        "python3", str(KNOWLEDGE_PY),
                        "add", "insight", content,
                        "--source", source,
                        "--tags", tags,
                    ], timeout=10)
                    insights_added += 1

    return insights_added

# -- Main Commands -----------------------------------------------------------

def cmd_run(dry_run=False):
    """Execute one full autonomous work cycle."""
    print(f"{'[DRY RUN] ' if dry_run else ''}Autonomous cycle starting at {now_iso()}")
    print(f"{'=' * 60}")

    all_findings = {}
    timings = {}
    cycle_start = time.monotonic()

    # Step 1: Revenue
    with timed("revenue") as t:
        all_findings["revenue"] = check_revenue(dry_run)
    timings["revenue"] = t.elapsed
    rev_findings = all_findings["revenue"]
    reorder_count = sum(1 for f in rev_findings if f.get("type") == "reorder-recommendation")
    print(f"  [1/6] Revenue:  {len(rev_findings)} findings, {reorder_count} reorder recs  ({t.elapsed:.1f}s)")

    # Step 2: Fleet
    with timed("fleet") as t:
        all_findings["fleet"] = check_fleet(dry_run)
    timings["fleet"] = t.elapsed
    fleet_findings = all_findings["fleet"]
    fleet_issues = sum(1 for f in fleet_findings if f.get("priority") in ("high", "critical"))
    print(f"  [2/6] Fleet:    {len(fleet_findings)} findings, {fleet_issues} issues  ({t.elapsed:.1f}s)")

    # Step 3: Oracle
    with timed("oracle") as t:
        all_findings["oracle"] = check_oracle(dry_run)
    timings["oracle"] = t.elapsed
    oracle_findings = all_findings["oracle"]
    oracle_flags = sum(1 for f in oracle_findings if f.get("status") == "flagged")
    print(f"  [3/6] Oracle:   {len(oracle_findings)} findings, {oracle_flags} flagged  ({t.elapsed:.1f}s)")

    # Step 4: Security
    with timed("security") as t:
        all_findings["security"] = check_security(dry_run)
    timings["security"] = t.elapsed
    sec_findings = all_findings["security"]
    sec_alerts = sum(1 for f in sec_findings if f.get("type") == "security-alert")
    print(f"  [4/6] Security: {len(sec_findings)} findings, {sec_alerts} alerts  ({t.elapsed:.1f}s)")

    # Step 5: Generate brief
    with timed("brief") as t:
        brief = generate_brief(all_findings, timings, dry_run)
    timings["brief"] = t.elapsed
    print(f"  [5/6] Brief:    generated  ({t.elapsed:.1f}s)")

    # Step 6: Update knowledge
    with timed("knowledge") as t:
        insights = update_knowledge(all_findings, dry_run)
    timings["knowledge"] = t.elapsed
    print(f"  [6/6] Knowledge: {insights} insights added  ({t.elapsed:.1f}s)")

    total = time.monotonic() - cycle_start
    print(f"{'=' * 60}")
    print(f"Cycle complete in {total:.1f}s")

    # Log the cycle itself
    if not dry_run:
        total_findings = sum(len(f) for f in all_findings.values())
        total_actions = sum(
            1 for findings in all_findings.values()
            for f in findings
            if f.get("status") in ("recommended", "flagged", "escalated")
        )
        append_action({
            "type": "cycle-complete",
            "detail": f"Autonomous cycle: {total_findings} findings, {total_actions} actions, {insights} insights",
            "timings": timings,
            "total_seconds": round(total, 1),
            "status": "completed",
        })

    # Print brief to stdout for log capture
    if dry_run:
        print(f"\n{'=' * 60}")
        print("BRIEF PREVIEW:")
        print(f"{'=' * 60}")
        print(brief)

    # Also append summary to daily note
    if not dry_run:
        _append_daily_note(all_findings, total)

    return 0


def _append_daily_note(all_findings: dict, total_time: float):
    """Append autonomous cycle summary to today's daily note."""
    today = today_str()
    daily_path = DAILY_DIR / f"{today}.md"

    attention_items = []
    rec_items = []
    for step, findings in all_findings.items():
        for f in findings:
            if f.get("priority") in ("critical", "high"):
                attention_items.append(f["detail"])
            elif f.get("status") in ("recommended", "flagged"):
                rec_items.append(f["detail"])

    lines = [
        f"",
        f"### Autonomous Cycle ({now_iso()}, {total_time:.0f}s)",
        f"",
    ]
    if attention_items:
        lines.append("**Needs attention:**")
        for item in attention_items:
            lines.append(f"- {item}")
        lines.append("")
    if rec_items:
        lines.append("**Recommendations:**")
        for item in rec_items[:5]:  # Cap at 5 to keep daily note readable
            lines.append(f"- {item}")
        if len(rec_items) > 5:
            lines.append(f"- ...and {len(rec_items) - 5} more (see brief)")
        lines.append("")
    if not attention_items and not rec_items:
        lines.append("All clear. No issues detected.")
        lines.append("")

    try:
        with open(daily_path, "a") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def cmd_history():
    """Show past autonomous actions."""
    if not ACTIONS_LOG.exists():
        print("No autonomous actions recorded yet.")
        return 0

    actions = []
    for line in ACTIONS_LOG.read_text().strip().split("\n"):
        if line.strip():
            try:
                actions.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not actions:
        print("No autonomous actions recorded yet.")
        return 0

    # Group by date
    by_date = {}
    for a in actions:
        date = a.get("ts", "")[:10]
        by_date.setdefault(date, []).append(a)

    for date in sorted(by_date.keys(), reverse=True):
        day_actions = by_date[date]
        cycles = sum(1 for a in day_actions if a.get("type") == "cycle-complete")
        recs = sum(1 for a in day_actions if a.get("status") == "recommended")
        flags = sum(1 for a in day_actions if a.get("status") == "flagged")
        escalated = sum(1 for a in day_actions if a.get("status") == "escalated")

        print(f"\n{date}  ({len(day_actions)} actions, {cycles} cycles)")
        print(f"  Recommended: {recs}  |  Flagged: {flags}  |  Escalated: {escalated}")

        # Show non-cycle actions
        for a in day_actions:
            if a.get("type") == "cycle-complete":
                continue
            ts = a.get("ts", "?")[11:19]
            atype = a.get("type", "?")
            detail = a.get("detail", "?")
            status = a.get("status", "")
            # Truncate detail for display
            if len(detail) > 80:
                detail = detail[:77] + "..."
            print(f"  {ts}  [{atype}] {detail}  ({status})")

    return 0


def cmd_impact():
    """Show cumulative impact of autonomous work."""
    if not ACTIONS_LOG.exists():
        print("No autonomous actions recorded yet.")
        return 0

    actions = []
    for line in ACTIONS_LOG.read_text().strip().split("\n"):
        if line.strip():
            try:
                actions.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not actions:
        print("No autonomous actions recorded yet.")
        return 0

    # Compute stats
    total_cycles = sum(1 for a in actions if a.get("type") == "cycle-complete")
    total_recs = sum(1 for a in actions if a.get("status") == "recommended")
    total_flags = sum(1 for a in actions if a.get("status") == "flagged")
    total_escalated = sum(1 for a in actions if a.get("status") == "escalated")

    # Financial impact from reorder recommendations
    total_reorder_cost = 0
    total_reorder_margin = 0
    reorder_count = 0
    for a in actions:
        if a.get("type") == "reorder-recommendation":
            impact = a.get("impact", {})
            total_reorder_cost += impact.get("cost", 0)
            total_reorder_margin += impact.get("margin", 0)
            reorder_count += 1

    # Type breakdown
    type_counts = {}
    for a in actions:
        atype = a.get("type", "unknown")
        type_counts[atype] = type_counts.get(atype, 0) + 1

    # Time range
    timestamps = [a.get("ts", "") for a in actions if a.get("ts")]
    first = min(timestamps) if timestamps else "?"
    last = max(timestamps) if timestamps else "?"

    # Average cycle time
    cycle_times = [a.get("total_seconds", 0) for a in actions if a.get("type") == "cycle-complete"]
    avg_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0

    # Count briefs
    briefs = list(BRIEFS_DIR.glob("*.md"))

    print(f"AUTONOMOUS WORK IMPACT")
    print(f"{'=' * 50}")
    print(f"Period:          {first[:10]} to {last[:10]}")
    print(f"Total cycles:    {total_cycles}")
    print(f"Avg cycle time:  {avg_cycle:.1f}s")
    print(f"Briefs written:  {len(briefs)}")
    print(f"")
    print(f"ACTIONS")
    print(f"  Recommendations: {total_recs}")
    print(f"  Flagged:         {total_flags}")
    print(f"  Escalated:       {total_escalated}")
    print(f"")
    print(f"FINANCIAL (reorder recommendations)")
    print(f"  Reorder recs:    {reorder_count}")
    print(f"  Total cost:      {total_reorder_cost:.0f}")
    print(f"  Potential margin: {total_reorder_margin:.0f}")
    print(f"")
    print(f"BY TYPE")
    for atype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {atype:<30} {count:>4}")

    return 0


# -- CLI Entry ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Kingdom Autonomous Work Cycle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Execute one autonomous cycle")
    run_p.add_argument("--dry-run", action="store_true", help="Show what would happen")

    sub.add_parser("history", help="Show past autonomous actions")
    sub.add_parser("impact", help="Show cumulative impact report")

    args = parser.parse_args()

    if args.command == "run":
        return cmd_run(dry_run=args.dry_run)
    elif args.command == "history":
        return cmd_history()
    elif args.command == "impact":
        return cmd_impact()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
