"""實況燈嘅白名單 schema 同固定提示；outcome 唔等於版本有效性。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import re
import uuid

from lantern_common.json import encode_json

SCHEMA_VERSION = "kingdom.reality-lantern/1"
MAX_RUNS = 256
MAX_SCOPES = 128
MAX_DELIVERIES = 256
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_DIGEST_BYTES = 16 * 1024 * 1024
MAX_INTEGER = 2**53 - 1
KINDS = frozenset({"tests", "build", "check"})
OUTCOMES = frozenset({"exit0", "nonzero", "signal", "spawn_failed"})
VALIDITIES = frozenset({"match", "stale", "unknown"})
ERROR_CODES = frozenset({
    "invalid_input", "state_corrupt", "state_unsafe", "state_io", "state_too_large",
    "state_full", "cache_too_large", "lock_timeout", "deadline_exceeded", "key_invalid",
    "key_missing", "run_missing", "completion_exists", "scope_missing", "scope_changed",
    "generation_changed", "no_receipts", "observation_older", "recording_full",
    "archive_pending", "archive_full", "archive_conflict", "archive_unverified",
    "reset_uncertain", "state_changed",
})
# Snapshot 只可以用預先批准嘅診斷，唔接受任意「似 code」嘅 raw 字串。
SNAPSHOT_CODES = frozenset({
    "not_git", "git_unavailable", "git_failed", "git_timeout", "git_output_limit",
    "deadline_exceeded", "snapshot_timeout", "snapshot_incomplete", "snapshot_race",
    "snapshot_changed", "snapshot_failed", "path_limit", "byte_limit", "unsupported",
    "unsupported_file", "unsupported_index", "unsupported_submodule", "credential_path",
    "secret_path", "read_failed", "unsafe_path", "scope_unavailable", "invalid_cwd",
})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")


class RealityError(Exception):
    """未知錯誤原文一律丟棄，唔由 exception 帶出路徑或 secrets。"""
    def __init__(self, code: str):
        self.code = code if type(code) is str and code in ERROR_CODES | SNAPSHOT_CODES else "invalid_input"
        super().__init__(self.code)


def require(condition):
    if not condition:
        raise RealityError("invalid_input")


def fields(value, expected):
    require(type(value) is dict and value.keys() == set(expected.split()))


def digest_id(value):
    require(type(value) is str and _HEX.fullmatch(value) is not None)


def run_id(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(uuid.UUID(value)) == value)
    except (ValueError, AttributeError):
        raise RealityError("invalid_input") from None


def integer(value, minimum=0, maximum=MAX_INTEGER):
    require(type(value) is int and minimum <= value <= maximum)


def timestamp(value):
    require(type(value) is str and _TIME.fullmatch(value) is not None)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RealityError("invalid_input") from None
    require(result.utcoffset().total_seconds() == 0)
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def keyed_digest(key: bytes, domain: str, value) -> str:
    require(type(key) is bytes and len(key) == 32)
    require(type(domain) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", domain) is not None)
    # Snapshot 暫存輸入同持久化 cache 分開上限；只保留最後 64hex。
    data = encode_json([SCHEMA_VERSION, domain, value], maximum=MAX_DIGEST_BYTES, error=RealityError)
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def validate_snapshot(value):
    fields(value, "schema scope_id worktree_id cwd_id fingerprint complete observed_at diagnostics coverage")
    require(value["schema"] == SCHEMA_VERSION)
    digest_id(value["scope_id"])
    digest_id(value["cwd_id"])
    for name in ("worktree_id", "fingerprint"):
        if value[name] is not None:
            digest_id(value[name])
    require(type(value["complete"]) is bool)
    timestamp(value["observed_at"])
    codes = value["diagnostics"]
    require(type(codes) is list and len(codes) <= 16)
    require(all(type(code) is str and code in SNAPSHOT_CODES for code in codes))
    require(len(codes) == len(set(codes)))
    coverage = value["coverage"]
    fields(coverage, "paths bytes ignored environment remote")
    integer(coverage["paths"], maximum=10000)
    integer(coverage["bytes"], maximum=64 * 1024 * 1024)
    require(coverage["ignored"] == "excluded" and coverage["environment"] == "unverified"
            and coverage["remote"] == "unverified")
    if value["complete"]:
        require(value["worktree_id"] is not None and value["fingerprint"] is not None and not codes)
    else:
        require(value["fingerprint"] is None and bool(codes))


def empty_state():
    return {"schema": SCHEMA_VERSION, "next_sequence": 1, "scopes": {}, "runs": {},
            "completions": {}, "projections": {}, "deliveries": {}}


def generation_floor(state):
    # 舊 v1 容許 generation 大過 sequence；只喺寫入時抬高，純讀唔偷偷遷移。
    return max(state["next_sequence"],
               max((scope["generation"] + 1 for scope in state["scopes"].values()), default=1))


def same_snapshot(left, right):
    return (left["complete"] and right["complete"] and all(left[name] == right[name]
            for name in ("scope_id", "worktree_id", "fingerprint")))


def intrinsic_validity(run, completion):
    if completion is None:
        return "unknown"
    before, after = run["before"], completion["after"]
    if not before["complete"] or not after["complete"]:
        return "unknown"
    return "match" if same_snapshot(before, after) else "stale"


def validate_state(state):
    fields(state, "schema next_sequence scopes runs completions projections deliveries")
    require(state["schema"] == SCHEMA_VERSION)
    integer(state["next_sequence"], 1)
    for name, maximum in (("scopes", MAX_SCOPES), ("runs", MAX_RUNS),
                          ("completions", MAX_RUNS), ("projections", MAX_RUNS),
                          ("deliveries", MAX_DELIVERIES)):
        require(type(state[name]) is dict and len(state[name]) <= maximum)
    scopes, runs = state["scopes"], state["runs"]
    for ident, scope in scopes.items():
        digest_id(ident)
        fields(scope, "generation changed_generation observation")
        integer(scope["generation"])
        integer(scope["changed_generation"], maximum=scope["generation"])
        if scope["observation"] is not None:
            validate_snapshot(scope["observation"])
            require(scope["observation"]["scope_id"] == ident)
    sequences = set()
    for ident, run in runs.items():
        run_id(ident)
        fields(run, "id sequence scope_id kind check_id generation started_at before")
        require(run["id"] == ident)
        integer(run["sequence"], 1, state["next_sequence"] - 1)
        require(run["sequence"] not in sequences)
        sequences.add(run["sequence"])
        digest_id(run["scope_id"])
        digest_id(run["check_id"])
        require(type(run["kind"]) is str and run["kind"] in KINDS)
        require(run["scope_id"] in scopes)
        integer(run["generation"], maximum=scopes[run["scope_id"]]["generation"])
        timestamp(run["started_at"])
        validate_snapshot(run["before"])
        require(run["before"]["scope_id"] == run["scope_id"])
    for ident, completion in state["completions"].items():
        require(ident in runs)
        fields(completion, "run_id outcome exit_code signal_number finished_at after")
        require(completion["run_id"] == ident)
        validate_outcome(completion["outcome"], completion["exit_code"], completion["signal_number"])
        timestamp(completion["finished_at"])
        validate_snapshot(completion["after"])
    require(state["projections"].keys() == runs.keys())
    for ident, projection in state["projections"].items():
        fields(projection, "validity generation compared_at")
        require(type(projection["validity"]) is str and projection["validity"] in VALIDITIES)
        run = runs[ident]
        scope = scopes[run["scope_id"]]
        integer(projection["generation"], maximum=scope["generation"])
        if projection["compared_at"] is not None:
            timestamp(projection["compared_at"])
        if projection["validity"] == "match":
            completion = state["completions"].get(ident)
            require(projection["compared_at"] is not None and completion is not None)
            require(projection["generation"] == scope["generation"]
                    and run["generation"] >= scope["changed_generation"])
            require(intrinsic_validity(run, completion) == "match")
            require(scope["observation"] is not None
                    and same_snapshot(run["before"], scope["observation"]))
            require(projection["compared_at"] == scope["observation"]["observed_at"])
    for ident, delivery in state["deliveries"].items():
        digest_id(ident)
        fields(delivery, "scope_id fingerprint delivered_at")
        require(delivery["scope_id"] in scopes)
        digest_id(delivery["fingerprint"])
        timestamp(delivery["delivered_at"])


def validate_outcome(outcome, exit_code, signal_number):
    require(type(outcome) is str and outcome in OUTCOMES)
    if outcome in ("exit0", "nonzero"):
        integer(exit_code, 0 if outcome == "exit0" else 1, 0 if outcome == "exit0" else 2**31 - 1)
        require(signal_number is None)
    elif outcome == "signal":
        integer(signal_number, 1, 127)
        require(exit_code is None)
    else:
        require(exit_code is None and signal_number is None)


def _empty_status(scope_id=None, *, corrupt=False):
    return {"schema": SCHEMA_VERSION, "scope_id": scope_id, "checks": [], "cached": True,
            "environment": "unverified", "remote": "unverified", "recording": "unknown",
            "diagnostics": ["state_corrupt" if corrupt else "no_receipts"]}


def build_status(state: dict | None, *, scope_id: str | None = None) -> dict:
    # 冇 locator 就唔猜使用者屬於邊個 worktree。
    if state is None or scope_id is None:
        return _empty_status()
    try:
        digest_id(scope_id)
        validate_state(state)
    except (RealityError, TypeError, ValueError, OverflowError, RecursionError):
        return _empty_status(corrupt=True)
    if scope_id not in state["scopes"]:
        return _empty_status(scope_id)
    status = _empty_status(scope_id)
    # Cache 只證明未到數量上限，唔證明而家仲寫到磁碟。
    status["recording"] = ("full" if len(state["runs"]) >= MAX_RUNS
                           or generation_floor(state) >= MAX_INTEGER else "capacity_available")
    latest = {}
    for run in state["runs"].values():
        if run["scope_id"] == scope_id:
            previous = latest.get(run["check_id"])
            if previous is None or previous["sequence"] < run["sequence"]:
                latest[run["check_id"]] = run
    for run in sorted(latest.values(), key=lambda item: item["sequence"], reverse=True):
        completion = state["completions"].get(run["id"])
        projection = state["projections"][run["id"]]
        status["checks"].append({
            "run_id": run["id"], "check_id": run["check_id"], "kind": run["kind"],
            "sequence": run["sequence"], "outcome": completion["outcome"] if completion else "pending",
            "exit_code": completion["exit_code"] if completion else None,
            "signal_number": completion["signal_number"] if completion else None,
            "validity": projection["validity"], "compared_at": projection["compared_at"],
            "started_at": run["started_at"], "finished_at": completion["finished_at"] if completion else None,
        })
    status["diagnostics"] = ([] if status["checks"] else ["no_receipts"])
    if status["recording"] == "full":
        status["diagnostics"].append("recording_full")
    return status


def _safe_status(status):
    # render 亦係 trust boundary；唔照抄 caller 放入嘅字串。
    try:
        fields(status, "schema scope_id checks cached environment remote recording diagnostics")
        require(status["schema"] == SCHEMA_VERSION and status["cached"] is True)
        require(status["environment"] == status["remote"] == "unverified")
        if status["scope_id"] is not None:
            digest_id(status["scope_id"])
        require(type(status["recording"]) is str and status["recording"] in {"unknown", "capacity_available", "full"})
        require(type(status["diagnostics"]) is list and len(status["diagnostics"]) <= 3)
        require(all(type(code) is str and code in {"state_corrupt", "no_receipts", "recording_full"}
                    for code in status["diagnostics"]))
        require(type(status["checks"]) is list and len(status["checks"]) <= MAX_RUNS)
        for check in status["checks"]:
            fields(check, "run_id check_id kind sequence outcome exit_code signal_number validity compared_at started_at finished_at")
            run_id(check["run_id"])
            digest_id(check["check_id"])
            integer(check["sequence"], 1)
            require(type(check["kind"]) is str and check["kind"] in KINDS)
            require(type(check["validity"]) is str and check["validity"] in VALIDITIES)
            if check["outcome"] == "pending":
                require(check["exit_code"] is None and check["signal_number"] is None
                        and check["finished_at"] is None and check["validity"] != "match")
            else:
                validate_outcome(check["outcome"], check["exit_code"], check["signal_number"])
                timestamp(check["finished_at"])
            timestamp(check["started_at"])
            if check["compared_at"] is not None:
                timestamp(check["compared_at"])
            require(check["validity"] != "match" or check["compared_at"] is not None)
        return status
    except (RealityError, TypeError, ValueError, OverflowError, RecursionError):
        return _empty_status(corrupt=True)


def status_fingerprint(status: dict) -> str:
    value = _safe_status(status)
    # 比較時間刷新唔係 material change；新 run／outcome／validity 先係。
    material = {key: value[key] for key in ("scope_id", "recording", "diagnostics", "environment", "remote")}
    material["checks"] = [{key: item[key] for key in
                          ("run_id", "check_id", "kind", "sequence", "outcome", "exit_code", "signal_number", "validity")}
                          for item in value["checks"]]
    return hashlib.sha256(encode_json(material, maximum=MAX_STATE_BYTES, error=RealityError)).hexdigest()


def render_brief(status: dict, *, max_bytes: int = 768) -> str:
    require(type(max_bytes) is int and 0 <= max_bytes <= 65536)
    value = _safe_status(status)
    prefix = ("實況燈｜收據容量已滿，暫停記錄；cached advisory；"
              if value["recording"] == "full" else "實況燈｜cached advisory；")
    suffix = "環境／線上未驗證。"
    if not value["checks"]:
        text = prefix + "未有可核實檢查；版本 unknown。" + suffix
    else:
        outcomes = {"pending": "仍 pending，未有 completion", "exit0": "command 上次 exit 0",
                    "nonzero": "command 上次 nonzero", "signal": "command 由 signal 結束",
                    "spawn_failed": "command 未能啟動"}
        lines = []
        for item in value["checks"]:
            validity = {"stale": "之後有改動，舊收據 stale，新改動未驗",
                        "unknown": "版本 unknown，需要重新比較"}.get(item["validity"])
            if validity is None:
                validity = "版本 match@" + item["compared_at"] + "，只係該時點"
            lines.append("本地 " + item["kind"] + " " + outcomes[item["outcome"]] + "；" + validity + "。")
        text = prefix
        reserve = "另有檢查未列。" + suffix
        for line in lines:
            if len((text + line + reserve).encode("utf-8")) > max_bytes:
                break
            text += line
        else:
            reserve = suffix
        text += reserve
    if len(text.encode("utf-8")) > max_bytes:
        text = ("實況燈｜容量已滿，暫停記錄；版本 unknown，環境／線上未驗證。"
                if value["recording"] == "full" else "實況燈｜unknown；完整收據未列，環境／線上未驗證。")
    return text if len(text.encode("utf-8")) <= max_bytes else ""
