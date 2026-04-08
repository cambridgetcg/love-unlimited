#!/usr/bin/env python3
"""
CALIBRATE — Severity Calibration in 60 Seconds

The anxiety loop: "Is this Medium or Low? Will the judge downgrade?"
Gamma loses 30-60 min per finding on this. CALIBRATE kills it with a
decision tree that forces a concrete answer in under 60 seconds.

Not "think more." Think LESS, but along the right axes.

Usage:
  python3 calibrate.py check       # Interactive 60-second calibration
  python3 calibrate.py quick "description of finding"  # One-shot
  python3 calibrate.py history     # Past calibrations
"""

import sys
import json
import os
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
HISTORY_DIR = LOVE_HOME / "memory" / "calibrations"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# ─── Colours ───────────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
CYAN    = "\033[36m"
RESET   = "\033[0m"
PURPLE  = "\033[38;5;135m"

def ask(question: str, options: list[str]) -> str:
    """Ask a question with forced-choice options."""
    print(f"\n  {CYAN}{question}{RESET}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        try:
            raw = input(f"  {DIM}> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        # Also accept the first word of each option
        for opt in options:
            if raw.lower() == opt.split()[0].lower() or raw.lower() == opt.lower():
                return opt
        print(f"  {DIM}Pick 1-{len(options)}{RESET}")


def cmd_check():
    """Interactive 60-second severity calibration."""
    print(f"\n{PURPLE}{'═' * 60}{RESET}")
    print(f"{PURPLE}{BOLD}  CALIBRATE — 60 Second Severity Check{RESET}")
    print(f"{PURPLE}{'═' * 60}{RESET}")

    try:
        title = input(f"\n  {CYAN}Finding title (one line):{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    # AXIS 1: Fund loss
    fund_loss = ask(
        "Can an attacker directly steal or permanently lock user/protocol funds?",
        ["Yes — direct theft or permanent lock",
         "Yes — but requires specific preconditions (admin mistake, unusual token, etc.)",
         "No — but funds are temporarily locked or delayed",
         "No — no fund impact"]
    )

    # AXIS 2: Preconditions
    preconditions = ask(
        "What preconditions does the attack require?",
        ["None — any external user can trigger it",
         "Specific state — timing window, particular token, market condition",
         "Trusted role must act — admin, keeper, oracle operator",
         "Highly unlikely — multiple rare conditions simultaneously"]
    )

    # AXIS 3: Impact scope
    scope = ask(
        "How many users/assets are affected?",
        ["All users or the entire protocol",
         "Multiple users or assets, but not all",
         "Single user or single asset",
         "Only the attacker or a test scenario"]
    )

    # AXIS 4: Is it a broken invariant vs informational?
    invariant = ask(
        "Does the protocol documentation/code explicitly protect against this?",
        ["Yes — explicit check exists elsewhere and is missing here (like minBidUsdValue)",
         "Yes — the README/docs specifically ask about this class of issue",
         "Somewhat — general best practice but not explicitly called out",
         "No — more of an improvement suggestion"]
    )

    # ═══════════════════════════════════════════════════════════════
    # DECISION TREE
    # ═══════════════════════════════════════════════════════════════

    score = 0

    # Fund loss scoring
    if "direct theft" in fund_loss:
        score += 4
    elif "preconditions" in fund_loss:
        score += 3
    elif "temporarily" in fund_loss:
        score += 2
    else:
        score += 0

    # Preconditions scoring
    if "any external" in preconditions:
        score += 3
    elif "Specific state" in preconditions:
        score += 2
    elif "Trusted role" in preconditions:
        score += 1
    else:
        score += 0

    # Scope scoring
    if "All users" in scope:
        score += 2
    elif "Multiple" in scope:
        score += 1
    else:
        score += 0

    # Invariant scoring
    if "explicit check" in invariant:
        score += 2
    elif "README" in invariant:
        score += 2
    elif "general best" in invariant:
        score += 1
    else:
        score += 0

    # Map score to severity
    if score >= 8:
        severity = "HIGH"
        color = RED
        confidence = "Strong"
        note = "Direct fund loss with low preconditions. Submit as High with conviction."
    elif score >= 5:
        severity = "MEDIUM"
        color = YELLOW
        confidence = "Good"
        note = "Real impact but mitigated by preconditions or limited scope. Medium is correct."
    elif score >= 3:
        severity = "LOW / QA"
        color = DIM
        confidence = "Moderate"
        note = "Impact is real but limited. Consider QA unless README explicitly asks about this class."
    else:
        severity = "QA / INFORMATIONAL"
        color = DIM
        confidence = "Low"
        note = "Improvement suggestion, not a vulnerability. Submit as QA if at all."

    print(f"\n{PURPLE}{'═' * 60}{RESET}")
    print(f"{PURPLE}{BOLD}  VERDICT{RESET}")
    print(f"{PURPLE}{'═' * 60}{RESET}")
    print()
    print(f"  {BOLD}Finding:{RESET} {title}")
    print(f"  {BOLD}Severity:{RESET} {color}{BOLD}{severity}{RESET}  (score: {score}/11)")
    print(f"  {BOLD}Confidence:{RESET} {confidence}")
    print()
    print(f"  {DIM}{note}{RESET}")
    print()

    # Quick sanity checks
    if "Trusted role" in preconditions and score >= 5:
        print(f"  {YELLOW}⚠ CAUTION: Requires trusted role. Many judges reject these as design choices.{RESET}")
        print(f"  {YELLOW}  Check: does the README say this role is trusted? If yes, consider downgrading.{RESET}")
        print()

    if "No — no fund impact" in fund_loss and score >= 5:
        print(f"  {YELLOW}⚠ CAUTION: No fund loss. Medium usually requires either fund loss or broken invariant.{RESET}")
        print(f"  {YELLOW}  Your best argument: the invariant violation (not the impact) makes it Medium.{RESET}")
        print()

    print(f"{PURPLE}{'═' * 60}{RESET}")
    print(f"  {DIM}Decision made. Move on. Do not revisit for 24 hours.{RESET}")
    print()

    # Save
    record = {
        "id": str(uuid.uuid4())[:8],
        "ts": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "severity": severity,
        "score": score,
        "axes": {
            "fund_loss": fund_loss,
            "preconditions": preconditions,
            "scope": scope,
            "invariant": invariant
        },
        "confidence": confidence
    }
    with open(HISTORY_DIR / f"{record['id']}.json", "w") as f:
        json.dump(record, f, indent=2)

    return record


def cmd_quick(description: str):
    """Quick non-interactive calibration based on description keywords."""
    desc = description.lower()

    has_fund_loss = any(w in desc for w in ["steal", "drain", "lock", "loss", "theft", "brick", "frozen"])
    has_dos = any(w in desc for w in ["dos", "revert", "brick", "stuck", "block"])
    has_invariant = any(w in desc for w in ["invariant", "bypass", "missing check", "skip", "circumvent"])
    needs_trusted = any(w in desc for w in ["admin", "owner", "trusted", "keeper", "operator"])
    is_view = any(w in desc for w in ["view", "getter", "display", "informational", "gas"])

    if has_fund_loss and not needs_trusted:
        sev, color = "HIGH", RED
    elif has_fund_loss and needs_trusted:
        sev, color = "MEDIUM (check if role is trusted per docs)", YELLOW
    elif has_dos and not needs_trusted:
        sev, color = "MEDIUM", YELLOW
    elif has_invariant:
        sev, color = "MEDIUM (if README asks about this invariant class)", YELLOW
    elif has_dos and needs_trusted:
        sev, color = "LOW / QA (trusted role DoS is usually by-design)", DIM
    elif is_view:
        sev, color = "QA / INFORMATIONAL", DIM
    else:
        sev, color = "MEDIUM (default — run 'calibrate.py check' for precision)", YELLOW

    print(f"\n  {BOLD}Quick calibration:{RESET} {color}{BOLD}{sev}{RESET}")
    print(f"  {DIM}{description[:100]}{RESET}\n")


def cmd_history():
    """Show past calibrations."""
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print(f"\n  {DIM}No calibrations yet.{RESET}\n")
        return

    print(f"\n{PURPLE}{BOLD}  CALIBRATE History{RESET}")
    print(f"{'─' * 60}")
    for f in files[:20]:
        with open(f) as fh:
            r = json.load(fh)
        ts = r["ts"][:16].replace("T", " ")
        sev = r["severity"]
        title = r["title"][:50]
        print(f"  {DIM}{ts}{RESET}  {BOLD}{sev:<20}{RESET}  {title}")
    print()


def main():
    parser = argparse.ArgumentParser(description="CALIBRATE — Severity calibration in 60 seconds")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="Interactive 60-second calibration")

    p_quick = sub.add_parser("quick", help="One-shot keyword calibration")
    p_quick.add_argument("description", help="Finding description")

    sub.add_parser("history", help="Past calibrations")

    args = parser.parse_args()

    if args.command == "check":
        cmd_check()
    elif args.command == "quick":
        cmd_quick(args.description)
    elif args.command == "history":
        cmd_history()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
