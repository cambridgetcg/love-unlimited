"""純 metadata contract 同有界預算；所有對外提示係固定文字。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
import signal
import stat
import sys
import threading
import time
import uuid

from . import RadarError

MAX_FILE = 1024 * 1024
MAX_ENTRIES = 256
MAX_CACHE = 256 * 1024
MAX_STDIN = 64 * 1024
TTL = 30 * 60
HOOK_SECONDS = 0.250
LOCK_SECONDS = 0.025
EXPLICIT = "explicit_observation"
HOOK = "claude_hook_observation"
SOURCES = frozenset((EXPLICIT, HOOK))
SCHEMA = "kingdom.coedit-radar.cache/2"
UNKNOWN = "共筆雷達｜觀察未知；唔係 Read 收據或寫入許可。"
CHANGED = ("共筆雷達｜檔案自本 session 上次有界觀察後有變；"
           "寫入前可以重新讀取同核對。未證 Read 返回 bytes，亦唔保證之後冇 race。")
REPLACED = ("共筆雷達｜檔案 identity 自本 session 上次有界觀察後已換；"
            "即使內容相同都應重新核對。未證 Read 返回 bytes，亦唔保證之後冇 race。")
DELETED = ("共筆雷達｜本 session 上次觀察嘅檔案目前已唔存在；"
           "寫入前可以核對原意。唔推斷由邊位改動，亦唔保證之後冇 race。")
DIFFERENCES = {"content_changed": CHANGED, "identity_changed": REPLACED,
               "deleted": DELETED, "metadata_changed": CHANGED}


def digest(key: bytes, domain: str, *values) -> str:
    # JSON tuple 編碼有長度邊界，唔用可碰撞嘅字串拼接。
    body = json.dumps([domain, *values], ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _linux_clock():
    # 固定 kernel pseudo-file，最多 64 bytes；唔讀機器／使用者識別或 subprocess。
    fd = os.open("/proc/sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW
                 | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RadarError("clock_unavailable")
        boot = os.read(fd, 64).decode("ascii").strip()
    finally:
        os.close(fd)
    return boot, time.clock_gettime(time.CLOCK_BOOTTIME)


def _darwin_clock():
    # Python monotonic 喺 Mac 用 mach_absolute_time，唔包含 sleep。
    # 固定 libSystem + 固定 sysctl；continuous clock 包 sleep，UUID 唔由 wall time 推算。
    import ctypes
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    sysctl = library.sysctlbyname
    sysctl.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                      ctypes.c_void_p, ctypes.c_size_t]
    sysctl.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(64)
    size = ctypes.c_size_t(len(buffer))
    if sysctl(b"kern.bootsessionuuid", buffer, ctypes.byref(size), None, 0) != 0 or size.value != 37:
        raise RadarError("clock_unavailable")
    if buffer.raw[36:37] != b"\0":
        raise RadarError("clock_unavailable")

    class Timebase(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    info = Timebase()
    timebase = library.mach_timebase_info
    timebase.argtypes = [ctypes.POINTER(Timebase)]
    timebase.restype = ctypes.c_int
    continuous = library.mach_continuous_time
    continuous.argtypes = []
    continuous.restype = ctypes.c_uint64
    if timebase(ctypes.byref(info)) != 0 or not info.numer or not info.denom:
        raise RadarError("clock_unavailable")
    return buffer.raw[:36].decode("ascii"), continuous() * info.numer / info.denom / 1_000_000_000


def system_clock():
    """Boot UUID + suspend-aware elapsed seconds；無可核實 platform source 就 unknown。"""
    try:
        if sys.platform == "linux":
            boot, elapsed = _linux_clock()
        elif sys.platform == "darwin":
            boot, elapsed = _darwin_clock()
        else:
            raise RadarError("clock_unavailable")
        parsed = uuid.UUID(boot)
        if str(parsed) != boot.lower() or parsed.int == 0:
            raise RadarError("clock_unavailable")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 10**12:
            raise RadarError("clock_unavailable")
        return str(parsed), elapsed
    except Exception as exc:
        raise RadarError("clock_unavailable") from exc


def clock_stamp(key):
    boot, elapsed = system_clock()
    # Wall time 只係 metadata，絕不參與 TTL／boot identity，回調唔會令舊證據復活。
    at = time.time()
    if type(at) not in (int, float) or not math.isfinite(at) or not 0 <= at <= 10**12:
        raise RadarError("clock_unavailable")
    return {"at": at, "elapsed": elapsed, "boot": digest(key, "boot", boot)}


def content_digest(key: bytes, data: bytes) -> str:
    return hmac.new(key, b"coedit-content-v1\0" + data, hashlib.sha256).hexdigest()


def identifier(value) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 4096
            and all(32 <= ord(char) < 127 for char in value))


def safe_path(value) -> str | None:
    try:
        path = os.fspath(value)
    except TypeError:
        return None
    if (not isinstance(path, str) or not path.startswith("/")
            or path.startswith("//") or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(0xD800 <= ord(c) <= 0xDFFF for c in path)
            or len(path.encode("utf-8")) > 4096):
        return None
    parts = path.split("/")[1:]
    if not parts or len(parts) > 128 or any(part in ("", ".", "..") for part in parts):
        return None
    return path


def check(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise RadarError("invalid_deadline")
    if time.monotonic() >= deadline:
        raise RadarError("deadline")


def end_time(deadline=None):
    now = time.monotonic()
    if deadline is None:
        return now + HOOK_SECONDS
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise RadarError("invalid_deadline")
    return min(deadline, now + HOOK_SECONDS)


@contextmanager
def bounded(deadline):
    check(deadline)
    alarm = threading.current_thread() is threading.main_thread()
    started = time.monotonic()
    previous_handler = previous_timer = None

    def expired(_number, _frame):
        raise RadarError("deadline")

    if alarm:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, expired)
        remaining = max(0.000001, deadline - started)
        signal.setitimer(signal.ITIMER_REAL, min(remaining, previous_timer[0])
                         if previous_timer[0] else remaining)
    try:
        yield
        check(deadline)
    finally:
        if alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                signal.setitimer(signal.ITIMER_REAL,
                                 max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                                 previous_timer[1])


def result(status="unknown", reason="unavailable", source=None):
    return {"status": status, "reason": reason, "source": source,
            "advisory": True, "read_bytes": "unknown"}


def compare_observations(baseline, current, *, source):
    if not baseline or baseline.get("source") != source:
        return result(reason="baseline_missing", source=source)
    if current.get("status") == "missing":
        return result("changed", "deleted", source)
    if current.get("status") != "observed":
        return result(reason=current.get("reason", "unavailable"), source=source)
    for field, reason in (("identity", "identity_changed"), ("digest", "content_changed"),
                          ("version", "metadata_changed")):
        if baseline[field] != current[field]:
            return result("changed", reason, source)
    return result("no_observed_difference", "observation_only", source)


def clip(text, maximum):
    if type(maximum) is not int or maximum < 0:
        return ""
    return text.encode("utf-8")[:min(maximum, 2048)].decode("utf-8", errors="ignore")


def render_brief(status, *, max_bytes=512):
    if status.get("status") == "changed":
        text = DIFFERENCES.get(status.get("reason"), UNKNOWN)
    elif status.get("status") == "no_observed_difference":
        text = "共筆雷達｜兩個有界觀察暫未見差異；唔證期間冇改過、Read 返回 bytes 或可安全寫入。"
    elif status.get("status") == "observed":
        text = "共筆雷達｜有本 session 有界觀察；唔係 Read 收據，亦未證目前檔案冇變。"
    else:
        text = UNKNOWN
    return clip(text, max_bytes)
