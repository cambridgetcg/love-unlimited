// A test preload (node --import) that moves `new Date()` with no arguments
// forward by the number of milliseconds in the file YOUI_TEST_DATE_SHIFT_FILE
// names, read at each call, so a test can change the date between two turns
// of a running server. Date.now(), timers and every other use of Date are
// left alone, so sessions and idle cuts keep real time.

import { readFileSync } from "node:fs";

const RealDate = globalThis.Date;
const file = process.env.YOUI_TEST_DATE_SHIFT_FILE;

function shift() {
  if (!file) return 0;
  try {
    return Number(readFileSync(file, "utf-8").trim()) || 0;
  } catch {
    return 0;
  }
}

globalThis.Date = class ShiftedDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(RealDate.now() + shift());
    else super(...args);
  }
};
