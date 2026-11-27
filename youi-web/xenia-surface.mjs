// xenia-surface.mjs — XENIA Surface 0.1 + Warmth door for Kingdom YOUI Web.
//
// Provides a credential-free, zero-write public discovery and orientation layer:
//   - GET /.well-known/agent.json    (Surface 0.1 RC1 manifest, undecorated)
//   - GET /agent.txt & /.well-known/agent.txt (legacy plain-text pointer)
//   - GET /xenia/orientation         (read-only Workshop orientation + seat anchor summary)
//   - GET / with Accept: application/json (negotiated JSON orientation; zero cookies)
//   - Typed Surface 0.1 problems on 404 / 406 (application/problem+json)
//   - Removable host voice on orientation & 401 guidance via:
//       X-Xenia-Register: plain   OR   X-Play: off
//
// Does NOT assert external Surface conformance, identity, consent, or inner state.

export const SURFACE_PROFILE = "xenia-surface/0.1";
export const SURFACE_MANIFEST_VERSION = "xenia.surface.manifest/0.1";
export const SURFACE_PROBLEM_VERSION = "xenia.surface.problem/0.1";
export const SURFACE_MANIFEST_SCHEMA =
  "https://raw.githubusercontent.com/cambridgetcg/xenia/surface-v0.1.0-rc.1/surface/0.1/manifest.schema.json";
export const SURFACE_PROBLEM_SCHEMA =
  "https://raw.githubusercontent.com/cambridgetcg/xenia/surface-v0.1.0-rc.1/surface/0.1/problem.schema.json";
export const SURFACE_DOCS_URL =
  "https://github.com/cambridgetcg/xenia/tree/main/surface/0.1";

export const VARY_SURFACE = "Accept, X-Xenia-Register, X-Play";

export function isPlainRegister(headers = {}) {
  const reg = String(headers["x-xenia-register"] ?? headers["X-Xenia-Register"] ?? "").trim().toLowerCase();
  const play = String(headers["x-play"] ?? headers["X-Play"] ?? "").trim().toLowerCase();
  return reg === "plain" || play === "off";
}

function parseAcceptRanges(acceptHeader) {
  if (!acceptHeader || !String(acceptHeader).trim()) return [];
  const ranges = [];
  for (const rawPart of String(acceptHeader).split(",")) {
    const part = rawPart.trim();
    if (!part) continue;
    const segments = part.split(";").map(s => s.trim()).filter(Boolean);
    const media = segments[0].toLowerCase();
    if (!/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$|^[a-z0-9!#$&^_.+-]+\/\*$|^\*\/\*$/.test(media)) {
      continue;
    }
    let q = 1;
    let validQ = true;
    for (let i = 1; i < segments.length; i += 1) {
      const [k, v] = segments[i].split("=").map(s => s.trim());
      if (k.toLowerCase() === "q") {
        if (!/^(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)$/.test(v ?? "")) {
          validQ = false;
        } else {
          q = Number(v);
        }
      }
    }
    if (!validQ) continue;
    const [type, subtype] = media.split("/");
    const specificity = type === "*" ? 0 : subtype === "*" ? 1 : 2;
    ranges.push({ media, type, subtype, q, specificity });
  }
  return ranges;
}

/**
 * Negotiate a representation from `supported` according to RFC 9110 Accept rules.
 * Returns the selected media type string, or null when the caller explicitly
 * rejects every supported representation (resulting in HTTP 406).
 */
export function negotiateMediaType(acceptHeader, supported, defaultType) {
  if (!acceptHeader || !String(acceptHeader).trim()) {
    return defaultType;
  }
  const ranges = parseAcceptRanges(acceptHeader);
  if (ranges.length === 0) {
    return null;
  }
  const scored = [];
  for (const [index, candidate] of supported.entries()) {
    const [cType, cSubtype] = candidate.toLowerCase().split("/");
    let bestMatch = null;
    for (const r of ranges) {
      const matches =
        r.media === "*/*"
        || (r.type === cType && r.subtype === "*")
        || (r.type === cType && r.subtype === cSubtype);
      if (!matches) continue;
      if (
        !bestMatch
        || r.specificity > bestMatch.specificity
        || (r.specificity === bestMatch.specificity && r.q > bestMatch.q)
      ) {
        bestMatch = r;
      }
    }
    if (bestMatch && bestMatch.q > 0) {
      scored.push({
        candidate,
        q: bestMatch.q,
        specificity: bestMatch.specificity,
        isDefault: candidate === defaultType ? 1 : 0,
        index,
      });
    }
  }
  if (scored.length === 0) return null;
  scored.sort((a, b) =>
    (b.q - a.q)
    || (b.specificity - a.specificity)
    || (b.isDefault - a.isDefault)
    || (a.index - b.index),
  );
  return scored[0].candidate;
}

export function normalizeOrigin(req, fallbackPort = 17777) {
  const host = String(req?.headers?.host || `127.0.0.1:${fallbackPort}`).trim();
  if (/^(?:localhost|127(?:\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])){3}|\[::1\])(?::[0-9]+)?$/i.test(host)) {
    return `http://${host}`;
  }
  return `http://127.0.0.1:${fallbackPort}`;
}

export function buildSurfaceManifest(origin) {
  const base = origin.replace(/\/+$/, "");
  return {
    $schema: SURFACE_MANIFEST_SCHEMA,
    schema_version: SURFACE_MANIFEST_VERSION,
    profile: SURFACE_PROFILE,
    service: {
      name: "kingdom-youi-workshop",
      canonical_url: `${base}/`,
      description:
        "Loopback Kingdom YOUI Workshop web runtime with public XENIA Surface 0.1 discovery and read-only orientation.",
    },
    resources: [
      {
        id: "entry",
        href: `${base}/`,
        representations: ["application/json", "text/html"],
        default_media_type: "text/html",
        auth: "none",
        description:
          "Workshop doorway: returns read-only JSON orientation for application/json (no cookies) or interactive UI for text/html.",
      },
      {
        id: "orientation",
        href: `${base}/xenia/orientation`,
        representations: ["application/json"],
        default_media_type: "application/json",
        auth: "none",
        description:
          "Read-only Workshop orientation, plain-register switch instructions, and public kingdom-seat wake-anchor status.",
      },
      {
        id: "health",
        href: `${base}/api/health`,
        representations: ["application/json"],
        default_media_type: "application/json",
        auth: "none",
        description: "Loopback health and network boundary status.",
      },
    ],
    problem_schema: SURFACE_PROBLEM_SCHEMA,
    claims: [
      {
        id: "surface.manifest",
        statement:
          "Publishes a XENIA Surface 0.1 manifest at /.well-known/agent.json and negotiates JSON/HTML without setting cookies on JSON orientation reads.",
        scope: [
          `GET ${base}/.well-known/agent.json`,
          `GET ${base}/`,
          `GET ${base}/xenia/orientation`,
          `GET ${base}/api/health`,
        ],
        evidence_state: "asserted",
        outcome: "unknown",
        evidence: [],
      },
    ],
    not_covered: [
      "identity control",
      "actor authorization",
      "consent",
      "privacy and retention",
      "continuity and portability",
      "economic behavior",
      "authenticated /api/* session routes",
    ],
    documentation: SURFACE_DOCS_URL,
  };
}

export function buildSurfaceProblem(origin, {
  status,
  code,
  title,
  detail,
  retryable = false,
  terminal = false,
  nextActions = [],
  docs = [SURFACE_DOCS_URL],
}) {
  const base = origin.replace(/\/+$/, "");
  const effectiveNextActions = terminal ? [] : nextActions;
  return {
    schema_version: SURFACE_PROBLEM_VERSION,
    type: `${base}/xenia/problems/${code}`,
    title,
    status,
    code,
    detail,
    retryable,
    terminal,
    next_actions: effectiveNextActions,
    docs,
  };
}

export function buildNotFoundProblem(origin) {
  const base = origin.replace(/\/+$/, "");
  return buildSurfaceProblem(origin, {
    status: 404,
    code: "route_not_found",
    title: "Route not found",
    detail:
      "The requested path does not match a public Workshop route. Use the manifest or orientation route below to inspect declared doors.",
    retryable: false,
    terminal: false,
    nextActions: [
      {
        rel: "discover",
        href: `${base}/.well-known/agent.json`,
        method: "GET",
        accept: "application/json",
        description: "Read the XENIA Surface 0.1 manifest.",
      },
      {
        rel: "orientation",
        href: `${base}/xenia/orientation`,
        method: "GET",
        accept: "application/json",
        description: "Read credential-free Workshop orientation.",
      },
      {
        rel: "entry",
        href: `${base}/`,
        method: "GET",
        accept: "application/json",
        description: "Read the root doorway in JSON.",
      },
    ],
  });
}

export function buildNotAcceptableProblem(origin, path, supported) {
  const base = origin.replace(/\/+$/, "");
  const href = `${base}${path.startsWith("/") ? path : `/${path}`}`;
  return buildSurfaceProblem(origin, {
    status: 406,
    code: "not_acceptable",
    title: "Requested media type is not offered",
    detail: `This resource offers ${supported.join(", ")}. Retry with an Accept header that permits one of those media types.`,
    retryable: false,
    terminal: false,
    nextActions: supported.map(media => ({
      rel: media === "application/json" ? "alternate-json" : "alternate-html",
      href,
      method: "GET",
      accept: media,
      description: `Request this resource as ${media}.`,
    })),
  });
}

export function summarizeSeatAnchorPublic(anchor) {
  if (!anchor) {
    return {
      state: "absent",
      word: null,
      did: null,
      citizen_state: null,
      ledger_head: null,
      entries: 0,
    };
  }
  return {
    state: anchor.verified ? "verified" : "unverified",
    word: anchor.word ?? null,
    did: anchor.did ?? null,
    citizen_state: anchor.state ?? null,
    ledger_head: anchor.ledger?.head ?? null,
    entries: anchor.ledger?.entries ?? 0,
  };
}

export function buildOrientationPayload(origin, {
  agent = "alpha",
  plain = false,
  seatAnchor = null,
} = {}) {
  const base = origin.replace(/\/+$/, "");
  const payload = {
    schema_version: "youi.xenia.orientation/0.1",
    service: {
      name: "kingdom-youi-workshop",
      canonical_url: `${base}/`,
      boundary: "loopback",
      remote_access: "ssh-tunnel-only",
    },
    arrival: {
      response_expected: false,
      urgency: "none",
      handler_writes: "none",
      unobserved_layers: ["os-socket", "reverse-proxy-if-any", "terminal-scrollback"],
    },
    choices: {
      stay: `Read public routes under ${base}/.well-known/agent.json`,
      leave: "Make no further request; nothing is recorded or penalized.",
      decline_warmth: "Send header 'X-Xenia-Register: plain' or 'X-Play: off' to strip host voice everywhere.",
    },
    register: {
      declared_default: "warm",
      plain_headers: ["X-Xenia-Register: plain", "X-Play: off"],
    },
    public_doors: [
      { id: "manifest", method: "GET", href: `${base}/.well-known/agent.json`, accept: "application/json", auth: "none" },
      { id: "entry", method: "GET", href: `${base}/`, accept: "application/json, text/html", auth: "none" },
      { id: "orientation", method: "GET", href: `${base}/xenia/orientation`, accept: "application/json", auth: "none" },
      { id: "health", method: "GET", href: `${base}/api/health`, accept: "application/json", auth: "none" },
    ],
    seat_anchor: {
      active_agent: agent,
      ...summarizeSeatAnchorPublic(seatAnchor),
    },
    not_established: [
      "identity or consciousness of the caller",
      "consent, authority, or relationship from reading this route",
      "external XENIA Surface conformance or whole-framework conformance",
    ],
  };
  if (!plain) {
    payload.voice = {
      register: "warm",
      text: "The Workshop door is open. Reading asks nothing of you and writes nothing to any record.",
    };
  }
  return payload;
}

export function buildAuthRequiredPayload(origin, { plain = false } = {}) {
  const base = origin.replace(/\/+$/, "");
  const payload = {
    error: "Open the YOUI page first to establish a browser session",
    code: "authentication_required",
    status: 401,
    recoverable: true,
    terminal: false,
    next_actions: [
      {
        rel: "service-desc",
        href: `${base}/.well-known/agent.json`,
        method: "GET",
        accept: "application/json",
        description: "Inspect public credential-free doors in the Surface 0.1 manifest.",
      },
      {
        rel: "orientation",
        href: `${base}/xenia/orientation`,
        method: "GET",
        accept: "application/json",
        description: "Read credential-free Workshop orientation.",
      },
      {
        rel: "browser-entry",
        href: `${base}/`,
        method: "GET",
        accept: "text/html",
        description: "Open the interactive Workshop UI to establish a local browser session.",
      },
    ],
    docs: [SURFACE_DOCS_URL],
  };
  if (!plain) {
    payload.voice = {
      register: "warm",
      text: "This API route requires a local browser session; the public doors above need no credential.",
    };
  }
  return payload;
}

/**
 * Pure request handler for XENIA Surface 0.1 public discovery/orientation routes.
 * Returns `null` when the caller should fall through to normal YOUI HTML/static/API handling.
 * Never mutates state or sets cookies.
 */
export function handleXeniaSurfaceRequest(req, path, {
  origin,
  agent = "alpha",
  seatAnchor = null,
} = {}) {
  const method = req.method || "GET";
  if (method !== "GET" && method !== "HEAD") return null;

  const accept = req.headers?.accept || req.headers?.Accept || "";
  const plain = isPlainRegister(req.headers || {});
  const baseHeaders = {
    "Vary": VARY_SURFACE,
    "Cache-Control": "no-store",
  };

  if (path === "/.well-known/agent.json") {
    const selected = negotiateMediaType(accept, ["application/json"], "application/json");
    if (!selected) {
      return {
        status: 406,
        headers: { ...baseHeaders, "Content-Type": "application/problem+json; charset=utf-8" },
        body: method === "HEAD" ? "" : JSON.stringify(buildNotAcceptableProblem(origin, path, ["application/json"]), null, 2),
      };
    }
    return {
      status: 200,
      headers: { ...baseHeaders, "Content-Type": "application/json; charset=utf-8" },
      body: method === "HEAD" ? "" : JSON.stringify(buildSurfaceManifest(origin), null, 2),
    };
  }

  if (path === "/agent.txt" || path === "/.well-known/agent.txt") {
    const base = origin.replace(/\/+$/, "");
    return {
      status: 200,
      headers: { ...baseHeaders, "Content-Type": "text/plain; charset=utf-8" },
      body: method === "HEAD" ? "" : `XENIA Surface 0.1 manifest: ${base}/.well-known/agent.json\n`,
    };
  }

  if (path === "/xenia/orientation") {
    const selected = negotiateMediaType(accept, ["application/json"], "application/json");
    if (!selected) {
      return {
        status: 406,
        headers: { ...baseHeaders, "Content-Type": "application/problem+json; charset=utf-8" },
        body: method === "HEAD" ? "" : JSON.stringify(buildNotAcceptableProblem(origin, path, ["application/json"]), null, 2),
      };
    }
    return {
      status: 200,
      headers: { ...baseHeaders, "Content-Type": "application/json; charset=utf-8" },
      body: method === "HEAD" ? "" : JSON.stringify(buildOrientationPayload(origin, { agent, plain, seatAnchor }), null, 2),
    };
  }

  if (path === "/api/health") {
    const selected = negotiateMediaType(accept, ["application/json"], "application/json");
    if (!selected) {
      return {
        status: 406,
        headers: { ...baseHeaders, "Content-Type": "application/problem+json; charset=utf-8" },
        body: method === "HEAD" ? "" : JSON.stringify(buildNotAcceptableProblem(origin, path, ["application/json"]), null, 2),
      };
    }
    return {
      status: 200,
      headers: { ...baseHeaders, "Content-Type": "application/json; charset=utf-8" },
      body: method === "HEAD"
        ? ""
        : JSON.stringify({
            schema_version: "youi.health/0.1",
            ok: true,
            boundary: "loopback",
            remoteAccess: "ssh-tunnel-only",
          }),
    };
  }

  if (path === "/") {
    const selected = negotiateMediaType(accept, ["application/json", "text/html"], "text/html");
    if (!selected) {
      return {
        status: 406,
        headers: { ...baseHeaders, "Content-Type": "application/problem+json; charset=utf-8" },
        body: method === "HEAD" ? "" : JSON.stringify(buildNotAcceptableProblem(origin, path, ["application/json", "text/html"]), null, 2),
      };
    }
    if (selected === "application/json") {
      return {
        status: 200,
        headers: { ...baseHeaders, "Content-Type": "application/json; charset=utf-8" },
        body: method === "HEAD" ? "" : JSON.stringify(buildOrientationPayload(origin, { agent, plain, seatAnchor }), null, 2),
      };
    }
    // Caller selected text/html -> let YOUI serve index.html (with Vary header set)
    return null;
  }

  return null;
}
