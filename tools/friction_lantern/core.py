"""純推導最近可比收據；唔讀 command/output，唔把模式當因果。"""
from __future__ import annotations

import hashlib

from lantern_common.json import encode_json
from reality_lantern.core import (KINDS, MAX_INTEGER, MAX_RUNS, MAX_SCOPES,
                                 RealityError, digest_id, fields, integer, require,
                                 same_snapshot, timestamp, validate_state)

SCHEMA = "kingdom.friction-lantern/1"
DIAGNOSTICS = frozenset({
    "no_receipts", "scope_unknown", "state_corrupt", "state_unavailable", "invalid_input",
    "insufficient_receipts", "recording_full", "interleaved", "pending", "not_nonzero",
    "projection_not_match", "snapshot_not_comparable", "clock_order", "sequence_gap",
})


def empty_status(code="no_receipts", *, scope_id=None, recording="unknown"):
    return {"schema": SCHEMA, "status": "unknown", "cached": True, "advisory": True,
            "scope_id": scope_id, "pattern": None, "as_of": None,
            "recording": recording, "diagnostics": [code if code in DIAGNOSTICS else "state_unavailable"]}


def _comparable(left, right):
    # observed_at 係觀察時點，唔係版本；其餘完整範圍唔可以縮窄嚟湊可比。
    return (same_snapshot(left, right) and left["cwd_id"] == right["cwd_id"]
            and left["coverage"] == right["coverage"])


def derive_pattern(state, scope_id, threshold=3) -> dict:
    """只考慮全域 recorded sequence 最尾一段，唔過濾其他 run 再湊三張。"""
    if type(threshold) is not int or not 3 <= threshold <= MAX_RUNS:
        return empty_status("invalid_input")
    try:
        if state is None:
            return empty_status()
        validate_state(state)
        if scope_id is None:
            return empty_status("scope_unknown")
        digest_id(scope_id)
    except (RealityError, TypeError, ValueError, OverflowError, RecursionError):
        return empty_status("state_corrupt")
    if scope_id not in state["scopes"]:
        return empty_status("scope_unknown")
    full = (len(state["runs"]) >= MAX_RUNS or len(state["scopes"]) >= MAX_SCOPES
            or state["next_sequence"] >= MAX_INTEGER
            or any(scope["generation"] >= MAX_INTEGER for scope in state["scopes"].values()))
    result = empty_status(scope_id=scope_id, recording="full" if full else "capacity_available")

    def suppress(code):
        result["status"] = "no_pattern" if code in ("no_receipts", "insufficient_receipts") else "suppressed"
        result["diagnostics"] = [code]
        return result

    if full:
        return suppress("recording_full")
    runs = sorted(state["runs"].values(), key=lambda run: run["sequence"])
    if not runs:
        return suppress("no_receipts")
    if len(runs) < threshold:
        return suppress("insufficient_receipts")
    candidates = runs[-threshold:]
    first = candidates[0]
    reference = first["before"]
    previous_finish = None
    exit_code = None
    for run in candidates:
        if (run["scope_id"] != scope_id or run["check_id"] != first["check_id"]
                or run["kind"] != first["kind"]):
            return suppress("interleaved")
        completion = state["completions"].get(run["id"])
        if completion is None:
            return suppress("pending")
        if completion["outcome"] != "nonzero":
            return suppress("not_nonzero")
        if exit_code is not None and completion["exit_code"] != exit_code:
            return suppress("not_nonzero")
        exit_code = completion["exit_code"]
        projection = state["projections"][run["id"]]
        if projection["validity"] != "match":
            return suppress("projection_not_match")
        observation = state["scopes"][scope_id]["observation"]
        if not all(_comparable(reference, item) for item in
                   (run["before"], completion["after"], observation)):
            return suppress("snapshot_not_comparable")
        before = timestamp(run["before"]["observed_at"])
        start = timestamp(run["started_at"])
        after = timestamp(completion["after"]["observed_at"])
        finish = timestamp(completion["finished_at"])
        compared = timestamp(projection["compared_at"])
        if (not before <= start <= after <= finish or compared < after
                or (previous_finish is not None and previous_finish > start)):
            return suppress("clock_order")
        previous_finish = finish
    # Highwater 亦由 invalidation 推進；只要求最近 recorded runs 相鄰，唔要求數字連號。
    result.update(status="pattern", diagnostics=[],
                  as_of=state["scopes"][scope_id]["observation"]["observed_at"],
                  pattern={"check_id": first["check_id"], "kind": first["kind"],
                           "exit_code": exit_code, "count": threshold,
                           "first_sequence": first["sequence"],
                           "last_sequence": candidates[-1]["sequence"],
                           "snapshot_id": reference["fingerprint"]})
    return result


def _safe_status(value):
    try:
        fields(value, "schema status cached advisory scope_id pattern as_of recording diagnostics")
        require(value["schema"] == SCHEMA and value["cached"] is True and value["advisory"] is True)
        require(type(value["status"]) is str and value["status"] in {"unknown", "no_pattern", "suppressed", "pattern"})
        require(type(value["recording"]) is str and value["recording"] in {"unknown", "capacity_available", "full"})
        if value["scope_id"] is not None:
            digest_id(value["scope_id"])
        require(type(value["diagnostics"]) is list and len(value["diagnostics"]) <= 1)
        require(all(type(code) is str and code in DIAGNOSTICS for code in value["diagnostics"]))
        if value["status"] != "pattern":
            require(value["pattern"] is None and value["as_of"] is None and bool(value["diagnostics"]))
        else:
            require(value["scope_id"] is not None and value["recording"] == "capacity_available"
                    and not value["diagnostics"])
            timestamp(value["as_of"])
            item = value["pattern"]
            fields(item, "check_id kind exit_code count first_sequence last_sequence snapshot_id")
            digest_id(item["check_id"])
            digest_id(item["snapshot_id"])
            require(type(item["kind"]) is str and item["kind"] in KINDS)
            integer(item["exit_code"], 1, 2**31 - 1)
            integer(item["count"], 3, MAX_RUNS)
            integer(item["first_sequence"], 1)
            integer(item["last_sequence"], item["first_sequence"])
            require(item["last_sequence"] - item["first_sequence"] + 1 >= item["count"])
        return value
    except (RealityError, TypeError, ValueError, OverflowError, RecursionError):
        return empty_status("state_corrupt")


def status_fingerprint(status):
    value = _safe_status(status)
    # 只刷新比較時點唔算 material change；新 recorded run 先改變尾段。
    material = {name: value[name] for name in ("scope_id", "status", "pattern", "recording", "diagnostics")}
    return hashlib.sha256(encode_json(material)).hexdigest()


def render_brief(status, *, max_bytes=512) -> str:
    if type(max_bytes) is not int or not 0 <= max_bytes <= 65536:
        return ""
    value = _safe_status(status)
    if value["pattern"] is not None:
        text = ("阻力燈｜cached advisory；截至 " + value["as_of"] + "，同一 recorded check 最近 "
                + str(value["pattern"]["count"]) + " 張可比收據都報 nonzero。可考慮另一個診斷角度；"
                "中途閱讀、未落帳工作、環境、原因同執行者未知；唔代表而家仍失敗。")
    elif value["recording"] == "full":
        text = "阻力燈｜cached advisory；收據容量已滿，模式抑制；唔代表檢查通過，環境／線上未驗。"
    else:
        text = "阻力燈｜cached advisory；未有足夠可比模式或證據 unknown；唔代表檢查通過，環境／線上未驗。"
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    fallback = "阻力燈｜unknown；完整 advisory 未列，唔代表檢查通過。"
    return fallback if len(fallback.encode("utf-8")) <= max_bytes else ""
