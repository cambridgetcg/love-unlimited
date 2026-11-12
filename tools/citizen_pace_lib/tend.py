from __future__ import annotations

# Tending homes between beats (spec section 6 "Tending", section 3 "The old paid
# lane", section 9 step 5; CONTRACT section 14).
#
# On the new code nothing leaves a home's pace files uncommitted on purpose:
# reflect commits before it reports a landed beat, and citizen-beat.sh puts the
# pace files back before it commits. So uncommitted changes to CADENCE.md,
# REST, EVENT-ONLY, inbox/ and beats/ at a tick are a killed beat's leftovers,
# and tend puts them back path by path. Only at a home's first full tend on the
# new code are they committed instead, once, as the fleet: those are the old
# code's. That first tend is a fact of each home, kept in its attempt record, so
# a home skipped at the fleet's first tick keeps its leftovers until its own.
# An untracked regular file under inbox/ or beats/ is never deleted: it may be a
# letter a person placed, so it is moved out of the home, into
# <state>/.set-aside/<slug>/, and named in the log.
# guard_restore does the same for citizen-beat.sh's beats with tools, which never
# set a pace, against the commit before the beat; when that beat committed
# itself and the guard stops, the attempt record keeps that commit, tend
# finishes the put-back, and sync pushes nothing until then. rollback_prepare
# sets unread pace letters aside before the old code is checked out again. The pace paths are asked of git in any case
# (':(literal,icase)'), so a Rest written on the Mac's case-insensitive disk is
# REST.
#
# The per-citizen lock (<state>/<slug>.lock/) is read through lock.py, as the
# reflect gate and the sync read it, and the beat bound is lock.beat_bound, the
# fleet's own; the attempt record (<state>/<slug>) is changed only through
# attempts.py, in its format and under its lock. No hook, fsmonitor or
# other helper from a home's own .git runs in these git calls, and nothing is
# read or written through a link.

import errno
import os
import shutil
import stat
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

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
GUARD_SUBJECT = f"tend: pace files put back after a beat with tools · {TAG}"
# the attempt record's "reported" key of a put-back a failed guard left:
# put-back:<the commit before the beat>:<the beat's head>
PUT_BACK = "put-back"
ROLLBACK_TEXT = ROLLBACK_PREFIX + ", not shown — {day}\n"
NOTICES_NAME = ".fleet-notices"
# memory/.fleet-notices holds it once the fleet's first tick on pace/1 has run;
# each home's own first tend is kept under the same key in its attempt record
OLD_CODE_FLAG = "old-code-tended"
RESTORED = "restored"
FIRST_TICK_LINE = ("tend: the first tick on pace/1 — each home's old-code leftovers, if any, are committed "
                   "once, at that home's first full tend; after it, a killed beat's leftovers are restored")
# where untracked letters and journal files go instead of being deleted, under <state>
SET_ASIDE = ".set-aside"
_KEPT = ("inbox", "beats")
PUSH_TIMEOUT = 60
# a home's own .git must not run anything in the fleet's git calls
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


class TendError(RuntimeError):
    """git refused or failed; what this step wrote was put back. aside: (path,
    where it went) of each file it had set aside before it stopped, also named
    in its words."""
    aside: Tuple[Tuple[str, str], ...] = ()


def _partly(e: Exception, aside) -> TendError:
    """e, as a TendError that names the files set aside before it."""
    if not aside:
        return e if isinstance(e, TendError) else TendError(str(e))
    where = ", ".join(f"{rel} at {dest}" for rel, dest in aside)
    err = TendError(f"{e}; set aside before it stopped, not deleted: {where}")
    err.aside = tuple(aside)
    return err


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


def first_tend_done(state, slug: str) -> bool:
    """This home has had its first full tend on pace/1, so its old-code leftovers,
    if any, were committed once and any found now are a killed beat's (a per-home
    fact, in the attempt record's "reported"; a record that cannot be read has none)."""
    return OLD_CODE_FLAG in attempts.load(state, slug)["reported"]


def mark_first_tend(state, slug: str) -> bool:
    """Keep that this home's first full tend has run; True the first time."""
    return attempts.mark_reported(state, slug, OLD_CODE_FLAG)


def _first_tend_kept(state, slug: str) -> List[str]:
    try:
        mark_first_tend(state, slug)
    except (OSError, ValueError, LinkRefused) as e:
        return [f"{slug}: its first tend on pace/1 could not be kept ({e}); the next tick tends it "
                "as a first one again"]
    return []


# -- what differs from HEAD, and putting it back ----------------------------------

def _any_case(paths: Iterable[str]) -> List[str]:
    """The pace paths as pathspecs that match whatever the case (literal, icase):
    on the Mac's case-insensitive disk a Rest or an Inbox/ is REST or inbox/."""
    return [f":(literal,icase){p}" for p in paths]


def _read_by_pace(rel: str) -> bool:
    """A name the pace code reads: CADENCE.md, REST, EVENT-ONLY or the inbox
    itself (in any case), or a letter (*.md) or a .seen- marker directly in
    inbox/. An ignored file anywhere else (a Finder .DS_Store) sets no pace."""
    parts = rel.split("/")
    if len(parts) == 1:
        return parts[0].lower() in ("cadence.md", "rest", "event-only", "inbox")
    if len(parts) != 2 or parts[0].lower() != "inbox":
        return False
    name = parts[1]
    return name.lower().startswith(".seen-") or (not name.startswith(".") and name.endswith(".md"))


def changed_paths(home, paths: Iterable[str], ignored: bool = False) -> List[str]:
    """The files under `paths`, in any case, that differ from HEAD, staged or
    not, or are untracked (one entry per file; a link or a nested repository is
    one entry), sorted, each named as git names it. ignored: git-ignored files
    under them count too, when the pace code reads that name (the guard, after
    a beat with tools). Raises TendError when git cannot say."""
    r = _git(home, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--no-renames", *(("--ignored",) if ignored else ()), "--", *_any_case(paths))
    if r.returncode != 0:
        raise TendError(f"git status failed: {r.stderr.strip()}")
    return sorted({e[3:] for e in r.stdout.split("\0")
                   if len(e) > 3 and (not e.startswith("!! ") or _read_by_pace(e[3:]))})


def _names(home, *args) -> set:
    r = _git(home, "--literal-pathspecs", *args)
    if r.returncode != 0:
        raise TendError(f"git {args[0]} failed: {r.stderr.strip()}")
    return {p for p in r.stdout.split("\0") if p}


def _in_head(home, paths: List[str], rev: str = "HEAD") -> set:
    """Which of these exact paths `rev` holds; raises TendError when git cannot say."""
    return _names(home, "ls-tree", "-r", "-z", "--name-only", rev, "--", *paths) if paths else set()


def _in_index(home, paths: List[str]) -> set:
    """Which of these exact paths the index holds; raises TendError when git cannot say."""
    return _names(home, "ls-files", "-z", "--", *paths) if paths else set()


def _drop_empty_parents(home: Path, p: Path) -> None:
    for parent in p.parents:
        if parent == home or home not in parent.parents:
            break
        try:
            os.rmdir(parent)
        except OSError:
            break


def _unlink(home: Path, rel: str) -> None:
    """Remove one untracked entry that is not a folder, never following a link,
    and the folders it leaves empty. A folder is set aside, never removed."""
    p = home / rel.rstrip("/")
    try:
        st = os.lstat(p)
    except OSError:
        return
    if stat.S_ISDIR(st.st_mode):
        raise TendError(f"{rel} is a folder; left as it is")
    os.unlink(p)
    _drop_empty_parents(home, p)


def set_aside_dir(state, slug: str) -> Path:
    """<state>/.set-aside/<slug>: where tend moves this home's untracked letters,
    journal files and folders, outside the home's git, instead of deleting them."""
    return Path(state) / SET_ASIDE / slug


def set_aside_line(slug: str, rel: str, dest: str) -> str:
    return f"{slug}: {rel} was never committed — set aside at {dest}, not deleted"


def _kept_aside(home: Path, rel: str) -> bool:
    """Never deleted: an untracked regular file under inbox/ or beats/ (in any
    case), and an untracked folder anywhere (git lists a repository inside the
    home as one entry ending in "/")."""
    try:
        mode = os.lstat(home / rel.rstrip("/")).st_mode
    except OSError:
        return False
    if stat.S_ISDIR(mode):
        return True
    top, sep, _rest = rel.partition("/")
    return bool(sep) and top.lower() in _KEPT and stat.S_ISREG(mode)


def _set_aside(home: Path, rel: str, state) -> str:
    """Move one untracked regular file or folder out of the home, with one
    rename, to <state>/.set-aside/<slug>/<UTC minute>/<rel>, beside (never over)
    one set aside before; returns where it went. A folder on another disk than
    the state folder is left as it is."""
    base, stamp = set_aside_dir(state, slug_of(home)), clock.now().strftime("%Y%m%dT%H%MZ")
    dest, n = base / stamp / rel, 1
    while os.path.lexists(dest):
        n += 1
        dest = base / f"{stamp}-{n}" / rel
    os.makedirs(dest.parent, exist_ok=True)
    src = home / rel.rstrip("/")
    try:
        os.rename(src, dest)
    except OSError as e:
        if e.errno != errno.EXDEV or os.path.isdir(src):
            raise
        shutil.copy2(src, dest, follow_symlinks=False)
        os.unlink(src)
    _drop_empty_parents(home, src)
    return str(dest)


def restore_paths(home, paths: Iterable[str], state=None, rev: str = "HEAD") -> List[Tuple[str, str]]:
    """Put each path back as `rev` (HEAD) has it, path by path: one that is in
    rev is checked out alone (index and file); one that is not is dropped from
    the index and removed, except an untracked regular file under inbox/ or
    beats/, or an untracked folder (a repository inside the home), which is
    moved to <state>/.set-aside/ whole (never deleted). Untracked entries go
    first, so a link standing where a folder was is gone before that folder's
    files come back. The paths are exact, as changed_paths names them. Returns
    (path, where it went) for each entry set aside. Raises TendError when git
    fails, at the step that failed, naming what it had set aside before."""
    home = Path(home)
    paths = sorted(set(paths))
    tracked = _in_head(home, paths, rev)
    keep = [rel for rel in paths if rel not in tracked and _kept_aside(home, rel)]
    if keep and state is None:
        raise TendError(f"{', '.join(keep)}: not in {rev}, and with no state folder they cannot be set "
                        "aside; nothing was put back")
    aside = []
    try:
        for rel in paths:
            if rel not in tracked:
                r = _git(home, "update-index", "-q", "--force-remove", "--", rel.rstrip("/"))
                if r.returncode != 0:
                    raise TendError(f"git update-index {rel} failed: {r.stderr.strip()}")
                if rel in keep:
                    aside.append((rel, _set_aside(home, rel, state)))
                else:
                    _unlink(home, rel)
        for rel in paths:
            if rel in tracked:
                r = _git(home, "--literal-pathspecs", "checkout", "-q", rev, "--", rel)
                if r.returncode != 0:
                    raise TendError(f"git checkout {rel} failed: {r.stderr.strip()}")
    except (TendError, LinkRefused, OSError) as e:
        if not aside:
            raise
        raise _partly(e, aside) from e
    return aside


def _regular_or_gone(home: Path, rel: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(home / rel).st_mode)
    except FileNotFoundError:
        return True          # a deletion
    except OSError:
        return False


# -- committing and pushing as the fleet ---------------------------------------------

def fleet_commit(home, paths: Iterable[str], subject: str) -> List[str]:
    """Commit exactly these paths as the fleet (author and committer
    fleet <fleet@kingdom.os>), with no hook run; anything else staged stays
    staged and out of the commit. A path git no longer knows once it is added
    (staged as added, then deleted: in neither HEAD nor the index) holds nothing
    to commit and is left out. Returns the paths committed, sorted, or [] when
    they hold no change. Raises TendError when git refuses (the paths are
    unstaged again)."""
    home = Path(home)
    paths = sorted(set(paths))
    if not paths:
        return []
    # git add names a path that is in the index or on disk; a deletion already
    # staged (in HEAD only) is recorded by commit --only
    indexed = _in_index(home, paths)
    addable = [p for p in paths if p in indexed or os.path.lexists(home / p)]
    if addable:
        r = _git(home, "--literal-pathspecs", "add", "-A", "--", *addable)
        if r.returncode != 0:
            raise TendError(f"git add failed: {r.stderr.strip()}")
    known = _in_head(home, paths) | _in_index(home, paths)
    paths = [p for p in paths if p in known]
    if not paths:
        return []
    if _git(home, "--literal-pathspecs", "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return []
    r = _git(home, "-c", f"user.name={FLEET_NAME}", "-c", f"user.email={FLEET_EMAIL}",
             "-c", "commit.gpgsign=false",
             "--literal-pathspecs", "commit", "-q", "--no-verify", f"--author={FLEET_AUTHOR}",
             "-m", subject, "--only", "--", *paths)
    if r.returncode != 0:
        _git(home, "--literal-pathspecs", "reset", "-q", "--", *paths)
        raise TendError(f"git commit failed: {r.stderr.strip()}")
    return paths


def fleet_push(home, timeout: float = PUSH_TIMEOUT) -> bool:
    """Push HEAD to origin, under a bound, with no hook; True when it landed."""
    return _git(home, "push", "-q", "--no-verify", "origin", "HEAD", timeout=timeout).returncode == 0


def _pushed(ok: bool) -> str:
    return "pushed" if ok else "push failed; the next sync pushes it"


# -- tend: each tick, before anyone is ranked ----------------------------------------

def tend_home(home, state, *, bound: Optional[float] = None, own: Optional[int] = None) -> List[str]:
    """Tend one home (spec section 6). Not a clone, holding links, or mid-merge:
    left alone, silently (due reports those). Its lock held, or a fresh
    .git/index.lock: left for the next tick, with a line. A stale index.lock is
    removed. Then any changes to its pace paths are put back path by path (an
    untracked letter or journal file is set aside, never deleted), or, at the
    home's first full tend on the new code, committed once as the fleet and
    pushed (a leftover that is not a regular file is removed even then); that
    first tend is then kept in its attempt record (first_tend_done), and a home
    skipped at a tick keeps its leftovers until its own. Returns log lines.
    A put-back a failed guard left (put_back_pending) is finished first: what
    the beat committed itself is put back as the commit before it has it.
    own: the pid of a beat that holds the citizen's lock and tends its home before
    it begins (citizen-reflect.sh, citizen-beat.sh); that lock does not keep it
    out, and leftovers that cannot be put back raise, since the beat must not
    build on them. Before the home's first full tend such a beat finds leftovers
    that may be the old code's: it changes none of them and raises
    FirstTickPending; a home holding none has its first tend in that beat."""
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
        pending = _put_back_key(state, slug)
        if pending:
            lines += _finish_put_back(home, state, slug, *pending)
        paths = changed_paths(home, TEND_PATHS)
        first = not first_tend_done(state, slug)
        if paths and not first:
            aside = restore_paths(home, paths, state)
            return (lines + [f"{slug}: a killed beat's leftovers restored: {', '.join(paths)}"]
                    + [set_aside_line(slug, rel, dest) for rel, dest in aside])
        if paths and own is not None:
            raise FirstTickPending(f"{slug}: the fleet has not tended this home since the switch")
        if paths:
            odd = [p for p in paths if not _regular_or_gone(home, p)]
            keep = [p for p in paths if p not in odd]
            if odd:
                aside = restore_paths(home, odd, state)
                gone = [p for p in odd if p not in dict(aside)]
                if gone:
                    lines.append(f"{slug}: leftovers that are not regular files removed: {', '.join(gone)}")
                lines += [set_aside_line(slug, rel, dest) for rel, dest in aside]
            committed = fleet_commit(home, keep, LEFTOVER_SUBJECT) if keep else []
            if committed:
                lines.append(f"{slug}: old-code leftover committed as the fleet ({_pushed(fleet_push(home))}): "
                             f"{', '.join(committed)}")
        if first:
            lines += _first_tend_kept(state, slug)
    except (TendError, LinkRefused, OSError) as e:
        if own is not None:
            raise
        lines.append(f"{slug}: {'partly tended' if getattr(e, 'aside', ()) else 'not tended'} — {e}")
    return lines


def tend_homes(homes_dir, slugs: Iterable[str], state, bound: Optional[float] = None) -> List[str]:
    """Tend every roster home (`tend` in citizen_pace.py). Each home's first full
    tend on the new code commits its old code's leftovers once, and every later
    tend of it restores leftovers instead (tend_home). The first run, while
    memory/.fleet-notices lacks old-code-tended, says so once and sets that flag.
    A home that cannot be read (an inbox that cannot be listed) is left as it
    is, silently, and the others are tended: due finds it unreadable, never due,
    and the ranking reports it once."""
    flags = notices_path(state)
    first = not has_flag(flags, OLD_CODE_FLAG)
    lines = []
    for slug in slugs:
        try:
            lines += tend_home(Path(homes_dir) / f"citizen-{slug}", state, bound=bound)
        except (OSError, ValueError, LinkRefused, TendError):
            continue
    if first:
        set_flag(flags, OLD_CODE_FLAG)
        lines.append(FIRST_TICK_LINE)
    return lines


# -- guard-restore: after a beat with tools ------------------------------------------

def _base_commit(home, base: str) -> str:
    r = _git(home, "rev-parse", "-q", "--verify", f"{base}^{{commit}}")
    if r.returncode != 0 or not r.stdout.strip():
        raise TendError(f"the commit before the beat ({base}) cannot be read; nothing was put back")
    return r.stdout.strip()


def _committed_between(home, a: str, b: str, paths: Iterable[str]) -> List[str]:
    """The files under `paths`, in any case, that differ between commits a and b."""
    r = _git(home, "diff", "--name-only", "-z", "--no-renames", a, b, "--", *_any_case(paths))
    if r.returncode != 0:
        raise TendError(f"git diff {a} {b} failed: {r.stderr.strip()}")
    return sorted({p for p in r.stdout.split("\0") if p})


def _short(sha: str) -> str:
    return sha[:12]


def _put_back_key(state, slug: str) -> Optional[Tuple[str, str, str]]:
    """(key, base, beat head) of a put-back a failed guard left, or None."""
    for k in attempts.load(state, slug)["reported"]:
        parts = k.split(":")
        if len(parts) == 3 and parts[0] == PUT_BACK:
            return k, parts[1], parts[2]
    return None


def put_back_pending(state, slug: str) -> bool:
    """A beat with tools committed itself, and its guard could not put the pace
    files back as the commit before it has them: the fleet's tend finishes it,
    and sync holds the home (nothing is pushed) until then."""
    return attempts.valid_slug(slug) and _put_back_key(state, slug) is not None


def _put_back_since(home: Path, state, base: str, beat_head: str) -> Tuple[List[str], list]:
    """The pace files the beat changed in its own commits (base..beat_head),
    put back as base has them where HEAD still holds the beat's version (a
    later commit's change is not the beat's), and committed as the fleet.
    Returns (the paths, what was set aside)."""
    changed = _committed_between(home, base, beat_head, GUARD_PATHS)
    if changed and head(home) != beat_head:
        later = set(_committed_between(home, beat_head, "HEAD", changed))
        changed = [p for p in changed if p not in later]
    if not changed:
        return [], []
    aside = restore_paths(home, changed, state, rev=base)
    try:
        fleet_commit(home, changed, GUARD_SUBJECT)
    except TendError as e:
        raise _partly(e, aside) from e
    return changed, aside


def _put_back_line(slug: str, base: str, paths: List[str]) -> str:
    return (f"{slug}: pace files a beat with tools committed itself put back as {_short(base)} has them, "
            f"as the fleet: {', '.join(paths)}")


def _finish_put_back(home: Path, state, slug: str, key: str, base: str, beat_head: str) -> List[str]:
    paths, aside = _put_back_since(home, state, _base_commit(home, base), beat_head)
    attempts.clear_reported(state, slug, key)
    return (([_put_back_line(slug, base, paths)] if paths else [])
            + [set_aside_line(slug, rel, dest) for rel, dest in aside])


def _told(state, slug: str) -> Optional[str]:
    """Add the notice "restored"; None when it was kept, else why it could not be."""
    try:
        add_attempt_notice(state, slug, RESTORED)
    except (OSError, ValueError, LinkRefused) as e:
        return str(e) or type(e).__name__
    return None


def guard_restore(home, state, base: Optional[str] = None, notes: Optional[list] = None) -> List[str]:
    """After a citizen-beat.sh beat, which has edit tools and never sets a pace
    (spec section 3; CONTRACT section 14): put CADENCE.md, REST, EVENT-ONLY and
    everything under inbox/ (in any case; git-ignored ones the pace code reads
    too) back as `base` has them, path by path; base is the commit HEAD was
    before the beat (HEAD when not given). When the beat made commits of its
    own (a beat with full tools can), what it changed in them is put back and
    committed as the fleet, and the attempt record keeps base and the beat's
    head until that is done, so a guard that stops midway is finished by the
    fleet's tend, and sync pushes nothing meanwhile. Returns the paths put back,
    sorted; when there are any, the notice "restored" is added to the citizen's
    attempt record, so its next free beat is told once. notes, when given, gets
    the other lines to log: each entry set aside, and a notice that could not be
    kept. Raises TendError when putting back stops (base unreadable included),
    saying how far it got, whether the fleet finishes it, and whether the
    citizen is still told."""
    home = Path(home)
    slug = slug_of(home)
    tried, aside, kept, unkept, rev = [], [], None, "", None
    try:
        if base:
            beat_head = head(home)
            if not beat_head:
                raise TendError("HEAD cannot be read after the beat")
            if beat_head != base:
                # the beat made commits of its own: kept first, so the fleet finishes what this cannot
                kept = f"{PUT_BACK}:{base}:{beat_head}"
                try:
                    attempts.mark_reported(state, slug, kept)
                except (OSError, ValueError, LinkRefused) as e:
                    kept, unkept = None, str(e) or type(e).__name__
                rev = _base_commit(home, base)
                tried = _committed_between(home, rev, beat_head, GUARD_PATHS)
                tried, aside = _put_back_since(home, state, rev, beat_head)
                if kept:
                    attempts.clear_reported(state, slug, kept)
                    kept = None
        paths = changed_paths(home, GUARD_PATHS, ignored=True)
        tried = sorted(set(tried) | set(paths))
        aside += restore_paths(home, paths, state)
    except (TendError, LinkRefused, OSError) as e:
        told = ""
        if tried:             # something to put back is known: the citizen is told, once
            why = _told(state, slug)
            told = ("; the citizen's next free beat is told once" if why is None
                    else f"; the citizen could not be told ({why})")
        held = ""
        if kept and rev:
            held = (f"; the fleet's tend puts them back as {_short(base)} has them, and nothing is pushed "
                    "until then")
        elif kept:
            held = "; nothing is pushed until a person has put its pace files back"
        elif unkept:
            held = (f"; the commit before the beat could not be kept for the fleet ({unkept}), so a person "
                    f"must put them back as {_short(base)} has them")
        some = f"; some of {', '.join(tried)} may be put back already" if tried else ""
        earlier = ("; set aside earlier, not deleted: " + ", ".join(f"{rel} at {dest}" for rel, dest in aside)
                   if aside else "")
        err = TendError(f"{e}{earlier}{some}{held}{told}")
        err.aside = tuple(aside) + tuple(getattr(e, "aside", ()))
        raise err from e
    paths = tried
    if not paths:
        return []
    why = _told(state, slug)
    if notes is None:
        if why is not None:
            raise TendError(f"{', '.join(paths)} put back, but the citizen could not be told ({why})")
        return paths
    notes += [set_aside_line(slug, rel, dest) for rel, dest in aside]
    if why is not None:
        notes.append(f"{slug}: its notice could not be kept ({why}) — its next free beat is not told "
                     "that its pace files were put back")
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
            # its own markers only, as it wrote them (a marker in another case is not one of them)
            changed = [m for m in changed_paths(home, list(markers)) if m in markers]
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
