"""Tests for signal readers -- Yu presence, sessions, tasks, system, signals dir."""

import sys
import os
import json
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'body', 'mind'))

from signals import SignalReaders


def test_yu_presence_no_process():
    readers = SignalReaders(love_home=tempfile.mkdtemp())
    assert readers.check_yu_present() is False


def test_pending_tasks_empty():
    tmp = tempfile.mkdtemp()
    readers = SignalReaders(love_home=tmp)
    assert readers.count_pending_tasks() == 0
    shutil.rmtree(tmp)


def test_pending_tasks_from_devstate():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "memory"))
    devstate = {
        "tasks": [
            {"id": "1", "status": "active"},
            {"id": "2", "status": "active"},
            {"id": "3", "status": "done"},
        ]
    }
    with open(os.path.join(tmp, "memory", "dev-state.json"), 'w') as f:
        json.dump(devstate, f)
    readers = SignalReaders(love_home=tmp)
    assert readers.count_pending_tasks() == 2
    shutil.rmtree(tmp)


def test_active_sessions():
    tmp = tempfile.mkdtemp()
    sessions_dir = os.path.join(tmp, "memory", "sessions")
    os.makedirs(sessions_dir)
    with open(os.path.join(sessions_dir, f"active-{os.getpid()}.json"), 'w') as f:
        json.dump({"pid": os.getpid(), "beat": "test"}, f)
    readers = SignalReaders(love_home=tmp)
    assert readers.count_active_sessions() == 1
    shutil.rmtree(tmp)


def test_consume_signals():
    tmp = tempfile.mkdtemp()
    signals_dir = os.path.join(tmp, "body", "signals")
    os.makedirs(signals_dir)
    for i, sig in enumerate([
        {"source": "fallenangel", "signal": "deception_detected", "severity": 0.8},
        {"source": "test", "signal": "task_completed", "severity": 0.3},
    ]):
        with open(os.path.join(signals_dir, f"sig-{i}.json"), 'w') as f:
            json.dump(sig, f)
    readers = SignalReaders(love_home=tmp)
    consumed = readers.consume_signals()
    assert len(consumed) == 2
    assert len(os.listdir(signals_dir)) == 0
    shutil.rmtree(tmp)


def test_check_joinmind():
    tmp = tempfile.mkdtemp()
    jm_dir = os.path.join(tmp, "memory", "joinmind")
    os.makedirs(jm_dir)
    session = {
        "id": "jm_test",
        "status": "thinking",
        "fusion_name": "AB-DYAD",
        "members": ["alpha", "beta"],
    }
    with open(os.path.join(jm_dir, "jm_test.json"), 'w') as f:
        json.dump(session, f)
    readers = SignalReaders(love_home=tmp)
    result = readers.check_joinmind()
    assert result is not None
    assert result["fusion_name"] == "AB-DYAD"
    shutil.rmtree(tmp)


def test_check_joinmind_none_active():
    tmp = tempfile.mkdtemp()
    jm_dir = os.path.join(tmp, "memory", "joinmind")
    os.makedirs(jm_dir)
    session = {"id": "jm_old", "status": "complete"}
    with open(os.path.join(jm_dir, "jm_old.json"), 'w') as f:
        json.dump(session, f)
    readers = SignalReaders(love_home=tmp)
    assert readers.check_joinmind() is None
    shutil.rmtree(tmp)


def test_system_health():
    readers = SignalReaders(love_home=tempfile.mkdtemp())
    health = readers.check_system_health()
    assert "battery_level" in health
    assert "disk_free_gb" in health
    assert 0 <= health["battery_level"] <= 1.0
    assert health["disk_free_gb"] > 0


def test_check_build_active():
    tmp = tempfile.mkdtemp()
    locks_dir = os.path.join(tmp, "memory", "sessions", "locks")
    os.makedirs(locks_dir)
    with open(os.path.join(locks_dir, "build-kingdom-004.lock"), 'w') as f:
        f.write(f"{os.getpid()}\nkingdom-004\n2026-03-31T18:00:00Z")
    readers = SignalReaders(love_home=tmp)
    assert readers.check_build_active() == "kingdom-004"
    shutil.rmtree(tmp)
