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
