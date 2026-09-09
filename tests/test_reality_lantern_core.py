"""固定模板、metadata 同證據投影嘅反證；全部只用 temporary HOME。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from reality_lantern import core
from reality_lantern.core import (RealityError, build_status, keyed_digest, render_brief,
                                  status_fingerprint, validate_snapshot, validate_state)
from reality_lantern.store import Store

SCOPE = "a" * 64
CHECK = "b" * 64


def snapshot(**changes):
    result = {"schema": core.SCHEMA_VERSION, "scope_id": SCOPE, "cwd_id": "c" * 64,
              "worktree_id": "d" * 64, "fingerprint": "e" * 64, "complete": True,
              "observed_at": "2026-09-07T10:00:00.000000Z", "diagnostics": [],
              "coverage": {"paths": 3, "bytes": 42, "ignored": "excluded",
                           "environment": "unverified", "remote": "unverified"}}
    result.update(changes)
    return result


def evidence(tmp_path):
    store = Store(home=tmp_path.resolve())
    run = store.start_run(kind="tests", check_id=CHECK, before=snapshot())
    store.finish_run(run["id"], outcome="exit0", exit_code=0, signal_number=None, after=snapshot())
    return store, run


def test_now_is_strict_utc_and_hash_is_keyed_canonical():
    value = core.utc_now()
    assert core.timestamp(value).tzinfo == timezone.utc
    assert value.endswith("Z")
    assert keyed_digest(b"k" * 32, "check", {"a": 1, "b": 2}) == keyed_digest(b"k" * 32, "check", {"b": 2, "a": 1})
    hashes = {keyed_digest(key, domain, ["RAW-ARGV", "RAW-PATH"])
              for key in (b"k" * 32, b"j" * 32) for domain in ("check", "path")}
    assert len(hashes) == 4 and all(len(item) == 64 for item in hashes)


def test_large_snapshot_digest_is_bounded_independently_of_state():
    records = [[f"synthetic/{index:05d}", "file", 420, "e" * 64] for index in range(10000)]
    assert len(json.dumps(records).encode("utf-8")) > 256 * 1024
    value = keyed_digest(b"k" * 32, "snapshot", records)
    assert len(value) == 64


@pytest.mark.parametrize("key,domain,value", [(b"k", "check", []), (b"k" * 32, "RAW DOMAIN", []),
    (b"k" * 32, "check", float("nan")), (b"k" * 32, "check", float("inf")),
    (b"k" * 32, "check", {"a": object()})])
def test_hash_rejects_malformed_inputs(key, domain, value):
    with pytest.raises(RealityError):
        keyed_digest(key, domain, value)


@pytest.mark.parametrize("change", [
    {"argv": ["SECRET-CANARY"]}, {"schema": "SECRET-CANARY"}, {"scope_id": "/raw/path"},
    {"cwd_id": "A" * 64}, {"fingerprint": "e" * 63}, {"worktree_id": None},
    {"complete": 1}, {"diagnostics": ["safe_looking_secret"]}, {"diagnostics": ["not_git"]},
    {"observed_at": "2026-02-30T10:00:00Z"}, {"observed_at": "2026-09-07"},
    {"observed_at": "2026-09-07T10:00:00+01:00"}, {"observed_at": "2026-09-07T10:00:60Z"},
    {"observed_at": "2026-09-07T10:00:00Z\nSECRET-CANARY"},
    {"complete": False}, {"complete": False, "fingerprint": None, "diagnostics": []},
])
def test_snapshot_metadata_is_whitelisted(change):
    with pytest.raises(RealityError, match="invalid_input"):
        validate_snapshot(snapshot(**change))


@pytest.mark.parametrize("name,value", [("paths", True), ("paths", 10001), ("paths", -1),
    ("bytes", float("nan")), ("bytes", float("inf")), ("bytes", 64 * 1024 * 1024 + 1),
    ("remote", "verified"), ("environment", "SECRET-CANARY"), ("ignored", "included"),
    ("path", "SECRET-CANARY")])
def test_snapshot_coverage_rejects_nonfinite_and_unproven(name, value):
    item = snapshot()
    item["coverage"][name] = value
    with pytest.raises(RealityError):
        validate_snapshot(item)


def test_valid_incomplete_snapshot_and_safe_error():
    validate_snapshot(snapshot(complete=False, fingerprint=None, worktree_id=None, diagnostics=["not_git"]))
    assert str(RealityError("/raw/SECRET-CANARY")) == "invalid_input"
    assert str(RealityError({})) == "invalid_input"


def test_empty_and_unrelated_scope_never_borrow_green(tmp_path):
    store, _ = evidence(tmp_path)
    state = store.read_state()
    for status in (build_status(None), build_status(state), build_status(state, scope_id="f" * 64)):
        assert not status["checks"]
        assert "unknown" in render_brief(status)
        assert "exit 0" not in render_brief(status)
    status = build_status(state, scope_id=SCOPE)
    assert status["checks"][0]["outcome"] == "exit0"
    assert status["checks"][0]["validity"] == "match"
    assert status["environment"] == status["remote"] == "unverified"
    assert "match@2026-09-07T10:00:00.000000Z" in render_brief(status)
    assert "tests passed" not in render_brief(status)


def test_failed_outcome_can_match_without_claiming_pass(tmp_path):
    store = Store(home=tmp_path.resolve())
    run = store.start_run(kind="build", check_id=CHECK, before=snapshot())
    store.finish_run(run["id"], outcome="nonzero", exit_code=7, signal_number=None, after=snapshot())
    status = build_status(store.read_state(), scope_id=SCOPE)
    assert status["checks"][0]["validity"] == "match"
    assert "nonzero" in render_brief(status) and "exit 0" not in render_brief(status)


@pytest.mark.parametrize("max_bytes", [0, 1, 64, 128, 256, 512, 768, 4096])
def test_brief_is_bounded_utf8_and_fixed_templates(tmp_path, max_bytes):
    store, _ = evidence(tmp_path)
    text = render_brief(build_status(store.read_state(), scope_id=SCOPE), max_bytes=max_bytes)
    assert len(text.encode("utf-8")) <= max_bytes
    if text:
        assert "線上未驗證" in text


@pytest.mark.parametrize("mutation", [
    lambda s: s.update({"argv": "SECRET-CANARY"}),
    lambda s: s["checks"][0].update({"kind": "SECRET-CANARY"}),
    lambda s: s["checks"][0].update({"compared_at": "SECRET-CANARY"}),
    lambda s: s["checks"][0].update({"outcome": "SECRET-CANARY"}),
    lambda s: s["checks"][0].update({"validity": "SECRET-CANARY"}),
    lambda s: s["diagnostics"].append("SECRET-CANARY"),
    lambda s: s.update({"environment": "SECRET-CANARY"}),
])
def test_brief_does_not_render_raw_untrusted_fields(tmp_path, mutation):
    store, _ = evidence(tmp_path)
    status = build_status(store.read_state(), scope_id=SCOPE)
    mutation(status)
    text = render_brief(status)
    assert "SECRET-CANARY" not in text and "exit 0" not in text
    assert len(status_fingerprint(status)) == 64


def test_material_fingerprint_ignores_asof_but_not_new_pending(tmp_path):
    store, _ = evidence(tmp_path)
    first = build_status(store.read_state(), scope_id=SCOPE)
    store.observe(snapshot(observed_at="2026-09-07T11:00:00Z"), expected_generation=0)
    second = build_status(store.read_state(), scope_id=SCOPE)
    assert first["checks"][0]["compared_at"] != second["checks"][0]["compared_at"]
    assert status_fingerprint(first) == status_fingerprint(second)
    store.start_run(kind="tests", check_id=CHECK, before=snapshot(observed_at="2026-09-07T12:00:00Z"))
    assert status_fingerprint(second) != status_fingerprint(build_status(store.read_state(), scope_id=SCOPE))


def test_malformed_state_fails_closed_for_claims_not_raw_output(tmp_path):
    store, run = evidence(tmp_path)
    state = store.read_state()
    state["projections"][run["id"]]["compared_at"] = "SECRET-CANARY"
    status = build_status(state, scope_id=SCOPE)
    assert status["diagnostics"] == ["state_corrupt"] and not status["checks"]
    assert "SECRET-CANARY" not in json.dumps(status)


@pytest.mark.parametrize("mutate", [
    lambda s, r: s.update(next_sequence=float("inf")),
    lambda s, r: s["scopes"][SCOPE].update(generation=True),
    lambda s, r: s["runs"][r["id"]].update(id="not-a-uuid"),
    lambda s, r: s["runs"][r["id"]].update(check_id="SECRET-CANARY"),
    lambda s, r: s["projections"][r["id"]].update(generation=42),
    lambda s, r: s["scopes"][SCOPE].update(observation=None),
    lambda s, r: s["completions"][r["id"]].update(exit_code=False),
    lambda s, r: s["completions"][r["id"]].update(after=snapshot(fingerprint="f" * 64)),
])
def test_cross_field_state_validation_prevents_forged_match(tmp_path, mutate):
    store, run = evidence(tmp_path)
    state = store.read_state()
    mutate(state, run)
    with pytest.raises(RealityError):
        validate_state(state)
    assert not build_status(state, scope_id=SCOPE)["checks"]
