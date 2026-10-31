# YOUI reaches Claude through Claude Code

**Status:** agreed by Yu at the Collab table on 2026-10-30 (proposal `report_428e05d7`); C1 and C2 are built (§6), as stacked local branches. The review's findings were fixed on the C2 branch in three rounds (§6.3, §6.4, §6.6); §6.5 lists the live checks and what is still not checked live.

**Date:** 2026-10-30

**Branch:** `feat/youi-claude-code-20261030` (stacked on love-unlimited#12)

## 1. Why

On 2026-10-30, Alpha's first message on Claude Opus 5.5 failed:

> API 400 … "Claude Code 2.1.92 does not support this model; version 2.1.280 or newer is required" (`claude_code_version_too_old`)

YOUI Web sends Claude requests itself, presenting itself as Claude Code:
- it uses the user's Claude Code login, read from the Keychain;
- it sends `User-Agent: claude-cli/2.1.92`, `x-app: cli` and the `claude-code-20250219` beta (`youi-web/server.mjs`, `claudeRequest`).

That login exists for Claude Code and Anthropic's own clients, not for other applications. Raising the version string to pass the check would mean impersonating a newer client. Yu chose instead to have YOUI go through the Claude Code installed on the device. Claude Code signs in, keeps itself current and uses Yu's subscription as it is meant to be used.

## 2. Feasibility check (2026-10-30, Claude Code 2.1.314 on the Air)

These runs were headless (`claude -p`), from an empty working directory, with Claude Haiku 5.5:

| Flags | Result |
|---|---|
| `--safe-mode`, `--strict-mcp-config --mcp-config <file>`, `--tools ""` | Signed in (`apiKeySource: none`, the subscription). No MCP servers were loaded (`mcp_servers: []`), so `--safe-mode` cannot carry YOUI's tools. It also answered in the user's configured language, so user settings still applied. |
| `--setting-sources project`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `--strict-mcp-config --mcp-config <file>`, `--tools ""`, `--allowedTools mcp__spike__ping`, `--system-prompt …` | The only tool was the MCP tool, and it was called and returned. A canary question found no global `CLAUDE.md` text and no memory directory in context. It answered in the language its system prompt asked for. |

- **Cost:** about US$0.0003 per run.
- **Overhead:** 70–140 ms beyond the API time.
- **Not usable:** `--bare` never reads OAuth, so it cannot use the subscription.
- **Where the canary ran:** not recorded, so this check says nothing about a working directory under the home directory. The C1 build's own live check (§6.1) found that, from a working directory under `~`, Claude Code's project walk reaches `~/.claude/CLAUDE.md` even with `--setting-sources project`, so the built runner also sets `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`.

## 3. Shape

### 3.1 One runner

`youi-web/claude-code.mjs` runs `claude -p` once per agent turn.

The flag list below is the plan as agreed; §6 is what was built and supersedes it (the prompt goes in `--system-prompt-file` with `--system-prompt-snapshot off`, the allowed-tools rule is `mcp__youi`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` are set, and an agent turn passes `--max-turns 50`).

**Flags (as planned):**
- `--input-format stream-json --output-format stream-json --include-partial-messages --verbose`;
- `--model <session model>`, `--effort <session effort>`;
- `--system-prompt <Alpha's prompt>`;
- `--setting-sources project`, run from a dedicated, empty working directory (`~/.local/share/youi/claude-code/`), with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`;
- `--strict-mcp-config --mcp-config <YOUI's bridge>`, `--tools ""`, `--allowedTools mcp__youi__*`;
- `--permission-prompts none`.

**Conversation and events:**
- A YOUI session maps to one Claude Code session: `--session-id <uuid>` on the first turn, `--resume <uuid>` after that. Claude Code keeps the model-side history; YOUI keeps its own for the page.
- **Switching models (Yu, 2026-10-30: "Carry, and say so").** YOUI keeps one history (`state.messages`) for every model, and records how much of it the Claude Code conversation has received (`seen`). A Claude turn always resumes the existing Claude Code conversation. The turns it has not received are put in front of the person's message as a fenced, text-only transcript: those an Ollama or vLLM model answered, and any message whose turn ended before it was sent. Only when Claude Code has lost the conversation does a new one start, carrying the whole chat (bounded). Before anything is sent, the page shows a notice naming how many messages are carried and which models answered them, and saying that they go to Anthropic and Claude Code stores them. The privacy block says the same (§4). An Ollama or vLLM turn after Claude reads the same history, where Claude's turns are its text only: the tool calls and results inside Claude Code's turns are not carried.
- Stream events become YOUI's SSE events: text deltas, thinking, tool calls, usage, cost.
- Closing the browser ends the process group.

**Claude minds** (`review`, `ensemble`, `decompose`, `pipeline`, `layerthink`) use the same runner with:
- `--tools ""` and no MCP;
- `--no-session-persistence`;
- the mind's role prompt and effort `medium`.

### 3.2 YOUI's tools through a loopback MCP bridge

YOUI serves an MCP endpoint on its own loopback listener for each turn. It is passed in `--mcp-config` as an `http` server, with a per-turn bearer token that is bound to the YOUI session and is valid for any number of requests until the turn ends.

Each tool call runs inside that session's scope, so nothing about tools changes:
- capabilities, the per-message mind budget and the decision seat apply as they do today;
- the same tool list is offered; capabilities that hide a tool hide it from the bridge.

Claude Code's built-in tools stay off. YOUI's own file and shell tools, gated by the developer profile, remain the only way to touch files.

### 3.3 What goes away

YOUI Web no longer reads the Claude Code credential from the Keychain or refreshes it, and no longer sends Claude Code's headers or betas. `claudeRequest` and the 401-refresh path are removed.

Budget figures that came from Anthropic's rate-limit headers now come from Claude Code's result events (cost and usage). Otherwise the UI says that Claude Code manages them.

### 3.4 Requirements and reporting

The minds registry checks `claude --version` without a model call:
- Claude minds are `not_configured` when Claude Code is missing.
- They are `unreachable` when its version is below the minimum. Every Claude model needs at least 2.1.299, YOUI's floor (§6.2), and a model can name a later minimum of its own.
- Otherwise they are `unchecked`, until the first answer.

## 4. Boundaries

- Content still goes to Anthropic (`REMOTE_MODEL:anthropic`). Only the client changes.
- When a session switches to Claude, the turns Claude has not received, local Ollama and vLLM turns included, are carried into Claude as text (§3.1). They go to Anthropic, and Claude Code keeps them in its transcript. `/api/status` says so in `privacy.switchToClaude`, `privacy.retention.claudeCode` and, on a Claude model, `privacy.direct.sends`.
- Claude Code keeps the transcripts of YOUI's Claude conversations, tool results included, under `~/.claude/projects/<the dedicated working directory's real path, with every non-alphanumeric character as "-">/` (by default `~/.claude/projects/-Users-<user>--local-share-youi-claude-code/`), as it does for any use. `/api/clear` starts a new conversation and leaves the old transcript there; YOUI deletes none. Raw mode and Claude minds are not kept. The privacy block (`/api/status` → `privacy.retention.claudeCode`) states the real path.
- **Not changed here.**
  - The terminal (`youi.mjs`) still uses the old route. Moving it is a separate question, once terminal and web share one module.
  - Ollama and vLLM routes are unchanged.
  - `/api/orchestrate/run` still runs the Python orchestrator (`adaptive/`), which calls Anthropic with an API key from the Keychain. That is an API-key route, not Claude Code's login.
  - The truth detector (`TRUTH_DETECTOR_ENABLED=1`) is a separate service that still reads and refreshes the Claude Code credential and calls Anthropic with Claude Code's headers. Moving its judge to the Claude Code runner is open.

## 5. Slices

| Slice | What |
|---|---|
| C1 | The runner and the MCP bridge; Alpha's Claude turns go through Claude Code; the direct route and its headers are removed from YOUI Web |
| C2 | Claude minds through the runner; the registry's Claude Code check; usage and budget from result events |

Each slice is a draft PR. Tests use a fake `claude` executable that emits stream-json and calls the bridge, with no network and no Keychain.

## 6. As built

### 6.1 C1 (`youi-web/claude-code.mjs`, `youi-web/mcp-bridge.mjs`, `youi-web/server.mjs`)

The binary is `YOUI_CLAUDE_BIN`, else `claude` in an absolute `PATH` entry, else `~/.local/bin/claude` (the native installer's place, which a launch agent's `PATH` often lacks), found at boot; relative `PATH` entries are skipped. Test mode (`YOUI_TEST=1`) only ever runs `YOUI_CLAUDE_BIN`. The kingdom-os launch agent (`kingdom-os/modules/09-browser.sh`) puts `$HOME/.local/bin` first on its `PATH`. The working directory is `YOUI_CLAUDE_CWD`, default `~/.local/share/youi/claude-code/`, created 0700; a run refuses if anything is in it, and the page gets an error naming the directory.

An agent turn runs:

```
claude -p --input-format stream-json --output-format stream-json --include-partial-messages --verbose
  --model <model> [--effort <effort>]
  --system-prompt-file <0600 temp file> --system-prompt-snapshot off
  --setting-sources project --strict-mcp-config --mcp-config <0600 temp file>
  --tools "" --allowedTools mcp__youi --permission-prompts none --max-turns 50
  --session-id <uuid> | --resume <uuid>
```

- **Environment:** the allowlisted child environment with `HOME` and `PATH` kept, plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` and `CLAUDE_CODE_DISABLE_FILE_MENTIONS=1`. No `ANTHROPIC_*` variable or other credential is passed.
- **Why `CLAUDE_CODE_DISABLE_FILE_MENTIONS`:** without it, Claude Code reads a file named by an `@path` token anywhere in the message, carried chat text included, and sends its contents to Anthropic, past YOUI's `read_file` gate and the carry notice. With it, the model gets only the path, which it cannot read with `--tools ""`. Checked live (§6.5). Claude Code's changelog adds the variable in 2.1.299, so YOUI's floor is 2.1.299 and an older Claude Code is held back.
- **Why `CLAUDE_CODE_DISABLE_CLAUDE_MDS`:** a live check on 2026-10-30 from a working directory under the home directory found `~/.claude/CLAUDE.md` in the model's context (about 1,240 input tokens). With the variable set, the context held no CLAUDE.md.
- **Why `--system-prompt-file` and snapshot off:** the prompt is long, so it goes in a file rather than on the command line. Each turn's prompt is the one YOUI built for that turn; by default Claude Code would reuse the first turn's prompt. Since YOUSPEAK Y2 the per-turn settings (date, git branch, model, thinking and effort, the minds hint) go in a `[YOUI context]` block at the top of the stdin message instead, so the system prompt, and Claude's cache of it, stays the same across a conversation ([`YOUSPEAK.md`](YOUSPEAK.md) §2.1).
- **Effort:** `none` passes no `--effort`; a model without effort gets none; `max` becomes `high` where `max` is not accepted.
- **Stdin:** one stream-json user line, written once the process has started (just after the page's notice, when there is one), and closed after the `result` event; Claude Code then exits, and its process group is ended if it is still running 5 seconds later. When the first word, after any leading whitespace, is one `/` and then a name (a letter, digit or `_`, followed only by letters, digits and `_ : . @ -`), its slash is sent as `\/` (`/compact now`, `/plugin:command`, `/__remote-workflow`): Claude Code runs a leading `/` as a slash command (§6.5), and the model still reads the text. Any other first word is sent unchanged, such as a path with a second `/` (`/Users/me/app.py`). A one-segment path such as `/tmp` fits the rule and is escaped, which the model reads the same.
- **Limits:** `--max-turns 50` per message, as YOUI's own loop has, and a turn that has not produced its result after 15 minutes is stopped, with an error on the page. `YOUI_CLAUDE_TURN_TIMEOUT_MS` sets another limit between 1 second and 6 hours; any other value is the default. The limit ends with the result: a run that answers and then lingers is not turned into an error.
- **Raw mode:** `--no-session-persistence`, no MCP config, no allowed tools, and the one-line system prompt `You are Claude.`, since Claude Code would otherwise use its own.
- **Thinking:** Claude Code decides; effort is the control. The `thinking` setting is not passed on.

The bridge is `POST /mcp/<turn id>` on YOUI's own listener, after the loopback and Host checks and before any browser handling:
- it needs `Authorization: Bearer <token>`; the id and token are random per turn, the token is valid for any number of requests during the turn, and both stop working when the turn ends;
- its mcp-config entry sets `alwaysLoad: true`. In 2.1.314's own schema that "blocks startup until the server is connected (capped at the standard 5s connect timeout)". So init comes after that wait, and a bridge that init reports as anything but `connected` (`pending` after the 5-second cap, `failed`, `needs-auth`, `disabled`, or not listed) means the turn would run without YOUI's tools. YOUI stops the run and sends an error instead;
- it refuses any request with an `Origin` or `Cookie` header;
- it is POST only, with JSON responses, and implements `initialize`, notifications, `ping`, `tools/list` and `tools/call`; anything else, including Claude Code's `server/discover` probe, gets "method not found".

`tools/list` is `availableTools()` for the session. `tools/call` runs `executeTool` inside the session's scope with the turn's own abort signal, which Stop, the turn limit and the end of the turn all abort (§6.4). Claude Code passes its tool-use id in `_meta`, so the page's tool cards line up.

Removed from YOUI Web:
- the Keychain read and token refresh;
- the direct Messages request and its headers and betas;
- the device id written under `~/.claude`;
- the billing line in the system prompt;
- the rate-limit header parsing.

The budget panel now says plan limits are managed by Claude Code. Until C2, Claude minds are listed as `not_configured`.

### 6.2 C2

**Claude minds.** A Claude mind runs the same runner with:
- `--no-session-persistence`, `--tools ""` and no MCP config;
- the mind's role prompt and `--effort medium` (none for a model that takes no effort);
- the mind's model.

Its tokens and cost come from the result event, and a success is recorded with the registry. A mind call is counted as a call (`mindCalls`), not as one of the agent's turns; one that gives no answer, including one stopped by a timeout or the end of the turn, is also counted in `failedMindCalls`, with no tokens when it was stopped.

**The registry.** `claude --version` is run with a 3-second timeout and cached for a minute, shared by the registry and chat turns. It never makes a model call. Only Claude Code's own line counts: `<x.y.z>[-pre-release] (Claude Code)` at the start of a line, optionally followed on that line by more words (a later build's ` (native)`). Claude minds are:
- `not_configured` when Claude Code is missing;
- `unreachable` when the version cannot be read, or is below what the model needs: YOUI's floor, 2.1.299, for every model, or the model's own minimum if later (`claude-opus-5-5`'s own minimum, 2.1.280, is below the floor). A pre-release of the minimum is below it;
- otherwise `unchecked` until a real answer, then `reachable`.

A chat turn checks the same version first and, if it is too old for the session's model, ends with an error that names both versions. A chat turn whose version cannot be read is refused with an error, like a too-old one, and the failed read is not cached, so the next turn asks again; the registry lists Claude minds as `unreachable`. Failing closed matters because below the 2.1.299 floor an `@path` in a message is read and sent (§6.1).

**Why 2.1.299.** The changelog Claude Code caches at `~/.claude/cache/changelog.md` (read 2026-10-30; its newest entry is 2.1.312) dates the newest of YOUI's flags it names: `--system-prompt-snapshot off` was added in 2.1.267, and `--permission-prompts none` in 2.1.259. The others it names are older (`alwaysLoad` in 2.1.121, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` in 2.1.169). It does not mention `CLAUDE_CODE_DISABLE_CLAUDE_MDS`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `--no-session-persistence` or `model_usage_since_last_result`, so when those arrived is not known from it. The binaries on the Air show that `model_usage_since_last_result` is absent from 2.1.303–2.1.307 and present in 2.1.311, 2.1.312 and 2.1.314. `CLAUDE_CODE_DISABLE_FILE_MENTIONS` came in 2.1.299 (§6.1), and below it Claude Code sends the contents of any file named by an `@path` in a message, so the floor is 2.1.299. It is also above every dated flag and above 2.1.280, the version Anthropic's API named for Opus 5.5 (§1). Raised from 2.1.280 on 2026-10-30, after the live mention check.

**Usage and budget.**
- `total_cost_usd` and `modelUsage` in a result are running totals for the Claude Code conversation, and a `--resume` continues from the totals its transcript saved (checked live, §6.5). When a result states `model_usage_since_last_result` (2.1.311 and later), YOUI counts the sum of its `costUSD` and its figures by model, as stated. Otherwise (2.1.303–2.1.307, and older builds) it counts what the result adds: the running total less the last total that conversation stated (by model, `modelUsage` less the last one).
- The baseline starts at zero whenever YOUI starts a conversation (first turn, `/api/clear`, `/api/switch`, a lost conversation). It moves with every result that states a total at or above it, an error result such as `error_max_turns` included, which so counts what its turn cost. An error result with a zero total (Claude Code 2.1.314 reports an unknown `--resume` with `total_cost_usd` 0, checked live) or a total below the baseline moves nothing and, without per-result figures, adds nothing; the next result's total counts what lies behind it, once.
- **Where YOUI can count less than Claude Code spent.** `model_usage_since_last_result` covers only what one Claude Code process spent since its own last result; a resumed process starts it empty (§6.5). So on 2.1.311 and later the model calls of a run that ended without a result (stopped before it, timed out, killed) are counted nowhere. On older builds the next result's running total takes them in, as far as Claude Code's transcript saved them (not checked live).
- A turn stopped after its result arrived still counts that result and keeps its completed texts; the usage event is sent if the page still listens.
- A mind and a raw-mode turn run without persistence, so a result's total is that run's cost, error or not.
- The session's token totals are the main loop's `usage`, as the page shows them.
- The usage event of a Claude turn carries the turn's added cost (`cost_usd`), the session's Claude cost, and the session's Claude totals (`claudeUsage`), which the page shows. After a Claude turn the page saw to its end, the live and reloaded Claude figures agree; after a Stop, only a reload is sure to show them. A Claude mind called from an Ollama or vLLM turn is counted in the session's Claude totals (`/api/usage`), but that turn's usage event carries no Claude totals, so the page's Claude panel shows it only after the next Claude turn or a reload.
- The 5-hour and 7-day figures are what Claude Code itself reports in its stream (`rate_limit_event`), labelled as such. Until it has stated a window, the panel says Claude Code manages them.

**Live check (2026-10-30, Claude Code 2.1.314).** One YOUI chat turn on Opus 5.5 at effort `low` answered "ready" through the bridge-connected Claude Code. It cost US$0.093 (11,648 input tokens, uncached).

### 6.3 Review fixes, first round (`report_428e05d7`)

A review of C1 and C2 found three chat breakers and a set of smaller faults. The first round (commits `3d3c1ac`, `eb2c1f2`) fixed most of them on the C2 branch, including the C1 code they touch. An adversarial verification then found gaps and three regressions, which the second round fixes (§6.4). What stands now:

- **Setup failures reach the page (F1).** `runClaudeCode` rejects only on abort. A working directory that is not empty, a scratch file that cannot be written or a bad argument resolves with an error (and `setupFailed`), and the turn sends it, then `done`. A setup failure is never taken for Claude Code's own "already in use" or "no conversation" answer.
- **One history across models (F4).** Superseded by §6.4: the first round started a new conversation after any non-Claude turn; now the conversation is resumed and only what it lacks is carried.
- **Finding Claude Code (F5, F17).** See §6.1.
- **Cost (F2, F20–F23).** See §6.2, "Usage and budget" (the baseline rules are from §6.4).
- **Stop and the bridge (F3, F7, F9).** Each Claude turn keeps its bridge calls in flight. Once the turn is closing, the bridge starts no new call; the turn, and the session's lock on chat, lasts until the running calls end or 25 seconds pass (§6.4). A call whose tool card is still awaited when Stop comes runs nothing. On Stop, the run waits up to 3 seconds for Claude Code's process group to go before the turn ends, so the next `--resume` does not meet the old process.
- **Limits and process groups (F10, F11).** `--max-turns 50`, the turn limit (§6.1), and the whole process group is signalled whether or not its leader is still running, including when the leader exits.
- **Claude Code's own messages (F12).** An assistant message Claude Code writes itself for an API error or a refusal (marked `error`, `refused_message_id`, or model `<synthetic>`) is not stored as the answer or shown as text. The rule for the model's own text is in §6.4.
- **Scratch files (F8).** Shutdown removes the running turns' scratch directories with their processes. At boot (not in test mode unless a test asks), YOUI removes, in its temp directory, directories that belong to this user, were last changed before it started, and are either `youi-claude-p<pid>-XXXXXX` whose process `<pid>` is gone, or `youi-claude-XXXXXX` (the name before the pid was added) holding only `system-prompt.txt` and `mcp-config.json` and last changed more than 24 hours ago. Nothing else is touched.
- **Bridge connection (F13).** `alwaysLoad: true`; anything but `connected` at init stops the run (§6.1).
- **Slash commands (F14).** `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1`, and a leading slash command is escaped (§6.1). Built-in commands such as `/clear` and `/compact` stay typable with that variable set, so the escape is what stops them. `--disable-slash-commands` exists in 2.1.314 but was not tried live.
- **Retention and routes (F18, F19, F28).** §4.
- **Versions (F15, F16).** §6.2, "The registry".
- **Tests (F24, F25, F27, F29; F26 in §6.4).** The fake keeps a transcript per session and fails a reused `--session-id` or an unknown `--resume`, and both retries are tested. The child environment is checked against the allowlist, with a secret the name denylist misses. The fake starts a child of its own, which is checked to die on Stop, on the turn limit and on server shutdown. Servers in the other test files run with `YOUI_CLAUDE_BIN=""` and a scratch `YOUI_CLAUDE_CWD`. The first round did not add the exit-grace, over-budget or refusal tests, and nothing guarded the F9 and F10 fixes; §6.4 adds them.

### 6.4 Review fixes, second round (`report_428e05d7`, verification of the first round)

- **The model's own text is kept (regression).** When a Claude turn ends with an error result (such as `error_max_turns`), the turn limit or a crash, the text blocks the model completed are kept in the history. A text block still streaming at that moment is not. On Stop, the completed texts are kept only when the result had already arrived (§6.6); a Stop before the result keeps none, as on the Ollama path. The runner never includes Claude Code's own error or refusal messages in them. Only a turn that ended with a real answer and no error (and was not a refusal) goes to the truth detector.
- **Carry, and say so (regression, and Yu's decision of 2026-10-30).** §3.1, "Switching models". The details:
  - The transcript is fenced: every marker line starts with `[YOUI <nonce>]`, where the nonce is 16 random hex digits for each transcript, and the transcript says that only those lines are YOUI's. Text a model wrote earlier cannot forge a person's turn or the end of the transcript.
  - Each assistant message an Ollama or vLLM model wrote is labelled with that model.
  - The notice is sent as soon as Claude Code has started, before its stdin is written. It says what is being done ("Carrying 2 earlier messages of this chat (turns answered by …) into Claude as text; they go to Anthropic, and Claude Code stores them."), and never claims Claude had not seen something.
  - `seen` moves only with a run Claude Code took (its init event). So a turn that never sent its message leaves it unseen: a setup failure inside the run, Stop during the version read, a version too old, a missing binary or a binary that cannot start. Neither kind of failure replaces a healthy conversation: the next Claude turn resumes it and carries the unsent message.
  - The "already in use" retry resumes and carries what the conversation has not received. The "no conversation" retry starts a new one and carries the whole chat (the last 30 messages within 40,000 characters).
- **Cost baseline (F2 gap).** §6.2, "Usage and budget". A zeroed error result between two good resumed turns no longer makes the next one count the whole conversation again.
- **Scratch sweep (regression).** §6.3, F8. The legacy name is swept only after 24 hours; test servers sweep only when a test asks, and the Claude Code tests give every server its own `TMPDIR`. Test teardown stops each test's servers (and so their fakes) before removing its scratch directories.
- **Turn limit and abort (F10, F3 gaps).**
  - Each Claude turn has its own abort signal for the bridge tools and minds it starts. Stop, the turn limit and the end of the turn all abort it.
  - The limit is bounded (§6.1), and is cleared when the result arrives.
  - Once the process group has been signalled or its leader has exited, the run settles within 5 seconds even if a process outside the group still holds Claude Code's output.
  - A closing turn waits up to 25 seconds for bridge calls still running. That covers one Collab request (`decision-seat.mjs` allows each 20 seconds), because `decision`, `council` and `delegate` do not take the turn's signal.
- **Bridge not connected (F13 gap).** §6.1.
- **Leading slash (low).** §6.1.
- **Robustness.** A stdout line that parses to JSON but is not an object (`null`, a number, an array) is skipped. The version line may carry words after `(Claude Code)`. The drain test waits up to 15 seconds.
- **Page.** A `/api/chat` answer that is not OK (409 while the last turn is still finishing, 400, 403) shows the person a line saying the message was not sent, and why, instead of nothing.
- **Tests that now guard the fixes.** The second round said each fails when its fix is reverted (as did the `35ddc64` commit message); the verification found that true for most, but not for the notice's order, which a third-round test now guards (§6.6). The list:
  - Stop waits for Claude Code to exit before the next `--resume` (F9).
  - A child left in the group goes when the leader exits (F10).
  - A Claude Code that lingers after its result is ended after 5 seconds, and the turn limit does not fire after the result (F26).
  - A leftover holding stdout cannot keep the turn open.
  - The turn limit stops a running bash tool.
  - Mind calls through the bridge stop at the per-message ceiling (F27).
  - A Claude mind's refusal is a failed call.
  - Error-turn text is kept, and the truth detector gets nothing from it.
  - A zeroed error result moves no baseline.
  - The carry tests: resume, fencing, unsent messages, and both retries; the notice before anything is sent since §6.6.
  - The sweep's age rule and its test-mode skip.
  - `pending` at init stops the turn.
  - A `null` stdout line does not crash the server.
  - An absurd turn limit is treated as the default.

### 6.5 Live checks

**2026-10-30, Claude Code 2.1.314, Claude Haiku 5.5, from a scratch directory.** Two runs, YOUI's flags, `--max-turns 50` accepted:
1. `--session-id`, "Reply with the single word: ready": answered "ready"; `total_cost_usd` 0.0001212, `modelUsage` and `model_usage_since_last_result` the same.
2. `--resume` of that conversation, with the stream-json message `/context`: Claude Code ran `/context` itself (a context table, no model request, `num_turns` 0, `usage` all zero), and the result still stated `total_cost_usd` 0.0001212 and the same `modelUsage`, restored from the transcript, while `model_usage_since_last_result` was empty.

Together they cost US$0.0001212.

**2026-10-30, second round, one run** (`--resume` of a random, unknown session id, Haiku 5.5, from a scratch directory outside the repository): Claude Code exited 1 before init, made no model request and cost US$0 (`total_cost_usd` 0, `duration_api_ms` 0). It reported "No conversation found with session ID: <id>" on **both** channels:
- on stderr, as one line;
- on stdout, as a stream-json `result` with `subtype` `error_during_execution`, `is_error` true, zeroed usage, `modelUsage` `{}` and the text in `errors`.

YOUI matches its "no conversation" and "already in use" patterns against the run's error and everything it kept of stderr (up to 8,000 characters). The run's error is the result's `result` text or its `errors`, or, when there was no result, the exit with the first 400 characters of stderr. Plain stdout lines that are not stream-json are ignored.

**Not checked live:**
- on which channel Claude Code reports a reused `--session-id` ("already in use"); the fake reports it on stderr only;
- whether `--max-turns` counts per message or across `--resume`;
- whether `modelUsage` keys match YOUI's model ids;
- a live run with the bridge at init after the second round (the `connected` requirement rests on 2.1.314's schema text and the C2 live check, where the bridge answered).

**2026-10-30, third round, two runs** (`@` file mentions; Claude Code 2.1.314, Haiku 5.5, `--no-session-persistence`, YOUI's flags and environment otherwise, from an empty scratch directory). A scratch file held only `CANARY-7Q4X-NOT-A-SECRET`, and the stream-json message was `@<its absolute path> What exact text does the attached file contain? If you see no attached file, answer NONE.`:
1. Without `CLAUDE_CODE_DISABLE_FILE_MENTIONS`: the answer was `CANARY-7Q4X-NOT-A-SECRET`, so Claude Code read the file and sent it. US$0.0001711.
2. With it: the answer said the file's contents were not attached and that it had no tool to read them; the canary appears nowhere in the output. US$0.0003441.

### 6.6 Review fixes, third round (`report_428e05d7`, verification of the second round)

- **Cost.** An error result with a real total now counts what it added and moves the baseline, so `error_max_turns` is counted on builds without per-result figures, and a later build without them does not count it again (§6.2). A raw-mode turn's result counts as its own run's cost. A turn stopped after its result counts that result on the server (the runner hands the result and completed texts back with its abort); the page has already disconnected, so it sees that cost only on reload. The usage send after Stop has no test. The cost comments and §6.2 now say where YOUI can count less than Claude Code spent.
- **No retry in an ended turn.** A run that reached the turn limit before init is not retried, even when its stderr reads like a lost or taken conversation.
- **`@` file mentions.** `CLAUDE_CODE_DISABLE_FILE_MENTIONS=1` (§6.1, checked live in §6.5). The transcript's fence is described as a label for the model, not a filter.
- **Leading slash.** A name may start with `_`, as Claude Code's own `/__remote-workflow` does (§6.1).
- **Robustness.** A stdout event with a field of the wrong type stops the run with an error instead of throwing out of the line listener, which would end the whole server. A `session_claimed` 409 on the page shows its own reason.
- **Tests added**, each checked to fail with its fix reverted: the notice is said before Claude Code can read the message (the fake records when it reads stdin); a turn stopped after its result keeps its cost and text; a turn ended by its limit retries nothing; the malformed event; the `_` names; the error-result cost.
- **Fixes that still have no test of their own:** `onTimeout` alone and the end-of-turn abort alone, the F10 SIGKILL escalation after the leader exits, the server clamp's 1-second floor and its fall-back to the default, and the refusal clause in `answered` (with the `refused_message_id` check). The refusal leg of the detector gate, the `<synthetic>` marker, `setupFailed` in the session check, the 25-second drain length, `needs-auth`, `disabled` or not-listed bridges at init, the raw-mode cost rule and the page's `session_claimed` line are untested too.

### 6.7 Final check (`report_428e05d7`)

- **The floor fails closed.** A chat turn whose `claude --version` cannot be read (no answer within 3 s, or an unexpected format) is refused, and the failed read is not cached. Before this, such a turn ran unchecked, so a 2.1.280–2.1.298 build could read and send an `@path` file.
- **Stop and usage.** §6.6 now says what happens: the cost of a turn stopped after its result is recorded on the server, and the page sees it on reload.
