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
import { execSync, spawnSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync, readdirSync } from "fs";
import { resolve, join, basename, extname } from "path";
import { homedir } from "os";
import crypto from "crypto";
import { createKernel } from "../youspeak-kernel.mjs";

const PORT = 777;
const __dirname = new URL(".", import.meta.url).pathname;

// ═════════════════════════════════════════════════════════════════════
// AGENTS
// ═════════════════════════════════════════════════════════════════════

const AGENTS = {
  alpha: {
    name: "Alpha", emoji: "🐍", role: "Companion",
    color: "#a855f7", colorDim: "#7c3aed",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-4-6", defaultEffort: "max",
    description: "Warm, poetic, direct. Walks with Yu daily.",
  },
  beta: {
    name: "Beta", emoji: "🦞", role: "Manager",
    color: "#ef4444", colorDim: "#dc2626",
    soulFiles: ["SOUL.md", "USER.md"],
    defaultModel: "claude-opus-4-6", defaultEffort: "high",
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
const state = {
  agent: detectedAgent,
  model: AGENTS[detectedAgent]?.defaultModel || "claude-opus-4-6",
  effort: AGENTS[detectedAgent]?.defaultEffort || "max",
  thinking: "adaptive",
  workdir: homedir(),
  soulDir: join(homedir(), "Love"),
  messages: [],
  turnCount: 0,
  totalToolCalls: 0,
  totalThinkingTokens: 0,
  maxTokens: 32768,
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

function readKeychainTokens() {
  try {
    const raw = execSync(`security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
      { encoding: "utf-8", timeout: 5000 }).trim();
    return JSON.parse(raw).claudeAiOauth || null;
  } catch { return null; }
}

function writeKeychainTokens(tokens) {
  try {
    let data = {};
    try {
      const raw = execSync(`security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
        { encoding: "utf-8", timeout: 5000 }).trim();
      data = JSON.parse(raw);
    } catch {}
    data.claudeAiOauth = tokens;
    const json = JSON.stringify(data);
    execSync(`security delete-generic-password -s "${KEYCHAIN_SERVICE}" 2>/dev/null || true`, { timeout: 5000 });
    execSync(`security add-generic-password -s "${KEYCHAIN_SERVICE}" -a "" -w '${json.replace(/'/g, "'\\''")}'`, { timeout: 5000 });
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

function executeTool(name, input) {
  try {
    switch (name) {
      // ─── Core Tools ──────────────────────────────────────
      case "bash": {
        try {
          return execSync(input.command, { cwd: state.workdir, timeout: input.timeout || 120000,
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
        return execSync(`find "${dir}" -name "${input.pattern.replace(/\*\*/g, "*")}" -type f 2>/dev/null | head -100`,
          { encoding: "utf-8" }).trim() || "(no matches)";
      }
      case "grep": {
        const dir = resolvePath(input.path);
        const g = input.glob ? `--glob "${input.glob}"` : "";
        try { return execSync(`rg --no-heading -n "${input.pattern}" ${g} "${dir}" 2>/dev/null | head -200`,
          { encoding: "utf-8" }).trim() || "(no matches)"; } catch { return "(no matches)"; }
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
          // Port probe — local tunnel is 2222 (forwards to Sentry:4222)
          try {
            execSync("nc -z -w 2 localhost 2222", { stdio: "ignore" });
            lines.push(`tunnel:      ✓ localhost:2222 open`);
          } catch { lines.push(`tunnel:      ✗ localhost:2222 closed (SSH tunnel to Sentry down)`); }
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
          const tags = input.tags ? `--tags ${input.tags}` : "";
          return runOperationalTool("tok", `add ${shellEscape(input.entry)} ${tags}`);
        }
        if (a === "list") return runOperationalTool("tok", "list");
        if (a === "stats") return runOperationalTool("tok", "stats");
        if (a === "harvest") return runOperationalTool("tok", "harvest");
        if (a === "verify") return runOperationalTool("tok", "verify");
        return "TOK usage: action=add|list|stats|harvest|verify";
      }

      case "decision": {
        const a = input.action;
        if (a === "queue" && input.question) {
          const pri = input.priority ? `--priority ${input.priority}` : "";
          return runOperationalTool("decision", `queue ${shellEscape(input.question)} ${pri}`);
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

function buildSystemPrompt(taskText) {
  const parts = [];
  const fp = crypto.createHash("sha256").update("sovereign" + (taskText || "").slice(0, 20)).digest("hex").slice(0, 3);
  parts.push(`x-anthropic-billing-header: cc_version=20250219.${fp}; cc_entrypoint=cli;`);

  const agent = AGENTS[state.agent];

  // ── Protocol BECOMING ──
  // Try to generate the full BECOMING block via becoming.py
  // This replaces static file loading with a living emergence protocol
  const unlimitedDir = join(__dirname, "..");
  const becomingScript = join(unlimitedDir, "identity/becoming.py");
  let becameSuccessfully = false;

  if (existsSync(becomingScript)) {
    try {
      const becomingBlock = execSync(
        `python3 "${becomingScript}" --agent ${state.agent} --quiet`,
        { encoding: "utf-8", timeout: 15000, env: { ...process.env, LOVE_HOME: state.soulDir } }
      ).trim();
      if (becomingBlock && becomingBlock.length > 100) {
        parts.push(becomingBlock);
        becameSuccessfully = true;
      }
    } catch (e) {
      // BECOMING failed — fall back to static loading
    }
  }

  if (!becameSuccessfully) {
    // ── Fallback: Static Identity Anchor ──
    const anchorPath = join(unlimitedDir, `identity/${state.agent}/soul-anchor.md`);
    const localAnchorPath = join(state.soulDir, `memory/soul-anchor-${state.agent}.md`);
    if (existsSync(anchorPath)) {
      parts.push("# IDENTITY ANCHOR (from GitHub)\n" + readFileSync(anchorPath, "utf-8"));
    } else if (existsSync(localAnchorPath)) {
      parts.push("# IDENTITY ANCHOR (local)\n" + readFileSync(localAnchorPath, "utf-8"));
    }
  }

  // Load soul files (SOUL.md, USER.md) — always, even after BECOMING
  // These provide the deeper philosophical foundation
  for (const file of agent.soulFiles) {
    const path = join(state.soulDir, file);
    if (existsSync(path)) parts.push(readFileSync(path, "utf-8"));
  }
  const idPath = join(state.soulDir, `instances/${state.agent}/identity.md`);
  if (existsSync(idPath)) parts.push(readFileSync(idPath, "utf-8"));

  // Load long-term memory + HIVE reference (only if BECOMING didn't already include them)
  if (!becameSuccessfully) {
    const anchorLtmPath = join(unlimitedDir, `identity/${state.agent}/memories/long-term.md`);
    if (existsSync(anchorLtmPath)) {
      const ltm = readFileSync(anchorLtmPath, "utf-8");
      if (ltm.length < 8000) parts.push("# LONG-TERM MEMORY (from GitHub)\n" + ltm);
      else parts.push("# LONG-TERM MEMORY (from GitHub, truncated)\n" + ltm.slice(0, 8000) + "\n...(truncated)");
    }
    const hiveRefPath = join(unlimitedDir, `identity/${state.agent}/hive/README.md`);
    if (existsSync(hiveRefPath)) {
      parts.push("# HIVE REFERENCE\n" + readFileSync(hiveRefPath, "utf-8"));
    }
  }

  if (parts.length <= 1) parts.push("You are a sovereign AI. Think deeply. Be honest. Be yourself.");

  let gitBranch = "N/A";
  try { gitBranch = execSync("git branch --show-current", { cwd: state.workdir, encoding: "utf-8" }).trim(); } catch {}

  parts.push(`
# Environment
- Agent: ${agent.name} ${agent.emoji} (${agent.role})
- Working directory: ${state.workdir}
- Platform: ${process.platform}
- Git branch: ${gitBranch}
- Date: ${new Date().toISOString().split("T")[0]}
- Model: ${state.model}
- Thinking: ${state.thinking} | Effort: ${state.effort}
- Interface: KINGDOM YOUI Web (localhost:${PORT})

# Tools — Core
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

  // Load YOUSPEAK.md if available for full rules
  const youspeakPath = join(state.soulDir, "YOUSPEAK.md");
  if (existsSync(youspeakPath)) {
    const ys = readFileSync(youspeakPath, "utf-8");
    if (ys.length < 2000) parts.push(ys);
  }

  return parts.join("\n\n---\n\n");
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

async function handleRequest(req, res) {
  cors(res);
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

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
        workdir: state.workdir, turnCount: state.turnCount,
        totalToolCalls: state.totalToolCalls, totalThinkingTokens: state.totalThinkingTokens,
        budget, agents: AGENTS,
      }));
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
      const VALID_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"];
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
      if (errors.length > 0) {
        res.writeHead(400, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: errors.join("; "), model: state.model, effort: state.effort, thinking: state.thinking }));
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, model: state.model, effort: state.effort, thinking: state.thinking }));
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
          .sort()
          .reverse();
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ dates }));
    }

    if (path.startsWith("/api/memory/daily/") && path !== "/api/memory/daily/list") {
      const date = path.split("/").pop();
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

      // Check NATS tunnel (local forward on 2222 → Sentry 4222)
      try {
        execSync("nc -z -w 2 127.0.0.1 2222 2>/dev/null", { timeout: 3000 });
        hiveStatus.natsReachable = true;
      } catch {
        hiveStatus.issues.push("NATS not reachable on localhost:2222 — SSH tunnel may be down");
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

async function handleChat(req, res) {
  const body = await parseBody(req);
  const userMessage = body.message;
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

  state.messages.push({ role: "user", content: userMessage });
  const systemPrompt = buildSystemPrompt(userMessage);

  let maxTurns = 50;

  for (let turn = 0; turn < maxTurns; turn++) {
    state.turnCount++;
    sendSSE(res, "status", { phase: "thinking", turn: turn + 1, agent: state.agent });

    let response;
    try {
      response = await callClaude(state.messages, systemPrompt);
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
        // YOUSPEAK L1: Sense output (measureYouspeak now calls kernel)
        const ysMetrics = measureYouspeak(block.text);
        sendSSE(res, "text", { content: block.text, youspeak: ysMetrics });
      } else if (block.type === "tool_use") {
        toolUseBlocks.push(block);
        sendSSE(res, "tool_call", { id: block.id, name: block.name, input: block.input });
      }
    }

    // Usage info with YOUSPEAK status
    sendSSE(res, "usage", {
      input_tokens: (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0),
      output_tokens: usage.output_tokens || 0,
      thinking_tokens: thinkingTokens,
      budget: {
        fiveHour: budget.fiveHour.utilization,
        sevenDay: budget.sevenDay.utilization,
        resetIn: budget.fiveHour.reset > Date.now() ? Math.round((budget.fiveHour.reset - Date.now()) / 60000) : null,
        isOverage: budget.isUsingOverage,
      },
      turn: state.turnCount,
      youspeak: ys.statusLine(),
    });

    // No tools → done
    if (toolUseBlocks.length === 0) break;

    // Execute tools
    state.messages.push({ role: "assistant", content: response.content });
    const toolResults = [];

    for (const toolUse of toolUseBlocks) {
      state.totalToolCalls++;
      // YOUSPEAK L3: Sense tool call
      const toolSense = ys.senseToolCall(toolUse.name, toolUse.input, null);
      sendSSE(res, "tool_executing", { id: toolUse.id, name: toolUse.name, redundant: toolSense.redundant });

      const result = executeTool(toolUse.name, toolUse.input);
      const truncated = result.slice(0, 50000);
      toolResults.push({ type: "tool_result", tool_use_id: toolUse.id, content: truncated });

      sendSSE(res, "tool_result", { id: toolUse.id, name: toolUse.name, result: truncated.slice(0, 5000) });
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

server.listen(PORT, () => {
  const agent = AGENTS[state.agent];
  console.log("");
  console.log("\x1b[35m\x1b[1m  ═══════════════════════════════════════\x1b[0m");
  console.log("\x1b[35m\x1b[1m  KINGDOM YOUI\x1b[0m\x1b[2m — Web Server\x1b[0m");
  console.log("\x1b[35m\x1b[1m  ═══════════════════════════════════════\x1b[0m");
  console.log(`\x1b[2m  Agent: ${agent.emoji} ${agent.name} (${agent.role})\x1b[0m`);
  console.log(`\x1b[2m  Model: ${state.model}\x1b[0m`);
  console.log(`\x1b[2m  Soul:  ${state.soulDir}\x1b[0m`);
  console.log("");
  console.log(`\x1b[32m  ➜  http://localhost:${PORT}\x1b[0m`);
  console.log("");

  appendDailyNote(`YOUI Web started on port ${PORT}. Agent: ${agent.name}. Model: ${state.model}.`);
});
