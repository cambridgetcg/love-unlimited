"""Advisory lifecycle hooks：本地、有界、失敗就靜默，唔會喚醒工作。"""

from __future__ import annotations

import hashlib
import re
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .core import SAFE_CODES, SCHEMA_VERSION, build_status, render_brief, status_fingerprint, utc_now
from .research import reflect
from .sources import collect_local
from .store import Store

EVENTS = frozenset({"SessionStart", "SubagentStart", "UserPromptSubmit", "Stop"})
BUDGET_SECONDS = 1.5
OBSERVE_SECONDS = 60
DELIVERY_SECONDS = 60
LOCK_WAIT_SECONDS = 0.05
MAX_SESSIONS = 256
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class _HookDeadline(Exception):
    pass


@contextmanager
def _budget(deadline):
    # 主 thread 有硬限時；其他 caller 仍然傳 deadline 落每個 bounded IO。
    armed = threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer")
    if not armed:
        yield
        return
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(_signum, _frame):
        raise _HookDeadline()

    remaining = max(0.000001, deadline - started)
    if old_timer[0] > 0:
        remaining = min(remaining, old_timer[0])
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.000001, old_timer[0] - (time.monotonic() - started)),
                             old_timer[1])


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity(event, payload):
    if not isinstance(event, str) or event not in EVENTS or not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") != event:
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not _ID.fullmatch(session):
        return None
    cwd = payload.get("cwd", "")
    if not isinstance(cwd, str) or len(cwd.encode("utf-8")) > 4096 or "\x00" in cwd:
        return None
    # 唔 resolve payload path，亦唔掂 transcript/prompts 或其他欄位。
    cwd_hash = _hash(cwd)
    return _hash(session + "\x00" + cwd_hash), cwd_hash


def _finite_time(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1e12


def _claim_observe(store, now, deadline):
    claimed = False

    def update(value):
        nonlocal claimed
        value = dict(value) if isinstance(value, dict) else {}
        prior = value.get("observe", {})
        at = prior.get("claimed_at") if isinstance(prior, dict) else None
        if _finite_time(at) and now - at < OBSERVE_SECONDS:
            return value
        value["observe"] = {"claimed_at": now}
        claimed = True
        return value

    store.mutate_cache("throttle", update, default={}, deadline=deadline)
    return claimed


def _observe(store, home, deadline):
    report = collect_local(home / ".codex", cursors=store.read_cache("cursors", {}), deadline=deadline)
    observations = report.get("observations", [])
    recorded = store.append(observations, deadline=deadline)
    diagnostics = [*report.get("diagnostics", []), *recorded.get("diagnostics", [])]
    safe = sorted({code if isinstance(code, str) and code in SAFE_CODES else "unknown_diagnostic"
                   for code in diagnostics})[:100]
    store.write_cache("diagnostics", safe, deadline=deadline)
    # journal 完整成功先可以推 cursor；pause/partial 永遠留低重試位置。
    if (not recorded.get("recording_paused", False)
            and recorded.get("added", 0) + recorded.get("duplicates", 0) == len(observations)):
        store.write_cache("cursors", report.get("cursors", {}), deadline=deadline)


def _claim_delivery(store, session_hash, fingerprint, now, deadline, *, force=False):
    claimed = False

    def update(value):
        nonlocal claimed
        value = dict(value) if isinstance(value, dict) else {}
        raw = value.get("deliveries")
        deliveries = {key: item for key, item in raw.items()
                      if isinstance(key, str) and isinstance(item, dict)
                      and _finite_time(item.get("at"))} if isinstance(raw, dict) else {}
        prior = deliveries.get(session_hash, {})
        if not force and (prior.get("fingerprint") == fingerprint
                          or _finite_time(prior.get("at")) and now - prior["at"] < DELIVERY_SECONDS):
            return value
        deliveries[session_hash] = {"at": now, "fingerprint": fingerprint}
        value["deliveries"] = dict(sorted(deliveries.items(), key=lambda entry: entry[1]["at"])[-MAX_SESSIONS:])
        claimed = True
        return value

    store.mutate_cache("throttle", update, default={}, deadline=deadline)
    return claimed


def _receipt(store, event, session_hash, cwd_hash, fingerprint, deadline):
    def update(value):
        # 只保留自己固定 schema 嘅記錄，唔傳播外來 cache 欄位。
        events = value.get("events", []) if isinstance(value, dict) else []
        events = [entry for entry in events if isinstance(entry, dict)
                  and set(entry) == {"event", "at", "session_hash", "cwd_hash", "fingerprint"}
                  and entry.get("event") in EVENTS][-63:] if isinstance(events, list) else []
        events.append({"event": event, "at": utc_now(), "session_hash": session_hash,
                       "cwd_hash": cwd_hash, "fingerprint": fingerprint})
        return {"schema": SCHEMA_VERSION, "events": events}

    store.mutate_cache("receipts", update, default={}, deadline=deadline)


def _cached_status(store):
    observations = store.read_latest(cache_only=True)
    try:
        research = store.read_cache("reflection", {})
    except _HookDeadline:
        raise
    except Exception:
        # 壞研究 cache 唔可以抹走已驗證嘅用量 evidence。
        research = {}
    try:
        diagnostics = store.read_cache("diagnostics", [])
    except _HookDeadline:
        raise
    except Exception:
        diagnostics = ["state_corrupt"]
    return build_status(observations, research=research, diagnostics=diagnostics)


def _output(event, status):
    brief = render_brief(status, max_bytes=512 if event == "SubagentStart" else 1024)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": brief}} if brief else None


def _lock_deadline(deadline):
    # 採樣 claim／receipt 係附帶記錄，唔值得用晒整個 hook 時限等把鎖。
    return min(deadline, time.monotonic() + LOCK_WAIT_SECONDS)


def _sample_if_due(store, home, deadline):
    try:
        if _claim_observe(store, time.time(), _lock_deadline(deadline)):
            _observe(store, home, deadline)
    except _HookDeadline:
        raise
    except Exception:
        # 採樣錯誤只代表冇新 evidence；唔刷新舊 evidence 時間。
        pass


def run_hook(event, payload, *, home=None, deadline=None):
    """回正確 event 嘅 additionalContext；共用 wrapper 剩餘時限，唔阻擋工作。"""
    output = None
    started = time.monotonic()
    try:
        deadline = min(deadline, started + BUDGET_SECONDS) if deadline is not None else started + BUDGET_SECONDS
        if deadline <= started:
            return None
        with _budget(deadline):
            identity = _identity(event, payload)
            if identity is None:
                return None
            session_hash, cwd_hash = identity
            home = Path(home) if home is not None else Path.home()
            store = Store(home=home)
            if event == "Stop":
                _sample_if_due(store, home, deadline)
                reflect(store, deadline=deadline)
                return None

            status = _cached_status(store)
            if event == "SubagentStart":
                # 唔讀 journal／source，亦唔寫 claim、receipt 或任何 state。
                return _output(event, status)
            if event == "SessionStart":
                # Resume／compact 已失去舊 context；同一 session 每次都要再有燈。
                # 先保留已驗證 cache 嘅 brief，鎖忙或採樣耗盡時限亦唔會吞咗佢。
                output = _output(event, status)
            if (event == "UserPromptSubmit" or not status["sources"]
                    or any(item["freshness"] != "fresh" for item in status["sources"])):
                _sample_if_due(store, home, deadline)
                status = _cached_status(store)
            candidate = _output(event, status)
            if not candidate:
                return output
            if event == "SessionStart":
                output = candidate
            fingerprint = status_fingerprint(status)
            # 只有 prompt delivery 需要「material change + 60s」原子 claim。
            # SessionStart 只更新下一次 prompt 嘅 baseline，記錄失敗亦照出 brief。
            if not _claim_delivery(store, session_hash, fingerprint, time.time(),
                                   _lock_deadline(deadline), force=event == "SessionStart"):
                return output
            output = candidate
            _receipt(store, event, session_hash, cwd_hash, fingerprint, _lock_deadline(deadline))
    except Exception:
        # 唔將 exception/payload 放入 stdout、diagnostics 或研究卡。
        pass
    return output
