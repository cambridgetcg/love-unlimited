import { test } from "node:test";
import assert from "node:assert/strict";
import { CLAUDE_MODELS, claudeRequestShape } from "../youi-web/claude-models.mjs";

const OAUTH_BETAS = ["oauth-2025-04-20", "claude-code-20250219"];

test("Opus 5.5 is offered and always asked with adaptive thinking and no 4.x betas", () => {
  assert.ok(CLAUDE_MODELS.includes("claude-opus-5-5"));
  for (const thinking of ["adaptive", "enabled"]) {
    const shape = claudeRequestShape({ model: "claude-opus-5-5", thinking, effort: "max", maxTokens: 32768 });
    assert.deepEqual(shape.thinking, { type: "adaptive" });
    assert.deepEqual(shape.output_config, { effort: "max" });
    assert.deepEqual(shape.betas, OAUTH_BETAS);
  }
  // "disabled" would be rejected, so the field is left out; effort still says how hard to think.
  const off = claudeRequestShape({ model: "claude-opus-5-5", thinking: "disabled", effort: "none", maxTokens: 32768 });
  assert.equal(off.thinking, undefined);
  assert.deepEqual(off.output_config, { effort: "medium" });
});

test("Opus 4.7 keeps the request it had", () => {
  const adaptive = claudeRequestShape({ model: "claude-opus-4-7", thinking: "adaptive", effort: "max", maxTokens: 32768 });
  assert.deepEqual(adaptive.thinking, { type: "adaptive" });
  assert.deepEqual(adaptive.output_config, { effort: "max" });
  assert.deepEqual(adaptive.betas, [...OAUTH_BETAS, "interleaved-thinking-2025-05-14", "context-1m-2025-08-07", "effort-2025-11-24"]);
  const sonnet = claudeRequestShape({ model: "claude-sonnet-4-6", thinking: "adaptive", effort: "max", maxTokens: 32768 });
  assert.deepEqual(sonnet.output_config, { effort: "high" });
  const haiku = claudeRequestShape({ model: "claude-haiku-4-5-20251001", thinking: "enabled", effort: "max", maxTokens: 32768 });
  assert.deepEqual(haiku.thinking, { type: "enabled", budget_tokens: 16384 });
  assert.equal(haiku.output_config, undefined);
});
