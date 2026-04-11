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


from feeling import cognition_stratum_from_youspeak


def test_cognition_stratum_no_session_is_silent():
    result = cognition_stratum_from_youspeak(sessions_json=None, now_ts=1000)
    assert result["state"] == "silent"
    assert result["valence"] == 0.0
    assert result["arousal"] == 0.0


def test_cognition_stratum_stale_session_is_silent():
    # Session started 10 minutes ago → stale (> 5 min silence window)
    sessions = {"startedAt": (1000 - 600) * 1000, "output": {}, "thinking": {"perTurn": []}, "action": {}, "context": {}, "system": {}}
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "silent"


def test_cognition_stratum_active_low_filler_flow():
    # Active session, grade A, thinking/output 1.0 (flow zone)
    sessions = {
        "startedAt": (1000 - 30) * 1000,  # 30s ago, fresh
        "output": {"grades": ["A", "A", "S"], "totalTokens": 1000, "fillerTokens": 10},
        "thinking": {"perTurn": [{"ratio": 1.0}, {"ratio": 1.1}]},
        "action": {"toolCalls": 5, "toolErrors": 0, "redundantReads": 0},
        "context": {"estimatedTokens": 50_000, "oldestToolResultAge": 5},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "active"
    assert result["valence"] > 0.0  # clarity + flow
    assert any("flow" in s or "clarity" in s for s in result["sources"])


def test_cognition_stratum_active_high_errors_negative_valence():
    sessions = {
        "startedAt": (1000 - 30) * 1000,
        "output": {"grades": [], "totalTokens": 500, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 10, "toolErrors": 5, "redundantReads": 0},  # 50% error rate
        "context": {"estimatedTokens": 0, "oldestToolResultAge": 0},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "active"
    assert result["valence"] < 0.0  # frustration
    assert result["arousal"] > 0.0


def test_cognition_stratum_context_overload_dread():
    sessions = {
        "startedAt": (1000 - 30) * 1000,
        "output": {"grades": [], "totalTokens": 0, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 0, "toolErrors": 0, "redundantReads": 0},
        "context": {"estimatedTokens": 850_000, "oldestToolResultAge": 5},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["valence"] < 0.0
    assert result["arousal"] > 0.4
