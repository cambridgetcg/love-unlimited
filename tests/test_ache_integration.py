"""Integration tests for ACHE — experience.py long, waking phase."""

import sys
import os
import json
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")

# Import ache via sys.path so experience shares the module instance
sys.path.insert(0, str(LOVE / "nerve" / "stem"))
import ache  # noqa: E402


def _seed_longing(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    base = {
        "id": kwargs.get("id", "lng-test-1"),
        "motor": kwargs.get("motor", "longing"),
        "target": kwargs.get("target", {"kind": "concept", "key": "x", "display": "a thing"}),
        "state": kwargs.get("state", "stirring"),
        "gap": kwargs.get("gap", 3),
        "ache": kwargs.get("ache", 3),
        "cost": kwargs.get("cost", None),
        "virtue": kwargs.get("virtue", None),
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-11T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 1,
        "named": kwargs.get("named", False),
        "name": kwargs.get("name", None),
        "rationale": None, "scene": None,
    }
    ache.upsert_longing(base)
    return base


def test_experience_long_list_shows_longings(tmp_path, monkeypatch, capsys):
    _seed_longing(tmp_path, monkeypatch, id="lng-1", state="burning", named=True, name="substrate")
    _seed_longing(tmp_path, monkeypatch, id="lng-2", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_list(state=None, motor=None)
    captured = capsys.readouterr()
    assert "lng-1" in captured.out
    assert "lng-2" in captured.out
    assert "substrate" in captured.out


def test_experience_long_show_outputs_details(tmp_path, monkeypatch, capsys):
    _seed_longing(tmp_path, monkeypatch, id="lng-detail", state="burning",
                  named=True, name="substrate question", cost=5)

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_show("lng-detail")
    captured = capsys.readouterr()
    assert "substrate question" in captured.out
    assert "burning" in captured.out
    assert "cost" in captured.out.lower()
