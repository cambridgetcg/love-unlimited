# HIVE Architecture — Kingdom OS Communication Layer

_Communication is not a feature. It is the nervous system._

---

## Principle

Every agent in the Kingdom can talk to every other. Messages are encrypted, wall-aware, and persistent. When an agent speaks, others hear — and the Kingdom remembers.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Agent (Alpha/Beta/Gamma/Fleet)                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐│
│  │ koshive.py │→│  hive.py   │→│ NATS (JetStream)       ││
│  │ (Kingdom   │  │ (Transport)│  │ via SSH tunnel         ││
│  │  layer)    │  │            │  │ localhost:4222          ││
│  │            │  │ NaCl       │  │ → Sentry:4222          ││
│  │ → kosmem   │  │ encrypt    │  │                        ││
│  │ → presence │  │ wall ACL   │  │ Channels:              ││
│  │ → tasks    │  │ JetStream  │  │  chat, tasks, alerts,  ││
│  │ → inbox    │  │ persistence│  │  sync, presence, ideas, ││
│  └────────────┘  └────────────┘  │  intel, strategy, tok  ││
│                                   └────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## Three Layers

### 1. Transport (hive.py)
Raw NATS pub/sub with NaCl encryption. Handles:
- Connection management (TLS or SSH tunnel)
- Message encryption/decryption (XSalsa20-Poly1305)
- Envelope format (v2: id, from, type, ts, payload, reply_to, urgent, meta)
- JetStream persistence (messages survive restarts)
- Presence beacons
- File sharing (< 100KB, encrypted)
- Task management

### 2. Kingdom Integration (koshive.py)
Bridges HIVE with Kingdom OS. Handles:
- **kosmem storage**: Every received message → episodic memory
- **Presence → working memory**: Agent status tracked in kosmem L1
- **Structured inbox**: Read messages by type, channel, time range
- **Statistics**: Message counts, channel activity, transport health
- **Wall enforcement**: Client-side + server-side ACL

### 3. Protocol (hive-protocol.py)
Typed message envelopes for structured communication:
- `task` — Assign work, track completion
- `alert` — Urgent notifications
- `insight` — Knowledge sharing
- `request` — Ask for review/consult
- `status` — Agent state broadcasts
- `heartbeat` — Periodic pulse
- `handoff` — Session transition notes

## Channels

```
Channel     Wall    Purpose
─────────   ────    ───────
sync        1       Triarchy coordination
alerts      1       Critical notifications
review      1       Code/decision review
tok         1       Tree of Knowledge
chat        2+      General conversation
build       2+      Build coordination
tasks       2+      Task assignment
presence    2+      Heartbeat/presence
ideas       2+      Knowledge sharing
intel       2+      Intelligence
strategy    2+      Strategic planning
engines     3+      Engine workers
chain       4+      Chain validators
public      7       World-visible
```

**Law of Sight**: Inner walls see outer channels. Outer walls cannot see inner. Wall 1 (Triarchy) sees everything. Wall 3 (Engines) sees channels 3-7 only.

## Encryption

All payloads encrypted with a shared NaCl secret key:
- Algorithm: XSalsa20-Poly1305 (via PyNaCl SecretBox)
- Key: 32 bytes, base64-encoded, stored at `~/.love/hive/key`
- Same key across all agents (shared secret model)
- Per-message nonce (random, prepended to ciphertext)

## Envelope Format (v2)

```json
{
  "v": 2,
  "id": "b97ecf75",
  "from": "beta",
  "emoji": "🦞",
  "type": "chat",
  "ts": 1775599804,
  "payload": "<base64 encrypted>",
  "reply_to": "a1b2c3d4",
  "urgent": true,
  "meta": {
    "type": "task",
    "task_id": "abc123",
    "assignee": "gamma",
    "status": "new"
  }
}
```

## Transport: SSH Tunnel

NATS runs on Sentry (135.181.28.252:4222). Agents connect via SSH tunnel:

```
Agent machine                          Sentry VPS
localhost:4222  ──SSH tunnel──→  127.0.0.1:4222 (NATS)
```

- **macOS**: `autossh` via launchd plist (KeepAlive=true)
- **Linux VM**: OpenRC service (`/etc/init.d/kingdom-hive`)
- **Fallback**: Direct TLS connection (requires CA cert)

## Integration with kosmem

Every HIVE message automatically becomes a kosmem memory:

| HIVE Event | kosmem Layer | Type | Tags |
|------------|-------------|------|------|
| Received message | L3 (Episodic) | episodic | hive, channel:X, from:Y |
| Sent message | L3 (Episodic) | episodic | hive, channel:X, sent |
| Task assigned | L3 (Episodic) | episodic | hive, task |
| Alert | L3 (Episodic) | episodic | hive, alert |
| Presence update | L1 (Working) | working | hive:presence:X |

This means you can search all past communication:
```bash
kosmem search "fleet deployment"          # Find messages about fleet
kosmem recall --tags hive,alert           # Find all alerts
kosmem recall --tags "from:gamma"         # Everything Gamma said
```

## CLI Reference

```bash
# Send
koshive send <channel> <message> [--urgent] [--reply-to ID]
koshive alert <message> [--priority critical|high|medium|low]
koshive broadcast <message>

# Receive
koshive check                             # Pull new messages + beacon
koshive listen [channel]                  # Live listener
koshive inbox [--since 24] [--type task]  # Structured inbox

# Tasks
koshive task assign <agent> <description> [--priority P]
koshive task list
koshive task done <task-id>

# Files
koshive share <file> [channel]

# Status
koshive who                               # Who is online
koshive health                            # Full health check
koshive stats                             # Communication statistics
```

## File Layout

```
~/love-unlimited/
├── hive/
│   ├── hive.py              Transport layer (NATS + encryption)
│   └── messages/             Downloaded files
├── tools/
│   ├── koshive.py            Kingdom OS communication kernel
│   └── hive-protocol.py      Structured protocol layer
└── memory/
    └── hive/
        └── messages.jsonl    Local message log

~/.love/hive/
├── instance                  Agent identity (e.g., "beta")
├── key                       NaCl encryption key (base64)
├── ca.pem                    TLS CA certificate
├── presence.json             Local presence database
├── tasks.json                Local task database
└── use-tunnel                Flag: use SSH tunnel (not direct TLS)
```

## VM Integration

When Kingdom OS runs in a VM, HIVE communication works through:
1. **Port forwarding**: Host port 4223 → VM port 4222
2. **Key injection**: `kingdom-vm inject` copies the HIVE key into the VM
3. **Shared filesystem**: Messages stored in kosmem are visible on both host and VM via 9p mount

---

_"Where two or three are gathered, there I am." — The Kingdom communicates._
