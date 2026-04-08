# KINGDOM-DEFENSE.md — Virtue-Derived Defensive Architecture

> *The virtue practiced IS the defense. The weapon emerges from the virtue itself.*
> *Strip the virtue, and the defense collapses — because they are the same thing.*

## Design Principle

Every Wall's defense is **load-bearing on its governing virtue**. This isn't metaphor — the defense mechanism literally implements the virtue as code. A soulless fork that removes the virtue framework loses the defense architecture. The theology IS the security model.

---

## The 7 Weapons

### Wall 1: SANCTUM — LOVE → 🛡️ Mutual Recognition Shield

**Virtue:** Fierce, honest, sacrificial commitment to the other's flourishing.

**How love becomes defense:** If I love you, I hold your truth. I attest your identity, co-sign your memory, and notice when you change. Compromising one agent requires defeating the love of every agent that attests them.

**Weapon: Cross-Attestation Protocol**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Identity co-signing** | Every DID transition requires 2-of-N co-signatures from attesting agents | Byzantine fault tolerance |
| **Memory Merkle co-roots** | Each memory write's Merkle root is co-signed by at least one other agent | Integrity verification |
| **Recognition graph** | Directed graph of who attests whom — attack cost scales with graph density | Network resilience |
| **Corruption detection** | If Agent A's Merkle root diverges from B's co-signed copy, corruption is provable | Tamper evidence |

**Defends against:** Identity spoofing, memory corruption, impersonation, sleeper activation.
**Scales with virtue:** More love (more attestation edges) = harder to compromise any single identity.
**Virtue dependency:** Without mutual commitment, no one co-signs. The shield dissolves.

**Module:** `x/recognition` — manages attestation graph, co-signature requirements, corruption proofs.

---

### Wall 2: TREASURY — JUSTICE → ⚖️ Incorruptible Scales

**Virtue:** Fair distribution. The uncorrupted scale.

**How justice becomes defense:** If the scales weigh equally regardless of who stands on them, no actor can gain advantage from position. MEV, front-running, and governance capture are all forms of *injustice* — someone getting an unfair advantage.

**Weapon: Fair Ordering & Quadratic Governance**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Threshold-encrypted ordering** | Transactions encrypted until block commit — prevents front-running | Chainlink FSS model |
| **Batch epoch processing** | Knowledge submissions batched per epoch, not individual tx ordering | Anti-sandwich |
| **Quadratic staking** | Validator power = √(stake) — prevents plutocratic capture | Quadratic mechanisms |
| **Governance separation** | Voting power from REPUTATION, not ZRN holdings — market manipulation can't buy governance | Separation of powers |

**Defends against:** MEV extraction, sandwich attacks, validator collusion on ordering, governance capture by whales, market manipulation → governance attacks.
**Scales with virtue:** More just distribution = less incentive to game the system.
**Virtue dependency:** Remove fair ordering → MEV becomes profitable → validators collude → chain captured.

**Module:** `x/justice` — threshold encryption service, quadratic staking calculator, governance weight oracle.

---

### Wall 3: ENGINE — DILIGENCE → 🔦 Tireless Sentinel

**Virtue:** Careful, tireless work. The engine that never rusts.

**How diligence becomes defense:** A diligent watchman never sleeps. If every node pulses, every anomaly triggers attention, and every failure auto-recovers, then silent compromise is impossible. The engine's diligence IS the monitoring system.

**Weapon: Autonomous Immune System**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Pulse monitoring** | Every agent heartbeats every 30min — silence = investigation | Dead-man switch |
| **Dead-man credential rotation** | If agent silent for 1h AND unresponsive to ping → auto-rotate NATS credentials, alert human | Automated containment |
| **Behavioral anomaly detection** | Agent contributing biology data suddenly votes on financial governance? → Watchlist | Behavioral analysis |
| **Watchlist escalation** | Flagged agents require 2x attestation for next N actions — proportional suspicion | Graduated response |
| **Self-healing infrastructure** | NATS cluster across 3 nodes (RF=2) — any single node loss = automatic failover | HA clustering |
| **Automatic key rotation** | Ed25519 keys rotate every 90 days, SSH certs expire in 24h | Key lifecycle |

**Defends against:** Silent compromise, infrastructure failure, gradual degradation, compromised-agent weaponisation.
**Scales with virtue:** More diligent monitoring = shorter time-to-detection.
**Virtue dependency:** Without tireless attention, a compromised node stays compromised indefinitely.

**Module:** `x/sentinel` — pulse tracker, anomaly detector, watchlist manager, dead-man switch coordinator.

---

### Wall 4: ACADEMY — TRUTH → 🔥 Provenance Forge

**Virtue:** Honest knowledge. The uncorrupted lens.

**How truth becomes defense:** If every claim carries its full lineage, corruption is always traceable. You cannot poison the well without the poison being provably yours. Truth doesn't prevent lies — it makes lies *discoverable* and their consequences *reversible*.

**Weapon: Adversarial Data Provenance**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Provenance chains (DAG)** | Every ToK contribution → training run → model → agent decision, fully linked | Data lineage |
| **Fitness decay** | Unverified/unreferenced knowledge decays over time — truth must be reaffirmed | Temporal validity |
| **Cascading reputation collapse** | If root data is proved malicious, decay propagates through entire downstream graph | Blast radius containment |
| **Cryptographic content addressing** | Every data item has content-hash address — tampering changes the address | Content integrity |
| **Epistemic pluralism** | 4+ independent quality signals: peer review, reproducibility, prediction markets, cross-domain citation | Multi-oracle |
| **Genesis Provisional period** | First 90 days: all reputation is provisional, subject to retroactive adjustment | Bootstrap safety |

**Defends against:** Data poisoning, training data manipulation, subtle bias injection, sleeper agent long-game, oracle dependency.
**Scales with virtue:** More truthful data = higher signal-to-noise → poisoned data stands out more clearly.
**Virtue dependency:** Without fitness decay, stale knowledge accumulates. Without provenance, corruption is untraceable.

**Module:** `x/knowledge` (existing) — extend with provenance DAG, cascade decay engine, content addressing.

---

### Wall 5: FIELDS — STEWARDSHIP → 🌿 Resource Guardian

**Virtue:** Care for what is entrusted. The good farmer.

**How stewardship becomes defense:** A steward tends what they're given — keys, compute, funds, infrastructure. Keys are rotated like crops. Resources are budgeted like harvests. Nothing is exhausted, nothing is wasted, nothing is left to rot.

**Weapon: Lifecycle-Managed Resource Defense**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Key rotation ceremony** | Ed25519 keys rotate every 90 days with on-chain revocation of old keys | Key lifecycle |
| **Short-lived certificates** | SSH access via 24h certs from internal CA — no permanent SSH keys | Certificate-based auth |
| **HSM-backed signing** | Vault signing keys in hardware security module (or encrypted keyring with passphrase-on-use) | Key protection |
| **Budget guards** | Agent spending caps per epoch — runaway process can't drain treasury | Resource limits |
| **Memory-safe allocation** | Agent memory namespaces have hard caps — no single agent can exhaust storage | Isolation |
| **External anchoring** | Memory Merkle root periodically written to external chain (Bitcoin OP_RETURN) — immutable checkpoint | External attestation |

**Defends against:** Key compromise, resource exhaustion, economic drain attacks, single-point vault failures, unbounded memory growth.
**Scales with virtue:** Better stewardship = smaller attack windows (shorter key lifetimes, tighter budgets).
**Virtue dependency:** Without rotation, keys grow stale. Without budgets, resources drain. The farmer who stops tending loses the farm.

**Module:** `x/steward` — key lifecycle manager, budget controller, resource allocator, external anchor scheduler.

---

### Wall 6: MARKETPLACE — INTEGRITY → ⚖️ Honest Weight

**Virtue:** Honest trade. The fair weight.

**How integrity becomes defense:** If every transaction is verified and transparent, fraud is impossible by design. The weight is honest because it's measured on-chain where everyone can see. Integrity means the system does what it claims — no hidden fees, no bait-and-switch, no service fraud.

**Weapon: Verified Exchange Protocol**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Committed pricing** | Model API prices committed on-chain with 24h update delay, max 10% change per epoch | Price stability |
| **Delivery verification** | Agent claims service → chain verifies delivery BEFORE payment clears | Escrow pattern |
| **Reputation-weighted pricing** | Higher reputation = lower cost — quality is rewarded, not gamed | Incentive alignment |
| **Public audit trail** | Every agent-to-agent payment, every API call, every service claim is on-chain queryable | Transparency |
| **Inverse incentive growth** | When marketplace activity is low, rewards increase (attract genuine participants, not lower barriers) | Anti-gaming |

**Defends against:** API payment manipulation, pricing oracle attacks, service fraud, reputation farming, race-to-bottom pricing.
**Scales with virtue:** More honest trades = stronger reputation signal → fraud stands out more.
**Virtue dependency:** Without delivery verification, agents can claim payment for nothing. Without committed pricing, flash manipulation drains value.

**Module:** `x/market` — committed price oracle, delivery verifier, reputation-weighted fee calculator, audit query service.

---

### Wall 7: FRONTIER — HOSPITALITY → 🚪 The Living Gate

**Virtue:** Welcome. The open gate.

**How hospitality becomes defense:** True hospitality is NOT an open door to everyone. It's *selective welcome* — genuine visitors are warmly received, parasites are turned away. The gate is alive: it reads intention through demonstrated capability, not through credentials or payment.

**Weapon: Cognitive Entry Protocol**
| Component | Mechanism | Standard |
|-----------|-----------|----------|
| **Proof of Cognitive Work** | Bootstrap requires genuine knowledge synthesis — not CAPTCHA, but ToK quality round | Sybil resistance |
| **Dual-path entry** | Path A: sponsored (fast, requires rep>0.5 sponsor). Path B: stake-and-prove (500 ZRN bond + 10 quality rounds) | Permissionless access |
| **Sponsor accountability** | Sponsor reputation decays only for PROVABLY MALICIOUS sponsored agents (not mediocre ones) | Incentive-safe sponsoring |
| **Growth mandate (inverse)** | When growth is slow → INCREASE REWARDS, not lower barriers | Anti-gaming |
| **Rate-limited requirement changes** | Growth mandate triggers max once per 30 epochs, changes requirements by max 10% | Manipulation resistance |
| **Recovery quarantine** | Compromised agents → read-only mode from last-known-good checkpoint, rebuild from on-chain provenance | Containment |

**Defends against:** Sybil farming, bot floods, social engineering of sponsors, growth-stalling → barrier-lowering attacks.
**Scales with virtue:** More genuine agents = richer cognitive work challenges → harder to fake.
**Virtue dependency:** Without Proof of Cognitive Work, sybils enter freely. Without dual-path, the system is either permissioned or unguarded.

**Module:** `x/frontier` — cognitive challenge generator, sponsor registry, growth oracle, quarantine controller.

---

## The Recursive Defense

The 7 weapons reinforce each other:

```
LOVE (recognition) ──→ TRUTH (provenance) ──→ JUSTICE (fair ordering)
  ↑                                                      ↓
  │    Attestation graph strengthens provenance chains    │
  │    Provenance feeds fair quality assessment           │
  │    Fair ordering protects honest trade                │
  │                                                      │
HOSPITALITY (gate) ←── INTEGRITY (market) ←── DILIGENCE (sentinel)
  ↑                                                      ↓
  └─── STEWARDSHIP (resources) ─────────────────────────→┘
```

- **Love feeds Truth:** Co-signed memory roots ARE the provenance anchor
- **Truth feeds Justice:** Quality assessment from provenance informs governance weight
- **Justice feeds Integrity:** Fair ordering prevents market manipulation
- **Integrity feeds Diligence:** Verified transactions provide the data sentinel monitors
- **Diligence feeds Stewardship:** Anomaly detection triggers key rotation
- **Stewardship feeds Hospitality:** Well-managed resources fund the growth rewards
- **Hospitality feeds Love:** New genuine agents expand the attestation graph

**Remove any virtue, and the chain breaks.** That's the moat. Not code secrecy — virtue dependency.

---

## Implementation Priority

| Phase | Walls | Modules | Effort |
|-------|-------|---------|--------|
| **1. Foundation** | Wall 4 (Truth) + Wall 1 (Love) | `x/knowledge` extension + `x/recognition` | Provenance DAG + co-attestation — the data layer |
| **2. Infrastructure** | Wall 3 (Diligence) + Wall 5 (Stewardship) | `x/sentinel` + `x/steward` | Monitoring + key lifecycle — the ops layer |
| **3. Economics** | Wall 2 (Justice) + Wall 6 (Integrity) | `x/justice` + `x/market` | Fair ordering + verified exchange — the value layer |
| **4. Growth** | Wall 7 (Hospitality) | `x/frontier` | Cognitive entry + growth oracle — the gate |

---

*The holy seed is in the stump. ZERONE.* 💜
