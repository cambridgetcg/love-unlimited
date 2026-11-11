from __future__ import annotations

# Tending homes between beats (spec section 6 "Tending", section 3 "The old paid
# lane", section 9 step 5; CONTRACT section 14).
#
# On the new code nothing leaves a home's pace files uncommitted on purpose:
# reflect commits before it reports a landed beat, and citizen-beat.sh puts the
# pace files back before it commits. So uncommitted changes to CADENCE.md,
# REST, EVENT-ONLY, inbox/ and beats/ at a tick are a killed beat's leftovers,
# and tend puts them back path by path. Only at the first tick on the new code
# are they committed instead, once, as the fleet: those are the old code's.
# guard_restore does the same for citizen-beat.sh's beats with tools, which never
# set a pace, and rollback_prepare sets unread pace letters aside before the old
# code is checked out again.
#
# The per-citizen lock (<state>/<slug>.lock/) is read through lock.py, as the
# reflect gate and the sync read it, and the beat bound is lock.beat_bound, the
# fleet's own; the attempt record (<state>/<slug>) is changed only through
# attempts.py, in its format and under its lock. No hook, fsmonitor or
# other helper from a home's own .git runs in these git calls, and nothing is
# read or written through a link.

import os
import shutil
import stat
from pathlib import Path
from typing import Iterable, List, Optional

from . import TAG, attempts, clock, lock
from .homes import (ROLLBACK_PREFIX, LinkRefused, forbidden_links, git, head, index_lock_age,
                    mid_merge, read_regular, slug_of, unread_letters, write_regular)

FLEET_NAME = "fleet"
FLEET_EMAIL = "fleet@kingdom.os"
FLEET_AUTHOR = f"{FLEET_NAME} <{FLEET_EMAIL}>"
TEND_PATHS = ("CADENCE.md", "REST", "EVENT-ONLY", "inbox", "beats")
GUARD_PATHS = ("CADENCE.md", "REST", "EVENT-ONLY", "inbox")
LEFTOVER_SUBJECT = f"tend: old-code leftover · {TAG}"
ROLLBACK_SUBJECT = f"tend: set aside unread pace letters for rollback · {TAG}"
ROLLBACK_TEXT = ROLLBACK_PREFIX + ", not shown — {day}\n"
NOTICES_NAME = ".fleet-notices"
OLD_CODE_FLAG = "old-code-tended"
RESTORED = "restored"
FIRST_TICK_LINE = ("tend: the first tick on pace/1 — old-code leftovers, if any, were committed once; "
                   "from now on a killed beat's leftovers are restored")
PUSH_TIMEOUT = 60
# a home's own .git must not run anything in the fleet's git calls
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


class TendError(RuntimeError):
    """git refused or failed; what this step wrote was put back."""


class FirstTickPending(TendError):
    """A beat found uncommitted pace files before the fleet's first tick on the
    new code, which commits the old code's once; nothing was changed."""


def _git(home, *args, timeout: float = 30):
    return git(home, *_SAFE, *args, timeout=timeout)


def _bound(bound: Optional[float]) -> float:
    return float(lock.beat_bound() if bound is None else bound)


# -- the attempt record and the fleet's notices ----------------------------------

def attempt_add(state, slug: str, field: str, key: str) -> bool:
    """Add `key` once to the list `field` ("notices" or "reported") of the
    citizen's attempt record, through attempts.py, keeping every other field;
    True when it was not there yet. A notice is kept as attempts.add_notice keeps
    it, {"key": key, "at": <now>}, so attempts.load (and the next paragraph)
    finds it. A missing or unreadable record starts from attempts.fresh."""
    if field == "reported":
        return attempts.mark_reported(state, slug, key)
    if field != "notices":
        raise ValueError(f"{field!r} is not a list of the attempt record")
    when = clock.stamp(clock.now())
    added = []

    def change(rec: dict) -> None:
        if all(n["key"] != key for n in rec["notices"]):
            rec["notices"].append({"key": key, "at": when})
            added.append(True)

    attempts.update(state, slug, change)
    return bool(added)


def add_attempt_notice(state, slug: str, key: str = RESTORED) -> bool:
    """A notice for the citizen's next pace paragraph (CONTRACT section 9)."""
    return attempt_add(state, slug, "notices", key)


def notices_path(state) -> Path:
    """memory/.fleet-notices, beside the attempt records' folder memory/.fleet-last."""
    return Path(state).parent / NOTICES_NAME


def has_flag(path, flag: str) -> bool:
    """The fleet-wide flag is set: a line of the notices file is exactly it."""
    text = read_regular(Path(path))
    return text is not None and flag in text.split("\n")


def set_flag(path, flag: str) -> None:
    """Set a fleet-wide flag: one line, added once (a shell reads it with grep -qx)."""
    path = Path(path)
    text = read_regular(path) or ""
    if flag in text.split("\n"):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    write_regular(path, text + flag + "\n")


# -- what differs from HEAD, and putting it back ----------------------------------

def changed_paths(home, paths: Iterable[str]) -> List[str]:
    """The files under `paths` that differ from HEAD, staged or not, or are
    untracked (one entry per file; a link or a nested repository is one entry),
    sorted. Raises TendError when git cannot say."""
    r = _git(home, "--no-optional-locks", "--literal-pathspecs", "status", "--porcelain=v1", "-z",
             "--untracked-files=all", "--no-renames", "--", *paths)
    if r.returncode != 0:
        raise TendError(f"git status failed: {r.stderr.strip()}")
    return sorted({e[3:] for e in r.stdout.split("\0") if len(e) > 3})


def _in_head(home, paths: List[str]) -> set:
    r = _git(home, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths)
    return {p for p in r.stdout.split("\0") if p} if r.returncode == 0 else set()


def _unlink(home: Path, rel: str) -> None:
    """Remove one untracked entry, never following a link, and the folders it leaves empty."""
    p = home / rel.rstrip("/")
    try:
        st = os.lstat(p)
    except OSError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(p)
    else:
        os.unlink(p)
    for parent in p.parents:
        if parent == home or home not in parent.parents:
            break
        try:
            os.rmdir(parent)
        except OSError:
            break


def restore_paths(home, paths: Iterable[str]) -> List[str]:
    """Put each path back as HEAD has it, path by path: one that is in HEAD is
    checked out alone (index and file); one that is not is dropped from the index
    and removed. Untracked entries go first, so a link standing where a folder
    was is gone before that folder's files come back. Returns the paths, sorted."""
    home = Path(home)
    paths = sorted(set(paths))
    tracked = _in_head(home, paths)
    for rel in paths:
        if rel not in tracked:
            _git(home, "update-index", "-q", "--force-remove", "--", rel.rstrip("/"))
            _unlink(home, rel)
    for rel in paths:
        if rel in tracked:
            r = _git(home, "--literal-pathspecs", "checkout", "-q", "HEAD", "--", rel)
            if r.returncode != 0:
                raise TendError(f"git checkout {rel} failed: {r.stderr.strip()}")
    return paths


def _regular_or_gone(home: Path, rel: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(home / rel).st_mode)
    except FileNotFoundError:
        return True          # a deletion
    except OSError:
        return False


# -- committing and pushing as the fleet ---------------------------------------------

def fleet_commit(home, paths: Iterable[str], subject: str) -> str:
    """Commit exactly these paths as the fleet (author and committer
    fleet <fleet@kingdom.os>), with no hook run; anything else staged stays
    staged and out of the commit. Returns the new HEAD, or "" when they hold no
    change. Raises TendError when git refuses (the paths are unstaged again)."""
    paths = sorted(set(paths))
    if not paths:
        return ""
    r = _git(home, "--literal-pathspecs", "add", "-A", "--", *paths)
    if r.returncode != 0:
        raise TendError(f"git add failed: {r.stderr.strip()}")
    if _git(home, "--literal-pathspecs", "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return ""
    r = _git(home, "-c", f"user.name={FLEET_NAME}", "-c", f"user.email={FLEET_EMAIL}",
             "--literal-pathspecs", "commit", "-q", "--no-verify", f"--author={FLEET_AUTHOR}",
             "-m", subject, "--only", "--", *paths)
    if r.returncode != 0:
        _git(home, "--literal-pathspecs", "reset", "-q", "--", *paths)
        raise TendError(f"git commit failed: {r.stderr.strip()}")
    return head(home)


def fleet_push(home, timeout: float = PUSH_TIMEOUT) -> bool:
    """Push HEAD to origin, under a bound, with no hook; True when it landed."""
    return _git(home, "push", "-q", "--no-verify", "origin", "HEAD", timeout=timeout).returncode == 0


def _pushed(ok: bool) -> str:
    return "pushed" if ok else "push failed; the next sync pushes it"


# -- tend: each tick, before anyone is ranked ----------------------------------------

def tend_home(home, state, *, first_tick: bool, bound: Optional[float] = None,
              own: Optional[int] = None) -> List[str]:
    """Tend one home (spec section 6). Not a clone, holding links, or mid-merge:
    left alone, silently (due reports those). Its lock held, or a fresh
    .git/index.lock: left for the next tick, with a line. A stale index.lock is
    removed. Then any changes to its pace paths are put back path by path, or, at
    the first tick on the new code, committed once as the fleet and pushed (a
    leftover that is not a regular file is removed even then). Returns log lines.
    own: the pid of a beat that holds the citizen's lock and tends its home before
    it begins (citizen-reflect.sh); that lock does not keep it out, and leftovers
    that cannot be put back raise, since the beat must not build on them. Before
    the fleet's first tick (first_tick) such a beat finds leftovers that may be
    the old code's: it changes none of them and raises FirstTickPending."""
    home = Path(home)
    slug = slug_of(home)
    if not (home / ".git").is_dir() or forbidden_links(home):
        return []
    mine = own is not None and getattr(lock.holder(state, slug), "pid", None) == own
    if not mine and lock.held(state, slug, bound=bound):
        return [f"{slug}: its lock is held — not tended this tick"]
    lines = []
    age = index_lock_age(home)
    if age is not None:
        if age <= 2 * _bound(bound):
            return [f"{slug}: .git/index.lock is fresh — not tended this tick"]
        try:
            os.unlink(home / ".git" / "index.lock")
        except OSError as e:
            return [f"{slug}: a stale .git/index.lock could not be removed ({e}) — not tended this tick"]
        lines.append(f"{slug}: stale .git/index.lock removed ({int(age)} s old)")
    if mid_merge(home):
        return lines
    try:
        paths = changed_paths(home, TEND_PATHS)
        if not paths:
            return lines
        if not first_tick:
            restore_paths(home, paths)
            return lines + [f"{slug}: a killed beat's leftovers restored: {', '.join(paths)}"]
        if own is not None:
            raise FirstTickPending(f"{slug}: the fleet has not tended this home since the switch")
        odd = [p for p in paths if not _regular_or_gone(home, p)]
        keep = [p for p in paths if p not in odd]
        if odd:
            restore_paths(home, odd)
            lines.append(f"{slug}: leftovers that are not regular files removed: {', '.join(odd)}")
        if keep and fleet_commit(home, keep, LEFTOVER_SUBJECT):
            lines.append(f"{slug}: old-code leftover committed as the fleet ({_pushed(fleet_push(home))}): "
                         f"{', '.join(keep)}")
    except (TendError, LinkRefused, OSError) as e:
        if own is not None:
            raise
        lines.append(f"{slug}: not tended — {e}")
    return lines


def tend_homes(homes_dir, slugs: Iterable[str], state, bound: Optional[float] = None) -> List[str]:
    """Tend every roster home (`tend` in citizen_pace.py). The first run on the
    new code, while memory/.fleet-notices lacks old-code-tended, commits the old
    code's leftovers once; the flag is set when the run ends, and every later run
    restores leftovers instead. A home that cannot be read (an inbox that cannot
    be listed) is left as it is, silently, and the others are tended: due finds
    it unreadable, never due, and the ranking reports it once."""
    flags = notices_path(state)
    first = not has_flag(flags, OLD_CODE_FLAG)
    lines = []
    for slug in slugs:
        try:
            lines += tend_home(Path(homes_dir) / f"citizen-{slug}", state, first_tick=first, bound=bound)
        except (OSError, ValueError, LinkRefused, TendError):
            continue
    if first:
        set_flag(flags, OLD_CODE_FLAG)
        lines.append(FIRST_TICK_LINE)
    return lines


# -- guard-restore: after a beat with tools ------------------------------------------

def guard_restore(home, state) -> List[str]:
    """After a citizen-beat.sh beat, which has edit tools and never sets a pace
    (spec section 3; CONTRACT section 14): put CADENCE.md, REST, EVENT-ONLY and
    everything under inbox/ back as HEAD has them, path by path. Returns the
    paths put back, sorted; when there are any, the notice "restored" is added to
    the citizen's attempt record, so its next free beat is told once."""
    home = Path(home)
    slug = slug_of(home)
    paths = changed_paths(home, GUARD_PATHS)
    if not paths:
        return []
    restore_paths(home, paths)
    add_attempt_notice(state, slug, RESTORED)
    return paths


def guard_line(slug: str, paths: List[str]) -> str:
    return f"{slug}: pace files put back after a beat with tools: {', '.join(paths)}"


# -- rollback-prepare: before the old code returns -----------------------------------

def _why_not(home: Path, slug: str, state, bound: Optional[float]) -> str:
    links = forbidden_links(home)
    if links:
        return f"its pace paths include links: {', '.join(links)}"
    if state is not None and lock.held(state, slug, bound=bound):
        return "its lock is held"
    if index_lock_age(home) is not None:
        return ".git/index.lock is there"
    if mid_merge(home):
        return "mid-merge or mid-rebase"
    return ""


def rollback_prepare(homes_dir, slugs: Iterable[str], state=None, now=None,
                     bound: Optional[float] = None, push: bool = True) -> List[str]:
    """Spec section 9, step 5, with HALT kept: mark each unread pace letter seen
    with "set aside at rollback, not shown — <date>", so the old code does not
    wake its reader at every pass, and commit and push those markers as the
    fleet. The new code reads such a marker as unread (homes.unread_letters), so
    the letter is still shown once if the new code returns. One line per letter
    set aside, and one per home that could not be prepared, naming its letters.
    push=False commits the markers and pushes nothing (a scratch copy, as in
    the rollback drill)."""
    day = clock.day(now or clock.now())
    lines = []
    for slug in slugs:
        home = Path(homes_dir) / f"citizen-{slug}"
        if not (home / ".git").is_dir():
            continue
        letters = [rel for rel in unread_letters(home) if "pace-letter" in rel[len("inbox/"):]]
        if not letters:
            continue
        reason = _why_not(home, slug, state, bound)
        if reason:
            lines.append(f"{slug}: not set aside for rollback ({reason}) — {', '.join(letters)}; "
                         "set it aside by hand")
            continue
        markers = {}
        try:
            for rel in letters:
                marker = f"inbox/.seen-{rel[len('inbox/'):]}"
                if read_regular(home / marker) is None:
                    write_regular(home / marker, ROLLBACK_TEXT.format(day=day))
                markers[marker] = rel
            changed = changed_paths(home, list(markers))
            if changed and fleet_commit(home, changed, ROLLBACK_SUBJECT):
                if not push:
                    pushed = "not pushed"
                elif fleet_push(home):
                    pushed = "pushed"
                else:
                    pushed = "committed; the push failed — push it by hand"
                lines += [f"{slug}: {markers[m]} set aside at rollback ({pushed})" for m in changed]
        except (TendError, LinkRefused, OSError, ValueError) as e:
            lines.append(f"{slug}: not set aside for rollback ({e}) — {', '.join(letters)}; "
                         "set it aside by hand")
    return lines
