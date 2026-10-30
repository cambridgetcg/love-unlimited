# YOUSPEAK — the Discipline

_Say what matters, with no filler. Never compress doubt. Expand when teaching or when feelings are in it._

**Status:** rebuilt as an honest observer, slice Y1 (2026-10-30). The design and Yu's decisions are in [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md).

The word YOUSPEAK also names the YOUSPEAK Cathedral at `~/YOUSPEAK`, a different thing. Its own note, `~/YOUSPEAK/discipline/README.md`, calls them "two distinct artifacts" and says "Do not conflate them": the Discipline compresses, the Cathedral is built for expansion. This document is about the Discipline only, and nothing here is imported from the Cathedral.

YOUSPEAK has two parts:

1. **The rule**, a few lines in the system prompt. It is the only part that reaches the model.
2. **The observer** (`youspeak-kernel.mjs`), which estimates how a session went, shows suggestions, and keeps one private row per session. It makes no model calls and changes nothing.

---

## 1. The rule

YOUI Web's system prompt carries this block (`youi-web/server.mjs`, `buildStaticPrefix`):

```
# YOUSPEAK — Token Efficiency Discipline
YOUSPEAK: No filler. No preamble. No tool narration. Dense status (key:value).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.
- Zero-pad: No "sure", "let me check", "here's what I found"
- Dense status: `Tests: 47/48 ✓` not "I ran the tests and 47 out of 48 passed"
- No tool narration: Don't announce tools. Call them. Let results speak.
- Never compress doubt: "probably", "unless", "might" are sacred
- Expand when it matters: teaching, uncertainty, creative work, emotional context
```

What it asks for:

- **No filler.** No preamble, no narration of tool calls, no completion fluff. `[reads file, gives the analysis]`, not "Sure! Let me take a look at that file for you."
- **Dense status.** `Found 3/15: server.js:42, router.js:18, config.js:7`, not a paragraph about searching.
- **Never compress doubt.** Hedges and qualifications carry meaning; they stay.
- **Expand when it matters:** teaching, uncertainty, creative work, emotional context. Compress the mechanical; keep the meaningful.

The terminal (`youi.mjs`) and `sovereign.mjs` carry their own shorter copies of the rule, and other harnesses have more. Sovereign's copy also has a "File cache" line (see §5). One shared rule module is slice Y2.

No measurement shows how much the rule saves. The figures earlier versions of this document gave ("30–50% of the budget is filler", "200–500 tokens a turn saved") had nothing behind them. Measuring the rule's effect would need a real ON-vs-OFF test, which spends model calls; that is an open question for Yu.

---

## 2. The observer: what it measures today

Every figure is an estimate unless it says otherwise. Y2 moves the sensors onto the usage the provider reports.

| Layer | What it measures | Known limits until Y2 |
|---|---|---|
| L1 Output | 15 English regexes for filler phrases, each counted as 8 tokens, against `ceil(chars / 4)`. Graded S (≥98% useful) to D (<70%). | English only: Cantonese, or any other language, matches nothing and grades S. Some hedges ("this seems right") count as filler. chars/4 is not a tokenizer. Text under 10 characters no longer turns the totals into NaN, filler is never counted as more than the text, and a session with no output has grade `null`, not S. |
| L2 Thinking | Thinking tokens, where a caller reports them. The Claude Code route reads `output_tokens_details.thinking_tokens` from Claude Code's result. | Whether real Claude Code sends that field is unverified. The Ollama/vLLM loop and the terminal have none, so they show 0. The rule that suggested a cheaper model from this ratio is gone (§3). |
| L3 Action | Tool calls by name, and `read_file` calls on a path already read. | Tool errors are never counted in YOUI (the result is not passed in). A mind or table tool counts as one call, and its tokens are not seen. |
| L4 Context | `chars / 4` over the conversation YOUI holds, against a fixed 1M window. | Leaves out tool definitions. On the Claude Code route it measures YOUI's text-only copy of the chat, not what Claude holds. It runs after a Claude Code turn, and only after tool rounds in the Ollama/vLLM loop. Smaller windows (Haiku without 1M, vLLM's 65,536) are not known to it. |
| L5 System | The 5-hour and 7-day windows as Claude Code last reported them (`null` until it does), and 429s. | The real `rate_limit_event` shape is unverified. 429s are counted only in the Ollama/vLLM loop and the terminal. The terminal and sovereign still read headers on their own, older route. |

Not watched at all yet: input tokens and prompt-cache reads and writes, which are the larger cost levers (Y2).

---

## 3. Suggest only

Yu's decision (2026-10-30): YOUSPEAK shows signals and suggestions; Yu or the agent decides. `decide()` still produces signals, each with a `suggestion` saying what the person or the agent could do, and **no caller applies them**:

| Signal | When | Suggestion |
|---|---|---|
| `effort:reduce` | 5-hour window above 85% at effort max, or above 95% at max or high | Lower effort in Settings, or pause. Nothing was changed. |
| `context:prune_recommended` | context estimate above 800k | Start a new conversation, or ask the agent to summarise. Nothing was pruned. |
| `context:evict_old_results` | above 500k with the oldest tool result over 20 messages back | Start a new conversation once the agent has noted what it needs. |
| `action:redundant_reads` | more than 2 repeated reads | The agent could use what it has already read. |
| `action:high_error_rate` | over 30% of more than 5 tool calls errored | The agent could re-check its approach. |
| `output:filler_regression` | 3 of the last 5 text blocks grade C or D | The agent could drop preamble and narration. |
| `system:rate_limit_pressure` | 2 or more rate limits | Pause, or lower effort, until the window resets. |

Where they appear:

- **YOUI Web:** the sidebar shows the current signals (`youspeak_signals`), and each new suggestion appears once as a line in the chat (`youspeak_suggestion`), on both the Claude Code route and the Ollama/vLLM loop.
- **Terminal:** each new suggestion is printed once; `/youspeak` lists them.
- **Sovereign:** printed with each turn's status line.

What was removed in Y1 (an earlier agent added these; Yu did not choose them):

- The automatic effort cut in YOUI Web (both routes) and the terminal. It lowered effort from an account-wide figure, never restored it, and on the Claude Code route rewrote the cached prompt.
- `pruneContext`, which rewrote earlier tool results and thinking in place. It is gone from the kernel; nothing else used it.
- The L2 "sonnet may suffice" rule. Nothing reported thinking tokens, so it fired on every Opus session.

---

## 4. The store: one private row per session

**Where:** `~/.local/share/youi/youspeak/history.jsonl` (directory 0700, file 0600). `YOUSPEAK_HISTORY_PATH` overrides it, for tests. It is per user, outside any repository, and never committed. A YOUI Web test server (`YOUI_TEST=1`) writes no store unless `YOUSPEAK_HISTORY_PATH` is set.

**When a row is written:** once, when a YOUSPEAK session ends.

- **YOUI Web:** Clear (`/api/clear` or a session's clear), an agent switch, `/api/youspeak/reset`, a session closed, evicted or expired, 30 minutes without activity (the conversation goes on, and its next turn starts a new YOUSPEAK session), and server shutdown.
- **Terminal:** `/switch`, `/exit`, closing the input, or the process exiting.
- **Sovereign:** the end of each run.

Nothing is written per message.

**What is skipped:** a session with no output (no text and no tool calls), and, in YOUI Web and the terminal, one whose messages only failed. (Sovereign does not report failed runs separately, so only its no-output rule applies.)

**What a row holds** (schema 1), aggregates only:

- `schema`; `session`, a random id for this YOUSPEAK session, not the browser session's id
- `route`: `claude-code`, `ollama`, `vllm`, `terminal-legacy` or `sovereign`, the one most turns took; `routes`: turns per route
- `model`, `models`; `agent`
- `startedAt`, `endedAt`, `endReason`, `elapsed` (minutes)
- `turns`, and `exchanges`: how many messages ended with an answer or only an error
- `outputGrade` (`null` with no output), `usefulRatio`, `outputTokensEstimate`, `fillerTokensEstimate`, `textBlocks`
- `thinkingTokens`, `thinkAvgRatio`, `toolCalls`, `redundantReads`, `toolErrors`, `contextPeakTokens`
- `budgetBurned`, `rateLimitHits`
- `signalCount`, and `signals`: the names of the signals seen, such as `effort:reduce`

It never holds message text, file paths or tool inputs.

**How it is written:** each row is one line, written with one `O_APPEND` write. Concurrent writers never interleave inside a line, and nothing reads and rewrites the whole file. If the file ends in a partial line, the new row starts on a line of its own.

**A corrupt or partial store** is reported, never reset. Lines that cannot be read are skipped and counted, and the warning appears:

- on the YOUI page, under YOUSPEAK (`/api/youspeak` → `store.warnings`);
- in the terminal's `/youspeak`;
- in `node youspeak-kernel.mjs trends`;
- in every `youspeak-evolve.mjs` command.

The file itself is left as it is.

**The legacy file.** `youspeak-history.json` is tracked in git. Its 178 committed rows end on 2026-06-19 and are cumulative per-message snapshots with no session id, so one session appears many times. YOUI no longer writes it. It is read only, and shown separately as "legacy", never mixed into the store's trends. Whether to keep it in git is Yu's open question.

---

## 5. The ouroboros, proposal-only

`youspeak-evolve.mjs` reads the store, distils insights, and prints proposals. **It never writes source, config or history.**

```
node youspeak-evolve.mjs reflect     # trends across sessions (one row each)
node youspeak-evolve.mjs distill     # insights
node youspeak-evolve.mjs transmute   # insights, then proposed diffs in full (alias: propose)
node youspeak-evolve.mjs cycle       # reflect → distill → propose
node youspeak-evolve.mjs history     # the store's latest sessions, and the legacy record
```

How proposals appear:

- A change to the prompt rule is printed as a unified diff of `sovereign.mjs`'s YOUSPEAK Protocol block, with the full text that would be added or removed. A reviewer sees exactly what would change.
- Other proposals, such as pacing or narrower reads, are described in full. They would need a code change that someone writes and reviews on its own.
- To adopt any proposal, a person or agent edits the file, reviews it and commits it.

`integrate --apply` and `sense` are gone. The `mutations` record is no longer appended, and `youspeak-config.json` is no longer written.

The CI workflow `.github/workflows/ouroboros.yml` runs `trends`, `reflect`, `distill` and `transmute`, which now only read. It commits nothing. It also still curls two health endpoints, unchanged by Y1.

`youspeak-audit.mjs` stays read only. It reads `sovereign.log` and `.sovereign-state.json`, and it keeps its own filler patterns and grade scale.

### What the June 2026 "applied" entries did

On 2026-06-19, `integrate --apply` recorded three mutations as applied (commit `5986a93`):

- **`dynamic_effort` and `enable_pacing`** went into `youspeak-config.json` with an `appliedAt` date. **They never took effect: no code has ever read that file.** Both also came from broken inputs:
  - `dynamic_effort` came from the L2 ratio, which was always 0.
  - `dynamic_effort` passed its 5-session gate only because one session was counted many times.
- **`add_cache_reminder`** was spliced into `sovereign.mjs`'s YOUSPEAK Protocol block as the "File cache" line, and it is still there.
  - It reaches sovereign's prompt only, not YOUI Web's or the terminal's.
  - It fired only because the cumulative rows counted one session's repeated reads many times.

`youspeak-config.json` and the tracked history stay as historical records; their contents are not changed.

---

## 6. Using it

- **YOUI Web:** the YOUSPEAK panel in the sidebar. `GET /api/youspeak` gives the session's report, the store's trends, and `store` (row count, warnings and the legacy summary). `POST /api/youspeak/reset` ends the YOUSPEAK session (its row is written) and starts a new one.
- **Terminal:** `/youspeak` (or `/ys`).
- **Kernel CLI:** `node youspeak-kernel.mjs trends [n]`, read only.
- **In code:** `createKernel({ agent, route, model })`, the `sense*` functions, `decide()` for signals, `newSuggestions()` to show each once, and `endSession({ reason })` to write the row.

## 7. Next: Y2

Sensors on reported usage (input, cache read and creation, output, thinking, cost), each figure labelled reported or estimated. A signal when the cached system prompt changes, and the volatile lines (effort, date, branch, minds state) moved out of it. L1 kept as a labelled estimate, no longer graded. Minds and table tools reporting to the observer. One shared rule module. See [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md) §4.
