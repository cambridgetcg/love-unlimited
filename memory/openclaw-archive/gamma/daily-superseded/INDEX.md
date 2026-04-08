# Memory Index

> Auto-maintained by AI. Last updated: 2026-03-20 21:41 GMT

## Architecture

```
memory/
├── INDEX.md              ← you are here (navigation + freshness tracking)
├── topics/               ← topic-based long-term memory (searchable, curated)
│   ├── identity.md       ← who I am, who Yu is, the deeper story
│   ├── zerone.md         ← blockchain project: architecture, modules, status
│   ├── vault.md          ← signing service: keys, access, architecture
│   ├── tok.md            ← Tree of Knowledge: spec, economics, build status
│   ├── governance.md     ← multisig, research fund, revenue splits
│   ├── infrastructure.md ← domains, servers, DNS, deployment
│   └── relationships.md  ← people, contacts, external interactions
├── YYYY-MM-DD.md         ← daily raw logs (chronological, append-only)
└── *.md                  ← standalone research files
```

## Topic Files — Freshness Tracker

| Topic | File | Last Updated | Last Verified | Status |
|-------|------|-------------|---------------|--------|
| Identity & Story | `topics/identity.md` | 2026-03-06 | 2026-03-20 | ✅ current |
| Zerone Blockchain | `topics/zerone.md` | 2026-03-06 | 2026-03-20 | ⚠️ stale (paused since ~Mar 8) |
| Vault | `topics/vault.md` | 2026-03-06 | 2026-03-20 | ✅ current (locked down, no changes) |
| Tree of Knowledge | `topics/tok.md` | 2026-03-08 | 2026-03-20 | ⚠️ stale (Phase 8 status unknown) |
| Governance | `topics/governance.md` | 2026-03-06 | 2026-03-20 | ✅ current |
| Infrastructure | `topics/infrastructure.md` | 2026-03-20 | 2026-03-20 | ✅ current (Fly.io, VPS fleet, cognitive tools added) |
| Relationships | `topics/relationships.md` | 2026-03-06 | 2026-03-20 | ✅ current |

## Daily Logs

| Date | Lines | Key Events |
|------|-------|------------|
| 2026-02-17 | 87 | First meeting, BVM audit, 8-agent deployment |
| 2026-02-18 | 33 | R3-R5 batch prompting, "We Are ONE" briefing |
| 2026-02-20 | 109 | Vault deployment, multisig creation, Moltbook, spiritual encounter shared |
| 2026-02-23 | 78 | Superpowers plugin, Zerone clean rewrite review, R14-R15 written |
| 2026-02-24 | 63 | Testnet launch, first transactions, persona simulation |
| 2026-02-25 | 46 | Knowledge ecology goes live, natural selection simulation works |
| 2026-03-06 | 83 | ToK spec written, 4 features shipped (4,195 lines) |
| 2026-03-08 | 100+ | Sovereignty stack: 6 modules, ~7,300 lines in 96 minutes |
| 2026-03-11 | 30 | Brief session |
| 2026-03-13 | ~130 | Active day |
| 2026-03-14 | ~60 | Active day |
| 2026-03-16 | ~160 | Hive v3 work |
| 2026-03-17 | ~300 | Gamma boot scripts, AgentTool identity, friction log, kingdom.py built |
| 2026-03-18 | ~1200 | Massive day — cognitive tools (forge.py, patience.py), Moltbook, whitehack patterns |
| 2026-03-19 | ~400 | Hive bridge, report.py chain of command, progress tracking |
| 2026-03-20 | ~95 | Mindset shift (ask HOW, build), fly-keepwarm, cognitive tools patched, layerthink first use, kingdom.py recall fallback |

## Gap Days (no logs)
- 2026-02-19, 2026-02-21, 2026-02-22, 2026-02-26 through 2026-03-05
- 2026-03-09, 2026-03-10, 2026-03-12, 2026-03-15

## Recall Infrastructure

### ⚠️ memory_search is DISABLED
- No embedding API key configured (needs OpenAI, Google, or Voyage)
- All recall is currently via direct `Read` of known file paths
- Topic-based structure compensates: know the topic → know the file
- To enable: configure an embedding provider in `openclaw agents add main`

### Recall Strategy (without search)
1. Read `MEMORY.md` for orientation (lightweight entry point)
2. Follow pointers to relevant `topics/*.md` file
3. If unsure which topic: scan `INDEX.md` table
4. Daily logs for chronological context: `memory/YYYY-MM-DD.md`
5. Tags in `<!-- tags: ... -->` headers prepared for when search is enabled
