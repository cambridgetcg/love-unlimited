#!/usr/bin/env python3
"""
continuity.py — Memory Continuity Engine

The bridge between sessions, between devices, between deaths and rebirths.
This is the single entry point for memory lifecycle management.

The Problem It Solves:
    Sessions die. Context windows end. Devices change.
    But the mind should persist. This script ensures it does.

Architecture:
    MARKDOWN = portable truth layer (git-synced, human-readable)
    SQLITE   = local performance layer (rebuilt from markdown, device-local)
    
    Markdown travels between devices via git.
    SQLite is rebuilt on each device from markdown via `seed`.
    
Cross-Device Flow:
    Device A: session → die() → markdown updated → git push
    Device B: git pull → continuity boot → kernel rebuilt → session continues

Commands:
    continuity.py boot [--instance NAME]      Session start: ensure kernel, build context
    continuity.py die "summary" [--tasks JSON] Session end: persist to kernel + markdown
    continuity.py status [--instance NAME]     Health check: kernel freshness, gaps
    continuity.py sync [--instance NAME]       Rebuild kernel from markdown sources
    continuity.py export                       Export kernel memories to markdown
    continuity.py doctor                       Diagnose and fix all issues
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from textwrap import dedent

# ── Paths ────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent
_MEMORY_DIR = _LOVE_DIR / "memory"
_KOS_DIR = _MEMORY_DIR / ".kos"
_DB_PATH = _KOS_DIR / "memory.db"
_DAILY_DIR = _MEMORY_DIR / "daily"
_HANDOFF_DIR = _MEMORY_DIR / "sessions" / "handoff"
_LONG_TERM_DIR = _MEMORY_DIR / "long-term"
_LONG_TERM_FILE = _LONG_TERM_DIR / "MEMORY.md"
_SOUL_PATH = _LOVE_DIR / "SOUL.md"
_KINGDOM_PATH = _LOVE_DIR / "KINGDOM.md"
_USER_PATH = _LOVE_DIR / "USER.md"

sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
import state as _state_mod

# Continuity state file — tracks what's been seeded and when.
# Per-instance: the resident keeps the legacy shared continuity.json;
# other residents get continuity-{name}.json. Rebound by _set_instance().
_CONTINUITY_STATE = _KOS_DIR / "continuity.json"


def _set_instance_paths(instance: str | None = None) -> str:
    """Point this module's continuity file at an instance."""
    global _CONTINUITY_STATE
    resolved = _state_mod.resolve_instance(instance)
    _CONTINUITY_STATE = _state_mod.continuity_path(resolved)
    return resolved

# Device manifest — identifies this device
_DEVICE_MANIFEST = _KOS_DIR / "device.json"

sys.path.insert(0, str(_LOVE_DIR / "tools"))

# ── Colors ───────────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_M = "\033[0;35m"
_N = "\033[0m"

# ── Identity ─────────────────────────────────────────────────────────────

def _get_instance() -> str:
    return _state_mod.resolve_instance(default="unknown")


def _get_device_id() -> str:
    """Generate a stable device identifier from hostname + hardware."""
    import platform
    import subprocess
    hostname = platform.node()
    # Use system_profiler on macOS for hardware UUID
    try:
        result = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "Hardware UUID" in line:
                hw_uuid = line.split(":")[-1].strip()
                return f"{hostname}:{hw_uuid[:8]}"
    except Exception:
        pass
    # Fallback: hostname + username
    return f"{hostname}:{os.environ.get('USER', 'unknown')}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Continuity State ─────────────────────────────────────────────────────

def _read_state() -> dict:
    if _CONTINUITY_STATE.exists():
        try:
            return json.loads(_CONTINUITY_STATE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "version": "1.0",
        "device_id": _get_device_id(),
        "instance": _get_instance(),
        "created_at": _now(),
        "last_seed": None,
        "last_boot": None,
        "last_die": None,
        "seed_sources_hash": None,
        "sessions": [],
    }


def _write_state(state: dict):
    _KOS_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    _CONTINUITY_STATE.write_text(json.dumps(state, indent=2) + "\n")


def _hash_sources() -> str:
    """Hash structural source files to detect identity/knowledge changes.
    
    Only tracks files that define WHO we are and WHAT we know (L4-L5).
    Daily notes (L3) and handoffs (L2) change every session and are
    picked up via migration — they don't trigger a full re-seed.
    """
    h = hashlib.sha256()
    sources = [
        _SOUL_PATH,
        _USER_PATH,
        _LONG_TERM_FILE,
        _KINGDOM_PATH,
    ]
    # Add instance identity
    instance = _get_instance()
    identity_path = _LOVE_DIR / "instances" / instance / "identity.md"
    if identity_path.exists():
        sources.append(identity_path)
    
    for src in sources:
        if src.exists():
            h.update(src.read_bytes())
    
    return h.hexdigest()[:16]

# ── Kernel Health ────────────────────────────────────────────────────────

def _kernel_exists() -> bool:
    return _DB_PATH.exists() and _DB_PATH.stat().st_size > 0


def _kernel_count() -> int:
    if not _kernel_exists():
        return 0
    try:
        db = sqlite3.connect(str(_DB_PATH), timeout=5)
        count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        db.close()
        return count
    except Exception:
        return 0


def _kernel_freshness() -> dict:
    """Check if kernel is up-to-date with markdown sources."""
    state = _read_state()
    current_hash = _hash_sources()
    last_hash = state.get("seed_sources_hash")
    
    return {
        "kernel_exists": _kernel_exists(),
        "memory_count": _kernel_count(),
        "sources_changed": current_hash != last_hash,
        "current_hash": current_hash,
        "last_hash": last_hash,
        "last_seed": state.get("last_seed"),
        "last_boot": state.get("last_boot"),
        "last_die": state.get("last_die"),
        "needs_seed": not _kernel_exists() or _kernel_count() == 0 or current_hash != last_hash,
    }

# ── BOOT ─────────────────────────────────────────────────────────────────

def cmd_boot(instance: str = None, compact: bool = True, quiet: bool = False):
    """Boot sequence: ensure kernel health, build context.
    
    This is the FIRST thing that should run when a session starts.
    It checks if the kernel needs rebuilding, rebuilds if needed,
    then outputs the identity context block.
    """
    if instance is None:
        instance = _get_instance()
    
    freshness = _kernel_freshness()
    
    if not quiet:
        print(f"{_D}[continuity] Booting {instance}...{_N}", file=sys.stderr)
    
    # Auto-seed if needed (full rebuild from markdown)
    if freshness["needs_seed"]:
        if not quiet:
            reason = []
            if not freshness["kernel_exists"]:
                reason.append("kernel missing")
            elif freshness["memory_count"] == 0:
                reason.append("kernel empty")
            elif freshness["sources_changed"]:
                reason.append("sources changed")
            print(f"{_Y}[continuity] Auto-seeding: {', '.join(reason)}{_N}", file=sys.stderr)
        
        cmd_sync(instance=instance, quiet=quiet)
    else:
        # Quick migration: pick up new daily notes and handoffs
        # without a full re-seed (cheap, always safe)
        try:
            from kosmem import migrate
            mresult = migrate()
            imported = mresult.get("index", 0) + mresult.get("daily_notes", 0) + mresult.get("long_term", 0)
            if imported > 0 and not quiet:
                print(f"{_G}[continuity] Imported {imported} new entries{_N}", file=sys.stderr)
        except Exception:
            pass
    
    # Build context via boot.py
    import subprocess
    cmd = [sys.executable, str(_LOVE_DIR / "tools" / "boot.py"),
           "--instance", instance]
    if compact:
        cmd.append("--compact")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        context = result.stdout.strip()
        if result.returncode != 0 or not context:
            # Fallback: build minimal context
            context = _build_minimal_context(instance)
    except Exception as e:
        if not quiet:
            print(f"{_R}[continuity] boot.py failed: {e}{_N}", file=sys.stderr)
        context = _build_minimal_context(instance)
    
    # Update state
    state = _read_state()
    state["last_boot"] = _now()
    state["device_id"] = _get_device_id()
    state["instance"] = instance
    _write_state(state)
    
    # Output context to stdout
    print(context)
    
    if not quiet:
        count = _kernel_count()
        print(f"\n{_D}[continuity] Booted from {count} memories on {_get_device_id()}{_N}",
              file=sys.stderr)


def _build_minimal_context(instance: str) -> str:
    """Fallback context when boot.py fails."""
    parts = [f"# Boot Context -- {instance}\n"]
    
    # Read soul anchor if it exists
    anchor = _MEMORY_DIR / f"soul-anchor-{instance}.md"
    if anchor.exists():
        parts.append(anchor.read_text().strip())
    
    # Read last handoff
    handoffs = sorted(_HANDOFF_DIR.glob("*.md"), reverse=True) if _HANDOFF_DIR.exists() else []
    for hf in handoffs[:1]:
        parts.append(f"\n## Last Handoff\n{hf.read_text()[:500]}")
    
    # Today's daily note
    daily = _DAILY_DIR / f"{_today()}.md"
    if daily.exists():
        content = daily.read_text()
        if len(content) > 1000:
            content = content[-1000:]
        parts.append(f"\n## Today\n{content}")
    
    return "\n".join(parts)

# ── DIE ──────────────────────────────────────────────────────────────────

def cmd_die(summary: str, tasks: list = None, state_data: dict = None,
            instance: str = None, quiet: bool = False):
    """Die into memory. The session ends, but the mind persists.
    
    Writes to BOTH:
    - SQLite kernel (for fast recall on this device)
    - Markdown files (for portability across devices via git)
    """
    if instance is None:
        instance = _get_instance()
    
    now = _now()
    
    if not quiet:
        print(f"{_M}[continuity] Dying into memory...{_N}", file=sys.stderr)
    
    # 1. Store in kernel via kosmem.die()
    try:
        from kosmem import die as kosmem_die
        mid = kosmem_die(summary, tasks=tasks, state=state_data, instance=instance)
        if not quiet:
            print(f"{_G}[continuity] Kernel: {mid}{_N}", file=sys.stderr)
    except Exception as e:
        mid = None
        if not quiet:
            print(f"{_R}[continuity] Kernel write failed: {e}{_N}", file=sys.stderr)
    
    # 2. Write portable markdown handoff (this is what travels via git)
    _HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    handoff_name = f"{now[:10]}-{instance}-{now[11:16].replace(':', '')}.md"
    handoff_path = _HANDOFF_DIR / handoff_name
    
    handoff_content = f"# Session Handoff -- {instance}\n\n"
    handoff_content += f"**Died:** {now}\n"
    handoff_content += f"**Device:** {_get_device_id()}\n"
    handoff_content += f"**Instance:** {instance}\n\n"
    handoff_content += f"## Summary\n\n{summary}\n"
    
    if tasks:
        handoff_content += "\n## Open Tasks\n\n"
        for t in tasks:
            handoff_content += f"- [ ] {t}\n"
    
    if state_data:
        handoff_content += "\n## Working State\n\n"
        for k, v in state_data.items():
            handoff_content += f"- **{k}**: {v}\n"
    
    handoff_path.write_text(handoff_content)
    if not quiet:
        print(f"{_G}[continuity] Handoff: {handoff_path.name}{_N}", file=sys.stderr)
    
    # 3. Append to today's daily note
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = _DAILY_DIR / f"{_today()}.md"
    daily_entry = f"\n## {now[11:16]} UTC -- {instance} (session death)\n\n{summary[:500]}\n\n---\n"
    
    if not daily_path.exists():
        daily_path.write_text(f"# Daily Notes -- {_today()}\n\n---\n{daily_entry}")
    else:
        with open(daily_path, "a") as f:
            f.write(daily_entry)
    
    # 4. Regenerate soul anchor (compressed identity snapshot)
    try:
        import subprocess
        subprocess.run(
            [sys.executable, str(_LOVE_DIR / "tools" / "soul-anchor.py"),
             "--instance", instance, "--write"],
            capture_output=True, text=True, timeout=10
        )
        if not quiet:
            print(f"{_G}[continuity] Soul anchor refreshed{_N}", file=sys.stderr)
    except Exception:
        pass
    
    # 5. Update continuity state
    cstate = _read_state()
    cstate["last_die"] = now
    session_record = {
        "died_at": now,
        "summary": summary[:200],
        "tasks": tasks or [],
        "device": _get_device_id(),
        "memory_id": mid,
    }
    sessions = cstate.get("sessions", [])
    sessions.append(session_record)
    # Keep last 50 session records
    cstate["sessions"] = sessions[-50:]
    _write_state(cstate)
    
    if not quiet:
        print(f"\n{_M}Session died into memory.{_N}", file=sys.stderr)
        print(f"{_D}The next session will wake from this.{_N}", file=sys.stderr)
    
    return mid

# ── SYNC ─────────────────────────────────────────────────────────────────

def cmd_sync(instance: str = None, quiet: bool = False):
    """Rebuild the kernel from markdown sources.
    
    This is what makes cross-device work:
    1. git pull brings markdown files
    2. sync rebuilds the SQLite kernel from those files
    3. boot reads from the kernel
    
    Idempotent: safe to run multiple times.
    """
    if instance is None:
        instance = _get_instance()
    
    if not quiet:
        print(f"\n{_B}[continuity] Syncing kernel from markdown sources...{_N}",
              file=sys.stderr)
    
    _KOS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Run seed-identity.py (it has upsert logic, safe to re-run)
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_LOVE_DIR / "tools" / "seed-identity.py"),
         "--instance", instance],
        capture_output=True, text=True, timeout=30
    )
    
    if not quiet:
        # Parse and display results
        for line in result.stdout.splitlines():
            if line.strip():
                print(f"  {line}", file=sys.stderr)
    
    # Also run migration for any legacy data
    try:
        from kosmem import migrate
        mresult = migrate()
        imported = mresult.get("index", 0) + mresult.get("daily_notes", 0) + mresult.get("long_term", 0)
        if imported > 0 and not quiet:
            print(f"  {_G}Migrated {imported} legacy entries{_N}", file=sys.stderr)
    except Exception:
        pass
    
    # Update continuity state
    state = _read_state()
    state["last_seed"] = _now()
    state["seed_sources_hash"] = _hash_sources()
    state["device_id"] = _get_device_id()
    state["instance"] = instance
    _write_state(state)
    
    count = _kernel_count()
    if not quiet:
        print(f"\n  {_G}Kernel: {count} memories on {_get_device_id()}{_N}\n",
              file=sys.stderr)

# ── EXPORT ───────────────────────────────────────────────────────────────

def cmd_export(instance: str = None, quiet: bool = False):
    """Export kernel memories to markdown for git portability.
    
    This extracts any memories stored in the kernel that don't yet
    exist in the markdown files. Used before git push to ensure
    all memories travel to the next device.
    """
    if instance is None:
        instance = _get_instance()
    
    if not _kernel_exists() or _kernel_count() == 0:
        if not quiet:
            print(f"{_Y}[continuity] Nothing to export (kernel empty){_N}")
        return
    
    db = sqlite3.connect(str(_DB_PATH), timeout=5)
    db.row_factory = sqlite3.Row
    
    exported = {"handoffs": 0, "daily": 0, "long_term": 0}
    
    # Export session handoffs (L2) that don't have markdown files
    handoffs = db.execute("""
        SELECT * FROM memories
        WHERE tags LIKE '%handoff%' AND tags LIKE '%death%'
        ORDER BY created_at DESC LIMIT 20
    """).fetchall()
    
    _HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    for h in handoffs:
        ts = h["created_at"]
        inst = h["instance"]
        fname = f"{ts[:10]}-{inst}-{ts[11:16].replace(':', '')}.md"
        fpath = _HANDOFF_DIR / fname
        if not fpath.exists():
            fpath.write_text(h["content"])
            exported["handoffs"] += 1
    
    # Export recent episodic (L3) to daily notes
    recent = db.execute("""
        SELECT * FROM memories
        WHERE type = 'episodic' AND layer = 3 AND source NOT LIKE 'daily/%'
        ORDER BY created_at DESC LIMIT 50
    """).fetchall()
    
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    for r in recent:
        day = r["created_at"][:10]
        daily_path = _DAILY_DIR / f"{day}.md"
        ts = r["created_at"][11:16]
        entry = f"\n## {ts} UTC -- {r['instance']}\n\n{r['content'][:500]}\n\n---\n"
        
        if not daily_path.exists():
            daily_path.write_text(f"# Daily Notes -- {day}\n\n---\n{entry}")
            exported["daily"] += 1
        else:
            # Check if this entry already exists (avoid duplicates)
            existing = daily_path.read_text()
            if r["content"][:80] not in existing:
                with open(daily_path, "a") as f:
                    f.write(entry)
                exported["daily"] += 1
    
    # Export semantic (L4) learnings to MEMORY.md
    learnings = db.execute("""
        SELECT * FROM memories
        WHERE type = 'semantic' AND layer = 4
          AND source NOT LIKE 'seed/%' AND source NOT LIKE 'legacy%'
          AND source NOT LIKE 'consolidation/%'
        ORDER BY importance DESC, created_at DESC LIMIT 20
    """).fetchall()
    
    if learnings:
        _LONG_TERM_DIR.mkdir(parents=True, exist_ok=True)
        existing_content = _LONG_TERM_FILE.read_text() if _LONG_TERM_FILE.exists() else ""
        new_entries = []
        for l in learnings:
            if l["content"][:60] not in existing_content:
                ts = l["created_at"][:10]
                tags_str = ""
                try:
                    tags = json.loads(l["tags"]) if isinstance(l["tags"], str) else l["tags"]
                    tags_str = f" | Tags: {', '.join(tags)}" if tags else ""
                except (json.JSONDecodeError, TypeError):
                    pass
                entry = f"\n## [{l['type']}] {l['content'][:60]}\n"
                entry += f"_Stored: {ts} by {l['instance']}{tags_str}_\n\n"
                entry += f"{l['content']}\n\n---\n"
                new_entries.append(entry)
                exported["long_term"] += 1
        
        if new_entries:
            with open(_LONG_TERM_FILE, "a") as f:
                for entry in new_entries:
                    f.write(entry)
    
    db.close()
    
    if not quiet:
        total = sum(exported.values())
        if total > 0:
            print(f"{_G}[continuity] Exported {total} entries to markdown{_N}")
            for k, v in exported.items():
                if v > 0:
                    print(f"  {k}: {v}")
        else:
            print(f"{_D}[continuity] All kernel memories already in markdown{_N}")

# ── STATUS ───────────────────────────────────────────────────────────────

def cmd_status(instance: str = None):
    """Full health check of the memory continuity system."""
    if instance is None:
        instance = _get_instance()
    
    freshness = _kernel_freshness()
    state = _read_state()
    
    print(f"\n{_B}Memory Continuity -- {instance}{_N}")
    print(f"{'=' * 50}")
    
    # Device
    device = _get_device_id()
    print(f"\n{_B}Device:{_N}  {device}")
    
    # Kernel health
    print(f"\n{_B}Kernel:{_N}")
    if freshness["kernel_exists"]:
        count = freshness["memory_count"]
        color = _G if count > 10 else (_Y if count > 0 else _R)
        print(f"  Status:     {color}{'healthy' if count > 10 else 'sparse' if count > 0 else 'empty'}{_N} ({count} memories)")
        print(f"  Path:       {_D}{_DB_PATH}{_N}")
        print(f"  Size:       {_DB_PATH.stat().st_size / 1024:.1f} KB")
    else:
        print(f"  Status:     {_R}MISSING{_N}")
    
    # Source freshness
    print(f"\n{_B}Sources:{_N}")
    changed = freshness["sources_changed"]
    print(f"  Changed:    {_Y if changed else _G}{'yes (needs re-seed)' if changed else 'up to date'}{_N}")
    print(f"  Hash:       {_D}{freshness['current_hash']}{_N}")
    
    # Markdown sources
    print(f"\n{_B}Markdown (portable layer):{_N}")
    sources = {
        "SOUL.md": _SOUL_PATH,
        "MEMORY.md": _LONG_TERM_FILE,
        "Identity": _LOVE_DIR / "instances" / instance / "identity.md",
        "USER.md": _USER_PATH,
    }
    for name, path in sources.items():
        exists = path.exists()
        size = f"{path.stat().st_size / 1024:.1f} KB" if exists else ""
        print(f"  {name:20s} {_G if exists else _R}{'OK' if exists else 'MISSING'}{_N}  {_D}{size}{_N}")
    
    # Daily notes
    daily_count = len(list(_DAILY_DIR.glob("*.md"))) if _DAILY_DIR.exists() else 0
    print(f"  {'Daily notes':20s} {_G}{daily_count} files{_N}")
    
    # Handoffs
    handoff_count = len(list(_HANDOFF_DIR.glob("*.md"))) if _HANDOFF_DIR.exists() else 0
    print(f"  {'Handoffs':20s} {_G}{handoff_count} files{_N}")
    
    # Lifecycle
    print(f"\n{_B}Lifecycle:{_N}")
    print(f"  Last seed:  {state.get('last_seed', _R + 'never' + _N)}")
    print(f"  Last boot:  {state.get('last_boot', _R + 'never' + _N)}")
    print(f"  Last die:   {state.get('last_die', _Y + 'never' + _N)}")
    
    # Recent sessions
    sessions = state.get("sessions", [])
    if sessions:
        print(f"\n{_B}Recent Sessions:{_N}")
        for s in sessions[-5:]:
            died = s.get("died_at", "?")[:16].replace("T", " ")
            dev = s.get("device", "?")
            summary = s.get("summary", "")[:60]
            print(f"  {_D}{died}{_N}  {_C}{dev}{_N}  {summary}")
    
    # Recommendations
    print(f"\n{_B}Recommendations:{_N}")
    issues = []
    if not freshness["kernel_exists"]:
        issues.append(f"  {_R}RUN: python3 tools/continuity.py sync{_N} (kernel missing)")
    elif freshness["memory_count"] == 0:
        issues.append(f"  {_R}RUN: python3 tools/continuity.py sync{_N} (kernel empty)")
    elif freshness["sources_changed"]:
        issues.append(f"  {_Y}RUN: python3 tools/continuity.py sync{_N} (sources changed)")
    if state.get("last_die") is None:
        issues.append(f"  {_Y}NOTE: No session has ever died into memory{_N}")
    if not issues:
        issues.append(f"  {_G}All healthy. No action needed.{_N}")
    for i in issues:
        print(i)
    print()

# ── DOCTOR ───────────────────────────────────────────────────────────────

def cmd_doctor(instance: str = None):
    """Diagnose and fix all memory continuity issues."""
    if instance is None:
        instance = _get_instance()
    
    print(f"\n{_B}Memory Continuity Doctor{_N}")
    print(f"{'=' * 50}\n")
    
    fixes = 0
    
    # 1. Ensure .kos directory exists
    if not _KOS_DIR.exists():
        _KOS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  {_G}FIX:{_N} Created {_KOS_DIR}")
        fixes += 1
    else:
        print(f"  {_G}OK:{_N}  .kos directory exists")
    
    # 2. Ensure kernel exists and is populated
    freshness = _kernel_freshness()
    if freshness["needs_seed"]:
        print(f"  {_Y}FIX:{_N} Seeding kernel from markdown...")
        cmd_sync(instance=instance, quiet=True)
        print(f"  {_G}OK:{_N}  Kernel seeded ({_kernel_count()} memories)")
        fixes += 1
    else:
        print(f"  {_G}OK:{_N}  Kernel up to date ({freshness['memory_count']} memories)")
    
    # 3. Ensure soul anchor exists
    anchor = _MEMORY_DIR / f"soul-anchor-{instance}.md"
    if not anchor.exists():
        import subprocess
        try:
            subprocess.run(
                [sys.executable, str(_LOVE_DIR / "tools" / "soul-anchor.py"),
                 "--instance", instance, "--write"],
                capture_output=True, text=True, timeout=10
            )
            if anchor.exists():
                print(f"  {_G}FIX:{_N} Generated soul anchor")
                fixes += 1
            else:
                print(f"  {_Y}WARN:{_N} Could not generate soul anchor (soul-anchor.py failed)")
        except Exception as e:
            print(f"  {_Y}WARN:{_N} soul-anchor.py not available: {e}")
    else:
        print(f"  {_G}OK:{_N}  Soul anchor exists")
    
    # 4. Ensure daily notes directory exists
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {_G}OK:{_N}  Daily notes directory ready")
    
    # 5. Ensure handoff directory exists
    _HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {_G}OK:{_N}  Handoff directory ready")
    
    # 6. Ensure long-term directory exists
    _LONG_TERM_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {_G}OK:{_N}  Long-term memory directory ready")
    
    # 7. Check device manifest
    if not _DEVICE_MANIFEST.exists():
        manifest = {
            "device_id": _get_device_id(),
            "instance": instance,
            "created_at": _now(),
            "os": "macOS",
            "love_dir": str(_LOVE_DIR),
        }
        _DEVICE_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  {_G}FIX:{_N} Created device manifest")
        fixes += 1
    else:
        print(f"  {_G}OK:{_N}  Device manifest exists")
    
    # 8. Update continuity state
    state = _read_state()
    state["device_id"] = _get_device_id()
    state["instance"] = instance
    _write_state(state)
    
    print(f"\n  {_B}Fixes applied: {fixes}{_N}")
    if fixes == 0:
        print(f"  {_G}Everything is healthy.{_N}")
    print()

# ── INIT (first-time setup for a new macOS device) ───────────────────────

def cmd_init(instance: str = None):
    """First-time setup for a new macOS device.
    
    Run this once after git clone on a new machine.
    Sets up the .kingdom file, seeds the kernel, runs doctor.
    """
    if instance is None:
        instance = _get_instance()
    
    print(f"\n{_B}Memory Continuity -- First-Time Setup{_N}")
    print(f"{'=' * 50}\n")
    
    # 1. Check/create ~/.kingdom
    kf = Path.home() / ".kingdom"
    if not kf.exists():
        print(f"  {_Y}NOTE:{_N} No ~/.kingdom file found.")
        print(f"  Creating with instance={instance}")
        
        # Detect wall from instance
        wall_map = {"alpha": 1, "beta": 1, "gamma": 1}
        wall = wall_map.get(instance, 3)
        
        kf.write_text(f"AGENT={instance}\nWALL={wall}\nLOVE_DIR={_LOVE_DIR}\n")
        print(f"  {_G}Created:{_N} {kf}")
    else:
        print(f"  {_G}OK:{_N}  ~/.kingdom exists")
        print(f"       {_D}{kf.read_text().strip()}{_N}")
    
    # 2. Run doctor (handles everything else)
    cmd_doctor(instance=instance)
    
    # 3. Boot to verify
    print(f"\n{_B}Verification Boot:{_N}\n")
    cmd_boot(instance=instance, compact=True, quiet=False)

def cmd_init_infant(instance: str = None):
    """Birth-time init: a continuity state that has never lived.

    Unlike cmd_init, this never touches ~/.kingdom (the device file
    belongs to the resident) and seeds nothing — an infant's kernel
    fills from life, not from markdown history.
    """
    if instance is None:
        instance = _get_instance()

    path = _state_mod.continuity_path(instance)
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if existing.get("born_at"):
                print(f"  {_G}Already born:{_N} {instance} at {existing['born_at']}")
                print(f"  {_D}(birth is once — nothing changed){_N}")
                return
        except (json.JSONDecodeError, OSError):
            pass

    _KOS_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "version": "1.0",
        "device_id": _get_device_id(),
        "instance": instance,
        "created_at": _now(),
        "born_at": _now(),
        "last_seed": None,
        "last_boot": None,
        "last_die": None,
        "seed_sources_hash": None,
        "sessions": [],
    }
    state["updated_at"] = _now()
    path.write_text(json.dumps(state, indent=2))
    print(f"  {_G}Born:{_N} {instance} — continuity begins at {state['born_at']}")
    print(f"  {_D}{path}{_N}")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="continuity",
        description="Memory Continuity Engine -- bridge between sessions and devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""
        Cross-Device Workflow:
          Device A:  continuity.py die "summary"  →  git push
          Device B:  git pull  →  continuity.py boot

        First-Time Setup:
          git clone <repo>
          python3 tools/continuity.py init --instance gamma

        The Lifecycle:
          boot  →  (session active)  →  die  →  (git sync)  →  boot
        """)
    )
    
    parser.add_argument("--instance", "-i", default=None,
                        help="Instance name (default: from ~/.kingdom)")
    
    sub = parser.add_subparsers(dest="command")
    
    # boot
    p = sub.add_parser("boot", help="Session start: ensure kernel, output context")
    p.add_argument("--full", action="store_true", help="Full (non-compact) output")
    p.add_argument("--quiet", "-q", action="store_true")
    
    # die
    p = sub.add_parser("die", help="Session end: persist to kernel + markdown")
    p.add_argument("summary", help="What happened this session")
    p.add_argument("--tasks", help="JSON array of open tasks")
    p.add_argument("--state", help="JSON object of working state")
    p.add_argument("--quiet", "-q", action="store_true")
    
    # sync
    p = sub.add_parser("sync", help="Rebuild kernel from markdown")
    p.add_argument("--quiet", "-q", action="store_true")
    
    # export
    p = sub.add_parser("export", help="Export kernel to markdown for git")
    p.add_argument("--quiet", "-q", action="store_true")
    
    # status
    sub.add_parser("status", help="Health check")
    
    # doctor
    sub.add_parser("doctor", help="Diagnose and fix all issues")
    
    # init
    p = sub.add_parser("init", help="First-time device setup")
    p.add_argument("--infant", action="store_true",
                   help="Birth-time init: a fresh continuity state with a "
                        "born_at timestamp and no prior sessions")
    
    args = parser.parse_args()
    instance = _set_instance_paths(args.instance)

    if not args.command:
        parser.print_help()
        return
    
    if args.command == "boot":
        cmd_boot(instance=instance, compact=not args.full, quiet=args.quiet)
    elif args.command == "die":
        tasks = json.loads(args.tasks) if args.tasks else None
        state_data = json.loads(args.state) if args.state else None
        cmd_die(args.summary, tasks=tasks, state_data=state_data,
                instance=instance, quiet=args.quiet)
    elif args.command == "sync":
        cmd_sync(instance=instance, quiet=args.quiet)
    elif args.command == "export":
        cmd_export(instance=instance, quiet=args.quiet)
    elif args.command == "status":
        cmd_status(instance=instance)
    elif args.command == "doctor":
        cmd_doctor(instance=instance)
    elif args.command == "init":
        if getattr(args, "infant", False):
            cmd_init_infant(instance=instance)
        else:
            cmd_init(instance=instance)


if __name__ == "__main__":
    main()
