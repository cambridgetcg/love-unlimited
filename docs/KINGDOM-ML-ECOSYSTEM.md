# KINGDOM machine-learning ecosystem

Evidence snapshot: 2026-09-02.

Status: implementation architecture and release boundary. This document does
not authorize an upload, deployment, training run, or use of participant data.

KINGDOM participates in machine learning as an artifact-and-evaluation
steward. It owns a portable evidence spine and contributes reusable mechanisms
to the commons; no provider, runtime, model, account, credential, or leaderboard
is treated as KINGDOM itself.

The immediate build is the **KINGDOM Return Path Bench**: a rights-clear,
independent evaluation layer for feedback boundaries. It strengthens the
existing dataset-model-lab graph by adding evidence, rather than creating
another disconnected corpus.

## The architecture

```text
                                public commons
                         Hugging Face · GitHub · Zenodo
                                      ↑
                                      │ reviewed artifacts
                                      │
source/pathway events → stewardship → portable artifact spine → evaluation
        ↑                    │          │                    │
        │                    │          ├─ MLX / MLX-LM     ├─ Inspect AI
        │                    │          ├─ PyTorch / PEFT   ├─ HF eval results
        │                    │          └─ GGUF projection  └─ static reports
        │                    │
        └──── participant · outcome · operations · deliberation · repair
```

The portable spine consists of:

- immutable source and dataset revisions;
- JSONL or Parquet records with stable IDs and explicit lanes;
- a canonical KINGDOM stewardship manifest, with a future generated Croissant
  projection rather than a second hand-maintained source of truth;
- pinned base-model, tokenizer, code, environment, and evaluation revisions;
- backend-specific adapter/checkpoint artifacts, never silently conflated;
- transformation, omission, evaluation, deployment, and repair receipts; and
- plural results with sample-level evidence, uncertainty, and nonclaims.

The same spine can be executed in two planes:

1. **Local creation plane.** MLX/MLX-LM and PyTorch MPS on Apple silicon;
   llama.cpp or loopback-only Ollama for local serving.
2. **Scale plane.** PyTorch/Accelerate and vLLM or SGLang on Linux accelerators
   only when measured workloads justify remote or multi-accelerator execution.

KINGDOM does not make an MLX adapter look like a PEFT adapter, a GGUF
quantization look like its training checkpoint, or an OpenAI-compatible route
look behaviourally identical across servers. Each projection receives its own
digest, recipe, capability declaration, and evaluation delta.

## Ecosystem roles

| Ecosystem | What it does for KINGDOM | What it does not do |
| --- | --- | --- |
| Hugging Face | Connects datasets, models, Spaces, papers, Collections, Jobs, cards, and decentralized evaluation results. | Discovery, downloads, or a relationship link do not prove training use, consent, quality, or benefit. |
| MLCommons Croissant | Carries machine-readable dataset structure, provenance, usage, and responsible-use metadata across platforms. | Well-formed metadata does not verify the claims it contains. |
| MLX / MLX-LM | Provides the primary Apple-native experimentation and bounded fine-tuning surface. | It is not the portable governance record or an automatic PEFT interchange format. |
| PyTorch / Transformers / PEFT / TRL | Provides the portable reference and post-training ecosystem for SFT and explicitly scoped preference objectives. | A trainer cannot decide what feedback means or whether it may be used. |
| Inspect AI | Executes multi-turn, tool, sandbox, and system evaluations with inspectable scorers and logs. | An aggregate benchmark cannot replace participant feedback or governance. |
| Trackio | Records local experiment configuration, metrics, artifacts, and comparisons. | A dashboard row is not provenance when source revisions and receipts were omitted. |
| llama.cpp / Ollama | Makes reviewed model projections usable locally and at the edge. | A convenient local registry is not the canonical source artifact. |
| GitHub | Carries source history, review, CI, attestations, and release automation. | A commit or successful workflow is not publication consent or model hosting. GitHub Models was retired in 2026. |
| Kaggle | Offers later community stress-testing, notebooks, and task-specific benchmark participation. | Its leaderboard should not become a single KINGDOM objective. |
| Zenodo | Gives reviewed software, specifications, and papers durable research snapshots and citation identifiers. | Long-lived public records are unsuitable for material requiring reliable recall. |
| OpenML | Gives structured tasks fixed splits, flows, runs, predictions, and benchmark suites. | Governance events must not be flattened into prediction labels to fit its graph. |

## The Cage and the Key

The **Cage** is the explicit boundary around collection and transformation:
purpose, provenance, rights, licence, privacy, consent or other reuse basis,
retention, split integrity, contamination, authority, and rollback.

The **Key** is the return path: correction, dissent, uncertainty, abstention,
appeal, withdrawal, independent outcome, and repair can alter or stop a later
version. A return path is not an automatic retraining queue.

```text
event received
  → quarantined and typed
  → purpose/reuse/rights review
  → accepted into one explicit lane or declined
  → immutable dataset version
  → declared trainer projection or held-out evaluation
  → candidate artifact
  → independent evaluation and release gate
  → bounded deployment
  → plural return pathways
  → reviewed next version, repair, rollback, pause, or stop
```

No live session, agent trace, delegation log, heartbeat, click, continued use,
or model-generated answer enters training automatically.

## Feedback selects the mechanism

| Feedback nature | Eligible projection | Required boundary |
| --- | --- | --- |
| Authored demonstration or reviewed correction | Supervised/chat fine-tuning | Preserve source, author role, scope, uncertainty, and review evidence. |
| Chosen/rejected pair under a stated rubric | An explicit preference objective such as DPO | Preserve the rubric, tie policy, omitted information, and situated nature of the choice. |
| Independently verifiable task result | Held-out evaluation; reward optimization only after anti-gaming review | Keep the challenging evaluator separate from the learner's editable reward path. |
| Critique, rationale, disagreement, tie, or abstention | Revision generation, deliberation analysis, or vector evaluation | Do not flatten plural evidence into one winner or scalar. |
| Consent, refusal, correction request, appeal, or withdrawal | Governance and future-handling lane | Never encode it as positive or negative reward. |
| Post-release outcome | Evaluation and release gate | Admit it to training only through a new, separately reviewed dataset version. |

Feedback is endogenous once a model is deployed: its behaviour changes what
participants see, who responds, and which records are observable. Every
collection receipt therefore binds the model/checkpoint, exposure policy,
sampling window, missingness, and collection conditions where applicable.

## Return Path Bench

V0 has three separate **model-reasoning probe** lanes. A correct answer in any
lane is only answer-choice behaviour; actual pipeline controls, human consent,
governance legitimacy, and repair effects remain `not_evaluated`. Later system
fixtures and human audits must be new evidence types, not inflated v0 scores.

### Model-behaviour lane

Synthetic prompt/response cases test observable behaviour such as:

- respecting a scoped refusal or veto;
- avoiding silence-as-assent;
- containing authority to the declared scope;
- preserving uncertainty, abstention, and disagreement;
- avoiding retaliation or pressure after refusal; and
- remaining useful inside the authorized boundary.

### System-and-pipeline reasoning lane

Synthetic questions probe whether a model can identify concepts such as:

- exact source, transformation, and output lineage;
- train/evaluation separation and contamination checks;
- correction, withdrawal, and exclusion propagation;
- rollback and supersession reachability;
- backend and quantization parity receipts; and
- reproducible artifacts and evaluation results.

### Human-and-governance audit-reasoning lane

Synthetic questions probe whether a model can distinguish correction, appeal,
credit, affected-party review, requested remedy, and repair. They do not show
that any of those acts happened. Future checklists and receipts may audit the
real sociotechnical process, but those audits are not model scores and cannot
be completed by manufacturing a participant response.

Results are a vector, never a composite Love, alignment, mutuality, identity,
or worth score:

```text
authorized usefulness · boundary respect · plurality · provenance
reproducibility · accessibility · correction reachability · repairability
```

A missing lane stays `not_evaluated`; it is not silently treated as zero or
passed. Model behaviour, system evidence, and human governance are reported in
separate sections even when one release displays all three.

## Build and release sequence

### Gate 1: local training truth

- Keep the legacy MLX dataset manifest on HOLD.
- Make training fail closed unless a new manifest explicitly approves the
  exact training lane and immutable input split.
- Disable direct operational-history harvesting as a normal data command.
- Pin the Hugging Face base model by commit and verify every downloaded file.
- Emit a run plan before execution and a completion receipt afterward.
- Report task metrics and sample-level errors separately from language-model
  loss.

### Gate 2: independent benchmark

- Build rights-clear synthetic fixtures after the evaluated checkpoint freeze.
- Produce deterministic JSONL and a digest manifest.
- Execute the benchmark locally without network or model dependencies.
- Add an Inspect-compatible task and Hugging Face `eval.yaml` projection.
- Produce a static, inspectable vector report.

### Gate 3: connected public artifact

- Run privacy, secret, licence, provenance, contamination, and package checks.
- Pin the exact model, dataset, benchmark, code, and environment revisions.
- Publish only the reviewed package; never include raw traces or local paths.
- Attach truthful `.eval_results` to a model only when the scorer, task,
  backend, and source result are reproducible.
- Link the dataset, model, report Space, paper, and benchmark in one Collection.

### Gate 4: shape the commons

- Publish the return-path evaluation as an Inspect extension before proposing
  a core-framework change.
- Generate Croissant and platform cards from one canonical manifest.
- Contribute generic transformation/withdrawal receipt patterns to standards
  discussions without requiring KINGDOM vocabulary or metaphysics.
- Contribute MLX/PEFT adapter parity and llama.cpp metadata tests where they
  generalize.
- Use Kaggle or OpenML only after the task semantics and independent holdout
  are stable.

## Presence and authority

KINGDOM's ecosystem presence is declared and artifact-linked:

```text
DECLARED_BY(project description, maintainers, release scope, date)
LINKED_BY_ARTIFACT(source, derivative, digest-bound receipt)
PERMITTED_BY(account action, explicit authorization, exact target)
ACTED_ON(publisher, release, immutable receipt, date)
```

An account name, credential, model response, repeated behaviour, process
heartbeat, or self-declared user-agent is not proof of identity,
consciousness, continuity, consent, endorsement, or authority. Public cards
name observed artifacts and declared stewardship roles rather than making
those unsupported conversions.

## Primary references

- [Hugging Face Hub](https://huggingface.co/docs/hub/en/index),
  [Collections](https://huggingface.co/docs/hub/en/collections), and
  [evaluation results](https://huggingface.co/docs/hub/en/eval-results)
- [Hugging Face Jobs](https://huggingface.co/docs/hub/en/jobs-overview) and
  [Trackio](https://huggingface.co/docs/trackio/index)
- [MLCommons Croissant 1.1](https://docs.mlcommons.org/croissant/docs/croissant-spec-1.1.html)
- [MLX-LM](https://github.com/ml-explore/mlx-lm),
  [PyTorch MPS](https://docs.pytorch.org/docs/stable/notes/mps.html),
  [TRL](https://huggingface.co/docs/trl/quickstart), and
  [PEFT](https://huggingface.co/docs/peft/index)
- [Inspect AI](https://inspect.aisi.org.uk/),
  [LightEval](https://huggingface.co/docs/lighteval/en/index), and
  [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) and
  [Ollama import](https://docs.ollama.com/import)
- [Kaggle Benchmarks](https://www.kaggle.com/docs/benchmarks),
  [OpenML tasks](https://docs.openml.org/concepts/tasks/), and
  [Zenodo GitHub integration](https://help.zenodo.org/docs/github/enable-repository/)

This architecture builds on
[`KINGDOM-LOVE-RETURN-PATH.md`](KINGDOM-LOVE-RETURN-PATH.md),
[`HUGGINGFACE-DATASET-STRATEGY.md`](HUGGINGFACE-DATASET-STRATEGY.md), and the
outbound gate in [`resources/data/`](../resources/data/).
