"""歷史反證以觀察時點判斷；CAS token 舊唔等於反證可以丟棄。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from reality_lantern import store as store_module
from reality_lantern.core import SCHEMA_VERSION
from reality_lantern.store import Store

SCOPE = "a" * 64
CHECK = "b" * 64


def stamp(tick):
    value = datetime(2026, 9, 8, tzinfo=timezone.utc) + timedelta(seconds=tick)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def snapshot(version, tick):
    return {
        "schema": SCHEMA_VERSION,
        "scope_id": SCOPE,
        "worktree_id": "c" * 64,
        "cwd_id": "d" * 64,
        "fingerprint": version * 64,
        "complete": True,
        "observed_at": stamp(tick),
        "diagnostics": [],
        "coverage": {
            "paths": 1,
            "bytes": 1,
            "ignored": "excluded",
            "environment": "unverified",
            "remote": "unverified",
        },
    }


def clock(monkeypatch, tick):
    monkeypatch.setattr(store_module, "utc_now", lambda: stamp(tick))


def start(store, monkeypatch, tick):
    clock(monkeypatch, tick)
    return store.start_run(kind="tests", check_id=CHECK, before=snapshot("1", tick - 1))


def finish(store, monkeypatch, run, tick):
    clock(monkeypatch, tick)
    return store.finish_run(
        run["id"], outcome="exit0", exit_code=0, signal_number=None,
        after=snapshot("1", tick - 1),
    )


@pytest.fixture
def local_store(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    store = Store(home=home)
    store.get_key(create=True)
    return store


@pytest.mark.parametrize("observation_tick, expected", [(25, "match"), (35, "stale")])
def test_same_epoch_old_cas_keeps_applicable_negative_evidence(
    local_store, monkeypatch, observation_tick, expected,
):
    store = local_store
    old = start(store, monkeypatch, 10)
    finish(store, monkeypatch, old, 20)
    old_generation = store.read_state()["scopes"][SCOPE]["generation"]

    # Observe 已經攞咗舊 CAS token；新一輪 Bash 先 invalidate，再起新 run。
    store.invalidate(SCOPE, changed=False)
    fresh = start(store, monkeypatch, 30)
    different = snapshot("2", observation_tick)
    finish(store, monkeypatch, fresh, 40)
    current = store.read_state()
    assert current["projections"][fresh["id"]]["validity"] == "match"
    assert current["scopes"][SCOPE]["generation"] != old_generation

    result = store.observe(different, expected_generation=old_generation)
    assert result["ok"] is False
    assert "generation_changed" in result["diagnostics"]
    current = store.read_state()
    assert current["scopes"][SCOPE]["observation"] == snapshot("1", 39)
    assert current["projections"][old["id"]]["validity"] == "stale"
    # 新 run 期間嘅完整差異係真反證；只有早於新 run 嘅差異先唔適用。
    assert current["projections"][fresh["id"]]["validity"] == expected

    generation = current["scopes"][SCOPE]["generation"]
    assert store.observe(snapshot("1", 45), expected_generation=generation)["ok"]
    current = store.read_state()
    assert current["projections"][old["id"]]["validity"] == "stale"
    assert current["projections"][fresh["id"]]["validity"] == expected


@pytest.mark.parametrize("observation_tick, expected", [(15, "match"), (65, "stale")])
def test_rotation_old_cas_cannot_upgrade_but_keeps_applicable_counterevidence(
    local_store, monkeypatch, observation_tick, expected,
):
    store = local_store
    old = start(store, monkeypatch, 10)
    finish(store, monkeypatch, old, 20)
    previous = store.read_state()
    old_generation = previous["scopes"][SCOPE]["generation"]
    key = store.get_key(create=False)

    clock(monkeypatch, 40)
    store.rotate(archive=True)
    assert not store.read_state()["runs"]
    assert store.get_key(create=False) == key

    fresh = start(store, monkeypatch, 60)
    finish(store, monkeypatch, fresh, 70)
    current = store.read_state()
    assert fresh["sequence"] > old["sequence"]
    assert current["scopes"][SCOPE]["generation"] > old_generation

    different = snapshot("2", observation_tick)
    result = store.observe(different, expected_generation=old_generation)
    assert result["ok"] is False
    assert "generation_changed" in result["diagnostics"]
    current = store.read_state()
    assert current["scopes"][SCOPE]["observation"] == snapshot("1", 69)
    assert current["projections"][fresh["id"]]["validity"] == expected

    # 舊 token 即使帶新鮮 A snapshot，亦唔可以升級到 match。
    store.invalidate(SCOPE, changed=False)
    before = store.read_state()["projections"][fresh["id"]]["validity"]
    result = store.observe(snapshot("1", 75), expected_generation=old_generation)
    assert result["ok"] is False
    assert store.read_state()["projections"][fresh["id"]]["validity"] == before
