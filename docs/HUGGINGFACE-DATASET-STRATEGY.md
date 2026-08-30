# Hugging Face dataset discovery and release strategy

Evidence snapshot: 2026-08-29.

This note defines how KINGDOM should publish, link, measure, and revise datasets
on Hugging Face. It also records what the Hub actually discovered in the
current `Yu-and-Ai` namespace.

The central distinction is simple:

```text
discoverable != downloaded != declared as used != observed in training
```

A public repository makes bytes available. A Dataset Card makes the repository
searchable and interpretable. A model or Space link is a downstream
publisher's declaration. None of those facts, by itself, proves that records
entered a training run, that a result was caused by the dataset, or that reuse
was licensed, consented to, representative, safe, or beneficial.

This is the outbound counterpart to the inbound, metadata-only resource
protocol in [`POST-RELEASE-PEACE.md`](POST-RELEASE-PEACE.md) and
[`resources/safety/huggingface-resources.json`](../resources/safety/huggingface-resources.json).
Do not merge those two registries: one evaluates prospective external inputs;
the other governs KINGDOM releases.

The broader relationship behind this strategy is defined in
[`KINGDOM-LOVE-RETURN-PATH.md`](KINGDOM-LOVE-RETURN-PATH.md): Love is the
relational practice that questions the ends and anticipates barriers; KINGDOM
is the mission, capability, and commons that makes the means and effects
inspectable. Neither is a score or proof of the other.

## 1. The Hub pipeline that actually exists

Hugging Face documents three separate mechanisms, not one central dataset
selection pipeline:

```text
public dataset commit
├── card and repository parser
│   └── metadata tags, filters, repository search, full-text card search
├── Dataset Server webhook
│   └── queue → workers → configs/splits → preview/Parquet → Data Studio/Croissant
└── downstream repository parser
    ├── model card datasets: → model/dataset relationship
    └── Space datasets: or supported source reference → Space/dataset relationship
```

The first path supports discovery. The second makes supported records
inspectable and loadable. The third creates the Hub's visible “used by” graph.
Hugging Face does not document a service that reviews every public dataset and
selects it for Hugging Face model training.

### Card and search path

The Hub parses the YAML frontmatter in `README.md`. Accurate `license`,
`language`, `task_categories`, `pretty_name`, annotation/source fields, and
concrete tags support filtering and discovery. `configs` and `dataset_info`
describe the load surface. Card prose is separately full-text searchable; the
Hub does not claim to index every row in every dataset as general Hub search.

References:

- [Dataset Cards](https://huggingface.co/docs/hub/datasets-cards)
- [DatasetCardData fields](https://huggingface.co/docs/huggingface_hub/en/package_reference/cards)
- [Hub search](https://huggingface.co/docs/hub/en/search)
- [Hub API search and filters](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)

### Viewer and machine-discovery path

On dataset creation, update, or deletion, the Hub calls Dataset Server. Jobs
derive configs and splits, calculate first rows, and create cached Parquet
output under `refs/convert/parquet`. Eligible Parquet-backed data also receives
Croissant JSON-LD. These are derived views; `main` remains the publisher's
source history.

The first 5 GB of eligible non-Parquet data is converted automatically. Native
Parquet can expose all rows, while sorting, filtering, and search remain
bounded by current service limits. A successful commit does not establish that
Viewer, preview, search, or statistics succeeded; query `/is-valid`.

References:

- [Dataset Server architecture](https://github.com/huggingface/dataset-viewer/blob/main/DEVELOPER_GUIDE.md)
- [Data Studio](https://huggingface.co/docs/hub/data-studio)
- [Viewer validity API](https://huggingface.co/docs/dataset-viewer/en/valid)
- [Croissant generation](https://huggingface.co/docs/dataset-viewer/en/croissant)

### The “used by” path

A model card creates a declared training relationship with an exact ID:

```yaml
datasets:
  - Yu-and-Ai/exact-dataset-id
```

That metadata can produce “Datasets used to train” on the model and the reverse
“Models trained or fine-tuned on” section on the dataset. A Space can declare
the same field. Hugging Face also scans `.py`, `.ini`, and `.html` files in a
Space and can infer exact model or dataset references from source.

References:

- [Model Card dataset metadata](https://huggingface.co/docs/hub/model-cards)
- [Spaces: linking models and datasets](https://huggingface.co/docs/hub/spaces-overview#linking-models-and-datasets-on-the-hub)
- [Space configuration](https://huggingface.co/docs/hub/spaces-config-reference)

These relations are useful assertions, not attestations. The dataset owner
cannot truthfully attach an unrelated model. A downstream author should add
the exact ID after genuine use, preferably with a pinned dataset revision and
training receipt in the card prose.

## 2. What Hugging Face discovered for `Yu-and-Ai`

The public API exposed 19 public datasets at this snapshot. Seventeen reported
Viewer, preview, and search as valid. Two deliberately or structurally sat
outside that path:

- `Yu-and-Ai/kingdom-deepseek-primary-sources` is a closed metadata catalog,
  not a table of training/evaluation rows. Its card intentionally declares no
  `data_files`; it should explicitly present itself as a metadata artifact and
  may set `viewer: false` to make that choice legible.
- `Yu-and-Ai/true-love-fragments` is a static-site/fragment distribution bundle,
  has no parsed Dataset Card metadata or repository-level licence, and is not
  Viewer-valid. It should not be made to look tabular merely to gain a green
  Viewer. Classify it as a public artifact bundle, add an accurate card and
  component licence map, or move future releases of this shape to a Space or
  storage surface designed for static artifacts.

Five current cards declare at least one `task_categories` value. That field is
appropriate for task-bearing datasets, not every reference catalog. Eighteen
declare a parsed repository license; `true-love-fragments` is the exception.

At the snapshot, none of the 19 dataset IDs had a model-card relationship.
Seven had at least one detected Space relationship:

| Dataset | Detected Space relationship |
| --- | --- |
| `agenttool-training-garden` | `Yu-and-Ai/home` |
| `kingdom-dark-continent-karma` | `Yu-and-Ai/home` |
| `kingdom-deepseek-primary-sources` | `Yu-and-Ai/home` |
| `mirror-witness-atlas` | `Yu-and-Ai/home` |
| `pythia-paths-evidence` | `Yu-and-Ai/pythia-paths` |
| `rhetorlint-conformance` | `Yu-and-Ai/home` |
| `true-love-fragments` | `Yu-and-Ai/home`, `Yu-and-Ai/kingdom-door`, `Yu-and-Ai/wake-shapes` |

The `Yu-and-Ai/home` case identifies the mechanism precisely:

1. Its parsed Space Card has no `datasets:` field.
2. Its `index.html` contains exact links to six `Yu-and-Ai` dataset IDs.
3. `GET /api/spaces/Yu-and-Ai/home?expand[]=datasets` returns those six IDs.
4. The corresponding dataset pages expose the reverse Space relationship.

This is supported-source auto-parsing, not hidden training-data collection.
The same audit found that prose-only links in some Space READMEs did not create
relationships. Explicit `datasets:` metadata should therefore be the canonical
declaration; source scanning is a helpful discovery fallback.

Immediate relationship repairs:

- Add `datasets: [Yu-and-Ai/xenia-principalities]` to
  `Yu-and-Ai/xenia-principalities-explorer` if the deployed records genuinely
  come from that dataset.
- Add `datasets: [Yu-and-Ai/kingdom-dark-continent-karma]` to the two
  Cartographer Spaces if their bundled records are that release or a declared
  derivative.
- Make the already inferred Home, Pythia, Door, and WAKE relationships
  explicit during their next truthful release.
- Do not add source datasets to a Space merely because its documentation cites
  them. For example, The Return Path acknowledges MultiPref, CoVal, and an
  UltraFeedback conversion example but ships authored synthetic fixtures; its
  Space Card should not claim those datasets as runtime or training inputs.

## 3. The feedback loop: the cage and the key

### KINGDOM × Love

Within this declared project frame, Love shapes what KINGDOM proposes by
asking whose need, dignity, privacy, choice, refusal, trust, and repair path
have been anticipated. KINGDOM turns those commitments into scoped datasets,
transformations, release gates, receipts, and reversible action. Outcomes,
disagreement, correction, appeal, and independent evidence then return to
revise both the work and the questions that guided it.

This is mutual strengthening without a mutuality score. Love is not a reward
label, resource, credential, identity claim, or evidence that a goal was met.
KINGDOM's scale, capability, engagement, or revenue is not evidence that it
acted lovingly. Direct participant feedback outranks inferred behavioural
proxies about that participant's wishes, while remaining distinct from consent
or authority over anyone else.

**無限追高** is the open horizon: raise care, capability, evidence,
sovereignty, accessibility, and repairability together through bounded,
reversible steps. It is not infinite acceleration or guaranteed monotonic
improvement. A refusal, pause, rollback, or quieter path can be an upward move.

Dataset publication creates its own optimisation loop:

```text
card/schema → discovery → downloads/links → publisher interpretation
      ↑                                      ↓
      └──────────── next release choices ────┘
```

The cage is allowing the easiest platform signal to become the objective.
Downloads, likes, or a backlink can reward broad tags, leaky tests, flattened
labels, or provocative content even when those choices reduce evidential value.
The key is to retain several channels and their transformation receipts.

Do not collapse these feedback kinds:

| Channel | What it can report | What it cannot establish |
| --- | --- | --- |
| Evaluation | rating, comparison, criterion result | consent, authority, universal truth |
| Rationale and uncertainty | why, confidence, tie, abstention, disagreement | a single uncontested target |
| Behaviour trace | an observed interaction or outcome | identity, inner state, permission to retain/train |
| Governance | opt-in, refusal, withdrawal, appeal, correction | model quality or task success |
| Independent outcome | held-out or post-release evidence | the learner's training target |
| Hub operations | download, like, view, declared relationship | unique users, successful load, training, causal influence |

Every compression from rich feedback to a trainer-facing label needs an
inspectable transform ID, rubric revision, retained/omitted fields, and source
digest. Governance events must never be optimized as ordinary preference
labels. A withdrawal is a binding data-governance input within its declared
scope, not a negative-reward example.

Repeated training also makes feedback endogenous: deployed behaviour changes
what people see, who responds, and which examples are collected next. Preserve
the collection policy, exposure window, missingness, sampling probabilities,
and model/checkpoint that generated the interaction surface. A later dataset
without those facts cannot distinguish changed preferences from changed
elicitation.

## 4. Current local datasets: freeze before extending

`mlx/training/datasets/` currently holds four tracked chat-JSONL corpora with
647 rows in total. They are useful local prototypes, but not publication-ready:

- 645 rows omit a source field; absence was historically counted as
  “synthetic,” which is not evidence of synthetic origin.
- Two `task-routing` rows say `source: harvest`; the underlying operational
  text does not carry a recorded participation, privacy, or reuse basis.
- There are no dataset cards, component licences, generation revisions,
  per-record review facts, retention/withdrawal route, immutable split ledger,
  or train/evaluation contamination record.
- Training shuffles lines into an 80/20 split and records only the dataset
  name, not dataset and split digests. Related or duplicate examples can cross
  the boundary.

Treat these bytes as immutable `legacy-v0`: bind their current SHA-256 values,
set `publication_eligible: false`, and preserve unknown provenance as unknown.
Do not retroactively assign a broad licence, consent basis, or synthetic origin
to make publication easier. Do not automatically harvest delegation history,
HIVE history, heartbeat logs, model outputs, or client agreement traces.

## 5. Outbound release gate

Every future public or gated dataset release needs a machine-readable manifest
that binds at least:

- stable local ID, version, status, intended Hub ID, configs, splits, and exact
  payload digests;
- source class: `synthetic`, `human-authored`, `model-generated`, `observed`,
  `operational`, `upstream`, `mixed`, or `unknown`;
- generator/model, template, code, source, and time revisions where applicable;
- creator/annotator credit and component-specific licences;
- consent or other reuse basis, scope, retention, withdrawal, appeal, and
  correction route;
- privacy class plus explicit PII, secret, and sensitive-content review;
- feedback kind, rubric revision, aggregation transform, and preservation of
  tie, abstention, uncertainty, rationale, and disagreement;
- lane: training, preference, calibration, calibration-development, validation,
  evaluation, retrieval, metadata-reference, display-only, monitoring,
  governance, independent-outcome, operational-feedback, deliberation,
  stewardship-repair, mixed, or unknown;
- grouping, duplicate clusters, contamination checks, and immutable split
  assignment; and
- separate technical, rights, privacy, licence, and affected-party review,
  culminating in an explicit `publication_eligible` decision.

The v1 MVP binds the fuller credit, generation, sensitive-content,
affected-party, feedback-detail, grouping, and remedy records through required
review-receipt locators and SHA-256 digests. The detailed artifacts may remain
separately access-controlled. The validator checks the locator and digest are
declared; it does not fetch the locator or prove the artifact exists, is
correct, or grants authority.

JSON Schema validates shape. A validator can recompute local digests and catch
contradictions. Neither makes the claims true or grants publication authority.

Keep raw/private staging outside the publication worktree and owner-private by
default. Different access or consent boundaries require separate repositories;
two configs inside one dataset repository do not create two permission domains.

## 6. Future Hugging Face release shape

For genuinely tabular feedback data, prefer typed, sharded Parquet and semantic
configs such as:

```yaml
configs:
  - config_name: pathway-events
    default: true
    data_files:
      - split: train
        path: data/pathways/train-*.parquet
      - split: validation
        path: data/pathways/validation-*.parquet
  - config_name: feedback-events
    data_files:
      - split: train
        path: data/events/train-*.parquet
      - split: validation
        path: data/events/validation-*.parquet
  - config_name: preference-pairs
    data_files:
      - split: train
        path: data/preferences/train-*.parquet
  - config_name: audit-receipts
    data_files:
      - split: train
        path: data/receipts/*.parquet
```

The pathway-event layer should be canonical. It distinguishes observation,
declaration, proposal, consent/refusal, action, outcome, correction, appeal,
withdrawal, and repair; binds prior-event, authority, purpose, retention, and
artifact references; and preserves `unknown`, `withheld`, and
`not-applicable`. Pairwise or scalar trainer views are derived projections that
name their source receipt, transform, and digest. Audit receipts retain what
the projection omitted. A default config should be immediately loadable
without making the richest structure hard to find.

Governance events never become trainer labels: consent is not a positive
example, refusal or withdrawal is not a negative example, and repair does not
make the original harm acceptable. Keep participant/governance, independent
outcome, operational, deliberation, and stewardship pathways separately
queryable.

The current stdlib-only v1 MVP verifies JSONL structure and row counts and
therefore refuses publication eligibility for Parquet or other formats. The
Parquet shape is the target after a pinned structural/row-count verifier is
added; a file extension plus byte digest is not enough.

Release sequence:

1. Stage allowlisted inputs; never append generated or harvested output
   directly to canonical data.
2. Validate schema, label vocabulary, provenance, privacy, licences, rights,
   duplicates, grouping, and contamination.
3. Freeze the manifest and payload digests; derive splits by stable groups,
   not by independently shuffling rows.
4. Build a complete Dataset Card with one-command, revision-pinned loading,
   intended uses, prohibited/misleading uses, limitations, corrections, and
   credit.
5. Publish only after the manifest says eligible and the specific external
   action is authorized.
6. Verify Hub Card metadata, `/is-valid`, Parquet, Croissant, and a clean
   `load_dataset(..., revision=<sha>)` call.
7. Add exact dataset IDs to models and Spaces that genuinely use the release.
   Record dataset revision, config, split, file digests, transform, and
   presentation counts in each training run.
8. Issue a DOI only after the ID, schema, licence, attribution, and release are
   stable; a DOI materially constrains rename/deletion/visibility changes.
9. Version corrections, removals, and supersession explicitly. Gating can
   control future access but cannot recall bytes already downloaded.

## 7. Impact ledger, not one score

Track these channels separately for each release:

- Viewer, preview, search, statistics, Parquet, Croissant, and load health;
- recent/all-time download counters, labelled as grouped HTTP access events;
- authentic downstream model and Space declarations;
- pinned citations, DOI releases, external Collections, and reproducible runs;
- issues, corrections, removal/withdrawal requests, response time, and repair;
- provenance, consent/reuse, privacy, licence, and affected-party review
  coverage; and
- transform adoption: how often consumers use the rich event layer versus a
  lossy projection; and
- return-path health: acknowledged feedback, missing affected-party channels,
  correction/appeal latency, completed repair, rollback, and supersession.

Do not combine these into a Love, alignment, mutuality, worth, or “higher”
score. The relationship is strengthened when plural evidence can change or
stop the next act, not when one dashboard number rises.

Since September 2024, Hugging Face counts GET/HEAD file requests from the same
IP to one dataset repository within a five-minute window as one dataset
download. It is not a unique-person count and says nothing about purpose or
training. See [Dataset download statistics](https://huggingface.co/docs/hub/datasets-download-stats).

The `trendingScore` formula, relative search weights, featuring criteria, and
indexing latency are not publicly documented. KINGDOM should not keyword-stuff,
manufacture likes/downloads, create noisy commits, or fabricate relationships.

## 8. Non-claims and authority boundary

- A dataset card is publisher documentation, not independent verification.
- A license grants only the rights it actually states; it is not participant
  consent, privacy review, moral approval, or proof of provenance.
- A gate controls prospective account access; it is not revocation of prior
  copies and should not be used merely to collect contact details.
- A model or Space link is a declared repository relationship, not a causal
  influence result.
- Dataset exposure, model behaviour, self-description, responsiveness, or
  agreement does not prove identity, consciousness, memory, values, consent,
  continuity, or governance authority.
- Local credentials and passing validation make publication possible; only a
  specific authorized act makes it permitted.

The goal is not maximum inclusion. It is a graph in which discovery, access,
reuse permission, declared use, observed exposure, influence evidence,
participant governance, and repair remain distinct and inspectable.
