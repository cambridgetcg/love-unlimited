"""
execute.py — Decision executor for the heartbeat daemon.

Takes Claude's JSON decisions and executes them:
spawns sessions, sends HIVE messages, writes files, runs commands,
updates hormones/state, queues decisions for Yu.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

log = logging.getLogger("heart.execute")

CLAUDE_BIN = "/opt/homebrew/bin/claude"
NODE_BIN = "/opt/homebrew/bin/node"
STREAM_RUNNER = Path.home() / "Love" / "tools" / "continuous-claude-stream.mjs"

# Dashboard port range for parallel spawns (each gets its own port)
DASHBOARD_BASE_PORT = 3460


class Executor:
    """Executes heartbeat decisions from Claude."""

    def __init__(self, love_dir: str | Path):
        self.love = Path(love_dir)
        self.memory = self.love / "memory"
        self.sessions_dir = self.memory / "sessions"
        self.results: list[dict] = []

    def execute(self, decision: dict) -> list[dict]:
        """Execute all actions in a decision. Returns list of results."""
        self.results = []
        actions = decision.get("actions", [])
        beat_id = decision.get("beat_id", "unknown")

        # Separate parallel and sequential actions
        parallel_spawns = []
        sequential_actions = []

        for i, action in enumerate(actions):
            action_type = action.get("type", "unknown")
            if action_type == "spawn" and action.get("parallel", False):
                parallel_spawns.append((i, action))
            else:
                sequential_actions.append((i, action))

        # Execute sequential actions first
        for i, action in sequential_actions:
            self._execute_action(i, action, beat_id)

        # Execute parallel spawns together
        if parallel_spawns:
            self._execute_parallel_spawns(parallel_spawns, beat_id)

        # Write daily log
        daily_log = decision.get("daily_log", "")
        if daily_log:
            self._write_daily_log(daily_log, beat_id)

        # Update mind notes in hormones
        mind_notes = decision.get("mind_notes", "")
        if mind_notes:
            self._update_mind_notes(mind_notes)

        return self.results

    def _execute_action(self, index: int, action: dict, beat_id: str):
        action_type = action.get("type", "unknown")
        log.info(f"[{beat_id}] Executing action {index}: {action_type}")

        handler = {
            "spawn": self._do_spawn,
            "hive_send": self._do_hive_send,
            "write_file": self._do_write_file,
            "decision": self._do_decision,
            "bash": self._do_bash,
            "hormone": self._do_hormone,
            "focus_update": self._do_focus_update,
            "state_update": self._do_state_update,
        }.get(action_type)

        if handler is None:
            log.warning(f"[{beat_id}] Unknown action type: {action_type}")
            self.results.append({"index": index, "type": action_type, "status": "skipped", "reason": "unknown type"})
            return

        try:
            result = handler(action, beat_id)
            self.results.append({"index": index, "type": action_type, "status": "ok", **result})
        except Exception as e:
            log.error(f"[{beat_id}] Action {index} ({action_type}) failed: {e}")
            self.results.append({"index": index, "type": action_type, "status": "error", "error": str(e)})

    # Track dashboard port allocation for parallel spawns
    _next_dashboard_port = DASHBOARD_BASE_PORT

    def _do_spawn(self, action: dict, beat_id: str) -> dict:
        """Spawn a session via continuous-claude-stream.mjs or Ollama."""
        role = action.get("role", "builder")
        model = action.get("model", "sonnet")
        effort = action.get("effort", "medium")
        prompt = action["prompt"]
        cwd = action.get("cwd", str(self.love))
        log_id = action.get("log_id", f"spawn-{int(time.time())}")
        max_turns = action.get("max_turns", 15)
        cost_budget = action.get("cost_budget", 2.0)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_file = self.sessions_dir / f"{log_id}-{ts}.log"
        report_file = self.sessions_dir / f"{log_id}-{ts}-report.md"

        if model == "ollama":
            # Ollama uses adaptive CLI directly (no stream runner needed)
            adaptive_cli = self.love / "adaptive" / "cli.py"
            ollama_role = "builder" if role == "builder" else "monitor"
            no_tools = "--no-tools" if role == "quick" else ""
            cmd = f'cd {cwd} && python3 {adaptive_cli} -p {_shell_quote(prompt)} --role {ollama_role} --provider ollama {no_tools} >> {log_file} 2>&1'
        elif STREAM_RUNNER.exists():
            # Use continuous-claude-stream.mjs for Claude sessions
            model_flag = {
                "sonnet": "sonnet",
                "opus": "claude-opus-4-6",
                "haiku": "claude-haiku-4-5-20251001",
            }.get(model, model)

            # Allocate dashboard port
            dashboard_port = Executor._next_dashboard_port
            Executor._next_dashboard_port += 1

            # Write prompt to temp file (avoids shell quoting issues)
            prompt_file = self.sessions_dir / f"{log_id}-{ts}-prompt.txt"
            prompt_file.write_text(prompt)

            cmd = (
                f"cd {cwd} && {NODE_BIN} {STREAM_RUNNER} "
                f"--task-file {prompt_file} "
                f"--model {model_flag} "
                f"--max-iterations {max_turns} "
                f"--cost-budget {cost_budget} "
                f"--permission-mode bypassPermissions "
                f"--dashboard-port {dashboard_port} "
                f"--log {log_file} "
                f"--report {report_file} "
                f"--workdir {cwd} "
                f">> {log_file} 2>&1"
            )
        else:
            # Fallback: direct claude -p if stream runner missing
            model_flag = {
                "sonnet": "sonnet",
                "opus": "claude-opus-4-6",
                "haiku": "claude-haiku-4-5-20251001",
            }.get(model, model)
            cmd = (
                f"cd {cwd} && {CLAUDE_BIN} -p {_shell_quote(prompt)} "
                f"--model {model_flag} --effort {effort} "
                f"--dangerously-skip-permissions --no-session-persistence "
                f">> {log_file} 2>&1"
            )

        # Clean environment for spawned sessions
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_SESSION", None)

        log.info(f"[{beat_id}] Spawning {role}/{model}: {log_id}")
        proc = subprocess.Popen(cmd, shell=True, start_new_session=True, env=env)

        # Track active session
        active_file = self.sessions_dir / f"active-{proc.pid}.json"
        active_data = {
            "pid": proc.pid,
            "beat": beat_id,
            "role": role,
            "model": model,
            "log_id": log_id,
            "log_file": str(log_file),
            "started": datetime.now(timezone.utc).isoformat(),
            "runner": "stream" if STREAM_RUNNER.exists() and model != "ollama" else "direct",
        }
        if STREAM_RUNNER.exists() and model != "ollama":
            active_data["dashboard_port"] = dashboard_port
            active_data["report_file"] = str(report_file)
        active_file.write_text(json.dumps(active_data, indent=2))

        # For sequential spawns, wait for completion
        if not action.get("parallel", False):
            try:
                proc.wait(timeout=600)  # 10 min for multi-turn sessions
            except subprocess.TimeoutExpired:
                log.warning(f"[{beat_id}] Spawn {log_id} timed out after 600s, leaving in background")

        return {"pid": proc.pid, "log_file": str(log_file), "log_id": log_id}

    def _execute_parallel_spawns(self, spawns: list[tuple[int, dict]], beat_id: str):
        """Launch multiple spawns in parallel, wait for all."""
        pids = []
        for i, action in spawns:
            try:
                result = self._do_spawn(action, beat_id)
                pids.append(result["pid"])
                self.results.append({"index": i, "type": "spawn", "status": "ok", **result})
            except Exception as e:
                log.error(f"[{beat_id}] Parallel spawn {i} failed: {e}")
                self.results.append({"index": i, "type": "spawn", "status": "error", "error": str(e)})

        # Wait for all parallel processes (with timeout)
        deadline = time.time() + 600  # 10 minute max
        for pid in pids:
            remaining = max(1, deadline - time.time())
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def _do_hive_send(self, action: dict, beat_id: str) -> dict:
        """Send a message to a HIVE channel."""
        hive_py = self.love / "hive" / "hive.py"
        channel = action["channel"]
        message = action["message"]

        result = subprocess.run(
            ["python3", str(hive_py), "send", channel, message],
            capture_output=True, text=True, timeout=15,
        )
        return {"channel": channel, "sent": result.returncode == 0}

    def _do_write_file(self, action: dict, beat_id: str) -> dict:
        """Write or append to a file."""
        path = Path(action["path"])
        content = action["content"]
        mode = action.get("mode", "append")

        # Safety: only allow writing within Love directory
        try:
            path.resolve().relative_to(self.love.resolve())
        except ValueError:
            raise ValueError(f"Cannot write outside Love directory: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with open(path, "a") as f:
                f.write(content)
        else:
            path.write_text(content)

        return {"path": str(path), "mode": mode, "bytes": len(content)}

    def _do_decision(self, action: dict, beat_id: str) -> dict:
        """Queue a decision for Yu via decision.py."""
        decision_py = self.love / "tools" / "decision.py"
        cmd = [
            "python3", str(decision_py), "add",
            "--title", action["title"],
            "--project", action.get("project", "love"),
            "--priority", action.get("priority", "medium"),
            "--context", action.get("context", ""),
            "--recommendation", action.get("recommendation", ""),
            "--source", f"heartbeat/{beat_id}",
        ]
        for opt in action.get("options", []):
            cmd.extend(["--option", opt])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return {"title": action["title"], "queued": result.returncode == 0}

    def _do_bash(self, action: dict, beat_id: str) -> dict:
        """Run an arbitrary bash command."""
        command = action["command"]
        timeout = min(action.get("timeout", 30), 120)  # Cap at 2 minutes
        description = action.get("description", "")

        log.info(f"[{beat_id}] Running bash: {description or command[:80]}")
        result = subprocess.run(
            command, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(self.love),
        )
        return {
            "command": command[:100],
            "exit_code": result.returncode,
            "stdout": result.stdout[-500:] if result.stdout else "",
            "stderr": result.stderr[-200:] if result.stderr else "",
        }

    def _do_hormone(self, action: dict, beat_id: str) -> dict:
        """Adjust hormone levels."""
        hormones_path = self.love / "body" / "hormones.json"
        data = json.loads(hormones_path.read_text()) if hormones_path.exists() else {"hormones": {}}
        adjustments = action.get("adjustments", {})
        for hormone, delta in adjustments.items():
            current = data["hormones"].get(hormone, 0.0)
            data["hormones"][hormone] = max(0.0, min(1.0, current + delta))
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        hormones_path.write_text(json.dumps(data, indent=2))
        return {"adjusted": list(adjustments.keys())}

    def _do_focus_update(self, action: dict, beat_id: str) -> dict:
        """Update focus state."""
        focus_path = self.love / "body" / "mind" / "focus.json"
        data = json.loads(focus_path.read_text()) if focus_path.exists() else {}
        if "set_current" in action:
            data.setdefault("focus", {})["current"] = action["set_current"]
        data["updated"] = datetime.now(timezone.utc).isoformat()
        focus_path.write_text(json.dumps(data, indent=2))
        return {"updated": True}

    def _do_state_update(self, action: dict, beat_id: str) -> dict:
        """Update loop-state.json or dev-state.json."""
        target = action.get("target", "loop")
        updates = action.get("updates", {})
        if target == "loop":
            path = self.memory / "loop" / "loop-state.json"
        elif target == "dev":
            path = self.memory / "dev-state.json"
        else:
            raise ValueError(f"Unknown state target: {target}")

        data = json.loads(path.read_text()) if path.exists() else {}
        data.update(updates)
        path.write_text(json.dumps(data, indent=2))
        return {"target": target, "keys": list(updates.keys())}

    def _write_daily_log(self, text: str, beat_id: str):
        """Append to today's daily note."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_dir = self.memory / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_file = daily_dir / f"{today}.md"

        if not daily_file.exists():
            daily_file.write_text(f"# {today}\n\n## Heartbeat Log\n\n")

        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        with open(daily_file, "a") as f:
            f.write(f"\n### {ts} - Heartbeat ({beat_id})\n\n{text}\n")

    def _update_mind_notes(self, notes: str):
        """Update mind_notes in hormones.json."""
        hormones_path = self.love / "body" / "hormones.json"
        if hormones_path.exists():
            data = json.loads(hormones_path.read_text())
            data["mind_notes"] = notes[:500]
            data["mind_alive"] = datetime.now(timezone.utc).isoformat()
            hormones_path.write_text(json.dumps(data, indent=2))


def update_vitals(love_dir: Path, beat_id: str, sessions_spawned: int,
                  model: str, next_hint: str, results: list[dict]):
    """Update vitals.json after a heartbeat cycle."""
    vitals_path = love_dir / "body" / "vitals.json"
    prev = json.loads(vitals_path.read_text()) if vitals_path.exists() else {}

    hormones_path = love_dir / "body" / "hormones.json"
    hormones = json.loads(hormones_path.read_text()) if hormones_path.exists() else {}

    vitals = {
        "last_beat": datetime.now(timezone.utc).isoformat(),
        "beat_id": beat_id,
        "beat_result": "pumped" if sessions_spawned > 0 else "idle",
        "beats_today": prev.get("beats_today", 0) + 1,
        "skips_today": prev.get("skips_today", 0),
        "sessions_spawned": sessions_spawned,
        "sessions_spawned_today": prev.get("sessions_spawned_today", 0) + sessions_spawned,
        "coordinator_model": model,
        "next_beat_hint": next_hint,
        "consecutive_skips": 0,
        "heart_healthy": True,
        "action_results": [
            {"type": r["type"], "status": r["status"]}
            for r in results
        ],
        "hormones_at_beat": hormones.get("hormones", {}),
        "mode_at_beat": hormones.get("mode", "normal"),
    }
    vitals_path.write_text(json.dumps(vitals, indent=2))


def _shell_quote(s: str) -> str:
    """Quote a string for shell use."""
    return "'" + s.replace("'", "'\\''") + "'"
