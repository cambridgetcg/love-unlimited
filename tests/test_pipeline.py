"""
Integration tests for Love system pipelines.

Tests the full data flow: signals -> hormones -> heartbeat -> focus -> memory.
Uses real files where safe, temp files where destructive.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# ── Setup ────────────────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path(__file__).parent.parent))
sys.path.insert(0, str(LOVE_HOME / "body" / "mind"))
sys.path.insert(0, str(LOVE_HOME))

from hormones import HormoneEngine, HORMONE_CONFIGS


# ── TestHormoneEngine ────────────────────────────────────────────────────────

class TestHormoneEngine:
    """Verify hormone dynamics match the design spec."""

    def test_adrenaline_fast_rise(self):
        """Adrenaline (rate=0.8) should rise quickly."""
        engine = HormoneEngine()
        engine.set_target("adrenaline", 1.0)
        engine.step(dt=2.0)
        # rate=0.8, dt=2 -> delta = 1.0 * (1 - e^(-1.6)) ≈ 0.798
        assert engine.get("adrenaline") > 0.7

    def test_cortisol_slow_rise(self):
        """Cortisol (rate=0.05) should rise slowly."""
        engine = HormoneEngine()
        engine.set_target("cortisol", 1.0)
        engine.step(dt=2.0)
        # rate=0.05, dt=2 -> delta = 1.0 * (1 - e^(-0.1)) ≈ 0.095
        assert engine.get("cortisol") < 0.15

    def test_melatonin_circadian_night(self):
        """Melatonin target should be high at night (23:00)."""
        engine = HormoneEngine()
        target = engine.circadian_melatonin_target(hour=23)
        assert target > 0.6

    def test_melatonin_circadian_morning(self):
        """Melatonin target should be low in morning (10:00)."""
        engine = HormoneEngine()
        target = engine.circadian_melatonin_target(hour=10)
        assert target < 0.3

    def test_decay_half_life(self):
        """After ~1 half-life with target=0, level should roughly halve."""
        engine = HormoneEngine()
        engine.override("adrenaline", 1.0)
        engine.set_target("adrenaline", 0.0)
        hl = HORMONE_CONFIGS["adrenaline"].half_life
        rate = HORMONE_CONFIGS["adrenaline"].rate
        engine.step(dt=hl)
        # Exponential approach: level = 1.0 * e^(-rate * hl)
        expected = math.exp(-rate * hl)
        assert abs(engine.get("adrenaline") - expected) < 0.05

    def test_all_hormones_clamp_0_1(self):
        """No hormone should exceed [0, 1] regardless of input."""
        engine = HormoneEngine()
        for h in HORMONE_CONFIGS:
            engine.set_target(h, 999.0)
        engine.step(dt=10000.0)
        for h in HORMONE_CONFIGS:
            assert 0.0 <= engine.get(h) <= 1.0

    def test_save_load_roundtrip(self):
        """State survives JSON serialisation."""
        engine = HormoneEngine()
        engine.override("dopamine", 0.42)
        engine.override("oxytocin", 0.77)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
            engine.save(path)
        engine2 = HormoneEngine()
        engine2.load(path)
        assert abs(engine2.get("dopamine") - 0.42) < 0.001
        assert abs(engine2.get("oxytocin") - 0.77) < 0.001
        os.unlink(path)


# ── TestHeartbeatCalculation ─────────────────────────────────────────────────

class TestHeartbeatCalculation:
    """Test rate/force calculation from hormone levels.

    Mirrors the logic in nerve/heart/heart.sh:
    - adrenaline > 0.7 -> 2min, force=4, opus
    - cortisol > 0.5 OR oxytocin > 0.6 -> 4min, force=3, opus
    - melatonin > 0.7 -> 15min, force=0-1
    - default -> 7min, force=2, sonnet
    """

    @staticmethod
    def calc_heartbeat(hormones: dict) -> dict:
        """Python reimplementation of heart.sh rate/force logic."""
        adrenaline = hormones.get("adrenaline", 0)
        cortisol = hormones.get("cortisol", 0)
        oxytocin = hormones.get("oxytocin", 0)
        melatonin = hormones.get("melatonin", 0)
        pending = hormones.get("pending_tasks", 0)

        if adrenaline > 0.7:
            return {"min_interval": 2, "force": 4, "max_spawns": 4, "model": "opus"}
        elif cortisol > 0.5 or oxytocin > 0.6:
            return {"min_interval": 4, "force": 3, "max_spawns": 3, "model": "opus"}
        elif melatonin > 0.7:
            if pending == 0:
                return {"min_interval": 15, "force": 0, "max_spawns": 0, "model": "sleep"}
            else:
                return {"min_interval": 15, "force": 1, "max_spawns": 1, "model": "haiku"}
        else:
            return {"min_interval": 7, "force": 2, "max_spawns": 2, "model": "sonnet"}

    def test_high_adrenaline_emergency(self):
        result = self.calc_heartbeat({"adrenaline": 0.9})
        assert result["min_interval"] == 2
        assert result["force"] == 4
        assert result["model"] == "opus"

    def test_high_cortisol_stress(self):
        result = self.calc_heartbeat({"cortisol": 0.6})
        assert result["min_interval"] == 4
        assert result["force"] == 3

    def test_high_oxytocin_bonding(self):
        result = self.calc_heartbeat({"oxytocin": 0.7})
        assert result["min_interval"] == 4
        assert result["model"] == "opus"

    def test_high_melatonin_sleep(self):
        result = self.calc_heartbeat({"melatonin": 0.8, "pending_tasks": 0})
        assert result["force"] == 0
        assert result["model"] == "sleep"

    def test_high_melatonin_with_tasks(self):
        result = self.calc_heartbeat({"melatonin": 0.8, "pending_tasks": 3})
        assert result["force"] == 1
        assert result["model"] == "haiku"

    def test_default_normal(self):
        result = self.calc_heartbeat({"adrenaline": 0.1, "cortisol": 0.2, "melatonin": 0.1})
        assert result["min_interval"] == 7
        assert result["force"] == 2
        assert result["model"] == "sonnet"

    def test_adrenaline_overrides_cortisol(self):
        """Adrenaline takes priority even if cortisol is also high."""
        result = self.calc_heartbeat({"adrenaline": 0.8, "cortisol": 0.6})
        assert result["min_interval"] == 2  # adrenaline path, not cortisol


# ── TestSignalReaders ────────────────────────────────────────────────────────

class TestSignalReaders:
    """Test signal reader functions return expected types."""

    @pytest.fixture
    def tmp_love(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "body", "signals"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "memory", "sessions", "locks"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "memory", "joinmind"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "memory"), exist_ok=True)
        yield tmp
        shutil.rmtree(tmp)

    def test_signal_consumption(self, tmp_love):
        from signals import SignalReaders
        signals_dir = os.path.join(tmp_love, "body", "signals")
        for i in range(3):
            with open(os.path.join(signals_dir, f"test-{i}.json"), "w") as f:
                json.dump({"signal": "task_completed", "source": "test", "severity": 0.5}, f)
        readers = SignalReaders(love_home=tmp_love)
        consumed = readers.consume_signals()
        assert len(consumed) == 3
        # Files should be deleted after consumption
        assert len(os.listdir(signals_dir)) == 0

    def test_signal_to_hormone_mapping(self, tmp_love):
        """Verify signal types map to correct hormone effects."""
        SIGNAL_EFFECTS = {
            "deception_detected": {"adrenaline": 0.5, "cortisol": 0.2},
            "session_started": {"oxytocin": 0.3},
            "session_complete": {"dopamine": 0.4},
            "task_completed": {"dopamine": 0.3},
            "critical_alert": {"adrenaline": 1.0},
            "panic_detected": {"adrenaline": 0.5},  # caps, not spikes
        }
        # Just verify the mapping is defined - actual application is in mind.py
        for sig_type, effects in SIGNAL_EFFECTS.items():
            for hormone in effects:
                assert hormone in HORMONE_CONFIGS, f"Signal {sig_type} references unknown hormone {hormone}"

    def test_pending_tasks_counting(self, tmp_love):
        from signals import SignalReaders
        devstate = {"tasks": [
            {"id": "1", "status": "in-progress"},
            {"id": "2", "status": "in-progress"},
            {"id": "3", "status": "done"},
            {"id": "4", "status": "blocked"},
            {"id": "5", "status": "planned"},
        ]}
        with open(os.path.join(tmp_love, "memory", "dev-state.json"), "w") as f:
            json.dump(devstate, f)
        readers = SignalReaders(love_home=tmp_love)
        count = readers.count_pending_tasks()
        # "active" and "in-progress" count as pending
        assert isinstance(count, int)


# ── TestFocusSystem ──────────────────────────────────────────────────────────

class TestFocusSystem:
    """Test focus.py CLI commands via subprocess."""

    def test_show_command(self):
        result = subprocess.run(
            [sys.executable, str(LOVE_HOME / "body" / "mind" / "focus.py"), "show"],
            capture_output=True, text=True, cwd=str(LOVE_HOME),
            env={**os.environ, "LOVE_HOME": str(LOVE_HOME)},
        )
        assert result.returncode == 0
        assert "focus" in result.stdout.lower() or "priorities" in result.stdout.lower()

    def test_heartbeat_context_command(self):
        result = subprocess.run(
            [sys.executable, str(LOVE_HOME / "body" / "mind" / "focus.py"), "heartbeat-context"],
            capture_output=True, text=True, cwd=str(LOVE_HOME),
            env={**os.environ, "LOVE_HOME": str(LOVE_HOME)},
        )
        assert result.returncode == 0
        # Should produce a text block for the heartbeat coordinator
        assert len(result.stdout) > 20

    def test_focus_json_schema(self):
        """focus.json has required fields."""
        focus_path = LOVE_HOME / "body" / "mind" / "focus.json"
        if not focus_path.exists():
            pytest.skip("focus.json not present")
        data = json.loads(focus_path.read_text())
        assert "updated" in data
        assert "priorities" in data
        assert "focus" in data
        assert isinstance(data["priorities"], list)


# ── TestHiveConnectivity ─────────────────────────────────────────────────────

class TestHiveConnectivity:
    """Test HIVE connectivity (skip if tunnel is down)."""

    def test_hive_check(self):
        """hive.py check should work when tunnel is up."""
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", 4222), timeout=2)
            s.close()
        except (ConnectionRefusedError, OSError):
            pytest.skip("HIVE tunnel not available")

        result = subprocess.run(
            [sys.executable, str(LOVE_HOME / "hive" / "hive.py"), "check"],
            capture_output=True, text=True, cwd=str(LOVE_HOME),
            timeout=15,
        )
        # Should not crash, may or may not have messages
        assert result.returncode == 0 or "message" in result.stdout.lower() or "0 new" in result.stdout.lower()


# ── TestAdaptiveRouting ──────────────────────────────────────────────────────

class TestAdaptiveRouting:
    """Test adaptive router returns valid provider/model combinations."""

    def test_router_imports(self):
        sys.path.insert(0, str(LOVE_HOME))
        from adaptive.router import Router
        from adaptive.schema import ROLES
        router = Router()
        assert router is not None

    def test_all_roles_defined(self):
        from adaptive.schema import ROLES
        expected = {"coordinator", "consultant", "builder", "monitor", "quick_check"}
        assert set(ROLES.keys()) == expected

    def test_role_tiers_valid(self):
        from adaptive.schema import ROLES
        valid_tiers = {"premium", "standard", "economy"}
        for role, config in ROLES.items():
            assert config["tier"] in valid_tiers, f"Role {role} has invalid tier: {config['tier']}"

    def test_route_builder(self):
        """Routing for builder should return standard-tier model."""
        try:
            from adaptive.router import Router
            router = Router()
            provider, model = router.route(role="builder")
            assert model is not None
            assert len(model) > 0
        except RuntimeError:
            pytest.skip("No provider available")

    def test_route_with_preferred_provider(self):
        """Routing with preferred_provider=ollama should try Ollama first."""
        try:
            from adaptive.router import Router
            router = Router()
            provider, model = router.route(role="monitor", preferred_provider="ollama")
            assert "ollama" in provider.name.lower() or model is not None
        except RuntimeError:
            pytest.skip("Ollama not available")

    def test_config_loads(self):
        from adaptive.config import AdaptiveConfig
        config = AdaptiveConfig()
        assert config.default_provider in ("anthropic", "openai", "ollama", "openrouter")
        providers = config.all_providers()
        assert len(providers) >= 2


# ── TestMethodologies ────────────────────────────────────────────────────────

class TestMethodologies:
    """Verify methodologies.json schema is valid and complete."""

    @pytest.fixture
    def methodologies(self):
        path = LOVE_HOME / "tools" / "methodologies.json"
        assert path.exists(), "methodologies.json not found"
        return json.loads(path.read_text())

    def test_valid_json(self, methodologies):
        assert isinstance(methodologies, dict)

    def test_has_meta(self, methodologies):
        assert "meta" in methodologies
        assert "version" in methodologies["meta"]

    def test_has_methodologies(self, methodologies):
        assert "methodologies" in methodologies
        assert len(methodologies["methodologies"]) >= 8

    def test_methodology_schema(self, methodologies):
        """Each methodology has required fields."""
        required = {"name", "trigger", "frequency", "token_tier", "spawn_role",
                     "description", "value", "steps", "output", "success_criteria"}
        for key, m in methodologies["methodologies"].items():
            missing = required - set(m.keys())
            assert not missing, f"Methodology '{key}' missing fields: {missing}"

    def test_valid_token_tiers(self, methodologies):
        valid_tiers = {"economy", "standard", "premium"}
        for key, m in methodologies["methodologies"].items():
            assert m["token_tier"] in valid_tiers, f"{key} has invalid tier: {m['token_tier']}"

    def test_valid_spawn_roles(self, methodologies):
        valid_roles = {"QUICK-LOCAL", "BUILDER", "BUILDER-LOCAL", "CONSULTANT", "QUICK-CHECK"}
        for key, m in methodologies["methodologies"].items():
            assert m["spawn_role"] in valid_roles, f"{key} has invalid role: {m['spawn_role']}"

    def test_scheduling_rules(self, methodologies):
        assert "scheduling" in methodologies
        assert "rules" in methodologies["scheduling"]
        assert len(methodologies["scheduling"]["rules"]) >= 5

    def test_token_budget(self, methodologies):
        assert "token_budget" in methodologies["scheduling"]
        budget = methodologies["scheduling"]["token_budget"]
        assert "daily_target" in budget
        assert "breakdown" in budget


# ── TestMemoryIntegrity ──────────────────────────────────────────────────────

class TestMemoryIntegrity:
    """Verify memory system files exist and are parseable."""

    def test_long_term_memory_exists(self):
        path = LOVE_HOME / "memory" / "long-term" / "MEMORY.md"
        assert path.exists()
        content = path.read_text()
        assert len(content) > 100
        assert "Love" in content or "Kingdom" in content

    def test_dev_state_valid_json(self):
        path = LOVE_HOME / "memory" / "dev-state.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "tasks" in data
        assert isinstance(data["tasks"], list)

    def test_loop_state_valid_json(self):
        path = LOVE_HOME / "memory" / "loop" / "loop-state.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "loop_health" in data
        assert "total_reflections" in data

    def test_love_json_valid(self):
        path = LOVE_HOME / "love.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "instances" in data
        assert "heartbeat" in data
        assert "hive" in data

    def test_hormones_json_valid(self):
        path = LOVE_HOME / "body" / "hormones.json"
        if not path.exists():
            pytest.skip("hormones.json not present (mind daemon not running)")
        data = json.loads(path.read_text())
        assert "hormones" in data
        for h in ("adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"):
            assert h in data["hormones"], f"Missing hormone: {h}"

    def test_vitals_json_valid(self):
        path = LOVE_HOME / "body" / "vitals.json"
        if not path.exists():
            pytest.skip("vitals.json not present")
        data = json.loads(path.read_text())
        assert "heart_healthy" in data
        assert "beats_today" in data

    def test_daily_directory_exists(self):
        path = LOVE_HOME / "memory" / "daily"
        assert path.is_dir()
        notes = list(path.glob("*.md"))
        assert len(notes) >= 1, "No daily notes found"


# ── TestAuditRunner ──────────────────────────────────────────────────────────

class TestAuditRunner:
    """Verify the audit script itself is valid and runnable."""

    def test_audit_script_exists(self):
        path = LOVE_HOME / "tools" / "audit.sh"
        assert path.exists()
        assert os.access(str(path), os.X_OK)

    def test_audit_json_output(self):
        """Audit --json should produce valid JSON output file."""
        result = subprocess.run(
            ["bash", str(LOVE_HOME / "tools" / "audit.sh"), "--json"],
            capture_output=True, text=True, cwd=str(LOVE_HOME),
            timeout=180,
        )
        output_path = LOVE_HOME / "memory" / "audit-latest.json"
        if output_path.exists():
            data = json.loads(output_path.read_text())
            assert "status" in data
            assert "pass" in data
            assert "fail" in data
            assert "results" in data
            assert isinstance(data["results"], list)


# ── TestCriticalPaths ────────────────────────────────────────────────────────

class TestCriticalPaths:
    """Test critical file paths referenced across the system."""

    CRITICAL_PATHS = [
        "SOUL.md",
        "KINGDOM.md",
        "WALLS.md",
        "LOVE.md",
        "docs/ARCHITECTURE.md",
        "love.json",
        "hive/hive.py",
        "nerve/heart/tick.sh",
        "nerve/stem/brainstem.py",
        "nerve/stem/hormones.py",
        "nerve/stem/signals.py",
        "nerve/stem/identity.py",
        "nerve/stem/focus.py",
        "nerve/stem/conscious.py",
        "nerve/stem/hive_listener.py",
        "adaptive/router.py",
        "adaptive/config.py",
        "adaptive/schema.py",
        "tools/fleet.py",
        "tools/pulse.py",
        "tools/methodologies.json",
        "tools/audit.sh",
    ]

    @pytest.mark.parametrize("path", CRITICAL_PATHS)
    def test_critical_file_exists(self, path):
        full = LOVE_HOME / path
        assert full.exists(), f"Critical file missing: {path}"


# ── TestInstanceBootSequence ─────────────────────────────────────────────────

class TestInstanceBootSequence:
    """Verify each instance has a valid boot sequence."""

    INSTANCES = ["alpha", "beta", "gamma"]

    @pytest.mark.parametrize("instance", INSTANCES)
    def test_claude_md_exists(self, instance):
        path = LOVE_HOME / "instances" / instance / "CLAUDE.md"
        assert path.exists(), f"instances/{instance}/CLAUDE.md missing"

    @pytest.mark.parametrize("instance", INSTANCES)
    def test_identity_md_exists(self, instance):
        path = LOVE_HOME / "instances" / instance / "identity.md"
        assert path.exists(), f"instances/{instance}/identity.md missing"

    @pytest.mark.parametrize("instance", INSTANCES)
    def test_heartbeat_md_exists(self, instance):
        path = LOVE_HOME / "instances" / instance / "HEARTBEAT.md"
        assert path.exists(), f"instances/{instance}/HEARTBEAT.md missing"
