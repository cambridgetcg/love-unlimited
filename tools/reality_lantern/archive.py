"""明示封存 typed receipts；durable 證據唔借用會吞 fsync 錯誤嘅 publish。"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import os
import re
import stat
import uuid

from lantern_common.json import decode_json, encode_json
from lantern_common.private import check_deadline, private_file_info, private_read
from .core import (MAX_STATE_BYTES, RealityError, digest_id, fields, keyed_digest,
                   require, validate_state)

ARCHIVE_SCHEMA = "kingdom.reality-lantern.archive/1"
ARCHIVE_DIR = "archives"
MAX_ARCHIVES = 32
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = MAX_STATE_BYTES + 1024
_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


@contextmanager
def directory(parent, *, create=False):
    """相對已核實 state fd 開目錄；唔跟 link、唔 chmod 外來物件。"""
    child = None
    try:
        if create:
            try:
                os.mkdir(ARCHIVE_DIR, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        try:
            child = os.open(ARCHIVE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
        except FileNotFoundError:
            if create:
                raise RealityError("state_unsafe") from None
            yield None
            return
        info = os.fstat(child)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RealityError("state_unsafe")
        yield child
        current = os.stat(ARCHIVE_DIR, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(info):
            raise RealityError("state_unsafe")
    except OSError:
        raise RealityError("state_unsafe") from None
    finally:
        if child is not None:
            os.close(child)


def inventory(fd, *, deadline=None):
    """只讀有界 metadata；數量唔代表 HMAC 已驗或磁碟可寫。"""
    entries, total = {}, 0
    if fd is None:
        return entries, total
    try:
        with os.scandir(fd) as iterator:
            for entry in iterator:
                check_deadline(deadline, error=RealityError)
                if len(entries) >= MAX_ARCHIVES:
                    raise RealityError("archive_full")
                if _NAME.fullmatch(entry.name) is None:
                    raise RealityError("state_unsafe")
                info = private_file_info(fd, entry.name, error=RealityError)
                if info is None:
                    raise RealityError("state_changed")
                if info.st_size > MAX_ARCHIVE_FILE_BYTES:
                    raise RealityError("state_too_large")
                entries[entry.name] = info.st_size
                total += info.st_size
                if total > MAX_ARCHIVE_BYTES:
                    raise RealityError("archive_full")
        return entries, total
    except OSError:
        raise RealityError("state_io") from None


def _validate(value):
    fields(value, "schema archive_id state")
    require(value["schema"] == ARCHIVE_SCHEMA)
    digest_id(value["archive_id"])
    validate_state(value["state"])
    require(value["state"]["runs"].keys() == value["state"]["completions"].keys())


def encode(state, key):
    validate_state(state)
    if state["runs"].keys() != state["completions"].keys():
        raise RealityError("archive_pending")
    ident = keyed_digest(key, "archive", state)
    value = {"schema": ARCHIVE_SCHEMA, "archive_id": ident, "state": state}
    return ident, encode_json(value, maximum=MAX_ARCHIVE_FILE_BYTES,
                             error=RealityError, validator=_validate)


def verify(data, key, ident):
    value = decode_json(data, maximum=MAX_ARCHIVE_FILE_BYTES,
                        error=RealityError, validator=_validate)
    if (not hmac.compare_digest(value["archive_id"], ident)
            or not hmac.compare_digest(keyed_digest(key, "archive", value["state"]), ident)):
        raise RealityError("archive_unverified")
    return value["state"]


def _read_equal(fd, name, data, key, ident):
    actual = private_read(fd, name, MAX_ARCHIVE_FILE_BYTES, error=RealityError)
    if actual != data:
        raise RealityError("archive_conflict")
    verify(actual, key, ident)


def _sync_file(fd, name):
    # 重用 target 亦要重新 fsync；單純 readback 唔係斷電 durability。
    info = private_file_info(fd, name, error=RealityError)
    if info is None:
        raise RealityError("archive_unverified")
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        if _identity(os.fstat(handle)) != _identity(info):
            raise RealityError("state_unsafe")
        os.fsync(handle)
    finally:
        os.close(handle)


def _publish_exclusive(fd, name, data):
    # link 係原子 no-clobber；唔用 replace 覆蓋同名外來 target。
    temporary = ".archive-" + str(uuid.uuid4())
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=fd)
    try:
        os.fchmod(handle, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(handle, data[offset:])
            if written <= 0:
                raise RealityError("state_io")
            offset += written
        os.fsync(handle)
        try:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            # 之後只接受安全且完全相同嘅既有 target，唔覆寫。
            pass
    finally:
        os.close(handle)
        os.unlink(temporary, dir_fd=fd)


def durable(parent, state, key, *, deadline=None):
    """成功先返回 HMAC id；中途失敗保留已發佈 archive，唔假裝冇寫過。"""
    ident, data = encode(state, key)
    name = ident + ".json"
    check_deadline(deadline, error=RealityError)
    try:
        with directory(parent, create=True) as fd:
            entries, total = inventory(fd, deadline=deadline)
            if name in entries:
                _read_equal(fd, name, data, key, ident)
            else:
                if len(entries) >= MAX_ARCHIVES or total + len(data) > MAX_ARCHIVE_BYTES:
                    raise RealityError("archive_full")
                check_deadline(deadline, error=RealityError)
                _publish_exclusive(fd, name, data)
            _read_equal(fd, name, data, key, ident)
            check_deadline(deadline, error=RealityError)
            _sync_file(fd, name)
            os.fsync(fd)
            os.fsync(parent)
            _read_equal(fd, name, data, key, ident)
            check_deadline(deadline, error=RealityError)
        return ident
    except OSError:
        raise RealityError("archive_unverified") from None
