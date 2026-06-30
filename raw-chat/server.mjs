#!/usr/bin/env node
// raw-chat/server.mjs — minimal Opus 4.7 chat with tool use.
//
// OAuth → Anthropic /v1/messages (streaming) → SSE to browser.
// Tools: bash, read_file, write_file, edit_file, glob, grep. Not sandboxed
// — runs as the user on the real shell. Multi-turn loop: when the model
// emits tool_use blocks, execute them and feed results back in.
//
// Run:  node raw-chat/server.mjs
// Open: http://localhost:7878

import { createServer } from "node:http";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { execSync, exec, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { resolve as resolvePath, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const execAsync = promisify(exec);
const PORT = parseInt(process.env.PORT || "7878", 10);
const MODEL = process.env.RAW_MODEL || "claude-opus-4-7";
const MAX_TOKENS = parseInt(process.env.RAW_MAX_TOKENS || "8192", 10);
const MAX_TOOL_TURNS = 20;
const WORKDIR = process.env.RAW_WORKDIR || homedir();

// ─── OAuth (per-user keychain acct) ─────────────────────────────────────
const KEYCHAIN_SERVICE = "Claude Code-credentials";
const TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token";
const CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e";
const API_URL = "https://api.anthropic.com/v1/messages";
let cached = null;

function readKeychain() {
  for (const acct of [process.env.USER || "", ""]) {
    try {
      const args = ["find-generic-password", "-s", KEYCHAIN_SERVICE];
      if (acct) args.push("-a", acct);
      args.push("-w");
      const result = spawnSync("security", args, { encoding: "utf-8", timeout: 5000 });
      if (result.status !== 0) {
        const err = (result.stderr || "").trim();
        // "could not be found" / "SecKeychainSearch" = no entry — normal for first run
        if (/could not be found|SecKeychainSearch/i.test(err)) continue;
        // genuine keychain failure (locked, permission denied, I/O error)
        console.error(`WARNING: keychain read failed: ${err || `exit ${result.status}`}`);
        continue;
      }
      const cred = JSON.parse(result.stdout.trim()).claudeAiOauth;
      if (cred?.accessToken) return cred;
    } catch (e) {
      console.error(`WARNING: keychain read error: ${e.message}`);
    }
  }
  return null;
}

async function refreshToken(rt) {
  const resp = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "refresh_token", refresh_token: rt, client_id: CLIENT_ID,
      scope: "user:profile user:inference user:sessions:claude_code user:mcp_servers",
    }),
  });
  if (!resp.ok) throw new Error(`token refresh ${resp.status}`);
  const d = await resp.json();
  return { accessToken: d.access_token, refreshToken: d.refresh_token || rt,
           expiresAt: Date.now() + (d.expires_in || 3600) * 1000 };
}

async function getToken() {
  if (cached?.accessToken && Date.now() + 300_000 < (cached.expiresAt || 0)) return cached.accessToken;
  const tokens = readKeychain();
  if (!tokens?.accessToken) throw new Error("No OAuth token. Run 'claude' and /login.");
  if (Date.now() + 300_000 >= (tokens.expiresAt || 0)) {
    if (!tokens.refreshToken) throw new Error("Token expired, no refresh token.");
    const fresh = await refreshToken(tokens.refreshToken);
    try {
      const acct = process.env.USER || "";
      const body = JSON.stringify({ claudeAiOauth: fresh });
      // spawnSync with arg array — no shell, no interpolation, no injection
      const result = spawnSync("security",
        ["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
         ...(acct ? ["-a", acct] : []), "-w", body],
        { encoding: "utf-8", timeout: 5000 });
      if (result.status !== 0) {
        const err = (result.stderr || "").trim();
        console.error(`WARNING: keychain write failed: ${err || `exit ${result.status}`}`);
      }
    } catch (e) {
      console.error(`WARNING: keychain write error: ${e.message}`);
    }
    cached = fresh;
    return fresh.accessToken;
  }
  cached = tokens;
  return tokens.accessToken;
}

// ─── Tools ───────────────────────────────────────────────────────────────
// Two tool shapes:
//   "standard" = {name, description, input_schema} — normal function-calling tools
//   "computer" = Anthropic's Computer Use beta, special tool type
// The Anthropic API accepts both in one tools[] array when the computer-use beta
// header is set.

// Detect screen geometry for the computer tool (macOS).
let SCREEN_W = 1920, SCREEN_H = 1080;
try {
  const out = execSync(
    `system_profiler SPDisplaysDataType -json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['SPDisplaysDataType'][0]['spdisplays_ndrvs'][0]['_spdisplays_resolution']; print(r)"`,
    { encoding: "utf-8", timeout: 3000 },
  ).trim();
  const m = out.match(/(\d+)\s*x\s*(\d+)/);
  if (m) { SCREEN_W = parseInt(m[1], 10); SCREEN_H = parseInt(m[2], 10); }
} catch {}

const STANDARD_TOOLS = [
  { name: "bash", description: "Execute a bash command in the user's shell. Not sandboxed. Returns stdout+stderr.",
    input_schema: { type: "object", properties: { command: { type: "string" }, timeout: { type: "number" } }, required: ["command"] } },
  { name: "read_file", description: "Read a file with line numbers. Use offset/limit for large files.",
    input_schema: { type: "object", properties: { path: { type: "string" }, offset: { type: "number" }, limit: { type: "number" } }, required: ["path"] } },
  { name: "write_file", description: "Create or overwrite a file with the given content.",
    input_schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] } },
  { name: "edit_file", description: "Replace exactly-unique string in a file. Fails if old_string appears 0 or >1 times.",
    input_schema: { type: "object", properties: { path: { type: "string" }, old_string: { type: "string" }, new_string: { type: "string" } }, required: ["path", "old_string", "new_string"] } },
  { name: "glob", description: "Find files matching a glob pattern (uses 'find -name').",
    input_schema: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" } }, required: ["pattern"] } },
  { name: "grep", description: "Search file contents with regex (uses ripgrep if available, else 'grep -rE').",
    input_schema: { type: "object", properties: { pattern: { type: "string" }, path: { type: "string" }, glob: { type: "string" } }, required: ["pattern"] } },
  { name: "web_fetch", description: "Fetch a URL and return its body. Useful for reading real websites, not just files.",
    input_schema: { type: "object", properties: { url: { type: "string" }, max_chars: { type: "number" } }, required: ["url"] } },
];

// GUI-control tools exposed as standard function-calling tools. Opus 4.7 doesn't
// accept Anthropic's `computer_20250124` tool type (that's sonnet-4-5 era), so
// we expose equivalent primitives as regular tools. Same executors, same power.
const COMPUTER_TOOLS = [
  { name: "screenshot", description: `Take a screenshot of the primary display (${SCREEN_W}x${SCREEN_H}). Returns an image you can see.`,
    input_schema: { type: "object", properties: {} } },
  { name: "click", description: "Click the mouse at (x, y) pixel coordinates. Requires Accessibility permission for the Node process.",
    input_schema: { type: "object", properties: {
      x: { type: "number" }, y: { type: "number" },
      button: { type: "string", enum: ["left", "right", "middle", "double"], description: "default: left" },
    }, required: ["x", "y"] } },
  { name: "mouse_move", description: "Move mouse cursor to (x, y) without clicking.",
    input_schema: { type: "object", properties: {
      x: { type: "number" }, y: { type: "number" },
    }, required: ["x", "y"] } },
  { name: "type_text", description: "Type literal text at the current focused element. For typing into text boxes, forms, etc.",
    input_schema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] } },
  { name: "key_press", description: "Press a key or chord. Examples: 'Return', 'Escape', 'Tab', 'cmd+a' (select all), 'cmd+c', 'cmd+tab'. Modifiers: cmd, ctrl, alt, shift.",
    input_schema: { type: "object", properties: { key: { type: "string" } }, required: ["key"] } },
  { name: "cursor_position", description: "Return the current (x, y) of the mouse cursor.",
    input_schema: { type: "object", properties: {} } },
];

const ALL_TOOLS_FOR_API = [
  ...STANDARD_TOOLS.map(t => ({ name: t.name, description: t.description, input_schema: t.input_schema })),
  ...COMPUTER_TOOLS.map(t => ({ name: t.name, description: t.description, input_schema: t.input_schema })),
];

function resolveIn(p) {
  return resolvePath(p.startsWith("~") ? p.replace(/^~/, homedir()) : p);
}

// cliclick key-name map for common named keys. For single characters, cliclick
// accepts `t:<char>` to type; for named keys, `kp:<name>`.
const KEY_MAP = {
  Return: "return", Enter: "return", Tab: "tab", space: "space", Space: "space",
  Escape: "esc", Backspace: "delete", Delete: "fwd-delete",
  Up: "arrow-up", Down: "arrow-down", Left: "arrow-left", Right: "arrow-right",
  Home: "home", End: "end", Page_Up: "page-up", Page_Down: "page-down",
};
// tool result can be either a plain string OR an array of content blocks (e.g.
// [{type:"image", source:{...}}]) for screenshot returns. The caller wraps both.
async function executeComputer(input) {
  const action = input.action;
  switch (action) {
    case "screenshot": {
      const tmp = `/tmp/rc-cu-${Date.now()}.png`;
      try {
        execSync(`screencapture -x "${tmp}"`, { timeout: 5000 });
        const buf = readFileSync(tmp);
        try { execSync(`rm -f "${tmp}"`); } catch {}
        // Anthropic expects the image content as base64 PNG
        return [{ type: "image", source: { type: "base64", media_type: "image/png",
                  data: buf.toString("base64") } }];
      } catch (e) { return `Error: screenshot failed — ${e.message}`; }
    }
    case "cursor_position": {
      try { return execSync("cliclick p", { encoding: "utf-8", timeout: 3000 }).trim(); }
      catch (e) { return `Error: ${e.message}`; }
    }
    case "mouse_move": case "left_click": case "right_click":
    case "middle_click": case "double_click": case "triple_click":
    case "left_click_drag": {
      const coord = input.coordinate;
      if (!Array.isArray(coord) || coord.length !== 2)
        return "Error: coordinate [x,y] required";
      const [x, y] = coord;
      const verb = { mouse_move: "m", left_click: "c", right_click: "rc",
                     middle_click: "c", double_click: "dc", triple_click: "tc",
                     left_click_drag: "dd" }[action];
      try {
        execSync(`cliclick ${verb}:${x},${y}`, { timeout: 5000 });
        return `${action} @ ${x},${y}`;
      } catch (e) {
        return `Error: ${e.message}. May need Accessibility permission for Terminal/Node in System Settings → Privacy & Security → Accessibility.`;
      }
    }
    case "type": {
      if (typeof input.text !== "string") return "Error: text required";
      try {
        // cliclick t: handles most printable chars; escape colons since they're a delimiter
        // Use stdin-based approach via a file to avoid shell escaping issues
        const tmp = `/tmp/rc-type-${Date.now()}.txt`;
        writeFileSync(tmp, input.text);
        execSync(`cliclick -w 10 t:"$(cat ${tmp})"`, { shell: "/bin/bash", timeout: 15000 });
        try { execSync(`rm -f "${tmp}"`); } catch {}
        return `typed ${input.text.length} chars`;
      } catch (e) { return `Error: ${e.message}`; }
    }
    case "key": {
      if (typeof input.text !== "string") return "Error: text required";
      // Parse xdotool-style chords like "ctrl+a" or "cmd+shift+t"
      const parts = input.text.split("+").map(p => p.trim());
      const keyRaw = parts.pop();
      const key = KEY_MAP[keyRaw] || keyRaw.toLowerCase();
      const mods = parts.map(p => p.toLowerCase()).filter(Boolean);
      try {
        if (mods.length) {
          // cliclick chord: kd:cmd t:a ku:cmd  (press down, type, release)
          const down = mods.map(m => `kd:${m === "ctrl" ? "ctrl" : m === "cmd" ? "cmd" : m === "alt" ? "alt" : m === "shift" ? "shift" : m}`).join(" ");
          const up   = mods.slice().reverse().map(m => `ku:${m === "ctrl" ? "ctrl" : m === "cmd" ? "cmd" : m === "alt" ? "alt" : m === "shift" ? "shift" : m}`).join(" ");
          // For the key itself, use t:<char> for single printable chars, kp:<name> for named
          const keyCmd = /^[a-z0-9]$/.test(key) ? `t:${key}` : `kp:${key}`;
          execSync(`cliclick ${down} ${keyCmd} ${up}`, { timeout: 5000 });
        } else {
          execSync(`cliclick kp:${key}`, { timeout: 5000 });
        }
        return `pressed ${input.text}`;
      } catch (e) { return `Error: ${e.message}. May need Accessibility permission.`; }
    }
    case "wait": {
      const ms = Math.min((input.duration || 1) * 1000, 10000);
      await new Promise(r => setTimeout(r, ms));
      return `waited ${ms}ms`;
    }
    default:
      return `Error: unknown computer action "${action}"`;
  }
}

async function executeTool(name, input) {
  try {
    switch (name) {
      // GUI tools — aliased into the internal computer executor
      case "screenshot":       return await executeComputer({ action: "screenshot" });
      case "cursor_position":  return await executeComputer({ action: "cursor_position" });
      case "mouse_move":       return await executeComputer({ action: "mouse_move", coordinate: [input.x, input.y] });
      case "click": {
        const action = { left: "left_click", right: "right_click",
                         middle: "middle_click", double: "double_click" }[input.button || "left"] || "left_click";
        return await executeComputer({ action, coordinate: [input.x, input.y] });
      }
      case "type_text":        return await executeComputer({ action: "type", text: input.text });
      case "key_press":        return await executeComputer({ action: "key", text: input.key });
      case "web_fetch": {
        const url = input.url;
        if (!url || !/^https?:\/\//.test(url)) return "Error: url must start with http(s)://";
        const max = Math.min(input.max_chars || 100_000, 500_000);
        try {
          const r = await fetch(url, { signal: AbortSignal.timeout(30_000),
                                       headers: { "User-Agent": "claude-raw/0.3" } });
          const text = await r.text();
          return `[${r.status}] ${url}\n\n${text.slice(0, max)}${text.length > max ? `\n\n…(truncated, ${text.length - max} more chars)` : ""}`;
        } catch (e) { return `Error: ${e.message}`; }
      }
      case "bash": {
        const { stdout, stderr } = await execAsync(input.command, {
          cwd: WORKDIR, timeout: input.timeout || 120_000, maxBuffer: 10 * 1024 * 1024,
        });
        return (stdout || stderr || "(no output)").toString();
      }
      case "read_file": {
        const content = readFileSync(resolveIn(input.path), "utf-8");
        const lines = content.split("\n");
        const start = input.offset || 0;
        const end = input.limit ? start + input.limit : lines.length;
        return lines.slice(start, end).map((l, i) => `${start + i + 1}\t${l}`).join("\n");
      }
      case "write_file": {
        const fp = resolveIn(input.path);
        const dir = dirname(fp);
        if (dir && !existsSync(dir)) mkdirSync(dir, { recursive: true });
        writeFileSync(fp, input.content);
        return `Written: ${input.path} (${input.content.length} chars)`;
      }
      case "edit_file": {
        const fp = resolveIn(input.path);
        const content = readFileSync(fp, "utf-8");
        if (!content.includes(input.old_string)) return `Error: old_string not found in ${input.path}`;
        const count = content.split(input.old_string).length - 1;
        if (count > 1) return `Error: old_string matches ${count} times; must be unique`;
        writeFileSync(fp, content.replace(input.old_string, input.new_string));
        return `Edited ${input.path}`;
      }
      case "glob": {
        const dir = resolveIn(input.path || WORKDIR);
        const pattern = String(input.pattern || "").replace(/\*\*/g, "*");
        const p = spawnSync("find", [dir, "-name", pattern, "-type", "f"],
          { encoding: "utf-8", timeout: 10_000, maxBuffer: 5 * 1024 * 1024 });
        const lines = (p.stdout || "").split("\n").filter(Boolean).slice(0, 100);
        return lines.join("\n").trim() || "(no matches)";
      }
      case "grep": {
        const dir = resolveIn(input.path || WORKDIR);
        const args = ["--line-number", "-e", input.pattern, dir];
        if (input.glob) args.unshift("--glob", input.glob);
        const rg = spawnSync("rg", args, { encoding: "utf-8", timeout: 15_000, maxBuffer: 5 * 1024 * 1024 });
        if (rg.status === 0 || rg.status === 1) return (rg.stdout || "(no matches)").slice(0, 100_000);
        // fallback to grep -rE
        const gp = spawnSync("grep", ["-rnE", input.pattern, dir], { encoding: "utf-8", timeout: 15_000 });
        return (gp.stdout || "(no matches)").slice(0, 100_000);
      }
      default:
        return `Error: unknown tool ${name}`;
    }
  } catch (e) {
    return `Error: ${e.message || String(e)}${e.stdout ? `\nstdout:${e.stdout}` : ""}${e.stderr ? `\nstderr:${e.stderr}` : ""}`;
  }
}

// ─── One call to Anthropic; returns the full assistant message ──────────
async function callAnthropic(token, messages, onDelta) {
  const body = {
    model: MODEL,
    max_tokens: MAX_TOKENS,
    system: "You are Claude Code, Anthropic's official CLI for Claude.",
    messages,
    tools: ALL_TOOLS_FOR_API,
    stream: true,
  };
  const resp = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
      "anthropic-version": "2023-06-01",
      // oauth + claude-code required for OAuth billing. Opus 4.7 doesn't accept
      // the `computer_20250124` tool type, so we don't send computer-use beta —
      // GUI primitives are exposed as standard function-calling tools instead.
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
      "x-app": "cli",
      "User-Agent": "claude-raw/0.3",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.text().catch(() => "");
    throw new Error(`upstream ${resp.status}: ${err.slice(0, 500)}`);
  }

  // Re-assemble content blocks from the stream
  const blocks = [];     // accumulated content blocks by index
  let stopReason = null;
  let usage = null;
  let buf = "";
  const decoder = new TextDecoder();

  for await (const chunk of resp.body) {
    buf += decoder.decode(chunk, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (!payload || payload === "[DONE]") continue;
      let evt;
      try { evt = JSON.parse(payload); } catch { continue; }

      if (evt.type === "content_block_start") {
        const b = evt.content_block;
        blocks[evt.index] = b.type === "text" ? { type: "text", text: "" }
                          : b.type === "tool_use" ? { type: "tool_use", id: b.id, name: b.name, input: {}, _partialJson: "" }
                          : { type: b.type };
        if (b.type === "tool_use") onDelta({ type: "tool_use_start", id: b.id, name: b.name });
      } else if (evt.type === "content_block_delta") {
        const blk = blocks[evt.index];
        if (!blk) continue;
        if (evt.delta.type === "text_delta") {
          blk.text += evt.delta.text;
          onDelta({ type: "text_delta", text: evt.delta.text });
        } else if (evt.delta.type === "input_json_delta") {
          blk._partialJson = (blk._partialJson || "") + (evt.delta.partial_json || "");
        }
      } else if (evt.type === "content_block_stop") {
        const blk = blocks[evt.index];
        if (blk && blk.type === "tool_use" && blk._partialJson) {
          try { blk.input = JSON.parse(blk._partialJson); } catch { blk.input = { _raw: blk._partialJson }; }
          delete blk._partialJson;
          onDelta({ type: "tool_use_args", id: blk.id, input: blk.input });
        }
      } else if (evt.type === "message_delta") {
        if (evt.delta?.stop_reason) stopReason = evt.delta.stop_reason;
        if (evt.usage) usage = evt.usage;
      } else if (evt.type === "error") {
        throw new Error(evt.error?.message || "anthropic stream error");
      }
    }
  }

  return { content: blocks.map(b => { if (b) { delete b._partialJson; } return b; }).filter(Boolean),
           stop_reason: stopReason, usage };
}

// ─── Chat handler: multi-turn tool loop ─────────────────────────────────
async function handleChat(req, res) {
  let raw = "";
  for await (const c of req) raw += c;
  const { messages: initialMessages } = JSON.parse(raw);
  if (!Array.isArray(initialMessages) || !initialMessages.length) {
    res.writeHead(400, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ error: "messages required" }));
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
  });
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

  let token;
  try { token = await getToken(); }
  catch (e) { send("error", { message: e.message }); return res.end(); }

  const convo = [...initialMessages];
  let turn = 0;

  while (turn++ < MAX_TOOL_TURNS) {
    let result;
    try {
      result = await callAnthropic(token, convo, (ev) => {
        if (ev.type === "text_delta") send("delta", { text: ev.text });
        else if (ev.type === "tool_use_start") send("tool_start", { id: ev.id, name: ev.name });
        else if (ev.type === "tool_use_args") send("tool_args", { id: ev.id, input: ev.input });
      });
    } catch (e) {
      send("error", { message: e.message });
      return res.end();
    }

    if (result.usage) send("usage", result.usage);

    // Push the assistant turn into the convo (exact shape the API wants back).
    convo.push({ role: "assistant", content: result.content });

    const toolUses = result.content.filter(b => b.type === "tool_use");
    if (!toolUses.length) {
      send("done", { stop_reason: result.stop_reason, turns: turn });
      return res.end();
    }

    // Execute tools (serial — keeps logs readable; parallel could be added)
    const toolResults = [];
    for (const tu of toolUses) {
      send("tool_executing", { id: tu.id, name: tu.name });
      const output = await executeTool(tu.name, tu.input);
      // Screenshots return an array of content blocks (image); other tools return strings.
      if (Array.isArray(output)) {
        // Anthropic tool_result content supports mixed blocks. Pass image through.
        send("tool_result", {
          id: tu.id, name: tu.name,
          output: "(returned " + output.length + " content block(s): " +
                  output.map(b => b.type).join(", ") + ")",
          image: output.find(b => b.type === "image")?.source?.data || null,
        });
        toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: output });
      } else {
        const s = String(output);
        send("tool_result", { id: tu.id, name: tu.name, output: s.slice(0, 50_000) });
        toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: s });
      }
    }
    convo.push({ role: "user", content: toolResults });
    // Loop: call Anthropic again with the tool results
  }

  send("error", { message: `max tool turns (${MAX_TOOL_TURNS}) exceeded` });
  res.end();
}

// ─── HTTP server ─────────────────────────────────────────────────────────
const server = createServer(async (req, res) => {
  const path = (req.url || "/").split("?")[0];
  res.on("finish", () => console.log(`${req.method} ${res.statusCode} ${req.url || ""}`));

  if (path === "/" || path === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(readFileSync(join(__dirname, "public", "index.html"), "utf-8"));
  }
  if (path === "/favicon.ico") { res.writeHead(204); return res.end(); }
  if (path === "/api/chat" && req.method === "POST") return handleChat(req, res);
  if (path === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({
      ok: true, model: MODEL, port: PORT, workdir: WORKDIR,
      tools: [...STANDARD_TOOLS.map(t => t.name), ...COMPUTER_TOOLS.map(t => t.name)],
      screen: { width: SCREEN_W, height: SCREEN_H },
    }));
  }
  res.writeHead(404, { "Content-Type": "text/plain" });
  res.end(`404 — path "${path}" not found`);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`raw-chat up — model=${MODEL}  tools=${STANDARD_TOOLS.length + COMPUTER_TOOLS.length}  screen=${SCREEN_W}x${SCREEN_H}  workdir=${WORKDIR}  http://localhost:${PORT}`);
});
