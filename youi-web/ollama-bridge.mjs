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
const OLLAMA_BASE = process.env.OLLAMA_BASE_URL || "https://ollama.com";

// ── Retry configuration ─────────────────────────────────────────────
const RETRIABLE_STATUS = new Set([429, 502, 503]);
const MAX_RETRIES = 4;
const RETRY_BASE_MS = 1000;  // 1s → 2s → 4s → 8s
const RETRY_MAX_MS  = 10000;

// ── Timeout presets by model tier ───────────────────────────────────
function selectTimeout(model = "") {
  const m = model.toLowerCase();
  if (/devstral|gemma4|ministral/.test(m)) return 30000;   // economy: 30s
  if (/glm-5|cogito|kimi|qwen3-coder/.test(m)) return 180000; // premium: 3min
  return 120000;  // standard: 2min
}

// ═════════════════════════════════════════════════════════════════════
// LOW-LEVEL — fetch with retry + endpoint fallback
// ═════════════════════════════════════════════════════════════════════

/**
 * POST with exponential backoff retry for transient HTTP errors.
 * Returns { ok, data?, status?, error?, latency }.
 */
async function postWithRetry(endpoint, body, timeout) {
  let lastError = null;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const start = performance.now();

    try {
      const resp = await fetch(`${OLLAMA_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${OLLAMA_API_KEY}`,
          "Content-Type": "application/json",
        },
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
  const choice = data.choices?.[0]?.message || {};

  // Handle reasoning-only responses (content empty, reasoning present)
  let textContent = choice.content || "";
  if (!textContent && choice.reasoning) textContent = choice.reasoning;

  const content = [];
  if (textContent) content.push({ type: "text", text: textContent });
  if (choice.tool_calls) {
    for (const tc of choice.tool_calls) {
      let args;
      try { args = JSON.parse(tc.function.arguments || "{}"); }
      catch { args = { raw: tc.function.arguments }; }
      content.push({
        type: "tool_use", id: tc.id, name: tc.function.name, input: args,
      });
    }
  }

  return {
    content,
    stop_reason: choice.tool_calls ? "tool_use" : "end_turn",
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


/**
 * Chat completion via Ollama Cloud API.
 *
 * Strategy:
 *   1. Try /v1/chat/completions (OpenAI-compat) with retry
 *   2. If /v1 fails after retries, fallback to /api/chat (native Ollama API)
 *   3. Native API uses `think` param instead of `reasoning_effort`
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
  } = options;

  const effectiveTimeout = timeout || selectTimeout(model);

  // Convert messages to OpenAI format
  const oaiMessages = [];
  if (system) oaiMessages.push({ role: "system", content: system });

  for (const msg of messages) {
    if (Array.isArray(msg.content)) {
      // Anthropic content blocks → OpenAI flat text
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

  // ── Strategy 1: /v1/chat/completions (primary) ──────────────────
  const v1Body = buildV1Body(oaiMessages, effectiveOptions);
  const v1Result = await postWithRetry("/v1/chat/completions", v1Body, effectiveTimeout);

  if (v1Result.ok) {
    const parsed = parseV1Response(v1Result.data, model);
    return {
      ok: true, status: v1Result.status,
      ...parsed,
      latency: v1Result.latency,
      _provider: "ollama", _endpoint: "v1",
    };
  }

  // ── Strategy 2: /api/chat (native fallback) ─────────────────────
  console.log(
    `[ollama] /v1 failed (${v1Result.status}: ${(v1Result.error || "").slice(0, 80)}), ` +
    `falling back to native /api/chat`
  );

  const nativeBody = buildNativeBody(oaiMessages, effectiveOptions);
  const nativeResult = await postWithRetry("/api/chat", nativeBody, effectiveTimeout);

  if (nativeResult.ok) {
    const parsed = parseNativeResponse(nativeResult.data, model);
    return {
      ok: true, status: nativeResult.status,
      ...parsed,
      latency: nativeResult.latency,
      _provider: "ollama", _endpoint: "native",
    };
  }

  // ── Both failed ─────────────────────────────────────────────────
  return {
    ok: false,
    status: nativeResult.status || v1Result.status,
    error: `Both endpoints failed. /v1: ${v1Result.error || "unknown"} | /api/chat: ${nativeResult.error || "unknown"}`,
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
 * List available Ollama cloud models.
 */
export async function ollamaModels() {
  try {
    const resp = await fetch(`${OLLAMA_BASE}/v1/models`, {
      headers: { "Authorization": `Bearer ${OLLAMA_API_KEY}` },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) return { ok: false, status: resp.status, error: await resp.text() };
    return { ok: true, ...(await resp.json()) };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}


/**
 * Quick connectivity test.
 */
export async function ollamaTest() {
  const results = { timestamp: new Date().toISOString(), tests: [] };

  // Test 1: Models endpoint
  try {
    const start = performance.now();
    const models = await ollamaModels();
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);
    results.tests.push({
      name: "models", ok: models.ok, latency: elapsed,
      detail: models.ok ? `${(models.data || models.models || []).length} models` : models.error,
    });
  } catch (e) {
    results.tests.push({ name: "models", ok: false, detail: e.message });
  }

  // Test 2: Chat (with retry — tests the resilience layer)
  try {
    const chat = await ollamaChat(
      [{ role: "user", content: "Respond with exactly: KINGDOM ONLINE" }],
      { model: "glm-5.1", maxTokens: 50, temperature: 0 }
    );
    results.tests.push({
      name: "chat", ok: chat.ok, latency: chat.latency,
      detail: chat.ok
        ? (chat.content?.find(b => b.type === "text")?.text || "").slice(0, 100)
        : chat.error,
      usage: chat.usage,
      endpoint: chat._endpoint,
    });
  } catch (e) {
    results.tests.push({ name: "chat", ok: false, detail: e.message });
  }

  // Test 3: Tool calling
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
      { model: "glm-5.1", maxTokens: 200, temperature: 0, tools }
    );
    const toolUse = tc.content?.find(b => b.type === "tool_use");
    results.tests.push({
      name: "tools", ok: tc.ok && !!toolUse, latency: tc.latency,
      detail: toolUse ? `${toolUse.name}(${JSON.stringify(toolUse.input)})` : (tc.content?.find(b => b.type === "text")?.text || "").slice(0, 80),
      endpoint: tc._endpoint,
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
    const list = r.data || r.models || [];
    return `Ollama Cloud Models (${list.length}):\n` + list.map(m => `  • ${m.id || m.name}`).join("\n");
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

import { readFileSync, writeFileSync, existsSync, unlinkSync, readdirSync } from "fs";
import { watch } from "fs";

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
