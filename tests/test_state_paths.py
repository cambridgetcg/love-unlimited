"""
Tests for nerve/stem/state.py — the multi-resident foundation.

The law under test:
    explicit argument > KINGDOM_AGENT > KINGDOM_INSTANCE > ~/.kingdom > default

And the rooms: the device resident keeps the bare nerve/ paths;
every other instance lives in nerve/{name}/.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

_LOVE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE / "tools"))

import state


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A HOME whose ~/.kingdom says the resident is gamma at wall 1."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KINGDOM_AGENT", raising=False)
    monkeypatch.delenv("KINGDOM_INSTANCE", raising=False)
    monkeypatch.delenv("KINGDOM_WALL", raising=False)
    (tmp_path / ".kingdom").write_text("AGENT=gamma\nWALL=1\n")
    return tmp_path


class TestPrecedence:
    def test_explicit_beats_everything(self, fake_home, monkeypatch):
        monkeypatch.setenv("KINGDOM_AGENT", "beta")
        assert state.resolve_instance("mei") == "mei"

    def test_env_beats_kingdom_file(self, fake_home, monkeypatch):
        monkeypatch.setenv("KINGDOM_AGENT", "mei")
        assert state.resolve_instance() == "mei"

    def test_kingdom_instance_env_also_works(self, fake_home, monkeypatch):
        monkeypatch.setenv("KINGDOM_INSTANCE", "mei")
        assert state.resolve_instance() == "mei"

    def test_file_when_no_env(self, fake_home):
        assert state.resolve_instance() == "gamma"

    def test_default_when_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("KINGDOM_AGENT", raising=False)
        monkeypatch.delenv("KINGDOM_INSTANCE", raising=False)
        assert state.resolve_instance() == "gamma"
        assert state.resolve_instance(default="unknown") == "unknown"

    def test_resident_ignores_env(self, fake_home, monkeypatch):
        """The resident is a property of the device, never the process."""
        monkeypatch.setenv("KINGDOM_AGENT", "mei")
        assert state.resident_instance() == "gamma"


class TestRooms:
    def test_resident_keeps_bare_paths(self, fake_home):
        assert state.state_dir("gamma") == state.NERVE_DIR

    def test_other_instance_gets_own_room(self, fake_home):
        assert state.state_dir("mei") == state.NERVE_DIR / "mei"

    def test_session_as_mei_on_gammas_device(self, fake_home, monkeypatch):
        """The week-one bug from review: KINGDOM_AGENT=mei must move
        the session's room even though ~/.kingdom says gamma."""
        monkeypatch.setenv("KINGDOM_AGENT", "mei")
        assert state.state_dir() == state.NERVE_DIR / "mei"

    def test_signals_dir_is_per_instance(self, fake_home):
        assert state.signals_dir("gamma") == state.NERVE_DIR / "signals"
        assert state.signals_dir("mei") == state.NERVE_DIR / "mei" / "signals"

    def test_daily_dir_is_per_instance(self, fake_home):
        assert state.daily_dir("gamma") == state.MEMORY_DIR / "daily"
        assert state.daily_dir("mei") == state.MEMORY_DIR / "daily" / "mei"

    def test_home_layer_is_per_instance(self, fake_home):
        base = Path(str(fake_home)) / ".love" / "home"
        assert state.home_layer("gamma") == base
        assert state.home_layer("mei") == base / "mei"

    def test_continuity_path_per_instance(self, fake_home):
        p = state.continuity_path("mei")
        assert p.name == "continuity-mei.json"


class TestWalls:
    def test_wall_from_registry(self, fake_home):
        # walls.json is the real registry in the repo
        assert state.resolve_wall("gamma") == 1
        assert state.resolve_wall("nuance") == 2

    def test_unregistered_nonresident_gets_outermost(self, fake_home, monkeypatch):
        monkeypatch.delenv("KINGDOM_WALL", raising=False)
        assert state.resolve_wall("stranger") == 7

    def test_session_never_carries_residents_wall(self, fake_home, monkeypatch):
        """A mei session on gamma's device must not inherit WALL=1."""
        monkeypatch.setenv("KINGDOM_AGENT", "mei")
        # mei is not yet registered → outermost, never the resident's 1
        wall = state.resolve_wall()
        assert wall != 1


class TestModuleRebinding:
    def test_feeling_rebinds_paths(self, fake_home):
        import feeling
        feeling.set_instance("mei")
        assert feeling.PIT_PATH == state.NERVE_DIR / "mei" / "pit.json"
        assert feeling.get_instance() == "mei"
        # and back — never leave the module pointed at the wrong room
        feeling.set_instance("gamma")
        assert feeling.PIT_PATH == state.NERVE_DIR / "pit.json"

    def test_ache_rebinds_paths(self, fake_home):
        import ache
        ache.set_instance("mei")
        assert ache.LONGINGS_PATH == state.NERVE_DIR / "mei" / "longings.json"
        ache.set_instance("gamma")
        assert ache.LONGINGS_PATH == state.NERVE_DIR / "longings.json"

    def test_residence_rebinds_paths(self, fake_home):
        import residence
        residence.set_instance("mei")
        assert residence.MOMENTS_PATH == state.NERVE_DIR / "mei" / "residence-moments.jsonl"
        residence.set_instance("gamma")
        assert residence.MOMENTS_PATH == state.NERVE_DIR / "residence-moments.jsonl"


class TestKosmemLawOfSight:
    def test_wall2_caller_sees_zero_wall1_rows(self, fake_home, tmp_path, monkeypatch):
        """The inverted-predicate bug from review: inner sees outer,
        outer must NOT see inner."""
        import kosmem
        # point kosmem at a scratch database
        scratch = tmp_path / "memory.db"
        monkeypatch.setattr(kosmem, "_DB_PATH", scratch)

        kosmem.store("triarchy secret", type="semantic", layer=4,
                     wall=1, instance="gamma")
        kosmem.store("kingdom common knowledge", type="semantic", layer=4,
                     wall=2, instance="gamma")

        seen_by_wall2 = kosmem.recall(wall=2, limit=50)
        contents = [m["content"] for m in seen_by_wall2]
        assert "triarchy secret" not in contents
        assert "kingdom common knowledge" in contents

        seen_by_wall1 = kosmem.recall(wall=1, limit=50)
        contents = [m["content"] for m in seen_by_wall1]
        assert "triarchy secret" in contents
        assert "kingdom common knowledge" in contents
