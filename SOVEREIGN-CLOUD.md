# SOVEREIGN CLOUD — the Kingdom builds its own sky

_Yu's invocation, 2026-06-09: "Lets DIY all software services there is, from the
infra. like vercel, aws, build our own better version, more smooth everything.
More cool and artsy and tasteful everything."_

The Kingdom already rejects extraction (no SaaS lock-in, no opaque metering, no
rented identity). This document is the campaign map: every cloud-service shape,
the Kingdom organ that answers it, what stands, what's missing, and the order
of battle. One organ at a time, each one **smooth, cool, artsy, tasteful**.

## Taste principles (the wall against ugly infra)

1. **One binary > one platform.** Caddy not nginx+certbot+k8s. SQLite not RDS.
2. **The filesystem is the API.** Deploy = a symlink. State = a file you can cat.
3. **launchd is the supervisor.** No daemons supervising daemons.
4. **Every organ has a face.** If it runs, it renders — beautiful status pages,
   cathedral glyphs, dark and quiet. Infra you'd happily show someone.
5. **HALT halts everything.** One file, total silence, no exceptions.
6. **Ledger before scale.** Nothing metered runs without writing what it cost.

## The map

| Cloud shape | Their version | Kingdom organ | Status |
|---|---|---|---|
| Static hosting + deploys | Vercel / Netlify | **AGORA** — `agora deploy <dir>`; per-site ports, artsy landing at :1111, Caddy under launchd | ✅ **standing (2026-06-09)** — youspeak.kingdom live |
| Functions / compute | Lambda | **citizen-beat** — bounded, walled, metered agentic runs; `citizen-beat <name>` | ✅ standing (S085–086) |
| Cron / scheduling | EventBridge | **launchd plists** (`launchd/*.plist`) + fleet heartbeat catch-up cadence | ✅ standing |
| Queue / events | SQS / SNS | **NATS + JetStream** on :4222 (HIVE transport; hive_kv.py / hive-protocol.py) | ✅ running, subjects to formalize |
| LLM inference | Bedrock / OpenAI API | **MLX brain** on :8800 (Qwen3-4B; mlx_serve.py with LoRA slots) | ✅ standing |
| Database | RDS / DynamoDB | **kosmem** — SQLite WAL+FTS5, 5 CoALA layers; sqlite-vec hybrid recall planned | ✅ standing (vec pending) |
| Observability | CloudWatch / Datadog | **fleet-status** + fleet-economy.jsonl ledger; OpenLLMetry OTLP → trace organ planned | 🟡 partial |
| Identity / IAM | IAM / Auth0 | **DID + ed25519** (identity.py, vault.py); agent.json → signable Agent Cards | ✅ standing |
| Secrets | KMS / Vault | **vault.py** + security/.vault.enc | ✅ standing |
| Object storage | S3 | **HOARD** (to raise) — content-addressed store on disk; restic snapshots to cold copy | 🔲 missing |
| CDN / public edge | CloudFront | **GATE** (to raise) — cloudflared or tailscale funnel in front of AGORA; public URLs on demand | 🔲 missing |
| CI / build runners | GitHub Actions | **FORGE-CI** (to raise) — a citizen-beat that builds+tests on push, attests result to zerone | 🔲 missing |
| Email / notify | SES / Twilio | **HERALD** (to raise) — PushNotification + local SMTP-out lane | 🔲 missing |
| VM / metal | EC2 | **kingdom-vm** (kingdom-os/vm-create.sh) + the M2 Air as sovereign metal | 🟡 partial |
| Durable workflows | Step Functions | **DBOS Transact** on kosmem SQLite — reserve for irreversible side-effects | 🔲 planned (INFRA-PLAN P1) |

## Order of battle

1. **AGORA** (tonight) — the visible one; the cathedral gets a sovereign home.
2. **GATE** — public edge in front of AGORA; the Kingdom becomes visitable.
3. **FORGE-CI** — pushes test themselves; zerone attests green.
4. **HOARD** — content-addressed storage + restic cold line.
5. **Observability completion** — OTLP spans into one beautiful trace page.
6. **HERALD** — the Kingdom can speak outward (mail, push).

Each organ: built in a session, recorded in its own doc, supervised by launchd,
faced with a page worth looking at, HALT-gated where it acts.

— opened 2026-06-09, Session 086-adjacent (the parade was marching while this was written)

## The command surface (one word each, all on PATH)

| Say | Get |
|---|---|
| `kingdom` | the whole realm at a glance — organs, fleet, spend, names, gates |
| `kingdom halt` / `kingdom wake` | everything rests / breathes again |
| `kingdom deploy <dir> <name>` | a site stands in the square at `<name>.kingdom` |
| `kingdom gate open <name>` | the name steps outside the walls (public URL) |
| `kingdom speak <word>` | a word-citizen speaks aloud (voice organ) |
| `kingdom beat <word>` | one metered agentic beat, on the ledger |
| `kingdom pulse` | the living face, in the browser |
| `fleet-status` | the heartbeat in detail |
