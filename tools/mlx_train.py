#!/usr/bin/env python3
"""mlx_train.py — LoRA fine-tuning pipeline for Kingdom models.

Produces adapter weights that mlx_serve.py can hot-swap.
Training runs via `python -m mlx_lm.lora` subprocess.

Usage:
  mlx_train.py run --dataset heartbeat-triage [--epochs 3] [--adapter kingdom-v2]
  mlx_train.py run --dataset all [--epochs 3] [--adapter kingdom-v2]
  mlx_train.py eval --adapter kingdom-v2 --dataset heartbeat-triage
  mlx_train.py list
"""
import argparse
import json
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
LORA_CONFIG = MLX_DIR / "training" / "lora-config.json"
DATASETS_DIR = MLX_DIR / "training" / "datasets"
ADAPTERS_DIR = MLX_DIR / "adapters"
RUNS_DIR = MLX_DIR / "training" / "runs"
VENV_PYTHON = MLX_DIR / ".venv" / "bin" / "python3"


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit"}


def load_lora_config():
    try:
        with open(LORA_CONFIG) as f:
            return json.load(f)
    except Exception:
        return {"rank": 8, "learning_rate": 1e-4, "batch_size": 4, "epochs": 3}


def validate_dataset(path):
    """Validate a JSONL dataset file. Returns {"valid": bool, "count": int, "error": str}."""
    path = Path(path)
    if not path.exists():
        return {"valid": False, "count": 0, "error": f"File not found: {path}"}

    count = 0
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if "messages" not in entry:
                    return {"valid": False, "count": i,
                            "error": f"Line {i+1}: missing 'messages' key"}
                count += 1
    except json.JSONDecodeError as e:
        return {"valid": False, "count": count, "error": f"Line {count+1}: {e}"}

    if count == 0:
        return {"valid": False, "count": 0, "error": "Empty dataset"}
    return {"valid": True, "count": count, "error": None}


def split_dataset(source_path, out_dir, eval_fraction=0.2, seed=42):
    """Split a JSONL file into train.jsonl and valid.jsonl."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(source_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    random.seed(seed)
    random.shuffle(lines)

    split_idx = int(len(lines) * (1 - eval_fraction))
    train_lines = lines[:split_idx]
    valid_lines = lines[split_idx:]

    (out_dir / "train.jsonl").write_text("\n".join(train_lines) + "\n")
    (out_dir / "valid.jsonl").write_text("\n".join(valid_lines) + "\n")
    return len(train_lines), len(valid_lines)


def build_lora_args(model, data_dir, adapter_path, lora_config, n_train=0):
    """Build CLI arguments for `python -m mlx_lm.lora`."""
    batch_size = lora_config.get("batch_size", 4)
    epochs = lora_config.get("epochs", 3)
    # Calculate iters from epochs and dataset size
    iters = max(1, (n_train * epochs) // batch_size) if n_train > 0 else 300
    args = [
        "--model", model,
        "--data", str(data_dir),
        "--adapter-path", str(adapter_path),
        "--train",
        "--num-layers", "16",
        "--learning-rate", str(lora_config.get("learning_rate", 1e-4)),
        "--batch-size", str(batch_size),
        "--iters", str(iters),
    ]
    return args


def cmd_run(args):
    config = load_config()
    lora_config = load_lora_config()

    if args.epochs:
        lora_config["epochs"] = args.epochs

    adapter_name = args.adapter or f"kingdom-{datetime.now().strftime('%Y%m%d-%H%M')}"
    adapter_path = ADAPTERS_DIR / adapter_name
    adapter_path.mkdir(parents=True, exist_ok=True)

    # Collect datasets
    if args.dataset == "all":
        datasets = list(DATASETS_DIR.glob("*.jsonl"))
        if not datasets:
            print("No datasets found.", file=sys.stderr)
            sys.exit(1)
    else:
        ds_path = DATASETS_DIR / f"{args.dataset}.jsonl"
        val = validate_dataset(ds_path)
        if not val["valid"]:
            print(f"Dataset invalid: {val['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Dataset: {args.dataset} ({val['count']} examples)")
        datasets = [ds_path]

    # Merge all datasets if multiple
    run_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    if len(datasets) > 1:
        merged = run_dir / "merged.jsonl"
        with open(merged, "w") as out:
            for ds in datasets:
                with open(ds) as f:
                    out.write(f.read())
        split_src = merged
    else:
        split_src = datasets[0]

    # Split into train/valid
    data_dir = run_dir / "split"
    n_train, n_valid = split_dataset(split_src, data_dir)
    print(f"Split: {n_train} train, {n_valid} valid")

    # Build and run training
    cli_args = build_lora_args(
        config["base_model"], str(data_dir), str(adapter_path), lora_config,
        n_train=n_train
    )

    if not VENV_PYTHON.exists():
        print(f"ERROR: Venv not found at {VENV_PYTHON}", file=sys.stderr)
        print("Run: python3 -m venv ~/Desktop/Love/mlx/.venv && mlx/.venv/bin/pip install mlx mlx-lm")
        sys.exit(1)

    print(f"Training with adapter: {adapter_name}")
    print(f"Running: {VENV_PYTHON} -m mlx_lm.lora {' '.join(cli_args)}")

    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "mlx_lm.lora"] + cli_args,
        capture_output=False,  # Let output stream to terminal
    )

    if result.returncode != 0:
        print(f"Training failed (exit code {result.returncode})", file=sys.stderr)
        sys.exit(1)

    # Save run metadata
    meta = {
        "adapter": adapter_name,
        "dataset": args.dataset,
        "model": config["base_model"],
        "lora_config": lora_config,
        "train_examples": n_train,
        "valid_examples": n_valid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"\nTraining complete. Adapter saved to: mlx/adapters/{adapter_name}/")
    print(f"To use: POST /reload {{\"adapter\": \"{adapter_name}\"}}")


def cmd_eval(args):
    """Evaluate an adapter on a dataset."""
    config = load_config()
    ds_path = DATASETS_DIR / f"{args.dataset}.jsonl"
    val = validate_dataset(ds_path)
    if not val["valid"]:
        print(f"Dataset invalid: {val['error']}", file=sys.stderr)
        sys.exit(1)

    adapter_path = ADAPTERS_DIR / args.adapter
    if not adapter_path.exists():
        print(f"Adapter not found: {adapter_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating adapter '{args.adapter}' on '{args.dataset}' ({val['count']} examples)")

    # Run eval via mlx_lm.lora --test
    data_dir = Path(tempfile.mkdtemp()) / "split"
    split_dataset(ds_path, data_dir)

    if not VENV_PYTHON.exists():
        print(f"ERROR: Venv not found at {VENV_PYTHON}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "mlx_lm.lora",
         "--model", config["base_model"],
         "--adapter-path", str(adapter_path),
         "--data", str(data_dir),
         "--test"],
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"Evaluation failed (exit code {result.returncode})", file=sys.stderr)


def cmd_list(args):
    """List available adapters."""
    if not ADAPTERS_DIR.exists():
        print("No adapters.")
        return
    adapters = sorted(ADAPTERS_DIR.iterdir())
    if not adapters:
        print("No adapters.")
        return
    print("Available adapters:")
    for a in adapters:
        if a.is_dir():
            # Check for training run metadata
            meta_found = False
            for run in sorted(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else []:
                meta_file = run / "meta.json"
                if meta_file.exists():
                    try:
                        meta = json.load(open(meta_file))
                        if meta.get("adapter") == a.name:
                            print(f"  {a.name:24s}  dataset={meta.get('dataset', '?'):20s}  {meta.get('timestamp', '?')[:10]}")
                            meta_found = True
                            break
                    except Exception:
                        pass
            if not meta_found:
                print(f"  {a.name}")


def main():
    parser = argparse.ArgumentParser(description="MLX LoRA fine-tuning")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Train a LoRA adapter")
    p_run.add_argument("--dataset", required=True, help="Dataset name or 'all'")
    p_run.add_argument("--epochs", type=int, default=None)
    p_run.add_argument("--adapter", default=None, help="Adapter output name")

    p_eval = sub.add_parser("eval", help="Evaluate an adapter")
    p_eval.add_argument("--adapter", required=True)
    p_eval.add_argument("--dataset", required=True)

    sub.add_parser("list", help="List adapters")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"run": cmd_run, "eval": cmd_eval, "list": cmd_list}[args.command](args)


if __name__ == "__main__":
    main()
