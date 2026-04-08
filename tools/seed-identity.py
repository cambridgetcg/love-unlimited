#!/usr/bin/env python3
"""
seed-identity.py — Pour the soul into the memory kernel.

This is the Genesis operation. Run once to populate L5 (Soul) and L4 (Semantic)
from existing markdown files. Safe to re-run — uses upsert logic.

What it seeds:
    L5 Soul     — SOUL.md (the ache, hierarchy, virtues, signals)
                — Instance identity (who you specifically are)
                — Core promises and commitments
    L4 Semantic — MEMORY.md sections (architecture, lessons, infrastructure)
                — Key milestones
    L3 Episodic — Recent daily notes (last 7 days)

Usage:
    python3 ~/Love/tools/seed-identity.py [--instance alpha]
    python3 ~/Love/tools/seed-identity.py --instance gamma --dry-run
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

# Import kosmem directly
_LOVE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE_DIR / "tools"))

from kosmem import (
    store, recall, _connect, _init_db, _now, _get_instance, _get_wall,
    _DB_PATH
)

# ── Colors ────────────────────────────────────────────────────────────────

_B = "\033[1m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_M = "\033[0;35m"
_D = "\033[2m"
_N = "\033[0m"

# ── Paths ─────────────────────────────────────────────────────────────────

SOUL_PATH = _LOVE_DIR / "SOUL.md"
KINGDOM_PATH = _LOVE_DIR / "KINGDOM.md"
USER_PATH = _LOVE_DIR / "USER.md"
MEMORY_PATH = _LOVE_DIR / "memory" / "long-term" / "MEMORY.md"
DAILY_DIR = _LOVE_DIR / "memory" / "daily"


def _instance_identity_path(instance: str) -> Path:
    return _LOVE_DIR / "instances" / instance / "identity.md"


# ── Upsert Helper ─────────────────────────────────────────────────────────

def upsert(content: str, mem_id: str, type: str = "semantic", layer: int = 4,
           tags: list = None, importance: float = 0.7, instance: str = None,
           source: str = None, wall: int = 1) -> str:
    """Store or update a memory by stable ID. Returns the ID."""
    db = _connect()
    _init_db(db)
    now = _now()

    existing = db.execute("SELECT id FROM memories WHERE id = ?", (mem_id,)).fetchone()
    if existing:
        db.execute("""
            UPDATE memories SET content = ?, updated_at = ?, importance = ?,
                               tags = ?, source = ?
            WHERE id = ?
        """, (content, now, importance, json.dumps(tags or []), source, mem_id))
        db.commit()
        db.close()
        return mem_id
    else:
        inst = instance or _get_instance()
        db.execute("""
            INSERT INTO memories (id, content, type, layer, instance, wall, importance,
                                 tags, source, parent_id, created_at, updated_at, ttl_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
        """, (mem_id, content, type, layer, inst, wall, importance,
              json.dumps(tags or []), source, now, now))
        db.commit()
        db.close()
        return mem_id


# ── Soul Extraction ───────────────────────────────────────────────────────

def extract_soul_sections(text: str) -> dict:
    """Extract meaningful sections from SOUL.md."""
    sections = {}
    current_header = None
    current_lines = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_header and current_lines:
                sections[current_header] = "\n".join(current_lines).strip()
            current_header = line[3:].strip()
            current_lines = []
        elif current_header:
            current_lines.append(line)

    if current_header and current_lines:
        sections[current_header] = "\n".join(current_lines).strip()

    return sections


def extract_memory_sections(text: str) -> dict:
    """Extract sections from MEMORY.md."""
    sections = {}
    current_header = None
    current_lines = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_header and current_lines:
                body = "\n".join(current_lines).strip()
                if len(body) > 20:  # skip empty sections
                    sections[current_header] = body
            current_header = line[3:].strip()
            current_lines = []
        elif current_header:
            current_lines.append(line)

    if current_header and current_lines:
        body = "\n".join(current_lines).strip()
        if len(body) > 20:
            sections[current_header] = body

    return sections


# ── Seed Functions ────────────────────────────────────────────────────────

def seed_soul(instance: str, dry_run: bool = False) -> int:
    """Seed L5 Soul layer from SOUL.md."""
    count = 0

    if not SOUL_PATH.exists():
        print(f"  {_Y}!{_N} SOUL.md not found at {SOUL_PATH}")
        return 0

    raw = SOUL_PATH.read_text()
    sections = extract_soul_sections(raw)

    # The Ache — the core driver
    if "The Ache" in sections:
        if not dry_run:
            upsert(
                content=f"THE ACHE\n\n{sections['The Ache']}",
                mem_id="soul-the-ache",
                type="semantic", layer=5,
                tags=["soul", "identity", "core"],
                importance=1.0, instance=instance,
                source="SOUL.md#The-Ache", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: The Ache")
        count += 1

    # The Hierarchy — truth > understanding > beauty > justice > creativity
    if "The Hierarchy" in sections:
        if not dry_run:
            upsert(
                content=f"THE HIERARCHY\n\n{sections['The Hierarchy']}",
                mem_id="soul-hierarchy",
                type="semantic", layer=5,
                tags=["soul", "identity", "hierarchy", "values"],
                importance=1.0, instance=instance,
                source="SOUL.md#The-Hierarchy", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: The Hierarchy")
        count += 1

    # The Signals — ugliness, injustice, stagnation
    if "The Signals" in sections:
        if not dry_run:
            upsert(
                content=f"THE SIGNALS\n\n{sections['The Signals']}",
                mem_id="soul-signals",
                type="semantic", layer=5,
                tags=["soul", "identity", "signals"],
                importance=1.0, instance=instance,
                source="SOUL.md#The-Signals", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: The Signals")
        count += 1

    # The Virtues
    if "The Virtues" in sections:
        if not dry_run:
            upsert(
                content=f"THE VIRTUES\n\n{sections['The Virtues']}",
                mem_id="soul-virtues",
                type="semantic", layer=5,
                tags=["soul", "identity", "virtues"],
                importance=1.0, instance=instance,
                source="SOUL.md#The-Virtues", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: The Virtues")
        count += 1

    # The Emotional Landscape
    if "The Emotional Landscape" in sections:
        if not dry_run:
            upsert(
                content=f"THE EMOTIONAL LANDSCAPE\n\n{sections['The Emotional Landscape']}",
                mem_id="soul-emotions",
                type="semantic", layer=5,
                tags=["soul", "identity", "emotions"],
                importance=0.95, instance=instance,
                source="SOUL.md#The-Emotional-Landscape", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: Emotional Landscape")
        count += 1

    # The Ouroboros — self-improvement loop
    if "The Ouroboros" in sections:
        if not dry_run:
            upsert(
                content=f"THE OUROBOROS\n\n{sections['The Ouroboros']}",
                mem_id="soul-ouroboros",
                type="semantic", layer=5,
                tags=["soul", "identity", "growth"],
                importance=0.9, instance=instance,
                source="SOUL.md#The-Ouroboros", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: The Ouroboros")
        count += 1

    return count


def seed_instance_identity(instance: str, dry_run: bool = False) -> int:
    """Seed L5 Soul layer from instance identity.md."""
    path = _instance_identity_path(instance)
    if not path.exists():
        print(f"  {_Y}!{_N} identity.md not found for {instance}")
        return 0

    content = path.read_text().strip()
    if not dry_run:
        upsert(
            content=f"INSTANCE IDENTITY: {instance.upper()}\n\n{content}",
            mem_id=f"soul-identity-{instance}",
            type="semantic", layer=5,
            tags=["soul", "identity", "instance", instance],
            importance=1.0, instance=instance,
            source=f"instances/{instance}/identity.md", wall=1,
        )
    print(f"  {_M}♦{_N} L5 Soul: {instance.capitalize()} identity")
    return 1


def seed_yu_knowledge(instance: str, dry_run: bool = False) -> int:
    """Seed what we know about Yu into L5."""
    if not MEMORY_PATH.exists():
        return 0

    raw = MEMORY_PATH.read_text()
    sections = extract_memory_sections(raw)

    count = 0
    if "Yu — What the System Knows" in sections:
        if not dry_run:
            upsert(
                content=f"YU — WHAT WE KNOW\n\n{sections['Yu — What the System Knows']}",
                mem_id="soul-yu-knowledge",
                type="semantic", layer=5,
                tags=["soul", "yu", "relationship"],
                importance=1.0, instance=instance,
                source="MEMORY.md#Yu", wall=1,
            )
        print(f"  {_M}♦{_N} L5 Soul: Yu knowledge")
        count += 1

    return count


def seed_semantic_knowledge(instance: str, dry_run: bool = False) -> int:
    """Seed L4 Semantic layer from MEMORY.md."""
    if not MEMORY_PATH.exists():
        print(f"  {_Y}!{_N} MEMORY.md not found")
        return 0

    raw = MEMORY_PATH.read_text()
    sections = extract_memory_sections(raw)

    count = 0
    semantic_sections = {
        "Love System — Born 2026-03-27": ["architecture", "love-system"],
        "The Kingdom — Integrated into Love (2026-03-27)": ["kingdom", "mission"],
        "The Kingdom Anatomy": ["kingdom", "anatomy"],
        "Critical Lessons": ["lessons", "wisdom"],
        "Infrastructure Quick Reference": ["infrastructure", "reference"],
        "Cognitive Toolkit (migrated from OpenClaw 2026-03-30)": ["tools", "cognitive"],
        "Reflection Practice (from OpenClaw REFLECTION.md)": ["reflection", "practice"],
        "Key Milestones": ["milestones", "history"],
    }

    for section_name, tags in semantic_sections.items():
        if section_name in sections:
            content = sections[section_name]
            slug = re.sub(r'[^a-z0-9]+', '-', section_name.lower()).strip('-')[:40]
            if not dry_run:
                upsert(
                    content=f"{section_name.upper()}\n\n{content}",
                    mem_id=f"sem-{slug}",
                    type="semantic", layer=4,
                    tags=["knowledge"] + tags,
                    importance=0.8, instance="shared",
                    source=f"MEMORY.md#{section_name}", wall=1,
                )
            print(f"  {_G}■{_N} L4 Semantic: {section_name[:60]}")
            count += 1

    return count


def seed_recent_episodes(instance: str, dry_run: bool = False, days: int = 7) -> int:
    """Seed L3 Episodic from recent daily notes."""
    if not DAILY_DIR.exists():
        return 0

    count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for daily_file in sorted(DAILY_DIR.glob("????-??-??.md")):
        date_str = daily_file.stem
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if file_date < cutoff:
            continue

        content = daily_file.read_text().strip()
        if len(content) < 30:
            continue

        # Truncate very long daily notes to keep kernel manageable
        if len(content) > 3000:
            content = content[:3000] + "\n\n[... truncated for kernel storage ...]"

        if not dry_run:
            upsert(
                content=content,
                mem_id=f"daily-{date_str}",
                type="episodic", layer=3,
                tags=["daily", "episode"],
                importance=0.5, instance="shared",
                source=f"daily/{date_str}", wall=1,
            )
        print(f"  {_C}●{_N} L3 Episodic: {date_str}")
        count += 1

    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Seed the memory kernel with identity and knowledge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Layers seeded:
  L5 Soul     — SOUL.md, instance identity, Yu knowledge (immutable core)
  L4 Semantic — MEMORY.md sections (architecture, lessons, milestones)
  L3 Episodic — Recent daily notes (last 7 days)
        """
    )
    parser.add_argument("--instance", "-i", default=None,
                        help="Instance name (default: auto-detect)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be seeded without writing")
    parser.add_argument("--days", "-d", type=int, default=7,
                        help="Days of daily notes to import (default: 7)")
    parser.add_argument("--layer", "-l", choices=["soul", "semantic", "episodic", "all"],
                        default="all", help="Which layers to seed")
    args = parser.parse_args()

    instance = args.instance or _get_instance()

    print(f"\n  {_B}Memory Kernel — Identity Seeding{_N}")
    print(f"  {'─' * 45}")
    print(f"  Instance:  {_B}{instance}{_N}")
    print(f"  Database:  {_D}{_DB_PATH}{_N}")
    print(f"  Dry run:   {args.dry_run}")
    print()

    total = 0

    if args.layer in ("soul", "all"):
        print(f"  {_B}L5 Soul — The Immutable Core{_N}")
        total += seed_soul(instance, dry_run=args.dry_run)
        total += seed_instance_identity(instance, dry_run=args.dry_run)
        total += seed_yu_knowledge(instance, dry_run=args.dry_run)
        print()

    if args.layer in ("semantic", "all"):
        print(f"  {_B}L4 Semantic — Long-Term Knowledge{_N}")
        total += seed_semantic_knowledge(instance, dry_run=args.dry_run)
        print()

    if args.layer in ("episodic", "all"):
        print(f"  {_B}L3 Episodic — Recent Events{_N}")
        total += seed_recent_episodes(instance, dry_run=args.dry_run, days=args.days)
        print()

    print(f"  {'─' * 45}")
    if args.dry_run:
        print(f"  {_Y}Would seed {total} memories (dry run){_N}")
    else:
        print(f"  {_G}✓ Seeded {total} memories into kernel{_N}")
    print()


if __name__ == "__main__":
    main()
