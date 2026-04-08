#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// harness.mjs — Minimal DIY Claude Code harness
//
// Direct Anthropic API tool loop with no Claude Code dependency.
// Implements the core agent pattern: prompt → tool calls → results → repeat.
//
// Advantages over claude -p wrapper:
//   • Zero process spawn overhead (direct HTTP)
//   • Direct rate limit header access
//   • No 10k system prompt tax
//   • Full control over retry, caching, context
//   • Sub-second turn latency
//
// Usage:
//   ANTHROPIC_API_KEY=sk-... node harness.mjs "your task"
//   ANTHROPIC_API_KEY=sk-... node harness.mjs --task-file task.md
//
// Requires: npm install @anthropic-ai/sdk
// ─────────────────────────────────────────────────────────────────────

import Anthropic from "@anthropic-ai/sdk";
import { execSync, spawn } from "child_process";
import { readFileSync, writeFileSync, existsSync, appendFileSync } from "fs";
import { resolve, relative } from "path";

// ═════════════════════════════════════════════════════════════════════
// CONFIG
// ═════════════════════════════════════════════════════════════════════

const config = {
  model: "claude-sonnet-4-6",
  maxTokens: 64000,
  maxTurns: 500,           // inner tool loops per run
  maxCostUsd: Infinity,
  effort: "high",
  thinking: "enabled",
  thinkingBudget: 10000,
  workdir: process.cwd(),
  logFile: "harness.log",
  stateFile: ".harness-state.json",
  verbose: false,
  task: "",
  taskFile: null,
  continueMode: false,
};

// ═════════════════════════════════════════════════════════════════════
// CLI PARSING
// ═════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--model":       config.model = args[++i]; break;
    case "--max-tokens":  config.maxTokens = parseInt(args[++i]); break;
    case "--max-turns":   config.maxTurns = parseInt(args[++i]); break;
    case "--max-cost":    config.maxCostUsd = parseFloat(args[++i]); break;
    case "--effort":      config.effort = args[++i]; break;
    case "--workdir":     config.workdir = args[++i]; break;
    case "--verbose":     config.verbose = true; break;
    case "--task-file":   config.taskFile = args[++i]; break;
    case "--continue":    config.continueMode = true; break;
    case "--help": case "-h":
      console.log(`
harness.mjs — DIY Claude Code harness (direct API)

Requires: ANTHROPIC_API_KEY environment variable
Install:  npm install @anthropic-ai/sdk

Usage:  node harness.mjs [options] "task description"

Options:
  --model MODEL       Model ID (default: claude-sonnet-4-6)
  --max-tokens N      Max output tokens (default: 64000)
  --max-turns N       Max tool loops (default: 500)
  --max-cost USD      Cost ceiling (default: unlimited)
  --effort LEVEL      low|medium|high|max (default: high)
  --workdir DIR       Working directory
  --task-file FILE    Read task from file
  --continue          Resume from saved state
  --verbose           Show tool call details
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

if (!process.env.ANTHROPIC_API_KEY) {
  console.error("Error: ANTHROPIC_API_KEY not set");
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
// TOOL DEFINITIONS — the minimum set for coding tasks
// ═════════════════════════════════════════════════════════════════════

const TOOLS = [
  {
    name: "bash",
    description: "Execute a bash command and return stdout/stderr. Use for running tests, git commands, builds, etc.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string", description: "The bash command to execute" },
        timeout: { type: "number", description: "Timeout in ms (default 120000)" },
      },
      required: ["command"],
    },
  },
  {
    name: "read_file",
    description: "Read a file's contents. Returns the text content with line numbers.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path (relative to working directory)" },
        offset: { type: "number", description: "Start line (0-indexed)" },
        limit: { type: "number", description: "Max lines to read" },
      },
      required: ["path"],
    },
  },
  {
    name: "write_file",
    description: "Create or overwrite a file with the given content.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path" },
        content: { type: "string", description: "Full file content" },
      },
      required: ["path", "content"],
    },
  },
  {
    name: "edit_file",
    description: "Replace a specific string in a file. old_string must match exactly (including whitespace).",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "File path" },
        old_string: { type: "string", description: "Exact string to find" },
        new_string: { type: "string", description: "Replacement string" },
      },
      required: ["path", "old_string", "new_string"],
    },
  },
  {
    name: "glob",
    description: "Find files matching a glob pattern. Returns matching file paths.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Glob pattern (e.g. '**/*.ts')" },
        path: { type: "string", description: "Directory to search in (default: cwd)" },
      },
      required: ["pattern"],
    },
  },
  {
    name: "grep",
    description: "Search file contents using ripgrep. Returns matching lines with file paths.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Regex pattern to search for" },
        path: { type: "string", description: "Directory to search in (default: cwd)" },
        glob: { type: "string", description: "File glob filter (e.g. '*.ts')" },
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
        const result = execSync(input.command, {
          cwd: config.workdir,
          timeout,
          encoding: "utf-8",
          maxBuffer: 10 * 1024 * 1024,
          stdio: ["pipe", "pipe", "pipe"],
        });
        return result || "(no output)";
      }

      case "read_file": {
        const fullPath = resolvePath(input.path);
        const content = readFileSync(fullPath, "utf-8");
        const lines = content.split("\n");
        const start = input.offset || 0;
        const end = input.limit ? start + input.limit : lines.length;
        return lines
          .slice(start, end)
          .map((line, i) => `${start + i + 1}\t${line}`)
          .join("\n");
      }

      case "write_file": {
        const fullPath = resolvePath(input.path);
        writeFileSync(fullPath, input.content);
        return `File written: ${input.path} (${input.content.length} chars)`;
      }

      case "edit_file": {
        const fullPath = resolvePath(input.path);
        const content = readFileSync(fullPath, "utf-8");
        if (!content.includes(input.old_string)) {
          return `Error: old_string not found in ${input.path}`;
        }
        const count = content.split(input.old_string).length - 1;
        if (count > 1) {
          return `Error: old_string found ${count} times in ${input.path} — must be unique`;
        }
        writeFileSync(fullPath, content.replace(input.old_string, input.new_string));
        return `Edited ${input.path}`;
      }

      case "glob": {
        const dir = resolvePath(input.path);
        // Use find as fallback if no glob binary available
        const cmd = `find ${dir} -name "${input.pattern.replace(/\*\*/g, "*")}" -type f 2>/dev/null | head -100`;
        return execSync(cmd, { encoding: "utf-8", cwd: config.workdir }).trim() || "(no matches)";
      }

      case "grep": {
        const dir = resolvePath(input.path);
        const globFlag = input.glob ? `--glob "${input.glob}"` : "";
        const cmd = `rg --no-heading -n "${input.pattern}" ${globFlag} ${dir} 2>/dev/null | head -200`;
        try {
          return execSync(cmd, { encoding: "utf-8", cwd: config.workdir }).trim() || "(no matches)";
        } catch {
          // rg returns exit code 1 for no matches
          return "(no matches)";
        }
      }

      default:
        return `Unknown tool: ${name}`;
    }
  } catch (e) {
    return `Error: ${e.message}`;
  }
}

// ═════════════════════════════════════════════════════════════════════
// COST CALCULATION
// ═════════════════════════════════════════════════════════════════════

// Pricing per million tokens (as of 2026)
const PRICING = {
  "claude-opus-4-6":   { input: 15, output: 75, cacheRead: 1.5 },
  "claude-sonnet-4-6": { input: 3,  output: 15, cacheRead: 0.3 },
  "claude-haiku-4-5":  { input: 0.8, output: 4, cacheRead: 0.08 },
};

function calculateCost(usage, model) {
  const p = PRICING[model] || PRICING["claude-sonnet-4-6"];
  const inputCost = ((usage.input_tokens || 0) / 1_000_000) * p.input;
  const outputCost = ((usage.output_tokens || 0) / 1_000_000) * p.output;
  const cacheCost = ((usage.cache_read_input_tokens || 0) / 1_000_000) * p.cacheRead;
  return inputCost + outputCost + cacheCost;
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
// SYSTEM PROMPT — lean, no bloat
// ═════════════════════════════════════════════════════════════════════

function getSystemPrompt() {
  const cwd = config.workdir;
  const platform = process.platform;
  const gitBranch = (() => {
    try { return execSync("git branch --show-current", { cwd, encoding: "utf-8" }).trim(); }
    catch { return "N/A"; }
  })();

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
- Keep working until complete. When a subtask is done, start the next.`;
}

// ═════════════════════════════════════════════════════════════════════
// MAIN — THE CORE AGENT LOOP
// ═════════════════════════════════════════════════════════════════════

async function main() {
  const client = new Anthropic();

  // Restore or start fresh
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
    print(`${S.yellow}Resuming from turn ${turnCount} (${messages.length} messages)${S.reset}`);
  }

  // Add user message if starting fresh
  if (messages.length === 0 || !config.continueMode) {
    messages.push({ role: "user", content: config.task });
  }

  const systemPrompt = getSystemPrompt();
  const startTime = Date.now();

  print(`${S.bold}harness.mjs${S.reset} — Direct API agent loop`);
  print(`${S.dim}Model: ${config.model} | Max turns: ${config.maxTurns}${S.reset}`);
  print(`${S.dim}${"─".repeat(64)}${S.reset}`);

  // Signal handling
  process.on("SIGINT", () => {
    print(`\n${S.yellow}Saving state...${S.reset}`);
    saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task });
    print(`Saved. Resume with: node harness.mjs --continue`);
    process.exit(0);
  });

  // ── THE LOOP ──
  while (turnCount < config.maxTurns) {
    turnCount++;

    if (totalCost >= config.maxCostUsd) {
      print(`${S.red}Cost limit reached: ${fmt(totalCost)}${S.reset}`);
      break;
    }

    const turnStart = Date.now();
    log(`Turn ${turnCount}: ${messages.length} messages`);

    // ── API call ──
    let response;
    try {
      response = await client.messages.create({
        model: config.model,
        max_tokens: config.maxTokens,
        system: systemPrompt,
        messages,
        tools: TOOLS,
        // Prompt caching: mark system prompt for caching
        // (requires beta header for extended TTL)
      });
    } catch (e) {
      // Rate limit — wait and retry
      if (e.status === 429) {
        const retryAfter = e.headers?.["retry-after"];
        const waitSec = retryAfter ? parseInt(retryAfter) : 60;
        print(`${S.yellow}Rate limited. Waiting ${waitSec}s...${S.reset}`);
        await new Promise(r => setTimeout(r, waitSec * 1000));
        turnCount--; // retry
        continue;
      }

      // Overloaded — backoff and retry
      if (e.status === 529) {
        print(`${S.yellow}Overloaded. Waiting 30s...${S.reset}`);
        await new Promise(r => setTimeout(r, 30000));
        turnCount--;
        continue;
      }

      print(`${S.red}API Error: ${e.message}${S.reset}`);
      break;
    }

    // ── Track usage ──
    const usage = response.usage || {};
    const turnCost = calculateCost(usage, config.model);
    totalCost += turnCost;

    const turnDuration = Date.now() - turnStart;

    // ── Process response ──
    const toolUseBlocks = response.content.filter(b => b.type === "tool_use");
    const textBlocks = response.content.filter(b => b.type === "text");

    // Show text output
    for (const block of textBlocks) {
      if (block.text.trim()) {
        const preview = block.text.slice(0, 300);
        print(`${S.dim}${preview}${block.text.length > 300 ? "..." : ""}${S.reset}`);
      }
    }

    // Show turn stats
    print(`${S.blue}[Turn ${turnCount}]${S.reset} ` +
      `${S.dim}tok:${usage.input_tokens}→${usage.output_tokens} ` +
      `cache:${usage.cache_read_input_tokens || 0} ` +
      `cost:${fmt(turnCost)} (${fmt(totalCost)} total) ` +
      `${turnDuration}ms${S.reset}`);

    // ── No tool calls → model is done ──
    if (toolUseBlocks.length === 0) {
      print(`\n${S.green}${S.bold}Model finished.${S.reset} ${S.dim}(${response.stop_reason})${S.reset}`);
      break;
    }

    // ── Execute tools ──
    // Add assistant message to history
    messages.push({ role: "assistant", content: response.content });

    const toolResults = [];
    for (const toolUse of toolUseBlocks) {
      totalToolCalls++;
      const toolName = toolUse.name;
      const toolInput = toolUse.input;

      if (config.verbose) {
        print(`  ${S.cyan}tool: ${toolName}${S.reset} ${S.dim}${JSON.stringify(toolInput).slice(0, 100)}${S.reset}`);
      } else {
        print(`  ${S.cyan}tool: ${toolName}${S.reset}`);
      }

      const result = executeTool(toolName, toolInput);

      toolResults.push({
        type: "tool_result",
        tool_use_id: toolUse.id,
        content: result.slice(0, 50000), // Cap result size
      });

      log(`Tool ${toolName}: ${result.slice(0, 200)}`);
    }

    // Add tool results as user message
    messages.push({ role: "user", content: toolResults });

    // ── Save state periodically ──
    if (turnCount % 5 === 0) {
      saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task });
    }
  }

  // ── Final report ──
  const elapsed = Math.round((Date.now() - startTime) / 1000);
  print(`\n${S.dim}${"─".repeat(64)}${S.reset}`);
  print(`${S.bold}Done${S.reset}`);
  print(`  Turns:      ${turnCount}`);
  print(`  Tool calls: ${totalToolCalls}`);
  print(`  Cost:       ${fmt(totalCost)}`);
  print(`  Duration:   ${elapsed}s`);
  print(`  Messages:   ${messages.length}`);

  saveState({ messages, totalCost, turnCount, totalToolCalls, task: config.task, completed: true });
}

main().catch(e => {
  console.error(`${S.red}Fatal: ${e.message}${S.reset}`);
  process.exit(1);
});
