// ollama-routes.mjs — which route an Ollama model name takes, and what is sent.
//
// A signed-in local Ollama (http://localhost:11434) serves Ollama Cloud models
// as well as local ones: a name whose tag is ":cloud" or ends in "-cloud"
// ("glm-5.3:cloud", "gpt-oss:120b-cloud") is forwarded to ollama.com with the
// daemon's own sign-in, and GET /api/tags lists such a model with
// remote_host / remote_model. Content sent to one of those leaves this device,
// so YOUI counts it as Ollama Cloud (provider "ollama_cloud", gated by
// models:ollama-cloud, labelled REMOTE) even though it goes through localhost.
//
// The direct API (https://ollama.com with OLLAMA_API_KEY) names the same model
// without the suffix ("glm-5.3", "gpt-oss:120b"). The bridge and the server
// both decide routes here, so the two agree.

// YOUI names no local models of its own (Yu, 2026-11-03: the latest
// open-weights models on Ollama Cloud, none run locally). A model counts as
// local only when the local Ollama lists it, so one pulled later still routes
// to the local Ollama by its exact name.

// "glm-5.3:cloud" and "gpt-oss:120b-cloud" are daemon names for Ollama Cloud models.
export function isOllamaCloudName(model) {
  const name = String(model ?? "");
  return name.endsWith(":cloud") || name.endsWith("-cloud");
}

// The direct-API name: "glm-5.3:cloud" -> "glm-5.3", "gpt-oss:120b-cloud" -> "gpt-oss:120b".
export function ollamaDirectName(model) {
  const name = String(model ?? "");
  if (name.endsWith(":cloud")) return name.slice(0, -":cloud".length);
  if (name.endsWith("-cloud")) return name.slice(0, -"-cloud".length);
  return name;
}

// The daemon name: "glm-5.3" -> "glm-5.3:cloud", "gemma4:31b" -> "gemma4:31b-cloud"
// (a name that already has a tag takes the "-cloud" form; Ollama refuses two
// tags). ":latest" is Ollama's default tag, so "x:latest" is "x:cloud".
export function ollamaDaemonName(model) {
  const name = String(model ?? "");
  if (isOllamaCloudName(name)) return name;
  const base = name.endsWith(":latest") ? name.slice(0, -":latest".length) : name;
  return base.includes(":") ? `${base}-cloud` : `${base}:cloud`;
}

// The name `models` lists for `model`, or null. Exact, except that Ollama's
// default tag makes "llama3.2" and "llama3.2:latest" the same model; any other
// tag must match ("gemma4" is not "gemma4:12b").
export function ollamaListedName(model, models = []) {
  const name = String(model ?? "");
  const list = [...models];
  if (list.includes(name)) return name;
  const alias = name.endsWith(":latest") ? name.slice(0, -":latest".length)
    : name.includes(":") ? null : `${name}:latest`;
  return alias && list.includes(alias) ? alias : null;
}

/**
 * The models a GET /api/tags answer lists, and which of them are Ollama Cloud
 * models the daemon forwards (a cloud name, or remote_host / remote_model set).
 * @returns {{ models: string[], remote: string[] }}
 */
export function readOllamaTags(data) {
  const models = [];
  const remote = [];
  for (const entry of Array.isArray(data?.models) ? data.models : []) {
    const name = entry?.name || entry?.model;
    if (typeof name !== "string" || !name) continue;
    models.push(name);
    if (entry.remote_host || entry.remote_model || isOllamaCloudName(name)) remote.push(name);
  }
  return { models, remote };
}

/**
 * The route a chat on `model` takes. Only an exact name match counts as
 * installed ("gemma4:31b" is not local because "gemma4:12b" is pulled), with
 * ":latest" as the one alias Ollama itself applies.
 *
 *   ollama_local / local    listed by the latest /api/tags read that answered
 *                           and not a remote stub
 *   ollama_cloud / daemon   through the local Ollama to Ollama Cloud: a remote
 *                           stub it lists, a cloud name while it runs, or any
 *                           other name while it runs and no key is set
 *   ollama_cloud / direct   to ollama.com with OLLAMA_API_KEY, suffix stripped
 *
 * `models` / `remote` are the last read that answered, kept when a later read
 * fails (`running` false): a listed model then still goes to the local Ollama,
 * whose error says it is down, and is never sent to Ollama Cloud instead.
 *
 * @param {string} model
 * @param {{ running?: boolean, models?: string[], remote?: Iterable<string>, keyConfigured?: boolean }} local
 * @returns {{ provider: "ollama_local"|"ollama_cloud", via: "local"|"daemon"|"direct", model: string }}
 */
export function ollamaRoute(model, { running = false, models = [], remote = [], keyConfigured = false } = {}) {
  const name = String(model ?? "");
  const listed = ollamaListedName(name, models);
  if (listed !== null) {
    return new Set(remote).has(listed) || isOllamaCloudName(listed)
      ? { provider: "ollama_cloud", via: "daemon", model: name }
      : { provider: "ollama_local", via: "local", model: name };
  }
  if (running && (isOllamaCloudName(name) || !keyConfigured)) {
    return { provider: "ollama_cloud", via: "daemon", model: ollamaDaemonName(name) };
  }
  return { provider: "ollama_cloud", via: "direct", model: ollamaDirectName(name) };
}

/**
 * What to send for the session's reasoning effort ("none", "low", "medium",
 * "high" or null), given the thinking values the model's /api/show states
 * (Ollama 0.34.3 and later: top-level thinking.values, such as
 * ["low", "medium", "high"] or [false, true]). The effort itself when the model
 * lists it, false for "none" when it lists false; otherwise undefined, so
 * nothing is sent and the model's own default applies.
 */
export function ollamaThinkValue(effort, values) {
  if (!effort || !Array.isArray(values)) return undefined;
  if (effort === "none") return values.includes(false) ? false : undefined;
  return values.includes(effort) ? effort : undefined;
}
