"""只用 fake HOME：封存 durability、容量、重試同跨 rotate generation 反證。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from reality_lantern import archive, core, store as storage
from reality_lantern.core import RealityError
from reality_lantern.store import Store

SCOPE = "a" * 64
CHECK = "b" * 64
CLI = Path(__file__).resolve().parents[1] / "tools/kingdom-reality.py"


def snapshot(**changes):
    value = {"schema": core.SCHEMA_VERSION, "scope_id": SCOPE, "cwd_id": "c" * 64,
             "worktree_id": "d" * 64, "fingerprint": "e" * 64, "complete": True,
             "observed_at": core.utc_now(), "diagnostics": [],
             "coverage": {"paths": 1, "bytes": 12, "ignored": "excluded",
                          "environment": "unverified", "remote": "unverified"}}
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "fake-home"
    monkeypatch.setenv("HOME", str(home))
    instance = Store(home=home)
    instance.get_key(create=True)
    return instance


def run(store, *, complete=True, before=None):
    value = store.start_run(kind="tests", check_id=CHECK, before=before or snapshot())
    if complete:
        store.finish_run(value["id"], outcome="exit0", exit_code=0,
                         signal_number=None, after=snapshot(scope_id=value["scope_id"]))
    return value


def persist(store, state):
    with store._locked() as fd:
        store._write_state(fd, state, None)


def files(store):
    return sorted((store.state_dir / archive.ARCHIVE_DIR).glob("*.json"))


def state_bytes(store):
    return (store.state_dir / storage.STATE_FILE).read_bytes()


def test_capacity_missing_and_rotate_empty_create_nothing(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "absent"
    monkeypatch.setenv("HOME", str(home))
    store = Store(home=home)
    result = store.capacity()
    assert result["ok"] and result["writable"] == "unknown"
    assert result["counts"] == {"runs": 0, "scopes": 0, "pending": 0, "state_bytes": 0,
                                "archives": 0, "archive_bytes": 0}
    assert result["limits"]["archives"] == 32
    assert result["limits"]["archive_bytes"] == 64 * 1024 * 1024
    assert store.rotate()["reset"] == "not_needed"
    assert not home.exists()


def test_capacity_is_readonly_metadata_not_durability(store, monkeypatch):
    run(store)
    store.rotate()
    run(store, complete=False)
    before = {path.name: path.read_bytes() for path in store.state_dir.iterdir() if path.is_file()}
    monkeypatch.setattr(storage, "private_locked", lambda *a, **kw: pytest.fail("純讀唔建鎖"))
    monkeypatch.setattr(storage, "atomic_write", lambda *a, **kw: pytest.fail("純讀唔寫"))
    monkeypatch.setattr(os, "fsync", lambda *a, **kw: pytest.fail("純讀唔 fsync"))
    value = store.capacity()
    assert value["ok"] and value["counts"]["archives"] == 1
    assert value["counts"]["pending"] == 1 and value["counts"]["state_bytes"] == len(state_bytes(store))
    assert value["archive_integrity"] == "unverified" and value["writable"] == "unknown"
    assert before == {path.name: path.read_bytes() for path in store.state_dir.iterdir() if path.is_file()}


def test_rotate_durable_typed_hmac_and_preserves_key_installation(store):
    first = run(store)
    store.invalidate(SCOPE, changed=True)
    store.write_cache("installation", {"test_metadata": True})
    store.deliver("RAW-SESSION-CANARY", SCOPE, "f" * 64, event="SessionStart")
    previous = store.read_state()
    key = store.get_key()
    result = store.rotate(archive=True)
    assert result["ok"] and result["archive_durable"] and result["reset"] == "complete"
    path, = files(store)
    assert path.name == result["archive_id"] + ".json"
    data = path.read_bytes()
    assert archive.verify(data, key, result["archive_id"]) == previous
    assert b"RAW-SESSION-CANARY" not in data and key not in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    current = core.empty_state()
    current["next_sequence"] = core.generation_floor(previous)
    assert store.read_state() == current and store.get_key() == key
    assert store.read_cache() == {"test_metadata": True}
    assert store.rotate()["reset"] == "not_needed" and len(files(store)) == 1
    second = run(store)
    assert second["sequence"] > first["sequence"]
    assert second["generation"] > previous["scopes"][SCOPE]["generation"]
    assert core.build_status(store.read_state(), scope_id=SCOPE)["checks"][0]["validity"] == "match"


@pytest.mark.parametrize("changed", [False, True])
def test_invalidation_updates_highwater_without_breaking_initial_zero(store, changed):
    first = run(store)
    assert first["generation"] == 0 and first["sequence"] == 1
    for _ in range(20):
        store.invalidate(SCOPE, changed=changed)
    state = store.read_state()
    assert state["scopes"][SCOPE]["generation"] == 20
    assert state["next_sequence"] >= 21
    other = run(store, before=snapshot(scope_id="1" * 64))
    assert other["generation"] >= 21
    assert store.read_state()["next_sequence"] > other["generation"]


@pytest.mark.parametrize("legacy_generation", [0, 50000, core.MAX_INTEGER - 2])
def test_rotate_blocks_late_observe_upgrade_but_keeps_applicable_changes(store, legacy_generation):
    old = run(store)
    state = store.read_state()
    scope = state["scopes"][SCOPE]
    scope["generation"] = legacy_generation
    scope["changed_generation"] = legacy_generation
    scope["observation"] = None
    state["projections"][old["id"]].update(validity="unknown", generation=legacy_generation, compared_at=None)
    # 舊 v1 合法：好多 invalidations 但 next_sequence 仍然細。
    assert state["next_sequence"] == 2
    persist(store, state)
    assert store.rotate()["ok"]
    new = run(store, complete=False)
    assert new["generation"] > legacy_generation
    before = store.read_state()
    # 舊 token 即使帶較新 matching snapshot，亦唔可以 publish observation。
    late = snapshot(observed_at="2099-01-01T00:00:00Z")
    result = store.observe(late, expected_generation=legacy_generation)
    assert result == {"ok": False, "diagnostics": ["generation_changed"]}
    assert store.read_state() == before
    store.finish_run(new["id"], outcome="exit0", exit_code=0, signal_number=None, after=snapshot())
    before = store.read_state()
    early = snapshot(fingerprint="f" * 64, observed_at="2000-01-01T00:00:00Z")
    assert not store.observe(early, expected_generation=legacy_generation)["ok"]
    assert store.read_state() == before
    # 真正晚於新 run 嘅差異仍係反證，跨 rotate 亦唔可以丟棄。
    late["fingerprint"] = "f" * 64
    assert not store.observe(late, expected_generation=legacy_generation)["ok"]
    assert store.read_state()["projections"][new["id"]]["validity"] == "stale"


def test_generation_mismatch_keeps_later_real_negative_evidence_in_same_epoch(store):
    old = run(store)
    store.invalidate(SCOPE, changed=False)
    new = run(store)
    late = snapshot(fingerprint="f" * 64, observed_at="2099-01-01T00:00:00Z")
    result = store.observe(late, expected_generation=old["generation"])
    assert result["diagnostics"] == ["generation_changed"]
    state = store.read_state()
    assert state["projections"][old["id"]]["validity"] == "stale"
    assert state["projections"][new["id"]]["validity"] == "stale"


def test_pending_refused_without_archive_or_state_change(store):
    run(store, complete=False)
    original = state_bytes(store)
    result = store.rotate()
    assert not result["ok"] and result["diagnostics"] == ["archive_pending"]
    assert result["reset"] == "not_reset" and not result["archive_durable"]
    assert state_bytes(store) == original and not (store.state_dir / archive.ARCHIVE_DIR).exists()


def test_pending_check_happens_inside_store_lock(store):
    run(store)
    with (store.state_dir / ".lock").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(store.rotate)
            time.sleep(0.03)
            state = store.read_state()
            ident = next(iter(state["completions"]))
            del state["completions"][ident]
            state["projections"][ident]["validity"] = "unknown"
            with storage.private_directory(store.state_dir, error=RealityError) as fd:
                store._write_state(fd, state, None)
            fcntl.flock(handle, fcntl.LOCK_UN)
            result = waiting.result(timeout=2)
    assert result["diagnostics"] == ["archive_pending"] and not files(store)


def test_parallel_rotates_produce_one_archive(store):
    run(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.rotate(), range(4)))
    assert all(result["ok"] for result in results)
    assert sum(result["reset"] == "complete" for result in results) == 1
    assert len(files(store)) == 1


@pytest.mark.parametrize("failure", ["file_fsync", "archive_dir_fsync", "state_dir_fsync", "write", "link", "readback"])
def test_archive_failure_never_resets_and_retries_are_safe(store, monkeypatch, failure):
    run(store)
    original = state_bytes(store)
    real_sync = os.fsync
    with monkeypatch.context() as patch:
        if failure.endswith("fsync"):
            def sync(fd):
                mode = os.fstat(fd).st_mode
                target = os.fstat(fd).st_ino
                directory = store.state_dir / archive.ARCHIVE_DIR
                archive_inode = directory.stat().st_ino if directory.exists() else None
                if ((failure == "file_fsync" and stat.S_ISREG(mode))
                        or (failure == "archive_dir_fsync" and target == archive_inode)
                        or (failure == "state_dir_fsync" and target == store.state_dir.stat().st_ino)):
                    raise OSError("RAW-FAULT-CANARY")
                real_sync(fd)
            patch.setattr(os, "fsync", sync)
        elif failure in {"write", "link"}:
            def fail(*a, **kw):
                raise OSError("RAW-FAULT-CANARY")
            patch.setattr(os, failure, fail)
        else:
            patch.setattr(archive, "_read_equal", lambda *a, **kw: (_ for _ in ()).throw(RealityError("archive_unverified")))
        result = store.rotate()
    assert not result["ok"] and not result["archive_durable"] and result["reset"] == "not_reset"
    assert "RAW-FAULT-CANARY" not in json.dumps(result)
    assert state_bytes(store) == original
    assert store.rotate()["ok"] and len(files(store)) == 1


@pytest.mark.parametrize("failure", ["before_replace", "after_replace", "lying_write", "reset_dir_fsync", "unreadable_after"])
def test_reset_failures_report_durable_archive_without_blind_restore(store, monkeypatch, failure):
    run(store)
    original = state_bytes(store)
    real_write = storage.atomic_write
    real_read = storage.private_read
    with monkeypatch.context() as patch:
        def write(fd, name, data, **kw):
            if failure in {"after_replace", "unreadable_after"}:
                real_write(fd, name, data, **kw)
            if failure == "reset_dir_fsync":
                real_sync = os.fsync
                def sync(handle):
                    if stat.S_ISDIR(os.fstat(handle).st_mode):
                        raise OSError("RESET-CANARY")
                    real_sync(handle)
                with monkeypatch.context() as inner:
                    inner.setattr(os, "fsync", sync)
                    real_write(fd, name, data, **kw)
            if failure == "unreadable_after":
                patch.setattr(storage, "private_read", lambda *a, **kw: (_ for _ in ()).throw(RealityError("state_io")))
            if failure != "lying_write":
                raise OSError("RESET-CANARY")
        patch.setattr(storage, "atomic_write", write)
        result = store.rotate()
    assert not result["ok"] and result["archive_durable"] and len(files(store)) == 1
    assert "CANARY" not in json.dumps(result)
    if failure in {"before_replace", "lying_write"}:
        assert result["reset"] == "not_reset" and state_bytes(store) == original
        assert store.rotate()["ok"] and len(files(store)) == 1
    else:
        assert result["reset"] == "uncertain" and result["diagnostics"] == ["reset_uncertain"]
        assert not store.read_state()["runs"]
        assert store.rotate()["reset"] == "not_needed" and len(files(store)) == 1


def test_archive_is_fsynced_and_verified_before_reset(store, monkeypatch):
    run(store)
    events = []
    real_sync, real_verify, real_write = os.fsync, archive.verify, storage.atomic_write
    def sync(fd):
        events.append("directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync")
        real_sync(fd)
    def verify(*args):
        events.append("verify")
        return real_verify(*args)
    def write(*args, **kwargs):
        assert events.count("file_sync") >= 2
        assert events.count("directory_sync") >= 2
        assert events.count("verify") >= 2 and events[-1] == "verify"
        events.append("reset")
        return real_write(*args, **kwargs)
    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(archive, "verify", verify)
    monkeypatch.setattr(storage, "atomic_write", write)
    assert store.rotate()["ok"] and "reset" in events


def existing_target(store, data=None):
    ident, encoded = archive.encode(store.read_state(), store.get_key())
    directory = store.state_dir / archive.ARCHIVE_DIR
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (ident + ".json")
    path.write_bytes(encoded if data is None else data)
    path.chmod(0o600)
    return path


def test_existing_identical_archive_reused_even_at_count_cap(store, monkeypatch):
    run(store)
    path = existing_target(store)
    before = path.stat().st_ino
    monkeypatch.setattr(archive, "MAX_ARCHIVES", 1)
    assert store.rotate()["ok"]
    assert len(files(store)) == 1 and path.stat().st_ino == before


@pytest.mark.parametrize("kind", ["different", "symlink", "hardlink", "fifo", "directory", "wide_file"])
def test_existing_target_never_overwritten_or_followed(store, tmp_path, kind):
    run(store)
    original = state_bytes(store)
    path = existing_target(store, b"FOREIGN-CANARY")
    foreign = tmp_path.resolve() / "foreign"
    foreign.write_bytes(b"FOREIGN-CANARY")
    foreign.chmod(0o600)
    if kind != "different":
        path.unlink()
        if kind == "symlink":
            path.symlink_to(foreign)
        elif kind == "hardlink":
            os.link(foreign, path)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "directory":
            path.mkdir(mode=0o700)
        else:
            path.write_bytes(b"FOREIGN-CANARY")
            path.chmod(0o644)
    result = store.rotate()
    assert not result["ok"] and not result["archive_durable"]
    assert state_bytes(store) == original and foreign.read_bytes() == b"FOREIGN-CANARY"
    assert "FOREIGN-CANARY" not in json.dumps(result)


def test_target_appearing_during_publish_not_clobbered(store, monkeypatch):
    run(store)
    original = state_bytes(store)
    real_link = os.link
    def link(source, target, **kw):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kw["dst_dir_fd"])
        os.write(fd, b"RACING-FOREIGN-CANARY")
        os.close(fd)
        return real_link(source, target, **kw)
    monkeypatch.setattr(os, "link", link)
    result = store.rotate()
    assert not result["ok"] and result["diagnostics"] == ["archive_conflict"]
    assert state_bytes(store) == original and files(store)[0].read_bytes() == b"RACING-FOREIGN-CANARY"


@pytest.mark.parametrize("kind", ["symlink", "wide_directory", "foreign_entry"])
def test_unsafe_archive_directory_is_not_repaired(store, tmp_path, kind):
    run(store)
    directory = store.state_dir / archive.ARCHIVE_DIR
    if kind == "symlink":
        foreign = tmp_path.resolve() / "outside"
        foreign.mkdir(mode=0o700)
        directory.symlink_to(foreign, target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        if kind == "wide_directory":
            directory.chmod(0o755)
        if kind == "foreign_entry":
            (directory / "RAW-PATH-CANARY").write_text("KEEP")
    assert not store.capacity()["ok"]
    result = store.rotate()
    assert not result["ok"] and result["diagnostics"] == ["state_unsafe"]
    assert "RAW-PATH-CANARY" not in json.dumps(result)
    assert store.read_state()["runs"]


@pytest.mark.parametrize("kind", ["count", "bytes"])
def test_archive_caps_noevict_and_history_full_can_rotate(store, monkeypatch, kind):
    run(store)
    assert store.rotate()["ok"]
    path = files(store)[0]
    archived = path.read_bytes()
    run(store)
    original = state_bytes(store)
    if kind == "count":
        monkeypatch.setattr(archive, "MAX_ARCHIVES", 1)
    else:
        monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", len(archived))
    result = store.rotate()
    assert not result["ok"] and result["diagnostics"] == ["archive_full"]
    assert state_bytes(store) == original and path.read_bytes() == archived and len(files(store)) == 1
    assert "archive_full" in store.capacity()["diagnostics"]


def test_full_active_history_is_explicitly_rotatable(store, monkeypatch):
    monkeypatch.setattr(storage, "MAX_RUNS", 2)
    monkeypatch.setattr(core, "MAX_RUNS", 2)
    run(store)
    run(store)
    assert "recording_full" in store.capacity()["diagnostics"]
    with pytest.raises(RealityError, match="state_full"):
        run(store)
    assert store.rotate()["ok"]
    run(store)
    assert store.capacity()["counts"]["runs"] == 1


@pytest.mark.parametrize("missing_state", [False, True])
def test_key_missing_never_regenerated_over_archives(store, missing_state):
    run(store)
    assert store.rotate()["ok"]
    if missing_state:
        (store.state_dir / storage.STATE_FILE).unlink()
    else:
        run(store)
    (store.state_dir / storage.KEY_FILE).unlink()
    with pytest.raises(RealityError, match="key_missing"):
        store.get_key(create=True)
    assert not (store.state_dir / storage.KEY_FILE).exists()
    assert store.capacity()["diagnostics"] == ["key_missing"]
    if not missing_state:
        result = store.rotate()
        assert not result["ok"] and result["diagnostics"] == ["key_missing"]


def test_archive_hmac_and_typed_schema_reject_tampering(store):
    run(store)
    ident, data = archive.encode(store.read_state(), store.get_key())
    value = json.loads(data)
    value["state"]["next_sequence"] += 1
    with pytest.raises(RealityError, match="archive_unverified"):
        archive.verify(json.dumps(value).encode(), store.get_key(), ident)
    value["state"]["raw_command"] = "RAW-COMMAND-CANARY"
    with pytest.raises(RealityError):
        archive.verify(json.dumps(value).encode(), store.get_key(), ident)


def test_exhausted_integer_refuses_recording_without_changing_existing_facts(store):
    first = run(store)
    state = store.read_state()
    state["scopes"][SCOPE].update(generation=core.MAX_INTEGER, changed_generation=core.MAX_INTEGER, observation=None)
    state["projections"][first["id"]].update(validity="unknown", generation=core.MAX_INTEGER, compared_at=None)
    persist(store, state)
    original = state_bytes(store)
    with pytest.raises(RealityError, match="state_full"):
        run(store)
    with pytest.raises(RealityError, match="state_full"):
        store.invalidate(SCOPE, changed=True)
    assert store.rotate()["diagnostics"] == ["state_full"]
    assert state_bytes(store) == original
    assert core.build_status(state, scope_id=SCOPE)["recording"] == "full"


@pytest.mark.parametrize("deadline", [0, float("nan"), "RAW-DEADLINE-CANARY"])
def test_deadline_failure_no_archive_or_reset(store, deadline):
    run(store)
    original = state_bytes(store)
    result = store.rotate(deadline=deadline)
    assert not result["ok"] and not result["archive_durable"] and state_bytes(store) == original
    assert not files(store) and "RAW-DEADLINE-CANARY" not in json.dumps(result)


def test_expired_after_archive_preserves_state_and_reports_durable(store, monkeypatch):
    run(store)
    original = state_bytes(store)
    real_durable = archive.durable
    def durable(*args, **kwargs):
        ident = real_durable(*args, **kwargs)
        def expired(*a, **kw):
            raise RealityError("lock_timeout")
        monkeypatch.setattr(storage, "check_deadline", expired)
        return ident
    monkeypatch.setattr(archive, "durable", durable)
    result = store.rotate(deadline=time.monotonic() + 5)
    assert not result["ok"] and result["archive_durable"] and result["reset"] == "not_reset"
    assert state_bytes(store) == original and len(files(store)) == 1


def test_actual_32_archive_cap_does_not_evict(store):
    for _ in range(32):
        run(store)
        assert store.rotate()["ok"]
    previous = {path.name: path.read_bytes() for path in files(store)}
    run(store)
    result = store.rotate()
    assert result["diagnostics"] == ["archive_full"]
    assert {path.name: path.read_bytes() for path in files(store)} == previous
    assert store.capacity()["counts"]["archives"] == 32


def test_legacy_scope_only_zero_cannot_reuse_after_rotate(store):
    state = core.empty_state()
    state["scopes"][SCOPE] = {"generation": 0, "changed_generation": 0, "observation": None}
    persist(store, state)
    assert store.rotate()["ok"]
    assert run(store)["generation"] > 0


def test_late_completion_keeps_later_real_negative_evidence_in_same_epoch(store):
    old = run(store, complete=False)
    store.invalidate(SCOPE, changed=False)
    new = run(store)
    store.finish_run(old["id"], outcome="exit0", exit_code=0, signal_number=None,
                     after=snapshot(fingerprint="f" * 64, observed_at="2099-01-01T00:00:00Z"))
    state = store.read_state()
    assert state["projections"][old["id"]]["validity"] == "stale"
    assert state["projections"][new["id"]]["validity"] == "stale"


@pytest.mark.parametrize("exit_code", [0, 23])
def test_exhausted_generation_runner_still_runs_original_once(store, monkeypatch, capsys, exit_code):
    from reality_lantern import runner, snapshot as snapshots
    run(store)
    state = store.read_state()
    state["next_sequence"] = core.MAX_INTEGER
    persist(store, state)
    original = state_bytes(store)
    monkeypatch.setattr(snapshots, "capture", lambda *a, **kw: snapshot())
    # 測 bookkeeping，唔需要向 test session 嘅 terminal 移交 foreground。
    monkeypatch.setattr(runner._Terminal, "__enter__", lambda self: self)
    marker = store.home / "child-count"
    argv = [sys.executable, "-I", "-S", "-B", "-c",
            "from pathlib import Path; import sys; "
            "p=Path(sys.argv[1]); p.write_bytes(p.read_bytes()+b'x' if p.exists() else b'x'); "
            "sys.exit(int(sys.argv[2]))", str(marker), str(exit_code)]
    assert runner.run_check(argv, cwd=store.home, kind="tests", store=store) == exit_code
    assert marker.read_bytes() == b"x" and state_bytes(store) == original
    assert "容量已滿" in capsys.readouterr().err
    assert b"child-count" not in original


def test_rotate_requires_archive_opt_in_no_destructive_mode(store):
    run(store)
    original = state_bytes(store)
    for value in (False, None, 1, "true"):
        with pytest.raises(RealityError, match="invalid_input"):
            store.rotate(archive=value)
    assert state_bytes(store) == original


def cli(store, *args):
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI), "--home", str(store.home), *args],
                          env={"HOME": str(store.home), "PATH": "/usr/bin:/bin"},
                          cwd=store.home, capture_output=True, text=True, timeout=10)


def test_cli_capacity_rotate_and_missing_optin(store):
    run(store)
    result = cli(store, "capacity", "--json")
    assert result.returncode == 0 and json.loads(result.stdout)["counts"]["runs"] == 1
    missing = cli(store, "rotate", "--json")
    assert missing.returncode == 2 and store.read_state()["runs"]
    result = cli(store, "rotate", "--archive", "--json")
    assert result.returncode == 0 and json.loads(result.stdout)["archive_durable"]
    assert cli(store, "rotate", "--archive").returncode == 0 and len(files(store)) == 1


def test_cli_pending_and_key_missing_are_truthful_failures(store):
    pending = run(store, complete=False)
    result = cli(store, "rotate", "--archive", "--json")
    assert result.returncode == 1 and json.loads(result.stdout)["diagnostics"] == ["archive_pending"]
    store.finish_run(pending["id"], outcome="exit0", exit_code=0, signal_number=None, after=snapshot())
    (store.state_dir / storage.KEY_FILE).unlink()
    result = cli(store, "rotate", "--archive", "--json")
    assert result.returncode == 1 and json.loads(result.stdout)["diagnostics"] == ["key_missing"]


def test_cli_observe_preserves_cas_diagnostic(monkeypatch, tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("archive_cli_regression", CLI)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    from reality_lantern import snapshot as snapshots
    monkeypatch.setattr(module, "cached_scope", lambda *a: ({"scopes": {SCOPE: {"generation": 7}}}, SCOPE, b"k" * 32))
    monkeypatch.setattr(module, "cached_status", lambda *a: core.build_status(None))
    monkeypatch.setattr(snapshots, "capture", lambda *a, **kw: snapshot())
    class Fake:
        def observe(self, *a, **kw):
            return {"ok": False, "diagnostics": ["generation_changed"]}
    result = module.observe_once(Fake(), tmp_path)
    assert not result["ok"] and result["diagnostics"] == ["generation_changed"]
