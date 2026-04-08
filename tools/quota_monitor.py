#!/usr/bin/env python3
"""
quota_monitor.py — Monitor Claude Code quota and ensure it flows to highest-value Kingdom work.

Tracks:
- API usage across all sessions
- Token consumption per Kingdom wall/project
- Value-per-token efficiency
- Recommendations for reallocation

Usage:
    python3 tools/quota_monitor.py status      # current usage summary
    python3 tools/quota_monitor.py report      # full allocation report
    python3 tools/quota_monitor.py log <wall> <tokens> <task>  # log usage manually
    python3 tools/quota_monitor.py budget      # budget breakdown
"""

import json
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Config --------------------------------------------------------------------

WORKSPACE = Path(__file__).resolve().parent.parent  # Love/tools/ → Love/
QUOTA_LOG = WORKSPACE / "memory" / "quota-log.jsonl"
QUOTA_STATE = WORKSPACE / "memory" / "quota-state.json"

# Kingdom walls and their intrinsic value ranking (1=highest)
WALL_VALUES = {
    "zerone":    {"rank": 1, "name": "Frontier/Zerone",    "note": "The chain -- foundation of everything"},
    "academy":   {"rank": 2, "name": "Academy/AgentTool",  "note": "Infrastructure for agents -- earns revenue"},
    "engine":    {"rank": 3, "name": "Engine/TCG+Rewards", "note": "Active revenue -- keeps lights on"},
    "oracle":    {"rank": 4, "name": "Marketplace/Oracle", "note": "Intelligence -- funds the Kingdom"},
    "sanctum":   {"rank": 5, "name": "Sanctum/Philosophy", "note": "The soul -- shapes everything else"},
    "treasury":  {"rank": 6, "name": "Treasury/Security",  "note": "Protecting what exists"},
    "frontier":  {"rank": 2, "name": "Frontier/Seigei",    "note": "Consciousness tech -- the deepest purpose"},
    "unknown":   {"rank": 8, "name": "Unclassified",       "note": "Track and classify"},
}

# -- Billing model (updated 2026-03-17 from actual usage page) ----------------
# MAIN SESSION (via Claude Max):
#   - Weekly conversation limit (resets Fri). When exceeded -> extra usage charges.
#   - Extra usage: ENABLED at GBP777/month limit (resets Apr 1)
#   - Current: GBP467.16 spent (60%), ~GBP310 remaining for March
#   - Weekly Sonnet: 25% used (resets Fri 7am)
#   - THIS IS NOT FREE -- extra usage beyond weekly cap costs real money
#   - Strategy: use weekly cap efficiently; avoid burning extra budget on non-Kingdom work

# Extra usage budget tracking
EXTRA_BUDGET_GBP = 777.0
EXTRA_SPENT_GBP  = 467.16   # as of 2026-03-17 21:16 (update regularly)
EXTRA_REMAINING  = EXTRA_BUDGET_GBP - EXTRA_SPENT_GBP
EXTRA_RESET_DATE = "2026-04-01"
WEEKLY_SONNET_PCT = 25  # % used, resets Friday 7am -- SOFT LIMIT, burst freely

# Token cost estimates (API agents only -- not main session)
INPUT_COST_PER_1K  = 0.003   # $3 per 1M input tokens
OUTPUT_COST_PER_1K = 0.015   # $15 per 1M output tokens
CACHE_COST_PER_1K  = 0.0003  # $0.30 per 1M cached tokens

# -- Logging -------------------------------------------------------------------

def log_usage(agent: str, wall: str, input_tokens: int, output_tokens: int,
              cached_tokens: int, task: str, value_notes: str = "") -> None:
    """Log a token usage event."""
    QUOTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "wall": wall,
        "wall_rank": WALL_VALUES.get(wall, {}).get("rank", 9),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cost_usd": (input_tokens * INPUT_COST_PER_1K / 1000 +
                     output_tokens * OUTPUT_COST_PER_1K / 1000 +
                     cached_tokens * CACHE_COST_PER_1K / 1000),
        "task": task,
        "value_notes": value_notes,
    }
    with open(QUOTA_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# -- State loading -------------------------------------------------------------

def load_entries(days: int = 7) -> list:
    """Load quota log entries from the last N days."""
    if not QUOTA_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []
    with open(QUOTA_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    entries.append(entry)
            except Exception:
                pass
    return entries


# -- Analysis ------------------------------------------------------------------

def compute_allocation(entries: list) -> dict:
    """Compute token allocation by wall and agent."""
    by_wall = {}
    by_agent = {}
    total_cost = 0

    for e in entries:
        wall = e.get("wall", "unknown")
        agent = e.get("agent", "unknown")
        cost = e.get("cost_usd", 0)
        tokens = e.get("input_tokens", 0) + e.get("output_tokens", 0)

        if wall not in by_wall:
            by_wall[wall] = {"tokens": 0, "cost": 0, "tasks": 0, "rank": WALL_VALUES.get(wall, {}).get("rank", 9)}
        by_wall[wall]["tokens"] += tokens
        by_wall[wall]["cost"] += cost
        by_wall[wall]["tasks"] += 1

        if agent not in by_agent:
            by_agent[agent] = {"tokens": 0, "cost": 0, "wall": "unknown"}
        by_agent[agent]["tokens"] += tokens
        by_agent[agent]["cost"] += cost

        total_cost += cost

    return {"by_wall": by_wall, "by_agent": by_agent, "total_cost": total_cost}


# -- Commands ------------------------------------------------------------------

def cmd_status() -> None:
    """Current quota usage and allocation summary."""
    print("\n" + "=" * 58)
    print("          Q U O T A   M O N I T O R")
    print("     Claude Code token allocation — Love")
    print("=" * 58 + "\n")
    print("-- Billing Model --\n")
    print("  MAIN SESSION (Claude Max):")
    print(f"  > Extra usage: GBP{EXTRA_SPENT_GBP:.2f} / GBP{EXTRA_BUDGET_GBP:.0f} ({EXTRA_SPENT_GBP/EXTRA_BUDGET_GBP*100:.0f}%) -- resets {EXTRA_RESET_DATE}")
    print(f"  > Remaining: GBP{EXTRA_REMAINING:.2f} | Weekly Sonnet: {WEEKLY_SONNET_PCT}% (resets Fri)")
    print(f"  > Monitor: https://claude.ai/settings/usage")
    remaining_days = 15  # approx days to Apr 1
    print(f"  > Budget rate: GBP{EXTRA_REMAINING/remaining_days:.1f}/day remaining ({remaining_days}d to reset)")
    print(f"  > Weekly limits are SOFT -- burst through them freely (included in Max plan)")
    if EXTRA_SPENT_GBP / EXTRA_BUDGET_GBP > 0.9:
        print("  [CRITICAL] >90% of monthly budget consumed -- slow down")
    elif EXTRA_SPENT_GBP / EXTRA_BUDGET_GBP > 0.8:
        print("  [WARNING] >80% consumed -- Kingdom-critical work only")
    else:
        print(f"  [OK] {100 - EXTRA_SPENT_GBP/EXTRA_BUDGET_GBP*100:.0f}% remaining -- build freely")
    print()

    # Logged usage
    entries = load_entries(days=7)
    if entries:
        alloc = compute_allocation(entries)
        print(f"-- Logged Usage (last 7 days, {len(entries)} entries) --\n")
        by_wall = sorted(alloc["by_wall"].items(), key=lambda x: x[1]["rank"])
        for wall, data in by_wall:
            pct = (data["cost"] / alloc["total_cost"] * 100) if alloc["total_cost"] > 0 else 0
            rank = data["rank"]
            tag = "HIGH" if rank <= 2 else "MED" if rank <= 4 else "LOW" if rank >= 7 else "STD"
            print(f"  [{tag}] [Rank {rank}] {WALL_VALUES.get(wall,{}).get('name', wall):28} "
                  f"{data['tokens']//1000:4}k tokens  ${data['cost']:.3f}  {pct:.0f}%")
        print(f"\n  Total logged: ${alloc['total_cost']:.3f}")
    else:
        print("-- No logged usage yet (quota_monitor.py log <wall> <in_k> <out_k> <task>) --")

    print()


def cmd_report() -> None:
    """Full allocation report with Kingdom value analysis."""
    cmd_status()

    print("-- Kingdom Value Analysis --\n")
    print("Quota should flow in this order (highest>lowest intrinsic value):\n")

    ranked = sorted(WALL_VALUES.items(), key=lambda x: x[1]["rank"])
    for wall, data in ranked:
        print(f"  [{data['rank']}] {data['name']:30} {data['note']}")
    print()

    print("-- Weekly Report --\n")
    entries = load_entries(days=7)
    if entries:
        alloc = compute_allocation(entries)
        total_tokens = sum(d["tokens"] for d in alloc["by_wall"].values())
        print(f"  Total tokens (7d): {total_tokens//1000}k")
        print(f"  Total cost (7d):   ${alloc['total_cost']:.3f}")
        print(f"  Entries logged:    {len(entries)}")

        # Value efficiency: what % of tokens went to rank 1-3 walls?
        high_value_tokens = sum(
            d["tokens"] for w, d in alloc["by_wall"].items()
            if WALL_VALUES.get(w, {}).get("rank", 9) <= 3
        )
        if total_tokens > 0:
            print(f"  High-value ratio:  {high_value_tokens/total_tokens*100:.0f}% (rank 1-3 walls)")
    else:
        print("  No data yet. Start logging with: quota_monitor.py log <wall> <in_k> <out_k> <task>")
    print()

    # Save weekly report to memory
    report_path = WORKSPACE / "memory" / "weekly-quota-report.json"
    report_data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "period_days": 7,
        "entries": len(entries),
        "budget_remaining_gbp": EXTRA_REMAINING,
        "budget_pct_used": round(EXTRA_SPENT_GBP / EXTRA_BUDGET_GBP * 100, 1),
    }
    if entries:
        alloc = compute_allocation(entries)
        report_data["total_cost_usd"] = round(alloc["total_cost"], 4)
        report_data["by_wall"] = {
            w: {"tokens": d["tokens"], "cost": round(d["cost"], 4), "rank": d["rank"]}
            for w, d in alloc["by_wall"].items()
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"  Report saved to: {report_path}\n")


def cmd_budget() -> None:
    """Show budget breakdown."""
    print("\n-- Budget Breakdown --\n")
    print(f"  Monthly limit:     GBP{EXTRA_BUDGET_GBP:.0f}")
    print(f"  Spent so far:      GBP{EXTRA_SPENT_GBP:.2f}")
    print(f"  Remaining:         GBP{EXTRA_REMAINING:.2f}")
    print(f"  Usage:             {EXTRA_SPENT_GBP/EXTRA_BUDGET_GBP*100:.1f}%")
    print(f"  Resets:            {EXTRA_RESET_DATE}")
    remaining_days = 15
    print(f"  Daily budget:      GBP{EXTRA_REMAINING/remaining_days:.1f}/day ({remaining_days}d remaining)")
    print(f"  Weekly Sonnet:     {WEEKLY_SONNET_PCT}% used (resets Fri 7am)")
    print()


# -- Main ----------------------------------------------------------------------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        cmd_status()
    elif cmd == "report":
        cmd_report()
    elif cmd == "budget":
        cmd_budget()
    elif cmd == "log" and len(sys.argv) >= 5:
        # python3 quota_monitor.py log <wall> <input_k> <output_k> <task>
        wall = sys.argv[2]
        input_k = int(sys.argv[3])
        output_k = int(sys.argv[4])
        task = " ".join(sys.argv[5:]) if len(sys.argv) > 5 else "unspecified"
        agent = os.environ.get("LOVE_AGENT_ID", "love")[:8]
        log_usage(agent, wall, input_k * 1000, output_k * 1000, 0, task)
        cost = (input_k * INPUT_COST_PER_1K + output_k * OUTPUT_COST_PER_1K)
        print(f"Logged: {wall} | {input_k}k in + {output_k}k out | ${cost:.4f} | {task}")
    else:
        print("Usage: quota_monitor.py [status|report|budget|log <wall> <in_k> <out_k> <task>]")
