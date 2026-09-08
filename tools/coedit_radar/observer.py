"""逐層 nofollow 嘅短檔案觀察；只回 HMAC 同必要數值。"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import PurePosixPath
import stat

from lantern_common.paths import is_secret_path
from . import RadarError
from .core import MAX_FILE, check, content_digest, digest, safe_path

UNSUPPORTED = frozenset((".pdf", ".ipynb", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                         ".bmp", ".ico", ".svgz", ".zip", ".gz", ".tar", ".sqlite",
                         ".db", ".woff", ".woff2", ".mp3", ".mp4", ".mov"))


def eligible_path(value):
    path = safe_path(value)
    if path is None:
        raise RadarError("unsafe_path")
    # Secret policy 必須先過，連 directory open 都唔做。
    if is_secret_path(path):
        raise RadarError("secret_path")
    if PurePosixPath(path).suffix.lower() in UNSUPPORTED:
        raise RadarError("unsupported_type")
    return path


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


def directory_identity(info):
    # 其他檔案改動可以改 directory mtime，唔代表呢條 path 被換。
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid)


@contextmanager
def parent_handle(path, deadline):
    handles, links = [], []
    try:
        check(deadline)
        root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.append(root)
        for part in path.split("/")[1:-1]:
            check(deadline)
            parent = handles[-1]
            before = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise RadarError("unsafe_path")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            handles.append(child)
            if directory_identity(before) != directory_identity(os.fstat(child)):
                raise RadarError("file_race")
            links.append((parent, part, child))

        def verify():
            for parent, name, child in links:
                check(deadline)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if directory_identity(current) != directory_identity(os.fstat(child)):
                    raise RadarError("file_race")

        yield handles[-1], path.rsplit("/", 1)[1], verify
        verify()
    finally:
        for handle in reversed(handles):
            os.close(handle)


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RadarError("unsafe_file")
    if info.st_size > MAX_FILE:
        raise RadarError("file_too_large")


def observe_file(path, *, key, deadline):
    try:
        path = eligible_path(path)
        with parent_handle(path, deadline) as (parent, leaf, verify):
            try:
                before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                verify()
                # 唔憑第一次 ENOENT 證已刪；同一 pinned parent 再核對一次。
                try:
                    os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return {"status": "missing", "reason": "deleted"}
                raise RadarError("file_race")
            _regular(before)
            check(deadline)
            handle = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if stamp(before) != stamp(os.fstat(handle)):
                    raise RadarError("file_race")
                chunks, size = [], 0
                while size <= MAX_FILE:
                    check(deadline)
                    data = os.read(handle, min(65536, MAX_FILE + 1 - size))
                    if not data:
                        break
                    size += len(data)
                    chunks.append(data)
                if size > MAX_FILE:
                    raise RadarError("file_too_large")
                if (size != before.st_size or stamp(before) != stamp(os.fstat(handle))
                        or stamp(before) != stamp(os.stat(leaf, dir_fd=parent, follow_symlinks=False))):
                    raise RadarError("file_race")
                verify()
            finally:
                os.close(handle)
        data = b"".join(chunks)
        if any(byte < 32 and byte not in (9, 10, 13) for byte in data) or b"\x7f" in data:
            raise RadarError("binary_file")
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise RadarError("binary_file") from None
        if data.startswith(b"%PDF-"):
            raise RadarError("unsupported_type")
        # LF 以外嘅行分隔有 harness 差異，保守唔配成 Read baseline。
        lines = data.split(b"\n")
        trailing_lf = data.endswith(b"\n")
        count = len(lines) - (1 if trailing_lf else 0) if data else 0
        check(deadline)
        return {"status": "observed", "reason": "observation_only",
                "identity": digest(key, "file-identity", before.st_dev, before.st_ino),
                "version": digest(key, "file-version", *stamp(before)),
                "digest": content_digest(key, data), "size": size, "lines": count,
                "max_line": max(map(len, lines)), "plain_lf": b"\r" not in data,
                "trailing_lf": trailing_lf}  # 只喺 process 用，唔加 cache 欄位。
    except RadarError as exc:
        return {"status": "unknown", "reason": str(exc)}
    except OSError as exc:
        # 缺 ancestor 未能證係原本 leaf 已刪，唔冒稱已知 deletion。
        reason = "unsafe_path" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "file_unavailable"
        return {"status": "unknown", "reason": reason}


def read_request_supported(inputs):
    return isinstance(inputs, dict) and set(inputs) == {"file_path"}


def read_bounds_supported(observation):
    return (observation.get("status") == "observed" and observation.get("plain_lf") is True
            and 1 <= observation["lines"] <= 2000 and observation["max_line"] <= 2000)


def read_response_supported(response, *, path, lines, trailing_lf=False):
    """只驗 shape／數值，唔讀、比對或計 content 長度，永遠唔證 returned bytes。"""
    if not isinstance(response, dict) or set(response) != {"type", "file"} or response["type"] != "text":
        return False
    file = response["file"]
    if (not isinstance(file, dict)
            or set(file) != {"filePath", "content", "numLines", "startLine", "totalLines"}):
        return False
    # 保留 physical count；只有實際尾 LF 先可多一個 terminal empty line。
    # Reported count 亦受 default 2000 上限約束，唔以 physical count 掩蓋截斷。
    return (isinstance(file["content"], str) and safe_path(file["filePath"]) == path
            and type(file["numLines"]) is int and type(file["totalLines"]) is int
            and file["numLines"] == file["totalLines"] and 1 <= file["totalLines"] <= 2000
            and type(file["startLine"]) is int and file["startLine"] == 1
            and 1 <= lines <= 2000
            and (file["totalLines"] == lines
                 or (trailing_lf is True and file["totalLines"] == lines + 1)))
