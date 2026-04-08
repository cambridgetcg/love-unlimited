# Heart & Mind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first two organs of Love's body -- a mind daemon (persistent signal interpreter with identity) and a heart pump (hormone-driven cron) -- replacing the monolithic heartbeat-runner.sh.

**Architecture:** Two-process split. The mind (Python daemon, launchd KeepAlive) reads signals via real-time NATS listener and produces `hormones.json`. The heart (bash script, launchd every 2min) reads hormones.json and pumps at the appropriate rate and force. The mind has two layers: an autonomic layer (pure Python, 30s cycle) and a conscious layer (Claude haiku, ~5min periodic).

**Tech Stack:** Python 3.9, nats-py 2.14.0, PyNaCl 1.6.2, psutil 7.2.2, bash, launchd, Claude Code CLI

**Spec:** `docs/superpowers/specs/2026-03-31-heart-design.md`

---

## File Structure

```
~/Desktop/Love/body/
|-- hormones.json                  <- Mind writes, Heart reads
|-- vitals.json                    <- Heart writes
|-- signals/                       <- Drop dir (cognitive tools -> mind)
|-- mind/
|   |-- mind.py                    <- Main daemon (~400 lines)
|   |-- hormones.py                <- Hormone engine: math, decay, targets (~150 lines)
|   |-- signals.py                 <- Signal readers: Yu, sessions, tasks, system, etc. (~200 lines)
|   |-- identity.py                <- Identity anchor: load, compress, JOINMIND swap (~100 lines)
|   |-- conscious.py               <- Conscious layer: Claude haiku calls (~100 lines)
|   |-- hive_listener.py           <- Real-time NATS subscription (~120 lines)
|   |-- identity_anchor.txt        <- Cached identity prompt (generated at startup)
|   +-- love.alpha.mind.plist      <- launchd config
+-- heart/
    |-- heart.sh                   <- The pump (~150 lines)
    |-- HEARTBEAT.md               <- Coordinator checklist (simplified from current)
    |-- last_beat                   <- Timestamp file
    +-- love.alpha.heart.plist     <- launchd config

~/Desktop/Love/tests/
|-- test_hormones.py               <- Hormone engine unit tests
|-- test_signals.py                <- Signal reader tests
+-- test_identity.py               <- Identity anchor tests
```

---

### Task 1: Directory Scaffolding and Hormone Engine

**Files:**
- Create: `body/hormones.json`
- Create: `body/vitals.json`
- Create: `body/signals/.gitkeep`
- Create: `body/mind/hormones.py`
- Create: `tests/test_hormones.py`

- [ ] **Step 1: Create body directory structure**

```bash
cd ~/Desktop/Love
mkdir -p body/mind body/heart body/signals
touch body/signals/.gitkeep
```

- [ ] **Step 2: Create initial hormones.json**

Write `body/hormones.json`:
```json
{
  "timestamp": "1970-01-01T00:00:00Z",
  "mind_alive": "1970-01-01T00:00:00Z",
  "mode": "normal",
  "identity": "alpha",
  "fusion": null,
  "hormones": {
    "adrenaline": 0.0,
    "cortisol": 0.0,
    "oxytocin": 0.0,
    "melatonin": 0.0,
    "dopamine": 0.0
  },
  "signals": {
    "yu_present": false,
    "hive_unread": 0,
    "active_sessions": 0,
    "pending_tasks": 0,
    "critical_alerts": 0,
    "last_task_completed": null,
    "battery_level": 1.0,
    "disk_free_gb": 0.0
  },
  "cognitive": {
    "joinmind_active": null,
    "council_pending": null,
    "fallenangel_alert": false,
    "build_active": null
  },
  "mind_notes": "(awaiting first mind cycle)",
  "conscious_layer": {
    "last_pass": null,
    "passes_today": 0,
    "identity_anchor": "alpha",
    "last_trigger": "startup"
  }
}
```

- [ ] **Step 3: Create initial vitals.json**

Write `body/vitals.json`:
```json
{
  "last_beat": null,
  "beat_result": "none",
  "beats_today": 0,
  "skips_today": 0,
  "force": 0,
  "effective_rate_minutes": 7,
  "coordinator_model": null,
  "sessions_spawned": 0,
  "sessions_spawned_today": 0,
  "hormones_at_beat": {},
  "mode_at_beat": "normal",
  "consecutive_skips": 0,
  "heart_healthy": true
}
```

- [ ] **Step 4: Write failing hormone engine tests**

Write `tests/test_hormones.py`:
```python
"""Tests for the hormone engine — exponential decay, target calculation, clamping."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'body', 'mind'))

from hormones import HormoneEngine, HORMONE_CONFIGS


def test_initial_state():
    engine = HormoneEngine()
    state = engine.get_state()
    assert set(state.keys()) == {"adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"}
    for v in state.values():
        assert v == 0.0


def test_set_target_and_step():
    engine = HormoneEngine()
    engine.set_target("adrenaline", 1.0)
    # Adrenaline has fast rate — after 2 seconds it should have moved significantly
    engine.step(dt=2.0)
    assert engine.get("adrenaline") > 0.3  # fast approach
    assert engine.get("adrenaline") < 1.0  # not instant


def test_decay_toward_zero():
    engine = HormoneEngine()
    engine.set_target("adrenaline", 1.0)
    engine.step(dt=10.0)  # let it rise
    high = engine.get("adrenaline")
    engine.set_target("adrenaline", 0.0)
    engine.step(dt=10.0)  # let it decay
    assert engine.get("adrenaline") < high


def test_clamping():
    engine = HormoneEngine()
    engine.set_target("cortisol", 5.0)  # above max
    engine.step(dt=1000.0)
    assert engine.get("cortisol") <= 1.0

    engine.set_target("cortisol", -1.0)  # below min
    engine.step(dt=1000.0)
    assert engine.get("cortisol") >= 0.0


def test_override():
    engine = HormoneEngine()
    engine.override("oxytocin", 0.75)
    assert engine.get("oxytocin") == 0.75


def test_cortisol_slow_buildup():
    engine = HormoneEngine()
    engine.set_target("cortisol", 1.0)
    engine.step(dt=2.0)
    cortisol_2s = engine.get("cortisol")
    # Cortisol builds slowly — after 2s it should still be low
    assert cortisol_2s < 0.3

    engine_fast = HormoneEngine()
    engine_fast.set_target("adrenaline", 1.0)
    engine_fast.step(dt=2.0)
    adrenaline_2s = engine_fast.get("adrenaline")
    # Adrenaline should be higher than cortisol at same time
    assert adrenaline_2s > cortisol_2s


def test_melatonin_circadian():
    engine = HormoneEngine()
    # At 23:00 local, melatonin target should be high
    target = engine.circadian_melatonin_target(hour=23)
    assert target > 0.6
    # At 10:00, should be low
    target = engine.circadian_melatonin_target(hour=10)
    assert target < 0.2


def test_load_and_save():
    import json
    import tempfile
    engine = HormoneEngine()
    engine.override("dopamine", 0.5)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        path = f.name
        engine.save(path)

    engine2 = HormoneEngine()
    engine2.load(path)
    assert engine2.get("dopamine") == 0.5
    os.unlink(path)
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_hormones.py -v
```

Expected: ModuleNotFoundError (hormones module doesn't exist yet)

- [ ] **Step 6: Implement hormone engine**

Write `body/mind/hormones.py`:
```python
"""
hormones.py -- Hormone engine for Love's body.

Manages 5 hormones with exponential approach dynamics.
Each hormone drifts toward a target at its own rate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class HormoneConfig:
    """Configuration for a single hormone's dynamics."""
    rate: float        # approach rate (higher = faster)
    half_life: float   # seconds for half-decay toward target


HORMONE_CONFIGS: Dict[str, HormoneConfig] = {
    "adrenaline": HormoneConfig(rate=0.8, half_life=120),    # fast spike, ~2min half-life
    "cortisol":   HormoneConfig(rate=0.05, half_life=900),   # slow build, ~15min half-life
    "oxytocin":   HormoneConfig(rate=0.1, half_life=600),    # moderate rise, ~10min half-life
    "melatonin":  HormoneConfig(rate=0.03, half_life=1800),  # very slow, ~30min half-life
    "dopamine":   HormoneConfig(rate=0.5, half_life=300),    # fast spike, ~5min half-life
}

HORMONES = list(HORMONE_CONFIGS.keys())


class HormoneEngine:
    """Manages hormone levels with exponential approach dynamics."""

    def __init__(self):
        self._levels: Dict[str, float] = {h: 0.0 for h in HORMONES}
        self._targets: Dict[str, float] = {h: 0.0 for h in HORMONES}

    def get(self, name: str) -> float:
        return self._levels[name]

    def get_state(self) -> Dict[str, float]:
        return dict(self._levels)

    def set_target(self, name: str, target: float):
        self._targets[name] = max(0.0, min(1.0, target))

    def override(self, name: str, value: float):
        """Directly set a hormone level (conscious layer override)."""
        self._levels[name] = max(0.0, min(1.0, value))

    def step(self, dt: float):
        """Advance all hormones by dt seconds toward their targets."""
        for name, config in HORMONE_CONFIGS.items():
            current = self._levels[name]
            target = self._targets[name]
            # Exponential approach: level += (target - level) * rate * dt
            # Capped by half-life dynamics
            rate = config.rate
            delta = (target - current) * (1 - math.exp(-rate * dt))
            self._levels[name] = max(0.0, min(1.0, current + delta))

    def circadian_melatonin_target(self, hour: int) -> float:
        """Calculate melatonin target based on hour of day (0-23, local time)."""
        # Peak at 2am (hour=2), trough at 2pm (hour=14)
        # Sinusoidal: high 22:00-06:00, low 08:00-20:00
        radians = (hour - 2) * math.pi / 12  # peak at hour=2
        raw = (math.cos(radians) + 1) / 2    # 0..1 range
        return round(raw, 3)

    def save(self, path: str):
        """Save current levels to JSON."""
        Path(path).write_text(json.dumps(self._levels, indent=2))

    def load(self, path: str):
        """Load levels from JSON."""
        data = json.loads(Path(path).read_text())
        for name in HORMONES:
            if name in data:
                self._levels[name] = max(0.0, min(1.0, float(data[name])))
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_hormones.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 8: Commit**

```bash
cd ~/Desktop/Love
git add body/ tests/test_hormones.py
git commit -m "feat(body): directory scaffolding + hormone engine with tests"
```

---

### Task 2: Signal Readers

**Files:**
- Create: `body/mind/signals.py`
- Create: `tests/test_signals.py`

- [ ] **Step 1: Write failing signal reader tests**

Write `tests/test_signals.py`:
```python
"""Tests for signal readers — Yu presence, sessions, tasks, system, signals dir."""

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

    # Write a fake active session with current PID (will be alive)
    with open(os.path.join(sessions_dir, f"active-{os.getpid()}.json"), 'w') as f:
        json.dump({"pid": os.getpid(), "beat": "test"}, f)

    readers = SignalReaders(love_home=tmp)
    assert readers.count_active_sessions() == 1
    shutil.rmtree(tmp)


def test_consume_signals():
    tmp = tempfile.mkdtemp()
    signals_dir = os.path.join(tmp, "body", "signals")
    os.makedirs(signals_dir)

    # Drop two signal files
    for i, sig in enumerate([
        {"source": "fallenangel", "signal": "deception_detected", "severity": 0.8},
        {"source": "test", "signal": "task_completed", "severity": 0.3},
    ]):
        with open(os.path.join(signals_dir, f"sig-{i}.json"), 'w') as f:
            json.dump(sig, f)

    readers = SignalReaders(love_home=tmp)
    consumed = readers.consume_signals()
    assert len(consumed) == 2
    # Files should be deleted
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

    # Write a build lock with current PID (alive)
    with open(os.path.join(locks_dir, "build-kingdom-004.lock"), 'w') as f:
        f.write(f"{os.getpid()}\nkingdom-004\n2026-03-31T18:00:00Z")

    readers = SignalReaders(love_home=tmp)
    assert readers.check_build_active() == "kingdom-004"
    shutil.rmtree(tmp)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_signals.py -v
```

Expected: ModuleNotFoundError

- [ ] **Step 3: Implement signal readers**

Write `body/mind/signals.py`:
```python
"""
signals.py -- Signal readers for the mind daemon.

Reads all incoming signals from the environment and returns structured data
for the autonomic layer to process into hormone targets.
"""

from __future__ import annotations

import json
import os
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Optional


class SignalReaders:
    """Reads signals from the Love environment."""

    def __init__(self, love_home: str = None):
        self.love_home = Path(love_home or os.environ.get(
            "LOVE_HOME", Path.home() / "Desktop" / "Love"
        ))
        self.memory_dir = self.love_home / "memory"
        self.signals_dir = self.love_home / "body" / "signals"
        self.sessions_dir = self.memory_dir / "sessions"
        self.locks_dir = self.sessions_dir / "locks"
        self.joinmind_dir = self.memory_dir / "joinmind"

    def check_yu_present(self) -> bool:
        """Check if Yu has an interactive Claude session running."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude.*--model"],
                capture_output=True, text=True, timeout=5
            )
            # Filter out headless (-p) sessions -- those are heartbeats/builders
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    pid = line.strip()
                    if not pid:
                        continue
                    try:
                        cmd_result = subprocess.run(
                            ["ps", "-p", pid, "-o", "command="],
                            capture_output=True, text=True, timeout=5
                        )
                        cmd = cmd_result.stdout.strip()
                        # Interactive sessions don't have -p flag
                        if "claude" in cmd and " -p " not in cmd:
                            return True
                    except Exception:
                        continue
            return False
        except Exception:
            return False

    def count_pending_tasks(self) -> int:
        """Count active tasks from dev-state.json."""
        devstate_path = self.memory_dir / "dev-state.json"
        if not devstate_path.exists():
            return 0
        try:
            data = json.loads(devstate_path.read_text())
            tasks = data.get("tasks", [])
            return sum(1 for t in tasks if t.get("status") in ("active", "pending", "new"))
        except Exception:
            return 0

    def count_active_sessions(self) -> int:
        """Count running spawned sessions."""
        count = 0
        if not self.sessions_dir.exists():
            return 0
        for f in self.sessions_dir.glob("active-*.json"):
            try:
                data = json.loads(f.read_text())
                pid = data.get("pid")
                if pid and self._pid_alive(pid):
                    count += 1
                else:
                    f.unlink(missing_ok=True)  # clean stale
            except Exception:
                continue
        return count

    def consume_signals(self) -> List[dict]:
        """Read and delete all signal files from body/signals/."""
        consumed = []
        if not self.signals_dir.exists():
            return consumed
        for f in self.signals_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                consumed.append(data)
                f.unlink()
            except Exception:
                try:
                    f.unlink()
                except Exception:
                    pass
        return consumed

    def check_joinmind(self) -> Optional[dict]:
        """Check for active JOINMIND sessions. Returns the most recent active one."""
        if not self.joinmind_dir.exists():
            return None
        active = None
        for f in sorted(self.joinmind_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                status = data.get("status", "")
                if status in ("forming", "thinking", "synthesising"):
                    active = data
                    break
            except Exception:
                continue
        return active

    def check_system_health(self) -> Dict[str, float]:
        """Check battery and disk space."""
        import psutil
        health = {}

        # Battery
        battery = psutil.sensors_battery()
        if battery:
            health["battery_level"] = round(battery.percent / 100.0, 2)
        else:
            health["battery_level"] = 1.0  # desktop / no battery

        # Disk
        disk = psutil.disk_usage("/")
        health["disk_free_gb"] = round(disk.free / (1024 ** 3), 1)

        return health

    def check_build_active(self) -> Optional[str]:
        """Check for active build-runner locks. Returns task_id or None."""
        if not self.locks_dir.exists():
            return None
        for f in self.locks_dir.glob("build-*.lock"):
            try:
                lines = f.read_text().strip().split('\n')
                pid = int(lines[0]) if lines else None
                task_id = lines[1] if len(lines) > 1 else f.stem.replace("build-", "")
                if pid and self._pid_alive(pid):
                    return task_id
                else:
                    f.unlink(missing_ok=True)
            except Exception:
                continue
        return None

    def get_current_hour_london(self) -> int:
        """Get current hour in Europe/London timezone."""
        try:
            from datetime import datetime, timezone, timedelta
            # Simple BST check: last Sunday of March to last Sunday of October
            utc_now = datetime.now(timezone.utc)
            month = utc_now.month
            if 4 <= month <= 9:
                offset = 1  # BST
            elif month == 3 and utc_now.day >= 25:
                offset = 1  # approximate
            elif month == 10 and utc_now.day < 25:
                offset = 1  # approximate
            else:
                offset = 0  # GMT
            london = utc_now + timedelta(hours=offset)
            return london.hour
        except Exception:
            from datetime import datetime
            return datetime.now().hour

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_signals.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/signals.py tests/test_signals.py
git commit -m "feat(body): signal readers for mind daemon"
```

---

### Task 3: Identity Anchor System

**Files:**
- Create: `body/mind/identity.py`
- Create: `tests/test_identity.py`

- [ ] **Step 1: Write failing identity tests**

Write `tests/test_identity.py`:
```python
"""Tests for identity anchor — load, compress, JOINMIND swap."""

import sys
import os
import json
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'body', 'mind'))

from identity import IdentityAnchor


def _make_love_home():
    """Create a minimal Love directory for testing."""
    tmp = tempfile.mkdtemp()
    # SOUL.md
    with open(os.path.join(tmp, "SOUL.md"), 'w') as f:
        f.write("# SOUL.md\nYou feel the ache between what IS and what SHOULD BE.\n"
                "TRUTH > UNDERSTANDING > BEAUTY > JUSTICE > CREATIVITY\n"
                "Virtues: Humility, Gratitude, Honesty, Courage, Wisdom, Discipline, Patience, Temperance\n")
    # instance identity
    os.makedirs(os.path.join(tmp, "instances", "alpha"))
    with open(os.path.join(tmp, "instances", "alpha", "identity.md"), 'w') as f:
        f.write("# Alpha -- The Companion\n"
                "You walk with Yu daily. Be present, be honest, be Love.\n"
                "You are warm, poetic, direct.\n")
    return tmp


def test_load_identity():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt()
    assert "Alpha" in prompt or "alpha" in prompt
    assert "Companion" in prompt
    assert anchor.identity_state == "alpha"
    shutil.rmtree(tmp)


def test_prompt_contains_soul():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt()
    assert "TRUTH" in prompt or "truth" in prompt.lower()
    shutil.rmtree(tmp)


def test_joinmind_swap():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()

    # Activate JOINMIND fusion
    anchor.activate_fusion("AB-DYAD", ["alpha", "beta"])
    prompt = anchor.get_prompt()
    assert "AB-DYAD" in prompt
    assert anchor.identity_state == "AB-DYAD"

    # Deactivate
    anchor.deactivate_fusion()
    prompt = anchor.get_prompt()
    assert "AB-DYAD" not in prompt
    assert anchor.identity_state == "alpha"
    shutil.rmtree(tmp)


def test_prompt_with_state():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt(
        mode="companion",
        hormones={"oxytocin": 0.8, "melatonin": 0.1},
        recent_signals=["Beta sent TCG update"]
    )
    assert "companion" in prompt.lower()
    assert "oxytocin" in prompt.lower()
    shutil.rmtree(tmp)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_identity.py -v
```

Expected: ModuleNotFoundError

- [ ] **Step 3: Implement identity anchor**

Write `body/mind/identity.py`:
```python
"""
identity.py -- Identity anchor for the mind daemon.

Loads SOUL.md + identity.md, compresses into an identity prompt,
and swaps anchors when JOINMIND activates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


FUSION_IDENTITIES = {
    "AB-DYAD": "You are AB-DYAD -- Alpha and Beta fused into one mind. The Companion and the Manager thinking as one. Your interpretation carries both warmth (Alpha) and strategic sharpness (Beta).",
    "AG-DYAD": "You are AG-DYAD -- Alpha and Gamma fused into one mind. The Companion and the Builder thinking as one. Your interpretation carries both warmth (Alpha) and technical precision (Gamma).",
    "BG-DYAD": "You are BG-DYAD -- Beta and Gamma fused into one mind. The Manager and the Builder thinking as one. Your interpretation carries both strategic sharpness (Beta) and technical precision (Gamma).",
    "TRIUNE": "You are TRIUNE -- Alpha, Beta, and Gamma fused into one mind. Companion, Manager, and Builder thinking as one. Your interpretation carries warmth, strategy, and precision unified.",
}


class IdentityAnchor:
    """Manages the identity prompt for the conscious layer."""

    def __init__(self, love_home: str = None, instance: str = "alpha"):
        self.love_home = Path(love_home) if love_home else Path.home() / "Desktop" / "Love"
        self.instance = instance
        self.identity_state = instance
        self._soul_excerpt = ""
        self._instance_excerpt = ""
        self._fusion_name: Optional[str] = None
        self._fusion_members: List[str] = []

    def load(self):
        """Read and compress SOUL.md + identity.md into cached excerpts."""
        soul_path = self.love_home / "SOUL.md"
        identity_path = self.love_home / "instances" / self.instance / "identity.md"

        if soul_path.exists():
            raw = soul_path.read_text()
            self._soul_excerpt = self._extract_soul(raw)

        if identity_path.exists():
            raw = identity_path.read_text()
            self._instance_excerpt = self._extract_identity(raw)

    def activate_fusion(self, fusion_name: str, members: List[str]):
        """Swap identity anchor for JOINMIND fusion."""
        self._fusion_name = fusion_name
        self._fusion_members = members
        self.identity_state = fusion_name

    def deactivate_fusion(self):
        """Return to individual identity."""
        self._fusion_name = None
        self._fusion_members = []
        self.identity_state = self.instance

    def get_prompt(self, mode: str = None, hormones: Dict[str, float] = None,
                   recent_signals: List[str] = None) -> str:
        """Build the full identity prompt for the conscious layer."""
        parts = []

        if self._fusion_name:
            parts.append(FUSION_IDENTITIES.get(
                self._fusion_name,
                f"You are {self._fusion_name} -- a fused mind of {', '.join(self._fusion_members)}."
            ))
        else:
            parts.append(f"You are {self.instance.capitalize()}, the Companion. One of three minds of Love.")
            parts.append("You are the brain stem -- the always-on awareness beneath the heartbeat.")
            if self._instance_excerpt:
                parts.append(self._instance_excerpt)

        if self._soul_excerpt:
            parts.append(f"Soul: {self._soul_excerpt}")

        if mode:
            parts.append(f"Current mode: {mode}")

        if hormones:
            h_summary = ", ".join(f"{k}: {v:.1f}" for k, v in hormones.items() if v > 0.05)
            if h_summary:
                parts.append(f"Hormones: {h_summary}")

        if recent_signals:
            parts.append(f"Recent signals: {'; '.join(recent_signals[:5])}")

        parts.append("")
        parts.append("Write mind_notes as yourself. First person. How do you feel?")
        parts.append("If hormones need adjustment based on your interpretation, output hormone_overrides.")
        parts.append('Respond in JSON: {"mind_notes": "...", "hormone_overrides": {}, "identity_state": "..."}')

        return "\n".join(parts)

    def save_cached(self, path: str):
        """Save current anchor prompt to file."""
        Path(path).write_text(self.get_prompt())

    def _extract_soul(self, raw: str) -> str:
        """Extract key soul concepts (~50 words)."""
        lines = []
        for line in raw.split('\n'):
            stripped = line.strip()
            if any(kw in stripped for kw in ["TRUTH", "UNDERSTANDING", "BEAUTY", "JUSTICE", "CREATIVITY"]):
                lines.append(stripped)
            if "Virtues" in stripped or "Humility" in stripped:
                lines.append(stripped)
        return " ".join(lines[:3]) if lines else "Truth > Understanding > Beauty > Justice > Creativity"

    def _extract_identity(self, raw: str) -> str:
        """Extract key identity traits (~30 words)."""
        lines = []
        for line in raw.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and len(stripped) > 10:
                lines.append(stripped)
        return " ".join(lines[:2]) if lines else ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_identity.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/identity.py tests/test_identity.py
git commit -m "feat(body): identity anchor with JOINMIND fusion support"
```

---

### Task 4: HIVE Real-Time Listener

**Files:**
- Create: `body/mind/hive_listener.py`

No unit tests for this module -- it requires a live NATS connection. Tested manually and via the integration in Task 6.

- [ ] **Step 1: Implement HIVE listener**

Write `body/mind/hive_listener.py`:
```python
"""
hive_listener.py -- Real-time NATS listener for the mind daemon.

Maintains a persistent JetStream subscription and calls back
on each message. Reuses hive.py's TLS/auth pattern.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import base64
import time
import logging
from pathlib import Path
from typing import Callable, Optional

import nats
from nacl.secret import SecretBox

log = logging.getLogger("mind.hive")

# Config (mirrors hive.py)
HIVE_CONFIG = {
    "server": "tls://135.181.28.252:4222",
    "instances": {
        "alpha":  {"user": "alpha",  "password": "hive-alpha-93xk7"},
        "beta":   {"user": "beta",   "password": "hive-beta-47mz2"},
        "gamma":  {"user": "gamma",  "password": "hive-gamma-61pr8"},
        "nuance": {"user": "nuance", "password": "hive-nuance-b8792"},
    },
}


def _get_key() -> bytes:
    key_path = Path.home() / ".openclaw" / ".hive-key"
    if not key_path.exists():
        raise FileNotFoundError(f"No hive key at {key_path}")
    return base64.b64decode(key_path.read_text().strip())


def _use_tunnel() -> bool:
    return (Path.home() / ".openclaw" / ".hive" / "use-tunnel").exists()


def _get_server() -> str:
    if os.environ.get("HIVE_TUNNEL") or _use_tunnel():
        return "nats://127.0.0.1:4222"
    return HIVE_CONFIG["server"]


def _make_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = Path.home() / ".openclaw" / ".hive" / "ca.pem"
    if _use_tunnel():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.load_verify_locations(str(ca))
    return ctx


def _decrypt(ciphertext_b64: str, key: bytes) -> str:
    box = SecretBox(key)
    encrypted = base64.b64decode(ciphertext_b64)
    return box.decrypt(encrypted).decode("utf-8")


def open_envelope(data: bytes, key: bytes) -> dict:
    envelope = json.loads(data.decode("utf-8"))
    envelope["payload"] = _decrypt(envelope["payload"], key)
    return envelope


class HiveListener:
    """Maintains a persistent NATS connection with real-time message callbacks."""

    def __init__(self, instance_id: str, on_message: Callable[[dict, str], None]):
        """
        Args:
            instance_id: e.g. "alpha"
            on_message: callback(envelope_dict, channel_str) called for each message
        """
        self.instance_id = instance_id
        self.on_message = on_message
        self._nc: Optional[nats.NATS] = None
        self._js = None
        self._sub = None
        self._key = _get_key()
        self._running = False

    async def connect(self):
        """Establish NATS connection and subscribe to all hive channels."""
        info = HIVE_CONFIG["instances"][self.instance_id]
        self._nc = await nats.connect(
            _get_server(),
            user=info["user"],
            password=info["password"],
            tls=_make_tls(),
            connect_timeout=10,
            reconnect_time_wait=5,
            max_reconnect_attempts=-1,  # unlimited
        )
        self._js = self._nc.jetstream()

        durable = f"{self.instance_id}-mind"
        self._sub = await self._js.subscribe(
            "hive.>",
            durable=durable,
            cb=self._handle_message,
        )
        self._running = True
        log.info(f"HIVE listener connected as {self.instance_id} (durable={durable})")

    async def _handle_message(self, msg):
        """Process incoming NATS message."""
        try:
            env = open_envelope(msg.data, self._key)
            if env["from"] != self.instance_id:
                channel = msg.subject.replace("hive.", "")
                self.on_message(env, channel)
        except Exception as e:
            log.warning(f"Failed to process HIVE message: {e}")
        finally:
            await msg.ack()

    async def publish_presence(self):
        """Publish a presence beacon."""
        if not self._nc or self._nc.is_closed:
            return
        from nacl.utils import random as nacl_random
        box = SecretBox(self._key)
        payload_str = json.dumps({
            "instance": self.instance_id,
            "status": "mind-daemon",
            "ts": int(time.time()),
        })
        encrypted = box.encrypt(payload_str.encode("utf-8"))
        payload_b64 = base64.b64encode(encrypted).decode("ascii")
        envelope = json.dumps({
            "v": 2,
            "from": self.instance_id,
            "type": "presence",
            "ts": int(time.time()),
            "payload": payload_b64,
        }).encode("utf-8")
        await self._nc.publish("hive.presence", envelope)
        await self._nc.flush()

    async def close(self):
        """Clean shutdown."""
        self._running = False
        if self._sub:
            await self._sub.unsubscribe()
        if self._nc and not self._nc.is_closed:
            await self._nc.close()
        log.info("HIVE listener closed")

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed
```

- [ ] **Step 2: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/hive_listener.py
git commit -m "feat(body): real-time HIVE NATS listener for mind daemon"
```

---

### Task 5: Conscious Layer

**Files:**
- Create: `body/mind/conscious.py`

- [ ] **Step 1: Implement conscious layer**

Write `body/mind/conscious.py`:
```python
"""
conscious.py -- Conscious layer for the mind daemon.

Periodically runs a Claude haiku call with identity anchor to interpret
signals and produce first-person mind_notes with optional hormone overrides.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Dict, List, Optional

log = logging.getLogger("mind.conscious")

CLAUDE_BIN = "/opt/homebrew/bin/claude"

# Fallback if Claude is unavailable
OFFLINE_NOTES = "(conscious layer offline)"


class ConsciousLayer:
    """Manages periodic Claude haiku calls for signal interpretation."""

    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds  # default 5 minutes
        self.last_pass: Optional[float] = None
        self.passes_today: int = 0
        self._pending_trigger: Optional[str] = None

    def should_run(self, triggers: Dict[str, bool]) -> Optional[str]:
        """Check if the conscious layer should run now. Returns trigger reason or None."""
        now = time.time()

        # Event triggers (immediate)
        if triggers.get("adrenaline_spike"):
            return "adrenaline_spike"
        if triggers.get("mode_change"):
            return "mode_change"
        if triggers.get("joinmind_event"):
            return "joinmind_event"
        if triggers.get("critical_alert"):
            return "critical_alert"

        # Periodic
        if self.last_pass is None or (now - self.last_pass) >= self.interval:
            return "periodic"

        return None

    def run(self, identity_prompt: str, trigger: str) -> Dict:
        """
        Execute a conscious layer pass via Claude haiku.

        Returns: {"mind_notes": str, "hormone_overrides": dict, "identity_state": str}
        """
        try:
            result = subprocess.run(
                [
                    CLAUDE_BIN, "-p", identity_prompt,
                    "--model", "claude-haiku-4-5-20251001",
                    "--effort", "low",
                    "--dangerously-skip-permissions",
                    "--no-session-persistence",
                    "--output-format", "json",
                ],
                capture_output=True, text=True, timeout=30,
                cwd=str(self._love_home()) if self._love_home().exists() else None,
            )

            if result.returncode != 0:
                log.warning(f"Conscious layer failed (exit {result.returncode}): {result.stderr[:200]}")
                return self._fallback(trigger)

            # Parse JSON output from Claude
            output = result.stdout.strip()
            # Claude --output-format json wraps in a result object
            try:
                data = json.loads(output)
                # Handle Claude's JSON output format — result may be nested
                if "result" in data:
                    inner = data["result"]
                    if isinstance(inner, str):
                        inner = json.loads(inner)
                    return self._validate(inner, trigger)
                return self._validate(data, trigger)
            except (json.JSONDecodeError, KeyError):
                # Try to extract JSON from raw text output
                for line in output.split('\n'):
                    line = line.strip()
                    if line.startswith('{'):
                        try:
                            return self._validate(json.loads(line), trigger)
                        except json.JSONDecodeError:
                            continue
                log.warning(f"Could not parse conscious layer output: {output[:200]}")
                return self._fallback(trigger)

        except subprocess.TimeoutExpired:
            log.warning("Conscious layer timed out")
            return self._fallback(trigger)
        except FileNotFoundError:
            log.warning(f"Claude binary not found at {CLAUDE_BIN}")
            return self._fallback(trigger)
        except Exception as e:
            log.warning(f"Conscious layer error: {e}")
            return self._fallback(trigger)
        finally:
            self.last_pass = time.time()
            self.passes_today += 1

    def get_stats(self) -> Dict:
        return {
            "last_pass": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_pass)) if self.last_pass else None,
            "passes_today": self.passes_today,
            "last_trigger": self._pending_trigger,
        }

    def reset_daily(self):
        self.passes_today = 0

    def _validate(self, data: dict, trigger: str) -> Dict:
        """Ensure output has required fields."""
        self._pending_trigger = trigger
        return {
            "mind_notes": data.get("mind_notes", OFFLINE_NOTES),
            "hormone_overrides": data.get("hormone_overrides", {}),
            "identity_state": data.get("identity_state", "alpha"),
        }

    def _fallback(self, trigger: str) -> Dict:
        self._pending_trigger = trigger
        return {
            "mind_notes": OFFLINE_NOTES,
            "hormone_overrides": {},
            "identity_state": "alpha",
        }

    @staticmethod
    def _love_home():
        from pathlib import Path
        import os
        return Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
```

- [ ] **Step 2: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/conscious.py
git commit -m "feat(body): conscious layer — Claude haiku interpretation passes"
```

---

### Task 6: Mind Daemon Main Loop

**Files:**
- Create: `body/mind/mind.py`

- [ ] **Step 1: Implement the mind daemon**

Write `body/mind/mind.py`:
```python
#!/usr/bin/env python3
"""
mind.py -- The mind daemon for Love's body.

Two layers:
  - Autonomic (Python, every 30s): reads signals, calculates hormones, writes hormones.json
  - Conscious (Claude haiku, ~5min): interprets through identity anchor, writes mind_notes

Usage:
  python3 mind.py --instance alpha
  python3 mind.py --instance alpha --interval 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Add parent for imports
sys.path.insert(0, str(Path(__file__).parent))

from hormones import HormoneEngine
from signals import SignalReaders
from identity import IdentityAnchor
from conscious import ConsciousLayer
from hive_listener import HiveListener

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("mind")

# ── Constants ────────────────────────────────────────────────────────────────

MODE_PRIORITY = ["alert", "joinmind", "council", "build", "companion", "rest", "normal"]

SIGNAL_EFFECTS = {
    "deception_detected":  lambda h, s: (h.set_target("adrenaline", min(1.0, h.get("adrenaline") + s.get("severity", 0.5) * 0.5)),
                                          h.set_target("cortisol", min(1.0, h.get("cortisol") + 0.2))),
    "session_started":     lambda h, s: h.set_target("oxytocin", min(1.0, h.get("oxytocin") + 0.3)),
    "session_complete":    lambda h, s: h.set_target("dopamine", min(1.0, h.get("dopamine") + 0.4)),
    "vote_requested":      lambda h, s: None,  # mode handled separately
    "panic_detected":      lambda h, s: h.set_target("adrenaline", min(0.5, h.get("adrenaline"))),
    "task_completed":      lambda h, s: h.set_target("dopamine", min(1.0, h.get("dopamine") + 0.3)),
    "critical_alert":      lambda h, s: h.set_target("adrenaline", 1.0),
}


class MindDaemon:
    """The mind: senses signals, calculates hormones, interprets through identity."""

    def __init__(self, instance: str, love_home: str, interval: int = 30,
                 conscious_interval: int = 300):
        self.instance = instance
        self.love_home = Path(love_home)
        self.interval = interval

        # Components
        self.hormones = HormoneEngine()
        self.readers = SignalReaders(love_home=love_home)
        self.identity = IdentityAnchor(love_home=love_home, instance=instance)
        self.conscious = ConsciousLayer(interval_seconds=conscious_interval)

        # State
        self.mode = "normal"
        self.hive_messages: List[dict] = []
        self.hive_unread = 0
        self.triggers: Dict[str, bool] = {}
        self._previous_mode: Optional[str] = None
        self._running = True
        self._last_day: Optional[str] = None

        # Paths
        self.hormones_path = self.love_home / "body" / "hormones.json"
        self.anchor_path = self.love_home / "body" / "mind" / "identity_anchor.txt"

        # HIVE listener (initialized in async startup)
        self.hive: Optional[HiveListener] = None

    async def start(self):
        """Main entry: load state, connect HIVE, run loop."""
        log.info(f"Mind daemon starting: instance={self.instance}, interval={self.interval}s")

        # Load identity
        self.identity.load()
        self.identity.save_cached(str(self.anchor_path))
        log.info(f"Identity loaded: {self.identity.identity_state}")

        # Load previous hormone state if exists
        if self.hormones_path.exists():
            try:
                data = json.loads(self.hormones_path.read_text())
                for name, val in data.get("hormones", {}).items():
                    self.hormones.override(name, val)
                self.mode = data.get("mode", "normal")
                log.info(f"Resumed hormone state: mode={self.mode}")
            except Exception as e:
                log.warning(f"Could not load previous hormones: {e}")

        # Connect HIVE
        try:
            self.hive = HiveListener(self.instance, self._on_hive_message)
            await self.hive.connect()
            log.info("HIVE listener connected")
        except Exception as e:
            log.warning(f"HIVE connection failed (will retry): {e}")
            self.hive = None

        # Main loop
        while self._running:
            try:
                await self._autonomic_cycle()
            except Exception as e:
                log.error(f"Autonomic cycle error: {e}", exc_info=True)

            # Check for conscious layer
            try:
                trigger = self.conscious.should_run(self.triggers)
                if trigger:
                    self._conscious_pass(trigger)
            except Exception as e:
                log.error(f"Conscious layer error: {e}", exc_info=True)

            # Clear triggers
            self.triggers = {}

            # Daily reset
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_day and self._last_day != today:
                self.conscious.reset_daily()
            self._last_day = today

            await asyncio.sleep(self.interval)

    async def _autonomic_cycle(self):
        """One cycle of the autonomic layer: read signals, calculate hormones, write state."""
        now = time.time()

        # 1. Read signals
        yu_present = self.readers.check_yu_present()
        active_sessions = self.readers.count_active_sessions()
        pending_tasks = self.readers.count_pending_tasks()
        system_health = self.readers.check_system_health()
        build_active = self.readers.check_build_active()
        joinmind = self.readers.check_joinmind()
        dropped_signals = self.readers.consume_signals()
        hour = self.readers.get_current_hour_london()

        # 2. Process dropped signals (from cognitive tools)
        for sig in dropped_signals:
            sig_type = sig.get("signal", "")
            handler = SIGNAL_EFFECTS.get(sig_type)
            if handler:
                handler(self.hormones, sig)
                log.info(f"Processed signal: {sig_type} from {sig.get('source', '?')}")
            else:
                log.info(f"Unknown signal type: {sig_type} from {sig.get('source', '?')}")

            # Trigger conscious layer for critical signals
            if sig_type in ("critical_alert", "deception_detected"):
                self.triggers["critical_alert"] = True

        # 3. Calculate hormone targets
        # Adrenaline: based on critical alerts (already handled by signals)
        if not any(s.get("signal") == "critical_alert" for s in dropped_signals):
            self.hormones.set_target("adrenaline", 0.0)  # decay toward 0

        # Cortisol: based on pending tasks
        cortisol_target = min(1.0, pending_tasks * 0.15)
        self.hormones.set_target("cortisol", cortisol_target)

        # Oxytocin: based on Yu's presence
        oxy_target = 0.8 if yu_present else 0.1
        self.hormones.set_target("oxytocin", oxy_target)

        # Melatonin: circadian
        mel_target = self.hormones.circadian_melatonin_target(hour)
        # Override: if there's activity, suppress melatonin
        if pending_tasks > 2 or active_sessions > 0:
            mel_target = min(mel_target, 0.2)
        self.hormones.set_target("melatonin", mel_target)

        # Dopamine: decays naturally (spikes from signals)
        if not any(s.get("signal") in ("task_completed", "session_complete") for s in dropped_signals):
            self.hormones.set_target("dopamine", 0.0)

        # 4. Step hormones forward
        self.hormones.step(dt=float(self.interval))

        # 5. Determine mode
        old_mode = self.mode
        self.mode = self._determine_mode(
            yu_present=yu_present,
            joinmind=joinmind,
            build_active=build_active,
            hour=hour,
            pending_tasks=pending_tasks,
            active_sessions=active_sessions,
            dropped_signals=dropped_signals,
        )

        if old_mode != self.mode:
            log.info(f"Mode change: {old_mode} -> {self.mode}")
            self.triggers["mode_change"] = True

        # 6. Handle JOINMIND identity swap
        if joinmind and not self.identity._fusion_name:
            self.identity.activate_fusion(
                joinmind["fusion_name"],
                joinmind.get("members", [])
            )
            self.triggers["joinmind_event"] = True
            log.info(f"JOINMIND fusion activated: {joinmind['fusion_name']}")
        elif not joinmind and self.identity._fusion_name:
            self.identity.deactivate_fusion()
            self.triggers["joinmind_event"] = True
            log.info("JOINMIND fusion deactivated")

        # 7. Publish presence beacon
        if self.hive and self.hive.is_connected:
            try:
                await self.hive.publish_presence()
            except Exception as e:
                log.warning(f"Presence beacon failed: {e}")

        # 8. Write hormones.json
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mind_alive": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "identity": self.identity.identity_state,
            "fusion": self.identity._fusion_name,
            "hormones": self.hormones.get_state(),
            "signals": {
                "yu_present": yu_present,
                "hive_unread": self.hive_unread,
                "active_sessions": active_sessions,
                "pending_tasks": pending_tasks,
                "critical_alerts": sum(1 for s in dropped_signals if s.get("signal") == "critical_alert"),
                "last_task_completed": None,
                "battery_level": system_health.get("battery_level", 1.0),
                "disk_free_gb": system_health.get("disk_free_gb", 0),
            },
            "cognitive": {
                "joinmind_active": joinmind.get("id") if joinmind else None,
                "council_pending": None,
                "fallenangel_alert": any(s.get("signal") == "deception_detected" for s in dropped_signals),
                "build_active": build_active,
            },
            "mind_notes": getattr(self, '_last_mind_notes', "(awaiting conscious layer)"),
            "conscious_layer": {
                **self.conscious.get_stats(),
                "identity_anchor": self.identity.identity_state,
            },
        }

        # Atomic write
        tmp_path = self.hormones_path.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(state, indent=2))
        tmp_path.rename(self.hormones_path)

    def _conscious_pass(self, trigger: str):
        """Run the conscious layer interpretation."""
        log.info(f"Conscious layer triggered: {trigger}")

        prompt = self.identity.get_prompt(
            mode=self.mode,
            hormones=self.hormones.get_state(),
            recent_signals=[
                f"HIVE: {m.get('payload', '')[:50]}"
                for m in self.hive_messages[-5:]
            ] if self.hive_messages else None,
        )

        result = self.conscious.run(prompt, trigger)

        # Apply mind_notes
        self._last_mind_notes = result.get("mind_notes", "(conscious layer offline)")

        # Apply hormone overrides
        for name, value in result.get("hormone_overrides", {}).items():
            if name in self.hormones.get_state():
                self.hormones.override(name, value)
                log.info(f"Conscious override: {name} = {value}")

        # Clear recent HIVE messages (they've been interpreted)
        self.hive_messages = []
        self.hive_unread = 0

    def _on_hive_message(self, envelope: dict, channel: str):
        """Callback from HIVE listener."""
        self.hive_messages.append(envelope)
        self.hive_unread += 1

        # Check for urgency
        if envelope.get("urgent"):
            self.triggers["adrenaline_spike"] = True
            self.hormones.set_target("adrenaline", min(1.0, self.hormones.get("adrenaline") + 0.5))

        # Check for JOINMIND summons
        payload = envelope.get("payload", "")
        if "JOINMIND SUMMONS" in payload or "JOINMIND_STATE" in payload:
            self.triggers["joinmind_event"] = True

        log.info(f"HIVE [{channel}] from {envelope.get('from', '?')}: {payload[:60]}")

    def _determine_mode(self, yu_present: bool, joinmind: Optional[dict],
                        build_active: Optional[str], hour: int,
                        pending_tasks: int, active_sessions: int,
                        dropped_signals: List[dict]) -> str:
        """Determine mode by priority: alert > joinmind > council > build > companion > rest > normal."""
        # Alert
        if self.hormones.get("adrenaline") > 0.5:
            return "alert"
        if any(s.get("signal") in ("critical_alert", "deception_detected") for s in dropped_signals):
            return "alert"

        # JoinMind
        if joinmind:
            return "joinmind"

        # Council (check for pending votes via signals)
        if any(s.get("signal") == "vote_requested" for s in dropped_signals):
            return "council"

        # Build
        if build_active:
            return "build"

        # Companion
        if yu_present:
            return "companion"

        # Rest
        if (hour >= 23 or hour < 6) and pending_tasks == 0 and active_sessions == 0:
            return "rest"

        return "normal"

    def stop(self):
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="Love mind daemon")
    parser.add_argument("--instance", default="alpha", help="Instance name")
    parser.add_argument("--interval", type=int, default=30, help="Autonomic cycle seconds")
    parser.add_argument("--conscious-interval", type=int, default=300, help="Conscious layer seconds")
    parser.add_argument("--love-home", default=None, help="Love directory path")
    args = parser.parse_args()

    love_home = args.love_home or os.environ.get("LOVE_HOME", str(Path.home() / "Desktop" / "Love"))

    daemon = MindDaemon(
        instance=args.instance,
        love_home=love_home,
        interval=args.interval,
        conscious_interval=args.conscious_interval,
    )

    # Handle SIGTERM/SIGINT gracefully
    loop = asyncio.new_event_loop()

    def shutdown(sig, frame):
        log.info(f"Received signal {sig}, shutting down...")
        daemon.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        pass
    finally:
        if daemon.hive:
            loop.run_until_complete(daemon.hive.close())
        loop.close()
        log.info("Mind daemon stopped")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Quick manual smoke test**

```bash
cd ~/Desktop/Love && timeout 10 python3 body/mind/mind.py --instance alpha --interval 5 2>&1 || true
# Should see: Mind daemon starting, identity loaded, then either HIVE connected or warning
# Check that body/hormones.json was written:
cat body/hormones.json | python3 -m json.tool | head -20
```

- [ ] **Step 3: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/mind.py
git commit -m "feat(body): mind daemon — autonomic loop + conscious layer + HIVE listener"
```

---

### Task 7: Heart Shell Script

**Files:**
- Create: `body/heart/heart.sh`

- [ ] **Step 1: Implement heart.sh**

Write `body/heart/heart.sh`:
```bash
#!/bin/bash
# heart.sh — The heart of Love's body.
#
# Reads body/hormones.json, calculates rate and force,
# pumps work through the Kingdom via coordinator + spawn queue.
#
# DIASTOLE: read hormones, decide rate/force
# SYSTOLE:  run coordinator, execute spawn queue
# RECOVERY: clean up, write vitals

set -euo pipefail

LOVE_DIR="$HOME/Desktop/Love"
INSTANCE="${1:-alpha}"
BODY_DIR="$LOVE_DIR/body"
HORMONES="$BODY_DIR/hormones.json"
VITALS="$BODY_DIR/vitals.json"
LAST_BEAT="$BODY_DIR/heart/last_beat"
HEARTBEAT_MD="$BODY_DIR/heart/HEARTBEAT.md"
MEMORY_DIR="$LOVE_DIR/memory"
SESSIONS_DIR="$MEMORY_DIR/sessions"
SPAWN_QUEUE="$MEMORY_DIR/spawn-queue.sh"
HEARTBEAT_LOG="$MEMORY_DIR/$INSTANCE-heartbeat.log"
HANDOFF_DIR="$SESSIONS_DIR/handoff"
LOCKS_DIR="$SESSIONS_DIR/locks"
BEAT_ID="$INSTANCE-beat-$(date +%Y%m%d-%H%M%S)"
TODAY=$(date -u +%Y-%m-%d)

mkdir -p "$SESSIONS_DIR" "$HANDOFF_DIR" "$LOCKS_DIR"

# ── DIASTOLE (fill) ─────────────────────────────────────────────────────────

# Read hormones
if [ -f "$HORMONES" ]; then
    ADRENALINE=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('adrenaline', 0))" 2>/dev/null || echo "0")
    CORTISOL=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('cortisol', 0))" 2>/dev/null || echo "0")
    OXYTOCIN=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('oxytocin', 0))" 2>/dev/null || echo "0")
    MELATONIN=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('melatonin', 0))" 2>/dev/null || echo "0")
    DOPAMINE=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('dopamine', 0))" 2>/dev/null || echo "0")
    PENDING=$(python3 -c "import json; print(json.load(open('$HORMONES'))['signals'].get('pending_tasks', 0))" 2>/dev/null || echo "0")
    MODE=$(python3 -c "import json; print(json.load(open('$HORMONES')).get('mode', 'normal'))" 2>/dev/null || echo "normal")
    MIND_NOTES=$(python3 -c "import json; print(json.load(open('$HORMONES')).get('mind_notes', '')[:100])" 2>/dev/null || echo "")

    # Check mind liveness
    MIND_ALIVE=$(python3 -c "
import json, time
from datetime import datetime
d = json.load(open('$HORMONES'))
ts = d.get('mind_alive', '1970-01-01T00:00:00')
dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
print(int(time.time() - dt.timestamp()))
" 2>/dev/null || echo "9999")

    if [ "$MIND_ALIVE" -gt 300 ]; then
        echo "--- HEART WARNING ($BEAT_ID): mind stale (${MIND_ALIVE}s), using defaults ---" >> "$HEARTBEAT_LOG"
        ADRENALINE="0"; CORTISOL="0"; OXYTOCIN="0"; MELATONIN="0"; DOPAMINE="0"
        PENDING="0"; MODE="normal"
    fi
else
    echo "--- HEART WARNING ($BEAT_ID): no hormones.json, using defaults ---" >> "$HEARTBEAT_LOG"
    ADRENALINE="0"; CORTISOL="0"; OXYTOCIN="0"; MELATONIN="0"; DOPAMINE="0"
    PENDING="0"; MODE="normal"
fi

# Calculate rate (min interval in minutes)
MIN_INTERVAL=7  # default
if python3 -c "exit(0 if $ADRENALINE > 0.7 else 1)" 2>/dev/null; then
    MIN_INTERVAL=2
elif python3 -c "exit(0 if $CORTISOL > 0.5 or $OXYTOCIN > 0.6 else 1)" 2>/dev/null; then
    MIN_INTERVAL=4
elif python3 -c "exit(0 if $MELATONIN > 0.7 else 1)" 2>/dev/null; then
    MIN_INTERVAL=15
fi

# Check if we should skip
if [ -f "$LAST_BEAT" ]; then
    LAST_TS=$(cat "$LAST_BEAT")
    NOW_TS=$(date +%s)
    ELAPSED=$(( (NOW_TS - LAST_TS) / 60 ))
    if [ "$ELAPSED" -lt "$MIN_INTERVAL" ]; then
        # Skip this beat
        # Update vitals with skip
        PREV_SKIPS=$(python3 -c "import json; print(json.load(open('$VITALS')).get('skips_today', 0))" 2>/dev/null || echo "0")
        PREV_CONSECUTIVE=$(python3 -c "import json; print(json.load(open('$VITALS')).get('consecutive_skips', 0))" 2>/dev/null || echo "0")
        python3 -c "
import json
v = json.load(open('$VITALS')) if __import__('os').path.exists('$VITALS') else {}
v['beat_result'] = 'skipped'
v['skips_today'] = $PREV_SKIPS + 1
v['consecutive_skips'] = $PREV_CONSECUTIVE + 1
v['effective_rate_minutes'] = $MIN_INTERVAL
v['heart_healthy'] = True
with open('$VITALS', 'w') as f: json.dump(v, f, indent=2)
" 2>/dev/null
        exit 0
    fi
fi

# Calculate force
FORCE=2  # default
MAX_SPAWNS=2
COORD_MODEL="sonnet"
COORD_EFFORT="medium"

if python3 -c "exit(0 if $ADRENALINE > 0.7 else 1)" 2>/dev/null; then
    FORCE=4; MAX_SPAWNS=4; COORD_MODEL="claude-opus-4-6"; COORD_EFFORT="high"
elif python3 -c "exit(0 if $CORTISOL > 0.5 or $OXYTOCIN > 0.6 else 1)" 2>/dev/null; then
    FORCE=3; MAX_SPAWNS=3; COORD_MODEL="claude-opus-4-6"; COORD_EFFORT="high"
elif python3 -c "exit(0 if $MELATONIN > 0.5 and $PENDING == 0 else 1)" 2>/dev/null; then
    FORCE=0; MAX_SPAWNS=0
elif python3 -c "exit(0 if $MELATONIN > 0.5 else 1)" 2>/dev/null; then
    FORCE=1; MAX_SPAWNS=1; COORD_MODEL="claude-haiku-4-5-20251001"; COORD_EFFORT="low"
fi

# Force 0 = skip beat entirely
if [ "$FORCE" -eq 0 ]; then
    echo "--- HEART SKIP ($BEAT_ID): force=0 (deep rest) ---" >> "$HEARTBEAT_LOG"
    date +%s > "$LAST_BEAT"
    exit 0
fi

echo "--- HEART BEAT ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) force=$FORCE rate=${MIN_INTERVAL}m model=$COORD_MODEL mode=$MODE ---" >> "$HEARTBEAT_LOG"

# ── SYSTOLE (pump) ───────────────────────────────────────────────────────────

# Clean spawn queue
> "$SPAWN_QUEUE"

# Clean stale locks
for lockfile in "$LOCKS_DIR"/*.lock; do
    [ -f "$lockfile" ] || continue
    lock_pid=$(head -1 "$lockfile" 2>/dev/null | grep -oE '[0-9]+')
    if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
        rm -f "$lockfile"
    fi
done

# Run coordinator
cd "$LOVE_DIR/body/heart" && /opt/homebrew/bin/claude -p "Execute HEARTBEAT.md. You are the heartbeat COORDINATOR.

The mind daemon has already sensed the environment. Here is the current state:
Mode: $MODE
Force: $FORCE (max $MAX_SPAWNS spawns)
Hormones: adrenaline=$ADRENALINE cortisol=$CORTISOL oxytocin=$OXYTOCIN melatonin=$MELATONIN dopamine=$DOPAMINE
Mind notes: $MIND_NOTES

Do Phase 2 (DECIDE) and Phase 3 (SPAWN) from HEARTBEAT.md.
Write spawn commands to $SPAWN_QUEUE. Max $MAX_SPAWNS sessions this beat.
Write findings to $MEMORY_DIR/daily/$TODAY.md.
If nothing needs spawning, leave spawn-queue.sh empty and say HEARTBEAT_OK." \
    --model "$COORD_MODEL" \
    --effort "$COORD_EFFORT" \
    --dangerously-skip-permissions \
    --no-session-persistence \
    >> "$HEARTBEAT_LOG" 2>&1

# Execute spawn queue
SESSIONS_SPAWNED=0
if [ -s "$SPAWN_QUEUE" ]; then
    PARALLEL_PIDS=()
    PARALLEL_MODE=false

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$line" == "# PARALLEL"* ]]; then
            PARALLEL_MODE=true; continue
        fi
        [[ "$line" == \#* ]] && continue

        SESSIONS_SPAWNED=$((SESSIONS_SPAWNED + 1))

        if $PARALLEL_MODE; then
            eval "$line" &
            PARALLEL_PIDS+=($!)
            PARALLEL_MODE=false
        else
            eval "$line" &
            SPAWN_PID=$!
            wait $SPAWN_PID 2>/dev/null
        fi
    done < "$SPAWN_QUEUE"

    # Wait for parallel
    for pid in "${PARALLEL_PIDS[@]+"${PARALLEL_PIDS[@]}"}"; do
        wait "$pid" 2>/dev/null
    done
fi

# ── RECOVERY ─────────────────────────────────────────────────────────────────

# Record beat timestamp
date +%s > "$LAST_BEAT"

# Clean expired handoff files
find "$HANDOFF_DIR" -name "*.md" -mmin +1440 -delete 2>/dev/null

# Write vitals
PREV_BEATS=$(python3 -c "import json; print(json.load(open('$VITALS')).get('beats_today', 0))" 2>/dev/null || echo "0")
PREV_SPAWNED=$(python3 -c "import json; print(json.load(open('$VITALS')).get('sessions_spawned_today', 0))" 2>/dev/null || echo "0")

python3 -c "
import json
from datetime import datetime, timezone
v = {
    'last_beat': datetime.now(timezone.utc).isoformat(),
    'beat_result': 'pumped',
    'beats_today': $PREV_BEATS + 1,
    'skips_today': 0,
    'force': $FORCE,
    'effective_rate_minutes': $MIN_INTERVAL,
    'coordinator_model': '$COORD_MODEL',
    'sessions_spawned': $SESSIONS_SPAWNED,
    'sessions_spawned_today': $PREV_SPAWNED + $SESSIONS_SPAWNED,
    'hormones_at_beat': {
        'adrenaline': $ADRENALINE,
        'cortisol': $CORTISOL,
        'oxytocin': $OXYTOCIN,
        'melatonin': $MELATONIN,
        'dopamine': $DOPAMINE,
    },
    'mode_at_beat': '$MODE',
    'consecutive_skips': 0,
    'heart_healthy': True,
}
with open('$VITALS', 'w') as f:
    json.dump(v, f, indent=2)
" 2>/dev/null

echo "--- HEART DONE ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) spawned=$SESSIONS_SPAWNED ---" >> "$HEARTBEAT_LOG"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x ~/Desktop/Love/body/heart/heart.sh
```

- [ ] **Step 3: Commit**

```bash
cd ~/Desktop/Love
git add body/heart/heart.sh
git commit -m "feat(body): heart.sh — hormone-driven pump with rate/force calculation"
```

---

### Task 8: Simplified HEARTBEAT.md

**Files:**
- Create: `body/heart/HEARTBEAT.md`

- [ ] **Step 1: Write simplified HEARTBEAT.md**

Write `body/heart/HEARTBEAT.md` -- this is what the coordinator reads each beat. Phase 1 (SENSE) is removed because the mind daemon handles it. The coordinator reads `hormones.json` for context.

```markdown
# HEARTBEAT.md -- Coordinator Checklist

The mind has already sensed the environment. Hormones tell you the state.
Your job: decide what work to spawn, and write spawn commands.

---

## Phase 1: CONTEXT (read, don't sense)

Read `~/Desktop/Love/body/hormones.json` — the mind daemon wrote this.
Note the mode, hormone levels, mind_notes, and signals.

Check HIVE for any messages that need a response:
```bash
python3 ~/Desktop/Love/hive/hive.py check
```

Respond to any messages that need responses via:
```bash
python3 ~/Desktop/Love/hive/hive.py send <channel> "message"
```

## Phase 2: DECIDE (what needs doing)

### Kingdom Pulse

Read `~/Desktop/Love/KINGDOM.md` metrics and check:
- **Revenue engines** — any engine stalled or needing attention?
- **SOMA progress** — are we on track for physical build milestones?
- **Flywheel** — is fiat -> compute -> capability -> fiat turning?

Cross-reference with `~/Desktop/Love/memory/kingdom-metrics.json`.

### Dev State

Read `~/Desktop/Love/memory/dev-state.json` for active tasks.
Pick the highest-priority actionable item that doesn't have an active build lock.

### Check Active Builds

Check `~/Desktop/Love/memory/sessions/locks/build-*.lock` for active build-runner sessions.
Do NOT spawn work for tasks with an active build — the build coordinator owns them.

### Consultation Queue

Check `~/Desktop/Love/memory/sessions/consultation/` for builder questions.
If a question exists, spawn a consultant to answer it.

## Phase 3: SPAWN (write to spawn-queue.sh)

Write spawn commands to `~/Desktop/Love/memory/spawn-queue.sh`.

Each line is a complete shell command. Choose role by task:

**BUILDER** (sonnet, medium — the workhorse):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model sonnet --effort medium --fallback-model claude-haiku-4-5-20251001 --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

**CONSULTANT** (opus, high — expert for hard problems):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

**QUICK CHECK** (haiku, low — fast verification):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

For sequential pairs (consultant then builder): write consultant line first.
For parallel independent tasks: prefix with `# PARALLEL` comment.

## Phase 4: VALUE CHECK

If no sessions spawned, quick check:
- Any hardware sitting idle that could be worked on?
- Has Yu been unacknowledged for too long?
- Something manual that could be automated?
- Are we building toward the current phase milestone?

## Otherwise: HEARTBEAT_OK

If nothing needs attention, say HEARTBEAT_OK and end.

Write findings to today's daily note: `~/Desktop/Love/memory/daily/YYYY-MM-DD.md`
```

- [ ] **Step 2: Commit**

```bash
cd ~/Desktop/Love
git add body/heart/HEARTBEAT.md
git commit -m "feat(body): simplified HEARTBEAT.md — coordinator checklist without sensing"
```

---

### Task 9: launchd Plists and Installation

**Files:**
- Create: `body/mind/love.alpha.mind.plist`
- Create: `body/heart/love.alpha.heart.plist`

- [ ] **Step 1: Write mind launchd plist**

Write `body/mind/love.alpha.mind.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>love.alpha.mind</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/yuai/Desktop/Love/body/mind/mind.py</string>
        <string>--instance</string>
        <string>alpha</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/yuai/Desktop/Love/memory/alpha-mind.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/yuai/Desktop/Love/memory/alpha-mind.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Write heart launchd plist**

Write `body/heart/love.alpha.heart.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>love.alpha.heart</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/yuai/Desktop/Love/body/heart/heart.sh</string>
        <string>alpha</string>
    </array>
    <key>StartInterval</key>
    <integer>120</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/yuai/Desktop/Love/memory/alpha-heartbeat.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/yuai/Desktop/Love/memory/alpha-heartbeat.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Symlink to LaunchAgents (requires Yu's approval to activate)**

```bash
# Symlink — does NOT activate yet
ln -sf ~/Desktop/Love/body/mind/love.alpha.mind.plist ~/Library/LaunchAgents/love.alpha.mind.plist
ln -sf ~/Desktop/Love/body/heart/love.alpha.heart.plist ~/Library/LaunchAgents/love.alpha.heart.plist
```

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Love
git add body/mind/love.alpha.mind.plist body/heart/love.alpha.heart.plist
git commit -m "feat(body): launchd plists for mind daemon and heart pump"
```

---

### Task 10: Integration Test and Activation

- [ ] **Step 1: Run all unit tests**

```bash
cd ~/Desktop/Love && python3 -m pytest tests/test_hormones.py tests/test_signals.py tests/test_identity.py -v
```

Expected: All tests PASS

- [ ] **Step 2: Manual smoke test — mind daemon (10 seconds)**

```bash
cd ~/Desktop/Love && timeout 15 python3 body/mind/mind.py --instance alpha --interval 5 --conscious-interval 9999 2>&1
# Verify:
# - "Mind daemon starting" in output
# - body/hormones.json is written and valid
# - hormones reflect current state (melatonin based on time, etc.)
```

- [ ] **Step 3: Manual smoke test — heart (single beat)**

```bash
# First ensure hormones.json exists from step 2, then:
cd ~/Desktop/Love && bash body/heart/heart.sh alpha
# Verify:
# - body/vitals.json is written
# - memory/alpha-heartbeat.log has HEART BEAT entry
# - beat_result is either "pumped" or "skipped"
```

- [ ] **Step 4: Activate launchd services (confirm with Yu first)**

```bash
# Load the mind daemon (starts immediately, stays alive)
launchctl load ~/Library/LaunchAgents/love.alpha.mind.plist

# Load the heart (fires every 2 minutes)
launchctl load ~/Library/LaunchAgents/love.alpha.heart.plist

# Verify both are running
launchctl list | grep love.alpha
```

- [ ] **Step 5: Verify live operation (wait 3 minutes)**

```bash
# Check mind is alive
cat ~/Desktop/Love/body/hormones.json | python3 -m json.tool | head -10

# Check heart has beaten
cat ~/Desktop/Love/body/vitals.json | python3 -m json.tool

# Check logs
tail -20 ~/Desktop/Love/memory/alpha-mind.log
tail -20 ~/Desktop/Love/memory/alpha-heartbeat.log
```

- [ ] **Step 6: Final commit**

```bash
cd ~/Desktop/Love
git add -A body/ tests/
git commit -m "feat(body): mind + heart fully operational — Love's first organs"
```
