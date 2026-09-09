"""有界工作樹觀察；只交出 keyed hashes，唔承諾兩次觀察之間冇變過。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import tempfile
import time

from lantern_common.paths import is_secret_path

MAX_SECONDS = 2.0
MAX_PATHS = 10_000
MAX_BYTES = 64 * 1024 * 1024
MAX_GIT_OUTPUT = 4 * 1024 * 1024
MAX_SCOPE_SECONDS = 0.025
MAX_SCOPE_DEPTH = 128
GIT_EXECUTABLE = "/usr/bin/git"
_CHUNK = 64 * 1024
_PUBLIC_CODES = {
    "deadline": "deadline_exceeded", "race": "snapshot_race",
    "unsafe_file": "unsafe_path", "hardlink": "unsafe_path",
    "special_file": "unsupported_file", "metadata_limit": "byte_limit",
    "unsupported_git": "unsupported", "nested_repository": "unsupported",
    "submodule": "unsupported_submodule", "git_busy": "snapshot_incomplete",
    "unreadable": "read_failed",
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class _Incomplete(Exception):
    """只攜帶固定 code；唔帶原 exception／path。"""


def _digest(key, domain, value):
    # Core 由共同 owner 提供；runtime import 唔強迫 import 次序。
    from .core import keyed_digest
    return keyed_digest(key, domain, value)


def _canonical(path):
    return Path(path).resolve(strict=False)


def scope_candidates(path: Path, *, key: bytes) -> list[str]:
    """只解 locator／lstat .git boundary；唔掃目錄、唔跑 Git、唔建立 scope。"""
    from .core import RealityError
    end = time.monotonic() + MAX_SCOPE_SECONDS
    try:
        path = _canonical(path)
        result = []
        for depth, candidate in enumerate((path, *path.parents)):
            if depth >= MAX_SCOPE_DEPTH or time.monotonic() >= end:
                return []
            try:
                marker = _lstat(candidate / ".git")
            except FileNotFoundError:
                marker = None
            except NotADirectoryError:
                if not stat.S_ISREG(_lstat(candidate).st_mode):
                    return []
                marker = None
            if marker is not None:
                _safe(marker, directory=stat.S_ISDIR(marker.st_mode))
            result.append(_digest(key, "path", str(candidate)))
            if marker is not None:
                # 新 nested repo／worktree 冇收據，都唔可以向上借 parent 綠燈。
                break
        return result if time.monotonic() < end else []
    except (OSError, ValueError, RuntimeError, RealityError, _Incomplete):
        return []


class _Budget:
    def __init__(self, deadline):
        now = time.monotonic()
        self.deadline = min(now + MAX_SECONDS, deadline) if deadline is not None else now + MAX_SECONDS
        self.bytes = 0
        self.paths = 0
        self.seen_paths = set()
        self.git_output = 0
        self.diagnostics = set()

    def check(self):
        if time.monotonic() >= self.deadline:
            raise _Incomplete("deadline")

    def consume(self, amount):
        self.check()
        if amount > MAX_BYTES - self.bytes:
            raise _Incomplete("byte_limit")
        self.bytes += amount

    def count(self, count):
        self.check()
        if count > MAX_PATHS:
            raise _Incomplete("path_limit")
        self.paths = max(self.paths, count)

    def visit(self, path):
        self.check()
        if path not in self.seen_paths:
            self.count(len(self.seen_paths) + 1)
            self.seen_paths.add(path)


def _metadata(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_uid,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _identity(st):
    return [st.st_dev, st.st_ino]


def _safe(st, *, directory=False):
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise _Incomplete("unsafe_file")
    if directory:
        if not stat.S_ISDIR(st.st_mode):
            raise _Incomplete("unsafe_path")
    elif not stat.S_ISREG(st.st_mode):
        raise _Incomplete("special_file")
    elif st.st_nlink != 1:
        raise _Incomplete("hardlink")


def _secret(path):
    # 同 Coedit 共用純 lexical 政策；唔讀內容去猜係咪 secret。
    return is_secret_path(path)


def _open_dir(path):
    # 每個 component 都 O_NOFOLLOW；唔靠先 lstat 再普通 open。
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise _Incomplete("unsafe_path")
    fd = os.open(path.anchor, _DIRECTORY)
    try:
        for part in path.parts[1:]:
            following = os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def _parent(path):
    return _open_dir(Path(path).parent)


def _lstat(path):
    parent = _parent(path)
    try:
        return os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def _read_regular(path, budget, *, limit=None, digest=False):
    budget.check()
    parent = _parent(path)
    fd = None
    try:
        name = Path(path).name
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _safe(before)
        if limit is not None and before.st_size > limit:
            raise _Incomplete("metadata_limit")
        if before.st_size > MAX_BYTES - budget.bytes:
            raise _Incomplete("byte_limit")
        fd = os.open(name, _FILE, dir_fd=parent)
        opened = os.fstat(fd)
        _safe(opened)
        if _metadata(before) != _metadata(opened):
            raise _Incomplete("race")
        chunks = []
        hasher = hashlib.sha256()
        total = 0
        while True:
            budget.check()
            chunk = os.read(fd, min(_CHUNK, MAX_BYTES - budget.bytes + 1))
            if not chunk:
                break
            budget.consume(len(chunk))
            total += len(chunk)
            if limit is not None and total > limit:
                raise _Incomplete("metadata_limit")
            if digest:
                hasher.update(chunk)
            else:
                chunks.append(chunk)
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (_metadata(before) != _metadata(os.fstat(fd))
                or _metadata(before) != _metadata(after) or total != before.st_size):
            raise _Incomplete("race")
        return (hasher.hexdigest() if digest else b"".join(chunks)), _metadata(before)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _optional(path, budget, *, limit=16 * 1024 * 1024):
    try:
        return _read_regular(path, budget, limit=limit)
    except FileNotFoundError:
        return None, None


def _normalize(base, value):
    value = os.fsdecode(value)
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise _Incomplete("unsupported_git")
    return Path(os.path.abspath(os.path.join(base, value)))


def _discover(cwd, budget):
    for root in (cwd, *cwd.parents):
        budget.check()
        try:
            marker = _lstat(root / ".git")
        except FileNotFoundError:
            continue
        marker_data = None
        if stat.S_ISDIR(marker.st_mode):
            _safe(marker, directory=True)
            gitdir = root / ".git"
        elif stat.S_ISREG(marker.st_mode):
            marker_data, _ = _read_regular(root / ".git", budget, limit=8192)
            if not marker_data.startswith(b"gitdir: ") or not marker_data.endswith(b"\n"):
                raise _Incomplete("unsupported_git")
            gitdir = _normalize(root, marker_data[8:-1])
        else:
            raise _Incomplete("unsafe_path")
        if _secret(gitdir):
            raise _Incomplete("credential_path")
        gitfd = _open_dir(gitdir)
        try:
            gitst = os.fstat(gitfd)
            _safe(gitst, directory=True)
        finally:
            os.close(gitfd)
        common_data, common_meta = _optional(gitdir / "commondir", budget, limit=8192)
        common = _normalize(gitdir, common_data.rstrip(b"\n")) if common_data is not None else gitdir
        if _secret(common):
            raise _Incomplete("credential_path")
        commonfd = _open_dir(common)
        try:
            commonst = os.fstat(commonfd)
            _safe(commonst, directory=True)
            rootfd = _open_dir(root)
            try:
                rootst = os.fstat(rootfd)
                _safe(rootst, directory=True)
            finally:
                os.close(rootfd)
        finally:
            os.close(commonfd)
        try:
            _lstat(common / "reftable")
        except FileNotFoundError:
            pass
        else:
            raise _Incomplete("unsupported_git")
        for required in (common / "objects", common / "refs"):
            requiredfd = _open_dir(required)
            try:
                _safe(os.fstat(requiredfd), directory=True)
            finally:
                os.close(requiredfd)
        identity = [str(root), _identity(rootst), str(gitdir), _identity(gitst),
                    str(common), _identity(commonst)]
        guard = [identity, _metadata(marker), marker_data, common_data, common_meta]
        return root, gitdir, common, identity, guard
    raise _Incomplete("not_git")


def _valid_path(raw):
    if len(raw) > 4096 or raw.count(b"/") > 128:
        raise _Incomplete("unsupported_index")
    if not raw or raw.startswith(b"/") or b"\x00" in raw:
        raise _Incomplete("unsupported_index")
    if any(part in (b"", b".", b"..") or part.lower() == b".git" for part in raw.split(b"/")):
        raise _Incomplete("unsupported_index")
    return os.fsdecode(raw)


def _parse_index(data, budget):
    if data is None:
        return []
    if len(data) < 32 or data[:4] != b"DIRC":
        raise _Incomplete("unsupported_index")
    version, count = struct.unpack_from("!II", data, 4)
    if version not in (2, 3):
        raise _Incomplete("unsupported_index")
    budget.count(count)
    if hashlib.sha1(data[:-20]).digest() == data[-20:]:
        oid_size = 20
    elif len(data) >= 44 and hashlib.sha256(data[:-32]).digest() == data[-32:]:
        oid_size = 32
    else:
        raise _Incomplete("unsupported_index")
    end, offset, entries = len(data) - oid_size, 12, []
    for _ in range(count):
        budget.check()
        start = offset
        if offset + 42 + oid_size >= end:
            raise _Incomplete("unsupported_index")
        mode = struct.unpack_from("!I", data, offset + 24)[0]
        oid = data[offset + 40:offset + 40 + oid_size].hex()
        flags = struct.unpack_from("!H", data, offset + 40 + oid_size)[0]
        offset += 42 + oid_size
        if flags & 0xC000 or flags & 0x3000:
            # assume-unchanged、extended／skip-worktree、conflict 全部保守 unknown。
            raise _Incomplete("unsupported_index")
        if mode == 0o160000:
            raise _Incomplete("submodule")
        if mode not in (0o100644, 0o100755, 0o120000):
            raise _Incomplete("unsupported_index")
        stop = data.find(b"\0", offset, end)
        if stop < 0 or (flags & 0xFFF != 0xFFF and flags & 0xFFF != stop - offset):
            raise _Incomplete("unsupported_index")
        path = _valid_path(data[offset:stop])
        budget.visit(path)
        offset = start + ((stop + 1 - start + 7) // 8) * 8
        if offset > end or any(data[stop:offset]):
            raise _Incomplete("unsupported_index")
        entries.append([path, mode, oid])
    if len({entry[0] for entry in entries}) != len(entries):
        raise _Incomplete("unsupported_index")
    while offset < end:
        if offset + 8 > end:
            raise _Incomplete("unsupported_index")
        name = data[offset:offset + 4]
        size = struct.unpack_from("!I", data, offset + 4)[0]
        if name not in (b"TREE", b"REUC", b"EOIE", b"IEOT"):
            raise _Incomplete("unsupported_index")
        offset += 8 + size
        if offset > end:
            raise _Incomplete("unsupported_index")
    return entries


def _git_state(gitdir, common, budget):
    for base, name in ((gitdir, "index.lock"), (gitdir, "HEAD.lock"), (common, "packed-refs.lock")):
        try:
            _lstat(base / name)
        except FileNotFoundError:
            continue
        raise _Incomplete("git_busy")
    head, headmeta = _read_regular(gitdir / "HEAD", budget, limit=8192)
    refdata, refmeta = None, None
    if re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\n?", head):
        resolved = head.strip().decode("ascii")
    elif head.startswith(b"ref: "):
        ref = head[5:].rstrip(b"\n")
        if (not re.fullmatch(rb"refs/(?:heads|tags)/[A-Za-z0-9_./-]+", ref)
                or b".." in ref or ref.endswith((b"/", b".lock")) or b"//" in ref):
            raise _Incomplete("unsupported_git")
        try:
            refdata, refmeta = _optional(common / os.fsdecode(ref), budget, limit=8192)
        except FileNotFoundError:
            refdata, refmeta = None, None
        if refdata is not None:
            if not re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\n?", refdata):
                raise _Incomplete("unsupported_git")
            resolved = refdata.strip().decode("ascii")
        else:
            refdata, refmeta = _optional(common / "packed-refs", budget)
            resolved = None
            if refdata is not None:
                for line in refdata.splitlines():
                    budget.check()
                    if line.startswith((b"#", b"^")) or not line:
                        continue
                    oid, separator, name = line.partition(b" ")
                    if not separator or not re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?", oid):
                        raise _Incomplete("unsupported_git")
                    if name == ref:
                        resolved = oid.decode("ascii")
    else:
        raise _Incomplete("unsupported_git")
    index, indexmeta = _optional(gitdir / "index", budget)
    entries = _parse_index(index, budget)
    exclude, excludemeta = _optional(common / "info" / "exclude", budget)
    value = [head.hex(), resolved, entries, hashlib.sha256(exclude or b"").hexdigest()]
    guard = [head, headmeta, refdata, refmeta, index, indexmeta, exclude, excludemeta]
    return entries, exclude, value, guard


def _git_environment(shadow):
    # 唔繼承 GIT_CONFIG_COUNT、PATH wrapper、HOME config、askpass 或動態 loader。
    return {
        "PATH": "/usr/bin:/bin", "HOME": str(shadow), "XDG_CONFIG_HOME": str(shadow),
        "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_ALLOW_PROTOCOL": "", "GIT_PAGER": "", "GIT_EXEC_PATH": "/usr/libexec/git-core",
    }


def _git(args, *, shadow, budget, payload):
    budget.check()
    command = [GIT_EXECUTABLE, "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
               "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
               "-c", "core.excludesFile=/dev/null", "-c", "protocol.allow=never",
               "-c", "maintenance.auto=false", "-c", "gc.auto=0",
               "-c", "core.attributesFile=/dev/null", "-c", "diff.external=",
               "--git-dir=" + str(shadow / ".git"), "--work-tree=" + str(shadow), *args]
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(command, cwd=shadow, env=_git_environment(shadow),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, start_new_session=True)
    except BaseException:
        selector.close()
        raise
    output = bytearray()
    cursor = 0
    try:
        for stream, event in ((process.stdin, selectors.EVENT_WRITE),
                              (process.stdout, selectors.EVENT_READ), (process.stderr, selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event)
        while selector.get_map():
            budget.check()
            for item, _ in selector.select(max(0, budget.deadline - time.monotonic())):
                stream = item.fileobj
                if stream is process.stdin:
                    try:
                        cursor += os.write(stream.fileno(), payload[cursor:cursor + _CHUNK])
                    except BrokenPipeError:
                        cursor = len(payload)
                    if cursor == len(payload):
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), _CHUNK)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    budget.git_output += len(chunk)
                    if budget.git_output > MAX_GIT_OUTPUT:
                        raise _Incomplete("git_output_limit")
                    if stream is process.stdout:
                        output.extend(chunk)
        budget.check()
        try:
            code = process.wait(timeout=max(0, budget.deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise _Incomplete("deadline") from None
        if code not in (0, 1):
            raise _Incomplete("git_failed")
        return bytes(output)
    finally:
        selector.close()
        # 就算 leader 已退出，亦收埋同 process group 嘅 child，然後 reap leader。
        try:
            process.poll()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except PermissionError:
                # 部分 sandbox 對已退出／受保護 process group 回 EPERM。
                if process.poll() is None:
                    process.kill()
            except ProcessLookupError:
                pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            process.wait()


def _make_shadow(path, exclude):
    gitdir = path / ".git"
    (gitdir / "objects").mkdir(parents=True)
    (gitdir / "refs").mkdir()
    (gitdir / "info").mkdir()
    (gitdir / "HEAD").write_bytes(b"ref: refs/heads/reality\n")
    if exclude is not None:
        (gitdir / "info" / "exclude").write_bytes(exclude)


def _layout(root, tracked, shadow, budget):
    # Git 只見到安全複本嘅 ignore rules；真 repo config／objects 完全唔入 helper。
    pending = [""]
    found, directories, ignores = {}, {}, {}
    traversed = 0
    tracked_parents = set()
    for path in tracked:
        budget.check()
        for parent in Path(path).parents:
            budget.check()
            if str(parent) != ".":
                tracked_parents.add(str(parent))
    while pending:
        children = []
        for relative in pending:
            budget.check()
            directory = root / relative
            fd = _open_dir(directory)
            try:
                before = os.fstat(fd)
                _safe(before, directory=True)
                directories[relative] = _metadata(before)
                names = []
                with os.scandir(fd) as iterator:
                    for entry in iterator:
                        budget.check()
                        if entry.name.casefold() == ".git":
                            if relative or entry.name != ".git":
                                budget.diagnostics.add("nested_repository")
                            continue
                        traversed += 1
                        budget.visit(str(Path(relative) / entry.name))
                        budget.count(max(len(tracked), traversed))
                        names.append(entry.name)
                ignore_path = directory / ".gitignore"
                try:
                    ignore_stat = os.stat(".gitignore", dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    ignore_stat = None
                if ignore_stat is not None:
                    if stat.S_ISLNK(ignore_stat.st_mode):
                        # Git 都唔跟隨 symlink ignore rules。
                        pass
                    else:
                        # 直接 lookup 同真 FS 一致；case-insensitive FS 嘅 .GITIGNORE 都生效。
                        content, meta = _read_regular(ignore_path, budget)
                        ignores[str(Path(relative) / ".gitignore")] = meta
                        (shadow / relative / ".gitignore").write_bytes(content)
                for name in names:
                    relative_path = str(Path(relative) / name)
                    current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if _secret(relative_path):
                        budget.diagnostics.add("credential_path")
                        found[relative_path] = _metadata(current)
                        continue
                    is_dir = stat.S_ISDIR(current.st_mode)
                    children.append((relative_path, _metadata(current), is_dir))
                if _metadata(before) != _metadata(os.fstat(fd)):
                    raise _Incomplete("race")
            finally:
                os.close(fd)
        ignored = set()
        if children:
            payload = b"".join(os.fsencode(path + ("/" if is_dir else "")) + b"\0"
                               for path, _, is_dir in children)
            for ignorecase in ("false", "true"):
                output = _git(["-c", "core.ignorecase=" + ignorecase,
                               "check-ignore", "--no-index", "-z", "--stdin"],
                              shadow=shadow, budget=budget, payload=payload)
                if output and not output.endswith(b"\0"):
                    raise _Incomplete("git_failed")
                current_ignored = {os.fsdecode(path).rstrip("/") for path in output.split(b"\0") if path}
                if not current_ignored.issubset({path for path, _, _ in children}):
                    raise _Incomplete("git_failed")
                # 唔讀 repo config；兩種 case 語義都忽略先排除，negation 都唔漏。
                ignored = current_ignored if ignorecase == "false" else ignored & current_ignored
        pending = []
        for path, meta, is_dir in children:
            budget.check()
            if path in ignored and path not in tracked and path not in tracked_parents:
                continue
            if is_dir:
                _safe(_lstat(root / path), directory=True)
                (shadow / path).mkdir(exist_ok=True)
                pending.append(path)
            else:
                found[path] = meta
    budget.count(len(set(found) | set(tracked)))
    return found, directories, ignores


def _read_content(path, expected, budget):
    if stat.S_ISLNK(expected[2]):
        parent = _parent(path)
        try:
            name = Path(path).name
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _metadata(before) != expected:
                raise _Incomplete("race")
            target = os.fsencode(os.readlink(name, dir_fd=parent))
            budget.consume(len(target))
            if _metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected:
                raise _Incomplete("race")
            return "symlink", hashlib.sha256(target).hexdigest()
        finally:
            os.close(parent)
    value, meta = _read_regular(path, budget, digest=True)
    if meta != expected:
        raise _Incomplete("race")
    return "file", value


def _fold(key, domain, records, budget):
    # 每筆先 HMAC，再摺成固定大小；唔突破 core canonical JSON 單筆上限。
    hasher = hashlib.sha256()
    for record in records:
        budget.check()
        path, *rest = record
        hasher.update(bytes.fromhex(_digest(key, domain, [os.fsencode(path).hex(), *rest])))
    return hasher.hexdigest()


def capture(cwd: Path, *, key: bytes, deadline: float | None = None) -> dict:
    """觀察 HEAD/index、tracked 同安全 non-ignored 輸入；不足就 null fingerprint。"""
    from .core import SCHEMA_VERSION, SNAPSHOT_CODES, RealityError, utc_now
    budget = _Budget(deadline)
    fallback_id = _digest(key, "path", str(Path(cwd).absolute()))
    result = {"schema": SCHEMA_VERSION, "scope_id": fallback_id, "worktree_id": None,
              "cwd_id": fallback_id, "fingerprint": None, "complete": False,
              "observed_at": utc_now(), "diagnostics": [],
              "coverage": {"paths": 0, "bytes": 0, "ignored": "excluded",
                           "environment": "unverified", "remote": "unverified"}}
    try:
        cwd = _canonical(cwd)
        result["cwd_id"] = result["scope_id"] = _digest(key, "path", str(cwd))
        budget.check()
        if _secret(cwd):
            raise _Incomplete("credential_path")
        root, gitdir, common, identity, discovery = _discover(cwd, budget)
        result["scope_id"] = _digest(key, "path", str(root))
        result["worktree_id"] = _digest(key, "worktree", identity)
        entries, exclude, gitvalue, gitguard = _git_state(gitdir, common, budget)
        tracked = {entry[0] for entry in entries}
        with tempfile.TemporaryDirectory(prefix="reality-snapshot-") as scratch:
            shadow = Path(scratch)
            _make_shadow(shadow, exclude)
            before = _layout(root, tracked, shadow, budget)
            records = []
            for relative in sorted(tracked | set(before[0])):
                budget.check()
                if _secret(relative):
                    budget.diagnostics.add("credential_path")
                    continue
                expected = before[0].get(relative)
                if expected is None:
                    if relative in before[1]:
                        raise _Incomplete("special_file")
                    # 清單之外嘅 tracked path 要再查，唔將 symlink ancestor 當刪除。
                    try:
                        _lstat(root / relative)
                    except FileNotFoundError:
                        pass
                    else:
                        raise _Incomplete("race")
                    records.append([relative, "deleted"])
                    continue
                try:
                    kind, content = _read_content(root / relative, expected, budget)
                except _Incomplete as exc:
                    if str(exc) not in {"hardlink", "unsafe_file", "special_file"}:
                        raise
                    budget.diagnostics.add(str(exc))
                    continue
                records.append([relative, kind, stat.S_IMODE(expected[2]), content])
            value = None
            if not budget.diagnostics:
                head, resolved, index_entries, exclude_hash = gitvalue
                index_hash = _fold(key, "index-entry", index_entries, budget)
                content_hash = _fold(key, "file-entry", records, budget)
                value = _digest(key, "snapshot", [result["worktree_id"], head, resolved,
                                                   index_hash, exclude_hash, content_hash])
            # 第二個獨立 shadow，避免已刪 ignore rule 留喺第一份複本影響清單。
            with tempfile.TemporaryDirectory(prefix="reality-snapshot-") as scratch_after:
                shadow_after = Path(scratch_after)
                _make_shadow(shadow_after, exclude)
                after = _layout(root, tracked, shadow_after, budget)
            _, _, _, after_guard = _git_state(gitdir, common, budget)
            after_discovery = _discover(cwd, budget)[4]
            if before != after or gitguard != after_guard or discovery != after_discovery:
                raise _Incomplete("race")
            # 慢工序完成後再核對 metadata；唔將較早讀到嘅檔案冒認為原子快照。
            for relative, expected in {**before[1], **before[0], **before[2]}.items():
                budget.check()
                if _metadata(_lstat(root / relative)) != expected:
                    raise _Incomplete("race")
            budget.check()
            if value is not None and not budget.diagnostics:
                result["fingerprint"] = value
                result["complete"] = True
        budget.check()
    except _Incomplete as exc:
        budget.diagnostics.add(str(exc))
    except (OSError, ValueError, RuntimeError, struct.error, RealityError):
        budget.diagnostics.add("unreadable")
    result["observed_at"] = utc_now()
    codes = {_PUBLIC_CODES.get(code, code) for code in budget.diagnostics}
    result["diagnostics"] = sorted({code if code in SNAPSHOT_CODES else "snapshot_failed" for code in codes})
    if codes:
        result["complete"] = False
        result["fingerprint"] = None
    result["coverage"]["paths"] = budget.paths
    result["coverage"]["bytes"] = budget.bytes
    return result
