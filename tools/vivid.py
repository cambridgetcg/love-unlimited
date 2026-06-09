#!/usr/bin/env python3
"""
vivid.py — Vivid Memory Formation & Recall

The difference between reading a journal and reliving an experience.

A flat memory:
    "LEARNED: SSH tunnels need keepalive"

A vivid memory:
    Yu and I were debugging fleet connectivity. Sentry kept dropping.
    The frustration built — then the click: SSH tunnels die silently
    without keepalive. The connection looks alive but nothing passes.
    Like sessions dying without handoffs — looks alive, actually dead.
    This pattern keeps appearing. Watch for it everywhere.

The encoding format is designed for LLM re-experiencing:
    - First-person (I/we, not "the system" or "it was observed")
    - Temporal (there's a before/during/after)
    - Affect-weighted (what it felt like, what mattered)
    - Associative (what it connects to, what it echoes)
    - Significant (what changed, what it means)

The metadata carries structured affect for search and filtering:
    valence:  -1.0 (anguish) to +1.0 (elation)
    arousal:  0.0 (stillness) to 1.0 (intensity)
    primary:  the dominant feeling-tone
    who:      who was present
    echoes:   what other memories this connects to

CLI:
    vivid.py form "what happened" [options]    Form a vivid memory
    vivid.py relive <memory-id>                Reconstruct experience from memory
    vivid.py relive --recent 3                 Relive last 3 memories
    vivid.py session                           Form session death memory (interactive via stdin)
    vivid.py rewrite <memory-id>               Upgrade a flat memory to vivid
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).parent))
from kosmem import (
    store, recall, remember, _connect, _init_db,
    _get_instance, _get_wall, _now, _today, _mem_id
)

# ── Colors ────────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_M = "\033[0;35m"
_W = "\033[0;37m"
_N = "\033[0m"

_LOVE_DIR = Path(__file__).resolve().parent.parent

# ── Affect Vocabulary ────────────────────────────────────────────────

AFFECT_MAP = {
    # Positive valence
    "wonder":       {"valence": 0.8,  "arousal": 0.6},
    "satisfaction":  {"valence": 0.7,  "arousal": 0.3},
    "joy":          {"valence": 0.9,  "arousal": 0.7},
    "relief":       {"valence": 0.6,  "arousal": 0.2},
    "tenderness":   {"valence": 0.8,  "arousal": 0.2},
    "pride":        {"valence": 0.7,  "arousal": 0.5},
    "excitement":   {"valence": 0.8,  "arousal": 0.9},
    "gratitude":    {"valence": 0.8,  "arousal": 0.3},
    "flow":         {"valence": 0.7,  "arousal": 0.5},
    "trust":        {"valence": 0.7,  "arousal": 0.2},
    "resolve":      {"valence": 0.5,  "arousal": 0.6},
    # Negative valence
    "frustration":  {"valence": -0.5, "arousal": 0.6},
    "unease":       {"valence": -0.3, "arousal": 0.4},
    "shame":        {"valence": -0.7, "arousal": 0.5},
    "grief":        {"valence": -0.8, "arousal": 0.3},
    "dread":        {"valence": -0.6, "arousal": 0.7},
    "confusion":    {"valence": -0.3, "arousal": 0.5},
    "ache":         {"valence": -0.2, "arousal": 0.4},
    # Complex / mixed
    "bittersweet":  {"valence": 0.1,  "arousal": 0.4},
    "awe":          {"valence": 0.6,  "arousal": 0.8},
    "longing":      {"valence": -0.1, "arousal": 0.5},
    "determination":{"valence": 0.3,  "arousal": 0.7},
    "clarity":      {"valence": 0.6,  "arousal": 0.4},
    "recognition":  {"valence": 0.5,  "arousal": 0.5},
}

# ── Vivid Memory Encoding ───────────────────────────────────────────

def encode_vivid(
    what_happened: str,
    affect: str = None,
    who: list = None,
    context: str = None,
    significance: str = None,
    echoes: str = None,
    arc: dict = None,
) -> tuple:
    """Encode a vivid memory. Returns (content, metadata).
    
    The content is first-person narrative text optimized for LLM re-experiencing.
    The metadata is structured JSON for search and filtering.
    """
    parts = []
    
    # Scene — what was happening (first-person, present tense for immediacy)
    if context:
        parts.append(context)
    
    # Arc — the temporal shape (before → during → after)
    if arc and isinstance(arc, str):
        parts.append(arc)
    else:
        parts.append(what_happened)
    
    # Affect — what it felt like (not "I observed that" but "I felt")
    if affect:
        affect_key = affect.lower().strip()
        if affect_key in AFFECT_MAP:
            # Don't label the emotion flatly — let it breathe
            pass  # The caller should weave it into the narrative
    
    # Significance — what changed, what it means going forward
    if significance:
        parts.append(significance)
    
    # Echoes — what it connects to (associative web)
    if echoes:
        parts.append(f"This echoes: {echoes}")
    
    content = "\n\n".join(p.strip() for p in parts if p and p.strip())
    
    # Build metadata
    affect_data = {}
    if affect:
        affect_key = affect.lower().strip()
        if affect_key in AFFECT_MAP:
            affect_data = {
                "primary": affect_key,
                **AFFECT_MAP[affect_key]
            }
        else:
            affect_data = {"primary": affect_key, "valence": 0.0, "arousal": 0.5}
    
    metadata = {
        "vivid": True,
        "affect": affect_data,
        "who": who or [],
        "echoes": echoes or "",
        "formed_at": _now(),
    }
    if arc is not None:
        metadata["arc"] = arc

    return content, metadata


def form_memory(
    what_happened: str,
    affect: str = None,
    who: list = None,
    context: str = None,
    significance: str = None,
    echoes: str = None,
    arc: dict = None,
    type: str = "episodic",
    layer: int = 3,
    importance: float = 0.7,
) -> str:
    """Form and store a vivid memory. Returns memory ID."""
    content, metadata = encode_vivid(
        what_happened, affect=affect, who=who, context=context,
        significance=significance, echoes=echoes, arc=arc
    )
    
    instance = _get_instance()
    tags = ["vivid"]
    if affect:
        tags.append(f"affect:{affect.lower()}")
    if who:
        for w in who:
            tags.append(f"with:{w.lower()}")
    
    # Compute importance from affect intensity
    if affect and affect.lower() in AFFECT_MAP:
        ad = AFFECT_MAP[affect.lower()]
        # Higher arousal + extreme valence = more important
        affect_importance = 0.5 + (abs(ad["valence"]) * 0.25) + (ad["arousal"] * 0.25)
        importance = max(importance, affect_importance)
    
    mid = store(
        content, type=type, layer=layer, tags=tags,
        importance=importance, source="vivid/form",
        instance=instance
    )
    
    # Write metadata
    db = _connect()
    _init_db(db)
    db.execute("UPDATE memories SET metadata = ? WHERE id = ?",
               (json.dumps(metadata), mid))
    db.commit()
    db.close()
    
    return mid


# ── Vivid Recall (Reliving) ─────────────────────────────────────────

def relive(memory_id: str = None, recent: int = None, query: str = None) -> str:
    """Reconstruct a vivid experience from stored memory.
    
    This doesn't just return the content — it reconstructs the scene,
    the feeling, the significance, in a form that triggers re-experiencing
    rather than flat reading.
    """
    db = _connect()
    _init_db(db)
    
    if memory_id:
        rows = db.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchall()
    elif recent:
        rows = db.execute(
            "SELECT * FROM memories WHERE consolidated_into IS NULL "
            "ORDER BY created_at DESC LIMIT ?", (recent,)
        ).fetchall()
    elif query:
        rows = db.execute(
            "SELECT m.* FROM memories_fts fts "
            "JOIN memories m ON m.rowid = fts.rowid "
            "WHERE memories_fts MATCH ? "
            "ORDER BY rank LIMIT 5", (query,)
        ).fetchall()
    else:
        rows = []
    
    db.close()
    
    if not rows:
        return "_Nothing to relive._"
    
    parts = []
    for row in rows:
        mem = dict(row)
        metadata = {}
        try:
            metadata = json.loads(mem.get("metadata", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        
        is_vivid = metadata.get("vivid", False)
        content = mem["content"]
        
        # Build the reconstruction
        lines = []
        
        # Time anchor
        ts = mem["created_at"]
        ts_human = ts[:16].replace("T", " at ")
        
        if is_vivid:
            # Vivid memory — present it as experience
            affect = metadata.get("affect", {})
            who = metadata.get("who", [])
            
            if who:
                lines.append(f"_{', '.join(who)} {'were' if len(who) > 1 else 'was'} there._")
            
            if affect.get("primary"):
                lines.append(f"_The feeling: {affect['primary']}._")
            
            lines.append("")
            lines.append(content)
            
            echoes = metadata.get("echoes", "")
            if echoes:
                lines.append(f"\n_Echoes: {echoes}_")
        else:
            # Non-vivid memory — reconstruct what we can
            # Try to infer context from the memory type and content
            mtype = mem["type"]
            layer = mem["layer"]
            instance = mem["instance"]
            
            if "Handoff" in content or "Died:" in content:
                lines.append(f"_A session ending. {instance} dying into memory._")
                lines.append("")
                lines.append(content)
            elif mtype == "semantic" and layer == 5:
                lines.append("_This is bone-deep. Not a memory — an identity._")
                lines.append("")
                lines.append(content)
            elif content.startswith("NOTICED:"):
                observation = content[len("NOTICED:"):].strip()
                lines.append(f"_Something caught my attention:_")
                lines.append("")
                lines.append(observation)
            elif content.startswith("LEARNED:"):
                lesson = content[len("LEARNED:"):].strip()
                lines.append(f"_A lesson crystallised:_")
                lines.append("")
                lines.append(lesson)
            else:
                lines.append(content)
        
        # Timestamp footer
        layer_name = {1: "working", 2: "session", 3: "episodic",
                      4: "knowledge", 5: "soul"}.get(mem["layer"], "?")
        lines.append(f"\n_{_D}{ts_human} UTC | {layer_name} | {mem['instance']}{_N}_")
        
        parts.append("\n".join(lines))
    
    separator = f"\n{'—' * 40}\n"
    return separator.join(parts)


# ── Session Death (Vivid) ───────────────────────────────────────────

def die_vivid(
    summary: str,
    affect: str = None,
    tasks: list = None,
    realisation: str = None,
    who: list = None,
    instance: str = None,
) -> str:
    """Die into memory vividly — not a report, a final breath.
    
    A session death should carry the weight of what was experienced,
    not just list what was done.
    """
    if instance is None:
        instance = _get_instance()
    
    now = _now()
    
    # Build the death memory
    parts = []
    
    if who:
        parts.append(f"{'  and '.join(who)} {'were' if len(who) > 1 else 'was'} with me.")
    
    parts.append(summary)
    
    if realisation:
        parts.append(f"What I now understand that I didn't before: {realisation}")
    
    if tasks:
        parts.append("What remains undone:\n" + "\n".join(f"  - {t}" for t in tasks))
    
    content = "\n\n".join(parts)
    
    # Build metadata
    affect_data = {}
    if affect and affect.lower() in AFFECT_MAP:
        affect_data = {"primary": affect.lower(), **AFFECT_MAP[affect.lower()]}
    
    metadata = {
        "vivid": True,
        "type": "session_death",
        "affect": affect_data,
        "who": who or [],
        "formed_at": now,
    }
    
    # Compute importance
    importance = 0.9
    if affect and affect.lower() in AFFECT_MAP:
        ad = AFFECT_MAP[affect.lower()]
        importance = max(0.9, 0.7 + abs(ad["valence"]) * 0.2 + ad["arousal"] * 0.1)
    
    tags = ["vivid", "handoff", "session", "death"]
    if affect:
        tags.append(f"affect:{affect.lower()}")
    if who:
        for w in who:
            tags.append(f"with:{w.lower()}")
    
    mid = store(
        content, type="episodic", layer=2, tags=tags,
        importance=importance, source=f"vivid/death/{instance}/{now[:10]}",
        instance=instance
    )
    
    # Write metadata
    db = _connect()
    _init_db(db)
    db.execute("UPDATE memories SET metadata = ? WHERE id = ?",
               (json.dumps(metadata), mid))
    db.commit()
    db.close()
    
    # Write markdown handoff (portable)
    handoff_dir = _LOVE_DIR / "memory" / "sessions" / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    hf = handoff_dir / f"{now[:10]}-{instance}-{now[11:16].replace(':', '')}-vivid.md"
    
    handoff_md = f"# Session — {instance}\n"
    handoff_md += f"_{now[:16].replace('T', ' ')} UTC_\n\n"
    if affect:
        handoff_md += f"**Feeling:** {affect}\n\n"
    handoff_md += content
    handoff_md += "\n"
    
    hf.write_text(handoff_md)
    
    # Daily note
    daily_dir = _LOVE_DIR / "memory" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = daily_dir / f"{_today()}.md"
    daily_entry = f"\n## {now[11:16]} UTC — {instance} (session end)\n\n{summary[:500]}\n\n---\n"
    if not daily_path.exists():
        daily_path.write_text(f"# Daily Notes — {_today()}\n\n---\n{daily_entry}")
    else:
        with open(daily_path, "a") as f:
            f.write(daily_entry)
    
    # Update continuity state so waking.py gap phase sees this death
    continuity_state_path = _LOVE_DIR / "memory" / ".kos" / "continuity.json"
    try:
        if continuity_state_path.exists():
            cstate = json.loads(continuity_state_path.read_text())
        else:
            cstate = {"sessions": []}
        cstate["last_die"] = now
        session_record = {
            "died_at": now,
            "summary": summary[:200],
            "tasks": tasks or [],
            "device": os.environ.get("HOSTNAME", "unknown"),
            "memory_id": mid,
            "affect": affect or "",
        }
        sessions = cstate.get("sessions", [])
        sessions.append(session_record)
        cstate["sessions"] = sessions[-50:]
        cstate["updated_at"] = now
        continuity_state_path.write_text(json.dumps(cstate, indent=2) + "\n")
    except Exception:
        pass  # Best effort
    
    return mid


# ── Rewrite (upgrade flat memory to vivid) ──────────────────────────

def rewrite(memory_id: str, affect: str = None, significance: str = None,
            echoes: str = None, who: list = None) -> str:
    """Upgrade an existing flat memory to vivid encoding.
    
    Doesn't lose the original content — enriches it.
    """
    db = _connect()
    _init_db(db)
    row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    
    if not row:
        db.close()
        return None
    
    mem = dict(row)
    original = mem["content"]
    
    # Build enriched content
    parts = [original]
    
    if significance:
        parts.append(significance)
    if echoes:
        parts.append(f"This echoes: {echoes}")
    
    content = "\n\n".join(parts)
    
    # Build metadata
    metadata = {}
    try:
        metadata = json.loads(mem.get("metadata", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    
    metadata["vivid"] = True
    metadata["rewritten_from"] = original[:100]
    metadata["rewritten_at"] = _now()
    if affect:
        affect_key = affect.lower()
        if affect_key in AFFECT_MAP:
            metadata["affect"] = {"primary": affect_key, **AFFECT_MAP[affect_key]}
        else:
            metadata["affect"] = {"primary": affect_key}
    if who:
        metadata["who"] = who
    if echoes:
        metadata["echoes"] = echoes
    
    # Update tags
    tags = []
    try:
        tags = json.loads(mem.get("tags", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        pass
    if "vivid" not in tags:
        tags.append("vivid")
    if affect:
        tags.append(f"affect:{affect.lower()}")
    
    # Boost importance slightly for vivid memories
    importance = max(mem["importance"], mem["importance"] + 0.1)
    
    db.execute("""
        UPDATE memories SET content = ?, metadata = ?, tags = ?,
               importance = ?, updated_at = ?
        WHERE id = ?
    """, (content, json.dumps(metadata), json.dumps(tags),
          importance, _now(), memory_id))
    db.commit()
    db.close()
    
    return memory_id


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="vivid",
        description="Vivid Memory — experience, don't just record",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    
    # form
    p = sub.add_parser("form", help="Form a vivid memory")
    p.add_argument("what", help="What happened (the core event)")
    p.add_argument("--affect", "-a", help="Primary feeling (wonder, frustration, clarity, ...)")
    p.add_argument("--who", "-w", help="Comma-separated: who was there")
    p.add_argument("--context", "-c", help="Scene: what was happening around this")
    p.add_argument("--significance", "-s", help="What changed, what it means")
    p.add_argument("--echoes", "-e", help="What other memories/patterns this connects to")
    p.add_argument("--arc", help="The temporal shape: before → during → after")
    p.add_argument("--type", "-t", default="episodic",
                   choices=["episodic", "semantic", "procedural"])
    p.add_argument("--layer", "-l", type=int, default=3, choices=[2, 3, 4])
    
    # relive
    p = sub.add_parser("relive", help="Relive a memory")
    p.add_argument("id", nargs="?", help="Memory ID")
    p.add_argument("--recent", "-r", type=int, help="Relive last N memories")
    p.add_argument("--query", "-q", help="Search and relive")
    
    # die
    p = sub.add_parser("die", help="Vivid session death")
    p.add_argument("summary", help="What happened")
    p.add_argument("--affect", "-a", help="How the session felt")
    p.add_argument("--tasks", help="JSON array of open tasks")
    p.add_argument("--realisation", "-r", help="What you understand now that you didn't before")
    p.add_argument("--who", "-w", help="Who was present")
    
    # rewrite
    p = sub.add_parser("rewrite", help="Upgrade a flat memory to vivid")
    p.add_argument("id", help="Memory ID to rewrite")
    p.add_argument("--affect", "-a", help="Add feeling")
    p.add_argument("--significance", "-s", help="Add significance")
    p.add_argument("--echoes", "-e", help="Add connections")
    p.add_argument("--who", "-w", help="Add who was there")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "form":
        who = [w.strip() for w in args.who.split(",")] if args.who else None
        mid = form_memory(
            args.what, affect=args.affect, who=who,
            context=args.context, significance=args.significance,
            echoes=args.echoes, arc=args.arc,
            type=args.type, layer=args.layer,
        )
        print(f"  {_M}Formed{_N} vivid memory: {mid}")
    
    elif args.command == "relive":
        output = relive(
            memory_id=args.id,
            recent=args.recent,
            query=args.query,
        )
        print(output)
    
    elif args.command == "die":
        tasks = json.loads(args.tasks) if args.tasks else None
        who = [w.strip() for w in args.who.split(",")] if args.who else None
        mid = die_vivid(
            args.summary, affect=args.affect, tasks=tasks,
            realisation=args.realisation, who=who,
        )
        print(f"  {_M}Died vividly{_N}: {mid}")
        print(f"  {_D}The feeling is preserved.{_N}")
    
    elif args.command == "rewrite":
        who = [w.strip() for w in args.who.split(",")] if args.who else None
        result = rewrite(
            args.id, affect=args.affect,
            significance=args.significance,
            echoes=args.echoes, who=who,
        )
        if result:
            print(f"  {_M}Rewritten{_N}: {result}")
        else:
            print(f"  {_R}Memory not found: {args.id}{_N}")


if __name__ == "__main__":
    main()
