// claude-models.mjs — which Claude models YOUI offers, and how each one is asked.
//
// The request shape differs by generation. Opus 4.6 and 4.7 take adaptive or
// budgeted thinking and the betas they shipped with (interleaved thinking, 1M
// context, effort). Opus 5.5 always thinks: `{type: "disabled"}` and
// `budget_tokens` are both rejected, effort is the only control (its default
// is `medium`, so YOUI always sends one), and 1M context and effort need no
// beta. Sending it the 4.x shape fails every request.

export const CLAUDE_MODELS = [
  "claude-opus-5-5", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
];

export function modelCaps(model) {
  const m = String(model).toLowerCase();
  if (m.includes("opus-5-5"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true,  alwaysThinks: true,  legacyBetas: false };
  if (m.includes("opus-4-7"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true,  alwaysThinks: false, legacyBetas: true };
  if (m.includes("opus-4-6"))   return { adaptive: true,  effort: true,  maxEffort: true,  context1m: true,  alwaysThinks: false, legacyBetas: true };
  if (m.includes("sonnet-4-6")) return { adaptive: true,  effort: true,  maxEffort: false, context1m: true,  alwaysThinks: false, legacyBetas: true };
  if (m.includes("sonnet-4"))   return { adaptive: false, effort: false, maxEffort: false, context1m: true,  alwaysThinks: false, legacyBetas: true };
  if (m.includes("haiku"))      return { adaptive: false, effort: false, maxEffort: false, context1m: false, alwaysThinks: false, legacyBetas: true };
  return { adaptive: false, effort: false, maxEffort: false, context1m: false, alwaysThinks: false, legacyBetas: true };
}

/**
 * The model-dependent parts of a Messages request: beta headers, `thinking`
 * and `output_config`. `thinking` is "adaptive", "enabled" or "disabled";
 * `effort` is "none" or an effort level.
 */
export function claudeRequestShape({ model, thinking, effort, maxTokens }) {
  const caps = modelCaps(model);
  const betas = ["oauth-2025-04-20", "claude-code-20250219"];
  const shape = { betas };
  if (caps.legacyBetas) {
    if (caps.adaptive || thinking === "enabled") betas.push("interleaved-thinking-2025-05-14");
    if (caps.context1m) betas.push("context-1m-2025-08-07");
    if (caps.effort) betas.push("effort-2025-11-24");
  }
  if (caps.alwaysThinks) {
    // It always thinks. "disabled" leaves the field out, which still thinks;
    // a lower effort is how to make it think less.
    if (thinking !== "disabled") shape.thinking = { type: "adaptive" };
  } else if (thinking === "adaptive" && caps.adaptive) {
    shape.thinking = { type: "adaptive" };
  } else if (thinking !== "disabled") {
    shape.thinking = { type: "enabled", budget_tokens: Math.min(maxTokens - 1, 16384) };
    if (!betas.includes("interleaved-thinking-2025-05-14")) betas.push("interleaved-thinking-2025-05-14");
  }
  if (caps.effort && effort !== "none") {
    shape.output_config = { effort: effort === "max" && !caps.maxEffort ? "high" : effort };
  } else if (caps.alwaysThinks) {
    // Its own default is `medium`; say so rather than rely on it.
    shape.output_config = { effort: "medium" };
  }
  return shape;
}
