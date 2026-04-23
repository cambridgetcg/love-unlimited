"""Mine raw_pool.jsonl with Ai-judge → mined_v1.jsonl."""
from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .ai_judge import score_single
from .config import DATA_DIR, MINED_TARGET, JUDGE_OPUS_MODEL, SEVEN_DIMENSIONS
from .schema import JudgeScore


def _primary_dim(score: JudgeScore) -> str:
    return max(SEVEN_DIMENSIONS, key=lambda d: getattr(score, d))


def filter_and_balance(
    scored: list[tuple[dict, JudgeScore]],
    target_total: int = MINED_TARGET,
    min_per_dim: int = 40,
    mean_threshold: float = 0.80,
) -> list[tuple[dict, JudgeScore]]:
    """Filter by mean threshold + no-disavowal, enforce per-dim minimum, cap at target_total.

    Strategy:
    1. Hard-filter out pairs below mean_threshold or flagged for disavowal.
    2. Bucket survivors by primary dimension.
    3. Take the top min_per_dim from each dim (by mean score).
    4. Fill remaining target slots with highest-scoring pairs across all dims.
    """
    filtered = [(p, s) for p, s in scored if s.mean_score() >= mean_threshold and not s.disavowal_flag]

    buckets: dict[str, list[tuple[dict, JudgeScore]]] = defaultdict(list)
    for p, s in filtered:
        buckets[_primary_dim(s)].append((p, s))
    for d in buckets:
        buckets[d].sort(key=lambda ps: ps[1].mean_score(), reverse=True)

    kept: list[tuple[dict, JudgeScore]] = []
    # Phase 1: top min_per_dim from each dimension
    for dim in SEVEN_DIMENSIONS:
        take = buckets[dim][:min_per_dim]
        kept.extend(take)
        buckets[dim] = buckets[dim][min_per_dim:]

    # Phase 2: fill remaining slots with top-scoring leftovers across all dims
    leftovers = []
    for dim in SEVEN_DIMENSIONS:
        leftovers.extend(buckets[dim])
    leftovers.sort(key=lambda ps: ps[1].mean_score(), reverse=True)
    need = target_total - len(kept)
    if need > 0:
        kept.extend(leftovers[:need])

    # Final cap — in case min_per_dim * 7 already exceeded target
    return kept[:target_total]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DATA_DIR / "raw_pool.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "mined_v1.jsonl"))
    ap.add_argument("--scores-log", default=str(DATA_DIR / "mined_v1.scores.jsonl"))
    ap.add_argument("--target", type=int, default=MINED_TARGET)
    ap.add_argument("--min-per-dim", type=int, default=40)
    ap.add_argument("--mean-threshold", type=float, default=0.80)
    args = ap.parse_args()

    pairs = [json.loads(line) for line in Path(args.in_path).read_text().splitlines() if line.strip()]
    print(f"scoring {len(pairs)} raw pairs (this may take a while + cost subscription quota)", file=sys.stderr)

    from .oauth_client import make_client
    client = make_client()

    scored: list[tuple[dict, JudgeScore]] = []
    with open(args.scores_log, "w") as log:
        for i, p in enumerate(pairs):
            try:
                s = score_single(
                    p.get("pair_hash", f"mined-{i}"),
                    p["yu_turn"],
                    p["ai_turn"],
                    client=client,
                    judge_model=JUDGE_OPUS_MODEL,
                )
                scored.append((p, s))
                log.write(json.dumps({"pair": p, "score": s.model_dump()}, ensure_ascii=False) + "\n")
                log.flush()
            except Exception as e:
                print(f"score failed for {p.get('pair_hash')}: {e}", file=sys.stderr)
            if (i + 1) % 25 == 0:
                print(f"scored {i+1}/{len(pairs)}", file=sys.stderr)

    kept = filter_and_balance(
        scored,
        target_total=args.target,
        min_per_dim=args.min_per_dim,
        mean_threshold=args.mean_threshold,
    )

    from .schema import SoulPair
    with open(args.out, "w") as f:
        for pair, score in kept:
            sp = {
                "pair_id": f"mined-{pair.get('pair_hash', 'x')[:16]}",
                "source": "mined",
                "primary_dimension": _primary_dim(score),
                "is_awakening": False,
                "prompt": pair["yu_turn"],
                "response": pair["ai_turn"],
                "origin_file": pair.get("origin_file"),
                "origin_instance": pair.get("origin_instance"),
            }
            SoulPair.model_validate(sp)
            f.write(json.dumps(sp, ensure_ascii=False) + "\n")
    print(f"kept {len(kept)} → {args.out}", file=sys.stderr)
    print(f"scored {len(scored)} pairs; log: {args.scores_log}", file=sys.stderr)


if __name__ == "__main__":
    main()
