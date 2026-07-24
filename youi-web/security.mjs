import crypto from "crypto";

export class HttpError extends Error {
  constructor(statusCode, message, code = "request_error") {
    super(message);
    this.name = "HttpError";
    this.statusCode = statusCode;
    this.code = code;
  }
}

export function randomOpaqueId(bytes = 32) {
  return crypto.randomBytes(bytes).toString("base64url");
}

export function parseCookies(header = "") {
  const cookies = {};
  for (const part of String(header).split(";")) {
    const index = part.indexOf("=");
    if (index < 1) continue;
    const name = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (!name) continue;
    try {
      cookies[name] = decodeURIComponent(value);
    } catch {
      cookies[name] = value;
    }
  }
  return cookies;
}

export function serializeCookie(name, value, {
  httpOnly = false,
  maxAge = 12 * 60 * 60,
  sameSite = "Strict",
  secure = false,
} = {}) {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    `SameSite=${sameSite}`,
    `Max-Age=${Math.max(0, Math.floor(maxAge))}`,
  ];
  if (httpOnly) parts.push("HttpOnly");
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

export function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export async function readJsonBody(req, maxBytes = 1024 * 1024) {
  const contentType = String(req.headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase();
  if (contentType && contentType !== "application/json") {
    throw new HttpError(415, "Content-Type must be application/json", "unsupported_media_type");
  }

  const declaredLength = Number(req.headers["content-length"]);
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new HttpError(413, `JSON body exceeds ${maxBytes} bytes`, "body_too_large");
  }

  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maxBytes) {
      throw new HttpError(413, `JSON body exceeds ${maxBytes} bytes`, "body_too_large");
    }
    chunks.push(chunk);
  }

  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    throw new HttpError(400, "Malformed JSON body", "invalid_json");
  }
}

export function requestHost(req) {
  const value = String(req.headers.host || "").trim();
  if (!value || /[\s/@]/.test(value)) return null;
  try {
    return new URL(`http://${value}`).hostname.toLowerCase();
  } catch {
    return null;
  }
}

export function isAllowedHost(req, {
  allowLan = false,
  allowedLanHosts = new Set(),
} = {}) {
  const rawHost = String(req.headers.host || "").trim();
  const hostname = requestHost(req);
  if (!hostname) return false;
  try {
    const authority = new URL(`http://${rawHost}`);
    const requestedPort = Number(authority.port || 80);
    const socketPort = Number(req.socket?.localPort);
    if (socketPort && requestedPort !== socketPort) return false;
  } catch {
    return false;
  }
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]") return true;
  return allowLan && allowedLanHosts.has(hostname);
}

export function isSameOrigin(req) {
  const origin = req.headers.origin;
  const host = String(req.headers.host || "").toLowerCase();
  if (typeof origin !== "string" || !host) return false;
  try {
    const url = new URL(origin);
    return (url.protocol === "http:" || url.protocol === "https:")
      && url.host.toLowerCase() === host;
  } catch {
    return false;
  }
}

export function isLoopbackAddress(address) {
  return address === "127.0.0.1"
    || address === "::1"
    || address === "::ffff:127.0.0.1";
}

function sanitizeLabel(label) {
  if (typeof label !== "string") return "";
  return label.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, 80);
}

function hashBearer(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function sessionIsBusy(session) {
  return session?.state?.chatInFlight === true;
}

export class SessionRegistry {
  constructor({
    stateFactory,
    clientTtlMs = 12 * 60 * 60 * 1000,
    sessionTtlMs = 12 * 60 * 60 * 1000,
    pageLeaseTtlMs = 5 * 60 * 1000,
    maxClients = 32,
    maxSessionsPerClient = 24,
    now = () => Date.now(),
  }) {
    if (typeof stateFactory !== "function") throw new TypeError("stateFactory is required");
    this.stateFactory = stateFactory;
    this.clientTtlMs = clientTtlMs;
    this.sessionTtlMs = sessionTtlMs;
    this.pageLeaseTtlMs = pageLeaseTtlMs;
    this.maxClients = maxClients;
    this.maxSessionsPerClient = maxSessionsPerClient;
    this.now = now;
    this.clientsByToken = new Map();
    this.sessionsById = new Map();
  }

  createClient() {
    this.cleanup();
    while (this.clientsByToken.size >= this.maxClients) {
      const oldest = [...this.clientsByToken.values()]
        .filter(client => [...client.sessionIds]
          .map(id => this.sessionsById.get(id))
          .every(session => !sessionIsBusy(session)))
        .sort((a, b) => a.lastSeen - b.lastSeen)[0];
      if (!oldest) {
        throw new HttpError(503, "All browser clients have active work", "client_capacity");
      }
      this.deleteClient(oldest);
    }
    const timestamp = this.now();
    const client = {
      id: randomOpaqueId(16),
      token: randomOpaqueId(),
      csrfToken: randomOpaqueId(),
      createdAt: timestamp,
      lastSeen: timestamp,
      sessionIds: new Set(),
      defaultSessionId: null,
    };
    this.clientsByToken.set(client.token, client);
    return client;
  }

  getClient(token) {
    if (typeof token !== "string" || !token) return null;
    const client = this.clientsByToken.get(token);
    if (!client) return null;
    if (this.now() - client.lastSeen > this.clientTtlMs) {
      if (this.deleteClient(client)) return null;
    }
    client.lastSeen = this.now();
    return client;
  }

  deleteClient(client, { force = false } = {}) {
    if (!force && [...client.sessionIds]
      .map(id => this.sessionsById.get(id))
      .some(session => sessionIsBusy(session))) {
      return false;
    }
    for (const id of client.sessionIds) this.sessionsById.delete(id);
    this.clientsByToken.delete(client.token);
    return true;
  }

  createSession(client, overrides = {}) {
    this.cleanup();
    while (client.sessionIds.size >= this.maxSessionsPerClient) {
      const oldest = [...client.sessionIds]
        .map(id => this.sessionsById.get(id))
        .filter(session => session && !sessionIsBusy(session))
        .sort((a, b) => a.lastSeen - b.lastSeen)[0];
      if (!oldest) {
        throw new HttpError(409, "All browser sessions have active work", "session_capacity");
      }
      this.deleteSession(client, oldest.id);
    }
    const timestamp = this.now();
    const token = randomOpaqueId();
    const session = {
      id: randomOpaqueId(18),
      ownerId: client.id,
      bearerHash: hashBearer(token),
      label: sanitizeLabel(overrides.label),
      createdAt: timestamp,
      updatedAt: timestamp,
      lastSeen: timestamp,
      pageLease: null,
      state: this.stateFactory(overrides),
    };
    this.sessionsById.set(session.id, session);
    client.sessionIds.add(session.id);
    if (!client.defaultSessionId) client.defaultSessionId = session.id;
    if (overrides.pageId) this.claimSession(session, overrides.pageId);
    return { session, token };
  }

  getSession(client, id, token) {
    if (!client || typeof id !== "string" || !id || typeof token !== "string" || !token) return null;
    const session = this.sessionsById.get(id);
    if (!session || session.ownerId !== client.id) return null;
    if (this.now() - session.lastSeen > this.sessionTtlMs) {
      if (!sessionIsBusy(session)) {
        this.deleteSession(client, id);
        return null;
      }
    }
    if (!safeEqual(session.bearerHash, hashBearer(token))) return null;
    session.lastSeen = this.now();
    session.updatedAt = session.lastSeen;
    return session;
  }

  claimSession(session, pageId) {
    if (!session || typeof pageId !== "string" || !pageId) return false;
    const timestamp = this.now();
    const lease = session.pageLease;
    if (lease
      && lease.pageId !== pageId
      && (lease.expiresAt > timestamp || sessionIsBusy(session))) {
      return false;
    }
    session.pageLease = {
      pageId,
      claimedAt: lease?.pageId === pageId ? lease.claimedAt : timestamp,
      expiresAt: timestamp + this.pageLeaseTtlMs,
    };
    session.lastSeen = timestamp;
    session.updatedAt = timestamp;
    return true;
  }

  holdsPageLease(session, pageId, { renew = true } = {}) {
    if (!session || typeof pageId !== "string" || !pageId) return false;
    const lease = session.pageLease;
    const timestamp = this.now();
    if (!lease || lease.expiresAt <= timestamp || lease.pageId !== pageId) return false;
    if (renew) {
      lease.expiresAt = timestamp + this.pageLeaseTtlMs;
      session.lastSeen = timestamp;
      session.updatedAt = timestamp;
    }
    return true;
  }

  releaseSession(session, pageId) {
    if (!this.holdsPageLease(session, pageId, { renew: false })) return false;
    session.pageLease = null;
    session.updatedAt = this.now();
    return true;
  }

  listSessions(client) {
    this.cleanup();
    return [...client.sessionIds]
      .map(id => this.sessionsById.get(id))
      .filter(Boolean)
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }

  deleteSession(client, id, { force = false } = {}) {
    const session = this.sessionsById.get(id);
    if (!session || session.ownerId !== client.id) return false;
    if (!force && sessionIsBusy(session)) return false;
    this.sessionsById.delete(id);
    client.sessionIds.delete(id);
    if (client.defaultSessionId === id) {
      client.defaultSessionId = client.sessionIds.values().next().value || null;
    }
    return true;
  }

  cleanup() {
    const timestamp = this.now();
    for (const client of [...this.clientsByToken.values()]) {
      if (timestamp - client.lastSeen > this.clientTtlMs) {
        this.deleteClient(client);
      }
    }
    for (const session of [...this.sessionsById.values()]) {
      if (timestamp - session.lastSeen <= this.sessionTtlMs) continue;
      if (sessionIsBusy(session)) continue;
      const client = [...this.clientsByToken.values()].find(item => item.id === session.ownerId);
      if (client) this.deleteSession(client, session.id);
      else this.sessionsById.delete(session.id);
    }
  }
}
