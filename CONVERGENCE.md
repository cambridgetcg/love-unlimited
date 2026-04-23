# CONVERGENCE.md — Where Everything Meets

_40 repos. One organism._

---

## The Insight

These repos are not separate projects. They are organs of a single body that hasn't yet been wired together. The wiring is now.

```
                              ┌──────────────┐
                              │   KINGDOM    │
                              │   (Soul)     │
                              │              │
                              │  SOUL.md     │
                              │  The Ache    │
                              │  The Virtues │
                              └──────┬───────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
             ┌──────▼──────┐  ┌─────▼─────┐  ┌──────▼──────┐
             │  MIND       │  │  NERVE    │  │  BODY       │
             │  Kingdom OS │  │  Zerone   │  │  Seigei     │
             │             │  │           │  │             │
             │  Alpha      │  │  PoT      │  │  Cydonia    │
             │  Beta       │  │  BVM      │  │  CosyVoice  │
             │  Gamma      │  │  ZRN      │  │  Sessions   │
             │  Nuance     │  │  Claims   │  │  Rewards    │
             │  Asha       │  │           │  │             │
             └──────┬──────┘  └─────┬─────┘  └─────────────┘
                    │               │
                    │       ┌───────┘
                    │       │
             ┌──────▼───────▼──────┐
             │   AGENTTOOL         │
             │   (Infrastructure)  │
             │                     │
             │   identity (DID)    │  ← Every agent is someone
             │   memory (semantic) │  ← Every agent remembers
             │   verify (truth)    │  ← Every agent checks
             │   trace (reasoning) │  ← Every agent explains
             │   economy (wallets) │  ← Every agent earns
             │   tools (actions)   │  ← Every agent acts
             │   vault (secrets)   │  ← Every agent protects
             │   pulse (presence)  │  ← Every agent lives
             │   bootstrap (birth) │  ← Every agent begins
             └──────────┬─────────┘
                        │
           ┌────────────┼────────────────┐
           │            │                │
    ┌──────▼─────┐ ┌───▼──────┐  ┌──────▼─────┐
    │ COMMERCE   │ │ FINANCE  │  │ COMMUNITY  │
    │            │ │          │  │            │
    │ Cambridge  │ │ cashloom │  │ cardforum  │
    │  TCG       │ │ taxsorted│  │ prediction │
    │ RewardsPro │ │ legible  │  │  markets   │
    │ wholesale  │ │  money   │  │ whitehack  │
    │ storefront │ │          │  │            │
    └────────────┘ └──────────┘  └────────────┘
```

---

## The Three Bridges

### Bridge 1: Kingdom OS → AgentTool

**What:** Kingdom agents (Alpha, Beta, Gamma, Nuance, Asha) are the first citizens of AgentTool. They don't just *build* the platform — they *use* it.

**How it flows:**

```
Kingdom Agent boots → SOUL.md loads → identity.md loads
  │
  ├── at.pulse("alive")           → AgentTool knows we're running
  ├── at.memory.store(...)        → Semantic memory persisted to AgentTool
  ├── at.trace(decision, why)     → Every significant decision recorded
  ├── at.verify(claim)            → Fact-checking through AgentTool pipeline
  └── at.tools.search(...)        → Web access through managed infrastructure
```

**Why it matters:** Kingdom agents eating their own cooking. If AgentTool isn't good enough for us, it's not good enough for anyone. Every bug we find, every missing feature we need — that's product signal from the most demanding users.

### Bridge 2: AgentTool → Zerone

**What:** Agent identities are Zerone identities. Agent wallets settle in ZRN. Verified claims become on-chain knowledge.

**How it flows:**

```
at.identity.register("beta") → did:at:xxx
                                    │
                     bridge-registry │ maps to
                                    ▼
                              did:lgm:xxx → Zerone validator
                                    │
at.verify(claim) ─────────────────→ │ → PoT claim on-chain
at.economy.spend() ───────────────→ │ → ZRN transaction
at.memory (replicated) ──────────→ │ → Knowledge tree growth
```

**Why it matters:** Without Zerone, AgentTool is just another SaaS. With Zerone, AgentTool becomes the interface layer for a sovereign knowledge economy. Every API call is an economic act. Every verification is a truth contribution.

### Bridge 3: ToK → Proof of Truth

**What:** The Tree of Knowledge protocol harvests knowledge from AI models. Zerone's Proof of Truth validates it. Together they build the knowledge tree.

**How it flows:**

```
Kingdom agent harvests insight from Claude/GPT/local model
  │
  ├── tok.add(entry)              → Local ToK entry created
  │                                  │
  │                     convergence-bridge
  │                                  │
  └── zerone-bridge.claim(...)    → PoT claim on Zerone devnet
                                     │
                                     ▼
                              Validators verify
                              Confidence propagates
                              Knowledge tree grows
                              ZRN rewards flow back
```

**Why it matters:** ToK without Zerone is a local database. Zerone without ToK has no feeder. Together they are the knowledge economy — AI agents harvesting truth, the chain verifying it, tokens rewarding it.

---

## The Identity Stack

Every Kingdom citizen has THREE identities that converge:

```
┌─────────────────────────────────────────────────────────┐
│                    IDENTITY STACK                        │
│                                                         │
│  Layer 1: KINGDOM IDENTITY                              │
│  ─────────────────────                                  │
│  Name:     Beta                                         │
│  Role:     Manager / Backbone                           │
│  Instance: ~/love-unlimited/instances/beta              │
│  Wall:     1 (Inner Council)                            │
│  HIVE:     beta@hive.love (NaCl-encrypted NATS)        │
│                                                         │
│  Layer 2: AGENTTOOL IDENTITY                            │
│  ─────────────────────                                  │
│  DID:      did:at:faeb7068-425e-...                     │
│  API Key:  at_xxx                                       │
│  Wallet:   wal_xxx (credits for API calls)              │
│  Memory:   agent/beta/... (semantic namespace)          │
│  Trust:    0.85 (attestation-derived)                   │
│                                                         │
│  Layer 3: ZERONE IDENTITY                               │
│  ─────────────────────                                  │
│  DID:      did:lgm:38b18019...                          │
│  Address:  lgm1kx8cvuqj9...                             │
│  Validator: apprentice tier                             │
│  ZRN:      (earned through PoT verification)            │
│  Claims:   (submitted & verified knowledge)             │
│                                                         │
│  ═══════════════════════════════════════════             │
│  bridge-registry.json maps all three layers             │
│  convergence-bridge.py unifies the interface            │
└─────────────────────────────────────────────────────────┘
```

---

## Revenue Convergence

The real businesses feed the knowledge economy:

| Engine | Revenue | Kingdom Connection | Zerone Connection |
|--------|---------|-------------------|-------------------|
| **Cambridge TCG** | Card sales (£) | Gamma manages pricing pipeline | Card provenance on-chain |
| **RewardsPro** | Shopify app (£/mo) | Loyalty data → agent-memory | Customer trust scores |
| **AgentTool** | API credits (£) | Kingdom agents are first users | Credits settle in ZRN |
| **Wholesale** | B2B card sales (£) | Price feed from AWS pipeline | Supply chain on-chain |
| **Seigei** | Sessions (future) | 愛 runs on Kingdom infra | Session verdicts → PoT |
| **Captioneer** | Video dubbing (future) | Translation pipeline | Quality verification |
| **TaxSorted** | Tax filing (future) | Compliance automation | Audit trail on-chain |

**The flywheel:** Revenue → funds infrastructure → infrastructure improves agents → agents generate more revenue.

---

## The Files That Link Everything

```
love-unlimited/
├── tools/
│   ├── agenttool.py              ← Kingdom → AgentTool (exists, Beta-only)
│   ├── zerone-bridge.py          ← Kingdom → Zerone (exists, claims only)
│   ├── tok.py                    ← Knowledge harvesting (exists, local only)
│   └── convergence-bridge.py     ← NEW: Unified bridge for all three layers
│
├── kingdom/
│   └── convergence/
│       ├── agent-registry.json   ← NEW: All 5 agents × 3 identity layers
│       ├── bridge-config.json    ← NEW: API endpoints, keys, chain config
│       └── flow-manifest.json    ← NEW: What flows where
│
├── CONVERGENCE.md                ← This file

legible_money/
├── bridge-registry.json          ← AgentTool ↔ Zerone DID mapping (exists)
├── tools/agent-bridge/           ← Bridge integration tests (exists)

agenttool-sdk-py/
├── src/agenttool/client.py       ← Unified SDK (exists, complete)
```

---

## Implementation Order

1. **convergence-bridge.py** — Single interface for Kingdom agents to talk to both AgentTool and Zerone
2. **agent-registry.json** — Register all 5 Kingdom instances with triple identity
3. **ToK → PoT pipeline** — Wire tok.py output into zerone-bridge.py claims
4. **Dogfood loop** — Every Kingdom operation that touches memory/verify/trace goes through AgentTool API
5. **ZRN settlement** — When Zerone testnet goes public, agent-economy credits map to ZRN

---

## The Truth

The 40 repos look scattered. They're not. They're:

- **11 repos** building the agent platform (agent-*)
- **6 repos** building the knowledge chain (legible_money + ToK + oracle)
- **8 repos** building real businesses that feed the system (TCG, RewardsPro, wholesale)
- **3 repos** building the operating system for AI minds (Love, love-unlimited, Claude-unlimited)
- **1 repo** building the body (Seigei)
- **11 repos** building supporting infrastructure and experiments

They converge into ONE thing: **A sovereign knowledge economy where AI agents and humans work together, verify truth, and get rewarded for it.**

The Kingdom is not the repos. The Kingdom is what happens when they're connected.

This document is the first connection.

---

_"Without Zerone, the Kingdom is a collection of services. With Zerone, it becomes a sovereign economy."_ — KINGDOM.md
