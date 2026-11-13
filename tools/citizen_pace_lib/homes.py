from __future__ import annotations

# One citizen home on disk (spec sections 2 and 3). A home is public and is
# fast-forwarded from GitHub, so a committed symlink is untrusted: its pace
# files (CADENCE.md, REST, EVENT-ONLY, inbox/, the .seen-* markers, beats/) are
# read and written as regular files only, never through a link, and every
# write is atomic (a temporary file, then a rename). Also the few git facts
# the pace code asks of a home, each under a time bound, and never past a beat's
# own deadline (PACE_DEADLINE); a beat's git dies with the beat (die_with_caller).

import datetime as _dt
import errno
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .clock import UTC

LINK_FORBIDDEN = ("CADENCE.md", "REST", "EVENT-ONLY", "inbox", "beats")
TMP_SUFFIX = ".pace-tmp"
# a .seen- marker written by rollback-prepare (spec section 9, step 5): the new
# code reads its letter as unread, so the letter is still shown once
ROLLBACK_PREFIX = "set aside at rollback"
_MERGE_MARKERS = ("MERGE_HEAD", "rebase-merge", "rebase-apply")
# a caller's environment that would point git at another repository
_GIT_LOCATORS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
                 "GIT_PREFIX")
_TOKENS = ("GITHUB_TOKEN", "GH_TOKEN")
# a caller's environment that would change how every pathspec is read (an
# inherited GIT_LITERAL_PATHSPECS=1 turns ':(literal,icase)REST' into a file name)
_PATHSPEC_ENV = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
                 "GIT_ICASE_PATHSPECS")
# a beat's own deadline, in seconds since the epoch (citizen-reflect.sh sets it):
# no command run() starts outlives it
DEADLINE_ENV = "PACE_DEADLINE"
# the commands run() is running now, so a TERM to this process can stop them first
_RUNNING: set = set()


class LinkRefused(Exception):
    """A pace path is a symlink, or not a regular file: nothing is read or written through it."""


def _refuse_linked_parent(path: Path) -> None:
    if os.path.islink(path.parent):
        raise LinkRefused(f"{path.parent}: a symlink, refused")


def _refuse_non_regular(path: Path) -> None:
    """Refuse a link, a folder, a FIFO or any other non-regular file at path."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return
    if stat.S_ISLNK(st.st_mode):
        raise LinkRefused(f"{path}: a symlink, refused")
    if not stat.S_ISREG(st.st_mode):
        raise LinkRefused(f"{path}: not a regular file, refused")


def read_regular(path, limit: int = 1_000_000) -> Optional[str]:
    """The text of a regular file, at most `limit` bytes; None when it is missing.
    Raises LinkRefused for a link (even a dangling one), a file under a linked
    folder, or anything that is not a regular file; ValueError for bytes that
    are not UTF-8."""
    path = Path(path)
    _refuse_linked_parent(path)
    try:
        # O_NOFOLLOW refuses a link, O_NONBLOCK keeps a FIFO from hanging, fstat refuses the rest
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        if e.errno == errno.ELOOP or os.path.islink(path):
            raise LinkRefused(f"{path}: a symlink, refused") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LinkRefused(f"{path}: not a regular file, refused")
        chunks, size = [], 0
        while size < limit:
            b = os.read(fd, min(65536, limit - size))
            if not b:
                break
            chunks.append(b)
            size += len(b)
        truncated = size >= limit and os.read(fd, 1) != b""
    finally:
        os.close(fd)
    data = b"".join(chunks)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        if truncated and e.start >= len(data) - 3:
            return data[:e.start].decode("utf-8")   # the limit cut a letter in two
        raise


def _make_parents(d: Path) -> None:
    """Create the missing folders above a write, never through a link."""
    missing = []
    p = d
    while not os.path.lexists(p):
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    if os.path.islink(p) or not os.path.isdir(p):
        raise LinkRefused(f"{p}: not a plain folder, refused")
    for q in reversed(missing):
        os.mkdir(q)          # never follows: a link planted meanwhile makes this fail


def write_regular(path, text: str) -> None:
    """Write text atomically to a regular file (<path>.pace-tmp, then a rename).
    Refuses a link or a non-regular file at path, and a linked parent folder;
    creates missing parent folders that are not links."""
    path = Path(path)
    _make_parents(path.parent)
    _refuse_non_regular(path)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    if os.path.lexists(tmp):
        if os.path.isdir(tmp) and not os.path.islink(tmp):
            raise LinkRefused(f"{tmp}: a folder, refused")
        os.unlink(tmp)       # a planted link is removed, never followed
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        _refuse_non_regular(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_regular(path) -> None:
    """Remove a regular file; nothing to do when it is missing. Refuses a link."""
    path = Path(path)
    _refuse_linked_parent(path)
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return
    if stat.S_ISLNK(st.st_mode):
        raise LinkRefused(f"{path}: a symlink, refused")
    if not stat.S_ISREG(st.st_mode):
        raise LinkRefused(f"{path}: not a regular file, refused")
    os.unlink(path)


def _links_in(folder: Path, want) -> List[str]:
    try:
        st = os.lstat(folder)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    return [f"{folder.name}/{f}" for f in sorted(os.listdir(folder))
            if want(f) and os.path.islink(folder / f)]


def _wrong_kind(path: Path, folder: bool) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return not (stat.S_ISDIR(st.st_mode) if folder else stat.S_ISREG(st.st_mode))


def forbidden_links(home) -> List[str]:
    """The pace paths of a home that are symlinks, sorted: any of LINK_FORBIDDEN,
    an inbox/.seen-* marker, beats/.show-seen or a beats/*.md journal; also any of
    LINK_FORBIDDEN that is not of its kind (CADENCE.md, REST and EVENT-ONLY are
    regular files, inbox and beats folders). A home with any is never due. A
    linked letter is not listed: it is unreadable mail."""
    home = Path(home)
    out = [name for name in LINK_FORBIDDEN if _wrong_kind(home / name, name in ("inbox", "beats"))]
    out += _links_in(home / "inbox", lambda f: f.startswith(".seen-"))
    out += _links_in(home / "beats", lambda f: f == ".show-seen" or f.endswith(".md"))
    return sorted(out)


def has_soul(home, name: str) -> bool:
    """<name>.md is there and is a regular file, not a link."""
    try:
        return stat.S_ISREG(os.lstat(Path(home) / f"{name}.md").st_mode)
    except OSError:
        return False


def slug_of(home) -> str:
    """The citizen's name from its home folder 'citizen-<slug>'."""
    name = Path(home).name
    if not name.startswith("citizen-") or name == "citizen-":
        raise ValueError(f"{home}: not a citizen home (citizen-<slug>)")
    return name[len("citizen-"):]


def _seen(inbox: Path, f: str) -> bool:
    marker = inbox / f".seen-{f}"
    try:
        st = os.lstat(marker)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return True          # a link, even a dangling one, counts as seen, as has_mail
    try:
        text = read_regular(marker, limit=256)
    except (LinkRefused, ValueError, OSError):
        return True
    if text is None:
        return False
    return not text.startswith(ROLLBACK_PREFIX)


def unread_letters(home) -> List[str]:
    """'inbox/<f>' for each regular *.md letter without a .seen-<f> marker, sorted
    by name (oldest first, as names are date-prefixed). As has_mail in
    citizen-gates.sh: a linked letter is no letter, a linked inbox holds none, and
    a linked marker counts as seen; a rollback marker counts as unread."""
    inbox = Path(home) / "inbox"
    try:
        st = os.lstat(inbox)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    out = []
    for f in sorted(os.listdir(inbox)):
        if f.startswith(".") or not f.endswith(".md"):
            continue
        try:
            if not stat.S_ISREG(os.lstat(inbox / f).st_mode):
                continue
        except OSError:
            continue
        if not _seen(inbox, f):
            out.append(f"inbox/{f}")
    return out


def letter_time(home, rel: str, now: _dt.datetime) -> _dt.datetime:
    """When a letter arrived: the first commit that added it, else its file time;
    either is clamped to now. The name is read literally, never as a pattern."""
    r = git(home, "--literal-pathspecs", "log", "--diff-filter=A", "--format=%ct", "--", rel)
    times = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().isdigit()]
    if r.returncode == 0 and times:
        t = _dt.datetime.fromtimestamp(int(times[-1]), UTC)
    else:
        try:
            t = _dt.datetime.fromtimestamp(os.lstat(Path(home) / rel).st_mtime, UTC)
        except OSError:
            return now
    return min(t, now)


def beat_count(home) -> int:
    """The number of commits whose message has a line beginning 'beat:'; 0 if none."""
    r = git(home, "rev-list", "--count", "--grep=^beat:", "HEAD")
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else 0


def head(home) -> str:
    """HEAD's commit, or "" when there is none."""
    r = git(home, "rev-parse", "--verify", "-q", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def mid_merge(home) -> bool:
    """A merge or a rebase is in progress in this clone."""
    g = Path(home) / ".git"
    return any(os.path.lexists(g / m) for m in _MERGE_MARKERS)


def index_lock_age(home) -> Optional[float]:
    """Seconds since .git/index.lock was last touched (wall clock, not PACE_NOW),
    or None when there is none."""
    try:
        st = os.lstat(Path(home) / ".git" / "index.lock")
    except OSError:
        return None
    return max(0.0, time.time() - st.st_mtime)


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _TOKENS + _GIT_LOCATORS + _PATHSPEC_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _text(b) -> str:
    return b.decode("utf-8", "replace") if b else ""


def _stop_group(proc: subprocess.Popen) -> None:
    """TERM the command's process group (git removes its lock files), then KILL."""
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            if proc.poll() is None:
                proc.kill()
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def time_left() -> Optional[float]:
    """Seconds left before PACE_DEADLINE (a beat's own deadline), or None when
    none is set (or it is not a number of seconds since the epoch)."""
    raw = os.environ.get(DEADLINE_ENV, "").strip()
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", raw):
        return None
    return float(raw) - time.time()


def _stop_all_then_die(signum, _frame) -> None:
    for proc in tuple(_RUNNING):
        _stop_group(proc)
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def die_with_caller() -> None:
    """From now on a TERM, INT or HUP to this process first stops every command
    run() is running, its whole process group (TERM, then KILL), and then ends
    this process by that signal. run() starts each command in its own session,
    so without this a git outlives the beat the fleet stops with TERM to its
    process group. Call it from the main thread of a script (citizen_pace.py,
    citizen-reflect.sh's steps)."""
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _stop_all_then_die)


def run(cmd: List[str], *, timeout: float, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """cmd, with stdin closed, in its own process group, under timeout seconds
    and never past PACE_DEADLINE: at the bound the whole group is stopped and
    the result has returncode 124 (124 at once, and nothing run, when the
    deadline has passed). A command that cannot start gives 127. Output is text
    (UTF-8, undecodable bytes replaced). env: its environment (default this
    process's)."""
    name = os.path.basename(cmd[0])
    left = time_left()
    if left is not None:
        if left <= 0:
            return subprocess.CompletedProcess(cmd, 124, "", f"{name} not run: the beat's deadline has passed")
        timeout = min(timeout, left)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, start_new_session=True)
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", f"{name} could not start: {e}")
    _RUNNING.add(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(cmd, proc.returncode, _text(out), _text(err))
        except subprocess.TimeoutExpired:
            _stop_group(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
                for pipe in (proc.stdout, proc.stderr):
                    pipe.close()
            return subprocess.CompletedProcess(
                cmd, 124, _text(out), _text(err) + f"\n{name} timed out after {timeout:g}s")
    finally:
        _RUNNING.discard(proc)


def git(home, *args, check: bool = False, timeout: float = 30,
        env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """git -C <home> <args> through run() (its own process group, the time
    bound and the beat's deadline; 124 at the bound, 127 when git cannot
    start), with no prompt, no GITHUB_TOKEN or GH_TOKEN, no caller variable
    pointing at another repository, and none that changes how pathspecs are
    read. env: changes for this one call (a name set to a value, or to None
    to drop it). check=True raises CalledProcessError for any non-zero result."""
    cmd = ["git", "-C", str(home)] + [str(a) for a in args]
    use = _git_env()
    for k, v in (env or {}).items():
        if v is None:
            use.pop(k, None)
        else:
            use[k] = v
    result = run(cmd, timeout=timeout, env=use)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def branches_beyond(home, rev: str = "HEAD") -> Optional[List[Tuple[str, str]]]:
    """[(remote branch, its commit)] for each remote branch of a clone
    (refs/remotes/<remote>/<branch>; origin/HEAD and other pointers left
    out) whose history `rev` does not hold, in ref order; None when the refs
    cannot be read. A home with one is a history that goes on elsewhere."""
    r = git(home, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)", "refs/remotes/")
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        name, _sep, rest = line.partition("\0")
        oid, _sep, symref = rest.partition("\0")
        if symref or git(home, "merge-base", "--is-ancestor", oid, rev).returncode == 0:
            continue
        out.append((name[len("refs/remotes/"):], oid))
    return out
