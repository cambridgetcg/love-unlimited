# YOUSPEAK history records

These two files are YOUSPEAK's records from before slice Y1, which lived at the repository root. They are kept here as history, not as current truth: nothing writes them, and nothing in them describes how YOUI behaves now. The current observer and its per-user store are described in [`../YOUSPEAK.md`](../YOUSPEAK.md).

Both were moved here unchanged on 2026-10-31 from `youspeak-history.json` and `youspeak-config.json` at the repository root. Git keeps their earlier history under the old names.

## `youspeak-history-2026-04-13-to-06-19.json`

An object with two keys, `sessions` (178 rows) and `mutations` (1 record). The rows run from 2026-04-13T03:28Z to 2026-06-19T11:16Z; 177 have agent `alpha` and one `glm-5.1`. The rows are not one continuous run: the first 177 (2026-04-13 to 04-17) were written by the kernel to a per-user file outside the repository and migrated here in `d570339` (2026-06-19) with 23 empty rows removed; the last row (2026-06-19, `glm-5.1`) was written to the root file and committed in `15ab6d7`. Nothing falls between 04-17 and 06-19.

**What a row is.** Before Y1 the kernel appended a row each time it saved: after every YOUI Web message, and once per terminal session or sovereign run. It kept the last 200. A row is a snapshot of the session so far with no session id, so a session saved more than once appears many times, with its counts growing. Trends drawn from these rows count one session many times.

**The fields.** `timestamp`, `agent`, `elapsed`, `turns`, `outputGrade`, `usefulRatio`, `thinkingTokens`, `thinkAvgRatio`, `toolCalls`, `redundantReads`, `toolErrors`, `contextPeakTokens`, `budgetBurned`, `rateLimitHits`, `signalCount`. Two need reading with care:

- `budgetBurned` is not money. It is how many percentage points the account's 5-hour window moved during the session, stored as a string such as `"0%"`. It can be negative (28 rows are), and one row has `null`.
- `outputGrade` is the old S–D letter scale over the L1 useful ratio: S from 0.98, A from 0.95, B from 0.85, C from 0.70, D below. Y2 dropped it. These rows hold only A (25) and S (153).

**The `mutations` record** lists the three proposals the June ouroboros made on 2026-06-18 and marked `applied` on 2026-06-19: `add_cache_reminder`, `dynamic_effort` and `enable_pacing`. What each one did is below.

## `youspeak-config-2026-06-19.json`

Written by `youspeak-evolve.mjs integrate --apply` in commit `5986a93` (2026-06-19). It holds two of those proposals, `dynamic_effort` and `enable_pacing`, each with an `appliedAt` time. No code ever acted on this file (only its own writer read it back, to add to it), so neither took effect.

The third proposal, `add_cache_reminder`, did land, but not here: it was spliced into `sovereign.mjs` as the "File cache" line. Since Y2 it is `YOUSPEAK_FILE_CACHE_LINE` in `youspeak-rule.mjs`, still added only by sovereign. [`../YOUSPEAK.md`](../YOUSPEAK.md) §5 has the detail.

## Who reads them now

Only the history file is read, by `readLegacyHistory` in `youspeak-kernel.mjs` (`LEGACY_HISTORY_PATH` points here). It is summarised as "legacy" in `/api/youspeak` (`store.legacy`), the terminal's `/youspeak`, `node youspeak-kernel.mjs trends` and `node youspeak-evolve.mjs history`, and is never mixed into the store's trends. Nothing reads the config file.

## The old root paths

Code from before Y1 writes both files into the directory it is started from: `youspeak-history.json` after every YOUI Web message and once per terminal session or sovereign run, and `youspeak-config.json` when `youspeak-evolve.mjs integrate --apply` runs. Those writes never reach the files here.

In a checkout that has this change, both names are in `.gitignore`, so a file older code writes there (a YOUI still running after its checkout moved forward, or old code started from inside this checkout) stays untracked and cannot be committed by accident. A checkout still at a commit before this change tracks the root `youspeak-history.json` under its own `.gitignore`, and can still commit it until it moves past this change.
