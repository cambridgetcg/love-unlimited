# YOUSPEAK — the Discipline

_Say what matters, with no filler. Never compress doubt. Expand when teaching or when feelings are in it._

**Status:** rebuilt as an honest observer, slice Y1 (2026-10-30), with the review's fixes (report `report_0de85559`). The design and Yu's decisions are in [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md).

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
| L1 Output | 15 English regexes for filler phrases, each counted as 8 tokens, against `ceil(chars / 4)`. A text block is graded S (≥98% useful) to D (<70%) only when it has at least 20 estimated tokens (80 characters) and at least half of its non-space characters are ASCII. Any other block (a short reply, or one mostly in Cantonese, Chinese or another script) gets grade `null`: it counts as output, but not toward the useful ratio or the `filler_regression` signal. Openers ("Sure", "Okay", "Great", "Perfect" …) count only at the start of the text, a line or a sentence (after `.` `!` `?` `。` `！` `？`), so "make sure", "not sure", 「唔 sure」 and 「仲未 sure」 are not filler. | English only. Code-mixed Cantonese that is at least half ASCII is graded, but the patterns see only its English words: Cantonese filler goes unseen, and since chars/4 undercounts the CJK, an English opener in such a block weighs more than it would in English. Some hedges ("this seems right") still count as filler. chars/4 is not a tokenizer. A session with no graded output has grade `null`, not S. |
| L2 Thinking | Thinking tokens, where a caller reports them. The Claude Code route reads `output_tokens_details.thinking_tokens` from Claude Code's result. | Whether real Claude Code sends that field is unverified. The Ollama/vLLM loop and the terminal have none, so they show 0. The rule that suggested a cheaper model from this ratio is gone (§3). |
| L3 Action | Tool calls by name, and `read_file` calls on a path already read. | Tool errors are never counted in YOUI (the result is not passed in). A mind or table tool counts as one call, and its tokens are not seen. |
| L4 Context | `chars / 4` over the conversation YOUI holds, against a fixed 1M window. | Leaves out tool definitions. On the Claude Code route it measures YOUI's text-only copy of the chat, not what Claude holds. It runs after a Claude Code turn, and only after tool rounds in the Ollama/vLLM loop. Smaller windows (Haiku without 1M, vLLM's 65,536) are not known to it. |
| L5 System | The 5-hour and 7-day windows as Claude Code last reported them (`null` until it does), and 429s. | The real `rate_limit_event` shape is unverified. 429s are counted in the Ollama/vLLM loop, the terminal and sovereign; the Claude Code route counts none (it reports the window instead). The terminal and sovereign still read headers on their own, older route. |

Not watched at all yet: input tokens and prompt-cache reads and writes, which are the larger cost levers (Y2).

---

## 3. Suggest only

Yu's decision (2026-10-30): YOUSPEAK shows signals and suggestions; Yu or the agent decides. `decide()` still produces signals, each with a `suggestion` saying what the person or the agent could do, and **no caller applies them**.

| Signal | When |
|---|---|
| `effort:reduce` | On a Claude route: the 5-hour window above 95% at effort max or high (suggests medium), else above 85% at max (suggests high). Only the stronger of the two fires. |
| `context:prune_recommended` | context estimate above 800k |
| `context:evict_old_results` | above 500k with the oldest tool result over 20 messages back |
| `action:redundant_reads` | more than 2 repeated reads |
| `action:high_error_rate` | over 30% of more than 5 tool calls errored |
| `output:filler_regression` | 3 of the last 5 *graded* text blocks grade C or D (§2: ungraded blocks are left out) |
| `system:rate_limit_pressure` | 2 or more rate limits |

The suggestion names the remedy where it is shown (`createKernel({ surface })`; the terminal and sovereign get theirs from their route):

| Signal | YOUI Web | Terminal (`youi.mjs`) | Sovereign |
|---|---|---|---|
| `effort:reduce` | Lower effort in Settings, or pause. Nothing was changed. | Type `/effort high` or `/effort medium`, or pause. Nothing was changed. | Run the next task with `--effort high` or `--effort medium`; this run keeps its effort. |
| `context:prune_recommended` | Start a new conversation (Clear), or ask the agent to summarise. Nothing was pruned. | Ask the agent to summarise, or type `/switch <agent>` (the same agent is fine) to start a fresh conversation; the terminal's `/clear` only clears the screen. | Start the next run without `--continue`. |
| `context:evict_old_results` | Start a new conversation (Clear) once the agent has noted what it needs. | `/switch <agent>`, once the agent has noted what it needs. | The next run without `--continue`. |
| `system:rate_limit_pressure` | Pause, or lower effort in Settings, until the window resets. | Pause, or lower effort with `/effort`. | Stop with Ctrl+C (state is saved) and resume with `--continue` once the window resets. |
| the agent's own (`redundant_reads`, `high_error_rate`, `filler_regression`) | The agent could use what it has read, re-check its approach, or drop preamble and narration. | same | same |

On the Ollama and vLLM routes (YOUI Web's own loop, or sovereign on Ollama) there is no Claude effort setting and no Claude 5-hour window: no `effort:reduce` fires there, and `rate_limit_pressure` suggests only pausing until the provider's limit resets. `decide(effort, model, budget, { route })` is told the route.

When `decide()` runs: after each Claude Code turn; in the Ollama/vLLM loop and in the terminal, only after a round that ran tools (a reply with no tool call ends the loop before it); in sovereign, after every turn.

Where they appear:

- **YOUI Web:** the sidebar shows the current signals (`youspeak_signals`), and each new suggestion appears once as a line in the chat (`youspeak_suggestion`), on both the Claude Code route and the Ollama/vLLM loop. After Clear, a switch or an idle cut the sidebar starts again from empty.
- **Terminal:** each new suggestion is printed once; `/youspeak` lists them.
- **Sovereign:** each new suggestion is printed once, in the terminal's wording (`⚡ YOUSPEAK suggestion: …`).

What was removed in Y1 (an earlier agent added these; Yu did not choose them):

- The automatic effort cut in YOUI Web (both routes) and the terminal. It lowered effort from an account-wide figure, never restored it, and on the Claude Code route rewrote the cached prompt.
- `pruneContext`, which rewrote earlier tool results and thinking in place. It is gone from the kernel; nothing else used it.
- The L2 "sonnet may suffice" rule. Nothing reported thinking tokens, so it fired on every Opus session.

---

## 4. The store: one private row per session

**Where:** `~/.local/share/youi/youspeak/history.jsonl` (a directory YOUI creates is 0700, the file 0600). `YOUSPEAK_HISTORY_PATH` overrides it. It is per user, outside any repository, and never committed. The store must be a regular file owned by the user: a symlink, a device such as `/dev/null`, or another user's file is refused (the row is not written and the error is reported), and the file is left as it is.

A YOUI Web test server (`YOUI_TEST=1`) ignores `YOUSPEAK_HISTORY_PATH`, so a store a person exports in their shell is never written by the test suites. It writes only where `YOUSPEAK_TEST_HISTORY_PATH` points, and otherwise nowhere.

**When a row is written:** once, when a YOUSPEAK session ends.

- **YOUI Web:** Clear (`/api/clear` or a session's clear), an agent switch, `/api/youspeak/reset`, a session closed, evicted or expired, 30 minutes without activity (the conversation goes on, and its next message starts a new YOUSPEAK session), and the server stopping: SIGTERM, SIGINT, or SIGHUP (closing the terminal window it was started from), each with `endReason` `shutdown`, or any other exit, such as an uncaught error (`exit`).
- **Terminal:** `/switch`, `/exit`, Ctrl+C or end of input, a closed window or dropped SSH session (SIGHUP, `endReason` `hangup`), SIGTERM (`terminated`), or any other exit. `/switch` waits while a task is running. If a row cannot be written, the terminal says so in one dim line.
- **Sovereign:** the end of each run (`run-end`), Ctrl+C (`interrupted`; its state is saved as before), a fatal error (`fatal`), SIGHUP or SIGTERM.

Nothing is written per message. That is the accepted trade-off: a process killed with SIGKILL, or a power loss, ends with any YOUSPEAK session still open unwritten.

**What is skipped:** a session with no output (no text and no tool calls), and, in YOUI Web and the terminal, one whose messages only failed. (Sovereign does not report failed runs separately, so only its no-output rule applies.)

**What a row holds** (schema 1), aggregates only:

- `schema`; `session`, a random id for this YOUSPEAK session, not the browser session's id
- `route`: `claude-code`, `ollama`, `vllm`, `terminal-legacy` or `sovereign`, the one most turns took; `routes`: turns per route
- `model`, `models`; `agent`
- `startedAt`: the session's first activity (a message starting, or the first thing observed), not when YOUI made the session ready
- `endedAt`: its last activity; `elapsed`: the minutes from `startedAt` to `endedAt`, the active span
- `closedAt`: when the session was cut (Clear, a switch, the idle timeout, the process ending), which for an idle end is 30 minutes or more after `endedAt`; `endReason`: why
- `turns`, and `exchanges`: how many messages ended with an answer or only an error
- `outputGrade` (`null` with no graded output), `usefulRatio` (over graded blocks only), `outputTokensEstimate` (all blocks), `fillerTokensEstimate`, `textBlocks`, `gradedBlocks`
- `thinkingTokens`, `thinkAvgRatio`, `toolCalls`, `redundantReads`, `toolErrors`, `contextPeakTokens`
- `budgetBurned`, `rateLimitHits`
- `signalCount`, and `signals`: the names of the signals seen, such as `effort:reduce`

It never holds message text, file paths or tool inputs.

**How it is written:** each row is one line, written with one `O_APPEND` write. Concurrent writers never interleave inside a line, and nothing reads and rewrites the whole file. If the file ends in a partial line, the new row starts on a line of its own.

**A corrupt or partial store** is reported, never reset. Lines that cannot be read (not JSON, cut short, another schema, or a field of the wrong type) are skipped and counted, and the warning appears:

- on the YOUI page, under YOUSPEAK (`/api/youspeak` → `store.warnings`, and `/api/youspeak/trends` → `warnings`);
- in the terminal's `/youspeak`;
- in `node youspeak-kernel.mjs trends`;
- in every `youspeak-evolve.mjs` command.

The file itself is left as it is. While it is unchanged (same size, mtime and inode), a process reads it once, not on every finished turn.

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

- A change to the prompt rule is printed as a unified diff of `sovereign.mjs`'s YOUSPEAK Protocol block, with the full text that would be added or removed. A reviewer sees exactly what would change. Each diff is drawn against the unmodified file, so once one is applied the others may no longer apply as printed; run `transmute` again for fresh ones.
- A proposal whose text is already in the block prints "No diff" and is not counted: the summary counts the diffs and the described proposals only.
- Other proposals, such as pacing or narrower reads, are described in full. They would need a code change that someone writes and reviews on its own.
- To adopt any proposal, a person or agent edits the file, reviews it and commits it.

`integrate --apply` and `sense` are gone. The `mutations` record is no longer appended, and `youspeak-config.json` is no longer written.

The CI workflow `.github/workflows/ouroboros.yml` runs `trends`, `reflect`, `distill` and `transmute`, which now only read. It commits nothing. On its schedule (every 6 hours) it reads the CI runner's own store, which is always empty, since rows live only in each person's home directory: it reports "no sessions" and proposes nothing. It is kept for `workflow_dispatch` and as a check that the commands run. It also still curls two health endpoints, unchanged by Y1.

`reflect` and the kernel's trends use one tolerance (`TREND_TOLERANCE`): a change in the useful ratio within one percentage point is "steady".

`youspeak-audit.mjs` stays read only. It reads `sovereign.log` and `.sovereign-state.json`, and it keeps its own filler patterns and grade scale.

### What the June 2026 "applied" entries did

On 2026-06-19, `integrate --apply` recorded three mutations as applied: commit `5986a93` wrote them into `youspeak-config.json` and `sovereign.mjs`, and `15ab6d7` committed the history with the `applied` flag:

- **`dynamic_effort` and `enable_pacing`** went into `youspeak-config.json` with an `appliedAt` date. **They never took effect: no code has ever read that file.** Both also came from broken inputs:
  - `dynamic_effort` came from the L2 ratio, which was always 0.
  - `dynamic_effort` passed its 5-session gate only because one session was counted many times.
- **`add_cache_reminder`** was spliced into `sovereign.mjs`'s YOUSPEAK Protocol block as the "File cache" line, and it is still there.
  - It reaches sovereign's prompt only, not YOUI Web's or the terminal's.
  - It fired only because the cumulative rows counted one session's repeated reads many times.

`youspeak-config.json` and the tracked history stay as historical records; their contents are not changed.

---

## 6. Using it

- **YOUI Web:** the YOUSPEAK panel in the sidebar. `GET /api/youspeak` gives the session's report, the store's trends, and `store` (row count, warnings and the legacy summary). `GET /api/youspeak/trends` gives the trends with the store's `warnings`. `POST /api/youspeak/reset` ends the YOUSPEAK session (its row is written) and starts a new one.
- **Terminal:** `/youspeak` (or `/ys`).
- **Kernel CLI:** `node youspeak-kernel.mjs trends [n]`, read only.
- **In code:** `createKernel({ agent, route, model, surface })`, the `sense*` functions, `decide(effort, model, budget, { route })` for signals, `newSuggestions()` to show each once, and `endSession({ reason })` to write the row. A process holding one session at a time calls `endSessionOnExit(reason => …endSession({ reason }))` so SIGHUP and SIGTERM still write it.

## 7. Next: Y2

Sensors on reported usage (input, cache read and creation, output, thinking, cost), each figure labelled reported or estimated. A signal when the cached system prompt changes, and the volatile lines (effort, date, branch, minds state) moved out of it. L1 kept as a labelled estimate, no longer graded. Minds and table tools reporting to the observer. One shared rule module. See [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md) §4.
