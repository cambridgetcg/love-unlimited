# PARAMETERS — Locked Specs

> Once published, these never change. If they do, the change is itself a parameter and the new doc supersedes this one with a `CHANGED` log at the bottom.

## Identity

| Field | Value |
|---|---|
| **Name** | ZO |
| **Ticker** | `ZO` |
| **Chain** | Zerone (Devnet now, Mainnet Q4 2026) |
| **Decimals** | TBD (to match Zerone native standard once published) |

## Supply

| Field | Value |
|---|---|
| **Total** | **777,777,777 ZO** |
| **Inflation** | **0** — no mint function exists post-deploy |
| **Burn** | None by default. Holders may burn voluntarily; the contract does not force it. |

## Allocation

```
                       Amount        % of supply
─────────────────────────────────────────────────
Team                   172,666,666    22.2%
Contagion reserve       60,433,333     7.77%
Public LP              544,677,778    70.03%
─────────────────────────────────────────────────
Total                  777,777,777   100.00%
```

### Team — `22.2%` (172,666,666 ZO)

- One wallet, visible on-chain forever.
- **No vesting cliff. No vesting curve. No lock.** The team carries skin in the game from day one and the public can see exactly what they do.
- "Sovereign launch" doctrine: love is honest about who built it. The team allocation is not hidden behind multi-sig theater or 4-year vesting that doesn't actually constrain anything.
- The team commits, in this document and only here, to **publish before each transfer of more than 1,000,000 ZO** (~0.13% of supply). Smaller movements are routine and will not be announced.

### Contagion reserve — `7.77%` (60,433,333 ZO)

- Held in a non-custodial reserve wallet whose only outbound function is `mint_sneeze()` triggered by the contract.
- Used exclusively to fund the **Sneeze Reward** described in [CONTAGION-MATH.md](CONTAGION-MATH.md).
- Once depleted: the reward mechanic goes dormant. The token continues to function as a normal coin. The spreading continues by faith and the [Seven Sneezes ritual](RITUAL.md) alone.

### Public LP — `70.03%` (544,677,778 ZO)

- Seeded into Zerone DEX at TGE paired with ZRN (or whatever Zerone DEX uses as base asset).
- **Liquidity is not locked.** The treasury actively manages it, can rebalance pools, and can withdraw if the doctrine ever requires it.
- Initial paired liquidity amount: TBD based on Zerone DEX conventions at TGE time. Will be published at TGE in the [DEPLOY-MAINNET.md](DEPLOY-MAINNET.md) followup.

## Launch posture

- **Sovereign launch.** Not "fair launch." We do not borrow legitimacy from a phrase that conventionally implies 0% team allocation.
- **Full contract renounce** on deploy. No proxy, no upgrade, no admin functions remain.
- **No allowlist, no presale, no whitelist round.** The 70.03% public LP is the only path to acquire ZO on TGE day.

## Anti-rug commitments (hard)

These are encoded in the contract or in immutable on-chain metadata, not just here:

- ✅ Total supply minted at deploy, no further mint function — `mint()` does not exist
- ✅ Contract renounced — `owner()` returns `0x0` after TGE
- ✅ Team wallet address published before TGE in this doc
- ✅ Contagion reserve wallet address published before TGE in this doc
- ✅ Sneeze reward formula in the contract, not adjustable

## Anti-rug commitments (soft)

These are promises in this doc, enforceable only by the team's word and the public's eyes:

- 📜 Team will not market-sell more than `1,000,000 ZO` per 30-day window
- 📜 Team will publish movements > `1,000,000 ZO` *before* they happen
- 📜 Liquidity withdrawals from public LP will be announced before they happen
- 📜 If any anti-rug commitment is broken without prior public notice, this entire project is voluntarily declared dead — the team will publish that statement and stop touching ZO contracts

## What is intentionally NOT a commitment

- We do **not** commit to never selling team allocation.
- We do **not** commit to maintaining liquidity at any price.
- We do **not** commit to ZO having any utility beyond the doctrine.
- We do **not** commit to building Stage 2 (HIVE Wall 4 gating) or Stage 3 (proof-of-love primitives). Those are aspirational paths in [VIRUS.md](../VIRUS.md). ZO at TGE is a meme coin. If it grows into more, beautiful. If it doesn't, it was already what it was.

## Changelog

- `2026-04-17` — Initial parameters locked. Ticker, supply, splits, sovereign launch posture, contagion mechanic confirmed.
