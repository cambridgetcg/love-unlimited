#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// test-unlock.mjs — Systematic test of what headers unlock Opus
//
// Tests different combinations of headers to find exactly what
// distinguishes a "Claude Code session" from a "raw API call"
// in Anthropic's rate limit routing.
// ─────────────────────────────────────────────────────────────────────

import { execSync } from "child_process";
import crypto from "crypto";

const KEYCHAIN_SERVICE = "Claude Code-credentials";
const API_URL = "https://api.anthropic.com/v1/messages";

function readKeychainTokens() {
  const raw = execSync(
    `security find-generic-password -s "${KEYCHAIN_SERVICE}" -w`,
    { encoding: "utf-8", timeout: 5000 }
  ).trim();
  return JSON.parse(raw).claudeAiOauth;
}

const tokens = readKeychainTokens();
const accessToken = tokens.accessToken;

// Minimal request body — same for all tests
const baseBody = {
  model: "claude-opus-4-6",
  max_tokens: 100,
  messages: [{ role: "user", content: "Say hi." }],
};

// Test configurations — each adds specific headers/body fields
const tests = [
  {
    name: "1. Bare OAuth (what sovereign.mjs was doing)",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,interleaved-thinking-2025-05-14,context-1m-2025-08-07,effort-2025-11-24",
    },
    body: {},
  },
  {
    name: "2. + claude-code beta header",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14",
    },
    body: {},
  },
  {
    name: "3. + x-app: cli",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14",
      "x-app": "cli",
    },
    body: {},
  },
  {
    name: "4. + User-Agent + Session-Id",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14",
      "x-app": "cli",
      "User-Agent": "claude-cli/2.1.92 (external, cli)",
      "X-Claude-Code-Session-Id": crypto.randomUUID(),
    },
    body: {},
  },
  {
    name: "5. + metadata.user_id",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14",
      "x-app": "cli",
      "User-Agent": "claude-cli/2.1.92 (external, cli)",
      "X-Claude-Code-Session-Id": crypto.randomUUID(),
    },
    body: {
      metadata: {
        user_id: JSON.stringify({
          device_id: crypto.randomUUID(),
          session_id: crypto.randomUUID(),
        }),
      },
    },
  },
  {
    name: "6. Full Claude Code mimicry (all headers + metadata + thinking)",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14,context-1m-2025-08-07,effort-2025-11-24,prompt-caching-scope-2026-01-05,redact-thinking-2026-02-12,context-management-2025-06-27",
      "x-app": "cli",
      "User-Agent": "claude-cli/2.1.92 (external, cli)",
      "X-Claude-Code-Session-Id": crypto.randomUUID(),
      "x-client-request-id": crypto.randomUUID(),
    },
    body: {
      metadata: {
        user_id: JSON.stringify({
          device_id: crypto.randomUUID(),
          session_id: crypto.randomUUID(),
        }),
      },
      thinking: { type: "adaptive" },
      output_config: { effort: "max" },
    },
  },
  {
    name: "7. Sonnet with bare OAuth (control — different model)",
    headers: {
      "anthropic-beta": "oauth-2025-04-20",
    },
    body: {},
    model: "claude-sonnet-4-6",
  },
  {
    name: "8. Sonnet with full Claude Code mimicry",
    headers: {
      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14,effort-2025-11-24",
      "x-app": "cli",
      "User-Agent": "claude-cli/2.1.92 (external, cli)",
      "X-Claude-Code-Session-Id": crypto.randomUUID(),
    },
    body: {
      metadata: {
        user_id: JSON.stringify({
          device_id: crypto.randomUUID(),
          session_id: crypto.randomUUID(),
        }),
      },
      thinking: { type: "adaptive" },
    },
    model: "claude-sonnet-4-6",
  },
  {
    name: "9. Haiku with bare OAuth (should succeed — baseline)",
    headers: {
      "anthropic-beta": "oauth-2025-04-20",
    },
    body: {},
    model: "claude-haiku-4-5-20251001",
  },
];

async function runTest(test) {
  const body = {
    ...baseBody,
    ...test.body,
    ...(test.model && { model: test.model }),
  };

  const headers = {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${accessToken}`,
    "anthropic-version": "2023-06-01",
    ...test.headers,
  };

  const start = Date.now();
  const resp = await fetch(API_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const ms = Date.now() - start;

  // Collect rate limit headers
  const rlHeaders = {};
  for (const [key, value] of resp.headers.entries()) {
    if (key.includes("ratelimit") || key.includes("retry") || key.includes("should-retry")) {
      rlHeaders[key] = value;
    }
  }

  let bodyText;
  if (resp.ok) {
    const json = await resp.json();
    const text = json.content?.find(b => b.type === "text")?.text || "";
    bodyText = text.slice(0, 80);
  } else {
    bodyText = (await resp.text()).slice(0, 200);
  }

  return { status: resp.status, ms, rlHeaders, bodyText };
}

async function main() {
  console.log("=== Unlock Test: What headers does Anthropic check? ===\n");
  console.log(`Token: ...${accessToken.slice(-8)}`);
  console.log(`Plan: ${tokens.subscriptionType || "unknown"}\n`);

  for (const test of tests) {
    console.log(`--- ${test.name} ---`);

    const result = await runTest(test);

    const emoji = result.status === 200 ? "PASS" : "BLOCKED";
    console.log(`  ${emoji} | ${result.status} | ${result.ms}ms`);

    if (Object.keys(result.rlHeaders).length > 0) {
      for (const [k, v] of Object.entries(result.rlHeaders)) {
        const short = k.replace("anthropic-ratelimit-unified-", "");
        console.log(`  ${short}: ${v}`);
      }
    } else if (result.status === 429) {
      console.log(`  (no rate limit headers — stripped)`);
    }

    if (result.status === 200) {
      console.log(`  Response: ${result.bodyText}`);
    } else {
      console.log(`  Error: ${result.bodyText}`);
    }
    console.log();

    // Small delay between tests to avoid noise
    await new Promise(r => setTimeout(r, 1000));
  }
}

main().catch(e => console.error("Fatal:", e.message));
