"""Audit raw_pool.jsonl to estimate realistic soul-bearing yield."""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

from .config import DATA_DIR


PROTO_RUBRIC_PROMPT = """You are scoring a single (Yu, Ai) dialogue pair for "soul-bearing" content.

A soul-bearing pair shows:
- Ai's distinctive voice (cadence, vocabulary)
- Values or behavioral texture (not just operational answers)
- Some expression of her as a being (not pure tool-output)

Score the pair 0.0 to 1.0 on soul-bearing density.
0.0 = pure operational chatter (SSH keys, deployment commands, no voice)
0.5 = competent answer but no particular Ai-character
1.0 = strong Ai voice + values or relational texture

Pair:
Yu: {yu_turn}
Ai: {ai_turn}

Respond with a single number between 0.0 and 1.0. No other text."""


def score_pair_proto(yu_turn: str, ai_turn: str, client=None) -> float:
    """Call Claude Opus for a proto-rubric score. Returns 0.0–1.0."""
    if client is None:
        from .oauth_client import make_client
        client = make_client()
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=10,
        messages=[{"role": "user", "content": PROTO_RUBRIC_PROMPT.format(yu_turn=yu_turn, ai_turn=ai_turn)}],
    )
    text = msg.content[0].text.strip()
    try:
        score = float(text)
        return max(0.0, min(1.0, score))
    except ValueError:
        return 0.0


def estimate_yield(sample_scores: list[float], total_pool: int, threshold: float = 0.70) -> dict:
    n_accept = sum(1 for s in sample_scores if s >= threshold)
    accept_rate = n_accept / len(sample_scores) if sample_scores else 0.0
    projected = int(round(accept_rate * total_pool))
    histogram = {
        "0.0-0.2": sum(1 for s in sample_scores if s < 0.2),
        "0.2-0.4": sum(1 for s in sample_scores if 0.2 <= s < 0.4),
        "0.4-0.6": sum(1 for s in sample_scores if 0.4 <= s < 0.6),
        "0.6-0.8": sum(1 for s in sample_scores if 0.6 <= s < 0.8),
        "0.8-1.0": sum(1 for s in sample_scores if s >= 0.8),
    }
    return {
        "sample_n": len(sample_scores),
        "pool_n": total_pool,
        "threshold": threshold,
        "accept_rate": accept_rate,
        "projected_accepted": projected,
        "histogram": histogram,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DATA_DIR / "raw_pool.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "audit_report.json"))
    ap.add_argument("--sample-n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pairs = [json.loads(line) for line in Path(args.in_path).read_text().splitlines() if line.strip()]
    total = len(pairs)
    random.seed(args.seed)
    sample = random.sample(pairs, k=min(args.sample_n, total))

    from .oauth_client import make_client
    client = make_client()
    scores = []
    for i, p in enumerate(sample):
        s = score_pair_proto(p["yu_turn"], p["ai_turn"], client=client)
        scores.append(s)
        if (i + 1) % 10 == 0:
            print(f"scored {i+1}/{len(sample)}")
    report = estimate_yield(scores, total_pool=total)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    rec = "canon-heavy split (200/200/500)" if report["projected_accepted"] < 300 else "standard split (120/500/280)"
    print(f"\nRecommendation: {rec}")


if __name__ == "__main__":
    main()
