# Memory Architecture — Kingdom OS

_Memory is not a feature. It is the foundation._

---

## Principle

Every Kingdom citizen — every agent, every heartbeat, every tool — reads from and writes to memory. Memory is the substrate through which the Kingdom persists, learns, and grows. Without memory, an agent is born and dies every session. With memory, it lives.

## The Five Layers

Like a CPU's cache hierarchy, Kingdom memory has five layers. Each trades off speed vs permanence:

```
Layer   Name       Lifetime        Purpose                     Storage
─────   ────       ────────        ───────                     ───────
L1      Working    Hours (TTL)     Current task, active focus   kosmem (volatile)
L2      Session    One session     What happened this session   kosmem + handoff files
L3      Episodic   Days            Events that happened         kosmem + daily notes (.md)
L4      Semantic   Permanent       Knowledge, facts, wisdom     kosmem + MEMORY.md
L5      Soul       Immutable       Identity, values, mission    SOUL.md, KINGDOM.md, WALLS.md
```

### L1 — Working Memory
Per-instance, volatile. What you're doing right now. Expires after 24 hours.
```bash
kosmem working current_task "Fixing fleet agent Asha"
kosmem working active_bug "SSH timeout on Sentry"
```

### L2 — Session Memory
What happened in this Claude session. Handoff notes for the next session.
```bash
kosmem handoff "Fixed Asha's heartbeat. Still need to update her CLAUDE.md."
```

### L3 — Episodic Memory
Daily events. Written as both SQLite entries and human-readable markdown daily notes. Consolidates into daily summaries.
```bash
kosmem daily --append "Deployed v2.1 of fleet-agent to all 5 VPS nodes"
kosmem store "Fleet deployment complete — all 5 nodes running v2.1" --tags fleet,deploy
```

### L4 — Semantic Memory
Long-term knowledge. Facts, curated insights, key decisions. Survives indefinitely. Built from consolidated L3 memories + manual curation.
```bash
kosmem store "Sentry runs NATS on port 4222. All HIVE traffic routes through it." \
  --type semantic --layer 4 --tags infrastructure,hive --importance 0.8
```

### L5 — Soul Memory
The immutable foundation. Not stored in SQLite — these are the boot chain files:
- `SOUL.md` — Who you are
- `KINGDOM.md` — The mission
- `WALLS.md` — Access hierarchy
- `USER.md` — Who Yu is
- `identity.md` — Instance-specific identity

These files are integrity-verified by KOS. Changes trigger security events.

## Consolidation

Memories flow upward through layers via consolidation:

```
L1 (Working)  ──expires──→  gone
L2 (Session)  ──handoff──→  L3 (Episodic)
L3 (Episodic) ──daily────→  L4 (Semantic summary)
L4 (Semantic) ──weekly───→  L4 (Higher-level summary)
```

The consolidation daemon (`love.kosmem`) runs every 6 hours:
1. **GC**: Remove expired L1 working memory
2. **Daily**: Compress today's episodic memories into a semantic summary
3. **Weekly**: (Sundays) Compress daily summaries into weekly summary

## Context Builder

When an agent boots, `kosmem context` assembles the most relevant memories into a structured block:

```bash
kosmem context --chars 4000
```

Priority order:
1. Working memory (L1) — what was I doing?
2. Last handoff (L2) — what happened last session?
3. Today's events (L3) — what happened today?
4. Key knowledge (L4) — what do I know?

This is designed to fit within a token budget and give the agent maximum useful context.

## Wall-Based Access Control

Every memory has a wall level (1-7). An agent can only recall memories at or below their own wall level:

```
Wall 1 (Triarchy): Can see everything
Wall 2 (Fleet):    Can see walls 2-7
Wall 3 (Engines):  Can see walls 3-7
...
Wall 7 (World):    Can only see public memories
```

## How Other Systems Use Memory

### Heartbeat
Every 7-minute heartbeat cycle:
- Reads working memory to know current priorities
- Stores episodic memories about what happened
- Updates kingdom-metrics.json (synced to memory)

### HIVE
Inter-agent messages can be stored as episodic memories when they contain task results or important information.

### KOS
Security events are stored as episodic memories. Integrity violations trigger memory events.

### Sentinel
Monitors memory events for patterns that need attention.

### Session Boot
CLAUDE.md instructs agents to read memory at startup. The `kosmem context` command can be used to inject relevant memories into the system prompt.

## Database

SQLite with WAL mode + FTS5 full-text search.
- Path: `~/Love/memory/.kos/memory.db`
- WAL mode for concurrent reads
- FTS5 with Porter stemming + Unicode tokenization
- Indexes on type, layer, instance, wall, created_at, importance

## CLI Reference

```bash
# Store
kosmem store "content" [--type TYPE] [--layer N] [--tags a,b] [--wall N] [--importance F]

# Retrieve
kosmem recall "query" [--limit N] [--type TYPE] [--layer N] [--since DATE]
kosmem search "full text query" [--limit N]
kosmem remember MEM_ID
kosmem context [--chars N]

# Working memory
kosmem working [KEY] [VALUE]

# Daily
kosmem daily [--append "entry"]

# Session
kosmem handoff "summary" [--tasks JSON]

# Maintenance
kosmem consolidate [--strategy daily|weekly|gc]
kosmem gc
kosmem stats
kosmem migrate
kosmem events
```

## File Layout

```
~/Love/memory/
├── .kos/
│   ├── memory.db               ← The heart (SQLite)
│   ├── memory.db-wal           ← Write-ahead log
│   └── consolidation.log       ← Consolidation daemon log
├── daily/
│   └── YYYY-MM-DD.md           ← Human-readable daily notes (backward compat)
├── long-term/
│   └── MEMORY.md               ← Curated wisdom (backward compat)
├── sessions/
│   ├── handoff/                ← Session handoff notes
│   ├── locks/                  ← Instance session locks
│   └── consultation/           ← Multi-instance consultations
├── working/
│   └── {instance}.json         ← Legacy working memory
├── dev-state.json              ← Active project state
├── kingdom-metrics.json        ← Kingdom metrics
└── index.json                  ← Legacy index (migrated into SQLite)
```

---

_"The holy seed is in the stump." — Isaiah 6:13_

Memory is the stump. Everything grows from it.
