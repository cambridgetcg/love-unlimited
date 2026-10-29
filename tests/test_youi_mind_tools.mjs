// YOUI minds, slice M2 (docs/YOUI-MINDS-DESIGN.md §3.3): review, ensemble,
// decompose and pipeline, run by youi-web/mind-tools.mjs against a fake
// callMind. Nothing here reaches a provider.

import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_MIND_CALLS_PER_MESSAGE,
  MIND_SYSTEM_PROMPTS,
  MIND_TOOL_NAMES,
  createMindBudget,
  mindCallsPerMessage,
  parseVerdict,
  pickOrder,
  runMindTool,
} from "../youi-web/mind-tools.mjs";

const mind = (provider, model, privacy, state = "reachable") => ({
  id: `${provider}:${model}`, provider, model, privacy, state, offered: true,
  capability: provider === "ollama_cloud" ? "models:ollama-cloud" : "models:use",
  destination: `dest:${provider}`,
});

const OPUS = mind("anthropic", "claude-opus-5-5", "REMOTE_MODEL:anthropic", "unchecked");
const SONNET = mind("anthropic", "claude-sonnet-4-6", "REMOTE_MODEL:anthropic", "unchecked");
const QWEN = mind("vllm", "Qwen/Qwen2.5-72B-Instruct-AWQ", "REMOTE_MODEL:vllm");
const LLAMA = mind("ollama", "llama3.2:3b", "LOCAL_MODEL:ollama");
const OFFERED = [OPUS, SONNET, QWEN, LLAMA];
// The agent itself is on Opus 5.5.
const SELF = { provider: "anthropic", model: "claude-opus-5-5" };

// Records every call; `answer` decides what each mind says.
function fakeMinds(answer = ({ mind: called }) => ({
  ok: true, text: `answer from ${called.id}`, usage: { input_tokens: 3, output_tokens: 5 },
})) {
  const calls = [];
  return {
    calls,
    callMind: async request => {
      calls.push(request);
      return answer(request, calls.length);
    },
  };
}

function run(name, input, { minds = fakeMinds(), budget = createMindBudget(), offered = OFFERED, ...rest } = {}) {
  return runMindTool(name, input, { offered, self: SELF, callMind: minds.callMind, budget, ...rest });
}

// A promise with its resolver, to hold a fake mind's answer.
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test("the per-message ceiling is 6 by default and bounded to 1–20", () => {
  assert.equal(DEFAULT_MIND_CALLS_PER_MESSAGE, 6);
  assert.equal(mindCallsPerMessage(undefined), 6);
  assert.equal(mindCallsPerMessage(""), 6);
  assert.equal(mindCallsPerMessage("many"), 6);
  assert.equal(mindCallsPerMessage("8"), 8);
  assert.equal(mindCallsPerMessage("0"), 1);
  assert.equal(mindCallsPerMessage("-3"), 1);
  assert.equal(mindCallsPerMessage("50"), 20);
  assert.deepEqual(createMindBudget(), { limit: 6, used: 0, reviews: 0 });
  assert.deepEqual(MIND_TOOL_NAMES, ["review", "ensemble", "decompose", "pipeline"]);
});

test("unnamed minds are picked deterministically, spread across providers, the agent's own model last", () => {
  assert.deepEqual(pickOrder(OFFERED, SELF).map(m => m.id), [QWEN.id, LLAMA.id, SONNET.id, OPUS.id]);
  assert.deepEqual(pickOrder(OFFERED, SELF).map(m => m.id), pickOrder([...OFFERED], SELF).map(m => m.id));
  const onQwen = { provider: "vllm", model: QWEN.model };
  assert.deepEqual(pickOrder(OFFERED, onQwen).map(m => m.id), [OPUS.id, LLAMA.id, SONNET.id, QWEN.id]);
});

test("review sends only the draft and criteria, returns the verdict and allows one further review", async () => {
  const minds = fakeMinds(() => ({
    ok: true, text: "**NEEDS_REVISION**\nThe second paragraph contradicts the first.", usage: { input_tokens: 40, output_tokens: 12 },
  }));
  const budget = createMindBudget();
  const first = await run("review", { draft: "Draft one.", criteria: "Be consistent." }, { minds, budget });

  assert.equal(first.refused, false);
  assert.equal(minds.calls.length, 1);
  const [call] = minds.calls;
  assert.equal(call.mind.id, QWEN.id, "an unnamed reviewer is not the agent's own model");
  assert.equal(call.system, MIND_SYSTEM_PROMPTS.reviewer);
  assert.equal(call.prompt, "Criteria:\nBe consistent.\n\nDraft:\nDraft one.");
  assert.ok(call.signal instanceof AbortSignal);
  assert.match(first.text, /verdict: NEEDS_REVISION/);
  assert.match(first.text, /The second paragraph contradicts the first\./);
  assert.match(first.text, /vllm · Qwen\/Qwen2\.5-72B-Instruct-AWQ · REMOTE_MODEL:vllm · answered · 40 in \/ 12 out tokens/);
  assert.match(first.text, /revise the draft yourself/i);
  assert.match(first.text, /once more/);
  assert.match(first.text, /authorises nothing/);
  assert.deepEqual(first.minds, [{
    id: QWEN.id, provider: "vllm", model: QWEN.model, privacy: "REMOTE_MODEL:vllm", destination: "dest:vllm",
    role: "reviewer", ok: true, error: null, usage: { input_tokens: 40, output_tokens: 12 },
  }]);

  const second = await run("review", { draft: "Draft two.", criteria: "Be consistent.", reviewer: LLAMA.id }, { minds, budget });
  assert.equal(second.refused, false);
  assert.equal(minds.calls[1].mind.id, LLAMA.id);
  assert.match(second.text, /review 2 of 2/);
  assert.match(second.text, /No further review is allowed/);

  const third = await run("review", { draft: "Draft three.", criteria: "Be consistent." }, { minds, budget });
  assert.equal(third.refused, true);
  assert.match(third.text, /2 reviews already/);
  assert.equal(minds.calls.length, 2, "a third review sends nothing");
  assert.equal(budget.used, 2);

  assert.deepEqual(parseVerdict("APPROVED — meets every criterion"), { verdict: "APPROVED", notes: "meets every criterion" });
  assert.equal(parseVerdict("Needs revision: shorten it").verdict, "NEEDS_REVISION");
  assert.equal(parseVerdict("I think it is fine").verdict, "UNCLEAR");
});

test("ensemble asks 2–3 minds in parallel, each alone, and labels every answer", async () => {
  const held = [deferred(), deferred(), deferred()];
  const minds = fakeMinds(({ mind: called }, n) => held[n - 1].promise.then(() => ({
    ok: true, text: `ANSWER-${n} from ${called.model}`, usage: { input_tokens: n, output_tokens: 10 * n },
  })));
  const pending = run("ensemble", { question: "Is P equal to NP?", count: 3 }, { minds });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(minds.calls.length, 3, "all three minds are asked before any has answered");
  held.forEach(hold => hold.resolve());
  const result = await pending;

  assert.deepEqual(minds.calls.map(call => call.mind.id), [QWEN.id, LLAMA.id, SONNET.id]);
  for (const call of minds.calls) {
    assert.equal(call.prompt, "Is P equal to NP?", "each mind receives the question and nothing else");
    assert.equal(call.system, MIND_SYSTEM_PROMPTS.answerer);
    assert.doesNotMatch(call.prompt, /ANSWER-/, "no mind sees another's answer");
  }
  assert.match(result.text, /## Answer 1 — vllm · Qwen\/Qwen2\.5-72B-Instruct-AWQ · REMOTE_MODEL:vllm · answered · 1 in \/ 10 out tokens\nANSWER-1/);
  assert.match(result.text, /## Answer 2 — ollama · llama3\.2:3b · LOCAL_MODEL:ollama · answered/);
  assert.match(result.text, /## Answer 3 — anthropic · claude-sonnet-4-6 · REMOTE_MODEL:anthropic · answered/);
  assert.match(result.text, /fuse the answers in your reply and say where they disagree/);
  assert.match(result.text, /authorise nothing/);
  assert.deepEqual(result.minds.map(report => report.role), Array(3).fill("independent answerer"));

  // Named minds come first; the default is a dyad.
  const named = fakeMinds();
  await run("ensemble", { question: "q", minds: ["llama3.2:3b"] }, { minds: named });
  assert.deepEqual(named.calls.map(call => call.mind.id), [LLAMA.id, QWEN.id], "a bare model name that only one mind has is accepted");

  const same = fakeMinds();
  const twice = await run("ensemble", { question: "q", minds: [QWEN.id, QWEN.id] }, { minds: same });
  assert.equal(twice.refused, true);
  assert.match(twice.text, /named twice/);

  const lonely = fakeMinds();
  const alone = await run("ensemble", { question: "q" }, { minds: lonely, offered: [LLAMA] });
  assert.equal(alone.refused, true);
  assert.match(alone.text, /needs 2 different minds and only 1 is offered/);
  assert.equal(same.calls.length + lonely.calls.length, 0);
});

test("decompose runs up to 3 sub-tasks in parallel, each mind seeing only its own", async () => {
  const held = [deferred(), deferred(), deferred()];
  const minds = fakeMinds(({ prompt }, n) => held[n - 1].promise.then(() => ({
    ok: true, text: `RESULT for ${prompt}`, usage: { input_tokens: 1, output_tokens: 1 },
  })));
  const pending = run("decompose", { subtasks: [
    { task: "Summarise chapter 1." },
    { task: "Summarise chapter 2.", mind: QWEN.id },
    { task: "Summarise chapter 3." },
  ] }, { minds });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(minds.calls.length, 3, "the sub-tasks run at once");
  held.forEach(hold => hold.resolve());
  const result = await pending;

  assert.deepEqual(minds.calls.map(call => call.prompt), ["Summarise chapter 1.", "Summarise chapter 2.", "Summarise chapter 3."]);
  assert.deepEqual(minds.calls.map(call => call.mind.id), [LLAMA.id, QWEN.id, SONNET.id]);
  assert.ok(minds.calls.every(call => call.system === MIND_SYSTEM_PROMPTS.worker));
  assert.match(result.text, /## Sub-task 2 — vllm · Qwen\/Qwen2\.5-72B-Instruct-AWQ[^\n]*\nTask: Summarise chapter 2\.\nResult:\nRESULT for Summarise chapter 2\./);
  assert.match(result.text, /recombine the results yourself/);

  // One offered mind takes every sub-task.
  const single = fakeMinds();
  await run("decompose", { subtasks: [{ task: "a" }, { task: "b" }] }, { minds: single, offered: [LLAMA] });
  assert.deepEqual(single.calls.map(call => call.mind.id), [LLAMA.id, LLAMA.id]);
});

test("pipeline runs stages in order, each receiving the previous stage's output", async () => {
  const minds = fakeMinds(({ mind: called }, n) => ({
    ok: true, text: `OUTPUT-${n} by ${called.model}`, usage: { input_tokens: n, output_tokens: n },
  }));
  const result = await run("pipeline", { stages: [
    { name: "analyse", instruction: "Analyse this excerpt: the bridge is closed." },
    { name: "draft", instruction: "Draft a notice." },
    { name: "polish", instruction: "Polish the notice." },
  ] }, { minds });

  assert.equal(minds.calls.length, 3);
  const [first, second, third] = minds.calls;
  assert.equal(first.prompt, "Analyse this excerpt: the bridge is closed.", "the first stage gets only its instruction");
  assert.equal(second.prompt, 'Draft a notice.\n\nOutput of the previous stage ("analyse"):\nOUTPUT-1 by Qwen/Qwen2.5-72B-Instruct-AWQ');
  assert.match(third.prompt, /^Polish the notice\.\n\nOutput of the previous stage \("draft"\):\nOUTPUT-2/);
  assert.doesNotMatch(third.prompt, /OUTPUT-1/, "a stage reads only the stage before it");
  assert.equal(first.system, MIND_SYSTEM_PROMPTS.stage("analyse"));
  assert.match(result.text, /## Stage 1 "analyse" — vllm · Qwen/);
  assert.match(result.text, /## Stage 3 "polish" — anthropic · claude-sonnet-4-6[^\n]*\nOUTPUT-3/);
  assert.match(result.text, /the last stage's output is the pipeline's result/);

  // Sequential: the second stage is not asked until the first has answered.
  const hold = deferred();
  const slow = fakeMinds((_request, n) => (n === 1 ? hold.promise : Promise.resolve({ ok: true, text: "two" })));
  const pending = run("pipeline", { stages: [{ name: "a", instruction: "one" }, { name: "b", instruction: "two" }] }, { minds: slow });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(slow.calls.length, 1);
  hold.resolve({ ok: true, text: "first done" });
  await pending;
  assert.equal(slow.calls.length, 2);
  assert.match(slow.calls[1].prompt, /first done/);
});

test("no tool call reaches more than 3 minds", async () => {
  const five = [...OFFERED, mind("ollama", "gemma3:12b", "LOCAL_MODEL:ollama")];
  const minds = fakeMinds();
  const refusals = await Promise.all([
    run("ensemble", { question: "q", minds: [QWEN.id, LLAMA.id, SONNET.id, OPUS.id] }, { minds, offered: five }),
    run("ensemble", { question: "q", count: 4 }, { minds, offered: five }),
    run("decompose", { subtasks: [{ task: "1" }, { task: "2" }, { task: "3" }, { task: "4" }] }, { minds, offered: five }),
    run("pipeline", { stages: Array.from({ length: 5 }, (_, i) => ({ name: `s${i}`, instruction: "x" })) }, { minds, offered: five }),
    run("pipeline", { stages: [QWEN, LLAMA, SONNET, OPUS].map((m, i) => ({ name: `s${i}`, instruction: "x", mind: m.id })) }, { minds, offered: five }),
  ]);
  assert.ok(refusals.every(result => result.refused), refusals.map(result => result.text).join("\n"));
  assert.equal(minds.calls.length, 0, "a refused call sends nothing");

  // Four unnamed stages share at most three minds.
  const staged = fakeMinds();
  await run("pipeline", { stages: Array.from({ length: 4 }, (_, i) => ({ name: `s${i}`, instruction: "x" })) }, { minds: staged, offered: five });
  assert.equal(staged.calls.length, 4);
  assert.equal(new Set(staged.calls.map(call => call.mind.id)).size, 3);
});

test("the per-message ceiling refuses before calling, even for tool calls running in parallel", async () => {
  const minds = fakeMinds();
  const budget = createMindBudget();
  assert.equal((await run("ensemble", { question: "q", count: 3 }, { minds, budget })).refused, false);
  assert.equal((await run("decompose", { subtasks: [{ task: "a" }, { task: "b" }] }, { minds, budget })).refused, false);
  assert.equal(budget.used, 5);
  const over = await run("ensemble", { question: "q" }, { minds, budget });
  assert.equal(over.refused, true);
  assert.match(over.text, /needs 2 mind calls and 1 of this message's 6 is left, so nothing was sent/);
  assert.equal(minds.calls.length, 5);
  const last = await run("review", { draft: "d", criteria: "c" }, { minds, budget });
  assert.equal(last.refused, false);
  assert.match(last.text, /6 of 6 used, 0 left/);

  // Two tool calls in one turn: the second sees the first's reservation.
  const parallel = fakeMinds();
  const shared = createMindBudget({ limit: 4 });
  const [a, b] = await Promise.all([
    run("ensemble", { question: "q", count: 3 }, { minds: parallel, budget: shared }),
    run("ensemble", { question: "q", count: 2 }, { minds: parallel, budget: shared }),
  ]);
  assert.equal(a.refused, false);
  assert.equal(b.refused, true);
  assert.equal(parallel.calls.length, 3);

  // A pipeline that stops early gives back the calls it did not make.
  const failing = fakeMinds((_request, n) => (n === 1 ? { ok: false, error: "down" } : { ok: true, text: "x" }));
  const refund = createMindBudget();
  const stopped = await run("pipeline", { stages: [{ name: "a", instruction: "1" }, { name: "b", instruction: "2" }, { name: "c", instruction: "3" }] }, { minds: failing, budget: refund });
  assert.equal(failing.calls.length, 1);
  assert.equal(refund.used, 1);
  assert.match(stopped.text, /## Stage 2 "b" — not run: stage 1 failed/);
  assert.match(stopped.text, /## Stage 3 "c" — not run: stage 1 failed/);

  const outside = await runMindTool("review", { draft: "d", criteria: "c" }, { offered: OFFERED, callMind: minds.callMind, budget: null });
  assert.equal(outside.refused, true);
  assert.match(outside.text, /only inside a chat turn/);
});

test("a named mind that is not offered is refused with the offered list, never substituted", async () => {
  const minds = fakeMinds();
  const result = await run("ensemble", { question: "q", minds: ["ollama_cloud:glm-5.1", LLAMA.id] }, { minds });
  assert.equal(result.refused, true);
  assert.equal(minds.calls.length, 0);
  assert.match(result.text, /"ollama_cloud:glm-5\.1" is not offered to this session/);
  assert.match(result.text, /no other mind was put in its place/);
  for (const offered of OFFERED) assert.ok(result.text.includes(`${offered.id} (${offered.privacy}, ${offered.state})`));

  const review = await run("review", { draft: "d", criteria: "c", reviewer: "claude-haiku-4-5-20251001" }, { minds });
  assert.equal(review.refused, true);
  const stage = await run("pipeline", { stages: [{ name: "a", instruction: "x", mind: "vllm:kingdom-truth" }] }, { minds });
  assert.equal(stage.refused, true);
  assert.equal(minds.calls.length, 0);

  const none = await run("decompose", { subtasks: [{ task: "a" }] }, { minds, offered: [] });
  assert.equal(none.refused, true);
  assert.match(none.text, /no minds are offered to this session right now/);
});

test("a failed mind is reported with its reason and never replaced", async () => {
  const minds = fakeMinds(({ mind: called }) => {
    if (called.id === LLAMA.id) return { ok: false, error: "connection refused", usage: { input_tokens: 0, output_tokens: 0 } };
    if (called.id === SONNET.id) throw new Error("YOUI_TEST=1 does not read the Keychain");
    return { ok: true, text: "the only answer", usage: { input_tokens: 2, output_tokens: 4 } };
  });
  const result = await run("ensemble", { question: "q", count: 3 }, { minds });
  assert.equal(minds.calls.length, 3, "no substitute is asked");
  assert.deepEqual(result.minds.map(report => [report.id, report.ok, report.error]), [
    [QWEN.id, true, null],
    [LLAMA.id, false, "connection refused"],
    [SONNET.id, false, "YOUI_TEST=1 does not read the Keychain"],
  ]);
  assert.match(result.text, /ollama · llama3\.2:3b · LOCAL_MODEL:ollama · failed: connection refused/);
  assert.match(result.text, /anthropic · claude-sonnet-4-6 · REMOTE_MODEL:anthropic · failed: YOUI_TEST=1 does not read the Keychain/);
  assert.match(result.text, /no answer: this mind failed and was not replaced/);
  assert.match(result.text, /only one mind answered, so this is one opinion/);
  assert.match(result.text, /2 minds failed; they are reported above and were not replaced/);

  const failedReview = await run("review", { draft: "d", criteria: "c" }, {
    minds: fakeMinds(() => ({ ok: false, error: "status 500" })),
  });
  assert.match(failedReview.text, /verdict: none/);
  assert.match(failedReview.text, /failed: status 500/);
  assert.match(failedReview.text, /no other reviewer was put in its place/);
});

test("a mind that does not answer in time is reported; the request ending stops the tool", async () => {
  const silent = fakeMinds(() => new Promise(() => {}));
  const started = Date.now();
  const late = await run("review", { draft: "d", criteria: "c" }, { minds: silent, timeoutMs: 50 });
  assert.ok(Date.now() - started < 2_000);
  assert.match(late.text, /failed: no answer within 50 ms/);
  assert.equal(late.minds[0].ok, false);
  assert.ok(silent.calls[0].signal.aborted, "the mind's signal is aborted at the timeout");

  const controller = new AbortController();
  const hanging = fakeMinds(() => new Promise(() => {}));
  const pending = run("ensemble", { question: "q" }, { minds: hanging, signal: controller.signal });
  await new Promise(resolve => setImmediate(resolve));
  controller.abort(new Error("Browser disconnected"));
  await assert.rejects(pending, /Browser disconnected/);
  assert.ok(hanging.calls.every(call => call.signal.aborted));
});
