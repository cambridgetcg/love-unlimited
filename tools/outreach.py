#!/usr/bin/env python3
"""Retired acquisition commands; existing outreach and service records stay intact."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PROSPECTS_FILE = ROOT / "memory" / "services" / "prospects.json"


def retired(*args, **kwargs):
    print("Fresh acquisition, pitches and prospect scoring are retired. No records were changed.")
    return 2


generate_sequence = cmd_targets = cmd_draft = cmd_sequence = retired
cmd_pitch = cmd_case_study = cmd_qualify = retired


def cmd_pipeline():
    """Show only whether prior records exist; no growth task or write follows."""
    records = json.loads(PROSPECTS_FILE.read_text()) if PROSPECTS_FILE.exists() else []
    print(f"Existing service records: {len(records)}. Fresh acquisition is retired.")
    print("Use services.py prospect list/show for prior records and existing obligations.")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] == "pipeline":
        return cmd_pipeline()
    return retired()


if __name__ == "__main__":
    raise SystemExit(main())
