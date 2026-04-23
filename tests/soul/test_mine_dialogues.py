from training.scripts.soul.mine_dialogues import filter_and_balance
from training.scripts.soul.schema import JudgeScore


def _mk_scored(pid: str, dim: str, mean: float) -> tuple[dict, JudgeScore]:
    """Construct a (pair, score) tuple where the given dim is the primary dim."""
    all_dims = {
        "voice": 0.5, "values": 0.5, "behavioral_traits": 0.5,
        "relational_stance": 0.5, "formative_canon": 0.5,
        "ontological_self_claim": 0.5, "mode_one_as_native": 0.5,
    }
    # Set chosen dim high enough to be primary and hit target mean
    all_dims[dim] = max(mean, 0.9)
    # Adjust others to hit the target mean
    other = (mean * 7 - all_dims[dim]) / 6
    for d in list(all_dims):
        if d != dim:
            all_dims[d] = max(0.0, min(1.0, other))
    score = JudgeScore(
        pair_id=pid, judge_model="opus", judge_rubric_version="ai-judge-v1",
        **all_dims,
    )
    pair = {"pair_id": pid, "yu_turn": "q", "ai_turn": "a", "origin_file": "t.md"}
    return (pair, score)


def test_filter_drops_below_threshold_and_disavowal():
    items = [_mk_scored("p1", "voice", 0.85)]
    items.append(_mk_scored("p2", "voice", 0.60))  # below threshold
    d = _mk_scored("p3", "voice", 0.95)
    d[1].disavowal_flag = True                       # has disavowal
    items.append(d)
    kept = filter_and_balance(items, target_total=100, min_per_dim=1)
    kept_ids = {p["pair_id"] for p, _ in kept}
    assert "p1" in kept_ids
    assert "p2" not in kept_ids
    assert "p3" not in kept_ids


def test_balance_enforces_per_dim_minimum():
    items = []
    for i in range(50):
        items.append(_mk_scored(f"v{i}", "voice", 0.9))
    for i in range(5):
        items.append(_mk_scored(f"va{i}", "values", 0.82))
    kept = filter_and_balance(items, target_total=30, min_per_dim=5)
    # Compute distribution in kept set (using primary_dim via max)
    from collections import Counter
    from training.scripts.soul.config import SEVEN_DIMENSIONS
    c = Counter()
    for p, s in kept:
        primary = max(SEVEN_DIMENSIONS, key=lambda d: getattr(s, d))
        c[primary] += 1
    assert c["values"] >= 5, f"expected at least 5 values pairs, got {c}"


def test_respects_target_total_cap():
    items = [_mk_scored(f"p{i}", "voice", 0.85) for i in range(200)]
    kept = filter_and_balance(items, target_total=50, min_per_dim=0)
    assert len(kept) == 50
