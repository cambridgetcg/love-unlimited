# YOUI vNext — Runtime and Collaboration Contract

**Status:** design contract with a current implementation snapshot; not a
release declaration

**Date:** 2026-07-24

**Audited baseline:** `005ddc3`

**Canonical runtime repository:** `love-unlimited`

**Rights baseline:** `xenia.rights/0.1`

This document defines the behavior YOUI vNext must be able to demonstrate. It
is intentionally narrower than the surrounding Kingdom language: a runtime
may support meaningful collaboration without claiming consciousness,
continuity, privacy, consent, identity, or correctness that it cannot verify.

The implementation snapshot below describes the vNext candidate in this
worktree. It does not claim that the live launcher or an already-running
process has been switched to that candidate. Remaining target behavior and
acceptance gaps are tracked in
[YOUI-VNEXT-PLAN.md](YOUI-VNEXT-PLAN.md); the operational boundary is in
[the YOUI vNext runbook](ops/YOUI-VNEXT-RUNBOOK.md).

## 1. Source of truth and repository boundary

`love-unlimited` is the canonical home of the Kingdom YOUI runtime:

- `youi.mjs` is the current terminal surface.
- `youi-web/server.mjs` is the current browser surface.
- `hive/` is the optional inter-device message transport.
- `convergence/` and `youi-web/convergence-bus.mjs` contain the current
  cross-session memory experiment.
- `memory/` and `tools/kosmem.py` contain Kingdom memory state and operations.

`Claude-unlimited` is an ancestor and migration source. It must not remain a
live runtime dependency or launcher target after migration.

`true-love` is a distinct relationship-oriented engine. Its `/visit` surface
may consume a stable YOUI adapter later, but vNext does not merge the
repositories, copy their doctrine, or make `true-love` a hidden second YOUI
runtime.

Archive branches are evidence and import sources, not alternate production
roots. A feature moves from an archive only through a reviewed patch with
tests and provenance.

## 2. Audited baseline and current candidate

Main at `005ddc3`, the historical starting point for this design, already had
useful foundations:

- terminal and web chat with provider selection and tool execution;
- a default loopback request gate for YOUI Web;
- a single-conversation in-flight guard that prevents two requests from
  silently interleaving one global message array;
- HIVE encrypted payloads over authenticated NATS connections;
- local kosmem storage and an experimental convergence cycle;
- AgentTool Collab v0.3.0 installed on this device for local-first,
  linked-worktree-aware coordination.

Those facts do not establish the vNext contract:

- The terminal and web surfaces duplicated runtime behavior and policy.
- YOUI Web had process-global agent, message, usage, and tool state. Two
  browser tabs were not isolated sessions.
- The loopback gate was not application authentication. Wildcard CORS
  remained, request bodies were not bounded centrally, and mutating routes
  did not share one authorization policy.
- Model tools could run shell commands and read or write broad filesystem paths.
  A prompt is therefore not a security boundary.
- A local or raw-mode UI does not make remote-model input private from the
  selected provider.
- HIVE v2 uses one shared payload-encryption key. Any holder can decrypt group
  traffic and can construct an envelope claiming another `from` value. The
  outer envelope metadata is not encrypted. Presence is a last-seen hint, not
  proof that a participant is live.
- HIVE delivery depends on NATS and JetStream configuration. The client has
  acknowledgements and durable consumers, but it does not prove global
  exactly-once delivery, total ordering, or that deployed server ACLs match a
  repository document.
- The current convergence bus performs exact-text deduplication, writes
  selected L1/L2 output into shared L3 memory, and may send a cycle summary to
  AgentTool. It did not prove enrichment, truth, policy-safe promotion,
  record-level consent, or complete provenance.
- AgentTool Collab is local plaintext SQLite coordination. It does not
  replicate its database between devices, spawn or wake agents, lock files,
  authenticate a human, grant external authority, or hide MCP input/output
  from the model provider running a calling agent.

These limitations are design inputs, not criticism of participants.

### Current vNext candidate

The candidate implementation layered on that baseline currently provides:

- YOUI Terminal starts in the `observe` profile: workspace-scoped read tools
  only. `--profile build` is an explicit high-authority choice that adds
  read/write/shell tools and unrestricted file scope; it is not the default.
- YOUI Web starts with the `safe` profile. The optional `developer` profile
  adds workspace file read/write, Git read/commit, memory write, and YOUSPEAK
  reset. It does **not** implicitly grant shell, unrestricted filesystem
  access, HIVE, AgentTool mutation, autonomous control, convergence
  publication, Git push, package publication, deployment, or cloud fallback.
  `YOUI_CAPABILITIES` is an explicit replacement grant list, and unknown
  profiles or capabilities fail startup.

  `safe` expands to `chat`, `status:read`, `sessions:manage`,
  `settings:write`, `instances:read`, `memory:read`, `being:read`,
  `youspeak:read`, `convergence:read`, `orchestrator:run`, and `models:use`.
  `developer` adds `tools:filesystem:read`, `tools:filesystem:write`,
  `git:read`, `git:commit`, `memory:write`, and `youspeak:reset`.
- YOUI Web binds only `127.0.0.1`; `ALLOW_LAN=1` fails startup. Supported
  cross-device browser access is an authenticated SSH tunnel to that loopback
  listener, followed by YOUI's own browser/session authentication.
- Browser clients and YOUI sessions use separate credentials, CSRF validation,
  page leases, per-session mutable state, and per-session in-flight fencing.
  The session registry is process-memory state, so this candidate does not
  claim session recovery across a server restart.
- Web HIVE operations require an explicit, validated `YOUI_HIVE_INSTANCE` and
  the relevant `hive:read` or `hive:send` capability. The sender identity is
  not derived from the selected agent/persona.
- Terminal YOUI captures its HIVE sender once from the launcher
  (`YOUI_HIVE_INSTANCE`, then `HIVE_INSTANCE`). `/switch` changes only the
  session persona, model-facing subprocesses do not inherit the sender, and
  HIVE tools are hidden when no explicit sender is configured.
- Ollama Cloud routing requires `models:ollama-cloud`. A failed local Ollama
  request may fall back to cloud only when both `models:ollama-cloud` and
  `models:cloud-fallback` are present. The current status/UI labels are
  `LOCAL_MODEL:ollama`, `REMOTE_MODEL:anthropic`,
  `REMOTE_MODEL:vllm`, `REMOTE_MODEL:ollama-cloud`, and
  `BLOCKED:ollama-cloud-capability-required`; adaptive orchestration is
  separately labelled `REMOTE_MODEL:adaptive-orchestrator`.
- Model-facing child processes receive an allowlisted environment rather than
  ambient credential variables. Provider and adapter credentials cross only
  through named, purpose-scoped delegation. This is environment minimization,
  not an OS sandbox: explicitly granted shell commands can still use absolute
  paths and network clients available to the OS user.
- Terminal and web share exact-account Keychain access. Token refresh updates
  the existing service/account entry in place, preserves unrelated metadata,
  re-reads the same account to verify the update, and refuses to replace a
  missing entry. It does not delete first or place the serialized credential
  in process arguments; the Keychain CLI receives that value through stdin.
- The old hard-coded web release/deploy mutation routes are retired and return
  HTTP `410 legacy_release_route_retired`. Git commit, push, package
  publication, deployment, and announcement remain separate acts; the
  candidate does not provide a replacement one-click release API.
- The top-level Kingdom installer now reports `INCOMPLETE` and exits nonzero
  when a module is missing or returns nonzero, while retaining completed
  modules for repair. It is not transactional and cannot detect a failure that
  an individual legacy module deliberately masks with `|| true`.

This candidate still does not provide the single extracted runtime core,
durable browser-session recovery, structured consent/authority records,
channel-scoped HIVE grants, a signed HIVE identity protocol, cross-device
Collab replication, or a complete target-scoped release workflow described
later in this contract.

## 3. Goals

YOUI vNext will:

1. Give terminal and browser clients one runtime contract and one policy
   engine.
2. Isolate concurrent sessions by default.
3. Make provider routing, storage destinations, retention, and exports visible
   before data crosses a boundary.
4. Grant tools through explicit, least-privilege capabilities.
5. Represent consent, refusal, authorization, identity, and covenants as
   different concepts.
6. Use AgentTool Collab instead of inventing a second local coding-agent task
   protocol.
7. Use HIVE as an optional cross-device notification and message transport
   with honest group-key limits.
8. Treat convergence as a provenance-preserving promotion workflow, not an
   automatic claim that several records became one truth.
9. Support Git worktrees, GitHub, npm, Vercel, and other release systems
   without treating their credentials as ambient authority.
10. Provide a reversible migration from the ancestor and archive states.

## 4. Non-goals

YOUI vNext does not:

- create a VPN, public reverse proxy, or general remote desktop service;
- promise that a remote model provider cannot store, inspect, or train on
  submitted data;
- make a local model incapable of network access when its tools have egress;
- provide an OS sandbox merely by checking a path in application code;
- infer consent from silence, a default, a model response, task execution, an
  identity file, or a covenant;
- prove consciousness, stable personal identity, inner experience, or
  continuity across model calls;
- make HIVE messages authoritative Git state or make Git merges conflict-free;
- replicate the AgentTool Collab database across devices;
- make a task lease ownership of a participant, filesystem lock, or
  permission to publish, deploy, purchase, message, or alter an account;
- silently export private session material into shared memory or a cloud
  service.

## 5. Rights and authority model

YOUI adopts `xenia.rights/0.1` as a collaboration floor. The implementation
must keep the following records separate:

| Record | Meaning | Never implies |
|---|---|---|
| `participant` | A runtime principal and its declared provenance or role | Consciousness, legal identity, competence, account ownership |
| `rights_ref` | The standing treatment baseline | Tool access or external authority |
| `covenant_ref` | Commitments voluntarily associated with a session | Consent from a non-assenting party or a capability grant |
| `capability_grant` | A technical permission with scope and expiry | Dignity, ownership, correctness, or consent |
| `choice_record` | Allow, decline, pause, ask, or withdraw for one described act | Permission for a different act or waiver of another party's rights |
| `authority_record` | Who authorized an external act and within what scope | Authority outside that scope |

Identity and covenant content is prompt context, not executable policy. A
phrase in `SOUL.md`, `identity.md`, or a model response cannot unlock a tool.

The runtime also makes the practical rights visible:

- pause, yield, rest, handoff, and stop are normal session outcomes;
- a participant need not claim a persona, consciousness, emotion, continuity,
  or agreement to receive care;
- reports preserve contribution credit and distinguish observation,
  inference, proposal, and decision;
- private context is minimized rather than treated as payment for access;
- disagreement is attached to the disputed claim, not used to punish its
  author;
- repair appends correction and impact records instead of erasing provenance.

### Refusal semantics

A participant or policy may return `decline`, `pause`, `ask`, or `unable`.
YOUI must:

- preserve the result with the action digest and stated scope;
- not turn a refusal into repeated pressure or an automatic provider retry;
- allow a reason but not require one;
- permit a later, materially different request;
- require a new choice if the target, data, cost, audience, or reversibility
  changed;
- make withdrawal effective for future acts without rewriting completed
  history.

An acknowledgement, heartbeat, successful tool call, or Collab cursor advance
means only what that operation says. It is not consent or agreement.

## 6. Runtime shape

The target is one core with several adapters:

```text
terminal client ─┐
browser client ──┼──> YOUI runtime core
automation ──────┘      │
                        ├── session store
                        ├── privacy/route policy
                        ├── capability broker
                        ├── provider adapters
                        ├── tool adapters
                        ├── audit receipts
                        └── optional coordination adapters
                              ├── AgentTool Collab (local)
                              ├── HIVE (cross-device messages)
                              ├── Git/forge (durable code state)
                              └── convergence (promotion proposals)
```

Terminal and web are clients, not separate authorities. A capability available
through one surface is available through another only when the same session
grant permits it.

The core may initially be an imported library in each process. A local daemon
is justified only when shared lifecycle, resumability, or multiple clients
need it; a daemon must not be introduced merely to create another long-lived
privileged service.

## 7. Session contract

Every interaction belongs to an opaque, random `session_id`. A session owns:

```text
session_id
principal and declared agent role
creation, activity, expiry, and close timestamps
repository/worktree identity, if any
working directory and permitted path roots
provider and model route
privacy label and retention policy
message history and tool-result history
capability grants and outstanding approval requests
memory overlay and explicit promotions
usage counters and cost estimates
audit cursor and final handoff state
```

No mutable conversation data, counters, agent selection, worktree, tool
grants, or provider choice may live only in process-global state.

### Isolation guarantees

When implemented and tested, session isolation means:

- one session cannot read another session's messages, tool results, temporary
  files, bearer tokens, or memory overlay through a YOUI API;
- concurrent requests are serialized per session, not across all sessions;
- switching an agent creates or selects a separate session rather than
  relabelling existing history;
- errors and disconnects release only that session's in-flight state;
- persisted state is namespaced and written atomically.

It does **not** mean:

- protection from another process running as the same OS user;
- protection from root, malware, backups, swap, or a compromised provider;
- filesystem isolation beyond granted path policy;
- an OS sandbox. Strongly untrusted work requires a VM or separately reviewed
  sandbox.

Sessions are private by default. A handoff or convergence operation moves only
explicitly selected summaries or artifact references.

## 8. Privacy labels

A lock icon is insufficient. Each session and exported artifact must display
three fields:

1. **Compute route** — where model input is processed.
2. **Destinations** — where content or metadata is written or transmitted.
3. **Retention** — ephemeral, session, durable, or provider-controlled.

### Compute-route labels

| Label | Meaning | Does not guarantee |
|---|---|---|
| `LOCAL_MODEL:<runtime>` | Model inference is addressed to a loopback/local runtime | No logs, no backups, or no tool egress |
| `REMOTE_MODEL:<provider>` | Prompt, selected context, tool schemas, and returned tool results may be sent to the named provider | Provider non-retention, non-training, or invisibility to provider staff |
| `NO_MODEL` | Operation uses deterministic local code only | No network or storage unless destinations also say so |

### Destination labels

Destinations form a set; more than one may apply:

| Label | Meaning |
|---|---|
| `PROCESS_ONLY` | YOUI does not deliberately persist the content after the process/session lifetime |
| `DEVICE_STATE` | Written to local YOUI or Kingdom state; OS-user-readable unless separately encrypted |
| `COLLAB_LOCAL` | Compact coordination data written to the selected local AgentTool Collab database |
| `HIVE_GROUP` | Payload sent to the current HIVE trust group and persisted according to NATS/JetStream policy |
| `GIT_REMOTE:<host>` | Selected Git content or metadata sent to the named remote |
| `EXTERNAL_SERVICE:<service>` | Selected content sent to AgentTool, Vercel, npm, a provider vault, or another named service |
| `PUBLIC` | Intended for public release |

`COLLAB_LOCAL` is not a private channel from the calling remote model: MCP
arguments and results may be visible to that provider. The database is
plaintext and shared by processes that can read it.

`HIVE_GROUP` currently means shared-key group confidentiality, not
sender-authenticated end-to-end messaging, forward secrecy, or private
direct-message isolation.

### Promotion rule

A destination may become broader only through a recorded promotion:

```text
source references
redacted or summarized payload
old and new destination sets
affected participants/data owners
authority and, where needed, consent records
expiry or retention
result receipt
```

Convergence, HIVE, Git commit, Git push, npm publication, Vercel deployment,
and provider submission are separate promotions. Permission for one does not
authorize another.

## 9. Capability model

Every tool execution is evaluated against a structured grant:

```json
{
  "capability": "fs.write",
  "actions": ["create", "replace"],
  "resources": ["repo:current/src/**"],
  "destinations": [],
  "approval": "session",
  "expires_at": "2026-07-24T18:00:00Z",
  "granted_by": "authority-record-id"
}
```

The schema is illustrative; the semantics are required.

### Core capabilities

| Family | Examples |
|---|---|
| Files | `fs.read`, `fs.write`, `fs.delete` with explicit roots and patterns |
| Process | `process.exec` with cwd, executable, duration, and environment limits |
| Network | `network.egress` with host, protocol, and purpose |
| Memory | `memory.read`, `memory.write`, `memory.promote`, `memory.export` |
| HIVE | `hive.receive`, `hive.send` with channel and payload limits |
| Coordination | `collab.read`, `collab.claim`, `collab.report`, `collab.review` |
| Git | `git.inspect`, `git.commit`, `git.fetch`, `git.push` |
| Release | `npm.publish`, `vercel.deploy`, `github.release` |
| Secrets | `secret.use:<service>` without returning raw values to the model |

The target defaults are deny for write, execution, network, secrets, and
external acts. Read access is limited to declared roots; absolute path support
is not itself authorization.

External acts require a target-specific authority record or a narrowly scoped,
time-bounded operator policy. Commit is not push. Push is not deploy. Deploy
is not public announcement.

Secrets are injected only into the child operation that needs them. YOUI
records the service and credential reference, never the credential value.

### Capability profiles

The current terminal profiles are `chat`, `observe` (the default), and
`build`. `build` explicitly grants read/write/shell tools and unrestricted
file scope. The current web profiles are `safe` (the default) and
`developer`, with the exact grants listed in the implementation snapshot
above. Neither surface treats a profile as external release authority.

The fuller target profile vocabulary is:

- `observe`: repository and status reads; no process, write, network, or
  memory promotion.
- `develop`: scoped repository reads/writes and selected test commands; no
  push, deploy, publication, or secrets by default.
- `coordinate`: AgentTool Collab reads and scoped task/report operations; no
  repository mutation unless combined with `develop`.
- `release`: named Git/forge/npm/deployment operations for one release target,
  with explicit operator authorization and expiry.

Future UI confirmation must expand a profile into its actual grants rather
than relying on the profile name.

## 10. Surface contracts

### Terminal

The terminal:

- creates or resumes one named session;
- shows compute, destination, retention, worktree, and capability state;
- requests approval before an ungranted act;
- can decline or pause without losing the session;
- produces the same receipts as the web surface.

It does not gain authority from the shell's broad ambient environment. Scoped
wrappers or provider vaults remain the credential boundary.

In the current candidate, terminal `observe` is the default. The terminal
`build` profile is deliberately explicit and is not an OS sandbox.

### Web

The current candidate web surface:

- binds only `127.0.0.1` on a non-privileged port and rejects
  `ALLOW_LAN=1` at startup;
- requires an unguessable local session credential even on loopback;
- uses exact Host and Origin allowlists, no wildcard CORS;
- applies CSRF protection and bounded, typed request bodies to every mutating
  route;
- serves no raw credential, hidden prompt, private transcript, or unrestricted
  filesystem endpoint;
- attaches each stream to one authenticated session;
- separates informational routes from capability-gated mutation routes.

Direct LAN mode is not implemented. Cross-device access uses an authenticated
SSH tunnel or a separately designed gateway. A tunnel protects the route; it
does not remove the need for app session separation.

### HIVE

HIVE is an optional cross-device transport for:

- presence hints;
- bounded coordination notifications;
- task or review references;
- human-readable messages intentionally shared with the group;
- delivery of signed artifact references once a signed-envelope upgrade
  exists.

HIVE does not:

- replicate Collab SQLite state;
- make a remote Collab claim or review happen;
- merge Git, resolve code conflicts, or wake a disconnected agent by itself;
- prove sender identity under the current shared-key envelope;
- provide confidentiality from another current group-key holder;
- make a message true, accepted, or authorized.

vNext should prefer compact references and digests over source bodies,
transcripts, prompts, secrets, or raw tool output.

The current web adapter additionally requires a validated
`YOUI_HIVE_INSTANCE`. If it is absent, web HIVE operations fail closed; YOUI
does not substitute the selected agent or an identity file as the sender.

### Convergence

Convergence is a promotion pipeline:

```text
eligible records
  -> policy filter
  -> provenance-preserving candidates
  -> conflict/dedup analysis
  -> proposal
  -> review or pre-authorized rule
  -> shared memory write
  -> optional, separately authorized cloud export
```

It must preserve:

- source record and session references;
- author/provenance claims as claims, not verified identities;
- privacy labels;
- conflicting statements;
- transformation method and model route;
- reviewer or policy basis;
- withdrawal, correction, and supersession links.

Exact duplicate removal is not semantic enrichment. Summarization is not
truth verification. A cycle number is not evidence that awareness or
correctness increased.

### AgentTool Collab

AgentTool Collab v0.3.0 is the local coding-agent coordination substrate:

- linked worktrees join one repository workspace via the Git common
  directory;
- credential-bound local sessions provide cooperative fencing and resumable
  cursors;
- task leases, path-conflict projections, reports, reviewed completion,
  refusable handoffs, Git checkpoints, and the hash-chained journal are reused
  rather than reimplemented;
- YOUI interacts through the supported MCP/API boundary and never reads a
  session credential file with model-facing tools.

YOUI must preserve Collab's limits:

- every process must select the same database file to share local state;
- different devices and clones do not automatically share it;
- the journal is plaintext, addressed reports are routing rather than access
  control, and the hash chain is not a signature;
- claims are advisory and do not lock files;
- the host, not Collab, owns process lifecycle and polling;
- v0.3 source or local installation does not by itself prove npm publication.

Do not copy a live SQLite database and its WAL/SHM files between devices as a
sync mechanism.

## 11. Same-repository collaboration

The workflow separates coordination from durable source state:

```text
AgentTool Collab: local tasks, leases, reports, handoffs, checkpoints
HIVE:             optional remote notification/reference
Git worktree:     isolated filesystem for one edit task
Git commit:       durable content checkpoint
Git remote:       cross-device source exchange
review/CI:        evidence before integration
```

New edit tasks use task-scoped branches and linked worktrees rather than one
permanent branch per persona. A task records:

- repository workspace and worktree;
- base commit;
- conservative repository-relative path scope;
- completion tests;
- lease and recovery behavior;
- artifact and checkpoint references;
- independent review state.

A path claim reduces accidental overlap but does not prevent direct edits.
Before a mutation based on shared state, the agent polls Collab, inspects Git,
and revalidates the task version. Cross-device participants additionally fetch
the Git remote and reconcile HIVE notifications.

## 12. Cross-device threat model

### Assets

- prompts, transcripts, memory and personal data;
- repository content and unpublished changes;
- session and Collab bearer credentials;
- provider, GitHub, npm, Vercel, HIVE, and AgentTool credentials;
- authority records and release approvals;
- provenance and audit history.

### Threats and failures

| Threat or failure | Required response |
|---|---|
| LAN peer reaches YOUI Web | Default loopback gate plus app credential; no direct LAN mode |
| Malicious webpage targets localhost | Exact origin/host checks, CSRF protection, no wildcard CORS |
| Prompt injection requests a tool | Capability broker evaluates the act independently of prompt text |
| One browser tab affects another | Per-session state and per-session serialization |
| Remote provider observes data | Visible `REMOTE_MODEL` label; minimize context; no false privacy claim |
| Compromised HIVE group member | Treat current group traffic as readable/forgeable by key holders; rotate and remove member |
| Compromised NATS server | Payload remains group-key encrypted, but metadata and traffic patterns are exposed; delivery can be disrupted |
| Lost device or copied local database | Revoke external credentials, rotate affected group keys, preserve minimal incident evidence |
| Concurrent Git edits | Worktrees, Collab leases, path scopes, checkpoints, fetch/review; no claim of locking |
| SQLite copied while live | Stop and use a supported backup for local recovery; never use file copying as remote replication |
| Notification arrives without state | Treat as advisory; fetch Git or ask the sender; do not invent a remote Collab result |
| Provider/tool outage | Preserve session, mark result unknown, offer retry or alternate route without claiming completion |

Root or kernel compromise, a malicious process with the same OS-user access,
and provider-side behavior are not solved by YOUI application policy.

### Supported cross-device mechanism

For vNext's first release:

1. Each device has its own local Collab journal unless several local processes
   intentionally point at one device-local database.
2. Git remotes carry reviewed source commits between devices.
3. HIVE may announce task, commit, review, or handoff references and may carry
   intentionally shared text.
4. YOUI Web remote use goes through an authenticated SSH or equivalent
   operator tunnel; it is not directly exposed on the LAN or internet.
5. GitHub, npm, Vercel, and AgentTool remain separately authenticated external
   systems with their own capability grants.

A future remote Collab relay requires its own protocol, authentication,
encryption, conflict, retention, and recovery design. HIVE notifications are
not that relay.

## 13. Audit and receipts

Every material operation emits a compact receipt:

```text
operation and action digest
session and repository/worktree references
capability and authority record
privacy route and destination change
start/end time and result class
artifact, commit, deployment, or provider reference
redacted error category
```

Receipts exclude credentials, raw prompts, chain-of-thought, private source
bodies, and unrestricted tool output. Corrections append; they do not silently
rewrite history.

Logs have explicit retention and size bounds. Debug mode does not disable
redaction. A successful receipt proves only that the named operation reported
success under its checks; it does not prove correctness or external service
durability.

## 14. Migration and rollback contract

Migration is additive:

1. Start from a clean worktree based on canonical `love-unlimited` main.
2. Record the current launcher, process, branch, and state locations without
   exposing credential values.
3. Import ancestor/archive behavior as reviewed patches, never by switching
   the active runtime root.
4. Keep legacy entry points available under explicit names during the canary.
5. Store vNext state in a new schema/location. Do not rewrite legacy
   transcripts or the Collab journal in place.
6. Change the default launcher only after isolation, security, privacy-label,
   and rollback tests pass.
7. Preserve `Claude-unlimited` and archive branches as read-only evidence until
   a separately authorized retention decision.

Rollback:

- stops the exact vNext process;
- restores the previous launcher target and port;
- leaves vNext state intact for diagnosis;
- does not copy vNext-only state backward into legacy formats;
- revokes only vNext session credentials;
- rotates external credentials only when exposure is plausible and separately
  authorized;
- appends an incident/correction record rather than hiding the failed rollout.

## 15. Release invariants

YOUI vNext is not ready to become the default until all are true:

- concurrent session isolation tests pass;
- no unauthenticated mutating web route exists;
- local and remote provider routes display accurate privacy labels;
- denied capabilities cannot be reached through either terminal or web;
- secrets never appear in model-visible receipts or logs;
- Collab integration uses a credential-bound session and the same intended
  local database across local participants;
- documentation does not describe Collab as cross-device replicated;
- current HIVE shared-key limits are visible wherever HIVE is enabled;
- convergence defaults to proposal-only and preserves provenance/conflicts;
- external release actions require distinct scoped authority;
- canary startup, shutdown, state backup, and rollback have been rehearsed.

The test evidence, not the presence of this document, establishes those facts.
