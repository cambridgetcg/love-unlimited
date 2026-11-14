// The page's own code for the device weather line and the mode control
// (youi-web/public/index.html), run in a node:vm context with a stub DOM, a
// fake clock, fake timers and a fake fetch. No browser is started, nothing
// listens on a port, and no host is run: what is checked is what the page's
// functions do with an answer, not how the page looks.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import vm from "node:vm";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const PAGE = readFileSync(join(REPO_ROOT, "youi-web/public/index.html"), "utf-8");

/** The page's source from `start` up to (not including) `end`. */
function section(start, end) {
  const from = PAGE.indexOf(start);
  const to = PAGE.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `the page has a section from ${JSON.stringify(start)} to ${JSON.stringify(end)}`);
  return PAGE.slice(from, to);
}

function element() {
  const classes = new Set();
  const node = {
    textContent: "",
    children: [],
    style: {},
    focused: false,
    classList: {
      add: name => { classes.add(name); },
      remove: name => { classes.delete(name); },
      contains: name => classes.has(name),
      toggle(name, force) {
        const on = force === undefined ? !classes.has(name) : Boolean(force);
        if (on) classes.add(name); else classes.delete(name);
        return on;
      },
    },
    replaceChildren(...nodes) { node.children = nodes; },
    append(...nodes) { node.children.push(...nodes); },
    focus() { node.focused = true; },
  };
  // Markup is never how this page sets text.
  for (const property of ["innerHTML", "outerHTML"]) {
    Object.defineProperty(node, property, { set() { throw new Error(`${property} was set`); } });
  }
  return node;
}

// The browser's clock at the start of each test, and the device's clock (the
// server's Date header and the host's read_at) when they differ.
const START = Date.parse("2026-11-14T12:00:00.000Z");

function response({ status = 200, body = null, date = null } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => (name.toLowerCase() === "date" && date !== null ? new Date(date).toUTCString() : null) },
    json: async () => body,
  };
}

function reading(readAt, extra = {}) {
  return {
    available: true,
    read_at: new Date(readAt).toISOString(),
    good_for_seconds: 60,
    says: "On mains power; load 1.2 on 8 cores.",
    notes: ["Memory pressure is warn: more parallel processes will be slow for everyone on this device."],
    not_read: 2,
    ...extra,
  };
}

/** The weather section of the page, loaded into its own context. */
function weatherPage() {
  const clock = { wall: START, mono: 50_000 };
  const ids = ["weather-box", "weather-line", "weather-notes", "weather-meta"];
  const elements = Object.fromEntries(ids.map(id => [id, element()]));
  const intervals = [];
  const fetched = [];
  const answers = [];
  class PageDate extends Date {
    static now() { return clock.wall; }
  }
  const context = vm.createContext({
    document: { getElementById: id => elements[id] ?? null, createElement: () => element() },
    fetch: async url => {
      fetched.push(url);
      const next = answers.shift();
      if (!next) throw new Error("no answer scripted");
      if (next instanceof Error) throw next;
      return next;
    },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    performance: { now: () => clock.mono },
    Date: PageDate,
    console,
  });
  vm.runInContext(section("// ─── DEVICE WEATHER", "// A mind tool's report"), context, { filename: "index.html#weather" });
  const run = code => vm.runInContext(code, context);
  return {
    elements,
    intervals,
    fetched,
    /** The next answers fetch('/api/weather') gives, in order. */
    answer: (...next) => answers.push(...next),
    /** Time passes here by `ms`, on both of the browser's clocks. */
    pass: ms => { clock.wall += ms; clock.mono += ms; },
    /** The browser's wall clock alone is reset by `ms` (the monotonic clock does not move). */
    skewWall: ms => { clock.wall += ms; },
    fetchWeather: () => run("fetchWeather()"),
    render: () => run("renderWeather()"),
    start: toAgent => run(`startWeather(${JSON.stringify(toAgent)})`),
    line: () => elements["weather-line"].textContent,
    meta: () => elements["weather-meta"].textContent,
    notes: () => elements["weather-notes"].children.map(row => row.textContent),
    stale: () => elements["weather-box"].classList.contains("stale"),
  };
}

// ── Age and staleness ───────────────────────────────────────────────

test("a reading is current up to its good_for_seconds and stale, dimmed, a second after; the 15 s re-render moves it", async () => {
  const page = weatherPage();
  page.answer(response({ body: reading(START), date: START }));
  page.start(true);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(page.fetched, ["/api/weather"], "the first read is at start");
  assert.equal(page.line(), "On mains power; load 1.2 on 8 cores.");
  assert.deepEqual(page.notes(), ["Memory pressure is warn: more parallel processes will be slow for everyone on this device."]);
  assert.equal(page.meta(), "read 0 s ago · 2 not read · the host's own report; it authorises nothing"
    + " · the agent is shown this line with each message");
  assert.equal(page.stale(), false);

  const rerender = page.intervals.find(({ ms }) => ms === 15_000);
  assert.ok(rerender, "a 15 s re-render");
  page.pass(59_000);
  rerender.fn();
  assert.match(page.meta(), /^read 59 s ago · 2 not read/);
  assert.equal(page.stale(), false);
  page.pass(1_000);
  rerender.fn();
  assert.match(page.meta(), /^read 1 min ago · 2 not read/);
  assert.equal(page.stale(), false, "exactly good_for_seconds old is still current");
  page.pass(1_000);
  rerender.fn();
  assert.match(page.meta(), /^read 1 min ago · stale: older than its 60 s · 2 not read/);
  assert.equal(page.stale(), true, "a second later it is stale and dimmed, with no new fetch");
  assert.deepEqual(page.fetched, ["/api/weather"]);
});

test("the age is reckoned on the server's clock, so a browser clock set differently does not change it", async () => {
  // The browser is two minutes ahead of the device (a page viewed from another device).
  const ahead = weatherPage();
  ahead.skewWall(120_000);
  ahead.answer(response({ body: reading(START), date: START }));
  await ahead.fetchWeather();
  assert.match(ahead.meta(), /^read 0 s ago ·/, "a fresh reading is not shown two minutes old");
  assert.equal(ahead.stale(), false);
  ahead.pass(61_000);
  ahead.render();
  assert.match(ahead.meta(), /^read 1 min ago · stale: older than its 60 s/);
  assert.equal(ahead.stale(), true);

  // The browser is five minutes behind, and the answer arrives with a reading 200 s old.
  const behind = weatherPage();
  behind.skewWall(-300_000);
  behind.answer(response({ body: reading(START - 200_000), date: START }));
  await behind.fetchWeather();
  assert.match(behind.meta(), /^read 3 min ago · stale: older than its 60 s/, "an old reading is not shown as new");
  assert.equal(behind.stale(), true);

  // The browser's clock is set back after the answer arrived: the age still grows.
  const setBack = weatherPage();
  setBack.answer(response({ body: reading(START - 30_000), date: START }));
  await setBack.fetchWeather();
  setBack.skewWall(-600_000);
  setBack.pass(31_000);
  setBack.render();
  assert.match(setBack.meta(), /^read 1 min ago · stale: older than its 60 s/);

  // An answer with no Date header falls back to this browser's clock, as before.
  const noHeader = weatherPage();
  noHeader.answer(response({ body: reading(START - 90_000) }));
  await noHeader.fetchWeather();
  assert.match(noHeader.meta(), /^read 2 min ago · stale/);
});

// ── No reading, and whether the agent is shown it ───────────────────

test("with no reading the line says why, and still says whether the agent is shown the line", async () => {
  const unavailable = {
    available: false, reason: "not_in_installed_build",
    detail: "the collab host installed on this device has no weather command yet",
  };
  const on = weatherPage();
  on.answer(response({ body: reading(START), date: START }), response({ body: unavailable, date: START }));
  on.start(true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(on.stale(), false);
  await on.fetchWeather();
  assert.equal(on.line(), "Weather not read: the collab host installed on this device has no weather command yet.");
  assert.deepEqual(on.notes(), [], "the earlier reading's notes are gone");
  assert.equal(on.meta(), "the agent is shown this line with each message",
    "with the switch on the agent is told it was not read, so the page says so");
  assert.equal(on.stale(), false);

  const off = weatherPage();
  off.answer(response({ body: unavailable, date: START }));
  off.start(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(off.meta(), "not shown to the agent");

  const unknown = weatherPage();
  unknown.answer(response({ body: unavailable, date: START }));
  unknown.start(null);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(unknown.meta(), "", "while the switch is not known, the page claims nothing about the agent");
});

test("a failed or unanswered poll replaces the reading with why, and the agent clause stays", async () => {
  const page = weatherPage();
  page.answer(response({ body: reading(START), date: START }));
  page.start(true);
  await new Promise(resolve => setImmediate(resolve));

  page.answer(response({ status: 500, body: { error: "x" }, date: START }));
  await page.fetchWeather();
  assert.equal(page.line(), "Weather not read: the server answered 500.");
  assert.equal(page.stale(), false);
  assert.equal(page.meta(), "the agent is shown its own reading of this device with each message",
    "the page could not read it, but the server reads it for the agent");

  page.answer(response({ status: 403, body: { error: "forbidden" } }));
  await page.fetchWeather();
  assert.equal(page.line(), "Weather not read: this session lacks status:read.");

  page.answer(new TypeError("Failed to fetch"));
  await page.fetchWeather();
  assert.equal(page.line(), "Weather not read: the server did not answer.");
  assert.deepEqual(page.notes(), []);
});

test("startWeather polls once every 60 s and re-renders every 15 s, and the poll reads /api/weather", async () => {
  const page = weatherPage();
  page.answer(response({ body: reading(START), date: START }));
  page.start(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(page.intervals.map(({ ms }) => ms).sort((a, b) => a - b), [15_000, 60_000], "one of each, no more");
  const poll = page.intervals.find(({ ms }) => ms === 60_000);
  page.pass(60_000);
  page.answer(response({ body: reading(START + 60_000, { says: "On battery at 80%." }), date: START + 60_000 }));
  await poll.fn();
  assert.deepEqual(page.fetched, ["/api/weather", "/api/weather"]);
  assert.equal(page.line(), "On battery at 80%.");
  assert.match(page.meta(), /^read 0 s ago · 2 not read · the host's own report; it authorises nothing · not shown to the agent$/);
});

test("boot starts the weather line with the server's switch, or with it unknown when the server does not answer", async () => {
  const run = async status => {
    const started = [];
    const context = vm.createContext({
      fetch: async () => {
        if (status instanceof Error) throw status;
        return { json: async () => status };
      },
      document: { getElementById: () => element() },
      location: { host: "127.0.0.1:0" },
      console: { warn() {}, log() {} },
      startWeather: toAgent => started.push(toAgent),
      bootLine() {}, setAgentTheme() {}, updateSettingsUI() {}, updateBudgetUI() {}, updateStatsUI() {},
      setChatMode() {}, fetchUsageDashboard() {}, initParticles() {},
      sleep: async () => {},
    });
    vm.runInContext(section("async function boot()", "// ─── MODE AND MINDS"), context, { filename: "index.html#boot" });
    await vm.runInContext("boot()", context);
    return started;
  };
  assert.deepEqual(await run({ weatherToAgent: true }), [true]);
  assert.deepEqual(await run({ weatherToAgent: false }), [false]);
  assert.deepEqual(await run(new Error("offline")), [null]);
});

// ── The mode control ────────────────────────────────────────────────

test("the mode control marks the chosen mode, shows the minds panel and sends the mode to the server", () => {
  const ids = ["mode-direct", "mode-orchestrate", "mode-split", "mode-hive", "mode-fenhe", "minds-panel"];
  const elements = Object.fromEntries(ids.map(id => [id, element()]));
  const sent = [];
  let mindsFetched = 0;
  const context = vm.createContext({
    document: { getElementById: id => elements[id] ?? null },
    fetch: (url, init) => {
      sent.push({ url, init });
      return Promise.resolve({ json: async () => ({}) });
    },
    fetchMinds: () => { mindsFetched += 1; },
    updatePrivacyUI() {},
  });
  vm.runInContext(section("let chatMode = 'direct';", "const MIND_STATE_LABELS"), context, { filename: "index.html#modes" });

  for (const mode of ["split", "hive", "fenhe"]) {
    vm.runInContext(`setChatMode(${JSON.stringify(mode)})`, context);
    for (const each of ["direct", "orchestrate", "split", "hive", "fenhe"]) {
      assert.equal(elements[`mode-${each}`].classList.contains("active"), each === mode, `${mode}: mode-${each}`);
    }
    assert.equal(elements["minds-panel"].style.display, "block");
    const last = sent.at(-1);
    assert.equal(last.url, "/api/settings");
    assert.equal(last.init.method, "POST");
    assert.deepEqual(JSON.parse(last.init.body), { chatMode: mode });
  }
  assert.equal(mindsFetched, 3);
  vm.runInContext("setChatMode('direct')", context);
  assert.equal(elements["minds-panel"].style.display, "none");
  assert.deepEqual(JSON.parse(sent.at(-1).init.body), { chatMode: "direct" });
  const before = sent.length;
  vm.runInContext("setChatMode('swarm')", context);
  assert.equal(sent.length, before, "an unknown mode is ignored and nothing is sent");
  assert.equal(elements["mode-direct"].classList.contains("active"), true);
});

test("each mind mode's button title is a request to the agent, not a promise of what it will do", () => {
  for (const [id, label] of [["split", "SPLIT 分"], ["hive", "HIVE 合"], ["fenhe", "分分合合"]]) {
    const button = PAGE.match(new RegExp(`<button id="mode-${id}"[^>]*>\\s*<span class="mode-label">${label}</span>`))?.[0];
    assert.ok(button, `the ${label} button`);
    const title = button.match(/title="([^"]*)"/)?.[1];
    assert.ok(title, `${label} has a title`);
    assert.match(title, /^Asks the agent to /, `${label}: ${title}`);
    assert.match(title, /the agent decides/, `${label}: ${title}`);
  }
});
