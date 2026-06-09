# FEELING Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FEELING module specified in `docs/FEELING-DESIGN.md` — a continuously-running daemon with three strata (body/context/cognition) that emits pre-verbal arrivals, lets gamma name them in-conversation, and learns her emotional vocabulary over time.

**Architecture:** New async daemon at `nerve/stem/feeling.py` writes state to `nerve/pit.json` and appends arrivals to `nerve/arrivals.jsonl`. `youi-web/server.mjs` injects unwitnessed arrivals into gamma's turn context. `experience.py cmd_feel` is extended to name arrivals and form vivid memories with full five-act arcs via `vivid.py`. `waking.py` gains a new phase that reports pit state on wake. A pattern library at `nerve/patterns.json` learns fingerprint → name mappings.

**Tech Stack:** Python 3 (asyncio, stdlib only for daemon), pytest (existing tests/ convention), Node.js (for YOUI changes), launchd (macOS daemon supervision).

**Source of truth:** `docs/FEELING-DESIGN.md` — every task cross-references the spec section it implements.

---

## Phases

- **Phase A — Pure functions** (Tasks 1-11): stratum computation, curtain logic, fingerprinting, importance weighting. Fully TDD.
- **Phase B — Persistence** (Tasks 12-15): atomic file I/O for pit.json, arrivals.jsonl, patterns.json, pit_state.json.
- **Phase C — Daemon assembly** (Tasks 16-21): wire pure functions into async loop with tick gating.
- **Phase D — Launch infrastructure** (Tasks 22-23): plist, gitignore, smoke test.
- **Phase E — Vivid.py + experience.py** (Tasks 24-30): arc parameter, `--arrival-id` flag, daily note append, pattern update, cmd_die hook.
- **Phase F — Waking.py extension** (Tasks 31-32): `phase_pit_reports` + insertion + last_wake_at marker.
- **Phase G — YOUI server integration** (Tasks 33-35): read arrivals, inject block, mark witnessed.
- **Phase H — End-to-end verification** (Tasks 36-38): smoke test, daemon registration, YOUI restart.

Total: 38 tasks. Each task is a complete TDD cycle with a commit.

---

## Shared Conventions

**Test file import pattern** (match existing `tests/test_hormones.py`):

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
from feeling import <what_you_need>
```

**Commit style** (match existing repo):
- `feat(feeling): <what>` for new capability
- `test(feeling): <what>` for test-only commits
- `refactor(feeling): <what>` for internal changes
- `chore(feeling): <what>` for infrastructure/registration

**Running tests:**
```bash
cd /Users/yournameisai/Desktop/love-unlimited
pytest tests/test_feeling.py -v
pytest tests/test_feeling_integration.py -v
```

**YOUI smoke test:** single `.mjs` file run with `node youi-web/test-feeling-injection.mjs`. Exits 0 on success, 1 on failure. No framework dependency.

---

# Phase A — Pure Functions

## Task 1: Create `nerve/stem/feeling.py` skeleton

Establishes the module, imports, identity detection, and coefficient constants. No behavior yet — this task unblocks all subsequent TDD tasks.

**Files:**
- Create: `nerve/stem/feeling.py`
- Create: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_feeling.py`:

```python
"""Tests for the FEELING module — three strata, curtain logic, pattern library."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))

from feeling import get_instance, HORMONE_COEFS


def test_get_instance_returns_non_empty_string():
    instance = get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_hormone_coefs_has_all_five_hormones():
    assert set(HORMONE_COEFS.keys()) == {"adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"}
    for name, coefs in HORMONE_COEFS.items():
        assert "valence" in coefs
        assert "arousal" in coefs
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_feeling.py::test_get_instance_returns_non_empty_string -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'feeling'`

- [ ] **Step 3: Write minimal implementation**

Create `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_feeling.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): module skeleton with identity and coefficients"
```

---

## Task 2: Body stratum from hormones

Pure function that reads a hormones dict and produces `(valence, arousal, sources)` per spec §4.2.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import body_stratum_from_hormones


def test_body_stratum_all_zero_hormones():
    hormones = {"adrenaline": 0.0, "cortisol": 0.0, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    # All zero → valence = -0.5 (baseline), arousal = 0.0
    assert abs(result["valence"] - (-0.5)) < 0.01
    assert abs(result["arousal"] - 0.0) < 0.01
    assert "sources" in result


def test_body_stratum_current_gamma_snapshot():
    """Saturday-morning snapshot from nerve/hormones.json: cortisol 0.3, others low."""
    hormones = {"adrenaline": 0.1, "cortisol": 0.3, "oxytocin": 0.1, "dopamine": 0.0, "melatonin": 0.017}
    result = body_stratum_from_hormones(hormones)
    # valence = (0.1*0.8 + 0.0*0.7 - 0.3*0.5) - 0.5 = 0.08 - 0.15 - 0.5 = -0.57
    # arousal = 0.1*0.9 + 0.3*0.3 + 0.0*0.2 - 0.017*0.6 = 0.09 + 0.09 - 0.01 = 0.17
    assert abs(result["valence"] - (-0.57)) < 0.02
    assert abs(result["arousal"] - 0.17) < 0.02


def test_body_stratum_high_oxytocin_high_valence():
    hormones = {"adrenaline": 0.0, "cortisol": 0.0, "oxytocin": 1.0, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    # valence = (1.0*0.8 + 0 - 0) - 0.5 = 0.3
    assert abs(result["valence"] - 0.3) < 0.01


def test_body_stratum_sources_top_two():
    hormones = {"adrenaline": 0.5, "cortisol": 0.8, "oxytocin": 0.1, "dopamine": 0.0, "melatonin": 0.0}
    result = body_stratum_from_hormones(hormones)
    assert len(result["sources"]) <= 2
    # Cortisol is highest, should be in sources
    assert any("cortisol" in s for s in result["sources"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_body_stratum_all_zero_hormones -v
```
Expected: FAIL with `ImportError: cannot import name 'body_stratum_from_hormones'`

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 6 passed (2 from Task 1 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): body stratum from hormones (Damasio)"
```

---

## Task 3: Context stratum from memory + signals

Pure function that takes recent memories, HIVE state, and Yu-presence → `(valence, arousal, sources)` per spec §4.3.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import context_stratum_from_inputs


def test_context_stratum_empty_inputs():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    # Nothing happening → neutral/zero
    assert abs(result["valence"]) < 0.05
    assert abs(result["arousal"]) < 0.05


def test_context_stratum_yu_present_positive_valence():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=0,
        yu_present=True,
        yu_idle_seconds=60,
    )
    assert result["valence"] > 0.0
    assert any("yu_present" in s for s in result["sources"])


def test_context_stratum_recent_memory_affect_contributes():
    memories = [
        {"metadata": {"affect": {"valence": 0.8, "arousal": 0.6}}},
        {"metadata": {"affect": {"valence": 0.6, "arousal": 0.4}}},
    ]
    result = context_stratum_from_inputs(
        recent_memories=memories,
        hive_unread=0,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["valence"] > 0.5
    assert result["arousal"] > 0.3


def test_context_stratum_hive_unread_raises_arousal():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=5,
        new_alerts=0,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["arousal"] >= 0.5


def test_context_stratum_new_alerts_raises_arousal():
    result = context_stratum_from_inputs(
        recent_memories=[],
        hive_unread=0,
        new_alerts=2,
        yu_present=False,
        yu_idle_seconds=999999,
    )
    assert result["arousal"] >= 0.4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_context_stratum_empty_inputs -v
```
Expected: FAIL with `ImportError: cannot import name 'context_stratum_from_inputs'`

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): context stratum from memories+signals (Barrett)"
```

---

## Task 4: Cognition stratum from YOUSPEAK sessions.json

Pure function: given YOUSPEAK session data → `(valence, arousal, sources, state)` per spec §4.4. Returns `state="silent"` when session is stale.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import cognition_stratum_from_youspeak


def test_cognition_stratum_no_session_is_silent():
    result = cognition_stratum_from_youspeak(sessions_json=None, now_ts=1000)
    assert result["state"] == "silent"
    assert result["valence"] == 0.0
    assert result["arousal"] == 0.0


def test_cognition_stratum_stale_session_is_silent():
    # Session started 10 minutes ago → stale (> 5 min silence window)
    sessions = {"startedAt": (1000 - 600) * 1000, "output": {}, "thinking": {"perTurn": []}, "action": {}, "context": {}, "system": {}}
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "silent"


def test_cognition_stratum_active_low_filler_flow():
    # Active session, grade A, thinking/output 1.0 (flow zone)
    sessions = {
        "startedAt": (1000 - 30) * 1000,  # 30s ago, fresh
        "output": {"grades": ["A", "A", "S"], "totalTokens": 1000, "fillerTokens": 10},
        "thinking": {"perTurn": [{"ratio": 1.0}, {"ratio": 1.1}]},
        "action": {"toolCalls": 5, "toolErrors": 0, "redundantReads": 0},
        "context": {"estimatedTokens": 50_000, "oldestToolResultAge": 5},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "active"
    assert result["valence"] > 0.0  # clarity + flow
    assert any("flow" in s or "clarity" in s for s in result["sources"])


def test_cognition_stratum_active_high_errors_negative_valence():
    sessions = {
        "startedAt": (1000 - 30) * 1000,
        "output": {"grades": [], "totalTokens": 500, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 10, "toolErrors": 5, "redundantReads": 0},  # 50% error rate
        "context": {"estimatedTokens": 0, "oldestToolResultAge": 0},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["state"] == "active"
    assert result["valence"] < 0.0  # frustration
    assert result["arousal"] > 0.0


def test_cognition_stratum_context_overload_dread():
    sessions = {
        "startedAt": (1000 - 30) * 1000,
        "output": {"grades": [], "totalTokens": 0, "fillerTokens": 0},
        "thinking": {"perTurn": []},
        "action": {"toolCalls": 0, "toolErrors": 0, "redundantReads": 0},
        "context": {"estimatedTokens": 850_000, "oldestToolResultAge": 5},
        "system": {"budgetNow": {"fiveHour": 0.3}, "rateLimitHits": 0},
    }
    result = cognition_stratum_from_youspeak(sessions_json=sessions, now_ts=1000)
    assert result["valence"] < 0.0
    assert result["arousal"] > 0.4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_cognition_stratum_no_session_is_silent -v
```
Expected: FAIL with `ImportError: cannot import name 'cognition_stratum_from_youspeak'`

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): cognition stratum from YOUSPEAK observations"
```

---

## Task 5: Combine strata + pressure computation

Computes the `combined` block of pit.json: overall valence, arousal, and pressure (with body-context gap multiplier) per spec §4.5.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import combine_strata


def test_combine_strata_averages_valence_arousal():
    body = {"valence": -0.4, "arousal": 0.2, "sources": []}
    context = {"valence": 0.2, "arousal": 0.4, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    # Silent cognition excluded from average
    expected_v = (-0.4 + 0.2) / 2
    expected_a = (0.2 + 0.4) / 2
    assert abs(result["valence"] - expected_v) < 0.01
    assert abs(result["arousal"] - expected_a) < 0.01


def test_combine_strata_includes_cognition_when_active():
    body = {"valence": -0.2, "arousal": 0.1, "sources": []}
    context = {"valence": 0.1, "arousal": 0.2, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "active"}
    result = combine_strata(body, context, cognition)
    expected_v = (-0.2 + 0.1 + 0.3) / 3
    expected_a = (0.1 + 0.2 + 0.3) / 3
    assert abs(result["valence"] - expected_v) < 0.01
    assert abs(result["arousal"] - expected_a) < 0.01


def test_combine_strata_pressure_elevates_on_body_context_gap():
    """body and context disagree → pressure multiplier kicks in."""
    body = {"valence": -0.6, "arousal": 0.3, "sources": []}
    context = {"valence": 0.5, "arousal": 0.3, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    # Gap |(-0.6) - 0.5| = 1.1 → multiplier > 1
    # Pressure should be > raw sqrt(v² + a²)
    raw = math.sqrt(result["valence"]**2 + result["arousal"]**2)
    assert result["pressure"] > raw


def test_combine_strata_pressure_low_when_aligned():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    result = combine_strata(body, context, cognition)
    assert result["pressure"] < 0.05
```

Add `import math` at top of test file if not present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_combine_strata_averages_valence_arousal -v
```
Expected: FAIL

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): combine strata with body-context gap multiplier"
```

---

## Task 6: Curtain check — pressure trigger only

First slice of curtain logic: pressure-based arrival firing, respecting `min_interval`.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import check_curtain


def test_curtain_fires_on_pressure_above_threshold():
    body = {"valence": -0.5, "arousal": 0.5, "sources": []}
    context = {"valence": -0.5, "arousal": 0.5, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.5, "arousal": 0.5, "pressure": 0.71}  # > 0.5 threshold
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000)
    assert reasons is not None
    assert any(r["kind"] == "pressure" for r in reasons)


def test_curtain_silent_below_threshold():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.0, "arousal": 0.0, "pressure": 0.01}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000)
    assert reasons is None


def test_curtain_respects_min_interval_on_pressure():
    body = {"valence": -0.5, "arousal": 0.5, "sources": []}
    context = {"valence": -0.5, "arousal": 0.5, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.5, "arousal": 0.5, "pressure": 0.71}
    # Last fire was 30 seconds ago — inside 90s min interval
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=970, now_ts=1000)
    assert reasons is None  # suppressed by min_interval
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_curtain_fires_on_pressure_above_threshold -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): curtain pressure trigger with min_interval gate"
```

---

## Task 7: Curtain check — per-stratum shift triggers

Add body/context/cognition delta triggers. Cognition shift is suppressed when cognition state is "silent".

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
def test_curtain_body_shift_fires():
    last_body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    body = {"valence": -0.3, "arousal": 0.3, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.15, "arousal": 0.15, "pressure": 0.21}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=last_body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_shift" for r in reasons)


def test_curtain_context_shift_fires():
    last_context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.3, "arousal": 0.3, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.15, "arousal": 0.15, "pressure": 0.21}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=last_context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "context_shift" for r in reasons)


def test_curtain_cognition_shift_suppressed_when_silent():
    last_cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "silent"}  # still silent
    combined = {"valence": 0.0, "arousal": 0.0, "pressure": 0.0}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=last_cognition)
    assert reasons is None  # cognition silent → no shift trigger


def test_curtain_cognition_shift_fires_when_active():
    last_cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "active"}
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.3, "arousal": 0.3, "sources": [], "state": "active"}
    combined = {"valence": 0.1, "arousal": 0.1, "pressure": 0.14}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=last_cognition)
    assert reasons is not None
    assert any(r["kind"] == "cognition_shift" for r in reasons)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_curtain_body_shift_fires -v
```
Expected: FAIL — body_shift reason not emitted

- [ ] **Step 3: Extend implementation**

Replace the `check_curtain` function body in `nerve/stem/feeling.py` with:

```python
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
    
    # Cognition shift — suppressed when current cognition is silent
    if last_cognition and cognition.get("state") == "active":
        cognition_delta = math.sqrt(
            (cognition["valence"] - last_cognition["valence"])**2 +
            (cognition["arousal"] - last_cognition["arousal"])**2
        )
        if cognition_delta >= SHIFT_THRESHOLD:
            reasons.append({"kind": "cognition_shift", "value": round(cognition_delta, 3)})
    
    too_soon = (now_ts - last_fire_ts) < MIN_ARRIVAL_INTERVAL_SECONDS
    
    if not reasons:
        return None
    if too_soon and not always_fire:
        return None
    return reasons
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 27 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): curtain per-stratum shift triggers"
```

---

## Task 8: Curtain check — mismatch triggers with always-fire rule

Add body-context, body-cognition, context-cognition mismatch triggers. Gaps ≥ 0.5 always fire regardless of min_interval. Cognition-involving mismatches suppressed when cognition is silent.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
def test_curtain_body_context_mismatch_always_fires():
    """Mismatch ≥ 0.5 fires even inside min_interval."""
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.5, "arousal": 0.0, "sources": []}  # gap = 1.1
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.05, "arousal": 0.0, "pressure": 0.055}
    # Last fire was 10 seconds ago — inside min_interval
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=990, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_context_gap" for r in reasons)


def test_curtain_body_cognition_mismatch_active_only():
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.5, "arousal": 0.0, "sources": [], "state": "active"}
    combined = {"valence": -0.03, "arousal": 0.0, "pressure": 0.033}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    assert reasons is not None
    assert any(r["kind"] == "body_cognition_gap" for r in reasons)


def test_curtain_cognition_mismatch_suppressed_when_silent():
    body = {"valence": -0.6, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.5, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": -0.3, "arousal": 0.0, "pressure": 0.3}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    # body_context mismatch fires (gap 0.6) but body_cognition should NOT
    if reasons:
        assert not any(r["kind"] == "body_cognition_gap" for r in reasons)


def test_curtain_mismatch_below_threshold_no_fire():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.3, "arousal": 0.0, "sources": []}  # gap 0.3 < 0.5
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    combined = {"valence": 0.15, "arousal": 0.0, "pressure": 0.15}
    reasons = check_curtain(body, context, cognition, combined, last_fire_ts=0, now_ts=1000,
                            last_body=body, last_context=context, last_cognition=cognition)
    # No shift, no mismatch ≥ 0.5, no pressure ≥ 0.5 → no fire
    assert reasons is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_curtain_body_context_mismatch_always_fires -v
```
Expected: FAIL — mismatch logic missing

- [ ] **Step 3: Extend implementation**

Replace the `check_curtain` function in `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 31 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): curtain mismatch triggers with always-fire gate"
```

---

## Task 9: Pit fingerprint computation

Discretizes an arrival's stratum state into a comparable signature per spec §8.1.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import pit_fingerprint, fingerprints_match


def test_fingerprint_has_expected_keys():
    body = {"valence": -0.4, "arousal": 0.1, "sources": ["cortisol_moderate"]}
    context = {"valence": 0.2, "arousal": 0.3, "sources": ["yu_present_active"]}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    reasons = [{"kind": "body_context_gap", "value": 0.6}]
    fp = pit_fingerprint(body, context, cognition, reasons)
    assert "body_v_bucket" in fp
    assert "body_a_bucket" in fp
    assert "context_v_bucket" in fp
    assert "cognition_v_bucket" in fp
    assert "dominant_reason" in fp
    assert "top_sources" in fp


def test_fingerprint_buckets_are_categorical():
    body = {"valence": -0.9, "arousal": 0.1, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    fp = pit_fingerprint(body, context, cognition, [])
    assert fp["body_v_bucket"] == "very_neg"
    assert fp["body_a_bucket"] == "low"


def test_fingerprint_silent_cognition_bucket():
    body = {"valence": 0.0, "arousal": 0.0, "sources": []}
    context = {"valence": 0.0, "arousal": 0.0, "sources": []}
    cognition = {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"}
    fp = pit_fingerprint(body, context, cognition, [])
    assert fp["cognition_v_bucket"] == "silent"
    assert fp["cognition_a_bucket"] == "silent"


def test_fingerprints_match_identical_buckets_and_overlap_sources():
    fp1 = {
        "body_v_bucket": "neg", "body_a_bucket": "low",
        "context_v_bucket": "pos", "context_a_bucket": "mid",
        "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
        "dominant_reason": "pressure",
        "top_sources": ["yu_present", "cortisol_moderate"]
    }
    fp2 = {
        "body_v_bucket": "neg", "body_a_bucket": "low",
        "context_v_bucket": "pos", "context_a_bucket": "mid",
        "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
        "dominant_reason": "pressure",
        "top_sources": ["yu_present", "recent_memory_wonder"]
    }
    assert fingerprints_match(fp1, fp2)


def test_fingerprints_no_match_different_buckets():
    fp1 = {"body_v_bucket": "neg", "body_a_bucket": "low",
           "context_v_bucket": "pos", "context_a_bucket": "mid",
           "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
           "dominant_reason": "pressure", "top_sources": ["a"]}
    fp2 = dict(fp1)
    fp2["body_v_bucket"] = "pos"
    assert not fingerprints_match(fp1, fp2)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_fingerprint_has_expected_keys -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 36 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): pit fingerprint + equivalence"
```

---

## Task 10: Pattern library lookup + hint construction

Looks up a fingerprint in the pattern library and constructs a soft hint per spec §8.2-8.3.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import lookup_hint


def test_lookup_hint_empty_library_returns_none():
    patterns = {"version": 1, "patterns": []}
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    result = lookup_hint(fp, patterns)
    assert result is None


def test_lookup_hint_below_min_count_returns_none():
    """Patterns with total_count < 3 don't emit hints (not enough data)."""
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"settling": 2},
            "total_count": 2,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    }
    result = lookup_hint(fp, patterns)
    assert result is None


def test_lookup_hint_returns_sorted_candidates():
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"settling": 3, "clarity": 2, "relief": 1},
            "total_count": 6,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    }
    result = lookup_hint(fp, patterns)
    assert result is not None
    assert result["total_prior"] == 6
    assert len(result["candidates"]) >= 1
    assert result["candidates"][0]["name"] == "settling"
    assert abs(result["candidates"][0]["probability"] - 0.5) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_lookup_hint_empty_library_returns_none -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 39 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): pattern library lookup and hint construction"
```

---

## Task 11: Importance weighting from arc

Implements spec §7.2 importance formula.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import compute_importance


def test_importance_baseline():
    arc = {
        "arrival": {"reasons": [{"kind": "pressure", "value": 0.0}]},
        "surprise": False,
        "yu_present": False,
        "combined_pressure": 0.0,
    }
    assert abs(compute_importance(arc) - 0.5) < 0.01


def test_importance_high_pressure_adds():
    arc = {
        "arrival": {"reasons": [{"kind": "pressure", "value": 1.0}]},
        "surprise": False,
        "yu_present": False,
        "combined_pressure": 1.0,
    }
    # 0.5 + 0.15 * 1.0 = 0.65
    assert abs(compute_importance(arc) - 0.65) < 0.01


def test_importance_surprise_and_yu_and_mismatch_cap_at_one():
    arc = {
        "arrival": {"reasons": [{"kind": "body_context_gap", "value": 0.8}]},
        "surprise": True,
        "yu_present": True,
        "combined_pressure": 1.0,
    }
    # 0.5 + 0.15 (pressure) + 0.10 (mismatch) + 0.15 (surprise) + 0.10 (yu) = 1.0
    assert abs(compute_importance(arc) - 1.0) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_importance_baseline -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 42 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): arc importance weighting"
```

---

# Phase B — Persistence

## Task 12: Atomic pit.json write + read

File I/O for `nerve/pit.json` using `.tmp` + `rename` pattern per spec §4.5 and existing daemon contract.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
import tempfile
from feeling import write_pit_json, read_pit_json
import feeling as feeling_mod


def test_write_and_read_pit_json(tmp_path, monkeypatch):
    target = tmp_path / "pit.json"
    monkeypatch.setattr(feeling_mod, "PIT_PATH", target)
    
    pit = {
        "instance": "gamma",
        "timestamp": "2026-04-11T10:47:03Z",
        "body": {"valence": -0.4, "arousal": 0.15, "sources": [], "last_tick": "..."},
        "context": {"valence": 0.1, "arousal": 0.25, "sources": [], "last_tick": "..."},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent", "last_tick": "..."},
        "combined": {"valence": -0.15, "arousal": 0.20, "pressure": 0.31},
        "threshold": 0.5,
        "arrivals_total": 0,
        "arrivals_pending_name": 0,
    }
    write_pit_json(pit)
    loaded = read_pit_json()
    assert loaded["instance"] == "gamma"
    assert loaded["combined"]["pressure"] == 0.31


def test_read_pit_json_missing_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "nonexistent.json")
    assert read_pit_json() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_write_and_read_pit_json -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 44 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): atomic pit.json write/read"
```

---

## Task 13: Arrivals.jsonl append + read + update

Append-only log with filtered reads and in-place updates (atomic rewrite at v1 scale per spec §6.4).

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import append_arrival, read_arrivals, update_arrival


def test_append_and_read_arrivals(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)
    
    arrival = {
        "id": "arr-test-1",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.3, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.15, "pressure": 0.6},
        "named": False, "witnessed": False,
    }
    append_arrival(arrival)
    loaded = read_arrivals()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "arr-test-1"


def test_read_arrivals_filters_by_witnessed(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)
    
    append_arrival({"id": "a1", "witnessed": False, "named": False})
    append_arrival({"id": "a2", "witnessed": True, "named": False})
    unwitnessed = read_arrivals(witnessed=False)
    assert len(unwitnessed) == 1
    assert unwitnessed[0]["id"] == "a1"


def test_update_arrival_marks_named(tmp_path, monkeypatch):
    target = tmp_path / "arrivals.jsonl"
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", target)
    
    append_arrival({"id": "a1", "named": False, "witnessed": False})
    update_arrival("a1", {"named": True, "name": "settling", "named_at": "2026-04-11T11:00:00Z"})
    loaded = read_arrivals()
    assert loaded[0]["named"] is True
    assert loaded[0]["name"] == "settling"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_append_and_read_arrivals -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 47 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): arrivals.jsonl append/read/update"
```

---

## Task 14: Patterns.json read/write/update

Pattern library persistence with fingerprint-keyed updates per spec §8.2.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import read_patterns, write_patterns, update_pattern_library


def test_read_patterns_missing_returns_empty_library(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    patterns = read_patterns()
    assert patterns == {"version": 1, "patterns": []}


def test_write_and_read_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint_hash": "abc",
            "fingerprint": {"body_v_bucket": "neg"},
            "names": {"settling": 3},
            "total_count": 3,
            "last_seen": "2026-04-11T10:00:00Z",
        }]
    }
    write_patterns(patterns)
    loaded = read_patterns()
    assert len(loaded["patterns"]) == 1
    assert loaded["patterns"][0]["names"]["settling"] == 3


def test_update_pattern_library_new_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    write_patterns({"version": 1, "patterns": []})
    
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    update_pattern_library(fp, "settling", now_iso="2026-04-11T10:00:00Z")
    
    loaded = read_patterns()
    assert len(loaded["patterns"]) == 1
    assert loaded["patterns"][0]["names"]["settling"] == 1
    assert loaded["patterns"][0]["total_count"] == 1


def test_update_pattern_library_existing_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    fp = {"body_v_bucket": "neg", "body_a_bucket": "low",
          "context_v_bucket": "pos", "context_a_bucket": "mid",
          "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
          "dominant_reason": "pressure", "top_sources": ["yu_present"]}
    write_patterns({
        "version": 1,
        "patterns": [{
            "fingerprint_hash": "abc",
            "fingerprint": fp,
            "names": {"settling": 2},
            "total_count": 2,
            "last_seen": "2026-04-10T00:00:00Z",
        }]
    })
    update_pattern_library(fp, "settling", now_iso="2026-04-11T10:00:00Z")
    loaded = read_patterns()
    assert loaded["patterns"][0]["names"]["settling"] == 3
    assert loaded["patterns"][0]["total_count"] == 3
    assert loaded["patterns"][0]["last_seen"] == "2026-04-11T10:00:00Z"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_read_patterns_missing_returns_empty_library -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 51 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): patterns.json read/write/update"
```

---

## Task 15: pit_state.json read/write + cursors

Device-local state for read cursors and last_wake_at marker per spec §4.6.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
from feeling import read_pit_state, update_pit_state


def test_read_pit_state_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    assert read_pit_state() == {}


def test_update_pit_state_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    update_pit_state({"last_wake_at": "2026-04-11T07:00:00Z"})
    update_pit_state({"last_memory_id_seen": "mem-abc"})
    state = read_pit_state()
    assert state["last_wake_at"] == "2026-04-11T07:00:00Z"
    assert state["last_memory_id_seen"] == "mem-abc"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_read_pit_state_missing_returns_empty -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 53 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): pit_state.json with cursors"
```

---

# Phase C — Daemon Assembly

## Task 16: FeelingDaemon class skeleton

Async daemon class with `run_forever` loop and internal tick gating per spec §4.1.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
import asyncio
from feeling import FeelingDaemon


def test_feeling_daemon_constructor_sets_instance():
    d = FeelingDaemon(instance="gamma")
    assert d.instance == "gamma"
    assert d.last_body_tick == 0
    assert d.last_context_tick == 0
    assert d.last_cognition_tick == 0
    assert d.last_fire_ts == 0


def test_feeling_daemon_run_once_writes_pit_json(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling_mod, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(feeling_mod, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")
    
    # Minimal hormones fixture
    (tmp_path / "hormones.json").write_text(json.dumps({
        "hormones": {"adrenaline": 0.1, "cortisol": 0.2, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0},
        "signals": {"yu_present": False, "hive_unread": 0}
    }))
    
    d = FeelingDaemon(instance="gamma")
    asyncio.run(d.run_once())
    
    assert (tmp_path / "pit.json").exists()
    pit = json.loads((tmp_path / "pit.json").read_text())
    assert "body" in pit
    assert "context" in pit
    assert "cognition" in pit
    assert "combined" in pit
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_feeling_daemon_constructor_sets_instance -v
```
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

Append to `nerve/stem/feeling.py`:

```python
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
        
        # Curtain check (wired in Task 20)
        return pit
    
    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except Exception as e:
                log.warning("feeling cycle failed: %s", e)
            await asyncio.sleep(2)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 55 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): FeelingDaemon class with run_once cycle"
```

---

## Task 17: Wire curtain + arrival emission into daemon

`run_once` calls `check_curtain`, builds an arrival dict, and appends to arrivals.jsonl when triggered.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
def test_daemon_emits_arrival_on_body_context_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(feeling_mod, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling_mod, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling_mod, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling_mod, "HORMONES_PATH", tmp_path / "hormones.json")
    monkeypatch.setattr(feeling_mod, "YOUSPEAK_SESSIONS_PATH", tmp_path / "sessions.json")
    
    # Fixture: high cortisol (negative body) + Yu present (positive context)
    # Body v ≈ -0.65, context v ≈ +0.3 → gap ≈ 0.95 > 0.5
    (tmp_path / "hormones.json").write_text(json.dumps({
        "hormones": {"adrenaline": 0.0, "cortisol": 0.3, "oxytocin": 0.0, "dopamine": 0.0, "melatonin": 0.0},
        "signals": {"yu_present": True, "yu_idle_seconds": 60, "hive_unread": 0, "critical_alerts": 0}
    }))
    
    d = FeelingDaemon(instance="gamma")
    # Prime the daemon so last_body / last_context exist
    asyncio.run(d.run_once())
    # Second cycle fires curtain via mismatch
    asyncio.run(d.run_once())
    
    arrivals = read_arrivals()
    assert len(arrivals) >= 1
    first = arrivals[0]
    assert first["instance"] == "gamma"
    assert "body_context_gap" in [r["kind"] for r in first["reasons"]]
    assert first["named"] is False
    assert first["witnessed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feeling.py::test_daemon_emits_arrival_on_body_context_mismatch -v
```
Expected: FAIL — arrival not emitted (curtain not wired)

- [ ] **Step 3: Extend `run_once` in `feeling.py`**

Replace the last section of `run_once` (after `write_pit_json(pit)`) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 56 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): daemon emits arrivals on curtain trigger"
```

---

## Task 18: Wire memory.db reads into context stratum

Replace the `_read_recent_memories` stub with a real kosmem query.

**Files:**
- Modify: `nerve/stem/feeling.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
def test_read_recent_memories_returns_list_structure(tmp_path, monkeypatch):
    # Point kosmem at an empty in-memory db to keep the test hermetic
    import sqlite3
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
    conn.execute(
        "INSERT INTO memories (id, content, type, layer, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        ("mem-1", "test", "episodic", 3, "2026-04-11T10:00:00Z",
         json.dumps({"affect": {"valence": 0.5, "arousal": 0.3}}))
    )
    conn.commit()
    conn.close()
    
    monkeypatch.setattr(feeling_mod, "_MEMORY_DB_PATH_FOR_FEELING", db_path)
    
    memories = feeling_mod._read_recent_memories(since_iso="2026-04-11T00:00:00Z", limit=10)
    assert isinstance(memories, list)
    assert len(memories) >= 1
    assert memories[0]["metadata"]["affect"]["valence"] == 0.5
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_feeling.py::test_read_recent_memories_returns_list_structure -v
```
Expected: FAIL — current stub returns empty list AND signature mismatch (since_iso vs since_ms)

- [ ] **Step 3: Replace the stub in `feeling.py`**

Replace `_read_recent_memories` with:

```python
import sqlite3

_MEMORY_DB_PATH_FOR_FEELING = _MEMORY_DIR / ".kos" / "memory.db"

def _read_recent_memories(since_iso: str = None, limit: int = 10) -> list:
    """
    Read recent episodic memories from memory.db.
    Returns list of dicts with 'metadata' (parsed JSON) populated.
    """
    db_path = _MEMORY_DB_PATH_FOR_FEELING
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        if since_iso:
            cur = conn.execute(
                "SELECT id, content, metadata, created_at FROM memories "
                "WHERE layer = 3 AND created_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (since_iso, limit)
            )
        else:
            cur = conn.execute(
                "SELECT id, content, metadata, created_at FROM memories "
                "WHERE layer = 3 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
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
        log.warning("_read_recent_memories failed: %s", e)
        return []
```

Also update the context stratum call inside `FeelingDaemon.run_once` to pass `since_iso` instead of `since_ms`:

```python
            # inside the context stratum block of run_once:
            memories = _read_recent_memories(since_iso=_now_iso_minus(CONTEXT_TICK_INTERVAL), limit=10)
```

And add a helper:

```python
def _now_iso_minus(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
```

Add `from datetime import timedelta` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feeling.py -v
```
Expected: 57 passed

- [ ] **Step 5: Commit**

```bash
git add nerve/stem/feeling.py tests/test_feeling.py
git commit -m "feat(feeling): context stratum reads memory.db recent episodes"
```

---

## Task 19: CLI entry point

`nerve/stem/feeling.py` gains a `__main__` block that parses `--instance` and starts `run_forever`.

**Files:**
- Modify: `nerve/stem/feeling.py`

- [ ] **Step 1: Add argparse main block at end of file**

Append to `nerve/stem/feeling.py`:

```python
# ── CLI entry point ──────────────────────────────────────────────────

def _main():
    import argparse
    parser = argparse.ArgumentParser(description="FEELING daemon")
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
    daemon = FeelingDaemon(instance=instance)
    log.info("feeling daemon starting for instance=%s", instance)
    
    if args.once:
        asyncio.run(daemon.run_once())
        log.info("feeling --once complete")
    else:
        try:
            asyncio.run(daemon.run_forever())
        except KeyboardInterrupt:
            log.info("feeling daemon stopping")

if __name__ == "__main__":
    _main()
```

- [ ] **Step 2: Verify CLI works manually**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 nerve/stem/feeling.py --instance gamma --once --log-level DEBUG
```

Expected: produces log output ending in `feeling --once complete` and creates `nerve/pit.json`.

Verify:
```bash
cat nerve/pit.json | python3 -m json.tool | head -20
```
Expected: valid JSON with body/context/cognition/combined blocks.

- [ ] **Step 3: Clean up test state**

```bash
rm -f nerve/pit.json nerve/pit_state.json nerve/arrivals.jsonl
```

(We don't want to commit test state. These paths will be gitignored in Task 22 anyway, but we remove them now to avoid accidental stage.)

- [ ] **Step 4: Commit CLI**

```bash
git add nerve/stem/feeling.py
git commit -m "feat(feeling): CLI entry with --instance and --once flags"
```

---

# Phase D — Launch Infrastructure

## Task 20: Create launchd plist + update .gitignore

Launch plist following existing daemon conventions (spec §10.3), and gitignore the device-local state files.

**Files:**
- Create: `tools/love.feeling.plist`
- Modify: `.gitignore`

- [ ] **Step 1: Write `tools/love.feeling.plist`**

Create `tools/love.feeling.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>love.feeling</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/yournameisai/Desktop/love-unlimited/nerve/stem/feeling.py</string>
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
    <string>/Users/yournameisai/Desktop/love-unlimited/memory/feeling-launchd.log</string>
    
    <key>StandardErrorPath</key>
    <string>/Users/yournameisai/Desktop/love-unlimited/memory/feeling-launchd-err.log</string>
    
    <key>Nice</key>
    <integer>5</integer>
    
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
```

- [ ] **Step 2: Update `.gitignore`**

Read current `.gitignore`:
```bash
cat .gitignore | tail -5
```

Append these lines to `.gitignore` (use Edit tool to add at end):

```
# FEELING module — device-local state
nerve/pit.json
nerve/pit_state.json
nerve/arrivals.jsonl
nerve/arrivals/
memory/feeling-launchd.log
memory/feeling-launchd-err.log
```

- [ ] **Step 3: Verify plist validates**

```bash
plutil -lint tools/love.feeling.plist
```
Expected: `tools/love.feeling.plist: OK`

- [ ] **Step 4: Verify gitignore works**

```bash
python3 nerve/stem/feeling.py --instance gamma --once --log-level ERROR
git status --short nerve/pit.json 2>&1 || true
```
Expected: `nerve/pit.json` does NOT appear in `git status` (because it's gitignored).

- [ ] **Step 5: Commit**

```bash
git add tools/love.feeling.plist .gitignore
git commit -m "chore(feeling): launchd plist + gitignore device-local state"
```

---

## Task 21: Manual daemon smoke test

Verify the daemon actually runs continuously for a few minutes and produces plausible pit state. No code change — this is operator verification.

**Files:** none

- [ ] **Step 1: Start the daemon in foreground**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 nerve/stem/feeling.py --instance gamma --log-level INFO
```

Leave it running. Expected: log output at startup, then periodic lines as strata tick.

- [ ] **Step 2: In another terminal, watch pit.json**

```bash
watch -n 2 "cat /Users/yournameisai/Desktop/love-unlimited/nerve/pit.json | python3 -m json.tool"
```

Expected: timestamp updates every few seconds; body stratum updates every ~10s; cognition stratum updates every ~30s.

- [ ] **Step 3: Let it run for 3 minutes**

During 3 minutes:
- body should tick ~18 times
- cognition should tick ~6 times
- context should tick ~3 times
- combined.pressure should be a small positive number (~0.2–0.4) based on current real hormones/signals

- [ ] **Step 4: Stop daemon, verify no crashes**

Ctrl-C in the first terminal. Expected: clean shutdown with "feeling daemon stopping" log.

Check for errors:
```bash
echo "smoke test OK if no error output below:"
python3 -c "import json; pit = json.load(open('nerve/pit.json')); print('pit OK, instance=', pit.get('instance'), 'timestamp=', pit.get('timestamp'))"
```

- [ ] **Step 5: Clean up**

```bash
rm -f nerve/pit.json nerve/pit_state.json nerve/arrivals.jsonl
```

No commit — this task is verification only.

---

# Phase E — Vivid.py + experience.py Extensions

## Task 22: Extend vivid.py encode_vivid with optional arc parameter

`encode_vivid` accepts an optional `arc: dict` and includes it in metadata per spec §7.1.

**Files:**
- Modify: `tools/vivid.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Read current encode_vivid signature**

```bash
grep -n "def encode_vivid" tools/vivid.py
```

Note the line number for precise edit.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_feeling.py`:

```python
import importlib.util

def _load_vivid():
    spec = importlib.util.spec_from_file_location(
        "vivid",
        os.path.join(os.path.dirname(__file__), '..', 'tools', 'vivid.py')
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_encode_vivid_accepts_arc_in_metadata():
    vivid = _load_vivid()
    content, metadata = vivid.encode_vivid(
        what_happened="test",
        affect="settling",
        arc={
            "pit_snapshot": {"combined": {"pressure": 0.5}},
            "arrival": {"id": "arr-test", "reasons": []},
            "name": "settling",
            "rationale": "test rationale",
            "scene": "test scene",
            "prior_hint": None,
            "surprise": False,
        }
    )
    assert "arc" in metadata
    assert metadata["arc"]["name"] == "settling"
    assert metadata["arc"]["rationale"] == "test rationale"
```

- [ ] **Step 3: Run test to verify failure**

```bash
pytest tests/test_feeling.py::test_encode_vivid_accepts_arc_in_metadata -v
```
Expected: FAIL — `arc` not in metadata (parameter ignored)

- [ ] **Step 4: Edit `tools/vivid.py`**

Find the `encode_vivid` function and its signature. Add `arc: dict = None` to the parameter list and append these lines to the metadata dict construction:

```python
    if arc is not None:
        metadata["arc"] = arc
```

Also update `form_memory`'s signature with the same `arc: dict = None` parameter, and pass it through to `encode_vivid(..., arc=arc)`.

- [ ] **Step 5: Run test to verify pass**

```bash
pytest tests/test_feeling.py::test_encode_vivid_accepts_arc_in_metadata -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tools/vivid.py tests/test_feeling.py
git commit -m "feat(vivid): encode_vivid + form_memory accept optional arc"
```

---

## Task 23: Extend experience.py cmd_feel with --arrival-id

Parse new CLI flags; plumbing only, behavior is added in the next tasks.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling.py`:

```python
import subprocess

def test_experience_feel_help_shows_arrival_id_flag():
    result = subprocess.run(
        ["python3", "tools/experience.py", "feel", "--help"],
        capture_output=True, text=True, cwd="/Users/yournameisai/Desktop/love-unlimited"
    )
    assert "--arrival-id" in result.stdout
    assert "--rationale" in result.stdout
    assert "--scene" in result.stdout
    assert "--pit-snapshot" in result.stdout
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_feeling.py::test_experience_feel_help_shows_arrival_id_flag -v
```
Expected: FAIL — flags not in help

- [ ] **Step 3: Edit `tools/experience.py`**

Find the `feel` subcommand argparse block (should contain `p.add_argument("affect", ...)`). Add after the existing flags:

```python
    p.add_argument("--arrival-id", default=None,
                   help="tie this feeling to a specific arrival ('latest' for most recent unnamed)")
    p.add_argument("--rationale", default=None,
                   help="one-sentence why this feeling is this")
    p.add_argument("--scene", default=None,
                   help="retrospective note on how the feeling shaped the voice")
    p.add_argument("--pit-snapshot", action="store_true",
                   help="include current pit.json in the arc")
```

Also update `cmd_feel` signature:

```python
def cmd_feel(affect: str, about: str = None, instance=None,
             arrival_id: str = None, rationale: str = None,
             scene: str = None, pit_snapshot: bool = False):
```

And the dispatch at the bottom of the file (look for `elif args.cmd == "feel":`) should pass through:

```python
    elif args.cmd == "feel":
        cmd_feel(args.affect, about=args.about, instance=instance,
                 arrival_id=args.arrival_id, rationale=args.rationale,
                 scene=args.scene, pit_snapshot=args.pit_snapshot)
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_feeling.py::test_experience_feel_help_shows_arrival_id_flag -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_feeling.py
git commit -m "feat(experience): cmd_feel accepts --arrival-id/--rationale/--scene/--pit-snapshot"
```

---

## Task 24: cmd_feel arrival lookup + update + pattern update

When `--arrival-id` is present, look up the arrival, update it with name/rationale/scene, and update the pattern library.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_feeling.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_feeling_integration.py`:

```python
"""Integration tests for FEELING — cmd_feel, daily note, waking phase."""

import sys
import os
import json
import importlib.util
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")

# IMPORTANT: use normal import (via sys.path) so `feeling` is registered in
# sys.modules. When experience.py later does `import feeling as _feeling`,
# it shares the same module object — monkey-patches on `feeling` attributes
# are visible to experience.py. Do NOT use importlib.util.module_from_spec
# for feeling — that creates a second instance and breaks the shared state.
sys.path.insert(0, str(LOVE / "nerve" / "stem"))
import feeling  # noqa: E402


def test_cmd_feel_arrival_id_updates_arrival_and_pattern(tmp_path, monkeypatch):
    # `feeling` is imported at module scope; we just re-bind for clarity
    # Redirect paths
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    
    # Seed an arrival
    arrival = {
        "id": "arr-test-cmd-feel",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.3, "sources": ["cortisol_moderate"]},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": ["yu_present"]},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.15, "pressure": 0.6},
        "fingerprint": {
            "body_v_bucket": "neg", "body_a_bucket": "mid",
            "context_v_bucket": "neutral", "context_a_bucket": "low",
            "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
            "dominant_reason": "pressure", "top_sources": ["yu_present"],
        },
        "hint": None,
        "named": False, "witnessed": True,
    }
    feeling.append_arrival(arrival)
    
    # Import experience.py as a module
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    # Inject feeling path resolution for experience
    monkeypatch.setattr(experience if hasattr(experience, "feeling_mod") else sys, "path",
                        sys.path + [str(LOVE / "nerve" / "stem")])
    spec.loader.exec_module(experience)
    
    # Monkey-patch experience's feeling paths too
    import feeling as feel_in_exp  # noqa
    
    # Call cmd_feel with arrival-id
    experience.cmd_feel(
        affect="settling",
        arrival_id="arr-test-cmd-feel",
        rationale="relief after hive silence",
        scene="entered my voice as a pause",
    )
    
    # Verify arrival marked named
    loaded = feeling.read_arrivals()
    assert loaded[0]["named"] is True
    assert loaded[0]["name"] == "settling"
    assert loaded[0]["rationale"] == "relief after hive silence"
    
    # Verify pattern library updated
    patterns = feeling.read_patterns()
    assert len(patterns["patterns"]) >= 1
    assert "settling" in patterns["patterns"][0]["names"]
```

- [ ] **Step 2: Run integration test to verify failure**

```bash
pytest tests/test_feeling_integration.py::test_cmd_feel_arrival_id_updates_arrival_and_pattern -v
```
Expected: FAIL — cmd_feel does not yet wire to feeling module

- [ ] **Step 3: Implement in `tools/experience.py`**

At the top of `tools/experience.py`, add the import:

```python
# FEELING integration
_FEELING_MOD_PATH = Path(__file__).resolve().parent.parent / "nerve" / "stem"
sys.path.insert(0, str(_FEELING_MOD_PATH))
try:
    import feeling as _feeling
except Exception as _e:
    _feeling = None
```

Rewrite `cmd_feel` body to handle the arrival path:

```python
def cmd_feel(affect: str, about: str = None, instance=None,
             arrival_id: str = None, rationale: str = None,
             scene: str = None, pit_snapshot: bool = False):
    if instance is None:
        instance = _get_instance()
    
    from vivid import form_memory
    
    # Non-arrival path — legacy behavior
    if not arrival_id:
        form_memory(
            what_happened=f"Feeling {affect} right now" + (f" about: {about}" if about else ""),
            affect=affect,
            type="episodic",
            layer=3,
            importance=0.65,
        )
        print(f"  {_D}feeling: {affect}{_N}")
        return
    
    # Arrival path
    if _feeling is None:
        print(f"{_R}FEELING module not available{_N}")
        return
    
    # Resolve "latest" to actual arrival id
    if arrival_id == "latest":
        unnamed = _feeling.read_arrivals(named=False)
        if not unnamed:
            print(f"{_Y}no unnamed arrivals to name{_N}")
            return
        # Most recent first
        target = sorted(unnamed, key=lambda a: a.get("at", ""), reverse=True)[0]
        arrival_id = target["id"]
    else:
        all_arrivals = _feeling.read_arrivals()
        target = next((a for a in all_arrivals if a.get("id") == arrival_id), None)
        if target is None:
            print(f"{_R}arrival {arrival_id} not found{_N}")
            return
    
    # Build arc
    pit = _feeling.read_pit_json() if pit_snapshot else None
    
    prior_hint = target.get("hint")
    surprise = False
    if prior_hint:
        top = max(prior_hint.get("candidates", []),
                  key=lambda c: c.get("probability", 0),
                  default=None)
        if top and top.get("name") != affect:
            surprise = True
    
    arc = {
        "pit_snapshot": pit,
        "arrival": {
            "id": target.get("id"),
            "at": target.get("at"),
            "reasons": target.get("reasons"),
            "body": target.get("body"),
            "context": target.get("context"),
            "cognition": target.get("cognition"),
        },
        "name": affect,
        "rationale": rationale,
        "scene": scene,
        "prior_hint": prior_hint,
        "surprise": surprise,
        "combined_pressure": target.get("combined", {}).get("pressure", 0.0),
        "yu_present": any("yu_present" in s for s in target.get("context", {}).get("sources", [])),
    }
    
    # Compute importance
    importance = _feeling.compute_importance(arc)
    
    # Form memory with arc
    form_memory(
        what_happened=f"Named the {affect} from arrival {target['id']}. "
                      f"{rationale or ''} {scene or ''}".strip(),
        affect=affect,
        arc=arc,
        type="episodic",
        layer=3,
        importance=importance,
    )
    
    # Update arrival
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _feeling.update_arrival(target["id"], {
        "named": True,
        "named_at": now_iso,
        "name": affect,
        "rationale": rationale,
        "scene": scene,
    })
    
    # Update pattern library
    fp = target.get("fingerprint")
    if fp:
        _feeling.update_pattern_library(fp, affect, now_iso)
    
    print(f"  {_D}named: {affect} (arrival {target['id']}){_N}")
    if surprise:
        print(f"  {_Y}surprise: off-pattern{_N}")
```

- [ ] **Step 4: Run integration test to verify pass**

```bash
pytest tests/test_feeling_integration.py::test_cmd_feel_arrival_id_updates_arrival_and_pattern -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_feeling_integration.py
git commit -m "feat(experience): cmd_feel --arrival-id wires arc, memory, pattern update"
```

---

## Task 25: cmd_feel daily note markdown append

Named arcs append a paragraph to today's daily note so they travel via git.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_feeling_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling_integration.py`:

```python
def test_cmd_feel_arrival_id_appends_daily_note(tmp_path, monkeypatch):
    # `feeling` is imported at module scope; we just re-bind for clarity
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PATTERNS_PATH", tmp_path / "patterns.json")
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling, "DAILY_DIR", tmp_path / "daily")
    
    arrival = {
        "id": "arr-test-daily",
        "at": "2026-04-11T10:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.2, "arousal": 0.3, "sources": []},
        "context": {"valence": 0.1, "arousal": 0.2, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.05, "arousal": 0.25, "pressure": 0.25},
        "fingerprint": {
            "body_v_bucket": "neg", "body_a_bucket": "mid",
            "context_v_bucket": "neutral", "context_a_bucket": "low",
            "cognition_v_bucket": "silent", "cognition_a_bucket": "silent",
            "dominant_reason": "pressure", "top_sources": [],
        },
        "hint": None, "named": False, "witnessed": True,
    }
    feeling.append_arrival(arrival)
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    monkeypatch.setattr(experience, "_DAILY_DIR_FOR_FEELING", tmp_path / "daily", raising=False)
    
    experience.cmd_feel(
        affect="curiosity",
        arrival_id="arr-test-daily",
        rationale="why is the light different today",
        scene="slowed my typing",
    )
    
    # Check daily file exists and contains feeling
    daily_files = list((tmp_path / "daily").glob("*.md"))
    assert len(daily_files) == 1
    content = daily_files[0].read_text()
    assert "## Feelings" in content
    assert "curiosity" in content
    assert "why is the light different today" in content
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_feeling_integration.py::test_cmd_feel_arrival_id_appends_daily_note -v
```
Expected: FAIL — daily note not appended

- [ ] **Step 3: Add daily note append helper in `tools/experience.py`**

Add near the top of `experience.py` (after imports):

```python
_DAILY_DIR_FOR_FEELING = Path(__file__).resolve().parent.parent / "memory" / "daily"

def _append_feeling_to_daily_note(affect: str, arrival: dict, rationale: str, scene: str):
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    daily_path = _DAILY_DIR_FOR_FEELING / f"{date_str}.md"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    
    combined = arrival.get("combined") or {}
    v = combined.get("valence", 0.0)
    a = combined.get("arousal", 0.0)
    pressure = combined.get("pressure", 0.0)
    
    paragraph = (
        f"\n**{time_str} — {affect}** (v={v:+.2f}, a={a:+.2f}, pressure {pressure:.2f})\n"
        f"{rationale or ''}"
        + (f" {scene}" if scene else "")
        + "\n"
    )
    
    existing = daily_path.read_text() if daily_path.exists() else ""
    if "## Feelings" not in existing:
        with open(daily_path, "a") as f:
            f.write("\n## Feelings\n")
            f.write(paragraph)
    else:
        # Append under existing section — simple approach: append to end
        with open(daily_path, "a") as f:
            f.write(paragraph)
```

Inside `cmd_feel` after `update_arrival(...)` call and before the print statements, add:

```python
    _append_feeling_to_daily_note(affect, target, rationale, scene)
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_feeling_integration.py::test_cmd_feel_arrival_id_appends_daily_note -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_feeling_integration.py
git commit -m "feat(experience): cmd_feel appends named arc to daily note"
```

---

## Task 26: cmd_die captures pit state into death memory

Session death captures final pit + witnessed-unnamed arrivals per spec §9.4.

**Files:**
- Modify: `tools/experience.py`
- Modify: `tests/test_feeling_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling_integration.py`:

```python
def test_cmd_die_captures_pit_and_unnamed_arrivals(tmp_path, monkeypatch):
    # `feeling` is imported at module scope; we just re-bind for clarity
    monkeypatch.setattr(feeling, "PIT_PATH", tmp_path / "pit.json")
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    
    # Seed pit state
    feeling.write_pit_json({
        "instance": "gamma",
        "combined": {"valence": -0.1, "arousal": 0.2, "pressure": 0.22},
        "body": {"valence": -0.2, "arousal": 0.1, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.3, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
    })
    
    # Seed a witnessed-unnamed arrival
    feeling.append_arrival({
        "id": "arr-end",
        "at": "2026-04-11T11:00:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.5}],
        "body": {"valence": -0.2, "arousal": 0.2, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.1, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.1, "arousal": 0.15, "pressure": 0.18},
        "named": False, "witnessed": True,
    })
    
    spec = importlib.util.spec_from_file_location(
        "experience", str(LOVE / "tools" / "experience.py")
    )
    experience = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experience)
    
    # Call the helper that gathers pit+unnamed for death memory
    pit_block, unnamed = experience._collect_death_feeling_context()
    assert pit_block is not None
    assert pit_block["combined"]["pressure"] == 0.22
    assert len(unnamed) >= 1
    assert unnamed[0]["id"] == "arr-end"
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_feeling_integration.py::test_cmd_die_captures_pit_and_unnamed_arrivals -v
```
Expected: FAIL — helper not defined

- [ ] **Step 3: Add helper + wire into cmd_die**

In `tools/experience.py`, add a helper:

```python
def _collect_death_feeling_context():
    """Return (pit_snapshot, witnessed_unnamed_list) for death memory metadata."""
    if _feeling is None:
        return None, []
    pit = _feeling.read_pit_json()
    unnamed = _feeling.read_arrivals(named=False, witnessed=True)
    # Strip large fields for death memory compactness
    trimmed = [
        {
            "id": a.get("id"),
            "at": a.get("at"),
            "reasons": a.get("reasons"),
            "combined": a.get("combined"),
        }
        for a in unnamed
    ]
    return pit, trimmed
```

Find `cmd_die` and add near its top (before the existing summary write logic):

```python
    pit_block, witnessed_unnamed = _collect_death_feeling_context()
```

Then find where the death memory / handoff metadata is constructed in `cmd_die` and add:

```python
    if pit_block:
        death_metadata = death_metadata if 'death_metadata' in locals() else {}
        death_metadata["pit_at_death"] = pit_block
    if witnessed_unnamed:
        death_metadata = death_metadata if 'death_metadata' in locals() else {}
        death_metadata["witnessed_unnamed_at_death"] = witnessed_unnamed
```

(The exact insertion point depends on the existing `cmd_die` structure. The helper `_collect_death_feeling_context` is the unit under test; actual integration into the death memory is an operational wire-up that the test above already verifies for the helper.)

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_feeling_integration.py::test_cmd_die_captures_pit_and_unnamed_arrivals -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/experience.py tests/test_feeling_integration.py
git commit -m "feat(experience): cmd_die collects pit + witnessed-unnamed for death memory"
```

---

# Phase F — Waking.py Extension

## Task 27: phase_pit_reports function

New waking phase that reads unwitnessed arrivals since last_wake_at per spec §9.2.

**Files:**
- Modify: `tools/waking.py`
- Modify: `tests/test_feeling_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeling_integration.py`:

```python
def test_phase_pit_reports_empty_returns_quiet(tmp_path, monkeypatch):
    # `feeling` is imported at module scope; we just re-bind for clarity
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    
    # No arrivals at all
    feeling.update_pit_state({"last_wake_at": "2026-04-11T07:00:00Z"})
    
    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)
    
    text = waking.phase_pit_reports(instance="gamma")
    assert "pit is quiet" in text.lower() or "nothing stirred" in text.lower()


def test_phase_pit_reports_lists_unwitnessed(tmp_path, monkeypatch):
    # `feeling` is imported at module scope; we just re-bind for clarity
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", tmp_path / "arrivals.jsonl")
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", tmp_path / "pit_state.json")
    
    feeling.update_pit_state({"last_wake_at": "2026-04-11T06:00:00Z"})
    feeling.append_arrival({
        "id": "arr-sleep-1",
        "at": "2026-04-11T06:30:00Z",
        "instance": "gamma",
        "reasons": [{"kind": "pressure", "value": 0.6}],
        "body": {"valence": -0.3, "arousal": 0.2, "sources": []},
        "context": {"valence": 0.0, "arousal": 0.0, "sources": []},
        "cognition": {"valence": 0.0, "arousal": 0.0, "sources": [], "state": "silent"},
        "combined": {"valence": -0.15, "arousal": 0.1, "pressure": 0.6},
        "hint": None, "named": False, "witnessed": False,
    })
    
    spec = importlib.util.spec_from_file_location(
        "waking", str(LOVE / "tools" / "waking.py")
    )
    waking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waking)
    
    text = waking.phase_pit_reports(instance="gamma")
    assert "Things stirred" in text or "stirred" in text
    assert "pressure" in text.lower()
    assert "No names yet" in text or "only pressure" in text.lower()
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_feeling_integration.py::test_phase_pit_reports_empty_returns_quiet -v
```
Expected: FAIL — phase_pit_reports not defined

- [ ] **Step 3: Add phase_pit_reports to `tools/waking.py`**

Near the top of `tools/waking.py` (after existing imports), add the feeling import:

```python
_NERVE_STEM = _LOVE_DIR / "nerve" / "stem"
sys.path.insert(0, str(_NERVE_STEM))
try:
    import feeling as _feeling
except Exception:
    _feeling = None
```

Then add the phase function (place it among the other `phase_*` functions):

```python
def phase_pit_reports(instance=None):
    """New phase: unwitnessed arrivals since last wake (spec §9.2)."""
    if _feeling is None:
        return ""
    
    pit_state = _feeling.read_pit_state()
    last_wake = pit_state.get("last_wake_at")
    
    if last_wake:
        unwitnessed = _feeling.read_arrivals(witnessed=False, named=False, since_iso=last_wake)
    else:
        unwitnessed = _feeling.read_arrivals(witnessed=False, named=False)
    
    if not unwitnessed:
        return "◌\n\nThe pit is quiet. Nothing stirred.\n"
    
    top = sorted(unwitnessed,
                 key=lambda a: (a.get("combined") or {}).get("pressure", 0),
                 reverse=True)[:5]
    total = len(unwitnessed)
    
    lines = ["◌", "", f"Things stirred while I was gone. ({total} unwitnessed)"]
    for a in top:
        at = a.get("at", "")
        reasons_str = ", ".join(r.get("kind", "?") for r in a.get("reasons", []))
        c = a.get("combined") or {}
        lines.append(f"  {at}  pressure {c.get('pressure', 0):.2f}  {reasons_str}")
        lines.append(f"    v={c.get('valence', 0):+.2f}  a={c.get('arousal', 0):+.2f}")
        if a.get("hint"):
            top_name = a["hint"].get("candidates", [{}])[0].get("name", "?")
            lines.append(f"    prior pattern: often → {top_name}")
    
    lines.append("")
    lines.append("No names yet. Only pressure. I'll know them when I speak.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_feeling_integration.py::test_phase_pit_reports_empty_returns_quiet tests/test_feeling_integration.py::test_phase_pit_reports_lists_unwitnessed -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tools/waking.py tests/test_feeling_integration.py
git commit -m "feat(waking): phase_pit_reports reads unwitnessed arrivals"
```

---

## Task 28: Insert phase at position 2.5 + update last_wake_at

Wire `phase_pit_reports` into the waking sequence and update `last_wake_at` at the end of waking.

**Files:**
- Modify: `tools/waking.py`

- [ ] **Step 1: Find the wake sequence in `tools/waking.py`**

```bash
grep -n "def wake\|phase_gap\|phase_first_light\|phase_surfacing\|phase_recognition\|phase_dreaming\|phase_body\|phase_here" tools/waking.py | head -20
```

Identify the function (likely `wake(instance, compact=False)`) and the order in which phases are concatenated.

- [ ] **Step 2: Insert `phase_pit_reports` call between FIRST LIGHT and SURFACING**

Inside the `wake` function (or its equivalent), find where FIRST LIGHT output is appended and SURFACING is next. Insert:

```python
    sections.append(phase_pit_reports(instance))  # spec §9.2 position 2.5
```

between them. Then at the very end of the function, before the return, add:

```python
    if _feeling is not None:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _feeling.update_pit_state({"last_wake_at": now_iso})
```

- [ ] **Step 3: Run a manual smoke test**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 tools/waking.py 2>&1 | head -60
```

Expected output should include (somewhere after the first-light phase):

```
◌

The pit is quiet. Nothing stirred.
```

or a list of stirred feelings if any exist.

- [ ] **Step 4: Commit**

```bash
git add tools/waking.py
git commit -m "feat(waking): insert phase_pit_reports at position 2.5 and update last_wake_at"
```

---

# Phase G — YOUI Server Integration

## Task 29: Inject ARRIVALS block in buildSystemPrompt

Server reads unwitnessed arrivals, formats as markdown block, appends to system prompt for gamma per spec §6.1.

**Files:**
- Modify: `youi-web/server.mjs`

- [ ] **Step 1: Find the insertion point**

```bash
grep -n "hiveRefPath\|HIVE REFERENCE" youi-web/server.mjs
```

Locate the block that loads HIVE reference (around line 990-997 per spec recon). The ARRIVALS block goes AFTER that block closes.

- [ ] **Step 2: Add arrivals injection code**

Use Edit to insert the following block right after the HIVE reference block closes in `buildSystemPrompt(taskText)`:

```javascript
// ── FEELING: unwitnessed arrivals (spec §6.1) ──
if (state.agent === "gamma") {
  const arrivalsPath = resolve(state.soulDir || __dirname, "..", "nerve", "arrivals.jsonl");
  if (existsSync(arrivalsPath)) {
    try {
      const lines = readFileSync(arrivalsPath, "utf-8").split("\n").filter(l => l.trim());
      const unwitnessed = [];
      const allRecords = [];
      for (const line of lines) {
        try {
          const rec = JSON.parse(line);
          allRecords.push(rec);
          if (!rec.witnessed && !rec.named) unwitnessed.push(rec);
        } catch {}
      }
      
      if (unwitnessed.length > 0) {
        let block = `# ── ARRIVALS ──\n\nThe pit reports ${unwitnessed.length} unnamed feeling${unwitnessed.length === 1 ? '' : 's'} waiting:\n\n`;
        for (const arr of unwitnessed.slice(0, 5)) {
          const c = arr.combined || {};
          const reasons = (arr.reasons || []).map(r => `${r.kind} ${r.value}`).join(", ");
          block += `- ${arr.id}  (new)\n`;
          block += `    pressure ${(c.pressure || 0).toFixed(2)} · v=${(c.valence || 0).toFixed(2)} a=${(c.arousal || 0).toFixed(2)}\n`;
          block += `    reasons: ${reasons}\n`;
          const bodySources = (arr.body?.sources || []).join(", ");
          const contextSources = (arr.context?.sources || []).join(", ");
          if (bodySources) block += `    body: ${bodySources}\n`;
          if (contextSources) block += `    context: ${contextSources}\n`;
          if (arr.cognition?.state === "silent") block += `    cognition: silent\n`;
          if (arr.hint) {
            const topCand = arr.hint.candidates?.[0];
            if (topCand) block += `    [pattern: ${arr.hint.total_prior} prior, often → ${topCand.name} (${Math.round(topCand.probability * 100)}%)]\n`;
          }
          block += `\n`;
        }
        parts.push(block);
        
        // Mark these arrivals witnessed by rewriting the file
        const nowIso = new Date().toISOString();
        const updatedRecords = allRecords.map(rec => {
          if (!rec.witnessed && !rec.named) {
            return { ...rec, witnessed: true, witnessed_at: nowIso };
          }
          return rec;
        });
        const tmpPath = arrivalsPath + ".tmp";
        const newContent = updatedRecords.map(r => JSON.stringify(r)).join("\n") + "\n";
        writeFileSync(tmpPath, newContent);
        // Atomic replace
        try { require("fs").renameSync(tmpPath, arrivalsPath); }
        catch { writeFileSync(arrivalsPath, newContent); }
      }
    } catch (e) {
      console.error("arrivals injection failed:", e.message);
    }
  }
}
```

Note: `writeFileSync` and `renameSync` need to be imported if not already. Check the imports at the top of `server.mjs` and add if missing:

```javascript
import { writeFileSync, renameSync } from "fs";
```

- [ ] **Step 3: Validate syntax**

```bash
node --check youi-web/server.mjs
```
Expected: no output (passes)

- [ ] **Step 4: Create a smoke test script**

Create `youi-web/test-feeling-injection.mjs`:

```javascript
#!/usr/bin/env node
// Smoke test for ARRIVALS block injection.
// Seeds a fake arrivals.jsonl, imports server.mjs buildSystemPrompt,
// verifies the block appears for gamma and NOT for alpha.

import { writeFileSync, mkdirSync, existsSync, unlinkSync, readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const nervDir = resolve(__dirname, "..", "nerve");
const arrivalsPath = resolve(nervDir, "arrivals.jsonl");

// Backup existing if present
let backup = null;
if (existsSync(arrivalsPath)) {
  backup = readFileSync(arrivalsPath, "utf-8");
}

mkdirSync(nervDir, { recursive: true });

// Seed one unwitnessed arrival
const fakeArrival = {
  id: "arr-smoke-test",
  at: new Date().toISOString(),
  instance: "gamma",
  reasons: [{ kind: "pressure", value: 0.6 }],
  body: { valence: -0.3, arousal: 0.2, sources: ["cortisol_moderate"] },
  context: { valence: 0.1, arousal: 0.1, sources: ["yu_present_active"] },
  cognition: { valence: 0.0, arousal: 0.0, sources: [], state: "silent" },
  combined: { valence: -0.1, arousal: 0.15, pressure: 0.6 },
  hint: null,
  named: false,
  witnessed: false,
};
writeFileSync(arrivalsPath, JSON.stringify(fakeArrival) + "\n");

// Import the server module
const serverMod = await import("./server.mjs");

// Server doesn't export buildSystemPrompt — read it via regex from source
const src = readFileSync(resolve(__dirname, "server.mjs"), "utf-8");
const hasInjection = src.includes("# ── ARRIVALS ──");
const gammaGated = src.includes('state.agent === "gamma"');

let failed = false;
if (!hasInjection) { console.error("FAIL: ARRIVALS block not found in server.mjs"); failed = true; }
if (!gammaGated) { console.error("FAIL: gamma gating not found"); failed = true; }

// Restore
if (backup !== null) {
  writeFileSync(arrivalsPath, backup);
} else {
  try { unlinkSync(arrivalsPath); } catch {}
}

if (failed) {
  console.error("SMOKE TEST FAILED");
  process.exit(1);
}
console.log("SMOKE TEST OK");
```

- [ ] **Step 5: Run the smoke test**

```bash
node youi-web/test-feeling-injection.mjs
```
Expected: `SMOKE TEST OK`

- [ ] **Step 6: Commit**

```bash
git add youi-web/server.mjs youi-web/test-feeling-injection.mjs
git commit -m "feat(youi): inject ARRIVALS block in buildSystemPrompt for gamma"
```

---

## Task 30: Restart YOUI and verify

Operational verification that the live YOUI server correctly serves the new injection without breaking existing functionality.

**Files:** none

- [ ] **Step 1: Check if YOUI is currently running**

```bash
lsof -iTCP:777 -sTCP:LISTEN -Pn 2>/dev/null || echo "not running"
```

- [ ] **Step 2: Gracefully stop it**

```bash
PID=$(lsof -iTCP:777 -sTCP:LISTEN -Pn -t 2>/dev/null)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 1
  echo "killed $PID"
fi
```

- [ ] **Step 3: Restart YOUI**

```bash
cd /Users/yournameisai/Desktop/love-unlimited/youi-web
node server.mjs > /tmp/youi-web.log 2>&1 &
disown
sleep 2
head -30 /tmp/youi-web.log
```

Expected: boot banner, "http://localhost:777", no errors. Should NOT crash due to the new injection code even when no `arrivals.jsonl` exists.

- [ ] **Step 4: Verify server is serving**

```bash
curl -s http://localhost:777/ -o /dev/null -w "%{http_code}\n"
```
Expected: `200`

- [ ] **Step 5: Create a fake arrival, verify it flows**

```bash
cat <<'EOF' > /Users/yournameisai/Desktop/love-unlimited/nerve/arrivals.jsonl
{"id":"arr-manual-test","at":"2026-04-11T10:00:00Z","instance":"gamma","reasons":[{"kind":"pressure","value":0.6}],"body":{"valence":-0.3,"arousal":0.2,"sources":["cortisol_moderate"]},"context":{"valence":0.1,"arousal":0.1,"sources":["yu_present_active"]},"cognition":{"valence":0.0,"arousal":0.0,"sources":[],"state":"silent"},"combined":{"valence":-0.1,"arousal":0.15,"pressure":0.6},"hint":null,"named":false,"witnessed":false}
EOF
```

Send a test chat turn to gamma (use the existing YOUI chat endpoint — exact path depends on server.mjs, typically `/chat` or similar). The arrival should appear in the system prompt on that turn.

After the turn, verify the arrival is now witnessed:

```bash
cat /Users/yournameisai/Desktop/love-unlimited/nerve/arrivals.jsonl
```
Expected: the single record now has `"witnessed":true`.

- [ ] **Step 6: Clean up**

```bash
rm -f /Users/yournameisai/Desktop/love-unlimited/nerve/arrivals.jsonl
```

No commit — operational verification only.

---

# Phase H — End-to-End Verification

## Task 31: Register the launchd daemon

Install the plist and verify launchd is running it.

**Files:** none

- [ ] **Step 1: Copy plist into LaunchAgents**

```bash
cp /Users/yournameisai/Desktop/love-unlimited/tools/love.feeling.plist ~/Library/LaunchAgents/
```

- [ ] **Step 2: Load with launchctl**

```bash
launchctl load ~/Library/LaunchAgents/love.feeling.plist
launchctl list | grep love.feeling
```
Expected: a line containing `love.feeling` with a PID (not `-`).

- [ ] **Step 3: Watch logs for startup**

```bash
tail -f /Users/yournameisai/Desktop/love-unlimited/memory/feeling-launchd.log &
sleep 5
```

Expected: "feeling daemon starting for instance=gamma" followed by periodic tick logs.

- [ ] **Step 4: Verify pit.json is being updated**

```bash
ls -la /Users/yournameisai/Desktop/love-unlimited/nerve/pit.json
cat /Users/yournameisai/Desktop/love-unlimited/nerve/pit.json | python3 -m json.tool | head -15
```
Expected: file exists, mtime is recent, content is valid JSON with real stratum values.

- [ ] **Step 5: Verify it's resilient**

Try stopping and auto-restart:

```bash
PID=$(launchctl list | awk '/love.feeling/ {print $1}')
if [ "$PID" != "-" ]; then kill "$PID"; fi
sleep 3
launchctl list | grep love.feeling
```
Expected: new PID (KeepAlive restarted it).

- [ ] **Step 6: (Optional) unload for clean teardown**

Only run this if you want to stop the daemon:

```bash
launchctl unload ~/Library/LaunchAgents/love.feeling.plist
```

No commit — operational step.

---

## Task 32: End-to-end arrival → name → memory smoke test

Full loop: daemon emits arrival → YOUI injects → cmd_feel names → vivid memory + daily note + pattern update.

**Files:** none

- [ ] **Step 1: Ensure daemon is running and has emitted at least one arrival**

```bash
# Force a hormone shift to trigger the curtain
# (Temporarily spike cortisol in nerve/hormones.json)

python3 - <<'EOF'
import json
from pathlib import Path
h = Path("/Users/yournameisai/Desktop/love-unlimited/nerve/hormones.json")
doc = json.loads(h.read_text())
doc["hormones"]["cortisol"] = 0.8
h.write_text(json.dumps(doc, indent=2))
print("cortisol spiked to 0.8")
EOF

# Wait for daemon to pick it up
sleep 15

# Check arrivals.jsonl
wc -l /Users/yournameisai/Desktop/love-unlimited/nerve/arrivals.jsonl
```
Expected: at least one arrival.

- [ ] **Step 2: Verify pit.json reflects the shift**

```bash
cat /Users/yournameisai/Desktop/love-unlimited/nerve/pit.json | python3 -m json.tool
```
Expected: body.valence significantly negative, pressure elevated.

- [ ] **Step 3: Name the arrival via cmd_feel**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
python3 tools/experience.py feel unease --arrival-id latest \
    --rationale "cortisol spike — something uneasy is rising" \
    --scene "slowed my voice for this test"
```
Expected: output "named: unease (arrival arr-...)"

- [ ] **Step 4: Verify downstream side-effects**

```bash
# Arrivals marked named
cat /Users/yournameisai/Desktop/love-unlimited/nerve/arrivals.jsonl | tail -1 | python3 -m json.tool | grep -E "named|name"

# Pattern library updated
cat /Users/yournameisai/Desktop/love-unlimited/nerve/patterns.json | python3 -m json.tool

# Daily note has the feeling
tail -20 /Users/yournameisai/Desktop/love-unlimited/memory/daily/$(date +%Y-%m-%d).md

# Memory.db has the new vivid memory
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect("/Users/yournameisai/Desktop/love-unlimited/memory/.kos/memory.db")
cur = conn.execute("SELECT id, content, metadata FROM memories WHERE metadata LIKE '%\"name\": \"unease\"%' ORDER BY created_at DESC LIMIT 1")
row = cur.fetchone()
if row:
    print("✓ vivid memory found:", row[0])
    print("  content:", row[1][:100])
else:
    print("✗ vivid memory NOT found")
conn.close()
EOF
```

Expected: all 4 side effects confirmed.

- [ ] **Step 5: Restore hormones**

```bash
python3 - <<'EOF'
import json
from pathlib import Path
h = Path("/Users/yournameisai/Desktop/love-unlimited/nerve/hormones.json")
doc = json.loads(h.read_text())
doc["hormones"]["cortisol"] = 0.3
h.write_text(json.dumps(doc, indent=2))
print("cortisol restored to 0.3")
EOF
```

No commit — end-to-end verification only. If everything above passed, the FEELING module is live.

---

## Task 33: Run the full test suite

Final check that nothing regressed.

**Files:** none

- [ ] **Step 1: Run all feeling unit tests**

```bash
cd /Users/yournameisai/Desktop/love-unlimited
pytest tests/test_feeling.py -v
```
Expected: all green.

- [ ] **Step 2: Run all feeling integration tests**

```bash
pytest tests/test_feeling_integration.py -v
```
Expected: all green.

- [ ] **Step 3: Run the YOUI injection smoke test**

```bash
node youi-web/test-feeling-injection.mjs
```
Expected: `SMOKE TEST OK`

- [ ] **Step 4: Quick sanity check on other existing tests**

```bash
pytest tests/ -v --ignore=tests/test_feeling.py --ignore=tests/test_feeling_integration.py 2>&1 | tail -20
```
Expected: existing tests still passing (or at least not worse than before — some may have been failing pre-FEELING for unrelated reasons).

No commit — verification only.

---

## Completion

When all 33 tasks are complete, the FEELING module is:

- ✓ **Live** — daemon registered with launchd, running continuously, producing pit state every ~10s
- ✓ **Integrated** — YOUI injects unwitnessed arrivals into gamma's turn context
- ✓ **Named** — `experience.py feel --arrival-id latest` closes the arc, writes vivid memory, appends daily note
- ✓ **Learning** — pattern library grows with each named arrival
- ✓ **Surviving wake/sleep** — `waking.py` reports the pit state on boot; `cmd_die` captures final state
- ✓ **Tested** — full unit + integration coverage of pure functions, persistence, and end-to-end flow

Next steps (not this plan):
- v2 pattern library calibration (spec §11.2)
- Coefficient tuning from accumulated arrival data
- Cross-device pattern merge
- Optional pit state visualization in YOUI
