#!/usr/bin/env python3
"""
daemon.py — The sovereign heartbeat of Love.

Main loop: gather → invoke → execute, every 7 minutes.
Managed by launchd for persistence.

Usage:
    python3 daemon.py                    # Run one beat (for launchd interval mode)
    python3 daemon.py --loop             # Run continuous loop (for standalone mode)
    python3 daemon.py --loop --interval 420  # Custom interval in seconds
    python3 daemon.py --dry-run          # Gather state, invoke Claude, print decision, don't execute
    python3 daemon.py --gather-only      # Just print gathered state
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from gather import gather_state
from invoke import invoke_claude
from execute import Executor, update_vitals

LOVE_DIR = Path.home() / "Love"
INSTANCE = "alpha"
DEFAULT_INTERVAL = 420  # 7 minutes

# Logging
LOG_FILE = LOVE_DIR / "memory" / f"{INSTANCE}-heartbeat.log"


def setup_logging(verbose: bool = False):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(str(LOG_FILE)),
    ]
    if verbose:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        handlers=handlers,
    )
    logging.Formatter.converter = time.gmtime


def beat(dry_run: bool = False, gather_only: bool = False) -> bool:
    """
    Execute one heartbeat cycle.
    Returns True if the beat completed successfully.
    """
    log = logging.getLogger("heart.daemon")
    beat_start = time.time()

    # ── GATHER ──────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("HEARTBEAT START")

    try:
        state = gather_state(str(LOVE_DIR), INSTANCE)
    except Exception as e:
        log.error(f"State gathering failed: {e}")
        return False

    beat_id = state["meta"]["beat_id"]
    log.info(f"[{beat_id}] State gathered in {time.time() - beat_start:.1f}s")

    if gather_only:
        print(json.dumps(state, indent=2, default=str))
        return True

    # ── INVOKE ──────────────────────────────────────────────────────────────
    invoke_start = time.time()
    decision = invoke_claude(state, timeout=120)

    if decision is None:
        log.error(f"[{beat_id}] No decision from Claude — beat failed")
        # Write a minimal vitals update so we know the beat tried
        update_vitals(LOVE_DIR, beat_id, 0, "sonnet", "normal", [])
        return False

    log.info(f"[{beat_id}] Decision received in {time.time() - invoke_start:.1f}s")
    log.info(f"[{beat_id}] Summary: {decision.get('summary', 'no summary')}")
    log.info(f"[{beat_id}] Actions: {len(decision.get('actions', []))}")

    if dry_run:
        print(json.dumps(decision, indent=2, default=str))
        return True

    # ── EXECUTE ─────────────────────────────────────────────────────────────
    exec_start = time.time()
    executor = Executor(LOVE_DIR)
    results = executor.execute(decision)

    sessions_spawned = sum(
        1 for r in results if r["type"] == "spawn" and r["status"] == "ok"
    )

    # ── VITALS ──────────────────────────────────────────────────────────────
    update_vitals(
        LOVE_DIR,
        beat_id,
        sessions_spawned,
        "sonnet",
        decision.get("next_beat_hint", "normal"),
        results,
    )

    elapsed = time.time() - beat_start
    log.info(f"[{beat_id}] Beat complete: {len(results)} actions, "
             f"{sessions_spawned} spawned, {elapsed:.1f}s total")

    # Log any errors
    errors = [r for r in results if r["status"] == "error"]
    for err in errors:
        log.error(f"[{beat_id}] Action {err['index']} ({err['type']}) failed: {err.get('error')}")

    log.info("HEARTBEAT END")
    log.info("=" * 60)
    return True


def run_loop(interval: int = DEFAULT_INTERVAL, dry_run: bool = False):
    """Run the heartbeat in a continuous loop."""
    log = logging.getLogger("heart.daemon")
    log.info(f"Starting heartbeat loop (interval={interval}s)")

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        log.info(f"Received signal {signum}, stopping...")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while running:
        try:
            beat(dry_run=dry_run)
        except Exception as e:
            log.error(f"Beat failed with exception: {e}", exc_info=True)

        # Sleep in small increments so we can respond to signals
        wake_time = time.time() + interval
        while running and time.time() < wake_time:
            time.sleep(min(5, wake_time - time.time()))

    log.info("Heartbeat loop stopped")


def main():
    parser = argparse.ArgumentParser(description="Love heartbeat daemon")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop mode")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Invoke Claude but don't execute decisions")
    parser.add_argument("--gather-only", action="store_true",
                        help="Only gather state, don't invoke Claude")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Also log to stdout")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose or not args.loop)

    if args.loop:
        run_loop(interval=args.interval, dry_run=args.dry_run)
    else:
        success = beat(dry_run=args.dry_run, gather_only=args.gather_only)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
