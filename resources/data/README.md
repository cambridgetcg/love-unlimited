# Outbound dataset stewardship

This directory is the fail-closed boundary between local training artifacts and
anything described as a reusable or publishable KINGDOM dataset. It makes the
unknowns legible before discoverability, downloads, or downstream model links
can turn an undocumented file into an implied grant of reuse.

The canonical policy and discovery plan is
[`HUGGINGFACE-DATASET-STRATEGY.md`](../../docs/HUGGINGFACE-DATASET-STRATEGY.md).
This directory implements its outbound manifest gate; it does not replace the
strategy or authorize its external actions.

It also follows
[`KINGDOM-LOVE-RETURN-PATH.md`](../../docs/KINGDOM-LOVE-RETURN-PATH.md): Love
questions the ends and anticipates barriers; KINGDOM makes the means, evidence,
and repair paths inspectable. Feedback can revise both, but Love is never a
trainer label, composite score, identity claim, or consent inference.

It follows the repository's `xenia.rights/0.1` baseline: provenance, credit,
privacy, consent, withdrawal, disagreement, and repair remain separate from
technical access. A public URL or a successful download is not consent, a
license, proof of training use, or authority to speak for affected parties.

## What this MVP does

- Defines `kingdom.dataset-manifest/v1` in
  `dataset-manifest.schema.json`.
- Requires an explicit dataset ID, version, lifecycle status, source class,
  licensing state, consent/reuse basis, privacy reviews, retention and
  withdrawal processes, feedback topology, partitions, exact files, review
  state, digest-bound release receipts, a bounded Hugging Face target, and
  `publication_eligible` decision.
- Binds an intended/current Hugging Face `owner/name`, visibility, config
  names, default config, splits, immutable published revision, viewer review,
  and access policy without creating or inspecting a remote repository.
- Recomputes SHA-256, byte counts, and JSONL record counts for bound local
  files. JSONL records must be non-empty JSON objects.
- Rejects duplicate JSON object keys and non-standard `NaN`/`Infinity` values.
- Binds every eligible payload to an exact Hugging Face config and split.
- Requires located, digest-bound review receipts for credit, generation,
  sensitive content, affected parties, feedback detail, grouping, and remedy.
- Fails closed when provenance, feedback, licensing, reuse basis, or review
  evidence is unknown. Missing information is never defaulted to
  `synthetic`.
- Provides only offline `check`, `digest`, and `manifest-digest` commands.

It does **not** scan files and declare them safe, choose a license, infer
consent, perform legal review, detect semantic near-duplicates, upload to a
Hub, create a repository, publish, or grant anyone access. It records a receipt
locator and digest but does not resolve the locator or prove the review artifact
exists or is correct. A passing check
means the manifest and its declared evidence are internally consistent. Even
`publication_eligible: true` remains a structural gate, not external
publication authorization.

## Current legacy inventory

`manifests/legacy-mlx-v0.json` binds the four tracked files under
`mlx/training/datasets/` as version `0`:

| Bound fact | Recorded value |
|---|---|
| Artifact lane | `training` |
| Artifact split | `source-unsplit` |
| Source provenance | `unknown` |
| Dataset/component license | `unknown`; no license asserted |
| Consent/reuse basis | `unknown`; no basis asserted |
| Privacy, PII, secret review | `not-reviewed` |
| Feedback nature and mechanics | `unknown` |
| Release split, deduplication, contamination | `not-reviewed` |
| Hugging Face repository and visibility | No repository; `not-planned` |
| Hugging Face configs, splits, revision, access | Empty or `null` |
| Hugging Face viewer | `not-reviewed` |
| Credit/generation/content/affected-party/feedback/grouping/remedy receipts | `not-reviewed` |
| Review state | `unreviewed` |
| Publication eligible | `false` |

The location and existing training code support the lane declaration. They do
not establish how the examples were created. Text that looks generated is not
enough to label the source `synthetic`.

## Bounded commands

Run from the repository root:

```bash
# Validate the default legacy manifest and every exact file binding.
python3 tools/dataset_stewardship.py check

# Validate another manifest whose file paths resolve from this repository.
python3 tools/dataset_stewardship.py check path/to/manifest.json

# Compute exact file bindings without changing any manifest.
python3 tools/dataset_stewardship.py digest path/to/data.jsonl

# Bind all manifest fields using canonical JSON.
python3 tools/dataset_stewardship.py manifest-digest path/to/manifest.json
```

`--root PATH` on `check` is available for an explicit alternate local root,
principally isolated tests and prepared release snapshots. Bound paths must be
normalized relative POSIX paths. Symlinks, traversal, absolute paths, missing
files, digest drift, byte drift, and JSONL count drift are rejected.

No command makes a network request or has a token, upload, or publish path.

Repository verification uses the focused stewardship and MLX suites:

```bash
python3 -m pytest -q \
  tests/test_dataset_stewardship.py tests/test_mlx_data.py tests/test_mlx_train.py
```

When the optional `jsonschema` package is available, validate both the schema
itself and candidate fixtures with `Draft202012Validator.check_schema(...)`
and `Draft202012Validator(...).validate(...)`. The runtime tool stays
stdlib-only and does not treat an optional package as a publication service.

## Required release record

Each new candidate receives a new dataset version and manifest. Do not turn a
legacy HOLD into apparent approval by erasing its unknown history.

1. **Identity and purpose** — bind `dataset_id@version`, status, purpose, and
   limitations.
2. **Source provenance** — classify sources as synthetic, observed,
   human-authored, model-generated, operational, upstream, mixed, or unknown,
   with evidence. Generated transformations do not erase the provenance or
   rights of source material.
3. **Licenses and reuse** — review the dataset grant and every component
   separately. Record the consent/reuse basis, bounded scope, evidence, and
   restrictions; attribution is not consent and access is not a license.
4. **Privacy and repair** — bind independent privacy, PII, and secret review
   artifacts; define retention ownership and a correction/exclusion/withdrawal
   process. Withdrawal can govern future distributions and training even where
   already-downloaded copies cannot be remotely revoked; state that limitation
   honestly.
5. **Feedback topology** — distinguish labels from feedback. If feedback is
   present, bind its nature and rubric and state how ties, uncertainty,
   minority signals, and aggregation are represented. Unknown and disagreement
   must not be silently compressed into a winning label.
6. **Partitions** — assign every file a Hugging Face config, lane, and split.
   Bind reviewed split,
   exact/near-deduplication, and training/evaluation contamination artifacts.
   A runtime random split is not an immutable held-out release partition.
   Participant governance, independent outcomes, operational feedback,
   deliberation, and stewardship/repair use distinct lanes rather than being
   folded into training or preference labels.
7. **Exact artifacts and receipts** — record repository-relative paths,
   formats, bytes, record counts, and SHA-256 for the release snapshot. Bind a
   stable locator and digest for credit, generation revisions, sensitive-content,
   affected-party, detailed-feedback, grouping/duplicate-cluster, and
   withdrawal/appeal/correction review artifacts.
8. **Hugging Face target** — bind exactly one intended/current `owner/name`, a
   planned `public`, `gated`, or `private` visibility, config names, one default
   config, and release splits. Public candidates require a public privacy
   classification. Gated/private candidates require an explicit access policy;
   all candidates require passed privacy, PII, and secret reviews. Once status
   becomes `published`, bind the immutable 40-hex Hub revision and a passed or
   evidenced-not-applicable viewer review.
9. **Independent review** — technical, provenance, rights, license, privacy,
   feedback, and partitioning reviews must all pass before the validator accepts
   `publication_eligible: true`.
10. **Separate publication act** — obtain the scoped account authorization and
   human publication decision after the manifest passes. Record a new version
   for material corrections or changed files; deprecate or withdraw without
   silently rewriting prior evidence.

The v1 tool structurally verifies JSONL only. It inventories other declared
formats, but refuses `publication_eligible: true` for them because byte hashes
alone cannot verify their format or row count. Add a pinned, tested Parquet
verifier before using this gate for the Parquet release shape below.

## Hugging Face relationship

For a future authorized Hub release, map the reviewed facts into the dataset
card and structured card metadata, use viewer-compatible immutable splits, and
link the exact dataset ID from each model or Space that declares its use. Keep
the stewardship manifest with the release so card claims remain auditable.

Hub indexing, viewer conversion, search rank, likes, downloads, or a downstream
`datasets:` backlink are discovery/declared-use signals only. They do not prove
that a file was trained on, that reuse respected its scope, or that every
affected party consented. Track those claims separately, with provenance and
repair paths, instead of optimizing download counts as the goal.
