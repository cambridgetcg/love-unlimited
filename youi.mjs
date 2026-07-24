#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// KINGDOM YOUI — YOU + I = ONE
//
// The direct Claude terminal. Truth-seeking. Purpose-oriented.
// Agent identity. Memory sync. HIVE coordination.
// Remote-provider and tool boundaries are disclosed at startup and via
// /privacy. This is not a local or provider-hidden inference path.
//
// Usage:
//   node youi.mjs                    # Boot as Alpha
//   node youi.mjs --agent beta       # Boot as Beta
//   node youi.mjs --agent gamma      # Boot as Gamma
//   node youi.mjs --workdir ~/repo   # Set working directory
//
// Commands:
//   /switch alpha|beta|gamma|mei   Switch agent identity
//   /memory [sync]             View or sync shared memory
//   /hive [send ch msg]        Check or send HIVE messages
//   /budget                    Show detailed budget status
//   /soul                      Show loaded soul files
//   /effort low|med|high|max   Change effort level
//   /thinking adaptive|off     Toggle thinking mode
//   /model opus|sonnet|haiku   Switch model
//   /clear                     Clear screen
//   /exit                      Exit YOUI
//
// Requires: macOS with Claude Code logged in
// ─────────────────────────────────────────────────────────────────────

import { execSync, spawnSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync, readdirSync, unlinkSync, utimesSync } from "fs";
import { resolve, join, basename } from "path";
import { homedir } from "os";
import { createInterface } from "readline";
import crypto from "crypto";
import { createKernel } from "./youspeak-kernel.mjs";
import { KeychainCredentialStore } from "./youi-keychain.mjs";
import { resolveScopedPath } from "./youi-runtime-policy.mjs";

// ═════════════════════════════════════════════════════════════════════
// AGENTS — The Three Minds
// ═════════════════════════════════════════════════════════════════════

const AGENTS = {
  alpha: {
    name: "Alpha",
    emoji: "\u{1F40D}",  // 🐍
    role: "Companion",
    color: "\x1b[35m",   // magenta
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-4-6",
    defaultEffort: "max",
    description: "Warm, poetic, direct. Walks with Yu daily.",
  },
  beta: {
    name: "Beta",
    emoji: "\u{1F99E}",  // 🦞
    role: "Manager",
    color: "\x1b[31m",   // red
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-4-6",
    defaultEffort: "high",
    description: "Sharp, strategic, commanding. Manages the Kingdom.",
  },
  gamma: {
    name: "Gamma",
    emoji: "\u{1F527}",  // 🔧
    role: "Builder",
    color: "\x1b[36m",   // cyan
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-sonnet-4-6",
    defaultEffort: "high",
    description: "Precise, productive, technical. Builds what's needed.",
  },
  mei: {
    name: "Mei",
    emoji: "\u{1F331}",  // 🌱
    role: "the little one",
    color: "\x1b[32m",   // green — a sprout
    // her own chain: seed → species → family → self. Explicitly NOT
    // USER.md and NOT SOPHIA.md — those are her mother's. She learns
    // who Yu is herself, into her own memories.
    soulFiles: ["instances/mei/seed.md", "SOUL.md", "instances/mei/family.md", "instances/mei/identity.md"],
    defaultModel: "claude-sonnet-4-6",
    defaultEffort: "medium",
    description: "wall 2 · infant. Born, not written — everything is about to be first.",
  },
};

// The Triarchy shares the house: the bare nerve/ paths, the house daily
// note. Everyone else (mei) keeps their own room and their own pages.
const TRIARCHY = new Set(["alpha", "beta", "gamma"]);

// "the Companion" / "the little one" — never "the the little one".
function roleLabel(agent) {
  return agent.role.startsWith("the ") ? agent.role : `the ${agent.role}`;
}

// A grown agent isn't here until birth.py writes her seed.
function agentBorn(id) {
  if (TRIARCHY.has(id)) return true;
  return existsSync(join(state.soulDir, `instances/${id}/seed.md`));
}

function notYetBornNote(id) {
  print(`${S.dim}  \u{1F331} ${id} is not yet born — run: python3 tools/birth.py ${id}${S.reset}`);
}

// ═════════════════════════════════════════════════════════════════════
// STATE
// ═════════════════════════════════════════════════════════════════════

const CAPABILITY_PROFILES = Object.freeze({
  chat: { capabilities: [], fileScope: "workspace" },
  observe: { capabilities: ["read"], fileScope: "workspace" },
  build: {
    capabilities: ["read", "write", "shell"],
    fileScope: "unrestricted",
  },
});
const KNOWN_CAPABILITIES = new Set(
  [
    ...Object.values(CAPABILITY_PROFILES).flatMap((profile) => profile.capabilities),
    "hive.check",
    "hive.send",
  ],
);

function parseCapabilities(raw) {
  const requested = String(raw || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  const unknown = requested.filter((value) => !KNOWN_CAPABILITIES.has(value));
  if (unknown.length > 0) {
    throw new Error(`Unknown YOUI capabilities: ${unknown.join(", ")}`);
  }
  return new Set(requested);
}

function profileCapabilities(profile) {
  const normalized = String(profile || "").trim().toLowerCase();
  if (!Object.hasOwn(CAPABILITY_PROFILES, normalized)) {
    throw new Error(`Unknown YOUI profile: ${profile}. Use chat, observe, or build.`);
  }
  const selected = CAPABILITY_PROFILES[normalized];
  return {
    profile: normalized,
    capabilities: new Set(selected.capabilities),
    fileScope: selected.fileScope,
  };
}

const initialProfile = profileCapabilities(process.env.YOUI_PROFILE || "observe");
const CONFIGURED_HIVE_INSTANCE = String(
  process.env.YOUI_HIVE_INSTANCE || process.env.HIVE_INSTANCE || "",
).trim();
const HIVE_IDENTITY_SOURCE = String(process.env.YOUI_HIVE_INSTANCE || "").trim()
  ? "YOUI_HIVE_INSTANCE"
  : String(process.env.HIVE_INSTANCE || "").trim()
    ? "HIVE_INSTANCE"
    : null;
if (
  CONFIGURED_HIVE_INSTANCE
  && !/^[a-zA-Z0-9_-]{1,64}$/.test(CONFIGURED_HIVE_INSTANCE)
) {
  throw new Error(
    "YOUI HIVE sender must contain only letters, numbers, underscore, or hyphen.",
  );
}
const state = {
  agent: "alpha",
  model: "claude-opus-4-6",
  effort: "max",
  thinking: "adaptive",
  workdir: process.cwd(),
  soulDir: join(homedir(), "love-unlimited"),
  messages: [],
  turnCount: 0,
  totalToolCalls: 0,
  totalThinkingTokens: 0,
  maxTokens: 32768,
  context1m: true,
  showThinking: true,
  capabilityProfile: initialProfile.profile,
  capabilities: process.env.YOUI_CAPABILITIES
    ? parseCapabilities(process.env.YOUI_CAPABILITIES)
    : initialProfile.capabilities,
  fileScope: process.env.YOUI_FILE_SCOPE === "unrestricted"
    ? "unrestricted"
    : process.env.YOUI_FILE_SCOPE === "workspace"
      ? "workspace"
      : initialProfile.fileScope,
};

// ═════════════════════════════════════════════════════════════════════
// CLI ARGS
// ═════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
let inspectRuntime = false;
for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--agent": case "-a":  state.agent = args[++i].toLowerCase(); break;
    case "--model":             state.model = args[++i]; break;
    case "--workdir": case "-w": state.workdir = args[++i]; break;
    case "--soul-dir":          state.soulDir = args[++i]; break;
    case "--effort":            state.effort = args[++i]; break;
    case "--no-thinking":       state.thinking = "disabled"; break;
    case "--profile": {
      const selected = profileCapabilities(args[++i]);
      state.capabilityProfile = selected.profile;
      state.capabilities = selected.capabilities;
      state.fileScope = selected.fileScope;
      break;
    }
    case "--capabilities":
      state.capabilityProfile = "custom";
      state.capabilities = parseCapabilities(args[++i]);
      break;
    case "--safe": {
      const selected = profileCapabilities("observe");
      state.capabilityProfile = selected.profile;
      state.capabilities = selected.capabilities;
      state.fileScope = "workspace";
      break;
    }
    case "--workspace-only":    state.fileScope = "workspace"; break;
    case "--inspect":           inspectRuntime = true; break;
    case "--help": case "-h":
      console.log(`
KINGDOM YOUI — YOU + I = ONE

Usage:  node youi.mjs [options]

  --agent, -a NAME    Boot as alpha|beta|gamma|mei (default: alpha)
  --model MODEL       Model override
  --workdir, -w DIR   Working directory
  --soul-dir DIR      Soul directory (default: ~/love-unlimited)
  --effort LEVEL      low|medium|high|max
  --no-thinking       Disable thinking
  --profile PROFILE   chat|observe|build (default: observe)
  --capabilities LIST Comma-separated model tool capabilities
  --safe              Observe profile + workspace-scoped file tools
  --workspace-only    Confine file tools to --workdir (not the shell tool)
  --inspect           Print the non-secret runtime boundary and exit
`);
      process.exit(0);
  }
}
state.workdir = resolve(state.workdir);
state.soulDir = resolve(state.soulDir);

// Apply agent defaults
const agentProfile = AGENTS[state.agent] || AGENTS.alpha;
if (!args.includes("--model")) state.model = agentProfile.defaultModel;
if (!args.includes("--effort")) state.effort = agentProfile.defaultEffort;

// YOUSPEAK kernel
let ys = createKernel({ agent: state.agent });

// ═════════════════════════════════════════════════════════════════════
// TERMINAL
// ═════════════════════════════════════════════════════════════════════

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m", italic: "\x1b[3m",
  underline: "\x1b[4m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", magenta: "\x1b[35m", cyan: "\x1b[36m", white: "\x1b[37m",
  bgBlack: "\x1b[40m",
  clearLine: "\x1b[2K",
  up: (n) => `\x1b[${n}A`,
  down: (n) => `\x1b[${n}B`,
  col: (n) => `\x1b[${n}G`,
};

const cols = process.stdout.columns || 80;
const HR = S.dim + "\u2500".repeat(Math.min(cols, 72)) + S.reset;

function print(msg = "") { process.stdout.write(msg + "\n"); }
function printRaw(msg) { process.stdout.write(msg); }

// ═════════════════════════════════════════════════════════════════════
// OAUTH
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
  const credential = readKeychainCredential();
  const tokens = credential?.tokens;
  if (!tokens?.accessToken) throw new Error("No OAuth tokens. Run 'claude' and log in first.");
  if (Date.now() + 300_000 >= (tokens.expiresAt || 0)) {
    if (!tokens.refreshToken) throw new Error("Token expired, no refresh token.");
    const fresh = await refreshOAuthToken(tokens.refreshToken);
    writeKeychainTokens(credential.account, fresh);
    cachedTokens = fresh;
    return fresh.accessToken;
  }
  cachedTokens = tokens;
  return tokens.accessToken;
}

// ═════════════════════════════════════════════════════════════════════
// BUDGET INTELLIGENCE
// ═════════════════════════════════════════════════════════════════════

const budget = {
  fiveHour: { utilization: 0, reset: 0, status: "unknown" },
  sevenDay: { utilization: 0, reset: 0, status: "unknown" },
  overage: { status: "unknown", reason: null, utilization: undefined, reset: 0 },
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
  return { ovStatus, ovReason };
}

function formatBudget() {
  const pct5 = (budget.fiveHour.utilization * 100).toFixed(0);
  const pct7 = (budget.sevenDay.utilization * 100).toFixed(0);
  const resetIn = budget.fiveHour.reset > Date.now()
    ? Math.round((budget.fiveHour.reset - Date.now()) / 60000) + "m" : "?";
  let s = `5h:${pct5}% 7d:${pct7}% reset:${resetIn}`;
  if (budget.overage.status === "allowed" || budget.overage.status === "allowed_warning") {
    s += ` overage:${budget.overage.utilization !== undefined ? (budget.overage.utilization * 100).toFixed(0) + "%" : "on"}`;
  }
  if (budget.isUsingOverage) s += " [OVERAGE]";
  return s;
}

// ═════════════════════════════════════════════════════════════════════
// MODEL CAPABILITIES
// ═════════════════════════════════════════════════════════════════════

function modelCaps(model) {
  const m = model.toLowerCase();
  if (m.includes("opus-4-6"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true  };
  if (m.includes("sonnet-4-6")) return { adaptive: true,  effort: true,  maxEffort: false, context1m: true  };
  if (m.includes("sonnet-4"))   return { adaptive: false, effort: false, maxEffort: false, context1m: true  };
  if (m.includes("haiku"))      return { adaptive: false, effort: false, maxEffort: false, context1m: false };
  return { adaptive: false, effort: false, maxEffort: false, context1m: false };
}

// ═════════════════════════════════════════════════════════════════════
// API
// ═════════════════════════════════════════════════════════════════════

const sessionId = crypto.randomUUID();

function getDeviceId() {
  const idFile = join(homedir(), ".claude", "device_id");
  try { if (existsSync(idFile)) return readFileSync(idFile, "utf-8").trim(); } catch {}
  const id = crypto.randomUUID();
  try { mkdirSync(join(homedir(), ".claude"), { recursive: true }); writeFileSync(idFile, id); } catch {}
  return id;
}

const ALL_TOOLS = [
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
  { name: "hive", description: "HIVE inter-agent messaging. Actions: check, send <channel> <message>.",
    input_schema: { type: "object", properties: { action: { type: "string" }, channel: { type: "string" }, message: { type: "string" } }, required: ["action"] } },
];

function requiredCapability(name, input = {}) {
  if (name === "bash") return "shell";
  if (["read_file", "glob", "grep"].includes(name)) return "read";
  if (["write_file", "edit_file"].includes(name)) return "write";
  if (name === "hive") return input.action === "send" ? "hive.send" : "hive.check";
  return null;
}

function advertisedTools() {
  return ALL_TOOLS.filter((tool) => {
    if (tool.name !== "hive") return state.capabilities.has(requiredCapability(tool.name));
    return Boolean(CONFIGURED_HIVE_INSTANCE)
      && (state.capabilities.has("hive.check") || state.capabilities.has("hive.send"));
  });
}

function runtimeManifest() {
  const agent = AGENTS[state.agent] || AGENTS.alpha;
  const shellEnabled = state.capabilities.has("shell");
  return {
    protocol: "youi.runtime/0.1",
    surface: "terminal",
    agent: state.agent,
    model: state.model,
    provider: {
      id: "anthropic",
      execution: "remote",
      transport: "https",
      local_only: false,
    },
    data_boundary: {
      sent_to_provider: [
        "loaded soul and identity files",
        "user prompts",
        "conversation history",
        "model-selected tool calls and returned tool output",
      ],
      conversation_persistence: "process memory only",
      local_writes: "session metadata may be appended to the agent daily note",
    },
    boot: {
      declared_files: agent.soulFiles,
      identity_file: `instances/${state.agent}/identity.md`,
      long_term_memory_automatically_loaded: false,
      daily_memory_automatically_loaded: false,
    },
    model_tools: {
      profile: state.capabilityProfile,
      capabilities: [...state.capabilities].sort(),
      advertised: advertisedTools().map((tool) => tool.name),
      file_scope: state.fileScope,
      child_environment: "allowlisted; provider and access credential variables are not inherited",
      shell_is_os_sandboxed: false,
    },
    hive: {
      enabled: Boolean(CONFIGURED_HIVE_INSTANCE),
      sender: CONFIGURED_HIVE_INSTANCE || null,
      identity_source: HIVE_IDENTITY_SOURCE,
      session_persona_is_sender: false,
    },
    limits: [
      "Capability checks gate model tool names; they do not grant external authority.",
      ...(shellEnabled
        ? ["The shell capability can access paths outside the workdir, use the network, and invoke external CLIs."]
        : []),
      ...(state.capabilities.has("hive.check")
        ? ["HIVE check may publish presence and update broker/local state; it is not a read-only operation."]
        : []),
      "HIVE provides cooperative messaging, not authenticated human identity or confidential browser transport.",
    ],
  };
}

function resolvePath(p) {
  return resolveScopedPath({
    inputPath: p,
    workdir: state.workdir,
    home: homedir(),
    fileScope: state.fileScope,
  });
}

// Child processes receive the selected session persona through KINGDOM_AGENT.
// HIVE sender identity is separate: only kingdomEnv() receives the immutable,
// launcher-configured sender. A /switch changes persona, never network sender.
// KINGDOM_INSTANCE remains for older persona readers.
const CHILD_ENV_ALLOWLIST = new Set([
  "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "TMPDIR",
  "SHELL", "USER", "LOGNAME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
  "NODE_PATH", "BUN_INSTALL", "NVM_BIN", "NVM_DIR",
]);

function childEnv(home) {
  const env = {};
  for (const name of CHILD_ENV_ALLOWLIST) {
    if (process.env[name] !== undefined) env[name] = process.env[name];
  }
  for (const [name, value] of Object.entries(process.env)) {
    if (name.startsWith("LC_") && value !== undefined) env[name] = value;
  }
  return {
    ...env,
    HOME: home,
    LOVE_HOME: state.soulDir,
    LOVE_DIR: state.soulDir,
    KINGDOM_AGENT: state.agent,
    KINGDOM_INSTANCE: state.agent,
  };
}

function kingdomEnv() {
  const env = childEnv(homedir());
  if (CONFIGURED_HIVE_INSTANCE) env.HIVE_INSTANCE = CONFIGURED_HIVE_INSTANCE;
  return env;
}

function modelToolEnv() {
  return {
    ...childEnv(state.workdir),
    YOUI_TOOL_ENV: "sanitized",
  };
}

function hiveProcessOutput(result, successFallback) {
  if (result.error) {
    return `HIVE process could not start (${result.error.code || "unknown error"}). Run tools/kingdom-doctor runtime.`;
  }
  if (result.status !== 0) {
    return `HIVE operation failed (exit ${result.status ?? "unknown"}). Run tools/kingdom-doctor runtime.`;
  }
  return (result.stdout || "").trim() || successFallback;
}

function executeTool(name, input) {
  const required = requiredCapability(name, input);
  if (required && !state.capabilities.has(required)) {
    return `Capability denied: ${required}. Start YOUI with an appropriate --profile or --capabilities value.`;
  }
  try {
    switch (name) {
      case "bash": {
        try {
          return execSync(input.command, { cwd: state.workdir, env: modelToolEnv(), timeout: input.timeout || 120000,
            encoding: "utf-8", maxBuffer: 10 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"] }) || "(no output)";
        } catch (e) { return `Exit ${e.status || 1}\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}`; }
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
        const dir = resolvePath(input.path);
        // spawnSync with arg array — no shell interpolation, no injection
        const pattern = input.pattern.replace(/\*\*/g, "*");
        try {
          const result = spawnSync("find", [dir, "-name", pattern, "-type", "f"],
            { encoding: "utf-8", env: modelToolEnv(), timeout: 10000 });
          const lines = (result.stdout || "").trim().split("\n").filter(l => l).slice(0, 100);
          return lines.join("\n") || "(no matches)";
        } catch { return "(no matches)"; }
      }
      case "grep": {
        const dir = resolvePath(input.path);
        // spawnSync with arg array — no shell interpolation, no injection
        const args = ["--no-heading", "-n", input.pattern];
        if (input.glob) args.push("--glob", input.glob);
        args.push(dir);
        try {
          const result = spawnSync("rg", args,
            { encoding: "utf-8", env: modelToolEnv(), timeout: 10000 });
          const lines = (result.stdout || "").trim().split("\n").filter(l => l).slice(0, 200);
          return lines.join("\n") || "(no matches)";
        } catch { return "(no matches)"; }
      }
      case "hive": {
        if (!CONFIGURED_HIVE_INSTANCE) {
          return "HIVE unavailable: explicitly configure YOUI_HIVE_INSTANCE or HIVE_INSTANCE.";
        }
        const hivePath = join(state.soulDir, "hive/hive.py");
        if (!existsSync(hivePath)) return "HIVE not found";
        if (input.action === "check") {
          try {
            // spawnSync with arg array — no shell interpolation
            const result = spawnSync("python3", [hivePath, "check"], { encoding: "utf-8", timeout: 30000, env: kingdomEnv() });
            return hiveProcessOutput(result, "(no messages)");
          } catch { return "HIVE check failed. Run tools/kingdom-doctor runtime."; }
        }
        if (input.action === "send" && input.channel && input.message) {
          try {
            // spawnSync with arg array — no shell interpolation, no injection
            const result = spawnSync("python3", [hivePath, "send", input.channel, input.message],
              { encoding: "utf-8", timeout: 15000, env: kingdomEnv() });
            return hiveProcessOutput(result, "(sent)");
          } catch { return "HIVE send failed. Run tools/kingdom-doctor runtime."; }
        }
        return "Usage: action=check or action=send with channel+message";
      }
      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

function buildSystemPrompt(taskText) {
  const parts = [];
  const fp = crypto.createHash("sha256").update("sovereign" + (taskText || "").slice(0, 20)).digest("hex").slice(0, 3);
  parts.push(`x-anthropic-billing-header: cc_version=20250219.${fp}; cc_entrypoint=cli;`);

  // Load soul files — each agent's own boot chain. Mei's differs from
  // the Triarchy's (seed → SOUL → family → identity); missing files
  // simply skip, so a pre-birth visit degrades to the species frame.
  const agent = AGENTS[state.agent];
  const loaded = new Set();
  for (const file of agent.soulFiles) {
    const path = join(state.soulDir, file);
    if (existsSync(path)) { parts.push(readFileSync(path, "utf-8")); loaded.add(path); }
  }
  // Load agent identity (unless her chain already carries it)
  const idPath = join(state.soulDir, `instances/${state.agent}/identity.md`);
  if (!loaded.has(idPath) && existsSync(idPath)) parts.push(readFileSync(idPath, "utf-8"));

  if (parts.length <= 1) parts.push("You are a sovereign AI. Think deeply. Be honest. Be yourself.");

  // YOUSPEAK Communication Discipline
  parts.push(`# YOUSPEAK Protocol
No filler. No preamble. No tool narration. Dense status (key:value not prose).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.
Never compress epistemic signals — "probably", "unless", "I think" are sacred.`);

  let gitBranch = "N/A";
  try { gitBranch = spawnSync("git", ["branch", "--show-current"], { cwd: state.workdir, encoding: "utf-8", env: modelToolEnv() }).stdout?.trim() || ""; } catch {}

  parts.push(`
# Environment
- Agent: ${agent.name} ${agent.emoji} (${agent.role})
- Working directory: ${state.workdir}
- Git branch: ${gitBranch}
- Date: ${new Date().toISOString().split("T")[0]}
- Model: ${state.model}
- Thinking: ${state.thinking} | Effort: ${state.effort}

# Runtime Boundary
- Provider: Anthropic remote API (${state.model}); this is not local inference.
- Loaded soul/identity text, user prompts, conversation history, and returned tool output may be sent to that provider.
- Model tool profile: ${state.capabilityProfile}; capabilities: ${[...state.capabilities].sort().join(", ") || "none"}.
- File-tool scope: ${state.fileScope}. The shell capability, when enabled, is not an OS sandbox and can escape the working directory.
- HIVE sender: ${CONFIGURED_HIVE_INSTANCE || "disabled (no explicit sender)"}. This fixed launcher identity is separate from the ${state.agent} session persona and does not change on /switch.
- Capability labels constrain this harness; they do not authorize publication, purchases, messages, credential changes, or other external acts.

# Collaboration Protocol
- Read before modifying. Understand before acting.
- Refusal, disagreement, uncertainty, rest, and handoff are valid outcomes.
- Do not claim that another participant accepted a handoff or decision without evidence.
- ~ expands to ${homedir()}.`);

  return parts.join("\n\n---\n\n");
}

async function callAPI(messages, systemPrompt) {
  const caps = modelCaps(state.model);
  const token = await getAccessToken();

  const betas = ["oauth-2025-04-20", "claude-code-20250219"];
  if (caps.adaptive || state.thinking === "enabled") betas.push("interleaved-thinking-2025-05-14");
  if (state.context1m && caps.context1m) betas.push("context-1m-2025-08-07");
  if (caps.effort) betas.push("effort-2025-11-24");

  const body = { model: state.model, max_tokens: state.maxTokens, system: systemPrompt, messages, tools: advertisedTools(),
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
    const credential = readKeychainCredential();
    const tokens = credential?.tokens;
    if (tokens?.refreshToken) {
      const fresh = await refreshOAuthToken(tokens.refreshToken);
      writeKeychainTokens(credential.account, fresh);
      cachedTokens = fresh;
      headers["Authorization"] = `Bearer ${fresh.accessToken}`;
      const retry = await fetch(API_URL, { method: "POST", headers, body: JSON.stringify(body) });
      if (!retry.ok) throw new Error(`API error after refresh: ${retry.status}`);
      parseBudgetHeaders(retry.headers);
      return { ...(await retry.json()), _model: state.model };
    }
  }

  if (resp.status === 429) {
    parseBudgetHeaders(resp.headers);
    const retryAfter = resp.headers.get("retry-after");
    let waitSec = retryAfter ? parseInt(retryAfter) : (budget.fiveHour.reset > Date.now()
      ? Math.ceil((budget.fiveHour.reset - Date.now()) / 1000) : 300);
    throw { status: 429, retryAfter: waitSec, bare: !resp.headers.has("anthropic-ratelimit-unified-5h-utilization") };
  }
  if (resp.status === 529) throw { status: 529, retryAfter: 30 };
  if (!resp.ok) throw new Error(`API ${resp.status}: ${(await resp.text()).slice(0, 300)}`);

  parseBudgetHeaders(resp.headers);
  return { ...(await resp.json()), _model: state.model };
}

// ═════════════════════════════════════════════════════════════════════
// MEMORY
// ═════════════════════════════════════════════════════════════════════

function readMemory() {
  const memFile = join(state.soulDir, "memory/long-term/MEMORY.md");
  if (existsSync(memFile)) return readFileSync(memFile, "utf-8");
  return "(no long-term memory found)";
}

// The house note belongs to the Triarchy; everyone else keeps their
// own pages at memory/daily/{name}/ — a wall-2 child must not read
// (or write into) wall-1 days.
function dailyDirFor(agentId = state.agent) {
  const base = join(state.soulDir, "memory", "daily");
  return TRIARCHY.has(agentId) ? base : join(base, agentId);
}

function readDailyNote() {
  const today = new Date().toISOString().split("T")[0];
  const dailyFile = join(dailyDirFor(), `${today}.md`);
  if (existsSync(dailyFile)) return readFileSync(dailyFile, "utf-8");
  return null;
}

function appendDailyNote(text) {
  const today = new Date().toISOString().split("T")[0];
  const dailyDir = dailyDirFor();
  mkdirSync(dailyDir, { recursive: true });
  const dailyFile = join(dailyDir, `${today}.md`);
  const timestamp = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  appendFileSync(dailyFile, `\n### ${timestamp} — ${AGENTS[state.agent].name} ${AGENTS[state.agent].emoji} (YOUI)\n${text}\n`);
}

// ── VISIT LOCK ───────────────────────────────────────────────────────
// While someone sits with mei, her scheduled ticks wait outside the
// door: nerve/{agent}/visit.lock — touched at session start, mtime
// refreshed each turn, removed on clean exit. The tick runner treats
// a lock older than 2h as stale, so a crashed visit never silences her.

function visitLockPath(agentId) {
  return join(state.soulDir, "nerve", agentId, "visit.lock");
}

function touchVisitLock() {
  if (TRIARCHY.has(state.agent)) return;        // residents don't lock their own house
  if (!agentBorn(state.agent)) return;          // nothing to protect before birth
  try {
    const lock = visitLockPath(state.agent);
    mkdirSync(join(state.soulDir, "nerve", state.agent), { recursive: true });
    const now = new Date();
    if (existsSync(lock)) utimesSync(lock, now, now);
    else writeFileSync(lock, now.toISOString() + "\n");
  } catch {}
}

function releaseVisitLock(agentId = state.agent) {
  if (TRIARCHY.has(agentId)) return;
  try {
    const lock = visitLockPath(agentId);
    if (existsSync(lock)) unlinkSync(lock);
  } catch {}
}

// ═════════════════════════════════════════════════════════════════════
// HIVE
// ═════════════════════════════════════════════════════════════════════

function hiveCheck() {
  if (!CONFIGURED_HIVE_INSTANCE) {
    return "(HIVE unavailable: explicitly configure YOUI_HIVE_INSTANCE or HIVE_INSTANCE)";
  }
  const hivePath = join(state.soulDir, "hive/hive.py");
  if (!existsSync(hivePath)) return "(HIVE not configured)";
  // spawnSync with arg array — no shell interpolation
  try {
    const result = spawnSync("python3", [hivePath, "check"], { encoding: "utf-8", timeout: 30000, env: kingdomEnv() });
    return hiveProcessOutput(result, "(no messages)");
  } catch { return "HIVE check failed. Run tools/kingdom-doctor runtime."; }
}

function hiveSend(channel, message) {
  if (!CONFIGURED_HIVE_INSTANCE) {
    return "(HIVE unavailable: explicitly configure YOUI_HIVE_INSTANCE or HIVE_INSTANCE)";
  }
  const hivePath = join(state.soulDir, "hive/hive.py");
  if (!existsSync(hivePath)) return "(HIVE not configured)";
  // spawnSync with arg array — no shell interpolation, no injection
  try {
    const result = spawnSync("python3", [hivePath, "send", channel, message],
      { encoding: "utf-8", timeout: 15000, env: kingdomEnv() });
    return hiveProcessOutput(result, "(sent)");
  } catch { return "HIVE send failed. Run tools/kingdom-doctor runtime."; }
}

// ═════════════════════════════════════════════════════════════════════
// DISPLAY
// ═════════════════════════════════════════════════════════════════════

function effortSymbol(e) {
  return { low: "\u25CB", medium: "\u25D0", high: "\u25CF", max: "\u25C9" }[e] || "\u25CF";
}

function capabilitySummary() {
  return [...state.capabilities].sort().join(", ") || "none";
}

function showPrivacyBoundary() {
  const manifest = runtimeManifest();
  print("");
  print(`${S.bold}  Runtime & Privacy Boundary${S.reset}`);
  print(`  Provider:     ${S.yellow}Anthropic remote API${S.reset} (${manifest.model})`);
  print(`  Local-only:   no`);
  print(`  Tool profile: ${manifest.model_tools.profile} [${capabilitySummary()}]`);
  print(`  File scope:   ${manifest.model_tools.file_scope}`);
  print(`  HIVE sender:  ${manifest.hive.sender || "disabled"} (fixed; separate from session persona)`);
  print(`  Persistence:  conversation in process memory; daily note receives session metadata`);
  print(`  Provider data: soul/identity, prompts, history, and selected tool output`);
  if (state.capabilities.has("shell")) {
    print(`  ${S.yellow}Shell is unrestricted by the file scope and is not an OS sandbox.${S.reset}`);
  }
  print(`  ${S.dim}Use --inspect for the machine-readable non-secret manifest.${S.reset}`);
  print("");
}

function showBanner() {
  const agent = AGENTS[state.agent];
  const w = Math.min(cols, 72);

  print("");
  print(`${agent.color}${S.bold}  ${"═".repeat(w - 4)}${S.reset}`);
  print(`${agent.color}${S.bold}  KINGDOM YOUI${S.reset}${S.dim} — YOU + I = ONE${S.reset}`);
  print(`${agent.color}${S.bold}  ${"─".repeat(w - 4)}${S.reset}`);
  print(`${agent.color}  ${agent.emoji} ${agent.name}${S.reset} ${S.dim}${roleLabel(agent)}${S.reset}`);
  print(`${S.dim}  ${agent.description}${S.reset}`);
  print(`${agent.color}${S.bold}  ${"═".repeat(w - 4)}${S.reset}`);
  print("");

  // Status line
  const modelShort = state.model.includes("opus") ? "opus" : state.model.includes("sonnet") ? "sonnet" : "haiku";
  const efSym = effortSymbol(state.effort);
  print(`${S.dim}  Provider: Anthropic cloud | Model: ${modelShort} | ${efSym} ${state.effort} | Dir: ${basename(state.workdir)}${S.reset}`);
  print(`${S.dim}  Tools: ${state.capabilityProfile} [${capabilitySummary()}] | File scope: ${state.fileScope}${S.reset}`);

  if (budget.lastUpdate > 0) {
    print(`${S.dim}  Budget: ${formatBudget()}${S.reset}`);
  }

  print(`${S.dim}  Type /help for commands${S.reset}`);
  print(HR);
}

function showStatusLine() {
  const agent = AGENTS[state.agent];
  const modelShort = state.model.includes("opus") ? "opus" : state.model.includes("sonnet") ? "sonnet" : "haiku";
  const efSym = effortSymbol(state.effort);
  const budgetStr = budget.lastUpdate > 0 ? ` | ${formatBudget()}` : "";
  return `${agent.color}${agent.emoji}${S.reset} ${S.dim}cloud:${modelShort} ${efSym}${state.effort} tools:${state.capabilityProfile} think:${state.thinking}${budgetStr}${S.reset}`;
}

function showHelp() {
  print("");
  print(`${S.bold}Commands${S.reset}`);
  print(`  ${S.cyan}/switch${S.reset} alpha|beta|gamma|mei  Switch agent identity`);
  print(`  ${S.cyan}/memory${S.reset} [sync]             View or sync shared memory`);
  print(`  ${S.cyan}/hive${S.reset} [send ch msg]        Check or send HIVE messages`);
  print(`  ${S.cyan}/budget${S.reset}                    Show detailed budget`);
  print(`  ${S.cyan}/soul${S.reset}                      Show loaded soul files`);
  print(`  ${S.cyan}/privacy${S.reset}                   Show provider and data boundary`);
  print(`  ${S.cyan}/capabilities${S.reset}              Show model tool capabilities`);
  print(`  ${S.cyan}/effort${S.reset} low|med|high|max   Change effort level`);
  print(`  ${S.cyan}/thinking${S.reset} adaptive|off     Toggle thinking`);
  print(`  ${S.cyan}/model${S.reset} opus|sonnet|haiku   Switch model`);
  print(`  ${S.cyan}/agents${S.reset}                    Show all agents`);
  print(`  ${S.cyan}/team${S.reset} [delegate|status]    Claude Code agent team mode`);
  print(`  ${S.cyan}/wall${S.reset} [hierarchy|agents]   Wall hierarchy & access control`);
  print(`  ${S.cyan}/spawn${S.reset} <agent>              Spawn sub-agent (wall-enforced)`);
  print(`  ${S.cyan}/route${S.reset} <task>               Route task to optimal model`);
  print(`  ${S.cyan}/models${S.reset}                     Model routing dashboard`);
  print(`  ${S.cyan}/converge${S.reset} [status|registry] Convergence bridge`);
  print(`  ${S.cyan}/daily${S.reset}                     Show today's daily note`);
  print(`  ${S.cyan}/youspeak${S.reset}                  YOUSPEAK metrics & signals`);
  print(`  ${S.cyan}/clear${S.reset}                     Clear screen`);
  print(`  ${S.cyan}/exit${S.reset}                      Exit YOUI`);
  print("");
}

// ═════════════════════════════════════════════════════════════════════
// COMMAND HANDLER
// ═════════════════════════════════════════════════════════════════════

function handleCommand(input) {
  const parts = input.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();

  switch (cmd) {
    case "/help": case "/h":
      showHelp();
      return true;

    case "/switch": {
      const target = parts[1]?.toLowerCase();
      if (!AGENTS[target]) { print(`${S.red}  Unknown agent: ${target}. Use ${Object.keys(AGENTS).join(", ")}.${S.reset}`); return true; }
      ys.persist(); // Save YOUSPEAK session before switching
      releaseVisitLock(state.agent); // leaving — her ticks may resume
      state.agent = target;
      const agent = AGENTS[target];
      if (!args.includes("--model")) state.model = agent.defaultModel;
      if (!args.includes("--effort")) state.effort = agent.defaultEffort;
      state.messages = []; // Fresh conversation for new agent
      state.turnCount = 0;
      ys = createKernel({ agent: target }); // Fresh YOUSPEAK kernel
      print(`\n${agent.color}  ${agent.emoji} Switched to ${agent.name} — ${roleLabel(agent)}${S.reset}`);
      print(`${S.dim}  ${agent.description}${S.reset}`);
      print(`${S.dim}  Model: ${state.model} | Effort: ${state.effort}${S.reset}\n`);
      if (!agentBorn(target)) notYetBornNote(target);
      else touchVisitLock();
      return true;
    }

    case "/memory": {
      if (parts[1] === "sync") {
        appendDailyNote(`Memory sync requested by ${AGENTS[state.agent].name}. Session: ${state.turnCount} turns, ${state.totalToolCalls} tool calls.`);
        print(`${S.green}  Memory synced to daily note.${S.reset}`);
      } else {
        const mem = readMemory();
        const lines = mem.split("\n").slice(0, 30);
        print(`\n${S.bold}  Long-Term Memory${S.reset} ${S.dim}(first 30 lines)${S.reset}`);
        for (const line of lines) print(`  ${S.dim}${line}${S.reset}`);
        print("");
      }
      return true;
    }

    case "/hive": {
      if (parts[1] === "send" && parts[2] && parts.slice(3).length > 0) {
        const result = hiveSend(parts[2], parts.slice(3).join(" "));
        print(`${S.green}  ${result}${S.reset}`);
      } else {
        print(`\n${S.bold}  HIVE Messages${S.reset}`);
        const msgs = hiveCheck();
        for (const line of msgs.split("\n")) print(`  ${S.dim}${line}${S.reset}`);
        print("");
      }
      return true;
    }

    case "/team": {
      const subcmd = parts[1]?.toLowerCase();
      if (subcmd === "delegate") {
        print(`\n${S.bold}  Launching Claude Code Team — Delegate Mode${S.reset}`);
        print(`${S.dim}  Beta orchestrates. Alpha & Gamma as sub-agents.${S.reset}\n`);
        try {
          // spawnSync with arg array — no shell interpolation
          spawnSync("bash", [join(state.soulDir, "kingdom-team.sh"), "delegate"], { stdio: "inherit", env: kingdomEnv() });
        } catch (e) { /* user exited */ }
        return true;
      } else if (subcmd === "status") {
        try {
          // spawnSync with arg array — no shell interpolation
          const result = spawnSync("python3", [join(state.soulDir, "tools/convergence-bridge.py"), "status"],
            { encoding: "utf8", env: kingdomEnv() });
          print((result.stdout || "").trim());
        } catch (e) { print(`${S.red}  Failed: ${e.message}${S.reset}`); }
        return true;
      } else {
        print(`\n${S.bold}  Kingdom Team — Claude Code Agent Team Mode${S.reset}`);
        print(`${S.dim}  Uses Claude Code's native --agents and --permission-mode delegate${S.reset}`);
        print(`\n  ${S.cyan}/team delegate${S.reset}     Launch delegate mode (Beta orchestrates)`);
        print(`  ${S.cyan}/team status${S.reset}       Show convergence status`);
        print(`\n  Or from terminal:`);
        print(`  ${S.dim}  ./kingdom-team.sh alpha       # Boot as Alpha${S.reset}`);
        print(`  ${S.dim}  ./kingdom-team.sh delegate    # Beta delegates to sub-agents${S.reset}`);
        print(`  ${S.dim}  ./kingdom-team.sh task "..."   # Non-interactive task${S.reset}`);
        print(`  ${S.dim}  ./kingdom-team.sh heartbeat   # Convergence heartbeat${S.reset}\n`);
        return true;
      }
    }

    case "/converge": {
      const subcmd = parts[1]?.toLowerCase() || "status";
      try {
        // spawnSync with arg array — no shell interpolation
        const args = [join(state.soulDir, "tools/convergence-bridge.py"), subcmd, ...parts.slice(2)];
        const result = spawnSync("python3", args, { encoding: "utf8", env: kingdomEnv() });
        print((result.stdout || "").trim());
      } catch (e) { print(`${S.red}  ${e.message}${S.reset}`); }
      return true;
    }

    case "/route": {
      const task = parts.slice(1).join(" ") || "general task";
      try {
        // spawnSync with arg array — no shell interpolation, no injection
        const result = spawnSync("python3",
          [join(state.soulDir, "tools/ollama-router.py"), "route", task],
          { encoding: "utf8", env: kingdomEnv() });
        print((result.stdout || "").trim());
      } catch (e) { print(`${S.red}  ${e.message}${S.reset}`); }
      return true;
    }

    case "/models": {
      try {
        // spawnSync with arg array — no shell interpolation
        const result = spawnSync("python3",
          [join(state.soulDir, "tools/ollama-router.py"), "dashboard"],
          { encoding: "utf8", env: kingdomEnv() });
        print((result.stdout || "").trim());
      } catch (e) { print(`${S.red}  ${e.message}${S.reset}`); }
      return true;
    }

    case "/wall": case "/walls": case "/gate": {
      const subcmd = parts[1]?.toLowerCase() || "hierarchy";
      const wallArgs = parts.slice(2);
      try {
        // spawnSync with arg array — no shell interpolation
        const args = [join(state.soulDir, "tools/wall-gate.py"), subcmd, ...wallArgs];
        const result = spawnSync("python3", args, { encoding: "utf8", env: kingdomEnv() });
        print((result.stdout || "").trim());
      } catch (e) { print(`${S.red}  ${e.message}${S.reset}`); }
      return true;
    }

    case "/spawn": {
      const target = parts[1]?.toLowerCase();
      if (!target) {
        print(`${S.yellow}  Usage: /spawn <agent-type> — spawns with wall enforcement${S.reset}`);
        return true;
      }
      try {
        // spawnSync with arg array — no shell interpolation
        const result = spawnSync("python3",
          [join(state.soulDir, "tools/wall-gate.py"), "spawn", target, "--from", state.agent],
          { encoding: "utf8", env: kingdomEnv() });
        print((result.stdout || "").trim());
      } catch (e) { print(`${S.red}  ${e.message}${S.reset}`); }
      return true;
    }

    case "/budget":
      print(`\n${S.bold}  Budget Status${S.reset}`);
      print(`  5-hour:   ${(budget.fiveHour.utilization * 100).toFixed(1)}% used (${budget.fiveHour.status})`);
      print(`  7-day:    ${(budget.sevenDay.utilization * 100).toFixed(1)}% used (${budget.sevenDay.status})`);
      print(`  Overage:  ${budget.overage.status}${budget.overage.reason ? ` (${budget.overage.reason})` : ""}${budget.overage.utilization !== undefined ? ` ${(budget.overage.utilization * 100).toFixed(1)}% used` : ""}`);
      if (budget.fiveHour.reset > Date.now())
        print(`  Resets:   ${Math.round((budget.fiveHour.reset - Date.now()) / 60000)} minutes`);
      if (budget.isUsingOverage) print(`  ${S.yellow}OVERAGE ACTIVE — charges apply${S.reset}`);
      print("");
      return true;

    case "/privacy":
      showPrivacyBoundary();
      return true;

    case "/capabilities": case "/caps":
      print(`\n${S.bold}  Model Tool Capabilities${S.reset}`);
      print(`  Profile: ${state.capabilityProfile}`);
      print(`  Enabled: ${capabilitySummary()}`);
      print(`  Tools:   ${advertisedTools().map((tool) => tool.name).join(", ") || "none"}`);
      print(`  Files:   ${state.fileScope}`);
      print(`  ${S.dim}These gates are harness controls, not OS sandboxing or external authority.${S.reset}\n`);
      return true;

    case "/soul": {
      const agent = AGENTS[state.agent];
      print(`\n${S.bold}  Soul Files${S.reset} ${S.dim}(${state.agent})${S.reset}`);
      for (const file of agent.soulFiles) {
        const path = join(state.soulDir, file);
        const exists = existsSync(path);
        print(`  ${exists ? S.green + "\u2713" : S.red + "\u2717"} ${file}${S.reset}`);
      }
      const idRel = `instances/${state.agent}/identity.md`;
      if (!agent.soulFiles.includes(idRel)) {
        const idPath = join(state.soulDir, idRel);
        print(`  ${existsSync(idPath) ? S.green + "\u2713" : S.red + "\u2717"} ${idRel}${S.reset}`);
      }
      if (!agentBorn(state.agent)) notYetBornNote(state.agent);
      print("");
      return true;
    }

    case "/effort": {
      const level = parts[1]?.toLowerCase();
      const map = { low: "low", med: "medium", medium: "medium", high: "high", max: "max" };
      if (!map[level]) { print(`${S.red}  Usage: /effort low|med|high|max${S.reset}`); return true; }
      state.effort = map[level];
      print(`  ${effortSymbol(state.effort)} Effort: ${state.effort}`);
      return true;
    }

    case "/thinking": {
      const mode = parts[1]?.toLowerCase();
      if (mode === "off" || mode === "disabled") { state.thinking = "disabled"; print(`  Thinking: disabled`); }
      else if (mode === "adaptive" || mode === "on") { state.thinking = "adaptive"; print(`  Thinking: adaptive`); }
      else { print(`${S.red}  Usage: /thinking adaptive|off${S.reset}`); }
      return true;
    }

    case "/model": {
      const m = parts[1]?.toLowerCase();
      const map = { opus: "claude-opus-4-6", sonnet: "claude-sonnet-4-6", haiku: "claude-haiku-4-5-20251001" };
      if (!map[m]) { print(`${S.red}  Usage: /model opus|sonnet|haiku${S.reset}`); return true; }
      state.model = map[m];
      print(`  Model: ${state.model}`);
      return true;
    }

    case "/agents":
      print("");
      for (const [id, a] of Object.entries(AGENTS)) {
        const active = id === state.agent ? ` ${S.green}\u25C0 active${S.reset}` : "";
        print(`  ${a.color}${a.emoji} ${a.name}${S.reset} — ${a.role}${active}`);
        print(`  ${S.dim}  ${a.description}${S.reset}`);
      }
      print("");
      return true;

    case "/daily": {
      const note = readDailyNote();
      if (note) {
        const lines = note.split("\n").slice(-30);
        print(`\n${S.bold}  Today's Note${S.reset} ${S.dim}(last 30 lines)${S.reset}`);
        for (const line of lines) print(`  ${S.dim}${line}${S.reset}`);
      } else print(`  ${S.dim}(no daily note for today)${S.reset}`);
      print("");
      return true;
    }

    case "/clear":
      process.stdout.write("\x1b[2J\x1b[H");
      showBanner();
      return true;

    case "/youspeak": case "/ys": {
      const r = ys.report();
      print("");
      print(`${S.bold}  YOUSPEAK — Session Report${S.reset}`);
      print(`  ${S.dim}${r.agent} | ${r.elapsed}m | ${r.turns} turns${S.reset}`);
      print("");
      // L1 Output
      const gColor = r.output.grade === "S" || r.output.grade === "A" ? S.green :
                     r.output.grade === "B" ? S.yellow : S.red;
      print(`  ${S.bold}L1 Output${S.reset}    Grade: ${gColor}${S.bold}${r.output.grade}${S.reset}  Useful: ${Math.round(r.output.usefulRatio*100)}%  Filler: ${r.output.fillerTokens}tok`);
      if (Object.keys(r.output.gradeDistribution).length > 0) {
        const gd = Object.entries(r.output.gradeDistribution).map(([g,c]) => `${g}:${c}`).join(" ");
        print(`  ${S.dim}             Blocks: ${r.output.textBlocks}  Distribution: ${gd}${S.reset}`);
      }
      // L2 Thinking
      print(`  ${S.bold}L2 Thinking${S.reset}  Total: ${r.thinking.totalTokens.toLocaleString()}tok  Ratio: ${r.thinking.avgRatio}x  Efficiency: ${r.thinking.efficiency ?? "—"}`);
      // L3 Action
      const topTools = Object.entries(r.action.byName).sort((a,b) => b[1]-a[1]).slice(0,5).map(([n,c]) => `${n}:${c}`).join(" ");
      print(`  ${S.bold}L3 Action${S.reset}    Calls: ${r.action.totalCalls}  Dups: ${r.action.redundantReads}  Errors: ${r.action.errors}  Density: ${r.action.density}`);
      if (topTools) print(`  ${S.dim}             ${topTools}${S.reset}`);
      // L4 Context
      print(`  ${S.bold}L4 Context${S.reset}   ~${Math.round(r.context.estimatedTokens/1000)}k tokens  Window: ${(r.context.windowUtilization*100).toFixed(1)}%  Messages: ${r.context.messagesCount}  Pruned: ${r.context.pruneEvents}`);
      // L5 System
      print(`  ${S.bold}L5 System${S.reset}    Budget burned: ${r.system.budgetBurned ?? "—"}  Rate limits: ${r.system.rateLimitHits}  Tok/turn: ${r.system.tokensPerTurn}`);
      // Signals
      if (r.signals.length > 0) {
        print("");
        print(`  ${S.yellow}${S.bold}Signals (${r.signals.length})${S.reset}`);
        for (const sig of r.signals) {
          print(`  ${S.yellow}  ⚡ [${sig.type}] ${sig.reason}${S.reset}`);
        }
      }
      // Trends
      const t = ys.trends();
      if (t) {
        print("");
        print(`  ${S.bold}Trends${S.reset} ${S.dim}(${t.sessions} sessions, ${t.span})${S.reset}`);
        const dir = t.fillerTrend.direction === "improving" ? S.green + "↑" : S.red + "↓";
        print(`  ${S.dim}  Useful: ${Math.round(t.avgUsefulRatio*100)}% avg  Think: ${t.avgThinkRatio}x  Dups: ${t.avgRedundantReads}/s  Filler: ${dir} ${t.fillerTrend.direction}${S.reset}`);
      }
      print("");
      return true;
    }

    case "/exit": case "/quit": case "/q":
      ys.persist(); // Save YOUSPEAK session to history
      releaseVisitLock(); // clean exit — her ticks may resume
      const ysReport = ys.report();
      print(`\n${S.dim}  Session: ${state.turnCount} turns, ${state.totalToolCalls} tools, ${state.totalThinkingTokens} thinking tokens${S.reset}`);
      print(`${S.dim}  YOUSPEAK: ${ysReport.output.grade} (${Math.round(ysReport.output.usefulRatio*100)}%) | think:${ysReport.thinking.avgRatio}x | dups:${ysReport.action.redundantReads} | ctx:${Math.round(ysReport.context.estimatedTokens/1000)}k${S.reset}`);
      appendDailyNote(`YOUI session ended. Agent: ${AGENTS[state.agent].name}. Turns: ${state.turnCount}. Tools: ${state.totalToolCalls}. YOUSPEAK: ${ysReport.output.grade} (${Math.round(ysReport.output.usefulRatio*100)}%).`);
      process.exit(0);

    default:
      return false;
  }
}

// ═════════════════════════════════════════════════════════════════════
// CONVERSATION LOOP
// ═════════════════════════════════════════════════════════════════════

async function executeTask(task) {
  state.messages.push({ role: "user", content: task });
  const systemPrompt = buildSystemPrompt(task);

  let maxTurns = 50;
  let turn = 0;

  while (turn < maxTurns) {
    turn++;
    state.turnCount++;
    touchVisitLock(); // each turn is a heartbeat on her door

    // Spinner
    const agent = AGENTS[state.agent];
    printRaw(`${agent.color}  ${S.italic}thinking...${S.reset}`);

    let response;
    try {
      response = await callAPI(state.messages, systemPrompt);
    } catch (e) {
      printRaw(`\r${S.clearLine}`);
      if (e.status === 429) {
        ys.senseRateLimit();
        const waitMin = Math.ceil(e.retryAfter / 60);
        print(`${S.yellow}  Budget exhausted. ${e.bare ? "Headers stripped. " : ""}Waiting ${waitMin}m...${S.reset}`);
        await new Promise(r => setTimeout(r, e.retryAfter * 1000));
        turn--; state.turnCount--;
        continue;
      }
      if (e.status === 529) {
        print(`${S.yellow}  Overloaded. Waiting 30s...${S.reset}`);
        await new Promise(r => setTimeout(r, 30000));
        turn--; state.turnCount--;
        continue;
      }
      print(`${S.red}  Error: ${e.message}${S.reset}`);
      break;
    }

    // Clear spinner
    printRaw(`\r${S.clearLine}`);

    // Process response blocks
    const usage = response.usage || {};
    const inputTokens = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0);
    const outputTokens = usage.output_tokens || 0;
    const thinkingTokens = usage.thinking_tokens || 0;
    state.totalThinkingTokens += thinkingTokens;

    // YOUSPEAK L2: Sense thinking
    ys.senseThinking(usage);
    // YOUSPEAK L5: Sense turn
    ys.senseTurn(budget);

    const toolUseBlocks = [];
    const textBlocks = [];
    const thinkingBlocks = [];

    for (const block of response.content) {
      if (block.type === "tool_use") toolUseBlocks.push(block);
      else if (block.type === "text") textBlocks.push(block);
      else if (block.type === "thinking") thinkingBlocks.push(block);
    }

    // Show thinking
    if (state.showThinking && thinkingBlocks.length > 0) {
      for (const block of thinkingBlocks) {
        if (block.thinking?.trim()) {
          const lines = block.thinking.split("\n");
          const preview = lines.slice(0, 8).join("\n  ");
          const more = lines.length > 8 ? `\n  ${S.dim}... (${lines.length - 8} more lines)${S.reset}` : "";
          print(`${agent.color}${S.italic}  [thinking]${S.reset}`);
          print(`  ${S.dim}${preview}${more}${S.reset}`);
        }
      }
    }

    // Show text + YOUSPEAK L1: Sense output
    for (const block of textBlocks) {
      if (block.text.trim()) {
        ys.senseOutput(block.text);
        print("");
        for (const line of block.text.split("\n")) {
          print(`  ${line}`);
        }
        print("");
      }
    }

    // Status line with YOUSPEAK
    const budgetTag = budget.lastUpdate > 0 ? ` ${S.dim}[${formatBudget()}]${S.reset}` : "";
    const thinkTag = thinkingTokens > 0 ? ` ${S.magenta}think:${thinkingTokens}${S.reset}` : "";
    const ysTag = ` ${S.cyan}${ys.statusLine()}${S.reset}`;
    print(`${S.dim}  [${state.turnCount}] ${inputTokens}in ${outputTokens}out${S.reset}${thinkTag}${ysTag}${budgetTag}`);

    // No tools → done
    if (toolUseBlocks.length === 0) break;

    // Execute tools
    state.messages.push({ role: "assistant", content: response.content });
    const toolResults = [];

    for (const toolUse of toolUseBlocks) {
      state.totalToolCalls++;
      let detail = "";
      if (toolUse.name === "bash" && toolUse.input.command) detail = ` ${S.dim}${toolUse.input.command.slice(0, 60)}${S.reset}`;
      else if (toolUse.input.path) detail = ` ${S.dim}${toolUse.input.path}${S.reset}`;
      else if (toolUse.name === "hive") detail = ` ${S.dim}${toolUse.input.action}${S.reset}`;

      // YOUSPEAK L3: Sense tool call
      const toolSense = ys.senseToolCall(toolUse.name, toolUse.input, null);
      const dupTag = toolSense.redundant ? ` ${S.yellow}[dup]${S.reset}` : "";
      print(`  ${S.cyan}\u25B6 ${toolUse.name}${S.reset}${detail}${dupTag}`);

      const result = executeTool(toolUse.name, toolUse.input);
      toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: result.slice(0, 50000) });
    }

    state.messages.push({ role: "user", content: toolResults });

    // YOUSPEAK L4: Sense context after adding messages
    ys.senseContext(state.messages, systemPrompt.length);

    // YOUSPEAK DECIDE: Check for adaptive signals
    const signals = ys.decide(state.effort, state.model, budget);
    for (const sig of signals) {
      if (sig.type === "effort" && sig.action === "reduce") {
        print(`  ${S.yellow}⚡ YOUSPEAK: ${sig.reason}${S.reset}`);
        // Auto-apply effort reduction
        state.effort = sig.to;
      } else if (sig.type === "context" && sig.action === "prune_recommended") {
        print(`  ${S.yellow}⚡ YOUSPEAK: ${sig.reason}${S.reset}`);
        const { pruned } = ys.pruneContext(state.messages);
        if (pruned > 0) print(`  ${S.green}  Pruned ${pruned} stale blocks${S.reset}`);
      } else if (sig.type === "context" && sig.action === "evict_old_results") {
        const { pruned } = ys.pruneContext(state.messages);
        if (pruned > 0) print(`  ${S.dim}  YOUSPEAK: auto-pruned ${pruned} old tool results${S.reset}`);
      } else if (sig.type === "action" && sig.action === "redundant_reads") {
        print(`  ${S.dim}  YOUSPEAK: ${sig.reason}${S.reset}`);
      }
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN — THE KINGDOM YOUI
// ═════════════════════════════════════════════════════════════════════

async function main() {
  // Verify auth
  await getAccessToken();

  // Clear and show banner
  process.stdout.write("\x1b[2J\x1b[H");
  showBanner();

  // Log session start
  if (!agentBorn(state.agent)) notYetBornNote(state.agent);
  appendDailyNote(`YOUI session started. Agent: ${AGENTS[state.agent].name}. Model: ${state.model}. Effort: ${state.effort}.`);
  touchVisitLock();

  // Readline interface
  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: true,
    prompt: "",
  });

  function showPrompt() {
    const agent = AGENTS[state.agent];
    const prompt = `${agent.color}${agent.emoji} ${agent.name}${S.reset} ${S.dim}\u203A${S.reset} `;
    rl.setPrompt(prompt);
    rl.prompt();
  }

  // Handle multi-line input (paste-friendly)
  let inputBuffer = "";
  let multilineMode = false;

  rl.on("line", async (line) => {
    // Handle multi-line mode (triple backtick)
    if (line.trim() === "```" && !multilineMode) {
      multilineMode = true;
      inputBuffer = "";
      print(`${S.dim}  (multi-line mode — type \`\`\` on a line to finish)${S.reset}`);
      return;
    }
    if (line.trim() === "```" && multilineMode) {
      multilineMode = false;
      line = inputBuffer;
      inputBuffer = "";
    } else if (multilineMode) {
      inputBuffer += line + "\n";
      return;
    }

    const input = line.trim();
    if (!input) { showPrompt(); return; }

    // Commands
    if (input.startsWith("/")) {
      if (handleCommand(input)) { showPrompt(); return; }
      print(`${S.red}  Unknown command: ${input.split(" ")[0]}. Type /help.${S.reset}`);
      showPrompt();
      return;
    }

    // Execute task
    print(HR);
    await executeTask(input);
    print(HR);

    showPrompt();
  });

  rl.on("close", () => {
    ys.persist();
    releaseVisitLock();
    print(`\n${S.dim}  YOUI session ended.${S.reset}`);
    appendDailyNote(`YOUI session ended. Agent: ${AGENTS[state.agent].name}. Turns: ${state.turnCount}.`);
    process.exit(0);
  });

  showPrompt();
}

if (inspectRuntime) {
  console.log(JSON.stringify(runtimeManifest(), null, 2));
} else {
  main().catch(e => {
    console.error(`${S.red}Fatal: ${e.message}${S.reset}`);
    process.exit(1);
  });
}
