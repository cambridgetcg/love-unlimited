"""Nonce 身份同 legacy adoption；全部 installer IO 只入 fake HOME。"""
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_common import product


@pytest.fixture(params=["quota", "reality", "generic"])
def desk(request, tmp_path, monkeypatch):
    home, repo = tmp_path / "fake-home", tmp_path / "source"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    name = request.param
    spec = product.ProductSpec("fixture-lantern", "fixture_lantern", "kingdom-fixture",
                               "fixture-lantern", {"SessionStart": None, "PostToolUse": "Bash"}, "固定指南。")
    package = spec.package if name == "generic" else name + "_lantern"
    command = spec.command if name == "generic" else "kingdom-" + name
    hook = spec.product_id if name == "generic" else name + "-lantern"
    (repo / "tools/hooks").mkdir(parents=True)
    (repo / "tools" / package / "skill").mkdir(parents=True)
    (repo / "tools" / package / "skill/SKILL.md").write_text("fixture\n")
    (repo / "tools" / (command + ".py")).write_text("pass\n")
    (repo / "tools/hooks" / (hook + ".py")).write_text("pass\n")
    module = product if name == "generic" else importlib.import_module(package + ".install")

    class Desk:
        def call(self, action, **kwargs):
            method = getattr(module, "product_" + action if name == "generic" else action)
            args = (spec,) if name == "generic" else ()
            return method(*args, home=home, repo_root=repo, **kwargs)

        def read(self):
            if name == "generic":
                return product._Product(spec, home, repo).read()
            return module.Store(home=home).read_cache("installation")

        def write(self, value):
            if name == "generic":
                product._Product(spec, home, repo).write(value)
            else:
                module.Store(home=home).write_cache("installation", value)

        def targets(self):
            return product._Product(spec, home, repo).targets if name == "generic" else module._targets(home)

    desk = Desk()
    desk.home, desk.repo, desk.module, desk.name = home, repo, module, name
    desk.store_type = product._Product if name == "generic" else module.Store
    desk.read_method = "read" if name == "generic" else "read_cache"
    desk.write_method = "write" if name == "generic" else "write_cache"
    return desk


def tree(home):
    return {str(path.relative_to(home)): (path.lstat().st_mode, path.lstat().st_ino,
            path.lstat().st_mtime_ns, os.readlink(path) if path.is_symlink()
            else path.read_bytes() if path.is_file() else None) for path in home.rglob("*")}


def test_fresh_nonce_idempotence_and_doctor(desk):
    requested = str(uuid4())
    result = desk.call("install", dry_run=False, installation_nonce=requested)
    assert result["ok"] and result["created"] and result["installation_nonce"] == requested, result
    value = desk.read()
    assert value["schema"].endswith(".installation/2")
    assert set(value) == {"schema", "repo_root", "installation_nonce", "artifacts"}
    assert str(UUID(value["installation_nonce"])) == requested
    before = tree(desk.home)
    result = desk.call("install", dry_run=False, installation_nonce=str(uuid4()))
    assert result["ok"] and not result["created"] and result["changed"] == []
    assert result["installation_nonce"] == requested
    report = desk.call("doctor")
    assert report["installed"] and report["installation_nonce"] == requested
    assert report["activation"] == "unverified"
    assert tree(desk.home) == before


def test_dryrun_has_no_new_identity_or_writes(desk):
    before = tree(desk.home)
    result = desk.call("install", installation_nonce=str(uuid4()))
    assert result["ok"] and not result["created"] and result["installation_nonce"] is None
    assert tree(desk.home) == before
    actual = desk.call("install", dry_run=False)
    before = tree(desk.home)
    result = desk.call("install", installation_nonce=str(uuid4()))
    assert result["ok"] and not result["created"] and result["installation_nonce"] == actual["installation_nonce"]
    assert tree(desk.home) == before


@pytest.mark.parametrize("invalid", ["", "PRIVATE_NONCE", "f" * 32, "AABBAABB-0000-0000-0000-000000000000", {}, 42])
def test_invalid_fresh_nonce_fails_without_state(desk, invalid):
    before = tree(desk.home)
    result = desk.call("install", dry_run=False, installation_nonce=invalid)
    assert not result["ok"] and not result["created"] and result["installation_nonce"] is None
    assert result["diagnostics"] == ["install_invalid_nonce"]
    assert "PRIVATE_NONCE" not in json.dumps(result)
    assert tree(desk.home) == before


def test_missing_install_expected_nonce_is_not_success(desk):
    before = tree(desk.home)
    result = desk.call("uninstall", expected_nonce=str(uuid4()))
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_mismatch"]
    assert tree(desk.home) == before


def test_wrong_nonce_does_not_touch_any_artifacts(desk):
    result = desk.call("install", dry_run=False)
    assert result["ok"]
    before = tree(desk.home)
    rejected = desk.call("uninstall", expected_nonce=str(uuid4()))
    assert not rejected["ok"] and rejected["diagnostics"] == ["install_nonce_mismatch"]
    assert rejected["changed"] == []
    assert tree(desk.home) == before
    assert desk.call("uninstall", expected_nonce=result["installation_nonce"])["ok"]
    assert desk.read()["artifacts"] == {}


def test_reinstallation_gets_new_nonce_and_rejects_old_owner(desk):
    first = desk.call("install", dry_run=False)
    assert desk.call("uninstall", expected_nonce=first["installation_nonce"])["ok"]
    second = desk.call("install", dry_run=False)
    assert second["ok"] and second["created"]
    assert first["installation_nonce"] != second["installation_nonce"]
    before = tree(desk.home)
    assert not desk.call("uninstall", expected_nonce=first["installation_nonce"])["ok"]
    assert tree(desk.home) == before


def test_nonce_rechecked_after_acquiring_lock(desk, monkeypatch):
    original = desk.call("install", dry_run=False)
    old = original["installation_nonce"]
    new = str(uuid4())
    expected_tree = None
    lock_owner = desk.module.common_install if desk.name == "quota" else desk.module
    real_lock = lock_owner.host_settings_lock

    @contextmanager
    def changed_before_lock(*args, **kwargs):
        nonlocal expected_tree
        # 模擬等 host lock 時另一安裝已換身份；以鎖內 manifest 作準。
        manifest = desk.read()
        manifest["installation_nonce"] = new
        desk.write(manifest)
        expected_tree = tree(desk.home)
        with real_lock(*args, **kwargs) as held:
            yield held

    monkeypatch.setattr(lock_owner, "host_settings_lock", changed_before_lock)
    result = desk.call("uninstall", expected_nonce=old)
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_mismatch"]
    assert result["installation_nonce"] == new and result["changed"] == []
    assert tree(desk.home) == expected_tree


def test_multi_artifact_foreign_drift_keeps_owned_leftovers(desk):
    installed = desk.call("install", dry_run=False)
    targets = desk.targets()
    targets["wrapper"].write_bytes(b"foreign wrapper\n")
    targets["codex_skill"].unlink()
    targets["codex_skill"].symlink_to(desk.home / "foreign-missing")
    settings = json.loads(targets["settings"].read_bytes())
    event = next(iter(desk.read()["artifacts"]["settings"]["hashes"]))
    settings["hooks"][event][0]["hooks"][0]["timeout"] = 99
    targets["settings"].write_text(json.dumps(settings))
    result = desk.call("uninstall", expected_nonce=installed["installation_nonce"])
    assert not result["ok"] and result["diagnostics"] == ["install_owned_changed"]
    manifest = desk.read()
    assert manifest["installation_nonce"] == installed["installation_nonce"]
    assert set(manifest["artifacts"]) == {"wrapper", "codex_skill", "settings"}
    assert set(manifest["artifacts"]["settings"]["hashes"]) == {event}
    assert targets["wrapper"].read_bytes() == b"foreign wrapper\n"
    assert os.readlink(targets["codex_skill"]) == str(desk.home / "foreign-missing")
    assert not os.path.lexists(targets["claude_skill"])
    assert not targets["agents"].exists()


@pytest.mark.parametrize("phase", ["before", "after", "unreadable", "silent"])
def test_failed_bookkeeping_never_claims_created(desk, monkeypatch, phase):
    real_write = getattr(desk.store_type, desk.write_method)
    real_read = getattr(desk.store_type, desk.read_method)
    published = False

    def write(*args, **kwargs):
        nonlocal published
        if phase == "silent":
            return None
        if phase == "before":
            raise OSError("PRIVATE_ERROR")
        real_write(*args, **kwargs)
        published = True
        raise OSError("PRIVATE_ERROR")

    def read(*args, **kwargs):
        if published and phase == "unreadable":
            raise OSError("PRIVATE_ERROR")
        return real_read(*args, **kwargs)

    monkeypatch.setattr(desk.store_type, desk.write_method, write)
    monkeypatch.setattr(desk.store_type, desk.read_method, read)
    result = desk.call("install", dry_run=False)
    assert not result["ok"] and not result["created"]
    assert result["installation_nonce"] is None
    assert "PRIVATE_ERROR" not in json.dumps(result)
    assert all(os.path.lexists(path) == published for path in desk.targets().values())
    if published:
        assert result["diagnostics"] == ["install_manifest_unverified" if phase == "unreadable"
                                         else "install_manifest_durability_unverified"]
    else:
        assert result["changed"] == []


@pytest.mark.parametrize("invalid", [None, "not-a-uuid", "f" * 32, {}, True])
def test_invalid_v2_manifest_nonce_is_never_accepted(desk, invalid):
    assert desk.call("install", dry_run=False)["ok"]
    manifest = desk.read()
    manifest["installation_nonce"] = invalid
    desk.write(manifest)
    before = tree(desk.home)
    for action in ("install", "doctor", "uninstall"):
        report = desk.call(action, **({"dry_run": False} if action == "install" else {}))
        assert not report["ok"] and report["diagnostics"] == ["install_manifest_conflict"]
    assert tree(desk.home) == before


def test_legacy_adoption_is_byte_and_metadata_preserving(desk, monkeypatch):
    if desk.name == "generic":
        return  # 新 engine 冇 v1；兩個舊 engine 先有 migration seam。
    assert desk.call("install", dry_run=False)["ok"]
    legacy = desk.read()
    legacy["schema"] = desk.module.LEGACY_MANIFEST_SCHEMA
    del legacy["installation_nonce"]
    desk.write(legacy)
    before = tree(desk.home)

    def forbidden(*args, **kwargs):
        pytest.fail("健康 legacy 唔應改寫")

    with monkeypatch.context() as patch:
        patch.setattr(desk.store_type, desk.write_method, forbidden)
        result = desk.call("install", dry_run=False, installation_nonce=str(uuid4()))
        assert result["ok"] and not result["created"] and result["installation_nonce"] is None
        assert result["changed"] == []
        result = desk.call("doctor")
        assert result["installed"] and result["installation_nonce"] is None
        assert not desk.call("uninstall", expected_nonce=str(uuid4()))["ok"]
    assert tree(desk.home) == before
    assert desk.call("uninstall")["ok"]
    assert desk.read()["schema"] == desk.module.LEGACY_MANIFEST_SCHEMA
    assert desk.read()["artifacts"] == {}


HISTORY_NAME = "installation-nonces.json"
HISTORY_SCHEMA = "kingdom.lantern.installation-nonces/1"


def history_path(desk):
    product_id = "fixture-lantern" if desk.name == "generic" else desk.name + "-lantern"
    return desk.home / ".local/state" / product_id / HISTORY_NAME


def seed_history(desk, nonces):
    path = history_path(desk)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": HISTORY_SCHEMA, "nonces": sorted(nonces)}))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("replacement", ["fresh", "automatic", "reuse", "multi_cycle"])
def test_public_api_delayed_first_lock_never_deletes_new_incarnation(desk, monkeypatch, replacement):
    nonce = str(uuid4())
    assert desk.call("install", dry_run=False, installation_nonce=nonce)["created"]
    # Delay BEFORE the first product/host lock, not while holding Quota's product lock.
    lock_name = "_installation_lock" if desk.name == "quota" else "host_settings_lock"
    lock = getattr(desk.module, lock_name)
    intervened, attempts, final_tree = False, [], None
    survivor = None

    @contextmanager
    def delayed(*args, **kwargs):
        nonlocal intervened, final_tree, survivor
        if not intervened:
            intervened = True
            assert desk.call("uninstall", expected_nonce=nonce)["ok"]
            if replacement == "multi_cycle":
                for _ in range(3):
                    middle = desk.call("install", dry_run=False, installation_nonce=str(uuid4()))
                    assert middle["ok"] and middle["created"]
                    assert desk.call("uninstall", expected_nonce=middle["installation_nonce"])["ok"]
            supplied = {} if replacement == "automatic" else {"installation_nonce":
                nonce if replacement in ("reuse", "multi_cycle") else str(uuid4())}
            attempt = desk.call("install", dry_run=False, **supplied)
            attempts.append(attempt)
            # Always leave a real new installation for the delayed caller to encounter.
            survivor = attempt if attempt["ok"] else desk.call("install", dry_run=False)
            assert survivor["ok"] and survivor["created"]
            final_tree = tree(desk.home)
        with lock(*args, **kwargs) as held:
            yield held

    monkeypatch.setattr(desk.module, lock_name, delayed)
    stale = desk.call("uninstall", expected_nonce=nonce)
    assert intervened
    assert not stale["ok"] and stale["diagnostics"] == ["install_nonce_mismatch"], (attempts, stale)
    assert stale["changed"] == [] and tree(desk.home) == final_tree
    assert desk.call("doctor")["installed"]
    if replacement in ("reuse", "multi_cycle"):
        assert not attempts[0]["ok"] and not attempts[0]["created"]
        assert attempts[0]["diagnostics"] == ["install_nonce_reused"]
    assert survivor["installation_nonce"] != nonce


def test_nonce_history_survives_all_cycles_and_dryrun_is_readonly(desk):
    nonces = [str(uuid4()) for _ in range(4)]
    for nonce in nonces:
        assert desk.call("install", dry_run=False, installation_nonce=nonce)["created"]
        before = tree(desk.home)
        adoption = desk.call("install", dry_run=False, installation_nonce=nonce)
        assert adoption["ok"] and not adoption["created"] and adoption["changed"] == []
        assert tree(desk.home) == before
        assert desk.call("uninstall", expected_nonce=nonce)["ok"]
    path = history_path(desk)
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600 and path.stat().st_nlink == 1
    assert json.loads(path.read_bytes()) == {"schema": HISTORY_SCHEMA, "nonces": sorted(nonces)}
    before = tree(desk.home)
    for nonce in nonces:
        for dry_run in (True, False):
            result = desk.call("install", dry_run=dry_run, installation_nonce=nonce)
            assert not result["ok"] and result["diagnostics"] == ["install_nonce_reused"]
            assert not result["created"] and result["changed"] == []
            assert tree(desk.home) == before
    assert desk.call("install", installation_nonce=str(uuid4()))["ok"]
    assert tree(desk.home) == before


@pytest.mark.parametrize("tombstone", [False, True])
def test_existing_v2_without_history_protected_before_retirement(desk, tombstone):
    nonce = str(uuid4())
    assert desk.call("install", dry_run=False, installation_nonce=nonce)["ok"]
    if tombstone:
        assert desk.call("uninstall", expected_nonce=nonce)["ok"]
    # Model an installation/tombstone written by the pre-history v2 installer.
    history_path(desk).unlink(missing_ok=True)
    if not tombstone:
        before = tree(desk.home)
        assert not desk.call("install", dry_run=False, installation_nonce=nonce)["created"]
        assert desk.call("doctor")["installed"]
        assert tree(desk.home) == before  # adoption must not silently migrate even v2
        assert desk.call("uninstall", expected_nonce=nonce)["ok"]
    other = str(uuid4())
    assert desk.call("install", dry_run=False, installation_nonce=other)["created"]
    assert desk.call("uninstall", expected_nonce=other)["ok"]
    result = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_reused"]
    assert json.loads(history_path(desk).read_bytes())["nonces"] == sorted([nonce, other])


def test_nonce_history_capacity_does_not_evict_and_allows_adoption_retirement(desk, monkeypatch):
    monkeypatch.setattr(product, "MAX_NONCES", 3, raising=False)
    nonces = [str(uuid4()) for _ in range(3)]
    for nonce in nonces[:-1]:
        assert desk.call("install", dry_run=False, installation_nonce=nonce)["created"]
        assert desk.call("uninstall", expected_nonce=nonce)["ok"]
    assert desk.call("install", dry_run=False, installation_nonce=nonces[-1])["created"]
    before = tree(desk.home)
    assert not desk.call("install", dry_run=False, installation_nonce=nonces[-1])["created"]
    assert tree(desk.home) == before
    assert desk.call("uninstall", expected_nonce=nonces[-1])["ok"]
    before = tree(desk.home)
    for dry_run in (True, False):
        result = desk.call("install", dry_run=dry_run)
        assert not result["ok"] and result["diagnostics"] == ["install_nonce_history_full"]
        assert result["changed"] == [] and not result["created"]
        assert tree(desk.home) == before
    assert json.loads(history_path(desk).read_bytes())["nonces"] == sorted(nonces)


@pytest.mark.parametrize("kind", ["json", "schema", "duplicate", "nonce", "extra", "oversize", "mode", "symlink", "hardlink", "fifo"])
def test_bad_nonce_history_fails_closed_before_any_host_change(desk, kind):
    nonce = str(uuid4())
    path = seed_history(desk, [nonce])
    if kind == "json":
        path.write_bytes(b'PRIVATE_INVALID_JSON')
    elif kind == "schema":
        path.write_text(json.dumps({"schema": "PRIVATE_UNKNOWN", "nonces": []}))
    elif kind == "duplicate":
        path.write_text(json.dumps({"schema": HISTORY_SCHEMA, "nonces": [nonce, nonce]}))
    elif kind == "nonce":
        path.write_text(json.dumps({"schema": HISTORY_SCHEMA, "nonces": ["PRIVATE_NONCE"]}))
    elif kind == "extra":
        path.write_text(json.dumps({"schema": HISTORY_SCHEMA, "nonces": [], "PRIVATE_EXTRA": True}))
    elif kind == "oversize":
        path.write_bytes(b" " * (256 * 1024 + 1))
    elif kind == "mode":
        path.chmod(0o644)
    else:
        outside = desk.home / "outside"
        outside.write_bytes(path.read_bytes())
        outside.chmod(0o600)
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
    before = tree(desk.home)
    for dry_run in (True, False):
        result = desk.call("install", dry_run=dry_run)
        assert not result["ok"] and not result["created"]
        assert result["diagnostics"] == ["install_nonce_history_unavailable"]
        assert result["changed"] == [] and "PRIVATE" not in json.dumps(result)
        # Real install can create its lock files, but never changes foreign history/host targets.
        assert all(not os.path.lexists(target) for target in desk.targets().values())
        assert tree(desk.home)[str(path.relative_to(desk.home))] == before[str(path.relative_to(desk.home))]


@pytest.mark.parametrize("action", ["install", "uninstall_existing_v2"])
@pytest.mark.parametrize("phase", ["file_fsync", "directory_fsync", "ancestor_fsync", "after_write", "silent", "readback"])
def test_nonce_reservation_faults_stop_before_host_mutation(desk, monkeypatch, action, phase):
    from lantern_common import private
    nonce = str(uuid4())
    if action == "uninstall_existing_v2":
        assert desk.call("install", dry_run=False, installation_nonce=nonce)["ok"]
        history_path(desk).unlink(missing_ok=True)
    target_before = {key: tree(desk.home).get(str(path.relative_to(desk.home)))
                     for key, path in desk.targets().items()}
    atomic, fsync, read = private.atomic_write, os.fsync, private.private_read
    writing, published, fired = False, False, []

    def write_fault(fd, name, data, **kwargs):
        nonlocal writing, published
        if name != HISTORY_NAME:
            return atomic(fd, name, data, **kwargs)
        if phase == "silent":
            fired.append(phase)
            return
        writing = True
        try:
            result = atomic(fd, name, data, **kwargs)
            published = True
            if phase == "after_write":
                fired.append(phase)
                raise OSError("PRIVATE_WRITE")
            return result
        finally:
            writing = False

    def fsync_fault(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        ancestor = history_path(desk).parent.parent
        fail = ((writing and phase == "file_fsync" and not is_dir)
                or (writing and phase == "directory_fsync" and is_dir)
                or (phase == "ancestor_fsync" and ancestor.exists()
                    and os.fstat(fd).st_ino == ancestor.stat().st_ino))
        if fail:
            fired.append(phase)
            raise OSError("PRIVATE_FSYNC")
        return fsync(fd)

    def read_fault(fd, name, *args, **kwargs):
        if name == HISTORY_NAME and phase == "readback" and published:
            fired.append(phase)
            raise OSError("PRIVATE_READBACK")
        return read(fd, name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(private, "atomic_write", write_fault)
        patch.setattr(private, "private_read", read_fault)
        patch.setattr(os, "fsync", fsync_fault)
        result = (desk.call("install", dry_run=False, installation_nonce=nonce) if action == "install"
                  else desk.call("uninstall", expected_nonce=nonce))
    assert fired, result
    assert not result["ok"] and not result.get("created", False) and result["changed"] == []
    assert result["diagnostics"] == ["install_nonce_reservation_uncertain"]
    assert "PRIVATE" not in json.dumps(result)
    assert {key: tree(desk.home).get(str(path.relative_to(desk.home)))
            for key, path in desk.targets().items()} == target_before
    if action == "install" and history_path(desk).exists():
        # Published-but-uncertain reservations are burned, never recovered as fresh identity.
        retry = desk.call("install", dry_run=False, installation_nonce=nonce)
        assert not retry["ok"] and retry["diagnostics"] == ["install_nonce_reused"]
        assert desk.call("install", dry_run=False)["created"]
    if action == "uninstall_existing_v2":
        assert desk.call("doctor")["installed"]
        assert desk.call("uninstall", expected_nonce=nonce)["ok"]
        assert desk.call("install", dry_run=False)["created"]
        assert desk.call("uninstall")["ok"]
        assert desk.call("install", dry_run=False, installation_nonce=nonce)["diagnostics"] == ["install_nonce_reused"]


def test_reserved_nonce_burned_on_later_commit_failure(desk, monkeypatch):
    nonce = str(uuid4())
    def fail(*args, **kwargs):
        raise OSError("PRIVATE_MANIFEST")
    with monkeypatch.context() as patch:
        patch.setattr(desk.store_type, desk.write_method, fail)
        result = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert not result["ok"] and not result["created"] and result["changed"] == []
    assert result.get("nonce_reservation") == "reserved"
    assert nonce in json.loads(history_path(desk).read_bytes())["nonces"]
    assert all(not os.path.lexists(path) for path in desk.targets().values())
    retry = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert not retry["ok"] and retry["diagnostics"] == ["install_nonce_reused"]
    assert desk.call("install", dry_run=False)["created"]


def test_durable_nonce_is_present_before_first_host_write(desk, monkeypatch):
    from lantern_common import private
    nonce = str(uuid4())
    fsync, atomic = os.fsync, private.atomic_write
    writing, synced_file, synced_dirs = False, False, set()
    def atomic_guard(fd, name, data, **kwargs):
        nonlocal writing
        writing = name == HISTORY_NAME
        try:
            return atomic(fd, name, data, **kwargs)
        finally:
            writing = False
    def witnessed(fd):
        nonlocal synced_file
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            synced_dirs.add((info.st_dev, info.st_ino))
        elif writing:
            synced_file = True
        return fsync(fd)
    put_name = "_put" if desk.name == "quota" else "host_put"
    put = getattr(desk.module, put_name)
    calls = []
    def checked(*args, **kwargs):
        assert synced_file
        path = history_path(desk)
        assert json.loads(path.read_bytes())["nonces"] == [nonce]
        current = path.parent
        while True:
            info = current.stat()
            assert (info.st_dev, info.st_ino) in synced_dirs
            if current == desk.home:
                break
            current = current.parent
        calls.append(True)
        return put(*args, **kwargs)
    monkeypatch.setattr(private, "atomic_write", atomic_guard)
    monkeypatch.setattr(os, "fsync", witnessed)
    monkeypatch.setattr(desk.module, put_name, checked)
    result = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert result["ok"] and result["created"] and calls, result


_CRASH_AFTER_RESERVATION = r'''
import importlib, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lantern_common import product
name, home, repo, nonce = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
module = product if name == "generic" else importlib.import_module(name + "_lantern.install")
def crash(*args, **kwargs):
    os._exit(91)
setattr(module, "_put" if name == "quota" else "host_put", crash)
if name == "generic":
    spec = product.ProductSpec("fixture-lantern", "fixture_lantern", "kingdom-fixture",
        "fixture-lantern", {"SessionStart": None, "PostToolUse": "Bash"}, "固定指南。")
    result = product.product_install(spec, home=home, repo_root=repo, dry_run=False, installation_nonce=nonce)
else:
    result = module.install(home=home, repo_root=repo, dry_run=False, installation_nonce=nonce)
raise AssertionError(result)
'''


def test_real_child_crash_after_reservation_cannot_reuse_nonce(desk):
    nonce = str(uuid4())
    child = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", _CRASH_AFTER_RESERVATION,
        str(ROOT / "tools"), desk.name, str(desk.home), str(desk.repo), nonce], cwd=desk.home,
        env={"HOME": str(desk.home), "CODEX_HOME": str(desk.home / ".codex"), "PATH": "/usr/bin:/bin",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True, text=True, timeout=5)
    assert child.returncode == 91, (child.stdout, child.stderr)
    assert desk.read() is None
    assert all(not os.path.lexists(path) for path in desk.targets().values())
    assert json.loads(history_path(desk).read_bytes())["nonces"] == [nonce]
    result = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_reused"]
    assert desk.call("install", dry_run=False)["created"]


def test_automatic_uuid_collision_is_rejected_not_reincarnated(desk, monkeypatch):
    first = desk.call("install", dry_run=False)
    assert first["created"]
    assert desk.call("uninstall", expected_nonce=first["installation_nonce"])["ok"]
    before = tree(desk.home)
    with monkeypatch.context() as patch:
        patch.setattr(desk.module, "uuid4", lambda: UUID(first["installation_nonce"]))
        result = desk.call("install", dry_run=False)
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_reused"]
    assert tree(desk.home) == before
    assert desk.call("install", dry_run=False)["created"]


@pytest.mark.parametrize("failure", ["invalid", "full"])
def test_retirement_cannot_skip_unavailable_history(desk, monkeypatch, failure):
    nonce = str(uuid4())
    assert desk.call("install", dry_run=False, installation_nonce=nonce)["created"]
    if failure == "full":
        monkeypatch.setattr(product, "MAX_NONCES", 3)
        seed_history(desk, [str(uuid4()) for _ in range(3)])
    else:
        history_path(desk).write_bytes(b"PRIVATE_HISTORY")
    before = tree(desk.home)
    # No mutation/ownership transfer on adoption, even if history needs operator care.
    adoption = desk.call("install", dry_run=False, installation_nonce=nonce)
    assert adoption["ok"] and not adoption["created"] and tree(desk.home) == before
    result = desk.call("uninstall", expected_nonce=nonce)
    assert not result["ok"] and result["changed"] == []
    assert result["diagnostics"] == ["install_nonce_history_full" if failure == "full"
                                     else "install_nonce_history_unavailable"]
    assert desk.call("doctor")["installed"] and tree(desk.home) == before


def test_actual_nonce_history_bound_retains_every_uuid(desk):
    assert product.MAX_NONCES == 4096 and product.MAX_NONCE_BYTES == 256 * 1024
    nonces = [str(UUID(int=index)) for index in range(1, 4097)]
    seed_history(desk, nonces[:-1])
    result = desk.call("install", dry_run=False, installation_nonce=nonces[-1])
    assert result["ok"] and result["created"]
    assert desk.call("uninstall", expected_nonce=nonces[-1])["ok"]
    path = history_path(desk)
    assert len(path.read_bytes()) < product.MAX_NONCE_BYTES
    assert json.loads(path.read_bytes())["nonces"] == nonces
    before = tree(desk.home)
    result = desk.call("install", dry_run=False)
    assert not result["ok"] and result["diagnostics"] == ["install_nonce_history_full"]
    assert tree(desk.home) == before
    assert desk.call("install", dry_run=False, installation_nonce=nonces[0])["diagnostics"] == ["install_nonce_reused"]
    assert tree(desk.home) == before
