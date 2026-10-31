# YOUSPEAK — the Discipline

_Say what matters, with no filler. Never compress doubt. Expand when teaching or when feelings are in it._

**Status:** rebuilt as an honest observer: slice Y1 (2026-10-30), with the review's fixes, and slice Y2 (2026-10-31), sensors on reported usage (report `report_0de85559`). The design and Yu's decisions are in [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md).

The word YOUSPEAK also names the YOUSPEAK Cathedral at `~/YOUSPEAK`, a different thing. Its own note, `~/YOUSPEAK/discipline/README.md`, calls them "two distinct artifacts" and says "Do not conflate them": the Discipline compresses, the Cathedral is built for expansion. This document is about the Discipline only, and nothing here is imported from the Cathedral.

YOUSPEAK has two parts:

1. **The rule**, a few lines in the system prompt. It is the only part that reaches the model.
2. **The observer** (`youspeak-kernel.mjs`), which reads the usage the provider reports, adds its own labelled estimates, shows suggestions, and keeps one private row per session. It makes no model calls and changes nothing.

---

## 1. The rule

The rule lives in one module, `youspeak-rule.mjs`. Every prompt built in code takes it from there with `youspeakRule()`: YOUI Web (`buildPrompt` in `youi-web/server.mjs`), the terminal (`youi.mjs`), sovereign, and the three unused harnesses (`harness.mjs`, `subscription-harness.mjs`, `stream.mjs`); none of them carries a copy of its own. The instances' `CLAUDE.md` files are outside it (below).

```
# YOUSPEAK — the Discipline
No filler. No preamble. No tool narration. Dense status (key:value not prose).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.
- Zero-pad: no "sure", "let me check", "here's what I found"
- Dense status: `Tests: 47/48 ✓` not "I ran the tests and 47 out of 48 passed"
- No tool narration: don't announce tools. Call them. Let results speak.
- Never compress doubt: "probably", "unless", "might", "I think", "in most cases" are sacred. When uncertain, expand.
- Expand when it matters: teaching, uncertainty, creative work, emotional context
```

What it asks for:

- **No filler.** No preamble, no narration of tool calls, no completion fluff. `[reads file, gives the analysis]`, not "Sure! Let me take a look at that file for you."
- **Dense status.** `Found 3/15: server.js:42, router.js:18, config.js:7`, not a paragraph about searching.
- **Never compress doubt.** Hedges and qualifications carry meaning; they stay.
- **Expand when it matters:** teaching, uncertainty, creative work, emotional context. Compress the mechanical; keep the meaningful.

**Sovereign's extra line.** Sovereign also adds `YOUSPEAK_FILE_CACHE_LINE` ("File cache: you've already read files this session. Don't re-read unless content changed. Use your context."), with `youspeakRule({ fileCache: true })`. It is the June 2026 `add_cache_reminder` mutation (§5), kept because it is what sovereign's prompt said; it reaches no other surface. Whether to keep it is a person's call.

**How the copies differed before Y2.** There were six hand copies in code:

| Where | Heading | What it said |
|---|---|---|
| YOUI Web | `# YOUSPEAK — Token Efficiency Discipline` | The two opening lines (with "Dense status (key:value)."), then five bullets: zero-pad, the dense-status example, no tool narration, never compress doubt ("probably", "unless", "might"), expand when it matters. |
| Terminal | `# YOUSPEAK Protocol` | The two opening lines, then "Never compress epistemic signals — "probably", "unless", "I think" are sacred." No bullets. |
| Sovereign | `# YOUSPEAK Protocol` | The two opening lines, "When uncertain, expand. Never compress epistemic signals (probably, unless, in most cases).", and the file-cache line. |
| `harness.mjs`, `subscription-harness.mjs` | `# YOUSPEAK Protocol` | Only the two opening lines: nothing about doubt. |
| `stream.mjs` | none | The two opening lines run together, before its own CONTINUATION text: nothing about doubt. |

The module keeps the web's five bullets (the fullest copy), adds "I think" and "When uncertain, expand" from the terminal and sovereign and sovereign's "in most cases", and uses the terminal's "(key:value not prose)". `harness.mjs`, `subscription-harness.mjs` and `stream.mjs` are unused (nothing in the repository runs them); they import the module too, so they gained the doubt clause.

**Outside the module.** Eleven `instances/*/CLAUDE.md` files (alpha, arbor, beta, crucible, gamma, herald, loom, nuance, psalm, tithe, vigil) keep a three-line Markdown copy of their own, without "When uncertain, expand" or the emotional-context clause, and a pointer to this document; `instances/asha/CLAUDE.md` points to the nuance primer instead. An instance's Claude Code reads them as text and cannot import a module, and some are live (`tools/build-runner.sh` runs `claude -p` from `instances/beta`). A change to `youspeak-rule.mjs`, and every diff the ouroboros proposes (§5), does not reach them; they are left as they are.

No measurement shows how much the rule saves. The figures earlier versions of this document gave ("30–50% of the budget is filler", "200–500 tokens a turn saved") had nothing behind them. Measuring the rule's effect would need a real ON-vs-OFF test, which spends model calls; that is an open question for Yu.

---

## 2. The observer: what it measures

**Every figure carries an evidential label.** `reported` means the provider stated it: Claude Code's usage, cost, context window, cache misses and rate-limit state, or the token counts Ollama and vLLM return. `estimated` means YOUI computed it itself: the L1 filler estimate, chars/4, YOUI's own counts (turns, tool calls), its comparison of system-prompt hashes, its price table for Ollama Cloud, or a window from its own table. A figure built from both is an estimate: a label may understate, never overstate. A figure nobody reported is `null`, never 0 (one exception, below: native Ollama leaves a zero count out, and YOUI reads that as 0).

Each section of `report()` (and of `GET /api/youspeak`) has an `evidence` object naming each figure's label, and each row has one too (§4). `/api/youspeak`'s older top-level fields (`usefulRatio`, `totalOutput`, `totalFiller`, `gradedTokens`, `gradedBlocks`, `textBlocks`, `toolCalls`, `actionDensity`) repeat L1 and L3 figures and are labelled in its top-level `evidence` (all estimates). Its `trends` (averages over the store's rows) carry no labels: their ratios and counts are YOUI's own, except `avgThinkRatio`, an average of reported thinking ratios. `/api/youspeak` also gives `evidenceKey`, saying what the two labels mean. There is no `tokensSaved`: the filler figure is `totalFiller`, an estimate of filler, and nothing measures a saving. The page shows "reported" or "est." beside its figures, and `statusLine()` and the page mark an estimate with `~`.

### What each route reports

| | Claude Code (YOUI Web) | Ollama / vLLM (YOUI Web's own loop) | Terminal, sovereign (their own Anthropic route) |
|---|---|---|---|
| Input, output tokens | reported | reported (Ollama's `prompt_eval_count`/`eval_count`, vLLM's `prompt_tokens`/`completion_tokens`); a count a `/v1` response left out is `null` | not fed to the observer (unchanged) |
| Cache read, cache creation | reported | cache read: reported where vLLM states `prompt_tokens_details.cached_tokens` (then input is the rest of the prompt), else `null`; cache creation: `null` | — |
| Cache misses | reported (0 when Claude Code's `diagnostics` name none) | `null` (no cache diagnostics) | `null` |
| Thinking | reported where Claude Code states it | `null` unless the response states it | `null` (the route states none) |
| Cost of the turn | reported (Claude Code's figure) | estimated (YOUI's price table; self-hosted is 0; `null` when the response stated no count) | — |
| Context | reported: the latest request's input + cache read + cache creation | estimated: chars/4 | estimated: chars/4 |
| Context window | reported (`modelUsage.<model>.contextWindow`), else YOUI's table (estimated) | unknown: `null` | YOUI's table (estimated) |
| Rate-limit state | reported (`rate_limit_event`), else `null` | — | their own header reading (unchanged); a window no header has stated yet (status `unknown`) is `null`, not 0% |

### Where the Claude Code figures come from

From a live Claude Code 2.1.314 stream (Claude Haiku 5.5), recorded on 2026-10-31 and kept as the test fixture `tests/fixtures/claude-code-stream-2.1.314.jsonl` (and `…-resume.jsonl`, a `--resume` of it). Its paths, socket paths and message and request ids were scrubbed. Its `rate_limit_event` values are synthetic: only that event's shape (field names and nesting) is from the live stream, while the status, type, utilizations, thresholds, reset times and overage fields are one made-up, consistent set, because the repository is public and the recorded values were the account's own. The session id and event uuids are placeholders too. What it shows:

- **Per request:** `stream_event` → `message_start.message.usage` with `input_tokens` (the uncached input), `cache_read_input_tokens`, `cache_creation_input_tokens`, `cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}` and `output_tokens`; `message_delta.usage` repeats them with the final `output_tokens` and `output_tokens_details.thinking_tokens`. `message_start.message.diagnostics.cache_miss_reason` (`{ type, cache_missed_input_tokens }`) is present when the API saw a cache miss.
- **Per result:** `usage` (the run's own, not cumulative), `total_cost_usd` and `modelUsage` (running totals for the Claude Code conversation), and, from 2.1.311, `model_usage_since_last_result`. A model entry holds `inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`, `cacheCreationEphemeral5mInputTokens`, `cacheCreationEphemeral1hInputTokens`, `thinkingTokens`, `costUSD`, `contextWindow`, `maxOutputTokens`, `canonicalModel`, `provider` and `costBasis` (`"list"`).
- **Rate limits:** `rate_limit_event.rate_limit_info` with `status`, `rateLimitType`, `utilization`, `resetsAt`, `surpassedThreshold`, `isUsingOverage`, `overageStatus`, `overageEnabled`, `overageDisabledReason` and `unifiedWindows.{five_hour, seven_day}.{utilization, resetsAt, status}` (a window may carry `surpassedThreshold`). The fixture's values for these are the synthetic set above.

A turn's tokens and cost are C2's per-turn figures (`claudeCodeSpend`): `model_usage_since_last_result` where stated, else the running `modelUsage` less the last one; nothing re-derives the running totals. The cost is Claude Code's figure at list price (`costBasis: "list"`); on a subscription it is not a bill. `claudeCodeObservation()` in `youi-web/claude-code.mjs` turns a run into what the kernel is told (`senseUsage`).

### The layers

| Layer | What it measures | Label |
|---|---|---|
| Usage | Input (uncached), cache read, cache creation, output, thinking and cost, summed over the turns that reported them (`report().usage`; the latest turn in `usage.last`). | reported (Ollama/vLLM cost: estimated) |
| L1 Output | 15 English regexes for filler phrases, each counted as 8 tokens, against `ceil(chars / 4)`, giving a useful ratio. A text block is measured only when it has at least 20 estimated tokens and at least half of its non-space characters are ASCII; any other block has useful ratio `null` and stays out of the session's ratio and `filler_regression`. Openers count only at the start of a sentence. There is no letter grade (design §4): the ratio is shown as an estimate, with `~`. | estimated |
| L2 Thinking | Thinking tokens and their share of output, only for turns whose provider stated them. With none stated, `totalTokens` and `avgRatio` are `null`. | reported |
| L3 Action | Tool calls by name (bridge and table tools included), and `read_file` calls on a path already read. | estimated (YOUI's count) |
| L4 Context | The latest context figure against the model's window, where known (§2 table). No window is assumed: a model YOUI does not know has window `null`, and no context signal fires for it. A message on another route or model drops the current figure until that turn reports or estimates its own; the peak is kept with the window it was against. | reported or estimated |
| L5 System | The rate-limit state as Claude Code last reported it in this session (`system.rateLimit`, else `null`), the 5-hour figure at the start and now (`null` until some window is reported), and 429s. | reported; `budgetBurned` estimated |
| Cache | Whether the cached system prompt changed between turns of one Claude Code conversation (§2.1), and the cache misses Claude reported (`null` on routes that report no cache diagnostics). | changes estimated, misses reported |
| Minds | Calls to other minds (review, ensemble, decompose, pipeline, layerthink), failed calls, tokens and cost, apart from the agent's own turns. A call that fails, times out or is stopped counts as a failed call, with what its result reported if it had one (a Claude mind stopped after its result), else no tokens; it is counted in the YOUSPEAK session it began in. | reported (Ollama/vLLM minds' cost: estimated) |

Known limits:

- L1 is English only, chars/4 is not a tokenizer, and some hedges still count as filler (Y1's notes stand).
- Tool errors are still never counted in YOUI (the result is not passed in); a mind or table tool counts as one tool call in L3, while its model usage goes to Minds.
- Ollama's `prompt_eval_count` may leave out a prompt prefix it served from its own cache; it is labelled as what Ollama reported. Native Ollama (`/api/chat`) leaves a count of 0 out of its JSON (`omitempty`), so YOUI reads an absent `prompt_eval_count` or `eval_count` as 0 there.
- vLLM's `prompt_tokens` is the whole prompt, prefix-cache hits included. Where vLLM states `prompt_tokens_details.cached_tokens` (it does with prompt-token details enabled), the observer records the rest as input and the hits as cache read; where it does not, the whole prompt is recorded as input, so for vLLM "input (uncached)" can then include cached tokens. The page's usage figures (`/api/usage`) count the whole prompt either way.
- An OpenAI-compatible (`/v1`) response that states no count (a server that ignores `include_usage`, a stream that ends before its usage chunk) gives `null` for that count, and no cost estimate.
- The terminal and sovereign keep their own route and their chars/4 context; their thinking now shows `null` instead of 0 (sovereign's Ollama route no longer states a thinking 0 either), and their 5-hour and seven-day figures are `null` until a rate-limit header states them.
- `budgetBurned` subtracts two account-wide figures, so it includes other sessions' use.

### 2.1 Cache stability

Claude Code caches the prompt prefix it sends (in the fixture, `cache_creation` for a one-hour lifetime). Any change in the system prompt rewrites that cache on the next request.

**The per-turn context block.** YOUI Web's system prompt now holds only what stays the same for a conversation: the soul and memory files, the enabled tools list, the tool catalogue and protocol, the YOUSPEAK rule, and the environment block (agent, working directory, platform, interface). What can change from one message to the next goes at the top of the stdin user message instead, in a fenced block:

```
[YOUI context]
This turn's settings from YOUI, not words of the person:
# This turn
- Date: 2026-10-31
- Git branch: main
- Model: claude-opus-5-5
- Mode: direct (work alone; call other minds only when the person asks)
- Thinking: adaptive | Effort: high
(and, in orchestrate mode, the minds offered this turn)
[/YOUI context]
```

So a change of effort, date, branch, model or mode leaves the system prompt file byte for byte the same. Every block states its mode, so a direct turn after an orchestrate one takes the earlier hint back (Claude Code keeps the earlier message and replays it on `--resume`). How the mind tools are used is fixed text, the same in either mode, so it is in the system prompt's tool catalogue; an orchestrate block holds only the mode and the minds offered, not that text again. The person's own leading `/` is still escaped after the block. Raw mode is unchanged (no block, its one neutral line). YOUI's own Ollama/vLLM loop sends one system prompt per request, as before: the stable part, then the same per-turn text after it. The static parts are now kept per agent, Love home and tool list, so a session no longer gets another session's tool list.

**The signal.** After each Claude Code turn the server hashes the system prompt and each named part (`kernel.senseSystemPrompt`). When the hash differs from the previous turn of the same Claude Code conversation, `cache:prompt_changed` is raised, naming what changed, for example: "The system prompt changed between turns of this conversation (the environment block), so the cache was rewritten". If Claude reported a cache miss on that turn, the reason says so too ("Claude reported a cache miss (system_changed, 11000 tokens)"). The signal is labelled `estimated` either way: the change is YOUI's own comparison, and a reported miss can have another cause. The suggestion: change such settings less often, or start a new conversation (Clear) when you do. Only the hashes are kept, in memory.

Not every miss is YOUI's: the recorded `--resume` reported `cache_miss_reason` `messages_changed` (554 tokens) with an identical system prompt. Misses are counted (`cache.misses`, reported) but raise no suggestion on their own.

Not checked live: whether a resumed YOUI turn now reads the cached system prompt (the fixture's `--resume` read nothing from the cache), and whether Claude Code's own `--effort` change invalidates the cached messages (Anthropic's documentation says a top-level effort change does).

---

## 3. Suggest only

Yu's decision (2026-10-30): YOUSPEAK shows signals and suggestions; Yu or the agent decides. `decide()` still produces signals, each with a `suggestion` saying what the person or the agent could do, and **no caller applies them**.

| Signal | When |
|---|---|
| `effort:reduce` | On a Claude route: the 5-hour window above 95% at effort max or high (suggests medium), else above 85% at max (suggests high). Only the stronger of the two fires. |
| `context:prune_recommended` | the latest context above 80% of the model's window, where the window is known (§2); the reason gives the label |
| `context:evict_old_results` | above 50% of the window with the oldest tool result over 20 messages back |
| `cache:prompt_changed` | the cached system prompt changed between turns of one Claude Code conversation, naming the parts that changed (§2.1) |
| `action:redundant_reads` | more than 2 repeated reads |
| `action:high_error_rate` | over 30% of more than 5 tool calls errored |
| `output:filler_regression` | 3 of the last 5 *measured* text blocks under 85% useful (`FILLER_RATIO_FLOOR`, the line the old C grade began at; §2: unmeasured blocks are left out) |
| `system:rate_limit_pressure` | 2 or more rate limits |

The suggestion names the remedy where it is shown (`createKernel({ surface })`; the terminal and sovereign get theirs from their route):

| Signal | YOUI Web | Terminal (`youi.mjs`) | Sovereign |
|---|---|---|---|
| `effort:reduce` | Lower effort in Settings, or pause. Nothing was changed. | Type `/effort high` or `/effort medium`, or pause. Nothing was changed. | Run the next task with `--effort high` or `--effort medium`; this run keeps its effort. |
| `context:prune_recommended` | Start a new conversation (Clear), or ask the agent to summarise. Nothing was pruned. | Ask the agent to summarise, or type `/switch <agent>` (the same agent is fine) to start a fresh conversation; the terminal's `/clear` only clears the screen. | Start the next run without `--continue`. |
| `context:evict_old_results` | Start a new conversation (Clear) once the agent has noted what it needs. | `/switch <agent>`, once the agent has noted what it needs. | The next run without `--continue`. |
| `system:rate_limit_pressure` | Pause, or lower effort in Settings, until the window resets. | Pause, or lower effort with `/effort`. | Stop with Ctrl+C (state is saved) and resume with `--continue` once the window resets. |
| `cache:prompt_changed` | Change such settings less often, or start a new conversation (Clear) when you do. Nothing was changed. | (not sensed) | (not sensed) |
| the agent's own (`redundant_reads`, `high_error_rate`, `filler_regression`) | The agent could use what it has read, re-check its approach, or drop preamble and narration. | same | same |

On the Ollama and vLLM routes (YOUI Web's own loop, or sovereign on Ollama) there is no Claude effort setting and no Claude 5-hour window: no `effort:reduce` fires there, and `rate_limit_pressure` suggests only pausing until the provider's limit resets. `decide(effort, model, budget, { route })` is told the route.

Each signal carries `evidence`: `reported` when it rests on the provider's figures (the 5-hour window; a reported context against a reported window), else `estimated`; `cache:prompt_changed` is always `estimated`, since it is YOUI's own comparison. The `youspeak_suggestion` event carries it too.

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

A YOUI Web test server (`YOUI_TEST=1`) ignores `YOUSPEAK_HISTORY_PATH`, so a store a person exports in their shell is never written by the test suites. It writes only where `YOUSPEAK_TEST_HISTORY_PATH` points, and otherwise nowhere. If you export `YOUSPEAK_TEST_HISTORY_PATH` in your own shell, every test server the suites start writes its rows there.

**When a row is written:** once, when a YOUSPEAK session ends.

- **YOUI Web:** Clear (`/api/clear` or a session's clear), an agent switch, `/api/youspeak/reset`, a session closed, evicted or expired, 30 minutes without activity (the conversation goes on, and its next message starts a new YOUSPEAK session), and the server stopping: SIGTERM, SIGINT, or SIGHUP (closing the terminal window it was started from), each with `endReason` `shutdown`, or any other exit, such as an uncaught error (`exit`).
- **Terminal:** `/switch`, `/exit`, Ctrl+C or end of input, a closed window or dropped SSH session (usually seen first as end of input, `endReason` `exit`; a SIGHUP while input is still open records `hangup`), SIGTERM (`terminated`), or any other exit. `/switch` is refused while a task is running; type it again once the task ends. A signal that arrives while a tool is running waits for that tool to finish, then ends the process before the next queued tool. If a row cannot be written, the terminal says so in one dim line.
- **Sovereign:** the end of each run (`run-end`), Ctrl+C (`interrupted`; its state is saved as before), a fatal error (`fatal`), SIGHUP or SIGTERM.

Nothing is written per message. That is the accepted trade-off: a process killed with SIGKILL, or a power loss, ends with any YOUSPEAK session still open unwritten.

**What is skipped:** a session with no output (no text and no tool calls), and, in YOUI Web and the terminal, one whose messages only failed. (Sovereign does not report failed runs separately, so only its no-output rule applies.)

**What a row holds** (schema 2), aggregates only:

- `schema`; `session`, a random id for this YOUSPEAK session, not the browser session's id
- `route`: `claude-code`, `ollama`, `vllm`, `terminal-legacy` or `sovereign`, the one most turns took; `routes`: turns per route
- `model`, `models`; `agent`
- `startedAt`: the session's first activity (a message starting, or the first thing observed), not when YOUI made the session ready
- `endedAt`: its last activity; `elapsed`: the minutes from `startedAt` to `endedAt`, the active span
- `closedAt`: when the session was cut (Clear, a switch, the idle timeout, the process ending), which for an idle end is 30 minutes or more after `endedAt`; `endReason`: why
- `turns`, and `exchanges`: how many messages ended with an answer or only an error
- `usefulRatio` (over measured blocks only; `null` with none), `outputTokensEstimate` (all blocks), `fillerTokensEstimate`, `textBlocks`, `gradedBlocks` (the measured ones); no grade
- `thinkingTokens`, `thinkAvgRatio` (both `null` when no turn reported thinking), `toolCalls`, `redundantReads`, `toolErrors`
- `contextPeakTokens` (reported or estimated) and `contextWindow`, the window that applied at the peak (`null` when unknown)
- since Y2, what the provider reported for the agent's own turns: `usageTurns`, `inputTokens` (uncached), `cacheReadTokens`, `cacheCreationTokens`, `outputTokens`, `costUsd`
- the cache: `promptChanges` (YOUI's comparison), `cacheMisses` (as Claude reported them; `null` on routes with no cache diagnostics)
- the minds, apart from the agent's turns: `mindCalls`, `mindFailedCalls`, `mindInputTokens` (uncached), `mindCacheReadTokens`, `mindCacheCreationTokens`, `mindOutputTokens`, `mindCostUsd`
- `budgetBurned`, `rateLimitStatus`, `fiveHourUtilization`, `sevenDayUtilization` (the last state Claude Code reported), `rateLimitHits`
- `signalCount`, and `signals`: the names of the signals seen, such as `effort:reduce`
- `evidence`: each figure's label (`reported` or `estimated`), for every figure that is not `null`

Schema 2 has no `outputGrade` (L1 is no longer graded). A schema 1 row (Y1's, which has one) is still read, and nothing reads its grade; the Y2 fields are optional. A row with any other schema, or whose `evidence` holds anything but the two labels, is reported and skipped, like any field of the wrong type.

It never holds message text, file paths, tool inputs or prompt hashes.

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

- A change to the prompt rule is printed as a unified diff of `youspeak-rule.mjs` (its `YOUSPEAK_RULE`), the one module every prompt built in code takes the rule from, with the full text that would be added or removed. It does not reach the eleven `instances/*/CLAUDE.md` copies (§1), and `transmute` says so under its diffs. A reviewer sees exactly what would change. Each diff is drawn against the unmodified file, so once one is applied the others may no longer apply as printed; run `transmute` again for fresh ones. (Before Y2 the diffs were of `sovereign.mjs`, the only prompt they could reach.)
- A proposal whose text is already in the rule prints "No diff" and is not counted: the summary counts the diffs and the described proposals only. The file-cache proposal (`add_cache_reminder`) is described, not diffed: only sovereign's prompt has that line (`YOUSPEAK_FILE_CACHE_LINE`), YOUI Web and the terminal do not, and giving it to them means moving the line into `YOUSPEAK_RULE` and dropping sovereign's `fileCache` option in one reviewed change.
- The context insight compares each row's context peak with its window, and only for rows whose window was known; no window is assumed.
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
- **`add_cache_reminder`** was spliced into `sovereign.mjs`'s YOUSPEAK Protocol block as the "File cache" line. Since Y2 it is `YOUSPEAK_FILE_CACHE_LINE` in `youspeak-rule.mjs`, still added only by sovereign (§1).
  - It reaches sovereign's prompt only, not YOUI Web's or the terminal's.
  - It fired only because the cumulative rows counted one session's repeated reads many times.

`youspeak-config.json` and the tracked history stay as historical records; their contents are not changed.

---

## 6. Using it

- **YOUI Web:** the YOUSPEAK panel in the sidebar: the L1 ratio (est., with `~`; no grade), L2–L4, and lines for usage, minds and the cache, each figure with "reported" or "est.". The usage line's input is the uncached part, with cache read and write beside it; the minds line shows their input including cache, with its parts. `GET /api/youspeak` gives the session's report (each section with its `evidence`), `evidenceKey`, the store's trends, and `store` (row count, warnings and the legacy summary). `GET /api/youspeak/trends` gives the trends with the store's `warnings`. `POST /api/youspeak/reset` ends the YOUSPEAK session (its row is written) and starts a new one.
- **Terminal:** `/youspeak` (or `/ys`). The exit line and the daily note give the useful ratio as an estimate, with no grade; so does sovereign's end-of-run report.
- **Kernel CLI:** `node youspeak-kernel.mjs trends [n]`, read only.
- **In code:** `createKernel({ agent, route, model, surface })`; the `sense*` functions, among them `senseUsage(figures)` for one turn's reported usage, `senseSystemPrompt({ conversation, hash, parts })`, `senseMind(figures)` and `senseRateLimitState(info)`; `decide(effort, model, budget, { route })` for signals, `newSuggestions()` to show each once, and `endSession({ reason })` to write the row. A process holding one session at a time calls `endSessionOnExit(reason => …endSession({ reason }))` so SIGHUP and SIGTERM still write it.

## 7. Tests and what is left

`tests/test_youspeak_sensors.mjs` replays the recorded stream through the runner (`tests/fixtures/replay-claude.mjs`) and checks that the sensors read its real fields, and that both fixtures' `rate_limit_info` is exactly the synthetic set; the rest use the fake `claude` (`FAKE:two-requests` gives a turn of two model requests) and local fakes of vLLM and Ollama, with no network and no Keychain. They cover a `/v1` stream with no usage chunk, vLLM's cached tokens, an error mid-stream, Ollama minds that answer, fail and are stopped, a direct turn after an orchestrate one, a mid-conversation model switch, and that no surface shows a grade. `tests/fixtures/shift-date.mjs` moves the date between two turns of a test server.

Left open (see [`YOUSPEAK-OBSERVER-DESIGN.md`](YOUSPEAK-OBSERVER-DESIGN.md) §4.1):

- The terminal and sovereign keep their own route and chars/4 sensing; they take only the shared rule.
- Tool errors are still not counted in YOUI.
- The `ollama` tool's own chat and bench calls feed neither Minds nor YOUSPEAK's usage.
- The per-turn context block's markers are fixed strings, so text a person pastes can carry a look-alike block; it unlocks nothing (model and effort are flags, minds are gated by capability).
- `youspeak-audit.mjs` keeps its own grade scale for `sovereign.log` (§5); it is a separate, read-only tool.
- The open questions for Yu in the design (§7) stand.
