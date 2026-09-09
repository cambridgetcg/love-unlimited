"""Private nofollow IO 同 bounded flock；唔跟 links、唔 chmod 外來 objects。"""
from __future__ import annotations

import errno
import fcntl
import math
import os
from pathlib import Path
import stat
import time
import uuid
from contextlib import contextmanager

from . import CommonError

_NOFOLLOW = os.O_NOFOLLOW
_DIRECTORY = os.O_DIRECTORY


def check_deadline(deadline, *, error=CommonError, timeout_code="lock_timeout"):
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise error("invalid_input")
        if time.monotonic() >= deadline:
            raise error(timeout_code)


def _name(name, error):
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise error("state_unsafe")


def _io_error(exc, error):
    return error("state_unsafe" if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM)
                 else "state_io")


@contextmanager
def private_directory(path: Path, *, create=False, error=CommonError):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise error("state_unsafe")
    fd = None
    try:
        fd = os.open("/", os.O_RDONLY | _DIRECTORY)
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            try:
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if index == len(parts) - 1:
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise error("state_unsafe")
            elif info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                raise error("state_unsafe")
        yield fd
    except OSError as exc:
        raise _io_error(exc, error) from None
    finally:
        if fd is not None:
            os.close(fd)


def private_file_info(fd, name, *, error=CommonError):
    _name(name, error)
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise error("state_unsafe")
    return info


def _stamp(info):
    return None if info is None else (info.st_dev, info.st_ino, info.st_size,
                                      info.st_mtime_ns, info.st_ctime_ns, info.st_mode,
                                      info.st_uid, info.st_nlink)


def private_read(fd, name, maximum, *, error=CommonError, file_info=None):
    _name(name, error)
    inspect = file_info or (lambda directory, leaf: private_file_info(directory, leaf, error=error))
    try:
        info = inspect(fd, name)
        if info is None:
            return None
        if info.st_size > maximum:
            raise error("state_too_large")
        handle = os.open(name, os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            if _stamp(os.fstat(handle)) != _stamp(info):
                raise error("state_unsafe")
            chunks, size = [], 0
            while size <= maximum:
                chunk = os.read(handle, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > maximum:
                raise error("state_too_large")
            if _stamp(os.fstat(handle)) != _stamp(info) or _stamp(inspect(fd, name)) != _stamp(info):
                raise error("state_unsafe")
            return b"".join(chunks)
        finally:
            os.close(handle)
    except OSError as exc:
        raise _io_error(exc, error) from None


def atomic_write(fd, name, data, *, maximum=20 * 1024 * 1024,
                 error=CommonError, file_info=None):
    """先 fsync temp 再 replace；directory fsync 失敗可能已 publish，唔自動 rollback。"""
    _name(name, error)
    if not isinstance(data, bytes):
        raise error("invalid_input")
    if len(data) > maximum:
        raise error("state_too_large")
    inspect = file_info or (lambda directory, leaf: private_file_info(directory, leaf, error=error))
    try:
        previous = inspect(fd, name)
        temporary = ".tmp-" + str(uuid.uuid4())
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.fchmod(handle, 0o600)
            offset = 0
            while offset < len(data):
                written = os.write(handle, data[offset:])
                if written <= 0:
                    raise error("state_io")
                offset += written
            os.fsync(handle)
            if _stamp(previous) != _stamp(inspect(fd, name)):
                raise error("state_unsafe")
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(handle)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise _io_error(exc, error) from None


@contextmanager
def bounded_lock(fd, name=".lock", *, deadline=None, error=CommonError,
                 unsafe_code="state_unsafe", timeout_code="lock_timeout",
                 file_info=None, check=None):
    """鎖 inode 永不 unlink；攞鎖後再驗證，避免等候期間被換鎖。"""
    _name(name, error)
    deadline = time.monotonic() + 5 if deadline is None else deadline
    check = check or (lambda value: check_deadline(value, error=error, timeout_code=timeout_code))
    check(deadline)
    handle = None
    acquired = False
    try:
        if file_info is not None:
            file_info(fd, name)
        flags = os.O_RDWR | _NOFOLLOW | os.O_NONBLOCK
        try:
            handle = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        except FileExistsError:
            handle = os.open(name, flags, dir_fd=fd)
        info = os.fstat(handle)
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size or _stamp(info) != _stamp(current)):
            raise error(unsafe_code)
        while True:
            check(deadline)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _stamp(info) or _stamp(os.fstat(handle)) != _stamp(info):
            raise error(unsafe_code)
        acquired = True
        yield fd
    except OSError as exc:
        if acquired:
            raise
        raise error(unsafe_code if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR) else "state_io") from None
    finally:
        # Close 亦會放 flock；唔刪 lock file，唔拆散排隊中嘅 processes。
        if handle is not None:
            os.close(handle)


@contextmanager
def private_locked(path: Path, *, create=True, deadline=None, error=CommonError):
    """State caller 可直接用；create=False 缺目錄時回 None，唔建檔。"""
    deadline = time.monotonic() + 5 if deadline is None else deadline
    check_deadline(deadline, error=error)
    with private_directory(path, create=create, error=error) as fd:
        if fd is None:
            yield None
            return
        with bounded_lock(fd, deadline=deadline, error=error):
            yield fd
