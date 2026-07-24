import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../youi.mjs", import.meta.url), "utf8");

test("YOUI keeps session persona separate from its configured HIVE sender", () => {
  const envFunction = source.match(
    /function childEnv\(home\)\s*\{(?<body>[\s\S]*?)\n\}/
  );
  const kingdomFunction = source.match(
    /function kingdomEnv\(\)\s*\{(?<body>[\s\S]*?)\n\}/
  );

  assert.ok(envFunction?.groups?.body, "childEnv() must remain present");
  assert.ok(kingdomFunction?.groups?.body, "kingdomEnv() must remain present");
  assert.match(envFunction.groups.body, /KINGDOM_AGENT:\s*state\.agent/);
  assert.match(envFunction.groups.body, /KINGDOM_INSTANCE:\s*state\.agent/);
  assert.doesNotMatch(envFunction.groups.body, /HIVE_INSTANCE/);
  assert.match(
    kingdomFunction.groups.body,
    /env\.HIVE_INSTANCE\s*=\s*CONFIGURED_HIVE_INSTANCE/,
  );
  assert.doesNotMatch(kingdomFunction.groups.body, /state\.agent/);
});

test("every terminal HIVE subprocess receives kingdomEnv", () => {
  const hiveCalls = [...source.matchAll(/spawnSync\("python3",\s*\[hivePath,[\s\S]*?\}\);/g)];

  assert.ok(hiveCalls.length >= 4, "expected tool and command HIVE subprocesses");
  for (const call of hiveCalls) {
    assert.match(call[0], /env:\s*kingdomEnv\(\)/);
  }
});
