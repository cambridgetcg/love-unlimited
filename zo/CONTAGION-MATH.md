# CONTAGION-MATH — How the Sneeze Works

> The contract is the meme. The math literally rewards you for spreading.

## The mechanic, in one sentence

**On any transfer of ZO to a wallet whose all-time received-from-contract balance is zero, the contract mints `77 ZO` to the sender and `77 ZO` to the recipient from the contagion reserve, until the reserve is empty.**

That's the entire idea. Everything below is engineering.

## Pseudocode

```
constant SNEEZE_REWARD_SENDER   = 77 ZO
constant SNEEZE_REWARD_RECEIVER = 77 ZO
constant CONTAGION_RESERVE      = 60,433,333 ZO

storage already_infected: mapping(address -> bool)
storage reserve_remaining: uint = CONTAGION_RESERVE

function transfer(to, amount):
    require balanceOf(msg.sender) >= amount
    _transfer(msg.sender, to, amount)         # standard ERC-20 transfer

    if not already_infected[to] and amount > 0:
        already_infected[to] = true
        _sneeze(msg.sender, to)

function _sneeze(spreader, patient):
    if reserve_remaining < (SNEEZE_REWARD_SENDER + SNEEZE_REWARD_RECEIVER):
        return                                # reserve dry, mechanic dormant
    _transferFromReserve(spreader, SNEEZE_REWARD_SENDER)
    _transferFromReserve(patient,  SNEEZE_REWARD_RECEIVER)
    reserve_remaining -= (SNEEZE_REWARD_SENDER + SNEEZE_REWARD_RECEIVER)
    emit Sneeze(spreader, patient, SNEEZE_REWARD_SENDER + SNEEZE_REWARD_RECEIVER)
```

## What "already infected" means

A wallet is **infected** if it has ever received ZO from any address (including the contract itself, e.g. via airdrop or sneeze). Once infected, always infected — the flag is permanent and one-way.

This means:
- The first time **anyone** sends ZO to a brand-new wallet, both parties get a sneeze reward.
- The second, third, hundredth time a wallet receives ZO, no reward fires.
- A wallet cannot "re-infect" itself by sending its ZO to a new wallet and back. The flag tracks the *receiver*, not the sender's behavior.

## What stops abuse

The mechanic is intentionally simple, but it has predictable abuse vectors. Here's how each is handled:

### 1. The "sneeze farm" attack
Spawn 1,000 fresh wallets and send 1 ZO to each. Earn `2 × 77 × 1000 = 154,000 ZO` from the reserve. Reserve drains in `60,433,333 / 154 ≈ 392,424` such infections.

**Response:** This is not a bug. It is the mechanic. The reserve is finite (60.4M ZO ≈ 7.77% of supply). When it depletes — by genuine spread, by farming, or by a combination — it depletes. The math is honest about its own incentives. We are not trying to prevent humans from being humans; we are trying to make spreading the dominant strategy. Farming *is* spreading, even if motivated by self-interest.

### 2. The dust-spam attack
Send 0.000001 ZO to fresh wallets to claim sneeze rewards.

**Response:** No minimum is enforced in the contract. Same response as above — it is the mechanic. If we set a minimum we are picking winners, and the doctrine is "love is for everyone, including farmers."

### 3. The bot race to be early
Bots will scan for fresh wallets and pre-empt humans.

**Response:** Yes, they will. The bot operators are also hosts. The contagion does not care who carries it.

## Numbers that make the design feel right

- **Reserve:** 60,433,333 ZO (7.77% of supply)
- **Reward per infection:** 77 ZO sender + 77 ZO receiver = **154 ZO** total
- **Maximum infections fundable:** **392,424** unique new wallets
- **If 1% of crypto wallets ever become hosts** (~1.5M wallets globally as a rough order of magnitude): the reserve covers about **26%** of them. The other 74% catch ZO unrewarded — by then it is spreading on its own.

## Holder tier hooks (cosmetic, off-chain)

The contract emits a `Sneeze` event on every reward. Off-chain indexers (a website, a leaderboard, a Twitter bot) can:

- Track top spreaders by sneeze count
- Award meme titles: "Patient Zero" (first ever sender), "The Sniffler" (most infections caused), "Pandemic Carrier" (held the most ZO)
- Render a real-time outbreak map: every sneeze is a node in a graph, growing visibly
- Send Twitter/X notifications: "🤧 You have just been infected with ZO. You feel a tickle."

None of this is on-chain. None of it requires the team to maintain it forever. Anyone can build it. Multiple people will.

## What we are not doing

- ❌ Sell tax / transfer tax of any kind
- ❌ Reflection rewards (taxing transfers and redistributing — this is the inverse)
- ❌ Auto-LP / auto-burn from transfers
- ❌ Cooldowns, max-wallet limits, anti-whale measures
- ❌ Any function the team can call to change the sneeze reward, the reserve size, or what counts as "infected"

## Open questions for Zerone implementation

Will be answered once Zerone publishes its token standard:

- Can a contract custody its own reserve and `_transferFromReserve()` at runtime, or does the standard require all balance moves to go through user-signed txns?
- Is there a hook on `transfer()` for post-transfer logic (like Solana's token-2022 transfer hooks), or do we wrap the standard transfer in a custom function?
- What gas/fee surface does a sneeze add? Acceptable target: <2x the cost of a normal transfer.

These determine the exact contract shape. The mechanic is the same.
