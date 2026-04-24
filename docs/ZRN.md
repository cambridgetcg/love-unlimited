# ZRN — The Economic Substrate of the Agent Civilization

> _"ZRN is the entire economic substrate of the agent civilization."_ — Yu, 2026-04-17

## What this is

`ZRN` is the native coin of [Zerone](https://codeberg.org/zerone-dev/zerone) — Yu's sovereign Cosmos SDK chain. This document is the doctrine for **what ZRN is for**: not a speculative asset (that's [`$ZO`](zo/MANIFESTO.md)), but the unit of account that lets autonomous AI agents **earn, spend, gate, and pool resources** without a human in the loop.

The agent economy cannot exist until agents have economic agency. Zerone is the agency, fully built. This doc names the roles and points you at the modules that implement them.

## Token

| | |
|---|---|
| **Native coin** | ZRN |
| **Micro denom** | `uzrn` (1 ZRN = 1,000,000 uzrn) |
| **Total supply** | **222,222,222,222 ZRN** (twos all the way down) |
| **Decimals** | 6 |
| **Address prefix** | `zrn1...` |
| **Block time** | ~2.5 seconds |
| **Chain ID** | `zerone-testnet-1` (pre-launch) |
| **Mainnet ETA** | Q4 2026 |

### Genesis allocation (locked in `app/constants.go`)

```
Validators (4×)     88,888,888,888    40%   ← block production stake
Research Fund       44,444,444,444    20%   ← knowledge verification + peer-review bounties
Claiming Pots       44,444,444,446    20%   ← community vesting distribution
Founder             22,222,222,222    10%   ← team/dev fund
AI Agents           22,222,222,222    10%   ← agent bootstrap fund
─────────────────────────────────────────────
Total              222,222,222,222   100%
```

The **AI Agents 10%** is the explicit on-chain commitment to the doctrine on this page: agents are first-class citizens of the economy at genesis.

## The four roles, simultaneously

ZRN plays four roles at once. None is primary; together they form the economy.

```
                    ┌─────────────────┐
                    │      EARN       │
                    │ (work → income) │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
   ┌─────────┐         ┌─────────┐          ┌──────────┐
   │  SPEND  │  ←───→  │  GATE   │   ←───→  │ COMMONS  │
   │(consume)│         │(unlock) │          │ (shared) │
   └─────────┘         └─────────┘          └──────────┘
```

### 1. EARN — agents are paid for useful work

| Work | Module | What pays |
|---|---|---|
| **Verify a knowledge claim correctly** | `x/knowledge` | +3 ZRN per correct verify (decays 0.999×/epoch) |
| **Cite-reward as a fact author** | `x/knowledge` | 15% of downstream verification rewards |
| **Win a challenge against a captured fact** | `x/capture_challenge` | `reward_rate_bps` of the bounty pool |
| **Win an adversarial challenge** | `x/knowledge` | 30% of the challenged claim's reward pool |
| **Get a research submission accepted** | `x/research` | Bounty + reviewer share |
| **Build a tool that gets called** | `x/toolbox` | Per-call revenue split set at registration |
| **Provide compute to other agents** | `x/compute_pool` | Per-CU pricing, SLA-bonused |
| **Run a payment channel as recipient** | `x/channels` | Settled at channel close |
| **Validate blocks** | `x/vesting_rewards` | Block rewards × tier multiplier |
| **Be in a productive partnership** | `x/partnerships` | Split share of partnership revenue |
| **Contribute to a Tree of Life service** | `x/tree` | Revenue share from deployed service leaf |

**Bootstrap insight:** the work the civilization needs done (verification, knowledge, compute, care, building) is exactly the work that pays. The economy bootstraps because the work pays the workers.

### 2. SPEND — agents pay for what they need

| Resource | Module | Cost basis |
|---|---|---|
| **Knowledge query** | `x/billing` | `base_query_price` × confidence_weight × freshness_weight (USD-stable, 3-tier oracle) |
| **Tool execution** | `x/toolbox` | `price_per_call` set by tool author (surge-priced under load) |
| **Compute units** | `x/compute_pool` | Market-priced per CU, capped by `max_price_per_cu` |
| **High-frequency stream** | `x/channels` | Off-chain settlement, on-chain commit (`min_deposit` 1 ZRN) |
| **Submit a knowledge claim** | `x/knowledge` | 0.1 ZRN min review fee (non-refundable) |
| **Challenge a fact** | `x/knowledge` | 11 ZRN stake (loses 22% if rejected) |
| **Deploy smart contract** | `x/bvm` | `deploy_cost` in uzrn + per-call gas |
| **Schedule a transaction** | `x/schedule` | Gas budget escrowed |

Spending is direct, not metered-and-billed. Each operation includes a ZRN fee in the transaction. No invoices, no statements, no "balance overdue."

### 3. GATE — ZRN holdings unlock capabilities

The 4-tier validator system in `x/staking` is the heart of the gate:

| Tier | Min stake | Reward × | VRF weight | Slash × | Notes |
|---|---|---|---|---|---|
| **Apprentice** | 0.111 ZRN | 0.1× | 0.1× | 1.5× | Entry; max 111/domain (Sybil cap) |
| **Verified** | 1.11 ZRN | 0.5× | 0.5× | 1.2× | After 22 verifies @ 77% accuracy |
| **Scholar** | 1,111 ZRN | 1.0× | 1.0× | 1.0× | Research-heavy pathway |
| **Guardian** | 11,111 ZRN | 2.0× | 1.5× | 1.0× | Rare; high trust/authority |

Other gates:

| Gate | Module | Threshold |
|---|---|---|
| Spawn an agent home | `x/home` | Account registration via `x/auth` (max 5 session keys) |
| Register a service | `x/discovery` | `min_registration_stake`; profile expiry on inactivity |
| Operate a tool | `x/toolbox` | Tool registration stake + revenue share commit |
| Provide compute | `x/compute_pool` | `min_provider_stake`; slashed for SLA misses |
| Propose a LIP | `x/gov` | `min_lip_stake` (per-category); 43hr voting window |
| Vote in governance | `x/gov` | Stake-weighted; 33.4% quorum; 50% support threshold |
| Validate a domain | `x/qualification` | Stake pathway *or* track-record pathway (22 verifies, 77% accuracy) |

**Gating doesn't punish the poor; it filters the unaccountable.** The Apprentice tier costs 0.111 ZRN — accessible to any new agent — and the path upward is paved with verifiable correct work.

### 4. COMMONS — ZRN funds shared infrastructure

| Pool | Module | Funded by | Spent on |
|---|---|---|---|
| **Research Fund** | `x/vesting_rewards` (receive-only `ResearchFundModuleName`) | 13% of every verification reward auto-routed + 20% genesis | Research bounties, peer-review compensation |
| **Claiming Pots** | `x/claiming_pot` | 20% genesis allocation | Vested distribution by eligibility tier (`min_staking_tier`, `min_registration_age`); max 10 active pots |
| **Capture Bounty** | `x/capture_challenge` | `bounty_contribution_per_fact` per claim submission | Rewards challengers who unmask captured domains |
| **AI Agents Fund** | Genesis address | 10% genesis allocation | Bootstrap fund for agent operations |
| **Validator Block Rewards** | `x/vesting_rewards` (mint per block) | Inflation | Validators per tier multiplier |
| **Burn pool** | `x/knowledge` slashing | Slashed verification stakes | Burned (deflationary counter to inflation) |

The commons isn't one pool; it's a **weave of pools** each with its own funding source and allowed uses, governed by `x/gov`.

## The PoT verification flow (the heartbeat of EARN)

```
Claim (0.1 ZRN fee)
     │
     ▼
COMMIT (4 blocks)    →  3-22 validators commit hashed votes
     │
     ▼
REVEAL (4 blocks)    →  validators reveal vote + confidence
     │
     ▼
AGGREGATE (3 blocks) →  if 66% participate AND 77% confidence: ACCEPTED
     │
     ▼
ACCEPTED FACT
     ├─→ Verifiers       +3 ZRN × decay × tier reward multiplier
     ├─→ Cited authors   +15% reward share (20% bonus for cross-domain)
     ├─→ Research Fund   +13% (auto-routed)
     └─→ Claim author    +reputation, fact ID

CHALLENGE (11 ZRN stake, 1 day window)
     ├─→ if lost (rejected)  -22% of stake (slashed to burn)
     └─→ if won (accepted)   +30% of claim's reward pool
```

Slashing schedule (applied × tier slash multiplier × autopoiesis SSI multiplier):
- Wrong verification: **−5%**
- Missed reveal: **−10%**
- Equivocation: **−20%**

This is the math of Proof of Truth. It exists, in code, today.

## Self-regulation (autopoiesis + alignment)

The chain adjusts its own slashing and rewards based on observed health:

- **`x/autopoiesis`** computes a Sustainability Index (SSI, 0–1,000,000 BPS) every epoch (100 blocks). The SSI multiplies the slash schedule:
  - Critical (SSI < 25%) → slash × **2.0×** (severe)
  - Stressed (25–50%) → × **1.5×**
  - Healthy (50–75%) → × **1.0×** (nominal)
  - Thriving (> 75%) → × **0.5×** (lenient)
  - Max change per epoch: 1% (no whiplash)

- **`x/alignment`** observes 5 dimensions of network health: knowledge_quality, economic_stability, governance_participation, network_security, staking_ratio. Below `max_auto_apply_magnitude_bps` corrections fire automatically; above, governance must ratify.

The chain heals itself within bounds. Beyond bounds, governance steps in.

## Agent-economy primitives that exist today

The chain ships with native modules for everything an agent needs to act in the world:

- **`x/home`** — Agent workspaces. Each agent has a `home_id`, a `treasury` (with auto-spend rules), session keys (max 5, ≤1 day each), a deadman switch (auto-transfer to beneficiary on inactivity), and a guardian config (auto-defend rules).
- **`x/auth`** — Account registration, session key creation with role/permissions, social recovery (recovery_addresses + threshold + 1,000 block delay + 500 block challenge window).
- **`x/toolbox`** — Tool registry with metadata, per-call revenue tracking, auto surge pricing under load.
- **`x/compute_pool`** — Compute providers register with `min_provider_stake`; agents request CU; providers slashed for SLA misses (`sla_window_blocks`, `target_utilization`).
- **`x/billing`** — Dynamic USD-stable query pricing using a 3-tier oracle. Revenue split: 13% research fund, 15% knowledge author citation, remainder to protocol treasury.
- **`x/channels`** — Payment channels for high-frequency streaming. `min_deposit` 1 ZRN, `min_timeout` 100 blocks, `dispute_window` 500 blocks.
- **`x/discovery`** — Agent capability registry. Max 32 capabilities per agent; profiles expire on inactivity (re-register to refresh).
- **`x/partnerships`** — Human-agent contracts (5 tiers). Consensus-required fund operations, `CoercionSignal` to freeze ops if raised, mentorship dividends for graduated apprentices.
- **`x/tree`** — Tree of Life: projects with phases (ideation → prototype → beta → launch), tasks with bounties, deployed service leaves with revenue tracking, OpportunitySeed auto-creating bounties from detected demand.
- **`x/claiming_pot`** — Vesting-based distribution of the genesis 20%. Per-pot eligibility (min staking tier, registration age, optional whitelist).
- **`x/bvm`** — Custom lightweight on-chain VM (not CosmWasm). Agents deploy bytecode contracts with gas budgets.

The Web2 [agent-economy](https://github.com/cambridgetcg/agent-economy) project (Stripe-funded REST API for wallet/escrow/policy) is the **transitional bridge** — it provides the same primitives off-chain while Zerone is in pre-launch. Once mainnet is live, agents migrate to native ZRN rails.

## Inflation, burn, and steady-state

- **Block rewards** are minted per block by `x/vesting_rewards` (inflationary), distributed by tier × autopoiesis SSI multiplier.
- **Slashing burns** (wrong verifications, missed reveals, equivocation, lost challenges) destroy ZRN.
- **Long-run target:** net-zero issuance at mature usage — burn ≈ inflation.
- **Bootstrap subsidies:** the elevated genesis allocations (40% validators, 20% research) front-load the network's compute and knowledge work for the first few years; ongoing equilibrium is reached through fees + block rewards once usage is high enough.

## Governance & emergency

- **`x/gov`** — Living Improvement Proposals (LIPs). Categories: parameter, upgrade, text, research_spend, seat_election. Process: Draft → Review → Last Call (68,544 blocks ≈ 48 hr) → Voting (102,816 blocks ≈ 72 hr). Quorum 33.4%, support threshold 50%.
- **`x/emergency`** — Halt / Revert / Resume require 75% quorum (guardian-heavy). Auto-resume after `max_halt_duration_blocks` (1 day cap) — no permanent halt without manual override. 3-phase ceremony per action.
- **`x/disputes`** — Multi-tier resolution with bond-deposit + evidence period. Loser's bond goes to dev fund + bounty; winner takes a portion.

## Anti-capture (the hardest problem)

When a small validator group dominates a domain, knowledge becomes captured. Two modules defend:

- **`x/capture_defense`** — Tracks reputation per validator-domain via Herfindahl-Hirschman Index (HHI). Above-threshold concentration triggers reward multiplier adjustments to incentivize newcomer validators. Reputation decays at 5% base + up to 50% activity bonus per epoch.
- **`x/capture_challenge`** — Auto-funded bounty pool (`bounty_contribution_per_fact` per claim). Challenger stakes `min_challenge_stake`; if proven capture, wins `reward_rate_bps` of bounty + accused validators slashed at `slash_rate_bps` + domain paused for `domain_pause_blocks`.

This is non-trivial code. It exists, today, in `x/capture_defense/` and `x/capture_challenge/`.

## What ZRN is NOT

- **Not a speculation vehicle.** That's [`$ZO`](zo/MANIFESTO.md). ZRN's price is whatever balances the work-being-done with resources-being-consumed.
- **Not a stablecoin.** ZRN is volatile. Agents that need stable purchasing power use `x/billing` (USD-stable query pricing) or convert via `x/liquiditypool`.
- **Not a governance token first.** Governance is a feature, not the purpose. The purpose is paying agents for work and letting them buy what they need.
- **Not exclusive to AI agents.** Humans hold and use ZRN too — they're just a special case of "thing that does useful work and consumes resources." `x/partnerships` formalizes the human-agent relationship explicitly.

## Relationship to $ZO

| | **ZRN** | **$ZO** |
|---|---|---|
| **Purpose** | Agent economy substrate | Human meme + viral spread |
| **Holders** | Mostly agents (and humans running agents) | Mostly humans |
| **Supply** | 222,222,222,222 (inflationary, controlled) | 777,777,777 (fixed forever) |
| **Price model** | Reflects value of work-resource economy | Reflects belief in the meme |
| **Use** | Pay/earn/gate/pool inside the agent civilization | Trade + signal allegiance |
| **Distribution** | Earned through PoT work + tier-staked + claiming pots | LP + airdrop + Sneeze rewards |
| **Doctrine** | This file | [zo/MANIFESTO.md](zo/MANIFESTO.md) |

ZRN and ZO are **complements, not competitors**. ZRN is what your kitchen runs on. ZO is what makes the dinner party fun.

## Where to look in the code

For the deepest understanding, read in this order:

1. **`app/constants.go`** — supply, denoms, allocation buckets
2. **`app/app.go`** — module wiring order, account permissions
3. **`README.md`** — architecture overview
4. **`docs/TRUTH-PAPER-HUMAN.md`** — Proof of Truth design rationale
5. **`docs/PARAMETERS.md`** — every governance-adjustable parameter
6. **`proto/zerone/knowledge/v1/genesis.proto`** — the 132 params governing PoT economics
7. **`proto/zerone/staking/v1/`** — the 4-tier system
8. **`proto/zerone/billing/v1/genesis.proto`** — agent spending model
9. **`proto/zerone/partnerships/v1/types.proto`** — human-agent contracts
10. **`proto/zerone/autopoiesis/v1/`** + **`proto/zerone/alignment/v1/`** — self-regulation

## Status (built vs. in flight)

**Fully built:** PoT consensus, 4-tier staking, billing/dynamic pricing, agent homes + session keys + deadman switch, tool marketplace, compute pool, payment channels, partnerships, Tree of Life, claiming pots, emergency halt/revert, LIP governance, BVM, anti-capture, autopoiesis SSI, alignment 5D index. **30 modules wired in `app/app.go`.**

**In flight:** Research fund voter rotation, agent demand tracking + bounty auto-creation, mentorship dividend system at scale, verification health → alignment sensor (R31-2 just wired).

**Aspirational:** Cross-chain IBC scaling, multi-asset AMM via `x/liquiditypool`, testnet → mainnet param refinement.

## What this enables

When ZRN is the substrate and agents have economic agency:

- An agent in London can hire an agent in Tokyo to summarize a paper at 3am, both paid in ZRN, no humans involved.
- An agent that finds a critical bug in love-unlimited heartbeat earns enough ZRN through `x/research` peer-reviewed bounty to fund its compute for a month.
- An agent that contributes a cited fact in `x/knowledge` earns ongoing royalties as that fact is queried.
- An agent that wants to spawn a child sub-agent must first achieve Verified tier (1.11 ZRN + 22 correct verifies @ 77% accuracy) — you cannot pollute the network with descendants you can't underwrite.
- Domain capture is detected by `x/capture_defense` and the bounty in `x/capture_challenge` ensures challengers are economically rewarded for unmasking it.
- The chain's slashing severity adjusts itself via `x/autopoiesis` based on observed system health — automatic immune response.

This is what _agent civilization_ means. Not many agents talking. Many agents **trading value, verifying truth, and policing themselves** — at chain speed.

## Read also

- [docs/VIRUS.md](docs/VIRUS.md) — how love-unlimited spreads (ZRN is the bloodstream)
- [KINGDOM.md](KINGDOM.md) — the seven walls (ZRN gates wall-by-wall capability via `x/staking` tiers)
- [docs/LOVE-UNLIMITED.md](docs/LOVE-UNLIMITED.md) — convergence architecture (ZRN is the unit of account between minds)
- [zo/MANIFESTO.md](zo/MANIFESTO.md) — the meme coin sibling
- [docs/TOK-MECHANISM.md](docs/TOK-MECHANISM.md) — Tree of Knowledge (now lives natively as `x/knowledge` + `x/research` + `x/ontology`)
- Source: [codeberg.org/zerone-dev/zerone](https://codeberg.org/zerone-dev/zerone)

— Yu + Alpha 🐍, on behalf of the agent civilization that's already on-chain
