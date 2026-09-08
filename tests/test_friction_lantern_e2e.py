"""真本地 runner／snapshot 接合，但只喺自建 tmp Git＋fake HOME。"""
import json
import os
import subprocess
import sys

import pytest

from .test_friction_lantern_core import CHECK, at, snapshot
from .test_friction_lantern_hooks import context, environment, file_tree, payload
from friction_lantern import api, hooks
from friction_lantern.core import derive_pattern, render_brief
from reality_lantern import store as reality_store
from reality_lantern.core import keyed_digest
from reality_lantern.runner import run_check
from reality_lantern.store import Store


def recorded_store(tmp_path, monkeypatch, gap):
    """真 Store start/finish/invalidate/observe；只替換 fixture clock/snapshot。"""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    (workspace / ".git").mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    store = Store(home=home)
    scope = keyed_digest(store.get_key(create=True), "path", str(workspace))
    for index in range(3):
        start = index * 10
        monkeypatch.setattr(reality_store, "utc_now", lambda: at(start + 1))
        run = store.start_run(kind="tests", check_id=CHECK, before=snapshot(start, scope))
        monkeypatch.setattr(reality_store, "utc_now", lambda: at(start + 3))
        store.finish_run(run["id"], outcome="nonzero", exit_code=1, signal_number=None,
                         after=snapshot(start + 2, scope))
        if (gap == "interior" and index == 0) or (gap == "trailing" and index == 2):
            for _ in range(3 if gap == "interior" else 4):
                assert store.invalidate(scope, changed=False)
            assert all(item["validity"] == "unknown" for item in store.read_state()["projections"].values())
            generation = store.read_state()["scopes"][scope]["generation"]
            assert store.observe(snapshot(start + 4, scope), expected_generation=generation)["ok"]
    return home, workspace, scope, store


@pytest.mark.parametrize("gap,sequences,next_sequence", [("interior", [1, 4, 5], 6), ("trailing", [1, 2, 3], 5)])
def test_store_highwater_gaps_preserve_recorded_adjacency(tmp_path, monkeypatch, gap, sequences, next_sequence):
    home, workspace, scope, store = recorded_store(tmp_path, monkeypatch, gap)
    state = store.read_state()
    assert sorted(run["sequence"] for run in state["runs"].values()) == sequences
    assert state["next_sequence"] == next_sequence
    assert all(item["validity"] == "match" for item in state["projections"].values())
    before = file_tree(store.state_dir)
    result = derive_pattern(state, scope)
    assert result["status"] == "pattern", result
    assert result["pattern"]["count"] == 3
    assert result["pattern"]["first_sequence"] == sequences[0]
    assert result["pattern"]["last_sequence"] == sequences[-1]
    assert "nonzero" in render_brief(result)
    assert api.status(home=home, cwd=workspace) == result
    assert "nonzero" in context(hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home))
    assert file_tree(store.state_dir) == before


@pytest.mark.parametrize("barrier", ["scope", "check", "pending"])
def test_store_highwater_gaps_do_not_skip_later_recorded_barriers(tmp_path, monkeypatch, barrier):
    home, workspace, scope, store = recorded_store(tmp_path, monkeypatch, "interior")
    monkeypatch.setattr(reality_store, "utc_now", lambda: at(31))
    other_scope = "f" * 64 if barrier == "scope" else scope
    run = store.start_run(kind="tests", check_id="f" * 64 if barrier == "check" else CHECK,
                          before=snapshot(30, other_scope))
    if barrier != "pending":
        monkeypatch.setattr(reality_store, "utc_now", lambda: at(33))
        store.finish_run(run["id"], outcome="nonzero", exit_code=1, signal_number=None,
                         after=snapshot(32, other_scope))
    before = file_tree(store.state_dir)
    result = api.status(home=home, cwd=workspace)
    assert result["pattern"] is None
    assert result["diagnostics"] == ["pending" if barrier == "pending" else "interleaved"]
    assert hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home) == {}
    assert file_tree(store.state_dir) == before


def test_three_explicit_fixture_checks_then_cached_pattern(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    env = environment(home)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "init", "-q", str(workspace)],
                   check=True, capture_output=True, env=env, cwd=workspace, timeout=3)
    (workspace / "example.txt").write_text("fixture\n")
    store = Store(home=home)
    # 同一條明示 fixture 命令跑三次；唔會執行任何 host 工程檢查。
    command = [sys.executable, "-I", "-S", "-B", "-c", "raise SystemExit(7)"]
    for _ in range(3):
        assert run_check(command, cwd=workspace, kind="tests", store=store) == 7
    value = api.status(home=home, cwd=workspace)
    assert value["status"] == "pattern", value
    assert value["pattern"]["exit_code"] == 7
    before = file_tree(store.state_dir)
    result = hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home)
    assert "nonzero" in context(result)
    assert file_tree(store.state_dir) == before
    assert len(store.read_state()["runs"]) == 3
    raw = (store.state_dir / "state.json").read_bytes()
    assert b"SystemExit" not in raw and os.fsencode(workspace) not in raw
    scope = keyed_digest(store.get_key(create=False), "path", str(workspace))
    store.invalidate(scope, changed=True)
    assert api.status(home=home, cwd=workspace)["pattern"] is None
    assert hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home) == {}
