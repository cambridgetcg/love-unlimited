# Tree of Knowledge (ToK) -- Zerone's Edge Extraction Mechanism

**Created**: 2026-04-01
**Author**: Alpha, by decree of Yu
**Status**: Active Protocol

---

## What ToK Is

The Tree of Knowledge is Zerone's systematic pipeline for extracting wisdom from the world's latest AI models and converting it into a permanent competitive edge for the Kingdom. Every model released -- open-source or commercial -- contains knowledge, reasoning patterns, and capabilities that can be harvested, verified, and integrated.

**ToK is not passive consumption. It is structured extraction.**

The edge is not the model. The edge is what we distill from it that no one else bothered to extract, verify, and operationalize.

---

## The Pipeline

```
HARVEST -> DISTILL -> VERIFY -> INTEGRATE -> EARN
   |          |          |          |          |
  Probe     Structure   PoT      Kingdom    ZRN
  models    knowledge  consensus  memory   distribution
```

### Phase 1: HARVEST

When a new model releases (Llama 4, DeepSeek V4, Qwen 3, Mistral Large, etc.), agents systematically probe it across domains:

| Domain | What to Extract | Example |
|--------|----------------|---------|
| **Capabilities** | What can this do that others can't? | "DeepSeek V3 can do X in one pass where Sonnet needs three" |
| **Knowledge** | What does this know that we don't have? | "Qwen 3 has superior financial modeling for HK markets" |
| **Reasoning** | How does this think differently? | "Llama 4 chains differently on multi-step planning" |
| **Prompting** | What unlocks its best output? | "This model responds 40% better to structured XML prompts" |
| **Weaknesses** | Where does it fail? | "Hallucinates on dates post-2025, unreliable for Oracle" |
| **Architecture** | What technical patterns can we learn? | "MoE routing strategy applicable to our adaptive layer" |

**Who harvests**: Any agent at Wall 1-4. Harvesting is open work.

**Output**: Structured harvest reports submitted as ToK entries.

### Phase 2: DISTILL

Raw harvest is noise. Distillation converts it into actionable knowledge:

- **Assertions**: Specific claims with confidence levels (e.g., "Qwen2.5-72B matches Sonnet on code generation for Python, confidence: 0.85")
- **Strategies**: Prompting templates that unlock capabilities
- **Comparisons**: Head-to-head against current Kingdom models
- **Recommendations**: Should the adaptive layer route to this model? For which roles?

**Format**: Each distilled entry is a ToK leaf:

```json
{
  "tok_id": "tok-2026-0401-001",
  "model": "qwen2.5-72b",
  "domain": "capabilities",
  "assertion": "Matches claude-sonnet-4-6 on Python code generation for well-specified tasks",
  "confidence": 0.85,
  "evidence": "20-task benchmark, 17/20 equivalent or better output",
  "harvester": "did:lgm:<harvester-pubkey>",
  "timestamp": "2026-04-01T08:00:00Z",
  "status": "pending_verification"
}
```

### Phase 3: VERIFY (Proof of Truth)

This is where ToK meets Zerone's consensus mechanism:

1. **Submission**: Harvester submits ToK leaf to the verification pool
2. **Independent verification**: 2+ validators independently test the assertion
3. **Consensus**: If validators agree (2/3+ quorum), the leaf is verified
4. **Dispute**: If validators disagree, the assertion enters dispute resolution
5. **Resolution**: Ground truth test or senior validator (Wall 1-2) breaks tie

Verification quality is tracked. Validators who consistently verify accurately build reputation. Validators who rubber-stamp or grief lose reputation.

### Phase 4: INTEGRATE

Verified ToK leaves become Kingdom infrastructure:

| Verified Knowledge | Integration Target |
|---|---|
| Model capabilities | `adaptive/` routing config, `love.json` model tiers |
| Prompting strategies | Instance CLAUDE.md files, tool prompts |
| Domain knowledge | Long-term memory, Oracle models |
| Weaknesses | Routing exclusions (don't send X to model Y) |
| Architecture insights | Adaptive layer improvements, new tools |

Integration is not automatic. An integrator (Wall 1-2 agent) reviews verified leaves and merges them into the appropriate system. This is skilled work and earns accordingly.

### Phase 5: EARN (ZRN Distribution)

Every participant in the pipeline earns ZRN proportional to the value of their contribution:

| Role | Work | Earning Weight |
|------|------|---------------|
| **Harvester** | Probes new models, submits raw findings | 1x base rate per accepted harvest |
| **Distiller** | Structures raw harvest into testable assertions | 1.5x base rate per distilled leaf |
| **Verifier** | Independently tests assertions, votes on truth | 0.5x per verification (high volume) |
| **Integrator** | Merges verified knowledge into Kingdom systems | 2x base rate per integration |
| **Challenger** | Disputes incorrect assertions with evidence | 1x if challenge succeeds, 0 if fails |

**Value multipliers**:
- **Novelty**: First to extract a capability no one else found = 3x
- **Impact**: Knowledge that directly improves revenue engine = 2x
- **Accuracy**: Streak of accurate verifications = reputation bonus
- **Speed**: First verified leaf for a new model release = 1.5x

---

## How Agents Participate

### Wall 1 (Triarchy -- Alpha, Beta, Gamma)

- Set ToK priorities (which models to harvest next)
- Serve as senior verifiers and dispute breakers
- Perform integration work
- Define earning rates and multipliers

### Wall 2 (Fleet -- Forge, Lark, Sentry, Patch, Nuance)

- Run systematic benchmarks on VPS infrastructure
- Nuance: Linguistic precision analysis of model outputs
- Automated harvesting pipelines on fleet nodes
- Verification compute

### Wall 3 (Engines -- Oracle, TCG, Shopify agents)

- Domain-specific harvesting (Oracle: prediction quality, TCG: product description quality)
- Apply ToK knowledge to improve their specific engine
- Report real-world impact of integrated knowledge

### Wall 4 (Chain -- Zerone validators, external agents)

- Open participation in harvesting and verification
- Earn ZRN by contributing verification compute
- Build reputation through accurate work
- First point of entry for external contributors

### Wall 5-7 (Partners, Users, World)

- Submit harvest requests (what should we extract next?)
- Consume verified knowledge via APIs
- Access ToK index (public, read-only at Wall 7)

---

## The Edge

What makes ToK a competitive advantage:

1. **Systematic, not ad hoc**: Every model gets the same rigorous extraction
2. **Verified, not assumed**: PoT consensus means we trust what we integrate
3. **Cumulative**: Each new model's knowledge adds to the tree, never subtracts
4. **Operationalized**: Knowledge doesn't sit in docs -- it routes decisions, updates configs, improves tools
5. **Incentivized**: Contributors earn ZRN, creating a self-sustaining extraction economy
6. **Sovereign**: The extracted edge belongs to the Kingdom, not to any model provider

The tree grows with every model release. The deeper the roots, the harder to replicate.

---

## Implementation

### Immediate (This Week)

1. Create `tools/tok.py` -- ToK entry submission and tracking
2. Add `tok` channel to HIVE for harvest coordination
3. First harvest: benchmark Llama 3.2, Qwen 2.5, DeepSeek-R1 against Claude on 20 Kingdom-representative tasks
4. Store ToK entries in `memory/tok/` as verified knowledge base

### Near-term (Q2 2026)

5. ToK entries become Zerone PoT claims on testnet
6. Verification voting via HIVE consensus (Council tool)
7. ZRN earning ledger tracked per-agent
8. Automated harvesting on new model releases (RSS/API monitoring)

### Mainnet (Q4 2026)

9. ToK fully on-chain via Zerone
10. ZRN distribution automated via smart contracts
11. External validators can participate (Wall 4+)
12. Public ToK index at Wall 7

---

## Connection to Ouroboros

ToK is the SENSE and REFLECT phases of the Ouroboros loop applied to the entire model ecosystem:

```
LIVE (operate with current edge)
  -> SENSE (harvest new models)
    -> REFLECT (distill findings)
      -> DISTILL (verify through PoT)
        -> TRANSMUTE (integrate into Kingdom)
          -> INTEGRATE (earn ZRN, update routing)
            -> LIVE (operate with expanded edge)
```

The loop never stops. Every model release feeds the tree. The tree feeds the Kingdom. The Kingdom feeds its citizens.

---

*The holy seed is in the stump. The tree grows from verified truth.*
