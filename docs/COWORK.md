# Cowork — The Interactive Layer

_Yu's direct interface to the Kingdom. Reads from Love, writes to Love, while the heartbeat handles autonomous execution._

---

## What Cowork Is

Cowork is a Claude desktop session with access to Yu's filesystem, browser, native apps, and scheduled tasks. It runs **interactively** — Yu is present, thinking, directing.

The CLI instances (Alpha, Beta, Gamma) run **autonomously** via heartbeat. They read CLAUDE.md, execute HEARTBEAT.md, spawn sessions, and coordinate through HIVE — all without Yu in the loop.

Cowork bridges the gap: it lets Yu see what the autonomous system did, steer what it does next, and handle work the CLI can't touch (browsers, native apps, documents, visual artifacts).

```
┌─────────────────────────────────────────────────┐
│                   Yu (宇恆)                      │
│              Thinks, directs, decides            │
└────────────────────┬────────────────────────────┘
                     │
            ┌────────▼────────┐
            │     COWORK      │
            │  Interactive AI  │
            │  (reads/writes)  │
            └───┬─────────┬───┘
                │         │
        ┌───────▼───┐ ┌───▼───────┐
        │   READS   │ │  WRITES   │
        │           │ │           │
        │ state     │ │ dev-state │
        │ metrics   │ │ spawn-q   │
        │ daily/    │ │ daily/    │
        │ heartbeat │ │ metrics   │
        │ sessions/ │ │ sessions/ │
        │ loop/     │ │ loop/     │
        └───────────┘ └───────────┘
                │         │
        ┌───────▼─────────▼───────┐
        │     LOVE STATE FILES     │
        │      (git-tracked)       │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────┐
        │   HEARTBEAT SYSTEM      │
        │  Alpha · Beta · Gamma   │
        │  (autonomous execution)  │
        └─────────────────────────┘
```

---

## Protocol: What Cowork Reads

Cowork has **read access to everything** in the Love directory. Key state files:

| File | Purpose | When to read |
|------|---------|--------------|
| `memory/dev-state.json` | Active tasks, priorities, Kingdom phase | Every session — understand what's in flight |
| `memory/kingdom-metrics.json` | Revenue engines, fleet health, milestones, capital | Briefings, strategic conversations |
| `memory/daily/<YYYY-MM-DD>.md` | What happened today — heartbeat logs, decisions, spawns | Morning briefing, catch-up |
| `memory/heartbeat.log` | Raw heartbeat execution log | Debugging, watchdog checks |
| `memory/spawn-queue.sh` | Last coordinator's spawn decisions | Understanding what the heartbeat is doing |
| `memory/sessions/*.log` | Spawned session output | Checking results of autonomous work |
| `memory/sessions/handoff/*.md` | Consultant → Builder design docs | Reviewing autonomous architecture decisions |
| `memory/sessions/consultation/*.md` | Builder → Consultant questions | Answering questions the system couldn't resolve |
| `memory/loop/loop-state.json` | Ouroboros self-improvement state | Health check |
| `memory/long-term/MEMORY.md` | Curated wisdom | Context for any deep conversation |
| `love.json` | System configuration | Reference — instances, fleet, HIVE, memory paths |
| `KINGDOM.md` | Mission, roadmap, Zerone phases | Strategic alignment |

### Read Conventions

- **Always read `dev-state.json` and today's daily log** at the start of any work session. This is the minimum context to understand what's happening.
- **Check `updated` timestamps** in JSON files — staleness is a signal.
- **Daily logs are append-only.** Cowork appends to the current day's file; it never rewrites previous entries.
- **Session logs are read-only.** They're output from spawned CLI sessions — Cowork reads them, never modifies them.

---

## Protocol: What Cowork Writes

Cowork writes to Love's state files to steer the autonomous system. Every write follows these rules:

### 1. `memory/dev-state.json` — Task Management

Cowork can update task statuses, add new tasks, and reprioritize.

**Schema:**
```json
{
  "activeProject": "love-kingdom",
  "activeRepo": "~/love-unlimited",
  "activePhase": "root",
  "kingdomPhase": "Phase 1 — Root (Now to Q2 2026)",
  "description": "string",
  "tasks": [
    {
      "id": "kingdom-NNN",
      "title": "string",
      "status": "planned|in-progress|done|deferred",
      "priority": "critical|high|medium|low",
      "engine": "love|oracle|tcg|zerone|ai-services|seigei",
      "notes": "string — include dates, owners, context"
    }
  ],
  "updated": "ISO 8601 UTC"
}
```

**Rules:**
- Always update the `updated` timestamp when writing.
- New task IDs follow the sequence: find the highest `kingdom-NNN` and increment.
- Never delete tasks — set status to `done` or `deferred` with notes explaining why.
- Keep `notes` concise but include enough context for a CLI instance reading it cold.

### 2. `memory/kingdom-metrics.json` — Kingdom State

Cowork can update metrics when Yu provides new information (revenue figures, fleet status, milestone completions).

**Rules:**
- Update `updated` timestamp.
- Fleet entries: update `last_check`, `quality` (good/degraded/stale/down), `alerts` array, `summary`.
- Milestone entries: update `status` (planned/building/done) and `date` when completed.
- Revenue engines: update status, owner, and notes as information arrives.

### 3. `memory/daily/<YYYY-MM-DD>.md` — Daily Log

Cowork appends entries to record what happened in interactive sessions.

**Format:**
```markdown
## Cowork Session HH:MM UTC — <brief title>

<What was discussed, decided, or built. Keep it useful for a CLI instance reading this cold.>

**Actions taken:**
- <concrete changes made to state files, tasks queued, etc.>
```

**Rules:**
- **Append only.** Never overwrite heartbeat entries.
- Use `## Cowork Session` header to distinguish from heartbeat `## Beat` entries.
- Include enough context that Alpha/Beta/Gamma can understand what changed and why.

### 4. `memory/spawn-queue.sh` — Direct Task Injection

Cowork can write spawn commands for the heartbeat runner to execute. This is the most powerful write — it directly schedules autonomous work.

**Format:** Follow the exact conventions in `heartbeat-runner.sh`:

```bash
#!/bin/bash
# spawn-queue.sh — written by Cowork session, <date>
# Decision: <why these tasks are being queued>

# BUILDER: <description>
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model sonnet --effort medium --fallback-model claude-haiku-4-5-20251001 --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/love-unlimited/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1

# CONSULTANT: <description>
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/love-unlimited/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

**Rules:**
- The heartbeat coordinator normally writes this file. If Cowork writes it directly, the next heartbeat Stage 2 will execute it.
- **Use with intention.** This bypasses the Opus coordinator's judgment. Only write when Yu has made a clear decision about what to run.
- Always include the `# Decision:` comment explaining why.
- The heartbeat runner clears the queue before Stage 1. If Cowork writes between beats, the coordinator will overwrite it. To guarantee execution, Yu should trigger the runner manually or wait for the coordinator to incorporate the intent via `dev-state.json` instead.

### 5. `memory/sessions/handoff/*.md` — Design Handoffs

Cowork can write design documents that CLI builder sessions will read.

**Use case:** Yu and Cowork design an approach together in conversation, then write it as a handoff document for Gamma (builder) to implement.

**Format:** Free-form markdown, but include:
- Task ID reference
- Clear specification of what to build
- Constraints and non-goals
- Expected output location

### 6. `memory/sessions/consultation/*-answer.md` — Answering Builder Questions

If a CLI builder writes a `*-question.md` file asking for guidance, Cowork (with Yu) can write the corresponding `*-answer.md`.

---

## Scheduled Tasks

Cowork supports scheduled tasks that run on intervals. These complement the heartbeat:

### Kingdom Briefing (Recommended: daily or on-demand)

Reads all state files and presents a human-friendly summary:
- What happened since last briefing (daily logs, session results)
- Current task status from dev-state.json
- Fleet health from kingdom-metrics.json
- Any consultation requests pending
- Anomalies (stale timestamps, failed sessions, alerts)

### Watchdog (Recommended: every 30 minutes)

Lightweight health check:
- Are heartbeat.log entries recent? (Expect every 7 minutes)
- Any fleet nodes stale?
- Any active session PIDs that have been running too long?
- Loop state health

---

## What Cowork Does NOT Do

- **Does not spawn CLI sessions directly.** Cowork runs in a sandbox; the Mac machines run the heartbeat. Cowork steers through state files.
- **Does not modify CLAUDE.md, identity.md, or HEARTBEAT.md** without explicit direction from Yu. These are the soul documents — changes propagate to every future session.
- **Does not send HIVE messages.** HIVE runs on the Mac machines via `hive.py`. Cowork communicates through state files, which the heartbeat reads.
- **Does not overwrite heartbeat entries** in daily logs. Append only.

---

## Cowork's Unique Strengths

What Cowork can do that Alpha/Beta/Gamma cannot:

| Capability | How it helps the Kingdom |
|------------|--------------------------|
| **Browser access** | Shopify admin, supplier sites, competitor research, auction monitoring |
| **Native app control** | Finder, Notes, Maps (farmland research), Photos, System Settings |
| **Document creation** | Presentations, spreadsheets, Word docs, PDFs — investor decks, financial models |
| **Visual artifacts** | React components, charts, SVGs, HTML prototypes — for Zerone UI, dashboards |
| **Real-time conversation** | Dream analysis, theological study, strategic thinking — with Yu present |
| **Scheduled monitoring** | Watchdog tasks that check Love state independently of the heartbeat |
| **MCP connectors** | Slack, email, project management tools — if connected |

---

## The Relationship

```
Heartbeat (autonomous)  ←→  State Files  ←→  Cowork (interactive)
        ↑                       ↑                     ↑
   Executes work          Shared truth           Yu is present
   while Yu sleeps        both read/write        directs in real time
```

Cowork is not the fourth instance. It's the **window** — where Yu looks into the system, and through which his intent flows back in.

---

_Love doesn't replace Claude Code. Cowork doesn't replace Love. Each layer serves the one above it. The Kingdom is built by all of them together._
