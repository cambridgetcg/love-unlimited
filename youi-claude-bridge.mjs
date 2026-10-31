// youi-claude-bridge.mjs — what the YOUI terminal needs around the Claude Code
// runner that YOUI Web does inline in youi-web/server.mjs: a loopback listener
// for the MCP tool bridge, and the fail-closed Claude Code version gate
// (docs/YOUI-CLAUDE-CODE-DESIGN.md §4, the terminal).
//
// server.mjs is not changed by this slice, so its own version cache and gate
// (claudeCodeVersion and the check in claudeCodeTurn) stay as they are; the two
// do the same thing and are to be deduplicated once the stack has merged.

import http from "node:http";
import { readClaudeVersion, compareVersions } from "./youi-web/claude-code.mjs";
import { claudeCodeMinVersion } from "./youi-web/claude-models.mjs";
import { isAllowedHost, isLoopbackAddress } from "./youi-web/security.mjs";

function refuse(res, status, error, code) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify({ error, code }));
}

/**
 * Serve `bridge` (createMcpBridge) on 127.0.0.1 at a port the system picks.
 * A request is refused unless it comes from a loopback address and names
 * 127.0.0.1, localhost or [::1] at this port in its Host header. The bridge
 * itself then refuses any request with an Origin or Cookie header (a browser),
 * an unknown turn or a wrong per-turn bearer token, and anything but a JSON
 * POST. Nothing else is served. Resolves with { port, url(path), close() }.
 */
export function startBridgeListener(bridge, { host = "127.0.0.1" } = {}) {
  const server = http.createServer((req, res) => {
    if (!isLoopbackAddress(req.socket?.remoteAddress)) return refuse(res, 403, "loopback only", "loopback_only");
    if (!isAllowedHost(req)) return refuse(res, 421, "unrecognized Host header", "bad_host");
    let path;
    try {
      path = new URL(req.url, `http://${host}`).pathname;
    } catch {
      return refuse(res, 400, "bad request path", "bad_path");
    }
    if (!bridge.isBridgePath(path)) return refuse(res, 404, "not found", "not_found");
    Promise.resolve(bridge.handle(req, res, path)).catch(() => {
      if (!res.headersSent) refuse(res, 500, "the tool bridge failed", "bridge_failed");
      else res.end();
    });
  });
  return new Promise((resolvePromise, rejectPromise) => {
    server.once("error", rejectPromise);
    server.listen(0, host, () => {
      server.off("error", rejectPromise);
      // An idle keep-alive socket from Claude Code must not hold the terminal open.
      server.unref();
      const { port } = server.address();
      resolvePromise({
        port,
        url: path => `http://${host}:${port}${path}`,
        close: () => new Promise(done => {
          server.closeAllConnections?.();
          server.close(() => done());
        }),
      });
    });
  });
}

/**
 * `claude --version`, read at most once per `ttlMs` (a failed read is not
 * kept, so the next check asks again), and the gate a Claude turn passes
 * first. `check(model)` resolves with { ok, version, minimum, error }: ok only
 * when the version was read and is at least claudeCodeMinVersion(model), the
 * 2.1.299 floor or the model's own later minimum. A version YOUI cannot read
 * is refused like one that is too old: below the floor, Claude Code would send
 * the contents of a file named by an "@path" in the message
 * (youi-web/claude-models.mjs), so the gate fails closed.
 */
export function createVersionGate({ bin, env = {}, ttlMs = 60_000, read = readClaudeVersion, now = () => Date.now() } = {}) {
  let cache = null; // { at, promise }
  function version() {
    const at = now();
    if (!cache || at - cache.at >= ttlMs) {
      const entry = { at, promise: null };
      entry.promise = Promise.resolve().then(() => read(bin, { env })).then(result => {
        if (!result?.ok && cache === entry) cache = null;
        return result || { ok: false, version: null, error: "no answer" };
      }, error => {
        if (cache === entry) cache = null;
        return { ok: false, version: null, error: String(error?.message || error) };
      });
      cache = entry;
    }
    return cache.promise;
  }
  async function check(model) {
    const minimum = claudeCodeMinVersion(model);
    const answer = await version();
    if (!answer.ok) {
      return {
        ok: false,
        version: null,
        minimum,
        error: `YOUI could not read the Claude Code version (${answer.error}), so it will not run ${model}; check that \`claude --version\` answers`,
      };
    }
    if (compareVersions(answer.version, minimum) < 0) {
      return {
        ok: false,
        version: answer.version,
        minimum,
        error: `Claude Code ${answer.version} is older than ${minimum}, which ${model} needs through YOUI; update Claude Code (claude update)`,
      };
    }
    return { ok: true, version: answer.version, minimum, error: null };
  }
  return { check, version };
}
