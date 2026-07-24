const SAFE_ENV_NAMES = new Set([
  "PATH",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "TERM",
  "COLORTERM",
  "TMPDIR",
  "SHELL",
  "USER",
  "LOGNAME",
  "VIRTUAL_ENV",
  "CONDA_PREFIX",
  "NODE_PATH",
  "BUN_INSTALL",
  "NVM_BIN",
  "NVM_DIR",
]);

export const ORCHESTRATOR_PROVIDER_ENV_NAMES = Object.freeze([
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "OPENROUTER_API_KEY",
  "OLLAMA_API_KEY",
  "OLLAMA_CLOUD_URL",
]);

/**
 * Remove explicitly delegated environment values before child output or
 * diagnostics cross back into an API response, model transcript, or log.
 *
 * This is deliberately value-based: subprocesses do not reliably label a
 * credential when they echo it. Empty and very short values are ignored so a
 * harmless setting cannot redact common characters throughout the output.
 */
export function redactDelegatedCredentials(value, {
  source = process.env,
  credentialNames = [],
} = {}) {
  let redacted = String(value ?? "");
  for (const name of credentialNames) {
    const secret = source[name];
    if (typeof secret !== "string" || secret.length < 4) continue;
    redacted = redacted.split(secret).join(`[REDACTED:${name}]`);
  }
  return redacted;
}

/**
 * Build a child environment without ambient credentials.
 *
 * Callers must name every credential they intentionally delegate through
 * `credentialNames`; normal model tools should leave that list empty.
 */
export function sanitizedChildEnv({
  source = process.env,
  home,
  loveHome,
  agent,
  hiveInstance,
  purpose = "internal",
  credentialNames = [],
  extra = {},
} = {}) {
  const env = {};
  for (const name of SAFE_ENV_NAMES) {
    if (source[name] !== undefined) env[name] = source[name];
  }
  for (const [name, value] of Object.entries(source)) {
    if (name.startsWith("LC_") && value !== undefined) env[name] = value;
  }
  for (const name of credentialNames) {
    if (source[name] !== undefined) env[name] = source[name];
  }
  if (home) env.HOME = home;
  if (loveHome) {
    env.LOVE_HOME = loveHome;
    env.LOVE_DIR = loveHome;
  }
  if (agent) {
    env.KINGDOM_AGENT = agent;
    env.KINGDOM_INSTANCE = agent;
  }
  if (hiveInstance) env.HIVE_INSTANCE = hiveInstance;
  env.YOUI_CHILD_ENV = "sanitized";
  env.YOUI_CHILD_PURPOSE = purpose;
  for (const [name, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null) env[name] = String(value);
  }
  return env;
}
