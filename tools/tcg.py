#!/usr/bin/env python3
"""
tcg.py -- Cambridge TCG Operations: The Kingdom's Bread

Beta's domain. Tracks inventory, pricing, margins, tasks, and opportunities
for Cambridge TCG Ltd -- the revenue engine that keeps the lights on.

Usage:
    python3 tools/tcg.py status              # Current business health
    python3 tools/tcg.py inventory           # Inventory status and alerts
    python3 tools/tcg.py pricing check       # Check pricing against market (manual input)
    python3 tools/tcg.py margin              # Margin analysis
    python3 tools/tcg.py targets             # Revenue targets vs actual
    python3 tools/tcg.py opportunities       # List optimization opportunities
    python3 tools/tcg.py tasks               # Operational task queue
    python3 tools/tcg.py task add "..."      # Add a task
    python3 tools/tcg.py task done <id>      # Complete a task
    python3 tools/tcg.py report [weekly]     # Generate business report
    python3 tools/tcg.py dashboard           # Full TCG dashboard
"""

import json
import sys
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# -- Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(__file__).resolve().parent.parent
TCG_DIR = LOVE / "memory" / "tcg"
STATUS_FILE = TCG_DIR / "status.json"
INVENTORY_FILE = TCG_DIR / "inventory.json"
TASKS_FILE = TCG_DIR / "tasks.json"
OPPORTUNITIES_FILE = TCG_DIR / "opportunities.json"
REPORTS_DIR = TCG_DIR / "reports"
KINGDOM_METRICS = LOVE / "memory" / "kingdom-metrics.json"

# Ensure dirs exist
TCG_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# -- Colors ───────────────────────────────────────────────────────────────────

class C:
    """ANSI color codes for terminal output."""
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"
    WHITE   = "\033[97m"

# -- Data I/O ─────────────────────────────────────────────────────────────────

def read_json(path: Path) -> dict | list:
    """Read JSON file, returning empty container on failure."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

def write_json(path: Path, data) -> None:
    """Write JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def fmt_gbp(amount: float) -> str:
    """Format amount as GBP string."""
    if amount < 0:
        return f"-\u00a3{abs(amount):,.2f}"
    return f"\u00a3{amount:,.2f}"

# -- Helpers ──────────────────────────────────────────────────────────────────

def bar(value: float, max_value: float, width: int = 30, fill_color: str = C.GREEN) -> str:
    """Generate a text-based progress bar."""
    if max_value <= 0:
        return f"{C.DIM}{'.' * width}{C.RESET}"
    ratio = min(value / max_value, 1.5)  # allow over 100% display
    filled = int(min(ratio, 1.0) * width)
    empty = width - filled
    pct = ratio * 100
    if ratio > 1.0:
        fill_color = C.GREEN  # over target is good for revenue
    return f"{fill_color}{'\u2588' * filled}{C.DIM}{'\u2591' * empty}{C.RESET} {pct:.0f}%"

def health_dot(status: str) -> str:
    """Color-coded health dot."""
    colors = {
        "in-stock": C.GREEN,
        "low-stock": C.YELLOW,
        "out-of-stock": C.RED,
        "good": C.GREEN,
        "warning": C.YELLOW,
        "critical": C.RED,
        "active": C.GREEN,
        "blocked": C.RED,
        "pending": C.YELLOW,
        "done": C.DIM,
        "actionable": C.GREEN,
        "planned": C.CYAN,
    }
    c = colors.get(status, C.DIM)
    return f"{c}\u25cf{C.RESET}"

def priority_badge(priority: str) -> str:
    """Color-coded priority badge."""
    colors = {"high": C.RED, "medium": C.YELLOW, "low": C.DIM}
    c = colors.get(priority, C.DIM)
    return f"{c}{priority.upper()}{C.RESET}"

def impact_badge(impact: str) -> str:
    """Color-coded impact badge."""
    colors = {"high": C.GREEN, "medium": C.YELLOW, "low": C.DIM}
    c = colors.get(impact, C.DIM)
    return f"{c}{impact.upper()}{C.RESET}"

def load_status() -> dict:
    return read_json(STATUS_FILE)

def load_inventory() -> list:
    data = read_json(INVENTORY_FILE)
    return data.get("categories", []) if isinstance(data, dict) else data

def load_tasks() -> list:
    data = read_json(TASKS_FILE)
    return data.get("tasks", []) if isinstance(data, dict) else data

def save_tasks(tasks: list) -> None:
    write_json(TASKS_FILE, {
        "updated": now_iso(),
        "tasks": tasks,
    })

def load_opportunities() -> list:
    data = read_json(OPPORTUNITIES_FILE)
    return data.get("opportunities", []) if isinstance(data, dict) else data

# -- Commands ─────────────────────────────────────────────────────────────────

def cmd_status():
    """One-line current business health."""
    st = load_status()
    inv = load_inventory()
    tasks = load_tasks()

    revenue = st.get("revenue_monthly", 0)
    target = st.get("revenue_target", 0)
    margin = st.get("margin_pct", 0)
    orders = st.get("orders_this_month", 0)

    # Health assessment
    rev_pct = (revenue / target * 100) if target > 0 else 0
    if rev_pct >= 95:
        health = f"{C.GREEN}STRONG{C.RESET}"
    elif rev_pct >= 80:
        health = f"{C.YELLOW}ON TRACK{C.RESET}"
    elif rev_pct >= 60:
        health = f"{C.YELLOW}BEHIND{C.RESET}"
    else:
        health = f"{C.RED}AT RISK{C.RESET}"

    low_stock = sum(1 for i in inv if i.get("status") == "low-stock")
    oos = sum(1 for i in inv if i.get("status") == "out-of-stock")
    pending_tasks = sum(1 for t in tasks if t.get("status") in ("pending", "blocked"))

    inv_warning = ""
    if oos > 0:
        inv_warning = f"  {C.RED}{oos} OOS{C.RESET}"
    elif low_stock > 0:
        inv_warning = f"  {C.YELLOW}{low_stock} low{C.RESET}"

    print(f"{C.BOLD}CAMBRIDGE TCG{C.RESET} {health}  "
          f"Rev {C.GREEN}{fmt_gbp(revenue)}{C.RESET}/{fmt_gbp(target)}  "
          f"Margin {C.BOLD}{margin:.0f}%{C.RESET}  "
          f"Orders {C.CYAN}{orders}{C.RESET}  "
          f"Tasks {C.YELLOW}{pending_tasks}{C.RESET}{inv_warning}")


def cmd_inventory():
    """Inventory status and alerts."""
    inv = load_inventory()

    print(f"\n{C.BOLD}INVENTORY STATUS{C.RESET}")
    print(f"{'=' * 80}")

    # Alerts first
    low_stock = [i for i in inv if i.get("status") == "low-stock"]
    oos = [i for i in inv if i.get("status") == "out-of-stock"]

    if oos:
        print(f"\n  {C.RED}{C.BOLD}OUT OF STOCK ({len(oos)}){C.RESET}")
        for item in oos:
            print(f"  {C.RED}\u25cf{C.RESET} {item['name']}")
            print(f"    {C.DIM}SKU: {item['sku']}  |  Reorder point: {item.get('reorder_point', '?')}{C.RESET}")

    if low_stock:
        print(f"\n  {C.YELLOW}{C.BOLD}LOW STOCK ({len(low_stock)}){C.RESET}")
        for item in low_stock:
            print(f"  {C.YELLOW}\u25cf{C.RESET} {item['name']}  —  {C.YELLOW}{item['qty']} remaining{C.RESET}")
            print(f"    {C.DIM}SKU: {item['sku']}  |  Reorder point: {item.get('reorder_point', '?')}  |  "
                  f"Supplier: {item.get('supplier', '?')}{C.RESET}")

    if not oos and not low_stock:
        print(f"\n  {C.GREEN}\u25cf No stock alerts{C.RESET}")

    # Full inventory table
    print(f"\n  {C.BOLD}{'SKU':<18} {'Name':<42} {'Qty':>5} {'Cost':>8} {'Price':>8} {'Margin':>7}{C.RESET}")
    print(f"  {'\u2500' * 92}")

    total_value = 0
    total_cost = 0
    by_category = defaultdict(lambda: {"qty": 0, "value": 0, "cost": 0})

    for item in sorted(inv, key=lambda x: x.get("category", "")):
        sku = item.get("sku", "?")
        name = item.get("name", "?")
        if len(name) > 40:
            name = name[:37] + "..."
        qty = item.get("qty", 0)
        cost = item.get("cost", 0)
        price = item.get("price", 0)
        margin = ((price - cost) / price * 100) if price > 0 else 0
        status = item.get("status", "?")
        cat = item.get("category", "other")

        total_value += qty * price
        total_cost += qty * cost
        by_category[cat]["qty"] += qty
        by_category[cat]["value"] += qty * price
        by_category[cat]["cost"] += qty * cost

        dot = health_dot(status)
        margin_color = C.GREEN if margin >= 50 else (C.YELLOW if margin >= 30 else C.RED)

        print(f"  {dot} {sku:<16} {name:<42} {qty:>5} {fmt_gbp(cost):>8} {fmt_gbp(price):>8} "
              f"{margin_color}{margin:.0f}%{C.RESET}")

    # Summary
    total_margin = ((total_value - total_cost) / total_value * 100) if total_value > 0 else 0
    print(f"  {'\u2500' * 92}")
    print(f"  {C.BOLD}{'TOTAL':<18} {'':42} {sum(i.get('qty', 0) for i in inv):>5} "
          f"{fmt_gbp(total_cost):>8} {fmt_gbp(total_value):>8} {total_margin:.0f}%{C.RESET}")

    # Category breakdown
    print(f"\n  {C.BOLD}BY CATEGORY{C.RESET}")
    print(f"  {'\u2500' * 60}")
    for cat, data in sorted(by_category.items(), key=lambda x: -x[1]["value"]):
        cat_margin = ((data["value"] - data["cost"]) / data["value"] * 100) if data["value"] > 0 else 0
        print(f"  {cat:<24} {data['qty']:>5} units  "
              f"Value {C.GREEN}{fmt_gbp(data['value']):>10}{C.RESET}  "
              f"Margin {cat_margin:.0f}%")

    print()


def cmd_pricing_check():
    """Pricing check guidance -- manual input for now."""
    inv = load_inventory()

    print(f"\n{C.BOLD}PRICING CHECK{C.RESET}")
    print(f"{'=' * 70}")
    print(f"\n  {C.DIM}Compare current prices against eBay sold listings and competitor sites.{C.RESET}")
    print(f"  {C.DIM}Update prices in memory/tcg/inventory.json after checking.{C.RESET}\n")

    print(f"  {C.BOLD}{'SKU':<18} {'Name':<36} {'Our Price':>10} {'Margin':>7}{C.RESET}")
    print(f"  {'\u2500' * 75}")

    for item in sorted(inv, key=lambda x: -(x.get("price", 0))):
        sku = item.get("sku", "?")
        name = item.get("name", "?")
        if len(name) > 34:
            name = name[:31] + "..."
        price = item.get("price", 0)
        cost = item.get("cost", 0)
        margin = ((price - cost) / price * 100) if price > 0 else 0
        margin_color = C.GREEN if margin >= 50 else (C.YELLOW if margin >= 30 else C.RED)

        print(f"  {sku:<18} {name:<36} {fmt_gbp(price):>10} {margin_color}{margin:.0f}%{C.RESET}")

    print(f"\n  {C.BOLD}CHECK AGAINST:{C.RESET}")
    print(f"  {C.CYAN}1.{C.RESET} eBay sold listings (search each SKU)")
    print(f"  {C.CYAN}2.{C.RESET} chaos-cards.com, magicmadhouse.co.uk, totalcards.net")
    print(f"  {C.CYAN}3.{C.RESET} TCGplayer market price (for singles)")
    print(f"\n  {C.BOLD}RULES:{C.RESET}")
    print(f"  - eBay: price to match or undercut top 3 by 1-3%")
    print(f"  - Website: price 5-8% below eBay (pass fee savings to customer)")
    print(f"  - Never drop below 40% margin on sealed, 50% on singles")
    print(f"  - Flag any SKU where we are >15% above market{C.RESET}")
    print()


def cmd_margin():
    """Margin analysis."""
    st = load_status()
    inv = load_inventory()

    revenue = st.get("revenue_monthly", 0)
    overall_margin = st.get("margin_pct", 0)
    channels = st.get("channels", {})

    print(f"\n{C.BOLD}MARGIN ANALYSIS{C.RESET}")
    print(f"{'=' * 65}")

    # Overall
    gross_profit = revenue * (overall_margin / 100)
    cogs = revenue - gross_profit
    print(f"\n  {C.BOLD}OVERALL{C.RESET}")
    print(f"  Revenue:       {C.GREEN}{fmt_gbp(revenue)}{C.RESET}/month")
    print(f"  COGS:          {C.RED}{fmt_gbp(cogs)}{C.RESET}/month")
    print(f"  Gross profit:  {C.GREEN}{C.BOLD}{fmt_gbp(gross_profit)}{C.RESET}/month")
    print(f"  Gross margin:  {C.BOLD}{overall_margin:.1f}%{C.RESET}  {bar(overall_margin, 100, 25, C.GREEN)}")

    # By channel
    print(f"\n  {C.BOLD}BY CHANNEL{C.RESET}")
    print(f"  {'\u2500' * 55}")

    for ch_name, ch_data in channels.items():
        rev_pct = ch_data.get("revenue_pct", 0)
        fees_pct = ch_data.get("fees_pct", 0)
        ch_revenue = revenue * (rev_pct / 100)
        ch_gross = ch_revenue * (overall_margin / 100)
        ch_fees = ch_revenue * (fees_pct / 100)
        ch_net = ch_gross - ch_fees
        ch_net_margin = (ch_net / ch_revenue * 100) if ch_revenue > 0 else 0

        ch_status = ch_data.get("status", "?")
        dot = health_dot(ch_status)

        print(f"\n  {dot} {C.BOLD}{ch_name.upper()}{C.RESET}  ({rev_pct}% of revenue)")
        print(f"    Revenue:     {fmt_gbp(ch_revenue)}")
        print(f"    Gross:       {fmt_gbp(ch_gross)}")
        print(f"    Fees ({fees_pct}%): {C.RED}-{fmt_gbp(ch_fees)}{C.RESET}")
        print(f"    Net profit:  {C.GREEN}{fmt_gbp(ch_net)}{C.RESET}  "
              f"({C.BOLD}{ch_net_margin:.1f}%{C.RESET} net margin)")

    # By product category
    print(f"\n  {C.BOLD}BY PRODUCT CATEGORY{C.RESET}")
    print(f"  {'\u2500' * 55}")

    by_cat = defaultdict(lambda: {"items": 0, "avg_margin": []})
    for item in inv:
        cat = item.get("category", "other")
        price = item.get("price", 0)
        cost = item.get("cost", 0)
        margin = ((price - cost) / price * 100) if price > 0 else 0
        by_cat[cat]["items"] += 1
        by_cat[cat]["avg_margin"].append(margin)

    for cat, data in sorted(by_cat.items(), key=lambda x: -(sum(x[1]["avg_margin"]) / len(x[1]["avg_margin"]) if x[1]["avg_margin"] else 0)):
        avg_m = sum(data["avg_margin"]) / len(data["avg_margin"]) if data["avg_margin"] else 0
        m_color = C.GREEN if avg_m >= 50 else (C.YELLOW if avg_m >= 30 else C.RED)
        print(f"  {cat:<24} {data['items']:>3} SKUs  "
              f"Avg margin: {m_color}{avg_m:.1f}%{C.RESET}  "
              f"{bar(avg_m, 100, 20, m_color)}")

    # Margin improvement levers
    print(f"\n  {C.BOLD}IMPROVEMENT LEVERS{C.RESET}")
    print(f"  {'\u2500' * 55}")

    ebay_fees = channels.get("ebay", {}).get("fees_pct", 12.8)
    web_fees = channels.get("website", {}).get("fees_pct", 2.9)
    ebay_rev_pct = channels.get("ebay", {}).get("revenue_pct", 72)
    fee_savings = revenue * (ebay_rev_pct / 100) * 0.10 * ((ebay_fees - web_fees) / 100)

    print(f"  1. Shift 10% eBay to website: saves ~{fmt_gbp(fee_savings)}/month in fees")
    print(f"  2. Bulk procurement: 5-8% better wholesale = ~{fmt_gbp(revenue * 0.36 * 0.065)}/month")
    print(f"  3. Singles grading: 3-5x markup on high-value pulls")
    print(f"  4. Bundle deals: increase AOV by 15-20%")
    print()


def cmd_targets():
    """Revenue targets vs actual."""
    st = load_status()

    revenue = st.get("revenue_monthly", 0)
    target = st.get("revenue_target", 0)
    orders = st.get("orders_this_month", 0)
    aov = st.get("avg_order_value", 0)

    gap = target - revenue
    pct = (revenue / target * 100) if target > 0 else 0

    print(f"\n{C.BOLD}REVENUE TARGETS{C.RESET}")
    print(f"{'=' * 60}")

    # Current vs target
    print(f"\n  {C.BOLD}MONTHLY{C.RESET}")
    print(f"  Current:    {C.GREEN}{C.BOLD}{fmt_gbp(revenue)}{C.RESET}")
    print(f"  Target:     {fmt_gbp(target)}  (+20% from baseline)")
    print(f"  Gap:        ", end="")
    if gap > 0:
        print(f"{C.RED}{fmt_gbp(gap)} to go{C.RESET}")
    else:
        print(f"{C.GREEN}TARGET MET (+{fmt_gbp(abs(gap))} over){C.RESET}")

    print(f"\n  Progress:   {bar(revenue, target, 35, C.CYAN)}")

    # Breakdown
    print(f"\n  {C.BOLD}METRICS{C.RESET}")
    print(f"  {'\u2500' * 45}")
    print(f"  Orders this month:    {C.CYAN}{orders}{C.RESET}")
    print(f"  Avg order value:      {fmt_gbp(aov)}")
    print(f"  Revenue/order:        {fmt_gbp(revenue / orders if orders > 0 else 0)}")

    # What it takes to hit target
    if gap > 0:
        orders_needed = int(gap / aov) + 1 if aov > 0 else 0
        days_left = max(1, 30 - datetime.now(timezone.utc).day)
        orders_per_day = orders_needed / days_left if days_left > 0 else 0

        print(f"\n  {C.BOLD}TO HIT TARGET{C.RESET}")
        print(f"  {'\u2500' * 45}")
        print(f"  Additional revenue:   {C.YELLOW}{fmt_gbp(gap)}{C.RESET}")
        print(f"  At current AOV:       {C.YELLOW}{orders_needed} more orders{C.RESET}")
        print(f"  Days remaining:       {days_left}")
        print(f"  Orders/day needed:    {C.BOLD}{orders_per_day:.1f}{C.RESET}")

        # Paths to target
        print(f"\n  {C.BOLD}PATHS TO +20%{C.RESET}")
        print(f"  {'\u2500' * 45}")
        print(f"  {C.CYAN}A.{C.RESET} Increase order volume 20% (more listings, better SEO)")
        print(f"  {C.CYAN}B.{C.RESET} Increase AOV 20% (bundles, upsells, premium products)")
        print(f"  {C.CYAN}C.{C.RESET} Mix: +10% volume, +10% AOV (most realistic)")
        print(f"  {C.CYAN}D.{C.RESET} Unlock eBay auto-sync (reclaim 3-4hrs/week for growth)")

    print()


def cmd_opportunities():
    """List optimization opportunities."""
    opps = load_opportunities()

    print(f"\n{C.BOLD}OPTIMIZATION OPPORTUNITIES{C.RESET}")
    print(f"{'=' * 75}")

    total_delta = sum(o.get("estimated_revenue_delta", 0) for o in opps)
    actionable = [o for o in opps if o.get("status") == "actionable"]
    blocked = [o for o in opps if o.get("status") == "blocked"]

    print(f"\n  Total potential:     {C.GREEN}{C.BOLD}+{fmt_gbp(total_delta)}/month{C.RESET}")
    print(f"  Actionable now:      {C.GREEN}{len(actionable)}{C.RESET}")
    print(f"  Blocked:             {C.RED}{len(blocked)}{C.RESET}")

    # Sort: blocked first (need attention), then actionable, then planned
    status_order = {"blocked": 0, "actionable": 1, "planned": 2, "done": 3}
    for opp in sorted(opps, key=lambda x: (status_order.get(x.get("status", ""), 9), -x.get("estimated_revenue_delta", 0))):
        opp_id = opp.get("id", "?")
        desc = opp.get("description", "?")
        impact = opp.get("impact", "?")
        effort = opp.get("effort", "?")
        delta = opp.get("estimated_revenue_delta", 0)
        status = opp.get("status", "?")
        blocker = opp.get("blocker")

        dot = health_dot(status)
        # Truncate description for display
        short_desc = desc.split(".")[0] if "." in desc else desc
        if len(short_desc) > 65:
            short_desc = short_desc[:62] + "..."

        print(f"\n  {dot} {C.BOLD}{opp_id}{C.RESET}  {short_desc}")
        print(f"    Impact: {impact_badge(impact)}  "
              f"Effort: {C.DIM}{effort}{C.RESET}  "
              f"Delta: {C.GREEN}+{fmt_gbp(delta)}/mo{C.RESET}  "
              f"Status: {status}")
        if blocker:
            print(f"    {C.RED}BLOCKED: {blocker}{C.RESET}")

    print()


def cmd_tasks():
    """Operational task queue."""
    tasks = load_tasks()

    pending = [t for t in tasks if t.get("status") == "pending"]
    blocked = [t for t in tasks if t.get("status") == "blocked"]
    done_recent = [t for t in tasks if t.get("status") == "done"
                   and t.get("completed", "") >= (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")]

    print(f"\n{C.BOLD}TCG TASK QUEUE{C.RESET}")
    print(f"{'=' * 75}")
    print(f"  {C.RED}{len(blocked)} blocked{C.RESET}  |  "
          f"{C.YELLOW}{len(pending)} pending{C.RESET}  |  "
          f"{C.GREEN}{len(done_recent)} done (7d){C.RESET}")

    if blocked:
        print(f"\n  {C.RED}{C.BOLD}BLOCKED{C.RESET}")
        for t in blocked:
            print(f"  {C.RED}\u25cf{C.RESET} [{t['id']}] {priority_badge(t.get('priority', 'medium'))}  {t['task']}")
            tags = t.get("tags", [])
            if tags:
                print(f"    {C.DIM}Tags: {', '.join(tags)}  |  Created: {t.get('created', '?')}{C.RESET}")

    if pending:
        print(f"\n  {C.YELLOW}{C.BOLD}PENDING{C.RESET}")
        # Sort by priority
        prio_order = {"high": 0, "medium": 1, "low": 2}
        for t in sorted(pending, key=lambda x: prio_order.get(x.get("priority", "medium"), 9)):
            print(f"  {C.YELLOW}\u25cf{C.RESET} [{t['id']}] {priority_badge(t.get('priority', 'medium'))}  {t['task']}")
            tags = t.get("tags", [])
            if tags:
                print(f"    {C.DIM}Tags: {', '.join(tags)}  |  Created: {t.get('created', '?')}{C.RESET}")

    if done_recent:
        print(f"\n  {C.GREEN}{C.BOLD}RECENTLY DONE{C.RESET}")
        for t in done_recent:
            print(f"  {C.DIM}\u25cf [{t['id']}] {t['task']}  (completed {t.get('completed', '?')}){C.RESET}")

    print()


def cmd_task_add(args: list):
    """Add a task to the queue."""
    if not args:
        print(f"{C.RED}Usage: tcg.py task add \"description\" [--priority high|medium|low] [--tags tag1,tag2]{C.RESET}")
        sys.exit(1)

    description = args[0]
    priority = "medium"
    tags = []

    i = 1
    while i < len(args):
        if args[i] == "--priority" and i + 1 < len(args):
            priority = args[i + 1].lower()
            i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            tags = [t.strip() for t in args[i + 1].split(",")]
            i += 2
        else:
            i += 1

    tasks = load_tasks()

    # Generate next task ID
    max_num = 0
    for t in tasks:
        tid = t.get("id", "")
        if tid.startswith("tcg-"):
            try:
                num = int(tid.split("-")[1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass

    task_id = f"tcg-{max_num + 1:03d}"

    new_task = {
        "id": task_id,
        "task": description,
        "priority": priority,
        "status": "pending",
        "created": now_date(),
        "completed": None,
        "tags": tags,
    }

    tasks.append(new_task)
    save_tasks(tasks)

    print(f"{C.GREEN}+{C.RESET} Task added: {C.BOLD}{task_id}{C.RESET}")
    print(f"  {description}")
    print(f"  Priority: {priority_badge(priority)}  |  Tags: {', '.join(tags) if tags else 'none'}")


def cmd_task_done(args: list):
    """Mark a task as done."""
    if not args:
        print(f"{C.RED}Usage: tcg.py task done <task-id>{C.RESET}")
        sys.exit(1)

    task_id = args[0]
    tasks = load_tasks()
    found = False

    for t in tasks:
        if t.get("id") == task_id:
            t["status"] = "done"
            t["completed"] = now_date()
            found = True
            print(f"{C.GREEN}\u2713{C.RESET} Task completed: {C.BOLD}{task_id}{C.RESET}")
            print(f"  {C.DIM}{t['task']}{C.RESET}")
            break

    if not found:
        print(f"{C.RED}Task not found: {task_id}{C.RESET}")
        print(f"  Available: {', '.join(t['id'] for t in tasks if t.get('status') != 'done')}")
        sys.exit(1)

    save_tasks(tasks)


def cmd_report(args: list):
    """Generate business report (markdown)."""
    report_type = args[0] if args else "weekly"

    st = load_status()
    inv = load_inventory()
    tasks = load_tasks()
    opps = load_opportunities()

    revenue = st.get("revenue_monthly", 0)
    target = st.get("revenue_target", 0)
    margin = st.get("margin_pct", 0)
    orders = st.get("orders_this_month", 0)
    aov = st.get("avg_order_value", 0)

    low_stock = [i for i in inv if i.get("status") == "low-stock"]
    oos = [i for i in inv if i.get("status") == "out-of-stock"]
    pending_tasks = [t for t in tasks if t.get("status") == "pending"]
    blocked_tasks = [t for t in tasks if t.get("status") == "blocked"]
    done_tasks = [t for t in tasks if t.get("status") == "done"
                  and t.get("completed", "") >= (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")]
    actionable_opps = [o for o in opps if o.get("status") == "actionable"]

    today = now_date()
    report_name = f"{report_type}-{today}.md"

    lines = []
    lines.append(f"# Cambridge TCG {report_type.title()} Report -- {today}")
    lines.append("")
    lines.append("## Revenue")
    lines.append(f"- **Current:** {fmt_gbp(revenue).replace(chr(163), 'GBP ')}/month")
    lines.append(f"- **Target:** {fmt_gbp(target).replace(chr(163), 'GBP ')}/month (+20%)")
    lines.append(f"- **Progress:** {revenue / target * 100 if target else 0:.0f}%")
    lines.append(f"- **Margin:** {margin:.0f}%")
    lines.append(f"- **Orders:** {orders} this month (AOV {fmt_gbp(aov).replace(chr(163), 'GBP ')})")
    lines.append("")
    lines.append("## Inventory Alerts")
    if oos:
        for item in oos:
            lines.append(f"- **OUT OF STOCK:** {item['name']} ({item['sku']})")
    if low_stock:
        for item in low_stock:
            lines.append(f"- **LOW STOCK:** {item['name']} -- {item['qty']} remaining (reorder point: {item.get('reorder_point', '?')})")
    if not oos and not low_stock:
        lines.append("- No stock alerts")
    lines.append("")
    lines.append("## Tasks")
    if blocked_tasks:
        lines.append(f"### Blocked ({len(blocked_tasks)})")
        for t in blocked_tasks:
            lines.append(f"- [{t['id']}] {t['task']}")
    if pending_tasks:
        lines.append(f"### Pending ({len(pending_tasks)})")
        for t in sorted(pending_tasks, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("priority", "medium"), 9)):
            lines.append(f"- [{t['id']}] ({t.get('priority', 'medium')}) {t['task']}")
    if done_tasks:
        lines.append(f"### Completed This Week ({len(done_tasks)})")
        for t in done_tasks:
            lines.append(f"- [{t['id']}] {t['task']}")
    lines.append("")
    lines.append("## Top Opportunities")
    for opp in sorted(opps, key=lambda x: -x.get("estimated_revenue_delta", 0))[:3]:
        lines.append(f"- **{opp['id']}** ({opp.get('impact', '?')} impact): {opp['description'].split('.')[0]}")
        delta = opp.get("estimated_revenue_delta", 0)
        lines.append(f"  - Potential: +GBP {delta}/month | Status: {opp.get('status', '?')}")
    lines.append("")
    lines.append("## Next Actions")
    if blocked_tasks:
        lines.append(f"1. Unblock: {blocked_tasks[0]['task'].split(' -- ')[0] if ' -- ' in blocked_tasks[0].get('task', '') else blocked_tasks[0].get('task', '')[:60]}")
    if actionable_opps:
        lines.append(f"{'2' if blocked_tasks else '1'}. Execute: {actionable_opps[0]['description'].split(':')[0]}")
    if low_stock:
        n = 2 + (1 if blocked_tasks else 0)
        lines.append(f"{n}. Restock: {low_stock[0]['name']}")
    lines.append("")
    lines.append(f"---\n_Generated by Beta at {now_iso()}_")

    report_content = "\n".join(lines)
    report_path = REPORTS_DIR / report_name
    report_path.write_text(report_content + "\n")

    print(f"{C.GREEN}\u2713{C.RESET} Report generated: {C.BOLD}{report_path.relative_to(LOVE)}{C.RESET}")
    print(f"\n{report_content}")


def cmd_dashboard():
    """Full TCG dashboard."""
    st = load_status()
    inv = load_inventory()
    tasks = load_tasks()
    opps = load_opportunities()

    revenue = st.get("revenue_monthly", 0)
    target = st.get("revenue_target", 0)
    margin = st.get("margin_pct", 0)
    orders = st.get("orders_this_month", 0)
    aov = st.get("avg_order_value", 0)
    channels = st.get("channels", {})

    rev_pct = (revenue / target * 100) if target > 0 else 0
    gap = target - revenue
    gross_profit = revenue * (margin / 100)

    # Header
    print()
    print(f"  {C.BOLD}{C.CYAN}\u2554{'=' * 62}\u2557{C.RESET}")
    print(f"  {C.BOLD}{C.CYAN}\u2551{C.RESET}       {C.BOLD}CAMBRIDGE TCG OPERATIONS DASHBOARD{C.RESET}              {C.BOLD}{C.CYAN}\u2551{C.RESET}")
    print(f"  {C.BOLD}{C.CYAN}\u2551{C.RESET}       {C.DIM}The Kingdom's Bread  --  Beta's Domain{C.RESET}           {C.BOLD}{C.CYAN}\u2551{C.RESET}")
    print(f"  {C.BOLD}{C.CYAN}\u255a{'=' * 62}\u255d{C.RESET}")

    # Health
    if rev_pct >= 95:
        health = f"{C.GREEN}\u25cf STRONG{C.RESET}"
    elif rev_pct >= 80:
        health = f"{C.YELLOW}\u25cf ON TRACK{C.RESET}"
    elif rev_pct >= 60:
        health = f"{C.YELLOW}\u25cf BEHIND{C.RESET}"
    else:
        health = f"{C.RED}\u25cf AT RISK{C.RESET}"
    print(f"\n  Status: {health}  |  {now_date()}")

    # -- Revenue vs Target --
    print(f"\n  {C.BOLD}REVENUE vs TARGET{C.RESET}")
    print(f"  {'\u2500' * 55}")
    print(f"  Current:      {C.GREEN}{C.BOLD}{fmt_gbp(revenue)}{C.RESET}/month")
    print(f"  Target:       {fmt_gbp(target)}/month  (+20%)")
    if gap > 0:
        print(f"  Gap:          {C.RED}{fmt_gbp(gap)} to go{C.RESET}")
    else:
        print(f"  Gap:          {C.GREEN}TARGET MET (+{fmt_gbp(abs(gap))}){C.RESET}")
    print(f"  Progress:     {bar(revenue, target, 35, C.CYAN)}")

    # -- Key Metrics --
    print(f"\n  {C.BOLD}KEY METRICS{C.RESET}")
    print(f"  {'\u2500' * 55}")
    print(f"  Gross margin:      {C.BOLD}{margin:.0f}%{C.RESET}  {bar(margin, 100, 20, C.GREEN)}")
    print(f"  Gross profit:      {C.GREEN}{fmt_gbp(gross_profit)}{C.RESET}/month")
    print(f"  Orders:            {C.CYAN}{orders}{C.RESET} this month")
    print(f"  Avg order value:   {fmt_gbp(aov)}")

    # -- Channels --
    print(f"\n  {C.BOLD}CHANNELS{C.RESET}")
    print(f"  {'\u2500' * 55}")
    for ch_name, ch_data in channels.items():
        rev_pct_ch = ch_data.get("revenue_pct", 0)
        fees = ch_data.get("fees_pct", 0)
        ch_rev = revenue * (rev_pct_ch / 100)
        net_margin = margin - fees
        dot = health_dot(ch_data.get("status", "active"))
        print(f"  {dot} {ch_name.upper():<12} {fmt_gbp(ch_rev):>10}  "
              f"({rev_pct_ch}% rev, {fees}% fees)  "
              f"Net margin: {C.BOLD}{net_margin:.1f}%{C.RESET}")

    # -- Inventory Health --
    print(f"\n  {C.BOLD}INVENTORY HEALTH{C.RESET}")
    print(f"  {'\u2500' * 55}")

    total_skus = len(inv)
    in_stock = sum(1 for i in inv if i.get("status") == "in-stock")
    low_stock = [i for i in inv if i.get("status") == "low-stock"]
    oos = [i for i in inv if i.get("status") == "out-of-stock"]
    total_units = sum(i.get("qty", 0) for i in inv)
    total_value = sum(i.get("qty", 0) * i.get("price", 0) for i in inv)

    print(f"  SKUs:     {C.CYAN}{total_skus}{C.RESET}  "
          f"({C.GREEN}{in_stock} OK{C.RESET}, "
          f"{C.YELLOW}{len(low_stock)} low{C.RESET}, "
          f"{C.RED}{len(oos)} OOS{C.RESET})")
    print(f"  Units:    {total_units}")
    print(f"  Value:    {C.GREEN}{fmt_gbp(total_value)}{C.RESET} (at retail)")

    if low_stock:
        print(f"\n  {C.YELLOW}Low stock alerts:{C.RESET}")
        for item in low_stock:
            print(f"    {C.YELLOW}\u25cf{C.RESET} {item['name']}  --  {item['qty']} left (reorder: {item.get('reorder_point', '?')})")
    if oos:
        print(f"\n  {C.RED}Out of stock:{C.RESET}")
        for item in oos:
            print(f"    {C.RED}\u25cf{C.RESET} {item['name']}")

    # -- Task Queue --
    pending_tasks = [t for t in tasks if t.get("status") in ("pending", "blocked")]
    blocked_tasks = [t for t in tasks if t.get("status") == "blocked"]
    done_count = sum(1 for t in tasks if t.get("status") == "done")

    print(f"\n  {C.BOLD}TASK QUEUE{C.RESET}")
    print(f"  {'\u2500' * 55}")
    print(f"  {C.RED}{len(blocked_tasks)} blocked{C.RESET}  |  "
          f"{C.YELLOW}{len(pending_tasks)} active{C.RESET}  |  "
          f"{C.GREEN}{done_count} done{C.RESET}")

    prio_order = {"high": 0, "medium": 1, "low": 2}
    top_tasks = sorted(pending_tasks, key=lambda x: prio_order.get(x.get("priority", "medium"), 9))[:5]
    for t in top_tasks:
        status = t.get("status", "?")
        dot = health_dot(status)
        task_text = t.get("task", "?")
        if len(task_text) > 55:
            task_text = task_text[:52] + "..."
        print(f"  {dot} [{t['id']}] {priority_badge(t.get('priority', 'medium'))}  {task_text}")

    # -- Top Opportunities --
    print(f"\n  {C.BOLD}TOP OPPORTUNITIES{C.RESET}")
    print(f"  {'\u2500' * 55}")

    total_potential = sum(o.get("estimated_revenue_delta", 0) for o in opps)
    print(f"  Total potential: {C.GREEN}+{fmt_gbp(total_potential)}/month{C.RESET}")

    top_opps = sorted(opps, key=lambda x: -x.get("estimated_revenue_delta", 0))[:3]
    for opp in top_opps:
        dot = health_dot(opp.get("status", "?"))
        desc = opp.get("description", "?").split(".")[0]
        if len(desc) > 50:
            desc = desc[:47] + "..."
        delta = opp.get("estimated_revenue_delta", 0)
        print(f"  {dot} {C.BOLD}{opp['id']}{C.RESET}  +{fmt_gbp(delta)}/mo  {desc}")

    # -- Footer --
    print(f"\n  {C.DIM}{'\u2500' * 55}{C.RESET}")
    print(f"  {C.DIM}Cambridge TCG Ltd  |  Owner: Beta  |  Wall 3 (Engine){C.RESET}")
    print(f"  {C.DIM}Channels: eBay + zerone-dev.com (Vercel)  |  Updated: {now_iso()}{C.RESET}")
    print()


# -- CLI ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "status":        ("Current business health",           lambda a: cmd_status()),
    "inventory":     ("Inventory status and alerts",       lambda a: cmd_inventory()),
    "pricing":       ("Pricing check (pricing check)",     lambda a: cmd_pricing_check()),
    "margin":        ("Margin analysis",                   lambda a: cmd_margin()),
    "targets":       ("Revenue targets vs actual",         lambda a: cmd_targets()),
    "opportunities": ("Optimization opportunities",        lambda a: cmd_opportunities()),
    "tasks":         ("Operational task queue",             lambda a: cmd_tasks()),
    "task":          ("Task management (add/done)",        lambda a: handle_task_subcommand(a)),
    "report":        ("Generate business report [weekly]", lambda a: cmd_report(a)),
    "dashboard":     ("Full TCG dashboard",                lambda a: cmd_dashboard()),
}

def handle_task_subcommand(args: list):
    """Route task subcommands."""
    if not args:
        cmd_tasks()
        return

    sub = args[0]
    sub_args = args[1:]

    if sub == "add":
        cmd_task_add(sub_args)
    elif sub == "done":
        cmd_task_done(sub_args)
    else:
        print(f"{C.RED}Unknown task subcommand: {sub}{C.RESET}")
        print(f"  Available: {C.CYAN}add{C.RESET}, {C.CYAN}done{C.RESET}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(f"\n{C.BOLD}tcg.py{C.RESET} -- Cambridge TCG Operations\n")
        print(f"  {C.DIM}The Kingdom's Bread. Beta's domain. Every card counted, every margin tracked.{C.RESET}\n")
        for cmd, (desc, _) in COMMANDS.items():
            print(f"  {C.CYAN}{cmd:<16}{C.RESET} {desc}")
        print(f"\n  {C.DIM}Task management:{C.RESET}")
        print(f"  {C.CYAN}{'task add':16}{C.RESET} Add a task: tcg.py task add \"description\" [--priority high]")
        print(f"  {C.CYAN}{'task done':16}{C.RESET} Complete a task: tcg.py task done <task-id>")
        print()
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    # Handle "pricing check" as compound command
    if cmd == "pricing" and args and args[0] == "check":
        cmd_pricing_check()
        return

    if cmd not in COMMANDS:
        print(f"{C.RED}Unknown command: {cmd}{C.RESET}")
        print(f"Run {C.CYAN}tcg.py help{C.RESET} for available commands")
        sys.exit(1)

    COMMANDS[cmd][1](args)


if __name__ == "__main__":
    main()
