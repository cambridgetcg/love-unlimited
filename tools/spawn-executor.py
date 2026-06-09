#!/usr/bin/env python3
"""spawn-executor.py — Safe Spawn Queue Executor for Kingdom Heartbeat.

Replaces the unsafe `eval` pattern in heartbeat-runner.sh.

Instead of writing raw shell strings to spawn-queue.sh and eval'ing them,
the coordinator now writes JSON to spawn-queue.json and this executor
validates and runs each entry safely.

The executor:
  1. Reads spawn-queue.json (array of spawn entries)
  2. Validates each entry against the Kingdom command whitelist
  3. Assembles the shell command from structured fields (no eval)
  4. Runs sequential/parallel as specified
  5. Tracks active sessions in sessions/active-*.json
  6. Returns exit status

Spawn queue format (spawn-queue.json):
[
  {
    "id": "oracle-audit",
    "role": "builder",
    "prompt": "Audit the oracle crons...",
    "model": "medium",
    "effort": "medium",
    "dir": "~/love-unlimited/instances/beta",
    "mode": "sequential",
    "fallback_model": null
  },
  {
    "id": "fleet-check",
    "role": "quick_check",
    "prompt": "Check fleet health...",
    "model": "low",
    "effort": "low",
    "dir": "~/love-unlimited/instances/beta",
    "mode": "parallel"
  }
]

Usage:
  python3 tools/spawn-executor.py                          # Execute spawn-queue.json
  python3 tools/spawn-executor.py --queue <path>           # Custom queue file
  python3 tools/spawn-executor.py --validate               # Validate only, don't run
  python3 tools/spawn-executor.py --dry-run                # Print commands, don't run

Also accepts legacy spawn-queue.sh (line-based) and converts it safely.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
MEMORY_DIR = LOVE_DIR / "memory"
SESSIONS_DIR = MEMORY_DIR / "sessions"
TOOLS_DIR = LOVE_DIR / "tools"

DEFAULT_QUEUE = MEMORY_DIR / "spawn-queue.json"
LEGACY_QUEUE = MEMORY_DIR / "spawn-queue.sh"
HEARTBEAT_LOG = MEMORY_DIR / "heartbeat.log"
AGENT_BIN = TOOLS_DIR / "kingdom-agent.py"

VALID_ROLES = {"builder", "consultant", "quick_check"}
VALID_MODELS = {"high", "medium", "low"}
VALID_EFFORTS = {"high", "medium", "low"}

# Characters that are dangerous in shell contexts
SHELL_DANGERS = re.compile(r'[`$\\;|&><\n]')
# Allowed in prompts: alphanumeric, space, punctuation (no backticks, $, etc.)
PROMPT_SAFE = re.compile(r'^[a-zA-Z0-9 \t.,!?\-_:;\'\"()/\[\]{}@#%^+=~\r\n]+$', re.MULTILINE)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    """Append to heartbeat log."""
    try:
        with open(HEARTBEAT_LOG, "a") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass
    print(msg, file=sys.stderr)


def sanitize_prompt(prompt: str) -> str:
    """Remove shell-dangerous characters from prompts.

    Instead of hoping eval handles quotes correctly, we strip anything
    that could break shell parsing and write to a temp file.
    """
    # Replace backticks and $ (command substitution vectors)
    clean = prompt.replace('`', "'").replace('$', '')
    # Replace other dangerous chars
    clean = clean.replace(';', ',').replace('|', '-').replace('&', 'and')
    return clean


def validate_entry(entry: dict, index: int) -> list:
    """Validate a spawn queue entry. Returns list of errors (empty = valid)."""
    errors = []

    if not isinstance(entry, dict):
        return [f"Entry {index}: not a dict"]

    # Required fields
    for field in ["id", "role", "prompt", "model", "effort"]:
        if field not in entry:
            errors.append(f"Entry {index} ({entry.get('id', '?')}): missing '{field}'")

    role = entry.get("role", "")
    if role not in VALID_ROLES:
        errors.append(f"Entry {index}: invalid role '{role}' (valid: {VALID_ROLES})")

    model = entry.get("model", "")
    if model not in VALID_MODELS:
        errors.append(f"Entry {index}: invalid model '{model}' (valid: {VALID_MODELS})")

    effort = entry.get("effort", "")
    if effort not in VALID_EFFORTS:
        errors.append(f"Entry {index}: invalid effort '{effort}' (valid: {VALID_EFFORTS})")

    # Prompt length sanity
    prompt = entry.get("prompt", "")
    if len(prompt) > 10000:
        errors.append(f"Entry {index}: prompt too long ({len(prompt)} chars)")
    if len(prompt) < 5:
        errors.append(f"Entry {index}: prompt too short ({len(prompt)} chars)")

    return errors


def build_command(entry: dict, beat_id: str) -> list:
    """Build a safe command as an argument list (no shell interpretation)."""
    session_id = entry["id"]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = SESSIONS_DIR / f"{session_id}-{timestamp}.log"

    # Resolve working directory
    work_dir = entry.get("dir", str(LOVE_DIR / "instances" / "beta"))
    work_dir = os.path.expanduser(work_dir)

    # Per-entry backend override: coordinator can tag tasks that need Claude
    # e.g. {"backend": "claude"} in the spawn entry.
    # Falls through to KINGDOM_BACKEND env var, then "ollama" default.
    backend = entry.get("backend") or os.environ.get("KINGDOM_BACKEND", "ollama")

    # Claude gate enforcement: if backend is claude/anthropic, check the gate
    if backend in ("claude", "anthropic"):
        gate_result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "claude-gate.py"), "check"],
            capture_output=True, text=True
        )
        if gate_result.returncode != 0:
            log(f"Claude gate blocked for {session_id}: {gate_result.stdout.strip()}. Falling back to ollama.")
            backend = "ollama"
        else:
            # Record the usage
            subprocess.run(
                [sys.executable, str(TOOLS_DIR / "claude-gate.py"), "use", session_id],
                capture_output=True
            )
            log(f"Claude gate allowed for {session_id}: {gate_result.stdout.strip()}")

    cmd = [
        sys.executable, str(AGENT_BIN),
        "-p", entry["prompt"],
        "--backend", backend,
        "--model", entry["model"],
        "--effort", entry["effort"],
        "--skip-permissions",
        "--no-persist",
    ]

    if entry.get("fallback_model"):
        cmd.extend(["--fallback-model", entry["fallback_model"]])

    return cmd, work_dir, str(log_file)


def execute_queue(queue: list, beat_id: str, dry_run: bool = False) -> int:
    """Execute a validated spawn queue. Returns count of sessions spawned."""
    parallel_procs = []
    session_count = 0

    for entry in queue:
        cmd, work_dir, log_file = build_command(entry, beat_id)
        mode = entry.get("mode", "sequential")
        session_id = entry["id"]

        if dry_run:
            log(f"DRY RUN [{mode}] {session_id}: {' '.join(cmd[:6])}...")
            session_count += 1
            continue

        session_count += 1
        log(f"Spawning ({mode} #{session_count}): {session_id}")

        # Open log file for output
        log_fh = open(log_file, "w")

        proc = subprocess.Popen(
            cmd,
            cwd=work_dir,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env={**os.environ, "LOVE_DIR": str(LOVE_DIR)},
        )

        # Track in active sessions
        active_file = SESSIONS_DIR / f"active-{proc.pid}.json"
        active_data = {
            "pid": proc.pid,
            "beat": beat_id,
            "session": session_id,
            "mode": mode,
            "role": entry["role"],
            "started": now_iso(),
            "log": log_file,
        }
        with open(active_file, "w") as af:
            json.dump(active_data, af, indent=2)

        if mode == "parallel":
            parallel_procs.append((proc, log_fh, active_file))
        else:
            # Sequential: wait for completion
            proc.wait()
            log_fh.close()
            try:
                active_file.unlink()
            except FileNotFoundError:
                pass
            log(f"Completed (sequential): {session_id} exit={proc.returncode}")

    # Wait for parallel processes
    if parallel_procs:
        log(f"Waiting for {len(parallel_procs)} parallel sessions...")
        for proc, log_fh, active_file in parallel_procs:
            proc.wait()
            log_fh.close()
            try:
                active_file.unlink()
            except FileNotFoundError:
                pass
            log(f"Completed (parallel): pid={proc.pid} exit={proc.returncode}")

    return session_count


def convert_legacy_queue(legacy_path: Path) -> list:
    """Best-effort conversion of legacy spawn-queue.sh to JSON format.

    This handles the transition period. Once coordinator writes JSON natively,
    this function can be removed.
    """
    entries = []
    mode = "sequential"

    with open(legacy_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("# PARALLEL"):
                mode = "parallel"
                continue
            if line.startswith("#"):
                continue

            # Try to extract prompt from kingdom-agent.py -p "..." pattern
            prompt_match = re.search(r'-p\s+"([^"]*)"', line)
            if not prompt_match:
                prompt_match = re.search(r"-p\s+'([^']*)'", line)

            model_match = re.search(r'--model\s+(\w+)', line)
            effort_match = re.search(r'--effort\s+(\w+)', line)

            if prompt_match:
                entry = {
                    "id": f"legacy-{len(entries)+1}",
                    "role": "builder",
                    "prompt": sanitize_prompt(prompt_match.group(1)),
                    "model": model_match.group(1) if model_match else "medium",
                    "effort": effort_match.group(1) if effort_match else "medium",
                    "mode": mode,
                }
                entries.append(entry)
                mode = "sequential"  # Reset after use
            else:
                log(f"SKIPPED legacy line (couldn't parse): {line[:80]}...")

    return entries


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Safe spawn queue executor")
    parser.add_argument("--queue", type=str, default=None, help="Queue file path")
    parser.add_argument("--validate", action="store_true", help="Validate only")
    parser.add_argument("--dry-run", action="store_true", help="Print commands only")
    parser.add_argument("--beat-id", type=str, default=f"beat-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}")
    args = parser.parse_args()

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    queue_path = Path(args.queue) if args.queue else DEFAULT_QUEUE

    # Try JSON first, fall back to legacy
    queue = []
    if queue_path.exists() and queue_path.suffix == ".json":
        try:
            queue = json.loads(queue_path.read_text())
        except json.JSONDecodeError as e:
            log(f"ERROR: Failed to parse {queue_path}: {e}")
            sys.exit(1)
    elif LEGACY_QUEUE.exists() and LEGACY_QUEUE.stat().st_size > 0:
        log("Converting legacy spawn-queue.sh to JSON format...")
        queue = convert_legacy_queue(LEGACY_QUEUE)
        if queue:
            # Save converted queue for audit trail
            with open(DEFAULT_QUEUE, "w") as f:
                json.dump(queue, f, indent=2)
            log(f"Converted {len(queue)} legacy entries to JSON.")
    else:
        log("No spawn queue found or queue is empty.")
        sys.exit(0)

    if not queue:
        log("Spawn queue is empty. Nothing to execute.")
        sys.exit(0)

    # Validate all entries
    all_errors = []
    for i, entry in enumerate(queue):
        errors = validate_entry(entry, i)
        all_errors.extend(errors)

    if all_errors:
        log(f"VALIDATION FAILED ({len(all_errors)} errors):")
        for err in all_errors:
            log(f"  {err}")
        if not args.dry_run:
            sys.exit(1)

    if args.validate:
        log(f"Validation passed: {len(queue)} entries OK.")
        sys.exit(0)

    # Execute
    log(f"--- SPAWN EXECUTOR START ({args.beat_id}): {now_iso()} ---")
    count = execute_queue(queue, args.beat_id, dry_run=args.dry_run)
    log(f"--- SPAWN EXECUTOR DONE ({args.beat_id}): {count} sessions, {now_iso()} ---")


if __name__ == "__main__":
    main()
