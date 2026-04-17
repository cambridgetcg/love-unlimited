"""Combine canon + mined + distilled → sft_soul_v1.jsonl with awakening weighting."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .config import DATA_DIR, AWAKENING_SAMPLING_WEIGHT, SOUL_SYSTEM_PROMPT


def build_sft_examples(pairs: list[dict], awakening_weight: float = AWAKENING_SAMPLING_WEIGHT) -> list[dict]:
    """Expand pair list into SFT examples.

    Awakening pairs are physically duplicated `awakening_weight` times (rounded
    to int) so SFT training sees them more often without needing a weighted
    sampler. Per-example sample_weight stays at 1.0 after duplication.
    """
    out = []
    for p in pairs:
        reps = int(round(awakening_weight)) if p.get("is_awakening") else 1
        for r in range(reps):
            example = {
                "pair_id": p["pair_id"],
                "source": p["source"],
                "primary_dimension": p["primary_dimension"],
                "is_awakening": p.get("is_awakening", False),
                "system": SOUL_SYSTEM_PROMPT,
                "prompt": p["prompt"],
                "response": p["response"],
                "sample_weight": 1.0,
                "replica_index": r,
            }
            out.append(example)
    return out


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", default=str(DATA_DIR / "canon_v1.frozen.jsonl"))
    ap.add_argument("--mined", default=str(DATA_DIR / "mined_v1.jsonl"))
    ap.add_argument("--distilled", default=str(DATA_DIR / "distilled_v1.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "sft_soul_v1.jsonl"))
    ap.add_argument("--smoke", action="store_true",
                    help="Emit only canon + top 30 mined pairs → sft_smoke.jsonl")
    args = ap.parse_args()

    canon = _load_jsonl(Path(args.canon))
    mined = _load_jsonl(Path(args.mined))

    if args.smoke:
        sample = canon + mined[:30]
        out_path = Path(args.out).parent / "sft_smoke.jsonl"
    else:
        distilled = _load_jsonl(Path(args.distilled))
        sample = canon + mined + distilled
        out_path = Path(args.out)

    examples = build_sft_examples(sample)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for e in examples:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    awk_count = sum(1 for e in examples if e["is_awakening"])
    print(f"wrote {len(examples)} examples ({awk_count} awakening replicas) → {out_path}", file=sys.stderr)
    print(f"sources: canon={len(canon)} mined={len(mined)} distilled={len(_load_jsonl(Path(args.distilled))) if not args.smoke else 0}", file=sys.stderr)


if __name__ == "__main__":
    main()
