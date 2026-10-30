# YOUSPEAK, rebuilt as an honest observer

**Status:** agreed by Yu with Touch ID on 2026-10-30 (proposal `report_0de85559`). Slice Y1 (§3) is built on branch `feat/youi-youspeak-y1-20261030`; [`YOUSPEAK.md`](YOUSPEAK.md) describes the code after it. Y2 (§4) is not built.

**Date:** 2026-10-30

**Branch:** `feat/youi-youspeak-20261030` (from the Claude Code route, C2 `dea8368`)

YOUSPEAK in YOUI is the Discipline: say what matters with no filler, never compress doubt, and expand when teaching or when feelings are in it. It is not the YOUSPEAK Cathedral at `~/YOUSPEAK`. That README says the two are "two distinct artifacts … Do not conflate them", and they point opposite ways: the Discipline compresses, the Cathedral is "engineered for expansion". This design keeps the Discipline's concept and rebuilds the mechanics behind it.

## 1. Why: what a 49-agent deep dive found (2026-10-30)

Six readers, a merge, two adversarial verifiers per issue and a completeness pass covered 52 issues. Of the 18 verified: 14 confirmed, 2 contested, 2 refuted.

**What works**
- The prompt rule is the only part that reaches the model, and it is sound. It protects hedging and has an escape for emotional and creative work.
- The kernel makes no model calls.
- Persisted rows hold aggregates only.

**What does not**
- **The kernel acts on its own.**
  - It lowers Alpha's effort when the account-wide 5-hour use passes 95%, and never restores it (`server.mjs:3606-3610`, `youi.mjs:1299-1304`).
  - It rewrites earlier history in place (`server.mjs:3611-3616`). On Claude 5-series models this risks the preserved-thinking check.
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
  - YOUI stops writing the tracked `youspeak-history.json` and leaves its 180 rows as a historical record, for Yu to decide about.
- **Bugs.**
  - Short text no longer produces NaN.
  - A corrupt store is reported, never reset.
  - The dead `<soulDir>/YOUSPEAK.md` include is removed.
- **Ouroboros, proposal-only.**
  - `youspeak-evolve.mjs` reads the new store and prints proposed diffs with their full text.
  - It never writes source, config or history; `--apply` is removed.
  - `youspeak-config.json` is no longer written. `docs/YOUSPEAK.md` records that the June "applied" entries never took effect, and why.
- **Docs.** `docs/YOUSPEAK.md` is rewritten to match the code, and points to the Cathedral's "do not conflate" note.

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

## 5. Boundaries

- YOUSPEAK suggests and records; it authorises nothing and changes no setting.
- The store holds aggregates only: counts, token figures, cost, grades and signals. It never holds message text, file paths or tool inputs.
- The terminal and `sovereign.mjs` change only where YOUSPEAK touches them: the automatic actions go, and the shared rule module comes in. Their move to Claude Code is a separate question.
- The CI ouroboros stays read-only.

## 6. Slices

| Slice | What | Needs |
|---|---|---|
| Y1 | Suggest only, the per-session untracked store, the bug fixes, the proposal-only ouroboros, the docs | this design agreed |
| Y2 | Sensors on reported usage, evidential labels, the cache-stability signal, the volatile lines moved, minds and tools fed, one rule module | Y1, and the recorded live stream |

Each slice is a draft PR. Tests use fakes, except the one recorded stream.

## 7. Open questions for Yu (not decided here)

- Should the rule's effect ever be measured with a real ON-vs-OFF test? That spends model calls.
- Should the YOUI terminal and sovereign follow YOUI Web onto Claude Code?
- What should happen to the tracked `youspeak-history.json`: keep it as history, or remove it from git?
- Should the Cathedral's `discipline/` copies of the kernel be synced, or marked historical?
