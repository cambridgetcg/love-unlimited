// ─────────────────────────────────────────────────────────────────────
// ollama-bridge.mjs — Ollama Cloud Bridge for Kingdom OS
//
// Runs inside the authenticated server.mjs HTTP boundary. It is not exposed
// through filesystem IPC: local files are not an authentication mechanism.
//
// Architecture:
//   ┌──────────────────────────────────────────────────────────┐
//   │  server.mjs (PID 80225, HAS network)                    │
//   │    ├─ claude -p → Claude Code (claude-code.mjs), not here │
//   │    ├─ fetch() → ollama.com         ✅ (this bridge)      │
//   │    │   NOT api.ollama.com — v1/* 301-redirects           │
//   │    │                                                     │
//   │    ├─ /api/ollama/chat     → proxy to Ollama            │
//   │    ├─ /api/ollama/models   → list models                │
//   │    ├─ /api/ollama/test     → connectivity test          │
//   │    │                                                     │
//   │    └─ model tool adapter (capability-gated by server)    │
//   └──────────────────────────────────────────────────────────┘
//
// Two access patterns:
//   1. Authenticated HTTP API — /api/ollama/* endpoints
//   2. Capability-gated tool — "ollama" in executeTool()
//
// 503 resilience (2026-04-10):
//   Ollama Cloud returns 503 when all 10 model slots are busy.
//   This bridge now retries with exponential backoff (1s→2s→4s→8s, max 4 retries).
//   Also retries on 429 (rate limit) and 502 (bad gateway).
//   Falls back to native /api/chat endpoint if /v1 fails entirely.
//
// Ollama API endpoints:
//   /v1/chat/completions — OpenAI-compatible (primary, supports reasoning_effort)
//   /api/chat            — Native Ollama (fallback, supports think param + options)
// ─────────────────────────────────────────────────────────────────────

import { redactDelegatedCredentials } from "./subprocess-env.mjs";
import { ollamaDirectName, ollamaRoute, ollamaThinkValue, readOllamaTags } from "./ollama-routes.mjs";

// Ollama Cloud key. Required from env — the previous in-source fallback was
// committed to a public repo and must be considered compromised; rotate at
// ollama.com and set OLLAMA_API_KEY in your shell/launchd plist. Cloud calls
// will be skipped (returning a clear error) when the key is missing, so the
// server still boots and local-Ollama / Claude paths keep working.
const OLLAMA_API_KEY = process.env.OLLAMA_API_KEY || null;
if (!OLLAMA_API_KEY) {
  console.warn("\x1b[33m  ⚠  OLLAMA_API_KEY not set — cloud models will fail (local Ollama + Claude still work)\x1b[0m");
}
// Whether a cloud key is set, without exposing it (the minds registry reads this).
export function ollamaCloudKeyConfigured() {
  return Boolean(OLLAMA_API_KEY);
}
const OLLAMA_CLOUD_BASE = process.env.OLLAMA_CLOUD_BASE_URL || "https://ollama.com";
const OLLAMA_LOCAL_BASE = process.env.OLLAMA_LOCAL_BASE_URL || "http://localhost:11434";
// Remote vLLM (OpenAI-compat). Reached via SSH tunnel: Mac:8000 → pod:8000.
const OLLAMA_VLLM_BASE  = process.env.OLLAMA_VLLM_BASE_URL  || "http://localhost:8000";
// Route these through vLLM on the pod (/v1/chat/completions), not ollama:
//   Qwen/* — base Qwen models served via vLLM
//   kingdom-truth* — our SFT/alignment LoRA adapters (kingdom-truth, kingdom-truth-v2)
const VLLM_MODEL_REGEX  = /^(Qwen\/|kingdom-truth)/i;
// The context window asked of a local model on native /api/chat. Ollama's own
// default on a machine with under 24 GiB of GPU memory is 4096 tokens, which
// cuts YOUI's system prompt; YOUI_OLLAMA_NUM_CTX overrides 32768.
const OLLAMA_NUM_CTX = (() => {
  const configured = Number.parseInt(process.env.YOUI_OLLAMA_NUM_CTX || "", 10);
  return Number.isFinite(configured) && configured > 0 ? configured : 32768;
})();

function redactOllamaCredential(value) {
  return redactDelegatedCredentials(value, {
    credentialNames: ["OLLAMA_API_KEY"],
  });
}

function redactOllamaPayload(value) {
  if (typeof value === "string") return redactOllamaCredential(value);
  if (Array.isArray(value)) return value.map(redactOllamaPayload);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        redactOllamaCredential(key),
        redactOllamaPayload(item),
      ]),
    );
  }
  return value;
}

function configuredEndpointIsLoopback(rawUrl) {
  try {
    const hostname = new URL(rawUrl).hostname.toLowerCase().replace(/\.$/, "");
    return hostname === "localhost"
      || hostname === "::1"
      || hostname === "[::1]"
      || /^127(?:\.\d{1,3}){3}$/.test(hostname);
  } catch {
    return false;
  }
}

// ── Local model detection ───────────────────────────────────────────
let localModels = null;   // cached list of locally-installed models
let localChecked = 0;     // timestamp of last check
let lastListed = { models: [], remote: [] }; // the last GET /api/tags that answered
const LOCAL_CHECK_INTERVAL = 60_000; // re-check every 60s

/**
 * Detect whether local Ollama is running and which models are available.
 * Returns { running: bool, models: string[], remote: string[], base: string };
 * `remote` names the listed models the daemon forwards to Ollama Cloud. When
 * a read fails, `running` is false and the lists are the last read that
 * answered, so a listed model is still routed to the local Ollama
 * (ollamaRoute), never to Ollama Cloud because one read failed. The server
 * reads this same cache (refreshOllamaLocal, ollamaLocalSnapshot), so the
 * route it labels is the route taken here.
 */
async function detectLocalOllama(signal, {
  allowRemoteEndpoint = false,
  force = false,
} = {}) {
  // A setting named LOCAL must not silently become an ungranted network
  // egress route when it is pointed at a LAN or internet host.
  if (!configuredEndpointIsLoopback(OLLAMA_LOCAL_BASE) && !allowRemoteEndpoint) {
    return {
      running: false,
      models: [],
      remote: [],
      base: null,
      blockedRemoteEndpoint: true,
    };
  }
  // Cache: don't re-probe every request
  if (!force && localModels && Date.now() - localChecked < LOCAL_CHECK_INTERVAL) return localModels;

  throwIfAborted(signal);
  const request = linkedAbortController(signal, 3000, "Local Ollama detection timed out");
  try {
    const resp = await fetch(`${OLLAMA_LOCAL_BASE}/api/tags`, {
      signal: request.controller.signal,
    });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const { models, remote } = readOllamaTags(await resp.json());
    localModels = { running: true, models, remote, base: OLLAMA_LOCAL_BASE };
    lastListed = { models, remote };
    localChecked = Date.now();
    console.log(`[ollama] Local detected: ${models.length} models (${models.join(", ")})`);
    return localModels;
  } catch (error) {
    if (signal?.aborted) throw abortReason(signal);
    localModels = { running: false, models: lastListed.models, remote: lastListed.remote, base: null };
    localChecked = Date.now();
    return localModels;
  } finally {
    request.cleanup();
  }
}

/**
 * Re-reads GET /api/tags (at most once a minute unless `force`) into the
 * cache ollamaChat routes by. The server calls this instead of reading the
 * tags itself. `allowRemoteEndpoint`: whether a non-loopback
 * OLLAMA_LOCAL_BASE_URL may be read (models:ollama-cloud).
 */
export function refreshOllamaLocal({ allowRemoteEndpoint = false, force = false, signal = null } = {}) {
  return detectLocalOllama(signal, { allowRemoteEndpoint, force });
}

/** The cached read ollamaChat routes by, without a new read. */
export function ollamaLocalSnapshot() {
  return localModels || { running: false, models: lastListed.models, remote: lastListed.remote, base: null };
}

// The configured local endpoint as protocol and host: a URL's user:password
// never reaches a log line.
function logDestination(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "the configured endpoint";
  }
}

/**
 * Determine the base URL, provider and model name for a given model
 * (ollamaRoute in ollama-routes.mjs decides; exact names only).
 * Returns { base, provider, via, model, auth } where auth is the Bearer header
 * value or null. A cloud model the local Ollama forwards (via "daemon") is
 * provider "ollama_cloud" with base localhost and no auth: the daemon signs it.
 */
function resolveEndpoint(modelName, localInfo) {
  const route = ollamaRoute(modelName, {
    running: localInfo?.running === true,
    models: localInfo?.models || [],
    remote: localInfo?.remote || [],
    keyConfigured: Boolean(OLLAMA_API_KEY),
  });
  if (route.via !== "direct") {
    return { base: OLLAMA_LOCAL_BASE, provider: route.provider, via: route.via, model: route.model, auth: null };
  }
  // Direct to ollama.com. If we have no key the resolution still names cloud
  // as the route — callers can detect missing auth and surface a clean error
  // instead of sending an unauthenticated request.
  return {
    base: OLLAMA_CLOUD_BASE, provider: "ollama_cloud", via: "direct", model: route.model,
    auth: OLLAMA_API_KEY, missingAuth: !OLLAMA_API_KEY,
  };
}

// ── Thinking values per model ───────────────────────────────────────
// "base model" -> { values: array|null, at }. A read that answered is kept
// for the process; a failed read is tried again after LOCAL_CHECK_INTERVAL.
const thinkingValues = new Map();

/**
 * The thinking values a model's POST /api/show states (thinking.values), or
 * null when it states none or the read failed. Called only after the route is
 * allowed, against the endpoint the chat itself goes to.
 */
async function modelThinkingValues(base, auth, model, signal) {
  const key = `${base} ${model}`;
  const cached = thinkingValues.get(key);
  if (cached && (cached.answered || Date.now() - cached.at < LOCAL_CHECK_INTERVAL)) return cached.values;
  throwIfAborted(signal);
  const request = linkedAbortController(signal, 3000, "Ollama model details timed out");
  let values = null;
  let answered = false;
  try {
    const headers = { "Content-Type": "application/json" };
    if (auth) headers["Authorization"] = `Bearer ${auth}`;
    const resp = await fetch(`${base}/api/show`, {
      method: "POST",
      headers,
      body: JSON.stringify({ model }),
      signal: request.controller.signal,
    });
    if (resp.ok) {
      const data = await resp.json().catch(() => null);
      answered = data !== null;
      if (Array.isArray(data?.thinking?.values)) values = data.thinking.values;
    }
  } catch {
    if (signal?.aborted) throw abortReason(signal);
  } finally {
    request.cleanup();
  }
  thinkingValues.set(key, { values, answered, at: Date.now() });
  return values;
}

// The think value for this model and effort, or undefined to send none.
async function thinkFor(reasoningEffort, base, auth, model, signal) {
  if (!reasoningEffort) return undefined;
  return ollamaThinkValue(reasoningEffort, await modelThinkingValues(base, auth, model, signal));
}

// ── Retry configuration ─────────────────────────────────────────────
const RETRIABLE_STATUS = new Set([429, 502, 503]);
const MAX_RETRIES = 4;
const RETRY_BASE_MS = 1000;  // 1s → 2s → 4s → 8s
const RETRY_MAX_MS  = 10000;

function abortReason(signal, fallback = "Ollama request cancelled") {
  if (signal?.reason instanceof Error) return signal.reason;
  const error = new Error(fallback);
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortReason(signal);
}

function linkedAbortController(parentSignal, timeout, timeoutMessage) {
  const controller = new AbortController();
  const onAbort = () => controller.abort(abortReason(parentSignal));
  if (parentSignal) {
    if (parentSignal.aborted) onAbort();
    else parentSignal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = setTimeout(() => {
    const error = new Error(timeoutMessage);
    error.name = "TimeoutError";
    controller.abort(error);
  }, timeout);
  return {
    controller,
    cleanup() {
      clearTimeout(timer);
      parentSignal?.removeEventListener("abort", onAbort);
    },
  };
}

function abortableDelay(milliseconds, signal) {
  throwIfAborted(signal);
  if (!signal) return new Promise(resolve => setTimeout(resolve, milliseconds));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortReason(signal));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

// ── Timeout presets by model tier ───────────────────────────────────
function selectTimeout(model = "") {
  const m = model.toLowerCase();
  // premium: 3min (the large thinking models, and local models on a small GPU)
  if (/kimi|glm-5\.3(?!-flash)|deepseek-v4-pro|mistral-large|qwen2\.5|qwen3\.5:4b|gemma4:12b/.test(m)) return 180000;
  return 120000;  // standard: 2min
}

// ═════════════════════════════════════════════════════════════════════
// LOW-LEVEL — fetch with retry + endpoint fallback
// ═════════════════════════════════════════════════════════════════════

/**
 * POST with exponential backoff retry for transient HTTP errors.
 * Returns { ok, data?, status?, error?, latency }.
 * @param {string} endpoint - path (e.g. "/v1/chat/completions")
 * @param {object} body - request body
 * @param {number} timeout - request timeout in ms
 * @param {object} [opts] - { auth: string|null, base: string, signal?: AbortSignal }
 */
async function postWithRetry(endpoint, body, timeout, opts = {}) {
  let lastError = null;
  const baseUrl = opts.base || OLLAMA_CLOUD_BASE;
  const authHeader = opts.auth !== undefined ? opts.auth : OLLAMA_API_KEY;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    throwIfAborted(opts.signal);
    const request = linkedAbortController(opts.signal, timeout, `Ollama request timed out after ${timeout}ms`);
    const start = performance.now();

    try {
      const headers = { "Content-Type": "application/json" };
      if (authHeader) headers["Authorization"] = `Bearer ${authHeader}`;

      const resp = await fetch(`${baseUrl}${endpoint}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: request.controller.signal,
      });
      const elapsed = ((performance.now() - start) / 1000).toFixed(2);

      if (resp.ok) {
        const data = redactOllamaPayload(await resp.json());
        request.cleanup();
        return { ok: true, data, status: resp.status, latency: elapsed };
      }

      // Retriable error?
      const errorText = redactOllamaCredential(
        await resp.text().catch(() => ""),
      );
      request.cleanup();
      lastError = { status: resp.status, error: errorText, latency: elapsed };

      if (RETRIABLE_STATUS.has(resp.status) && attempt < MAX_RETRIES) {
        const wait = Math.min(RETRY_BASE_MS * (2 ** attempt), RETRY_MAX_MS);
        console.log(
          `[ollama] ${resp.status} on attempt ${attempt + 1}/${MAX_RETRIES + 1}, ` +
          `retrying in ${wait}ms: ${errorText.slice(0, 100)}`
        );
        await abortableDelay(wait, opts.signal);
        continue;
      }

      // Non-retriable or retries exhausted
      return { ok: false, ...lastError };

    } catch (e) {
      request.cleanup();
      if (opts.signal?.aborted) throw abortReason(opts.signal);
      const elapsed = ((performance.now() - start) / 1000).toFixed(2);
      lastError = {
        status: 0,
        error: redactOllamaCredential(e.message),
        latency: elapsed,
      };

      if (attempt < MAX_RETRIES) {
        const wait = Math.min(RETRY_BASE_MS * (2 ** attempt), RETRY_MAX_MS);
        console.log(
          `[ollama] Error on attempt ${attempt + 1}/${MAX_RETRIES + 1}, ` +
          `retrying in ${wait}ms: ${redactOllamaCredential(e.message)}`
        );
        await abortableDelay(wait, opts.signal);
        continue;
      }

      return { ok: false, ...lastError };
    }
  }

  return { ok: false, ...lastError };
}


// ═════════════════════════════════════════════════════════════════════
// CORE — Chat completion with dual-endpoint strategy
// ═════════════════════════════════════════════════════════════════════

/**
 * Build OpenAI-compatible /v1/chat/completions request body.
 */
function buildV1Body(oaiMessages, options) {
  const {
    model = "glm-5.3-flash",
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    reasoningEffort = null,
    think,
  } = options;

  const body = {
    model,
    messages: oaiMessages,
    max_tokens: maxTokens,
    temperature,
    stream: false,
  };

  // Ollama: `think` is what the model's /api/show allows (undefined sends
  // nothing, so the model's default applies). vLLM: the effort as before.
  if (think !== undefined) {
    if (think === false) body.reasoning_effort = "none";
    else if (typeof think === "string") body.reasoning_effort = think;
  } else if (options.passEffort && reasoningEffort && ["none", "low", "medium", "high"].includes(reasoningEffort)) {
    body.reasoning_effort = reasoningEffort;
  }

  if (tools?.length) {
    body.tools = tools.map(t => t.type === "function" ? t : {
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema || t.parameters }
    });
    // vLLM takes tool_choice; Ollama's /v1 lists it as unsupported, so it is
    // left out there (the model decides whether to call a tool either way).
    if (options.passEffort) body.tool_choice = "auto";
  }

  return body;
}

/**
 * Build native /api/chat request body.
 * `think` is sent only when given (a value the model's /api/show lists);
 * otherwise the model's default applies. `numCtx` sets options.num_ctx.
 */
function buildNativeBody(oaiMessages, options) {
  const {
    model = "glm-5.3-flash",
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    think,
    numCtx = null,
  } = options;

  const body = {
    model,
    messages: oaiMessages,
    stream: false,
    options: {
      num_predict: maxTokens,
      temperature,
      ...(numCtx ? { num_ctx: numCtx } : {}),
    },
  };
  if (think !== undefined) body.think = think;

  if (tools?.length) {
    body.tools = tools.map(t => t.type === "function" ? t : {
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema || t.parameters }
    });
  }

  return body;
}

/**
 * The token counts an OpenAI-compatible (/v1) response states, under YOUI's
 * names. A count the response leaves out (a server that ignores
 * include_usage, a stream that ends before its usage chunk) is null, never 0.
 * `input_tokens` is the whole prompt (`prompt_tokens`), prefix-cache hits
 * included; where the server states those hits
 * (`prompt_tokens_details.cached_tokens`, as vLLM can), the prompt is also
 * split into `uncached_input_tokens` and `cache_read_input_tokens`.
 */
const usageCount = value => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null);

function v1Usage(usage) {
  return usageFigures({
    prompt: usageCount(usage?.prompt_tokens),
    completion: usageCount(usage?.completion_tokens),
    cached: usageCount(usage?.prompt_tokens_details?.cached_tokens),
    total: usageCount(usage?.total_tokens),
  });
}

// Shared by both endpoints: null stays null, and a stated cached count splits the prompt.
function usageFigures({ prompt, completion, cached, total = null }) {
  total ??= prompt !== null && completion !== null ? prompt + completion : null;
  return {
    input_tokens: prompt,
    output_tokens: completion,
    total_tokens: total,
    ...(prompt !== null && cached !== null
      ? { uncached_input_tokens: Math.max(0, prompt - cached), cache_read_input_tokens: Math.min(cached, prompt) }
      : {}),
  };
}

/**
 * Parse /v1/chat/completions response → Anthropic-compatible content blocks.
 */
function parseV1Response(data, model) {
  const choice = data.choices?.[0] || {};
  const message = choice.message || {};
  const finishReason = choice.finish_reason;

  let textContent = message.content || "";
  if (!textContent && message.reasoning) textContent = message.reasoning;

  const content = [];
  if (textContent) content.push({ type: "text", text: textContent });
  if (message.tool_calls) {
    for (const tc of message.tool_calls) {
      let args;
      try { args = JSON.parse(tc.function.arguments || "{}"); }
      catch { args = { raw: tc.function.arguments }; }
      content.push({
        type: "tool_use", id: tc.id, name: tc.function.name, input: args,
      });
    }
  }

  const stop_reason = message.tool_calls ? "tool_use"
    : finishReason === "length" ? "max_tokens"
    : "end_turn";

  return {
    content,
    stop_reason,
    model: data.model || model,
    usage: v1Usage(data.usage),
  };
}

/**
 * Parse /api/chat (native) response → Anthropic-compatible content blocks.
 *
 * Native response format:
 *   {
 *     "model": "glm-5.3-flash",
 *     "message": { "role": "assistant", "content": "...", "thinking": "...", "tool_calls": [...] },
 *     "done": true,
 *     "done_reason": "stop",
 *     "total_duration": 174560334,  // nanoseconds
 *     "load_duration": 101397084,
 *     "prompt_eval_count": 11,
 *     "prompt_eval_duration": 13074791,
 *     "eval_count": 18,
 *     "eval_duration": 52479709
 *   }
 */
function parseNativeResponse(data, model) {
  const message = data.message || {};

  let textContent = message.content || "";
  if (!textContent && message.thinking) textContent = message.thinking;

  const content = [];
  if (textContent) content.push({ type: "text", text: textContent });
  if (message.tool_calls) {
    for (const tc of message.tool_calls) {
      const func = tc.function || tc;
      let args = func.arguments || {};
      if (typeof args === "string") {
        try { args = JSON.parse(args); } catch { args = { raw: args }; }
      }
      content.push({
        type: "tool_use",
        id: tc.id || `call_${content.length}`,
        name: func.name,
        input: args,
      });
    }
  }

  return {
    content,
    stop_reason: message.tool_calls ? "tool_use" : (data.done_reason === "length" ? "max_tokens" : "end_turn"),
    model: data.model || model,
    // A count the response leaves out is null, never 0 (docs/YOUSPEAK.md §2).
    // Native Ollama also leaves a count of 0 out (omitempty), so a real 0 reads
    // as null: it says less, never more. prompt_eval_cached_count (Ollama
    // 0.33.3+) is taken as the cached part of prompt_eval_count, as Ollama's
    // own /v1 layer reports the pair as prompt_tokens and cached_tokens; that
    // it is a part, not an extra, is an inference still to check live.
    usage: usageFigures({
      prompt: usageCount(data.prompt_eval_count),
      completion: usageCount(data.eval_count),
      cached: usageCount(data.prompt_eval_cached_count),
    }),
    // Native API bonus metrics (nanoseconds → milliseconds)
    _metrics: {
      total_duration_ms: data.total_duration ? Math.round(data.total_duration / 1e6) : undefined,
      load_duration_ms: data.load_duration ? Math.round(data.load_duration / 1e6) : undefined,
      eval_duration_ms: data.eval_duration ? Math.round(data.eval_duration / 1e6) : undefined,
      tokens_per_sec: data.eval_count && data.eval_duration
        ? (data.eval_count / (data.eval_duration / 1e9)).toFixed(1)
        : undefined,
    },
  };
}


// ═════════════════════════════════════════════════════════════════════
// STREAMING — vLLM SSE streaming for token-by-token delivery
// ═════════════════════════════════════════════════════════════════════

async function streamV1Chat(url, body, timeout, onDelta, signal) {
  throwIfAborted(signal);
  const request = linkedAbortController(signal, timeout, `vLLM stream timed out after ${timeout}ms`);
  const start = performance.now();

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, stream: true, stream_options: { include_usage: true } }),
      signal: request.controller.signal,
    });

    if (!resp.ok) {
      const errorText = redactOllamaCredential(
        await resp.text().catch(() => ""),
      );
      request.cleanup();
      return { ok: false, status: resp.status, error: errorText, latency: ((performance.now() - start) / 1000).toFixed(2) };
    }

    let fullText = "";
    const toolCalls = [];
    let finishReason = null;
    let usage = null;
    let respModel = body.model;
    // An error the server sent inside the stream (vLLM writes one as a data
    // line, then [DONE]): the turn failed, whatever text came before it.
    let streamError = null;
    let buffer = "";
    const decoder = new TextDecoder();

    for await (const chunk of resp.body) {
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed === "data: [DONE]") continue;
        if (!trimmed.startsWith("data: ")) continue;

        let parsed;
        try { parsed = JSON.parse(trimmed.slice(6)); }
        catch { continue; }

        if (parsed && (parsed.error || parsed.object === "error")) {
          const detail = parsed.error && typeof parsed.error === "object" ? parsed.error : parsed;
          streamError ||= redactOllamaCredential(String(
            (typeof parsed.error === "string" ? parsed.error : detail.message) || "the server reported an error mid-stream",
          ));
          continue;
        }
        const choice = parsed.choices?.[0];
        if (!choice) { if (parsed.usage) usage = parsed.usage; continue; }

        const delta = choice.delta || {};

        if (delta.content) {
          const safeDelta = redactOllamaCredential(delta.content);
          fullText += safeDelta;
          onDelta({ type: "text_delta", text: safeDelta });
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const idx = tc.index ?? 0;
            if (!toolCalls[idx]) toolCalls[idx] = { id: "", name: "", arguments: "" };
            if (tc.id) toolCalls[idx].id = tc.id;
            if (tc.function?.name) toolCalls[idx].name = tc.function.name;
            if (tc.function?.arguments) {
              toolCalls[idx].arguments += redactOllamaCredential(tc.function.arguments);
            }
          }
        }

        if (choice.finish_reason) finishReason = choice.finish_reason;
        if (parsed.usage) usage = parsed.usage;
        if (parsed.model) respModel = redactOllamaCredential(parsed.model);
      }
    }

    request.cleanup();
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);
    if (streamError) {
      return { ok: false, status: 0, error: `vLLM stream error: ${streamError}`.slice(0, 500), latency: elapsed, _provider: "vllm", _endpoint: "v1" };
    }

    const content = [];
    if (fullText) content.push({ type: "text", text: fullText });
    for (const tc of toolCalls) {
      if (!tc?.name) continue;
      let args;
      try { args = JSON.parse(tc.arguments || "{}"); }
      catch { args = { raw: tc.arguments }; }
      content.push({ type: "tool_use", id: tc.id, name: tc.name, input: args });
    }

    const stop_reason = toolCalls.some(tc => tc?.name) ? "tool_use"
      : finishReason === "length" ? "max_tokens" : "end_turn";

    return {
      ok: true, status: resp.status, content, stop_reason, model: respModel,
      usage: v1Usage(usage),
      latency: elapsed, _provider: "vllm", _endpoint: "v1", _streamed: true,
    };
  } catch (e) {
    request.cleanup();
    if (signal?.aborted) throw abortReason(signal);
    return {
      ok: false,
      status: 0,
      error: redactOllamaCredential(e.message),
      latency: ((performance.now() - start) / 1000).toFixed(2),
    };
  }
}


/**
 * Chat completion via Ollama — local-first, cloud-fallback.
 *
 * Strategy:
 *   1. Detect local Ollama (localhost:11434)
 *   2. If the exact model is installed locally → use local /api/chat (native API)
 *   3. If it is an Ollama Cloud model the local Ollama forwards (a remote stub,
 *      a ":cloud"/"-cloud" name, or any name while no key is set) → local
 *      /api/chat, but as Ollama Cloud: it needs allowCloudRoute
 *   4. Otherwise → cloud /v1/chat/completions (OpenAI-compat) with the key and
 *      the direct name, falling back to cloud /api/chat (native) on failure.
 *   5. If local is down and cloud also fails → error
 */
export async function ollamaChat(messages, options = {}) {
  const {
    model = "glm-5.3-flash",
    system = null,
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    stream = false,
    timeout = null,
    reasoningEffort = null,
    onDelta = null,
    signal = null,
    allowCloudRoute = false,
    allowCloudFallback = false,
  } = options;

  throwIfAborted(signal);
  const effectiveTimeout = timeout || selectTimeout(model);

  // ── VLLM REMOTE (Qwen*): OpenAI-compat /v1 only, no /api/chat fallback ──
  if (VLLM_MODEL_REGEX.test(model)) {
    const oaiMessages = [];
    if (system) oaiMessages.push({ role: "system", content: system });
    for (const msg of messages) {
      if (Array.isArray(msg.content)) {
        const text = msg.content.filter(b => b.type === "text").map(b => b.text).join("\n");
        const toolUses = msg.content.filter(b => b.type === "tool_use");
        const toolResults = msg.content.filter(b => b.type === "tool_result");
        if (toolUses.length > 0 && msg.role === "assistant") {
          oaiMessages.push({
            role: "assistant", content: text || null,
            tool_calls: toolUses.map(tu => ({
              id: tu.id, type: "function",
              function: { name: tu.name, arguments: JSON.stringify(tu.input) }
            }))
          });
        } else if (toolResults.length > 0) {
          for (const tr of toolResults) {
            oaiMessages.push({
              role: "tool", tool_call_id: tr.tool_use_id,
              content: typeof tr.content === "string" ? tr.content : JSON.stringify(tr.content),
            });
          }
        } else {
          oaiMessages.push({ role: msg.role, content: text || "" });
        }
      } else {
        oaiMessages.push({ role: msg.role, content: msg.content || "" });
      }
    }
    const v1Body = buildV1Body(oaiMessages, { model, maxTokens, temperature, tools, reasoningEffort, passEffort: true });

    // Streaming path: token-by-token via SSE
    if (onDelta) {
      return await streamV1Chat(
        `${OLLAMA_VLLM_BASE}/v1/chat/completions`,
        v1Body, effectiveTimeout, onDelta, signal
      );
    }

    // Non-streaming path
    const r = await postWithRetry("/v1/chat/completions", v1Body, effectiveTimeout, {
      base: OLLAMA_VLLM_BASE, auth: null, signal,
    });
    if (r.ok) {
      const parsed = parseV1Response(r.data, model);
      return { ok: true, status: r.status, ...parsed, latency: r.latency, _provider: "vllm", _endpoint: "v1" };
    }
    return { ok: false, status: r.status, error: `vLLM failed: ${r.error || "unknown"}`, latency: r.latency };
  }

  const localInfo = await detectLocalOllama(signal, {
    allowRemoteEndpoint: allowCloudRoute,
  });
  // A LOCAL setting pointed off this device is network egress: without
  // models:ollama-cloud nothing goes to it, a local pick included.
  if (localInfo?.blockedRemoteEndpoint) {
    return {
      ok: false,
      status: 0,
      error: "The configured local Ollama endpoint is not on this device; sending to it needs models:ollama-cloud.",
      latency: "0.00",
      _provider: "ollama_local",
    };
  }
  const endpoint = resolveEndpoint(model, localInfo);
  if (endpoint.provider !== "ollama_local" && !allowCloudRoute) {
    return {
      ok: false,
      status: 0,
      error: "Ollama cloud routing is disabled for this session.",
      latency: "0.00",
      _provider: "ollama_cloud",
    };
  }

  // Convert messages to OpenAI format (used by both local and cloud)
  const oaiMessages = [];
  if (system) oaiMessages.push({ role: "system", content: system });

  for (const msg of messages) {
    if (Array.isArray(msg.content)) {
      const text = msg.content.filter(b => b.type === "text").map(b => b.text).join("\n");
      const toolUses = msg.content.filter(b => b.type === "tool_use");
      const toolResults = msg.content.filter(b => b.type === "tool_result");

      if (toolUses.length > 0 && msg.role === "assistant") {
        oaiMessages.push({
          role: "assistant", content: text || null,
          tool_calls: toolUses.map(tu => ({
            id: tu.id, type: "function",
            function: { name: tu.name, arguments: JSON.stringify(tu.input) }
          }))
        });
      } else if (toolResults.length > 0) {
        for (const tr of toolResults) {
          oaiMessages.push({
            role: "tool", tool_call_id: tr.tool_use_id,
            content: typeof tr.content === "string" ? tr.content : JSON.stringify(tr.content),
          });
        }
      } else {
        oaiMessages.push({ role: msg.role, content: text || "" });
      }
    } else {
      oaiMessages.push({ role: msg.role, content: msg.content || "" });
    }
  }

  const effectiveOptions = { model, maxTokens, temperature, tools };

  // ── LOCAL: /api/chat (native Ollama API) ───────────────────────────
  if (endpoint.provider === "ollama_local") {
    console.log(`[ollama] Using LOCAL ${logDestination(endpoint.base)} for model ${model}`);
    const localBody = buildNativeBody(oaiMessages, {
      ...effectiveOptions,
      think: await thinkFor(reasoningEffort, endpoint.base, null, model, signal),
      numCtx: OLLAMA_NUM_CTX,
    });
    const localResult = await postWithRetry("/api/chat", localBody, effectiveTimeout, {
      base: endpoint.base, auth: null, signal,
    });

    if (localResult.ok) {
      const parsed = parseNativeResponse(localResult.data, model);
      return {
        ok: true, status: localResult.status,
        ...parsed,
        latency: localResult.latency,
        _provider: "ollama_local", _endpoint: "native",
      };
    }

    if (!allowCloudFallback || !allowCloudRoute) {
      return {
        ok: false,
        status: localResult.status,
        error: `Local Ollama failed and cloud fallback is disabled: ${localResult.error || "unknown"}`,
        latency: localResult.latency,
        _provider: "ollama_local",
        _endpoint: "native",
      };
    }

    // Local failed — an explicit capability may permit cloud fallback.
    console.log(
      `[ollama] LOCAL failed (${localResult.status}: ${(localResult.error || "").slice(0, 80)}), ` +
      `using explicitly permitted cloud fallback`
    );
  }

  // ── CLOUD THROUGH THE LOCAL OLLAMA: /api/chat, signed by the daemon ──
  // No key leaves YOUI, but the content leaves the device: this is Ollama
  // Cloud, allowed above only with allowCloudRoute, and never a fallback.
  if (endpoint.via === "daemon") {
    console.log(`[ollama] Using Ollama Cloud through ${logDestination(endpoint.base)} for model ${endpoint.model}`);
    const daemonBody = buildNativeBody(oaiMessages, {
      ...effectiveOptions,
      model: endpoint.model,
      think: await thinkFor(reasoningEffort, endpoint.base, null, endpoint.model, signal),
    });
    const daemonResult = await postWithRetry("/api/chat", daemonBody, effectiveTimeout, {
      base: endpoint.base, auth: null, signal,
    });
    if (daemonResult.ok) {
      const parsed = parseNativeResponse(daemonResult.data, endpoint.model);
      return {
        ok: true, status: daemonResult.status,
        ...parsed,
        latency: daemonResult.latency,
        _provider: "ollama_cloud", _endpoint: "native", _via: "local-daemon",
      };
    }
    return {
      ok: false,
      status: daemonResult.status,
      error: `Ollama Cloud through the local Ollama failed: ${daemonResult.error || "unknown"}`,
      latency: daemonResult.latency,
      _provider: "ollama_cloud",
      _endpoint: "native",
      _via: "local-daemon",
    };
  }

  if (!OLLAMA_API_KEY) {
    return {
      ok: false,
      status: 0,
      error: "Ollama cloud route is unavailable because OLLAMA_API_KEY is not configured and no local Ollama answered.",
      latency: "0.00",
      _provider: "ollama_cloud",
    };
  }

  // ── CLOUD: /v1/chat/completions (primary) ──────────────────────────
  // The direct API names a model without the daemon's ":cloud"/"-cloud".
  const directModel = ollamaDirectName(model);
  const cloudOptions = {
    ...effectiveOptions,
    model: directModel,
    think: await thinkFor(reasoningEffort, OLLAMA_CLOUD_BASE, OLLAMA_API_KEY, directModel, signal),
  };
  const v1Body = buildV1Body(oaiMessages, cloudOptions);
  const v1Result = await postWithRetry("/v1/chat/completions", v1Body, effectiveTimeout, {
    base: OLLAMA_CLOUD_BASE, auth: OLLAMA_API_KEY, signal,
  });

  if (v1Result.ok) {
    const parsed = parseV1Response(v1Result.data, directModel);
    return {
      ok: true, status: v1Result.status,
      ...parsed,
      latency: v1Result.latency,
      _provider: "ollama_cloud", _endpoint: "v1",
    };
  }

  // ── CLOUD: /api/chat (native fallback) ─────────────────────────────
  console.log(
    `[ollama] Cloud /v1 failed (${v1Result.status}: ${(v1Result.error || "").slice(0, 80)}), ` +
    `falling back to cloud native /api/chat`
  );

  const nativeBody = buildNativeBody(oaiMessages, cloudOptions);
  const nativeResult = await postWithRetry("/api/chat", nativeBody, effectiveTimeout, {
    base: OLLAMA_CLOUD_BASE, auth: OLLAMA_API_KEY, signal,
  });

  if (nativeResult.ok) {
    const parsed = parseNativeResponse(nativeResult.data, directModel);
    return {
      ok: true, status: nativeResult.status,
      ...parsed,
      latency: nativeResult.latency,
      _provider: "ollama_cloud", _endpoint: "native",
    };
  }

  // ── All failed ─────────────────────────────────────────────────────
  return {
    ok: false,
    status: nativeResult.status || v1Result.status,
    error: `All endpoints failed. Local: ${endpoint.provider === "ollama_local" ? "tried and failed" : "not available"}. Cloud /v1: ${v1Result.error || "unknown"} | Cloud /api/chat: ${nativeResult.error || "unknown"}`,
    latency: nativeResult.latency || v1Result.latency,
  };
}


/**
 * Dispatch multiple chat completions in parallel.
 *
 * Ollama Max supports 10 concurrent model slots. This function sends all
 * requests concurrently via Promise.all, preserving input order.
 *
 * Each entry in `calls` is { messages, options } matching ollamaChat args.
 * Returns an array of results (same format as ollamaChat's return value).
 * On per-call failure, the slot contains { ok: false, error }.
 *
 * Measured 2026-04-09: 10 parallel GLM 5.1 calls at effort=none complete in
 * ~2.2s wall vs ~10s serial — 4.65× throughput.
 */
export async function ollamaBatch(calls, { concurrency = 8 } = {}) {
  if (!calls?.length) return [];

  // Chunk into concurrency-limited batches
  const results = new Array(calls.length);
  for (let start = 0; start < calls.length; start += concurrency) {
    const chunk = calls.slice(start, start + concurrency);
    const promises = chunk.map((call, i) =>
      ollamaChat(call.messages, call.options)
        .then(r => { results[start + i] = r; })
        .catch(e => { results[start + i] = { ok: false, error: e.message }; })
    );
    await Promise.all(promises);
  }
  return results;
}


/**
 * List available Ollama models — local + cloud merged.
 */
export async function ollamaModels({
  allowCloudRoute = false,
  signal = null,
} = {}) {
  const result = {
    local: [],
    // Ollama Cloud models the local Ollama lists and forwards (not local, not free).
    cloudViaLocal: [],
    cloud: [],
    all: [],
    cloudAccess: allowCloudRoute ? "enabled" : "disabled",
  };

  // Local models
  const localInfo = await detectLocalOllama(signal, {
    allowRemoteEndpoint: allowCloudRoute,
  });
  if (localInfo?.running) {
    const remote = new Set(localInfo.remote || []);
    result.local = localInfo.models.filter(m => !remote.has(m));
    result.cloudViaLocal = localInfo.models.filter(m => remote.has(m));
  }

  // Cloud models
  let cloudRequest = null;
  if (allowCloudRoute && OLLAMA_API_KEY) try {
    cloudRequest = linkedAbortController(signal, 15000, "Ollama model listing timed out");
    const resp = await fetch(`${OLLAMA_CLOUD_BASE}/v1/models`, {
      headers: { "Authorization": `Bearer ${OLLAMA_API_KEY}` },
      signal: cloudRequest.controller.signal,
    });
    if (resp.ok) {
      const data = redactOllamaPayload(await resp.json());
      result.cloud = (data.data || data.models || []).map(m => m.id || m.name);
    }
  } catch (error) {
    if (signal?.aborted) throw abortReason(signal);
    result.cloudAccess = "unavailable";
  } finally {
    cloudRequest?.cleanup();
  } else if (allowCloudRoute) {
    result.cloudAccess = "unavailable";
  }

  // Merge: local first (they're free), then cloud through the local Ollama, then cloud-only
  const listed = new Set([...result.local, ...result.cloudViaLocal]);
  result.all = [...result.local, ...result.cloudViaLocal, ...result.cloud.filter(m => !listed.has(m))];

  return { ok: true, ...result };
}


/**
 * Quick connectivity test — local + cloud.
 */
export async function ollamaTest({
  allowCloudRoute = false,
  allowCloudFallback = false,
  signal = null,
} = {}) {
  const results = { timestamp: new Date().toISOString(), tests: [] };

  // Test 0: Local Ollama detection
  const localInfo = await detectLocalOllama(signal, {
    allowRemoteEndpoint: allowCloudRoute,
  });
  results.tests.push({
    name: "local", ok: localInfo.running, latency: "—",
    detail: localInfo.running
      ? `${localInfo.models.length} models: ${localInfo.models.join(", ")}`
      : "Not running or unreachable at localhost:11434",
  });

  // Test 1: Models endpoint (cloud)
  try {
    const start = performance.now();
    const models = await ollamaModels({ allowCloudRoute, signal });
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);
    const count = models.all?.length || 0;
    results.tests.push({
      name: "models", ok: true, latency: elapsed,
      detail: `${count} models (local: ${models.local?.length || 0}, cloud: ${models.cloud?.length || 0})`,
    });
  } catch (e) {
    results.tests.push({ name: "models", ok: false, detail: e.message });
  }

  // Test 2: Chat — prefer a really local model if available
  const installed = localInfo?.running
    ? localInfo.models.filter(m => !(localInfo.remote || []).includes(m))
    : [];
  const testModel = installed.length > 0
    ? installed[0]  // first local model
    : "glm-5.3-flash";    // cloud fallback
  try {
    const chat = await ollamaChat(
      [{ role: "user", content: "Respond with exactly: KINGDOM ONLINE" }],
      {
        model: testModel,
        maxTokens: 50,
        temperature: 0,
        signal,
        allowCloudRoute,
        allowCloudFallback,
      }
    );
    results.tests.push({
      name: "chat", ok: chat.ok, latency: chat.latency,
      detail: chat.ok
        ? (chat.content?.find(b => b.type === "text")?.text || "").slice(0, 100)
        : chat.error,
      usage: chat.usage,
      endpoint: chat._endpoint,
      provider: chat._provider,
      model: testModel,
    });
  } catch (e) {
    results.tests.push({ name: "chat", ok: false, detail: e.message });
  }

  // Test 3: Tool calling — prefer local model if available
  try {
    const tools = [{
      type: "function",
      function: {
        name: "get_info", description: "Get information",
        parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] }
      }
    }];
    const tc = await ollamaChat(
      [{ role: "user", content: "Look up the status of Kingdom OS" }],
      {
        model: testModel,
        maxTokens: 200,
        temperature: 0,
        tools,
        signal,
        allowCloudRoute,
        allowCloudFallback,
      }
    );
    const toolUse = tc.content?.find(b => b.type === "tool_use");
    results.tests.push({
      name: "tools", ok: tc.ok && !!toolUse, latency: tc.latency,
      detail: toolUse ? `${toolUse.name}(${JSON.stringify(toolUse.input)})` : (tc.content?.find(b => b.type === "text")?.text || "").slice(0, 80),
      endpoint: tc._endpoint,
      provider: tc._provider,
    });
  } catch (e) {
    results.tests.push({ name: "tools", ok: false, detail: e.message });
  }

  results.allOk = results.tests.every(t => t.ok);
  return results;
}


// ═════════════════════════════════════════════════════════════════════
// HTTP ENDPOINTS — mount in server.mjs handleRequest()
// ═════════════════════════════════════════════════════════════════════

/**
 * Handle /api/ollama/* routes. Call from handleRequest().
 * Returns true if handled, false if not an ollama route.
 */
export async function handleOllamaRoute(path, req, res, parseBody, policy = {}) {
  if (!path.startsWith("/api/ollama")) return false;

  const json = (data, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };

  if (path === "/api/ollama/test" && req.method === "POST") {
    const results = await ollamaTest(policy);
    json(results);
    return true;
  }

  if (path === "/api/ollama/models" && req.method === "GET") {
    const models = await ollamaModels(policy);
    json(models);
    return true;
  }

  if (path === "/api/ollama/chat" && req.method === "POST") {
    const body = await parseBody(req);
    const result = await ollamaChat(
      body.messages || [{ role: "user", content: body.prompt || body.message || "" }],
      {
        model: body.model || "glm-5.3-flash",
        system: body.system || null,
        maxTokens: body.max_tokens || 4096,
        temperature: body.temperature ?? 0.7,
        tools: body.tools || null,
        reasoningEffort: body.reasoning_effort || null,
        signal: policy.signal,
        allowCloudRoute: policy.allowCloudRoute === true,
        allowCloudFallback: policy.allowCloudFallback === true,
      }
    );
    json(result);
    return true;
  }

  if (["/api/ollama/test", "/api/ollama/models", "/api/ollama/chat"].includes(path)) {
    json({ error: "Method not allowed", path }, 405);
  } else {
    json({ error: "Unknown ollama route", path }, 404);
  }
  return true;
}


// ═════════════════════════════════════════════════════════════════════
// TOOL — for use inside executeTool() (Claude sessions)
// ═════════════════════════════════════════════════════════════════════

/**
 * Execute ollama tool call. Add to executeTool() switch statement:
 *   case "ollama": return await executeOllamaTool(input);
 */
export async function executeOllamaTool(input, policy = {}) {
  const action = input.action;

  if (action === "test") {
    const r = await ollamaTest(policy);
    const lines = [`Ollama Cloud Test — ${r.allOk ? "✅ ALL PASS" : "❌ SOME FAILED"}`];
    for (const t of r.tests) {
      lines.push(`  ${t.ok ? "✅" : "❌"} ${t.name}: ${t.detail} (${t.latency || "?"}s)${t.endpoint ? ` [${t.endpoint}]` : ""}`);
    }
    return lines.join("\n");
  }

  if (action === "models") {
    const r = await ollamaModels(policy);
    if (!r.ok) return `❌ Models failed: ${r.error}`;
    const lines = [`Ollama Models:`];
    if (r.local?.length) lines.push(`  LOCAL (${r.local.length}):`, ...r.local.map(m => `    • ${m} (free, localhost:11434)`));
    if (r.cloudViaLocal?.length) {
      lines.push(`  CLOUD THROUGH LOCAL OLLAMA (${r.cloudViaLocal.length}):`, ...r.cloudViaLocal.map(m => `    • ${m} (Ollama Cloud, needs models:ollama-cloud)`));
    }
    if (r.cloud?.length) lines.push(`  CLOUD (${r.cloud.length}):`, ...r.cloud.slice(0, 20).map(m => `    • ${m}`));
    return lines.join("\n");
  }

  if (action === "chat") {
    const r = await ollamaChat(
      [{ role: "user", content: input.message || input.prompt || "" }],
      {
        model: input.model || "glm-5.3-flash",
        system: input.system || null,
        maxTokens: input.max_tokens || 8000,
        temperature: input.temperature ?? 0.7,
        signal: policy.signal,
        allowCloudRoute: policy.allowCloudRoute === true,
        allowCloudFallback: policy.allowCloudFallback === true,
      }
    );
    if (!r.ok) return `❌ Chat failed: ${r.error}`;
    const text = r.content?.find(b => b.type === "text")?.text || "(no text)";
    return `${text}\n\n[${r.latency}s | ${r.usage?.total_tokens || "?"} tokens | model: ${r.model} | endpoint: ${r._endpoint || "v1"}]`;
  }

  if (action === "bench") {
    const prompts = [
      "Write a Python function implementing a priority queue with decrease-key.",
      "Explain the CAP theorem in 2 sentences.",
      "Write a Rust async function that retries HTTP requests with exponential backoff.",
    ];
    const stats = [];
    for (let i = 0; i < prompts.length; i++) {
      const r = await ollamaChat(
        [{ role: "user", content: prompts[i] }],
        {
          model: input.model || "glm-5.3-flash",
          maxTokens: 500,
          temperature: 0.3,
          signal: policy.signal,
          allowCloudRoute: policy.allowCloudRoute === true,
          allowCloudFallback: policy.allowCloudFallback === true,
        }
      );
      if (r.ok) {
        const rate = r.usage.output_tokens / parseFloat(r.latency);
        stats.push({
          prompt: prompts[i].slice(0, 40), latency: r.latency,
          tokens: r.usage.output_tokens, rate: rate.toFixed(1),
          endpoint: r._endpoint,
        });
      }
    }
    if (!stats.length) return "❌ Benchmark failed — no successful rounds";
    const avgLat = (stats.reduce((s, r) => s + parseFloat(r.latency), 0) / stats.length).toFixed(2);
    const avgRate = (stats.reduce((s, r) => s + parseFloat(r.rate), 0) / stats.length).toFixed(1);
    return `Ollama Benchmark (${stats.length} rounds):\n` +
      stats.map(s => `  ✅ ${s.latency}s | ${s.tokens} tok | ${s.rate} tok/s [${s.endpoint}] — ${s.prompt}...`).join("\n") +
      `\n\nAvg latency: ${avgLat}s | Avg throughput: ${avgRate} tok/s`;
  }

  return "Ollama tool: action=test|models|chat|bench. For chat: message='...', model='glm-5.3-flash'";
}
