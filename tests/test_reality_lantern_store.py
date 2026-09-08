"""Immutable 收據、並行／競態、private IO 同 cache-only 邊界。"""
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
from lantern_common import private
from reality_lantern import core, store as storage
from reality_lantern.core import RealityError, build_status, keyed_digest
from reality_lantern.store import Store

SCOPE = "a" * 64
CHECK = "b" * 64


def snapshot(**changes):
    result = {"schema": core.SCHEMA_VERSION, "scope_id": SCOPE, "cwd_id": "c" * 64,
              "worktree_id": "d" * 64, "fingerprint": "e" * 64, "complete": True,
              "observed_at": "2026-09-07T10:00:00.000000Z", "diagnostics": [],
              "coverage": {"paths": 1, "bytes": 12, "ignored": "excluded",
                           "environment": "unverified", "remote": "unverified"}}
    result.update(changes)
    return result


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Synthetic before@10:00:00 早於 run，11:00 差異先係 run 後反證；唔借真時鐘。
    monkeypatch.setattr(storage, "utc_now", lambda: "2026-09-07T10:00:01Z")
    return Store(home=tmp_path.resolve())


def start(store, **kwargs):
    return store.start_run(kind=kwargs.pop("kind", "tests"), check_id=kwargs.pop("check_id", CHECK),
                           before=kwargs.pop("before", snapshot()), **kwargs)


def finish(store, run, **kwargs):
    outcome = kwargs.pop("outcome", "exit0")
    return store.finish_run(run["id"], outcome=outcome, exit_code=kwargs.pop("exit_code", 0),
                            signal_number=kwargs.pop("signal_number", None), after=kwargs.pop("after", snapshot()), **kwargs)


def check(store, scope=SCOPE, index=0):
    return build_status(store.read_state(), scope_id=scope)["checks"][index]


def test_constructor_and_missing_reads_create_nothing(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "missing"
    instance = Store(home=home)
    monkeypatch.setattr(storage, "private_locked", lambda *a, **k: pytest.fail("純讀唔鎖"))
    monkeypatch.setattr(storage, "atomic_write", lambda *a, **k: pytest.fail("純讀唔寫"))
    assert instance.get_key(create=False) is None
    assert instance.read_state() == core.empty_state()
    assert instance.read_cache(default={"installed": False}) == {"installed": False}
    assert not home.exists()


def test_key_exact_bytes_mode_and_stable(store):
    key = store.get_key(create=True)
    assert type(key) is bytes and len(key) == 32
    assert key == store.get_key(create=True) == store.get_key(create=False)
    assert stat.S_IMODE(store.state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.state_dir / storage.KEY_FILE).stat().st_mode) == 0o600
    assert not (store.state_dir / storage.STATE_FILE).exists()


def test_read_state_is_cache_only_and_detached(store, monkeypatch):
    store.get_key(create=True)
    start(store)
    cached = (store.state_dir / storage.STATE_FILE).read_bytes()
    monkeypatch.setattr(storage, "private_locked", lambda *a, **k: pytest.fail("純讀唔鎖"))
    monkeypatch.setattr(os, "scandir", lambda *a, **k: pytest.fail("純讀唔掃"))
    state = store.read_state()
    state["runs"].clear()
    assert len(store.read_state()["runs"]) == 1
    assert (store.state_dir / storage.STATE_FILE).read_bytes() == cached
    assert store.get_key(create=False)


def test_pending_and_completion_remain_independent_immutable_facts(store):
    run = start(store)
    original = deepcopy(run)
    assert check(store)["outcome"] == "pending" and check(store)["validity"] == "unknown"
    receipt = finish(store, run)
    state = store.read_state()
    assert state["runs"][run["id"]] == original
    assert state["completions"][run["id"]] == receipt
    assert check(store)["outcome"] == "exit0" and check(store)["validity"] == "match"
    with pytest.raises(RealityError, match="completion_exists"):
        finish(store, run, outcome="nonzero", exit_code=9)
    assert store.read_state()["completions"][run["id"]] == receipt
    run["before"]["fingerprint"] = "f" * 64
    receipt["outcome"] = "pending"
    assert store.read_state()["runs"][run["id"]] == original


def test_older_completion_never_hides_new_pending_or_failure(store):
    old = start(store)
    new = start(store)
    finish(store, old)
    assert check(store)["run_id"] == new["id"] and check(store)["outcome"] == "pending"
    finish(store, new, outcome="nonzero", exit_code=3)
    assert check(store)["outcome"] == "nonzero"
    assert len(store.read_state()["runs"]) == 2


def test_separate_checks_and_scopes_do_not_borrow_results(store):
    first = start(store)
    finish(store, first)
    second = start(store, check_id="f" * 64, kind="build")
    another = start(store, before=snapshot(scope_id="1" * 64))
    finish(store, another, outcome="nonzero", exit_code=2, after=snapshot(scope_id="1" * 64))
    status = build_status(store.read_state(), scope_id=SCOPE)
    assert [item["outcome"] for item in status["checks"]] == ["pending", "exit0"]
    assert check(store, scope="1" * 64)["outcome"] == "nonzero"
    assert second["sequence"] > first["sequence"]


@pytest.mark.parametrize("changed", [True, False])
def test_invalidation_separates_known_change_from_generic_bash(store, changed):
    run = start(store)
    finish(store, run)
    facts = deepcopy(store.read_state()["completions"])
    assert store.invalidate(SCOPE, changed=changed)
    assert check(store)["validity"] == ("stale" if changed else "unknown")
    assert store.read_state()["scopes"][SCOPE]["generation"] == 1
    store.observe(snapshot(), expected_generation=1)
    assert check(store)["validity"] == ("stale" if changed else "match")
    assert store.read_state()["completions"] == facts


def test_observed_change_then_revert_stays_stale(store):
    run = start(store)
    finish(store, run)
    store.observe(snapshot(fingerprint="f" * 64, observed_at="2026-09-07T11:00:00Z"), expected_generation=0)
    assert check(store)["validity"] == "stale"
    store.observe(snapshot(observed_at="2026-09-07T12:00:00Z"), expected_generation=0)
    assert check(store)["validity"] == "stale"
    new = start(store, before=snapshot(observed_at="2026-09-07T13:00:00Z"))
    finish(store, new, after=snapshot(observed_at="2026-09-07T14:00:00Z"))
    assert check(store)["validity"] == "match"
    assert store.read_state()["projections"][run["id"]]["validity"] == "stale"


def test_new_runner_recovers_generic_unknown_but_not_stale_other_checks(store):
    first = start(store)
    finish(store, first)
    store.invalidate(SCOPE, changed=False)
    second = start(store, check_id="f" * 64)
    finish(store, second)
    assert all(item["validity"] == "match" for item in build_status(store.read_state(), scope_id=SCOPE)["checks"])
    store.invalidate(SCOPE, changed=True)
    third = start(store, check_id="f" * 64)
    finish(store, third)
    assert [item["validity"] for item in build_status(store.read_state(), scope_id=SCOPE)["checks"]] == ["match", "stale"]


@pytest.mark.parametrize("changed", [False, True])
def test_generation_changes_during_run_prevent_fresh_completion_claim(store, changed):
    run = start(store)
    store.invalidate(SCOPE, changed=changed)
    finish(store, run)
    assert check(store)["validity"] == ("stale" if changed else "unknown")


def test_observe_cas_and_older_snapshot_never_roll_back(store):
    run = start(store)
    finish(store, run)
    store.invalidate(SCOPE, changed=False)
    before = store.read_state()
    result = store.observe(snapshot(), expected_generation=0)
    assert not result["ok"] and result["diagnostics"] == ["generation_changed"]
    assert store.read_state() == before
    store.observe(snapshot(observed_at="2026-09-07T12:00:00Z"), expected_generation=1)
    result = store.observe(snapshot(fingerprint="f" * 64), expected_generation=1)
    assert not result["ok"] and result["diagnostics"] == ["observation_older"]
    assert check(store)["validity"] == "match"


def test_late_old_completion_is_projected_against_latest_observation(store):
    old = start(store)
    new = start(store, before=snapshot(fingerprint="f" * 64, observed_at="2026-09-07T11:00:00Z"))
    finish(store, new, after=snapshot(fingerprint="f" * 64, observed_at="2026-09-07T12:00:00Z"))
    finish(store, old, after=snapshot(observed_at="2026-09-07T10:30:00Z"))
    assert check(store)["run_id"] == new["id"] and check(store)["validity"] == "match"
    assert store.read_state()["projections"][old["id"]]["validity"] == "stale"


@pytest.mark.parametrize("after", [snapshot(complete=False, fingerprint=None, diagnostics=["snapshot_race"]),
    snapshot(complete=False, fingerprint=None, diagnostics=["not_git"], worktree_id=None)])
def test_incomplete_completion_cannot_be_healed_into_match(store, after):
    run = start(store)
    finish(store, run, after=after)
    assert check(store)["validity"] == "unknown"
    store.observe(snapshot(observed_at="2026-09-07T11:00:00Z"), expected_generation=0)
    assert check(store)["validity"] == "unknown"


def test_new_worktree_identity_makes_old_receipt_stale(store):
    run = start(store)
    finish(store, run, after=snapshot(worktree_id="f" * 64))
    assert check(store)["validity"] == "stale"


def test_unknown_scope_does_not_create_state(store):
    assert not store.invalidate(SCOPE, changed=True)
    assert not store.observe(snapshot(), expected_generation=0)["ok"]
    assert not store.state_dir.exists()


def test_delivery_keys_raw_session_and_throttles_material_change(store, monkeypatch):
    store.get_key(create=True)
    start(store)
    monkeypatch.setattr(storage, "utc_now", lambda: "2026-09-07T11:00:00Z")
    assert store.deliver("SESSION-RAW-CANARY", SCOPE, "1" * 64, event="SessionStart")
    assert not store.deliver("SESSION-RAW-CANARY", SCOPE, "1" * 64, event="UserPromptSubmit")
    assert not store.deliver("SESSION-RAW-CANARY", SCOPE, "2" * 64, event="UserPromptSubmit")
    monkeypatch.setattr(storage, "utc_now", lambda: "2026-09-07T11:01:01Z")
    assert store.deliver("SESSION-RAW-CANARY", SCOPE, "2" * 64, event="UserPromptSubmit")
    assert "SESSION-RAW-CANARY" not in (store.state_dir / storage.STATE_FILE).read_text()
    before = (store.state_dir / storage.STATE_FILE).read_bytes()
    assert not store.deliver("OTHER-SESSION", SCOPE, "3" * 64, event="SubagentStart")
    assert (store.state_dir / storage.STATE_FILE).read_bytes() == before


def test_missing_key_is_not_regenerated_over_evidence(store):
    start(store)
    with pytest.raises(RealityError, match="key_missing"):
        store.get_key(create=True)
    assert not (store.state_dir / storage.KEY_FILE).exists()


def test_full_store_refuses_new_facts_without_deleting_history(store, monkeypatch):
    monkeypatch.setattr(storage, "MAX_RUNS", 2)
    monkeypatch.setattr(core, "MAX_RUNS", 2)
    first, second = start(store), start(store, check_id="f" * 64)
    before = store.read_state()
    with pytest.raises(RealityError, match="state_full"):
        start(store)
    assert store.read_state() == before
    assert build_status(before, scope_id=SCOPE)["recording"] == "full"
    assert "暫停記錄" in core.render_brief(build_status(before, scope_id=SCOPE))
    finish(store, first)
    finish(store, second, outcome="nonzero", exit_code=5)
    assert len(store.read_state()["completions"]) == 2


def test_byte_capacity_failure_keeps_original_atomic_state(store, monkeypatch):
    start(store)
    before = (store.state_dir / storage.STATE_FILE).read_bytes()
    monkeypatch.setattr(storage, "MAX_STATE_BYTES", len(before) + 4)
    with pytest.raises(RealityError, match="state_full"):
        start(store)
    assert (store.state_dir / storage.STATE_FILE).read_bytes() == before


def test_lock_timeout_is_bounded_and_does_not_change_evidence(store):
    start(store)
    before = store.read_state()
    with (store.state_dir / ".lock").open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        beginning = time.monotonic()
        with pytest.raises(RealityError, match="lock_timeout"):
            start(store, deadline=beginning + 0.025)
        assert time.monotonic() - beginning < 0.2
    assert store.read_state() == before


def test_default_store_wait_can_outlast_hook_budget_without_retry(store):
    start(store)
    with (store.state_dir / ".lock").open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(start, store)
            time.sleep(0.08)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            assert waiting.result(timeout=1)["sequence"] == 2


def test_parallel_writers_get_unique_sequences_and_no_lost_facts(store):
    store.get_key(create=True)
    def one(index):
        # 正常 Store 有獨立 bounded wait，唔靠 caller retry 補救並行寫入。
        return start(store, check_id=f"{index:064x}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(one, range(1, 17)))
    assert sorted(run["sequence"] for run in runs) == list(range(1, 17))
    assert len({run["id"] for run in runs}) == 16
    assert len(store.read_state()["runs"]) == 16


def test_parallel_key_creation_returns_one_identity(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: store.get_key(create=True), range(4)))
    assert len(set(values)) == 1
    assert values[0] == store.get_key(create=False)


@pytest.mark.parametrize("failure", ["before_replace", "after_replace", "lying_write"])
def test_atomic_publish_failure_and_readback(store, monkeypatch, failure):
    start(store)
    before = (store.state_dir / storage.STATE_FILE).read_bytes()
    original = storage.atomic_write
    def write(fd, name, data, **kwargs):
        if failure == "after_replace":
            original(fd, name, data, **kwargs)
        if failure != "lying_write":
            raise OSError("SECRET-FAULT-CANARY")
    monkeypatch.setattr(storage, "atomic_write", write)
    if failure == "after_replace":
        start(store)
        assert len(store.read_state()["runs"]) == 2
    else:
        with pytest.raises(RealityError, match="state_io") as caught:
            start(store)
        assert "SECRET-FAULT-CANARY" not in str(caught.value)
        assert (store.state_dir / storage.STATE_FILE).read_bytes() == before


def test_actual_post_replace_fsync_failure_is_read_back(store, monkeypatch):
    start(store)
    original = os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("FSYNC-CANARY")
        original(fd)
    monkeypatch.setattr(os, "fsync", fail_directory)
    start(store)
    assert len(store.read_state()["runs"]) == 2


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "wide_file", "wide_directory", "ancestor_link"])
def test_unsafe_existing_objects_are_not_repaired(store, tmp_path, kind):
    outside = tmp_path.resolve() / "outside"
    outside.write_bytes(b"OUTSIDE-CANARY")
    outside.chmod(0o600)
    store.state_dir.mkdir(parents=True, mode=0o700)
    target = store.state_dir / storage.STATE_FILE
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target, mode=0o600)
    elif kind == "wide_file":
        target.write_text("{}")
        target.chmod(0o644)
    elif kind == "wide_directory":
        store.state_dir.chmod(0o755)
    else:
        moved = store.state_dir.with_name("other")
        store.state_dir.rename(moved)
        store.state_dir.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RealityError, match="state_unsafe"):
        store.read_state()
    assert outside.read_bytes() == b"OUTSIDE-CANARY"
    assert not list(store.state_dir.glob(".tmp-*"))


@pytest.mark.parametrize("data", [b'{"schema":1,"schema":2}', b'NaN', b'Infinity', b'{',
    b'{"SECRET-CANARY":true}', b'[]', b'null', b'"SECRET-CANARY"'])
def test_corrupt_state_is_not_repaired_or_leaked(store, data):
    store.state_dir.mkdir(parents=True, mode=0o700)
    target = store.state_dir / storage.STATE_FILE
    target.write_bytes(data)
    target.chmod(0o600)
    with pytest.raises(RealityError, match="state_corrupt") as caught:
        store.read_state()
    assert "SECRET-CANARY" not in str(caught.value)
    with pytest.raises(RealityError):
        start(store)
    assert target.read_bytes() == data


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "TIME-CANARY"])
def test_invalid_deadlines_do_not_create_state(store, value):
    with pytest.raises(RealityError):
        store.get_key(create=True, deadline=value)
    assert not store.state_dir.exists()


def test_deadline_expired_before_publish_keeps_previous_state(store, monkeypatch):
    start(store)
    before = store.read_state()
    original = storage.encode_json
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.02)
        return result
    monkeypatch.setattr(storage, "encode_json", delayed)
    with pytest.raises(RealityError, match="lock_timeout"):
        start(store, deadline=time.monotonic() + 0.01)
    assert store.read_state() == before


def test_manifest_is_separate_bounded_private_cache(store):
    assert store.read_cache() is None
    store.write_cache("installation", {"installed": False, "entries": []})
    assert store.read_cache() == {"installed": False, "entries": []}
    assert store.read_state() == core.empty_state()
    assert not (store.state_dir / storage.STATE_FILE).exists()
    assert stat.S_IMODE((store.state_dir / "installation.json").stat().st_mode) == 0o600
    with pytest.raises(RealityError):
        store.write_cache("../state", {})


def test_manifest_post_publish_fault_propagates_for_installer_reconciliation(store, monkeypatch):
    store.write_cache("installation", {"installed": False})
    original = os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("MANIFEST-DURABILITY-CANARY")
        original(fd)
    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(RealityError, match="state_io") as caught:
        store.write_cache("installation", {"installed": True})
    assert "CANARY" not in str(caught.value)
    assert store.read_cache() == {"installed": True}
    assert store.read_state() == core.empty_state()


def test_raw_argv_paths_environment_and_session_never_enter_evidence(store):
    key = store.get_key(create=True)
    raw = ["ARGV-CANARY", "/RAW-PATH-CANARY", "ENV-VALUE-CANARY"]
    run = start(store, check_id=keyed_digest(key, "check", raw))
    finish(store, run)
    store.deliver("SESSION-CANARY", SCOPE, "f" * 64, event="SessionStart")
    data = (store.state_dir / storage.STATE_FILE).read_text()
    brief = core.render_brief(build_status(store.read_state(), scope_id=SCOPE))
    assert all(value not in data + brief for value in raw + ["SESSION-CANARY"])
    assert "argv" not in data and "output" not in data and '"env"' not in data


@pytest.mark.parametrize("length", [0, 1, 31, 33])
def test_malformed_key_is_never_replaced(store, length):
    store.state_dir.mkdir(parents=True, mode=0o700)
    target = store.state_dir / storage.KEY_FILE
    target.write_bytes(b"k" * length)
    target.chmod(0o600)
    for create in (False, True):
        with pytest.raises(RealityError):
            store.get_key(create=create)
        assert target.read_bytes() == b"k" * length


@pytest.mark.parametrize("target_name", [storage.KEY_FILE, storage.STATE_FILE, ".lock", "installation.json"])
def test_private_object_writes_reject_links_without_touching_canary(store, tmp_path, target_name):
    outside = tmp_path.resolve() / "PRIVATE-FAULT-CANARY"
    outside.write_bytes(b"DO-NOT-CHANGE")
    outside.chmod(0o600)
    store.state_dir.mkdir(parents=True, mode=0o700)
    (store.state_dir / target_name).symlink_to(outside)
    operations = {storage.KEY_FILE: lambda: store.get_key(create=True),
                  storage.STATE_FILE: lambda: start(store), ".lock": lambda: start(store),
                  "installation.json": lambda: store.write_cache("installation", {})}
    with pytest.raises(RealityError):
        operations[target_name]()
    assert outside.read_bytes() == b"DO-NOT-CHANGE"


def test_changed_during_pending_then_revert_stays_stale(store):
    run = start(store)
    store.observe(snapshot(fingerprint="f" * 64, observed_at="2026-09-07T11:00:00Z"), expected_generation=0)
    store.observe(snapshot(observed_at="2026-09-07T12:00:00Z"), expected_generation=0)
    finish(store, run, after=snapshot(observed_at="2026-09-07T13:00:00Z"))
    assert check(store)["outcome"] == "exit0" and check(store)["validity"] == "stale"


def test_unsafe_snapshot_metadata_creates_no_state(store):
    for changes in ({"argv": ["RAW-CANARY"]}, {"scope_id": "/raw/path"},
                    {"observed_at": "TIME-CANARY"}, {"diagnostics": ["raw_secret_canary"]}):
        with pytest.raises(RealityError):
            start(store, before=snapshot(**changes))
        assert not store.state_dir.exists()


def test_completion_after_scope_change_cannot_borrow_original_scope_match(store):
    run = start(store)
    finish(store, run, after=snapshot(scope_id="f" * 64))
    assert check(store)["validity"] == "stale"
    assert "f" * 64 not in store.read_state()["scopes"]


def test_full_history_retains_latest_per_check_and_advertises_pause(store):
    latest = {}
    for index in range(core.MAX_RUNS):
        ident = f"{index % 16:064x}"
        latest[ident] = start(store, check_id=ident)
    state = store.read_state()
    assert len(state["runs"]) == core.MAX_RUNS
    assert len((store.state_dir / storage.STATE_FILE).read_bytes()) < core.MAX_STATE_BYTES
    status = build_status(state, scope_id=SCOPE)
    assert {item["run_id"] for item in status["checks"]} == {run["id"] for run in latest.values()}
    for maximum in (256, 512, 768):
        text = core.render_brief(status, max_bytes=maximum)
        assert "暫停記錄" in text and len(text.encode("utf-8")) <= maximum
    with pytest.raises(RealityError, match="state_full"):
        start(store)


def test_real_temporary_git_snapshot_round_trip_and_sticky_revert(store, tmp_path):
    from reality_lantern.snapshot import capture
    repo = tmp_path.resolve() / "REPO-PATH-CANARY"
    repo.mkdir()
    home = tmp_path.resolve() / "git-home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["/usr/bin/git", "init", "-q", "--template="], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    path = repo / "FILE-PATH-CANARY"
    path.write_text("CONTENT-CANARY\n")
    subprocess.run(["/usr/bin/git", "add", "--", path.name], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    key = store.get_key(create=True)
    before = capture(repo, key=key)
    core.validate_snapshot(before)
    assert before["complete"]
    run = start(store, before=before)
    finish(store, run, after=capture(repo, key=key))
    assert check(store, scope=before["scope_id"])["validity"] == "match"
    path.write_text("DIFFERENT-CONTENT-CANARY\n")
    assert store.observe(capture(repo, key=key), expected_generation=0)["ok"]
    assert check(store, scope=before["scope_id"])["validity"] == "stale"
    path.write_text("CONTENT-CANARY\n")
    assert store.observe(capture(repo, key=key), expected_generation=0)["ok"]
    assert check(store, scope=before["scope_id"])["validity"] == "stale"
    data = (store.state_dir / storage.STATE_FILE).read_text()
    assert all(canary not in data for canary in (str(repo), path.name, "CONTENT-CANARY"))


def test_late_real_snapshot_records_sticky_change_without_staling_later_run(store, tmp_path):
    from reality_lantern.snapshot import capture
    repo = tmp_path.resolve() / "late-observation-repo"
    repo.mkdir()
    home = tmp_path.resolve() / "late-git-home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["/usr/bin/git", "init", "-q", "--template="], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    path = repo / "version.txt"
    path.write_text("A\n")
    key = store.get_key(create=True)
    before = capture(repo, key=key)
    assert before["complete"]
    old = start(store, before=before)
    finish(store, old, after=capture(repo, key=key))
    assert check(store, scope=before["scope_id"])["validity"] == "match"
    path.write_text("B\n")
    late = capture(repo, key=key)
    path.write_text("A\n")
    newer = capture(repo, key=key)
    assert late["complete"] and newer["complete"]
    assert core.timestamp(late["observed_at"]) < core.timestamp(newer["observed_at"])
    store.observe(newer, expected_generation=0)
    new_run = start(store, check_id="f" * 64, before=newer)
    finish(store, new_run, after=capture(repo, key=key))
    state_before = store.read_state()
    assert all(item["validity"] == "match" for item in build_status(state_before, scope_id=before["scope_id"])["checks"])
    result = store.observe(late, expected_generation=0)
    assert not result["ok"] and result["diagnostics"] == ["observation_older"]
    state = store.read_state()
    assert state["scopes"] == state_before["scopes"]
    assert state["runs"] == state_before["runs"] and state["completions"] == state_before["completions"]
    assert state["projections"][old["id"]]["validity"] == "stale"
    assert state["projections"][new_run["id"]]["validity"] == "match"
    store.observe(capture(repo, key=key), expected_generation=0)
    assert store.read_state()["projections"][old["id"]]["validity"] == "stale"
