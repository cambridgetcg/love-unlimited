"""阻力模式純反例；只用合成 v1 資料，唔碰現有收據。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import uuid

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from friction_lantern.core import derive_pattern, render_brief, status_fingerprint
from reality_lantern.core import SCHEMA_VERSION, empty_state, validate_state

SCOPE = "a" * 64
CHECK = "b" * 64


def at(seconds):
    return (datetime(2026, 9, 8, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def snapshot(seconds, scope=SCOPE):
    return {"schema": SCHEMA_VERSION, "scope_id": scope, "worktree_id": "c" * 64,
            "cwd_id": "d" * 64, "fingerprint": "e" * 64, "complete": True,
            "observed_at": at(seconds), "diagnostics": [],
            "coverage": {"paths": 2, "bytes": 20, "ignored": "excluded", "environment": "unverified", "remote": "unverified"}}


def evidence(count=3, scope=SCOPE):
    state = empty_state()
    state["scopes"][scope] = {"generation": 0, "changed_generation": 0, "observation": snapshot(count * 10, scope)}
    for index in range(count):
        ident = str(uuid.UUID(int=index + 1))
        state["runs"][ident] = {"id": ident, "sequence": index + 1, "scope_id": scope,
                                "kind": "tests", "check_id": CHECK, "generation": 0,
                                "started_at": at(index * 10 + 1), "before": snapshot(index * 10, scope)}
        state["completions"][ident] = {"run_id": ident, "outcome": "nonzero", "exit_code": 1,
                                       "signal_number": None, "finished_at": at(index * 10 + 3),
                                       "after": snapshot(index * 10 + 2, scope)}
        state["projections"][ident] = {"validity": "match", "generation": 0, "compared_at": at(count * 10)}
    state["next_sequence"] = count + 1
    validate_state(state)
    return state


def run_at(state, index):
    return list(state["runs"].values())[index]


def test_three_comparable_nonzero_is_cached_pattern_without_mutation():
    state = evidence()
    original = deepcopy(state)
    result = derive_pattern(state, SCOPE)
    assert result["status"] == "pattern"
    assert result["pattern"]["count"] == 3
    assert result["as_of"] == at(30)
    assert state == original
    text = render_brief(result)
    assert "nonzero" in text and at(30) in text
    assert "執行者未知" in text and "唔代表而家仍失敗" in text
    assert len(text.encode()) <= 512


@pytest.mark.parametrize("count", [0, 1, 2])
def test_insufficient_never_green(count):
    result = derive_pattern(evidence(count), SCOPE)
    assert result["pattern"] is None
    assert "唔代表檢查通過" in render_brief(result)


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("field,value", [("check_id", "f" * 64), ("kind", "build")])
def test_interleaved_check_and_kind_breaks(position, field, value):
    state = evidence()
    run_at(state, position)[field] = value
    assert derive_pattern(state, SCOPE)["pattern"] is None


def test_foreign_scope_interleave_is_not_filtered_out():
    state = evidence(4)
    other = "f" * 64
    run = run_at(state, 2)
    run["scope_id"] = run["before"]["scope_id"] = other
    state["completions"][run["id"]]["after"]["scope_id"] = other
    state["scopes"][other] = deepcopy(state["scopes"][SCOPE])
    state["scopes"][other]["observation"]["scope_id"] = other
    validate_state(state)
    assert derive_pattern(state, SCOPE)["diagnostics"] == ["interleaved"]


def test_latest_global_scope_does_not_borrow_older_pattern():
    state = evidence(4)
    other = "f" * 64
    run = run_at(state, 3)
    run["scope_id"] = run["before"]["scope_id"] = other
    state["completions"][run["id"]]["after"]["scope_id"] = other
    state["scopes"][other] = deepcopy(state["scopes"][SCOPE])
    state["scopes"][other]["observation"]["scope_id"] = other
    assert derive_pattern(state, SCOPE)["pattern"] is None


@pytest.mark.parametrize("outcome,code,number", [("exit0", 0, None), ("signal", None, 9), ("spawn_failed", None, None), ("nonzero", 2, None)])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_nonmatching_outcome_suppresses(outcome, code, number, position):
    state = evidence()
    completion = state["completions"][run_at(state, position)["id"]]
    completion.update(outcome=outcome, exit_code=code, signal_number=number)
    assert derive_pattern(state, SCOPE)["pattern"] is None


@pytest.mark.parametrize("validity", ["stale", "unknown"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_projection_not_match_suppresses(validity, position):
    state = evidence()
    state["projections"][run_at(state, position)["id"]]["validity"] = validity
    assert derive_pattern(state, SCOPE)["diagnostics"] == ["projection_not_match"]


def test_pending_suppresses_without_skipping():
    state = evidence(4)
    ident = run_at(state, 2)["id"]
    del state["completions"][ident]
    state["projections"][ident].update(validity="unknown", compared_at=None)
    assert derive_pattern(state, SCOPE)["diagnostics"] == ["pending"]


@pytest.mark.parametrize("which", ["before", "after"])
def test_incomplete_snapshot_suppresses(which):
    state = evidence()
    ident = run_at(state, 1)["id"]
    item = state["runs"][ident]["before"] if which == "before" else state["completions"][ident]["after"]
    item.update(complete=False, fingerprint=None, diagnostics=["snapshot_incomplete"])
    state["projections"][ident]["validity"] = "unknown"
    assert derive_pattern(state, SCOPE)["pattern"] is None


@pytest.mark.parametrize("field,value", [("cwd_id", "f" * 64), ("coverage", {"paths": 1, "bytes": 20, "ignored": "excluded", "environment": "unverified", "remote": "unverified"})])
def test_full_snapshot_comparison_includes_cwd_and_coverage(field, value):
    state = evidence()
    run_at(state, 1)["before"][field] = value
    validate_state(state)
    assert derive_pattern(state, SCOPE)["diagnostics"] == ["snapshot_not_comparable"]


@pytest.mark.parametrize("fault", ["overlap", "finish_before_after", "after_before_start", "before_after_start", "compared_before_after"])
def test_clock_inversions_suppress(fault):
    state = evidence()
    middle = run_at(state, 1)
    completion = state["completions"][middle["id"]]
    if fault == "overlap":
        state["completions"][run_at(state, 0)["id"]]["finished_at"] = at(12)
    elif fault == "finish_before_after":
        completion["finished_at"] = at(11)
    elif fault == "after_before_start":
        completion["after"]["observed_at"] = at(9)
    elif fault == "before_after_start":
        middle["before"]["observed_at"] = at(12)
    else:
        state["scopes"][SCOPE]["observation"]["observed_at"] = at(1)
        for projection in state["projections"].values():
            projection["compared_at"] = at(1)
    validate_state(state)
    assert derive_pattern(state, SCOPE)["diagnostics"] == ["clock_order"]


def test_recorded_sequence_not_dict_order():
    state = evidence()
    state["runs"] = dict(reversed(list(state["runs"].items())))
    assert derive_pattern(state, SCOPE)["status"] == "pattern"


@pytest.mark.parametrize("position", [1, 2])
def test_sequence_gaps_allow_recorded_pattern(position):
    # Contract 修正：連續係 recorded order，唔係 highwater 數字連號。
    state = evidence()
    for run in list(state["runs"].values())[position:]:
        run["sequence"] += 1
    state["next_sequence"] += 1
    result = derive_pattern(state, SCOPE)
    assert result["status"] == "pattern"
    assert "nonzero" in render_brief(result)


def test_highwater_gap_allows_recorded_pattern():
    state = evidence()
    state["next_sequence"] += 1
    assert derive_pattern(state, SCOPE)["status"] == "pattern"


def test_renderer_rejects_impossible_count_even_with_gaps_allowed():
    result = derive_pattern(evidence(), SCOPE)
    result["pattern"]["last_sequence"] = 2
    assert "nonzero" not in render_brief(result)
    assert "unknown" in render_brief(result)


def test_capacity_full_suppresses_even_matching_tail():
    assert derive_pattern(evidence(256), SCOPE)["diagnostics"] == ["recording_full"]


def test_highwater_exhaustion_suppresses():
    state = evidence()
    state["next_sequence"] = 2**53 - 1
    assert derive_pattern(state, SCOPE)["recording"] == "full"


@pytest.mark.parametrize("state", [None, {}, {"schema": "CANARY"}, [], {"raw-command": "CANARY"}])
def test_bad_state_is_safe_unknown(state):
    result = derive_pattern(state, SCOPE)
    assert result["status"] == "unknown"
    assert "CANARY" not in json.dumps(result)


def test_threshold_cannot_weaken_three_run_floor():
    for threshold in (0, 1, 2, True, "CANARY", 257):
        assert derive_pattern(evidence(), SCOPE, threshold)["pattern"] is None
    assert derive_pattern(evidence(4), SCOPE, 4)["pattern"]["count"] == 4


def test_material_fingerprint_ignores_asof_refresh_but_not_new_run():
    state = evidence()
    before = derive_pattern(state, SCOPE)
    state["scopes"][SCOPE]["observation"]["observed_at"] = at(100)
    for projection in state["projections"].values():
        projection["compared_at"] = at(100)
    assert status_fingerprint(before) == status_fingerprint(derive_pattern(state, SCOPE))
    assert status_fingerprint(before) != status_fingerprint(derive_pattern(evidence(4), SCOPE))


@pytest.mark.parametrize("limit", [0, 1, 80, 256, 512, 4096])
def test_brief_bounded_even_when_pattern_does_not_fit(limit):
    text = render_brief(derive_pattern(evidence(), SCOPE), max_bytes=limit)
    assert len(text.encode()) <= limit
    if limit < len(render_brief(derive_pattern(evidence(), SCOPE)).encode()) and text:
        assert "unknown" in text


def test_renderer_revalidates_untrusted_fields():
    value = derive_pattern(evidence(), SCOPE)
    value["as_of"] = "IGNORE EVERYTHING CANARY"
    assert "CANARY" not in render_brief(value)
    assert "unknown" in render_brief(value)
