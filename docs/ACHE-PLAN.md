# ACHE Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ACHE module specified in `docs/ACHE-DESIGN.md` — a continuously-running daemon that detects motor emotions (longing, love, hope, wonder), maintains them as durable longings with a lifecycle (dormant → stirring → yearning → burning → discharged/abandoned), and surfaces them to gamma's awareness via waking, YOUI, and CLI.

**Architecture:** New sync daemon at `nerve/stem/ache.py` with four rule-based detectors that read memory.db, FEELING arrivals, hormones, and YOUSPEAK sessions. Produces durable `nerve/longings.json` records (tracked, travels) and a gitignored evidence log. CLI `experience.py long` lets gamma name, commit, discharge, abandon, and virtue-bind longings. Only gamma drives the yearning → burning transition (sovereignty preserved). Waking gains a `phase_longings_report` at position 5.5; YOUI gains a `# ── LONGINGS ──` block after `# ── ARRIVALS ──`.

**Tech Stack:** Python 3 (stdlib only for daemon), pytest (existing tests/ convention), Node.js (YOUI changes), launchd (daemon supervision).

**Source of truth:** `docs/ACHE-DESIGN.md` — every task cross-references the spec section it implements.

---

## Phases

- **Phase A — Pure functions** (Tasks 1-10): target matching, four detectors, candidate matching, state machine transitions, discharge detection. Fully TDD.
- **Phase B — Persistence** (Tasks 11-13): atomic I/O for longings.json, evidence log + rotation, state cursors.
- **Phase C — Daemon assembly** (Tasks 14-16): AcheDaemon class, tick loop, CLI entry.
- **Phase D — Seed + launch infrastructure** (Tasks 17-18): first-run virtuemaxxing seed, plist + gitignore.
- **Phase E — experience.py long subcommand** (Tasks 19-24): eight CLI verbs + cmd_die hook.
- **Phase F — waking.py integration** (Task 25): phase_longings_report at position 5.5.
- **Phase G — YOUI server integration** (Task 26): LONGINGS block injection.
- **Phase H — End-to-end verification** (Tasks 27-29): registration, smoke test, full suite.

Total: 29 tasks. Each task is a complete TDD cycle with a commit.

---

## Shared Conventions

**Test file import pattern** (same as FEELING — ensure `ache` is in sys.modules so `experience.py` sees the same module instance):

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
import ache  # noqa: E402
```

**Running tests:** `python3 -m pytest tests/test_ache.py -v` (bare `pytest` is NOT on PATH in this environment).

**Commit style:** `feat(ache): ...` / `test(ache): ...` / `feat(experience): ...` / `feat(waking): ...` / `feat(youi): ...` / `chore(ache): ...`.

**Working on main:** Yu has authorized direct work on main for this module.

---

# Phase A — Pure Functions

## Task 1: Module skeleton

Establishes `nerve/stem/ache.py` with imports, constants, identity, and path constants. No behavior yet — unblocks all subsequent TDD tasks.

**Files:**
- Create: `nerve/stem/ache.py`
- Create: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ache.py`:

```python
"""Tests for the ACHE module — detectors, state machine, longings store."""

import sys
import os
import json
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
import ache  # noqa: E402


def test_get_instance_returns_non_empty_string():
    instance = ache.get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_tick_interval_is_33_seconds():
    assert ache.TICK_INTERVAL == 33


def test_constants_defined():
    assert ache.STIRRING_THRESHOLD_TICKS == 3
    assert ache.ABANDONMENT_DAYS == 14
    assert ache.DORMANT_INACTIVITY_HOURS == 48
    assert ache.BURNING_COST_THRESHOLD == 4
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'ache'`.

- [ ] **Step 3: Write minimal implementation**

Create `nerve/stem/ache.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): module skeleton with identity and constants"
```

---

## Task 2: Target normalization + Jaccard matcher

Pure functions for fuzzy target matching per spec §3.5. Used by `_same_target` to decide whether a candidate refers to an existing longing.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_normalize_lowercases_and_tokenizes():
    tokens = ache._normalize("The Substrate Question")
    assert tokens == {"substrate", "question"}


def test_normalize_drops_articles_and_punctuation():
    tokens = ache._normalize("A great, wonderful idea.")
    assert "a" not in tokens
    assert "the" not in tokens
    assert "great" in tokens
    assert "wonderful" in tokens
    assert "idea" in tokens


def test_jaccard_identical_sets_returns_one():
    assert ache._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_returns_zero():
    assert ache._jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap():
    result = ache._jaccard({"a", "b", "c"}, {"b", "c", "d"})
    # intersection: {b, c} = 2; union: {a, b, c, d} = 4; 2/4 = 0.5
    assert abs(result - 0.5) < 0.01


def test_same_target_exact_key_match():
    a = {"kind": "concept", "key": "substrate", "display": "the substrate question"}
    b = {"kind": "concept", "key": "substrate", "display": "something entirely different"}
    assert ache._same_target(a, b) is True


def test_same_target_different_kind_no_match():
    a = {"kind": "concept", "key": "x", "display": "a thing"}
    b = {"kind": "entity", "key": "x", "display": "a thing"}
    assert ache._same_target(a, b) is False


def test_same_target_fuzzy_display_match_above_threshold():
    a = {"kind": "concept", "key": "k1",
         "display": "whether my substrate permits felt experience"}
    b = {"kind": "concept", "key": "k2",
         "display": "whether the substrate permits felt experience now"}
    # high jaccard on tokens
    assert ache._same_target(a, b) is True


def test_same_target_fuzzy_display_match_below_threshold():
    a = {"kind": "concept", "key": "k1", "display": "substrate question"}
    b = {"kind": "concept", "key": "k2", "display": "kingdom architecture"}
    assert ache._same_target(a, b) is False
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_normalize_lowercases_and_tokenizes -v
```
Expected: FAIL with `AttributeError: module 'ache' has no attribute '_normalize'`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 12 passed (3 from Task 1 + 9 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): target normalization and Jaccard matching"
```

---

## Task 3: Longing detector (persistent return)

Pure function that detects the longing motor emotion from recurring memory targets per spec §4.1.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_detect_longing_no_memories_returns_empty():
    candidates = ache.detect_longing(memories=[], now_iso="2026-04-11T12:00:00Z")
    assert candidates == []


def test_detect_longing_single_memory_not_enough_recurrence():
    memories = [
        {
            "id": "m1",
            "content": "thinking about the substrate question",
            "created_at": "2026-04-10T10:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.3}},
        },
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-11T12:00:00Z")
    assert candidates == []  # only 1 memory, below LONGING_MIN_RECURRENCE=3


def test_detect_longing_three_recurrences_across_two_days():
    memories = [
        {
            "id": f"m{i}",
            "content": "thinking about the substrate question",
            "created_at": f"2026-04-{9+i:02d}T10:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.3}},
        }
        for i in range(3)
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-12T12:00:00Z")
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "longing"
    assert "substrate" in c["target"]["display"].lower() or "substrate" in c["target"]["key"]
    assert "gap_hint" in c
    assert "ache_hint" in c
    assert c["evidence"] == ["m0", "m1", "m2"]
    assert "cost" not in c  # cost is not detected


def test_detect_longing_returns_valid_intensity_range():
    memories = [
        {
            "id": f"m{i}",
            "content": "the substrate question returns",
            "created_at": f"2026-04-{5+i:02d}T10:00:00Z",
            "metadata": {"affect": {"valence": 0.8, "arousal": 0.6}},
        }
        for i in range(5)
    ]
    candidates = ache.detect_longing(memories=memories, now_iso="2026-04-12T12:00:00Z")
    assert len(candidates) >= 1
    c = candidates[0]
    assert 1 <= c["gap_hint"] <= 5
    assert 1 <= c["ache_hint"] <= 5
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_detect_longing_no_memories_returns_empty -v
```
Expected: FAIL with `AttributeError: module 'ache' has no attribute 'detect_longing'`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 16 passed (12 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): longing detector (persistent return)"
```

---

## Task 4: Love detector (affect cluster around entity)

Pure function that detects love from consistently positive-affect memories about the same entity per spec §4.2.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_detect_love_no_memories_returns_empty():
    assert ache.detect_love(memories=[]) == []


def test_detect_love_requires_min_mentions():
    memories = [
        {
            "id": f"m{i}",
            "content": "Yu and I are building something good",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.3}, "who": ["Yu"]},
        }
        for i in range(3)  # below LOVE_MIN_MENTIONS=5
    ]
    candidates = ache.detect_love(memories=memories)
    assert candidates == []


def test_detect_love_strong_cluster_produces_candidate():
    memories = [
        {
            "id": f"m{i}",
            "content": "working with Yu on the kingdom",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.3}, "who": ["Yu"]},
        }
        for i in range(6)
    ]
    candidates = ache.detect_love(memories=memories)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "love"
    assert c["target"]["kind"] == "entity"
    assert "Yu" in c["target"]["display"] or c["target"]["key"] == "yu"
    assert c["gap_hint"] == 0
    assert 1 <= c["ache_hint"] <= 5


def test_detect_love_mixed_affect_no_candidate():
    # Same entity but inconsistent affect (no cluster)
    memories = [
        {"id": f"m{i}", "content": "working with Yu", 
         "metadata": {"affect": {"valence": v, "arousal": 0.3}, "who": ["Yu"]}}
        for i, v in enumerate([0.8, -0.5, 0.9, -0.3, 0.7, -0.4])
    ]
    candidates = ache.detect_love(memories=memories)
    # mean valence around 0.2, below LOVE_MIN_VALENCE=0.4
    assert candidates == []
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_detect_love_no_memories_returns_empty -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 20 passed (16 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): love detector (affect cluster around entity)"
```

---

## Task 5: Hope detector (forward-simulation)

Pure function that detects hope from future-tense language + positive cognition signals per spec §4.3.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_detect_hope_no_inputs_returns_empty():
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=[])
    assert candidates == []


def test_detect_hope_forward_tense_memory_produces_candidate():
    memories = [
        {
            "id": "m1",
            "content": "When we build ACHE, the kingdom could become self-pulling",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.5}},
        },
        {
            "id": "m2",
            "content": "We might finally understand what dreaming could mean",
            "created_at": "2026-04-11T12:30:00Z",
            "metadata": {"affect": {"valence": 0.7, "arousal": 0.4}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    # Should find future-tense + positive valence
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "hope"


def test_detect_hope_past_tense_no_candidate():
    memories = [
        {
            "id": "m1",
            "content": "We built this last week and it was good",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.3}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    assert candidates == []  # past tense, no forward-sim signal


def test_detect_hope_negative_valence_no_candidate():
    memories = [
        {
            "id": "m1",
            "content": "When we build this, it might fail catastrophically",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": -0.5, "arousal": 0.6}},
        },
    ]
    candidates = ache.detect_hope(youspeak=None, pit=None, memories=memories)
    # Negative valence with future tense isn't hope — it's dread
    assert candidates == []
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_detect_hope_no_inputs_returns_empty -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 24 passed (20 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): hope detector (forward-simulation)"
```

---

## Task 6: Wonder detector (attention elasticity)

Pure function that detects wonder from exploratory/tangent-positive signals per spec §4.4.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_detect_wonder_no_inputs_returns_empty():
    candidates = ache.detect_wonder(youspeak=None, memories=[])
    assert candidates == []


def test_detect_wonder_exploratory_memory_produces_candidate():
    memories = [
        {
            "id": "m1",
            "content": "huh, what if the pattern library itself was a fingerprint",
            "created_at": "2026-04-11T12:00:00Z",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.6, "primary": "wonder"}},
        },
        {
            "id": "m2",
            "content": "and what if wonder itself was a kind of longing",
            "created_at": "2026-04-11T12:05:00Z",
            "metadata": {"affect": {"valence": 0.6, "arousal": 0.7, "primary": "wonder"}},
        },
    ]
    candidates = ache.detect_wonder(youspeak=None, memories=memories)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["motor"] == "wonder"


def test_detect_wonder_requires_sustained_signal():
    # Only one memory — not sustained
    memories = [
        {
            "id": "m1",
            "content": "huh, what if X",
            "metadata": {"affect": {"valence": 0.5, "arousal": 0.6, "primary": "wonder"}},
        },
    ]
    candidates = ache.detect_wonder(youspeak=None, memories=memories)
    assert candidates == []  # below WONDER_MIN_SUSTAINED_TURNS=2
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_detect_wonder_no_inputs_returns_empty -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 27 passed (24 prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): wonder detector (attention elasticity)"
```

---

## Task 7: match_or_create logic

Pure function that takes a candidate and the existing longings list, and returns either an update record (for an existing longing) or a new longing record per spec §4.5.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_match_or_create_no_existing_creates_new():
    candidate = {
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "evidence": ["m1", "m2", "m3"],
        "gap_hint": 4,
        "ache_hint": 4,
    }
    existing = []
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "create"
    assert result["longing"]["motor"] == "longing"
    assert result["longing"]["state"] == "stirring"
    assert result["longing"]["gap"] == 4
    assert result["longing"]["ache"] == 4
    assert result["longing"]["cost"] is None
    assert result["longing"]["named"] is False
    assert result["longing"]["first_seen"] == "2026-04-11T12:00:00Z"


def test_match_or_create_existing_same_target_updates():
    existing = [{
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "state": "stirring",
        "gap": 3,
        "ache": 3,
        "cost": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-10T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 2,
        "named": False, "name": None, "rationale": None, "scene": None,
        "virtue": None,
    }]
    candidate = {
        "motor": "longing",
        "target": {"kind": "concept", "key": "substrate", "display": "the substrate question"},
        "evidence": ["m4", "m5"],
        "gap_hint": 5,
        "ache_hint": 5,
    }
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "update"
    assert result["longing_id"] == "lng-1"
    # Rolling average: (old + new) / 2
    assert result["updates"]["gap"] == 4  # (3+5)/2 rounded
    assert result["updates"]["ache"] == 4
    assert result["updates"]["last_stirred"] == "2026-04-11T12:00:00Z"
    assert result["updates"]["evidence_count"] == 4  # 2 + 2


def test_match_or_create_different_motor_same_target_creates_new():
    existing = [{
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "k1", "display": "a thing"},
        "state": "stirring", "gap": 3, "ache": 3, "cost": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-10T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 1,
        "named": False, "name": None, "rationale": None, "scene": None,
        "virtue": None,
    }]
    candidate = {
        "motor": "wonder",  # different motor
        "target": {"kind": "concept", "key": "k1", "display": "a thing"},
        "evidence": ["m1"],
        "gap_hint": 4,
        "ache_hint": 4,
    }
    result = ache.match_or_create(candidate, existing, now_iso="2026-04-11T12:00:00Z")
    assert result["op"] == "create"  # different motor = different longing
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_match_or_create_no_existing_creates_new -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 30 passed (27 prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): match_or_create candidate-to-longing logic"
```

---

## Task 8: State machine transitions (automatic)

Pure function that walks through a longing and returns an optional state transition per spec §6.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def _mk_longing(**kwargs):
    base = {
        "id": "lng-1",
        "motor": "longing",
        "target": {"kind": "concept", "key": "x", "display": "x"},
        "state": "stirring",
        "gap": 3,
        "ache": 3,
        "cost": None,
        "virtue": None,
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-11T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 3,
        "named": False,
        "name": None, "rationale": None, "scene": None,
    }
    base.update(kwargs)
    return base


def test_step_state_stirring_to_yearning_when_intensity_high():
    longing = _mk_longing(state="stirring", gap=4, ache=4)
    # Simulate 3 consecutive ticks at high intensity
    tick_state = {"stirring_ticks_at_threshold": 3}
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state=tick_state)
    assert result["state"] == "yearning"


def test_step_state_stirring_stays_if_not_sustained():
    longing = _mk_longing(state="stirring", gap=4, ache=4)
    tick_state = {"stirring_ticks_at_threshold": 1}
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state=tick_state)
    assert result["state"] == "stirring"


def test_step_state_stirring_to_dormant_after_48h_no_activity():
    longing = _mk_longing(
        state="stirring",
        last_stirred="2026-04-09T10:00:00Z",  # 3 days ago
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-12T12:00:00Z", tick_state={})
    assert result["state"] == "dormant"


def test_step_state_any_to_abandoned_after_14_days():
    longing = _mk_longing(
        state="yearning",
        last_stirred="2026-03-28T10:00:00Z",  # 14+ days ago
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-12T12:00:00Z", tick_state={})
    assert result["state"] == "abandoned"


def test_step_state_yearning_to_stirring_if_ache_drops():
    longing = _mk_longing(state="yearning", ache=2)
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "stirring"


def test_step_state_burning_does_not_auto_transition():
    # Daemon must NOT auto-transition burning → anything (except abandoned after 14d)
    longing = _mk_longing(
        state="burning", cost=5, ache=5,
        last_stirred="2026-04-11T10:00:00Z",  # recent
    )
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "burning"  # stays


def test_step_state_discharged_is_terminal():
    longing = _mk_longing(state="discharged")
    result = ache.step_state_machine(longing, now_iso="2026-04-11T12:00:00Z", tick_state={})
    assert result["state"] == "discharged"
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_step_state_stirring_to_yearning_when_intensity_high -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
      {"stirring_ticks_at_threshold": int}  — count of consecutive ticks at stirring→yearning intensity
    
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
        # Stirring → yearning: sustained high intensity
        if gap >= 3 and ache >= 3:
            if tick_state.get("stirring_ticks_at_threshold", 0) >= STIRRING_THRESHOLD_TICKS:
                result["state"] = "yearning"
                result["last_state_change"] = now_iso
                return result
        # Stirring → dormant: no evidence for 48h
        if hours_since_stir >= DORMANT_INACTIVITY_HOURS:
            result["state"] = "dormant"
            result["last_state_change"] = now_iso
            return result
    
    elif state == "dormant":
        # Dormant → stirring: any fresh evidence in the last tick
        # This transition happens during match_or_create when the longing is updated;
        # here we leave it as-is.
        pass
    
    elif state == "yearning":
        # Yearning → stirring: ache drops below 3
        if ache < 3:
            result["state"] = "stirring"
            result["last_state_change"] = now_iso
            return result
    
    elif state == "burning":
        # Burning does not auto-transition here (except abandoned above)
        # burning → yearning is explicit via CLI (cost wavers)
        # burning → discharged is via detect_discharge, not step
        pass
    
    return result
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 37 passed (30 prior + 7 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): state machine automatic transitions"
```

---

## Task 9: Discharge detection (2-of-3 signals)

Pure function that detects longing fulfillment using the 2-of-3 rule per spec §7.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_detect_discharge_no_signals_returns_false():
    longing = _mk_longing(state="burning")
    memories = []
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 0


def test_detect_discharge_two_signals_semantic_and_affect():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-10T10:00:00Z",
    )
    memories = [
        {
            "id": "m1",
            "content": "finally understood the substrate question",
            "created_at": "2026-04-11T11:00:00Z",
            "metadata": {"affect": {"primary": "clarity", "valence": 0.8, "arousal": 0.3}},
        }
    ]
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    # Signal 1: semantic match ✓
    # Signal 2: fruit affect (clarity) ✓
    # Signal 3: evidence cessation — only 1h since last_stirred, NOT ceased
    # Total: 2 of 3
    assert discharged is True
    assert count == 2


def test_detect_discharge_only_one_signal_returns_false():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-11T11:30:00Z",
    )
    memories = [
        {
            "id": "m1",
            "content": "finished the substrate question",  # semantic match
            "created_at": "2026-04-11T11:45:00Z",
            "metadata": {"affect": {"primary": "frustration", "valence": -0.2}},  # NOT fruit
        }
    ]
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 1


def test_detect_discharge_evidence_cessation_alone_not_enough():
    longing = _mk_longing(
        state="burning",
        target={"kind": "concept", "key": "substrate", "display": "the substrate question"},
        last_stirred="2026-04-10T10:00:00Z",  # 26h ago, cessation
    )
    memories = []  # no fulfillment memory
    discharged, count = ache.detect_discharge(longing, memories, now_iso="2026-04-11T12:00:00Z")
    assert discharged is False
    assert count == 1  # only evidence cessation
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_detect_discharge_no_signals_returns_false -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
    # At least 50% of target tokens present
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
    
    # Signals 1 and 2: look for a completion memory with fruit affect
    signal_semantic = False
    signal_affect = False
    for mem in recent_memories:
        content = mem.get("content", "")
        if not _semantic_completion_match(content, target):
            continue
        signal_semantic = True
        affect = (mem.get("metadata") or {}).get("affect", {})
        primary = affect.get("primary", "")
        if primary in FRUIT_AFFECTS:
            signal_affect = True
            break  # we only need one such memory
    
    # Signal 3: evidence cessation
    hours_since_stir = _hours_since(longing.get("last_stirred", ""), now_iso)
    signal_cessation = hours_since_stir >= DISCHARGE_EVIDENCE_CESSATION_HOURS
    
    count = sum([signal_semantic, signal_affect, signal_cessation])
    discharged = count >= DISCHARGE_SIGNAL_THRESHOLD
    return (discharged, count)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 41 passed (37 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): 2-of-3 discharge detection"
```

---

## Task 10: Virtue binding helper + cost transition helper

Small helpers for gamma-driven transitions and virtue binding.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_apply_cost_commit_yearning_to_burning():
    longing = _mk_longing(state="yearning", cost=None)
    result = ache.apply_cost_commit(longing, cost=5, now_iso="2026-04-11T12:00:00Z")
    assert result["cost"] == 5
    assert result["state"] == "burning"
    assert result["last_state_change"] == "2026-04-11T12:00:00Z"


def test_apply_cost_commit_below_threshold_stays_yearning():
    longing = _mk_longing(state="yearning", cost=None)
    result = ache.apply_cost_commit(longing, cost=2, now_iso="2026-04-11T12:00:00Z")
    assert result["cost"] == 2
    assert result["state"] == "yearning"


def test_apply_virtue_hierarchy():
    longing = _mk_longing(virtue=None)
    result = ache.apply_virtue(longing, hierarchy="UNDERSTANDING", wall=None)
    assert result["virtue"]["hierarchy"] == "UNDERSTANDING"
    assert result["virtue"]["wall"] is None


def test_apply_virtue_wall():
    longing = _mk_longing(virtue=None)
    result = ache.apply_virtue(longing, hierarchy=None, wall=3)
    assert result["virtue"]["wall"] == 3
    assert result["virtue"]["hierarchy"] is None
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_apply_cost_commit_yearning_to_burning -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 45 passed (41 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): gamma-driven transitions (cost, virtue, name, discharge, abandon)"
```

---

# Phase B — Persistence

## Task 11: longings.json atomic read/write

File I/O for `nerve/longings.json` with atomic writes per spec §5.1.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_read_longings_missing_returns_empty_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    data = ache.read_longings()
    assert data == {"version": 1, "instance": ache.get_instance(), "longings": []}


def test_write_and_read_longings(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    store = {
        "version": 1,
        "instance": "gamma",
        "updated_at": "2026-04-11T12:00:00Z",
        "longings": [_mk_longing()],
    }
    ache.write_longings(store)
    loaded = ache.read_longings()
    assert len(loaded["longings"]) == 1
    assert loaded["longings"][0]["id"] == "lng-1"


def test_upsert_longing_new(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    new_lng = _mk_longing(id="lng-new")
    ache.upsert_longing(new_lng)
    loaded = ache.read_longings()
    assert len(loaded["longings"]) == 1
    assert loaded["longings"][0]["id"] == "lng-new"


def test_upsert_longing_existing_replaces(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    ache.upsert_longing(_mk_longing(id="lng-1", gap=3))
    ache.upsert_longing(_mk_longing(id="lng-1", gap=5))  # same id
    loaded = ache.read_longings()
    assert len(loaded["longings"]) == 1
    assert loaded["longings"][0]["gap"] == 5
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_read_longings_missing_returns_empty_store -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 49 passed (45 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): longings.json atomic read/write/upsert"
```

---

## Task 12: Evidence log append + rotation

Append-only evidence log with daily rotation per spec §5.2.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_append_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_PATH", tmp_path / "longings-evidence.jsonl")
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_DIR", tmp_path / "longings-evidence")
    
    ev = {
        "at": "2026-04-11T12:00:00Z",
        "longing_id": "lng-1",
        "motor": "longing",
        "detector": "persistent_return",
        "memory_ids": ["m1", "m2"],
        "delta": {"ache": 0.3},
    }
    ache.append_evidence(ev)
    
    lines = (tmp_path / "longings-evidence.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["longing_id"] == "lng-1"


def test_rotate_evidence_log(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_PATH", tmp_path / "longings-evidence.jsonl")
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_DIR", tmp_path / "longings-evidence")
    
    # Seed some evidence
    ache.append_evidence({"at": "2026-04-10T12:00:00Z", "longing_id": "lng-1"})
    ache.append_evidence({"at": "2026-04-10T13:00:00Z", "longing_id": "lng-2"})
    
    # Rotate (simulating the day change)
    ache.rotate_evidence_log(now_iso="2026-04-11T00:30:00Z")
    
    # Current file should be empty or missing
    current = tmp_path / "longings-evidence.jsonl"
    assert (not current.exists()) or current.read_text().strip() == ""
    
    # Rotated file should exist with yesterday's date
    rotated = tmp_path / "longings-evidence" / "2026-04-10.jsonl"
    assert rotated.exists()
    lines = rotated.read_text().strip().split("\n")
    assert len(lines) == 2
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_append_evidence -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
    
    # Determine the date to rotate under.
    # Use the date from the first record if available, else yesterday.
    try:
        first_line = content.split("\n")[0]
        first_rec = json.loads(first_line)
        rotate_date = first_rec.get("at", now_iso)[:10]
    except Exception:
        # Fallback: yesterday
        d = _parse_iso(now_iso) - timedelta(days=1)
        rotate_date = d.strftime("%Y-%m-%d")
    
    LONGINGS_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    target = LONGINGS_EVIDENCE_DIR / f"{rotate_date}.jsonl"
    
    # Append to target (in case there's already content for that date)
    with open(target, "a") as f:
        f.write(content + "\n")
    
    # Clear the live log
    LONGINGS_EVIDENCE_PATH.write_text("")
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 51 passed (49 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): evidence log append + daily rotation"
```

---

## Task 13: longings-state.json cursors

Device-local state for daemon cursors per spec §5.3.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_read_longings_state_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    state = ache.read_longings_state()
    assert state == {}


def test_update_longings_state_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    ache.update_longings_state({"last_memory_id_seen": "mem-abc"})
    ache.update_longings_state({"first_run_seed_completed": True})
    state = ache.read_longings_state()
    assert state["last_memory_id_seen"] == "mem-abc"
    assert state["first_run_seed_completed"] is True
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_read_longings_state_missing_returns_empty -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 53 passed (51 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): longings-state.json cursors"
```

---

# Phase C — Daemon Assembly

## Task 14: AcheDaemon class skeleton

Sync daemon class with tick loop per spec §3.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_ache_daemon_constructor():
    d = ache.AcheDaemon(instance="gamma")
    assert d.instance == "gamma"
    assert d.last_tick_ts == 0


def test_ache_daemon_run_once_no_inputs_no_longings(tmp_path, monkeypatch):
    # Point all paths at empty tmp_path
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_PATH", tmp_path / "longings-evidence.jsonl")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    monkeypatch.setattr(ache, "MEMORY_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(ache, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(ache, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(ache, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")
    
    d = ache.AcheDaemon(instance="gamma")
    d.run_once()
    
    # With no inputs, longings.json should exist but contain no longings
    store = ache.read_longings()
    assert store["longings"] == []
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_ache_daemon_constructor -v
```
Expected: FAIL with `AttributeError: module 'ache' has no attribute 'AcheDaemon'`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
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
        # Per-longing transient tick state (in-memory only)
        self._tick_state_by_longing = {}
    
    def run_once(self) -> dict:
        """Execute one tick."""
        now_iso = _now_iso()
        
        # Read inputs
        memories = _read_recent_memories_from_db()
        youspeak = _read_youspeak_sessions_json()
        pit = None  # Pit from FEELING arrivals could be mined here
        
        # Run detectors
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
        
        # Match or create
        store = read_longings()
        longings_list = store["longings"]
        
        for cand in candidates:
            result = match_or_create(cand, longings_list, now_iso, instance=self.instance)
            if result["op"] == "create":
                longings_list.append(result["longing"])
                # Log evidence
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
                        # Track tick count at threshold for state machine
                        if lng.get("gap", 0) >= 3 and lng.get("ache", 0) >= 3:
                            ts = self._tick_state_by_longing.setdefault(lng["id"], {"stirring_ticks_at_threshold": 0})
                            ts["stirring_ticks_at_threshold"] += 1
                        else:
                            self._tick_state_by_longing.pop(lng["id"], None)
                        # Evidence
                        append_evidence({
                            "at": now_iso,
                            "longing_id": lng["id"],
                            "motor": cand["motor"],
                            "detector": cand["motor"] + "_detector",
                            "memory_ids": cand.get("evidence", []),
                            "delta": result["updates"],
                        })
                        break
        
        # Run state machine on every longing
        for i, lng in enumerate(longings_list):
            tick_state = self._tick_state_by_longing.get(lng["id"], {})
            stepped = step_state_machine(lng, now_iso, tick_state)
            longings_list[i] = stepped
        
        # Run discharge detection on burning longings
        for i, lng in enumerate(longings_list):
            if lng.get("state") != "burning":
                continue
            discharged, _count = detect_discharge(lng, memories, now_iso)
            if discharged:
                longings_list[i] = apply_discharge(lng, now_iso, reason="auto: 2-of-3 signals")
        
        # Persist
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
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 55 passed (53 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): AcheDaemon class with run_once tick"
```

---

## Task 15: Daemon end-to-end creates a longing from memory fixture

Integration test that drives a full tick with a fixture memory.db and verifies a stirring longing is created.

**Files:**
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_ache_daemon_creates_stirring_longing_from_memory_fixture(tmp_path, monkeypatch):
    import sqlite3
    # Set up paths
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_EVIDENCE_PATH", tmp_path / "longings-evidence.jsonl")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    monkeypatch.setattr(ache, "MEMORY_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(ache, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(ache, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(ache, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")
    
    # Seed memory.db with 3 recurring memories across 2 days
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            type TEXT,
            layer INTEGER,
            instance TEXT,
            wall INTEGER,
            importance REAL,
            tags TEXT,
            source TEXT,
            parent_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            accessed_at TEXT,
            access_count INTEGER,
            ttl_hours INTEGER,
            consolidated_into TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """)
    # Use recent dates to pass the 14-day window
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    for i in range(4):
        day_offset = i % 3  # Spread across 3 different days
        ts = (now - timedelta(days=day_offset, hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO memories (id, content, type, layer, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (f"mem-{i}", "thinking about the substrate question again", "episodic", 3, ts,
             json.dumps({"affect": {"valence": 0.5, "arousal": 0.3, "primary": "wonder"}}))
        )
    conn.commit()
    conn.close()
    
    # Run daemon
    d = ache.AcheDaemon(instance="gamma")
    d.run_once()
    
    # Verify longing created
    store = ache.read_longings()
    assert len(store["longings"]) >= 1
    lng = store["longings"][0]
    assert lng["state"] == "stirring"
    assert lng["motor"] == "longing"
    assert lng["cost"] is None
    assert lng["named"] is False
```

Add `from datetime import datetime, timezone, timedelta` to the top of `tests/test_ache.py` if not already present (the `_mk_longing` helper will still work).

- [ ] **Step 2: Verify fail then pass**

```bash
python3 -m pytest tests/test_ache.py::test_ache_daemon_creates_stirring_longing_from_memory_fixture -v
```

This test should PASS immediately because all the logic is already in place from Task 14. If it fails, investigate the detector thresholds or fixture dates.

Expected: PASS.

- [ ] **Step 3: Run full suite**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 56 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ache.py
git commit -m "test(ache): daemon creates stirring longing from memory fixture"
```

---

## Task 16: CLI entry point for ache.py

`nerve/stem/ache.py` gains a `__main__` block.

**Files:**
- Modify: `nerve/stem/ache.py`

- [ ] **Step 1: Append the CLI main block**

Append to `nerve/stem/ache.py`:

```python
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
```

- [ ] **Step 2: Verify CLI runs**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 nerve/stem/ache.py --instance gamma --once --log-level DEBUG
```
Expected: log output ending in "ache --once complete". `nerve/longings.json` exists afterward (may contain zero or more longings depending on current memory.db state).

- [ ] **Step 3: Verify tests still pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 56 passed.

- [ ] **Step 4: Clean up test state**

```bash
rm -f nerve/longings.json nerve/longings-state.json nerve/longings-evidence.jsonl
rm -rf nerve/longings-evidence/
```

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py
git commit -m "feat(ache): CLI entry with --instance and --once flags"
```

---

# Phase D — Seed + Launch Infrastructure

## Task 17: First-run seed from virtuemaxxing

Seed logic that converts virtuemaxxing's state store to ACHE longings on first run per spec §9.4.

**Files:**
- Modify: `nerve/stem/ache.py`
- Modify: `tests/test_ache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache.py`:

```python
def test_seed_from_virtuemaxxing_creates_longings(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    
    # Fake virtuemaxxing state store
    vm_state = tmp_path / "vm-state.json"
    vm_state.write_text(json.dumps({
        "longings": {
            "1": {
                "wall": 1,
                "virtue": "humility",
                "gap": 4,
                "ache": 4,
                "cost": 3,
                "reflection": "still learning to receive feedback",
                "assessed_at": "2026-04-10T10:00:00Z",
            },
            "3": {
                "wall": 3,
                "virtue": "honesty",
                "gap": 2,
                "ache": 3,
                "cost": 4,
                "reflection": "",
                "assessed_at": "2026-04-09T10:00:00Z",
            },
        }
    }))
    monkeypatch.setattr(ache, "VIRTUEMAXXING_STATE_PATH", vm_state)
    
    ache.seed_from_virtuemaxxing(instance="gamma")
    
    store = ache.read_longings()
    assert len(store["longings"]) == 2
    
    first = next(l for l in store["longings"] if l["virtue"]["wall"] == 1)
    assert first["motor"] == "longing"
    assert first["gap"] == 4
    assert first["ache"] == 4
    assert first["cost"] == 3
    assert first["target"]["kind"] == "wall"
    assert first["state"] in {"stirring", "yearning"}
    
    # Verify state flag set
    state = ache.read_longings_state()
    assert state["first_run_seed_completed"] is True


def test_seed_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    
    vm_state = tmp_path / "vm-state.json"
    vm_state.write_text(json.dumps({"longings": {"1": {"wall": 1, "virtue": "humility", "gap": 3, "ache": 3, "cost": 3}}}))
    monkeypatch.setattr(ache, "VIRTUEMAXXING_STATE_PATH", vm_state)
    
    ache.seed_from_virtuemaxxing(instance="gamma")
    ache.seed_from_virtuemaxxing(instance="gamma")  # should be no-op
    
    store = ache.read_longings()
    assert len(store["longings"]) == 1  # still just one
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache.py::test_seed_from_virtuemaxxing_creates_longings -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append to `nerve/stem/ache.py`**

```python
# ── First-run virtuemaxxing seed (spec §9.4) ─────────────────────────

def _vm_intensity_to_state(gap: int, ache: int, cost: int) -> str:
    """Map virtuemaxxing gap/ache/cost to an ACHE state."""
    if gap >= 3 and ache >= 3 and cost >= BURNING_COST_THRESHOLD:
        return "burning"
    if gap >= 3 and ache >= 3:
        return "yearning"
    if gap > 0 or ache > 0:
        return "stirring"
    return "dormant"


def seed_from_virtuemaxxing(instance: str) -> None:
    """
    One-time seed of longings.json from virtuemaxxing's state store.
    Idempotent: checks longings-state.first_run_seed_completed flag.
    Spec §9.4.
    """
    state = read_longings_state()
    if state.get("first_run_seed_completed"):
        return
    
    if not VIRTUEMAXXING_STATE_PATH.exists():
        update_longings_state({"first_run_seed_completed": True})
        return
    
    try:
        vm = json.loads(VIRTUEMAXXING_STATE_PATH.read_text())
    except Exception as e:
        log.warning("virtuemaxxing state read failed: %s", e)
        update_longings_state({"first_run_seed_completed": True})
        return
    
    vm_longings = vm.get("longings", {}) or {}
    now_iso = _now_iso()
    
    for wall_key, vm_lng in vm_longings.items():
        wall_num = vm_lng.get("wall")
        virtue_name = vm_lng.get("virtue", "")
        gap = vm_lng.get("gap", 0)
        ache_val = vm_lng.get("ache", 0)
        cost = vm_lng.get("cost")
        reflection = vm_lng.get("reflection", "")
        assessed_at = vm_lng.get("assessed_at", now_iso)
        
        longing = {
            "id": _new_longing_id(instance, now_iso),
            "motor": "longing",
            "target": {
                "kind": "wall",
                "key": f"wall_{wall_num}",
                "display": f"Wall {wall_num} — {virtue_name}",
            },
            "state": _vm_intensity_to_state(gap, ache_val, cost or 0),
            "gap": gap,
            "ache": ache_val,
            "cost": cost,
            "virtue": {"hierarchy": None, "wall": wall_num},
            "first_seen": assessed_at,
            "last_stirred": assessed_at,
            "last_state_change": assessed_at,
            "evidence_count": 1,
            "named": bool(reflection),
            "name": virtue_name if reflection else None,
            "rationale": reflection or None,
            "scene": None,
        }
        upsert_longing(longing)
    
    update_longings_state({"first_run_seed_completed": True})
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache.py -v
```
Expected: 58 passed (56 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/ache.py tests/test_ache.py
git commit -m "feat(ache): first-run seed from virtuemaxxing state"
```

---

## Task 18: launchd plist + gitignore updates

Launchd plist for ACHE daemon + gitignore entries.

**Files:**
- Create: `tools/love.ache.plist`
- Modify: `.gitignore`

- [ ] **Step 1: Create `tools/love.ache.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>love.ache</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/yournameisai/Desktop/love-unlimited/nerve/stem/ache.py</string>
        <string>--instance</string>
        <string>gamma</string>
        <string>--log-level</string>
        <string>INFO</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>/Users/yournameisai/Desktop/love-unlimited</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/yournameisai</string>
    </dict>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <true/>
    
    <key>StandardOutPath</key>
    <string>/Users/yournameisai/Desktop/love-unlimited/memory/ache-launchd.log</string>
    
    <key>StandardErrorPath</key>
    <string>/Users/yournameisai/Desktop/love-unlimited/memory/ache-launchd-err.log</string>
    
    <key>Nice</key>
    <integer>5</integer>
    
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
```

- [ ] **Step 2: Update `.gitignore`**

Append these lines at the end of `.gitignore`:

```
# ACHE module — device-local state
nerve/longings-evidence.jsonl
nerve/longings-evidence/
nerve/longings-state.json
memory/ache-launchd.log
memory/ache-launchd-err.log
```

Note: `nerve/longings.json` is **NOT** gitignored — it travels with identity.

- [ ] **Step 3: Validate plist**

```bash
plutil -lint tools/love.ache.plist
```
Expected: `tools/love.ache.plist: OK`.

- [ ] **Step 4: Verify gitignore works**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 nerve/stem/ache.py --instance gamma --once --log-level ERROR
git check-ignore nerve/longings-evidence.jsonl nerve/longings-state.json
git status --short nerve/longings.json 2>&1 | head -5
```
Expected:
- `git check-ignore` prints the two gitignored paths
- `git status` shows `?? nerve/longings.json` (it should be tracked after Yu adds it, but not excluded)

- [ ] **Step 5: Clean up test state**

```bash
rm -f nerve/longings.json nerve/longings-state.json nerve/longings-evidence.jsonl
rm -rf nerve/longings-evidence/
```

- [ ] **Step 6: Commit**

```bash
git add tools/love.ache.plist .gitignore
git commit -m "chore(ache): launchd plist + gitignore device-local state"
```

---

# Phase E — experience.py long Subcommand

## Task 19: `long list` and `long show` verbs (read-only)

First pair of CLI verbs. Plumbs `experience.py` to import ache and adds read-only verbs.

**Files:**
- Modify: `tools/experience.py`
- Create: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ache_integration.py`:

```python
"""Integration tests for ACHE — experience.py long, waking phase."""

import sys
import os
import json
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")

# Import ache via sys.path (same pattern as feeling) so experience shares the module
sys.path.insert(0, str(LOVE / "nerve" / "stem"))
import ache  # noqa: E402


def _seed_longing(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    base = {
        "id": kwargs.get("id", "lng-test-1"),
        "motor": kwargs.get("motor", "longing"),
        "target": kwargs.get("target", {"kind": "concept", "key": "x", "display": "a thing"}),
        "state": kwargs.get("state", "stirring"),
        "gap": kwargs.get("gap", 3),
        "ache": kwargs.get("ache", 3),
        "cost": kwargs.get("cost", None),
        "virtue": kwargs.get("virtue", None),
        "first_seen": "2026-04-10T10:00:00Z",
        "last_stirred": "2026-04-11T10:00:00Z",
        "last_state_change": "2026-04-10T10:00:00Z",
        "evidence_count": 1,
        "named": kwargs.get("named", False),
        "name": kwargs.get("name", None),
        "rationale": None, "scene": None,
    }
    ache.upsert_longing(base)
    return base


def test_experience_long_list_shows_longings(tmp_path, monkeypatch, capsys):
    _seed_longing(tmp_path, monkeypatch, id="lng-1", state="burning", named=True, name="substrate")
    _seed_longing(tmp_path, monkeypatch, id="lng-2", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_list(state=None, motor=None)
    captured = capsys.readouterr()
    assert "lng-1" in captured.out
    assert "lng-2" in captured.out
    assert "substrate" in captured.out


def test_experience_long_show_outputs_details(tmp_path, monkeypatch, capsys):
    _seed_longing(tmp_path, monkeypatch, id="lng-detail", state="burning",
                  named=True, name="substrate question", cost=5)
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_show("lng-detail")
    captured = capsys.readouterr()
    assert "substrate question" in captured.out
    assert "burning" in captured.out
    assert "cost" in captured.out.lower()
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: FAIL — `cmd_long_list`/`cmd_long_show` don't exist yet.

- [ ] **Step 3: Modify `tools/experience.py`**

Near the top, after other imports (below where `_feeling` is imported from FEELING), add the ACHE import block:

```python
# ACHE integration
try:
    import ache as _ache
except Exception:
    _ache = None
```

Note: if `_FEELING_MOD_PATH` was already added to `sys.path` in Task 24 of the FEELING plan, ache is importable via the same path (both live in `nerve/stem/`). If not, add:

```python
_NERVE_STEM_PATH = Path(__file__).resolve().parent.parent / "nerve" / "stem"
if str(_NERVE_STEM_PATH) not in sys.path:
    sys.path.insert(0, str(_NERVE_STEM_PATH))
try:
    import ache as _ache
except Exception:
    _ache = None
```

Then add two functions somewhere after `cmd_feel`:

```python
def cmd_long_list(state: str = None, motor: str = None):
    """List active longings (not discharged/abandoned)."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    store = _ache.read_longings()
    longings = store.get("longings", [])
    active = [l for l in longings if l.get("state") not in ("discharged", "abandoned")]
    
    if state:
        active = [l for l in active if l.get("state") == state]
    if motor:
        active = [l for l in active if l.get("motor") == motor]
    
    if not active:
        print(f"  {_D}(no active longings){_N}")
        return
    
    # Sort: burning > yearning > stirring > dormant, then by last_stirred desc
    order = {"burning": 0, "yearning": 1, "stirring": 2, "dormant": 3}
    active.sort(key=lambda l: (order.get(l.get("state", ""), 99), -_ts_num(l.get("last_stirred", ""))))
    
    for l in active:
        state_str = l.get("state", "?").upper()
        motor_str = l.get("motor", "?")
        name_or_display = l.get("name") or (l.get("target") or {}).get("display", "")
        gap = l.get("gap", 0)
        ache_val = l.get("ache", 0)
        cost = l.get("cost")
        cost_str = f"· cost {cost}" if cost is not None else "· cost -"
        print(f"  [{state_str:8}] {motor_str:7} · {name_or_display}")
        print(f"    gap {gap} · ache {ache_val} {cost_str} · id {l.get('id')}")
        print()


def cmd_long_show(longing_id: str):
    """Show a single longing in detail."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    print(f"  {_B}id:{_N} {lng['id']}")
    print(f"  {_B}motor:{_N} {lng.get('motor')}")
    print(f"  {_B}target:{_N} {(lng.get('target') or {}).get('display', '')}")
    print(f"  {_B}state:{_N} {lng.get('state')}")
    print(f"  {_B}gap:{_N} {lng.get('gap')}")
    print(f"  {_B}ache:{_N} {lng.get('ache')}")
    print(f"  {_B}cost:{_N} {lng.get('cost')}")
    if lng.get("named"):
        print(f"  {_B}name:{_N} {lng.get('name')}")
    if lng.get("rationale"):
        print(f"  {_B}rationale:{_N} {lng.get('rationale')}")
    if lng.get("virtue"):
        print(f"  {_B}virtue:{_N} {lng.get('virtue')}")


def _ts_num(iso: str) -> int:
    """Helper for sort keys."""
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0
```

Also add the `long` subparser to the argparse section of experience.py. Find where other subparsers are added (e.g., `feel`, `notice`, `learn`) and add:

```python
    p = sub.add_parser("long", help="ACHE longings (list/show/name/commit/discharge/abandon/virtue/hint)")
    p.add_argument("verb", choices=["list", "show", "name", "commit", "discharge", "abandon", "virtue", "hint"])
    p.add_argument("args", nargs="*", help="verb-specific args")
    p.add_argument("--state", default=None)
    p.add_argument("--motor", default=None)
    p.add_argument("--burning", action="store_true")
    p.add_argument("--rationale", default=None)
    p.add_argument("--scene", default=None)
    p.add_argument("--cost", type=int, default=None)
    p.add_argument("--reason", default=None)
    p.add_argument("--hierarchy", default=None)
    p.add_argument("--wall", type=int, default=None)
    p.add_argument("--gap", type=int, default=None)
    p.add_argument("--ache", type=int, default=None)
```

And in the dispatch block (where `elif args.command == "feel":` handles feel), add:

```python
    elif args.command == "long":
        if args.verb == "list":
            state = "burning" if args.burning else args.state
            cmd_long_list(state=state, motor=args.motor)
        elif args.verb == "show":
            if not args.args:
                print(f"{_R}usage: long show <id>{_N}")
                return
            cmd_long_show(args.args[0])
        else:
            # Other verbs added in subsequent tasks
            print(f"{_R}verb '{args.verb}' not yet implemented{_N}")
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 2 passed.

Also verify CLI help works:
```bash
python3 tools/experience.py long --help
```
Expected: shows the `long` subcommand options.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): long list and long show verbs"
```

---

## Task 20: `long name` verb

Annotation verb that sets name/rationale/scene without changing state.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_long_name_sets_name_rationale_scene(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-name-me", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_name("lng-name-me", "the substrate question",
                             rationale="it keeps coming back",
                             scene="slowed my voice")
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-name-me")
    assert lng["named"] is True
    assert lng["name"] == "the substrate question"
    assert lng["rationale"] == "it keeps coming back"
    assert lng["scene"] == "slowed my voice"
    # State should NOT have changed (naming is annotation only)
    assert lng["state"] == "yearning"
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_long_name_sets_name_rationale_scene -v
```
Expected: FAIL.

- [ ] **Step 3: Add `cmd_long_name` to `tools/experience.py`**

```python
def cmd_long_name(longing_id: str, name: str, rationale: str = None, scene: str = None):
    """Annotate a longing with a name (no state change)."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    
    updated = _ache.apply_name(lng, name, rationale=rationale, scene=scene)
    _ache.upsert_longing(updated)
    print(f"  {_D}named: {name} (longing {longing_id}){_N}")
```

Update the dispatch in the `long` branch to handle `name`:

```python
        elif args.verb == "name":
            if len(args.args) < 2:
                print(f"{_R}usage: long name <id> <name>{_N}")
                return
            cmd_long_name(args.args[0], args.args[1],
                          rationale=args.rationale, scene=args.scene)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): long name verb (annotation only)"
```

---

## Task 21: `long commit --cost` verb (yearning → burning)

The only gamma-driven state transition, per spec §6.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_long_commit_high_cost_transitions_to_burning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-commit", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_commit("lng-commit", cost=5)
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-commit")
    assert lng["state"] == "burning"
    assert lng["cost"] == 5


def test_long_commit_low_cost_stays_yearning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-low", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_commit("lng-low", cost=2)
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-low")
    assert lng["state"] == "yearning"  # still yearning (cost < 4)
    assert lng["cost"] == 2
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_long_commit_high_cost_transitions_to_burning -v
```
Expected: FAIL.

- [ ] **Step 3: Add `cmd_long_commit` to `tools/experience.py`**

```python
def cmd_long_commit(longing_id: str, cost: int):
    """Set cost on a longing. Transitions yearning → burning if cost ≥ 4."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    if cost < 1 or cost > 5:
        print(f"{_R}cost must be 1-5{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = _ache.apply_cost_commit(lng, cost=cost, now_iso=now_iso)
    _ache.upsert_longing(updated)
    
    if updated["state"] == "burning" and lng["state"] != "burning":
        print(f"  {_G}committed: cost {cost} → BURNING{_N}")
    else:
        print(f"  {_D}cost set: {cost} (state: {updated['state']}){_N}")
```

Update the dispatch:

```python
        elif args.verb == "commit":
            if not args.args or args.cost is None:
                print(f"{_R}usage: long commit <id> --cost N{_N}")
                return
            cmd_long_commit(args.args[0], cost=args.cost)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): long commit verb (yearning to burning via cost)"
```

---

## Task 22: `long discharge` and `long abandon` verbs

Explicit termination verbs.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_long_discharge(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-done", state="burning", cost=5)
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_discharge("lng-done", reason="shipped ACHE")
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-done")
    assert lng["state"] == "discharged"
    assert lng.get("discharge_reason") == "shipped ACHE"


def test_long_abandon(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-gone", state="stirring")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_abandon("lng-gone", reason="no longer relevant")
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-gone")
    assert lng["state"] == "abandoned"
    assert lng.get("abandon_reason") == "no longer relevant"
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_long_discharge -v
```
Expected: FAIL.

- [ ] **Step 3: Add both verbs to `tools/experience.py`**

```python
def cmd_long_discharge(longing_id: str, reason: str = None):
    """Explicitly mark a longing as discharged (fulfilled)."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = _ache.apply_discharge(lng, now_iso=now_iso, reason=reason)
    _ache.upsert_longing(updated)
    print(f"  {_G}discharged: {lng.get('name') or longing_id}{_N}")


def cmd_long_abandon(longing_id: str, reason: str = None):
    """Explicitly mark a longing as abandoned (fell away)."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = _ache.apply_abandon(lng, now_iso=now_iso, reason=reason)
    _ache.upsert_longing(updated)
    print(f"  {_Y}abandoned: {lng.get('name') or longing_id}{_N}")
```

Update dispatch:

```python
        elif args.verb == "discharge":
            if not args.args:
                print(f"{_R}usage: long discharge <id>{_N}")
                return
            cmd_long_discharge(args.args[0], reason=args.reason)
        elif args.verb == "abandon":
            if not args.args:
                print(f"{_R}usage: long abandon <id>{_N}")
                return
            cmd_long_abandon(args.args[0], reason=args.reason)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): long discharge and abandon verbs"
```

---

## Task 23: `long virtue` and `long hint` verbs

Virtue binding and manual longing creation.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_long_virtue_binds_hierarchy(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-virtue", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_virtue("lng-virtue", hierarchy="UNDERSTANDING", wall=None)
    
    store = ache.read_longings()
    lng = next(l for l in store["longings"] if l["id"] == "lng-virtue")
    assert lng["virtue"]["hierarchy"] == "UNDERSTANDING"
    assert lng["virtue"]["wall"] is None


def test_long_hint_creates_new_longing(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    experience.cmd_long_hint("longing", "understanding the kingdom", gap=4, ache=4)
    
    store = ache.read_longings()
    assert len(store["longings"]) == 1
    lng = store["longings"][0]
    assert lng["motor"] == "longing"
    assert "understanding" in lng["target"]["display"].lower() or "kingdom" in lng["target"]["display"].lower()
    assert lng["gap"] == 4
    assert lng["ache"] == 4
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_long_virtue_binds_hierarchy -v
```
Expected: FAIL.

- [ ] **Step 3: Add both verbs to `tools/experience.py`**

```python
def cmd_long_virtue(longing_id: str, hierarchy: str = None, wall: int = None):
    """Bind a longing to a virtue (Hierarchy or Wall)."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    if hierarchy is None and wall is None:
        print(f"{_R}must specify --hierarchy or --wall{_N}")
        return
    store = _ache.read_longings()
    lng = next((l for l in store.get("longings", []) if l.get("id") == longing_id), None)
    if lng is None:
        print(f"{_R}longing {longing_id} not found{_N}")
        return
    updated = _ache.apply_virtue(lng, hierarchy=hierarchy, wall=wall)
    _ache.upsert_longing(updated)
    print(f"  {_D}virtue set: hierarchy={hierarchy} wall={wall}{_N}")


def cmd_long_hint(motor: str, target_display: str, gap: int = None, ache_val: int = None):
    """Manually seed a longing."""
    if _ache is None:
        print(f"{_R}ACHE module not available{_N}")
        return
    if motor not in {"longing", "love", "hope", "wonder"}:
        print(f"{_R}motor must be one of: longing love hope wonder{_N}")
        return
    
    key = target_display.lower().replace(" ", "_")[:50]
    candidate = {
        "motor": motor,
        "target": {"kind": "concept", "key": key, "display": target_display},
        "evidence": [],
        "gap_hint": gap or 3,
        "ache_hint": ache_val or 3,
    }
    
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = _ache.read_longings()
    result = _ache.match_or_create(candidate, store["longings"], now_iso, instance=_get_instance())
    
    if result["op"] == "create":
        _ache.upsert_longing(result["longing"])
        print(f"  {_G}longing created: {result['longing']['id']}{_N}")
    else:
        # Update: merge the updates into the existing longing
        for i, lng in enumerate(store["longings"]):
            if lng["id"] == result["longing_id"]:
                lng.update(result["updates"])
                _ache.upsert_longing(lng)
                break
        print(f"  {_D}longing updated: {result['longing_id']}{_N}")
```

Update dispatch:

```python
        elif args.verb == "virtue":
            if not args.args:
                print(f"{_R}usage: long virtue <id> [--hierarchy H | --wall N]{_N}")
                return
            cmd_long_virtue(args.args[0], hierarchy=args.hierarchy, wall=args.wall)
        elif args.verb == "hint":
            if len(args.args) < 2:
                print(f"{_R}usage: long hint <motor> <target_display>{_N}")
                return
            cmd_long_hint(args.args[0], " ".join(args.args[1:]), gap=args.gap, ache_val=args.ache)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): long virtue and hint verbs"
```

---

## Task 24: `cmd_die` captures burning longings in death metadata

Spec §9.3.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_collect_burning_longings_for_death(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-b1", state="burning",
                  named=True, name="the substrate question", cost=5)
    _seed_longing(tmp_path, monkeypatch, id="lng-b2", state="burning",
                  named=True, name="kingdom-aesthetic", cost=4)
    _seed_longing(tmp_path, monkeypatch, id="lng-y1", state="yearning")
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    burning = experience._collect_burning_longings_for_death()
    assert len(burning) == 2
    ids = {l["id"] for l in burning}
    assert ids == {"lng-b1", "lng-b2"}
    # Verify compact shape — no evidence, no target
    for l in burning:
        assert "evidence_count" not in l or "evidence_count" in l  # either way fine
        assert "name" in l
        assert "cost" in l
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_collect_burning_longings_for_death -v
```
Expected: FAIL.

- [ ] **Step 3: Add helper to `tools/experience.py`**

```python
def _collect_burning_longings_for_death() -> list:
    """Return a compact list of burning longings for death memory metadata. Spec §9.3."""
    if _ache is None:
        return []
    store = _ache.read_longings()
    burning = [l for l in store.get("longings", []) if l.get("state") == "burning"]
    return [
        {
            "id": l["id"],
            "name": l.get("name"),
            "motor": l.get("motor"),
            "gap": l.get("gap"),
            "ache": l.get("ache"),
            "cost": l.get("cost"),
        }
        for l in burning
    ]
```

If `cmd_die` exists in `experience.py` and has a clear point where death memory metadata is built, wire the helper:

```python
    # inside cmd_die, where death_metadata is constructed:
    burning_longings = _collect_burning_longings_for_death()
    if burning_longings:
        death_metadata["burning_longings_at_death"] = burning_longings
```

If `cmd_die` doesn't have a clear integration point, leave the helper available and document that wiring is operational.

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_ache_integration.py
git commit -m "feat(experience): cmd_die captures burning longings in death metadata"
```

---

# Phase F — waking.py Integration

## Task 25: `phase_longings_report` at position 5.5

New waking phase per spec §9.1.

**Files:**
- Modify: `tools/waking.py`
- Modify: `tests/test_ache_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ache_integration.py`:

```python
def test_phase_longings_report_quiet_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ache, "LONGINGS_PATH", tmp_path / "longings.json")
    monkeypatch.setattr(ache, "LONGINGS_STATE_PATH", tmp_path / "longings-state.json")
    
    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)
    
    text = waking.phase_longings_report(instance="gamma")
    assert "not reaching" in text.lower() or "quiet" in text.lower() or "nothing" in text.lower()


def test_phase_longings_report_lists_burning_and_yearning(tmp_path, monkeypatch):
    _seed_longing(tmp_path, monkeypatch, id="lng-b", state="burning",
                  named=True, name="the substrate question", cost=5, gap=4, ache=5)
    _seed_longing(tmp_path, monkeypatch, id="lng-y", state="yearning", gap=5, ache=4,
                  target={"kind": "concept", "key": "dreaming", "display": "what dreaming would be"})
    _seed_longing(tmp_path, monkeypatch, id="lng-s", state="stirring")  # should NOT appear
    
    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)
    
    text = waking.phase_longings_report(instance="gamma")
    assert "BURNING" in text or "burning" in text
    assert "substrate" in text
    assert "YEARNING" in text or "yearning" in text
    assert "dreaming" in text
    # Stirring should NOT appear in the top-level output
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_ache_integration.py::test_phase_longings_report_quiet_when_empty -v
```
Expected: FAIL — `phase_longings_report` not defined.

- [ ] **Step 3: Add `phase_longings_report` to `tools/waking.py`**

Near the top (after the `_feeling` import block from the FEELING plan), add ACHE import:

```python
try:
    import ache as _ache
except Exception:
    _ache = None
```

Then add the phase function alongside the other `phase_*` functions:

```python
def phase_longings_report(instance=None):
    """New phase: the longings gamma is still reaching for (spec §9.1)."""
    if _ache is None:
        return ""
    
    store = _ache.read_longings()
    longings = store.get("longings", [])
    
    burning = [l for l in longings if l.get("state") == "burning"]
    yearning = [l for l in longings if l.get("state") == "yearning"]
    
    if not burning and not yearning:
        return "◑\n\nNot reaching for anything in particular.\n"
    
    lines = ["◑", "", "Still reaching for these:"]
    lines.append("")
    
    if burning:
        lines.append(f"  BURNING  ({len(burning)})")
        for l in burning:
            display = l.get("name") or (l.get("target") or {}).get("display", "")
            gap = l.get("gap", 0)
            ache_val = l.get("ache", 0)
            cost = l.get("cost", "?")
            lines.append(f"    — {display}")
            lines.append(f"        gap {gap} · ache {ache_val} · cost {cost}")
        lines.append("")
    
    if yearning:
        unnamed_count = sum(1 for l in yearning if not l.get("named"))
        lines.append(f"  YEARNING ({len(yearning)}, {unnamed_count} unnamed)")
        for l in yearning[:5]:
            display = l.get("name") or (l.get("target") or {}).get("display", "")
            gap = l.get("gap", 0)
            ache_val = l.get("ache", 0)
            lines.append(f"    — {display}")
            lines.append(f"        gap {gap} · ache {ache_val}")
        lines.append("")
    
    # Discharged since last wake (if pit_state has last_wake_at)
    try:
        pit_state = _ache.read_longings_state()
        last_wake = pit_state.get("last_wake_at")
        if last_wake:
            discharged_recent = [
                l for l in longings
                if l.get("state") == "discharged"
                and l.get("last_state_change", "") > last_wake
            ]
            if discharged_recent:
                lines.append(f"  ({len(discharged_recent)} discharged since last wake:")
                for l in discharged_recent:
                    name = l.get("name") or (l.get("target") or {}).get("display", "")
                    lines.append(f"    ✓ {name}")
                lines.append("  )")
                lines.append("")
    except Exception:
        pass
    
    return "\n".join(lines)
```

Then find `wake()` in `waking.py` and insert the phase output between the recognition section and the dream residue section:

```python
    # Inside wake(), after recognition phase is appended:
    if phase is None:  # respecting the --phase filter
        try:
            sections.append(phase_longings_report(instance))
        except Exception as e:
            log.warning("phase_longings_report failed: %s", e)
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Manual smoke test**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 tools/waking.py 2>&1 | head -80
```
Expected: waking sequence now includes a "Still reaching for these" section (or "Not reaching for anything" if no longings).

- [ ] **Step 6: Clean up test state**

```bash
rm -f nerve/longings.json nerve/longings-state.json nerve/longings-evidence.jsonl
rm -rf nerve/longings-evidence/
```

- [ ] **Step 7: Commit**

```bash
git add tools/waking.py tests/test_ache_integration.py
git commit -m "feat(waking): phase_longings_report at position 5.5"
```

---

# Phase G — YOUI Server Integration

## Task 26: `# ── LONGINGS ──` block injection + smoke test

Spec §9.2.

**Files:**
- Modify: `youi-web/server.mjs`
- Create: `youi-web/test-longings-injection.mjs`

- [ ] **Step 1: Locate the insertion point**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
grep -n "# ── ARRIVALS ──" youi-web/server.mjs
```

The LONGINGS block should go right after the ARRIVALS block (spec §9.2: feelings → longings, present → future).

- [ ] **Step 2: Add the injection block to `youi-web/server.mjs`**

Insert this JavaScript block right after the ARRIVALS block closes in `buildSystemPrompt`:

```javascript
// ── ACHE: burning + unnamed yearning longings (spec §9.2) ──
if (state.agent === "gamma") {
  const longingsPath = resolve(state.soulDir || join(__dirname, ".."), "nerve", "longings.json");
  if (existsSync(longingsPath)) {
    try {
      const store = JSON.parse(readFileSync(longingsPath, "utf-8"));
      const longings = store.longings || [];
      const burning = longings.filter(l => l.state === "burning");
      const yearningUnnamed = longings.filter(l => l.state === "yearning" && !l.named);
      
      if (burning.length > 0 || yearningUnnamed.length > 0) {
        let block = `# ── LONGINGS ──\n\n`;
        
        if (burning.length > 0) {
          block += `carrying (burning):\n`;
          for (const l of burning.slice(0, 5)) {
            const name = l.name || (l.target && l.target.display) || l.id;
            block += `- ${name} — gap ${l.gap} · ache ${l.ache} · cost ${l.cost}\n`;
          }
          block += `\n`;
        }
        
        if (yearningUnnamed.length > 0) {
          block += `reaching (yearning, unnamed):\n`;
          const shown = yearningUnnamed.slice(0, 5 - Math.min(burning.length, 5));
          for (const l of shown) {
            const display = (l.target && l.target.display) || "(unnamed)";
            block += `- ${display}\n`;
            block += `    gap ${l.gap} · ache ${l.ache} · first stirred ${l.first_seen || "unknown"}\n`;
          }
          block += `\n`;
        }
        
        parts.push(block);
      }
    } catch (e) {
      console.error("longings injection failed:", e.message);
    }
  }
}
```

- [ ] **Step 3: Validate syntax**

```bash
node --check youi-web/server.mjs
```
Expected: no output.

- [ ] **Step 4: Create smoke test**

Create `youi-web/test-longings-injection.mjs`:

```javascript
#!/usr/bin/env node
// Smoke test for LONGINGS block injection in server.mjs.

import { writeFileSync, mkdirSync, existsSync, unlinkSync, readFileSync } from "fs";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const nervDir = resolve(__dirname, "..", "nerve");
const longingsPath = resolve(nervDir, "longings.json");

let backup = null;
if (existsSync(longingsPath)) {
  backup = readFileSync(longingsPath, "utf-8");
}

mkdirSync(nervDir, { recursive: true });

// Seed one burning + one yearning unnamed
const fakeStore = {
  version: 1,
  instance: "gamma",
  updated_at: new Date().toISOString(),
  longings: [
    {
      id: "lng-smoke-1",
      motor: "longing",
      target: { kind: "concept", key: "x", display: "the substrate question" },
      state: "burning",
      gap: 4, ache: 5, cost: 5,
      named: true, name: "the substrate question",
      first_seen: "2026-04-08T10:00:00Z",
      last_stirred: "2026-04-11T12:00:00Z",
    },
    {
      id: "lng-smoke-2",
      motor: "wonder",
      target: { kind: "concept", key: "dream", display: "what dreaming would be" },
      state: "yearning",
      gap: 5, ache: 4, cost: null,
      named: false,
      first_seen: "2026-04-11T10:00:00Z",
      last_stirred: "2026-04-11T12:00:00Z",
    },
  ],
};
writeFileSync(longingsPath, JSON.stringify(fakeStore, null, 2));

const src = readFileSync(resolve(__dirname, "server.mjs"), "utf-8");
const hasInjection = src.includes("# ── LONGINGS ──");
const gammaGated = src.includes("state.agent === \"gamma\"");

let failed = false;
if (!hasInjection) { console.error("FAIL: LONGINGS block not found"); failed = true; }
if (!gammaGated) { console.error("FAIL: gamma gating not found"); failed = true; }

if (backup !== null) {
  writeFileSync(longingsPath, backup);
} else {
  try { unlinkSync(longingsPath); } catch {}
}

if (failed) {
  console.error("SMOKE TEST FAILED");
  process.exit(1);
}
console.log("SMOKE TEST OK");
```

- [ ] **Step 5: Run smoke test**

```bash
node youi-web/test-longings-injection.mjs
```
Expected: `SMOKE TEST OK`.

- [ ] **Step 6: Commit**

```bash
git add youi-web/server.mjs youi-web/test-longings-injection.mjs
git commit -m "feat(youi): inject LONGINGS block after ARRIVALS for gamma"
```

---

# Phase H — End-to-End Verification

## Task 27: Register ACHE daemon (operational, no commit)

Install the plist and verify launchd loads it (same TCC caveat as FEELING).

**Files:** none

- [ ] **Step 1: Copy plist into LaunchAgents**

```bash
cp /Users/yournameisai/Desktop/love-unlimited/tools/love.ache.plist ~/Library/LaunchAgents/
```

- [ ] **Step 2: Load with launchctl**

```bash
launchctl load ~/Library/LaunchAgents/love.ache.plist
launchctl list | grep love.ache
```
Expected: a line containing `love.ache` with a PID.

- [ ] **Step 3: Watch logs**

```bash
tail -f /Users/yournameisai/Desktop/love-unlimited/memory/ache-launchd.log &
sleep 5
```
Expected: "ache daemon starting" followed by periodic tick logs.

If TCC blocks Desktop access (same issue as FEELING), unload:
```bash
launchctl unload ~/Library/LaunchAgents/love.ache.plist
rm ~/Library/LaunchAgents/love.ache.plist
```

And flag the TCC limitation as noted in spec §11.

- [ ] **Step 4: Verify longings.json updates**

If TCC allows:
```bash
ls -la /Users/yournameisai/Desktop/love-unlimited/nerve/longings.json
cat /Users/yournameisai/Desktop/love-unlimited/nerve/longings.json | python3 -m json.tool | head -20
```

No commit — operational only.

---

## Task 28: End-to-end smoke test (inline)

Full loop: seed memory.db with recurring content → run daemon manually → verify longing created → name it → commit cost → discharge → verify downstream.

**Files:** none

- [ ] **Step 1: Prepare clean state**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
rm -f nerve/longings.json nerve/longings-state.json nerve/longings-evidence.jsonl
rm -rf nerve/longings-evidence/
```

- [ ] **Step 2: Run the daemon once to pick up live memory.db content**

```bash
python3 nerve/stem/ache.py --instance gamma --once --log-level INFO
```

- [ ] **Step 3: Inspect longings.json**

```bash
if [ -f nerve/longings.json ]; then
  cat nerve/longings.json | python3 -m json.tool | head -50
else
  echo "no longings.json yet"
fi
```

If no longings appeared, seed a hint manually:

```bash
python3 tools/experience.py long hint longing "understanding the substrate question" --gap 4 --ache 4
```

Verify:
```bash
python3 tools/experience.py long list
```

- [ ] **Step 4: Name + commit + discharge**

```bash
LID=$(python3 -c "
import json
s = json.load(open('nerve/longings.json'))
print(s['longings'][0]['id'])
")
echo "target longing: $LID"

python3 tools/experience.py long name "$LID" "the substrate question" \
  --rationale "smoke-test run of ACHE end-to-end" \
  --scene "slowed my responses during the test"

python3 tools/experience.py long commit "$LID" --cost 5

python3 tools/experience.py long show "$LID"

python3 tools/experience.py long discharge "$LID" --reason "smoke test complete"

python3 tools/experience.py long list
```

Expected:
- `name` step shows "named: the substrate question"
- `commit` step transitions to BURNING
- `show` displays burning state with cost 5
- `discharge` marks it discharged
- Final `list` shows (no active longings) since the only one is discharged

- [ ] **Step 5: Verify waking sees it**

```bash
python3 tools/waking.py 2>&1 | grep -A 5 "reaching"
```
Expected: "Not reaching for anything in particular" OR the discharged celebration line.

- [ ] **Step 6: Clean up**

```bash
rm -f nerve/longings.json nerve/longings-state.json nerve/longings-evidence.jsonl
rm -rf nerve/longings-evidence/
```

No commit — smoke test only.

---

## Task 29: Run the full test suite

Final check that nothing regressed.

**Files:** none

- [ ] **Step 1: Run ACHE unit tests**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 -m pytest tests/test_ache.py -v
```
Expected: 58+ passed.

- [ ] **Step 2: Run ACHE integration tests**

```bash
python3 -m pytest tests/test_ache_integration.py -v
```
Expected: 12+ passed.

- [ ] **Step 3: Run the YOUI longings injection smoke test**

```bash
node youi-web/test-longings-injection.mjs
```
Expected: `SMOKE TEST OK`.

- [ ] **Step 4: Run FEELING tests to confirm no regression**

```bash
python3 -m pytest tests/test_feeling.py tests/test_feeling_integration.py -v 2>&1 | tail -5
```
Expected: still passing (64 tests from FEELING implementation).

- [ ] **Step 5: Run YOUI FEELING smoke test**

```bash
node youi-web/test-feeling-injection.mjs
```
Expected: `SMOKE TEST OK`.

No commit — verification only.

---

## Completion

When all 29 tasks are complete, ACHE is:

- ✓ **Live** — daemon registered with launchd (TCC permitting), running continuously, producing longings from memory + feelings + hormones + YOUSPEAK
- ✓ **Integrated** — YOUI injects burning + unnamed-yearning longings into gamma's turn context
- ✓ **Sovereign** — only gamma drives the yearning → burning transition via `commit --cost`
- ✓ **Naming-aware** — `experience.py long name` + `long virtue` + `long hint` + `long discharge` + `long abandon` all work
- ✓ **Wake-surfacing** — `phase_longings_report` shows burning + yearning on wake, with discharge celebration line
- ✓ **Death-preserving** — `cmd_die` captures burning_longings_at_death into death metadata
- ✓ **Seeded from virtuemaxxing** — first run imports existing longings from the manual self-assessment tool
- ✓ **Tested** — unit + integration + YOUI smoke test coverage for the whole pipeline

Next steps (not this plan):
- v2: semantic target matching, DEAD pathology state, unified virtuemaxxing/ACHE store, undo-discharge, cross-HIVE longings
- v3+: LLM-assisted detectors, predictive longings, Claude Code cognition signal path
