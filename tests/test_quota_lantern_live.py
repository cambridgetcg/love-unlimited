"""名稱 live 指 adapter，全部測試只 spawn 本 repo fake child。"""
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_lantern import sources
from quota_lantern.core import build_status

FAKE = Path(__file__).parent / "fixtures" / "quota_lantern" / "fake_codex.py"


@pytest.fixture
def run_fake(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    original = sources.subprocess.Popen
    children = []

    def only_fake(command, **kwargs):
        assert command[:2] == [sys.executable, str(FAKE)]
        assert "env" not in kwargs
        child = original(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(sources.subprocess, "Popen", only_fake)

    def run(mode="normal", timeout=2.0):
        marker = tmp_path / f"{mode}-{time.monotonic_ns()}.jsonl"
        start = time.monotonic()
        report = sources.collect_live(codex_command=[sys.executable, str(FAKE), mode, str(marker)],
                                      timeout_seconds=timeout)
        elapsed = time.monotonic() - start
        records = [json.loads(line) for line in marker.read_text().splitlines()] if marker.exists() else []
        assert elapsed < timeout + 0.15
        for child in children:
            assert child.poll() is not None, "adapter left a child running"
            with pytest.raises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        assert "SECRET_CANARY" not in json.dumps(report)
        assert report["coverage"]["bytes_read"] <= sources.MAX_BYTES
        assert report["coverage"]["lines_read"] <= sources.MAX_LIVE_LINES
        return report, records

    yield run
    # 測試失敗都清走自己嘅 fake children。
    for child in children:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)


def requests(records):
    return [row["request"] for row in records if "request" in row]


def test_version_initialized_and_read_protocol_without_params(run_fake):
    report, records = run_fake()
    assert [row["args"] for row in records if "args" in row] == [["--version"], ["app-server"]]
    calls = requests(records)
    assert [call["method"] for call in calls] == ["initialize", "initialized", "account/rateLimits/read"]
    assert calls[0]["params"]["clientInfo"]["name"] == "quota_lantern"
    assert calls[0]["params"]["clientInfo"]["version"]
    assert "params" not in calls[1] and "params" not in calls[2]
    assert report["diagnostics"] == []
    item = report["observations"][0]
    assert item["windows"]["primary"]["used_percent"] == 42
    assert item["windows"]["primary"]["window_minutes"] == 300
    assert item["credits"]["balance"] == "12.5"
    assert item["source"]["version"] == "0.153.3"
    assert item["source"]["experimental"] is True
    assert item["ordinary_usage_allowed"] is None
    assert item["account_ref"] is None and item["identity_status"] != "verified"
    assert len(build_status(report["observations"])["sources"]) == 1


def test_unknown_account_connections_never_merge(run_fake):
    first, _ = run_fake()
    second, _ = run_fake()
    assert first["observations"][0]["source"]["id"] != second["observations"][0]["source"]["id"]
    status = build_status(first["observations"] + second["observations"])
    assert len(status["sources"]) == 2


@pytest.mark.parametrize("mode, expected", [("sparse", "source_sparse"), ("refetch", None)])
def test_sparse_refetch_is_exactly_one(run_fake, mode, expected):
    report, records = run_fake(mode)
    calls = requests(records)
    assert sum(call["method"] == "account/rateLimits/read" for call in calls) == 2
    if expected:
        assert expected in report["diagnostics"]
    else:
        assert report["diagnostics"] == []
        assert report["observations"][0]["windows"]["primary"]["used_percent"] == 42


def test_multibucket_legacy_mirror_is_not_double_counted(run_fake):
    report, _ = run_fake("multibucket")
    assert {item["bucket_id"] for item in report["observations"]} == {"codex", "review"}
    assert len(report["observations"]) == 2


@pytest.mark.parametrize("mode, code", [
    ("old_version", "source_version_unsupported"),
    ("version_timeout", "source_deadline"),
    ("version_lingers", "source_version_failed"),
    ("timeout", "source_deadline"),
    ("early_eof", "source_child_eof"),
    ("malformed", "source_malformed_json"),
    ("rpc_error", "source_rpc_error"),
    ("oversized_stdout", "source_line_limit"),
    ("oversized_stderr", "source_byte_limit"),
    ("too_many_lines", "source_line_count_limit"),
])
def test_failure_bounds_cleanup_and_no_raw_output(run_fake, mode, code):
    report, records = run_fake(mode, timeout=0.8)
    assert report["observations"] == []
    assert code in report["diagnostics"]
    assert report["coverage"]["partial"]
    if mode in {"old_version", "version_timeout", "version_lingers"}:
        assert all(row["args"] == ["--version"] for row in records if "args" in row)
    assert sum(call["method"] == "account/rateLimits/read" for call in requests(records)) == 0


def test_success_still_terminates_sigterm_ignoring_child(run_fake):
    report, _ = run_fake("cleanup_hang", timeout=0.8)
    assert len(report["observations"]) == 1
    assert "source_cleanup_incomplete" not in report["diagnostics"]


@pytest.mark.parametrize("timeout", [0, -1, 0.000001, 0.05, float("nan"), float("inf"), True])
def test_invalid_timeout_never_spawns(monkeypatch, timeout):
    monkeypatch.setattr(sources.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("must not spawn"))
    report = sources.collect_live(timeout_seconds=timeout)
    assert report["diagnostics"] == ["source_deadline"]


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_overflow_reset_drops_only_bad_time_not_valid_live_metrics(run_fake, monkeypatch, timestamp):
    original = sources._Child.response

    def overflow_reset(child, request_id):
        raw = original(child, request_id)
        if request_id >= 2:
            raw["rateLimits"]["primary"]["resetsAt"] = timestamp
            raw["rateLimitsByLimitId"]["codex"]["primary"]["resetsAt"] = timestamp
        return raw

    monkeypatch.setattr(sources._Child, "response", overflow_reset)
    report, records = run_fake()
    assert "source_metadata_rejected" in report["diagnostics"]
    assert report["coverage"]["partial"]
    assert len(report["observations"]) == 1
    window = report["observations"][0]["windows"]["primary"]
    assert window["resets_at"] is None and window["used_percent"] == 42
    assert sum(call["method"] == "account/rateLimits/read" for call in requests(records)) == 1


def test_sparse_metadata_without_activity_never_invents_live_evidence(run_fake, monkeypatch):
    original = sources._Child.response

    def metadata_only(child, request_id):
        raw = original(child, request_id)
        return {"rateLimits": {"limitId": "codex", "planType": "pro"}} if request_id >= 2 else raw

    monkeypatch.setattr(sources._Child, "response", metadata_only)
    report, records = run_fake()
    assert report["observations"] == []
    assert "source_sparse" in report["diagnostics"]
    assert sum(call["method"] == "account/rateLimits/read" for call in requests(records)) == 2


def test_empty_test_command_never_spawns(monkeypatch):
    monkeypatch.setattr(sources.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("must not spawn"))
    assert sources.collect_live(codex_command=[])["diagnostics"] == ["source_spawn_failed"]
