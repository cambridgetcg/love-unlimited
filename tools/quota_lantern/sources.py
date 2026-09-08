"""只收白名單 metrics；local 唔寫 state，live 唔接觸登入資料。"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

from .core import LanternError, normalize_rate_limits, utc_now

MAX_ENTRIES = 256
MAX_FILES = 8
MAX_FILE_BYTES = 128 * 1024
MAX_BYTES = 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_LIVE_LINES = 256
RECENT_DAYS = 7
SUPPORTED_VERSION = "0.153.3"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_NUMBER = re.compile(r"-?[0-9]{1,24}(?:\.[0-9]{1,12})?\Z", re.ASCII)
_ROLLOUT = re.compile(r"rollout-[A-Za-z0-9_.-]{1,160}\.jsonl\Z", re.ASCII)


def _report():
    return {"observations": [], "coverage": {"partial": False,
            "entries_seen": 0, "files_read": 0, "bytes_read": 0,
            "lines_read": 0}, "diagnostics": [], "cursors": {}}


def _partial(report, code):
    report["coverage"]["partial"] = True
    if code not in report["diagnostics"]:
        report["diagnostics"].append(code)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _expired(deadline):
    return deadline is not None and time.monotonic() >= deadline


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    except OverflowError:
        return False


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        pass
    return None


def _metrics(raw, report, depth=0):
    """先投影，再交畀 core；自由文字唔當 metadata。"""
    if not isinstance(raw, dict):
        return {}
    if depth > 4:
        _partial(report, "source_metadata_rejected")
        return {}
    if "window_duration_mins" in raw and "window_minutes" not in raw:
        raw = {**raw, "window_minutes": raw["window_duration_mins"]}
    result = {}
    aliases = {
        "used_percent": "usedPercent", "window_minutes": "windowDurationMins",
        "resets_at": "resetsAt", "limit_id": "limitId", "plan_type": "planType",
        "has_credits": "hasCredits", "unlimited": "unlimited", "balance": "balance",
        "ordinary_usage_allowed": "ordinaryUsageAllowed",
        "rate_limit_reached_type": "rateLimitReachedType",
        "spend_control_reached": "spendControlReached",
    }
    for snake, camel in aliases.items():
        if snake not in raw and camel not in raw:
            continue
        value = raw[snake] if snake in raw else raw[camel]
        if value is None or isinstance(value, bool) or _number(value):
            result[snake] = value
        elif isinstance(value, str):
            if snake == "resets_at":
                value = _timestamp(value)
            elif snake == "balance":
                value = value if _NUMBER.fullmatch(value) else None
            elif snake in {"limit_id", "plan_type", "rate_limit_reached_type"}:
                value = value if _TOKEN.fullmatch(value) else None
            else:
                value = None
            result[snake] = value
            if value is None:
                _partial(report, "source_metadata_rejected")
        else:
            _partial(report, "source_metadata_rejected")
    # limit_name 係顯示文字，schema 冇需要，永遠唔帶出。
    for key in ("primary", "secondary", "credits"):
        if key in raw:
            result[key] = _metrics(raw[key], report, depth + 1) if isinstance(raw[key], dict) else None
            if raw[key] is not None and not isinstance(raw[key], dict):
                _partial(report, "source_metadata_rejected")
    for snake, camel in (("rate_limits", "rateLimits"),
                         ("rate_limits_by_limit_id", "rateLimitsByLimitId")):
        value = raw.get(snake, raw.get(camel))
        if snake == "rate_limits" and isinstance(value, dict):
            result[camel] = _metrics(value, report, depth + 1)
        elif snake.endswith("by_limit_id") and isinstance(value, dict):
            buckets = {}
            for key, bucket in list(value.items())[:64]:
                if isinstance(key, str) and _TOKEN.fullmatch(key) and isinstance(bucket, dict):
                    buckets[key] = _metrics(bucket, report, depth + 1)
                else:
                    _partial(report, "source_metadata_rejected")
            if len(value) > 64:
                _partial(report, "source_metadata_rejected")
            result[camel] = buckets
    return result


def _usage(payload):
    info = payload.get("info")
    raw = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(raw, dict):
        return None
    return {key: raw[key] for key in ("input_tokens", "cached_input_tokens",
            "output_tokens", "reasoning_output_tokens", "total_tokens")
            if key in raw and _number(raw[key])}


def _open_dir(path):
    """逐層 nofollow，唔畀 parent symlink／rename 將讀取帶出根目錄。"""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_relative(root_fd, parts, *, directory=False):
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if index < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            else:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError(errno.EINVAL, "source_unsafe_file")
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _log_parts(parts):
    if (len(parts) != 5 or parts[0] != "sessions"
            or not re.fullmatch(r"[0-9]{4}", parts[1])
            or not all(re.fullmatch(r"[0-9]{2}", part) for part in parts[2:4])
            or not _ROLLOUT.fullmatch(parts[4])):
        return False
    try:
        datetime(int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        return False
    return True


def _discover(root_fd, report, deadline):
    candidates = []
    # 日期係可預測嘅有限集合，唔遞迴巡視 home 或 archived/config。
    today = datetime.now(timezone.utc).date()
    _partial(report, "source_recent_scope")
    for age in range(RECENT_DAYS):
        if _expired(deadline):
            _partial(report, "source_deadline")
            break
        date = today - timedelta(days=age)
        parts = ("sessions", f"{date.year:04d}", f"{date.month:02d}", f"{date.day:02d}")
        try:
            fd = _open_relative(root_fd, parts, directory=True)
        except FileNotFoundError:
            continue
        except OSError:
            _partial(report, "source_unsafe_path")
            continue
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    report["coverage"]["entries_seen"] += 1
                    if _expired(deadline):
                        _partial(report, "source_deadline")
                        return candidates
                    if _ROLLOUT.fullmatch(entry.name):
                        if entry.is_file(follow_symlinks=False):
                            # 只用本輪有界 discovery 嘅 mtime 排讀取次序，唔當 evidence time。
                            candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns,
                                               parts + (entry.name,)))
                        else:
                            _partial(report, "source_unsafe_file")
                    else:
                        _partial(report, "source_discovery_skipped")
                    if report["coverage"]["entries_seen"] >= MAX_ENTRIES:
                        _partial(report, "source_discovery_limit")
                        return candidates
        except OSError:
            _partial(report, "source_read_failed")
        finally:
            os.close(fd)
    return candidates


def _read_local_file(fd, source_id, cursor, report, deadline):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1):
        _partial(report, "source_unsafe_file")
        return
    report["coverage"]["files_read"] += 1
    budget = min(MAX_FILE_BYTES, MAX_BYTES - report["coverage"]["bytes_read"])
    offset, discarding = 0, False
    cursor = cursor if isinstance(cursor, dict) else {}
    prior = cursor.get("offset")
    changed_in_place = (cursor.get("size") == info.st_size
                        and cursor.get("mtime_ns") != info.st_mtime_ns)
    if (type(prior) is int and 0 <= prior <= info.st_size and not changed_in_place
            and cursor.get("device") == info.st_dev and cursor.get("inode") == info.st_ino
            and isinstance(cursor.get("anchor"), str)):
        anchor = os.pread(fd, min(64, prior), max(0, prior - 64))
        budget -= len(anchor)
        report["coverage"]["bytes_read"] += len(anchor)
        if _hash(anchor) == cursor["anchor"]:
            offset, discarding = prior, cursor.get("discarding") is True
        else:
            _partial(report, "source_rotation")
    elif cursor:
        _partial(report, "source_rotation")
    if info.st_size - offset > budget - 64:
        # 首次／落後太多只取尾段，清楚承認中間冇見到。
        offset = max(offset, info.st_size - max(0, budget - 64))
        discarding = True
        _partial(report, "source_file_limit")
    if budget <= 64 or _expired(deadline):
        _partial(report, "source_deadline" if _expired(deadline) else "source_byte_limit")
        return
    # 留 64 bytes 畀 cursor anchor，實際 IO 亦計入每檔上限。
    data = os.pread(fd, budget - 64, offset)
    report["coverage"]["bytes_read"] += len(data)
    position = 0
    collected = utc_now()
    while position < len(data):
        if _expired(deadline):
            _partial(report, "source_deadline")
            break
        end = data.find(b"\n", position)
        if end < 0:
            _partial(report, "source_partial_line")
            if discarding or len(data) - position > MAX_LINE_BYTES:
                discarding = True
                position = len(data)
            break
        line = data[position:end]
        position = end + 1
        if discarding:
            discarding = False
            _partial(report, "source_line_skipped")
            continue
        report["coverage"]["lines_read"] += 1
        if len(line) > MAX_LINE_BYTES:
            _partial(report, "source_line_limit")
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            _partial(report, "source_malformed_json")
            continue
        if not isinstance(event, dict) or event.get("type") != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        observed = _timestamp(event.get("timestamp"))
        if observed is None:
            _partial(report, "source_event_time_invalid")
            continue
        raw = _metrics(payload.get("rate_limits"), report)
        usage = _usage(payload)
        if not raw:
            if not usage:
                continue
            raw = {"primary": None, "secondary": None}
        try:
            observations = normalize_rate_limits(raw, source_kind="codex_local",
                source_id=source_id, source_version=None, observed_at=observed,
                collected_at=collected, usage=usage, coverage=report["coverage"],
                account_ref=None)
        except (LanternError, ValueError, OverflowError, RecursionError):
            _partial(report, "source_metadata_rejected")
            continue
        report["observations"].extend(observations)
    offset += position
    anchor = os.pread(fd, min(64, offset), max(0, offset - 64))
    report["coverage"]["bytes_read"] += len(anchor)
    report["cursors"][source_id] = {"device": info.st_dev, "inode": info.st_ino,
        "size": info.st_size, "mtime_ns": info.st_mtime_ns,
        "offset": offset, "anchor": _hash(anchor), "discarding": discarding}


def collect_local(codex_home: Path, *, files: list[Path] | None = None,
                  cursors: dict | None = None, deadline=None) -> dict:
    report = _report()
    if _expired(deadline):
        _partial(report, "source_deadline")
        return report
    root = Path(os.path.abspath(codex_home))
    try:
        root_fd = _open_dir(root)
    except FileNotFoundError:
        _partial(report, "source_missing")
        return report
    except OSError:
        _partial(report, "source_unsafe_path")
        return report
    try:
        if files is None:
            # 活躍舊 session append 後可以入選；filename 只做同 mtime 嘅 tie-break。
            candidates = [parts for _, parts in sorted(_discover(root_fd, report, deadline), reverse=True)]
        else:
            candidates = []
            if len(files) > MAX_ENTRIES:
                _partial(report, "source_discovery_limit")
            for file in files[:MAX_ENTRIES]:
                report["coverage"]["entries_seen"] += 1
                try:
                    path = Path(file)
                    path = path if path.is_absolute() else root / path
                    relative = path.relative_to(root)
                    if ".." in relative.parts or path.suffix != ".jsonl":
                        raise ValueError
                    # Explicit fixture seam 都唔可以指向 auth/config 或任意其他檔案。
                    if not _log_parts(relative.parts):
                        raise ValueError
                    candidates.append(relative.parts)
                except (TypeError, ValueError):
                    _partial(report, "source_unsafe_path")
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) > MAX_FILES:
            _partial(report, "source_file_count_limit")
        for parts in candidates[:MAX_FILES]:
            if _expired(deadline):
                _partial(report, "source_deadline")
                break
            source_id = "local-" + _hash(os.fsencode(str(root / Path(*parts))))
            try:
                fd = _open_relative(root_fd, parts)
                try:
                    _read_local_file(fd, source_id,
                        cursors.get(source_id) if isinstance(cursors, dict) else None,
                        report, deadline)
                finally:
                    os.close(fd)
            except OSError:
                _partial(report, "source_read_failed")
    finally:
        os.close(root_fd)
    # 每筆都帶最終 coverage，唔因先後次序而將丟棄隱藏。
    for observation in report["observations"]:
        observation["coverage"] = dict(report["coverage"])
    return report


class _TransportError(Exception):
    pass


class _Child:
    def __init__(self, command, deadline, report):
        if _expired(deadline):
            raise _TransportError("source_deadline")
        self.deadline = deadline
        self.report = report
        self.buffer = bytearray()
        self.stdout_closed = False
        self.selector = selectors.DefaultSelector()
        try:
            # Official child 繼承 existing environment；adapter 唔讀任何 env values。
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                start_new_session=True)
        except (OSError, ValueError):
            self.selector.close()
            raise _TransportError("source_spawn_failed") from None
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            os.set_blocking(stream.fileno(), False)
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")

    def _pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _TransportError("source_deadline")
        for key, _ in self.selector.select(min(remaining, 0.05)):
            coverage = self.report["coverage"]
            capacity = MAX_BYTES - coverage["bytes_read"]
            if capacity <= 0:
                raise _TransportError("source_byte_limit")
            data = os.read(key.fd, min(8192, capacity))
            if not data:
                self.selector.unregister(key.fileobj)
                if key.data == "stdout":
                    self.stdout_closed = True
                continue
            coverage = self.report["coverage"]
            coverage["bytes_read"] += len(data)
            if coverage["bytes_read"] > MAX_BYTES:
                raise _TransportError("source_byte_limit")
            if key.data == "stdout":
                self.buffer.extend(data)
                if len(self.buffer) > MAX_LINE_BYTES and b"\n" not in self.buffer:
                    raise _TransportError("source_line_limit")
            # stderr 只 drain，連片段都唔保存。

    def send(self, message):
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        while data:
            if _expired(self.deadline):
                raise _TransportError("source_deadline")
            try:
                count = os.write(self.process.stdin.fileno(), data)
                data = data[count:]
            except BlockingIOError:
                self._pump()
            except OSError:
                raise _TransportError("source_child_eof") from None

    def line(self):
        while True:
            if _expired(self.deadline):
                raise _TransportError("source_deadline")
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end > MAX_LINE_BYTES:
                    raise _TransportError("source_line_limit")
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                if self.report["coverage"]["lines_read"] >= MAX_LIVE_LINES:
                    raise _TransportError("source_line_count_limit")
                self.report["coverage"]["lines_read"] += 1
                return line
            if self.stdout_closed:
                raise _TransportError("source_child_eof")
            self._pump()

    def response(self, request_id):
        while True:
            try:
                message = json.loads(self.line())
            except (ValueError, UnicodeError, RecursionError):
                raise _TransportError("source_malformed_json") from None
            if not isinstance(message, dict):
                raise _TransportError("source_protocol_error")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise _TransportError("source_rpc_error")
            if not isinstance(message.get("result"), dict):
                raise _TransportError("source_protocol_error")
            return message["result"]

    def close(self, deadline):
        # EOF → TERM → KILL → reap，所有 wait 都用同一總 deadline。
        self.process.stdin.close()
        remaining = max(0.0, deadline - time.monotonic())
        for sig, fraction in ((None, 0.15), (signal.SIGTERM, 0.25),
                              (signal.SIGKILL, 1.0)):
            if sig is not None:
                try:
                    os.killpg(self.process.pid, sig)
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=max(0.0, remaining * fraction))
                break
            except subprocess.TimeoutExpired:
                remaining = max(0.0, deadline - time.monotonic())
        # 清走同一 child process group 留低嘅 descendants，唔留后台 organ。
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if self.process.poll() is None:
            _partial(self.report, "source_cleanup_incomplete")
        self.selector.close()
        self.process.stdout.close()
        self.process.stderr.close()


def _sparse(raw):
    buckets = raw.get("rateLimitsByLimitId")
    values = list(buckets.values()) if isinstance(buckets, dict) and buckets else [raw.get("rateLimits", raw)]
    return not any(isinstance(bucket, dict) and any(
        isinstance(bucket.get(window), dict) and bucket[window].get("used_percent") is not None
        for window in ("primary", "secondary")) for bucket in values)


def collect_live(*, codex_command: list[str] | None = None,
                 timeout_seconds: float = 30) -> dict:
    report = _report()
    if not _number(timeout_seconds) or timeout_seconds <= 0:
        _partial(report, "source_deadline")
        return report
    timeout = min(float(timeout_seconds), 30.0)
    # 冇足夠 cleanup budget 就唔 spawn；唔賭 microsecond deadline 可以 reap。
    if timeout < 0.1:
        _partial(report, "source_deadline")
        return report
    deadline = time.monotonic() + timeout
    reserve = min(1.0, timeout * 0.3)
    work_deadline = deadline - reserve
    command = ["codex"] if codex_command is None else list(codex_command)
    if not command or not all(isinstance(part, str) and part for part in command):
        _partial(report, "source_spawn_failed")
        return report
    child = None
    try:
        child = _Child(command + ["--version"], work_deadline, report)
        version = child.line().decode("ascii", errors="replace").strip()
        if re.fullmatch(r"codex-cli 0\.153\.3", version) is None:
            raise _TransportError("source_version_unsupported")
        # Version check cleanup 都唔另開額外 timeout。
        child.close(min(deadline, time.monotonic() + reserve / 2))
        if child.process.returncode != 0:
            child = None
            raise _TransportError("source_version_failed")
        child = None
        if _expired(work_deadline):
            raise _TransportError("source_deadline")
        child = _Child(command + ["app-server"], work_deadline, report)
        child.send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "quota_lantern", "title": "Quota Lantern", "version": "1.0.0"}}})
        child.response(1)
        child.send({"method": "initialized"})
        raw = {}
        for request_id in (2, 3):
            child.send({"id": request_id, "method": "account/rateLimits/read"})
            raw = _metrics(child.response(request_id), report)
            if not _sparse(raw):
                break
        if _sparse(raw):
            _partial(report, "source_sparse")
        # 0.153.3 呢個 RPC 冇 account ID；每個 connection 分開，唔假設全域 account。
        observed = utc_now()
        report["observations"] = normalize_rate_limits(raw,
            source_kind="codex_live", source_id="live-" + uuid.uuid4().hex,
            source_version=SUPPORTED_VERSION, observed_at=observed, collected_at=observed,
            coverage=report["coverage"], account_ref=None)
    except _TransportError as exc:
        _partial(report, str(exc))
    except (LanternError, ValueError, OverflowError, RecursionError):
        _partial(report, "source_metadata_rejected")
    except OSError:
        _partial(report, "source_read_failed")
    finally:
        if child is not None:
            child.close(deadline)
    for observation in report["observations"]:
        observation["coverage"] = dict(report["coverage"])
    return report
