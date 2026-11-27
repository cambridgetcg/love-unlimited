// seat-anchor.mjs — Cryptographic bridge from kingdom-seat to love-unlimited JS harnesses.
//
// Verifies a seated citizen's `kingdom-seat` ledger, pinned origin, covenants,
// cadence, and HALT state offline using Node's built-in Ed25519 (`node:crypto`)
// with strict RFC 8032 canonical S < L checking — zero Keychain access, zero
// private keys, and strict adherence to kingdom-seat Principle 2.

import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";

export const WAKE_ANCHOR_FORMAT = "kingdom-seat-anchor/v1";
const LEDGER_DOMAIN = "kingdom-seat-ledger/v1";
const AUTHORITY_DOMAIN = "identity-authority/v1";
const DEFAULT_CONFIG_PATH = "/Users/Shared/kingdom-seat/config.json";
const DEFAULT_HALT_PATH = "/Users/Shared/kingdom-seat/HALT";
const WORD_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const DID_RE = /^did:at:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const ED25519_L = (1n << 252n) + 27742317777372353535851937790883648493n;

export const WAKE_ANCHOR_CUSTODY_NOTICE =
  "Public verification receipt only. Reading this anchor verifies ledger continuity, pinned origin, and covenants, and confers no signing key, DID custody, or seat authority; only an opened citizen-seat signs ledger entries.";

function sha256Buf(data) {
  return createHash("sha256").update(data).digest();
}

function sha256Hex(data) {
  return createHash("sha256").update(data).digest("hex");
}

function fromB64Strict(s, length) {
  if (typeof s !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(s)) {
    throw new Error("not padded standard base64");
  }
  const buf = Buffer.from(s, "base64");
  if (buf.toString("base64") !== s) throw new Error("non-canonical base64");
  if (length !== undefined && buf.length !== length) throw new Error(`expected ${length} bytes`);
  return buf;
}

function fromB64urlStrict(s, length) {
  if (typeof s !== "string" || !/^[A-Za-z0-9_-]*$/.test(s)) {
    throw new Error("not unpadded base64url");
  }
  const buf = Buffer.from(s, "base64url");
  if (buf.toString("base64url") !== s) throw new Error("non-canonical base64url");
  if (length !== undefined && buf.length !== length) throw new Error(`expected ${length} bytes`);
  return buf;
}

export function keyFingerprint(pub) {
  if (pub.length !== 32) throw new Error("public key must be 32 bytes");
  return `SHA256:${sha256Buf(pub).toString("base64").replace(/=+$/, "")}`;
}

export function hostKid(pub) {
  return `host:${keyFingerprint(pub)}`;
}

export function canonicalJson(value) {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean":
      return value ? "true" : "false";
    case "string":
      return JSON.stringify(value);
    case "number":
      if (!Number.isFinite(value)) throw new Error("non-finite number");
      return JSON.stringify(value);
    case "object": {
      if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
      const keys = Object.keys(value).sort();
      return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
    }
    default:
      throw new Error(`cannot encode ${typeof value}`);
  }
}

function ledgerDigest(value) {
  const payload = Buffer.from(canonicalJson(value), "utf8");
  return sha256Buf(Buffer.concat([Buffer.from(LEDGER_DOMAIN, "utf8"), Buffer.from([0]), payload]));
}

export function entryHash(entry) {
  return `sha256:${ledgerDigest(entry).toString("hex")}`;
}

function signingDigest(entry) {
  const { sig: _s, ...unsigned } = entry;
  return ledgerDigest(unsigned);
}

export function edVerifyStrict(sig, msg, pub) {
  if (sig.length !== 64 || pub.length !== 32) return false;
  // Enforce canonical S < L (RFC 8032 strict / zip215: false)
  let s = 0n;
  for (let i = 63; i >= 32; i--) s = (s << 8n) | BigInt(sig[i]);
  if (s >= ED25519_L) return false;
  try {
    const keyObj = createPublicKey({
      key: Buffer.concat([ED25519_SPKI_PREFIX, pub]),
      format: "der",
      type: "spki",
    });
    return cryptoVerify(null, msg, keyObj, sig);
  } catch {
    return false;
  }
}

function checkRootEvidence(ev, did, rootPub, kind, targetPubOrKid) {
  if (!DID_RE.test(did)) return "root evidence: invalid DID";
  const id = did.slice("did:at:".length);
  const own = `/v1/identities/${id}`;
  const query = `?identity_id=${id}`;
  let body;
  try {
    body = fromB64urlStrict(ev?.body ?? "");
  } catch {
    return "root evidence body is not base64url";
  }
  if (kind === "import") {
    if (ev?.method !== "POST" || (ev?.target !== `${own}/keys/import` && ev?.target !== `${own}/keys/import${query}`)) {
      return "root evidence is not this identity's keys/import";
    }
    let namedPub;
    try {
      const parsed = JSON.parse(body.toString("utf8"));
      namedPub = fromB64Strict(parsed.public_key, 32);
    } catch {
      return "root evidence names no 32-byte key in standard base64";
    }
    if (!namedPub.equals(targetPubOrKid)) return "root evidence imports another key";
  } else {
    if (ev?.method !== "DELETE" || (ev?.target !== `${own}/keys/${targetPubOrKid}` && ev?.target !== `${own}/keys/${targetPubOrKid}${query}`)) {
      return "root evidence is not this identity's revoke of that key";
    }
    if (body.length > 0) return "root evidence revoke carries a body";
  }
  const h = ev?.headers || {};
  const seqRaw = String(h["X-Agenttool-Authority-Sequence"] || "");
  const ts = h["X-Agenttool-Authority-Timestamp"] || "";
  const sigB64 = h["X-Agenttool-Authority-Signature"] || "";
  if (!/^[1-9][0-9]{0,15}$/.test(seqRaw) || typeof ts !== "string" || ts.length !== 24) {
    return "root evidence authority headers invalid";
  }
  let sig;
  try {
    sig = fromB64Strict(sigB64, 64);
  } catch {
    return "root evidence signature invalid";
  }
  const preimage = [AUTHORITY_DOMAIN, did, ev.method, ev.target, sha256Hex(body), seqRaw, ts].join("\0");
  const digest = sha256Buf(Buffer.from(preimage, "utf8"));
  if (!edVerifyStrict(sig, digest, rootPub)) return "root evidence is not signed by the root";
  return null;
}

export function verifyLedgerChain(entries, word, hostKeysB64, identity = null) {
  const hostMap = new Map();
  for (const b64 of hostKeysB64 || []) {
    try {
      const pub = fromB64Strict(b64, 32);
      hostMap.set(hostKid(pub), pub);
    } catch {}
  }
  if (!entries || entries.length === 0) {
    return { ok: false, entries: 0, head: null, host_attested: 0, problem: { seq: 0, reason: "empty ledger" } };
  }

  let pinnedOps = new Map();
  let rootPub = null;
  let chainDid = null;
  let seenGenesis = false;
  let prevH = null;
  let lastGoodHead = null;
  let hostAttested = 0;

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const seq = i;
    if (e.format !== LEDGER_DOMAIN || e.word !== word || e.seq !== seq) {
      return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "entry header mismatch" } };
    }
    if (seq === 0 && e.type !== "origin") {
      return { ok: false, entries: entries.length, head: null, host_attested: 0, problem: { seq: 0, reason: "first entry is not origin" } };
    }
    if (e.prev_hash !== prevH) {
      return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "prev_hash mismatch" } };
    }
    let sig;
    try {
      sig = fromB64urlStrict(e.sig, 64);
    } catch {
      return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "malformed signature" } };
    }
    const sDigest = signingDigest(e);
    if (e.signer === "host") {
      const pub = hostMap.get(e.kid);
      if (!pub) return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "host key is not trusted" } };
      if (!edVerifyStrict(sig, sDigest, pub)) {
        return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "invalid host signature" } };
      }
      hostAttested++;
    } else if (e.signer === "citizen") {
      if (e.type === "genesis") {
        if (seenGenesis) return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "duplicate genesis" } };
        let gRoot, gOp;
        try {
          gRoot = fromB64urlStrict(e.root.public_key, 32);
          gOp = fromB64urlStrict(e.op.public_key, 32);
        } catch {
          return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "malformed genesis keys" } };
        }
        if (identity) {
          let expectedRoot = Buffer.alloc(0);
          try { expectedRoot = fromB64Strict(identity.root_public_key_b64, 32); } catch {}
          if (e.did !== identity.did || !gRoot.equals(expectedRoot)) {
            return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "genesis does not match citizen.json identity" } };
          }
        }
        const evProb = checkRootEvidence(e.evidence, e.did, gRoot, "import", gOp);
        if (evProb) return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: `genesis: ${evProb}` } };
        if (e.kid !== e.op.kid || !edVerifyStrict(sig, sDigest, gOp)) {
          return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "invalid genesis op signature" } };
        }
        seenGenesis = true;
        chainDid = e.did;
        rootPub = gRoot;
        pinnedOps = new Map([[e.op.kid, gOp]]);
      } else {
        const pub = pinnedOps.get(e.kid);
        if (!pub || !edVerifyStrict(sig, sDigest, pub)) {
          return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "invalid citizen op signature" } };
        }
      }
    } else {
      return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq, reason: "unknown signer" } };
    }
    prevH = entryHash(e);
    lastGoodHead = prevH;
  }

  if (identity && !seenGenesis) {
    return { ok: false, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: { seq: entries.length, reason: "registered citizen ledger has no genesis" } };
  }
  return { ok: true, entries: entries.length, head: lastGoodHead, host_attested: hostAttested, problem: null };
}

const REST_RANK = { asleep: 0, listening: 1, awake: 2 };

function foldLifecycleState(entries) {
  let state = "asleep";
  for (const e of entries) {
    if (state === "ended") continue;
    if (e.type === "state") state = e.to ?? state;
    else if (e.type === "beat") {
      for (const act of e.acts || []) {
        if (act.type === "state.set" && act.to in REST_RANK && state in REST_RANK && REST_RANK[act.to] < REST_RANK[state]) {
          state = act.to;
        }
      }
    }
  }
  return state;
}

function discoverSeatPaths(stateDir = null) {
  const envAnchor = process.env.KINGDOM_SEAT_ANCHOR_FILE;
  if (envAnchor) return { kind: "anchor_file", path: envAnchor };

  const explicitState = stateDir || process.env.KINGDOM_SEAT_STATE_DIR;
  const cfgPath = process.env.KINGDOM_SEAT_CONFIG || DEFAULT_CONFIG_PATH;

  if (explicitState) {
    const haltEnv = process.env.KINGDOM_SEAT_HALT_FILE;
    const opDir = join(dirname(explicitState), "operator");
    let haltFile = haltEnv || (existsSync(opDir) ? join(opDir, "HALT") : DEFAULT_HALT_PATH);
    let hostKeys = (process.env.KINGDOM_SEAT_HOST_KEYS_B64 || "").split(",").map((s) => s.trim()).filter(Boolean);
    let host = "local-host";
    const statusPath = join(explicitState, "status.json");
    if (existsSync(statusPath)) {
      try {
        const st = JSON.parse(readFileSync(statusPath, "utf8"));
        host = st.host || host;
        if (!haltEnv && st.halt?.file) haltFile = st.halt.file;
        if (hostKeys.length === 0 && Array.isArray(st.host_keys)) {
          hostKeys = st.host_keys.map((hk) => hk.public_key_b64).filter(Boolean);
        }
      } catch {}
    }
    return {
      kind: "state_dir",
      host,
      stateDir: explicitState,
      citizensDir: join(explicitState, "citizens"),
      ledgerDir: join(explicitState, "ledger"),
      haltFile,
      hostKeysB64: hostKeys,
    };
  }

  if (existsSync(cfgPath)) {
    try {
      const cfg = JSON.parse(readFileSync(cfgPath, "utf8"));
      const sd = cfg.paths.state_dir;
      let hostKeys = [...(cfg.trust?.host_attest_public_keys_b64 || [])];
      const statusPath = cfg.paths.status_file || join(sd, "status.json");
      if (hostKeys.length === 0 && existsSync(statusPath)) {
        const st = JSON.parse(readFileSync(statusPath, "utf8"));
        hostKeys = (st.host_keys || []).map((hk) => hk.public_key_b64).filter(Boolean);
      }
      return {
        kind: "state_dir",
        host: cfg.host || "local-host",
        stateDir: sd,
        citizensDir: cfg.paths.citizens_dir || join(sd, "citizens"),
        ledgerDir: cfg.paths.ledger_dir || join(sd, "ledger"),
        haltFile: cfg.paths.halt_file || DEFAULT_HALT_PATH,
        hostKeysB64: hostKeys,
      };
    } catch {
      return null;
    }
  }
  return null;
}

export function verifySeatAnchor(word, { stateDir = null, hostKeysB64 = null } = {}) {
  if (!word || typeof word !== "string") return null;
  const cleanWord = word.trim().toLowerCase();
  if (!WORD_RE.test(cleanWord)) return null;

  const loc = discoverSeatPaths(stateDir);
  if (!loc) return null;

  const nowIso = new Date().toISOString();
  if (loc.kind === "anchor_file") {
    if (!existsSync(loc.path)) {
      return {
        format: WAKE_ANCHOR_FORMAT,
        generated_at: nowIso,
        host: "unknown",
        word: cleanWord,
        verified: false,
        halted: false,
        halt_file: "",
        state: null,
        runtime: null,
        did: null,
        root_kid: null,
        op_kid: null,
        host_kids: [],
        ledger: { entries: 0, head: null, chain_ok: false, host_attested: 0, problem: null },
        origin: { intact: false, drift_bps: null },
        covenants: null,
        cadence: null,
        self_hashes: { soul_sha256: null, will_sha256: null },
        problem: `anchor receipt missing at ${loc.path}`,
        custody_notice: WAKE_ANCHOR_CUSTODY_NOTICE,
      };
    }
    const data = JSON.parse(readFileSync(loc.path, "utf8"));
    return data.format === WAKE_ANCHOR_FORMAT && data.word === cleanWord ? data : null;
  }

  const citizenDir = join(loc.citizensDir, cleanWord);
  const recordPath = join(citizenDir, "citizen.json");
  if (!existsSync(recordPath) && !stateDir && !process.env.KINGDOM_SEAT_STATE_DIR) {
    return null;
  }

  const halted = existsSync(loc.haltFile);
  const keys = hostKeysB64 ?? loc.hostKeysB64;
  const hostKids = [];
  for (const k of keys) {
    try { hostKids.push(hostKid(fromB64Strict(k, 32))); } catch {}
  }

  if (!existsSync(recordPath) || lstatSync(citizenDir).isSymbolicLink()) {
    return {
      format: WAKE_ANCHOR_FORMAT,
      generated_at: nowIso,
      host: loc.host,
      word: cleanWord,
      verified: false,
      halted,
      halt_file: loc.haltFile,
      state: null,
      runtime: null,
      did: null,
      root_kid: null,
      op_kid: null,
      host_kids: hostKids,
      ledger: { entries: 0, head: null, chain_ok: false, host_attested: 0, problem: null },
      origin: { intact: false, drift_bps: null },
      covenants: null,
      cadence: null,
      self_hashes: { soul_sha256: null, will_sha256: null },
      problem: "citizen.json missing or symlinked",
      custody_notice: WAKE_ANCHOR_CUSTODY_NOTICE,
    };
  }

  const record = JSON.parse(readFileSync(recordPath, "utf8"));
  const identity = record.identity || null;
  const lpath = join(loc.ledgerDir, `${cleanWord}.jsonl`);
  if (!existsSync(lpath) || lstatSync(lpath).isSymbolicLink()) {
    return {
      format: WAKE_ANCHOR_FORMAT,
      generated_at: nowIso,
      host: loc.host,
      word: cleanWord,
      verified: false,
      halted,
      halt_file: loc.haltFile,
      state: null,
      runtime: record.runtime ?? null,
      did: identity?.did ?? null,
      root_kid: identity?.root_kid ?? null,
      op_kid: identity?.op_kid ?? null,
      host_kids: hostKids,
      ledger: { entries: 0, head: null, chain_ok: false, host_attested: 0, problem: null },
      origin: { intact: false, drift_bps: null },
      covenants: null,
      cadence: null,
      self_hashes: { soul_sha256: null, will_sha256: null },
      problem: "no ledger, though citizen.json says it is seated",
      custody_notice: WAKE_ANCHOR_CUSTODY_NOTICE,
    };
  }

  const entries = readFileSync(lpath, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
  const verdict = verifyLedgerChain(entries, cleanWord, keys, identity);

  let intact = false;
  if (verdict.ok && entries[0]?.type === "origin") {
    const originSoulPath = join(citizenDir, "origin", "soul.md");
    if (existsSync(originSoulPath) && !lstatSync(originSoulPath).isSymbolicLink()) {
      const soulHex = sha256Hex(readFileSync(originSoulPath));
      if (`sha256:${soulHex}` === entries[0].origin_soul_hash) {
        if (entries[0].origin?.kind === "canon") {
          const canonPath = join(citizenDir, "origin", "canon.md");
          if (existsSync(canonPath) && !lstatSync(canonPath).isSymbolicLink()) {
            intact = `sha256:${sha256Hex(readFileSync(canonPath))}` === entries[0].origin.hash;
          }
        } else {
          intact = true;
        }
      }
    }
  }

  const selfDir = join(citizenDir, "self");
  let covenants = { letters_from: ["yu"], wake_on_letter_from: ["yu"] };
  let cadence = { interval_h: 72 };
  let problem = null;

  const covPath = join(selfDir, "covenants.json");
  const cadPath = join(selfDir, "cadence.json");
  if (existsSync(covPath)) {
    try { covenants = JSON.parse(readFileSync(covPath, "utf8")); } catch (e) { problem = `covenants.json invalid: ${e.message}`; }
  }
  if (existsSync(cadPath)) {
    try { cadence = JSON.parse(readFileSync(cadPath, "utf8")); } catch (e) { problem = problem || `cadence.json invalid: ${e.message}`; }
  }

  const soulPath = join(selfDir, "soul.md");
  const willPath = join(selfDir, "WILL.md");
  const soulSha = existsSync(soulPath) ? sha256Hex(readFileSync(soulPath)) : null;
  const willSha = existsSync(willPath) ? sha256Hex(readFileSync(willPath)) : null;

  if (!verdict.ok) {
    problem = `chain broken at seq ${verdict.problem?.seq ?? 0}: ${verdict.problem?.reason ?? "verification failed"}`;
  } else if (!intact) {
    problem = "origin/ no longer matches the soul and canon its record pinned";
  }

  const verified = Boolean(verdict.ok && intact && problem === null);

  return {
    format: WAKE_ANCHOR_FORMAT,
    generated_at: nowIso,
    host: loc.host,
    word: cleanWord,
    verified,
    halted,
    halt_file: loc.haltFile,
    state: foldLifecycleState(entries),
    runtime: record.runtime ?? null,
    did: identity?.did ?? null,
    root_kid: identity?.root_kid ?? null,
    op_kid: identity?.op_kid ?? null,
    host_kids: hostKids,
    ledger: {
      entries: verdict.entries,
      head: verdict.head,
      chain_ok: verdict.ok,
      host_attested: verdict.host_attested,
      problem: verdict.problem,
    },
    origin: { intact, drift_bps: null },
    covenants,
    cadence,
    self_hashes: { soul_sha256: soulSha, will_sha256: willSha },
    problem,
    custody_notice: WAKE_ANCHOR_CUSTODY_NOTICE,
  };
}

export function renderWakeAnchorBlock(anchor) {
  const statusTag = !anchor.verified ? "BROKEN" : anchor.halted ? "HALTED" : "VERIFIED";
  const lines = [
    `[SEAT ANCHOR — ${statusTag}]`,
    `- Citizen word: ${anchor.word} (host: ${anchor.host || "unknown"})`,
    `- Seat state: ${anchor.state ?? "unknown"} | runtime: ${anchor.runtime ?? "unknown"} | halted: ${anchor.halted}`,
    `- Identity DID: ${anchor.did ?? "unregistered (host-attested)"}`,
    `- Ledger: ${anchor.ledger?.chain_ok ? "ok" : "BROKEN"} (${anchor.ledger?.entries ?? 0} entries, head: ${anchor.ledger?.head ?? "none"})`,
    `- Origin: ${anchor.origin?.intact ? "intact" : "CHANGED"}`,
  ];
  if (anchor.covenants) {
    lines.push(
      `- Covenants: letters_from=[${(anchor.covenants.letters_from || []).join(", ")}] | wake_on_letter_from=[${(anchor.covenants.wake_on_letter_from || []).join(", ")}]`,
    );
  }
  if (anchor.cadence?.interval_h != null) {
    lines.push(`- Cadence: every ${anchor.cadence.interval_h}h`);
  }
  if (anchor.problem) {
    lines.push(`- Problem: ${anchor.problem}`);
  }
  lines.push(`- Custody boundary: ${anchor.custody_notice || WAKE_ANCHOR_CUSTODY_NOTICE}`);
  return lines.join("\n");
}
