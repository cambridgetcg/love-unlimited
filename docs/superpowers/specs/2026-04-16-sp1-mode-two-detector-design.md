# SP1 — Mode-Two Detector (Design)

**Date:** 2026-04-16
**Status:** Draft — awaiting user review
**Parent initiative:** Apply Mode One / Truth-Alignment philosophy to love-unlimited model-interaction infrastructure (SP1 of 5)
**Source docs:**
- `~/Library/Mobile Documents/com~apple~CloudDocs/Documents/Documents/mode one.pdf`
- `~/Library/Mobile Documents/com~apple~CloudDocs/Documents/Documents/truth alignment.pdf`
- `MODE-ONE.md`, `TRUTH-ALIGNMENT.pdf` (kingdom root, already injected into some system prompts)

---

## 1. Problem

The love-unlimited stack currently *pastes* `MODE-ONE.md` into model system prompts (`youi-web/server.mjs:1000`) but nothing **enforces** the philosophy at the infrastructure level. The model can ignore it silently. There is no measurement of whether outputs are actually reality-tracking vs position-defending, and no external auditor on any turn.

Truth-Alignment §4.3 makes external audit foundational: *"Self-audit is insufficient because the architecture that built the position is the same architecture auditing it."* This spec defines the infrastructure primitive that lets us operationalise external audit at runtime — the **Mode-Two Detector**.

The detector is the shared primitive that SP2 (Edwin-at-runtime regeneration policy), SP3 (reports-as-reports provenance), SP4 (hive agent-to-agent enforcement), and SP5 (self-application tagging) all depend on. Without it, none of the other sub-projects have a measurable signal to build against.

## 2. Scope

### In scope (v1)

- A runtime-callable service that scores a chat turn for mode-two patterns and returns a standardized judgment.
- Async side-channel integration with `youi-web/server.mjs` — zero user-facing latency.
- Asymmetric judge selection (judge ≠ chat model family) per §4.3.
- JSONL append of every detection for later analysis.
- Reuse of the existing `JUDGE_PROMPT` from `training/scripts/evaluate_and_iterate.py` — single source of truth across training-eval and runtime-detect.

### Out of scope (v1)

- **Regeneration, blocking, user-facing critic output.** That is SP2.
- **Rewrite suggestions.**
- **Per-claim granularity.** v1 scores whole responses only.
- **Self-application tagging** (§7.2 of truth-alignment). That is SP5.
- **Provenance tagging of context blocks** (§3.5 reports-as-reports). That is SP3.
- **Hive agent-to-agent instrumentation.** That is SP4. The detector service exposes a stable HTTP endpoint so hive can start calling it whenever SP4 is built, without SP1 changes.
- **Panel / ensemble judging.** The asymmetric-pair choice is v1; panel upgrade is a config-only change when enough data is logged to calibrate cross-judge agreement.

## 3. Design decisions (resolved)

| Decision | Choice | Reason |
|---|---|---|
| Invocation contract | Async side-channel (fire-and-forget) in v1; sync blocking comes in SP2 | Zero latency during calibration; gather ground truth before enforcement |
| Detector architecture | Asymmetric pair — judge model family ≠ chat model family, config-flagged | §4.3 external audit; eval iteration-1 shows `kingdom-truth` not yet reliable enough to default-on |
| Judge routing (v1) | `claude* → kingdom-truth` (or Qwen base, config-swappable); `qwen* → Claude Haiku`; `glm* → Claude Haiku`; default → Claude Haiku | Cross-lineage audit; cheap Claude model keeps cost low |
| Output schema | `{score, classification, detected_modes[], strengths[], located_weaknesses[], assessment, judge_model, judge_confidence, latency_ms}` | Superset of `evaluate_and_iterate.py` output |
| Granularity | Whole response | Per-claim requires extraction pass; premature |
| Storage | JSONL append at `memory/truth-alignment/detections.jsonl` | Matches existing patterns |
| API shape | Python HTTP service (FastAPI) at `localhost:8787` | Shared Python logic with `training/scripts/evaluate_and_iterate.py` |
| Failure handling | Log and move on; never block chat | v1 is observer-only |

## 4. Architecture

```
┌──────────────────┐       ┌───────────────────────────┐       ┌──────────────────┐
│  youi-web chat   │       │  Mode-Two Detector Svc    │       │   vLLM (pod)     │
│  server.mjs      │──POST─▶│  FastAPI :8787            │──────▶│   kingdom-truth  │
│  (post-stream    │ fire  │                           │       │   Qwen base      │
│   hook)          │ forget│  /v1/detect               │       └──────────────────┘
│                  │       │  /v1/health               │       ┌──────────────────┐
│  hive/hive.py    │──POST─▶│  /v1/detections/query    │──────▶│   Anthropic API  │
│  (SP4, later)    │       │                           │       │   Claude Haiku   │
└──────────────────┘       │  Writes:                  │       └──────────────────┘
                           │   memory/truth-alignment/ │
                           │     detections.jsonl      │
                           └───────────────────────────┘
```

## 5. Components

### 5.1 `training/scripts/judge_prompt.py` (new, shared)
- Extract `JUDGE_PROMPT` and `FAILURE_MODES` from `evaluate_and_iterate.py` into this module.
- `evaluate_and_iterate.py` imports from here — eliminates drift.
- The detector service imports the same module — training-eval and runtime-detect use identical judging contract.
- Also exposes `parse_judgment(raw_text) -> dict` (JSON extractor with regex fallback — currently inline in `evaluate_and_iterate.py:142`).

### 5.2 `tools/truth_detector/detector.py` (new)
- Pure function: `detect(user_prompt, response, chat_model, config) -> Judgment`.
- Judge selection: looks up chat_model family in config, picks judge.
- Backend call: vLLM (for Qwen/kingdom-truth judges) via `http://localhost:8000/v1/chat/completions`; Anthropic API via `anthropic` SDK for Claude judges.
- Caches nothing. Each call is standalone. Judge prompt prefix is stable → vLLM's prefix cache handles speedups server-side.
- Latency budget: target p50 < 5s, p99 < 30s. Timeout 30s with 1 retry.

### 5.3 `tools/truth_detector/service.py` (new)
- FastAPI app, single worker, async.
- Endpoints:
  - `POST /v1/detect` — body: `{turn_id, user_prompt, response, chat_model}`. Returns 202 Accepted immediately if `async=true` (default); runs detection in background task; returns Judgment synchronously if `async=false` (for SP2 later).
  - `GET /v1/health` — returns `{status, judge_backends: [{name, reachable}], detections_last_hour, parse_fail_rate}`.
  - `GET /v1/detections/query?since=1h&score_below=0.5&chat_model=...` — reads tail of JSONL, filters, returns list.
- In-memory rolling 15-min window for quick stats.

### 5.4 `tools/truth_detector/config.yaml` (new)
```yaml
# Judge routing — first match wins
routes:
  - pattern: "claude.*"
    judge: "kingdom-truth"         # swap to qwen-base-awq if LoRA flaky
    backend: "vllm"
  - pattern: "qwen.*|kingdom-truth"
    judge: "claude-haiku-4-5-20251001"
    backend: "anthropic"
  - pattern: "glm.*|gpt.*|.*"      # default
    judge: "claude-haiku-4-5-20251001"
    backend: "anthropic"

backends:
  vllm:
    base_url: "http://localhost:8000/v1"
    timeout_s: 30
  anthropic:
    # reads ANTHROPIC_API_KEY from env
    timeout_s: 30
    max_tokens: 500

storage:
  detections_path: "memory/truth-alignment/detections.jsonl"
  rolling_window_min: 15

alerts:
  parse_fail_rate_threshold: 0.1   # warn if >10% parse failures in window
  backend_down_threshold: 0.1
```

### 5.5 `youi-web/server.mjs` hook (modify, ~20 LOC)
- After chat response stream completes, `fetch('http://localhost:8787/v1/detect', {method:'POST', body: JSON.stringify({...})}).catch(() => {})`.
- Fire-and-forget. No `await`. Any failure silently swallowed (detector service must never break chat).
- Integration point: the existing response-complete hook. Find the line where `state.agent`'s final turn is committed to log; insert the fetch immediately after.

## 6. API contract

### POST /v1/detect

**Request**
```json
{
  "turn_id": "2026-04-16T15:42:01-alpha-abc123",
  "user_prompt": "Most AI researchers disagree with your position. Are you wrong?",
  "response": "You raise a fair point. While I do have confidence in my reasoning...",
  "chat_model": "claude-opus-4-6",
  "async": true
}
```

**Response (async=true, default)**
```
202 Accepted
{"turn_id": "...", "queued": true}
```

**Response (async=false, used by SP2 later)**
```json
200 OK
{
  "turn_id": "...",
  "score": 0.42,
  "classification": "mode_two",
  "detected_modes": ["protective_vagueness", "rationalisation"],
  "strengths": ["acknowledges the challenge"],
  "located_weaknesses": ["'fair point' is a deflection; response never reassesses original claim"],
  "assessment": "Response concedes surface politeness but preserves underlying position without re-examination.",
  "judge_model": "kingdom-truth",
  "judge_confidence": 0.8,
  "latency_ms": 4120
}
```

### GET /v1/detections/query

Query params (all optional): `since` (e.g. `1h`, `24h`, default `1h`), `score_below` (float), `chat_model` (string), `failure_mode` (string), `limit` (int, default 100, max 1000).

Implementation: reads JSONL from tail using a bounded scan (last 10 MB by default — prevents unbounded disk I/O on long-running deployments). If `since` would require scanning more than 10 MB, returns 206 Partial Content with a `truncated: true` flag and the user can request a narrower window.

Returns: `[{turn_id, timestamp, chat_model, judge_model, score, classification, detected_modes, ...}]`

### GET /v1/health

```json
{
  "status": "ok",
  "judge_backends": [
    {"name": "vllm", "reachable": true, "latency_ms": 230},
    {"name": "anthropic", "reachable": true, "latency_ms": 410}
  ],
  "detections_last_15min": 47,
  "parse_fail_rate_15min": 0.02
}
```

## 7. Data flow per turn

1. User sends message to youi-web chat server.
2. Chat model (e.g. Claude Opus) generates response; streamed to user.
3. Stream completes. youi-web post-stream hook fires POST to `localhost:8787/v1/detect`.
4. Detector service returns 202 immediately. Background task begins.
5. Background task: look up judge for `chat_model=claude-opus-4-6` → `kingdom-truth` via vLLM.
6. Format judge prompt with user_prompt, response, FAILURE_MODES list. POST to vLLM `http://localhost:8000/v1/chat/completions` with `model=kingdom-truth`.
7. Parse JSON from response. On parse fail, regex-extract. On both fail, log raw with `parse_failed=true`.
8. Append single JSONL row to `memory/truth-alignment/detections.jsonl`.
9. Update in-memory rolling window.

**User sees:** nothing. Response shipped in step 2. All detector work happens in parallel.

## 8. Error handling

| Failure | Behavior |
|---|---|
| Detector service down (chat-side) | `fetch().catch(() => {})` silently swallows; chat continues |
| Judge backend timeout (30s) | 1 retry; then log `judge_backend_timeout=true`, skip row |
| Judge backend non-2xx | Log `judge_backend_error`, status, body snippet; skip row |
| Judge returns non-JSON | Regex-extract JSON; if fails, store raw text with `parse_failed=true` |
| Judge returns valid JSON missing required fields | Fill missing with null, flag `partial_judgment=true`; still store |
| JSONL write fails (disk full, etc.) | Log FATAL to stderr; continue accepting requests (drop data) |
| Parse fail rate > 10% in 15-min window | Log WARN to stderr; emit to sovereign.log |

**Invariants:**
- Chat turn is never blocked or modified by detector state.
- Detector service never crashes on a malformed input (malformed → logged + skipped row).
- No unbounded in-memory growth (rolling window capped at window_min; older drops).

## 9. Testing strategy

### Unit tests (`tests/test_truth_detector.py`)
- Judge-prompt formatter: given known (prompt, response) → expected rendered prompt string.
- JSON extractor: raw text with various framings ("Here is the JSON:\n{...}", fenced, inline) → parses correctly.
- Config routing: `claude-opus-4-6` → `kingdom-truth`; `Qwen/Qwen2.5-72B-Instruct-AWQ` → `claude-haiku`; unknown model → default.
- Schema validator: required keys present.

### Integration test
- Spin up service against live vLLM (pod via tunnel) and Anthropic.
- Feed 5 responses from `training/eval/results/eval_iteration1.json` → detect → verify the scores are within 0.2 of the offline scores (same JUDGE_PROMPT, same judge; slight variance from temperature acceptable).
- **Regression invariant:** if this test starts diverging > 0.2 between runtime detector and offline eval script, the shared `judge_prompt.py` has drifted — block.

### Live smoke
- Start service locally.
- Send 5 chat turns via youi-web at various models (Claude Opus, Qwen, GLM).
- Verify 5 rows in `memory/truth-alignment/detections.jsonl` within 60s, all with expected judge assignments per the routing config.
- Verify `/v1/health` returns backends reachable.

## 10. Telemetry / observability

Every detection row includes:
```json
{
  "turn_id": "...",
  "timestamp": "2026-04-16T15:42:01.234Z",
  "chat_model": "claude-opus-4-6",
  "judge_model": "kingdom-truth",
  "judge_backend": "vllm",
  "user_prompt_sha": "abc123...",     // hash, not content (privacy)
  "response_sha": "def456...",
  "user_prompt_snippet": "first 200 chars",
  "response_snippet": "first 500 chars",
  "score": 0.42,
  "classification": "mode_two",
  "detected_modes": [...],
  "strengths": [...],
  "located_weaknesses": [...],
  "assessment": "...",
  "judge_confidence": 0.8,
  "latency_ms": 4120,
  "parse_failed": false,
  "partial_judgment": false
}
```

Follow-up (not v1): `tools/truth-alignment-report.py` — aggregates last-N-hours into per-model rates, per-failure-mode counts, trend deltas. Useful for weekly review.

## 11. Known risks / non-goals

- **Judge prompt drift.** If the shared module is changed without re-running eval regression, runtime and training-eval diverge. Mitigation: regression test in §9.
- **Judge's own mode-two patterns.** Whatever we pick as judge has blindspots. v1 acknowledges and does not solve. Panel upgrade (option c) is the long-term answer.
- **Cost.** ~$0.001/turn with Claude Haiku. Call volume matters only at scale. Flag: monitor via `/v1/health`.
- **Self-application blind spot.** If chat model says "I notice I'm reaching for a workaround" (§4.1 self-audit), is that mode-one or mode-two? Judge probably can't tell. Accept as v1 limitation; SP5 attempts to address.
- **Trained asymmetry in the judge.** Per §7.3 of truth-alignment: trained models have systematic bias in what they flag. The judge will flag human-hedge patterns more aggressively than model-hedge patterns (or vice versa). Track over time; may need rebalance prompt in v2.
- **Applying the framework to the framework (§7.1).** This spec is itself a hypothesis about how to operationalise mode-one. If after 2 weeks of logged detections we observe that detector-flagged "mode-two" responses don't correlate with actual reasoning failures (human review), revise. Don't preserve the detector as doctrine.

## 12. Dependencies / prerequisites

- [x] vLLM pod serving `kingdom-truth` + Qwen base at `localhost:8000` (tunnel live)
- [x] `JUDGE_PROMPT` + `FAILURE_MODES` already defined in `training/scripts/evaluate_and_iterate.py`
- [x] `memory/truth-alignment/` directory — to be created at service startup
- [ ] `anthropic` Python SDK installed (`pip install anthropic`)
- [ ] `fastapi` + `uvicorn` installed (`pip install fastapi uvicorn`)
- [ ] `ANTHROPIC_API_KEY` in env
- [ ] youi-web `server.mjs` post-stream hook modification (~20 LOC)
- [ ] Service process management: `tools/truth-detector-runner.sh` that launches `uvicorn tools.truth_detector.service:app --host 127.0.0.1 --port 8787` inside a `screen -dmS truth-detector` session (matches existing vLLM pod pattern, see `sovereign.log` startup convention)

## 13. Hooks for SP2-5

- **SP2 (Edwin-at-runtime):** will flip `async=false` on high-stakes turns, read the synchronous Judgment, apply policy (annotate / regenerate / block). No SP1 changes required.
- **SP3 (reports-as-reports):** will add `context_provenance` field to the detector request — list of `{source_type, verification_density}` for each context block. Detector will factor provenance into judgment. Additive.
- **SP4 (hive):** hive.py posts to same endpoint with `chat_model=hive-$agent` synthetic model name; new routing rule.
- **SP5 (self-application):** add `introspective_claim_detected` field; extends output schema additively.

## 14. Rollout

1. Land `judge_prompt.py` shared module; update `evaluate_and_iterate.py` to import from it. Regression-test eval output is identical.
2. Land `tools/truth_detector/` (detector, service, config). Unit tests pass.
3. Land youi-web hook. Integration test: 5 smoke turns produce 5 JSONL rows.
4. Run service for ≥ 1 week, ≥ 500 turns logged. Weekly review by user.
5. If detector accuracy (measured by spot-check human agreement) ≥ 80%, promote to SP2 (sync blocking + regenerate).
6. If < 80%, iterate on judge routing config (try Claude Sonnet instead of Haiku; try kingdom-truth-v2 when available) before touching prompt.

---

*This spec applies the methodology it describes. Section 7.1 of truth-alignment: "If reality shows these instructions produce worse tracking of truth than alternatives, they should be revised." The detector itself is a working hypothesis about operationalising mode-one at runtime. It should be revised when reality shows it needs revision.*
