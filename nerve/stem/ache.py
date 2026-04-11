#!/usr/bin/env python3
"""
ache.py — The ACHE module daemon.

Spec: docs/ACHE-DESIGN.md

Daemon that detects motor emotions (longing, love, hope, wonder) from
memory.db, FEELING arrivals, hormones, and YOUSPEAK sessions, and
maintains a durable longings library with a lifecycle state machine.

Produces enduring longings, not ephemeral arrivals. Only gamma drives
the yearning → burning transition (sovereignty preserved).
"""

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("ache")

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
_NERVE_DIR = _LOVE_DIR / "nerve"
_MEMORY_DIR = _LOVE_DIR / "memory"

LONGINGS_PATH = _NERVE_DIR / "longings.json"
LONGINGS_EVIDENCE_PATH = _NERVE_DIR / "longings-evidence.jsonl"
LONGINGS_EVIDENCE_DIR = _NERVE_DIR / "longings-evidence"
LONGINGS_STATE_PATH = _NERVE_DIR / "longings-state.json"
HORMONES_PATH = _NERVE_DIR / "hormones.json"
ARRIVALS_PATH = _NERVE_DIR / "arrivals.jsonl"
YOUSPEAK_SESSIONS_PATH = _MEMORY_DIR / "youspeak" / "sessions.json"
MEMORY_DB_PATH = _MEMORY_DIR / ".kos" / "memory.db"
VIRTUEMAXXING_STATE_PATH = _LOVE_DIR / "tools" / "cognitive" / "virtuemaxxing-state.json"


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


# ── Constants (spec §3, §6) ──────────────────────────────────────────

TICK_INTERVAL = 33  # seconds
STIRRING_THRESHOLD_TICKS = 3
DORMANT_INACTIVITY_HOURS = 48
ABANDONMENT_DAYS = 14
BURNING_COST_THRESHOLD = 4

# Detector thresholds (spec §4)
LONGING_MIN_RECURRENCE = 3
LONGING_MIN_DAYS = 2
LONGING_MIN_MEAN_ABS_VALENCE = 0.2

LOVE_MIN_VALENCE = 0.4
LOVE_MIN_MENTIONS = 5

HOPE_MIN_SCORE = 0.5

WONDER_MIN_THINKING_RATIO = 1.5
WONDER_MIN_SUSTAINED_TURNS = 2

# Discharge detection (spec §7)
DISCHARGE_SIGNAL_THRESHOLD = 2
DISCHARGE_EVIDENCE_CESSATION_HOURS = 24

# Fruit affects (SOUL — used for discharge satisfaction signal)
FRUIT_AFFECTS = {"joy", "satisfaction", "awe", "relief", "clarity", "pride"}
