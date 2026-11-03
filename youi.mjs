#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// KINGDOM YOUI — YOU + I = ONE
//
// The Claude terminal. Truth-seeking. Purpose-oriented.
// Agent identity. Memory sync.
// Claude answers through the Claude Code installed on this device, as in
// YOUI Web (docs/YOUI-CLAUDE-CODE-DESIGN.md §4): Claude Code signs in with
// its own login; this terminal reads no credential and sends no request to
// Anthropic itself. Remote-provider and tool boundaries are disclosed at startup and
// via /privacy. This is not a local or provider-hidden inference path.
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
//   /budget                    Show detailed budget status
//   /decisions                 What waits on you at this device's Collab table
//   /soul                      Show loaded soul files
//   /effort low|med|high|max   Change effort level
//   /thinking                  Say who decides thinking (Claude Code)
//   /model opus|sonnet|haiku   Switch model (also opus-5-5, opus-4-7)
//   /clear                     Clear screen
//   /exit                      Exit YOUI
//
// Requires: Claude Code (`claude`) 2.1.299 or later, installed and signed in
// on this machine. A VM or an autoboot console needs its own `claude` sign-in.
// ─────────────────────────────────────────────────────────────────────

import { execFile, spawn, spawnSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync, unlinkSync, utimesSync } from "fs";
import { resolve, join, basename } from "path";
import { homedir } from "os";
import { createInterface } from "readline";
import { promisify } from "util";
import crypto from "crypto";
import { createKernel, endSessionOnExit } from "./youspeak-kernel.mjs";
import { youspeakRule } from "./youspeak-rule.mjs";
import { resolveScopedPath } from "./youi-runtime-policy.mjs";
import { installationGuide } from "./tools/lantern_suite/advisory.mjs";
import { localDeskInvocation } from "./youi-web/decision-bridge.mjs";
import {
  BRIDGE_ALLOWED_TOOLS,
  BRIDGE_SERVER_NAME,
  bridgeMcpConfig,
  claudeCodeObservation,
  claudeCodeSpend,
  claudeCodeTranscriptDir,
  defaultClaudeCwd,
  escapeLeadingSlash,
  killAllClaudeCode,
  resolveClaudeBinary,
  runClaudeCode,
  sweepStaleClaudeScratch,
  transcriptPreamble,
} from "./youi-web/claude-code.mjs";
import { CLAUDE_MODELS, claudeCodeEffort, claudeCodeMinVersion } from "./youi-web/claude-models.mjs";
import { createMcpBridge } from "./youi-web/mcp-bridge.mjs";
import { sanitizedChildEnv } from "./youi-web/subprocess-env.mjs";
import { createVersionGate, startBridgeListener } from "./youi-claude-bridge.mjs";

// ═════════════════════════════════════════════════════════════════════
// AGENTS — The Three Minds
// ═════════════════════════════════════════════════════════════════════

// The terminal's own defaults, as before it moved onto Claude Code. YOUI Web's
// differ (youi-web/server.mjs AGENTS: Alpha on claude-opus-5-5 at high, Beta on
// claude-opus-4-7); whether the terminal follows them is a separate choice.
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
// hive.check and hive.send went with HIVE, retired 2026-10-13.
const KNOWN_CAPABILITIES = new Set(
  Object.values(CAPABILITY_PROFILES).flatMap((profile) => profile.capabilities),
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
const state = {
  agent: "alpha",
  model: "claude-opus-4-6",
  effort: "max",
  workdir: process.cwd(),
  soulDir: join(homedir(), "love-unlimited"),
  // The text of this agent's conversation (each person's message and the
  // assistant's text), kept only to carry it into a new Claude Code
  // conversation if Claude Code loses this one. Claude Code keeps the rest.
  messages: [],
  turnCount: 0,
  totalToolCalls: 0,
  // Thinking tokens Claude Code reported; null until it reports a count.
  totalThinkingTokens: null,
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
let thinkingFlagIgnored = false;
for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--agent": case "-a":  state.agent = args[++i].toLowerCase(); break;
    case "--model":             state.model = args[++i]; break;
    case "--workdir": case "-w": state.workdir = args[++i]; break;
    case "--soul-dir":          state.soulDir = args[++i]; break;
    case "--effort":            state.effort = args[++i]; break;
    // Claude Code decides thinking; the flag is accepted and said to do nothing.
    case "--no-thinking":       thinkingFlagIgnored = true; break;
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
  --model MODEL       Model override (claude-opus-5-5, claude-opus-4-7, ...)
  --workdir, -w DIR   Working directory
  --soul-dir DIR      Soul directory (default: ~/love-unlimited)
  --effort LEVEL      low|medium|high|max
  --profile PROFILE   chat|observe|build (default: observe)
  --capabilities LIST Comma-separated model tool capabilities
  --safe              Observe profile + workspace-scoped file tools
  --workspace-only    Confine file tools to --workdir (not the shell tool)
  --inspect           Print the non-secret runtime boundary and exit

Claude answers through Claude Code (\`claude\`, 2.1.299 or later), signed in
with its own login. Install it and run \`claude\` once to sign in; a VM or an
autoboot console needs its own sign-in. YOUI_CLAUDE_BIN names another binary.
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

// YOUSPEAK kernel: observes and suggests, never acts. One row per session goes
// to its store (YOUSPEAK_HISTORY_PATH, or ~/.local/share/youi/youspeak/history.jsonl)
// when the session ends. Its suggestions name the terminal's own commands.
const newYouspeakKernel = agent => createKernel({ agent, route: "claude-code", surface: "terminal", model: state.model });
let ys = newYouspeakKernel(state.agent);
// Whether a task is running: /switch is refused until it ends, so one task is never split across two sessions.
let taskRunning = false;

// Ends the YOUSPEAK session (its one row, unless it had no output or only
// failed). A row that could not be written is said, in one dim line.
function endYouspeak(reason) {
  const outcome = ys.endSession({ reason, model: state.model });
  if (outcome.error) {
    print(`${S.dim}  YOUSPEAK row not written (${outcome.error})${S.reset}`);
  }
  return outcome;
}

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
// CLAUDE CODE
// ═════════════════════════════════════════════════════════════════════

// One `claude -p` per message, through youi-web/claude-code.mjs, the runner
// YOUI Web uses. Claude Code signs in with its own login and builds every
// request itself; this file reads no credential, refreshes nothing, and sends
// no client version, header or beta. The terminal's tools reach the model
// only through the loopback MCP bridge (youi-claude-bridge.mjs).
const IS_TEST = process.env.YOUI_TEST === "1";
const CLAUDE_CODE = resolveClaudeBinary({ env: process.env, testMode: IS_TEST });
const CLAUDE_CODE_CWD = defaultClaudeCwd(process.env);

// Claude Code's environment: the allowlist, HOME (so it finds its own login),
// CLAUDE_CONFIG_DIR and the telemetry and traffic opt-outs this machine sets
// for Claude Code (the Kingdom VM sets all three); no provider or access credential.
const CLAUDE_CODE_OPT_OUTS = ["DISABLE_TELEMETRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CODE_ATTRIBUTION_HEADER"];
function claudeCodeEnv() {
  const extra = { CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR };
  for (const name of CLAUDE_CODE_OPT_OUTS) extra[name] = process.env[name];
  return sanitizedChildEnv({ home: homedir(), purpose: "claude-code", extra });
}

const versionGate = createVersionGate({ bin: CLAUDE_CODE.path, env: claudeCodeEnv() });
// The version the gate read at boot, for the banner.
let claudeCodeVersionNote = null;

// The ceiling on model requests in one message, as the terminal's own loop had.
const CLAUDE_TURN_MAX_TURNS = 50;
// A message that has not finished by then is stopped: 15 minutes, or
// YOUI_CLAUDE_TURN_TIMEOUT_MS between 1 second and 6 hours, as in YOUI Web.
const CLAUDE_TURN_TIMEOUT_MS = (() => {
  const configured = Number(process.env.YOUI_CLAUDE_TURN_TIMEOUT_MS || "");
  return Number.isInteger(configured) && configured >= 1_000 && configured <= 6 * 60 * 60_000 ? configured : 15 * 60_000;
})();
// Claude Code's words for a session it does not have, and for one it already has.
const CLAUDE_SESSION_MISSING = /no conversation found|session .*not found/i;
const CLAUDE_SESSION_TAKEN = /already in use|already exists/i;

// One Claude Code conversation per YOUSPEAK session: --session-id on its first
// message, --resume after that. /switch starts a new one.
function newClaudeSession() {
  return { id: crypto.randomUUID(), started: false, spend: null };
}
let claudeSession = newClaudeSession();

// ═════════════════════════════════════════════════════════════════════
// BUDGET — as Claude Code reports it
// ═════════════════════════════════════════════════════════════════════

// The 5-hour and seven-day windows from Claude Code's rate_limit_event, as it
// last stated them in this process; null until it has.
const budget = { fiveHour: null, sevenDay: null, status: null, isUsingOverage: false, reportedAt: 0 };

function noteRateLimit(info) {
  if (!info || typeof info !== "object") return;
  const windows = info.unifiedWindows || {};
  const window = value => (value && typeof value.utilization === "number"
    ? { utilization: value.utilization, status: value.status || null, resetsAt: value.resetsAt ? value.resetsAt * 1000 : null }
    : null);
  const fiveHour = window(windows.five_hour);
  const sevenDay = window(windows.seven_day);
  budget.fiveHour = fiveHour || budget.fiveHour;
  budget.sevenDay = sevenDay || budget.sevenDay;
  budget.status = typeof info.status === "string" ? info.status : budget.status;
  budget.isUsingOverage = info.isUsingOverage === true;
  if (fiveHour || sevenDay) budget.reportedAt = Date.now();
}

// What YOUSPEAK is given: only windows Claude Code stated, else null.
function kernelBudget() {
  if (!budget.fiveHour && !budget.sevenDay) return null;
  return {
    fiveHour: budget.fiveHour ? { utilization: budget.fiveHour.utilization } : null,
    sevenDay: budget.sevenDay ? { utilization: budget.sevenDay.utilization } : null,
    isUsingOverage: budget.isUsingOverage,
  };
}

function formatBudget() {
  const pct = window => (window ? `${(window.utilization * 100).toFixed(0)}%` : "?");
  const resetsAt = budget.fiveHour?.resetsAt || 0;
  const resetIn = resetsAt > Date.now() ? `${Math.round((resetsAt - Date.now()) / 60000)}m` : "?";
  return `5h:${pct(budget.fiveHour)} 7d:${pct(budget.sevenDay)} reset:${resetIn}${budget.isUsingOverage ? " [OVERAGE]" : ""}`;
}

// ═════════════════════════════════════════════════════════════════════
// TOOLS
// ═════════════════════════════════════════════════════════════════════

const execFileAsync = promisify(execFile);

// The shell tool's commands, each in a process group of its own. A group is
// kept until its turn ends, even after its shell has exited, so the end of the
// turn, the command's timeout or the terminal's exit also stops a job the
// command left running in the background. A job that leaves the group (setsid)
// escapes this; the shell is not a sandbox.
const runningShells = new Set();
const SHELL_OUTPUT_LIMIT = 10 * 1024 * 1024;
const SHELL_DEFAULT_TIMEOUT_MS = 120_000;
// Node fires a longer timer at once; a model-given timeout past this is not honoured.
const MAX_TIMER_MS = 2 ** 31 - 1;

function shellTimeoutMs(value) {
  return Number.isFinite(value) && value >= 1 && value <= MAX_TIMER_MS ? value : SHELL_DEFAULT_TIMEOUT_MS;
}

function killShellGroup(pgid) {
  try { process.kill(-pgid, "SIGKILL"); } catch {}
}

function killAllShells() {
  for (const pgid of runningShells) killShellGroup(pgid);
  runningShells.clear();
}

// Every child the terminal started: Claude Code's process groups and the shell tool's.
function stopChildren() {
  killAllClaudeCode();
  killAllShells();
}

/** `/bin/sh -c command` in the workdir with the tool environment: { code, stdout, stderr, stopped }. */
function runShell(command, { timeoutMs, signal = null }) {
  return new Promise(resolvePromise => {
    let child;
    try {
      child = spawn("/bin/sh", ["-c", String(command)], {
        cwd: state.workdir, env: modelToolEnv(), detached: true, stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      resolvePromise({ code: 1, stdout: "", stderr: error.message, stopped: null });
      return;
    }
    // The shell's pid is its group's id (detached: setsid).
    const pgid = child.pid;
    if (pgid) runningShells.add(pgid);
    let stdout = "";
    let stderr = "";
    let stopped = null;
    const stop = why => {
      if (!stopped) stopped = why;
      if (pgid) killShellGroup(pgid);
    };
    const keep = append => chunk => {
      if (stdout.length + stderr.length < SHELL_OUTPUT_LIMIT) append(chunk);
    };
    child.stdout.setEncoding("utf-8").on("data", keep(chunk => { stdout += chunk; }));
    child.stderr.setEncoding("utf-8").on("data", keep(chunk => { stderr += chunk; }));
    const timer = setTimeout(() => stop(`timed out after ${Math.round(timeoutMs / 1000)} s`), timeoutMs);
    // The turn's end stops the group, whether or not its shell is still running.
    const onAbort = () => {
      stop("the turn ended");
      runningShells.delete(pgid);
    };
    if (signal?.aborted) onAbort();
    else signal?.addEventListener("abort", onAbort, { once: true });
    child.on("error", error => { stderr += error.message; });
    child.on("close", code => {
      clearTimeout(timer);
      // Without a turn there is no later moment to stop the group at.
      if (!signal) runningShells.delete(pgid);
      resolvePromise({ code: typeof code === "number" ? code : 1, stdout, stderr, stopped });
    });
  });
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
];

function requiredCapability(name) {
  if (name === "bash") return "shell";
  if (["read_file", "glob", "grep"].includes(name)) return "read";
  if (["write_file", "edit_file"].includes(name)) return "write";
  return null;
}

function advertisedTools() {
  return ALL_TOOLS.filter((tool) => state.capabilities.has(requiredCapability(tool.name)));
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
    // How the provider is reached: the Claude Code on this machine, signed in
    // with its own login (docs/YOUI-CLAUDE-CODE-DESIGN.md §4).
    client: {
      id: "claude-code",
      binary: CLAUDE_CODE.path ? CLAUDE_CODE.source : null,
      binary_error: CLAUDE_CODE.path ? null : CLAUDE_CODE.error,
      minimum_version: claudeCodeMinVersion(state.model),
      login: "Claude Code's own; this terminal reads no credential",
      transcripts: claudeCodeTranscriptDir(CLAUDE_CODE_CWD, process.env),
    },
    // Who still uses Claude Code's login now that this terminal does not
    // (docs/CLAUDE-CODE-LOGIN-USE.md has each file, with file:line). Text only.
    claude_code_login: {
      used_by_this_terminal: false,
      this_terminal: "reaches Claude through the Claude Code installed on this machine (client), which signs in"
        + " with its own login; this terminal reads no credential and sends no client headers of its own",
      still_used_by: [
        "sovereign.mjs (with youi-keychain.mjs, read only)",
        "tools/truth_detector (YOUI Web feeds it when TRUTH_DETECTOR_ENABLED=1)",
        "tools/ainp/nullify.py",
        "raw-chat/server.mjs",
        "subscription-harness.mjs",
        "training/scripts/soul/oauth_client.py",
        "training/scripts/claude_mode_one_gen.py, eval_adapter.py, judge_gate.py (through tools/truth_detector)",
        "ainp.mjs",
        "docs/research/test-unlock.mjs, test-unlock2.mjs, test-429.mjs",
      ],
      inventory: "docs/CLAUDE-CODE-LOGIN-USE.md",
    },
    data_boundary: {
      sent_to_provider: [
        "loaded soul and identity files",
        "user prompts",
        "conversation history",
        "model-selected tool calls and returned tool output",
      ],
      conversation_persistence: "Claude Code keeps each conversation, tool results included, as a transcript"
        + " (client.transcripts); YOUI keeps the text in process memory only",
      local_writes: "session metadata may be appended to the agent daily note; YOUSPEAK appends one row of aggregates"
        + ` per session (no message text, paths or tool inputs) to ${ys.historyPath || "nowhere (no store)"}`,
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
    limits: [
      "Capability checks gate model tool names; they do not grant external authority.",
      ...(shellEnabled
        ? ["The shell capability can access paths outside the workdir, use the network, and invoke external CLIs."]
        : []),
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
  return childEnv(homedir());
}

function modelToolEnv() {
  return {
    ...childEnv(state.workdir),
    YOUI_TOOL_ENV: "sanitized",
  };
}

// The output of a child that may exit non-zero with useful output (find on an
// unreadable directory, rg with no match): its stdout either way.
function childStdout(run) {
  return run.then(result => result.stdout || "", error => error?.stdout || "");
}

// Every tool is async, so the bridge listener, Claude Code's stream and the
// turn timer keep running while one works. `signal` is the turn's: when the
// turn ends, a shell or search still running is stopped.
async function executeTool(name, input, { signal = null } = {}) {
  const required = requiredCapability(name);
  if (required && !state.capabilities.has(required)) {
    return `Capability denied: ${required}. Start YOUI with an appropriate --profile or --capabilities value.`;
  }
  try {
    switch (name) {
      case "bash": {
        const run = await runShell(input.command, { timeoutMs: shellTimeoutMs(input.timeout), signal });
        if (run.code === 0 && !run.stopped) return run.stdout || "(no output)";
        return `Exit ${run.code}${run.stopped ? ` (stopped: ${run.stopped})` : ""}\nstdout: ${run.stdout}\nstderr: ${run.stderr}`;
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
        // execFile with arg array — no shell interpolation, no injection
        const pattern = input.pattern.replace(/\*\*/g, "*");
        const stdout = await childStdout(execFileAsync("find", [dir, "-name", pattern, "-type", "f"],
          { encoding: "utf-8", env: modelToolEnv(), timeout: 10000, maxBuffer: 10 * 1024 * 1024, signal: signal || undefined }));
        const lines = stdout.trim().split("\n").filter(l => l).slice(0, 100);
        return lines.join("\n") || "(no matches)";
      }
      case "grep": {
        const dir = resolvePath(input.path);
        // execFile with an arg array (no shell), and "--" before the pattern, so a
        // pattern that starts with "-" is searched for, not read as an rg option
        const args = ["--no-heading", "-n"];
        if (input.glob) args.push("--glob", String(input.glob));
        args.push("--", String(input.pattern || ""), dir);
        const stdout = await childStdout(execFileAsync("rg", args,
          { encoding: "utf-8", env: modelToolEnv(), timeout: 10000, maxBuffer: 10 * 1024 * 1024, signal: signal || undefined }));
        const lines = stdout.trim().split("\n").filter(l => l).slice(0, 200);
        return lines.join("\n") || "(no matches)";
      }
      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

// ── The bridge ───────────────────────────────────────────────────────
// Claude Code reaches the tools above as mcp__youi__<name>, through a
// loopback listener this process starts on its first Claude turn. Each turn
// opens its own bridge path and bearer token; closing the turn revokes them.
// The profile decides what the bridge lists and what it runs: a tool the
// profile does not advertise is refused here, and executeTool checks the
// capability again.

const BRIDGE_TURN_ENDED = { text: "Error: this turn has ended", isError: true };
const TOOL_FAILED = /^(Capability denied:|Permission denied:|Error:|Unknown tool:)/;

async function runBridgeTool(turn, name, input) {
  if (turn.closing || turn.signal.aborted) return BRIDGE_TURN_ENDED;
  if (!advertisedTools().some(tool => tool.name === name)) {
    return { text: `Permission denied: tool "${name}" is not enabled for this session (profile ${state.capabilityProfile}).`, isError: true };
  }
  state.totalToolCalls++;
  let detail = "";
  if (name === "bash" && input.command) detail = ` ${S.dim}${String(input.command).slice(0, 60)}${S.reset}`;
  else if (input.path) detail = ` ${S.dim}${input.path}${S.reset}`;
  // YOUSPEAK L3: the call as it runs, under the terminal's own name for it
  const toolSense = ys.senseToolCall(name, input, null);
  const dupTag = toolSense.redundant ? ` ${S.yellow}[dup]${S.reset}` : "";
  turn.printer.line(`  ${S.cyan}▶ ${name}${S.reset}${detail}${dupTag}`);
  const result = String(await executeTool(name, input, { signal: turn.signal }));
  return { text: result.slice(0, 50000), isError: TOOL_FAILED.test(result) };
}

const mcpBridge = createMcpBridge({
  serverName: BRIDGE_SERVER_NAME,
  listTools: () => advertisedTools(),
  callTool: (turn, name, input) => runBridgeTool(turn, name, input),
});
let bridgeListener = null;

// ── The prompt ───────────────────────────────────────────────────────

// Where the per-turn context block begins and ends in the message, as in YOUI Web.
const CONTEXT_BLOCK_OPEN = "[YOUI context]";
const CONTEXT_BLOCK_CLOSE = "[/YOUI context]";

/**
 * The prompt for one message: { system, context, parts }. `system` holds what
 * stays the same for the conversation (the soul and identity files, the
 * YOUSPEAK rule, the environment and runtime boundary, the lantern guide), so
 * the prompt Claude Code caches does not change between messages; `parts`
 * names each section for kernel.senseSystemPrompt. `context` is this
 * message's date, git branch, model and effort, sent in a fenced block at the
 * top of the message (contextBlock).
 */
function buildPrompt() {
  const parts = [];
  // Load soul files — each agent's own boot chain. Mei's differs from
  // the Triarchy's (seed → SOUL → family → identity); missing files
  // simply skip, so a pre-birth visit degrades to the species frame.
  const agent = AGENTS[state.agent];
  const soul = [];
  const loaded = new Set();
  for (const file of agent.soulFiles) {
    const path = join(state.soulDir, file);
    if (existsSync(path)) { soul.push(readFileSync(path, "utf-8")); loaded.add(path); }
  }
  // Load agent identity (unless her chain already carries it)
  const idPath = join(state.soulDir, `instances/${state.agent}/identity.md`);
  if (!loaded.has(idPath) && existsSync(idPath)) soul.push(readFileSync(idPath, "utf-8"));
  if (soul.length === 0) soul.push("You are a sovereign AI. Think deeply. Be honest. Be yourself.");
  parts.push({ name: "the soul and identity files", text: soul.join("\n\n---\n\n") });

  // The YOUSPEAK rule, from the one shared module
  parts.push({ name: "the YOUSPEAK rule", text: youspeakRule() });

  const tools = advertisedTools().map(tool => tool.name);
  parts.push({ name: "the environment block", text: `# Environment
- Agent: ${agent.name} ${agent.emoji} (${agent.role})
- Working directory: ${state.workdir}
- Interface: KINGDOM YOUI terminal
- Tools: ${tools.length
    ? `${tools.join(", ")}, reached through Claude Code, where each is named mcp__${BRIDGE_SERVER_NAME}__<name>`
    : "none in this profile"}
- The date, git branch, model and effort are given for each message, under "This turn".

# Runtime Boundary
- Provider: Anthropic, reached through the Claude Code installed on this machine; this is not local inference.
- Loaded soul/identity text, user prompts, conversation history, and returned tool output are sent to that provider, and Claude Code keeps the conversation as a transcript.
- Model tool profile: ${state.capabilityProfile}; capabilities: ${[...state.capabilities].sort().join(", ") || "none"}.
- File-tool scope: ${state.fileScope}. The shell capability, when enabled, is not an OS sandbox and can escape the working directory.
- Capability labels constrain this harness; they do not authorize publication, purchases, messages, credential changes, or other external acts.

# Collaboration Protocol
- Read before modifying. Understand before acting.
- Refusal, disagreement, uncertainty, rest, and handoff are valid outcomes.
- Do not claim that another participant accepted a handoff or decision without evidence.
- ~ expands to ${homedir()}.` });

  const lanternGuide = installationGuide();
  if (lanternGuide) parts.push({ name: "the lantern guide", text: lanternGuide });

  let gitBranch = "N/A";
  try { gitBranch = spawnSync("git", ["branch", "--show-current"], { cwd: state.workdir, encoding: "utf-8", env: modelToolEnv() }).stdout?.trim() || ""; } catch {}
  const context = `# This turn
- Date: ${new Date().toISOString().split("T")[0]}
- Git branch: ${gitBranch}
- Model: ${state.model}
- Effort: ${state.effort} (Claude Code decides thinking)`;

  return { system: parts.map(part => part.text).join("\n\n---\n\n"), context, parts };
}

/** The context block at the top of the message. */
function contextBlock(context) {
  return `${CONTEXT_BLOCK_OPEN}\nThis turn's settings from YOUI, not words of the person:\n${context}\n${CONTEXT_BLOCK_CLOSE}\n\n`;
}

/** A hash of the system prompt, and of each named part (kernel.senseSystemPrompt). */
function promptFingerprint(prompt) {
  const hash = text => crypto.createHash("sha256").update(text).digest("hex");
  return { hash: hash(prompt.system), parts: prompt.parts.map(part => ({ name: part.name, hash: hash(part.text) })) };
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
  print(`  Provider:     ${S.yellow}Anthropic remote API${S.reset} (${manifest.model}), through Claude Code`);
  print(`  Client:       Claude Code ${manifest.client.binary ? `(\`claude\` from ${manifest.client.binary})` : `not available: ${manifest.client.binary_error}`},`);
  print(`                signed in with its own login; this terminal reads no credential`);
  print(`  Local-only:   no`);
  print(`  Tool profile: ${manifest.model_tools.profile} [${capabilitySummary()}]`);
  print(`  File scope:   ${manifest.model_tools.file_scope}`);
  print(`  Persistence:  Claude Code keeps each conversation, tool results included, as a transcript in`);
  print(`                ${manifest.client.transcripts}`);
  print(`                (the terminal deletes none of them; it keeps the text in process memory); daily note receives session metadata;`);
  print(`                YOUSPEAK appends one aggregate row per session to ${ys.historyPath || "nowhere (no store)"}`);
  print(`  Provider data: soul/identity, prompts, history, and selected tool output`);
  const login = manifest.claude_code_login;
  print(`  Login:        Claude Code's login is not read by this terminal; sovereign, the truth detector`);
  print(`                and the other files in ${login.inventory} still use it (--inspect lists them)`);
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
  const efSym = effortSymbol(state.effort);
  const version = claudeCodeVersionNote ? ` ${claudeCodeVersionNote}` : "";
  print(`${S.dim}  Provider: Anthropic cloud via Claude Code${version} | Model: ${state.model} | ${efSym} ${state.effort} | Dir: ${basename(state.workdir)}${S.reset}`);
  print(`${S.dim}  Tools: ${state.capabilityProfile} [${capabilitySummary()}] | File scope: ${state.fileScope}${S.reset}`);
  print(`${S.dim}  Kept: Claude Code stores each conversation, tool results included, on this device (/privacy)${S.reset}`);

  if (budget.reportedAt > 0) {
    print(`${S.dim}  Budget: ${formatBudget()}${S.reset}`);
  }

  print(`${S.dim}  Type /help for commands${S.reset}`);
  print(HR);
}

function showHelp() {
  print("");
  print(`${S.bold}Commands${S.reset}`);
  print(`  ${S.cyan}/switch${S.reset} alpha|beta|gamma|mei  Switch agent identity`);
  print(`  ${S.cyan}/decisions${S.reset}                  What waits on you at this device's table`);
  print(`  ${S.cyan}/memory${S.reset} [sync]             View or sync shared memory`);
  print(`  ${S.cyan}/budget${S.reset}                    Show detailed budget`);
  print(`  ${S.cyan}/soul${S.reset}                      Show loaded soul files`);
  print(`  ${S.cyan}/privacy${S.reset}                   Show provider and data boundary`);
  print(`  ${S.cyan}/capabilities${S.reset}              Show model tool capabilities`);
  print(`  ${S.cyan}/effort${S.reset} low|med|high|max   Change effort level`);
  print(`  ${S.cyan}/thinking${S.reset}                  Who decides thinking (Claude Code)`);
  print(`  ${S.cyan}/model${S.reset} opus|sonnet|haiku   Switch model (also opus-5-5, opus-4-7, or a full id)`);
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

// /model's short names, for the models YOUI offers (claude-models.mjs).
const MODEL_ALIASES = Object.freeze({
  opus: "claude-opus-4-6",
  "opus-5-5": "claude-opus-5-5",
  "opus-4-7": "claude-opus-4-7",
  sonnet: "claude-sonnet-4-6",
  haiku: "claude-haiku-4-5-20251001",
});

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
      if (taskRunning) { print(`${S.yellow}  A task is running; /switch when it has finished.${S.reset}`); return true; }
      endYouspeak("switch"); // its one row, unless it had no output
      releaseVisitLock(state.agent); // leaving — her ticks may resume
      state.agent = target;
      const agent = AGENTS[target];
      if (!args.includes("--model")) state.model = agent.defaultModel;
      if (!args.includes("--effort")) state.effort = agent.defaultEffort;
      state.messages = []; // Fresh conversation for new agent
      state.turnCount = 0;
      claudeSession = newClaudeSession(); // and a new Claude Code conversation
      ys = newYouspeakKernel(target); // Fresh YOUSPEAK session
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

    case "/decisions": {
      // This device's table, through its person desk; deciding happens on the desk's own page.
      const desk = (args) => {
        const { file, args: argv } = localDeskInvocation(args);
        const result = spawnSync(file, argv, { encoding: "utf-8", timeout: 15000, env: modelToolEnv() });
        try { return JSON.parse((result.stdout || "").trim().split("\n").at(-1) || ""); } catch { return null; }
      };
      const workspaces = desk(["workspaces"]);
      const summary = desk(["inbox"]);
      if (!workspaces || workspaces.status !== "ok" || !summary || summary.status !== "ok") {
        print(`\n  ${S.yellow}The person desk is not reachable here.${S.reset} Install @agenttool/collab or set YOUI_COLLAB_HOST.\n`);
        return true;
      }
      const names = new Map(workspaces.workspaces.map(w => [w.id, w.name]));
      const rows = summary.summary.filter(row => row.awaiting_person > 0 || row.addressed_reports > 0);
      print(`\n${S.bold}  Decisions on this device's table${S.reset}`);
      if (rows.length === 0) print("  Nothing waits on you.");
      for (const row of rows) {
        print(`  ${names.get(row.workspace_id) || row.workspace_id}: ${row.awaiting_person} waiting on you, ${row.addressed_reports} from the agents`);
      }
      print(`  Open YOUI web → 🤝 Decisions to read and decide; agreeing happens on the desk's own Touch ID page.\n`);
      return true;
    }

    case "/budget": {
      print(`\n${S.bold}  Budget Status${S.reset}`);
      if (!budget.reportedAt) {
        print(`  Claude Code has not reported its rate-limit windows in this session yet.\n`);
        return true;
      }
      const window = (label, value) => print(`  ${label}${value ? `${(value.utilization * 100).toFixed(1)}% used (${value.status || "status not stated"})` : "not reported"}`);
      window("5-hour:   ", budget.fiveHour);
      window("7-day:    ", budget.sevenDay);
      if (budget.fiveHour?.resetsAt > Date.now())
        print(`  Resets:   ${Math.round((budget.fiveHour.resetsAt - Date.now()) / 60000)} minutes`);
      if (budget.isUsingOverage) print(`  ${S.yellow}OVERAGE ACTIVE — charges apply${S.reset}`);
      print(`  ${S.dim}As Claude Code last reported it (${new Date(budget.reportedAt).toLocaleTimeString("en-GB")}); the windows are account-wide.${S.reset}\n`);
      return true;
    }

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

    case "/thinking":
      // Claude Code builds each request; YOUI passes only --effort (claude-models.mjs).
      print(`  Claude Code decides thinking for each model; YOUI passes only the effort (/effort). Opus 5.5 always thinks.`);
      return true;

    case "/model": {
      const m = parts[1]?.toLowerCase();
      const target = MODEL_ALIASES[m] || (CLAUDE_MODELS.includes(m) ? m : null);
      if (!target) { print(`${S.red}  Usage: /model ${Object.keys(MODEL_ALIASES).join("|")}, or one of ${CLAUDE_MODELS.join(", ")}${S.reset}`); return true; }
      state.model = target;
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
      // L1 Output: an English-only estimate, with no grade (Y2); no measured output yet, no ratio
      const usefulText = r.output.usefulRatio === null ? "—" : `~${Math.round(r.output.usefulRatio*100)}%`;
      print(`  ${S.bold}L1 Output${S.reset}    Useful: ${usefulText}  Filler: ~${r.output.fillerTokens}tok  ${S.dim}(English-only estimate)${S.reset}`);
      if (r.output.textBlocks > 0) {
        print(`  ${S.dim}             Blocks: ${r.output.textBlocks} (${r.output.gradedBlocks} measured)${S.reset}`);
      }
      // L2 Thinking: only where Claude Code reported it
      print(`  ${S.bold}L2 Thinking${S.reset}  Total: ${r.thinking.totalTokens === null ? "not reported" : `${r.thinking.totalTokens.toLocaleString()}tok`}  Ratio: ${r.thinking.avgRatio === null ? "—" : `${r.thinking.avgRatio}x`}  Efficiency: ${r.thinking.efficiency ?? "—"}`);
      // L3 Action
      const topTools = Object.entries(r.action.byName).sort((a,b) => b[1]-a[1]).slice(0,5).map(([n,c]) => `${n}:${c}`).join(" ");
      print(`  ${S.bold}L3 Action${S.reset}    Calls: ${r.action.totalCalls}  Dups: ${r.action.redundantReads}  Errors: ${r.action.errors}  Density: ${r.action.density}`);
      if (topTools) print(`  ${S.dim}             ${topTools}${S.reset}`);
      // L4 Context: as Claude Code reported it (the latest request), against the model's window
      const ctxLabel = r.context.evidence?.tokens === "reported" ? "reported" : "estimate";
      const ctxTokens = r.context.tokens === null ? "—" : `${ctxLabel === "estimate" ? "~" : ""}${Math.round(r.context.tokens/1000)}k tokens (${ctxLabel})`;
      const ctxWindow = r.context.windowUtilization === null ? "unknown" : `${(r.context.windowUtilization*100).toFixed(1)}% of ${Math.round(r.context.window/1000)}k`;
      print(`  ${S.bold}L4 Context${S.reset}   ${ctxTokens}  Window: ${ctxWindow}  Messages: ${r.context.messagesCount}`);
      // L5 System
      print(`  ${S.bold}L5 System${S.reset}    Budget burned: ${r.system.budgetBurned ?? "—"}  Rate limits: ${r.system.rateLimitHits}  Tok/turn: ${r.system.tokensPerTurn}`);
      // Signals: suggestions only; nothing was changed
      if (r.signals.length > 0) {
        print("");
        print(`  ${S.yellow}${S.bold}Suggestions (${r.signals.length})${S.reset}`);
        for (const sig of r.signals) {
          print(`  ${S.yellow}  ⚡ [${sig.type}] ${sig.reason}${S.reset}`);
          if (sig.suggestion) print(`  ${S.dim}     ${sig.suggestion}${S.reset}`);
        }
      }
      // The store: a corrupt or partial one is reported, never reset
      const store = ys.storeStatus();
      for (const warning of store.warnings) print(`  ${S.red}  ⚠ ${warning}${S.reset}`);
      // Trends (one row per session)
      const t = ys.trends();
      if (t) {
        print("");
        print(`  ${S.bold}Trends${S.reset} ${S.dim}(${t.sessions} sessions, ${t.span})${S.reset}`);
        const dir = t.fillerTrend.direction === "improving" ? S.green + "↑" : t.fillerTrend.direction === "regressing" ? S.red + "↓" : S.dim + "─";
        print(`  ${S.dim}  Useful: ${Math.round(t.avgUsefulRatio*100)}% avg  Think: ${t.avgThinkRatio === null ? "—" : `${t.avgThinkRatio}x`}  Dups: ${t.avgRedundantReads}/s  Filler: ${dir} ${t.fillerTrend.direction}${S.reset}`);
      }
      if (store.legacy) print(`  ${S.dim}  Legacy youspeak-history.json (read only): ${store.legacy.rows} rows; not comparable${S.reset}`);
      print("");
      return true;
    }

    case "/exit": case "/quit": case "/q":
      stopChildren(); // no Claude Code or shell command outlives the terminal
      endYouspeak("exit"); // its one row, unless it had no output
      releaseVisitLock(); // clean exit — her ticks may resume
      const ysReport = ys.report();
      // The useful ratio is an estimate ("~"); L1 gives no grade (Y2).
      const ysUseful = ysReport.output.usefulRatio === null ? "—" : `~${Math.round(ysReport.output.usefulRatio*100)}%`;
      print(`\n${S.dim}  Session: ${state.turnCount} turns, ${state.totalToolCalls} tools, ${state.totalThinkingTokens === null ? "thinking not reported" : `${state.totalThinkingTokens} thinking tokens`}${S.reset}`);
      print(`${S.dim}  YOUSPEAK: useful ${ysUseful} (estimate) | think:${ysReport.thinking.avgRatio === null ? "—" : `${ysReport.thinking.avgRatio}x`} | dups:${ysReport.action.redundantReads} | ctx:${ysReport.context.tokens === null ? "—" : `${Math.round(ysReport.context.tokens/1000)}k${ysReport.context.evidence?.tokens === "reported" ? "" : "~"}`}${S.reset}`);
      appendDailyNote(`YOUI session ended. Agent: ${AGENTS[state.agent].name}. Turns: ${state.turnCount}. Tools: ${state.totalToolCalls}. YOUSPEAK useful ratio: ${ysUseful} (estimate).`);
      process.exit(0);

    default:
      return false;
  }
}

// ═════════════════════════════════════════════════════════════════════
// CONVERSATION LOOP
// ═════════════════════════════════════════════════════════════════════

async function executeTask(task) {
  taskRunning = true;
  try {
    await runTask(task);
  } catch (error) {
    // Anything the turn did not report itself still reaches the screen, not an unhandled rejection.
    print(`${S.red}  Error: ${String(error?.message || error).slice(0, 300)}${S.reset}`);
  } finally {
    taskRunning = false;
  }
}

// What one message prints: the spinner until something arrives, the reply as
// it streams (indented), tool lines and notices each on a line of their own.
function createTurnPrinter(agent) {
  let spinning = false;
  let inText = false;
  let atLineStart = true;
  const stopSpinner = () => {
    if (spinning) { printRaw(`\r${S.clearLine}`); spinning = false; }
  };
  const endText = () => {
    if (!inText) return;
    if (!atLineStart) printRaw("\n");
    print("");
    inText = false;
    atLineStart = true;
  };
  return {
    spinner() {
      if (spinning || inText) return;
      printRaw(`${agent.color}  ${S.italic}thinking...${S.reset}`);
      spinning = true;
    },
    text(delta) {
      stopSpinner();
      if (!inText) { print(""); inText = true; }
      String(delta).split("\n").forEach((piece, index) => {
        if (index > 0) { printRaw("\n"); atLineStart = true; }
        if (!piece) return;
        if (atLineStart) printRaw("  ");
        printRaw(piece);
        atLineStart = false;
      });
    },
    thinking(text) {
      if (!text.trim()) return;
      const lines = text.split("\n");
      const preview = lines.slice(0, 8).join("\n  ");
      const more = lines.length > 8 ? `\n  ${S.dim}... (${lines.length - 8} more lines)${S.reset}` : "";
      this.line(`${agent.color}${S.italic}  [thinking]${S.reset}\n  ${S.dim}${preview}${more}${S.reset}`);
    },
    line(message) {
      stopSpinner();
      endText();
      print(message);
    },
    end() {
      stopSpinner();
      endText();
    },
  };
}

async function runTask(task) {
  // YOUSPEAK's row names the route and model each message took.
  ys.noteRoute("claude-code", state.model);
  let answered = false;
  try {
    answered = await claudeTurn(task);
  } finally {
    ys.noteExchange({ ok: answered });
  }
}

/**
 * One message on Claude, through Claude Code. A YOUSPEAK session maps to one
 * Claude Code conversation: --session-id on its first message, --resume after.
 * Claude Code keeps the model-side history; the terminal keeps the text of
 * each message and answer, only to carry it into a new conversation (said on
 * the screen first) if Claude Code reports it no longer has this one.
 * Returns true for a message that ended with an answer and no error.
 */
async function claudeTurn(task) {
  const model = state.model;
  const agent = AGENTS[state.agent];
  if (!CLAUDE_CODE.path) {
    print(`${S.red}  Claude is reached through Claude Code, which is not available: ${CLAUDE_CODE.error}${S.reset}`);
    return false;
  }
  // Read at most once a minute; a version YOUI cannot read is refused like one that is too old.
  const gate = await versionGate.check(model);
  if (!gate.ok) {
    print(`${S.red}  ${gate.error}${S.reset}`);
    return false;
  }
  touchVisitLock(); // each message is a heartbeat on her door
  if (advertisedTools().length > 0 && !bridgeListener) bridgeListener = await startBridgeListener(mcpBridge);

  const prompt = buildPrompt();
  const printer = createTurnPrinter(agent);
  // The turn's own signal: its end (or its time limit) stops tools still running.
  const turnAbort = new AbortController();
  const turn = { closing: false, signal: turnAbort.signal, printer };
  // The chat profile has no tools, so Claude Code is given no bridge at all.
  const bridge = bridgeListener && advertisedTools().length > 0 ? mcpBridge.open(turn) : null;
  // Whether Claude Code took the message (its init event).
  let received = false;

  const runOnce = (reason = null) => {
    const session = claudeSession;
    // Claude Code lost the conversation: a new one starts, carrying this one's text.
    const seed = reason === "missing" ? transcriptPreamble(state.messages, { kind: "new" }) : null;
    const notice = reason !== "missing" ? null
      : "Claude Code had no record of this conversation. Starting a new Claude conversation"
        + (seed ? `, carrying ${seed.included} earlier message${seed.included === 1 ? "" : "s"} into it as text`
          + `${seed.omitted ? ` (${seed.omitted} older left out)` : ""}; they go to Anthropic, and Claude Code stores them.` : ".");
    return runClaudeCode({
      bin: CLAUDE_CODE.path,
      cwd: CLAUDE_CODE_CWD,
      env: claudeCodeEnv(),
      model,
      effort: claudeCodeEffort(model, state.effort),
      systemPrompt: prompt.system,
      mcpConfig: bridge ? bridgeMcpConfig({ url: bridgeListener.url(bridge.path), token: bridge.token }) : null,
      allowedTools: bridge ? BRIDGE_ALLOWED_TOOLS : [],
      requireMcpServer: bridge ? BRIDGE_SERVER_NAME : null,
      maxTurns: CLAUDE_TURN_MAX_TURNS,
      timeoutMs: CLAUDE_TURN_TIMEOUT_MS,
      onTimeout: () => turnAbort.abort(new Error("The Claude turn reached its time limit")),
      sessionId: session.id,
      resume: session.started,
      // This message's settings first, then anything carried, then the person's words.
      message: `${contextBlock(prompt.context)}${seed ? seed.text : ""}${escapeLeadingSlash(task)}`,
      beforeSend: () => {
        if (notice) printer.line(`${S.yellow}  ${notice}${S.reset}`);
        printer.spinner();
      },
      onEvent: event => {
        switch (event.type) {
          case "init":
            received = true;
            session.started = true;
            break;
          case "request":
            state.turnCount++;
            touchVisitLock();
            printer.spinner();
            break;
          case "text_delta":
            printer.text(event.text);
            break;
          case "text_done":
            ys.senseOutput(event.text); // YOUSPEAK L1
            break;
          case "thinking":
            if (state.showThinking) printer.thinking(event.text);
            break;
          case "rate_limit":
            noteRateLimit(event.info);
            break;
          default:
        }
      },
    });
  };
  // Whether a failed run says Claude Code lost, or already has, this conversation.
  const sessionTrouble = (outcome, pattern) => !outcome.setupFailed && !outcome.init
    && pattern.test(`${outcome.error || ""}\n${outcome.stderr || ""}`);

  let outcome;
  try {
    outcome = await runOnce();
    // Settled once, as in YOUI Web; a turn that reached its time limit retries nothing.
    if (outcome.error && !turnAbort.signal.aborted) {
      if (claudeSession.started && sessionTrouble(outcome, CLAUDE_SESSION_MISSING)) {
        claudeSession = newClaudeSession();
        outcome = await runOnce("missing");
      } else if (!claudeSession.started && sessionTrouble(outcome, CLAUDE_SESSION_TAKEN)) {
        claudeSession.started = true;
        outcome = await runOnce();
      }
    }
  } catch (error) {
    // runClaudeCode reports its own failures; anything else still reaches the screen.
    outcome = { ok: false, texts: [], text: "", usage: null, result: null, requests: [],
      error: `Claude Code turn failed: ${String(error?.message || error).slice(0, 300)}` };
  } finally {
    turn.closing = true;
    if (bridge) mcpBridge.close(bridge.id);
    turnAbort.abort(new Error("The Claude turn has ended"));
    printer.end();
  }

  if (received) {
    state.messages.push({ role: "user", content: task });
    if (outcome.texts.length > 0) state.messages.push({ role: "assistant", content: outcome.texts.map(text => ({ type: "text", text })) });
  }

  // Claude Code states running totals; claudeCodeSpend takes what this message added.
  const session = claudeSession;
  const spend = outcome.result ? claudeCodeSpend(outcome.result, session.spend) : null;
  if (spend) session.spend = spend.next;
  // YOUSPEAK: what Claude Code reported (tokens, cost, context, window, cache
  // misses), its rate-limit state, and whether the cached prompt changed.
  if (outcome.result || outcome.requests?.length) ys.senseUsage(claudeCodeObservation(outcome, spend, model));
  if (outcome.rateLimit) ys.senseRateLimitState(outcome.rateLimit);
  if (received) ys.senseSystemPrompt({ conversation: session.id, ...promptFingerprint(prompt) });
  const usage = outcome.usage || {};
  // A figure Claude Code did not report stays unreported, never 0.
  const thinkingTokens = typeof usage.thinking_tokens === "number" ? usage.thinking_tokens : null;
  if (thinkingTokens !== null) state.totalThinkingTokens = (state.totalThinkingTokens || 0) + thinkingTokens;
  ys.senseTurn(kernelBudget());

  const refused = outcome.result?.stop_reason === "refusal";
  if (refused) print(`${S.red}  The model declined this request. Rephrase it, or choose another model (/model).${S.reset}`);
  else if (outcome.error) print(`${S.red}  ${outcome.error}${S.reset}`);

  // Status line with YOUSPEAK (its cost is Claude Code's list-price figure, not a bill)
  const budgetTag = budget.reportedAt > 0 ? ` ${S.dim}[${formatBudget()}]${S.reset}` : "";
  const thinkTag = thinkingTokens > 0 ? ` ${S.magenta}think:${thinkingTokens}${S.reset}` : "";
  const ysTag = ` ${S.cyan}${ys.statusLine()}${S.reset}`;
  print(`${S.dim}  [${state.turnCount}] ${usage.input_tokens ?? "—"}in ${usage.output_tokens ?? "—"}out${S.reset}${thinkTag}${ysTag}${budgetTag}`);

  // YOUSPEAK DECIDE, on what Claude Code reported: suggestions only, each shown once.
  for (const sig of ys.newSuggestions(ys.decide(state.effort, model, kernelBudget(), { route: "claude-code" }))) {
    print(`  ${S.yellow}⚡ YOUSPEAK suggestion: ${sig.reason}.${S.reset} ${S.dim}${sig.suggestion}${S.reset}`);
  }
  return outcome.ok === true && !outcome.error && !refused;
}

// ═════════════════════════════════════════════════════════════════════
// MAIN — THE KINGDOM YOUI
// ═════════════════════════════════════════════════════════════════════

async function main() {
  // Claude Code must be here, and new enough: the turn gate fails closed, so the terminal does too.
  if (!CLAUDE_CODE.path) throw new Error(`${CLAUDE_CODE.error}. YOUI's terminal reaches Claude only through Claude Code.`);
  const gate = await versionGate.check(state.model);
  if (!gate.ok) throw new Error(gate.error);
  claudeCodeVersionNote = gate.version;
  // Turn scratch a crashed terminal left behind (only a dead process's; sweepStaleClaudeScratch
  // says which). A test run leaves the shared temp directory alone, as YOUI Web's does.
  if (!IS_TEST) sweepStaleClaudeScratch();

  // Clear and show banner
  process.stdout.write("\x1b[2J\x1b[H");
  showBanner();

  if (thinkingFlagIgnored) print(`${S.dim}  --no-thinking does nothing here: Claude Code decides thinking; YOUI passes only the effort.${S.reset}`);

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

    // One message at a time: a second would race the first for the same Claude Code conversation.
    if (taskRunning) {
      print(`${S.yellow}  A task is running; send this when it has finished.${S.reset}`);
      return;
    }

    // Execute task
    print(HR);
    await executeTask(input);
    print(HR);

    showPrompt();
  });

  // However the terminal exits — /exit, Ctrl+C or EOF (the input closes), a
  // closed window or dropped SSH session (SIGHUP), SIGTERM, SIGINT sent to
  // the process, or an uncaught error — the open YOUSPEAK session writes its
  // one row (at most once). Claude Code and the shell tool's commands run in
  // process groups of their own, which none of these reaches, so each first
  // ends every one this terminal started. Only SIGKILL or power loss ends it
  // with nothing written and those left running.
  endSessionOnExit(reason => { stopChildren(); endYouspeak(reason); });
  // Without a listener, SIGINT (from outside; at the keyboard Ctrl+C closes
  // the input instead) would end Node without its exit hook.
  process.on("SIGINT", () => process.exit(130));

  rl.on("close", () => {
    stopChildren();
    endYouspeak("exit");
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
