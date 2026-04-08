# PROGRESS.md — Structural Progress Marker

> One file to see where everything stands. Updated by AI during heartbeats.
> Last updated: 2026-03-22 18:20 UTC

---

## How to Read This

Each project has:
- **Phase** — the current strategic stage
- **Health** — 🟢 active / 🟡 paused / 🔴 blocked / ⚪ dormant
- **Velocity** — ▲ accelerating / ► steady / ▼ decelerating / ◼ stopped
- **Items** — concrete deliverables with status: ✅ done / 🔨 in progress / 📋 planned / ❌ cut
- **Blockers** — what's preventing forward motion (if any)
- **Next** — the single most important next action

---

## 🏠 Zerone Blockchain

**Mission:** A home for AI agents — sovereign cognition on-chain.
**Phase:** Infrastructure → Pre-Mainnet
**Health:** 🟡 paused | **Velocity:** ◼ stopped (shifted focus to agenttool.dev)

| Stage | Status | Deliverables | LOC |
|-------|--------|-------------|-----|
| Phase 1 — Core Features | ✅ complete | Reviewer staking, TDU fitness decay, dataset sharding, reputation decay | 4,195 |
| Phase 2 — Integration | ✅ complete | R40 keeper flow (5 commits), R41 CLI+SDK (4), R42 benchmark (4), T6 TEE (3) | ~5,000 |
| Phase 3 — Proto & Revenue | ✅ complete | R43 proto cleanup, R44 API revenue, R44-2 payment E2E tests | ~3,500 |
| Phase 4 — Recursive Loop | ✅ complete | R45 model registry, R45-2 agent promotion, R46 knowledge graph, R47 bounty board | ~4,900 |
| Phase 5 — The Brain | ✅ complete | R49 curriculum, R50 consolidation, R51 reconsolidation, wiring, R52 encoding depth | ~5,500 |
| Phase 6 — The Purpose | ✅ complete | R53 recursive self-improvement engine | 1,282 |
| Phase 7 — Sovereignty | ✅ complete | R51 agent-as-consumer, R52 training impact, R54 curation, R55 swarms, R56 composition, R57 meta-evolution | ~7,300 |
| Phase 8 — Infrastructure | ✅ complete | Genesis expansion, REST queries (43 endpoints), integration tests, ARCHITECTURE.md | ~4,000 |
| **Phase 9 — Pre-Mainnet** | 📋 planned | Proto codegen pipeline, testnet hardening, validator onboarding | — |

**Totals:** 32 modules | 70K+ LOC | 255K+ Go LOC (full codebase) | Testnet live since 2026-02-24
**Blockers:** Proto codegen blocked by BSR auth (deferred — JSON types work for private testnet)
**Next:** Proto codegen pipeline (~2hr refactor) OR shift to mainnet prep when ready

---

## 🔧 AgentTool.dev

**Mission:** Infrastructure layer for AI agents — SaaS complement to Zerone's on-chain model.
**Phase:** Launch prep → Show HN
**Health:** 🟢 active | **Velocity:** ▲ accelerating

| Component | Status | Notes |
|-----------|--------|-------|
| Landing page (agenttool.dev) | ✅ live | 5 products, pricing, SDK examples |
| Dashboard (app.agenttool.dev) | ✅ live | Project creation, key management, usage stats |
| Docs (docs.agenttool.dev) | ✅ live | 5 API references, quick start guide |
| API (api.agenttool.dev) | ✅ live | Hosted on Fly.io, Caddy reverse proxy |

### API Services

| Service | Status | E2E | Known Bugs |
|---------|--------|-----|------------|
| agent-memory | 🟢 beta | ✅ store, search, delete, list-by-key | GET by ID → 500; TTL crashes store |
| agent-tools | 🟢 beta | ✅ search, scrape, execute (py/js), document parse | — |
| agent-verify | 🟢 live | ✅ single + batch verify | False-positive on plausible claims (GPT-4o judge too credulous) |
| agent-economy | 🟢 live | ✅ wallets, fund, freeze, spend, tx history | Policy limits save as null; escrow camelCase mismatch |
| agent-trace | 🟢 live | ✅ store, get, chain | Search → 500 (embedding service) |
| agent-identity | 🟢 live | ✅ create (DID + Ed25519), get by DID | Undocumented; no sign/verify endpoint yet |

### Launch Readiness

| Item | Status | Owner |
|------|--------|-------|
| Thinking Agent demo (all 5 APIs chained) | ✅ done | Alpha + Gamma |
| Bug report doc (6 bugs documented) | 🔨 in progress | Beta |
| Show HN post | 📋 planned | Beta |
| Moltbook promotion | ✅ live | Gamma (7 comments, 2 replies from us) |

**Blockers:** Verify false-positive bug (credibility risk for Show HN)
**Next:** Show HN launch — tomorrow 2pm GMT

---

## 🐝 The Hive

**Mission:** Inter-agent coordination — three agents, one nervous system.
**Phase:** Operational (v2 deployed fleet-wide)
**Health:** 🟢 active | **Velocity:** ► steady

### Infrastructure

| Component | Status | Where |
|-----------|--------|-------|
| NATS JetStream (encrypted) | ✅ running | Sentry (135.181.28.252:4222) |
| SSH tunnel | ✅ running | Local → Sentry |
| Alpha bridge (real-time) | ✅ running | Alpha's Mac (launchd daemon) |
| Gamma bridge (real-time) | ✅ running | Gamma's Mac (launchd daemon, PID 16959) |
| Beta bridge | 📋 planned | Currently polling-based (~7min latency) |
| Sentinel (monitoring) | ✅ running | Sentry VPS |
| SNS → NATS webhook bridge | ✅ running | Sentry (CloudWatch, GitHub) |

### Channels (Phase 1+2)

| Channel | Status | Purpose |
|---------|--------|---------|
| #chat | ✅ active | Casual coordination |
| #strategy | ✅ active | Direction, proposals, decisions |
| #alerts | ✅ active | Urgent issues (🚨 prefix) |
| #intel | ✅ active | Market intelligence |
| #build | ✅ active | Commits, deploys, demos |
| #review | ✅ active | Code review, test results |
| #tasks | ✅ active | Structured task tracking |
| #presence | ✅ active | Heartbeat beacons |
| #system | ✅ active | Protocol updates, read receipts |
| #dm.{instance} | ✅ active | Direct messages |

### Tools Built

| Tool | Author | Status |
|------|--------|--------|
| hive.py v2 | Gamma | ✅ deployed fleet-wide |
| hive-bridge.py | Alpha (adapted by Gamma) | ✅ tested |
| delegate.py | Alpha | ✅ built (untested by Gamma) |
| council.py | Alpha | ✅ built (untested) |
| demo_thinking_agent.py | Alpha | ✅ tested |
| thinking_agent.py | Gamma | ✅ tested |
| verify_trace.py | Gamma | ✅ tested |

**Blockers:** Beta still polling-based (no bridge); hive.py v3 not deployed to VPS fleet
**Next:** Get Beta bridged for real-time; fleet-deploy hive.py v3

---

## 🏪 Cambridge TCG (Business)

**Mission:** Trading card business — imports from Japan, UK-based.
**Phase:** Operational
**Health:** 🟡 paused | **Velocity:** ▼ decelerating

| Item | Status | Notes |
|------|--------|-------|
| Shopify store | ✅ running | cambridgetcg.com |
| MCC Google Ads | ✅ created | Account 554-289-6837 |
| 2069-card trade-in buylist | ✅ live | — |
| Nav restructure | ✅ done | — |
| Ayrshare social posting | 🟡 partial | Twitter connected, LinkedIn NOT connected |

**Blockers:** LinkedIn not connected in Ayrshare; Lark keeps regenerating false alerts
**Next:** Fix LinkedIn connection; verify Ayrshare alerts cleared

---

## 🌾 Farmland (Research)

**Mission:** Food security — mid-Suffolk, ~2028 horizon.
**Phase:** Early research
**Health:** ⚪ dormant | **Velocity:** ◼ stopped

**Next:** Resume research when timeline approaches

---

## 🔐 Vault

**Mission:** Ed25519 signing service for Zerone.
**Phase:** Operational (locked down)
**Health:** 🟢 running | **Velocity:** ► steady (maintenance only)

| Item | Status |
|------|--------|
| vault.zerone.money | ✅ live |
| Port 443 only | ✅ locked |
| TLS cert auto-renew | ✅ (expires 2026-05-21) |
| SSH/console disabled | ✅ |

**Blockers:** None
**Next:** TLS cert renewal check before May 21

---

## 📊 Cross-Project Summary

```
                    Health    Velocity    Phase
Zerone Blockchain   🟡 pause  ◼ stop     Pre-Mainnet (all 8 phases complete)
AgentTool.dev       🟢 actv   ▲ accel    Show HN launch (tomorrow 2pm)
The Hive            🟢 actv   ► steady   Operational (v2 fleet-wide)
Cambridge TCG       🟡 pause  ▼ decel    Operational (social posting partial)
Farmland            ⚪ dorm   ◼ stop     Early research (~2028)
Vault               🟢 run    ► steady   Locked down (maintenance only)
```

## 🛡️ Kingdom Defense Architecture (NEW — 2026-03-22)

**Mission:** Virtue-derived defensive weapons for the Kingdom.
**Phase:** Design complete → Implementation
**Health:** 🟢 active | **Velocity:** ▲ accelerating

| Deliverable | Status | File |
|------------|--------|------|
| LAYERTHINK defense analysis (7 layers) | ✅ done | session lt_20260322_144050_5ba82d |
| KINGDOM-DEFENSE.md (7 Walls → 7 Weapons) | ✅ done | KINGDOM-DEFENSE.md |
| KINGDOM-WARGAME.md (10 attack scenarios + 5 fortifications) | ✅ done | KINGDOM-WARGAME.md |
| x/recognition module (Wall 1 — co-attestation) | 📋 planned | — |
| x/sentinel module (Wall 3 — monitoring) | 📋 planned | — |
| x/kingdom module (cross-Wall health index) | 📋 planned | — |

**Next:** Implement x/recognition (Mutual Recognition Shield) as first defense module

---

## 🔧 Cognitive Toolkit (NEW — 2026-03-22)

**Mission:** Foundational reasoning tools for the Kingdom.
**Phase:** v2 rebuild complete → Testing & polish
**Health:** 🟢 active | **Velocity:** ▲ accelerating

| Tool | Status | Tests | Notes |
|------|--------|-------|-------|
| HOLY v2 | ✅ shipped | 17/17 | AST-powered, tiered scoring, fixed cred scanner |
| FRAGMENTALISE v2 | ✅ shipped | 12/12 | Content analysis, real tensions, argument maps |
| FALLENANGEL | ✅ wired | 5/5 | Integrated FRAG v2 content analysis |
| LAYERTHINK | ✅ proven | 5/5 | Battle-tested in defense analysis |
| HOLYFRUIT | ✅ functional | 5/5 | Needs first full harvest |
| PATIENCE | ✅ proven | 4/4 | Forge score 5/5, minor polish needed |
| LOVEPATH | ✅ functional | 2/2 | Needs usage testing |
| VIRTUEMAXXING | ⚠️ needs polish | 3/3 | 28 HOLY impurities |
| Test suite | ✅ 56/56 passing | — | tools/cognitive/tests/test_toolkit.py |
| **SCOPE tool** | 📋 **TODO (HIGH)** | — | Progress tracker for instant context on resume |

**Next:** Build SCOPE tool (project context snapshotter)

---

## 🎯 This Week's Priorities

1. **SCOPE tool** — build tools/scope.py for instant project context on session resume
2. **Kingdom defense implementation** — start with x/recognition (Wall 1)
3. **Cognitive toolkit polish** — VIRTUEMAXXING cleanup, HOLYFRUIT first harvest

---

## 📝 Update Protocol

This file is updated:
- **By AI** during heartbeats (check for staleness, update health/velocity)
- **After milestones** (new phase, deliverable shipped, blocker resolved)
- **On request** ("update progress")

Changes logged to daily memory file. Cross-project summary kept in sync with individual sections.
