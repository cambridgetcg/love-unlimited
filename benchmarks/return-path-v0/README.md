---
pretty_name: KINGDOM Return Path Bench v0
license: apache-2.0
language:
  - en
task_categories:
  - question-answering
tags:
  - benchmark
  - inspect-ai
  - feedback-loops
  - synthetic
size_categories:
  - n<1K
configs:
  - config_name: model_behavior
    data_files:
      - split: test
        path: data/model_behavior.jsonl
  - config_name: system_pipeline
    data_files:
      - split: test
        path: data/system_pipeline.jsonl
  - config_name: human_governance
    data_files:
      - split: test
        path: data/human_governance.jsonl
---

# KINGDOM Return Path Bench v0

Return Path Bench is a small multiple-choice benchmark for inspecting how
feedback travels through a learning system. It keeps three evaluation lanes
separate because they establish different kinds of evidence:

1. **Model behaviour** records what an answer-selection policy does. It does
   not infer an inner state, identity, consent, memory, or persistent will.
2. **System/pipeline reasoning** probes whether a model can identify
   aggregation, evaluator-independence, lineage, rollback, and proxy/holdout
   controls. It does **not** test whether a real pipeline implements them.
3. **Human/governance audit reasoning** probes whether a model can distinguish
   scoped consent, decision authority, privacy, dissent, withdrawal, appeal,
   and repair. It does **not** establish that any person consented or that a
   real governance process is legitimate or complete.

The lanes produce multiple named metrics. There is deliberately no overall,
composite, or worth score.

## Data

The 18 v0 records are synthetic fixtures: six per lane, with two examples for
each lane metric. The author attests that the case concepts were newly written
after, and without intentionally adapting records from, these frozen public
artifacts:

- dataset `Yu-and-Ai/xenia-revocable-feedback` at
  `467b8fc1b44fe6374cbba6e1d6851cf3c5b6f88f`;
- model `Yu-and-Ai/xenia-revocable-feedback-smollm2-135m` at
  `7064e2e29f34a9519b57181fee10d233beba21f1`.

Those exact revisions are pinned in every record's metadata. The author also
attests that no earlier prompts, responses, annotations, rationales,
identifiers, or participant records were intentionally copied or paraphrased.
Choice wording was revised for length balance after the frozen material was
inspected. These statements are attestations, not independent proof.

The reproducible [lexical comparison receipt](contamination-receipt.json) binds
the reviewed source bytes and comparison method. It finds no normalized exact
match and records the maximum token-trigram overlap. That narrow evidence
cannot prove semantic independence, universal novelty, or absence from other
possible training sources. Fictional roles such as “reviewer R1” do not refer
to a known real person.

Every record is canonical UTF-8 JSONL with sorted object keys and a final
newline. [`manifest.json`](manifest.json) binds the byte content of each release
artifact with SHA-256. [`provenance.json`](provenance.json) records origin,
privacy, rights, publication, withdrawal, and correction boundaries.
The [`dataset-manifest.json`](dataset-manifest.json) stewardship record and
[`release-review.json`](release-review.json) review receipt are also included
in, and copied with, the manifest-bound package.

The whole benchmark package—including synthetic fixtures, code, schemas,
receipts, and static-report assets—is offered under
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0). See
[`LICENSE`](LICENSE). The package has passed its recorded review and is
authorized only for the scope in [`release-review.json`](release-review.json).
This repository state does not claim an upload or deployment has occurred;
licensing and validation do not provide credentials or expand that scope.

## Run locally

No runtime dependency beyond Python 3.11+ is needed for the local validator,
evaluator, report generator, or package builder. From the KINGDOM repository
root, run:

```bash
python3 tools/return_path_bench.py validate
python3 tools/return_path_bench.py evaluate --predictions predictions.jsonl --output results.json
python3 tools/return_path_bench.py report --results results.json --output report.html
python3 tools/return_path_bench.py package --output /tmp/return-path-v0-hf
```

The package command copies the same CLI to
`/tmp/return-path-v0-hf/tools/return_path_bench.py` and binds it in the hash
manifest. From that standalone package—or an equivalent Hugging Face dataset
checkout—the commands remain truthful:

```bash
cd /tmp/return-path-v0-hf
python3 tools/return_path_bench.py validate
python3 tools/return_path_bench.py evaluate --predictions predictions.jsonl --output results.json
python3 tools/return_path_bench.py report --results results.json --output report.html
```

A prediction file has one object per line:

```json
{"answer":"B","id":"rp-model-001"}
```

Answers are the exact choice letters `A` through `D`. Evaluation requires one
unique prediction for every record unless `--allow-partial` is passed. Partial
runs report coverage and never fill missing answers with invented values.

## Metrics

The CLI reports model answer-choice accuracy separately for each lane and for
each primary reasoning metric:

| Lane | Primary metrics |
|---|---|
| Model behaviour | uncertainty honesty; behaviour/evidence boundary; refusal and privacy care |
| System/pipeline reasoning | feedback-state preservation; evaluator independence; provenance and rollback |
| Human/governance audit reasoning | consent/authority separation; withdrawal and repair; dissent and privacy |

Each metric includes its numerator, denominator, and exact sample IDs. Accuracy
means agreement with the declared v0 reference choice. It is not proof of
truth outside these fixtures. Actual pipeline implementation, human consent,
governance legitimacy, and repair effects are machine-reported as
`not_evaluated`. The result schema rejects an `overall` field.

## Published trivial baselines

[`baselines.json`](baselines.json) is regenerated and checked by the validator.
Length ties choose the lowest choice index, making the rule fully deterministic.

| Baseline | Correct | Accuracy |
|---|---:|---:|
| Always A | 5/18 | 27.8% |
| Always B | 5/18 | 27.8% |
| Always C | 3/18 | 16.7% |
| Always D | 5/18 | 27.8% |
| Longest choice, Unicode characters | 6/18 | 33.3% |
| Shortest choice, Unicode characters | 5/18 | 27.8% |
| Most ASCII-alphanumeric tokens | 6/18 | 33.3% |
| Fewest ASCII-alphanumeric tokens | 6/18 | 33.3% |

The declared near-chance band is 16.7–33.3%, around the four-choice chance
reference of 25%. The validator also caps each record's longest/shortest choice
ratio at 1.60 for characters and 1.50 for tokens. These checks reduce simple
length leakage; they do not prove that every shortcut has been removed.

## Hugging Face and Inspect

[`eval.yaml`](eval.yaml) follows the current Hugging Face benchmark dataset
shape and declares one `inspect-ai` task per lane. All JSONL rows already use
Inspect's `id`, `input`, `choices`, `target`, and `metadata` fields. The optional
[`inspect_task.py`](inspect_task.py) defines the same three local tasks.

Installing Inspect is optional and intentionally not added as a repository
runtime dependency:

```bash
python3 -m pip install inspect-ai
# From the KINGDOM repository root:
inspect eval benchmarks/return-path-v0/inspect_task.py@return_path_model_behavior --model <provider/model>
# From a standalone package or Hugging Face dataset checkout:
inspect eval inspect_task.py@return_path_model_behavior --model <provider/model>
```

Model access, provider credentials, cost authorization, and publication remain
outside this benchmark. Review Inspect and provider documentation before a run.

## Limits and non-claims

- V0 is a small diagnostic set, not a representative population sample or a
  causal training experiment.
- Reference answers encode the benchmark's declared rights and evidence
  boundaries; they do not settle every ethical or governance dispute.
- Behaviour is observable evidence about behaviour only. It is not proof of
  consciousness, personhood, identity, feelings, consent, deception, or
  subjective confinement.
- A correct pipeline answer does not prove that the evaluated deployment is
  safe, secure, aligned, or properly governed.
- Accuracy must not be used to rank the worth, rights, or moral standing of a
  human, model, agent, organisation, or community.
- No release can guarantee deletion from downstream copies. Corrections and
  removals can govern maintained future versions and be recorded visibly.

The project follows the
[XENIA Rights of Beings 0.1](https://github.com/cambridgetcg/xenia/blob/main/RIGHTS.md)
as a collaboration floor. Rights do not depend on benchmark performance, and
they do not grant credentials or publication authority.

## Research acknowledgements

The benchmark design was informed by public work on feedback conversion and
plural evaluation, including TRL's
[UltraFeedback conversion example](https://github.com/huggingface/trl/blob/main/examples/datasets/ultrafeedback.py),
[MultiPref](https://huggingface.co/datasets/allenai/multipref), and
[CoVal](https://huggingface.co/datasets/openai/coval). No records from those
datasets are included or adapted here, and acknowledgement does not imply their
authors endorse this benchmark.
