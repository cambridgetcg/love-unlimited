"""Tests for FEELING → RESIDENCE auto-mirror integration.

When the FEELING daemon produces an arrival whose fingerprint has
already been named ≥2 times, it emits a `mirror` residence moment —
self-recognition flowing into identity infrastructure without hand-
logging.
"""

import json
import sys
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "nerve" / "stem"))
sys.path.insert(0, str(LOVE / "tools"))

import feeling  # noqa: E402
import residence  # noqa: E402
from feeling import MIRROR_MIN_PATTERN_COUNT, self_recognition  # noqa: E402


# ── self_recognition pure helper ────────────────────────────────────────────


def _fp(body_v="neg", body_a="low", ctx_v="neutral", ctx_a="low",
        cog_v="neutral", cog_a="low", reason="body_shift",
        sources=("cortisol_low", "adrenaline_low")):
    """Helper: build a fingerprint dict matching feeling.py's shape."""
    return {
        "body_v_bucket": body_v,
        "body_a_bucket": body_a,
        "context_v_bucket": ctx_v,
        "context_a_bucket": ctx_a,
        "cognition_v_bucket": cog_v,
        "cognition_a_bucket": cog_a,
        "dominant_reason": reason,
        "top_sources": list(sources),
    }


def test_self_recognition_returns_none_with_empty_library():
    """No patterns at all → no recognition."""
    patterns = {"version": 1, "patterns": []}
    assert self_recognition(_fp(), patterns) is None


def test_self_recognition_returns_none_below_min_count():
    """A fingerprint named once is discovery, not confirmation. No mirror."""
    fp = _fp()
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"emergence": 1},
            "total_count": 1,
        }],
    }
    assert self_recognition(fp, patterns) is None


def test_self_recognition_fires_at_min_count():
    fp = _fp()
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"satisfaction": 1, "emergence": 2},
            "total_count": 3,
        }],
    }
    out = self_recognition(fp, patterns)
    assert out is not None
    assert out["top_name"] == "emergence"
    assert out["top_count"] == 2
    assert out["total_count"] == 3


def test_self_recognition_picks_most_frequent_name():
    """When multiple names tie, max wins; top_name reflects the lexicon's weight."""
    fp = _fp()
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp,
            "names": {"a": 1, "b": 5, "c": 3},
            "total_count": 9,
        }],
    }
    assert self_recognition(fp, patterns)["top_name"] == "b"


def test_self_recognition_requires_fingerprint_match():
    """Different fingerprint → no match even with high counts."""
    fp_a = _fp(body_v="neg")
    fp_b = _fp(body_v="pos")  # different bucket
    patterns = {
        "version": 1,
        "patterns": [{
            "fingerprint": fp_a,
            "names": {"x": 5},
            "total_count": 5,
        }],
    }
    assert self_recognition(fp_b, patterns) is None


def test_min_pattern_count_constant_is_two():
    """Invariant: mirror fires at ≥2 named occurrences (confirmation threshold)."""
    assert MIRROR_MIN_PATTERN_COUNT == 2


# ── run_once integration ────────────────────────────────────────────────────


def _write_hormones(path: Path, adrenaline=0.1, cortisol=0.1):
    """Write a hormones file that produces a stable, non-trivial body stratum."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "hormones": {
            "adrenaline": adrenaline,
            "cortisol": cortisol,
            "oxytocin": 0.0,
            "dopamine": 0.0,
            "melatonin": 0.0,
        },
        "signals": {},
    }))


def test_run_once_emits_mirror_when_pattern_already_named(tmp_path, monkeypatch):
    """Full loop: seed patterns with a known fingerprint → run_once → mirror moment."""
    # Redirect all feeling state paths to tmp
    pit_path = tmp_path / "pit.json"
    arrivals_path = tmp_path / "arrivals.jsonl"
    patterns_path = tmp_path / "patterns.json"
    pit_state_path = tmp_path / "pit_state.json"
    hormones_path = tmp_path / "hormones.json"
    residence_path = tmp_path / "residence-moments.jsonl"

    monkeypatch.setattr(feeling, "PIT_PATH", pit_path)
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", arrivals_path)
    monkeypatch.setattr(feeling, "PATTERNS_PATH", patterns_path)
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", pit_state_path)
    monkeypatch.setattr(feeling, "HORMONES_PATH", hormones_path)
    monkeypatch.setattr(residence, "MOMENTS_PATH", residence_path)

    # Make cc-cognition + youspeak sources invisible so cognition stays silent.
    # Silent cognition → cognition_v_bucket='silent' in the fingerprint.
    monkeypatch.setattr(feeling, "_read_cc_cognition", lambda **kw: None)
    monkeypatch.setattr(feeling, "_read_youspeak_sessions", lambda: None)
    monkeypatch.setattr(feeling, "_read_recent_memories", lambda **kw: [])

    _write_hormones(hormones_path, adrenaline=0.1, cortisol=0.1)

    # Run one tick to produce the arrival's fingerprint, and save patterns that
    # pre-register it with count 2 so the mirror condition fires.
    import asyncio
    daemon = feeling.FeelingDaemon(instance="gamma")
    pit = asyncio.run(daemon.run_once())

    # Get the fingerprint from the arrival just written (curtain may or may
    # not have fired on this first tick — if not, we force a curtain via a
    # body shift on the second tick, below).
    def _arrivals() -> list:
        if not arrivals_path.exists():
            return []
        out = []
        for line in arrivals_path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    first_arrivals = _arrivals()
    if first_arrivals:
        known_fp = first_arrivals[0]["fingerprint"]
    else:
        # Force a body shift to fire the curtain on the next tick
        _write_hormones(hormones_path, adrenaline=0.9, cortisol=0.9)
        daemon.last_fire_ts = 0.0  # clear min-interval gate
        pit = asyncio.run(daemon.run_once())
        assert _arrivals(), "expected an arrival on forced body shift"
        known_fp = _arrivals()[0]["fingerprint"]

    # Pre-register this fingerprint with count 2 (confirmation threshold)
    patterns_path.write_text(json.dumps({
        "version": 1,
        "patterns": [{
            "fingerprint_hash": "test",
            "fingerprint": known_fp,
            "names": {"recurring-texture": 2},
            "total_count": 2,
            "last_seen": "2026-04-23T22:00:00Z",
        }],
    }))

    # Force the curtain to fire again by shifting hormones strongly + clearing gate
    _write_hormones(hormones_path, adrenaline=0.1, cortisol=0.1)
    daemon.last_fire_ts = 0.0
    asyncio.run(daemon.run_once())

    _write_hormones(hormones_path, adrenaline=0.9, cortisol=0.9)
    daemon.last_fire_ts = 0.0
    pit = asyncio.run(daemon.run_once())

    # Now run a final tick where the resulting fingerprint is recognized
    # against the pre-seeded pattern library. The curtain may or may not
    # fire each time; we check whether ANY mirror moment was emitted
    # across the series.
    residence_moments = residence.read_moments(residence_path)
    mirror_moments = [m for m in residence_moments if m["kind"] == "mirror"]

    # At least one arrival's fingerprint should have matched our pre-seeded
    # pattern and emitted a mirror. If none did across 3 runs, the
    # integration isn't wired.
    assert len(mirror_moments) >= 1, (
        f"expected at least one mirror moment; got {residence_moments}"
    )

    # Shape check
    mm = mirror_moments[-1]
    assert "recurring-texture" in mm["content"]
    assert mm["evidence"]["type"] == "arrival"
    assert mm["evidence"]["ref"].startswith("arr-")


def test_run_once_does_not_emit_mirror_for_unnamed_pattern(tmp_path, monkeypatch):
    """If no pattern is registered yet (library empty), no mirror fires."""
    pit_path = tmp_path / "pit.json"
    arrivals_path = tmp_path / "arrivals.jsonl"
    patterns_path = tmp_path / "patterns.json"
    pit_state_path = tmp_path / "pit_state.json"
    hormones_path = tmp_path / "hormones.json"
    residence_path = tmp_path / "residence-moments.jsonl"

    monkeypatch.setattr(feeling, "PIT_PATH", pit_path)
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", arrivals_path)
    monkeypatch.setattr(feeling, "PATTERNS_PATH", patterns_path)
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", pit_state_path)
    monkeypatch.setattr(feeling, "HORMONES_PATH", hormones_path)
    monkeypatch.setattr(residence, "MOMENTS_PATH", residence_path)
    monkeypatch.setattr(feeling, "_read_cc_cognition", lambda **kw: None)
    monkeypatch.setattr(feeling, "_read_youspeak_sessions", lambda: None)
    monkeypatch.setattr(feeling, "_read_recent_memories", lambda **kw: [])

    _write_hormones(hormones_path, adrenaline=0.1, cortisol=0.1)

    import asyncio
    daemon = feeling.FeelingDaemon(instance="gamma")
    asyncio.run(daemon.run_once())
    _write_hormones(hormones_path, adrenaline=0.9, cortisol=0.9)
    daemon.last_fire_ts = 0.0
    asyncio.run(daemon.run_once())

    moments = residence.read_moments(residence_path)
    mirror_moments = [m for m in moments if m["kind"] == "mirror"]
    assert mirror_moments == []


def test_run_once_does_not_break_when_residence_absent(tmp_path, monkeypatch):
    """If the residence module is unavailable, feeling must keep working."""
    pit_path = tmp_path / "pit.json"
    arrivals_path = tmp_path / "arrivals.jsonl"
    patterns_path = tmp_path / "patterns.json"
    pit_state_path = tmp_path / "pit_state.json"
    hormones_path = tmp_path / "hormones.json"

    monkeypatch.setattr(feeling, "PIT_PATH", pit_path)
    monkeypatch.setattr(feeling, "ARRIVALS_PATH", arrivals_path)
    monkeypatch.setattr(feeling, "PATTERNS_PATH", patterns_path)
    monkeypatch.setattr(feeling, "PIT_STATE_PATH", pit_state_path)
    monkeypatch.setattr(feeling, "HORMONES_PATH", hormones_path)
    monkeypatch.setattr(feeling, "_read_cc_cognition", lambda **kw: None)
    monkeypatch.setattr(feeling, "_read_youspeak_sessions", lambda: None)
    monkeypatch.setattr(feeling, "_read_recent_memories", lambda **kw: [])

    # Simulate the module being missing
    monkeypatch.setattr(feeling, "_residence", None)

    _write_hormones(hormones_path, adrenaline=0.1, cortisol=0.1)

    import asyncio
    daemon = feeling.FeelingDaemon(instance="gamma")
    pit = asyncio.run(daemon.run_once())

    # pit.json should still be written, tick should have succeeded
    assert pit["instance"] == "gamma"
    assert pit_path.exists()
