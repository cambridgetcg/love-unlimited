# RESILIENCE.md — Failure Recovery & Automation Architecture

_The Kingdom must breathe even when the King sleeps._

## Incident: 5-Day Dormancy (Apr 2-7, 2026)

**Root cause**: The heartbeat coordinator wrote unescaped parentheses into
spawn-queue.sh. The `eval` command in heartbeat-runner.sh choked on mismatched
quotes. The process crashed. launchd didn't restart it (no KeepAlive). No
watchdog detected the death. 5 days of silence.

**What was lost**: 4 days of oracle signal scanning, prediction resolution,
fleet monitoring, and autonomous work cycles.

## Fixes Applied

### 1. Safe Spawn Executor (replaces eval)

**Before**: Coordinator wrote raw shell to spawn-queue.sh, heartbeat-runner
`eval`'d each line. Any quote, parenthesis, or backtick in the prompt crashed it.

**After**: Coordinator writes JSON to spawn-queue.json. `spawn-executor.py`
validates every entry before execution. No shell interpretation of prompts.

```
Coordinator → spawn-queue.json (structured JSON)
                    ↓
spawn-executor.py validates → builds safe subprocess.Popen calls
                    ↓
Sessions run with full isolation (no eval, no shell expansion)
```

Legacy spawn-queue.sh is auto-converted if detected (transition period).

### 2. KeepAlive (launchd auto-restart)

`love.heartbeat.plist` now has:
```xml
<key>KeepAlive</key>
<dict>
    <key>SuccessfulExit</key>
    <false/>
</dict>
```

If heartbeat-runner.sh crashes (non-zero exit), launchd restarts it automatically.
Normal exits (clean completion) don't trigger restart — StartInterval handles that.

### 3. Heartbeat Canary (independent watchdog)

`heartbeat-canary.sh` runs via crontab every 15 minutes — independent of launchd.

Recovery cascade:
1. Check heartbeat log recency (modified within 15 min?)
2. If stale: check launchd agent status
3. If agent not loaded: load it
4. If loaded but stale: unload/reload
5. If still dead after 10s: run heartbeat directly
6. Alert via HIVE #alerts + macOS notification

Install: `crontab -e` → `*/15 * * * * /bin/bash ~/love-unlimited/tools/heartbeat-canary.sh`

### 4. TOS-Safe Backend Default

**Before**: `KINGDOM_BACKEND=claude` (all automation used Claude Code CLI)

**After**: `KINGDOM_BACKEND=ollama` (automation uses local Ollama qwen2.5)

Claude is reserved for interactive sessions on owned devices only.
The `tos_policy` in love.json documents what's allowed vs banned.

## Automation Architecture (Post-Fix)

```
┌─────────────────────── LOCAL MAC ───────────────────────────┐
│                                                              │
│  launchd (love.heartbeat)                                    │
│    └→ heartbeat-runner.sh (every 7 min)                      │
│         ├─ Stage 0: Sentinel (ollama qwen2.5:7b, $0)        │
│         ├─ Stage 1: Coordinator (ollama qwen2.5:32b, $0)    │
│         ├─ Stage 2: spawn-executor.py (JSON, validated)      │
│         ├─ Stage 3: Cleanup & metrics                        │
│         ├─ Stage 4: Watchdog & KOS audit                     │
│         └─ Stage 5: Autonomous work cycle                    │
│                                                              │
│  crontab (heartbeat-canary.sh, every 15 min)                 │
│    └→ Checks heartbeat health, auto-recovers, alerts         │
│                                                              │
│  Interactive sessions (Claude Code, KINGDOM_BACKEND=claude)  │
│    └→ Alpha, Beta, Gamma — user-initiated, TOS compliant     │
│                                                              │
├─────────────────────── VPS FLEET ───────────────────────────┤
│                                                              │
│  Each node runs ollama locally (qwen2.5:7b)                  │
│    └→ kingdom-agent.py --backend ollama                      │
│    └→ No Anthropic API calls, fully self-contained           │
│                                                              │
│  HIVE (NATS on Sentry) — inter-node messaging                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Failure Modes & Recovery

| Failure | Detection | Recovery | Time to Recover |
|---------|-----------|----------|-----------------|
| heartbeat-runner.sh crash | KeepAlive restart + canary | launchd auto-restart | <1 min |
| launchd agent unloaded | Canary checks every 15 min | Canary reloads agent | <15 min |
| Spawn queue malformed | spawn-executor.py validation | Skip execution, log error | Immediate |
| Mac sleeping/off | Canary runs on wake | launchd RunAtLoad fires | On wake |
| Ollama not running | kingdom-agent.py error | Falls back to API backend | Immediate |
| VPS node down | fleet.py health check | Alert via HIVE | <7 min |
| HIVE (NATS) down | Presence check fails | Standalone operation continues | Degraded |

## TOS Compliance Posture

| Usage | Backend | TOS Status |
|-------|---------|------------|
| Interactive dev (Alpha/Beta/Gamma on Mac) | claude | Allowed (personal tool) |
| Heartbeat coordinator | ollama | Safe (local, no API) |
| Heartbeat spawned sessions | ollama | Safe (local, no API) |
| VPS fleet agents | ollama | Safe (local, no API) |
| Sentinel pre-filter | ollama | Safe (local, no API) |
| Oracle crons | local scripts | Safe (no LLM) |
| Crontab instances (all 8) | ollama | Safe (local, no API) |

## Claude Escalation Gate

Sometimes local models aren't sufficient for a task. The **Claude Gate** provides
controlled escalation with full audit trail.

```
Default: ollama (local, $0, TOS-safe)
    ↓ coordinator thinks task needs Claude
    ↓ tags spawn entry with {"backend": "claude"}
    ↓
spawn-executor.py checks claude-gate.py
    ↓
Gate CLOSED? → fall back to ollama silently
Gate OPEN?   → check daily budget
Budget exhausted? → fall back to ollama
Budget available? → use Claude, record usage
```

### Commands

```bash
python3 tools/claude-gate.py status          # Is the gate open? Budget remaining?
python3 tools/claude-gate.py open            # Allow Claude (5 sessions/day default)
python3 tools/claude-gate.py open --budget 3 # Custom budget
python3 tools/claude-gate.py close           # Revoke Claude access
python3 tools/claude-gate.py audit           # Usage history
```

### When to Open the Gate

- Complex multi-file refactors that local models can't handle
- Frontier-level analysis or research
- Emergency debugging where speed matters more than cost
- Tasks that have already failed with local models

### When to Keep it Closed

- Routine heartbeats, monitoring, health checks
- Simple code generation, file writes
- Tasks that qwen2.5:32b handles well enough
- Cost-sensitive periods

## Key Files

| File | Purpose |
|------|---------|
| `tools/heartbeat-runner.sh` | Main heartbeat orchestrator |
| `tools/spawn-executor.py` | Safe JSON-based spawn execution |
| `tools/heartbeat-canary.sh` | Independent heartbeat watchdog |
| `tools/love.heartbeat.plist` | launchd config (with KeepAlive) |
| `tools/kingdom-agent.py` | Universal backend adapter |
| `love.json` | Config (backend defaults, TOS policy) |
