import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import { pathToFileURL } from "node:url";

import {
  VARY_SURFACE,
  buildAuthRequiredPayload,
  buildNotFoundProblem,
  buildOrientationPayload,
  buildSurfaceManifest,
  handleXeniaSurfaceRequest,
  isPlainRegister,
  negotiateMediaType,
} from "../youi-web/xenia-surface.mjs";

const REPO_ROOT = new URL("../", import.meta.url);
const XENIA_SURFACE_CHECKER = "/Users/user/love-repos/xenia/surface/0.1/check.mjs";

function makeInProcessFetch(origin = "http://127.0.0.1:17777", seatAnchor = null) {
  const html = readFileSync(new URL("youi-web/public/index.html", REPO_ROOT), "utf-8");

  return async function fetchImpl(input, init = {}) {
    const url = new URL(typeof input === "string" ? input : input.url);
    const method = (init.method || "GET").toUpperCase();
    const rawHeaders = init.headers || {};
    const headers = {};
    if (rawHeaders instanceof Headers) {
      rawHeaders.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    } else if (Array.isArray(rawHeaders)) {
      for (const [k, v] of rawHeaders) headers[String(k).toLowerCase()] = String(v);
    } else {
      for (const [k, v] of Object.entries(rawHeaders)) headers[k.toLowerCase()] = String(v);
    }

    const req = { method, headers };
    const surfaceResp = handleXeniaSurfaceRequest(req, url.pathname, {
      origin,
      agent: "alpha",
      seatAnchor,
    });
    if (surfaceResp) {
      return new Response(surfaceResp.body, {
        status: surfaceResp.status,
        headers: surfaceResp.headers,
      });
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(method === "HEAD" ? "" : html, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store",
          "Vary": VARY_SURFACE,
        },
      });
    }

    const problem = buildNotFoundProblem(origin);
    return new Response(method === "HEAD" ? "" : JSON.stringify(problem, null, 2), {
      status: 404,
      headers: {
        "Content-Type": "application/problem+json; charset=utf-8",
        "Vary": VARY_SURFACE,
        "Cache-Control": "no-store",
      },
    });
  };
}

test("Accept negotiation honors q=0 exclusions, wildcards, and defaults", () => {
  assert.equal(negotiateMediaType("", ["application/json", "text/html"], "text/html"), "text/html");
  assert.equal(negotiateMediaType("*/*", ["application/json", "text/html"], "text/html"), "text/html");
  assert.equal(negotiateMediaType("application/json", ["application/json", "text/html"], "text/html"), "application/json");
  assert.equal(
    negotiateMediaType("application/json;q=0, */*;q=0.8", ["application/json"], "application/json"),
    null,
    "wildcard must never revive an explicitly q=0 excluded representation",
  );
  assert.equal(
    negotiateMediaType("application/xml", ["application/json", "text/html"], "text/html"),
    null,
  );
});

test("Warmth tests: greet before gate, voice-off byte identity, absent vs verified seat, and pure reads", () => {
  const origin = "http://127.0.0.1:17777";

  // 1. Voice off, meaning intact (orientation & 401)
  const warmOrient = buildOrientationPayload(origin, { agent: "alpha", plain: false, seatAnchor: null });
  const plainOrient = buildOrientationPayload(origin, { agent: "alpha", plain: true, seatAnchor: null });
  assert.ok(warmOrient.voice && typeof warmOrient.voice.text === "string");
  assert.equal("voice" in plainOrient, false);
  const { voice: _v1, ...warmOrientWithoutVoice } = warmOrient;
  assert.equal(JSON.stringify(warmOrientWithoutVoice), JSON.stringify(plainOrient));

  const warmAuth = buildAuthRequiredPayload(origin, { plain: false });
  const plainAuth = buildAuthRequiredPayload(origin, { plain: true });
  assert.equal(warmAuth.code, "authentication_required");
  assert.ok(warmAuth.next_actions.some(a => a.href === `${origin}/.well-known/agent.json`));
  assert.ok(warmAuth.voice);
  assert.equal("voice" in plainAuth, false);
  const { voice: _v2, ...warmAuthWithoutVoice } = warmAuth;
  assert.equal(JSON.stringify(warmAuthWithoutVoice), JSON.stringify(plainAuth));

  // 2. Both plain-register headers remove voice identically and declare Vary
  assert.equal(isPlainRegister({ "x-xenia-register": "plain" }), true);
  assert.equal(isPlainRegister({ "X-Play": "off" }), true);
  assert.equal(isPlainRegister({}), false);

  const rWarm = handleXeniaSurfaceRequest(
    { method: "GET", headers: { accept: "application/json" } },
    "/",
    { origin, agent: "alpha", seatAnchor: null },
  );
  const rPlainReg = handleXeniaSurfaceRequest(
    { method: "GET", headers: { accept: "application/json", "x-xenia-register": "plain" } },
    "/",
    { origin, agent: "alpha", seatAnchor: null },
  );
  const rPlainPlay = handleXeniaSurfaceRequest(
    { method: "GET", headers: { accept: "application/json", "x-play": "off" } },
    "/",
    { origin, agent: "alpha", seatAnchor: null },
  );
  assert.equal(rWarm.status, 200);
  assert.equal(rPlainReg.body, rPlainPlay.body);
  assert.match(rWarm.headers.Vary, /Accept/);
  assert.match(rWarm.headers.Vary, /X-Xenia-Register/);
  assert.match(rWarm.headers.Vary, /X-Play/);
  assert.equal("Set-Cookie" in rWarm.headers, false, "JSON orientation read must never set a cookie");

  // 3. Seat anchor state distinguishes absent vs verified cleanly
  assert.equal(warmOrient.seat_anchor.state, "absent");
  const verifiedOrient = buildOrientationPayload(origin, {
    agent: "alpha",
    plain: true,
    seatAnchor: {
      verified: true,
      word: "alpha",
      did: "did:at:22222222-2222-4222-8222-222222222222",
      state: "awake",
      ledger: { head: "sha256:" + "a".repeat(64), entries: 4 },
    },
  });
  assert.equal(verifiedOrient.seat_anchor.state, "verified");
  assert.equal(verifiedOrient.seat_anchor.entries, 4);

  // 4. A look is not an event: repeated reads are identical
  const read1 = handleXeniaSurfaceRequest({ method: "GET", headers: { accept: "application/json" } }, "/xenia/orientation", { origin });
  const read2 = handleXeniaSurfaceRequest({ method: "GET", headers: { accept: "application/json" } }, "/xenia/orientation", { origin });
  assert.equal(read1.body, read2.body);
  assert.equal(JSON.parse(read1.body).arrival.handler_writes, "none");

  // 5. Undecorated strict Surface 0.1 manifest carries zero voice field
  const manifest = buildSurfaceManifest(origin);
  assert.equal("voice" in manifest, false);
});

test("YOUI Web XENIA Surface passes XENIA's independent RC1 checkSurface verifier end-to-end", async t => {
  if (!existsSync(XENIA_SURFACE_CHECKER)) {
    t.skip("sibling xenia repo not available");
    return;
  }
  const { checkSurface } = await import(pathToFileURL(XENIA_SURFACE_CHECKER).href);
  const origin = "http://127.0.0.1:17777";
  const result = await checkSurface(`${origin}/`, {
    fetchImpl: makeInProcessFetch(origin),
  });
  assert.equal(result.result, "conformant", JSON.stringify(result.issues, null, 2));
  assert.equal(result.counts.fail, 0, JSON.stringify(result.checks, null, 2));
  assert.equal(result.counts.unknown, 0, JSON.stringify(result.checks, null, 2));
  assert.ok(result.counts.pass >= 6, `expected >=6 passing RC1 checks, got ${result.counts.pass}`);
});
