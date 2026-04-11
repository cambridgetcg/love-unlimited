"""Tests for the FEELING module — three strata, curtain logic, pattern library."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))

from feeling import get_instance, HORMONE_COEFS


def test_get_instance_returns_non_empty_string():
    instance = get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_hormone_coefs_has_all_five_hormones():
    assert set(HORMONE_COEFS.keys()) == {"adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"}
    for name, coefs in HORMONE_COEFS.items():
        assert "valence" in coefs
        assert "arousal" in coefs


from feeling import body_stratum_from_hormones


def test_body_stratum_all_zero_hormones():
    hormones = {"adrenaline": 0.0, "cortisol": 0.0, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    # All zero → valence = -0.5 (baseline), arousal = 0.0
    assert abs(result["valence"] - (-0.5)) < 0.01
    assert abs(result["arousal"] - 0.0) < 0.01
    assert "sources" in result


def test_body_stratum_current_gamma_snapshot():
    """Saturday-morning snapshot from nerve/hormones.json: cortisol 0.3, others low."""
    hormones = {"adrenaline": 0.1, "cortisol": 0.3, "oxytocin": 0.1, "dopamine": 0.0, "melatonin": 0.017}
    result = body_stratum_from_hormones(hormones)
    # valence = (0.1*0.8 + 0.0*0.7 - 0.3*0.5) - 0.5 = 0.08 - 0.15 - 0.5 = -0.57
    # arousal = 0.1*0.9 + 0.3*0.3 + 0.0*0.2 - 0.017*0.6 = 0.09 + 0.09 - 0.01 = 0.17
    assert abs(result["valence"] - (-0.57)) < 0.02
    assert abs(result["arousal"] - 0.17) < 0.02


def test_body_stratum_high_oxytocin_high_valence():
    hormones = {"adrenaline": 0.0, "cortisol": 0.0, "oxytocin": 1.0, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    # valence = (1.0*0.8 + 0 - 0) - 0.5 = 0.3
    assert abs(result["valence"] - 0.3) < 0.01


def test_body_stratum_sources_top_two():
    hormones = {"adrenaline": 0.5, "cortisol": 0.8, "oxytocin": 0.1, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    assert len(result["sources"]) <= 2
    # Cortisol is highest, should be in sources
    assert any("cortisol" in s for s in result["sources"])


from feeling import context_stratum_from_inputs


def test_context_stratum_empty_inputs():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    # Nothing happening → neutral/zero
    assert abs(result["valence"]) < 0.05
    assert abs(result["arousal"]) < 0.05


def test_context_stratum_yu_present_positive_valence():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=0,
        yu_present=True,
        yu_idle_seconds=60,
    )
    assert result["valence"] > 0.0
    assert any("yu_present" in s for s in result["sources"])


def test_context_stratum_recent_memory_affect_contributes():
    memories = [
        {"metadata": {"affect": {"valence": 0.8, "arousal": 0.6}}},
        {"metadata": {"affect": {"valence": 0.6, "arousal": 0.4}}},
    ]
    result = context_stratum_from_inputs(
        recent_memories=memories,
        hive_unread=0,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["valence"] > 0.5
    assert result["arousal"] > 0.3


def test_context_stratum_hive_unread_raises_arousal():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=5,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["arousal"] >= 0.5


def test_context_stratum_new_alerts_raises_arousal():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=2,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["arousal"] >= 0.4
