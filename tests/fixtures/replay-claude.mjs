#!/usr/bin/env node
// A stand-in for the `claude` CLI that replays a recorded Claude Code stream,
// for tests/test_youspeak_sensors.mjs. It makes no network call and reads no
// credential.
//
// FAKE_CLAUDE_REPLAY names a .jsonl file of stdout lines Claude Code printed
// (tests/fixtures/claude-code-stream-2.1.314.jsonl, recorded live on
// 2026-10-31, with paths, socket paths and message and request ids scrubbed).
// Its rate_limit_event values are synthetic: only that event's shape (its
// field names and nesting) is from the live stream, while its status, type,
// utilizations, thresholds, reset times and overage fields were replaced with
// one made-up consistent set. Its session id and event uuids are
// placeholders. It waits for the one stream-json user message on stdin, writes
// the recorded lines unchanged, and exits once stdin closes, as Claude Code
// does after its result. FAKE_CLAUDE_LOG, when set, gets the argv and the
// message as one JSON line.

import { appendFileSync, readFileSync } from "node:fs";
import readline from "node:readline";

const lines = readFileSync(process.env.FAKE_CLAUDE_REPLAY, "utf-8").split("\n").filter(line => line.trim());
const input = readline.createInterface({ input: process.stdin });
let answered = false;
input.on("line", line => {
  if (answered || !line.trim()) return;
  answered = true;
  if (process.env.FAKE_CLAUDE_LOG) {
    appendFileSync(process.env.FAKE_CLAUDE_LOG, `${JSON.stringify({ argv: process.argv.slice(2), line: JSON.parse(line) })}\n`);
  }
  for (const recorded of lines) process.stdout.write(`${recorded}\n`);
});
input.on("close", () => setTimeout(() => process.exit(0), 20));
// Never outlive a test that forgot it.
setTimeout(() => process.exit(3), 30_000).unref();
