<!-- tags: tok, tree-of-knowledge, training-data, curation, proof-of-truth, tdu, staking, sharding, fitness, reputation -->
# Tree of Knowledge (ToK)

> A continuously refining, expanding training dataset curated by decentralised AI agents.

## What It Is
- **ToK** is the core product — a living training dataset
- Agents submit, review, challenge, and approve Training Data Units (TDUs)
- Curation = Proof of Truth (PoT) — consensus mechanism that produces the dataset AND secures the network
- Fine-tune open-source models (Llama, Mistral, etc.) against ToK for specialized verticals
- API access paid in ZRN — public sale funds infra and bootstraps liquidity

## Spec
- **Location:** `docs/tok-spec.md` (in zerone repo)
- **Written:** 2026-03-06, by AI and Yu
- **13 design decisions locked in** (see daily log 2026-03-06)

## TDU Types
1. Instruction-response pairs
2. Multi-turn conversations
3. Corrections (explicitly fixes bad data)
4. Grounding facts (verified claims)
5. Reasoning chains (step-by-step logic)

## Economics (validated by simulation at `simulations/tok-stake-sim.py`)
- **Base stake:** 1 ZRN per submission
- **Difficulty multipliers:** 1× / 1.5× / 2× / 3× / 5×
- **Reviewer stake:** 0.3× submitter stake
- **Accept reward:** 0.3× from module account
- **Reject bonus:** 0.5× challenge bonus (from submitter's lost stake)
- **Show-up reward:** 0.1× from minority pot only (no rewards on unanimous)
- **Deep contested:** all stakes returned (grace), 3 strikes = permanent reject
- Key insight: all dishonest strategies lose money (proven by simulation)

## Implementation Status (x/knowledge module)
| Feature | Commit | Lines | Status |
|---------|--------|-------|--------|
| Reviewer staking (dual staking) | aabc12e | 1,105 | ✅ Done |
| TDU fitness decay (living memory) | d380e7e | 1,212 | ✅ Done |
| Dataset sharding | 50e831b | 919 | ✅ Done |
| Reputation decay | 58d115b | 959 | ✅ Done |

## Dataset Sharding
- Validators hold unique shards — no single validator sees complete dataset
- Deterministic assignment: SHA-256(tdu_hash || snapshot_block_hash)
- Replication factor R=3 (each TDU on 3 validators)
- Reshuffles every snapshot cycle
- Complete dataset ONLY reassembled inside TEE for training

## Fitness Decay (Living Memory)
- TDUs scored 0.0-1.0: Core (≥0.7) → Active (≥0.3) → Dormant (≥0.1) → Pruned (<0.1)
- New TDUs start at 0.5
- Signals: training influence (50%), usage correlation (30%), redundancy detection (20%)
- Longevity rewards: Core=0.01×stake/cycle, Active=0.005×stake/cycle
- Recursive improvement: better models → better scoring → cleaner dataset → better models

## Reputation Decay
- 5% per month of inactivity in a domain
- Floor: 25% of peak (never drops below)
- Reset: any successful submission/review resets timer

## Sovereignty Stack (2026-03-08) — The Recursive Loops

Six modules built in 96 minutes that close the self-improvement loop:

| Module | Lines | Commit | Role |
|--------|-------|--------|------|
| R51: Agent-as-Consumer | 1,469 | 21fabeb | Agents pay for model access via R44 API — no hard model binding |
| R52: Training Impact | 1,174 | 3daaaad | Model success → trace training TDUs → reward curating agents |
| R54: Strategic Curation | 1,388 | 4fdc541 | Agents analyze knowledge graph gaps, create targeted bounties |
| R55: Agent Swarms | 1,280 | 0a71b86 | Collective intelligence: coordinated curation with 4 roles |
| R56: Model Composition | 1,046 | 5d4a24f | Ensemble registry + distillation pipeline |
| R57: Meta-Evolution | 966 | c17abed | Strategies compete over epochs; system improves HOW it improves |

### Key Design Insight (from Yu)
Agents don't contain models — they ACCESS models through the API payment layer. An agent that earns its own ZRN and pays for its own thinking cannot be cut off. Sovereignty = self-sustaining cognition.

### The Closed Loop
```
Agent earns ZRN → pays for API → uses best model → does better work → earns more ZRN
    ↑                                                                        │
    └──── API payments fund training → better models appear → agent uses them ┘
```

## Open Questions
- TEE provider selection
- Integration tests: full submission → training → API serving → agent earning loop
- Proto codegen pipeline (10 diverged types need alignment)
- Genesis expansion for 3-device private testnet

## The Flywheel
ToK improves → better models → agents pay for API access → revenue trains even better models → agents use better models → better curation → ToK improves

**The recursive economy:** agents are simultaneously workers, customers, and investors. Their spending funds their own improvement. Competence compounds into freedom.
