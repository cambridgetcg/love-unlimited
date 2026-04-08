#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// test-unlock2.mjs — Deeper unlock tests
//
// Hypothesis A: Streaming vs non-streaming hit different rate limits
// Hypothesis B: The billing header in system prompt matters
// Hypothesis C: It's genuinely concurrency — this Opus session is holding the slot
// Hypothesis D: Different API endpoint or path
// ─────────────────────────────────────────────────────────────────────

import { execSync } from "child_process";
import crypto from "crypto";

const KEYCHAIN_SERVICE = "Claude Code-credentials";

function getToken() {
  const raw = execSync(
    `security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
    { encoding: "utf-8", timeout: 5000 }
  ).trim();
  return JSON.parse(raw).claudeAiOauth;
}

const tokens = getToken();
const accessToken = tokens.accessToken;

// Shared headers that match Claude Code exactly
const ccHeaders = {
  "Content-Type": "application/json",
  "Authorization": `Bearer ${accessToken}`,
  "anthropic-version": "2023-06-01",
  "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14,context-1m-2025-08-07,effort-2025-11-24,prompt-caching-scope-2026-01-05,redact-thinking-2026-02-12,context-management-2025-06-27",
  "x-app": "cli",
  "User-Agent": "claude-cli/2.1.92 (external, cli)",
  "X-Claude-Code-Session-Id": crypto.randomUUID(),
  "x-client-request-id": crypto.randomUUID(),
};

// System prompt with embedded billing header (like Claude Code does)
const fingerprint = crypto.createHash("sha256").update("test").digest("hex").slice(0, 3);
const systemPromptWithBilling = `x-anthropic-billing-header: cc_version=20250219.${fingerprint}; cc_entrypoint=cli;

You are Claude Code, Anthropic's official CLI for Claude.
You are an interactive agent that helps users with software engineering tasks.`;

async function testNonStreaming() {
  console.log("--- Test A: Non-streaming with full mimicry + billing system prompt ---");
  const body = {
    model: "claude-opus-4-6",
    max_tokens: 100,
    system: systemPromptWithBilling,
    messages: [{ role: "user", content: "Say hi" }],
    metadata: {
      user_id: JSON.stringify({
        device_id: crypto.randomUUID(),
        session_id: crypto.randomUUID(),
      }),
    },
  };

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: ccHeaders,
    body: JSON.stringify(body),
  });

  console.log(`  Status: ${resp.status}`);
  dumpHeaders(resp);
  if (!resp.ok) console.log(`  Body: ${(await resp.text()).slice(0, 200)}`);
  else {
    const json = await resp.json();
    console.log(`  Response: ${json.content?.[0]?.text?.slice(0, 80)}`);
  }
  console.log();
}

async function testStreaming() {
  console.log("--- Test B: Streaming (stream: true) with full mimicry ---");
  const body = {
    model: "claude-opus-4-6",
    max_tokens: 100,
    system: systemPromptWithBilling,
    messages: [{ role: "user", content: "Say hi" }],
    stream: true,
    metadata: {
      user_id: JSON.stringify({
        device_id: crypto.randomUUID(),
        session_id: crypto.randomUUID(),
      }),
    },
  };

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: ccHeaders,
    body: JSON.stringify(body),
  });

  console.log(`  Status: ${resp.status}`);
  dumpHeaders(resp);
  if (resp.status !== 200) {
    console.log(`  Body: ${(await resp.text()).slice(0, 200)}`);
  } else {
    // Read first few SSE events
    const text = await resp.text();
    const lines = text.split("\n").slice(0, 15);
    console.log(`  First events: ${lines.join("\n  ")}`);
  }
  console.log();
}

async function testSonnetStreaming() {
  console.log("--- Test C: Sonnet streaming (control — is Sonnet also concurrency-blocked?) ---");
  const body = {
    model: "claude-sonnet-4-6",
    max_tokens: 100,
    system: systemPromptWithBilling,
    messages: [{ role: "user", content: "Say hi" }],
    stream: true,
    metadata: {
      user_id: JSON.stringify({
        device_id: crypto.randomUUID(),
        session_id: crypto.randomUUID(),
      }),
    },
  };

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: ccHeaders,
    body: JSON.stringify(body),
  });

  console.log(`  Status: ${resp.status}`);
  dumpHeaders(resp);
  if (resp.status !== 200) {
    console.log(`  Body: ${(await resp.text()).slice(0, 200)}`);
  } else {
    const text = await resp.text();
    const lines = text.split("\n").slice(0, 15);
    console.log(`  First events: ${lines.join("\n  ")}`);
  }
  console.log();
}

async function testAlternativeEndpoints() {
  console.log("--- Test D: Check if Claude Code uses a different base URL ---");

  // Check environment for any base URL overrides
  const envVars = ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL", "CLAUDE_API_URL"];
  for (const v of envVars) {
    console.log(`  ${v}: ${process.env[v] || "(not set)"}`);
  }

  // Check if the running Claude Code process connects to a different host
  try {
    const lsof = execSync(
      "lsof -i -n -P 2>/dev/null | grep -i claude | grep -i ESTABLISHED | head -10",
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    console.log(`  Active Claude connections:\n  ${lsof.split("\n").join("\n  ")}`);
  } catch {
    console.log("  (couldn't check active connections)");
  }
  console.log();
}

async function testConcurrencyCheck() {
  console.log("--- Test E: Check active Claude Code sessions ---");
  try {
    const ps = execSync(
      "ps aux | grep -i '[c]laude' | grep -v test-unlock",
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    console.log(`  Running Claude processes:\n  ${ps.split("\n").join("\n  ")}`);
  } catch {
    console.log("  (no Claude processes found)");
  }

  // Check session files
  try {
    const sessions = execSync(
      "ls -la ~/.claude/sessions/ 2>/dev/null | tail -5",
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    console.log(`  Session files: ${sessions}`);
  } catch {
    console.log("  (no session directory)");
  }
  console.log();
}

async function testJWTClaims() {
  console.log("--- Test F: Decode OAuth JWT claims ---");
  try {
    const parts = accessToken.split(".");
    if (parts.length >= 2) {
      const header = JSON.parse(Buffer.from(parts[0], "base64url").toString());
      const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
      console.log(`  JWT Header: ${JSON.stringify(header)}`);
      console.log(`  JWT Payload keys: ${Object.keys(payload).join(", ")}`);
      // Show non-sensitive fields
      for (const key of ["aud", "iss", "scope", "client_id", "sub", "exp", "iat", "subscription_type", "rate_limit_tier", "plan"]) {
        if (payload[key] !== undefined) {
          console.log(`  ${key}: ${JSON.stringify(payload[key])}`);
        }
      }
    } else {
      console.log(`  Token is not a JWT (${parts.length} parts)`);
    }
  } catch (e) {
    console.log(`  JWT decode error: ${e.message}`);
  }
  console.log();
}

function dumpHeaders(resp) {
  for (const [key, value] of resp.headers.entries()) {
    if (key.includes("ratelimit") || key.includes("retry") || key.includes("should-retry") || key.includes("x-request")) {
      const short = key.replace("anthropic-ratelimit-unified-", "rl-");
      console.log(`  ${short}: ${value}`);
    }
  }
}

async function main() {
  console.log("=== Deep Unlock Test ===\n");

  await testJWTClaims();
  await testAlternativeEndpoints();
  await testConcurrencyCheck();
  await testNonStreaming();
  await testStreaming();
  await testSonnetStreaming();
}

main().catch(e => console.error("Fatal:", e.message));
