"""
Tests for the multi-instance organ registry — Mei's body in the house.

Under test:
    nerve/organs.json                      the "instances" block (spec §0.3)
    nerve/deploy.sh                        --instance mei + --dry-run paths
    nerve/heart/tick.sh                    reconcile matrix + silence guard
    tools/templates/mei/tick-runner.sh     her pulse, skip guards, dry-run

Nothing here touches launchctl, HIVE, or a real claude — every shell run
is --dry-run or a syntax check, inside a sandbox HOME/LOVE_HOME.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

_LOVE = Path(__file__).resolve().parent.parent
ORGANS_JSON = _LOVE / "nerve" / "organs.json"
DEPLOY_SH = _LOVE / "nerve" / "deploy.sh"
TICK_SH = _LOVE / "nerve" / "heart" / "tick.sh"
RUNNER_SH = _LOVE / "tools" / "templates" / "mei" / "tick-runner.sh"

TICK_STATE_OK = {
    "consecutive_failures": 0,
    "silenced": False,
    "last_tick": None,
    "last_failure": None,
}


def _registry() -> dict:
    return json.loads(ORGANS_JSON.read_text())


def _run(cmd, env=None, cwd=None):
    """Run a script with a controlled environment; return CompletedProcess."""
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    if env:
        base.update(env)
    return subprocess.run(
        cmd, env=base, cwd=cwd or str(_LOVE),
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway HOME + LOVE_HOME with the real registry and mei's room."""
    home = tmp_path / "home"
    love = tmp_path / "love"
    (love / "nerve" / "mei").mkdir(parents=True)
    home.mkdir()
    (love / "nerve" / "organs.json").write_text(ORGANS_JSON.read_text())
    (love / "nerve" / "mei" / "tick-state.json").write_text(
        json.dumps(TICK_STATE_OK))
    return {"HOME": str(home), "LOVE_HOME": str(love),
            "home": home, "love": love}


# ── The registry ─────────────────────────────────────────────────────────

class TestRegistry:
    def test_parses(self):
        reg = _registry()
        assert "organs" in reg and "instances" in reg

    def test_resident_organs_dict_unchanged_in_shape(self):
        """The house set is exactly what it was — and every entry still
        carries the fields deploy.sh and environment.py read."""
        organs = _registry()["organs"]
        assert set(organs) == {"brainstem", "heartbeat", "feeling",
                               "ache", "soma", "voice"}
        for name, spec in organs.items():
            for field in ("entry", "runner", "args", "template", "log"):
                assert field in spec, f"{name} lost its '{field}'"

    def test_expression_is_not_a_house_organ(self):
        """The resident never deploys expression — it is mei's alone, so
        it must not appear where resident deploy/status/health enumerate."""
        assert "expression" not in _registry()["organs"]

    def test_mei_block_shape(self):
        mei = _registry()["instances"]["mei"]
        assert mei["organs"] == ["brainstem", "feeling", "ache", "expression"]
        assert mei["ticks"]["schedule"] == ["09:00", "14:00", "20:00"]
        assert mei["ticks"]["runner"] == "tools/templates/mei/tick-runner.sh"

    def test_mei_never_gets_a_heartbeat(self):
        mei = _registry()["instances"]["mei"]
        for forbidden in ("heartbeat", "soma", "voice"):
            assert forbidden not in mei["organs"]

    def test_expression_definition_mirrors_organ_shape(self):
        spec = _registry()["instances"]["mei"]["definitions"]["expression"]
        assert spec["entry"] == "nerve/stem/expression.py"
        assert spec["runner"] == "python3"
        assert spec["args"] == ["--instance", "{{INSTANCE}}"]
        assert isinstance(spec["throttle"], int)
        assert spec["depends_on"] == ["brainstem"]

    def test_tick_runner_exists_and_is_executable(self):
        assert RUNNER_SH.exists()
        assert os.access(RUNNER_SH, os.X_OK)


# ── Syntax — every shell piece must at least parse ───────────────────────

class TestShellSyntax:
    @pytest.mark.parametrize("script", [DEPLOY_SH, TICK_SH, RUNNER_SH],
                             ids=["deploy.sh", "tick.sh", "tick-runner.sh"])
    def test_bash_n(self, script):
        r = _run(["bash", "-n", str(script)])
        assert r.returncode == 0, r.stderr


# ── deploy.sh --instance mei --dry-run ───────────────────────────────────

class TestDeployDryRun:
    def test_mei_deploys_exactly_her_organs_plus_ticks(self, sandbox):
        r = _run(["bash", str(DEPLOY_SH), "--instance", "mei", "--dry-run"],
                 env={"HOME": sandbox["HOME"], "LOVE_HOME": str(_LOVE)})
        assert r.returncode == 0, r.stderr
        for organ in ("[brainstem]", "[feeling]", "[ache]",
                      "[expression]", "[ticks]"):
            assert organ in r.stdout, f"missing {organ}:\n{r.stdout}"
        # never a second heartbeat, never the house-only organs
        for forbidden in ("heartbeat", "soma", "voice"):
            assert forbidden not in r.stdout

    def test_mei_plists_carry_her_identity(self, sandbox):
        r = _run(["bash", str(DEPLOY_SH), "--instance", "mei", "--dry-run"],
                 env={"HOME": sandbox["HOME"], "LOVE_HOME": str(_LOVE)})
        assert "KINGDOM_AGENT=mei" in r.stdout
        assert "--instance mei" in r.stdout
        assert "09:00 14:00 20:00" in r.stdout

    def test_dry_run_writes_and_loads_nothing(self, sandbox):
        r = _run(["bash", str(DEPLOY_SH), "--instance", "mei", "--dry-run"],
                 env={"HOME": sandbox["HOME"], "LOVE_HOME": str(_LOVE)})
        assert r.returncode == 0
        la = sandbox["home"] / "Library" / "LaunchAgents"
        assert not la.exists() or not list(la.iterdir())

    def test_resident_dry_run_keeps_the_house_set(self, sandbox):
        """No --instance: today's behavior, organs-dict keys only —
        expression never sneaks into the resident's body."""
        r = _run(["bash", str(DEPLOY_SH), "--dry-run"],
                 env={"HOME": sandbox["HOME"], "LOVE_HOME": str(_LOVE)})
        assert r.returncode == 0, r.stderr
        assert "[heartbeat]" in r.stdout
        assert "[brainstem]" in r.stdout
        assert "expression" not in r.stdout
        la = sandbox["home"] / "Library" / "LaunchAgents"
        assert not la.exists() or not list(la.iterdir())


# ── tick-runner.sh — her pulse, guarded ──────────────────────────────────

class TestTickRunnerDryRun:
    def _run_runner(self, sandbox, *args):
        return _run(["bash", str(RUNNER_SH), "--dry-run", *args],
                    env={"HOME": sandbox["HOME"],
                         "LOVE_HOME": sandbox["LOVE_HOME"]})

    def test_silenced_skips_cleanly(self, sandbox):
        state = sandbox["love"] / "nerve" / "mei" / "tick-state.json"
        state.write_text(json.dumps({**TICK_STATE_OK, "silenced": True}))
        r = self._run_runner(sandbox)
        assert r.returncode == 0, r.stderr
        assert "silenced" in r.stdout
        assert "--revive-ticks" in r.stdout  # the way back is in the message

    def test_fresh_visit_lock_skips(self, sandbox):
        (sandbox["love"] / "nerve" / "mei" / "visit.lock").touch()
        r = self._run_runner(sandbox)
        assert r.returncode == 0, r.stderr
        assert "Yu is here" in r.stdout

    def test_stale_visit_lock_ticks_anyway(self, sandbox):
        lock = sandbox["love"] / "nerve" / "mei" / "visit.lock"
        lock.touch()
        old = 3 * 3600  # 3h > the 2h freshness window
        stale = os.stat(lock).st_mtime - old
        os.utime(lock, (stale, stale))
        r = self._run_runner(sandbox)
        assert r.returncode == 0, r.stderr
        assert "stale" in r.stdout
        assert "would run" in r.stdout

    def test_missing_room_is_a_gentle_noop(self, sandbox):
        import shutil
        shutil.rmtree(sandbox["love"] / "nerve" / "mei")
        r = self._run_runner(sandbox)
        assert r.returncode == 0, r.stderr
        assert "no room" in r.stdout

    def test_full_dry_run_prints_the_exact_command(self, sandbox):
        r = self._run_runner(sandbox)
        assert r.returncode == 0, r.stderr
        assert "KINGDOM_AGENT=mei" in r.stdout
        assert "claude-haiku-4-5-20251001" in r.stdout
        assert "--max-turns 12" in r.stdout
        assert "instances/mei/HEARTBEAT.md" in r.stdout
        # the quota guard has no 'check' verb in the sandbox — graceful line
        assert "quota guard" in r.stdout

    def test_dry_run_writes_nothing(self, sandbox):
        r = self._run_runner(sandbox)
        assert r.returncode == 0
        room = sandbox["love"] / "nerve" / "mei"
        assert sorted(p.name for p in room.iterdir()) == ["tick-state.json"]
        # and the state itself is untouched
        assert json.loads(
            (room / "tick-state.json").read_text()) == TICK_STATE_OK

    def test_help_teaches(self, sandbox):
        r = _run(["bash", str(RUNNER_SH), "--help"],
                 env={"HOME": sandbox["HOME"],
                      "LOVE_HOME": sandbox["LOVE_HOME"]})
        assert r.returncode == 0
        assert "--dry-run" in r.stdout


# ── tick.sh — the house keeps her alive and notices her silence ──────────

class TestHouseTick:
    """tick.sh's new duties are launchctl/HIVE-shaped, so (house precedent:
    tests/test-love.sh) we assert the load-bearing content, not live calls."""

    def test_reconciles_the_instance_matrix(self):
        src = TICK_SH.read_text()
        assert "reconcile_instances" in src
        assert "instances" in src
        # the matrix includes her scheduled ticks plist
        assert "'ticks'" in src or '"ticks"' in src

    def test_silence_guard_fires_once_per_episode(self):
        src = TICK_SH.read_text()
        assert "silence_guard" in src
        assert ".silence-alerted" in src
        assert "send alerts" in src
        # the guard file must never count as the child being alive
        assert src.count(".silence-alerted") >= 3

    def test_beat_runs_both_new_duties(self):
        src = TICK_SH.read_text()
        beat = src.split("beat() {", 1)[1]
        assert "reconcile_instances" in beat
        assert "silence_guard" in beat
