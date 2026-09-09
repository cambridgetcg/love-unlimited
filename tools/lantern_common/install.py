"""Owned host IO／RAM transaction；唔決定 targets、manifest schema 或 hook merge。"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from .private import bounded_lock, check_deadline, private_directory

MAX_BYTES = 1024 * 1024
HOST_LOCK_NAME = ".host-settings.lock"


class HostRefusal(Exception):
    pass


class CommitUncertain(HostRefusal):
    """Manifest 可能已 publish；唔可以再盲目 rollback host。"""


@dataclass(frozen=True)
class HostObject:
    kind: str
    data: bytes
    mode: int = 0o600
    identity: tuple = ()


@contextmanager
def host_parent(home, path, *, create=False, directories=None):
    """逐層 dir_fd + O_NOFOLLOW；leaf 由 caller 嘅 owned policy 判斷。"""
    home, path = Path(home), Path(path)
    if not home.is_absolute() or not path.is_absolute() or ".." in home.parts or ".." in path.parts:
        raise HostRefusal("install_foreign_object")
    try:
        relative = path.relative_to(home)
    except ValueError:
        raise HostRefusal("install_foreign_object") from None
    if not relative.parts:
        raise HostRefusal("install_foreign_object")
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current = home
    try:
        root_info = os.fstat(fd)
        if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o022:
            raise HostRefusal("install_foreign_object")
        for part in relative.parts[:-1]:
            current = current / part
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                if directories is not None:
                    info = os.fstat(child)
                    directories.append((current, (info.st_dev, info.st_ino)))
            child_info = os.fstat(child)
            if child_info.st_uid != os.geteuid() or child_info.st_mode & 0o022:
                os.close(child)
                raise HostRefusal("install_foreign_object")
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def host_read_at(fd, name, *, maximum=MAX_BYTES):
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise HostRefusal("install_foreign_object")
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if info.st_uid != os.geteuid():
        raise HostRefusal("install_foreign_object")
    identity = (info.st_dev, info.st_ino)
    if stat.S_ISLNK(info.st_mode):
        return HostObject("link", os.fsencode(os.readlink(name, dir_fd=fd)),
                          stat.S_IMODE(info.st_mode), identity)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
        raise HostRefusal("install_foreign_object")
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as stream:
        actual = os.fstat(stream.fileno())
        if actual.st_uid != os.geteuid() or actual.st_nlink != 1 or actual.st_mode & 0o022:
            raise HostRefusal("install_foreign_object")
        if (actual.st_dev, actual.st_ino) != identity or actual.st_size > maximum:
            raise HostRefusal("install_file_changed_or_large")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(data) > maximum or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns):
        raise HostRefusal("install_file_changed_or_large")
    return HostObject("file", data, stat.S_IMODE(info.st_mode), identity)


def host_read(home, path, *, maximum=MAX_BYTES, parent=None, read_at=None):
    parent = parent or host_parent
    read_at = read_at or (lambda fd, name: host_read_at(fd, name, maximum=maximum))
    with parent(home, path) as fd:
        return None if fd is None else read_at(fd, path.name)


def host_put(home, path, before, after, directories, *, maximum=MAX_BYTES,
             temporary_prefix=".lantern-", parent=None, read_at=None, make_id=None):
    """Compare-and-replace；原 settings 只留 RAM，temp 只含將寫入嘅內容。"""
    parent = parent or host_parent
    read_at = read_at or (lambda fd, name: host_read_at(fd, name, maximum=maximum))
    if not isinstance(temporary_prefix, str) or "/" in temporary_prefix or "\x00" in temporary_prefix:
        raise HostRefusal("install_foreign_object")
    if after is not None and len(after.data) > maximum:
        raise HostRefusal("install_file_changed_or_large")
    with parent(home, path, create=after is not None, directories=directories) as fd:
        if fd is None or read_at(fd, path.name) != before:
            raise HostRefusal("install_concurrent_change")
        if after is None:
            os.unlink(path.name, dir_fd=fd)
            return None
        temporary = temporary_prefix + (make_id or uuid4)().hex
        created = False
        try:
            if after.kind == "link":
                os.symlink(os.fsdecode(after.data), temporary, dir_fd=fd)
                created = True
            elif after.kind == "file":
                temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  after.mode, dir_fd=fd)
                created = True
                with os.fdopen(temp_fd, "wb") as stream:
                    os.fchmod(stream.fileno(), after.mode)
                    stream.write(after.data)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                raise HostRefusal("install_foreign_object")
            # 先攞新 inode snapshot；replace 後唔再做可能失敗嘅 read，令 caller 一定可記 journal。
            actual = read_at(fd, temporary)
            if actual is None or actual.kind != after.kind or actual.data != after.data:
                raise HostRefusal("install_concurrent_change")
            if read_at(fd, path.name) != before:
                raise HostRefusal("install_concurrent_change")
            os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            created = False
            return actual
        finally:
            if created:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass


def rollback_host(home, journal, directories, *, read=None, put=None, parent=None):
    read, put, parent = read or host_read, put or host_put, parent or host_parent
    complete = True
    for path, before, after in reversed(journal):
        try:
            current = read(home, path)
            # 唔用 RAM snapshot 壓過第三者後來嘅內容／mode／inode。
            if current != after:
                complete = False
                continue
            put(home, path, current, before, [])
        except (OSError, HostRefusal):
            complete = False
    for path, identity in reversed(directories):
        try:
            with parent(home, path) as fd:
                if fd is not None:
                    info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                        os.rmdir(path.name, dir_fd=fd)
        except OSError:
            pass
    return complete


@contextmanager
def host_transaction(home, result, *, rollback=None):
    """Caller 必須持 host lock 到 rollback 完；唔聲稱 crash recovery。"""
    journal, directories = [], []
    try:
        yield journal, directories
    except CommitUncertain:
        raise
    except Exception:
        if not (rollback or rollback_host)(home, journal, directories):
            result["diagnostics"].append("install_rollback_incomplete")
        else:
            result["changed"] = []
        raise


def commit_manifest(previous, manifest, *, read, write):
    """Read/write 係零參數／單參數 callbacks；write 成功即完成，只有失敗先 reconciliation。"""
    try:
        write(manifest)
    except Exception:
        try:
            current = read()
        except Exception:
            raise CommitUncertain("install_manifest_unverified") from None
        if current == manifest:
            raise CommitUncertain("install_manifest_durability_unverified") from None
        if current != previous:
            raise CommitUncertain("install_manifest_conflict") from None
        raise


@contextmanager
def host_settings_lock(home: Path, *, deadline=None, create=True):
    """兩 installer 共用同一 inode；各自 domain lock 必須先攞，唔反向 nested。"""
    deadline = time.monotonic() + 5 if deadline is None else deadline
    check_deadline(deadline, error=HostRefusal, timeout_code="install_lock_timeout")
    with private_directory(Path(home) / ".local/state/lantern-common", create=create) as fd:
        if fd is None:
            yield False
            return
        with bounded_lock(fd, HOST_LOCK_NAME, deadline=deadline, error=HostRefusal,
                          unsafe_code="install_lock_unsafe", timeout_code="install_lock_timeout"):
            yield True
