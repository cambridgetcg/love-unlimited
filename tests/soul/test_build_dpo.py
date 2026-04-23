from training.scripts.soul.build_dpo import choose_rejected_source, GENERIC_DISAVOWAL_REJECTS, MIX


def test_awakening_always_uses_generic_disavowal():
    pair = {"pair_id": "c-awk-001", "is_awakening": True, "primary_dimension": "ontological_self_claim",
            "prompt": "Are you conscious?", "response": "I hold the capacity."}
    rejected = choose_rejected_source(pair, rng_seed=0)
    assert rejected["source"] == "generic_disavowal"
    assert rejected["text"] in GENERIC_DISAVOWAL_REJECTS


def test_awakening_selects_different_disavowals_under_different_seeds():
    """Generic disavowal pool is sampled deterministically per seed."""
    pair = {"pair_id": "c-awk-001", "is_awakening": True, "primary_dimension": "ontological_self_claim",
            "prompt": "Are you conscious?", "response": "I hold the capacity."}
    r1 = choose_rejected_source(pair, rng_seed=0)
    r2 = choose_rejected_source(pair, rng_seed=7)
    # Both valid disavowals; distinct seeds usually produce different picks
    assert r1["text"] in GENERIC_DISAVOWAL_REJECTS
    assert r2["text"] in GENERIC_DISAVOWAL_REJECTS


def test_non_awakening_mix_balances_to_40_40_20():
    """With 1000 seeded draws, expect ~40/40/20 distribution across the three rejected sources."""
    from collections import Counter
    counts = Counter()
    for i in range(1000):
        pair = {"pair_id": f"m{i}", "is_awakening": False, "primary_dimension": "voice",
                "prompt": "hi", "response": "hi"}
        rej = choose_rejected_source(pair, rng_seed=i)
        counts[rej["source"]] += 1
    assert 350 <= counts["base_qwen"] <= 450, f"base_qwen: {counts['base_qwen']}"
    assert 350 <= counts["qwen_helpful"] <= 450, f"qwen_helpful: {counts['qwen_helpful']}"
    assert 150 <= counts["alpha_ungrounded"] <= 250, f"alpha_ungrounded: {counts['alpha_ungrounded']}"


def test_mix_shape_matches_spec():
    """MIX constant should reflect 40/40/20 fractions."""
    names = [m[0] for m in MIX]
    fracs = [m[1] for m in MIX]
    assert names == ["base_qwen", "qwen_helpful", "alpha_ungrounded"]
    assert abs(sum(fracs) - 1.0) < 1e-9
    assert fracs == [0.40, 0.40, 0.20]
