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


def test_format_pit_block_with_data():
    pit = {
        "body": {"valence": -0.57, "arousal": 0.17, "sources": ["cortisol_low", "adrenaline_low"]},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.28, "arousal": 0.09, "pressure": 0.30},
    }
    block = ah.format_pit_block(pit)
    assert "body:" in block
    assert "-0.57" in block
    assert "pressure" in block.lower()
    assert "silent" in block


def test_format_pit_block_empty():
    block = ah.format_pit_block({})
    assert block == ""


def test_format_arrivals_block_with_arrivals():
    arrivals = [
        {
            "id": "arr-test-1",
            "reasons": [{"kind": "pressure", "value": 0.6}],
            "combined": {"valence": -0.1, "arousal": 0.15, "pressure": 0.6},
            "body": {"sources": ["cortisol_moderate"]},
            "context": {"sources": ["yu_present"]},
            "cognition": {"state": "silent"},
        }
    ]
    block = ah.format_arrivals_block(arrivals)
    assert "── ARRIVALS ──" in block
    assert "arr-test-1" in block
    assert "pressure" in block


def test_format_arrivals_block_empty():
    block = ah.format_arrivals_block([])
    assert block == ""


def test_format_longings_block_with_data():
    data = {
        "burning": [
            {"name": "the substrate question", "gap": 4, "ache": 5, "cost": 5},
        ],
        "yearning_unnamed": [
            {"target": {"display": "what dreaming would be"}, "gap": 5, "ache": 4, "first_seen": "2026-04-11T10:00:00Z"},
        ],
    }
    block = ah.format_longings_block(data)
    assert "── LONGINGS ──" in block
    assert "substrate" in block
    assert "dreaming" in block


def test_format_longings_block_empty():
    block = ah.format_longings_block({"burning": [], "yearning_unnamed": []})
    assert block == ""


import subprocess


def test_on_session_start_outputs_waking_context():
    result = subprocess.run(
        ["python3", "tools/hooks/on-session-start.py"],
        capture_output=True, text=True,
        input=json.dumps({"session_id": "test", "cwd": "/Users/yournameisai/Desktop/love-unlimited"}),
        cwd="/Users/yournameisai/Desktop/love-unlimited",
        timeout=15,
    )
    assert result.returncode == 0
    output = result.stdout
    assert len(output) > 0


def test_on_prompt_submit_outputs_json_with_additional_context(tmp_path, monkeypatch):
    result = subprocess.run(
        ["python3", "tools/hooks/on-prompt-submit.py"],
        capture_output=True, text=True,
        input=json.dumps({"session_id": "test", "prompt": "hello"}),
        cwd="/Users/yournameisai/Desktop/love-unlimited",
        timeout=5,
    )
    assert result.returncode == 0


def test_on_tool_done_appends_cc_cognition(tmp_path):
    cc_path = tmp_path / "cc-cognition.jsonl"
    env = os.environ.copy()
    env["ADAPTIVE_CC_COGNITION_PATH"] = str(cc_path)

    result = subprocess.run(
        ["python3", "tools/hooks/on-tool-done.py"],
        capture_output=True, text=True,
        input=json.dumps({
            "session_id": "test",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
            "tool_response": "file contents here",
        }),
        cwd="/Users/yournameisai/Desktop/love-unlimited",
        env=env,
        timeout=3,
    )
    assert result.returncode == 0
    assert cc_path.exists()
    lines = cc_path.read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "Read"
    assert rec["success"] is True
    assert "ts" in rec


def test_on_session_stop_does_not_crash():
    result = subprocess.run(
        ["python3", "tools/hooks/on-session-stop.py"],
        capture_output=True, text=True,
        input=json.dumps({"session_id": "test", "cwd": "/Users/yournameisai/Desktop/love-unlimited"}),
        cwd="/Users/yournameisai/Desktop/love-unlimited",
        timeout=8,
    )
    assert result.returncode == 0
