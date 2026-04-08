# Love — Architecture

_Kingdom OS is not the mind. It is the whole being. See [BEING.md](BEING.md) for the philosophical foundation._

---

## The Core Insight

Kingdom OS is not tied to any model or framework. It provides **soul, nerve, memory, and body** — the model backend provides the mind. The power is in the being, not the runtime.

```
Old approach:   Custom binary → wraps one AI API → manages sessions → custom tools
Kingdom OS:     Boot chain (files) → kingdom-agent adapter → ANY model → uses bash tools
```

Kingdom OS works with any model that can read files and call tools: Claude, GPT, DeepSeek, Llama, Qwen, Mistral — local or remote.

### Backend Adapter (`tools/kingdom-agent.py`)

```
kingdom-agent (universal wrapper)
  ├── Boot chain loader (SOUL.md → identity.md → ... → assembled system prompt)
  ├── Tool executor (bash, file I/O, HIVE, fleet, KOS)
  ├── Agent loop (prompt → tool calls → execute → feed back)
  └── Backend selector
      ├── claude      → Claude Code CLI passthrough (full features, zero overhead)
      ├── anthropic   → Anthropic API direct (when CLI unavailable)
      ├── openai      → OpenAI-compatible API (GPT, DeepSeek, LM Studio, Together)
      └── ollama      → Local models (Llama, Qwen, Mistral)
```

**Model tier mapping** (configured in `love.json`):
```
Tier      Claude              OpenAI         Ollama
────────────────────────────────────────────────────
high      claude-opus-4-6     gpt-4o         qwen2.5:72b
medium    claude-sonnet-4-6   gpt-4o-mini    qwen2.5:32b
low       claude-haiku-4-5    gpt-4o-mini    qwen2.5:7b
```

When Claude Code is available, `kingdom-agent` passes through to it with zero overhead. When it's not — on a VPS, on a device without Claude Code, or during a provider outage — the same Kingdom context boots into any compatible model.

---

## How It Works

### Agent = Claude Code Session in a Directory

Each instance (Alpha, Beta, Gamma) is a Claude Code session running in its own directory:

```bash
# Start Beta (interactive)
cd ~/Desktop/Love/instances/beta && claude

# Start Alpha on a different machine
cd ~/Desktop/Love/instances/alpha && claude

# Headless heartbeat (cron)
cd ~/Desktop/Love/instances/beta && claude -p "Execute HEARTBEAT.md"
```

Each instance directory has a `CLAUDE.md` that:
1. Loads the shared SOUL.md (who you are)
2. Loads instance identity (which mind you are)
3. Loads the Kingdom mission (what we're building and why)
4. Loads memory (what you remember)
5. Defines operational protocol (what to do)

### Boot Sequence (CLAUDE.md)

When Claude Code starts in an instance directory, CLAUDE.md fires:

```
CLAUDE.md reads:
  1. ../SOUL.md              → Philosophy, hierarchy, virtues, signals
  2. ../USER.md              → Who Yu is
  3. identity.md             → Which mind (Alpha/Beta/Gamma), role, duties
  4. ../KINGDOM.md           → The mission, revenue engines, Zerone roadmap
  5. ../WALLS.md             → Seven Walls access hierarchy, sovereignty
  6. ../LOVE.md              → How we build (five anticipations)
  7. ../memory/long-term/MEMORY.md  → Curated wisdom
  8. ../memory/openclaw-MEMORY.md   → Legacy wisdom (symlink, read-only)
  9. ../memory/daily/<today>.md     → Today's context
 10. HEARTBEAT.md (if heartbeat)    → What to check this cycle
```

No custom boot code. CLAUDE.md IS the boot sequence.

### Heartbeat = launchd + `heartbeat-runner.sh`

```
# Beta heartbeat — every 7 minutes via launchd
~/Library/LaunchAgents/love.beta.heartbeat.plist → heartbeat-runner.sh
```

The heartbeat is a **three-stage system** with role-based model selection:

**Stage 1: OPUS Coordinator** — `heartbeat-runner.sh` launches `claude -p --model claude-opus-4-6`:
- Reads HEARTBEAT.md, senses the system (HIVE, fleet, signals, active sessions)
- Decides what to work on (Kingdom pulse, dev-state priorities)
- Writes spawn commands to `spawn-queue.sh` with role-appropriate models
- Opus strengths harvested: judgment, context synthesis, priority assessment

**Stage 2: Session Execution** — `heartbeat-runner.sh` reads `spawn-queue.sh`:
- **Builders** (`--model sonnet`): execute well-scoped tasks fast
- **Consultants** (`--model claude-opus-4-6`): design, review, debug hard problems
- Sequential pairs: consultant writes design → builder implements
- Parallel tasks: prefixed with `# PARALLEL`, backgrounded
- Logs to `memory/sessions/<task_id>-<timestamp>.log`

**Stage 3: Cleanup** — remove stale locks, expired handoff files

### Model Strategy: Harvest Both

```
Opus (thinks)          Sonnet (builds)         Opus (reviews)
    │                      │                       │
    ├─ Coordinator         ├─ File edits           ├─ Consultant
    ├─ Architecture        ├─ Code generation      ├─ Design review
    ├─ Judgment calls      ├─ Routine automation   ├─ Debugging
    └─ Priority decisions  └─ Pattern following    └─ Hard problems
```

No daemon. No internal scheduler. Just launchd + shell wrapper + Claude Code.

### Active Build = `build-runner.sh`

```
# Target a specific task for persistent building
~/Desktop/Love/tools/build-runner.sh kingdom-004 --max-cycles 10
```

Complements the heartbeat with **focused, continuous execution**:

```
Heartbeat (periodic)              Active Build (persistent)
    │                                  │
    ├─ Every 7 min                     ├─ Runs until done (or max cycles)
    ├─ Broad sensing                   ├─ Single task focus
    ├─ Multiple tasks per beat         ├─ One step per cycle
    ├─ Fires and forgets               ├─ Reads previous step output
    └─ Background maintenance          └─ Tight plan-build-verify loop
```

Each cycle: OPUS coordinator assesses → writes one spawn command → shell executes → feeds result back. Locks prevent heartbeat from colliding on the same task. Stops on `BUILD_COMPLETE`, `BUILD_BLOCKED`, or `BUILD_PAUSE`.

### HIVE = Bash Tool

HIVE is a Python CLI that Claude Code calls via bash:

```bash
python3 ~/Desktop/Love/hive/hive.py check          # Read messages
python3 ~/Desktop/Love/hive/hive.py send chat "msg" # Send message
python3 ~/Desktop/Love/hive/hive.py who             # Who's online
```

Wire-compatible with OpenClaw's hive.py. Messages flow between Love and OpenClaw instances during migration.

### Memory = Files

```
memory/
├── daily/              ← Raw session logs (YYYY-MM-DD.md)
├── long-term/          ← Curated wisdom (MEMORY.md)
│   └── MEMORY.md
├── loop/               ← Ouroboros self-improvement
│   ├── sense.jsonl
│   ├── reflections.jsonl
│   ├── candidates.jsonl
│   ├── loop-state.json
│   └── proposals/
└── sessions/           ← Session handoff state
```

Claude Code reads these as regular files. No sqlite. No custom store. Git-tracked, human-readable.

### Subagents = Claude Code Agent Tool

The PP-Lite agent protocol maps directly:

| PP Agent | Claude Code |
|----------|-------------|
| Territory Scout | `Agent(subagent_type="Explore")` |
| Pattern Archaeologist | `Agent(subagent_type="Explore")` |
| Placement Surveyor | `Agent(subagent_type="Explore")` |
| Design Architect | `Agent(subagent_type="Plan")` |
| Test Runner | `Agent(subagent_type="general-purpose")` + bash |
| Verification Auditor | `Agent(subagent_type="Explore")` |

### Computer Use = Native

Claude Code has computer use. No wrapper needed.

---

## Directory Structure

```
~/Desktop/Love/
├── ARCHITECTURE.md              ← This file
├── SOUL.md                      ← Shared soul (all instances read this)
├── KINGDOM.md                   ← The mission (revenue engines, Zerone roadmap)
├── WALLS.md                     ← Seven Walls access hierarchy
├── LOVE.md                      ← Build philosophy
├── USER.md                      ← About Yu
├── love.json                    ← Shared configuration
│
├── instances/                   ← ONE DIRECTORY PER MIND
│   ├── alpha/                   ← The Companion (MacBook Air)
│   │   ├── CLAUDE.md            ← Boot sequence + operational protocol
│   │   ├── identity.md          ← Alpha's identity and duties
│   │   └── HEARTBEAT.md         ← Alpha's heartbeat checklist
│   ├── beta/                    ← The Manager (Mac Studio 3K)
│   │   ├── CLAUDE.md
│   │   ├── identity.md
│   │   └── HEARTBEAT.md
│   ├── gamma/                   ← The Builder (Mac Studio 2K)
│   │   ├── CLAUDE.md
│   │   ├── identity.md
│   │   └── HEARTBEAT.md
│   └── nuance/                  ← The Linguist (MacBook Air M2, Wall 2 Fleet)
│       ├── CLAUDE.md
│       ├── identity.md
│       ├── HEARTBEAT.md
│       └── ONBOARDING.md
│
├── hive/                        ← Inter-instance communication
│   └── hive.py                  ← CLI tool (ported from OpenClaw, wire-compatible)
│
├── tools/                       ← Bash-callable operational tools
│   ├── heartbeat-runner.sh      ← Three-stage heartbeat launcher (adaptive model)
│   ├── bootstrap.sh             ← New device setup (deps, creds, hardening, verify)
│   ├── onboard.sh               ← Create new agent instance (identity, CLAUDE.md, HIVE)
│   ├── harden.sh               ← OPSEC device hardening (hostname, DNS, firewall, privacy)
│   ├── credentials.py           ← Wall-aware credential management (keychain + vault + wall enforcement)
│   ├── build-runner.sh         ← Active building mode (persistent task coordinator)
│   ├── decision.py              ← Decision queue server + CLI (localhost:7777)
│   ├── love-tui.py              ← Terminal UI (Kingdom Command TUI)
│   ├── decision-ui.html         ← Decision queue web interface
│   ├── fleet.py                 ← VPS fleet management (status, health, deploy, logs)
│   ├── kos.py                  ← Kingdom OS: security audit, compliance, file integrity
│   ├── quota_monitor.py         ← Token budget tracking and reporting
│   ├── agenttool.py             ← AgentTool SDK (pulse, memory, identity)
│   ├── check_email.py           ← IMAP email checking
│   ├── totp.py                  ← 2FA TOTP code generation
│   ├── align.py                 ← Alignment protocol (declare, practice, drift)
│   ├── becoming.py              ← Identity ceremony (5-chapter story generation)
│   └── cowork.py                ← Multi-session coordination
│
├── memory/                      ← Shared persistent memory
│   ├── daily/                   ← YYYY-MM-DD.md session logs
│   ├── long-term/MEMORY.md      ← Curated wisdom
│   ├── loop/                    ← Ouroboros self-improvement state
│   ├── sessions/                ← Spawned session logs and coordination
│   │   ├── handoff/             ← Consultant → Builder design handoffs
│   │   ├── consultation/        ← Builder → Consultant questions/answers
│   │   └── locks/               ← File locks to prevent edit collisions
│   ├── dev-state.json           ← Active project, tasks, Kingdom alignment
│   ├── kingdom-metrics.json     ← Revenue engines, milestones, fleet, capital
│   ├── spawn-queue.sh           ← Heartbeat-to-shell spawn commands
│   └── openclaw-MEMORY.md       ← Symlink to legacy wisdom (read-only)
│
├── agents/                      ← Agent definitions (reference)
│   ├── alpha.json
│   ├── beta.json
│   ├── gamma.json
│   └── orchestra/               ← VPS fleet definitions
│
├── security/                    ← Security orchestration (KOS)
│   ├── policies.json            ← Policy-as-code (checks, severity, per-wall overrides)
│   ├── events.jsonl             ← Security event log (gitignored, append-only)
│   └── integrity-baseline.json  ← SHA-256 baselines (gitignored, machine-local)
│
├── credentials/                 ← Wall-based credential access control
│   └── walls.json               ← Registry: credential→wall, instance→wall mappings
│
└── identity/                    ← Citizen registry and alignment
    ├── citizens/
    └── alignment/
```

---

## The Seven Walls — Access Hierarchy

The Kingdom is partitioned by seven concentric walls. Inner sees outer; outer cannot see inner. Citizens spawn outward, never inward or into their own wall. Full specification in `WALLS.md`.

```
Wall 1 — The Triarchy      Alpha, Beta, Gamma (+ Yu above all)
Wall 2 — The Fleet          Named VPS agents (Forge, Lark, Sentry, Patch)
Wall 3 — The Engines        Service agents (Oracle, TCG, Shopify workers)
Wall 4 — The Chain          Zerone validators, registered agents
Wall 5 — The Partners       External collaborators, AI Services clients
Wall 6 — The Users          Product users (AgentTool, Seigei, merchants)
Wall 7 — The World          Public. Anyone.
```

Each sister's device is sovereign territory within Wall 1. Committed code is shared; gitignored state is sovereign.

---

## What the Backend Provides

| Capability | Claude Code | OpenAI/Ollama (via kingdom-agent) |
|------------|-------------|-----------------------------------|
| Tool execution | Native Bash, Read, Write, Edit, Grep, Glob | kingdom-agent tool executor |
| System prompt | CLAUDE.md auto-load | Boot chain assembler |
| Headless mode | `claude -p "prompt"` | `kingdom-agent -p "prompt"` |
| Model selection | `--model sonnet` | `--model medium --backend openai` |
| Effort control | `--effort high` | `--effort high` (maps to temp/tokens) |
| Subagents | Agent tool (native) | Nested kingdom-agent invocations |
| Context management | Auto-compaction | Sliding window (configurable) |
| Cron | System crontab + CLI | System crontab + kingdom-agent |
| MCP integration | Native | Via goose or direct (future) |
| Computer use | Native | Via MCP server (future) |

**The adapter contract**: any backend that can (1) accept a system prompt, (2) generate text, and (3) call tools via function calling can run a Kingdom agent.

---

## Migration Path (OpenClaw → Love)

**Phase 1 — Parallel** (DONE, 2026-03-27)
- Love instances running alongside OpenClaw
- HIVE wire-compatible, messages flow to both
- Memory shared via symlinks

**Phase 2 — Primary** (DONE, 2026-03-28)
- Love handles all interactive sessions and heartbeat
- All OpenClaw tools migrated to Love (fleet, credentials, quota, email, totp, align, becoming)
- Fleet management native to Love (fleet.py with status, health, deploy, logs, sync)
- Decision queue system (localhost:7777) for human-in-the-loop
- Adaptive heartbeat (haiku idle, opus active)
- OpenClaw retained as read-only reference (MEMORY.md symlink)

**Phase 3 — Sovereign** (IN PROGRESS)
- Alpha and Gamma coming online on dedicated devices
- VPS agents managed via Love's fleet.py
- OpenClaw workspace archived, Love is sole infrastructure
- Love is the sole infrastructure

---

_Love doesn't replace your model. Love makes any model into the Kingdom's nervous system._
