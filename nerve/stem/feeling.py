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
    """
    Decide whether an arrival should fire.
    Returns list of reason dicts, or None if no trigger.
    """
    reasons = []
    always_fire = False

    # Pressure trigger
    if combined["pressure"] >= PRESSURE_THRESHOLD:
        reasons.append({"kind": "pressure", "value": round(combined["pressure"], 3)})

    # min_interval gate (bypassed only by always_fire mismatches)
    too_soon = (now_ts - last_fire_ts) < MIN_ARRIVAL_INTERVAL_SECONDS

    if not reasons:
        return None
    if too_soon and not always_fire:
        return None
    return reasons
