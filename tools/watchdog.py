#!/usr/bin/env python3
"""
watchdog.py — Automated Incident Response Wiring for Kingdom OS

Monitors security events and fleet canaries. When threats are detected,
escalates through HIVE and triggers PEACE actions automatically.

Escalation Rules:
  - Canary tripped on ANY VPS       → HIVE alert + auto-PEACE halt
  - KOS critical check failure       → HIVE alert + log escalation
  - File integrity violation          → HIVE alert + auto-PEACE halt
  - Multiple high events in 10 min   → HIVE alert (surge warning)

CLI:
    watchdog.py check                One-shot: check all sources, escalate if needed
    watchdog.py check --dry-run      Show what WOULD happen without acting
    watchdog.py watch                Continuous: poll every 60 seconds
    watchdog.py watch --dry-run      Continuous dry-run mode
    watchdog.py status               Show current alert state
"""

import json
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/love-unlimited"))
SECURITY = LOVE / "security"
EVENTS_FILE = SECURITY / "events.jsonl"
PEACE_STATE = SECURITY / "peace-state.json"
WATCHDOG_STATE = SECURITY / "watchdog-state.json"
HIVE_PY = LOVE / "hive" / "hive.py"
PEACE_PY = LOVE / "tools" / "peace.py"
HIVE_INSTANCE_FILE = Path.home() / ".love" / "hive" / "instance"

# ── Fleet Nodes ──────────────────────────────────────────────────────────────

FLEET_NODES = {
    "forge":  "89.167.84.100",
    "lark":   "89.167.95.165",
    "sentry": "135.181.28.252",
    "patch":  "65.109.11.26",
    "sage":   "204.168.140.12",
}

SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]

# ── Colors (matching kos.py / peace.py) ──────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# ── Escalation thresholds ────────────────────────────────────────────────────

HIGH_SURGE_WINDOW_MINUTES = 10
HIGH_SURGE_THRESHOLD = 3
POLL_INTERVAL_SECONDS = 60

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


def get_instance_id():
    if HIVE_INSTANCE_FILE.exists():
        try:
            return HIVE_INSTANCE_FILE.read_text().strip()
        except OSError:
            pass
    return os.environ.get("HIVE_INSTANCE", "unknown")


def run_cmd(cmd, timeout=15):
    """Run shell command, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1, ""


def log_event(event_type, severity, message, details=None):
    """Append to security event log (same format as peace.py / kos.py)."""
    entry = {
        "ts": now_iso(),
        "type": event_type,
        "severity": severity,
        "message": message,
        "source": "watchdog",
    }
    if details:
        entry["details"] = details
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Watchdog State ───────────────────────────────────────────────────────────


def load_watchdog_state():
    return load_json(WATCHDOG_STATE, {
        "last_check": None,
        "last_events_offset": 0,
        "active_alerts": [],
        "total_escalations": 0,
        "total_halts": 0,
    })


def save_watchdog_state(state):
    save_json(WATCHDOG_STATE, state)


# ── HIVE Integration ─────────────────────────────────────────────────────────


def hive_alert(message, dry_run=False):
    """Send alert to HIVE #alerts channel."""
    instance = get_instance_id()
    full_msg = f"[WATCHDOG] {instance}: {message}"
    if dry_run:
        print(f"  {DIM}[dry-run] HIVE alert: {full_msg}{NC}")
        return True
    if not HIVE_PY.exists():
        print(f"  {YELLOW}HIVE unavailable (hive.py not found){NC}")
        return False
    rc, _ = run_cmd(
        f'cd {LOVE} && python3 {HIVE_PY} send alerts "{full_msg}" 2>/dev/null',
        timeout=15,
    )
    return rc == 0


# ── PEACE Integration ────────────────────────────────────────────────────────


def peace_snapshot(dry_run=False):
    """Create state snapshot before halt."""
    if dry_run:
        print(f"  {DIM}[dry-run] Would create PEACE snapshot{NC}")
        return True
    if not PEACE_PY.exists():
        print(f"  {YELLOW}peace.py not found — skipping snapshot{NC}")
        return False
    rc, out = run_cmd(f'cd {LOVE} && python3 {PEACE_PY} snapshot 2>/dev/null', timeout=30)
    return rc == 0


def peace_halt(reason, dry_run=False):
    """Trigger PEACE halt protocol."""
    if dry_run:
        print(f"  {DIM}[dry-run] Would trigger PEACE halt: {reason}{NC}")
        return True
    if not PEACE_PY.exists():
        print(f"  {YELLOW}peace.py not found — cannot halt{NC}")
        return False
    rc, _ = run_cmd(
        f'cd {LOVE} && python3 {PEACE_PY} halt --reason "{reason}" 2>/dev/null',
        timeout=30,
    )
    return rc == 0


# ── Escalation Engine ────────────────────────────────────────────────────────


class Escalation:
    """Represents a pending escalation action."""

    def __init__(self, trigger, severity, message, hive_alert=True, peace_halt=False):
        self.trigger = trigger
        self.severity = severity
        self.message = message
        self.needs_hive = hive_alert
        self.needs_halt = peace_halt
        self.ts = now_iso()

    def to_dict(self):
        return {
            "trigger": self.trigger,
            "severity": self.severity,
            "message": self.message,
            "hive_alert": self.needs_hive,
            "peace_halt": self.needs_halt,
            "ts": self.ts,
        }

    def __repr__(self):
        halt_tag = " +HALT" if self.needs_halt else ""
        return f"[{self.severity.upper()}] {self.trigger}: {self.message}{halt_tag}"


def execute_escalations(escalations, dry_run=False):
    """Execute a list of escalation actions. Returns (alerts_sent, halts_triggered)."""
    if not escalations:
        return 0, 0

    alerts_sent = 0
    halts_triggered = 0
    needs_halt = any(e.needs_halt for e in escalations)

    # 1. Send HIVE alerts
    for esc in escalations:
        if esc.needs_hive:
            severity_tag = f"[{esc.severity.upper()}]"
            sent = hive_alert(f"{severity_tag} {esc.message}", dry_run=dry_run)
            if sent:
                alerts_sent += 1
            # Log the escalation event
            if not dry_run:
                log_event("watchdog_escalation", esc.severity, esc.message,
                          details=esc.trigger)

    # 2. Snapshot + Halt if any escalation requires it
    if needs_halt:
        halt_reasons = [e.message for e in escalations if e.needs_halt]
        reason = "; ".join(halt_reasons)

        print(f"\n  {RED}{BOLD}AUTO-HALT TRIGGERED{NC}")
        print(f"  Reason: {reason}\n")

        # Snapshot first
        print(f"  Creating pre-halt snapshot...")
        peace_snapshot(dry_run=dry_run)

        # Then halt
        print(f"  Initiating PEACE halt...")
        halted = peace_halt(reason, dry_run=dry_run)
        if halted:
            halts_triggered += 1
            if not dry_run:
                log_event("watchdog_auto_halt", "critical",
                          f"Auto-halt triggered: {reason}")

    return alerts_sent, halts_triggered


# ── Check: Fleet Canary Alerts ───────────────────────────────────────────────


def check_fleet_canaries():
    """SSH to all VPS nodes, check /root/.canary-alert for entries.
    Returns list of Escalation objects."""
    escalations = []

    def check_node(name, ip):
        """Check a single node for canary alerts."""
        try:
            r = subprocess.run(
                ["ssh"] + SSH_OPTS + [f"root@{ip}",
                 "cat /root/.canary-alert 2>/dev/null; echo '---WATCHDOG-SEP---'; "
                 "stat -c %Y /root/.canary-alert 2>/dev/null || echo 'NOFILE'"],
                capture_output=True, text=True, timeout=15,
            )
            return name, r.returncode, r.stdout.strip()
        except (subprocess.TimeoutExpired, Exception):
            return name, -1, ""

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(check_node, n, ip): n for n, ip in FLEET_NODES.items()}
        results = {}
        for f in as_completed(futures):
            name, rc, out = f.result()
            results[name] = (rc, out)

    for name in sorted(FLEET_NODES):
        rc, out = results.get(name, (-1, ""))

        if rc == -1:
            # Node unreachable — not necessarily a canary trip, but notable
            continue

        # Parse output
        parts = out.split("---WATCHDOG-SEP---")
        alert_content = parts[0].strip() if parts else ""
        mtime_str = parts[1].strip() if len(parts) > 1 else "NOFILE"

        if alert_content and mtime_str != "NOFILE":
            # Canary alert file exists and has content
            escalations.append(Escalation(
                trigger="fleet_canary",
                severity="critical",
                message=f"Canary tripped on {name} ({FLEET_NODES[name]}): {alert_content[:200]}",
                hive_alert=True,
                peace_halt=True,
            ))

    return escalations


# ── Check: Security Events (tail) ───────────────────────────────────────────


def check_security_events(last_offset=0):
    """Read new events from events.jsonl since last_offset.
    Returns (escalations, new_offset)."""
    escalations = []

    if not EVENTS_FILE.exists():
        return escalations, 0

    new_events = []
    current_offset = 0

    with open(EVENTS_FILE, "r") as f:
        for i, line in enumerate(f):
            current_offset = i + 1
            if i < last_offset:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Skip events from watchdog itself to avoid feedback loops
            if event.get("source") == "watchdog":
                continue
            new_events.append(event)

    # Rule 1: KOS critical check failure → HIVE alert + log escalation
    for ev in new_events:
        if ev.get("severity") == "critical" and ev.get("type") == "check_failed":
            escalations.append(Escalation(
                trigger="kos_critical",
                severity="critical",
                message=f"KOS critical failure: {ev.get('message', 'unknown')}",
                hive_alert=True,
                peace_halt=False,
            ))

    # Rule 2: File integrity violation → HIVE alert + auto-PEACE halt
    for ev in new_events:
        if ev.get("type") in ("integrity_violation", "integrity_drift"):
            escalations.append(Escalation(
                trigger="integrity_violation",
                severity="critical",
                message=f"File integrity violation: {ev.get('message', 'unknown')}",
                hive_alert=True,
                peace_halt=True,
            ))

    # Rule 3: Emergency halt from other sources (re-alert via HIVE)
    for ev in new_events:
        if ev.get("type") == "emergency_halt" and ev.get("source") != "watchdog":
            escalations.append(Escalation(
                trigger="external_halt",
                severity="critical",
                message=f"External halt detected: {ev.get('message', 'unknown')}",
                hive_alert=True,
                peace_halt=False,  # Already halted
            ))

    # Rule 4: Fleet canary trip logged by peace.py
    for ev in new_events:
        if ev.get("type") == "fleet_canary_trip":
            escalations.append(Escalation(
                trigger="fleet_canary_event",
                severity="critical",
                message=f"Fleet canary trip event: {ev.get('message', 'unknown')}",
                hive_alert=True,
                peace_halt=True,
            ))

    # Rule 5: High-severity surge (3+ high events in 10 minutes)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=HIGH_SURGE_WINDOW_MINUTES)
    high_events_in_window = []
    for ev in new_events:
        if ev.get("severity") == "high":
            try:
                ev_ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
                if ev_ts >= window_start:
                    high_events_in_window.append(ev)
            except (KeyError, ValueError):
                pass

    if len(high_events_in_window) >= HIGH_SURGE_THRESHOLD:
        messages = [e.get("message", "?")[:60] for e in high_events_in_window[:5]]
        escalations.append(Escalation(
            trigger="high_surge",
            severity="high",
            message=f"Surge: {len(high_events_in_window)} high-severity events in "
                    f"{HIGH_SURGE_WINDOW_MINUTES}m — {'; '.join(messages)}",
            hive_alert=True,
            peace_halt=False,
        ))

    return escalations, current_offset


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_check(dry_run=False):
    """One-shot: check all sources, escalate if needed."""
    print(f"\n{BOLD}  Watchdog Check{NC}  {DIM}{now_iso()}{NC}\n")

    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE — no actions will be taken{NC}\n")

    state = load_watchdog_state()
    all_escalations = []

    # 1. Check fleet canaries
    print(f"  {CYAN}[1/2]{NC} Checking fleet canary alerts...")
    try:
        fleet_escs = check_fleet_canaries()
        if fleet_escs:
            print(f"    {RED}{len(fleet_escs)} canary alert(s) found{NC}")
            for e in fleet_escs:
                print(f"      {RED}●{NC} {e.message[:100]}")
        else:
            print(f"    {GREEN}No canary alerts{NC}")
        all_escalations.extend(fleet_escs)
    except Exception as ex:
        print(f"    {YELLOW}Fleet check error: {ex}{NC}")

    # 2. Check security events
    print(f"  {CYAN}[2/2]{NC} Scanning security events...")
    try:
        last_offset = state.get("last_events_offset", 0)
        event_escs, new_offset = check_security_events(last_offset)
        new_events_count = new_offset - last_offset
        if event_escs:
            print(f"    {RED}{len(event_escs)} escalation(s) from {new_events_count} new event(s){NC}")
            for e in event_escs:
                print(f"      {RED}●{NC} {e}")
        else:
            print(f"    {GREEN}{new_events_count} new event(s), no escalations{NC}")

        # Update offset even in dry-run (just tracking read position)
        state["last_events_offset"] = new_offset
        all_escalations.extend(event_escs)
    except Exception as ex:
        print(f"    {YELLOW}Events check error: {ex}{NC}")
        new_offset = state.get("last_events_offset", 0)

    # 3. Execute escalations
    if all_escalations:
        print(f"\n  {RED}{BOLD}Escalating {len(all_escalations)} incident(s):{NC}\n")
        alerts_sent, halts_triggered = execute_escalations(all_escalations, dry_run=dry_run)

        # Update state
        state["total_escalations"] = state.get("total_escalations", 0) + alerts_sent
        state["total_halts"] = state.get("total_halts", 0) + halts_triggered
        state["active_alerts"] = [e.to_dict() for e in all_escalations]

        print(f"\n  Escalation summary: {alerts_sent} alert(s) sent", end="")
        if halts_triggered:
            print(f", {RED}{halts_triggered} halt(s) triggered{NC}")
        else:
            print()
    else:
        print(f"\n  {GREEN}{BOLD}All clear — no escalations needed{NC}")
        state["active_alerts"] = []

    state["last_check"] = now_iso()
    if not dry_run:
        save_watchdog_state(state)

    print()
    return all_escalations


def cmd_watch(dry_run=False):
    """Continuous: poll every 60 seconds. Suitable for launchd daemon."""
    print(f"\n{BOLD}  Watchdog — Continuous Mode{NC}")
    print(f"  Polling every {POLL_INTERVAL_SECONDS}s")
    if dry_run:
        print(f"  {YELLOW}DRY-RUN MODE{NC}")
    print(f"  Press Ctrl+C to stop.\n")

    log_event("watchdog_start", "low", "Watchdog continuous mode started")

    try:
        while True:
            try:
                escalations = cmd_check(dry_run=dry_run)
                if escalations:
                    # Log a summary
                    halts = sum(1 for e in escalations if e.needs_halt)
                    log_event("watchdog_cycle", "high" if halts else "medium",
                              f"Cycle: {len(escalations)} escalation(s), {halts} halt(s)")
            except Exception as ex:
                print(f"  {RED}Cycle error: {ex}{NC}")
                log_event("watchdog_error", "high", f"Cycle error: {ex}")

            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print(f"\n  {DIM}Watchdog stopped by user.{NC}")
        log_event("watchdog_stop", "low", "Watchdog stopped by user")


def cmd_status():
    """Show current alert state."""
    print(f"\n{BOLD}  Watchdog Status{NC}\n")

    state = load_watchdog_state()

    last_check = state.get("last_check", "never")
    total_esc = state.get("total_escalations", 0)
    total_halts = state.get("total_halts", 0)
    active = state.get("active_alerts", [])

    # Overall status
    if active:
        has_halt = any(a.get("peace_halt") for a in active)
        if has_halt:
            status_icon = f"{RED}{BOLD}HALTED{NC}"
        else:
            status_icon = f"{YELLOW}{BOLD}ALERT{NC}"
    else:
        status_icon = f"{GREEN}{BOLD}CLEAR{NC}"

    print(f"  Status:       {status_icon}")
    print(f"  Last check:   {last_check}")
    print(f"  Escalations:  {total_esc} total")
    print(f"  Auto-halts:   {total_halts} total")

    # PEACE state
    peace = load_json(PEACE_STATE)
    peace_status = peace.get("status", "unknown")
    if peace_status == "halted":
        print(f"  PEACE state:  {RED}HALTED{NC} — {peace.get('reason', '?')}")
    else:
        score = peace.get("score")
        if score is not None:
            color = GREEN if score >= 80 else YELLOW if score >= 60 else RED
            print(f"  PEACE state:  {color}{score:.0f}%{NC}")
        else:
            print(f"  PEACE state:  {DIM}not scored{NC}")

    # Active alerts
    if active:
        print(f"\n  {YELLOW}Active Alerts ({len(active)}):{NC}")
        for a in active:
            sev = a.get("severity", "?")
            sev_color = RED if sev == "critical" else YELLOW if sev == "high" else CYAN
            halt_tag = f" {RED}+HALT{NC}" if a.get("peace_halt") else ""
            print(f"    {sev_color}[{sev.upper()}]{NC} {a.get('trigger', '?')}: "
                  f"{a.get('message', '?')[:80]}{halt_tag}")
            print(f"           {DIM}{a.get('ts', '?')}{NC}")
    else:
        print(f"\n  {GREEN}No active alerts{NC}")

    # Fleet nodes summary
    print(f"\n  Fleet nodes monitored: {', '.join(sorted(FLEET_NODES.keys()))}")
    print(f"  Events file: {EVENTS_FILE}")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if cmd == "check":
        cmd_check(dry_run=dry_run)
    elif cmd == "watch":
        cmd_watch(dry_run=dry_run)
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
