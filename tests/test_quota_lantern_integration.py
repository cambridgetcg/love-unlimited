"""跨模組邊界 regressions；只用自己建立嘅 private fixtures。"""
from pathlib import Path
import copy
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_lantern import store as storage
from quota_lantern.core import normalize_rate_limits


@pytest.mark.parametrize("cache_state", ["valid", "missing", "stamp_changed"])
def test_cache_only_never_opens_journal(tmp_path, monkeypatch, cache_state):
    store = storage.Store(home=tmp_path)
    observations = normalize_rate_limits(
        {"limit_id": "codex", "primary": {"used_percent": 96, "window_minutes": 10080,
                                          "resets_at": 1788803399}},
        source_kind="codex_local", source_id="fixture-source",
        observed_at="2026-09-07T00:00:00Z",
    )
    assert observations
    assert store.append(observations)["added"] == 1
    if cache_state == "missing":
        (store.state_dir / "latest.json").unlink()
    elif cache_state == "stamp_changed":
        with (store.state_dir / "observations.jsonl").open("ab") as journal:
            journal.write(b"\n")
    original = storage._read

    def guarded(fd, name, maximum):
        assert name != "observations.jsonl", "cache-only 唔應該 fallback 讀 journal"
        return original(fd, name, maximum)

    monkeypatch.setattr(storage, "_read", guarded)
    actual = store.read_latest(cache_only=True)
    assert actual == (observations if cache_state == "valid" else [])


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_manifest_post_commit_error_does_not_orphan_host_targets(tmp_path, monkeypatch, action):
    from quota_lantern import install as installer

    repo = Path(__file__).resolve().parents[1]
    if action == "uninstall":
        assert installer.install(home=tmp_path, repo_root=repo, dry_run=False)["ok"]
    original = storage.Store.write_cache
    injected = False

    def committed_then_failed(self, name, value, **kwargs):
        nonlocal injected
        result = original(self, name, value, **kwargs)
        if name == "installation" and not injected:
            injected = True
            raise OSError("POST-REPLACE-FSYNC-CANARY")
        return result

    monkeypatch.setattr(storage.Store, "write_cache", committed_then_failed)
    if action == "install":
        result = installer.install(home=tmp_path, repo_root=repo, dry_run=False)
    else:
        result = installer.uninstall(home=tmp_path, repo_root=repo)
    assert injected
    assert "POST-REPLACE-FSYNC-CANARY" not in str(result)
    manifest = storage.Store(home=tmp_path).read_cache("installation")
    for key, path in installer._targets(tmp_path).items():
        item = manifest["artifacts"].get(key)
        obj = installer._read(tmp_path, path)
        if item is None:
            assert obj is None, "manifest 已移除嘅 owned target 唔可以被 rollback 復活"
        else:
            assert installer._matches(key, obj, item), "已 commit 嘅 manifest 必須仍然對應 host"
    assert installer.uninstall(home=tmp_path, repo_root=repo)["ok"]
    assert not (tmp_path / ".local/bin/kingdom-quota").exists()


def _sample(used, minute):
    return normalize_rate_limits(
        {"limit_id": "codex", "primary": {"used_percent": used, "window_minutes": 10080,
                                          "resets_at": 1788803399}},
        source_kind="codex_local", source_id="research-regression",
        observed_at=f"2026-09-07T12:{minute:02d}:00Z",
    )[0]


def test_conflicting_simultaneous_endpoint_cannot_support_exact_delta(tmp_path):
    from quota_lantern.research import record_hypothesis, reflect

    store = storage.Store(home=tmp_path)
    simultaneous = [_sample(10, 0), _sample(20, 0)]
    endpoint = _sample(30, 1)
    store.append([*simultaneous, endpoint])
    chosen = sorted(simultaneous, key=lambda value: value["id"])[-1]
    expected = 30 - chosen["windows"]["primary"]["used_percent"]
    note = record_hypothesis(store, claim="同時異值必須保留不確定性", metric="delta_used_percent",
                             lower=expected, upper=expected,
                             evidence=[chosen["id"], endpoint["id"]])
    result = reflect(store, hypothesis_id=note["id"])["hypotheses"][0]
    assert result["state"] == "insufficient_evidence"
    assert result["confounded"] is True


@pytest.mark.parametrize("middle_value, expected", [
    (15, "supported"), (5, "insufficient_evidence"), (25, "insufficient_evidence"),
])
def test_uncited_monotonic_midpoint_does_not_change_endpoint_delta(tmp_path, middle_value, expected):
    from quota_lantern.research import record_hypothesis, reflect

    store = storage.Store(home=tmp_path)
    first, middle, last = _sample(10, 0), _sample(middle_value, 1), _sample(20, 2)
    store.append([first, last])
    note = record_hypothesis(store, claim="只比較引用 endpoints", metric="delta_used_percent",
                             lower=10, upper=10, evidence=[first["id"], last["id"]])
    assert reflect(store, hypothesis_id=note["id"])["hypotheses"][0]["state"] == "supported"
    store.append([middle])
    result = reflect(store, hypothesis_id=note["id"])["hypotheses"][0]
    assert result["state"] == expected
    if expected == "supported":
        assert result["range"] == {"min": 10, "max": 10}
    else:
        assert result["confounded"] is True


def test_hypothesis_outcome_changes_next_brief_without_raw_prose(tmp_path):
    from quota_lantern.core import build_status, render_brief
    from quota_lantern.research import record_hypothesis, reflect

    store = storage.Store(home=tmp_path)
    item = _sample(10, 0)
    store.append([item])
    record_hypothesis(store, claim="RAW-HYPOTHESIS-CANARY", metric="used_percent",
                      upper=15, evidence=[item["id"]])
    report = reflect(store)
    first = render_brief(build_status(store.read_latest(), research=report))
    changed = copy.deepcopy(report)
    changed["hypotheses"][0]["state"] = "contradicted"
    second = render_brief(build_status(store.read_latest(), research=changed))
    assert first != second
    assert "RAW-HYPOTHESIS-CANARY" not in first + second


def test_hook_keeps_recording_pause_diagnostic(tmp_path, monkeypatch):
    from quota_lantern import hooks

    store = storage.Store(home=tmp_path)
    monkeypatch.setattr(hooks, "collect_local", lambda *args, **kwargs: {
        "observations": [], "cursors": {}, "diagnostics": ["source_recent_scope"],
    })
    monkeypatch.setattr(store, "append", lambda *args, **kwargs: {
        "added": 0, "duplicates": 0, "recording_paused": True, "diagnostics": ["journal_full"],
    })
    hooks._observe(store, tmp_path, None)
    assert "journal_full" in store.read_cache("diagnostics", [])
    from quota_lantern.core import render_brief
    brief = render_brief(hooks._cached_status(store))
    assert "Work/Codex" in brief
    assert "記錄暫停" in brief


def test_all_stale_brief_prioritizes_latest_observation():
    from datetime import datetime, timezone
    from quota_lantern.core import build_status, render_brief

    observations = []
    for index in range(17):
        observations.extend(normalize_rate_limits(
            {"primary": {"used_percent": 95 if index == 16 else 20, "window_minutes": 10080}},
            source_kind="codex_local", source_id=f"source-{index:02d}",
            observed_at=f"2026-09-07T12:{index:02d}:00Z",
        ))
    brief = render_brief(build_status(observations, now=datetime(2026, 9, 7, 14, tzinfo=timezone.utc)),
                         max_bytes=512)
    first_source = next(line for line in brief.splitlines() if line.startswith("來源 "))
    assert "95%" in first_source
    assert "unknown/stale" in first_source
