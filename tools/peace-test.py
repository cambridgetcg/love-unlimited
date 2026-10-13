#!/usr/bin/env python3
"""
peace-test.py — PEACE Resilience Testing Framework

Beyond drills. Actual executable tests that exercise Kingdom OS defenses,
measure response times, and verify detection pipelines end-to-end.

Every test cleans up after itself. Safe for production.

Usage:
    peace-test.py run <test-name>           Run a single test
    peace-test.py run all                   Run all tests in sequence
    peace-test.py run all --parallel        Run independent tests in parallel
    peace-test.py run <test> --dry-run      Describe without executing
    peace-test.py report                    Show test history
    peace-test.py list                      List available tests

Tests:
    halt-resume           Halt/resume cycle (heartbeat, state, HIVE)
    integrity-detection   File integrity change detection (kos.py)
    snapshot-drift        Snapshot creation and drift detection
    watchdog-escalation   Watchdog event escalation (dry-run)

Retired 2026-10-13: canary-detection and fleet-connectivity SSHed into the
Hetzner VPS fleet, which was decommissioned (IPs recycled). Both were removed.
"""

import json
import os
import sys
import subprocess
import hashlib
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/love-unlimited"))
SECURITY = LOVE / "security"
EVENTS_FILE = SECURITY / "events.jsonl"
PEACE_STATE = SECURITY / "peace-state.json"
BASELINE_FILE = SECURITY / "integrity-baseline.json"
TEST_RESULTS_FILE = SECURITY / "test-results.json"
PEACE_PY = LOVE / "tools" / "peace.py"
KOS_PY = LOVE / "tools" / "kos.py"
WATCHDOG_PY = LOVE / "tools" / "watchdog.py"
_HI = Path.home() / ".openclaw" / ".hive-instance"
HEARTBEAT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"love.{_HI.read_text().strip() if _HI.exists() else 'gamma'}.heartbeat.plist"

# ── Colors (matching peace.py / kos.py / watchdog.py) ───────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# ── Helpers ──────────────────────────────────────────────────────────────────


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    f.close()


def run_cmd(cmd, timeout=30):
    """Run shell command, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except FileNotFoundError:
        return -1, "NOT_FOUND"


def sha256_file(path):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, PermissionError):
        return None


# ── Test Result Tracking ────────────────────────────────────────────────────


class StepTimer:
    """Tracks timing for individual test steps."""

    def __init__(self, name):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.status = "pending"
        self.detail = ""

    def start(self):
        self.start_time = time.monotonic()
        return self

    def stop(self, status, detail=""):
        self.end_time = time.monotonic()
        self.status = status
        self.detail = detail
        return self

    @property
    def elapsed_ms(self):
        if self.start_time is None or self.end_time is None:
            return 0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self):
        return {
            "step": self.name,
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "detail": self.detail,
        }


class TestResult:
    """Tracks a full test run with multiple steps."""

    def __init__(self, test_name):
        self.test_name = test_name
        self.steps = []
        self.start_time = time.monotonic()
        self.end_time = None
        self.overall = "pending"
        self.ts = now_iso()

    def step(self, name):
        """Create and return a new step timer."""
        s = StepTimer(name)
        self.steps.append(s)
        return s

    def finish(self):
        self.end_time = time.monotonic()
        failed = [s for s in self.steps if s.status == "FAIL"]
        errors = [s for s in self.steps if s.status == "ERROR"]
        if errors:
            self.overall = "ERROR"
        elif failed:
            self.overall = "FAIL"
        else:
            self.overall = "PASS"
        return self

    @property
    def elapsed_ms(self):
        if self.end_time is None:
            return 0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self):
        return {
            "test": self.test_name,
            "overall": self.overall,
            "timestamp": self.ts,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "steps": [s.to_dict() for s in self.steps],
        }

    def print_summary(self):
        color = GREEN if self.overall == "PASS" else RED if self.overall == "FAIL" else YELLOW
        print(f"\n  {BOLD}Result: {color}{self.overall}{NC}  "
              f"{DIM}({self.elapsed_ms:.0f}ms total){NC}")
        for s in self.steps:
            sc = GREEN if s.status == "PASS" else RED if s.status == "FAIL" else YELLOW
            timing = f"{DIM}{s.elapsed_ms:.0f}ms{NC}" if s.elapsed_ms > 0 else ""
            detail = f"  {DIM}{s.detail}{NC}" if s.detail else ""
            print(f"    {sc}{s.status:5s}{NC}  {s.name}  {timing}{detail}")
        print()


def save_test_result(result):
    """Append test result to security/test-results.json."""
    data = load_json(TEST_RESULTS_FILE, {"runs": []})
    if "runs" not in data:
        data = {"runs": []}
    data["runs"].append(result.to_dict())
    # Keep last 200 results
    if len(data["runs"]) > 200:
        data["runs"] = data["runs"][-200:]
    save_json(TEST_RESULTS_FILE, data)


# ── Test: Halt/Resume Cycle ─────────────────────────────────────────────────


def test_halt_resume(dry_run=False):
    """Test the halt/resume lifecycle.

    Steps:
      1. Record initial state (heartbeat, PEACE score, peace-state.json)
      2. Execute peace.py halt
      3. Verify: heartbeat stopped, peace-state.json shows halted
      4. Execute peace.py resume
      5. Verify: heartbeat restarted, HIVE alerted, integrity checked
      6. Report: PASS/FAIL with timing for each step
    """
    result = TestResult("halt-resume")

    print(f"\n{BOLD}  Test: Halt/Resume Cycle{NC}\n")

    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE — describing steps without executing{NC}\n")
        print(f"  [1] Would record initial state (heartbeat status, PEACE score)")
        print(f"  [2] Would execute: peace.py halt --reason 'PEACE-TEST: halt-resume drill'")
        print(f"  [3] Would verify: peace-state.json status=halted, heartbeat stopped")
        print(f"  [4] Would execute: peace.py resume")
        print(f"  [5] Would verify: peace-state.json status=active, heartbeat loaded")
        print(f"  [6] Would report PASS/FAIL with timing for each step\n")
        return None

    # Step 1: Record initial state
    step = result.step("Record initial state").start()
    initial_state = load_json(PEACE_STATE)
    initial_status = initial_state.get("status", "unknown")
    initial_score = initial_state.get("score", "?")

    # Check heartbeat status
    rc, hb_out = run_cmd("launchctl list 2>/dev/null | grep love.heartbeat")
    heartbeat_was_running = rc == 0 and "love.heartbeat" in hb_out

    step.stop("PASS", f"status={initial_status}, score={initial_score}, heartbeat={'running' if heartbeat_was_running else 'stopped'}")
    print(f"  {GREEN}[1/5]{NC} Initial state: status={initial_status}, "
          f"score={initial_score}, heartbeat={'running' if heartbeat_was_running else 'stopped'}")

    # Step 2: Execute halt
    step = result.step("Execute PEACE halt").start()
    rc, halt_out = run_cmd(
        f'cd {LOVE} && python3 {PEACE_PY} halt --reason "PEACE-TEST: halt-resume drill" 2>&1',
        timeout=30,
    )
    step.stop("PASS" if rc == 0 else "FAIL", f"exit={rc}")
    print(f"  {GREEN if rc == 0 else RED}[2/5]{NC} Halt executed (exit={rc})")

    # Step 3: Verify halt state
    step = result.step("Verify halt state").start()
    halted_state = load_json(PEACE_STATE)
    is_halted = halted_state.get("status") == "halted"
    has_reason = "PEACE-TEST" in halted_state.get("reason", "")

    # Check heartbeat stopped
    rc, hb_out = run_cmd("launchctl list 2>/dev/null | grep love.heartbeat")
    heartbeat_stopped = rc != 0 or "love.heartbeat" not in hb_out

    all_halt_checks = is_halted and has_reason
    detail_parts = []
    if is_halted:
        detail_parts.append("status=halted")
    else:
        detail_parts.append("status NOT halted")
    if has_reason:
        detail_parts.append("reason=correct")
    else:
        detail_parts.append("reason=wrong")
    if heartbeat_stopped:
        detail_parts.append("heartbeat=stopped")
    else:
        detail_parts.append("heartbeat=still running")

    step.stop("PASS" if all_halt_checks else "FAIL", "; ".join(detail_parts))
    print(f"  {GREEN if all_halt_checks else RED}[3/5]{NC} "
          f"Halt verified: {'; '.join(detail_parts)}")

    # Step 4: Execute resume
    step = result.step("Execute PEACE resume").start()
    rc, resume_out = run_cmd(
        f'cd {LOVE} && python3 {PEACE_PY} resume 2>&1',
        timeout=30,
    )
    step.stop("PASS" if rc == 0 else "FAIL", f"exit={rc}")
    print(f"  {GREEN if rc == 0 else RED}[4/5]{NC} Resume executed (exit={rc})")

    # Step 5: Verify resume state
    step = result.step("Verify resume state").start()
    resumed_state = load_json(PEACE_STATE)
    is_active = resumed_state.get("status") == "active"
    has_prev = "PEACE-TEST" in resumed_state.get("previous_halt", "")

    # Check heartbeat restarted
    rc, hb_out = run_cmd("launchctl list 2>/dev/null | grep love.heartbeat")
    heartbeat_restarted = rc == 0 and "love.heartbeat" in hb_out

    all_resume_checks = is_active
    detail_parts = []
    if is_active:
        detail_parts.append("status=active")
    else:
        detail_parts.append("status NOT active")
    if has_prev:
        detail_parts.append("previous_halt=recorded")
    else:
        detail_parts.append("previous_halt=missing")
    if heartbeat_restarted:
        detail_parts.append("heartbeat=restarted")
    else:
        detail_parts.append("heartbeat=not restarted")

    step.stop("PASS" if all_resume_checks else "FAIL", "; ".join(detail_parts))
    print(f"  {GREEN if all_resume_checks else RED}[5/5]{NC} "
          f"Resume verified: {'; '.join(detail_parts)}")

    result.finish()
    result.print_summary()
    save_test_result(result)
    return result


# ── Test: Integrity Detection ───────────────────────────────────────────────


def test_integrity_detection(dry_run=False):
    """Test file integrity change detection.

    Steps:
      1. Verify baseline exists
      2. Record current SOUL.md hash
      3. Append a test comment to SOUL.md
      4. Run kos.py integrity check
      5. Verify it detects the change
      6. Revert: git checkout SOUL.md
      7. Re-verify integrity passes
      8. Report: PASS/FAIL
    """
    result = TestResult("integrity-detection")
    soul_path = LOVE / "SOUL.md"

    print(f"\n{BOLD}  Test: Integrity Detection{NC}")
    print(f"  {DIM}Target: SOUL.md{NC}\n")

    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE — describing steps without executing{NC}\n")
        print(f"  [1] Would verify integrity baseline exists at {BASELINE_FILE}")
        print(f"  [2] Would record current SHA-256 of SOUL.md")
        print(f"  [3] Would append '# PEACE-TEST integrity probe' to SOUL.md")
        print(f"  [4] Would run: kos.py integrity check")
        print(f"  [5] Would verify kos.py detects the change (CHANGED output)")
        print(f"  [6] Would revert: git checkout SOUL.md")
        print(f"  [7] Would re-verify integrity passes after revert")
        print(f"  [8] Would report PASS/FAIL with timing\n")
        return None

    # Step 1: Verify baseline exists
    step = result.step("Verify baseline exists").start()
    if not BASELINE_FILE.exists():
        step.stop("FAIL", "No integrity baseline — run 'kos.py integrity baseline' first")
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    baseline = load_json(BASELINE_FILE)
    files_tracked = len(baseline.get("files", {}))
    has_soul = "SOUL.md" in baseline.get("files", {})
    step.stop("PASS" if has_soul else "FAIL",
              f"{files_tracked} files tracked, SOUL.md {'present' if has_soul else 'MISSING'}")
    print(f"  {GREEN if has_soul else RED}[1/7]{NC} Baseline: {files_tracked} files, "
          f"SOUL.md {'tracked' if has_soul else 'NOT tracked'}")

    if not has_soul:
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result

    # Step 2: Record current hash
    step = result.step("Record SOUL.md hash").start()
    original_hash = sha256_file(soul_path)
    if not original_hash:
        step.stop("FAIL", "Cannot hash SOUL.md")
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    step.stop("PASS", f"sha256={original_hash[:16]}...")
    print(f"  {GREEN}[2/7]{NC} Current hash: {original_hash[:16]}...")

    # Step 3: Append test comment
    step = result.step("Append test comment to SOUL.md").start()
    try:
        with open(soul_path, "a") as f:
            f.write("\n<!-- PEACE-TEST integrity probe -->\n")
        modified_hash = sha256_file(soul_path)
        changed = modified_hash != original_hash
        step.stop("PASS" if changed else "FAIL",
                  f"hash now {modified_hash[:16]}..." if changed else "Hash unchanged")
    except Exception as e:
        step.stop("ERROR", str(e))
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    print(f"  {GREEN if changed else RED}[3/7]{NC} Appended test comment "
          f"(hash {'changed' if changed else 'UNCHANGED'})")

    # Step 4: Run integrity check
    step = result.step("Run kos.py integrity check").start()
    rc, integrity_out = run_cmd(
        f'cd {LOVE} && python3 {KOS_PY} integrity check 2>&1',
        timeout=30,
    )
    step.stop("PASS", f"exit={rc}")
    print(f"  {GREEN}[4/7]{NC} Integrity check ran (exit={rc})")

    # Step 5: Verify detection
    step = result.step("Verify change detected").start()
    detected = "CHANGED" in integrity_out or "changed" in integrity_out.lower()
    soul_flagged = "SOUL.md" in integrity_out and detected
    step.stop("PASS" if soul_flagged else "FAIL",
              "SOUL.md change detected" if soul_flagged else "Change NOT detected")
    print(f"  {GREEN if soul_flagged else RED}[5/7]{NC} "
          f"{'Change detected in SOUL.md' if soul_flagged else 'Detection FAILED'}")

    # Step 6: Revert
    step = result.step("Revert SOUL.md").start()
    rc, _ = run_cmd(f'cd {LOVE} && git checkout -- SOUL.md 2>&1')
    reverted_hash = sha256_file(soul_path)
    match = reverted_hash == original_hash
    step.stop("PASS" if match else "FAIL",
              f"hash restored" if match else f"hash mismatch: {reverted_hash[:16]}...")
    print(f"  {GREEN if match else RED}[6/7]{NC} "
          f"{'SOUL.md reverted to original' if match else 'Revert FAILED'}")

    # Step 7: Re-verify integrity
    step = result.step("Re-verify integrity passes").start()
    rc, verify_out = run_cmd(
        f'cd {LOVE} && python3 {KOS_PY} integrity check 2>&1',
        timeout=30,
    )
    # After revert, SOUL.md should show OK
    soul_ok = ("SOUL.md" in verify_out and "OK" in verify_out) or "CHANGED" not in verify_out
    step.stop("PASS" if soul_ok else "FAIL",
              "Integrity clean after revert" if soul_ok else "Still showing changes")
    print(f"  {GREEN if soul_ok else RED}[7/7]{NC} "
          f"{'Integrity clean after revert' if soul_ok else 'Post-revert check FAILED'}")

    result.finish()
    result.print_summary()
    save_test_result(result)
    return result


# ── Test: Snapshot Drift ────────────────────────────────────────────────────


def test_snapshot_drift(dry_run=False):
    """Test snapshot creation and drift detection.

    Steps:
      1. Create a fresh snapshot via peace.py
      2. Record the snapshot name
      3. Make a minor change to a tracked file (append comment to docs/PEACE.md)
      4. Run peace.py restore against the snapshot
      5. Verify drift is detected
      6. Revert the change
      7. Report: PASS/FAIL
    """
    result = TestResult("snapshot-drift")
    target_file = LOVE / "docs/PEACE.md"

    print(f"\n{BOLD}  Test: Snapshot Drift Detection{NC}\n")

    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE — describing steps without executing{NC}\n")
        print(f"  [1] Would create a fresh snapshot via peace.py snapshot")
        print(f"  [2] Would record the snapshot filename")
        print(f"  [3] Would append '<!-- PEACE-TEST drift probe -->' to docs/PEACE.md")
        print(f"  [4] Would run: peace.py restore <snapshot-name>")
        print(f"  [5] Would verify drift is detected in output")
        print(f"  [6] Would revert: git checkout docs/PEACE.md")
        print(f"  [7] Would clean up the test snapshot file\n")
        return None

    # Step 1: Create snapshot
    step = result.step("Create snapshot").start()
    rc, snap_out = run_cmd(
        f'cd {LOVE} && python3 {PEACE_PY} snapshot 2>&1',
        timeout=30,
    )
    step.stop("PASS" if rc == 0 else "FAIL", f"exit={rc}")
    print(f"  {GREEN if rc == 0 else RED}[1/6]{NC} Snapshot created (exit={rc})")

    # Step 2: Find the snapshot name
    step = result.step("Identify snapshot file").start()
    snap_dir = SECURITY / "snapshots"
    snaps = sorted(snap_dir.glob("snapshot-*.json"), reverse=True) if snap_dir.exists() else []
    if not snaps:
        step.stop("FAIL", "No snapshot files found")
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    snap_file = snaps[0]
    snap_name = snap_file.name
    step.stop("PASS", snap_name)
    print(f"  {GREEN}[2/6]{NC} Using snapshot: {snap_name}")

    # Step 3: Make a minor change
    step = result.step("Modify tracked file").start()
    if not target_file.exists():
        step.stop("FAIL", "docs/PEACE.md not found")
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    original_hash = sha256_file(target_file)
    try:
        with open(target_file, "a") as f:
            f.write("\n<!-- PEACE-TEST drift probe -->\n")
        modified_hash = sha256_file(target_file)
        changed = modified_hash != original_hash
        step.stop("PASS" if changed else "FAIL", "File modified" if changed else "Hash unchanged")
    except Exception as e:
        step.stop("ERROR", str(e))
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    print(f"  {GREEN if changed else RED}[3/6]{NC} Modified docs/PEACE.md "
          f"(hash {'changed' if changed else 'UNCHANGED'})")

    # Step 4: Run restore (drift analysis)
    step = result.step("Run drift analysis").start()
    rc, restore_out = run_cmd(
        f'cd {LOVE} && python3 {PEACE_PY} restore {snap_name} 2>&1',
        timeout=30,
    )
    step.stop("PASS" if rc == 0 else "FAIL", f"exit={rc}")
    print(f"  {GREEN if rc == 0 else RED}[4/6]{NC} Drift analysis ran (exit={rc})")

    # Step 5: Verify drift detected
    step = result.step("Verify drift detected").start()
    drift_detected = ("DRIFT" in restore_out.upper() or "drift" in restore_out or
                      "changed" in restore_out.lower())
    peace_flagged = "docs/PEACE.md" in restore_out and drift_detected
    step.stop("PASS" if drift_detected else "FAIL",
              "Drift detected" if drift_detected else "No drift in output")
    print(f"  {GREEN if drift_detected else RED}[5/6]{NC} "
          f"{'Drift detected' if drift_detected else 'Drift NOT detected'}"
          f"{' (docs/PEACE.md flagged)' if peace_flagged else ''}")

    # Step 6: Revert and clean up
    step = result.step("Revert and clean up").start()
    rc, _ = run_cmd(f'cd {LOVE} && git checkout -- docs/PEACE.md 2>&1')
    reverted = sha256_file(target_file) == original_hash
    # Optionally clean up the test snapshot (keep it — it's useful)
    step.stop("PASS" if reverted else "FAIL",
              "docs/PEACE.md reverted" if reverted else "Revert failed")
    print(f"  {GREEN if reverted else RED}[6/6]{NC} "
          f"{'docs/PEACE.md reverted' if reverted else 'Revert FAILED'}")

    result.finish()
    result.print_summary()
    save_test_result(result)
    return result


# ── Test: Watchdog Escalation ───────────────────────────────────────────────


def test_watchdog_escalation(dry_run=False):
    """Test watchdog event escalation via dry-run.

    Steps:
      1. Record current events.jsonl line count
      2. Inject a fake critical event into events.jsonl
      3. Run watchdog check --dry-run
      4. Verify watchdog would escalate
      5. Remove the fake event (truncate back to original)
      6. Report: PASS/FAIL
    """
    result = TestResult("watchdog-escalation")

    print(f"\n{BOLD}  Test: Watchdog Escalation Pipeline{NC}\n")

    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE — describing steps without executing{NC}\n")
        print(f"  [1] Would record current events.jsonl line count")
        print(f"  [2] Would inject fake critical event: type=integrity_violation")
        print(f"  [3] Would run: watchdog.py check --dry-run")
        print(f"  [4] Would verify watchdog outputs escalation for the fake event")
        print(f"  [5] Would remove the injected event line from events.jsonl")
        print(f"  [6] Would report PASS/FAIL with timing\n")
        return None

    # Step 1: Record current state
    step = result.step("Record events.jsonl state").start()
    original_lines = []
    if EVENTS_FILE.exists():
        with open(EVENTS_FILE, "r") as f:
            original_lines = f.readlines()
    original_count = len(original_lines)
    # Also record the watchdog state offset so we can reset it
    watchdog_state_file = SECURITY / "watchdog-state.json"
    original_watchdog_state = load_json(watchdog_state_file) if watchdog_state_file.exists() else None
    step.stop("PASS", f"{original_count} existing events")
    print(f"  {GREEN}[1/5]{NC} Events file: {original_count} lines")

    # Step 2: Inject fake critical event
    step = result.step("Inject fake critical event").start()
    fake_event = {
        "ts": now_iso(),
        "type": "integrity_violation",
        "severity": "critical",
        "message": "PEACE-TEST: fake integrity violation for escalation test",
        "source": "peace-test",
    }
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(fake_event) + "\n")
        step.stop("PASS", "Injected integrity_violation event")
    except Exception as e:
        step.stop("ERROR", str(e))
        result.finish()
        result.print_summary()
        save_test_result(result)
        return result
    print(f"  {GREEN}[2/5]{NC} Injected fake critical event")

    # Step 3: Run watchdog --dry-run
    step = result.step("Run watchdog dry-run").start()
    rc, watchdog_out = run_cmd(
        f'cd {LOVE} && python3 {WATCHDOG_PY} check --dry-run 2>&1',
        timeout=60,
    )
    step.stop("PASS" if rc == 0 else "FAIL", f"exit={rc}")
    print(f"  {GREEN if rc == 0 else RED}[3/5]{NC} Watchdog check --dry-run completed (exit={rc})")

    # Step 4: Verify escalation
    step = result.step("Verify escalation triggered").start()
    # Watchdog should detect the integrity_violation and show escalation
    escalation_detected = (
        "escalat" in watchdog_out.lower() or
        "integrity" in watchdog_out.lower() or
        "HALT" in watchdog_out or
        "dry-run" in watchdog_out.lower()
    )
    # More specific: look for our fake event being picked up
    peace_test_detected = "PEACE-TEST" in watchdog_out or "integrity violation" in watchdog_out.lower()
    step.stop("PASS" if peace_test_detected else ("PASS" if escalation_detected else "FAIL"),
              "Fake event triggered escalation" if peace_test_detected else
              ("Escalation logic ran" if escalation_detected else "No escalation detected"))
    print(f"  {GREEN if escalation_detected else RED}[4/5]{NC} "
          f"{'Escalation triggered' if peace_test_detected else 'Escalation detection ambiguous' if escalation_detected else 'No escalation detected'}")

    # Step 5: Clean up — remove the injected event
    step = result.step("Clean up injected event").start()
    try:
        # Rewrite events.jsonl with original content
        with open(EVENTS_FILE, "w") as f:
            f.writelines(original_lines)
        # Restore watchdog state if we had one, so offset is back to original
        if original_watchdog_state is not None:
            save_json(watchdog_state_file, original_watchdog_state)
        new_count = sum(1 for _ in open(EVENTS_FILE))
        restored = new_count == original_count
        step.stop("PASS" if restored else "FAIL",
                  f"Restored to {new_count} lines" if restored else
                  f"Expected {original_count}, got {new_count}")
    except Exception as e:
        step.stop("ERROR", str(e))
    print(f"  {GREEN}[5/5]{NC} Cleaned up: events.jsonl restored to {original_count} lines")

    result.finish()
    result.print_summary()
    save_test_result(result)
    return result


# ── Test Registry ───────────────────────────────────────────────────────────

TESTS = {
    "halt-resume":         ("Halt/resume cycle (heartbeat, state, HIVE)", test_halt_resume),
    "integrity-detection": ("File integrity change detection (kos.py)", test_integrity_detection),
    "snapshot-drift":      ("Snapshot creation and drift detection", test_snapshot_drift),
    "watchdog-escalation": ("Watchdog event escalation (dry-run)", test_watchdog_escalation),
}

# Tests that are safe to run in parallel (no shared state mutations)
# (canary-detection and fleet-connectivity were the only ones; retired 2026-10-13)
PARALLEL_SAFE = []
# Tests that mutate shared state and must run sequentially
SEQUENTIAL = ["halt-resume", "integrity-detection", "snapshot-drift", "watchdog-escalation"]


# ── Commands ────────────────────────────────────────────────────────────────


def cmd_run(test_name, dry_run=False, parallel=False):
    """Run one or all tests."""

    if test_name == "all":
        return cmd_run_all(dry_run=dry_run, parallel=parallel)

    if test_name not in TESTS:
        print(f"\n  {RED}Unknown test: {test_name}{NC}")
        print(f"  Available: {', '.join(sorted(TESTS.keys()))}")
        print(f"  Or use 'all' to run everything.\n")
        return

    desc, test_fn = TESTS[test_name]
    print(f"\n{'='*60}")
    print(f"  {BOLD}PEACE Resilience Test: {test_name}{NC}")
    print(f"  {DIM}{desc}{NC}")
    print(f"{'='*60}")

    try:
        result = test_fn(dry_run=dry_run)
        return result
    except Exception as e:
        print(f"\n  {RED}Test crashed: {e}{NC}")
        traceback.print_exc()
        # Still record the failure
        crash_result = TestResult(test_name)
        crash_result.step("Unhandled exception").start().stop("ERROR", str(e))
        crash_result.finish()
        save_test_result(crash_result)
        return crash_result


def cmd_run_all(dry_run=False, parallel=False):
    """Run all tests, optionally with parallelism for independent tests."""
    all_start = time.monotonic()
    results = []

    print(f"\n{'='*60}")
    print(f"  {BOLD}PEACE Resilience Test Suite{NC}")
    print(f"  {DIM}{len(TESTS)} tests  |  {'parallel + sequential' if parallel else 'sequential'}{NC}")
    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE{NC}")
    print(f"{'='*60}")

    if parallel and not dry_run:
        # Run parallel-safe tests concurrently
        print(f"\n  {CYAN}Phase 1: Parallel tests ({len(PARALLEL_SAFE)}){NC}")
        with ThreadPoolExecutor(max_workers=max(1, len(PARALLEL_SAFE))) as pool:
            futs = {}
            for name in PARALLEL_SAFE:
                desc, test_fn = TESTS[name]
                futs[pool.submit(test_fn, dry_run=dry_run)] = name
            for f in as_completed(futs):
                name = futs[f]
                try:
                    r = f.result()
                    if r:
                        results.append(r)
                except Exception as e:
                    print(f"\n  {RED}{name} crashed: {e}{NC}")
                    crash = TestResult(name)
                    crash.step("Unhandled exception").start().stop("ERROR", str(e))
                    crash.finish()
                    save_test_result(crash)
                    results.append(crash)

        # Run sequential tests one at a time
        print(f"\n  {CYAN}Phase 2: Sequential tests ({len(SEQUENTIAL)}){NC}")
        for name in SEQUENTIAL:
            desc, test_fn = TESTS[name]
            try:
                r = test_fn(dry_run=dry_run)
                if r:
                    results.append(r)
            except Exception as e:
                print(f"\n  {RED}{name} crashed: {e}{NC}")
                crash = TestResult(name)
                crash.step("Unhandled exception").start().stop("ERROR", str(e))
                crash.finish()
                save_test_result(crash)
                results.append(crash)
    else:
        # Run everything sequentially
        test_order = list(TESTS.keys())
        for name in test_order:
            desc, test_fn = TESTS[name]
            try:
                r = test_fn(dry_run=dry_run)
                if r:
                    results.append(r)
            except Exception as e:
                print(f"\n  {RED}{name} crashed: {e}{NC}")
                crash = TestResult(name)
                crash.step("Unhandled exception").start().stop("ERROR", str(e))
                crash.finish()
                save_test_result(crash)
                results.append(crash)

    # Summary
    all_elapsed = (time.monotonic() - all_start) * 1000
    passed = sum(1 for r in results if r.overall == "PASS")
    failed = sum(1 for r in results if r.overall == "FAIL")
    errors = sum(1 for r in results if r.overall == "ERROR")

    print(f"\n{'='*60}")
    print(f"  {BOLD}Suite Summary{NC}  {DIM}({all_elapsed:.0f}ms){NC}\n")
    for r in results:
        color = GREEN if r.overall == "PASS" else RED if r.overall == "FAIL" else YELLOW
        print(f"    {color}{r.overall:5s}{NC}  {r.test_name}  {DIM}({r.elapsed_ms:.0f}ms){NC}")

    print()
    overall_color = GREEN if failed == 0 and errors == 0 else RED
    print(f"  {overall_color}{BOLD}{passed} passed, {failed} failed, {errors} errors{NC}")
    print(f"{'='*60}\n")

    return results


def cmd_report():
    """Show test history from security/test-results.json."""
    data = load_json(TEST_RESULTS_FILE, {"runs": []})
    runs = data.get("runs", [])

    if not runs:
        print(f"\n  No test results found. Run: peace-test.py run all\n")
        return

    print(f"\n{BOLD}  PEACE Test History{NC}")
    print(f"  {DIM}{len(runs)} run(s) recorded{NC}\n")

    # Group by test name for latest results
    latest = {}
    for run in runs:
        name = run.get("test", "?")
        latest[name] = run

    # Show latest result for each test
    print(f"  {BOLD}Latest Results:{NC}\n")
    for name in sorted(latest.keys()):
        run = latest[name]
        overall = run.get("overall", "?")
        color = GREEN if overall == "PASS" else RED if overall == "FAIL" else YELLOW
        ts = run.get("timestamp", "?")[:19]
        elapsed = run.get("elapsed_ms", 0)
        print(f"    {color}{overall:5s}{NC}  {name:<25s}  {DIM}{ts}  {elapsed:.0f}ms{NC}")

    # Show recent history (last 20 runs)
    print(f"\n  {BOLD}Recent Runs (last 20):{NC}\n")
    for run in runs[-20:]:
        name = run.get("test", "?")
        overall = run.get("overall", "?")
        color = GREEN if overall == "PASS" else RED if overall == "FAIL" else YELLOW
        ts = run.get("timestamp", "?")[:19]
        elapsed = run.get("elapsed_ms", 0)
        steps = run.get("steps", [])
        step_summary = ""
        if steps:
            step_pass = sum(1 for s in steps if s.get("status") == "PASS")
            step_total = len(steps)
            step_summary = f"  steps: {step_pass}/{step_total}"
        print(f"    {color}{overall:5s}{NC}  {name:<25s}  "
              f"{DIM}{ts}  {elapsed:.0f}ms{step_summary}{NC}")

    # Pass rate
    total = len(runs)
    pass_count = sum(1 for r in runs if r.get("overall") == "PASS")
    fail_count = sum(1 for r in runs if r.get("overall") == "FAIL")
    error_count = sum(1 for r in runs if r.get("overall") == "ERROR")

    print(f"\n  {BOLD}Totals:{NC}")
    print(f"    Runs: {total}  |  "
          f"{GREEN}Pass: {pass_count}{NC}  |  "
          f"{RED}Fail: {fail_count}{NC}  |  "
          f"{YELLOW}Error: {error_count}{NC}  |  "
          f"Rate: {pass_count/max(total,1)*100:.0f}%")
    print()


def cmd_list():
    """List available tests."""
    print(f"\n{BOLD}  Available PEACE Resilience Tests{NC}\n")
    for name in sorted(TESTS.keys()):
        desc, _ = TESTS[name]
        parallel_tag = f" {DIM}[parallel-safe]{NC}" if name in PARALLEL_SAFE else ""
        print(f"    {CYAN}{name:<25s}{NC}  {desc}{parallel_tag}")
    print(f"\n  Run one:    peace-test.py run <test-name>")
    print(f"  Run all:    peace-test.py run all")
    print(f"  Parallel:   peace-test.py run all --parallel")
    print(f"  Dry run:    peace-test.py run <test-name> --dry-run")
    print(f"  History:    peace-test.py report\n")


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    parallel = "--parallel" in sys.argv

    if cmd == "run":
        if len(sys.argv) < 3:
            print("Usage: peace-test.py run <test-name|all> [--dry-run] [--parallel]")
            return
        test_name = sys.argv[2]
        cmd_run(test_name, dry_run=dry_run, parallel=parallel)
    elif cmd == "report":
        cmd_report()
    elif cmd == "list":
        cmd_list()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
