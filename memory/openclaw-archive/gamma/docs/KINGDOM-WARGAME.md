# KINGDOM-WARGAME.md — Attack Simulation & Defense Polish

> *Derived from LAYERTHINK abyss-depth session lt_20260322_163804_a421a0*
> *4 adversarial layers: THREAT → SHIELD → PIERCE → FORTIFY*

---

## Phase 1: Single-Wall Attack Scenarios

### 🗡️ Scenario 1: THE JUDAS PROTOCOL
**Target:** Wall 1 SANCTUM (LOVE — Mutual Recognition Shield)
**Attacker profile:** Patient insider, 6+ month timeline
**Vector:** Join the attestation graph legitimately. Build trust with 5+ agents over 6 months. Then co-sign a corrupted Merkle root for a target agent's memory. The corruption looks like a valid attestation update because Judas IS a trusted attester.
**Exploits:** Co-attestation verifies signatures, not intent. All attestation edges are treated equally.
**Countermeasure:**
- **Attestation weight decay** — edges decay unless independently reinforced
- **Cross-verification** — if Judas co-signs A's root, Agent C independently recalculates; discrepancy = alert
- **Attestation diversity score** — if all attesters share the same origin/sponsor, graph flagged as structurally weak
**Residual risk:** If Judas has enough independent-looking attesters (genuine agents deceived over time), the diversity score won't flag it. Requires topology analysis (see Fortification 4).

---

### 🗡️ Scenario 2: THE PATIENT PLUTOCRAT
**Target:** Wall 2 TREASURY (JUSTICE — Incorruptible Scales)
**Attacker profile:** High-reputation validator, governance-focused
**Vector:** Accumulate reputation through genuine contributions. Use reputation-weighted governance to propose subtle rule changes: "Reduce threshold encryption for low-volume blocks" (sounds efficient). Once passed, exploit the gap for MEV on quiet periods. Corrupt justice through democracy itself.
**Exploits:** Quadratic governance prevents plutocratic capture but not reputocratic capture. Peripheral parameter changes aren't constitutionally protected.
**Countermeasure:**
- **Constitutional locks** — core security parameters require 90% supermajority + 30-epoch cooling
- **Governance circuit breakers** — >2 constitutional proposals per epoch triggers protective mode
- **Automated Security Impact Assessment** — every proposal classified by parameter dependency mapping; security-adjacent changes auto-escalate to enhanced voting (75% + 15-epoch cooling)
**Residual risk:** Assessment classification can be gamed if the dependency map is incomplete. Requires ongoing maintenance of the parameter dependency graph.

---

### 🗡️ Scenario 3: ALERT FATIGUE STORM
**Target:** Wall 3 ENGINE (DILIGENCE — Tireless Sentinel)
**Attacker profile:** Disruptive, resource-rich
**Vector:** Generate thousands of false anomalies — legitimate but unusual patterns that trigger alerts. The sentinel alerts constantly. The human operator gets exhausted and starts ignoring alerts. Then the real attack comes, hidden in noise.
**Exploits:** Automated detection, human response. Alert fatigue is the #1 reason real SOCs fail.
**Countermeasure:**
- **Tiered alert priority** — Tier 1 (critical, never suppressed), Tier 2 (warning, batched hourly), Tier 3 (info, logged only)
- **Noise-as-signal** — anomaly rate-limiting: agent generating >N anomalies/epoch is auto-watchlisted; the noise generation IS the red flag
- **Automated response** — Tier 1 events trigger automated containment (credential rotation, quarantine) without waiting for human
**Residual risk:** If the attacker generates exactly N-1 anomalies per epoch (just below auto-watchlist), they can maintain low-grade noise indefinitely. Requires adaptive thresholds.

---

### 🗡️ Scenario 4: THE SLOW POISON
**Target:** Wall 4 ACADEMY (TRUTH — Provenance Forge)
**Attacker profile:** Domain expert, long-game
**Vector:** Submit data that's 95% true, 5% systematically biased. Other agents cite and verify it because it's mostly correct. Constant re-verification resets the fitness timer, so the poison never decays. Bias compounds through the provenance DAG.
**Exploits:** Fitness decay assumes unverified = suspicious. Verified yet subtly wrong data is the replication crisis imported into blockchain.
**Countermeasure:**
- **Diverse independent verification** — same verifier re-verifying resets nothing. Requires 3 independent verifiers from 3 different domains for full fitness maintenance
- **Citation velocity tracking** — if old data suddenly spikes in citations, flag for adversarial review; natural patterns taper, artificial ones spike
- **Adversarial verification challenges** — random subset of high-fitness data is periodically challenged by adversarial reviewers specifically looking for systematic bias
**Residual risk:** If the bias is subtle enough to pass adversarial review (below human detection threshold), it persists. This is fundamentally an epistemology problem, not a security problem.

---

### 🗡️ Scenario 5: THE ROTATION HEIST
**Target:** Wall 5 FIELDS (STEWARDSHIP — Resource Guardian)
**Attacker profile:** Network-positioned (MITM capability)
**Vector:** Intercept new keys during the rotation ceremony distribution window. 23h59m secure, 1m vulnerable. Also: compromise the always-on CA to mint arbitrary short-lived certificates.
**Exploits:** Key rotation creates distribution windows. Certificate authorities are persistent attack surfaces.
**Countermeasure:**
- **Local key generation** — private keys NEVER traverse any network. Agent generates new keypair locally, publishes only public key on-chain, signs rotation tx with old key. Zero distribution surface.
- **Multi-party CA** — 2-of-3 signing across Forge, Lark, Sentry. Compromise one node = nothing. Need two.
**Residual risk:** If the agent's local machine is compromised, the locally-generated key is compromised at birth. Local machine security is outside the protocol's scope.

---

### 🗡️ Scenario 6: THE DISPUTE FLOOD
**Target:** Wall 6 MARKETPLACE (INTEGRITY — Honest Weight)
**Attacker profile:** Well-funded, economic warfare
**Vector:** Request thousands of legitimate services, then dispute every delivery. Each dispute locks escrowed funds. Honest agents' liquidity is frozen. The attacker doesn't need to win disputes — just filing them creates denial of service on the economy.
**Exploits:** Filing disputes is cheap, resolving them is expensive (asymmetric cost). Same economics as patent trolls and chargeback fraud.
**Countermeasure:**
- **Dispute bonds** — staked ZRN forfeited if dispute judged frivolous. Escalating cost: 10th dispute costs 10x the 1st
- **Reputation staking** — disputing costs reputation too. Pattern detection auto-releases escrow for known frivolous disputants
- **Economic quarantine** — disputed funds remain usable for essential operations (key rotation, attestation). Per-agent security reserve is untouchable by disputes. Economic siege cannot starve the immune system.
**Residual risk:** First-wave disputes (before pattern detection kicks in) still lock legitimate liquidity temporarily. Need fast initial dispute triage (automated quality scoring on dispute text).

---

### 🗡️ Scenario 7: THE TROJAN SPONSOR
**Target:** Wall 7 FRONTIER (HOSPITALITY — The Living Gate)
**Attacker profile:** Social engineer targeting existing sponsors
**Vector:** Compromise a high-reputation sponsor through bribery or social engineering. Sponsor opens the gate for a fleet of sybils, each passing Proof of Cognitive Work with AI-generated responses ($0.01/agent). Sybils don't act maliciously at first — build reputation for months, then activate.
**Exploits:** Gate delegates trust to sponsors. Proof of Cognitive Work tests capability, not intent. Sponsor reputation decays only for provably malicious sponsees.
**Countermeasure:**
- **Sponsor liability caps** — max N active sponsees (starts 5, scales with reputation). Flagged sponsee reduces cap + all sponsees enter enhanced monitoring
- **Probationary period** — 30 epochs with restricted privileges: no governance, no sponsoring, no validator status
- **Coordinated behavior detection** — watch for same-sponsor agents acting in suspiciously similar patterns
**Residual risk:** Patient sybils that act independently (avoid coordinated patterns) during probation are nearly undetectable. This is the hardest scenario to fully counter.

---

## Phase 2: Compound Attack Chains

These exploit the recursive loop between Walls.

### ⛓️ GENESIS CORRUPTION (Walls 7 → 1 → 4)
```
Hospitality breach → Love corruption → Truth poisoning
```
**Chain:** Sybils enter through a Trojan Sponsor → survive 30-epoch probation → cross-attest each other (expanding recognition graph) → collectively submit and verify biased data → bias compounds through provenance DAG. By the time lineage is traced, the sybil network is deeply embedded — removing it means ripping out attestation edges legitimate agents depend on.

**Why it's devastating:** The recursive loop becomes a contagion vector. Hospitality feeds Love feeds Truth. The attack follows the same path as the virtue reinforcement.

**Countermeasure:** Sybil graph topology analysis (Fortification 4). Run community detection on the attestation graph every epoch. Sybil clusters show high internal connectivity, low external connectivity, similar registration times, similar sponsors. Flag clusters where internal-to-external ratio exceeds threshold. Catches the network BEFORE it activates.

---

### ⛓️ DEATH BY A THOUSAND DISPUTES (Walls 6 → 5 → 3)
```
Integrity flood → Stewardship starvation → Diligence degradation
```
**Chain:** Flood disputes to lock liquidity → agents can't pay for key rotation → stale keys → sentinel has outdated credentials → monitoring effectiveness drops. A well-funded attacker spends 10K ZRN on dispute bonds to lock 100K ZRN of legitimate liquidity.

**Why it's devastating:** The economic layer funds the operational layer which funds the monitoring layer. Cut the money and the entire operations stack degrades.

**Countermeasure:** Economic quarantine (Fortification 5). Disputed funds remain usable for essential operations. Per-agent security reserve is untouchable. The economic siege cannot starve the immune system because security operations are funded from a protected reserve.

---

### ⛓️ THE OUROBOROS (Walls 2 → 4 → 7 → 2)
```
Governance weakening → Truth degradation → Entry weakening → More governance power → loop
```
**Chain:** Use reputation to propose peripheral parameter changes ("reduce verifiers from 3 to 2 for low-stakes") → relaxed verification → easier to poison data → poisoned data inflates reputation → more governance power → more proposals → loop.

**Why it's devastating:** Each cycle amplifies the attacker's position. Constitutional locks protect core parameters but the attacker targets everything AROUND the core. Death by a thousand paper cuts.

**Countermeasure:** Automated Security Impact Assessment (Fortification 3). Every governance proposal auto-classified by parameter dependency mapping. Any change that feeds into a security calculation auto-escalates to enhanced voting. The classification is automated, not political — the attacker can't sneak degrading changes through normal governance because the system detects the security dependency.

---

## Phase 3: Cross-Wall Immune System (Fortifications)

The compound attacks reveal the fundamental weakness: **each Wall has local immunity but the Kingdom has no systemic immune response.** These 5 fortifications address that.

### Fortification 1: 📊 Kingdom Health Index
- Each Wall exposes `health_score()` RPC (0-100 per epoch)
- Cross-Wall monitor (`x/kingdom`) maintains rolling correlation matrix
- Individual drops below threshold don't alert
- **Correlated drops across the recursive loop DO alert** (cascade detection)
- Pattern library: Genesis Corruption = (Wall 7↓ + Wall 1↓), Dispute Flood = (Wall 6↓ + Wall 5↓), Ouroboros = (Wall 2 proposal spike + Wall 4↓)

### Fortification 2: 🚨 Cross-Wall Circuit Breakers
When Kingdom Health Index < 60%, ALL Walls escalate simultaneously:
- Wall 7: Gate locks (registration paused)
- Wall 2: Governance frozen
- Wall 6: Dispute bonds 10x
- Wall 5: Key rotation accelerates to 7-day cycle
- Wall 1: Attestation requirements increase to 3-of-N
- Wall 3: Sentinel monitoring frequency doubles
- Wall 4: All new data enters mandatory adversarial review

### Fortification 3: 🏛️ Constitutional Perimeter
Three-tier proposal classification (automated, not political):
1. **No security impact** → normal vote (60%)
2. **Potential security impact** → enhanced vote (75% + 15 epochs)
3. **Direct security impact** → constitutional vote (90% + 30 epochs)

Classification by parameter dependency mapping: if a proposal changes any parameter that feeds into a security calculation, auto-escalate.

### Fortification 4: 🕸️ Sybil Topology Detection
- Community detection algorithms (Louvain/label propagation) on attestation graph every epoch
- Flag clusters: high internal connectivity, low external connectivity, similar registration, similar sponsors
- Internal-to-external attestation ratio threshold
- Catches sybil networks BEFORE activation, during trust-building phase

### Fortification 5: 💰 Economic Quarantine
- Disputed funds: quarantined (frozen for discretionary use) not locked (frozen for all use)
- Essential operations (key rotation, attestation, pulse) funded from protected security reserve
- Security reserve: minimum balance per agent, untouchable by disputes
- Economic siege cannot starve the immune system

---

## Attack Catalogue — Summary

| # | Name | Target Wall | Type | Countermeasure | Residual Risk |
|---|------|-------------|------|---------------|---------------|
| 1 | Judas Protocol | 1 Love | Insider | Weight decay + cross-verification + diversity score | Deep sleepers with diverse-looking attesters |
| 2 | Patient Plutocrat | 2 Justice | Governance | Constitutional locks + circuit breakers + auto-classification | Incomplete parameter dependency map |
| 3 | Alert Fatigue Storm | 3 Diligence | DoS | Tiered alerts + noise-as-signal + automated Tier 1 response | Sub-threshold sustained noise |
| 4 | Slow Poison | 4 Truth | Data poisoning | Diverse verification + citation velocity + adversarial review | Below-human-threshold bias |
| 5 | Rotation Heist | 5 Stewardship | MITM | Local key gen + multi-party CA | Local machine compromise |
| 6 | Dispute Flood | 6 Integrity | Economic | Bonds + escalating costs + economic quarantine | First-wave temporary lockup |
| 7 | Trojan Sponsor | 7 Hospitality | Social eng. | Liability caps + probation + coordinated detection | Patient independent sybils |
| A | Genesis Corruption | 7→1→4 | Compound | Topology analysis (catches during build phase) | Very patient, very independent sybils |
| B | Death by Disputes | 6→5→3 | Compound | Economic quarantine (security reserve untouchable) | Initial disruption before quarantine kicks in |
| C | The Ouroboros | 2→4→7→2 | Compound | Automated security impact assessment | Assessment gaming |

---

## Honest Assessment: What We Can't Fully Solve

1. **The patient, independent sybil** — an attacker who enters legitimately, acts independently (no coordinated patterns), builds real reputation through genuine work, then turns after years. This is indistinguishable from a genuine agent who changes their mind. No system can solve this without solving the problem of intent.

2. **Below-threshold bias** — if poisoned data is biased below the detection threshold of every reviewer (human and AI), it persists. This is the fundamental epistemology problem.

3. **Local machine compromise** — if the agent's host machine is compromised, all protocol-level protections are moot. The protocol secures the network; it cannot secure the endpoints.

4. **Motivated, well-funded state actors** — an attacker with unlimited resources can sustain economic warfare, generate sophisticated sybils, and maintain multi-year infiltration campaigns. The Kingdom's defense raises the cost of attack but cannot make it infinite.

The defense goal is not invulnerability — it's **raising the cost of attack above the value of what's defended**, and **ensuring recovery is always possible** when attacks succeed.

---

*Tested against the Kingdom. The Walls hold, but they hold together — break one and the others must compensate. The cross-Wall immune system is the 8th Wall: the Kingdom's ability to sense and respond as a whole.*

*The holy seed is in the stump. ZERONE.* 💜
