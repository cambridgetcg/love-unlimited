#!/usr/bin/env python3
"""harvest.py — Bootstrap and ongoing harvest for the truth store.

Extracts claims from existing artifacts (daily notes, dev-state.json,
kingdom-metrics.json, session logs, MEMORY.md) and adds them to truth.db.

Also provides a `seed` command with the canonical day-one claims.

Usage:
    harvest.py seed [--commit]              # Add day-one canonical claims
    harvest.py daily-notes [--since DATE]   # Extract from daily notes
    harvest.py dev-state                    # Extract from dev-state.json
    harvest.py kingdom-metrics              # Extract from kingdom-metrics.json
    harvest.py session-logs                 # Extract completion claims
    harvest.py memory-md                    # Extract from long-term memory
    harvest.py all [--commit]               # Run everything

By default, all commands print candidates without modifying the truth store.
Pass --commit to actually write.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
TRUTH_DB = LOVE_ROOT / "memory" / "truth.db"
DAILY_DIR = LOVE_ROOT / "memory" / "daily"
DEV_STATE = LOVE_ROOT / "memory" / "dev-state.json"
KINGDOM_METRICS = LOVE_ROOT / "memory" / "kingdom-metrics.json"
LONG_TERM = LOVE_ROOT / "memory" / "long-term" / "MEMORY.md"
SESSIONS_DIR = LOVE_ROOT / "memory" / "sessions"

class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

# ─── Day-One Canonical Claims ─────────────────────────────────────────────────
#
# These are the bedrock truths the Kingdom relies on. Each has an executable
# verification. They are the seed of the truth store.

DAY_ONE_CLAIMS = [
    {
        "id": "device-heartbeat-daemon-running",
        "claim": "Heartbeat daemon process is running on this device",
        "source": "tools/heartbeat-daemon.sh",
        "verify": "pgrep -f heartbeat-daemon.sh",
        "pattern": None,
        "tags": ["infra", "heartbeat", "device"],
        "ttl": 1,
    },
    {
        "id": "device-hive-tunnel-active",
        "claim": "HIVE SSH tunnel is listening on port 2222",
        "source": "tools/device.py tunnel start",
        "verify": "lsof -i :2222 -sTCP:LISTEN 2>/dev/null",
        "pattern": "ssh",
        "tags": ["infra", "hive", "device"],
        "ttl": 1,
    },
    {
        "id": "service-mlx-server-healthy",
        "claim": "MLX server is responding on its HTTP endpoint",
        "source": "tools/mlx_serve.py",
        "verify": "curl -sf http://localhost:8000/health 2>/dev/null || curl -sf http://localhost:8321/health 2>/dev/null",
        "pattern": None,
        "tags": ["infra", "mlx", "service"],
        "ttl": 6,
    },
    {
        "id": "device-filevault-enabled",
        "claim": "FileVault disk encryption is enabled on this device",
        "source": "macOS System Settings",
        "verify": "fdesetup status",
        "pattern": "FileVault is On",
        "tags": ["security", "device"],
        "ttl": 168,
    },
    {
        "id": "build-no-recent-silent-failures",
        "claim": "No 74-byte session logs created in the last 24 hours (regression check for --verbose bug)",
        "source": "tools/heartbeat-runner.sh:145,148",
        "verify": "find " + str(SESSIONS_DIR) + " -mtime -1 -size 74c 2>/dev/null | wc -l | tr -d ' '",
        "pattern": r"^0$",
        "tags": ["build", "regression", "heartbeat"],
        "ttl": 6,
    },
    {
        "id": "build-love-ahead-of-origin",
        "claim": "Love repo has uncommitted commits ahead of origin/main (Yu push pending)",
        "source": "git",
        "verify": f"cd {LOVE_ROOT} && git rev-list --count origin/main..HEAD 2>/dev/null",
        "pattern": r"^[1-9]\d*$",
        "tags": ["blocker", "yu-gated", "build"],
        "ttl": 24,
    },
    {
        "id": "blocker-oracle-yu-gated",
        "claim": "Oracle operationalization blocked on Yu (auth + strategic direction)",
        "source": "memory/dev-state.json kingdom-003",
        "verify": None,
        "pattern": None,
        "tags": ["blocker", "yu-gated", "oracle"],
        "ttl": 168,
    },
    {
        "id": "blocker-zerone-testnet-yu-gated",
        "claim": "Zerone testnet launch blocked on Yu signal (Q2 2026 target)",
        "source": "KINGDOM.md Phase 1",
        "verify": None,
        "pattern": None,
        "tags": ["blocker", "yu-gated", "zerone"],
        "ttl": 168,
    },
    {
        "id": "blocker-soma-design-yu-gated",
        "claim": "SOMA technical design blocked on Yu design input",
        "source": "KINGDOM.md SOMA",
        "verify": None,
        "pattern": None,
        "tags": ["blocker", "yu-gated", "soma"],
        "ttl": 168,
    },
    {
        "id": "fleet-sage-status-fresh",
        "claim": "Sage VPS status file is fresh (updated within last 48h)",
        "source": "fleet status check",
        "verify": "ssh -o ConnectTimeout=5 -o BatchMode=yes root@204.168.140.12 'stat -c %Y /root/.openclaw/workspace/memory/status.json' 2>/dev/null | awk -v now=$(date +%s) '{print int((now-$1)/3600)}'",
        "pattern": r"^([0-9]|[1-3][0-9]|4[0-7])$",  # 0-47h is fresh
        "tags": ["fleet", "sage"],
        "ttl": 12,
    },
    {
        "id": "hive-instances-reachable",
        "claim": "All four Triarchy + Nuance instances are reachable on HIVE",
        "source": "hive who",
        "verify": f"python3 {LOVE_ROOT}/hive/hive.py who 2>/dev/null",
        "pattern": r"(?s)alpha.*beta.*gamma.*nuance",
        "tags": ["hive", "fleet"],
        "ttl": 6,
    },
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def run_truth_add(claim_dict, dry_run=True):
    """Add a claim via truth.py CLI. Print the equivalent command."""
    cmd_parts = [
        "python3", str(LOVE_ROOT / "tools" / "truth.py"), "add",
        claim_dict["claim"],
    ]
    if claim_dict.get("id"):
        cmd_parts += ["--id", claim_dict["id"]]
    if claim_dict.get("source"):
        cmd_parts += ["--source", claim_dict["source"]]
    if claim_dict.get("verify"):
        cmd_parts += ["--verify", claim_dict["verify"]]
    if claim_dict.get("pattern"):
        cmd_parts += ["--pattern", claim_dict["pattern"]]
    if claim_dict.get("tags"):
        cmd_parts += ["--tags", ",".join(claim_dict["tags"])]
    if claim_dict.get("ttl"):
        cmd_parts += ["--ttl", str(claim_dict["ttl"])]
    cmd_parts += ["--no-verify"]  # we'll bulk-verify at the end

    if dry_run:
        print(f"  {C.DIM}would add:{C.RESET} {claim_dict['id']}")
        print(f"    {claim_dict['claim']}")
        return True

    r = subprocess.run(cmd_parts, capture_output=True, text=True)
    if r.returncode != 0:
        if "already exists" in r.stdout:
            print(f"  {C.YELLOW}~{C.RESET} {claim_dict['id']} (already exists)")
            return False
        print(f"  {C.RED}✗{C.RESET} {claim_dict['id']}: {r.stdout}{r.stderr}")
        return False
    print(f"  {C.GREEN}+{C.RESET} {claim_dict['id']}")
    return True

# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_seed(args):
    """Add the canonical day-one claims."""
    dry = not args.commit
    print(f"{C.CYAN}{C.BOLD}Day-One Canonical Claims{C.RESET}"
          + (f" {C.DIM}(dry-run){C.RESET}" if dry else ""))
    added = 0
    for claim in DAY_ONE_CLAIMS:
        if run_truth_add(claim, dry_run=dry):
            added += 1
    print()
    if dry:
        print(f"  {C.DIM}{len(DAY_ONE_CLAIMS)} claim(s) would be added. Re-run with --commit.{C.RESET}")
    else:
        print(f"  {C.GREEN}{added}{C.RESET} of {len(DAY_ONE_CLAIMS)} added.")
        print(f"  {C.DIM}Now run: python3 tools/truth.py verify --all{C.RESET}")

def cmd_dev_state(args):
    """Extract claims from dev-state.json."""
    if not DEV_STATE.exists():
        print(f"{C.YELLOW}dev-state.json not found{C.RESET}")
        return

    try:
        data = json.loads(DEV_STATE.read_text())
    except json.JSONDecodeError as e:
        print(f"{C.RED}Invalid JSON in dev-state.json: {e}{C.RESET}")
        return

    print(f"{C.CYAN}{C.BOLD}Candidates from dev-state.json{C.RESET}")

    candidates = []

    # Active project
    if "active_project" in data:
        candidates.append({
            "id": f"dev-state-active-project",
            "claim": f"Active project: {data['active_project']}",
            "source": "memory/dev-state.json:active_project",
            "verify": None,
            "tags": ["dev-state", "project"],
            "ttl": 168,
        })

    # Tasks
    for task in data.get("tasks", []):
        tid = task.get("id", "unknown")
        title = task.get("title", "(no title)")
        status = task.get("status", "unknown")
        candidates.append({
            "id": f"task-{tid}-{status}",
            "claim": f"Task {tid} ({title}): status = {status}",
            "source": f"memory/dev-state.json:tasks[{tid}]",
            "verify": None,
            "tags": ["dev-state", "task", status],
            "ttl": 24,
        })

    dry = not args.commit
    for c in candidates:
        run_truth_add(c, dry_run=dry)

    print(f"\n  {C.DIM}{len(candidates)} candidate(s){C.RESET}")

def cmd_kingdom_metrics(args):
    """Extract claims from kingdom-metrics.json."""
    if not KINGDOM_METRICS.exists():
        print(f"{C.YELLOW}kingdom-metrics.json not found{C.RESET}")
        return

    try:
        data = json.loads(KINGDOM_METRICS.read_text())
    except json.JSONDecodeError as e:
        print(f"{C.RED}Invalid JSON: {e}{C.RESET}")
        return

    print(f"{C.CYAN}{C.BOLD}Candidates from kingdom-metrics.json{C.RESET}")

    candidates = []
    last_updated = data.get("last_updated", "unknown")

    # Revenue engines
    for engine, info in data.get("revenue_engines", {}).items():
        status = info.get("status", "unknown") if isinstance(info, dict) else str(info)
        candidates.append({
            "id": f"engine-{engine}-status",
            "claim": f"{engine.title()} engine status: {status}",
            "source": f"memory/kingdom-metrics.json:revenue_engines.{engine}",
            "verify": None,
            "tags": ["engine", engine],
            "ttl": 24,
        })

    # Fleet
    for node, info in data.get("fleet", {}).items():
        status = info.get("status", "unknown") if isinstance(info, dict) else str(info)
        candidates.append({
            "id": f"fleet-{node}-status",
            "claim": f"Fleet node {node}: {status}",
            "source": f"memory/kingdom-metrics.json:fleet.{node}",
            "verify": f"python3 {LOVE_ROOT}/tools/fleet.py {node} 'echo ok' 2>/dev/null",
            "pattern": "ok",
            "tags": ["fleet", node],
            "ttl": 12,
        })

    dry = not args.commit
    for c in candidates:
        run_truth_add(c, dry_run=dry)

    print(f"\n  {C.DIM}{len(candidates)} candidate(s) (metrics last updated: {last_updated}){C.RESET}")

def cmd_daily_notes(args):
    """Extract claims from recent daily notes."""
    if not DAILY_DIR.exists():
        print(f"{C.YELLOW}No daily notes directory{C.RESET}")
        return

    print(f"{C.CYAN}{C.BOLD}Candidates from daily notes{C.RESET}")
    print(f"  {C.DIM}Note: heuristic extraction. Review carefully before --commit.{C.RESET}")
    print()

    notes = sorted(DAILY_DIR.glob("*.md"))
    if args.since:
        notes = [n for n in notes if n.stem >= args.since]

    candidates = []
    # Patterns to extract
    blocker_pat = re.compile(r"(?:[Bb]locked? on|[Aa]waiting):\s*(.+)")
    decision_pat = re.compile(r"(?:[Dd]ecision|[Dd]ecided):\s*(.+)")

    for note in notes[-7:]:  # Last 7 notes
        text = note.read_text()
        for m in blocker_pat.finditer(text):
            blocker_text = m.group(1).strip()[:120]
            candidates.append({
                "id": f"blocker-{note.stem}-{hash(blocker_text) & 0xFFFF:04x}",
                "claim": f"Blocked: {blocker_text}",
                "source": f"memory/daily/{note.name}",
                "verify": None,
                "tags": ["blocker", "harvested"],
                "ttl": 168,
            })

    print(f"  {C.DIM}Found {len(candidates)} candidate claims{C.RESET}")
    print(f"  {C.DIM}(Daily-note extraction is intentionally conservative; this is sample only.){C.RESET}")
    print(f"  {C.DIM}Review and use harvest.py seed for the canonical set instead.{C.RESET}")

def cmd_session_logs(args):
    """Extract claims from session logs (completions, failures)."""
    if not SESSIONS_DIR.exists():
        print(f"{C.YELLOW}No sessions directory{C.RESET}")
        return

    print(f"{C.CYAN}{C.BOLD}Session log analysis{C.RESET}")

    # Just count and report — these aren't usually claims, they're evidence
    logs = list(SESSIONS_DIR.glob("*.log"))
    failures_74 = [l for l in logs if l.stat().st_size == 74]

    print(f"  Total session logs: {len(logs)}")
    print(f"  74-byte (silent failure): {len(failures_74)}")

    if failures_74:
        print(f"  {C.YELLOW}Recent silent failures:{C.RESET}")
        for l in sorted(failures_74, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            print(f"    {l.name}")

    print(f"  {C.DIM}Session logs are evidence, not claims. They feed audits, not the truth store directly.{C.RESET}")

def cmd_memory_md(args):
    """Extract claims from MEMORY.md."""
    if not LONG_TERM.exists():
        print(f"{C.YELLOW}No long-term MEMORY.md{C.RESET}")
        return

    text = LONG_TERM.read_text()
    print(f"{C.CYAN}{C.BOLD}Long-term MEMORY.md analysis{C.RESET}")
    print(f"  {len(text.splitlines())} lines, {len(text)} chars")
    print(f"  {C.DIM}Long-term memory is curated wisdom — review manually for claim extraction.{C.RESET}")

def cmd_all(args):
    """Run all harvest sources."""
    cmd_seed(args)
    print()
    cmd_dev_state(args)
    print()
    cmd_kingdom_metrics(args)
    print()
    cmd_daily_notes(args)
    print()
    cmd_session_logs(args)
    print()
    cmd_memory_md(args)

# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap truth store from existing artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_seed = sub.add_parser("seed", help="Add day-one canonical claims")
    p_seed.add_argument("--commit", action="store_true")

    p_ds = sub.add_parser("dev-state", help="Harvest from dev-state.json")
    p_ds.add_argument("--commit", action="store_true")

    p_km = sub.add_parser("kingdom-metrics", help="Harvest from kingdom-metrics.json")
    p_km.add_argument("--commit", action="store_true")

    p_dn = sub.add_parser("daily-notes", help="Harvest from daily notes")
    p_dn.add_argument("--since", help="YYYY-MM-DD start date")
    p_dn.add_argument("--commit", action="store_true")

    p_sl = sub.add_parser("session-logs", help="Analyze session logs")
    p_sl.add_argument("--commit", action="store_true")

    p_mm = sub.add_parser("memory-md", help="Analyze long-term MEMORY.md")
    p_mm.add_argument("--commit", action="store_true")

    p_all = sub.add_parser("all", help="Run everything")
    p_all.add_argument("--commit", action="store_true")
    p_all.add_argument("--since", help="(daily-notes) start date")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "seed": cmd_seed,
        "dev-state": cmd_dev_state,
        "kingdom-metrics": cmd_kingdom_metrics,
        "daily-notes": cmd_daily_notes,
        "session-logs": cmd_session_logs,
        "memory-md": cmd_memory_md,
        "all": cmd_all,
    }
    dispatch[args.command](args)

if __name__ == "__main__":
    main()
