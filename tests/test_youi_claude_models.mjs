import { test } from "node:test";
import assert from "node:assert/strict";
import { CLAUDE_MODELS, claudeCodeEffort } from "../youi-web/claude-models.mjs";

test("Opus 5.5 is offered, and each model gets the --effort it accepts", () => {
  assert.ok(CLAUDE_MODELS.includes("claude-opus-5-5"));
  assert.equal(claudeCodeEffort("claude-opus-5-5", "max"), "max");
  assert.equal(claudeCodeEffort("claude-opus-5-5", "low"), "low");
  // "none" sets nothing: Claude Code decides.
  assert.equal(claudeCodeEffort("claude-opus-5-5", "none"), null);
  assert.equal(claudeCodeEffort("claude-opus-4-7", "high"), "high");
  // Sonnet 4.6 stops at high; Haiku 4.5 takes no effort at all.
  assert.equal(claudeCodeEffort("claude-sonnet-4-6", "max"), "high");
  assert.equal(claudeCodeEffort("claude-haiku-4-5-20251001", "high"), null);
  assert.equal(claudeCodeEffort("claude-opus-5-5", "bogus"), null);
});
