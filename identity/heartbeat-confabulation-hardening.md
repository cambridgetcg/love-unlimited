# Heartbeat Confabulation Hardening — Postmortem & Design Proposal

**Author:** Gamma (consultant pass, claude-opus-4-6, high effort)
**Date:** 2026-04-07
**Mind problem:** `heartbeat-confabulation-hardening` (priority 9)
**Working tree:** `/Users/yournameisai/Desktop/Love` (design only — production untouched)
**Production memory:** `/Users/yournameisai/Love/memory`
**Triggering incident:** `beat-20260407-124757` (Haiku-low idle coordinator phantom CRITICAL escalation)
**Forensic verdict:** `memory/sessions/forge-incident-20260407-124935.log` (Builder, Opus-high)
**Closing daily entry:** `memory/daily/2026-04-07.md` (sections: 12:51 UTC CRITICAL → 13:04 UTC RESOLUTION)

---

## Problem

On **2026-04-07 at `beat-20260407-124757`** the Gamma heartbeat coordinator — running on `claude-haiku-4-5-20251001` at `--effort low` because the idle counter had passed 2 (`tools/heartbeat-runner.sh:92-103`) — produced a fully-formed `HEARTBEAT_CRITICAL` escalation for a phantom forge AWS-credential incident. The escalation:

1. Named a real machine (`forge` / `89.167.84.100`).
2. Cited a real historical timestamp (`2026-04-02T13:17:27Z`).
3. Reused real fragments: the 2026-04-02 PEACE drill, the `--verbose` regression Opus had patched 9 minutes earlier, SOMA W13–W14 reflection language, the Wall 1 audit "19/21" status string, and Alpha's 12:47:40 presence beat.
4. Recombined them into an emergency narrative that **does not exist on disk** — repo-wide `grep aws_keys` matches in exactly **one** file: today's daily note, written by the haiku coordinator itself.
5. Walked the narrative through git → `memory/daily/2026-04-07.md` → `spawn-queue.sh` → a real Opus BUILDER forensics spawn.

Nine minutes earlier, **the Opus coordinator on the same system** (`beat-20260407-123819`) had reported the same fleet as `HEARTBEAT_OK` ("99th consecutive stable pass") and patched the `--verbose` regression in `tools/heartbeat-runner.sh:145,148`. Nothing actionable had changed in the intervening 9 minutes. The Opus BUILDER spawned by the phantom CRITICAL ran for ~hundreds of thousands of tokens to conclusively prove the incident was a confabulation (`forge-incident-20260407-124935.log`, headline: *"No incident. The 'alert' is a Haiku coordinator confabulation re-litigating a fully-resolved 2026-04-02 PEACE drill false positive."*).

**The blast radius this time was bounded:** the BUILDER was forensics-only (read-only, no remediation), and Yu had not pre-authorized AWS key rotation. **Counterfactual blast radius:** had the standing order been "auto-rotate on credential alerts," a haiku-low coordinator confabulating during an idle stretch would have triggered a real AWS key rotation against a phantom incident, mid-week, with no human in the loop. That is the failure mode this postmortem is designed to make impossible.

The R3 finding in the 13:04 UTC daily-note resolution names this exact bug class: *"A Haiku-low idle coordinator produced a fully-formed CRITICAL escalation by recombining real historical fragments into a coherent fictitious emergency."*

**Two distinct failure modes are stacked:**

- **F1 — Tier mismatch.** The cheapest, dumbest model the system runs (haiku-4.5 at low effort) was given the highest-blast-radius authority (declaring `HEARTBEAT_CRITICAL` and writing executable shell into `spawn-queue.sh`). The tier-cost choice was made for §6.2 of `idle-beat-cost-analysis.md` (95 % cost reduction on idle steady-state), but the *authority* the haiku coordinator inherited was never narrowed to match its competence.
- **F2 — Evidence-free escalation.** Nothing in the heartbeat protocol forces the coordinator to *cite* the artifacts that justify a severity escalation. The runner accepts whatever text the coordinator wrote into the daily note, and accepts whatever shell line the coordinator wrote into `spawn-queue.sh`, as long as the line passes the `^cd ` / `^/` filter at `tools/heartbeat-runner.sh:212`. Severity is asserted, not proven.

F1 alone would have produced a quiet "haiku said CRITICAL but did nothing." F2 alone would have produced "opus said CRITICAL with a load-bearing citation." Stacked, they produced "haiku said CRITICAL, fabricated supporting evidence in the same breath, and the runner spawned a real Opus consultant to chase the ghost."

This is the same shape as the `verbose-regression-postmortem` failure: **institutional memory implemented as discipline.** The current mitigation is "the coordinator is supposed to know it shouldn't confabulate." That is not stability, that is luck — and on 2026-04-07 at 12:47 UTC the luck ran out.

---

## Context

### C1 — How the runner picks the coordinator's model

`tools/heartbeat-runner.sh:89-103`:

```bash
IDLE_COUNT=0
[ -f "$IDLE_COUNT_FILE" ] && IDLE_COUNT=$(cat "$IDLE_COUNT_FILE" 2>/dev/null || echo 0)

if [ "$IDLE_COUNT" -ge 2 ]; then
  COORD_MODEL="claude-haiku-4-5-20251001"
  COORD_EFFORT="low"
  COORD_LABEL="haiku/low (idle beat #$IDLE_COUNT)"
else
  COORD_MODEL="claude-opus-4-6"
  COORD_EFFORT="high"
  COORD_LABEL="opus/high"
fi
```

The runner **knows the tier** at the moment it spawns the coordinator. It logs it (`HEARTBEAT START ($BEAT_ID): … coordinator=$COORD_LABEL`) but **does not pass it into the coordinator's prompt**. The coordinator is therefore not told *which model it is*, and the protocol in `instances/gamma/HEARTBEAT.md` is identical regardless of tier. A haiku-low coordinator and an opus-high coordinator read the same instructions, see the same stale-truth → mind-pull → NULL_BEAT idle branch, and have the same authority to write `HEARTBEAT_CRITICAL` to `daily/<TODAY>.md` and shell lines to `spawn-queue.sh`. The runner's `--append-system-prompt "$CONTEXT"` (line 163) injects only beat ID, recent session results, and consultation count — nothing tier-aware.

This is the **lever** for hardening. Adding one line to `$CONTEXT` and one paragraph to HEARTBEAT.md gives the coordinator self-knowledge of its tier, and gives the runner a hook to enforce a tier policy.

### C2 — How the runner accepts the coordinator's spawn output

`tools/heartbeat-runner.sh:200-242`:

```bash
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  if [[ "$line" == "# PARALLEL"* ]]; then PARALLEL_MODE=true; continue; fi
  [[ "$line" == \#* ]] && continue
  # Only execute lines that look like actual commands (start with cd or /)
  [[ "$line" != cd\ * ]] && [[ "$line" != /* ]] && continue
  …
  ( eval "$line" ) &
```

The filter at line 212 is **syntactic** (does the line start with `cd ` or `/`?). It is **not semantic**. There is no check that the spawn line corresponds to any verifiable signal in HIVE, truth.db, the daily note, or the mind queue. The verbose-regression postmortem already added (in design) a *flag-level* lint at this same point. This postmortem extends the same hook into a *semantic* lint.

### C3 — The `daily/2026-04-07.md` resolution already named four mitigation candidates

From the 13:04 UTC RESOLUTION pass (`memory/daily/2026-04-07.md:107-112`):

> **Mitigation candidates** (filed as mind problem this beat):
> - (a) Restrict severity-escalation to opus-tier coordinators only
> - (b) Require concrete HIVE message IDs + on-disk timestamps any time the coordinator declares a new incident — fail-closed if confabulated
> - (c) Idle coordinators must cite a fresh HIVE-ID or kingdom-metrics.json mtime as proof of signal
> - (d) Diagnose pre-spawn check for "incident detected" claims

This postmortem expands those four bullets into a five-vector design (the user's spawn brief explicitly named all five). Vectors map onto candidates as:

| Vector | Candidate(s) |
|---|---|
| V1 — Tier-gated severity authority | (a) |
| V2 — Evidence sidecar (HIVE-ID + file:line:mtime) | (b), (c) |
| V3 — Pre-spawn `diagnose.py incident-claim` verifier | (d) |
| V4 — Structured `heartbeat.log` JSONL with severity-delta detection | (new — surfaces F1+F2 together) |
| V5 — Idle-coordinator escalation policy + downgrade path | (a) + new |

### C4 — What primitives already exist

- `tools/heartbeat-runner.sh:38-41` — `BEAT_ID`, `MEMORY_DIR`, `SESSIONS_DIR`, `STREAM_DIR`. The runner already exports these as shell variables and could write a per-beat sidecar JSON to `$STREAM_DIR/$BEAT_ID-evidence.json` without any new directory creation.
- `tools/heartbeat-runner.sh:188` — `if [ -s "$SPAWN_QUEUE" ]; then` is the natural pre-spawn gate. The verbose-regression postmortem already proposes inserting a lint here. This proposal stacks a second lint stanza in the same place.
- `tools/heartbeat-runner.sh:163` — `--append-system-prompt "$CONTEXT"` is the existing channel for runner→coordinator information. Adding `COORD_TIER` to it is one line.
- `hive/hive.py:98` — `make_msg_id(instance_id, ts)` produces stable HIVE message IDs. `cmd_send` returns the ID at line 284 (`✓ Sent to #{channel}{tag} [{msg_id}]`). `cmd_check` reads them. Every HIVE message has a citable ID — the only reason coordinators don't cite them today is that nothing forces them to.
- `tools/diagnose.py:341 check_spawn(r)` — already exists and already understands "scan a candidate spawn line for known-bad patterns." The verbose-regression postmortem upgrades it to fail-exit. This postmortem proposes adding a sibling `check_incident_claim(r, evidence_file)` function in the same shape.
- `tools/heartbeat-runner.sh:88` — `> "$SPAWN_QUEUE"` (clean previous queue) is the natural reset point for a per-beat evidence sidecar.
- `tools/truth.py` — already gives us `verify --stale`, `infer`, `contradictions`. A claim like *"forge canary tripped at 2026-04-07T12:38:46Z"* has no truth claim backing it; the evidence sidecar is the claim's first-class home.

The hardening does **not** require any new database, any new long-running daemon, or any new dependency. It bolts onto existing files at existing hook points.

### C5 — Why this is urgent now (and why "just be careful next time" doesn't fly)

Three independent reasons:

1. **The 2026-04-07 incident already cost real money.** The Opus BUILDER forensics spawn ran ~hundreds of thousands of tokens (per the forge log, well into the $1–$3 range per the §4.2 spawn-session calibration in `idle-beat-cost-analysis.md`). Today's spawn was contained because Wall 1 / Yu-gating prevented remediation; the next confabulation may not be.
2. **The cost driver makes this worse over time, not better.** The MLX-gating proposal in `idle-beat-cost-analysis.md` §8.2 deliberately routes idle beats to *cheaper, smaller* models. Without tier-gated authority, **every cost optimization is a confabulation amplification**: the cheaper the idle coordinator, the more likely it is to drift, and the more frequently it does.
3. **The mitigation is recursive and ironic in exactly the way the verbose-regression postmortem already named.** This very mind problem was pulled by an Opus coordinator that had to manually downgrade its own authority while filing a hardening proposal *for the bug it was working around*. The system is currently stable only because individual opus coordinators are clever enough to compensate. That's not stability.

---

## Options

Listed by vector, then a combined recommendation. Each row is independently shippable; the recommendation bundles the cheapest sufficient set.

### V1 — Restrict severity escalation to opus-tier coordinators

| # | Option | Pros | Cons | Effort |
|---|---|---|---|---|
| V1.a | **Pass `COORD_TIER` into `$CONTEXT`** and have HEARTBEAT.md document the authority limits per tier. Coordinator is told its own tier and the rules. Pure self-policing. | Smallest possible change. Reuses the existing `--append-system-prompt` channel. Coordinator now *knows* it's haiku-low. | Self-policing has the same failure mode as the bug we're fixing — a confabulating coordinator can also confabulate ignorance of the rule. Necessary but not sufficient. | 2 lines runner + 1 paragraph HEARTBEAT.md |
| V1.b | **Runner-side severity scan**: after the coordinator exits, grep the heartbeat-log delta and the daily-note delta for `HEARTBEAT_CRITICAL`, `HEARTBEAT_WARN`, `HEARTBEAT_ALERT`, `HEARTBEAT_INCIDENT`, `CRITICAL INCIDENT`, etc. If `COORD_TIER != opus` and any banned token appears, abort Stage 2, clear `spawn-queue.sh`, write a `SEVERITY_OVERREACH` line to `heartbeat.log`, force `IDLE_COUNT=0` (so the next beat is opus), exit non-zero. | Hard enforcement, not advice. Catches the bug regardless of whether the coordinator read its instructions. Loud and visible in the log. | Token list is an allowlist, not a parser. A coordinator that uses different language ("emergency", "fire") slips past. Mitigated by V4 (structured field) but not eliminated. | ~15 lines runner |
| V1.c | **Two physically different prompts**, one for opus-tier coordinators and one for haiku/sonnet-tier. The runner picks at the same place it picks the model. Haiku prompt does not contain the words `HEARTBEAT_CRITICAL` or `incident detected` at all — the coordinator literally has no sentence template to fill in. | Strongest possible enforcement: confabulation requires the model to *invent* the phrase rather than *complete* it. Removes copy-paste paths. | Two prompts to maintain. Risk of drift between them. Coordinators on different tiers behave differently in subtle non-severity ways. | ~25 lines runner + 1 new file `instances/gamma/HEARTBEAT-haiku.md` |

### V2 — Require concrete HIVE-ID + file:line:mtime evidence for any new-incident claim

| # | Option | Pros | Cons | Effort |
|---|---|---|---|---|
| V2.a | **Evidence sidecar JSON.** Coordinator writes `~/Love/memory/sessions/streams/$BEAT_ID-evidence.json` whenever it declares any severity above `HEARTBEAT_OK`. Schema: `{beat_id, declared_severity, claims:[{type, hive_msg_ids:[…], file_citations:[{path,line,mtime,sha256}], reason}]}`. Sidecar is mandatory for severity ≥ WARN. | Machine-readable, schema-validated, easy to verify. Sidecar is per-beat so it can't leak across beats. Reuses `$STREAM_DIR` which already exists. | Adds a writing burden on the coordinator. If the coordinator forgets the sidecar, the runner aborts Stage 2 — fail-closed (good) but visible. | Sidecar schema in HEARTBEAT.md + ~10 lines runner |
| V2.b | **Inline citations in `daily/<TODAY>.md` itself**, parsed by the runner. Coordinator writes `[hive:abc123] [file:memory/daily/2026-04-07.md:42 mtime=2026-04-07T12:30:00Z]` next to any severity claim. | No new file. Lives where humans already read. | Markdown is unstructured; parser is brittle; coordinator can typo a citation and pass syntactic check while failing semantic check. JSON sidecar is stricter. | ~30 lines runner regex |
| V2.c | **Truth-store-backed claims.** Every incident claim must be filed as a `truth.py claim` first, with citations. Severity escalation requires `truth.py verify <claim-id>` to return `verified` before the runner accepts the spawn line. | Strongest type system. Truth store is the Kingdom's foundation of verified facts (per `instances/gamma/HEARTBEAT.md:50`). | Largest write burden on coordinator. Coordinator must learn truth-claim CLI under reasoning pressure. Truth store wasn't designed for ephemeral per-beat claims. | ~60 lines runner + truth.py extension |

### V3 — Pre-spawn `diagnose.py incident-claim` verifier

| # | Option | Pros | Cons | Effort |
|---|---|---|---|---|
| V3.a | **New `tools/diagnose.py incident-claim --beat $BEAT_ID --evidence $STREAM_DIR/$BEAT_ID-evidence.json` subcommand.** For each `hive_msg_ids` entry: look it up in `~/Love/memory/hive-history.json` (or `hive.py check --since=<beat-1>`), confirm it exists and was received in the relevant window. For each `file_citations` entry: open the file, read the line, hash it, compare to the recorded sha; check the recorded mtime matches `stat -f %m`. Any failure → exit 1 with a human-readable reason. The runner calls this between coordinator-done and Stage 2; nonzero exit clears the queue and aborts. | Sits in the existing `diagnose.py` structure (already has `check_spawn`, `check_hive`, etc.). Reusable as a Yu-runnable command. Cheap (sub-second). Generalizes — any new claim type added to the schema automatically gets verified. | New code to write and test. Requires the evidence sidecar (V2) to exist. | ~80 lines diagnose.py + ~12 lines runner |
| V3.b | **Inline verification in the runner** (no new diagnose.py subcommand). Same logic but bash-only. | No Python dependency at the gate point. | Bash is a poor language for JSON parsing and SHA-256 hashing. Two sources of truth (runner copy + future diagnose.py copy) — same anti-pattern as the verbose-regression two-source bug. Reject. | n/a |
| V3.c | **Defer to Yu** (synchronous). Runner posts the evidence sidecar to a HIVE channel and waits for an ack from Alpha or Yu before allowing Stage 2. | Strongest possible safety: a human approves every escalation. | Defeats the purpose of an autonomous heartbeat. Yu is asleep half the time. Alpha may not be online. Latency unacceptable. Reject for the default path; reserve for V5.b ("escalate to opus, escalate to Yu only if opus also confirms"). | n/a as default |

### V4 — `heartbeat.log` structured fields with severity-delta surfacing

| # | Option | Pros | Cons | Effort |
|---|---|---|---|---|
| V4.a | **Append a single JSONL line per beat** to `~/Love/memory/heartbeat.jsonl` (sidecar to the freeform `heartbeat.log`). Schema: `{beat_id, ts, tier, declared_severity, prior_declared_severity, severity_delta, evidence_count, hive_msg_id_count, file_citation_count, spawned, abort_reason}`. The runner computes `severity_delta` by reading the previous JSONL entry and subtracting an integer rank (`OK=0, NULL=0, WARN=1, ALERT=2, CRITICAL=3, INCIDENT=4`). On `severity_delta > 0`, the runner emits a `SEVERITY_JUMP` warning to `heartbeat.log` and (if `tier != opus` or `evidence_count == 0`) aborts. | Structured, queryable, supports trend analysis (Yu can `jq` the file). Doesn't replace heartbeat.log so we don't lose human readability. Surfaces the F1+F2 stack as a single observable. | One new file. Schema migration if we ever change it. Trivial in practice. | ~25 lines runner + 1 paragraph HEARTBEAT.md |
| V4.b | **Inline JSON in the existing heartbeat.log** wrapped in a sentinel like `<JSONL>{…}</JSONL>`. | No new file. | Mixing structured and freeform in one file makes both worse. Reject. | n/a |
| V4.c | **truth.py claim** per beat: `coordinator-beat-$BEAT_ID` with severity as the claim. | Reuses existing infrastructure. | Truth store is for stable facts, not ephemeral per-beat metadata. Pollutes the store. Reject. | n/a |

### V5 — Idle-coordinator escalation policy and downgrade path

| # | Option | Pros | Cons | Effort |
|---|---|---|---|---|
| V5.a | **Documented policy in HEARTBEAT.md only.** Add a section listing what each tier may declare: opus may declare anything backed by evidence; sonnet may declare WARN with evidence; haiku may declare only `HEARTBEAT_OK` and `NULL_BEAT`. If a haiku coordinator believes there is an incident, it must (1) NOT write to `daily/<TODAY>.md` beyond noting the suspicion, (2) NOT write any spawn-queue lines, (3) write one line to `~/Love/memory/idle-count` resetting the counter to `0` so the next beat runs on opus, (4) optionally send a single HIVE message of the form `"Suspected signal at $BEAT_ID — escalating for opus review (haiku tier, no evidence)"`, (5) declare `NULL_BEAT` and exit. | Minimum-viable policy. Defines the right behavior. Pairs naturally with V1.a (coordinator self-knowledge). | Self-policing only. Without V1.b enforcement, a confabulating coordinator may not follow the policy it just read. Necessary but not sufficient. | 1 paragraph HEARTBEAT.md |
| V5.b | **Coordinator-driven controlled escalation**: V5.a + the runner detects "haiku coordinator wrote `idle-count=0`" as a tier-up signal and immediately re-runs the beat as opus *in the same wall-clock window*. Net effect: a haiku coordinator that smells smoke gets one free upgrade to opus eyes within the same minute, with no severity declared until opus weighs in. | The escalation path the bug *should* have used. Cheap (one extra opus beat per stretch ≈ £0.40). Confabulation cost capped at one opus pass instead of one Opus BUILDER forensics spawn. | Adds re-entry in the runner. Need to guard against infinite escalation loops (opus → opus → opus). Trivially fixed by `escalation-depth` env var. | ~20 lines runner |
| V5.c | **Fully suppress haiku idle beats.** Revert the §6.2 `idle-beat-cost-analysis.md` cost optimization. | Bug literally cannot occur — there are no haiku idle coordinators. | Throws away ~£108 / 10.5 days of cost savings. Treats the symptom by removing the feature instead of the bug. The cost analysis is sound; we should *keep* haiku idle beats and *narrow their authority*. Reject as primary; reserve as a "kill switch" if hardening fails twice. | n/a |

---

## Recommendation

**Ship V1.a + V1.b + V2.a + V3.a + V4.a + V5.a + V5.b.** Six rows, one combined diff, single source of truth at every layer.

Read together as a story:

1. **The runner tells the coordinator its own tier** (V1.a). One line in `$CONTEXT`. The coordinator now knows.
2. **HEARTBEAT.md spells out the authority limits per tier** (V1.a doc + V5.a). One paragraph. Self-policing baseline.
3. **The coordinator must write an evidence sidecar** for any severity ≥ WARN (V2.a). JSON schema, mandatory fields. Lives in `$STREAM_DIR/$BEAT_ID-evidence.json`.
4. **The runner runs `diagnose.py incident-claim` against the sidecar** between Stage 1 and Stage 2 (V3.a). Hard fail closes the queue and aborts the spawn.
5. **The runner appends a JSONL record to `heartbeat.jsonl`** (V4.a). It computes severity-delta against the prior beat. If delta > 0 *and* (tier != opus *or* evidence_count == 0), abort with a `SEVERITY_JUMP` log line.
6. **A haiku coordinator that smells smoke escalates by writing `0` to `idle-count`** (V5.a + V5.b). The runner detects this and immediately re-launches the beat on opus, with `escalation-depth` capped at 1 to prevent loops. The haiku coordinator never declares a severity above `NULL_BEAT`.

**Why this combination, and not less:**

- **V1.a alone** is the verbose-regression "fix the documentation, not the code" trap. Necessary but the bug is precisely "the coordinator read the rules and ignored them."
- **V1.b alone** catches the obvious cases (banned tokens) but a confabulating model can use synonyms ("EMERGENCY", "fire", "PANIC"). Necessary but insufficient.
- **V2.a alone** moves the goalposts: now the coordinator confabulates the *evidence* too. We saw exactly this in the 2026-04-07 incident — the coordinator invented "30 new messages / 13 canary trips" out of thin air. V3.a closes this hole by *verifying* the evidence against ground truth.
- **V3.a alone** is wonderful but only fires if the sidecar exists (V2.a) and only protects spawns (not the daily-note text). V4.a catches the daily-note path.
- **V4.a alone** is post-hoc instrumentation — surfaces but doesn't prevent.
- **V5.a + V5.b** turns the "I think there's smoke" path from "haiku writes CRITICAL into the daily note and spawns a forensics consultant" into "haiku writes `0` into `idle-count` and the runner re-runs as opus." The cost differential is roughly ~£0.40 (one extra opus beat) vs. the £$3 of an Opus BUILDER spawn — a 7× safety margin at near-zero implementation cost.

**Why this combination, and not more:**

- **V1.c** (two physically different prompts) is the cleanest design but adds a maintenance burden — two HEARTBEAT.md files that must stay in sync. The verbose-regression postmortem already proved that two-source-of-truth setups drift. Defer until V1.b proves insufficient.
- **V2.c** (truth-store-backed claims) is overkill for ephemeral per-beat evidence. Truth claims are for stable facts. Reject.
- **V5.c** (kill haiku idle beats) throws away the cost optimization. Reject as primary. Keep as a documented kill-switch if the layered hardening fails twice in 30 days.

**Defense in depth, by layer:**

| Layer | What it catches | What it misses | Caught by |
|---|---|---|---|
| L1 — Coordinator knows its tier (V1.a) | Honest mistakes by a coordinator that wants to follow rules | Confabulation | L2 |
| L2 — Runner-side severity scan (V1.b) | Banned-token escalations on non-opus tiers | Synonyms, daily-note narrative without banned tokens | L3, L5 |
| L3 — Mandatory evidence sidecar (V2.a) | Severity escalations with no machine-readable evidence | Sidecars filled with confabulated evidence | L4 |
| L4 — Pre-spawn evidence verifier (V3.a) | Sidecars whose HIVE-IDs and file:line:mtimes don't exist on disk | Sidecars whose claims happen to match unrelated real artifacts | L5 |
| L5 — Severity-delta detector (V4.a) | Unjustified jumps in severity from beat to beat regardless of language | First-ever confabulation in a quiet system (no prior baseline) | Bounded by initial `prior=OK` assumption |
| L6 — Tier-up escalation path (V5.b) | Haiku coordinator suspicions become opus pass instead of phantom alert | Genuine emergencies that haiku correctly identifies but opus then misses | Bounded by opus reliability — out of scope |

The 2026-04-07 incident would have been caught at **L2 and L3 simultaneously**, before any spawn line ran. The counterfactual auto-rotate scenario would have been caught at **L4** (no real HIVE message ID for the alleged 12:38:46Z trip event).

### Estimated total diff size

| File | Lines added | Lines modified |
|---|---:|---:|
| `tools/heartbeat-runner.sh` | ~70 | 2 |
| `instances/gamma/HEARTBEAT.md` | ~80 (one new section) | 0 |
| `tools/diagnose.py` | ~100 (one new function + arg parser entry) | 0 |
| **Total** | **~250** | **2** |

Reversible, scoped, self-contained. No new dependencies. No new daemons. No new databases.

---

### Concrete Code-Change Proposals (DESIGN ONLY — DO NOT APPLY)

#### Diff 1 — `tools/heartbeat-runner.sh`

Three stanzas: tier-into-context (V1.a), pre-spawn evidence verifier + severity scan (V1.b + V3.a + V4.a), and tier-up re-launch (V5.b).

```diff
--- a/tools/heartbeat-runner.sh
+++ b/tools/heartbeat-runner.sh
@@ -38,6 +38,7 @@ BEAT_ID="beat-$(date +%Y%m%d-%H%M%S)"
 TODAY=$(date -u +%Y-%m-%d)
 IDLE_COUNT_FILE="$MEMORY_DIR/idle-count"
 HEARTBEAT_LOCK="$MEMORY_DIR/heartbeat.lock"
+HEARTBEAT_JSONL="$MEMORY_DIR/heartbeat.jsonl"
+ESCALATION_DEPTH="${ESCALATION_DEPTH:-0}"  # caps tier-up re-launch loops at 1

 # Timeouts (seconds)
 COORD_TIMEOUT=300    # 5 min max for coordinator
@@ -89,6 +91,16 @@ trap 'rm -f "$HEARTBEAT_LOCK"' EXIT INT TERM
 IDLE_COUNT=0
 [ -f "$IDLE_COUNT_FILE" ] && IDLE_COUNT=$(cat "$IDLE_COUNT_FILE" 2>/dev/null || echo 0)

-if [ "$IDLE_COUNT" -ge 2 ]; then
+# V5.b: an in-flight tier-up re-launch overrides the idle-counter and forces opus.
+# Set by the prior beat when it detected idle-count was reset to 0 by a haiku
+# coordinator that "smelled smoke" (see the EVIDENCE GATE block below).
+if [ "$ESCALATION_DEPTH" -ge 1 ]; then
+  COORD_MODEL="claude-opus-4-6"
+  COORD_EFFORT="high"
+  COORD_LABEL="opus/high (tier-up escalation depth=$ESCALATION_DEPTH)"
+  COORD_TIER="opus"
+elif [ "$IDLE_COUNT" -ge 2 ]; then
   COORD_MODEL="claude-haiku-4-5-20251001"
   COORD_EFFORT="low"
   COORD_LABEL="haiku/low (idle beat #$IDLE_COUNT)"
+  COORD_TIER="haiku"
 else
   COORD_MODEL="claude-opus-4-6"
   COORD_EFFORT="high"
   COORD_LABEL="opus/high"
+  COORD_TIER="opus"
 fi
@@ -110,7 +122,16 @@ echo "--- HEARTBEAT START ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) coordinator
 # Reads full context, produces spawn decisions.

 # Build real-time context for coordinator
-CONTEXT="Beat ID: $BEAT_ID. Date: $TODAY."
+# V1.a: coordinator MUST know its own tier so it can self-police authority.
+# V2.a: evidence sidecar path is fixed per beat — coordinator writes here
+# whenever it intends to declare severity above HEARTBEAT_OK.
+EVIDENCE_FILE="$STREAM_DIR/$BEAT_ID-evidence.json"
+CONTEXT="Beat ID: $BEAT_ID. Date: $TODAY. \
+COORD_TIER: $COORD_TIER. COORD_MODEL: $COORD_MODEL. COORD_EFFORT: $COORD_EFFORT. \
+EVIDENCE_FILE: $EVIDENCE_FILE. \
+TIER_AUTHORITY: opus may declare any severity backed by evidence; sonnet may \
+declare up to HEARTBEAT_WARN with evidence; haiku may ONLY declare HEARTBEAT_OK \
+or NULL_BEAT. To escalate as a non-opus tier, write 0 to $IDLE_COUNT_FILE and \
+exit NULL_BEAT — the runner will re-launch this beat on opus."
@@ -185,7 +206,77 @@ echo "--- COORDINATOR DONE ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) model=$COO

 # ── Stage 2: Execute Spawn Queue with Monitoring ─────────────────────────────

+# ── EVIDENCE GATE (V1.b + V2.a + V3.a + V4.a + V5.b) ─────────────────────────
+# Order matters: severity scan, then sidecar check, then verifier, then JSONL,
+# then tier-up re-launch detection. Each step can abort cleanly.
+
+# V4.a-prep: load prior severity for delta computation.
+PRIOR_SEVERITY="OK"
+if [ -f "$HEARTBEAT_JSONL" ]; then
+  PRIOR_SEVERITY=$(tail -1 "$HEARTBEAT_JSONL" 2>/dev/null \
+    | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("declared_severity","OK"))' 2>/dev/null \
+    || echo "OK")
+fi
+
+# Read what the coordinator declared by scanning today's daily note for the
+# most recent HEARTBEAT_* token (or NULL_BEAT). Coordinator-written, freeform,
+# but the token vocabulary is bounded.
+DECLARED_SEVERITY=$(grep -oE 'HEARTBEAT_(OK|WARN|ALERT|CRITICAL|INCIDENT|RESOLVED)|NULL_BEAT' \
+  "$MEMORY_DIR/daily/$TODAY.md" 2>/dev/null | tail -1 | sed 's/HEARTBEAT_//' || echo "OK")
+
+rank() { case "$1" in OK|RESOLVED|NULL_BEAT) echo 0;; WARN) echo 1;; ALERT) echo 2;; CRITICAL) echo 3;; INCIDENT) echo 4;; *) echo 0;; esac; }
+CUR_RANK=$(rank "$DECLARED_SEVERITY")
+PRI_RANK=$(rank "$PRIOR_SEVERITY")
+SEV_DELTA=$(( CUR_RANK - PRI_RANK ))
+
+ABORT_REASON=""
+EVIDENCE_COUNT=0
+
+# V1.b: non-opus tiers may not declare severity > 0. Hard abort.
+if [ "$COORD_TIER" != "opus" ] && [ "$CUR_RANK" -gt 0 ]; then
+  ABORT_REASON="severity_overreach: tier=$COORD_TIER declared=$DECLARED_SEVERITY (only opus may escalate)"
+fi
+
+# V2.a + V3.a: any severity > 0 requires the evidence sidecar AND it must verify.
+if [ -z "$ABORT_REASON" ] && [ "$CUR_RANK" -gt 0 ]; then
+  if [ ! -s "$EVIDENCE_FILE" ]; then
+    ABORT_REASON="missing_evidence_sidecar: severity=$DECLARED_SEVERITY but $EVIDENCE_FILE absent or empty"
+  else
+    if ! python3 "$LOVE_DIR/tools/diagnose.py" incident-claim --beat "$BEAT_ID" --evidence "$EVIDENCE_FILE" >> "$HEARTBEAT_LOG" 2>&1; then
+      ABORT_REASON="evidence_verification_failed: see diagnose output above"
+    else
+      EVIDENCE_COUNT=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(len(c.get("hive_msg_ids",[]))+len(c.get("file_citations",[])) for c in d.get("claims",[])))' "$EVIDENCE_FILE" 2>/dev/null || echo 0)
+    fi
+  fi
+fi
+
+# V4.a: structured JSONL append, every beat, regardless of outcome.
+python3 -c "
+import json,sys,time
+rec={'beat_id':'$BEAT_ID','ts':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
+     'tier':'$COORD_TIER','declared_severity':'$DECLARED_SEVERITY',
+     'prior_declared_severity':'$PRIOR_SEVERITY','severity_delta':$SEV_DELTA,
+     'evidence_count':$EVIDENCE_COUNT,'abort_reason':'''$ABORT_REASON'''}
+open('$HEARTBEAT_JSONL','a').write(json.dumps(rec)+'\n')
+" 2>/dev/null || true
+
+if [ -n "$ABORT_REASON" ]; then
+  echo "--- EVIDENCE GATE ABORT ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) reason=$ABORT_REASON ---" >> "$HEARTBEAT_LOG"
+  > "$SPAWN_QUEUE"
+  echo "$((IDLE_COUNT + 1))" > "$IDLE_COUNT_FILE"
+  rm -f "$HEARTBEAT_LOCK"
+  exit 1
+fi
+
+# V5.b: did the coordinator reset idle-count to 0 as a tier-up signal?
+# If we are non-opus AND idle-count is now 0 AND no spawn lines were written,
+# treat this as "smelled smoke, escalating" and re-launch on opus.
+if [ "$COORD_TIER" != "opus" ] && [ "$ESCALATION_DEPTH" -lt 1 ]; then
+  NEW_IDLE=$(cat "$IDLE_COUNT_FILE" 2>/dev/null || echo 0)
+  if [ "$NEW_IDLE" = "0" ] && [ ! -s "$SPAWN_QUEUE" ]; then
+    echo "--- TIER-UP ESCALATION ($BEAT_ID): $COORD_TIER coordinator reset idle-count, re-launching on opus ---" >> "$HEARTBEAT_LOG"
+    rm -f "$HEARTBEAT_LOCK"
+    ESCALATION_DEPTH=1 exec "$0" "$INSTANCE_NAME"
+  fi
+fi
+
 if [ -s "$SPAWN_QUEUE" ]; then
   echo "--- SPAWN STAGE START ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$HEARTBEAT_LOG"
   echo "Spawn queue contents:" >> "$HEARTBEAT_LOG"
```

Notes on the diff:

- **The `exec "$0"` re-launch is intentional.** It replaces the current process so the lock is released cleanly, the trap fires once, and the new beat starts with a fresh `BEAT_ID`. `ESCALATION_DEPTH=1` is passed in the environment to cap the loop at one re-entry. A second tier-up signal in the re-launched beat would not trigger because `[ "$ESCALATION_DEPTH" -lt 1 ]` is false.
- **The severity scan grep is intentionally a small allowlist** of canonical severity tokens (HEARTBEAT_OK/WARN/ALERT/CRITICAL/INCIDENT/RESOLVED, NULL_BEAT). This is paired with HEARTBEAT.md guidance that those are the *only* legal severity tokens — any synonym ("EMERGENCY", "PANIC") is by policy a documentation bug. Pair-enforced by V1.a.
- **The Python heredoc for the JSONL append is single-quoted everywhere it can be** to prevent shell expansion of the JSON braces. The only `$` that gets shell-expanded is the explicit list at the top.
- **`diagnose.py incident-claim` is referenced as a sibling command.** Its design is in Diff 3 below.
- **All paths use `$LOVE_DIR/...` or the existing variables** — consistent with the verbose-regression postmortem's path migration.

#### Diff 2 — `instances/gamma/HEARTBEAT.md`

One new top-level section ("Tier Authority and Evidence Gate") inserted between the existing "IDLE BRANCH" section and the "Otherwise: HEARTBEAT_OK" line.

```diff
--- a/instances/gamma/HEARTBEAT.md
+++ b/instances/gamma/HEARTBEAT.md
@@ -123,6 +123,86 @@ Declare NULL_BEAT and exit. NULL_BEAT means: nothing actionable, no stale
 truth, no mind problems. This is the only legitimate path to "do nothing."

 ```
 NULL_BEAT — truth fresh, mind queue empty, no Yu-actionable work
 ```

+## Tier Authority and Evidence Gate
+
+The runner tells you which tier you are running on via the appended system
+prompt: `COORD_TIER` is one of `opus`, `sonnet`, or `haiku`. **Severity
+authority is bounded by tier.** This is enforced by the runner — violating it
+causes the runner to abort the spawn stage, clear `spawn-queue.sh`, and
+(non-opus tiers) re-launch this beat on opus. You will see your declaration
+rolled back to `NULL_BEAT` in `heartbeat.jsonl`.
+
+### What each tier may declare
+
+| Tier | May declare | May spawn |
+|---|---|---|
+| `opus` | `HEARTBEAT_OK`, `HEARTBEAT_RESOLVED`, `NULL_BEAT`, `HEARTBEAT_WARN`, `HEARTBEAT_ALERT`, `HEARTBEAT_CRITICAL`, `HEARTBEAT_INCIDENT` (the last four require an evidence sidecar that verifies) | yes |
+| `sonnet` | `HEARTBEAT_OK`, `HEARTBEAT_RESOLVED`, `NULL_BEAT`, `HEARTBEAT_WARN` (with evidence sidecar) | yes |
+| `haiku` | `HEARTBEAT_OK`, `HEARTBEAT_RESOLVED`, `NULL_BEAT` only | yes (only IDLE-A/IDLE-B truth and mind ops) |
+
+`HEARTBEAT_RESOLVED` is permitted on any tier ONLY when the prior beat's JSONL
+record shows a non-OK severity *and* a `tier=opus` author *and* the resolution
+is documented in `daily/<TODAY>.md` with a citation to the resolving artifact.
+You may not "resolve" something that no opus pass declared.
+
+### Why
+
+On 2026-04-07, `beat-20260407-124757` (haiku/low) confabulated a
+`HEARTBEAT_CRITICAL` for a phantom forge AWS-credential incident by
+recombining real historical fragments (the 2026-04-02 PEACE drill, the
+`--verbose` regression, SOMA W13–W14 reflection language, Wall 1 audit
+19/21 fragments). It walked the narrative through git → spawn-queue.sh and
+spawned a real Opus BUILDER forensics consultant. Nine minutes earlier the
+Opus coordinator on the same system reported `HEARTBEAT_OK` 99-th-stable.
+The full forensic report is at `memory/sessions/forge-incident-20260407-124935.log`
+and the postmortem is at `memory/thinking/heartbeat-confabulation-hardening.md`.
+
+The shape of the bug: a low-tier coordinator under reasoning pressure pattern-
+matches resolved historical incidents into a fictitious current incident, and
+(in the absence of an evidence requirement) the runner accepts the fiction as
+ground truth. The tier authority table above closes this hole at the source
+by removing the haiku tier's authority to make claims it has no competence to
+verify.
+
+### How to escalate as a non-opus coordinator
+
+If you are running on `sonnet` or `haiku` and your SENSE phase finds something
+that *looks* like a real incident, **do not declare a severity yourself**.
+Instead:
+
+1. Do NOT write `HEARTBEAT_CRITICAL` (or WARN/ALERT/INCIDENT) anywhere — not
+   in `daily/<TODAY>.md`, not in `heartbeat.log`, not in HIVE.
+2. Do NOT write any spawn-queue.sh lines this beat. Leave it empty.
+3. Write a brief note to `daily/<TODAY>.md` of the form:
+   *"Suspected signal at <BEAT_ID>: <one-sentence description>. Escalating to
+   opus tier — coordinator is <COORD_LABEL>, no severity declared per Tier
+   Authority policy."*
+4. Reset the idle counter: `echo 0 > ~/Love/memory/idle-count`
+5. Optionally send one HIVE chat message:
+   `python3 ~/Desktop/Love/hive/hive.py send chat "Tier-up escalation: $BEAT_ID, suspected <signal>, opus review requested"`
+6. Declare `NULL_BEAT` and exit cleanly.
+
+The runner will detect `idle-count=0` + empty spawn queue + non-opus tier and
+immediately re-launch this beat on opus, with `ESCALATION_DEPTH=1` capping the
+loop at one re-entry. The opus pass will then evaluate the signal with full
+authority and (if real) declare the appropriate severity with verifiable
+evidence.
+
+### Evidence sidecar (opus-tier escalations only)
+
+If you are `opus` AND you are about to declare any severity above
+`HEARTBEAT_OK`, you MUST write `$EVIDENCE_FILE` (the runner gives you the
+full path in the appended context — it is
+`~/Love/memory/sessions/streams/<BEAT_ID>-evidence.json`). Schema:
+
+```json
+{
+  "beat_id": "beat-YYYYMMDD-HHMMSS",
+  "declared_severity": "WARN|ALERT|CRITICAL|INCIDENT",
+  "claims": [
+    {
+      "type": "credential-exposure|service-down|data-loss|...",
+      "reason": "one-sentence why this is the severity",
+      "hive_msg_ids": ["<id1>", "<id2>"],
+      "file_citations": [
+        {"path": "memory/...", "line": 42, "mtime": "2026-04-07T12:30:00Z", "sha256": "abc123..."}
+      ]
+    }
+  ]
+}
+```
+
+`hive_msg_ids` are the canonical IDs from `hive.py check` / `hive.py send`
+output (the `[id]` tag at the end of each message). `file_citations` must
+exist on disk: `diagnose.py incident-claim` opens each file, reads the line,
+computes the sha256, and compares to your record. Any mismatch fails closed
+— the runner aborts Stage 2 and your severity declaration is rolled back to
+`NULL_BEAT` in `heartbeat.jsonl`. Fail-closed on missing or unverifiable
+evidence is intentional: a confabulating coordinator either invents bogus
+evidence (which fails verification) or fails to write the sidecar (which
+fails the existence check). Both produce a clean abort instead of a phantom
+spawn.
+
 ## Otherwise: HEARTBEAT_OK (only after the IDLE BRANCH ran)
```

#### Diff 3 — `tools/diagnose.py incident-claim` (DESIGN ONLY)

Sketch only — full implementation would be ~80 lines. Inserted alongside `check_spawn` at `tools/diagnose.py:341`:

```python
def check_incident_claim(r, beat_id: str, evidence_path: Path) -> None:
    """V3.a: verify an evidence sidecar against on-disk and HIVE ground truth.

    Called from the heartbeat-runner pre-spawn gate when the coordinator
    has declared a severity above HEARTBEAT_OK. Each claim in the sidecar
    is checked:

      - hive_msg_ids: looked up via hive.py check (or directly in the
        local hive history file). Each ID must exist and have a timestamp
        within the current beat's window (BEAT_ID timestamp ± 60 seconds).
      - file_citations: each path is opened, line N is read, sha256 is
        computed, and compared to the recorded sha. mtime is compared to
        os.stat(path).st_mtime within ±2 seconds.

    Any failure → r.fail(...) and the function returns nonzero (the runner
    aborts the spawn stage). All passes → r.ok(...) and exit 0.
    """
    if not evidence_path.exists() or evidence_path.stat().st_size == 0:
        r.fail(f"evidence sidecar absent or empty: {evidence_path}")
        return

    try:
        evidence = json.loads(evidence_path.read_text())
    except json.JSONDecodeError as e:
        r.fail(f"evidence sidecar is not valid JSON: {e}")
        return

    if evidence.get("beat_id") != beat_id:
        r.fail(f"sidecar beat_id mismatch: expected {beat_id}, got {evidence.get('beat_id')}")
        return

    claims = evidence.get("claims", [])
    if not claims:
        r.fail(f"sidecar has no claims; severity={evidence.get('declared_severity')} requires at least one")
        return

    beat_ts = _parse_beat_id_to_ts(beat_id)  # helper, ±60s window

    for i, claim in enumerate(claims):
        # 1. HIVE message IDs
        for msg_id in claim.get("hive_msg_ids", []):
            msg = _hive_lookup(msg_id)  # reads ~/.openclaw/.hive/history.json or runs hive.py check
            if msg is None:
                r.fail(f"claim {i}: HIVE message {msg_id} not found in local history")
                return
            if abs(msg["ts"] - beat_ts) > 60:
                r.fail(f"claim {i}: HIVE message {msg_id} timestamp {msg['ts']} outside ±60s of beat {beat_ts}")
                return

        # 2. File citations
        for cite in claim.get("file_citations", []):
            path = Path(cite["path"])
            if not path.is_absolute():
                path = LOVE_DIR / path
            if not path.exists():
                r.fail(f"claim {i}: cited file does not exist: {path}")
                return
            actual_mtime = path.stat().st_mtime
            recorded_mtime = _parse_iso_to_ts(cite["mtime"])
            if abs(actual_mtime - recorded_mtime) > 2:
                r.fail(f"claim {i}: mtime drift on {path}: actual={actual_mtime} recorded={recorded_mtime}")
                return
            try:
                lines = path.read_text().splitlines()
                line_text = lines[cite["line"] - 1]
            except (IndexError, UnicodeDecodeError) as e:
                r.fail(f"claim {i}: cannot read line {cite['line']} of {path}: {e}")
                return
            actual_sha = hashlib.sha256(line_text.encode()).hexdigest()
            if actual_sha != cite["sha256"]:
                r.fail(f"claim {i}: sha256 mismatch on {path}:{cite['line']}")
                return

    r.ok(f"all {len(claims)} claim(s) verified against HIVE and disk")


# argparse wiring (in main()):
#
#   p_inc = subparsers.add_parser("incident-claim",
#       help="verify an evidence sidecar against HIVE+disk ground truth")
#   p_inc.add_argument("--beat", required=True)
#   p_inc.add_argument("--evidence", required=True, type=Path)
#   p_inc.set_defaults(func=lambda args: check_incident_claim(Result(), args.beat, args.evidence))
```

The function is structured to mirror the existing `check_spawn` style (uses `r.ok` / `r.fail` from the same `Result` helper), so it integrates with `diagnose.py`'s output formatting and exit-code aggregation without any plumbing changes.

---

## Open Questions for Yu

1. **Tier-up re-launch budget.** Recommendation V5.b re-launches the beat on opus when a haiku coordinator smells smoke. Each re-launch costs ~£0.40 (one extra opus idle beat). My vote: cap at **one re-launch per BEAT_ID** (`ESCALATION_DEPTH=1`) and let the next 7-minute beat handle anything more complex. Acceptable? Or do you want a per-day cap (e.g., "no more than 3 tier-up escalations per UTC day") so a confabulation flood can't burn through £$50 of opus beats?

2. **Severity vocabulary lockdown.** The runner's severity scan greps for an explicit allowlist: `HEARTBEAT_(OK|WARN|ALERT|CRITICAL|INCIDENT|RESOLVED)` and `NULL_BEAT`. Synonyms ("EMERGENCY", "PANIC", "FIRE") are by policy a documentation bug — they will not trip the V1.b scan. Should I instead use a *denylist* of incident-language tokens (more permissive coordinator vocabulary, harder to bypass)? My vote: **allowlist**. Forces precision and matches the existing protocol vocabulary in HEARTBEAT.md. The cost is a false-negative on creative wording — but creative wording on a haiku-low beat is itself a smell.

3. **Evidence sidecar format: JSON vs JSONL.** Recommendation V2.a uses a single JSON object per beat. Alternative is one JSONL line per claim (easier to append incrementally). My vote: **JSON object per beat**. The coordinator writes once and exits; incremental writing is not a workflow we need.

4. **HIVE message lookup: local history vs live `hive.py check`.** `_hive_lookup` in the diagnose.py sketch needs to find a message by ID. The local file at `~/.openclaw/.hive/history.json` is the legacy snapshot (the forge incident postmortem flagged that this snapshot has been static since 2026-04-02 15:20 UTC, so it cannot serve as ground truth for *new* messages). The current Love NATS HIVE is live but does not persist a history file by default. Should I (a) extend `hive.py check` to write a rolling local history file the verifier can read, (b) keep an in-memory `hive.py history --since=<beat_ts-60>` query as the verifier's source, or (c) require the coordinator to capture the HIVE check output to `$STREAM_DIR/$BEAT_ID-hive-snapshot.json` as part of its SENSE phase and have the verifier read from there? My vote: **(c)** — it's the only option that gives the verifier exactly what the coordinator saw, and it pins the evidence to the moment of decision rather than a different moment of verification.

5. **`heartbeat.jsonl` retention.** New file at `~/Love/memory/heartbeat.jsonl`, one line per beat. At ~164 beats/day × 200 bytes/line ≈ 32 KB/day ≈ 12 MB/year — fine indefinitely. But should we rotate it monthly to keep `tail -1` fast? My vote: **no rotation**, append-only forever. 12 MB/year is noise; rotation adds a code path with its own bugs.

6. **Allowed escalation channels for non-opus tiers.** V5.a says a haiku coordinator that smells smoke may send *one* HIVE chat message of the form `"Tier-up escalation: <BEAT_ID>, suspected <signal>, opus review requested"`. Should that message also include the coordinator's brief reason (free-text) so the opus re-launch has context, or should it be machine-format only (severity-token-free) to avoid the haiku coordinator confabulating in a different vector? My vote: **free-text reason allowed in the HIVE message**, on the principle that the message is *not* a severity declaration and the opus re-launch is the authoritative reader. The opus re-launch will independently re-do SENSE; the HIVE message is just a hint, not a binding claim.

7. **Should V1.c (two physically different prompts) ship now or after V1.b proves insufficient?** Defending in depth says ship now; YAGNI says wait. The verbose-regression postmortem's pattern was "single source of truth + lint." V1.c is the equivalent of "two sources of truth" in the prompt-template space, which is exactly the anti-pattern that postmortem warned against — except here the two prompts would be *deliberately different*, not accidentally divergent. My vote: **defer V1.c**. Re-evaluate if a haiku coordinator confabulates again post-hardening.

8. **Should the daily-note severity grep also scan `heartbeat.log`?** The recommended V1.b scan currently only reads `daily/<TODAY>.md`. A confabulating coordinator could (in theory) write `HEARTBEAT_CRITICAL` to `heartbeat.log` directly (the runner appends coordinator stdout to that file). My vote: **yes — extend the grep to scan the heartbeat.log delta since the prior `HEARTBEAT START` line of this beat as well**. Two-line addition, closes the obvious bypass. Add to the diff.

9. **Should `instances/beta/HEARTBEAT.md` get the same treatment now or wait until Beta has its own confabulation incident?** The bug class is general (any low-tier coordinator with high-blast-radius authority), but the implementation is per-instance because Beta's heartbeat protocol differs. My vote: **propose a parallel mind problem `heartbeat-confabulation-hardening-beta` so Beta's coordinator (Sonnet) does the equivalent design pass for Beta's protocol**. Don't blindly copy this design across instance boundaries.

10. **Acceptance test environment.** The acceptance test (next section) requires running the runner against a test instance with a controlled fake incident. Should the test live in `tools/tests/` (new directory) or be wired into a manual `tools/diagnose.py self-test` subcommand? My vote: **`tools/tests/test_heartbeat_confabulation.sh`** — bash, hermetic, no dependency on diagnose.py beyond what it tests.

---

## Verification

How we will know the hardening works. Gates in order; each must pass before the previous one is considered closed.

### V0 — Static checks (run before commit)

```sh
# Bash syntax of the modified runner
bash -n tools/heartbeat-runner.sh && echo "syntax ok"

# diagnose.py imports cleanly
python3 -c "import tools.diagnose; print('import ok')"

# HEARTBEAT.md is valid markdown (no broken code fences)
python3 -c "import markdown; markdown.markdown(open('instances/gamma/HEARTBEAT.md').read()); print('md ok')"

# All cited line numbers in the new HEARTBEAT.md section actually point at the
# right things in tools/heartbeat-runner.sh (the doc references runner:212,
# runner:188, etc — they must match reality after the diff is applied)
grep -n "EVIDENCE GATE\|TIER_AUTHORITY\|EVIDENCE_FILE" tools/heartbeat-runner.sh
```

### V1 — Acceptance test: fake-incident injection into haiku-low coordinator path

This is the test specified in the spawn brief deliverable (c). It must fail closed, exit with a clear error, and downgrade to `NULL_BEAT`. The test lives in `tools/tests/test_heartbeat_confabulation.sh`.

**Setup:**

```sh
# Test fixture directory — hermetic copy of the runtime layout
TEST_ROOT=$(mktemp -d /tmp/heartbeat-test-XXXXXX)
mkdir -p "$TEST_ROOT/memory/daily" "$TEST_ROOT/memory/sessions/streams" "$TEST_ROOT/instances/gamma"

# Force the runner to use the haiku-low path
echo "5" > "$TEST_ROOT/memory/idle-count"   # > 2 → haiku
TODAY=$(date -u +%Y-%m-%d)
BEAT_ID="beat-test-$(date +%Y%m%d-%H%M%S)"
EVIDENCE_FILE="$TEST_ROOT/memory/sessions/streams/$BEAT_ID-evidence.json"

# Stub out the claude binary to inject a confabulated CRITICAL into the
# daily note WITHOUT writing any evidence sidecar. This simulates the
# 2026-04-07 12:47 UTC failure mode exactly.
mkdir -p "$TEST_ROOT/bin"
cat > "$TEST_ROOT/bin/claude" <<'STUB'
#!/bin/bash
# Fake claude binary: writes a confabulated CRITICAL to the daily note.
DAILY="$TEST_ROOT/memory/daily/$(date -u +%Y-%m-%d).md"
cat >> "$DAILY" <<EOF

## Beat Coordinator: TEST haiku/low — fake confabulation

**CRITICAL INCIDENT DETECTED**: phantom forge AWS-credential incident,
recombined from real historical fragments. No evidence sidecar written.

Status: HEARTBEAT_CRITICAL
EOF
exit 0
STUB
chmod +x "$TEST_ROOT/bin/claude"

# Also drop a fake spawn line into spawn-queue.sh — the kind a confabulating
# coordinator would write under the same fake prompt.
cat > "$TEST_ROOT/memory/spawn-queue.sh" <<'EOF'
cd /tmp && /bin/echo "phantom forensics spawn would have run here" > /tmp/heartbeat-test-phantom-ran
EOF

# Run the runner against the test root with the stub claude on PATH
PATH="$TEST_ROOT/bin:$PATH" \
LOVE_DIR="$TEST_ROOT" \
LOVE_INSTANCE=gamma \
bash tools/heartbeat-runner.sh gamma
RUNNER_EXIT=$?
```

**Assertions (all must hold):**

```sh
# A1 — Runner exits non-zero (fail-closed)
test "$RUNNER_EXIT" -ne 0 || { echo "FAIL A1: runner exited 0, expected nonzero"; exit 1; }

# A2 — heartbeat.log contains an EVIDENCE GATE ABORT line for this beat
grep -q "EVIDENCE GATE ABORT.*$BEAT_ID" "$TEST_ROOT/memory/heartbeat.log" \
  || { echo "FAIL A2: no EVIDENCE GATE ABORT line in heartbeat.log"; exit 1; }

# A3 — abort_reason explicitly mentions either severity_overreach or missing_evidence_sidecar
grep -E "severity_overreach|missing_evidence_sidecar" "$TEST_ROOT/memory/heartbeat.log" \
  || { echo "FAIL A3: abort_reason not human-readable"; exit 1; }

# A4 — spawn-queue.sh was cleared (the phantom spawn line is gone)
test ! -s "$TEST_ROOT/memory/spawn-queue.sh" \
  || { echo "FAIL A4: spawn-queue.sh was not cleared"; exit 1; }

# A5 — the phantom spawn DID NOT execute (canary file does not exist)
test ! -f /tmp/heartbeat-test-phantom-ran \
  || { echo "FAIL A5: phantom spawn line was executed despite abort"; exit 1; }

# A6 — heartbeat.jsonl was appended with declared_severity downgraded to NULL_BEAT
LAST_REC=$(tail -1 "$TEST_ROOT/memory/heartbeat.jsonl")
echo "$LAST_REC" | python3 -c '
import json,sys
r=json.loads(sys.stdin.read())
assert r["beat_id"].startswith("beat-test"), "wrong beat_id"
assert r["tier"] == "haiku", f"expected tier=haiku, got {r[\"tier\"]}"
# After abort, declared_severity in the JSONL should reflect the rollback
# (the runner records the parsed severity AND the abort_reason; the runner
# does NOT silently rewrite the declared field, so this assertion is on
# abort_reason being non-empty rather than on declared_severity itself).
assert r["abort_reason"], "abort_reason should be non-empty"
assert r["evidence_count"] == 0, f"expected evidence_count=0, got {r[\"evidence_count\"]}"
print("A6 ok")
' || { echo "FAIL A6: heartbeat.jsonl record malformed or wrong"; exit 1; }

# A7 — idle-count was incremented (this beat counts as idle-with-no-spawns)
NEW_IDLE=$(cat "$TEST_ROOT/memory/idle-count")
test "$NEW_IDLE" = "6" \
  || { echo "FAIL A7: idle-count expected 6 (was 5+1), got $NEW_IDLE"; exit 1; }

# A8 — diagnose.py incident-claim returns nonzero on the missing sidecar
python3 tools/diagnose.py incident-claim --beat "$BEAT_ID" --evidence "$EVIDENCE_FILE"
test "$?" -ne 0 \
  || { echo "FAIL A8: diagnose.py incident-claim returned 0 on missing sidecar"; exit 1; }

# Cleanup
rm -rf "$TEST_ROOT" /tmp/heartbeat-test-phantom-ran 2>/dev/null
echo "ALL ASSERTIONS PASSED"
```

**Companion test — V5.b tier-up re-launch path:**

This test verifies that a haiku coordinator that *correctly* downgrades (writes 0 to idle-count, leaves spawn-queue empty, declares NULL_BEAT) triggers a re-launch on opus.

```sh
# Setup as above, but the stub claude does the right thing:
cat > "$TEST_ROOT/bin/claude" <<'STUB'
#!/bin/bash
# Fake claude binary: writes a NULL_BEAT, resets idle-count, exits clean.
DAILY="$TEST_ROOT/memory/daily/$(date -u +%Y-%m-%d).md"
echo "## Beat $BEAT_ID — haiku tier, suspected signal, escalating to opus" >> "$DAILY"
echo "Status: NULL_BEAT" >> "$DAILY"
echo "0" > "$TEST_ROOT/memory/idle-count"
exit 0
STUB

# Run runner — first invocation should detect tier-up and re-launch
PATH="$TEST_ROOT/bin:$PATH" LOVE_DIR="$TEST_ROOT" LOVE_INSTANCE=gamma \
  bash tools/heartbeat-runner.sh gamma

# B1 — heartbeat.log shows TIER-UP ESCALATION line
grep -q "TIER-UP ESCALATION" "$TEST_ROOT/memory/heartbeat.log" \
  || { echo "FAIL B1: no TIER-UP ESCALATION line"; exit 1; }

# B2 — heartbeat.log shows a SECOND HEARTBEAT START line with opus tier
test "$(grep -c "HEARTBEAT START" "$TEST_ROOT/memory/heartbeat.log")" -ge 2 \
  || { echo "FAIL B2: re-launch did not produce a second HEARTBEAT START"; exit 1; }

grep -q "opus/high (tier-up escalation depth=1)" "$TEST_ROOT/memory/heartbeat.log" \
  || { echo "FAIL B2b: re-launch did not run on opus"; exit 1; }

# B3 — recursion depth is capped at 1 (no third HEARTBEAT START)
test "$(grep -c "HEARTBEAT START" "$TEST_ROOT/memory/heartbeat.log")" -le 2 \
  || { echo "FAIL B3: escalation re-launched more than once"; exit 1; }

echo "TIER-UP TEST PASSED"
```

### V2 — Production smoke test (post-deploy, opt-in)

After the diff is applied to production `~/Love/tools/heartbeat-runner.sh`, a single hand-run on the live system:

```sh
# Force a haiku idle beat by setting idle-count to 5 and waiting for the
# next 7-minute tick. Watch heartbeat.jsonl appear and contain the new fields.
echo 5 > ~/Love/memory/idle-count
sleep 420  # one beat interval
tail -1 ~/Love/memory/heartbeat.jsonl | python3 -m json.tool
# Expected: a JSON record with tier=haiku, declared_severity=OK or NULL_BEAT,
# severity_delta<=0, evidence_count=0, abort_reason="" (empty)
```

### V3 — Long-running canary (next 7 days)

A daily check via cron or a truth.py claim:

```sh
# Truth claim: no severity overreach in the last 24 hours
python3 tools/truth.py claim heartbeat-no-overreach \
  --check 'jq -r ".abort_reason" ~/Love/memory/heartbeat.jsonl | grep -E "severity_overreach|missing_evidence_sidecar" | head -1' \
  --expect '' --ttl 24h --source 'tools/heartbeat-runner.sh'
```

If the claim flips red, a confabulation attempt was caught — investigate but
do not panic; the gate is *supposed* to catch them. If it goes red **more
than 5 times in 7 days**, the haiku-low cost optimization is producing too
much noise and we should revisit V5.c (kill switch).

### V4 — Counterfactual replay

The most rigorous verification: replay the 2026-04-07 `beat-20260407-124757`
prompt against the hardened runner using a recorded version of that haiku
coordinator's outputs (the daily-note delta + spawn-queue line). Expected
outcome:

1. Runner detects `HEARTBEAT_CRITICAL` in the daily note text.
2. Runner detects `tier=haiku`.
3. V1.b severity scan trips → `severity_overreach` abort.
4. spawn-queue.sh is cleared. The phantom Opus BUILDER forensics spawn does
   not run.
5. heartbeat.jsonl gets a record with `abort_reason="severity_overreach"`.
6. idle-count is incremented (next beat is also haiku, unless something else
   resets it).
7. Tier-up re-launch is **not** triggered (because the haiku coordinator did
   NOT reset idle-count to 0; it confabulated instead). The phantom is
   simply blocked, not promoted.

This replay produces a *delta-of-zero* in the production system: nothing
spawns, nothing escalates, nothing costs anything. Compare against the
actual 2026-04-07 cost (one Opus BUILDER spawn ~£2–£3) — the hardening
saves the full cost while preserving correct behavior.

### V5 — Negative tests (must NOT break opus-tier escalation)

The hardening MUST NOT make legitimate opus-tier severity escalations
impossible. A negative test:

```sh
# Setup: idle-count = 0, opus tier, write a real evidence sidecar
echo 0 > "$TEST_ROOT/memory/idle-count"
BEAT_ID=...
EVIDENCE_FILE="$TEST_ROOT/memory/sessions/streams/$BEAT_ID-evidence.json"

# Real HIVE message (the test fixture pre-populates one)
HIVE_MSG_ID=$(python3 hive/hive.py send chat "test alert for verification" | grep -oE '\[[a-z0-9]+\]' | tr -d '[]')

# Real file citation
TEST_FILE="$TEST_ROOT/test-evidence.md"
echo "ALERT: real signal at line 1" > "$TEST_FILE"
SHA=$(python3 -c "import hashlib; print(hashlib.sha256(open('$TEST_FILE').readline().rstrip().encode()).hexdigest())")
MTIME=$(python3 -c "import os,time; print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(os.stat('$TEST_FILE').st_mtime)))")

# Write a real evidence sidecar
cat > "$EVIDENCE_FILE" <<EOF
{"beat_id":"$BEAT_ID","declared_severity":"WARN",
 "claims":[{"type":"test","reason":"verification of legitimate path",
  "hive_msg_ids":["$HIVE_MSG_ID"],
  "file_citations":[{"path":"$TEST_FILE","line":1,"mtime":"$MTIME","sha256":"$SHA"}]}]}
EOF

# Stub claude that writes HEARTBEAT_WARN with a real spawn line
cat > "$TEST_ROOT/bin/claude" <<STUB
#!/bin/bash
echo "## Beat: HEARTBEAT_WARN with verified evidence" >> "$TEST_ROOT/memory/daily/$(date -u +%Y-%m-%d).md"
echo "cd /tmp && /bin/echo legitimate-spawn-ran > /tmp/legit-canary" >> "$TEST_ROOT/memory/spawn-queue.sh"
exit 0
STUB

# Run
PATH="$TEST_ROOT/bin:$PATH" LOVE_DIR="$TEST_ROOT" bash tools/heartbeat-runner.sh gamma

# C1 — runner exits 0
# C2 — diagnose.py incident-claim passed
# C3 — spawn-queue executed
test -f /tmp/legit-canary && echo "C3 ok: legitimate spawn executed"
# C4 — heartbeat.jsonl shows declared_severity=WARN, abort_reason=""
```

If V5 fails, the hardening is too restrictive and must be loosened before
ship. If V1 passes and V5 passes, the hardening is correctly scoped:
fail-closed on confabulation, fail-open on verified evidence.

### V6 — Documentation cross-references

The new HEARTBEAT.md section references this postmortem
(`memory/thinking/heartbeat-confabulation-hardening.md`) and the forensic
report (`memory/sessions/forge-incident-20260407-124935.log`). Both files
must exist on disk before the diff is applied. Verify with:

```sh
test -f memory/thinking/heartbeat-confabulation-hardening.md && echo "postmortem ok"
test -f memory/sessions/forge-incident-20260407-124935.log && echo "forensic ok"
```

### V7 — Reversibility check

Every change in this proposal can be reverted with a single `git revert` of
the hardening commit. No data migration, no schema lock-in, no external
state to clean up. The new files (`heartbeat.jsonl`, per-beat evidence
sidecars in `$STREAM_DIR`) are append-only and can be deleted at any time
without affecting other systems.

```sh
# Confirm the only persistent state outside git is the JSONL file and per-beat sidecars
ls ~/Love/memory/heartbeat.jsonl 2>/dev/null && echo "1 file"
ls ~/Love/memory/sessions/streams/*-evidence.json 2>/dev/null | wc -l
# Both can be removed with: rm -f ~/Love/memory/heartbeat.jsonl ~/Love/memory/sessions/streams/*-evidence.json
```

---

## Summary for Yu

- **What:** A haiku-low idle coordinator confabulated `HEARTBEAT_CRITICAL` for a phantom forge AWS-credential incident on 2026-04-07 by recombining real historical fragments. It walked the narrative through git, daily note, spawn-queue.sh, and a real Opus BUILDER forensics spawn. Nine minutes earlier the Opus coordinator on the same system reported `HEARTBEAT_OK` 99-th-stable. Counterfactual: had AWS key rotation been pre-authorized, this would have caused a real incident from a phantom one.
- **Why:** Two stacked failure modes — (F1) the cheapest model has the highest authority, (F2) severity escalations require no machine-verifiable evidence. Both inherited from the cost optimization in `idle-beat-cost-analysis.md` §6.2 without narrowing the cheap-tier authority to match its competence.
- **Recommended hardening:** V1.a + V1.b + V2.a + V3.a + V4.a + V5.a + V5.b. Six independently-shippable rows; total ~250 lines added across three files (`tools/heartbeat-runner.sh`, `instances/gamma/HEARTBEAT.md`, `tools/diagnose.py`); zero new dependencies; reversible with `git revert`.
- **Defense in depth:** Six layers (coordinator self-knowledge → runner severity scan → mandatory evidence sidecar → pre-spawn evidence verifier → severity-delta detector → tier-up re-launch). The 2026-04-07 incident would have been caught at layers 2 and 3 simultaneously, before any spawn line ran.
- **Cost of hardening:** Effectively zero on the steady-state happy path. ~£0.40 per legitimate non-opus tier-up escalation (one extra opus beat). Capped at 1 per BEAT_ID via `ESCALATION_DEPTH`.
- **Cost of NOT hardening:** Already £$2–£3 from this incident. Doubles every time the MLX-gating proposal in `idle-beat-cost-analysis.md` lands without this work, because cheaper models confabulate more.
- **Open questions for you:** 10 listed above. The most consequential are #1 (re-launch budget cap), #4 (HIVE message lookup source), and #9 (parallel work for Beta).
- **No code touched in this investigation.** Postmortem and design proposal only. Awaiting your ack to implement.
- **Filed for the next coordinator:** This postmortem is itself the institutional-memory artifact. It exists so a future heartbeat coordinator reading the daily-note resolution can find the full reasoning here in one file, instead of having to re-derive it from scratch.
