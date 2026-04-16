"""JSONL-backed detection storage with bounded-tail queries and rolling window."""

from __future__ import annotations

import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _parse_since(since: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([mhd])", since.strip())
    if not m:
        raise ValueError(f"since must be like '1h', '24h', '30m', got {since!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


class DetectionStore:
    def __init__(self, path: str, rolling_window_min: int = 15,
                 tail_scan_bytes: int = 10 * 1024 * 1024):
        self.path = path
        self.rolling_window = timedelta(minutes=rolling_window_min)
        self.tail_scan_bytes = tail_scan_bytes
        self._lock = threading.Lock()
        self._window: deque = deque()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: "dict[str, Any]") -> None:
        if "timestamp" not in row or not row["timestamp"]:
            row["timestamp"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            try:
                ts = _parse_ts(row["timestamp"])
            except Exception:
                ts = datetime.now(timezone.utc)
            self._window.append((ts, bool(row.get("parse_failed", False))))
            self._evict_locked()

    def _evict_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.rolling_window
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def window_stats(self) -> "dict[str, Any]":
        with self._lock:
            self._evict_locked()
            n = len(self._window)
            fails = sum(1 for _, pf in self._window if pf)
        return {
            "count": n,
            "parse_fail_rate": (fails / n) if n else 0.0,
        }

    def query(self, *, since: str = "1h",
              score_below: "float | None" = None,
              chat_model: "str | None" = None,
              failure_mode: "str | None" = None,
              limit: int = 100) -> "list[dict[str, Any]]":
        rows, _truncated = self.query_raw(
            since=since, score_below=score_below, chat_model=chat_model,
            failure_mode=failure_mode, limit=limit,
        )
        return rows

    def query_raw(self, *, since: str = "1h",
                  score_below: "float | None" = None,
                  chat_model: "str | None" = None,
                  failure_mode: "str | None" = None,
                  limit: int = 100) -> "tuple[list[dict[str, Any]], bool]":
        if not os.path.exists(self.path):
            return [], False

        cutoff = datetime.now(timezone.utc) - _parse_since(since)
        size = os.path.getsize(self.path)
        truncated = size > self.tail_scan_bytes
        read_from = max(0, size - self.tail_scan_bytes)

        rows: "list[dict[str, Any]]" = []
        with open(self.path, "rb") as f:
            f.seek(read_from)
            if read_from > 0:
                f.readline()  # drop partial line
            for raw in f:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                ts_raw = obj.get("timestamp")
                if not ts_raw:
                    continue
                try:
                    ts = _parse_ts(ts_raw)
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                if chat_model is not None and obj.get("chat_model") != chat_model:
                    continue
                if score_below is not None and obj.get("score", 1.0) >= score_below:
                    continue
                if failure_mode is not None and failure_mode not in obj.get("detected_modes", []):
                    continue
                rows.append(obj)
        rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return rows[:limit], truncated
