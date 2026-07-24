import assert from "node:assert/strict";
import test from "node:test";

import { KeychainCredentialStore } from "../youi-keychain.mjs";

function fakeKeychain({ account = "yu", failUpdate = false } = {}) {
  const entries = new Map([
    [account, {
      metadata: "preserved",
      claudeAiOauth: {
        accessToken: "test-old-access",
        refreshToken: "test-old-refresh",
        expiresAt: 1,
      },
    }],
  ]);
  const calls = [];

  function run(command, args, options) {
    calls.push({ command, args, options });
    const operation = args[0];
    const accountIndex = args.indexOf("-a");
    const selected = accountIndex >= 0 ? args[accountIndex + 1] : "";
    if (operation === "find-generic-password") {
      const value = entries.get(selected);
      return value
        ? { status: 0, stdout: JSON.stringify(value), stderr: "" }
        : { status: 44, stdout: "", stderr: "could not be found" };
    }
    if (operation === "add-generic-password") {
      if (failUpdate) return { status: 1, stdout: "", stderr: "test failure" };
      entries.set(selected, JSON.parse(String(options.input).trim()));
      return { status: 0, stdout: "", stderr: "" };
    }
    throw new Error(`Unexpected fake operation: ${operation}`);
  }

  return { entries, calls, run };
}

test("Keychain refresh updates the exact account without delete-first", () => {
  const fake = fakeKeychain();
  const store = new KeychainCredentialStore({
    service: "test-service",
    user: "yu",
    run: fake.run,
    logger: () => {},
    sourceEnv: {
      PATH: "/usr/bin:/bin",
      HOME: "/test-home",
      API_SECRET_SENTINEL: "must-not-pass",
    },
  });
  const credential = store.readCredential();
  assert.equal(credential.account, "yu");

  store.updateTokens("yu", {
    accessToken: "test-new-access",
    refreshToken: "test-new-refresh",
    expiresAt: 2,
  });

  assert.equal(fake.entries.get("yu").metadata, "preserved");
  assert.equal(
    fake.entries.get("yu").claudeAiOauth.accessToken,
    "test-new-access",
  );
  assert.equal(
    fake.calls.some(call => call.args.includes("delete-generic-password")),
    false,
  );
  const update = fake.calls.find(call => call.args[0] === "add-generic-password");
  assert.ok(update.args.includes("-U"));
  assert.equal(update.args[update.args.indexOf("-a") + 1], "yu");
  assert.equal(update.args.at(-1), "-w");
  assert.equal(update.args.includes(JSON.stringify(fake.entries.get("yu"))), false);
  assert.equal(update.command, "/usr/bin/security");
  assert.equal(update.options.env.API_SECRET_SENTINEL, undefined);
  assert.equal(update.options.env.HOME, "/test-home");
});

test("failed Keychain update preserves the prior credential and propagates", () => {
  const fake = fakeKeychain({ failUpdate: true });
  const store = new KeychainCredentialStore({
    service: "test-service",
    user: "yu",
    run: fake.run,
    logger: () => {},
  });

  assert.throws(
    () => store.updateTokens("yu", {
      accessToken: "test-new-access",
      refreshToken: "test-new-refresh",
      expiresAt: 2,
    }),
    /previous entry was not deleted/,
  );
  assert.equal(
    fake.entries.get("yu").claudeAiOauth.accessToken,
    "test-old-access",
  );
});

test("blank-account fallback is updated exactly without creating a user entry", () => {
  const fake = fakeKeychain({ account: "" });
  const store = new KeychainCredentialStore({
    service: "test-service",
    user: "yu",
    run: fake.run,
    logger: () => {},
  });

  const credential = store.readCredential();
  assert.equal(credential.account, "");
  store.updateTokens("", {
    accessToken: "test-new-access",
    refreshToken: "test-new-refresh",
    expiresAt: 2,
  });

  assert.equal(fake.entries.has("yu"), false);
  assert.equal(
    fake.entries.get("").claudeAiOauth.accessToken,
    "test-new-access",
  );
  const update = fake.calls.find(call => call.args[0] === "add-generic-password");
  assert.equal(update.args[update.args.indexOf("-a") + 1], "");
});
