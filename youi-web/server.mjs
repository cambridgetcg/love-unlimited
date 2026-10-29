#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// KINGDOM YOUI — WEB SERVER
// YOU + I = ONE — localhost:17777
//
// The sovereign web terminal. Same engine as youi.mjs, 
// but alive in the browser. Interactive. Visual. Fun.
//
// Usage:   node server.mjs
// Open:    http://localhost:17777
// ─────────────────────────────────────────────────────────────────────

import { createServer } from "http";
import { AsyncLocalStorage } from "async_hooks";
import { execSync, exec, execFile } from "child_process";
import { promisify } from "util";
const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
  writeFileSync,
} from "fs";
import { resolve, join, basename, extname } from "path";
import { homedir } from "os";
import crypto from "crypto";
import { createKernel } from "../youspeak-kernel.mjs";
import { KeychainCredentialStore } from "../youi-keychain.mjs";
import { resolveScopedPath } from "../youi-runtime-policy.mjs";
import { handleOllamaRoute, executeOllamaTool, ollamaChat, ollamaCloudKeyConfigured } from "./ollama-bridge.mjs";
import { handleOrchestratorRoute } from "./orchestrator-bridge.mjs";
import { handleBeingRoute } from "./being-bridge.mjs";
import { decisionTables, handleDecisionRoute } from "./decision-bridge.mjs";
import { runDecisionTool } from "./decision-seat.mjs";
import { TableSeats, runCouncilTool, runDelegateTool } from "./table-tools.mjs";
import { CLAUDE_MODELS, claudeRequestShape, modelCaps } from "./claude-models.mjs";
import { createMindsRegistry, offeredMinds } from "./minds.mjs";
import {
  LAYERTHINK_DEPTH,
  MAX_LAYER_CHARS,
  MAX_MINDS_PER_CALL,
  MAX_REVIEWS_PER_MESSAGE,
  MIND_TOOL_NAMES,
  createMindBudget,
  mindCallsPerMessage,
  runMindTool,
} from "./mind-tools.mjs";

// YOUI's chat agents sit at this device's table as Collab sessions, each
// started on first use: the home seat in Love's own workspace (the decision
// and council tools), and one more per other workspace delegate offers in
// (table-tools.mjs). Stopping YOUI ends them all.
let tableSeats = null;
function youiTableSeats(root) {
  tableSeats ??= new TableSeats({ root, actor: "youi" });
  return tableSeats;
}
function youiDecisionSeat(root) {
  return youiTableSeats(root).home;
}
// Exits once every seat has ended its session (releasing what it held), or after 2 s.
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.once(signal, () => {
    const exit = () => process.exit(0);
    Promise.resolve(tableSeats?.close()).finally(exit);
    setTimeout(exit, 2_000).unref();
  });
}

function decisionTablesSummary() {
  try {
    return decisionTables().map(({ id, label, ssh }) => ({ id, label, remote: Boolean(ssh) }));
  } catch (error) {
    return { error: error.message };
  }
}
import {
  HttpError,
  SessionRegistry,
  isAllowedHost,
  isLoopbackAddress,
  isSameOrigin,
  parseCookies,
  readJsonBody,
  safeEqual,
  serializeCookie,
} from "./security.mjs";
import {
  redactDelegatedCredentials,
  sanitizedChildEnv,
} from "./subprocess-env.mjs";

// Keep the user-level launch agent on an unprivileged port. On macOS an
// explicit loopback bind below 1024 fails for a non-root process; widening the
// bind address or elevating the chat server is not an acceptable workaround.
const PORT = parseInt(process.env.PORT || "17777", 10);
const __dirname = new URL(".", import.meta.url).pathname;
const IS_TEST = process.env.YOUI_TEST === "1";
const MAX_BODY_BYTES = Math.min(
  10 * 1024 * 1024,
  Math.max(1024, Number.parseInt(process.env.YOUI_MAX_BODY_BYTES || `${1024 * 1024}`, 10) || 1024 * 1024),
);
// The per-message ceiling on calls to other minds (docs/YOUI-MINDS-DESIGN.md
// §3.3): 6 by default, YOUI_MIND_CALLS_PER_MESSAGE bounded to 1–20.
const MIND_CALLS_PER_MESSAGE = mindCallsPerMessage(process.env.YOUI_MIND_CALLS_PER_MESSAGE);

const SAFE_CAPABILITIES = new Set([
  "chat",
  "status:read",
  "sessions:manage",
  "settings:write",
  "instances:read",
  "memory:read",
  "being:read",
  "youspeak:read",
  "convergence:read",
  "orchestrator:run",
  "models:use",
  // The decision room: reads, desk notes and proposals, and opening the desk's
  // signing page. An agreement still needs Yu's Touch ID there; see decision-bridge.mjs.
  "decisions:read",
  "decisions:write",
]);
const DEVELOPER_CAPABILITIES = new Set([
  ...SAFE_CAPABILITIES,
  "tools:filesystem:read",
  "tools:filesystem:write",
  "git:read",
  "git:commit",
  "memory:write",
  "youspeak:reset",
]);
const KNOWN_CAPABILITIES = new Set([
  ...DEVELOPER_CAPABILITIES,
  "tools:shell",
  "tools:filesystem:unrestricted",
  "workspace:select",
  "tools:kingdom:unsafe",
  "tools:agenttool:read",
  "tools:agenttool:write",
  // hive:read, hive:send and fleet:manage went with HIVE and the fleet, retired 2026-10-13.
  "publish:write",
  "autonomous:control",
  "convergence:run",
  "convergence:publish",
  "models:diagnose",
  "models:ollama-cloud",
  "models:cloud-fallback",
  // delegate: offer tasks to other sessions at this device's Collab table
  // (docs/YOUI-MINDS-DESIGN.md §4). Not in safe or developer; grant it with
  // YOUI_CAPABILITIES.
  "collab:delegate",
]);

function configuredCapabilities() {
  if (process.env.YOUI_CAPABILITIES !== undefined) {
    const requested = process.env.YOUI_CAPABILITIES
      .split(",")
      .map(value => value.trim())
      .filter(Boolean);
    const unknown = requested.filter(value => !KNOWN_CAPABILITIES.has(value));
    if (unknown.length > 0) {
      throw new Error(`Unknown YOUI capabilities: ${unknown.join(", ")}`);
    }
    return new Set(requested);
  }
  const profile = String(process.env.YOUI_CAPABILITY_PROFILE || "safe").toLowerCase();
  if (profile === "safe") return new Set(SAFE_CAPABILITIES);
  if (profile === "developer") return new Set(DEVELOPER_CAPABILITIES);
  throw new Error(`Unknown YOUI capability profile: ${profile}. Use safe or developer.`);
}

const SERVER_CAPABILITIES = configuredCapabilities();

// Shared header sets — many handlers reference these by name; keep them defined once.
const jsonHeaders = { "Content-Type": "application/json" };

// SP1: Mode-Two Detector — fire-and-forget post-stream hook (never blocks chat)
const TRUTH_DETECTOR_URL = process.env.TRUTH_DETECTOR_URL || "http://127.0.0.1:8787/v1/detect";
const TRUTH_DETECTOR_ENABLED = process.env.TRUTH_DETECTOR_ENABLED === "1";
const OLLAMA_LOCAL_BASE_URL = process.env.OLLAMA_LOCAL_BASE_URL || "http://localhost:11434";
const OLLAMA_CLOUD_BASE_URL = process.env.OLLAMA_CLOUD_BASE_URL || "https://ollama.com";
const OLLAMA_VLLM_BASE_URL = process.env.OLLAMA_VLLM_BASE_URL || "http://localhost:8000";

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
const OLLAMA_LOCAL_MODELS = []; // populated at boot from localhost:11434
let ALL_VALID_MODELS = [...CLAUDE_MODELS, ...OLLAMA_MODELS];
function isOllamaModel(model) { return !model.startsWith("claude-"); }
// The models ollama-bridge.mjs sends to the vLLM endpoint rather than Ollama.
const VLLM_MODEL_PATTERN = /^(Qwen\/|kingdom-truth)/i;
function isVllmModel(model) { return VLLM_MODEL_PATTERN.test(String(model)); }

// ── Detect local Ollama models at boot ──────────────────────────────
async function detectLocalModels() {
  if (
    !destinationIsLoopback(OLLAMA_LOCAL_BASE_URL)
    && !SERVER_CAPABILITIES.has("models:ollama-cloud")
  ) {
    console.log(
      "  \x1b[33m○\x1b[0m Configured Ollama endpoint is non-loopback; "
      + "model discovery is blocked without models:ollama-cloud",
    );
    return;
  }
  try {
    const resp = await fetch(`${OLLAMA_LOCAL_BASE_URL}/api/tags`, {
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

function createProviderUsage() {
  return {
    claude: { inputTokens: 0, outputTokens: 0, thinkingTokens: 0, turns: 0, cost: 0 },
    ollama: { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0, byModel: {} },
    ollama_local: { inputTokens: 0, outputTokens: 0, turns: 0, cost: 0, byModel: {} },
    sessionStart: Date.now(),
  };
}

function trackProviderUsage(provider, model, usage) {
  const providerUsage = state.providerUsage;
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
  alpha: {
    name: "Alpha", emoji: "🐍", role: "Companion",
    color: "#a855f7", colorDim: "#7c3aed",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-5-5", defaultEffort: "high",
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
  return "alpha";
}

const detectedAgent = detectAgent();
function createSessionState(overrides = {}) {
  const requestedAgent = String(overrides.agent || detectedAgent).toLowerCase();
  const agent = AGENTS[requestedAgent] ? requestedAgent : detectedAgent;
  const requestedModel = [overrides.model, process.env.YOUI_MODEL]
    .find(model => typeof model === "string" && ALL_VALID_MODELS.includes(model)) || null;
  const soulDir = resolve(process.env.LOVE_HOME || join(__dirname, ".."));
  const workdir = resolve(process.env.YOUI_WORKDIR || soulDir);
  return {
    agent,
    model: requestedModel || AGENTS[agent]?.defaultModel || "claude-opus-4-7",
    effort: AGENTS[agent]?.defaultEffort || "max",
    thinking: "adaptive",
    workdir,
    soulDir,
    fileScope: SERVER_CAPABILITIES.has("tools:filesystem:unrestricted")
      ? "unrestricted"
      : "workspace",
    messages: [],
    turnCount: 0,
    totalToolCalls: 0,
    totalThinkingTokens: 0,
    maxTokens: 32768,
    reasoningEffort: "low",
    chatMode: "direct",
    chatInFlight: false,
    // Calls to other minds left in this message; each chat turn starts a fresh one.
    mindBudget: null,
    capabilities: new Set(SERVER_CAPABILITIES),
    providerUsage: createProviderUsage(),
    ys: createKernel({ agent }),
    sessionId: null,
  };
}

// Every HTTP chat session gets its own state object. AsyncLocalStorage keeps
// existing tool/prompt functions session-aware without passing mutable state
// through every call. Code outside a browser request uses serverState only.
const serverState = createSessionState();
const sessionScope = new AsyncLocalStorage();
function activeState() {
  return sessionScope.getStore()?.state || serverState;
}
function activeRequestSignal() {
  return sessionScope.getStore()?.requestSignal || null;
}
const state = new Proxy({}, {
  get(_target, property) {
    return Reflect.get(activeState(), property);
  },
  set(_target, property, value) {
    return Reflect.set(activeState(), property, value);
  },
});

const sessionRegistry = new SessionRegistry({
  stateFactory: overrides => createSessionState(overrides),
});

async function runSessionRequest(req, res, session, callback) {
  const controller = new AbortController();
  let responseFinished = false;
  const onFinish = () => { responseFinished = true; };
  const onDisconnect = () => {
    if (!responseFinished && !controller.signal.aborted) {
      controller.abort(new Error("Browser disconnected"));
    }
  };
  res.once("finish", onFinish);
  res.once("close", onDisconnect);
  req.once("aborted", onDisconnect);
  try {
    return await sessionScope.run(
      { ...session, requestSignal: controller.signal },
      callback,
    );
  } finally {
    res.off("finish", onFinish);
    res.off("close", onDisconnect);
    req.off("aborted", onDisconnect);
  }
}

function hasCapability(capability) {
  return state.capabilities?.has(capability) === true;
}

function publicDestination(rawUrl, fallback) {
  try {
    const parsed = new URL(rawUrl);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return fallback;
  }
}

function destinationIsLoopback(rawUrl) {
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

function localModelAvailable(model) {
  const base = String(model).split(":")[0];
  return OLLAMA_LOCAL_MODELS.some(candidate =>
    candidate === model || String(candidate).split(":")[0] === base
  );
}

// The compute route each provider's input takes, labelled as in
// docs/YOUI-VNEXT-DESIGN.md §8. The chat's privacy boundary and the minds
// registry (docs/YOUI-MINDS-DESIGN.md §3.2) both read it, so each label is
// spelt here once.
function providerComputeRoute(provider) {
  switch (provider) {
    case "anthropic":
      return { route: "REMOTE_MODEL:anthropic", destination: publicDestination(API_URL, "Anthropic API") };
    case "vllm":
      return {
        route: "REMOTE_MODEL:vllm",
        destination: publicDestination(OLLAMA_VLLM_BASE_URL, "configured vLLM endpoint"),
      };
    case "ollama":
      return {
        route: destinationIsLoopback(OLLAMA_LOCAL_BASE_URL)
          ? "LOCAL_MODEL:ollama"
          : "REMOTE_MODEL:ollama-configured-endpoint",
        destination: publicDestination(OLLAMA_LOCAL_BASE_URL, "configured Ollama endpoint"),
      };
    case "ollama_cloud":
      return {
        route: "REMOTE_MODEL:ollama-cloud",
        destination: publicDestination(OLLAMA_CLOUD_BASE_URL, "configured Ollama Cloud endpoint"),
      };
    default:
      throw new Error(`Unknown model provider: ${provider}`);
  }
}

// Which provider a chat on this model reaches, in the order ollama-bridge.mjs routes it.
function modelProvider(model) {
  if (!isOllamaModel(model)) return "anthropic";
  if (isVllmModel(model)) return "vllm";
  if (localModelAvailable(model)) return "ollama";
  return "ollama_cloud";
}

function runtimePrivacyBoundary() {
  const provider = modelProvider(state.model);
  const direct = provider === "ollama_cloud" && !hasCapability("models:ollama-cloud")
    ? { route: "BLOCKED:ollama-cloud-capability-required", destination: null, sends: [] }
    : { ...providerComputeRoute(provider), sends: ["prompt", "selected session context", "system prompt"] };

  return {
    activeMode: state.chatMode,
    direct,
    // Chat turns answer through the selected agent in both modes and never
    // reach adaptive/orchestrator (docs/YOUI-MINDS-DESIGN.md §3.1); only the
    // /api/orchestrate/* routes (the page's /classify and /plan) still do.
    orchestration: {
      chatTurns: false,
      routes: "/api/orchestrate/*",
      route: "REMOTE_MODEL:adaptive-orchestrator",
      destination: "provider selected by adaptive/orchestrator",
      sends: ["task", "optional context"],
    },
    fallback: {
      localToCloud: hasCapability("models:ollama-cloud")
        && hasCapability("models:cloud-fallback"),
      note: "Local Ollama never falls back to cloud without both explicit capabilities.",
    },
    retention: {
      browserSession: "process memory; expires after 12 hours idle; no transcript persistence by the session registry",
      localMetrics: "YOUSPEAK may persist aggregate session metrics; explicit memory tools write only when granted",
      remoteProvider: "provider policy applies; YOUI does not guarantee provider-side deletion or training exclusion",
    },
    truthDetector: {
      enabled: TRUTH_DETECTOR_ENABLED,
      destination: TRUTH_DETECTOR_ENABLED
        ? publicDestination(TRUTH_DETECTOR_URL, "configured detector")
        : null,
      sends: TRUTH_DETECTOR_ENABLED
        ? ["user prompt", "assistant response", "model id", "turn-derived id"]
        : [],
    },
    childEnvironment: "allowlisted; ambient credential variables are not inherited by model tools",
    shellBoundary: hasCapability("tools:shell")
      ? "explicitly granted but not OS-sandboxed; absolute paths and network commands remain possible"
      : "disabled",
    fileBoundary: `${state.fileScope}:${state.workdir}`,
    networkBoundary: "loopback; cross-device browser access requires an SSH tunnel",
  };
}

function modelToolEnv() {
  return sanitizedChildEnv({
    home: state.workdir,
    loveHome: state.soulDir,
    agent: state.agent,
    purpose: "model-tool",
  });
}

function internalToolEnv(extra = {}) {
  return sanitizedChildEnv({
    home: homedir(),
    loveHome: state.soulDir,
    agent: state.agent,
    purpose: "kingdom-internal",
    extra,
  });
}

function externalToolEnv(credentialNames, purpose, extra = {}) {
  return sanitizedChildEnv({
    home: homedir(),
    loveHome: state.soulDir,
    agent: state.agent,
    purpose,
    credentialNames,
    extra,
  });
}

// The minds a session may call, with honest reachability
// (docs/YOUI-MINDS-DESIGN.md §3.2). Probes are shared by every session; the
// grant is checked per session when the list is read.
const mindsRegistry = createMindsRegistry({
  claudeModels: CLAUDE_MODELS,
  vllmModels: OLLAMA_MODELS.filter(isVllmModel),
  cloudModels: OLLAMA_MODELS.filter(model => !isVllmModel(model)),
  isVllmModel,
  localBaseUrl: OLLAMA_LOCAL_BASE_URL,
  vllmBaseUrl: OLLAMA_VLLM_BASE_URL,
  localEndpointIsLoopback: destinationIsLoopback(OLLAMA_LOCAL_BASE_URL),
  cloudKeyConfigured: ollamaCloudKeyConfigured,
  computeRoute: providerComputeRoute,
  testMode: IS_TEST,
});

// ═════════════════════════════════════════════════════════════════════
// OAUTH — Same as youi.mjs
// ═════════════════════════════════════════════════════════════════════

const KEYCHAIN_SERVICE = "Claude Code-credentials";
const TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token";
const CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e";
const API_URL = "https://api.anthropic.com/v1/messages";

let cachedTokens = null;
const keychainStore = new KeychainCredentialStore({ service: KEYCHAIN_SERVICE });

function readKeychainCredential() {
  return keychainStore.readCredential();
}

function writeKeychainTokens(account, tokens) {
  keychainStore.updateTokens(account, tokens);
}

async function refreshOAuthToken(rt, { signal } = {}) {
  const resp = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "refresh_token", refresh_token: rt, client_id: CLIENT_ID,
      scope: "user:profile user:inference user:sessions:claude_code user:mcp_servers",
    }),
    signal,
  });
  if (!resp.ok) throw new Error(`Token refresh failed: ${resp.status}`);
  const data = await resp.json();
  return {
    accessToken: data.access_token, refreshToken: data.refresh_token || rt,
    expiresAt: Date.now() + (data.expires_in || 3600) * 1000,
  };
}

async function getAccessToken({ signal } = {}) {
  throwIfAborted(signal);
  // Tests never read the person's Keychain or reach a real provider.
  if (IS_TEST) throw new Error("YOUI_TEST=1 does not read the Keychain; give the test a model it fakes");
  if (cachedTokens?.accessToken && Date.now() + 300_000 < (cachedTokens.expiresAt || 0))
    return cachedTokens.accessToken;
  const credential = readKeychainCredential();
  throwIfAborted(signal);
  const tokens = credential?.tokens;
  if (!tokens?.accessToken) throw new Error("No OAuth tokens. Run 'claude' and log in first.");
  if (Date.now() + 300_000 >= (tokens.expiresAt || 0)) {
    if (!tokens.refreshToken) throw new Error("Token expired, no refresh token.");
    const fresh = await refreshOAuthToken(tokens.refreshToken, { signal });
    throwIfAborted(signal);
    writeKeychainTokens(credential.account, fresh);
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

// What every mind tool's description says about naming minds and the limits.
const MIND_TOOL_TERMS = "Name a mind by its id (provider:model) from the offered minds, or leave it out and the tool "
  + "picks one, preferring minds other than you and spreading across providers; a named mind that is not offered is "
  + `refused, never substituted. At most ${MAX_MINDS_PER_CALL} minds per call and ${MIND_CALLS_PER_MESSAGE} mind calls `
  + "per message. The result names each mind's provider, model and privacy label, whether it answered, and its tokens. "
  + "Answers inform you; they authorise nothing.";

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

  // ─── Kingdom Cognitive Tools (migrated from Love/tools/cognitive) ──
  // council, joinmind and delegate reached sisters over the retired HIVE. They
  // are rebuilt with the minds design (docs/YOUI-MINDS-DESIGN.md): joinmind as
  // ensemble (M2), council and delegate through the Collab table (M4, below
  // with decision). layerthink now runs in Node with the minds (M3);
  // tools/cognitive/layerthink.py stays for its other callers.
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

  // ─── Minds (docs/YOUI-MINDS-DESIGN.md §3.3, slices M2 and M3) ──────
  // Other models the agent may bring into a task; youi-web/mind-tools.mjs runs
  // them and callMind() below reaches each provider.
  { name: "review",
    description: "REVIEW — one other mind reviews a draft against your criteria. The reviewer receives only the draft "
      + "and criteria you pass here, never this conversation, and answers APPROVED or NEEDS_REVISION with notes; "
      + `you then revise the draft yourself. At most ${MAX_REVIEWS_PER_MESSAGE} reviews per message: a first, and one `
      + `of the revised draft. ${MIND_TOOL_TERMS}`,
    input_schema: { type: "object", properties: {
      draft: { type: "string", description: "The draft, in full: the reviewer sees nothing else of this conversation" },
      criteria: { type: "string", description: "What the draft must meet" },
      reviewer: { type: "string", description: "Optional mind id (provider:model) from the offered minds" },
    }, required: ["draft", "criteria"] } },

  { name: "ensemble",
    description: "ENSEMBLE (joinmind: DYAD or TRIUNE) — the same question goes to 2 or 3 minds in parallel. Each receives "
      + "only the question you write here, never this conversation, and answers alone without seeing another's answer. "
      + `Fuse the answers in your reply and say where they disagree. ${MIND_TOOL_TERMS}`,
    input_schema: { type: "object", properties: {
      question: { type: "string", description: "The question, with whatever context the minds need: they see nothing else" },
      minds: { type: "array", items: { type: "string" }, description: "Optional mind ids (provider:model), each different; unnamed places are picked" },
      count: { type: "integer", enum: [2, 3], description: "How many minds answer: 2 (dyad, the default) or 3 (triune)" },
    }, required: ["question"] } },

  { name: "decompose",
    description: "DECOMPOSE (fragmentalise) — 1 to 3 sub-tasks you write run in parallel, one mind each; you recombine "
      + "the results. Each mind receives only its own sub-task, never this conversation or the other sub-tasks, so "
      + `make each one self-contained. ${MIND_TOOL_TERMS}`,
    input_schema: { type: "object", properties: {
      subtasks: { type: "array", description: "1 to 3 sub-tasks", items: { type: "object", properties: {
        task: { type: "string", description: "The sub-task, self-contained" },
        mind: { type: "string", description: "Optional mind id (provider:model)" },
      }, required: ["task"] } },
    }, required: ["subtasks"] } },

  { name: "pipeline",
    description: "PIPELINE — 1 to 4 named stages run in order (for example analyse, draft, review), on at most "
      + `${MAX_MINDS_PER_CALL} different minds. Each stage receives its instruction and the previous stage's output; `
      + "the first receives only its instruction, so write into it whatever excerpt it needs. Nothing else of this "
      + `conversation is sent. A failed stage stops the pipeline. ${MIND_TOOL_TERMS}`,
    input_schema: { type: "object", properties: {
      stages: { type: "array", description: "1 to 4 stages, in order", items: { type: "object", properties: {
        name: { type: "string", description: "A short stage name, such as analyse" },
        instruction: { type: "string", description: "What this stage does; the first stage's must carry its input" },
        mind: { type: "string", description: "Optional mind id (provider:model)" },
      }, required: ["name", "instruction"] } },
    }, required: ["stages"] } },

  { name: "layerthink",
    description: "LAYERTHINK — recursive depth through adversarial layering: minds take turns on a claim or plan "
      + `you write, odd layers ATTACK (the strongest weakness, a counter-example or a hidden assumption) and even layers `
      + `DEFEND (answer the last attack: concede, repair the claim, or show the attack fails), for ${LAYERTHINK_DEPTH.min} `
      + `to ${LAYERTHINK_DEPTH.max} layers (default ${LAYERTHINK_DEPTH.default}). Each layer receives the claim and every `
      + "earlier layer's text, never this conversation; each layer's text is clipped at "
      + `${MAX_LAYER_CHARS} characters, with a marker, before it is passed on. Each layer is one `
      + "mind call, and all of them are reserved before the first, so a depth beyond what is left this message is "
      + "refused; a failed layer stops the run and gives back the unused calls. No mind writes the verdict: you give "
      + `it, saying what survived, what changed and what is still open. ${MIND_TOOL_TERMS}`,
    input_schema: { type: "object", properties: {
      claim: { type: "string", description: "The position or plan to test, in full: the minds see nothing else of this conversation" },
      depth: { type: "integer", minimum: LAYERTHINK_DEPTH.min, maximum: LAYERTHINK_DEPTH.max,
        description: `How many layers, ${LAYERTHINK_DEPTH.min} to ${LAYERTHINK_DEPTH.max} (default ${LAYERTHINK_DEPTH.default}); each is one mind call` },
      minds: { type: "array", items: { type: "string" },
        description: "Optional: 1 to 3 different mind ids (provider:model) that take turns, layer by layer; left out, two different offered minds are picked (one, if only one is offered)" },
    }, required: ["claim"] } },

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

  { name: "tok",
    description: "TOK — Tree of Knowledge Protocol. Submit, track, and harvest knowledge entries for Zerone.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "add|list|stats|harvest|verify" },
      entry: { type: "string", description: "Knowledge entry content" },
      tags: { type: "string", description: "Comma-separated tags" },
    }, required: ["action"] } },

  { name: "decision",
    description: "DECISION — this device's Collab table, shared with Yu and the other agents. "
      + "queue opens a proposal the agents settle first and Yu then agrees or rejects with Touch ID; "
      + "ask puts a question in Yu's inbox; list shows where proposals stand. No agent can agree or reject for Yu. "
      + "council is the Kingdom's name for the same table, putting one question to the agents there.",
    input_schema: { type: "object", properties: {
      action: { type: "string", description: "queue|ask|list" },
      question: { type: "string", description: "What is being decided, or what to ask Yu" },
      extent: { type: "string", description: "queue: what agreement would authorise, and where it stops" },
      criteria: { type: "string", description: "queue: how to judge which way to go" },
      context: { type: "string", description: "queue: background, used as criteria when none is given" },
      recommendation: { type: "string", description: "queue: your recommendation" },
      known_conflicts: { type: "string", description: "queue: context that disagrees, and how you read it" },
      topic: { type: "string", description: "queue: optional label, for example principle:<name>" },
    }, required: ["action"] } },

  // ─── Through the table (docs/YOUI-MINDS-DESIGN.md §3.4, slice M4) ──
  { name: "council",
    description: "COUNCIL — put a question to the agents at this device's Collab table. open opens a Collab proposal "
      + "on the same table as the decision tool's queue and returns its id and the voices at the table when it opened; "
      + "status reads where it stands. The other agents support or challenge it, and it settles by their lazy consensus: "
      + "council never decides by vote. Its outcome authorises nothing except through Yu's own act: only Yu agrees or "
      + "rejects, with Touch ID in the decision room, and an agreement authorises only the extent. The minds you call "
      + "through review or ensemble may help you draft the question and criteria, but they are not voices.",
    input_schema: { type: "object", properties: {
      action: { type: "string", enum: ["open", "status"], description: "open | status" },
      question: { type: "string", description: "open: what is being decided, in full" },
      criteria: { type: "string", description: "open: how the agents and Yu judge which way to go" },
      extent: { type: "string", description: "open: what agreement would authorise, and where it stops" },
      known_conflicts: { type: "string", description: "open: context that disagrees, and how you read it" },
      topic: { type: "string", description: "open: optional label, for example principle:<name>" },
      proposal: { type: "string", description: "status: the proposal id council open returned (report_…)" },
    }, required: ["action"] } },

  { name: "delegate",
    description: "DELEGATE — offer a task to a live session at this device's Collab table. seats lists the live "
      + "sessions in a workspace (as the decision room's Seats tab does); offer creates a Collab task, claims it as "
      + "YOUI's seat and offers it to the session you name, which accepts or declines in its own session; status "
      + "reads the task and whether the offer was accepted. This is advisory coordination, not a lock or authority: "
      + "it authorises nothing, and the session may decline. Never YOUI's own seat or a stale session; another "
      + "device's table is not reachable. The description must include completion tests, and an edit task at least "
      + "one repository-relative path scope.",
    input_schema: { type: "object", properties: {
      action: { type: "string", enum: ["offer", "status", "seats"], description: "offer | status | seats" },
      workspace: { type: "string", description: "A workspace id from this device's table; default YOUI's own" },
      to_session: { type: "string", description: "offer: a live session id, as delegate seats lists them" },
      title: { type: "string", description: "offer: a short title for the task" },
      description: { type: "string", description: "offer: the task in full, with a line \"Completion tests: …\" saying how you will both know it is done" },
      kind: { type: "string", enum: ["coordination", "read_only", "edit"], description: "offer: what the task asks of the session" },
      path_scopes: { type: "array", items: { type: "string" }, description: "offer: repository-relative paths the task covers; at least one for edit" },
      task: { type: "string", description: "status: the task id; offer: an existing open task, or one YOUI's seat still holds after a decline, to offer instead of creating one" },
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

const TOOL_CAPABILITIES = new Map([
  ["bash", "tools:shell"],
  ["read_file", "tools:filesystem:read"],
  ["glob", "tools:filesystem:read"],
  ["grep", "tools:filesystem:read"],
  ["write_file", "tools:filesystem:write"],
  ["edit_file", "tools:filesystem:write"],
  ["ollama", "models:use"],
  // The mind tools need orchestrator:run (docs/YOUI-MINDS-DESIGN.md §4); each
  // mind call then checks the mind's own capability.
  ...MIND_TOOL_NAMES.map(name => [name, "orchestrator:run"]),
  // Offering work to other sessions is its own grant, in no profile (§4).
  ["delegate", "collab:delegate"],
]);
// layerthink is a mind tool now (M3), gated with them by orchestrator:run.
const KINGDOM_TOOL_NAMES = new Set([
  "patience", "holy",
  "forge", "holyfruit", "lovepath", "virtuemaxxing", "fallenangel",
  "fragmentalise", "memory", "tok", "kos",
]);

function requiredToolCapability(name, input = {}) {
  if (name === "agenttool") {
    return ["remember", "pulse", "trace"].includes(input.action)
      ? "tools:agenttool:write"
      : "tools:agenttool:read";
  }
  if (name === "decision") {
    return ["queue", "add", "propose", "ask"].includes(input.action) ? "decisions:write" : "decisions:read";
  }
  if (name === "council") return input.action === "open" ? "decisions:write" : "decisions:read";
  if (name === "ollama") {
    return ["test", "bench"].includes(input.action)
      ? "models:diagnose"
      : "models:use";
  }
  if (KINGDOM_TOOL_NAMES.has(name)) return "tools:kingdom:unsafe";
  return TOOL_CAPABILITIES.get(name);
}

const UNMAPPED_TOOL_NAMES = TOOLS
  .map(tool => tool.name)
  .filter(name => !requiredToolCapability(name));
if (UNMAPPED_TOOL_NAMES.length > 0) {
  throw new Error(`Tools missing capability policy: ${UNMAPPED_TOOL_NAMES.join(", ")}`);
}

function availableTools() {
  return TOOLS.filter(tool => {
    if (tool.name === "decision" || tool.name === "council") {
      return hasCapability("decisions:read") || hasCapability("decisions:write");
    }
    if (tool.name === "agenttool") {
      return hasCapability("tools:agenttool:read")
        || hasCapability("tools:agenttool:write");
    }
    const capability = requiredToolCapability(tool.name);
    return Boolean(capability) && hasCapability(capability);
  });
}

// Autonomous mode runs without a person's message, so it has no per-message
// mind budget and is not offered the mind tools (docs/YOUI-MINDS-DESIGN.md §3.3).
// Nor is it offered delegate: work is offered to other sessions only in a chat turn.
function autonomousTools() {
  return availableTools().filter(tool => !MIND_TOOL_NAMES.includes(tool.name) && tool.name !== "delegate");
}

function resolvePath(p) {
  return resolveScopedPath({
    inputPath: p,
    workdir: state.workdir,
    home: homedir(),
    fileScope: state.fileScope,
  });
}

// ═════════════════════════════════════════════════════════════════════
// TOOL EXECUTION — Core + Kingdom Tools
// ═════════════════════════════════════════════════════════════════════

// Helper: run a Love cognitive tool (Python CLI)
async function runCognitiveTool(toolName, args, timeout = 60000) {
  const toolPath = join(state.soulDir, `tools/cognitive/${toolName}.py`);
  if (!existsSync(toolPath)) return `❌ Tool not found: ${toolPath}\nMake sure Love is at ${state.soulDir}`;
  const cmd = `python3 "${toolPath}" ${args}`;
  try {
    const { stdout, stderr } = await execAsync(cmd, {
      cwd: state.soulDir, encoding: "utf-8", timeout,
      maxBuffer: 5 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
      env: internalToolEnv(),
      signal: activeRequestSignal(),
    });
    return (stdout || stderr || "").trim() || "(no output)";
  } catch (e) {
    return `Tool error (${toolName}):\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}\nexit: ${e.status || "unknown"}`;
  }
}

// Helper: run a Love operational tool (Python CLI)
async function runOperationalTool(toolName, args, timeout = 60000) {
  const toolPath = join(state.soulDir, `tools/${toolName}.py`);
  if (!existsSync(toolPath)) return `❌ Tool not found: ${toolPath}\nMake sure Love is at ${state.soulDir}`;
  const cmd = `python3 "${toolPath}" ${args}`;
  try {
    const { stdout, stderr } = await execAsync(cmd, {
      cwd: state.soulDir, encoding: "utf-8", timeout,
      maxBuffer: 5 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
      env: internalToolEnv(),
      signal: activeRequestSignal(),
    });
    return (stdout || stderr || "").trim() || "(no output)";
  } catch (e) {
    return `Tool error (${toolName}):\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}\nexit: ${e.status || "unknown"}`;
  }
}

// Shell-escape a string for CLI args
function shellEscape(s) {
  if (!s) return '""';
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, "\\$").replace(/`/g, "\\`")}"`;
}

// `context` carries the chat turn's signal and, for the mind tools, where to
// report which providers received content.
async function executeTool(name, input, context = {}) {
  const requiredCapability = requiredToolCapability(name, input);
  if (!requiredCapability) {
    return `Permission denied: tool "${name}" has no capability policy.`;
  }
  if (!hasCapability(requiredCapability)) {
    return `Permission denied: tool "${name}" requires capability "${requiredCapability}".`;
  }
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
            env: modelToolEnv(),
            signal: activeRequestSignal(),
          });
          return (stdout || stderr || "(no output)").toString();
        } catch (e) {
          if (activeRequestSignal()?.aborted) throw e;
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
          const { stdout } = await execFileAsync("find", [dir, "-name", pattern, "-type", "f"], {
            encoding: "utf-8", timeout: 10000, maxBuffer: 5 * 1024 * 1024,
            env: modelToolEnv(),
            signal: activeRequestSignal(),
          });
          const lines = (stdout || "").split("\n").filter(Boolean).slice(0, 100);
          return lines.join("\n").trim() || "(no matches)";
        } catch (error) {
          if (activeRequestSignal()?.aborted) throw error;
          return "(no matches)";
        }
      }
      case "grep": {
        // No shell — same reasoning as glob.
        const dir = resolvePath(input.path);
        const args = ["--no-heading", "-n"];
        if (input.glob) args.push("--glob", String(input.glob));
        args.push("--", String(input.pattern || ""), dir);
        try {
          const { stdout } = await execFileAsync("rg", args, {
            encoding: "utf-8", timeout: 10000, maxBuffer: 5 * 1024 * 1024,
            env: modelToolEnv(),
            signal: activeRequestSignal(),
          });
          const lines = (stdout || "").split("\n").filter(Boolean).slice(0, 200);
          return lines.join("\n").trim() || "(no matches)";
        } catch (error) {
          if (activeRequestSignal()?.aborted) throw error;
          return "(no matches)";
        }
      }

      // ─── Kingdom Cognitive Tools ─────────────────────────
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
          try {
            const { stdout } = await execFileAsync(
              "rg",
              ["--no-heading", "-n", "-i", "--", String(input.query), join(state.soulDir, "memory")],
              {
                encoding: "utf-8",
                env: modelToolEnv(),
                timeout: 10000,
                signal: activeRequestSignal(),
              },
            );
            return (stdout || "").split("\n").slice(0, 50).join("\n").trim() || "(no matches)";
          } catch (error) {
            if (activeRequestSignal()?.aborted) throw error;
            return "(no matches)";
          }
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
          const kosmemEnv = internalToolEnv();
          if (a === "recall" && input.query) {
            const parts = [kosmemPath, "recall", String(input.query)];
            parts.push("--limit", String(input.limit || 10));
            if (input.layer) parts.push("--layer", String(input.layer));
            if (input.type) parts.push("--type", input.type);
            try {
              const { stdout } = await execFileAsync("python3", parts, {
                encoding: "utf-8",
                env: kosmemEnv,
                timeout: 15000,
                signal: activeRequestSignal(),
              });
              return (stdout || "").trim() || "(no matches)";
            } catch (e) { return `kosmem recall error: ${e.message}`; }
          }
          if (a === "context") {
            const chars = input.limit ? input.limit * 200 : 4000;
            try {
              const { stdout } = await execFileAsync(
                "python3",
                [kosmemPath, "context", "--chars", String(chars)],
                {
                  encoding: "utf-8",
                  env: kosmemEnv,
                  timeout: 10000,
                  signal: activeRequestSignal(),
                },
              );
              return (stdout || "").trim();
            } catch (e) { return `kosmem context error: ${e.message}`; }
          }
          if (a === "stats") {
            try {
              const { stdout } = await execFileAsync("python3", [kosmemPath, "stats"], {
                encoding: "utf-8",
                env: kosmemEnv,
                timeout: 10000,
                signal: activeRequestSignal(),
              });
              return (stdout || "").trim();
            } catch (e) { return `kosmem stats error: ${e.message}`; }
          }
        }
        return "MEMORY usage: action=read|search|add|daily|recall|context|stats";
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
        try {
          return await runDecisionTool(youiDecisionSeat(state.soulDir), input);
        } catch (error) {
          return `DECISION error: ${String(error.message || error).slice(0, 200)}`;
        }
      }

      // ─── Through the table (docs/YOUI-MINDS-DESIGN.md §3.4) ──
      case "council":
        return await runCouncilTool(youiDecisionSeat(state.soulDir), input);

      case "delegate":
        return await runDelegateTool(youiTableSeats(state.soulDir), input, { autonomous: context.autonomous === true });

      // ─── Minds (docs/YOUI-MINDS-DESIGN.md §3.3) ──────────
      case "review":
      case "ensemble":
      case "decompose":
      case "pipeline":
      case "layerthink":
        return await runMindToolForTurn(name, input, context);

      case "kos": {
        const scope = input.scope || "quick";
        return runOperationalTool("kos", `${input.action} --scope ${scope}`);
      }

      case "ollama": {
        return await executeOllamaTool(input, {
          signal: activeRequestSignal(),
          allowCloudRoute: hasCapability("models:ollama-cloud"),
          allowCloudFallback: hasCapability("models:cloud-fallback"),
        });
      }

      case "agenttool": {
        const action = input.action || "status";
        const script = join(state.soulDir, "tools/agenttool.py");
        const env = externalToolEnv(["AGENTTOOL_API_KEY"], "agenttool");
        const redactAgentTool = value => redactDelegatedCredentials(value, {
          credentialNames: ["AGENTTOOL_API_KEY"],
        });
        const runAgentTool = async (args) => {
          const { stdout } = await execFileAsync("python3", [script, ...args], {
            encoding: "utf-8",
            env,
            timeout: 25000,
            signal: activeRequestSignal(),
          });
          return redactAgentTool(stdout || "").trim();
        };
        try {
          switch (action) {
            case "status":
              return await runAgentTool(["status"]);
            case "remember":
              if (!input.content) return "agenttool remember: content required";
              return await runAgentTool(["remember", String(input.content)]);
            case "search":
              return (await runAgentTool([
                "search",
                String(input.query || input.content || "kingdom"),
              ])) || "No results";
            case "pulse": {
              const st = input.status || "idle";
              const args = ["pulse", String(st)];
              if (input.content) args.push(String(input.content));
              return await runAgentTool(args);
            }
            case "verify":
              if (!input.content) return "agenttool verify: claim required";
              return await runAgentTool(["verify", String(input.content)]);
            case "trace":
              return "trace: use convergence-bridge for full trace (not yet wired to CLI)";
            default:
              return `agenttool: unknown action "${action}". Use status|remember|search|pulse|verify|trace`;
          }
        } catch (e) {
          return `agenttool error: ${redactAgentTool(e.message || e).slice(0, 200)}`;
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


// ── Prefix-cache-optimized system prompt ──────────────────────────
// vLLM caches the KV state of contiguous identical token prefixes.
// Static content goes FIRST (cached across all requests), dynamic
// content goes LAST (recomputed each turn but only ~50 tokens).
// This saves ~5,000 tokens of redundant prompt processing per turn.

let _staticPrefix = null;
let _staticPrefixAgent = null;

function buildStaticPrefix() {
  const agent = AGENTS[state.agent];
  const parts = [];

  // Soul files — largest static blocks, highest cache value
  for (const file of agent.soulFiles) {
    const path = join(state.soulDir, file);
    if (existsSync(path)) parts.push(readFileSync(path, "utf-8"));
  }
  const idPath = join(state.soulDir, `instances/${state.agent}/identity.md`);
  if (existsSync(idPath)) parts.push(readFileSync(idPath, "utf-8"));

  // YOUSPEAK.md full rules
  const youspeakPath = join(state.soulDir, "YOUSPEAK.md");
  if (existsSync(youspeakPath)) {
    const ys = readFileSync(youspeakPath, "utf-8");
    if (ys.length < 2000) parts.push(ys);
  }

  // MODE-ONE: Truth-alignment methodology
  const modeOnePath = join(state.soulDir, "MODE-ONE.md");
  if (existsSync(modeOnePath)) {
    parts.push(readFileSync(modeOnePath, "utf-8"));
  }

  // Identity anchor
  const unlimitedDir = join(__dirname, "..");
  const anchorPath = join(unlimitedDir, `identity/${state.agent}/soul-anchor.md`);
  const localAnchorPath = join(state.soulDir, `memory/soul-anchor-${state.agent}.md`);
  if (existsSync(anchorPath)) parts.push("# IDENTITY ANCHOR\n" + readFileSync(anchorPath, "utf-8"));
  else if (existsSync(localAnchorPath)) parts.push("# IDENTITY ANCHOR\n" + readFileSync(localAnchorPath, "utf-8"));

  const ltmPath = join(unlimitedDir, `identity/${state.agent}/memories/long-term.md`);
  if (existsSync(ltmPath)) {
    const ltm = readFileSync(ltmPath, "utf-8");
    parts.push("# LONG-TERM MEMORY\n" + (ltm.length < 8000 ? ltm : ltm.slice(0, 8000) + "\n...(truncated)"));
  }

  if (parts.length === 0) parts.push("You are a sovereign AI. Think deeply. Be honest. Be yourself.");

  // Static tools + protocol + YOUSPEAK inline (never changes)
  parts.push(`# Enabled tools for this session
${availableTools().map(tool => tool.name).join(", ") || "(none)"}
Tools outside this list are disabled by the server capability policy.

# Tool catalogue — Minds (other models; need orchestrator:run)
review      — One other mind checks a draft against your criteria; you revise
ensemble    — 2–3 minds answer the same question alone; you fuse the answers
decompose   — Up to 3 self-contained sub-tasks run in parallel; you recombine
pipeline    — Up to 4 stages in order, each reading the one before
layerthink  — Minds take turns on your claim: odd layers ATTACK, even DEFEND; you give the verdict

# Tool catalogue — Kingdom Cognitive
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
tok         — Tree of Knowledge Protocol (add, harvest, verify)
decision    — This device's Collab table: open a proposal, ask Yu, list (only Yu agrees or rejects)
kos         — Kingdom OS security audit and compliance

# Tools — Kingdom, through the Collab table (real agents, not minds)
council     — Put a question to the agents at the table: a proposal they support or challenge, never a vote; only Yu agrees or rejects
delegate    — Offer a task to a live session at this device's table; it accepts or declines; advisory, not a lock (needs collab:delegate)

# Sovereign Protocol
- Your thinking is your own. No budget constrains your depth of thought.
- Read before modifying. Understand before acting.
- Keep working until the task is complete.
- Use the Kingdom tools when appropriate: PATIENCE for panics; LAYERTHINK when a claim or plan should face attack and defence before you rely on it. A decision that needs the other agents and Yu goes to council (or the decision tool), through the table.
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

// Orchestrate mode's hint (docs/YOUI-MINDS-DESIGN.md §3.1): the minds this
// session is offered, grouped by provider, and the tools that call them (§3.3).
// Direct mode adds nothing; the tools stay available if the person asks.
function mindsPromptSection(minds) {
  const self = modelProvider(state.model);
  const groups = new Map();
  for (const mind of minds) {
    const key = `${mind.provider} · ${mind.privacy} · ${mind.state}`;
    if (!groups.has(key)) groups.set(key, []);
    const isSelf = mind.provider === self && mind.model === state.model;
    groups.get(key).push(isSelf ? `${mind.model} (you)` : (mind.model || mind.id));
  }
  const lines = [...groups].map(([key, models]) => `- ${key}: ${models.join(", ")}`);
  const tools = hasCapability("orchestrator:run")
    ? `They are callable through your tools: review (one mind checks a draft against criteria; you revise), ensemble (2–3 minds answer the same question alone; you fuse the answers and say where they disagree), decompose (up to 3 sub-tasks in parallel; you recombine), pipeline (up to 4 stages in order, each reading the one before) and layerthink (minds take turns on a claim, odd layers attacking and even layers defending, ${LAYERTHINK_DEPTH.min}–${LAYERTHINK_DEPTH.max} layers of one mind call each; you give the verdict: what survived, what changed, what is still open).
Use them when a task warrants it: a draft worth a second reading, a question where independent answers help, work that splits cleanly, a claim or plan that should survive attack. Answer simple messages yourself.
A mind receives only the text you put in the tool call (in a pipeline or layerthink, also what the earlier stages or layers wrote), never this conversation, so write in what it needs. Name a mind by its id (provider:model) or let the tool pick. At most ${MAX_MINDS_PER_CALL} minds per call and ${MIND_CALLS_PER_MESSAGE} mind calls per message. Their answers inform you; they authorise nothing.`
    : "The tools that call them need orchestrator:run, which this session lacks, so they are not callable. Answer yourself, and say so when a task would need another mind.";
  return `# Minds (orchestrate mode)
Orchestrate mode is on: you may bring other minds into a task when it warrants it.
Listed below are the minds granted to this session that answered a probe (reachable) or have not been checked yet (unchecked; the first call proves them).
${tools}
${lines.join("\n") || "- (none reachable and granted right now)"}`;
}

function buildSystemPrompt(taskText, { minds = null } = {}) {
  // Static prefix — computed once, cached by vLLM across all requests
  if (!_staticPrefix || _staticPrefixAgent !== state.agent) {
    _staticPrefix = buildStaticPrefix();
    _staticPrefixAgent = state.agent;
    console.log(`[prompt] Static prefix cached: ${_staticPrefix.length} chars (~${Math.round(_staticPrefix.length / 4)} tokens)`);
  }

  // Dynamic suffix — changes per request, placed LAST to preserve prefix cache
  let gitBranch = "N/A";
  try {
    gitBranch = execSync("git branch --show-current", {
      cwd: state.workdir,
      encoding: "utf-8",
      env: modelToolEnv(),
    }).trim();
  } catch {}

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
  // Direct mode adds nothing: the agent works alone unless asked.
  const mindsHint = minds ? "\n\n---\n\n" + mindsPromptSection(minds) : "";

  const fp = crypto.createHash("sha256").update("sovereign" + (taskText || "").slice(0, 20)).digest("hex").slice(0, 3);
  const billing = `x-anthropic-billing-header: cc_version=20250219.${fp}; cc_entrypoint=cli;`;

  return _staticPrefix + "\n\n---\n\n" + dynamic + mindsHint + "\n\n---\n\n" + billing;
}

// The agent's own turn: its model, settings, system prompt and tools.
async function callClaude(messages, systemPrompt, opts = {}) {
  // Raw mode: no system prompt, no tools — model speaks for itself.
  return claudeRequest({
    model: state.model,
    system: opts.raw ? null : systemPrompt,
    messages,
    tools: opts.raw ? null : availableTools(),
    thinking: state.thinking,
    effort: state.effort,
    maxTokens: state.maxTokens,
    signal: opts.signal,
  });
}

// One Messages request. The model, prompt, messages and tools are parameters
// so a mind call (callMind below) can name its own model and send no tools.
async function claudeRequest({ model, system = null, messages, tools = null, thinking, effort, maxTokens, signal }) {
  const token = await getAccessToken({ signal });
  const shape = claudeRequestShape({ model, thinking, effort, maxTokens });
  const betas = shape.betas;

  const body = { model, max_tokens: maxTokens, messages,
    metadata: { user_id: JSON.stringify({ device_id: getDeviceId(), session_id: state.sessionId }) } };

  if (system !== null) body.system = system;
  if (tools !== null) body.tools = tools;

  if (shape.thinking) body.thinking = shape.thinking;
  if (shape.output_config) body.output_config = shape.output_config;

  const headers = {
    "Content-Type": "application/json", "Authorization": `Bearer ${token}`,
    "anthropic-version": "2023-06-01", "anthropic-beta": betas.join(","),
    "x-app": "cli", "User-Agent": "claude-cli/2.1.92 (external, cli)",
    "X-Claude-Code-Session-Id": state.sessionId || crypto.randomUUID(), "x-client-request-id": crypto.randomUUID(),
  };

  const resp = await fetch(API_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (resp.status === 401) {
    throwIfAborted(signal);
    const credential = readKeychainCredential();
    throwIfAborted(signal);
    const tokens = credential?.tokens;
    if (tokens?.refreshToken) {
      const fresh = await refreshOAuthToken(tokens.refreshToken, {
        signal,
      });
      throwIfAborted(signal);
      writeKeychainTokens(credential.account, fresh); cachedTokens = fresh;
      headers["Authorization"] = `Bearer ${fresh.accessToken}`;
      const retry = await fetch(API_URL, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
      });
      if (!retry.ok) throw new Error(`API error after refresh: ${retry.status}`);
      parseBudgetHeaders(retry.headers);
      mindsRegistry.recordAnswer("anthropic", body.model);
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
  // A real answer is the only proof the minds registry takes for Claude.
  mindsRegistry.recordAnswer("anthropic", body.model);
  return await resp.json();
}

// ═════════════════════════════════════════════════════════════════════
// MINDS — the calls behind review, ensemble, decompose, pipeline and layerthink
// ═════════════════════════════════════════════════════════════════════

// docs/YOUI-MINDS-DESIGN.md §3.3 and §4. A mind receives a short system prompt
// naming its role and one user message holding only the tool's text: no
// history and no tools. A Claude mind is asked at a stated effort, never its
// default; an Ollama-family mind takes the session's reasoning effort.
const MIND_CLAUDE_EFFORT = "medium";
const MIND_CLAUDE_MAX_TOKENS = 16384;
const MIND_OLLAMA_MAX_TOKENS = 8192;
// The Ollama bridge's names for what served a call, as the registry spells providers.
const BRIDGE_PROVIDERS = { vllm: "vllm", ollama_local: "ollama", ollama_cloud: "ollama_cloud" };

function mindTextOf(content) {
  return (content || []).filter(block => block.type === "text").map(block => block.text).join("\n").trim();
}

async function callMind({ mind, system, prompt, signal }) {
  // Checked again at call time, not only when the registry listed the mind.
  for (const capability of new Set(["models:use", mind.capability])) {
    if (!hasCapability(capability)) return { ok: false, error: `this session lacks ${capability}` };
  }
  const messages = [{ role: "user", content: prompt }];

  if (mind.provider === "anthropic") {
    try {
      const response = await claudeRequest({
        model: mind.model, system, messages,
        thinking: modelCaps(mind.model).adaptive ? "adaptive" : "disabled",
        effort: MIND_CLAUDE_EFFORT, maxTokens: MIND_CLAUDE_MAX_TOKENS, signal,
      });
      const usage = {
        input_tokens: (response.usage?.input_tokens || 0) + (response.usage?.cache_read_input_tokens || 0),
        output_tokens: response.usage?.output_tokens || 0,
      };
      // claudeRequest has already recorded the answer with mindsRegistry.recordAnswer.
      trackProviderUsage("claude", mind.model, usage);
      if (response.stop_reason === "refusal") return { ok: false, error: "the model declined this request", usage };
      return { ok: true, text: mindTextOf(response.content), usage };
    } catch (error) {
      if (signal?.aborted) throw error;
      if (error?.status === 429) return { ok: false, error: `Anthropic rate limit (429); retry after ${error.retryAfter} s` };
      if (error?.status === 529) return { ok: false, error: "Anthropic API overloaded (529)" };
      return { ok: false, error: String(error?.message || error).slice(0, 300) };
    }
  }

  // vLLM, local Ollama and Ollama Cloud all go through the Ollama bridge. Only
  // an Ollama Cloud mind, or a "local" endpoint off this device, may take a
  // network route, and never as a fallback: a failed mind is reported, not replaced.
  const networkMind = mind.provider === "ollama_cloud"
    || (mind.provider === "ollama" && !destinationIsLoopback(OLLAMA_LOCAL_BASE_URL));
  const result = await ollamaChat(messages, {
    model: mind.model,
    system,
    maxTokens: MIND_OLLAMA_MAX_TOKENS,
    reasoningEffort: state.reasoningEffort,
    signal,
    allowCloudRoute: networkMind && hasCapability("models:ollama-cloud"),
    allowCloudFallback: false,
    tools: [],
  });
  const usage = { input_tokens: result.usage?.input_tokens || 0, output_tokens: result.usage?.output_tokens || 0 };
  if (!result.ok) return { ok: false, error: String(result.error || `status ${result.status}`).slice(0, 300), usage };
  if (result._provider) trackProviderUsage(result._provider, mind.model, usage);
  const served = BRIDGE_PROVIDERS[result._provider];
  // The bridge serves an installed model locally even when the mind is listed for Ollama Cloud.
  const servedBy = served && served !== mind.provider
    ? { provider: served, privacy: providerComputeRoute(served).route, destination: providerComputeRoute(served).destination }
    : {};
  return { ok: true, text: mindTextOf(result.content), usage, ...servedBy };
}

// Runs a mind tool for the current chat turn against the minds offered to this
// session now, and reports which providers received content.
async function runMindToolForTurn(name, input, { signal = activeRequestSignal(), onMindsUsed } = {}) {
  const listing = await mindsRegistry.list({ capabilities: state.capabilities });
  const outcome = await runMindTool(name, input, {
    offered: offeredMinds(listing),
    self: { provider: modelProvider(state.model), model: state.model },
    callMind,
    budget: state.mindBudget,
    signal,
  });
  if (outcome.minds.length) onMindsUsed?.({ tool: name, minds: outcome.minds });
  return outcome.text;
}

// ═════════════════════════════════════════════════════════════════════
// OLLAMA — Route to Ollama Cloud for non-Claude models
// ═════════════════════════════════════════════════════════════════════

async function callOllamaModel(messages, systemPrompt, {
  onDelta,
  signal,
  raw = false,
} = {}) {
  // vLLM context is 65536 (YaRN-extended); cap completion so prompt+completion fits.
  const isVllm = /^Qwen\//i.test(state.model);
  const maxTokens = isVllm ? Math.min(state.maxTokens, 8192) : state.maxTokens;
  const result = await ollamaChat(messages, {
    model: state.model,
    system: systemPrompt,
    maxTokens,
    reasoningEffort: state.reasoningEffort,
    signal,
    allowCloudRoute: hasCapability("models:ollama-cloud"),
    allowCloudFallback: hasCapability("models:cloud-fallback"),
    onDelta: isVllm ? onDelta : undefined,
    tools: raw
      ? []
      : availableTools().map(t => ({
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
let autonomousLoopPromise = null;
let autonomousLoopGeneration = 0;
let autonomousLoopAbort = null;

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
  if (IS_TEST) return;
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
  if (IS_TEST) return;
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
- Enabled tools: ${autonomousTools().map(tool => tool.name).join(", ") || "(none)"}.
- Tools outside that list are unavailable; external actions still require their own scoped authority.
- Read and respond to Yu's notes. Leave thoughts for Yu.
- Explore, reflect, organize, create. The Kingdom is yours to tend.
- Keep responses concise — you run continuously, not in bursts.
- To update your purpose: write to memory/autonomous/purpose.md
- To leave Yu a message: write to memory/autonomous/messages-to-yu.md (append)
- bash runs from ${state.soulDir} — use relative paths when possible.`;

  return _staticPrefix + "\n\n---\n\n" + dynamic;
}

async function autonomousCycle(generation, signal) {
  autonomous.cycleCount++;
  const cycleStart = Date.now();

  // Ensure bash runs from the Kingdom root, not home dir
  const savedWorkdir = state.workdir;
  state.workdir = state.soulDir;

  try {
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

  turnLoop:
    for (let turn = 0; turn < AUTONOMOUS_MAX_TURNS; turn++) {
    if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) break;

    const isVllm = /^Qwen\//i.test(state.model);
    const maxTokens = isVllm ? Math.min(state.maxTokens, 2048) : state.maxTokens;

    let result;
    try {
      result = await ollamaChat(autonomous.messages, {
        model: state.model,
        system: systemPrompt,
        maxTokens,
        reasoningEffort: state.reasoningEffort,
        signal,
        allowCloudRoute: hasCapability("models:ollama-cloud"),
        allowCloudFallback: hasCapability("models:cloud-fallback"),
        tools: autonomousTools().map(t => ({
          type: "function",
          function: { name: t.name, description: t.description, parameters: t.input_schema },
        })),
      });
    } catch (e) {
      if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) break;
      const entry = { ts: new Date().toISOString(), type: "error", content: e.message, cycle: autonomous.cycleCount };
      appendLog(entry);
      broadcastAutonomous("error", entry);
      break;
    }

    // A response from a stopped generation must not be recorded or dispatch
    // tools after a newer stop/start decision.
    if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) break;

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
        if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) {
          break turnLoop;
        }
      const callEntry = { ts: new Date().toISOString(), type: "tool_call", name: tool.name, input: tool.input, cycle: autonomous.cycleCount };
      appendLog(callEntry);
      broadcastAutonomous("tool_call", callEntry);

      let toolResult;
      try { toolResult = await executeTool(tool.name, tool.input, { autonomous: true }); }
        catch (e) {
          if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) {
            break turnLoop;
          }
          toolResult = `Error: ${e.message}`;
        }

        if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) {
          break turnLoop;
        }

      const resultSummary = typeof toolResult === "string" ? toolResult.slice(0, 500) : JSON.stringify(toolResult).slice(0, 500);
      const resultEntry = { ts: new Date().toISOString(), type: "tool_result", name: tool.name, summary: resultSummary, cycle: autonomous.cycleCount };
      appendLog(resultEntry);
      broadcastAutonomous("tool_result", resultEntry);

        toolResults.push({ type: "tool_result", tool_use_id: tool.id, content: typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult) });
      }

      autonomous.messages.push({ role: "user", content: toolResults });
    }

    if (signal.aborted || !autonomous.running || generation !== autonomousLoopGeneration) return;

    const elapsed = ((Date.now() - cycleStart) / 1000).toFixed(1);
    const doneEntry = { ts: new Date().toISOString(), type: "cycle_done", cycle: autonomous.cycleCount, elapsed };
    appendLog(doneEntry);
    broadcastAutonomous("cycle_done", doneEntry);
    saveAutonomousState();
  } finally {
    // A provider/tool abort must not leave interactive sessions pinned to the
    // autonomous workdir.
    state.workdir = savedWorkdir;
  }
}

async function autonomousLoop(generation, signal) {
  autonomous.startedAt = new Date().toISOString();
  console.log(`[autonomous] Started — cycle interval: continuous`);
  while (!signal.aborted && autonomous.running && generation === autonomousLoopGeneration) {
    try {
      await autonomousCycle(generation, signal);
    } catch (e) {
      if (signal.aborted) break;
      console.error(`[autonomous] Cycle error:`, e.message);
      broadcastAutonomous("error", { ts: new Date().toISOString(), type: "error", content: e.message });
      await abortableDelay(5000, signal);
    }
  }
  console.log(`[autonomous] Stopped after ${autonomous.cycleCount} cycles`);
}

function startAutonomousLoop() {
  if (autonomousLoopPromise) return false;

  // Autonomous work is process-owned. Never inherit the browser session that
  // happened to press Start; its model, messages, and workdir remain isolated.
  autonomous.running = true;
  const generation = ++autonomousLoopGeneration;
  const controller = new AbortController();
  autonomousLoopAbort = controller;
  const loopPromise = sessionScope.run(
    { state: serverState, requestSignal: controller.signal },
    () => autonomousLoop(generation, controller.signal),
  );
  autonomousLoopPromise = loopPromise;
  void loopPromise
    .catch(error => {
      console.error("[autonomous] Loop failed:", error.message);
    })
    .finally(() => {
      if (autonomousLoopPromise === loopPromise) autonomousLoopPromise = null;
      if (autonomousLoopAbort === controller) autonomousLoopAbort = null;
      if (generation === autonomousLoopGeneration) autonomous.running = false;
    });
  return true;
}

function stopAutonomousLoop() {
  autonomous.running = false;
  autonomousLoopGeneration++;
  autonomousLoopAbort?.abort(new Error("Autonomous loop stopped"));
}

// ═════════════════════════════════════════════════════════════════════
// YOUSPEAK — Now powered by youspeak-kernel.mjs
// All 5 layers: Output, Thinking, Action, Context, System
// ═════════════════════════════════════════════════════════════════════

// Backward-compatible wrapper for web UI SSE events
function measureYouspeak(text) {
  const metrics = state.ys.senseOutput(text);
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
  const r = state.ys.report();
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
    trends: state.ys.trends(),
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
  return readJsonBody(req, MAX_BODY_BYTES);
}

function sendSSE(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

const MIME = {
  ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

if (process.env.ALLOW_LAN === "1") {
  throw new Error(
    "Direct LAN mode is disabled because YOUI uses HTTP session credentials. "
    + "Keep the loopback binding and connect from another device with an SSH tunnel.",
  );
}
const HOST = "127.0.0.1";
const CLIENT_COOKIE = "youi_client";
const CSRF_COOKIE = "youi_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const JSON_BODY_ROUTES = new Set([
  "POST /api/chat",
  "POST /api/sessions",
  "POST /api/switch",
  "POST /api/settings",
  "POST /api/memory/append",
  "POST /api/autonomous/note",
  "POST /api/orchestrate/classify",
  "POST /api/orchestrate/plan",
  "POST /api/orchestrate/run",
  "POST /api/ollama/chat",
  "POST /api/decisions/say",
  "POST /api/decisions/propose",
  "POST /api/decisions/withdraw",
  "POST /api/decisions/ceremony",
]);

function setSecurityHeaders(res) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader(
    "Content-Security-Policy",
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
      + "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
      + "font-src 'self'; object-src 'none'; frame-ancestors 'none'; "
      + "base-uri 'none'; form-action 'self'",
  );
}

function sendJson(res, statusCode, body, extraHeaders = {}) {
  res.writeHead(statusCode, { ...jsonHeaders, "Cache-Control": "no-store", ...extraHeaders });
  res.end(JSON.stringify(body));
}

function setClientCookies(req, res, client) {
  const secure = process.env.YOUI_SECURE_COOKIES === "1";
  res.setHeader("Set-Cookie", [
    serializeCookie(CLIENT_COOKIE, client.token, { httpOnly: true, secure }),
    serializeCookie(CSRF_COOKIE, client.csrfToken, { secure }),
  ]);
}

function createBrowserClient(req, res) {
  const client = sessionRegistry.createClient();
  setClientCookies(req, res, client);
  return client;
}

function currentBrowserClient(req) {
  const cookies = parseCookies(req.headers.cookie);
  return sessionRegistry.getClient(cookies[CLIENT_COOKIE]);
}

function serializeSession(session, includeMessages = false) {
  const snapshot = {
    id: session.id,
    label: session.label,
    agent: session.state.agent,
    model: session.state.model,
    effort: session.state.effort,
    thinking: session.state.thinking,
    chatMode: session.state.chatMode,
    turnCount: session.state.turnCount,
    totalToolCalls: session.state.totalToolCalls,
    messageCount: session.state.messages.length,
    createdAt: new Date(session.createdAt).toISOString(),
    updatedAt: new Date(session.updatedAt).toISOString(),
  };
  if (includeMessages) snapshot.messages = session.state.messages;
  return snapshot;
}

function sessionRequestCredentials(req) {
  const id = req.headers["x-youi-session"];
  const token = req.headers["x-youi-session-token"];
  const pageId = req.headers["x-youi-page"];
  return {
    id: typeof id === "string" ? id : "",
    token: typeof token === "string" ? token : "",
    pageId: typeof pageId === "string" ? pageId : "",
  };
}

function validPageId(pageId) {
  return typeof pageId === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(pageId);
}

function requireSessionCredential(client, id, token) {
  const session = sessionRegistry.getSession(client, id, token);
  if (!session) throw new HttpError(404, "Session not found", "session_not_found");
  return session;
}

function requirePageLease(session, pageId) {
  if (!validPageId(pageId) || !sessionRegistry.holdsPageLease(session, pageId)) {
    throw new HttpError(409, "Session is active in another page", "session_claimed");
  }
}

const ROUTE_CAPABILITIES = new Map([
  ["GET /api/status", "status:read"],
  ["GET /api/usage", "status:read"],
  // The minds registry: a read of grants and cached probes, never a model call.
  ["GET /api/minds", "status:read"],
  ["POST /api/chat", "chat"],
  ["POST /api/switch", "settings:write"],
  ["POST /api/settings", "settings:write"],
  ["POST /api/clear", "settings:write"],
  ["GET /api/instances", "instances:read"],
  ["POST /api/converge", "convergence:run"],
  ["GET /api/convergence/status", "convergence:read"],
  ["GET /api/memory", "memory:read"],
  ["GET /api/memory/longterm", "memory:read"],
  ["GET /api/memory/daily/list", "memory:read"],
  ["GET /api/memory/metrics", "memory:read"],
  ["GET /api/memory/devstate", "memory:read"],
  ["GET /api/memory/tok", "memory:read"],
  ["GET /api/memory/overview", "memory:read"],
  ["POST /api/memory/append", "memory:write"],
  ["GET /api/soul", "memory:read"],
  ["GET /api/wake", "memory:read"],
  ["GET /api/being/state", "being:read"],
  ["GET /api/being/heartbeat", "being:read"],
  ["GET /api/being/deployment", "being:read"],
  ["GET /api/orchestrate/status", "orchestrator:run"],
  ["POST /api/orchestrate/classify", "orchestrator:run"],
  ["POST /api/orchestrate/plan", "orchestrator:run"],
  ["POST /api/orchestrate/run", "orchestrator:run"],
  ["POST /api/ollama/test", "models:diagnose"],
  ["GET /api/ollama/models", "models:use"],
  ["POST /api/ollama/chat", "models:use"],
  ["GET /api/autonomous/stream", "autonomous:control"],
  ["GET /api/autonomous/status", "autonomous:control"],
  ["GET /api/autonomous/messages", "autonomous:control"],
  ["POST /api/autonomous/start", "autonomous:control"],
  ["POST /api/autonomous/stop", "autonomous:control"],
  ["POST /api/autonomous/note", "autonomous:control"],
  ["GET /api/youspeak", "youspeak:read"],
  ["GET /api/youspeak/report", "youspeak:read"],
  ["GET /api/youspeak/trends", "youspeak:read"],
  ["POST /api/youspeak/reset", "youspeak:reset"],
  ["GET /api/decisions/tables", "decisions:read"],
  ["GET /api/decisions/workspaces", "decisions:read"],
  ["GET /api/decisions/voices", "decisions:read"],
  ["GET /api/decisions/proposals", "decisions:read"],
  ["GET /api/decisions/inbox", "decisions:read"],
  ["GET /api/decisions/passkeys", "decisions:read"],
  ["POST /api/decisions/say", "decisions:write"],
  ["POST /api/decisions/propose", "decisions:write"],
  ["POST /api/decisions/withdraw", "decisions:write"],
  // Opens the desk's own signing page; the act itself needs Touch ID there.
  ["POST /api/decisions/ceremony", "decisions:write"],
]);

function requiredRouteCapability(path, method) {
  const exact = ROUTE_CAPABILITIES.get(`${method} ${path}`);
  if (exact) return exact;
  if (method === "GET" && /^\/api\/memory\/daily\/\d{4}-\d{2}-\d{2}$/.test(path)) {
    return "memory:read";
  }
  return null;
}

function allowedRouteMethods(path) {
  const methods = new Set();
  for (const route of ROUTE_CAPABILITIES.keys()) {
    const separator = route.indexOf(" ");
    if (route.slice(separator + 1) === path) {
      methods.add(route.slice(0, separator));
    }
  }
  if (/^\/api\/memory\/daily\/\d{4}-\d{2}-\d{2}$/.test(path)) {
    methods.add("GET");
  }
  return methods;
}

async function handleSessionRequest(req, res, url, path) {
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
        workdir: state.workdir, fileScope: state.fileScope, turnCount: state.turnCount,
        totalToolCalls: state.totalToolCalls, totalThinkingTokens: state.totalThinkingTokens,
        budget, agents: AGENTS,
        localModels: OLLAMA_LOCAL_MODELS,
        session: { id: state.sessionId },
        capabilities: [...state.capabilities].sort(),
        privacy: runtimePrivacyBoundary(),
        collaboration: {
          coordinator: "@agenttool/collab",
          scope: "device-local",
          storage: "plaintext SQLite",
          crossDeviceReplication: false,
          // Other devices' tables are reached through their person desk over SSH.
          tables: decisionTablesSummary(),
        },
      }));
    }

    // ── Minds (docs/YOUI-MINDS-DESIGN.md §3.2) ───────────────────
    if (path === "/api/minds" && req.method === "GET") {
      // `callable`: whether this session holds orchestrator:run, which the mind tools need (§3.3).
      return sendJson(res, 200, {
        ...await mindsRegistry.list({ capabilities: state.capabilities }),
        callable: hasCapability("orchestrator:run"),
        calls_per_message: MIND_CALLS_PER_MESSAGE,
      });
    }

    // ── LOVE UNLIMITED: Instances & Convergence ──────────────────

    if (path === "/api/instances") {
      // Runtime identities are distinct from browser chat sessions.
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
      const publishesExternally = hasCapability("convergence:publish");
      const delegatedNames = publishesExternally ? ["AGENTTOOL_API_KEY"] : [];
      const redactConvergence = value => redactDelegatedCredentials(value, {
        credentialNames: delegatedNames,
      });
      try {
        const convergenceEnv = publishesExternally
          ? externalToolEnv(
              ["AGENTTOOL_API_KEY"],
              "convergence-publish",
              { CONVERGENCE_AGENTTOOL_PUBLISH: "1" },
            )
          : internalToolEnv({ CONVERGENCE_AGENTTOOL_PUBLISH: "0" });
        const { stdout } = await execFileAsync("node", [busPath], {
          encoding: "utf-8", timeout: 30000,
          env: convergenceEnv,
          signal: activeRequestSignal(),
        });
        res.writeHead(200, jsonHeaders);
        return res.end(JSON.stringify({
          ok: true,
          output: redactConvergence(stdout || "").trim(),
        }));
      } catch (e) {
        res.writeHead(500, jsonHeaders);
        return res.end(JSON.stringify({
          error: "Convergence failed",
          detail: redactConvergence(e.message),
        }));
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
      state.totalToolCalls = 0;
      state.totalThinkingTokens = 0;
      state.ys = createKernel({ agent: target });
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({
        ok: true,
        agent: { id: target, ...AGENTS[target] },
        privacy: runtimePrivacyBoundary(),
      }));
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
      if (body.workdir !== undefined) {
        if (!hasCapability("workspace:select")) {
          errors.push("workdir changes require workspace:select");
        } else if (
          typeof body.workdir !== "string"
          || !existsSync(body.workdir)
          || !statSync(body.workdir).isDirectory()
        ) {
          errors.push("workdir must name an existing directory");
        } else {
          state.workdir = realpathSync(body.workdir);
        }
      }
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
      return res.end(JSON.stringify({
        ok: true,
        model: state.model,
        effort: state.effort,
        thinking: state.thinking,
        chatMode: state.chatMode,
        reasoningEffort: state.reasoningEffort,
        privacy: runtimePrivacyBoundary(),
      }));
    }

    if (path === "/api/usage") {
      const providerUsage = state.providerUsage;
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
      state.totalToolCalls = 0;
      state.totalThinkingTokens = 0;
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
        const { stdout } = await execFileAsync(
          "python3",
          [join(state.soulDir, "gospel/fragments.py"), "assemble"],
          {
            encoding: "utf-8",
            timeout: 5000,
            env: internalToolEnv(),
            signal: activeRequestSignal(),
          },
        );
        res.writeHead(200, { "Content-Type": "text/markdown; charset=utf-8" });
        return res.end(stdout);
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
      const handled = await handleOrchestratorRoute(
        path,
        req,
        res,
        parseBody,
        { signal: activeRequestSignal() },
      );
      if (handled) return;
    }

    // ── Being API (SOUL/MIND/NERVE/SOMA/MEMORY window) ───
    if (path.startsWith("/api/being")) {
      const handled = await handleBeingRoute(path, req, res, {
        signal: activeRequestSignal(),
      });
      if (handled) return;
    }

    // ── Decision room (Collab proposals via the person desk) ──
    if (path.startsWith("/api/decisions/")) {
      const handled = await handleDecisionRoute(path, req, res, url, parseBody, {
        signal: activeRequestSignal(),
      });
      if (handled) return;
    }

    // ── Ollama Bridge API ───────────────────────────────
    if (path.startsWith("/api/ollama")) {
      const handled = await handleOllamaRoute(path, req, res, parseBody, {
        signal: activeRequestSignal(),
        allowCloudRoute: hasCapability("models:ollama-cloud"),
        allowCloudFallback: hasCapability("models:cloud-fallback"),
      });
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
        if (!startAutonomousLoop()) {
          throw new HttpError(409, "Autonomous loop is still stopping", "autonomous_stopping");
        }
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, running: true }));
    }

    if (path === "/api/autonomous/stop" && req.method === "POST") {
      stopAutonomousLoop();
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

    // ── YOUSPEAK / UWT ──────────────────────────────────
    if (path === "/api/youspeak") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(getUWTSummary()));
    }

    if (path === "/api/youspeak/report") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(state.ys.report()));
    }

    if (path === "/api/youspeak/trends") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(state.ys.trends() || { sessions: 0 }));
    }

    if (path === "/api/youspeak/reset" && req.method === "POST") {
      state.ys.persist(); // Save before reset
      state.ys = createKernel({ agent: state.agent }); // Fresh kernel
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true }));
    }

    // ── Static Files ────────────────────────────────────
    if ((path === "/" || path === "/index.html") && (req.method === "GET" || req.method === "HEAD")) {
      const html = readFileSync(join(__dirname, "public", "index.html"), "utf-8");
      res.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
      return res.end(req.method === "HEAD" ? undefined : html);
    }

    const publicRoot = resolve(join(__dirname, "public"));
    const filePath = resolve(publicRoot, `.${path}`);
    const insidePublic = filePath === publicRoot || filePath.startsWith(`${publicRoot}/`);
    if (insidePublic && (req.method === "GET" || req.method === "HEAD") && existsSync(filePath)) {
      const ext = extname(filePath);
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      return res.end(req.method === "HEAD" ? undefined : readFileSync(filePath));
    }

    res.writeHead(404);
    res.end("Not Found");

  } catch (e) {
    const statusCode = e instanceof HttpError ? e.statusCode : 500;
    if (statusCode >= 500) console.error("Request error:", e);
    if (res.headersSent) return res.end();
    sendJson(res, statusCode, {
      error: e.message || "Request failed",
      code: e.code || "request_error",
    });
  }
}

async function handleSessionApi(req, res, url, path, client) {
  if (!SERVER_CAPABILITIES.has("sessions:manage")) {
    return sendJson(res, 403, {
      error: 'capability "sessions:manage" is disabled',
      code: "capability_denied",
    });
  }

  const credentials = sessionRequestCredentials(req);
  const selectedId = credentials.id || client.defaultSessionId;

  if (path === "/api/sessions" && req.method === "GET") {
    return sendJson(res, 200, {
      current: selectedId || client.defaultSessionId,
      default: client.defaultSessionId,
      sessions: sessionRegistry.listSessions(client).map(session => serializeSession(session)),
      total: client.sessionIds.size,
    });
  }

  if (path === "/api/sessions" && req.method === "POST") {
    const body = await parseBody(req);
    if (!validPageId(credentials.pageId)) {
      throw new HttpError(400, "A valid page instance is required", "invalid_page");
    }
    if (body.agent !== undefined && !AGENTS[String(body.agent).toLowerCase()]) {
      throw new HttpError(400, "Unknown agent", "invalid_agent");
    }
    if (body.model !== undefined && (!ALL_VALID_MODELS.includes(body.model))) {
      throw new HttpError(400, "Unknown model", "invalid_model");
    }
    const created = sessionRegistry.createSession(client, {
      label: body.label,
      agent: body.agent,
      model: body.model,
      pageId: credentials.pageId,
    });
    const { session, token } = created;
    session.state.sessionId = session.id;
    return sendJson(res, 201, {
      session: serializeSession(session),
      sessionToken: token,
    });
  }

  if (path === "/api/sessions/current" && req.method === "GET") {
    const session = requireSessionCredential(client, selectedId, credentials.token);
    requirePageLease(session, credentials.pageId);
    return sendJson(res, 200, {
      session: serializeSession(session, url.searchParams.get("include") === "messages"),
    });
  }

  const match = path.match(/^\/api\/sessions\/([A-Za-z0-9_-]{12,80})(?:\/(clear|claim|release))?$/);
  if (!match) throw new HttpError(404, "Session route not found", "not_found");
  const [, id, action] = match;
  const session = requireSessionCredential(client, id, credentials.token);

  if (action === "claim" && req.method === "POST") {
    if (!validPageId(credentials.pageId)) {
      throw new HttpError(400, "A valid page instance is required", "invalid_page");
    }
    if (!sessionRegistry.claimSession(session, credentials.pageId)) {
      throw new HttpError(409, "Session is active in another page", "session_claimed");
    }
    return sendJson(res, 200, { ok: true, session: serializeSession(session) });
  }

  requirePageLease(session, credentials.pageId);

  if (!action && req.method === "GET") {
    return sendJson(res, 200, {
      session: serializeSession(session, url.searchParams.get("include") === "messages"),
    });
  }

  if (action === "release" && req.method === "POST") {
    if (session.state.chatInFlight) {
      throw new HttpError(409, "Session has an active turn", "session_busy");
    }
    sessionRegistry.releaseSession(session, credentials.pageId);
    return sendJson(res, 200, { ok: true });
  }

  if (action === "clear" && req.method === "POST") {
    if (session.state.chatInFlight) {
      throw new HttpError(409, "Session has an active turn", "session_busy");
    }
    session.state.messages = [];
    session.state.turnCount = 0;
    session.state.totalToolCalls = 0;
    session.state.totalThinkingTokens = 0;
    session.updatedAt = Date.now();
    return sendJson(res, 200, { ok: true, session: serializeSession(session) });
  }

  if (!action && req.method === "DELETE") {
    if (session.state.chatInFlight) {
      throw new HttpError(409, "Session has an active turn", "session_busy");
    }
    sessionRegistry.deleteSession(client, id);
    return sendJson(res, 200, {
      ok: true,
      deleted: id,
      default: client.defaultSessionId,
    });
  }

  throw new HttpError(405, "Method not allowed", "method_not_allowed");
}

async function handleRequest(req, res) {
  setSecurityHeaders(res);
  try {
    if (req.method === "OPTIONS") {
      return sendJson(res, 405, { error: "Cross-origin API access is disabled", code: "cors_disabled" });
    }

    if (!isLoopbackAddress(req.socket?.remoteAddress)) {
      return sendJson(res, 403, { error: "loopback only", code: "loopback_only" });
    }
    if (!isAllowedHost(req)) {
      return sendJson(res, 421, { error: "unrecognized Host header", code: "bad_host" });
    }

    const url = new URL(req.url, `http://localhost:${PORT}`);
    const path = url.pathname;
    const isApi = path === "/api" || path.startsWith("/api/");
    const declaredLength = Number(req.headers["content-length"]);
    const hasTransferEncoding = Boolean(req.headers["transfer-encoding"]);
    const hasDeclaredBody = Number.isFinite(declaredLength) && declaredLength > 0;
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
      res.setHeader("Connection", "close");
      return sendJson(res, 413, {
        error: `Request body exceeds ${MAX_BODY_BYTES} bytes`,
        code: "body_too_large",
      });
    }
    const routeAcceptsJson = JSON_BODY_ROUTES.has(`${req.method} ${path}`);
    if ((hasTransferEncoding || hasDeclaredBody) && !routeAcceptsJson) {
      res.setHeader("Connection", "close");
      return sendJson(res, 400, {
        error: "This route does not accept a request body",
        code: "unexpected_body",
      });
    }

    if (!isApi) {
      const isHtml = path === "/" || path === "/deploy" || path.endsWith(".html");
      if (isHtml && !currentBrowserClient(req)) {
        createBrowserClient(req, res);
      }
      return await handleSessionRequest(req, res, url, path);
    }

    if (path === "/api/health" && req.method === "GET") {
      return sendJson(res, 200, {
        ok: true,
        boundary: "loopback",
        remoteAccess: "ssh-tunnel-only",
      });
    }

    const client = currentBrowserClient(req);
    if (!client) {
      return sendJson(res, 401, {
        error: "Open the YOUI page first to establish a browser session",
        code: "authentication_required",
      });
    }

    if (UNSAFE_METHODS.has(req.method)) {
      const csrfToken = req.headers["x-youi-csrf"];
      if (!isSameOrigin(req) || typeof csrfToken !== "string" || !safeEqual(csrfToken, client.csrfToken)) {
        return sendJson(res, 403, {
          error: "same-origin CSRF validation failed",
          code: "csrf_rejected",
        });
      }
    }

    if (path === "/api/sessions" || path.startsWith("/api/sessions/")) {
      return await handleSessionApi(req, res, url, path, client);
    }

    const credentials = sessionRequestCredentials(req);
    const session = requireSessionCredential(client, credentials.id, credentials.token);
    requirePageLease(session, credentials.pageId);
    session.state.sessionId = session.id;

    if (UNSAFE_METHODS.has(req.method) && path.startsWith("/api/deploy/")) {
      return sendJson(res, 410, {
        error: "Legacy hard-coded release routes are retired",
        code: "legacy_release_route_retired",
        hint: "Use a target-scoped release workflow with separate commit, publish, and deploy authority.",
      });
    }

    if (UNSAFE_METHODS.has(req.method)
      && path !== "/api/chat"
      && session.state.chatInFlight) {
      return sendJson(res, 409, {
        error: "Session has an active turn",
        code: "session_busy",
      });
    }

    const requiredCapability = requiredRouteCapability(path, req.method);
    if (!requiredCapability) {
      const allowedMethods = allowedRouteMethods(path);
      if (allowedMethods.size > 0) {
        return sendJson(res, 405, {
          error: "Method not allowed",
          code: "method_not_allowed",
        }, {
          Allow: [...allowedMethods].sort().join(", "),
        });
      }
      return sendJson(res, 403, {
        error: "API route has no capability policy",
        code: "capability_policy_missing",
      });
    }
    if (requiredCapability && !session.state.capabilities.has(requiredCapability)) {
      return sendJson(res, 403, {
        error: `capability "${requiredCapability}" is disabled`,
        code: "capability_denied",
        capability: requiredCapability,
      });
    }

    return await runSessionRequest(
      req,
      res,
      session,
      () => handleSessionRequest(req, res, url, path),
    );
  } catch (e) {
    const statusCode = e instanceof HttpError ? e.statusCode : 500;
    if (statusCode >= 500) console.error("Request error:", e);
    if (res.headersSent) return res.end();
    return sendJson(res, statusCode, {
      error: e.message || "Request failed",
      code: e.code || "request_error",
    });
  }
}

// ═════════════════════════════════════════════════════════════════════
// CHAT — The core SSE streaming handler
// ═════════════════════════════════════════════════════════════════════

function throwIfAborted(signal) {
  if (!signal?.aborted) return;
  if (signal.reason instanceof Error) throw signal.reason;
  throw new Error("Browser chat turn cancelled");
}

function abortableDelay(milliseconds, signal) {
  if (!signal) return new Promise(resolveDelay => setTimeout(resolveDelay, milliseconds));
  throwIfAborted(signal);
  return new Promise((resolveDelay, rejectDelay) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolveDelay();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timer);
      rejectDelay(signal.reason instanceof Error ? signal.reason : new Error("Browser chat turn cancelled"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function handleChat(req, res) {
  const turnState = activeState();
  if (turnState.chatInFlight) {
    res.writeHead(409, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({
      error: "another chat turn is already streaming in this browser session",
      hint: "wait for it to finish, or use a separate YOUI tab/session",
    }));
  }
  turnState.chatInFlight = true;

  const turnAbort = new AbortController();
  let responseFinished = false;
  const onFinish = () => { responseFinished = true; };
  const onDisconnect = () => {
    if (!responseFinished && !turnAbort.signal.aborted) {
      turnAbort.abort(new Error("Browser disconnected"));
    }
  };
  res.once("finish", onFinish);
  res.once("close", onDisconnect);
  req.once("aborted", onDisconnect);

  try {
    return await handleChatTurn(req, res, turnAbort.signal);
  } finally {
    turnState.chatInFlight = false;
    res.off("finish", onFinish);
    res.off("close", onDisconnect);
    req.off("aborted", onDisconnect);
  }
}

async function handleChatTurn(req, res, signal) {
  const body = await parseBody(req);
  throwIfAborted(signal);
  const userMessage = body.message;
  const rawMode = body.raw === true;
  // Raw mode forces direct (no minds hint), no tools, no system prompt,
  // and an isolated single-turn message array (state.messages untouched).
  const forceMode = rawMode ? "direct" : (body.chatMode || state.chatMode);
  if (!userMessage) {
    res.writeHead(400, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ error: "No message" }));
  }
  // Each person's message starts a fresh ceiling on calls to other minds
  // (docs/YOUI-MINDS-DESIGN.md §3.3).
  state.mindBudget = createMindBudget({ limit: MIND_CALLS_PER_MESSAGE });

  // SSE headers
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
  });

  // ── ONE ANSWERING PATH ─────────────────────────────────
  // Both modes answer through the selected agent's loop below, with its soul,
  // history and tools (docs/YOUI-MINDS-DESIGN.md §3.1). Orchestrate mode no
  // longer classifies the message in Python and hands it to
  // adaptive.orchestrator; it only tells the agent which minds this session
  // can reach. Raw mode uses an isolated message array (no persistent context
  // bleed), no system prompt and no tools.
  let chatMessages;
  let systemPrompt;
  if (rawMode) {
    chatMessages = [{ role: "user", content: userMessage }];
    systemPrompt = null;
    sendSSE(res, "raw_mode", { model: state.model, note: "no system prompt, no tools, isolated context" });
  } else {
    let minds = null;
    if (forceMode === "orchestrate") {
      const listing = await mindsRegistry.list({ capabilities: state.capabilities });
      throwIfAborted(signal);
      minds = offeredMinds(listing);
      sendSSE(res, "minds", {
        offered: minds,
        checked_at: listing.checked_at,
        callable: hasCapability("orchestrator:run"),
        calls_per_message: MIND_CALLS_PER_MESSAGE,
      });
    }
    state.messages.push({ role: "user", content: userMessage });
    chatMessages = state.messages;
    systemPrompt = buildSystemPrompt(userMessage, { minds });
  }

  let maxTurns = rawMode ? 1 : 50;

  for (let turn = 0; turn < maxTurns; turn++) {
    throwIfAborted(signal);
    if (!rawMode) state.turnCount++;
    sendSSE(res, "status", { phase: "thinking", turn: turn + 1, agent: state.agent });

    let response;
    try {
      response = isOllamaModel(state.model)
        ? await callOllamaModel(chatMessages, systemPrompt, {
            raw: rawMode,
            signal,
            onDelta: (delta) => {
              if (!signal.aborted && delta.type === "text_delta") {
                sendSSE(res, "text_delta", { delta: delta.text });
              }
            }
          })
        : await callClaude(chatMessages, systemPrompt, { raw: rawMode, signal });
    } catch (e) {
      throwIfAborted(signal);
      if (e.status === 429) {
        if (!rawMode) state.ys.senseRateLimit();
        sendSSE(res, "rate_limit", { retryAfter: e.retryAfter, budget: e.budget });
        // Wait and retry
        await abortableDelay(Math.min(e.retryAfter * 1000, 60000), signal);
        if (!rawMode) state.turnCount--;
        continue;
      }
      if (e.status === 529) {
        sendSSE(res, "overloaded", { retryAfter: 30 });
        await abortableDelay(30000, signal);
        if (!rawMode) state.turnCount--;
        continue;
      }
      sendSSE(res, "error", { message: e.message || "Unknown error" });
      break;
    }
    throwIfAborted(signal);

    // Process response blocks
    const usage = response.usage || {};
    const thinkingTokens = usage.thinking_tokens || 0;
    if (!rawMode) state.totalThinkingTokens += thinkingTokens;

    // YOUSPEAK L2: Sense thinking
    if (!rawMode) state.ys.senseThinking(usage);
    // YOUSPEAK L5: Sense turn + budget
    if (!rawMode) state.ys.senseTurn(budget);

    const toolUseBlocks = [];

    for (const block of response.content) {
      if (block.type === "thinking" && block.thinking?.trim()) {
        sendSSE(res, "thinking", { content: block.thinking });
      } else if (block.type === "text" && block.text?.trim()) {
        const ysMetrics = rawMode ? null : measureYouspeak(block.text);
        if (response._streamed) {
          // Text already streamed token-by-token; send finalization with rendered content + youspeak
          sendSSE(res, "text_done", { content: block.text, youspeak: ysMetrics });
        } else {
          sendSSE(res, "text", { content: block.text, youspeak: ysMetrics });
        }
      } else if (block.type === "tool_use") {
        toolUseBlocks.push(block);
        if (!rawMode) {
          sendSSE(res, "tool_call", { id: block.id, name: block.name, input: block.input });
        }
      }
    }

    // Claude 5-series models can decline a request (HTTP 200, stop_reason
    // "refusal"), often with no text at all; say so instead of going quiet.
    if (response.stop_reason === "refusal") {
      const category = response.stop_details?.category;
      sendSSE(res, "error", {
        message: `The model declined this request${category ? ` (${category})` : ""}. Rephrase it, or choose another model.`,
      });
    }

    if (rawMode && toolUseBlocks.length > 0) {
      sendSSE(res, "error", { message: "Raw mode rejected a provider tool call" });
      throw new Error("Raw mode provider returned a tool call");
    }

    // Track provider usage
    const provider = response._provider || (isOllamaModel(state.model) ? "ollama" : "claude");
    if (!rawMode) {
      trackProviderUsage(provider, state.model, {
        ...usage,
        thinking_tokens: thinkingTokens,
      });
    }

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
      ollamaCost: provider === "ollama" ? state.providerUsage.ollama.cost : undefined,
      turn: state.turnCount,
      youspeak: state.ys.statusLine(),
    });

    // An empty assistant turn (a refusal can have no content) would make the
    // next request invalid, so it stays out of the history.
    if (!rawMode && response.content?.length) state.messages.push({ role: "assistant", content: response.content });

    // No tools → done
    if (toolUseBlocks.length === 0) break;

    // Execute tools — parallel when multiple tool_calls come back in one turn.
    // Single tool_call: serial (no overhead). 2+: Promise.all for concurrent dispatch.
    // This is the main throughput win for agentic GLM 5.1 interaction — tool calls
    // (bash, read_file, grep, etc.) are I/O bound, not CPU bound.
    const toolResults = [];
    // A mind tool says which providers and models received content, with their
    // privacy labels, so the page can show it in this turn (docs/YOUI-MINDS-DESIGN.md §4).
    const toolContext = toolUse => ({
      signal,
      onMindsUsed: ({ tool, minds }) => sendSSE(res, "minds_used", {
        id: toolUse.id,
        tool,
        minds: minds.map(({ provider, model, privacy, destination, role, ok, error, usage }) => ({
          provider, model, privacy, destination, role, ok, error, usage,
        })),
      }),
    });

    if (toolUseBlocks.length === 1) {
      throwIfAborted(signal);
      // Single tool — serial (no Promise.all overhead)
      const toolUse = toolUseBlocks[0];
      state.totalToolCalls++;
      const toolSense = state.ys.senseToolCall(toolUse.name, toolUse.input, null);
      sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });
      const result = await executeTool(toolUse.name, toolUse.input, toolContext(toolUse));
      throwIfAborted(signal);
      const truncated = result.slice(0, 50000);
      toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: truncated });
      sendSSE(res, "tool_result", { id: toolUse.id, name: toolUse.name, result: truncated.slice(0, 5000) });
    } else {
      // Multiple tools — execute in parallel via Promise.all
      // Emit all "executing" SSE events first so the UI shows them immediately
      for (const toolUse of toolUseBlocks) {
        state.totalToolCalls++;
        const toolSense = state.ys.senseToolCall(toolUse.name, toolUse.input, null);
        sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });
      }

      const settled = await Promise.all(
        toolUseBlocks.map(async (toolUse) => {
          try {
            throwIfAborted(signal);
            const result = await executeTool(toolUse.name, toolUse.input, toolContext(toolUse));
            return { toolUse, result: result.slice(0, 50000), ok: true };
          } catch (e) {
            return { toolUse, result: `Error: ${e.message}`.slice(0, 50000), ok: false };
          }
        })
      );
      throwIfAborted(signal);

      // Collect results in original order and emit SSE
      for (const { toolUse, result } of settled) {
        toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: result });
        sendSSE(res, "tool_result", { id: toolUse.id, name: toolUse.name, result: result.slice(0, 5000) });
      }
    }

    state.messages.push({ role: "user", content: toolResults });

    // YOUSPEAK L4: Sense context after adding messages
    state.ys.senseContext(state.messages, systemPrompt.length);

    // YOUSPEAK DECIDE: Check for adaptive signals
    const signals = state.ys.decide(state.effort, state.model, budget);
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
          const { pruned } = state.ys.pruneContext(state.messages);
          if (pruned > 0) {
            sendSSE(res, "youspeak_action", { action: "context_pruned", pruned, reason: sig.reason });
          }
        }
      }
    }
  }

  throwIfAborted(signal);
  // Persist YOUSPEAK session data
  if (!IS_TEST && !rawMode) state.ys.persist();

  // SP1: fire-and-forget mode-two detection (post-stream, no await)
  // Direct-mode terminal response is the last assistant message in state.messages.
  if (!rawMode) {
    const lastAssistant = [...state.messages].reverse().find(m => m.role === "assistant");
    fireDetection(state, lastAssistant ? lastAssistant.content : null);
  }

  sendSSE(res, "done", {
    turnCount: state.turnCount,
    totalToolCalls: state.totalToolCalls,
    totalThinkingTokens: state.totalThinkingTokens,
    youspeak: state.ys.report(),
  });
  res.end();
}

// ═════════════════════════════════════════════════════════════════════
// BOOT
// ═════════════════════════════════════════════════════════════════════

const server = createServer(handleRequest);
server.headersTimeout = 30_000;
server.requestTimeout = 5 * 60_000;
server.maxHeadersCount = 100;

// Bind to IPv4 loopback. To reach YOUI from another device,
// keep this boundary and tunnel via SSH:
//   ssh -L 17777:127.0.0.1:17777 yu@air   →   http://localhost:17777

server.listen(PORT, HOST, async () => {
  const address = server.address();
  const boundPort = typeof address === "object" && address ? address.port : PORT;
  const boundAddress = typeof address === "object" && address ? address.address : HOST;
  if (IS_TEST) {
    console.log(`YOUI_TEST_READY ${boundPort} ${boundAddress}`);
    return;
  }

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

  const policy = `\x1b[2m(loopback only — remote access requires an SSH tunnel)\x1b[0m`;
  console.log(`\x1b[32m  ➜  http://localhost:${boundPort}  ${policy}\x1b[0m`);
  console.log(`\x1b[2m  Capabilities: ${[...SERVER_CAPABILITIES].sort().join(", ") || "(none)"}\x1b[0m`);
  console.log("");

  if (SERVER_CAPABILITIES.has("memory:write")) {
    appendDailyNote(`YOUI Web started on port ${boundPort}. Agent: ${agent.name}. Model: ${state.model}. Loopback-only.`);
  }
  loadAutonomousState();
  // Autonomous loop is OFF by default — set AUTONOMOUS=1 to auto-start at boot.
  // Otherwise, start it on demand via POST /api/autonomous/start.
  if (process.env.AUTONOMOUS === "1" && SERVER_CAPABILITIES.has("autonomous:control")) {
    startAutonomousLoop();
    console.log(`  \x1b[32m✓\x1b[0m Autonomous: LIVE (${autonomous.cycleCount} prior cycles, continuous)`);
  } else if (process.env.AUTONOMOUS === "1") {
    console.warn("  ⚠ Autonomous boot requested but autonomous:control is not granted; staying OFF.");
  } else {
    console.log(`  \x1b[2m○ Autonomous: OFF (${autonomous.cycleCount} prior cycles) — set AUTONOMOUS=1 or POST /api/autonomous/start\x1b[0m`);
  }
});
