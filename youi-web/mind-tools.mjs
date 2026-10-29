// mind-tools.mjs — the tools the agent calls to bring other minds into a task.
//
// docs/YOUI-MINDS-DESIGN.md §3.3 (slice M2): review, ensemble, decompose and
// pipeline. The agent being spoken to decides when to call them; nothing here
// bypasses it. The module is pure: the server injects
//
//   callMind({ mind, system, prompt, signal })
//     -> { ok, text, usage: { input_tokens, output_tokens }, error }
//
// with a per-message budget, and passes the minds the registry offers this
// session (minds.mjs). The boundaries, as §3.3 and §4 set them:
//
// - a mind receives only the text in the tool input, under a short neutral
//   system prompt naming its role; never the conversation or any tools;
// - at most 3 minds per tool call, and a per-message ceiling on mind calls;
//   a call that would pass the ceiling is refused before anything is sent;
// - a named mind must be offered to this session, or the tool refuses and
//   lists the offered minds; an unnamed one is picked deterministically;
// - every result names each mind's provider, model and privacy label, whether
//   it answered or failed and why, and its tokens; a failed mind is reported,
//   never replaced;
// - an answer informs the agent; it authorises nothing.

export const MIND_TOOL_NAMES = Object.freeze(["review", "ensemble", "decompose", "pipeline"]);
export const MAX_MINDS_PER_CALL = 3;
export const MAX_PIPELINE_STAGES = 4;
// A first review and one of the revised draft (§3.3: at most 1 revision pass).
export const MAX_REVIEWS_PER_MESSAGE = 2;
export const DEFAULT_MIND_CALLS_PER_MESSAGE = 6;
export const MIND_CALLS_PER_MESSAGE_RANGE = Object.freeze({ min: 1, max: 20 });
export const MIND_CALL_TIMEOUT_MS = 120_000;
// One answer's share of a tool result (the server caps a result at 50,000
// characters), and how much of a stage's output the next stage receives.
const MAX_ANSWER_CHARS = 10_000;
const MAX_HANDOFF_CHARS = 20_000;

export const MIND_SYSTEM_PROMPTS = Object.freeze({
  reviewer: "You are a reviewer. You are given a draft and the criteria to judge it by, and nothing else. "
    + "Begin your answer with APPROVED or NEEDS_REVISION on the first line, then give brief, specific notes against the criteria.",
  answerer: "You are an independent answerer. Answer the question you are given on your own, as well as you can, "
    + "and say what you are unsure of.",
  worker: "You are a worker on one part of a larger task. Complete the sub-task you are given and return only the result.",
  stage: name => `You are the "${name}" stage of a pipeline. Follow the instruction you are given; when the previous `
    + "stage's output is included, build on it. Return only this stage's output.",
});

/** YOUI_MIND_CALLS_PER_MESSAGE, bounded 1–20; anything unreadable is the default, 6. */
export function mindCallsPerMessage(raw) {
  const value = Number.parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(value)) return DEFAULT_MIND_CALLS_PER_MESSAGE;
  return Math.min(MIND_CALLS_PER_MESSAGE_RANGE.max, Math.max(MIND_CALLS_PER_MESSAGE_RANGE.min, value));
}

/** A fresh budget for one user message; the server makes one at the start of each turn. */
export function createMindBudget({ limit = DEFAULT_MIND_CALLS_PER_MESSAGE } = {}) {
  return { limit: mindCallsPerMessage(limit), used: 0, reviews: 0 };
}

// ── Choosing minds ──────────────────────────────────────────────────

const mindId = mind => mind.id || `${mind.provider}:${mind.model}`;
const isSelf = (mind, self) => Boolean(self) && mind.provider === self.provider && mind.model === self.model;

/**
 * The offered minds in the order a tool picks unnamed ones: one provider at a
 * time in registry order, so picks spread across providers; the agent's own
 * provider last in each round, and the agent's own model after everything.
 */
export function pickOrder(offered, self = null) {
  const groups = new Map();
  for (const mind of offered) {
    if (isSelf(mind, self)) continue;
    if (!groups.has(mind.provider)) groups.set(mind.provider, []);
    groups.get(mind.provider).push(mind);
  }
  const providers = [...groups.keys()];
  const queues = [
    ...providers.filter(provider => provider !== self?.provider),
    ...providers.filter(provider => provider === self?.provider),
  ].map(provider => [...groups.get(provider)]);
  const order = [];
  while (queues.some(queue => queue.length)) {
    for (const queue of queues) if (queue.length) order.push(queue.shift());
  }
  return [...order, ...offered.filter(mind => isSelf(mind, self))];
}

// An id (provider:model), or a model name that only one offered mind has.
function resolveNamed(name, offered) {
  const exact = offered.find(mind => mindId(mind) === name);
  if (exact) return exact;
  const byModel = offered.filter(mind => mind.model === name);
  return byModel.length === 1 ? byModel[0] : null;
}

function offeredText(offered) {
  if (!offered.length) return "none (no mind is granted and reachable for this session right now)";
  return offered.map(mind => `${mindId(mind)} (${mind.privacy}, ${mind.state})`).join("; ");
}

// Gives each slot a mind: the named one if it is offered, else the next in
// pick order. `distinct` (ensemble) needs a different mind in every slot;
// otherwise unnamed slots reuse minds once `cap` are in use or none is left.
function assignMinds(tool, slots, offered, self, { distinct = false, cap = MAX_MINDS_PER_CALL } = {}) {
  const missing = slots.filter(name => name !== null && !resolveNamed(name, offered));
  if (missing.length) {
    return {
      error: `${tool}: ${missing.map(name => JSON.stringify(name)).join(", ")} ${missing.length === 1 ? "is" : "are"} `
        + `not offered to this session, so nothing was sent and no other mind was put in its place. `
        + `Offered minds: ${offeredText(offered)}. Name one of these by its id (provider:model), or leave the mind out and the tool picks one.`,
    };
  }
  if (!offered.length) {
    return { error: `${tool}: no minds are offered to this session right now, so nothing was sent. Answer yourself, and say so.` };
  }
  const assigned = slots.map(name => (name === null ? null : resolveNamed(name, offered)));
  const used = new Map();
  for (const mind of assigned) if (mind) used.set(mindId(mind), mind);
  if (distinct && used.size < assigned.filter(Boolean).length) {
    return { error: `${tool}: the same mind is named twice; each answer must come from a different mind. Nothing was sent.` };
  }
  if (used.size > cap) {
    return { error: `${tool}: ${used.size} different minds are named; at most ${cap} may take part in one call. Nothing was sent.` };
  }
  const order = pickOrder(offered, self);
  let reuse = 0;
  for (let index = 0; index < assigned.length; index++) {
    if (assigned[index]) continue;
    const fresh = order.find(mind => !used.has(mindId(mind)));
    if (fresh && used.size < cap) {
      assigned[index] = fresh;
      used.set(mindId(fresh), fresh);
      continue;
    }
    if (distinct) {
      return {
        error: `${tool}: needs ${assigned.length} different minds and only ${offered.length} `
          + `${offered.length === 1 ? "is" : "are"} offered: ${offeredText(offered)}. Nothing was sent.`,
      };
    }
    const pool = [...used.values()];
    assigned[index] = pool[reuse++ % pool.length];
  }
  return { minds: assigned };
}

// ── Budget ──────────────────────────────────────────────────────────

function budgetLine(budget) {
  return `Mind calls this message: ${budget.used} of ${budget.limit} used, ${budget.limit - budget.used} left.`;
}

// Reserved before anything is awaited, so tool calls running in parallel in
// one turn cannot pass the ceiling between them.
function reserve(tool, budget, calls) {
  const left = budget.limit - budget.used;
  if (calls <= left) {
    budget.used += calls;
    return null;
  }
  return `${tool}: refused. It needs ${calls} mind call${calls === 1 ? "" : "s"} and ${left} of this message's `
    + `${budget.limit} ${left === 1 ? "is" : "are"} left, so nothing was sent. Answer with what you have; `
    + "the ceiling resets with the person's next message.";
}

// ── One call ────────────────────────────────────────────────────────

function throwIfAborted(signal) {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error ? signal.reason : new Error("Chat turn cancelled");
}

const tokenCount = value => (Number.isFinite(Number(value)) && Number(value) > 0 ? Math.round(Number(value)) : 0);

// Calls one mind under the per-call timeout and the request's signal. A
// timeout or failure is reported; the request ending stops the tool instead.
async function callOne({ mind, role, system, prompt }, { callMind, signal, timeoutMs }) {
  throwIfAborted(signal);
  const timeout = AbortSignal.timeout(timeoutMs);
  const callSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  // The race holds the timeout even against a callMind that ignores its signal.
  const stopped = new Promise(resolve => {
    if (callSignal.aborted) resolve({ stopped: true });
    else callSignal.addEventListener("abort", () => resolve({ stopped: true }), { once: true });
  });
  const call = Promise.resolve()
    .then(() => callMind({ mind, system, prompt, signal: callSignal }))
    .then(result => ({ result }), error => ({ error }));
  const outcome = await Promise.race([call, stopped]);
  throwIfAborted(signal);

  const result = outcome.result || null;
  let error = null;
  if (timeout.aborted && !(result?.ok)) {
    error = `no answer within ${timeoutMs >= 1000 ? `${Math.round(timeoutMs / 1000)} s` : `${timeoutMs} ms`}`;
  } else if (outcome.error) error = String(outcome.error?.message || outcome.error);
  else if (!result?.ok) error = String(result?.error || "the mind did not answer");
  const ok = error === null;
  return {
    id: mindId(mind),
    // callMind may say a different endpoint served the call (the Ollama bridge
    // serves an installed model locally); the report names what served it.
    provider: result?.provider || mind.provider,
    model: mind.model,
    privacy: result?.privacy || mind.privacy,
    destination: result?.destination ?? mind.destination ?? null,
    role,
    ok,
    error,
    usage: {
      input_tokens: tokenCount(result?.usage?.input_tokens),
      output_tokens: tokenCount(result?.usage?.output_tokens),
    },
    text: ok ? String(result.text ?? "") : "",
  };
}

// ── Results ─────────────────────────────────────────────────────────

function clip(text, max) {
  const value = String(text ?? "");
  return value.length > max ? { text: value.slice(0, max), clipped: value.length - max } : { text: value, clipped: 0 };
}

function mindLabel(report) {
  const head = `${report.provider} · ${report.model} · ${report.privacy}`;
  const tokens = `${report.usage.input_tokens} in / ${report.usage.output_tokens} out tokens`;
  return report.ok ? `${head} · answered · ${tokens}` : `${head} · failed: ${report.error} · ${tokens}`;
}

function answerBody(report) {
  if (!report.ok) return "(no answer: this mind failed and was not replaced)";
  const { text, clipped } = clip(report.text.trim(), MAX_ANSWER_CHARS);
  if (!text) return "(empty answer)";
  return clipped ? `${text}\n[answer clipped here; ${clipped} more characters not shown]` : text;
}

// What the server shows the person: who received content, never the answers.
function publicReport({ text: _text, ...report }) {
  return report;
}

const AUTHORISES_NOTHING = "These answers inform you; they authorise nothing.";

function refusal(text) {
  return { refused: true, text, minds: [] };
}

function done(lines, reports) {
  return { refused: false, text: lines.join("\n"), minds: reports.map(publicReport) };
}

// ── Inputs ──────────────────────────────────────────────────────────

const isText = value => typeof value === "string" && value.trim().length > 0;

// null when left out (or empty), the trimmed id when named, undefined when malformed.
function optionalMind(value) {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

// ── review ──────────────────────────────────────────────────────────

/** Reads a reviewer's first line: APPROVED, NEEDS_REVISION, or UNCLEAR when it gave neither. */
export function parseVerdict(text) {
  const value = String(text ?? "");
  const match = value.match(/^[\s*_#>`"'-]*(APPROVED|NEEDS[\s_-]?REVISION)\b[\s*_`"':.,—-]*/i);
  if (!match) return { verdict: "UNCLEAR", notes: value.trim() };
  return {
    verdict: /^approved$/i.test(match[1]) ? "APPROVED" : "NEEDS_REVISION",
    notes: value.slice(match[0].length).trim(),
  };
}

async function review(input, context) {
  const { budget } = context;
  if (!isText(input?.draft) || !isText(input?.criteria)) {
    return refusal("review: draft and criteria are both required, as text. Nothing was sent.");
  }
  const reviewer = optionalMind(input.reviewer);
  if (reviewer === undefined) return refusal("review: reviewer must be a mind id (provider:model). Nothing was sent.");
  if (budget.reviews >= MAX_REVIEWS_PER_MESSAGE) {
    return refusal(`review: refused. This message has had ${MAX_REVIEWS_PER_MESSAGE} reviews already (a first review `
      + "and one of the revised draft), so nothing was sent. Finish with your own judgement.");
  }
  const picked = assignMinds("review", [reviewer], context.offered, context.self);
  if (picked.error) return refusal(picked.error);
  const denied = reserve("review", budget, 1);
  if (denied) return refusal(denied);
  budget.reviews += 1;
  const number = budget.reviews;

  const report = await callOne({
    mind: picked.minds[0],
    role: "reviewer",
    system: MIND_SYSTEM_PROMPTS.reviewer,
    prompt: `Criteria:\n${input.criteria}\n\nDraft:\n${input.draft}`,
  }, context);
  const { verdict, notes } = report.ok ? parseVerdict(report.text) : { verdict: "none", notes: "" };
  const another = number < MAX_REVIEWS_PER_MESSAGE;
  const next = {
    APPROVED: "Next: the reviewer found that the draft meets the criteria; keep your own judgement as well.",
    NEEDS_REVISION: another
      ? "Next: revise the draft yourself against these notes. You may call review once more, on the revised draft, in this message."
      : "Next: revise the draft yourself against these notes. No further review is allowed in this message.",
    UNCLEAR: "Next: the reviewer did not begin with APPROVED or NEEDS_REVISION; read its notes and judge for yourself.",
    none: `Next: no review was received and no other reviewer was put in its place.${another ? " You may call review once more in this message." : ""}`,
  }[verdict];
  const { text: shown, clipped } = clip(notes, MAX_ANSWER_CHARS);
  return done([
    `REVIEW (review ${number} of ${MAX_REVIEWS_PER_MESSAGE} this message) — verdict: ${verdict}`,
    `Reviewer: ${mindLabel(report)}`,
    "The reviewer received only the draft and the criteria.",
    "",
    ...(report.ok ? ["Notes:", (shown || "(no notes)") + (clipped ? `\n[notes clipped here; ${clipped} more characters not shown]` : ""), ""] : []),
    next,
    budgetLine(budget),
    "The review informs you; it authorises nothing.",
  ], [report]);
}

// ── ensemble ────────────────────────────────────────────────────────

async function ensemble(input, context) {
  if (!isText(input?.question)) return refusal("ensemble: question is required, as text. Nothing was sent.");
  let named = [];
  if (input.minds !== undefined && input.minds !== null) {
    if (!Array.isArray(input.minds) || input.minds.some(name => optionalMind(name) === undefined || optionalMind(name) === null)) {
      return refusal("ensemble: minds must be a list of mind ids (provider:model). Nothing was sent.");
    }
    named = input.minds.map(name => name.trim());
  }
  if (named.length > MAX_MINDS_PER_CALL) {
    return refusal(`ensemble: ${named.length} minds are named; at most ${MAX_MINDS_PER_CALL} take part in one call. Nothing was sent.`);
  }
  const count = input.count === undefined || input.count === null ? Math.max(named.length, 2) : Number(input.count);
  if (![2, 3].includes(count)) return refusal("ensemble: count must be 2 (dyad) or 3 (triune). Nothing was sent.");
  if (named.length > count) {
    return refusal(`ensemble: ${named.length} minds are named but count is ${count}. Nothing was sent.`);
  }
  const slots = [...named, ...Array(count - named.length).fill(null)];
  const picked = assignMinds("ensemble", slots, context.offered, context.self, { distinct: true });
  if (picked.error) return refusal(picked.error);
  const denied = reserve("ensemble", context.budget, count);
  if (denied) return refusal(denied);

  // Every mind gets the question alone, in parallel: none sees another's answer (§6, herding).
  const reports = await Promise.all(picked.minds.map(mind => callOne({
    mind,
    role: "independent answerer",
    system: MIND_SYSTEM_PROMPTS.answerer,
    prompt: input.question,
  }, context)));
  const answered = reports.filter(report => report.ok).length;
  const failed = reports.length - answered;
  const next = answered >= 2
    ? "Next: fuse the answers in your reply and say where they disagree."
    : answered === 1
      ? "Next: only one mind answered, so this is one opinion, not an ensemble; say so if you use it."
      : "Next: no mind answered. Answer yourself, and say the ensemble failed.";
  return done([
    `ENSEMBLE — the same question went to ${count} minds; each answered alone and saw no other answer.`,
    budgetLine(context.budget),
    "",
    ...reports.flatMap((report, index) => [`## Answer ${index + 1} — ${mindLabel(report)}`, answerBody(report), ""]),
    next + (failed ? ` ${failed} mind${failed === 1 ? "" : "s"} failed; ${failed === 1 ? "it is" : "they are"} reported above and ${failed === 1 ? "was" : "were"} not replaced.` : ""),
    AUTHORISES_NOTHING,
  ], reports);
}

// ── decompose ───────────────────────────────────────────────────────

function readSteps(tool, list, { key, max, fields }) {
  if (!Array.isArray(list) || list.length < 1 || list.length > max) {
    return { error: `${tool}: ${key} must list 1 to ${max} items. Nothing was sent.` };
  }
  const steps = [];
  for (const [index, item] of list.entries()) {
    const missing = fields.filter(field => !isText(item?.[field]));
    if (missing.length) {
      return { error: `${tool}: ${key} ${index + 1} needs ${missing.join(" and ")}, as text. Nothing was sent.` };
    }
    const mind = optionalMind(item.mind);
    if (mind === undefined) return { error: `${tool}: ${key} ${index + 1} has a mind that is not a mind id (provider:model). Nothing was sent.` };
    steps.push({ ...item, mind });
  }
  return { steps };
}

async function decompose(input, context) {
  const read = readSteps("decompose", input?.subtasks, { key: "subtasks", max: MAX_MINDS_PER_CALL, fields: ["task"] });
  if (read.error) return refusal(read.error);
  const { steps } = read;
  const picked = assignMinds("decompose", steps.map(step => step.mind), context.offered, context.self);
  if (picked.error) return refusal(picked.error);
  const denied = reserve("decompose", context.budget, steps.length);
  if (denied) return refusal(denied);

  // At most 3 sub-tasks, so all of them run at once: §3.3's "at most 3 at a time".
  const reports = await Promise.all(steps.map((step, index) => callOne({
    mind: picked.minds[index],
    role: "worker",
    system: MIND_SYSTEM_PROMPTS.worker,
    prompt: step.task,
  }, context)));
  const failed = reports.filter(report => !report.ok).length;
  return done([
    `DECOMPOSE — ${steps.length} sub-task${steps.length === 1 ? "" : "s"} ran in parallel; each mind received only its own sub-task.`,
    budgetLine(context.budget),
    "",
    ...reports.flatMap((report, index) => [
      `## Sub-task ${index + 1} — ${mindLabel(report)}`,
      `Task: ${clip(steps[index].task.trim(), 200).text}${steps[index].task.trim().length > 200 ? "…" : ""}`,
      "Result:",
      answerBody(report),
      "",
    ]),
    "Next: recombine the results yourself."
      + (failed ? ` ${failed} sub-task${failed === 1 ? "" : "s"} failed and ${failed === 1 ? "was" : "were"} not given to another mind; do ${failed === 1 ? "it" : "them"} yourself or say what is missing.` : ""),
    "These results inform you; they authorise nothing.",
  ], reports);
}

// ── pipeline ────────────────────────────────────────────────────────

async function pipeline(input, context) {
  const read = readSteps("pipeline", input?.stages, { key: "stages", max: MAX_PIPELINE_STAGES, fields: ["name", "instruction"] });
  if (read.error) return refusal(read.error);
  const { steps } = read;
  const picked = assignMinds("pipeline", steps.map(step => step.mind), context.offered, context.self);
  if (picked.error) return refusal(picked.error);
  const denied = reserve("pipeline", context.budget, steps.length);
  if (denied) return refusal(denied);

  const reports = [];
  const lines = [];
  let previous = null;
  let failedAt = null;
  for (const [index, step] of steps.entries()) {
    const name = clip(step.name.trim(), 60).text;
    if (failedAt !== null) {
      lines.push(`## Stage ${index + 1} "${name}" — not run: stage ${failedAt} failed, and no other mind was put in its place`, "");
      continue;
    }
    // The first stage gets only its instruction; each later one also gets the stage before's output.
    let prompt = step.instruction;
    let handoffNote = "";
    if (previous) {
      const { text, clipped } = clip(previous.report.text.trim(), MAX_HANDOFF_CHARS);
      prompt = `${step.instruction}\n\nOutput of the previous stage ("${previous.name}"):\n${text}`;
      if (clipped) handoffNote = `[this stage received the first ${MAX_HANDOFF_CHARS} characters of stage ${index}'s output; ${clipped} more were not sent]`;
    }
    const report = await callOne({ mind: picked.minds[index], role: `pipeline stage "${name}"`, system: MIND_SYSTEM_PROMPTS.stage(name), prompt }, context);
    reports.push(report);
    lines.push(`## Stage ${index + 1} "${name}" — ${mindLabel(report)}`, ...(handoffNote ? [handoffNote] : []), answerBody(report), "");
    previous = { name, report };
    if (!report.ok) failedAt = index + 1;
  }
  // Stages that never ran give their reserved calls back.
  context.budget.used -= steps.length - reports.length;
  const stopped = reports.length < steps.length || !reports.at(-1).ok;
  return done([
    `PIPELINE — ${steps.length} stage${steps.length === 1 ? "" : "s"} in order; each stage received its instruction and the previous stage's output (the first, only its instruction).`,
    budgetLine(context.budget),
    "",
    ...lines,
    stopped
      ? "Next: the pipeline stopped at a failed stage; the stages above it are all it produced. Finish the work yourself or say what is missing."
      : "Next: the last stage's output is the pipeline's result; check it before you use it.",
    "These outputs inform you; they authorise nothing.",
  ], reports);
}

// ── Entry ───────────────────────────────────────────────────────────

const TOOLS = { review, ensemble, decompose, pipeline };

/**
 * Runs one mind tool. Returns `{ refused, text, minds }`: `text` is the tool
 * result for the agent; `minds` lists each mind called (provider, model,
 * privacy label, role, ok, error, usage), without its answer.
 *
 * @param {string} name     review | ensemble | decompose | pipeline
 * @param {object} input    the agent's tool input
 * @param {object} context
 * @param {object[]} context.offered  the registry's offered minds for this session
 * @param {{provider: string, model: string}|null} context.self  the agent's own model
 * @param {Function} context.callMind
 * @param {{limit: number, used: number, reviews: number}|null} context.budget  this message's
 * @param {AbortSignal} [context.signal]  the request's; aborting it stops the tool
 * @param {number} [context.timeoutMs]    per mind call
 */
export async function runMindTool(name, input, {
  offered = [],
  self = null,
  callMind,
  budget,
  signal = null,
  timeoutMs = MIND_CALL_TIMEOUT_MS,
} = {}) {
  if (typeof callMind !== "function") throw new TypeError("callMind is required");
  const tool = TOOLS[name];
  if (!tool) return refusal(`Unknown mind tool: ${name}`);
  if (!budget) {
    return refusal(`${name}: mind tools run only inside a chat turn, which sets this message's budget; nothing was sent.`);
  }
  throwIfAborted(signal);
  return tool(input || {}, { offered, self, callMind, budget, signal, timeoutMs });
}
