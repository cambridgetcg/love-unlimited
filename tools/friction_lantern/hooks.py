"""短小 cached advisory；只喺 material change 並過節流先提示。"""
from contextlib import contextmanager
from pathlib import Path
import signal
import threading
import time

from .api import status
from .core import render_brief, status_fingerprint
from .store import DeliveryStore

EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SubagentStart"})
HOOK_SECONDS = 0.250
UNKNOWN = "阻力燈｜cached 狀態未能核實；未重新檢查，唔代表檢查通過。"


def _context(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


@contextmanager
def _bounded(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    alarm = threading.current_thread() is threading.main_thread()
    started = time.monotonic()

    def expired(_number, _frame):
        raise TimeoutError()

    if alarm:
        handler = signal.getsignal(signal.SIGALRM)
        timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, min(remaining, timer[0]) if timer[0] else remaining)
    try:
        yield
        if time.monotonic() >= deadline:
            raise TimeoutError()
    finally:
        if alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, handler)
            if timer[0]:
                signal.setitimer(signal.ITIMER_REAL,
                                 max(0.000001, timer[0] - (time.monotonic() - started)), timer[1])


def run_hook(event, payload, *, home=None, deadline=None) -> dict:
    if type(event) is not str or event not in EVENTS:
        return {}
    if type(payload) is not dict or payload.get("hook_event_name", event) != event:
        return {}
    # 唔接觸 tool_input／tool_response；普通 Bash success 永遠唔產生收據。
    if event in ("PostToolUse", "PostToolUseFailure") and payload.get("tool_name") != "Bash":
        return {}
    end = time.monotonic() + HOOK_SECONDS
    if deadline is not None:
        end = min(end, deadline)
    try:
        with _bounded(end):
            root = Path.home() if home is None else Path(home)
            value = status(home=root, cwd=payload.get("cwd"))
            if value["pattern"] is None:
                return {}
            text = render_brief(value)
            if event == "SubagentStart":
                return _context(event, text)
            session = payload.get("session_id")
            if type(session) is not str or not 0 < len(session) <= 4096:
                return {}
            due = DeliveryStore(home=root).deliver(session, value["scope_id"], status_fingerprint(value),
                                                   event=event, deadline=end)
            return _context(event, text) if due else {}
    except Exception:
        # 開場明講 bookkeeping 未知；逐回合故障唔洗版，亦唔阻止原工具。
        return _context(event, UNKNOWN) if event == "SessionStart" else {}
