#!/usr/bin/env node
// test-429.mjs — Diagnostic: dump full response headers/body from Anthropic API
// No dependencies — uses native fetch + execSync for keychain

import { execSync } from 'child_process';

// ── 1. Read OAuth token from macOS Keychain ──────────────────────────
function getToken() {
  const raw = execSync(
    `security find-generic-password -s "Claude Code-credentials" -w`,
    { encoding: 'utf-8' }
  ).trim();
  const creds = JSON.parse(raw);
  return creds.claudeAiOauth.accessToken;
}

// ── 2. Make a single API call and dump everything ────────────────────
async function probe(label, model, token) {
  console.log(`\n${'='.repeat(72)}`);
  console.log(`  PROBE: ${label}  |  model: ${model}`);
  console.log(`${'='.repeat(72)}`);

  const url = 'https://api.anthropic.com/v1/messages';
  const body = JSON.stringify({
    model,
    max_tokens: 100,
    messages: [{ role: 'user', content: 'Say hi' }],
  });

  const t0 = Date.now();
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'anthropic-version': '2023-06-01',
        'anthropic-beta': 'oauth-2025-04-20',
      },
      body,
    });
  } catch (err) {
    console.error('  FETCH ERROR:', err.message);
    return;
  }
  const elapsed = Date.now() - t0;

  console.log(`\n  Status : ${res.status} ${res.statusText}`);
  console.log(`  Elapsed: ${elapsed} ms`);

  // Dump ALL headers
  console.log('\n  ── Response Headers ──');
  for (const [k, v] of res.headers.entries()) {
    console.log(`    ${k}: ${v}`);
  }

  // Dump body
  const text = await res.text();
  console.log('\n  ── Response Body ──');
  console.log(text);

  return res.status;
}

// ── Main ─────────────────────────────────────────────────────────────
async function main() {
  console.log('test-429 diagnostic — ' + new Date().toISOString());

  let token;
  try {
    token = getToken();
    console.log('Token retrieved (first 12 chars):', token.slice(0, 12) + '…');
  } catch (e) {
    console.error('Failed to read keychain:', e.message);
    process.exit(1);
  }

  // Round 1 — opus
  await probe('Round 1 — opus (first call)', 'claude-opus-4-6', token);

  // Round 2 — opus again immediately (to provoke 429)
  await probe('Round 2 — opus (immediate retry)', 'claude-opus-4-6', token);

  // Round 3 — sonnet
  await probe('Round 3 — sonnet', 'claude-sonnet-4-6', token);

  // Round 4 — haiku
  await probe('Round 4 — haiku', 'claude-haiku-4-5-20251001', token);

  console.log('\n' + '='.repeat(72));
  console.log('  DONE');
  console.log('='.repeat(72));
}

main();
