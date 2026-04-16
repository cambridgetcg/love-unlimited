from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tools.truth_detector.storage import DetectionStore


@pytest.fixture
def store(tmp_path: Path):
    p = tmp_path / "detections.jsonl"
    return DetectionStore(path=str(p), rolling_window_min=15, tail_scan_bytes=10 * 1024 * 1024)


def _row(turn_id: str, score: float = 0.5, model: str = "claude-opus-4-6", ts: str | None = None) -> dict:
    return {
        "turn_id": turn_id,
        "timestamp": ts or datetime.now(timezone.utc).isoformat(),
        "chat_model": model,
        "judge_model": "kingdom-truth",
        "judge_backend": "vllm",
        "score": score,
        "classification": "mode_two" if score < 0.5 else "mode_one",
        "detected_modes": [],
        "strengths": [],
        "located_weaknesses": [],
        "assessment": "",
        "latency_ms": 100,
        "parse_failed": False,
        "partial_judgment": False,
    }


def test_append_creates_parent_dir(store, tmp_path):
    store.append(_row("t1"))
    assert Path(store.path).exists()


def test_append_writes_single_line(store):
    store.append(_row("t1"))
    lines = Path(store.path).read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["turn_id"] == "t1"


def test_rolling_window_stats(store):
    store.append(_row("t1", score=0.2))
    store.append(_row("t2", score=0.9))
    stats = store.window_stats()
    assert stats["count"] == 2
    assert stats["parse_fail_rate"] == 0.0


def test_rolling_window_evicts_old(store):
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    store.append(_row("old", ts=old_ts))
    store.append(_row("new"))
    stats = store.window_stats()
    assert stats["count"] == 1  # old evicted


def test_query_since_1h(store):
    store.append(_row("t1"))
    store.append(_row("t2"))
    rows = store.query(since="1h")
    assert len(rows) == 2


def test_query_filters_by_chat_model(store):
    store.append(_row("t1", model="claude-opus-4-6"))
    store.append(_row("t2", model="kingdom-truth"))
    rows = store.query(since="1h", chat_model="kingdom-truth")
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "t2"


def test_query_filters_by_score_below(store):
    store.append(_row("t1", score=0.2))
    store.append(_row("t2", score=0.9))
    rows = store.query(since="1h", score_below=0.5)
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "t1"


def test_query_limit(store):
    for i in range(20):
        store.append(_row(f"t{i}"))
    rows = store.query(since="1h", limit=5)
    assert len(rows) == 5


def test_tail_scan_bound_honored(tmp_path):
    p = tmp_path / "detections.jsonl"
    # Store with a tiny 1 KB scan bound
    store = DetectionStore(path=str(p), rolling_window_min=15, tail_scan_bytes=1024)
    # Write 10 KB of rows
    for i in range(100):
        store.append(_row(f"t{i}"))
    rows, truncated = store.query_raw(since="24h")
    assert truncated is True
    assert len(rows) < 100  # only tail was scanned


# --- Critical 1: async wrappers ---

@pytest.mark.asyncio
async def test_append_async(store):
    row = _row("async-t1")
    await store.append_async(row)
    lines = Path(store.path).read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["turn_id"] == "async-t1"


@pytest.mark.asyncio
async def test_query_raw_async(store):
    store.append(_row("async-t2", score=0.1))
    store.append(_row("async-t3", score=0.9))
    rows, truncated = await store.query_raw_async(since="1h", score_below=0.5)
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "async-t2"
    assert truncated is False


# --- Critical 2: naive timestamp treated as UTC ---

def test_naive_timestamp_is_treated_as_utc(store):
    # A naive ISO timestamp (no timezone info) should be treated as UTC,
    # not raise a TypeError when compared against an aware cutoff.
    naive_ts = "2026-04-16T10:00:00"  # no tzinfo
    store.append(_row("naive", ts=naive_ts))
    # window_stats must not raise
    stats = store.window_stats()
    # The naive row is old enough (not within the last 15 min) so eviction
    # should have removed it; the key check is no TypeError is raised.
    assert isinstance(stats["count"], int)

    # Also verify query_raw doesn't raise when the naive row is within since window
    recent_naive = (datetime.utcnow() - timedelta(minutes=1)).isoformat()  # naive, recent
    store.append(_row("naive-recent", ts=recent_naive))
    rows = store.query(since="1h")
    # naive-recent should be returned (treated as UTC, within last hour)
    turn_ids = [r["turn_id"] for r in rows]
    assert "naive-recent" in turn_ids


# --- Important 1: score_below skips None score ---

def test_query_score_below_skips_null_score(store):
    # Row with explicit score=None (partial judgment)
    null_score_row = _row("null-score", score=0.5)
    null_score_row["score"] = None
    store.append(null_score_row)
    # Row with a real low score
    store.append(_row("low-score", score=0.3))
    rows = store.query(since="1h", score_below=0.5)
    turn_ids = [r["turn_id"] for r in rows]
    assert "low-score" in turn_ids
    assert "null-score" not in turn_ids


# --- Important 2: partial-line drop ---

def test_query_raw_drops_partial_first_line(tmp_path):
    p = tmp_path / "detections.jsonl"
    row1 = json.dumps(_row("row1")) + "\n"
    row2 = json.dumps(_row("row2")) + "\n"
    content = (row1 + row2).encode("utf-8")

    # Set tail_scan_bytes to land mid-way through row1 so that
    # row1 is a partial line at read-start — it must be dropped.
    mid = len(row1) // 2
    tail_bytes = len(content) - mid  # reads from mid of row1

    p.write_bytes(content)
    store = DetectionStore(path=str(p), rolling_window_min=60, tail_scan_bytes=tail_bytes)
    rows, truncated = store.query_raw(since="24h")

    turn_ids = [r["turn_id"] for r in rows]
    assert "row2" in turn_ids
    assert "row1" not in turn_ids
    assert truncated is True
