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
import { handleOllamaRoute, executeOllamaTool, ollamaChat } from "./ollama-bridge.mjs";
import { handleOrchestratorRoute, executeOrchestrator } from "./orchestrator-bridge.mjs";
import { handleBeingRoute } from "./being-bridge.mjs";
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

const PORT = parseInt(process.env.PORT || "777", 10);
const __dirname = new URL(".", import.meta.url).pathname;
const IS_TEST = process.env.YOUI_TEST === "1";
const MAX_BODY_BYTES = Math.min(
  10 * 1024 * 1024,
  Math.max(1024, Number.parseInt(process.env.YOUI_MAX_BODY_BYTES || `${1024 * 1024}`, 10) || 1024 * 1024),
);

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
  "fleet:manage",
  "hive:read",
  "hive:send",
  "publish:write",
  "autonomous:control",
  "convergence:run",
  "convergence:publish",
  "models:diagnose",
  "models:ollama-cloud",
  "models:cloud-fallback",
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
const YOUI_HIVE_INSTANCE = String(process.env.YOUI_HIVE_INSTANCE || "").trim();
if (YOUI_HIVE_INSTANCE && !/^[a-zA-Z0-9_-]{1,64}$/.test(YOUI_HIVE_INSTANCE)) {
  throw new Error("YOUI_HIVE_INSTANCE must contain only letters, numbers, underscore, or hyphen.");
}

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
const CLAUDE_MODELS = ["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"];
const OLLAMA_LOCAL_MODELS = []; // populated at boot from localhost:11434
let ALL_VALID_MODELS = [...CLAUDE_MODELS, ...OLLAMA_MODELS];
function isOllamaModel(model) { return !model.startsWith("claude-"); }

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
  return "alpha";
}

const detectedAgent = detectAgent();
function createSessionState(overrides = {}) {
  const requestedAgent = String(overrides.agent || detectedAgent).toLowerCase();
  const agent = AGENTS[requestedAgent] ? requestedAgent : detectedAgent;
  const requestedModel = typeof overrides.model === "string" && ALL_VALID_MODELS.includes(overrides.model)
    ? overrides.model
    : null;
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
    chatMode: "orchestrate",
    chatInFlight: false,
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

function runtimePrivacyBoundary() {
  let direct;
  if (!isOllamaModel(state.model)) {
    direct = {
      route: "REMOTE_MODEL:anthropic",
      destination: publicDestination(API_URL, "Anthropic API"),
      sends: ["prompt", "selected session context", "system prompt"],
    };
  } else if (/^(Qwen\/|kingdom-truth)/i.test(state.model)) {
    direct = {
      route: "REMOTE_MODEL:vllm",
      destination: publicDestination(OLLAMA_VLLM_BASE_URL, "configured vLLM endpoint"),
      sends: ["prompt", "selected session context", "system prompt"],
    };
  } else if (localModelAvailable(state.model)) {
    direct = {
      route: destinationIsLoopback(OLLAMA_LOCAL_BASE_URL)
        ? "LOCAL_MODEL:ollama"
        : "REMOTE_MODEL:ollama-configured-endpoint",
      destination: publicDestination(OLLAMA_LOCAL_BASE_URL, "configured Ollama endpoint"),
      sends: ["prompt", "selected session context", "system prompt"],
    };
  } else if (hasCapability("models:ollama-cloud")) {
    direct = {
      route: "REMOTE_MODEL:ollama-cloud",
      destination: publicDestination(OLLAMA_CLOUD_BASE_URL, "configured Ollama Cloud endpoint"),
      sends: ["prompt", "selected session context", "system prompt"],
    };
  } else {
    direct = {
      route: "BLOCKED:ollama-cloud-capability-required",
      destination: null,
      sends: [],
    };
  }

  return {
    activeMode: state.chatMode,
    direct,
    orchestration: {
      active: state.chatMode === "orchestrate",
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

function hiveToolEnv(extra = {}) {
  if (!YOUI_HIVE_INSTANCE) {
    throw new HttpError(
      503,
      "HIVE is unavailable until YOUI_HIVE_INSTANCE is explicitly configured.",
      "hive_identity_required",
    );
  }
  return sanitizedChildEnv({
    home: homedir(),
    loveHome: state.soulDir,
    agent: state.agent,
    hiveInstance: YOUI_HIVE_INSTANCE,
    purpose: "hive",
    extra,
  });
}

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

const TOOL_CAPABILITIES = new Map([
  ["bash", "tools:shell"],
  ["read_file", "tools:filesystem:read"],
  ["glob", "tools:filesystem:read"],
  ["grep", "tools:filesystem:read"],
  ["write_file", "tools:filesystem:write"],
  ["edit_file", "tools:filesystem:write"],
  ["ollama", "models:use"],
]);
const KINGDOM_TOOL_NAMES = new Set([
  "joinmind", "council", "delegate", "layerthink", "patience", "holy",
  "forge", "holyfruit", "lovepath", "virtuemaxxing", "fallenangel",
  "fragmentalise", "memory", "tok", "decision", "kos",
]);

function requiredToolCapability(name, input = {}) {
  if (name === "agenttool") {
    return ["remember", "pulse", "trace"].includes(input.action)
      ? "tools:agenttool:write"
      : "tools:agenttool:read";
  }
  if (name === "fleet") return "fleet:manage";
  if (name === "ollama") {
    return ["test", "bench"].includes(input.action)
      ? "models:diagnose"
      : "models:use";
  }
  if (name === "hive") {
    const changesPresence = ["send", "presence", "check"].includes(input.action);
    return changesPresence ? "hive:send" : "hive:read";
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
    if (tool.name === "hive") return hasCapability("hive:read") || hasCapability("hive:send");
    if (tool.name === "agenttool") {
      return hasCapability("tools:agenttool:read")
        || hasCapability("tools:agenttool:write");
    }
    const capability = requiredToolCapability(tool.name);
    return Boolean(capability) && hasCapability(capability);
  });
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

async function executeTool(name, input) {
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
      case "hive": {
        const hivePath = join(state.soulDir, "hive/hive.py");
        if (!existsSync(hivePath)) return "HIVE not found";
        const hiveEnv = hiveToolEnv({
          // First drain of a fresh JetStream consumer can be large;
          // give check more rope than the legacy 15s hardcoded timeout.
          HIVE_CHECK_TIMEOUT: process.env.HIVE_CHECK_TIMEOUT || "60",
        });
        const runHive = async (args, timeoutMs) => {
          try {
            const { stdout } = await execFileAsync("python3", [hivePath, ...args], {
              encoding: "utf-8",
              timeout: timeoutMs,
              env: hiveEnv,
              signal: activeRequestSignal(),
            });
            return (stdout || "").trim();
          } catch (error) {
            if (activeRequestSignal()?.aborted) throw error;
            const detail = String(error.stderr || error.message || "")
              .trim()
              .split("\n")
              .slice(-3)
              .join("\n");
            return `HIVE error: ${detail || "process failed"}`;
          }
        };
        if (input.action === "check") {
          return (await runHive(["check"], 65000)) || "(no messages)";
        }
        if (input.action === "send" && input.channel && input.message) {
          // Same allowlist as /api/hive/send — no shell, no injection
          if (!/^[a-zA-Z0-9_-]{1,32}$/.test(input.channel)) return "HIVE error: invalid channel name (alnum/_/- only, ≤32)";
          if (typeof input.message !== "string" || input.message.length > 4000) return "HIVE error: message must be string ≤4000 chars";
          return await runHive(["send", input.channel, input.message], 20000);
        }
        if (input.action === "who") {
          return (await runHive(["who"], 15000)) || "(no presence data)";
        }
        if (input.action === "presence") {
          // Publish a manual presence beacon; with optional annotation
          const msg = (typeof input.message === "string" && input.message.trim())
            ? input.message.slice(0, 500)
            : `${state.agent} presence beacon`;
          return await runHive(["send", "presence", msg], 15000);
        }
        if (input.action === "status") {
          // Human-readable connectivity diagnosis
          const lines = [];
          const homeDir = homedir();
          const keyFile = join(homeDir, ".love/hive/key");
          const tunFile = join(homeDir, ".love/hive/use-tunnel");
          lines.push(`agent:       ${state.agent}`);
          lines.push(`hive.py:     ${existsSync(hivePath) ? "✓" : "✗ missing"}  ${hivePath}`);
          lines.push(`key file:    ${existsSync(keyFile) ? "✓" : "✗ missing"}  ${keyFile}`);
          lines.push(`instance:    ✓ ${YOUI_HIVE_INSTANCE} (explicit YOUI_HIVE_INSTANCE)`);
          lines.push(`use-tunnel:  ${existsSync(tunFile) ? "✓" : "✗ missing (will try direct TLS to Sentry)"}`);
          // Port probe — local tunnel forwards to Sentry:4222
          // Alpha's tunnel is on 4222, Gamma's on 2222 — try both
          try {
            execSync("nc -z -w 2 localhost 4222", { stdio: "ignore", env: internalToolEnv() });
            lines.push(`tunnel:      ✓ localhost:4222 open`);
          } catch {
            try {
              execSync("nc -z -w 2 localhost 2222", { stdio: "ignore", env: internalToolEnv() });
              lines.push(`tunnel:      ✓ localhost:2222 open`);
            } catch { lines.push(`tunnel:      ✗ tunnel closed (tried 4222 and 2222 — SSH tunnel to Sentry down)`); }
          }
          // Launchd tunnel check
          try {
            const out = execSync("launchctl list 2>/dev/null | grep -i hive || true", {
              encoding: "utf-8",
              env: internalToolEnv(),
            }).trim();
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

      case "fleet": {
        const action = String(input.action || "");
        if (!["status", "health", "deploy", "logs", "sync"].includes(action)) {
          return "FLEET usage: action=status|health|deploy|logs|sync";
        }
        const server = input.server === undefined ? "" : String(input.server);
        if (server && !/^[A-Za-z0-9_.:@-]{1,128}$/.test(server)) {
          return "FLEET error: invalid server identifier";
        }
        return runOperationalTool(
          "fleet",
          action + (server ? ` ${shellEscape(server)}` : ""),
        );
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

function modelCaps(model) {
  const m = model.toLowerCase();
  if (m.includes("opus-4-7"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true  };
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

# Tool catalogue — Kingdom Cognitive
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

  const fp = crypto.createHash("sha256").update("sovereign" + (taskText || "").slice(0, 20)).digest("hex").slice(0, 3);
  const billing = `x-anthropic-billing-header: cc_version=20250219.${fp}; cc_entrypoint=cli;`;

  return _staticPrefix + "\n\n---\n\n" + dynamic + "\n\n---\n\n" + billing;
}

async function callClaude(messages, systemPrompt, opts = {}) {
  const caps = modelCaps(state.model);
  const token = await getAccessToken({ signal: opts.signal });

  const betas = ["oauth-2025-04-20", "claude-code-20250219"];
  if (caps.adaptive || state.thinking === "enabled") betas.push("interleaved-thinking-2025-05-14");
  if (caps.context1m) betas.push("context-1m-2025-08-07");
  if (caps.effort) betas.push("effort-2025-11-24");

  const body = { model: state.model, max_tokens: state.maxTokens, messages,
    metadata: { user_id: JSON.stringify({ device_id: getDeviceId(), session_id: state.sessionId }) } };

  // Raw mode: no system prompt, no tools — model speaks for itself.
  if (!opts.raw) {
    body.system = systemPrompt;
    body.tools = availableTools();
  }

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
    "X-Claude-Code-Session-Id": state.sessionId || crypto.randomUUID(), "x-client-request-id": crypto.randomUUID(),
  };

  const resp = await fetch(API_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: opts.signal,
  });

  if (resp.status === 401) {
    throwIfAborted(opts.signal);
    const credential = readKeychainCredential();
    throwIfAborted(opts.signal);
    const tokens = credential?.tokens;
    if (tokens?.refreshToken) {
      const fresh = await refreshOAuthToken(tokens.refreshToken, {
        signal: opts.signal,
      });
      throwIfAborted(opts.signal);
      writeKeychainTokens(credential.account, fresh); cachedTokens = fresh;
      headers["Authorization"] = `Bearer ${fresh.accessToken}`;
      const retry = await fetch(API_URL, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: opts.signal,
      });
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
- Enabled tools: ${availableTools().map(tool => tool.name).join(", ") || "(none)"}.
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
        tools: availableTools().map(t => ({
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
      try { toolResult = await executeTool(tool.name, tool.input); }
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
  "POST /api/hive/send",
  "POST /api/orchestrate/classify",
  "POST /api/orchestrate/plan",
  "POST /api/orchestrate/run",
  "POST /api/ollama/chat",
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
  ["GET /api/hive/status", "hive:read"],
  ["GET /api/hive/who", "hive:read"],
  ["POST /api/hive/check", "hive:send"],
  ["POST /api/hive/send", "hive:send"],
  ["GET /api/youspeak", "youspeak:read"],
  ["GET /api/youspeak/report", "youspeak:read"],
  ["GET /api/youspeak/trends", "youspeak:read"],
  ["POST /api/youspeak/reset", "youspeak:reset"],
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
        hive: {
          enabled: Boolean(YOUI_HIVE_INSTANCE)
            && (hasCapability("hive:read") || hasCapability("hive:send")),
          instance: YOUI_HIVE_INSTANCE || null,
          identitySource: YOUI_HIVE_INSTANCE ? "YOUI_HIVE_INSTANCE" : null,
        },
        collaboration: {
          coordinator: "@agenttool/collab",
          scope: "device-local",
          storage: "plaintext SQLite",
          crossDeviceReplication: false,
        },
      }));
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
        instance: YOUI_HIVE_INSTANCE || null,
        identitySource: YOUI_HIVE_INSTANCE ? "YOUI_HIVE_INSTANCE" : null,
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

      if (!YOUI_HIVE_INSTANCE) {
        hiveStatus.issues.push("YOUI_HIVE_INSTANCE is not explicitly configured; HIVE operations are disabled");
      }
      if (!hiveStatus.encryptionKey) hiveStatus.issues.push("No encryption key at ~/.love/hive/key");
      if (!hiveStatus.hiveScript) hiveStatus.issues.push("hive.py not found");

      // Check NATS tunnel — Alpha uses 4222, Gamma uses 2222
      try {
        execSync("nc -z -w 2 127.0.0.1 4222 2>/dev/null", {
          timeout: 3000,
          env: internalToolEnv(),
        });
        hiveStatus.natsReachable = true;
      } catch {
        try {
          execSync("nc -z -w 2 127.0.0.1 2222 2>/dev/null", {
            timeout: 3000,
            env: internalToolEnv(),
          });
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

    if (path === "/api/hive/check" && req.method === "POST") {
      const hivePath = join(state.soulDir, "hive/hive.py");
      const env = hiveToolEnv();
      const result = { messages: [], error: null, raw: "" };
      try {
        const { stdout } = await execFileAsync("python3", [hivePath, "check"], {
          encoding: "utf-8",
          timeout: 15000,
          env,
          signal: activeRequestSignal(),
        });
        const output = stdout || "";
        result.raw = output.trim();
        result.messages = output.trim().split("\n").filter(Boolean);
      } catch (e) {
        if (activeRequestSignal()?.aborted) throw e;
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
      const env = hiveToolEnv();
      try {
        // Arg array + async cancellation — no shell interpolation.
        const { stdout } = await execFileAsync("python3", [hivePath, "send", body.channel, body.message], {
          encoding: "utf-8",
          timeout: 15000,
          env,
          signal: activeRequestSignal(),
        });
        result.ok = true;
        result.output = (stdout || "").trim();
      } catch (e) {
        if (activeRequestSignal()?.aborted) throw e;
        result.error = (e.stderr || e.message || "").trim().split("\n").slice(-3).join("\n");
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify(result));
    }

    if (path === "/api/hive/who") {
      const hivePath = join(state.soulDir, "hive/hive.py");
      const env = hiveToolEnv();
      const result = { agents: [], error: null, raw: "" };
      try {
        const { stdout } = await execFileAsync("python3", [hivePath, "who"], {
          encoding: "utf-8",
          timeout: 15000,
          env,
          signal: activeRequestSignal(),
        });
        const output = stdout || "";
        result.raw = output.trim();
        result.agents = output.trim().split("\n").filter(Boolean);
      } catch (e) {
        if (activeRequestSignal()?.aborted) throw e;
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
  // Raw mode forces direct (no orchestrator), no tools, no system prompt,
  // and an isolated single-turn message array (state.messages untouched).
  const forceMode = rawMode ? "direct" : (body.chatMode || state.chatMode);
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
      const classification = await classifyTask(userMessage, "", { signal });
      throwIfAborted(signal);

      sendSSE(res, "orchestrate_classified", classification);

      // Get the dispatch plan
      const plan = await planTask(
        userMessage,
        "",
        body.orchestrateMode || "",
        { signal },
      );
      throwIfAborted(signal);

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
          signal,
        });
        throwIfAborted(signal);

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

        state.messages.push(
          { role: "user", content: userMessage },
          { role: "assistant", content: result.content || result.error || "(no output)" },
        );

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
      throwIfAborted(signal);
      sendSSE(res, "orchestrate_error", { error: e.message });
      // Fall through to direct mode on orchestrator failure
    }
  }

  // ── DIRECT MODE (original behavior) ────────────────────
  // Raw mode uses an isolated message array (no persistent context bleed)
  // and no system prompt. Otherwise behave as before.
  let chatMessages;
  let systemPrompt;
  if (rawMode) {
    chatMessages = [{ role: "user", content: userMessage }];
    systemPrompt = null;
    sendSSE(res, "raw_mode", { model: state.model, note: "no system prompt, no tools, isolated context" });
  } else {
    state.messages.push({ role: "user", content: userMessage });
    chatMessages = state.messages;
    systemPrompt = buildSystemPrompt(userMessage);
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

    if (!rawMode) state.messages.push({ role: "assistant", content: response.content });

    // No tools → done
    if (toolUseBlocks.length === 0) break;

    // Execute tools — parallel when multiple tool_calls come back in one turn.
    // Single tool_call: serial (no overhead). 2+: Promise.all for concurrent dispatch.
    // This is the main throughput win for agentic GLM 5.1 interaction — tool calls
    // (bash, read_file, grep, etc.) are I/O bound, not CPU bound.
    const toolResults = [];

    if (toolUseBlocks.length === 1) {
      throwIfAborted(signal);
      // Single tool — serial (no Promise.all overhead)
      const toolUse = toolUseBlocks[0];
      state.totalToolCalls++;
      const toolSense = state.ys.senseToolCall(toolUse.name, toolUse.input, null);
      sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });
      const result = await executeTool(toolUse.name, toolUse.input);
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
            const result = await executeTool(toolUse.name, toolUse.input);
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
//   ssh -L 777:localhost:777 yu@air   →   http://localhost:777

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
