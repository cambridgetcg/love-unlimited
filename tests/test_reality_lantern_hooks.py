"""實況燈 event 合約測試；fake Store 同 private subprocess HOME。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import time
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "tools/hooks/reality-lantern.py"


class FakeStore:
    def __init__(self):
        self.key = b"k" * 32
        self.state = {"scopes": {"repo": {"generation": 0}, "other": {"generation": 0}},
                      "validity": "match"}
        self.calls = []
        self.due = True

    def get_key(self, *, create):
        self.calls.append(("key", create))
        assert create is False
        return self.key

    def read_state(self):
        self.calls.append(("read",))
        return copy.deepcopy(self.state)

    def invalidate(self, scope, *, changed, deadline):
        self.calls.append(("invalidate", scope, changed))
        self.state["scopes"][scope]["generation"] += 1
        self.state["validity"] = "stale" if changed else "unknown"
        assert deadline <= time.monotonic() + 0.026

    def deliver(self, session, scope, fingerprint, *, event, interval_seconds, deadline):
        self.calls.append(("deliver", event, interval_seconds))
        assert deadline <= time.monotonic() + 0.026
        return self.due


@pytest.fixture
def unit(monkeypatch):
    store = FakeStore()
    core = types.ModuleType("reality_lantern.core")
    core.build_status = lambda state, **kwargs: {"validity": state["validity"] if state else "unknown",
                                               "scope_id": kwargs.get("scope_id")}
    budgets = []

    def render(status, *, max_bytes):
        budgets.append(max_bytes)
        return "固定提示 " + status["validity"]

    core.render_brief = render
    core.status_fingerprint = lambda status: status["validity"]
    snapshot = types.ModuleType("reality_lantern.snapshot")
    snapshot.scope_candidates = lambda path, **kw: [
        "repo" if str(path).startswith("/workspace/repo") else
        "other" if str(path).startswith("/workspace/other") else "missing",
    ]
    storage = types.ModuleType("reality_lantern.store")
    storage.Store = lambda **kwargs: store
    for module in (core, snapshot, storage):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = spec_from_file_location("reality_lantern.hooks_unit", REPO / "tools/reality_lantern/hooks.py")
    hooks = module_from_spec(spec)
    spec.loader.exec_module(hooks)
    return hooks, store, budgets


def payload(tool=None):
    return {"cwd": "/workspace/repo", "session_id": "SESSION-CANARY", "tool_name": tool,
            "prompt": "PROMPT-CANARY", "transcript_path": "TRANSCRIPT-CANARY"}


def text(result):
    return result.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_pre_bash_invalidates_before_runner(unit):
    hooks, store, _ = unit
    event = payload("Bash")
    event["tool_input"] = {"command": "COMMAND-CANARY"}
    assert hooks.run_hook("PreToolUse", event) == {}
    assert ("invalidate", "repo", False) in store.calls
    assert store.state["validity"] == "unknown"


def test_post_bash_never_mints_success(unit):
    hooks, store, _ = unit
    store.state["validity"] = "unknown"
    event = payload("Bash")
    event["tool_response"] = {"stdout": "tests passed", "exit_code": 0,
                              "backgroundTaskId": "background-CANARY"}
    result = hooks.run_hook("PostToolUse", event)
    assert "unknown" in text(result)
    assert not any(call[0] == "invalidate" for call in store.calls)
    assert "CANARY" not in json.dumps(result)


def test_new_runner_receipt_survives_post_bash(unit):
    hooks, store, _ = unit
    hooks.run_hook("PreToolUse", payload("Bash"))
    store.state["validity"] = "match"
    result = hooks.run_hook("PostToolUse", payload("Bash"))
    assert "match" in text(result)
    assert len([call for call in store.calls if call[0] == "invalidate"]) == 1


@pytest.mark.parametrize("tool, field", [("Edit", "file_path"), ("Write", "file_path"),
                                          ("NotebookEdit", "notebook_path")])
def test_file_success_stales_related_scope(unit, tool, field):
    hooks, store, _ = unit
    event = payload(tool)
    event["tool_input"] = {field: "/workspace/other/file with spaces.py", "content": "SECRET-CANARY"}
    result = hooks.run_hook("PostToolUse", event)
    assert ("invalidate", "other", True) in store.calls
    assert "stale" in text(result)
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("event_name, response", [("PostToolUseFailure", None),
                                                  ("PostToolUse", {"error": "ERROR-CANARY"})])
def test_failure_or_notebook_error_is_unknown(unit, event_name, response):
    hooks, store, _ = unit
    event = payload("NotebookEdit")
    event["tool_input"] = {"notebook_path": "/workspace/repo/a.ipynb"}
    event["tool_response"] = response
    result = hooks.run_hook(event_name, event)
    assert ("invalidate", "repo", False) in store.calls
    assert "unknown" in text(result)
    assert "CANARY" not in json.dumps(result)


def test_missing_file_locator_marks_unknown_not_success(unit):
    hooks, store, _ = unit
    result = hooks.run_hook("PostToolUse", payload("Write"))
    assert ("invalidate", "repo", False) in store.calls
    assert "unknown" in text(result)


def test_write_outside_known_scope_does_not_stale_cwd(unit):
    hooks, store, _ = unit
    event = payload("Write")
    event["tool_input"] = {"file_path": "/untracked-scope/example.py"}
    assert hooks.run_hook("PostToolUse", event) == {}
    assert not any(call[0] == "invalidate" for call in store.calls)


def test_subagent_reads_cache_without_any_write(unit):
    hooks, store, budgets = unit
    result = hooks.run_hook("SubagentStart", payload())
    assert "match" in text(result)
    assert store.calls == [("key", False), ("read",)]
    assert budgets == [512]


def test_subagent_does_not_borrow_other_scope(unit):
    hooks, store, _ = unit
    event = payload()
    event["cwd"] = "/unrelated"
    assert "unknown" in text(hooks.run_hook("SubagentStart", event))
    assert not any(call[0] in ("deliver", "invalidate") for call in store.calls)


def test_each_session_start_has_context_even_when_delivery_not_due(unit):
    hooks, store, budgets = unit
    store.due = False
    assert text(hooks.run_hook("SessionStart", payload()))
    assert text(hooks.run_hook("SessionStart", payload()))
    assert budgets == [768, 768]


def test_session_start_cached_fallback_survives_busy_delivery(unit):
    hooks, store, _ = unit
    store.deliver = lambda *a, **kw: (_ for _ in ()).throw(OSError("RAW-CANARY"))
    result = hooks.run_hook("SessionStart", payload())
    assert "match" in text(result) and "CANARY" not in str(result)


def test_prompt_submit_uses_sixty_second_material_throttle(unit):
    hooks, store, _ = unit
    store.due = False
    assert hooks.run_hook("UserPromptSubmit", payload()) == {}
    assert ("deliver", "UserPromptSubmit", 60) in store.calls


def test_tool_change_does_not_wait_sixty_seconds(unit):
    hooks, store, _ = unit
    event = payload("Edit")
    event["tool_input"] = {"file_path": "/workspace/repo/a.py"}
    hooks.run_hook("PostToolUse", event)
    assert ("deliver", "PostToolUse", 0) in store.calls


@pytest.mark.parametrize("event_name", ["SessionStart", "SubagentStart", "UserPromptSubmit",
                                        "PreToolUse", "PostToolUse", "PostToolUseFailure"])
def test_missing_state_never_creates_key(unit, event_name):
    hooks, store, _ = unit
    store.key = None
    hooks.run_hook(event_name, payload("Bash"))
    assert store.calls == [("key", False)]


@pytest.mark.parametrize("data", [None, [], "RAW-CANARY", {"hook_event_name": "Stop"}])
def test_malformed_payload_is_safe_unknown(unit, data):
    hooks, store, _ = unit
    result = hooks.run_hook("PostToolUse", data)
    assert "未知" in text(result)
    assert "CANARY" not in str(result)
    assert not store.calls


def test_unknown_event_has_no_effect(unit):
    hooks, store, _ = unit
    assert hooks.run_hook("Stop", payload()) == {}
    assert not store.calls


def test_hook_timeout_is_bounded_and_does_not_keep_match(unit):
    hooks, store, _ = unit
    store.read_state = lambda: time.sleep(2)
    started = time.monotonic()
    result = hooks.run_hook("SessionStart", payload(), deadline=started + 0.050)
    assert time.monotonic() - started < 0.5
    assert "未知" in text(result) and "match" not in text(result)


def test_expired_deadline_does_not_touch_store(unit):
    hooks, store, _ = unit
    result = hooks.run_hook("PostToolUse", payload("Edit"), deadline=time.monotonic() - 1)
    assert "未知" in text(result) and not store.calls


@pytest.mark.parametrize("data", [b'{"cwd":"one","cwd":"two"}', b'{"x":NaN}',
                                  b'{"x":Infinity}', b'"STRING-CANARY"',
                                  b'{"unclosed":', b"x" * (64 * 1024 + 1)])
def test_wrapper_malformed_input_never_emits_raw_content(tmp_path, data):
    environment = dict(os.environ, HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WRAPPER), "PostToolUse"],
                            input=data, capture_output=True, env=environment, timeout=3)
    assert result.returncode == 0
    decoded = json.loads(result.stdout)
    assert "未知" in text(decoded)
    assert "CANARY" not in result.stdout.decode() + result.stderr.decode()
    assert not result.stderr
    assert not list(tmp_path.iterdir())
