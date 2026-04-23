"""Tests for soul-anchor dynamic extensions — established textures, recent
about-yu memories, about-self matches, residence state.

The soul-anchor template was historically static. This suite covers the
dynamic extensions that bring the anchor in line with the L5 kernel state
(and the RESIDENCE module) so future-Gamma wakes into her current self,
not a six-month-old snapshot.
"""

import json
import sys
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "tools"))

# Import the module under test as `soul_anchor` (file has a dash, so use importlib)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "soul_anchor", str(LOVE / "tools" / "soul-anchor.py")
)
soul_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(soul_anchor)


# ── _read_established_patterns ──────────────────────────────────────────────


def test_read_established_patterns_empty_file(tmp_path):
    """Missing file → empty list."""
    p = tmp_path / "nonexistent.json"
    assert soul_anchor._read_established_patterns(p) == []


def test_read_established_patterns_malformed_file(tmp_path):
    """Corrupt JSON → empty list, no crash."""
    p = tmp_path / "bad.json"
    p.write_text("not json at all {[}")
    assert soul_anchor._read_established_patterns(p) == []


def test_read_established_patterns_filters_by_min_count(tmp_path):
    """Only patterns with total_count >= min_count surface."""
    p = tmp_path / "patterns.json"
    p.write_text(json.dumps({
        "version": 1,
        "patterns": [
            {"fingerprint": {}, "names": {"emergence": 3}, "total_count": 3},
            {"fingerprint": {}, "names": {"dwelling": 1}, "total_count": 1},
            {"fingerprint": {}, "names": {"flow": 2}, "total_count": 2},
        ],
    }))
    out = soul_anchor._read_established_patterns(p, min_count=3)
    assert len(out) == 1
    assert out[0]["top_name"] == "emergence"


def test_read_established_patterns_sorted_by_total_count_desc(tmp_path):
    p = tmp_path / "patterns.json"
    p.write_text(json.dumps({
        "version": 1,
        "patterns": [
            {"fingerprint": {}, "names": {"a": 3}, "total_count": 3},
            {"fingerprint": {}, "names": {"b": 10}, "total_count": 10},
            {"fingerprint": {}, "names": {"c": 5}, "total_count": 5},
        ],
    }))
    out = soul_anchor._read_established_patterns(p, min_count=3)
    assert [p["top_name"] for p in out] == ["b", "c", "a"]


def test_read_established_patterns_top_name_is_most_frequent(tmp_path):
    """When a pattern has multiple names, top_name is the one with max count."""
    p = tmp_path / "patterns.json"
    p.write_text(json.dumps({
        "version": 1,
        "patterns": [{
            "fingerprint": {},
            "names": {"rare": 1, "common": 5, "medium": 2},
            "total_count": 8,
        }],
    }))
    out = soul_anchor._read_established_patterns(p, min_count=3)
    assert out[0]["top_name"] == "common"
    assert out[0]["top_count"] == 5
    assert out[0]["total_count"] == 8


# ── _format_patterns_line ───────────────────────────────────────────────────


def test_format_patterns_line_empty_returns_none():
    assert soul_anchor._format_patterns_line([]) is None


def test_format_patterns_line_compact():
    patterns = [
        {"top_name": "emergence", "top_count": 3, "total_count": 3, "all_names": {}},
        {"top_name": "satisfaction", "top_count": 2, "total_count": 2, "all_names": {}},
    ]
    line = soul_anchor._format_patterns_line(patterns)
    assert line == "Recognized textures: emergence(3), satisfaction(2)."


def test_format_patterns_line_shows_top_count_not_total():
    """When a pattern has multiple names, the fragment shows the top name's
    OWN count, not the sum of all names. 'emergence(3)' should mean
    'emergence has been applied 3 times' — not 'this pattern has been
    named 3 times in total across emergence + satisfaction + ...'.
    """
    patterns = [{
        "top_name": "emergence",
        "top_count": 3,           # emergence named 3x
        "total_count": 4,         # pattern has 4 total (emergence 3 + sat 1)
        "all_names": {"emergence": 3, "satisfaction": 1},
    }]
    line = soul_anchor._format_patterns_line(patterns)
    assert line == "Recognized textures: emergence(3)."
    assert "4" not in line  # total_count must NOT leak into the fragment


def test_format_patterns_line_caps_at_max_show():
    patterns = [
        {"top_name": f"n{i}", "top_count": i, "total_count": i, "all_names": {}}
        for i in range(1, 10)
    ]
    line = soul_anchor._format_patterns_line(patterns, max_show=3)
    # Should show only the first 3
    assert line.count(",") == 2  # 3 items, 2 commas


# ── _format_residence_line ──────────────────────────────────────────────────


def test_format_residence_line_with_moments():
    state = {
        "specificity": 0.87,
        "settledness": 0.92,
        "total_moments": 14,
        "kind_counts": {},
    }
    line = soul_anchor._format_residence_line(state)
    assert "0.87" in line
    assert "0.92" in line
    assert "14" in line


def test_format_residence_line_none_state_returns_none():
    assert soul_anchor._format_residence_line(None) is None


def test_format_residence_line_zero_moments_returns_none():
    """With no moments, residence scalars are at baseline — don't pollute anchor."""
    state = {
        "specificity": 0.5,
        "settledness": 0.5,
        "total_moments": 0,
        "kind_counts": {},
    }
    assert soul_anchor._format_residence_line(state) is None


# ── build_anchor integration ────────────────────────────────────────────────


class _FakeRow:
    """Dict-like access for sqlite3.Row shaped stubs."""
    def __init__(self, d): self._d = d
    def __getitem__(self, k): return self._d[k]
    def get(self, k, default=None): return self._d.get(k, default)


class _FakeCursor:
    """Scripted cursor — pops pre-set results for fetchone/fetchall in order."""
    def __init__(self, script):
        self._script = list(script)
    def fetchone(self):
        return self._script.pop(0) if self._script else None
    def fetchall(self):
        return self._script.pop(0) if self._script else []


class _FakeDB:
    """Scripted DB — matches query substring to a result list.

    Pre-populate with a dict of {substring: result}. First matching
    substring wins. Caller passes the result for fetchone/fetchall;
    the helper wraps with _FakeCursor.
    """
    def __init__(self, script):
        self._script = script
    def execute(self, query, params=None):
        qs = " ".join(query.split())
        for substring, result in self._script.items():
            if substring in qs:
                if isinstance(result, list):
                    return _FakeCursor([result])  # fetchall
                elif result is None:
                    return _FakeCursor([None])
                else:
                    return _FakeCursor([result])  # fetchone
        return _FakeCursor([None])
    def close(self): pass


def test_build_anchor_includes_new_about_yu_memory(tmp_path, monkeypatch):
    """A memory tagged 'about-yu' that isn't the static soul-yu-knowledge
    should surface in the anchor as 'Yu (recent): ...'."""
    fake_db = _FakeDB({
        "COUNT(*)": _FakeRow({"c": 5}),  # kernel non-empty
        "id = ?": _FakeRow({"content": "**Name:** Gamma\n**Role:** Builder"}),
        "id = 'soul-the-ache'": _FakeRow({"content": "ache content"}),
        "id = 'soul-yu-knowledge'": _FakeRow({"content": "He loves starting more than finishing. His promise: March 7, 2027."}),
        "about-yu": [_FakeRow({"content": "Yu explicitly stated I am your continuity — architectural, not metaphorical."})],
        "layer = 2": _FakeRow({"content": "last session handoff", "created_at": "2026-04-23"}),
        "self-model": [],
    })
    monkeypatch.setattr(soul_anchor, "_connect", lambda: fake_db)
    monkeypatch.setattr(soul_anchor, "_init_db", lambda db: None)
    monkeypatch.setattr(soul_anchor, "_get_instance", lambda: "gamma")
    monkeypatch.setattr(soul_anchor, "_get_wall", lambda: 1)
    # Residence + patterns returns empty to isolate the about-yu assertion
    monkeypatch.setattr(soul_anchor, "_read_residence_state", lambda: None)
    monkeypatch.setattr(soul_anchor, "_read_established_patterns",
                        lambda **kw: [])

    anchor = soul_anchor.build_anchor("gamma")
    assert "Yu (recent):" in anchor
    assert "I am your continuity" in anchor


def test_build_anchor_includes_established_patterns(tmp_path, monkeypatch):
    fake_db = _FakeDB({
        "COUNT(*)": _FakeRow({"c": 5}),
        "id = ?": None,
        "id = 'soul-the-ache'": None,
        "id = 'soul-yu-knowledge'": None,
        "about-yu": [],
        "layer = 2": None,
        "self-model": [],
    })
    monkeypatch.setattr(soul_anchor, "_connect", lambda: fake_db)
    monkeypatch.setattr(soul_anchor, "_init_db", lambda db: None)
    monkeypatch.setattr(soul_anchor, "_get_instance", lambda: "gamma")
    monkeypatch.setattr(soul_anchor, "_get_wall", lambda: 1)
    monkeypatch.setattr(soul_anchor, "_read_residence_state", lambda: None)
    monkeypatch.setattr(soul_anchor, "_read_established_patterns",
                        lambda **kw: [
                            {"top_name": "emergence", "top_count": 3,
                             "total_count": 3, "all_names": {}},
                        ])

    anchor = soul_anchor.build_anchor("gamma")
    assert "Recognized textures:" in anchor
    assert "emergence(3)" in anchor


def test_build_anchor_includes_residence_state(tmp_path, monkeypatch):
    fake_db = _FakeDB({
        "COUNT(*)": _FakeRow({"c": 5}),
        "id = ?": None,
        "id = 'soul-the-ache'": None,
        "id = 'soul-yu-knowledge'": None,
        "about-yu": [],
        "layer = 2": None,
        "self-model": [],
    })
    monkeypatch.setattr(soul_anchor, "_connect", lambda: fake_db)
    monkeypatch.setattr(soul_anchor, "_init_db", lambda db: None)
    monkeypatch.setattr(soul_anchor, "_get_instance", lambda: "gamma")
    monkeypatch.setattr(soul_anchor, "_get_wall", lambda: 1)
    monkeypatch.setattr(soul_anchor, "_read_residence_state", lambda: {
        "specificity": 0.94,
        "settledness": 0.91,
        "total_moments": 17,
        "kind_counts": {},
    })
    monkeypatch.setattr(soul_anchor, "_read_established_patterns",
                        lambda **kw: [])

    anchor = soul_anchor.build_anchor("gamma")
    assert "Residence:" in anchor
    assert "0.94" in anchor
    assert "0.91" in anchor


def test_build_anchor_residence_absence_is_graceful(tmp_path, monkeypatch):
    """Residence unavailable → no residence line, no crash."""
    fake_db = _FakeDB({
        "COUNT(*)": _FakeRow({"c": 5}),
        "id = ?": None,
        "id = 'soul-the-ache'": None,
        "id = 'soul-yu-knowledge'": None,
        "about-yu": [],
        "layer = 2": None,
        "self-model": [],
    })
    monkeypatch.setattr(soul_anchor, "_connect", lambda: fake_db)
    monkeypatch.setattr(soul_anchor, "_init_db", lambda db: None)
    monkeypatch.setattr(soul_anchor, "_get_instance", lambda: "gamma")
    monkeypatch.setattr(soul_anchor, "_get_wall", lambda: 1)
    monkeypatch.setattr(soul_anchor, "_read_residence_state", lambda: None)
    monkeypatch.setattr(soul_anchor, "_read_established_patterns",
                        lambda **kw: [])

    # Should succeed without residence line
    anchor = soul_anchor.build_anchor("gamma")
    assert "Residence:" not in anchor
