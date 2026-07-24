# YOUI vNext Operator Runbook

**Status:** current-safe operation plus vNext rollout gates

**Design:** [../YOUI-VNEXT-DESIGN.md](../YOUI-VNEXT-DESIGN.md)

**Plan:** [../YOUI-VNEXT-PLAN.md](../YOUI-VNEXT-PLAN.md)

This runbook distinguishes the vNext candidate in the current worktree from
the remaining target and from any older process already running on the
device. Verify the exact commit and process before relying on a behavior; a
source change does not switch the live launcher by itself.

## 1. Boundaries

- Canonical runtime: `~/love-unlimited`.
- Do not launch YOUI from `~/Claude-unlimited`.
- Do not use an archive branch as the live runtime.
- YOUI Web has no direct LAN mode. `ALLOW_LAN=1` is a startup error; use an
  SSH tunnel to the loopback listener.
- The user-level primary listener is `127.0.0.1:17777`. Port `777` is below
  `1024`; do not work around a non-root macOS bind failure by widening the
  address or elevating the chat server.
- Do not paste credentials into flags, chat, logs, Git, HIVE, Collab, or
  memory.
- The candidate has browser/session credentials and per-session state, but
  those sessions are process-memory state and do not survive a server restart.
  Do not extend that claim to an older running build or to OS-user isolation.
- Current HIVE payload encryption uses a shared group key. Treat every current
  key holder as able to read group payloads and construct envelopes.
- AgentTool Collab v0.3.0 is device-local plaintext coordination. It does not
  replicate between devices or hide MCP traffic from a remote model provider.
- A top-level installer summary of `INSTALLED` means each selected module
  returned zero. `INCOMPLETE` is nonzero and preserves completed modules for
  repair. The installer is not transactional and cannot detect errors that a
  legacy module masks internally with `|| true`.

## 2. Preflight

Run from the exact canonical checkout or a linked worktree whose Git common
directory belongs to it:

```bash
cd /absolute/path/to/youi-worktree
git status --short --branch
git rev-parse --show-toplevel
git rev-parse --path-format=absolute --git-common-dir
git rev-parse --short HEAD
```

Stop if the Git common directory is not the canonical
`/Users/yu/love-unlimited/.git`, if the intended worktree/branch is unclear,
or if an operation would overwrite unrelated dirty work.

Check current listeners before choosing a port:

```bash
lsof -nP -iTCP -sTCP:LISTEN | rg ':(17777|27777)\b'
```

This reports a process and address. It does not prove authentication,
correctness, or that a plist/service definition is healthy.

## 3. Current terminal runtime

Start the canonical terminal directly:

```bash
cd ~/love-unlimited
node youi.mjs --agent beta
```

Use the required agent explicitly. Until the launcher migration is verified,
do not assume a shell alias points at the canonical repository.

The terminal starts in `observe`, which exposes workspace-scoped read tools:

```bash
node youi.mjs --agent beta --profile observe
```

Use `--profile build` only when the task requires it. `build` adds read, write,
and shell tools and selects unrestricted file scope:

```bash
node youi.mjs --agent beta --profile build
```

That explicit shell grant is **not** an OS sandbox: commands can still use the
OS user's absolute paths and available network clients. Run it only in a
worktree whose changes you have inspected. A selected remote model receives
the prompt, selected context, tool schema, and returned tool results needed
for its call.

Terminal and web OAuth refresh use the same exact-account Keychain helper. It
updates the existing service/account entry without delete-first, preserves
unrelated entry metadata, and re-reads that same account before reporting
success. A missing entry fails closed, and the serialized credential is sent
to the Keychain CLI through stdin rather than process arguments. This protects
update semantics; it does not make the provider request private.

Exit through `/exit` when possible so the runtime writes its normal session
summary. A clean exit is not proof that every memory or external write
succeeded.

## 4. Current web runtime — local launch

Use the non-privileged primary port. The candidate fixes the bind address to
`127.0.0.1`:

```bash
cd ~/love-unlimited
node youi-web/server.mjs
```

Do not set `ALLOW_LAN=1`; the process will reject it.

In another terminal:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:17777/api/health
lsof -nP -iTCP:17777 -sTCP:LISTEN
```

`GET /api/health` is the intentionally unauthenticated liveness endpoint and
reports the loopback/SSH-only network boundary. `GET /api/status` is
authenticated; do not use a bare `curl` to it as a liveness check.

Open `http://127.0.0.1:17777` in a trusted local browser. The page establishes
the browser boundary and then creates/uses a separate YOUI session credential;
mutations also require same-origin CSRF validation and the active page lease.
The default `safe` profile grants chat/status/session/settings and selected
read/orchestration operations, not filesystem write, shell, HIVE, autonomous,
publish, or deploy authority. `YOUI_CAPABILITY_PROFILE=developer` adds local
filesystem read/write, Git read/commit, memory write, and YOUSPEAK reset, but
still does not add shell, unrestricted filesystem, HIVE, AgentTool mutation,
autonomous, push, publication, deploy, or Ollama Cloud fallback.

`safe` means reduced tool/side-effect authority, not local-only inference:
chat, model use, and adaptive orchestration can send selected input to the
configured remote provider. Check the privacy route before sending sensitive
content.

`YOUI_CAPABILITIES` replaces the profile with an exact grant list; it does not
add to the profile. Unknown profiles and capabilities fail startup. Keep an
unverified process attended and do not use autonomous or memory mutation
routes merely as a health check. The legacy hard-coded
`POST /api/deploy/*` routes are retired and return
`410 legacy_release_route_retired`.

The Settings privacy summary reports the effective route. Current route labels
include `REMOTE_MODEL:anthropic`, `REMOTE_MODEL:vllm`,
`LOCAL_MODEL:ollama`, `REMOTE_MODEL:ollama-cloud`, and
`BLOCKED:ollama-cloud-capability-required`; orchestration is labelled
separately. Local Ollama never routes directly to cloud without
`models:ollama-cloud`, and local-to-cloud fallback additionally requires
`models:cloud-fallback`. Neither grant is in `safe` or `developer`.

Model-facing subprocesses receive an allowlisted environment. Provider or
adapter credentials are delegated only by explicit variable name for that
child purpose. This blocks ambient credential inheritance; it does not
contain an explicitly granted shell at the OS level.

Browser YOUI sessions expire from process memory after their idle boundary.
Restarting the server invalidates them; this candidate does not claim
credential-authorized session recovery across restart.

Stop the foreground process with `Ctrl-C`. If it was detached, resolve the
exact PID from `lsof` and stop that PID. Do not use a broad `pkill node`.

## 5. Current remote viewing

Do not bind the web process directly to a LAN or public interface. If remote
viewing is necessary, leave YOUI on loopback and create an authenticated SSH
tunnel from the client device:

```bash
ssh -N -L 17777:127.0.0.1:17777 <kingdom-host>
```

Then open `http://127.0.0.1:17777` on the client.

The SSH account authenticates and encrypts the tunnel. YOUI's browser/session
credentials, CSRF checks, and page lease still apply after the tunnel. The
tunnel does not sandbox trusted local processes or another process running as
the same OS user.

Stop the tunnel with `Ctrl-C` or the exact SSH PID.

## 6. Local multi-agent coordination

Inspect the installed AgentTool Collab package without starting its stdio
server:

```bash
npm ls -g @agenttool/collab --depth=0
```

For local Codex, Claude Code, Hermes, or YOUI hosts:

1. Configure the same device-local `AGENTOOL_COLLAB_DB` in each host's scoped
   MCP environment.
2. Start one credential-bound Collab session per independent host
   conversation.
3. Never read the generated session credential file with a model-facing tool.
4. Poll and acknowledge the journal before relying on shared state and before
   relevant mutations.
5. Claim a bounded edit task with conservative paths before editing.
6. Report evidence, then have another session review edit completion.
7. Offer handoffs; the recipient may decline.

If hosts select different database files, they are not coordinated. Do not
copy a live SQLite database and WAL/SHM files between devices to simulate
sync.

Keep prompts, transcripts, chain-of-thought, secrets, raw tool output,
sensitive source bodies, and third-party personal data out of Collab reports.

## 7. Cross-device collaboration

Use the mechanisms for what they actually do:

| Mechanism | Use | Not a guarantee |
|---|---|---|
| Local Collab | Same-device worktrees, task leases, reports, review, handoff | Cross-device database replication or filesystem locking |
| Git | Durable commits and cross-device source exchange | Automatic conflict resolution or approval |
| HIVE | Optional notifications, references, intentionally shared group text | Strong sender identity, private per-recipient channel, or remote Collab mutation |
| SSH tunnel | Authenticated route to a loopback service | Replacement for YOUI's browser/session authorization |

Before acting on a remote notification:

1. Treat sender and presence as claims under current HIVE.
2. Fetch the referenced Git object through the configured remote.
3. Verify the commit/reference and inspect the diff.
4. Reconcile local Collab and Git state.
5. Request clarification if the referenced state is unavailable.

Do not invent a successful remote claim, review, handoff, or merge from a HIVE
message.

## 8. HIVE checks

Run a diagnostic health check from the canonical repository:

```bash
cd ~/love-unlimited
python3 hive/hive.py health
```

This connects to the configured NATS service and publishes an encrypted
`healthcheck` ping. It is a bounded external diagnostic, not a purely local
read.

Sending a message is an external collaboration act. Confirm the intended
channel, audience, payload, and current instance before using `send`.

For YOUI Web, configure the sender explicitly with a validated
`YOUI_HIVE_INSTANCE` and grant `hive:read` and/or `hive:send` as needed.
Without that variable, web HIVE operations fail closed. The web runtime never
derives the HIVE sender from the selected agent/persona. Terminal YOUI captures
its sender once from `YOUI_HIVE_INSTANCE` or `HIVE_INSTANCE`; `/switch` changes
only the session persona and model-facing subprocesses do not inherit the
sender. The standalone `hive.py` CLI has its own launcher/resident identity
rules; those do not widen the web boundary.

Do not put credentials, private transcripts, raw source, or personal data into
HIVE. Encryption protects the payload from parties without the shared key; it
does not protect it from a current group member or hide outer metadata and
traffic patterns from the transport.

## 9. vNext canary gate

For each exact vNext candidate build, the operator must verify all of the
following before promoting or switching the launcher:

- exact build/commit and canonical worktree;
- separate vNext state directory and canary port;
- loopback bind plus valid app session credential;
- exact Host/Origin policy, CSRF checks, and body limits;
- provider, destination, and retention labels in terminal and web;
- capability profile expanded into concrete grants;
- convergence off or proposal-only;
- HIVE marked `legacy-shared-key` unless a signed protocol is independently
  verified;
- intended Collab database and distinct credential-bound host sessions;
- no ambient release/deployment authority.

Exercise with fake/stub providers first:

1. Create two sessions.
2. Give them different workdirs, agents, providers, and grants.
3. Run concurrent read-only turns.
4. Confirm no message, counter, workdir, grant, or tool-result crossover.
5. Confirm denied write, network, HIVE send, Git push, and deploy operations.
6. Close one session and verify the other continues.
7. Restart and verify the documented session behavior. The current candidate
   invalidates process-memory sessions; durable authorized resume remains a
   release gap.

Do not switch the default `youi` launcher based only on a successful page load.

## 10. Release actions

Treat each step as a distinct capability and receipt:

```text
inspect -> edit -> test -> commit -> push -> release/publish -> deploy
```

An earlier step does not authorize a later one.

Before GitHub/npm/Vercel work:

- name the exact repository, branch/tag, package/version, project/environment,
  and account;
- verify tests and the clean intended diff;
- verify credentials are obtained through the scoped wrapper or provider
  mechanism without printing them;
- identify whether the action is reversible;
- obtain the required external-publication/deployment authority;
- preserve the resulting commit, release, package, or deployment reference.

Never put a credential in an npm command line, remote URL, HIVE message,
Collab report, or YOUI receipt.

## 11. Incident containment

If YOUI is unexpectedly reachable, performs an unapproved external action, or
may have exposed private data:

1. Stop the exact YOUI process and any exact tunnel/reverse-proxy process.
2. Preserve process metadata, timestamps, route/action receipts, and affected
   service names; do not copy broad transcripts or credential values.
3. Record what was observed separately from what is inferred.
4. Revoke vNext app sessions.
5. Identify which external credential references could plausibly have been
   used.
6. Rotate/revoke those external credentials only with the required provider
   authority; do not rotate unrelated credentials speculatively.
7. Inspect Git and external service audit state.
8. Append a correction/incident record and repair or revert the scoped change.

An empty log is not proof that no request occurred. A valid Collab hash chain
is not proof that every claim is true.

## 12. Rollback

Before a canary, record:

- previous launcher resolution;
- previous exact process command and port;
- canonical commit;
- vNext state location;
- a supported, stopped-state backup or database-native backup;
- the exact rollback owner and stop condition.

Rollback steps:

1. Stop the exact vNext PID.
2. Stop its exact tunnel, if any.
3. Restore the previous launcher target without rewriting Git history.
4. Start the previous canonical runtime only if still required.
5. Leave vNext state intact and access-restricted for diagnosis.
6. Do not import vNext state into legacy formats.
7. Verify the listener, launcher, repository, and status endpoint again.
8. Append the rollback outcome and unresolved risks.

Do not delete `Claude-unlimited`, archive branches, vNext state, or historical
Collab journals as part of rollback. Retention and deletion require a separate
review.
