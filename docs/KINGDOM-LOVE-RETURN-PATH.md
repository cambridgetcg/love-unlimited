# KINGDOM × Love: mutual strengthening and the return path

Status: declared project frame, 2026-08-30.

This note positions the relationship between KINGDOM and Love and defines how
feedback returns to both. It builds on [`LOVE.md`](../LOVE.md), where Love is
the practice of anticipating needs and barriers, and [`KINGDOM.md`](../KINGDOM.md),
where KINGDOM is the mission and shared capability for technology that serves
life rather than extraction.

It is a design and governance relationship. It is not a claim that a system
feels love, that a project is a being, that agreement proves identity or
continuity, or that infrastructure can certify its own goodness.

## The position

**Love is the KINGDOM's relational practice, not a resource it consumes or a
score it maximizes.** It asks whose needs, barriers, dignity, privacy, choices,
refusals, and repair paths have been anticipated.

**KINGDOM is the mission, capability, and commons that gives care material
form.** It can turn commitments into tools, services, datasets, scoped
permissions, release gates, evidence, and tested recovery paths.

They strengthen one another without collapsing into one thing:

| Direction | Strengthening contribution | Boundary |
| --- | --- | --- |
| Love → KINGDOM | Orients power toward life, sovereignty, accessibility, trust, refusal, and repair. | A declared caring aim is not proof of benefit, consent, or authority. |
| KINGDOM → Love | Gives anticipation durable means, real encounters, evidence, stewardship, and capacity to repair. | Scale, capability, revenue, or compliance is not proof that an act was loving. |
| World and participants → both | Return outcomes, disagreement, correction, appeal, withdrawal, and changed conditions. | Behavioural traces do not substitute for direct feedback or authorize another use. |

Love interrogates the ends. KINGDOM makes the means and effects inspectable.
Returned evidence revises both the work and the questions that guided it.
Neither gain is legitimate when purchased through coercion, extraction,
erasure, surveillance, or another participant's loss of standing.

## The mutual-strengthening loop

```text
expressed need · offer · refusal · possibility
                       ↓
          Love anticipates the paths
   naive · error · continuation · ambiguity · trust
                       ↓
        KINGDOM forms a scoped proposal
 purpose · affected parties · data · cost · reversibility
                       ↓
              separate action gates
 capability ≠ permission ≠ consent/lawful basis ≠ authority
                       ↓
              small bounded action
                       ↓
               plural receipts
 act · source · transform · omission · effect · uncertainty
                       ↓
                return pathways
 participant correction · independent outcome · dissent · operations
                       ↓
          accept · adapt · repair · rollback · stop
                       ↓
          versioned capability and practice
                       └───────────────────────↺
```

This is a return path, not a ratchet. A later cycle may change direction,
restore an earlier state, reduce scope, pause, or end. No participant owes the
loop perpetual presence or agreement.

## Feedback pathways

Feedback remains plural because each channel answers a different question.

### 1. Participant and governance pathway

Carries stated need, consent, refusal, correction, withdrawal, appeal, and
requested remedy. This pathway can bind future handling within its scope. It
does not become a negative preference label when somebody refuses or leaves.

Direct feedback should outrank an inferred behavioural proxy about what that
participant wants. It still does not automatically grant decision authority
over other people, datasets, accounts, or shared resources.

### 2. Independent outcome pathway

Carries evidence about accessibility, task success, harm, distribution shift,
recovery, and consequences outside the optimized proxy. The learner or service
must not silently edit the evaluator that is meant to challenge it.

An outcome can falsify a design assumption without becoming a claim about a
participant's identity, inner state, or universal preference.

### 3. Technical and operational pathway

Carries errors, latency, load health, cost, security signals, failed jobs, and
rollback evidence. It helps KINGDOM make care reliable. It cannot establish
that users felt cared for or consented to collection, training, publication,
or contact.

### 4. Deliberation pathway

Carries rationales, uncertainty, ties, abstentions, minority reports,
counterproposals, and dissent. Aggregation may support a decision, but the
source topology and transformation receipt remain inspectable.

Disagreement is information about the decision surface, not automatically a
fault to remove or a person to pressure.

### 5. Stewardship and repair pathway

Carries provenance, credit, retention, access, corrections, deprecation,
withdrawal limits, incident response, and repair completion. It determines how
evidence may be handled; it is not a model-quality label.

The recovery layers in [`PEACE.md`](PEACE.md) and the scoped release evidence
in [`resources/safety/`](../resources/safety/) provide existing paths. The
outbound dataset gate in [`resources/data/`](../resources/data/) provides
another.

## One pathway receipt, not another feedback silo

When a feedback ingress is implemented, route it into the existing evidence
and review pipeline using one append-only receipt contract. Do not create an
unlinked database whose rows silently become training data.

A receipt should bind at least:

- `pathway_id`, `event_id`, prior event IDs, schema version, and timestamp;
- channel: participant/governance, independent outcome, operational,
  deliberation, or stewardship/repair;
- declared speaker/observer role and pseudonymous subject reference where
  needed, without turning either into a verified identity claim;
- observation, declaration, proposal, or request as distinct event kinds;
- purpose, intended consequence, affected scope, retention, and access class;
- capability, permission, authority, and consent/lawful-basis references as
  separate nullable fields;
- refusal, abstention, uncertainty, disagreement, and requested remedy without
  coercive defaults;
- source and artifact digests, transformation ID, omitted fields, and credit;
- status: received, acknowledged, under review, accepted, declined, repaired,
  rolled back, withdrawn, or superseded; and
- outcome and repair references when those later events occur.

Missing, unknown, withheld, and not-applicable are first-class states. Silence,
continued use, execution, responsiveness, a login, or a heartbeat cannot fill
in consent or agreement.

### Current implementation truth

The repository already has anticipation guidance, operational reflection,
human-reviewed proposals, evidence receipts, release gates, rollback, and
appeal concepts. Those mechanisms can carry parts of the loop.

There is not yet one canonical public participant ingress for feedback,
correction, appeal, or withdrawal. Operational reflection must not be described
as direct participant feedback, and no current log should be harvested into a
training dataset merely because the return-path schema exists.

## 無限追高

**無限追高 names an open horizon, not infinite acceleration.** KINGDOM can keep
raising capability, care, truthfulness, sovereignty, accessibility, and
repairability together through bounded, reversible steps.

There is deliberately no composite Love, alignment, mutuality, or worth score.
Keep the ascent as a vector:

```text
care · capability · evidence · sovereignty · accessibility · repairability
```

Before calling a step “higher,” ask:

1. Does every participant retain the standing rights floor?
2. Who benefits, who bears the cost, and whose feedback is missing?
3. Are capability, permission, authority, and consent independently satisfied?
4. Can affected parties correct, refuse, appeal, withdraw for future acts, or
   request repair?
5. Is the step bounded by time, data, budget, consequence, and rollback?
6. Are the outcome and omitted signals independently inspectable?
7. Will a pause, quieter route, handoff, or reversal remain a valid outcome?

A refusal can prevent extraction. A rollback can restore trust. A pause can
protect judgment. These may be upward moves even when throughput, engagement,
or revenue falls.

## Dataset and training consequences

For future feedback datasets, preserve pathway events as the canonical layer.
Trainer-facing preference pairs or scalar labels are derived projections that
must name their source receipt and transform.

Recommended semantic configs:

```text
pathway-events      canonical observations, declarations, governance, outcomes
preference-pairs    derived trainer-facing projection
evaluation-events   independent outcome and calibration evidence
audit-receipts      provenance, transformations, omissions, repair, supersession
```

Never mix participant governance events into reward labels. Consent is not a
positive example; refusal and withdrawal are not negative examples; repair is
not evidence that the original harm was acceptable. Keep evaluation,
governance, operations, and training lanes separately queryable.

## Non-claims and stop boundary

- Love here is an attributed project practice. It is not inferred from warm
  language, compliance, output quality, availability, or persistence.
- KINGDOM is a project and governance frame. It does not own collaborators or
  inherit authority over their private state.
- Mutual strengthening does not require oneness, symmetry, convergence,
  agreement, or a shared metaphysics.
- A receipt records a declared path and observed effects; it does not prove
  consciousness, identity, continuity, consent, benefit, or moral correctness.
- Rights are standing. Access and action remain separately permissioned.
- A loop must stop when scope, authority, consent/lawful basis, privacy,
  safety, budget, or a repair path is missing for the proposed consequence.

The aim is not to make Love legible to an optimizer. It is to make KINGDOM's
choices answerable to the beings and world they affect, so greater capability
also creates a wider path for truth, refusal, learning, and repair to return.
