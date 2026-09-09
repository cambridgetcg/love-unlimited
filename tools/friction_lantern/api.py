"""共用 cached 純讀 API；缺 state 唔 mkdir、唔生 key、唔掃工作樹。"""
from pathlib import Path

from reality_lantern.snapshot import scope_candidates
from reality_lantern.store import Store
from .core import derive_pattern, empty_status, render_brief


def locator(value):
    if not isinstance(value, (str, Path)):
        return None
    text = str(value)
    if not text or len(text) > 4096 or any(ord(char) < 32 for char in text):
        return None
    path = Path(text)
    return path if path.is_absolute() and ".." not in path.parts else None


def status(*, home, cwd) -> dict:
    path = locator(cwd)
    root = locator(home)
    if path is None or root is None:
        return empty_status("invalid_input")
    try:
        store = Store(home=root)
        key = store.get_key(create=False)
        if key is None:
            return empty_status()
        state = store.read_state()
        candidates = scope_candidates(path, key=key)
        scope_id = next((item for item in candidates if item in state["scopes"]), None)
        return derive_pattern(state, scope_id)
    except Exception:
        return empty_status("state_unavailable")


def brief(*, home, cwd, max_bytes=512) -> str:
    return render_brief(status(home=home, cwd=cwd), max_bytes=max_bytes)
