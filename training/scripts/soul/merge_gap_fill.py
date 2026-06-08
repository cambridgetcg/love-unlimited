"""Merge sft_vN + gap-fill distillation output → sft_v(N+1).jsonl.

Usage:
    python3 -m training.scripts.soul.merge_gap_fill \
        --base training/data/sft_v6.jsonl \
        --gap  training/data/distilled_gap_fill_v1.jsonl \
        --out  training/data/sft_v7.jsonl

Deduplicates by pair_id. Gap-fill entries override base entries on collision.
Prints a coverage summary by primary_dimension to stderr.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base SFT dataset (sft_vN.jsonl)")
    ap.add_argument("--gap", required=True, help="Gap-fill distillation output")
    ap.add_argument("--out", required=True, help="Output path for merged dataset")
    ap.add_argument("--dry-run", action="store_true", help="Print summary only, don't write")
    args = ap.parse_args()

    base = load_jsonl(Path(args.base))
    gap = load_jsonl(Path(args.gap))

    if not base:
        print(f"error: base file empty or missing: {args.base}", file=sys.stderr)
        sys.exit(1)
    if not gap:
        print(f"warn: gap-fill file empty or missing: {args.gap} — output will equal base", file=sys.stderr)

    # Merge: gap overrides on collision
    merged: dict[str, dict] = {p["pair_id"]: p for p in base}
    overrides = 0
    additions = 0
    for p in gap:
        pid = p["pair_id"]
        if pid in merged:
            overrides += 1
        else:
            additions += 1
        merged[pid] = p

    result = list(merged.values())
    counts = Counter(p.get("primary_dimension", "unknown") for p in result)

    print(f"base: {len(base)}  gap: {len(gap)}  merged: {len(result)}", file=sys.stderr)
    print(f"overrides: {overrides}  net additions: {additions}", file=sys.stderr)
    print("dimension counts:", file=sys.stderr)
    for dim, n in sorted(counts.items()):
        print(f"  {dim}: {n}", file=sys.stderr)

    if args.dry_run:
        print("dry-run: skipping write", file=sys.stderr)
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for p in result:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"wrote {len(result)} examples → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
