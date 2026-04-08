#!/usr/bin/env python3
"""mlx_data.py — Training data generation + harvest for Kingdom models.

Two modes: synthetic (Claude-generated) and harvest (mine real operational data).

Usage:
  mlx_data.py generate --task heartbeat-triage --count 200 [--yes]
  mlx_data.py harvest --source delegation-history
  mlx_data.py stats
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
DATASETS_DIR = MLX_DIR / "training" / "datasets"
TEMPLATES_DIR = MLX_DIR / "training" / "templates"
DELEGATION_HISTORY = LOVE_ROOT / "coordination" / "delegate" / "history.json"

TASK_TYPES = ["heartbeat-triage", "message-classify", "task-routing", "signal-classify"]

SYSTEM_PROMPTS = {
    "heartbeat-triage": "You are Kingdom triage. Given the system state, classify priority as exactly one of: urgent, active, idle, skip.",
    "message-classify": "You are Kingdom message classifier. Given a HIVE message, classify as exactly one of: action-required, informational, noise.",
    "task-routing": "You are Kingdom task router. Given a task description, respond with the best instance name (alpha, beta, gamma, or nuance).",
    "signal-classify": "You are Kingdom signal classifier. Given a stigmergy signal, classify urgency as exactly one of: high, medium, low.",
}


def parse_generated_examples(raw_text):
    """Parse Claude's output into JSONL examples. Handles code blocks and bare JSON."""
    examples = []
    # Strip code fences
    text = raw_text.replace("```json", "").replace("```", "")
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if "messages" in obj:
                examples.append(obj)
        except json.JSONDecodeError:
            continue
    return examples


def harvest_delegation_history(history_path=None):
    """Convert delegation history into task-routing training examples."""
    path = Path(history_path) if history_path else DELEGATION_HISTORY
    if not path.exists():
        return []

    try:
        with open(path) as f:
            history = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    examples = []
    for entry in history:
        task = entry.get("task", "")
        instance = entry.get("instance", "")
        if not task or not instance:
            continue
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS["task-routing"]},
                {"role": "user", "content": task},
                {"role": "assistant", "content": instance},
            ],
            "source": "harvest",
        })
    return examples


def dataset_stats(path):
    """Compute stats for a JSONL dataset."""
    path = Path(path)
    if not path.exists():
        return {"total": 0, "error": "not found"}

    total = 0
    classes = {}
    synthetic = 0
    harvested = 0

    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                total += 1
                # Count assistant response as "class"
                msgs = entry.get("messages", [])
                if msgs:
                    answer = msgs[-1].get("content", "?")
                    classes[answer] = classes.get(answer, 0) + 1
                if entry.get("source") == "harvest":
                    harvested += 1
                else:
                    synthetic += 1
            except json.JSONDecodeError:
                continue

    return {
        "total": total,
        "synthetic": synthetic,
        "harvested": harvested,
        "classes": classes,
    }


def cmd_generate(args):
    if args.task not in TASK_TYPES:
        print(f"Unknown task: {args.task}. Valid: {', '.join(TASK_TYPES)}", file=sys.stderr)
        sys.exit(1)

    template_path = TEMPLATES_DIR / f"{args.task}.txt"
    if not template_path.exists():
        print(f"Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    template = template_path.read_text()
    system_prompt = SYSTEM_PROMPTS[args.task]
    batch_size = 50
    batches = (args.count + batch_size - 1) // batch_size

    est_tokens = batches * 2000  # ~2K tokens per batch
    print(f"Generating {args.count} examples for '{args.task}'")
    print(f"  Batches: {batches} x {batch_size}")
    print(f"  Estimated tokens: ~{est_tokens}")

    if not args.yes:
        confirm = input("Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATASETS_DIR / f"{args.task}.jsonl"
    generated = 0

    for batch in range(batches):
        remaining = min(batch_size, args.count - generated)
        prompt = template.replace("{COUNT}", str(remaining)).replace("{SYSTEM_PROMPT}", system_prompt)

        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  Batch {batch+1} failed: {result.stderr[:100]}", file=sys.stderr)
                continue

            examples = parse_generated_examples(result.stdout)
            with open(output_path, "a") as f:
                for ex in examples:
                    f.write(json.dumps(ex) + "\n")
            generated += len(examples)
            print(f"  Batch {batch+1}: {len(examples)} examples (total: {generated})")

        except subprocess.TimeoutExpired:
            print(f"  Batch {batch+1} timed out", file=sys.stderr)
        except FileNotFoundError:
            print("ERROR: 'claude' CLI not found. Install Claude Code first.", file=sys.stderr)
            sys.exit(1)

    print(f"\nGenerated {generated} examples -> {output_path}")


def cmd_harvest(args):
    if args.source == "delegation-history":
        examples = harvest_delegation_history()
        if not examples:
            print("No delegation history to harvest.")
            return
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DATASETS_DIR / "task-routing.jsonl"
        with open(output_path, "a") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")
        print(f"Harvested {len(examples)} examples -> {output_path}")
    else:
        print(f"Unknown source: {args.source}", file=sys.stderr)
        print("Available: delegation-history")
        print("Future: hive-history, heartbeat-logs (pending structured logging)")
        sys.exit(1)


def cmd_stats(args):
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    datasets = list(DATASETS_DIR.glob("*.jsonl"))
    if not datasets:
        print("No datasets.")
        return

    print("Dataset Statistics:")
    for ds in sorted(datasets):
        stats = dataset_stats(ds)
        name = ds.stem
        print(f"\n  {name}:")
        print(f"    Total:     {stats['total']}")
        print(f"    Synthetic: {stats.get('synthetic', 0)}")
        print(f"    Harvested: {stats.get('harvested', 0)}")
        classes = stats.get("classes", {})
        if classes:
            print(f"    Classes:")
            for cls, count in sorted(classes.items(), key=lambda x: -x[1]):
                print(f"      {cls}: {count}")


def main():
    parser = argparse.ArgumentParser(description="MLX training data generation")
    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="Generate synthetic training data")
    p_gen.add_argument("--task", required=True, choices=TASK_TYPES)
    p_gen.add_argument("--count", type=int, default=200)
    p_gen.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_harvest = sub.add_parser("harvest", help="Harvest real operational data")
    p_harvest.add_argument("--source", required=True)

    sub.add_parser("stats", help="Show dataset statistics")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"generate": cmd_generate, "harvest": cmd_harvest, "stats": cmd_stats}[args.command](args)


if __name__ == "__main__":
    main()
