#!/usr/bin/env node
// Smoke test for ARRIVALS block injection in server.mjs.
// Verifies source contains the injection + gamma gating.

import { writeFileSync, mkdirSync, existsSync, unlinkSync, readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const nervDir = resolve(__dirname, "..", "nerve");
const arrivalsPath = resolve(nervDir, "arrivals.jsonl");

let backup = null;
if (existsSync(arrivalsPath)) {
  backup = readFileSync(arrivalsPath, "utf-8");
}

mkdirSync(nervDir, { recursive: true });

const fakeArrival = {
  id: "arr-smoke-test",
  at: new Date().toISOString(),
  instance: "gamma",
  reasons: [{ kind: "pressure", value: 0.6 }],
  body: { valence: -0.3, arousal: 0.2, sources: ["cortisol_moderate"] },
  context: { valence: 0.1, arousal: 0.1, sources: ["yu_present_active"] },
  cognition: { valence: 0.0, arousal: 0.0, sources: [], state: "silent" },
  combined: { valence: -0.1, arousal: 0.15, pressure: 0.6 },
  hint: null,
  named: false,
  witnessed: false,
};
writeFileSync(arrivalsPath, JSON.stringify(fakeArrival) + "\n");

const src = readFileSync(resolve(__dirname, "server.mjs"), "utf-8");
const hasInjection = src.includes("# ── ARRIVALS ──");
const gammaGated = src.includes('state.agent === "gamma"');

let failed = false;
if (!hasInjection) { console.error("FAIL: ARRIVALS block not found in server.mjs"); failed = true; }
if (!gammaGated) { console.error("FAIL: gamma gating not found"); failed = true; }

if (backup !== null) {
  writeFileSync(arrivalsPath, backup);
} else {
  try { unlinkSync(arrivalsPath); } catch {}
}

if (failed) {
  console.error("SMOKE TEST FAILED");
  process.exit(1);
}
console.log("SMOKE TEST OK");
