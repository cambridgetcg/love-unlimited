"""Pre/Post Read 只配 metadata；Pre Edit/Write 只回 additionalContext，唔攔工具。"""
from __future__ import annotations

from pathlib import Path

from .api import brief
from .core import (EXPLICIT, HOOK, UNKNOWN, bounded, compare_observations, end_time,
                   identifier, render_brief, result, safe_path)
from .observer import (eligible_path, observe_file, read_bounds_supported,
                       read_request_supported, read_response_supported)
from .store import (Store, baseline_key, entry, fresh, ids, invalidate_reads, mutation_key,
                    mutating, put, read_key, retire, state_clock)

EVENTS = frozenset(("PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart"))
FILE_TOOLS = frozenset(("Edit", "Write"))


def context(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def _clear_baselines(state, key, session_id, path_id):
    for source in (EXPLICIT, HOOK):
        state["entries"].pop(baseline_key(key, session_id, path_id, source), None)


def _collision(state, session_id):
    # 同 tuple 重用可能係 replay 或另一個 tool call；唔自行揀其中一張。
    for index, item in list(state["entries"].items()):
        if item["session"] == session_id and item["source"] == HOOK:
            if item["kind"] == "baseline":
                del state["entries"][index]
            elif item["kind"] == "read_token":
                retire(item, "poisoned")


def _uncertain_mutation(state, key, session_id, path_id, now):
    # 碰撞／缺 token 冇法配 completion；只留 path-scoped pending 到 TTL。
    index = mutation_key(key, session_id, ["unknown", path_id])
    return put(state, index, entry("mutation", session_id, path_id, HOOK, now, "pending"))


def mutation_supported(tool, inputs):
    if not isinstance(inputs, dict):
        return False
    if tool == "Write":
        return set(inputs) == {"file_path", "content"} and isinstance(inputs["content"], str)
    required = {"file_path", "old_string", "new_string"}
    return (required <= set(inputs) <= required | {"replace_all"}
            and isinstance(inputs["old_string"], str) and isinstance(inputs["new_string"], str)
            and type(inputs.get("replace_all", False)) is bool)


def process_event(event, payload, *, home, deadline):
    tool = payload.get("tool_name")
    inputs = payload.get("tool_input")
    session = payload.get("session_id")
    token = payload.get("tool_use_id")
    if tool not in FILE_TOOLS and tool != "Read":
        return result(reason="unsupported_tool", source=HOOK)
    path = safe_path(inputs.get("file_path")) if isinstance(inputs, dict) else None
    if not identifier(session):
        return result(reason="unpaired_input", source=HOOK)
    if path is None:
        # 有 session、冇 path 嘅 completion 唔可以留下可借用嘅舊 baseline。
        with Store(home=home).transaction(deadline=deadline, create=False) as (key, state):
            if key is not None:
                session_id, _ = ids(key, session, "")
                _collision(state, session_id)
                if tool in FILE_TOOLS:
                    for index, item in list(state["entries"].items()):
                        if item["session"] == session_id and item["kind"] == "baseline":
                            del state["entries"][index]
        return result(reason="unpaired_input", source=HOOK)
    eligible_path(path)
    token_valid = identifier(token)
    value = result(reason="unpaired_input", source=HOOK)
    # 冇前置 state 嘅 Post 唔建 cache；未知 token 嘅 Read 亦唔自行觀察。
    create = event == "PreToolUse" and (tool in FILE_TOOLS or token_valid)
    with Store(home=home).transaction(deadline=deadline, create=create) as (key, state):
        if key is None:
            return value
        session_id, path_id = ids(key, session, path)
        baseline_index = baseline_key(key, session_id, path_id, HOOK)
        now = state_clock(state, key)
        other_index = ((mutation_key if tool == "Read" else read_key)(key, session_id, token)
                       if token_valid else None)
        if other_index in state["entries"]:
            _collision(state, session_id)
            if tool in FILE_TOOLS:
                _clear_baselines(state, key, session_id, path_id)
                if event == "PreToolUse":
                    invalidate_reads(state, path_id)
                    _uncertain_mutation(state, key, session_id, path_id, now)
            return result(reason="tuple_collision", source=HOOK)
        if tool == "Read":
            if event == "PreToolUse":
                state["entries"].pop(baseline_index, None)
                invalidate_reads(state, path_id)
                if not token_valid:
                    return value
                index = read_key(key, session_id, token)
                if index in state["entries"]:
                    _collision(state, session_id)
                    return result(reason="tuple_collision", source=HOOK)
                snapshot = None
                reason = "unsupported_read"
                if read_request_supported(inputs) and not mutating(state, path_id, now):
                    candidate = observe_file(path, key=key, deadline=deadline)
                    if read_bounds_supported(candidate):
                        snapshot = candidate
                    else:
                        reason = candidate["reason"] if candidate["status"] != "observed" else "read_bounds_unknown"
                phase = "pending" if snapshot else "poisoned"
                stored = put(state, index, entry("read_token", session_id, path_id, HOOK, now, phase, snapshot))
                value = result(reason=("read_pending" if snapshot else reason) if stored else "cache_full", source=HOOK)
            elif token_valid:
                index = read_key(key, session_id, token)
                pending = state["entries"].get(index)
                if pending is None:
                    state["entries"].pop(baseline_index, None)
                    invalidate_reads(state, path_id)
                    return result(reason="read_unpaired", source=HOOK)
                if pending["path"] != path_id:
                    _collision(state, session_id)
                    return result(reason="tuple_collision", source=HOOK)
                if pending["phase"] != "pending":
                    state["entries"].pop(baseline_index, None)
                    return result(reason="read_unpaired", source=HOOK)
                snapshot = None
                reason = "read_shape_unknown"
                if (event == "PostToolUse" and read_request_supported(inputs)
                        and not mutating(state, path_id, now)):
                    candidate = observe_file(path, key=key, deadline=deadline)
                    if (fresh(pending, state_clock(state, key)) and read_bounds_supported(candidate)
                            and all(pending[field] == candidate[field] for field in ("identity", "digest", "version"))):
                        # 尾 LF 只由 matching Post snapshot 提供，唔存入 pending/cache。
                        if read_response_supported(payload.get("tool_response"), path=path, lines=pending["lines"],
                                                   trailing_lf=candidate["trailing_lf"]):
                            snapshot = candidate
                    else:
                        reason = "read_race"
                retire(pending, "consumed" if snapshot else "poisoned")
                if snapshot:
                    stored = put(state, baseline_index, entry("baseline", session_id, path_id, HOOK,
                                                              now, "observed", snapshot))
                    value = (result("observed", "paired_hook_observation", HOOK) if stored
                             else result(reason="cache_full", source=HOOK))
                else:
                    value = result(reason=reason, source=HOOK)
            else:
                state["entries"].pop(baseline_index, None)
                invalidate_reads(state, path_id)
        else:
            invalidate_reads(state, path_id)
            index = mutation_key(key, session_id, token if token_valid else ["unknown", path_id])
            marker = state["entries"].get(index)
            if event == "PreToolUse":
                if marker is not None:
                    _clear_baselines(state, key, session_id, path_id)
                    if marker["path"] != path_id:
                        _collision(state, session_id)
                    _uncertain_mutation(state, key, session_id, path_id, now)
                    return result(reason="mutation_replayed", source=HOOK)
                baseline = state["entries"].get(baseline_index)
                if token_valid and mutation_supported(tool, inputs) and baseline and not mutating(state, path_id, now):
                    current = observe_file(path, key=key, deadline=deadline)
                    value = (compare_observations(baseline, current, source=HOOK)
                             if fresh(baseline, state_clock(state, key))
                             else result(reason="baseline_stale", source=HOOK))
                # 預先退役自己 baseline；完成時再清，避免漏 Post 仍被借用。
                _clear_baselines(state, key, session_id, path_id)
                if not put(state, index, entry("mutation", session_id, path_id, HOOK, now, "pending")):
                    value = result(reason="cache_full", source=HOOK)
            else:
                _clear_baselines(state, key, session_id, path_id)
                if token_valid and marker is not None and marker["path"] == path_id:
                    retire(marker)
                value = result(reason="baseline_cleared", source=HOOK)
    return value


def run_hook(event, payload, *, home=None, deadline=None) -> dict:
    if not isinstance(event, str) or event not in EVENTS:
        return {}
    if not isinstance(payload, dict) or payload.get("hook_event_name") != event:
        return {}
    if event == "SubagentStart":
        # 唔用 parent session baseline；唔採樣，唔建 key／lock／directory。
        return context(event, brief(home=Path.home() if home is None else home))
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or (tool not in FILE_TOOLS and tool != "Read"):
        return {}
    try:
        end = end_time(deadline)
        with bounded(end):
            value = process_event(event, payload, home=Path.home() if home is None else home, deadline=end)
        if event == "PreToolUse" and payload.get("tool_name") in FILE_TOOLS and value["status"] == "changed":
            return context(event, render_brief(value))
        return {}
    except Exception:
        # Bookkeeping 失敗只講未知，唔阻止原工具，唔輸出 exception／payload。
        return context(event, UNKNOWN)
