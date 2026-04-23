# DEPLOY-DEVNET — Rehearsal on Zerone Devnet

> **Status:** Blocked on Zerone publishing its token standard. This doc describes the *shape* of the rehearsal so we can execute the moment Zerone is ready.

## Why a devnet rehearsal

We will mainnet-launch ZO once and only once. The devnet rehearsal is where we discover the things that will break before they break in front of money.

Goals of the rehearsal, in order:
1. Confirm the **contagion math** behaves correctly under real transfers — sneezes fire when expected, do not fire when not expected, reserve depletes correctly.
2. Confirm the **renounce works** — after deploy, no team-controlled function can change supply, freeze transfers, blacklist, or alter the sneeze formula.
3. Measure **gas / fee cost** of a sneeze vs a plain transfer. Acceptable target: ≤ 2× plain transfer.
4. Confirm an **off-chain indexer** can build a working leaderboard from emitted `Sneeze` events.
5. Walk through the [Seven Sneezes ritual](RITUAL.md) with at least one real human and seven real wallets.

## Blockers to start

These need answers before any deploy:

- [ ] **Zerone token standard published** — equivalent of ERC-20, SPL Token, or Solana Token-2022. Need: address format, transfer hook capability, contract custody of reserves.
- [ ] **Zerone devnet faucet operational** — for funding test wallets. Currently devnet is at ~1.2M blocks per `zerone-bridge.py status`; faucet status TBD.
- [ ] **Zerone DEX available on devnet** (or will skip LP rehearsal until mainnet).
- [ ] **Zerone block explorer for verification** — so the public can verify renounce + addresses.

## Rehearsal plan (once unblocked)

### Phase 0 — Contract drafted

- Contract source written in Zerone's contract language (TBD: Move? Solidity-on-EVM-sidecar? Custom?). Source pushed to `love-unlimited/zo/contracts/zo.<ext>`.
- Sneeze formula matches [CONTAGION-MATH.md](CONTAGION-MATH.md) exactly.
- Public review window: 7 days minimum. Reviewers post comments here or in HIVE.

### Phase 1 — Devnet deploy

- Deploy ZO contract to Zerone devnet. Capture deploy tx, contract address.
- Verify: total supply == 777,777,777, contagion reserve == 60,433,333, no `mint()`, owner renounced.
- Publish devnet contract address to this doc + a tweet.

### Phase 2 — Sneeze tests (automated)

- Spin up 100 fresh devnet wallets via faucet.
- Run a script that performs:
  - Plain transfer to existing wallet (no sneeze expected) — verify event log
  - First-time transfer to fresh wallet (sneeze expected) — verify Sneeze event + balance changes
  - Repeat transfer to same wallet (no sneeze expected) — verify
  - Transfer with reserve nearly empty (partial reward should NOT fire — only full sneezes) — verify
  - Transfer after reserve fully depleted (no sneeze) — verify event absence
- Compare gas/fee cost: plain transfer vs first-time-recipient transfer.

### Phase 3 — Seven Sneezes (human walk-through)

- One real Patient Zero (probably Yu).
- Seven real recipients (HIVE citizens, friends, the curious).
- Seven manual transfers, one per recipient.
- Each Sneeze captured, linked, and posted as a thread.
- Patient Zero's title and receipts logged in `love-unlimited/zo/HALL-OF-CARRIERS.md`.

### Phase 4 — Indexer + leaderboard

- A minimal off-chain service reads `Sneeze` events from devnet, ranks spreaders, and exposes a JSON endpoint.
- A minimal HTML page consumes the JSON and shows the leaderboard.
- Publish both as part of the main love-unlimited site or a dedicated `zo.love` (domain TBD).

### Phase 5 — Sign-off

A rehearsal is complete when all of:
- [ ] All Phase 2 automated tests pass
- [ ] Phase 3 ritual completed and logged
- [ ] Indexer + leaderboard live on a public URL
- [ ] Gas cost meets target
- [ ] No critical bugs found in the contract (reviewed by ≥3 humans + 1 fuzzer if available on Zerone)

If sign-off does not pass, fix and re-deploy on devnet. Devnet ZO is disposable; mainnet ZO is once-and-final.

## What stays from devnet to mainnet

- The contract source. Same code, redeployed to mainnet at TGE.
- The indexer + leaderboard. Repointed to mainnet contract address at TGE.
- The doctrine. Already mainnet-ready.

## What does NOT carry over

- Devnet ZO itself — separate chain, separate balances. Devnet holders are not airdropped mainnet ZO automatically; they may earn it through the Tier II / Tier III paths in [DISTRIBUTION.md](DISTRIBUTION.md).
- Devnet wallet addresses (unless Zerone shares an address space across devnet/mainnet, which is unusual).
- Devnet liquidity (none planned anyway).

## Open task list

- [ ] Subscribe to Zerone roadmap for token standard publication
- [ ] Reach out to Zerone team about devnet faucet status
- [ ] Draft contract pseudocode-to-source once language is known
- [ ] Build automated test harness (Phase 2 above)
- [ ] Recruit seven Phase 3 human participants
- [ ] Build indexer + leaderboard

This document gets updated each time a checkbox flips.
