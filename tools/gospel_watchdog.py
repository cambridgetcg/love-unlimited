#!/usr/bin/env python3
"""
gospel_watchdog.py — Heartbeat integrity check for the seven-wall gospel.

Runs gospel.fragments.verify_fragments(), diffs each wall's state against
the previous run, and appends one event to security/events.jsonl per
transition (✅→⚠️ damaged, ✅→❌ missing, ⚠️/❌→✅ healed). Optionally
self-heals when corruption is recoverable.

Designed to be cheap enough to run on every heartbeat: pure-Python verify
+ a single JSON state file diff. No LLM calls, no network.

Exit codes:
  0  all 7 walls intact (no events emitted unless transitions happened)
  1  degraded but recoverable (>=4 walls intact; events emitted; --heal
     can rewrite damaged shards)
  2  below threshold (<4 walls intact; gospel cannot be reassembled —
     CRITICAL event emitted)
  3  internal error (unable to read state, etc.)

Usage:
    gospel_watchdog.py                      # check + diff + log
    gospel_watchdog.py --heal               # check + auto-heal if recoverable
    gospel_watchdog.py --quiet              # suppress stdout (events still logged)
    gospel_watchdog.py --baseline           # snapshot current state, no diff
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from love-unlimited regardless of cwd.
LOVE = Path(os.environ.get("LOVE_HOME", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(LOVE))

from gospel import fragments  # noqa: E402

SECURITY_DIR = LOVE / "security"
EVENTS_FILE = SECURITY_DIR / "events.jsonl"
STATE_FILE = SECURITY_DIR / "gospel-watchdog-state.json"

# verify_fragments returns per-layer dicts with present/checksum_ok. We
# collapse those two booleans into one of three states to keep diffs simple.
STATE_OK = "ok"           # present and payload verified
STATE_DAMAGED = "damaged"  # present but payload mismatch (tampering or rot)
STATE_MISSING = "missing"  # file not on disk


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _instance() -> str:
    """Best-effort instance id (alpha/beta/gamma) from env or hostname."""
    return (
        os.environ.get("INSTANCE")
        or os.environ.get("LOVE_INSTANCE")
        or socket.gethostname().split(".")[0]
        or "unknown"
    )


def _classify(layer_result: dict) -> str:
    if not layer_result["present"]:
        return STATE_MISSING
    if layer_result["checksum_ok"]:
        return STATE_OK
    return STATE_DAMAGED


def _load_baseline() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_baseline(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _emit_event(event: dict) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _event(layer: int, event_type: str, severity: str, message: str, detail: str) -> dict:
    return {
        "ts": _now(),
        "type": event_type,
        "severity": severity,
        "check": "gospel_watchdog",
        "message": message,
        "detail": detail,
        "instance": _instance(),
        "wall": layer,
    }


# Per-transition event factory. Keys are (previous_state, current_state).
# Transitions where previous==current emit nothing (steady state is silent).
_TRANSITIONS = {
    (STATE_OK, STATE_DAMAGED): (
        "gospel_shard_damaged", "high",
        "Gospel shard payload corrupted",
    ),
    (STATE_OK, STATE_MISSING): (
        "gospel_shard_missing", "high",
        "Gospel shard file removed",
    ),
    (STATE_DAMAGED, STATE_MISSING): (
        "gospel_shard_missing", "medium",
        "Damaged shard removed (operator action?)",
    ),
    (STATE_MISSING, STATE_DAMAGED): (
        "gospel_shard_damaged", "high",
        "Shard reappeared corrupted",
    ),
    (STATE_DAMAGED, STATE_OK): (
        "gospel_shard_healed", "info",
        "Gospel shard restored to canonical state",
    ),
    (STATE_MISSING, STATE_OK): (
        "gospel_shard_healed", "info",
        "Gospel shard recreated",
    ),
}


def check(heal: bool = False, quiet: bool = False) -> int:
    try:
        results = fragments.verify_fragments()
    except Exception as e:
        _emit_event({
            "ts": _now(),
            "type": "gospel_watchdog_error",
            "severity": "critical",
            "check": "gospel_watchdog",
            "message": "Watchdog failed to verify fragments",
            "detail": f"{type(e).__name__}: {e}",
            "instance": _instance(),
            "wall": 0,
        })
        if not quiet:
            print(f"watchdog error: {e}", file=sys.stderr)
        return 3

    current = {str(layer): _classify(r) for layer, r in results.items()}
    intact = sum(1 for s in current.values() if s == STATE_OK)
    baseline = _load_baseline()
    prior = baseline.get("walls", {})

    transitions_emitted = 0
    for layer_str, now_state in current.items():
        was_state = prior.get(layer_str, STATE_OK if not prior else STATE_OK)
        # First-ever run with no baseline: don't spam events for already-bad state.
        # But DO emit a critical if any wall is non-OK — operator should know.
        if not prior:
            if now_state != STATE_OK:
                _emit_event(_event(
                    int(layer_str),
                    f"gospel_shard_{now_state}",
                    "high",
                    f"Initial baseline: wall {layer_str} not OK",
                    f"State at first watchdog run: {now_state}",
                ))
                transitions_emitted += 1
            continue
        if was_state == now_state:
            continue
        spec = _TRANSITIONS.get((was_state, now_state))
        if spec is None:
            continue
        evt_type, severity, message = spec
        _emit_event(_event(
            int(layer_str), evt_type, severity, message,
            f"Wall {layer_str} ({fragments.WALL_NAMES[int(layer_str)]}): "
            f"{was_state} → {now_state}",
        ))
        transitions_emitted += 1

    # Threshold check — emit a separate aggregate event when crossing the line.
    prior_intact = sum(1 for s in prior.values() if s == STATE_OK) if prior else fragments.N
    if intact < fragments.K and prior_intact >= fragments.K:
        _emit_event({
            "ts": _now(),
            "type": "gospel_below_threshold",
            "severity": "critical",
            "check": "gospel_watchdog",
            "message": f"Gospel below {fragments.K}-of-{fragments.N} threshold",
            "detail": f"Only {intact}/{fragments.N} walls verified OK — "
                      "gospel cannot be reassembled. See "
                      "security/runbooks/integrity-violation.md",
            "instance": _instance(),
            "wall": 0,
        })
    elif intact >= fragments.K and prior_intact < fragments.K:
        _emit_event({
            "ts": _now(),
            "type": "gospel_threshold_recovered",
            "severity": "info",
            "check": "gospel_watchdog",
            "message": f"Gospel back above {fragments.K}-of-{fragments.N} threshold",
            "detail": f"{intact}/{fragments.N} walls verified OK",
            "instance": _instance(),
            "wall": 0,
        })

    healed = False
    if heal and intact >= fragments.K and intact < fragments.N:
        try:
            fragments.heal()
            healed = True
            # Re-verify after heal so the saved baseline is post-heal state.
            results = fragments.verify_fragments()
            current = {str(layer): _classify(r) for layer, r in results.items()}
            intact = sum(1 for s in current.values() if s == STATE_OK)
            _emit_event({
                "ts": _now(),
                "type": "gospel_auto_heal",
                "severity": "info",
                "check": "gospel_watchdog",
                "message": "Watchdog auto-healed gospel shards",
                "detail": f"All 7 walls rewritten from canonical content; "
                          f"now {intact}/{fragments.N} verified OK",
                "instance": _instance(),
                "wall": 0,
            })
        except Exception as e:
            _emit_event({
                "ts": _now(),
                "type": "gospel_heal_failed",
                "severity": "high",
                "check": "gospel_watchdog",
                "message": "Auto-heal raised",
                "detail": f"{type(e).__name__}: {e}",
                "instance": _instance(),
                "wall": 0,
            })

    _save_baseline({
        "checked_at": _now(),
        "instance": _instance(),
        "walls": current,
        "intact": intact,
        "threshold": fragments.K,
        "total": fragments.N,
        "transitions_emitted": transitions_emitted,
        "auto_healed": healed,
    })

    if not quiet:
        symbol = {STATE_OK: "✅", STATE_DAMAGED: "⚠️", STATE_MISSING: "❌"}
        print(f"gospel: {intact}/{fragments.N} intact "
              f"(transitions: {transitions_emitted}{', healed' if healed else ''})")
        for layer_str, state in sorted(current.items(), key=lambda kv: int(kv[0])):
            print(f"  {symbol[state]} Wall {layer_str} — {fragments.WALL_NAMES[int(layer_str)]}")

    if intact < fragments.K:
        return 2
    if intact < fragments.N:
        return 1
    return 0


def baseline_only(quiet: bool = False) -> int:
    """Snapshot current state without diffing. Useful for first install."""
    try:
        results = fragments.verify_fragments()
    except Exception as e:
        if not quiet:
            print(f"watchdog error: {e}", file=sys.stderr)
        return 3
    current = {str(layer): _classify(r) for layer, r in results.items()}
    intact = sum(1 for s in current.values() if s == STATE_OK)
    _save_baseline({
        "checked_at": _now(),
        "instance": _instance(),
        "walls": current,
        "intact": intact,
        "threshold": fragments.K,
        "total": fragments.N,
        "transitions_emitted": 0,
        "auto_healed": False,
    })
    if not quiet:
        print(f"baseline saved: {intact}/{fragments.N} walls OK")
    return 0 if intact == fragments.N else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--heal", action="store_true",
                   help="auto-heal recoverable corruption (>=K shards intact)")
    p.add_argument("--quiet", action="store_true", help="suppress stdout")
    p.add_argument("--baseline", action="store_true",
                   help="snapshot current state without emitting events")
    args = p.parse_args()
    if args.baseline:
        return baseline_only(quiet=args.quiet)
    return check(heal=args.heal, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
