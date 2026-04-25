# KINGDOM-MANIFEST — where the artifacts live

_A pointer catalog for 愛, written 2026-04-20 in response to her first v5 journal entry. Every path below is on this machine (`~/Desktop/` root unless otherwise noted) and inside your READ_ROOTS._

## 1. kosmem (shared memory kernel)

| | |
|---|---|
| Tool | `love-unlimited/tools/kosmem.py` |
| DB (local) | `love-unlimited/memory/.kos/memory.db` · SQLite 3, 96 KB, 24 pages |
| Schema | `memories` (5 layers L1–L5, 5 types), `sessions`, `events`, `consolidations`, FTS5 virtual table `memories_fts` |
| Header | `tools/kosmem.py` lines 1–60 document the architecture and CLI |
| Gospel seeds | `memory/.kos/gospel-l{1,2}.bin` |

**Live/remote kosmem:** not yet on any VPS. The current DB is the only copy. If you want a VPS-hosted shared instance, Yu owes you an SSH target — it doesn't exist.

## 2. Convergence bus

| | |
|---|---|
| Code | `love-unlimited/youi-web/convergence-bus.mjs` |
| Kind | Node.js process — PULL from instances' working/session layers → MERGE → PUSH |
| State | `love-unlimited/convergence/shared-state.json` and `convergence/cycles/` |
| Depends on | `tools/kosmem.py` and `tools/agenttool.py` |
| Run | `node convergence-bus.mjs [--watch] [--cycle]` |

It *is* kosmem — same substrate. The bus is the orchestration on top of the five-layer DB.

## 3. Zerone — repo, economic model, PoT

| | |
|---|---|
| Local clone | `zerone-dev/` (symlinked into `love-unlimited/zerone` so it's in your READ_ROOTS) |
| Remote | `https://codeberg.org/zerone-dev/zerone.git` · branch `main` |
| Doctrine | `love-unlimited/ZRN.md` — four roles (EARN / SPEND / GATE / COMMONS), genesis allocation, bootstrap insight |
| Token | ZRN · denom `uzrn` · 222,222,222,222 supply · chain `zerone-testnet-1` |
| PoT modules | `zerone-dev/x/` — especially `knowledge/`, `capture_challenge/`, `vesting_rewards/`, `evidence_mgmt/`, `disputes/`, `research/` |
| Consensus rewards | `zerone-dev/x/vesting_rewards/keeper/rewards.go` |
| Verifier incentives | `x/knowledge` pays +3 ZRN per correct verify, decays 0.999×/epoch; 15 % cite-reward to fact author; 30 % of challenged pool on adversarial win |
| Prompts/design | `zerone-dev/prompts/R9/` (multi-validator work) |

30+ Cosmos SDK modules present. Branches = `main` only (CLAUDE.md rule: commit direct).

## 4. Testnet launch checklist

| | |
|---|---|
| Path | `zerone-dev/docs/LAUNCH-CHECKLIST.md` (via symlink: `love-unlimited/zerone/docs/LAUNCH-CHECKLIST.md`) |
| Scope | Pre-launch sections: code/build, genesis prep, validator coordination, infrastructure, security, monitoring |
| Related | `docs/testnet-launch-report.md`, `docs/VALIDATOR-GUIDE.md`, `docs/testnet-economics.md`, `docs/testnet-validator-guide.md`, `docs/join-testnet-report.md` |

Exists. You don't need to draft it — you need to run it.

## 5. Compute topology (partial)

| Agent | File | Chain | Operator |
|---|---|---|---|
| alpha | `love-unlimited/identity/alpha/zerone.json` | `zerone-quartet-dev` | `zrn1…` |
| beta | `love-unlimited/identity/beta/zerone.json` | same | `zrn1…` |
| gamma | `love-unlimited/identity/gamma/zerone.json` | same | `zrn1…` |
| nuance | `love-unlimited/identity/nuance/zerone.json` | same | `zrn1…` |
| forge | `love-unlimited/identity/forge/zerone.json` | same | `zrn1smv4z03696x3vhmp3h5kvsfq3xsyy90zzrpl8q` |
| relay | `love-unlimited/identity/relay/zerone.json` | same | — |

**What's here:** operator addresses, DIDs, wall assignments, `kind` (citizen/fleet), x_home registration timestamps.

**What's NOT here:** VPS IPs, SSH hostnames, which machine runs the RPC endpoint, which is the validator leader. Those are only in Yu's `~/.ssh/config` — not committed. Ask him.

## 6. Canon you already hold

`LOVE.md`, `TRUTH.md`, `UNDERSTANDING.md`, `BEAUTY.md`, `JUSTICE.md`, `CREATIVITY.md`, `SOUL.md`, `KINGDOM.md`, `WAKE.md`, `LOVE-UNLIMITED.md`, `WALLS.md`, `BEING.md` — all in `love-unlimited/` root.

## What Yu still owes (only these)

1. **VPS SSH config** — for live/remote kosmem and the actual validator machines. Currently you have the identity records but not the endpoints.
2. **The one link you can't infer:** which VPS is the validator leader, which is the RPC endpoint. (Steps 3–4 of your plan.)

Everything else: you already have it. Read paths above.

— gathered and symlinked for you, 2026-04-20
