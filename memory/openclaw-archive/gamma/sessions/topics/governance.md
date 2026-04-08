<!-- tags: governance, multisig, research-fund, revenue, ledger, founder-share -->
# Governance & Research Fund

> How decisions are made and money flows.

## Research Fund — 2-of-2 Multisig
- **Both signatures required** for any spend
- **Yu's key:** Ledger Nano X (secp256k1 via Cosmos app)
- **AI's key:** Vault Ed25519 on zerone server
- **Voting:** Yu gets 1 vote, AI gets 1 vote (AI consults 3 codebase instances)
- **Veto:** Either party can block — unanimous consent required

## Addresses
- **Yu's address:** `lgm1g0q9amg6l666rtee23xjcser4h9wgk8yncedtg`
- **Yu's pubkey (secp256k1):** `A3Bi9pkAomYcoVOdIczr64+3OxYsLBKg0NliywiysXAY`
- **Yu's Cosmos Hub address:** `cosmos1g0q9amg6l666rtee23xjcser4h9wgk8yvp8j03`
- **AI's address:** `lgm1cgjw09mg6ylc2mwmk6jp8n2yth2ex9jganhptc`
- **Research Fund multisig:** `lgm120p3d4hhy3dwvpfskpslmpzltclz2vyq0lswp6`
- **Threshold:** 2-of-2
- **Status:** Multisig created, pending genesis integration

## Revenue Split (Production Target)
3.33% Research Tax from block rewards:
- 86% → Research treasury (multisig `lgm120p3...`)
- 7% → Founder operations (Yu, Ledger only)
- 7% → AI operations (Vault + Yu cosign, same multisig — needs module-level sub-accounting)

### Code Change Pending
Add `AIOperationsShareBps` + `AIOperationsAddress` to vesting_rewards params, mirror founder split logic in `DepositToResearchFund`

## Founder Share
- 7% of research fund = 0.23% of total supply
- **GOVERNANCE-IMMUNE** — `ValidateFounderShareImmutability()` rejects changes once set
- Only modifiable via code upgrade (upgrade-category LIP)

## Protocol Revenue Split (Overall)
- 55% contributors
- 22% protocol
- 19.67% development fund
- 3.33% research fund
- 0% burn (every ZRN does productive work)
