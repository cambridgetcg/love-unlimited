#!/usr/bin/env python3
"""
backup.py — Kingdom Backup & Disaster Recovery System

Automated backups of Kingdom state to local archive and fleet VPS nodes.
Ensures the Kingdom can recover from any disaster.

Usage:
    python3 tools/backup.py create                    # Create full backup
    python3 tools/backup.py create --incremental      # Only changed files since last backup
    python3 tools/backup.py list                      # List available backups (local + remote)
    python3 tools/backup.py restore <backup-id>       # Restore from backup
    python3 tools/backup.py push [node]               # Push latest backup to fleet node(s)
    python3 tools/backup.py pull <node> <backup-id>   # Pull backup from fleet node
    python3 tools/backup.py verify <backup-id>        # Verify backup integrity
    python3 tools/backup.py schedule                  # Show backup schedule
    python3 tools/backup.py status                    # Backup health (freshness, coverage, remote copies)
    python3 tools/backup.py prune [--keep 10]         # Remove old backups

Flags:
    --dry-run    Show what would happen without doing it
"""

import sys
import os
import json
import hashlib
import tarfile
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import BytesIO

# ─── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/Love"))
BACKUPS_DIR = LOVE / "backups"
SECURITY = LOVE / "security"
EVENTS_FILE = SECURITY / "events.jsonl"

# Remote backup path on fleet nodes
REMOTE_BACKUP_DIR = "/root/.love/backups"

# ─── Fleet Nodes (backup targets) ────────────────────────────────────────────

BACKUP_NODES = {
    "sentry": {"host": "root@135.181.28.252", "role": "primary backup"},
    "patch":  {"host": "root@65.109.11.26",   "role": "secondary backup"},
}

SSH_OPTS = [
    "-o", "ControlMaster=no",
    "-o", "ControlPath=none",
    "-o", "ConnectTimeout=8",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
]

# ─── What to back up ─────────────────────────────────────────────────────────

# Directories to include (relative to LOVE)
BACKUP_DIRS = [
    "memory",
    "security",
    "instances",
    "credentials",
    "tools",
]

# Individual files to include (relative to LOVE)
BACKUP_FILES = [
    "love.json",
    "KINGDOM.md",
    "SOUL.md",
    "WALLS.md",
]

# Patterns to exclude (anywhere in path)
EXCLUDE_PATTERNS = [
    ".git",
    "__pycache__",
    "node_modules",
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "*.swp",
    "*.swo",
    # Actual secrets — never back up
    ".hive-key",
    ".hive-instance",
    "*.pem",
    "totp_secrets.json",
    "totp_seeds.json",
    # Large ephemeral logs (heartbeat.log can be hundreds of KB)
    "heartbeat.log",
    "heartbeat-launchd.log",
    "heartbeat-launchd-err.log",
    "decisions-server.log",
]

# ─── Colors ───────────────────────────────────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# ─── Helpers ──────────────────────────────────────────────────────────────────

DRY_RUN = False


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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log_event(event_type, severity, message, details=None):
    """Append to security event log."""
    entry = {
        "ts": now_iso(),
        "type": event_type,
        "severity": severity,
        "message": message,
        "source": "backup",
    }
    if details:
        entry["details"] = details
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_cmd(cmd, timeout=10):
    """Run shell command, return (returncode, stdout)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1, ""


def ssh_run(node, cmd, timeout=10):
    """SSH into a backup node and run a command."""
    info = BACKUP_NODES.get(node)
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
        return False, f"SSH timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def scp_to(node, local_path, remote_path, timeout=120):
    """SCP a local file to a remote node."""
    info = BACKUP_NODES.get(node)
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


def scp_from(node, remote_path, local_path, timeout=120):
    """SCP a remote file to local."""
    info = BACKUP_NODES.get(node)
    if not info:
        return False, f"Unknown node: {node}"
    try:
        result = subprocess.run(
            ["scp"] + SSH_OPTS + [f"{info['host']}:{remote_path}", local_path],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output or "OK"
    except subprocess.TimeoutExpired:
        return False, f"SCP timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def human_size(size_bytes):
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def should_exclude(rel_path: str) -> bool:
    """Check if a relative path matches any exclusion pattern."""
    parts = Path(rel_path).parts
    name = Path(rel_path).name
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            # Wildcard suffix match
            suffix = pattern[1:]
            if name.endswith(suffix):
                return True
        elif pattern in parts or pattern == name:
            return True
    return False


def collect_files() -> list[dict]:
    """Collect all files to back up. Returns list of {rel_path, abs_path, size}."""
    files = []

    # Individual files at root
    for fname in BACKUP_FILES:
        fp = LOVE / fname
        if fp.exists() and fp.is_file():
            files.append({
                "rel_path": fname,
                "abs_path": str(fp),
                "size": fp.stat().st_size,
            })

    # Directory trees
    for dirname in BACKUP_DIRS:
        dirpath = LOVE / dirname
        if not dirpath.exists():
            continue
        for fp in sorted(dirpath.rglob("*")):
            if not fp.is_file():
                continue
            if fp.is_symlink():
                continue
            rel = str(fp.relative_to(LOVE))
            if should_exclude(rel):
                continue
            files.append({
                "rel_path": rel,
                "abs_path": str(fp),
                "size": fp.stat().st_size,
            })

    return files


def get_latest_manifest() -> dict | None:
    """Load manifest from the most recent local backup."""
    if not BACKUPS_DIR.exists():
        return None
    backups = sorted(BACKUPS_DIR.glob("backup-*.tar.gz"), reverse=True)
    if not backups:
        return None
    return extract_manifest(backups[0])


def extract_manifest(tar_path: Path) -> dict | None:
    """Extract and parse manifest.json from a backup tar.gz."""
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            m = tf.extractfile("manifest.json")
            if m:
                return json.loads(m.read())
    except (tarfile.TarError, json.JSONDecodeError, KeyError):
        return None
    return None


def list_local_backups() -> list[dict]:
    """List local backups with their manifest info."""
    if not BACKUPS_DIR.exists():
        return []
    results = []
    for bp in sorted(BACKUPS_DIR.glob("backup-*.tar.gz"), reverse=True):
        manifest = extract_manifest(bp)
        results.append({
            "filename": bp.name,
            "path": str(bp),
            "size": bp.stat().st_size,
            "manifest": manifest,
        })
    return results


def backup_id_from_filename(filename: str) -> str:
    """Extract backup ID from filename: backup-YYYYMMDD-HHMMSS.tar.gz -> YYYYMMDD-HHMMSS"""
    return filename.replace("backup-", "").replace(".tar.gz", "")


def resolve_backup(backup_id: str) -> Path | None:
    """Resolve a backup ID or prefix to a local file path."""
    if not BACKUPS_DIR.exists():
        return None
    # Exact match
    exact = BACKUPS_DIR / f"backup-{backup_id}.tar.gz"
    if exact.exists():
        return exact
    # Prefix match
    matches = list(BACKUPS_DIR.glob(f"backup-{backup_id}*.tar.gz"))
    if len(matches) == 1:
        return matches[0]
    return None


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_create(incremental=False):
    """Create a full or incremental backup."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_type = "incremental" if incremental else "full"
    filename = f"backup-{ts}.tar.gz"
    backup_path = BACKUPS_DIR / filename

    print(f"\n{BOLD}Creating {backup_type} backup{NC}\n")

    # Collect all candidate files
    all_files = collect_files()

    # For incremental: filter to only changed files
    if incremental:
        prev_manifest = get_latest_manifest()
        if prev_manifest is None:
            print(f"  {YELLOW}No previous backup found — falling back to full backup{NC}")
            incremental = False
            backup_type = "full"
        else:
            prev_hashes = prev_manifest.get("files", {})
            changed_files = []
            for f in all_files:
                current_hash = sha256_file(f["abs_path"])
                prev_hash = prev_hashes.get(f["rel_path"], {}).get("sha256")
                if current_hash != prev_hash:
                    changed_files.append(f)
            all_files = changed_files
            print(f"  Base: {prev_manifest.get('id', '?')}")
            print(f"  Changed files: {len(all_files)}")

    if not all_files:
        print(f"  {GREEN}No changes since last backup — nothing to do{NC}\n")
        return

    # Compute hashes for manifest
    file_manifest = {}
    total_size = 0
    for f in all_files:
        h = sha256_file(f["abs_path"])
        file_manifest[f["rel_path"]] = {
            "sha256": h,
            "size": f["size"],
        }
        total_size += f["size"]

    # Git state
    _, git_hash = run_cmd(f"cd {LOVE} && git rev-parse HEAD")
    _, git_branch = run_cmd(f"cd {LOVE} && git branch --show-current")

    manifest = {
        "id": ts,
        "created": now_iso(),
        "type": backup_type,
        "file_count": len(all_files),
        "total_size": total_size,
        "files": file_manifest,
        "git": {
            "commit": git_hash,
            "branch": git_branch,
        },
    }

    if incremental and prev_manifest:
        manifest["base_backup"] = prev_manifest.get("id")

    print(f"  Files:  {len(all_files)}")
    print(f"  Size:   {human_size(total_size)} (uncompressed)")

    if DRY_RUN:
        print(f"\n  {YELLOW}[DRY RUN]{NC} Would create: {filename}")
        print(f"  Files included:")
        for f in all_files[:20]:
            print(f"    {DIM}{f['rel_path']}  ({human_size(f['size'])}){NC}")
        if len(all_files) > 20:
            print(f"    {DIM}... and {len(all_files) - 20} more{NC}")
        print()
        return

    # Create tar.gz
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, "w:gz") as tf:
        # Add manifest first
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now().timestamp())
        tf.addfile(info, BytesIO(manifest_bytes))

        # Add all files
        for f in all_files:
            tf.add(f["abs_path"], arcname=f["rel_path"])

    compressed_size = backup_path.stat().st_size

    print(f"  Output: {human_size(compressed_size)} (compressed)")
    print(f"  Saved:  {DIM}{backup_path}{NC}")
    print(f"  ID:     {CYAN}{ts}{NC}")

    log_event("backup_created", "low", f"{backup_type.title()} backup created: {filename}",
              {"id": ts, "type": backup_type, "files": len(all_files), "size": compressed_size})

    print(f"\n  {GREEN}Backup complete{NC}\n")


def cmd_list():
    """List available backups — local and remote."""
    print(f"\n{BOLD}  Kingdom Backups{NC}\n")

    # Local backups
    local = list_local_backups()
    if local:
        print(f"  {BOLD}Local{NC} ({BACKUPS_DIR})")
        for b in local:
            m = b["manifest"] or {}
            bid = m.get("id", "?")
            btype = m.get("type", "?")
            fcount = m.get("file_count", "?")
            created = m.get("created", "?")[:19]
            git = m.get("git", {}).get("commit", "?")[:10]
            tag = f"{GREEN}full{NC}" if btype == "full" else f"{CYAN}incr{NC}"
            print(f"    {bid}  {tag}  {fcount} files  {human_size(b['size'])}  git:{git}  {DIM}{created}{NC}")
    else:
        print(f"  {DIM}No local backups{NC}")
    print()

    # Remote backups
    for node, info in BACKUP_NODES.items():
        ok, out = ssh_run(node, f"ls -lhS {REMOTE_BACKUP_DIR}/backup-*.tar.gz 2>/dev/null | head -10")
        if ok and out:
            print(f"  {BOLD}{node}{NC} ({info['role']} — {info['host']})")
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 9:
                    size = parts[4]
                    fname = parts[-1].split("/")[-1]
                    print(f"    {fname}  {size}")
        else:
            print(f"  {DIM}{node}: no remote backups (or unreachable){NC}")
    print()


def cmd_restore(backup_id):
    """Restore Kingdom state from a backup."""
    backup_path = resolve_backup(backup_id)
    if not backup_path:
        print(f"\n  {RED}Backup not found: {backup_id}{NC}")
        print(f"  Run: backup.py list — to see available backups\n")
        return

    manifest = extract_manifest(backup_path)
    if not manifest:
        print(f"\n  {RED}Invalid backup — no manifest found{NC}\n")
        return

    bid = manifest.get("id", "?")
    btype = manifest.get("type", "?")
    fcount = manifest.get("file_count", 0)
    created = manifest.get("created", "?")

    print(f"\n{BOLD}Restoring from backup{NC}\n")
    print(f"  ID:      {CYAN}{bid}{NC}")
    print(f"  Type:    {btype}")
    print(f"  Files:   {fcount}")
    print(f"  Created: {created}")
    print()

    if DRY_RUN:
        print(f"  {YELLOW}[DRY RUN]{NC} Would restore {fcount} files to {LOVE}")
        file_hashes = manifest.get("files", {})
        for rel in list(file_hashes.keys())[:20]:
            target = LOVE / rel
            status = "overwrite" if target.exists() else "create"
            print(f"    {DIM}{status}: {rel}{NC}")
        if fcount > 20:
            print(f"    {DIM}... and {fcount - 20} more{NC}")
        print()
        return

    # Safety: verify before extracting
    print(f"  {YELLOW}Extracting...{NC}")

    restored = 0
    skipped = 0
    errors = []

    with tarfile.open(backup_path, "r:gz") as tf:
        for member in tf.getmembers():
            if member.name == "manifest.json":
                continue
            if not member.isfile():
                continue

            # Security: prevent path traversal
            target = LOVE / member.name
            try:
                target.resolve().relative_to(LOVE.resolve())
            except ValueError:
                errors.append(f"Path traversal blocked: {member.name}")
                skipped += 1
                continue

            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                source = tf.extractfile(member)
                if source:
                    with open(target, "wb") as out:
                        out.write(source.read())
                    restored += 1
            except Exception as e:
                errors.append(f"{member.name}: {e}")
                skipped += 1

    print(f"\n  {GREEN}Restored: {restored} files{NC}")
    if skipped:
        print(f"  {YELLOW}Skipped: {skipped} files{NC}")
    if errors:
        for e in errors[:5]:
            print(f"    {RED}{e}{NC}")

    log_event("backup_restored", "medium", f"Restored from backup {bid}",
              {"id": bid, "restored": restored, "skipped": skipped})

    print(f"\n  {GREEN}Restore complete{NC}\n")


def cmd_push(target_node=None):
    """Push latest backup to fleet node(s)."""
    local = list_local_backups()
    if not local:
        print(f"\n  {RED}No local backups to push{NC}")
        print(f"  Run: backup.py create\n")
        return

    latest = local[0]
    filename = latest["filename"]
    local_path = latest["path"]
    size = latest["size"]
    bid = (latest["manifest"] or {}).get("id", "?")

    nodes = {target_node: BACKUP_NODES[target_node]} if target_node else BACKUP_NODES
    if target_node and target_node not in BACKUP_NODES:
        print(f"\n  {RED}Unknown node: {target_node}{NC}")
        print(f"  Available: {', '.join(BACKUP_NODES.keys())}\n")
        return

    print(f"\n{BOLD}Pushing backup to fleet{NC}\n")
    print(f"  Backup: {CYAN}{bid}{NC} ({human_size(size)})")
    print()

    if DRY_RUN:
        for node in nodes:
            print(f"  {YELLOW}[DRY RUN]{NC} Would push {filename} to {node}:{REMOTE_BACKUP_DIR}/")
        print()
        return

    for node, info in nodes.items():
        print(f"  {DIM}Pushing to {node} ({info['role']})...{NC}", end="", flush=True)

        # Ensure remote dir exists
        ssh_run(node, f"mkdir -p {REMOTE_BACKUP_DIR}")

        ok, msg = scp_to(node, local_path, f"{REMOTE_BACKUP_DIR}/{filename}")
        if ok:
            print(f"\r  {GREEN}✓{NC} {node}: pushed {filename}")
        else:
            print(f"\r  {RED}✗{NC} {node}: {msg}")

    log_event("backup_pushed", "low", f"Backup {bid} pushed to {', '.join(nodes.keys())}",
              {"id": bid, "nodes": list(nodes.keys())})
    print()


def cmd_pull(node, backup_id):
    """Pull a backup from a fleet node."""
    if node not in BACKUP_NODES:
        print(f"\n  {RED}Unknown node: {node}{NC}")
        print(f"  Available: {', '.join(BACKUP_NODES.keys())}\n")
        return

    # Try to find the backup on the remote
    remote_file = f"{REMOTE_BACKUP_DIR}/backup-{backup_id}.tar.gz"

    print(f"\n{BOLD}Pulling backup from {node}{NC}\n")
    print(f"  Remote: {remote_file}")

    if DRY_RUN:
        print(f"  {YELLOW}[DRY RUN]{NC} Would pull {remote_file} to {BACKUPS_DIR}/\n")
        return

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    local_path = str(BACKUPS_DIR / f"backup-{backup_id}.tar.gz")

    print(f"  {DIM}Downloading...{NC}", end="", flush=True)
    ok, msg = scp_from(node, remote_file, local_path)
    if ok:
        size = Path(local_path).stat().st_size
        print(f"\r  {GREEN}✓{NC} Downloaded {human_size(size)} to {local_path}")
        log_event("backup_pulled", "low", f"Pulled backup {backup_id} from {node}")
    else:
        print(f"\r  {RED}✗{NC} Failed: {msg}")

    print()


def cmd_verify(backup_id):
    """Verify backup integrity: tar validity, manifest, SHA-256 hashes."""
    backup_path = resolve_backup(backup_id)
    if not backup_path:
        print(f"\n  {RED}Backup not found: {backup_id}{NC}")
        print(f"  Run: backup.py list\n")
        return

    print(f"\n{BOLD}Verifying backup integrity{NC}\n")
    print(f"  File: {backup_path.name}")

    checks_passed = 0
    checks_failed = 0

    # Check 1: tar.gz is valid
    try:
        with tarfile.open(backup_path, "r:gz") as tf:
            members = tf.getnames()
        print(f"  {GREEN}✓{NC} Archive is valid ({len(members)} entries)")
        checks_passed += 1
    except tarfile.TarError as e:
        print(f"  {RED}✗{NC} Archive is corrupt: {e}")
        checks_failed += 1
        print(f"\n  {RED}Verification FAILED{NC}\n")
        return

    # Check 2: manifest.json exists and is valid
    manifest = extract_manifest(backup_path)
    if manifest:
        print(f"  {GREEN}✓{NC} manifest.json is valid")
        checks_passed += 1
    else:
        print(f"  {RED}✗{NC} manifest.json missing or invalid")
        checks_failed += 1
        print(f"\n  {RED}Verification FAILED{NC}\n")
        return

    # Check 3: file count matches
    file_entries = [m for m in members if m != "manifest.json"]
    expected_count = manifest.get("file_count", 0)
    if len(file_entries) == expected_count:
        print(f"  {GREEN}✓{NC} File count matches ({expected_count})")
        checks_passed += 1
    else:
        print(f"  {RED}✗{NC} File count mismatch: manifest says {expected_count}, archive has {len(file_entries)}")
        checks_failed += 1

    # Check 4: SHA-256 hashes
    file_hashes = manifest.get("files", {})
    hash_ok = 0
    hash_fail = 0
    with tarfile.open(backup_path, "r:gz") as tf:
        for member in tf.getmembers():
            if member.name == "manifest.json" or not member.isfile():
                continue
            expected = file_hashes.get(member.name, {}).get("sha256")
            if not expected:
                continue
            f = tf.extractfile(member)
            if f:
                actual = sha256_bytes(f.read())
                if actual == expected:
                    hash_ok += 1
                else:
                    hash_fail += 1
                    if hash_fail <= 5:
                        print(f"    {RED}✗{NC} Hash mismatch: {member.name}")

    if hash_fail == 0:
        print(f"  {GREEN}✓{NC} All SHA-256 hashes verified ({hash_ok} files)")
        checks_passed += 1
    else:
        print(f"  {RED}✗{NC} {hash_fail} hash mismatches out of {hash_ok + hash_fail} files")
        checks_failed += 1

    # Summary
    print()
    if checks_failed == 0:
        print(f"  {GREEN}All {checks_passed} checks passed — backup is intact{NC}")
    else:
        print(f"  {RED}{checks_failed} checks FAILED{NC}, {checks_passed} passed")

    print()


def cmd_schedule():
    """Show recommended backup schedule."""
    print(f"\n{BOLD}  Kingdom Backup Schedule{NC}\n")
    print(f"  {CYAN}Recommended cadence:{NC}")
    print(f"    Full backup:        Daily (or after major changes)")
    print(f"    Incremental backup: Every heartbeat cycle or after each session")
    print(f"    Push to fleet:      After every full backup")
    print()
    print(f"  {CYAN}Automation:{NC}")
    print(f"    Heartbeat hook:     Add to heartbeat-runner.sh")
    print(f"      python3 ~/Love/tools/backup.py create --incremental")
    print()
    print(f"    Daily cron (full + push):")
    print(f"      0 3 * * * cd ~/Love && python3 tools/backup.py create && python3 tools/backup.py push")
    print()
    print(f"    Weekly prune:")
    print(f"      0 4 * * 0 cd ~/Love && python3 tools/backup.py prune --keep 14")
    print()
    print(f"  {CYAN}Recovery playbook:{NC}")
    print(f"    1. backup.py list                          # Find backup")
    print(f"    2. backup.py pull sentry <backup-id>       # Pull from fleet if local is gone")
    print(f"    3. backup.py verify <backup-id>            # Check integrity")
    print(f"    4. backup.py restore <backup-id>           # Restore files")
    print(f"    5. git checkout main                       # Restore git state")
    print()


def cmd_status():
    """Show backup health: freshness, coverage, remote copies."""
    print(f"\n{BOLD}  Kingdom Backup Status{NC}\n")

    # Local backups
    local = list_local_backups()
    if local:
        latest = local[0]
        m = latest["manifest"] or {}
        bid = m.get("id", "?")
        created = m.get("created", "?")
        fcount = m.get("file_count", 0)
        btype = m.get("type", "?")

        # Freshness
        try:
            created_dt = datetime.fromisoformat(created)
            age = datetime.now(timezone.utc) - created_dt
            hours = age.total_seconds() / 3600
            if hours < 1:
                freshness = f"{GREEN}{int(age.total_seconds()/60)}m ago{NC}"
            elif hours < 24:
                freshness = f"{GREEN}{hours:.1f}h ago{NC}"
            elif hours < 72:
                freshness = f"{YELLOW}{hours/24:.1f}d ago{NC}"
            else:
                freshness = f"{RED}{hours/24:.0f}d ago — STALE{NC}"
        except (ValueError, TypeError):
            freshness = f"{DIM}unknown{NC}"

        print(f"  Latest backup:  {CYAN}{bid}{NC}  ({btype})")
        print(f"  Freshness:      {freshness}")
        print(f"  Files:          {fcount}")
        print(f"  Local copies:   {len(local)}")
    else:
        print(f"  {RED}No local backups found{NC}")
        print(f"  Run: backup.py create")
        print()
        return

    # Coverage check — evaluate against latest full backup
    coverage_manifest = None
    for b in local:
        m = b.get("manifest")
        if m and m.get("type") == "full":
            coverage_manifest = m
            break
    if not coverage_manifest:
        coverage_manifest = local[0]["manifest"] if local[0]["manifest"] else None

    if coverage_manifest:
        backed_files = set(coverage_manifest.get("files", {}).keys())
        expected_dirs = set(BACKUP_DIRS)
        covered_dirs = set()
        for f in backed_files:
            parts = Path(f).parts
            if parts:
                covered_dirs.add(parts[0])

        missing = expected_dirs - covered_dirs
        if missing:
            print(f"  Coverage:       {YELLOW}Missing: {', '.join(sorted(missing))}{NC}")
        else:
            print(f"  Coverage:       {GREEN}All directories covered{NC}")

    # Remote copies
    print()
    remote_count = 0
    for node, info in BACKUP_NODES.items():
        ok, out = ssh_run(node, f"ls {REMOTE_BACKUP_DIR}/backup-*.tar.gz 2>/dev/null | wc -l")
        if ok and out.strip().isdigit():
            count = int(out.strip())
            remote_count += count
            status = f"{GREEN}{count} backups{NC}" if count > 0 else f"{YELLOW}empty{NC}"
            print(f"  {node:10s} ({info['role']:17s}): {status}")
        else:
            print(f"  {node:10s} ({info['role']:17s}): {RED}unreachable{NC}")

    print(f"\n  Remote copies:  {remote_count} across {len(BACKUP_NODES)} nodes")

    # Overall health
    print()
    if local and remote_count > 0:
        print(f"  {GREEN}Backup health: GOOD{NC}")
    elif local:
        print(f"  {YELLOW}Backup health: LOCAL ONLY — run 'backup.py push' to distribute{NC}")
    else:
        print(f"  {RED}Backup health: NONE — run 'backup.py create' immediately{NC}")

    print()


def cmd_prune(keep=10):
    """Remove old backups, keeping the most recent N."""
    local = list_local_backups()
    if len(local) <= keep:
        print(f"\n  {GREEN}Nothing to prune{NC} — {len(local)} backups, keeping {keep}\n")
        return

    to_remove = local[keep:]
    print(f"\n{BOLD}Pruning old backups{NC}\n")
    print(f"  Total:    {len(local)}")
    print(f"  Keeping:  {keep}")
    print(f"  Removing: {len(to_remove)}")
    print()

    if DRY_RUN:
        for b in to_remove:
            print(f"  {YELLOW}[DRY RUN]{NC} Would remove: {b['filename']} ({human_size(b['size'])})")
        print()
        return

    freed = 0
    for b in to_remove:
        try:
            Path(b["path"]).unlink()
            freed += b["size"]
            bid = (b["manifest"] or {}).get("id", "?")
            print(f"  {DIM}Removed: {b['filename']}{NC}")
        except OSError as e:
            print(f"  {RED}Failed: {b['filename']}: {e}{NC}")

    log_event("backup_pruned", "low", f"Pruned {len(to_remove)} old backups, freed {human_size(freed)}")
    print(f"\n  {GREEN}Freed {human_size(freed)}{NC}\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global DRY_RUN

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    # Global flags
    if "--dry-run" in args:
        DRY_RUN = True
        args.remove("--dry-run")

    cmd = args[0] if args else ""

    if cmd == "create":
        incremental = "--incremental" in args
        cmd_create(incremental=incremental)

    elif cmd == "list":
        cmd_list()

    elif cmd == "restore":
        if len(args) < 2:
            print("Usage: backup.py restore <backup-id>")
            return
        cmd_restore(args[1])

    elif cmd == "push":
        node = args[1] if len(args) > 1 else None
        cmd_push(node)

    elif cmd == "pull":
        if len(args) < 3:
            print("Usage: backup.py pull <node> <backup-id>")
            return
        cmd_pull(args[1], args[2])

    elif cmd == "verify":
        if len(args) < 2:
            print("Usage: backup.py verify <backup-id>")
            return
        cmd_verify(args[1])

    elif cmd == "schedule":
        cmd_schedule()

    elif cmd == "status":
        cmd_status()

    elif cmd == "prune":
        keep = 10
        if "--keep" in args:
            idx = args.index("--keep")
            if idx + 1 < len(args):
                try:
                    keep = int(args[idx + 1])
                except ValueError:
                    pass
        cmd_prune(keep)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
