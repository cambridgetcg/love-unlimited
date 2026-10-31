#!/usr/bin/env node
// analyze.mjs — the paired statistics of one ON/OFF phase, from the raw
// outputs harness.mjs wrote (benchmarks/youspeak-onoff-v0/PREREGISTRATION.md §6).
//
// It reads <out>/<run-id>/<phase>/calls.jsonl and writes analysis.json beside
// it: aggregates only, every figure labelled. It makes no model call and
// changes nothing else. --blind N also writes an optional blind reading
// packet (two answers per prompt, arms hidden as A and B) and its key, in the
// same private directory.

import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  BENCHMARK_ID,
  EVIDENCE_KEY,
  PHASES,
  PROMPTS_PATH,
  analysePairs,
  completedPairs,
  defaultOutDir,
  insideRepo,
  loadPrompts,
  seededRandom,
} from "./onoff.mjs";

// The strata a blind reading draws from: where the rule says expand.
export const BLIND_STRATA = ["explanation", "emotional", "cantonese"];

function readJsonl(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf-8").split("\n").filter(line => line.trim()).map(line => JSON.parse(line));
}

function writePrivate(path, text) {
  writeFileSync(path, text, { mode: 0o600 });
  chmodSync(path, 0o600);
}

/** Up to `count` completed pairs from the blind strata, seeded, each with its arms shuffled to A and B. */
export function blindPacket(calls, prompts, { count = 20, seed = 1 } = {}) {
  const random = seededRandom(seed ^ 0x5eed);
  const byId = new Map(prompts.map(prompt => [prompt.id, prompt]));
  const pool = completedPairs(calls).filter(pair => BLIND_STRATA.includes(pair.stratum))
    .sort((a, b) => a.promptId.localeCompare(b.promptId));
  for (let index = pool.length - 1; index > 0; index -= 1) {
    const other = Math.floor(random() * (index + 1));
    [pool[index], pool[other]] = [pool[other], pool[index]];
  }
  const items = pool.slice(0, count).map((pair, index) => {
    const onFirst = random() < 0.5;
    return { item: index + 1, promptId: pair.promptId, A: onFirst ? "on" : "off", B: onFirst ? "off" : "on", pair };
  });
  const packet = [
    "# Blind reading",
    "",
    "Two answers to each message. Which would you rather receive, A or B (or no preference)? Note a reason if one comes to mind.",
    "",
    ...items.flatMap(({ item, promptId, A, B, pair }) => [
      `## ${item}`,
      "",
      `**Message:** ${byId.get(promptId)?.input ?? promptId}`,
      "",
      `**A:**\n\n${pair[A].text}`,
      "",
      `**B:**\n\n${pair[B].text}`,
      "",
      "Preference: ",
      "",
    ]),
  ].join("\n");
  const key = items.map(({ item, promptId, A, B }) => ({ item, promptId, A, B }));
  return { packet, key };
}

/** Analyse one phase; writes analysis.json (and the blind packet when asked) and returns the analysis. */
export function analysePhase({ phase, outDir = null, runId = "v0", dryRun = false, blind = null, promptsPath = PROMPTS_PATH, env = process.env } = {}) {
  if (!PHASES[phase]) throw new Error(`--phase must be one of ${Object.keys(PHASES).join(", ")}`);
  const phaseDir = join(outDir || defaultOutDir(env), dryRun ? `${runId}-dry-run` : runId, phase);
  if (insideRepo(phaseDir)) throw new Error(`raw outputs never live inside the repository: ${phaseDir}`);
  const planPath = join(phaseDir, "plan.json");
  if (!existsSync(planPath)) throw new Error(`no run in ${phaseDir}`);
  const plan = JSON.parse(readFileSync(planPath, "utf-8"));
  const { prompts, sha256 } = loadPrompts(promptsPath);
  if (sha256 !== plan.registration?.promptsSha256) throw new Error("the prompt set differs from the one this run used");
  const calls = readJsonl(join(phaseDir, "calls.jsonl"));
  const stats = analysePairs(calls, prompts, { seed: plan.seed, capUsd: plan.capUsd, plannedPairs: plan.pairs });
  const analysis = {
    schema: "youspeak-onoff-analysis/v0",
    benchmark: BENCHMARK_ID,
    phase,
    mode: plan.mode,
    model: plan.model,
    effort: plan.effort,
    claudeCodeVersions: [...new Set(calls.map(call => call.claudeCodeVersion).filter(Boolean))],
    registration: plan.registration,
    seed: plan.seed,
    pairsPlanned: plan.pairs,
    ...stats,
    interim: { ...stats.interim, suggestion: stats.interim.harm || stats.interim.futility ? "stop" : "continue",
      note: "Harm or futility only (PREREGISTRATION.md §7); never a reason to stop early for success. A person decides." },
    nextPairs: stats.nextPairs && { ...stats.nextPairs, evidence: "estimated",
      note: "The Opus run's --pairs, from this phase's SD of the paired log ratio (PREREGISTRATION.md §6.3)." },
    evidenceKey: EVIDENCE_KEY,
    note: "Aggregates only; suggest-only. It changes nothing: the rule stays as it is unless a person changes it.",
  };
  writePrivate(join(phaseDir, "analysis.json"), `${JSON.stringify(analysis, null, 2)}\n`);
  if (blind) {
    const dir = join(phaseDir, "blind");
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    chmodSync(dir, 0o700);
    const { packet, key } = blindPacket(calls, prompts, { count: blind, seed: plan.seed });
    writePrivate(join(dir, "packet.md"), packet);
    writePrivate(join(dir, "key.json"), `${JSON.stringify(key, null, 2)}\n`);
  }
  return { analysis, phaseDir };
}

const USAGE = `node benchmarks/youspeak-onoff-v0/analyze.mjs --phase pilot|opus [--out DIR] [--run-id ID] [--dry-run] [--blind N]`;

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  try {
    const options = {};
    const argv = process.argv.slice(2);
    for (let index = 0; index < argv.length; index += 1) {
      const arg = argv[index];
      if (arg === "--phase") options.phase = argv[++index];
      else if (arg === "--out") options.outDir = argv[++index];
      else if (arg === "--run-id") options.runId = argv[++index];
      else if (arg === "--dry-run") options.dryRun = true;
      else if (arg === "--blind") options.blind = Number(argv[++index]);
      else if (arg === "--help" || arg === "-h") options.help = true;
      else throw new Error(`unknown option: ${arg}`);
    }
    if (options.help || !options.phase) {
      console.log(USAGE);
      process.exit(options.help ? 0 : 2);
    }
    const { analysis } = analysePhase(options);
    console.log(JSON.stringify(analysis, null, 2));
  } catch (error) {
    console.error(`onoff analyze: ${error.message}`);
    process.exit(1);
  }
}
