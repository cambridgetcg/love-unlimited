import assert from "node:assert/strict";
import test from "node:test";

import {
  redactDelegatedCredentials,
  sanitizedChildEnv,
} from "../youi-web/subprocess-env.mjs";

test("web child environments exclude ambient credentials by default", () => {
  const source = {
    PATH: "/usr/bin:/bin",
    LANG: "en_GB.UTF-8",
    API_SECRET_SENTINEL: "must-not-pass",
    OLLAMA_API_KEY: "must-not-pass",
    GITHUB_TOKEN: "must-not-pass",
    HIVE_INSTANCE: "resident-must-not-pass",
  };
  const env = sanitizedChildEnv({
    source,
    home: "/workspace",
    loveHome: "/workspace/love-unlimited",
    agent: "beta",
    purpose: "test-model-tool",
  });

  assert.equal(env.PATH, source.PATH);
  assert.equal(env.HOME, "/workspace");
  assert.equal(env.KINGDOM_AGENT, "beta");
  assert.equal(env.YOUI_CHILD_ENV, "sanitized");
  assert.equal(env.API_SECRET_SENTINEL, undefined);
  assert.equal(env.OLLAMA_API_KEY, undefined);
  assert.equal(env.GITHUB_TOKEN, undefined);
  assert.equal(env.HIVE_INSTANCE, undefined);
});

test("credentials and HIVE identity cross only explicit scoped boundaries", () => {
  const source = {
    PATH: "/usr/bin:/bin",
    OLLAMA_API_KEY: "scoped-test-value",
    GITHUB_TOKEN: "must-not-pass",
    HIVE_INSTANCE: "resident-must-not-pass",
  };
  const env = sanitizedChildEnv({
    source,
    home: "/workspace",
    loveHome: "/workspace/love-unlimited",
    hiveInstance: "authorized-device",
    purpose: "test-provider",
    credentialNames: ["OLLAMA_API_KEY"],
  });

  assert.equal(env.OLLAMA_API_KEY, "scoped-test-value");
  assert.equal(env.GITHUB_TOKEN, undefined);
  assert.equal(env.HIVE_INSTANCE, "authorized-device");
});

test("delegated credential values are redacted before child output crosses boundaries", () => {
  const source = {
    AGENTTOOL_API_KEY: "unit-test-sensitive-value",
    SHORT_VALUE: "abc",
  };
  const redacted = redactDelegatedCredentials(
    "child echoed unit-test-sensitive-value; short abc remains diagnostic",
    {
      source,
      credentialNames: ["AGENTTOOL_API_KEY", "SHORT_VALUE"],
    },
  );

  assert.doesNotMatch(redacted, /unit-test-sensitive-value/);
  assert.match(redacted, /\[REDACTED:AGENTTOOL_API_KEY\]/);
  assert.match(redacted, /short abc remains diagnostic/);
});
