"""CLI／薄 installer／stdin 假 HOME E2E；唔代表 live hook activation。"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "tools"))
from coedit_radar import api
from coedit_radar.cli import main
from coedit_radar.install import SPEC, doctor, install, uninstall


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    repo = tmp_path.resolve() / "source"
    repo.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    subprocess.run(["git", "init", "-q", str(repo)], env=dict(os.environ), check=True, capture_output=True)
    file = repo / "scratch.txt"
    file.write_text("initial\n")
    return home, repo, file


def call_cli(home, *args):
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(SOURCE / "tools/kingdom-coedit.py"), *args],
                          env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=3)


def test_cli_requires_explicit_file_and_session(desk):
    home, repo, file = desk
    for args in (("observe", "--file", str(file)), ("compare", "--session", "s"),
                 ("observe",), ("compare", "--unknown-CANARY")):
        value = call_cli(home, *args)
        assert value.returncode == 2
        assert json.loads(value.stdout)["reason"] == "invalid_arguments"
        assert "CANARY" not in value.stdout + value.stderr
        assert str(file) not in value.stdout + value.stderr
    assert not list(home.iterdir())


def test_cli_explicit_lifecycle_and_readonly_missing_session(desk):
    home, repo, file = desk
    value = call_cli(home, "status", "--json")
    assert value.returncode == 0
    assert json.loads(value.stdout)["reason"] == "session_required"
    value = call_cli(home, "observe", "--file", str(file), "--session", "s", "--json")
    assert json.loads(value.stdout)["source"] == "explicit_observation"
    value = call_cli(home, "compare", "--file", str(file), "--session", "s", "--json")
    assert json.loads(value.stdout)["status"] == "no_observed_difference"
    file.write_text("changed\n")
    value = call_cli(home, "compare", "--file", str(file), "--session", "s", "--json")
    assert json.loads(value.stdout)["status"] == "changed"
    value = call_cli(home, "brief", "--session", "s", "--max-bytes", "80")
    assert len(value.stdout.strip().encode()) <= 80
    value = call_cli(home, "brief")
    assert "未知" in value.stdout
    assert str(file) not in value.stdout


def test_cli_corruption_fixed_unknown_failopen(desk):
    home, repo, file = desk
    api.observe(home=home, path=file, session="s")
    (home / ".local/state/coedit-radar/cache.json").write_text("SECRET-CANARY")
    value = call_cli(home, "observe", "--file", str(file), "--session", "s", "--json")
    assert value.returncode == 0
    assert json.loads(value.stdout)["status"] == "unknown"
    assert "SECRET-CANARY" not in value.stdout + value.stderr


def copy_source(repo):
    # 只帶產品本身同 public common dependency，唔複製 repo／HOME state。
    for package in ("coedit_radar", "lantern_common"):
        for path in (SOURCE / "tools" / package).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md"):
                target = repo / path.relative_to(SOURCE)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    for name in ("tools/kingdom-coedit.py", "tools/hooks/coedit-radar.py"):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)


def test_thin_installer_fake_home_roundtrip_preserves_foreign(desk):
    home, repo, file = desk
    copy_source(repo)
    (home / ".claude").mkdir(mode=0o700)
    (home / ".codex").mkdir(mode=0o700)
    settings = home / ".claude/settings.json"
    original = {"model": "preserved-model", "permissions": {"allow": ["Read"], "deny": ["Bash(secret)"]},
                "statusLine": {"type": "command", "command": "preserved-statusline"},
                "env": {"LOGIN-CANARY": "preserved-login"},
                "hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "foreign"}]}]}}
    settings.write_text(json.dumps(original))
    settings.chmod(0o600)
    guide = home / ".codex/AGENTS.md"
    guide.write_text("foreign guide\n")
    guide.chmod(0o600)
    before = settings.read_bytes()
    dry = install(home=home, repo_root=repo)
    assert dry["ok"] and dry["dry_run"] and not dry["created"]
    assert settings.read_bytes() == before
    assert not (home / ".local").exists()
    created = install(home=home, repo_root=repo, dry_run=False)
    assert created["ok"] and created["created"]
    assert created["activation"] == "unverified"
    value = json.loads(settings.read_bytes())
    for key in ("model", "permissions", "statusLine", "env"):
        assert value[key] == original[key]
    assert value["hooks"]["PreToolUse"][0] == original["hooks"]["PreToolUse"][0]
    assert set(SPEC.events) <= set(value["hooks"])
    healthy = doctor(home=home, repo_root=repo)
    assert healthy["ok"] and healthy["installed"]
    assert healthy["activation"] == "unverified"
    again = install(home=home, repo_root=repo, dry_run=False)
    assert again["ok"] and not again["created"]
    assert again["installation_nonce"] == created["installation_nonce"]
    wrapper = home / ".local/bin/kingdom-coedit"
    output = subprocess.run([str(wrapper), "status", "--json"], env={**os.environ, "HOME": str(home)},
                            capture_output=True, text=True, timeout=3)
    assert output.returncode == 0 and json.loads(output.stdout)["status"] == "unknown"
    rejected = uninstall(home=home, repo_root=repo, expected_nonce="00000000-0000-4000-8000-000000000000")
    assert not rejected["ok"] and wrapper.exists()
    api.observe(home=home, path=file, session="s")
    cached = (home / ".local/state/coedit-radar/cache.json").read_bytes()
    removed = uninstall(home=home, repo_root=repo, expected_nonce=created["installation_nonce"])
    assert removed["ok"]
    assert not wrapper.exists()
    assert json.loads(settings.read_bytes()) == original
    assert guide.read_text() == "foreign guide\n"
    assert (home / ".local/state/coedit-radar/cache.json").read_bytes() == cached


def hook_process(home, event, data):
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(SOURCE / "tools/hooks/coedit-radar.py"), event],
                          input=data, env={**os.environ, "HOME": str(home)}, capture_output=True, timeout=3)


@pytest.mark.parametrize("data", [b"{", b"[]", b'{"hook_event_name":"PreToolUse","hook_event_name":"PostToolUse"}',
                                   b"CANARY" * 12000, b'{"x":NaN}'])
def test_hook_stdin_corruption_and_bounds_failopen(desk, data):
    home, repo, file = desk
    value = hook_process(home, "PreToolUse", data)
    assert value.returncode == 0
    output = json.loads(value.stdout)
    assert "CANARY" not in value.stdout.decode() + value.stderr.decode()
    assert not list(home.iterdir())
    if output:
        assert set(output) == {"hookSpecificOutput"}
        assert set(output["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
        assert "未知" in output["hookSpecificOutput"]["additionalContext"]


def test_hook_waiting_stdin_deadline(desk):
    home, repo, file = desk
    start = time.monotonic()
    child = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(SOURCE / "tools/hooks/coedit-radar.py"), "PreToolUse"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={**os.environ, "HOME": str(home)})
    try:
        child.wait(timeout=2)
        assert time.monotonic() - start < 1
        assert child.returncode == 0
        output = json.loads(child.stdout.read())
        assert "未知" in output["hookSpecificOutput"]["additionalContext"]
        assert not list(home.iterdir())
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_hook_pipe_proves_fake_payload_not_live(desk):
    home, repo, file = desk
    def send(event, tool, token):
        inputs = {"file_path": str(file)}
        if tool == "Write":
            inputs["content"] = "WRITE-CANARY"
        data = {"hook_event_name": event, "tool_name": tool, "tool_input": inputs,
                "session_id": "SESSION-CANARY", "tool_use_id": token, "transcript_path": "/TRANSCRIPT-CANARY"}
        if event == "PostToolUse" and tool == "Read":
            data["tool_response"] = {"type": "text", "file": {"filePath": str(file), "content": "CONTENT-CANARY",
                                      "numLines": 1, "startLine": 1, "totalLines": 1}}
        response = hook_process(home, event, json.dumps(data).encode())
        assert response.returncode == 0
        return json.loads(response.stdout)
    assert send("PreToolUse", "Read", "r") == {}
    assert send("PostToolUse", "Read", "r") == {}
    assert api.status(home=home, session="SESSION-CANARY")["baseline_count"] == 1
    file.write_text("changed\n")
    output = send("PreToolUse", "Write", "w")
    assert "有變" in output["hookSpecificOutput"]["additionalContext"]
    assert set(output["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert "CANARY" not in json.dumps(output)
    assert "CANARY" not in (home / ".local/state/coedit-radar/cache.json").read_text()
    assert send("PostToolUseFailure", "Write", "w") == {}
    assert api.status(home=home, session="SESSION-CANARY")["baseline_count"] == 0


def clock_cli(home, wall, elapsed, *args, boot="11111111-1111-4111-8111-111111111111"):
    # 測試 process 內雙 clock，真正 source entry／argparse／Store；無 production env override。
    driver = """
import runpy, sys, time
sys.path.insert(0, sys.argv[1])
from coedit_radar import core
entry, wall, elapsed, boot = sys.argv[2:6]
time.time = lambda: float(wall)
core.system_clock = lambda: (boot, float(elapsed))
sys.argv = [entry, *sys.argv[6:]]
runpy.run_path(entry, run_name='__main__')
"""
    response = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", driver,
                               str(SOURCE / "tools"), str(SOURCE / "tools/kingdom-coedit.py"),
                               str(wall), str(elapsed), boot, *args],
                              env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=3)
    assert response.returncode == 0, response.stderr
    return json.loads(response.stdout)


def test_source_cli_dual_clock_readonly_stale_stays_stale(desk):
    home, repo, file = desk
    args = ("--file", str(file), "--session", "clock-reader", "--json")
    assert clock_cli(home, 2_000_000_000, 10_000, "observe", *args)["status"] == "observed"
    root = home / ".local/state/coedit-radar"
    (root / ".lock").unlink()
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    assert clock_cli(home, 2_000_001_801, 11_801, "compare", *args)["reason"] == "baseline_stale"
    assert clock_cli(home, 2_000_001_741, 11_802, "compare", *args)["reason"] == "baseline_stale"
    assert clock_cli(home, 2_000_001_742, 11_803, "status", "--session", "clock-reader", "--json")["status"] == "unknown"
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()} == before


@pytest.mark.parametrize("count", [255, 256])
def test_source_cli_overflow_persists_across_hook_processes(desk, count):
    from coedit_radar.core import MAX_CACHE, MAX_ENTRIES, digest, end_time
    from coedit_radar.store import Store, put
    home, repo, file = desk
    args = ("--file", str(file), "--session", "reader", "--json")
    assert json.loads(call_cli(home, "observe", *args).stdout)["status"] == "observed"
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        template = next(iter(state["entries"].values()))
        for number in range(count - 1):
            clone = copy.deepcopy(template)
            clone["session"] = digest(key, "filler", number)
            assert put(state, digest(key, "entry", number), clone)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "writer",
               "tool_use_id": "w", "tool_input": {"file_path": str(file), "content": "not-executed"}}
    response = hook_process(home, "PreToolUse", json.dumps(payload).encode())
    assert response.returncode == 0
    assert json.loads(response.stdout) == {}
    assert json.loads(call_cli(home, "compare", *args).stdout)["reason"] == "mutation_pending"
    assert json.loads(call_cli(home, "observe", *args).stdout)["reason"] == "mutation_pending"
    assert json.loads(call_cli(home, "compare", *args).stdout)["status"] == "unknown"
    assert file.read_text() == "initial\n"
    assert len(Store(home=home).read()[1]["entries"]) <= MAX_ENTRIES
    assert (home / ".local/state/coedit-radar/cache.json").stat().st_size <= MAX_CACHE


def test_source_cli_new_baseline_stable_after_wall_rollback_and_reboot(desk):
    home, repo, file = desk
    args = ("--file", str(file), "--session", "reader", "--json")
    assert clock_cli(home, 2_000_000_000, 10_000, "observe", *args)["status"] == "observed"
    assert clock_cli(home, 1_999_999_940, 10_001, "observe", *args)["status"] == "observed"
    assert clock_cli(home, 1_999_999_880, 10_002, "compare", *args)["status"] == "no_observed_difference"
    assert clock_cli(home, 1_999_999_881, 10_003, "compare", *args,
                     boot="22222222-2222-4222-8222-222222222222")["reason"] == "baseline_stale"
    assert clock_cli(home, 1_999_999_882, 10_004, "observe", *args,
                     boot="22222222-2222-4222-8222-222222222222")["status"] == "observed"
    assert clock_cli(home, 1_999_999_883, 10_005, "compare", *args,
                     boot="22222222-2222-4222-8222-222222222222")["status"] == "no_observed_difference"
