"""Fake hook payload lifecycle；synthetic 配對唔當實際 harness activation。"""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from coedit_radar import RadarError, api, core, hooks, store
from coedit_radar.core import EXPLICIT, HOOK, MAX_CACHE, MAX_ENTRIES, TTL, digest, end_time
from coedit_radar.store import Store, put


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    file = tmp_path.resolve() / "source.txt"
    file.write_text("initial\n")
    return home, file


def payload(file, *, event="PreToolUse", tool="Read", session="s", token="r1"):
    inputs = {"file_path": str(file)}
    if tool == "Write":
        inputs["content"] = "WRITE-CANARY"
    elif tool == "Edit":
        inputs.update(old_string="EDIT-OLD-CANARY", new_string="EDIT-NEW-CANARY")
    value = {"hook_event_name": event, "tool_name": tool, "tool_input": inputs,
             "session_id": session, "tool_use_id": token, "cwd": "CWD-CANARY",
             "transcript_path": "/TRANSCRIPT-CANARY", "prompt": "PROMPT-CANARY"}
    if event == "PostToolUse" and tool == "Read":
        value["tool_response"] = {"type": "text", "file": {"filePath": str(file), "content": "RESPONSE-CANARY",
                                  "numLines": 1, "startLine": 1, "totalLines": 1}}
    return value


def process(home, value):
    return hooks.process_event(value["hook_event_name"], value, home=home, deadline=end_time())


def read(home, file, *, session="s", token="r1"):
    assert process(home, payload(file, session=session, token=token))["reason"] == "read_pending"
    value = process(home, payload(file, event="PostToolUse", session=session, token=token))
    assert value["status"] == "observed"
    assert value["read_bytes"] == "unknown"


@pytest.mark.parametrize("tool", ["Edit", "Write"])
@pytest.mark.parametrize("completion", ["PostToolUse", "PostToolUseFailure"])
def test_terminal_empty_line_metadata_lifecycle(desk, tool, completion):
    home, file = desk
    # Synthetic 重現 main 回報嘅 UTF-8 bytes／metadata shape，唔讀真 scratch／payload。
    file.write_bytes("狀態：完成。\n註記：模擬另一個 writer 已更新。\n".encode("utf-8"))
    before = payload(file)
    after = payload(file, event="PostToolUse")
    after["tool_response"]["file"].update(numLines=3, startLine=1, totalLines=3)
    assert hooks.run_hook("PreToolUse", before, home=home) == {}
    assert hooks.run_hook("PostToolUse", after, home=home) == {}
    status = api.status(home=home, session="s")
    assert status["baseline_count"] == 1
    assert status["sources"][HOOK] == 1
    assert status["read_bytes"] == "unknown"
    _, state = Store(home=home).read()
    baseline = next(item for item in state["entries"].values() if item["kind"] == "baseline")
    assert state["schema"] == "kingdom.coedit-radar.cache/2"
    assert baseline["lines"] == 2  # 冇改 physical line count 或 schema。
    assert all("trailing_lf" not in item for item in state["entries"].values())
    cached = (home / ".local/state/coedit-radar/cache.json").read_text()
    assert "CANARY" not in cached and str(file) not in cached and "模擬" not in cached

    file.write_bytes("狀態：再更新。\n".encode("utf-8"))
    output = hooks.run_hook("PreToolUse", payload(file, tool=tool, token="w"), home=home)
    assert set(output) == {"hookSpecificOutput"}
    context = output["hookSpecificOutput"]
    assert set(context) == {"hookEventName", "additionalContext"}
    assert context["hookEventName"] == "PreToolUse"
    assert "重新讀取" in context["additionalContext"]
    assert "CANARY" not in context["additionalContext"] and str(file) not in context["additionalContext"]
    assert api.status(home=home, session="s")["baseline_count"] == 0
    assert api.observe(home=home, path=file, session="s")["reason"] == "mutation_pending"
    assert hooks.run_hook(completion, payload(file, tool=tool, token="w", event=completion), home=home) == {}
    assert api.status(home=home, session="s")["baseline_count"] == 0
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"


@pytest.mark.parametrize("tool", ["Edit", "Write"])
@pytest.mark.parametrize("change", ["content", "identity", "delete"])
def test_preflight_only_additional_context(desk, tool, change):
    home, file = desk
    read(home, file)
    if change == "content":
        file.write_text("changed\n")
    elif change == "identity":
        replacement = file.with_name("replacement")
        replacement.write_bytes(file.read_bytes())
        replacement.replace(file)
    else:
        file.unlink()
    value = payload(file, tool=tool, token="w1")
    output = hooks.run_hook("PreToolUse", value, home=home)
    assert set(output) == {"hookSpecificOutput"}
    assert set(output["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    text = output["hookSpecificOutput"]["additionalContext"]
    assert len(text.encode()) <= 512
    assert "本 session" in text
    assert str(file) not in text
    assert "CANARY" not in text
    assert api.status(home=home, session="s")["baseline_count"] == 0
    assert hooks.run_hook("PreToolUse", value, home=home) == {}


def test_no_difference_is_not_a_green_hook(desk):
    home, file = desk
    read(home, file)
    assert hooks.run_hook("PreToolUse", payload(file, tool="Write", token="w"), home=home) == {}


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_completion_clears_explicit_and_hook_baseline(desk, event):
    home, file = desk
    read(home, file)
    api.observe(home=home, path=file, session="s")
    assert api.status(home=home, session="s")["baseline_count"] == 2
    value = process(home, payload(file, tool="Edit", token="w", event=event))
    assert value["reason"] == "baseline_cleared"
    assert api.status(home=home, session="s")["baseline_count"] == 0
    assert api.compare(home=home, path=file, session="s")["status"] == "unknown"


def test_explicit_not_used_as_hook_read(desk):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    file.write_text("changed")
    assert hooks.run_hook("PreToolUse", payload(file, tool="Edit", token="w"), home=home) == {}


def test_hook_not_used_as_explicit_observation(desk):
    home, file = desk
    read(home, file)
    assert api.compare(home=home, path=file, session="s")["status"] == "unknown"


def test_session_isolation_and_subagent_zero_io(desk, monkeypatch):
    home, file = desk
    read(home, file)
    file.write_text("changed")
    assert hooks.run_hook("PreToolUse", payload(file, tool="Edit", token="w", session="other"), home=home) == {}
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("SubagentStart 唔應 IO"))
    value = hooks.run_hook("SubagentStart", {"hook_event_name": "SubagentStart", "session_id": "s"}, home=home)
    assert "未知" in value["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("field", ["session_id", "tool_use_id", "tool_input"])
def test_missing_pair_input_never_mints(desk, field):
    home, file = desk
    before = payload(file)
    before.pop(field)
    assert process(home, before)["status"] == "unknown"
    assert process(home, payload(file, event="PostToolUse"))["status"] == "unknown"
    assert api.status(home=home, session="s")["status"] == "unknown"


@pytest.mark.parametrize("which", ["token", "path", "inputs", "response"])
def test_missing_post_invalidates_pending(desk, which):
    home, file = desk
    process(home, payload(file))
    post = payload(file, event="PostToolUse")
    if which == "token":
        post.pop("tool_use_id")
    elif which == "path":
        post["tool_input"].pop("file_path")
    elif which == "inputs":
        post["tool_input"] = None
    else:
        post.pop("tool_response")
    assert process(home, post)["status"] == "unknown"
    assert process(home, payload(file, event="PostToolUse"))["status"] == "unknown"


@pytest.mark.parametrize("lines", [1, 1999, 2000, 2001])
@pytest.mark.parametrize("trailing_lf", [False, True])
@pytest.mark.parametrize("extra_line", [0, 1])
def test_read_line_conventions_and_default_cap(desk, lines, trailing_lf, extra_line):
    home, file = desk
    file.write_bytes(b"\n".join([b"x"] * lines) + (b"\n" if trailing_lf else b""))
    before = process(home, payload(file))
    assert before["reason"] == ("read_pending" if lines <= 2000 else "read_bounds_unknown")
    post = payload(file, event="PostToolUse")
    reported = lines + extra_line
    post["tool_response"]["file"].update(numLines=reported, totalLines=reported)
    value = process(home, post)
    expected = reported <= 2000 and (extra_line == 0 or trailing_lf)
    assert value["status"] == ("observed" if expected else "unknown")
    assert value["read_bytes"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == int(expected)


@pytest.mark.parametrize("data,reported", [
    (b"", 0), (b"", 1), (b"\n", 2), (b"x\n\n", 3),
    (b"x\r\n", 2), (b"x" * 2001 + b"\n", 2),
])
def test_terminal_empty_line_empty_cr_and_long_line_controls(desk, data, reported):
    home, file = desk
    file.write_bytes(data)
    process(home, payload(file))
    post = payload(file, event="PostToolUse")
    post["tool_response"]["file"].update(numLines=reported, totalLines=reported)
    value = process(home, post)
    expected = data in (b"\n", b"x\n\n")
    assert value["status"] == ("observed" if expected else "unknown")
    assert value["read_bytes"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == int(expected)


@pytest.mark.parametrize("num_lines,total_lines", [(2000, 2001), (2001, 2001), (1999, 2000), (2000, 1999)])
def test_reported_overflow_or_partial_never_baseline(desk, num_lines, total_lines):
    home, file = desk
    file.write_bytes(b"x\n" * 2000)
    assert process(home, payload(file))["reason"] == "read_pending"
    post = payload(file, event="PostToolUse")
    post["tool_response"]["file"].update(numLines=num_lines, totalLines=total_lines)
    assert process(home, post)["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("stage", ["pre", "post", "both"])
@pytest.mark.parametrize("field,value", [("offset", 0), ("offset", 1), ("limit", 2000), ("limit", 10),
                                         ("offset", None), ("pages", "1"), ("unknown", True)])
def test_partial_or_unrecognized_request_never_baseline(desk, stage, field, value):
    home, file = desk
    before = payload(file)
    after = payload(file, event="PostToolUse")
    after["tool_response"]["file"].update(numLines=2, totalLines=2)
    if stage in ("pre", "both"):
        before["tool_input"][field] = value
    if stage in ("post", "both"):
        after["tool_input"][field] = value
    assert process(home, before)["reason"] == ("read_pending" if stage == "post" else "unsupported_read")
    assert process(home, after)["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("reported", [1, 2])
@pytest.mark.parametrize("alter", ["top-extra", "file-extra", "wrong-total", "wrong-start", "missing-content", "wrong-content", "bare", "type", "bool-lines"])
def test_unknown_response_shape(desk, reported, alter):
    home, file = desk
    process(home, payload(file))
    post = payload(file, event="PostToolUse")
    response = post["tool_response"]
    response["file"].update(numLines=reported, totalLines=reported)
    if alter == "top-extra":
        response["truncated"] = False
    elif alter == "file-extra":
        response["file"]["truncated"] = False
    elif alter == "wrong-total":
        response["file"]["totalLines"] += 1
    elif alter == "wrong-start":
        response["file"]["startLine"] = 2
    elif alter == "missing-content":
        response["file"].pop("content")
    elif alter == "wrong-content":
        response["file"]["content"] = []
    elif alter == "bare":
        post["tool_response"] = "CANARY"
    elif alter == "type":
        response["type"] = "image"
    else:
        response["file"]["numLines"] = True
    assert process(home, post)["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("field", ["filePath", "content", "numLines", "startLine", "totalLines"])
def test_terminal_empty_line_requires_all_metadata(desk, field):
    home, file = desk
    process(home, payload(file))
    post = payload(file, event="PostToolUse")
    post["tool_response"]["file"].update(numLines=2, totalLines=2)
    post["tool_response"]["file"].pop(field)
    assert process(home, post)["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("filePath", "/different.txt"), ("numLines", 2.0), ("startLine", True),
    ("totalLines", True), ("totalLines", 2.0), ("numLines", 1), ("totalLines", 1),
])
def test_terminal_empty_line_rejects_bad_metadata(desk, field, value):
    home, file = desk
    process(home, payload(file))
    post = payload(file, event="PostToolUse")
    post["tool_response"]["file"].update(numLines=2, totalLines=2)
    post["tool_response"]["file"][field] = value
    assert process(home, post)["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("change", ["content", "identity", "trailing-lf", "version"])
def test_terminal_empty_line_requires_matching_snapshots(desk, change):
    home, file = desk
    assert process(home, payload(file))["reason"] == "read_pending"
    if change == "content":
        file.write_bytes(b"changed\n")
    elif change == "identity":
        replacement = file.with_name("replacement")
        replacement.write_bytes(file.read_bytes())
        replacement.replace(file)
    elif change == "trailing-lf":
        file.write_bytes(b"initial")
    else:
        file.chmod(0o600 if file.stat().st_mode & 0o777 != 0o600 else 0o644)
    post = payload(file, event="PostToolUse")
    post["tool_response"]["file"].update(numLines=2, totalLines=2)
    value = process(home, post)
    assert value["status"] == "unknown" and value["read_bytes"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0
    assert process(home, post)["reason"] == "read_unpaired"


@pytest.mark.parametrize("kind", ["duplicate-pre", "duplicate-post", "different-path", "different-tool"])
def test_tuple_collision_unknown(desk, kind):
    home, file = desk
    process(home, payload(file))
    if kind == "duplicate-pre":
        assert process(home, payload(file))["reason"] == "tuple_collision"
    elif kind == "duplicate-post":
        assert process(home, payload(file, event="PostToolUse"))["status"] == "observed"
    else:
        other = file.with_name("other.txt")
        other.write_text("other")
        event = payload(other, token="r1", tool="Write" if kind == "different-tool" else "Read")
        assert process(home, event)["reason"] == "tuple_collision"
    assert process(home, payload(file, event="PostToolUse"))["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


def test_cross_session_same_token_does_not_collide(desk):
    home, file = desk
    read(home, file, session="first")
    read(home, file, session="second")
    assert api.status(home=home, session="first")["baseline_count"] == 1
    assert api.status(home=home, session="second")["baseline_count"] == 1


def test_read_mutation_epoch_race_never_pairs(desk):
    home, file = desk
    process(home, payload(file))
    process(home, payload(file, tool="Write", token="w", session="other"))
    process(home, payload(file, tool="Write", token="w", session="other", event="PostToolUseFailure"))
    assert process(home, payload(file, event="PostToolUse"))["status"] == "unknown"
    read(home, file, token="r2")


def test_mutation_pending_prevents_hook_and_explicit_read(desk):
    home, file = desk
    process(home, payload(file, tool="Write", token="w", session="other"))
    assert process(home, payload(file))["status"] == "unknown"
    assert api.observe(home=home, path=file, session="s")["reason"] == "mutation_pending"


def test_external_change_between_pre_post_unknown(desk):
    home, file = desk
    process(home, payload(file))
    file.write_text("other\n")
    assert process(home, payload(file, event="PostToolUse"))["reason"] == "read_race"


def test_interleaved_reads_do_not_claim_each_others_bytes(desk):
    home, file = desk
    process(home, payload(file, token="r1"))
    process(home, payload(file, token="r2"))
    assert process(home, payload(file, event="PostToolUse", token="r1"))["status"] == "unknown"
    assert process(home, payload(file, event="PostToolUse", token="r2"))["status"] == "observed"


def test_stale_baseline_no_preflight_warning(desk, monkeypatch):
    home, file = desk
    read(home, file)
    _, state = Store(home=home).read()
    at = max(item["at"] for item in state["entries"].values())
    elapsed = max(item["elapsed"] for item in state["entries"].values())
    boot, _ = core.system_clock()
    monkeypatch.setattr(time, "time", lambda: at + TTL + 1)
    monkeypatch.setattr(core, "system_clock", lambda: (boot, elapsed + TTL + 1))
    file.write_text("changed")
    assert hooks.run_hook("PreToolUse", payload(file, tool="Write", token="w"), home=home) == {}


def test_full_cache_does_not_rollback_read_invalidation(desk):
    home, file = desk
    process(home, payload(file))
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        template = next(iter(state["entries"].values()))
        for number in range(MAX_ENTRIES - 1):
            clone = copy.deepcopy(template)
            clone["session"] = digest(key, "session", number)
            assert put(state, digest(key, "entry", number), clone)
    assert process(home, payload(file, token="new"))["reason"] == "cache_full"
    assert process(home, payload(file, token="r1", event="PostToolUse"))["status"] == "unknown"
    assert api.status(home=home, session="s")["baseline_count"] == 0


def test_full_cache_post_cannot_claim_unsaved_baseline(desk):
    home, file = desk
    process(home, payload(file))
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        template = next(iter(state["entries"].values()))
        for number in range(MAX_ENTRIES - 1):
            clone = copy.deepcopy(template)
            clone["session"] = digest(key, "session", number)
            assert put(state, digest(key, "entry", number), clone)
    assert process(home, payload(file, event="PostToolUse"))["reason"] == "cache_full"
    assert api.status(home=home, session="s")["baseline_count"] == 0


def test_lock_timeout_fail_open_under_budget(desk):
    home, file = desk
    read(home, file)
    lock = home / ".local/state/coedit-radar/.lock"
    with lock.open("r+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        output = hooks.run_hook("PreToolUse", payload(file, tool="Write", token="w"), home=home)
        elapsed = time.monotonic() - start
    assert elapsed < 0.250
    assert set(output["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert "未知" in output["hookSpecificOutput"]["additionalContext"]


def test_cache_canaries_absent_and_bash_no_pass(desk):
    home, file = desk
    read(home, file)
    event = payload(file, tool="Bash", event="PostToolUse")
    event["tool_input"] = {"command": "COMMAND-CANARY"}
    event["tool_response"] = {"exit_code": 0, "stdout": "STDOUT-CANARY"}
    assert hooks.run_hook("PostToolUse", event, home=home) == {}
    state = (home / ".local/state/coedit-radar/cache.json").read_text()
    assert "CANARY" not in state
    assert str(file) not in state


def test_unknown_mutation_shape_not_compare(desk):
    home, file = desk
    read(home, file)
    file.write_text("changed")
    event = payload(file, tool="Edit", token="w")
    event["tool_input"]["future_field"] = True
    assert hooks.run_hook("PreToolUse", event, home=home) == {}
    assert api.status(home=home, session="s")["baseline_count"] == 0


@pytest.mark.parametrize("tool", [None, [], {}, 1, True])
def test_unknown_tool_shape_zero_io(desk, monkeypatch, tool):
    home, file = desk
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("未知 tool 唔應 open"))
    event = payload(file)
    event["tool_name"] = tool
    assert hooks.run_hook("PreToolUse", event, home=home) == {}


def test_hook_secret_path_zero_io(desk, monkeypatch):
    home, file = desk
    secret = file.with_name(".env")
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("secret hook 唔應 open"))
    output = hooks.run_hook("PreToolUse", payload(secret), home=home)
    assert "未知" in output["hookSpecificOutput"]["additionalContext"]


def test_missing_token_mutations_guard_each_path(desk):
    home, file = desk
    other = file.with_name("other")
    other.write_text("other")
    for path in (file, other):
        event = payload(path, tool="Write")
        event.pop("tool_use_id")
        assert process(home, event)["status"] == "unknown"
    for path in (file, other):
        assert api.observe(home=home, path=path, session="s")["reason"] == "mutation_pending"


def test_mutation_reused_tuple_keeps_both_paths_unknown(desk):
    home, file = desk
    other = file.with_name("other")
    other.write_text("other")
    process(home, payload(file, tool="Write", token="w"))
    assert process(home, payload(other, tool="Write", token="w"))["reason"] == "mutation_replayed"
    for path in (file, other):
        assert api.observe(home=home, path=path, session="s")["reason"] == "mutation_pending"


def test_cross_tool_collision_does_not_leave_mutation_untracked(desk):
    home, file = desk
    process(home, payload(file, token="same"))
    other = file.with_name("other")
    other.write_text("other")
    assert process(home, payload(other, tool="Write", token="same"))["reason"] == "tuple_collision"
    assert api.observe(home=home, path=other, session="s")["reason"] == "mutation_pending"


def fill_explicit_cache(home, file, count):
    assert api.observe(home=home, path=file, session="reader")["status"] == "observed"
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        original = next(iter(state["entries"].values()))
        for number in range(count - 1):
            clone = copy.deepcopy(original)
            clone["session"] = digest(key, "filler-session", number)
            assert put(state, digest(key, "filler-entry", number), clone)
    assert len(Store(home=home).read()[1]["entries"]) == count


@pytest.mark.parametrize("count", [255, 256])
def test_full_explicit_cache_keeps_pending_mutation_unknown(desk, count):
    home, file = desk
    fill_explicit_cache(home, file, count)
    process(home, payload(file, tool="Write", session="writer", token="w"))
    # Reader 有 existing slot，writer 冇可清 baseline；255 對照同 256 都唔可遺忘。
    assert api.compare(home=home, path=file, session="reader")["reason"] == "mutation_pending"
    assert api.observe(home=home, path=file, session="reader")["reason"] == "mutation_pending"
    assert api.compare(home=home, path=file, session="reader")["status"] == "unknown"
    assert process(home, payload(file, session="reader", token="new-read"))["status"] == "unknown"
    assert process(home, payload(file, session="reader", token="new-read", event="PostToolUse"))["status"] == "unknown"
    assert len(Store(home=home).read()[1]["entries"]) <= MAX_ENTRIES
    assert (home / ".local/state/coedit-radar/cache.json").stat().st_size <= MAX_CACHE


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
@pytest.mark.parametrize("missing_token", [False, True])
def test_multiple_overflow_completions_cannot_clear_uncertainty(desk, event, missing_token):
    home, file = desk
    other = file.with_name("other.txt")
    other.write_text("other\n")
    fill_explicit_cache(home, file, MAX_ENTRIES)
    for path, token in ((file, "w1"), (other, "w2")):
        process(home, payload(path, tool="Write", session="writer", token=token))
    completion = payload(file, tool="Write", session="writer", token="w1", event=event)
    if missing_token:
        completion.pop("tool_use_id")
    process(home, completion)
    # 明知另一個 overflow pending；仲要唔讓第一個無法核實嘅 completion 清全域 guard。
    for path in (file, other):
        assert api.observe(home=home, path=path, session="reader")["reason"] == "mutation_pending"
    assert process(home, payload(other, token="r-new"))["status"] == "unknown"
    assert process(home, payload(other, token="r-new", event="PostToolUse"))["status"] == "unknown"


def test_overflow_guard_tracks_latest_ttl_without_reviving_old_reads(desk, monkeypatch):
    home, file = desk
    clocks = {"wall": 2_000_000_000.0, "elapsed": 10_000.0}
    monkeypatch.setattr(time, "time", lambda: clocks["wall"])
    monkeypatch.setattr(core, "system_clock", lambda: ("11111111-1111-4111-8111-111111111111", clocks["elapsed"]))
    process(home, payload(file, session="pending-reader", token="pending"))
    read(home, file, session="consumed-reader", token="consumed")
    # 一張 pending read 喺另一個 path，overflow 仍然要 poison；唔 evict tombstones。
    other = file.with_name("other.txt")
    other.write_text("other\n")
    process(home, payload(other, session="pending-reader", token="other-pending"))
    protected = {index for index, item in Store(home=home).read()[1]["entries"].items()
                 if item["kind"] == "read_token"}
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        template = next(item for item in state["entries"].values() if item["kind"] == "baseline")
        for number in range(MAX_ENTRIES - len(state["entries"])):
            clone = copy.deepcopy(template)
            clone["session"] = digest(key, "filler", number)
            assert put(state, digest(key, "entry", number), clone)
    process(home, payload(file, tool="Write", session="writer", token="overflow-1"))
    first = Store(home=home).read()[1]
    assert protected <= first["entries"].keys()
    assert first["overflow"] is not None
    assert all(first["entries"][index]["phase"] != "pending" for index in protected)
    clocks["elapsed"] += 100
    clocks["wall"] -= 60
    process(home, payload(other, tool="Write", session="writer", token="overflow-2"))
    guard = Store(home=home).read()[1]["overflow"]
    assert guard["elapsed"] == 10_100
    clocks["elapsed"] = 10_000 + TTL + 1
    # 第一批 entry 已 expired，最後 overflow 仲生效；新 slot 都唔 mint baseline。
    assert api.observe(home=home, path=other, session="fresh")["reason"] == "mutation_pending"
    assert process(home, payload(other, token="during-guard"))["status"] == "unknown"
    clocks["elapsed"] = 10_100 + TTL
    clocks["wall"] -= 60
    assert process(home, payload(other, token="during-guard", event="PostToolUse"))["status"] == "unknown"
    assert process(home, payload(other, session="pending-reader", token="other-pending", event="PostToolUse"))["status"] == "unknown"
    assert api.status(home=home, session="consumed-reader")["status"] == "unknown"
    assert api.observe(home=home, path=other, session="fresh")["status"] == "observed"
    assert api.compare(home=home, path=other, session="fresh")["status"] == "no_observed_difference"


@pytest.mark.parametrize("after_publish", [False, True])
def test_overflow_publication_fault_is_unknown_not_success(desk, monkeypatch, after_publish):
    home, file = desk
    fill_explicit_cache(home, file, MAX_ENTRIES)
    cache = home / ".local/state/coedit-radar/cache.json"
    before = cache.read_bytes()
    write = store.atomic_write
    def fail(fd, name, data, **kwargs):
        if name == "cache.json":
            if after_publish:
                write(fd, name, data, **kwargs)
            raise RadarError("state_io")
        return write(fd, name, data, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(store, "atomic_write", fail)
        output = hooks.run_hook("PreToolUse", payload(file, tool="Write", session="writer", token="w"), home=home)
    assert "未知" in output["hookSpecificOutput"]["additionalContext"]
    if after_publish:
        assert api.compare(home=home, path=file, session="reader")["reason"] == "mutation_pending"
        assert api.observe(home=home, path=file, session="reader")["reason"] == "mutation_pending"
    else:
        # 無法 durable write 就無法保證後續 process 記得；呢次只可報 unknown，唔偽報成功。
        assert cache.read_bytes() == before


@pytest.mark.parametrize("event_kind", ["read-post", "write-pre", "explicit-compare"])
def test_ttl_crossing_during_observer_never_upgrades(desk, monkeypatch, event_kind):
    home, file = desk
    clocks = {"elapsed": 10_000.0}
    monkeypatch.setattr(core, "system_clock", lambda: ("11111111-1111-4111-8111-111111111111", clocks["elapsed"]))
    if event_kind == "read-post":
        process(home, payload(file))
    elif event_kind == "write-pre":
        read(home, file)
    else:
        assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    clocks["elapsed"] += TTL - 0.1
    module = api if event_kind == "explicit-compare" else hooks
    observer = module.observe_file
    def cross(*args, **kwargs):
        observed = observer(*args, **kwargs)
        clocks["elapsed"] += 1
        return observed
    monkeypatch.setattr(module, "observe_file", cross)
    if event_kind == "read-post":
        value = process(home, payload(file, event="PostToolUse"))
    elif event_kind == "write-pre":
        value = process(home, payload(file, tool="Write", token="w"))
    else:
        value = api.compare(home=home, path=file, session="s")
    assert value["status"] == "unknown"
    assert value["read_bytes"] == "unknown"


def test_readonly_compare_cas_includes_overflow_guard(desk, monkeypatch):
    home, file = desk
    other = file.with_name("unrelated.txt")
    other.write_text("other\n")
    fill_explicit_cache(home, file, MAX_ENTRIES)
    observer = api.observe_file
    def racing(*args, **kwargs):
        observed = observer(*args, **kwargs)
        process(home, payload(other, tool="Write", session="writer", token="overflow"))
        return observed
    monkeypatch.setattr(api, "observe_file", racing)
    assert api.compare(home=home, path=file, session="reader")["reason"] == "cache_race"
    assert api.status(home=home, session="reader")["status"] == "unknown"
