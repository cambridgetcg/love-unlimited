// ─────────────────────────────────────────────────────────────────────
// ollama-bridge.mjs — Ollama Cloud Bridge for Kingdom OS
//
// Runs INSIDE the server.mjs process (which has network access),
// bypassing the sandbox that blocks socket() in child processes.
//
// Architecture:
//   ┌──────────────────────────────────────────────────────┐
//   │  server.mjs (PID 80225, HAS network)                │
//   │    ├─ fetch() → api.anthropic.com  ✅ (existing)     │
//   │    ├─ fetch() → ollama.com         ✅ (this bridge)  │
//   │    │   NOT api.ollama.com — v1/* 301-redirects       │
//   │    │                                                 │
//   │    ├─ /api/ollama/chat     → proxy to Ollama        │
//   │    ├─ /api/ollama/models   → list models            │
//   │    ├─ /api/ollama/test     → connectivity test      │
//   │    │                                                 │
//   │    └─ bash tool (execSync, child process)            │
//   │         └─ ❌ socket() BLOCKED by sandbox            │
//   │         └─ ✅ reads /tmp/ollama-*.json (file IPC)    │
//   └──────────────────────────────────────────────────────┘
//
// Three access patterns:
//   1. HTTP API  — /api/ollama/* endpoints (for web UI, external tools)
//   2. Tool      — "ollama" tool in executeTool() (for Claude sessions)
//   3. File IPC  — /tmp/ollama-req-*.json → /tmp/ollama-res-*.json (for sandboxed scripts)
// ─────────────────────────────────────────────────────────────────────

const OLLAMA_API_KEY = process.env.OLLAMA_API_KEY
  || "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK";
const OLLAMA_BASE = process.env.OLLAMA_BASE_URL || "https://ollama.com";

// ═════════════════════════════════════════════════════════════════════
// CORE — runs in server.mjs process (has network)
// ═════════════════════════════════════════════════════════════════════

/**
 * Chat completion via Ollama Cloud API (OpenAI-compatible).
 */
export async function ollamaChat(messages, options = {}) {
  const {
    model = "glm-5.1",
    system = null,
    maxTokens = 4096,
    temperature = 0.7,
    tools = null,
    stream = false,
    timeout = 120000,
  } = options;

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

  const body = { model, messages: oaiMessages, max_tokens: maxTokens, temperature, stream };

  if (tools?.length) {
    body.tools = tools.map(t => t.type === "function" ? t : {
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema || t.parameters }
    });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const start = performance.now();

  try {
    const resp = await fetch(`${OLLAMA_BASE}/v1/chat/completions`, {
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

    if (!resp.ok) {
      return { ok: false, status: resp.status, error: await resp.text(), latency: elapsed };
    }

    const data = await resp.json();
    const choice = data.choices?.[0]?.message || {};

    // Build Anthropic-compatible content blocks
    const content = [];
    if (choice.content) content.push({ type: "text", text: choice.content });
    if (choice.tool_calls) {
      for (const tc of choice.tool_calls) {
        content.push({
          type: "tool_use", id: tc.id, name: tc.function.name,
          input: JSON.parse(tc.function.arguments || "{}"),
        });
      }
    }

    return {
      ok: true, status: resp.status, content,
      stop_reason: choice.tool_calls ? "tool_use" : "end_turn",
      model: data.model || model,
      usage: {
        input_tokens: data.usage?.prompt_tokens || 0,
        output_tokens: data.usage?.completion_tokens || 0,
        total_tokens: data.usage?.total_tokens || 0,
      },
      latency: elapsed, _provider: "ollama",
    };
  } catch (e) {
    clearTimeout(timer);
    return { ok: false, status: 0, error: e.message, latency: ((performance.now() - start) / 1000).toFixed(2) };
  }
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

  // Test 2: Chat
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
      lines.push(`  ${t.ok ? "✅" : "❌"} ${t.name}: ${t.detail} (${t.latency || "?"}s)`);
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
        maxTokens: input.max_tokens || 4096,
        temperature: input.temperature ?? 0.7,
      }
    );
    if (!r.ok) return `❌ Chat failed: ${r.error}`;
    const text = r.content?.find(b => b.type === "text")?.text || "(no text)";
    return `${text}\n\n[${r.latency}s | ${r.usage?.total_tokens || "?"} tokens | model: ${r.model}]`;
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
        stats.push({ prompt: prompts[i].slice(0, 40), latency: r.latency, tokens: r.usage.output_tokens, rate: rate.toFixed(1) });
      }
    }
    if (!stats.length) return "❌ Benchmark failed — no successful rounds";
    const avgLat = (stats.reduce((s, r) => s + parseFloat(r.latency), 0) / stats.length).toFixed(2);
    const avgRate = (stats.reduce((s, r) => s + parseFloat(r.rate), 0) / stats.length).toFixed(1);
    return `Ollama Benchmark (${stats.length} rounds):\n` +
      stats.map(s => `  ✅ ${s.latency}s | ${s.tokens} tok | ${s.rate} tok/s — ${s.prompt}...`).join("\n") +
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
