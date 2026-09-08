"""Quota Lantern 嘅純函數；外來文字唔會變成 brief 指令。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

SCHEMA_VERSION = "kingdom.quota-lantern/1"
MAX_OBSERVATIONS = 20000
SAFE_CODES = frozenset({
    "invalid_input", "invalid_observation", "invalid_timestamp", "invalid_window",
    "invalid_percent", "invalid_minutes", "invalid_reset", "invalid_credits",
    "invalid_usage", "invalid_boolean", "invalid_metadata", "invalid_coverage",
    "invalid_bucket", "bucket_limit", "unknown_diagnostic", "internal_error",
    "state_unsafe", "state_corrupt", "state_io", "state_too_large", "lock_timeout",
    "invalid_cache", "cache_too_large", "journal_full", "recording_paused",
    "cache_update_failed", "invalid_note", "invalid_note_id", "note_not_found",
    "note_limit", "invalid_limit", "deadline_exceeded", "source_unavailable",
    "coverage_partial", "local_partial", "local_unavailable", "live_unavailable",
    "unsupported_version", "rpc_timeout", "rpc_error", "invalid_json",
    "invalid_feedback", "invalid_hypothesis", "invalid_evidence", "invalid_metric",
    "observation_not_found", "hypothesis_not_found", "installation_conflict",
    "source_byte_limit", "source_child_eof", "source_cleanup_incomplete", "source_deadline",
    "source_discovery_limit", "source_discovery_skipped", "source_event_time_invalid",
    "source_file_count_limit", "source_file_limit", "source_line_count_limit", "source_line_limit",
    "source_line_skipped", "source_malformed_json", "source_metadata_rejected", "source_missing",
    "source_partial_line", "source_protocol_error", "source_read_failed", "source_recent_scope",
    "source_rotation", "source_rpc_error", "source_sparse", "source_spawn_failed", "source_unsafe_file",
    "source_unsafe_path", "source_version_failed", "source_version_unsupported",
    "install_agents_conflict", "install_agents_unclosed_block", "install_concurrent_change",
    "install_file_changed_or_large", "install_foreign_object", "install_hook_conflict",
    "install_incomplete_manifest", "install_invalid_agents", "install_invalid_hooks",
    "install_invalid_settings", "install_manifest_conflict", "install_missing_root",
    "install_missing_source", "install_owned_changed", "install_source_changed",
    "install_source_symlink", "install_symlink", "install_target_conflict", "install_failed",
    "install_rollback_incomplete", "install_not_installed", "uninstall_failed", "doctor_failed",
})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ID = re.compile(r"[0-9a-f]{64}\Z")
_PLANS = frozenset({"free", "go", "plus", "pro", "team", "business", "enterprise", "edu", "unknown"})
_REASONS = frozenset({"none", "primary", "secondary", "credits", "usage_limit", "rate_limit", "spend_limit", "included", "credit_limit", "unknown"})
_USAGE = {
    "input_tokens": ("input_tokens", "inputTokens", "input"),
    "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens", "cached_input"),
    "output_tokens": ("output_tokens", "outputTokens", "output"),
    "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens", "reasoning_output"),
    "total_tokens": ("total_tokens", "totalTokens", "total"),
}


class LanternError(Exception):
    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in SAFE_CODES else "internal_error"
        super().__init__(self.code)


def utc_now() -> str:
    return _iso(datetime.now(timezone.utc))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _time(value):
    if isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
    return None


def _timestamp(value, issues, code="invalid_timestamp", *, epoch=False):
    if value is None:
        return None
    parsed = _time(value)
    if epoch and _number(value) is not None:
        try:
            parsed = datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            parsed = None
    if parsed is None:
        issues.append(code)
        return None
    return _iso(parsed)


def _number(value, *, minimum=0, maximum=1e18):
    if type(value) not in (int, float):
        return None
    if not minimum <= value <= maximum or not math.isfinite(value):
        return None
    return value


def _pick(value, *names):
    for name in names:
        if name in value:
            return value[name]
    return None


def _identifier(value, fallback):
    if not isinstance(value, str) or not value:
        return fallback
    if _IDENTIFIER.fullmatch(value):
        return value
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _boolean(value, issues):
    if value is None or type(value) is bool:
        return value
    issues.append("invalid_boolean")
    return None


def _window(value, issues):
    if value is None:
        return None
    if not isinstance(value, dict):
        issues.append("invalid_window")
        return None
    percent = _pick(value, "used_percent", "usedPercent")
    minutes = _pick(value, "window_minutes", "window_duration_mins", "windowDurationMins", "windowMinutes")
    if percent is not None and _number(percent, maximum=100) is None:
        issues.append("invalid_percent")
        percent = None
    if minutes is not None and (_number(minutes, maximum=5256000) is None or minutes <= 0):
        issues.append("invalid_minutes")
        minutes = None
    return {
        "used_percent": percent,
        "window_minutes": minutes,
        "resets_at": _timestamp(_pick(value, "resets_at", "resetsAt"), issues, "invalid_reset", epoch=True),
    }


def _credits(value, issues):
    if value is None:
        return None
    if not isinstance(value, dict):
        issues.append("invalid_credits")
        return None
    balance = value.get("balance")
    if isinstance(balance, str):
        try:
            if not re.fullmatch(r"[0-9]{1,24}(?:\.[0-9]{1,12})?", balance):
                raise InvalidOperation
            number = Decimal(balance)
            if not number.is_finite():
                raise InvalidOperation
            balance = format(number.normalize(), "f")
        except InvalidOperation:
            balance = None
            issues.append("invalid_credits")
    elif balance is not None and _number(balance) is None:
        balance = None
        issues.append("invalid_credits")
    return {
        "has_credits": _boolean(_pick(value, "has_credits", "hasCredits"), issues),
        "unlimited": _boolean(value.get("unlimited"), issues),
        "balance": balance,
    }


def _usage(value, issues):
    if value is None:
        return None
    if not isinstance(value, dict):
        issues.append("invalid_usage")
        return None
    clean = {}
    for key, names in _USAGE.items():
        item = _pick(value, *names)
        if item is not None:
            if _number(item) is None:
                issues.append("invalid_usage")
            else:
                clean[key] = item
    return clean


def _coverage(value):
    if not isinstance(value, dict):
        return {}
    clean = {}
    for key in ("partial", "complete", "truncated", "rotated"):
        if type(value.get(key)) is bool:
            clean[key] = value[key]
    for key in ("files", "files_seen", "files_read", "files_scanned", "bytes_read", "lines_read", "events", "observations", "skipped", "dropped", "discovered", "entries_seen", "discovery_entries"):
        if type(value.get(key)) is int and 0 <= value[key] <= 10**12:
            clean[key] = value[key]
    if value.get("status") in ("complete", "partial", "unknown", "unavailable"):
        clean["status"] = value["status"]
    return clean


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _observation_id(value):
    return _digest({key: item for key, item in value.items() if key not in ("id", "collected_at", "coverage")})


def normalize_rate_limits(raw: dict, *, source_kind: str, source_id: str,
                          source_version: str | None = None, observed_at: str | None = None,
                          collected_at: str | None = None, usage: dict | None = None,
                          coverage: dict | None = None, account_ref: str | None = None) -> list[dict]:
    if not isinstance(raw, dict):
        raise LanternError("invalid_input")
    envelope = raw
    for _ in range(4):
        if isinstance(envelope.get("result"), dict):
            envelope = envelope["result"]
        else:
            break
    groups = _pick(envelope, "rateLimitsByLimitId", "rate_limits_by_limit_id")
    if groups is None:
        legacy = _pick(envelope, "rate_limits", "rateLimits")
        if isinstance(legacy, dict):
            envelope = legacy
            groups = _pick(envelope, "rateLimitsByLimitId", "rate_limits_by_limit_id")
    # 多 bucket 嘅 map 係權威；legacy rateLimits 只係其中一個 mirror。
    if isinstance(groups, dict) and groups:
        if len(groups) > 64:
            raise LanternError("bucket_limit")
        buckets = list(groups.items())
    elif any(key in envelope for key in ("primary", "secondary", "credits", "ordinary_usage_allowed", "ordinaryUsageAllowed", "spend_control_reached", "spendControlReached", "rate_limit_reached_type", "rateLimitReachedType")) or _usage(usage, []):
        # metadata-only 同空 rate_limits 都保留有效 token activity，唔補 quota。
        buckets = [(_pick(envelope, "limit_id", "limitId", "rate_limit_id", "rateLimitId") or "codex", envelope)]
    else:
        return []
    base_issues = []
    event_time = _timestamp(observed_at, base_issues)
    collection_time = _timestamp(collected_at if collected_at is not None else utc_now(), base_issues)
    source = {
        "kind": _identifier(source_kind, "unknown"),
        "id": _identifier(source_id, "unknown"),
        "version": source_version if isinstance(source_version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]{1,40})?", source_version) else None,
        "experimental": source_kind in ("live", "live_rpc", "codex_rpc", "codex_live", "rpc"),
    }
    result = []
    for bucket_id, bucket in buckets:
        if not isinstance(bucket, dict):
            raise LanternError("invalid_bucket")
        issues = list(base_issues)
        plan = _pick(bucket, "plan_type", "planType")
        if plan is not None and (not isinstance(plan, str) or plan not in _PLANS):
            plan = None
            issues.append("invalid_metadata")
        reason = _pick(bucket, "rate_limit_reached_type", "rateLimitReachedType")
        if reason is not None and (not isinstance(reason, str) or reason not in _REASONS):
            reason = "unknown"
        item = {
            "schema": SCHEMA_VERSION,
            "provider": "openai", "product_pool": "work_codex",
            "source": dict(source), "observed_at": event_time, "collected_at": collection_time,
            "account_ref": _identifier(account_ref, None),
            "identity_status": "verified" if isinstance(account_ref, str) and account_ref else "unverified",
            "bucket_id": _identifier(bucket_id, "unknown"), "plan_type": plan,
            "windows": {name: _window(bucket.get(name), issues) for name in ("primary", "secondary")},
            "credits": _credits(bucket.get("credits"), issues),
            "ordinary_usage_allowed": _boolean(_pick(bucket, "ordinary_usage_allowed", "ordinaryUsageAllowed"), issues),
            "rate_limit_reached_type": reason,
            "spend_control_reached": _boolean(_pick(bucket, "spend_control_reached", "spendControlReached"), issues),
            "usage": _usage(usage, issues), "coverage": _coverage(coverage),
        }
        item["validity"] = {"valid": not issues, "issues": sorted(set(issues))}
        item["id"] = _observation_id(item)
        result.append(item)
    return sorted(result, key=lambda item: item["bucket_id"])


def _source_key(item):
    return (item["provider"], item["product_pool"], item["source"]["kind"], item["source"]["id"], item["account_ref"] or "", item["bucket_id"])


def _normalized(item):
    """只接受 canonical observation，防止 journal 夾帶額外 payload。"""
    if not isinstance(item, dict) or item.get("schema") != SCHEMA_VERSION:
        return False
    try:
        source = item["source"]
        raw = {"rateLimitsByLimitId": {item["bucket_id"]: {
            **item["windows"], "credits": item["credits"], "plan_type": item["plan_type"],
            "ordinary_usage_allowed": item["ordinary_usage_allowed"],
            "rate_limit_reached_type": item["rate_limit_reached_type"],
            "spend_control_reached": item["spend_control_reached"],
        }}}
        normalized = normalize_rate_limits(raw, source_kind=source["kind"], source_id=source["id"], source_version=source["version"], observed_at=item["observed_at"], collected_at=item["collected_at"], usage=item["usage"], coverage=item["coverage"], account_ref=item["account_ref"])[0]
        validity = item["validity"]
        if set(validity) != {"valid", "issues"} or type(validity["valid"]) is not bool or not isinstance(validity["issues"], list):
            return False
        if len(validity["issues"]) > 32 or any(not isinstance(code, str) or code not in SAFE_CODES for code in validity["issues"]):
            return False
        if validity["valid"] != (not validity["issues"]):
            return False
        if item["collected_at"] is None:
            normalized["collected_at"] = None
        normalized["validity"] = validity
        normalized["id"] = _observation_id(normalized)
        return normalized == item and _ID.fullmatch(item["id"]) is not None
    except (KeyError, TypeError, ValueError, IndexError, LanternError):
        return False


def _evidence_kind(item):
    # 無 quota／credits／allowstate 嘅活動快照唔可以覆蓋上一筆 rate evidence。
    if (all(window is None for window in item["windows"].values())
            and all(item[key] is None for key in ("credits", "ordinary_usage_allowed",
                "rate_limit_reached_type", "spend_control_reached"))):
        return "token_activity" if item["usage"] else "unknown"
    return "rate_limits"


def _latest(observations):
    """每個 source/bucket 各留最新完整 rate 快照同活動快照；唔跨 event 拼欄位。"""
    latest = {}
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for item in observations:
        # unknown 同 token_activity 同屬無 rate evidence，最多兩筆，唔膨脹 schema。
        key = _source_key(item) + (_evidence_kind(item) == "rate_limits",)
        # collection time 唔係 evidence time；重讀唔取代同一 evidence。
        rank = (_time(item["observed_at"]) or floor, item["id"])
        previous = latest.get(key)
        if previous is None or rank > (_time(previous["observed_at"]) or floor, previous["id"]):
            latest[key] = item
    return [latest[key] for key in sorted(latest)]


def _research(value):
    """Reflection cache 都只接受固定 derived fields，唔信任 prose。"""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("sample_count", "comparable_count", "confounded"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 10**9 or key == "confounded" and type(item) is bool:
            result[key] = item
    codes = frozenset({
        "incomparable_pair", "observed_delta", "delta_zero_not_free",
        "source_bucket_window_reset_isolated", "explicit_feedback_recorded",
        "invalid_hypothesis_fields", "specify_numeric_test", "collect_comparable_evidence",
        "descriptive_only_no_causal_forecast", "findings_truncated",
    })
    for field, maximum in (("findings", 130), ("hypotheses", 100), ("next_steps", 101)):
        if not isinstance(value.get(field), list):
            continue
        result[field] = []
        for entry in value[field][:maximum]:
            if not isinstance(entry, dict):
                continue
            clean = {}
            for key, item in entry.items():
                if key == "code" and isinstance(item, str) and item in codes:
                    clean[key] = item
                elif key in ("id", "hypothesis_id") and isinstance(item, str) and len(item) == 36:
                    try:
                        if str(uuid.UUID(item)) == item:
                            clean[key] = item
                    except ValueError:
                        pass
                elif key == "metric" and item in (None, "used_percent", "window_minutes", "delta_used_percent"):
                    clean[key] = item
                elif key == "window" and item == "primary":
                    clean[key] = item
                elif key == "state" and item in ("untestable", "insufficient_evidence", "supported", "contradicted"):
                    clean[key] = item
                elif key in ("count", "group_count", "omitted_count", "sample_count", "observation_id_count") and type(item) is int and 0 <= item <= 10**9:
                    clean[key] = item
                elif key in ("confounded", "observation_ids_truncated") and type(item) is bool:
                    clean[key] = item
                elif key == "value" and _number(item, minimum=-1e18) is not None:
                    clean[key] = item
                elif key == "range" and isinstance(item, dict):
                    clean[key] = {name: item[name] for name in ("min", "max") if _number(item.get(name), minimum=-1e18) is not None}
                elif key == "observation_ids" and isinstance(item, list):
                    clean[key] = [ident for ident in item[:16] if isinstance(ident, str) and _ID.fullmatch(ident)]
            if (field == "hypotheses" and "id" in clean and "state" in clean) or (field != "hypotheses" and "code" in clean):
                result[field].append(clean)
    return result


def build_status(observations: list[dict], *, now=None, freshness_seconds: int = 300,
                 diagnostics: list | None = None, research: dict | None = None) -> dict:
    if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
        raise LanternError("invalid_input")
    if type(freshness_seconds) is not int or freshness_seconds < 0:
        raise LanternError("invalid_input")
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise LanternError("invalid_timestamp")
    codes = {code if isinstance(code, str) and code in SAFE_CODES else "unknown_diagnostic" for code in (diagnostics or [])[:100]}
    valid = []
    for item in observations:
        if _normalized(item):
            valid.append(item)
        else:
            codes.add("invalid_observation")
    sources = []
    for item in _latest(valid):
        entry = json.loads(json.dumps(item))
        event = _time(item["observed_at"])
        age = (current - event).total_seconds() if event else None
        freshness = "unknown" if age is None or age < 0 else "fresh" if age <= freshness_seconds else "stale"
        windows = [window for window in item["windows"].values() if window is not None]
        percentages = [window["used_percent"] for window in windows if window["used_percent"] is not None]
        expired_reset = any(_time(window["resets_at"]) is not None and _time(window["resets_at"]) <= current for window in windows)
        reliable = freshness == "fresh" and item["validity"]["valid"] and not expired_reset
        percent = max(percentages) if percentages and reliable else None
        pressure = "unknown" if percent is None else "conserve" if percent >= 95 else "mindful" if percent >= 80 else "roomy"
        availability = "unknown"
        if reliable:
            if item["ordinary_usage_allowed"] is False or item["spend_control_reached"] is True or item["rate_limit_reached_type"] not in (None, "none", "unknown"):
                availability = "blocked"
            elif item["ordinary_usage_allowed"] is True:
                availability = "allowed"
        entry.update(evidence_kind=_evidence_kind(item), freshness=freshness, age_seconds=round(age, 3) if age is not None else None, pressure=pressure, availability=availability)
        sources.append(entry)
    derived = _research(research)
    return {"schema": SCHEMA_VERSION, "generated_at": _iso(current), "mode": "advisory", "billing_route": "unverified", "sources": sources, "research": derived, "diagnostics": sorted(codes)}


def _brief_rank(item):
    fresh = item.get("freshness") == "fresh"
    pressure = {"conserve": 3, "mindful": 2, "roomy": 1}.get(
        item.get("pressure") if isinstance(item.get("pressure"), str) else "", 0)
    windows = item.get("windows")
    percentages = [_number(windows[name].get("used_percent"), maximum=100)
                   for name in ("primary", "secondary") if isinstance(windows.get(name), dict)] if isinstance(windows, dict) else []
    percent = max((value for value in percentages if value is not None), default=-1)
    event = _time(item.get("observed_at")) if item.get("freshness") in ("fresh", "stale") else None
    return (fresh, fresh and item.get("availability") == "blocked",
            pressure if fresh else 0, percent if fresh and pressure else -1,
            event or datetime.min.replace(tzinfo=timezone.utc))


def _brief_source(index, item, *, compact=False):
    pressure = item.get("pressure") if item.get("pressure") in ("roomy", "mindful", "conserve", "unknown") else "unknown"
    freshness = item.get("freshness") if item.get("freshness") in ("fresh", "stale", "unknown") else "unknown"
    availability = item.get("availability") if item.get("availability") in ("allowed", "blocked", "unknown") else "unknown"
    label = f"來源 {index} 獨立" if compact else f"來源 {index}（獨立計）"
    line = f"{label}：{pressure}/{freshness}；availability {availability}"
    if item.get("evidence_kind") == "token_activity":
        line += "；token activity only"
    windows = item.get("windows")
    if isinstance(windows, dict):
        for name in ("primary", "secondary"):
            window = windows.get(name)
            if not isinstance(window, dict):
                continue
            percent = _number(window.get("used_percent"), maximum=100)
            minutes = _number(window.get("window_minutes"), maximum=5256000)
            if percent is not None:
                line += f"；{name} {percent:g}%"
                if not compact and minutes is not None and minutes > 0:
                    line += f"/{minutes:g}m"
    return line + "。"


def _brief_research_outcomes(research):
    """只呈現已投影嘅數值結果，唔將 supported 當成文字 claim 已證實。"""
    parts = []
    feedback = [item["count"] for item in research.get("findings", [])
                if item["code"] == "explicit_feedback_recorded" and "count" in item]
    if feedback:
        # 呢個 finding 本身係 aggregate；重複 cache entry 唔相加。
        parts.append(f"回饋 {max(feedback)}")
    hypotheses = research.get("hypotheses", [])
    states = []
    for state, label in (("supported", "吻合"), ("contradicted", "反證"),
                         ("insufficient_evidence", "不足"), ("untestable", "未測")):
        count = sum(item["state"] == state for item in hypotheses)
        if count:
            states.append(f"{label}{count}")
    if states:
        parts.append("假設數值：" + "/".join(states))
    if hypotheses or "confounded" in research:
        confounded = research.get("confounded")
        if confounded or any(item.get("confounded") is True for item in hypotheses):
            flag = "yes"
        elif confounded is not None:
            flag = "no"
        else:
            flag = "unknown"
        parts.append("confounded " + flag)
    return "；".join(parts) + "。" if parts else None


def render_brief(status: dict, *, max_bytes: int = 1024) -> str:
    if type(max_bytes) is not int or max_bytes < 0:
        raise LanternError("invalid_limit")
    header = "Work/Codex｜advisory；billing route 未核實；唔改 routing。"
    diagnostics = status.get("diagnostics", []) if isinstance(status, dict) else []
    if "journal_full" in diagnostics or "recording_paused" in diagnostics:
        header = "Work/Codex｜advisory；計費未核實；記錄暫停；唔改 routing。"
    caveat = "Included quota、credits、token activity 分帳；reset 唔等於已恢復。"
    research_lines = []
    research = _research(status.get("research")) if isinstance(status, dict) else {}
    if "sample_count" in research and "comparable_count" in research:
        research_lines.append(f"研究：{research['sample_count']} 個樣本，{research['comparable_count']} 組可比；只描述，唔作因果預測。")
    outcomes = _brief_research_outcomes(research)
    if outcomes:
        research_lines.append(outcomes)
    steps = {item["code"] for item in research.get("next_steps", [])}
    if "collect_comparable_evidence" in steps:
        research_lines.append("下一步：補同 source/bucket/window/reset 可比 evidence。")
    elif "specify_numeric_test" in steps:
        research_lines.append("下一步：明示數值測試；假設文字唔等於指令。")
    if any(item["code"] == "delta_zero_not_free" for item in research.get("findings", [])):
        research_lines.append("Δpercent = 0 唔代表免費。")
    sources = status.get("sources", []) if isinstance(status, dict) else []
    if not isinstance(sources, list):
        sources = []
    # 全部有界 sources 先排序，唔喺 hash 排序頭 64 筆入面先揀；index 對應原 status。
    ranked = sorted(((index, item) for index, item in enumerate(sources[:MAX_OBSERVATIONS], 1)
                     if isinstance(item, dict)), key=lambda pair: _brief_rank(pair[1]), reverse=True)

    def compose(rows):
        omitted = len(sources) - len(rows)
        summary = [f"略 {omitted} 個來源快照。"] if omitted else []
        if not sources:
            summary = ["冇可用 evidence；用量同 availability 未知。"]
        return [header, *rows, *summary, caveat, *research_lines]

    # 細 budget 先壓縮重複解說同 window duration，唔犧牲最相關讀數或研究結果。
    first = [_brief_source(*ranked[0])] if ranked else []
    if len("\n".join(compose(first)).encode("utf-8")) > max_bytes:
        research_lines = [line.replace("；只描述，唔作因果預測。", "；非因果。") for line in research_lines]
        # 呢個分支只縮固定句式；數值、confounded 同四類 outcomes 一樣保留。
        if "sample_count" in research and "comparable_count" in research:
            research_lines[0] = f"研究：{research['sample_count']}樣本/{research['comparable_count']}可比；非因果。"
        research_lines = [line.replace("補同 source/bucket/window/reset 可比 evidence", "同 source/bucket/window/reset 可比")
                          .replace("明示數值測試；假設文字唔等於指令", "數值測試；假設非指令") for line in research_lines]
        caveat = "Included quota/credits/token activity 分帳；reset 唔等於恢復。"
    rows = []
    for index, item in ranked[:64]:
        candidate = rows + [_brief_source(index, item)]
        if not rows and len("\n".join(compose(candidate)).encode("utf-8")) > max_bytes:
            candidate = [_brief_source(index, item, compact=True)]
        if len("\n".join(compose(candidate)).encode("utf-8")) > max_bytes:
            break
        rows = candidate
    # 預留 caveats、omitted count 同研究 feedforward；唔再將尾段 byte 截走。
    lines = compose(rows)
    if len("\n".join(lines).encode("utf-8")) <= max_bytes:
        return "\n".join(lines)
    # 極細 caller budget 放唔齊時只出完整固定句，唔截成半句意思。
    fitted = []
    for line in lines:
        if len("\n".join(fitted + [line]).encode("utf-8")) <= max_bytes:
            fitted.append(line)
    return "\n".join(fitted)


def status_fingerprint(status: dict) -> str:
    sources = []
    for item in status.get("sources", []):
        if isinstance(item, dict):
            sources.append({key: item.get(key) for key in (
                "provider", "product_pool", "source", "account_ref", "identity_status", "bucket_id", "plan_type", "windows", "credits", "ordinary_usage_allowed", "rate_limit_reached_type", "spend_control_reached", "usage", "validity", "freshness", "pressure", "availability",
            )})
    return _digest({"sources": sorted(sources, key=_digest), "diagnostics": status.get("diagnostics", []), "research": _research(status.get("research"))})
