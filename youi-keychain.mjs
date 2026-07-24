import { spawnSync as defaultSpawnSync } from "child_process";

function keychainChildEnv(source = process.env) {
  const env = {};
  for (const name of [
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "USER",
  ]) {
    if (source[name] !== undefined) env[name] = source[name];
  }
  return env;
}

function uniqueAccounts(user) {
  return [user || "", ""].filter((value, index, values) =>
    values.indexOf(value) === index
  );
}

/**
 * Exact-account Keychain access for YOUI OAuth credentials.
 *
 * The store never delete-firsts. Updates use `security ... -U`, then re-read
 * the same service/account pair before reporting success. Diagnostics contain
 * service/account/exit status only, never the credential payload or stderr.
 */
export class KeychainCredentialStore {
  constructor({
    service,
    user = process.env.USER || "",
    run = defaultSpawnSync,
    logger = message => console.error(message),
    command = "/usr/bin/security",
    sourceEnv = process.env,
  }) {
    if (!service) throw new TypeError("Keychain service is required");
    this.service = service;
    this.accounts = uniqueAccounts(user);
    this.run = run;
    this.logger = logger;
    this.command = command;
    this.childEnv = keychainChildEnv(sourceEnv);
  }

  readEntry(account) {
    const result = this.run(
      this.command,
      ["find-generic-password", "-s", this.service, "-a", account, "-w"],
      { encoding: "utf-8", timeout: 5000, env: this.childEnv },
    );
    if (result.status !== 0) return { result, data: null };
    try {
      return { result, data: JSON.parse(result.stdout.trim()) };
    } catch {
      throw new Error(
        `Keychain entry for service ${this.service}, account "${account}" `
        + "is not valid JSON.",
      );
    }
  }

  readCredential() {
    for (const account of this.accounts) {
      try {
        const { result, data } = this.readEntry(account);
        if (result.status === 0 && data?.claudeAiOauth?.accessToken) {
          return { account, tokens: data.claudeAiOauth };
        }
        const diagnostic = String(result.stderr || "");
        if (
          result.status !== 0
          && !/could not be found|SecKeychainSearch/i.test(diagnostic)
        ) {
          this.logger(
            `[keychain] read failed for service ${this.service}, account "${account}" `
            + `(exit ${result.status ?? "unknown"}); credential content was not logged.`,
          );
        }
      } catch (error) {
        this.logger(`[keychain] ${error.message}`);
      }
    }
    return null;
  }

  updateTokens(account, tokens) {
    const { result, data } = this.readEntry(account);
    if (result.status !== 0 || !data) {
      throw new Error(
        `Refusing to replace missing Keychain entry for service ${this.service}, `
        + `account "${account}".`,
      );
    }

    const updated = { ...data, claudeAiOauth: tokens };
    const serialized = JSON.stringify(updated);
    const writeResult = this.run(
      this.command,
      [
        "add-generic-password", "-U",
        "-s", this.service,
        "-a", account,
        // `security` explicitly warns that `-w <password>` exposes the value
        // in argv. A final bare `-w` reads the prompted value from stdin.
        "-w",
      ],
      {
        encoding: "utf-8",
        timeout: 5000,
        env: this.childEnv,
        input: `${serialized}\n`,
      },
    );
    if (writeResult.status !== 0) {
      throw new Error(
        `Keychain update failed for service ${this.service}, account "${account}" `
        + `(exit ${writeResult.status ?? "unknown"}); the previous entry was not deleted.`,
      );
    }

    const verification = this.readEntry(account);
    if (
      verification.result.status !== 0
      || verification.data?.claudeAiOauth?.accessToken !== tokens.accessToken
    ) {
      throw new Error(
        `Keychain update could not be verified for service ${this.service}, `
        + `account "${account}".`,
      );
    }
  }
}
