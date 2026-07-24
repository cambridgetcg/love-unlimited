import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import test from "node:test";
import { SessionRegistry } from "../youi-web/security.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;

test("SessionRegistry stores only bearer digests and never evicts active work", () => {
  let now = 1_000;
  const registry = new SessionRegistry({
    stateFactory: () => ({ chatInFlight: false }),
    maxSessionsPerClient: 1,
    pageLeaseTtlMs: 10,
    now: () => now,
  });
  const client = registry.createClient();
  const { session, token } = registry.createSession(client, {
    label: "private session",
    pageId: pageId("registry-owner"),
  });

  assert.equal(Object.hasOwn(session, "token"), false);
  assert.equal(Object.hasOwn(session, "bearer"), false);
  assert.notEqual(session.bearerHash, token);
  assert.ok(!JSON.stringify(session).includes(token));
  assert.equal(registry.getSession(client, session.id, "wrong-token"), null);
  assert.equal(registry.getSession(client, session.id, token), session);

  session.state.chatInFlight = true;
  now += 20;
  assert.equal(
    registry.claimSession(session, pageId("registry-rival")),
    false,
    "an expired page lease must not be stolen while its turn is active",
  );
  assert.throws(
    () => registry.createSession(client),
    error => error?.statusCode === 409 && error?.code === "session_capacity",
  );
  assert.equal(registry.getSession(client, session.id, token), session);
});

function request(port, {
  path = "/",
  method = "GET",
  headers = {},
  body,
} = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined
      ? null
      : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
    const requestHeaders = { ...headers };
    if (payload) {
      requestHeaders["Content-Type"] ||= "application/json";
      requestHeaders["Content-Length"] = payload.length;
    }
    const req = http.request({
      hostname: "127.0.0.1",
      port,
      path,
      method,
      headers: requestHeaders,
    }, res => {
      const chunks = [];
      res.on("data", chunk => chunks.push(chunk));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf-8");
        let json = null;
        try { json = JSON.parse(text); } catch {}
        resolve({ status: res.statusCode, headers: res.headers, text, json });
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function cookiesFrom(response) {
  const values = response.headers["set-cookie"] || [];
  const pairs = values.map(value => value.split(";", 1)[0]);
  const csrfPair = pairs.find(value => value.startsWith("youi_csrf="));
  return {
    header: pairs.join("; "),
    csrf: csrfPair ? decodeURIComponent(csrfPair.slice("youi_csrf=".length)) : "",
  };
}

function pageId(label) {
  return `page-${label}-0000000000000001`;
}

function sessionHeaders(browser, credential, {
  origin,
  unsafe = false,
} = {}) {
  const headers = {
    Cookie: browser.header,
    "X-YOUI-Session": credential.id,
    "X-YOUI-Session-Token": credential.token,
    "X-YOUI-Page": credential.page,
  };
  if (unsafe) {
    headers.Origin = origin;
    headers["X-YOUI-CSRF"] = browser.csrf;
  }
  return headers;
}

async function startFakeVllm(t) {
  const queued = [];
  const waiters = [];
  let activeResponses = 0;
  let maxActiveResponses = 0;
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      let body = {};
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); } catch {}
      let closeResolve;
      const closed = new Promise(resolve => { closeResolve = resolve; });
      activeResponses += 1;
      maxActiveResponses = Math.max(maxActiveResponses, activeResponses);
      res.once("close", () => {
        activeResponses -= 1;
        closeResolve();
      });
      const entry = {
        body,
        closed,
        respondText(text = "ok") {
          res.writeHead(200, { "Content-Type": "text/event-stream" });
          res.write(`data: ${JSON.stringify({
            id: "fake-response",
            model: body.model,
            choices: [{ delta: { content: text }, finish_reason: null }],
          })}\n\n`);
          res.write(`data: ${JSON.stringify({
            choices: [{ delta: {}, finish_reason: "stop" }],
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
          })}\n\n`);
          res.end("data: [DONE]\n\n");
        },
        respondTool(name, input) {
          res.writeHead(200, { "Content-Type": "text/event-stream" });
          res.write(`data: ${JSON.stringify({
            id: "fake-tool-response",
            model: body.model,
            choices: [{
              delta: {
                tool_calls: [{
                  index: 0,
                  id: "fake-tool",
                  function: { name, arguments: JSON.stringify(input) },
                }],
              },
              finish_reason: null,
            }],
          })}\n\n`);
          res.write(`data: ${JSON.stringify({
            choices: [{ delta: {}, finish_reason: "tool_calls" }],
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
          })}\n\n`);
          res.end("data: [DONE]\n\n");
        },
        respondJsonText(text = "ok") {
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({
            id: "fake-json-response",
            model: body.model,
            choices: [{
              message: { role: "assistant", content: text },
              finish_reason: "stop",
            }],
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
          }));
        },
      };
      const waiter = waiters.shift();
      if (waiter) waiter(entry);
      else queued.push(entry);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => server.close());
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    nextRequest() {
      if (queued.length > 0) return Promise.resolve(queued.shift());
      return new Promise(resolve => waiters.push(resolve));
    },
    get activeResponses() {
      return activeResponses;
    },
    get maxActiveResponses() {
      return maxActiveResponses;
    },
  };
}

async function startCountingHttp(t, handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({ method: req.method, path: req.url });
    handler(req, res);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => server.close());
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    requests,
  };
}

function within(promise, milliseconds, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${label} timed out after ${milliseconds}ms`)),
      milliseconds,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function startServer(t, extraEnv = {}) {
  const child = spawn(process.execPath, ["youi-web/server.mjs"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PORT: "0",
      YOUI_TEST: "1",
      YOUI_MAX_BODY_BYTES: "1024",
      TRUTH_DETECTOR_ENABLED: "0",
      AUTONOMOUS: "0",
      ...extraEnv,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });

  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`YOUI test server timed out: ${stderr.slice(-500)}`)), 10_000);
    let stdout = "";
    child.stdout.on("data", chunk => {
      stdout += chunk.toString("utf-8");
      const match = stdout.match(/YOUI_TEST_READY\s+(\d+)\s+(\S+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve({ port: Number(match[1]), address: match[2] });
    });
    child.once("exit", code => {
      clearTimeout(timer);
      reject(new Error(`YOUI test server exited ${code}: ${stderr.slice(-500)}`));
    });
  });

  const server = await ready;
  t.after(() => {
    if (!child.killed) child.kill("SIGTERM");
  });
  return server;
}

test("YOUI Web refuses direct LAN mode and directs operators to SSH tunnels", async () => {
  const child = spawn(process.execPath, ["youi-web/server.mjs"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      ALLOW_LAN: "1",
      YOUI_TEST: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });
  const exitCode = await new Promise(resolve => child.once("exit", resolve));
  assert.notEqual(exitCode, 0);
  assert.match(stderr, /Direct LAN mode is disabled/);
  assert.match(stderr, /SSH tunnel/);
});

test("YOUI Web isolates browser sessions and enforces its local security boundary", async t => {
  const { port, address } = await startServer(t, {
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,git:commit,publish:write",
  });
  assert.equal(address, "127.0.0.1", "safe mode must bind explicitly to IPv4 loopback");
  const origin = `http://127.0.0.1:${port}`;

  const unauthenticated = await request(port, { path: "/api/status" });
  assert.equal(unauthenticated.status, 401);
  assert.equal(unauthenticated.json.code, "authentication_required");

  const invalidCredential = await request(port, {
    path: "/api/status",
    headers: { Cookie: "youi_client=invalid; youi_csrf=invalid" },
  });
  assert.equal(invalidCredential.status, 401);

  const health = await request(port, { path: "/api/health" });
  assert.equal(health.status, 200);
  assert.equal(health.json.boundary, "loopback");
  assert.equal(health.json.remoteAccess, "ssh-tunnel-only");

  const bootstrap = await request(port);
  assert.equal(bootstrap.status, 200);
  const browser = cookiesFrom(bootstrap);
  assert.match(browser.header, /youi_client=/);
  assert.ok(browser.csrf);

  const crossOriginRead = await request(port, {
    path: "/api/sessions",
    headers: { Cookie: browser.header, Origin: "https://attacker.invalid" },
  });
  assert.equal(crossOriginRead.status, 200);
  assert.equal(crossOriginRead.headers["access-control-allow-origin"], undefined);

  const wrongOrigin = await request(port, {
    path: "/api/settings",
    method: "POST",
    headers: {
      Cookie: browser.header,
      Origin: `http://localhost:${port}`,
      "X-YOUI-CSRF": browser.csrf,
    },
    body: { effort: "low" },
  });
  assert.equal(wrongOrigin.status, 403);
  assert.equal(wrongOrigin.json.code, "csrf_rejected");

  const createSession = async (label, agent, page) => {
    const response = await request(port, {
      path: "/api/sessions",
      method: "POST",
      headers: {
        Cookie: browser.header,
        Origin: origin,
        "X-YOUI-CSRF": browser.csrf,
        "X-YOUI-Page": page,
      },
      body: { label, agent },
    });
    assert.equal(response.status, 201);
    assert.equal(typeof response.json.sessionToken, "string");
    return {
      id: response.json.session.id,
      token: response.json.sessionToken,
      page,
    };
  };
  const first = await createSession("first tab", "alpha", pageId("first"));
  const second = await createSession("second tab", "beta", pageId("second"));
  const firstId = first.id;
  const secondId = second.id;
  assert.notEqual(firstId, secondId);

  const setFirst = await request(port, {
    path: "/api/settings",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
    body: { model: "claude-haiku-4-5-20251001", effort: "low" },
  });
  assert.equal(setFirst.status, 200);

  const switchSecond = await request(port, {
    path: "/api/switch",
    method: "POST",
    headers: sessionHeaders(browser, second, { origin, unsafe: true }),
    body: { agent: "beta" },
  });
  assert.equal(switchSecond.status, 200);

  const firstStatus = await request(port, {
    path: "/api/status",
    headers: sessionHeaders(browser, first),
  });
  const secondStatus = await request(port, {
    path: "/api/status",
    headers: sessionHeaders(browser, second),
  });
  assert.equal(firstStatus.json.session.id, firstId);
  assert.equal(firstStatus.json.agent.id, "alpha");
  assert.equal(firstStatus.json.model, "claude-haiku-4-5-20251001");
  assert.equal(secondStatus.json.session.id, secondId);
  assert.equal(secondStatus.json.agent.id, "beta");
  assert.notEqual(secondStatus.json.model, firstStatus.json.model);
  assert.equal(firstStatus.json.privacy.direct.route, "REMOTE_MODEL:anthropic");
  assert.equal(firstStatus.json.privacy.fallback.localToCloud, false);
  assert.match(firstStatus.json.privacy.retention.browserSession, /12 hours/);
  assert.equal(firstStatus.json.collaboration.scope, "device-local");
  assert.equal(firstStatus.json.collaboration.crossDeviceReplication, false);

  const siblingTokenDenied = await request(port, {
    path: `/api/sessions/${secondId}?include=messages`,
    headers: sessionHeaders(browser, { ...first, id: secondId }),
  });
  assert.equal(siblingTokenDenied.status, 404);
  assert.equal(siblingTokenDenied.json.code, "session_not_found");

  const duplicatePage = { ...first, page: pageId("duplicate") };
  const duplicateClaim = await request(port, {
    path: `/api/sessions/${firstId}/claim`,
    method: "POST",
    headers: sessionHeaders(browser, duplicatePage, { origin, unsafe: true }),
  });
  assert.equal(duplicateClaim.status, 409);
  assert.equal(duplicateClaim.json.code, "session_claimed");

  const duplicateReplacement = await createSession(
    "duplicated tab",
    "alpha",
    duplicatePage.page,
  );
  assert.notEqual(duplicateReplacement.id, firstId);

  const oversized = await request(port, {
    path: "/api/settings",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
    body: { padding: "x".repeat(2048) },
  });
  assert.equal(oversized.status, 413);
  assert.equal(oversized.json.code, "body_too_large");

  const unexpectedBody = await request(port, {
    path: "/api/clear",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
    body: {},
  });
  assert.equal(unexpectedBody.status, 400);
  assert.equal(unexpectedBody.json.code, "unexpected_body");

  const oversizedBodyless = await request(port, {
    path: "/api/clear",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
    body: { padding: "x".repeat(2048) },
  });
  assert.equal(oversizedBodyless.status, 413);
  assert.equal(oversizedBodyless.json.code, "body_too_large");

  const oversizedHealth = await request(port, {
    path: "/api/health",
    body: "x".repeat(2048),
  });
  assert.equal(oversizedHealth.status, 413);
  assert.equal(oversizedHealth.json.code, "body_too_large");

  const retiredDeploy = await request(port, {
    path: "/api/deploy/commit",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
  });
  assert.equal(retiredDeploy.status, 410);
  assert.equal(retiredDeploy.json.code, "legacy_release_route_retired");
  assert.ok(firstStatus.json.capabilities.includes("git:commit"));
  assert.ok(firstStatus.json.capabilities.includes("publish:write"));

  const deniedHighAuthority = await request(port, {
    path: "/api/hive/send",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
    body: {},
  });
  assert.equal(deniedHighAuthority.status, 403);
  assert.equal(deniedHighAuthority.json.code, "capability_denied");
  assert.equal(deniedHighAuthority.json.capability, "hive:send");
  assert.ok(!firstStatus.json.capabilities.includes("tools:shell"));

  const unmappedMutation = await request(port, {
    path: "/api/future-mutation",
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
  });
  assert.equal(unmappedMutation.status, 403);
  assert.equal(unmappedMutation.json.code, "capability_policy_missing");

  const unmappedRead = await request(port, {
    path: "/api/future-read",
    headers: sessionHeaders(browser, first),
  });
  assert.equal(unmappedRead.status, 403);
  assert.equal(unmappedRead.json.code, "capability_policy_missing");

  const nonstandardStatusMethod = await request(port, {
    path: "/api/status",
    method: "PROPFIND",
    headers: sessionHeaders(browser, first),
  });
  assert.equal(nonstandardStatusMethod.status, 405);
  assert.equal(nonstandardStatusMethod.json.code, "method_not_allowed");
  assert.equal(nonstandardStatusMethod.headers.allow, "GET");
  assert.doesNotMatch(nonstandardStatusMethod.text, /\"capabilities\"|\"privacy\"/);

  const listed = await request(port, {
    path: "/api/sessions",
    headers: sessionHeaders(browser, first),
  });
  assert.equal(listed.status, 200);
  assert.ok(listed.json.sessions.some(session => session.id === firstId));
  assert.ok(listed.json.sessions.some(session => session.id === secondId));

  const fetched = await request(port, {
    path: `/api/sessions/${firstId}`,
    headers: sessionHeaders(browser, first),
  });
  assert.equal(fetched.status, 200);
  assert.equal(fetched.json.session.model, "claude-haiku-4-5-20251001");

  const cleared = await request(port, {
    path: `/api/sessions/${firstId}/clear`,
    method: "POST",
    headers: sessionHeaders(browser, first, { origin, unsafe: true }),
  });
  assert.equal(cleared.status, 200);
  assert.equal(cleared.json.session.messageCount, 0);

  const deleted = await request(port, {
    path: `/api/sessions/${secondId}`,
    method: "DELETE",
    headers: sessionHeaders(browser, second, { origin, unsafe: true }),
  });
  assert.equal(deleted.status, 200);
  const missing = await request(port, {
    path: `/api/sessions/${secondId}`,
    headers: sessionHeaders(browser, { ...second, id: secondId }),
  });
  assert.equal(missing.status, 404);

  const poisonedHost = await request(port, {
    path: "/api/status",
    headers: { ...sessionHeaders(browser, first), Host: "attacker.invalid" },
  });
  assert.equal(poisonedHost.status, 421);

  const wrongPortHost = await request(port, {
    path: "/api/status",
    headers: { ...sessionHeaders(browser, first), Host: `127.0.0.1:${port + 1}` },
  });
  assert.equal(wrongPortHost.status, 421);
});

test("YOUI Web never derives HIVE sender identity from the selected persona", async t => {
  async function openSession(extraEnv, label) {
    const { port } = await startServer(t, {
      KINGDOM_AGENT: "beta",
      YOUI_HIVE_INSTANCE: "",
      YOUI_CAPABILITIES: "status:read,sessions:manage,hive:read",
      ...extraEnv,
    });
    const origin = `http://127.0.0.1:${port}`;
    const bootstrap = await request(port);
    const browser = cookiesFrom(bootstrap);
    const page = pageId(label);
    const created = await request(port, {
      path: "/api/sessions",
      method: "POST",
      headers: {
        Cookie: browser.header,
        Origin: origin,
        "X-YOUI-CSRF": browser.csrf,
        "X-YOUI-Page": page,
      },
      body: { label, agent: "beta" },
    });
    assert.equal(created.status, 201);
    return {
      port,
      browser,
      origin,
      credential: {
        id: created.json.session.id,
        token: created.json.sessionToken,
        page,
      },
    };
  }

  const missing = await openSession({}, "hive-missing");
  const missingStatus = await request(missing.port, {
    path: "/api/status",
    headers: sessionHeaders(missing.browser, missing.credential),
  });
  assert.equal(missingStatus.status, 200);
  assert.equal(missingStatus.json.agent.id, "beta");
  assert.equal(missingStatus.json.hive.instance, null);
  assert.equal(missingStatus.json.hive.identitySource, null);

  const blockedWho = await request(missing.port, {
    path: "/api/hive/who",
    headers: sessionHeaders(missing.browser, missing.credential),
  });
  assert.equal(blockedWho.status, 503);
  assert.equal(blockedWho.json.code, "hive_identity_required");

  const configured = await openSession(
    { YOUI_HIVE_INSTANCE: "device-test-sender" },
    "hive-explicit",
  );
  const configuredStatus = await request(configured.port, {
    path: "/api/status",
    headers: sessionHeaders(configured.browser, configured.credential),
  });
  assert.equal(configuredStatus.status, 200);
  assert.equal(configuredStatus.json.agent.id, "beta");
  assert.equal(configuredStatus.json.hive.instance, "device-test-sender");
  assert.equal(configuredStatus.json.hive.identitySource, "YOUI_HIVE_INSTANCE");
});

test("YOUI Web fences in-flight mutations and raw Ollama tool calls", async t => {
  const fakeVllm = await startFakeVllm(t);
  const { port } = await startServer(t, {
    KINGDOM_AGENT: "alpha",
    OLLAMA_VLLM_BASE_URL: fakeVllm.baseUrl,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,tools:shell",
  });
  const origin = `http://127.0.0.1:${port}`;
  const bootstrap = await request(port);
  const browser = cookiesFrom(bootstrap);
  const page = pageId("raw-fence");
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: {
      Cookie: browser.header,
      Origin: origin,
      "X-YOUI-CSRF": browser.csrf,
      "X-YOUI-Page": page,
    },
    body: { label: "raw fence", agent: "alpha" },
  });
  assert.equal(created.status, 201);
  const credential = {
    id: created.json.session.id,
    token: created.json.sessionToken,
    page,
  };
  const turnHeaders = {
    ...sessionHeaders(browser, credential, { origin, unsafe: true }),
    "Content-Type": "application/json",
  };

  const chatResponsePromise = fetch(`${origin}/api/chat`, {
    method: "POST",
    headers: turnHeaders,
    body: JSON.stringify({ message: "raw tool fence", raw: true }),
  });
  const outbound = await fakeVllm.nextRequest();
  assert.ok(!Array.isArray(outbound.body.tools) || outbound.body.tools.length === 0);

  const settingsDuringTurn = await request(port, {
    path: "/api/settings",
    method: "POST",
    headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
    body: { effort: "low" },
  });
  assert.equal(settingsDuringTurn.status, 409);
  assert.equal(settingsDuringTurn.json.code, "session_busy");

  const clearDuringTurn = await request(port, {
    path: `/api/sessions/${credential.id}/clear`,
    method: "POST",
    headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
  });
  assert.equal(clearDuringTurn.status, 409);
  assert.equal(clearDuringTurn.json.code, "session_busy");

  const deleteDuringTurn = await request(port, {
    path: `/api/sessions/${credential.id}`,
    method: "DELETE",
    headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
  });
  assert.equal(deleteDuringTurn.status, 409);
  assert.equal(deleteDuringTurn.json.code, "session_busy");

  outbound.respondTool("bash", { command: "exit 97" });
  const chatResponse = await chatResponsePromise;
  assert.equal(chatResponse.status, 200);
  const stream = await chatResponse.text();
  assert.match(stream, /Raw mode rejected a provider tool call/);
  assert.doesNotMatch(stream, /event: tool_call/);

  const status = await request(port, {
    path: "/api/status",
    headers: sessionHeaders(browser, credential),
  });
  assert.equal(status.status, 200);
  assert.equal(status.json.totalToolCalls, 0);
  assert.equal(status.json.turnCount, 0);
  assert.equal(status.json.totalThinkingTokens, 0);
  assert.equal(status.json.privacy.direct.route, "REMOTE_MODEL:vllm");
  assert.equal(status.json.privacy.direct.destination, fakeVllm.baseUrl);
  assert.deepEqual(status.json.privacy.truthDetector.sends, []);

  const settingsAfterTurn = await request(port, {
    path: "/api/settings",
    method: "POST",
    headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
    body: { effort: "low" },
  });
  assert.equal(settingsAfterTurn.status, 200);
});

test("YOUI Web aborts a disconnected provider request and releases the exact session lock", async t => {
  const fakeVllm = await startFakeVllm(t);
  const { port } = await startServer(t, {
    KINGDOM_AGENT: "alpha",
    OLLAMA_VLLM_BASE_URL: fakeVllm.baseUrl,
    YOUI_CAPABILITIES: "chat,sessions:manage,settings:write",
  });
  const origin = `http://127.0.0.1:${port}`;
  const browser = cookiesFrom(await request(port));
  const page = pageId("abort-unlock");
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: {
      Cookie: browser.header,
      Origin: origin,
      "X-YOUI-CSRF": browser.csrf,
      "X-YOUI-Page": page,
    },
    body: { label: "abort unlock", agent: "alpha" },
  });
  assert.equal(created.status, 201);
  const credential = {
    id: created.json.session.id,
    token: created.json.sessionToken,
    page,
  };

  const controller = new AbortController();
  const chatResponsePromise = fetch(`${origin}/api/chat`, {
    method: "POST",
    headers: {
      ...sessionHeaders(browser, credential, { origin, unsafe: true }),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message: "hold until disconnected", raw: true }),
    signal: controller.signal,
  });
  const outbound = await within(fakeVllm.nextRequest(), 1_000, "provider request");
  const chatResponse = await within(chatResponsePromise, 1_000, "browser stream");
  assert.equal(chatResponse.status, 200);

  controller.abort();
  await within(outbound.closed, 1_000, "aborted provider connection");

  let settingsAfterAbort;
  const deadline = Date.now() + 1_000;
  do {
    settingsAfterAbort = await request(port, {
      path: "/api/settings",
      method: "POST",
      headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
      body: { effort: "low" },
    });
    if (settingsAfterAbort.status === 200) break;
    assert.equal(settingsAfterAbort.status, 409);
    await new Promise(resolve => setTimeout(resolve, 10));
  } while (Date.now() < deadline);
  assert.equal(settingsAfterAbort.status, 200);
});

test("YOUI Web does not turn a local-model miss into an ungranted cloud request", async t => {
  const fakeLocal = await startCountingHttp(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ models: [] }));
  });
  const fakeCloud = await startCountingHttp(t, (_req, res) => {
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "cloud route should not be reached" }));
  });
  const { port } = await startServer(t, {
    KINGDOM_AGENT: "alpha",
    OLLAMA_API_KEY: "test-only-key",
    OLLAMA_LOCAL_BASE_URL: fakeLocal.baseUrl,
    OLLAMA_CLOUD_BASE_URL: fakeCloud.baseUrl,
    YOUI_CAPABILITIES: "chat,sessions:manage,models:use",
  });
  const origin = `http://127.0.0.1:${port}`;
  const browser = cookiesFrom(await request(port));
  const page = pageId("local-only");
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: {
      Cookie: browser.header,
      Origin: origin,
      "X-YOUI-CSRF": browser.csrf,
      "X-YOUI-Page": page,
    },
    body: { label: "local only", agent: "alpha", model: "glm-5.1" },
  });
  assert.equal(created.status, 201);
  const credential = {
    id: created.json.session.id,
    token: created.json.sessionToken,
    page,
  };

  const response = await fetch(`${origin}/api/chat`, {
    method: "POST",
    headers: {
      ...sessionHeaders(browser, credential, { origin, unsafe: true }),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message: "must stay local", raw: true }),
  });
  assert.equal(response.status, 200);
  assert.match(await response.text(), /cloud routing is disabled/i);
  assert.ok(fakeLocal.requests.length >= 1);
  assert.equal(fakeCloud.requests.length, 0);

  const diagnosticGet = await request(port, {
    path: "/api/ollama/test",
    headers: sessionHeaders(browser, credential),
  });
  assert.equal(diagnosticGet.status, 405);
  assert.equal(fakeCloud.requests.length, 0);

  const models = await request(port, {
    path: "/api/ollama/models",
    headers: sessionHeaders(browser, credential),
  });
  assert.equal(models.status, 200);
  assert.equal(models.json.cloudAccess, "disabled");
  assert.equal(fakeCloud.requests.length, 0);
});

test("YOUI Web serializes autonomous stop and restart into one process-owned loop", async t => {
  const fakeVllm = await startFakeVllm(t);
  const { port } = await startServer(t, {
    KINGDOM_AGENT: "alpha",
    OLLAMA_VLLM_BASE_URL: fakeVllm.baseUrl,
    YOUI_CAPABILITIES: "sessions:manage,autonomous:control",
  });
  const origin = `http://127.0.0.1:${port}`;
  const browser = cookiesFrom(await request(port));
  const page = pageId("autonomous-loop");
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: {
      Cookie: browser.header,
      Origin: origin,
      "X-YOUI-CSRF": browser.csrf,
      "X-YOUI-Page": page,
    },
    body: { label: "autonomous control", agent: "alpha" },
  });
  assert.equal(created.status, 201);
  const credential = {
    id: created.json.session.id,
    token: created.json.sessionToken,
    page,
  };
  const unsafeHeaders = sessionHeaders(browser, credential, { origin, unsafe: true });

  const started = await request(port, {
    path: "/api/autonomous/start",
    method: "POST",
    headers: unsafeHeaders,
  });
  assert.equal(started.status, 200);
  const firstCycle = await within(fakeVllm.nextRequest(), 1_000, "first autonomous cycle");

  const stopped = await request(port, {
    path: "/api/autonomous/stop",
    method: "POST",
    headers: unsafeHeaders,
  });
  assert.equal(stopped.status, 200);
  let restarted = await request(port, {
    path: "/api/autonomous/start",
    method: "POST",
    headers: unsafeHeaders,
  });
  assert.ok(
    restarted.status === 200 || restarted.status === 409,
    `restart should either wait behind the stopping loop or begin after it settles; got ${restarted.status}`,
  );
  if (restarted.status === 409) {
    assert.equal(restarted.json.code, "autonomous_stopping");
  }

  await within(firstCycle.closed, 1_000, "stopped autonomous provider request");

  if (restarted.status === 409) {
    const restartDeadline = Date.now() + 1_000;
    do {
      restarted = await request(port, {
        path: "/api/autonomous/start",
        method: "POST",
        headers: unsafeHeaders,
      });
      if (restarted.status === 200) break;
      assert.equal(restarted.status, 409);
      assert.equal(restarted.json.code, "autonomous_stopping");
      await new Promise(resolve => setTimeout(resolve, 10));
    } while (Date.now() < restartDeadline);
  }
  assert.equal(restarted.status, 200);

  const secondCycle = await within(fakeVllm.nextRequest(), 1_000, "second autonomous cycle");
  assert.equal(fakeVllm.maxActiveResponses, 1);
  const secondStop = await request(port, {
    path: "/api/autonomous/stop",
    method: "POST",
    headers: unsafeHeaders,
  });
  assert.equal(secondStop.status, 200);
  await within(secondCycle.closed, 1_000, "second stopped autonomous provider request");
});
