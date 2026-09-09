"""Private HMAC cache；讀取唔 mkdir／建 key／建 lock，損壞唔自動修。"""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import time

from lantern_common.json import decode_json, encode_json
from lantern_common.private import atomic_write, bounded_lock, private_directory, private_read
from . import RadarError
from .core import (EXPLICIT, HOOK, LOCK_SECONDS, MAX_CACHE, MAX_ENTRIES, MAX_FILE,
                   SCHEMA, SOURCES, TTL, check, clock_stamp, digest)

HEX = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = frozenset(("kind", "session", "path", "source", "phase", "at", "boot", "elapsed", "digest", "identity",
                    "version", "size", "lines", "max_line", "plain_lf"))
OBSERVATION_FIELDS = ("digest", "identity", "version", "size", "lines", "max_line", "plain_lf")


def valid_hex(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def valid_stamp(item):
    return (valid_hex(item.get("boot"))
            and all(type(item.get(field)) in (int, float) and math.isfinite(item[field])
                    and 0 <= item[field] <= 10**12 for field in ("at", "elapsed")))


def validate_state(state):
    if (type(state) is not dict or set(state) != {"schema", "key_id", "entries", "overflow"}
            or state["schema"] != SCHEMA or not valid_hex(state["key_id"])
            or type(state["entries"]) is not dict or len(state["entries"]) > MAX_ENTRIES):
        raise RadarError("state_corrupt")
    guard = state["overflow"]
    if guard is not None and (type(guard) is not dict or set(guard) != {"at", "elapsed", "boot"}
                              or not valid_stamp(guard)):
        raise RadarError("state_corrupt")
    for key, item in state["entries"].items():
        if (not valid_hex(key) or type(item) is not dict or set(item) != FIELDS
                or item["kind"] not in ("baseline", "read_token", "mutation")
                or item["source"] not in SOURCES
                or not valid_hex(item["session"]) or not valid_hex(item["path"])
                or not valid_stamp(item)):
            raise RadarError("state_corrupt")
        kind, phase = item["kind"], item["phase"]
        if ((kind == "baseline" and phase != "observed")
                or (kind == "read_token" and phase not in ("pending", "consumed", "poisoned"))
                or (kind == "mutation" and phase not in ("pending", "consumed"))
                or (kind != "baseline" and item["source"] != HOOK)):
            raise RadarError("state_corrupt")
        observed = kind == "baseline" or (kind == "read_token" and phase == "pending")
        if observed:
            if (not all(valid_hex(item[field]) for field in ("digest", "identity", "version"))
                    or any(type(item[field]) is not int or not 0 <= item[field] <= MAX_FILE
                           for field in ("size", "lines", "max_line"))
                    or type(item["plain_lf"]) is not bool):
                raise RadarError("state_corrupt")
        elif any(item[field] is not None for field in OBSERVATION_FIELDS):
            raise RadarError("state_corrupt")


def entry(kind, session, path, source, at, phase, observation=None):
    value = {"kind": kind, "session": session, "path": path, "source": source,
             **at, "phase": phase}
    value.update({field: observation[field] if observation else None for field in OBSERVATION_FIELDS})
    return value


def retire(item, phase="consumed"):
    item["phase"] = phase
    for field in OBSERVATION_FIELDS:
        item[field] = None


def fresh(item, now):
    return item["boot"] == now["boot"] and 0 <= now["elapsed"] - item["elapsed"] < TTL


def state_clock(state, key):
    now = clock_stamp(key)
    # 有可核實 counter 倒退唔當過期去丟 pending／tombstones；保留 cache 並 unknown。
    stamps = list(state["entries"].values())
    if state["overflow"] is not None:
        stamps.append(state["overflow"])
    if any(item["boot"] == now["boot"] and item["elapsed"] > now["elapsed"] for item in stamps):
        raise RadarError("clock_anomaly")
    return now


class Store:
    def __init__(self, *, home):
        self.root = Path(home) / ".local" / "state" / "coedit-radar"

    def _read(self, fd):
        key = private_read(fd, "key.bin", 32, error=RadarError)
        data = private_read(fd, "cache.json", MAX_CACHE, error=RadarError)
        if key is None:
            if data is not None:
                raise RadarError("key_missing")
            return None, None
        if len(key) != 32:
            raise RadarError("key_invalid")
        state = ({"schema": SCHEMA, "key_id": digest(key, "key-id"), "entries": {}, "overflow": None}
                 if data is None else decode_json(data, maximum=MAX_CACHE, error=RadarError,
                                                 validator=validate_state))
        if state["key_id"] != digest(key, "key-id"):
            raise RadarError("key_mismatch")
        return key, state

    def read(self):
        with private_directory(self.root, create=False, error=RadarError) as fd:
            return (None, None) if fd is None else self._read(fd)

    @contextmanager
    def transaction(self, *, deadline, create=True):
        check(deadline)
        with private_directory(self.root, create=create, error=RadarError) as fd:
            if fd is None:
                yield None, None
                return
            with bounded_lock(fd, deadline=min(deadline, time.monotonic() + LOCK_SECONDS),
                              error=RadarError):
                key, state = self._read(fd)
                if key is None:
                    if not create:
                        yield None, None
                        return
                    key = os.urandom(32)
                    atomic_write(fd, "key.bin", key, maximum=32, error=RadarError)
                    state = {"schema": SCHEMA, "key_id": digest(key, "key-id"), "entries": {}, "overflow": None}
                now = state_clock(state, key)
                previous = encode_json(state, maximum=MAX_CACHE, error=RadarError, validator=validate_state)
                state["entries"] = {key: item for key, item in state["entries"].items() if fresh(item, now)}
                if state["overflow"] is not None and not fresh(state["overflow"], now):
                    state["overflow"] = None
                yield key, state
                check(deadline)
                data = encode_json(state, maximum=MAX_CACHE, error=RadarError, validator=validate_state)
                if data != previous:
                    atomic_write(fd, "cache.json", data, maximum=MAX_CACHE, error=RadarError)
                check(deadline)


def baseline_key(key, session_id, path_id, source):
    return digest(key, "baseline", session_id, path_id, source)


def read_key(key, session_id, token):
    return digest(key, "read-token", session_id, token)


def mutation_key(key, session_id, token):
    return digest(key, "mutation-token", session_id, token)


def ids(key, session, path):
    return digest(key, "session", session), digest(key, "path", path)


def put(state, key, item):
    if key not in state["entries"] and len(state["entries"]) >= MAX_ENTRIES:
        # 容量不足都要 commit 已做嘅退役，唔可以 rollback 令舊 baseline 復活。
        if item["kind"] == "mutation" and item["phase"] == "pending":
            # 固定一個 global guard；唔 evict pending／consumed 去換位置。
            # Completion 無法枚舉 overflow tokens，所以永不提早清除。
            state["overflow"] = {field: item[field] for field in ("at", "elapsed", "boot")}
            for pending in state["entries"].values():
                if pending["kind"] == "read_token" and pending["phase"] == "pending":
                    retire(pending, "poisoned")
        return False
    state["entries"][key] = item
    return True


def invalidate_reads(state, path_id):
    for item in state["entries"].values():
        if item["kind"] == "read_token" and item["path"] == path_id and item["phase"] == "pending":
            retire(item, "poisoned")


def mutating(state, path_id, now):
    guard = state["overflow"]
    return (bool(guard and fresh(guard, now))
            or any(item["kind"] == "mutation" and item["path"] == path_id and item["phase"] == "pending"
                   and fresh(item, now) for item in state["entries"].values()))
