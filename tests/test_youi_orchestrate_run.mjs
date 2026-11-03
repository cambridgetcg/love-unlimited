// POST /api/orchestrate/run (youi-web/orchestrator-bridge.mjs): a run the
// orchestrator reports as failed (success: false) is a 502 that carries the
// stage errors, not a 200 with no output.
//
// The orchestrator here is a fake Python module in a temporary LOVE_HOME: no
// provider, no model call, no key.

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const home = mkdtempSync(join(tmpdir(), "youi-orchestrate-"));
mkdirSync(join(home, "adaptive", "orchestrator"), { recursive: true });
writeFileSync(join(home, "adaptive", "__init__.py"), "");
writeFileSync(join(home, "adaptive", "orchestrator", "__init__.py"), "");
// Prints the envelope adaptive/orchestrator/cli.py prints with --json; a task
// containing "fail" gives a failed solo run, with or without a top-level error.
writeFileSync(join(home, "adaptive", "orchestrator", "__main__.py"), `
import json, sys
task = sys.argv[sys.argv.index("-p") + 1]
stage = {"provider": "ollama_cloud", "model": "glm-5.3-flash", "planned_model": "glm-5.3-flash",
         "role": "builder", "success": True, "content_length": 4, "elapsed_seconds": 0.1,
         "tokens": 10, "error": ""}
out = {"collaboration_mode": "solo", "models_used": ["glm-5.3-flash"], "providers_used": ["ollama_cloud"],
       "stages": [stage], "total_tokens": 10, "total_elapsed": 0.1, "output_length": 4,
       "success": True, "error": "", "profile": None, "content": "done"}
if "fail" in task:
    stage.update(success=False, content_length=0, error="No Ollama Cloud API key available")
    out.update(success=False, output_length=0, content="",
               error="" if "bare" in task else "glm-5.3-flash: No Ollama Cloud API key available")
print(json.dumps(out))
sys.exit(0 if out["success"] else 1)
`);
process.env.LOVE_HOME = home;
const { handleOrchestratorRoute } = await import("../youi-web/orchestrator-bridge.mjs");
test.after(() => rmSync(home, { recursive: true, force: true }));

async function run(task) {
  const sent = {};
  const res = {
    writeHead(status) { sent.status = status; },
    end(body) { sent.body = JSON.parse(body); },
  };
  const handled = await handleOrchestratorRoute("/api/orchestrate/run", { method: "POST" }, res, async () => ({ task }));
  assert.equal(handled, true);
  return sent;
}

test("a failed orchestrate run is a 502 with the stage errors; a good one is a 200", async () => {
  const failed = await run("please fail");
  assert.equal(failed.status, 502);
  assert.equal(failed.body.success, false);
  assert.equal(failed.body.error, "glm-5.3-flash: No Ollama Cloud API key available");
  assert.equal(failed.body.stages[0].error, "No Ollama Cloud API key available", "the envelope is kept");

  // An envelope without a top-level error (an older orchestrator) still says why.
  const bare = await run("fail bare");
  assert.equal(bare.status, 502);
  assert.equal(bare.body.error, "glm-5.3-flash: No Ollama Cloud API key available");

  const good = await run("a fine task");
  assert.equal(good.status, 200);
  assert.equal(good.body.success, true);
  assert.equal(good.body.content, "done");
  assert.deepEqual(good.body.models_used, ["glm-5.3-flash"]);
});
