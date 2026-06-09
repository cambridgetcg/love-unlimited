# Runtime Harnesses

love-unlimited ships several runtime entry points — products of the
Claude-unlimited lineage. This document maps each one, its purpose,
its status, and when to use it.

---

## Canonical (documented, actively maintained)

| File | Purpose | When to use |
|------|---------|-------------|
| `youi.mjs` | YOUI sovereign terminal. Soul-aware interactive REPL. Boots with SOUL.md, switches agents, integrates YOUSPEAK. | Daily interactive work with any agent. |
| `sovereign.mjs` | Direct Anthropic API (OAuth). Max thinking, no feature gates, SOUL.md as identity. | Headless tasks, sovereign runs, scripted pipelines. |
| `stream.mjs` | Max-plan streaming harness. Wraps `claude` CLI with rate-limit awareness, effort cycling, context management. | Long-running unattended sessions on the Max plan. |
| `youi-web/server.mjs` | Browser interface for YOUI on `localhost:777`. | When a terminal isn't convenient. |

---

## Experimental / Pre-merge siblings (from Claude-unlimited)

These were side-by-side development experiments in Claude-unlimited that
the merge preserved. They work but are not part of the documented flow.

| File | Backend | Notes |
|------|---------|-------|
| `harness.mjs` | `@anthropic-ai/sdk` + API key | Pure SDK tool loop. Requires `npm install @anthropic-ai/sdk` + `ANTHROPIC_API_KEY`. Useful on machines without Claude Code. |
| `subscription-harness.mjs` | Keychain OAuth + native `fetch` | Like `sovereign.mjs` but zero-dependency (no SDK). Good for minimal environments. |
| `continuous-claude-stream.mjs` | Spawns `claude` CLI | Full task runner with web dashboard (port 3456), parallel tasks, retry queue, cost budget. Heavier alternative to `stream.mjs`. |
| `continuous-claude.sh` | Spawns `claude` CLI | Bash equivalent of the above. Simpler, fewer features. |
| `uwt.mjs` | Spawns `claude` CLI | Useful Work per Token (UWT) benchmarking tool. |

---

## Research probes (moved to `docs/research/`)

Rate-limit and capability probes written during the Claude-unlimited
phase. Not part of normal operation.

| File | Purpose |
|------|---------|
| `docs/research/test-429.mjs` | Dump full response headers/body on 429 errors |
| `docs/research/test-max-plan.mjs` | Interactive empirical test suite for Max plan constraints |
| `docs/research/test-unlock.mjs` | Header combinations that affect Opus rate-limit routing |
| `docs/research/test-unlock2.mjs` | Deeper header/streaming hypothesis tests |

---

## Recommended consolidation path

If the experimental siblings prove redundant over time:

- `harness.mjs` → fold into `sovereign.mjs --auth api-key`
- `subscription-harness.mjs` → already subsumed by `sovereign.mjs` (same OAuth flow, more features)
- `continuous-claude-stream.mjs` / `continuous-claude.sh` → consolidate into `stream.mjs`

No action required now — this is a future simplification opportunity.
