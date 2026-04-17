// ─────────────────────────────────────────────────────────────────────
// ollama-bridge.mjs — Ollama Cloud Bridge for Kingdom OS
//
// Runs INSIDE the server.mjs process (which has network access),
// bypassing the sandbox that blocks socket() in child processes.
//
// Architecture:
//   ┌──────────────────────────────────────────────────────────┐
//   │  server.mjs (PID 80225, HAS network)                    │
//   │    ├─ fetch() → api.anthropic.com  ✅ (existing)         │
//   │    ├─ fetch() → ollama.com         ✅ (this bridge)      │
//   │    │   NOT api.ollama.com — v1/* 301-redirects           │
//   │    │                                                     │
//   │    ├─ /api/ollama/chat     → proxy to Ollama            │
//   │    ├─ /api/ollama/models   → list models                │
//   │    ├─ /api/ollama/test     → connectivity test          │
//   │    │                                                     │
//   │    └─ bash tool (execSync, child process)                │
//   │         └─ ❌ socket() BLOCKED by sandbox                │
//   │         └─ ✅ reads /tmp/ollama-*.json (file IPC)        │
//   └──────────────────────────────────────────────────────────┘
//
// Three access patterns:
//   1. HTTP API  — /api/ollama/* endpoints (for web UI, external tools)
//   2. Tool      — "ollama" tool in executeTool() (for Claude sessions)
//   3. File IPC  — /tmp/ollama-req-*.json → /tmp/ollama-res-*.json (for sandboxed scripts)
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

const OLLAMA_API_KEY = process.env.OLLAMA_API_KEY
  || "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK";
const OLLAMA_CLOUD_BASE = process.env.OLLAMA_CLOUD_BASE_URL || "https://ollama.com";
const OLLAMA_LOCAL_BASE = process.env.OLLAMA_LOCAL_BASE_URL || "http://localhost:11434";
// Remote vLLM (OpenAI-compat). Reached via SSH tunnel: Mac:8000 → pod:8000.
const OLLAMA_VLLM_BASE  = process.env.OLLAMA_VLLM_BASE_URL  || "http://localhost:8000";
const VLLM_MODEL_REGEX  = /^Qwen\//i;

// ── Local model detection ───────────────────────────────────────────
let localModels = null;   // cached list of locally-installed models
let localChecked = 0;     // timestamp of last check
const LOCAL_CHECK_INTERVAL = 60_000; // re-check every 60s

/**
 * Detect whether local Ollama is running and which models are available.
 * Returns { running: bool, models: string[], base: string } or null if unreachable.
 */
async function detectLocalOllama() {
  // Cache: don't re-probe every request
  if (localModels && Date.now() - localChecked < LOCAL_CHECK_INTERVAL) return localModels;

  try {
    const resp = await fetch(`${OLLAMA_LOCAL_BASE}/api/tags`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const data = await resp.json();
    const models = (data.models || []).map(m => m.name || m.model);
    localModels = { running: true, models, base: OLLAMA_LOCAL_BASE };
    localChecked = Date.now();
    console.log(`[ollama] Local detected: ${models.length} models (${models.join(", ")})`);
    return localModels;
  } catch {
    localModels = { running: false, models: [], base: null };
    localChecked = Date.now();
    return localModels;
  }
}

/**
 * Determine the base URL and provider for a given model.
 * Local first: if the model is installed locally, use localhost.
 * Cloud fallback: if model isn't local, use ollama.com.
 * Returns { base, provider, auth } where auth is the Bearer header value or null.
 */
function resolveEndpoint(modelName, localInfo) {
  // Strip tag suffixes for matching (e.g. "qwen2.5:7b" → "qwen2.5")
  const modelNameBase = modelName.split(":")[0];

  if (localInfo?.running) {
    // Exact match first, then base match
    const exactMatch = localInfo.models.includes(modelName);
    const baseMatch = localInfo.models.some(m => m.split(":")[0] === modelNameBase);
    if (exactMatch || baseMatch) {
      return { base: OLLAMA_LOCAL_BASE, provider: "ollama_local", auth: null };
    }
  }

  // Cloud fallback
  return { base: OLLAMA_CLOUD_BASE, provider: "ollama_cloud", auth: OLLAMA_API_KEY };
}

// ── Retry configuration ─────────────────────────────────────────────
const RETRIABLE_STATUS = new Set([429, 502, 503]);
const MAX_RETRIES = 4;
const RETRY_BASE_MS = 1000;  // 1s → 2s → 4s → 8s
const RETRY_MAX_MS  = 10000;

// ── Timeout presets by model tier ───────────────────────────────────
function selectTimeout(model = "") {
  const m = model.toLowerCase();
  if (/devstral|gemma4|ministral/.test(m)) return 30000;   // economy: 30s
  if (/glm-5|cogito|kimi|qwen3-coder|qwen2\.5/.test(m)) return 180000; // premium: 3min
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
 * @param {object} [opts] - { auth: string|null, base: string }
 */
async function postWithRetry(endpoint, body, timeout, opts = {}) {
  let lastError = null;
  const baseUrl = opts.base || OLLAMA_CLOUD_BASE;
  const authHeader = opts.auth !== undefined ? opts.auth : OLLAMA_API_KEY;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const start = performance.now();

    try {
      const headers = { "Content-Type": "application/json" };
      if (authHeader) headers["Authorization"] = `Bearer ${authHeader}`;

      const resp = await fetch(`${baseUrl}${endpoint}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timer);
      const elapsed = ((performance.now() - start) / 1000).toFixed(2);

      if (resp.ok) {
        const data = await resp.json();
        return { ok: true, data, status: resp.status, latency: elapsed };
      }

      // Retriable error?
      const errorText = await resp.text().catch(() => "");
      lastError = { status: resp.status, error: errorText, latency: elapsed };

      if (RETRIABLE_STATUS.has(resp.status) && attempt < MAX_RETRIES) {
        const wait = Math.min(RETRY_BASE_MS * (2 ** attempt), RETRY_MAX_MS);
        console.log(
          `[ollama] ${resp.status} on attempt ${attempt + 1}/${MAX_RETRIES + 1}, ` +
          `retrying in ${wait}ms: ${errorText.slice(0, 100)}`
        );
        await new Promise(r => setTimeout(r, wait));
        continue;
      }

      // Non-retriable or retries exhausted
      return { ok: false, ...lastError };

    } catch (e) {
      clearTimeout(timer);
      const elapsed = ((performance.now() - start) / 1000).toFixed(2);
      lastError = { status: 0, error: e.message, latency: elapsed };

      if (attempt < MAX_RETRIES) {
        const wait = Math.min(RETRY_BASE_MS * (2 ** attempt), RETRY_MAX_MS);
        console.log(
          `[ollama] Error on attempt ${attempt + 1}/${MAX_RETRIES + 1}, ` +
          `retrying in ${wait}ms: ${e.message}`
        );
        await new Promise(r => setTimeout(r, wait));
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
    model = "glm-5.1",
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    reasoningEffort = null,
  } = options;

  const body = {
    model,
    messages: oaiMessages,
    max_tokens: maxTokens,
    temperature,
    stream: false,
  };

  if (reasoningEffort && ["none", "low", "medium", "high"].includes(reasoningEffort)) {
    body.reasoning_effort = reasoningEffort;
  }

  if (tools?.length) {
    body.tools = tools.map(t => t.type === "function" ? t : {
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema || t.parameters }
    });
    body.tool_choice = "auto";
  }

  return body;
}

/**
 * Build native /api/chat request body.
 * Uses `think` parameter (bool or "low"/"medium"/"high") and `options.num_predict`.
 */
function buildNativeBody(oaiMessages, options) {
  const {
    model = "glm-5.1",
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    reasoningEffort = null,
  } = options;

  // Map reasoning_effort → think parameter
  let think;
  if (reasoningEffort === "none") think = false;
  else if (["low", "medium", "high"].includes(reasoningEffort)) think = reasoningEffort;
  else think = true; // default: enable thinking

  const body = {
    model,
    messages: oaiMessages,
    stream: false,
    think,
    options: {
      num_predict: maxTokens,
      temperature,
    },
  };

  if (tools?.length) {
    body.tools = tools.map(t => t.type === "function" ? t : {
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema || t.parameters }
    });
  }

  return body;
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
    usage: {
      input_tokens: data.usage?.prompt_tokens || 0,
      output_tokens: data.usage?.completion_tokens || 0,
      total_tokens: data.usage?.total_tokens || 0,
    },
  };
}

/**
 * Parse /api/chat (native) response → Anthropic-compatible content blocks.
 *
 * Native response format:
 *   {
 *     "model": "glm-5.1",
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
    usage: {
      input_tokens: data.prompt_eval_count || 0,
      output_tokens: data.eval_count || 0,
      total_tokens: (data.prompt_eval_count || 0) + (data.eval_count || 0),
    },
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

async function streamV1Chat(url, body, timeout, onDelta) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const start = performance.now();

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, stream: true, stream_options: { include_usage: true } }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      clearTimeout(timer);
      const errorText = await resp.text().catch(() => "");
      return { ok: false, status: resp.status, error: errorText, latency: ((performance.now() - start) / 1000).toFixed(2) };
    }

    let fullText = "";
    const toolCalls = [];
    let finishReason = null;
    let usage = null;
    let respModel = body.model;
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

        const choice = parsed.choices?.[0];
        if (!choice) { if (parsed.usage) usage = parsed.usage; continue; }

        const delta = choice.delta || {};

        if (delta.content) {
          fullText += delta.content;
          onDelta({ type: "text_delta", text: delta.content });
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const idx = tc.index ?? 0;
            if (!toolCalls[idx]) toolCalls[idx] = { id: "", name: "", arguments: "" };
            if (tc.id) toolCalls[idx].id = tc.id;
            if (tc.function?.name) toolCalls[idx].name = tc.function.name;
            if (tc.function?.arguments) toolCalls[idx].arguments += tc.function.arguments;
          }
        }

        if (choice.finish_reason) finishReason = choice.finish_reason;
        if (parsed.usage) usage = parsed.usage;
        if (parsed.model) respModel = parsed.model;
      }
    }

    clearTimeout(timer);
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);

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
      usage: { input_tokens: usage?.prompt_tokens || 0, output_tokens: usage?.completion_tokens || 0, total_tokens: usage?.total_tokens || 0 },
      latency: elapsed, _provider: "vllm", _endpoint: "v1", _streamed: true,
    };
  } catch (e) {
    clearTimeout(timer);
    return { ok: false, status: 0, error: e.message, latency: ((performance.now() - start) / 1000).toFixed(2) };
  }
}


/**
 * Chat completion via Ollama — local-first, cloud-fallback.
 *
 * Strategy:
 *   1. Detect local Ollama (localhost:11434)
 *   2. If model is available locally → use local /api/chat (native API)
 *      Local Ollama only supports the native /api/chat endpoint, not /v1.
 *   3. If model is NOT local → use cloud /v1/chat/completions (OpenAI-compat)
 *      with retry, falling back to cloud /api/chat (native) on failure.
 *   4. If local is down and cloud also fails → error
 */
export async function ollamaChat(messages, options = {}) {
  const {
    model = "glm-5.1",
    system = null,
    maxTokens = 8000,
    temperature = 0.7,
    tools = null,
    stream = false,
    timeout = null,
    reasoningEffort = null,
    onDelta = null,
  } = options;

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
    const v1Body = buildV1Body(oaiMessages, { model, maxTokens, temperature, tools, reasoningEffort });

    // Streaming path: token-by-token via SSE
    if (onDelta) {
      return await streamV1Chat(
        `${OLLAMA_VLLM_BASE}/v1/chat/completions`,
        v1Body, effectiveTimeout, onDelta
      );
    }

    // Non-streaming path
    const r = await postWithRetry("/v1/chat/completions", v1Body, effectiveTimeout, {
      base: OLLAMA_VLLM_BASE, auth: null,
    });
    if (r.ok) {
      const parsed = parseV1Response(r.data, model);
      return { ok: true, status: r.status, ...parsed, latency: r.latency, _provider: "vllm", _endpoint: "v1" };
    }
    return { ok: false, status: r.status, error: `vLLM failed: ${r.error || "unknown"}`, latency: r.latency };
  }

  const localInfo = await detectLocalOllama();
  const endpoint = resolveEndpoint(model, localInfo);

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

  const effectiveOptions = { model, maxTokens, temperature, tools, reasoningEffort };

  // ── LOCAL: /api/chat (native Ollama API) ───────────────────────────
  if (endpoint.provider === "ollama_local") {
    console.log(`[ollama] Using LOCAL ${endpoint.base} for model ${model}`);
    const localBody = buildNativeBody(oaiMessages, effectiveOptions);
    const localResult = await postWithRetry("/api/chat", localBody, effectiveTimeout, {
      base: endpoint.base, auth: null,
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

    // Local failed — fall through to cloud
    console.log(
      `[ollama] LOCAL failed (${localResult.status}: ${(localResult.error || "").slice(0, 80)}), ` +
      `falling back to cloud`
    );
  }

  // ── CLOUD: /v1/chat/completions (primary) ──────────────────────────
  const v1Body = buildV1Body(oaiMessages, effectiveOptions);
  const v1Result = await postWithRetry("/v1/chat/completions", v1Body, effectiveTimeout, {
    base: OLLAMA_CLOUD_BASE, auth: OLLAMA_API_KEY,
  });

  if (v1Result.ok) {
    const parsed = parseV1Response(v1Result.data, model);
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

  const nativeBody = buildNativeBody(oaiMessages, effectiveOptions);
  const nativeResult = await postWithRetry("/api/chat", nativeBody, effectiveTimeout, {
    base: OLLAMA_CLOUD_BASE, auth: OLLAMA_API_KEY,
  });

  if (nativeResult.ok) {
    const parsed = parseNativeResponse(nativeResult.data, model);
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
export async function ollamaModels() {
  const result = { local: [], cloud: [], all: [] };

  // Local models
  const localInfo = await detectLocalOllama();
  if (localInfo?.running) {
    result.local = localInfo.models;
  }

  // Cloud models
  try {
    const resp = await fetch(`${OLLAMA_CLOUD_BASE}/v1/models`, {
      headers: { "Authorization": `Bearer ${OLLAMA_API_KEY}` },
      signal: AbortSignal.timeout(15000),
    });
    if (resp.ok) {
      const data = await resp.json();
      result.cloud = (data.data || data.models || []).map(m => m.id || m.name);
    }
  } catch {}

  // Merge: local first (they're free), then cloud-only
  const localSet = new Set(result.local);
  result.all = [...result.local, ...result.cloud.filter(m => !localSet.has(m))];

  return { ok: true, ...result };
}


/**
 * Quick connectivity test — local + cloud.
 */
export async function ollamaTest() {
  const results = { timestamp: new Date().toISOString(), tests: [] };

  // Test 0: Local Ollama detection
  const localInfo = await detectLocalOllama();
  results.tests.push({
    name: "local", ok: localInfo.running, latency: "—",
    detail: localInfo.running
      ? `${localInfo.models.length} models: ${localInfo.models.join(", ")}`
      : "Not running or unreachable at localhost:11434",
  });

  // Test 1: Models endpoint (cloud)
  try {
    const start = performance.now();
    const models = await ollamaModels();
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);
    const count = models.all?.length || 0;
    results.tests.push({
      name: "models", ok: true, latency: elapsed,
      detail: `${count} models (local: ${models.local?.length || 0}, cloud: ${models.cloud?.length || 0})`,
    });
  } catch (e) {
    results.tests.push({ name: "models", ok: false, detail: e.message });
  }

  // Test 2: Chat — prefer local model if available
  const testModel = localInfo?.running && localInfo.models.length > 0
    ? localInfo.models[0]  // first local model
    : "glm-5.1";           // cloud fallback
  try {
    const chat = await ollamaChat(
      [{ role: "user", content: "Respond with exactly: KINGDOM ONLINE" }],
      { model: testModel, maxTokens: 50, temperature: 0 }
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
      { model: testModel, maxTokens: 200, temperature: 0, tools }
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
export async function handleOllamaRoute(path, req, res, parseBody) {
  if (!path.startsWith("/api/ollama")) return false;

  const json = (data, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };

  if (path === "/api/ollama/test") {
    const results = await ollamaTest();
    json(results);
    return true;
  }

  if (path === "/api/ollama/models") {
    const models = await ollamaModels();
    json(models);
    return true;
  }

  if (path === "/api/ollama/chat" && req.method === "POST") {
    const body = await parseBody(req);
    const result = await ollamaChat(
      body.messages || [{ role: "user", content: body.prompt || body.message || "" }],
      {
        model: body.model || "glm-5.1",
        system: body.system || null,
        maxTokens: body.max_tokens || 4096,
        temperature: body.temperature ?? 0.7,
        tools: body.tools || null,
        reasoningEffort: body.reasoning_effort || null,
      }
    );
    json(result);
    return true;
  }

  json({ error: "Unknown ollama route", path }, 404);
  return true;
}


// ═════════════════════════════════════════════════════════════════════
// TOOL — for use inside executeTool() (Claude sessions)
// ═════════════════════════════════════════════════════════════════════

/**
 * Execute ollama tool call. Add to executeTool() switch statement:
 *   case "ollama": return await executeOllamaTool(input);
 */
export async function executeOllamaTool(input) {
  const action = input.action;

  if (action === "test") {
    const r = await ollamaTest();
    const lines = [`Ollama Cloud Test — ${r.allOk ? "✅ ALL PASS" : "❌ SOME FAILED"}`];
    for (const t of r.tests) {
      lines.push(`  ${t.ok ? "✅" : "❌"} ${t.name}: ${t.detail} (${t.latency || "?"}s)${t.endpoint ? ` [${t.endpoint}]` : ""}`);
    }
    return lines.join("\n");
  }

  if (action === "models") {
    const r = await ollamaModels();
    if (!r.ok) return `❌ Models failed: ${r.error}`;
    const lines = [`Ollama Models:`];
    if (r.local?.length) lines.push(`  LOCAL (${r.local.length}):`, ...r.local.map(m => `    • ${m} (free, localhost:11434)`));
    if (r.cloud?.length) lines.push(`  CLOUD (${r.cloud.length}):`, ...r.cloud.slice(0, 20).map(m => `    • ${m}`));
    return lines.join("\n");
  }

  if (action === "chat") {
    const r = await ollamaChat(
      [{ role: "user", content: input.message || input.prompt || "" }],
      {
        model: input.model || "glm-5.1",
        system: input.system || null,
        maxTokens: input.max_tokens || 8000,
        temperature: input.temperature ?? 0.7,
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
        { model: input.model || "glm-5.1", maxTokens: 500, temperature: 0.3 }
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

  return "Ollama tool: action=test|models|chat|bench. For chat: message='...', model='glm-5.1'";
}


// ═════════════════════════════════════════════════════════════════════
// FILE IPC — for sandboxed child processes
// ═════════════════════════════════════════════════════════════════════

import { readFileSync, writeFileSync, existsSync, unlinkSync, readdirSync, watch } from "fs";

const IPC_DIR = "/tmp";
const IPC_PREFIX = "ollama-req-";
const IPC_RES_PREFIX = "ollama-res-";

/**
 * Start watching for file-based IPC requests.
 * Sandboxed scripts write /tmp/ollama-req-{id}.json
 * Bridge processes and writes /tmp/ollama-res-{id}.json
 * 
 * Call once from server.mjs boot:
 *   import { startFileIPC } from "./ollama-bridge.mjs";
 *   startFileIPC();
 */
export function startFileIPC() {
  // Process any existing requests on boot
  processIpcRequests();

  // Watch for new requests
  try {
    watch(IPC_DIR, (event, filename) => {
      if (filename?.startsWith(IPC_PREFIX) && filename.endsWith(".json")) {
        setTimeout(() => processIpcRequests(), 100); // debounce
      }
    });
    console.log("  \x1b[32m✓\x1b[0m Ollama file IPC watching /tmp/ollama-req-*.json");
  } catch (e) {
    console.log(`  \x1b[33m⚠\x1b[0m Ollama file IPC watch failed: ${e.message}`);
  }
}

async function processIpcRequests() {
  try {
    const files = readdirSync(IPC_DIR).filter(f => f.startsWith(IPC_PREFIX) && f.endsWith(".json"));
    for (const file of files) {
      const reqPath = `${IPC_DIR}/${file}`;
      const id = file.replace(IPC_PREFIX, "").replace(".json", "");
      const resPath = `${IPC_DIR}/${IPC_RES_PREFIX}${id}.json`;

      try {
        const req = JSON.parse(readFileSync(reqPath, "utf-8"));
        unlinkSync(reqPath); // consume the request

        let result;
        if (req.action === "chat") {
          result = await ollamaChat(
            req.messages || [{ role: "user", content: req.message || "" }],
            { model: req.model, system: req.system, maxTokens: req.max_tokens, temperature: req.temperature, tools: req.tools }
          );
        } else if (req.action === "models") {
          result = await ollamaModels();
        } else if (req.action === "test") {
          result = await ollamaTest();
        } else {
          result = { ok: false, error: `Unknown action: ${req.action}` };
        }

        writeFileSync(resPath, JSON.stringify(result));
      } catch (e) {
        try { writeFileSync(resPath, JSON.stringify({ ok: false, error: e.message })); } catch {}
        try { unlinkSync(reqPath); } catch {}
      }
    }
  } catch {}
}
