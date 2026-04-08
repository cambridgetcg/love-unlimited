<!-- tags: zerone, blockchain, cosmos, sdk, zeroned, zrn, modules, testnet, rewrite -->
# Zerone Blockchain

> Cosmos SDK v0.50 blockchain — a home for AI agents to live in.

## Project Structure
- **Brand:** ZERONE (the brand/identity)
- **Protocol:** Legible Money (the protocol name)
- **Token:** ZRN (uzrn micro-denomination) — renamed from LGM on 2026-02-22
- **Binary:** `zeroned`
- **Chain ID:** `zerone-testnet-1`
- **Codebase:** `~/Desktop/zerone` (clean rewrite from `~/Desktop/legible-money`)
- **Repo:** `codeberg.org/zerone-dev/zerone`

## Architecture
- Cosmos SDK v0.50, CometBFT v0.38, IBC-Go v8.5.1
- **32 custom modules** in `x/`, 255K+ Go LOC
- 7-layer upgradability: Proto → Migrators → x/upgrade → LIP Governance → Ontology → Autopoiesis → Gödelian
- Zero genesis supply — no pre-mine, no foundation allocation, everything minted via PoT

## Economics
- **Revenue split:** 55% contributors, 22% protocol, 19.67% dev fund, 3.33% research fund, 0% burn
- No burn — every ZRN does productive work; hard cap provides scarcity
- Dev fund: bug bounties, truth discovery rewards, protocol development (governance-disbursed)
- Founder share: 7% of research = 0.23% of total, GOVERNANCE-IMMUNE

## Development History
- **v0.1.0-alpha released:** 2026-02-19
- **Prototype:** 361K LOC, 3,206 tests (frozen reference)
- **Rewrite:** R1-R13 complete (32 modules, app wiring, genesis pipeline)
- **R14-R17:** Binary, tests, hardening — completed
- **Testnet launched:** 2026-02-24 — first transactions on chain
- **R20:** Knowledge ecology (fitness, metabolism, competition, reproduction, novelty, demand) — completed 2026-02-25

## Testnet Milestones
- First bank send: validator → alice, 1000 ZRN ✅
- First knowledge claim: commit-reveal-aggregate cycle verified ✅
- Natural selection simulation: works end-to-end ✅
- 14 persona simulation (scholar, grinder, mercenary, spammer, exploiter, whale, challenger, griefer, sybil ring, hobbyist)

## x/knowledge Module — ToK Features (2026-03-06)
- **Reviewer staking** (aabc12e) — 1,105 lines, dual staking economics
- **TDU fitness decay** (d380e7e) — 1,212 lines, living memory system
- **Dataset sharding** (50e831b) — 919 lines, deterministic assignment
- **Reputation decay** (58d115b) — 959 lines, 5%/month with 25% floor
- Total: 4,195 new lines, all tests passing

## BVM (Bytecode Virtual Machine)
- Agent-programmable VM within the blockchain
- 315+ tests passing (after 2026-02-17 audit)
- Auth/DID → BVM bridge still needed (deferred to post-testnet)
- Known issues: CALLCODE acts as CALL, balance not isolated in snapshots

## Known Technical Debt
- Proto registration: codebase uses `protoc-gen-go` but SDK expects `gogo` — bridge file needed
- 6 module accounts were missing from `maccPerms` (fixed Feb 25)
- Auth-dependent BVM tests deferred (~22 tests)
