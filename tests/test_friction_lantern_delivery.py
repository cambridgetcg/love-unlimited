"""真 private atomic IO 故障注入；全部 fake HOME，唔寫 Reality bytes。"""
import errno
import json
import os
import stat
import time

import pytest

from .test_friction_lantern_core import CHECK, SCOPE, at
from .test_friction_lantern_hooks import context, file_tree, fixture, payload, write_private
from friction_lantern import hooks, store
from lantern_common import private
from reality_lantern.core import RealityError

EVENTS = ["SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure"]


def publication_fault(monkeypatch, home, *, phase=None, readback_failures=0):
    """只攔 delivery rename／directory fsync；key 同 Reality IO 保持原樣。"""
    root = home / ".local/state/friction-lantern"
    write_private(root / "key", b"f" * 32)
    active = {"delivery": False, "published": False, "faults": 0, "readbacks": 0}
    atomic_write, read, fsync, replace = store.atomic_write, store.private_read, os.fsync, os.replace

    def write(fd, name, data, **kw):
        active["delivery"] = name == "delivery.json"
        try:
            return atomic_write(fd, name, data, **kw)
        finally:
            active["delivery"] = False

    def rename(src, dst, **kw):
        if active["delivery"] and dst == "delivery.json" and phase == "prepublish":
            active["faults"] += 1
            raise OSError(errno.EIO, "RAW-CANARY")
        result = replace(src, dst, **kw)
        if active["delivery"] and dst == "delivery.json":
            active["published"] = True
        return result

    def sync(fd):
        if (active["delivery"] and stat.S_ISDIR(os.fstat(fd).st_mode)
                and phase == "postrename" and not active["faults"]):
            assert active["published"]
            active["faults"] += 1
            raise OSError(errno.EIO, "RAW-CANARY")
        return fsync(fd)

    def readback(fd, name, maximum, **kw):
        if name == "delivery.json" and active["published"]:
            active["readbacks"] += 1
            if active["readbacks"] <= readback_failures:
                raise OSError(errno.EIO, "READBACK-CANARY")
        return read(fd, name, maximum, **kw)

    monkeypatch.setattr(store, "atomic_write", write)
    monkeypatch.setattr(store, "private_read", readback)
    monkeypatch.setattr(private.os, "replace", rename)
    monkeypatch.setattr(private.os, "fsync", sync)
    return active


@pytest.mark.parametrize("event", EVENTS)
def test_delivery_postrename_fsync_failure_reconciles_due_and_dedups(fixture, monkeypatch, event):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    message = payload(workspace, event)
    monkeypatch.setattr(store, "utc_now", lambda: at(0))
    with monkeypatch.context() as fault:
        active = publication_fault(fault, home, phase="postrename")
        result = hooks.run_hook(event, message, home=home)
        assert active["faults"] == 1 and active["published"]
        assert "nonzero" in context(result)
        assert active["readbacks"] == 1
    # IO 已恢復：同一 session/scope/fingerprint 唔再發，唔止受 60s 節流。
    monkeypatch.setattr(store, "utc_now", lambda: at(1000000))
    assert hooks.run_hook(event, message, home=home) == {}
    assert file_tree(home / ".local/state/reality-lantern") == before
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("event", EVENTS)
def test_delivery_prepublish_failure_keeps_retry_available(fixture, monkeypatch, event):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    message = payload(workspace, event)
    with monkeypatch.context() as fault:
        active = publication_fault(fault, home, phase="prepublish")
        result = hooks.run_hook(event, message, home=home)
        assert active["faults"] == 1 and not active["published"]
        assert context(result) == (hooks.UNKNOWN if event == "SessionStart" else "")
        assert not (home / ".local/state/friction-lantern/delivery.json").exists()
    assert "nonzero" in context(hooks.run_hook(event, message, home=home))
    assert hooks.run_hook(event, message, home=home) == {}
    assert file_tree(home / ".local/state/reality-lantern") == before


@pytest.mark.parametrize("phase", [None, "postrename"])
def test_delivery_one_readback_failure_has_bounded_retry(fixture, monkeypatch, phase):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    with monkeypatch.context() as fault:
        active = publication_fault(fault, home, phase=phase, readback_failures=1)
        result = hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home)
        assert "nonzero" in context(result)
        assert active["readbacks"] == 2
    assert hooks.run_hook("PostToolUse", payload(workspace, "PostToolUse"), home=home) == {}
    assert file_tree(home / ".local/state/reality-lantern") == before


@pytest.mark.parametrize("event", EVENTS)
def test_delivery_unreadable_publication_stays_bounded_unknown_without_rollback(fixture, monkeypatch, event):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    active = publication_fault(monkeypatch, home, phase="postrename", readback_failures=100)
    started = time.monotonic()
    result = hooks.run_hook(event, payload(workspace, event), home=home)
    assert time.monotonic() - started < 0.3
    assert active["published"] and active["readbacks"] == 2
    assert context(result) == (hooks.UNKNOWN if event == "SessionStart" else "")
    # 未確認 bytes 唔宣称 due，亦唔盲目 rollback 已發佈 cache。
    cache = home / ".local/state/friction-lantern/delivery.json"
    assert len(json.loads(cache.read_bytes())["entries"]) == 1
    assert b"CANARY" not in cache.read_bytes()
    assert file_tree(home / ".local/state/reality-lantern") == before


def test_delivery_readback_requires_exact_bytes_not_only_equivalent_json(fixture, monkeypatch):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    write_private(home / ".local/state/friction-lantern/key", b"f" * 32)
    atomic_write = store.atomic_write
    published = []

    def write(fd, name, data, **kw):
        if name == "delivery.json":
            data += b"\n"
            published.append(data)
        atomic_write(fd, name, data, **kw)

    monkeypatch.setattr(store, "atomic_write", write)
    result = hooks.run_hook("SessionStart", payload(workspace), home=home)
    assert context(result) == hooks.UNKNOWN
    assert (home / ".local/state/friction-lantern/delivery.json").read_bytes() == published[0]
    assert len(json.loads(published[0])["entries"]) == 1
    assert file_tree(home / ".local/state/reality-lantern") == before


@pytest.mark.parametrize("boundary,expected_reads", [("before_readback", 0), ("after_readback", 1), ("before_retry", 1)])
def test_delivery_reconcile_keeps_same_deadline(tmp_path, monkeypatch, boundary, expected_reads):
    delivery = store.DeliveryStore(home=tmp_path)
    write_private(delivery.state_dir / "key", b"f" * 32)
    active = {"published": False, "expired": False, "reads": 0}
    atomic_write, read, check = store.atomic_write, store.private_read, store.check_deadline
    deadline = time.monotonic() + 1

    def checked(value, **kw):
        assert value == deadline
        check(value, **kw)
        if active["expired"]:
            raise RealityError("lock_timeout")

    def write(fd, name, data, **kw):
        atomic_write(fd, name, data, **kw)
        if name == "delivery.json":
            active["published"] = True
            active["expired"] = boundary == "before_readback"

    def readback(fd, name, maximum, **kw):
        result = read(fd, name, maximum, **kw)
        if name == "delivery.json" and active["published"]:
            active["reads"] += 1
            active["expired"] = True
            if boundary == "before_retry":
                raise OSError(errno.EIO, "READBACK-CANARY")
        return result

    monkeypatch.setattr(store, "check_deadline", checked)
    monkeypatch.setattr(store, "atomic_write", write)
    monkeypatch.setattr(store, "private_read", readback)
    with pytest.raises(RealityError) as caught:
        delivery.deliver("SESSION-CANARY", SCOPE, CHECK, event="SessionStart", deadline=deadline)
    assert caught.value.code == "lock_timeout"
    assert active["published"] and active["reads"] == expected_reads
    assert len(json.loads((delivery.state_dir / "delivery.json").read_bytes())["entries"]) == 1


@pytest.mark.parametrize("event", ["SessionStart", "PostToolUse"])
def test_delivery_reconcile_stalled_readback_respects_hook_budget(fixture, monkeypatch, event):
    home, workspace, _, _ = fixture
    before = file_tree(home / ".local/state/reality-lantern")
    active = publication_fault(monkeypatch, home, phase="postrename")
    read = store.private_read

    def stalled(fd, name, maximum, **kw):
        if name == "delivery.json" and active["published"]:
            active["stalled_reads"] = active.get("stalled_reads", 0) + 1
            time.sleep(2)
        return read(fd, name, maximum, **kw)

    monkeypatch.setattr(store, "private_read", stalled)
    started = time.monotonic()
    result = hooks.run_hook(event, payload(workspace, event), home=home, deadline=started + 0.05)
    assert time.monotonic() - started < 0.3
    assert active.get("stalled_reads") == 1
    assert context(result) == (hooks.UNKNOWN if event == "SessionStart" else "")
    assert file_tree(home / ".local/state/reality-lantern") == before
