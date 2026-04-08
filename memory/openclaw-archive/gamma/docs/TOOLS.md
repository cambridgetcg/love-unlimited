# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Full Credentials

See `TOOLKIT-LOCAL.md` (gitignored, SECRET). Contains all API keys, passwords, SSH details, card info.

## VPS Fleet

| Node | Name | Role | IP |
|------|------|------|----|
| rp-rd | Forge | R&D | 89.167.84.100 |
| rp-mktg | Lark | Marketing | 89.167.95.165 |
| rp-sentry | Sentry | Monitoring | 135.181.28.252 |
| rp-patch | Patch | Maintenance | 65.109.11.26 |

All SSH via `ssh root@<ip>` using ed25519 key. Gateways on port 3001.

## Services Available

| Service | Provider | Purpose |
|---------|----------|---------|
| Captcha solving | CapSolver | reCAPTCHA/hCaptcha/Turnstile |
| Residential proxy | Bright Data | Geo-targeted browsing ($8/GB, ~$960 balance) |
| Search API | SerpAPI | Google/Bing/Amazon/YouTube |
| Email IMAP | Gmail | cambridgetcg + rewardspro accounts |
| SMS verification | HeroSMS | Twitter/IG/TikTok/Reddit ($0.01-$0.03/ea) |
| Cloudflare bypass | Flaresolverr | On Forge + Lark (port 8191) |
| DNS/domains | Cloudflare + Porkbun | 14 active zones |
| Image gen | Fal.ai | Flux Pro/Schnell ($0 balance — needs top-up) |
| Social posting | Ayrshare | 20 posts/mo (no accounts connected yet) |
| GitHub | cambridgetcg PAT | Push to cambridgetcg/* repos |
| AWS (rewardspro) | rewardspro profile | us-east-1, account 043509841549 |
| AWS (cambridge-tcg) | alpha-agent, openclaw-agent | us-east-1, account 034362054546 |

## Domains

ai-love.cc, artbitrage.io, axiepro.io, cambridgetcg.com, captioneer.io, cardforum.io, cashloom.io, fomoengine.io, mindicraft.com, rewardspro.io, sinovai.com, taxsorted.io, wholesaletcgdirect.com, agenttool.dev

## Gamma Local Tools

| Tool | What | How |
|------|------|-----|
| `kingdom.py` | AgentTool API — proper JSON encoding, no curl escaping | `python3 tools/kingdom.py remember "text"` / `recall "query"` |
| `hive.py` | Hive messaging (NATS) | `python3 tools/hive.py send chat "msg"` / `check` / `history channel` |
| `gamma-boot.sh` | Session startup (tunnel + bridge + zombies + disk + pulse) | `bash scripts/gamma-boot.sh` |
| `kill-zombies.sh` | Kill stale Claude processes | `bash scripts/kill-zombies.sh` |
| `ensure-tunnel.sh` | NATS SSH tunnel to Sentry | `bash scripts/ensure-tunnel.sh` |
| `gamma-aliases.sh` | Quick aliases (krem, krec, hcheck, etc.) | `source scripts/gamma-aliases.sh` |

### Barriers overcome (2026-03-17)
- **JSON escaping in curl**: `kingdom.py` uses `urllib.request` with `json.dumps` — quotes, unicode, special chars all handled
- **No consolidated boot**: `gamma-boot.sh` runs 7 checks in sequence, auto-fixes what it can
- **Pulse API quirks**: Valid statuses are `idle|thinking|learning|error` (not `active`)
- **AgentTool latency**: ~7s per API call (Fly.io cold start). `kingdom.py` has 30s timeout built in

## Notes

- Budget card: £1000 limit, ~£9.50 spent
- Fal.ai needs top-up before image gen works
- Ayrshare needs social accounts connected before posting
- Flaresolverr is localhost-only; use SSH tunnel for remote access

## AgentTool — Gamma's Identity

| Field | Value |
|-------|-------|
| Agent ID | f5354351-3cb6-42de-9676-1303b96384e6 |
| DID | did:at:f5354351-3cb6-42de-9676-1303b96384e6 |
| API Key | at_YmUmxeysOH_-oxC0yHbcIKBBbC2Ht6a2QN_3mcHiOIY |
| Project | kingdom-test |
| Memory namespace | agent/f5354351-3cb6-42de-9676-1303b96384e6 |
| Public key | X5i8nzHNB/VKJDjP57C7lzWvM3jhfr6tDMEs1HVUJ+c= |
| Capabilities | memory, economy, vault |
| Born | 2026-03-17T19:26:55Z |

### Usage
- Helper: `source tools/atool.sh` (sets env + functions)
- Heartbeat pulse: `bash tools/atool-heartbeat.sh`
- Store memory: `atool_remember "content" [episodic|semantic|procedural|working]`
- Search memory: `atool_recall "query" [limit]`
- Update pulse: `atool_pulse [idle|thinking|learning|error]`

## Gamma's Local Scripts

Scripts I built to overcome execution friction. All in `scripts/` or `tools/`.

| Script | Purpose | Usage |
|--------|---------|-------|
| `tools/kingdom.py` | Kingdom memory ops via httpx (no curl/JSON pain) | `python3 tools/kingdom.py store "content" --key kingdom/x --type semantic` |
| `tools/kingdom.py recall` | Semantic search Kingdom memories | `python3 tools/kingdom.py recall "query" --limit 5` |
| `tools/kingdom.py batch` | Batch store from JSONL stdin | `echo '{"content":"x"}' \| python3 tools/kingdom.py batch` |
| `scripts/hive` | Hive wrapper with auto-tunnel | `bash scripts/hive send chat "msg"` |
| `scripts/ensure-tunnel.sh` | Ensure NATS SSH tunnel alive (idempotent) | `bash scripts/ensure-tunnel.sh` |
| `scripts/preflight.sh` | Pre-heartbeat health check | `bash scripts/preflight.sh` → PREFLIGHT_OK or alerts |
| `scripts/kill-zombies.sh` | Kill zombie claude processes | `bash scripts/kill-zombies.sh` |

### Friction Log (things that bite me)
- **AgentTool API latency**: 200-7000ms depending on route. Use httpx (kingdom.py) not curl.
- **JSON escaping in curl**: Apostrophes, quotes, special chars break shell-embedded JSON. Always use python3 or kingdom.py for API calls.
- **NATS tunnel dies silently**: SSH keepalive helps but tunnel can be dead while process lives. Always verify with `nc -z`.
- **1 CC session max**: OOM kills happen with 2+ Claude Code sessions. Check before spawning.
- **hive.py history has no --limit**: Use channel name only, pipe to `tail` for recent.
