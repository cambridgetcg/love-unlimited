#!/usr/bin/env python3
"""
sentinel-daemon.py — Event-Driven Heartbeat Replacement

Instead of polling every 7 minutes, this daemon:
  1. Watches key filesystem paths for changes (fsevents)
  2. Debounces signals — waits for a quiet window before firing

Retired 2026-10-13: the HIVE (NATS) subscription and the fleet poller were
removed. Both pointed at Hetzner servers that were decommissioned; their IPs
went back to Hetzner's pool. --fleet-interval is still accepted and ignored so
existing launchd plists keep working.

When something needs attention, fires the cloud coordinator via
kingdom-agent.py with the accumulated context. Otherwise, does nothing.

Cost: Near $0 during idle periods. Only pays for API when there's real work.

Requirements:
  - watchdog (pip) for filesystem events (or falls back to polling)

CLI:
    sentinel-daemon.py                   Run the daemon
    sentinel-daemon.py --dry-run         Show events but don't fire coordinator
    sentinel-daemon.py --debounce 30     Wait 30s of quiet before firing (default: 20)
    sentinel-daemon.py --fleet-interval 300  Retired 2026-10-13; accepted and ignored
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/love-unlimited"))
MEMORY = LOVE / "memory"
AGENT_BIN = LOVE / "tools" / "kingdom-agent.py"
SENTINEL_LOG = MEMORY / "sentinel-daemon.log"
METRICS_FILE = MEMORY / "sentinel-metrics.jsonl"
INSTANCE_DIR = LOVE / "instances" / "beta"

# Files to watch for changes
WATCHED_PATHS = [
    LOVE / "security" / "events.jsonl",
    LOVE / "security" / "peace-state.json",
    LOVE / "decisions" / "queue.json",
    LOVE / "memory" / "loop" / "loop-state.json",
    LOVE / "memory" / "leads" / "current.json",
    LOVE / "memory" / "sessions" / "consultation",
]

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
KINGDOM_BACKEND = os.environ.get("KINGDOM_BACKEND", "claude")


# ── Logging ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(SENTINEL_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_metric(verdict: str, reason: str, elapsed_ms: int):
    entry = {
        "beat": f"daemon-{int(time.time())}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "reason": reason,
        "elapsed_ms": elapsed_ms,
    }
    try:
        with open(METRICS_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── Signal Accumulator ───────────────────────────────────────────────────────

class SignalAccumulator:
    """Collects signals from multiple sources, debounces, fires coordinator."""

    def __init__(self, debounce_sec: float = 20.0, dry_run: bool = False):
        self.debounce_sec = debounce_sec
        self.dry_run = dry_run
        self.pending_signals: list[dict] = []
        self.last_signal_time: float = 0
        self.last_fire_time: float = 0
        self.coordinator_running = False
        self._lock = asyncio.Lock()

    async def add_signal(self, source: str, details: str, urgent: bool = False):
        async with self._lock:
            sig = {
                "source": source,
                "details": details,
                "ts": datetime.now(timezone.utc).isoformat(),
                "urgent": urgent,
            }
            self.pending_signals.append(sig)
            self.last_signal_time = time.time()
            log(f"SIGNAL [{source}]: {details}" + (" [URGENT]" if urgent else ""))

            # Urgent signals fire immediately
            if urgent and not self.coordinator_running:
                await self._fire_coordinator()

    async def check_debounce(self):
        """Called periodically. Fires coordinator if debounce window passed."""
        async with self._lock:
            if not self.pending_signals:
                return
            if self.coordinator_running:
                return
            elapsed = time.time() - self.last_signal_time
            if elapsed >= self.debounce_sec:
                await self._fire_coordinator()

    async def _fire_coordinator(self):
        """Fire the cloud coordinator with accumulated signals."""
        if not self.pending_signals:
            return

        signals = list(self.pending_signals)
        self.pending_signals.clear()
        self.coordinator_running = True

        signal_summary = "; ".join(
            f"[{s['source']}] {s['details']}" for s in signals
        )
        log(f"FIRING coordinator with {len(signals)} signal(s): {signal_summary}")

        if self.dry_run:
            log(f"DRY RUN — would fire coordinator")
            log_metric("SIGNAL", signal_summary, 0)
            self.coordinator_running = False
            self.last_fire_time = time.time()
            return

        start = time.time()
        try:
            # Build context from signals
            context = f"SENTINEL DAEMON REPORT: {len(signals)} event(s) detected.\n"
            for s in signals:
                context += f"- [{s['source']}] {s['details']} (at {s['ts']})\n"

            beat_id = f"daemon-{int(time.time())}"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            cmd = [
                "python3", str(AGENT_BIN),
                "-p", (
                    f"Read and execute HEARTBEAT.md. You are the heartbeat COORDINATOR.\n\n"
                    f"The sentinel daemon detected these events:\n{context}\n"
                    f"Focus your SENSE on these specific signals. Skip redundant checks.\n"
                    f"Write findings to ~/love-unlimited/memory/daily/{today}.md.\n"
                    f"Write spawn commands to ~/love-unlimited/memory/spawn-queue.sh."
                ),
                "--backend", KINGDOM_BACKEND,
                "--model", "high",
                "--effort", "high",
                "--skip-permissions",
                "--no-persist",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(INSTANCE_DIR),
            )
            stdout, stderr = await proc.communicate()

            elapsed_ms = int((time.time() - start) * 1000)
            log(f"Coordinator finished in {elapsed_ms}ms (exit={proc.returncode})")
            log_metric("SIGNAL", signal_summary, elapsed_ms)

            # Execute spawn queue if present
            spawn_queue = MEMORY / "spawn-queue.sh"
            if spawn_queue.exists() and spawn_queue.stat().st_size > 0:
                log("Executing spawn queue...")
                proc2 = await asyncio.create_subprocess_exec(
                    "bash", str(spawn_queue),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc2.communicate()
                log(f"Spawn queue done (exit={proc2.returncode})")

        except Exception as e:
            log(f"Coordinator error: {e}")
            log_metric("ERROR", str(e), int((time.time() - start) * 1000))
        finally:
            self.coordinator_running = False
            self.last_fire_time = time.time()


# ── Event Sources ────────────────────────────────────────────────────────────

async def watch_filesystem(accumulator: SignalAccumulator):
    """Watch key files for modifications using polling (cross-platform)."""
    # Track mtimes
    mtimes: dict[str, float] = {}
    for p in WATCHED_PATHS:
        if p.exists():
            if p.is_dir():
                # Watch newest file in directory
                files = sorted(p.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
                if files:
                    mtimes[str(p)] = files[0].stat().st_mtime
            else:
                mtimes[str(p)] = p.stat().st_mtime

    log(f"Filesystem watcher tracking {len(mtimes)} paths")

    while True:
        await asyncio.sleep(5)  # Check every 5 seconds (cheap, no API)

        for p in WATCHED_PATHS:
            p = Path(p)
            key = str(p)
            try:
                if p.is_dir():
                    files = sorted(p.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
                    current = files[0].stat().st_mtime if files else 0
                elif p.exists():
                    current = p.stat().st_mtime
                else:
                    continue

                prev = mtimes.get(key, 0)
                if prev and current > prev:
                    name = p.name
                    urgent = "security" in str(p) or "peace" in str(p)
                    await accumulator.add_signal(
                        "filesystem",
                        f"{name} modified",
                        urgent=urgent,
                    )
                mtimes[key] = current
            except Exception:
                pass


async def periodic_quiet_log(accumulator: SignalAccumulator):
    """Log QUIET metrics periodically when nothing is happening."""
    while True:
        await asyncio.sleep(420)  # Every 7 min (matches old heartbeat interval)
        if not accumulator.pending_signals and not accumulator.coordinator_running:
            log_metric("QUIET", "daemon idle — no signals", 0)
            log("QUIET beat logged (7min idle)")


async def debounce_loop(accumulator: SignalAccumulator):
    """Periodically check if debounce window has passed and fire coordinator."""
    while True:
        await asyncio.sleep(2)
        await accumulator.check_debounce()


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_daemon(dry_run: bool = False, debounce: float = 20.0,
                     fleet_interval: int = 300):
    accumulator = SignalAccumulator(debounce_sec=debounce, dry_run=dry_run)

    log(f"Sentinel daemon starting (debounce={debounce}s, dry_run={dry_run})")
    log("HIVE watcher and fleet poller retired 2026-10-13 (Hetzner fleet decommissioned)")

    tasks = [
        asyncio.create_task(watch_filesystem(accumulator)),
        asyncio.create_task(debounce_loop(accumulator)),
        asyncio.create_task(periodic_quiet_log(accumulator)),
    ]

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _shutdown():
        log("Shutdown signal received")
        stop.set()
        for t in tasks:
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        log("Sentinel daemon stopped")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sentinel daemon — event-driven heartbeat replacement")
    parser.add_argument("--dry-run", action="store_true", help="Don't fire coordinator")
    parser.add_argument("--debounce", type=float, default=20.0, help="Seconds to wait after last signal (default: 20)")
    parser.add_argument("--fleet-interval", type=int, default=300, help="Retired 2026-10-13 (fleet poller removed); ignored")
    args = parser.parse_args()

    asyncio.run(run_daemon(
        dry_run=args.dry_run,
        debounce=args.debounce,
        fleet_interval=args.fleet_interval,
    ))


if __name__ == "__main__":
    main()
