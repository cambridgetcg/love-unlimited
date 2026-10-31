# Who still uses Claude Code's login

**Status:** inventory taken on 2026-10-31 on branch `feat/youi-honesty-20261031`, under proposal `report_18b095a6` (item 2, honesty). Line numbers are for that branch.

Claude Code signs in once and keeps its login in the macOS Keychain, under the service `Claude Code-credentials`. That login exists for Claude Code and Anthropic's own clients. On this branch stack (C1 and C2, draft PRs #13 and #14, not merged or installed), YOUI Web's Claude turns and Claude minds run the installed `claude` ([`YOUI-CLAUDE-CODE-DESIGN.md`](YOUI-CLAUDE-CODE-DESIGN.md)). A YOUI Web started from an older checkout still reads the login and sends Claude Code 2.1.92's headers. The "YOUI Web reaches it?" column below is about the code on this branch.

**Moving YOUI Web did not end the use of Claude Code's login.** The files below still do one or more of these:

- **(a) read** Claude Code's login from the Keychain;
- **(b) present themselves as Claude Code**: the `claude-cli/2.1.92` User-Agent, `x-app: cli`, the `claude-code-20250219` beta, the `x-anthropic-billing-header` line in the system prompt, the "You are Claude Code" system line, or `device_id` / `account_uuid` in the request metadata;
- **(c) write** Claude Code's Keychain entry (after refreshing the token themselves) or delete it.

How it was found: `/usr/bin/grep -rn` over the whole repo, `.git` excluded, for the service name, `find-`/`add-`/`delete-generic-password`, `oauth/token`, `get_oauth_token`, `claude-cli/`, `x-app`, `claude-code-20250219`, `billing-header`, `device_id`, `account_uuid` and "You are Claude Code". Left out: hits about other Keychain entries (`tools/credentials.py`, `tools/kos.py`, `tools/store-api-keys.sh`, `tools/bootstrap.sh`); tests of these files; files that only quote the "You are Claude Code" line as text or data and call nothing (`fate/`, `training/data/`, `AINP.md`, `memory/`, `docs/superpowers/`); and files that match `device_id` or `x-app` for other reasons (`tools/continuity.py` and `tools/experience.py` keep their own `device_id`; `x-apple` URLs in `tools/kingdom-mac`, `tools/macos-grants.sh` and `kingdom-os/MACOS.md`).

## The two runtimes people type into

| File | (a) read | (b) presents as Claude Code | (c) writes | YOUI Web reaches it? |
|---|---|---|---|---|
| `youi.mjs` (the terminal), with `youi-keychain.mjs` | `youi.mjs:288`, `youi-keychain.mjs:54` | 2.1.92 User-Agent and `x-app` `youi.mjs:698`; betas `:677`; billing line `:623`; `device_id` `:684`, from the file named at `:408` (created if missing) | refreshes at `platform.claude.com/v1/oauth/token` (`:289`) and writes back with `add-generic-password -U` (`youi-keychain.mjs:106`, called from `youi.mjs:301`); it refuses to create a missing entry and never deletes | No. A separate process. |
| `sovereign.mjs` | `:198`, `:213` (through `youi-keychain.mjs:54`, read only) | 2.1.92 User-Agent and `x-app` `:651-652`; `claude-code-20250219` beta `:627-628`; billing line `:1017`; `device_id` `:639`, created at `:591` if missing; `account_uuid` from the token `:608`, `:641` | **None since this branch.** It used to delete the entry and add it again with the credential JSON in argv. Now an expired or expiring token stops it (below). | No. A separate process. |

Both say this in their own output: `node youi.mjs --inspect` and `node sovereign.mjs --inspect` print a `claude_code_login` block. Another slice of the same proposal (item 3) is to move the terminal onto the installed `claude`; until it lands, the terminal does what its row says. Sovereign's move waits for Yu's answers.

## Services and scripts

| File | (a) read | (b) presents as Claude Code | (c) writes | YOUI Web reaches it? |
|---|---|---|---|---|
| `tools/truth_detector/_oauth.py`, `backends.py` | `_oauth.py:26`, `:49` | through `tools/ainp/nullify.py` (next row) | refreshes at `api.anthropic.com/v1/oauth/token` (`:28`) and writes back with `add-generic-password -U` (`:77`), with the credential JSON in argv; the value written holds only `accessToken`, `refreshToken` and `expiresAt` (`:74`), so other fields in the entry are lost | **Yes, when `TRUTH_DETECTOR_ENABLED=1`.** YOUI Web posts every non-raw turn that answered without an error to it (`youi-web/server.mjs`, `postDetection`). Its routes (`config.yaml`) judge Claude chats with the local `kingdom-truth` model on vLLM and every other chat (Ollama, Qwen, GLM and the rest) with Claude Haiku. The Haiku judge uses `ANTHROPIC_API_KEY` when the detector's own environment has one, and Claude Code's login otherwise. |
| `tools/ainp/nullify.py` | — | 2.1.92 User-Agent `:220`, `x-app` `:244`, `claude-code-20250219` beta `:219` (`build_headers`) | — | Only through the truth detector. |
| `raw-chat/server.mjs` | `:29`, `:38` | `x-app` `:364`; `claude-code-20250219` beta `:363`; "You are Claude Code" system line `:349`; its User-Agent is its own (`claude-raw/0.3`) | refreshes (`:30`) and writes back with `add-generic-password -U` (`:86`), with the credential JSON in argv; the value written holds only `accessToken`, `refreshToken` and `expiresAt` (`:83`), so other fields in the entry are lost | No. Its own server on port 7878. |
| `subscription-harness.mjs` | `:120`, `:130` | only the `oauth-2025-04-20` beta | refreshes (`:121`), then **deletes the entry and adds it again** with the credential JSON in argv (`:170`, `:174`): the hazard sovereign had | No. Listed as unused in [`YOUSPEAK.md`](YOUSPEAK.md) §1. |
| `training/scripts/soul/oauth_client.py` | `:39`, `:104` | `x-app` `:190`; `claude-code-20250219` beta `:45`; "You are Claude Code" system line `:52`; its User-Agent is its own (`claude-soul/0.1`) | refreshes (`:40`) and writes back with `add-generic-password -U` (`:120`), with the credential JSON in argv; the value written holds only `accessToken`, `refreshToken` and `expiresAt` (`:117`), so other fields in the entry are lost | No. Used by the training scripts in the same folder. |
| `training/scripts/claude_mode_one_gen.py`, `eval_adapter.py`, `judge_gate.py` | through `tools/truth_detector/_oauth.py`'s `get_oauth_token`: imported at `claude_mode_one_gen.py:39`, `eval_adapter.py:40`, `judge_gate.py:53`; called at `:174`, `:149`, `:219` | "You are Claude Code" system line `claude_mode_one_gen.py:43`, `eval_adapter.py:45`, `judge_gate.py:57`; only the `oauth-2025-04-20` beta (`:109`, `:114`, `:149`) | through `_oauth.py`'s refresh and write-back (the truth detector row) | No. Run by hand. |
| `ainp.mjs` | — | 2.1.92 User-Agent `:160`, `x-app` `:174`, `claude-code-20250219` beta `:159` (`buildHeaders`) | — | No. Nothing in the repo imports it; `AINP.md` describes it. |
| `docs/research/test-unlock.mjs`, `test-unlock2.mjs`, `test-429.mjs` | `test-unlock.mjs:13`, `test-unlock2.mjs:14`, `test-429.mjs:10` | `test-unlock.mjs`: `x-app` from `:54`, 2.1.92 User-Agent from `:63`, random `device_id` from `:79`; `test-unlock2.mjs`: User-Agent and `x-app` `:33-34`, billing line and "You are Claude Code" `:41-43`, `device_id` from `:55`; `test-429.mjs`: only the `oauth-2025-04-20` beta | — | No. One-off research scripts, run by hand. |

Only `youi-keychain.mjs` writes the entry with the credential on stdin and keeps the entry's other fields.

Close to this list but not on it: `fate/cloak.sh:109` overwrites `~/.claude/device_id` with a fresh id. That file is the `device_id` the terminal and sovereign send.

## Sovereign no longer rewrites Claude Code's login

Sovereign used to refresh an expired token itself and write the result back: it read the entry, deleted it, then added it again with the credential JSON as a command-line argument. Nothing checked the delete, so a failed add left Claude Code, and YOUI Web with it, with no login.

Refreshing without writing back would be no better: the token endpoint can return a new refresh token, which Claude Code would not have. So sovereign now only reads the login, through the terminal's read-only Keychain code: the entry under your account name first, then the one with an empty account name. The old sovereign re-added the entry under an empty account name, so that one can be a stale copy, and the message says so when the login came from it.

- **At start**, a missing login, an expired token, or one that expires within 5 minutes stops it with a message to sign in again with the claude CLI: start `claude`, use `/login` if it asks, then run sovereign again. It exits 1.
- **Mid-task**, the same message, after it saves its state the way Ctrl+C does, so `node sovereign.mjs --continue` picks the task up after you sign in. It exits 1.
- **On a 401**, it reads the Keychain once more. If Claude Code has renewed its login in the meantime, it retries once with the new token; otherwise, or if the entry is now missing or expired, it stops the same way.

Tested in `tests/test_youi_sovereign_login.mjs`, with a fake `security` on `PATH` and a `fetch` replaced by a preload that answers from a plan and blocks everything else: the start-up refusal for a missing, an expired and an expiring token, with no request sent; the fallback to the empty-account entry and its message; a 401 with the same token (state saved, exit 1, no retry); a 401 after a renewal (one retry with the new token, and no second retry if that is refused too); a 401 after which the entry is expired or missing; and a 401 after a tool turn, which keeps that turn so `--continue` picks the task up. In every run only `find-generic-password` is called. Whether starting `claude` renews an expiring login by itself was not checked here.

## Not changed here

No headers changed, nothing moved onto Claude Code, and the truth detector and the other files above are untouched. Each one still does what its row says.
