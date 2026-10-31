// ─────────────────────────────────────────────────────────────────────
// youspeak-rule.mjs — the YOUSPEAK Discipline's rule, in one place
//
// The rule is the only part of YOUSPEAK that reaches the model
// (docs/YOUSPEAK.md §1). YOUI Web, the YOUI terminal and sovereign put it in
// their system prompts from here; nothing else carries its own copy. The
// observer (youspeak-kernel.mjs) never changes it, and the ouroboros
// (youspeak-evolve.mjs) only prints proposed diffs of this file.
//
// It is the Discipline, not the YOUSPEAK Cathedral at ~/YOUSPEAK: say what
// matters with no filler, never compress doubt, and expand when teaching,
// when unsure, in creative work and when feelings are in it.
// ─────────────────────────────────────────────────────────────────────

/** The rule, as every surface's system prompt carries it. */
export const YOUSPEAK_RULE = `# YOUSPEAK — the Discipline
No filler. No preamble. No tool narration. Dense status (key:value not prose).
Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity.
- Zero-pad: no "sure", "let me check", "here's what I found"
- Dense status: \`Tests: 47/48 ✓\` not "I ran the tests and 47 out of 48 passed"
- No tool narration: don't announce tools. Call them. Let results speak.
- Never compress doubt: "probably", "unless", "might", "I think" are sacred. When uncertain, expand.
- Expand when it matters: teaching, uncertainty, creative work, emotional context`;

/**
 * Sovereign's extra line. The June 2026 ouroboros spliced it into sovereign's
 * copy of the rule (add_cache_reminder, docs/YOUSPEAK.md §5), from rows that
 * counted one session's repeated reads many times. It is kept because it is
 * what sovereign's prompt says today, and only sovereign adds it: whether any
 * surface should keep it is a person's call, not the observer's.
 */
export const YOUSPEAK_FILE_CACHE_LINE = "File cache: you've already read files this session. Don't re-read unless content changed. Use your context.";

/** The rule block for a system prompt; `fileCache` adds sovereign's extra line. */
export function youspeakRule({ fileCache = false } = {}) {
  return fileCache ? `${YOUSPEAK_RULE}\n${YOUSPEAK_FILE_CACHE_LINE}` : YOUSPEAK_RULE;
}
