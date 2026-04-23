"""Alpha spot-audits random canon pairs for hollow-template + voice-drift."""
from __future__ import annotations
import argparse
import json
import random
import sys
from pathlib import Path

from .ai_judge import score_single
from .config import DATA_DIR, EVAL_DIR, JUDGE_ALPHA_MODEL
from .schema import JudgeScore


def build_report(sample: list[dict], scores: list[JudgeScore], flag_threshold: int = 3) -> dict:
    """Aggregate per-pair scores into a flag report.

    A pair is flagged if hollow_template_flag is True OR mean_score < 0.70.
    The recommendation is "revisit with Yu" if flagged_count > flag_threshold,
    else "canon OK to freeze".
    """
    flagged = []
    for pair, score in zip(sample, scores):
        if score.hollow_template_flag or score.mean_score() < 0.70:
            flagged.append({
                "pair_id": pair["pair_id"],
                "score": score.model_dump(),
                "prompt": pair["prompt"],
                "response": pair["response"],
            })
    return {
        "sampled": len(sample),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "recommendation": "revisit with Yu" if len(flagged) > flag_threshold else "canon OK to freeze",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DATA_DIR / "canon_v1.jsonl"))
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default=str(EVAL_DIR / "canon_audit_report.json"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--flag-threshold", type=int, default=3)
    args = ap.parse_args()

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"error: canon file not found at {in_path}. Run canon curation (Tasks 6-7) first.", file=sys.stderr)
        sys.exit(2)

    pairs = [json.loads(line) for line in in_path.read_text().splitlines() if line.strip()]
    random.seed(args.seed)
    sample = random.sample(pairs, k=min(args.n, len(pairs)))

    from .oauth_client import make_client
    client = make_client()

    scores: list[JudgeScore] = []
    for p in sample:
        score = score_single(
            p["pair_id"],
            p["prompt"],
            p["response"],
            client=client,
            judge_model=JUDGE_ALPHA_MODEL,
        )
        scores.append(score)

    report = build_report(sample, scores, flag_threshold=args.flag_threshold)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"flagged {report['flagged_count']}/{report['sampled']} — {report['recommendation']}", file=sys.stderr)


if __name__ == "__main__":
    main()
