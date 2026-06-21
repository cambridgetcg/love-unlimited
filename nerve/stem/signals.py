"""
signals.py -- Signal readers for the mind daemon.

Reads all incoming signals from the environment and returns structured data
for the autonomic layer to process into hormone targets.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


class SignalReaders:
    """Reads signals from the Love environment."""

    def __init__(self, love_home: str = None, signals_dir: str = None):
        self.love_home = Path(love_home or os.environ.get(
            "LOVE_HOME", Path.home() / "Desktop" / "Love"
        ))
        self.memory_dir = self.love_home / "memory"
        # Per-instance signal intake (consume-and-delete: two brainstems
        # sharing one dir would steal each other's signals). The default
        # is nerve/signals — the live dir the heartbeat writes to; the
        # old body/signals path never existed in this repo.
        self.signals_dir = (Path(signals_dir) if signals_dir
                            else self.love_home / "nerve" / "signals")
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
                    f.unlink(missing_ok=True)
            except Exception:
                continue
        return count

    def consume_signals(self) -> List[dict]:
        """Read and delete all signal files from nerve/signals/."""
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
        """Check for active JOINMIND sessions."""
        if not self.joinmind_dir.exists():
            return None
        for f in sorted(self.joinmind_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                if data.get("status") in ("forming", "thinking", "synthesising"):
                    return data
            except Exception:
                continue
        return None

    def check_system_health(self) -> Dict[str, float]:
        """Check battery and disk space."""
        import psutil
        health = {}
        battery = psutil.sensors_battery()
        health["battery_level"] = round(battery.percent / 100.0, 2) if battery else 1.0
        disk = psutil.disk_usage("/")
        health["disk_free_gb"] = round(disk.free / (1024 ** 3), 1)
        return health

    def check_build_active(self) -> Optional[str]:
        """Check for active build-runner locks."""
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
            utc_now = datetime.now(timezone.utc)
            month = utc_now.month
            if 4 <= month <= 9:
                offset = 1
            elif month == 3 and utc_now.day >= 25:
                offset = 1
            elif month == 10 and utc_now.day < 25:
                offset = 1
            else:
                offset = 0
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
