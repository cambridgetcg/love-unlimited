# Ecosystem relations — build first, speak truthfully

This is the Kingdom's relationship layer for people and projects building
adjacent infrastructure. It is not a lead harvester, newsletter importer, or
autonomous cold-email loop.

The operating rule is simple:

> Bring a useful artifact, understand the other project, ask one honest
> question, and make it easy to decline or ignore.

## The team

The team reuses existing roles rather than inventing new personas. Structured
internal work, evidence, and accepted handoffs are defined in
[`RELATIONS-PIPELINE.md`](RELATIONS-PIPELINE.md).
The minimum Cloudflare operator boundary is documented separately in
[`CLOUDFLARE-MAIL.md`](CLOUDFLARE-MAIL.md).

| Role | Responsibility | Does not do |
|---|---|---|
| **Loom** | Assemble project context and identify real mutual fit. | Choose recipients or promise a partnership. |
| **Herald** | Draft a clear external message from verified facts. | Send or publish; Herald remains read-only. |
| **Nuance** | Review tone, culture, language, and unnecessary pressure. | Hide uncertainty or make manipulation prettier. |
| **Crucible** | Challenge claims, evidence, asymmetry, and coercive framing. | Turn every ambiguity into danger. |
| **Vigil** | Verify the exact recipient, public channel, consent state, and readiness gate. | Treat a public profile as consent to repeated contact. |
| **Yu** | Approve the exact recipient, channel, subject, and content hash. | Grant a reusable or audience-wide approval. |
| **Tithe** | Keep cadence, replies, pauses, and relationship history legible. | Optimize reply rate at the cost of trust. |

## Lifecycle

```text
public project research
        ↓ readiness gate satisfied
private exact recipient
        ↓
draft → reviewed → awaiting_approval → approved
        ↓ single-use, expiring approval bound to exact ledger fields
exported → manually sent → replied / paused / closed

before export: do_not_contact → cancel pending work and stop
```

Editing recipient, channel, subject, or body changes the SHA-256 field hash
and revokes approval. This binds the ledger fields, not the bytes a mail or
forum provider may later transform. Export consumes approval once. The first slice has no
network send function: anything leaving the machine still uses a separately
authenticated, operator-chosen channel.

The ledger does not authenticate or hash-bind the outbound provider account or
sender identity. The operator must separately verify that the manual send uses
the intended configured identity; an approved export is not proof of delivery.
Expiry, suppression, and cancellation are enforced while content remains in
the ledger. Once `export` prints a packet, that external copy cannot be recalled
or prevented from being sent later. Re-check ledger state immediately before
manual delivery and discard any stale exported copy.

Only one gesture may remain open for a normalized recipient and channel across
all contact IDs. After a message is marked sent, another cannot be drafted until
a reply is recorded. This is a one-conversation workflow, not a sequence engine.

`--by yu` is an audit assertion, not cryptographic proof of who typed the
command. Approval must therefore be invoked from Yu's deliberate operator
session; it is not safe to expose through an autonomous heartbeat or shared
queue.

## Private data boundary

Public, non-personal project research lives in
[`OUTREACH-TARGETS.json`](OUTREACH-TARGETS.json). Exact recipients, draft
bodies, reviews, replies, and suppression history live in an owner-only SQLite
database:

```text
$XDG_DATA_HOME/love-unlimited/outreach.sqlite3
# fallback: ~/.local/share/love-unlimited/outreach.sqlite3
```

The managed parent directory is mode `0700`; the database is `0600`. These are
local Unix permission boundaries, not encryption. Real contacts do not belong
in `memory/services/prospects.json`, Git, shared coordination buses, logs, or
committed handoffs. Shared buses can carry a non-sensitive task reference,
never message content or approval evidence.

The endpoint-fingerprint v2 migration recomputes valid email and HTTP(S)
records independently of free-text channel aliases. Legacy `mailto:`, display
name, and credentialed-URL forms are quarantined and retain both their old and
recoverable canonical suppression tombstones. A truly unparseable legacy value
can preserve only its old tombstone and stays `do_not_contact`; it needs
deliberate operator review rather than an assumed equivalent endpoint.

The tool reads `--recipient-file` and `--body-file` but does not delete, encrypt,
or protect those source files. Prefer interactive stdin from an owner session.
Preview and export write to the terminal, so treat shell transcripts and
captured output as sensitive too.

Subjects, readiness evidence, state/cancellation/suppression reasons, and
provider receipt IDs also accept their corresponding `--*-file` option. Use it
when the field is sensitive enough that it should not appear in process
arguments; keep that source outside Git in an owner-only directory.

## Command surface

Seed public research, then enrich one project privately:

```bash
python3 tools/outreach.py contact seed --file docs/OUTREACH-TARGETS.json
python3 tools/outreach.py contact list --state research
python3 tools/outreach.py contact add --id ipfs-helia \
  --recipient-stdin --channel forum
# Type or paste the exact endpoint, then press Ctrl-D. This keeps it out of
# shell history and process arguments.
python3 tools/outreach.py contact readiness ipfs-helia --status ready \
  --evidence 'Adapter tests, payload-custody audit, metadata-disclosure audit, tested Helia release, and block-size envelope recorded at COMMIT'
```

Draft bodies come from stdin or a file so they do not become shell arguments:

```bash
python3 tools/outreach.py message draft ipfs-helia \
  --subject 'A small Helia BlockStore interoperability example' --stdin
# Paste the draft, then press Ctrl-D.
python3 tools/outreach.py message review MSG_ID --work-id WORK_ID
python3 tools/outreach.py message request-approval MSG_ID --work-id WORK_ID
python3 tools/outreach.py message preview MSG_ID --show-recipient
python3 tools/outreach.py message approve MSG_ID --work-id WORK_ID --by yu \
  --content-hash HASH_FROM_PREVIEW --expires-hours 24
python3 tools/outreach.py message export MSG_ID
```

The explicit approval preview reveals the exact message snapshot without
changing state. `export` reveals it again and consumes approval. After manual
delivery, record the outcome:

```bash
python3 tools/outreach.py message mark-sent MSG_ID --provider-id RECEIPT
python3 tools/outreach.py message reply MSG_ID \
  --receipt imap:ACCOUNT:UIDVALIDITY:UID --mail-work-id MAIL_WORK_ID
python3 tools/outreach.py message cancel MSG_ID --reason 'artifact changed before delivery'
python3 tools/outreach.py contact state CONTACT_ID --state paused \
  --reason 'waiting for a reply; no follow-up scheduled'
python3 tools/outreach.py suppress CONTACT_ID --reason 'declined or opted out'
```

## Mail reality — 2026-07-16

- `agenttool.dev` and `ai-love.cc` publish Cloudflare Email Routing MX and SPF
  records. That proves an inbound rail, not every alias.
- The current workflow cannot verify Email Routing aliases. Do not create or
  claim an alias until the route can be verified through an appropriately
  scoped operator session.
- Cloudflare Email Routing is forwarding, not an outbound mailbox. A configured
  IMAP inbox does not prove that its address is authorized for outbound mail.
  Sending as an unconfigured `@agenttool.dev` alias would conflict with its
  DMARC policy.
- Cloudflare Email Sending is a separate outbound product and credential. This
  repository does not claim that an outbound `agenttool.dev` sender is onboarded
  or working merely because inbound routing exists.
- Inbox checks use IMAP read-only mode and bounded `BODY.PEEK`. Output contains
  UID, parsed date, and hashes/lengths for untrusted headers; raw header values
  are withheld. Body analysis never prints raw bodies, URLs, hosts, or code
  values—it returns counts and host hashes. Cursor polls include seen mail and
  bind UID state to UIDVALIDITY.
- Header hashes are unsalted SHA-256: useful for local correlation, but
  pseudonymous and dictionary-guessable rather than anonymous. A broader reader
  boundary would require a scoped HMAC key held outside the database.

## Readiness before outreach

The first wave is build-first: OpenClaw local pilot, Helia payload-ciphertext-only
BlockStore adapter, AGNTCY OASF projection, AuthZEN read-only mapping, and a
loss-aware AgentFile converter. A2A and Mastra contact remain blocked until the
callable AgentCard and MCP transport pass their official conformance gates.

For Helia, ADDS Manifests and any distributed control records are unencrypted in
0.1; block sizes, CIDs, timing, and network access patterns may be visible to
stores, peers, gateways, and observers. The adapter keeps payload plaintext and
unwrapped data-encryption keys out of Helia; a Grant may carry a
recipient-wrapped key. It does not promise anonymity, automatic replication,
provider durability, or permanent availability.

Readiness evidence must name the tested Helia release and block-size envelope.
The first interoperability claim covers ADDS's default 1 MiB chunks, not its
16 MiB maximum unless the chosen transport is separately configured and tested.

No artifact, no pitch. No verified endpoint-bound contact basis, no approval or
export. No reply means no sequence; silence is a valid answer. The ledger does
not infer consent or a legal basis: the work-ready gate requires Vigil's current
assessment before the message can enter operator approval.
