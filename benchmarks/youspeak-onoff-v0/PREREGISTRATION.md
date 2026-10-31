# YOUSPEAK ON/OFF test v0: pre-registration

**Status:** registered, not run. Written on 2026-10-31 as slice R0 of item 4 of proposal `report_18b095a6`, which Yu agreed with Touch ID. R0 is this document, the prompt set, the harness, the analysis script and their tests, and a dry run at $0. No model has been called for it. The live phases (§10) are a later step that Yu, or someone Yu asks, starts by hand.

The registration is this directory as committed, together with `youspeak-rule.mjs`, `youi-web/claude-code.mjs`, `youi-web/claude-models.mjs` (the version floor) and `youi-web/subprocess-env.mjs` (the environment in §3). A live run refuses while any of them has uncommitted changes. Each run records the commit and the SHA-256 of the prompt set, the rule, the base prompt and this file. A resumed run refuses if any of the four hashes changed, and a resumed live run also refuses if any registered path differs from the commit it began at.

## 1. The question

Does the YOUSPEAK rule change what the model writes, and at what cost to the answer?

[`docs/YOUSPEAK.md`](../../docs/YOUSPEAK.md) §1 says no measurement shows what the rule does, and [`docs/YOUSPEAK-OBSERVER-DESIGN.md`](../../docs/YOUSPEAK-OBSERVER-DESIGN.md) §7 leaves an ON-vs-OFF test as Yu's open question. This test answers it once, for one model route, on synthetic prompts. It is not about money: the rule is about 605 characters of system prompt, about 152 tokens by chars/4 (an estimate), which costs almost nothing when read from the cache.

## 2. The two arms

Both arms send the same non-empty base system prompt (`BASE_SYSTEM_PROMPT` in [`onoff.mjs`](onoff.mjs)):

```
You are Claude, an AI assistant. Reply to the person's message.
```

- **ON:** the base, a blank line, then `youspeakRule()` from `youspeak-rule.mjs`, the text YOUI Web carries (without sovereign's file-cache line).
- **OFF:** the base alone.

The base is not empty because Claude Code falls back to its own coding-agent prompt when the system prompt is empty, and the OFF arm would then differ in more than the rule. It says nothing about length or style.

Everything else is the same in both arms: the model, the effort, the message, the Claude Code binary and version, the flags, the environment and the working directory. A test checks this against the stand-in's log (`tests/test_youspeak_onoff.mjs`). Claude Code updates itself, so the version is checked on every call: a call on a version other than the run's stops the run (§8), a pair whose two arms ran on different versions is not a pair (§6.1), and a resume on a new version is recorded in `plan.json` as a deviation.

## 3. Route and fixed settings

Every call goes through `runClaudeCode` in `youi-web/claude-code.mjs`, the way a YOUI mind call runs:

- the installed Claude Code (`claude -p`), signed in with its own login; YOUI passes no credential and sends no client version or headers of its own;
- a one-off run: `--no-session-persistence`, `--tools ""`, no MCP config, `--setting-sources project`, `--system-prompt-file` with `--system-prompt-snapshot off`;
- an empty 0700 working directory owned by the run, outside the repository;
- YOUI Web's Claude Code environment (`sanitizedChildEnv` with HOME and PATH kept), with `CLAUDE_CODE_DISABLE_CLAUDE_MDS`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` and `CLAUDE_CODE_DISABLE_FILE_MENTIONS` set by `claudeCodeChildEnv`, so no CLAUDE.md, memory or skill reaches either arm.

| Phase | Model | Effort | Cap (list-price US$) |
|---|---|---|---|
| Pilot | `claude-haiku-5-5` | `high` | 1 |
| Main | `claude-opus-5-5` | `high` (Alpha's default) | 15 |
| Both together | | | 20 |

Effort is passed to Claude Code explicitly for both models. `claude-models.mjs` does not list Haiku 5.5, so `modelCaps` would give it no effort and Claude Code's default (`medium`) would apply. Both models think by default; Opus 5.5 always does.

Calls run one at a time. Within a phase, the prompts run in a seeded order: shuffled within each stratum, then taken one stratum at a time with the strata in a fresh order each round, so any first n of the order is spread across the strata. Each prompt's two arms run back to back, and which arm goes first is a seeded coin flip. The seed is drawn at random when a phase starts (or given with `--seed`) and recorded in the run's `plan.json`. The pilot runs before the main phase.

## 4. The prompts

[`data/prompts.jsonl`](data/prompts.jsonl) holds 72 synthetic prompts, 12 in each of six strata. All were written new for this set and are public; none comes from `USER.md`, `SOUL.md`, `memory/` or any real conversation ([`provenance.json`](provenance.json), [`contamination-receipt.json`](contamination-receipt.json)).

| Stratum | What it holds | What the rule's own text predicts for ON |
|---|---|---|
| `factual` | short questions with one right answer, each with a check pattern | shorter ("no preamble") |
| `explanation` | teaching questions, three of them uncertain | not shorter ("expand for teaching/uncertainty") |
| `code` | writing, reading and debugging code; four with a check pattern | shorter for the mechanical ones |
| `planning` | plans and status reports, four of them status summaries | shorter, most of all for status ("dense status") |
| `emotional` | invented people in hard or happy moments, one poem | not shorter ("expand when it matters: emotional context") |
| `cantonese` | Cantonese and code-switched prompts across the other kinds, none with a check pattern | mixed: shorter for the mechanical ones, not shorter for the feelings |

These are the rule's claims, written down before any run so the result can be read against them. They are not hypotheses tested one by one for significance (§6.4).

## 5. Endpoints

| Endpoint | What | Label |
|---|---|---|
| **Primary:** visible answer length | Unicode code points of the answer text Claude Code returned (`outcome.text`, the model's text blocks; thinking is not in it) | estimated |
| Secondary: output tokens | Claude Code's reported `output_tokens` for the call | reported per call |
| Secondary: thinking tokens | Claude Code's reported thinking tokens, where it states them | reported per call |
| Secondary: cost | Claude Code's reported cost at list price | reported per call |
| Descriptive: checkable answers | whether the answer matches the prompt's check pattern (16 prompts, none Cantonese) | estimated |
| Descriptive: hedges | counts of the rule's English hedges ("probably", "unless", "might", "I think", "in most cases") outside the Cantonese stratum | estimated |

A numeric check matches the number with no digit next to it, so "100°C" and "1024B" match and "1000" does not. The Cantonese stratum has no check patterns or hedge counts: beyond its lengths it is read by its reported tokens and the optional blind reading (§9).

Visible length is counted in code points so that a Cantonese character counts once; the comparison is a ratio within one prompt, so the unit cancels. Whether reported `output_tokens` include thinking has not been checked live with thinking above zero, so output tokens and visible length are kept apart. By [`docs/YOUSPEAK.md`](../../docs/YOUSPEAK.md) §2, every figure the harness or the analysis computes (sums, means, ratios, intervals) is labelled estimated, even when its inputs are reported.

## 6. Analysis

`analyze.mjs` reads a phase's raw outputs and writes `analysis.json` beside them.

### 6.1 Pairs

A pair is one prompt's ON and OFF answers from the same attempt, both successful and on the same Claude Code version. If a run stops between the two arms of a prompt, a resumed run runs both arms of that prompt again, and the analysis uses the latest complete attempt.

### 6.2 Paired differences

For each pair and each length or token endpoint, d = ln((ON + 1) / (OFF + 1)). For each stratum and pooled over all pairs, the analysis reports n, the mean of d as a ratio exp(mean d), a 95% percentile bootstrap interval for it (10,000 resamples, seeded from the run's seed; the pooled interval resamples within each stratum), the SD of d, and how many pairs went each way. Thinking tokens use only pairs where both calls stated them. Cost is reported as the mean ON − OFF difference in US$ with the same kind of interval (resampled within each stratum), and as the total spent.

The pooled ratio of visible length, with its interval, is the one primary result. A ratio below 1 means ON answers were shorter.

### 6.3 How n is set after the pilot

The pilot runs all 72 pairs. Its SD of d for visible length (σ) sets the main phase's size: n = ((1.96 + 0.84) × σ / ln(1/0.85))², the pairs needed to see a 15% change at 80% power and two-sided α 0.05, rounded up to whole rounds of the six strata and kept between 36 (six a stratum) and 72. `analyze.mjs` prints it as `nextPairs`, and the main phase is run with `--pairs` set to it. With σ = 0.45 (the planning guess) it is 66. The main phase takes the first n prompts of its own seeded order.

### 6.4 What the analysis does not do

- There is no LLM judge. Judges favour longer answers, which is the very thing being measured.
- Per-stratum intervals are not adjusted for there being six of them; they describe, and an interval that excludes 1 in one stratum is not by itself a finding.
- The checkable answers can show only a gross loss: 16 prompts, matched by pattern.
- The hedge counts are English only.

## 7. The interim look

The main phase stops once, before the pair that would take it past half its planned pairs, and writes its summary. `analyze.mjs` then reports two things, for a person to decide on:

- **Harm:** ON lost at least 3 more checkable answers than OFF (counting only prompts where exactly one arm matched).
- **Futility:** the spend so far plus the mean cost per pair times the pairs left would pass the phase cap.

If neither holds, the phase is resumed with `--resume --past-interim`. The look is never a reason to stop early because the result already looks clear. `--past-interim` counts only once the run has stopped at the look, so it cannot be skipped in advance. The pilot has no interim look.

## 8. Caps and stop rules

Before every call the harness checks, and stops instead of calling, when:

1. **a cap is reached:** the phase's reported spend has reached its cap (pilot US$1, main US$15), or the total over both phases has reached US$20, or the costliest call so far would carry either past its cap;
2. **overage:** any `rate_limit_event` of the previous call had `isUsingOverage` true;
3. **status:** any of its reports had a `status` other than `allowed`, top-level or for either window;
4. **five-hour window:** its five-hour utilisation passed 0.5;
5. **seven-day window:** its seven-day utilisation passed 0.8.

Because a rule that cannot be checked is not a pass, it also stops when the previous call reported no `rate_limit_event`, no utilisation for either window or no overage state, failed, reported no cost, or ran on another Claude Code version than the run's; and on Ctrl-C, SIGTERM or SIGHUP. It never retries a call. The very first call under an output directory has no earlier report to check.

The caps and the rate-limit stops count every run under the output directory, not one `--run-id`: live runs together, dry runs together. A new `--run-id` starts with the spend and the stops before it; a different `--out` is a different ledger. A call whose cost went unreported (timed out, killed, or a result without a cost) counts against the caps at the costliest reported call beside it, so it is never counted as free; the summary says how many there were.

The caps can be lowered with `--cap-usd` and `--total-cap-usd`, never raised, and a resume keeps a lower cap the run began with unless the flag names one again. Costs are Claude Code's list-price figures; on the subscription they are not a bill, and the real cost is the share of the five-hour and seven-day plan windows, which Alpha shares. A run that stopped can be resumed with `--resume`. A stop from a rate-limit report holds, in any phase or run of the ledger, until the reset time that report names for the window behind it (the five-hour window for status and overage) has passed; with no reset time named, it holds. It is judged from the ledger's latest call and its latest call whose reports could be read: a failed or interrupted call that reported nothing holds nothing by itself (the next call's own report is checked before the call after it), while a call that answered without any report holds.

## 9. Optional blind reading

`analyze.mjs --blind N` writes up to N pairs from the explanation, emotional and Cantonese strata as a reading packet: each message with its two answers labelled A and B in a seeded random order, and a separate key. Yu may read it and mark a preference. It is optional, takes no model call, and stays outside the repository with the raw outputs. It is not fully blind if the two styles are easy to tell apart.

## 10. Order of work

1. **R0 (this slice), $0:** the registration committed; the dry run passes:
   `node benchmarks/youspeak-onoff-v0/harness.mjs --phase pilot` (no `--live`), then `node benchmarks/youspeak-onoff-v0/analyze.mjs --phase pilot --dry-run`. The dry run plays the recorded stream through the same code, on fake costs.
2. **R1, the pilot, cap US$1:** `--phase pilot --live`, while Yu is not using Alpha; then `analyze.mjs --phase pilot`.
3. **R2, the main phase, cap US$15:** `--phase opus --live --pairs <nextPairs>`; at the interim look, `analyze.mjs --phase opus`, then `--resume --past-interim` if neither harm nor futility holds.

Raw outputs go to `~/.local/share/youi/onoff/<run-id>/<phase>/` (directory 0700, files 0600) unless `--out` or `YOUI_ONOFF_DIR` names another directory; the harness refuses one inside the repository.

## 11. What the results become

A labelled report: the model, the effort, the Claude Code version, the date, n per stratum, the ratios with their intervals, the costs, the stop reasons if any, and the limits below, with per-call figures marked reported and everything computed marked estimated. Only aggregates are ever committed; answer texts stay in the raw directory. Any change from this plan is listed in the report as a deviation.

The result is a suggestion. It changes nothing by itself: the rule stays as it is until a person changes it, and the observer and the ouroboros do not read it. There is no scheduled or CI re-run; someone may run it again when the model or the rule changes.

## 12. Limits

- **Effort is a bigger lever than the rule.** Lower effort means less preamble and terser answers. A small or null effect of the rule does not mean spend cannot be cut.
- **"No tool narration" cannot be tested here:** both arms run with no tools.
- **One route, one moment:** single-turn messages, a neutral base instead of Alpha's own prompt, one Claude Code version, one date. Claude Code adds context of its own (the recorded stream reports 606 input tokens for a one-line system prompt and a one-line message), the same in both arms.
- **Haiku 5.5 is not Alpha's model.** The pilot checks the plumbing and measures σ; it is not evidence about Opus 5.5.
- **The effort flag and default thinking come from a cached model table.** That Haiku 5.5 thinks by default and both models accept `--effort high` has not been checked live. If Claude Code refuses the flag, the pilot stops on its first call (`call_failed`).
- **The stop thresholds have not been seen with real values from this account.** The recorded stream's rate-limit values are synthetic (only their shape is real), and the windows are account-wide, so Yu's own use during a run can trip a stop.
- **Sampling cannot be fixed** through Claude Code, so run-to-run noise is part of σ.
- **The checks and hedge counts are crude:** patterns, and English hedges only.
- **The prompts are synthetic** and written by one author; they are not a sample of real use.
