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


# ── Target matching (spec §3.5) ──────────────────────────────────────

_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "by",
    "with", "from", "is", "it", "and", "or", "but", "if", "then",
    "than", "that", "this", "these", "those",
}

_TOKEN_RE = None  # lazy init

def _normalize(s: str) -> set:
    """Lowercase, drop punctuation and stopwords, return token set."""
    import re
    global _TOKEN_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = re.compile(r"[a-z0-9]+")
    tokens = set(_TOKEN_RE.findall(s.lower()))
    return tokens - _STOPWORDS

def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity. Returns 0.0 when both sets empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)

_FUZZY_THRESHOLD = 0.7

def _same_target(a: dict, b: dict) -> bool:
    """Two targets match if: same kind AND (same key OR fuzzy display match)."""
    if a.get("kind") != b.get("kind"):
        return False
    if a.get("key") and a["key"] == b.get("key"):
        return True
    a_tokens = _normalize(a.get("display", ""))
    b_tokens = _normalize(b.get("display", ""))
    return _jaccard(a_tokens, b_tokens) >= _FUZZY_THRESHOLD


# ── Longing detector (spec §4.1) ─────────────────────────────────────

def _extract_targets_from_content(content: str) -> list:
    """
    Extract candidate noun-phrase targets from memory content.
    Very simple: match sequences of 2-5 non-stopword tokens.
    Returns list of {kind, key, display} dicts.
    """
    import re
    if not content:
        return []
    words = re.findall(r"[A-Za-z][A-Za-z0-9_']*", content)
    targets = []
    seen = set()
    n = len(words)
    # Sliding windows of size 2-3 that skip stopwords on the edges
    for size in (3, 2):
        for i in range(n - size + 1):
            window = words[i:i + size]
            if window[0].lower() in _STOPWORDS or window[-1].lower() in _STOPWORDS:
                continue
            phrase = " ".join(window).lower()
            key = "_".join(w.lower() for w in window)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"kind": "concept", "key": key, "display": phrase})
    return targets


def _parse_iso(s: str):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def detect_longing(memories: list, now_iso: str) -> list:
    """
    Detect the longing motor emotion from recurring targets in episodic memories.
    Spec §4.1.
    """
    if not memories:
        return []

    # Group memories by target (fuzzy: we use the 'key' for exact grouping here)
    target_occurrences = {}  # key -> list of (memory_id, created_at, valence, display)
    for mem in memories:
        content = mem.get("content", "")
        created_at = mem.get("created_at", "")
        valence = (mem.get("metadata") or {}).get("affect", {}).get("valence", 0.0)
        for t in _extract_targets_from_content(content):
            k = t["key"]
            target_occurrences.setdefault(k, []).append(
                (mem.get("id"), created_at, float(valence), t["display"])
            )

    candidates = []
    for key, occ_list in target_occurrences.items():
        if len(occ_list) < LONGING_MIN_RECURRENCE:
            continue

        # Count distinct days
        days = {c[1][:10] for c in occ_list if c[1]}
        if len(days) < LONGING_MIN_DAYS:
            continue

        # Mean absolute valence
        abs_valences = [abs(c[2]) for c in occ_list]
        mean_abs_val = sum(abs_valences) / len(abs_valences)
        if mean_abs_val < LONGING_MIN_MEAN_ABS_VALENCE:
            continue

        # Compute intensity
        recurrence_score = min(5, len(occ_list))  # cap at 5
        ache_hint = max(1, min(5, int(round(mean_abs_val * 5))))
        # gap_hint: hard to estimate without semantic understanding; default mid-range
        gap_hint = 3

        display = occ_list[0][3]
        evidence = [c[0] for c in occ_list if c[0]]

        candidates.append({
            "motor": "longing",
            "target": {"kind": "concept", "key": key, "display": display},
            "evidence": evidence,
            "gap_hint": gap_hint,
            "ache_hint": ache_hint,
        })

    return candidates
