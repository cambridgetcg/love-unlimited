// minds.mjs — the minds a YOUI session may call, and whether each one answers.
//
// docs/YOUI-MINDS-DESIGN.md §3.2 (slice M1). A mind is a provider and model
// that the agent being spoken to may bring in. Each entry names the
// capability that gates it, the privacy label its content would carry, and a
// reachability state:
//
//   reachable       a cheap probe of its endpoint answered and lists the model
//   unreachable     the probe failed, or the endpoint does not serve the model
//   not_granted     this session lacks the capability; nothing was probed
//   not_configured  a required setting is missing (neither an Ollama Cloud
//                   key nor a local Ollama to go through, no local models
//                   pulled); nothing was called
//   unchecked       not probed by design; the first real call proves it
//
// Probes are GET /api/tags (local Ollama) and GET /v1/models (vLLM), about
// 1 s each, and `claude --version` for Claude minds, which go through the
// installed Claude Code (docs/YOUI-CLAUDE-CODE-DESIGN.md §3.4); each is
// cached for about a minute. The registry never calls a model, never reads a
// credential and never contacts Ollama Cloud to find out.
//
// A model the local Ollama lists as remote (a ":cloud" or "-cloud" name, or
// remote_host / remote_model in /api/tags) is an Ollama Cloud mind, not a
// local one: its content leaves the device, so it needs models:ollama-cloud.
// A running local Ollama also counts as a way to reach the Ollama Cloud
// minds without a key (it forwards them with its own sign-in, which the
// registry does not check; the first call proves it).
//
// Claude minds are not_configured when Claude Code is missing, unreachable
// when its version is below what a model needs (every model needs YOUI's floor, 2.1.299) or
// cannot be read, and otherwise unchecked until a real answer.
// Only granted minds that are reachable or unchecked are offered.

import { readOllamaTags } from "./ollama-routes.mjs";

export const MIND_STATES = Object.freeze([
  "reachable", "unreachable", "not_granted", "not_configured", "unchecked",
]);
const OFFERED_STATES = new Set(["reachable", "unchecked"]);

export const MIND_PROBE_TIMEOUT_MS = 1_000;
export const MIND_CACHE_TTL_MS = 60_000;

function probeError(error, timeoutMs) {
  if (error?.name === "TimeoutError" || error?.name === "AbortError") {
    return `no answer within ${timeoutMs} ms`;
  }
  return error?.cause?.code || error?.message || "probe failed";
}

/**
 * @param {object} options
 * @param {string[]} options.claudeModels   Anthropic model ids
 * @param {string[]} options.vllmModels     vLLM model ids YOUI is configured for
 * @param {string[]} options.cloudModels    Ollama Cloud model ids
 * @param {(model: string) => boolean} options.isVllmModel  which served ids YOUI routes to vLLM
 * @param {string} options.localBaseUrl     OLLAMA_LOCAL_BASE_URL
 * @param {string} options.vllmBaseUrl      OLLAMA_VLLM_BASE_URL
 * @param {boolean} options.localEndpointIsLoopback
 * @param {() => boolean} options.cloudKeyConfigured  whether OLLAMA_API_KEY is set
 * @param {(provider: string, options?: {throughDaemon?: boolean}) => {route: string, destination: string|null}} options.computeRoute
 *   the server's privacy labels, so each label is spelt in one place
 * @param {string|null} [options.claudeCodeMissing]  why Claude Code cannot be
 *   run (no binary), or null when it can
 * @param {() => Promise<{ok: boolean, version: string|null, error: string|null}>} [options.readClaudeVersion]
 *   `claude --version`, parsed; never a model call
 * @param {(model: string) => string|null} [options.claudeMinVersion]
 * @param {(left: string, right: string) => number} [options.compareVersions]
 */
export function createMindsRegistry({
  claudeModels = [],
  vllmModels = [],
  cloudModels = [],
  isVllmModel = () => false,
  localBaseUrl,
  vllmBaseUrl,
  localEndpointIsLoopback = true,
  cloudKeyConfigured = () => false,
  computeRoute,
  claudeCodeMissing = null,
  readClaudeVersion = null,
  claudeMinVersion = () => null,
  compareVersions = (left, right) => String(left).localeCompare(String(right), undefined, { numeric: true }),
  probeTimeoutMs = MIND_PROBE_TIMEOUT_MS,
  cacheTtlMs = MIND_CACHE_TTL_MS,
  fetchImpl = globalThis.fetch,
  now = Date.now,
} = {}) {
  if (typeof computeRoute !== "function") throw new TypeError("computeRoute is required");

  // url -> { at, ok, models, error } once settled, or { pending } while a probe runs.
  const probes = new Map();
  // "provider:model" -> when that model last answered a real call in this process.
  const answers = new Map();

  async function runProbe(url, readModels) {
    const at = now();
    try {
      const response = await fetchImpl(url, {
        method: "GET",
        signal: AbortSignal.timeout(probeTimeoutMs),
      });
      if (!response.ok) return { at, ok: false, models: [], remote: [], error: `status ${response.status}` };
      const read = readModels(await response.json());
      const models = read.models.filter(model => typeof model === "string");
      return { at, ok: true, models, remote: read.remote || [], error: null };
    } catch (error) {
      return { at, ok: false, models: [], remote: [], error: probeError(error, probeTimeoutMs) };
    }
  }

  function probe(url, readModels) {
    const cached = probes.get(url);
    if (cached?.pending) return cached.pending;
    if (cached && now() - cached.at < cacheTtlMs) return Promise.resolve(cached);
    const pending = runProbe(url, readModels).then(result => {
      probes.set(url, result);
      return result;
    });
    probes.set(url, { pending });
    return pending;
  }

  // `claude --version`, shared and cached like the HTTP probes.
  function probeClaudeCode() {
    const key = "claude --version";
    const cached = probes.get(key);
    if (cached?.pending) return cached.pending;
    if (cached && now() - cached.at < cacheTtlMs) return Promise.resolve(cached);
    const at = now();
    const pending = Promise.resolve()
      .then(() => readClaudeVersion())
      .catch(error => ({ ok: false, version: null, error: error?.message || "failed" }))
      .then(result => {
        const settled = { at, ok: result.ok === true, version: result.version || null, error: result.error || null };
        probes.set(key, settled);
        return settled;
      });
    probes.set(key, { pending });
    return pending;
  }

  const probeLocal = () => probe(`${localBaseUrl}/api/tags`, readOllamaTags);
  const probeVllm = () => probe(
    `${vllmBaseUrl}/v1/models`,
    data => ({ models: (data?.data || []).map(model => model?.id) }),
  );

  function entry(provider, model, capability, state, note, checkedAt = null, routeOptions = undefined) {
    const { route, destination } = computeRoute(provider, routeOptions);
    return {
      id: model ? `${provider}:${model}` : provider,
      provider,
      model,
      capability,
      privacy: route,
      destination,
      state,
      offered: OFFERED_STATES.has(state),
      checked_at: checkedAt ? new Date(checkedAt).toISOString() : null,
      note,
    };
  }

  async function anthropicMinds(granted) {
    if (!granted) return claudeModels.map(model => entry("anthropic", model, "models:use", "not_granted", "needs models:use"));
    if (claudeCodeMissing) {
      return claudeModels.map(model => entry("anthropic", model, "models:use", "not_configured", claudeCodeMissing));
    }
    const check = readClaudeVersion ? await probeClaudeCode() : null;
    return claudeModels.map(model => {
      if (check && !check.ok) {
        return entry("anthropic", model, "models:use", "unreachable", `claude --version: ${check.error}`, check.at);
      }
      const minimum = claudeMinVersion(model);
      if (check && minimum && compareVersions(check.version, minimum) < 0) {
        return entry(
          "anthropic", model, "models:use", "unreachable",
          `Claude Code ${check.version} is older than ${minimum}, which ${model} needs through YOUI; update Claude Code`, check.at,
        );
      }
      const answeredAt = answers.get(`anthropic:${model}`);
      if (answeredAt && now() - answeredAt < cacheTtlMs) {
        return entry("anthropic", model, "models:use", "reachable", "answered a call in this process", answeredAt);
      }
      const through = check ? `through Claude Code ${check.version}` : "through Claude Code";
      return entry(
        "anthropic", model, "models:use", "unchecked",
        `${through}; not probed, since probing would mean a model call; the first call proves it`, check?.at || null,
      );
    });
  }

  async function vllmMinds(granted) {
    if (!granted) return vllmModels.map(model => entry("vllm", model, "models:use", "not_granted", "needs models:use"));
    const result = await probeVllm();
    const served = result.models.filter(model => isVllmModel(model));
    const models = [...new Set([...vllmModels, ...served])];
    return models.map(model => {
      if (!result.ok) {
        return entry("vllm", model, "models:use", "unreachable", `GET /v1/models: ${result.error}`, result.at);
      }
      return served.includes(model)
        ? entry("vllm", model, "models:use", "reachable", "listed by GET /v1/models", result.at)
        : entry("vllm", model, "models:use", "unreachable", "the endpoint answered but does not serve this model", result.at);
    });
  }

  async function localMinds(capabilities) {
    // A setting named LOCAL that points off this device is a network route,
    // so it needs the cloud grant as well, as the Ollama bridge requires.
    const capability = localEndpointIsLoopback ? "models:use" : "models:ollama-cloud";
    if (!capabilities.has("models:use") || !capabilities.has(capability)) {
      return [entry("ollama", null, capability, "not_granted", `needs ${capability}`)];
    }
    const result = await probeLocal();
    if (!result.ok) {
      return [entry("ollama", null, capability, "unreachable", `GET /api/tags: ${result.error}`, result.at)];
    }
    const installed = result.models.filter(model => !result.remote.includes(model));
    if (installed.length === 0) {
      return [entry("ollama", null, capability, "not_configured", "Ollama answered with no local models pulled", result.at)];
    }
    return installed.map(model =>
      entry("ollama", model, capability, "reachable", "listed by GET /api/tags", result.at));
  }

  async function cloudMinds(capabilities) {
    const granted = capabilities.has("models:use") && capabilities.has("models:ollama-cloud");
    if (!granted) {
      return cloudModels.map(model =>
        entry("ollama_cloud", model, "models:ollama-cloud", "not_granted", "needs models:ollama-cloud"));
    }
    // The local Ollama is read only with the grant: it is the other way to Ollama Cloud.
    const local = await probeLocal();
    const keyed = cloudKeyConfigured();
    const viaLocal = !keyed && local.ok;
    const listed = cloudModels.map(model => {
      if (!keyed && !local.ok) {
        return entry(
          "ollama_cloud", model, "models:ollama-cloud", "not_configured",
          "OLLAMA_API_KEY is not set and no local Ollama answered GET /api/tags", local.at,
        );
      }
      return entry(
        "ollama_cloud", model, "models:ollama-cloud", "unchecked",
        viaLocal
          ? "through the local Ollama, whose sign-in is not checked; not probed: the first call proves it"
          : "not probed: YOUI does not contact Ollama Cloud to probe; the first call proves it",
        viaLocal ? local.at : null,
        { throughDaemon: viaLocal },
      );
    });
    const stubs = local.ok ? local.remote.filter(model => !cloudModels.includes(model)) : [];
    return [...listed, ...stubs.map(model => entry(
      "ollama_cloud", model, "models:ollama-cloud", "unchecked",
      "listed by GET /api/tags as an Ollama Cloud model the local Ollama forwards; not probed: the first call proves it",
      local.at, { throughDaemon: true },
    ))];
  }

  return {
    /**
     * Every mind, with its state for a session holding `capabilities`.
     * `checked_at` is when the oldest probe result in the list was taken,
     * or null when nothing needed probing.
     */
    async list({ capabilities = new Set() } = {}) {
      const granted = capabilities.has("models:use");
      const [anthropic, vllm, local, cloud] = await Promise.all([
        anthropicMinds(granted), vllmMinds(granted), localMinds(capabilities), cloudMinds(capabilities),
      ]);
      const minds = [...anthropic, ...vllm, ...local, ...cloud];
      const probedAt = minds.map(mind => mind.checked_at).filter(Boolean).sort();
      return { minds, checked_at: probedAt[0] || null, cache_ttl_ms: cacheTtlMs };
    },

    /** A real call to this model succeeded; the registry may call it reachable for a while. */
    recordAnswer(provider, model) {
      answers.set(`${provider}:${model}`, now());
    },
  };
}

/** The minds a session is offered: granted, and reachable or unchecked. */
export function offeredMinds(listing) {
  return (listing?.minds || []).filter(mind => mind.offered);
}
