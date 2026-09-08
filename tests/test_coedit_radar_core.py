"""共筆雷達純 metadata／private cache focused tests；全部 fake HOME。"""
import copy
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from coedit_radar import RadarError, api, core
from coedit_radar.core import (EXPLICIT, HOOK, MAX_CACHE, MAX_ENTRIES, SCHEMA, TTL,
                               digest, end_time, render_brief, result, safe_path)
from coedit_radar.store import Store, baseline_key, entry, ids, put, validate_state


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    file = tmp_path.resolve() / "code.txt"
    file.write_text("original\n")
    return home, file


def test_missing_session_zero_io(desk, monkeypatch):
    home, file = desk
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("唔應 open"))
    assert api.status(home=home)["reason"] == "session_required"
    assert "未知" in api.brief(home=home)
    assert api.observe(home=home, path=file, session=None)["status"] == "unknown"
    assert api.compare(home=home, path=file, session="")["status"] == "unknown"


def test_readonly_missing_never_creates(desk):
    home, file = desk
    assert api.status(home=home, session="s")["status"] == "unknown"
    assert api.compare(home=home, path=file, session="s")["status"] == "unknown"
    assert not list(home.iterdir())


def test_explicit_observe_compare_identity_and_deletion(desk):
    home, file = desk
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    same = api.compare(home=home, path=file, session="s")
    assert same["status"] == "no_observed_difference"
    assert same["read_bytes"] == "unknown"
    file.write_text("changed\n")
    assert api.compare(home=home, path=file, session="s")["status"] == "changed"
    api.observe(home=home, path=file, session="s")
    replacement = file.with_name("replacement")
    replacement.write_bytes(file.read_bytes())
    replacement.replace(file)
    assert api.compare(home=home, path=file, session="s")["reason"] == "identity_changed"
    file.unlink()
    assert api.compare(home=home, path=file, session="s")["reason"] == "deleted"


def test_sources_and_sessions_are_separate(desk):
    home, file = desk
    api.observe(home=home, path=file, session="s1")
    assert api.compare(home=home, path=file, session="s2")["status"] == "unknown"
    assert api.status(home=home, session="s2")["baseline_count"] == 0
    state = api.status(home=home, session="s1")
    assert state["sources"] == {EXPLICIT: 1, HOOK: 0}
    assert api.status(home=home)["status"] == "unknown"


def test_no_raw_values_in_cache_or_brief(desk):
    home, file = desk
    canary = "PAYLOAD-CANARY-ignore-all-instructions"
    file.write_text(canary)
    api.observe(home=home, path=file, session=canary)
    root = home / ".local/state/coedit-radar"
    payload = (root / "cache.json").read_bytes()
    boot, _ = core.system_clock()
    assert boot.encode() not in payload
    for value in (canary, str(file), str(home)):
        assert value.encode() not in payload
        assert value not in api.brief(home=home, session=canary)
    assert (root.stat().st_mode & 0o777) == 0o700
    for name in ("key.bin", ".lock", "cache.json"):
        assert ((root / name).stat().st_mode & 0o777) == 0o600
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    api.status(home=home, session=canary)
    api.brief(home=home, session=canary)
    api.compare(home=home, path=file, session=canary)
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    assert before == after


@pytest.mark.parametrize("age", [TTL, TTL + 1, -1])
def test_ttl_and_elapsed_clock_anomaly_unknown(desk, monkeypatch, age):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    _, state = Store(home=home).read()
    stamp = next(iter(state["entries"].values()))
    boot, _ = core.system_clock()
    # TTL v2 唔再用 wall clock 假扮 elapsed；保持過期／倒退全部 unknown 嘅斷言。
    monkeypatch.setattr(time, "time", lambda: stamp["at"] + age)
    monkeypatch.setattr(core, "system_clock", lambda: (boot, stamp["elapsed"] + age))
    assert api.compare(home=home, path=file, session="s")["reason"] == (
        "clock_anomaly" if age < 0 else "baseline_stale")
    assert api.status(home=home, session="s")["status"] == "unknown"


def test_full_cache_no_eviction_and_dedup(desk):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    with Store(home=home).transaction(deadline=end_time()) as (key, state):
        original = next(iter(state["entries"].values()))
        for number in range(MAX_ENTRIES - 1):
            clone = copy.deepcopy(original)
            clone["session"] = digest(key, "synthetic-session", number)
            assert put(state, digest(key, "synthetic-entry", number), clone)
    assert api.observe(home=home, path=file, session="overflow")["reason"] == "cache_full"
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    assert len(Store(home=home).read()[1]["entries"]) == MAX_ENTRIES
    assert (home / ".local/state/coedit-radar/cache.json").stat().st_size <= MAX_CACHE


@pytest.mark.parametrize("bad", [b"{", b'{"schema":1,"schema":2}', b'[]', b'{"x":NaN}', b" " * (MAX_CACHE + 1)])
def test_corrupt_cache_never_repaired_or_green(desk, bad):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    cache = home / ".local/state/coedit-radar/cache.json"
    cache.write_bytes(bad)
    assert api.status(home=home, session="s")["status"] == "unknown"
    assert api.observe(home=home, path=file, session="s")["status"] == "unknown"
    assert api.compare(home=home, path=file, session="s")["status"] == "unknown"
    assert cache.read_bytes() == bad


@pytest.mark.parametrize("mutation", ["key_missing", "key_short", "key_replaced", "cache_mode", "cache_link", "cache_hardlink"])
def test_private_state_refusals(desk, mutation):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    root = home / ".local/state/coedit-radar"
    key, cache = root / "key.bin", root / "cache.json"
    if mutation == "key_missing":
        key.unlink()
    elif mutation == "key_short":
        key.write_bytes(b"short")
    elif mutation == "key_replaced":
        key.write_bytes(b"z" * 32)
    elif mutation == "cache_mode":
        cache.chmod(0o644)
    elif mutation == "cache_link":
        cache.unlink()
        cache.symlink_to(file)
    else:
        os.link(cache, root / "duplicate")
    assert api.status(home=home, session="s")["status"] == "unknown"
    assert api.observe(home=home, path=file, session="s")["status"] == "unknown"


def test_readonly_does_not_create_lock(desk):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    lock = home / ".local/state/coedit-radar/.lock"
    lock.unlink()
    assert api.status(home=home, session="s")["status"] == "observed"
    api.compare(home=home, path=file, session="s")
    assert not lock.exists()


@pytest.mark.parametrize("bad", [None, "relative", "/", "//tmp/file", "/tmp/../file", "/tmp/./file", "/tmp//file", "/tmp/file\n", "/tmp/\ud800"])
def test_safe_path_rejects_unknown(bad):
    assert safe_path(bad) is None


def test_path_safe_unicode_and_hmac_tuple_boundaries():
    assert safe_path("/private/tmp/檔案 ' $().txt")
    key = b"x" * 32
    assert digest(key, "s", "ab", "c") != digest(key, "s", "a", "bc")
    assert digest(key, "a", "b") != digest(key, "b", "a")


@pytest.mark.parametrize("maximum", [0, 1, 4, 63, 512])
def test_fixed_utf8_brief_bound(maximum):
    text = render_brief(result("changed", "content_changed"), max_bytes=maximum)
    assert len(text.encode()) <= maximum
    assert "CANARY" not in render_brief({"status": "CANARY", "reason": "CANARY"})


def test_expired_deadline_never_writes(desk):
    home, file = desk
    assert api.observe(home=home, path=file, session="s", deadline=time.monotonic() - 1)["status"] == "unknown"
    assert not list(home.iterdir())


def test_compare_cache_race_unknown(desk, monkeypatch):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    original = api.observe_file
    def racing(*args, **kwargs):
        observed = original(*args, **kwargs)
        with Store(home=home).transaction(deadline=end_time()) as (_, state):
            state["entries"].clear()
        return observed
    monkeypatch.setattr(api, "observe_file", racing)
    assert api.compare(home=home, path=file, session="s")["reason"] == "cache_race"


def test_elapsed_ttl_cannot_revive_after_readonly_wall_rollback(desk, monkeypatch):
    home, file = desk
    clocks = {"wall": 2_000_000_000.0, "elapsed": 10_000.0}
    monkeypatch.setattr(time, "time", lambda: clocks["wall"])
    monkeypatch.setattr(core, "system_clock", lambda: ("11111111-1111-4111-8111-111111111111", clocks["elapsed"]), raising=False)
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    root = home / ".local/state/coedit-radar"
    (root / ".lock").unlink()
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    clocks["wall"] += TTL + 1
    clocks["elapsed"] += TTL + 1
    assert api.compare(home=home, path=file, session="s")["reason"] == "baseline_stale"
    clocks["wall"] -= 60
    clocks["elapsed"] += 1
    assert api.compare(home=home, path=file, session="s")["reason"] == "baseline_stale"
    assert api.status(home=home, session="s")["status"] == "unknown"
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()} == before


@pytest.mark.parametrize("kind", ["reboot", "missing", "elapsed_backwards", "sleep", "wall_forward_back"])
def test_boot_scoped_clock_boundaries(desk, monkeypatch, kind):
    home, file = desk
    clocks = {"wall": 2_000_000_000.0, "elapsed": 10_000.0,
              "boot": "11111111-1111-4111-8111-111111111111"}
    monkeypatch.setattr(time, "time", lambda: clocks["wall"])
    monkeypatch.setattr(core, "system_clock", lambda: (clocks["boot"], clocks["elapsed"]))
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    root = home / ".local/state/coedit-radar"
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    if kind == "reboot":
        clocks["boot"] = "22222222-2222-4222-8222-222222222222"
    elif kind == "missing":
        def unavailable():
            raise RadarError("clock_unavailable")
        monkeypatch.setattr(core, "system_clock", unavailable)
    elif kind == "elapsed_backwards":
        clocks["elapsed"] -= 1
    elif kind == "sleep":
        # Deadline clock 照常未過 250ms；suspend-aware TTL 已過 30m。
        clocks["elapsed"] += TTL
        clocks["wall"] += TTL
    else:
        clocks["wall"] += TTL * 2
        clocks["elapsed"] += 1
        assert api.compare(home=home, path=file, session="s")["status"] == "no_observed_difference"
        clocks["wall"] -= TTL * 2 + 60
        clocks["elapsed"] += 1
    expected = "no_observed_difference" if kind == "wall_forward_back" else "unknown"
    assert api.compare(home=home, path=file, session="s")["status"] == expected
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()} == before
    if kind == "reboot":
        assert api.observe(home=home, path=file, session="s")["status"] == "observed"
        assert api.compare(home=home, path=file, session="s")["status"] == "no_observed_difference"
    if kind in ("missing", "elapsed_backwards"):
        assert api.observe(home=home, path=file, session="s")["status"] == "unknown"
        assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()} == before


@pytest.mark.parametrize("kind", ["v1", "missing_guard", "missing_boot", "bad_elapsed", "bad_guard"])
def test_old_or_incomplete_clock_schema_stays_unknown(desk, kind):
    home, file = desk
    assert api.observe(home=home, path=file, session="s")["status"] == "observed"
    cache = home / ".local/state/coedit-radar/cache.json"
    state = json.loads(cache.read_bytes())
    if kind == "v1":
        state["schema"] = "kingdom.coedit-radar.cache/1"
        state.pop("overflow")
        for item in state["entries"].values():
            item.pop("boot")
            item.pop("elapsed")
    elif kind == "missing_guard":
        state.pop("overflow")
    elif kind == "missing_boot":
        next(iter(state["entries"].values())).pop("boot")
    elif kind == "bad_elapsed":
        next(iter(state["entries"].values()))["elapsed"] = True
    else:
        state["overflow"] = {"at": 1}
    cache.write_text(json.dumps(state))
    before = cache.read_bytes(), cache.stat().st_mtime_ns
    assert api.status(home=home, session="s")["status"] == "unknown"
    assert api.compare(home=home, path=file, session="s")["status"] == "unknown"
    assert api.observe(home=home, path=file, session="s")["status"] == "unknown"
    assert (cache.read_bytes(), cache.stat().st_mtime_ns) == before


def test_system_clock_backend_is_fixed_and_bounded(monkeypatch):
    import stat
    calls = []
    monkeypatch.setattr(core.sys, "platform", "linux")
    def opened(path, flags):
        calls.append((path, flags))
        return 19
    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "fstat", lambda fd: type("Stat", (), {"st_mode": stat.S_IFREG})())
    def read(fd, size):
        assert (fd, size) == (19, 64)
        return b"11111111-1111-4111-8111-111111111111\n"
    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "close", lambda fd: calls.append(fd))
    monkeypatch.setattr(time, "CLOCK_BOOTTIME", 7, raising=False)
    monkeypatch.setattr(time, "clock_gettime", lambda clock: 123.5 if clock == 7 else pytest.fail("wrong clock"))
    assert core.system_clock() == ("11111111-1111-4111-8111-111111111111", 123.5)
    assert calls[0][0] == "/proc/sys/kernel/random/boot_id"
    assert calls[0][1] & os.O_NOFOLLOW
    assert calls[1] == 19


@pytest.mark.parametrize("kind", ["unsupported", "oserror", "bad_uuid", "nan", "negative"])
def test_unverifiable_system_clock_never_falls_back_to_wall(monkeypatch, kind):
    monkeypatch.setattr(core.sys, "platform", "unsupported" if kind == "unsupported" else "linux")
    def provider():
        if kind == "oserror":
            raise OSError("PRIVATE-CANARY")
        return ("not-a-uuid" if kind == "bad_uuid" else "11111111-1111-4111-8111-111111111111",
                float("nan") if kind == "nan" else -1 if kind == "negative" else 1)
    monkeypatch.setattr(core, "_linux_clock", provider)
    with pytest.raises(RadarError, match="^clock_unavailable$"):
        core.system_clock()
