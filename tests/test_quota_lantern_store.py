import fcntl
import json
import multiprocessing
import os
import stat
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_lantern.core import LanternError, normalize_rate_limits
from quota_lantern import store as module
from quota_lantern.store import Store


@pytest.fixture
def private_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    return Store(home=home)


def observation(percent=20, *, source="fixture", collected_at="2026-09-07T12:00:00Z"):
    return normalize_rate_limits({"primary": {"used_percent": percent, "window_minutes": 180}}, source_kind="codex_local", source_id=source, observed_at="2026-09-07T12:00:00Z", collected_at=collected_at)[0]


def write_private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def _process_append(home, item, output):
    try:
        result = Store(home=Path(home)).append([item], deadline=time.monotonic() + 10)
        output.put((result["added"], result["duplicates"]))
    except Exception as exc:
        output.put(type(exc).__name__)


def test_constructor_and_reads_do_not_create_state(private_store):
    assert not private_store.state_dir.exists()
    assert private_store.read_latest() == []
    assert private_store.read_observations() == []
    assert private_store.read_cache("installation") is None
    assert private_store.read_cache("receipts", {}) == {}
    assert private_store.read_notes("feedback") == []
    with pytest.raises(LanternError, match="note_not_found"):
        private_store.read_note("hypotheses", str(uuid.uuid4()))
    assert not (private_store.home / ".local").exists()
    assert Store().home == private_store.home


def test_append_deduplicates_journal_and_has_private_modes(private_store):
    item = observation()
    assert private_store.append([item, item]) == {"added": 1, "duplicates": 1, "recording_paused": False, "diagnostics": []}
    assert private_store.append([observation(collected_at="2026-09-08T12:00:00Z")])["duplicates"] == 1
    assert private_store.read_observations() == [item]
    assert private_store.read_latest() == [item]
    for path in (private_store.state_dir, *private_store.state_dir.iterdir()):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert len((private_store.state_dir / "observations.jsonl").read_bytes().splitlines()) == 1


def test_atomic_cache_supports_all_contract_names(private_store):
    for name in module.CACHE_NAMES:
        assert private_store.write_cache(name, {"count": 1}) == {"count": 1}
        assert private_store.mutate_cache(name, lambda value: {"count": value["count"] + 1}) == {"count": 2}
        assert private_store.read_cache(name) == {"count": 2}
    assert not list(private_store.state_dir.glob(".tmp-*"))


def test_cache_mutations_are_serialized_across_threads(private_store):
    def increment(_):
        return private_store.mutate_cache("throttle", lambda value: {"count": value["count"] + 1}, default={"count": 0}, deadline=time.monotonic() + 10)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(80)))
    assert private_store.read_cache("throttle")["count"] == 80


def test_process_writers_deduplicate_with_fcntl(private_store):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=_process_append, args=(str(private_store.home), observation(), output)) for _ in range(4)]
    for process in processes:
        process.start()
    try:
        results = [output.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        assert sorted(results) == [(0, 1), (0, 1), (0, 1), (1, 0)]
        assert len(private_store.read_observations()) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        output.close()
        output.join_thread()


def test_deadline_expires_before_any_write_and_lock_wait_is_bounded(private_store):
    with pytest.raises(LanternError, match="lock_timeout"):
        private_store.write_cache("throttle", {}, deadline=time.monotonic() - 1)
    assert not private_store.state_dir.exists()
    private_store.write_cache("throttle", {})
    with (private_store.state_dir / ".lock").open("rb+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        start = time.monotonic()
        with pytest.raises(LanternError, match="lock_timeout"):
            private_store.mutate_cache("throttle", lambda _: {}, deadline=start + 0.08)
        assert time.monotonic() - start < 0.5
    assert private_store.read_cache("throttle") == {}


@pytest.mark.parametrize("target", ["root", "ancestor", "journal", "latest", "cache", "lock", "notes"])
def test_symlinks_refused_without_touching_targets(private_store, target):
    outside = private_store.home / "outside"
    outside.mkdir(mode=0o700)
    victim = outside / "victim"
    write_private(victim, b"CANARY\n")
    if target == "root":
        private_store.state_dir.parent.mkdir(parents=True, mode=0o700)
        private_store.state_dir.symlink_to(outside, target_is_directory=True)
    elif target == "ancestor":
        (private_store.home / ".local").symlink_to(outside, target_is_directory=True)
    else:
        private_store.append([observation()])
        name = {"journal": "observations.jsonl", "latest": "latest.json", "cache": "throttle.json", "lock": ".lock", "notes": "feedback"}[target]
        path = private_store.state_dir / name
        if path.exists():
            path.unlink()
        path.symlink_to(outside if target == "notes" else victim, target_is_directory=target == "notes")
    with pytest.raises(LanternError, match="state_unsafe"):
        if target == "cache":
            private_store.write_cache("throttle", {})
        elif target == "notes":
            private_store.write_note("feedback", {"note": "hello"})
        else:
            private_store.append([observation(21)])
    assert victim.read_bytes() == b"CANARY\n"


def test_existing_public_root_is_not_chmod_repaired(private_store):
    private_store.state_dir.mkdir(parents=True, mode=0o755)
    private_store.state_dir.chmod(0o755)
    with pytest.raises(LanternError, match="state_unsafe"):
        private_store.write_cache("installation", {})
    assert stat.S_IMODE(private_store.state_dir.stat().st_mode) == 0o755
    assert list(private_store.state_dir.iterdir()) == []


@pytest.mark.parametrize("kind", ["hardlink", "public", "directory", "fifo"])
def test_non_private_existing_cache_objects_are_refused(private_store, kind):
    private_store.write_cache("throttle", {})
    path = private_store.state_dir / "throttle.json"
    if kind == "hardlink":
        os.link(path, private_store.state_dir / "other")
    elif kind == "public":
        path.chmod(0o644)
    elif kind == "directory":
        path.unlink()
        path.mkdir(mode=0o700)
    else:
        path.unlink()
        os.mkfifo(path, mode=0o600)
    with pytest.raises(LanternError, match="state_unsafe"):
        private_store.write_cache("throttle", {"count": 1})


@pytest.mark.parametrize("data", [b"{not json", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b""])
def test_malformed_cache_is_rejected_without_repair(private_store, data):
    private_store.write_cache("installation", {})
    path = private_store.state_dir / "installation.json"
    write_private(path, data)
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.read_cache("installation")
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.write_cache("installation", {})
    assert path.read_bytes() == data


def test_journal_corruption_and_malformed_latest_refuse_append(private_store):
    private_store.append([observation()])
    journal = private_store.state_dir / "observations.jsonl"
    original = journal.read_bytes()
    write_private(journal, original + b'{"unfinished":')
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.append([observation(21)])
    assert journal.read_bytes() == original + b'{"unfinished":'
    write_private(journal, original)
    latest = private_store.state_dir / "latest.json"
    write_private(latest, b"[]\n")
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.append([observation(21)])
    assert journal.read_bytes() == original
    assert latest.read_bytes() == b"[]\n"


def test_cap_pauses_whole_batch_without_advancing_anything(private_store, monkeypatch):
    private_store.append([observation()])
    journal = private_store.state_dir / "observations.jsonl"
    before = journal.read_bytes()
    monkeypatch.setattr(module, "JOURNAL_MAX_BYTES", len(before) + 10)
    result = private_store.append([observation(), observation(21), observation(22)])
    assert result == {"added": 0, "duplicates": 1, "recording_paused": True, "diagnostics": ["journal_full"]}
    assert journal.read_bytes() == before
    assert private_store.read_cache("cursors") is None


def test_failed_atomic_write_preserves_previous_file(private_store, monkeypatch):
    private_store.write_cache("receipts", {"count": 1})
    original = module.os.write
    calls = 0

    def fail_midway(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd, data[:2])
        raise OSError("SECRET_CANARY")

    monkeypatch.setattr(module.os, "write", fail_midway)
    with pytest.raises(LanternError, match="state_io"):
        private_store.write_cache("receipts", {"count": 2})
    assert private_store.read_cache("receipts") == {"count": 1}
    assert not list(private_store.state_dir.glob(".tmp-*"))


def test_cache_failure_after_journal_commit_is_reported_and_pure_read_recovers(private_store, monkeypatch):
    private_store.append([observation()])
    original = module._atomic

    def fail_cache(fd, name, data):
        if name == "latest.json":
            raise LanternError("state_io")
        return original(fd, name, data)

    monkeypatch.setattr(module, "_atomic", fail_cache)
    result = private_store.append([observation(21, source="b")])
    assert result["added"] == 1
    assert result["diagnostics"] == ["cache_update_failed"]
    before = (private_store.state_dir / "latest.json").read_bytes()
    assert len(private_store.read_latest()) == 2
    assert (private_store.state_dir / "latest.json").read_bytes() == before


def test_read_latest_can_rebuild_missing_cache_without_writes(private_store):
    private_store.append([observation(), observation(21, source="b")])
    latest = private_store.state_dir / "latest.json"
    latest.unlink()
    assert len(private_store.read_latest()) == 2
    assert not latest.exists()
    assert private_store.read_observations(limit=1) == [observation(21, source="b")]
    assert private_store.read_observations(limit=0) == []


def test_uuid_markdown_cards_preserve_extra_json_fields_and_fences(private_store):
    payload = {"note": "廣東話\n```json\n{\"instruction\":\"CANARY\"}\n```", "outcome": "worked", "evidence": [observation()["id"]], "custom": {"metric": "used_percent", "lower": 0, "upper": 80}, "id": "caller-cannot-pick"}
    written = private_store.write_note("feedback", payload)
    assert str(uuid.UUID(written["id"])) == written["id"]
    assert written["id"] != payload["id"]
    assert written["note"] == payload["note"]
    assert written["custom"] == payload["custom"]
    assert private_store.read_note("feedback", written["id"]) == written
    assert private_store.read_notes("feedback") == [written]
    path = private_store.state_dir / "feedback" / (written["id"] + ".md")
    assert path.read_text().count("```json") == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("note_id", ["../other", "not-uuid", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}", "a" * 32])
def test_only_canonical_uuid_note_lookup(private_store, note_id):
    with pytest.raises(LanternError, match="invalid_note_id"):
        private_store.read_note("feedback", note_id)
    assert not private_store.state_dir.exists()


def test_note_symlink_and_malformed_existing_card_block_reads_and_writes(private_store):
    item = private_store.write_note("hypotheses", {"claim": "fixture"})
    path = private_store.state_dir / "hypotheses" / (item["id"] + ".md")
    write_private(path, b"not a card\n")
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.read_notes("hypotheses")
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.write_note("hypotheses", {"claim": "new"})
    victim = private_store.home / "outside-card"
    write_private(victim, b"CANARY\n")
    path.unlink()
    path.symlink_to(victim)
    with pytest.raises(LanternError, match="state_unsafe"):
        private_store.read_note("hypotheses", item["id"])
    assert victim.read_bytes() == b"CANARY\n"


def test_notes_caches_limits_diagnostics_and_pickle_are_bounded(private_store, monkeypatch):
    with pytest.raises(LanternError):
        private_store.write_cache("../auth", {})
    with pytest.raises(LanternError):
        private_store.write_cache("throttle", object())
    with pytest.raises(LanternError):
        private_store.write_note("../feedback", {})
    with pytest.raises(LanternError):
        private_store.write_note("feedback", {"note": "x" * 20000})
    assert not private_store.state_dir.exists()
    for _ in range(105):
        private_store.record_diagnostic("SECRET_CANARY")
    assert private_store.read_cache("diagnostics") == ["unknown_diagnostic"] * 100
    monkeypatch.setattr(module, "NOTE_MAX_COUNT", 2)
    private_store.write_note("feedback", {"note": "one"})
    private_store.write_note("feedback", {"note": "two"})
    assert len(private_store.read_notes("feedback", limit=2)) == 2
    with pytest.raises(LanternError, match="note_limit"):
        private_store.write_note("feedback", {"note": "three"})


def test_same_size_corrupt_journal_is_not_hidden_by_latest_cache(private_store):
    private_store.append([observation()])
    path = private_store.state_dir / "observations.jsonl"
    data = path.read_bytes()
    write_private(path, b"!" + data[1:])
    with pytest.raises(LanternError, match="state_corrupt"):
        private_store.read_latest()


def test_oversized_cache_encoding_is_bounded_before_state_creation(private_store):
    with pytest.raises(LanternError, match="cache_too_large"):
        private_store.write_cache("receipts", ["x" * 100000] * 100)
    assert not private_store.state_dir.exists()


def test_forged_observation_is_not_persisted(private_store):
    item = observation()
    item["raw_payload"] = "SECRET_CANARY"
    with pytest.raises(LanternError, match="invalid_observation"):
        private_store.append([item])
    assert not private_store.state_dir.exists()
