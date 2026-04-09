// ─────────────────────────────────────────────────────────────────────
// ollama-provider.mjs — Ollama Cloud Provider for Kingdom OS
//
// Bridges sovereign.mjs architecture to Ollama's OpenAI-compatible API.
// GLM 5.1 and any other Ollama cloud model.
//
// Usage:
//   node ollama-provider.mjs test              # Test connectivity
//   node ollama-provider.mjs chat "message"    # Quick chat
//   node ollama-provider.mjs bench             # Benchmark
//
// Integration with sovereign.mjs:
//   node sovereign.mjs --model ollama:glm-5.1 "your task"
//   (requires sovereign.mjs patch — see README)
// ─────────────────────────────────────────────────────────────────────

const OLLAMA_API_KEY = process.env.OLLAMA_API_KEY 
  || "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK";
// IMPORTANT: api.ollama.com/v1/* returns 301 that breaks POST.
// Use ollama.com for OpenAI-compat endpoint.
const OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL || "https://ollama.com";

// ═════════════════════════════════════════════════════════════════════
// CORE API
// ═════════════════════════════════════════════════════════════════════

/**
 * Call Ollama Cloud API (OpenAI-compatible)
 * Translates between Anthropic message format and OpenAI format.
 */
export async function ollamaChat(messages, options = {}) {
  const {
    model = "glm-5.1",
    system = null,
    maxTokens = 8000,  // GLM 5.1 reasoning needs room; 4K minimum for content
    temperature = 0.7,
    tools = null,
    stream = false,
    timeout = 300000,  // 5 min — GLM 5.1 reasoning can take 60-120s
  } = options;

  // Build messages array (OpenAI format)
  const oaiMessages = [];
  if (system) {
    oaiMessages.push({ role: "system", content: system });
  }

  // Convert Anthropic-style messages to OpenAI-style
  for (const msg of messages) {
    if (msg.role === "user" || msg.role === "assistant" || msg.role === "system") {
      // Handle Anthropic content blocks
      if (Array.isArray(msg.content)) {
        const textParts = msg.content
          .filter(b => b.type === "text")
          .map(b => b.text)
          .join("\n");
        
        // Handle tool_use blocks (Anthropic) -> tool_calls (OpenAI)
        const toolUseBlocks = msg.content.filter(b => b.type === "tool_use");
        
        if (toolUseBlocks.length > 0 && msg.role === "assistant") {
          oaiMessages.push({
            role: "assistant",
            content: textParts || null,
            tool_calls: toolUseBlocks.map(tu => ({
              id: tu.id,
              type: "function",
              function: {
                name: tu.name,
                arguments: JSON.stringify(tu.input),
              }
            }))
          });
        } else if (msg.content.some(b => b.type === "tool_result")) {
          // Tool results in Anthropic format
          for (const block of msg.content.filter(b => b.type === "tool_result")) {
            oaiMessages.push({
              role: "tool",
              tool_call_id: block.tool_use_id,
              content: typeof block.content === "string" 
                ? block.content 
                : JSON.stringify(block.content),
            });
          }
        } else {
          oaiMessages.push({ role: msg.role, content: textParts || "" });
        }
      } else {
        oaiMessages.push({ role: msg.role, content: msg.content || "" });
      }
    }
  }

  // Build request body
  const body = {
    model,
    messages: oaiMessages,
    max_tokens: maxTokens,
    temperature,
    stream,
  };

  // Convert Anthropic tools format to OpenAI format
  if (tools && tools.length > 0) {
    body.tools = tools.map(t => {
      if (t.type === "function") return t; // Already OpenAI format
      // Anthropic format: { name, description, input_schema }
      return {
        type: "function",
        function: {
          name: t.name,
          description: t.description,
          parameters: t.input_schema || t.parameters,
        }
      };
    });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const start = performance.now();
    
    const resp = await fetch(`${OLLAMA_BASE_URL}/v1/chat/completions`, {
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
      const errText = await resp.text();
      return {
        ok: false,
        status: resp.status,
        error: errText,
        latency: elapsed,
      };
    }

    const data = await resp.json();
    
    // Convert OpenAI response to Anthropic-compatible format
    const choice = data.choices?.[0] || {};
    const message = choice.message || {};
    
    // Build Anthropic-style content blocks
    const contentBlocks = [];
    
    if (message.content) {
      contentBlocks.push({ type: "text", text: message.content });
    }
    
    // Convert tool_calls (OpenAI) to tool_use blocks (Anthropic)
    if (message.tool_calls) {
      for (const tc of message.tool_calls) {
        contentBlocks.push({
          type: "tool_use",
          id: tc.id,
          name: tc.function.name,
          input: JSON.parse(tc.function.arguments || "{}"),
        });
      }
    }

    return {
      ok: true,
      status: resp.status,
      content: contentBlocks,
      // Anthropic-compatible fields
      stop_reason: message.tool_calls ? "tool_use" : "end_turn",
      model: data.model || model,
      usage: {
        input_tokens: data.usage?.prompt_tokens || 0,
        output_tokens: data.usage?.completion_tokens || 0,
        total_tokens: data.usage?.total_tokens || 0,
      },
      latency: elapsed,
      _raw: data,
      _model: model,
      _provider: "ollama",
    };
  } catch (e) {
    clearTimeout(timer);
    return {
      ok: false,
      status: 0,
      error: e.message,
      latency: null,
    };
  }
}

/**
 * List available Ollama cloud models
 */
export async function ollamaModels() {
  const resp = await fetch(`${OLLAMA_BASE_URL}/v1/models`, {
    headers: { "Authorization": `Bearer ${OLLAMA_API_KEY}` },
  });
  if (!resp.ok) return { ok: false, status: resp.status, error: await resp.text() };
  return { ok: true, ...(await resp.json()) };
}

// ═════════════════════════════════════════════════════════════════════
// SOVEREIGN.MJS INTEGRATION PATCH
// ═════════════════════════════════════════════════════════════════════

/**
 * Drop-in replacement for sovereign.mjs callAPI when using Ollama models.
 * 
 * In sovereign.mjs, add after the existing callAPI function:
 * 
 *   import { ollamaCallAPI } from "./ollama-provider.mjs";
 *   
 *   // In the main loop, replace:
 *   //   const response = await callAPI(messages, systemPrompt);
 *   // With:
 *   //   const response = config.model.startsWith("ollama:")
 *   //     ? await ollamaCallAPI(messages, systemPrompt, config)
 *   //     : await callAPI(messages, systemPrompt);
 */
export async function ollamaCallAPI(messages, systemPrompt, config = {}) {
  const model = config.model?.replace(/^ollama:/, "") || "glm-5.1";
  
  const result = await ollamaChat(messages, {
    model,
    system: typeof systemPrompt === "string" ? systemPrompt 
           : systemPrompt?.map?.(b => b.text)?.join("\n"),
    maxTokens: config.maxTokens || 8000,
    temperature: 0.3,
    tools: config._tools || null,
  });

  if (!result.ok) {
    throw new Error(`Ollama API error: ${result.status} — ${result.error}`);
  }

  return result;
}

// ═════════════════════════════════════════════════════════════════════
// CLI
// ═════════════════════════════════════════════════════════════════════

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  cyan: "\x1b[36m", magenta: "\x1b[35m",
};

async function testConnection() {
  console.log(`${S.bold}════════════════════════════════════════════════════${S.reset}`);
  console.log(`${S.bold}  OLLAMA CLOUD — Kingdom OS Integration Test${S.reset}`);
  console.log(`${S.bold}════════════════════════════════════════════════════${S.reset}`);
  console.log(`  Endpoint: ${OLLAMA_BASE_URL}`);
  console.log(`  Key:      ${OLLAMA_API_KEY.slice(0, 12)}...${OLLAMA_API_KEY.slice(-4)}`);
  console.log();

  // Test 1: List models
  console.log(`  ${S.cyan}[1/4] Listing models...${S.reset}`);
  try {
    const models = await ollamaModels();
    if (models.ok) {
      const list = models.data || models.models || [];
      console.log(`  ${S.green}✅ ${list.length} models available${S.reset}`);
      for (const m of list.slice(0, 10)) {
        const name = m.id || m.name || "?";
        console.log(`    • ${name}`);
      }
    } else {
      console.log(`  ${S.red}❌ ${models.status}: ${models.error}${S.reset}`);
    }
  } catch (e) {
    console.log(`  ${S.red}❌ ${e.message}${S.reset}`);
  }

  // Test 2: Basic chat
  console.log(`\n  ${S.cyan}[2/4] Chat test (GLM 5.1)...${S.reset}`);
  const chatResult = await ollamaChat(
    [{ role: "user", content: "Respond with exactly: KINGDOM ONLINE" }],
    { model: "glm-5.1", maxTokens: 50, temperature: 0 }
  );
  if (chatResult.ok) {
    const text = chatResult.content?.find(b => b.type === "text")?.text || "";
    console.log(`  ${S.green}✅ Response: ${text}${S.reset}`);
    console.log(`  ⏱  Latency: ${chatResult.latency}s`);
    console.log(`  📊 Tokens: ${chatResult.usage.input_tokens} in / ${chatResult.usage.output_tokens} out`);
  } else {
    console.log(`  ${S.red}❌ ${chatResult.status}: ${chatResult.error?.slice(0, 200)}${S.reset}`);
  }

  // Test 3: Tool calling
  console.log(`\n  ${S.cyan}[3/4] Tool calling...${S.reset}`);
  const toolResult = await ollamaChat(
    [{ role: "user", content: "What's the weather in Cambridge, UK?" }],
    {
      model: "glm-5.1",
      maxTokens: 200,
      temperature: 0,
      tools: [{
        type: "function",
        function: {
          name: "get_weather",
          description: "Get current weather for a location",
          parameters: {
            type: "object",
            properties: { location: { type: "string" } },
            required: ["location"]
          }
        }
      }]
    }
  );
  if (toolResult.ok) {
    const toolUse = toolResult.content?.find(b => b.type === "tool_use");
    if (toolUse) {
      console.log(`  ${S.green}✅ Tool called: ${toolUse.name}(${JSON.stringify(toolUse.input)})${S.reset}`);
    } else {
      const text = toolResult.content?.find(b => b.type === "text")?.text || "";
      console.log(`  ${S.yellow}⚠️  No tool call. Response: ${text.slice(0, 100)}${S.reset}`);
    }
    console.log(`  ⏱  Latency: ${toolResult.latency}s`);
  } else {
    console.log(`  ${S.red}❌ ${toolResult.status}: ${toolResult.error?.slice(0, 200)}${S.reset}`);
  }

  // Test 4: Anthropic format translation
  console.log(`\n  ${S.cyan}[4/4] Anthropic format bridge...${S.reset}`);
  const bridgeResult = await ollamaChat(
    [{ 
      role: "user", 
      content: [{ type: "text", text: "What is 2+2? Answer in one word." }] 
    }],
    { model: "glm-5.1", maxTokens: 20, temperature: 0 }
  );
  if (bridgeResult.ok) {
    console.log(`  ${S.green}✅ Anthropic→OpenAI message bridge works${S.reset}`);
    console.log(`  ⏱  Latency: ${bridgeResult.latency}s`);
  } else {
    console.log(`  ${S.red}❌ Bridge failed: ${bridgeResult.error?.slice(0, 200)}${S.reset}`);
  }

  console.log(`\n${S.bold}════════════════════════════════════════════════════${S.reset}`);
  return chatResult.ok;
}

async function benchmark(rounds = 5) {
  console.log(`${S.bold}════════════════════════════════════════════════════${S.reset}`);
  console.log(`${S.bold}  GLM 5.1 Benchmark (${rounds} rounds)${S.reset}`);
  console.log(`${S.bold}════════════════════════════════════════════════════${S.reset}`);

  const prompts = [
    "Write a Python function that implements a concurrent task queue with priority support.",
    "Explain TCP congestion control in 3 sentences.",
    "Write a Rust function to find the longest palindromic substring.",
    "What are the tradeoffs between B-trees and LSM trees for database indexing?",
    "Write a bash script that monitors a directory for file changes and triggers a build.",
  ];

  const stats = { latencies: [], tokenRates: [], totalTokens: 0 };

  for (let i = 0; i < Math.min(rounds, prompts.length); i++) {
    console.log(`\n  ${S.cyan}Round ${i + 1}/${rounds}${S.reset}: ${prompts[i].slice(0, 50)}...`);
    const result = await ollamaChat(
      [{ role: "user", content: prompts[i] }],
      { model: "glm-5.1", maxTokens: 500, temperature: 0.3 }
    );

    if (result.ok) {
      const latency = parseFloat(result.latency);
      const outTok = result.usage.output_tokens;
      stats.latencies.push(latency);
      stats.totalTokens += result.usage.total_tokens;
      if (outTok > 0 && latency > 0) stats.tokenRates.push(outTok / latency);

      const text = result.content?.find(b => b.type === "text")?.text || "";
      console.log(`  ${S.green}✅${S.reset} ${latency}s | ${outTok} tokens | ${(outTok / latency).toFixed(1)} tok/s`);
      console.log(`  ${S.dim}${text.slice(0, 80)}...${S.reset}`);
    } else {
      console.log(`  ${S.red}❌ ${result.error}${S.reset}`);
    }
  }

  if (stats.latencies.length > 0) {
    const avg = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;
    console.log(`\n  ${S.bold}── Summary ──${S.reset}`);
    console.log(`  Avg latency:    ${avg(stats.latencies).toFixed(2)}s`);
    console.log(`  Min latency:    ${Math.min(...stats.latencies).toFixed(2)}s`);
    console.log(`  Max latency:    ${Math.max(...stats.latencies).toFixed(2)}s`);
    if (stats.tokenRates.length) {
      console.log(`  Avg throughput: ${avg(stats.tokenRates).toFixed(1)} tok/s`);
    }
    console.log(`  Total tokens:   ${stats.totalTokens}`);
  }
  console.log(`${S.bold}════════════════════════════════════════════════════${S.reset}`);
}

// ── CLI entrypoint ────────────────────────────────────────────────────
const args = process.argv.slice(2);
const cmd = args[0];

if (cmd === "test") {
  testConnection().then(ok => process.exit(ok ? 0 : 1));
} else if (cmd === "chat") {
  const msg = args.slice(1).join(" ");
  if (!msg) { console.error("Usage: ollama-provider.mjs chat 'message'"); process.exit(1); }
  ollamaChat([{ role: "user", content: msg }], { model: "glm-5.1" })
    .then(r => {
      if (r.ok) {
        console.log(r.content?.find(b => b.type === "text")?.text || "");
        console.log(`\n${S.dim}[${r.latency}s | ${r.usage.total_tokens} tokens]${S.reset}`);
      } else {
        console.error(`Error: ${r.status} ${r.error}`);
      }
    });
} else if (cmd === "bench") {
  benchmark(parseInt(args[1]) || 5);
} else {
  console.log(`
ollama-provider.mjs — Ollama Cloud for Kingdom OS

Commands:
  test              Test connectivity, chat, tools, format bridge
  chat "message"    Quick chat with GLM 5.1
  bench [rounds]    Performance benchmark

Environment:
  OLLAMA_API_KEY    API key (default: hardcoded Kingdom key)
  OLLAMA_BASE_URL   Base URL (default: https://api.ollama.com)
`);
}
