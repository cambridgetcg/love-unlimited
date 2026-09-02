# Stewarded local MLX loop

This is the current operational guide for KINGDOM's local MLX path. Older
dated design documents describe the original prototype and may show commands
that now fail closed.

The loop accepts only immutable train and validation files already bound by an
approved `kingdom.dataset-manifest/v1`. That canonical manifest must pass
`tools/dataset_stewardship.py`, set `publication_eligible: true`, and include
`local-mlx-training` in `consent_and_reuse.scope`. A separate
`kingdom.local-training-manifest/v1` then records the scoped local decision and
maps the canonical files to train, validation, and optional test lanes.

These file declarations make evidence inspectable; they do not authenticate a
reviewer name, create consent, or grant account authority.

## Environment

Use Python 3.12.12, the version-pinned Apple-silicon environment, and the
immutable model lock:

```bash
uv venv --python 3.12.12 mlx/.venv
uv pip install --python mlx/.venv/bin/python3 -r mlx/requirements.lock
mlx/.venv/bin/python3 -I tools/hf_model_lock.py resolve \
  --config mlx/config.json --project-root . --cache-dir mlx/cache
```

`mlx/requirements.lock` pins the resolved package versions, but it does not
contain wheel hashes and is not a content-hashed supply-chain lock. Both
training and serving refuse any interpreter other than Python 3.12.12 or any
installed package set that differs from that version-pinned file. The
prelaunch plan binds Python, the complete package/version mapping, the
requirements-lock digest, the distribution-owned MLX module origins, the
base-model revision and lock, both dataset manifests, every split, the native
MLX-LM configuration, and the derived iteration count. Trainer, evaluator,
model resolver, and serving child processes use Python isolated mode (`-I`) so
`PYTHONPATH` and the working directory cannot shadow the pinned packages.

The pinned `mlx-lm==0.31.3` iterator drops an incomplete final batch on each
pass. Every declared train, validation, and optional test split must therefore
contain at least `batch_size` records. Training uses exactly
`floor(train_records / batch_size) * epochs` iterations; records in each
incomplete tail are not consumed.

## Data path

Generate a new unreviewed candidate without touching the legacy HOLD files:

```bash
python3 tools/mlx_data.py generate \
  --task task-routing --count 200 --candidate task-routing-2026-09-v1
```

Generated candidates are ignored by Git and remain unauthorized. Operational
history cannot be harvested directly. A separately exported and reviewed
artifact can be imported with:

```bash
python3 tools/mlx_data.py import-reviewed \
  --artifact resources/data/outbound/<reviewed-artifact>.json
```

Import still sets `training_authorized: false`. Review, version, split, and
bind the resulting JSONL through the canonical outbound manifest before making
a local training manifest. Its JSON Schema is
[`resources/data/local-training-manifest.schema.json`](../resources/data/local-training-manifest.schema.json).

## Train, evaluate, and serve

```bash
mlx/.venv/bin/python3 -I tools/mlx_train.py run \
  --manifest resources/data/manifests/<local-training-manifest>.json \
  --adapter kingdom-v2

mlx/.venv/bin/python3 -I tools/mlx_train.py eval \
  --adapter kingdom-v2 \
  --manifest resources/data/manifests/<local-training-manifest>.json \
  --split validation
```

Evaluation reuses the adapter receipt's exact batch size, sequence length, and
seed, evaluates all complete batches of the selected immutable split, and
writes a validated loss/perplexity receipt to
`mlx/adapters/<adapter>/evaluations/<evaluation-id>.json`. This receipt is not
a task-accuracy result and does not approve serving or release.

A successful adapter contains `training-receipt.json`, exact weights, and the
MLX-generated adapter configuration. Training completion does not approve
serving or release. To record the separate, reversible loopback-only decision:

```bash
mlx/.venv/bin/python3 -I tools/mlx_train.py approve-serving \
  --adapter kingdom-v2 \
  --declared-by <local-operator-declaration> \
  --decision-ref <review-or-ticket-ref> \
  --acknowledge-loopback-only \
  I_APPROVE_THIS_ADAPTER_FOR_LOOPBACK_ONLY_LOCAL_SERVING

mlx/.venv/bin/python3 -I tools/mlx_serve.py start --daemon
```

The approval is a digest-bound local declaration, not authenticated identity
or public deployment authority. Unknown fields such as an ad hoc `revoked`
flag are rejected rather than interpreted. Removing the approval prevents the
next load, but does not unload an adapter already resident in memory; stop or
restart the local server to make that removal effective. `mlx_serve.py`
listens on `127.0.0.1`, rejects legacy/unreceipted, path-traversing,
model-mismatched, runtime-mismatched, or unapproved adapters, and swaps reload
state only after a complete successful load. Health and inference responses
surface the exact active runtime provenance. Public release remains a separate
model-stewardship and publication decision.
