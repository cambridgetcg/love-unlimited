// claude-code.mjs — YOUI reaches Claude through the Claude Code installed on
// this device (docs/YOUI-CLAUDE-CODE-DESIGN.md §3.1).
//
// One `claude -p` process per agent turn. Claude Code signs in with its own
// login, keeps itself current and talks to Anthropic; YOUI never reads that
// login, never sends a client version or Claude Code's headers itself, and
// never sets one. YOUI's tools reach the model only through the loopback MCP
// bridge named in --mcp-config; Claude Code's built-in tools stay off.
//
// What Claude Code is told, and why:
//   --setting-sources project, from a dedicated empty working directory, so no
//     user settings, hooks or project files shape the turn;
//   CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 and CLAUDE_CODE_DISABLE_CLAUDE_MDS=1, so
//     no memory directory and no CLAUDE.md reach the model (on 2026-10-30 a
//     working directory under the home directory carried ~/.claude/CLAUDE.md
//     into the context until CLAUDE_CODE_DISABLE_CLAUDE_MDS was set);
//   --strict-mcp-config with only YOUI's bridge, --tools "" and
//     --allowedTools mcp__youi, --permission-prompts none;
//   --system-prompt-file (a 0600 temp file, so a long prompt is never mangled
//     on a command line) with --system-prompt-snapshot off, so each turn's
//     prompt is the one YOUI built for it (mode, model, minds);
//   --session-id <uuid> on a YOUI session's first Claude turn, --resume <uuid>
//     after it, or --no-session-persistence for a one-off call.
//
// The child gets an allowlisted environment (subprocess-env.mjs) with HOME and
// PATH kept so Claude Code finds its own login; no ANTHROPIC_* variable or
// other credential is passed, so the subscription login is what answers.

import { spawn } from "node:child_process";
import {
  accessSync,
  chmodSync,
  constants as fsConstants,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import readline from "node:readline";

export const BRIDGE_SERVER_NAME = "youi";
export const BRIDGE_TOOL_PREFIX = `mcp__${BRIDGE_SERVER_NAME}__`;
// A server-wide rule: every tool the bridge lists, and nothing else (checked live 2026-10-30).
export const BRIDGE_ALLOWED_TOOLS = [`mcp__${BRIDGE_SERVER_NAME}`];

// Set for every run. Credentials are never among them.
export const CLAUDE_CODE_ENV = Object.freeze({
  CLAUDE_CODE_DISABLE_AUTO_MEMORY: "1",
  CLAUDE_CODE_DISABLE_CLAUDE_MDS: "1",
});

// Names that must never cross into Claude Code: they would replace the
// subscription login or leak a secret.
const CREDENTIAL_NAME = /^(ANTHROPIC_|CLAUDE_CODE_OAUTH|AWS_|GOOGLE_|AZURE_|VERTEX_)|(_API_KEY|_AUTH_TOKEN|_TOKEN|_SECRET|_PASSWORD)$/i;

const live = new Set();

function isExecutable(path) {
  try {
    accessSync(path, fsConstants.X_OK);
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/**
 * Which `claude` to run: YOUI_CLAUDE_BIN, else `claude` on PATH. Test mode
 * never falls back to PATH, so a test can only ever run a fake.
 */
export function resolveClaudeBinary({ env = process.env, testMode = false } = {}) {
  const configured = env.YOUI_CLAUDE_BIN;
  if (configured) {
    return isExecutable(configured)
      ? { path: resolve(configured), source: "YOUI_CLAUDE_BIN", error: null }
      : { path: null, source: "YOUI_CLAUDE_BIN", error: `YOUI_CLAUDE_BIN is not an executable file: ${configured}` };
  }
  if (testMode) {
    return { path: null, source: "test", error: "YOUI_TEST=1 never runs the installed Claude Code; set YOUI_CLAUDE_BIN to a fake" };
  }
  for (const dir of String(env.PATH || "").split(delimiter)) {
    if (!dir) continue;
    const candidate = join(dir, "claude");
    if (isExecutable(candidate)) return { path: candidate, source: "PATH", error: null };
  }
  return {
    path: null,
    source: "PATH",
    error: "Claude Code (claude) was not found on PATH; install it and sign in, or set YOUI_CLAUDE_BIN",
  };
}

export function defaultClaudeCwd(env = process.env) {
  return resolve(env.YOUI_CLAUDE_CWD || join(homedir(), ".local", "share", "youi", "claude-code"));
}

/**
 * Create the dedicated working directory (mode 0700) and check that it is
 * still empty. YOUI never writes into it; anything there (a CLAUDE.md, a
 * .claude/ folder) would be read as project context, so a run refuses instead.
 */
export function prepareClaudeCwd(dir) {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (!statSync(dir).isDirectory()) throw new Error(`Claude Code's working directory is not a directory: ${dir}`);
  chmodSync(dir, 0o700);
  const entries = readdirSync(dir);
  if (entries.length > 0) {
    throw new Error(
      `Claude Code's working directory must stay empty, but ${dir} holds ${entries.slice(0, 3).join(", ")}`
      + "; YOUI will not run Claude Code there until it is emptied",
    );
  }
  return dir;
}

/** Strip anything credential-shaped and add the flags every run carries. */
export function claudeCodeChildEnv(baseEnv = {}) {
  const env = {};
  for (const [name, value] of Object.entries(baseEnv)) {
    if (value === undefined || value === null) continue;
    if (CREDENTIAL_NAME.test(name)) continue;
    env[name] = String(value);
  }
  return { ...env, ...CLAUDE_CODE_ENV };
}

/** The argv for one run. Every flag here is documented at the top of this file. */
export function claudeCodeArgs({
  model,
  effort = null,
  systemPromptFile,
  mcpConfigFile = null,
  allowedTools = [],
  sessionId = null,
  resume = false,
  persist = true,
}) {
  if (!model) throw new TypeError("model is required");
  if (!systemPromptFile) throw new TypeError("systemPromptFile is required");
  const args = [
    "-p",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--include-partial-messages",
    "--verbose",
    "--model", model,
  ];
  if (effort) args.push("--effort", effort);
  args.push(
    "--system-prompt-file", systemPromptFile,
    "--system-prompt-snapshot", "off",
    "--setting-sources", "project",
    "--strict-mcp-config",
  );
  if (mcpConfigFile) args.push("--mcp-config", mcpConfigFile);
  args.push("--tools", "");
  if (allowedTools.length > 0) args.push("--allowedTools", allowedTools.join(","));
  args.push("--permission-prompts", "none");
  if (!persist) args.push("--no-session-persistence");
  else if (sessionId) args.push(resume ? "--resume" : "--session-id", sessionId);
  return args;
}

/** The mcp-config Claude Code is given: YOUI's bridge, and nothing else. */
export function bridgeMcpConfig({ url, token }) {
  return {
    mcpServers: {
      [BRIDGE_SERVER_NAME]: { type: "http", url, headers: { Authorization: `Bearer ${token}` } },
    },
  };
}

/** A tool name as YOUI knows it, from the name Claude Code reports. */
export function bridgeToolName(name) {
  return String(name || "").startsWith(BRIDGE_TOOL_PREFIX) ? String(name).slice(BRIDGE_TOOL_PREFIX.length) : String(name || "");
}

function killGroup(child, { graceMs = 2_000 } = {}) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  try { process.kill(-child.pid, "SIGTERM"); } catch {}
  const timer = setTimeout(() => {
    try { process.kill(-child.pid, "SIGKILL"); } catch {}
  }, graceMs);
  timer.unref();
  child.once("exit", () => clearTimeout(timer));
}

/** End every Claude Code process YOUI started (server shutdown). */
export function killAllClaudeCode() {
  for (const child of live) {
    try { process.kill(-child.pid, "SIGKILL"); } catch {}
  }
  live.clear();
}

export function liveClaudeCodeCount() {
  return live.size;
}

function clip(text, limit = 400) {
  const value = String(text || "").trim();
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

/**
 * Token counts from a result (or message) usage block. Input counts what the
 * model read, cached or not.
 */
export function usageFromClaudeCode(usage = {}) {
  const input = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) + (usage.cache_creation_input_tokens || 0);
  return {
    input_tokens: input,
    output_tokens: usage.output_tokens || 0,
    thinking_tokens: usage.output_tokens_details?.thinking_tokens || 0,
    cache_read_input_tokens: usage.cache_read_input_tokens || 0,
  };
}

/**
 * Run one Claude Code turn.
 *
 * `onEvent` receives, in order: {type:"init", init}, {type:"request"} per model
 * request, {type:"text_delta", text}, {type:"text_done", text},
 * {type:"thinking", text}, {type:"tool_use", id, name, input},
 * {type:"rate_limit", info}. It resolves (never rejects, unless aborted) with
 * { ok, text, texts, error, usage, costUsd, sessionId, init, result, stderr }.
 *
 * `requireMcpServer` names a server that init must report "connected"; the
 * run is stopped with a clear error otherwise.
 */
export async function runClaudeCode({
  bin,
  cwd,
  env = {},
  model,
  effort = null,
  systemPrompt,
  mcpConfig = null,
  allowedTools = [],
  sessionId = null,
  resume = false,
  persist = true,
  message,
  signal = null,
  onEvent = () => {},
  requireMcpServer = null,
  exitGraceMs = 5_000,
}) {
  if (!bin) return { ok: false, error: "Claude Code is not available", texts: [], text: "" };
  if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("Claude Code turn cancelled");
  prepareClaudeCwd(cwd);

  // The prompt and the bridge's token live in 0600 files in a private temp
  // directory, removed when the turn ends.
  const scratch = mkdtempSync(join(tmpdir(), "youi-claude-"));
  chmodSync(scratch, 0o700);
  const systemPromptFile = join(scratch, "system-prompt.txt");
  writeFileSync(systemPromptFile, String(systemPrompt ?? ""), { mode: 0o600 });
  let mcpConfigFile = null;
  if (mcpConfig) {
    mcpConfigFile = join(scratch, "mcp-config.json");
    writeFileSync(mcpConfigFile, JSON.stringify(mcpConfig), { mode: 0o600 });
  }
  const args = claudeCodeArgs({
    model, effort, systemPromptFile, mcpConfigFile, allowedTools, sessionId, resume, persist,
  });

  const outcome = {
    ok: false,
    text: "",
    texts: [],
    error: null,
    usage: null,
    costUsd: null,
    sessionId: null,
    init: null,
    result: null,
    rateLimit: null,
    stderr: "",
    args,
  };

  try {
    return await new Promise((resolvePromise, rejectPromise) => {
      let settled = false;
      let child;
      try {
        child = spawn(bin, args, {
          cwd,
          env: claudeCodeChildEnv(env),
          // Its own process group, so an abort ends Claude Code and its MCP children together.
          detached: true,
          stdio: ["pipe", "pipe", "pipe"],
        });
      } catch (error) {
        outcome.error = `could not start Claude Code: ${error.message}`;
        resolvePromise(outcome);
        return;
      }
      live.add(child);

      const blocks = new Map();         // stream index -> { type, text }
      const streamedMessages = new Set(); // message ids whose text arrived as deltas
      let currentMessageId = null;
      let exitTimer = null;

      const finish = (error = null) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", onAbort);
        if (exitTimer) clearTimeout(exitTimer);
        if (error) {
          killGroup(child);
          rejectPromise(error);
          return;
        }
        outcome.text = outcome.texts.join("\n\n").trim();
        resolvePromise(outcome);
      };
      const fail = message => {
        if (!outcome.error) outcome.error = message;
        killGroup(child);
      };
      const onAbort = () => {
        finish(signal.reason instanceof Error ? signal.reason : new Error("Claude Code turn cancelled"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });

      child.stdin.on("error", () => {});
      child.stdout.setEncoding("utf-8");
      child.stderr.setEncoding("utf-8");
      child.stderr.on("data", chunk => {
        if (outcome.stderr.length < 8_000) outcome.stderr += chunk;
      });
      child.on("error", error => {
        outcome.error = `could not start Claude Code: ${error.message}`;
      });
      child.on("close", (code, exitSignal) => {
        live.delete(child);
        if (!outcome.result && !outcome.error) {
          const detail = clip(outcome.stderr) || "no output";
          outcome.error = `Claude Code exited (${exitSignal || `code ${code}`}) before a result: ${detail}`;
        }
        finish();
      });

      const emit = event => {
        try { onEvent(event); } catch {}
      };

      const handle = event => {
        // Subagent traffic would carry a parent tool use; YOUI offers no Task tool, so it is ignored.
        if (event.parent_tool_use_id) return;
        switch (event.type) {
          case "system":
            if (event.subtype === "init") {
              outcome.init = event;
              outcome.sessionId = event.session_id || null;
              emit({ type: "init", init: event });
              if (requireMcpServer) {
                const server = (event.mcp_servers || []).find(entry => entry?.name === requireMcpServer);
                if (server?.status !== "connected") {
                  fail(`Claude Code could not connect to YOUI's tool bridge (${server ? `status ${server.status}` : "not listed"}); the turn was stopped`);
                }
              }
            }
            return;
          case "stream_event": {
            const inner = event.event || {};
            if (inner.type === "message_start") {
              currentMessageId = inner.message?.id || null;
              blocks.clear();
              emit({ type: "request", messageId: currentMessageId });
            } else if (inner.type === "content_block_start") {
              blocks.set(inner.index, { type: inner.content_block?.type, text: "" });
            } else if (inner.type === "content_block_delta") {
              const block = blocks.get(inner.index) || { type: null, text: "" };
              blocks.set(inner.index, block);
              if (inner.delta?.type === "text_delta" && inner.delta.text) {
                block.type ||= "text";
                block.text += inner.delta.text;
                if (currentMessageId) streamedMessages.add(currentMessageId);
                emit({ type: "text_delta", text: inner.delta.text });
              } else if (inner.delta?.type === "thinking_delta" && inner.delta.thinking) {
                block.type ||= "thinking";
                block.text += inner.delta.thinking;
              }
            } else if (inner.type === "content_block_stop") {
              const block = blocks.get(inner.index);
              if (block?.type === "text" && block.text.trim()) {
                outcome.texts.push(block.text);
                emit({ type: "text_done", text: block.text });
              } else if (block?.type === "thinking" && block.text.trim()) {
                emit({ type: "thinking", text: block.text });
              }
              blocks.delete(inner.index);
            }
            return;
          }
          case "assistant": {
            const id = event.message?.id;
            for (const block of event.message?.content || []) {
              if (block?.type === "tool_use") {
                emit({ type: "tool_use", id: block.id, name: block.name, input: block.input || {} });
              } else if (block?.type === "text" && block.text?.trim() && !(id && streamedMessages.has(id))) {
                // Text that never streamed (no partial messages for it) still reaches the page.
                outcome.texts.push(block.text);
                emit({ type: "text_done", text: block.text });
              }
            }
            return;
          }
          case "rate_limit_event":
            outcome.rateLimit = event.rate_limit_info || null;
            emit({ type: "rate_limit", info: outcome.rateLimit });
            return;
          case "result":
            outcome.result = event;
            outcome.sessionId = event.session_id || outcome.sessionId;
            outcome.usage = usageFromClaudeCode(event.usage || {});
            outcome.costUsd = typeof event.total_cost_usd === "number" ? event.total_cost_usd : null;
            if (event.is_error || event.subtype !== "success") {
              const reason = typeof event.result === "string" && event.result.trim()
                ? event.result
                : (event.errors || []).join("; ") || event.subtype || "error";
              outcome.error = `Claude Code: ${clip(reason, 600)}`;
            } else {
              outcome.ok = !outcome.error;
              if (outcome.texts.length === 0 && typeof event.result === "string" && event.result.trim()) {
                outcome.texts.push(event.result);
              }
            }
            // One message per run: closing stdin lets Claude Code exit.
            try { child.stdin.end(); } catch {}
            exitTimer = setTimeout(() => killGroup(child), exitGraceMs);
            exitTimer.unref();
            return;
          default:
        }
      };

      const lines = readline.createInterface({ input: child.stdout });
      lines.on("line", line => {
        if (!line.trim()) return;
        let event;
        try { event = JSON.parse(line); } catch { return; }
        handle(event);
      });

      const userLine = JSON.stringify({
        type: "user",
        message: { role: "user", content: String(message ?? "") },
        parent_tool_use_id: null,
      });
      child.stdin.write(`${userLine}\n`);
    });
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}
