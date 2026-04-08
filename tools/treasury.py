#!/usr/bin/env python3
"""
treasury.py — Kingdom Treasury: Revenue & Financial Tracking

Tithe's domain. Tracks every pound earned, every token spent, every cost incurred.
This is the Kingdom's financial nerve center.

Usage:
    python3 tools/treasury.py status                    # One-line financial health
    python3 tools/treasury.py revenue                   # Revenue by engine
    python3 tools/treasury.py costs                     # Costs by category
    python3 tools/treasury.py add-revenue <engine> <amount> [--currency GBP] [--date YYYY-MM-DD] [--notes "..."]
    python3 tools/treasury.py add-cost <description> <amount> [--currency USD] [--category compute] [--date YYYY-MM-DD]
    python3 tools/treasury.py pnl                       # Profit/loss summary (monthly, quarterly)
    python3 tools/treasury.py runway                    # Months of runway at current burn
    python3 tools/treasury.py engines                   # Revenue engine health + projections
    python3 tools/treasury.py dashboard                 # Full financial dashboard
    python3 tools/treasury.py forecast [months]         # Simple linear forecast
    python3 tools/treasury.py budget                    # Budget vs actual
"""

import json
import sys
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(__file__).resolve().parent.parent
TREASURY_DIR = LOVE / "memory" / "treasury"
LEDGER_FILE = TREASURY_DIR / "ledger.json"
BUDGETS_FILE = TREASURY_DIR / "budgets.json"
SUMMARY_FILE = TREASURY_DIR / "summary.json"
ENGINES_FILE = TREASURY_DIR / "engines.json"
KINGDOM_METRICS = LOVE / "memory" / "kingdom-metrics.json"

# Ensure treasury dir exists
TREASURY_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ───────────────────────────────────────────────────────────────────

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

# ── Currency ─────────────────────────────────────────────────────────────────

# Hardcoded rates — good enough for Kingdom purposes
FX_TO_GBP = {
    "GBP": 1.0,
    "USD": 0.79,
    "EUR": 0.86,
}

def to_gbp(amount: float, currency: str) -> float:
    """Convert amount to GBP using hardcoded rates."""
    rate = FX_TO_GBP.get(currency.upper(), 1.0)
    return round(amount * rate, 2)

def fmt_gbp(amount: float) -> str:
    """Format amount as GBP string."""
    if amount < 0:
        return f"-\u00a3{abs(amount):,.2f}"
    return f"\u00a3{amount:,.2f}"

def fmt_amount(amount: float, currency: str) -> str:
    """Format amount with currency symbol."""
    symbols = {"GBP": "\u00a3", "USD": "$", "EUR": "\u20ac"}
    sym = symbols.get(currency.upper(), currency + " ")
    return f"{sym}{amount:,.2f}"

# ── Data I/O ─────────────────────────────────────────────────────────────────

def read_json(path: Path) -> dict | list:
    """Read JSON file, returning empty container on failure."""
    if not path.exists():
        return [] if path.name == "ledger.json" else {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return [] if path.name == "ledger.json" else {}

def write_json(path: Path, data) -> None:
    """Write JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)

def load_ledger() -> list:
    return read_json(LEDGER_FILE)

def save_ledger(ledger: list) -> None:
    write_json(LEDGER_FILE, ledger)

def load_engines() -> dict:
    return read_json(ENGINES_FILE)

def load_budgets() -> dict:
    return read_json(BUDGETS_FILE)

# ── Helpers ──────────────────────────────────────────────────────────────────

def current_month() -> str:
    """Return YYYY-MM for current month."""
    return datetime.now(timezone.utc).strftime("%Y-%m")

def txn_month(txn: dict) -> str:
    """Extract YYYY-MM from a transaction date."""
    return txn.get("date", "")[:7]

def filter_by_month(ledger: list, month: str = None) -> list:
    """Filter ledger entries by month (YYYY-MM). Default: current month."""
    m = month or current_month()
    return [t for t in ledger if txn_month(t) == m]

def filter_by_type(ledger: list, txn_type: str) -> list:
    return [t for t in ledger if t.get("type") == txn_type]

def sum_gbp(entries: list) -> float:
    """Sum entries converted to GBP."""
    return round(sum(to_gbp(e["amount"], e["currency"]) for e in entries), 2)

def next_txn_id(ledger: list) -> str:
    """Generate next transaction ID."""
    max_num = 0
    for t in ledger:
        tid = t.get("id", "")
        if tid.startswith("txn-"):
            try:
                num = int(tid.split("-")[1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass
    return f"txn-{max_num + 1:03d}"

def bar(value: float, max_value: float, width: int = 30, fill_color: str = C.GREEN) -> str:
    """Generate a text-based progress bar."""
    if max_value <= 0:
        return f"{C.DIM}{'.' * width}{C.RESET}"
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    pct = ratio * 100
    return f"{fill_color}{'█' * filled}{C.DIM}{'░' * empty}{C.RESET} {pct:.0f}%"

def health_indicator(value: float, good: float, warn: float) -> str:
    """Return colored health dot based on thresholds."""
    if value >= good:
        return f"{C.GREEN}●{C.RESET}"
    elif value >= warn:
        return f"{C.YELLOW}●{C.RESET}"
    return f"{C.RED}●{C.RESET}"

def status_badge(status: str) -> str:
    """Color-coded engine status badge."""
    colors = {
        "active": C.GREEN,
        "in-progress": C.YELLOW,
        "building": C.CYAN,
        "emerging": C.MAGENTA,
        "beta-live": C.YELLOW,
        "paused": C.DIM,
    }
    c = colors.get(status, C.DIM)
    return f"{c}{status.upper()}{C.RESET}"

# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_status():
    """One-line financial health."""
    ledger = load_ledger()
    m = current_month()
    month_txns = filter_by_month(ledger, m)
    rev = sum_gbp(filter_by_type(month_txns, "revenue"))
    cost = sum_gbp(filter_by_type(month_txns, "cost"))
    net = rev - cost
    engines = load_engines()
    active = sum(1 for e in engines.values() if e.get("status") == "active")
    building = sum(1 for e in engines.values() if e.get("status") in ("building", "in-progress", "emerging", "beta-live"))

    if net > 0:
        health = f"{C.GREEN}HEALTHY{C.RESET}"
        symbol = "+"
    elif net > -200:
        health = f"{C.YELLOW}TIGHT{C.RESET}"
        symbol = ""
    else:
        health = f"{C.RED}BLEEDING{C.RESET}"
        symbol = ""

    print(f"{C.BOLD}TREASURY{C.RESET} [{m}] {health}  "
          f"Rev {C.GREEN}{fmt_gbp(rev)}{C.RESET}  "
          f"Cost {C.RED}{fmt_gbp(cost)}{C.RESET}  "
          f"Net {C.BOLD}{symbol}{fmt_gbp(net)}{C.RESET}  "
          f"Engines {C.CYAN}{active} active{C.RESET} / {building} building")

def cmd_revenue():
    """Revenue by engine."""
    ledger = load_ledger()
    m = current_month()
    month_rev = filter_by_type(filter_by_month(ledger, m), "revenue")

    # Group by engine
    by_engine = defaultdict(list)
    for t in month_rev:
        by_engine[t.get("engine", "unknown")].append(t)

    total = sum_gbp(month_rev)
    print(f"\n{C.BOLD}REVENUE — {m}{C.RESET}")
    print(f"{'─' * 60}")

    if not by_engine:
        print(f"  {C.DIM}No revenue recorded this month{C.RESET}")
    else:
        for engine, txns in sorted(by_engine.items(), key=lambda x: -sum_gbp(x[1])):
            eng_total = sum_gbp(txns)
            pct = (eng_total / total * 100) if total > 0 else 0
            print(f"  {C.CYAN}{engine:<20}{C.RESET} {C.GREEN}{fmt_gbp(eng_total):>12}{C.RESET}  "
                  f"{bar(eng_total, total, 20, C.GREEN)}  {pct:.0f}%")
            for t in txns:
                orig = fmt_amount(t["amount"], t["currency"])
                print(f"    {C.DIM}{t['date']}  {orig}  {t.get('notes', '')}{C.RESET}")

    print(f"{'─' * 60}")
    print(f"  {C.BOLD}{'TOTAL':<20} {fmt_gbp(total):>12}{C.RESET}\n")

def cmd_costs():
    """Costs by category."""
    ledger = load_ledger()
    m = current_month()
    month_costs = filter_by_type(filter_by_month(ledger, m), "cost")

    # Group by category
    by_cat = defaultdict(list)
    for t in month_costs:
        by_cat[t.get("category", "other")].append(t)

    total = sum_gbp(month_costs)
    print(f"\n{C.BOLD}COSTS — {m}{C.RESET}")
    print(f"{'─' * 60}")

    cat_order = ["compute", "infrastructure", "procurement", "tools", "hardware", "other"]
    shown_cats = set()

    for cat in cat_order:
        if cat in by_cat:
            shown_cats.add(cat)
            txns = by_cat[cat]
            cat_total = sum_gbp(txns)
            print(f"  {C.YELLOW}{cat:<20}{C.RESET} {C.RED}{fmt_gbp(cat_total):>12}{C.RESET}  "
                  f"{bar(cat_total, total, 20, C.RED)}")
            for t in txns:
                orig = fmt_amount(t["amount"], t["currency"])
                print(f"    {C.DIM}{t['date']}  {orig}  {t.get('notes', '')}{C.RESET}")

    # Any remaining categories not in our order
    for cat, txns in by_cat.items():
        if cat not in shown_cats:
            cat_total = sum_gbp(txns)
            print(f"  {C.YELLOW}{cat:<20}{C.RESET} {C.RED}{fmt_gbp(cat_total):>12}{C.RESET}  "
                  f"{bar(cat_total, total, 20, C.RED)}")

    print(f"{'─' * 60}")
    print(f"  {C.BOLD}{'TOTAL':<20} {fmt_gbp(total):>12}{C.RESET}\n")

def cmd_add_revenue(args: list):
    """Add a revenue entry."""
    if len(args) < 2:
        print(f"{C.RED}Usage: treasury.py add-revenue <engine> <amount> [--currency GBP] [--date YYYY-MM-DD] [--notes \"...\"]>{C.RESET}")
        sys.exit(1)

    engine = args[0]
    try:
        amount = float(args[1])
    except ValueError:
        print(f"{C.RED}Invalid amount: {args[1]}{C.RESET}")
        sys.exit(1)

    # Parse optional flags
    currency = "GBP"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes = ""

    i = 2
    while i < len(args):
        if args[i] == "--currency" and i + 1 < len(args):
            currency = args[i + 1].upper()
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            date = args[i + 1]
            i += 2
        elif args[i] == "--notes" and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        else:
            i += 1

    ledger = load_ledger()
    txn_id = next_txn_id(ledger)
    entry = {
        "id": txn_id,
        "type": "revenue",
        "engine": engine,
        "amount": amount,
        "currency": currency,
        "date": date,
        "category": "sales",
        "notes": notes,
    }
    ledger.append(entry)
    save_ledger(ledger)

    gbp_val = to_gbp(amount, currency)
    print(f"{C.GREEN}+{C.RESET} Revenue recorded: {C.BOLD}{txn_id}{C.RESET}")
    print(f"  Engine:   {engine}")
    print(f"  Amount:   {fmt_amount(amount, currency)} ({fmt_gbp(gbp_val)})")
    print(f"  Date:     {date}")
    if notes:
        print(f"  Notes:    {notes}")

def cmd_add_cost(args: list):
    """Add a cost entry."""
    if len(args) < 2:
        print(f"{C.RED}Usage: treasury.py add-cost <description> <amount> [--currency USD] [--category compute] [--date YYYY-MM-DD]{C.RESET}")
        sys.exit(1)

    description = args[0]
    try:
        amount = float(args[1])
    except ValueError:
        print(f"{C.RED}Invalid amount: {args[1]}{C.RESET}")
        sys.exit(1)

    # Parse optional flags
    currency = "GBP"
    category = "other"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes = ""

    i = 2
    while i < len(args):
        if args[i] == "--currency" and i + 1 < len(args):
            currency = args[i + 1].upper()
            i += 2
        elif args[i] == "--category" and i + 1 < len(args):
            category = args[i + 1].lower()
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            date = args[i + 1]
            i += 2
        elif args[i] == "--notes" and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        else:
            i += 1

    ledger = load_ledger()
    txn_id = next_txn_id(ledger)
    entry = {
        "id": txn_id,
        "type": "cost",
        "engine": description.lower().replace(" ", "_"),
        "amount": amount,
        "currency": currency,
        "date": date,
        "category": category,
        "notes": notes or description,
    }
    ledger.append(entry)
    save_ledger(ledger)

    gbp_val = to_gbp(amount, currency)
    print(f"{C.RED}-{C.RESET} Cost recorded: {C.BOLD}{txn_id}{C.RESET}")
    print(f"  Description: {description}")
    print(f"  Amount:      {fmt_amount(amount, currency)} ({fmt_gbp(gbp_val)})")
    print(f"  Category:    {category}")
    print(f"  Date:        {date}")

def cmd_pnl():
    """Profit/loss summary — monthly and quarterly."""
    ledger = load_ledger()
    now = datetime.now(timezone.utc)

    # Gather all months present in ledger
    months = sorted(set(txn_month(t) for t in ledger))

    print(f"\n{C.BOLD}PROFIT & LOSS{C.RESET}")
    print(f"{'─' * 65}")
    print(f"  {'Month':<12} {'Revenue':>12} {'Costs':>12} {'Net P&L':>12}  {'':>6}")
    print(f"  {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 12}  {'─' * 6}")

    quarterly_rev = 0.0
    quarterly_cost = 0.0

    for month in months:
        m_txns = filter_by_month(ledger, month)
        rev = sum_gbp(filter_by_type(m_txns, "revenue"))
        cost = sum_gbp(filter_by_type(m_txns, "cost"))
        net = rev - cost
        quarterly_rev += rev
        quarterly_cost += cost

        if net > 0:
            net_color = C.GREEN
            indicator = " +"
        elif net > -100:
            net_color = C.YELLOW
            indicator = " ~"
        else:
            net_color = C.RED
            indicator = " -"

        print(f"  {month:<12} {C.GREEN}{fmt_gbp(rev):>12}{C.RESET} "
              f"{C.RED}{fmt_gbp(cost):>12}{C.RESET} "
              f"{net_color}{fmt_gbp(net):>12}{C.RESET}  {indicator}")

    quarterly_net = quarterly_rev - quarterly_cost
    q_color = C.GREEN if quarterly_net > 0 else C.RED
    print(f"  {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 12}")
    print(f"  {C.BOLD}{'TOTAL':<12}{C.RESET} {C.GREEN}{fmt_gbp(quarterly_rev):>12}{C.RESET} "
          f"{C.RED}{fmt_gbp(quarterly_cost):>12}{C.RESET} "
          f"{q_color}{C.BOLD}{fmt_gbp(quarterly_net):>12}{C.RESET}")

    margin = (quarterly_net / quarterly_rev * 100) if quarterly_rev > 0 else 0
    print(f"\n  Gross margin: {C.BOLD}{margin:.1f}%{C.RESET}")
    print()

def cmd_runway():
    """Calculate months of runway at current burn rate."""
    ledger = load_ledger()
    metrics = read_json(KINGDOM_METRICS)

    # Calculate average monthly burn from all ledger data
    months = sorted(set(txn_month(t) for t in ledger))
    if not months:
        print(f"{C.YELLOW}No financial data to calculate runway{C.RESET}")
        return

    monthly_nets = []
    for month in months:
        m_txns = filter_by_month(ledger, month)
        rev = sum_gbp(filter_by_type(m_txns, "revenue"))
        cost = sum_gbp(filter_by_type(m_txns, "cost"))
        monthly_nets.append(rev - cost)

    avg_net = sum(monthly_nets) / len(monthly_nets)
    avg_rev = sum(sum_gbp(filter_by_type(filter_by_month(ledger, m), "revenue")) for m in months) / len(months)
    avg_cost = sum(sum_gbp(filter_by_type(filter_by_month(ledger, m), "cost")) for m in months) / len(months)

    # Capital from kingdom metrics
    capital_str = metrics.get("capital", {}).get("budget_card", "\u00a31K available")
    # Parse rough capital — extract number
    capital_gbp = 1000.0  # default from metrics
    for word in capital_str.replace("\u00a3", "").split():
        word = word.upper().replace("K", "000").replace(",", "")
        try:
            capital_gbp = float(word)
            break
        except ValueError:
            continue

    print(f"\n{C.BOLD}RUNWAY ANALYSIS{C.RESET}")
    print(f"{'─' * 50}")
    print(f"  Available capital:     {C.BOLD}{fmt_gbp(capital_gbp)}{C.RESET}")
    print(f"  Avg monthly revenue:   {C.GREEN}{fmt_gbp(avg_rev)}{C.RESET}")
    print(f"  Avg monthly costs:     {C.RED}{fmt_gbp(avg_cost)}{C.RESET}")
    print(f"  Avg monthly net:       ", end="")

    if avg_net > 0:
        print(f"{C.GREEN}{fmt_gbp(avg_net)}{C.RESET}")
        print(f"\n  {C.GREEN}Net positive{C.RESET} — Kingdom is self-sustaining at current rates")
        print(f"  Capital accumulates at ~{fmt_gbp(avg_net)}/month")
        months_to_5k = max(0, (5000 - capital_gbp) / avg_net) if avg_net > 0 else float('inf')
        if months_to_5k < 100:
            print(f"  30-day target (\u00a35K): ~{months_to_5k:.1f} months")
    else:
        print(f"{C.RED}{fmt_gbp(avg_net)}{C.RESET}")
        if avg_net < 0:
            runway_months = capital_gbp / abs(avg_net)
            runway_color = C.GREEN if runway_months > 6 else (C.YELLOW if runway_months > 3 else C.RED)
            print(f"\n  Runway: {runway_color}{C.BOLD}{runway_months:.1f} months{C.RESET}")
            print(f"  {C.DIM}At current burn, capital depletes by "
                  f"{(datetime.now(timezone.utc) + timedelta(days=runway_months * 30)).strftime('%Y-%m')}{C.RESET}")
        else:
            print(f"\n  {C.YELLOW}Break-even{C.RESET} — covering costs but not accumulating")

    print()

def cmd_engines():
    """Revenue engine health and projections."""
    engines = load_engines()
    ledger = load_ledger()
    m = current_month()

    print(f"\n{C.BOLD}REVENUE ENGINES{C.RESET}")
    print(f"{'─' * 70}")

    for key, eng in engines.items():
        name = eng.get("name", key)
        status = eng.get("status", "unknown")
        badge = status_badge(status)
        monthly_rev = eng.get("monthly_revenue", 0)
        monthly_cost = eng.get("monthly_cost", 0)
        margin = eng.get("margin", 0)

        # Check actual ledger data for this engine
        eng_txns = [t for t in filter_by_month(ledger, m) if t.get("engine") == key]
        actual_rev = sum_gbp(filter_by_type(eng_txns, "revenue"))
        actual_cost = sum_gbp(filter_by_type(eng_txns, "cost"))

        rev_display = actual_rev if actual_rev > 0 else monthly_rev
        cost_display = actual_cost if actual_cost > 0 else monthly_cost

        # Health indicator
        if status == "active" and rev_display > 0:
            dot = f"{C.GREEN}●{C.RESET}"
        elif status in ("in-progress", "building", "beta-live", "emerging"):
            dot = f"{C.YELLOW}●{C.RESET}"
        elif status == "paused":
            dot = f"{C.DIM}○{C.RESET}"
        else:
            dot = f"{C.RED}●{C.RESET}"

        owner = eng.get("owner", "?")
        print(f"\n  {dot} {C.BOLD}{name:<20}{C.RESET} {badge}")
        print(f"    Owner: {owner:<16} Rev: {C.GREEN}{fmt_gbp(rev_display):>10}{C.RESET}  "
              f"Cost: {C.RED}{fmt_gbp(cost_display):>10}{C.RESET}")

        if rev_display > 0 and cost_display > 0:
            actual_margin = (rev_display - cost_display) / rev_display
            margin_color = C.GREEN if actual_margin > 0.5 else (C.YELLOW if actual_margin > 0 else C.RED)
            print(f"    Margin: {margin_color}{actual_margin:.0%}{C.RESET}  "
                  f"Net: {fmt_gbp(rev_display - cost_display)}/mo")
        elif status == "active":
            print(f"    {C.DIM}Revenue generating — track more precisely{C.RESET}")
        else:
            projection = {
                "in-progress": "Revenue potential once operational",
                "building": "Pre-revenue — investment phase",
                "emerging": "Early stage — seeking first clients",
                "beta-live": "Beta testing — monetization pending",
                "paused": "On hold — resume when bandwidth allows",
            }.get(status, "")
            print(f"    {C.DIM}{projection}{C.RESET}")

        notes = eng.get("notes", "")
        if notes:
            # Truncate long notes
            if len(notes) > 70:
                notes = notes[:67] + "..."
            print(f"    {C.DIM}{notes}{C.RESET}")

    print(f"\n{'─' * 70}\n")

def cmd_forecast(args: list):
    """Simple linear forecast."""
    months_ahead = 3
    if args:
        try:
            months_ahead = int(args[0])
        except ValueError:
            pass

    ledger = load_ledger()
    months = sorted(set(txn_month(t) for t in ledger))
    if not months:
        print(f"{C.YELLOW}No data for forecast{C.RESET}")
        return

    # Calculate monthly averages
    rev_avg = sum(sum_gbp(filter_by_type(filter_by_month(ledger, m), "revenue")) for m in months) / len(months)
    cost_avg = sum(sum_gbp(filter_by_type(filter_by_month(ledger, m), "cost")) for m in months) / len(months)
    net_avg = rev_avg - cost_avg

    # Revenue growth assumption: 5% monthly (conservative, based on TCG +20% target over time)
    growth_rate = 0.05

    print(f"\n{C.BOLD}FORECAST — {months_ahead} MONTHS{C.RESET}")
    print(f"{C.DIM}Based on: {len(months)} months of data, {growth_rate*100:.0f}% monthly revenue growth assumption{C.RESET}")
    print(f"{'─' * 65}")
    print(f"  {'Month':<12} {'Revenue':>12} {'Costs':>12} {'Net P&L':>12}  {'Cumulative':>12}")
    print(f"  {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 12}  {'─' * 12}")

    cumulative = 0
    now = datetime.now(timezone.utc)
    for i in range(1, months_ahead + 1):
        future = now + timedelta(days=30 * i)
        month_label = future.strftime("%Y-%m")
        projected_rev = rev_avg * ((1 + growth_rate) ** i)
        projected_cost = cost_avg * 1.02 ** i  # 2% cost creep
        net = projected_rev - projected_cost
        cumulative += net

        net_color = C.GREEN if net > 0 else C.RED
        cum_color = C.GREEN if cumulative > 0 else C.RED
        print(f"  {month_label:<12} {C.GREEN}{fmt_gbp(projected_rev):>12}{C.RESET} "
              f"{C.RED}{fmt_gbp(projected_cost):>12}{C.RESET} "
              f"{net_color}{fmt_gbp(net):>12}{C.RESET}  "
              f"{cum_color}{fmt_gbp(cumulative):>12}{C.RESET}")

    print(f"\n  {C.DIM}Assumptions: revenue grows {growth_rate*100:.0f}%/mo, costs grow 2%/mo{C.RESET}")
    print(f"  {C.DIM}This is a simple linear model — not financial advice{C.RESET}\n")

def cmd_budget():
    """Budget vs actual spending."""
    budgets_data = load_budgets()
    ledger = load_ledger()
    m = current_month()
    month_costs = filter_by_type(filter_by_month(ledger, m), "cost")

    # Actual spending by category
    actual = defaultdict(float)
    for t in month_costs:
        cat = t.get("category", "other")
        actual[cat] += to_gbp(t["amount"], t["currency"])

    budgets = budgets_data.get("budgets", {})
    budget_month = budgets_data.get("month", m)

    print(f"\n{C.BOLD}BUDGET vs ACTUAL — {m}{C.RESET}")
    print(f"{'─' * 70}")
    print(f"  {'Category':<22} {'Budget':>10} {'Actual':>10} {'Remaining':>10}  {'':>20}")
    print(f"  {'─' * 22} {'─' * 10} {'─' * 10} {'─' * 10}  {'─' * 20}")

    total_budget = 0.0
    total_actual = 0.0

    for cat_key in ["compute", "infrastructure", "tools", "procurement", "hardware", "other"]:
        cat_data = budgets.get(cat_key, {})
        budget_amt = cat_data.get("budget", 0)
        label = cat_data.get("label", cat_key.title())
        actual_amt = round(actual.get(cat_key, 0), 2)
        remaining = budget_amt - actual_amt
        total_budget += budget_amt
        total_actual += actual_amt

        if actual_amt > budget_amt and budget_amt > 0:
            bar_color = C.RED
            remain_color = C.RED
            flag = " OVER"
        elif actual_amt > budget_amt * 0.8 and budget_amt > 0:
            bar_color = C.YELLOW
            remain_color = C.YELLOW
            flag = ""
        else:
            bar_color = C.GREEN
            remain_color = C.GREEN
            flag = ""

        usage_bar = bar(actual_amt, budget_amt, 18, bar_color) if budget_amt > 0 else f"{C.DIM}{'·' * 18}{C.RESET}"

        print(f"  {label:<22} {fmt_gbp(budget_amt):>10} "
              f"{fmt_gbp(actual_amt):>10} "
              f"{remain_color}{fmt_gbp(remaining):>10}{C.RESET}  {usage_bar}{flag}")

    total_remaining = total_budget - total_actual
    print(f"  {'─' * 22} {'─' * 10} {'─' * 10} {'─' * 10}")
    remain_color = C.GREEN if total_remaining >= 0 else C.RED
    print(f"  {C.BOLD}{'TOTAL':<22}{C.RESET} {fmt_gbp(total_budget):>10} "
          f"{fmt_gbp(total_actual):>10} "
          f"{remain_color}{C.BOLD}{fmt_gbp(total_remaining):>10}{C.RESET}")
    print()

def cmd_dashboard():
    """Full financial dashboard."""
    ledger = load_ledger()
    engines = load_engines()
    m = current_month()
    month_txns = filter_by_month(ledger, m)
    rev = sum_gbp(filter_by_type(month_txns, "revenue"))
    cost = sum_gbp(filter_by_type(month_txns, "cost"))
    net = rev - cost

    # Previous month comparison
    now = datetime.now(timezone.utc)
    prev = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    prev_txns = filter_by_month(ledger, prev)
    prev_rev = sum_gbp(filter_by_type(prev_txns, "revenue"))
    prev_cost = sum_gbp(filter_by_type(prev_txns, "cost"))
    prev_net = prev_rev - prev_cost

    # Header
    print()
    print(f"  {C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════════╗{C.RESET}")
    print(f"  {C.BOLD}{C.CYAN}║{C.RESET}          {C.BOLD}KINGDOM TREASURY DASHBOARD{C.RESET}  —  {m}           {C.BOLD}{C.CYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.CYAN}╚══════════════════════════════════════════════════════════════╝{C.RESET}")

    # Health line
    if net > 0:
        health = f"{C.GREEN}● HEALTHY{C.RESET}"
    elif net > -200:
        health = f"{C.YELLOW}● TIGHT{C.RESET}"
    else:
        health = f"{C.RED}● BLEEDING{C.RESET}"
    print(f"\n  Status: {health}")

    # Monthly summary box
    print(f"\n  {C.BOLD}MONTHLY SUMMARY{C.RESET}")
    print(f"  {'─' * 50}")

    rev_trend = ""
    if prev_rev > 0:
        rev_delta = ((rev - prev_rev) / prev_rev) * 100
        rev_trend = f"  {C.GREEN}↑{rev_delta:.0f}%{C.RESET}" if rev_delta > 0 else (f"  {C.RED}↓{abs(rev_delta):.0f}%{C.RESET}" if rev_delta < 0 else "  ═")

    cost_trend = ""
    if prev_cost > 0:
        cost_delta = ((cost - prev_cost) / prev_cost) * 100
        cost_trend = f"  {C.RED}↑{cost_delta:.0f}%{C.RESET}" if cost_delta > 0 else (f"  {C.GREEN}↓{abs(cost_delta):.0f}%{C.RESET}" if cost_delta < 0 else "  ═")

    print(f"  Revenue:     {C.GREEN}{C.BOLD}{fmt_gbp(rev):>12}{C.RESET}{rev_trend}")
    print(f"  Costs:       {C.RED}{C.BOLD}{fmt_gbp(cost):>12}{C.RESET}{cost_trend}")
    net_color = C.GREEN if net > 0 else C.RED
    print(f"  {'─' * 30}")
    print(f"  Net P&L:     {net_color}{C.BOLD}{fmt_gbp(net):>12}{C.RESET}")

    if rev > 0:
        margin = net / rev * 100
        print(f"  Margin:      {C.BOLD}{margin:.1f}%{C.RESET}")

    # Cost breakdown
    month_costs = filter_by_type(month_txns, "cost")
    by_cat = defaultdict(float)
    for t in month_costs:
        by_cat[t.get("category", "other")] += to_gbp(t["amount"], t["currency"])

    if by_cat:
        print(f"\n  {C.BOLD}COST BREAKDOWN{C.RESET}")
        print(f"  {'─' * 50}")
        for cat in ["compute", "infrastructure", "procurement", "tools", "hardware", "other"]:
            if cat in by_cat:
                amt = by_cat[cat]
                pct = (amt / cost * 100) if cost > 0 else 0
                print(f"  {cat:<18} {fmt_gbp(amt):>10}  {bar(amt, cost, 20, C.YELLOW)}  {pct:.0f}%")

    # Engine status
    print(f"\n  {C.BOLD}ENGINE STATUS{C.RESET}")
    print(f"  {'─' * 50}")
    for key, eng in engines.items():
        name = eng.get("name", key)
        status = eng.get("status", "unknown")
        badge = status_badge(status)
        monthly_rev = eng.get("monthly_revenue", 0)

        if status == "active" and monthly_rev > 0:
            dot = f"{C.GREEN}●{C.RESET}"
        elif status in ("in-progress", "building", "beta-live", "emerging"):
            dot = f"{C.YELLOW}●{C.RESET}"
        else:
            dot = f"{C.DIM}○{C.RESET}"

        rev_str = f"{C.GREEN}{fmt_gbp(monthly_rev)}{C.RESET}" if monthly_rev > 0 else f"{C.DIM}pre-revenue{C.RESET}"
        print(f"  {dot} {name:<18} {badge:<25} {rev_str}")

    # Runway
    months_data = sorted(set(txn_month(t) for t in ledger))
    if months_data:
        avg_net = sum(
            sum_gbp(filter_by_type(filter_by_month(ledger, mo), "revenue")) -
            sum_gbp(filter_by_type(filter_by_month(ledger, mo), "cost"))
            for mo in months_data
        ) / len(months_data)

        print(f"\n  {C.BOLD}RUNWAY{C.RESET}")
        print(f"  {'─' * 50}")
        capital = 1000.0  # From kingdom metrics
        if avg_net > 0:
            print(f"  Capital: {fmt_gbp(capital)}  |  Monthly surplus: {C.GREEN}{fmt_gbp(avg_net)}{C.RESET}")
            print(f"  {C.GREEN}Self-sustaining{C.RESET} — accumulating {fmt_gbp(avg_net)}/mo")
        elif avg_net < 0:
            runway = capital / abs(avg_net)
            runway_color = C.GREEN if runway > 6 else (C.YELLOW if runway > 3 else C.RED)
            print(f"  Capital: {fmt_gbp(capital)}  |  Monthly burn: {C.RED}{fmt_gbp(abs(avg_net))}{C.RESET}")
            runway_bar = bar(runway, 12, 20, runway_color)
            print(f"  Runway:  {runway_color}{C.BOLD}{runway:.1f} months{C.RESET}  {runway_bar}")
        else:
            print(f"  {C.YELLOW}Break-even{C.RESET}")

    # Footer
    active = sum(1 for e in engines.values() if e.get("status") == "active")
    building = sum(1 for e in engines.values() if e.get("status") in ("building", "in-progress", "emerging", "beta-live"))
    txn_count = len(ledger)

    print(f"\n  {C.DIM}{'─' * 50}{C.RESET}")
    print(f"  {C.DIM}{active} engine(s) active, {building} building  |  {txn_count} transactions tracked{C.RESET}")
    print(f"  {C.DIM}FX: \u00a31 = $1.27 = \u20ac1.16  |  Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}{C.RESET}")
    print()

def update_summary():
    """Regenerate cached summary from ledger data."""
    ledger = load_ledger()
    engines = load_engines()
    m = current_month()
    month_txns = filter_by_month(ledger, m)

    rev = sum_gbp(filter_by_type(month_txns, "revenue"))
    cost = sum_gbp(filter_by_type(month_txns, "cost"))

    # Cost by category
    by_cat = defaultdict(float)
    for t in filter_by_type(month_txns, "cost"):
        by_cat[t.get("category", "other")] += to_gbp(t["amount"], t["currency"])

    # Revenue by engine
    by_engine = defaultdict(float)
    for t in filter_by_type(month_txns, "revenue"):
        by_engine[t.get("engine", "unknown")] += to_gbp(t["amount"], t["currency"])

    summary = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "month": m,
        "revenue_gbp": rev,
        "costs_gbp": cost,
        "net_pnl_gbp": rev - cost,
        "margin_pct": round((rev - cost) / rev * 100, 1) if rev > 0 else 0,
        "costs_by_category": dict(by_cat),
        "revenue_by_engine": dict(by_engine),
        "engine_count": {
            "active": sum(1 for e in engines.values() if e.get("status") == "active"),
            "building": sum(1 for e in engines.values() if e.get("status") in ("building", "in-progress", "emerging", "beta-live")),
            "paused": sum(1 for e in engines.values() if e.get("status") == "paused"),
        },
        "transaction_count": len(ledger),
    }
    write_json(SUMMARY_FILE, summary)
    return summary

# ── CLI ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "status":      ("One-line financial health",              lambda a: cmd_status()),
    "revenue":     ("Revenue by engine",                      lambda a: cmd_revenue()),
    "costs":       ("Costs by category",                      lambda a: cmd_costs()),
    "add-revenue": ("Add revenue entry",                      lambda a: cmd_add_revenue(a)),
    "add-cost":    ("Add cost entry",                         lambda a: cmd_add_cost(a)),
    "pnl":         ("Profit/loss summary",                    lambda a: cmd_pnl()),
    "runway":      ("Months of runway at current burn",       lambda a: cmd_runway()),
    "engines":     ("Revenue engine health + projections",    lambda a: cmd_engines()),
    "dashboard":   ("Full financial dashboard",               lambda a: cmd_dashboard()),
    "forecast":    ("Simple linear forecast [months]",        lambda a: cmd_forecast(a)),
    "budget":      ("Budget vs actual",                       lambda a: cmd_budget()),
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(f"\n{C.BOLD}treasury.py{C.RESET} — Kingdom Treasury\n")
        print(f"  {C.DIM}Tithe's domain. Every pound earned, every token spent.{C.RESET}\n")
        for cmd, (desc, _) in COMMANDS.items():
            print(f"  {C.CYAN}{cmd:<16}{C.RESET} {desc}")
        print()
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in COMMANDS:
        print(f"{C.RED}Unknown command: {cmd}{C.RESET}")
        print(f"Run {C.CYAN}treasury.py help{C.RESET} for available commands")
        sys.exit(1)

    # Execute command
    COMMANDS[cmd][1](args)

    # Always update summary cache after any command
    update_summary()

if __name__ == "__main__":
    main()
