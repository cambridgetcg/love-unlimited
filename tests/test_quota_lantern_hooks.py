"""Fake HOME hooks、stdin canaries、跨程序 atomic throttles 同 isolation。"""

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
WRAPPER = TOOLS / "hooks" / "quota-lantern.py"
sys.path.insert(0, str(TOOLS))
from quota_lantern import hooks, store as store_module
from quota_lantern.core import normalize_rate_limits
from quota_lantern.research import record_feedback, record_hypothesis
from quota_lantern.store import Store


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def payload(event="SessionStart", session="fixture-session", cwd="/fixture/project-a"):
    return {"hook_event_name": event, "session_id": session, "cwd": cwd}


def observation(percent=10, *, source="fixture", observed_at=None):
    now = datetime.now(timezone.utc)
    observed_at = observed_at or (now.replace(second=0, microsecond=0)
                                  - timedelta(minutes=30) + timedelta(seconds=percent)).isoformat()
    return normalize_rate_limits({"primary": {
        "used_percent": percent, "window_minutes": 300,
        "resets_at": (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=3)).isoformat(),
    }}, source_kind="local", source_id=source, observed_at=observed_at)[0]


def collector(items=()):
    def collect(_codex_home, *, cursors=None, deadline=None):
        assert deadline is not None
        return {"observations": list(items), "coverage": {}, "diagnostics": [], "cursors": {"fixture": 1}}
    return collect


def invoke(home, event="SessionStart", data=None, *, cwd=None, env=None, timeout=3):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(WRAPPER), event],
        input=json.dumps(payload(event)) if data is None else data,
        text=True, capture_output=True, timeout=timeout, cwd=cwd or home,
        env={**os.environ, "HOME": str(home), **(env or {})},
    )


def test_session_json_and_payload_privacy(home, monkeypatch):
    monkeypatch.setattr(hooks, "collect_local", collector([observation()]))
    forbidden = home / "never-open-transcript.jsonl"
    request = {**payload(), "transcript_path": str(forbidden),
               "prompt": "PROMPT_CANARY", "system_prompt": "SYSTEM_CANARY",
               "tool_input": {"anything": "TOOL_CANARY"}}
    result = hooks.run_hook("SessionStart", request, home=home)
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert len(result["hookSpecificOutput"]["additionalContext"].encode()) <= 1024
    state = home / ".local" / "state" / "quota-lantern"
    saved = "".join(path.read_text() for path in state.rglob("*") if path.is_file())
    for canary in ("PROMPT_CANARY", "SYSTEM_CANARY", "TOOL_CANARY", "never-open", "fixture-session", "/fixture/project-a"):
        assert canary not in saved + json.dumps(result)
    assert not forbidden.exists()
    assert len(Store(home=home).read_cache("receipts")["events"]) == 1


def test_global_observe_and_session_delivery_throttle(home, monkeypatch):
    wall = [1000.0]
    items = [observation(10)]
    calls = []
    monkeypatch.setattr(hooks.time, "time", lambda: wall[0])

    def collect(*args, **kwargs):
        calls.append(1)
        return collector(items)(*args, **kwargs)

    monkeypatch.setattr(hooks, "collect_local", collect)
    assert hooks.run_hook("SessionStart", payload(), home=home)
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None
    assert hooks.run_hook("SessionStart", payload(session="another", cwd="/project-b"), home=home)
    assert len(calls) == 1
    wall[0] += 61
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None
    assert len(calls) == 2
    items[:] = [observation(90)]
    wall[0] += 61
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home)
    assert len(calls) == 3
    items[:] = [observation(99)]
    Store(home=home).append(items)
    wall[0] += 1
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None


def test_subagent_is_cache_only_without_any_writes(home, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SubagentStart must not collect or mutate")

    monkeypatch.setattr(hooks, "collect_local", forbidden)
    monkeypatch.setattr(Store, "append", forbidden)
    monkeypatch.setattr(Store, "mutate_cache", forbidden)
    monkeypatch.setattr(Store, "write_cache", forbidden)
    result = hooks.run_hook("SubagentStart", payload("SubagentStart"), home=home)
    assert result["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert not (home / ".local").exists()


def test_stop_observes_reflects_but_never_blocks(home, monkeypatch):
    monkeypatch.setattr(hooks, "collect_local", collector([observation()]))
    result = hooks.run_hook("Stop", {**payload("Stop"), "stop_hook_active": True}, home=home)
    assert result is None
    assert Store(home=home).read_cache("reflection")["sample_count"] == 1
    process = invoke(home, "Stop")
    assert process.returncode == 0 and process.stderr == ""
    assert json.loads(process.stdout) == {}


def test_failure_does_not_advance_cursor_or_emit_noise(home, monkeypatch):
    monkeypatch.setattr(hooks, "collect_local", collector([observation()]))
    monkeypatch.setattr(Store, "append", lambda *args, **kwargs: {
        "added": 0, "duplicates": 0, "recording_paused": True, "diagnostics": ["journal_full"],
    })
    hooks.run_hook("SessionStart", payload(), home=home)
    assert Store(home=home).read_cache("cursors") is None
    monkeypatch.setattr(hooks, "build_status", lambda *args, **kwargs: 1 / 0)
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None


@pytest.mark.parametrize("event,data", [
    ("NotAnEvent", {}), ("SessionStart", []), ("SessionStart", {}),
    ("SessionStart", payload("Stop")),
    ("SessionStart", {**payload(), "session_id": "../escape"}),
    ("SessionStart", {**payload(), "session_id": "x" * 129}),
    ("SessionStart", {**payload(), "cwd": "x" * 4097}),
])
def test_invalid_event_or_ids_are_noop(home, event, data):
    assert hooks.run_hook(event, data, home=home) is None
    assert not (home / ".local").exists()


@pytest.mark.parametrize("data", [
    "", "{", "[]", '{"hook_event_name":"Stop"}', "x" * (65536 + 1),
    '{"hook_event_name":"Stop","hook_event_name":"SessionStart","session_id":"fixture"}',
    '{"hook_event_name":"SessionStart","session_id":"fixture","prompt":NaN}',
])
def test_wrapper_invalid_input_exits_zero_with_json(home, data):
    process = invoke(home, data=data)
    assert process.returncode == 0
    assert process.stderr == ""
    assert json.loads(process.stdout) == {}
    assert not (home / ".local").exists()


def test_wrapper_ignores_cwd_and_pythonpath_shadow_packages(home, tmp_path):
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "quota_lantern").mkdir()
    (hostile / "quota_lantern" / "__init__.py").write_text('raise RuntimeError("IMPORT_CANARY")')
    (hostile / "json.py").write_text('raise RuntimeError("JSON_CANARY")')
    (hostile / "sitecustomize.py").write_text('raise RuntimeError("SITE_CANARY")')
    process = invoke(home, "SubagentStart", cwd=hostile, env={"PYTHONPATH": str(hostile)})
    assert process.returncode == 0 and process.stderr == ""
    assert json.loads(process.stdout)["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert not (home / ".local").exists()


def test_stdin_without_eof_obeys_internal_deadline(home):
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(WRAPPER), "SessionStart"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, "HOME": str(home)}, cwd=home)
    try:
        process.wait(timeout=2.0)
        assert process.returncode == 0
        assert process.stdout.read().strip() == b"{}"
        assert process.stderr.read() == b""
        assert time.monotonic() - started < 2.0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_slow_collector_is_cut_off(home, monkeypatch):
    monkeypatch.setattr(hooks, "collect_local", lambda *args, **kwargs: time.sleep(10))
    started = time.monotonic()
    result = hooks.run_hook("SessionStart", payload(), home=home)
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "advisory" in result["hookSpecificOutput"]["additionalContext"]
    assert time.monotonic() - started < 2.0


def test_observe_claim_is_process_atomic(home):
    code = (
        "import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from quota_lantern.hooks import _claim_observe; from quota_lantern.store import Store; "
        "print(int(_claim_observe(Store(home=Path(sys.argv[2])),1000,time.monotonic()+1.5)))"
    )
    processes = [subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, str(TOOLS), str(home)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=home,
                                 env={**os.environ, "HOME": str(home)}) for _ in range(8)]
    results = [process.communicate(timeout=3) for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert all(stderr == b"" for _, stderr in results)
    assert sum(int(stdout) for stdout, _ in results) == 1


def test_two_projects_share_real_fixture_observations_without_raw_content(home):
    now = datetime.now(timezone.utc)
    directory = home / ".codex" / "sessions" / now.strftime("%Y/%m/%d")
    directory.mkdir(parents=True)
    event = {"type": "event_msg", "timestamp": now.isoformat(), "payload": {
        "type": "token_count", "rate_limits": {"primary": {
            "used_percent": 25, "window_minutes": 300,
            "resets_at": (now + timedelta(hours=3)).timestamp(),
        }}, "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}},
    }}
    log = directory / "rollout-fixture.jsonl"
    log.write_text(json.dumps({"type": "response_item", "payload": {"text": "LOG_CANARY"}})
                   + "\n" + json.dumps(event) + "\n")
    first = invoke(home, data=json.dumps(payload(session="project-a", cwd="/project-a")))
    second = invoke(home, data=json.dumps(payload(session="project-b", cwd="/project-b")))
    for process in (first, second):
        assert process.returncode == 0 and process.stderr == ""
        assert "25%" in json.loads(process.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "LOG_CANARY" not in process.stdout
    store = Store(home=home)
    assert len(store.read_observations()) == 1
    ident = store.read_observations()[0]["id"]
    record_feedback(store, observation_id=ident, outcome="completed", note="FEEDBACK_CANARY")
    record_hypothesis(store, claim="CLAIM_CANARY", evidence=[ident], next_test="NEXT_CANARY")
    stopped = invoke(home, "Stop", data=json.dumps(payload("Stop", "project-b", "/project-b")))
    assert json.loads(stopped.stdout) == {}
    reflection = store.read_cache("reflection")
    assert reflection["sample_count"] == 1
    assert {"code": "explicit_feedback_recorded", "count": 1} in reflection["findings"]
    resumed = invoke(home, data=json.dumps({**payload(session="project-a", cwd="/project-a"), "source": "resume"}))
    brief = json.loads(resumed.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "研究：1 個樣本" in brief and "下一步：" in brief
    for canary in ("FEEDBACK_CANARY", "CLAIM_CANARY", "NEXT_CANARY"):
        assert canary not in brief + json.dumps(reflection)
    content = "".join(path.read_text() for path in (home / ".local" / "state").rglob("*") if path.is_file())
    assert "LOG_CANARY" not in content


@pytest.mark.parametrize("source", ["startup", "resume", "compact", "clear"])
def test_session_start_always_briefs_and_seeds_prompt_throttle(home, monkeypatch, source):
    wall = [1000.0]
    monkeypatch.setattr(hooks.time, "time", lambda: wall[0])
    monkeypatch.setattr(hooks, "collect_local", collector([observation()]))
    assert hooks.run_hook("SessionStart", payload(), home=home)
    request = {**payload(), "source": source}
    assert hooks.run_hook("SessionStart", request, home=home)
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None
    wall[0] += 61
    assert hooks.run_hook("UserPromptSubmit", payload("UserPromptSubmit"), home=home) is None


@pytest.mark.parametrize("age,expected_calls", [(0, 0), (299, 0), (301, 1)])
def test_session_start_samples_only_missing_or_stale_cache(home, monkeypatch, age, expected_calls):
    now = datetime.now(timezone.utc)
    Store(home=home).append([observation(observed_at=(now - timedelta(seconds=age)).isoformat())])
    calls = []

    def collect(*args, **kwargs):
        calls.append(1)
        return collector()(*args, **kwargs)

    monkeypatch.setattr(hooks, "collect_local", collect)
    assert hooks.run_hook("SessionStart", payload(), home=home)
    assert hooks.run_hook("SessionStart", {**payload(), "source": "resume"}, home=home)
    assert len(calls) == expected_calls


@pytest.mark.parametrize("cache_state", ["present", "missing", "stamp_mismatch"])
def test_subagent_never_falls_back_to_journal(home, monkeypatch, cache_state):
    store = Store(home=home)
    store.append([observation()])
    if cache_state == "missing":
        (store.state_dir / "latest.json").unlink()
    elif cache_state == "stamp_mismatch":
        os.utime(store.state_dir / "observations.jsonl", ns=(1, 1))
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in store.state_dir.iterdir() if path.is_file()}
    actual_read = store_module._read
    actual_latest = Store.read_latest
    reads, modes = [], []

    def read(fd, name, maximum):
        reads.append(name)
        if name == "observations.jsonl":
            raise AssertionError("journal fallback forbidden")
        return actual_read(fd, name, maximum)

    def latest(self, *, cache_only=False):
        modes.append(cache_only)
        return actual_latest(self, cache_only=cache_only)

    def forbidden(*args, **kwargs):
        raise AssertionError("cache-only hook must not collect or write")

    monkeypatch.setattr(store_module, "_read", read)
    monkeypatch.setattr(Store, "read_latest", latest)
    monkeypatch.setattr(hooks, "collect_local", forbidden)
    monkeypatch.setattr(Store, "read_observations", forbidden)
    monkeypatch.setattr(Store, "_locked", forbidden)
    result = hooks.run_hook("SubagentStart", payload("SubagentStart"), home=home)
    assert result is not None
    assert modes == [True] and "observations.jsonl" not in reads
    brief = result["hookSpecificOutput"]["additionalContext"]
    assert ("10%" in brief) == (cache_state == "present")
    assert len(brief.encode()) <= 512
    assert before == {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in store.state_dir.iterdir() if path.is_file()}


def test_subagent_rejects_malformed_cache_without_journal_fallback(home, monkeypatch):
    store = Store(home=home)
    store.append([observation()])
    (store.state_dir / "latest.json").write_text("RAW_CACHE_CANARY")
    reads = []
    actual_read = store_module._read

    def read(fd, name, maximum):
        reads.append(name)
        return actual_read(fd, name, maximum)

    monkeypatch.setattr(store_module, "_read", read)
    assert hooks.run_hook("SubagentStart", payload("SubagentStart"), home=home) is None
    assert "observations.jsonl" not in reads
    assert (store.state_dir / "latest.json").read_text() == "RAW_CACHE_CANARY"


def test_subagent_many_sources_brief_is_at_most_512_utf8_bytes(home, monkeypatch):
    store = Store(home=home)
    store.append([observation(source=f"fixture-{index}") for index in range(100)])
    monkeypatch.setattr(hooks, "collect_local", lambda *args, **kwargs: pytest.fail("source read"))
    result = hooks.run_hook("SubagentStart", payload("SubagentStart"), home=home)
    brief = result["hookSpecificOutput"]["additionalContext"]
    assert "advisory" in brief
    assert 0 < len(brief.encode("utf-8")) <= 512


@pytest.mark.parametrize("failure", ["claim", "collector", "append", "receipt"])
def test_cached_session_brief_survives_sampling_and_receipt_errors(home, monkeypatch, failure):
    Store(home=home).append([observation()])
    monkeypatch.setattr(hooks, "collect_local", collector([observation(90)]))

    def broken(*args, **kwargs):
        raise RuntimeError("RAW_EXCEPTION_CANARY")

    target, attribute = {"claim": (hooks, "_claim_observe"), "collector": (hooks, "collect_local"),
                         "append": (Store, "append"), "receipt": (hooks, "_receipt")}[failure]
    monkeypatch.setattr(target, attribute, broken)
    result = hooks.run_hook("SessionStart", payload(), home=home)
    assert result is not None and "advisory" in result["hookSpecificOutput"]["additionalContext"]
    brief = result["hookSpecificOutput"]["additionalContext"]
    assert ("90%" if failure == "receipt" else "10%") in brief
    assert "stale" in brief
    assert "RAW_EXCEPTION_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("age", [0, 301])
def test_cached_brief_survives_real_process_lock_contention(home, age):
    store = Store(home=home)
    store.append([observation(observed_at=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat())])
    with (store.state_dir / ".lock").open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        result = invoke(home, timeout=2)
        elapsed = time.monotonic() - started
    assert result.returncode == 0 and result.stderr == ""
    assert "10%" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert elapsed < 2


def test_cached_brief_survives_collector_using_all_remaining_budget(home, monkeypatch):
    Store(home=home).append([observation()])
    monkeypatch.setattr(hooks, "collect_local", lambda *args, **kwargs: time.sleep(10))
    started = time.monotonic()
    result = hooks.run_hook("SessionStart", payload(), home=home)
    assert "10%" in result["hookSpecificOutput"]["additionalContext"]
    assert time.monotonic() - started < 2


def test_hook_accepts_only_remaining_wrapper_budget(home, monkeypatch):
    Store(home=home).append([observation()])
    monkeypatch.setattr(hooks, "collect_local", lambda *args, **kwargs: time.sleep(10))
    started = time.monotonic()
    result = hooks.run_hook("SessionStart", payload(), home=home, deadline=started + 0.2)
    assert "10%" in result["hookSpecificOutput"]["additionalContext"]
    assert time.monotonic() - started < 0.5


def test_wrapper_stdin_and_sampling_share_one_deadline(home):
    Store(home=home).append([observation()])
    code = (
        "import sys,time,runpy; sys.path.insert(0,sys.argv[1]); "
        "from quota_lantern import hooks; "
        "hooks.collect_local=lambda *args,**kwargs: time.sleep(10); "
        "sys.argv=[sys.argv[2],'SessionStart']; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, str(TOOLS), str(WRAPPER)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, "HOME": str(home)}, cwd=home)
    try:
        time.sleep(0.8)
        stdout, stderr = process.communicate(json.dumps(payload()).encode(), timeout=1.2)
        assert process.returncode == 0 and stderr == b""
        assert "10%" in json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        assert time.monotonic() - started < 2
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_parallel_delivery_claim_is_once(home):
    code = (
        "import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from quota_lantern.hooks import _claim_delivery; from quota_lantern.store import Store; "
        "print(int(_claim_delivery(Store(home=Path(sys.argv[2])),'a'*64,'b'*64,1000,time.monotonic()+1.5)))"
    )
    processes = [subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, str(TOOLS), str(home)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=home,
                                 env={**os.environ, "HOME": str(home)}) for _ in range(8)]
    results = [process.communicate(timeout=3) for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert all(stderr == b"" for _, stderr in results)
    assert sum(int(stdout) for stdout, _ in results) == 1
