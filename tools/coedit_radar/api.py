"""CLI 同 suite 共用嘅窄 API；explicit 同 hook 觀察永不混認。"""
from __future__ import annotations

from . import RadarError
from .core import (EXPLICIT, HOOK, bounded, compare_observations, end_time, identifier,
                   render_brief, result)
from .observer import eligible_path, observe_file
from .store import Store, baseline_key, entry, fresh, ids, mutating, put, state_clock

REASONS = frozenset(("state_corrupt", "state_unsafe", "state_io", "state_too_large", "cache_too_large",
                     "cache_full", "invalid_input", "invalid_deadline", "key_missing", "key_invalid",
                     "key_mismatch", "lock_timeout", "deadline", "unsafe_path", "secret_path",
                     "unsupported_type", "unsafe_file", "file_too_large", "file_race", "binary_file",
                     "file_unavailable", "clock_unavailable", "clock_anomaly"))


def failure(exc, *, source=None):
    reason = str(exc) if isinstance(exc, RadarError) and str(exc) in REASONS else "bookkeeping_unavailable"
    return result(reason=reason, source=source)


def status(*, home, cwd=None, session=None) -> dict:
    if not identifier(session):
        return result(reason="session_required")
    try:
        with bounded(end_time()):
            key, state = Store(home=home).read()
            if key is None:
                return result(reason="cache_missing")
            session_id, _ = ids(key, session, "")
            now = state_clock(state, key)
            items = [item for item in state["entries"].values() if item["session"] == session_id]
            baselines = [item for item in items if item["kind"] == "baseline" and fresh(item, now)
                         and not mutating(state, item["path"], now)]
            value = result("observed" if baselines else "unknown",
                           "session_observations" if baselines else "baseline_missing")
            value.update({"scope": "session", "baseline_count": len(baselines),
                          "sources": {source: sum(item["source"] == source for item in baselines)
                                      for source in (EXPLICIT, HOOK)},
                          "stale_count": sum(not fresh(item, now) for item in items),
                          "unknown_count": sum(item["kind"] == "read_token" and item["phase"] == "poisoned"
                                               and fresh(item, now) for item in items)})
            return value
    except Exception as exc:
        return failure(exc)


def brief(*, home, cwd=None, session=None, max_bytes=512) -> str:
    return render_brief(status(home=home, cwd=cwd, session=session), max_bytes=max_bytes)


def observe(*, home, path, session, deadline=None) -> dict:
    if not identifier(session):
        return result(reason="session_required", source=EXPLICIT)
    try:
        end = end_time(deadline)
        with bounded(end):
            path = eligible_path(path)
            value = result(reason="unavailable", source=EXPLICIT)
            with Store(home=home).transaction(deadline=end) as (key, state):
                session_id, path_id = ids(key, session, path)
                index = baseline_key(key, session_id, path_id, EXPLICIT)
                state["entries"].pop(index, None)
                now = state_clock(state, key)
                if mutating(state, path_id, now):
                    value = result(reason="mutation_pending", source=EXPLICIT)
                else:
                    snapshot = observe_file(path, key=key, deadline=end)
                    if snapshot["status"] == "observed":
                        stored = put(state, index, entry("baseline", session_id, path_id, EXPLICIT,
                                                         now, "observed", snapshot))
                        value = (result("observed", "observation_only", EXPLICIT) if stored
                                 else result(reason="cache_full", source=EXPLICIT))
                    else:
                        value = result(reason=snapshot["reason"], source=EXPLICIT)
            return value
    except Exception as exc:
        return failure(exc, source=EXPLICIT)


def compare(*, home, path, session, deadline=None) -> dict:
    if not identifier(session):
        return result(reason="session_required", source=EXPLICIT)
    try:
        end = end_time(deadline)
        with bounded(end):
            path = eligible_path(path)
            store = Store(home=home)
            key, state = store.read()
            if key is None:
                return result(reason="baseline_missing", source=EXPLICIT)
            session_id, path_id = ids(key, session, path)
            index = baseline_key(key, session_id, path_id, EXPLICIT)
            baseline = state["entries"].get(index)
            now = state_clock(state, key)
            if baseline is None or not fresh(baseline, now):
                return result(reason="baseline_missing" if baseline is None else "baseline_stale", source=EXPLICIT)
            if mutating(state, path_id, now):
                return result(reason="mutation_pending", source=EXPLICIT)
            snapshot = observe_file(path, key=key, deadline=end)
            # Read-only CAS：另一個 event 清／換 baseline，就唔借舊 observation 比較。
            after_key, after = store.read()
            if key != after_key or after != state:
                return result(reason="cache_race", source=EXPLICIT)
            if not fresh(baseline, state_clock(after, key)):
                return result(reason="baseline_stale", source=EXPLICIT)
            return compare_observations(baseline, snapshot, source=EXPLICIT)
    except Exception as exc:
        return failure(exc, source=EXPLICIT)
