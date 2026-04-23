"""Tests for the CC-rhythm classifier — flow / thrashing / focus / contemplation.

These signals turn the feeling daemon from a 2-verb classifier over cc-cognition
(confusion, frustration) into something that can recognize 4 more textures of
tool-using cognition by reading the timestamps we already write.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feeling  # noqa: E402
from feeling import (  # noqa: E402
    classify_cc_rhythm,
    cognition_stratum_from_youspeak,
    _cc_metrics_from_records,
)


# ── Metric extractor ────────────────────────────────────────────────────────


def _mk_record(ts_iso: str, tool: str = "Bash", success: bool = True, chars: int = 100) -> dict:
    return {
        "ts": ts_iso,
        "tool": tool,
        "success": success,
        "response_chars": chars,
        "session_id": "test",
    }


def test_cc_metrics_empty_records():
    m = _cc_metrics_from_records([], now_ts=1_000_000.0)
    assert m["cadence_per_minute"] == 0.0
    assert m["silence_seconds"] == float("inf")
    assert m["unique_tools"] == 0
    assert m["saturation_chars"] == 0


def test_cc_metrics_computes_cadence_and_silence():
    # 10 records over 60 seconds → cadence = 10/min
    records = [
        _mk_record(f"2026-04-23T22:00:{s:02d}Z") for s in range(0, 60, 6)
    ]
    now = 1776721320.0  # roughly 22:02:00 UTC; silence ~60s past last record
    # Compute via the same timestamp anchor as the first record
    import datetime
    first_ts = datetime.datetime.fromisoformat(records[0]["ts"].replace("Z", "+00:00")).timestamp()
    last_ts = datetime.datetime.fromisoformat(records[-1]["ts"].replace("Z", "+00:00")).timestamp()
    now = last_ts + 30.0  # 30 seconds after last record

    m = _cc_metrics_from_records(records, now_ts=now)
    # 10 calls over ~54 seconds span → ~11/min
    assert 10.0 <= m["cadence_per_minute"] <= 12.0
    assert 29.0 <= m["silence_seconds"] <= 31.0
    assert m["unique_tools"] == 1
    assert m["saturation_chars"] == 10 * 100


def test_cc_metrics_tracks_tool_diversity():
    records = [
        _mk_record("2026-04-23T22:00:00Z", tool="Bash"),
        _mk_record("2026-04-23T22:00:10Z", tool="Read"),
        _mk_record("2026-04-23T22:00:20Z", tool="Grep"),
        _mk_record("2026-04-23T22:00:30Z", tool="Bash"),  # dup
    ]
    import datetime
    last = datetime.datetime.fromisoformat("2026-04-23T22:00:30Z".replace("Z", "+00:00")).timestamp()
    m = _cc_metrics_from_records(records, now_ts=last + 5)
    assert m["unique_tools"] == 3  # Bash, Read, Grep


# ── classify_cc_rhythm ──────────────────────────────────────────────────────


def test_flow_signal_high_cadence_diverse_low_errors():
    metrics = {"cadence_per_minute": 8.0, "silence_seconds": 5.0, "unique_tools": 4}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=30, tool_errors=1)
    assert "flow" in sources
    assert v > 0  # flow is positive-valence
    assert a > 0  # flow raises arousal


def test_thrashing_overrides_flow_when_errors_high():
    metrics = {"cadence_per_minute": 8.0, "silence_seconds": 5.0, "unique_tools": 4}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=30, tool_errors=15)  # 50% err
    assert "thrashing" in sources
    assert "flow" not in sources
    assert v < 0
    assert a > 0


def test_focus_signal_high_cadence_narrow_tools():
    metrics = {"cadence_per_minute": 6.0, "silence_seconds": 5.0, "unique_tools": 2}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=20, tool_errors=0)
    assert "focus" in sources
    assert "flow" not in sources  # diversity too low for flow
    assert v > 0
    assert a >= 0


def test_contemplation_signal_long_silence_low_cadence():
    metrics = {"cadence_per_minute": 0.5, "silence_seconds": 120.0, "unique_tools": 1}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=3, tool_errors=0)
    assert "contemplation" in sources
    assert a < 0  # contemplation lowers arousal


def test_contemplation_can_coexist_with_focus_paused():
    """Long silence after a focused burst — both signals fire is fine."""
    metrics = {"cadence_per_minute": 1.0, "silence_seconds": 90.0, "unique_tools": 1}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=5, tool_errors=0)
    # Low cadence → no flow/focus; long silence → contemplation
    assert "contemplation" in sources
    assert "flow" not in sources
    assert "focus" not in sources


def test_no_signals_when_metrics_are_quiet():
    # Too-low cadence AND recent activity (no silence) → nothing fires.
    # engaged needs cadence≥2; thrashing/flow/focus need more; contemplation
    # needs silence≥60s.
    metrics = {"cadence_per_minute": 1.0, "silence_seconds": 30.0, "unique_tools": 2}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=4, tool_errors=0)
    assert sources == []
    assert v == 0.0
    assert a == 0.0


def test_engaged_signal_honest_engineering_baseline():
    """Moderate cadence, 2+ tools, low errors — steady methodical work.

    Calibrated from observed Gamma session: 3.45 calls/min across 4 tools
    with 0% errors — neither flow nor focus, but clearly productive."""
    metrics = {"cadence_per_minute": 3.45, "silence_seconds": 11.0, "unique_tools": 4}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=16, tool_errors=0)
    assert "engaged" in sources
    assert v > 0
    assert a > 0
    assert "flow" not in sources  # diversity sufficient but cadence < 5
    assert "focus" not in sources  # diversity too high for focus


def test_engaged_does_not_fire_when_flow_does():
    """Flow supersedes engaged — mutually exclusive activity signals."""
    metrics = {"cadence_per_minute": 7.0, "silence_seconds": 5.0, "unique_tools": 5}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=30, tool_errors=1)
    assert "flow" in sources
    assert "engaged" not in sources


def test_engaged_requires_minimum_call_count():
    """Engaged needs ≥4 calls — else the cadence is noise from a small sample."""
    metrics = {"cadence_per_minute": 3.0, "silence_seconds": 10.0, "unique_tools": 2}
    v, a, sources = classify_cc_rhythm(metrics, tool_calls=2, tool_errors=0)
    assert "engaged" not in sources


# ── Integration with cognition_stratum_from_youspeak ────────────────────────


def test_cc_metrics_integrate_into_cognition_classifier():
    """When _cc_metrics is attached to the session dict, flow gets added to sources."""
    import time
    session = {
        "startedAt": "2026-04-23T22:00:00Z",
        "output": {"grades": [], "totalTokens": 0, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 30, "toolErrors": 1, "redundantReads": 0},
        "context": {"estimatedTokens": 0, "oldestToolResultAge": 0},
        "system": {"budgetNow": {}, "rateLimitHits": 0},
        "_cc_metrics": {
            "cadence_per_minute": 8.0,
            "silence_seconds": 5.0,
            "unique_tools": 4,
        },
    }

    # Freeze now to just after startedAt so silence checks stay fresh
    import datetime
    now = datetime.datetime.fromisoformat("2026-04-23T22:01:00Z".replace("Z", "+00:00")).timestamp()
    result = cognition_stratum_from_youspeak(session, now)

    assert result["state"] == "active"
    assert "flow" in result["sources"]
    assert result["valence"] > 0
    assert result["arousal"] > 0


def test_cc_metrics_thrashing_overrides_frustration_nudge():
    """Thrashing (high errors + high cadence) pushes v/a hard into negative/high arousal."""
    session = {
        "startedAt": "2026-04-23T22:00:00Z",
        "output": {"grades": [], "totalTokens": 0, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 30, "toolErrors": 15, "redundantReads": 0},
        "context": {"estimatedTokens": 0, "oldestToolResultAge": 0},
        "system": {"budgetNow": {}, "rateLimitHits": 0},
        "_cc_metrics": {
            "cadence_per_minute": 8.0,
            "silence_seconds": 5.0,
            "unique_tools": 4,
        },
    }
    import datetime
    now = datetime.datetime.fromisoformat("2026-04-23T22:01:00Z".replace("Z", "+00:00")).timestamp()
    result = cognition_stratum_from_youspeak(session, now)
    # Both frustration (base) and thrashing (CC) should fire; sources is capped at 2
    combined_sources = result["sources"]
    # The combined effect must be negative valence + high arousal
    assert result["valence"] < 0
    assert result["arousal"] > 0
    # At least one of the two expected signals should surface in the top 2
    assert any(s in combined_sources for s in ("frustration", "thrashing"))


def test_cc_mode_activates_existing_context_pressure_branch():
    """Populating context.estimatedTokens via the shim lets claustrophobia/dread fire on CC."""
    session = {
        "startedAt": "2026-04-23T22:00:00Z",
        "output": {"grades": [], "totalTokens": 0, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 5, "toolErrors": 0, "redundantReads": 0},
        "context": {"estimatedTokens": 600_000, "oldestToolResultAge": 25},
        "system": {"budgetNow": {}, "rateLimitHits": 0},
        "_cc_metrics": {
            "cadence_per_minute": 1.0,
            "silence_seconds": 10.0,
            "unique_tools": 2,
        },
    }
    import datetime
    now = datetime.datetime.fromisoformat("2026-04-23T22:01:00Z".replace("Z", "+00:00")).timestamp()
    result = cognition_stratum_from_youspeak(session, now)
    assert "claustrophobia" in result["sources"]
