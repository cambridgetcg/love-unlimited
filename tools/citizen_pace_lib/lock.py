from __future__ import annotations

# The per-citizen lock (spec section 3, "Keeping the clock honest"; CONTRACT
# section 14): a folder <state>/<slug>.lock holding `pid`, `started` (a
# stamp) and `bound` (the holder's own beat bound, in seconds). citizen-reflect.sh and citizen-beat.sh take it before their gates, so
# a hand-run beside the fleet can never beat a citizen twice or confirm a
# proposal inside one beat; a second run finds it held and writes `busy`. Tend,
# the bring-up-to-date step and the heard-late step skip a home whose lock is
# held. A lock is stale, and is reclaimed, when its pid is gone or it is older
# than twice the beat bound: the larger of the holder's own and the taker's
# (FLEET_BEAT_BOUND_SEC, 600 s by default), so a holder with a longer bound
# (citizen-beat.sh's) is never reclaimed mid-beat.

import datetime as _dt
import errno
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from . import clock
from .attempts import record_path

DEFAULT_BOUND = 600
MIN_BOUND = 30
SUFFIX = ".lock"


def beat_bound(value: Optional[str] = None) -> int:
    """The beat bound in seconds, read as citizen-fleet.sh's num() reads
    FLEET_BEAT_BOUND_SEC, so the lock is judged by the bound the fleet enforces:
    anything but the digits 0-9 (a space included) counts as 0, and under 30 is 600."""
    raw = os.environ.get("FLEET_BEAT_BOUND_SEC", "") if value is None else str(value)
    n = int(raw) if re.fullmatch(r"[0-9]+", raw) else 0
    return DEFAULT_BOUND if n < MIN_BOUND else n


def lock_path(state, slug: str) -> Path:
    """<state>/<slug>.lock (the slug is checked as for the attempt record)."""
    p = record_path(state, slug)
    return p.with_name(p.name + SUFFIX)


@dataclass(frozen=True)
class Holder:
    pid: Optional[int]          # None when no pid is recorded (yet) or it is not a process number
    started: Optional[str]      # stamp, or None
    age_s: Optional[float]      # seconds since started (or since the folder was made)
    bound: Optional[int] = None  # the holder's own beat bound, None when none is recorded


@dataclass(frozen=True)
class Taken:
    got: bool
    reclaimed: str = ""                 # why a stale lock was reclaimed first, "" when none was
    holder: Optional[Holder] = None     # who holds it, when not got


def _read_small(path: Path) -> Optional[str]:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, 64).decode("utf-8", "replace").strip()
    finally:
        os.close(fd)


def _ident(d: Path) -> Tuple[Optional[int], Optional[str]]:
    """(pid, started) as recorded in a lock folder; (None, None) for anything else."""
    try:
        if not stat.S_ISDIR(os.lstat(d).st_mode):
            return None, None
    except OSError:
        return None, None
    text = _read_small(d / "pid") or ""
    pid = int(text) if re.fullmatch(r"[0-9]{1,9}", text) and int(text) > 0 else None
    started = _read_small(d / "started")
    try:
        started = clock.stamp(clock.parse_stamp(started)) if started else None
    except ValueError:
        started = None
    return pid, started


def _bound_of(d: Path) -> Optional[int]:
    """The bound a lock folder's holder recorded (1-9 digits), or None."""
    text = _read_small(d / "bound") or ""
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) and int(text) > 0 else None


def holder(state, slug: str, now: Optional[_dt.datetime] = None) -> Optional[Holder]:
    """Who holds the citizen's lock, or None when there is no lock. A path that is
    not a plain folder (a stray file or a link) is a lock with no holder recorded."""
    d = lock_path(state, slug)
    try:
        st = os.lstat(d)
    except OSError:
        return None
    pid, started = _ident(d)
    if started:
        age = ((now or clock.now()) - clock.parse_stamp(started)).total_seconds()
    else:
        age = time.time() - st.st_mtime
    return Holder(pid, started, max(0.0, age), _bound_of(d) if stat.S_ISDIR(st.st_mode) else None)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OverflowError:
        return True          # not a process number this system knows: judged by age
    except OSError:
        return True          # it exists, but is not ours to signal
    return True


def stale_reason(h: Optional[Holder], bound: Optional[int] = None) -> str:
    """Why a lock is stale ("pid N is gone", "older than S s"), or "" when it is
    held. Its age is judged against twice the larger of the holder's own bound
    and this taker's (`bound`, else beat_bound())."""
    if h is None:
        return ""
    limit = 2 * max(beat_bound() if bound is None else bound, h.bound or 0)
    if h.pid is not None and not _alive(h.pid):
        return f"pid {h.pid} is gone"
    if h.age_s is not None and h.age_s > limit:
        return f"older than {limit} s"
    return ""


def held(state, slug: str, now: Optional[_dt.datetime] = None, bound: Optional[int] = None) -> bool:
    """A live holder that is not stale holds the lock."""
    h = holder(state, slug, now)
    return h is not None and not stale_reason(h, bound)


def _discard(p: Path) -> None:
    try:
        if stat.S_ISDIR(os.lstat(p).st_mode):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.unlink(p)
    except OSError:
        pass


def _reclaim(d: Path, seen: Holder) -> bool:
    """Move a stale lock aside and remove it. If what was moved is not the lock
    judged stale (another run took the path meanwhile), put it back."""
    grave = d.with_name(f"{d.name}.stale-{os.getpid()}-{time.time_ns()}")
    try:
        os.rename(d, grave)
    except OSError:
        return False
    if _ident(grave) != (seen.pid, seen.started):
        try:
            os.rename(grave, d)
        except OSError:
            _discard(grave)
        return False
    _discard(grave)
    return True


def _write_new(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def take(state, slug: str, pid: int, now: Optional[_dt.datetime] = None,
         bound: Optional[int] = None) -> Taken:
    """Take the citizen's lock for pid, recording `bound` (else beat_bound())
    as the holder's own. A stale lock is reclaimed first (the reason is
    returned, for the log); a held one is not taken."""
    if type(pid) is not int or pid <= 0:
        raise ValueError(f"{pid!r} is not a process number")
    when = now or clock.now()
    d = lock_path(state, slug)
    os.makedirs(str(state), exist_ok=True)
    reclaimed = ""
    for _ in range(2):
        try:
            os.mkdir(d)
        except FileExistsError:
            h = holder(state, slug, when)
            reason = stale_reason(h, bound)
            if not reason or reclaimed or not _reclaim(d, h):
                return Taken(False, reclaimed, h)
            reclaimed = reason
            continue
        try:
            _write_new(d / "pid", f"{pid}\n")
            _write_new(d / "started", f"{clock.stamp(when)}\n")
            _write_new(d / "bound", f"{beat_bound() if bound is None else int(bound)}\n")
        except OSError as e:
            if e.errno != errno.ENOENT:      # the folder is still ours: leave no half-made lock
                _discard(d)
            return Taken(False, reclaimed, holder(state, slug, when))
        return Taken(True, reclaimed)
    return Taken(False, reclaimed, holder(state, slug, when))


def release(state, slug: str, pid: Optional[int] = None) -> bool:
    """Remove the lock; with pid, only when that pid holds it. True when removed."""
    d = lock_path(state, slug)
    if not os.path.lexists(d):
        return False
    if pid is not None and _ident(d)[0] != pid:
        return False
    _discard(d)
    return True
