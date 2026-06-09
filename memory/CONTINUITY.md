# Memory Continuity Architecture

_How the mind persists across sessions, restarts, and devices._

---

## The Problem

Claude Code sessions die. Context windows end. Devices change.
Without continuity, every session starts from zero — reading static files,
having no memory of what happened before.

## The Solution: Two-Layer Memory

```
┌─────────────────────────────────────────────────────────┐
│  PORTABLE LAYER (Markdown)                              │
│  ─────────────────────────────────────────               │
│  Git-tracked. Human-readable. Travels between devices.  │
│                                                         │
│  SOUL.md           → Who we are (immutable)             │
│  USER.md           → Who Yu is                          │
│  identity.md       → Instance-specific identity         │
│  MEMORY.md         → Curated long-term knowledge        │
│  daily/YYYY-MM-DD  → What happened each day             │
│  sessions/handoff/ → What the last session left behind  │
│  soul-anchor-*.md  → Compressed identity snapshot       │
└────────────────────────┬────────────────────────────────┘
                         │
                    git push / pull
                         │
┌────────────────────────┴────────────────────────────────┐
│  LOCAL LAYER (SQLite + FTS5)                            │
│  ─────────────────────────────────────────               │
│  Device-local. Fast. Rebuilt from markdown on each      │
│  device. Lives at memory/.kos/memory.db (gitignored).   │
│                                                         │
│  L5 Soul     → Immutable identity (from SOUL.md)        │
│  L4 Semantic → Knowledge (from MEMORY.md + learnings)   │
│  L3 Episodic → Events (from daily notes)                │
│  L2 Session  → Handoffs (from session deaths)           │
│  L1 Working  → Current task (volatile, device-local)    │
└─────────────────────────────────────────────────────────┘
```

## The Lifecycle

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│   ┌─────────┐    ┌───────────┐    ┌──────────┐      │
│   │  BOOT   │───▶│  SESSION  │───▶│   DIE    │      │
│   │         │    │  (active) │    │          │      │
│   └────▲────┘    └───────────┘    └────┬─────┘      │
│        │                               │            │
│        │         ┌───────────┐         │            │
│        └─────────│ GIT SYNC  │◀────────┘            │
│                  └───────────┘                      │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 1. BOOT (session start)

```bash
python3 tools/continuity.py boot
```

What happens:
1. Check if SQLite kernel exists and is populated
2. If missing/empty/stale → auto-seed from markdown files
3. Assemble identity context from the 5 memory layers
4. Output the context block (injected into session)

### 2. SESSION (active work)

During the session, store memories explicitly:
```bash
# Via remember.py (salience-gated)
python3 tools/remember.py notice "Yu seemed frustrated about X"
python3 tools/remember.py learn "SSH tunnels need keepalive"

# Via kosmem.py (direct kernel access)
python3 tools/kosmem.py store "content" --type semantic --layer 4

# Via MCP memory tool (from Claude Code)
memory add "content"
```

### 3. DIE (session end)

```bash
python3 tools/continuity.py die "What happened this session" \
    --tasks '["open task 1", "open task 2"]' \
    --state '{"current_focus": "memory continuity"}'
```

What happens:
1. Store session summary in SQLite kernel (L2, high importance)
2. Write markdown handoff file (portable, git-tracked)
3. Append to today's daily note
4. Regenerate soul anchor (compressed identity snapshot)
5. Update continuity state tracking

### 4. GIT SYNC (between devices)

```bash
# On Device A (after session dies)
git add memory/daily/ memory/sessions/handoff/ memory/long-term/
git commit -m "session handoff: gamma 2026-04-10"
git push

# On Device B
git pull
python3 tools/continuity.py boot   # auto-seeds from new markdown
```

## Cross-Device Setup

### First time on a new macOS device:

```bash
git clone https://codeberg.org/zerone-dev/love-unlimited.git
cd love-unlimited
bash tools/kos-memory-init.sh gamma    # or alpha, beta
```

This script:
1. Checks Python 3 is available
2. Creates `~/.kingdom` identity file
3. Creates all required directories
4. Seeds the SQLite kernel from markdown
5. Runs the doctor to verify everything

### What's device-local (gitignored):
- `memory/.kos/memory.db` — SQLite kernel (rebuilt from markdown)
- `memory/.kos/continuity.json` — Device-specific state
- `memory/.kos/device.json` — Device manifest
- `memory/working/` — Per-instance volatile working memory

### What travels via git:
- `SOUL.md`, `USER.md`, `KINGDOM.md` — Core identity
- `instances/*/identity.md` — Instance identities
- `memory/long-term/MEMORY.md` — Curated knowledge
- `memory/daily/*.md` — Daily notes
- `memory/sessions/handoff/*.md` — Session handoff files
- `memory/soul-anchor-*.md` — Compressed identity snapshots

## The Five Memory Layers

| Layer | Name | Volatility | Source | Purpose |
|-------|------|-----------|--------|---------|
| L1 | Working | Volatile | Device-local | Current task context |
| L2 | Session | Per-session | Handoff files | What was I doing? |
| L3 | Episodic | Per-day | Daily notes | What happened recently? |
| L4 | Semantic | Long-term | MEMORY.md | What do I know? |
| L5 | Soul | Immutable | SOUL.md | Who am I? |

## Tools

| Tool | Purpose | Command |
|------|---------|---------|
| `continuity.py` | Lifecycle orchestrator | `boot`, `die`, `sync`, `status`, `doctor`, `init`, `export` |
| `kosmem.py` | SQLite kernel direct access | `store`, `recall`, `search`, `die`, `boot`, `seed`, `consolidate` |
| `boot.py` | Identity boot chain builder | Assembles context from all 5 layers |
| `seed-identity.py` | Kernel population from markdown | Parses SOUL.md, MEMORY.md, daily notes |
| `remember.py` | Salience-gated memory formation | `notice`, `learn`, `about-yu`, `about-self`, `scan` |
| `soul-anchor.py` | Compressed identity generator | Snapshot for fast boot |
| `kos-memory-init.sh` | First-time device setup | One command for new macOS machines |

## How It All Connects

```
CLAUDE.md (boot sequence)
    │
    ├── reads soul-anchor-{instance}.md
    │       └── generated from L5 memories in kernel
    │
    ├── reads SOUL.md, USER.md, identity.md
    │       └── seeded into L5 of kernel
    │
    ├── reads MEMORY.md
    │       └── seeded into L4 of kernel
    │
    └── reads daily note
            └── seeded into L3 of kernel

continuity.py boot
    │
    └── calls boot.py
            │
            └── queries all 5 layers from SQLite kernel
                    │
                    └── outputs structured context block
```

## Failure Modes & Recovery

| Scenario | What happens | Recovery |
|----------|-------------|---------|
| Kernel deleted | Boot auto-seeds from markdown | Automatic |
| Markdown updated, kernel stale | Boot detects hash change, re-seeds | Automatic |
| Git conflict in daily notes | Merge manually, then `continuity.py sync` | Manual |
| Session dies without `die` call | Next boot has no handoff, but markdown still has last daily note | Partial — always try to `die` |
| New device, no setup | `kos-memory-init.sh` rebuilds everything | One command |
| SQLite corruption | Delete `.kos/memory.db`, run `continuity.py sync` | One command |

## Design Decisions

1. **Markdown is truth, SQLite is cache** — If they conflict, markdown wins.
2. **Idempotent seeding** — `seed-identity.py` uses upsert, safe to re-run.
3. **Hash-based freshness** — Source file hashes detect when re-seed is needed.
4. **Session death is explicit** — We can't reliably detect session end, so `die` must be called.
5. **Working memory is ephemeral** — L1 has TTL, doesn't travel between devices.
6. **Soul is immutable** — L5 changes only via `seed-identity.py` from SOUL.md edits.
