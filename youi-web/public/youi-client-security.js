(() => {
  "use strict";

  const SESSION_KEY = "kingdom.youi.browser-session.v1";
  const nativeFetch = window.fetch.bind(window);
  const pageId = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${crypto.getRandomValues(new Uint32Array(4)).join("-")}`;
  let sessionId = null;
  let sessionToken = null;

  function cookie(name) {
    const prefix = `${name}=`;
    for (const part of document.cookie.split(";")) {
      const value = part.trim();
      if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
    }
    return "";
  }

  function storedSession() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
      if (typeof parsed?.id !== "string" || typeof parsed?.token !== "string") return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function storeSession(value) {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    } catch {}
  }

  function apiHeaders(headers = {}, { includeSession = true, unsafe = false } = {}) {
    const result = new Headers(headers);
    result.set("X-YOUI-Page", pageId);
    if (includeSession && sessionId && sessionToken) {
      result.set("X-YOUI-Session", sessionId);
      result.set("X-YOUI-Session-Token", sessionToken);
    }
    if (unsafe) result.set("X-YOUI-CSRF", cookie("youi_csrf"));
    return result;
  }

  function storedHeaders(stored, { unsafe = false } = {}) {
    const headers = apiHeaders({}, { includeSession: false, unsafe });
    headers.set("X-YOUI-Session", stored.id);
    headers.set("X-YOUI-Session-Token", stored.token);
    return headers;
  }

  async function claimSession(stored) {
    const response = await nativeFetch(`/api/sessions/${encodeURIComponent(stored.id)}/claim`, {
      method: "POST",
      headers: storedHeaders(stored, { unsafe: true }),
      cache: "no-store",
    });
    return response.ok;
  }

  async function createSession() {
    const response = await nativeFetch("/api/sessions", {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }, {
        includeSession: false,
        unsafe: true,
      }),
      body: JSON.stringify({ label: `tab ${new Date().toLocaleTimeString()}` }),
    });
    if (!response.ok) throw new Error(`YOUI session bootstrap failed (${response.status})`);
    const data = await response.json();
    if (typeof data.session?.id !== "string" || typeof data.sessionToken !== "string") {
      throw new Error("YOUI session bootstrap returned an invalid credential");
    }
    return { id: data.session.id, token: data.sessionToken };
  }

  const ready = (async () => {
    const stored = storedSession();
    if (stored && await claimSession(stored)) {
      sessionId = stored.id;
      sessionToken = stored.token;
      return sessionId;
    }
    const created = await createSession();
    sessionId = created.id;
    sessionToken = created.token;
    storeSession(created);
    return sessionId;
  })();

  window.fetch = async function youiFetch(input, init = {}) {
    const requestUrl = input instanceof Request ? input.url : input;
    const url = new URL(requestUrl, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) {
      return nativeFetch(input, init);
    }

    await ready;
    const method = String(init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const unsafe = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
    const headers = apiHeaders(
      init.headers || (input instanceof Request ? input.headers : {}),
      { includeSession: true, unsafe },
    );
    return nativeFetch(input, { ...init, headers });
  };

  window.YOUI = Object.freeze({
    ready,
    get sessionId() { return sessionId; },
    newSession: async () => {
      const created = await createSession();
      sessionId = created.id;
      sessionToken = created.token;
      storeSession(created);
      return sessionId;
    },
  });

  const renewLease = () => {
    if (!sessionId || !sessionToken) return;
    void nativeFetch(`/api/sessions/${encodeURIComponent(sessionId)}/claim`, {
      method: "POST",
      headers: apiHeaders({}, { unsafe: true }),
      cache: "no-store",
    }).catch(() => {});
  };
  setInterval(renewLease, 60_000);

  addEventListener("pagehide", () => {
    if (!sessionId || !sessionToken) return;
    void nativeFetch(`/api/sessions/${encodeURIComponent(sessionId)}/release`, {
      method: "POST",
      headers: apiHeaders({}, { unsafe: true }),
      keepalive: true,
    }).catch(() => {});
  });
})();
