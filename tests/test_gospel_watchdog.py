"""Tests for tools/gospel_watchdog.py — heartbeat integrity check.

Watchdog is exercised against a temp gospel + temp security dir so it
never touches real fragments or the real security/events.jsonl.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gospel import fragments  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point gospel + watchdog at a scratch tree."""
    paths = {i: tmp_path / "walls" / f"wall-{i}.blob" for i in range(1, 8)}
    paths[4] = tmp_path / "walls" / "wall-4.json"
    paths[6] = tmp_path / "walls" / "wall-6.json"
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fragments, "LAYER_PATHS", paths)

    # Watchdog reads/writes inside a sandboxed security dir.
    sec = tmp_path / "security"
    sec.mkdir(parents=True, exist_ok=True)
    # Import fresh after monkeypatching so module-level paths can be rebound.
    if "gospel_watchdog" in sys.modules:
        del sys.modules["gospel_watchdog"]
    import gospel_watchdog as wd  # noqa: WPS433
    monkeypatch.setattr(wd, "SECURITY_DIR", sec)
    monkeypatch.setattr(wd, "EVENTS_FILE", sec / "events.jsonl")
    monkeypatch.setattr(wd, "STATE_FILE", sec / "gospel-watchdog-state.json")
    return {"paths": paths, "sec": sec, "wd": wd}


def _events(sec) -> list[dict]:
    p = sec / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _flip_byte(path: Path, layer: int) -> None:
    """Flip one byte deep inside the masked shard payload (past the binary
    header / inside the base64'd JSON fragment field). The shard parses
    cleanly but its bytes have been silently mutated — the case the
    pre-fix verify couldn't see."""
    if layer in (4, 6):
        import base64
        payload = json.loads(path.read_text())
        masked = bytearray(base64.b64decode(payload["fragment"]))
        masked[-1] ^= 0xFF
        payload["fragment"] = base64.b64encode(bytes(masked)).decode()
        path.write_text(json.dumps(payload, indent=2) + "\n")
    else:
        data = bytearray(path.read_bytes())
        # Binary header is 45 bytes; flip the LAST byte of the masked shard
        # payload so we're guaranteed past the header regardless of shard size.
        data[-1] ^= 0xFF
        path.write_bytes(bytes(data))


def test_baseline_emits_no_events_on_clean_state(sandbox):
    fragments.create_fragments(content=b"# WAKE\nclean state\n")
    rc = sandbox["wd"].baseline_only(quiet=True)
    assert rc == 0
    assert _events(sandbox["sec"]) == []
    state = json.loads((sandbox["sec"] / "gospel-watchdog-state.json").read_text())
    assert state["intact"] == 7


def test_check_silent_when_steady_state(sandbox):
    fragments.create_fragments(content=b"# WAKE\nsteady\n")
    sandbox["wd"].baseline_only(quiet=True)
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 0
    assert _events(sandbox["sec"]) == []  # no transitions = no events


def test_tamper_emits_damaged_event(sandbox):
    fragments.create_fragments(content=b"# WAKE\ntamper test\n")
    sandbox["wd"].baseline_only(quiet=True)
    _flip_byte(sandbox["paths"][1], 1)
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 1  # degraded but recoverable
    events = _events(sandbox["sec"])
    assert len(events) == 1
    assert events[0]["type"] == "gospel_shard_damaged"
    assert events[0]["wall"] == 1
    assert events[0]["severity"] == "high"


def test_missing_emits_missing_event(sandbox):
    fragments.create_fragments(content=b"# WAKE\nmissing test\n")
    sandbox["wd"].baseline_only(quiet=True)
    sandbox["paths"][3].unlink()
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 1
    events = _events(sandbox["sec"])
    assert any(e["type"] == "gospel_shard_missing" and e["wall"] == 3 for e in events)


def test_heal_emits_damaged_then_healed_sequence(sandbox):
    fragments.create_fragments(content=b"# WAKE\nheal sequence\n")
    sandbox["wd"].baseline_only(quiet=True)
    _flip_byte(sandbox["paths"][2], 2)
    rc = sandbox["wd"].check(heal=True, quiet=True)
    assert rc == 0  # healed back to full
    events = _events(sandbox["sec"])
    types = [e["type"] for e in events]
    assert "gospel_shard_damaged" in types
    assert "gospel_auto_heal" in types
    # Damage event must come before heal event.
    assert types.index("gospel_shard_damaged") < types.index("gospel_auto_heal")


def test_below_threshold_emits_critical(sandbox):
    fragments.create_fragments(content=b"# WAKE\nthreshold\n")
    sandbox["wd"].baseline_only(quiet=True)
    for layer in (1, 2, 3, 4):
        sandbox["paths"][layer].unlink()
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 2  # below threshold
    events = _events(sandbox["sec"])
    critical = [e for e in events if e["type"] == "gospel_below_threshold"]
    assert len(critical) == 1
    assert critical[0]["severity"] == "critical"


def test_threshold_recovery_emits_info_event(sandbox):
    fragments.create_fragments(content=b"# WAKE\nrecovery\n")
    sandbox["wd"].baseline_only(quiet=True)
    # Drop below threshold first.
    for layer in (1, 2, 3, 4):
        sandbox["paths"][layer].unlink()
    sandbox["wd"].check(heal=False, quiet=True)
    # Now restore via heal — but we need >= K survivors first.
    # Re-create from canonical content (simulating an operator restore).
    fragments.create_fragments(content=b"# WAKE\nrecovery\n")
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 0
    events = _events(sandbox["sec"])
    assert any(e["type"] == "gospel_threshold_recovered" for e in events)


def test_no_baseline_first_run_doesnt_spam_for_healthy(sandbox):
    """First-ever run with healthy state should not emit damage events."""
    fragments.create_fragments(content=b"# WAKE\nfirst run\n")
    rc = sandbox["wd"].check(heal=False, quiet=True)
    assert rc == 0
    assert _events(sandbox["sec"]) == []
