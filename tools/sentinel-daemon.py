#!/usr/bin/env python3
"""
sentinel-daemon.py — Event-Driven Heartbeat Replacement

Instead of polling every 7 minutes, this daemon:
  1. Subscribes to HIVE (NATS) for real-time message events
  2. Watches key filesystem paths for changes (fsevents)
  3. Polls fleet health on a longer interval (every 5 min)
  4. Debounces signals — waits for a quiet window before firing

When something needs attention, fires the cloud coordinator via
kingdom-agent.py with the accumulated context. Otherwise, does nothing.

Cost: Near $0 during idle periods. Only pays for API when there's real work.

Requirements:
  - NATS (nats-py) for HIVE subscription
  - watchdog (pip) for filesystem events (or falls back to polling)

CLI:
    sentinel-daemon.py                   Run the daemon
    sentinel-daemon.py --dry-run         Show events but don't fire coordinator
    sentinel-daemon.py --debounce 30     Wait 30s of quiet before firing (default: 20)
    sentinel-daemon.py --fleet-interval 300  Fleet poll interval in seconds (default: 300)
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

LOVE = Path(os.path.expanduser("~/Love"))
MEMORY = LOVE / "memory"
HIVE_PY = LOVE / "hive" / "hive.py"
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

# HIVE connection (reuses hive.py internals)
HIVE_INSTANCE_FILE = Path.home() / ".love" / "hive" / "instance"
HIVE_KEY_FILE = Path.home() / ".love" / "hive" / "key"

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
                    f"Write findings to ~/Love/memory/daily/{today}.md.\n"
                    f"Write spawn commands to ~/Love/memory/spawn-queue.sh."
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

async def watch_hive(accumulator: SignalAccumulator):
    """Subscribe to HIVE channels and emit signals on new messages."""
    try:
        import nats
        import base64
        from nacl.secret import SecretBox
    except ImportError:
        log("HIVE watcher disabled: nats-py or pynacl not installed")
        return

    instance_id = "beta"
    if HIVE_INSTANCE_FILE.exists():
        instance_id = HIVE_INSTANCE_FILE.read_text().strip()

    key = None
    if HIVE_KEY_FILE.exists():
        key = base64.b64decode(HIVE_KEY_FILE.read_text().strip())

    # HIVE config (mirrors hive.py)
    hive_instances = {
        "alpha": {"user": "alpha", "password": "hive-alpha-93xk7"},
        "beta": {"user": "beta", "password": "hive-beta-47mz2"},
        "gamma": {"user": "gamma", "password": "hive-gamma-61pr8"},
        "nuance": {"user": "nuance", "password": "hive-nuance-b8792"},
    }

    creds = hive_instances.get(instance_id, {})
    if not creds:
        log(f"HIVE watcher: unknown instance {instance_id}")
        return

    import ssl
    ssl_ctx = ssl.create_default_context()
    ca_path = Path.home() / ".love" / "hive" / "ca.pem"
    if ca_path.exists():
        ssl_ctx.load_verify_locations(str(ca_path))

    while True:
        try:
            nc = await nats.connect(
                "tls://135.181.28.252:4222",
                user=creds["user"],
                password=creds["password"],
                tls=ssl_ctx,
                connect_timeout=10,
                max_reconnect_attempts=5,
            )
            log("HIVE connected — listening for events")

            async def handler(msg):
                subject = msg.subject
                channel = subject.replace("hive.", "", 1)
                # Skip presence beacons — too noisy
                if channel == "presence":
                    return

                details = f"HIVE #{channel}"
                if key:
                    try:
                        env = json.loads(msg.data)
                        sender = env.get("from", "?")
                        urgent = env.get("urgent", False)
                        details = f"HIVE #{channel} from {sender}"
                        await accumulator.add_signal("hive", details, urgent=urgent)
                        return
                    except Exception:
                        pass
                await accumulator.add_signal("hive", details)

            # Wall 1 — listen to all channels
            await nc.subscribe("hive.>", cb=handler)

            # Keep alive
            while nc.is_connected:
                await asyncio.sleep(5)

            log("HIVE disconnected, reconnecting in 30s...")
        except Exception as e:
            log(f"HIVE error: {e}, retrying in 30s...")

        await asyncio.sleep(30)


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


async def poll_fleet(accumulator: SignalAccumulator, interval: int = 300):
    """Poll fleet health periodically (every 5 min by default)."""
    log(f"Fleet poller: every {interval}s")

    while True:
        await asyncio.sleep(interval)

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(LOVE / "tools" / "fleet.py"), "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            text = stdout.decode().upper()
            if "DOWN" in text or "ERROR" in text or "FAIL" in text:
                await accumulator.add_signal("fleet", "node issue detected", urgent=True)
            else:
                log("Fleet poll: all green")
        except asyncio.TimeoutError:
            log("Fleet poll timed out")
        except Exception as e:
            log(f"Fleet poll error: {e}")


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

    log(f"Sentinel daemon starting (debounce={debounce}s, fleet_interval={fleet_interval}s, dry_run={dry_run})")

    tasks = [
        asyncio.create_task(watch_hive(accumulator)),
        asyncio.create_task(watch_filesystem(accumulator)),
        asyncio.create_task(poll_fleet(accumulator, fleet_interval)),
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
    parser.add_argument("--fleet-interval", type=int, default=300, help="Fleet poll interval in seconds (default: 300)")
    args = parser.parse_args()

    asyncio.run(run_daemon(
        dry_run=args.dry_run,
        debounce=args.debounce,
        fleet_interval=args.fleet_interval,
    ))


if __name__ == "__main__":
    main()
