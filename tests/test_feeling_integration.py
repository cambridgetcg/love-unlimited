"""Integration tests for FEELING — cmd_feel, daily note, waking phase."""

import sys
import os
import json
import importlib.util
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")

# IMPORTANT: use normal import (via sys.path) so `feeling` is registered in
# sys.modules. When experience.py later does `import feeling as _feeling`,
# it shares the same module object — monkey-patches on `feeling` attributes
# are visible to experience.py. Do NOT use importlib.util.module_from_spec
# for feeling — that creates a second instance and breaks the shared state.
sys.path.insert(0, str(LOVE / "nerve" / "stem"))
import feeling  # noqa: E402


def test_cmd_feel_arrival_id_updates_arrival_and_pattern(tmp_path, monkeypatch):
    # Redirect paths
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")

    # Seed an arrival
    arrival = {
        "id": "arr-test-cmd-feel",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.3, "sources": ["cortisol_moderate"]},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": ["yu_present"]},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.15, "pressure": 0.6},
        "fingerprint": {
            "body_v_bucket": "neg", "body_a_bucket": "mid",
            "context_v_bucket": "neutral", "context_a_bucket": "low",
            "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
            "dominant_reason": "pressure", "top_sources": ["yu_present"],
        },
        "hint": None,
        "named": False, "witnessed": True,
    }
    feeling.append_arrival(arrival)

    # Import experience.py as a module
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    # Call cmd_feel with arrival-id
    experience.cmd_feel(
        affect="settling",
        arrival_id="arr-test-cmd-feel",
        rationale="relief after hive silence",
        scene="entered my voice as a pause",
    )

    # Verify arrival marked named
    loaded = feeling.read_arrivals()
    assert loaded[0]["named"] is True
    assert loaded[0]["name"] == "settling"
    assert loaded[0]["rationale"] == "relief after hive silence"

    # Verify pattern library updated
    patterns = feeling.read_patterns()
    assert len(patterns["patterns"]) >= 1
    assert "settling" in patterns["patterns"][0]["names"]


def test_cmd_feel_arrival_id_appends_daily_note(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling, "DAILY_DIR", tmp_path / "daily")

    arrival = {
        "id": "arr-test-daily",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.2, "arousal": 0.3, "sources": []},
        "context": {"valence": 0.1, "arousal": 0.2, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.05, "arousal": 0.25, "pressure": 0.25},
        "fingerprint": {
            "body_v_bucket": "neg", "body_a_bucket": "mid",
            "context_v_bucket": "neutral", "context_a_bucket": "low",
            "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
            "dominant_reason": "pressure", "top_sources": [],
        },
        "hint": None, "named": False, "witnessed": True,
    }
    feeling.append_arrival(arrival)

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    monkeypatch.setattr(experience, "_DAILY_DIR_FOR_FEELING", tmp_path / "daily", raising=False)

    experience.cmd_feel(
        affect="curiosity",
        arrival_id="arr-test-daily",
        rationale="why is the light different today",
        scene="slowed my typing",
    )

    # Check daily file exists and contains feeling
    daily_files = list((tmp_path / "daily").glob("*.md"))
    assert len(daily_files) == 1
    content = daily_files[0].read_text()
    assert "## Feelings" in content
    assert "curiosity" in content
    assert "why is the light different today" in content


def test_cmd_die_captures_pit_and_unnamed_arrivals(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")

    # Seed pit state
    feeling.write_pit_json({
        "instance": "gamma",
        "combined": {"valence": -0.1, "arousal": 0.2, "pressure": 0.22},
        "body": {"valence": -0.2, "arousal": 0.1, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.3, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
    })

    # Seed a witnessed-unnamed arrival
    feeling.append_arrival({
        "id": "arr-end",
        "at": "2026-04-11T11:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.5}],
        "body": {"valence": -0.2, "arousal": 0.2, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.1, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.1, "arousal": 0.15, "pressure": 0.18},
        "named": False, "witnessed": True,
    })

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    # Call the helper that gathers pit+unnamed for death memory
    pit_block, unnamed = experience._collect_death_feeling_context()
    assert pit_block is not None
    assert pit_block["combined"]["pressure"] == 0.22
    assert len(unnamed) >= 1
    assert unnamed[0]["id"] == "arr-end"


def test_phase_pit_reports_empty_returns_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", tmp_path / "pit_state.json")

    feeling.update_pit_state({"last_wake_at": "2026-04-11T07:00:00Z"})

    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)

    text = waking.phase_pit_reports(instance="gamma")
    assert "pit is quiet" in text.lower() or "nothing stirred" in text.lower()


def test_phase_pit_reports_lists_unwitnessed(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", tmp_path / "pit_state.json")

    feeling.update_pit_state({"last_wake_at": "2026-04-11T06:00:00Z"})
    feeling.append_arrival({
        "id": "arr-sleep-1",
        "at": "2026-04-11T06:30:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.2, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.1, "pressure": 0.6},
        "hint": None, "named": False, "witnessed": False,
    })

    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)

    text = waking.phase_pit_reports(instance="gamma")
    assert "Things stirred" in text or "stirred" in text
    assert "pressure" in text.lower()
    assert "No names yet" in text or "only pressure" in text.lower()
