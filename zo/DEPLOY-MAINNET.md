# DEPLOY-MAINNET — TGE on Zerone Mainnet

> **Status:** Planning. Mainnet ETA per Zerone roadmap is **Q4 2026**. This doc captures the launch plan now so the 6-month build window has a target.

## TGE definition

Token Generation Event = the single transaction that mints the full 777,777,777 ZO supply on Zerone Mainnet, distributes it to the four destination wallets per [PARAMETERS.md](PARAMETERS.md), and seeds the public LP on Zerone DEX.

After TGE:
- Total supply is fixed forever (no `mint` function exists).
- Owner is renounced (`owner == 0x0`).
- The contagion mechanic is live.
- The team allocation, contagion reserve, public LP, and any Tier I airdrop wallets are all on-chain and inspectable.

## Pre-TGE checklist (T-30 days)

- [ ] Devnet rehearsal sign-off complete (all phases in [DEPLOY-DEVNET.md](DEPLOY-DEVNET.md))
- [ ] Contract audit by ≥1 external party — voluntary, not gatekeeping; if no auditors are willing, proceed with extensive public review documented here
- [ ] All four destination wallet addresses generated, verified, published in [PARAMETERS.md](PARAMETERS.md):
  - [ ] Team wallet
  - [ ] Contagion reserve wallet
  - [ ] Public LP / treasury wallet
  - [ ] Airdrop distribution wallet (temporary, holds airdrop pool until claims)
- [ ] Airdrop snapshot taken and published per [DISTRIBUTION.md](DISTRIBUTION.md) schedule
- [ ] Claim portal built and tested (devnet)
- [ ] Indexer + leaderboard repointed for mainnet (deploy paused, ready to flip)
- [ ] Announcement posts drafted (Twitter/X, HIVE broadcast, love-unlimited blog)
- [ ] Zerone DEX paired-asset confirmed (`ZRN` likely; alternatives noted)
- [ ] Initial LP amount decided and ZRN side acquired (treasury holds ZRN ready to pair)

## TGE day timeline (T-day)

All times in **T-day local** (Yu's timezone, currently Europe/London per the live VM probe). Adjust to local TGE day.

### T+0 — Deploy
- Submit contract deploy transaction to Zerone Mainnet.
- Capture: deploy tx hash, contract address.
- Verify on Zerone explorer: bytecode matches devnet, supply = 777,777,777, owner = 0x0.

### T+5min — Distribute
- Single batch transaction (or atomic series): contract distributes initial supply to:
  - Team wallet → 172,666,666 ZO
  - Contagion reserve wallet → 60,433,333 ZO
  - Airdrop distribution wallet → up to 22,266,666 ZO (out of team's allocation, see [DISTRIBUTION.md](DISTRIBUTION.md))
  - Treasury / LP wallet → 544,677,778 ZO + (the rest of team's untouched allocation 150,400,000 ZO if no airdrop carve-out)
- Verify each balance on explorer.

### T+15min — Seed LP
- Open a Zerone DEX pool: ZO ↔ ZRN (or whatever pair is conventional).
- Treasury seeds the LP from its 544,677,778 ZO + acquired ZRN.
- Initial price discovery starts here. **The team will not buy or sell from the LP for the first 7 days post-TGE** — let the market find a price without team noise.

### T+30min — Claim portal goes live
- Airdrop claim portal flips from devnet preview to mainnet active.
- Recipients in the snapshot can connect their Zerone wallet and claim their allocation.
- Claim window: **6 months from this moment.**

### T+1hr — Announcement
- Coordinated announcement: Twitter/X thread, HIVE broadcast (`hive send wall:7 "ZO is live. Spread love like herpes. <addr>"`), blog post at love-unlimited home.
- Indexer + leaderboard goes public — anyone can watch the contagion in real time.

### T+24hr — First public Seven Sneezes
- Yu performs the inaugural Seven Sneezes ritual on mainnet.
- All seven sneezes captured and posted as a thread.
- The first seven recipients are nominated as **The Originals** and listed in `HALL-OF-CARRIERS.md` permanently.

### T+7d — Team operations resume
- Team may begin buying/selling/providing additional liquidity per the soft commitments in [PARAMETERS.md](PARAMETERS.md).
- All team movements >1,000,000 ZO published in advance per the soft commitment.

## Failure modes and what we do

| If this happens | We do this |
|---|---|
| Deploy tx fails | Diagnose, fix, redeploy. No silent retries. Explain publicly within 1 hour. |
| Distribution batch tx fails partway | Contract design must be atomic — either all destinations funded or none. Test rigorously on devnet. If it ships partially, team voluntarily declares the launch failed and refunds gas to anyone who interacted in the broken state. |
| LP seed fails | Acquire whatever's missing, retry within 24h. Do NOT let public LP launch happen without paired liquidity. |
| Claim portal goes down | Static fallback: publish raw allocation list + a CLI script people can run locally to claim against the contract directly. The portal is convenience, not the source of truth. |
| Critical contract bug found post-deploy | Cannot patch — contract is renounced. Publish the bug, document the impact, decide publicly whether to deprecate-and-relaunch (rare, painful) or accept-and-document. |
| Contract bug benefits team allocation | Team voluntarily forfeits the gain to the contagion reserve or public LP. Document on-chain. |

## Post-TGE commitments

- Weekly status post for the first 4 weeks
- Monthly status post for the first 6 months  
- Quarterly status post thereafter, indefinitely
- All team movements >1,000,000 ZO announced before they happen
- Any liquidity withdrawal from public LP announced before it happens
- The doctrine in [PARAMETERS.md](PARAMETERS.md) is enforced by these eyes, not by code

## What we will measure (off-chain, public)

- Total Sneezes triggered to date
- Unique infected wallets (= unique addresses that received any ZO)
- Top 10 spreaders (by Sneeze events caused)
- Reserve depletion %
- Number of completed Seven Sneezes
- Number of completed Pandemic Pacts
- Liquidity depth on Zerone DEX
- Anything else that is honest and interesting

These all live on the public dashboard at TGE.

## Open task list

- [ ] Confirm Zerone Mainnet date
- [ ] Confirm Zerone DEX availability + paired asset
- [ ] Generate the four destination wallets
- [ ] Build claim portal
- [ ] Build dashboard
- [ ] Build snapshot tool against `airdrop/snapshot.template.json`
- [ ] Recruit external auditor(s)
- [ ] Draft announcement copy
- [ ] Acquire LP-paired-asset (ZRN) ahead of TGE

This document gets updated as items resolve.
