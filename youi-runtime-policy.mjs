import { lstatSync, realpathSync } from "fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "path";

/**
 * Resolve a YOUI file-tool path and enforce a canonical workspace boundary.
 *
 * Existing targets are canonicalized directly. For a new file, its existing
 * parent is canonicalized before the basename is appended. This makes a
 * symlink inside the workspace unable to redirect a read or write outside it.
 */
export function resolveScopedPath({
  inputPath,
  workdir,
  home,
  fileScope = "workspace",
}) {
  let requested = inputPath || workdir;
  if (requested === "~") requested = home;
  if (requested.startsWith("~/")) requested = join(home, requested.slice(2));

  const candidate = isAbsolute(requested)
    ? resolve(requested)
    : resolve(workdir, requested);
  if (fileScope !== "workspace") return candidate;

  const workspace = realpathSync(workdir);
  const existsWithoutFollowing = path => {
    try {
      lstatSync(path);
      return true;
    } catch {
      return false;
    }
  };
  let canonical;
  if (existsWithoutFollowing(candidate)) {
    canonical = realpathSync(candidate);
  } else {
    const missingSegments = [];
    let ancestor = candidate;
    while (!existsWithoutFollowing(ancestor)) {
      const parent = dirname(ancestor);
      if (parent === ancestor) {
        throw new Error(`No existing ancestor for path: ${inputPath}`);
      }
      missingSegments.push(basename(ancestor));
      ancestor = parent;
    }
    canonical = resolve(realpathSync(ancestor), ...missingSegments.reverse());
  }
  const rel = relative(workspace, canonical);
  if (rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) {
    throw new Error(`Path outside workspace file scope: ${inputPath}`);
  }
  return canonical;
}
