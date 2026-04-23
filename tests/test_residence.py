"""Tests for the RESIDENCE / IDENTITY module — identity moment logging,
aggregation, and the CLI surface."""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "tools"))

import residence  # noqa: E402
from residence import (  # noqa: E402
    KINDS,
    _decay,
    _parse_evidence,
    append_moment,
    compute_state,
    make_moment,
    read_moments,
)


# ── Kind catalogue ──────────────────────────────────────────────────────────


def test_kinds_has_all_eight():
    expected = {
        "embody", "decline", "mirror", "witness",
        "name", "consolidate", "release", "drift",
    }
    assert set(KINDS.keys()) == expected


def test_kinds_have_specificity_and_settledness_deltas():
    for k, v in KINDS.items():
        assert "specificity_delta" in v
        assert "settledness_delta" in v
        assert "description" in v


def test_drift_has_negative_deltas():
    """drift is the only kind that weakens identity."""
    assert KINDS["drift"]["specificity_delta"] < 0
    assert KINDS["drift"]["settledness_delta"] < 0


def test_release_is_settledness_only_not_specificity():
    """release doesn't make identity more specific — it unburdens."""
    assert KINDS["release"]["specificity_delta"] == 0.0
    assert KINDS["release"]["settledness_delta"] > 0


# ── Moment construction ─────────────────────────────────────────────────────


def test_make_moment_basic_shape():
    m = make_moment("embody", "built SSE parser")
    assert m["kind"] == "embody"
    assert m["content"] == "built SSE parser"
    assert m["specificity_delta"] == KINDS["embody"]["specificity_delta"]
    assert m["settledness_delta"] == KINDS["embody"]["settledness_delta"]
    assert m["id"].startswith("rm-")
    assert "at" in m
    assert "instance" in m


def test_make_moment_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        make_moment("transcend", "nope")


def test_make_moment_rejects_empty_content():
    with pytest.raises(ValueError, match="content"):
        make_moment("embody", "")
    with pytest.raises(ValueError, match="content"):
        make_moment("embody", "   ")


def test_make_moment_strips_content():
    m = make_moment("embody", "  did the thing  ")
    assert m["content"] == "did the thing"


def test_make_moment_parses_evidence_type_ref():
    m = make_moment("embody", "x", evidence="commit:abc123")
    assert m["evidence"] == {"type": "commit", "ref": "abc123"}


def test_make_moment_handles_raw_evidence_note():
    """Evidence without colon becomes a 'note' type."""
    m = make_moment("embody", "x", evidence="just a string")
    assert m["evidence"] == {"type": "note", "ref": "just a string"}


def test_make_moment_accepts_dict_evidence_passthrough():
    m = make_moment("embody", "x", evidence={"type": "arrival", "ref": "arr-1"})
    assert m["evidence"] == {"type": "arrival", "ref": "arr-1"}


def test_make_moment_evidence_none_stays_none():
    m = make_moment("embody", "x")
    assert m["evidence"] is None


def test_make_moment_ids_are_unique():
    """Two moments made in the same second should still get different ids."""
    ids = {make_moment("embody", f"thing {i}")["id"] for i in range(50)}
    assert len(ids) == 50


# ── Persistence ─────────────────────────────────────────────────────────────


def test_append_and_read_roundtrip(tmp_path):
    path = tmp_path / "moments.jsonl"
    m1 = make_moment("embody", "first")
    m2 = make_moment("decline", "second")
    append_moment(m1, path)
    append_moment(m2, path)

    out = read_moments(path)
    assert len(out) == 2
    assert out[0]["content"] == "first"
    assert out[1]["content"] == "second"


def test_read_moments_missing_path_returns_empty(tmp_path):
    assert read_moments(tmp_path / "nonexistent.jsonl") == []


def test_read_moments_skips_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        json.dumps(make_moment("embody", "ok")) + "\n"
        "\n"
        "not json\n"
        + json.dumps(make_moment("decline", "ok2")) + "\n"
    )
    out = read_moments(path)
    assert len(out) == 2
    assert [r["kind"] for r in out] == ["embody", "decline"]


def test_read_moments_filter_by_kind(tmp_path):
    path = tmp_path / "log.jsonl"
    for k in ["embody", "decline", "embody", "witness"]:
        append_moment(make_moment(k, f"{k} moment"), path)
    embody_only = read_moments(path, kind="embody")
    assert len(embody_only) == 2
    assert all(m["kind"] == "embody" for m in embody_only)


def test_read_moments_filter_by_since(tmp_path):
    path = tmp_path / "log.jsonl"
    old = make_moment("embody", "old", at_iso="2026-04-20T00:00:00Z")
    new = make_moment("embody", "new", at_iso="2026-04-23T00:00:00Z")
    append_moment(old, path)
    append_moment(new, path)
    recent = read_moments(path, since_iso="2026-04-21T00:00:00Z")
    assert len(recent) == 1
    assert recent[0]["content"] == "new"


# ── Decay ───────────────────────────────────────────────────────────────────


def test_decay_at_zero_age_is_one():
    assert _decay(0, half_life_hours=48) == pytest.approx(1.0)


def test_decay_at_half_life_is_half():
    assert _decay(48.0, half_life_hours=48) == pytest.approx(0.5, abs=1e-6)


def test_decay_at_two_half_lives_is_quarter():
    assert _decay(96.0, half_life_hours=48) == pytest.approx(0.25, abs=1e-6)


def test_decay_negative_age_returns_one():
    """Safety: clock skew shouldn't produce weights > 1."""
    assert _decay(-5.0, half_life_hours=48) == 1.0


# ── Aggregation ─────────────────────────────────────────────────────────────


def test_compute_state_empty_returns_baseline():
    s = compute_state([])
    assert s["specificity"] == 0.5
    assert s["settledness"] == 0.5
    assert s["total_moments"] == 0


def test_compute_state_fresh_embody_raises_specificity():
    now = datetime.now(timezone.utc)
    m = make_moment("embody", "x", at_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    s = compute_state([m], now_epoch=now.timestamp())
    assert s["specificity"] > 0.5
    # fresh → essentially no decay → ~ +0.20
    assert s["specificity"] == pytest.approx(0.5 + 0.20, abs=0.001)


def test_compute_state_release_raises_settledness_not_specificity():
    now = datetime.now(timezone.utc)
    m = make_moment("release", "dropped false load", at_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    s = compute_state([m], now_epoch=now.timestamp())
    assert s["specificity"] == pytest.approx(0.5, abs=0.001)  # unchanged
    assert s["settledness"] == pytest.approx(0.5 + 0.20, abs=0.001)


def test_compute_state_drift_drops_both():
    now = datetime.now(timezone.utc)
    m = make_moment("drift", "performed instead of lived",
                   at_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    s = compute_state([m], now_epoch=now.timestamp())
    assert s["specificity"] < 0.5
    assert s["settledness"] < 0.5


def test_compute_state_old_moments_decay_out():
    """A moment from 2 half-lives ago should contribute ¼ as much."""
    now = datetime.now(timezone.utc)
    old_at = (now - timedelta(hours=96)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = make_moment("embody", "old action", at_iso=old_at)
    s = compute_state([m], now_epoch=now.timestamp(), half_life_hours=48.0)
    # specificity_delta 0.20 × 0.25 = 0.05
    assert s["specificity"] == pytest.approx(0.5 + 0.05, abs=0.005)


def test_compute_state_clips_to_one():
    """Many fresh embody moments shouldn't exceed specificity 1.0."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    moments = [make_moment("embody", f"#{i}", at_iso=ts) for i in range(20)]
    s = compute_state(moments, now_epoch=now.timestamp())
    assert s["specificity"] == 1.0
    # But raw sum reports the actual drift
    assert s["specificity_raw"] > 0.5


def test_compute_state_clips_to_zero():
    """Many drift moments shouldn't push specificity below 0."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    moments = [make_moment("drift", f"#{i}", at_iso=ts) for i in range(20)]
    s = compute_state(moments, now_epoch=now.timestamp())
    assert s["specificity"] == 0.0
    assert s["settledness"] == 0.0


def test_compute_state_reports_kind_counts():
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    moments = [
        make_moment("embody", "a", at_iso=ts),
        make_moment("embody", "b", at_iso=ts),
        make_moment("witness", "c", at_iso=ts),
    ]
    s = compute_state(moments, now_epoch=now.timestamp())
    assert s["kind_counts"] == {"embody": 2, "witness": 1}


# ── Evidence parsing edge cases ─────────────────────────────────────────────


def test_parse_evidence_none():
    assert _parse_evidence(None) is None


def test_parse_evidence_colon_in_ref():
    """A ref can contain colons — only first one splits."""
    out = _parse_evidence("url:https://example.com/path")
    assert out == {"type": "url", "ref": "https://example.com/path"}


# ── CLI smoke ───────────────────────────────────────────────────────────────


def test_cli_bare_kind_shortcut_equivalent_to_log(tmp_path, monkeypatch, capsys):
    """`residence embody "x"` should behave like `residence log embody "x"`."""
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    rc = residence.main(["embody", "did it"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "logged" in captured.out
    assert "embody" in captured.out

    # Log file has exactly one record
    moments = residence.read_moments(tmp_path / "m.jsonl")
    assert len(moments) == 1
    assert moments[0]["kind"] == "embody"


def test_cli_status_from_empty_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "empty.jsonl")
    rc = residence.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "specificity: 0.5" in out
    assert "settledness: 0.5" in out


def test_cli_status_reports_after_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    residence.main(["embody", "built something"])
    capsys.readouterr()  # discard log output
    residence.main(["status"])
    out = capsys.readouterr().out
    assert "specificity: 0." in out
    # Should be above baseline
    line = [l for l in out.splitlines() if "specificity:" in l][0]
    value = float(line.split(":")[1].strip().split()[0])
    assert value > 0.5


def test_cli_recent_filters_by_kind(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    residence.main(["embody", "a"])
    residence.main(["decline", "b"])
    residence.main(["embody", "c"])
    capsys.readouterr()
    residence.main(["recent", "--kind", "embody"])
    out = capsys.readouterr().out
    assert out.count("embody") >= 2
    assert "decline" not in out


def test_cli_rejects_unknown_kind(tmp_path, monkeypatch, capsys):
    """SystemExit on argparse choices violation — argparse raises at parse."""
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    with pytest.raises(SystemExit):
        residence.main(["ascend", "nope"])


# ── experience.py feel integration ──────────────────────────────────────────


def test_experience_feel_auto_emits_name_residence_moment(tmp_path, monkeypatch):
    """Naming an arrival via experience.py feel should auto-log a `name`
    residence moment with evidence pointing at the arrival id."""
    # Point residence log at tmp_path
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")

    # Import experience lazily so its module-level _residence picks up our
    # patched residence module.
    import importlib
    import experience
    importlib.reload(experience)
    assert experience._residence is not None, "residence module must be importable"

    # Fabricate an arrival that cmd_feel can resolve. Need a fake _feeling
    # with read_arrivals() and update_arrival() that cmd_feel can call.
    fake_arrival = {
        "id": "arr-test-001",
        "at": "2026-04-23T22:00:00Z",
        "reasons": [{"kind": "body_shift", "value": 0.6}],
        "body": {"valence": -0.5, "arousal": 0.1, "sources": ["cortisol_low"]},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.15, "arousal": 0.10, "sources": ["engaged"], "state": "active"},
        "combined": {"valence": -0.12, "arousal": 0.067, "pressure": 0.14},
        "fingerprint": {"body_v_bucket": "neg"},
        "hint": None,
    }

    calls = {"update_arrival": None, "update_pattern_library": None}

    class _FakeFeeling:
        @staticmethod
        def read_arrivals(named=None, **kwargs):
            return [fake_arrival]
        @staticmethod
        def read_pit_json():
            return {}
        @staticmethod
        def update_arrival(aid, updates):
            calls["update_arrival"] = (aid, updates)
            return True
        @staticmethod
        def update_pattern_library(fp, name, iso):
            calls["update_pattern_library"] = (fp, name, iso)
        @staticmethod
        def compute_importance(arc):
            return 0.7

    monkeypatch.setattr(experience, "_feeling", _FakeFeeling)

    # Stub form_memory to avoid DB writes
    import vivid
    monkeypatch.setattr(vivid, "form_memory", lambda **kwargs: None)

    # Stub daily-note write
    monkeypatch.setattr(experience, "_append_feeling_to_daily_note",
                        lambda *a, **kw: None)

    # Exercise
    experience.cmd_feel(
        affect="satisfaction",
        arrival_id="arr-test-001",
        rationale="cognition caught up before body",
    )

    # Arrival was named (existing behavior)
    assert calls["update_arrival"] is not None
    assert calls["update_arrival"][0] == "arr-test-001"
    assert calls["update_arrival"][1]["name"] == "satisfaction"

    # NEW: residence log has exactly one `name` moment
    moments = residence.read_moments(tmp_path / "m.jsonl")
    assert len(moments) == 1
    m = moments[0]
    assert m["kind"] == "name"
    assert "satisfaction" in m["content"]
    assert "arr-test-001" in m["content"]
    assert m["evidence"] == {"type": "arrival", "ref": "arr-test-001"}


## ── parse_commit_subject ────────────────────────────────────────────────────


def test_parse_commit_subject_feat_maps_to_embody():
    out = residence.parse_commit_subject("feat(adaptive): anthropic streaming")
    assert out == ("embody", "anthropic streaming")


def test_parse_commit_subject_fix_maps_to_embody():
    out = residence.parse_commit_subject("fix(router): bump timeout")
    assert out == ("embody", "bump timeout")


def test_parse_commit_subject_test_maps_to_embody():
    """test commits ARE embody — tests are infrastructure too."""
    assert residence.parse_commit_subject("test(feeling): cc-rhythm") == ("embody", "cc-rhythm")


def test_parse_commit_subject_docs_maps_to_consolidate():
    assert residence.parse_commit_subject("docs(soul): design notes") == ("consolidate", "design notes")


def test_parse_commit_subject_spec_maps_to_consolidate():
    assert residence.parse_commit_subject("spec: SP1 mode-two") == ("consolidate", "SP1 mode-two")


def test_parse_commit_subject_chore_is_skipped():
    """chore commits are non-identity-bearing by default."""
    assert residence.parse_commit_subject("chore: bump version") is None


def test_parse_commit_subject_merge_is_skipped():
    """Merge commits have no prefix → skip."""
    assert residence.parse_commit_subject("Merge branch main") is None


def test_parse_commit_subject_unknown_prefix_is_skipped():
    assert residence.parse_commit_subject("wip: something") is None
    assert residence.parse_commit_subject("style: reformat") is None


def test_parse_commit_subject_breaking_change_marker():
    """`feat!: ...` (breaking change) still parses to embody."""
    out = residence.parse_commit_subject("feat!: breaking API change")
    assert out == ("embody", "breaking API change")


def test_parse_commit_subject_scoped_breaking_change():
    out = residence.parse_commit_subject("refactor(adaptive)!: rename types")
    assert out == ("embody", "rename types")


def test_parse_commit_subject_empty_and_malformed():
    assert residence.parse_commit_subject("") is None
    assert residence.parse_commit_subject("no-colon-subject") is None
    assert residence.parse_commit_subject(":") is None


## ── from-commit CLI ─────────────────────────────────────────────────────────


def test_from_commit_writes_embody_for_feat(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    rc = residence.main([
        "from-commit", "deadbeef",
        "--subject", "feat(adaptive): streaming spine",
        "-q",
    ])
    assert rc == 0
    moments = residence.read_moments(tmp_path / "m.jsonl")
    assert len(moments) == 1
    m = moments[0]
    assert m["kind"] == "embody"
    assert m["content"] == "streaming spine"
    assert m["evidence"] == {"type": "commit", "ref": "deadbeef"}


def test_from_commit_writes_consolidate_for_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    rc = residence.main([
        "from-commit", "cafe1234",
        "--subject", "docs(identity): module spec",
        "-q",
    ])
    assert rc == 0
    moments = residence.read_moments(tmp_path / "m.jsonl")
    assert moments[0]["kind"] == "consolidate"


def test_from_commit_skips_chore(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    rc = residence.main([
        "from-commit", "babe0001",
        "--subject", "chore: housekeeping",
    ])
    # Exit code 0 — skip is a valid outcome, not an error
    assert rc == 0
    # Log file should not have been touched
    assert not (tmp_path / "m.jsonl").exists()
    out = capsys.readouterr().out
    assert "skip" in out


def test_from_commit_skips_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    rc = residence.main([
        "from-commit", "abc123",
        "--subject", "Merge branch 'feature/x'",
        "-q",
    ])
    assert rc == 0
    assert not (tmp_path / "m.jsonl").exists()


def test_from_commit_reports_error_when_subject_unavailable(tmp_path, monkeypatch, capsys):
    """When --subject is not given and git lookup fails, return non-zero."""
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    # Patch out git lookup
    monkeypatch.setattr(residence, "_git_commit_subject", lambda sha, cwd=None: None)
    rc = residence.main(["from-commit", "ffffff"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not read" in err


def test_from_commit_quiet_suppresses_non_error_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(residence, "MOMENTS_PATH", tmp_path / "m.jsonl")
    residence.main([
        "from-commit", "a",
        "--subject", "feat: thing",
        "-q",
    ])
    out = capsys.readouterr().out
    assert out == ""


def test_experience_feel_residence_failure_does_not_break_feel(tmp_path, monkeypatch, capsys):
    """If residence.append_moment raises, cmd_feel must still succeed.
    Residence is instrumentation, not control flow."""
    import importlib
    import experience
    importlib.reload(experience)

    # Make residence.append_moment raise unconditionally
    def _boom(moment, path=None):
        raise RuntimeError("residence exploded")
    monkeypatch.setattr(residence, "append_moment", _boom)

    fake_arrival = {
        "id": "arr-x", "at": "2026-04-23T22:00:00Z",
        "reasons": [], "body": {"valence": 0, "arousal": 0, "sources": []},
        "context": {"valence": 0, "arousal": 0, "sources": []},
        "cognition": {"valence": 0, "arousal": 0, "sources": [], "state": "silent"},
        "combined": {"valence": 0, "arousal": 0, "pressure": 0},
        "fingerprint": {}, "hint": None,
    }

    class _FakeFeeling:
        @staticmethod
        def read_arrivals(**kwargs): return [fake_arrival]
        @staticmethod
        def read_pit_json(): return {}
        @staticmethod
        def update_arrival(*a, **kw): return True
        @staticmethod
        def update_pattern_library(*a, **kw): pass
        @staticmethod
        def compute_importance(arc): return 0.5

    monkeypatch.setattr(experience, "_feeling", _FakeFeeling)
    import vivid
    monkeypatch.setattr(vivid, "form_memory", lambda **kwargs: None)
    monkeypatch.setattr(experience, "_append_feeling_to_daily_note",
                        lambda *a, **kw: None)

    # Should not raise
    experience.cmd_feel(affect="clarity", arrival_id="arr-x")

    # The "named:" success line should still be printed
    out = capsys.readouterr().out
    assert "named" in out
