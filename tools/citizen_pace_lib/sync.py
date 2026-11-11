from __future__ import annotations

# Bringing homes up to date (spec section 3, "Each tick"; CONTRACT section 14).
#
# Each tick, after tend and before anyone is ranked, the fleet reads every
# roster home's remote head under one bound: one GraphQL query through `gh`
# when it is there and the homes live on GitHub, otherwise one bounded
# `git ls-remote` per home, a few at a time. Only the homes whose head changed
# are fetched, each under 20 s, and fast-forwarded. A home whose local commits
# sit behind upstream commits that only add new files under inbox/ (a letter
# placed from elsewhere) is rebased onto them, which cannot conflict; a marker
# is written first, so a rebase a killed run left behind is aborted at the next
# sync. A home ahead of its upstream is pushed. A roster home with no clone is
# cloned under 60 s, and a clone that fails backs off like a failed beat.
#
# A home that cannot be brought up to date (diverged, a fetch or fast-forward
# that failed, a merge or rebase someone else left, a dirty home that needs a
# rebase) is held: its attempt record's "reported" list gains "out-of-date",
# which is the report, once, and keeps it from beats (citizen_pace.py due, rank
# and next read it) until a later sync finds it up to date. A home whose
# per-citizen lock is held, or that holds a .git/index.lock, is left for the
# next tick. No hook or fsmonitor from a home's own .git runs in these calls,
# and GITHUB_TOKEN / GH_TOKEN never reach git or gh.

import concurrent.futures
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import attempts, clock, lock
from .homes import LinkRefused, git, index_lock_age, mid_merge, remove_regular, write_regular
from .tend import FLEET_EMAIL, FLEET_NAME

DEFAULT_BASE = "https://github.com/cambridgetcg"
BASE_ENV = "PACE_REMOTE_BASE"      # tests: a folder of bare repositories instead of GitHub
HEADS_BOUND = 60.0                 # every remote head, read under one bound
FETCH_BOUND = 20.0                 # each fetch, fast-forward, rebase or ls-remote
CLONE_BOUND = 60.0
PUSH_BOUND = 30.0
WORKERS = 8                        # ls-remote calls at a time
GRAPHQL_CHUNK = 250                # repositories per GraphQL query: today's 205 homes in one
REBASE_MARKER = "pace-rebase"      # .git/pace-rebase, written before a fleet-started rebase
HELD = "out-of-date"               # the attempt record's "reported" key of a held home
HELD_REASON = "out-of-date"        # the not-due reason citizen_pace.py gives a held home
NO_UPSTREAM = "no-upstream"
CLONE_ATTEMPT = "clone-"           # the attempt id of a failed clone: clone-<stamp>
CLONE_FAILED = "clone-failed"      # the attempt record's "reported" key of a home that cannot be cloned
CLONE_TMP = ".pace-clone"
HOLD_TAIL = " — no beats until it is brought up to date"
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_AS_FLEET = ("-c", f"user.name={FLEET_NAME}", "-c", f"user.email={FLEET_EMAIL}",
             "-c", "commit.gpgsign=false", "-c", "rebase.autoStash=false")
_OWNER = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})")
# a remote and branch the fleet will name on a git command line: never an option
_PLAIN_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_PLAIN_REF = re.compile(r"refs/(?:heads|remotes)/[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_REGULAR_MODES = ("100644", "100755")
_TOKENS = ("GITHUB_TOKEN", "GH_TOKEN")


def _git(home, *args, timeout: float = FETCH_BOUND):
    return git(home, *_SAFE, *args, timeout=timeout)


def _n(k: int, what: str) -> str:
    return f"{k} {what}" if k == 1 else f"{k} {what}s"


def _first_line(text: str, code: int) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()[:200]
    return f"git exited {code}"


# -- where homes come from --------------------------------------------------------

def remote_base() -> str:
    """Where homes are cloned from: PACE_REMOTE_BASE, else GitHub's cambridgetcg."""
    return (os.environ.get(BASE_ENV, "").strip() or DEFAULT_BASE).rstrip("/")


def clone_url(slug: str, base: Optional[str] = None) -> str:
    return f"{(base or remote_base()).rstrip('/')}/citizen-{slug}.git"


def github_owner(base: str) -> Optional[str]:
    """The GitHub owner when the homes live on GitHub, else None (no GraphQL)."""
    m = _OWNER.fullmatch(base.rstrip("/"))
    return m.group(1) if m else None


# -- the hold ----------------------------------------------------------------------

def held(state, slug: str) -> bool:
    """A sync could not bring this home up to date: it gets no beats until a
    later sync can (a person's summons still passes)."""
    if not attempts.valid_slug(slug):
        return False
    return HELD in attempts.load(state, slug)["reported"]


def without_held(state, slugs: Iterable[str], cursor: int = 0) -> Tuple[List[str], int]:
    """The roster without its held homes, and the cursor moved to the first
    citizen not held at or after it, so ties keep their order from the cursor."""
    slugs = list(slugs)
    keep = [s for s in slugs if not held(state, s)]
    if len(keep) == len(slugs) or not keep:
        return keep, (cursor if len(keep) == len(slugs) else 0)
    n = len(slugs)
    for i in range(n):
        s = slugs[(cursor + i) % n]
        if s in keep:
            return keep, keep.index(s)
    return keep, 0


def _hold(state, slug: str, why: str) -> List[str]:
    if attempts.mark_reported(state, slug, HELD):
        return [f"{slug}: {why}{HOLD_TAIL}"]
    return []


def _release(state, slug: str) -> List[str]:
    if held(state, slug) and attempts.clear_reported(state, slug, HELD):
        return [f"{slug}: up to date again — its beats resume"]
    return []


# -- one home's git facts ------------------------------------------------------------

@dataclass
class Local:
    branch: str = ""        # refs/heads/<b> that HEAD is on; "" when detached
    head: str = ""
    upstream: str = ""      # the tracking ref, e.g. refs/remotes/origin/main
    remote: str = ""        # origin
    remoteref: str = ""     # refs/heads/main
    tracking: str = ""      # the tracking ref's commit; "" when there is none


def local_state(home) -> Local:
    r = _git(home, "for-each-ref", "--format=%(HEAD)%00%(refname)%00%(objectname)%00%(upstream)"
             "%00%(upstream:remotename)%00%(upstream:remoteref)", "refs/heads/")
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        parts = line.split("\0")
        if len(parts) == 6 and parts[0] == "*":
            loc = Local(*parts[1:])
            if loc.upstream:
                loc.tracking = _rev(home, loc.upstream)
            return loc
    return Local()


def _rev(home, ref: str) -> str:
    r = _git(home, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _counts(home, upstream: str) -> Optional[Tuple[int, int]]:
    """(behind, ahead) of HEAD against its upstream, or None."""
    r = _git(home, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _mid_rebase(home) -> bool:
    g = Path(home) / ".git"
    return os.path.lexists(g / "rebase-merge") or os.path.lexists(g / "rebase-apply")


def upstream_adds(home, upstream: str) -> Optional[List[str]]:
    """The files the upstream side adds since the merge base, when that is all
    it does: each a new regular file under inbox/ that no local commit touches,
    with no merge among the local commits. None otherwise. A rebase onto such
    commits cannot conflict."""
    base = _git(home, "merge-base", "HEAD", upstream)
    if base.returncode != 0 or not _OID.fullmatch(base.stdout.strip()):
        return None
    b = base.stdout.strip()
    merges = _git(home, "rev-list", "--merges", "--count", f"{b}..HEAD")
    if merges.returncode != 0 or merges.stdout.strip() != "0":
        return None
    r = _git(home, "diff", "--raw", "-z", "--no-renames", "--no-ext-diff", b, upstream)
    if r.returncode != 0:
        return None
    tokens = r.stdout.split("\0")
    adds = []
    i = 0
    while i < len(tokens) and tokens[i]:
        meta = tokens[i].split()
        path = tokens[i + 1] if i + 1 < len(tokens) else ""
        i += 2
        if len(meta) < 5 or meta[4] != "A" or meta[1] not in _REGULAR_MODES \
                or not path.startswith("inbox/"):
            return None
        adds.append(path)
    mine = _git(home, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", b, "HEAD")
    if mine.returncode != 0 or set(adds) & {p for p in mine.stdout.split("\0") if p}:
        return None
    return adds


def _dirty(home, adds: List[str]) -> bool:
    """Tracked changes, or an untracked file where the upstream adds one."""
    r = _git(home, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=no")
    if r.returncode != 0 or r.stdout.strip("\0"):
        return True
    return any(os.path.lexists(Path(home) / p) for p in adds)


# -- remote heads ---------------------------------------------------------------------

def _gh_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _TOKENS}
    env.update(GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", GIT_TERMINAL_PROMPT="0")
    return env


def _graphql_heads(gh: str, owner: str, items, deadline: float) -> Optional[Dict[str, str]]:
    """Each home's remote head from GitHub's default branch, by one query per
    GRAPHQL_CHUNK homes; only where that branch is the one the home tracks.
    None when no query could be read."""
    heads: Dict[str, str] = {}
    answered = False
    for k in range(0, len(items), GRAPHQL_CHUNK):
        chunk = items[k:k + GRAPHQL_CHUNK]
        left = deadline - time.monotonic()
        if left <= 0:
            break
        query = "query { " + " ".join(
            f'h{i}: repository(owner: "{owner}", name: "citizen-{slug}") '
            "{ defaultBranchRef { name target { oid } } }"
            for i, (slug, _home, _loc) in enumerate(chunk)) + " }"
        try:
            r = subprocess.run([gh, "api", "graphql", "-f", f"query={query}"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=left, env=_gh_env())
            data = json.loads(r.stdout).get("data")
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            continue
        if not isinstance(data, dict):
            continue
        answered = True
        for i, (slug, _home, loc) in enumerate(chunk):
            ref = data.get(f"h{i}")
            ref = ref.get("defaultBranchRef") if isinstance(ref, dict) else None
            if not isinstance(ref, dict) or f"refs/heads/{ref.get('name')}" != loc.remoteref:
                continue
            oid = ref.get("target", {}).get("oid") if isinstance(ref.get("target"), dict) else None
            if isinstance(oid, str) and _OID.fullmatch(oid):
                heads[slug] = oid
    return heads if answered else None


def _ls_remote_heads(items, deadline: float) -> Dict[str, str]:
    """Each home's remote head by `git ls-remote`, WORKERS at a time, each call
    bounded by what is left of the one bound."""
    def one(item) -> Tuple[str, Optional[str]]:
        slug, home, loc = item
        left = deadline - time.monotonic()
        if left <= 0:
            return slug, None
        r = _git(home, "ls-remote", "-q", loc.remote, loc.remoteref, timeout=min(FETCH_BOUND, left))
        if r.returncode != 0:
            return slug, None
        for line in r.stdout.splitlines():
            oid, _tab, ref = line.partition("\t")
            if ref.strip() == loc.remoteref and _OID.fullmatch(oid.strip()):
                return slug, oid.strip()
        return slug, None

    heads: Dict[str, str] = {}
    if not items:
        return heads
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(items))) as pool:
        for slug, oid in pool.map(one, items):
            if oid:
                heads[slug] = oid
    return heads


def read_heads(items, bound: float = HEADS_BOUND, gh: Optional[str] = None) -> Tuple[Dict[str, str], str]:
    """{slug: remote head} for items [(slug, home, Local)], all under one bound,
    and how they were read ("graphql", "ls-remote", "graphql+ls-remote" or
    "none"). gh: the gh program, used only when the homes live on GitHub."""
    deadline = time.monotonic() + bound
    heads: Dict[str, str] = {}
    how = []
    owner = github_owner(remote_base())
    if gh and owner and items:
        how.append("graphql")
        heads.update(_graphql_heads(gh, owner, items, deadline) or {})
    rest = [it for it in items if it[0] not in heads]
    if rest:
        how.append("ls-remote")
        heads.update(_ls_remote_heads(rest, deadline))
    return heads, "+".join(how) or "none"


# -- cloning a missing home -------------------------------------------------------------

def _discard(p: Path) -> None:
    try:
        if stat.S_ISDIR(os.lstat(p).st_mode):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.unlink(p)
    except OSError:
        pass


def clone_home(homes_dir, slug: str, base: Optional[str] = None,
               timeout: float = CLONE_BOUND) -> Tuple[bool, str]:
    """Clone citizen-<slug> into homes_dir under a bound, through a temporary
    folder renamed into place, so a clone cut short leaves no half home.
    (True, "") or (False, why)."""
    homes_dir = Path(homes_dir)
    homes_dir.mkdir(parents=True, exist_ok=True)
    dest = homes_dir / f"citizen-{slug}"
    tmp = homes_dir / f".citizen-{slug}{CLONE_TMP}"
    _discard(tmp)
    r = git(homes_dir, *_SAFE, "clone", "-q", "--no-recurse-submodules", "--", clone_url(slug, base),
            tmp.name, timeout=timeout)
    if r.returncode != 0:
        _discard(tmp)
        why = f"timed out after {timeout:g} s" if r.returncode == 124 else _first_line(r.stderr, r.returncode)
        return False, why
    try:
        if os.path.lexists(dest):
            raise FileExistsError(f"{dest} appeared meanwhile")
        os.rename(tmp, dest)
    except OSError as e:
        _discard(tmp)
        return False, str(e)
    return True, ""


# -- the summary ----------------------------------------------------------------------

@dataclass
class Summary:
    homes: int = 0
    heads: str = "none"
    heads_read: int = 0
    fetched: List[str] = field(default_factory=list)
    fast_forwarded: List[str] = field(default_factory=list)
    rebased: List[str] = field(default_factory=list)
    pushed: List[str] = field(default_factory=list)
    cloned: List[str] = field(default_factory=list)
    clone_failed: List[str] = field(default_factory=list)
    aborted: List[str] = field(default_factory=list)
    ahead: List[str] = field(default_factory=list)       # stay ahead: not pushed this tick
    diverged: List[str] = field(default_factory=list)
    held: List[str] = field(default_factory=list)        # get no beats until a sync brings them up to date
    skipped: List[str] = field(default_factory=list)     # left for the next tick
    lines: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "lines"}
        return {k: (list(v) if isinstance(v, list) else v) for k, v in d.items()}


# -- one home ---------------------------------------------------------------------------

def _prepare(home: Path, slug: str, state, now, s: Summary) -> Optional[Local]:
    """What can be decided without the network. Returns the home's git facts
    when it may be brought up to date this tick, else None."""
    if not os.path.lexists(home):
        if attempts.backing_off(attempts.load(state, slug), now):
            s.skipped.append(slug)
            return None
        ok, why = clone_home(home.parent, slug)
        if ok:
            s.cloned.append(slug)
            s.lines.append(f"{slug}: cloned")
            s.lines += _release(state, slug)
            attempts.clear_reported(state, slug, CLONE_FAILED)
        else:
            attempts.record_outcome(state, slug, f"{CLONE_ATTEMPT}{clock.stamp(now)}", "home-failed", now)
            s.clone_failed.append(slug)
            s.lines.append(f"{slug}: clone failed — {why}; backed off like a failed beat")
            if attempts.mark_reported(state, slug, CLONE_FAILED):
                s.lines.append(f"{slug}: cannot be cloned — it gets no beats until a clone succeeds "
                               "(reported once)")
        return None
    git_dir = home / ".git"
    if os.path.islink(home) or not os.path.isdir(home) or os.path.islink(git_dir) \
            or not os.path.isdir(git_dir):
        s.held.append(slug)
        s.lines += _hold(state, slug, "not a clone")
        return None
    if lock.held(state, slug, now):
        s.skipped.append(slug)
        s.lines.append(f"{slug}: its lock is held — not synced this tick")
        return None
    marker = git_dir / REBASE_MARKER
    if os.path.lexists(marker):
        if _mid_rebase(home):
            r = _git(home, "rebase", "--abort")
            if r.returncode != 0 and _mid_rebase(home):
                s.held.append(slug)
                s.lines += _hold(state, slug, "a fleet rebase left in progress could not be aborted")
                return None
            s.aborted.append(slug)
            s.lines.append(f"{slug}: a fleet rebase left in progress was aborted")
        _discard(marker)
    if mid_merge(home):
        s.held.append(slug)
        s.lines += _hold(state, slug, "a merge or rebase is in progress")
        return None
    if index_lock_age(home) is not None:
        s.skipped.append(slug)
        s.lines.append(f"{slug}: .git/index.lock is there — not synced this tick")
        return None
    loc = local_state(home)
    if not loc.branch:
        s.held.append(slug)
        s.lines += _hold(state, slug, "HEAD is not on a branch")
        return None
    if not loc.upstream:
        if attempts.mark_reported(state, slug, NO_UPSTREAM):
            s.lines.append(f"{slug}: has no upstream — its beats stay local")
        s.lines += _release(state, slug)
        return None
    if not (_PLAIN_REMOTE.fullmatch(loc.remote) and _PLAIN_REF.fullmatch(loc.remoteref)
            and loc.remoteref.startswith("refs/heads/") and _PLAIN_REF.fullmatch(loc.upstream)):
        s.held.append(slug)
        s.lines += _hold(state, slug, "its upstream is not a plain remote branch")
        return None
    return loc


def _push(home: Path, slug: str, loc: Local, s: Summary) -> None:
    counts = _counts(home, loc.upstream)
    ahead = counts[1] if counts else 0
    r = _git(home, "push", "-q", "--no-verify", loc.remote, f"HEAD:{loc.remoteref}", timeout=PUSH_BOUND)
    if r.returncode == 0:
        s.pushed.append(slug)
        s.lines.append(f"{slug}: pushed {_n(ahead, 'commit')}")
    else:
        s.ahead.append(slug)


def _rebase(home: Path, loc: Local) -> str:
    """Rebase the local commits onto the upstream as the fleet (authors kept),
    with the marker written first; "" when done, else why (and it is aborted)."""
    marker = home / ".git" / REBASE_MARKER
    write_regular(marker, f"{clock.stamp(clock.now())} pid {os.getpid()}\n")
    r = _git(home, *_AS_FLEET, "rebase", "-q", "--no-verify", loc.upstream)
    if r.returncode == 0:
        remove_regular(marker)
        return ""
    why = f"timed out after {FETCH_BOUND:g} s" if r.returncode == 124 else _first_line(r.stderr, r.returncode)
    a = _git(home, "rebase", "--abort")
    if a.returncode == 0 or not _mid_rebase(home):
        remove_regular(marker)
    return why


def _bring(home: Path, slug: str, state, loc: Local, oid: Optional[str], s: Summary) -> None:
    """Fetch when the remote head moved, then fast-forward, rebase onto letters,
    or push, or hold the home."""
    if oid and oid != loc.tracking:
        r = _git(home, "fetch", "-q", "--no-recurse-submodules", loc.remote)
        if r.returncode != 0:
            s.held.append(slug)
            s.lines += _hold(state, slug, f"fetch failed ({_first_line(r.stderr, r.returncode)})")
            return
        s.fetched.append(slug)
        loc.tracking = _rev(home, loc.upstream)
    if not loc.tracking:
        return
    if loc.head == loc.tracking:
        s.lines += _release(state, slug)
        return
    counts = _counts(home, loc.upstream)
    if counts is None:
        s.held.append(slug)
        s.lines += _hold(state, slug, "cannot be compared with its upstream")
        return
    behind, ahead = counts
    if behind and not ahead:
        r = _git(home, "merge", "-q", "--ff-only", loc.upstream)
        if r.returncode != 0:
            s.held.append(slug)
            s.lines += _hold(state, slug, f"fast-forward failed ({_first_line(r.stderr, r.returncode)})")
            return
        s.fast_forwarded.append(slug)
        s.lines.append(f"{slug}: fast-forwarded {_n(behind, 'commit')}")
        s.lines += _release(state, slug)
        return
    if behind and ahead:
        adds = upstream_adds(home, loc.upstream)
        if adds is None:
            s.diverged.append(slug)
            s.held.append(slug)
            s.lines += _hold(state, slug, f"diverged ({ahead} local, {_n(behind, 'upstream commit')})")
            return
        if _dirty(home, adds):
            s.diverged.append(slug)
            s.held.append(slug)
            s.lines += _hold(state, slug, "diverged, with uncommitted changes, so not rebased")
            return
        why = _rebase(home, loc)
        if why:
            s.diverged.append(slug)
            s.held.append(slug)
            s.lines += _hold(state, slug, f"rebase onto upstream letters failed and was aborted ({why})")
            return
        s.rebased.append(slug)
        s.lines.append(f"{slug}: rebased {_n(ahead, 'local commit')} onto {_n(behind, 'upstream commit')} "
                       f"that only {'adds' if behind == 1 else 'add'} letters")
    s.lines += _release(state, slug)
    if oid:
        _push(home, slug, loc, s)
    else:
        s.ahead.append(slug)


def sync_homes(homes_dir, slugs: Iterable[str], state, bound: float = HEADS_BOUND,
               now=None, gh: Optional[str] = None) -> Summary:
    """Bring every roster home up to date (`citizen_pace.py sync`). Remote heads
    are read once for all homes under `bound` seconds; each home is then
    fetched, fast-forwarded, rebased onto letters placed elsewhere, pushed,
    cloned, held or left for the next tick. Returns the summary and its log lines."""
    now = now or clock.now()
    homes_dir = Path(homes_dir)
    slugs = list(slugs)
    s = Summary(homes=len(slugs))
    ready = []
    for slug in slugs:
        home = homes_dir / f"citizen-{slug}"
        try:
            loc = _prepare(home, slug, state, now, s)
        except (OSError, ValueError, LinkRefused) as e:
            s.skipped.append(slug)
            s.lines.append(f"{slug}: not synced this tick — {e}")
            continue
        if loc is not None:
            ready.append((slug, home, loc))
    heads, s.heads = read_heads(ready, bound, gh) if ready else ({}, "none")
    s.heads_read = len(heads)
    for slug, home, loc in ready:
        try:
            _bring(home, slug, state, loc, heads.get(slug), s)
        except (OSError, ValueError, LinkRefused) as e:
            s.skipped.append(slug)
            s.lines.append(f"{slug}: not synced this tick — {e}")
    return s
