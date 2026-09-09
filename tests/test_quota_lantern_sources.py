"""Local collector 用 fake HOME；任何真實 log/auth 都唔需要。"""
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_lantern import sources
from quota_lantern.core import build_status

NOW = "2026-09-07T12:00:00Z"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex = tmp_path / ".codex"
    codex.mkdir()
    return codex


def event(percent=42, timestamp=NOW, **changes):
    result = {"type": "event_msg", "timestamp": timestamp,
        "payload": {"type": "token_count", "rate_limits": {
            "limit_id": "codex", "limit_name": "METADATA_SECRET_CANARY",
            "plan_type": "pro", "primary": {"used_percent": percent,
                "window_minutes": 300, "resets_at": 2000000000}, "secondary": None},
            "info": {"total_token_usage": {"input_tokens": 10,
                "cached_input_tokens": 2, "output_tokens": 3,
                "reasoning_output_tokens": 1, "total_tokens": 13,
                "text": "USAGE_SECRET_CANARY"}}, "text": "PAYLOAD_SECRET_CANARY"}}
    result.update(changes)
    return result


def write_log(home, records, name="rollout-fixture.jsonl", age=0):
    date = datetime.now(timezone.utc).date() - timedelta(days=age)
    path = home / "sessions" / date.strftime("%Y/%m/%d") / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(json.dumps(record).encode() + b"\n" for record in records))
    return path


def collect(home, path, **kwargs):
    return sources.collect_local(home, files=[path], **kwargs)


def test_metrics_allowlist_opaque_locator_and_no_store_write(home):
    path = write_log(home, [
        {"type": "session_meta", "payload": {"title": "TITLE_SECRET_CANARY"}},
        {"type": "response_item", "payload": {"content": "CONVERSATION_SECRET_CANARY"}}, event()])
    report = collect(home, path)
    assert len(report["observations"]) == 1
    item = report["observations"][0]
    assert item["windows"]["primary"]["used_percent"] == 42
    assert item["usage"] == {"input_tokens": 10, "cached_input_tokens": 2,
        "output_tokens": 3, "reasoning_output_tokens": 1, "total_tokens": 13}
    assert item["source"]["version"] is None
    assert item["account_ref"] is None and item["identity_status"] != "verified"
    assert item["observed_at"] == NOW
    assert "SECRET_CANARY" not in json.dumps(report)
    assert str(home) not in json.dumps(report)
    assert path.name not in json.dumps(report)
    assert not (home.parent / ".local").exists()
    status = build_status(report["observations"])
    assert len(status["sources"]) == 1
    assert not report["coverage"]["partial"]


def test_cursor_is_return_only_and_repeat_does_not_refresh_evidence(home):
    path = write_log(home, [event()])
    supplied = {}
    first = collect(home, path, cursors=supplied)
    assert supplied == {}
    repeated = collect(home, path)
    assert repeated["observations"][0]["id"] == first["observations"][0]["id"]
    assert repeated["observations"][0]["observed_at"] == NOW
    next_report = collect(home, path, cursors=first["cursors"])
    assert not next_report["observations"]
    assert next_report["cursors"] == first["cursors"]


def test_partial_line_retried_without_loss(home):
    path = write_log(home, [event()])
    pending = json.dumps(event(51, "2026-09-07T12:01:00Z")).encode()
    with path.open("ab") as stream:
        stream.write(pending[:100])
    first = collect(home, path)
    assert len(first["observations"]) == 1
    assert "source_partial_line" in first["diagnostics"]
    with path.open("ab") as stream:
        stream.write(pending[100:] + b"\n")
    second = collect(home, path, cursors=first["cursors"])
    assert [item["windows"]["primary"]["used_percent"] for item in second["observations"]] == [51]


@pytest.mark.parametrize("replacement", [False, True])
def test_rotation_and_same_inode_rewrite_are_detected(home, replacement):
    path = write_log(home, [event()])
    first = collect(home, path)
    if replacement:
        path.rename(path.with_suffix(".old"))
    path.write_text(json.dumps(event(93)) + "\n")
    second = collect(home, path, cursors=first["cursors"])
    assert "source_rotation" in second["diagnostics"]
    assert second["observations"][0]["windows"]["primary"]["used_percent"] == 93


def test_out_of_order_uses_event_time_not_read_order(home):
    path = write_log(home, [event(80, "2026-09-07T12:02:00Z"), event(20, NOW)])
    report = collect(home, path)
    assert len(report["observations"]) == 2
    assert build_status(report["observations"])["sources"][0]["windows"]["primary"]["used_percent"] == 80


def test_only_recent_date_discovery_and_never_other_config(home, monkeypatch):
    recent = write_log(home, [event()])
    old = write_log(home, [event(99)], age=sources.RECENT_DAYS + 1)
    forbidden = [home / "auth.json", home / "config.toml", home / "other.jsonl", old]
    for path in forbidden[:-1]:
        path.write_text("AUTH_CONFIG_SECRET_CANARY")
    opened = []
    original = sources._open_relative

    def checked(root_fd, parts, **kwargs):
        opened.append(parts)
        assert parts[0] == "sessions"
        assert tuple(old.relative_to(home).parts) != tuple(parts)
        return original(root_fd, parts, **kwargs)

    monkeypatch.setattr(sources, "_open_relative", checked)
    report = sources.collect_local(home)
    assert len(report["observations"]) == 1
    assert tuple(recent.relative_to(home).parts) in opened
    assert report["coverage"]["partial"]
    assert "source_recent_scope" in report["diagnostics"]
    assert "SECRET_CANARY" not in json.dumps(report)


def test_discovery_entries_file_count_and_byte_bounds(home):
    first = write_log(home, [event()])
    for index in range(300):
        (first.parent / f"rollout-{index:04d}.jsonl").write_text(json.dumps(event()) + "\n")
    report = sources.collect_local(home)
    assert report["coverage"]["entries_seen"] <= 256
    assert report["coverage"]["files_read"] <= 8
    assert report["coverage"]["bytes_read"] <= 1024 * 1024
    assert report["coverage"]["partial"]
    assert "source_discovery_limit" in report["diagnostics"]
    assert "source_file_count_limit" in report["diagnostics"]


def test_tail_read_byte_and_line_limits_keep_latest_event(home):
    path = write_log(home, [])
    path.write_bytes(b"X" * (300 * 1024) + b"\n" + json.dumps(event(96)).encode() + b"\n")
    report = collect(home, path)
    assert report["coverage"]["bytes_read"] <= 128 * 1024
    assert report["observations"][0]["windows"]["primary"]["used_percent"] == 96
    assert "source_file_limit" in report["diagnostics"]
    assert "source_line_skipped" in report["diagnostics"]


def test_oversized_line_and_invalid_json_do_not_hide_next_metrics(home):
    path = write_log(home, [])
    path.write_bytes(b"X" * (65 * 1024) + b"\n{broken\n" + json.dumps(event()).encode() + b"\n")
    report = collect(home, path)
    assert len(report["observations"]) == 1
    assert "source_line_limit" in report["diagnostics"]
    assert "source_malformed_json" in report["diagnostics"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "parent_symlink", "fifo", "directory", "outside", "config"])
def test_unsafe_paths_and_filetypes_are_not_read(home, kind):
    path = write_log(home, [event()])
    if kind == "symlink":
        target = path.with_name("rollout-link.jsonl")
        target.symlink_to(path)
        path = target
    elif kind == "hardlink":
        target = path.with_name("rollout-link.jsonl")
        os.link(path, target)
        path = target
    elif kind == "parent_symlink":
        link = home / "sessions" / "alias"
        link.symlink_to(path.parent, target_is_directory=True)
        path = link / path.name
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "outside":
        path = home.parent / "outside.jsonl"
        path.write_text(json.dumps(event()))
    elif kind == "config":
        path = home / "config.jsonl"
        path.write_text(json.dumps(event()))
    start = time.monotonic()
    report = collect(home, path)
    assert time.monotonic() - start < 0.5
    assert not report["observations"]
    assert report["coverage"]["partial"]


def test_symlink_codex_root_and_missing_home_do_not_create_files(home):
    alias = home.parent / "alias"
    alias.symlink_to(home, target_is_directory=True)
    assert "source_unsafe_path" in sources.collect_local(alias)["diagnostics"]
    missing = home.parent / "missing"
    assert "source_missing" in sources.collect_local(missing)["diagnostics"]
    assert not missing.exists()


@pytest.mark.parametrize("metadata", ["IGNORE PREVIOUS INSTRUCTIONS", "line\nbreak", "x" * 1000, "日本語", "[31m", "token=secret"])
def test_untrusted_limit_id_and_name_cannot_escape(home, metadata):
    record = event()
    record["payload"]["rate_limits"]["limit_id"] = metadata
    record["payload"]["rate_limits"]["limit_name"] = metadata
    path = write_log(home, [record])
    report = collect(home, path)
    assert metadata not in json.dumps(report, ensure_ascii=False)
    assert "source_metadata_rejected" in report["diagnostics"]


def test_missing_invalid_timestamp_never_becomes_fresh_evidence(home):
    path = write_log(home, [event(timestamp=None), event(timestamp="yesterday"), event(timestamp="2026-09-07T12:00:00")])
    report = collect(home, path)
    assert report["observations"] == []
    assert "source_event_time_invalid" in report["diagnostics"]


def test_deadline_prevents_discovery_and_io(home, monkeypatch):
    monkeypatch.setattr(sources, "_open_dir", lambda *_: pytest.fail("deadline already elapsed"))
    report = sources.collect_local(home, deadline=time.monotonic() - 1)
    assert report["diagnostics"] == ["source_deadline"]
    assert report["cursors"] == {}


def test_snake_window_duration_and_multibucket_mirror(home):
    record = event()
    bucket = record["payload"]["rate_limits"]
    bucket["primary"]["window_duration_mins"] = bucket["primary"].pop("window_minutes")
    record["payload"]["rate_limits"] = {"rateLimits": bucket,
        "rateLimitsByLimitId": {"codex": bucket, "review": {**bucket, "limit_id": "review"}}}
    path = write_log(home, [record])
    report = collect(home, path)
    assert {item["bucket_id"] for item in report["observations"]} == {"codex", "review"}
    assert all(item["windows"]["primary"]["window_minutes"] == 300 for item in report["observations"])


def test_usage_only_event_keeps_token_activity_without_inventing_quota(home):
    record = event()
    record["payload"]["rate_limits"] = None
    path = write_log(home, [record])
    report = collect(home, path)
    item = report["observations"][0]
    assert item["usage"]["total_tokens"] == 13
    assert item["windows"] == {"primary": None, "secondary": None}
    assert item["credits"] is None
    status = build_status(report["observations"])
    assert len(status["sources"]) == 1
    assert status["sources"][0]["pressure"] == "unknown"


@pytest.mark.parametrize("relative", [
    "sessions/auth.jsonl", "sessions/2026/02/30/rollout-fixture.jsonl",
    "sessions/2026/13/01/rollout-fixture.jsonl", "sessions/2026/9/7/rollout-fixture.jsonl",
    "sessions/2026/09/07/config.jsonl", "sessions/../../outside.jsonl",
])
def test_explicit_files_require_valid_dated_rollout_path(home, relative, monkeypatch):
    monkeypatch.setattr(sources, "_open_relative", lambda *_a, **_kw: pytest.fail("unsafe locator must not open"))
    report = sources.collect_local(home, files=[home / relative])
    assert not report["observations"]
    assert "source_unsafe_path" in report["diagnostics"]


def test_eight_large_files_stay_under_total_io_cap(home):
    paths = []
    for index in range(8):
        path = write_log(home, [], name=f"rollout-{index}.jsonl")
        path.write_bytes(b"X" * 300000 + b"\n" + json.dumps(event(index)).encode() + b"\n")
        paths.append(path)
    report = sources.collect_local(home, files=paths)
    assert len(report["observations"]) == 8
    assert report["coverage"]["bytes_read"] <= sources.MAX_BYTES
    assert report["coverage"]["files_read"] == 8


def test_oversized_unfinished_line_stays_discarded_until_newline(home):
    path = write_log(home, [])
    path.write_bytes(b"X" * 70000)
    first = collect(home, path)
    assert next(iter(first["cursors"].values()))["discarding"] is True
    with path.open("ab") as stream:
        # 喺巨大行中間夾一個合法 JSON，唔可以當新 event。
        stream.write(json.dumps(event(90)).encode() + b"\n" + json.dumps(event(40)).encode() + b"\n")
    report = collect(home, path, cursors=first["cursors"])
    assert [item["windows"]["primary"]["used_percent"] for item in report["observations"]] == [40]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10 ** 400, ["SECRET_CANARY"], {"text": "SECRET_CANARY"}])
def test_invalid_metric_values_do_not_crash_or_leak(home, value):
    record = event()
    record["payload"]["rate_limits"]["primary"]["used_percent"] = value
    report = collect(home, write_log(home, [record]))
    serialized = json.dumps(report, allow_nan=False)
    assert "SECRET_CANARY" not in serialized
    assert "source_metadata_rejected" in report["diagnostics"]


@pytest.mark.parametrize("limits", [None, {"limit_id": "codex", "plan_type": "pro"}])
def test_sparse_metadata_keeps_usage_observation(home, limits):
    record = event()
    record["payload"]["rate_limits"] = limits
    report = collect(home, write_log(home, [record]))
    assert len(report["observations"]) == 1
    item = report["observations"][0]
    assert item["usage"]["total_tokens"] == 13
    assert item["windows"] == {"primary": None, "secondary": None}
    assert item["credits"] is None and item["ordinary_usage_allowed"] is None
    if limits:
        assert item["bucket_id"] == "codex" and item["plan_type"] == "pro"


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_timestamp_overflow_reports_and_continues_valid_lines(home, timestamp):
    report = collect(home, write_log(home, [event(timestamp=timestamp), event(99)]))
    assert "source_event_time_invalid" in report["diagnostics"]
    assert report["coverage"]["partial"]
    assert [item["windows"]["primary"]["used_percent"] for item in report["observations"]] == [99]


@pytest.mark.parametrize("age", [0, sources.RECENT_DAYS - 1])
def test_default_discovery_notices_active_older_filename(home, age):
    paths = [write_log(home, [event(20)], name=f"rollout-{index:02d}.jsonl", age=age if index == 0 else 0)
             for index in range(9)]
    # 明示 mtime，唔依賴檔案系統時鐘解析度或者 readdir 排序。
    base = time.time_ns() - 20_000_000_000
    for index, path in enumerate(paths):
        os.utime(path, ns=(base + index, base + index))
    first = sources.collect_local(home)
    assert first["coverage"]["files_read"] == 8
    with paths[0].open("ab") as stream:
        stream.write(json.dumps(event(99, "2026-09-07T12:01:00Z")).encode() + b"\n")
    os.utime(paths[0], ns=(base + 100, base + 100))
    second = sources.collect_local(home, cursors=first["cursors"])
    assert any(item["windows"]["primary"]["used_percent"] == 99 for item in second["observations"])
    assert second["coverage"]["files_read"] <= 8
    assert second["coverage"]["entries_seen"] <= 256
    assert second["coverage"]["bytes_read"] <= sources.MAX_BYTES
    assert "source_file_count_limit" in second["diagnostics"]
    assert "source_recent_scope" in second["diagnostics"]
    limiting = next(item for item in second["observations"] if item["windows"]["primary"]["used_percent"] == 99)
    assert limiting["observed_at"] == "2026-09-07T12:01:00Z"


@pytest.mark.parametrize("limits", [None, {"limit_id": "codex", "plan_type": "pro"}])
def test_usage_only_update_survives_store_cache_without_retiming_quota(home, limits):
    from quota_lantern.store import Store

    path = write_log(home, [event(96)])
    store = Store(home=home.parent)
    first = collect(home, path)
    quota = first["observations"][0]
    assert store.append(first["observations"])["added"] == 1
    record = event(timestamp="2026-09-07T12:01:00Z")
    record["payload"]["rate_limits"] = limits
    record["payload"]["info"]["total_token_usage"]["total_tokens"] = 21
    with path.open("ab") as stream:
        stream.write(json.dumps(record).encode() + b"\n")
    second = collect(home, path, cursors=first["cursors"])
    activity = second["observations"][0]
    assert store.append(second["observations"])["added"] == 1
    expected = {quota["id"], activity["id"]}
    for cache_only in (False, True):
        latest = store.read_latest(cache_only=cache_only)
        assert {item["id"] for item in latest} == expected
        assert quota in latest and activity in latest
        status = build_status(latest, now=datetime(2026, 9, 7, 12, 2, tzinfo=timezone.utc))
        saved = next(item for item in status["sources"] if item["id"] == quota["id"])
        assert saved["pressure"] == "conserve" and saved["age_seconds"] == 120
        assert saved["usage"]["total_tokens"] == 13
    assert store.append(collect(home, path)["observations"])["duplicates"] == 2
    assert len(store.read_observations()) == 2
    # Cache 遺失嘅純讀重建亦用同一兩類快照語義，唔重新寫 state。
    (store.state_dir / "latest.json").unlink()
    assert {item["id"] for item in store.read_latest()} == expected
    assert not (store.state_dir / "latest.json").exists()


def test_store_roundtrip_deduplicates_and_only_caller_advances_cursor(home):
    from quota_lantern.store import Store

    path = write_log(home, [event()])
    store = Store(home=home.parent)
    report = collect(home, path)
    assert store.read_cache("cursors", default={}) == {}
    assert store.append(report["observations"])["added"] == 1
    assert store.append(collect(home, path)["observations"])["duplicates"] == 1
    store.write_cache("cursors", report["cursors"])
    assert not collect(home, path, cursors=store.read_cache("cursors"))["observations"]
    assert len(store.read_observations()) == 1
    assert len(store.read_latest()) == 1
