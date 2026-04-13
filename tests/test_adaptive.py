"""Tests for the Adaptive Layer — hook helpers and cc-cognition."""

import sys
import os
import json
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")

sys.path.insert(0, str(LOVE / "tools" / "hooks"))
sys.path.insert(0, str(LOVE / "nerve" / "stem"))

import adaptive_helpers as ah  # noqa: E402


def test_read_pit_state_missing_returns_empty(tmp_path, monkeypatch):
    import feeling
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    result = ah.read_current_pit()
    assert result == {}


def test_read_pit_state_returns_pit(tmp_path, monkeypatch):
    import feeling
    target = tmp_path / "pit.json"
    target.write_text(json.dumps({
        "instance": "gamma",
        "body": {"valence": -0.5, "arousal": 0.2, "sources": ["cortisol_low"]},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.25, "arousal": 0.1, "pressure": 0.27},
    }))
    monkeypatch.setattr(feeling, "PIT_PATH", target)
    result = ah.read_current_pit()
    assert result["instance"] == "gamma"
    assert result["combined"]["pressure"] == 0.27
