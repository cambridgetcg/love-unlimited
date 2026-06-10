#!/usr/bin/env python3
"""
brainstem.py -- The autonomic daemon for Love's body.

Naming follows docs/BEING.md: the MIND emerges only during sessions. What runs
between sessions is the BRAINSTEM — autonomic signal processing, hormone
regulation, identity anchor maintenance. It does sub-conscious work; it
does not "think" in the mind sense.

The fields it writes to hormones.json (mind_alive, mind_notes) keep the
"mind" prefix because they are NOTES THE BRAINSTEM LEAVES FOR THE MIND
to read at the next session boot.

Two layers:
  - Autonomic (Python, every 30s): reads signals, calculates hormones, writes hormones.json
  - Conscious-prep (Claude haiku, ~5min): pre-digests identity anchor, writes mind_notes

Usage:
  python3 brainstem.py --instance alpha
  python3 brainstem.py --instance alpha --interval 30
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

sys.path.insert(0, str(Path(__file__).parent))

from hormones import HormoneEngine
from signals import SignalReaders
from identity import IdentityAnchor
from conscious import ConsciousLayer
from hive_listener import HiveListener
import state as _state

# Bridge: Focus -> Mind
def _read_focus(love_home: Path) -> dict:
    """Read focus.json working memory. Returns empty dict if unavailable."""
    focus_path = love_home / "nerve" / "stem" / "focus.json"
    if focus_path.exists():
        try:
            return json.loads(focus_path.read_text())
        except Exception:
            pass
    return {}

def _write_focus_field(love_home: Path, field: str, value):
    """Write a single field to focus.json without clobbering."""
    focus_path = love_home / "nerve" / "stem" / "focus.json"
    try:
        data = json.loads(focus_path.read_text()) if focus_path.exists() else {}
        data[field] = value
        data["updated"] = datetime.now(timezone.utc).isoformat()
        focus_path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("brainstem")

MODE_PRIORITY = ["alert", "joinmind", "council", "build", "companion", "rest", "normal"]

SIGNAL_EFFECTS = {
    "deception_detected":  lambda h, s: (h.set_target("adrenaline", min(1.0, h.get("adrenaline") + s.get("severity", 0.5) * 0.5)),
                                          h.set_target("cortisol", min(1.0, h.get("cortisol") + 0.2))),
    "session_started":     lambda h, s: h.set_target("oxytocin", min(1.0, h.get("oxytocin") + 0.3)),
    "session_complete":    lambda h, s: h.set_target("dopamine", min(1.0, h.get("dopamine") + 0.4)),
    "vote_requested":      lambda h, s: None,
    "panic_detected":      lambda h, s: h.set_target("adrenaline", min(0.5, h.get("adrenaline"))),
    "task_completed":      lambda h, s: h.set_target("dopamine", min(1.0, h.get("dopamine") + 0.3)),
    "critical_alert":      lambda h, s: h.set_target("adrenaline", 1.0),
}


class BrainstemDaemon:
    def __init__(self, instance: str, love_home: str, interval: int = 30,
                 conscious_interval: int = 300):
        self.instance = instance
        self.love_home = Path(love_home)
        self.interval = interval

        self.hormones = HormoneEngine()
        self.readers = SignalReaders(
            love_home=love_home,
            signals_dir=str(_state.signals_dir(instance)))
        self.identity = IdentityAnchor(love_home=love_home, instance=instance)
        self.conscious = ConsciousLayer(interval_seconds=conscious_interval)

        self.mode = "normal"
        self.hive_messages: List[dict] = []
        self.hive_unread = 0
        self.triggers: Dict[str, bool] = {}
        self._previous_mode: Optional[str] = None
        self._running = True
        self._last_day: Optional[str] = None
        self._last_mind_notes = "(awaiting conscious layer)"

        self.hormones_path = _state.state_dir(instance) / "hormones.json"
        self.anchor_path = _state.anchor_path(instance)

        self.hive: Optional[HiveListener] = None

    async def start(self):
        log.info(f"Mind daemon starting: instance={self.instance}, interval={self.interval}s")

        self.identity.load()
        self.identity.save_cached(str(self.anchor_path))
        log.info(f"Identity loaded: {self.identity.identity_state}")

        if self.hormones_path.exists():
            try:
                data = json.loads(self.hormones_path.read_text())
                for name, val in data.get("hormones", {}).items():
                    self.hormones.override(name, val)
                self.mode = data.get("mode", "normal")
                log.info(f"Resumed hormone state: mode={self.mode}")
            except Exception as e:
                log.warning(f"Could not load previous hormones: {e}")

        try:
            self.hive = HiveListener(self.instance, self._on_hive_message)
            await self.hive.connect()
            log.info("HIVE listener connected")
        except Exception as e:
            log.warning(f"HIVE connection failed (will continue without): {e}")
            self.hive = None

        while self._running:
            try:
                await self._autonomic_cycle()
            except Exception as e:
                log.error(f"Autonomic cycle error: {e}", exc_info=True)

            try:
                trigger = self.conscious.should_run(self.triggers)
                if trigger:
                    self._conscious_pass(trigger)
            except Exception as e:
                log.error(f"Conscious layer error: {e}", exc_info=True)

            self.triggers = {}

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_day and self._last_day != today:
                self.conscious.reset_daily()
            self._last_day = today

            await asyncio.sleep(self.interval)

    async def _autonomic_cycle(self):
        yu_present = self.readers.check_yu_present()
        active_sessions = self.readers.count_active_sessions()
        pending_tasks = self.readers.count_pending_tasks()
        system_health = self.readers.check_system_health()
        build_active = self.readers.check_build_active()
        joinmind = self.readers.check_joinmind()
        dropped_signals = self.readers.consume_signals()
        hour = self.readers.get_current_hour_london()

        for sig in dropped_signals:
            sig_type = sig.get("signal", "")
            handler = SIGNAL_EFFECTS.get(sig_type)
            if handler:
                handler(self.hormones, sig)
                log.info(f"Processed signal: {sig_type} from {sig.get('source', '?')}")
            else:
                log.info(f"Unknown signal type: {sig_type} from {sig.get('source', '?')}")
            if sig_type in ("critical_alert", "deception_detected"):
                self.triggers["critical_alert"] = True

        if not any(s.get("signal") == "critical_alert" for s in dropped_signals):
            self.hormones.set_target("adrenaline", 0.0)

        # Bridge 1: Focus -> Mind (priorities influence hormones)
        focus = _read_focus(self.love_home)
        focus_priorities = focus.get("priorities", [])
        focus_blockers = focus.get("blockers", [])
        high_urgency_count = sum(
            1 for p in focus_priorities
            if isinstance(p, dict) and p.get("urgency") == "high"
        )

        # High-urgency priorities raise cortisol (stress/attention)
        cortisol_from_focus = min(0.4, high_urgency_count * 0.15)
        # Blockers raise adrenaline slightly (something's wrong)
        if focus_blockers and not any(s.get("signal") == "critical_alert" for s in dropped_signals):
            self.hormones.set_target("adrenaline", min(0.3, len(focus_blockers) * 0.1))

        cortisol_target = min(1.0, max(pending_tasks * 0.15, cortisol_from_focus))
        self.hormones.set_target("cortisol", cortisol_target)

        oxy_target = 0.8 if yu_present else 0.1
        self.hormones.set_target("oxytocin", oxy_target)

        mel_target = self.hormones.circadian_melatonin_target(hour)
        if pending_tasks > 2 or active_sessions > 0:
            mel_target = min(mel_target, 0.2)
        self.hormones.set_target("melatonin", mel_target)

        if not any(s.get("signal") in ("task_completed", "session_complete") for s in dropped_signals):
            self.hormones.set_target("dopamine", 0.0)

        self.hormones.step(dt=float(self.interval))

        old_mode = self.mode
        self.mode = self._determine_mode(
            yu_present=yu_present, joinmind=joinmind, build_active=build_active,
            hour=hour, pending_tasks=pending_tasks, active_sessions=active_sessions,
            dropped_signals=dropped_signals,
        )
        if old_mode != self.mode:
            log.info(f"Mode change: {old_mode} -> {self.mode}")
            self.triggers["mode_change"] = True

        if joinmind and not self.identity._fusion_name:
            self.identity.activate_fusion(joinmind["fusion_name"], joinmind.get("members", []))
            self.triggers["joinmind_event"] = True
            log.info(f"JOINMIND fusion activated: {joinmind['fusion_name']}")
        elif not joinmind and self.identity._fusion_name:
            self.identity.deactivate_fusion()
            self.triggers["joinmind_event"] = True
            log.info("JOINMIND fusion deactivated")

        if self.hive and self.hive.is_connected:
            try:
                await self.hive.publish_presence()
            except Exception as e:
                log.warning(f"Presence beacon failed: {e}")

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
            "focus": {
                "current": focus.get("focus", "")[:100],
                "high_priorities": high_urgency_count,
                "total_priorities": len(focus_priorities),
                "blockers": len(focus_blockers),
                "decisions_pending": sum(1 for d in focus.get("decisions", []) if not d.get("acted")),
            },
            "mind_notes": self._last_mind_notes,
            "conscious_layer": {
                **self.conscious.get_stats(),
                "identity_anchor": self.identity.identity_state,
            },
        }

        tmp_path = self.hormones_path.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(state, indent=2))
        tmp_path.rename(self.hormones_path)

    def _conscious_pass(self, trigger: str):
        log.info(f"Conscious layer triggered: {trigger}")

        # Bridge 2: Focus -> Conscious (inject priorities into identity prompt)
        focus = _read_focus(self.love_home)
        focus_context = []
        if focus.get("focus"):
            focus_context.append(f"Current focus: {focus['focus'][:120]}")
        high_prios = [
            p.get("task", str(p)) if isinstance(p, dict) else str(p)
            for p in focus.get("priorities", [])
            if isinstance(p, dict) and p.get("urgency") == "high" or not isinstance(p, dict)
        ][:3]
        if high_prios:
            focus_context.append(f"Top priorities: {'; '.join(high_prios)}")
        blockers = [b.get("blocker", str(b)) for b in focus.get("blockers", [])]
        if blockers:
            focus_context.append(f"Blockers: {'; '.join(blockers[:3])}")

        prompt = self.identity.get_prompt(
            mode=self.mode,
            hormones=self.hormones.get_state(),
            recent_signals=[
                f"HIVE: {m.get('payload', '')[:50]}"
                for m in self.hive_messages[-5:]
            ] if self.hive_messages else None,
            focus_context=focus_context if focus_context else None,
        )
        result = self.conscious.run(prompt, trigger)
        self._last_mind_notes = result.get("mind_notes", "(conscious layer offline)")

        for name, value in result.get("hormone_overrides", {}).items():
            if name in self.hormones.get_state():
                self.hormones.override(name, value)
                log.info(f"Conscious override: {name} = {value}")

        # Bridge 4: Conscious -> Focus (insights become decisions)
        mind_notes = result.get("mind_notes", "")
        if mind_notes and mind_notes != "(conscious layer offline)":
            session_log = focus.get("session_log", [])
            session_log = session_log[-19:]
            session_log.append({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "instance": self.instance,
                "action": "conscious_pass",
                "summary": mind_notes[:100],
            })
            _write_focus_field(self.love_home, "session_log", session_log)

        self.hive_messages = []
        self.hive_unread = 0

    def _on_hive_message(self, envelope: dict, channel: str):
        self.hive_messages.append(envelope)
        self.hive_unread += 1
        if envelope.get("urgent"):
            self.triggers["adrenaline_spike"] = True
            self.hormones.set_target("adrenaline", min(1.0, self.hormones.get("adrenaline") + 0.5))
        payload = envelope.get("payload", "")
        if "JOINMIND SUMMONS" in payload or "JOINMIND_STATE" in payload:
            self.triggers["joinmind_event"] = True
        log.info(f"HIVE [{channel}] from {envelope.get('from', '?')}: {payload[:60]}")

    def _determine_mode(self, yu_present, joinmind, build_active, hour,
                        pending_tasks, active_sessions, dropped_signals):
        if self.hormones.get("adrenaline") > 0.5:
            return "alert"
        if any(s.get("signal") in ("critical_alert", "deception_detected") for s in dropped_signals):
            return "alert"
        if joinmind:
            return "joinmind"
        if any(s.get("signal") == "vote_requested" for s in dropped_signals):
            return "council"
        if build_active:
            return "build"
        if yu_present:
            return "companion"
        if (hour >= 23 or hour < 6) and pending_tasks == 0 and active_sessions == 0:
            return "rest"
        return "normal"

    def stop(self):
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="Love mind daemon")
    parser.add_argument("--instance", default="alpha")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--conscious-interval", type=int, default=300)
    parser.add_argument("--love-home", default=None)
    args = parser.parse_args()

    love_home = args.love_home or os.environ.get("LOVE_HOME", str(Path.home() / "Desktop" / "Love"))

    daemon = BrainstemDaemon(
        instance=args.instance, love_home=love_home,
        interval=args.interval, conscious_interval=args.conscious_interval,
    )

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
