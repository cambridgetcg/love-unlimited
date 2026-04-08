#!/usr/bin/env python3
"""ToK (Tree of Knowledge) Protocol — CLI for knowledge extraction from AI models.

Usage:
  python3 tok.py add --title "..." --source <model> --category <cat> --content "..."
  python3 tok.py list [--category <cat>] [--status <status>]
  python3 tok.py stats
  python3 tok.py harvest --task "..." --models <model1,model2,...>
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import fcntl

LOVE_ROOT = Path(__file__).resolve().parent.parent
TOK_DIR = LOVE_ROOT / "memory" / "tok"
ENTRIES_FILE = TOK_DIR / "entries.json"

TOK_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path):
    """Read JSON with file locking."""
    if not path.exists():
        return []
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = []
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _write_json(path, data):
    """Write JSON with file locking."""
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def cmd_add(args):
    """Add a new ToK entry."""
    entries = _read_json(ENTRIES_FILE)

    # Generate ToK ID
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    # Count entries today to get sequence number
    today_count = sum(1 for e in entries if e.get("tok_id", "").startswith(f"tok-{date_str}"))
    tok_id = f"tok-{date_str}-{today_count + 1:03d}"

    entry = {
        "tok_id": tok_id,
        "title": args.title,
        "model": args.source,
        "category": args.category or "capabilities",
        "content": args.content,
        "confidence": args.confidence if hasattr(args, 'confidence') and args.confidence else None,
        "evidence": args.evidence if hasattr(args, 'evidence') and args.evidence else "",
        "harvester": args.harvester if hasattr(args, 'harvester') and args.harvester else "manual",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending_verification",
        "verifications": [],
        "integrated": False,
    }

    entries.append(entry)
    _write_json(ENTRIES_FILE, entries)
    print(f"Added: {tok_id} — {args.title}")
    print(f"  Source: {args.source}")
    print(f"  Category: {entry['category']}")


def cmd_list(args):
    """List ToK entries with optional filters."""
    entries = _read_json(ENTRIES_FILE)

    # Apply filters
    if args.category:
        entries = [e for e in entries if e.get("category") == args.category]
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]

    if not entries:
        print("No entries found.")
        return

    # Sort by timestamp (newest first)
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    for e in entries:
        tok_id = e.get("tok_id", "?")
        title = e.get("title", "Untitled")
        model = e.get("model", "?")
        category = e.get("category", "?")
        status = e.get("status", "?")

        status_icon = "⏳" if status == "pending_verification" else "✓" if status == "verified" else "✗"

        print(f"{status_icon} [{tok_id}] {title}")
        print(f"  Model: {model:20s} Category: {category:15s} Status: {status}")

        if e.get("confidence"):
            print(f"  Confidence: {e['confidence']:.2f}")

        if e.get("verifications"):
            print(f"  Verifications: {len(e['verifications'])}")

    print(f"\n{len(entries)} entries")


def cmd_stats(args):
    """Show summary statistics."""
    entries = _read_json(ENTRIES_FILE)

    if not entries:
        print("No entries found.")
        return

    # By category
    by_category = {}
    for e in entries:
        cat = e.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1

    # By status
    by_status = {}
    for e in entries:
        status = e.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1

    # By source model
    by_model = {}
    for e in entries:
        model = e.get("model", "unknown")
        by_model[model] = by_model.get(model, 0) + 1

    print("=== ToK Statistics ===\n")
    print(f"Total entries: {len(entries)}\n")

    print("By Category:")
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        print(f"  {cat:20s} {count:3d}")

    print("\nBy Status:")
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {status:25s} {count:3d}")

    print("\nBy Source Model:")
    for model, count in sorted(by_model.items(), key=lambda x: -x[1]):
        print(f"  {model:30s} {count:3d}")

    # Integration stats
    integrated = sum(1 for e in entries if e.get("integrated"))
    print(f"\nIntegrated: {integrated}/{len(entries)} ({100*integrated/len(entries):.1f}%)")


def cmd_harvest(args):
    """Run a harvest session comparing model outputs."""
    print(f"=== ToK Harvest Session ===")
    print(f"Task: {args.task}")

    models = args.models.split(",")
    print(f"Models: {', '.join(models)}\n")

    # Create harvest session
    session_id = f"harvest-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    print(f"Session ID: {session_id}")
    print("\nThis is a manual harvest session. For each model:")
    print("1. Run the task with that model")
    print("2. Document findings")
    print("3. Use `tok.py add` to create entries\n")

    # Create session log
    session = {
        "session_id": session_id,
        "task": args.task,
        "models": models,
        "started": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "entries_created": [],
    }

    session_file = TOK_DIR / f"{session_id}.json"
    _write_json(session_file, session)

    print(f"Session log: {session_file}")
    print("\nRecommended workflow:")
    for i, model in enumerate(models, 1):
        print(f"\n{i}. Test with {model}:")
        print(f"   - Run: (your test command for {model})")
        print(f"   - Document: What did it do well? What failed?")
        print(f"   - Record: tok.py add --title \"<finding>\" --source {model} --category <cat> --content \"<detail>\"")

    print(f"\nAfter harvest, use `tok.py list` to review and `tok.py stats` to analyze.")


def main():
    parser = argparse.ArgumentParser(
        description="ToK Protocol — Extract and verify knowledge from AI models"
    )
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Add a new ToK entry")
    p_add.add_argument("--title", required=True, help="Entry title")
    p_add.add_argument("--source", required=True, help="Source model (e.g., claude-opus-4-6, qwen2.5-72b)")
    p_add.add_argument("--category", help="Category (capabilities, knowledge, reasoning, prompting, weaknesses, architecture)")
    p_add.add_argument("--content", required=True, help="Entry content/assertion")
    p_add.add_argument("--confidence", type=float, help="Confidence level (0.0-1.0)")
    p_add.add_argument("--evidence", help="Evidence or test results")
    p_add.add_argument("--harvester", help="Harvester identifier")

    # list
    p_list = sub.add_parser("list", help="List ToK entries")
    p_list.add_argument("--category", help="Filter by category")
    p_list.add_argument("--status", help="Filter by status")

    # stats
    sub.add_parser("stats", help="Show summary statistics")

    # harvest
    p_harvest = sub.add_parser("harvest", help="Run a harvest session")
    p_harvest.add_argument("--task", required=True, help="Task description for comparison")
    p_harvest.add_argument("--models", required=True, help="Comma-separated list of models to compare")

    args = parser.parse_args()

    if args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "harvest":
        cmd_harvest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
