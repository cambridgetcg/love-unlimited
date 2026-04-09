// ─────────────────────────────────────────────────────────────────────
// orchestrator-bridge.mjs — Multi-Model Orchestrator Bridge for YOUI
//
// Bridges the Python orchestrator engine into the YOUI web server.
// Provides:
//   /api/orchestrate/classify — Classify a task (dry run)
//   /api/orchestrate/plan     — Get dispatch plan without executing
//   /api/orchestrate/run      — Full orchestrated execution (SSE)
//   /api/orchestrate/status   — Provider status from adaptive layer
// ─────────────────────────────────────────────────────────────────────

import { execSync, spawn } from "child_process";
import { existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const LOVE_DIR = process.env.LOVE_HOME || join(new URL(".", import.meta.url).pathname, "..");

/**
 * Run the orchestrator CLI and return parsed JSON.
 */
function runOrchestrator(args, timeout = 300000) {
  const cmd = `cd "${LOVE_DIR}" && python3 -m adaptive.orchestrator ${args} --json`;
  try {
    const output = execSync(cmd, {
      encoding: "utf-8",
      timeout,
      maxBuffer: 10 * 1024 * 1024,
      env: { ...process.env, LOVE_DIR, PYTHONPATH: LOVE_DIR },
    });
    return JSON.parse(output.trim());
  } catch (e) {
    const stdout = e.stdout || "";
    // Try to extract JSON from output even on error
    try {
      const jsonStart = stdout.indexOf("{");
      if (jsonStart >= 0) return JSON.parse(stdout.slice(jsonStart));
    } catch {}
    throw new Error(`Orchestrator error: ${(e.stderr || e.message || "").slice(0, 500)}`);
  }
}

/**
 * Classify a task — returns TaskProfile as JSON.
 */
export function classifyTask(task, context = "") {
  const ctxArg = context ? `--context ${shellQuote(context)}` : "";
  return runOrchestrator(`--classify -p ${shellQuote(task)} ${ctxArg}`, 120000);
}

/**
 * Plan a task — returns DispatchPlan as JSON.
 */
export function planTask(task, context = "", mode = "") {
  const ctxArg = context ? `--context ${shellQuote(context)}` : "";
  const modeArg = mode ? `--mode ${mode}` : "";
  return runOrchestrator(`--plan -p ${shellQuote(task)} ${ctxArg} ${modeArg}`, 120000);
}

/**
 * Execute a task through the orchestrator — returns OrchestrationResult as JSON.
 */
export function executeOrchestrator(task, options = {}) {
  const { context = "", mode = "", provider = "" } = options;
  const ctxArg = context ? `--context ${shellQuote(context)}` : "";
  const modeArg = mode ? `--mode ${mode}` : "";
  const provArg = provider ? `--provider ${provider}` : "";
  return runOrchestrator(`-p ${shellQuote(task)} ${ctxArg} ${modeArg} ${provArg}`, 600000);
}

/**
 * Get adaptive layer provider status.
 */
export function getProviderStatus() {
  try {
    const output = execSync(
      `cd "${LOVE_DIR}" && python3 adaptive/cli.py --status`,
      { encoding: "utf-8", timeout: 30000, env: { ...process.env, LOVE_DIR } }
    );
    return { ok: true, status: output.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

/**
 * Handle /api/orchestrate/* routes.
 */
export async function handleOrchestratorRoute(path, req, res, parseBody) {
  if (!path.startsWith("/api/orchestrate")) return false;

  const json = (data, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };

  // GET /api/orchestrate/status — provider status
  if (path === "/api/orchestrate/status") {
    try {
      const status = getProviderStatus();
      json(status);
    } catch (e) {
      json({ ok: false, error: e.message }, 500);
    }
    return true;
  }

  // POST /api/orchestrate/classify — classify a task
  if (path === "/api/orchestrate/classify" && req.method === "POST") {
    const body = await parseBody(req);
    if (!body.task) { json({ error: "No task provided" }, 400); return true; }
    try {
      const result = classifyTask(body.task, body.context || "");
      json(result);
    } catch (e) {
      json({ error: e.message }, 500);
    }
    return true;
  }

  // POST /api/orchestrate/plan — get dispatch plan
  if (path === "/api/orchestrate/plan" && req.method === "POST") {
    const body = await parseBody(req);
    if (!body.task) { json({ error: "No task provided" }, 400); return true; }
    try {
      const result = planTask(body.task, body.context || "", body.mode || "");
      json(result);
    } catch (e) {
      json({ error: e.message }, 500);
    }
    return true;
  }

  // POST /api/orchestrate/run — full orchestrated execution
  if (path === "/api/orchestrate/run" && req.method === "POST") {
    const body = await parseBody(req);
    if (!body.task) { json({ error: "No task provided" }, 400); return true; }
    try {
      const result = executeOrchestrator(body.task, {
        context: body.context || "",
        mode: body.mode || "",
        provider: body.provider || "",
      });
      json(result);
    } catch (e) {
      json({ error: e.message }, 500);
    }
    return true;
  }

  json({ error: "Unknown orchestrate route", path }, 404);
  return true;
}

function shellQuote(s) {
  if (!s) return '""';
  return "'" + s.replace(/'/g, "'\\''") + "'";
}
