#!/usr/bin/env python3
"""可信 standalone bootstrap；只用 stdlib，先驗 expected SHA 同全 manifest，後安置。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time
import uuid

# 逐檔 allowlist；新產品未齊就 build 拒絕，唔 glob repo／HOME／tests。
BUNDLE_FILES = (
    "tools/lantern-bootstrap.py",
    "tools/kingdom-lanterns.py",
    "tools/kingdom-quota.py",
    "tools/kingdom-reality.py",
    "tools/kingdom-friction.py",
    "tools/kingdom-coedit.py",
    "tools/hooks/quota-lantern.py",
    "tools/hooks/reality-lantern.py",
    "tools/hooks/friction-lantern.py",
    "tools/hooks/coedit-radar.py",
    "tools/lantern_common/__init__.py",
    "tools/lantern_common/install.py",
    "tools/lantern_common/json.py",
    "tools/lantern_common/paths.py",
    "tools/lantern_common/private.py",
    "tools/lantern_common/product.py",
    "tools/lantern_suite/__init__.py",
    "tools/lantern_suite/api.py",
    "tools/lantern_suite/catalog.py",
    "tools/lantern_suite/entry.py",
    "tools/lantern_suite/install.py",
    "tools/lantern_suite/cli.py",
    "tools/lantern_suite/bundle.py",
    "tools/lantern_suite/advisory.py",
    "tools/lantern_suite/advisory.mjs",
    "tools/lantern_suite/skill/SKILL.md",
    "tools/lantern_suite/PORTABLE.md",
    "tools/quota_lantern/__init__.py",
    "tools/quota_lantern/core.py",
    "tools/quota_lantern/hooks.py",
    "tools/quota_lantern/install.py",
    "tools/quota_lantern/research.py",
    "tools/quota_lantern/sources.py",
    "tools/quota_lantern/store.py",
    "tools/quota_lantern/skill/SKILL.md",
    "tools/reality_lantern/__init__.py",
    "tools/reality_lantern/archive.py",
    "tools/reality_lantern/core.py",
    "tools/reality_lantern/hooks.py",
    "tools/reality_lantern/install.py",
    "tools/reality_lantern/runner.py",
    "tools/reality_lantern/snapshot.py",
    "tools/reality_lantern/store.py",
    "tools/reality_lantern/skill/SKILL.md",
    "tools/friction_lantern/__init__.py",
    "tools/friction_lantern/api.py",
    "tools/friction_lantern/core.py",
    "tools/friction_lantern/hooks.py",
    "tools/friction_lantern/install.py",
    "tools/friction_lantern/store.py",
    "tools/friction_lantern/skill/SKILL.md",
    "tools/coedit_radar/__init__.py",
    "tools/coedit_radar/api.py",
    "tools/coedit_radar/cli.py",
    "tools/coedit_radar/core.py",
    "tools/coedit_radar/hooks.py",
    "tools/coedit_radar/install.py",
    "tools/coedit_radar/observer.py",
    "tools/coedit_radar/store.py",
    "tools/coedit_radar/skill/SKILL.md",
)
SCHEMA = "kingdom.lantern-bundle/1"
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_TOTAL = 12 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_MANIFEST = 65536
MAX_MEMBERS = 128
HEX = re.compile(r"[0-9a-f]{64}\Z")


class BundleError(Exception):
    """固定錯誤代號，唔回傳來源路徑或任意 exception。"""


def require(condition, code="bundle_invalid"):
    if not condition:
        raise BundleError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_mode(name):
    return 0o700 if name.startswith("tools/kingdom-") or name == "tools/lantern-bootstrap.py" else 0o600


def header(name, size, mode):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, mode, 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info.tobuf(format=tarfile.USTAR_FORMAT)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


@contextmanager
def directory(path, *, create=False, private=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and path != Path("/"), "bundle_unsafe_path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(info.st_uid in (0, os.getuid()) and
                    (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)),
                    "bundle_unsafe_path")
            if private and index == len(path.parts) - 2:
                require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                        "bundle_unsafe_path")
        yield fd
    finally:
        os.close(fd)


def _read_at(fd, name, maximum, *, mode=None):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum,
            "bundle_unsafe_file")
    if mode is not None:
        require(before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == mode,
                "bundle_unsafe_file")
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        require(_stamp(os.fstat(handle)) == _stamp(before), "bundle_source_race")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(handle, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) == before.st_size and len(data) <= maximum, "bundle_source_race")
        require(_stamp(os.fstat(handle)) == _stamp(before) and
                _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) == _stamp(before), "bundle_source_race")
        return bytes(data)
    finally:
        os.close(handle)


def safe_read(path, maximum=MAX_FILE, *, mode=None):
    path = Path(path)
    with directory(path.parent) as fd:
        return _read_at(fd, path.name, maximum, mode=mode)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "bundle_duplicate_json")
        result[key] = value
    return result


def _manifest(data):
    require(len(data) <= MAX_MANIFEST, "bundle_manifest_limit")
    try:
        value = json.loads(data, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BundleError("bundle_invalid_json")))
    except (ValueError, UnicodeError, RecursionError):
        raise BundleError("bundle_invalid_json") from None
    require(type(value) is dict and set(value) == {"schema", "content_digest", "base_revision", "files"})
    require(value["schema"] == SCHEMA and type(value["content_digest"]) is str and HEX.fullmatch(value["content_digest"]))
    require(value["base_revision"] is None or (type(value["base_revision"]) is str and
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["base_revision"]) is not None))
    files = value["files"]
    require(type(files) is list and len(files) == len(BUNDLE_FILES) <= MAX_MEMBERS)
    names, total = [], 0
    for item in files:
        require(type(item) is dict and set(item) == {"path", "sha256", "size", "mode"})
        require(type(item["path"]) is str and item["path"] in BUNDLE_FILES)
        require(type(item["size"]) is int and 0 <= item["size"] <= MAX_FILE, "bundle_file_limit")
        require(type(item["mode"]) is int and item["mode"] == file_mode(item["path"]))
        require(type(item["sha256"]) is str and HEX.fullmatch(item["sha256"]))
        names.append(item["path"])
        total += item["size"]
    require(names == sorted(BUNDLE_FILES), "bundle_manifest_files")
    require(total <= MAX_TOTAL, "bundle_total_limit")
    require(hmac.compare_digest(sha(canonical(files)), value["content_digest"]), "bundle_content_mismatch")
    require(data == canonical(value), "bundle_noncanonical_manifest")
    return value


def verify_bytes(data, *, expected_sha256):
    """Checksum 只係完整性；expected SHA 必須由獨立可信渠道取得，唔係 signature。"""
    require(type(expected_sha256) is str and HEX.fullmatch(expected_sha256), "bundle_expected_sha_required")
    require(type(data) is bytes and 1024 <= len(data) <= MAX_ARCHIVE and len(data) % 512 == 0, "bundle_archive_limit")
    require(hmac.compare_digest(sha(data), expected_sha256), "bundle_sha_mismatch")
    entries, offset, total = {}, 0, 0
    allowed = set(BUNDLE_FILES) | {"MANIFEST.json"}
    # 逐個固定 USTAR header 驗；唔畀 tarfile 隱藏 pax／GNU extension／第二個 archive。
    while offset + 512 <= len(data):
        block = data[offset:offset + 512]
        if block == bytes(512):
            require(len(data) - offset >= 1024 and not any(data[offset:]), "bundle_trailing_data")
            break
        require(len(entries) < MAX_MEMBERS, "bundle_member_limit")
        try:
            info = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
        except (tarfile.TarError, UnicodeError, ValueError):
            raise BundleError("bundle_invalid_header") from None
        name = info.name
        require(name in allowed and name not in entries, "bundle_entry_refused")
        require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts)
        limit = MAX_MANIFEST if name == "MANIFEST.json" else MAX_FILE
        mode = 0o600 if name == "MANIFEST.json" else file_mode(name)
        require(info.type == tarfile.REGTYPE and 0 <= info.size <= limit, "bundle_entry_refused")
        require(block == header(name, info.size, mode), "bundle_noncanonical_header")
        start, end = offset + 512, offset + 512 + info.size
        offset = end + (-info.size % 512)
        require(offset <= len(data) and not any(data[end:offset]), "bundle_entry_truncated")
        entries[name] = data[start:end]
        total += info.size
        require(total <= MAX_TOTAL + MAX_MANIFEST, "bundle_total_limit")
    else:
        raise BundleError("bundle_missing_terminator")
    require(set(entries) == allowed, "bundle_missing_entries")
    value = _manifest(entries["MANIFEST.json"])
    for item in value["files"]:
        content = entries[item["path"]]
        require(len(content) == item["size"] and hmac.compare_digest(sha(content), item["sha256"]),
                "bundle_file_mismatch")
    return value, entries


def verify(bundle, *, expected_sha256):
    value, _ = verify_bytes(safe_read(bundle, MAX_ARCHIVE), expected_sha256=expected_sha256)
    return {"ok": True, "sha256": expected_sha256, "content_digest": value["content_digest"],
            "base_revision": value["base_revision"], "file_count": len(value["files"])}


@contextmanager
def _lock(fd):
    try:
        handle = os.open(".bootstrap.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        os.fchmod(handle, 0o600)
    except FileExistsError:
        handle = os.open(".bootstrap.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(handle)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0, "bundle_lock_unsafe")
        end = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < end, "bundle_lock_timeout")
                time.sleep(0.01)
        require(_stamp(os.stat(".bootstrap.lock", dir_fd=fd, follow_symlinks=False)) == _stamp(info),
                "bundle_lock_unsafe")
        yield
    finally:
        os.close(handle)


def _put(fd, name, data, mode):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    try:
        os.fchmod(handle, mode)
        offset = 0
        while offset < len(data):
            written = os.write(handle, data[offset:])
            require(written > 0, "bundle_copy_failed")
            offset += written
        os.fsync(handle)
    finally:
        os.close(handle)
    os.fsync(fd)
    require(_read_at(fd, name, len(data), mode=mode) == data, "bundle_copy_unverified")


def _verify_tree(root, entries):
    """唔忽略多出嚟嘅 module／links／mode drift，包括上一輪半裝 staging。"""
    expected_dirs = {str(parent) for name in entries for parent in PurePosixPath(name).parents if str(parent) != "."}
    seen_files, seen_dirs = set(), set()
    with directory(root, private=True):
        pass
    def walk(path, prefix=""):
        with directory(path, private=True) as fd:
            for leaf in os.listdir(fd):
                name = prefix + leaf
                info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                if name in expected_dirs and stat.S_ISDIR(info.st_mode):
                    seen_dirs.add(name)
                    walk(path / leaf, name + "/")
                else:
                    require(name in entries, "bundle_root_conflict")
                    mode = 0o600 if name == "MANIFEST.json" else file_mode(name)
                    require(_read_at(fd, leaf, len(entries[name]), mode=mode) == entries[name], "bundle_root_conflict")
                    seen_files.add(name)
    walk(Path(root))
    require(seen_files == set(entries) and seen_dirs == expected_dirs, "bundle_root_conflict")


def stage(bundle, *, expected_sha256, home):
    """全部驗好先 copy；有同 digest root 只核對，唔覆寫、唔 silent upgrade。"""
    require(sys.version_info >= (3, 11), "bundle_python_version")
    data = safe_read(bundle, MAX_ARCHIVE)
    value, entries = verify_bytes(data, expected_sha256=expected_sha256)
    home = Path(home)
    with directory(home) as home_fd:
        require(os.fstat(home_fd).st_uid == os.getuid(), "bundle_unsafe_path")
    parent = home / ".local/share/kingdom-lanterns"
    digest = value["content_digest"]
    root = parent / digest
    with directory(parent, create=True, private=True) as fd, _lock(fd):
        try:
            os.stat(digest, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            staging = ".staging-" + str(uuid.uuid4())
            os.mkdir(staging, 0o700, dir_fd=fd)
            temporary = parent / staging
            # 中斷只留 private staging，唔刪除不確定 leftovers。
            for name, content in sorted(entries.items()):
                target = temporary / name
                with directory(target.parent, create=True, private=True) as target_fd:
                    _put(target_fd, target.name, content, 0o600 if name == "MANIFEST.json" else file_mode(name))
            _verify_tree(temporary, entries)
            require(not root.exists() and not root.is_symlink(), "bundle_root_conflict")
            os.rename(staging, digest, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        _verify_tree(root, entries)
        os.fsync(fd)
    return {"ok": True, "action": "stage", "content_digest": digest, "sha256": expected_sha256,
            "root": str(root), "activation": "unverified"}


def install(bundle, *, expected_sha256, home):
    staged = stage(bundle, expected_sha256=expected_sha256, home=home)
    # 到呢一刻先執行永久已驗 root；固定 argv、唔走 PATH、唔從 staging import。
    command = [sys.executable, "-I", "-S", "-B", str(Path(staged["root"]) / "tools/kingdom-lanterns.py"),
               "--home", str(home), "install", "--json"]
    # 先由同一 owned installer preflight；唔公開會stage嘅「dry-run」操作。
    for dry_run in (True, False):
        child = subprocess.run(command + (["--dry-run"] if dry_run else []),
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                               env={**os.environ, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            require(len(child.stdout) <= MAX_MANIFEST, "bundle_install_output_limit")
            outcome = json.loads(child.stdout)
            require(type(outcome) is dict and outcome.get("action") == "install"
                    and outcome.get("dry_run") is dry_run, "bundle_install_unverified")
        except (ValueError, UnicodeError):
            raise BundleError("bundle_install_unverified") from None
        ok = child.returncode == 0 and outcome.get("ok") is True
        if not ok or not dry_run:
            return {**staged, "action": "install", "ok": ok, "installation": outcome}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "stage", "install"))
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--home", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.operation == "verify":
            result = verify(args.bundle.absolute(), expected_sha256=args.expected_sha256)
        else:
            result = (stage if args.operation == "stage" else install)(
                args.bundle.absolute(), expected_sha256=args.expected_sha256,
                home=Path.home() if args.home is None else args.home.absolute())
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result["ok"] else 1
    except Exception as exc:
        code = str(exc) if isinstance(exc, BundleError) else "bundle_operation_failed"
        print(json.dumps({"ok": False, "diagnostics": [code], "activation": "unverified"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
