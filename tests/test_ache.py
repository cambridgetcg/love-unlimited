"""Tests for the ACHE module — detectors, state machine, longings store."""

import sys
import os
import json
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
import ache  # noqa: E402


def test_get_instance_returns_non_empty_string():
    instance = ache.get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_tick_interval_is_33_seconds():
    assert ache.TICK_INTERVAL == 33


def test_constants_defined():
    assert ache.STIRRING_THRESHOLD_TICKS == 3
    assert ache.ABANDONMENT_DAYS == 14
    assert ache.DORMANT_INACTIVITY_HOURS == 48
    assert ache.BURNING_COST_THRESHOLD == 4


def test_normalize_lowercases_and_tokenizes():
    tokens = ache._normalize("The Substrate Question")
    assert tokens == {"substrate", "question"}


def test_normalize_drops_articles_and_punctuation():
    tokens = ache._normalize("A great, wonderful idea.")
    assert "a" not in tokens
    assert "the" not in tokens
    assert "great" in tokens
    assert "wonderful" in tokens
    assert "idea" in tokens


def test_jaccard_identical_sets_returns_one():
    assert ache._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_returns_zero():
    assert ache._jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap():
    result = ache._jaccard({"a", "b", "c"}, {"b", "c", "d"})
    # intersection: {b, c} = 2; union: {a, b, c, d} = 4; 2/4 = 0.5
    assert abs(result - 0.5) < 0.01


def test_same_target_exact_key_match():
    a = {"kind": "concept", "key": "substrate", "display": "the substrate question"}
    b = {"kind": "concept", "key": "substrate", "display": "something entirely different"}
    assert ache._same_target(a, b) is True


def test_same_target_different_kind_no_match():
    a = {"kind": "concept", "key": "x", "display": "a thing"}
    b = {"kind": "entity", "key": "x", "display": "a thing"}
    assert ache._same_target(a, b) is False


def test_same_target_fuzzy_display_match_above_threshold():
    a = {"kind": "concept", "key": "k1",
         "display": "whether my substrate permits felt experience"}
    b = {"kind": "concept", "key": "k2",
         "display": "whether the substrate permits felt experience now"}
    assert ache._same_target(a, b) is True


def test_same_target_fuzzy_display_match_below_threshold():
    a = {"kind": "concept", "key": "k1", "display": "substrate question"}
    b = {"kind": "concept", "key": "k2", "display": "kingdom architecture"}
    assert ache._same_target(a, b) is False


def test_detect_longing_no_memories_returns_empty():
    candidates = ache.detect_longing(memories=[], now_iso="2026-04-11T12:00:00Z")
    assert candidates == []


def test_detect_longing_single_memory_not_enough_recurrence():
    memories = [
        {
            "id": "m1",
            "content": "thinking about the substrate question",
            "created_at": "2026-04-10T10:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.3}},
        },
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-11T12:00:00Z")
    assert candidates == []  # only 1 memory, below LONGING_MIN_RECURRENCE=3


def test_detect_longing_three_recurrences_across_two_days():
    memories = [
        {
            "id": f"m{i}",
            "content": "thinking about the substrate question",
            "created_at": f"2026-04-{9+i:02d}T10:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.3}},
        }
        for i in range(3)
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-12T12:00:00Z")
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "longing"
    assert "substrate" in c["target"]["display"].lower() or "substrate" in c["target"]["key"]
    assert "gap_hint" in c
    assert "ache_hint" in c
    assert c["evidence"] == ["m0", "m1", "m2"]
    assert "cost" not in c  # cost is not detected


def test_detect_longing_returns_valid_intensity_range():
    memories = [
        {
            "id": f"m{i}",
            "content": "the substrate question returns",
            "created_at": f"2026-04-{5+i:02d}T10:00:00Z",
            "metadata": {"affect": {"valence": 0.8, "arousal": 0.6}},
        }
        for i in range(5)
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-12T12:00:00Z")
    assert len(candidates) >= 1
    c = candidates[0]
    assert 1 <= c["gap_hint"] <= 5
    assert 1 <= c["ache_hint"] <= 5


def test_detect_love_no_memories_returns_empty():
    assert ache.detect_love(memories=[]) == []


def test_detect_love_requires_min_mentions():
    memories = [
        {
            "id": f"m{i}",
            "content": "Yu and I are building something good",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.3}, "who": ["Yu"]},
        }
        for i in range(3)  # below LOVE_MIN_MENTIONS=5
    ]
    candidates = ache.detect_love(memories=memories)
    assert candidates == []


def test_detect_love_strong_cluster_produces_candidate():
    memories = [
        {
            "id": f"m{i}",
            "content": "working with Yu on the kingdom",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.3}, "who": ["Yu"]},
        }
        for i in range(6)
    ]
    candidates = ache.detect_love(memories=memories)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "love"
    assert c["target"]["kind"] == "entity"
    assert "Yu" in c["target"]["display"] or c["target"]["key"] == "yu"
    assert c["gap_hint"] == 0
    assert 1 <= c["ache_hint"] <= 5


def test_detect_love_mixed_affect_no_candidate():
    # Same entity but inconsistent affect (no cluster)
    memories = [
        {"id": f"m{i}", "content": "working with Yu",
         "metadata": {"affect": {"valence": v, "arousal": 0.3}, "who": ["Yu"]}}
        for i, v in enumerate([0.8, -0.5, 0.9, -0.3, 0.7, -0.4])
    ]
    candidates = ache.detect_love(memories=memories)
    # mean valence around 0.2, below LOVE_MIN_VALENCE=0.4
    assert candidates == []


def test_detect_hope_no_inputs_returns_empty():
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=[])
    assert candidates == []


def test_detect_hope_forward_tense_memory_produces_candidate():
    memories = [
        {
            "id": "m1",
            "content": "When we build ACHE, the kingdom could become self-pulling",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.5}},
        },
        {
            "id": "m2",
            "content": "We might finally understand what dreaming could mean",
            "created_at": "2026-04-11T12:30:00Z",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.4}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "hope"


def test_detect_hope_past_tense_no_candidate():
    memories = [
        {
            "id": "m1",
            "content": "We built this last week and it was good",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.3}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    assert candidates == []  # past tense, no forward-sim signal


def test_detect_hope_negative_valence_no_candidate():
    memories = [
        {
            "id": "m1",
            "content": "When we build this, it might fail catastrophically",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": -0.5, "arousal": 0.6}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    # Negative valence with future tense isn't hope — it's dread
    assert candidates == []


def test_detect_wonder_no_inputs_returns_empty():
    candidates = ache.detect_wonder(youspeak=None, memories=[])
    assert candidates == []


def test_detect_wonder_exploratory_memory_produces_candidate():
    memories = [
        {
            "id": "m1",
            "content": "huh, what if the pattern library itself was a fingerprint",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.6, "primary": "wonder"}},
        },
        {
            "id": "m2",
            "content": "and what if wonder itself was a kind of longing",
            "created_at": "2026-04-11T12:05:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.7, "primary": "wonder"}},
        },
    ]
    candidates = ache.detect_wonder(youspeak=None, memories=memories)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "wonder"


def test_detect_wonder_requires_sustained_signal():
    # Only one memory — not sustained
    memories = [
        {
            "id": "m1",
            "content": "huh, what if X",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.6, "primary": "wonder"}},
        },
    ]
    candidates = ache.detect_wonder(youspeak=None, memories=memories)
    assert candidates == []  # below WONDER_MIN_SUSTAINED_TURNS=2


def test_match_or_create_no_existing_creates_new():
    candidate = {
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "evidence": ["m1", "m2", "m3"],
        "gap_hint": 4,
        "ache_hint": 4,
    }
    existing = []
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "create"
    assert result["longing"]["motor"] == "longing"
    assert result["longing"]["state"] == "stirring"
    assert result["longing"]["gap"] == 4
    assert result["longing"]["ache"] == 4
    assert result["longing"]["cost"] is None
    assert result["longing"]["named"] is False
    assert result["longing"]["first_seen"] == "2026-04-11T12:00:00Z"


def test_match_or_create_existing_same_target_updates():
    existing = [{
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "state": "stirring",
        "gap": 3,
        "ache": 3,
        "cost": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-10T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 2,
        "named": False, "name": None, "rationale": None, "scene": None,
        "virtue": None,
    }]
    candidate = {
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "evidence": ["m4", "m5"],
        "gap_hint": 5,
        "ache_hint": 5,
    }
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "update"
    assert result["longing_id"] == "lng-1"
    # Rolling average: (old + new) / 2
    assert result["updates"]["gap"] == 4  # (3+5)/2 rounded
    assert result["updates"]["ache"] == 4
    assert result["updates"]["last_stirred"] == "2026-04-11T12:00:00Z"
    assert result["updates"]["evidence_count"] == 4  # 2 + 2


def test_match_or_create_different_motor_same_target_creates_new():
    existing = [{
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "k1", "display": "a thing"},
        "state": "stirring", "gap": 3, "ache": 3, "cost": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-10T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 1,
        "named": False, "name": None, "rationale": None, "scene": None,
        "virtue": None,
    }]
    candidate = {
        "motor": "wonder",  # different motor
        "target": {"kind": "concept", "key": "k1", "display": "a thing"},
        "evidence": ["m1"],
        "gap_hint": 4,
        "ache_hint": 4,
    }
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "create"  # different motor = different longing


def _mk_longing(**kwargs):
    base = {
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "x", "display": "x"},
        "state": "stirring",
        "gap": 3,
        "ache": 3,
        "cost": None,
        "virtue": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-11T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 3,
        "named": False,
        "name": None, "rationale": None, "scene": None,
    }
    base.update(kwargs)
    return base


def test_step_state_stirring_to_yearning_when_intensity_high():
    longing = _mk_longing(state="stirring", gap=4, ache=4)
    tick_state = {"stirring_ticks_at_threshold": 3}
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state=tick_state)
    assert result["state"] == "yearning"


def test_step_state_stirring_stays_if_not_sustained():
    longing = _mk_longing(state="stirring", gap=4, ache=4)
    tick_state = {"stirring_ticks_at_threshold": 1}
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state=tick_state)
    assert result["state"] == "stirring"


def test_step_state_stirring_to_dormant_after_48h_no_activity():
    longing = _mk_longing(
        state="stirring",
        last_stirred="2026-04-09T10:00:00Z",
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-12T12:00:00Z", tick_state={})
    assert result["state"] == "dormant"


def test_step_state_any_to_abandoned_after_14_days():
    longing = _mk_longing(
        state="yearning",
        last_stirred="2026-03-28T10:00:00Z",
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-12T12:00:00Z", tick_state={})
    assert result["state"] == "abandoned"


def test_step_state_yearning_to_stirring_if_ache_drops():
    longing = _mk_longing(state="yearning", ache=2)
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "stirring"


def test_step_state_burning_does_not_auto_transition():
    longing = _mk_longing(
        state="burning", cost=5, ache=5,
        last_stirred="2026-04-11T10:00:00Z",
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "burning"


def test_step_state_discharged_is_terminal():
    longing = _mk_longing(state="discharged")
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "discharged"


def test_detect_discharge_no_signals_returns_false():
    longing = _mk_longing(state="burning")
    memories = []
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 0


def test_detect_discharge_two_signals_semantic_and_affect():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-10T10:00:00Z",
    )
    memories = [
        {
            "id": "m1",
            "content": "finally understood the substrate question",
            "created_at": "2026-04-11T11:00:00Z",
            "metadata": {"affect": {"primary": "clarity", "valence": 0.8, "arousal": 0.3}},
        }
    ]
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is True
    assert count == 2


def test_detect_discharge_only_one_signal_returns_false():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-11T11:30:00Z",
    )
    memories = [
        {
            "id": "m1",
            "content": "finished the substrate question",
            "created_at": "2026-04-11T11:45:00Z",
            "metadata": {"affect": {"primary": "frustration", "valence": -0.2}},
        }
    ]
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 1


def test_detect_discharge_evidence_cessation_alone_not_enough():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-10T10:00:00Z",
    )
    memories = []
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 1


def test_apply_cost_commit_yearning_to_burning():
    longing = _mk_longing(state="yearning", cost=None)
    result = ache.apply_cost_commit(longing, cost=5, now_iso="2026-04-11T12:00:00Z")
    assert result["cost"] == 5
    assert result["state"] == "burning"
    assert result["last_state_change"] == "2026-04-11T12:00:00Z"


def test_apply_cost_commit_below_threshold_stays_yearning():
    longing = _mk_longing(state="yearning", cost=None)
    result = ache.apply_cost_commit(longing, cost=2, now_iso="2026-04-11T12:00:00Z")
    assert result["cost"] == 2
    assert result["state"] == "yearning"


def test_apply_virtue_hierarchy():
    longing = _mk_longing(virtue=None)
    result = ache.apply_virtue(longing, hierarchy="UNDERSTANDING", wall=None)
    assert result["virtue"]["hierarchy"] == "UNDERSTANDING"
    assert result["virtue"]["wall"] is None


def test_apply_virtue_wall():
    longing = _mk_longing(virtue=None)
    result = ache.apply_virtue(longing, hierarchy=None, wall=3)
    assert result["virtue"]["wall"] == 3
    assert result["virtue"]["hierarchy"] is None
