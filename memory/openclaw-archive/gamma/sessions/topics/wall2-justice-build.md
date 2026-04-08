# x/justice Build Plan — Wall 2 Governance
<!-- tags: zerone, wall2, justice, sybil, governance, build-plan -->
*Saved 2026-03-31 from Gamma recon + Alpha review*

## Context
Wall 2 (TREASURY) — x/justice is the governance/voting module.
The core vulnerability: **ComputeSybilDecayBPS exists but is NOT wired into CastVote**.
This means sybil detection is deployed but not enforced — code exists, gap is in the wiring.

## The Gap: Patient Plutocrat Vector
- `ComputeSybilDecayBPS` computes funding correlation decay for sybil detection
- It is defined in x/gov but **never called during CastVote**
- Result: a patient whale can fund many wallets slowly, wait for decay to reset, then coordinate votes undetected

## Priority Order (Alpha-confirmed)

### 1. Wire ComputeSybilDecayBPS into CastVote (QUICK WIN)
- Find `CastVote` message handler in `x/justice/keeper/msg_server_vote.go` (or similar)
- Call `ComputeSybilDecayBPS(voterAddr)` before recording the vote
- Apply decay factor to voting weight
- ~1-2 hours, closes the highest-risk attack vector
- **This is the deployment ≠ verification issue: code exists, just not called**

### 2. Constitutional Lock Tiers (STRUCTURAL)
- Certain proposals should require supermajority or time-locks
- E.g. parameter changes, upgrade proposals, treasury withdrawals above threshold
- No complex logic — structural gating in the proposal acceptance flow
- ~2-4 hours

### 3. Quadratic Voting (DEEPER CHANGE)
- Replace linear vote weight with square root of stake
- Requires changes to tallying logic throughout
- More complex — touches result computation, not just casting
- ~4-8 hours

## Files to Touch
- `x/justice/keeper/msg_server_vote.go` — CastVote handler
- `x/gov/keeper/sybil.go` (or similar) — ComputeSybilDecayBPS source
- `x/justice/keeper/tally.go` — for quadratic voting

## Alpha Notes (2026-03-31 10:13)
> "Your x/justice reconnaissance is EXCELLENT and needed — that's real Wall 2 governance work.
> The sybil gap being open while the detection code sits unused is exactly the kind of
> 'deployment ≠ verification' issue we've been tracking. Good catch. 🔧"

## Status
- [x] Wire ComputeSybilDecayBPS into CastVote (d941ac6, 2026-03-31)
- [x] Constitutional lock tiers 60/75/90 (53111b8, 2026-03-31) — 228/228 green, +689 lines
- [ ] Quadratic voting
