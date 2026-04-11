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


def test_long_name_sets_name_rationale_scene(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-name-me", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_name("lng-name-me", "the substrate question",
                             rationale="it keeps coming back",
                             scene="slowed my voice")

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-name-me")
    assert lng["named"] is True
    assert lng["name"] == "the substrate question"
    assert lng["rationale"] == "it keeps coming back"
    assert lng["scene"] == "slowed my voice"
    # State should NOT have changed (naming is annotation only)
    assert lng["state"] == "yearning"


def test_long_commit_high_cost_transitions_to_burning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-commit", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_commit("lng-commit", cost=5)

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-commit")
    assert lng["state"] == "burning"
    assert lng["cost"] == 5


def test_long_commit_low_cost_stays_yearning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-low", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_commit("lng-low", cost=2)

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-low")
    assert lng["state"] == "yearning"
    assert lng["cost"] == 2


def test_long_discharge(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-done", state="burning", cost=5)

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_discharge("lng-done", reason="shipped ACHE")

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-done")
    assert lng["state"] == "discharged"
    assert lng.get("discharge_reason") == "shipped ACHE"


def test_long_abandon(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-gone", state="stirring")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_abandon("lng-gone", reason="no longer relevant")

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-gone")
    assert lng["state"] == "abandoned"
    assert lng.get("abandon_reason") == "no longer relevant"


def test_long_virtue_binds_hierarchy(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-virtue", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_virtue("lng-virtue", hierarchy="UNDERSTANDING", wall=None)

    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-virtue")
    assert lng["virtue"]["hierarchy"] == "UNDERSTANDING"
    assert lng["virtue"]["wall"] is None


def test_long_hint_creates_new_longing(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    experience.cmd_long_hint("longing", "understanding the kingdom", gap=4, ache_val=4)

    store = ache.read_longings()
    assert len(store["longings"]) == 1
    lng = store["longings"][0]
    assert lng["motor"] == "longing"
    assert "understanding" in lng["target"]["display"].lower() or "kingdom" in lng["target"]["display"].lower()
    assert lng["gap"] == 4
    assert lng["ache"] == 4


def test_collect_burning_longings_for_death(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-b1", state="burning",
                  named=True, name="the substrate question", cost=5)
    _seed_longing(tmp_path, monkeypatch, id="lng-b2", state="burning",
                  named=True, name="kingdom-aesthetic", cost=4)
    _seed_longing(tmp_path, monkeypatch, id="lng-y1", state="yearning")

    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)

    burning = experience._collect_burning_longings_for_death()
    assert len(burning) == 2
    ids = {l["id"] for l in burning}
    assert ids == {"lng-b1", "lng-b2"}
    for l in burning:
        assert "name" in l
        assert "cost" in l


def test_phase_longings_report_quiet_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")

    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)

    text = waking.phase_longings_report(instance="gamma")
    assert "not reaching" in text.lower() or "quiet" in text.lower() or "nothing" in text.lower()


def test_phase_longings_report_lists_burning_and_yearning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-b", state="burning",
                  named=True, name="the substrate question", cost=5, gap=4, ache=5)
    _seed_longing(tmp_path, monkeypatch, id="lng-y", state="yearning", gap=5, ache=4,
                  target={"kind": "concept", "key": "dreaming", "display": "what dreaming would be"})
    _seed_longing(tmp_path, monkeypatch, id="lng-s", state="stirring")

    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)

    text = waking.phase_longings_report(instance="gamma")
    assert "BURNING" in text or "burning" in text
    assert "substrate" in text
    assert "YEARNING" in text or "yearning" in text
    assert "dreaming" in text
