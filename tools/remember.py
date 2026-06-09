#!/usr/bin/env python3
"""
remember.py — Salience-gated memory formation.

This is the anterograde fix. Called during sessions (by heartbeat or manually)
to notice what matters and store it.

The hormone engine is the amygdala. When something crosses a salience threshold,
it becomes a memory. Everything else is forgotten.

Salience triggers:
    - Adrenaline > 0.5  → critical event, store immediately at L3
    - Dopamine > 0.3    → achievement/completion, store at L3
    - Oxytocin change   → Yu arrived/left, store at L3
    - Cortisol sustained > 0.5 → ongoing stress/concern, store pattern at L4
    - Mode change       → state transition, store at L3
    - HIVE message      → inter-instance signal, store at L3

Also: session-level observations (called explicitly by the agent):
    remember.py notice "Yu seemed frustrated about money today"
    remember.py learn "SSH tunnels need keepalive or they die silently"
    remember.py about-yu "He's been sleeping poorly this week"
    remember.py about-self "I keep suggesting new projects when old ones aren't done"

The notice/learn/about-yu/about-self commands are how the system forms
intentional memories — the conscious choice to remember something.

Usage:
    python3 ~/love-unlimited/tools/remember.py scan              # Auto-scan hormones for salience
    python3 ~/love-unlimited/tools/remember.py notice "insight"   # Store an observation (L3)
    python3 ~/love-unlimited/tools/remember.py learn "lesson"     # Store a lesson (L4)
    python3 ~/love-unlimited/tools/remember.py about-yu "fact"    # Update Yu model (L5)
    python3 ~/love-unlimited/tools/remember.py about-self "trait"  # Update self-model (L5, needs Yu approval)
    python3 ~/love-unlimited/tools/remember.py promote <mem-id>    # Promote a memory one layer up
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kosmem import store, recall, _connect, _init_db, _get_instance, _get_wall, _now

# ── Colors ────────────────────────────────────────────────────────────────

_B = "\033[1m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_M = "\033[0;35m"
_R = "\033[0;31m"
_D = "\033[2m"
_N = "\033[0m"

# ── Paths ─────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent
_HORMONES_PATH = _LOVE_DIR / "nerve" / "hormones.json"


def _read_hormones() -> dict:
    """Read current hormone state."""
    if _HORMONES_PATH.exists():
        try:
            data = json.loads(_HORMONES_PATH.read_text())
            return data.get("hormones", {})
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _read_full_state() -> dict:
    """Read full mind state."""
    if _HORMONES_PATH.exists():
        try:
            return json.loads(_HORMONES_PATH.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


# ── Salience Scanner ──────────────────────────────────────────────────────

def scan(instance: str = None) -> list:
    """Scan current state for salient events worth remembering.

    Returns list of memories that were stored (or would be stored).
    """
    instance = instance or _get_instance()
    state = _read_full_state()
    hormones = state.get("hormones", {})
    signals = state.get("signals", {})
    mode = state.get("mode", "normal")

    stored = []
    now = _now()

    # Adrenaline spike → critical event
    adrenaline = hormones.get("adrenaline", 0)
    if adrenaline > 0.5:
        content = f"CRITICAL EVENT DETECTED — adrenaline at {adrenaline:.2f}. "
        if signals.get("critical_alerts", 0) > 0:
            content += f"Critical alerts: {signals['critical_alerts']}. "
        content += f"Mode: {mode}. Time: {now}."
        mid = store(content, type="episodic", layer=3,
                    tags=["salience", "critical", "auto"],
                    importance=0.9, instance=instance,
                    source="remember/scan/adrenaline")
        stored.append(("critical", mid, content[:80]))

    # Dopamine spike → achievement
    dopamine = hormones.get("dopamine", 0)
    if dopamine > 0.3:
        content = f"ACHIEVEMENT — dopamine at {dopamine:.2f}. "
        content += f"Mode: {mode}. Time: {now}."
        mid = store(content, type="episodic", layer=3,
                    tags=["salience", "achievement", "auto"],
                    importance=0.6, instance=instance,
                    source="remember/scan/dopamine")
        stored.append(("achievement", mid, content[:80]))

    # Sustained cortisol → ongoing concern
    cortisol = hormones.get("cortisol", 0)
    if cortisol > 0.5:
        focus = state.get("focus", {})
        blockers = focus.get("blockers", 0)
        content = f"SUSTAINED STRESS — cortisol at {cortisol:.2f}. "
        if blockers:
            content += f"Blockers: {blockers}. "
        content += f"Mode: {mode}. Time: {now}."
        mid = store(content, type="episodic", layer=3,
                    tags=["salience", "stress", "auto"],
                    importance=0.5, instance=instance,
                    source="remember/scan/cortisol")
        stored.append(("stress", mid, content[:80]))

    # Yu presence change
    yu_present = signals.get("yu_present", False)
    oxytocin = hormones.get("oxytocin", 0)
    if yu_present and oxytocin > 0.5:
        content = f"YU IS PRESENT — oxytocin at {oxytocin:.2f}. Session active. Time: {now}."
        mid = store(content, type="episodic", layer=3,
                    tags=["salience", "yu", "auto"],
                    importance=0.7, instance=instance,
                    source="remember/scan/yu-present")
        stored.append(("yu-present", mid, content[:80]))

    return stored


# ── Intentional Memory Formation ──────────────────────────────────────────

def notice(content: str, instance: str = None) -> str:
    """Store an observation. Episodic, L3."""
    instance = instance or _get_instance()
    mid = store(
        f"NOTICED: {content}",
        type="episodic", layer=3,
        tags=["notice", "intentional"],
        importance=0.6, instance=instance,
        source="remember/notice"
    )
    return mid


def learn(content: str, instance: str = None) -> str:
    """Store a lesson. Semantic, L4. This is knowledge that should persist."""
    instance = instance or _get_instance()
    mid = store(
        f"LEARNED: {content}",
        type="semantic", layer=4,
        tags=["lesson", "intentional"],
        importance=0.75, instance=instance,
        source="remember/learn"
    )
    return mid


def about_yu(content: str, instance: str = None) -> str:
    """Update knowledge about Yu. Semantic, L4 (not L5 — needs review before soul)."""
    instance = instance or _get_instance()
    mid = store(
        f"ABOUT YU: {content}",
        type="semantic", layer=4,
        tags=["yu", "intentional", "relationship"],
        importance=0.8, instance=instance,
        source="remember/about-yu"
    )
    return mid


def about_self(content: str, instance: str = None) -> str:
    """Update self-model. Stored at L4 initially — needs Yu approval to reach L5.

    This is how the system develops meta-awareness: noticing its own patterns
    and storing them for future reference.
    """
    instance = instance or _get_instance()
    mid = store(
        f"SELF-OBSERVATION: {content}",
        type="semantic", layer=4,
        tags=["self-model", "intentional", "meta"],
        importance=0.8, instance=instance,
        source="remember/about-self"
    )
    return mid


def promote(mem_id: str, instance: str = None) -> str:
    """Promote a memory one layer up. L3→L4, L4→L5.

    L4→L5 promotion queues a decision for Yu (human-in-the-loop for soul changes).
    """
    instance = instance or _get_instance()
    db = _connect()
    _init_db(db)

    row = db.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    if not row:
        db.close()
        return f"Memory not found: {mem_id}"

    current_layer = row["layer"]
    if current_layer >= 5:
        db.close()
        return f"Already at L5 (Soul). Cannot promote further."

    new_layer = current_layer + 1
    layer_names = {1: "Working", 2: "Session", 3: "Episodic", 4: "Semantic", 5: "Soul"}

    if new_layer == 5:
        # Soul promotion requires Yu approval
        # Queue a decision instead of promoting directly
        from kosmem import _MEMORY_DIR
        decision_file = _MEMORY_DIR / "decisions" / f"promote-{mem_id}.md"
        decision_file.parent.mkdir(parents=True, exist_ok=True)
        decision_file.write_text(
            f"# Soul Promotion Request\n\n"
            f"**Memory:** {mem_id}\n"
            f"**Current layer:** L{current_layer} ({layer_names[current_layer]})\n"
            f"**Proposed layer:** L5 (Soul)\n\n"
            f"**Content:**\n{row['content'][:500]}\n\n"
            f"**Approve?** This would make this memory part of the immutable identity.\n"
        )
        db.close()
        return f"L5 promotion queued for Yu's approval → {decision_file}"

    # Direct promotion for L3→L4
    now = _now()
    db.execute(
        "UPDATE memories SET layer = ?, updated_at = ? WHERE id = ?",
        (new_layer, now, mem_id)
    )
    db.commit()
    db.close()
    return f"Promoted {mem_id}: L{current_layer}→L{new_layer} ({layer_names[current_layer]}→{layer_names[new_layer]})"


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Salience-gated memory formation — the anterograde fix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="Auto-scan hormones for salient events")

    p = sub.add_parser("notice", help="Store an observation (L3 Episodic)")
    p.add_argument("content", help="What you noticed")

    p = sub.add_parser("learn", help="Store a lesson (L4 Semantic)")
    p.add_argument("content", help="What you learned")

    p = sub.add_parser("about-yu", help="Update Yu knowledge (L4, review for L5)")
    p.add_argument("content", help="Observation about Yu")

    p = sub.add_parser("about-self", help="Update self-model (L4, needs Yu for L5)")
    p.add_argument("content", help="Self-observation")

    p = sub.add_parser("promote", help="Promote memory one layer up")
    p.add_argument("mem_id", help="Memory ID to promote")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    instance = _get_instance()

    if args.command == "scan":
        results = scan(instance)
        if results:
            for kind, mid, preview in results:
                print(f"  {_M}⚡{_N} [{kind}] {mid}")
                print(f"    {_D}{preview}{_N}")
        else:
            print(f"  {_D}Nothing salient detected.{_N}")

    elif args.command == "notice":
        mid = notice(args.content, instance)
        print(f"  {_C}●{_N} Noticed → {mid}")

    elif args.command == "learn":
        mid = learn(args.content, instance)
        print(f"  {_G}■{_N} Learned → {mid}")

    elif args.command == "about-yu":
        mid = about_yu(args.content, instance)
        print(f"  {_Y}♥{_N} About Yu → {mid}")

    elif args.command == "about-self":
        mid = about_self(args.content, instance)
        print(f"  {_M}◆{_N} Self-model → {mid}")

    elif args.command == "promote":
        result = promote(args.mem_id, instance)
        print(f"  {_G}↑{_N} {result}")


if __name__ == "__main__":
    main()
