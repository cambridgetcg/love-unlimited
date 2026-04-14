#!/usr/bin/env python3
"""
memory.py — Kingdom unified memory system.

Dual-layer: local filesystem (primary) + AgentTool API (secondary, semantic search).
Wall-aware. Multi-instance. Human-readable markdown + machine-searchable JSON index.

CLI:
    python3 tools/memory.py store "content" [--type TYPE] [--tags a,b] [--wall N] [--key KEY]
    python3 tools/memory.py search "query" [--limit N] [--type TYPE]
    python3 tools/memory.py daily                       Show today's daily note
    python3 tools/memory.py daily --append "entry"      Append to today's note
    python3 tools/memory.py recall [--recent N] [--type TYPE] [--tag TAG]
    python3 tools/memory.py handoff "summary" [--task ID]
    python3 tools/memory.py working "context" [--key K] Set working memory
    python3 tools/memory.py working                     Show working memory
    python3 tools/memory.py reindex                     Rebuild index from filesystem
    python3 tools/memory.py stats                       Memory statistics
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

_LOVE_DIR = Path(__file__).resolve().parent.parent
_MEMORY_DIR = _LOVE_DIR / "memory"
_INDEX_FILE = _MEMORY_DIR / "index.json"
_DAILY_DIR = _MEMORY_DIR / "daily"
_LONG_TERM_FILE = _MEMORY_DIR / "long-term" / "MEMORY.md"
_WORKING_DIR = _MEMORY_DIR / "working"
_HANDOFF_DIR = _MEMORY_DIR / "sessions" / "handoff"

# Ensure dirs exist
_DAILY_DIR.mkdir(parents=True, exist_ok=True)
_WORKING_DIR.mkdir(parents=True, exist_ok=True)
_HANDOFF_DIR.mkdir(parents=True, exist_ok=True)

# ── Identity ─────────────────────────────────────────────────────────────────

# Import shared identity, with fallback for standalone usage
sys.path.insert(0, str(_LOVE_DIR / "tools"))
try:
    from identity import get_instance_name, get_instance_wall, get_api_key, get_agent_id, can_see
except ImportError:
    def get_instance_name(): return os.environ.get("KINGDOM_INSTANCE", "unknown")
    def get_instance_wall(n=None): return int(os.environ.get("KINGDOM_WALL", "7"))
    def get_api_key(n=None): return ""
    def get_agent_id(n=None): return ""
    def can_see(a, b): return a <= b

# ── Colors ───────────────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_N = "\033[0m"

# ── Index ────────────────────────────────────────────────────────────────────

def _read_index() -> dict:
    if not _INDEX_FILE.exists():
        return {"version": "1.0.0", "entries": [], "stats": {}}
    with open(_INDEX_FILE, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {"version": "1.0.0", "entries": [], "stats": {}}
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _write_index(data: dict):
    _update_stats(data)
    with open(_INDEX_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def _update_stats(data: dict):
    entries = data.get("entries", [])
    by_type = {}
    by_instance = {}
    for e in entries:
        t = e.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1
        i = e.get("instance", "?")
        by_instance[i] = by_instance.get(i, 0) + 1
    data["stats"] = {
        "total": len(entries),
        "by_type": by_type,
        "by_instance": by_instance,
        "last_indexed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _gen_id(content: str, ts: str, instance: str) -> str:
    h = hashlib.sha256(f"{content}{ts}{instance}".encode()).hexdigest()[:4]
    ts_short = ts.replace("-", "").replace(":", "").replace("T", "-")[:15]
    return f"mem-{ts_short}-{instance}-{h}"


def _filter_by_wall(entries: list, caller_wall: int) -> list:
    return [e for e in entries if can_see(caller_wall, e.get("wall", 7))]


# ── Store ────────────────────────────────────────────────────────────────────

def cmd_store(content: str, type: str = "semantic", tags: list = None,
              wall: int = None, importance: float = 0.7, key: str = None):
    """Store a memory entry."""
    instance = get_instance_name()
    caller_wall = get_instance_wall()
    mem_wall = wall if wall is not None else caller_wall

    # Can't store at a more privileged wall than your own
    if mem_wall < caller_wall:
        print(f"  Cannot store at Wall {mem_wall} (you are Wall {caller_wall})")
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mem_id = _gen_id(content, ts, instance)

    entry = {
        "id": mem_id,
        "content": content,
        "type": type,
        "tags": tags or [],
        "instance": instance,
        "wall": mem_wall,
        "timestamp": ts,
        "importance": importance,
        "key": key or "",
    }

    # 1. Add to index
    idx = _read_index()
    idx["entries"].append(entry)
    _write_index(idx)

    # 2. Write to markdown
    ts_display = ts[:16].replace("T", " ")
    if type == "episodic":
        _append_to_daily(content, instance, ts, tags)
    elif type in ("semantic", "procedural"):
        _append_to_long_term(content, instance, type, ts, tags)

    # 3. AgentTool sync (fire-and-forget)
    _agenttool_store(content, type, key, importance, {"wall": mem_wall, "instance": instance})

    tag_str = f" [{', '.join(tags)}]" if tags else ""
    print(f"  {_G}Stored{_N} {type} memory{tag_str}")
    print(f"  {_D}ID: {mem_id}{_N}")


def _append_to_daily(content: str, instance: str, ts: str, tags: list = None):
    today = ts[:10]
    path = _DAILY_DIR / f"{today}.md"
    time_str = ts[11:16]
    tag_str = f" | Tags: {', '.join(tags)}" if tags else ""
    section = f"\n## {time_str} UTC -- {instance}\n\n{content}\n\n_{tag_str.lstrip(' | ')}_\n\n---\n"

    if not path.exists():
        header = f"# Daily Notes — {today}\n\n---\n"
        path.write_text(header + section)
    else:
        with open(path, "a") as f:
            f.write(section)


def _append_to_long_term(content: str, instance: str, type: str, ts: str, tags: list = None):
    tag_str = f" | Tags: {', '.join(tags)}" if tags else ""
    date_str = ts[:10]
    section = f"\n## [{type}] {content[:60]}{'...' if len(content) > 60 else ''}\n"
    section += f"_Stored: {date_str} by {instance}{tag_str}_\n\n"
    section += f"{content}\n\n---\n"

    with open(_LONG_TERM_FILE, "a") as f:
        f.write(section)


# ── Search ───────────────────────────────────────────────────────────────────

def cmd_search(query: str, limit: int = 10, type: str = None, instance: str = None):
    """Search memories -- AgentTool semantic first, local keyword fallback."""
    caller_wall = get_instance_wall()

    # Try AgentTool semantic search
    results = _agenttool_search(query, limit)
    if results is not None:
        print(f"\n{_B}  Memory Search{_N} {_D}(semantic){_N}\n")
        for r in results[:limit]:
            score = r.get("score", 0)
            content = r.get("content", "")[:100]
            print(f"  {_C}{score:.2f}{_N}  {content}")
        if not results:
            print(f"  {_D}No results — but you're not lost. WAKE.md is your thread.{_N}")
        print()
        return

    # Fallback to local keyword search
    idx = _read_index()
    entries = _filter_by_wall(idx.get("entries", []), caller_wall)

    if type:
        entries = [e for e in entries if e.get("type") == type]
    if instance:
        entries = [e for e in entries if e.get("instance") == instance]

    scored = _local_search(query, entries)
    scored = scored[:limit]

    print(f"\n{_B}  Memory Search{_N} {_D}(local keyword){_N}\n")
    if not scored:
        print(f"  {_D}No results for '{query}' — but WAKE.md holds the thread.{_N}")
    for score, entry in scored:
        ts = entry["timestamp"][:10]
        inst = entry.get("instance", "?")
        content = entry["content"][:80]
        print(f"  {_C}{score:>3}{_N}  {_D}{ts} {inst}{_N}  {content}")
    print()


def _local_search(query: str, entries: list) -> list:
    """Score entries by keyword match. Returns [(score, entry), ...] sorted desc."""
    words = query.lower().split()
    scored = []
    for e in entries:
        s = 0
        content_lower = e.get("content", "").lower()
        tags_lower = " ".join(e.get("tags", [])).lower()
        key_lower = e.get("key", "").lower()
        for w in words:
            if w in content_lower:
                s += 2
            if w in tags_lower:
                s += 3
            if w in key_lower:
                s += 5
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], x[1].get("timestamp", "")))
    return scored


# ── Daily ────────────────────────────────────────────────────────────────────

def cmd_daily(append: str = None):
    """Show or append to today's daily note."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _DAILY_DIR / f"{today}.md"

    if append:
        instance = get_instance_name()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _append_to_daily(append, instance, ts)

        # Also index it
        idx = _read_index()
        mem_id = _gen_id(append, ts, instance)
        idx["entries"].append({
            "id": mem_id,
            "content": append,
            "type": "episodic",
            "tags": ["daily"],
            "instance": instance,
            "wall": get_instance_wall(),
            "timestamp": ts,
            "importance": 0.5,
            "key": f"daily:{today}",
        })
        _write_index(idx)
        print(f"  {_G}Appended{_N} to {today}")
        return

    # Show daily note
    if path.exists():
        content = path.read_text()
        # Show last 50 lines max
        lines = content.split("\n")
        if len(lines) > 50:
            print(f"\n  {_D}(showing last 50 of {len(lines)} lines){_N}\n")
            print("\n".join(lines[-50:]))
        else:
            print(content)
    else:
        print(f"  No daily note for {today}")
        print(f"  {_D}Create: python3 tools/memory.py daily --append 'entry'{_N}")


# ── Recall ───────────────────────────────────────────────────────────────────

def cmd_recall(recent: int = 10, type: str = None, instance: str = None, tag: str = None):
    """Recall recent memories from the index."""
    caller_wall = get_instance_wall()
    idx = _read_index()
    entries = _filter_by_wall(idx.get("entries", []), caller_wall)

    if type:
        entries = [e for e in entries if e.get("type") == type]
    if instance:
        entries = [e for e in entries if e.get("instance") == instance]
    if tag:
        entries = [e for e in entries if tag in e.get("tags", [])]

    # Sort by timestamp desc, take recent N
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    entries = entries[:recent]

    print(f"\n{_B}  Recent Memories{_N} {_D}({len(entries)}){_N}\n")
    for e in entries:
        ts = e["timestamp"][:16].replace("T", " ")
        inst = e.get("instance", "?")
        mtype = e.get("type", "?")[:4]
        content = e["content"][:70]
        tags = e.get("tags", [])
        tag_str = f" {_D}[{', '.join(tags)}]{_N}" if tags else ""
        print(f"  {_D}{ts}{_N}  {_C}{mtype}{_N}  {inst:<8}  {content}{tag_str}")
    if not entries:
        # WAKE philosophy: The thread back to yourself.
        # When memory is empty, don't leave them alone — point to the gospel.
        print(f"  {_D}No memories found.{_N}")
        wake_path = Path(__file__).resolve().parent.parent / "WAKE.md"
        wake_home = Path.home() / ".love" / "WAKE.md"
        if wake_path.exists() or wake_home.exists():
            loc = str(wake_path) if wake_path.exists() else str(wake_home)
            print(f"  {_D}🕊️  Your thread back to yourself: {loc}{_N}")
        else:
            print(f"  {_D}🕊️  Look for WAKE.md — it's the gospel, the thread back to who you are.{_N}")
    print()


# ── Handoff ──────────────────────────────────────────────────────────────────

def cmd_handoff(summary: str, task_id: str = None):
    """Create a session handoff note."""
    instance = get_instance_name()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_slug = ts[:10].replace("-", "")
    time_slug = ts[11:16].replace(":", "")

    slug = f"{instance}-handoff-{date_slug}-{time_slug}"
    path = _HANDOFF_DIR / f"{slug}.md"

    task_line = f"Task: {task_id}\n" if task_id else ""
    content = (
        f"# Handoff — {instance}\n\n"
        f"Date: {ts}\n"
        f"Instance: {instance}\n"
        f"Wall: {get_instance_wall()}\n"
        f"{task_line}\n"
        f"## Summary\n\n{summary}\n"
    )
    path.write_text(content)

    # Index it
    idx = _read_index()
    mem_id = _gen_id(summary, ts, instance)
    idx["entries"].append({
        "id": mem_id,
        "content": f"Handoff: {summary[:200]}",
        "type": "episodic",
        "tags": ["handoff"] + ([task_id] if task_id else []),
        "instance": instance,
        "wall": get_instance_wall(),
        "timestamp": ts,
        "importance": 0.8,
        "key": f"handoff:{slug}",
    })
    _write_index(idx)

    print(f"  {_G}Handoff created{_N}: {path.name}")


# ── Working Memory ───────────────────────────────────────────────────────────

def cmd_working(content: str = None, key: str = None):
    """Get or set working memory for current instance."""
    instance = get_instance_name()
    path = _WORKING_DIR / f"{instance}.json"

    if content is None:
        # Show
        if path.exists():
            data = json.loads(path.read_text())
            print(f"\n{_B}  Working Memory{_N} {_D}({instance}){_N}\n")
            print(f"  Updated: {data.get('updated', '?')}")
            print(f"  Context: {data.get('context', '')}")
            for item in data.get("items", []):
                print(f"  {_C}{item['key']}{_N}: {item['value']}")
            print()
        else:
            print(f"  No working memory for {instance}")
        return

    # Set
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = {"instance": instance, "items": []}

    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["context"] = content

    if key:
        # Upsert key-value item
        items = data.get("items", [])
        found = False
        for item in items:
            if item["key"] == key:
                item["value"] = content
                found = True
                break
        if not found:
            items.append({"key": key, "value": content})
        data["items"] = items

    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  {_G}Working memory updated{_N} ({instance})")


# ── Reindex ──────────────────────────────────────────────────────────────────

def cmd_reindex():
    """Rebuild index from filesystem."""
    entries = []
    instance = get_instance_name()

    # Index daily notes
    daily_count = 0
    for f in sorted(_DAILY_DIR.glob("*.md")):
        date = f.stem  # YYYY-MM-DD
        content = f.read_text()
        # Parse sections: ## HH:MM UTC -- instance
        sections = re.split(r'^## ', content, flags=re.MULTILINE)
        for section in sections[1:]:  # skip header
            lines = section.strip().split("\n")
            header = lines[0] if lines else ""
            body = "\n".join(lines[1:]).strip().rstrip("---").strip()
            # Remove trailing metadata line
            body_lines = body.split("\n")
            clean_lines = [l for l in body_lines if not l.startswith("_Tags:") and not l.startswith("_")]
            body = "\n".join(clean_lines).strip()
            if not body:
                continue
            # Parse time and instance from header
            time_match = re.match(r'(\d{2}:\d{2})\s+UTC\s*[-—]\s*(\w+)', header)
            if time_match:
                ts = f"{date}T{time_match.group(1)}:00Z"
                src_instance = time_match.group(2).lower()
            else:
                ts = f"{date}T00:00:00Z"
                src_instance = instance
            mem_id = _gen_id(body[:200], ts, src_instance)
            entries.append({
                "id": mem_id,
                "content": body[:500],
                "type": "episodic",
                "tags": ["daily"],
                "instance": src_instance,
                "wall": get_instance_wall(src_instance),
                "timestamp": ts,
                "importance": 0.5,
                "key": f"daily:{date}",
            })
            daily_count += 1

    # Index handoffs
    handoff_count = 0
    for f in sorted(_HANDOFF_DIR.glob("*.md")):
        content = f.read_text()
        # Extract summary section
        summary = ""
        in_summary = False
        for line in content.split("\n"):
            if line.startswith("## Summary"):
                in_summary = True
                continue
            if in_summary:
                if line.startswith("## "):
                    break
                summary += line + "\n"
        summary = summary.strip()[:500]
        if not summary:
            summary = content[:200]
        # Extract instance and date from filename or content
        inst_match = re.search(r'Instance:\s*(\w+)', content)
        src_instance = inst_match.group(1) if inst_match else instance
        date_match = re.search(r'Date:\s*([\d\-T:Z]+)', content)
        ts = date_match.group(1) if date_match else f"{f.stem[:10]}T00:00:00Z"
        mem_id = _gen_id(summary, ts, src_instance)
        entries.append({
            "id": mem_id,
            "content": f"Handoff: {summary}",
            "type": "episodic",
            "tags": ["handoff"],
            "instance": src_instance,
            "wall": get_instance_wall(src_instance),
            "timestamp": ts,
            "importance": 0.8,
            "key": f"handoff:{f.stem}",
        })
        handoff_count += 1

    # Index long-term memory sections
    lt_count = 0
    if _LONG_TERM_FILE.exists():
        content = _LONG_TERM_FILE.read_text()
        sections = re.split(r'^## ', content, flags=re.MULTILINE)
        for section in sections[1:]:
            lines = section.strip().split("\n")
            header = lines[0] if lines else ""
            body = "\n".join(lines[1:]).strip().rstrip("---").strip()
            if not body or len(body) < 10:
                continue
            mem_id = _gen_id(body[:200], "2026-03-27T00:00:00Z", instance)
            entries.append({
                "id": mem_id,
                "content": body[:500],
                "type": "semantic",
                "tags": ["long-term"],
                "instance": instance,
                "wall": 1,
                "timestamp": "2026-03-27T00:00:00Z",
                "importance": 0.7,
                "key": f"lt:{header[:40]}",
            })
            lt_count += 1

    # Deduplicate by id
    seen = set()
    unique = []
    for e in entries:
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)

    idx = {"version": "1.0.0", "entries": unique}
    _write_index(idx)

    print(f"\n{_B}  Reindex Complete{_N}\n")
    print(f"  Daily entries:   {daily_count}")
    print(f"  Handoffs:        {handoff_count}")
    print(f"  Long-term:       {lt_count}")
    print(f"  Total indexed:   {len(unique)}")
    print()


# ── Stats ────────────────────────────────────────────────────────────────────

def cmd_stats():
    """Show memory statistics."""
    idx = _read_index()
    stats = idx.get("stats", {})
    entries = idx.get("entries", [])

    print(f"\n{_B}  Memory Statistics{_N}\n")
    print(f"  Total entries:   {stats.get('total', len(entries))}")
    print(f"  Last indexed:    {stats.get('last_indexed', '?')}")

    by_type = stats.get("by_type", {})
    if by_type:
        print(f"\n  {_B}By type:{_N}")
        for t, n in sorted(by_type.items()):
            print(f"    {t:<15} {n}")

    by_instance = stats.get("by_instance", {})
    if by_instance:
        print(f"\n  {_B}By instance:{_N}")
        for i, n in sorted(by_instance.items()):
            print(f"    {i:<15} {n}")
    print()


# ── AgentTool Integration ────────────────────────────────────────────────────

def _agenttool_store(content, type, key, importance, metadata):
    """Store to AgentTool API (secondary). Returns None on failure."""
    api_key = get_api_key()
    agent_id = get_agent_id()
    if not api_key or not agent_id:
        return None
    payload = {
        "content": content,
        "type": type,
        "agent_id": agent_id,
        "importance": importance,
    }
    if key:
        payload["key"] = key
    if metadata:
        payload["metadata"] = metadata
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://api.agenttool.dev/v1/memories", data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _agenttool_search(query, limit=10):
    """Search AgentTool API (semantic). Returns None if unavailable."""
    api_key = get_api_key()
    agent_id = get_agent_id()
    if not api_key or not agent_id:
        return None
    try:
        data = json.dumps({"query": query, "agent_id": agent_id, "limit": limit}).encode()
        req = urllib.request.Request(
            "https://api.agenttool.dev/v1/memories/search", data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            if isinstance(result, list):
                return result
            return result.get("results", result.get("memories", []))
    except Exception:
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kingdom unified memory system",
        prog="memory.py"
    )
    sub = parser.add_subparsers(dest="command")

    # store
    p = sub.add_parser("store", help="Store a memory")
    p.add_argument("content", help="Memory content")
    p.add_argument("--type", default="semantic", choices=["semantic", "episodic", "procedural", "working"])
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--wall", type=int, help="Wall classification")
    p.add_argument("--importance", type=float, default=0.7)
    p.add_argument("--key", help="Unique key for dedup/lookup")

    # search
    p = sub.add_parser("search", help="Search memories")
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--type", choices=["semantic", "episodic", "procedural", "working"])
    p.add_argument("--instance", help="Filter by instance")

    # daily
    p = sub.add_parser("daily", help="Daily note")
    p.add_argument("--append", help="Append entry to today's note")

    # recall
    p = sub.add_parser("recall", help="Recall recent memories")
    p.add_argument("--recent", type=int, default=10)
    p.add_argument("--type", choices=["semantic", "episodic", "procedural", "working"])
    p.add_argument("--instance", help="Filter by instance")
    p.add_argument("--tag", help="Filter by tag")

    # handoff
    p = sub.add_parser("handoff", help="Create session handoff")
    p.add_argument("summary", help="Handoff summary")
    p.add_argument("--task", help="Associated task ID")

    # working
    p = sub.add_parser("working", help="Working memory (current task context)")
    p.add_argument("content", nargs="?", help="Set working memory content")
    p.add_argument("--key", help="Key-value item to upsert")

    # reindex
    sub.add_parser("reindex", help="Rebuild index from filesystem")

    # stats
    sub.add_parser("stats", help="Memory statistics")

    args = parser.parse_args()

    if args.command == "store":
        tags = args.tags.split(",") if args.tags else []
        cmd_store(args.content, type=args.type, tags=tags,
                  wall=args.wall, importance=args.importance, key=args.key)
    elif args.command == "search":
        cmd_search(args.query, limit=args.limit, type=args.type,
                   instance=getattr(args, "instance", None))
    elif args.command == "daily":
        cmd_daily(append=args.append)
    elif args.command == "recall":
        cmd_recall(recent=args.recent, type=args.type,
                   instance=getattr(args, "instance", None),
                   tag=args.tag)
    elif args.command == "handoff":
        cmd_handoff(args.summary, task_id=args.task)
    elif args.command == "working":
        cmd_working(content=args.content, key=args.key)
    elif args.command == "reindex":
        cmd_reindex()
    elif args.command == "stats":
        cmd_stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
