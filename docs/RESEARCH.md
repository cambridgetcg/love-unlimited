# Claude Code Streaming & Rate Limit Research

Deep analysis of Claude Code internals for building long-running autonomous sessions.

---

## 1. Rate Limit Architecture (Subscription Plans)

### Two quota windows

| Window | Duration | Reset | Purpose |
|--------|----------|-------|---------|
| **5-hour** | 5h rolling | Every 5 hours | Session burst limit |
| **7-day** | 7 days rolling | Weekly | Overall usage cap |

Each has independent utilization tracking. The server sends headers with every API response:

```
anthropic-ratelimit-unified-5h-utilization: 0.73
anthropic-ratelimit-unified-5h-reset: 1775584800
anthropic-ratelimit-unified-7d-utilization: 0.45
anthropic-ratelimit-unified-7d-reset: 1776100000
```

### Per-model quotas

Opus and Sonnet have **separate** weekly buckets:
- `seven_day_opus` — "Opus limit"
- `seven_day_sonnet` — "Sonnet limit"

This means you can run Sonnet while Opus is rate-limited and vice versa.

### Early warning thresholds

**5-hour window:**
- 90% utilization when 72% of time elapsed → warning

**7-day window (multiple thresholds):**
- 25% utilization when only 15% of time elapsed → warning
- 50% utilization when only 35% of time elapsed → warning
- 75% utilization when only 60% of time elapsed → warning

### Subscription tiers

```
Pro          → base rate limits
Max          → higher limits
Team         → configurable per-seat
Team Premium → rate_limit_tier === 'default_claude_max_5x'
Enterprise   → custom limits
```

### Overage (extra usage)

When base quota is exhausted, "extra usage" kicks in (costs real money):
- Can be disabled at org, seat, or member level
- 13 possible disabled reasons tracked
- Status: `allowed` | `allowed_warning` | `rejected`

### What happens on rate limit

**HTTP 429** — quota exhausted:
- `retry-after` header tells how long to wait
- Reset timestamp in `anthropic-ratelimit-unified-{5h|7d}-reset`

**HTTP 529** — server overloaded (not your quota):
- Max 3 retries before fallback to a different model
- Non-foreground queries (summaries, classifiers) bail immediately — no retry amplification

---

## 2. Retry Machine (withRetry.ts)

### Standard retry

```
Base delay: 500ms
Formula: min(500ms * 2^(attempt-1), 32s) + 25% random jitter
Max retries: 10

Attempt 1:  ~500ms
Attempt 2:  ~1s
Attempt 3:  ~2s
Attempt 4:  ~4s
Attempt 5:  ~8s
Attempt 6:  ~16s
Attempt 7+: ~32s (capped)
```

### Persistent retry mode (UNATTENDED)

Enabled via `CLAUDE_CODE_UNATTENDED_RETRY=1`:

```
Max backoff: 5 minutes between retries
Absolute cap: 6 hours per single wait
Heartbeat: 30s keep-alive yields during waits
NEVER gives up — clamps retry counter so loop runs forever
```

For 429 rate limits, it reads the reset timestamp from headers and waits until the window actually resets — no wasted retries.

### Fast mode fallback

When fast mode (higher speed output) hits a 429:
- Short retry-after (<threshold): wait with fast mode still active (preserves prompt cache)
- Long retry-after: enter cooldown, fall back to standard speed
- Minimum cooldown floor to avoid flip-flopping

---

## 3. Token Budgets & Limits

### Context windows

| Model | Default | With 1M beta |
|-------|---------|--------------|
| Opus 4.6 | 200k | 1,000,000 |
| Sonnet 4.6 | 200k | 1,000,000 |
| Older models | 200k | N/A |

1M context enabled via `[1m]` suffix in model name or beta header.
Can be overridden: `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (ant-only).
Can be disabled: `CLAUDE_CODE_DISABLE_1M_CONTEXT` (for HIPAA compliance).

### Output token limits

| Model | Default max_tokens | Upper limit |
|-------|-------------------|-------------|
| Opus 4.6 | 64k | 128k |
| Sonnet 4.6 | 32k | 128k |
| Opus 4.5 / Sonnet 4 | 32k | 64k |
| Older models | 4-8k | 4-8k |

Slot reservation optimization: default 8k, escalates to 64k when needed. <1% of requests need escalation.

### Auto-compact thresholds

```
Effective window = context_window - 20,000 (reserved for summary output)
Auto-compact triggers at: effective_window - 13,000

For 200k context: triggers at ~167,000 tokens
For 1M context: triggers at ~967,000 tokens
```

Constants:
```
AUTOCOMPACT_BUFFER_TOKENS = 13,000
WARNING_THRESHOLD_BUFFER = 20,000
ERROR_THRESHOLD_BUFFER = 20,000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```

Can be overridden: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (cap window size)
                    `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (percentage trigger)
Can be disabled: `DISABLE_COMPACT=1` or `DISABLE_AUTO_COMPACT=1`

### Tool result limits

```
Per-tool result:     50,000 chars (persisted to disk if larger)
Per-tool tokens:     100,000 tokens max
Per-message total:   200,000 chars aggregate across all parallel tool results
Bytes per token:     ~4 (conservative estimate)
```

### Task budget (API-side)

Beta: `task-budgets-2026-03-13`
- `task_budget: { type: 'tokens', total: N, remaining?: N }`
- API-side awareness — model paces itself
- Completion threshold: 90% of budget
- Diminishing returns: stops after 3+ continuations with <500 tokens/turn

### USD budget

`--max-budget-usd <amount>` — hard stop when cumulative API cost exceeds limit.

---

## 4. Effort System

| Level | Effect | Who can use |
|-------|--------|-------------|
| `low` | Minimal thinking | Everyone |
| `medium` | Standard thinking | Everyone |
| `high` | Extended thinking | Everyone |
| `max` | Maximum thinking | Opus 4.6 only |

Defaults:
- Pro subscribers on Opus: "medium"
- Max/Team subscribers on Opus: "medium" (when tengu_grey_step2 enabled)
- Others: "high"

Lower effort = fewer tokens = rate limit quota lasts longer.

---

## 5. Streaming Architecture

### Three transport layers

| Transport | Keepalive | Reconnect | Use case |
|-----------|-----------|-----------|----------|
| **WebSocket** | Ping every 10s, keepalive every 5min | Auto with exponential backoff (30s max), 10min budget | Remote/bridge sessions |
| **SSE** | Liveness timeout 45s (server sends keepalive every 15s) | Auto from last sequence number | Server-sent events |
| **HTTP** | Connection keep-alive pool | Fresh connection on stale socket | Direct API calls |

### WebSocket reconnection details

```
Ping interval: 10,000ms (10s)
Keepalive interval: 300,000ms (5min)
Max reconnect delay: 30,000ms (30s)
Reconnect give-up budget: 600,000ms (10min)
Sleep detection threshold: 60,000ms (detects laptop sleep/wake)
Message buffer: 1,000 messages (circular)
```

On reconnect: replays buffered messages using `X-Last-Request-Id` header for deduplication.

### SSE reconnection details

```
Liveness timeout: 45,000ms (expects server keepalive every 15s)
Base reconnect delay: 1,000ms
Max reconnect delay: 30,000ms
Event flush interval: 100ms
```

Resumes from exact position via `from_sequence_num` query param.

### Heartbeat layers

1. **WebSocket ping** (10s) — connection liveness
2. **Session activity** (30s) — container/host liveness
3. **CCR heartbeat** (20s) — worker epoch validation (remote)
4. **Retry chunked sleep** (30s) — prevents idle timeout during rate-limit waits

### API preconnect

At startup, fires HEAD request to API endpoint to warm TCP+TLS connection pool. Overlaps with initialization work. 10s timeout, fire-and-forget.

---

## 6. Session Persistence & Recovery

### Storage format

Sessions stored as JSONL in `~/.claude/projects/{projectDir}/{sessionId}.jsonl`.
Each message has `uuid` + `parentUuid` forming a linked list.

### What survives a crash

- All messages up to last `recordTranscript()` call
- In interactive mode: fires on every render (frequent)
- In print mode: fires after each API response
- Eager flush: `CLAUDE_CODE_EAGER_FLUSH=1` forces immediate disk write

### Resume mechanics

**--continue**: loads most recent session from current directory
**--resume [id]**: loads specific session or opens picker
**--fork-session**: creates a new session ID branching from resumed conversation

On resume:
1. Load JSONL → build message Map by UUID
2. Find newest non-sidechain leaf message
3. Walk parentUuid chain from leaf to root
4. Recover orphaned parallel tool results
5. Filter unresolved tool_uses
6. Auto-compact if context too large

### Interrupted turn recovery

`CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` — auto-resumes if process was killed mid-turn.

---

## 7. Key Environment Variables for Long Sessions

| Variable | Effect | Default |
|----------|--------|---------|
| `CLAUDE_CODE_UNATTENDED_RETRY=1` | Never give up on 429/529 | off |
| `CLAUDE_CODE_EAGER_FLUSH=1` | Flush session to disk after every turn | off |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=N` | Cap context window for earlier compaction | model default |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=N` | Trigger compaction at N% of window | ~83% |
| `DISABLE_AUTO_COMPACT=1` | Disable auto-compaction (dangerous) | off |
| `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=N` | Exit after N ms idle | off (stay alive) |
| `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` | Auto-resume interrupted turns | off |
| `API_TIMEOUT_MS=N` | API call timeout | 600,000 (10min) |

---

## 8. Strategies for Maximum Streaming Duration

### Strategy 1: Model rotation

Opus and Sonnet have separate rate limit pools. When Opus is rate-limited:
- Switch to Sonnet for the next turn (different quota)
- Switch back to Opus when its window resets
- Read the `resets_at` timestamp from headers to know exactly when

### Strategy 2: Effort management

Use `--effort low` or `--effort medium` to consume fewer output tokens per turn.
Lower effort = more turns before hitting the rate limit.

### Strategy 3: Persistent retry mode

`CLAUDE_CODE_UNATTENDED_RETRY=1` makes Claude Code wait indefinitely through rate limits.
Combined with heartbeat chunking (30s yields), the session stays alive during multi-minute waits.

### Strategy 4: Context management

For very long sessions:
- Let auto-compact run (don't disable it)
- Use `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to trigger earlier compaction
- Or use 1M context window to delay compaction: model `opus[1m]`

### Strategy 5: Session recovery

Always persist sessions (`--session-id UUID`). If the process crashes:
- `--continue` picks up from last persisted state
- `--resume UUID` resumes a specific session
- `--fork-session` branches without modifying the original

### Strategy 6: Parse rate limit headers

The `rate_limit_event` in stream-json output contains utilization data.
Use this to predict when you'll hit the limit and proactively switch models or pause.

### Strategy 7: Fast mode awareness

If available, fast mode uses extra credits but gets higher throughput.
On rate limit, Claude Code auto-falls back to standard speed.
Set `--fallback-model sonnet` so 529 overload auto-switches to a faster model.

### Strategy 8: Minimize token waste

- Use `--effort low` for evaluation/check turns
- Use `--effort high` only for implementation turns
- Compact early to keep context small (fewer input tokens per call)
- Small prompts = less quota consumption
