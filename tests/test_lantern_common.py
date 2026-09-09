"""Common IO 直接測試；只用 fake HOME，唔讀真 state。"""
import fcntl
import os
from pathlib import Path
import stat
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from lantern_common import CommonError
from lantern_common.json import decode_json, encode_json, validate_plain
from lantern_common import private as io


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(path))
    return path


def test_canonical_json_and_product_error_seam():
    class ProductError(ValueError):
        pass

    assert encode_json({"z": 2, "a": "燈"}) == '{"a":"燈","z":2}\n'.encode()
    assert decode_json(encode_json({"a": [True, None, 1.25]})) == {"a": [True, None, 1.25]}
    with pytest.raises(ProductError, match="state_corrupt"):
        decode_json(b'{"a":1,"a":2}', error=ProductError)
    with pytest.raises(ProductError, match="full"):
        encode_json([1, 2], maximum=3, error=ProductError, too_large="full")


@pytest.mark.parametrize("raw", [b'{"a":NaN}', b'{"a":1e999}', b'{"a":1,"a":2}',
                                b"\xff", b"[", b'"\\ud800"', b"[" * 20 + b"]" * 20])
def test_decode_refuses_noncanonical_or_unbounded_values(raw):
    with pytest.raises(CommonError, match="state_corrupt"):
        decode_json(raw)


def test_json_limits_include_newline_and_bound_alias_expansion():
    assert encode_json({}, maximum=3) == b"{}\n"
    with pytest.raises(CommonError, match="cache_too_large"):
        encode_json({}, maximum=2)
    with pytest.raises(CommonError, match="state_too_large"):
        decode_json(b"{}\n", maximum=2)
    nested = [None] * 10
    for _ in range(8):
        nested = [nested] * 10
    start = time.monotonic()
    with pytest.raises(CommonError, match="invalid_input"):
        validate_plain(nested, max_nodes=1000)
    assert time.monotonic() - start < 0.5


def test_private_reads_are_pure_and_write_modes_are_private(home):
    path = home / ".local/state/example"
    with io.private_directory(path) as fd:
        assert fd is None
    with io.private_locked(path, create=False) as fd:
        assert fd is None
    assert list(home.iterdir()) == []
    with io.private_locked(path) as fd:
        assert io.private_read(fd, "state.json", 100) is None
        io.atomic_write(fd, "state.json", encode_json({"count": 1}))
        assert decode_json(io.private_read(fd, "state.json", 100)) == {"count": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in path.iterdir())


@pytest.mark.parametrize("name", ["../outside", "/absolute", "", ".", "..", "bad\x00name"])
def test_private_leaf_never_escapes_directory(home, name):
    with io.private_locked(home / "state") as fd:
        for call in (lambda: io.private_read(fd, name, 10),
                     lambda: io.atomic_write(fd, name, b"secret")):
            with pytest.raises(CommonError, match="state_unsafe"):
                call()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "fifo", "directory"])
def test_private_unsafe_objects_are_not_followed_or_repaired(home, kind):
    state = home / "state"
    with io.private_locked(state):
        pass
    victim = home / "victim"
    victim.write_bytes(b"CANARY")
    victim.chmod(0o600)
    path = state / "state.json"
    if kind == "symlink":
        path.symlink_to(victim)
    elif kind == "hardlink":
        os.link(victim, path)
    elif kind == "public":
        path.write_bytes(b"{}")
        path.chmod(0o644)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.mkdir(mode=0o700)
    before = path.lstat()
    with io.private_directory(state) as fd:
        with pytest.raises(CommonError, match="state_unsafe"):
            io.private_read(fd, path.name, 100)
        with pytest.raises(CommonError, match="state_unsafe"):
            io.atomic_write(fd, path.name, b"{}")
    assert path.lstat() == before
    assert victim.read_bytes() == b"CANARY"


def test_private_parent_symlink_is_not_followed(home):
    outside = home / "outside"
    outside.mkdir(mode=0o700)
    (home / "state").symlink_to(outside)
    with pytest.raises(CommonError, match="state_unsafe"):
        with io.private_locked(home / "state/nested"):
            pytest.fail("唔應該攞到 symlink 下嘅 fd")
    assert list(outside.iterdir()) == []


def test_atomic_precommit_and_postcommit_failures_remain_distinct(home, monkeypatch):
    state = home / "state"
    with io.private_locked(state) as fd:
        io.atomic_write(fd, "state.json", b"old")
        original = io.os.fsync

        def fail_before_replace(handle):
            if handle != fd:
                raise OSError("PRIVATE_PRECOMMIT_CANARY")
            return original(handle)

        monkeypatch.setattr(io.os, "fsync", fail_before_replace)
        with pytest.raises(CommonError, match="state_io"):
            io.atomic_write(fd, "state.json", b"new")
        assert io.private_read(fd, "state.json", 100) == b"old"

        def fail_after_replace(handle):
            if handle == fd:
                raise OSError("PRIVATE_POSTCOMMIT_CANARY")
            return original(handle)

        monkeypatch.setattr(io.os, "fsync", fail_after_replace)
        with pytest.raises(CommonError, match="state_io"):
            io.atomic_write(fd, "state.json", b"new")
        assert io.private_read(fd, "state.json", 100) == b"new"
    assert not list(state.glob(".tmp-*"))


def test_read_detects_change_during_bounded_io(home, monkeypatch):
    with io.private_locked(home / "state") as fd:
        io.atomic_write(fd, "state.json", b"old")
        original = io.os.read
        changed = False

        def mutate(handle, maximum):
            nonlocal changed
            data = original(handle, maximum)
            if not changed:
                changed = True
                (home / "state/state.json").write_bytes(b"new")
            return data

        monkeypatch.setattr(io.os, "read", mutate)
        with pytest.raises(CommonError, match="state_unsafe"):
            io.private_read(fd, "state.json", 100)


def test_lock_deadline_precedes_creation_and_wait_is_bounded(home):
    state = home / "state"
    with pytest.raises(CommonError, match="lock_timeout"):
        with io.private_locked(state, deadline=time.monotonic() - 1):
            pass
    assert list(home.iterdir()) == []
    with io.private_locked(state):
        start = time.monotonic()
        with pytest.raises(CommonError, match="lock_timeout"):
            with io.private_locked(state, deadline=start + 0.03):
                pass
        assert time.monotonic() - start < 0.5
    identity = (state / ".lock").stat().st_ino
    with io.private_locked(state):
        pass
    assert (state / ".lock").stat().st_ino == identity


def test_waiting_lock_rechecks_identity_after_acquisition(home, monkeypatch):
    with io.private_directory(home / "state", create=True) as fd:
        original = io.fcntl.flock
        replaced = False

        def replace_lock(handle, operation):
            nonlocal replaced
            if not replaced:
                replaced = True
                os.unlink(".lock", dir_fd=fd)
                replacement = os.open(".lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600, dir_fd=fd)
                os.close(replacement)
            return original(handle, operation)

        monkeypatch.setattr(io.fcntl, "flock", replace_lock)
        with pytest.raises(CommonError, match="state_unsafe"):
            with io.bounded_lock(fd):
                pytest.fail("唔應該借用已換走嘅鎖")
