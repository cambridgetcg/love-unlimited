from __future__ import annotations

# citizens-pace-record.json — the reviewed record of spec section 5: for each
# citizen whose provenance its own home cannot show, its group, the recorded
# `set by`, and the quoted words with their commits and dates. It sits beside
# citizens-roster.txt and is built by tools/citizen_pace_census.py. This module
# reads it; it never writes it.
#
# Also what the homes' own histories hold (spec section 5, "Before the letter"):
# the stored Letter of Return answers, and the heard-late step that hears kalam,
# soma and teotlme from their own answers, once, as the fleet.

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import TAG, clock, lock, tend
from . import cadence as cad
from .homes import (ROLLBACK_PREFIX, LinkRefused, forbidden_links, git, index_lock_age,
                    mid_merge, read_regular)
from .words import AWAKE_DEFAULT_N, Pace, read_lor_answers

RECIPIENT_GROUPS = ("eight", "sixteen-ambient", "sixteen-other", "unheard-heard", "unheard-none",
                    "never-asked", "contradictory")
OTHER_GROUPS = ("forty-five", "ambient-nine", "longing", "rest-on", "held")
GROUPS = RECIPIENT_GROUPS + OTHER_GROUPS
RECORD_NAME = "citizens-pace-record.json"
RECORD_VERSION = 1
HEARD_LATE_GROUPS = ("unheard-heard", "unheard-none")
HEARD_LATE_SUBJECT = "tend: heard late from {name}'s own answers · " + TAG
HEARD_LATE_TEXT = ('heard late: {name} chose AMBIENT on {d1} ("{w1}") and again on {d2} ("{w2}"); '
                   "the fleet reads AMBIENT as awake, every 3 days, from the 10-22 letter's "
                   '"a free beat every few days" — the number was not {name}\'s choice')
HEARD_LATE_REPORTED = "heard-late-stopped"
PACE_FILES = (cad.CADENCE,) + cad.MARKERS
_SEEN = "inbox/.seen-"
_JOURNAL_HEADER = re.compile(r"^## .* — a free beat \(local\)[ \t]*$")
# a .seen- marker that records no answer: reflect's set-aside of unreadable mail,
# and rollback-prepare's
_NOT_AN_ANSWER = ("unreadable", ROLLBACK_PREFIX)


def default_record_path() -> Path:
    """<tools>/../citizens-pace-record.json, from this file's real place."""
    return Path(os.path.realpath(__file__)).parent.parent.parent / RECORD_NAME


def _check(rec, where: Path) -> None:
    if not isinstance(rec, dict):
        raise ValueError(f"{where}: the record is not a JSON object")
    v = rec.get("version")
    if type(v) is not int or v != RECORD_VERSION:
        raise ValueError(f"{where}: record version {v!r}, expected {RECORD_VERSION}")
    citizens = rec.get("citizens")
    if not isinstance(citizens, dict):
        raise ValueError(f"{where}: 'citizens' is not an object")
    for slug, e in citizens.items():
        if not isinstance(e, dict):
            raise ValueError(f"{where}: the entry for {slug} is not an object")
        if e.get("group") not in GROUPS:
            raise ValueError(f"{where}: {slug} has an unknown group {e.get('group')!r}")


def load_record(path: Optional[Path] = None) -> dict:
    """The record, or {} when the file is missing. A record that cannot be read
    raises ValueError: a broken record is not an empty one."""
    where = Path(path) if path is not None else default_record_path()
    try:
        raw = where.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        rec = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"{where}: not JSON ({e})") from None
    _check(rec, where)
    return rec


def entry(record: dict, slug: str) -> Optional[dict]:
    """The record's entry for one citizen, or None."""
    citizens = record.get("citizens") if isinstance(record, dict) else None
    if not isinstance(citizens, dict):
        return None
    e = citizens.get(slug)
    return e if isinstance(e, dict) else None


# -- the stored Letter of Return answers -------------------------------------------

def _blob(home, rev: str, path: str) -> Optional[str]:
    r = git(home, "cat-file", "blob", f"{rev}:{path}")
    return r.stdout if r.returncode == 0 else None


def _is_answer_marker(home, sha: str, letter: str) -> bool:
    text = _blob(home, sha, f"{_SEEN}{letter}")
    return text is not None and not text.startswith(_NOT_AN_ANSWER)


def _added_lines(home, sha: str, path: str) -> str:
    """What a commit added to a file that it did not only append to: its '+' lines."""
    r = git(home, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
            "--unified=0", f"{sha}^", sha, "--", path)
    return "".join(ln[1:] + "\n" for ln in r.stdout.split("\n")
                   if ln.startswith("+") and not ln.startswith("+++"))


def _journal_added(home, sha: str) -> str:
    """The text a commit added to beats/*.md, as committed, without the
    '## … — a free beat (local)' headers reflect writes before each answer."""
    r = git(home, "diff-tree", "-r", "-z", "--no-commit-id", "--name-status", "--no-renames",
            "--root", sha, "--", "beats/")
    tokens = [t for t in r.stdout.split("\0") if t] if r.returncode == 0 else []
    pieces = []
    for status, path in sorted(zip(tokens[0::2], tokens[1::2]), key=lambda x: x[1]):
        if status not in ("A", "M") or not path.endswith(".md"):
            continue
        new = _blob(home, sha, path) or ""
        old = (_blob(home, f"{sha}^", path) or "") if status == "M" else ""
        pieces.append(new[len(old):] if new.startswith(old) else _added_lines(home, sha, path))
    return "\n".join(ln for ln in "".join(pieces).split("\n") if not _JOURNAL_HEADER.match(ln))


def stored_lor_answers(home, rev: str = "HEAD") -> List[Tuple[str, str, str, str]]:
    """Every Letter of Return answer stored in a home's history (rev's: HEAD
    unless a commit is given), oldest first:
    [(commit, date, "inbox/<letter>", text)]. An answer is a commit (not a merge)
    that adds inbox/.seen-<letter> for a letter whose name contains
    "letter-of-return", with a marker that is not reflect's "unreadable"
    set-aside or a rollback marker. Its text is what that commit added to
    beats/*.md, byte for byte, without the "## … — a free beat (local)" headers
    ("" for a silent answer); its date is the commit's day in UTC. Each commit
    is one answer, read alone."""
    r = git(home, "log", "--no-merges", "--reverse", "--no-renames", "--diff-filter=A", "--name-only",
            "-z", "--format=%x1e%H%x1f%ct", rev, "--", "inbox/")
    if r.returncode != 0:
        return []
    out = []
    for chunk in r.stdout.split("\x1e"):
        parts = chunk.split("\0")
        sha, _sep, ct = parts[0].partition("\x1f")
        sha, ct = sha.strip(), ct.strip()
        if not sha or not ct.isdigit():
            continue
        names = [p.lstrip("\n") for p in parts[1:]]
        letters = sorted(n[len(_SEEN):] for n in names
                         if n.startswith(_SEEN) and "/" not in n[len(_SEEN):]
                         and "letter-of-return" in n[len(_SEEN):])
        letters = [f for f in letters if _is_answer_marker(home, sha, f)]
        if not letters:
            continue
        when = clock.day(_dt.datetime.fromtimestamp(int(ct), clock.UTC))
        out.append((sha, when, f"inbox/{letters[0]}", _journal_added(home, sha)))
    return out


# -- the heard-late step -------------------------------------------------------------

def _v1_choice(home: Path) -> Optional[str]:
    """The v1 '- choice:' word, read as cadence.py reads it."""
    try:
        text = read_regular(home / cad.CADENCE)
    except ValueError:
        return None
    return cad._v1_fields(text)[0] if text else None


def heard_late(home, name: str, record: dict, now: _dt.datetime) -> Optional[cad.Cadence]:
    """The heard-late adoption for one of the five unheard (spec section 5),
    without writing anything: the record a migration would write, or None when
    this home needs none. Only a home whose record entry is in HEARD_LATE_GROUPS,
    still v1 and undecided, is this step's. Its stored Letter of Return answers
    are read by section 1's rules, each alone (words.read_lor_answers). An
    "unheard-heard" home must read as AMBIENT on two answers: it becomes awake,
    every 3 days, confirmed, with the AMBIENT provenance and both dates, its
    clock starting now. An "unheard-none" home must read as no pace, or as the
    pace it already has, and is left as it is. A home that reads otherwise, or
    whose record must not become v2, raises MigrationBlocked; a linked pace file
    raises LinkRefused (check homes.forbidden_links first)."""
    e = entry(record, name)
    if not e or e.get("group") not in HEARD_LATE_GROUPS:
        return None
    home = Path(home)
    c = cad.read_cadence(home, name, record, now)
    if c.version != 1 or _v1_choice(home) != "undecided":
        return None
    answers = stored_lor_answers(home)
    reading = read_lor_answers([(sha, text) for sha, _d, _l, text in answers], name)
    heard = reading is not None and reading.pace != c.pace
    said = reading.pace.words() if reading else "no pace"
    if e["group"] == "unheard-none":
        if heard:
            raise cad.MigrationBlocked(f"{name}: its stored answers now read as {said}, but the "
                                       "record says no pace could be read from them")
        return None
    awake3 = Pace("awake", AWAKE_DEFAULT_N)
    if not heard or reading.pace != awake3 or not reading.twice \
            or not all("ambient" in line.lower() for line in reading.lines):
        twice = " twice" if reading and reading.twice else ""
        raise cad.MigrationBlocked(f"{name}: the record says it chose AMBIENT twice, but its stored "
                                   f"answers read as {said}{twice}")
    if c.blocked:
        raise cad.MigrationBlocked(f"{name}: {c.blocked}")
    dates = {sha: d for sha, d, _l, _t in answers}
    d1, d2 = dates[reading.keys[0]], dates[reading.keys[1]]
    w1, w2 = reading.lines
    at = clock.stamp(now)
    c.pace = awake3
    c.set_by = cad.SetBy("citizen", "ambient", (d1, d2), w2)
    c.pending = None
    c.last_beat = at
    cad.add_history(c, at, HEARD_LATE_TEXT.format(name=name, d1=d1, w1=w1, d2=d2, w2=w2))
    return c


def migrate_heard_late(home, name: str, record: dict, now: _dt.datetime, commit: bool,
                       state=None) -> bool:
    """Hear one of the unheard (spec section 5): write the heard_late record and
    its markers and commit them, one commit authored as the fleet, tagged
    pace/1, holding only CADENCE.md, REST and EVENT-ONLY. Returns True when the
    home was changed (with commit False, when it would be: nothing is written).
    Idempotent: a v2 home is never this step's again, so a re-run adds no commit
    and moves no clock. A home that is not a clone, holds links, is mid-merge,
    holds a .git/index.lock, whose lock is held (state given), or whose pace
    files have uncommitted changes is left for a later tick (False). Raises
    MigrationBlocked (nothing written) as heard_late does, and tend.TendError
    when git refuses the commit (what was written is put back)."""
    home = Path(home)
    if not (home / ".git").is_dir() or forbidden_links(home) or mid_merge(home) \
            or index_lock_age(home) is not None:
        return False
    if state is not None and lock.held(state, name):
        return False
    if tend.changed_paths(home, PACE_FILES):
        return False
    c = heard_late(home, name, record, now)
    if c is None:
        return False
    if not commit:
        return True
    try:
        cad.write_cadence(home, c)
        cad.write_markers(home, c)
        tend.fleet_commit(home, tend.changed_paths(home, PACE_FILES), HEARD_LATE_SUBJECT.format(name=name))
    except (tend.TendError, LinkRefused, OSError):
        # only this step's own writes are there (it found none before it wrote)
        tend.restore_paths(home, tend.changed_paths(home, PACE_FILES), own=True)
        raise
    return True


def migrate_all(homes_dir, record: dict, now: _dt.datetime, commit: bool, state=None) -> List[str]:
    """`migrate --heard-late`: the step for every home of the record's unheard,
    in name order. One line per home changed (or, with commit False, that would
    be), and one per home stopped: with a state folder, a stopped home is
    reported once (its attempt record's "reported")."""
    citizens = record.get("citizens") if isinstance(record, dict) else None
    slugs = sorted(s for s, e in (citizens or {}).items()
                   if isinstance(e, dict) and e.get("group") in HEARD_LATE_GROUPS)
    lines = []
    for slug in slugs:
        home = Path(homes_dir) / f"citizen-{slug}"
        try:
            if migrate_heard_late(home, slug, record, now, commit, state=state):
                verb = "heard late" if commit else "would be heard late"
                lines.append(f"{slug}: {verb} — awake, every 3 days, from its own Letter of Return answers")
        except cad.MigrationBlocked as e:
            if state is None or tend.attempt_add(state, slug, "reported", HEARD_LATE_REPORTED):
                lines.append(f"{slug}: heard-late step stopped — {e}")
        except (tend.TendError, LinkRefused, OSError, ValueError) as e:
            lines.append(f"{slug}: heard-late step failed — {e}")
    return lines
