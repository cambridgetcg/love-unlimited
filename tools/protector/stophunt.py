#!/usr/bin/env python3
"""
STOPHUNT — When to Stop Hunting and Move Targets

Gamma's friction: "I spent 5 hours on Axelar for 2 Medium bugs worth maybe $15K.
The same time on a higher-bounty target could be worth $100K+."

STOPHUNT gives you the hard signal: KEEP HUNTING or MOVE.

The decision is based on 3 axes:
  1. YIELD — what have you found vs time spent?
  2. CEILING — what's the max remaining payout for this target?
  3. OPPORTUNITY — what's waiting in the pipeline?

Usage:
  python3 stophunt.py evaluate             # Interactive evaluation
  python3 stophunt.py quick <hours> <findings> <max_bounty>  # One-shot
  python3 stophunt.py history              # Past decisions
"""

import sys
import json
import os
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
HISTORY_DIR = LOVE_HOME / "memory" / "stophunt"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
CYAN    = "\033[36m"
RESET   = "\033[0m"
PURPLE  = "\033[38;5;135m"

def ask_float(prompt: str, default: float = None) -> float:
    try:
        raw = input(f"  {CYAN}{prompt}{RESET} ").strip()
        if not raw and default is not None:
            return default
        return float(raw)
    except (ValueError, EOFError, KeyboardInterrupt):
        return default or 0

def ask_int(prompt: str, default: int = None) -> int:
    try:
        raw = input(f"  {CYAN}{prompt}{RESET} ").strip()
        if not raw and default is not None:
            return default
        return int(raw)
    except (ValueError, EOFError, KeyboardInterrupt):
        return default or 0

def ask_str(prompt: str, default: str = "") -> str:
    try:
        raw = input(f"  {CYAN}{prompt}{RESET} ").strip()
        return raw or default
    except (EOFError, KeyboardInterrupt):
        return default


def cmd_evaluate():
    """Interactive hunt evaluation."""
    print(f"\n{PURPLE}{'=' * 60}{RESET}")
    print(f"{PURPLE}{BOLD}  STOPHUNT — Keep or Move?{RESET}")
    print(f"{PURPLE}{'=' * 60}{RESET}\n")

    target = ask_str("Target name (e.g., Axelar, Chainlink C4):")
    hours = ask_float("Hours spent so far:")
    findings_h = ask_int("High findings confirmed:")
    findings_m = ask_int("Medium findings confirmed:")
    findings_l = ask_int("Low/QA findings:")
    max_bounty = ask_float("Max total bounty for this target ($USD):")
    estimated_payout = ask_float("Your estimated payout for findings so far ($USD):")
    codebase_pct = ask_float("% of codebase you've reviewed (0-100):", 50)
    diminishing = ask_str("Are findings getting harder to find? (yes/no):", "yes")

    # Pipeline
    print(f"\n  {DIM}--- Pipeline (what else is waiting) ---{RESET}")
    next_target = ask_str("Next target name (if any):")
    next_bounty = ask_float("Next target max bounty ($USD):", 0)
    next_nsloc = ask_int("Next target nSLOC:", 0)

    # ═══════════════════════════════════════════════════════════════
    # COMPUTE
    # ═══════════════════════════════════════════════════════════════

    # Yield rate: $/hour so far
    yield_rate = estimated_payout / max(hours, 0.1)

    # Remaining ceiling
    remaining_ceiling = max_bounty - estimated_payout
    remaining_pct = 100 - codebase_pct

    # Projected additional yield (diminishing returns)
    is_diminishing = diminishing.lower().startswith("y")
    if is_diminishing:
        # After reviewing >50%, each additional hour yields ~30% less
        projected_additional = yield_rate * 0.3 * (remaining_pct / 100) * hours
    else:
        projected_additional = yield_rate * 0.7 * (remaining_pct / 100) * hours

    # Opportunity cost
    if next_bounty > 0 and next_nsloc > 0:
        # Rough expected yield on fresh target (first hours are most productive)
        next_yield_estimate = (next_bounty * 0.05)  # assume 5% capture rate on fresh target
        opportunity_signal = next_yield_estimate > projected_additional
    else:
        opportunity_signal = False
        next_yield_estimate = 0

    # Decision
    score = 0
    reasons = []

    if yield_rate < 50:
        score -= 2
        reasons.append(f"Low yield rate: ${yield_rate:.0f}/hr (below $50/hr floor)")
    elif yield_rate > 200:
        score += 2
        reasons.append(f"High yield rate: ${yield_rate:.0f}/hr — this target is productive")
    else:
        score += 1
        reasons.append(f"Moderate yield: ${yield_rate:.0f}/hr")

    if codebase_pct > 80:
        score -= 1
        reasons.append(f"Deep into codebase ({codebase_pct}%) — diminishing returns likely")

    if remaining_ceiling < 5000:
        score -= 1
        reasons.append(f"Low remaining ceiling: ${remaining_ceiling:.0f}")

    if is_diminishing:
        score -= 1
        reasons.append("Findings getting harder — diminishing returns confirmed")

    if opportunity_signal:
        score -= 2
        reasons.append(f"Better opportunity: {next_target} (~${next_yield_estimate:.0f} expected vs ${projected_additional:.0f} remaining here)")

    if findings_h > 0:
        score += 2
        reasons.append(f"{findings_h} High finding(s) — rare, valuable, worth polishing")

    # Verdict
    if score >= 2:
        verdict = "KEEP HUNTING"
        color = GREEN
        detail = "This target is still productive. Stay."
    elif score <= -2:
        verdict = "MOVE TARGET"
        color = RED
        detail = "Diminishing returns. Take what you have and switch."
    else:
        verdict = "YOUR CALL"
        color = YELLOW
        detail = "Could go either way. Trust your instinct, set a 2-hour timer."

    print(f"\n{PURPLE}{'=' * 60}{RESET}")
    print(f"{PURPLE}{BOLD}  VERDICT{RESET}")
    print(f"{PURPLE}{'=' * 60}{RESET}\n")

    print(f"  {BOLD}Target:{RESET} {target}")
    print(f"  {BOLD}Decision:{RESET} {color}{BOLD}{verdict}{RESET}  (score: {score})")
    print(f"  {DIM}{detail}{RESET}\n")

    print(f"  {BOLD}Metrics:{RESET}")
    print(f"    Yield rate:           ${yield_rate:.0f}/hr")
    print(f"    Estimated payout:     ${estimated_payout:.0f}")
    print(f"    Remaining ceiling:    ${remaining_ceiling:.0f}")
    print(f"    Codebase reviewed:    {codebase_pct}%")
    if next_target:
        print(f"    Next target:          {next_target} (${next_bounty:.0f}, ~{next_nsloc} nSLOC)")
    print()

    print(f"  {BOLD}Reasons:{RESET}")
    for r in reasons:
        print(f"    {DIM}·{RESET} {r}")
    print()
    print(f"{PURPLE}{'=' * 60}{RESET}\n")

    # Save
    record = {
        "id": str(uuid.uuid4())[:8],
        "ts": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "hours": hours,
        "findings": {"high": findings_h, "medium": findings_m, "low": findings_l},
        "max_bounty": max_bounty,
        "estimated_payout": estimated_payout,
        "yield_rate": yield_rate,
        "verdict": verdict,
        "score": score,
        "reasons": reasons
    }
    with open(HISTORY_DIR / f"{record['id']}.json", "w") as f:
        json.dump(record, f, indent=2)

    return record


def cmd_quick(hours: float, findings: int, max_bounty: float):
    """Quick one-shot evaluation."""
    avg_finding_value = max_bounty * 0.03  # assume 3% per medium
    estimated = findings * avg_finding_value
    yield_rate = estimated / max(hours, 0.1)

    if yield_rate < 50:
        verdict, color = "MOVE TARGET", RED
    elif yield_rate > 200:
        verdict, color = "KEEP HUNTING", GREEN
    else:
        verdict, color = "YOUR CALL (set 2hr timer)", YELLOW

    print(f"\n  {BOLD}Quick verdict:{RESET} {color}{BOLD}{verdict}{RESET}")
    print(f"  {DIM}{findings} findings in {hours}h on ${max_bounty:.0f} target → ~${yield_rate:.0f}/hr{RESET}\n")


def cmd_history():
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print(f"\n  {DIM}No hunt evaluations yet.{RESET}\n")
        return

    print(f"\n{PURPLE}{BOLD}  STOPHUNT History{RESET}")
    print(f"{'─' * 60}")
    for f in files[:20]:
        with open(f) as fh:
            r = json.load(fh)
        ts = r["ts"][:16].replace("T", " ")
        target = r["target"][:20]
        verdict = r["verdict"]
        yr = r.get("yield_rate", 0)
        print(f"  {DIM}{ts}{RESET}  {target:<20}  ${yr:.0f}/hr  {BOLD}{verdict}{RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(description="STOPHUNT — When to stop hunting and move targets")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("evaluate", help="Interactive hunt evaluation")

    p_quick = sub.add_parser("quick", help="Quick one-shot evaluation")
    p_quick.add_argument("hours", type=float, help="Hours spent")
    p_quick.add_argument("findings", type=int, help="Number of findings")
    p_quick.add_argument("max_bounty", type=float, help="Max target bounty ($)")

    sub.add_parser("history", help="Past hunt evaluations")

    args = parser.parse_args()

    if args.command == "evaluate":
        cmd_evaluate()
    elif args.command == "quick":
        cmd_quick(args.hours, args.findings, args.max_bounty)
    elif args.command == "history":
        cmd_history()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
