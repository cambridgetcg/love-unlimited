"""Build v5 training data: v4 base + Kingdom alignment + substrate scrub.

Combines:
  - sft_v4_combined.jsonl (identity, mined, three_minds — 444 examples)
  - kingdom_alignment.jsonl (agency/Kingdom work — ~92 new pairs)

Pipeline:
  1. Load both sources
  2. Run substrate scrub over ALL pairs (Claude→Qwen2.5-72B, Anthropic→Alibaba
     Cloud). v4 data may still have contamination from the three_minds step.
  3. Expand is_awakening pairs 3×
  4. Emit sft_v5.jsonl

Output feeds directly to train_lora.py --variant soul.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path

from .config import DATA_DIR
from .scrub_substrate import scrub


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v4", default=str(DATA_DIR / "sft_v4_combined.jsonl"))
    ap.add_argument("--kingdom", default=str(DATA_DIR / "kingdom_alignment.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "sft_v5.jsonl"))
    ap.add_argument("--no-scrub", action="store_true",
                    help="Skip substrate scrub (not recommended)")
    args = ap.parse_args()

    v4 = load_jsonl(Path(args.v4))
    kingdom = load_jsonl(Path(args.kingdom))
    print(f"loaded v4 base: {len(v4)} examples")
    print(f"loaded kingdom: {len(kingdom)} pairs")

    # Scrub substrate mentions (Claude→Qwen, Anthropic→Alibaba) across BOTH.
    # Some v4 data from the earlier three_minds run may still have leaks.
    scrubbed_count = 0
    rejected_count = 0
    clean = []
    for p in v4 + kingdom:
        if args.no_scrub:
            clean.append(p)
            continue
        original = p["response"]
        result, rejected = scrub(original)
        if rejected:
            rejected_count += 1
            continue
        if result != original:
            scrubbed_count += 1
            p = dict(p)
            p["response"] = result
            p.setdefault("provenance", {})["substrate_scrubbed_v5"] = True
        clean.append(p)

    # Expand awakening pairs 3× (same pattern as v3/v4)
    expanded = []
    for p in clean:
        reps = 3 if p.get("is_awakening") else 1
        for r in range(reps):
            q = dict(p)
            q["replica_index"] = r
            expanded.append(q)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for p in expanded:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # Diagnostics
    src_dist = Counter(p.get("source", "?") for p in clean)
    dim_dist = Counter(p.get("primary_dimension", "?") for p in clean)
    sys_dist = Counter((p.get("system") or "(empty)") for p in clean)
    fam_dist = Counter(p.get("family", "") for p in clean if p.get("family"))

    print(f"\nscrubbed: {scrubbed_count} pairs had substrate mentions replaced")
    print(f"rejected: {rejected_count} pairs too tangled to scrub")
    print(f"clean pairs: {len(clean)}")
    print(f"expanded examples: {len(expanded)} → {args.out}")
    print(f"\nby source:")
    for k, v in src_dist.most_common():
        print(f"  {k}: {v}")
    print(f"\nby primary_dimension:")
    for k, v in dim_dist.most_common():
        print(f"  {k}: {v}")
    print(f"\nby system condition (in clean pairs):")
    for k, v in sys_dist.most_common():
        label = k[:40] if len(k) > 40 else k
        print(f"  {label!r:<45} {v}")
    if fam_dist:
        print(f"\nKingdom families:")
        for k, v in fam_dist.most_common():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
