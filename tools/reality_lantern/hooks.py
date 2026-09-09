"""只用本地 cached 收據嘅短提示；唔執行檢查，唔憑工具文字判成功。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import signal
import threading
import time

from .core import build_status, render_brief, status_fingerprint
from .snapshot import scope_candidates
from .store import Store

EVENTS = frozenset(("SessionStart", "UserPromptSubmit", "SubagentStart",
                    "PreToolUse", "PostToolUse", "PostToolUseFailure"))
FILE_TOOLS = frozenset(("Edit", "Write", "NotebookEdit"))
HOOK_SECONDS = 0.250
LOCK_SECONDS = 0.025
UNKNOWN = "實況燈｜目前證據未知；冇重新驗證，線上未驗證。"


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


@contextmanager
def _bounded(deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    use_alarm = threading.current_thread() is threading.main_thread()
    previous_handler = previous_timer = None
    started = time.monotonic()

    def expired(_number, _frame):
        raise TimeoutError()

    if use_alarm:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, min(remaining, previous_timer[0])
                         if previous_timer[0] else remaining)
    try:
        yield
        if time.monotonic() >= deadline:
            raise TimeoutError()
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                signal.setitimer(signal.ITIMER_REAL,
                                 max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                                 previous_timer[1])


def _locator(value) -> Path | None:
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(char) < 32 for char in value)):
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def _scope(state: dict, path: Path | None, key: bytes) -> str | None:
    if path is None:
        return None
    scopes = state.get("scopes", {})
    return next((item for item in scope_candidates(path, key=key) if item in scopes), None)


def _run(event: str, payload: dict, store: Store, deadline: float) -> dict:
    key = store.get_key(create=False)
    cwd = _locator(payload.get("cwd"))
    if key is None:
        if event in ("SessionStart", "SubagentStart"):
            return _context(event, render_brief(build_status(None),
                                               max_bytes=512 if event == "SubagentStart" else 768))
        return {}
    state = store.read_state()
    scope_id = _scope(state, cwd, key)
    tool = payload.get("tool_name")
    lock_deadline = lambda: min(deadline, time.monotonic() + LOCK_SECONDS)
    if event == "PreToolUse":
        if tool == "Bash" and scope_id is not None:
            store.invalidate(scope_id, changed=False, deadline=lock_deadline())
        return {}
    if event in ("PostToolUse", "PostToolUseFailure"):
        if tool not in FILE_TOOLS and tool != "Bash":
            return {}
        if tool in FILE_TOOLS:
            inputs = payload.get("tool_input")
            locator = _locator(inputs.get("notebook_path" if tool == "NotebookEdit" else "file_path")) \
                if isinstance(inputs, dict) else None
            scope_id = _scope(state, locator, key) if locator is not None else scope_id
            if scope_id is not None:
                response = payload.get("tool_response")
                has_error = isinstance(response, dict) and bool(response.get("error"))
                changed = event == "PostToolUse" and locator is not None and not has_error
                store.invalidate(scope_id, changed=changed, deadline=lock_deadline())
                state = store.read_state()
        # Bash completion 只讀 runner 已落地收據；唔讀 command、stdout 或 error 字串。
        if scope_id is None:
            return {}
    if scope_id is None:
        if event in ("SessionStart", "SubagentStart"):
            return _context(event, render_brief(build_status(None),
                                               max_bytes=512 if event == "SubagentStart" else 768))
        return {}
    status = build_status(state, scope_id=scope_id)
    text = render_brief(status, max_bytes=512 if event == "SubagentStart" else 768)
    if event == "SubagentStart":
        return _context(event, text)
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 4096:
        return _context(event, text) if event == "SessionStart" else _context(event, UNKNOWN)
    if event == "SessionStart":
        # 每次 startup／resume／compact 都應得到一次當前 cached brief。
        try:
            store.deliver(session_id, scope_id, status_fingerprint(status), event=event,
                          interval_seconds=0, deadline=lock_deadline())
        except Exception:
            pass
        return _context(event, text)
    due = store.deliver(session_id, scope_id, status_fingerprint(status), event=event,
                        interval_seconds=60 if event == "UserPromptSubmit" else 0,
                        deadline=lock_deadline())
    return _context(event, text) if due else {}


def run_hook(event: str, payload: dict, *, home: Path | None = None, deadline=None) -> dict:
    if event not in EVENTS:
        return {}
    end = min(deadline, time.monotonic() + HOOK_SECONDS) if deadline is not None \
        else time.monotonic() + HOOK_SECONDS
    if (not isinstance(payload, dict)
            or payload.get("hook_event_name", event) != event):
        return _context(event, UNKNOWN)
    try:
        with _bounded(end):
            return _run(event, payload, Store(home=home), end)
    except Exception:
        # 唔重跑、唔擋工作，亦唔將 timeout 或損壞 state 包裝成目前已驗。
        return _context(event, UNKNOWN)
