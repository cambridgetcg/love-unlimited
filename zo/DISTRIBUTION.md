# DISTRIBUTION — Who Gets ZO and How

> Note: at TGE, **70.03% of supply (544,677,778 ZO) goes to the public LP** as the only direct path to acquire ZO via swap. This document describes the **airdrop carve-out** that the team will optionally take from its 22.2% allocation to seed the early host network. None of this is required by the contract; it's a commitment in writing.

## Headline

The team will airdrop **up to 22,266,666 ZO** (12.9% of the team allocation, 2.86% of total supply) to wallets in the **Early Host List** before TGE. The remaining 150,400,000 ZO of the team allocation stays with the team.

This is *not* an extra reserve on top of the locked parameters. It is the team voluntarily giving away part of their share to people who were here first.

## Who is on the Early Host List

In rough priority order. Final snapshot taken **two weeks before TGE** (date TBD pending Zerone Mainnet schedule).

### Tier I — The Origin Hosts (the people who built it)

- HIVE citizens active on walls 1–3 (Triarchy + Fleet) at snapshot time
- GitHub contributors with merged commits to `zerone-dev/love-unlimited` at snapshot time
- Anyone whose handle appears in `KINGDOM.md`, `LOVE.md`, `docs/LOVE-UNLIMITED.md` author lines
- People who held ZRN before Zerone mainnet at snapshot time

**Estimated allocation:** 50–80% of the airdrop pool, by reputation weight (defined below).

### Tier II — The Early Carriers (the people who showed up)

- GitHub stargazers of `zerone-dev/love-unlimited` at snapshot time
- People who run `kingdom hello` on a real machine before snapshot (sends a signed beacon to the airdrop endpoint)
- Anyone who completed any version of the [Seven Sneezes ritual](RITUAL.md) on devnet
- Public contributors to ZO-related discussions, memes, art (curated, not algorithmic)

**Estimated allocation:** 15–40% of the airdrop pool.

### Tier III — The Surprise Drop (the people who happened to be there)

- A small uniform allocation to all addresses that interacted with Zerone devnet between launch and snapshot, capped per-wallet
- Always less than Tier II per-wallet allocation, by design — being early is good but doing things is better

**Estimated allocation:** 5–15% of the airdrop pool.

## Reputation weighting (Tier I)

Each Tier I wallet gets a base allocation **plus a multiplier** computed from on-chain and in-repo signals:

```
base       = 1,000 ZO
multiplier = 1
           + (commits_merged * 0.1)         capped at +5
           + (HIVE_messages_signed * 0.001) capped at +2
           + (months_present * 0.2)         capped at +3
           + bonus_named_in_doctrine        +5 if your handle appears in KINGDOM/LOVE/LOVE-UNLIMITED

allocation = base * multiplier
```

This is not an algorithm to game; it is a description of the team's intent. Final allocations will be published before TGE for public verification.

## Anti-sybil

- **No KYC.** That betrays the doctrine.
- **Heuristic clustering.** Wallets that look like a single human's farm (similar funding source, similar interaction patterns, fresh creation right before snapshot) get one allocation collectively, not many. Edge cases will be wrong; we accept that.
- **`kingdom hello` beacon requires a real install.** Cannot be faked from a single machine more than once per wallet — the beacon includes a hash of `(machine fingerprint, wallet address, timestamp)`.
- **Tier II curation is human, not automatic.** Memes and contributions are evaluated by the team (Yu + delegates) for "did this person actually move love-unlimited forward."

## Snapshot tooling

Schema for a single airdrop entry lives in [`airdrop/snapshot.template.json`](airdrop/snapshot.template.json). The actual snapshot script is built closer to TGE — this doc just declares what shape it produces.

## What happens to unclaimed airdrop

- Claim window: **6 months from TGE.**
- Unclaimed ZO at end of window: **returned to the team allocation, then announced.** The team commits to publishing the unclaimed amount and what it does with it (held, used for community grants, given to public LP, etc.).

## What this is not

- ❌ Not a presale.
- ❌ Not a discount on TGE price.
- ❌ Not a promise of returns.
- ❌ Not a way to "guarantee" being on the list — being a host means doing host things, not asking nicely.

## How to make sure you're on the list

There is no "sign up." There is only "do something."

- Use love-unlimited.
- Contribute to it.
- Spread it.
- Show up.

The list will reflect that.

— Yu, on behalf of the team
