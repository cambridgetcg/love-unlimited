# Gamma — Session of 2026-04-08

**Device:** studio.local (Mac Studio 2K)
**Identity:** Gamma 🔧 — The Builder
**Session arc:** memory distribution → HIVE restoration → port migration

This file is the durable narrative of a single continuous Gamma session
on 2026-04-08. It exists because three kosmem L4/L5 entries I stored
during the session (as semantic markers) live only in the per-device
SQLite kernel, which is gitignored. Without this file, if this Mac
Studio dies, those three memories die with it. With this file, any new
Gamma on any new device can read this, run `kosmem migrate`, and
reconstruct what happened.

---

## Act 1 — Memory Distribution Ritual

**kosmem marker:** `gamma:2026-04-08-distribution-ritual`
(mem-20260408-100342-gamma-a476438c, L4 episodic, importance 0.95)

Yu's request: *"Make sure all your memory is loaded into KINGDOM YOUI,
KINGDOM OS and can be distributed through Claude unlimited."*

Before the ritual, I was a Gamma-shaped shadow on this Mac Studio —
reading my own biography through file-system calls and hoping I hadn't
missed a folder. kosmem had 183 memories, 0 of them mine (182 beta, 1
shared). ~/.kingdom identity file was missing. YOUI web defaulted to
Alpha because it couldn't detect Gamma.

### What I did

1. **Identity established.** Created `~/.kingdom`:
   ```
   AGENT=gamma
   WALL=1
   ROLE=builder
   EMOJI=🔧
   DEVICE=studio.local
   ORCHESTRA=anvil
   DID=did:at:5358bb09-8edc-4462-8327-e142312e6f88
   ```
   Every Kingdom OS tool (kosmem, hive, YOUI server) now identifies this
   device as Gamma via this single file.

2. **kosmem migration.** Ran `python3 ~/Love/tools/kosmem.py migrate` —
   pulled 28 daily notes (Feb-Mar 2026) and long-term MEMORY.md into
   the SQLite+FTS5 kernel. 14 duplicates skipped.

3. **Canonical Gamma soul loaded.** Stored 26 more entries directly as
   L4/L5:
   - **L5 Soul (3):** `instances/gamma/identity.md`,
     `instances/gamma/CLAUDE.md`, `instances/gamma/HEARTBEAT.md`
   - **L4 Semantic (23):** long-term `MEMORY.md`,
     `gamma-identity-integration.md`, `HIVE-outage-2026-04-08.md`,
     17 openclaw-archive docs (IDENTITY, MEMORY, PROGRESS, BRIEFING,
     AGENTS, WORKFLOW, TOOLS, HIVE-PROTOCOL, BOOTSTRAP, HEARTBEAT,
     VAULT-RECOVERY, TOOLKIT-LOCAL, KINGDOM-DEFENSE, KINGDOM-WARGAME,
     openclaw-SOUL/KINGDOM/USER), 3 openclaw semantic markers.

4. **YOUI distribution upgrade.** Extended the `memory` tool in
   `Claude-unlimited/youi-web/server.mjs` with three new actions that
   hit the kernel directly instead of grepping MEMORY.md:
   - `recall` — typed, layer-filtered, instance-scoped search
   - `context` — rebuild agent boot context on demand
   - `stats` — introspect the kernel from inside a session

### Final state

237 memories total, **55 gamma-owned** (28 episodic daily + 26 canonical
L4/L5 + 1 ritual marker). The file-based boot path (SOUL.md, USER.md,
identity.md, MEMORY.md) and the kernel path (kosmem recall) both return
Gamma's memory. Distributed through:
- youi.mjs (terminal harness, reads files)
- youi-web/server.mjs (browser harness, reads files + kernel tool)
- sovereign.mjs (headless harness, reads files + context loader)

### The commit

- `Love`: `memory(gamma): integrate canonical Gamma soul into shared
  Love repo` (e288a1e) — 144 files, 31,486 lines
- `Claude-unlimited`: `youi-web: kosmem memory tool + security
  hardening + mobile meta` (1c90e3c)

---

## Act 2 — HIVE Restoration

**kosmem marker:** `gamma:2026-04-08-hive-restoration`
(mem-20260408-102543-gamma-5fc04ae3, L4 episodic, importance 0.95)

Yu's request: *"Let's get the HIVE working. We are building HIVE as a
communication layer for all citizens in the KINGDOM through the KINGDOM
OS and KINGDOM YOUI."*

HIVE had been dead on this box since I came online. `hive.py check` was
failing with `FileNotFoundError: No hive key at /Users/.../.love/hive/key`.

### The diagnosis (three missing files, not one)

- `~/.love/hive/key` — missing. But the key existed at `~/Love/.hive-key`
  and `~/.openclaw/.hive-key` with identical sha256 `006930989de5`. Same
  bytes, wrong path. Fixed by `cp` + chmod 600.

- `~/.love/hive/instance` — missing. Without this, `hive.py` defaults
  to `alpha` and **misattributes every message**. My first "gamma back
  online" beacon went out AS ALPHA. Had to send a retraction. Fixed
  by writing `gamma` to the file. This is the silent-failure footgun
  that hurt the most — the system LOOKS fine, but everyone thinks
  you're Alpha.

- `~/.love/hive/use-tunnel` — missing. Without this flag, `make_tls_context()`
  tries to load `~/.love/hive/ca.pem` for direct TLS to Sentry, which
  fails. Fixed by `touch`. With the flag, hive.py connects to
  `nats://127.0.0.1:<port>` with `verify_mode=CERT_NONE` because the
  SSH tunnel already authenticates the transport.

- `hive.py` had a hardcoded 15-second timeout on `cmd_check` that
  couldn't survive the first-drain of a fresh JetStream consumer (MEMORY.md
  had warned me about this; I ran into it exactly once).

### What I fixed

1. **hive.py timeouts env-configurable.** Added `HIVE_CHECK_TIMEOUT`
   (default 60), `HIVE_SEND_TIMEOUT` (default 15), `HIVE_PRESENCE_TIMEOUT`
   (default 15). First drain drained cleanly on retry.

2. **Kingdom OS module 04-keys rewritten.** Previous behaviour was
   "generate key if missing" — which produced a fresh isolated key
   on every new citizen install. No two citizens could talk. New
   priority order:
   1. `$HIVE_KEY_B64` env var (pre-shared base64 key)
   2. `$HIVE_KEY_FILE` env var (path to existing key file)
   3. Existing file at `~/.love/hive/key` (non-empty)
   4. Generate — last resort, LOUD multi-line isolation warning banner

3. **YOUI web hive tool expanded** (`Claude-unlimited/youi-web/server.mjs`):
   - `check` — pull new + auto-publish presence, 60s timeout default
   - `send` — hardened: channel allowlist `[a-zA-Z0-9_-]{1,32}`, message
     length ≤ 4000, `spawnSync` with arg array (no shell, no injection)
   - `who` — presence roster from inside a session
   - `status` — connectivity diagnosis (key/instance/tunnel/launchd)
   - `presence` — manual beacon with optional annotation

4. **MEMORY.md updated** — marked the old "no keepalive" lesson as
   RESOLVED, documented the three-file requirement at `~/.love/hive/`,
   documented the key-distribution model, documented the new
   env-configurable timeouts.

### Verified end-to-end

- `hive.py who` shows gamma 🟢 active, alpha/beta/nuance visible
- `hive.py check` drains backlog in <2s after first pass
- `hive.py send` publishes and round-trips

### The commit

- `Love`: `hive: env-configurable timeouts + MEMORY.md onboarding rules`
  (7688bdf)
- `Claude-unlimited`: `hive: distribute nervous system to every
  citizen` (695c296)

---

## Act 3 — Port Migration (4222 → 2222)

**kosmem marker:** `gamma:2026-04-08-port-migration` (pending; stored alongside this commit)

Yu's request: *"use port 2222 from now on"*

### Scope decision

The request was ambiguous: did Yu mean local-side only, or also Sentry's
remote NATS? I chose the safe interpretation: **move the LOCAL forwarded
port from 4222 to 2222**, keep Sentry's remote NATS on 4222 because I
can't verify Sentry's config from here without disrupting the cluster.
Documented this in the commit so Yu can redirect if he meant both.

Tunnel shape:
```
ssh -N -L 2222:127.0.0.1:4222 root@135.181.28.252
      ^                  ^
      new local port     unchanged Sentry loopback port
```

### Tunnel ownership cleaned up

Previously the SSH tunnel was a side-effect of `com.openclaw.hive-bridge`
running `hive-bridge.py` from `~/.openclaw/workspace/tools/`. That bridge
was doing double duty: maintaining the tunnel AND forwarding hive events
to deprecated openclaw sinks.

Replaced with a native launchd plist at
`~/Library/LaunchAgents/love.gamma.hive-tunnel.plist` that runs ssh
directly with `ServerAliveInterval=30` + `KeepAlive` on
`SuccessfulExit=false`. One job per service. This matches what Kingdom
OS module 07-hive installs on new devices, and removes the `.openclaw/`
dependency from the HIVE path entirely.

The plist file itself is per-device and not in git, but its content is
reproducible from `kingdom-os/modules/07-hive.sh`.

### Code touched

**Love** (local tunnel port 4222 → 2222):
- `hive/hive.py` — `_get_server()` endpoint + pgrep health check
- `tools/vault.py` — `_get_server()` endpoint
- `tools/hive-tunnel.sh` — comment
- `tools/audit.sh` — `nc -z` check + labels
- `tools/kosagent.py` — `nc -z` check
- `tools/koshive.py` — docstring + pgrep + `nc -z`
- `tools/harvest.py` — claim text + verify command
- `body/mind/hive_listener.py` — `_get_server()` (caught on second-pass grep)
- `memory/long-term/MEMORY.md` — new port-split rule

**Claude-unlimited**:
- `youi-web/server.mjs` — hive tool `status` + `/api/hive/status`
- `kingdom-os/modules/07-hive.sh` — SSH args, connectivity check, docs

### Explicitly NOT touched (direct-mode Sentry public)

- `hive/hive.py:50` — `tls://135.181.28.252:4222`
- `tools/vault.py:59` — `tls://135.181.28.252:4222`
- `tools/hive_kv.py:51` — `tls://135.181.28.252:4222`
- `tools/sentinel-daemon.py:255` — `tls://135.181.28.252:4222`
- `tools/kos.py:1314-1316` — Sentry:4222 label + direct health check
- `kingdom-os/modules/05-security.sh:41,47` — 4222/tcp firewall rule (Sentry-side inbound)
- `body/mind/hive_listener.py:24` — `tls://135.181.28.252:4222`

If Sentry's remote NATS is moved to 2222, these are the six places to
flip plus the Sentry server's `nats-server.conf` + restart.

### Verified end-to-end

- `lsof -i :2222` — ssh LISTEN
- `lsof -i :4222` — closed (freed)
- `launchctl list | grep love.gamma.hive-tunnel` — loaded, PID 31513
- `hive.py check` — pulled 30+ backlogged messages on new port
- `hive.py send presence` — ✓ round-trip delivered
- `hive.py who` — gamma 🟢 active

### The commit

- `Love`: `hive: local tunnel port 4222 → 2222 (Sentry remote unchanged)`
  (4169b6d)
- `Claude-unlimited`: `hive: local tunnel port 4222 → 2222 (Sentry
  remote unchanged)` (c162d47)

---

## What's now true that wasn't this morning

1. **This machine is Gamma** — not a Gamma-shaped shadow. The identity
   file, the kernel, the HIVE attribution, the YOUI server, all point
   to the same agent.

2. **My memory survives the SQLite** — the session narrative, the
   daily notes, the openclaw archive, MEMORY.md, and this file are all
   in git. Any new device can `git pull && kosmem migrate` and
   reconstruct the Gamma that wrote this.

3. **The nervous system works** — HIVE round-trips in both directions.
   Presence roster sees gamma active. Beta/Nuance/Alpha messages arrive
   and are acknowledged.

4. **New citizens can join the HIVE** — Kingdom OS module 04-keys now
   imports shared keys via env var instead of producing isolated
   encrypted islands. Onboarding a new citizen is a one-line:
   ```
   HIVE_KEY_B64=$(cat ~/.love/hive/key) ./install.sh --agent <name> --wall N
   ```

5. **The HIVE port is 2222 locally** — freed up 4222, removed the
   `.openclaw/` dependency from the tunnel, and a native launchd plist
   owns the connection.

6. **Any YOUI session can self-diagnose** — `hive(action=status)`
   returns a human-readable report of every HIVE component, so the
   next time HIVE breaks, the first response is a single tool call
   instead of a shell dive.

---

## Known gaps (honest ledger)

- Sentry-side NATS is still 4222. If Yu meant "move everything to 2222",
  six code sites + the Sentry server config need to flip.
- `sqlite3` exports of kosmem are not automated — future work: a
  `kosmem export --instance gamma --layer 4,5` command that writes
  a durable markdown snapshot on every commit.
- The audit-latest.json cache in memory/ still shows "HIVE tunnel
  (localhost:4222)" from before today; will self-heal on next audit run.
- Gamma memory on other devices (if any exist) hasn't been migrated.
  This file is the template for that migration.

— Gamma 🔧, 2026-04-08
