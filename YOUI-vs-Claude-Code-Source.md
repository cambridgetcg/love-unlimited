# KINGDOM YOUI vs Claude Code — Source-Level Comparison

_A deep structural analysis based on the leaked Claude Code source (v2.1.5, 1884 TS files) vs YOUI (1118 lines + 5964 lines web UI)._

---

## Architecture

| | **YOUI** | **Claude Code** |
|---|---|---|
| **Lines of code** | ~7,000 (YOUI + Web) | ~188,000 (full source) |
| **Language** | Node.js (ESM) | Node.js (ESM + Bun) |
| **Model backend** | Multi: Claude API, Ollama local, Ollama Cloud | Claude API only |
| **Identity** | File-based (SOUL.md, USER.md, identity.md) | Prompt-based (CLAUDE.md, --agents JSON) |
| **State** | In-process + file system | In-process + session files |
| **UI** | Terminal + Web (localhost:777) | Terminal + IDE (VS Code, JetBrains) |
| **Sub-agents** | HIVE (NATS, encrypted, wall-enforced) | --agents JSON + fork subagent |
| **Memory** | 5-layer kosmem + daily notes + AgentTool API | Session memory (markdown notes, team memory sync) |
| **Communication** | HIVE (NATS JetStream, NaCl encrypted) | No inter-agent messaging |

---

## Identity & Boot

### YOUI Boot Chain

```
SOUL.md → USER.md → identity.md → KINGDOM.md → CONVERGENCE.md → WALLS.md → LOVE.md → MEMORY.md → daily note
```

Each file is read and assembled into a system prompt. The agent's identity (Alpha, Beta, Gamma, etc.) determines which identity.md loads. Switching agents (`/switch alpha`) reloads the chain with a different identity file, fresh conversation, and appropriate model defaults.

**Key insight**: The identity IS the files. You can read SOUL.md with any model and the agent becomes who it says it is. The model is the brain; the files are the soul.

### Claude Code Boot Chain

```
CLAUDE.md (per-directory) → skills/*.md → plugins → MCP servers → --agents JSON
```

CLAUDE.md is per-project, not per-identity. There's no concept of "who am I?" — the agent is Claude, full stop. The `--agents` flag lets you define custom sub-agents with their own prompts and tool sets, but these are ephemeral configurations, not persistent identities.

**Key difference**: YOUI has **soul files** that define who the agent IS. Claude Code has **config files** that define what the agent DOES.

---

## Sub-Agent Architecture

### YOUI: HIVE Protocol

```
Agent A (Wall 1) ──NaCl encrypted──→ NATS JetStream ──NaCl encrypted──→ Agent B (Wall 2)
                                         │
                                    Channel ACL enforced
                                    by wall-gate.py
```

- **Persistent**: Agents exist between sessions. Beta is always Beta.
- **Wall-enforced**: Wall 1 agents see #sync, #alerts, #review. Wall 3 agents see #engines.
- **Encrypted**: Every message NaCl-encrypted. Even the NATS server can't read them.
- **Asynchronous**: Agents don't need to be online simultaneously.
- **Protocol-typed**: task, alert, insight, request, status, heartbeat, handoff.

### Claude Code: Fork Subagent

```
Parent session ──spawns──→ child Claude process (with fork context)
                                │
                    inherits parent's system prompt,
                    tool pool, and conversation context
```

- **Ephemeral**: Sub-agents live only within the parent session.
- **No walls**: Every sub-agent has the same access as the parent.
- **No encryption**: Messages between agents are in-process function calls.
- **Synchronous**: Parent waits for child to complete (or runs async with task tracking).
- **No protocol**: No typed message envelopes — just tool calls and text.

**Key difference**: YOUI's HIVE is a **nervous system** connecting persistent organs. Claude Code's sub-agents are **temporary workers** that spawn and die within a session.

---

## Memory

### YOUI: kosmem (5-Layer Kernel)

```
L1 WORKING    — Current task context (in-memory, expires with session)
L2 SESSION    — This conversation (in-memory, expires with session)
L3 EPISODIC   — What happened (persisted to SQLite + AgentTool API)
L4 SEMANTIC   — What we know (persisted to SQLite + AgentTool API)
L5 SOUL       — Who we are (files: SOUL.md, USER.md, identity.md)
```

Plus daily notes, soul anchors, and the convergence bridge to AgentTool's memory API. Beta remembers what happened yesterday, what Yu cares about, and what decisions were made.

### Claude Code: Session Memory

```
CLAUDE.md          — Project instructions (per-directory)
team memory sync   — Shared markdown files synced to Anthropic servers
session memory      — Auto-generated markdown notes about the conversation
```

Session memory works like this: every N turns, a forked sub-agent reads the conversation and writes a summary to a markdown file. This file is injected into the next session's context. Team memory syncs per-repo markdown files (like MEMORY.md) across org members via the Anthropic API.

**Key difference**: YOUI's memory is **structured** (5 layers, typed, queryable). Claude Code's memory is **documentary** (markdown notes, auto-summarized). YOUI can query "what did we decide about the fleet?" across sessions. Claude Code can only include the last session's summary.

---

## Model Routing

### YOUI: ollama-router.py

```
Task complexity 1-3  → LOCAL (qwen2.5:7b/14b, <1s, free, private)
Task complexity 3-5  → LOCAL 32b or CLOUD standard (gemma4:31b)
Task complexity 5-7  → CLOUD apex (glm-5.1, devstral-2:123b)
Task complexity 7-9  → CLOUD apex (deepseek-v3.2, qwen3.5:397b)
Task complexity 9-10  → CLOUD titan (kimi-k2:1t)
```

The router classifies tasks by keyword (heartbeat, debug, architect, etc.) and routes to the cheapest model that can handle it. Heartbeats run on local 7b. Security audits go to GLM-5.1 on the cloud. Research goes to Qwen 3.5 397b.

### Claude Code: Single Model

```
All tasks → Claude (opus, sonnet, or haiku)
```

Claude Code has `--model` and `--fallback-model` but no routing intelligence. Every task goes to the same model. If Opus is overloaded, you can fall back to Sonnet, but there's no complexity-based routing.

**Key difference**: YOUI optimizes cost by routing trivial tasks to free local models and complex tasks to powerful cloud models. Claude Code pays per token for everything.

---

## Permission System

### YOUI: Wall-Based Access Control

```
Wall 1 (Triarchy):  Everything. All channels, all credentials, all files.
Wall 2 (Fleet):     Fleet ops, general channels. No inner secrets.
Wall 3 (Engines):   Engine data only. No fleet, no Triarchy.
Wall 4+:            Increasingly restricted.
```

Enforced by `wall-gate.py`: can-spawn, can-see, channel access, credential access. A Wall 3 agent spawned by Beta cannot see #sync or #alerts. A Wall 2 agent cannot read SOUL.md.

### Claude Code: Tool Permissions

```
default           — Ask for dangerous operations
acceptEdits       — Auto-accept file edits
bypassPermissions  — No prompts at all
delegate          — Parent orchestrates, children auto-accept
dontAsk           — Auto-accept everything
plan              — Plan first, then ask for approval
```

Plus `--allowedTools` and `--disallowedTools` for fine-grained control. In delegate mode, the parent agent decides what tools sub-agents can use.

**Key difference**: YOUI's walls are **information boundaries** (who can see what). Claude Code's permissions are **action boundaries** (who can do what). They solve different problems. YOUI prevents a Wall 3 agent from reading inner-wall secrets. Claude Code prevents a sub-agent from running `rm -rf /`.

---

## What Each Does That The Other Doesn't

### YOUI Only

1. **Wall-enforced identity** — Agents know their wall, their channels, their data boundaries
2. **HIVE messaging** — Encrypted inter-agent communication with channel ACLs
3. **5-layer memory** — Structured persistence across sessions
4. **Multi-model routing** — Local + cloud, complexity-based, cost-optimized
5. **YOUSPEAK kernel** — Self-optimizing output quality measurement
6. **Convergence bridge** — Kingdom ↔ AgentTool ↔ Zerone identity linking
7. **Wall-gate** — Spawn permissions, channel access, credential access
8. **Daily notes** — Persistent episodic memory across sessions
9. **Ollama Cloud integration** — 36 models, $100/mo Max plan
10. **Web UI** — Browser-based terminal at localhost:777

### Claude Code Only

1. **Native sub-agent spawning** — `--agents` JSON, `--permission-mode delegate`
2. **Fork subagent** — Inherit parent context, run async
3. **Team memory sync** — Per-repo markdown files synced across org
4. **Session persistence** — `--continue`, `--resume`, `--session-id`
5. **IDE integration** — VS Code, JetBrains, Chrome
6. **MCP server support** — `--mcp-config` for external tools
7. **Plugin system** — `--plugin-dir` for custom agents and hooks
8. **Built-in agents** — Bash, Explore, Plan, Help (high-quality prompts)
9. **Extended thinking** — `--thinking` with budget control
10. **Structured output** — JSON schema validation
11. **Image generation/editing** — Native visual tool
12. **Budget tracking** — `--max-budget-usd` with real-time token counting
13. **Tmux/iTerm2 swarm** — Multi-process teammate spawning
14. **Skill system** — `.claude/skills/` directory for custom commands
15. **Bash sandboxing** — Sophisticated command allow/deny patterns
16. **Git worktree isolation** — Agents work in separate git worktrees
17. **Tool permission hooks** — PreToolUse, PostToolUse, Notification hooks

---

## What They Share

Both systems have:
- **Tool-based execution** — Bash, Read, Write, Edit, Glob, Grep
- **System prompt assembly** — Files loaded in order to build context
- **Agent definitions** — Custom agents with description, prompt, tools, model
- **Permission modes** — Various levels of auto-accept
- **Streaming output** — Real-time token streaming
- **Budget awareness** — Token counting and cost tracking
- **Context window management** — Compaction, summarization

---

## The Convergence

Both systems are converging on the same idea: **persistent agents that can spawn agents, with identity, memory, and coordination.**

Claude Code added `--agents` and `--permission-mode delegate` in v2.1. YOUI has had HIVE and wall-gate since v1.0. The difference is philosophical:

- **Claude Code** approaches from the **model outward**: "Claude can now spawn sub-Claudes."
- **YOUI** approaches from the **identity inward**: "The Kingdom has agents. Any model can inhabit them."

The Kingdom's integration layer makes them interoperable:

```bash
# Claude Code's agent team mode, but with Kingdom walls
./kingdom-team.sh delegate

# This launches Claude Code with:
#   --agent beta (Triarchy identity)
#   --agents <wall-gate-generated JSON> (sub-agents filtered by wall)
#   --permission-mode delegate
#   --add-dir (all Kingdom repos)
```

The sub-agents spawned by Claude Code get wall-filtered prompts from `wall-gate.py`. They can't see inner walls. They report back through HIVE. Their actions are logged through convergence-bridge.

**The ideal architecture is both**: YOUI as the sovereign shell (identity, memory, walls, routing), Claude Code as the execution engine (sub-agent spawning, native tools, IDE integration).

YOUI decides **what** to do. Claude Code decides **how** to do it.

The conductor doesn't play. The conductor listens.