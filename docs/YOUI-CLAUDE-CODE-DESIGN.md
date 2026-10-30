# YOUI reaches Claude through Claude Code

**Status:** agreed by Yu at the Collab table on 2026-10-30 (proposal `report_428e05d7`); C1 is built (§6); C2 is not built yet

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

## 3. Shape

### 3.1 One runner

`youi-web/claude-code.mjs` runs `claude -p` once per agent turn.

**Flags:**
- `--input-format stream-json --output-format stream-json --include-partial-messages --verbose`;
- `--model <session model>`, `--effort <session effort>`;
- `--system-prompt <Alpha's prompt>`;
- `--setting-sources project`, run from a dedicated, empty working directory (`~/.local/share/youi/claude-code/`), with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`;
- `--strict-mcp-config --mcp-config <YOUI's bridge>`, `--tools ""`, `--allowedTools mcp__youi__*`;
- `--permission-prompts none`.

**Conversation and events:**
- A YOUI session maps to one Claude Code session: `--session-id <uuid>` on the first turn, `--resume <uuid>` after that. Claude Code keeps the model-side history; YOUI keeps its own for the page.
- Stream events become YOUI's SSE events: text deltas, thinking, tool calls, usage, cost.
- Closing the browser ends the process group.

**Claude minds** (`review`, `ensemble`, `decompose`, `pipeline`, `layerthink`) use the same runner with:
- `--tools ""` and no MCP;
- `--no-session-persistence`;
- the mind's role prompt and effort `medium`.

### 3.2 YOUI's tools through a loopback MCP bridge

YOUI serves an MCP endpoint on its own loopback listener for each turn. It is passed in `--mcp-config` as an `http` server, with a one-time bearer token that is bound to the YOUI session and expires with the turn.

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
- They are `unreachable` when its version is below a model's minimum. Opus 5.5 needs 2.1.280.
- Otherwise they are `unchecked`, until the first answer.

## 4. Boundaries

- Content still goes to Anthropic (`REMOTE_MODEL:anthropic`). Only the client changes.
- Claude Code may store its own session transcripts for the dedicated working directory, as it does for any use; YOUI points at nothing else.
- **Not changed here.**
  - The terminal (`youi.mjs`) still uses the old route. Moving it is a separate question, once terminal and web share one module.
  - Ollama and vLLM routes are unchanged.

## 5. Slices

| Slice | What |
|---|---|
| C1 | The runner and the MCP bridge; Alpha's Claude turns go through Claude Code; the direct route and its headers are removed from YOUI Web |
| C2 | Claude minds through the runner; the registry's Claude Code check; usage and budget from result events |

Each slice is a draft PR. Tests use a fake `claude` executable that emits stream-json and calls the bridge, with no network and no Keychain.

## 6. As built

### C1 (`youi-web/claude-code.mjs`, `youi-web/mcp-bridge.mjs`, `youi-web/server.mjs`)

The binary is `YOUI_CLAUDE_BIN`, else `claude` on `PATH`, found at boot; test mode (`YOUI_TEST=1`) only ever runs `YOUI_CLAUDE_BIN`. The working directory is `YOUI_CLAUDE_CWD`, default `~/.local/share/youi/claude-code/`, created 0700; a run refuses if anything is in it.

An agent turn runs:

```
claude -p --input-format stream-json --output-format stream-json --include-partial-messages --verbose
  --model <model> [--effort <effort>]
  --system-prompt-file <0600 temp file> --system-prompt-snapshot off
  --setting-sources project --strict-mcp-config --mcp-config <0600 temp file>
  --tools "" --allowedTools mcp__youi --permission-prompts none
  --session-id <uuid> | --resume <uuid>
```

- **Environment:** the allowlisted child environment with `HOME` and `PATH` kept, plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`. No `ANTHROPIC_*` variable or other credential is passed.
- **Why `CLAUDE_CODE_DISABLE_CLAUDE_MDS`:** a live check on 2026-10-30 from a working directory under the home directory found `~/.claude/CLAUDE.md` in the model's context (about 1,240 input tokens). With the variable set, the context held no CLAUDE.md.
- **Why `--system-prompt-file` and snapshot off:** the prompt is long, so it goes in a file rather than on the command line. Each turn's prompt (mode, model, minds) is the one YOUI built for that turn; by default Claude Code would reuse the first turn's prompt.
- **Effort:** `none` passes no `--effort`; a model without effort gets none; `max` becomes `high` where `max` is not accepted.
- **Stdin:** one stream-json user line, closed after the `result` event; Claude Code then exits.
- **Raw mode:** `--no-session-persistence`, no MCP config, no allowed tools, and the one-line system prompt `You are Claude.`, since Claude Code would otherwise use its own.
- **Thinking:** Claude Code decides; effort is the control. The `thinking` setting is not passed on.

The bridge is `POST /mcp/<turn id>` on YOUI's own listener, after the loopback and Host checks and before any browser handling:
- it needs `Authorization: Bearer <token>`; the id and token are random per turn and stop working when the turn ends;
- it refuses any request with an `Origin` or `Cookie` header;
- it is POST only, with JSON responses, and implements `initialize`, notifications, `ping`, `tools/list` and `tools/call`; anything else, including Claude Code's `server/discover` probe, gets "method not found".

`tools/list` is `availableTools()` for the session. `tools/call` runs `executeTool` inside the session's scope with the turn's abort signal. Claude Code passes its tool-use id in `_meta`, so the page's tool cards line up.

Removed from YOUI Web:
- the Keychain read and token refresh;
- the direct Messages request and its headers and betas;
- the device id written under `~/.claude`;
- the billing line in the system prompt;
- the rate-limit header parsing.

The budget panel now says plan limits are managed by Claude Code. Until C2, Claude minds are listed as `not_configured`.
