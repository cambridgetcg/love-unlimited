import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";
import vm from "node:vm";

const REPO_ROOT = new URL("../", import.meta.url);

function read(relativePath) {
  return readFileSync(new URL(relativePath, REPO_ROOT), "utf-8");
}

test("legacy Ollama filesystem IPC is absent and its compatibility client fails closed", () => {
  const server = read("youi-web/server.mjs");
  const bridge = read("youi-web/ollama-bridge.mjs");
  const client = read("tools/ollama-ipc.py");
  const notes = read("youi-web/OLLAMA-PATCH.md");

  assert.doesNotMatch(server, /\bstartFileIPC\b/);
  assert.doesNotMatch(bridge, /\bstartFileIPC\b|\bprocessIpcRequests\b|ollama-(?:req|res)-/);
  assert.doesNotMatch(client, /ollama-(?:req|res)-|\bIPC_DIR\b|\bipc_call\b/);
  assert.doesNotMatch(notes, /\bstartFileIPC\s*\(\s*\)/);

  const result = spawnSync("python3", ["tools/ollama-ipc.py", "test"], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  assert.equal(result.status, 2);
  assert.match(`${result.stdout}${result.stderr}`, /disabled.*unauthenticated bridge was removed/is);
});

test("orchestrator and being views do not interpolate remote fields into executable markup", () => {
  const page = read("youi-web/public/index.html");

  assert.match(page, /function safeClassToken\(/);
  assert.match(page, /modeBadge\.textContent\s*=\s*String\(mode\)/);
  assert.match(page, /pill\.textContent\s*=\s*String\(models\[i\]\)/);
  assert.match(page, /installedLabels\.map\(escapeHtml\)/);
  assert.match(page, /escapeHtml\(String\(d\.error\)\.slice\(0,\s*80\)\)/);
  assert.match(page, /const statusCls = safeClassToken\(engine\.status\)/);
  assert.match(page, /engine-status \$\{safeClassToken\(task\.status\)\}/);
  assert.match(page, /escapeHtml\(`tool-body-\$\{String\(id\)\}`\)/);
  assert.match(page, /candidate\.dataset\.toolId\s*===\s*String\(data\.id\)/);
  assert.doesNotMatch(page, /getElementById\('dp-mode'\)\.innerHTML/);
  assert.doesNotMatch(page, /header\.innerHTML\s*=\s*buildOrchHeader/);
  assert.doesNotMatch(page, /item\.innerHTML\s*=\s*`<div class="stage-dot"/);
  assert.doesNotMatch(page, /querySelector\('\.deploy-badge\[data-instance="'\s*\+/);
  assert.doesNotMatch(page, /querySelector\(`\[data-tool-id="/);
  assert.match(page, /beingLastState\?\.repo\s*\|\|\s*'~\/love-unlimited'/);
  assert.doesNotMatch(page, /~\/Desktop\/love-unlimited/);
});

test("deployment page stops on denial or error and never invents success totals", () => {
  const page = read("youi-web/public/deploy.html");

  assert.match(page, /if\s*\(!resp\.ok\)/);
  assert.match(page, /data\.ok\s*!==\s*true\s*&&\s*data\.skipped\s*!==\s*true/);
  assert.match(page, /phase-failed/);
  assert.match(page, /workflowError\.textContent/);
  assert.match(page, /break;/);
  assert.doesNotMatch(page, /filesDeployed\s*=\s*47/);
  assert.doesNotMatch(page, /servicesLive\s*\|\|\s*9|reposPushed\s*\|\|\s*12/);
  assert.doesNotMatch(page, /If server not running,\s*simulate|ready \(run \.\/DEPLOY-GOSPEL\.sh\)/i);
  assert.doesNotMatch(page, /The Gospel is Live|services live|files deployed/i);
});

test("changed YOUI pages contain syntactically valid inline JavaScript", () => {
  for (const relativePath of ["youi-web/public/index.html", "youi-web/public/deploy.html"]) {
    const page = read(relativePath);
    const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
      .map(match => match[1])
      .filter(source => source.trim());
    assert.ok(scripts.length > 0, `${relativePath} should contain an inline script`);
    for (const source of scripts) {
      assert.doesNotThrow(() => new vm.Script(source, { filename: relativePath }));
    }
  }
});
