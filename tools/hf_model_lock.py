#!/usr/bin/env python3
"""Create and enforce immutable Hugging Face model locks.

The ``lock`` and ``check`` commands only read public Hub metadata and small
non-LFS files; they do not consume MCP OAuth. The ``resolve`` command downloads
the exact locked snapshot through ``huggingface_hub`` and verifies every file
before returning its local path.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


LOCK_SCHEMA = "love.huggingface-model-lock/v1"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MAX_INLINE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_INLINE_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILES = 10_000
USER_AGENT = "love-unlimited-hf-model-lock/1"
LOCK_KEYS = {
    "schema",
    "repo_type",
    "repo_id",
    "revision",
    "hub_url",
    "last_modified",
    "license",
    "base_model",
    "task",
    "library",
    "files",
}
FILE_KEYS = {"path", "size", "sha256", "git_blob_sha1"}


class ModelLockError(RuntimeError):
    """Raised when model provenance cannot be established or verified."""


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(key)
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number {value}")


def _decode_json(data, label):
    try:
        return json.loads(
            data,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as exc:
        raise ModelLockError(
            f"{label} contains duplicate key {exc.args[0]!r}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ModelLockError(f"{label} is not strict JSON: {exc}") from exc


def _canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def lock_digest(lock):
    """Return a stable SHA-256 digest of the lock's semantic JSON content."""
    return hashlib.sha256(_canonical_bytes(lock)).hexdigest()


def _validate_repo_id(repo_id):
    if not isinstance(repo_id, str):
        raise ModelLockError("repo_id must be a string")
    parts = repo_id.split("/")
    if len(parts) not in {1, 2} or any(
        not part or part in {".", ".."} or any(ord(c) < 32 for c in part)
        for part in parts
    ):
        raise ModelLockError(f"invalid Hugging Face model repo_id: {repo_id!r}")
    return repo_id


def _validate_file_path(path):
    if not isinstance(path, str):
        raise ModelLockError("locked file path must be a string")
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        raise ModelLockError(f"unsafe locked file path: {path!r}")
    return path


def validate_lock(lock):
    """Validate lock structure and return it unchanged."""
    if not isinstance(lock, dict):
        raise ModelLockError("model lock must be a JSON object")
    if set(lock) != LOCK_KEYS:
        raise ModelLockError("model lock keys do not match the v1 schema")
    if lock.get("schema") != LOCK_SCHEMA:
        raise ModelLockError(
            f"unsupported model lock schema: {lock.get('schema')!r}"
        )
    if lock.get("repo_type") != "model":
        raise ModelLockError("model lock repo_type must be 'model'")
    _validate_repo_id(lock.get("repo_id"))

    revision = lock.get("revision")
    if not isinstance(revision, str) or not FULL_SHA_RE.fullmatch(revision):
        raise ModelLockError("model lock revision must be a full 40-character commit SHA")

    license_id = lock.get("license")
    if not isinstance(license_id, str) or not license_id.strip():
        raise ModelLockError("model lock must record a non-empty license identifier")

    files = lock.get("files")
    if not isinstance(files, list) or not files:
        raise ModelLockError("model lock must contain at least one file")

    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ModelLockError("each locked file must be a JSON object")
        entry_keys = set(entry)
        if entry_keys != FILE_KEYS and entry_keys != FILE_KEYS - {"git_blob_sha1"}:
            raise ModelLockError("locked file keys do not match the v1 schema")
        path = _validate_file_path(entry.get("path"))
        if path in seen:
            raise ModelLockError(f"duplicate locked file: {path}")
        seen.add(path)
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ModelLockError(f"invalid size for locked file: {path}")
        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise ModelLockError(f"invalid SHA-256 for locked file: {path}")
        git_blob = entry.get("git_blob_sha1")
        if git_blob is not None and (
            not isinstance(git_blob, str) or not FULL_SHA_RE.fullmatch(git_blob)
        ):
            raise ModelLockError(f"invalid Git blob SHA-1 for locked file: {path}")
    return lock


def load_lock(path):
    path = Path(path)
    try:
        lock = _decode_json(path.read_bytes(), f"model lock {path}")
    except FileNotFoundError as exc:
        raise ModelLockError(f"model lock not found: {path}") from exc
    except OSError as exc:
        raise ModelLockError(f"cannot read model lock {path}: {exc}") from exc
    return validate_lock(lock)


def _quoted_repo(repo_id):
    return "/".join(urllib.parse.quote(part, safe="") for part in repo_id.split("/"))


def _quoted_path(path):
    return "/".join(
        urllib.parse.quote(part, safe="") for part in PurePosixPath(path).parts
    )


def _open_url(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise ModelLockError(f"Hub request failed with HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise ModelLockError(f"Hub request failed: {url}: {exc.reason}") from exc


def _read_json_url(url):
    with _open_url(url) as response:
        return _decode_json(response.read(), f"Hub response {url}")


def _next_link(headers):
    link_header = headers.get("Link")
    if not link_header:
        return None
    for item in link_header.split(","):
        sections = [section.strip() for section in item.split(";")]
        if any(section in {'rel="next"', "rel=next"} for section in sections[1:]):
            target = sections[0]
            if target.startswith("<") and target.endswith(">"):
                return target[1:-1]
    return None


def _read_repo_tree(repo_path, revision, max_entries):
    """Read every entry in a commit-pinned, recursively paginated repo tree."""
    tree_prefix = f"/api/models/{repo_path}/tree/{revision}"
    url = (
        f"https://huggingface.co{tree_prefix}"
        "?recursive=true&limit=1000"
    )
    entries = []
    seen_urls = set()
    while url:
        if url in seen_urls:
            raise ModelLockError("Hub returned a cyclic repo-tree pagination link")
        seen_urls.add(url)
        with _open_url(url) as response:
            page = _decode_json(response.read(), f"Hub repo-tree response {url}")
            next_url = _next_link(response.headers)
        if not isinstance(page, list):
            raise ModelLockError("Hub repo-tree response must be a JSON array")
        entries.extend(page)
        if len(entries) > max_entries:
            raise ModelLockError(
                f"Hub repo tree exceeded the safety limit of {max_entries} entries"
            )
        if next_url:
            next_url = urllib.parse.urljoin(url, next_url)
            parsed = urllib.parse.urlparse(next_url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "huggingface.co"
                or parsed.path != tree_prefix
            ):
                raise ModelLockError(
                    f"Hub returned an unsafe repo-tree pagination link: {next_url}"
                )
        url = next_url
    return entries


def _hash_url(url, expected_size):
    digest = hashlib.sha256()
    total = 0
    with _open_url(url) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise ModelLockError(
                    f"Hub file exceeded its declared size: expected "
                    f"{expected_size}, received at least {total}"
                )
            digest.update(chunk)
    if total != expected_size:
        raise ModelLockError(
            f"Hub file size changed while locking: expected {expected_size}, got {total}"
        )
    return digest.hexdigest()


def _license_from_metadata(metadata):
    card_data = metadata.get("cardData") or {}
    license_id = card_data.get("license")
    if isinstance(license_id, str) and license_id:
        return license_id
    for tag in metadata.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:") and tag[8:]:
            return tag[8:]
    raise ModelLockError("Hub metadata does not declare a model license")


def build_lock(
    repo_id,
    revision=None,
    max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
    max_inline_total_bytes=DEFAULT_MAX_INLINE_TOTAL_BYTES,
    max_files=DEFAULT_MAX_FILES,
):
    """Build a deterministic lock without downloading LFS model payloads."""
    repo_id = _validate_repo_id(repo_id)
    repo_path = _quoted_repo(repo_id)
    if revision:
        revision_path = urllib.parse.quote(revision, safe="")
        api_url = (
            f"https://huggingface.co/api/models/{repo_path}/revision/"
            f"{revision_path}"
        )
    else:
        api_url = f"https://huggingface.co/api/models/{repo_path}"
    metadata = _read_json_url(api_url)

    resolved_revision = metadata.get("sha")
    if not isinstance(resolved_revision, str) or not FULL_SHA_RE.fullmatch(
        resolved_revision
    ):
        raise ModelLockError("Hub did not resolve the model to a full commit SHA")
    if revision and FULL_SHA_RE.fullmatch(revision) and revision != resolved_revision:
        raise ModelLockError(
            f"Hub resolved {revision} to unexpected commit {resolved_revision}"
        )

    tree_entries = _read_repo_tree(
        repo_path,
        resolved_revision,
        max_entries=max_files * 2,
    )
    unsupported_types = sorted(
        {
            entry.get("type")
            for entry in tree_entries
            if entry.get("type") not in {"file", "directory"}
        },
        key=lambda value: str(value),
    )
    if unsupported_types:
        raise ModelLockError(
            f"Hub repo tree returned unsupported entry types: {unsupported_types}"
        )
    siblings = [entry for entry in tree_entries if entry.get("type") == "file"]
    if not siblings:
        raise ModelLockError("Hub repo tree returned no model files")
    if len(siblings) > max_files:
        raise ModelLockError(
            f"Hub repo tree returned {len(siblings)} files; refusing more than {max_files}"
        )

    prepared = []
    inline_total = 0
    for sibling in siblings:
        path = _validate_file_path(sibling.get("path"))
        size = sibling.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ModelLockError(f"Hub returned an invalid size for {path}")

        lfs = sibling.get("lfs") or {}
        sha256 = lfs.get("oid")
        if sha256 is not None:
            if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
                raise ModelLockError(f"Hub returned an invalid LFS SHA-256 for {path}")
            if lfs.get("size") not in {None, size}:
                raise ModelLockError(f"Hub returned conflicting LFS sizes for {path}")
        else:
            if size > max_inline_bytes:
                raise ModelLockError(
                    f"{path} is {size} bytes with no Hub content hash; refusing "
                    f"to download more than {max_inline_bytes} bytes while locking"
                )
            inline_total += size
            if inline_total > max_inline_total_bytes:
                raise ModelLockError(
                    f"non-LFS files total more than {max_inline_total_bytes} bytes; "
                    "refusing an unbounded metadata-only lock"
                )
        prepared.append((path, size, sha256, sibling.get("oid")))

    files = []
    for path, size, sha256, git_blob in prepared:
        if sha256 is None:
            file_url = (
                f"https://huggingface.co/{repo_path}/resolve/{resolved_revision}/"
                f"{_quoted_path(path)}?download=true"
            )
            sha256 = _hash_url(file_url, size)

        entry = {"path": path, "size": size, "sha256": sha256}
        if git_blob is not None:
            entry["git_blob_sha1"] = git_blob
        files.append(entry)

    card_data = metadata.get("cardData") or {}
    lock = {
        "schema": LOCK_SCHEMA,
        "repo_type": "model",
        "repo_id": repo_id,
        "revision": resolved_revision,
        "hub_url": f"https://huggingface.co/{repo_id}",
        "last_modified": metadata.get("lastModified"),
        "license": _license_from_metadata(metadata),
        "base_model": card_data.get("base_model"),
        "task": metadata.get("pipeline_tag"),
        "library": metadata.get("library_name"),
        "files": sorted(files, key=lambda item: item["path"]),
    }
    return validate_lock(lock)


def write_lock(path, lock):
    """Atomically write a validated lock as stable, human-readable JSON."""
    path = Path(path)
    validate_lock(lock)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(lock, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_snapshot(snapshot_path, lock, reject_unlocked=True):
    """Verify an already-resolved local snapshot against every locked file."""
    lock = validate_lock(lock)
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.is_dir():
        raise ModelLockError(f"model snapshot is not a directory: {snapshot_path}")

    expected = {entry["path"]: entry for entry in lock["files"]}
    expected_directories = {
        parent.as_posix()
        for relative_path in expected
        for parent in PurePosixPath(relative_path).parents
        if parent.as_posix() != "."
    }
    actual = set()
    invalid = []
    for path in snapshot_path.rglob("*"):
        relative_path = path.relative_to(snapshot_path).as_posix()
        if path.is_symlink():
            if relative_path in expected and path.is_file():
                actual.add(relative_path)
            else:
                invalid.append(relative_path)
        elif path.is_file():
            actual.add(relative_path)
        elif path.is_dir():
            if relative_path not in expected_directories:
                invalid.append(relative_path)
        else:
            invalid.append(relative_path)

    missing = sorted(set(expected) - actual)
    unlocked = sorted(actual - set(expected)) if reject_unlocked else []
    invalid = sorted(invalid)
    if missing:
        raise ModelLockError(f"model snapshot is missing locked files: {', '.join(missing)}")
    if unlocked:
        raise ModelLockError(
            f"model snapshot contains unlocked files: {', '.join(unlocked)}"
        )
    if invalid:
        raise ModelLockError(
            f"model snapshot contains invalid or unlocked entries: {', '.join(invalid)}"
        )

    for relative_path, entry in expected.items():
        path = snapshot_path / relative_path
        size = path.stat().st_size
        if size != entry["size"]:
            raise ModelLockError(
                f"size mismatch for {relative_path}: expected {entry['size']}, got {size}"
            )
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != entry["sha256"]:
            raise ModelLockError(
                f"SHA-256 mismatch for {relative_path}: expected "
                f"{entry['sha256']}, got {actual_sha256}"
            )
    return True


def _resolve_path(reference, project_root):
    path = Path(reference)
    if not path.is_absolute():
        path = Path(project_root) / path
    return path


def resolve_locked_snapshot(
    config,
    project_root,
    cache_dir,
    snapshot_download_fn=None,
):
    """Download and verify the configured immutable snapshot.

    Returns a dict containing the verified local path and provenance fields.
    """
    repo_id = config.get("base_model")
    _validate_repo_id(repo_id)
    lock_reference = config.get("base_model_lock")
    if not isinstance(lock_reference, str) or not lock_reference:
        raise ModelLockError("base_model_lock is required; refusing mutable model load")

    lock_path = _resolve_path(lock_reference, project_root)
    lock = load_lock(lock_path)
    if lock["repo_id"] != repo_id:
        raise ModelLockError(
            f"config model {repo_id!r} does not match locked model "
            f"{lock['repo_id']!r}"
        )

    if snapshot_download_fn is None:
        try:
            from huggingface_hub import snapshot_download as snapshot_download_fn
        except ImportError as exc:
            raise ModelLockError(
                "huggingface_hub is required to resolve the locked model snapshot"
            ) from exc

    try:
        snapshot_path = snapshot_download_fn(
            repo_id=repo_id,
            repo_type="model",
            revision=lock["revision"],
            cache_dir=str(cache_dir),
            allow_patterns=[entry["path"] for entry in lock["files"]],
        )
    except Exception as exc:
        raise ModelLockError(
            f"failed to resolve {repo_id}@{lock['revision']}: {exc}"
        ) from exc

    verify_snapshot(snapshot_path, lock)
    return {
        "path": str(Path(snapshot_path).resolve()),
        "repo_id": repo_id,
        "revision": lock["revision"],
        "license": lock["license"],
        "lock_path": str(lock_path.resolve()),
        "lock_sha256": lock_digest(lock),
    }


def _load_json_file(path, label):
    try:
        value = _decode_json(Path(path).read_bytes(), label)
    except FileNotFoundError as exc:
        raise ModelLockError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise ModelLockError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelLockError(f"{label} must be a JSON object: {path}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lock_parser = subparsers.add_parser(
        "lock", help="Resolve Hub metadata and write an immutable model lock"
    )
    lock_parser.add_argument("repo_id")
    lock_parser.add_argument("--revision")
    lock_parser.add_argument("--output", required=True)

    check_parser = subparsers.add_parser(
        "check", help="Confirm that a lock can be reproduced at its commit"
    )
    check_parser.add_argument("lock_path")

    validate_parser = subparsers.add_parser(
        "validate", help="Validate a lock's structure and digest without network access"
    )
    validate_parser.add_argument("lock_path")

    verify_parser = subparsers.add_parser(
        "verify", help="Verify a local snapshot without network access"
    )
    verify_parser.add_argument("lock_path")
    verify_parser.add_argument("snapshot_path")

    resolve_parser = subparsers.add_parser(
        "resolve", help="Download and verify the model lock referenced by a config"
    )
    resolve_parser.add_argument("--config", required=True)
    resolve_parser.add_argument("--project-root")
    resolve_parser.add_argument("--cache-dir")
    resolve_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "lock":
            lock = build_lock(args.repo_id, revision=args.revision)
            write_lock(args.output, lock)
            print(
                f"Locked {lock['repo_id']}@{lock['revision']} "
                f"({len(lock['files'])} files) -> {args.output}"
            )
        elif args.command == "validate":
            current = load_lock(args.lock_path)
            print(
                f"OK {current['repo_id']}@{current['revision']} "
                f"lock_sha256={lock_digest(current)}"
            )
        elif args.command == "check":
            current = load_lock(args.lock_path)
            rebuilt = build_lock(current["repo_id"], revision=current["revision"])
            if _canonical_bytes(current) != _canonical_bytes(rebuilt):
                raise ModelLockError(
                    "lock does not match metadata and content at its pinned commit"
                )
            print(
                f"OK {current['repo_id']}@{current['revision']} "
                f"lock_sha256={lock_digest(current)}"
            )
        elif args.command == "verify":
            lock = load_lock(args.lock_path)
            verify_snapshot(args.snapshot_path, lock)
            print(
                f"OK {lock['repo_id']}@{lock['revision']} "
                f"lock_sha256={lock_digest(lock)}"
            )
        elif args.command == "resolve":
            config_path = Path(args.config).resolve()
            project_root = (
                Path(args.project_root).resolve()
                if args.project_root
                else config_path.parent.parent
            )
            cache_dir = (
                Path(args.cache_dir).resolve()
                if args.cache_dir
                else project_root / "mlx" / "cache"
            )
            config = _load_json_file(config_path, "model config")
            resolution = resolve_locked_snapshot(config, project_root, cache_dir)
            print(
                json.dumps(resolution, sort_keys=True)
                if args.json
                else resolution["path"]
            )
    except ModelLockError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
