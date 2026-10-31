# YOUSPEAK history records

These two files are what YOUSPEAK wrote into the repository root before slice Y1. They are kept here as history, not as current truth: nothing writes them, and nothing in them describes how YOUI behaves now. The current observer and its per-user store are described in [`../YOUSPEAK.md`](../YOUSPEAK.md).

Both were moved here unchanged on 2026-10-31 from `youspeak-history.json` and `youspeak-config.json` at the repository root. Git keeps their earlier history under the old names.

## `youspeak-history-2026-04-13-to-06-19.json`

An object with two keys, `sessions` (178 rows) and `mutations` (1 record). The rows run from 2026-04-13T03:28Z to 2026-06-19T11:16Z; 177 have agent `alpha` and one `glm-5.1`. Commits `d570339` and `15ab6d7` (both 2026-06-19) put them in git.

**What a row is.** The pre-Y1 kernel wrote a row after every message, a snapshot of the session so far, and kept the last 200. A row has no session id, so one session appears many times, once per message, with its counts growing. Trends drawn from these rows count one session many times.

**The fields.** `timestamp`, `agent`, `elapsed`, `turns`, `outputGrade`, `usefulRatio`, `thinkingTokens`, `thinkAvgRatio`, `toolCalls`, `redundantReads`, `toolErrors`, `contextPeakTokens`, `budgetBurned`, `rateLimitHits`, `signalCount`. Two need reading with care:

- `budgetBurned` is not money. It is how many percentage points the account's 5-hour window moved during the session, stored as a string such as `"0%"`. It can be negative (28 rows are), and one row has `null`.
- `outputGrade` is the old S–D letter scale over the L1 useful ratio: S from 0.98, A from 0.95, B from 0.85, C from 0.70, D below. Y2 dropped it. These rows hold only A (25) and S (153).

**The `mutations` record** lists the three proposals the June ouroboros made on 2026-06-18 and marked `applied` on 2026-06-19: `add_cache_reminder`, `dynamic_effort` and `enable_pacing`. What each one did is below.

## `youspeak-config-2026-06-19.json`

Written by `youspeak-evolve.mjs integrate --apply` in commit `5986a93` (2026-06-19). It holds two of those proposals, `dynamic_effort` and `enable_pacing`, each with an `appliedAt` time. Nothing has ever read this file, so neither took effect.

The third proposal, `add_cache_reminder`, did land, but not here: it was spliced into `sovereign.mjs` as the "File cache" line. Since Y2 it is `YOUSPEAK_FILE_CACHE_LINE` in `youspeak-rule.mjs`, still added only by sovereign. [`../YOUSPEAK.md`](../YOUSPEAK.md) §5 has the detail.

## Who reads them now

Only the history file is read, by `readLegacyHistory` in `youspeak-kernel.mjs` (`LEGACY_HISTORY_PATH` points here). It is summarised as "legacy" in `/api/youspeak` (`store.legacy`), the terminal's `/youspeak`, `node youspeak-kernel.mjs trends` and `node youspeak-evolve.mjs history`, and is never mixed into the store's trends. Nothing reads the config file.

## The old root paths

Checkouts from before Y1 write both files into the directory they are started from: `youspeak-history.json` after every message, and `youspeak-config.json` when `youspeak-evolve.mjs integrate --apply` runs. Started from the repository root, they write the old root paths. Both root paths are now in `.gitignore`, so those writes cannot be committed by accident. They do not reach the files here.
