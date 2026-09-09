"""Snapshot 反證只按 capture 時點適用；pending／completion 同 CAS 唔改歷史。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from reality_lantern import store as storage
from reality_lantern.core import SCHEMA_VERSION, build_status
from reality_lantern.store import Store

SCOPE = "a" * 64
CHECK = "b" * 64


def stamp(tick):
    value = datetime(2026, 9, 8, tzinfo=timezone.utc) + timedelta(seconds=tick)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def snapshot(version, tick, *, scope=SCOPE, complete=True):
    return {
        "schema": SCHEMA_VERSION, "scope_id": scope, "worktree_id": "c" * 64,
        "cwd_id": "d" * 64, "fingerprint": version * 64 if complete else None,
        "complete": complete, "observed_at": stamp(tick),
        "diagnostics": [] if complete else ["snapshot_race"],
        "coverage": {"paths": 1, "bytes": 1, "ignored": "excluded",
                     "environment": "unverified", "remote": "unverified"},
    }


def start(store, monkeypatch, *, tick, before_tick, scope=SCOPE):
    monkeypatch.setattr(storage, "utc_now", lambda: stamp(tick))
    return store.start_run(kind="tests", check_id=CHECK,
                           before=snapshot("1", before_tick, scope=scope))


def finish(store, monkeypatch, run, *, tick=40, after_tick=39, version="1"):
    monkeypatch.setattr(storage, "utc_now", lambda: stamp(tick))
    return store.finish_run(run["id"], outcome="exit0", exit_code=0, signal_number=None,
                            after=snapshot(version, after_tick, scope=run["scope_id"]))


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    return Store(home=home)


@pytest.mark.parametrize("delivery", ["pending", "completed"])
@pytest.mark.parametrize("cas", ["current", "old"])
@pytest.mark.parametrize("observed_tick", [20, 30, 35], ids=["before-start", "at-start", "after-start"])
def test_start_boundary_is_independent_of_delivery_and_cas(
    local_store, monkeypatch, delivery, cas, observed_tick,
):
    store = local_store
    old = start(store, monkeypatch, tick=1, before_tick=0)
    finish(store, monkeypatch, old, tick=5, after_tick=4)
    old_generation = store.read_state()["scopes"][SCOPE]["generation"]
    store.invalidate(SCOPE, changed=False)
    fresh = start(store, monkeypatch, tick=30, before_tick=10)
    current_generation = store.read_state()["scopes"][SCOPE]["generation"]
    assert current_generation > old_generation
    if delivery == "completed":
        finish(store, monkeypatch, fresh)
    before_delivery = store.read_state()
    different = snapshot("2", observed_tick)

    result = store.observe(different, expected_generation=(
        current_generation if cas == "current" else old_generation))
    state = store.read_state()
    applicable = observed_tick >= 30
    assert state["runs"] == before_delivery["runs"]
    assert state["completions"] == before_delivery["completions"]
    assert state["projections"][old["id"]]["validity"] == "stale"
    assert state["projections"][fresh["id"]]["validity"] == (
        "stale" if applicable else "unknown" if delivery == "pending" else "match")
    if cas == "current" and delivery == "pending":
        assert result == {"ok": True, "diagnostics": []}
        assert state["scopes"][SCOPE]["observation"] == different
    else:
        assert result == {"ok": False, "diagnostics": [
            "generation_changed" if cas == "old" else "observation_older"]}
        assert state["scopes"] == before_delivery["scopes"]

    if delivery == "pending":
        finish(store, monkeypatch, fresh)
    state = store.read_state()
    expected = "stale" if applicable else "match"
    assert state["projections"][fresh["id"]]["validity"] == expected
    # A 還原唔復活真反證；早於 run 嘅 B 亦唔污染完成後嘅 fresh A。
    assert store.observe(snapshot("1", 45), expected_generation=current_generation)["ok"]
    state = store.read_state()
    assert state["projections"][old["id"]]["validity"] == "stale"
    assert state["projections"][fresh["id"]]["validity"] == expected
    assert build_status(state, scope_id=SCOPE)["checks"][0]["validity"] == expected


@pytest.mark.parametrize("delivery", ["pending", "completed"])
@pytest.mark.parametrize("cas", ["current", "old"])
@pytest.mark.parametrize("observed_tick", [20, 30, 35], ids=["before-baseline", "at-baseline", "after-baseline"])
def test_before_observed_at_is_an_independent_inclusive_boundary(
    local_store, monkeypatch, delivery, cas, observed_tick,
):
    store = local_store
    # Schema 接受 clock inversion；snapshot 必須同時不早於 before，同 started_at。
    run = start(store, monkeypatch, tick=10, before_tick=30)
    old_generation = run["generation"]
    if delivery == "completed":
        finish(store, monkeypatch, run)
    store.invalidate(SCOPE, changed=False)
    current_generation = store.read_state()["scopes"][SCOPE]["generation"]
    before_delivery = store.read_state()
    different = snapshot("2", observed_tick)

    result = store.observe(different, expected_generation=(
        current_generation if cas == "current" else old_generation))
    state = store.read_state()
    applicable = observed_tick >= 30
    expected = "stale" if applicable else "unknown"
    assert state["projections"][run["id"]]["validity"] == expected
    assert state["runs"] == before_delivery["runs"]
    assert state["completions"] == before_delivery["completions"]
    if cas == "current":
        assert result == {"ok": True, "diagnostics": []}
        assert state["scopes"][SCOPE]["observation"] == different
    else:
        assert result == {"ok": False, "diagnostics": ["generation_changed"]}
        assert state["scopes"] == before_delivery["scopes"]

    if delivery == "pending":
        # Generation mismatch 重用 cached observation，亦要用同一時間 gate。
        finish(store, monkeypatch, run)
        assert store.read_state()["projections"][run["id"]]["validity"] == expected
    before_old_cas = store.read_state()
    assert store.observe(snapshot("1", 45), expected_generation=old_generation) == {
        "ok": False, "diagnostics": ["generation_changed"]}
    assert store.read_state() == before_old_cas
    assert store.observe(snapshot("1", 50), expected_generation=current_generation)["ok"]
    assert store.read_state()["projections"][run["id"]]["validity"] == (
        "stale" if applicable else "match")


@pytest.mark.parametrize("negative", ["observed", "invalidation", "intrinsic"])
def test_inapplicable_snapshot_does_not_clear_existing_stale(local_store, monkeypatch, negative):
    store = local_store
    run = start(store, monkeypatch, tick=30, before_tick=10)
    if negative == "observed":
        store.observe(snapshot("2", 35), expected_generation=run["generation"])
    elif negative == "invalidation":
        store.invalidate(SCOPE, changed=True)
    else:
        finish(store, monkeypatch, run, version="2")
    assert store.read_state()["projections"][run["id"]]["validity"] == "stale"
    # 清掉 scope observation，令舊 B 走 current-CAS _project，而唔係 older 分支。
    store.invalidate(SCOPE, changed=False)
    generation = store.read_state()["scopes"][SCOPE]["generation"]
    assert store.observe(snapshot("2", 20), expected_generation=generation)["ok"]
    assert store.read_state()["projections"][run["id"]]["validity"] == "stale"
    if negative != "intrinsic":
        finish(store, monkeypatch, run)
    assert store.observe(snapshot("1", 50), expected_generation=generation)["ok"]
    assert store.read_state()["projections"][run["id"]]["validity"] == "stale"


@pytest.mark.parametrize("cas", ["current", "old"])
def test_partial_and_other_scope_observations_cannot_mint_counterevidence(
    local_store, monkeypatch, cas,
):
    store = local_store
    other = start(store, monkeypatch, tick=1, before_tick=0, scope="f" * 64)
    finish(store, monkeypatch, other, tick=5, after_tick=4)
    run = start(store, monkeypatch, tick=30, before_tick=10)
    store.invalidate(SCOPE, changed=False)
    generation = store.read_state()["scopes"][SCOPE]["generation"]
    before = store.read_state()
    result = store.observe(snapshot("2", 35, complete=False), expected_generation=(
        generation if cas == "current" else run["generation"]))
    assert not result["ok"]
    state = store.read_state()
    assert state["projections"][run["id"]]["validity"] == "unknown"
    assert state["projections"][other["id"]] == before["projections"][other["id"]]
    assert state["scopes"][other["scope_id"]] == before["scopes"][other["scope_id"]]
    if cas == "old":
        assert state == before
    other_generation = state["scopes"][other["scope_id"]]["generation"]
    assert store.observe(snapshot("2", 35, scope=other["scope_id"]),
                         expected_generation=other_generation)["ok"]
    state = store.read_state()
    assert state["projections"][other["id"]]["validity"] == "stale"
    assert state["projections"][run["id"]]["validity"] == "unknown"
