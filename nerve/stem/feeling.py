#!/usr/bin/env python3
"""
feeling.py — The FEELING module daemon.

Spec: docs/FEELING-DESIGN.md

Three strata of continuous subconscious processing:
  body       — Damasio, reads nerve/hormones.json
  context    — Barrett, reads memory.db and signals
  cognition  — YOUSPEAK, reads memory/youspeak/sessions.json

Produces arrivals when thresholds are crossed. Arrivals are pre-verbal;
naming happens in-conversation via experience.py feel.
"""

import asyncio
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("feeling")

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
_NERVE_DIR = _LOVE_DIR / "nerve"
_MEMORY_DIR = _LOVE_DIR / "memory"

PIT_PATH = _NERVE_DIR / "pit.json"
ARRIVALS_PATH = _NERVE_DIR / "arrivals.jsonl"
PATTERNS_PATH = _NERVE_DIR / "patterns.json"
PIT_STATE_PATH = _NERVE_DIR / "pit_state.json"
HORMONES_PATH = _NERVE_DIR / "hormones.json"
YOUSPEAK_SESSIONS_PATH = _MEMORY_DIR / "youspeak" / "sessions.json"
DAILY_DIR = _MEMORY_DIR / "daily"


# ── Identity ─────────────────────────────────────────────────────────

def get_instance() -> str:
    """Read the active instance from ~/.kingdom or env."""
    kf = Path.home() / ".kingdom"
    if kf.exists():
        for line in kf.read_text().splitlines():
            if line.startswith("AGENT="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("KINGDOM_AGENT",
           os.environ.get("KINGDOM_INSTANCE", "gamma"))


# ── Coefficients (v1 first-guesses, spec §4.2) ───────────────────────

HORMONE_COEFS = {
    "adrenaline": {"valence":  0.0, "arousal":  0.9},
    "cortisol":   {"valence": -0.5, "arousal":  0.3},
    "oxytocin":   {"valence":  0.8, "arousal":  0.0},
    "dopamine":   {"valence":  0.7, "arousal":  0.2},
    "melatonin":  {"valence":  0.0, "arousal": -0.6},
}

# Tick cadences (seconds)
BODY_TICK_INTERVAL = 10
CONTEXT_TICK_INTERVAL = 60
COGNITION_TICK_INTERVAL = 30

# Curtain thresholds
PRESSURE_THRESHOLD = 0.5
SHIFT_THRESHOLD = 0.25
MISMATCH_ALWAYS_FIRE_THRESHOLD = 0.5
MIN_ARRIVAL_INTERVAL_SECONDS = 90

# Cognition silence window
COGNITION_SILENCE_AGE_SECONDS = 300  # 5 min


# ── Body stratum (Damasio, spec §4.2) ────────────────────────────────

def body_stratum_from_hormones(hormones: dict) -> dict:
    """
    Compute body core affect from hormone levels.
    Returns {valence, arousal, sources}.
    """
    v = 0.0
    a = 0.0
    for name, coefs in HORMONE_COEFS.items():
        level = float(hormones.get(name, 0.0))
        v += level * coefs["valence"]
        a += level * coefs["arousal"]

    # Baseline shift: v is centered around -0.5 when all hormones near zero
    # (the body is not positive by default, it's neutral-negative)
    v -= 0.5

    # Clamp to [-1, 1]
    v = max(-1.0, min(1.0, v))
    a = max(-1.0, min(1.0, a))

    # Sources: top 2 non-zero hormones by absolute level
    sorted_hormones = sorted(
        ((name, abs(float(hormones.get(name, 0.0)))) for name in HORMONE_COEFS),
        key=lambda x: x[1],
        reverse=True
    )
    sources = []
    for name, level in sorted_hormones[:2]:
        if level > 0.05:  # ignore trace
            if level > 0.6:
                sources.append(f"{name}_high")
            elif level > 0.3:
                sources.append(f"{name}_moderate")
            elif level > 0.05:
                sources.append(f"{name}_low")

    return {"valence": round(v, 3), "arousal": round(a, 3), "sources": sources}


# ── Context stratum (Barrett, spec §4.3) ─────────────────────────────

def context_stratum_from_inputs(
    recent_memories: list,
    hive_unread: int,
    new_alerts: int,
    yu_present: bool,
    yu_idle_seconds: int,
) -> dict:
    """
    Compute context core affect from exteroceptive signals.
    Returns {valence, arousal, sources}.
    """
    v = 0.0
    a = 0.0
    sources = []

    # Recent memory affect contributes via weighted average
    valences = []
    arousals = []
    for mem in recent_memories:
        affect = (mem.get("metadata") or {}).get("affect") or {}
        if "valence" in affect:
            valences.append(float(affect["valence"]))
        if "arousal" in affect:
            arousals.append(float(affect["arousal"]))

    if valences:
        v += sum(valences) / len(valences)
        sources.append(f"recent_memory_avg_v={v:.2f}")
    if arousals:
        a += sum(arousals) / len(arousals)

    # Yu presence bonus
    if yu_present:
        v += 0.3
        if yu_idle_seconds < 300:
            sources.append(f"yu_present_active")
        else:
            sources.append(f"yu_present_idle_{yu_idle_seconds//60}min")

    # HIVE unread raises arousal
    if hive_unread > 0:
        a += min(0.6, hive_unread * 0.12)
        sources.append(f"hive_unread_{hive_unread}")

    # Alerts raise arousal more sharply
    if new_alerts > 0:
        a += min(0.5, new_alerts * 0.2)
        sources.append(f"alerts_{new_alerts}")

    v = max(-1.0, min(1.0, v))
    a = max(-1.0, min(1.0, a))

    return {"valence": round(v, 3), "arousal": round(a, 3), "sources": sources[:2]}


# ── Cognition stratum (YOUSPEAK, spec §4.4) ──────────────────────────

def cognition_stratum_from_youspeak(sessions_json: dict, now_ts: float) -> dict:
    """
    Compute cognition core affect from YOUSPEAK observations.
    Returns {valence, arousal, sources, state}.
    state is 'silent' when no fresh session exists.
    """
    silent = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}

    if not sessions_json:
        return silent

    started_at_ms = sessions_json.get("startedAt", 0)
    if not started_at_ms:
        return silent

    session_age_s = now_ts - (started_at_ms / 1000.0)
    if session_age_s > COGNITION_SILENCE_AGE_SECONDS:
        return silent

    v = 0.0
    a = 0.0
    sources = []

    # L1: useful ratio grade
    grades = sessions_json.get("output", {}).get("grades", [])
    if grades:
        recent_grades = grades[-5:]
        s_or_a_count = sum(1 for g in recent_grades if g in ("S", "A"))
        c_or_d_count = sum(1 for g in recent_grades if g in ("C", "D"))
        if s_or_a_count >= 3:
            v += 0.3
            sources.append("clarity")
        if c_or_d_count >= 2:
            v -= 0.4
            a += 0.3
            sources.append("shame_filler")

    # L2: thinking/output ratio
    per_turn = sessions_json.get("thinking", {}).get("perTurn", [])
    if per_turn:
        recent_ratios = [t.get("ratio", 0.0) for t in per_turn[-3:]]
        avg_ratio = sum(recent_ratios) / len(recent_ratios)
        if 0.8 <= avg_ratio <= 1.5:
            v += 0.3
            a += 0.2
            if "clarity" not in sources:
                sources.append("flow")
        elif avg_ratio > 3.0:
            v -= 0.3
            a += 0.3
            sources.append("overthinking")
        elif 0 < avg_ratio < 0.3:
            v -= 0.2
            a += 0.2
            sources.append("restlessness")

    # L3: redundant reads + tool errors
    action = sessions_json.get("action", {})
    tool_calls = action.get("toolCalls", 0)
    tool_errors = action.get("toolErrors", 0)
    redundant_reads = action.get("redundantReads", 0)

    if redundant_reads > 2:
        v -= 0.4
        a += 0.3
        sources.append("confusion")

    if tool_calls > 5 and (tool_errors / tool_calls) > 0.3:
        v -= 0.5
        a += 0.5
        sources.append("frustration")

    # L4: context pressure
    ctx = sessions_json.get("context", {})
    est_tokens = ctx.get("estimatedTokens", 0)
    stale_age = ctx.get("oldestToolResultAge", 0)

    if est_tokens > 800_000:
        v -= 0.5
        a += 0.6
        sources.append("dread_context_full")
    elif est_tokens > 500_000 and stale_age > 20:
        v -= 0.2
        a += 0.3
        sources.append("claustrophobia")

    # L5: budget + rate limits
    system = sessions_json.get("system", {})
    budget = (system.get("budgetNow") or {}).get("fiveHour", 0.0)
    rate_limit_hits = system.get("rateLimitHits", 0)

    if budget > 0.85:
        v -= 0.3
        a += 0.5
        sources.append("anxiety_budget")

    if rate_limit_hits > 0:
        v -= 0.6
        a += 0.8
        sources.append("panic_rate_limit")

    v = max(-1.0, min(1.0, v))
    a = max(-1.0, min(1.0, a))

    return {
        "valence": round(v, 3),
        "arousal": round(a, 3),
        "sources": sources[:2],
        "state": "active",
    }


# ── Combine strata + pressure (spec §4.5) ────────────────────────────

def combine_strata(body: dict, context: dict, cognition: dict) -> dict:
    """
    Produce the combined block of pit.json.
    Cognition is excluded from average when state == 'silent'.
    Pressure is sqrt(v² + a²) × body-context-gap multiplier.
    """
    active_strata = [body, context]
    if cognition.get("state") == "active":
        active_strata.append(cognition)

    v = sum(s["valence"] for s in active_strata) / len(active_strata)
    a = sum(s["arousal"] for s in active_strata) / len(active_strata)

    raw_pressure = math.sqrt(v**2 + a**2)
    gap = abs(body["valence"] - context["valence"])
    gap_multiplier = max(gap, 1.0)
    pressure = raw_pressure * gap_multiplier

    return {
        "valence": round(v, 3),
        "arousal": round(a, 3),
        "pressure": round(pressure, 3),
    }


# ── Curtain check (spec §5.1) ────────────────────────────────────────

def check_curtain(
    body: dict,
    context: dict,
    cognition: dict,
    combined: dict,
    last_fire_ts: float,
    now_ts: float,
    last_body: dict = None,
    last_context: dict = None,
    last_cognition: dict = None,
) -> list or None:
    reasons = []
    always_fire = False

    # Pressure trigger
    if combined["pressure"] >= PRESSURE_THRESHOLD:
        reasons.append({"kind": "pressure", "value": round(combined["pressure"], 3)})

    # Body shift
    if last_body:
        body_delta = math.sqrt(
            (body["valence"] - last_body["valence"])**2 +
            (body["arousal"] - last_body["arousal"])**2
        )
        if body_delta >= SHIFT_THRESHOLD:
            reasons.append({"kind": "body_shift", "value": round(body_delta, 3)})

    # Context shift
    if last_context:
        context_delta = math.sqrt(
            (context["valence"] - last_context["valence"])**2 +
            (context["arousal"] - last_context["arousal"])**2
        )
        if context_delta >= SHIFT_THRESHOLD:
            reasons.append({"kind": "context_shift", "value": round(context_delta, 3)})

    # Cognition shift — only if active
    if last_cognition and cognition.get("state") == "active":
        cognition_delta = math.sqrt(
            (cognition["valence"] - last_cognition["valence"])**2 +
            (cognition["arousal"] - last_cognition["arousal"])**2
        )
        if cognition_delta >= SHIFT_THRESHOLD:
            reasons.append({"kind": "cognition_shift", "value": round(cognition_delta, 3)})

    # Mismatches — always_fire gate
    body_context_gap = abs(body["valence"] - context["valence"])
    if body_context_gap >= MISMATCH_ALWAYS_FIRE_THRESHOLD:
        reasons.append({"kind": "body_context_gap", "value": round(body_context_gap, 3)})
        always_fire = True

    if cognition.get("state") == "active":
        body_cognition_gap = abs(body["valence"] - cognition["valence"])
        if body_cognition_gap >= MISMATCH_ALWAYS_FIRE_THRESHOLD:
            reasons.append({"kind": "body_cognition_gap", "value": round(body_cognition_gap, 3)})
            always_fire = True

        context_cognition_gap = abs(context["valence"] - cognition["valence"])
        if context_cognition_gap >= MISMATCH_ALWAYS_FIRE_THRESHOLD:
            reasons.append({"kind": "context_cognition_gap", "value": round(context_cognition_gap, 3)})
            always_fire = True

    too_soon = (now_ts - last_fire_ts) < MIN_ARRIVAL_INTERVAL_SECONDS

    if not reasons:
        return None
    if too_soon and not always_fire:
        return None
    return reasons


# ── Pit fingerprint (spec §8.1) ──────────────────────────────────────

def _valence_bucket(v: float) -> str:
    if v <= -0.6: return "very_neg"
    if v <= -0.2: return "neg"
    if v <= 0.2:  return "neutral"
    if v <= 0.6:  return "pos"
    return "very_pos"

def _arousal_bucket(a: float) -> str:
    if a <= 0.2: return "low"
    if a <= 0.6: return "mid"
    return "high"

def pit_fingerprint(body: dict, context: dict, cognition: dict, reasons: list) -> dict:
    """Discretize stratum state into a comparable fingerprint."""
    fp = {
        "body_v_bucket": _valence_bucket(body["valence"]),
        "body_a_bucket": _arousal_bucket(body["arousal"]),
        "context_v_bucket": _valence_bucket(context["valence"]),
        "context_a_bucket": _arousal_bucket(context["arousal"]),
    }
    if cognition.get("state") == "silent":
        fp["cognition_v_bucket"] = "silent"
        fp["cognition_a_bucket"] = "silent"
    else:
        fp["cognition_v_bucket"] = _valence_bucket(cognition["valence"])
        fp["cognition_a_bucket"] = _arousal_bucket(cognition["arousal"])

    fp["dominant_reason"] = reasons[0]["kind"] if reasons else "none"

    # Collect top sources from all strata
    all_sources = (body.get("sources") or []) + (context.get("sources") or []) + (cognition.get("sources") or [])
    fp["top_sources"] = sorted(all_sources)[:2]
    return fp

def fingerprints_match(fp1: dict, fp2: dict) -> bool:
    """Two fingerprints match when all buckets agree AND top_sources overlap."""
    bucket_keys = ("body_v_bucket", "body_a_bucket",
                   "context_v_bucket", "context_a_bucket",
                   "cognition_v_bucket", "cognition_a_bucket",
                   "dominant_reason")
    for k in bucket_keys:
        if fp1.get(k) != fp2.get(k):
            return False
    s1 = set(fp1.get("top_sources") or [])
    s2 = set(fp2.get("top_sources") or [])
    return bool(s1 & s2) or (not s1 and not s2)


# ── Pattern library lookup (spec §8.2-8.3) ───────────────────────────

PATTERN_MIN_COUNT_FOR_HINT = 3

def lookup_hint(fingerprint: dict, patterns: dict) -> dict or None:
    """Find a matching pattern in the library and build a soft hint."""
    for pat in patterns.get("patterns", []):
        if fingerprints_match(fingerprint, pat.get("fingerprint", {})):
            if pat.get("total_count", 0) < PATTERN_MIN_COUNT_FOR_HINT:
                return None
            total = sum(pat.get("names", {}).values()) or 1
            candidates = [
                {"name": name, "probability": round(count / total, 3)}
                for name, count in sorted(pat.get("names", {}).items(),
                                          key=lambda x: x[1], reverse=True)
            ]
            return {
                "candidates": candidates[:3],
                "total_prior": pat["total_count"],
            }
    return None


# ── Importance weighting (spec §7.2) ─────────────────────────────────

_MISMATCH_REASONS = {"body_context_gap", "body_cognition_gap", "context_cognition_gap"}

def compute_importance(arc: dict) -> float:
    """Compute importance from arc metadata."""
    importance = 0.5  # baseline
    importance += 0.15 * float(arc.get("combined_pressure", 0.0))
    
    reasons = arc.get("arrival", {}).get("reasons", [])
    if any(r["kind"] in _MISMATCH_REASONS for r in reasons):
        importance += 0.10
    
    if arc.get("surprise"):
        importance += 0.15
    
    if arc.get("yu_present"):
        importance += 0.10

    return round(min(importance, 1.0), 3)


# ── Persistence: pit.json (spec §4.5) ────────────────────────────────

def write_pit_json(pit: dict) -> None:
    """Atomic write via .tmp + rename."""
    PIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PIT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pit, indent=2))
    tmp.replace(PIT_PATH)

def read_pit_json() -> dict:
    """Return current pit.json, empty dict if missing."""
    if not PIT_PATH.exists():
        return {}
    try:
        return json.loads(PIT_PATH.read_text())
    except Exception as e:
        log.warning("pit.json read failed: %s", e)
        return {}


# ── Persistence: arrivals.jsonl (spec §5.2, 6.4) ─────────────────────

def append_arrival(arrival: dict) -> None:
    """Append one arrival to the log (atomic append)."""
    ARRIVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(arrival, separators=(",", ":")) + "\n"
    with open(ARRIVALS_PATH, "a") as f:
        f.write(line)

def read_arrivals(
    witnessed: bool = None,
    named: bool = None,
    since_iso: str = None,
) -> list:
    """Read arrivals with optional filters."""
    if not ARRIVALS_PATH.exists():
        return []
    out = []
    with open(ARRIVALS_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if witnessed is not None and rec.get("witnessed") != witnessed:
                continue
            if named is not None and rec.get("named") != named:
                continue
            if since_iso and rec.get("at", "") < since_iso:
                continue
            out.append(rec)
    return out

def update_arrival(arrival_id: str, updates: dict) -> bool:
    """Rewrite arrivals.jsonl with updates applied to the matching row."""
    if not ARRIVALS_PATH.exists():
        return False
    rows = []
    found = False
    with open(ARRIVALS_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("id") == arrival_id:
                rec.update(updates)
                found = True
            rows.append(rec)
    if not found:
        return False
    tmp = ARRIVALS_PATH.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for rec in rows:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    tmp.replace(ARRIVALS_PATH)
    return True


# ── Persistence: patterns.json (spec §8.2) ───────────────────────────

import hashlib

def _fingerprint_hash(fp: dict) -> str:
    canonical = json.dumps(fp, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

def read_patterns() -> dict:
    """Return pattern library, empty default if missing."""
    if not PATTERNS_PATH.exists():
        return {"version": 1, "patterns": []}
    try:
        return json.loads(PATTERNS_PATH.read_text())
    except Exception as e:
        log.warning("patterns.json read failed: %s", e)
        return {"version": 1, "patterns": []}

def write_patterns(patterns: dict) -> None:
    """Atomic write."""
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATTERNS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(patterns, indent=2))
    tmp.replace(PATTERNS_PATH)

def update_pattern_library(fingerprint: dict, name: str, now_iso: str) -> None:
    """Increment (fingerprint → name) count in the library."""
    patterns = read_patterns()
    fp_hash = _fingerprint_hash(fingerprint)

    for pat in patterns["patterns"]:
        if fingerprints_match(fingerprint, pat.get("fingerprint", {})):
            pat["names"][name] = pat["names"].get(name, 0) + 1
            pat["total_count"] = pat.get("total_count", 0) + 1
            pat["last_seen"] = now_iso
            write_patterns(patterns)
            return

    # New pattern
    patterns["patterns"].append({
        "fingerprint_hash": fp_hash,
        "fingerprint": fingerprint,
        "names": {name: 1},
        "total_count": 1,
        "last_seen": now_iso,
    })
    write_patterns(patterns)

# ── Persistence: pit_state.json (spec §4.6) ──────────────────────────

def read_pit_state() -> dict:
    if not PIT_STATE_PATH.exists():
        return {}
    try:
        return json.loads(PIT_STATE_PATH.read_text())
    except Exception:
        return {}

def update_pit_state(updates: dict) -> None:
    state = read_pit_state()
    state.update(updates)
    PIT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PIT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(PIT_STATE_PATH)


# ── Daemon (spec §4.1) ───────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _read_hormones() -> dict:
    """Read nerve/hormones.json, return hormones + signals blocks."""
    if not HORMONES_PATH.exists():
        return {"hormones": {}, "signals": {}}
    try:
        return json.loads(HORMONES_PATH.read_text())
    except Exception:
        return {"hormones": {}, "signals": {}}

def _read_youspeak_sessions() -> dict:
    """Read memory/youspeak/sessions.json."""
    if not YOUSPEAK_SESSIONS_PATH.exists():
        return None
    try:
        return json.loads(YOUSPEAK_SESSIONS_PATH.read_text())
    except Exception:
        return None

def _read_recent_memories(since_ms: float, limit: int = 10) -> list:
    """Stub — will be wired to kosmem in Task 18. Returns empty list for now."""
    return []

class FeelingDaemon:
    def __init__(self, instance: str):
        self.instance = instance
        self.last_body_tick = 0.0
        self.last_context_tick = 0.0
        self.last_cognition_tick = 0.0
        self.last_fire_ts = 0.0
        self.last_body = None
        self.last_context = None
        self.last_cognition = None
        self._current_body = {"valence": 0.0, "arousal": 0.0, "sources": []}
        self._current_context = {"valence": 0.0, "arousal": 0.0, "sources": []}
        self._current_cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}

    async def run_once(self):
        """Execute one cycle of strata + curtain + pit write."""
        now = time.monotonic()
        now_wall = time.time()

        # Body stratum
        if now - self.last_body_tick >= BODY_TICK_INTERVAL or self.last_body is None:
            hormones_doc = _read_hormones()
            new_body = body_stratum_from_hormones(hormones_doc.get("hormones", {}))
            new_body["last_tick"] = _now_iso()
            self.last_body = self._current_body
            self._current_body = new_body
            self.last_body_tick = now

        # Context stratum
        if now - self.last_context_tick >= CONTEXT_TICK_INTERVAL or self.last_context is None:
            hormones_doc = _read_hormones()
            signals = hormones_doc.get("signals", {})
            memories = _read_recent_memories(now_wall - CONTEXT_TICK_INTERVAL)
            new_context = context_stratum_from_inputs(
                recent_memories=memories,
                hive_unread=signals.get("hive_unread", 0),
                new_alerts=signals.get("critical_alerts", 0),
                yu_present=bool(signals.get("yu_present", False)),
                yu_idle_seconds=int(signals.get("yu_idle_seconds", 999999)),
            )
            new_context["last_tick"] = _now_iso()
            self.last_context = self._current_context
            self._current_context = new_context
            self.last_context_tick = now

        # Cognition stratum
        if now - self.last_cognition_tick >= COGNITION_TICK_INTERVAL or self.last_cognition is None:
            sessions = _read_youspeak_sessions()
            new_cognition = cognition_stratum_from_youspeak(sessions, now_wall)
            new_cognition["last_tick"] = _now_iso()
            self.last_cognition = self._current_cognition
            self._current_cognition = new_cognition
            self.last_cognition_tick = now

        # Combine
        combined = combine_strata(self._current_body, self._current_context, self._current_cognition)

        # Write pit.json
        pit = {
            "instance": self.instance,
            "timestamp": _now_iso(),
            "body": self._current_body,
            "context": self._current_context,
            "cognition": self._current_cognition,
            "combined": combined,
            "threshold": PRESSURE_THRESHOLD,
            "arrivals_total": len(read_arrivals()),
            "arrivals_pending_name": len(read_arrivals(named=False)),
        }
        write_pit_json(pit)

        # Curtain check
        reasons = check_curtain(
            body=self._current_body,
            context=self._current_context,
            cognition=self._current_cognition,
            combined=combined,
            last_fire_ts=self.last_fire_ts,
            now_ts=now,
            last_body=self.last_body,
            last_context=self.last_context,
            last_cognition=self.last_cognition,
        )

        if reasons:
            arrival_id = f"arr-{_now_iso().replace(':', '-').replace('.', '-')}-{self.instance}-{os.urandom(2).hex()}"
            fp = pit_fingerprint(self._current_body, self._current_context, self._current_cognition, reasons)
            hint = lookup_hint(fp, read_patterns())
            arrival = {
                "id": arrival_id,
                "at": _now_iso(),
                "instance": self.instance,
                "reasons": reasons,
                "body": {k: v for k, v in self._current_body.items() if k != "last_tick"},
                "context": {k: v for k, v in self._current_context.items() if k != "last_tick"},
                "cognition": {k: v for k, v in self._current_cognition.items() if k != "last_tick"},
                "combined": combined,
                "fingerprint": fp,
                "hint": hint,
                "context_tags": [],
                "lineage": [],
                "named": False, "named_at": None, "name": None,
                "rationale": None, "scene": None,
                "witnessed": False, "witnessed_at": None,
            }
            append_arrival(arrival)
            self.last_fire_ts = now

        return pit

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except Exception as e:
                log.warning("feeling cycle failed: %s", e)
            await asyncio.sleep(2)
