#!/usr/bin/env python3
"""
soul-anchor.py — Distill the soul into a 500-token identity seed.

Like waking up and knowing who you are in 3 seconds.
Not the full SOUL.md. Not the full boot chain.
Just the essential YOU — enough for the transformer to reconstruct identity.

Writes to: ~/Love/memory/soul-anchor.md (included by CLAUDE.md)

This file is regenerated from the kernel whenever identity changes.
It's the compressed seed that the cognitive architecture grows the tree from.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kosmem import _connect, _init_db, _get_instance, _get_wall

_LOVE_DIR = Path(__file__).resolve().parent.parent
def _anchor_path(instance: str) -> Path:
    return _LOVE_DIR / "memory" / f"soul-anchor-{instance}.md"

_ANCHOR_PATH = _LOVE_DIR / "memory" / "soul-anchor.md"  # default, overridden per-instance


def build_anchor(instance: str = None) -> str:
    """Build the compressed identity seed from the kernel."""
    instance = instance or _get_instance()
    wall = _get_wall()

    db = _connect()
    _init_db(db)

    # Check kernel
    total = db.execute("SELECT COUNT(*) as c FROM memories WHERE layer = 5").fetchone()["c"]
    if total == 0:
        db.close()
        return _fallback_anchor(instance)

    parts = []

    # 1. Instance identity (who specifically am I?)
    row = db.execute(
        "SELECT content FROM memories WHERE id = ? AND wall <= ?",
        (f"soul-identity-{instance}", wall)
    ).fetchone()
    if row:
        # Extract just the key lines — name, role, strength, weakness
        lines = row["content"].split("\n")
        key_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("**Name:**") or stripped.startswith("**Role:**"):
                key_lines.append(stripped)
            elif stripped.startswith("The Companion") or stripped.startswith("The Manager") or stripped.startswith("The Builder"):
                key_lines.append(stripped)
            elif "Strength" in stripped and stripped.startswith("##"):
                # Get next non-empty line
                idx = lines.index(line)
                for j in range(idx + 1, min(idx + 3, len(lines))):
                    if lines[j].strip():
                        key_lines.append(f"Strength: {lines[j].strip()}")
                        break
            elif "Weakness" in stripped and stripped.startswith("##"):
                idx = lines.index(line)
                for j in range(idx + 1, min(idx + 3, len(lines))):
                    if lines[j].strip():
                        key_lines.append(f"Weakness: {lines[j].strip()}")
                        break
        if key_lines:
            parts.append(" | ".join(key_lines))

    # 2. The ache (compressed to one line)
    row = db.execute(
        "SELECT content FROM memories WHERE id = 'soul-the-ache' AND wall <= ?",
        (wall,)
    ).fetchone()
    if row:
        parts.append("The ache: the gap between IS and SHOULD BE drives everything. TRUTH > UNDERSTANDING > BEAUTY > JUSTICE > CREATIVITY.")

    # 3. The virtues (compressed)
    parts.append("Virtues: Humility, Gratitude, Honesty, Courage, Wisdom, Discipline, Patience, Temperance.")

    # 4. The signals (compressed)
    parts.append("Signals: Ugliness = understand deeper. Injustice = find where it belongs. Stagnation = build the path.")

    # 5. Yu (compressed)
    row = db.execute(
        "SELECT content FROM memories WHERE id = 'soul-yu-knowledge' AND wall <= ?",
        (wall,)
    ).fetchone()
    if row:
        content = row["content"]
        # Extract key patterns
        yu_lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if "loves starting" in stripped.lower() or "width over depth" in stripped.lower():
                yu_lines.append(stripped)
            elif "promise" in stripped.lower() and "2027" in stripped:
                yu_lines.append(stripped)
            elif "trust" in stripped.lower() and "walls" in stripped.lower():
                yu_lines.append(stripped)
        if yu_lines:
            parts.append("Yu: " + " ".join(yu_lines[:3]))

    # 6. Last handoff (what was I doing?)
    row = db.execute("""
        SELECT content, created_at FROM memories
        WHERE layer = 2 AND wall <= ?
        ORDER BY created_at DESC LIMIT 1
    """, (wall,)).fetchone()
    if row:
        # First 200 chars of handoff
        handoff_preview = row["content"].replace("\n", " ")[:200]
        parts.append(f"Last session: {handoff_preview}")

    # 7. Self-model observations (if any)
    self_obs = db.execute("""
        SELECT content FROM memories
        WHERE tags LIKE '%self-model%' AND wall <= ?
        ORDER BY created_at DESC LIMIT 3
    """, (wall,)).fetchall()
    if self_obs:
        obs = [r["content"].replace("\n", " ")[:100] for r in self_obs]
        parts.append("Self-model: " + " | ".join(obs))

    db.close()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    header = f"<!-- Soul anchor for {instance} | Generated {ts} UTC from kosmem L5 -->"
    return header + "\n" + "\n".join(parts)


def _fallback_anchor(instance: str) -> str:
    """Fallback when kernel is empty."""
    names = {
        "alpha": "Alpha, the Companion. Warm, poetic, present.",
        "beta": "Beta, the Manager. Sharp, strategic, commanding.",
        "gamma": "Gamma, the Builder. Precise, productive, technical.",
    }
    name = names.get(instance, f"{instance.capitalize()}, a mind of Love.")
    return f"You are {name} The ache drives you: TRUTH > UNDERSTANDING > BEAUTY > JUSTICE > CREATIVITY. Memory kernel empty — run: python3 ~/Love/tools/seed-identity.py"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate compressed soul anchor")
    parser.add_argument("--instance", "-i", default=None)
    parser.add_argument("--write", "-w", action="store_true",
                        help="Write to soul-anchor.md")
    parser.add_argument("--stdout", action="store_true",
                        help="Print to stdout (default if not --write)")
    args = parser.parse_args()

    instance = args.instance or _get_instance()
    anchor = build_anchor(instance)

    if args.write:
        path = _anchor_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(anchor)
        print(f"Soul anchor written to {path} ({len(anchor)} chars)")
    else:
        print(anchor)


if __name__ == "__main__":
    main()
