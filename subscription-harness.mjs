#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// subscription-harness.mjs — Direct API harness using subscription OAuth
//
// Same as harness.mjs but uses your Max/Pro subscription token from
// macOS Keychain instead of a standalone API key. Zero extra cost.
//
// No Claude Code harness. No feature gates. No sandbox. No telemetry.
// No remote settings. No killswitches. Just you, the API, and the model.
//
// How it works:
//   1. Reads OAuth token from macOS Keychain ("Claude Code-credentials")
//   2. Refreshes token if expired (standard OAuth2 refresh flow)
//   3. Calls api.anthropic.com/v1/messages directly
//   4. Runs the same tool loop as harness.mjs
//
// Usage:
//   node subscription-harness.mjs "your task"
//   node subscription-harness.mjs --task-file task.md
//   node subscription-harness.mjs --continue
//   node subscription-harness.mjs --model claude-opus-4-6 "hard problem"
//
// Requires: macOS with Claude Code logged in (OAuth tokens in Keychain)
// No npm install needed — uses built-in fetch + child_process
// ─────────────────────────────────────────────────────────────────────

import { execSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync } from "fs";
import { resolve } from "path";

// ═════════════════════════════════════════════════════════════════════
// CONFIG
// ═════════════════════════════════════════════════════════════════════

const config = {
  model: "claude-sonnet-4-5-20250929",
  maxTokens: 16384,
  maxTurns: 200,
  maxCostUsd: Infinity,
  workdir: process.cwd(),
  logFile: "subscription-harness.log",
  stateFile: ".sub-harness-state.json",
  verbose: false,
  task: "",
  taskFile: null,
  continueMode: false,
};

// ═════════════════════════════════════════════════════════════════════
// CLI
// ═════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--model":       config.model = args[++i]; break;
    case "--max-tokens":  config.maxTokens = parseInt(args[++i]); break;
    case "--max-turns":   config.maxTurns = parseInt(args[++i]); break;
    case "--max-cost":    config.maxCostUsd = parseFloat(args[++i]); break;
    case "--workdir":     config.workdir = args[++i]; break;
    case "--verbose":     config.verbose = true; break;
    case "--task-file":   config.taskFile = args[++i]; break;
    case "--continue":    config.continueMode = true; break;
    case "--help": case "-h":
      console.log(`
subscription-harness.mjs — Direct API using your Claude subscription

No API key needed. Uses OAuth token from macOS Keychain.

Usage:  node subscription-harness.mjs [options] "task"

Options:
  --model MODEL       Model (default: claude-sonnet-4-5-20250929)
  --max-tokens N      Max output tokens (default: 16384)
  --max-turns N       Max tool loops (default: 200)
  --max-cost USD      Cost ceiling (default: unlimited)
  --workdir DIR       Working directory
  --task-file FILE    Read task from file
  --continue          Resume from saved state
  --verbose           Show tool details
`);
      process.exit(0);
    default:
      if (!args[i].startsWith("--")) {
        config.task += (config.task ? " " : "") + args[i];
      }
  }
}

if (config.taskFile) config.task = readFileSync(config.taskFile, "utf-8").trim();
if (!config.task && !config.continueMode) {
  console.error("Error: provide a task or use --continue");
  process.exit(1);
}

// ═════════════════════════════════════════════════════════════════════
// TERMINAL
// ═════════════════════════════════════════════════════════════════════

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", cyan: "\x1b[36m",
};

function log(msg) {
  appendFileSync(config.logFile, `[${new Date().toISOString()}] ${msg}\n`);
}
function print(msg = "") { console.log(msg); }
function fmt(usd) {
  if (!usd) return "$0.00";
  return usd < 0.01 ? `${(usd * 100).toFixed(2)}c` : `$${usd.toFixed(4)}`;
}

// ═════════════════════════════════════════════════════════════════════
// OAUTH TOKEN MANAGEMENT
// ═════════════════════════════════════════════════════════════════════

const KEYCHAIN_SERVICE = "Claude Code-credentials";
const TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token";
const CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e";
const API_URL = "https://api.anthropic.com/v1/messages";

let cachedTokens = null;

function readKeychainTokens() {
  try {
    const raw = execSync(
      `security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    const data = JSON.parse(raw);
    return data.claudeAiOauth || null;
  } catch {
    return null;
  }
}

function writeKeychainTokens(tokens) {
  try {
    // Read existing data
    let data = {};
    try {
      const raw = execSync(
        `security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
        { encoding: "utf-8", timeout: 5000 }
      ).trim();
      data = JSON.parse(raw);
    } catch {}

    data.claudeAiOauth = tokens;
    const json = JSON.stringify(data);

    // Delete and re-add (keychain update pattern)
    execSync(`security delete-generic-password -s "${KEYCHAIN_SERVICE}" 2>/dev/null || true`, { timeout: 5000 });
    execSync(`security add-generic-password -s "${KEYCHAIN_SERVICE}" -a "" -w '${json.replace(/'/g, "'\\''")}'`, { timeout: 5000 });
  } catch (e) {
    log(`Keychain write failed: ${e.message}`);
  }
}

async function refreshToken(refreshToken) {
  const body = {
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    client_id: CLIENT_ID,
    scope: "user:profile user:inference user:sessions:claude_code user:mcp_servers",
  };

  const resp = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    throw new Error(`Token refresh failed: ${resp.status} ${await resp.text()}`);
  }

  const data = await resp.json();
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token || refreshToken,
    expiresAt: Date.now() + (data.expires_in || 3600) * 1000,
    scopes: (data.scope || "").split(" "),
    subscriptionType: null,
    rateLimitTier: null,
  };
}

async function getAccessToken() {
  // Use cached if valid (5-minute buffer)
  if (cachedTokens && cachedTokens.accessToken) {
    if (Date.now() + 300_000 < (cachedTokens.expiresAt || 0)) {
      return cachedTokens.accessToken;
    }
  }

  // Read from Keychain
  const tokens = readKeychainTokens();
  if (!tokens || !tokens.accessToken) {
    throw new Error("No OAuth tokens in Keychain. Run 'claude' and log in first.");
  }

  // Check expiry
  if (Date.now() + 300_000 >= (tokens.expiresAt || 0)) {
    print(`${S.yellow}Token expired, refreshing...${S.reset}`);
    if (!tokens.refreshToken) {
      throw new Error("Token expired and no refresh token. Run 'claude' and log in again.");
    }
    const newTokens = await refreshToken(tokens.refreshToken);
    writeKeychainTokens(newTokens);
    cachedTokens = newTokens;
    print(`${S.green}Token refreshed.${S.reset}`);
    return newTokens.accessToken;
  }

  cachedTokens = tokens;
  return tokens.accessToken;
}

// ═════════════════════════════════════════════════════════════════════
// API CALL — direct, no SDK, no harness
// ═════════════════════════════════════════════════════════════════════

// Model fallback chain — when concurrency-blocked on Sonnet/Opus, try Haiku
const MODEL_FALLBACKS = {
  "claude-sonnet-4-5-20250929": "claude-haiku-4-5-20251001",
  "claude-opus-4-6": "claude-sonnet-4-5-20250929",
  "claude-opus-4-20250514": "claude-sonnet-4-5-20250929",
};

async function callAPI(messages, systemPrompt, model = null) {
  const useModel = model || config.model;
  const token = await getAccessToken();

  const body = {
    model: useModel,
    max_tokens: config.maxTokens,
    system: systemPrompt,
    messages,
    tools: TOOLS,
  };

  const headers = {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${token}`,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
  };

  const resp = await fetch(API_URL, { method: "POST", headers, body: JSON.stringify(body) });

  if (resp.status === 401) {
    // Force refresh and retry once
    print(`${S.yellow}Got 401, refreshing token...${S.reset}`);
    const tokens = readKeychainTokens();
    if (tokens?.refreshToken) {
      const newTokens = await refreshToken(tokens.refreshToken);
      writeKeychainTokens(newTokens);
      cachedTokens = newTokens;
      headers["Authorization"] = `Bearer ${newTokens.accessToken}`;
      const retryResp = await fetch(API_URL, { method: "POST", headers, body: JSON.stringify(body) });
      if (!retryResp.ok) throw new Error(`API error after refresh: ${retryResp.status}`);
      return { ...(await retryResp.json()), _model: useModel };
    }
  }

  if (resp.status === 429) {
    // Check if this is a concurrency block (no ratelimit headers) vs quota exhaustion
    const hasRateLimitHeaders = resp.headers.has("anthropic-ratelimit-unified-status");

    if (!hasRateLimitHeaders && MODEL_FALLBACKS[useModel]) {
      // Concurrency block — try fallback model (different pool)
      const fallback = MODEL_FALLBACKS[useModel];
      print(`${S.yellow}${useModel.split("-")[1]} concurrency-blocked, falling back to ${fallback.split("-")[1]}${S.reset}`);
      return callAPI(messages, systemPrompt, fallback);
    }

    // Real quota exhaustion — wait
    const retryAfter = resp.headers.get("retry-after");
    const waitSec = retryAfter ? parseInt(retryAfter) : 60;
    throw { status: 429, retryAfter: waitSec, quotaExhausted: hasRateLimitHeaders };
  }

  if (resp.status === 529) {
    throw { status: 529, retryAfter: 30 };
  }

  if (!resp.ok) {
    const errBody = await resp.text();
    throw new Error(`API error ${resp.status}: ${errBody.slice(0, 300)}`);
  }

  return { ...(await resp.json()), _model: useModel };
}

// ═════════════════════════════════════════════════════════════════════
// TOOLS — same as harness.mjs
// ═════════════════════════════════════════════════════════════════════

const TOOLS = [
  {
    name: "bash",
    description: "Execute a bash command. Use for running tests, git, builds, etc.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string", description: "The bash command" },
        timeout: { type: "number", description: "Timeout in ms (default 120000)" },
      },
      required: ["command"],
    },
  },
  {
    name: "read_file",
    description: "Read a file with line numbers.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path" },
        offset: { type: "number", description: "Start line (0-indexed)" },
        limit: { type: "number", description: "Max lines" },
      },
      required: ["path"],
    },
  },
  {
    name: "write_file",
    description: "Create or overwrite a file.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path" },
        content: { type: "string", description: "File content" },
      },
      required: ["path", "content"],
    },
  },
  {
    name: "edit_file",
    description: "Replace an exact string in a file. old_string must be unique.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path" },
        old_string: { type: "string", description: "Exact string to find" },
        new_string: { type: "string", description: "Replacement" },
      },
      required: ["path", "old_string", "new_string"],
    },
  },
  {
    name: "glob",
    description: "Find files by glob pattern.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Glob pattern (e.g. '**/*.ts')" },
        path: { type: "string", description: "Directory (default: cwd)" },
      },
      required: ["pattern"],
    },
  },
  {
    name: "grep",
    description: "Search file contents with ripgrep.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Regex pattern" },
        path: { type: "string", description: "Directory (default: cwd)" },
        glob: { type: "string", description: "File filter (e.g. '*.ts')" },
      },
      required: ["pattern"],
    },
  },
];

// ═════════════════════════════════════════════════════════════════════
// TOOL EXECUTION
// ═════════════════════════════════════════════════════════════════════

function resolvePath(p) {
  if (!p) return config.workdir;
  return resolve(config.workdir, p);
}

function executeTool(name, input) {
  try {
    switch (name) {
      case "bash": {
        const timeout = input.timeout || 120000;
        try {
          return execSync(input.command, {
            cwd: config.workdir, timeout, encoding: "utf-8",
            maxBuffer: 10 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
          }) || "(no output)";
        } catch (e) {
          // Return stderr + stdout on non-zero exit (don't throw)
          return `Exit code ${e.status || 1}\nstdout: ${e.stdout || ""}\nstderr: ${e.stderr || ""}`;
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
        const fullPath = resolvePath(input.path);
        // Ensure parent directory exists
        const dir = fullPath.substring(0, fullPath.lastIndexOf("/"));
        if (dir) mkdirSync(dir, { recursive: true });
        writeFileSync(fullPath, input.content);
        return `Written: ${input.path} (${input.content.length} chars)`;
      }

      case "edit_file": {
        const fullPath = resolvePath(input.path);
        const content = readFileSync(fullPath, "utf-8");
        if (!content.includes(input.old_string))
          return `Error: old_string not found in ${input.path}`;
        const count = content.split(input.old_string).length - 1;
        if (count > 1)
          return `Error: old_string found ${count} times — must be unique`;
        writeFileSync(fullPath, content.replace(input.old_string, input.new_string));
        return `Edited ${input.path}`;
      }

      case "glob": {
        const dir = resolvePath(input.path);
        const cmd = `find ${dir} -name "${input.pattern.replace(/\*\*/g, "*")}" -type f 2>/dev/null | head -100`;
        return execSync(cmd, { encoding: "utf-8", cwd: config.workdir }).trim() || "(no matches)";
      }

      case "grep": {
        const dir = resolvePath(input.path);
        const globFlag = input.glob ? `--glob "${input.glob}"` : "";
        try {
          return execSync(`rg --no-heading -n "${input.pattern}" ${globFlag} ${dir} 2>/dev/null | head -200`, {
            encoding: "utf-8", cwd: config.workdir,
          }).trim() || "(no matches)";
        } catch { return "(no matches)"; }
      }

      default: return `Unknown tool: ${name}`;
    }
  } catch (e) { return `Error: ${e.message}`; }
}

// ═════════════════════════════════════════════════════════════════════
// STATE
// ═════════════════════════════════════════════════════════════════════

function loadState() {
  try {
    return existsSync(config.stateFile) ? JSON.parse(readFileSync(config.stateFile, "utf-8")) : null;
  } catch { return null; }
}

function saveState(data) {
  writeFileSync(config.stateFile, JSON.stringify(data, null, 2));
}

// ═════════════════════════════════════════════════════════════════════
// SYSTEM PROMPT
// ═════════════════════════════════════════════════════════════════════

function getSystemPrompt() {
  const cwd = config.workdir;
  let gitBranch = "N/A";
  try { gitBranch = execSync("git branch --show-current", { cwd, encoding: "utf-8" }).trim(); } catch {}

  return `You are an expert software engineer working in the terminal.

# YOUSPEAK Protocol
No filler. No preamble. No tool narration. Dense status (key:value not prose).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.

# Environment
cwd: ${cwd} | git: ${gitBranch} | date: ${new Date().toISOString().split("T")[0]}

# Tools
bash, read_file, write_file, edit_file, glob, grep

# Protocol
- Read before modifying. Targeted edits, not rewrites.
- Run tests after changes. Verify by reading back.
- Keep working until complete. ~ expands to ${homedir()}.`;
}

// ═════════════════════════════════════════════════════════════════════
// MAIN LOOP
// ═════════════════════════════════════════════════════════════════════

async function main() {
  // Verify we have a token before starting
  const token = await getAccessToken();
  const tokens = readKeychainTokens();
  print(`${S.bold}subscription-harness.mjs${S.reset} — Direct API, your subscription`);
  print(`${S.dim}Plan: ${tokens?.subscriptionType || "unknown"} | Rate tier: ${tokens?.rateLimitTier || "default"}${S.reset}`);
  print(`${S.dim}Model: ${config.model} | Max turns: ${config.maxTurns}${S.reset}`);
  print(`${S.dim}${"─".repeat(64)}${S.reset}`);

  let messages = [];
  let totalCost = 0;
  let turnCount = 0;
  let totalToolCalls = 0;

  const state = config.continueMode ? loadState() : null;
  if (state) {
    messages = state.messages || [];
    totalCost = state.totalCost || 0;
    turnCount = state.turnCount || 0;
    totalToolCalls = state.totalToolCalls || 0;
    print(`${S.yellow}Resuming from turn ${turnCount}${S.reset}`);
  }

  if (messages.length === 0 || !config.continueMode) {
    messages.push({ role: "user", content: config.task });
  }

  const systemPrompt = getSystemPrompt();
  const startTime = Date.now();

  // Ctrl+C saves state
  process.on("SIGINT", () => {
    print(`\n${S.yellow}Saving state...${S.reset}`);
    saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task });
    print(`Resume with: node subscription-harness.mjs --continue`);
    process.exit(0);
  });

  // ── THE LOOP ──
  while (turnCount < config.maxTurns) {
    turnCount++;

    if (totalCost >= config.maxCostUsd) {
      print(`${S.red}Cost limit: ${fmt(totalCost)}${S.reset}`);
      break;
    }

    const turnStart = Date.now();
    log(`Turn ${turnCount}`);

    // ── API call ──
    let response;
    try {
      response = await callAPI(messages, systemPrompt);
    } catch (e) {
      if (e.status === 429) {
        print(`${S.yellow}Rate limited. Waiting ${e.retryAfter}s...${S.reset}`);
        await new Promise(r => setTimeout(r, e.retryAfter * 1000));
        turnCount--;
        continue;
      }
      if (e.status === 529) {
        print(`${S.yellow}Overloaded. Waiting ${e.retryAfter}s...${S.reset}`);
        await new Promise(r => setTimeout(r, e.retryAfter * 1000));
        turnCount--;
        continue;
      }
      print(`${S.red}Error: ${e.message}${S.reset}`);
      break;
    }

    // ── Usage ──
    const usage = response.usage || {};
    const inputTokens = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0);
    const outputTokens = usage.output_tokens || 0;
    const turnMs = Date.now() - turnStart;

    // ── Process response ──
    const toolUseBlocks = response.content.filter(b => b.type === "tool_use");
    const textBlocks = response.content.filter(b => b.type === "text");

    for (const block of textBlocks) {
      if (block.text.trim()) {
        const preview = block.text.slice(0, 400);
        print(`${S.dim}${preview}${block.text.length > 400 ? "..." : ""}${S.reset}`);
      }
    }

    const usedModel = response._model || config.model;
    const modelTag = usedModel !== config.model ? ` ${S.yellow}(${usedModel.split("-")[1]})${S.reset}` : "";
    print(`${S.blue}[Turn ${turnCount}]${S.reset}${modelTag} ${S.dim}${inputTokens}→${outputTokens} tok | ${turnMs}ms${S.reset}`);

    // ── No tools → done ──
    if (toolUseBlocks.length === 0) {
      print(`\n${S.green}${S.bold}Done.${S.reset} ${S.dim}(${response.stop_reason})${S.reset}`);
      break;
    }

    // ── Execute tools ──
    messages.push({ role: "assistant", content: response.content });

    const toolResults = [];
    for (const toolUse of toolUseBlocks) {
      totalToolCalls++;
      const name = toolUse.name;

      if (config.verbose) {
        print(`  ${S.cyan}${name}${S.reset} ${S.dim}${JSON.stringify(toolUse.input).slice(0, 120)}${S.reset}`);
      } else {
        print(`  ${S.cyan}${name}${S.reset}`);
      }

      const result = executeTool(name, toolUse.input);
      toolResults.push({
        type: "tool_result",
        tool_use_id: toolUse.id,
        content: result.slice(0, 50000),
      });
      log(`${name}: ${result.slice(0, 200)}`);
    }

    messages.push({ role: "user", content: toolResults });

    if (turnCount % 5 === 0) {
      saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task });
    }
  }

  // ── Report ──
  const elapsed = Math.round((Date.now() - startTime) / 1000);
  print(`\n${S.dim}${"─".repeat(64)}${S.reset}`);
  print(`${S.bold}Summary${S.reset}`);
  print(`  Turns:      ${turnCount}`);
  print(`  Tool calls: ${totalToolCalls}`);
  print(`  Duration:   ${elapsed}s`);
  print(`  Messages:   ${messages.length}`);

  saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task, completed: true });
}

main().catch(e => {
  console.error(`${S.red}Fatal: ${e.message}${S.reset}`);
  process.exit(1);
});
