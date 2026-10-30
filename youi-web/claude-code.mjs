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
//     after it, or --no-session-persistence for a one-off call;
//   --max-turns for an agent turn, so one message cannot run without end.
//
// The child gets an allowlisted environment (subprocess-env.mjs) with HOME and
// PATH kept so Claude Code finds its own login; no ANTHROPIC_* variable or
// other credential is passed, so the subscription login is what answers.
// CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1 keeps Claude Code's bundled skills out,
// and a message that starts with "/" is sent as "\/…", because Claude Code
// runs a leading "/" in a stream-json user message as a slash command (checked
// live on 2026-10-30: "/context" ran locally and reached no model).

import { execFile, spawn } from "node:child_process";
import {
  accessSync,
  chmodSync,
  constants as fsConstants,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { delimiter, isAbsolute, join, resolve } from "node:path";
import readline from "node:readline";

export const BRIDGE_SERVER_NAME = "youi";
export const BRIDGE_TOOL_PREFIX = `mcp__${BRIDGE_SERVER_NAME}__`;
// A server-wide rule: every tool the bridge lists, and nothing else (checked live 2026-10-30).
export const BRIDGE_ALLOWED_TOOLS = [`mcp__${BRIDGE_SERVER_NAME}`];

// Set for every run. Credentials are never among them.
export const CLAUDE_CODE_ENV = Object.freeze({
  CLAUDE_CODE_DISABLE_AUTO_MEMORY: "1",
  CLAUDE_CODE_DISABLE_BUNDLED_SKILLS: "1",
  CLAUDE_CODE_DISABLE_CLAUDE_MDS: "1",
});

// Names that must never cross into Claude Code: they would replace the
// subscription login or leak a secret.
const CREDENTIAL_NAME = /^(ANTHROPIC_|CLAUDE_CODE_OAUTH|AWS_|GOOGLE_|AZURE_|VERTEX_)|(_API_KEY|_AUTH_TOKEN|_TOKEN|_SECRET|_PASSWORD)$/i;

// Running Claude Code processes, each with its turn's scratch directory, so a
// shutdown can end them and remove what they were given.
const live = new Map();
const liveScratch = new Set();
const SCRATCH_PREFIX = `youi-claude-p${process.pid}-`;

function isExecutable(path) {
  try {
    accessSync(path, fsConstants.X_OK);
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/**
 * Which `claude` to run: YOUI_CLAUDE_BIN, else `claude` in an absolute PATH
 * entry, else ~/.local/bin/claude (where Claude Code's native installer puts
 * it; a launch agent's PATH often lacks it). Relative PATH entries are
 * skipped, since Claude Code runs from its own working directory. Test mode
 * never falls back to PATH or ~/.local/bin, so a test can only ever run a fake.
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
    if (!dir || !isAbsolute(dir)) continue;
    const candidate = resolve(dir, "claude");
    if (isExecutable(candidate)) return { path: candidate, source: "PATH", error: null };
  }
  const native = resolve(env.HOME || homedir(), ".local", "bin", "claude");
  if (isExecutable(native)) return { path: native, source: "~/.local/bin", error: null };
  return {
    path: null,
    source: "PATH",
    error: "Claude Code (claude) was not found on PATH or in ~/.local/bin; install it and sign in, or set YOUI_CLAUDE_BIN",
  };
}

export function defaultClaudeCwd(env = process.env) {
  return resolve(env.YOUI_CLAUDE_CWD || join(homedir(), ".local", "share", "youi", "claude-code"));
}

/**
 * Where Claude Code keeps the transcripts of conversations run from `cwd`:
 * <config dir>/projects/<the real path with every non-alphanumeric character
 * as "-">, as Claude Code 2.1.314 names it (longer than 200 characters, it
 * adds a hash YOUI does not compute).
 */
export function claudeCodeTranscriptDir(cwd, env = process.env) {
  let real = resolve(cwd);
  try { real = realpathSync(real); } catch {}
  const slug = real.replace(/[^a-zA-Z0-9]/g, "-");
  const configDir = env.CLAUDE_CONFIG_DIR ? resolve(env.CLAUDE_CONFIG_DIR) : join(env.HOME || homedir(), ".claude");
  return join(configDir, "projects", slug.length <= 200 ? slug : `${slug.slice(0, 200)}-…`);
}

/**
 * Create the dedicated working directory (mode 0700) and check that it is
 * still empty. YOUI never writes into it; anything there (a CLAUDE.md, a
 * .claude/ folder) would be read as project context, so a run refuses instead.
 */
export function prepareClaudeCwd(dir) {
  const refuse = message => Object.assign(new Error(message), { claudeCwd: dir });
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (!statSync(dir).isDirectory()) throw refuse(`Claude Code's working directory is not a directory: ${dir}`);
  chmodSync(dir, 0o700);
  const entries = readdirSync(dir);
  if (entries.length > 0) {
    throw refuse(
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
  maxTurns = null,
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
  if (Number.isInteger(maxTurns) && maxTurns > 0) args.push("--max-turns", String(maxTurns));
  if (!persist) args.push("--no-session-persistence");
  else if (sessionId) args.push(resume ? "--resume" : "--session-id", sessionId);
  return args;
}

/**
 * The mcp-config Claude Code is given: YOUI's bridge, and nothing else.
 * `alwaysLoad` keeps its tools out of tool search and makes Claude Code wait
 * for the bridge to connect before the first request (2.1.314's schema; MCP
 * startup is otherwise non-blocking, and init can report "pending").
 */
export function bridgeMcpConfig({ url, token }) {
  return {
    mcpServers: {
      [BRIDGE_SERVER_NAME]: { type: "http", url, headers: { Authorization: `Bearer ${token}` }, alwaysLoad: true },
    },
  };
}

/**
 * A message as Claude Code is given it. Claude Code runs a leading "/" as a
 * slash command (a /clear or /compact would drop its context while the page
 * still shows it), so that slash is sent as "\/": the model still reads the
 * person's text, and Claude Code runs nothing.
 */
export function escapeLeadingSlash(text) {
  return String(text ?? "").replace(/^(\s*)\//, "$1\\/");
}

/** A tool name as YOUI knows it, from the name Claude Code reports. */
export function bridgeToolName(name) {
  return String(name || "").startsWith(BRIDGE_TOOL_PREFIX) ? String(name).slice(BRIDGE_TOOL_PREFIX.length) : String(name || "");
}

// The whole process group, whether or not its leader is still running: a
// child Claude Code started can outlive it and hold the output pipes open.
function killGroup(child, { graceMs = 2_000 } = {}) {
  if (!child?.pid || child.groupKilled) return;
  child.groupKilled = true;
  try { process.kill(-child.pid, "SIGTERM"); } catch {}
  const timer = setTimeout(() => {
    try { process.kill(-child.pid, "SIGKILL"); } catch {}
  }, graceMs);
  timer.unref();
}

function removeScratch(dir) {
  if (!dir) return;
  liveScratch.delete(dir);
  try { rmSync(dir, { recursive: true, force: true }); } catch {}
}

/** End every Claude Code process YOUI started and remove their scratch files (server shutdown). */
export function killAllClaudeCode() {
  for (const child of live.keys()) {
    try { process.kill(-child.pid, "SIGKILL"); } catch {}
  }
  live.clear();
  for (const dir of [...liveScratch]) removeScratch(dir);
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

/**
 * Remove turn scratch directories a YOUI process left behind (it crashed or
 * was killed before its turn ended): ours by name, owned by this user, older
 * than `startedAt`, and whose process is gone. Returns how many were removed.
 */
export function sweepStaleClaudeScratch({ dir = tmpdir(), startedAt = Date.now() - process.uptime() * 1000 } = {}) {
  let entries;
  try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return 0; }
  let removed = 0;
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const owned = entry.name.match(/^youi-claude-p(\d+)-[A-Za-z0-9]{6}$/);
    // Before the pid was in the name: only the two files a turn writes.
    const legacy = /^youi-claude-[A-Za-z0-9]{6}$/.test(entry.name);
    if (!owned && !legacy) continue;
    const path = join(dir, entry.name);
    try {
      const stat = lstatSync(path);
      if (typeof process.getuid === "function" && stat.uid !== process.getuid()) continue;
      if (stat.mtimeMs >= startedAt) continue;
      if (owned) {
        const pid = Number(owned[1]);
        if (pid === process.pid || pidAlive(pid)) continue;
      } else if (!readdirSync(path).every(name => name === "system-prompt.txt" || name === "mcp-config.json")) {
        continue;
      }
      rmSync(path, { recursive: true, force: true });
      removed += 1;
    } catch {}
  }
  return removed;
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
 * What one result adds to a conversation's spend. Claude Code's
 * `total_cost_usd` and `modelUsage` are running totals for the whole Claude
 * Code session, and a --resume continues from what its transcript saved
 * (2.1.314's schema; checked live on 2026-10-30), so a turn's cost is the
 * total less the last total this conversation saw. Per model, the figures
 * Claude Code counted since its previous result are used when present
 * (`model_usage_since_last_result`), else `modelUsage` less the last one.
 *
 * `baseline` is { costUsd, modelUsage } from the conversation's previous
 * result, or null for a new conversation or a one-off run. Returns
 * { costUsd, byModel, next }: byModel maps a model to { inputTokens,
 * outputTokens, costUsd } (input counts what the model read, cached or not),
 * or is null when the result names no model; `next` is the new baseline.
 */
export function claudeCodeSpend(result, baseline = null) {
  const total = typeof result?.total_cost_usd === "number" ? result.total_cost_usd : null;
  const lastCost = typeof baseline?.costUsd === "number" ? baseline.costUsd : 0;
  const costUsd = total === null ? null : Math.max(0, total - lastCost);
  const tokens = entry => ({
    input: (entry?.inputTokens || 0) + (entry?.cacheReadInputTokens || 0) + (entry?.cacheCreationInputTokens || 0),
    output: entry?.outputTokens || 0,
    cost: typeof entry?.costUSD === "number" ? entry.costUSD : 0,
  });
  let byModel = null;
  const since = result?.model_usage_since_last_result;
  const cumulative = result?.modelUsage;
  if (since && typeof since === "object") {
    byModel = {};
    for (const [model, entry] of Object.entries(since)) {
      const now = tokens(entry);
      byModel[model] = { inputTokens: now.input, outputTokens: now.output, costUsd: now.cost };
    }
  } else if (cumulative && typeof cumulative === "object") {
    byModel = {};
    for (const [model, entry] of Object.entries(cumulative)) {
      const now = tokens(entry);
      const before = tokens(baseline?.modelUsage?.[model]);
      byModel[model] = {
        inputTokens: Math.max(0, now.input - before.input),
        outputTokens: Math.max(0, now.output - before.output),
        costUsd: Math.max(0, now.cost - before.cost),
      };
    }
  }
  return {
    costUsd,
    byModel,
    next: {
      costUsd: total === null ? lastCost : total,
      modelUsage: cumulative && typeof cumulative === "object" ? cumulative : (baseline?.modelUsage || null),
    },
  };
}

function messageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  const parts = [];
  for (const block of content) {
    if (block?.type === "text" && typeof block.text === "string" && block.text.trim()) parts.push(block.text.trim());
    else if (block?.type === "tool_use" && block.name) parts.push(`[called the tool ${block.name}]`);
  }
  return parts.join("\n\n");
}

/**
 * The earlier turns of a YOUI chat, as a plain transcript for the first
 * message of a new Claude Code conversation: text only (tool calls are named,
 * their inputs and results are not carried), the most recent `maxMessages`
 * within `maxChars`, with a visible line saying how many older ones were left
 * out. Returns { text, included, omitted }, or null when there is nothing.
 */
export function transcriptPreamble(messages, { maxMessages = 30, maxChars = 40_000 } = {}) {
  const withText = (messages || [])
    .map(message => ({ role: message?.role === "assistant" ? "assistant" : "user", text: messageText(message) }))
    .filter(message => message.text);
  if (withText.length === 0) return null;
  const kept = [];
  let chars = 0;
  for (let index = withText.length - 1; index >= 0 && kept.length < maxMessages; index -= 1) {
    let { role, text } = withText[index];
    if (chars + text.length > maxChars) {
      if (kept.length > 0) break;
      text = `${text.slice(0, maxChars)}\n[… clipped]`;
    }
    kept.unshift({ role, text });
    chars += text.length;
  }
  const omitted = withText.length - kept.length;
  const lines = [
    "[YOUI] This is a new Claude conversation. Earlier turns of this chat, which it has not seen, follow as a plain"
      + " transcript for context. It is text only: tools that were called are named, but their inputs and results are not included.",
  ];
  if (omitted > 0) lines.push(`[YOUI] ${omitted} earlier message${omitted === 1 ? "" : "s"} omitted.`);
  for (const { role, text } of kept) lines.push(`--- ${role} ---\n${text}`);
  lines.push("[YOUI] End of the earlier transcript. The person's new message follows.");
  return { text: `${lines.join("\n\n")}\n\n`, included: kept.length, omitted };
}

/**
 * Run one Claude Code turn.
 *
 * `onEvent` receives, in order: {type:"init", init}, {type:"request"} per model
 * request, {type:"text_delta", text}, {type:"text_done", text},
 * {type:"thinking", text}, {type:"tool_use", id, name, input},
 * {type:"rate_limit", info}. It resolves with
 * { ok, text, texts, error, usage, costUsd, sessionId, init, result, stderr };
 * it rejects only when `signal` aborts, and then only after the process group
 * has gone (or `abortGraceMs` has passed), so the next --resume cannot meet
 * the old process. A setup failure (a working directory that is not empty, a
 * scratch file that cannot be written) resolves with `error` saying so.
 *
 * `requireMcpServer` names a server that must not report "failed" or
 * "needs-auth" at init; the run is stopped with a clear error otherwise.
 * `timeoutMs` stops a run that has not finished by then, with an error.
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
  maxTurns = null,
  message,
  signal = null,
  onEvent = () => {},
  requireMcpServer = null,
  exitGraceMs = 5_000,
  abortGraceMs = 3_000,
  timeoutMs = null,
}) {
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
    args: null,
  };
  if (!bin) {
    outcome.error = "Claude Code is not available";
    return outcome;
  }
  if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("Claude Code turn cancelled");

  // The prompt and the bridge's token live in 0600 files in a private temp
  // directory, removed when the turn ends (or the server stops).
  let scratch = null;
  try {
    prepareClaudeCwd(cwd);
    scratch = mkdtempSync(join(tmpdir(), SCRATCH_PREFIX));
    liveScratch.add(scratch);
    chmodSync(scratch, 0o700);
    const systemPromptFile = join(scratch, "system-prompt.txt");
    writeFileSync(systemPromptFile, String(systemPrompt ?? ""), { mode: 0o600 });
    let mcpConfigFile = null;
    if (mcpConfig) {
      mcpConfigFile = join(scratch, "mcp-config.json");
      writeFileSync(mcpConfigFile, JSON.stringify(mcpConfig), { mode: 0o600 });
    }
    outcome.args = claudeCodeArgs({
      model, effort, systemPromptFile, mcpConfigFile, allowedTools, sessionId, resume, persist, maxTurns,
    });
  } catch (error) {
    removeScratch(scratch);
    outcome.error = error?.claudeCwd
      ? error.message
      : `YOUI could not prepare the Claude Code run: ${clip(error?.message || error, 300)}`;
    return outcome;
  }
  const args = outcome.args;

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
      live.set(child, scratch);
      const closed = new Promise(resolveClosed => child.once("close", resolveClosed));

      const blocks = new Map();         // stream index -> { type, text }
      const streamedMessages = new Set(); // message ids whose text arrived as deltas
      let currentMessageId = null;
      let exitTimer = null;
      let turnTimer = null;
      let syntheticText = null;         // an API-error or refusal message Claude Code wrote itself

      const finish = (error = null) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", onAbort);
        if (exitTimer) clearTimeout(exitTimer);
        if (turnTimer) clearTimeout(turnTimer);
        if (error) {
          killGroup(child);
          // Settle once the group has gone, or after the grace, whichever is first.
          const grace = new Promise(resolveGrace => setTimeout(resolveGrace, abortGraceMs).unref());
          Promise.race([closed, grace]).then(() => rejectPromise(error));
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
      if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
        turnTimer = setTimeout(() => {
          const minutes = timeoutMs >= 60_000 ? `${Math.round(timeoutMs / 60_000)} min` : `${Math.round(timeoutMs / 1000)} s`;
          fail(`Claude Code did not finish within ${minutes}; YOUI stopped the turn`);
        }, timeoutMs);
        turnTimer.unref();
      }

      child.stdin.on("error", () => {});
      child.stdout.setEncoding("utf-8");
      child.stderr.setEncoding("utf-8");
      child.stderr.on("data", chunk => {
        if (outcome.stderr.length < 8_000) outcome.stderr += chunk;
      });
      child.on("error", error => {
        outcome.error = `could not start Claude Code: ${error.message}`;
      });
      // Whatever Claude Code started goes with it.
      child.once("exit", () => killGroup(child));
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
                // "pending" is still connecting; only a failure stops the run.
                if (!server || server.status === "failed" || server.status === "needs-auth") {
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
            // Claude Code's own API-error and refusal messages are not the model's answer.
            const synthetic = Boolean(event.error || event.refused_message_id);
            for (const block of event.message?.content || []) {
              if (block?.type === "tool_use") {
                emit({ type: "tool_use", id: block.id, name: block.name, input: block.input || {} });
              } else if (block?.type === "text" && block.text?.trim() && !(id && streamedMessages.has(id))) {
                if (synthetic) {
                  syntheticText ||= block.text.trim();
                  continue;
                }
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
            // A running total for the Claude Code session (claudeCodeSpend).
            outcome.costUsd = typeof event.total_cost_usd === "number" ? event.total_cost_usd : null;
            if (event.is_error || event.subtype !== "success") {
              const reason = typeof event.result === "string" && event.result.trim()
                ? event.result
                : (event.errors || []).join("; ") || syntheticText || event.subtype || "error";
              outcome.error = `Claude Code: ${clip(reason, 600)}`;
            } else if (syntheticText && outcome.texts.length === 0) {
              outcome.error = `Claude Code: ${clip(syntheticText, 600)}`;
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
        message: { role: "user", content: escapeLeadingSlash(message) },
        parent_tool_use_id: null,
      });
      child.stdin.write(`${userLine}\n`);
    });
  } finally {
    removeScratch(scratch);
  }
}

/**
 * `claude --version`, parsed. It makes no model call. Only Claude Code's own
 * format counts: "<x.y.z>[-pre-release] (Claude Code)".
 */
export function readClaudeVersion(bin, { env = {}, timeoutMs = 3_000 } = {}) {
  return new Promise(resolvePromise => {
    if (!bin) {
      resolvePromise({ ok: false, version: null, error: "Claude Code is not available" });
      return;
    }
    execFile(bin, ["--version"], {
      env: claudeCodeChildEnv(env),
      timeout: timeoutMs,
      killSignal: "SIGKILL",
      maxBuffer: 64 * 1024,
      encoding: "utf-8",
    }, (error, stdout) => {
      const match = String(stdout || "").match(/^\s*(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s+\(Claude Code\)\s*$/m);
      if (error && !match) {
        resolvePromise({
          ok: false,
          version: null,
          error: error.killed ? `no answer within ${timeoutMs} ms` : clip(error.message, 200),
        });
        return;
      }
      if (!match) {
        resolvePromise({ ok: false, version: null, error: `printed no Claude Code version: ${clip(stdout, 120)}` });
        return;
      }
      resolvePromise({ ok: true, version: match[1], error: null });
    });
  });
}

/**
 * -1, 0 or 1, comparing versions part by part as numbers. A pre-release
 * ("2.1.280-beta.1") comes before its release, so it does not meet a minimum
 * of "2.1.280".
 */
export function compareVersions(left, right) {
  const split = value => {
    const [core, ...pre] = String(value).split("-");
    return { parts: core.split(".").map(part => Number.parseInt(part, 10) || 0), pre: pre.join("-") };
  };
  const a = split(left);
  const b = split(right);
  for (let index = 0; index < Math.max(a.parts.length, b.parts.length); index += 1) {
    const difference = (a.parts[index] || 0) - (b.parts[index] || 0);
    if (difference !== 0) return difference < 0 ? -1 : 1;
  }
  if (a.pre === b.pre) return 0;
  if (!a.pre) return 1;
  if (!b.pre) return -1;
  return a.pre.localeCompare(b.pre, undefined, { numeric: true }) < 0 ? -1 : 1;
}
