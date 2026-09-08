"""共同 host transaction 邊界；host、manifest 同 foreign additions 全係 fixture。"""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from lantern_common import CommonError
from lantern_common import install as common
from quota_lantern import install as quota
from quota_lantern.store import Store

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    return home


def test_missing_lock_read_and_expired_deadline_are_pure(home):
    with common.host_settings_lock(home, create=False) as held:
        assert held is False
    with pytest.raises(common.HostRefusal, match="install_lock_timeout"):
        with common.host_settings_lock(home, deadline=time.monotonic() - 1):
            pass
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("target", ["home", "parent", "leaf"])
@pytest.mark.parametrize("write_bit", [0o020, 0o002])
def test_host_writable_by_others_is_refused_without_chmod(home, target, write_bit):
    path = home / ".claude/settings.json"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b'{"model":"keep"}')
    path.chmod(0o600)
    unsafe = {"home": home, "parent": path.parent, "leaf": path}[target]
    original_mode = unsafe.stat().st_mode & 0o777
    unsafe.chmod(original_mode | write_bit)
    with pytest.raises(common.HostRefusal, match="install_foreign_object"):
        common.host_read(home, path)
    result = quota.install(home=home, repo_root=REPO, dry_run=False)
    assert not result["ok"]
    assert not result["changed"]
    assert path.read_bytes() == b'{"model":"keep"}'
    assert unsafe.stat().st_mode & 0o777 == original_mode | write_bit
    assert not (home / ".local").exists()


def test_successful_manifest_write_keeps_existing_no_readback_contract():
    written = []
    manifest = {"owned": True}
    common.commit_manifest(None, manifest, write=written.append,
                           read=lambda: pytest.fail("成功 write 唔應該新增無必要 readback"))
    assert written == [manifest]


def test_host_compare_replace_and_ram_rollback_preserve_foreign_edits(home):
    path = home / ".claude/settings.json"
    result = {"changed": [], "diagnostics": []}
    with common.host_settings_lock(home):
        with pytest.raises(OSError):
            with common.host_transaction(home, result) as (journal, directories):
                after = common.host_put(home, path, None, common.HostObject("file", b"new"), directories)
                journal.append((path, None, after))
                result["changed"].append("settings")
                path.write_bytes(b"foreign")
                raise OSError("PRIVATE_FAIL")
    assert path.read_bytes() == b"foreign"
    assert result == {"changed": ["settings"], "diagnostics": ["install_rollback_incomplete"]}
    with pytest.raises(common.HostRefusal, match="install_concurrent_change"):
        common.host_put(home, path, after, None, [])
    assert path.read_bytes() == b"foreign"


def test_failed_precommit_rolls_back_bytes_mode_and_new_directories(home):
    settings = home / "settings.json"
    settings.write_bytes(b"{\"private\":\"RAM_ONLY\"}")
    settings.chmod(0o640)
    original = common.host_read(home, settings)
    result = {"changed": [], "diagnostics": []}
    with common.host_settings_lock(home):
        with pytest.raises(OSError):
            with common.host_transaction(home, result) as (journal, directories):
                for path in (settings, home / "new/owned/wrapper"):
                    before = common.host_read(home, path)
                    after = common.host_put(home, path, before, common.HostObject("file", b"next"), directories)
                    journal.append((path, before, after))
                    result["changed"].append(path.name)
                common.commit_manifest(None, {"owned": True}, read=lambda: None,
                                       write=lambda value: (_ for _ in ()).throw(OSError("PRIVATE_FAIL")))
    current = common.host_read(home, settings)
    assert (current.data, current.mode) == (original.data, original.mode)
    assert not (home / "new").exists()
    assert result == {"changed": [], "diagnostics": []}
    assert not list(home.rglob(".lantern-*"))
    assert all(b"RAM_ONLY" not in p.read_bytes() for p in (home / ".local").rglob("*") if p.is_file())


@pytest.mark.parametrize("readback", ["published", "unreadable", "conflict"])
def test_commit_uncertainty_does_not_run_ram_rollback(home, readback):
    path = home / "owned"
    manifest = {"owned": True}
    result = {"changed": [], "diagnostics": []}

    def read():
        if readback == "unreadable":
            raise OSError("PRIVATE_READBACK")
        return manifest if readback == "published" else {"foreign": True}

    diagnostic = {"published": "install_manifest_durability_unverified",
                  "unreadable": "install_manifest_unverified",
                  "conflict": "install_manifest_conflict"}[readback]
    with common.host_settings_lock(home):
        with pytest.raises(common.CommitUncertain, match=diagnostic):
            with common.host_transaction(home, result) as (journal, directories):
                after = common.host_put(home, path, None, common.HostObject("file", b"published"), directories)
                journal.append((path, None, after))
                result["changed"].append("owned")
                common.commit_manifest(None, manifest, read=read,
                                       write=lambda value: (_ for _ in ()).throw(OSError("POST_PUBLISH")))
    assert path.read_bytes() == b"published"
    assert result["changed"] == ["owned"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "fifo", "directory", "nonempty"])
def test_shared_lock_refuses_unsafe_objects_without_repair(home, kind):
    with common.host_settings_lock(home):
        pass
    lock = home / ".local/state/lantern-common" / common.HOST_LOCK_NAME
    lock.unlink()
    victim = home / "victim"
    victim.write_bytes(b"CANARY")
    victim.chmod(0o600)
    if kind == "symlink":
        lock.symlink_to(victim)
    elif kind == "hardlink":
        os.link(victim, lock)
    elif kind == "public":
        lock.write_bytes(b"")
        lock.chmod(0o644)
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    elif kind == "directory":
        lock.mkdir(mode=0o700)
    else:
        lock.write_bytes(b"nonempty")
        lock.chmod(0o600)
    before = lock.lstat()
    with pytest.raises((common.HostRefusal, CommonError)):
        with common.host_settings_lock(home):
            pytest.fail("唔應該攞到 unsafe lock")
    assert lock.lstat() == before
    assert victim.read_bytes() == b"CANARY"


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_quota_waits_for_shared_host_lock_without_domain_cache_lock(home, monkeypatch, action):
    assert quota.install(home=home, repo_root=REPO, dry_run=False)["ok"]
    before = {path: quota._read(home, path) for path in quota._targets(home).values()}
    previous = copy.deepcopy(Store(home=home).read_cache("installation"))
    monkeypatch.setattr(quota, "INSTALL_LOCK_SECONDS", 0.04)
    with common.host_settings_lock(home):
        kwargs = {"dry_run": False} if action == "install" else {}
        start = time.monotonic()
        result = getattr(quota, action)(home=home, repo_root=REPO, **kwargs)
        assert time.monotonic() - start < 0.5
        Store(home=home).write_cache("throttle", {"not_blocked": True}, deadline=time.monotonic() + 0.1)
    assert result["diagnostics"] == ["install_lock_timeout"]
    assert result["changed"] == []
    assert {path: quota._read(home, path) for path in before} == before
    assert Store(home=home).read_cache("installation") == previous


def test_quota_replans_after_shared_lock_and_preserves_other_lantern(home, monkeypatch):
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "keep", "permissions": {"deny": ["Write"]}}))
    settings.chmod(0o640)
    ready = threading.Event()
    original = quota._plan

    def plan(*args):
        planned = original(*args)
        ready.set()
        return planned

    monkeypatch.setattr(quota, "_plan", plan)
    foreign = {"hooks": [{"type": "command", "command": "reality-owned-fixture", "timeout": 1}]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with common.host_settings_lock(home):
            future = pool.submit(quota.install, home=home, repo_root=REPO, dry_run=False)
            assert ready.wait(3)
            assert not future.done()
            before = common.host_read(home, settings)
            value = json.loads(before.data)
            value["hooks"] = {"SessionStart": [foreign]}
            value["future"] = {"preserve": [1, 2]}
            common.host_put(home, settings, before,
                            common.HostObject("file", json.dumps(value).encode(), before.mode), [])
            agents = home / ".codex/AGENTS.md"
            common.host_put(home, agents, None, common.HostObject("file", b"other lantern block\n"), [])
        installed = future.result(timeout=5)
    assert installed["ok"], installed
    current = json.loads(settings.read_bytes())
    assert current["hooks"]["SessionStart"][0] == foreign
    assert current["future"] == value["future"]
    assert settings.stat().st_mode & 0o777 == 0o640
    assert quota.uninstall(home=home, repo_root=REPO)["ok"]
    assert json.loads(settings.read_bytes()) == value
    assert agents.read_bytes() == b"other lantern block\n"
    assert (home / ".local/state/lantern-common" / common.HOST_LOCK_NAME).is_file()


def test_host_put_returns_journal_identity_without_postreplace_read(home, monkeypatch):
    path = home / "owned"
    replaced = False
    real_replace = common.os.replace

    def replace(*args, **kwargs):
        nonlocal replaced
        real_replace(*args, **kwargs)
        replaced = True

    def read_at(fd, name):
        assert not replaced, "publish 後唔應該再有令 journal 缺漏嘅 read"
        return common.host_read_at(fd, name)

    monkeypatch.setattr(common.os, "replace", replace)
    actual = common.host_put(home, path, None, common.HostObject("file", b"new"), [], read_at=read_at)
    assert replaced
    assert actual == common.host_read(home, path)


def test_host_temp_collision_never_unlinks_foreign_object(home, monkeypatch):
    from uuid import UUID

    fixed = UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(quota, "uuid4", lambda: fixed)
    temporary = home / (".quota-lantern-" + fixed.hex)
    temporary.write_bytes(b"FOREIGN_TEMP_CANARY")
    for kind in ("file", "link"):
        with pytest.raises(FileExistsError):
            quota._put(home, home / "owned", None, quota._Object(kind, b"next"), [])
        assert temporary.read_bytes() == b"FOREIGN_TEMP_CANARY"
        assert not (home / "owned").exists()


def test_shared_lock_serializes_independent_process(home):
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lantern_common.install import host_settings_lock
print('waiting', flush=True)
with host_settings_lock(Path(sys.argv[2])):
    print('acquired', flush=True)
"""
    process = None
    try:
        with common.host_settings_lock(home):
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", script, str(REPO / "tools"), str(home)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={"HOME": str(home), "PATH": "/usr/bin:/bin"}, cwd=home)
            assert select.select([process.stdout], [], [], 3)[0]
            assert process.stdout.readline().strip() == "waiting"
            assert not select.select([process.stdout], [], [], 0.05)[0]
        output, errors = process.communicate(timeout=3)
        assert process.returncode == 0, errors
        assert output.strip() == "acquired"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
