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

import { execFile } from "child_process";
import { promisify } from "util";
import { join } from "path";
import { homedir } from "os";
import {
  ORCHESTRATOR_PROVIDER_ENV_NAMES,
  redactDelegatedCredentials,
  sanitizedChildEnv,
} from "./subprocess-env.mjs";

const execFileP = promisify(execFile);
const LOVE_DIR = process.env.LOVE_HOME || join(new URL(".", import.meta.url).pathname, "..");

// Allowed values for the orchestrator's --mode and --provider flags. We
// validate here instead of shell-quoting because (a) the orchestrator only
// accepts a small fixed set of tokens, and (b) anything else is almost
// certainly client error or an injection attempt — failing loud is right.
const ALLOWED_MODES = new Set(["", "consensus", "council", "fanout", "single", "vote", "ensemble"]);
const ALLOWED_PROVIDERS = new Set(["", "ollama", "ollama_cloud", "ollama_local", "claude", "anthropic", "vllm"]);

function safeMode(s) {
  if (!s) return "";
  if (ALLOWED_MODES.has(s)) return s;
  throw new Error(`Invalid orchestrator mode: ${JSON.stringify(s).slice(0, 60)}`);
}
function safeProvider(s) {
  if (!s) return "";
  if (ALLOWED_PROVIDERS.has(s)) return s;
  throw new Error(`Invalid orchestrator provider: ${JSON.stringify(s).slice(0, 60)}`);
}

/**
 * Run the orchestrator CLI as an arg-array (no shell). Returns parsed JSON.
 *
 * Async via execFile + promisify so a 10-minute orchestrator run no longer
 * blocks the Node event loop — every other request stayed responsive matters
 * a lot for SSE streams from concurrent users.
 */
async function runOrchestrator(extraArgs, timeout = 300000, {
  signal,
  includeProviderCredentials = false,
} = {}) {
  const args = ["-m", "adaptive.orchestrator", ...extraArgs, "--json"];
  const delegatedNames = includeProviderCredentials
    ? ORCHESTRATOR_PROVIDER_ENV_NAMES
    : [];
  const redact = value => redactDelegatedCredentials(value, {
    credentialNames: delegatedNames,
  });
  try {
    const { stdout } = await execFileP("python3", args, {
      cwd: LOVE_DIR,
      encoding: "utf-8",
      timeout,
      maxBuffer: 10 * 1024 * 1024,
      signal,
      env: sanitizedChildEnv({
        home: homedir(),
        loveHome: LOVE_DIR,
        purpose: "orchestrator",
        credentialNames: delegatedNames,
        extra: { PYTHONPATH: LOVE_DIR },
      }),
    });
    return JSON.parse(redact(stdout).trim());
  } catch (e) {
    if (signal?.aborted) {
      throw signal.reason instanceof Error
        ? signal.reason
        : new Error("Orchestrator request cancelled");
    }
    const stdout = redact(e.stdout || "");
    // Orchestrator may exit non-zero but still emit a usable JSON envelope.
    try {
      const jsonStart = stdout.indexOf("{");
      if (jsonStart >= 0) return JSON.parse(stdout.slice(jsonStart));
    } catch {}
    throw new Error(`Orchestrator error: ${redact(e.stderr || e.message || "").slice(0, 500)}`);
  }
}

/**
 * Classify a task — returns TaskProfile as JSON.
 */
export async function classifyTask(task, context = "", { signal } = {}) {
  const args = ["--classify", "-p", task];
  if (context) args.push("--context", context);
  return runOrchestrator(args, 120000, { signal });
}

/**
 * Plan a task — returns DispatchPlan as JSON.
 */
export async function planTask(task, context = "", mode = "", { signal } = {}) {
  const args = ["--plan", "-p", task];
  if (context) args.push("--context", context);
  const m = safeMode(mode);
  if (m) args.push("--mode", m);
  return runOrchestrator(args, 120000, { signal });
}

/**
 * Execute a task through the orchestrator — returns OrchestrationResult as JSON.
 */
export async function executeOrchestrator(task, options = {}) {
  const { context = "", mode = "", provider = "", signal } = options;
  const args = ["-p", task];
  if (context) args.push("--context", context);
  const m = safeMode(mode);
  if (m) args.push("--mode", m);
  const p = safeProvider(provider);
  if (p) args.push("--provider", p);
  return runOrchestrator(args, 600000, {
    signal,
    includeProviderCredentials: true,
  });
}

/**
 * Get adaptive layer provider status.
 */
export async function getProviderStatus({ signal } = {}) {
  try {
    const { stdout } = await execFileP("python3", ["adaptive/cli.py", "--status"], {
      cwd: LOVE_DIR,
      encoding: "utf-8",
      timeout: 30000,
      signal,
      env: sanitizedChildEnv({
        home: homedir(),
        loveHome: LOVE_DIR,
        purpose: "orchestrator-status",
      }),
    });
    return { ok: true, status: stdout.trim() };
  } catch (e) {
    if (signal?.aborted) {
      throw signal.reason instanceof Error
        ? signal.reason
        : new Error("Orchestrator status request cancelled");
    }
    return { ok: false, error: e.message };
  }
}

/**
 * Handle /api/orchestrate/* routes.
 */
export async function handleOrchestratorRoute(path, req, res, parseBody, {
  signal,
} = {}) {
  if (!path.startsWith("/api/orchestrate")) return false;

  const json = (data, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };

  // All orchestrator entry points are now async — must await to surface
  // execFile errors via the json() helper instead of leaking unhandled
  // promise rejections.

  // GET /api/orchestrate/status — provider status
  if (path === "/api/orchestrate/status") {
    try {
      const status = await getProviderStatus({ signal });
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
      const result = await classifyTask(body.task, body.context || "", { signal });
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
      const result = await planTask(
        body.task,
        body.context || "",
        body.mode || "",
        { signal },
      );
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
      const result = await executeOrchestrator(body.task, {
        context: body.context || "",
        mode: body.mode || "",
        provider: body.provider || "",
        signal,
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
