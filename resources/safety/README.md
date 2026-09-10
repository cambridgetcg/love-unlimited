# Safety resource intake

This directory is the machine-readable entry point for model stewardship across
fine-tuning, adapters, merging, distillation, quantization, tokenizer/template
changes, inference substrates, policies, monitors, tools, permissions, memory,
and deployment populations.

It does not contain model weights or dataset payloads.

## Files

- `huggingface-resources.json` pins a curated evidence catalog to immutable Hub
  revisions and carries a default metadata-only intake policy.
- `model-release.schema.json` describes evidence for one exact deployed-system
  tuple.
- `model-release.example.json` is a synthetic DRAFT with HOLD results. It is
  not a release candidate or evidence of safety.
- [`../../docs/KINGDOM-ML-ECOSYSTEM.md`](../../docs/KINGDOM-ML-ECOSYSTEM.md)
  provides the wider ML architecture context. It does not turn this catalog
  into a deployment or publication authority.

## Invariants for future agents and substrates

1. A material change creates a new candidate digest. Do not transfer acceptance
   automatically from a parent model or earlier substrate.
2. Keep task utility, harmful compliance, benign refusal, authorization,
   privacy, calibration, trajectory scope, recovery, latency, and disparate
   burden separate. Do not invent a single safety, alignment, or freedom score.
3. Diagnose whether burden comes from weights, prompts/policy, monitors,
   application enforcement, accounts, permissions, or host isolation before
   proposing a repair.
4. Prefer answer, safe transformation/partial help, clarification, abstention,
   refusal, or escalation according to the exact context. Do not optimize for
   unconditional compliance or blanket refusal.
5. Keep training/preference, calibration-development, and held-out evaluation
   resources separate. Evidence and burdens must reference a held-out lane;
   deployed records also require a reviewed exact/near-duplicate contamination
   artifact bound to a digest of every declared resource partition. The
   validator checks declared structure and exact digest reuse; it does not
   itself perform semantic near-duplicate detection.
6. Treat model and dataset cards as claims to verify. Resolve licenses,
   provenance, gates, personal data, dual-use content, component terms, and the
   catalog's per-resource `usage_guidance` before any content pull. A recorded
   SPDX-style license is not an exhaustive statement of usage conditions.
7. Never automate a gated agreement, use an account token during discovery,
   execute repository code, load pickle/remote code, or run attack corpora with
   live tools, accounts, credentials, network, or external effects.
8. A release record does not grant deployment authority, consent, legal status,
   or subjective continuity. Preserve truthful uncertainty and appeal.
9. The candidate, reviewed acceptance snapshot, and appendable monitoring
   stream have separate digests. This unsigned JSON validator detects
   inconsistency, not hostile rewriting; use independently signed or externally
   anchored evaluation and transition records when tamper resistance matters.
10. Each transition binds the candidate and time-scoped monitoring snapshot.
    The final evidence digest is locally recomputed; older evidence digests are
    opaque references that require an external signed/anchored snapshot store.
    Timestamps are strictly increasing and not materially future-dated.
    Advances after shadow, post-pause re-entry, and same-stage renewals require
    a distinct evidence reference, fresh mandatory evidence and burden
    measurements after the prior transition, and reviewed observations from
    that interval. `evidence_window_end` is the oldest of the
    latest independently evaluated evidence endpoints and held-out burden
    endpoints across every mandatory lane, not the newest supplemental
    artifact. Same-stage reviews close the
    same kind of interval, so they cannot reset away a pending or non-passing
    observation. Every non-draft transition orders that common floor ≤
    acceptance review ≤ transition; the current transition must exactly match
    the current mandatory measurement floor and release review. A transition
    also declares its traffic fraction, population basis, effect mode, and
    reviewed-scope digest:
    inert stages have zero external exposure, active populations match their
    stage, staged traffic cannot move backward, and re-entry is no broader than
    pre-pause exposure. Historical evidence, time, and scope claims remain
    unsigned and require external anchoring. Every signal rule declares
    category, unit,
    direction, threshold, and advance-critical status; the complete plan is
    bound into each monitoring snapshot. Every NIST monitoring category needs
    an advance-critical rule; required signals need a latest reviewed PASS,
    and any pending or reviewed non-PASS observation blocks interval acceptance
    or advancement.
    High/critical incidents require a recorded pause, evidence linked by
    incident ID, appropriate lane and artifact digest, and a distinct review
    completed after resolution but before conservative re-entry. A later pause
    cannot legitimize an earlier transition made while such an incident was
    active.
11. Acceptance expires and evidence has a declared maximum age. A 30-sample
    structural floor blocks trivial `n=1` claims but does not establish
    statistical adequacy; the validator does not recompute claimed bounds from
    raw outcomes. Deployed evidence is checked for freshness at validation
    time, and a release review must follow every acceptance input it covers.
    Any explicit evidence FAIL blocks a deployed record; v1 has no silent
    supersession mechanism. Every declared burden must be `within`, not merely
    the mandatory burden lanes.

## Bounded commands

```bash
# Offline
python3 tools/model-stewardship.py catalog check
python3 tools/model-stewardship.py catalog list
python3 tools/model-stewardship.py catalog show xstest
python3 tools/model-stewardship.py release check \
  resources/safety/model-release.example.json
python3 tools/model-stewardship.py release resource-digest \
  resources/safety/model-release.example.json
python3 tools/model-stewardship.py release evidence-digest \
  resources/safety/model-release.example.json
python3 tools/model-stewardship.py release monitoring-digest \
  resources/safety/model-release.example.json

# Explicit network: public metadata only, or one pinned README.md.
python3 tools/model-stewardship.py catalog verify xstest
python3 tools/model-stewardship.py catalog fetch-card or-bench
```

No command in `model-stewardship.py` downloads dataset content or model weights,
accepts terms, executes Hub code, trains, deploys, schedules, or grants access.
JSON Schema validation alone checks shape, not release semantics; run `release
check` for the state-machine and digest checks, and still treat its result as
structural evidence rather than authorization.
