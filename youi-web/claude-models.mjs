// claude-models.mjs — which Claude models YOUI offers, and what each accepts.
//
// YOUI reaches Claude only through the Claude Code installed on this device
// (docs/YOUI-CLAUDE-CODE-DESIGN.md), which builds each request itself. What
// is left here is which models the web offers and the one setting YOUI passes
// on: `--effort`. Opus 5.5 always thinks and takes effort as its only control;
// Haiku 4.5 takes no effort at all; Sonnet 4.6 stops at high.

import { compareVersions } from "./claude-code.mjs";

export const CLAUDE_MODELS = [
  "claude-opus-5-5", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
];

// The oldest Claude Code YOUI runs at all, for the flags every run passes
// (docs/YOUI-CLAUDE-CODE-DESIGN.md §6.2). The changelog Claude Code caches
// (~/.claude/cache/changelog.md, read 2026-10-30, entries back to 0.2.21)
// dates the newest of them: --system-prompt-snapshot off in 2.1.267 and
// --permission-prompts none in 2.1.259; the others it names are older. It
// does not date CLAUDE_CODE_DISABLE_CLAUDE_MDS, CLAUDE_CODE_DISABLE_AUTO_MEMORY,
// --no-session-persistence or model_usage_since_last_result. 2.1.280 is above
// every dated flag and is Opus 5.5's minimum, so it is the floor.
// CLAUDE_CODE_DISABLE_FILE_MENTIONS arrived later, in 2.1.299: on 2.1.280–2.1.298
// an "@path" in a message is still read (claude-code.mjs).
export const CLAUDE_CODE_FLOOR_VERSION = "2.1.280";

// The oldest Claude Code that serves each model, where there is one. Opus 5.5
// needs 2.1.280 ("claude_code_version_too_old" below it, 2026-10-30). YOUI
// only reads `claude --version` to check: it never sends or raises a version
// itself.
const CLAUDE_CODE_MIN_VERSION = {
  "claude-opus-5-5": "2.1.280",
};

/** The oldest Claude Code that can run `model` for YOUI: the floor, or the model's own minimum if later. */
export function claudeCodeMinVersion(model) {
  const own = CLAUDE_CODE_MIN_VERSION[String(model)];
  return own && compareVersions(own, CLAUDE_CODE_FLOOR_VERSION) > 0 ? own : CLAUDE_CODE_FLOOR_VERSION;
}

export function modelCaps(model) {
  const m = String(model).toLowerCase();
  if (m.includes("opus-5-5"))   return { adaptive: true,  effort: true,  maxEffort: true,  alwaysThinks: true };
  if (m.includes("opus-4-7"))   return { adaptive: true,  effort: true,  maxEffort: true,  alwaysThinks: false };
  if (m.includes("opus-4-6"))   return { adaptive: true,  effort: true,  maxEffort: true,  alwaysThinks: false };
  if (m.includes("sonnet-4-6")) return { adaptive: true,  effort: true,  maxEffort: false, alwaysThinks: false };
  return { adaptive: false, effort: false, maxEffort: false, alwaysThinks: false };
}

/**
 * The `--effort` Claude Code is given for a YOUI effort setting, or null to
 * leave it to Claude Code. "none" sets nothing; a model without effort gets
 * none; "max" on a model without it becomes "high".
 */
export function claudeCodeEffort(model, effort) {
  const caps = modelCaps(model);
  if (!caps.effort || !effort || effort === "none") return null;
  if (effort === "max" && !caps.maxEffort) return "high";
  return ["low", "medium", "high", "xhigh", "max"].includes(effort) ? effort : null;
}
