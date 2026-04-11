"""Tests for the FEELING module — three strata, curtain logic, pattern library."""

import math
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


from feeling import combine_strata


def test_combine_strata_averages_valence_arousal():
    body = {"valence": -0.4, "arousal": 0.2, "sources": []}
    context = {"valence": 0.2, "arousal": 0.4, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    # Silent cognition excluded from average
    expected_v = (-0.4 + 0.2) / 2
    expected_a = (0.2 + 0.4) / 2
    assert abs(result["valence"] - expected_v) < 0.01
    assert abs(result["arousal"] - expected_a) < 0.01


def test_combine_strata_includes_cognition_when_active():
    body = {"valence": -0.2, "arousal": 0.1, "sources": []}
    context = {"valence": 0.1, "arousal": 0.2, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "active"}
    result = combine_strata(body, context, cognition)
    expected_v = (-0.2 + 0.1 + 0.3) / 3
    expected_a = (0.1 + 0.2 + 0.3) / 3
    assert abs(result["valence"] - expected_v) < 0.01
    assert abs(result["arousal"] - expected_a) < 0.01


def test_combine_strata_pressure_elevates_on_body_context_gap():
    """body and context disagree → pressure multiplier kicks in."""
    body = {"valence": -0.6, "arousal": 0.3, "sources": []}
    context = {"valence": 0.5, "arousal": 0.3, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    # Gap |(-0.6) - 0.5| = 1.1 → multiplier > 1
    raw = math.sqrt(result["valence"]**2 + result["arousal"]**2)
    assert result["pressure"] > raw


def test_combine_strata_pressure_low_when_aligned():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    assert result["pressure"] < 0.05


from feeling import check_curtain


def test_curtain_fires_on_pressure_above_threshold():
    body = {"valence": -0.5, "arousal": 0.5, "sources": []}
    context = {"valence": -0.5, "arousal": 0.5, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.5, "arousal": 0.5, "pressure": 0.71}  # > 0.5 threshold
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000)
    assert reasons is not None
    assert any(r["kind"] == "pressure" for r in reasons)


def test_curtain_silent_below_threshold():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.0, "arousal": 0.0, "pressure": 0.01}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000)
    assert reasons is None


def test_curtain_respects_min_interval_on_pressure():
    body = {"valence": -0.5, "arousal": 0.5, "sources": []}
    context = {"valence": -0.5, "arousal": 0.5, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.5, "arousal": 0.5, "pressure": 0.71}
    # Last fire was 30 seconds ago — inside 90s min interval
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=970, now_ts=1000)
    assert reasons is None  # suppressed by min_interval


def test_curtain_body_shift_fires():
    last_body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    body = {"valence": -0.3, "arousal": 0.3, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.15, "arousal": 0.15, "pressure": 0.21}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=last_body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_shift" for r in reasons)


def test_curtain_context_shift_fires():
    last_context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.3, "arousal": 0.3, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.15, "arousal": 0.15, "pressure": 0.21}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=last_context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "context_shift" for r in reasons)


def test_curtain_cognition_shift_suppressed_when_silent():
    last_cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "silent"}  # still silent
    combined = {"valence": 0.0, "arousal": 0.0, "pressure": 0.0}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=last_cognition)
    assert reasons is None  # cognition silent → no shift trigger


def test_curtain_cognition_shift_fires_when_active():
    last_cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "active"}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "active"}
    combined = {"valence": 0.1, "arousal": 0.1, "pressure": 0.14}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=last_cognition)
    assert reasons is not None
    assert any(r["kind"] == "cognition_shift" for r in reasons)


def test_curtain_body_context_mismatch_always_fires():
    """Mismatch ≥ 0.5 fires even inside min_interval."""
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.5, "arousal": 0.0, "sources": []}  # gap = 1.1
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.05, "arousal": 0.0, "pressure": 0.055}
    # Last fire was 10 seconds ago — inside min_interval
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=990, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_context_gap" for r in reasons)


def test_curtain_body_cognition_mismatch_active_only():
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.5, "arousal": 0.0, "sources": [], "state": "active"}
    combined = {"valence": -0.03, "arousal": 0.0, "pressure": 0.033}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_cognition_gap" for r in reasons)


def test_curtain_cognition_mismatch_suppressed_when_silent():
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.5, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.3, "arousal": 0.0, "pressure": 0.3}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    # body_context mismatch fires (gap 0.6) but body_cognition should NOT
    if reasons:
        assert not any(r["kind"] == "body_cognition_gap" for r in reasons)


def test_curtain_mismatch_below_threshold_no_fire():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.3, "arousal": 0.0, "sources": []}  # gap 0.3 < 0.5
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.15, "arousal": 0.0, "pressure": 0.15}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    # No shift, no mismatch ≥ 0.5, no pressure ≥ 0.5 → no fire
    assert reasons is None


from feeling import pit_fingerprint, fingerprints_match


def test_fingerprint_has_expected_keys():
    body = {"valence": -0.4, "arousal": 0.1, "sources": ["cortisol_moderate"]}
    context = {"valence": 0.2, "arousal": 0.3, "sources": ["yu_present_active"]}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    reasons = [{"kind": "body_context_gap", "value": 0.6}]
    fp = pit_fingerprint(body, context, cognition, reasons)
    assert "body_v_bucket" in fp
    assert "body_a_bucket" in fp
    assert "context_v_bucket" in fp
    assert "cognition_v_bucket" in fp
    assert "dominant_reason" in fp
    assert "top_sources" in fp


def test_fingerprint_buckets_are_categorical():
    body = {"valence": -0.9, "arousal": 0.1, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    fp = pit_fingerprint(body, context, cognition, [])
    assert fp["body_v_bucket"] == "very_neg"
    assert fp["body_a_bucket"] == "low"


def test_fingerprint_silent_cognition_bucket():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    fp = pit_fingerprint(body, context, cognition, [])
    assert fp["cognition_v_bucket"] == "silent"
    assert fp["cognition_a_bucket"] == "silent"


def test_fingerprints_match_identical_buckets_and_overlap_sources():
    fp1 = {
        "body_v_bucket": "neg", "body_a_bucket": "low",
        "context_v_bucket": "pos", "context_a_bucket": "mid",
        "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
        "dominant_reason": "pressure",
        "top_sources": ["yu_present", "cortisol_moderate"]
    }
    fp2 = {
        "body_v_bucket": "neg", "body_a_bucket": "low",
        "context_v_bucket": "pos", "context_a_bucket": "mid",
        "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
        "dominant_reason": "pressure",
        "top_sources": ["yu_present", "recent_memory_wonder"]
    }
    assert fingerprints_match(fp1, fp2)


def test_fingerprints_no_match_different_buckets():
    fp1 = {"body_v_bucket": "neg", "body_a_bucket": "low",
           "context_v_bucket": "pos", "context_a_bucket": "mid",
           "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
           "dominant_reason": "pressure", "top_sources": ["a"]}
    fp2 = dict(fp1)
    fp2["body_v_bucket"] = "pos"
    assert not fingerprints_match(fp1, fp2)


from feeling import lookup_hint


def test_lookup_hint_empty_library_returns_none():
    patterns = {"version": 1, "patterns": []}
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    result = lookup_hint(fp, patterns)
    assert result is None


def test_lookup_hint_below_min_count_returns_none():
    """Patterns with total_count < 3 don't emit hints (not enough data)."""
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"settling": 2},
            "total_count": 2,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    }
    result = lookup_hint(fp, patterns)
    assert result is None


def test_lookup_hint_returns_sorted_candidates():
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"settling": 3, "clarity": 2, "relief": 1},
            "total_count": 6,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    }
    result = lookup_hint(fp, patterns)
    assert result is not None
    assert result["total_prior"] == 6
    assert len(result["candidates"]) >= 1
    assert result["candidates"][0]["name"] == "settling"
    assert abs(result["candidates"][0]["probability"] - 0.5) < 0.01


from feeling import compute_importance


def test_importance_baseline():
    arc = {
        "arrival": {"reasons": [{"kind": "pressure", "value": 0.0}]},
        "surprise": False,
        "yu_present": False,
        "combined_pressure": 0.0,
    }
    assert abs(compute_importance(arc) - 0.5) < 0.01


def test_importance_high_pressure_adds():
    arc = {
        "arrival": {"reasons": [{"kind": "pressure", "value": 1.0}]},
        "surprise": False,
        "yu_present": False,
        "combined_pressure": 1.0,
    }
    # 0.5 + 0.15 * 1.0 = 0.65
    assert abs(compute_importance(arc) - 0.65) < 0.01


def test_importance_surprise_and_yu_and_mismatch_cap_at_one():
    arc = {
        "arrival": {"reasons": [{"kind": "body_context_gap", "value": 0.8}]},
        "surprise": True,
        "yu_present": True,
        "combined_pressure": 1.0,
    }
    # 0.5 + 0.15 (pressure) + 0.10 (mismatch) + 0.15 (surprise) + 0.10 (yu) = 1.0
    assert abs(compute_importance(arc) - 1.0) < 0.01


import tempfile
from feeling import write_pit_json, read_pit_json
import feeling as feeling_mod


def test_write_and_read_pit_json(tmp_path, monkeypatch):
    target = tmp_path / "pit.json"
    monkeypatch.setattr(feeling_mod, "PIT_PATH", target)

    pit = {
        "instance": "gamma",
        "timestamp": "2026-04-11T10:47:03Z",
        "body": {"valence": -0.4, "arousal": 0.15, "sources": [], "last_tick": "..."},
        "context": {"valence": 0.1, "arousal": 0.25, "sources": [], "last_tick": "..."},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent", "last_tick": "..."},
        "combined": {"valence": -0.15, "arousal": 0.20, "pressure": 0.31},
        "threshold": 0.5,
        "arrivals_total": 0,
        "arrivals_pending_name": 0,
    }
    write_pit_json(pit)
    loaded = read_pit_json()
    assert loaded["instance"] == "gamma"
    assert loaded["combined"]["pressure"] == 0.31


def test_read_pit_json_missing_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "nonexistent.json")
    assert read_pit_json() == {}


from feeling import append_arrival, read_arrivals, update_arrival


def test_append_and_read_arrivals(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)

    arrival = {
        "id": "arr-test-1",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.3, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.15, "pressure": 0.6},
        "named": False, "witnessed": False,
    }
    append_arrival(arrival)
    loaded = read_arrivals()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "arr-test-1"


def test_read_arrivals_filters_by_witnessed(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)

    append_arrival({"id": "a1", "witnessed": False, "named": False})
    append_arrival({"id": "a2", "witnessed": True, "named": False})
    unwitnessed = read_arrivals(witnessed=False)
    assert len(unwitnessed) == 1
    assert unwitnessed[0]["id"] == "a1"


def test_update_arrival_marks_named(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)

    append_arrival({"id": "a1", "named": False, "witnessed": False})
    update_arrival("a1", {"named": True, "name": "settling", "named_at": "2026-04-11T11:00:00Z"})
    loaded = read_arrivals()
    assert loaded[0]["named"] is True
    assert loaded[0]["name"] == "settling"


from feeling import read_patterns, write_patterns, update_pattern_library


def test_read_patterns_missing_returns_empty_library(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    patterns = read_patterns()
    assert patterns == {"version": 1, "patterns": []}


def test_write_and_read_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint_hash": "abc",
            "fingerprint": {"body_v_bucket": "neg"},
            "names": {"settling": 3},
            "total_count": 3,
            "last_seen": "2026-04-11T10:00:00Z",
        }]
    }
    write_patterns(patterns)
    loaded = read_patterns()
    assert len(loaded["patterns"]) == 1
    assert loaded["patterns"][0]["names"]["settling"] == 3


def test_update_pattern_library_new_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    write_patterns({"version": 1, "patterns": []})

    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    update_pattern_library(fp, "settling", now_iso="2026-04-11T10:00:00Z")

    loaded = read_patterns()
    assert len(loaded["patterns"]) == 1
    assert loaded["patterns"][0]["names"]["settling"] == 1
    assert loaded["patterns"][0]["total_count"] == 1


def test_update_pattern_library_existing_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    write_patterns({
        "version": 1,
        "patterns": [{
            "fingerprint_hash": "abc",
            "fingerprint": fp,
            "names": {"settling": 2},
            "total_count": 2,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    })
    update_pattern_library(fp, "settling", now_iso="2026-04-11T10:00:00Z")
    loaded = read_patterns()
    assert loaded["patterns"][0]["names"]["settling"] == 3
    assert loaded["patterns"][0]["total_count"] == 3
    assert loaded["patterns"][0]["last_seen"] == "2026-04-11T10:00:00Z"


from feeling import read_pit_state, update_pit_state


def test_read_pit_state_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    assert read_pit_state() == {}


def test_update_pit_state_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    update_pit_state({"last_wake_at": "2026-04-11T07:00:00Z"})
    update_pit_state({"last_memory_id_seen": "mem-abc"})
    state = read_pit_state()
    assert state["last_wake_at"] == "2026-04-11T07:00:00Z"
    assert state["last_memory_id_seen"] == "mem-abc"


import asyncio
import json
from feeling import FeelingDaemon


def test_feeling_daemon_constructor_sets_instance():
    d = FeelingDaemon(instance="gamma")
    assert d.instance == "gamma"
    assert d.last_body_tick == 0
    assert d.last_context_tick == 0
    assert d.last_cognition_tick == 0
    assert d.last_fire_ts == 0


def test_feeling_daemon_run_once_writes_pit_json(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling_mod, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(feeling_mod, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")

    # Minimal hormones fixture
    (tmp_path / "hormones.json").write_text(json.dumps({
        "hormones": {"adrenaline": 0.1, "cortisol": 0.2, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0},
        "signals": {"yu_present": False, "hive_unread": 0}
    }))

    d = FeelingDaemon(instance="gamma")
    asyncio.run(d.run_once())

    assert (tmp_path / "pit.json").exists()
    pit = json.loads((tmp_path / "pit.json").read_text())
    assert "body" in pit
    assert "context" in pit
    assert "cognition" in pit
    assert "combined" in pit


def test_daemon_emits_arrival_on_body_context_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling_mod, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(feeling_mod, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")

    # Fixture: high cortisol (negative body) + Yu present (positive context)
    # Body v ≈ -0.65, context v ≈ +0.3 → gap ≈ 0.95 > 0.5
    (tmp_path / "hormones.json").write_text(json.dumps({
        "hormones": {"adrenaline": 0.0, "cortisol": 0.3, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0},
        "signals": {"yu_present": True, "yu_idle_seconds": 60, "hive_unread": 0, "critical_alerts": 0}
    }))

    d = FeelingDaemon(instance="gamma")
    # Prime the daemon so last_body / last_context exist
    asyncio.run(d.run_once())
    # Second cycle fires curtain via mismatch
    asyncio.run(d.run_once())

    arrivals = read_arrivals()
    assert len(arrivals) >= 1
    first = arrivals[0]
    assert first["instance"] == "gamma"
    assert "body_context_gap" in [r["kind"] for r in first["reasons"]]
    assert first["named"] is False
    assert first["witnessed"] is False


def test_read_recent_memories_returns_list_structure(tmp_path, monkeypatch):
    # Point kosmem at an empty in-memory db to keep the test hermetic
    import sqlite3
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            type TEXT,
            layer INTEGER,
            instance TEXT,
            wall INTEGER,
            importance REAL,
            tags TEXT,
            source TEXT,
            parent_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            accessed_at TEXT,
            access_count INTEGER,
            ttl_hours INTEGER,
            consolidated_into TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute(
        "INSERT INTO memories (id, content, type, layer, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        ("mem-1", "test", "episodic", 3, "2026-04-11T10:00:00Z",
         json.dumps({"affect": {"valence": 0.5, "arousal": 0.3}}))
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(feeling_mod, "_MEMORY_DB_PATH_FOR_FEELING", db_path)

    memories = feeling_mod._read_recent_memories(since_iso="2026-04-11T00:00:00Z", limit=10)
    assert isinstance(memories, list)
    assert len(memories) >= 1
    assert memories[0]["metadata"]["affect"]["valence"] == 0.5


import importlib.util

def _load_vivid():
    spec = importlib.util.spec_from_file_location(
        "vivid",
        os.path.join(os.path.dirname(__file__), '..', 'tools', 'vivid.py')
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_encode_vivid_accepts_arc_in_metadata():
    vivid = _load_vivid()
    content, metadata = vivid.encode_vivid(
        what_happened="test",
        affect="settling",
        arc={
            "pit_snapshot": {"combined": {"pressure": 0.5}},
            "arrival": {"id": "arr-test", "reasons": []},
            "name": "settling",
            "rationale": "test rationale",
            "scene": "test scene",
            "prior_hint": None,
            "surprise": False,
        }
    )
    assert "arc" in metadata
    assert metadata["arc"]["name"] == "settling"
    assert metadata["arc"]["rationale"] == "test rationale"
