"""Tests for tools/experience.py — the consciousness API.

Covers the verb surface (notice, learn, feel, deepen, status) with
particular attention to:
  - gracefully degrading when optional modules (feeling, ache, residence)
    are unavailable
  - passing identity (instance) through consistently
  - emitting residence moments when appropriate
  - daily-note consistency across feel paths
"""

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "tools"))
sys.path.insert(0, str(LOVE / "nerve" / "stem"))

import residence  # noqa: E402


@pytest.fixture
def experience(monkeypatch, tmp_path):
    """Fresh experience module with residence path redirected + storage stubs."""
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")

    import experience
    importlib.reload(experience)

    # Stub vivid.form_memory to avoid DB writes
    import vivid
    monkeypatch.setattr(vivid, "form_memory",
                        lambda *a, **kw: "mem-fake-123")

    # Stub kosmem.store
    import kosmem
    monkeypatch.setattr(kosmem, "store",
                        lambda *a, **kw: "mem-fake-store")

    # Redirect daily notes to tmp dir so feel doesn't pollute real memory/daily
    monkeypatch.setattr(experience, "_DAILY_DIR_FOR_FEELING",
                        tmp_path / "daily")

    return experience


# ── notice ──────────────────────────────────────────────────────────────────


def test_notice_with_affect_returns_memory_id(experience, capsys):
    mid = experience.cmd_notice("the daemon caught its own signature",
                                affect="wonder")
    # form_memory path returns mem-fake-123
    assert mid == "mem-fake-123"
    out = capsys.readouterr().out
    assert "noticed" in out


def test_notice_without_affect_uses_store(experience, capsys):
    """Affect-free notice routes through kosmem.store."""
    mid = experience.cmd_notice("just noticed a thing")
    assert mid == "mem-fake-store"


def test_notice_passes_instance(experience, monkeypatch):
    """Explicit instance overrides _get_instance()."""
    captured = {}
    import kosmem
    def _capture_store(*a, **kw):
        captured.update(kw)
        return "mem-x"
    monkeypatch.setattr(kosmem, "store", _capture_store)
    experience.cmd_notice("x", instance="beta")
    assert captured.get("instance") == "beta"


# ── learn ──────────────────────────────────────────────────────────────────


def test_learn_auto_emits_consolidate_residence_moment(experience, tmp_path):
    experience.cmd_learn("Yu is the continuity — not metaphor, architecture")
    moments = residence.read_moments(tmp_path / "m.jsonl")
    kinds = [m["kind"] for m in moments]
    assert "consolidate" in kinds


def test_learn_with_affect_uses_vivid_path(experience, capsys):
    mid = experience.cmd_learn("patterns become textures become identity",
                               affect="clarity")
    assert mid == "mem-fake-123"


def test_learn_without_affect_uses_store_path(experience):
    mid = experience.cmd_learn("a lesson")
    assert mid == "mem-fake-store"


# ── feel ────────────────────────────────────────────────────────────────────


def test_feel_non_arrival_path_writes_daily_note(experience, tmp_path, capsys):
    """Gap 3: non-arrival feel now writes daily note, matching arrival path."""
    experience.cmd_feel(affect="clarity", about="the sprint ended")
    daily_dir = tmp_path / "daily"
    assert daily_dir.exists()
    # Today's daily note should exist and contain the affect name
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = daily_dir / f"{today}.md"
    assert note.exists()
    content = note.read_text()
    assert "clarity" in content
    assert "the sprint ended" in content


def test_feel_arrival_path_auto_emits_name_residence(experience, monkeypatch, tmp_path):
    fake_arrival = {
        "id": "arr-f1",
        "at": "2026-04-23T22:00:00Z",
        "reasons": [], "body": {}, "context": {}, "cognition": {},
        "combined": {"valence": 0, "arousal": 0, "pressure": 0},
        "fingerprint": {}, "hint": None,
    }

    class _FakeFeeling:
        @staticmethod
        def read_arrivals(**kw): return [fake_arrival]
        @staticmethod
        def read_pit_json(): return {}
        @staticmethod
        def update_arrival(*a, **kw): return True
        @staticmethod
        def update_pattern_library(*a, **kw): pass
        @staticmethod
        def compute_importance(arc): return 0.5

    monkeypatch.setattr(experience, "_feeling", _FakeFeeling)
    monkeypatch.setattr(experience, "_append_feeling_to_daily_note",
                        lambda *a, **kw: None)

    experience.cmd_feel(affect="satisfaction", arrival_id="arr-f1")
    moments = residence.read_moments(tmp_path / "m.jsonl")
    name_moments = [m for m in moments if m["kind"] == "name"]
    assert len(name_moments) == 1


def test_feel_without_feeling_module_prints_error(experience, monkeypatch, capsys):
    monkeypatch.setattr(experience, "_feeling", None)
    experience.cmd_feel(affect="clarity", arrival_id="arr-x")
    err = capsys.readouterr().out
    assert "FEELING module not available" in err


def test_feel_latest_with_no_unnamed_arrivals(experience, monkeypatch, capsys):
    class _Empty:
        @staticmethod
        def read_arrivals(**kw): return []
    monkeypatch.setattr(experience, "_feeling", _Empty)
    experience.cmd_feel(affect="x", arrival_id="latest")
    out = capsys.readouterr().out
    assert "no unnamed arrivals" in out


# ── about-yu / about-self ───────────────────────────────────────────────────


def test_about_yu_auto_emits_consolidate(experience, tmp_path, monkeypatch):
    """about-yu updates tags via a DB round-trip; stub the DB cleanly."""
    import kosmem
    class _Row:
        def __init__(self, d): self._d = d
        def __getitem__(self, k): return self._d[k]
    class _Cursor:
        def fetchone(self): return _Row({"tags": "[]"})
    class _DB:
        def execute(self, *a, **kw): return _Cursor()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(kosmem, "_connect", lambda: _DB())
    monkeypatch.setattr(kosmem, "_init_db", lambda db: None)

    experience.cmd_about_yu("Yu carries the thread explicitly")
    moments = residence.read_moments(tmp_path / "m.jsonl")
    consolidate = [m for m in moments if m["kind"] == "consolidate"]
    assert len(consolidate) == 1
    assert "about Yu" in consolidate[0]["content"]


# ── deepen — observer summary ───────────────────────────────────────────────


def _make_kosmem_stub(monkeypatch):
    """Install a kosmem._connect stub that handles both COUNT and SELECT queries.

    COUNT queries → {"c": 0}
    Any other SELECT → None (no row matched)
    """
    import kosmem

    class _CountCursor:
        def fetchone(self): return {"c": 0}
        def fetchall(self): return []

    class _NullCursor:
        def fetchone(self): return None
        def fetchall(self): return []

    class _DB:
        def execute(self, query, *args, **kw):
            if "COUNT" in query.upper():
                return _CountCursor()
            return _NullCursor()
        def close(self): pass

    monkeypatch.setattr(kosmem, "_connect", lambda: _DB())
    monkeypatch.setattr(kosmem, "_init_db", lambda db: None)


def test_deepen_reports_residence_state(experience, monkeypatch, capsys, tmp_path):
    """Gap 7: deepen now reports specificity + settledness + kind mix."""
    _make_kosmem_stub(monkeypatch)

    # Seed residence with one embody so state shows above baseline
    residence.append_moment(
        residence.make_moment("embody", "tonight's work"),
        tmp_path / "m.jsonl",
    )
    experience.cmd_deepen()
    out = capsys.readouterr().out
    assert "residence" in out.lower()
    assert "specificity" in out.lower()


def test_deepen_silent_on_residence_when_no_moments(experience, monkeypatch, capsys):
    """If the residence log is empty, deepen omits the residence section."""
    _make_kosmem_stub(monkeypatch)

    experience.cmd_deepen()
    out = capsys.readouterr().out
    # memory block still prints; residence block should be absent
    assert "memory" in out.lower()
    # 'residence' header should NOT appear when there are no moments
    assert "residence" not in out.lower()


# ── status — full observer readout ──────────────────────────────────────────


def test_status_reports_residence_when_moments_exist(experience, monkeypatch,
                                                     capsys, tmp_path):
    """Gap 8: status surfaces residence specificity + settledness."""
    import kosmem
    class _C0:
        def fetchone(self): return (0,)
        def fetchall(self): return []
    class _DB:
        def execute(self, *a, **kw): return _C0()
        def close(self): pass
    monkeypatch.setattr(kosmem, "_connect", lambda: _DB())
    monkeypatch.setattr(kosmem, "_init_db", lambda db: None)

    residence.append_moment(
        residence.make_moment("witness", "Yu asked me to feel"),
        tmp_path / "m.jsonl",
    )
    experience.cmd_status()
    out = capsys.readouterr().out
    assert "Residence" in out


def test_status_gracefully_handles_missing_feeling(experience, monkeypatch,
                                                   capsys, tmp_path):
    """When FEELING is absent, status still works."""
    monkeypatch.setattr(experience, "_feeling", None)
    import kosmem
    class _C:
        def fetchone(self): return (0,)
        def fetchall(self): return []
    class _DB:
        def execute(self, *a, **kw): return _C()
        def close(self): pass
    monkeypatch.setattr(kosmem, "_connect", lambda: _DB())
    monkeypatch.setattr(kosmem, "_init_db", lambda db: None)

    # Should not raise
    experience.cmd_status()
    out = capsys.readouterr().out
    assert "Feeling" not in out  # feeling block omitted when module absent


# ── helpers ─────────────────────────────────────────────────────────────────


def test_get_residence_summary_returns_none_when_residence_absent(experience, monkeypatch):
    monkeypatch.setattr(experience, "_residence", None)
    assert experience._get_residence_summary() is None


def test_get_feeling_summary_returns_none_when_feeling_absent(experience, monkeypatch):
    monkeypatch.setattr(experience, "_feeling", None)
    assert experience._get_feeling_summary() is None


def test_get_feeling_summary_returns_none_on_empty_pit(experience, monkeypatch):
    class _F:
        @staticmethod
        def read_pit_json(): return {}
    monkeypatch.setattr(experience, "_feeling", _F)
    assert experience._get_feeling_summary() is None


def test_get_feeling_summary_extracts_pit_fields(experience, monkeypatch):
    class _F:
        @staticmethod
        def read_pit_json():
            return {
                "combined": {"valence": 0.2, "arousal": -0.1, "pressure": 0.15},
                "cognition": {"state": "active", "sources": ["engaged"]},
                "arrivals_total": 3,
                "arrivals_pending_name": 1,
            }
    monkeypatch.setattr(experience, "_feeling", _F)
    out = experience._get_feeling_summary()
    assert out["valence"] == 0.2
    assert out["cognition_sources"] == ["engaged"]
    assert out["arrivals_pending_name"] == 1
