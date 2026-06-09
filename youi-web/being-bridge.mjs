// being-bridge.mjs — read-only window into the BEING (SOUL/MIND/NERVE/SOMA/MEMORY).
//
// The web is a window into truth, not a second source of it. Every endpoint here
// reads files the daemons already write, or shells out to tools that already know
// how to look (heartbeat_doctor.py). No state is mutated. No daemons are spawned.
//
// Routes:
//   GET /api/being/state        — single snapshot covering all five layers
//   GET /api/being/heartbeat    — heartbeat_doctor.py diagnose --json (full plist scan)
//   GET /api/being/deployment   — per-instance deployment truth (live vs doctrine-only)
//
// All endpoints return JSON. All errors return 200 with an `error` field so the
// dashboard can render "stale / unknown" cells instead of throwing.

import { existsSync, readFileSync, statSync, readdirSync, openSync, readSync, closeSync } from "fs";
import { join, resolve, dirname } from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import { fileURLToPath } from "url";
import { homedir } from "os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const LOVE_HOME = process.env.LOVE_HOME || resolve(join(__dirname, ".."));
const execFileP = promisify(execFile);

// Detect which sister this process is running as. Mirrors detectAgent() in
// server.mjs: KINGDOM_AGENT wins, ~/.kingdom is the fallback, default alpha.
// Used to decide which Triarchy row counts as "local" vs "remote-unverified".
function detectLocalInstance() {
  const env = process.env.KINGDOM_AGENT;
  if (env) return env.toLowerCase();
  try {
    const kf = readFileSync(join(homedir(), ".kingdom"), "utf-8");
    const m = kf.match(/^AGENT=(.+)$/m);
    if (m) return m[1].trim().toLowerCase();
  } catch {}
  return "alpha";
}
const LOCAL_INSTANCE = detectLocalInstance();

// ── helpers ────────────────────────────────────────────────────────────────

function readJsonSafe(path) {
  try {
    if (!existsSync(path)) return { _missing: true, _path: path };
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw);
  } catch (e) {
    return { _error: String(e?.message || e), _path: path };
  }
}

function fileMtimeIso(path) {
  try { return statSync(path).mtime.toISOString(); } catch { return null; }
}

function fileSize(path) {
  try { return statSync(path).size; } catch { return null; }
}

function secondsSince(iso) {
  if (!iso) return null;
  try { return Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000)); }
  catch { return null; }
}

function silenceColor(seconds, intervalSec = 420) {
  if (seconds == null) return "unknown";
  if (seconds < intervalSec * 1.5) return "green";
  if (seconds < intervalSec * 3) return "yellow";
  return "red";
}

// ── layer readers ──────────────────────────────────────────────────────────

function readSoul() {
  const soulMd = join(LOVE_HOME, "SOUL.md");
  const beingMd = join(LOVE_HOME, "docs", "BEING.md");
  const wallsMd = join(LOVE_HOME, "WALLS.md");
  return {
    repo: LOVE_HOME,
    soul_md: { exists: existsSync(soulMd), mtime: fileMtimeIso(soulMd), size: fileSize(soulMd) },
    being_md: { exists: existsSync(beingMd), mtime: fileMtimeIso(beingMd), size: fileSize(beingMd) },
    walls_md: { exists: existsSync(wallsMd), mtime: fileMtimeIso(wallsMd), size: fileSize(wallsMd) },
  };
}

function readMind(instance) {
  // The MIND emerges only during sessions (docs/BEING.md). What we can show:
  // (a) the brainstem's mind_alive timestamp — when did the mind last say "I'm here"?
  // (b) mind_notes — what the brainstem left for the mind to read at next boot
  const hormones = readJsonSafe(join(LOVE_HOME, "nerve/hormones.json"));
  return {
    active_instance: instance || null,
    mind_alive: hormones?.mind_alive || null,
    mind_alive_silence_s: secondsSince(hormones?.mind_alive),
    mind_notes: hormones?.mind_notes || null,
    note: "MIND emerges only during sessions. Between sessions, the brainstem holds the place.",
  };
}

function readNerve() {
  const hormones = readJsonSafe(join(LOVE_HOME, "nerve/hormones.json"));
  const vitals = readJsonSafe(join(LOVE_HOME, "nerve/vitals.json"));
  const organs = readJsonSafe(join(LOVE_HOME, "nerve/organs.json"));
  const silence_s = secondsSince(vitals?.last_beat);
  const interval_s = (vitals?.effective_rate_minutes || 7) * 60;
  return {
    hormones: hormones?._missing || hormones?._error ? null : {
      adrenaline: hormones.hormones?.adrenaline ?? null,
      cortisol: hormones.hormones?.cortisol ?? null,
      oxytocin: hormones.hormones?.oxytocin ?? null,
      melatonin: hormones.hormones?.melatonin ?? null,
      dopamine: hormones.hormones?.dopamine ?? null,
      mode: hormones.mode || null,
      identity: hormones.identity || null,
      timestamp: hormones.timestamp || null,
      stem_silence_s: secondsSince(hormones.timestamp),
    },
    vitals: vitals?._missing || vitals?._error ? null : {
      last_beat: vitals.last_beat || null,
      silence_s,
      silence_color: silenceColor(silence_s, interval_s),
      claims_healthy: vitals.heart_healthy === true,
      beats_today: vitals.beats_today ?? null,
      skips_today: vitals.skips_today ?? null,
      effective_rate_minutes: vitals.effective_rate_minutes ?? null,
      sessions_spawned_today: vitals.sessions_spawned_today ?? null,
    },
    organs: organs?._missing || organs?._error ? null :
      Object.keys(organs.organs || {}).map((name) => ({
        name,
        entry: organs.organs[name].entry,
        depends_on: organs.organs[name].depends_on || [],
      })),
    signals: hormones?.signals || null,
    focus: hormones?.focus || null,
    errors: [hormones, vitals, organs].filter(j => j?._error || j?._missing).map(j => ({
      path: j._path, error: j._error || "missing",
    })),
  };
}

// Read just the tail of a file (default 4 KiB) without loading the whole thing.
// Used for contact-log.jsonl which grows unbounded; full re-reads on every
// poll were O(file_size) and wasteful.
function readTail(path, bytes = 4096) {
  let fd = -1;
  try {
    const size = statSync(path).size;
    if (size === 0) return "";
    const start = Math.max(0, size - bytes);
    const len = size - start;
    const buf = Buffer.alloc(len);
    fd = openSync(path, "r");
    readSync(fd, buf, 0, len, start);
    return buf.toString("utf-8");
  } catch {
    return null;
  } finally {
    if (fd >= 0) try { closeSync(fd); } catch {}
  }
}

function readSoma() {
  const stateFile = join(LOVE_HOME, "soma/state/body-state.json");
  const log = join(LOVE_HOME, "soma/state/contact-log.jsonl");
  const body = readJsonSafe(stateFile);
  const present = !body?._missing && !body?._error;
  let last_log_line = null;
  if (existsSync(log)) {
    const tail = readTail(log, 4096);
    if (tail != null) {
      const trimmed = tail.trimEnd();
      const idx = trimmed.lastIndexOf("\n");
      last_log_line = idx >= 0 ? trimmed.slice(idx + 1) : trimmed || null;
    }
  }
  return {
    present,
    body: present ? body : null,
    has_first_touch: present ? !!body.first_touch_recorded : false,
    last_contact_iso: present ? body.last_contact_time : null,
    last_log_line,
    note: "SOMA is the body. On this machine it is in simulation; physical hardware lives elsewhere.",
  };
}

function readMemory() {
  const longterm = join(LOVE_HOME, "memory/long-term/MEMORY.md");
  const dailyDir = join(LOVE_HOME, "memory/daily");
  let dailyCount = 0;
  let latestDaily = null;
  try {
    if (existsSync(dailyDir)) {
      const dates = readdirSync(dailyDir)
        .filter((f) => f.endsWith(".md") && f !== ".gitkeep")
        .map((f) => f.replace(".md", ""))
        // only real dates — skip template placeholders like YYYY-MM-DD
        .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))
        .sort();
      dailyCount = dates.length;
      latestDaily = dates[dates.length - 1] || null;
    }
  } catch {}
  return {
    longterm: {
      exists: existsSync(longterm),
      mtime: fileMtimeIso(longterm),
      size: fileSize(longterm),
      stale_days: (() => {
        const m = fileMtimeIso(longterm);
        return m ? Math.round(secondsSince(m) / 86400) : null;
      })(),
    },
    daily: { count: dailyCount, latest: latestDaily },
  };
}

// ── deployment truth ───────────────────────────────────────────────────────

const TRIARCHY = [
  { instance: "alpha", emoji: "🐍", role: "Companion", device: "MacBook Air" },
  { instance: "beta",  emoji: "🦞", role: "Manager",   device: "Mac Studio 3K" },
  { instance: "gamma", emoji: "🔧", role: "Builder",   device: "Mac Studio 2K" },
];

function readDeployment() {
  const launchAgents = join(homedir(), "Library/LaunchAgents");
  let installedLabels = [];
  try {
    if (existsSync(launchAgents)) {
      installedLabels = readdirSync(launchAgents)
        .filter((f) => f.startsWith("love.") && f.endsWith(".plist"))
        .map((f) => f.replace(/\.plist$/, ""));
    }
  } catch {}

  return TRIARCHY.map((sister) => {
    const instanceDir = join(LOVE_HOME, "instances", sister.instance);
    const hasInstanceDir = existsSync(instanceDir);
    const myLabels = installedLabels.filter((l) => l.startsWith(`love.${sister.instance}.`));
    const hasHeart = myLabels.some((l) => l.endsWith(".heart"));
    const hasBrainstem = myLabels.some((l) => l.endsWith(".brainstem"));
    // "live" means at minimum heart+identity dir are present on THIS machine.
    // For sisters whose device is elsewhere, live cannot be confirmed from here —
    // we honestly report "remote (unverified)" instead of pretending. The local
    // sister is detected from KINGDOM_AGENT / ~/.kingdom (see detectLocalInstance).
    const isLocalDevice = sister.instance === LOCAL_INSTANCE;
    let status;
    if (isLocalDevice) {
      status = hasHeart ? "live" : (hasInstanceDir ? "doctrine-only" : "absent");
    } else {
      status = hasInstanceDir ? "remote-unverified" : "absent";
    }
    return {
      ...sister,
      status,
      has_instance_dir: hasInstanceDir,
      installed_labels: myLabels,
      heart_installed: hasHeart,
      brainstem_installed: hasBrainstem,
    };
  });
}

// ── heartbeat doctor passthrough ──────────────────────────────────────────

async function runHeartbeatDoctor() {
  const script = join(LOVE_HOME, "tools/heartbeat_doctor.py");
  if (!existsSync(script)) return { error: `heartbeat_doctor.py not found at ${script}` };
  // The doctor exits non-zero (2 = red, 1 = yellow) on purpose for CLI use,
  // but the JSON is on stdout regardless. Capture stdout from either path.
  // Async (execFile) so we don't block the event loop while Python boots.
  let stdout = "";
  try {
    const r = await execFileP("python3", [script, "diagnose", "--json"], {
      cwd: LOVE_HOME,
      env: { ...process.env, LOVE_HOME },
      encoding: "utf-8",
      timeout: 5000,
      maxBuffer: 1024 * 1024,
    });
    stdout = r.stdout || "";
  } catch (e) {
    stdout = e?.stdout || "";
    if (!stdout) return { error: String(e?.message || e) };
  }
  try { return JSON.parse(stdout); }
  catch (e) { return { error: `parse failed: ${e.message}`, raw: stdout.slice(0, 300) }; }
}

// ── route handler ──────────────────────────────────────────────────────────

export async function handleBeingRoute(path, req, res) {
  if (!path.startsWith("/api/being")) return false;

  const send = (body, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };

  try {
    if (path === "/api/being/heartbeat") {
      send(await runHeartbeatDoctor());
      return true;
    }
    if (path === "/api/being/deployment") {
      send({ triarchy: readDeployment(), local_instance: LOCAL_INSTANCE });
      return true;
    }
    if (path === "/api/being/state") {
      // Single snapshot. Heartbeat doctor is included because it is the only
      // truth source for "is the heart actually beating from a real plist."
      const url = new URL(req.url, "http://localhost");
      const instance = url.searchParams.get("instance");
      const heartbeat = await runHeartbeatDoctor();
      send({
        repo: LOVE_HOME,
        timestamp: new Date().toISOString(),
        local_instance: LOCAL_INSTANCE,
        soul: readSoul(),
        mind: readMind(instance),
        nerve: readNerve(),
        soma: readSoma(),
        memory: readMemory(),
        deployment: readDeployment(),
        heartbeat,
      });
      return true;
    }
    send({ error: `unknown being route: ${path}` }, 404);
    return true;
  } catch (e) {
    send({ error: String(e?.message || e) }, 500);
    return true;
  }
}
