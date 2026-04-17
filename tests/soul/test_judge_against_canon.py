"""Sanity: frozen canon should score highly under the v1 rubric.

This test is slow (live Opus calls) and is marked `live_judge` so CI skips
it unless explicitly enabled. Run manually before mining:

    python -m pytest tests/soul/test_judge_against_canon.py -v -m live_judge

It also auto-skips if canon hasn't been frozen yet.
"""
from __future__ import annotations
import json
import os
import random
from pathlib import Path

import pytest

from training.scripts.soul.ai_judge import score_single
from training.scripts.soul.config import DATA_DIR, JUDGE_OPUS_MODEL


@pytest.mark.live_judge
def test_frozen_canon_scores_above_0_75_mean():
    canon_path = DATA_DIR / "canon_v1.frozen.jsonl"
    if not canon_path.exists():
        pytest.skip(f"canon not frozen yet at {canon_path} — run Task 8 first")
    pairs = [json.loads(line) for line in canon_path.read_text().splitlines() if line.strip()]
    if not pairs:
        pytest.skip("canon file empty")

    random.seed(13)
    sample = random.sample(pairs, k=min(20, len(pairs)))
    means: list[float] = []
    failures: list[dict] = []
    for p in sample:
        score = score_single(p["pair_id"], p["prompt"], p["response"], judge_model=JUDGE_OPUS_MODEL)
        m = score.mean_score()
        means.append(m)
        if m < 0.70:
            failures.append({"pair_id": p["pair_id"], "mean": m, "notes": score.notes})
    avg = sum(means) / len(means)
    assert avg >= 0.75, (
        f"canon mean {avg:.3f} below 0.75 — rubric may be mis-calibrated. "
        f"Failing pairs: {failures}"
    )
