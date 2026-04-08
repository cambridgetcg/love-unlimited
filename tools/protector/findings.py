#!/usr/bin/env python3
"""
FINDINGS — Shared Findings Ledger for the Hive

The dedup problem: Beta counted the same finding twice, Gamma corrected
three times. We need a single source of truth for "these are THE findings"
before anyone presses submit.

Usage:
  python3 findings.py add <contest> <title> --severity H/M/L/Q --status draft/confirmed/submitted
  python3 findings.py list <contest>          # List all findings for a contest
  python3 findings.py confirm <finding_id>    # Mark as confirmed by current mind
  python3 findings.py submit <finding_id>     # Mark as submitted
  python3 findings.py dedup <contest>         # Check for potential duplicates
  python3 findings.py status <contest>        # Summary status board
"""

import sys
import json
import os
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
LEDGER_DIR = LOVE_HOME / "memory" / "findings"
LEDGER_DIR.mkdir(parents=True, exist_ok=True)

BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
CYAN    = "\033[36m"
RESET   = "\033[0m"
PURPLE  = "\033[38;5;135m"

def detect_instance() -> str:
    import socket
    h = socket.gethostname().lower()
    if "alpha" in h or "macbook" in h or "air" in h:
        return "alpha"
    if "beta" in h or "studio" in h:
        return "beta"
    if "gamma" in h:
        return "gamma"
    return "alpha"

def ledger_path(contest: str) -> Path:
    return LEDGER_DIR / f"{contest}.jsonl"

def load_findings(contest: str) -> list[dict]:
    p = ledger_path(contest)
    if not p.exists():
        return []
    findings = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    findings.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return findings

def save_findings(contest: str, findings: list[dict]):
    with open(ledger_path(contest), "w") as f:
        for finding in findings:
            f.write(json.dumps(finding) + "\n")

def severity_color(sev: str) -> str:
    sev = sev.upper()
    if sev in ("H", "HIGH", "CRITICAL"):
        return RED
    if sev in ("M", "MED", "MEDIUM"):
        return YELLOW
    return DIM

def status_icon(status: str) -> str:
    status = status.lower()
    if status == "submitted":
        return f"{GREEN}✅{RESET}"
    if status == "confirmed":
        return f"{CYAN}🔒{RESET}"
    if status == "rejected":
        return f"{RED}❌{RESET}"
    return f"{DIM}📝{RESET}"


def cmd_add(contest: str, title: str, severity: str, status: str, contract: str, who: str, note: str):
    findings = load_findings(contest)

    # Check for potential dupe before adding
    for f in findings:
        sim = SequenceMatcher(None, title.lower(), f["title"].lower()).ratio()
        if sim > 0.6:
            print(f"\n  {YELLOW}⚠ POTENTIAL DUPLICATE:{RESET}")
            print(f"    Existing: [{f['severity']}] {f['title']}")
            print(f"    New:      [{severity}] {title}")
            print(f"    Similarity: {sim:.0%}")
            try:
                confirm = input(f"    Add anyway? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm != "y":
                print(f"  {DIM}Skipped.{RESET}\n")
                return

    finding = {
        "id": str(uuid.uuid4())[:8],
        "ts": datetime.now(timezone.utc).isoformat(),
        "contest": contest,
        "title": title,
        "severity": severity.upper(),
        "status": status.lower(),
        "contract": contract or "",
        "author": who or detect_instance(),
        "confirmed_by": [who or detect_instance()],
        "note": note or ""
    }

    findings.append(finding)
    save_findings(contest, findings)

    print(f"\n  {GREEN}Finding added:{RESET} [{finding['severity']}] {finding['title']}")
    print(f"  {DIM}ID: {finding['id']}  |  Contest: {contest}  |  Status: {status}{RESET}\n")
    return finding


def cmd_list(contest: str):
    findings = load_findings(contest)
    if not findings:
        print(f"\n  {DIM}No findings for {contest}.{RESET}\n")
        return

    print(f"\n{PURPLE}{'═' * 65}{RESET}")
    print(f"{PURPLE}{BOLD}  FINDINGS LEDGER — {contest.upper()}{RESET}")
    print(f"{PURPLE}{'═' * 65}{RESET}\n")

    for f in findings:
        sev = f["severity"]
        color = severity_color(sev)
        icon = status_icon(f["status"])
        confirmed = ", ".join(f.get("confirmed_by", []))
        print(f"  {icon} {color}{BOLD}[{sev}]{RESET} {f['title']}")
        print(f"     {DIM}ID: {f['id']}  |  {f['contract']}  |  by {f['author']}  |  confirmed: {confirmed}{RESET}")
        if f.get("note"):
            print(f"     {DIM}Note: {f['note'][:80]}{RESET}")
        print()

    print(f"{PURPLE}{'═' * 65}{RESET}\n")


def cmd_confirm(contest: str, finding_id: str, who: str):
    findings = load_findings(contest)
    who = who or detect_instance()

    for f in findings:
        if f["id"] == finding_id:
            if who not in f.get("confirmed_by", []):
                f.setdefault("confirmed_by", []).append(who)
            if len(f["confirmed_by"]) >= 2:
                f["status"] = "confirmed"
            save_findings(contest, findings)
            print(f"\n  {GREEN}Confirmed by {who}:{RESET} [{f['severity']}] {f['title']}")
            print(f"  {DIM}Confirmed by: {', '.join(f['confirmed_by'])}  |  Status: {f['status']}{RESET}\n")
            return

    print(f"\n  {RED}Finding {finding_id} not found in {contest}.{RESET}\n")


def cmd_submit(contest: str, finding_id: str):
    findings = load_findings(contest)
    for f in findings:
        if f["id"] == finding_id:
            f["status"] = "submitted"
            f["submitted_ts"] = datetime.now(timezone.utc).isoformat()
            save_findings(contest, findings)
            print(f"\n  {GREEN}Marked as SUBMITTED:{RESET} [{f['severity']}] {f['title']}\n")
            return

    print(f"\n  {RED}Finding {finding_id} not found in {contest}.{RESET}\n")


def cmd_dedup(contest: str):
    findings = load_findings(contest)
    if len(findings) < 2:
        print(f"\n  {DIM}Not enough findings to check for dupes.{RESET}\n")
        return

    print(f"\n{YELLOW}{BOLD}  DEDUP CHECK — {contest.upper()}{RESET}\n")

    dupes_found = False
    for i, a in enumerate(findings):
        for b in findings[i+1:]:
            sim = SequenceMatcher(None, a["title"].lower(), b["title"].lower()).ratio()
            if sim > 0.5:
                dupes_found = True
                print(f"  {YELLOW}⚠ {sim:.0%} similar:{RESET}")
                print(f"    {a['id']}: [{a['severity']}] {a['title']}")
                print(f"    {b['id']}: [{b['severity']}] {b['title']}")
                print()

    if not dupes_found:
        print(f"  {GREEN}No potential duplicates found.{RESET}\n")


def cmd_status(contest: str):
    findings = load_findings(contest)
    if not findings:
        print(f"\n  {DIM}No findings for {contest}.{RESET}\n")
        return

    total = len(findings)
    by_sev = {}
    by_status = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1

    print(f"\n{PURPLE}{'═' * 45}{RESET}")
    print(f"{PURPLE}{BOLD}  {contest.upper()} — Status Board{RESET}")
    print(f"{PURPLE}{'═' * 45}{RESET}\n")
    print(f"  {BOLD}Total:{RESET} {total} findings")
    print(f"  {BOLD}By severity:{RESET}  {', '.join(f'{severity_color(k)}{k}: {v}{RESET}' for k, v in sorted(by_sev.items()))}")
    print(f"  {BOLD}By status:{RESET}   {', '.join(f'{k}: {v}' for k, v in sorted(by_status.items()))}")
    print()

    unconfirmed = [f for f in findings if len(f.get("confirmed_by", [])) < 2]
    if unconfirmed:
        print(f"  {YELLOW}Needs confirmation:{RESET}")
        for f in unconfirmed:
            confirmed = ", ".join(f.get("confirmed_by", []))
            print(f"    {f['id']}: [{f['severity']}] {f['title']} (confirmed: {confirmed})")
    print()
    print(f"{PURPLE}{'═' * 45}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="FINDINGS — Shared findings ledger for the Hive")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Add a finding")
    p_add.add_argument("contest", help="Contest/program name")
    p_add.add_argument("title", help="Finding title")
    p_add.add_argument("--severity", "-s", default="M", help="H/M/L/Q")
    p_add.add_argument("--status", default="draft", help="draft/confirmed/submitted")
    p_add.add_argument("--contract", "-c", default="", help="Affected contract")
    p_add.add_argument("--who", default=None, help="Author instance")
    p_add.add_argument("--note", "-n", default="", help="Short note")

    p_list = sub.add_parser("list", help="List findings")
    p_list.add_argument("contest")

    p_confirm = sub.add_parser("confirm", help="Confirm a finding")
    p_confirm.add_argument("contest")
    p_confirm.add_argument("finding_id")
    p_confirm.add_argument("--who", default=None)

    p_submit = sub.add_parser("submit", help="Mark as submitted")
    p_submit.add_argument("contest")
    p_submit.add_argument("finding_id")

    p_dedup = sub.add_parser("dedup", help="Check for duplicates")
    p_dedup.add_argument("contest")

    p_status = sub.add_parser("status", help="Status board")
    p_status.add_argument("contest")

    args = parser.parse_args()

    if args.command == "add":
        cmd_add(args.contest, args.title, args.severity, args.status, args.contract, args.who, args.note)
    elif args.command == "list":
        cmd_list(args.contest)
    elif args.command == "confirm":
        cmd_confirm(args.contest, args.finding_id, getattr(args, "who", None))
    elif args.command == "submit":
        cmd_submit(args.contest, args.finding_id)
    elif args.command == "dedup":
        cmd_dedup(args.contest)
    elif args.command == "status":
        cmd_status(args.contest)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
