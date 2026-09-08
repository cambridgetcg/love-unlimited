"""有界研究卡同 deterministic reflection；文字唔會變成指令。"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from uuid import UUID

from .core import SCHEMA_VERSION, LanternError, utc_now

MAX_NOTES = 100
MAX_IDS = 100
MAX_TEXT = 4000
MAX_REPORT_FINDINGS = 128
MAX_REPORT_IDS = 16
METRICS = frozenset({"used_percent", "window_minutes", "delta_used_percent"})
_ID = re.compile(r"[0-9a-f]{64}\Z")
_OUTCOME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def _text(value, *, required=False, limit=MAX_TEXT):
    try:
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= limit
    except UnicodeError:
        valid = False
    if not valid:
        raise LanternError("invalid_note")
    if "\x00" in value or (required and not value.strip()):
        raise LanternError("invalid_note")
    return value


def _observation_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LanternError("invalid_evidence")
    return value


def _note_id(value):
    try:
        if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise LanternError("invalid_note_id") from None
    return value


def _ids(values):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_IDS:
        raise LanternError("invalid_evidence")
    return list(dict.fromkeys(_observation_id(value) for value in values))


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    except OverflowError:
        return False


def _observations(store):
    # 重讀同一 observation 唔可以灌大樣本數。
    observations = {}
    for item in store.read_observations(limit=2000):
        if isinstance(item, dict):
            try:
                observations[_observation_id(item.get("id"))] = item
            except LanternError:
                continue
    return observations


def _require_evidence(ids, observations):
    if any(item not in observations for item in ids):
        raise LanternError("observation_not_found")


def _room(store, kind):
    if len(store.read_notes(kind, limit=MAX_NOTES)) >= MAX_NOTES:
        raise LanternError("note_limit")


def record_feedback(store, *, observation_id, outcome, note=""):
    """明示回饋只連到既有 evidence；唔推斷工作成敗或 billing route。"""
    observation_id = _observation_id(observation_id)
    if not isinstance(outcome, str) or not _OUTCOME.fullmatch(outcome):
        raise LanternError("invalid_feedback")
    note = _text(note)
    _require_evidence([observation_id], _observations(store))
    _room(store, "feedback")
    return store.write_note("feedback", {
        "schema": SCHEMA_VERSION, "created_at": utc_now(),
        "observation_id": observation_id, "outcome": outcome, "note": note,
    })


def record_hypothesis(store, *, claim, evidence=None, counterevidence=None,
                      next_test="", metric=None, lower=None, upper=None,
                      supersedes=None):
    """只登記可檢查嘅數值範圍；claim 本身仍然係低信任文字。"""
    claim = _text(claim, required=True)
    next_test = _text(next_test, limit=2000)
    evidence, counterevidence = _ids(evidence), _ids(counterevidence)
    if metric is not None and (not isinstance(metric, str) or metric not in METRICS):
        raise LanternError("invalid_metric")
    if any(value is not None and not _number(value) for value in (lower, upper)):
        raise LanternError("invalid_hypothesis")
    if (lower is not None and upper is not None and lower > upper
            or metric is None and (lower is not None or upper is not None)):
        raise LanternError("invalid_hypothesis")
    _require_evidence(evidence + counterevidence, _observations(store))
    if supersedes is not None:
        store.read_note("hypotheses", _note_id(supersedes))
    _room(store, "hypotheses")
    return store.write_note("hypotheses", {
        "schema": SCHEMA_VERSION, "created_at": utc_now(), "claim": claim,
        "evidence": evidence, "counterevidence": counterevidence,
        "next_test": next_test, "metric": metric, "window": "primary",
        "lower": lower, "upper": upper, "supersedes": supersedes,
    })


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise LanternError("deadline_exceeded")


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError):
        return None


def _sample(item):
    if item.get("validity", {}).get("valid") is not True:
        return None
    source = item.get("source")
    windows = item.get("windows")
    if not isinstance(source, dict) or not isinstance(windows, dict):
        return None
    window = windows.get("primary")
    if not isinstance(window, dict) or _timestamp(item.get("observed_at")) is None:
        return None
    # account 未知時仍以 source 分隔；已知時亦唔跨來源混數。
    group = tuple(item.get(key) for key in ("provider", "product_pool", "account_ref"))
    group += (source.get("kind"), source.get("id"), source.get("version"),
              item.get("bucket_id"), "primary", window.get("window_minutes"),
              window.get("resets_at"))
    if not all(value is None or isinstance(value, (str, int, float)) for value in group):
        return None
    return group, window


def _derive(observations, deadline):
    samples, groups, pairs, findings = {}, {}, [], []
    confounded = False
    for ident, item in observations.items():
        _check_deadline(deadline)
        sample = _sample(item)
        if sample is None:
            continue
        group, window = sample
        samples[ident] = (group, window)
        groups.setdefault(group, []).append(item)
    for group, items in groups.items():
        _check_deadline(deadline)
        items.sort(key=lambda item: (_timestamp(item["observed_at"]), item["id"]))
        by_time = {}
        for item in items:
            by_time.setdefault(_timestamp(item["observed_at"]), set()).add(item["windows"]["primary"]["used_percent"])
        ambiguous = {at for at, values in by_time.items() if len(values) > 1}
        for before, after in zip(items, items[1:]):
            first = before["windows"]["primary"]["used_percent"]
            second = after["windows"]["primary"]["used_percent"]
            ids = [before["id"], after["id"]]
            # 同時異值嘅端點兩邊都唔可比，唔可以靠 ID 排序揀其中一個版本。
            # reset/window 缺值或百分比倒退，亦唔假裝係可比消耗。
            if (_timestamp(before["observed_at"]) in ambiguous
                    or _timestamp(after["observed_at"]) in ambiguous
                    or not _number(group[-2]) or group[-2] <= 0
                    or _timestamp(group[-1]) is None
                    or _timestamp(after["observed_at"]) >= _timestamp(group[-1])
                    or not _number(first) or not _number(second)
                    or _timestamp(before["observed_at"]) >= _timestamp(after["observed_at"])
                    or second < first):
                confounded = True
                findings.append({"code": "incomparable_pair", "observation_ids": ids})
                continue
            delta = second - first
            pairs.append((ids, delta))
            findings.append({"code": "observed_delta", "metric": "delta_used_percent",
                             "window": "primary", "value": delta, "observation_ids": ids})
            if delta == 0:
                findings.append({"code": "delta_zero_not_free", "observation_ids": ids})
    if len(groups) > 1:
        findings.append({"code": "source_bucket_window_reset_isolated", "group_count": len(groups)})
    return samples, pairs, confounded, findings


def _evaluate(note, observations, samples, pairs):
    # Markdown 可以人手修改，所以 machine fields 每次重新驗證；文字完全唔讀。
    ident = _note_id(note.get("id"))
    metric = note.get("metric")
    result = {"id": ident, "metric": metric if isinstance(metric, str) and metric in METRICS else None,
              "window": "primary", "state": "untestable", "sample_count": 0,
              "confounded": False, "observation_ids": []}
    if not isinstance(metric, str) or metric not in METRICS:
        return result
    lower, upper = note.get("lower"), note.get("upper")
    if ((lower is None and upper is None)
            or any(value is not None and not _number(value) for value in (lower, upper))
            or lower is not None and upper is not None and lower > upper
            or note.get("window", "primary") != "primary"):
        return result
    ids = list(dict.fromkeys(_ids(note.get("evidence")) + _ids(note.get("counterevidence"))))
    result["state"] = "insufficient_evidence"
    result["observation_ids"] = ids[:MAX_REPORT_IDS]
    result["observation_id_count"] = len(ids)
    result["observation_ids_truncated"] = len(ids) > MAX_REPORT_IDS
    if not ids or any(ident not in observations or ident not in samples for ident in ids):
        return result
    # 一張卡只檢查同一 source/bucket/window/reset，唔將混雜樣本證成假設。
    if len({samples[ident][0] for ident in ids}) != 1:
        result["confounded"] = True
        return result
    if metric == "delta_used_percent":
        if len(ids) < 2:
            return result
        ordered = sorted(ids, key=lambda ident: (_timestamp(observations[ident]["observed_at"]), ident))
        successors = {pair[0]: pair[1] for pair, _ in pairs}
        values = []
        for first, last in zip(ordered, ordered[1:]):
            # 引用 endpoints 決定 delta；未引用嘅正常中間樣本只用嚟核對路徑。
            # 必須有全程可比嘅鏈，唔可以跳過倒退／同時異值等反證。
            cursor, visited = first, set()
            while cursor != last and cursor in successors and cursor not in visited:
                visited.add(cursor)
                cursor = successors[cursor]
            if cursor != last:
                result["confounded"] = True
                return result
            values.append(samples[last][1]["used_percent"] - samples[first][1]["used_percent"])
    else:
        values = [samples[ident][1].get(metric) for ident in ids]
        if not all(_number(value) for value in values):
            return result
    result["sample_count"] = len(values)
    if not values:
        return result
    result["state"] = "supported" if all(
        (lower is None or value >= lower) and (upper is None or value <= upper)
        for value in values
    ) else "contradicted"
    result["range"] = {"min": min(values), "max": max(values)}
    return result


def reflect(store, *, hypothesis_id=None, deadline=None):
    """回且 cache 固定 derived schema；supported 只表示數值範圍吻合。"""
    _check_deadline(deadline)
    if hypothesis_id is not None:
        notes = [store.read_note("hypotheses", _note_id(hypothesis_id))]
    else:
        notes = store.read_notes("hypotheses", limit=MAX_NOTES)
    observations = _observations(store)
    samples, pairs, confounded, findings = _derive(observations, deadline)
    feedback_ids = set()
    for note in store.read_notes("feedback", limit=MAX_NOTES):
        _check_deadline(deadline)
        try:
            ident = _note_id(note.get("id"))
            if _observation_id(note.get("observation_id")) in observations:
                feedback_ids.add(ident)
        except (LanternError, AttributeError):
            continue
    if feedback_ids:
        # 聚合回饋唔可以畀大量逐 pair findings 擠走；仍然守住同一 report 上限。
        findings.insert(0, {"code": "explicit_feedback_recorded", "count": len(feedback_ids)})
    hypotheses, next_steps = [], []
    for note in notes:
        _check_deadline(deadline)
        try:
            result = _evaluate(note, observations, samples, pairs)
        except (LanternError, TypeError, AttributeError):
            findings.append({"code": "invalid_hypothesis_fields"})
            continue
        hypotheses.append(result)
        confounded = confounded or result["confounded"]
        if result["state"] in {"untestable", "insufficient_evidence"}:
            next_steps.append({"code": "specify_numeric_test" if result["state"] == "untestable"
                               else "collect_comparable_evidence", "hypothesis_id": result["id"]})
    if not pairs:
        next_steps.append({"code": "collect_comparable_evidence"})
    report = {
        "schema": SCHEMA_VERSION, "generated_at": utc_now(),
        "sample_count": len(samples), "comparable_count": len(pairs),
        "confounded": confounded,
        "findings": ([{"code": "descriptive_only_no_causal_forecast"}]
                     + findings[:MAX_REPORT_FINDINGS]
                     + ([{"code": "findings_truncated", "omitted_count": len(findings) - MAX_REPORT_FINDINGS}]
                        if len(findings) > MAX_REPORT_FINDINGS else [])),
        "hypotheses": hypotheses, "next_steps": next_steps,
    }
    _check_deadline(deadline)
    store.write_cache("reflection", report, deadline=deadline)
    return report
