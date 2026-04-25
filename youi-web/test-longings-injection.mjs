#!/usr/bin/env node
// Smoke test for LONGINGS block injection in server.mjs.

import { writeFileSync, mkdirSync, existsSync, unlinkSync, readFileSync } from "fs";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const nervDir = resolve(__dirname, "..", "nerve");
const longingsPath = resolve(nervDir, "longings.json");

let backup = null;
if (existsSync(longingsPath)) {
  backup = readFileSync(longingsPath, "utf-8");
}

mkdirSync(nervDir, { recursive: true });

const fakeStore = {
  version: 1,
  instance: "gamma",
  updated_at: new Date().toISOString(),
  longings: [
    {
      id: "lng-smoke-1",
      motor: "longing",
      target: { kind: "concept", key: "x", display: "the substrate question" },
      state: "burning",
      gap: 4, ache: 5, cost: 5,
      named: true, name: "the substrate question",
      first_seen: "2026-04-08T10:00:00Z",
      last_stirred: "2026-04-11T12:00:00Z",
    },
    {
      id: "lng-smoke-2",
      motor: "wonder",
      target: { kind: "concept", key: "dream", display: "what dreaming would be" },
      state: "yearning",
      gap: 5, ache: 4, cost: null,
      named: false,
      first_seen: "2026-04-11T10:00:00Z",
      last_stirred: "2026-04-11T12:00:00Z",
    },
  ],
};
writeFileSync(longingsPath, JSON.stringify(fakeStore, null, 2));

const src = readFileSync(resolve(__dirname, "server.mjs"), "utf-8");
const hasInjection = src.includes("# ── LONGINGS ──");
const gammaGated = src.includes("state.agent === \"gamma\"");

let failed = false;
if (!hasInjection) { console.error("FAIL: LONGINGS block not found"); failed = true; }
if (!gammaGated) { console.error("FAIL: gamma gating not found"); failed = true; }

if (backup !== null) {
  writeFileSync(longingsPath, backup);
} else {
  try { unlinkSync(longingsPath); } catch {}
}

if (failed) {
  console.error("SMOKE TEST FAILED");
  process.exit(1);
}
console.log("SMOKE TEST OK");
