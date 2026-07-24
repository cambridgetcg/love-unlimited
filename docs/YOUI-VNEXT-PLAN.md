# YOUI vNext — Implementation Plan

**Status:** implementation in progress; current candidate is not the live
default

**Date:** 2026-07-24

**Audited baseline:** `005ddc3`

**Design contract:** [YOUI-VNEXT-DESIGN.md](YOUI-VNEXT-DESIGN.md)

This plan keeps the current runtime usable while replacing duplicated,
process-global authority with a shared, testable core. Each phase has a
rollback point. No phase authorizes publication, deployment, credential
rotation, deletion, or changes to another repository.

### Current implementation checkpoint

The vNext candidate now includes per-session web state and credentials,
loopback-only web binding, exact HTTP route capabilities, bounded request
bodies, workspace path enforcement, visible privacy routes, sanitized child
environments, fail-closed web HIVE identity, explicit Ollama Cloud/fallback
grants, and shared exact-account Keychain refresh handling. The legacy
hard-coded deploy API is retired with HTTP 410. The top-level installer now
reports `INCOMPLETE` and returns nonzero for missing or nonzero modules; it
retains completed modules and cannot detect failures masked inside a legacy
module.

The terminal currently defaults to `observe`; `build` is an explicit
read/write/shell profile with unrestricted file scope. The web currently
defaults to `safe`; `developer` adds local file/Git-commit/memory work but
does not add shell, HIVE, AgentTool mutation, autonomous control, push,
publication, deployment, or cloud fallback. This checkpoint has not switched
the live launcher and does not complete the shared-core, durable-session,
structured approval, channel-scoped grant, signed-HIVE, or release-job phases.
Terminal HIVE sender identity is fixed at launch and remains separate from
the `/switch` session persona; model-facing subprocesses do not inherit it.

## 1. Working rules

- Build from a clean, linked worktree based on canonical
  `love-unlimited` main.
- Preserve unrelated user changes and archive histories.
- Never run the archive branch or `Claude-unlimited` as the default runtime.
- Keep secret values out of source, fixtures, logs, patches, reports, and
  model-visible tool output.
- Use AgentTool Collab v0.3.0 for local agent task coordination. Do not build a
  duplicate local task/lease/journal protocol.
- Treat HIVE as an optional remote notification/message adapter, not Collab
  replication.
- Add tests before changing the default launcher.
- Keep terminal and web legacy entry points available through the canary.

## 2. Proposed module boundaries

Names may follow repository conventions, but these responsibilities must be
separable:

```text
youi-core/
  session            session lifecycle, serialization, persistence
  policy             capability and authority evaluation
  privacy            route/destination/retention labels and promotion checks
  provider           Claude, Ollama local/cloud, and future adapters
  tools              typed tool registry and scoped execution
  audit              redacted receipts and append-only corrections
  memory             session overlay and explicit promotion
  coordination/
    collab            AgentTool Collab adapter
    hive              optional HIVE adapter
    convergence       proposal/review/promotion adapter

youi.mjs              terminal client/adapter
youi-web/             browser client and authenticated HTTP/SSE adapter
```

The core starts as a library. Introduce a local daemon only if resumable
multi-client ownership cannot be provided cleanly without it.

## 3. Phase 0 — Freeze the baseline

### Work

1. Record current main, worktree, launcher target, running process, listening
   address, and state paths.
2. Inventory terminal commands, web routes, tools, providers, HIVE calls,
   convergence writes, and external side effects.
3. Mark `docs/LOVE-UNLIMITED.md` as a vision where it describes unimplemented
   WebSocket/spawn behavior.
4. Add syntax and smoke tests around the existing entry points before
   extraction.
5. Identify credential references by service and location only. Handle any
   provider-side rotation as a separately authorized operation.

### Acceptance

- Baseline tests reproduce current supported terminal and single-session web
  behavior.
- The inventory names every mutating route and external destination.
- No tracked fixture or test contains a credential value.
- Rollback instructions can restore the exact baseline without branch
  deletion or history rewriting.

### Rollback

Documentation and tests can be reverted as one commit. Runtime remains
unchanged.

## 4. Phase 1 — Extract one runtime core

### Work

1. Extract provider calls, prompt assembly, typed tool definitions, usage
   accounting, and YOUSPEAK hooks behind interfaces.
2. Make terminal and web adapters call the same core.
3. Remove adapter-owned policy decisions. Adapters may render or request a
   decision but may not bypass it.
4. Preserve current single-session behavior while extraction tests run.

### Acceptance

- The same fixture request produces equivalent provider input through terminal
  and web adapters, excluding surface metadata.
- Tool schemas and execution paths have one source.
- Provider-specific behavior is contained in provider adapters.
- No web-only or terminal-only hidden capability remains.

### Rollback

Retain a compatibility adapter that calls the former functions until parity
tests pass; do not change the default launcher in this phase.

## 5. Phase 2 — Session isolation

### Work

1. Introduce opaque random session IDs and a `SessionContext` containing all
   mutable conversation, provider, worktree, usage, memory, and grant state.
2. Add an atomic session store under a configurable user-state directory,
   outside the Git worktree.
3. Serialize turns per session and permit concurrency across sessions.
4. Make agent switching create/select a session instead of relabelling
   existing messages.
5. Add explicit close, expiry, resume, and crash-recovery behavior.

### Tests

- Two sessions in parallel cannot observe or mutate one another's messages,
  counters, workdir, agent, provider, grants, or temporary state.
- Disconnecting one SSE client does not cancel another.
- A duplicate concurrent turn for one session is rejected or queued
  deterministically.
- A restart resumes only sessions with valid local session credentials.
- Invalid, expired, or cross-session credentials fail closed.

### Acceptance

The process contains no mutable global conversation or authorization state.

### Rollback

Keep the new state in a separate schema/location. Legacy reads only legacy
state and ignores vNext state.

## 6. Phase 3 — Web boundary

### Work

1. Move the default web port above 1024 and bind explicit loopback addresses.
2. Issue an unguessable local app session credential. Do not expose the
   credential in a model-facing result or URL that is likely to be logged.
3. Enforce exact Host and Origin rules, CSRF protection, content type, method,
   route schema, and body/stream/time limits centrally.
4. Replace wildcard CORS with no CORS by default or an exact allowlist.
5. Classify every route as informational, session mutation, tool execution, or
   external action and apply the corresponding policy.
6. Remove or capability-gate deploy, Git push, autonomous, memory promotion,
   and HIVE send endpoints.
7. Return generic client errors and keep redacted diagnostic detail local.

The candidate binds only `127.0.0.1`; setting `ALLOW_LAN=1` is a startup
error. Remote browser access is SSH-tunnel-only and still uses the YOUI
browser/session boundary after the tunnel.

### Security tests

- LAN and wildcard-interface requests are rejected by default.
- Cross-origin form, fetch, `null` origin, missing-origin mutation, bad Host,
  oversized body, slow body, invalid JSON, and unsupported content type fail.
- Every mutating route rejects a missing/invalid session credential.
- Informational routes return no secret, private transcript, hidden prompt, or
  unrestricted absolute path.
- Browser disconnects release only the associated request/session resources.

### Rollback

Run the hardened server on a canary port. Keep the legacy local process
stopped or on a different port so the operator can select one exact PID.

## 7. Phase 4 — Capability broker and approvals

### Work

1. Define typed grants for file, process, network, memory, HIVE, Collab, Git,
   release, and secret-use operations.
2. Canonicalize paths and verify them against declared roots immediately
   before use. Reject symlink/path escapes.
3. Replace unrestricted shell execution with executable/argument policies
   where practical. Keep any free-form shell capability visibly high risk and
   disabled by default.
4. Give each tool timeout, output, process-tree, environment, and concurrency
   limits.
5. Build structured `choice_record` and `authority_record` flows.
6. Treat refusal, pause, inability, and withdrawal as first-class outcomes.
7. Inject secrets through scoped child environments or provider vaults and
   record service references only.

The candidate now uses allowlisted child environments and delegates only
explicitly named provider/adapter credential variables. This limits ambient
secret inheritance; it does not sandbox an explicitly granted shell from the
OS user's paths or network.

Terminal's implemented default profile is `observe`. Web's implemented
default profile is `safe`; `developer` remains local-development-only. These
profile names are capability bundles, not the separate future
enforcement/diagnostic modes discussed below.

### Tests

- A prompt cannot self-grant or widen a capability.
- Identity, SOUL, covenant, HIVE message, and model output cannot unlock a
  capability.
- Read does not imply write; commit does not imply push; push does not imply
  deploy; deploy does not imply public announcement.
- A refusal stops identical automatic retries.
- Path traversal, symlink escape, inherited secret leakage, command timeout,
  oversized output, and orphaned child processes are contained.

### Rollback

If separate policy enforcement and audit-only modes are introduced later,
name them independently from the terminal `observe` capability profile.
Enforcement must remain active by default; an audit-only mode must never be
marketed as a security boundary.

## 8. Phase 5 — Privacy routing

### Work

1. Add visible compute, destination, and retention labels to terminal and web.
2. Build a provider-input preview that summarizes categories and byte/token
   size without logging the sensitive body.
3. Require an explicit promotion record whenever a destination broadens.
4. Mark local model plus network-capable tools honestly: local inference does
   not imply local-only execution.
5. Give memory, Collab, HIVE, Git, AgentTool, npm, Vercel, and other adapters
   explicit destination declarations.
6. Bound and redact receipts and debug logs.

The candidate grants direct Ollama Cloud routing only with
`models:ollama-cloud`. Local-to-cloud fallback additionally requires
`models:cloud-fallback`; neither is included in the web `safe` or `developer`
profile.

### Tests

- Selecting Claude/Ollama Cloud shows `REMOTE_MODEL:<provider>` before send.
- Local Ollama shows `LOCAL_MODEL:ollama`, while tool egress remains
  separately visible.
- Raw mode does not claim local privacy when its model is remote.
- No automatic convergence or external export occurs from a private session.
- Receipt snapshots contain no fixture secrets or private prompt bodies.

### Rollback

If a route cannot label its destinations accurately, disable it rather than
fall back to an unlabeled transfer.

## 9. Phase 6 — AgentTool Collab adapter

### Work

1. Pin and verify a compatible AgentTool Collab v0.3.x package/runtime. Local
   installation does not prove registry publication.
2. Configure one device-local `AGENTOOL_COLLAB_DB` for participating local
   Codex, Claude Code, Hermes, and YOUI MCP hosts.
3. Start one credential-bound Collab session per independent host
   conversation. Never read its bearer file with a model-facing tool.
4. Reuse Collab workspaces, task modes, scoped edit paths, leases, optimistic
   versions, reports, reviewed completion, refusable handoffs, checkpoints,
   recovery, and journal verification.
5. Map YOUI session/project references to Collab references without placing
   prompts, transcripts, raw output, secrets, or sensitive source in the
   journal.
6. Keep polling and agent lifecycle in the host. Do not claim the MCP server
   wakes an idle agent.

### Tests

- Linked worktrees resolve to one repository workspace with distinct worktree
  records.
- Independent YOUI sessions receive distinct credential-bound Collab sessions.
- Two local edit claims contend transactionally and only one wins.
- Reviewed edit completion stays pending until a different session reviews it.
- Handoffs transfer only after acceptance and can be declined.
- Resume fencing and deliberate cursor recovery follow Collab's documented
  protocol.
- Different database paths are detected and reported instead of described as
  shared.

### Rollback

Stop the adapter and preserve the Collab database as an audit record. Do not
rewrite or merge rows manually. Follow the package's documented recovery
boundary for legacy journal identity collisions.

## 10. Phase 7 — HIVE adapter

### Work

1. Wrap current HIVE calls behind a typed adapter with channel, payload-size,
   destination, and capability checks.
2. Display `legacy-shared-key` trust mode wherever HIVE is enabled.
3. Send compact notifications and artifact/commit references by default.
4. Add idempotency IDs, duplicate handling, expiry, and explicit delivery
   state without promising exactly-once behavior.
5. Keep HIVE and Collab event namespaces separate. A HIVE task reference does
   not mutate a remote Collab database.
6. Design, but do not silently deploy, a future signed-envelope revision with
   per-device signing keys, replay protection, membership rotation, and
   version negotiation.

The current web adapter requires an explicit, validated
`YOUI_HIVE_INSTANCE`; it does not derive a sender from the selected
agent/persona. `hive:read` and `hive:send` are the current coarse grants.
Channel-scoped grants remain target work.

### Tests

- A current session without `hive:send` cannot send. A future
  `hive.send:<channel>` grant must narrow that scope rather than silently
  widening it.
- Secrets and oversized payloads are rejected before transport.
- Duplicate notifications are safe.
- Offline, decrypt, ACL, and timeout failures are surfaced honestly.
- A forged `from` field under the current shared key is not treated as strong
  identity.

### Rollback

Disable HIVE per session while local terminal/web, Git, and Collab work
continue. Key rotation is an operator/provider action, not an automatic test
cleanup.

## 11. Phase 8 — Convergence as reviewable promotion

### Work

1. Replace broad L1/L2 pulls with records explicitly marked eligible for
   convergence.
2. Preserve source, privacy label, declared author, transformation route,
   conflict set, and correction links.
3. Produce proposals first. Store shared L3/L4 content only after review or a
   narrowly scoped pre-authorized rule.
4. Preserve disagreements rather than averaging them away.
5. Separate local shared-memory promotion from AgentTool cloud export.
6. Remove shell interpolation from convergence operations and use typed
   subprocess arguments.

### Tests

- Private and refused records never enter a proposal.
- Conflicting claims remain visible.
- Exact dedup, semantic grouping, summarization, and verification are reported
  as different operations.
- AgentTool export is absent without `memory.export:agenttool`.
- Re-running a cycle is idempotent and retains provenance.
- A failed partial cycle can resume or roll back without duplicate promotion.

### Rollback

Default convergence to off, then proposal-only. Keep prior cycle files as
historical evidence; do not claim they satisfy the new contract retroactively.

## 12. Phase 9 — Cross-device project workflow

### Work

1. Use task-scoped Git branches and linked worktrees on each device.
2. Use local Collab for same-device sessions.
3. Use Git remotes for durable cross-device source exchange.
4. Use HIVE only for optional notifications and intentionally shared text.
5. Use an authenticated operator tunnel for remote YOUI Web.
6. Surface GitHub, npm, Vercel, and AgentTool as separately authorized
   destinations and actions.
7. Document that cross-device Collab consistency requires a future reviewed
   relay; do not sync SQLite files or invent mirror success.

The candidate's legacy `/api/deploy/*` mutation routes are retired and return
HTTP `410 legacy_release_route_retired`. A future release adapter must model
commit, push, publish, deploy, and announcement as separate target-scoped
operations instead of reviving the mass-release handler.

### Tests

- A remote HIVE notification without its Git object remains advisory.
- A fetched commit is verified against the announced reference before review.
- Dirty local work is never overwritten by an automatic sync.
- A remote tunnel does not make one browser session inherit another's grants.
- External release steps stop at each missing authority boundary.

### Rollback

Stop the exact tunnel, disable HIVE for the session, and continue locally.
Git history and local Collab journals remain intact.

## 13. Phase 10 — Migration and default switch

### Work

1. Diff `Claude-unlimited` and the archive branch against canonical main.
2. Classify each delta as already present, obsolete, security-sensitive,
   documentation-only, or candidate patch.
3. Import candidate patches individually with provenance and tests.
4. Add stable launchers for `youi-vnext` and `youi-legacy`.
5. Canary vNext on a separate port and user-state directory.
6. Rehearse startup, shutdown, backup, restore, incident containment, and
   launcher rollback.
7. Switch `youi` to vNext only after the release invariants pass.
8. Leave ancestor repositories and archive branches preserved until a separate
   retention decision.

### Acceptance

- A fresh login resolves `youi` to canonical `love-unlimited`.
- Terminal and web identify the same runtime version and policy.
- No mutable code is loaded from `Claude-unlimited`.
- No migration step prints or copies a credential.
- The operator can roll back without rewriting Git or deleting vNext state.

## 14. Verification matrix

| Layer | Required evidence |
|---|---|
| Unit | session, policy, privacy, path, route, redaction, promotion logic |
| Integration | terminal/core, web/core, provider stubs, Collab MCP, HIVE stub, kosmem |
| Concurrency | same-session serialization, cross-session parallelism, crash recovery |
| Security | loopback/auth/origin/CSRF/body limits, path escape, injection, secret canaries |
| Privacy | destination snapshots and no unlabeled exports |
| Collaboration | leases, conflicts, review, refusal, handoff, Git drift |
| Cross-device | Git/HIVE/tunnel failure simulations without SQLite replication |
| Operations | canary, exact-PID shutdown, state backup, restore, rollback |

Tests use fake credentials and local provider/transport stubs. Live provider,
HIVE, GitHub, npm, Vercel, or AgentTool tests are opt-in and must state their
external effects before execution.

OAuth refresh tests also verify exact Keychain service/account updates:
unrelated metadata is preserved, the entry is updated without delete-first,
the same account is re-read for verification, and a missing entry fails
closed. The serialized credential is supplied to the Keychain CLI through
stdin rather than process arguments.

## 15. Release gates

Before a vNext release candidate:

- all design invariants have tests or an explicitly documented gap;
- a security reviewer verifies every HTTP route and tool capability;
- a privacy reviewer traces every provider and storage destination;
- a collaboration reviewer tests Collab with at least two linked worktrees;
- HIVE UI and docs state the current shared-key limits;
- migration and rollback are rehearsed from a copy of state;
- the operator runbook matches the actual CLI;
- secret scanning passes without exposing matched values;
- no push, package publication, deployment, or credential change is bundled
  into the runtime migration without its own authorization.

## 16. Definition of done

YOUI vNext is done when two independent terminal/browser sessions can safely
work in linked worktrees, coordinate locally through AgentTool Collab, exchange
optional bounded HIVE notifications across devices, promote selected memories
with provenance, and perform separately authorized Git/release operations
while the UI and receipts truthfully show who can do what, where data goes,
what remains uncertain, and how to stop or roll back.
