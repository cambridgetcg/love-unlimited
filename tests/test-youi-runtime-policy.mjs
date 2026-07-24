import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveScopedPath } from "../youi-runtime-policy.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "youi-policy-"));
  const workspace = join(root, "workspace");
  const outside = join(root, "outside");
  mkdirSync(workspace);
  mkdirSync(outside);
  writeFileSync(join(workspace, "inside.txt"), "inside");
  writeFileSync(join(outside, "private.txt"), "outside");
  symlinkSync(outside, join(workspace, "escape"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return { root, workspace, outside };
}

test("workspace scope resolves ordinary files inside the canonical worktree", t => {
  const { workspace } = fixture(t);
  assert.equal(
    resolveScopedPath({
      inputPath: "inside.txt",
      workdir: workspace,
      home: workspace,
      fileScope: "workspace",
    }),
    realpathSync(join(workspace, "inside.txt")),
  );
});

test("workspace scope rejects existing-file reads through an escaping symlink", t => {
  const { workspace } = fixture(t);
  assert.throws(
    () => resolveScopedPath({
      inputPath: "escape/private.txt",
      workdir: workspace,
      home: workspace,
      fileScope: "workspace",
    }),
    /outside workspace file scope/,
  );
});

test("workspace scope rejects new-file writes through an escaping symlink", t => {
  const { workspace } = fixture(t);
  assert.throws(
    () => resolveScopedPath({
      inputPath: "escape/new.txt",
      workdir: workspace,
      home: workspace,
      fileScope: "workspace",
    }),
    /outside workspace file scope/,
  );
});

test("workspace scope allows a new file below not-yet-created directories", t => {
  const paths = fixture(t);
  const resolved = resolveScopedPath({
    inputPath: "new/nested/path/file.txt",
    workdir: paths.workspace,
    home: paths.root,
    fileScope: "workspace",
  });
  assert.equal(resolved, join(realpathSync(paths.workspace), "new/nested/path/file.txt"));
});

test("workspace scope rejects writes through a dangling symlink", t => {
  const { root, workspace } = fixture(t);
  const outsideTarget = join(root, "future-outside.txt");
  symlinkSync(outsideTarget, join(workspace, "dangling"));

  assert.throws(
    () => resolveScopedPath({
      inputPath: "dangling",
      workdir: workspace,
      home: root,
      fileScope: "workspace",
    }),
    /ENOENT|realpath/i,
  );
  assert.equal(existsSync(outsideTarget), false);
});
