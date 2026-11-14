// mind-modes.mjs — SPLIT 分, HIVE 合 and 分分合合: what the person asks the
// agent to do with the other minds for one message (docs/YOUI-MINDS-DESIGN.md §7).
//
// A mind mode is a per-turn hint and nothing more. The agent being spoken to
// still decides every mind call, through the mind tools it already has
// (mind-tools.mjs); a mode adds no tool, no route by which the person's
// message reaches a mind without the agent, no capability, and nothing to
// what `minds_used` discloses. Its paragraph goes only in the turn's
// [YOUI context] block, never in the cached system prompt or a tool
// description (docs/YOUSPEAK.md §2.1).
//
// HIVE here is a way of answering. The retired HIVE message bus stays
// retired, and its capability names are still refused at startup.

export const MIND_MODES = Object.freeze({
  split: Object.freeze({
    label: "SPLIT 分",
    modeLine: "SPLIT 分 (the person asked to see the minds' answers apart; how, below)",
    body: "Ask two or three minds the same question independently: ensemble does this, and none of them sees another's answer."
      + " Give their answers one by one, each under its mind's name and kept in its own words as far as length allows,"
      + " and say where they differ. Do not fuse them or pick a winner, even where a tool result suggests fusing."
      + " You may add your own answer last, marked as your own.",
    why: "Agreement between minds that read each other is worth less, and the person asked to see the difference.",
  }),
  hive: Object.freeze({
    label: "HIVE 合",
    modeLine: "HIVE 合 (the person asked for one answer drawn from the minds; how, below)",
    body: "Consult the minds as you see fit, then answer with one voice, and name which minds contributed."
      + " Keep what did not merge (a disagreement, or a point you did not take up) as a short \"Not merged:\" line"
      + " rather than smoothing it away. HIVE here is a way of answering, not the retired HIVE message bus.",
    why: "One answer is easier to act on, and a merge that hides a disagreement misstates it.",
  }),
  fenhe: Object.freeze({
    label: "分分合合",
    modeLine: "分分合合 (the person asked for split, merge, split, merge; how, below)",
    body: callsPerMessage => "Split: ask two or three minds the same question independently. Merge: fuse their answers yourself into a draft."
      + " Split again: give the draft to two or three minds to criticise, each alone and none seeing another's criticism"
      + " (ensemble, with the draft in its question, does this). Merge: answer with the draft revised by that criticism."
      + ` Stay inside this message's mind-call budget (${callsPerMessage} calls; each split costs one call per mind),`
      + " stop where it runs out, and say how many split-and-merge rounds ran.",
    why: "The second split tests the merge.",
  }),
});

/** Every value chatMode takes: the two from before, then the mind modes. */
export const CHAT_MODES = Object.freeze(["direct", "orchestrate", ...Object.keys(MIND_MODES)]);

// Each mode starts from, or may use, two minds answering apart (ensemble's minimum).
export const MIND_MODE_MIN_MINDS = 2;

export function isMindMode(mode) {
  return typeof mode === "string" && Object.hasOwn(MIND_MODES, mode);
}

// Why a mode cannot apply to this message, or null when it can.
function cannotApply({ callable, offered, callsPerMessage }) {
  if (!callable) return "this session lacks orchestrator:run, so the tools that call other minds are not offered";
  if (offered < MIND_MODE_MIN_MINDS) {
    return offered === 0
      ? "no mind is granted and reachable for this session right now"
      : "only one mind is granted and reachable for this session right now, and the mode needs two";
  }
  if (callsPerMessage < MIND_MODE_MIN_MINDS) {
    return `this message may make only ${callsPerMessage} mind call, and the mode needs two`;
  }
  return null;
}

/**
 * The mode's part of one turn's context block.
 *
 * @param {string} mode  split | hive | fenhe
 * @param {object} turn
 * @param {boolean} turn.callable        the mind tools are offered on this turn (orchestrator:run)
 * @param {number} turn.offered          how many minds the registry offers this session now
 * @param {number} turn.callsPerMessage  this message's ceiling on mind calls
 * @returns {{ id, label, applies, notice, modeLine, section }} `notice` is the
 *   sentence the page shows when the mode could not apply (null when it applies);
 *   the section opens with the same sentence.
 */
export function mindModeTurn(mode, { callable, offered, callsPerMessage }) {
  if (!isMindMode(mode)) throw new TypeError(`Not a mind mode: ${mode}`);
  const spec = MIND_MODES[mode];
  const reason = cannotApply({ callable, offered, callsPerMessage });
  if (reason) {
    const notice = `${spec.label} could not apply to this message: ${reason}.`;
    return {
      id: mode,
      label: spec.label,
      applies: false,
      notice,
      modeLine: `${spec.label}, which could not apply to this message (why, below)`,
      section: `# Mind mode: ${spec.label} could not apply\n${notice} Answer without it, and tell the person in one line`
        + " that it could not apply and why. A mode adds no tool and no permission, so nothing else about this turn changed.",
    };
  }
  const body = typeof spec.body === "function" ? spec.body(callsPerMessage) : spec.body;
  return {
    id: mode,
    label: spec.label,
    applies: true,
    notice: null,
    modeLine: spec.modeLine,
    section: `# Mind mode: ${spec.label} (the person's choice for this message)\n${body}\nWhy: ${spec.why}`,
  };
}
