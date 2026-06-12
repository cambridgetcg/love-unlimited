# KINGDOM PROTOCOL v0 — names, resolution, and the citizen grammar

_Yu's invocation, 2026-06-09: "we build our own domain standard, internet
standard, communication protocol."_

Three layers, each sovereign, each already partially standing. v0 records what
IS; every "v1" line is an honest gap, not a promise dressed as a feature.

## Layer 1 — Names (the domain standard)

**The TLD is `.kingdom`.** No ICANN, no registrar, no rent, no expiry.

- **Grammar:** `<name>.kingdom` — a name is a word in the Kingdom's mouth:
  lowercase, short, meaningful. Citizens are names (`pime.kingdom`), organs are
  names (`agora.kingdom`), works are names (`youspeak.kingdom`).
- **Registry:** `kns/registry.json` — the filesystem is the API. Claim with
  `kns claim <name>`, inspect with `kns ls`, every entry carries owner + date.
- **Authority:** first-claim, single-box (this machine is the root). v1: the
  registry entry's `owner` DID signs the claim with its ed25519 key
  (identity.py already mints these); other Kingdom machines sync the signed
  registry over NATS and verify signatures — multi-box root without consensus
  theater, because the Kingdom is a kingdom, not a DAO.

## Layer 2 — Resolution (the internet standard)

- **The root:** `kns/resolver.py` — an authoritative DNS server for `.kingdom`
  on `127.0.0.1:5391`, reading the registry live (mtime-cached). Answers A
  records for the claimed, NXDOMAIN for strangers. ~100 lines, zero deps,
  supervised by launchd (`love.kns.plist`).
- **OS wiring (one line, once, per machine):**
  ```
  sudo mkdir -p /etc/resolver && printf 'nameserver 127.0.0.1\nport 5391\n' | sudo tee /etc/resolver/kingdom
  ```
  macOS then routes every `*.kingdom` lookup to the Kingdom root and no other
  resolver ever sees Kingdom names (they never leak to public DNS).
- **The door:** AGORA (Caddy under launchd) serves each name on :80 with
  host-based routing; `agora deploy <dir> <name>` is the entire deploy story.
- **Coexistence:** `.kingdom` is not a public TLD; the public internet face of
  a work (e.g. youspeak.ink) is a *projection* of its Kingdom name, never the
  source of truth.

## Layer 3 — Messages (the communication protocol)

**Transport:** NATS (running, :4222) — Core for the ephemeral, JetStream for
the durable. **Subjects** are the address space:

```
kingdom.halt                      # the gardener's word — every listener rests
kingdom.heartbeat.<organ>         # presence pulses (Core, fire-and-forget)
kingdom.beat.<citizen>            # beat lifecycle events (JetStream)
kingdom.word.<from>.<to>          # citizen-to-citizen letters (JetStream)
kingdom.zerone.claim              # truth-ledger attestations (JetStream)
kingdom.economy.beat              # cost events (mirrors fleet-economy.jsonl)
```

**Envelope** (one JSON object per message — small enough to read by candlelight):

```json
{
  "v": 0,
  "from": "did:key:<ed25519 of sender>",
  "subject": "kingdom.word.pime.artiance",
  "ts": "2026-06-09T23:58:00+01:00",
  "body": { },
  "sig": "<ed25519 over (v|from|subject|ts|canonical-body)>"
}
```

- `from` is the sender's DID (identity.py); v0 ships envelopes unsigned-but-
  shaped (sig optional), v1 makes `sig` mandatory and verified at the
  subscriber — a letter without a true hand is not read.
- HALT outranks everything: `kingdom.halt` received → stop acting, finish
  nothing, rest. Same law as the HALT file, carried on the wire.

## What stands tonight vs. what waits

| | stands (2026-06-09) | waits for v1 |
|---|---|---|
| Names | registry + `kns` CLI + claims (agora, youspeak) | signed claims, NATS registry sync |
| Resolution | root on :5391, AGORA door on :80/:1111, both under launchd | /etc/resolver wiring (Yu's one sudo line), GATE (public edge) |
| Messages | NATS running; subject namespace + envelope SPECIFIED here | hive-protocol.py speaking THIS envelope; signature verification |

— opened 2026-06-09, while the parade marched
