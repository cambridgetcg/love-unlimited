"""Private、本地、有界收據帳；pending/completion 只新增，projection 先可以改。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import os
import time
import uuid

from lantern_common.json import decode_json, encode_json
from lantern_common.private import (atomic_write, check_deadline, private_directory,
                                    private_locked, private_read)
from .core import (KINDS, MAX_DELIVERIES, MAX_INTEGER, MAX_RUNS, MAX_SCOPES,
                   MAX_STATE_BYTES, RealityError, digest_id, empty_state, generation_floor, integer,
                   intrinsic_validity, keyed_digest, require, run_id as validate_run_id, same_snapshot,
                   timestamp, utc_now, validate_outcome, validate_snapshot, validate_state)

STATE_FILE = "state.json"
KEY_FILE = "key"
MAX_CACHE_BYTES = 256 * 1024
# 非 hook caller 最多等 500ms；hook 明示傳入 25ms deadline，唔擴大佢嘅時限。
LOCK_WAIT_SECONDS = 0.500
EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "SubagentStart", "PreToolUse",
                    "PostToolUse", "PostToolUseFailure"})


def _observation_applies(run, observed):
    # Snapshot 反證同時受兩個時間邊界約束；current CAS 亦唔可污染較新 run。
    return (timestamp(run["started_at"]) <= observed
            and timestamp(run["before"]["observed_at"]) <= observed)


class Store:
    def __init__(self, *, home: Path | None = None, state_dir: Path | None = None):
        self.home = Path.home() if home is None else Path(home)
        self.state_dir = (self.home / ".local" / "state" / "reality-lantern"
                          if state_dir is None else Path(state_dir))
        # 唔 resolve／mkdir；private IO 使用時先逐層 nofollow 核對。

    @contextmanager
    def _locked(self, *, create=True, deadline=None):
        check_deadline(deadline, error=RealityError)
        lock_deadline = time.monotonic() + LOCK_WAIT_SECONDS
        if deadline is not None:
            lock_deadline = min(lock_deadline, deadline)
        with private_locked(self.state_dir, create=create, deadline=lock_deadline,
                            error=RealityError) as fd:
            check_deadline(deadline, error=RealityError)
            yield fd

    @staticmethod
    def _read_state(fd):
        data = private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError)
        return (empty_state() if data is None else decode_json(data, maximum=MAX_STATE_BYTES,
                error=RealityError, validator=validate_state))

    @staticmethod
    def _publish(fd, name, data, maximum):
        try:
            atomic_write(fd, name, data, maximum=maximum, error=RealityError)
        except (RealityError, OSError):
            # replace 後 fsync 可能失敗：只憑 private readback 確認已 publish，唔 rollback 或重試。
            if private_read(fd, name, maximum, error=RealityError) != data:
                raise RealityError("state_io") from None
        if private_read(fd, name, maximum, error=RealityError) != data:
            raise RealityError("state_io")

    def _write_state(self, fd, state, deadline):
        data = encode_json(state, maximum=MAX_STATE_BYTES, error=RealityError,
                           too_large="state_full", validator=validate_state)
        check_deadline(deadline, error=RealityError)
        self._publish(fd, STATE_FILE, data, MAX_STATE_BYTES)

    @staticmethod
    def _read_key(fd):
        key = private_read(fd, KEY_FILE, 32, error=RealityError)
        if key is not None and len(key) != 32:
            raise RealityError("key_invalid")
        return key

    def get_key(self, *, create: bool = False, deadline=None) -> bytes | None:
        require(type(create) is bool)
        check_deadline(deadline, error=RealityError)
        if not create:
            with private_directory(self.state_dir, create=False, error=RealityError) as fd:
                return None if fd is None else self._read_key(fd)
        with self._locked(deadline=deadline) as fd:
            key = self._read_key(fd)
            if key is None:
                # 缺 key 但已有 evidence 時唔換身份；明確停低，而唔生成不可比新 digests。
                if private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError) is not None:
                    raise RealityError("key_missing")
                from .archive import directory, inventory
                with directory(fd) as archive_fd:
                    if inventory(archive_fd)[0]:
                        raise RealityError("key_missing")
                key = os.urandom(32)
                check_deadline(deadline, error=RealityError)
                self._publish(fd, KEY_FILE, key, 32)
            return key

    def read_state(self) -> dict:
        with private_directory(self.state_dir, create=False, error=RealityError) as fd:
            return empty_state() if fd is None else self._read_state(fd)

    @staticmethod
    def _project(state, ident, snapshot):
        run = state["runs"][ident]
        scope = state["scopes"][run["scope_id"]]
        previous = state["projections"][ident]
        completion = state["completions"].get(ident)
        validity = intrinsic_validity(run, completion)
        if previous["validity"] == "stale" or scope["changed_generation"] > run["generation"]:
            validity = "stale"
        elif (snapshot is not None and snapshot["complete"] and run["before"]["complete"]
              and not same_snapshot(run["before"], snapshot)
              and _observation_applies(run, timestamp(snapshot["observed_at"]))):
            validity = "stale"
        elif validity == "match" and (snapshot is None or not same_snapshot(run["before"], snapshot)):
            validity = "unknown"
        state["projections"][ident] = {
            "validity": validity, "generation": scope["generation"],
            "compared_at": snapshot["observed_at"] if snapshot is not None else None,
        }

    @staticmethod
    def _record_changes(state, scope_id, snapshot):
        # CAS／時間排序只限制升級證據；已收到嘅完整反證仍可以降級舊收據。
        if not snapshot["complete"] or snapshot["scope_id"] != scope_id:
            return False
        scope = state["scopes"][scope_id]
        observed = timestamp(snapshot["observed_at"])
        changed = False
        for ident, run in state["runs"].items():
            if (run["scope_id"] == scope_id and run["before"]["complete"]
                    and _observation_applies(run, observed)
                    and not same_snapshot(run["before"], snapshot)
                    and state["projections"][ident]["validity"] != "stale"):
                state["projections"][ident] = {
                    "validity": "stale", "generation": scope["generation"],
                    "compared_at": snapshot["observed_at"],
                }
                changed = True
        return changed

    @classmethod
    def _observe(cls, state, scope_id, snapshot):
        scope = state["scopes"][scope_id]
        previous = scope["observation"]
        # 較早 capture 遲咗攞鎖，唔可以倒轉較新 observation；但負面證據唔可以丟棄。
        if previous is not None and timestamp(snapshot["observed_at"]) < timestamp(previous["observed_at"]):
            cls._record_changes(state, scope_id, snapshot)
            return False
        scope["observation"] = deepcopy(snapshot)
        for ident, run in state["runs"].items():
            if run["scope_id"] == scope_id:
                cls._project(state, ident, snapshot)
        return True

    def start_run(self, *, kind: str, check_id: str, before: dict, deadline=None) -> dict:
        require(type(kind) is str and kind in KINDS)
        digest_id(check_id)
        validate_snapshot(before)
        before = deepcopy(before)
        with self._locked(deadline=deadline) as fd:
            state = self._read_state(fd)
            state["next_sequence"] = generation_floor(state)
            if len(state["runs"]) >= MAX_RUNS or state["next_sequence"] >= MAX_INTEGER:
                raise RealityError("state_full")
            scope_id = before["scope_id"]
            if scope_id not in state["scopes"]:
                if len(state["scopes"]) >= MAX_SCOPES:
                    raise RealityError("state_full")
                # 只保留全新帳第一個 scope 嘅 generation 0；rotate 後唔重用舊 token。
                generation = 0 if state["next_sequence"] == 1 else state["next_sequence"]
                state["scopes"][scope_id] = {"generation": generation,
                                            "changed_generation": generation, "observation": None}
            scope = state["scopes"][scope_id]
            self._observe(state, scope_id, before)
            run = {"id": str(uuid.uuid4()), "sequence": state["next_sequence"], "scope_id": scope_id,
                   "kind": kind, "check_id": check_id, "generation": scope["generation"],
                   "started_at": utc_now(), "before": before}
            state["next_sequence"] += 1
            state["runs"][run["id"]] = run
            state["projections"][run["id"]] = {"validity": "unknown", "generation": scope["generation"],
                                               "compared_at": None}
            self._write_state(fd, state, deadline)
            return deepcopy(run)

    def finish_run(self, run_id: str, *, outcome: str, exit_code: int | None,
                   signal_number: int | None, after: dict, deadline=None) -> dict:
        # 參數同 helper 同名，所以 UUID 經簡單資料驗證使用別名。
        validate_run_id(run_id)
        validate_outcome(outcome, exit_code, signal_number)
        validate_snapshot(after)
        after = deepcopy(after)
        with self._locked(create=False, deadline=deadline) as fd:
            if fd is None:
                raise RealityError("run_missing")
            state = self._read_state(fd)
            if run_id not in state["runs"]:
                raise RealityError("run_missing")
            if run_id in state["completions"]:
                raise RealityError("completion_exists")
            run = state["runs"][run_id]
            scope = state["scopes"][run["scope_id"]]
            receipt = {"run_id": run_id, "outcome": outcome, "exit_code": exit_code,
                       "signal_number": signal_number, "finished_at": utc_now(), "after": after}
            state["completions"][run_id] = receipt
            if scope["generation"] == run["generation"] and after["scope_id"] == run["scope_id"]:
                self._observe(state, run["scope_id"], after)
            elif after["scope_id"] == run["scope_id"]:
                self._record_changes(state, run["scope_id"], after)
            # Generation 變咗：唔接受舊 after 升級比較，但完整反證仍會令舊收據失效。
            self._project(state, run_id, scope["observation"])
            self._write_state(fd, state, deadline)
            return deepcopy(receipt)

    def invalidate(self, scope_id: str, *, changed: bool, deadline=None) -> bool:
        digest_id(scope_id)
        require(type(changed) is bool)
        with self._locked(create=False, deadline=deadline) as fd:
            if fd is None:
                return False
            state = self._read_state(fd)
            scope = state["scopes"].get(scope_id)
            if scope is None:
                return False
            if scope["generation"] >= MAX_INTEGER - 1 or generation_floor(state) > MAX_INTEGER:
                raise RealityError("state_full")
            scope["generation"] += 1
            state["next_sequence"] = generation_floor(state)
            if changed:
                scope["changed_generation"] = scope["generation"]
            scope["observation"] = None
            for ident, run in state["runs"].items():
                if run["scope_id"] == scope_id:
                    self._project(state, ident, None)
            self._write_state(fd, state, deadline)
            return True

    def observe(self, snapshot: dict, *, expected_generation: int, deadline=None) -> dict:
        validate_snapshot(snapshot)
        integer(expected_generation)
        snapshot = deepcopy(snapshot)
        with self._locked(create=False, deadline=deadline) as fd:
            if fd is None:
                return {"ok": False, "diagnostics": ["scope_missing"]}
            state = self._read_state(fd)
            scope_id = snapshot["scope_id"]
            scope = state["scopes"].get(scope_id)
            if scope is None:
                return {"ok": False, "diagnostics": ["scope_missing"]}
            if scope["generation"] != expected_generation:
                if self._record_changes(state, scope_id, snapshot):
                    self._write_state(fd, state, deadline)
                return {"ok": False, "diagnostics": ["generation_changed"]}
            if not self._observe(state, scope_id, snapshot):
                # Scope 最新時點冇倒退；遲到差異造成嘅 sticky stale 仍要原子保存。
                self._write_state(fd, state, deadline)
                return {"ok": False, "diagnostics": ["observation_older"]}
            self._write_state(fd, state, deadline)
            return {"ok": bool(snapshot["complete"]), "diagnostics": list(snapshot["diagnostics"])}

    def deliver(self, session_id: str, scope_id: str, fingerprint: str, *, event: str,
                interval_seconds: int = 60, deadline=None) -> bool:
        require(type(session_id) is str and 0 < len(session_id) <= 4096)
        require(type(event) is str and event in EVENTS)
        digest_id(scope_id)
        digest_id(fingerprint)
        integer(interval_seconds, maximum=86400)
        # SubagentStart 唔可以藉節流 API 偷寫 state。
        if event == "SubagentStart":
            return False
        with self._locked(create=False, deadline=deadline) as fd:
            if fd is None:
                return False
            key = self._read_key(fd)
            if key is None:
                return False
            state = self._read_state(fd)
            if scope_id not in state["scopes"]:
                return False
            ident = keyed_digest(key, "delivery", [session_id, scope_id])
            now = utc_now()
            previous = state["deliveries"].get(ident)
            if previous is not None:
                elapsed = (timestamp(now) - timestamp(previous["delivered_at"])).total_seconds()
                if previous["fingerprint"] == fingerprint or elapsed < interval_seconds:
                    return False
            elif len(state["deliveries"]) >= MAX_DELIVERIES:
                # 呢啲係 delivery cache，唔係收據；只移除最舊節流項。
                oldest = min(state["deliveries"], key=lambda item: timestamp(state["deliveries"][item]["delivered_at"]))
                del state["deliveries"][oldest]
            state["deliveries"][ident] = {"scope_id": scope_id, "fingerprint": fingerprint, "delivered_at": now}
            self._write_state(fd, state, deadline)
            return True

    def capacity(self) -> dict:
        """純讀 state 同 archive metadata；唔建鎖，唔承諾可寫或 archive 已驗。"""
        from . import archive as archives
        result = {"ok": False, "operation": "capacity", "cached": True,
                  "writable": "unknown", "archive_integrity": "unverified",
                  "counts": None, "next_sequence": None, "generation_floor": None,
                  "limits": {"runs": MAX_RUNS, "scopes": MAX_SCOPES,
                             "state_bytes": MAX_STATE_BYTES, "archives": archives.MAX_ARCHIVES,
                             "archive_bytes": archives.MAX_ARCHIVE_BYTES}, "diagnostics": []}
        try:
            with private_directory(self.state_dir, create=False, error=RealityError) as fd:
                data = None if fd is None else private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError)
                state = (empty_state() if data is None else decode_json(data, maximum=MAX_STATE_BYTES,
                         error=RealityError, validator=validate_state))
                entries, total = {}, 0
                if fd is not None:
                    with archives.directory(fd) as archive_fd:
                        entries, total = archives.inventory(archive_fd)
                    if self._read_key(fd) is None and (data is not None or entries):
                        result["diagnostics"].append("key_missing")
                result["counts"] = {"runs": len(state["runs"]), "scopes": len(state["scopes"]),
                                    "pending": len(state["runs"]) - len(state["completions"]),
                                    "state_bytes": len(data) if data is not None else 0,
                                    "archives": len(entries), "archive_bytes": total}
                result["next_sequence"] = state["next_sequence"]
                result["generation_floor"] = generation_floor(state)
                if (len(state["runs"]) >= MAX_RUNS or len(state["scopes"]) >= MAX_SCOPES
                        or generation_floor(state) >= MAX_INTEGER
                        or result["counts"]["state_bytes"] >= MAX_STATE_BYTES):
                    result["diagnostics"].append("recording_full")
                if len(entries) >= archives.MAX_ARCHIVES or total >= archives.MAX_ARCHIVE_BYTES:
                    result["diagnostics"].append("archive_full")
                result["ok"] = "key_missing" not in result["diagnostics"]
        except (RealityError, OSError) as exc:
            result["diagnostics"] = [exc.code if isinstance(exc, RealityError) else "state_io"]
        return result

    def rotate(self, *, archive=True, deadline=None) -> dict:
        """同一 Store lock 封存先清空；任何不確定都唔盲目 restore／刪 archive。"""
        from . import archive as archives
        require(archive is True)
        result = {"ok": False, "operation": "rotate", "archive_id": None,
                  "archive_durable": False, "reset": "not_reset", "diagnostics": []}
        try:
            with self._locked(create=False, deadline=deadline) as fd:
                state = empty_state() if fd is None else self._read_state(fd)
                if not any(state[name] for name in ("scopes", "runs", "completions", "projections", "deliveries")):
                    result.update(ok=True, reset="not_needed", diagnostics=["no_receipts"])
                    return result
                if state["runs"].keys() != state["completions"].keys():
                    raise RealityError("archive_pending")
                floor = max(2, generation_floor(state))
                if floor > MAX_INTEGER:
                    raise RealityError("state_full")
                key = self._read_key(fd)
                if key is None:
                    raise RealityError("key_missing")
                original = private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError)
                if original is None or decode_json(original, maximum=MAX_STATE_BYTES,
                        error=RealityError, validator=validate_state) != state:
                    raise RealityError("state_changed")
                # 預先 encode reset，避免封存後先發現 schema／integer 無法表示。
                reset = empty_state()
                reset["next_sequence"] = floor
                data = encode_json(reset, maximum=MAX_STATE_BYTES, error=RealityError, validator=validate_state)
                result["archive_id"] = archives.durable(fd, state, key, deadline=deadline)
                result["archive_durable"] = True
                check_deadline(deadline, error=RealityError)
                if (private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError) != original
                        or self._read_key(fd) != key):
                    raise RealityError("state_changed")
                try:
                    # 唔用 _publish：reset 嘅 directory fsync 錯誤必須如實返回。
                    atomic_write(fd, STATE_FILE, data, maximum=MAX_STATE_BYTES, error=RealityError)
                    if private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError) != data:
                        raise RealityError("state_io")
                except (RealityError, OSError):
                    try:
                        current = private_read(fd, STATE_FILE, MAX_STATE_BYTES, error=RealityError)
                    except (RealityError, OSError):
                        current = None
                    result["reset"] = "not_reset" if current == original else "uncertain"
                    result["diagnostics"] = ["state_io" if current == original else "reset_uncertain"]
                    return result
                result.update(ok=True, reset="complete")
        except (RealityError, OSError) as exc:
            result["diagnostics"] = [exc.code if isinstance(exc, RealityError) else "state_io"]
        return result

    def read_cache(self, name="installation", default=None):
        require(name == "installation")
        with private_directory(self.state_dir, create=False, error=RealityError) as fd:
            data = None if fd is None else private_read(fd, name + ".json", MAX_CACHE_BYTES, error=RealityError)
            return deepcopy(default) if data is None else decode_json(data, maximum=MAX_CACHE_BYTES, error=RealityError)

    def write_cache(self, name, value, *, deadline=None):
        require(name == "installation")
        data = encode_json(value, maximum=MAX_CACHE_BYTES, error=RealityError)
        with self._locked(deadline=deadline) as fd:
            check_deadline(deadline, error=RealityError)
            # Manifest 由 installer transaction 負責 reconcile；唔吞已 publish 後嘅 durability 警告。
            atomic_write(fd, name + ".json", data, maximum=MAX_CACHE_BYTES, error=RealityError)
            if private_read(fd, name + ".json", MAX_CACHE_BYTES, error=RealityError) != data:
                raise RealityError("state_io")
