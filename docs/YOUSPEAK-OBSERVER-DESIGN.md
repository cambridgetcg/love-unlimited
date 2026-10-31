# YOUSPEAK, rebuilt as an honest observer

**Status:** agreed by Yu with Touch ID on 2026-10-30 (proposal `report_0de85559`). Slice Y1 (§3) is built on branch `feat/youi-youspeak-y1-20261030`, with the fixes from its adversarial review (§3.1). Slice Y2 (§4) is built on branch `feat/youi-youspeak-y2-20261031`, stacked on Y1 (§4.1). [`YOUSPEAK.md`](YOUSPEAK.md) describes the code after both.

**Date:** 2026-10-30

**Branch:** `feat/youi-youspeak-y1-20261030` (from the Claude Code route, C2 `dea8368`)

YOUSPEAK in YOUI is the Discipline: say what matters with no filler, never compress doubt, and expand when teaching or when feelings are in it. It is not the YOUSPEAK Cathedral at `~/YOUSPEAK`. The Cathedral's `discipline/README.md` says the word names "two distinct artifacts" and "Do not conflate them", and they point opposite ways: the Discipline compresses, while the Cathedral's `THE-PATH.md` says it "is engineered for expansion, not reduction". This design keeps the Discipline's concept and rebuilds the mechanics behind it.

## 1. Why: what a 49-agent deep dive found (2026-10-30)

Six readers, a merge, two adversarial verifiers per issue and a completeness pass covered 52 issues. Of the 18 verified: 14 confirmed, 2 contested, 2 refuted.

**What works**
- The prompt rule is the only part that reaches the model, and it is sound. It protects hedging and has an escape for emotional and creative work.
- The kernel makes no model calls.
- Persisted rows hold aggregates only.

**What does not**
- **The kernel acts on its own.**
  - It lowers Alpha's effort when the account-wide 5-hour use passes 95%, and never restores it (at `dea8368`: `youi-web/server.mjs:2046-2048` on the Claude Code route and `:3824-3826` in the Ollama/vLLM loop, `youi.mjs:1301-1304`).
  - It rewrites earlier history in place (at `dea8368`: `youi-web/server.mjs:3829-3833`, `youi.mjs:1305-1311`). On Claude 5-series models this risks the preserved-thinking check.
  - An earlier agent added both; Yu did not choose them.
- **A tracked file is written after every message.** `youspeak-history.json` is git-tracked in a likely public repository and its location depends on the working directory.
  - Rows are cumulative snapshots with no session id, so trends and the ouroboros count one session many times.
  - Writes are not atomic, and a corrupt file is silently reset.
- **Several sensors measure nothing or mislead.**
  - L2 reads `usage.thinking_tokens`, which nothing produces, so Opus always gets a false "sonnet may suffice".
  - L1 is 15 English regexes over a chars/4 estimate. It cannot see Cantonese, it penalises hedges, and any reply under 10 characters turns the session into NaN, pinned at 100% / S.
  - L4 assumes a 1M window and runs only after tool rounds.
- **The real cost levers go unwatched.** YOUSPEAK ignores input tokens and prompt-cache stability, and its own effort cut breaks the cache.
- **The ouroboros changed nothing.** `youspeak-evolve.mjs integrate --apply` splices text from a runtime data file into `sovereign.mjs` behind a preview that hides the payload. `youspeak-config.json` has no reader, so the June 2026 "applied" changes never took effect.
- **Minds and table tools bypass the kernel entirely.**

## 2. Yu's decisions (2026-10-30)

| Question | Yu's choice |
|---|---|
| Should YOUSPEAK ever act by itself? | Suggest only. It shows signals and suggestions; Yu or Alpha decides. |
| How to rebuild | B: an honest observer, in two steps. |
| Where history lives | An untracked per-user store, written once per session. |
| The ouroboros | Proposal-only. It prints diffs for review and never writes source or config. |

## 3. Step 1 (slice Y1): safety

- **Suggest only.**
  - `decide()` returns signals; no caller applies them. The automatic effort cut and `pruneContext` calls are removed from YOUI Web and the terminal.
  - A suggestion appears as a line in the chat; the person or Alpha acts on it.
  - The L2 "sonnet may suffice" rule is dropped.
- **History.**
  - One row per session, written when the session ends, is cleared, or goes idle for 30 minutes.
  - Each row carries a session id, the route (Claude Code, Ollama, vLLM or the terminal's legacy route) and the model.
  - Rows are appended to `~/.local/share/youi/youspeak/history.jsonl` (directory 0700, file 0600) with one atomic append per row.
  - Empty and failed sessions are not written; a session with no output has grade `null`, not S.
  - YOUI stops writing the tracked `youspeak-history.json` and leaves its 178 rows as a historical record, for Yu to decide about.
- **Bugs.**
  - Short text no longer produces NaN.
  - A corrupt store is reported, never reset.
  - The dead `<soulDir>/YOUSPEAK.md` include is removed.
- **Ouroboros, proposal-only.**
  - `youspeak-evolve.mjs` reads the new store and prints proposed diffs with their full text.
  - It never writes source, config or history; `--apply` is removed.
  - `youspeak-config.json` is no longer written. `docs/YOUSPEAK.md` records that the June "applied" entries never took effect, and why.
- **Docs.** `docs/YOUSPEAK.md` is rewritten to match the code, and points to the Cathedral's "do not conflate" note.

### 3.1 As built, after review

An adversarial review of Y1 (2026-10-30, two skeptics per finding) found six must-fix issues and a set of smaller ones. All are fixed on this branch, except where noted:

- **Rows on every ordinary exit.** The terminal and sovereign write their row on SIGHUP and SIGTERM too (`endSessionOnExit`: exit 129 or 143, so the exit hook runs); sovereign also on Ctrl+C (`interrupted`) and a fatal error. YOUI Web adds SIGHUP to its shutdown signals and an exit hook for uncaught errors. SIGKILL and power loss still lose an open session: nothing is written per message, by design.
- **Honest times.** `startedAt` is the first activity, `endedAt` the last, `elapsed` the active span; the cut is `closedAt`. An idle session's row no longer counts the 30 silent minutes.
- **L1 grades less, and more fairly.** Openers count only at the start of a sentence, so "唔 sure" and "not sure" are not filler. A block under 20 estimated tokens, or mostly non-ASCII, gets grade `null` and stays out of the ratio and of `filler_regression`.
- **Remedies per surface.** The terminal's suggestions name `/effort` and `/switch` (its `/clear` only clears the screen); sovereign's name `--effort` and `--continue`. No effort suggestion on the Ollama/vLLM routes, and only the stronger of the two effort suggestions fires.
- **Smaller fixes.** Rows with mistyped fields are reported and skipped; the store must be a regular file of the user's (`O_NOFOLLOW`); reads are cached while the file is unchanged; a test server ignores `YOUSPEAK_HISTORY_PATH` (`YOUSPEAK_TEST_HISTORY_PATH` instead); the terminal says when a row fails and refuses `/switch` mid-task; sovereign's rows name agent `sovereign` and print each suggestion once; the page's sidebar resets with the session; `transmute` counts only real proposals; one trend tolerance.
- **Docs.** `UWT-METHODOLOGY.md` no longer says the loop applies changes or reads UWT scores.
- **Not changed:** sovereign is still the only prompt the diffs reach (Y6; one shared rule module is Y2), and the terminal's own `/switch` and `/exit` are tested through a harness of the same pattern, not by spawning `youi.mjs`, which needs the Keychain and the Anthropic API.

## 4. Step 2 (slice Y2): sensors on reported usage

- **Live stream first.** One real Claude Code stream (Claude Haiku 5.5, trivial prompt) is recorded first. It confirms the usage fields and the `rate_limit_event` shape, and becomes the fixture for tests.
- **Reported usage.** Per turn, the observer reads what the provider reports: input, cache read, cache creation, output, thinking where present, the cost delta for the turn, and context as the latest input plus cache. Rate-limit state is stored as reported, or `null`. It extends the existing usage tracking and C2's result-event usage, not a new ledger.
- **Evidential labels.** Every metric is marked `reported` or `estimated`, in the spirit of ORDO's evidential algebra, where honesty "may understate, never overstate". No code from the Cathedral is used.
- **Cache stability.**
  - A signal flags when the cached system prompt changed between turns.
  - The volatile lines (Effort, Date, branch, minds state) move out of the system prompt into a per-turn context block, so a mode or effort change does not rewrite the cache.
- **L1 filler** stays as a labelled estimate, English only, and is no longer graded.
- **Minds and table tools** report their usage to the observer.
- **One rule module.** The prompt rule lives in one shared module used by YOUI Web, the terminal and sovereign; today there are seven or more hand copies.

### 4.1 As built (2026-10-31)

- **The live stream.** Two runs of Claude Code 2.1.314 with Claude Haiku 5.5, YOUI's flags and environment, a one-line system prompt and "Reply with the single word ready.", from an empty scratch directory: a `--session-id` turn and one `--resume` of it. Together they cost US$0.000254 (US$0.000123, then US$0.000131). Their stdout, scrubbed of paths, socket paths and message and request ids, is `tests/fixtures/claude-code-stream-2.1.314.jsonl` and `…-resume.jsonl`. Their `rate_limit_event` values are synthetic: only that event's shape is from the live runs; its status, type, utilizations, thresholds, reset times and overage fields were replaced, before the branch was first pushed, with one made-up consistent set (the repository is public, and the recorded values were the account's own). Their session id and event uuids are placeholders. What they showed:
  - the usage field names and their places, `cache_creation`'s split by lifetime (all one-hour), `output_tokens_details.thinking_tokens`, and `modelUsage` entries with `thinkingTokens` and `contextWindow` (1,000,000 for Haiku 5.5) ([`YOUSPEAK.md`](YOUSPEAK.md) §2);
  - the result's `usage` is the run's own, while `total_cost_usd` and `modelUsage` are running totals, as C2 found;
  - the shape of `rate_limit_event.rate_limit_info`, with `unifiedWindows.five_hour` and `seven_day`, which C2's reading already matched;
  - `message_start.message.diagnostics.cache_miss_reason`: the resume missed the cache (`messages_changed`, 554 tokens) with an identical system prompt, so neither run read from the cache.
- **Reported usage.** Per Claude Code turn the kernel is told the run's input, cache read, cache creation, output, thinking where stated and cost, from C2's per-turn spend (`claudeCodeSpend`, extended with the parts), and the context as the latest request's input + cache read + cache creation. The window is the model's `contextWindow` where stated, else YOUI's table for its Claude models (an estimate), else unknown. The rate-limit state is kept as reported, or `null`. The Ollama/vLLM loop gives its reported token counts and YOUI's price-table cost. No new ledger: the page's usage figures are unchanged.
- **Labels.** Every section of `report()` and of `/api/youspeak`, its older top-level fields and the row carry `reported` or `estimated`; `/api/youspeak`'s `trends` (averages over rows) are not labelled. Thinking and its ratio are `null` where not reported (the old 0 is gone), and no window is assumed.
- **Cache stability.** The system prompt holds only the stable parts, among them how the mind tools are used, for either mode; date, git branch, model, the mode, thinking and effort, and in orchestrate mode the minds offered, go in a `[YOUI context]` block at the top of the stdin message. `cache:prompt_changed` fires when the system prompt's hash changes within one Claude Code conversation, naming the parts. The static parts are now kept per agent, Love home and tool list.
- **Minds and tools.** Each mind call reports its usage and cost to the observer (`senseMind`), apart from the agent's turns; the row has the minds' calls, tokens and cost. Bridge and table tools are sensed in L3 as before.
- **One rule module.** `youspeak-rule.mjs`; YOUI Web, the terminal, sovereign and the three unused harnesses import it. Sovereign keeps its June file-cache line through `youspeakRule({ fileCache: true })`. The ouroboros' diffs are of `youspeak-rule.mjs` now, so they reach every prompt built in code (this resolves Y6 for those), not the instances' `CLAUDE.md` copies (below).
- **L1, no longer graded** (§4). The S–D letter is gone from the kernel, the row (schema 2; schema 1 rows are still read), `statusLine()`, the page, the terminal's `/youspeak`, exit line and daily note, and sovereign's report. The useful ratio stays, labelled as an estimate and shown with `~`. `filler_regression` reads the ratio: 3 of the last 5 measured blocks under 85% useful, the line the old C grade began at.
- **After review** (2026-10-31, against `report_0de85559`; finding ids from the review):
  - the fixtures' rate-limit values are synthetic and pinned by a test, in both files, and the docs and headers say so (Z1, Z4, Z5, Z14);
  - L1 is no longer graded, as above (Z13);
  - figures nobody reported are `null`: sovereign-Ollama thinking (Z6), cache misses on routes without cache diagnostics (Z9), terminal and sovereign budget windows no header stated (Z16), and token counts a `/v1` response left out (Z8);
  - vLLM's stated `cached_tokens` are split out of its prompt, and the docs say what `prompt_tokens` holds where it does not state them (Z7);
  - an Ollama/vLLM mind that fails, times out or is stopped is counted, in the session that called it; a Claude mind stopped after its result counts what it reported (Z10, Z18);
  - every per-turn block states its mode, so direct takes back an earlier orchestrate hint; the fixed minds guide moved into the stable system prompt (Z2, Z20);
  - the claims about the rule module are narrowed to prompts built in code, the instance copies are listed, and the "in most cases" doubt example is back (Z12, Z25, Z26);
  - `/api/youspeak` drops `tokensSaved`, `grades` and `overallGrade`, and labels its older top-level fields (Z23); the Minds line shows input including cache (Z11);
  - a route or model switch drops the stale context figure, and the row keeps the peak's own window (Z15); `cache:prompt_changed` is always an estimate (Z17); evolve describes the file-cache proposal instead of "No diff" (Z24); tests cover the model line, a model switch, the latest request's context, the rate-limit state and Ollama minds (Z21, Z22);
  - a vLLM stream that carries an error mid-stream now fails the turn instead of returning its partial text as an answer.
- **Not done in Y2:**
  - Eleven `instances/*/CLAUDE.md` files keep their own three-line Markdown copy and a pointer to `YOUSPEAK.md`, which `youspeak-rule.mjs` and the ouroboros' diffs do not reach; `instances/asha/CLAUDE.md` points to the nuance primer instead.
  - Tool errors are still not counted in YOUI.
  - The `ollama` tool's own chat and bench calls are not metered (review Z19), and the `[YOUI context]` markers carry no nonce (Z3).
- **Not checked live:** whether a resumed YOUI turn now reads the cached system prompt; whether a change of `--effort` alone still invalidates Claude's cached messages.

## 5. Boundaries

- YOUSPEAK suggests and records; it authorises nothing and changes no setting.
- The store holds aggregates only: counts, token figures, cost, grades and signals. It never holds message text, file paths or tool inputs.
- The terminal and `sovereign.mjs` change only where YOUSPEAK touches them: the automatic actions go, and the shared rule module comes in. Their move to Claude Code is a separate question.
- The CI ouroboros stays read-only.

## 6. Slices

| Slice | What | Needs |
|---|---|---|
| Y1 | Suggest only, the per-session untracked store, the bug fixes, the proposal-only ouroboros, the docs | this design agreed |
| Y2 | Sensors on reported usage, evidential labels, the cache-stability signal, the volatile lines moved, minds and tools fed, one rule module (built, §4.1) | Y1, and the recorded live stream |

Each slice is a draft PR. Tests use fakes, except the one recorded stream.

## 7. Open questions for Yu (not decided here)

- Should the rule's effect ever be measured with a real ON-vs-OFF test? That spends model calls.
- Should the YOUI terminal and sovereign follow YOUI Web onto Claude Code?
- What should happen to the tracked `youspeak-history.json`: keep it as history, or remove it from git?
- Should the Cathedral's `discipline/` copies of the kernel be synced, or marked historical?
