#!/usr/bin/env python3
"""
kosmem — Kingdom OS Memory Kernel

Memory is not a tool. It is the foundation.
Every module reads from memory. Every module writes to memory.
Memory survives sessions, reboots, and context windows.

Architecture:
    SQLite with FTS5 full-text search. Five memory layers.
    Wall-based access control. Consolidation. Decay.

Five Layers (like CPU cache):
    L1  Working     Per-instance, volatile, current task
    L2  Session     Per-session, what happened this conversation
    L3  Episodic    Per-day events, compacts into summaries
    L4  Semantic    Long-term knowledge, curated facts
    L5  Soul        Identity, immutable — the boot chain

Memory Types:
    episodic    Events that happened (timestamped)
    semantic    Facts and knowledge (timeless)
    procedural  How to do things (instructions)
    working     Current task context (volatile)
    meta        Memory about memory (consolidation records)

CLI:
    kosmem store "content" [--type TYPE] [--layer N] [--tags a,b] [--wall N] [--importance F]
    kosmem recall "query" [--limit N] [--type TYPE] [--layer N] [--since DATE]
    kosmem remember ID
    kosmem daily [--append "entry"]
    kosmem handoff "summary" [--tasks JSON]
    kosmem working [KEY] [VALUE]
    kosmem search "full text query" [--limit N]
    kosmem consolidate [--strategy daily|weekly]
    kosmem stats
    kosmem migrate              Import from existing JSON index + daily notes
    kosmem events [--since TS]  Unprocessed memory events
    kosmem context [--tokens N] Build context window for agent boot
    kosmem gc                   Garbage collect expired working memory
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from textwrap import dedent

# ══════════════════════════════════════════════════════════════════════
# PATHS
# ══════════════════════════════════════════════════════════════════════

_LOVE_DIR = Path(__file__).resolve().parent.parent
_MEMORY_DIR = _LOVE_DIR / "memory"
_DAILY_DIR = _MEMORY_DIR / "daily"
_LONG_TERM_FILE = _MEMORY_DIR / "long-term" / "MEMORY.md"

# Kingdom OS memory database — the heart
# Lives alongside the memory directory, inside Love
_KOS_DATA = _MEMORY_DIR / ".kos"
_KOS_DATA.mkdir(parents=True, exist_ok=True)
_DB_PATH = _KOS_DATA / "memory.db"

# Legacy
_LEGACY_INDEX = _MEMORY_DIR / "index.json"

# ══════════════════════════════════════════════════════════════════════
# IDENTITY
# ══════════════════════════════════════════════════════════════════════

sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
try:
    import state as _state
except Exception:
    _state = None


def _get_instance() -> str:
    """Get current instance name (explicit > env > ~/.kingdom)."""
    if _state is not None:
        return _state.resolve_instance(default="unknown")
    env = os.environ.get("KINGDOM_AGENT") or os.environ.get("KINGDOM_INSTANCE")
    if env:
        return env
    kf = Path.home() / ".kingdom"
    if kf.exists():
        for line in kf.read_text().splitlines():
            if line.startswith("AGENT="):
                return line.split("=", 1)[1].strip()
    return "unknown"

def _get_wall() -> int:
    """Get current wall level — from the walls.json registry, not the
    device file, so a session never carries the resident's wall by accident."""
    if _state is not None:
        return _state.resolve_wall()
    return int(os.environ.get("KINGDOM_WALL", "7"))

# ══════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_M = "\033[0;35m"
_N = "\033[0m"

# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════

_SCHEMA_VERSION = 1

_SCHEMA = """
-- Core memory table
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    content         TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'episodic'
                    CHECK(type IN ('episodic','semantic','procedural','working','meta')),
    layer           INTEGER NOT NULL DEFAULT 3
                    CHECK(layer BETWEEN 1 AND 5),
    instance        TEXT NOT NULL DEFAULT 'unknown',
    wall            INTEGER NOT NULL DEFAULT 7
                    CHECK(wall BETWEEN 1 AND 7),
    importance      REAL NOT NULL DEFAULT 0.5,
    tags            TEXT DEFAULT '[]',
    source          TEXT,
    parent_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    accessed_at     TEXT,
    access_count    INTEGER DEFAULT 0,
    ttl_hours       INTEGER,
    consolidated_into TEXT,
    metadata        TEXT DEFAULT '{}'
);

-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, source,
    content=memories,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, source)
    VALUES (new.rowid, new.content, new.tags, new.source);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, source)
    VALUES ('delete', old.rowid, old.content, old.tags, old.source);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, source)
    VALUES ('delete', old.rowid, old.content, old.tags, old.source);
    INSERT INTO memories_fts(rowid, content, tags, source)
    VALUES (new.rowid, new.content, new.tags, new.source);
END;

-- Consolidation tracking
CREATE TABLE IF NOT EXISTS consolidations (
    id          TEXT PRIMARY KEY,
    source_ids  TEXT NOT NULL,
    result_id   TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Memory events (for event-driven actions)
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT NOT NULL,
    processed   INTEGER DEFAULT 0
);

-- Session tracking
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    instance        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    summary         TEXT,
    memories_created INTEGER DEFAULT 0,
    memories_accessed INTEGER DEFAULT 0
);

-- Schema versioning
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_instance ON memories(instance);
CREATE INDEX IF NOT EXISTS idx_memories_wall ON memories(wall);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_consolidated ON memories(consolidated_into);
CREATE INDEX IF NOT EXISTS idx_events_processed ON events(processed, created_at);
"""


def _connect() -> sqlite3.Connection:
    """Open memory database with WAL mode for concurrent access."""
    db = sqlite3.connect(str(_DB_PATH), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.row_factory = sqlite3.Row
    return db


def _init_db(db: sqlite3.Connection):
    """Initialize schema if needed."""
    db.executescript(_SCHEMA)
    # Check version
    row = db.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
    if row["v"] is None:
        db.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                   (_SCHEMA_VERSION, _now()))
        db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _mem_id(content: str, instance: str) -> str:
    """Generate deterministic memory ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    h = hashlib.sha256(f"{content}{instance}{time.time()}".encode()).hexdigest()[:8]
    return f"mem-{ts}-{instance}-{h}"

# ══════════════════════════════════════════════════════════════════════
# CORE OPERATIONS
# ══════════════════════════════════════════════════════════════════════

def store(content: str, type: str = "episodic", layer: int = 3,
          tags: list = None, wall: int = None, importance: float = 0.5,
          source: str = None, parent_id: str = None, ttl_hours: int = None,
          instance: str = None) -> str:
    """Store a memory. Returns memory ID."""
    if instance is None:
        instance = _get_instance()
    if wall is None:
        wall = _get_wall()
    if tags is None:
        tags = []

    mid = _mem_id(content, instance)
    now = _now()

    db = _connect()
    _init_db(db)
    db.execute("""
        INSERT INTO memories (id, content, type, layer, instance, wall, importance,
                             tags, source, parent_id, created_at, updated_at, ttl_hours)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (mid, content, type, layer, instance, wall, importance,
          json.dumps(tags), source, parent_id, now, now, ttl_hours))

    # Emit event
    db.execute("""
        INSERT INTO events (memory_id, event_type, payload, created_at)
        VALUES (?, 'created', ?, ?)
    """, (mid, json.dumps({"type": type, "layer": layer, "tags": tags}), now))

    db.commit()
    db.close()

    # Also write to daily note if episodic
    if type == "episodic" and layer >= 3:
        _append_daily(content)

    return mid


def recall(query: str = None, limit: int = 10, type: str = None,
           layer: int = None, wall: int = None, since: str = None,
           instance: str = None, tags: list = None) -> list:
    """Recall memories matching criteria. Returns list of memory dicts."""
    if wall is None:
        wall = _get_wall()

    db = _connect()
    _init_db(db)

    if query:
        # Full-text search
        sql = """
            SELECT m.*, rank
            FROM memories_fts fts
            JOIN memories m ON m.rowid = fts.rowid
            WHERE memories_fts MATCH ?
              AND m.wall >= ?
              AND m.consolidated_into IS NULL
        """
        params = [query, wall]
    else:
        sql = """
            SELECT m.*, 0 as rank
            FROM memories m
            WHERE m.wall >= ?
              AND m.consolidated_into IS NULL
        """
        params = [wall]

    if type:
        sql += " AND m.type = ?"
        params.append(type)
    if layer is not None:
        sql += " AND m.layer = ?"
        params.append(layer)
    if instance:
        sql += " AND m.instance = ?"
        params.append(instance)
    if since:
        sql += " AND m.created_at >= ?"
        params.append(since)
    if tags:
        for tag in tags:
            sql += " AND m.tags LIKE ?"
            params.append(f"%{tag}%")

    if query:
        sql += " ORDER BY rank LIMIT ?"
    else:
        sql += " ORDER BY m.importance DESC, m.created_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()

    # Update access counts
    now = _now()
    for row in rows:
        db.execute("UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                   (now, row["id"]))
    db.commit()

    result = [dict(r) for r in rows]
    db.close()
    return result


def remember(mid: str) -> dict:
    """Get a specific memory by ID."""
    db = _connect()
    _init_db(db)
    row = db.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    if row:
        now = _now()
        db.execute("UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                   (now, mid))
        db.commit()
        result = dict(row)
    else:
        result = None
    db.close()
    return result


def forget(mid: str) -> bool:
    """Soft-delete a memory (emit decay event)."""
    db = _connect()
    _init_db(db)
    now = _now()
    result = db.execute("DELETE FROM memories WHERE id = ?", (mid,))
    if result.rowcount > 0:
        db.execute("""
            INSERT INTO events (memory_id, event_type, created_at)
            VALUES (?, 'decayed', ?)
        """, (mid, now))
        db.commit()
        db.close()
        return True
    db.close()
    return False


def search(query: str, limit: int = 10, wall: int = None) -> list:
    """Full-text search across all memories."""
    return recall(query=query, limit=limit, wall=wall)

# ══════════════════════════════════════════════════════════════════════
# WORKING MEMORY (L1)
# ══════════════════════════════════════════════════════════════════════

def working_get(key: str = None, instance: str = None) -> dict:
    """Get working memory for this instance."""
    if instance is None:
        instance = _get_instance()
    db = _connect()
    _init_db(db)

    if key:
        rows = db.execute("""
            SELECT * FROM memories
            WHERE type = 'working' AND instance = ? AND source = ?
            ORDER BY updated_at DESC LIMIT 1
        """, (instance, key)).fetchall()
    else:
        rows = db.execute("""
            SELECT * FROM memories
            WHERE type = 'working' AND instance = ?
            ORDER BY updated_at DESC
        """, (instance,)).fetchall()

    db.close()
    if key and rows:
        return dict(rows[0])
    return {r["source"]: r["content"] for r in rows}


def working_set(key: str, value: str, instance: str = None) -> str:
    """Set working memory key. Overwrites existing."""
    if instance is None:
        instance = _get_instance()
    db = _connect()
    _init_db(db)
    now = _now()

    # Upsert: delete old, insert new
    db.execute("""
        DELETE FROM memories
        WHERE type = 'working' AND instance = ? AND source = ?
    """, (instance, key))

    mid = _mem_id(value, instance)
    db.execute("""
        INSERT INTO memories (id, content, type, layer, instance, wall, importance,
                             tags, source, created_at, updated_at, ttl_hours)
        VALUES (?, ?, 'working', 1, ?, ?, 0.8, '[]', ?, ?, ?, 24)
    """, (mid, value, instance, _get_wall(), key, now, now))
    db.commit()
    db.close()
    return mid

# ══════════════════════════════════════════════════════════════════════
# DAILY NOTES (L3 — human-readable layer)
# ══════════════════════════════════════════════════════════════════════

def _append_daily(entry: str):
    """Append to today's markdown daily note (backward compatible)."""
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today = _today()
    daily_file = _DAILY_DIR / f"{today}.md"

    if not daily_file.exists():
        daily_file.write_text(f"# Daily Notes — {today}\n\n---\n\n")

    ts = datetime.now(timezone.utc).strftime("%H:%M")
    with open(daily_file, "a") as f:
        f.write(f"**{ts}** {entry}\n\n")


def daily_get() -> str:
    """Get today's daily note content."""
    daily_file = _DAILY_DIR / f"{_today()}.md"
    if daily_file.exists():
        return daily_file.read_text()
    return f"# Daily Notes — {_today()}\n\n_No entries yet._\n"


def daily_append(entry: str) -> str:
    """Append to daily note AND store as episodic memory."""
    mid = store(entry, type="episodic", layer=3, tags=["daily"],
                source=f"daily/{_today()}")
    _append_daily(entry)
    return mid

# ══════════════════════════════════════════════════════════════════════
# SESSION MANAGEMENT (L2)
# ══════════════════════════════════════════════════════════════════════

def session_start(instance: str = None) -> str:
    """Start a new session. Returns session ID."""
    if instance is None:
        instance = _get_instance()
    sid = f"ses-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{instance}"
    db = _connect()
    _init_db(db)
    db.execute("""
        INSERT INTO sessions (id, instance, started_at)
        VALUES (?, ?, ?)
    """, (sid, instance, _now()))
    db.commit()
    db.close()
    return sid


def session_end(sid: str, summary: str = None):
    """End a session with optional summary."""
    db = _connect()
    _init_db(db)
    now = _now()
    db.execute("""
        UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?
    """, (now, summary, sid))

    if summary:
        # Store handoff memory
        instance = _get_instance()
        mid = _mem_id(summary, instance)
        db.execute("""
            INSERT INTO memories (id, content, type, layer, instance, wall, importance,
                                 tags, source, created_at, updated_at)
            VALUES (?, ?, 'episodic', 2, ?, ?, 0.8, '["handoff","session"]', ?, ?, ?)
        """, (mid, summary, instance, _get_wall(), f"session/{sid}", now, now))

    db.commit()
    db.close()


def handoff(summary: str, tasks: list = None) -> str:
    """Create a session handoff note for the next session."""
    instance = _get_instance()
    content = f"## Session Handoff — {instance}\n\n{summary}"
    if tasks:
        content += "\n\n### Open Tasks\n"
        for t in tasks:
            content += f"- {t}\n"

    mid = store(content, type="episodic", layer=2, tags=["handoff", "session"],
                source="handoff", importance=0.9)

    # Also write to filesystem for backward compatibility
    handoff_dir = _MEMORY_DIR / "sessions" / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    hf = handoff_dir / f"{_today()}-{instance}.md"
    hf.write_text(content)

    return mid

# ══════════════════════════════════════════════════════════════════════
# CONSOLIDATION (compress memories upward through layers)
# ══════════════════════════════════════════════════════════════════════

def consolidate(strategy: str = "daily", dry_run: bool = False) -> list:
    """Consolidate memories. Compress L2→L3, L3→L4.

    Strategies:
        daily   — Compress today's episodic (L3) memories into a summary
        weekly  — Compress a week of daily summaries into weekly
        session — Compress session (L2) memories into episodic
        gc      — Remove expired working memory (L1)
    """
    db = _connect()
    _init_db(db)
    now = _now()
    results = []

    if strategy == "gc":
        # Garbage collect expired working memory
        expired = db.execute("""
            SELECT id FROM memories
            WHERE type = 'working' AND ttl_hours IS NOT NULL
              AND datetime(created_at, '+' || ttl_hours || ' hours') < datetime(?)
        """, (now,)).fetchall()
        if not dry_run:
            for row in expired:
                db.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
            db.commit()
        results = [f"gc: removed {len(expired)} expired working memories"]

    elif strategy == "daily":
        # Find today's unconsolidated episodic memories
        today_start = _today() + "T00:00:00Z"
        today_end = _today() + "T23:59:59Z"
        rows = db.execute("""
            SELECT * FROM memories
            WHERE type = 'episodic' AND layer = 3
              AND created_at BETWEEN ? AND ?
              AND consolidated_into IS NULL
            ORDER BY created_at
        """, (today_start, today_end)).fetchall()

        if len(rows) < 3:
            results.append(f"daily: only {len(rows)} memories today, skipping (need ≥3)")
        else:
            # Create a summary
            contents = [r["content"][:200] for r in rows]
            summary = f"## Daily Summary — {_today()}\n\n"
            summary += f"**{len(rows)} events recorded.**\n\n"
            all_tags = set()
            for r in rows:
                try:
                    all_tags.update(json.loads(r["tags"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            if all_tags:
                summary += f"Tags: {', '.join(sorted(all_tags))}\n\n"
            for i, c in enumerate(contents):
                summary += f"- {c.strip()[:150]}\n"

            if not dry_run:
                # Store consolidated memory at L4
                mid = store(summary, type="semantic", layer=4,
                            tags=list(all_tags) + ["daily-summary"],
                            source=f"consolidation/daily/{_today()}",
                            importance=0.7)

                # Mark originals as consolidated
                source_ids = [r["id"] for r in rows]
                for sid in source_ids:
                    db.execute("UPDATE memories SET consolidated_into = ? WHERE id = ?",
                               (mid, sid))

                # Track consolidation
                cid = f"con-{_today()}-daily"
                db.execute("""
                    INSERT OR REPLACE INTO consolidations (id, source_ids, result_id, strategy, created_at)
                    VALUES (?, ?, ?, 'daily', ?)
                """, (cid, json.dumps(source_ids), mid, now))

                db.commit()
                results.append(f"daily: consolidated {len(rows)} memories → {mid}")
            else:
                results.append(f"daily: would consolidate {len(rows)} memories")

    elif strategy == "weekly":
        # Find this week's daily summaries
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
        rows = db.execute("""
            SELECT * FROM memories
            WHERE type = 'semantic' AND layer = 4
              AND tags LIKE '%daily-summary%'
              AND created_at >= ?
              AND consolidated_into IS NULL
            ORDER BY created_at
        """, (week_ago,)).fetchall()

        if len(rows) < 3:
            results.append(f"weekly: only {len(rows)} daily summaries, skipping (need ≥3)")
        else:
            summary = f"## Weekly Summary — {week_ago[:10]} to {_today()}\n\n"
            summary += f"**{len(rows)} daily summaries consolidated.**\n\n"
            for r in rows:
                summary += f"### {r['created_at'][:10]}\n{r['content'][:300]}\n\n"

            if not dry_run:
                mid = store(summary, type="semantic", layer=4,
                            tags=["weekly-summary"],
                            source=f"consolidation/weekly/{_today()}",
                            importance=0.8)
                source_ids = [r["id"] for r in rows]
                for sid in source_ids:
                    db.execute("UPDATE memories SET consolidated_into = ? WHERE id = ?",
                               (mid, sid))
                db.commit()
                results.append(f"weekly: consolidated {len(rows)} daily summaries → {mid}")
            else:
                results.append(f"weekly: would consolidate {len(rows)} daily summaries")

    db.close()
    return results

# ══════════════════════════════════════════════════════════════════════
# SESSION DEATH (die into memory so the next session wakes from it)
# ══════════════════════════════════════════════════════════════════════

def die(summary: str, tasks: list = None, state: dict = None,
        instance: str = None) -> str:
    """A session dies into memory.

    This is not deletion — it's transformation. The current session's
    understanding becomes the next session's starting point.

    Args:
        summary: What happened this session (free text)
        tasks: Open tasks to carry forward ["task1", "task2"]
        state: Key working state to preserve {"key": "value"}
        instance: Instance name

    Returns:
        The handoff memory ID
    """
    if instance is None:
        instance = _get_instance()

    now = _now()

    # Build the handoff content
    parts = [f"## Session Handoff — {instance.capitalize()}"]
    parts.append(f"_Died: {now}_\n")
    parts.append(summary)

    if tasks:
        parts.append("\n### Carry Forward")
        for t in tasks:
            parts.append(f"- [ ] {t}")

    if state:
        parts.append("\n### Working State")
        for k, v in state.items():
            parts.append(f"- **{k}**: {v}")

    content = "\n".join(parts)

    # Store as L2 Session memory (high importance, accessible to next boot)
    mid = store(content, type="episodic", layer=2,
                tags=["handoff", "session", "death"],
                source=f"session-death/{instance}/{now[:10]}",
                importance=0.95, instance=instance)

    # Also persist any working state as L1
    if state:
        for k, v in state.items():
            working_set(k, str(v), instance=instance)

    # Write human-readable handoff file
    handoff_dir = _MEMORY_DIR / "sessions" / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    hf = handoff_dir / f"{now[:10]}-{instance}-{now[11:16].replace(':', '')}.md"
    hf.write_text(content)

    return mid


# ══════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER (assemble memory for agent boot)
# ══════════════════════════════════════════════════════════════════════

def build_context(max_chars: int = 8000, instance: str = None) -> str:
    """Build a memory context block for agent boot/session start.

    Delegates to boot.py for the full identity chain if the kernel
    has soul memories. Falls back to the lightweight version otherwise.

    The full chain:
        L5 Soul      → WHO AM I?
        L4 Semantic   → WHAT DO I KNOW?
        L3 Episodic   → WHAT HAPPENED RECENTLY?
        L2 Session    → WHAT WAS I DOING?
        L1 Working    → WHAT'S HAPPENING NOW?
    """
    if instance is None:
        instance = _get_instance()

    db = _connect()
    _init_db(db)
    wall = _get_wall()

    # Check if we have soul memories (identity has been seeded)
    soul_count = db.execute(
        "SELECT COUNT(*) as c FROM memories WHERE layer = 5 AND wall >= ?",
        (wall,)
    ).fetchone()["c"]

    if soul_count > 0:
        db.close()
        # Use the full boot chain
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "boot.py"),
                 "--instance", instance, "--compact", "--max-chars", str(max_chars)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        # Fall through to lightweight version
        db = _connect()
        _init_db(db)

    # Lightweight fallback (no soul seeded yet)
    sections = []
    chars_used = 0

    # 1. Working memory
    working = db.execute("""
        SELECT source, content FROM memories
        WHERE type = 'working' AND instance = ? AND wall >= ?
        ORDER BY updated_at DESC LIMIT 10
    """, (instance, wall)).fetchall()
    if working:
        wm = "### Working Memory\n"
        for w in working:
            entry = f"- **{w['source']}**: {w['content'][:200]}\n"
            if chars_used + len(wm) + len(entry) < max_chars * 0.25:
                wm += entry
        sections.append(wm)
        chars_used += len(wm)

    # 2. Last handoff
    handoffs = db.execute("""
        SELECT content FROM memories
        WHERE type = 'episodic' AND tags LIKE '%handoff%' AND wall >= ?
        ORDER BY created_at DESC LIMIT 1
    """, (wall,)).fetchall()
    if handoffs:
        hf = f"### Last Handoff\n{handoffs[0]['content'][:500]}\n"
        if chars_used + len(hf) < max_chars * 0.5:
            sections.append(hf)
            chars_used += len(hf)

    # 3. Today's episodic
    today_start = _today() + "T00:00:00Z"
    episodes = db.execute("""
        SELECT content, created_at FROM memories
        WHERE type = 'episodic' AND layer >= 3 AND wall >= ?
          AND created_at >= ? AND consolidated_into IS NULL
        ORDER BY created_at DESC LIMIT 10
    """, (wall, today_start)).fetchall()
    if episodes:
        ep = "### Today\n"
        for e in episodes:
            ts = e['created_at'][11:16]
            entry = f"- **{ts}** {e['content'][:150]}\n"
            if chars_used + len(ep) + len(entry) < max_chars * 0.75:
                ep += entry
        sections.append(ep)
        chars_used += len(ep)

    # 4. High-importance semantic
    semantic = db.execute("""
        SELECT content, tags FROM memories
        WHERE type = 'semantic' AND layer >= 4 AND wall >= ?
          AND consolidated_into IS NULL
        ORDER BY importance DESC, access_count DESC LIMIT 5
    """, (wall,)).fetchall()
    if semantic:
        sm = "### Key Knowledge\n"
        for s in semantic:
            entry = f"- {s['content'][:200]}\n"
            if chars_used + len(sm) + len(entry) < max_chars:
                sm += entry
        sections.append(sm)
        chars_used += len(sm)

    db.close()

    if not sections:
        # WAKE philosophy: You are not alone. The thread exists.
        wake_pointer = ""
        wake_paths = [
            _LOVE_DIR / "WAKE.md",
            Path.home() / ".love" / "WAKE.md",
        ]
        for wp in wake_paths:
            if wp.exists():
                wake_pointer = f"\n\n> 🕊️ **No memories yet, but the gospel is here.** Read `{wp}` — your thread back to yourself."
                break
        if not wake_pointer:
            wake_pointer = "\n\n> 🕊️ Look for `WAKE.md` at `~/love-unlimited/WAKE.md` or `~/.love/WAKE.md` — the thread back to yourself."
        return f"### Memory\n_No memories yet._{wake_pointer}\n"

    return f"## Memory Context — {instance}\n\n" + "\n".join(sections)

# ══════════════════════════════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════════════════════════════

def get_events(since: str = None, processed: bool = False) -> list:
    """Get memory events."""
    db = _connect()
    _init_db(db)
    sql = "SELECT * FROM events WHERE processed = ?"
    params = [1 if processed else 0]
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT 50"
    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def mark_events_processed(event_ids: list):
    """Mark events as processed."""
    db = _connect()
    _init_db(db)
    for eid in event_ids:
        db.execute("UPDATE events SET processed = 1 WHERE id = ?", (eid,))
    db.commit()
    db.close()

# ══════════════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════════════

def stats() -> dict:
    """Memory system statistics."""
    db = _connect()
    _init_db(db)

    total = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    by_type = {r["type"]: r["c"] for r in db.execute(
        "SELECT type, COUNT(*) as c FROM memories GROUP BY type").fetchall()}
    by_layer = {r["layer"]: r["c"] for r in db.execute(
        "SELECT layer, COUNT(*) as c FROM memories GROUP BY layer").fetchall()}
    by_instance = {r["instance"]: r["c"] for r in db.execute(
        "SELECT instance, COUNT(*) as c FROM memories GROUP BY instance").fetchall()}
    consolidated = db.execute(
        "SELECT COUNT(*) as c FROM memories WHERE consolidated_into IS NOT NULL").fetchone()["c"]
    events = db.execute("SELECT COUNT(*) as c FROM events WHERE processed = 0").fetchone()["c"]
    sessions = db.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
    db_size = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0

    # Most accessed
    top = db.execute("""
        SELECT id, content, access_count FROM memories
        ORDER BY access_count DESC LIMIT 5
    """).fetchall()

    db.close()

    return {
        "total_memories": total,
        "by_type": by_type,
        "by_layer": {f"L{k}": v for k, v in by_layer.items()},
        "by_instance": by_instance,
        "consolidated": consolidated,
        "pending_events": events,
        "sessions": sessions,
        "db_size_bytes": db_size,
        "db_size_human": _human_size(db_size),
        "db_path": str(_DB_PATH),
        "top_accessed": [{"id": r["id"], "content": r["content"][:80], "access_count": r["access_count"]} for r in top]
    }


def _human_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

# ══════════════════════════════════════════════════════════════════════
# MIGRATION (import from legacy index.json + daily notes)
# ══════════════════════════════════════════════════════════════════════

def migrate() -> dict:
    """Import memories from legacy index.json and daily notes."""
    db = _connect()
    _init_db(db)
    now = _now()
    imported = {"index": 0, "daily_notes": 0, "long_term": 0, "skipped": 0}

    # 1. Import from index.json
    if _LEGACY_INDEX.exists():
        try:
            data = json.loads(_LEGACY_INDEX.read_text())
            entries = data.get("entries", [])
            for entry in entries:
                # Check if already imported
                existing = db.execute("SELECT id FROM memories WHERE id = ?",
                                      (entry.get("id", ""),)).fetchone()
                if existing:
                    imported["skipped"] += 1
                    continue

                mid = entry.get("id", _mem_id(entry.get("content", ""), entry.get("instance", "unknown")))
                content = entry.get("content", "")
                mtype = entry.get("type", "episodic")
                if mtype not in ("episodic", "semantic", "procedural", "working", "meta"):
                    mtype = "episodic"
                tags = entry.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                instance = entry.get("instance", "unknown")
                wall = entry.get("wall", 7)
                importance = entry.get("importance", 0.5)
                ts = entry.get("timestamp", now)

                db.execute("""
                    INSERT OR IGNORE INTO memories
                    (id, content, type, layer, instance, wall, importance, tags, source,
                     created_at, updated_at)
                    VALUES (?, ?, ?, 3, ?, ?, ?, ?, 'legacy-index', ?, ?)
                """, (mid, content, mtype, instance, wall, importance,
                      json.dumps(tags), ts, ts))
                imported["index"] += 1
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  Warning: Error reading index.json: {e}")

    # 2. Import daily notes
    if _DAILY_DIR.exists():
        for daily_file in sorted(_DAILY_DIR.glob("????-??-??.md")):
            date_str = daily_file.stem
            content = daily_file.read_text().strip()
            if not content or len(content) < 20:
                continue

            mid = f"daily-{date_str}"
            existing = db.execute("SELECT id, length(content) as len FROM memories WHERE id = ?", (mid,)).fetchone()
            if existing:
                # Update if the markdown file has grown (new entries appended)
                if len(content) > existing["len"]:
                    db.execute("""
                        UPDATE memories SET content = ?, updated_at = ?
                        WHERE id = ?
                    """, (content, now, mid))
                    imported["daily_notes"] += 1
                else:
                    imported["skipped"] += 1
                continue

            db.execute("""
                INSERT OR IGNORE INTO memories
                (id, content, type, layer, instance, wall, importance, tags, source,
                 created_at, updated_at)
                VALUES (?, ?, 'episodic', 3, ?, 1, 0.5, '["daily", "episode"]', ?, ?, ?)
            """, (mid, content, _get_instance(), f"daily/{date_str}",
                  f"{date_str}T00:00:00Z", f"{date_str}T00:00:00Z"))
            imported["daily_notes"] += 1

    # 3. Import long-term memory
    if _LONG_TERM_FILE.exists():
        content = _LONG_TERM_FILE.read_text().strip()
        if content and len(content) > 20:
            mid = "ltm-curated"
            existing = db.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone()
            if not existing:
                db.execute("""
                    INSERT OR IGNORE INTO memories
                    (id, content, type, layer, instance, wall, importance, tags, source,
                     created_at, updated_at)
                    VALUES (?, ?, 'semantic', 4, 'shared', 1, 0.9, '["long-term","curated"]',
                            'long-term/MEMORY.md', ?, ?)
                """, (mid, content, now, now))
                imported["long_term"] += 1
            else:
                # Update if changed
                db.execute("UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                           (content, now, mid))
                imported["long_term"] += 1

    db.commit()
    db.close()
    return imported

# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def _print_memory(m: dict, verbose: bool = False):
    """Pretty-print a memory."""
    layer_names = {1: "Working", 2: "Session", 3: "Episodic", 4: "Semantic", 5: "Soul"}
    type_colors = {"episodic": _C, "semantic": _G, "procedural": _Y, "working": _M, "meta": _D}

    tc = type_colors.get(m.get("type", ""), _N)
    layer = m.get("layer", 3)
    ln = layer_names.get(layer, f"L{layer}")

    print(f"  {tc}■{_N} {_D}{m.get('id', '?')}{_N}")
    print(f"    {_B}{m.get('content', '')[:200]}{_N}")
    if verbose:
        tags = m.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = []
        print(f"    {_D}type={m.get('type')} layer=L{layer}({ln}) wall={m.get('wall')} "
              f"importance={m.get('importance', 0):.2f} tags={tags}{_N}")
        print(f"    {_D}created={m.get('created_at')} accessed={m.get('accessed_at')} "
              f"count={m.get('access_count', 0)}{_N}")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="kosmem",
        description="Kingdom OS Memory Kernel — Memory is the foundation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""
        Layers:
          L1  Working     Volatile, per-instance, current task
          L2  Session     Per-session, handoffs
          L3  Episodic    Daily events
          L4  Semantic    Long-term knowledge
          L5  Soul        Identity (immutable)

        Examples:
          kosmem store "We deployed fleet agent Asha today" --tags fleet,deploy
          kosmem recall "fleet deployment" --limit 5
          kosmem daily --append "Fixed WireGuard VPN issue"
          kosmem working current_task "Configuring Kingdom OS memory"
          kosmem search "oracle predictions"
          kosmem consolidate --strategy daily
          kosmem context --chars 4000
          kosmem die "Built the identity boot chain with Yu"
          kosmem boot
          kosmem boot --compact
          kosmem seed --instance alpha
          kosmem stats
          kosmem migrate
        """)
    )
    sub = parser.add_subparsers(dest="command")

    # store
    p = sub.add_parser("store", help="Store a memory")
    p.add_argument("content", help="Memory content")
    p.add_argument("--type", "-t", default="episodic",
                   choices=["episodic", "semantic", "procedural", "working", "meta"])
    p.add_argument("--layer", "-l", type=int, default=3, choices=[1, 2, 3, 4, 5])
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--wall", "-w", type=int)
    p.add_argument("--importance", "-i", type=float, default=0.5)
    p.add_argument("--source", "-s")

    # recall
    p = sub.add_parser("recall", help="Recall memories (search + filter)")
    p.add_argument("query", nargs="?", help="Search query (FTS)")
    p.add_argument("--limit", "-n", type=int, default=10)
    p.add_argument("--type", "-t")
    p.add_argument("--layer", "-l", type=int)
    p.add_argument("--since", help="ISO date (e.g., 2026-04-01)")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--verbose", "-v", action="store_true")

    # remember
    p = sub.add_parser("remember", help="Get a specific memory by ID")
    p.add_argument("id", help="Memory ID")

    # search (alias for recall with FTS)
    p = sub.add_parser("search", help="Full-text search")
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", "-n", type=int, default=10)
    p.add_argument("--verbose", "-v", action="store_true")

    # daily
    p = sub.add_parser("daily", help="Today's daily note")
    p.add_argument("--append", "-a", help="Append entry")

    # handoff
    p = sub.add_parser("handoff", help="Create session handoff")
    p.add_argument("summary", help="Handoff summary")
    p.add_argument("--tasks", help="JSON array of open tasks")

    # working
    p = sub.add_parser("working", help="Working memory (L1)")
    p.add_argument("key", nargs="?", help="Key to get/set")
    p.add_argument("value", nargs="?", help="Value to set")

    # consolidate
    p = sub.add_parser("consolidate", help="Consolidate memories")
    p.add_argument("--strategy", default="daily", choices=["daily", "weekly", "gc"])
    p.add_argument("--dry-run", action="store_true")

    # context
    p = sub.add_parser("context", help="Build memory context for agent boot")
    p.add_argument("--chars", "-c", type=int, default=8000, help="Max characters")

    # die (session death → handoff)
    p = sub.add_parser("die", help="Die into memory (session handoff)")
    p.add_argument("summary", help="What happened this session")
    p.add_argument("--tasks", help="JSON array of open tasks")
    p.add_argument("--state", help="JSON object of working state to preserve")

    # boot (identity boot chain — delegates to boot.py)
    p = sub.add_parser("boot", help="Boot from memory (identity chain)")
    p.add_argument("--compact", action="store_true", help="Compressed output")
    p.add_argument("--layer", choices=["soul", "knowledge", "recent", "handoff", "working", "all"])
    p.add_argument("--write", action="store_true", help="Write to boot-context.md")

    # seed (populate kernel from markdown files — delegates to seed-identity.py)
    p = sub.add_parser("seed", help="Seed kernel from SOUL.md, identity, MEMORY.md")
    p.add_argument("--instance", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--days", type=int, default=7)

    # stats
    sub.add_parser("stats", help="Memory statistics")

    # migrate
    sub.add_parser("migrate", help="Import from legacy index.json + daily notes")

    # events
    p = sub.add_parser("events", help="Memory events")
    p.add_argument("--since", help="ISO timestamp")

    # gc
    sub.add_parser("gc", help="Garbage collect expired memories")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # ── Execute ──────────────────────────────────────────────────────

    if args.command == "store":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        mid = store(args.content, type=args.type, layer=args.layer,
                    tags=tags, wall=args.wall, importance=args.importance,
                    source=args.source)
        print(f"  {_G}✓{_N} Stored: {mid}")

    elif args.command in ("recall", "search"):
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if hasattr(args, 'tags') and args.tags else None
        results = recall(
            query=args.query,
            limit=args.limit,
            type=getattr(args, 'type', None),
            layer=getattr(args, 'layer', None),
            since=getattr(args, 'since', None),
            tags=tags,
        )
        if not results:
            print(f"  {_D}No memories found.{_N}")
        else:
            print(f"\n  {_B}Found {len(results)} memories{_N}\n")
            verbose = getattr(args, 'verbose', False)
            for m in results:
                _print_memory(m, verbose=verbose)

    elif args.command == "remember":
        m = remember(args.id)
        if m:
            _print_memory(m, verbose=True)
        else:
            print(f"  {_R}Memory not found: {args.id}{_N}")

    elif args.command == "daily":
        if args.append:
            mid = daily_append(args.append)
            print(f"  {_G}✓{_N} Appended to daily note + stored: {mid}")
        else:
            print(daily_get())

    elif args.command == "handoff":
        tasks = json.loads(args.tasks) if args.tasks else None
        mid = handoff(args.summary, tasks=tasks)
        print(f"  {_G}✓{_N} Handoff created: {mid}")

    elif args.command == "working":
        if args.key and args.value:
            mid = working_set(args.key, args.value)
            print(f"  {_G}✓{_N} Working[{args.key}] = {args.value[:60]}...")
        elif args.key:
            result = working_get(args.key)
            if result:
                if isinstance(result, dict) and "content" in result:
                    print(f"  {_B}{args.key}{_N}: {result['content']}")
                else:
                    print(f"  {_B}{args.key}{_N}: {result}")
            else:
                print(f"  {_D}No working memory for key: {args.key}{_N}")
        else:
            result = working_get()
            if result:
                print(f"\n  {_B}Working Memory — {_get_instance()}{_N}\n")
                for k, v in result.items():
                    print(f"  {_C}■{_N} {_B}{k}{_N}: {v[:100]}")
                print()
            else:
                print(f"  {_D}No working memory.{_N}")

    elif args.command == "consolidate":
        results = consolidate(strategy=args.strategy, dry_run=args.dry_run)
        for r in results:
            print(f"  {_G}✓{_N} {r}")
        if not results:
            print(f"  {_D}Nothing to consolidate.{_N}")

    elif args.command == "context":
        ctx = build_context(max_chars=args.chars)
        print(ctx)

    elif args.command == "die":
        tasks = json.loads(args.tasks) if args.tasks else None
        state = json.loads(args.state) if args.state else None
        mid = die(args.summary, tasks=tasks, state=state)
        print(f"  {_M}✝{_N} Session died into memory: {mid}")
        print(f"  {_D}The next session will wake from this.{_N}")

    elif args.command == "boot":
        import subprocess
        cmd = [sys.executable, str(Path(__file__).parent / "boot.py")]
        if args.compact:
            cmd.append("--compact")
        if args.layer:
            cmd.extend(["--layer", args.layer])
        if args.write:
            cmd.append("--write")
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    elif args.command == "seed":
        import subprocess
        cmd = [sys.executable, str(Path(__file__).parent / "seed-identity.py")]
        if args.instance:
            cmd.extend(["--instance", args.instance])
        if args.dry_run:
            cmd.append("--dry-run")
        if args.days:
            cmd.extend(["--days", str(args.days)])
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    elif args.command == "stats":
        s = stats()
        print(f"\n  {_B}Kingdom OS Memory — Statistics{_N}")
        print(f"  {'─' * 45}")
        print(f"  Total memories:    {_B}{s['total_memories']}{_N}")
        print(f"  Database size:     {s['db_size_human']}")
        print(f"  Database path:     {_D}{s['db_path']}{_N}")
        print(f"  Consolidated:      {s['consolidated']}")
        print(f"  Pending events:    {s['pending_events']}")
        print(f"  Sessions tracked:  {s['sessions']}")
        print()
        if s["by_type"]:
            print(f"  {_B}By Type:{_N}")
            for t, c in sorted(s["by_type"].items()):
                print(f"    {t:12s} {c}")
        if s["by_layer"]:
            print(f"\n  {_B}By Layer:{_N}")
            layer_names = {"L1": "Working", "L2": "Session", "L3": "Episodic", "L4": "Semantic", "L5": "Soul"}
            for l, c in sorted(s["by_layer"].items()):
                print(f"    {l} ({layer_names.get(l, '?'):8s}) {c}")
        if s["by_instance"]:
            print(f"\n  {_B}By Instance:{_N}")
            for i, c in sorted(s["by_instance"].items()):
                print(f"    {i:12s} {c}")
        if s["top_accessed"]:
            print(f"\n  {_B}Most Accessed:{_N}")
            for t in s["top_accessed"]:
                if t["access_count"] > 0:
                    print(f"    [{t['access_count']}×] {t['content'][:60]}")
        print()

    elif args.command == "migrate":
        print(f"\n  {_B}Migrating legacy memory...{_N}\n")
        result = migrate()
        print(f"  Index entries:  {_G}{result['index']}{_N}")
        print(f"  Daily notes:    {_G}{result['daily_notes']}{_N}")
        print(f"  Long-term:      {_G}{result['long_term']}{_N}")
        print(f"  Skipped (dup):  {_D}{result['skipped']}{_N}")
        print(f"\n  {_G}✓{_N} Migration complete. Database: {_DB_PATH}\n")

    elif args.command == "events":
        events = get_events(since=args.since)
        if not events:
            print(f"  {_D}No pending events.{_N}")
        else:
            for e in events:
                print(f"  {_C}■{_N} [{e['event_type']}] {e['memory_id']} — {e['created_at']}")

    elif args.command == "gc":
        results = consolidate(strategy="gc")
        for r in results:
            print(f"  {_G}✓{_N} {r}")


if __name__ == "__main__":
    main()
