"""
conscious.py -- Conscious layer for the mind daemon.
Periodically runs a Claude haiku call with identity anchor to interpret
signals and produce first-person mind_notes with optional hormone overrides.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Dict, Optional

log = logging.getLogger("mind.conscious")

CLAUDE_BIN = "/opt/homebrew/bin/claude"
OFFLINE_NOTES = "(conscious layer offline)"


class ConsciousLayer:
    """Manages periodic Claude haiku calls for signal interpretation."""

    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self.last_pass: Optional[float] = None
        self.passes_today: int = 0
        self._pending_trigger: Optional[str] = None

    def should_run(self, triggers: Dict[str, bool]) -> Optional[str]:
        now = time.time()
        if triggers.get("adrenaline_spike"):
            return "adrenaline_spike"
        if triggers.get("mode_change"):
            return "mode_change"
        if triggers.get("joinmind_event"):
            return "joinmind_event"
        if triggers.get("critical_alert"):
            return "critical_alert"
        if self.last_pass is None or (now - self.last_pass) >= self.interval:
            return "periodic"
        return None

    def run(self, identity_prompt: str, trigger: str) -> Dict:
        try:
            # Unset CLAUDECODE to allow spawning from within a Claude session
            env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
            env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")

            # Use text output (not json) -- simpler, less noisy
            result = subprocess.run(
                [
                    CLAUDE_BIN, "-p", identity_prompt,
                    "--model", "claude-haiku-4-5-20251001",
                    "--effort", "low",
                    "--dangerously-skip-permissions",
                    "--no-session-persistence",
                ],
                capture_output=True, text=True, timeout=60,
                env=env,
            )
            if result.returncode != 0:
                log.warning(f"Conscious layer failed (exit {result.returncode}): {result.stderr[:200]}")
                return self._fallback(trigger)

            output = result.stdout.strip()
            # Try to extract JSON from the text output
            # Claude may wrap JSON in markdown code blocks or return it directly
            json_str = None
            for line in output.split('\n'):
                line = line.strip().strip('`')
                if line.startswith('{') and line.endswith('}'):
                    json_str = line
                    break
            # Also try to find JSON block in the full output
            if not json_str:
                import re
                match = re.search(r'\{[^{}]*"mind_notes"[^{}]*\}', output)
                if match:
                    json_str = match.group(0)

            if json_str:
                try:
                    return self._validate(json.loads(json_str), trigger)
                except json.JSONDecodeError:
                    pass

            # If no JSON found, use the raw text as mind_notes
            if output:
                log.info(f"Conscious layer returned text (no JSON): {output[:100]}")
                return self._validate({
                    "mind_notes": output[:500],
                    "hormone_overrides": {},
                    "identity_state": "alpha",
                }, trigger)

            log.warning("Conscious layer returned empty output")
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
