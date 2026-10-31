# YOUSPEAK ON/OFF test v0

A paired test of whether the YOUSPEAK rule (`youspeak-rule.mjs`) changes what Claude writes. Each synthetic prompt is sent twice through the Claude Code installed on the device, once with the rule in the system prompt (ON) and once without (OFF), with everything else the same. The plan, the endpoints, the caps and the stop rules are fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md), written before any run.

**Status:** R0 only. The registration, the prompt set, the harness, the analysis and their tests are here, and the dry run passes at $0. No live call has been made. The live pilot and main phase (PREREGISTRATION.md §10) are a later step started by hand.

## Files

| File | What |
|---|---|
| [`PREREGISTRATION.md`](PREREGISTRATION.md) | the question, the arms, the endpoints, the analysis, the caps, the stop rules and the limits |
| [`data/prompts.jsonl`](data/prompts.jsonl) | 72 synthetic prompts, 12 in each of six strata (`factual`, `explanation`, `code`, `planning`, `emotional`, `cantonese`); 18 carry a `check` pattern |
| [`provenance.json`](provenance.json) | where the prompts came from, privacy and licence |
| [`contamination-receipt.json`](contamination-receipt.json) | a lexical check of the prompts against the repository's private-context files |
| [`onoff.mjs`](onoff.mjs) | the arms, the phases, the stop rules, the seeded order and the statistics |
| [`harness.mjs`](harness.mjs) | runs a phase through `runClaudeCode`, one call at a time |
| [`analyze.mjs`](analyze.mjs) | the paired statistics from a phase's raw outputs, and an optional blind reading packet |

Each line of `prompts.jsonl` is canonical JSON with sorted keys: `id`, `stratum`, `input`, and optionally `check` (a case-insensitive regular expression the right answer matches) and `tags` (`uncertain`, `status`, `creative`).

## Run the dry run

From the repository root:

```bash
node benchmarks/youspeak-onoff-v0/harness.mjs --phase pilot
node benchmarks/youspeak-onoff-v0/analyze.mjs --phase pilot --dry-run
```

Without `--live`, the harness runs every prompt through `tests/fixtures/replay-claude.mjs`, which plays the recorded Claude Code stream and makes no network call; `--fake fake` uses `tests/fixtures/fake-claude.mjs` instead. Every answer in a dry run is the stand-in's, and its costs are the stand-in's figures, not spend.

Raw outputs go to `~/.local/share/youi/onoff/<run-id>/<phase>/` (`<run-id>-dry-run` for a dry run), or under `--out` / `YOUI_ONOFF_DIR`; a directory inside the repository is refused. There:

- `plan.json`: the seed, the order, the caps and what the run was registered against;
- `calls.jsonl`: one line per call, with the answer text, Claude Code's reported figures and its rate-limit reports (raw: never committed);
- `summary.json`: aggregates only, labelled reported or estimated;
- `analysis.json`: the paired statistics, aggregates only;
- `blind/`: the optional reading packet and its key (`--blind N`).

## The live phases

`--live` runs the installed Claude Code and spends plan limits. It refuses unless the registration (this directory, `youspeak-rule.mjs` and `youi-web/claude-code.mjs`) is committed and Claude Code meets YOUI's version floor for the model. See PREREGISTRATION.md §8 for every stop rule and §10 for the order: the Haiku 5.5 pilot first (cap US$1), then Opus 5.5 at effort high (cap US$15), US$20 in all.

```bash
node benchmarks/youspeak-onoff-v0/harness.mjs --phase pilot --live
node benchmarks/youspeak-onoff-v0/analyze.mjs --phase pilot
node benchmarks/youspeak-onoff-v0/harness.mjs --phase opus --live --pairs <nextPairs from the pilot>
```

A phase that stopped is continued with `--resume`; after the main phase's interim look, with `--resume --past-interim`.

## Tests

`tests/test_youspeak_onoff.mjs`, with stand-ins only: the arms differ only by the rule, every stop rule fires, the caps cannot be raised, the interim look and resuming behave as registered, the dry run completes and writes aggregates only, and nothing is written inside the repository.

## Licence

Code and data are under the repository's Apache-2.0 [`LICENSE`](../../LICENSE).
