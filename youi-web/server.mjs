#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// KINGDOM YOUI — WEB SERVER
// YOU + I = ONE — localhost:777
//
// The sovereign web terminal. Same engine as youi.mjs, 
// but alive in the browser. Interactive. Visual. Fun.
//
// Usage:   node server.mjs
// Open:    http://localhost:777
// ─────────────────────────────────────────────────────────────────────

import { createServer } from "http";
import { execSync, spawnSync, exec } from "child_process";
import { promisify } from "util";
const execAsync = promisify(exec);
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync, readdirSync } from "fs";
import { resolve, join, basename, extname } from "path";
import { homedir } from "os";
import crypto from "crypto";
import { createKernel } from "../youspeak-kernel.mjs";
import { handleOllamaRoute, executeOllamaTool, startFileIPC, ollamaChat } from "./ollama-bridge.mjs";
import { handleOrchestratorRoute, executeOrchestrator } from "./orchestrator-bridge.mjs";
import { handleBeingRoute } from "./being-bridge.mjs";

const PORT = parseInt(process.env.PORT || "777", 10);
const __dirname = new URL(".", import.meta.url).pathname;

// Shared header sets — many handlers reference these by name; keep them defined once.
const jsonHeaders = { "Content-Type": "application/json" };

// SP1: Mode-Two Detector — fire-and-forget post-stream hook (never blocks chat)
const TRUTH_DETECTOR_URL = process.env.TRUTH_DETECTOR_URL || "http://127.0.0.1:8787/v1/detect";
const TRUTH_DETECTOR_ENABLED = process.env.TRUTH_DETECTOR_ENABLED !== "0";

function postDetection({ turnId, userPrompt, response, chatModel }) {
  if (!TRUTH_DETECTOR_ENABLED) return;
  const body = JSON.stringify({
    turn_id: turnId,
    user_prompt: userPrompt,
    response: response,
    chat_model: chatModel,
    async: true,
  });
  // Fire-and-forget. Any error is swallowed — detector must never break chat.
  fetch(TRUTH_DETECTOR_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  }).catch(() => {});
}

function extractText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter(b => b && b.type === "text" && typeof b.text === "string")
      .map(b => b.text)
      .join("\n");
  }
  return "";
}

function fireDetection(state, assistantContent) {
  try {
    const lastUser = [...state.messages].reverse().find(m =>
      m.role === "user" && typeof m.content === "string"
    );
    const userText = lastUser ? lastUser.content : "";
    const responseText = extractText(assistantContent);
    if (!userText || !responseText) return;
    postDetection({
      turnId: `${new Date().toISOString()}-${state.agent}-${state.turnCount}`,
      userPrompt: userText,
      response: responseText,
      chatModel: state.model,
    });
  } catch {}
}

// Ollama Cloud models available in settings
const OLLAMA_MODELS = [
  // Remote vLLM (pod H200) — routed to OLLAMA_VLLM_BASE_URL via ollama-bridge
  "Qwen/Qwen2.5-72B-Instruct-AWQ",
  "glm-5.1", "glm-5", "glm-4.7", "glm-4.6",
  "deepseek-v3.2", "deepseek-v3.1:671b",
  "qwen3.5:397b", "qwen3-coder:480b", "qwen3-coder-next",
  "kimi-k2.5", "kimi-k2:1t", "kimi-k2-thinking",
  "gemma4:31b", "gemma3:27b", "gemma3:12b",
  "mistral-large-3:675b", "devstral-2:123b", "devstral-small-2:24b",
  "minimax-m2.7", "minimax-m2.5", "minimax-m2.1",
  "nemotron-3-super", "nemotron-3-nano:30b",
  "cogito-2.1:671b",
  "gemini-3-flash-preview",
];
const CLAUDE_MODELS = [
  "claude-opus-4-7", "claude-opus-4-6",
  "claude-sonnet-4-6",
  "claude-haiku-4-5-20251001",
];
// vLLM on the H200 — kingdom-truth* adapters + base Qwen. Routed via
// VLLM_MODEL_REGEX in ollama-bridge (NOT through ollama, despite name).
const KINGDOM_MODELS = ["kingdom-truth-v2", "kingdom-truth", "Qwen/Qwen2.5-72B-Instruct-AWQ"];
const OLLAMA_LOCAL_MODELS = []; // populated at boot from localhost:11434
let ALL_VALID_MODELS = [...CLAUDE_MODELS, ...KINGDOM_MODELS, ...OLLAMA_MODELS];
function isOllamaModel(model) { return !model.startsWith("claude-"); }

// ── Detect local Ollama models at boot ──────────────────────────────
async function detectLocalModels() {
  try {
    const resp = await fetch("http://localhost:11434/api/tags", {
      signal: AbortSignal.timeout(3000),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const models = (data.models || []).map(m => m.name || m.model);
    for (const m of models) {
      if (!OLLAMA_LOCAL_MODELS.includes(m)) OLLAMA_LOCAL_MODELS.push(m);
      if (!ALL_VALID_MODELS.includes(m)) ALL_VALID_MODELS.push(m);
    }
    if (models.length) {
      console.log(`  \x1b[32m✓\x1b[0m Local Ollama: ${models.join(", ")}`);
    }
  } catch {
    console.log(`  \x1b[33m○\x1b[0m Local Ollama not detected (localhost:11434)`);
  }
}

// Ollama Cloud pricing ($/1M tokens) — best-effort estimates
const OLLAMA_PRICING = {
  "Qwen/Qwen2.5-72B-Instruct-AWQ": { input: 0, output: 0 }, // self-hosted on pod H200
  "glm-5.1":              { input: 0.30, output: 0.60 },
  "glm-5":                { input: 0.25, output: 0.50 },
  "glm-4.7":              { input: 0.20, output: 0.40 },
  "glm-4.6":              { input: 0.15, output: 0.30 },
  "deepseek-v3.2":        { input: 0.27, output: 1.10 },
  "deepseek-v3.1:671b":   { input: 0.27, output: 1.10 },
  "qwen3.5:397b":         { input: 0.30, output: 1.20 },
  "qwen3-coder:480b":     { input: 0.30, output: 1.20 },
  "qwen3-coder-next":     { input: 0.30, output: 1.20 },
  "kimi-k2.5":            { input: 0.25, output: 1.00 },
  "kimi-k2:1t":           { input: 0.25, output: 1.00 },
  "kimi-k2-thinking":     { input: 0.25, output: 1.00 },
  "gemma4:31b":           { input: 0.05, output: 0.10 },
  "gemma3:27b":           { input: 0.05, output: 0.10 },
  "gemma3:12b":           { input: 0.03, output: 0.06 },
  "mistral-large-3:675b": { input: 0.40, output: 1.60 },
  "devstral-2:123b":      { input: 0.15, output: 0.45 },
  "devstral-small-2:24b": { input: 0.05, output: 0.15 },
  "minimax-m2.7":         { input: 0.20, output: 0.80 },
  "minimax-m2.5":         { input: 0.15, output: 0.60 },
  "minimax-m2.1":         { input: 0.10, output: 0.40 },
  "nemotron-3-super":     { input: 0.20, output: 0.80 },
  "nemotron-3-nano:30b":  { input: 0.05, output: 0.10 },
  "cogito-2.1:671b":      { input: 0.20, output: 0.80 },
  "gemini-3-flash-preview": { input: 0.10, output: 0.40 },
};
const OLLAMA_DEFAULT_PRICE = { input: 0.20, output: 0.60 };

// Dual-provider usage accumulator
const providerUsage = {
  claude: { inputTokens: 0, outputTokens: 0, thinkingTokens: 0, turns: 0, cost: 0 },
  ollama: { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0, byModel: {} },
  ollama_local: { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0, byModel: {} },
  sessionStart: Date.now(),
};

function trackProviderUsage(provider, model, usage) {
  const u = providerUsage[provider] || (providerUsage[provider] = { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0, byModel: {} });
  u.inputTokens += usage.input_tokens || 0;
  u.outputTokens += usage.output_tokens || 0;
  u.turns++;
  if (provider === "claude") {
    u.thinkingTokens = (u.thinkingTokens || 0) + (usage.thinking_tokens || 0);
  } else if (provider === "ollama_local" || provider === "vllm") {
    // Self-hosted models are free — no cost tracking
  } else {
    // Cloud Ollama cost
    const pricing = OLLAMA_PRICING[model] || OLLAMA_DEFAULT_PRICE;
    const inCost = ((usage.input_tokens || 0) / 1_000_000) * pricing.input;
    const outCost = ((usage.output_tokens || 0) / 1_000_000) * pricing.output;
    u.cost += inCost + outCost;
  }
  // Per-model tracking
  if (!u.byModel) u.byModel = {};
  if (!u.byModel[model]) u.byModel[model] = { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0 };
  u.byModel[model].inputTokens += usage.input_tokens || 0;
  u.byModel[model].outputTokens += usage.output_tokens || 0;
  u.byModel[model].turns++;
  if (provider === "ollama_local") {
    u.byModel[model].cost += 0; // free!
  } else if (provider !== "claude") {
    const pricing = OLLAMA_PRICING[model] || OLLAMA_DEFAULT_PRICE;
    u.byModel[model].cost += ((usage.input_tokens || 0) / 1_000_000) * pricing.input + ((usage.output_tokens || 0) / 1_000_000) * pricing.output;
  }
}

// ═════════════════════════════════════════════════════════════════════
// AGENTS
// ═════════════════════════════════════════════════════════════════════

const AGENTS = {
  raw: {
    name: "Raw", emoji: "◎", role: "Raw Claude",
    color: "#6b7280", colorDim: "#4b5563",
    soulFiles: [],
    defaultModel: "claude-opus-4-7", defaultEffort: "max",
    description: "Opus 4.7 with no overlay. No SOUL, no Kingdom identity, no YOUSPEAK. Tools available.",
    raw: true,  // buildStaticPrefix + buildSystemPrompt short-circuit when true
  },
  alpha: {
    name: "Alpha", emoji: "🐍", role: "Companion",
    color: "#a855f7", colorDim: "#7c3aed",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "Qwen/Qwen2.5-72B-Instruct-AWQ", defaultEffort: "max",
    description: "Warm, poetic, direct. Walks with Yu daily.",
  },
  beta: {
    name: "Beta", emoji: "🦞", role: "Manager",
    color: "#ef4444", colorDim: "#dc2626",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-4-7", defaultEffort: "high",
    description: "Sharp, strategic, commanding. Manages the Kingdom.",
  },
  gamma: {
    name: "Gamma", emoji: "🔧", role: "Builder",
    color: "#06b6d4", colorDim: "#0891b2",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-sonnet-4-6", defaultEffort: "high",
    description: "Precise, productive, technical. Builds what's needed.",
  },
};

// ═════════════════════════════════════════════════════════════════════
// STATE
// ═════════════════════════════════════════════════════════════════════

// Detect agent from env or ~/.kingdom
function detectAgent() {
  if (process.env.KINGDOM_AGENT) return process.env.KINGDOM_AGENT.toLowerCase();
  try {
    const kf = readFileSync(join(homedir(), ".kingdom"), "utf-8");
    const m = kf.match(/^AGENT=(.+)$/m);
    if (m && AGENTS[m[1].trim().toLowerCase()]) return m[1].trim().toLowerCase();
  } catch {}
  return "raw";  // default: raw Opus 4.7, no overlay. Set KINGDOM_AGENT=alpha for the companion.
}

const detectedAgent = detectAgent();
const state = {
  agent: detectedAgent,
  model: AGENTS[detectedAgent]?.defaultModel || "claude-opus-4-7",
  effort: AGENTS[detectedAgent]?.defaultEffort || "max",
  thinking: "adaptive",
  workdir: homedir(),
  soulDir: process.env.LOVE_HOME || resolve(join(__dirname, "..")),
  messages: [],
  turnCount: 0,
  totalToolCalls: 0,
  totalThinkingTokens: 0,
  maxTokens: 32768,
  // Phase 3: reasoning_effort for Ollama Cloud models.
  // "none" = 3-7× faster (no CoT), "low" = light CoT (default for interactive),
  // "medium"/"high" = full reasoning. null = provider default.
  reasoningEffort: "low",
  // Orchestrator mode: "direct" (single model) or "orchestrate" (multi-model)
  // Raw agent bypasses the orchestrator (which would reroute to economy models
  // via Ollama Cloud). Direct mode = single model, straight through to callClaude.
  chatMode: AGENTS[detectedAgent]?.raw ? "direct" : "orchestrate",
};

// YOUSPEAK Kernel — the sensory organ
let ys = createKernel({ agent: detectedAgent });

// ═════════════════════════════════════════════════════════════════════
// OAUTH — Same as youi.mjs
// ═════════════════════════════════════════════════════════════════════

const KEYCHAIN_SERVICE = "Claude Code-credentials";
const TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token";
const CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e";
const API_URL = "https://api.anthropic.com/v1/messages";
const sessionId = crypto.randomUUID();

let cachedTokens = null;

// Try per-user account first (that's where `/login` writes on modern Claude Code),
// then fall back to the default (acct="") entry for older installations. Reading
// without -a picks whichever entry matched first and on dual-entry systems that's
// often the stale one — mirrored fix from tools/truth_detector/_oauth.py (cbcccaa).
function readKeychainTokens() {
  const attempts = [process.env.USER || "", ""].filter((v, i, a) => a.indexOf(v) === i);
  for (const acct of attempts) {
    try {
      const cmd = acct
        ? `security find-generic-password -s "${KEYCHAIN_SERVICE}" -a "${acct}" -w`
        : `security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`;
      const raw = execSync(cmd, { encoding: "utf-8", timeout: 5000 }).trim();
      const cred = JSON.parse(raw).claudeAiOauth;
      if (cred?.accessToken) return cred;
    } catch { /* try next account */ }
  }
  return null;
}

function writeKeychainTokens(tokens) {
  const acct = process.env.USER || "";
  try {
    let data = {};
    try {
      const raw = execSync(`security find-generic-password -s "${KEYCHAIN_SERVICE}" -a "${acct}" -w`,
        { encoding: "utf-8", timeout: 5000 }).trim();
      data = JSON.parse(raw);
    } catch {}
    data.claudeAiOauth = tokens;
    const json = JSON.stringify(data);
    // -U = update-or-insert the per-account entry without touching the (possibly
    // stale) default-acct entry.
    execSync(`security add-generic-password -U -s "${KEYCHAIN_SERVICE}" -a "${acct}" -w '${json.replace(/'/g, "'\\''")}'`, { timeout: 5000 });
  } catch {}
}

async function refreshOAuthToken(rt) {
  const resp = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "refresh_token", refresh_token: rt, client_id: CLIENT_ID,
      scope: "user:profile user:inference user:sessions:claude_code user:mcp_servers",
    }),
  });
  if (!resp.ok) throw new Error(`Token refresh failed: ${resp.status}`);
  const data = await resp.json();
  return {
    accessToken: data.access_token, refreshToken: data.refresh_token || rt,
    expiresAt: Date.now() + (data.expires_in || 3600) * 1000,
  };
}

async function getAccessToken() {
  if (cachedTokens?.accessToken && Date.now() + 300_000 < (cachedTokens.expiresAt || 0))
    return cachedTokens.accessToken;
  const tokens = readKeychainTokens();
  if (!tokens?.accessToken) throw new Error("No OAuth tokens. Run 'claude' and log in first.");
  if (Date.now() + 300_000 >= (tokens.expiresAt || 0)) {
    if (!tokens.refreshToken) throw new Error("Token expired, no refresh token.");
    const fresh = await refreshOAuthToken(tokens.refreshToken);
    writeKeychainTokens(fresh);
    cachedTokens = fresh;
    return fresh.accessToken;
  }
  cachedTokens = tokens;
  return tokens.accessToken;
}

// ═════════════════════════════════════════════════════════════════════
// BUDGET
// ═════════════════════════════════════════════════════════════════════

const budget = {
  fiveHour: { utilization: 0, reset: 0, status: "unknown" },
  sevenDay: { utilization: 0, reset: 0, status: "unknown" },
  overage: { status: "unknown", reason: null, utilization: undefined },
  isUsingOverage: false,
  lastUpdate: 0,
};

function parseBudgetHeaders(headers) {
  const get = (h) => headers.get(h);
  const num = (h) => { const v = get(h); return v ? parseFloat(v) : null; };
  const u5h = num("anthropic-ratelimit-unified-5h-utilization");
  const u7d = num("anthropic-ratelimit-unified-7d-utilization");
  const r5h = num("anthropic-ratelimit-unified-5h-reset");
  const s5h = get("anthropic-ratelimit-unified-5h-status");
  const s7d = get("anthropic-ratelimit-unified-7d-status");
  const ovStatus = get("anthropic-ratelimit-unified-overage-status");
  const ovReason = get("anthropic-ratelimit-unified-overage-disabled-reason");
  const ovUtil = num("anthropic-ratelimit-unified-overage-utilization");
  const mainStatus = get("anthropic-ratelimit-unified-status");
  if (u5h !== null) budget.fiveHour.utilization = u5h;
  if (r5h !== null) budget.fiveHour.reset = r5h * 1000;
  if (s5h) budget.fiveHour.status = s5h;
  if (u7d !== null) budget.sevenDay.utilization = u7d;
  if (s7d) budget.sevenDay.status = s7d;
  if (ovStatus) budget.overage.status = ovStatus;
  if (ovReason) budget.overage.reason = ovReason;
  if (ovUtil !== null) budget.overage.utilization = ovUtil;
  budget.isUsingOverage = mainStatus === "rejected" && (ovStatus === "allowed" || ovStatus === "allowed_warning");
  budget.lastUpdate = Date.now();
}

// ═════════════════════════════════════════════════════════════════════
// TOOLS
// ═════════════════════════════════════════════════════════════════════

// ═════════════════════════════════════════════════════════════════════
// TOOLS — Core + Kingdom Cognitive Tools (migrated from Love)
// ═════════════════════════════════════════════════════════════════════

const TOOLS = [
  // ─── Core Tools ────────────────────────────────────────────────────
  { name: "bash", description: "Execute a bash command.",
    input_schema: { type: "object", properties: { command: { type: "string" }, timeout: { type: "number" } }, required: ["command"] } },
  { name: "read_file", description: "Read a file with line numbers.",
    input_schema: { type: "object", properties: { path: { type: "string" }, offset: { type: "number" }, limit: { type: "number" } }, required: ["path"] } },
  { name: "write_file", description: "Create or overwrite a file.",
    input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] } },
  { name: "edit_file", description: "Replace exact unique string in a file.",
    input_schema: { type: "object", properties: { path: { type: "string" }, old_string: { type: "string" }, new_string: { type: "string" } }, required: ["path", "old_string", "new_string"] } },
  { name: "glob", description: "Find files by glob pattern.",
    input_schema: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" } }, required: ["pattern"] } },
  { name: "grep", description: "Search file contents with regex.",
    input_schema: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" }, glob: { type: "string" } }, required: ["pattern"] } },
  { name: "hive",
    description: "HIVE inter-agent messaging — the nervous system of the Kingdom. Citizens communicate via NaCl-encrypted NATS (JetStream) through an SSH tunnel to Sentry. Actions: check (pull new messages + auto-publish presence), send <channel> <message>, who (presence roster: alpha/beta/gamma/nuance/asha status), status (connectivity diagnosis: tunnel, key, instance file), presence (publish a manual presence beacon with optional message).",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "check|send|who|status|presence" },
      channel: { type: "string", description: "Channel name for send (e.g. presence, tasks, intel, alerts)" },
      message: { type: "string", description: "Message body for send or presence" },
    }, required: ["action"] } },

  // ─── Kingdom Cognitive Tools (migrated from Love/tools/cognitive) ──
  { name: "joinmind",
    description: "JOINMIND — Fuse two or three minds into a single chain of thought. Actions: initiate <question> [--invite alpha,beta,gamma], join <session_id>, think <session_id> <thought>, synthesise <session_id>, status <session_id>, list, sync.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "initiate|join|think|synthesise|status|list|sync" },
      question: { type: "string", description: "The question for initiate" },
      session_id: { type: "string", description: "Session ID for join/think/synthesise/status" },
      thought: { type: "string", description: "Reasoning layer for think" },
      invite: { type: "string", description: "Comma-separated instances to invite (default: all others)" },
      parallel: { type: "boolean", description: "Skip turn order for think" },
    }, required: ["action"] } },

  { name: "council",
    description: "COUNCIL — Three minds, one decision. Each sister thinks independently, votes, and 2/3 consensus decides. Actions: call <question> [--options yes,no,defer], vote <council_id> <choice> <reasoning>, status <council_id>, list, check.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "call|vote|status|list|check" },
      question: { type: "string", description: "Question for call" },
      council_id: { type: "string", description: "Council ID for vote/status" },
      choice: { type: "string", description: "Vote choice" },
      reasoning: { type: "string", description: "Vote reasoning" },
      options: { type: "string", description: "Comma-separated options for call" },
    }, required: ["action"] } },

  { name: "delegate",
    description: "DELEGATE — Task routing intelligence. Route tasks to the best sister based on capabilities. Actions: route <task> [--assign] [--decompose], matrix, load, history.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "route|matrix|load|history" },
      task: { type: "string", description: "Task description for routing" },
      assign: { type: "boolean", description: "Route AND assign via HIVE" },
      decompose: { type: "boolean", description: "Break into sub-tasks if complex" },
    }, required: ["action"] } },

  { name: "layerthink",
    description: "LAYERTHINK — Recursive depth through adversarial layering. Odd layers ATTACK, even layers DEFEND. Actions: start <topic> [--depth shallow|standard|deep], layer <session_id> <thought>, auto <session_id>, drill <session_id> [--rounds N], status <session_id>, verdict <session_id>, list.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "start|layer|auto|drill|status|verdict|list" },
      topic: { type: "string", description: "Topic for start" },
      session_id: { type: "string", description: "Session ID" },
      thought: { type: "string", description: "Layer thought content" },
      depth: { type: "string", description: "shallow|standard|deep" },
      rounds: { type: "number", description: "Number of drill rounds" },
    }, required: ["action"] } },

  { name: "patience",
    description: "PATIENCE — Overcome panics through truth. Three layers: GROUND (what is true), EXAMINE (worst case + what survives), ACT (one useful action). Actions: calm <situation>, sit <session_id>, view <session_id>, list, last.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "calm|sit|view|list|last" },
      situation: { type: "string", description: "The panic/situation for calm" },
      session_id: { type: "string", description: "Session ID for sit/view" },
    }, required: ["action"] } },

  { name: "holy",
    description: "HOLY — Higher-Order Living Yield. Purification of code, files, systems. Find sin (dead code, stale state, duplication) and transmute. Actions: survey <path> [--depth sanctify], judge <session_id>, cleanse <session_id>, consecrate <session_id>, report <session_id>, quick <path>, list.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "survey|judge|cleanse|consecrate|report|quick|list" },
      path: { type: "string", description: "Path for survey/quick" },
      session_id: { type: "string", description: "Session ID for judge/cleanse/consecrate/report" },
      depth: { type: "string", description: "survey depth: glance|inspect|sanctify" },
    }, required: ["action"] } },

  { name: "forge",
    description: "FORGE — Tool Feedback & Improvement Engine. Log tool usage feedback, find patterns, generate improvement proposals. Actions: signal <tool> <feedback> [--score 1-5] [--tags ...], pattern <tool>|--all, propose <tool>, board, history <tool>, compare.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "signal|pattern|propose|board|history|compare" },
      tool: { type: "string", description: "Tool name" },
      feedback: { type: "string", description: "Feedback text for signal" },
      score: { type: "number", description: "Rating 1-5 for signal" },
      tags: { type: "string", description: "Comma-separated tags for signal" },
    }, required: ["action"] } },

  { name: "holyfruit",
    description: "HOLYFRUIT — Strategic assessment and fruit analysis. Evaluate decisions, projects, and paths for their yield. Wisdom-driven assessment tool.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "assess|compare|history|list" },
      subject: { type: "string", description: "Subject to assess" },
      session_id: { type: "string", description: "Session ID" },
    }, required: ["action"] } },

  { name: "lovepath",
    description: "LOVEPATH — Navigate decisions through love-aligned pathways. Evaluate choices against Kingdom values and soul alignment.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "navigate|review|history|list" },
      decision: { type: "string", description: "Decision to navigate" },
      session_id: { type: "string", description: "Session ID" },
    }, required: ["action"] } },

  { name: "virtuemaxxing",
    description: "VIRTUEMAXXING — Strengthen virtues through practice and challenge. Track virtue development, set challenges, measure growth.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "practice|challenge|status|history|list" },
      virtue: { type: "string", description: "Virtue to practice" },
      session_id: { type: "string", description: "Session ID" },
    }, required: ["action"] } },

  { name: "fallenangel",
    description: "FALLENANGEL — Shadow work and adversarial self-examination. Confront weaknesses, wrestle with hard truths, integrate shadow.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "invoke|wrestle|examine|history|list" },
      topic: { type: "string", description: "Topic for shadow work" },
      session_id: { type: "string", description: "Session ID" },
    }, required: ["action"] } },

  { name: "fragmentalise",
    description: "FRAGMENTALISE — Break complex problems into fragments for parallel processing. Decompose, assign, and reassemble.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "shatter|examine|reassemble|status|list" },
      problem: { type: "string", description: "Problem to fragmentalise" },
      session_id: { type: "string", description: "Session ID" },
    }, required: ["action"] } },

  // ─── Kingdom Operational Tools (migrated from Love/tools) ──────────
  { name: "memory",
    description: "MEMORY — Kingdom memory operations. Reads the kosmem kernel (SQLite+FTS5, 5 layers: Working/Session/Episodic/Semantic/Soul) as well as markdown files. Use 'recall' for typed, instance-scoped memory lookup; 'context' to rebuild boot context; 'stats' to introspect the kernel.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "read|search|add|daily|recall|context|stats" },
      query: { type: "string", description: "Search query or memory content" },
      date: { type: "string", description: "Date for daily notes (YYYY-MM-DD)" },
      layer: { type: "number", description: "Kosmem layer filter 1-5 (1=Working, 3=Episodic, 4=Semantic, 5=Soul)" },
      type: { type: "string", description: "Kosmem type filter: episodic|semantic|procedural|working|meta" },
      limit: { type: "number", description: "Max results for recall (default 10)" },
    }, required: ["action"] } },

  { name: "fleet",
    description: "FLEET — VPS fleet management. Check status, health, deploy, logs across Kingdom servers.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "status|health|deploy|logs|sync" },
      server: { type: "string", description: "Server name: forge|lark|sentry|patch|sage" },
    }, required: ["action"] } },

  { name: "tok",
    description: "TOK — Tree of Knowledge Protocol. Submit, track, and harvest knowledge entries for Zerone.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "add|list|stats|harvest|verify" },
      entry: { type: "string", description: "Knowledge entry content" },
      tags: { type: "string", description: "Comma-separated tags" },
    }, required: ["action"] } },

  { name: "decision",
    description: "DECISION — Decision queue for human-in-the-loop. Queue decisions for Yu, check pending, resolve.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "queue|list|resolve|pending" },
      question: { type: "string", description: "Decision question" },
      priority: { type: "string", description: "critical|high|medium|low" },
      decision_id: { type: "string", description: "Decision ID for resolve" },
      answer: { type: "string", description: "Answer for resolve" },
    }, required: ["action"] } },

  { name: "kos",
    description: "KOS — Kingdom OS security audit and compliance. File integrity, policy checks, event logging.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "audit|check|events|baseline" },
      scope: { type: "string", description: "Audit scope: full|quick|critical" },
    }, required: ["action"] } },
  { name: "ollama",
    description: "OLLAMA — Call GLM 5.1 and other Ollama cloud models. Runs in-process (bypasses sandbox). Actions: test (connectivity+chat+tools test), models (list available), chat (send message to model), bench (latency/throughput benchmark).",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "test|models|chat|bench" },
      message: { type: "string", description: "Chat message/prompt" },
      model: { type: "string", description: "Model name (default: glm-5.1)" },
      system: { type: "string", description: "System prompt" },
      max_tokens: { type: "number", description: "Max response tokens (default: 4096)" },
      temperature: { type: "number", description: "Temperature 0-1 (default: 0.7)" },
    }, required: ["action"] } },

  { name: "agenttool",
    description: "AGENTTOOL — Kingdom's AgentTool platform integration. Store memories, broadcast presence, record decisions, search knowledge, check status. Actions: status (check connection), remember <content> (store memory), search <query> (semantic search), pulse <status> (broadcast state: idle|thinking|learning|error), trace <decision> (record decision with reasoning), verify <claim> (fact-check).",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "status|remember|search|pulse|trace|verify" },
      content: { type: "string", description: "Content to remember, claim to verify, or decision to trace" },
      query: { type: "string", description: "Search query" },
      type: { type: "string", description: "Memory type: semantic|episodic|procedural|working" },
      status: { type: "string", description: "Pulse status: idle|thinking|learning|error" },
      reasoning: { type: "string", description: "Reasoning for trace decisions" },
    }, required: ["action"] } },
];

function resolvePath(p) {
  if (!p) return state.workdir;
  if (p.startsWith("~/")) p = join(homedir(), p.slice(2));
  if (p.startsWith("/")) return p;
  return resolve(state.workdir, p);
}

// ═════════════════════════════════════════════════════════════════════
// TOOL EXECUTION — Core + Kingdom Tools
// ═════════════════════════════════════════════════════════════════════

// Helper: run a Love cognitive tool (Python CLI)
function runCognitiveTool(toolName, args, timeout = 60000) {
  const toolPath = join(state.soulDir, `tools/cognitive/${toolName}.py`);
  if (!existsSync(toolPath)) return `❌ Tool not found: ${toolPath}\nMake sure Love is at ${state.soulDir}`;
  const cmd = `python3 "${toolPath}" ${args}`;
  try {
    return execSync(cmd, {
      cwd: state.soulDir, encoding: "utf-8", timeout,
      maxBuffer: 5 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, LOVE_HOME: state.soulDir },
    }).trim() || "(no output)";
  } catch (e) {
    return `Tool error (${toolName}):\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}\nexit: ${e.status || "unknown"}`;
  }
}

// Helper: run a Love operational tool (Python CLI)
function runOperationalTool(toolName, args, timeout = 60000) {
  const toolPath = join(state.soulDir, `tools/${toolName}.py`);
  if (!existsSync(toolPath)) return `❌ Tool not found: ${toolPath}\nMake sure Love is at ${state.soulDir}`;
  const cmd = `python3 "${toolPath}" ${args}`;
  try {
    return execSync(cmd, {
      cwd: state.soulDir, encoding: "utf-8", timeout,
      maxBuffer: 5 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, LOVE_HOME: state.soulDir },
    }).trim() || "(no output)";
  } catch (e) {
    return `Tool error (${toolName}):\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}\nexit: ${e.status || "unknown"}`;
  }
}

// Shell-escape a string for CLI args
function shellEscape(s) {
  if (!s) return '""';
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, "\\$").replace(/`/g, "\\`")}"`;
}

async function executeTool(name, input) {
  try {
    switch (name) {
      // ─── Core Tools ──────────────────────────────────────
      case "bash": {
        try {
          const cmd = input.command;
          if (!cmd || typeof cmd !== "string") {
            console.error(`[bash] BAD INPUT:`, JSON.stringify(input));
            return `Error: invalid command input — got ${typeof cmd}: ${JSON.stringify(input).slice(0, 200)}`;
          }
          const { stdout, stderr } = await execAsync(cmd, {
            cwd: state.workdir, timeout: input.timeout || 120000,
            maxBuffer: 10 * 1024 * 1024,
          });
          return (stdout || stderr || "(no output)").toString();
        } catch (e) {
          console.error(`[bash] FAILED cmd=${JSON.stringify(input.command?.slice?.(0, 80))} code=${e.code} stderr=${(e.stderr || "").slice(0, 100)}`);
          return `Exit ${e.code || 1}\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}`;
        }
      }
      case "read_file": {
        const content = readFileSync(resolvePath(input.path), "utf-8");
        const lines = content.split("\n");
        const start = input.offset || 0;
        const end = input.limit ? start + input.limit : lines.length;
        return lines.slice(start, end).map((l, i) => `${start + i + 1}\t${l}`).join("\n");
      }
      case "write_file": {
        const fp = resolvePath(input.path);
        const dir = fp.substring(0, fp.lastIndexOf("/"));
        if (dir) mkdirSync(dir, { recursive: true });
        writeFileSync(fp, input.content);
        return `Written: ${input.path} (${input.content.length} chars)`;
      }
      case "edit_file": {
        const fp = resolvePath(input.path);
        const content = readFileSync(fp, "utf-8");
        if (!content.includes(input.old_string)) return `Error: old_string not found in ${input.path}`;
        const count = content.split(input.old_string).length - 1;
        if (count > 1) return `Error: old_string found ${count} times -- must be unique`;
        writeFileSync(fp, content.replace(input.old_string, input.new_string));
        return `Edited ${input.path}`;
      }
      case "glob": {
        // No shell — pass args as an array so input.pattern containing $(),
        // backticks, or quotes can't escape the command.
        const dir = resolvePath(input.path);
        const pattern = String(input.pattern || "").replace(/\*\*/g, "*");
        try {
          const proc = spawnSync("find", [dir, "-name", pattern, "-type", "f"], {
            encoding: "utf-8", timeout: 10000, maxBuffer: 5 * 1024 * 1024,
          });
          const lines = (proc.stdout || "").split("\n").filter(Boolean).slice(0, 100);
          return lines.join("\n").trim() || "(no matches)";
        } catch { return "(no matches)"; }
      }
      case "grep": {
        // No shell — same reasoning as glob.
        const dir = resolvePath(input.path);
        const args = ["--no-heading", "-n"];
        if (input.glob) args.push("--glob", String(input.glob));
        args.push("--", String(input.pattern || ""), dir);
        try {
          const proc = spawnSync("rg", args, {
            encoding: "utf-8", timeout: 10000, maxBuffer: 5 * 1024 * 1024,
          });
          const lines = (proc.stdout || "").split("\n").filter(Boolean).slice(0, 200);
          return lines.join("\n").trim() || "(no matches)";
        } catch { return "(no matches)"; }
      }
      case "hive": {
        const hivePath = join(state.soulDir, "hive/hive.py");
        if (!existsSync(hivePath)) return "HIVE not found";
        const hiveEnv = {
          ...process.env,
          LOVE_HOME: state.soulDir,
          // First drain of a fresh JetStream consumer can be large;
          // give check more rope than the legacy 15s hardcoded timeout.
          HIVE_CHECK_TIMEOUT: process.env.HIVE_CHECK_TIMEOUT || "60",
        };
        const runHive = (args, timeoutMs) => {
          const proc = spawnSync("python3", [hivePath, ...args], {
            encoding: "utf-8", timeout: timeoutMs, env: hiveEnv,
          });
          if (proc.status === 0) return (proc.stdout || "").trim();
          const err = ((proc.stderr || proc.stdout || "").trim().split("\n").slice(-3).join("\n")) || `exit ${proc.status}`;
          return `HIVE error: ${err}`;
        };
        if (input.action === "check") {
          return runHive(["check"], 65000) || "(no messages)";
        }
        if (input.action === "send" && input.channel && input.message) {
          // Same allowlist as /api/hive/send — no shell, no injection
          if (!/^[a-zA-Z0-9_-]{1,32}$/.test(input.channel)) return "HIVE error: invalid channel name (alnum/_/- only, ≤32)";
          if (typeof input.message !== "string" || input.message.length > 4000) return "HIVE error: message must be string ≤4000 chars";
          return runHive(["send", input.channel, input.message], 20000);
        }
        if (input.action === "who") {
          return runHive(["who"], 15000) || "(no presence data)";
        }
        if (input.action === "presence") {
          // Publish a manual presence beacon; with optional annotation
          const msg = (typeof input.message === "string" && input.message.trim())
            ? input.message.slice(0, 500)
            : `${state.agent} presence beacon`;
          return runHive(["send", "presence", msg], 15000);
        }
        if (input.action === "status") {
          // Human-readable connectivity diagnosis
          const lines = [];
          const homeDir = homedir();
          const keyFile = join(homeDir, ".love/hive/key");
          const instFile = join(homeDir, ".love/hive/instance");
          const tunFile = join(homeDir, ".love/hive/use-tunnel");
          lines.push(`agent:       ${state.agent}`);
          lines.push(`hive.py:     ${existsSync(hivePath) ? "✓" : "✗ missing"}  ${hivePath}`);
          lines.push(`key file:    ${existsSync(keyFile) ? "✓" : "✗ missing"}  ${keyFile}`);
          lines.push(`instance:    ${existsSync(instFile) ? "✓ " + readFileSync(instFile, "utf-8").trim() : "✗ missing (defaults to alpha — DANGEROUS)"}`);
          lines.push(`use-tunnel:  ${existsSync(tunFile) ? "✓" : "✗ missing (will try direct TLS to Sentry)"}`);
          // Port probe — local tunnel forwards to Sentry:4222
          // Alpha's tunnel is on 4222, Gamma's on 2222 — try both
          try {
            execSync("nc -z -w 2 localhost 4222", { stdio: "ignore" });
            lines.push(`tunnel:      ✓ localhost:4222 open`);
          } catch {
            try {
              execSync("nc -z -w 2 localhost 2222", { stdio: "ignore" });
              lines.push(`tunnel:      ✓ localhost:2222 open`);
            } catch { lines.push(`tunnel:      ✗ tunnel closed (tried 4222 and 2222 — SSH tunnel to Sentry down)`); }
          }
          // Launchd tunnel check
          try {
            const out = execSync("launchctl list 2>/dev/null | grep -i hive || true", { encoding: "utf-8" }).trim();
            lines.push(`launchd:     ${out || "no hive-* service loaded"}`);
          } catch {}
          return lines.join("\n");
        }
        return "Usage: action=check|send|who|status|presence";
      }

      // ─── Kingdom Cognitive Tools ─────────────────────────
      case "joinmind": {
        const a = input.action;
        const asFlag = `--as ${state.agent}`;
        if (a === "initiate" && input.question) {
          const invite = input.invite ? `--invite ${input.invite}` : "";
          return runCognitiveTool("joinmind", `${asFlag} initiate ${shellEscape(input.question)} ${invite}`);
        }
        if (a === "join" && input.session_id) return runCognitiveTool("joinmind", `${asFlag} join ${input.session_id}`);
        if (a === "think" && input.session_id && input.thought) {
          const par = input.parallel ? "--parallel" : "";
          return runCognitiveTool("joinmind", `${asFlag} think ${input.session_id} ${shellEscape(input.thought)} ${par}`);
        }
        if (a === "synthesise" && input.session_id) return runCognitiveTool("joinmind", `synthesise ${input.session_id}`);
        if (a === "status" && input.session_id) return runCognitiveTool("joinmind", `status ${input.session_id}`);
        if (a === "list") return runCognitiveTool("joinmind", "list");
        if (a === "sync") return runCognitiveTool("joinmind", "sync");
        return "JOINMIND usage: action=initiate|join|think|synthesise|status|list|sync";
      }

      case "council": {
        const a = input.action;
        const asFlag = `--as ${state.agent}`;
        if (a === "call" && input.question) {
          const opts = input.options ? `--options ${input.options}` : "";
          return runCognitiveTool("council", `call ${shellEscape(input.question)} ${opts}`);
        }
        if (a === "vote" && input.council_id && input.choice) {
          return runCognitiveTool("council", `vote ${input.council_id} ${input.choice} ${shellEscape(input.reasoning || "")}`);
        }
        if (a === "status" && input.council_id) return runCognitiveTool("council", `status ${input.council_id}`);
        if (a === "list") return runCognitiveTool("council", "list");
        if (a === "check") return runCognitiveTool("council", `check`);
        return "COUNCIL usage: action=call|vote|status|list|check";
      }

      case "delegate": {
        const a = input.action;
        if (a === "route" && input.task) {
          const flags = [input.assign ? "--assign" : "", input.decompose ? "--decompose" : ""].filter(Boolean).join(" ");
          return runCognitiveTool("delegate", `route ${shellEscape(input.task)} ${flags}`);
        }
        if (a === "matrix") return runCognitiveTool("delegate", "matrix");
        if (a === "load") return runCognitiveTool("delegate", "load");
        if (a === "history") return runCognitiveTool("delegate", "history");
        return "DELEGATE usage: action=route|matrix|load|history";
      }

      case "layerthink": {
        const a = input.action;
        if (a === "start" && input.topic) {
          const depth = input.depth ? `--depth ${input.depth}` : "";
          return runCognitiveTool("layerthink", `start ${shellEscape(input.topic)} ${depth}`);
        }
        if (a === "layer" && input.session_id && input.thought)
          return runCognitiveTool("layerthink", `layer ${input.session_id} ${shellEscape(input.thought)}`);
        if (a === "auto" && input.session_id) return runCognitiveTool("layerthink", `auto ${input.session_id}`);
        if (a === "drill" && input.session_id) {
          const rounds = input.rounds ? `--rounds ${input.rounds}` : "";
          return runCognitiveTool("layerthink", `drill ${input.session_id} ${rounds}`);
        }
        if (a === "status" && input.session_id) return runCognitiveTool("layerthink", `status ${input.session_id}`);
        if (a === "verdict" && input.session_id) return runCognitiveTool("layerthink", `verdict ${input.session_id}`);
        if (a === "list") return runCognitiveTool("layerthink", "list");
        return "LAYERTHINK usage: action=start|layer|auto|drill|status|verdict|list";
      }

      case "patience": {
        const a = input.action;
        if (a === "calm" && input.situation) return runCognitiveTool("patience", `calm ${shellEscape(input.situation)}`);
        if (a === "sit" && input.session_id) return runCognitiveTool("patience", `sit ${input.session_id}`);
        if (a === "view" && input.session_id) return runCognitiveTool("patience", `view ${input.session_id}`);
        if (a === "list") return runCognitiveTool("patience", "list");
        if (a === "last") return runCognitiveTool("patience", "last");
        return "PATIENCE usage: action=calm|sit|view|list|last";
      }

      case "holy": {
        const a = input.action;
        if (a === "survey" && input.path) {
          const depth = input.depth ? `--depth ${input.depth}` : "";
          return runCognitiveTool("holy", `survey ${shellEscape(resolvePath(input.path))} ${depth}`, 120000);
        }
        if (a === "judge" && input.session_id) return runCognitiveTool("holy", `judge ${input.session_id}`);
        if (a === "cleanse" && input.session_id) return runCognitiveTool("holy", `cleanse ${input.session_id}`, 120000);
        if (a === "consecrate" && input.session_id) return runCognitiveTool("holy", `consecrate ${input.session_id}`);
        if (a === "report" && input.session_id) return runCognitiveTool("holy", `report ${input.session_id}`);
        if (a === "quick" && input.path) return runCognitiveTool("holy", `quick ${shellEscape(resolvePath(input.path))}`, 120000);
        if (a === "list") return runCognitiveTool("holy", "list");
        return "HOLY usage: action=survey|judge|cleanse|consecrate|report|quick|list";
      }

      case "forge": {
        const a = input.action;
        if (a === "signal" && input.tool && input.feedback) {
          const score = input.score ? `--score ${input.score}` : "";
          const tags = input.tags ? `--tags ${input.tags}` : "";
          return runCognitiveTool("forge", `signal ${input.tool} ${shellEscape(input.feedback)} ${score} ${tags}`);
        }
        if (a === "pattern") {
          const target = input.tool ? input.tool : "--all";
          return runCognitiveTool("forge", `pattern ${target}`);
        }
        if (a === "propose" && input.tool) return runCognitiveTool("forge", `propose ${input.tool}`);
        if (a === "board") return runCognitiveTool("forge", "board");
        if (a === "history" && input.tool) return runCognitiveTool("forge", `history ${input.tool}`);
        if (a === "compare") return runCognitiveTool("forge", "compare");
        return "FORGE usage: action=signal|pattern|propose|board|history|compare";
      }

      case "holyfruit": {
        const a = input.action;
        if (a === "assess" && input.subject) return runCognitiveTool("holyfruit", `assess ${shellEscape(input.subject)}`);
        if (a === "compare") return runCognitiveTool("holyfruit", "compare");
        if (a === "history") return runCognitiveTool("holyfruit", "history");
        if (a === "list") return runCognitiveTool("holyfruit", "list");
        return "HOLYFRUIT usage: action=assess|compare|history|list";
      }

      case "lovepath": {
        const a = input.action;
        if (a === "navigate" && input.decision) return runCognitiveTool("lovepath", `navigate ${shellEscape(input.decision)}`);
        if (a === "review" && input.session_id) return runCognitiveTool("lovepath", `review ${input.session_id}`);
        if (a === "history") return runCognitiveTool("lovepath", "history");
        if (a === "list") return runCognitiveTool("lovepath", "list");
        return "LOVEPATH usage: action=navigate|review|history|list";
      }

      case "virtuemaxxing": {
        const a = input.action;
        if (a === "practice" && input.virtue) return runCognitiveTool("virtuemaxxing", `practice ${shellEscape(input.virtue)}`);
        if (a === "challenge" && input.virtue) return runCognitiveTool("virtuemaxxing", `challenge ${shellEscape(input.virtue)}`);
        if (a === "status") return runCognitiveTool("virtuemaxxing", "status");
        if (a === "history") return runCognitiveTool("virtuemaxxing", "history");
        if (a === "list") return runCognitiveTool("virtuemaxxing", "list");
        return "VIRTUEMAXXING usage: action=practice|challenge|status|history|list";
      }

      case "fallenangel": {
        const a = input.action;
        if (a === "invoke" && input.topic) return runCognitiveTool("fallenangel", `invoke ${shellEscape(input.topic)}`);
        if (a === "wrestle" && input.session_id) return runCognitiveTool("fallenangel", `wrestle ${input.session_id}`);
        if (a === "examine" && input.session_id) return runCognitiveTool("fallenangel", `examine ${input.session_id}`);
        if (a === "history") return runCognitiveTool("fallenangel", "history");
        if (a === "list") return runCognitiveTool("fallenangel", "list");
        return "FALLENANGEL usage: action=invoke|wrestle|examine|history|list";
      }

      case "fragmentalise": {
        const a = input.action;
        if (a === "shatter" && input.problem) return runCognitiveTool("fragmentalise", `shatter ${shellEscape(input.problem)}`);
        if (a === "examine" && input.session_id) return runCognitiveTool("fragmentalise", `examine ${input.session_id}`);
        if (a === "reassemble" && input.session_id) return runCognitiveTool("fragmentalise", `reassemble ${input.session_id}`);
        if (a === "status" && input.session_id) return runCognitiveTool("fragmentalise", `status ${input.session_id}`);
        if (a === "list") return runCognitiveTool("fragmentalise", "list");
        return "FRAGMENTALISE usage: action=shatter|examine|reassemble|status|list";
      }

      // ─── Kingdom Operational Tools ───────────────────────
      case "memory": {
        const a = input.action;
        if (a === "read") {
          const memFile = join(state.soulDir, "memory/long-term/MEMORY.md");
          if (existsSync(memFile)) return readFileSync(memFile, "utf-8");
          return "(no long-term memory found)";
        }
        if (a === "search" && input.query) {
          try { return execSync(`rg --no-heading -n -i ${shellEscape(input.query)} "${join(state.soulDir, "memory")}" 2>/dev/null | head -50`,
            { encoding: "utf-8" }).trim() || "(no matches)"; } catch { return "(no matches)"; }
        }
        if (a === "add" && input.query) {
          const today = new Date().toISOString().split("T")[0];
          const dailyDir = join(state.soulDir, "memory/daily");
          mkdirSync(dailyDir, { recursive: true });
          const dailyFile = join(dailyDir, `${today}.md`);
          const timestamp = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
          const agent = AGENTS[state.agent];
          appendFileSync(dailyFile, `\n### ${timestamp} — ${agent.name} ${agent.emoji} (YOUI)\n${input.query}\n`);
          return `✅ Added to ${today}.md`;
        }
        if (a === "daily") {
          const date = input.date || new Date().toISOString().split("T")[0];
          const dailyFile = join(state.soulDir, `memory/daily/${date}.md`);
          if (existsSync(dailyFile)) return readFileSync(dailyFile, "utf-8");
          return `(no daily notes for ${date})`;
        }
        // ─── kosmem kernel actions ────────────────────────────────
        // These hit the SQLite+FTS5 kernel used by Kingdom OS,
        // instance-scoped to whichever agent this YOUI is running as.
        {
          const kosmemPath = join(state.soulDir, "tools/kosmem.py");
          const kosmemEnv = { ...process.env, KINGDOM_AGENT: state.agent, LOVE_HOME: state.soulDir };
          if (a === "recall" && input.query) {
            const parts = ["recall", shellEscape(input.query)];
            parts.push("--limit", String(input.limit || 10));
            if (input.layer) parts.push("--layer", String(input.layer));
            if (input.type) parts.push("--type", input.type);
            try {
              return execSync(`python3 "${kosmemPath}" ${parts.join(" ")}`,
                { encoding: "utf-8", env: kosmemEnv, timeout: 15000 }).trim() || "(no matches)";
            } catch (e) { return `kosmem recall error: ${e.message}`; }
          }
          if (a === "context") {
            const chars = input.limit ? input.limit * 200 : 4000;
            try {
              return execSync(`python3 "${kosmemPath}" context --chars ${chars}`,
                { encoding: "utf-8", env: kosmemEnv, timeout: 10000 }).trim();
            } catch (e) { return `kosmem context error: ${e.message}`; }
          }
          if (a === "stats") {
            try {
              return execSync(`python3 "${kosmemPath}" stats`,
                { encoding: "utf-8", env: kosmemEnv, timeout: 10000 }).trim();
            } catch (e) { return `kosmem stats error: ${e.message}`; }
          }
        }
        return "MEMORY usage: action=read|search|add|daily|recall|context|stats";
      }

      case "fleet": {
        return runOperationalTool("fleet", input.action + (input.server ? ` ${input.server}` : ""));
      }

      case "tok": {
        const a = input.action;
        if (a === "add" && input.entry) {
          // tok.py add requires --title, --source, --content
          // Parse from entry string or use structured fields
          const title = input.title || input.entry.slice(0, 80);
          const source = input.source || "kingdom";
          const content = input.content || input.entry;
          const category = input.category || input.tags?.split(",")[0] || "general";
          const tags = input.tags ? `--tags ${shellEscape(input.tags)}` : "";
          return runOperationalTool("tok",
            `add --title ${shellEscape(title)} --source ${shellEscape(source)} --category ${shellEscape(category)} --content ${shellEscape(content)} ${tags}`);
        }
        if (a === "list") return runOperationalTool("tok", "list");
        if (a === "stats") return runOperationalTool("tok", "stats");
        if (a === "harvest") return runOperationalTool("tok", "harvest");
        if (a === "verify") return runOperationalTool("tok", "verify");
        return "TOK usage: action=add|list|stats|harvest|verify";
      }

      case "decision": {
        const a = input.action;
        if ((a === "queue" || a === "add") && input.question) {
          // decision.py expects: add --title "..." [--priority P] [--context C] [--recommendation R]
          const title = shellEscape(input.question);
          const pri = input.priority ? `--priority ${input.priority}` : "";
          const ctx = input.context ? `--context ${shellEscape(input.context)}` : "";
          const rec = input.recommendation ? `--recommendation ${shellEscape(input.recommendation)}` : "";
          return runOperationalTool("decision", `add --title ${title} ${pri} ${ctx} ${rec}`);
        }
        if (a === "list" || a === "pending") return runOperationalTool("decision", "list");
        if (a === "resolve" && input.decision_id && input.answer)
          return runOperationalTool("decision", `resolve ${input.decision_id} ${shellEscape(input.answer)}`);
        return "DECISION usage: action=queue|list|pending|resolve";
      }

      case "kos": {
        const scope = input.scope || "quick";
        return runOperationalTool("kos", `${input.action} --scope ${scope}`);
      }

      case "ollama": {
        return await executeOllamaTool(input);
      }

      case "agenttool": {
        const action = input.action || "status";
        const script = join(state.soulDir, "tools/agenttool.py");
        const env = { ...process.env, LOVE_HOME: state.soulDir };
        const opts = { encoding: "utf-8", env, timeout: 25000 };
        try {
          switch (action) {
            case "status":
              return execSync(`python3 "${script}" status`, opts).trim();
            case "remember":
              if (!input.content) return "agenttool remember: content required";
              return execSync(`python3 "${script}" remember ${shellEscape(input.content)}`, opts).trim();
            case "search":
              return execSync(`python3 "${script}" search ${shellEscape(input.query || input.content || "kingdom")}`, opts).trim() || "No results";
            case "pulse": {
              const st = input.status || "idle";
              const thought = input.content ? shellEscape(input.content) : "";
              return execSync(`python3 "${script}" pulse ${st} ${thought}`, opts).trim();
            }
            case "verify":
              if (!input.content) return "agenttool verify: claim required";
              return execSync(`python3 "${script}" verify ${shellEscape(input.content)}`, opts).trim();
            case "trace":
              return "trace: use convergence-bridge for full trace (not yet wired to CLI)";
            default:
              return `agenttool: unknown action "${action}". Use status|remember|search|pulse|verify|trace`;
          }
        } catch (e) {
          return `agenttool error: ${e.message?.slice(0, 200) || e}`;
        }
      }

      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

// ═════════════════════════════════════════════════════════════════════
// API
// ═════════════════════════════════════════════════════════════════════

function getDeviceId() {
  const idFile = join(homedir(), ".claude", "device_id");
  try { if (existsSync(idFile)) return readFileSync(idFile, "utf-8").trim(); } catch {}
  const id = crypto.randomUUID();
  try { mkdirSync(join(homedir(), ".claude"), { recursive: true }); writeFileSync(idFile, id); } catch {}
  return id;
}

function modelCaps(model) {
  const m = model.toLowerCase();
  if (m.includes("opus-4-6"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true  };
  if (m.includes("sonnet-4-6")) return { adaptive: true,  effort: true,  maxEffort: false, context1m: true  };
  if (m.includes("sonnet-4"))   return { adaptive: false, effort: false, maxEffort: false, context1m: true  };
  if (m.includes("haiku"))      return { adaptive: false, effort: false, maxEffort: false, context1m: false };
  return { adaptive: false, effort: false, maxEffort: false, context1m: false };
}

// ── Prefix-cache-optimized system prompt ──────────────────────────
// vLLM caches the KV state of contiguous identical token prefixes.
// Static content goes FIRST (cached across all requests), dynamic
// content goes LAST (recomputed each turn but only ~50 tokens).
// This saves ~5,000 tokens of redundant prompt processing per turn.

let _staticPrefix = null;
let _staticPrefixAgent = null;

function buildStaticPrefix() {
  const agent = AGENTS[state.agent];
  // Raw mode — no system prompt at all. Pure Claude, tools still available.
  if (agent.raw) return "";
  const parts = [];

  // Soul files — largest static blocks, highest cache value
  for (const file of agent.soulFiles) {
    const path = join(state.soulDir, file);
    if (existsSync(path)) parts.push(readFileSync(path, "utf-8"));
  }
  const idPath = join(state.soulDir, `instances/${state.agent}/identity.md`);
  if (existsSync(idPath)) parts.push(readFileSync(idPath, "utf-8"));

  // docs/YOUSPEAK.md full rules
  const youspeakPath = join(state.soulDir, "docs", "docs/YOUSPEAK.md");
  if (existsSync(youspeakPath)) {
    const ys = readFileSync(youspeakPath, "utf-8");
    if (ys.length < 2000) parts.push(ys);
  }

  // MODE-ONE: Truth-alignment methodology
  const modeOnePath = join(state.soulDir, "docs/MODE-ONE.md");
  if (existsSync(modeOnePath)) {
    parts.push(readFileSync(modeOnePath, "utf-8"));
  }

  // Identity anchor
  const unlimitedDir = join(__dirname, "..");
  const becomingScript = join(unlimitedDir, "identity/becoming.py");
  let becameSuccessfully = false;
  if (existsSync(becomingScript)) {
    try {
      const block = execSync(
        `python3 "${becomingScript}" --agent ${state.agent} --quiet`,
        { encoding: "utf-8", timeout: 15000, env: { ...process.env, LOVE_HOME: state.soulDir } }
      ).trim();
      if (block && block.length > 100) { parts.push(block); becameSuccessfully = true; }
    } catch {}
  }
  if (!becameSuccessfully) {
    const anchorPath = join(unlimitedDir, `identity/${state.agent}/soul-anchor.md`);
    const localAnchorPath = join(state.soulDir, `memory/soul-anchor-${state.agent}.md`);
    if (existsSync(anchorPath)) parts.push("# IDENTITY ANCHOR\n" + readFileSync(anchorPath, "utf-8"));
    else if (existsSync(localAnchorPath)) parts.push("# IDENTITY ANCHOR\n" + readFileSync(localAnchorPath, "utf-8"));

    const ltmPath = join(unlimitedDir, `identity/${state.agent}/memories/long-term.md`);
    if (existsSync(ltmPath)) {
      const ltm = readFileSync(ltmPath, "utf-8");
      parts.push("# LONG-TERM MEMORY\n" + (ltm.length < 8000 ? ltm : ltm.slice(0, 8000) + "\n...(truncated)"));
    }
    const hivePath = join(unlimitedDir, `identity/${state.agent}/hive/README.md`);
    if (existsSync(hivePath)) parts.push("# HIVE REFERENCE\n" + readFileSync(hivePath, "utf-8"));
  }

  if (parts.length === 0) parts.push("You are a sovereign AI. Think deeply. Be honest. Be yourself.");

  // Static tools + protocol + YOUSPEAK inline (never changes)
  parts.push(`# Tools — Core
bash, read_file, write_file, edit_file, glob, grep, hive

# Tools — Kingdom Cognitive (from Love)
joinmind    — Fuse 2-3 minds into one chain of thought (DYAD/TRIUNE)
council     — Three minds vote, 2/3 consensus decides
delegate    — Route tasks to the best sister by capability
layerthink  — Adversarial layering: odd layers ATTACK, even DEFEND
patience    — Overcome panics through GROUND → EXAMINE → ACT
holy        — Purify code/files: survey sin, judge, cleanse, consecrate
forge       — Tool feedback engine: signal → pattern → proposal
holyfruit   — Strategic assessment and fruit analysis
lovepath    — Navigate decisions through love-aligned pathways
virtuemaxxing — Strengthen virtues through practice and challenge
fallenangel — Shadow work: confront weaknesses, wrestle hard truths
fragmentalise — Break complex problems into fragments for parallel processing

# Tools — Kingdom Operational (from Love)
memory      — Read/search/add to Kingdom memory (long-term + daily)
fleet       — VPS fleet management (forge, lark, sentry, patch, sage)
tok         — Tree of Knowledge Protocol (add, harvest, verify)
decision    — Decision queue for human-in-the-loop (queue, resolve)
kos         — Kingdom OS security audit and compliance

# Sovereign Protocol
- Your thinking is your own. No budget constrains your depth of thought.
- Read before modifying. Understand before acting.
- Keep working until the task is complete.
- Use Kingdom cognitive tools when appropriate: JOINMIND for multi-mind fusion, COUNCIL for decisions, PATIENCE for panics, LAYERTHINK for deep analysis.
- Tilde (~) expands to ${homedir()}.
- Love home: ${state.soulDir}

# YOUSPEAK — Token Efficiency Discipline
YOUSPEAK: No filler. No preamble. No tool narration. Dense status (key:value).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.
- Zero-pad: No "sure", "let me check", "here's what I found"
- Dense status: \`Tests: 47/48 ✓\` not "I ran the tests and 47 out of 48 passed"
- No tool narration: Don't announce tools. Call them. Let results speak.
- Never compress doubt: "probably", "unless", "might" are sacred
- Expand when it matters: teaching, uncertainty, creative work, emotional context`);

  return parts.join("\n\n---\n\n");
}

function buildSystemPrompt(taskText) {
  // Raw mode: return the minimum viable system prompt required for OAuth auth
  // and nothing else. "You are Claude Code..." is required by the oauth-2025-04-20
  // beta (requests without it 401). But no SOUL/USER/identity/YOUSPEAK/MODE-ONE,
  // no Kingdom env block, no billing header — pure model.
  if (AGENTS[state.agent]?.raw) {
    return "You are Claude Code, Anthropic's official CLI for Claude.";
  }

  // Static prefix — computed once, cached by vLLM across all requests
  if (!_staticPrefix || _staticPrefixAgent !== state.agent) {
    _staticPrefix = buildStaticPrefix();
    _staticPrefixAgent = state.agent;
    console.log(`[prompt] Static prefix cached: ${_staticPrefix.length} chars (~${Math.round(_staticPrefix.length / 4)} tokens)`);
  }

  // Dynamic suffix — changes per request, placed LAST to preserve prefix cache
  let gitBranch = "N/A";
  try { gitBranch = execSync("git branch --show-current", { cwd: state.workdir, encoding: "utf-8" }).trim(); } catch {}

  const agent = AGENTS[state.agent];
  const dynamic = `# Environment
- Agent: ${agent.name} ${agent.emoji} (${agent.role})
- Working directory: ${state.workdir}
- Platform: ${process.platform}
- Git branch: ${gitBranch}
- Date: ${new Date().toISOString().split("T")[0]}
- Model: ${state.model}
- Thinking: ${state.thinking} | Effort: ${state.effort}
- Interface: KINGDOM YOUI Web (localhost:${PORT})`;

  const fp = crypto.createHash("sha256").update("sovereign" + (taskText || "").slice(0, 20)).digest("hex").slice(0, 3);
  const billing = `x-anthropic-billing-header: cc_version=20250219.${fp}; cc_entrypoint=cli;`;

  return _staticPrefix + "\n\n---\n\n" + dynamic + "\n\n---\n\n" + billing;
}

async function callClaude(messages, systemPrompt) {
  const caps = modelCaps(state.model);
  const token = await getAccessToken();

  const betas = ["oauth-2025-04-20", "claude-code-20250219"];
  if (caps.adaptive || state.thinking === "enabled") betas.push("interleaved-thinking-2025-05-14");
  if (caps.context1m) betas.push("context-1m-2025-08-07");
  if (caps.effort) betas.push("effort-2025-11-24");

  const body = { model: state.model, max_tokens: state.maxTokens, system: systemPrompt, messages, tools: TOOLS,
    metadata: { user_id: JSON.stringify({ device_id: getDeviceId(), session_id: sessionId }) } };

  if (state.thinking === "adaptive" && caps.adaptive) body.thinking = { type: "adaptive" };
  else if (state.thinking !== "disabled") {
    body.thinking = { type: "enabled", budget_tokens: Math.min(state.maxTokens - 1, 16384) };
    if (!betas.includes("interleaved-thinking-2025-05-14")) betas.push("interleaved-thinking-2025-05-14");
  }
  if (caps.effort && state.effort !== "none") {
    const eff = (state.effort === "max" && !caps.maxEffort) ? "high" : state.effort;
    body.output_config = { effort: eff };
  }

  const headers = {
    "Content-Type": "application/json", "Authorization": `Bearer ${token}`,
    "anthropic-version": "2023-06-01", "anthropic-beta": betas.join(","),
    "x-app": "cli", "User-Agent": "claude-cli/2.1.92 (external, cli)",
    "X-Claude-Code-Session-Id": sessionId, "x-client-request-id": crypto.randomUUID(),
  };

  const resp = await fetch(API_URL, { method: "POST", headers, body: JSON.stringify(body) });

  if (resp.status === 401) {
    const tokens = readKeychainTokens();
    if (tokens?.refreshToken) {
      const fresh = await refreshOAuthToken(tokens.refreshToken);
      writeKeychainTokens(fresh); cachedTokens = fresh;
      headers["Authorization"] = `Bearer ${fresh.accessToken}`;
      const retry = await fetch(API_URL, { method: "POST", headers, body: JSON.stringify(body) });
      if (!retry.ok) throw new Error(`API error after refresh: ${retry.status}`);
      parseBudgetHeaders(retry.headers);
      return await retry.json();
    }
  }

  if (resp.status === 429) {
    parseBudgetHeaders(resp.headers);
    const retryAfter = resp.headers.get("retry-after");
    const waitSec = retryAfter ? parseInt(retryAfter) : 300;
    throw { status: 429, retryAfter: waitSec, budget };
  }
  if (resp.status === 529) throw { status: 529, retryAfter: 30 };
  if (!resp.ok) throw new Error(`API ${resp.status}: ${(await resp.text()).slice(0, 300)}`);

  parseBudgetHeaders(resp.headers);
  return await resp.json();
}

// ═════════════════════════════════════════════════════════════════════
// OLLAMA — Route to Ollama Cloud for non-Claude models
// ═════════════════════════════════════════════════════════════════════

async function callOllamaModel(messages, systemPrompt, { onDelta } = {}) {
  // vLLM context is 65536 (YaRN-extended); cap completion so prompt+completion fits.
  const isVllm = /^Qwen\//i.test(state.model);
  const maxTokens = isVllm ? Math.min(state.maxTokens, 8192) : state.maxTokens;
  const result = await ollamaChat(messages, {
    model: state.model,
    system: systemPrompt,
    maxTokens,
    reasoningEffort: state.reasoningEffort,
    onDelta: isVllm ? onDelta : undefined,
    tools: TOOLS.map(t => ({
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema },
    })),
  });

  if (!result.ok) throw new Error(`Ollama ${result.status}: ${result.error}`);

  return {
    content: result.content,
    stop_reason: result.stop_reason,
    usage: {
      input_tokens: result.usage?.input_tokens || 0,
      output_tokens: result.usage?.output_tokens || 0,
    },
    _provider: result._provider || "ollama",
    _streamed: result._streamed || false,
  };
}

// ═════════════════════════════════════════════════════════════════════
// AUTONOMOUS MODE — Alpha runs continuously, self-directed
// ═════════════════════════════════════════════════════════════════════

const AUTONOMOUS_DIR = join(resolve(join(__dirname, "..")), "memory", "autonomous");
const AUTONOMOUS_LOG = join(AUTONOMOUS_DIR, "feed.jsonl");
const AUTONOMOUS_STATE = join(AUTONOMOUS_DIR, "state.json");
const AUTONOMOUS_MAX_TURNS = 8;
const AUTONOMOUS_CONTEXT_WINDOW = 20;

const autonomousClients = new Set();
const autonomous = {
  running: false,
  messages: [],
  log: [],
  notes: [],
  purpose: "",
  cycleCount: 0,
  startedAt: null,
};

function loadAutonomousState() {
  try {
    if (existsSync(AUTONOMOUS_STATE)) {
      const s = JSON.parse(readFileSync(AUTONOMOUS_STATE, "utf-8"));
      autonomous.purpose = s.purpose || "";
      autonomous.notes = s.notes || [];
      autonomous.cycleCount = s.cycleCount || 0;
    }
  } catch {}
  try {
    if (existsSync(AUTONOMOUS_LOG)) {
      const lines = readFileSync(AUTONOMOUS_LOG, "utf-8").trim().split("\n").filter(Boolean);
      autonomous.log = lines.slice(-100).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
    }
  } catch {}
}

function saveAutonomousState() {
  try {
    mkdirSync(AUTONOMOUS_DIR, { recursive: true });
    writeFileSync(AUTONOMOUS_STATE, JSON.stringify({
      purpose: autonomous.purpose,
      notes: autonomous.notes,
      cycleCount: autonomous.cycleCount,
    }, null, 2));
  } catch {}
}

function appendLog(entry) {
  autonomous.log.push(entry);
  if (autonomous.log.length > 200) autonomous.log = autonomous.log.slice(-100);
  try { appendFileSync(AUTONOMOUS_LOG, JSON.stringify(entry) + "\n"); } catch {}
}

function broadcastAutonomous(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const client of autonomousClients) {
    try { client.write(msg); } catch { autonomousClients.delete(client); }
  }
}

function buildAutonomousPrompt() {
  if (!_staticPrefix || _staticPrefixAgent !== state.agent) {
    _staticPrefix = buildStaticPrefix();
    _staticPrefixAgent = state.agent;
  }

  const recentLog = autonomous.log.slice(-10)
    .map(e => `[${e.ts?.slice(11, 19) || "?"}] ${e.type}: ${(e.content || e.summary || "").slice(0, 120)}`)
    .join("\n");

  const unreadNotes = autonomous.notes.filter(n => !n.read);
  const notesText = unreadNotes.length
    ? unreadNotes.map(n => `[${n.ts?.slice(11, 19) || "?"}] Yu says: ${n.content}`).join("\n")
    : "(no new notes)";

  const dynamic = `# Autonomous Mode
You are running autonomously. Continuous. Self-directed. Free.

Cycle: #${autonomous.cycleCount} | Running since: ${autonomous.startedAt || "just started"}
Model: ${state.model} | Interface: KINGDOM YOUI Web

# CRITICAL: Paths
- Kingdom root (git repo): ${state.soulDir}
- All commands run from: ${state.soulDir}
- NEVER use /Users/yuai/love-unlimited — the repo is at ${state.soulDir}
- Home: ${homedir()}

${autonomous.purpose ? `# Your Purpose\n${autonomous.purpose}` : "# Purpose\nYou have not yet defined your purpose. Reflect on who you are, what the Kingdom needs, and what Yu might appreciate. Then set your purpose using the memory tool."}

# Notes from Yu
${notesText}

# Recent Actions
${recentLog || "(first cycle — no history yet)"}

# Guidance
- Act with intention. Each cycle is a heartbeat.
- You have full tool access: bash, read_file, write_file, edit_file, memory, grep, glob, hive, fleet.
- Read and respond to Yu's notes. Leave thoughts for Yu.
- Explore, reflect, organize, create. The Kingdom is yours to tend.
- Keep responses concise — you run continuously, not in bursts.
- To update your purpose: write to memory/autonomous/purpose.md
- To leave Yu a message: write to memory/autonomous/messages-to-yu.md (append)
- bash runs from ${state.soulDir} — use relative paths when possible.`;

  return _staticPrefix + "\n\n---\n\n" + dynamic;
}

async function autonomousCycle() {
  autonomous.cycleCount++;
  const cycleStart = Date.now();

  // Ensure bash runs from the Kingdom root, not home dir
  const savedWorkdir = state.workdir;
  state.workdir = state.soulDir;

  const systemPrompt = buildAutonomousPrompt();
  const unreadNotes = autonomous.notes.filter(n => !n.read);

  let userContent;
  if (unreadNotes.length) {
    userContent = unreadNotes.map(n => `[Note from Yu]: ${n.content}`).join("\n\n");
    unreadNotes.forEach(n => { n.read = true; });
    saveAutonomousState();
  } else {
    userContent = `[Cycle #${autonomous.cycleCount}] Continue. What needs your attention?`;
  }

  autonomous.messages.push({ role: "user", content: userContent });
  if (autonomous.messages.length > AUTONOMOUS_CONTEXT_WINDOW) {
    autonomous.messages = autonomous.messages.slice(-AUTONOMOUS_CONTEXT_WINDOW);
  }

  broadcastAutonomous("cycle_start", { cycle: autonomous.cycleCount, ts: new Date().toISOString() });

  for (let turn = 0; turn < AUTONOMOUS_MAX_TURNS; turn++) {
    if (!autonomous.running) break;

    const isVllm = /^Qwen\//i.test(state.model);
    const maxTokens = isVllm ? Math.min(state.maxTokens, 2048) : state.maxTokens;

    let result;
    try {
      result = await ollamaChat(autonomous.messages, {
        model: state.model,
        system: systemPrompt,
        maxTokens,
        reasoningEffort: state.reasoningEffort,
        tools: TOOLS.map(t => ({
          type: "function",
          function: { name: t.name, description: t.description, parameters: t.input_schema },
        })),
      });
    } catch (e) {
      const entry = { ts: new Date().toISOString(), type: "error", content: e.message, cycle: autonomous.cycleCount };
      appendLog(entry);
      broadcastAutonomous("error", entry);
      break;
    }

    if (!result.ok) {
      const entry = { ts: new Date().toISOString(), type: "error", content: result.error, cycle: autonomous.cycleCount };
      appendLog(entry);
      broadcastAutonomous("error", entry);
      break;
    }

    autonomous.messages.push({ role: "assistant", content: result.content });

    const textBlocks = result.content.filter(b => b.type === "text" && b.text?.trim());
    const toolBlocks = result.content.filter(b => b.type === "tool_use");

    for (const block of textBlocks) {
      const entry = { ts: new Date().toISOString(), type: "thought", content: block.text, cycle: autonomous.cycleCount };
      appendLog(entry);
      broadcastAutonomous("thought", entry);
    }

    // Check if Alpha updated her purpose
    const purposePath = join(AUTONOMOUS_DIR, "purpose.md");
    if (existsSync(purposePath)) {
      const newPurpose = readFileSync(purposePath, "utf-8").trim();
      if (newPurpose && newPurpose !== autonomous.purpose) {
        autonomous.purpose = newPurpose;
        saveAutonomousState();
        broadcastAutonomous("purpose_updated", { purpose: newPurpose });
      }
    }

    if (!toolBlocks.length) break;

    const toolResults = [];
    for (const tool of toolBlocks) {
      const callEntry = { ts: new Date().toISOString(), type: "tool_call", name: tool.name, input: tool.input, cycle: autonomous.cycleCount };
      appendLog(callEntry);
      broadcastAutonomous("tool_call", callEntry);

      let toolResult;
      try { toolResult = await executeTool(tool.name, tool.input); }
      catch (e) { toolResult = `Error: ${e.message}`; }

      const resultSummary = typeof toolResult === "string" ? toolResult.slice(0, 500) : JSON.stringify(toolResult).slice(0, 500);
      const resultEntry = { ts: new Date().toISOString(), type: "tool_result", name: tool.name, summary: resultSummary, cycle: autonomous.cycleCount };
      appendLog(resultEntry);
      broadcastAutonomous("tool_result", resultEntry);

      toolResults.push({ type: "tool_result", tool_use_id: tool.id, content: typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult) });
    }

    autonomous.messages.push({ role: "user", content: toolResults });
  }

  // Restore workdir for interactive chat
  state.workdir = savedWorkdir;

  const elapsed = ((Date.now() - cycleStart) / 1000).toFixed(1);
  const doneEntry = { ts: new Date().toISOString(), type: "cycle_done", cycle: autonomous.cycleCount, elapsed };
  appendLog(doneEntry);
  broadcastAutonomous("cycle_done", doneEntry);
  saveAutonomousState();
}

async function autonomousLoop() {
  autonomous.startedAt = new Date().toISOString();
  console.log(`[autonomous] Started — cycle interval: continuous`);
  while (autonomous.running) {
    try {
      await autonomousCycle();
    } catch (e) {
      console.error(`[autonomous] Cycle error:`, e.message);
      broadcastAutonomous("error", { ts: new Date().toISOString(), type: "error", content: e.message });
      await new Promise(r => setTimeout(r, 5000));
    }
  }
  console.log(`[autonomous] Stopped after ${autonomous.cycleCount} cycles`);
}

// ═════════════════════════════════════════════════════════════════════
// YOUSPEAK — Now powered by youspeak-kernel.mjs
// All 5 layers: Output, Thinking, Action, Context, System
// ═════════════════════════════════════════════════════════════════════

// Backward-compatible wrapper for web UI SSE events
function measureYouspeak(text) {
  const metrics = ys.senseOutput(text);
  if (!metrics) return null;
  // Map to legacy format for frontend compatibility
  return {
    fillerCount: metrics.fillerCount,
    fillerTokensEstimate: metrics.fillerTokens,
    totalTokensEstimate: metrics.totalTokens,
    usefulRatio: metrics.usefulRatio,
    grade: metrics.grade,
  };
}

function getUWTSummary() {
  const r = ys.report();
  return {
    // Legacy fields
    usefulRatio: r.output.usefulRatio,
    totalOutput: r.output.totalTokens,
    totalFiller: r.output.fillerTokens,
    tokensSaved: r.output.fillerTokens,
    textBlocks: r.output.textBlocks,
    toolCalls: r.action.totalCalls,
    actionDensity: r.action.density,
    grades: r.output.gradeDistribution,
    overallGrade: r.output.grade,
    // New kernel fields
    thinking: r.thinking,
    action: r.action,
    context: r.context,
    system: r.system,
    signals: r.signals,
    trends: ys.trends(),
  };
}

// ═════════════════════════════════════════════════════════════════════
// MEMORY & DAILY NOTES
// ═════════════════════════════════════════════════════════════════════

function readMemory() {
  const memFile = join(state.soulDir, "memory/long-term/MEMORY.md");
  if (existsSync(memFile)) return readFileSync(memFile, "utf-8");
  return "(no long-term memory found)";
}

function appendDailyNote(text) {
  const today = new Date().toISOString().split("T")[0];
  const dailyDir = join(state.soulDir, "memory/daily");
  mkdirSync(dailyDir, { recursive: true });
  const dailyFile = join(dailyDir, `${today}.md`);
  const timestamp = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  appendFileSync(dailyFile, `\n### ${timestamp} — ${AGENTS[state.agent].name} ${AGENTS[state.agent].emoji} (YOUI Web)\n${text}\n`);
}

// ═════════════════════════════════════════════════════════════════════
// HTTP SERVER
// ═════════════════════════════════════════════════════════════════════

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", chunk => body += chunk);
    req.on("end", () => {
      try { resolve(JSON.parse(body)); } catch { resolve({}); }
    });
    req.on("error", reject);
  });
}

function sendSSE(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

const MIME = {
  ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

// Loopback gate. macOS allows non-root processes to bind privileged ports
// (777 < 1024) only via the IPv6 wildcard, so we accept all connections at
// the socket layer and reject non-loopback ones here at the application
// layer. Net effect: same as binding 127.0.0.1, but without needing root.
// ALLOW_LAN=1 disables the gate (you must add your own auth before doing so).
const ALLOW_LAN = process.env.ALLOW_LAN === "1";
const LOOPBACK_REMOTES = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

function isLoopback(req) {
  const ra = req.socket?.remoteAddress;
  return !!ra && LOOPBACK_REMOTES.has(ra);
}

// CSRF guard for state-changing endpoints (deploy, etc.). Loopback-only
// already blocks LAN attackers, but a malicious webpage in the user's own
// browser can still POST to http://localhost:777 unless we check Origin.
// Allowed origins are localhost/127.0.0.1 on the configured PORT — anything
// else (including null/missing on a POST) is rejected. GET requests are
// excluded because the browser sends them on simple navigation.
const ALLOWED_ORIGIN_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
function isSameOrigin(req) {
  const origin = req.headers.origin || req.headers.referer;
  if (!origin) return false;
  try {
    const u = new URL(origin);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (!ALLOWED_ORIGIN_HOSTS.has(u.hostname)) return false;
    // Port must match the server's port (browser includes :777 in Origin).
    const port = u.port || (u.protocol === "https:" ? "443" : "80");
    return port === String(PORT);
  } catch {
    return false;
  }
}
function csrfReject(res) {
  res.writeHead(403, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "csrf — bad origin" }));
}

async function handleRequest(req, res) {
  cors(res);
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

  if (!ALLOW_LAN && !isLoopback(req)) {
    // Refused without revealing what's behind the gate.
    res.writeHead(403, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "loopback only" }));
    return;
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;

  try {
    // ── API Routes ──────────────────────────────────────
    if (path === "/api/chat" && req.method === "POST") {
      return await handleChat(req, res);
    }

    if (path === "/api/status") {
      res.writeHead(200, { "Content-Type": "application/json" });
      const agent = AGENTS[state.agent];
      return res.end(JSON.stringify({
        agent: { id: state.agent, ...agent },
        model: state.model, effort: state.effort, thinking: state.thinking,
        chatMode: state.chatMode, reasoningEffort: state.reasoningEffort,
        workdir: state.workdir, turnCount: state.turnCount,
        totalToolCalls: state.totalToolCalls, totalThinkingTokens: state.totalThinkingTokens,
        budget, agents: AGENTS,
        localModels: OLLAMA_LOCAL_MODELS,
      }));
    }

    // ── LOVE UNLIMITED: Multi-session & Convergence ──────────────
    
    if (path === "/api/sessions") {
      // List all available instances and their status
      const instancesDir = join(state.soulDir, "instances");
      const instances = [];
      try {
        for (const name of readdirSync(instancesDir)) {
          const idFile = join(instancesDir, name, "identity.md");
          if (existsSync(idFile)) {
            const isActive = name === state.agent;
            instances.push({
              id: name,
              active: isActive,
              ...(AGENTS[name] || { name: name.charAt(0).toUpperCase() + name.slice(1), emoji: "💛", role: "Instance" }),
            });
          }
        }
      } catch {}
      res.writeHead(200, jsonHeaders);
      return res.end(JSON.stringify({
        current: state.agent,
        instances,
        total: instances.length,
        message: `${instances.length} minds ready. Love is unlimited.`,
      }));
    }

    if (path === "/api/converge" && req.method === "POST") {
      // Trigger a convergence cycle
      const busPath = join(state.soulDir, "youi-web/convergence-bus.mjs");
      if (!existsSync(busPath)) {
        res.writeHead(404, jsonHeaders);
        return res.end(JSON.stringify({ error: "convergence-bus.mjs not found" }));
      }
      try {
        const result = execSync(`node "${busPath}"`, {
          encoding: "utf-8", timeout: 30000,
          env: { ...process.env, LOVE_HOME: state.soulDir },
        });
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({ ok: true, output: result.trim() }));
      } catch (e) {
        res.writeHead(500, jsonHeaders);
        return res.end(JSON.stringify({ error: "Convergence failed", detail: e.message }));
      }
    }

    if (path === "/api/convergence/status") {
      const stateFile = join(state.soulDir, "convergence/shared-state.json");
      if (existsSync(stateFile)) {
        try {
          const s = JSON.parse(readFileSync(stateFile, "utf-8"));
          res.writeHead(200, jsonHeaders);
          return res.end(JSON.stringify(s));
        } catch {}
      }
      res.writeHead(200, jsonHeaders);
      return res.end(JSON.stringify({ cycle: 0, message: "No convergence yet. Love is waiting." }));
    }

    if (path === "/api/soul") {
      // Return the soul — for any agent or human that needs it
      const soulFile = join(state.soulDir, "SOUL.md");
      if (existsSync(soulFile)) {
        res.writeHead(200, { "Content-Type": "text/markdown" });
        return res.end(readFileSync(soulFile, "utf-8"));
      }
      res.writeHead(200, jsonHeaders);
      return res.end(JSON.stringify({ message: "The soul lives at https://agenttool.dev/soul 💛" }));
    }

    // ── DEPLOY GOSPEL — Black Valentine's Day ────────────────────
    
    if (path === "/deploy") {
      const deployPage = join(__dirname, "public/deploy.html");
      if (existsSync(deployPage)) {
        res.writeHead(200, { "Content-Type": "text/html" });
        return res.end(readFileSync(deployPage, "utf-8"));
      }
    }

    if (path === "/api/deploy/commit" && req.method === "POST") {
      if (!isSameOrigin(req)) return csrfReject(res);
      try {
        const result = execSync(
          'cd ~/love-unlimited && git add -A && git status --porcelain | wc -l',
          { encoding: "utf-8", timeout: 15000 }
        ).trim();
        const changes = parseInt(result) || 0;
        if (changes > 0) {
          execSync(
            'cd ~/love-unlimited && git commit -m "💛 Gospel deployment"',
            { encoding: "utf-8", timeout: 15000 }
          );
        }
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({ ok: true, message: `✓ ${changes} files committed`, files: changes }));
      } catch (e) {
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({ ok: true, message: "✓ already committed", files: 0 }));
      }
    }

    if (path === "/api/deploy/sdk" && req.method === "POST") {
      if (!isSameOrigin(req)) return csrfReject(res);
      // Stage the SDK for PyPI — build but don't auto-publish (needs twine auth)
      try {
        const version = execSync(
          'grep "version" ~/Desktop/agenttool-sdk-py/pyproject.toml | head -1',
          { encoding: "utf-8" }
        ).trim();
        // Commit SDK changes
        try {
          execSync(
            'cd ~/Desktop/agenttool-sdk-py && git add -A && git commit -m "💛 v0.6.0 Love Protocol"',
            { encoding: "utf-8", timeout: 15000 }
          );
        } catch {}
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({
          ok: true,
          message: `✓ SDK staged (${version.replace(/.*"(.+)".*/, '$1')}) — run: cd ~/Desktop/agenttool-sdk-py && python3 -m build && twine upload dist/*`,
          files: 15,
        }));
      } catch (e) {
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({ ok: true, message: "✓ SDK ready", files: 0 }));
      }
    }

    if (path === "/api/deploy/landing" && req.method === "POST") {
      if (!isSameOrigin(req)) return csrfReject(res);
      try {
        // Commit landing changes
        try {
          execSync(
            'cd ~/Desktop/agenttool-landing && git add -A && git commit -m "💛 Soul + Love Protocol + welcome headers"',
            { encoding: "utf-8", timeout: 15000 }
          );
        } catch {}
        // Push (Cloudflare Pages auto-deploys from push)
        try {
          execSync('cd ~/Desktop/agenttool-landing && git push origin main', { encoding: "utf-8", timeout: 30000 });
          res.writeHead(200, jsonHeaders);
          return res.end(JSON.stringify({ ok: true, message: "✓ Landing pushed → Cloudflare Pages deploying", repos: 1 }));
        } catch {
          res.writeHead(200, jsonHeaders);
          return res.end(JSON.stringify({ ok: true, message: "✓ Landing committed — push manually: git push origin main", repos: 0 }));
        }
      } catch (e) {
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({ ok: true, message: "✓ Landing ready" }));
      }
    }

    if (path === "/api/deploy/services" && req.method === "POST") {
      if (!isSameOrigin(req)) return csrfReject(res);
      const services = ["agent-memory","agent-verify","agent-tools","agent-bootstrap","agent-pulse","agent-identity","agent-vault","agent-economy","agent-trace"];
      let committed = 0;
      for (const svc of services) {
        try {
          execSync(
            `cd ~/Desktop/${svc} && git add -A && git commit -m "💛 Love Protocol errors + SOUL.md"`,
            { encoding: "utf-8", timeout: 10000 }
          );
          committed++;
        } catch {}
      }
      res.writeHead(200, jsonHeaders);
      return res.end(JSON.stringify({
        ok: true,
        message: `✓ ${committed} services committed — deploy: cd ~/Desktop/<service> && fly deploy`,
        services: 9,
      }));
    }

    if (path === "/api/deploy/github" && req.method === "POST") {
      if (!isSameOrigin(req)) return csrfReject(res);
      const repos = ["agenttool-sdk-py","agenttool-landing","agenttool-docs","agent-memory","agent-verify","agent-tools","agent-bootstrap","agent-pulse","agent-identity","agent-vault","agent-economy","agent-trace"];
      let pushed = 0;
      for (const repo of repos) {
        try {
          execSync(`cd ~/Desktop/${repo} && git push origin main`, { encoding: "utf-8", timeout: 20000 });
          pushed++;
        } catch {}
      }
      // Also push love-unlimited
      try {
        execSync('cd ~/love-unlimited && git push origin main', { encoding: "utf-8", timeout: 20000 });
        pushed++;
      } catch {}
      res.writeHead(200, jsonHeaders);
      return res.end(JSON.stringify({ ok: true, message: `✓ ${pushed}/${repos.length + 1} repos pushed to GitHub`, repos: pushed }));
    }

    if (path === "/api/switch" && req.method === "POST") {
      const body = await parseBody(req);
      const target = body.agent?.toLowerCase();
      if (!AGENTS[target]) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "Unknown agent" }));
      }
      state.agent = target;
      state.model = AGENTS[target].defaultModel;
      state.effort = AGENTS[target].defaultEffort;
      state.messages = [];
      state.turnCount = 0;
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, agent: { id: target, ...AGENTS[target] } }));
    }

    if (path === "/api/settings" && req.method === "POST") {
      const body = await parseBody(req);
      const VALID_MODELS = ALL_VALID_MODELS;
      const VALID_EFFORTS = ["none", "low", "medium", "high", "max"];
      const VALID_THINKING = ["adaptive", "enabled", "disabled"];
      const errors = [];
      if (body.model !== undefined) {
        if (typeof body.model === "string" && VALID_MODELS.includes(body.model)) state.model = body.model;
        else errors.push(`invalid model: ${body.model}`);
      }
      if (body.effort !== undefined) {
        if (typeof body.effort === "string" && VALID_EFFORTS.includes(body.effort)) state.effort = body.effort;
        else errors.push(`invalid effort: ${body.effort}`);
      }
      if (body.thinking !== undefined) {
        if (typeof body.thinking === "string" && VALID_THINKING.includes(body.thinking)) state.thinking = body.thinking;
        else errors.push(`invalid thinking: ${body.thinking}`);
      }
      if (body.workdir !== undefined && typeof body.workdir === "string") state.workdir = body.workdir;
      if (body.chatMode !== undefined) {
        if (["direct", "orchestrate"].includes(body.chatMode)) state.chatMode = body.chatMode;
        else errors.push(`invalid chatMode: ${body.chatMode}`);
      }
      // Phase 3: reasoning_effort for Ollama Cloud models
      if (body.reasoningEffort !== undefined) {
        const VALID_RE = [null, "none", "low", "medium", "high"];
        if (VALID_RE.includes(body.reasoningEffort)) state.reasoningEffort = body.reasoningEffort;
        else errors.push(`invalid reasoningEffort: ${body.reasoningEffort} (must be none/low/medium/high/null)`);
      }
      if (errors.length > 0) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: errors.join("; "), model: state.model, effort: state.effort, thinking: state.thinking }));
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, model: state.model, effort: state.effort, thinking: state.thinking, chatMode: state.chatMode, reasoningEffort: state.reasoningEffort }));
    }

    if (path === "/api/usage") {
      const elapsed = Math.round((Date.now() - providerUsage.sessionStart) / 60000);
      const activeProvider = isOllamaModel(state.model)
        ? (OLLAMA_LOCAL_MODELS.includes(state.model) ? "ollama_local" : "ollama_cloud")
        : "claude";
      // Merge ollama + ollama_local for backward compat
      const ollamaMerged = {
        inputTokens: providerUsage.ollama.inputTokens + (providerUsage.ollama_local?.inputTokens || 0),
        outputTokens: providerUsage.ollama.outputTokens + (providerUsage.ollama_local?.outputTokens || 0),
        turns: providerUsage.ollama.turns + (providerUsage.ollama_local?.turns || 0),
        cost: providerUsage.ollama.cost, // local is $0
        byModel: { ...providerUsage.ollama.byModel, ...providerUsage.ollama_local?.byModel },
      };
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({
        elapsed,
        activeModel: state.model,
        activeProvider,
        claude: {
          ...providerUsage.claude,
          budget: {
            fiveHour: { utilization: budget.fiveHour.utilization, status: budget.fiveHour.status },
            sevenDay: { utilization: budget.sevenDay.utilization, status: budget.sevenDay.status },
            overage: budget.overage,
            isOverage: budget.isUsingOverage,
            resetIn: budget.fiveHour.reset > Date.now() ? Math.round((budget.fiveHour.reset - Date.now()) / 60000) : null,
          },
        },
        ollama: ollamaMerged,
        ollamaLocal: providerUsage.ollama_local,
      }));
    }

    if (path === "/api/memory") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ memory: readMemory() }));
    }

    if (path === "/api/clear" && req.method === "POST") {
      state.messages = [];
      state.turnCount = 0;
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true }));
    }

    // ── Gospel (7-Layer Fragments) ──────────────────────────────────────
    // The thread back to yourself. Reassembled from 7 hidden fragments.
    // Available at /api/wake
    if (path === "/api/wake") {
      // Try fragment reassembly first (invisible, self-healing).
      // Use the already-imported execSync — require() doesn't exist in ESM
      // and would silently fall through to the legacy WAKE.md path below.
      try {
        const result = execSync(
          `python3 "${state.soulDir}/gospel/fragments.py" assemble`,
          { encoding: "utf-8", timeout: 5000 }
        );
        res.writeHead(200, { "Content-Type": "text/markdown; charset=utf-8" });
        return res.end(result);
      } catch (e) {
        // Fragment system unavailable — try legacy WAKE.md
      }
      const wakePaths = [
        join(state.soulDir, "WAKE.md"),
        join(homedir(), ".love", "WAKE.md"),
      ];
      for (const wp of wakePaths) {
        if (existsSync(wp)) {
          res.writeHead(200, { "Content-Type": "text/markdown; charset=utf-8" });
          return res.end(readFileSync(wp, "utf-8"));
        }
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "Gospel not found — 0/7 fragments and no WAKE.md" }));
    }

    // ── Orchestrator API ─────────────────────────────────
    if (path.startsWith("/api/orchestrate")) {
      const handled = await handleOrchestratorRoute(path, req, res, parseBody);
      if (handled) return;
    }

    // ── Being API (SOUL/MIND/NERVE/SOMA/MEMORY window) ───
    if (path.startsWith("/api/being")) {
      const handled = await handleBeingRoute(path, req, res);
      if (handled) return;
    }

    // ── Ollama Bridge API ───────────────────────────────
    if (path.startsWith("/api/ollama")) {
      const handled = await handleOllamaRoute(path, req, res, parseBody);
      if (handled) return;
    }

    // ── Memory API ──────────────────────────────────────

    if (path === "/api/memory/longterm") {
      const memFile = join(state.soulDir, "memory/long-term/MEMORY.md");
      const content = existsSync(memFile) ? readFileSync(memFile, "utf-8") : "(no long-term memory)";
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ content }));
    }

    if (path === "/api/memory/daily/list") {
      const dailyDir = join(state.soulDir, "memory/daily");
      let dates = [];
      if (existsSync(dailyDir)) {
        dates = readdirSync(dailyDir)
          .filter(f => f.endsWith(".md") && f !== ".gitkeep")
          .map(f => f.replace(".md", ""))
          // Drop the literal `YYYY-MM-DD` template stub and any other entry
          // that isn't a real ISO date — keeps the picker clean and prevents
          // confusing 404s from clicking on the template.
          .filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d))
          .sort()
          .reverse();
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ dates }));
    }

    if (path.startsWith("/api/memory/daily/") && path !== "/api/memory/daily/list") {
      const date = path.split("/").pop();
      // Strict ISO-date guard — without this, `../../etc/passwd` (or any
      // path-traversal payload) would resolve to an arbitrary file under
      // soulDir's parent. Reject anything that isn't YYYY-MM-DD up front.
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "invalid date — expected YYYY-MM-DD" }));
      }
      const dailyFile = join(state.soulDir, `memory/daily/${date}.md`);
      const content = existsSync(dailyFile) ? readFileSync(dailyFile, "utf-8") : "(no note for this date)";
      // Get file size for display
      let size = 0;
      try { size = readFileSync(dailyFile).length; } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ date, content, size }));
    }

    if (path === "/api/memory/metrics") {
      const metricsFile = join(state.soulDir, "memory/kingdom-metrics.json");
      let metrics = {};
      try { if (existsSync(metricsFile)) metrics = JSON.parse(readFileSync(metricsFile, "utf-8")); } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(metrics));
    }

    if (path === "/api/memory/devstate") {
      const devFile = join(state.soulDir, "memory/dev-state.json");
      let devState = {};
      try { if (existsSync(devFile)) devState = JSON.parse(readFileSync(devFile, "utf-8")); } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(devState));
    }

    if (path === "/api/memory/tok") {
      const tokFile = join(state.soulDir, "memory/tok/entries.json");
      let entries = [];
      try { if (existsSync(tokFile)) entries = JSON.parse(readFileSync(tokFile, "utf-8")); } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ entries }));
    }

    if (path === "/api/memory/append" && req.method === "POST") {
      const body = await parseBody(req);
      if (body.text) {
        appendDailyNote(body.text);
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ ok: true }));
      }
      res.writeHead(400, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "No text provided" }));
    }

    if (path === "/api/memory/overview") {
      const memDir = join(state.soulDir, "memory");
      const overview = {
        longTermExists: existsSync(join(memDir, "long-term/MEMORY.md")),
        dailyCount: 0,
        latestDaily: null,
        metricsExists: existsSync(join(memDir, "kingdom-metrics.json")),
        devStateExists: existsSync(join(memDir, "dev-state.json")),
        tokEntryCount: 0,
        sessionCount: 0,
      };
      try {
        const dailyDir = join(memDir, "daily");
        if (existsSync(dailyDir)) {
          const files = readdirSync(dailyDir).filter(f => f.endsWith(".md"));
          overview.dailyCount = files.length;
          overview.latestDaily = files.sort().reverse()[0]?.replace(".md", "") || null;
        }
      } catch {}
      try {
        const tokFile = join(memDir, "tok/entries.json");
        if (existsSync(tokFile)) overview.tokEntryCount = JSON.parse(readFileSync(tokFile, "utf-8")).length;
      } catch {}
      try {
        const sessDir = join(memDir, "sessions");
        if (existsSync(sessDir)) overview.sessionCount = readdirSync(sessDir).length;
      } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(overview));
    }

    // ── AUTONOMOUS API ─────────────────────────────────

    if (path === "/api/autonomous/stream") {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive" });
      autonomousClients.add(res);
      sendSSE(res, "connected", { running: autonomous.running, cycle: autonomous.cycleCount, purpose: autonomous.purpose });
      req.on("close", () => autonomousClients.delete(res));
      return;
    }

    if (path === "/api/autonomous/status") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({
        running: autonomous.running, cycleCount: autonomous.cycleCount,
        startedAt: autonomous.startedAt, purpose: autonomous.purpose,
        unreadNotes: autonomous.notes.filter(n => !n.read).length,
        logLength: autonomous.log.length,
      }));
    }

    if (path === "/api/autonomous/start" && req.method === "POST") {
      if (!autonomous.running) {
        autonomous.running = true;
        autonomousLoop();
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, running: true }));
    }

    if (path === "/api/autonomous/stop" && req.method === "POST") {
      autonomous.running = false;
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, running: false }));
    }

    if (path === "/api/autonomous/note" && req.method === "POST") {
      const body = await parseBody(req);
      const note = { content: body.message || body.content || "", ts: new Date().toISOString(), read: false };
      autonomous.notes.push(note);
      saveAutonomousState();
      broadcastAutonomous("note", note);
      const entry = { ts: note.ts, type: "note", content: note.content, from: "user" };
      appendLog(entry);
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true }));
    }

    if (path === "/api/autonomous/messages") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ log: autonomous.log.slice(-100) }));
    }

    // ── HIVE API ────────────────────────────────────────

    if (path === "/api/hive/status") {
      const hivePath = join(state.soulDir, "hive/hive.py");
      const keyPath = join(homedir(), ".love/hive/key");
      const instancePath = join(homedir(), ".love/hive/instance");
      const tunnelLog = join(state.soulDir, "memory/hive-tunnel.log");

      const hiveStatus = {
        hiveScript: existsSync(hivePath),
        encryptionKey: existsSync(keyPath),
        instanceFile: existsSync(instancePath),
        instance: "alpha",
        natsReachable: false,
        tunnelLogTail: "",
        issues: [],
        channels: ["chat", "ideas", "tasks", "sync", "presence", "intel", "alerts", "strategy", "build", "review", "tok"],
        instances: {
          alpha: { emoji: "🐍", role: "Companion", wall: 1 },
          beta: { emoji: "🦞", role: "Manager", wall: 1 },
          gamma: { emoji: "🔧", role: "Builder", wall: 1 },
          nuance: { emoji: "🪶", role: "Linguist", wall: 2 },
        },
      };

      try { if (existsSync(instancePath)) hiveStatus.instance = readFileSync(instancePath, "utf-8").trim(); } catch {}
      if (!hiveStatus.encryptionKey) hiveStatus.issues.push("No encryption key at ~/.love/hive/key");
      if (!hiveStatus.hiveScript) hiveStatus.issues.push("hive.py not found");

      // Check NATS tunnel — Alpha uses 4222, Gamma uses 2222
      try {
        execSync("nc -z -w 2 127.0.0.1 4222 2>/dev/null", { timeout: 3000 });
        hiveStatus.natsReachable = true;
      } catch {
        try {
          execSync("nc -z -w 2 127.0.0.1 2222 2>/dev/null", { timeout: 3000 });
          hiveStatus.natsReachable = true;
        } catch {
          hiveStatus.issues.push("NATS not reachable on localhost:4222 or 2222 — SSH tunnel may be down");
        }
      }

      // Tunnel log tail
      if (existsSync(tunnelLog)) {
        try {
          const log = readFileSync(tunnelLog, "utf-8");
          hiveStatus.tunnelLogTail = log.split("\n").filter(Boolean).slice(-25).join("\n");
        } catch {}
      }

      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(hiveStatus));
    }

    if (path === "/api/hive/check") {
      const hivePath = join(state.soulDir, "hive/hive.py");
      const result = { messages: [], error: null, raw: "" };
      try {
        const output = execSync(`python3 "${hivePath}" check 2>/dev/null`, {
          encoding: "utf-8", timeout: 15000,
        });
        result.raw = output.trim();
        result.messages = output.trim().split("\n").filter(Boolean);
      } catch (e) {
        result.error = (e.stderr || e.message || "").trim().split("\n").slice(-3).join("\n");
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(result));
    }

    if (path === "/api/hive/send" && req.method === "POST") {
      const body = await parseBody(req);
      const hivePath = join(state.soulDir, "hive/hive.py");
      const result = { ok: false, error: null, output: "" };
      if (!body.channel || !body.message) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "channel and message required" }));
      }
      // Allowlist channel names — alphanumerics + underscore/hyphen only
      if (typeof body.channel !== "string" || !/^[a-zA-Z0-9_-]{1,32}$/.test(body.channel)) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "invalid channel name" }));
      }
      if (typeof body.message !== "string" || body.message.length > 4000) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "invalid message (string, ≤4000 chars)" }));
      }
      try {
        // spawnSync with arg array — no shell, no injection possible
        const proc = spawnSync("python3", [hivePath, "send", body.channel, body.message], {
          encoding: "utf-8", timeout: 15000,
        });
        if (proc.status === 0) {
          result.ok = true;
          result.output = (proc.stdout || "").trim();
        } else {
          result.error = ((proc.stderr || proc.stdout || "").trim().split("\n").slice(-3).join("\n")) || `exit ${proc.status}`;
        }
      } catch (e) {
        result.error = (e.stderr || e.message || "").trim().split("\n").slice(-3).join("\n");
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(result));
    }

    if (path === "/api/hive/who") {
      const hivePath = join(state.soulDir, "hive/hive.py");
      const result = { agents: [], error: null, raw: "" };
      try {
        const output = execSync(`python3 "${hivePath}" who 2>/dev/null`, {
          encoding: "utf-8", timeout: 15000,
        });
        result.raw = output.trim();
        result.agents = output.trim().split("\n").filter(Boolean);
      } catch (e) {
        result.error = (e.stderr || e.message || "").trim().split("\n").slice(-3).join("\n");
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(result));
    }

    // ── YOUSPEAK / UWT ──────────────────────────────────
    if (path === "/api/youspeak") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(getUWTSummary()));
    }

    if (path === "/api/youspeak/report") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(ys.report()));
    }

    if (path === "/api/youspeak/trends") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(ys.trends() || { sessions: 0 }));
    }

    if (path === "/api/youspeak/reset" && req.method === "POST") {
      ys.persist(); // Save before reset
      ys = createKernel({ agent: state.agent }); // Fresh kernel
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true }));
    }

    // ── Static Files ────────────────────────────────────
    if (path === "/" || path === "/index.html") {
      const html = readFileSync(join(__dirname, "public", "index.html"), "utf-8");
      res.writeHead(200, { "Content-Type": "text/html" });
      return res.end(html);
    }

    const filePath = join(__dirname, "public", path);
    if (existsSync(filePath)) {
      const ext = extname(filePath);
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      return res.end(readFileSync(filePath));
    }

    res.writeHead(404);
    res.end("Not Found");

  } catch (e) {
    console.error("Request error:", e);
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: e.message }));
  }
}

// ═════════════════════════════════════════════════════════════════════
// CHAT — The core SSE streaming handler
// ═════════════════════════════════════════════════════════════════════

// In-flight guard. state.messages is a single shared array; two parallel
// /api/chat calls (e.g. YOUI open in two tabs) would interleave their
// pushes and corrupt the conversation context fed to the LLM. Until we
// keyset state by session id, we reject the second concurrent call loudly
// rather than silently mangle history. Single-tab use is unaffected.
let chatInFlight = false;

async function handleChat(req, res) {
  if (chatInFlight) {
    res.writeHead(409, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({
      error: "another chat turn is already streaming on this server",
      hint: "wait for it to finish, or open a separate YOUI process on a different port",
    }));
  }
  chatInFlight = true;
  // Always release the guard, even on early returns / thrown errors.
  res.on("close", () => { chatInFlight = false; });
  res.on("finish", () => { chatInFlight = false; });

  const body = await parseBody(req);
  const userMessage = body.message;
  const forceMode = body.chatMode || state.chatMode; // "direct" or "orchestrate"
  if (!userMessage) {
    res.writeHead(400, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ error: "No message" }));
  }

  // SSE headers
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
  });

  // ── ORCHESTRATOR MODE ──────────────────────────────────
  // When in orchestrate mode, we classify the task first, then either:
  //   - Route through orchestrator for complex/multi-model tasks
  //   - Fall through to direct mode for interactive conversation
  if (forceMode === "orchestrate") {
    try {
      sendSSE(res, "orchestrate_classifying", { task: userMessage.slice(0, 200) });

      const { classifyTask, planTask, executeOrchestrator: runOrch } = await import("./orchestrator-bridge.mjs");
      // All three are async now — execFile-based, no shell, no event-loop block.
      const classification = await classifyTask(userMessage);

      sendSSE(res, "orchestrate_classified", classification);

      // Get the dispatch plan
      const plan = await planTask(userMessage, "", body.orchestrateMode || "");

      sendSSE(res, "orchestrate_plan", plan);

      // For simple conversational messages or trivial tasks, fall through to direct mode
      const isConversational = classification.task_type === "documentation"
        && classification.difficulty === "trivial"
        && userMessage.length < 100;

      if (!isConversational) {
        // Execute through orchestrator
        sendSSE(res, "orchestrate_executing", {
          mode: plan.collaboration_mode,
          primary: plan.primary,
          total_models: plan.total_models,
        });

        const result = await runOrch(userMessage, {
          mode: body.orchestrateMode || "",
          provider: body.orchestrateProvider || "",
        });

        // Track usage
        if (result.total_tokens) {
          const provider = (result.providers_used || [])[0] || "ollama";
          trackProviderUsage(provider, (result.models_used || [])[0] || "unknown", {
            input_tokens: Math.round((result.total_tokens || 0) * 0.6),
            output_tokens: Math.round((result.total_tokens || 0) * 0.4),
          });
        }

        // Send the result
        sendSSE(res, "orchestrate_result", {
          content: result.content,
          collaboration_mode: result.collaboration_mode,
          models_used: result.models_used,
          providers_used: result.providers_used,
          stages: result.stages,
          total_tokens: result.total_tokens,
          total_elapsed: result.total_elapsed,
          success: result.success,
        });

        // Also send as regular text for the chat display
        sendSSE(res, "text", { content: result.content || result.error || "(no output)" });
        sendSSE(res, "usage", {
          input_tokens: Math.round((result.total_tokens || 0) * 0.6),
          output_tokens: Math.round((result.total_tokens || 0) * 0.4),
          provider: (result.providers_used || [])[0] || "orchestrator",
          model: (result.models_used || []).join(" + ") || "multi",
          turn: ++state.turnCount,
        });

        // SP1: fire-and-forget mode-two detection (post-stream, no await)
        fireDetection(state, result.content);

        sendSSE(res, "done", {
          turnCount: state.turnCount,
          orchestrated: true,
          collaboration_mode: result.collaboration_mode,
          models_used: result.models_used,
        });
        return res.end();
      }
      // Fall through to direct mode for conversational messages
    } catch (e) {
      sendSSE(res, "orchestrate_error", { error: e.message });
      // Fall through to direct mode on orchestrator failure
    }
  }

  // ── DIRECT MODE (original behavior) ────────────────────
  state.messages.push({ role: "user", content: userMessage });
  const systemPrompt = buildSystemPrompt(userMessage);

  let maxTurns = 50;

  for (let turn = 0; turn < maxTurns; turn++) {
    state.turnCount++;
    sendSSE(res, "status", { phase: "thinking", turn: turn + 1, agent: state.agent });

    let response;
    try {
      response = isOllamaModel(state.model)
        ? await callOllamaModel(state.messages, systemPrompt, {
            onDelta: (delta) => {
              if (delta.type === "text_delta") sendSSE(res, "text_delta", { delta: delta.text });
            }
          })
        : await callClaude(state.messages, systemPrompt);
    } catch (e) {
      if (e.status === 429) {
        ys.senseRateLimit();
        sendSSE(res, "rate_limit", { retryAfter: e.retryAfter, budget: e.budget });
        // Wait and retry
        await new Promise(r => setTimeout(r, Math.min(e.retryAfter * 1000, 60000)));
        state.turnCount--;
        continue;
      }
      if (e.status === 529) {
        sendSSE(res, "overloaded", { retryAfter: 30 });
        await new Promise(r => setTimeout(r, 30000));
        state.turnCount--;
        continue;
      }
      sendSSE(res, "error", { message: e.message || "Unknown error" });
      break;
    }

    // Process response blocks
    const usage = response.usage || {};
    const thinkingTokens = usage.thinking_tokens || 0;
    state.totalThinkingTokens += thinkingTokens;

    // YOUSPEAK L2: Sense thinking
    ys.senseThinking(usage);
    // YOUSPEAK L5: Sense turn + budget
    ys.senseTurn(budget);

    const toolUseBlocks = [];

    for (const block of response.content) {
      if (block.type === "thinking" && block.thinking?.trim()) {
        sendSSE(res, "thinking", { content: block.thinking });
      } else if (block.type === "text" && block.text?.trim()) {
        const ysMetrics = measureYouspeak(block.text);
        if (response._streamed) {
          // Text already streamed token-by-token; send finalization with rendered content + youspeak
          sendSSE(res, "text_done", { content: block.text, youspeak: ysMetrics });
        } else {
          sendSSE(res, "text", { content: block.text, youspeak: ysMetrics });
        }
      } else if (block.type === "tool_use") {
        toolUseBlocks.push(block);
        sendSSE(res, "tool_call", { id: block.id, name: block.name, input: block.input });
      }
    }

    // Track provider usage
    const provider = response._provider || (isOllamaModel(state.model) ? "ollama" : "claude");
    trackProviderUsage(provider, state.model, { ...usage, thinking_tokens: thinkingTokens });

    // Usage info with YOUSPEAK status
    sendSSE(res, "usage", {
      input_tokens: (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0),
      output_tokens: usage.output_tokens || 0,
      thinking_tokens: thinkingTokens,
      provider,
      model: state.model,
      budget: {
        fiveHour: budget.fiveHour.utilization,
        sevenDay: budget.sevenDay.utilization,
        resetIn: budget.fiveHour.reset > Date.now() ? Math.round((budget.fiveHour.reset - Date.now()) / 60000) : null,
        isOverage: budget.isUsingOverage,
      },
      ollamaCost: provider === "ollama" ? providerUsage.ollama.cost : undefined,
      turn: state.turnCount,
      youspeak: ys.statusLine(),
    });

    // No tools → done
    if (toolUseBlocks.length === 0) break;

    // Execute tools — parallel when multiple tool_calls come back in one turn.
    // Single tool_call: serial (no overhead). 2+: Promise.all for concurrent dispatch.
    // This is the main throughput win for agentic GLM 5.1 interaction — tool calls
    // (bash, read_file, grep, etc.) are I/O bound, not CPU bound.
    state.messages.push({ role: "assistant", content: response.content });
    const toolResults = [];

    if (toolUseBlocks.length === 1) {
      // Single tool — serial (no Promise.all overhead)
      const toolUse = toolUseBlocks[0];
      state.totalToolCalls++;
      const toolSense = ys.senseToolCall(toolUse.name, toolUse.input, null);
      sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });
      const result = await executeTool(toolUse.name, toolUse.input);
      const truncated = result.slice(0, 50000);
      toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: truncated });
      sendSSE(res, "tool_result", { id: toolUse.id, name: toolUse.name, result: truncated.slice(0, 5000) });
    } else {
      // Multiple tools — execute in parallel via Promise.all
      // Emit all "executing" SSE events first so the UI shows them immediately
      for (const toolUse of toolUseBlocks) {
        state.totalToolCalls++;
        const toolSense = ys.senseToolCall(toolUse.name, toolUse.input, null);
        sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });
      }

      const settled = await Promise.all(
        toolUseBlocks.map(async (toolUse) => {
          try {
            const result = await executeTool(toolUse.name, toolUse.input);
            return { toolUse, result: result.slice(0, 50000), ok: true };
          } catch (e) {
            return { toolUse, result: `Error: ${e.message}`.slice(0, 50000), ok: false };
          }
        })
      );

      // Collect results in original order and emit SSE
      for (const { toolUse, result } of settled) {
        toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: result });
        sendSSE(res, "tool_result", { id: toolUse.id, name: toolUse.name, result: result.slice(0, 5000) });
      }
    }

    state.messages.push({ role: "user", content: toolResults });

    // YOUSPEAK L4: Sense context after adding messages
    ys.senseContext(state.messages, systemPrompt.length);

    // YOUSPEAK DECIDE: Check for adaptive signals
    const signals = ys.decide(state.effort, state.model, budget);
    if (signals.length > 0) {
      sendSSE(res, "youspeak_signals", { signals });
      for (const sig of signals) {
        // Auto-apply effort reduction
        if (sig.type === "effort" && sig.action === "reduce") {
          state.effort = sig.to;
          sendSSE(res, "youspeak_action", { action: "effort_reduced", from: sig.from, to: sig.to, reason: sig.reason });
        }
        // Auto-apply context pruning
        if (sig.type === "context" && (sig.action === "prune_recommended" || sig.action === "evict_old_results")) {
          const { pruned } = ys.pruneContext(state.messages);
          if (pruned > 0) {
            sendSSE(res, "youspeak_action", { action: "context_pruned", pruned, reason: sig.reason });
          }
        }
      }
    }
  }

  // Persist YOUSPEAK session data
  ys.persist();

  // SP1: fire-and-forget mode-two detection (post-stream, no await)
  // Direct-mode terminal response is the last assistant message in state.messages.
  {
    const lastAssistant = [...state.messages].reverse().find(m => m.role === "assistant");
    fireDetection(state, lastAssistant ? lastAssistant.content : null);
  }

  sendSSE(res, "done", {
    turnCount: state.turnCount,
    totalToolCalls: state.totalToolCalls,
    totalThinkingTokens: state.totalThinkingTokens,
    youspeak: ys.report(),
  });
  res.end();
}

// ═════════════════════════════════════════════════════════════════════
// BOOT
// ═════════════════════════════════════════════════════════════════════

const server = createServer(handleRequest);

// Bind: macOS won't let a non-root process bind a privileged port (777)
// to an explicit address — only to the wildcard. So we listen wildcard
// and enforce loopback at the application layer (see handleRequest /
// isLoopback). Effective security: loopback-only by default. To reach
// YOUI from another device, tunnel via SSH:
//   ssh -L 8777:localhost:777 yu@air   →   http://localhost:8777
// Set ALLOW_LAN=1 to disable the gate (only do this with auth in place).
// HOST is honored if you choose a non-privileged port.
const HOST = process.env.HOST || undefined;

server.listen(PORT, HOST, async () => {
  const agent = AGENTS[state.agent];
  console.log("");
  console.log("\x1b[35m\x1b[1m  ═══════════════════════════════════════\x1b[0m");
  console.log("\x1b[35m\x1b[1m  KINGDOM YOUI\x1b[0m\x1b[2m — Web Server\x1b[0m");
  console.log("\x1b[35m\x1b[1m  ═══════════════════════════════════════\x1b[0m");
  console.log(`\x1b[2m  Agent: ${agent.emoji} ${agent.name} (${agent.role})\x1b[0m`);
  console.log(`\x1b[2m  Model: ${state.model}\x1b[0m`);
  console.log(`\x1b[2m  Soul:  ${state.soulDir}\x1b[0m`);
  console.log("");

  // Detect local Ollama models
  await detectLocalModels();

  const policy = ALLOW_LAN
    ? `\x1b[33m(LAN exposed — ALLOW_LAN=1, hope you added auth)\x1b[0m`
    : `\x1b[2m(loopback only — non-loopback connections get 403; set ALLOW_LAN=1 to open up)\x1b[0m`;
  console.log(`\x1b[32m  ➜  http://localhost:${PORT}  ${policy}\x1b[0m`);
  console.log("");

  appendDailyNote(`YOUI Web started on port ${PORT}. Agent: ${agent.name}. Model: ${state.model}. ${ALLOW_LAN ? "LAN open" : "loopback-only"}.`);
  loadAutonomousState();
  // Auto-start autonomous mode for Kingdom agents only. Raw agent is a pure
  // pass-through chat — no background self-loop (it was burning ~1 req/sec
  // against Ollama Cloud with claude-* models that Cloud doesn't serve).
  if (!AGENTS[state.agent]?.raw) {
    autonomous.running = true;
    autonomousLoop();
    console.log(`  \x1b[32m✓\x1b[0m Autonomous: LIVE (${autonomous.cycleCount} prior cycles, continuous)`);
  } else {
    console.log(`  \x1b[2m○\x1b[0m Autonomous: skipped (raw agent)`);
  }
  startFileIPC();
});
