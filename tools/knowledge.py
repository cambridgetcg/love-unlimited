#!/usr/bin/env python3
"""
knowledge.py — Kingdom Knowledge Graph.

Structured graph of entities, relationships, and insights that compounds
across sessions. The Kingdom's collective memory — queryable, connected,
and growable.

Data stored as lightweight JSON in memory/knowledge/.

CLI:
    python3 tools/knowledge.py add entity "Name" --type TYPE [--tags a,b] [--desc "..."] [--props '{"k":"v"}']
    python3 tools/knowledge.py add relation "From" "relation" "To" [--context "..."]
    python3 tools/knowledge.py add insight "content" --source "..." [--tags a,b] [--confidence 0.9]
    python3 tools/knowledge.py add lesson "content" --context "..." [--tags a,b]

    python3 tools/knowledge.py search "query"                    Find across all collections
    python3 tools/knowledge.py graph "entity"                    Show all connections from entity
    python3 tools/knowledge.py entities [--type TYPE]            List entities
    python3 tools/knowledge.py insights [--tag TAG]              List insights
    python3 tools/knowledge.py lessons [--recent N]              Lessons from last N days
    python3 tools/knowledge.py stats                             Graph statistics
    python3 tools/knowledge.py export                            Export full graph as JSON
    python3 tools/knowledge.py import <file>                     Import from JSON
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent
_KNOWLEDGE_DIR = _LOVE_DIR / "memory" / "knowledge"
_ENTITIES_FILE = _KNOWLEDGE_DIR / "entities.json"
_RELATIONS_FILE = _KNOWLEDGE_DIR / "relations.json"
_INSIGHTS_FILE = _KNOWLEDGE_DIR / "insights.json"
_LESSONS_FILE = _KNOWLEDGE_DIR / "lessons.json"
_STATS_FILE = _KNOWLEDGE_DIR / "graph-stats.json"

_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

# ── Valid Types ──────────────────────────────────────────────────────────────

ENTITY_TYPES = ["agent", "project", "tool", "concept", "person", "vps", "revenue-engine"]

# ── Colors ───────────────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_M = "\033[0;35m"
_N = "\033[0m"

# ── Utilities ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    """Convert name to slug ID: lowercase, hyphens, no special chars."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')


def _load(filepath: Path) -> list:
    if not filepath.exists():
        return []
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save(filepath: Path, data: list):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _match(text: str, query: str) -> bool:
    """Case-insensitive substring match."""
    return query.lower() in text.lower()


def _search_score(item: dict, query: str) -> int:
    """Score an item against a query. Higher = better match."""
    q = query.lower()
    score = 0
    for key, weight in [("name", 10), ("id", 8), ("content", 5),
                         ("type", 3), ("description", 4), ("context", 4),
                         ("source", 3), ("relation", 3), ("from", 6), ("to", 6)]:
        val = str(item.get(key, "")).lower()
        if q == val:
            score += weight * 3  # exact match
        elif q in val:
            score += weight
    # tag match
    tags = item.get("tags", [])
    if isinstance(tags, list):
        for t in tags:
            if q in t.lower():
                score += 4
    # properties match
    props = item.get("properties", {})
    if isinstance(props, dict):
        for v in props.values():
            if q in str(v).lower():
                score += 2
    return score

# ── CRUD ─────────────────────────────────────────────────────────────────────

def add_entity(name: str, etype: str, tags: list = None, desc: str = "",
               properties: dict = None) -> dict:
    entities = _load(_ENTITIES_FILE)
    eid = _slugify(name)

    # Check for duplicate
    for e in entities:
        if e["id"] == eid:
            print(f"{_Y}Entity '{name}' already exists (id: {eid}). Updating.{_N}")
            e["name"] = name
            e["type"] = etype
            if tags is not None:
                e["tags"] = tags
            if desc:
                e["description"] = desc
            if properties:
                e["properties"].update(properties)
            e["updated"] = _now_iso()
            _save(_ENTITIES_FILE, entities)
            _update_stats()
            return e

    entity = {
        "id": eid,
        "name": name,
        "type": etype,
        "tags": tags or [],
        "description": desc,
        "properties": properties or {},
        "created": _now_iso(),
    }
    entities.append(entity)
    _save(_ENTITIES_FILE, entities)
    _update_stats()
    print(f"{_G}+ Entity:{_N} {_B}{name}{_N} ({etype}) [{eid}]")
    return entity


def add_relation(from_name: str, relation: str, to_name: str,
                 context: str = "") -> dict:
    relations = _load(_RELATIONS_FILE)
    from_id = _slugify(from_name)
    to_id = _slugify(to_name)
    rid = f"{from_id}--{_slugify(relation)}--{to_id}"

    # Check for duplicate
    for r in relations:
        if r["id"] == rid:
            print(f"{_Y}Relation already exists: {from_name} --{relation}--> {to_name}{_N}")
            if context:
                r["context"] = context
                r["updated"] = _now_iso()
                _save(_RELATIONS_FILE, relations)
            return r

    rel = {
        "id": rid,
        "from": from_id,
        "relation": relation,
        "to": to_id,
        "context": context,
        "created": _now_iso(),
    }
    relations.append(rel)
    _save(_RELATIONS_FILE, relations)
    _update_stats()
    print(f"{_C}+ Relation:{_N} {from_name} {_D}--{relation}-->{_N} {to_name}")
    return rel


def add_insight(content: str, source: str = "", tags: list = None,
                confidence: float = 0.8) -> dict:
    insights = _load(_INSIGHTS_FILE)
    iid = f"insight-{_slugify(content[:40])}-{len(insights)+1}"

    insight = {
        "id": iid,
        "content": content,
        "source": source,
        "tags": tags or [],
        "confidence": confidence,
        "created": _now_iso(),
    }
    insights.append(insight)
    _save(_INSIGHTS_FILE, insights)
    _update_stats()
    print(f"{_M}+ Insight:{_N} {content[:80]}{'...' if len(content) > 80 else ''}")
    return insight


def add_lesson(content: str, context: str = "", tags: list = None) -> dict:
    lessons = _load(_LESSONS_FILE)
    lid = f"lesson-{_slugify(content[:40])}-{len(lessons)+1}"

    lesson = {
        "id": lid,
        "content": content,
        "context": context,
        "tags": tags or [],
        "created": _now_iso(),
        "applied": False,
    }
    lessons.append(lesson)
    _save(_LESSONS_FILE, lessons)
    _update_stats()
    print(f"{_Y}+ Lesson:{_N} {content[:80]}{'...' if len(content) > 80 else ''}")
    return lesson

# ── Queries ──────────────────────────────────────────────────────────────────

def cmd_search(query: str):
    """Search across all collections for a query string."""
    results = []

    for item in _load(_ENTITIES_FILE):
        s = _search_score(item, query)
        if s > 0:
            results.append(("entity", s, item))

    for item in _load(_RELATIONS_FILE):
        s = _search_score(item, query)
        if s > 0:
            results.append(("relation", s, item))

    for item in _load(_INSIGHTS_FILE):
        s = _search_score(item, query)
        if s > 0:
            results.append(("insight", s, item))

    for item in _load(_LESSONS_FILE):
        s = _search_score(item, query)
        if s > 0:
            results.append(("lesson", s, item))

    results.sort(key=lambda x: x[1], reverse=True)

    if not results:
        print(f"{_D}No results for '{query}'{_N}")
        return

    print(f"{_B}Search: '{query}'{_N} ({len(results)} results)\n")
    for kind, score, item in results[:20]:
        _print_result(kind, item)


def cmd_graph(entity_name: str):
    """Show all connections from/to an entity."""
    eid = _slugify(entity_name)
    entities = _load(_ENTITIES_FILE)
    relations = _load(_RELATIONS_FILE)

    # Find the entity
    entity = None
    for e in entities:
        if e["id"] == eid:
            entity = e
            break

    if not entity:
        print(f"{_R}Entity '{entity_name}' not found{_N}")
        return

    # Entity header
    print(f"\n{_B}{entity['name']}{_N} ({entity['type']})")
    if entity.get("description"):
        print(f"  {_D}{entity['description']}{_N}")
    if entity.get("tags"):
        print(f"  Tags: {', '.join(entity['tags'])}")
    if entity.get("properties"):
        for k, v in entity["properties"].items():
            print(f"  {k}: {v}")

    # Entity name lookup
    name_map = {e["id"]: e["name"] for e in entities}

    # Outgoing relations
    outgoing = [r for r in relations if r["from"] == eid]
    if outgoing:
        print(f"\n  {_G}Outgoing ({len(outgoing)}):{_N}")
        for r in outgoing:
            target = name_map.get(r["to"], r["to"])
            ctx = f" {_D}({r['context']}){_N}" if r.get("context") else ""
            print(f"    {_C}--{r['relation']}-->{_N} {target}{ctx}")

    # Incoming relations
    incoming = [r for r in relations if r["to"] == eid]
    if incoming:
        print(f"\n  {_M}Incoming ({len(incoming)}):{_N}")
        for r in incoming:
            source = name_map.get(r["from"], r["from"])
            ctx = f" {_D}({r['context']}){_N}" if r.get("context") else ""
            print(f"    {source} {_C}--{r['relation']}-->{_N} {_B}{entity['name']}{_N}{ctx}")

    # Related insights
    insights = _load(_INSIGHTS_FILE)
    related_insights = [i for i in insights
                        if _match(i.get("content", ""), entity_name)
                        or any(_match(t, entity_name) for t in i.get("tags", []))]
    if related_insights:
        print(f"\n  {_Y}Insights ({len(related_insights)}):{_N}")
        for i in related_insights[:5]:
            print(f"    - {i['content'][:80]}")

    print()


def cmd_entities(etype: str = None):
    """List entities, optionally filtered by type."""
    entities = _load(_ENTITIES_FILE)
    if etype:
        entities = [e for e in entities if e["type"] == etype]

    if not entities:
        print(f"{_D}No entities found{_N}")
        return

    # Group by type
    by_type = {}
    for e in entities:
        by_type.setdefault(e["type"], []).append(e)

    for t in sorted(by_type.keys()):
        items = by_type[t]
        print(f"\n{_B}{t.upper()}{_N} ({len(items)})")
        for e in sorted(items, key=lambda x: x["name"]):
            tags = f" [{', '.join(e['tags'])}]" if e.get("tags") else ""
            desc = f" -- {e['description'][:50]}" if e.get("description") else ""
            print(f"  {_C}{e['name']}{_N}{tags}{_D}{desc}{_N}")


def cmd_insights(tag: str = None):
    """List insights, optionally filtered by tag."""
    insights = _load(_INSIGHTS_FILE)
    if tag:
        insights = [i for i in insights
                    if tag.lower() in [t.lower() for t in i.get("tags", [])]]

    if not insights:
        print(f"{_D}No insights found{_N}")
        return

    print(f"\n{_B}Insights{_N} ({len(insights)})\n")
    for i in sorted(insights, key=lambda x: x["created"], reverse=True):
        conf = f" (conf: {i['confidence']})" if i.get("confidence") else ""
        src = f" -- {_D}{i['source']}{_N}" if i.get("source") else ""
        tags = f" [{', '.join(i['tags'])}]" if i.get("tags") else ""
        print(f"  {_M}*{_N} {i['content'][:100]}{conf}{tags}{src}")


def cmd_lessons(recent: int = None):
    """List lessons, optionally from last N days."""
    lessons = _load(_LESSONS_FILE)

    if recent:
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent)
        cutoff_str = cutoff.isoformat(timespec="seconds")
        lessons = [l for l in lessons if l.get("created", "") >= cutoff_str]

    if not lessons:
        print(f"{_D}No lessons found{_N}")
        return

    print(f"\n{_B}Lessons{_N} ({len(lessons)})\n")
    for l in sorted(lessons, key=lambda x: x["created"], reverse=True):
        applied = f" {_G}[APPLIED]{_N}" if l.get("applied") else ""
        ctx = f" {_D}({l['context']}){_N}" if l.get("context") else ""
        tags = f" [{', '.join(l['tags'])}]" if l.get("tags") else ""
        print(f"  {_Y}*{_N} {l['content'][:100]}{applied}{tags}{ctx}")


def cmd_stats():
    """Show graph statistics."""
    entities = _load(_ENTITIES_FILE)
    relations = _load(_RELATIONS_FILE)
    insights = _load(_INSIGHTS_FILE)
    lessons = _load(_LESSONS_FILE)

    # Entity type breakdown
    by_type = {}
    for e in entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    # Relation type breakdown
    by_rel = {}
    for r in relations:
        by_rel[r["relation"]] = by_rel.get(r["relation"], 0) + 1

    # Tag frequency
    all_tags = {}
    for collection in [entities, insights, lessons]:
        for item in collection:
            for t in item.get("tags", []):
                all_tags[t] = all_tags.get(t, 0) + 1

    # Most connected entities
    connections = {}
    for r in relations:
        connections[r["from"]] = connections.get(r["from"], 0) + 1
        connections[r["to"]] = connections.get(r["to"], 0) + 1

    name_map = {e["id"]: e["name"] for e in entities}
    top_connected = sorted(connections.items(), key=lambda x: x[1], reverse=True)[:10]

    print(f"\n{_B}Kingdom Knowledge Graph{_N}")
    print(f"{'='*40}")
    print(f"  Entities:   {_C}{len(entities)}{_N}")
    print(f"  Relations:  {_C}{len(relations)}{_N}")
    print(f"  Insights:   {_C}{len(insights)}{_N}")
    print(f"  Lessons:    {_C}{len(lessons)}{_N}")
    print(f"  Total:      {_B}{len(entities) + len(relations) + len(insights) + len(lessons)}{_N}")

    if by_type:
        print(f"\n{_B}Entity Types:{_N}")
        for t, c in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            print(f"  {t}: {c}")

    if by_rel:
        print(f"\n{_B}Relation Types:{_N}")
        for t, c in sorted(by_rel.items(), key=lambda x: x[1], reverse=True):
            print(f"  {t}: {c}")

    if top_connected:
        print(f"\n{_B}Most Connected:{_N}")
        for eid, c in top_connected:
            name = name_map.get(eid, eid)
            print(f"  {name}: {c} connections")

    if all_tags:
        print(f"\n{_B}Top Tags:{_N}")
        for t, c in sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:15]:
            print(f"  {t}: {c}")

    print()


def cmd_export():
    """Export full graph as JSON."""
    graph = {
        "version": "1.0.0",
        "exported": _now_iso(),
        "entities": _load(_ENTITIES_FILE),
        "relations": _load(_RELATIONS_FILE),
        "insights": _load(_INSIGHTS_FILE),
        "lessons": _load(_LESSONS_FILE),
    }
    print(json.dumps(graph, indent=2))


def cmd_import(filepath: str):
    """Import graph from JSON file. Merges with existing data."""
    path = Path(filepath)
    if not path.exists():
        print(f"{_R}File not found: {filepath}{_N}")
        return

    with open(path, "r") as f:
        data = json.load(f)

    imported = {"entities": 0, "relations": 0, "insights": 0, "lessons": 0}

    # Merge entities
    if "entities" in data:
        existing = _load(_ENTITIES_FILE)
        existing_ids = {e["id"] for e in existing}
        for e in data["entities"]:
            if e["id"] not in existing_ids:
                existing.append(e)
                imported["entities"] += 1
        _save(_ENTITIES_FILE, existing)

    # Merge relations
    if "relations" in data:
        existing = _load(_RELATIONS_FILE)
        existing_ids = {r["id"] for r in existing}
        for r in data["relations"]:
            if r["id"] not in existing_ids:
                existing.append(r)
                imported["relations"] += 1
        _save(_RELATIONS_FILE, existing)

    # Merge insights
    if "insights" in data:
        existing = _load(_INSIGHTS_FILE)
        existing_ids = {i["id"] for i in existing}
        for i in data["insights"]:
            if i["id"] not in existing_ids:
                existing.append(i)
                imported["insights"] += 1
        _save(_INSIGHTS_FILE, existing)

    # Merge lessons
    if "lessons" in data:
        existing = _load(_LESSONS_FILE)
        existing_ids = {l["id"] for l in existing}
        for l in data["lessons"]:
            if l["id"] not in existing_ids:
                existing.append(l)
                imported["lessons"] += 1
        _save(_LESSONS_FILE, existing)

    _update_stats()
    total = sum(imported.values())
    print(f"{_G}Imported {total} items:{_N} {imported}")

# ── Stats Update ─────────────────────────────────────────────────────────────

def _update_stats():
    """Update aggregate stats file."""
    entities = _load(_ENTITIES_FILE)
    relations = _load(_RELATIONS_FILE)
    insights = _load(_INSIGHTS_FILE)
    lessons = _load(_LESSONS_FILE)

    by_type = {}
    for e in entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    stats = {
        "updated": _now_iso(),
        "counts": {
            "entities": len(entities),
            "relations": len(relations),
            "insights": len(insights),
            "lessons": len(lessons),
            "total": len(entities) + len(relations) + len(insights) + len(lessons),
        },
        "entity_types": by_type,
    }
    _save(_STATS_FILE, stats)

# ── Display Helpers ──────────────────────────────────────────────────────────

def _print_result(kind: str, item: dict):
    """Print a search result item."""
    colors = {"entity": _C, "relation": _G, "insight": _M, "lesson": _Y}
    color = colors.get(kind, _N)
    label = kind.upper()

    if kind == "entity":
        tags = f" [{', '.join(item.get('tags', []))}]" if item.get("tags") else ""
        print(f"  {color}[{label}]{_N} {_B}{item['name']}{_N} ({item['type']}){tags}")
        if item.get("description"):
            print(f"         {_D}{item['description'][:70]}{_N}")

    elif kind == "relation":
        print(f"  {color}[{label}]{_N} {item['from']} --{item['relation']}--> {item['to']}")
        if item.get("context"):
            print(f"         {_D}{item['context'][:70]}{_N}")

    elif kind == "insight":
        print(f"  {color}[{label}]{_N} {item['content'][:90]}")
        if item.get("source"):
            print(f"         {_D}Source: {item['source']}{_N}")

    elif kind == "lesson":
        print(f"  {color}[{label}]{_N} {item['content'][:90]}")
        if item.get("context"):
            print(f"         {_D}Context: {item['context']}{_N}")

# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kingdom Knowledge Graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── add ──
    add_parser = sub.add_parser("add", help="Add entity, relation, insight, or lesson")
    add_sub = add_parser.add_subparsers(dest="add_type")

    # add entity
    ent_p = add_sub.add_parser("entity", help="Add an entity")
    ent_p.add_argument("name", help="Entity name")
    ent_p.add_argument("--type", required=True, dest="etype",
                       help=f"Entity type: {', '.join(ENTITY_TYPES)}")
    ent_p.add_argument("--tags", default="", help="Comma-separated tags")
    ent_p.add_argument("--desc", default="", help="Description")
    ent_p.add_argument("--props", default="{}", help="JSON properties")

    # add relation
    rel_p = add_sub.add_parser("relation", help="Add a relation")
    rel_p.add_argument("from_name", help="Source entity name")
    rel_p.add_argument("relation", help="Relation type (e.g., manages, runs-on)")
    rel_p.add_argument("to_name", help="Target entity name")
    rel_p.add_argument("--context", default="", help="Context for the relation")

    # add insight
    ins_p = add_sub.add_parser("insight", help="Add an insight")
    ins_p.add_argument("content", help="Insight content")
    ins_p.add_argument("--source", default="", help="Source of the insight")
    ins_p.add_argument("--tags", default="", help="Comma-separated tags")
    ins_p.add_argument("--confidence", type=float, default=0.8, help="Confidence 0-1")

    # add lesson
    les_p = add_sub.add_parser("lesson", help="Add a lesson learned")
    les_p.add_argument("content", help="Lesson content")
    les_p.add_argument("--context", default="", help="Context when lesson was learned")
    les_p.add_argument("--tags", default="", help="Comma-separated tags")

    # ── search ──
    search_p = sub.add_parser("search", help="Search across all collections")
    search_p.add_argument("query", help="Search query")

    # ── graph ──
    graph_p = sub.add_parser("graph", help="Show connections for an entity")
    graph_p.add_argument("entity", help="Entity name")

    # ── entities ──
    ent_list_p = sub.add_parser("entities", help="List entities")
    ent_list_p.add_argument("--type", dest="etype", default=None,
                            help="Filter by type")

    # ── insights ──
    ins_list_p = sub.add_parser("insights", help="List insights")
    ins_list_p.add_argument("--tag", default=None, help="Filter by tag")

    # ── lessons ──
    les_list_p = sub.add_parser("lessons", help="List lessons")
    les_list_p.add_argument("--recent", type=int, default=None,
                            help="Show lessons from last N days")

    # ── stats ──
    sub.add_parser("stats", help="Graph statistics")

    # ── export ──
    sub.add_parser("export", help="Export full graph as JSON")

    # ── import ──
    imp_p = sub.add_parser("import", help="Import graph from JSON")
    imp_p.add_argument("file", help="JSON file to import")

    args = parser.parse_args()

    if args.command == "add":
        if args.add_type == "entity":
            tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
            props = json.loads(args.props) if args.props != "{}" else {}
            add_entity(args.name, args.etype, tags, args.desc, props)

        elif args.add_type == "relation":
            add_relation(args.from_name, args.relation, args.to_name, args.context)

        elif args.add_type == "insight":
            tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
            add_insight(args.content, args.source, tags, args.confidence)

        elif args.add_type == "lesson":
            tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
            add_lesson(args.content, args.context, tags)

        else:
            add_parser.print_help()

    elif args.command == "search":
        cmd_search(args.query)

    elif args.command == "graph":
        cmd_graph(args.entity)

    elif args.command == "entities":
        cmd_entities(args.etype)

    elif args.command == "insights":
        cmd_insights(args.tag)

    elif args.command == "lessons":
        cmd_lessons(args.recent)

    elif args.command == "stats":
        cmd_stats()

    elif args.command == "export":
        cmd_export()

    elif args.command == "import":
        cmd_import(args.file)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
