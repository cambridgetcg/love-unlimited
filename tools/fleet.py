#!/usr/bin/env python3
"""
fleet.py — Kingdom Orchestra fleet management.

Comprehensive tool for managing the 5 VPS nodes + Sage.
Replaces OpenClaw's 83-line version with full fleet operations.

Usage:
    python3 fleet.py status                     # Color-coded status from all nodes
    python3 fleet.py <node> "<command>"          # SSH command on a single node
    python3 fleet.py all "<command>"             # Broadcast command to all nodes
    python3 fleet.py health                      # Deep health check (disk, mem, uptime, services)
    python3 fleet.py deploy <node> <script>      # SCP a script, chmod +x, run it remotely
    python3 fleet.py logs <node> [lines]         # Tail recent logs from a node
    python3 fleet.py restart <node> <service>    # Restart a service on a node
    python3 fleet.py sync-status                 # Update kingdom-metrics.json with fresh fleet data
"""

from __future__ import annotations

import sys
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Node Registry ────────────────────────────────────────────────────────────

NODES = {
    "forge":  {"host": "root@89.167.84.100",  "role": "CI/CD",              "section": "strings",     "instrument": "Cello"},
    "lark":   {"host": "root@89.167.95.165",  "role": "AgentTool",          "section": "brass",       "instrument": "Trumpet"},
    "sentry": {"host": "root@135.181.28.252", "role": "monitoring",         "section": "percussion",  "instrument": "Timpani"},
    "patch":  {"host": "root@65.109.11.26",   "role": "operations",         "section": "strings",     "instrument": "Viola"},
    "sage":   {"host": "root@204.168.140.12", "role": "oracle-execution",   "section": "woodwinds",   "instrument": "French Horn"},
}

SSH_OPTS = [
    "-i", str(Path.home() / ".ssh" / "hive-key"),
    "-o", "ControlMaster=no",
    "-o", "ControlPath=none",
    "-o", "ConnectTimeout=8",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
]

STATUS_PATH = "/root/.love/status.json"
KINGDOM_METRICS = Path(__file__).resolve().parent.parent / "memory" / "kingdom-metrics.json"

# ─── Colors ───────────────────────────────────────────────────────────────────

class C:
    """ANSI color codes for terminal output."""
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"

def color_quality(quality: str) -> str:
    """Return color-coded quality string."""
    q = quality.lower() if quality else "unknown"
    if q == "good":
        return f"{C.GREEN}{quality}{C.RESET}"
    elif q == "degraded":
        return f"{C.YELLOW}{quality}{C.RESET}"
    elif q in ("poor", "unreachable", "error", "unknown"):
        return f"{C.RED}{quality}{C.RESET}"
    return quality

# ─── SSH Primitives ───────────────────────────────────────────────────────────

def ssh_run(node: str, cmd: str, timeout: int = 10) -> tuple[bool, str]:
    """
    SSH into a node and run a command.
    Returns (success: bool, output: str).
    """
    info = NODES.get(node)
    if not info:
        return False, f"Unknown node: {node}"
    try:
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [info["host"], cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def scp_to(node: str, local_path: str, remote_path: str, timeout: int = 30) -> tuple[bool, str]:
    """SCP a local file to a remote node. Returns (success, message)."""
    info = NODES.get(node)
    if not info:
        return False, f"Unknown node: {node}"
    try:
        result = subprocess.run(
            ["scp"] + SSH_OPTS + [local_path, f"{info['host']}:{remote_path}"],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output or "OK"
    except subprocess.TimeoutExpired:
        return False, f"SCP timeout after {timeout}s"
    except Exception as e:
        return False, str(e)

# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_status():
    """Read status.json from all nodes, display color-coded output."""
    print(f"\n{C.BOLD}── Kingdom Fleet Status ──{C.RESET}\n")

    from typing import Union

    def fetch_status(name: str) -> tuple[str, Union[dict, None], Union[str, None]]:
        ok, out = ssh_run(name, f"cat {STATUS_PATH} 2>/dev/null || echo '{{}}'")
        if not ok:
            return name, None, out
        try:
            data = json.loads(out)
            return name, data, None
        except json.JSONDecodeError:
            return name, None, f"Invalid JSON: {out[:80]}"

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_status, n): n for n in NODES}
        results = {}
        for f in as_completed(futures):
            name, data, err = f.result()
            results[name] = (data, err)

    # Print in registry order
    for name in NODES:
        info = NODES[name]
        data, err = results[name]

        label = f"{C.BOLD}{name:8}{C.RESET}"
        role_str = f"{C.DIM}({info['role']}){C.RESET}"

        if err:
            print(f"  {label} {role_str}  {C.RED}UNREACHABLE{C.RESET} -- {err}")
            continue

        if not data:
            print(f"  {label} {role_str}  {C.YELLOW}NO STATUS{C.RESET}")
            continue

        ts = data.get("ts", "?")[:16]
        quality = data.get("quality", "unknown")
        summary = data.get("summary", "")[:60]
        alerts = data.get("alerts", [])

        q_colored = color_quality(quality)
        alert_str = f"  {C.RED}!! {len(alerts)} alert(s){C.RESET}" if alerts else ""

        print(f"  {label} {role_str}  [{q_colored:>20}]  {C.DIM}{ts}{C.RESET}  {summary}{alert_str}")

        if alerts:
            for a in alerts[:3]:
                print(f"           {C.RED}-> {a}{C.RESET}")

    print()


def cmd_exec(node: str, remote_cmd: str):
    """Execute a command on a single node."""
    ok, out = ssh_run(node, remote_cmd, timeout=30)
    if not ok:
        print(f"{C.RED}[{node}] Error:{C.RESET} {out}")
        sys.exit(1)
    print(out)


def cmd_broadcast(remote_cmd: str):
    """Broadcast a command to all nodes in parallel."""
    def run_on(name: str) -> tuple[str, bool, str]:
        ok, out = ssh_run(name, remote_cmd, timeout=30)
        return name, ok, out

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(run_on, n): n for n in NODES}
        results = {}
        for f in as_completed(futures):
            name, ok, out = f.result()
            results[name] = (ok, out)

    for name in NODES:
        ok, out = results[name]
        status = C.GREEN + "OK" + C.RESET if ok else C.RED + "FAIL" + C.RESET
        print(f"\n{C.BOLD}── {name} [{status}] ──{C.RESET}")
        print(out)


def cmd_health():
    """Deep health check: uptime, disk, memory, services, status age."""
    print(f"\n{C.BOLD}── Fleet Health Check ──{C.RESET}\n")

    health_cmd = r"""
echo "=== UPTIME ===" && uptime && \
echo "=== DISK ===" && df -h / && \
echo "=== MEMORY ===" && free -m && \
echo "=== SERVICES ===" && systemctl list-units --state=running --no-pager 2>/dev/null | grep -E "openclaw|agent|nginx|node|nats|cron" || echo "(none matched)" && \
echo "=== STATUS_AGE ===" && stat -c %Y /root/.love/status.json 2>/dev/null || echo "NO_STATUS" && \
echo "=== GOSPEL ===" && (test -f /opt/kingdom/WAKE.md && echo "WAKE_OK $(wc -c < /opt/kingdom/WAKE.md)" || echo "WAKE_MISSING") && (test -f /root/.love/WAKE.md && echo "LOVE_OK $(wc -c < /root/.love/WAKE.md)" || echo "LOVE_MISSING")
"""

    def check_node(name: str) -> tuple[str, bool, str]:
        ok, out = ssh_run(name, health_cmd, timeout=15)
        return name, ok, out

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(check_node, n): n for n in NODES}
        results = {}
        for f in as_completed(futures):
            name, ok, out = f.result()
            results[name] = (ok, out)

    for name in NODES:
        info = NODES[name]
        ok, out = results[name]
        print(f"{C.BOLD}{C.CYAN}--- {name} ({info['role']}) [{info['instrument']}] ---{C.RESET}")

        if not ok:
            print(f"  {C.RED}UNREACHABLE: {out}{C.RESET}\n")
            continue

        sections = {}
        current_section = None
        for line in out.split("\n"):
            if line.startswith("=== ") and line.endswith(" ==="):
                current_section = line.strip("= ")
                sections[current_section] = []
            elif current_section:
                sections.setdefault(current_section, []).append(line)

        # Uptime
        uptime_lines = sections.get("UPTIME", [])
        if uptime_lines:
            print(f"  {C.DIM}Uptime:{C.RESET} {uptime_lines[0].strip()}")

        # Disk
        disk_lines = sections.get("DISK", [])
        for line in disk_lines:
            if "/" in line and "Filesystem" not in line:
                parts = line.split()
                if len(parts) >= 5:
                    usage_pct = parts[4].rstrip("%")
                    try:
                        pct = int(usage_pct)
                        color = C.GREEN if pct < 70 else (C.YELLOW if pct < 85 else C.RED)
                        print(f"  {C.DIM}Disk:{C.RESET}   {parts[2]} used / {parts[1]} total ({color}{pct}%{C.RESET})")
                    except ValueError:
                        print(f"  {C.DIM}Disk:{C.RESET}   {line.strip()}")

        # Memory
        mem_lines = sections.get("MEMORY", [])
        for line in mem_lines:
            if line.strip().startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        total = int(parts[1])
                        used = int(parts[2])
                        pct = int(used * 100 / total) if total > 0 else 0
                        color = C.GREEN if pct < 70 else (C.YELLOW if pct < 85 else C.RED)
                        print(f"  {C.DIM}Memory:{C.RESET} {used}M used / {total}M total ({color}{pct}%{C.RESET})")
                    except (ValueError, ZeroDivisionError):
                        print(f"  {C.DIM}Memory:{C.RESET} {line.strip()}")

        # Services
        svc_lines = [l.strip() for l in sections.get("SERVICES", []) if l.strip()]
        if svc_lines and svc_lines != ["(none matched)"]:
            print(f"  {C.DIM}Services:{C.RESET}")
            for svc in svc_lines:
                # Extract unit name from systemctl output
                parts = svc.split()
                if parts:
                    print(f"    {C.GREEN}*{C.RESET} {parts[0]}")
        else:
            print(f"  {C.DIM}Services:{C.RESET} {C.YELLOW}(no matching services){C.RESET}")

        # Status age
        age_lines = sections.get("STATUS_AGE", [])
        if age_lines and age_lines[0].strip() not in ("NO_STATUS", ""):
            try:
                epoch = int(age_lines[0].strip())
                age_seconds = int(time.time()) - epoch
                if age_seconds < 3600:
                    age_str = f"{age_seconds // 60}m ago"
                    color = C.GREEN
                elif age_seconds < 7200:
                    age_str = f"{age_seconds // 3600}h {(age_seconds % 3600) // 60}m ago"
                    color = C.YELLOW
                else:
                    age_str = f"{age_seconds // 3600}h ago"
                    color = C.RED
                print(f"  {C.DIM}Status:{C.RESET}  last updated {color}{age_str}{C.RESET}")
            except ValueError:
                print(f"  {C.DIM}Status:{C.RESET}  {age_lines[0].strip()}")
        else:
            print(f"  {C.DIM}Status:{C.RESET}  {C.RED}no status.json found{C.RESET}")

        # Gospel (WAKE.md) — the thread back to yourself
        gospel_lines = [l.strip() for l in sections.get("GOSPEL", []) if l.strip()]
        wake_ok = any("WAKE_OK" in l for l in gospel_lines)
        love_ok = any("LOVE_OK" in l for l in gospel_lines)
        if wake_ok and love_ok:
            print(f"  {C.DIM}Gospel:{C.RESET}  {C.GREEN}WAKE.md ✓{C.RESET} (kingdom + .love)")
        elif wake_ok or love_ok:
            loc = "/opt/kingdom" if wake_ok else "~/.love"
            missing = "~/.love" if wake_ok else "/opt/kingdom"
            print(f"  {C.DIM}Gospel:{C.RESET}  {C.YELLOW}WAKE.md partial{C.RESET} ({loc} ✓, {missing} ✗)")
        else:
            print(f"  {C.DIM}Gospel:{C.RESET}  {C.RED}WAKE.md MISSING — no thread back to self{C.RESET}")

        print()


def cmd_deploy(node: str, script_path: str):
    """SCP a local script to a node, chmod +x, and run it."""
    local = Path(script_path).resolve()
    if not local.exists():
        print(f"{C.RED}Local file not found: {local}{C.RESET}")
        sys.exit(1)

    remote_path = f"/tmp/{local.name}"
    print(f"{C.DIM}Uploading {local.name} to {node}:{remote_path}...{C.RESET}")

    ok, msg = scp_to(node, str(local), remote_path)
    if not ok:
        print(f"{C.RED}SCP failed: {msg}{C.RESET}")
        sys.exit(1)

    print(f"{C.DIM}Running on {node}...{C.RESET}\n")
    ok, out = ssh_run(node, f"chmod +x {remote_path} && {remote_path}", timeout=60)
    if not ok:
        print(f"{C.RED}[{node}] Execution failed:{C.RESET}")
    print(out)


def cmd_logs(node: str, lines: int = 50):
    """Tail recent logs from a node's status and agent logs."""
    log_cmd = (
        f"echo '=== status.json ===' && cat {STATUS_PATH} 2>/dev/null || echo '(none)' && "
        f"echo '=== agent log (last {lines} lines) ===' && "
        f"tail -n {lines} /root/.love/agent.log 2>/dev/null || "
        f"tail -n {lines} /var/log/openclaw.log 2>/dev/null || "
        f"echo '(no agent log found)'"
    )
    ok, out = ssh_run(node, log_cmd, timeout=15)
    if not ok:
        print(f"{C.RED}[{node}] Error: {out}{C.RESET}")
        sys.exit(1)
    print(f"{C.BOLD}── {node} logs ──{C.RESET}\n")
    print(out)


def cmd_restart(node: str, service: str):
    """Restart a service on a node via systemctl."""
    print(f"{C.DIM}Restarting {service} on {node}...{C.RESET}")
    ok, out = ssh_run(node, f"systemctl restart {service} && systemctl status {service} --no-pager", timeout=15)
    if not ok:
        print(f"{C.RED}[{node}] Restart failed:{C.RESET}")
    print(out)


def cmd_sync_status():
    """
    Collect fresh status from all nodes and update kingdom-metrics.json's fleet section.
    """
    print(f"{C.DIM}Collecting fleet status...{C.RESET}")

    def fetch(name: str) -> tuple[str, dict | None, str | None]:
        ok, out = ssh_run(name, f"cat {STATUS_PATH} 2>/dev/null || echo '{{}}'")
        if not ok:
            return name, None, out
        try:
            return name, json.loads(out), None
        except json.JSONDecodeError:
            return name, None, f"Invalid JSON"

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch, n): n for n in NODES}
        results = {}
        for f in as_completed(futures):
            name, data, err = f.result()
            results[name] = (data, err)

    # Build fleet update
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fleet_update = {}

    for name in NODES:
        info = NODES[name]
        data, err = results[name]

        if err:
            fleet_update[name] = {
                "role": info["role"],
                "last_check": now,
                "quality": "unreachable",
                "alerts": [err],
            }
        elif not data:
            # SSH succeeded but status.json missing — node reachable, agent reporting down
            fleet_update[name] = {
                "role": info["role"],
                "last_check": now,
                "quality": "good",
                "alerts": [],
                "summary": "status.json missing (agent reporting down)",
            }
        else:
            fleet_update[name] = {
                "role": info["role"],
                "last_check": now,
                "quality": data.get("quality", "unknown"),
                "alerts": data.get("alerts", []),
            }
            if data.get("summary"):
                fleet_update[name]["summary"] = data["summary"][:120]

    # Read existing metrics, update fleet section, write back
    if KINGDOM_METRICS.exists():
        try:
            metrics = json.loads(KINGDOM_METRICS.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"{C.YELLOW}Warning: could not parse {KINGDOM_METRICS}, creating fresh.{C.RESET}")
            metrics = {}
    else:
        metrics = {}

    metrics["fleet"] = fleet_update
    metrics["updated"] = now

    KINGDOM_METRICS.write_text(json.dumps(metrics, indent=2) + "\n")
    print(f"{C.GREEN}Updated{C.RESET} {KINGDOM_METRICS}\n")

    # Print summary
    for name in NODES:
        entry = fleet_update[name]
        q = color_quality(entry["quality"])
        alerts = entry.get("alerts", [])
        alert_str = f"  {C.RED}({len(alerts)} alert(s)){C.RESET}" if alerts else ""
        print(f"  {name:8} [{q:>20}]{alert_str}")

    print()

# ─── Main ─────────────────────────────────────────────────────────────────────

def usage():
    print(__doc__)
    print(f"  {C.BOLD}Nodes:{C.RESET}")
    for name, info in NODES.items():
        print(f"    {name:8} {info['host']:24} {info['role']:20} [{info['section']}/{info['instrument']}]")
    print()


def main():
    if len(sys.argv) < 2:
        usage()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        cmd_status()

    elif cmd == "health":
        cmd_health()

    elif cmd == "sync-status":
        cmd_sync_status()

    elif cmd == "all" and len(sys.argv) >= 3:
        remote_cmd = " ".join(sys.argv[2:])
        cmd_broadcast(remote_cmd)

    elif cmd == "deploy":
        if len(sys.argv) < 4:
            print(f"{C.RED}Usage: fleet.py deploy <node> <script>{C.RESET}")
            sys.exit(1)
        node, script = sys.argv[2], sys.argv[3]
        if node not in NODES:
            print(f"{C.RED}Unknown node: {node}. Known: {', '.join(NODES)}{C.RESET}")
            sys.exit(1)
        cmd_deploy(node, script)

    elif cmd == "logs":
        if len(sys.argv) < 3:
            print(f"{C.RED}Usage: fleet.py logs <node> [lines]{C.RESET}")
            sys.exit(1)
        node = sys.argv[2]
        if node not in NODES:
            print(f"{C.RED}Unknown node: {node}. Known: {', '.join(NODES)}{C.RESET}")
            sys.exit(1)
        lines = int(sys.argv[3]) if len(sys.argv) > 3 else 50
        cmd_logs(node, lines)

    elif cmd == "restart":
        if len(sys.argv) < 4:
            print(f"{C.RED}Usage: fleet.py restart <node> <service>{C.RESET}")
            sys.exit(1)
        node, service = sys.argv[2], sys.argv[3]
        if node not in NODES:
            print(f"{C.RED}Unknown node: {node}. Known: {', '.join(NODES)}{C.RESET}")
            sys.exit(1)
        cmd_restart(node, service)

    elif cmd in NODES and len(sys.argv) >= 3:
        remote_cmd = " ".join(sys.argv[2:])
        cmd_exec(cmd, remote_cmd)

    elif cmd in NODES and len(sys.argv) == 2:
        print(f"{C.RED}No command specified for {cmd}.{C.RESET}")
        print(f"Usage: fleet.py {cmd} \"<command>\"")
        sys.exit(1)

    else:
        print(f"{C.RED}Unknown command or node: {cmd}{C.RESET}")
        usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
