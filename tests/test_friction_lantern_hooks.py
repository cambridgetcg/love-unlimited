"""只讀 API、delivery、薄 hook／CLI 嘅隔離測試。"""
from copy import deepcopy
from pathlib import Path
import fcntl
import json
import os
import subprocess
import sys
import time

import pytest

from .test_friction_lantern_core import CHECK, REPO, SCOPE, at, evidence
from friction_lantern import api, hooks
from friction_lantern.core import derive_pattern
from friction_lantern.store import DeliveryStore
from reality_lantern.core import RealityError, keyed_digest

WRAPPER = REPO / "tools/hooks/friction-lantern.py"
CLI = REPO / "tools/kingdom-friction.py"


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    path.chmod(0o600)


def file_tree(path):
    return {str(item.relative_to(path)): (item.read_bytes(), item.stat().st_mode)
            for item in path.rglob("*") if item.is_file()}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    workspace = tmp_path / "workspace-CANARY"
    workspace.mkdir(mode=0o700)
    # 自建 boundary fixture；唔借真 repo，亦唔執行 Git。
    (workspace / ".git").mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    key = b"k" * 32
    scope = keyed_digest(key, "path", str(workspace))
    state = evidence(scope=scope)
    root = home / ".local/state/reality-lantern"
    write_private(root / "key", key)
    write_private(root / "state.json", json.dumps(state).encode())
    return home, workspace, scope, state


def payload(workspace, event="SessionStart"):
    return {"hook_event_name": event, "cwd": str(workspace), "session_id": "SESSION-CANARY",
            "tool_name": "Bash", "tool_input": {"command": "COMMAND-CANARY"},
            "tool_response": {"stdout": "OUTPUT-CANARY tests passed", "exit_code": 0},
            "prompt": "PROMPT-CANARY", "transcript_path": "TRANSCRIPT-CANARY"}


def context(result):
    return result.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_api_existing_v1_is_readonly_and_scopelocal(fixture):
    home, workspace, scope, _ = fixture
    before = file_tree(home)
    assert api.status(home=home, cwd=workspace)["status"] == "pattern"
    assert "nonzero" in api.brief(home=home, cwd=workspace)
    assert api.status(home=home, cwd=workspace.parent)["pattern"] is None
    child = workspace / "nested"
    child.mkdir()
    (child / ".git").mkdir()
    assert api.status(home=home, cwd=child)["pattern"] is None
    assert file_tree(home) == before
    assert not (home / ".local/state/friction-lantern").exists()


def test_api_missing_state_zero_mkdir_and_key(tmp_path, monkeypatch):
    home = tmp_path / "absent"
    monkeypatch.setenv("HOME", str(home))
    assert api.status(home=home, cwd=tmp_path)["pattern"] is None
    assert "唔代表檢查通過" in api.brief(home=home, cwd=tmp_path)
    assert not home.exists()


@pytest.mark.parametrize("kind", ["corrupt", "duplicate", "symlink", "hardlink", "oversize", "mode", "missing_key"])
def test_api_unsafe_state_unknown_without_repair(fixture, kind):
    home, workspace, _, _ = fixture
    state = home / ".local/state/reality-lantern/state.json"
    if kind == "corrupt":
        state.write_text("RAW-CANARY")
    elif kind == "duplicate":
        state.write_text('{"schema":1,"schema":"CANARY"}')
    elif kind == "symlink":
        target = home / "target-CANARY"
        target.write_bytes(state.read_bytes())
        state.unlink()
        state.symlink_to(target)
    elif kind == "hardlink":
        os.link(state, home / "linked")
    elif kind == "oversize":
        state.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    elif kind == "mode":
        state.chmod(0o644)
    else:
        (state.parent / "key").unlink()
    before = file_tree(home)
    value = api.status(home=home, cwd=workspace)
    assert value["pattern"] is None and "CANARY" not in json.dumps(value)
    assert file_tree(home) == before


def test_api_never_captures_or_runs_process(fixture, monkeypatch):
    home, workspace, _, _ = fixture

    def forbidden(*a, **kw):
        raise AssertionError("唔可以做新 observation")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import reality_lantern.snapshot
    monkeypatch.setattr(reality_lantern.snapshot, "capture", forbidden)
    assert api.status(home=home, cwd=workspace)["status"] == "pattern"


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure"])
def test_hook_emits_pattern_only_once_and_never_writes_reality(fixture, event):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    message = payload(workspace, event)
    value = hooks.run_hook(event, message, home=home)
    assert "nonzero" in context(value)
    assert len(context(value).encode()) <= 512
    assert "CANARY" not in json.dumps(value)
    assert hooks.run_hook(event, message, home=home) == {}
    assert file_tree(home / ".local/state/reality-lantern") == before
    cache = file_tree(home / ".local/state/friction-lantern")
    assert all(b"CANARY" not in content for content, _mode in cache.values())
    assert len((home / ".local/state/friction-lantern/key").read_bytes()) == 32


def test_subagent_pattern_is_zero_write(fixture):
    home, workspace, _, _ = fixture
    before = file_tree(home)
    assert "nonzero" in context(hooks.run_hook("SubagentStart", payload(workspace, "SubagentStart"), home=home))
    assert file_tree(home) == before
    assert not (home / ".local/state/friction-lantern").exists()


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SubagentStart"])
def test_no_pattern_hook_silent_no_state_even_bash_success(tmp_path, monkeypatch, event):
    home = tmp_path / "absent"
    monkeypatch.setenv("HOME", str(home))
    assert hooks.run_hook(event, payload(tmp_path, event), home=home) == {}
    assert not home.exists()


def test_existing_receipts_ordinary_bash_does_not_mint_pass(fixture):
    home, workspace, _, state = fixture
    last = list(state["projections"])[-1]
    state["projections"][last]["validity"] = "unknown"
    write_private(home / ".local/state/reality-lantern/state.json", json.dumps(state).encode())
    before = file_tree(home)
    assert hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home) == {}
    assert file_tree(home) == before


@pytest.mark.parametrize("tool", ["Read", "Edit", "Write", None, {"tool": "CANARY"}])
def test_unrelated_post_tool_ignored(fixture, tool):
    home, workspace, _, _ = fixture
    message = payload(workspace, "PostToolUse")
    message["tool_name"] = tool
    before = file_tree(home)
    assert hooks.run_hook("PostToolUse", message, home=home) == {}
    assert file_tree(home) == before


@pytest.mark.parametrize("bad", [None, "CANARY", [], {"hook_event_name": "CANARY"}])
def test_malformed_hook_is_safe_silent(tmp_path, bad):
    assert hooks.run_hook("SessionStart", bad, home=tmp_path) == {}
    assert not list(tmp_path.iterdir())


def test_delivery_requires_change_and_sixty_seconds(tmp_path, monkeypatch):
    from friction_lantern import store
    delivery = DeliveryStore(home=tmp_path)
    monkeypatch.setattr(store, "utc_now", lambda: at(0))
    assert delivery.deliver("SESSION-CANARY", SCOPE, CHECK, event="SessionStart")
    monkeypatch.setattr(store, "utc_now", lambda: at(59))
    assert not delivery.deliver("SESSION-CANARY", SCOPE, "c" * 64, event="PostToolUse")
    monkeypatch.setattr(store, "utc_now", lambda: at(60))
    assert not delivery.deliver("SESSION-CANARY", SCOPE, CHECK, event="SessionStart")
    assert delivery.deliver("SESSION-CANARY", SCOPE, "c" * 64, event="UserPromptSubmit")
    monkeypatch.setattr(store, "utc_now", lambda: at(59))
    assert not delivery.deliver("SESSION-CANARY", SCOPE, CHECK, event="PostToolUseFailure")
    assert b"CANARY" not in (delivery.state_dir / "delivery.json").read_bytes()


def test_delivery_subagent_never_writes_even_bad_inputs(tmp_path):
    home = tmp_path / "absent"
    assert DeliveryStore(home=home).deliver(None, None, None, event="SubagentStart") is False
    assert not home.exists()


def test_delivery_bounded_eviction(tmp_path):
    delivery = DeliveryStore(home=tmp_path)
    for index in range(258):
        assert delivery.deliver("SESSION-CANARY-" + str(index), SCOPE, CHECK, event="SessionStart")
    raw = (delivery.state_dir / "delivery.json").read_bytes()
    assert len(json.loads(raw)["entries"]) == 256
    assert len(raw) <= 256 * 1024 and b"CANARY" not in raw


def test_delivery_lock_wait_twenty_five_ms(fixture):
    home, workspace, _, _ = fixture
    root = home / ".local/state/friction-lantern"
    root.mkdir(mode=0o700)
    write_private(root / ".lock", b"")
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        result = hooks.run_hook("SessionStart", payload(workspace), home=home)
        elapsed = time.monotonic() - started
    assert "未能核實" in context(result)
    assert elapsed < 0.2
    assert not (root / "key").exists()


def test_delivery_write_failure_is_fixed_failopen(fixture, monkeypatch):
    from friction_lantern import store
    home, workspace, _, _ = fixture

    def fail(*args, **kwargs):
        raise OSError("RAW-CANARY")

    monkeypatch.setattr(store, "atomic_write", fail)
    result = hooks.run_hook("SessionStart", payload(workspace), home=home)
    assert "未能核實" in context(result)
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SubagentStart"])
def test_bookkeeping_fault_does_not_spam_per_turn(fixture, monkeypatch, event):
    home, workspace, _, _ = fixture

    def fail(**kw):
        raise OSError("RAW-CANARY")

    monkeypatch.setattr(hooks, "status", fail)
    for _ in range(2):
        assert hooks.run_hook(event, payload(workspace, event), home=home) == {}
    assert not (home / ".local/state/friction-lantern").exists()


def test_existing_cache_missing_key_is_not_repaired(tmp_path):
    delivery = DeliveryStore(home=tmp_path)
    assert delivery.deliver("session", SCOPE, CHECK, event="SessionStart")
    (delivery.state_dir / "key").unlink()
    before = file_tree(tmp_path)
    with pytest.raises(RealityError):
        delivery.deliver("session", SCOPE, CHECK, event="SessionStart")
    assert file_tree(tmp_path) == before


def test_hook_timeout_respects_shared_deadline(fixture, monkeypatch):
    home, workspace, _, _ = fixture
    monkeypatch.setattr(hooks, "status", lambda **kw: time.sleep(2))
    started = time.monotonic()
    result = hooks.run_hook("SessionStart", payload(workspace), home=home, deadline=started + 0.05)
    assert time.monotonic() - started < 0.3
    assert "未能核實" in context(result)


@pytest.mark.parametrize("session", [None, "", "x" * 4097, ["CANARY"]])
def test_missing_session_does_not_create_delivery(fixture, session):
    home, workspace, _, _ = fixture
    message = payload(workspace)
    message["session_id"] = session
    before = file_tree(home)
    assert hooks.run_hook("SessionStart", message, home=home) == {}
    assert file_tree(home) == before
    assert not (home / ".local/state/friction-lantern").exists()


@pytest.mark.parametrize("kind", ["corrupt", "symlink", "hardlink", "mode", "oversize"])
def test_delivery_unsafe_cache_is_not_repaired(fixture, kind):
    home, workspace, _, _ = fixture
    root = home / ".local/state/friction-lantern"
    write_private(root / "delivery.json", b"RAW-CANARY")
    cache = root / "delivery.json"
    if kind == "symlink":
        cache.unlink()
        cache.symlink_to(home / "absent-CANARY")
    elif kind == "hardlink":
        os.link(cache, home / "link")
    elif kind == "mode":
        cache.chmod(0o644)
    elif kind == "oversize":
        cache.write_bytes(b"x" * (256 * 1024 + 1))
    result = hooks.run_hook("SessionStart", payload(workspace), home=home)
    assert "未能核實" in context(result) and "CANARY" not in json.dumps(result)
    assert not (root / "key").exists()
    if kind == "symlink":
        assert cache.is_symlink() and not (home / "absent-CANARY").exists()
    else:
        assert cache.read_bytes() == (b"x" * (256 * 1024 + 1) if kind == "oversize" else b"RAW-CANARY")


def test_api_rejects_relative_and_control_locators_without_io(tmp_path):
    for value in (None, "relative-CANARY", "/path\nCANARY", "/a/../b", {"raw": "CANARY"}):
        assert api.status(home=tmp_path, cwd=value)["diagnostics"] == ["invalid_input"]
        assert api.status(home=value, cwd=tmp_path)["diagnostics"] == ["invalid_input"]
    assert not list(tmp_path.iterdir())


def environment(home):
    return dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")


@pytest.mark.parametrize("data", [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1,"x":2}', b'"CANARY"', b'{"x":"CANARY"', b"x" * (64 * 1024 + 1)])
def test_thin_hook_bad_stdin_never_echoes(tmp_path, data):
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WRAPPER), "SessionStart"],
                            input=data, capture_output=True, timeout=2, env=environment(tmp_path), cwd=tmp_path)
    assert result.returncode == 0
    assert isinstance(json.loads(result.stdout), dict)
    assert b"CANARY" not in result.stdout + result.stderr
    assert not result.stderr and not list(tmp_path.iterdir())


def test_thin_hook_stalled_stdin_total_budget(tmp_path):
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(WRAPPER), "SessionStart"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment(tmp_path), cwd=tmp_path)
    started = time.monotonic()
    try:
        process.wait(timeout=1.5)
        assert time.monotonic() - started < 1.0
        assert "未能核實" in context(json.loads(process.stdout.read()))
        assert process.stderr.read() == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    assert not list(tmp_path.iterdir())


def test_thin_hook_actual_cached_fixture_not_live_harness(fixture):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WRAPPER), "PostToolUse"],
                            input=json.dumps(payload(workspace, "PostToolUse")).encode(), capture_output=True,
                            timeout=2, env=environment(home), cwd=workspace)
    assert result.returncode == 0 and not result.stderr
    assert "nonzero" in context(json.loads(result.stdout))
    assert b"CANARY" not in result.stdout
    assert file_tree(home / ".local/state/reality-lantern") == before


@pytest.mark.parametrize("args", [["--RAW-CANARY"], ["status", "--RAW-CANARY"], ["--home", "relative-CANARY", "status"],
                                   ["brief", "--max-bytes", "RAW-CANARY"], ["status", "--cwd", "relative-CANARY"],
                                   ["install", "--RAW-CANARY"]])
def test_cli_bad_flags_are_private(tmp_path, args):
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI), *args],
                            capture_output=True, timeout=2, env=environment(tmp_path), cwd=tmp_path)
    assert result.returncode == 2
    assert b"CANARY" not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


def test_cli_status_explicit_home_only_reads_fixture(fixture, tmp_path):
    home, workspace, _, _ = fixture
    untouched = tmp_path / "other-home"
    before = file_tree(home)
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI), "--home", str(home),
                             "status", "--cwd", str(workspace), "--json"],
                            capture_output=True, timeout=2, env=environment(untouched), cwd=workspace)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["status"] == "pattern"
    assert b"CANARY" not in result.stdout
    assert file_tree(home) == before and not untouched.exists()
