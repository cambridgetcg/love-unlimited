#!/usr/bin/env node
// A stand-in for the `claude` CLI, for tests/test_youi_claude_code.mjs. It
// never touches the network beyond 127.0.0.1 and reads no credential.
//
// Run it through a wrapper that sets FAKE_CLAUDE_LOG (where each invocation
// appends one JSON line per step, with the time), FAKE_CLAUDE_SESSIONS (a
// directory standing in for Claude Code's transcripts) and optionally
// FAKE_CLAUDE_VERSION or FAKE_CLAUDE_VERSION_FILE. It:
//   - answers `--version` with the version in FAKE_CLAUDE_VERSION_FILE when
//     that file exists, else FAKE_CLAUDE_VERSION (default 2.1.314);
//   - records its argv, cwd, environment, the system prompt file and the
//     mcp-config it was given (with both files' modes), then reads one
//     stream-json user message from stdin;
//   - keeps a transcript per --session-id, as Claude Code does: a --resume of
//     an unknown one prints "No conversation found with session ID: <id>" on
//     stderr and as an error result (subtype error_during_execution, zero
//     cost, the text in `errors`) on stdout, exiting 1 before init, as 2.1.314
//     did live on 2026-10-30; a --session-id already used prints "Error:
//     Session ID <id> is already in use." on stderr only (not checked live),
//     exiting 1 before init;
//   - emits init / stream_event / assistant / user / result lines the way
//     Claude Code 2.1.314 did on 2026-10-30, with total_cost_usd and
//     modelUsage as running totals for a kept session (0.0123 a turn), and
//     exits when stdin closes.
//
// The user message steers it, one directive per line (only the part after
// YOUI's carried transcript, whose end marker carries that transcript's nonce):
//   FAKE:list                  tools/list through the bridge; answers with the names
//   FAKE:call <tool> <json>    tools/call through the bridge, as a tool_use block
//   FAKE:call-unannounced <tool> <json>
//                              tools/call through the bridge for a tool use it never reports
//   FAKE:sleep                 starts a sleeping child, records both pids and waits to be killed
//   FAKE:error <text>          Claude Code's own API-error message, then an error result
//   FAKE:error-zeroed          an error result with zero cost and usage, as a crash reports it
//   FAKE:text-then-error <text> the model's own text, then an error result (error_max_turns)
//   FAKE:text-then-sleep <text> the model's own text, then no result: waits to be killed
//   FAKE:refuse                Claude Code's own refusal message and a refusal result
//   FAKE:bridge-down           reports the bridge as failed in init
//   FAKE:bridge-pending        reports the bridge as still connecting in init
//   FAKE:rate-limit-bare       a rate_limit_event with no windows
//   FAKE:die-before-init       keeps the session, then exits 1 before init
//   FAKE:thinking <text>       a thinking block before the answer
//   FAKE:null-line             stdout lines that are JSON but not objects ("null", "42", "[]") before the answer
// and, with any of those, how the process ends:
//   FAKE:ignore-eof            stays running after stdin closes (until killed)
//   FAKE:slow-exit             on SIGTERM, waits 800 ms before exiting
//   FAKE:leave-child           starts a child in its group that holds no pipe, then answers and exits
//   FAKE:detached-holder       starts a process outside its group that holds stdout, then answers and exits
// Anything else is echoed back as "Echo: <message>".

import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { join } from "node:path";
import crypto from "node:crypto";
import readline from "node:readline";

const LOG = process.env.FAKE_CLAUDE_LOG;
const SESSIONS = process.env.FAKE_CLAUDE_SESSIONS;
const VERSION_FILE = process.env.FAKE_CLAUDE_VERSION_FILE;
const VERSION = (VERSION_FILE && existsSync(VERSION_FILE) ? readFileSync(VERSION_FILE, "utf-8").trim() : "")
  || process.env.FAKE_CLAUDE_VERSION || "2.1.314";
const argv = process.argv.slice(2);
const TURN_COST = 0.0123;

function log(entry) {
  if (LOG) appendFileSync(LOG, `${JSON.stringify({ pid: process.pid, at: Date.now(), ...entry })}\n`);
}

if (argv.includes("--version") || argv.includes("-v")) {
  log({ step: "version", argv });
  process.stdout.write(`${VERSION} (Claude Code)\n`);
  process.exit(0);
}

function flag(name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

function fileRecord(path) {
  if (!path) return null;
  try {
    return { path, mode: statSync(path).mode & 0o777, content: readFileSync(path, "utf-8") };
  } catch (error) {
    return { path, error: error.code };
  }
}

const env = { ...process.env };
delete env.FAKE_CLAUDE_LOG;
delete env.FAKE_CLAUDE_VERSION;
delete env.FAKE_CLAUDE_SESSIONS;
delete env.FAKE_CLAUDE_VERSION_FILE;
const sessionId = flag("--session-id") || flag("--resume") || crypto.randomUUID();
const model = flag("--model");
const mcp = fileRecord(flag("--mcp-config"));
const systemPrompt = fileRecord(flag("--system-prompt-file"));
log({ step: "start", argv, cwd: process.cwd(), env, mcp, systemPrompt });

// The transcript registry: { cost, usage } per kept session.
const kept = SESSIONS && !argv.includes("--no-session-persistence") && (flag("--session-id") || flag("--resume"));
const transcript = kept ? join(SESSIONS, `${sessionId}.json`) : null;
if (transcript) {
  mkdirSync(SESSIONS, { recursive: true });
  if (flag("--session-id") && existsSync(transcript)) {
    log({ step: "session-taken" });
    process.stderr.write(`Error: Session ID ${sessionId} is already in use.\n`);
    process.exit(1);
  }
  if (flag("--resume") && !existsSync(transcript)) {
    log({ step: "session-missing" });
    const text = `No conversation found with session ID: ${sessionId}`;
    process.stdout.write(`${JSON.stringify({
      type: "result", subtype: "error_during_execution", duration_ms: 0, duration_api_ms: 0, is_error: true,
      num_turns: 0, stop_reason: null, session_id: sessionId, total_cost_usd: 0,
      usage: { input_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, output_tokens: 0 },
      modelUsage: {}, permission_denials: [], errors: [text],
    })}\n`);
    process.stderr.write(`${text}\n`);
    process.exitCode = 1;
    // As Claude Code does: the output is flushed, then the process exits.
    setTimeout(() => process.exit(1), 10);
    await new Promise(() => {});
  }
  if (flag("--session-id")) writeFileSync(transcript, JSON.stringify({ cost: 0, usage: {} }));
}
const saved = transcript ? JSON.parse(readFileSync(transcript, "utf-8")) : { cost: 0, usage: {} };

const out = event => process.stdout.write(`${JSON.stringify({ session_id: sessionId, ...event })}\n`);

let rpcId = 0;
async function bridge(method, params, { notify = false } = {}) {
  const config = JSON.parse(mcp.content).mcpServers.youi;
  const body = notify
    ? { jsonrpc: "2.0", method, ...(params ? { params } : {}) }
    : { jsonrpc: "2.0", id: rpcId++, method, ...(params ? { params } : {}) };
  const response = await fetch(config.url, {
    method: "POST",
    headers: {
      ...config.headers,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "User-Agent": "fake-claude/test",
    },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}

async function connectBridge() {
  // As 2.1.314 does: a server/discover probe first, then initialize.
  const discover = await bridge("server/discover", {});
  const init = await bridge("initialize", {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: { name: "fake-claude", version: VERSION },
  });
  const initialized = await bridge("notifications/initialized", undefined, { notify: true });
  log({ step: "bridge-connect", discover, init, initialized: initialized.status });
  return init.status === 200;
}

let messageCount = 0;
function textMessage(text) {
  const id = `msg_fake_${++messageCount}`;
  out({ type: "stream_event", event: { type: "message_start", message: { id, model, role: "assistant", content: [], usage: { input_tokens: 3, output_tokens: 1 } } }, parent_tool_use_id: null });
  out({ type: "stream_event", event: { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }, parent_tool_use_id: null });
  const half = Math.ceil(text.length / 2);
  for (const part of [text.slice(0, half), text.slice(half)].filter(Boolean)) {
    out({ type: "stream_event", event: { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: part } }, parent_tool_use_id: null });
  }
  out({ type: "assistant", message: { id, model, role: "assistant", content: [{ type: "text", text }] }, parent_tool_use_id: null });
  out({ type: "stream_event", event: { type: "content_block_stop", index: 0 }, parent_tool_use_id: null });
  out({ type: "stream_event", event: { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 5 } }, parent_tool_use_id: null });
  out({ type: "stream_event", event: { type: "message_stop" }, parent_tool_use_id: null });
}

function result({ text = "", isError = false, stopReason = "end_turn", subtype = "success", zeroed = false } = {}) {
  if (zeroed) {
    // A crash or startup error: zeros, and nothing saved to the transcript.
    out({
      type: "result", subtype: "error_during_execution", is_error: true, num_turns: 0, stop_reason: null,
      total_cost_usd: 0, modelUsage: {}, errors: [text || "crashed"],
      usage: { input_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, output_tokens: 0 },
    });
    return;
  }
  // Running totals for the session, as Claude Code's result states them.
  const total = Number((saved.cost + TURN_COST).toFixed(6));
  const before = saved.usage[model] || {};
  const modelUsage = {
    [model]: {
      inputTokens: (before.inputTokens || 0) + 10,
      cacheReadInputTokens: (before.cacheReadInputTokens || 0) + 1000,
      cacheCreationInputTokens: (before.cacheCreationInputTokens || 0) + 100,
      outputTokens: (before.outputTokens || 0) + 20,
      costUSD: Number(((before.costUSD || 0) + TURN_COST).toFixed(6)),
    },
  };
  if (transcript) writeFileSync(transcript, JSON.stringify({ cost: total, usage: { ...saved.usage, ...modelUsage } }));
  out({
    type: "result",
    subtype,
    is_error: isError,
    result: text,
    stop_reason: stopReason,
    num_turns: messageCount,
    total_cost_usd: total,
    modelUsage,
    usage: {
      input_tokens: 10,
      cache_creation_input_tokens: 100,
      cache_read_input_tokens: 1000,
      output_tokens: 20,
      output_tokens_details: { thinking_tokens: 7 },
    },
  });
}

// YOUI's transcript of turns carried in front of the message is context, not
// directives: only what follows its end marker steers the fake. The marker is
// the one with the nonce the transcript's first line names, so a marker
// quoted inside the transcript (with any other nonce) is not taken for it.
function afterTranscript(full) {
  const nonce = String(full).match(/^\[YOUI ([0-9a-f]{16})\] /)?.[1];
  if (!nonce) return String(full);
  const end = `[YOUI ${nonce}] End of the earlier transcript. The person's new message follows.\n\n`;
  const at = String(full).lastIndexOf(end);
  return at >= 0 ? String(full).slice(at + end.length) : String(full);
}

let holdOnEof = false;
async function answer(full) {
  const message = afterTranscript(full);
  const lines = String(message).split("\n");
  const has = name => lines.find(line => line === `FAKE:${name}` || line.startsWith(`FAKE:${name} `));
  if (has("ignore-eof")) holdOnEof = true;
  if (has("slow-exit")) {
    process.on("SIGTERM", () => {
      log({ step: "sigterm" });
      setTimeout(() => {
        log({ step: "exiting" });
        process.exit(0);
      }, 800);
    });
  }
  if (has("leave-child")) {
    // In the fake's process group, holding none of its pipes.
    const child = spawn(process.execPath, ["-e", "setTimeout(() => {}, 30000)"], { stdio: "ignore" });
    log({ step: "left-child", child: child.pid });
  }
  if (has("detached-holder")) {
    // Outside the fake's process group, holding its stdout open.
    const holder = spawn(process.execPath, ["-e", "setTimeout(() => {}, 30000)"], { detached: true, stdio: ["ignore", "inherit", "ignore"] });
    holder.unref();
    log({ step: "detached-holder", holder: holder.pid });
  }
  if (has("die-before-init")) {
    process.stderr.write("boom before init\n");
    process.exit(1);
  }
  const bridgeDown = Boolean(has("bridge-down"));
  const bridgePending = Boolean(has("bridge-pending"));
  const unconnected = bridgeDown || bridgePending;
  out({
    type: "system",
    subtype: "init",
    cwd: process.cwd(),
    tools: mcp ? ["mcp__youi__(listed at runtime)"] : [],
    mcp_servers: mcp ? [{ name: "youi", status: bridgeDown ? "failed" : bridgePending ? "pending" : "connected", source: "dynamic" }] : [],
    model,
    apiKeySource: "none",
    claude_code_version: VERSION,
    permissionMode: "default",
  });
  if (unconnected) {
    // Claude Code would go on without the tools; YOUI stops it first.
    await new Promise(resolve => setTimeout(resolve, 30_000));
    return;
  }
  if (has("null-line")) process.stdout.write("null\n42\n[]\n");
  if (has("rate-limit-bare")) {
    out({ type: "rate_limit_event", rate_limit_info: { status: "allowed", isUsingOverage: false } });
  } else {
    out({ type: "rate_limit_event", rate_limit_info: { status: "allowed", unifiedWindows: { five_hour: { utilization: 0.25, resetsAt: Math.floor(Date.now() / 1000) + 3600, status: "allowed" }, seven_day: { utilization: 0.5, resetsAt: Math.floor(Date.now() / 1000) + 86400, status: "allowed" } }, isUsingOverage: false } });
  }

  if (has("sleep")) {
    // A child of its own, in its process group, as an MCP server or hook would be.
    const grandchild = spawn(process.execPath, ["-e", "setTimeout(() => {}, 60000)"], { stdio: ["ignore", "inherit", "inherit"] });
    log({ step: "sleeping", grandchild: grandchild.pid });
    await new Promise(() => {});
  }
  if (has("error-zeroed")) {
    result({ text: "the process crashed", zeroed: true });
    return;
  }
  const textThenError = has("text-then-error");
  if (textThenError) {
    textMessage(textThenError.slice("FAKE:text-then-error".length).trim() || "partial answer");
    result({ text: "", isError: true, subtype: "error_max_turns", stopReason: "tool_use" });
    return;
  }
  const textThenSleep = has("text-then-sleep");
  if (textThenSleep) {
    textMessage(textThenSleep.slice("FAKE:text-then-sleep".length).trim() || "partial answer");
    log({ step: "sleeping" });
    await new Promise(() => {});
  }
  if (has("refuse")) {
    // Claude Code's own refusal message (never streamed), then a refusal result.
    out({ type: "assistant", message: { id: crypto.randomUUID(), model, role: "assistant", content: [{ type: "text", text: "I can't help with that." }] }, parent_tool_use_id: null, refused_message_id: "msg_refused" });
    result({ text: "I can't help with that.", stopReason: "refusal" });
    return;
  }
  const error = has("error");
  if (error) {
    const text = error.slice("FAKE:error".length).trim() || "failed";
    // Claude Code writes an API error as an assistant message of its own, then an error result.
    out({ type: "assistant", message: { id: crypto.randomUUID(), model: "<synthetic>", role: "assistant", content: [{ type: "text", text: `API Error: ${text}` }] }, parent_tool_use_id: null, error: "unknown" });
    result({ text, isError: true });
    return;
  }
  const thinking = has("thinking");
  if (thinking) {
    const id = `msg_fake_${++messageCount}`;
    out({ type: "stream_event", event: { type: "message_start", message: { id, model, content: [] } }, parent_tool_use_id: null });
    out({ type: "stream_event", event: { type: "content_block_start", index: 0, content_block: { type: "thinking", thinking: "" } }, parent_tool_use_id: null });
    out({ type: "stream_event", event: { type: "content_block_delta", index: 0, delta: { type: "thinking_delta", thinking: thinking.slice("FAKE:thinking".length).trim() } }, parent_tool_use_id: null });
    out({ type: "stream_event", event: { type: "content_block_stop", index: 0 }, parent_tool_use_id: null });
  }

  if (has("list")) {
    await connectBridge();
    const listed = await bridge("tools/list", {});
    log({ step: "tools-list", listed });
    const names = (listed.body?.result?.tools || []).map(tool => tool.name);
    textMessage(`Tools: ${names.join(",")}`);
    result({ text: `Tools: ${names.join(",")}` });
    return;
  }

  const unannounced = has("call-unannounced");
  if (unannounced) {
    await connectBridge();
    const [, name, ...rest] = unannounced.split(" ");
    const input = rest.length ? JSON.parse(rest.join(" ")) : {};
    log({ step: "calling-unannounced", name });
    const called = await bridge("tools/call", { name, arguments: input, _meta: { "claudecode/toolUseId": "toolu_never_reported" } });
    log({ step: "tools-call", name, input, called });
    textMessage("called");
    result({ text: "called" });
    return;
  }

  const calls = lines.filter(line => line.startsWith("FAKE:call "));
  if (calls.length > 0) {
    await connectBridge();
    const replies = [];
    for (const [index, line] of calls.entries()) {
      const [, name, ...rest] = line.split(" ");
      const input = rest.length ? JSON.parse(rest.join(" ")) : {};
      const toolUseId = `toolu_fake_${index + 1}`;
      const id = `msg_fake_${++messageCount}`;
      out({ type: "stream_event", event: { type: "message_start", message: { id, model, content: [] } }, parent_tool_use_id: null });
      out({ type: "stream_event", event: { type: "content_block_start", index: 0, content_block: { type: "tool_use", id: toolUseId, name: `mcp__youi__${name}`, input: {} } }, parent_tool_use_id: null });
      out({ type: "assistant", message: { id, model, role: "assistant", content: [{ type: "tool_use", id: toolUseId, name: `mcp__youi__${name}`, input }] }, parent_tool_use_id: null });
      out({ type: "stream_event", event: { type: "content_block_stop", index: 0 }, parent_tool_use_id: null });
      const called = await bridge("tools/call", { name, arguments: input, _meta: { "claudecode/toolUseId": toolUseId, progressToken: index } });
      log({ step: "tools-call", name, input, called });
      const content = called.body?.result?.content || [];
      out({ type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: toolUseId, content, is_error: called.body?.result?.isError === true }] }, parent_tool_use_id: null });
      replies.push(`${name}${called.body?.result?.isError ? " (error)" : ""}: ${content.map(block => block.text).join("").slice(0, 200)}`);
    }
    const text = `Tool said: ${replies.join(" | ")}`;
    textMessage(text);
    result({ text });
    return;
  }

  const text = `Echo: ${message}`;
  textMessage(text);
  result({ text });
}

const input = readline.createInterface({ input: process.stdin });
let answered = false;
input.on("line", async line => {
  if (answered || !line.trim()) return;
  const event = JSON.parse(line);
  if (event.type !== "user") return;
  answered = true;
  const content = event.message?.content;
  const message = typeof content === "string" ? content : (content || []).map(block => block.text || "").join("");
  log({ step: "message", message, line: event });
  try {
    await answer(message);
  } catch (error) {
    log({ step: "fake-error", error: String(error?.stack || error) });
    result({ text: `fake failed: ${error.message}`, isError: true });
  }
});
input.on("close", () => {
  log({ step: "stdin-closed" });
  // Kept alive (until killed) by a timer, since nothing else would hold the process.
  if (holdOnEof) {
    setTimeout(() => process.exit(4), 30_000);
    return;
  }
  setTimeout(() => process.exit(0), 20);
});
// Never outlive a test that forgot it.
setTimeout(() => process.exit(3), 60_000).unref();
