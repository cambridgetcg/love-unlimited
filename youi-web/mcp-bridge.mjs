// mcp-bridge.mjs — YOUI's tools for Claude Code, over a loopback MCP endpoint
// (docs/YOUI-CLAUDE-CODE-DESIGN.md §3.2).
//
// Each Claude turn opens one bridge turn: a path /mcp/<turn id> on YOUI's own
// loopback listener and a per-turn bearer token, both random, bound to that
// YOUI session and that turn. The token is valid for any number of requests
// until the turn ends; closing the turn forgets it, so it is refused afterwards. The server routes /mcp/* here after its loopback and
// Host checks and before any browser handling: no cookie, CSRF token or page
// lease ever reaches this endpoint, and a request carrying an Origin or Cookie
// header (a browser) is refused.
//
// It speaks MCP's Streamable HTTP transport, statelessly, with JSON responses:
// POST only (GET's server-to-client stream and DELETE are 405), JSON-RPC 2.0,
// initialize, notifications, ping, tools/list and tools/call. Anything else
// gets "method not found", which is how Claude Code's newer server/discover
// probe falls back to initialize.

import crypto from "node:crypto";

export const MCP_PROTOCOL_VERSIONS = Object.freeze(["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]);
const TURN_PATH = /^\/mcp\/([a-f0-9]{32})$/;

const JSONRPC = Object.freeze({
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
});

function digest(value) {
  return crypto.createHash("sha256").update(String(value)).digest();
}

function sendJson(res, status, body, headers = {}) {
  if (body === undefined) {
    res.writeHead(status, { "Cache-Control": "no-store", ...headers });
    res.end();
    return;
  }
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    ...headers,
  });
  res.end(JSON.stringify(body));
}

function readBody(req, maxBytes) {
  return new Promise((resolvePromise, rejectPromise) => {
    const chunks = [];
    let bytes = 0;
    req.on("data", chunk => {
      bytes += chunk.length;
      if (bytes > maxBytes) {
        rejectPromise(Object.assign(new Error("body too large"), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolvePromise(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", rejectPromise);
  });
}

function rpcError(id, code, message) {
  return { jsonrpc: "2.0", id: id ?? null, error: { code, message } };
}

/**
 * @param {object} options
 * @param {(context: object) => Promise<object[]>|object[]} options.listTools
 *   the tools this turn's session may use, as YOUI's TOOLS entries
 * @param {(context: object, name: string, args: object, meta: {toolUseId: string|null}) => Promise<{text: string, isError: boolean}>} options.callTool
 */
export function createMcpBridge({
  listTools,
  callTool,
  serverName = "youi",
  serverVersion = "1.0.0",
  maxBodyBytes = 1024 * 1024,
}) {
  if (typeof listTools !== "function" || typeof callTool !== "function") {
    throw new TypeError("listTools and callTool are required");
  }
  // turn id -> { tokenDigest, context }
  const turns = new Map();

  function authorised(req, id) {
    const turn = turns.get(id);
    const header = String(req.headers.authorization || "");
    const match = header.match(/^Bearer ([A-Za-z0-9_-]{20,200})$/);
    // Always compare, so an unknown turn and a wrong token take the same path.
    const expected = turn?.tokenDigest || digest(crypto.randomBytes(32));
    const given = digest(match ? match[1] : "");
    return crypto.timingSafeEqual(expected, given) && turn ? turn : null;
  }

  async function dispatch(turn, message) {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      return rpcError(null, JSONRPC.INVALID_REQUEST, "Invalid request");
    }
    const isRequest = message.id !== undefined && message.id !== null;
    if (typeof message.method !== "string") {
      // A response from the client (or junk): nothing to answer.
      return isRequest ? rpcError(message.id, JSONRPC.INVALID_REQUEST, "Invalid request") : null;
    }
    if (!isRequest) return null; // notifications/initialized and other notifications
    if (message.jsonrpc !== "2.0") return rpcError(message.id, JSONRPC.INVALID_REQUEST, "jsonrpc must be \"2.0\"");
    const params = message.params && typeof message.params === "object" ? message.params : {};
    const reply = result => ({ jsonrpc: "2.0", id: message.id, result });

    switch (message.method) {
      case "initialize": {
        const requested = String(params.protocolVersion || "");
        return reply({
          protocolVersion: MCP_PROTOCOL_VERSIONS.includes(requested) ? requested : MCP_PROTOCOL_VERSIONS[0],
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: serverName, version: serverVersion },
        });
      }
      case "ping":
        return reply({});
      case "tools/list": {
        const tools = await listTools(turn.context);
        return reply({
          tools: tools.map(tool => ({
            name: tool.name,
            description: tool.description,
            inputSchema: tool.input_schema || { type: "object", properties: {} },
          })),
        });
      }
      case "tools/call": {
        if (typeof params.name !== "string" || !params.name) {
          return rpcError(message.id, JSONRPC.INVALID_PARAMS, "tools/call needs a tool name");
        }
        const args = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments)
          ? params.arguments
          : {};
        const meta = params._meta && typeof params._meta === "object" ? params._meta : {};
        const toolUseId = typeof meta["claudecode/toolUseId"] === "string" ? meta["claudecode/toolUseId"] : null;
        let outcome;
        try {
          outcome = await callTool(turn.context, params.name, args, { toolUseId });
        } catch (error) {
          outcome = { text: `Error: ${String(error?.message || error).slice(0, 500)}`, isError: true };
        }
        return reply({
          content: [{ type: "text", text: String(outcome?.text ?? "") }],
          isError: outcome?.isError === true,
        });
      }
      default:
        return rpcError(message.id, JSONRPC.METHOD_NOT_FOUND, `Method not found: ${message.method}`);
    }
  }

  return {
    /** Open a bridge turn for `context` (the session state, its signal, the page's events). */
    open(context) {
      const id = crypto.randomBytes(16).toString("hex");
      const token = crypto.randomBytes(32).toString("base64url");
      turns.set(id, { tokenDigest: digest(token), context });
      return { id, token, path: `/mcp/${id}` };
    },

    /** The turn has ended: its path and token stop working. */
    close(id) {
      turns.delete(id);
    },

    openTurns() {
      return turns.size;
    },

    isBridgePath(path) {
      return path === "/mcp" || path.startsWith("/mcp/");
    },

    async handle(req, res, path) {
      // Browsers always send Origin on these requests; Claude Code never does.
      if (req.headers.origin !== undefined || req.headers.cookie !== undefined) {
        return sendJson(res, 403, { error: "the tool bridge does not accept browser requests", code: "bridge_browser_refused" });
      }
      const match = path.match(TURN_PATH);
      if (!match) return sendJson(res, 404, { error: "unknown bridge path", code: "bridge_not_found" });
      const turn = authorised(req, match[1]);
      if (!turn) return sendJson(res, 401, { error: "unknown or ended turn, or wrong token", code: "bridge_unauthorized" });
      if (req.method !== "POST") {
        return sendJson(res, 405, { error: "POST only", code: "method_not_allowed" }, { Allow: "POST" });
      }
      if (!String(req.headers["content-type"] || "").toLowerCase().startsWith("application/json")) {
        return sendJson(res, 415, { error: "application/json only", code: "unsupported_media_type" });
      }
      let raw;
      try {
        raw = await readBody(req, maxBodyBytes);
      } catch (error) {
        return sendJson(res, error.statusCode || 400, { error: "could not read the request", code: "bad_body" });
      }
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return sendJson(res, 400, rpcError(null, JSONRPC.PARSE_ERROR, "Parse error"));
      }
      try {
        if (Array.isArray(parsed)) {
          if (parsed.length === 0) return sendJson(res, 400, rpcError(null, JSONRPC.INVALID_REQUEST, "Empty batch"));
          const replies = (await Promise.all(parsed.map(message => dispatch(turn, message)))).filter(Boolean);
          return replies.length ? sendJson(res, 200, replies) : sendJson(res, 202, undefined);
        }
        const reply = await dispatch(turn, parsed);
        return reply ? sendJson(res, 200, reply) : sendJson(res, 202, undefined);
      } catch (error) {
        const id = Array.isArray(parsed) ? null : parsed?.id;
        return sendJson(res, 200, rpcError(id, JSONRPC.INTERNAL_ERROR, String(error?.message || error).slice(0, 300)));
      }
    },
  };
}
