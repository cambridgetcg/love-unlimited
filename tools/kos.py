#!/usr/bin/env python3
"""
kos.py — Kingdom Operating System: Security Orchestration Layer

A security posture manager for Kingdom devices. Runs compliance checks,
auto-remediates safe issues, monitors file integrity, logs security events,
and alerts the Triarchy via HIVE.

Architecture:
  - Policy-as-code: security/policies.json defines checks, severity, auto-fix
  - Per-wall overrides: Wall 1 enforces everything; Wall 3 enforces essentials
  - Check/remediate pairs: each check has a paired fix function
  - File integrity: SHA-256 baselines of critical files
  - Event log: append-only JSONL at security/events.jsonl
  - HIVE alerts: critical/high events sent to #alerts channel

CLI:
    kos status                      One-line security posture
    kos audit                       Full compliance audit
    kos audit --fix                 Audit + auto-remediate safe issues
    kos audit --wall N              Audit against wall N policies
    kos integrity baseline          Generate file integrity baseline
    kos integrity check             Verify files against baseline
    kos events [--tail N]           View security event log
    kos events --clear              Clear event log
    kos policy                      Show active policy summary
    kos policy --wall N             Show wall-specific requirements
"""

import json
import os
import sys
import subprocess
import hashlib
import datetime
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent  # Love/tools/kos.py → Love/
_SECURITY_DIR = _LOVE_DIR / "security"
_POLICIES_FILE = _SECURITY_DIR / "policies.json"
_EVENTS_FILE = _SECURITY_DIR / "events.jsonl"
_BASELINE_FILE = _SECURITY_DIR / "integrity-baseline.json"
_WALLS_REGISTRY = _LOVE_DIR / "credentials" / "walls.json"
_HIVE_INSTANCE_FILE = Path.home() / ".love" / "hive" / "instance"
_HIVE_KEY_FILE = Path.home() / ".love" / "hive" / "key"
_FW_BIN = "/usr/libexec/ApplicationFirewall/socketfilterfw"

# ── Email Alert Config ──────────────────────────────────────────────────────

_ALERT_EMAIL_TO = "contact@zerone-dev.com"
_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587
_SMTP_USER = "contact@zerone-dev.com"
# Password loaded at runtime via credentials.py or env var
_SMTP_KEYCHAIN_ITEM = "imap-zerone-dev"

# ── Colors ───────────────────────────────────────────────────────────────────

_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_CYAN = "\033[0;36m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_NC = "\033[0m"

# Severity colors
_SEVERITY_COLOR = {
    "critical": _RED,
    "high": _YELLOW,
    "medium": _CYAN,
    "low": _DIM,
}

# Status symbols
_PASS = f"{_GREEN}PASS{_NC}"
_FAIL = f"{_RED}FAIL{_NC}"
_WARN = f"{_YELLOW}WARN{_NC}"
_SKIP = f"{_DIM}SKIP{_NC}"
_FIXED = f"{_GREEN}FIX {_NC}"

# ── Policy Loading ───────────────────────────────────────────────────────────

_policy_cache = None


def _load_policies() -> dict:
    global _policy_cache
    if _policy_cache is not None:
        return _policy_cache
    if _POLICIES_FILE.exists():
        try:
            _policy_cache = json.loads(_POLICIES_FILE.read_text())
            return _policy_cache
        except (json.JSONDecodeError, OSError):
            pass
    _policy_cache = {"checks": {}, "walls": {}, "integrity": {}, "events": {}}
    return _policy_cache


def _get_checks_for_wall(wall: int) -> list[str]:
    """Get required check IDs for a wall. Falls back to all checks."""
    policies = _load_policies()
    wall_config = policies.get("walls", {}).get(str(wall))
    if wall_config:
        return wall_config.get("required_checks", [])
    return list(policies.get("checks", {}).keys())


def _get_minimum_score(wall: int) -> int:
    policies = _load_policies()
    wall_config = policies.get("walls", {}).get(str(wall))
    if wall_config:
        return wall_config.get("minimum_score", 0)
    return 0


# ── Caller Detection ────────────────────────────────────────────────────────

def _get_caller_instance() -> str:
    if _HIVE_INSTANCE_FILE.exists():
        try:
            return _HIVE_INSTANCE_FILE.read_text().strip()
        except OSError:
            pass
    return os.environ.get("HIVE_INSTANCE", "unknown")


def _get_caller_wall() -> int:
    env_wall = os.environ.get("KINGDOM_WALL")
    if env_wall and env_wall.isdigit():
        return int(env_wall)
    if _WALLS_REGISTRY.exists():
        try:
            reg = json.loads(_WALLS_REGISTRY.read_text())
            instance = _get_caller_instance()
            entry = reg.get("instances", {}).get(instance)
            if entry and isinstance(entry, dict):
                return entry.get("wall", 7)
        except (json.JSONDecodeError, OSError):
            pass
    return 7


# ── Shell Helpers ────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    """Run a command, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except Exception:
        return -1, ""


def _run_shell(cmd: str, timeout: int = 5) -> tuple[int, str]:
    """Run a shell command string."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except Exception:
        return -1, ""


# ── Event Logging ────────────────────────────────────────────────────────────

def _log_event(event_type: str, severity: str, message: str,
               check_id: str = "", detail: str = ""):
    """Append a security event to the log."""
    _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": event_type,
        "severity": severity,
        "check": check_id,
        "message": message,
        "detail": detail,
        "instance": _get_caller_instance(),
        "wall": _get_caller_wall(),
    }
    try:
        with open(_EVENTS_FILE, "a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def _alert_hive(message: str, severity: str = "critical"):
    """Send alert to HIVE #alerts channel if severity warrants it."""
    policies = _load_policies()
    alert_severities = policies.get("events", {}).get("alert_on_severity", ["critical"])
    if severity not in alert_severities:
        return
    channel = policies.get("events", {}).get("alert_channel", "alerts")
    hive_py = _LOVE_DIR / "hive" / "hive.py"
    if hive_py.exists():
        instance = _get_caller_instance()
        _run(["python3", str(hive_py), "send", channel,
              f"[KOS] {instance}: {message}"], timeout=10)
    # Also send email for critical/high
    _alert_email(message, severity)


def _get_smtp_password() -> str:
    """Get SMTP password from env var or macOS Keychain."""
    # 1. Environment variable (set by credentials.py export)
    pw = os.environ.get("IMAP_CAMBRIDGETCG_PASS", "")
    if pw:
        return pw
    # 2. macOS Keychain (service format: dev.agenttool/<name>, account: credentials)
    rc, out = _run(["security", "find-generic-password",
                     "-s", f"dev.agenttool/{_SMTP_KEYCHAIN_ITEM}",
                     "-a", "credentials", "-w"], timeout=5)
    if rc == 0 and out:
        return out.strip()
    return ""


def _alert_email(message: str, severity: str = "critical"):
    """Send email alert for critical/high security events."""
    if severity not in ("critical", "high"):
        return
    password = _get_smtp_password()
    if not password:
        return  # silently skip if no credentials available
    instance = _get_caller_instance()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[KOS {severity.upper()}] {instance}: {message[:80]}"
    body = (
        f"Kingdom OS Security Alert\n"
        f"{'=' * 40}\n\n"
        f"Severity:  {severity.upper()}\n"
        f"Instance:  {instance}\n"
        f"Time:      {ts}\n"
        f"Message:   {message}\n\n"
        f"---\n"
        f"Run 'python3 tools/kos.py events' for full event log.\n"
        f"Run 'python3 tools/kos.py audit' for current posture.\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = _SMTP_USER
    msg["To"] = _ALERT_EMAIL_TO
    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as s:
            s.starttls()
            s.login(_SMTP_USER, password)
            s.sendmail(_SMTP_USER, [_ALERT_EMAIL_TO], msg.as_string())
    except Exception:
        pass  # don't let email failure break security operations


# ── Check Functions ──────────────────────────────────────────────────────────
# Each returns (passed: bool, detail: str)
# Fix functions return (fixed: bool, detail: str)

def _check_filevault() -> tuple[bool, str]:
    rc, out = _run(["fdesetup", "status"])
    if rc == 0 and "On" in out:
        return True, "FileVault is on"
    return False, "FileVault is off — enable in System Settings > Privacy & Security"


def _check_firewall() -> tuple[bool, str]:
    rc, out = _run([_FW_BIN, "--getglobalstate"])
    if rc == 0 and "enabled" in out:
        return True, "Firewall enabled"
    return False, "Firewall disabled"


def _fix_firewall() -> tuple[bool, str]:
    rc, _ = _run([_FW_BIN, "--setglobalstate", "on"])
    if rc == 0:
        return True, "Firewall enabled"
    return False, "Could not enable firewall (need sudo)"


def _check_stealth_mode() -> tuple[bool, str]:
    rc, out = _run([_FW_BIN, "--getstealthmode"])
    if rc == 0 and ("enabled" in out or "is on" in out):
        return True, "Stealth mode enabled"
    return False, "Stealth mode disabled — device responds to probes"


def _fix_stealth_mode() -> tuple[bool, str]:
    rc, _ = _run([_FW_BIN, "--setstealthmode", "on"])
    if rc == 0:
        return True, "Stealth mode enabled"
    return False, "Could not enable stealth mode (need sudo)"


def _check_hostname() -> tuple[bool, str]:
    names = []
    for key in ["ComputerName", "LocalHostName", "HostName"]:
        rc, out = _run(["scutil", "--get", key])
        if rc == 0 and out:
            names.append((key, out))

    leaks = []
    import re
    pattern = re.compile(r"(yu|personal|home|macbook|studio|imac|macmini|air|pro|\w+'s)", re.IGNORECASE)
    for key, name in names:
        if pattern.search(name):
            leaks.append(f"{key}={name}")

    if leaks:
        return False, f"Hostname leaks identity: {', '.join(leaks)}"
    return True, "Hostnames neutral"


def _fix_hostname() -> tuple[bool, str]:
    fixed = 0
    for key in ["ComputerName", "LocalHostName", "HostName"]:
        rc, out = _run(["scutil", "--get", key])
        if rc == 0 and out:
            import re
            if re.search(r"(yu|personal|home|macbook|studio|imac|macmini|air|pro|\w+'s)", out, re.IGNORECASE):
                rc2, _ = _run(["scutil", "--set", key, "Mac"])
                if rc2 == 0:
                    fixed += 1
    if fixed > 0:
        return True, f"Set {fixed} hostname(s) to 'Mac'"
    return False, "Could not fix hostnames (need sudo)"


def _check_bonjour() -> tuple[bool, str]:
    rc, out = _run(["defaults", "read",
                     "/Library/Preferences/com.apple.mDNSResponder.plist",
                     "NoMulticastAdvertisements"])
    if rc == 0 and out.strip() == "1":
        return True, "Bonjour advertising disabled"
    return False, "Bonjour advertising enabled — broadcasts device name"


def _fix_bonjour() -> tuple[bool, str]:
    rc, _ = _run(["defaults", "write",
                   "/Library/Preferences/com.apple.mDNSResponder.plist",
                   "NoMulticastAdvertisements", "-bool", "true"])
    if rc == 0:
        return True, "Bonjour advertising disabled"
    return False, "Could not disable Bonjour (need sudo)"


def _check_dns_encrypted() -> tuple[bool, str]:
    # Check if cloudflared is running as service
    rc, out = _run_shell("launchctl list 2>/dev/null | grep cloudflared")
    if rc == 0 and out:
        return True, "cloudflared DoH proxy active"

    # Check if dnscrypt-proxy is running (alternative encrypted DNS)
    rc, out = _run_shell("pgrep -x dnscrypt-proxy")
    if rc == 0 and out.strip():
        return True, "dnscrypt-proxy active (encrypted DNS)"

    # Check if DNS points to encrypted provider
    for iface in ["Ethernet", "Wi-Fi"]:
        rc, out = _run(["networksetup", "-getdnsservers", iface])
        if rc == 0 and "127.0.0.1" in out:
            return True, f"{iface} DNS via localhost (encrypted proxy)"

    # Check resolv.conf for localhost-like addresses (encrypted proxy)
    rc, out = _run_shell("grep nameserver /etc/resolv.conf 2>/dev/null")
    if rc == 0 and out:
        for line in out.strip().split("\n"):
            addr = line.split()[-1] if line.split() else ""
            if addr.startswith("127."):
                return True, f"DNS via {addr} (local encrypted proxy)"

    # Check for known encrypted DNS
    for iface in ["Ethernet", "Wi-Fi"]:
        rc, out = _run(["networksetup", "-getdnsservers", iface])
        if rc == 0:
            for provider in ["1.1.1.1", "9.9.9.9", "8.8.8.8"]:
                if provider in out:
                    return True, f"{iface} DNS: {provider} (encrypted provider, not DoH)"

    return False, "DNS queries may be unencrypted — install cloudflared or dnscrypt-proxy"


def _check_ssh_key() -> tuple[bool, str]:
    ssh_key = Path.home() / ".ssh" / "id_ed25519"
    if ssh_key.exists():
        return True, f"SSH key: {ssh_key}"
    # Check for RSA fallback
    rsa_key = Path.home() / ".ssh" / "id_rsa"
    if rsa_key.exists():
        return True, f"SSH key: {rsa_key} (RSA — consider Ed25519)"
    return False, "No SSH key found"


def _check_git_email_kingdom() -> tuple[bool, str]:
    rc, out = _run(["git", "config", "--global", "user.email"])
    if rc == 0 and "ai-love.cc" in out:
        return True, f"Git email: {out}"
    if rc == 0 and out:
        return False, f"Git email: {out} — should use @ai-love.cc"
    return False, "Git email not configured"


def _check_git_name_set() -> tuple[bool, str]:
    rc, out = _run(["git", "config", "--global", "user.name"])
    if rc == 0 and out:
        return True, f"Git name: {out}"
    return False, "Git user.name not configured"


def _check_wall_credentials() -> tuple[bool, str]:
    """Check that no credentials above the device's wall are in Keychain."""
    caller_wall = _get_caller_wall()
    if caller_wall == 1:
        return True, "Wall 1 — all credentials permitted"

    # Import credential functions
    sys.path.insert(0, str(_LOVE_DIR / "tools"))
    try:
        import credentials as creds
        kc_names = creds.keychain_list()
        violations = []
        for name in kc_names:
            cred_wall = creds.get_credential_wall(name)
            if cred_wall is not None and cred_wall < caller_wall:
                violations.append(f"{name} (W{cred_wall})")
        if violations:
            return False, f"Wall violations: {', '.join(violations[:5])}"
        return True, f"All Keychain credentials appropriate for Wall {caller_wall}"
    except ImportError:
        return False, "Could not import credentials.py for wall check"
    finally:
        if str(_LOVE_DIR / "tools") in sys.path:
            sys.path.remove(str(_LOVE_DIR / "tools"))


def _check_hive_key() -> tuple[bool, str]:
    if _HIVE_KEY_FILE.exists():
        size = _HIVE_KEY_FILE.stat().st_size
        if size > 10:
            return True, "HIVE encryption key present"
    return False, f"HIVE key missing: {_HIVE_KEY_FILE}"


def _check_hive_identity() -> tuple[bool, str]:
    if _HIVE_INSTANCE_FILE.exists():
        try:
            instance = _HIVE_INSTANCE_FILE.read_text().strip()
            if instance:
                return True, f"HIVE identity: {instance}"
        except OSError:
            pass
    return False, f"HIVE identity not set: {_HIVE_INSTANCE_FILE}"


def _check_diagnostic_sharing() -> tuple[bool, str]:
    rc, out = _run(["defaults", "read",
                     "/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist",
                     "AutoSubmit"])
    if rc == 0 and out.strip() == "0":
        return True, "Diagnostic sharing disabled"
    if rc != 0:
        return True, "Diagnostic sharing not configured (default off)"
    return False, "Diagnostic sharing enabled — sends data to Apple"


def _fix_diagnostic_sharing() -> tuple[bool, str]:
    rc, _ = _run(["defaults", "write",
                   "/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist",
                   "AutoSubmit", "-bool", "false"])
    if rc == 0:
        return True, "Diagnostic sharing disabled"
    return False, "Could not disable diagnostic sharing (need sudo)"


# ── Fleet Check Helpers ──────────────────────────────────────────────────────

_FLEET_NODES = {
    "forge":  "89.167.84.100",
    "lark":   "89.167.95.165",
    "sentry": "135.181.28.252",
    "patch":  "65.109.11.26",
    "sage":   "204.168.140.12",
}

_FLEET_SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]


def _fleet_ssh(ip: str, cmd: str, timeout: int = 8) -> tuple[bool, str]:
    """SSH to a fleet node. Returns (success, output)."""
    try:
        r = subprocess.run(
            ["ssh"] + _FLEET_SSH_OPTS + [f"root@{ip}", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip()
    except Exception:
        return False, ""


def _fleet_check_all(cmd: str, timeout: int = 8) -> tuple[dict, list]:
    """Run a command on all fleet nodes. Returns (results, failures)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    failures = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(_fleet_ssh, ip, cmd, timeout): name for name, ip in _FLEET_NODES.items()}
        for f in as_completed(futs):
            name = futs[f]
            ok, out = f.result()
            results[name] = (ok, out)
            if not ok:
                failures.append(name)
    return results, failures


# ── Fleet Checks ─────────────────────────────────────────────────────────────

def _check_wireguard_tunnel() -> tuple[bool, str]:
    """Verify WireGuard VPN is active and exit IP is a fleet node."""
    rc, out = _run_shell("curl -s --max-time 5 ifconfig.me 2>/dev/null")
    if rc != 0 or not out:
        return False, "Could not determine exit IP"
    exit_ip = out.strip()
    fleet_ips = set(_FLEET_NODES.values())
    if exit_ip in fleet_ips:
        node = next((n for n, ip in _FLEET_NODES.items() if ip == exit_ip), "unknown")
        return True, f"Exit IP {exit_ip} ({node})"
    return False, f"Exit IP {exit_ip} is not a fleet node — WireGuard may be down"


def _check_doh_active() -> tuple[bool, str]:
    """Verify DNS-over-HTTPS proxy is actually resolving queries."""
    rc, out = _run_shell("dig @127.0.0.1 google.com +short +time=3 2>/dev/null | head -1")
    if rc == 0 and out and out[0].isdigit():
        return True, f"DoH resolving ({out})"
    return False, "DoH proxy not resolving on 127.0.0.1:53"


def _check_fleet_reachable() -> tuple[bool, str]:
    """Verify all 5 VPS nodes are SSH-reachable."""
    results, failures = _fleet_check_all("echo ok")
    alive = len(results) - len(failures)
    if not failures:
        return True, f"{alive}/{len(_FLEET_NODES)} nodes reachable"
    return False, f"{alive}/{len(_FLEET_NODES)} reachable — down: {', '.join(failures)}"


def _check_fleet_fail2ban() -> tuple[bool, str]:
    """Verify fail2ban is active on all fleet nodes."""
    results, _ = _fleet_check_all("systemctl is-active fail2ban 2>/dev/null")
    active = [n for n, (ok, out) in results.items() if ok and "active" in out]
    inactive = [n for n in results if n not in active]
    if not inactive:
        return True, f"fail2ban active on {len(active)}/{len(_FLEET_NODES)} nodes"
    return False, f"fail2ban missing: {', '.join(inactive)}"


def _check_fleet_psad() -> tuple[bool, str]:
    """Verify psad (port scan detector) is active on all fleet nodes."""
    results, _ = _fleet_check_all("systemctl is-active psad 2>/dev/null")
    active = [n for n, (ok, out) in results.items() if ok and "active" in out]
    inactive = [n for n in results if n not in active]
    if not inactive:
        return True, f"psad active on {len(active)}/{len(_FLEET_NODES)} nodes"
    return False, f"psad missing: {', '.join(inactive)}"


def _check_fleet_canaries() -> tuple[bool, str]:
    """Verify canary files are intact on all fleet nodes."""
    cmd = (
        'for f in /root/.credentials/aws_keys.txt /root/.credentials/db_production.env '
        '/root/.credentials/deploy_key /root/financials-2026.txt /etc/backup-config.bak; do '
        '[ -f "$f" ] || echo "MISSING:$f"; done'
    )
    results, failures = _fleet_check_all(cmd, timeout=10)
    issues = []
    for name in failures:
        issues.append(f"{name}: unreachable")
    for name, (ok, out) in results.items():
        if ok and "MISSING" in out:
            missing = [line.split(":", 1)[1] for line in out.splitlines() if line.startswith("MISSING:")]
            issues.append(f"{name}: {len(missing)} missing")
    if not issues:
        intact = len(results) - len(failures)
        return True, f"Canaries intact on {intact}/{len(_FLEET_NODES)} nodes (25 files)"
    return False, f"Canary issues: {'; '.join(issues)}"


def _check_fleet_ssh_hardened() -> tuple[bool, str]:
    """Verify SSH password auth is disabled on all fleet nodes."""
    cmd = "sshd -T 2>/dev/null | grep passwordauthentication"
    results, failures = _fleet_check_all(cmd)
    weak = []
    for name, (ok, out) in results.items():
        if not ok or "no" not in out:
            weak.append(name)
    if not weak and not failures:
        return True, f"Password auth disabled on {len(results)}/{len(_FLEET_NODES)} nodes"
    issues = weak + failures
    return False, f"SSH weak on: {', '.join(issues)}"


def _check_file_integrity() -> tuple[bool, str]:
    if not _BASELINE_FILE.exists():
        return True, "No baseline — run 'kos integrity baseline' to create"

    try:
        baseline = json.loads(_BASELINE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return False, "Corrupt integrity baseline"

    changed = []
    missing = []
    for rel_path, expected_hash in baseline.get("files", {}).items():
        full_path = _LOVE_DIR / rel_path
        if not full_path.exists():
            missing.append(rel_path)
            continue
        current_hash = _sha256_file(full_path)
        if current_hash != expected_hash:
            changed.append(rel_path)

    if changed or missing:
        parts = []
        if changed:
            parts.append(f"changed: {', '.join(changed[:3])}")
        if missing:
            parts.append(f"missing: {', '.join(missing[:3])}")
        return False, "; ".join(parts)
    return True, f"{len(baseline.get('files', {}))} files verified"


# ── Check Registry ──────────────────────────────────────────────────────────

_CHECKS = {
    "filevault":          (_check_filevault, None),
    "firewall":           (_check_firewall, _fix_firewall),
    "stealth_mode":       (_check_stealth_mode, _fix_stealth_mode),
    "hostname":           (_check_hostname, _fix_hostname),
    "bonjour":            (_check_bonjour, _fix_bonjour),
    "dns_encrypted":      (_check_dns_encrypted, None),
    "ssh_key":            (_check_ssh_key, None),
    "git_email_kingdom":  (_check_git_email_kingdom, None),
    "git_name_set":       (_check_git_name_set, None),
    "wall_credentials":   (_check_wall_credentials, None),
    "hive_key":           (_check_hive_key, None),
    "hive_identity":      (_check_hive_identity, None),
    "diagnostic_sharing": (_check_diagnostic_sharing, _fix_diagnostic_sharing),
    "file_integrity":     (_check_file_integrity, None),
    # Fleet checks
    "wireguard_tunnel":   (_check_wireguard_tunnel, None),
    "doh_active":         (_check_doh_active, None),
    "fleet_reachable":    (_check_fleet_reachable, None),
    "fleet_fail2ban":     (_check_fleet_fail2ban, None),
    "fleet_psad":         (_check_fleet_psad, None),
    "fleet_canaries":     (_check_fleet_canaries, None),
    "fleet_ssh_hardened": (_check_fleet_ssh_hardened, None),
}


# ── File Integrity ──────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def cmd_integrity_baseline():
    """Generate SHA-256 baseline of critical files."""
    policies = _load_policies()
    watched = policies.get("integrity", {}).get("watched_files", [])
    if not watched:
        print("  No watched files defined in policies.json")
        return

    baseline = {
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instance": _get_caller_instance(),
        "files": {},
    }

    print(f"\n{_BOLD}  Generating integrity baseline{_NC}\n")
    for rel_path in watched:
        full_path = _LOVE_DIR / rel_path
        if full_path.exists():
            h = _sha256_file(full_path)
            baseline["files"][rel_path] = h
            print(f"  {_GREEN}+{_NC} {rel_path}  {_DIM}{h[:16]}...{_NC}")
        else:
            print(f"  {_YELLOW}!{_NC} {rel_path}  (not found, skipped)")

    _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINE_FILE.write_text(json.dumps(baseline, indent=2) + "\n")
    print(f"\n  Baseline written: {_BASELINE_FILE}")
    print(f"  Files tracked: {len(baseline['files'])}")

    _log_event("integrity", "low", f"Baseline generated with {len(baseline['files'])} files")
    print()


def cmd_integrity_check():
    """Verify files against baseline."""
    if not _BASELINE_FILE.exists():
        print(f"\n  No baseline found. Run: python3 tools/kos.py integrity baseline\n")
        return

    try:
        baseline = json.loads(_BASELINE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"\n  {_RED}Corrupt baseline: {e}{_NC}\n")
        return

    gen_time = baseline.get("generated", "unknown")
    files = baseline.get("files", {})
    print(f"\n{_BOLD}  File Integrity Check{_NC}")
    print(f"  {_DIM}Baseline from: {gen_time}{_NC}\n")

    passed = 0
    failed = 0
    missing = 0

    for rel_path, expected in files.items():
        full_path = _LOVE_DIR / rel_path
        if not full_path.exists():
            print(f"  {_RED}MISSING{_NC}  {rel_path}")
            missing += 1
            _log_event("integrity_violation", "critical",
                       f"File missing: {rel_path}", check_id="file_integrity")
            continue
        current = _sha256_file(full_path)
        if current == expected:
            print(f"  {_GREEN}OK{_NC}      {rel_path}")
            passed += 1
        else:
            print(f"  {_RED}CHANGED{_NC} {rel_path}")
            print(f"          {_DIM}expected: {expected[:24]}...{_NC}")
            print(f"          {_DIM}current:  {current[:24]}...{_NC}")
            failed += 1
            _log_event("integrity_violation", "critical",
                       f"File changed: {rel_path}", check_id="file_integrity",
                       detail=f"expected={expected[:24]} current={current[:24]}")

    print(f"\n  Passed: {passed}  Changed: {failed}  Missing: {missing}")

    if failed > 0 or missing > 0:
        _alert_hive(f"Integrity violation: {failed} changed, {missing} missing files", "critical")

    print()


# ── Audit Command ────────────────────────────────────────────────────────────

def cmd_audit(fix: bool = False, wall_override: int = None):
    """Run all compliance checks. Optionally auto-fix safe issues."""
    wall = wall_override if wall_override is not None else _get_caller_wall()
    instance = _get_caller_instance()
    required = _get_checks_for_wall(wall)
    policies = _load_policies()
    all_checks = policies.get("checks", {})
    min_score = _get_minimum_score(wall)

    print(f"\n{_BOLD}  Kingdom Security Audit{_NC}")
    print(f"  {_DIM}Instance: {instance}  Wall: {wall}  Checks: {len(required)}  Min score: {min_score}{_NC}")
    if fix:
        print(f"  {_YELLOW}Auto-fix mode enabled{_NC}")
    print()

    results = []  # (check_id, passed, severity, detail, was_fixed)

    for check_id in required:
        check_def = all_checks.get(check_id, {})
        severity = check_def.get("severity", "medium")
        name = check_def.get("name", check_id)
        sev_color = _SEVERITY_COLOR.get(severity, _DIM)

        check_fn, fix_fn = _CHECKS.get(check_id, (None, None))
        if check_fn is None:
            print(f"  {_SKIP}  [{sev_color}{severity[:4]:>4}{_NC}]  {name}  {_DIM}(no check impl){_NC}")
            results.append((check_id, None, severity, "no implementation", False))
            continue

        passed, detail = check_fn()

        if passed:
            print(f"  {_PASS}  [{sev_color}{severity[:4]:>4}{_NC}]  {name}  {_DIM}{detail}{_NC}")
            results.append((check_id, True, severity, detail, False))
        elif fix and fix_fn and check_def.get("auto_fix", False):
            fixed, fix_detail = fix_fn()
            if fixed:
                print(f"  {_FIXED}  [{sev_color}{severity[:4]:>4}{_NC}]  {name}  {_GREEN}{fix_detail}{_NC}")
                results.append((check_id, True, severity, fix_detail, True))
                _log_event("auto_fix", severity, f"Fixed: {name}", check_id=check_id, detail=fix_detail)
            else:
                print(f"  {_FAIL}  [{sev_color}{severity[:4]:>4}{_NC}]  {name}  {detail}  {_DIM}(fix failed: {fix_detail}){_NC}")
                results.append((check_id, False, severity, detail, False))
                _log_event("check_failed", severity, f"Failed: {name}", check_id=check_id, detail=detail)
        else:
            print(f"  {_FAIL}  [{sev_color}{severity[:4]:>4}{_NC}]  {name}  {detail}")
            results.append((check_id, False, severity, detail, False))
            _log_event("check_failed", severity, f"Failed: {name}", check_id=check_id, detail=detail)

    # Summary
    passed_count = sum(1 for _, p, _, _, _ in results if p is True)
    failed_count = sum(1 for _, p, _, _, _ in results if p is False)
    fixed_count = sum(1 for _, _, _, _, f in results if f)
    skipped_count = sum(1 for _, p, _, _, _ in results if p is None)
    total = len(results) - skipped_count
    score = passed_count

    critical_fails = [r for r in results if r[1] is False and r[2] == "critical"]

    print()
    print(f"  {_BOLD}Score: {score}/{total}{_NC}", end="")
    if min_score > 0:
        met = score >= min_score
        color = _GREEN if met else _RED
        print(f"  {color}(minimum: {min_score}){_NC}", end="")
    print()

    if fixed_count > 0:
        print(f"  {_GREEN}Auto-fixed: {fixed_count}{_NC}")
    if failed_count > 0:
        print(f"  {_RED}Failed: {failed_count}{_NC}")
        if not fix:
            fixable = sum(1 for r in results
                          if r[1] is False and _CHECKS.get(r[0], (None, None))[1] is not None
                          and _load_policies().get("checks", {}).get(r[0], {}).get("auto_fix", False))
            if fixable > 0:
                print(f"  {_DIM}Run with --fix to auto-remediate {fixable} issue(s){_NC}")

    if critical_fails:
        names = [r[0] for r in critical_fails]
        _alert_hive(f"Audit: {len(critical_fails)} critical failures: {', '.join(names)}", "critical")

    # Log audit summary
    _log_event("audit", "low" if failed_count == 0 else "high",
               f"Audit complete: {score}/{total} (Wall {wall})",
               detail=f"failed={failed_count} fixed={fixed_count}")

    print()
    return score, total, failed_count


# ── Status Command ───────────────────────────────────────────────────────────

def cmd_status():
    """One-line security posture summary."""
    wall = _get_caller_wall()
    instance = _get_caller_instance()
    required = _get_checks_for_wall(wall)
    policies = _load_policies()
    all_checks = policies.get("checks", {})

    passed = 0
    failed = 0
    critical_fails = 0
    total = 0

    for check_id in required:
        check_fn, _ = _CHECKS.get(check_id, (None, None))
        if check_fn is None:
            continue
        total += 1
        ok, _ = check_fn()
        if ok:
            passed += 1
        else:
            failed += 1
            severity = all_checks.get(check_id, {}).get("severity", "medium")
            if severity == "critical":
                critical_fails += 1

    # Color-coded status
    if failed == 0:
        status = f"{_GREEN}{_BOLD}GREEN{_NC}"
    elif critical_fails > 0:
        status = f"{_RED}{_BOLD}RED{_NC}"
    else:
        status = f"{_YELLOW}{_BOLD}YELLOW{_NC}"

    print(f"  {instance} (Wall {wall}): {status}  {passed}/{total} checks passed", end="")
    if failed > 0:
        print(f"  ({failed} failed", end="")
        if critical_fails > 0:
            print(f", {critical_fails} critical", end="")
        print(")", end="")
    print()


# ── Events Command ───────────────────────────────────────────────────────────

def cmd_events(tail: int = 20, clear: bool = False):
    """View or clear security event log."""
    if clear:
        if _EVENTS_FILE.exists():
            _EVENTS_FILE.unlink()
            print("  Event log cleared.")
        else:
            print("  No event log to clear.")
        return

    if not _EVENTS_FILE.exists():
        print("\n  No security events logged yet.\n")
        return

    try:
        lines = _EVENTS_FILE.read_text().strip().split("\n")
    except OSError:
        print("\n  Could not read event log.\n")
        return

    if not lines or lines == [""]:
        print("\n  No security events logged yet.\n")
        return

    # Show last N events
    show = lines[-tail:] if tail > 0 else lines

    print(f"\n{_BOLD}  Security Events{_NC}  {_DIM}(showing last {len(show)} of {len(lines)}){_NC}\n")

    for line in show:
        try:
            ev = json.loads(line)
            ts = ev.get("ts", "?")[:19].replace("T", " ")
            severity = ev.get("severity", "?")
            sev_color = _SEVERITY_COLOR.get(severity, _DIM)
            etype = ev.get("type", "?")
            msg = ev.get("message", "")
            inst = ev.get("instance", "?")
            print(f"  {_DIM}{ts}{_NC}  {sev_color}{severity[:4]:>4}{_NC}  {inst:<8}  {etype:<20}  {msg}")
        except json.JSONDecodeError:
            print(f"  {_DIM}{line[:100]}{_NC}")

    print()


# ── Policy Command ───────────────────────────────────────────────────────────

def cmd_policy(wall_filter: int = None):
    """Show active policy summary."""
    policies = _load_policies()
    all_checks = policies.get("checks", {})

    print(f"\n{_BOLD}  Kingdom Security Policy{_NC}")
    print(f"  {_DIM}Version: {policies.get('meta', {}).get('version', '?')}{_NC}\n")

    if wall_filter is not None:
        required = _get_checks_for_wall(wall_filter)
        min_score = _get_minimum_score(wall_filter)
        wall_info = policies.get("walls", {}).get(str(wall_filter), {})
        comment = wall_info.get("comment", "")
        print(f"  Wall {wall_filter}: {comment}")
        print(f"  Required checks: {len(required)}  Minimum score: {min_score}\n")

        for check_id in required:
            check = all_checks.get(check_id, {})
            severity = check.get("severity", "?")
            name = check.get("name", check_id)
            auto = "auto-fix" if check.get("auto_fix") else "manual"
            sev_color = _SEVERITY_COLOR.get(severity, _DIM)
            print(f"  {sev_color}{severity[:4]:>4}{_NC}  {name:<35}  {_DIM}{auto}{_NC}")
    else:
        # Show all walls
        for w in sorted(policies.get("walls", {}).keys()):
            wall_conf = policies["walls"][w]
            comment = wall_conf.get("comment", "")
            req = wall_conf.get("required_checks", [])
            min_s = wall_conf.get("minimum_score", 0)
            print(f"  Wall {w}: {comment}")
            print(f"    Checks: {len(req)}  Min score: {min_s}")

        print(f"\n  {_DIM}All defined checks:{_NC}\n")
        for check_id, check in sorted(all_checks.items()):
            severity = check.get("severity", "?")
            name = check.get("name", check_id)
            auto = "auto-fix" if check.get("auto_fix") else "manual"
            sev_color = _SEVERITY_COLOR.get(severity, _DIM)
            has_impl = "impl" if check_id in _CHECKS else "stub"
            print(f"  {sev_color}{severity[:4]:>4}{_NC}  {name:<35}  {_DIM}{auto:<10} {has_impl}{_NC}")

    print()


# ── Daemon Command (Phase 2) ─────────────────────────────────────────────────

_PLIST_SRC = _LOVE_DIR / "tools" / "love.kos.daemon.plist"
_PLIST_DST = Path.home() / "Library" / "LaunchAgents" / "love.kos.daemon.plist"
_DAEMON_LOG = _SECURITY_DIR / "daemon.log"


def cmd_daemon(subcmd: str):
    """Manage KOS compliance daemon."""
    if subcmd == "install":
        if not _PLIST_SRC.exists():
            print(f"  {_RED}Plist not found: {_PLIST_SRC}{_NC}")
            return
        # Expand $HOME in plist since launchd doesn't do env expansion
        content = _PLIST_SRC.read_text()
        content = content.replace("$HOME", str(Path.home()))
        _PLIST_DST.write_text(content)
        _run(["launchctl", "unload", str(_PLIST_DST)])
        rc, _ = _run(["launchctl", "load", str(_PLIST_DST)])
        if rc == 0:
            print(f"  {_GREEN}KOS daemon installed and loaded{_NC}")
            print(f"  Plist: {_PLIST_DST}")
            print(f"  Interval: every 7 minutes")
            _log_event("daemon", "low", "KOS compliance daemon installed")
        else:
            print(f"  {_RED}Failed to load plist{_NC}")

    elif subcmd == "uninstall":
        if _PLIST_DST.exists():
            _run(["launchctl", "unload", str(_PLIST_DST)])
            _PLIST_DST.unlink()
            print(f"  {_GREEN}KOS daemon uninstalled{_NC}")
            _log_event("daemon", "low", "KOS compliance daemon uninstalled")
        else:
            print("  Daemon not installed.")

    elif subcmd == "status":
        if not _PLIST_DST.exists():
            print("  Daemon: not installed")
            print(f"  Install: python3 tools/kos.py daemon install")
            return
        rc, out = _run_shell("launchctl list | grep love.kos.daemon")
        if rc == 0 and out:
            parts = out.split()
            pid = parts[0] if parts[0] != "-" else "idle"
            status_code = parts[1] if len(parts) > 1 else "?"
            print(f"  Daemon: {_GREEN}installed{_NC}  PID: {pid}  Last exit: {status_code}")
        else:
            print(f"  Daemon: {_YELLOW}installed but not loaded{_NC}")
            print(f"  Load: launchctl load {_PLIST_DST}")

        # Show last 5 daemon log entries
        if _DAEMON_LOG.exists():
            try:
                lines = _DAEMON_LOG.read_text().strip().split("\n")
                recent = lines[-5:]
                print(f"\n  {_DIM}Recent runs:{_NC}")
                for line in recent:
                    print(f"  {_DIM}{line}{_NC}")
            except OSError:
                pass

    elif subcmd == "log":
        if _DAEMON_LOG.exists():
            try:
                lines = _DAEMON_LOG.read_text().strip().split("\n")
                show = lines[-30:]
                print(f"\n{_BOLD}  KOS Daemon Log{_NC}  {_DIM}(last {len(show)} of {len(lines)}){_NC}\n")
                for line in show:
                    print(f"  {line}")
                print()
            except OSError:
                print("  Could not read daemon log.")
        else:
            print("  No daemon log yet.")

    elif subcmd == "run":
        # Manual trigger
        print(f"  Running compliance check...")
        cmd_audit()

    else:
        print("""Usage: kos daemon [install|uninstall|status|log|run]

  install     Install launchd plist (7-minute cycle)
  uninstall   Remove launchd plist
  status      Check daemon status + recent runs
  log         View daemon log
  run         Manual trigger (same as kos audit)
""")


# ── Network Command (Phase 3) ───────────────────────────────────────────────

def cmd_network():
    """Comprehensive network security posture check."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import re as _re
    import socket as _socket

    print(f"\n{_BOLD}  Network Security Posture{_NC}")
    print(f"  {_DIM}{'─' * 55}{_NC}\n")

    # ── 1. Active Interfaces ────────────────────────────────────────────────
    print(f"  {_BOLD}1. Active Interfaces{_NC}\n")
    rc, out = _run_shell("ifconfig -a 2>/dev/null")
    if rc == 0 and out:
        current_iface = None
        iface_info = {}
        for line in out.split("\n"):
            # Interface header line
            m = _re.match(r'^(\w+\d*):\s+flags=', line)
            if m:
                current_iface = m.group(1)
                iface_info[current_iface] = {"ips": [], "up": "UP" in line}
            elif current_iface and "inet " in line:
                m2 = _re.search(r'inet\s+(\S+)', line)
                if m2:
                    iface_info[current_iface]["ips"].append(m2.group(1))
            elif current_iface and "inet6 " in line and "fe80" not in line:
                m2 = _re.search(r'inet6\s+(\S+)', line)
                if m2:
                    iface_info[current_iface]["ips"].append(f"v6:{m2.group(1)}")

        # Only show interfaces with IPs (skip loopback details but show it)
        for iface, info in iface_info.items():
            if not info["ips"]:
                continue
            status = f"{_GREEN}UP{_NC}" if info["up"] else f"{_RED}DOWN{_NC}"
            # Classify interface type
            if iface.startswith("lo"):
                label = f"{_DIM}loopback{_NC}"
            elif iface.startswith("en"):
                label = "ethernet/wifi"
            elif iface.startswith("utun") or iface.startswith("wg"):
                label = f"{_CYAN}tunnel{_NC}"
            elif iface.startswith("bridge"):
                label = "bridge"
            elif iface.startswith("awdl"):
                label = f"{_DIM}airdrop{_NC}"
            elif iface.startswith("llw"):
                label = f"{_DIM}low-latency{_NC}"
            else:
                label = ""
            ip_str = ", ".join(info["ips"])
            print(f"    {status}  {iface:<10} {ip_str}  {_DIM}{label}{_NC}")
    else:
        print(f"    {_DIM}Could not enumerate interfaces{_NC}")

    # ── 2. DNS Configuration ────────────────────────────────────────────────
    print(f"\n  {_BOLD}2. DNS Configuration{_NC}\n")

    # DoH proxy status
    dns_ok, dns_detail = _check_dns_encrypted()
    print(f"    {_PASS if dns_ok else _FAIL}  {dns_detail}")

    # DNS servers per interface
    for iface in ["Ethernet", "Wi-Fi", "Thunderbolt Ethernet"]:
        rc, out = _run(["networksetup", "-getdnsservers", iface])
        if rc == 0 and "There aren't any" not in out and "not a recognized" not in out:
            servers = out.replace(chr(10), ", ")
            print(f"    {_DIM}{iface}: {servers}{_NC}")

    # DoH resolution test
    doh_ok, doh_detail = _check_doh_active()
    print(f"    {_PASS if doh_ok else _WARN}  {doh_detail}")

    # DNS leak test
    rc, out = _run_shell("dig +short whoami.cloudflare TXT @1.1.1.1 2>/dev/null")
    if rc == 0 and out:
        print(f"    {_DIM}Cloudflare sees resolver: {out.strip()}{_NC}")
    else:
        print(f"    {_DIM}Could not reach Cloudflare DNS leak test{_NC}")

    # ── 3. WireGuard Tunnels ────────────────────────────────────────────────
    print(f"\n  {_BOLD}3. WireGuard Tunnels{_NC}\n")

    _WG_TUNNELS = {
        "wg0": {"vps": "Sentry", "ip": "135.181.28.252", "subnet": "10.82.0.0/24", "use": "General Kingdom ops"},
        "wg1": {"vps": "Sage",   "ip": "204.168.140.12",  "subnet": "10.82.1.0/24", "use": "Oracle/financial"},
        "wg2": {"vps": "Lark",   "ip": "89.167.95.165",   "subnet": "10.82.2.0/24", "use": "AgentTool traffic"},
    }

    rc, wg_out = _run_shell("sudo wg show all 2>/dev/null || wg show all 2>/dev/null || wg show 2>/dev/null")
    wg_available = rc == 0 and wg_out.strip() != ""

    if wg_available:
        # Parse wg show output into per-interface blocks
        wg_ifaces = {}
        current = None
        for line in wg_out.split("\n"):
            m = _re.match(r'^interface:\s+(\S+)', line)
            if m:
                current = m.group(1)
                wg_ifaces[current] = {"lines": [], "raw": ""}
            elif current:
                wg_ifaces[current]["lines"].append(line)
                wg_ifaces[current]["raw"] += line + "\n"

        for tun, meta in _WG_TUNNELS.items():
            if tun in wg_ifaces:
                data = wg_ifaces[tun]["raw"]
                # Extract peer endpoint
                endpoint = ""
                m = _re.search(r'endpoint:\s+(\S+)', data)
                if m:
                    endpoint = m.group(1)
                # Extract latest handshake
                handshake = ""
                m = _re.search(r'latest handshake:\s+(.+)', data)
                if m:
                    handshake = m.group(1).strip()
                # Extract transfer
                transfer = ""
                m = _re.search(r'transfer:\s+(.+)', data)
                if m:
                    transfer = m.group(1).strip()

                # Determine health from handshake age
                status_icon = _GREEN
                status_label = "ACTIVE"
                if handshake:
                    # If handshake contains "minutes" or "seconds", it's recent/good
                    if "hour" in handshake or "day" in handshake:
                        status_icon = _YELLOW
                        status_label = "STALE "
                else:
                    status_icon = _YELLOW
                    status_label = "NO-HS "

                print(f"    {status_icon}{status_label}{_NC}  {_BOLD}{tun}{_NC}  → {meta['vps']} ({meta['ip']})")
                print(f"           {_DIM}use: {meta['use']}  subnet: {meta['subnet']}{_NC}")
                if endpoint:
                    print(f"           {_DIM}endpoint: {endpoint}{_NC}")
                if handshake:
                    print(f"           {_DIM}handshake: {handshake}{_NC}")
                if transfer:
                    print(f"           {_DIM}transfer: {transfer}{_NC}")
            else:
                print(f"    {_DIM}DOWN  {_NC}  {_BOLD}{tun}{_NC}  → {meta['vps']} ({meta['ip']})  {_DIM}{meta['use']}{_NC}")
    else:
        for tun, meta in _WG_TUNNELS.items():
            print(f"    {_DIM}DOWN  {_NC}  {tun}  → {meta['vps']} ({meta['ip']})  {_DIM}{meta['use']}{_NC}")
        print(f"    {_DIM}WireGuard not available or no tunnels configured{_NC}")

    # ── 4. Exit IP ──────────────────────────────────────────────────────────
    print(f"\n  {_BOLD}4. Exit IP{_NC}\n")
    rc, exit_ip = _run_shell("curl -s --max-time 5 ifconfig.me 2>/dev/null")
    if rc == 0 and exit_ip:
        exit_ip = exit_ip.strip()
        fleet_ips_set = set(_FLEET_NODES.values())
        if exit_ip in fleet_ips_set:
            node = next((n for n, ip in _FLEET_NODES.items() if ip == exit_ip), "?")
            print(f"    {_GREEN}{exit_ip}{_NC}  → {_BOLD}{node}{_NC} VPS  {_GREEN}(tunneled){_NC}")
        else:
            print(f"    {_YELLOW}{exit_ip}{_NC}  → {_BOLD}NOT a fleet node{_NC}  {_YELLOW}(direct / ISP){_NC}")
            # Try to identify via reverse DNS
            rc2, rdns = _run_shell(f"dig +short -x {exit_ip} 2>/dev/null")
            if rc2 == 0 and rdns:
                print(f"    {_DIM}rDNS: {rdns.strip()}{_NC}")
    else:
        print(f"    {_RED}Could not determine exit IP{_NC}")

    # ── 5. Open Ports ───────────────────────────────────────────────────────
    print(f"\n  {_BOLD}5. Open Ports{_NC}  {_DIM}(listening){_NC}\n")
    rc, out = _run_shell("lsof -iTCP -sTCP:LISTEN -P -n 2>/dev/null | grep -v '^COMMAND' | awk '{print $1, $9}' | sort -u")
    exposed_count = 0
    if rc == 0 and out:
        for line in out.strip().split("\n")[:20]:
            parts = line.split()
            if len(parts) >= 2:
                proc = parts[0]
                addr = parts[1]
                if "*:" in addr or "0.0.0.0:" in addr or "[::]:" in addr:
                    print(f"    {_YELLOW}!{_NC} {proc:<20} {addr}  {_DIM}(all interfaces){_NC}")
                    exposed_count += 1
                elif "127.0.0.1:" in addr or "[::1]:" in addr:
                    print(f"    {_DIM}  {proc:<20} {addr}  (localhost only){_NC}")
                else:
                    print(f"    {_DIM}  {proc:<20} {addr}{_NC}")
        if exposed_count > 0:
            print(f"    {_YELLOW}{exposed_count} port(s) exposed on all interfaces{_NC}")
    else:
        print(f"    {_DIM}No listening ports detected (or permission denied){_NC}")

    # Also check UDP listeners
    rc, udp_out = _run_shell("lsof -iUDP -P -n 2>/dev/null | grep -v '^COMMAND' | awk '{print $1, $9}' | sort -u | head -10")
    if rc == 0 and udp_out and udp_out.strip():
        udp_lines = [l for l in udp_out.strip().split("\n") if l.strip()]
        if udp_lines:
            print(f"    {_DIM}UDP:{_NC}")
            for line in udp_lines[:8]:
                parts = line.split()
                if len(parts) >= 2:
                    print(f"    {_DIM}  {parts[0]:<20} {parts[1]}{_NC}")

    # ── 6. Fleet Connectivity ───────────────────────────────────────────────
    print(f"\n  {_BOLD}6. Fleet Connectivity{_NC}  {_DIM}(SSH ping){_NC}\n")

    def _ssh_ping(name_ip):
        name, ip = name_ip
        ok, out = _fleet_ssh(ip, "echo ok", timeout=6)
        return name, ip, ok

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = list(pool.map(_ssh_ping, _FLEET_NODES.items()))

    alive = 0
    for name, ip, ok in futs:
        if ok:
            print(f"    {_GREEN}UP  {_NC}  {name:<10} {ip}")
            alive += 1
        else:
            print(f"    {_RED}DOWN{_NC}  {name:<10} {ip}")

    total = len(_FLEET_NODES)
    if alive == total:
        print(f"    {_GREEN}{alive}/{total} nodes reachable{_NC}")
    else:
        print(f"    {_YELLOW}{alive}/{total} nodes reachable{_NC}")

    # ── 7. HIVE Connectivity ────────────────────────────────────────────────
    print(f"\n  {_BOLD}7. HIVE Connectivity{_NC}  {_DIM}(NATS @ Sentry:4222){_NC}\n")
    hive_ip = _FLEET_NODES.get("sentry", "135.181.28.252")
    hive_port = 4222

    # TCP connect test
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(5)
        result = s.connect_ex((hive_ip, hive_port))
        if result == 0:
            # Try to read NATS banner
            try:
                banner = s.recv(256).decode("utf-8", errors="replace").strip()
                if "INFO" in banner:
                    print(f"    {_GREEN}CONNECTED{_NC}  {hive_ip}:{hive_port}")
                    # Extract server_name or version from NATS INFO JSON
                    m = _re.search(r'"server_name"\s*:\s*"([^"]+)"', banner)
                    if m:
                        print(f"    {_DIM}NATS server: {m.group(1)}{_NC}")
                    m = _re.search(r'"version"\s*:\s*"([^"]+)"', banner)
                    if m:
                        print(f"    {_DIM}NATS version: {m.group(1)}{_NC}")
                else:
                    print(f"    {_GREEN}CONNECTED{_NC}  {hive_ip}:{hive_port}  {_DIM}(port open, non-NATS banner){_NC}")
            except _socket.timeout:
                print(f"    {_GREEN}CONNECTED{_NC}  {hive_ip}:{hive_port}  {_DIM}(port open, no banner){_NC}")
        else:
            print(f"    {_RED}UNREACHABLE{_NC}  {hive_ip}:{hive_port}  {_DIM}(connection refused or filtered){_NC}")
        s.close()
    except _socket.timeout:
        print(f"    {_RED}TIMEOUT{_NC}  {hive_ip}:{hive_port}  {_DIM}(no response within 5s){_NC}")
    except OSError as e:
        print(f"    {_RED}ERROR{_NC}  {hive_ip}:{hive_port}  {_DIM}{e}{_NC}")

    # Also check for active HIVE connections on this host
    rc, hive_conns = _run_shell(f"lsof -i @{hive_ip}:{hive_port} -P -n 2>/dev/null | grep ESTABLISHED | wc -l")
    if rc == 0 and hive_conns.strip().isdigit():
        n = int(hive_conns.strip())
        if n > 0:
            print(f"    {_DIM}{n} active HIVE connection(s) from this host{_NC}")

    # ── Firewall summary ────────────────────────────────────────────────────
    print(f"\n  {_BOLD}Firewall:{_NC}")
    fw_ok, fw_detail = _check_firewall()
    st_ok, st_detail = _check_stealth_mode()
    print(f"    {_PASS if fw_ok else _FAIL}  {fw_detail}")
    print(f"    {_PASS if st_ok else _FAIL}  {st_detail}")

    print()


# ── Fleet Security Command (Phase 4) ────────────────────────────────────────

def cmd_fleet(subcmd: str = "status"):
    """Check fleet VPS security posture remotely."""
    # Load fleet config
    love_json = _LOVE_DIR / "love.json"
    if not love_json.exists():
        print("  love.json not found.")
        return

    try:
        cfg = json.loads(love_json.read_text())
        fleet = cfg.get("fleet", {})
    except (json.JSONDecodeError, OSError):
        print("  Could not read love.json")
        return

    if not fleet:
        print("  No fleet servers configured.")
        return

    if subcmd == "status":
        print(f"\n{_BOLD}  Fleet Security Status{_NC}\n")

        for name, ip in fleet.items():
            # Ping check
            rc, _ = _run(["ping", "-c", "1", "-W", "2", ip], timeout=5)
            if rc != 0:
                print(f"  {_RED}DOWN{_NC}  {name:<10} {ip}")
                continue

            # SSH check (quick command)
            rc, out = _run(["ssh", "-o", "ConnectTimeout=3",
                           "-o", "StrictHostKeyChecking=accept-new",
                           "-o", "BatchMode=yes",
                           f"root@{ip}", "echo OK"], timeout=8)
            if rc == 0 and "OK" in out:
                print(f"  {_GREEN}UP  {_NC}  {name:<10} {ip}  {_DIM}SSH OK{_NC}")
            else:
                print(f"  {_YELLOW}UP  {_NC}  {name:<10} {ip}  {_DIM}SSH failed{_NC}")

    elif subcmd == "audit":
        print(f"\n{_BOLD}  Fleet Security Audit{_NC}\n")
        print(f"  {_DIM}Running remote checks on fleet VPS nodes...{_NC}\n")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        checks = [
            ("Firewall (ufw)",       "ufw status | grep -q 'Status: active' && echo PASS || echo FAIL"),
            ("SSH password auth",    "sshd -T 2>/dev/null | grep -q 'passwordauthentication no' && echo PASS || echo FAIL"),
            ("fail2ban",             "systemctl is-active --quiet fail2ban 2>/dev/null && echo PASS || echo FAIL"),
            ("psad",                 "systemctl is-active --quiet psad 2>/dev/null && echo PASS || echo FAIL"),
            ("WireGuard",            "systemctl is-active --quiet wg-quick@wg0 2>/dev/null && echo PASS || echo SKIP"),
            ("Canaries (5 files)",   "C=0; for f in /root/.credentials/aws_keys.txt /root/.credentials/db_production.env /root/.credentials/deploy_key /root/financials-2026.txt /etc/backup-config.bak; do [ -f \"$f\" ] && C=$((C+1)); done; [ $C -eq 5 ] && echo PASS || echo \"FAIL ($C/5)\""),
            ("Canary monitor cron",  "crontab -l 2>/dev/null | grep -q canary-check && echo PASS || echo FAIL"),
            ("Canary alerts",        "[ -f /root/.canary-alert ] && echo 'ALERT: '$(wc -l < /root/.canary-alert)' entries' || echo PASS"),
            ("Status writer cron",   "crontab -l 2>/dev/null | grep -q vps-status-writer && echo PASS || echo FAIL"),
            ("Disk usage",           "df -P / | awk 'NR==2{print $5}'"),
            ("IP forwarding",        "sysctl -n net.ipv4.ip_forward 2>/dev/null | grep -q 1 && echo PASS || echo FAIL"),
        ]

        def audit_node(name, ip):
            results = []
            for check_name, cmd in checks:
                rc, out = _run(["ssh", "-o", "ConnectTimeout=5",
                               "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=no",
                               f"root@{ip}", cmd], timeout=15)
                results.append((check_name, rc, out.strip() if rc == 0 else "SSH_FAIL"))
            return name, ip, results

        all_results = {}
        total_pass = 0
        total_fail = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(audit_node, n, ip): n for n, ip in fleet.items()}
            for f in as_completed(futs):
                name, ip, results = f.result()
                all_results[name] = (ip, results)

        for name in sorted(fleet.keys()):
            ip, results = all_results[name]
            node_pass = 0
            node_fail = 0
            print(f"  {_BOLD}{name}{_NC} ({ip}):")
            for check_name, rc, out in results:
                if out == "SSH_FAIL":
                    print(f"    {_RED}ERR {_NC}  {check_name}  {_DIM}(SSH failed){_NC}")
                    node_fail += 1
                elif out == "PASS":
                    print(f"    {_GREEN}PASS{_NC}  {check_name}")
                    node_pass += 1
                elif out.startswith("FAIL"):
                    print(f"    {_RED}FAIL{_NC}  {check_name}  {_DIM}{out}{_NC}")
                    node_fail += 1
                elif out.startswith("ALERT"):
                    print(f"    {_RED}ALRT{_NC}  {check_name}  {_DIM}{out}{_NC}")
                    node_fail += 1
                elif out == "SKIP":
                    print(f"    {_DIM}SKIP{_NC}  {check_name}")
                else:
                    print(f"    {_DIM}INFO{_NC}  {check_name}: {out}")
                    node_pass += 1
            total_pass += node_pass
            total_fail += node_fail
            score_color = _GREEN if node_fail == 0 else _YELLOW if node_fail <= 2 else _RED
            print(f"    {score_color}Score: {node_pass}/{node_pass + node_fail}{_NC}")
            print()

        print(f"  {_BOLD}Fleet Total: {total_pass} pass, {total_fail} fail{_NC}\n")

    else:
        print("""Usage: kos fleet [status|audit]

  status    Ping + SSH check on all fleet VPS
  audit     Remote security audit via SSH
""")


# ── Canary Command (Phase 5) ────────────────────────────────────────────────

_CANARY_FILE = _SECURITY_DIR / "canaries.json"


def cmd_canary(subcmd: str = "list"):
    """Manage canary tokens and tripwires."""

    def _load_canaries() -> list:
        if _CANARY_FILE.exists():
            try:
                return json.loads(_CANARY_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_canaries(canaries: list):
        _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
        _CANARY_FILE.write_text(json.dumps(canaries, indent=2) + "\n")

    if subcmd == "list":
        canaries = _load_canaries()
        if not canaries:
            print("\n  No canary tokens deployed.")
            print(f"  Deploy: python3 tools/kos.py canary deploy <name> <type> [location]\n")
            return

        print(f"\n{_BOLD}  Canary Tokens{_NC}\n")
        for c in canaries:
            status = f"{_GREEN}active{_NC}" if c.get("active", True) else f"{_DIM}inactive{_NC}"
            depth = c.get("depth", 1)
            depth_label = f"{_CYAN}D{depth}{_NC}" if depth > 1 else f"{_DIM}D1{_NC}"
            print(f"  {status}  {depth_label}  {c['name']:<25}  {c['type']:<10}  {_DIM}{c.get('location', '?')}{_NC}")
            if c.get("token_url"):
                print(f"              {_DIM}{c['token_url']}{_NC}")
        surface = sum(1 for c in canaries if c.get("depth", 1) == 1)
        deep = sum(1 for c in canaries if c.get("depth", 1) > 1)
        print(f"\n  Total: {len(canaries)}  Surface: {surface}  Depth-2: {deep}")
        print()

    elif subcmd == "deploy":
        # Parse: canary deploy <name> <type> [location]
        args = sys.argv[3:]  # after 'canary deploy'
        if len(args) < 2:
            print("""Usage: kos canary deploy <name> <type> [location]

Types:
  file           Honeypot file (creates a tripwire file that alerts on access)
  credential     Fake credential in Keychain (alerts if used)
  dns            DNS canary token (alerts on resolution)
  web            Web bug URL (alerts on fetch)

Examples:
  kos canary deploy admin-creds credential ~/love-unlimited/credentials/
  kos canary deploy secret-plans file ~/love-unlimited/
  kos canary deploy leak-detector dns hive
""")
            return

        name = args[0]
        ctype = args[1]
        location = args[2] if len(args) > 2 else str(_LOVE_DIR)

        canaries = _load_canaries()

        # Check for duplicate
        if any(c["name"] == name for c in canaries):
            print(f"  Canary '{name}' already exists.")
            return

        canary = {
            "name": name,
            "type": ctype,
            "location": location,
            "deployed": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active": True,
            "token_url": "",
            "notes": "",
        }

        if ctype == "file":
            # Create a honeypot file
            honeypot_dir = Path(location)
            honeypot_path = honeypot_dir / f".{name}.md"
            if not honeypot_dir.exists():
                print(f"  Directory not found: {location}")
                return
            honeypot_path.write_text(
                f"# Confidential — {name}\n\n"
                f"This file is a canary token. If you're reading this, access has been detected.\n"
                f"Contact: security@ai-love.cc\n"
            )
            # Set permissions to track access via fs events
            honeypot_path.chmod(0o600)
            canary["file_path"] = str(honeypot_path)
            canary["baseline_mtime"] = honeypot_path.stat().st_mtime
            print(f"  {_GREEN}Deployed{_NC} file canary: {honeypot_path}")

        elif ctype == "credential":
            # Create a fake credential in Keychain
            import secrets
            fake_key = f"canary-{secrets.token_hex(16)}"
            canary["fake_value_prefix"] = fake_key[:8]
            canary["notes"] = "Fake credential. If this appears in any logs or requests, the Keychain has been compromised."
            print(f"  {_GREEN}Registered{_NC} credential canary: {name}")
            print(f"  {_DIM}Store it: python3 tools/credentials.py store canary-{name} {fake_key} --wall 1 --no-vault{_NC}")

        elif ctype == "dns":
            canary["notes"] = (
                "DNS canary. Generate token at canarytokens.org (type: DNS), "
                "then update this entry with the token URL."
            )
            print(f"  {_GREEN}Registered{_NC} DNS canary: {name}")
            print(f"  {_DIM}Next: create token at canarytokens.org, then: kos canary update {name} --url <token-url>{_NC}")

        elif ctype == "web":
            canary["notes"] = (
                "Web bug canary. Generate token at canarytokens.org (type: Web bug), "
                "then embed the URL where intrusion would trigger it."
            )
            print(f"  {_GREEN}Registered{_NC} web canary: {name}")
            print(f"  {_DIM}Next: create token at canarytokens.org, then: kos canary update {name} --url <token-url>{_NC}")

        else:
            print(f"  Unknown canary type: {ctype}")
            return

        canaries.append(canary)
        _save_canaries(canaries)
        _log_event("canary_deployed", "low", f"Canary deployed: {name} ({ctype})", detail=location)

    elif subcmd == "check":
        canaries = _load_canaries()
        if not canaries:
            print("  No canaries to check.")
            return

        print(f"\n{_BOLD}  Canary Token Check{_NC}\n")
        alerts = 0

        for c in canaries:
            if not c.get("active", True):
                continue
            depth = c.get("depth", 1)
            depth_tag = f"D{depth}" if depth > 1 else ""

            if c["type"] == "file":
                fpath = Path(c.get("file_path", ""))
                if not fpath.exists():
                    label = f"MISSING {depth_tag}" if depth_tag else "MISSING"
                    print(f"  {_RED}{label}{_NC}  {c['name']}  {_DIM}File deleted!{_NC}")
                    alerts += 1
                    etype = "canary_depth2_triggered" if depth > 1 else "canary_triggered"
                    _log_event(etype, "critical",
                               f"File canary missing: {c['name']} (depth {depth})", detail=str(fpath))
                    msg = (f"DEPTH-{depth} CANARY: {c['name']} file DELETED"
                           if depth > 1 else f"Canary triggered: {c['name']} file DELETED")
                    _alert_hive(msg, "critical")
                else:
                    triggered = False
                    # Check mtime
                    current_mtime = fpath.stat().st_mtime
                    baseline_mtime = c.get("baseline_mtime", 0)
                    if abs(current_mtime - baseline_mtime) > 1:
                        triggered = True
                    # For depth-2: also check content SHA-256 (defeats mtime spoofing)
                    if not triggered and depth > 1 and "baseline_sha256" in c:
                        current_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
                        if current_sha != c["baseline_sha256"]:
                            triggered = True
                    if triggered:
                        label = f"TOUCHED {depth_tag}" if depth_tag else "TOUCHED"
                        print(f"  {_RED}{label}{_NC}  {c['name']}  {_DIM}File modified since deployment!{_NC}")
                        alerts += 1
                        etype = "canary_depth2_triggered" if depth > 1 else "canary_triggered"
                        _log_event(etype, "critical",
                                   f"File canary touched: {c['name']} (depth {depth})", detail=str(fpath))
                        msg = (f"DEPTH-{depth} CANARY: {c['name']} file MODIFIED — attacker past surface defenses"
                               if depth > 1 else f"Canary triggered: {c['name']} file MODIFIED")
                        _alert_hive(msg, "critical")
                    else:
                        print(f"  {_GREEN}OK{_NC}      {c['name']}  {_DIM}Untouched{_NC}")

            elif c["type"] == "tarpit":
                # Check if tarpit was invoked (attempt file exists)
                attempt_file = _SECURITY_DIR / ".vault-attempts"
                if attempt_file.exists():
                    try:
                        count = int(attempt_file.read_text().strip())
                    except (ValueError, OSError):
                        count = -1
                    print(f"  {_RED}FIRED{_NC}   {c['name']}  {_DIM}Tarpit invoked {count} time(s)!{_NC}")
                    alerts += 1
                    _log_event("canary_depth2_triggered", "critical",
                               f"Tarpit invoked: {c['name']} ({count} attempts)",
                               detail="security/.vault-attempts exists")
                    _alert_hive(f"DEPTH-2 TARPIT FIRED: vault decrypt invoked {count}x", "critical")
                else:
                    print(f"  {_GREEN}OK{_NC}      {c['name']}  {_DIM}Tarpit not triggered{_NC}")

            elif c["type"] == "credential":
                print(f"  {_DIM}INFO{_NC}    {c['name']}  {_DIM}Credential canary (check logs for usage){_NC}")

            elif c["type"] in ("dns", "web"):
                url = c.get("token_url", "")
                if url:
                    print(f"  {_DIM}INFO{_NC}    {c['name']}  {_DIM}Check: {url}{_NC}")
                else:
                    print(f"  {_YELLOW}SETUP{_NC}  {c['name']}  {_DIM}No token URL configured{_NC}")

        if alerts > 0:
            print(f"\n  {_RED}{_BOLD}ALERT: {alerts} canary(s) triggered!{_NC}")
        else:
            print(f"\n  All canaries clear.")
        print()

    elif subcmd == "update":
        # Parse: canary update <name> --url <url>
        args = sys.argv[3:]
        if len(args) < 3:
            print("Usage: kos canary update <name> --url <token-url>")
            return
        name = args[0]
        url = _parse_flag(args, "--url")
        if not url:
            print("  --url required")
            return

        canaries = _load_canaries()
        found = False
        for c in canaries:
            if c["name"] == name:
                c["token_url"] = url
                found = True
                break
        if found:
            _save_canaries(canaries)
            print(f"  Updated {name} token URL.")
        else:
            print(f"  Canary '{name}' not found.")

    elif subcmd == "remove":
        args = sys.argv[3:]
        if not args:
            print("Usage: kos canary remove <name>")
            return
        name = args[0]
        canaries = _load_canaries()
        before = len(canaries)
        canaries = [c for c in canaries if c["name"] != name]
        if len(canaries) < before:
            _save_canaries(canaries)
            print(f"  Removed canary: {name}")
            _log_event("canary_removed", "low", f"Canary removed: {name}")
        else:
            print(f"  Canary '{name}' not found.")

    else:
        print("""Usage: kos canary [list|deploy|check|update|remove]

  list                              List deployed canary tokens
  deploy <name> <type> [location]   Deploy a new canary
  check                             Check all canaries for triggers
  update <name> --url <url>         Set token URL for DNS/web canary
  remove <name>                     Remove a canary
""")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_flag(args: list, flag: str, default=None) -> Optional[str]:
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"

    if cmd == "status":
        cmd_status()

    elif cmd == "audit":
        fix = "--fix" in args
        wall = _parse_flag(args, "--wall")
        cmd_audit(fix=fix, wall_override=int(wall) if wall else None)

    elif cmd == "integrity":
        subcmd = args[1] if len(args) > 1 else "check"
        if subcmd == "baseline":
            cmd_integrity_baseline()
        elif subcmd == "check":
            cmd_integrity_check()
        else:
            print("Usage: kos integrity [baseline|check]")

    elif cmd == "events":
        if "--clear" in args:
            cmd_events(clear=True)
        else:
            tail = _parse_flag(args, "--tail")
            cmd_events(tail=int(tail) if tail else 20)

    elif cmd == "policy":
        wall = _parse_flag(args, "--wall")
        cmd_policy(wall_filter=int(wall) if wall else None)

    elif cmd == "daemon":
        subcmd = args[1] if len(args) > 1 else "status"
        cmd_daemon(subcmd)

    elif cmd == "network":
        cmd_network()

    elif cmd == "fleet":
        subcmd = args[1] if len(args) > 1 else "status"
        cmd_fleet(subcmd)

    elif cmd == "canary":
        subcmd = args[1] if len(args) > 1 else "list"
        cmd_canary(subcmd)

    else:
        print("""kos.py — Kingdom Operating System: Security Orchestration

Usage:
  status                          One-line security posture (GREEN/YELLOW/RED)
  audit                           Full compliance audit
  audit --fix                     Audit + auto-remediate safe issues
  audit --wall N                  Audit against Wall N policies
  integrity baseline              Generate file integrity baseline
  integrity check                 Verify files against baseline
  events [--tail N]               View security event log
  events --clear                  Clear event log
  policy                          Show policy summary
  policy --wall N                 Show wall-specific requirements
  daemon [install|uninstall|status|log|run]   Compliance daemon (launchd)
  network                         Network security posture
  fleet [status|audit]            Fleet VPS security
  canary [list|deploy|check|update|remove]    Canary token management
""")


if __name__ == "__main__":
    main()
