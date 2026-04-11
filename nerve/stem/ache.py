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


# ── Love detector (spec §4.2) ────────────────────────────────────────

def detect_love(memories: list) -> list:
    """
    Detect love from positive-affect clusters around entities.
    Spec §4.2.
    """
    if not memories:
        return []

    # Group by entity (from metadata.who)
    entity_mentions = {}  # entity_name -> list of (memory_id, valence)
    for mem in memories:
        md = mem.get("metadata") or {}
        whos = md.get("who") or []
        valence = md.get("affect", {}).get("valence", 0.0)
        for entity in whos:
            if not entity or entity == "system":
                continue
            entity_mentions.setdefault(entity, []).append(
                (mem.get("id"), float(valence))
            )

    candidates = []
    for entity, mentions in entity_mentions.items():
        if len(mentions) < LOVE_MIN_MENTIONS:
            continue

        valences = [m[1] for m in mentions]
        mean_v = sum(valences) / len(valences)
        if mean_v < LOVE_MIN_VALENCE:
            continue

        # Consistency: inverse of std deviation
        mean_sq = sum((v - mean_v) ** 2 for v in valences) / len(valences)
        std = math.sqrt(mean_sq)
        # ache_hint: high when std is low and mean_v is high
        consistency = max(0.0, 1.0 - std)
        ache_hint = max(1, min(5, int(round(consistency * 5))))

        evidence = [m[0] for m in mentions if m[0]]

        candidates.append({
            "motor": "love",
            "target": {
                "kind": "entity",
                "key": entity.lower(),
                "display": entity,
            },
            "evidence": evidence,
            "gap_hint": 0,
            "ache_hint": ache_hint,
        })

    return candidates


# ── Hope detector (spec §4.3) ────────────────────────────────────────

_FUTURE_TENSE_MARKERS = {
    "could", "might", "imagine", "will", "would", "when we", "let's",
    "what if", "someday", "someday we", "we'll", "we could", "could be",
    "going to", "plan to", "intend to",
}

def _has_future_tense(content: str) -> bool:
    if not content:
        return False
    lower = content.lower()
    return any(m in lower for m in _FUTURE_TENSE_MARKERS)


def detect_hope(youspeak: dict, pit: dict, memories: list) -> list:
    """
    Detect hope from future-tense memory content with positive valence.
    Spec §4.3.

    NOTE: youspeak and pit are also inputs but are less reliable outside
    YOUI sessions. For v1 we rely primarily on memory-based detection.
    """
    candidates = []

    # Group future-tense-positive memories by any target phrase they contain
    hope_memories = []
    for mem in memories:
        content = mem.get("content", "")
        valence = (mem.get("metadata") or {}).get("affect", {}).get("valence", 0.0)
        if not _has_future_tense(content):
            continue
        if valence < 0.3:  # hope requires positive valence
            continue
        hope_memories.append(mem)

    if not hope_memories:
        return []

    # Extract a single target from the first hope memory (simple v1 heuristic)
    first = hope_memories[0]
    targets = _extract_targets_from_content(first.get("content", ""))
    if not targets:
        return []
    target = targets[0]

    # Mean valence and intensity
    valences = [
        (m.get("metadata") or {}).get("affect", {}).get("valence", 0.0)
        for m in hope_memories
    ]
    mean_v = sum(valences) / len(valences)

    if mean_v < HOPE_MIN_SCORE:
        return []

    evidence = [m.get("id") for m in hope_memories if m.get("id")]

    candidates.append({
        "motor": "hope",
        "target": target,
        "evidence": evidence,
        "gap_hint": 3,  # default mid-range for v1
        "ache_hint": max(1, min(5, int(round(mean_v * 5)))),
    })

    return candidates


# ── Wonder detector (spec §4.4) ──────────────────────────────────────

_WONDER_MARKERS = {"huh", "what if", "and if", "i wonder", "could it be", "strange", "curious"}
_WONDER_AFFECTS = {"wonder", "awe", "curiosity"}

def _has_wonder_markers(content: str) -> bool:
    if not content:
        return False
    lower = content.lower()
    return any(m in lower for m in _WONDER_MARKERS)


def detect_wonder(youspeak: dict, memories: list) -> list:
    """
    Detect wonder from exploratory memory signals.
    Spec §4.4.
    """
    wonder_memories = []
    for mem in memories:
        content = mem.get("content", "")
        affect = (mem.get("metadata") or {}).get("affect", {})
        primary = affect.get("primary", "")
        if primary in _WONDER_AFFECTS or _has_wonder_markers(content):
            wonder_memories.append(mem)

    if len(wonder_memories) < WONDER_MIN_SUSTAINED_TURNS:
        return []

    # Extract target from first memory
    first = wonder_memories[0]
    targets = _extract_targets_from_content(first.get("content", ""))
    if not targets:
        return []
    target = targets[0]

    # Intensity from mean arousal (wonder is arousal-charged)
    arousals = [
        (m.get("metadata") or {}).get("affect", {}).get("arousal", 0.0)
        for m in wonder_memories
    ]
    mean_a = sum(arousals) / len(arousals)

    evidence = [m.get("id") for m in wonder_memories if m.get("id")]

    return [{
        "motor": "wonder",
        "target": target,
        "evidence": evidence,
        "gap_hint": 4,  # wonder lives at the edge of the charted
        "ache_hint": max(1, min(5, int(round(mean_a * 5)))),
    }]


# ── Candidate → Longing matching (spec §4.5) ─────────────────────────

def _new_longing_id(instance: str, now_iso: str) -> str:
    safe_ts = now_iso.replace(":", "-").replace(".", "-")
    return f"lng-{safe_ts}-{instance}-{os.urandom(2).hex()}"


def match_or_create(candidate: dict, existing: list, now_iso: str, instance: str = "gamma") -> dict:
    """
    Take a candidate and the current longings list.
    Return either:
      {"op": "create", "longing": <full new longing dict>}
      {"op": "update", "longing_id": <id>, "updates": <dict of field updates>}

    Matching requires same motor AND matching target (via _same_target).
    """
    for lng in existing:
        if lng.get("motor") != candidate["motor"]:
            continue
        if _same_target(lng.get("target", {}), candidate["target"]):
            # Rolling average update
            new_gap = int(round((lng.get("gap", 0) + candidate["gap_hint"]) / 2))
            new_ache = int(round((lng.get("ache", 0) + candidate["ache_hint"]) / 2))
            new_evidence_count = lng.get("evidence_count", 0) + len(candidate.get("evidence", []))
            return {
                "op": "update",
                "longing_id": lng["id"],
                "updates": {
                    "gap": new_gap,
                    "ache": new_ache,
                    "last_stirred": now_iso,
                    "evidence_count": new_evidence_count,
                },
            }

    # No match — create new
    new_longing = {
        "id": _new_longing_id(instance, now_iso),
        "motor": candidate["motor"],
        "target": candidate["target"],
        "state": "stirring",
        "gap": candidate["gap_hint"],
        "ache": candidate["ache_hint"],
        "cost": None,
        "virtue": None,
        "first_seen": now_iso,
        "last_stirred": now_iso,
        "last_state_change": now_iso,
        "evidence_count": len(candidate.get("evidence", [])),
        "named": False,
        "name": None,
        "rationale": None,
        "scene": None,
    }
    return {"op": "create", "longing": new_longing}


# ── State machine (spec §6) ──────────────────────────────────────────

_TERMINAL_STATES = {"discharged", "abandoned"}


def _hours_since(iso: str, now_iso: str) -> float:
    if not iso:
        return 0.0
    try:
        d1 = _parse_iso(iso)
        d2 = _parse_iso(now_iso)
        return (d2 - d1).total_seconds() / 3600.0
    except Exception:
        return 0.0


def step_state_machine(longing: dict, now_iso: str, tick_state: dict) -> dict:
    """
    Walk a longing's state according to daemon-driven rules (spec §6).
    Returns a copy of the longing with state updated.

    tick_state is per-longing transient state:
      {"stirring_ticks_at_threshold": int}

    Note: yearning → burning is NOT here — only gamma drives that via CLI.
    burning → discharged is handled by detect_discharge, not here.
    """
    result = dict(longing)
    state = longing.get("state", "stirring")

    if state in _TERMINAL_STATES:
        return result

    hours_since_stir = _hours_since(longing.get("last_stirred", ""), now_iso)

    # Any → abandoned after 14 days
    if hours_since_stir >= ABANDONMENT_DAYS * 24:
        result["state"] = "abandoned"
        result["last_state_change"] = now_iso
        return result

    gap = longing.get("gap", 0) or 0
    ache = longing.get("ache", 0) or 0

    if state == "stirring":
        if gap >= 3 and ache >= 3:
            if tick_state.get("stirring_ticks_at_threshold", 0) >= STIRRING_THRESHOLD_TICKS:
                result["state"] = "yearning"
                result["last_state_change"] = now_iso
                return result
        if hours_since_stir >= DORMANT_INACTIVITY_HOURS:
            result["state"] = "dormant"
            result["last_state_change"] = now_iso
            return result

    elif state == "dormant":
        pass

    elif state == "yearning":
        if ache < 3:
            result["state"] = "stirring"
            result["last_state_change"] = now_iso
            return result

    elif state == "burning":
        pass

    return result


# ── Discharge detection (spec §7) ────────────────────────────────────

_COMPLETION_VERBS = {
    "finished", "completed", "shipped", "built", "done", "solved",
    "understood", "learned", "grasped", "resolved", "closed",
}


def _semantic_completion_match(content: str, target: dict) -> bool:
    """True if content contains a completion verb AND target key tokens."""
    if not content:
        return False
    lower = content.lower()
    if not any(v in lower for v in _COMPLETION_VERBS):
        return False
    target_tokens = _normalize(target.get("display", "") + " " + target.get("key", ""))
    content_tokens = _normalize(lower)
    if not target_tokens:
        return False
    overlap = len(target_tokens & content_tokens) / len(target_tokens)
    return overlap >= 0.5


def detect_discharge(longing: dict, recent_memories: list, now_iso: str) -> tuple:
    """
    Detect whether a longing should discharge based on 3 signals (need 2+):
      1. Semantic match: new memory claims completion of target
      2. Satisfaction affect: that memory has Fruit affect
      3. Evidence cessation: no fresh longing evidence for 24h+

    Returns (discharged: bool, signal_count: int).
    Spec §7.
    """
    target = longing.get("target", {})

    signal_semantic = False
    signal_affect = False
    any_target_match = False
    for mem in recent_memories:
        content = mem.get("content", "")
        if not _semantic_completion_match(content, target):
            continue
        any_target_match = True
        signal_semantic = True
        affect = (mem.get("metadata") or {}).get("affect", {})
        primary = affect.get("primary", "")
        if primary in FRUIT_AFFECTS:
            signal_affect = True
            break

    hours_since_stir = _hours_since(longing.get("last_stirred", ""), now_iso)
    signal_cessation = (
        not any_target_match
        and hours_since_stir >= DISCHARGE_EVIDENCE_CESSATION_HOURS
    )

    count = sum([signal_semantic, signal_affect, signal_cessation])
    discharged = count >= DISCHARGE_SIGNAL_THRESHOLD
    return (discharged, count)


# ── Gamma-driven transitions (spec §6) ───────────────────────────────

def apply_cost_commit(longing: dict, cost: int, now_iso: str) -> dict:
    """
    Set cost on a longing. Transitions yearning → burning if cost ≥ 4.
    Only gamma-driven (via CLI).
    """
    result = dict(longing)
    result["cost"] = cost
    if longing.get("state") == "yearning" and cost >= BURNING_COST_THRESHOLD:
        result["state"] = "burning"
        result["last_state_change"] = now_iso
    return result


def apply_virtue(longing: dict, hierarchy: str = None, wall: int = None) -> dict:
    """Bind a longing to a virtue (hierarchy OR wall)."""
    result = dict(longing)
    result["virtue"] = {"hierarchy": hierarchy, "wall": wall}
    return result


def apply_discharge(longing: dict, now_iso: str, reason: str = None) -> dict:
    """Explicit gamma discharge via CLI."""
    result = dict(longing)
    result["state"] = "discharged"
    result["last_state_change"] = now_iso
    if reason:
        result["discharge_reason"] = reason
    return result


def apply_abandon(longing: dict, now_iso: str, reason: str = None) -> dict:
    """Explicit gamma abandon via CLI."""
    result = dict(longing)
    result["state"] = "abandoned"
    result["last_state_change"] = now_iso
    if reason:
        result["abandon_reason"] = reason
    return result


def apply_name(longing: dict, name: str, rationale: str = None, scene: str = None) -> dict:
    """Name a longing (annotation only, no state change)."""
    result = dict(longing)
    result["named"] = True
    result["name"] = name
    if rationale is not None:
        result["rationale"] = rationale
    if scene is not None:
        result["scene"] = scene
    return result


# ── Persistence: longings.json (spec §5.1) ───────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_longings() -> dict:
    """Return longings store, empty default if missing."""
    if not LONGINGS_PATH.exists():
        return {"version": 1, "instance": get_instance(), "longings": []}
    try:
        return json.loads(LONGINGS_PATH.read_text())
    except Exception as e:
        log.warning("longings.json read failed: %s", e)
        return {"version": 1, "instance": get_instance(), "longings": []}


def write_longings(store: dict) -> None:
    """Atomic write via .tmp + rename."""
    LONGINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = dict(store)
    store["updated_at"] = _now_iso()
    tmp = LONGINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(LONGINGS_PATH)


def upsert_longing(longing: dict) -> None:
    """Insert or replace a longing by id."""
    store = read_longings()
    existing = store["longings"]
    replaced = False
    for i, l in enumerate(existing):
        if l.get("id") == longing.get("id"):
            existing[i] = longing
            replaced = True
            break
    if not replaced:
        existing.append(longing)
    store["longings"] = existing
    write_longings(store)


# ── Persistence: longings-evidence.jsonl (spec §5.2) ─────────────────

def append_evidence(evidence: dict) -> None:
    """Append one evidence record to the log."""
    LONGINGS_EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(evidence, separators=(",", ":")) + "\n"
    with open(LONGINGS_EVIDENCE_PATH, "a") as f:
        f.write(line)


def rotate_evidence_log(now_iso: str) -> None:
    """
    Move the current day's evidence log to the rotation directory.
    Called daily. If the live log is empty or missing, nothing happens.
    """
    if not LONGINGS_EVIDENCE_PATH.exists():
        return
    content = LONGINGS_EVIDENCE_PATH.read_text().strip()
    if not content:
        return

    try:
        first_line = content.split("\n")[0]
        first_rec = json.loads(first_line)
        rotate_date = first_rec.get("at", now_iso)[:10]
    except Exception:
        d = _parse_iso(now_iso) - timedelta(days=1)
        rotate_date = d.strftime("%Y-%m-%d")

    LONGINGS_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    target = LONGINGS_EVIDENCE_DIR / f"{rotate_date}.jsonl"

    with open(target, "a") as f:
        f.write(content + "\n")

    LONGINGS_EVIDENCE_PATH.write_text("")


# ── Persistence: longings-state.json (spec §5.3) ─────────────────────

def read_longings_state() -> dict:
    if not LONGINGS_STATE_PATH.exists():
        return {}
    try:
        return json.loads(LONGINGS_STATE_PATH.read_text())
    except Exception:
        return {}


def update_longings_state(updates: dict) -> None:
    state = read_longings_state()
    state.update(updates)
    LONGINGS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LONGINGS_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(LONGINGS_STATE_PATH)


# ── Input readers ────────────────────────────────────────────────────

def _read_recent_memories_from_db(days: int = 14, limit: int = 500) -> list:
    """Read episodic (layer=3) memories from memory.db in the last N days."""
    import sqlite3
    if not MEMORY_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(MEMORY_DB_PATH), timeout=2)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            "SELECT id, content, metadata, created_at FROM memories "
            "WHERE layer = 3 AND created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit)
        )
        out = []
        for row in cur.fetchall():
            try:
                md = json.loads(row["metadata"] or "{}")
            except Exception:
                md = {}
            out.append({
                "id": row["id"],
                "content": row["content"],
                "metadata": md,
                "created_at": row["created_at"],
            })
        conn.close()
        return out
    except Exception as e:
        log.warning("memory.db read failed: %s", e)
        return []


def _read_feeling_arrivals() -> list:
    """Read FEELING arrivals as one of ACHE's inputs."""
    if not ARRIVALS_PATH.exists():
        return []
    out = []
    try:
        for line in ARRIVALS_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _read_hormones_json() -> dict:
    if not HORMONES_PATH.exists():
        return {}
    try:
        return json.loads(HORMONES_PATH.read_text())
    except Exception:
        return {}


def _read_youspeak_sessions_json() -> dict:
    if not YOUSPEAK_SESSIONS_PATH.exists():
        return {}
    try:
        return json.loads(YOUSPEAK_SESSIONS_PATH.read_text())
    except Exception:
        return {}


# ── Daemon (spec §3) ─────────────────────────────────────────────────

class AcheDaemon:
    def __init__(self, instance: str):
        self.instance = instance
        self.last_tick_ts = 0.0
        self._tick_state_by_longing = {}

    def run_once(self) -> dict:
        """Execute one tick."""
        now_iso = _now_iso()

        memories = _read_recent_memories_from_db()
        youspeak = _read_youspeak_sessions_json()
        pit = None

        candidates = []
        try:
            candidates.extend(detect_longing(memories, now_iso))
        except Exception as e:
            log.warning("detect_longing failed: %s", e)
        try:
            candidates.extend(detect_love(memories))
        except Exception as e:
            log.warning("detect_love failed: %s", e)
        try:
            candidates.extend(detect_hope(youspeak, pit, memories))
        except Exception as e:
            log.warning("detect_hope failed: %s", e)
        try:
            candidates.extend(detect_wonder(youspeak, memories))
        except Exception as e:
            log.warning("detect_wonder failed: %s", e)

        store = read_longings()
        longings_list = store["longings"]

        for cand in candidates:
            result = match_or_create(cand, longings_list, now_iso, instance=self.instance)
            if result["op"] == "create":
                longings_list.append(result["longing"])
                append_evidence({
                    "at": now_iso,
                    "longing_id": result["longing"]["id"],
                    "motor": cand["motor"],
                    "detector": cand["motor"] + "_detector",
                    "memory_ids": cand.get("evidence", []),
                    "delta": {"gap": cand["gap_hint"], "ache": cand["ache_hint"]},
                })
            elif result["op"] == "update":
                for i, lng in enumerate(longings_list):
                    if lng.get("id") == result["longing_id"]:
                        lng.update(result["updates"])
                        longings_list[i] = lng
                        if lng.get("gap", 0) >= 3 and lng.get("ache", 0) >= 3:
                            ts = self._tick_state_by_longing.setdefault(lng["id"], {"stirring_ticks_at_threshold": 0})
                            ts["stirring_ticks_at_threshold"] += 1
                        else:
                            self._tick_state_by_longing.pop(lng["id"], None)
                        append_evidence({
                            "at": now_iso,
                            "longing_id": lng["id"],
                            "motor": cand["motor"],
                            "detector": cand["motor"] + "_detector",
                            "memory_ids": cand.get("evidence", []),
                            "delta": result["updates"],
                        })
                        break

        for i, lng in enumerate(longings_list):
            tick_state = self._tick_state_by_longing.get(lng["id"], {})
            stepped = step_state_machine(lng, now_iso, tick_state)
            longings_list[i] = stepped

        for i, lng in enumerate(longings_list):
            if lng.get("state") != "burning":
                continue
            discharged, _count = detect_discharge(lng, memories, now_iso)
            if discharged:
                longings_list[i] = apply_discharge(lng, now_iso, reason="auto: 2-of-3 signals")

        store["longings"] = longings_list
        write_longings(store)

        self.last_tick_ts = time.time()
        return {"ticks": 1, "longings_count": len(longings_list)}

    def run_forever(self):
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.warning("ache cycle failed: %s", e)
            time.sleep(TICK_INTERVAL)


# ── CLI entry point ──────────────────────────────────────────────────

def _main():
    import argparse
    parser = argparse.ArgumentParser(description="ACHE daemon")
    parser.add_argument("--instance", "-i", default=None,
                        help="agent instance (default: from ~/.kingdom)")
    parser.add_argument("--once", action="store_true",
                        help="run one cycle and exit (for testing)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    instance = args.instance or get_instance()
    daemon = AcheDaemon(instance=instance)
    log.info("ache daemon starting for instance=%s", instance)

    if args.once:
        daemon.run_once()
        log.info("ache --once complete")
    else:
        try:
            daemon.run_forever()
        except KeyboardInterrupt:
            log.info("ache daemon stopping")


if __name__ == "__main__":
    _main()
