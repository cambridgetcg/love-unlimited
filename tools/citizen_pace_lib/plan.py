from __future__ import annotations

# The schedule simulator and the rollback drill (spec section 3 "Each tick",
# section 8 "Schedule", section 9 step 3).
#
# simulate models the new fleet over the homes it is given and runs no beat. A
# tick comes every tick_hours and beats at most one citizen; the daily cap
# counts a UTC day's landed beats, and a failed attempt is not counted. The
# most overdue citizen goes first, ties by roster order from the cursor, which
# moves past each citizen beaten, as due.rank orders them, and every decision
# is due.is_due's, the function the fleet's ranking and the reflect gate call.
# The heard-late step runs at the first tick, as the fleet runs it. Pace
# letters arrive by delivery runs: at most per_run a run, one run a day. Every
# beat is modeled as silent: no pace line, so no pace changes and a pending
# proposal lapses, and each letter shown (up to three, oldest first) is marked
# seen, once. The report is a model of the Studio, not the Studio's live state:
# each citizen's longest wait, from when its beat was due to when it came, and
# when the last letter is read.
#
# Reading the homes writes nothing, there or in the attempt records. write_out
# writes the homes as a run would leave them into a scratch folder, through
# the real code (record.migrate_heard_late and answer.apply_answer), each a
# git repository with no remote, and nothing anywhere else. rollback_drill
# counts the wakes per day that PR #24's keeps_rest and round robin (the code
# the Studio would roll back to) would make over a folder of homes, with every
# citizen silent, and writes nothing.

import argparse
import datetime as _dt
import os
import shutil
import stat
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import TAG, attempts, clock, due, homes, tend
from . import cadence as cad
from . import record as records
from .answer import SHOW, SHOW_SEEN, apply_answer
from .clock import DAY
from .due import Due
from .homes import LinkRefused
from .letter import letter_path

CAP = 4                  # the Studio's FLEET_DAILY_CAP since 11-05
TICK_HOURS = 6.0         # the Studio's launchd interval since 11-05
MAX_SHOWN = 3            # letters one beat shows, oldest first (spec section 3)
MAX_TICKS = 100_000
PACE_MARK = "pace-letter"
LABEL = "a model of the Studio's fleet, not its live state"
DRILL_LABEL = "PR #24's keeps_rest and round robin, a model, not the Studio's live state"
SILENT = ("every beat is modeled as silent: no pace line, so no pace changes and a pending proposal "
          "lapses; each letter shown is marked seen")
DRILL_SILENT = ("every citizen is modeled as silent: an ordinary letter met with silence stays unread "
                "(PR #24's set-aside), so its reader is woken again at every pass; a Letter of Return "
                "met with silence is read as rest-on")
LETTER_TEXT = ("# One question, once — your pace\n\n"
               "(placed by citizen_pace.py plan in a scratch copy: a model of the delivery, not the "
               "letter)\n")
OUT_SUBJECT = f"plan: the simulated end state, a model, not a beat · {TAG}"
BASE_SUBJECT = "plan: the home as it was read, before the model ran"
NEEDS_A_LOOK = ("no-home", "no-soul", "links", "mid-merge", "blocked", "error")
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_FAR = _dt.datetime(9999, 12, 31, tzinfo=clock.UTC)
_EVENT_ORDER = {"letter": 0, "heard-late": 1, "beat": 2}


# -- what a run records ---------------------------------------------------------------

@dataclass(frozen=True)
class Beat:
    slug: str
    at: _dt.datetime
    kind: str                       # "schedule" | "letter" | "show-back", the Due reason
    since: _dt.datetime             # when the beat became due
    last: _dt.datetime              # the citizen's last beat before it
    shown: tuple = ()               # "inbox/<f>", oldest first, at most 3; or ("SHOW.md",)
    next_since: Optional[_dt.datetime] = None   # when the next citizen in the ranking became due

    @property
    def wait(self) -> _dt.timedelta:
        return self.at - self.since


@dataclass(frozen=True)
class Failure:
    slug: str
    at: _dt.datetime
    failures: int
    retry_after: _dt.datetime


@dataclass(frozen=True)
class Placed:
    slug: str
    rel: str
    at: _dt.datetime


@dataclass
class Result:
    start: _dt.datetime
    end: _dt.datetime
    days: int
    cap: int
    tick_hours: float
    confirm_beat: bool
    roster: List[str]
    ticks: List[_dt.datetime] = field(default_factory=list)
    outages: List[Tuple[_dt.datetime, _dt.datetime]] = field(default_factory=list)
    per_run: Optional[int] = None
    record_empty: bool = False
    paces: Dict[str, str] = field(default_factory=dict)        # at the first tick
    heard_late: List[str] = field(default_factory=list)
    stopped: List[Tuple[str, str]] = field(default_factory=list)
    placed: List[Placed] = field(default_factory=list)
    not_placed: List[Tuple[str, str]] = field(default_factory=list)
    runs: List[_dt.datetime] = field(default_factory=list)
    beats: List[Beat] = field(default_factory=list)
    failures: List[Failure] = field(default_factory=list)
    capped: int = 0                  # ticks on which the cap held a due citizen back
    arrived: Dict[Tuple[str, str], _dt.datetime] = field(default_factory=dict)
    read: Dict[Tuple[str, str], _dt.datetime] = field(default_factory=dict)
    ends: Dict[str, Due] = field(default_factory=dict)
    open_since: Dict[str, _dt.datetime] = field(default_factory=dict)
    asked_once: Dict[str, str] = field(default_factory=dict)
    events: Dict[str, list] = field(default_factory=dict)

    def longest_wait(self, slug: str) -> Optional[_dt.timedelta]:
        """From when a beat was due to when it came; a wait still open at the end counts to the end."""
        waits = [b.wait for b in self.beats if b.slug == slug]
        if slug in self.open_since:
            waits.append(self.end - self.open_since[slug])
        return max(waits) if waits else None

    def unread_at_end(self) -> Dict[str, List[str]]:
        """The letters no beat showed, by citizen, oldest first."""
        out: Dict[str, List[str]] = {}
        for slug, rel in sorted(self.arrived):
            if (slug, rel) not in self.read:
                out.setdefault(slug, []).append(rel)
        return out


# -- the homes, as the fleet reads them --------------------------------------------------

@dataclass
class _Citizen:
    slug: str
    index: int
    home: Path
    exists: bool = False
    error: bool = False
    soul: bool = False
    links: list = field(default_factory=list)
    midmerge: bool = False
    refused: bool = False            # CADENCE.md could not be read through a link
    unwritable: bool = False         # its attempt record cannot be written (state given)
    c: Optional[cad.Cadence] = None
    rec: dict = field(default_factory=dict)
    unread: list = field(default_factory=list)   # [(rel, arrival)], by name
    show: bool = False               # SHOW.md waits to be shown
    pace_letter: bool = False        # holds an inbox/*pace-letter* file, read or not
    volatile: bool = False           # a time later than the start: assessed at every tick
    cached: Optional[Due] = None
    episode: Optional[_dt.datetime] = None   # due since then, not yet beaten
    events: list = field(default_factory=list)


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _holds_pace_letter(home: Path) -> bool:
    try:
        names = os.listdir(home / "inbox")
    except OSError:
        return False
    return any(PACE_MARK in n and not n.startswith(".") for n in names)


class Model:
    """The homes as the fleet reads them, held in memory. Each citizen's record
    (read_deciding, as due reads it), its attempt record (from state, read and
    never written; a fresh one without state) and its unread letters are read once. due(slug,
    now) gives due.due_for_home's answer for that home, as the fleet's ranking
    asks it (a citizen with no last beat has its clock started the first time),
    without reading the home again; the simulator changes the model, never the
    home."""

    def __init__(self, homes_dir, roster, record: dict, start: _dt.datetime, *, state=None,
                 confirm_beat: Optional[bool] = None):
        self.homes_dir = Path(homes_dir)
        self.record = record
        self.start = start
        self.state = state
        self.confirm = due.WAKE_CONFIRM_BEAT if confirm_beat is None else bool(confirm_beat)
        self.slugs = due._slugs(roster)
        self.citizens = {s: self._read(s, i) for i, s in enumerate(self.slugs)}

    def _read(self, slug: str, index: int) -> _Citizen:
        z = _Citizen(slug, index, self.homes_dir / f"citizen-{slug}")
        z.rec = attempts.load(self.state, slug) if self.state is not None else attempts.fresh(slug)
        try:
            z.exists = z.home.is_dir()
            if not z.exists:
                return z
            z.soul = homes.has_soul(z.home, slug)
            z.links = homes.forbidden_links(z.home) if z.soul else []
            z.midmerge = bool(z.soul and not z.links
                              and (homes.mid_merge(z.home) or due.fresh_index_lock(z.home)))
            if not z.soul or z.links:
                return z
            try:
                z.c = cad.read_deciding(z.home, slug, self.record, self.start)
            except LinkRefused:
                z.refused = True
                return z
            except due.READ_ERRORS:
                if not z.midmerge:      # due_for_home never reads a mid-merge home's record
                    raise
            if self.state is not None and not z.midmerge:
                try:
                    attempts.check_writable(self.state, slug)
                except due.READ_ERRORS:   # as due_for_home: never due ("error"); nothing written
                    z.unwritable = True
            z.unread = [(rel, homes.letter_time(z.home, rel, _FAR)) for rel in homes.unread_letters(z.home)]
            z.show = _regular(z.home / SHOW) and not os.path.lexists(z.home / SHOW_SEEN)
            z.pace_letter = _holds_pace_letter(z.home)
        except due.READ_ERRORS:       # as due_for_home: a home that cannot be read
            z.error = True
            return z
        later = [t for t in (due._when(z.c.last_beat if z.c else None), attempts.last_beat_at(z.rec))
                 if t is not None and t > self.start]
        z.volatile = bool(later) or any(t > self.start for _rel, t in z.unread)
        return z

    def label(self, slug: str) -> str:
        """The citizen's pace in the menu's words, or why there is none to read."""
        z = self.citizens[slug]
        if not z.exists:
            return "no-home"
        if z.error:
            return "error"
        if not z.soul:
            return "no-soul"
        if z.links or z.refused:
            return "links"
        if z.c is None:
            return "mid-merge"
        if z.c.blocked:
            return "blocked"
        return z.c.pace.words() if z.c.pace is not None else "unreadable"

    def due(self, slug: str, now: _dt.datetime) -> Due:
        """Whether the citizen is due now. A Due stays true until the citizen's
        state changes, and a not-due one until its next_at, so only those are
        assessed again; times must not go back."""
        z = self.citizens[slug]
        d = z.cached
        if d is None or z.volatile or (not d.due and d.next_at is not None and now >= d.next_at):
            d = z.cached = self._assess(z, now)
        return d

    def _assess(self, z: _Citizen, now: _dt.datetime) -> Due:
        # due.due_for_home, step by step, on the model
        if not z.exists:
            return Due(False, "no-home")
        if z.error:
            return Due(False, "error")
        if not z.soul or z.links or z.midmerge:
            return due.is_due(None, {}, now, [], z.soul, z.links, z.midmerge)
        if z.c is None or z.refused:
            return Due(False, "links")
        if z.unwritable:
            return Due(False, "error")
        try:
            if due.last_beat(z.c, z.rec, now) is None:
                self._seed(z, now)
            c = z.c
            wants_mail = not c.blocked and (c.pace is None or c.pace.kind in ("asleep", "listening"))
            mail = [min(t, now) for _rel, t in z.unread] if wants_mail else []
            return due.is_due(c, z.rec, now, mail, z.soul, z.links, z.midmerge, confirm_beat=self.confirm)
        except due.READ_ERRORS:     # as due_for_home: a home that cannot be read is not due
            return Due(False, "error")

    def _seed(self, z: _Citizen, now: _dt.datetime) -> None:
        """The clock start the fleet's ranking writes (attempts.seed), kept in the
        model only. A record path that write refuses (a link, a folder) raises
        LinkRefused here too, so the model reads that home as the ranking does."""
        if self.state is not None:
            homes._refuse_non_regular(attempts.record_path(self.state, z.slug))
        z.rec["last_beat"], z.rec["seeded"] = clock.stamp(now), attempts.SEEDED

    # -- what happens to a citizen in the model ---------------------------------------

    def place(self, slug: str, rel: str, at: _dt.datetime) -> None:
        z = self.citizens[slug]
        z.unread = sorted(z.unread + [(rel, at)])
        z.pace_letter = z.pace_letter or PACE_MARK in rel
        z.cached = None
        z.events.append((at, "letter", rel))

    def hear(self, slug: str, c: cad.Cadence, at: _dt.datetime) -> None:
        z = self.citizens[slug]
        z.c, z.cached = c, None
        z.events.append((at, "heard-late"))

    def beat(self, slug: str, at: _dt.datetime, d: Due, next_since=None) -> Beat:
        """A silent landed beat: the letters shown are marked seen, a pending
        proposal lapses, the pace letter is noted once, and the clock moves."""
        z = self.citizens[slug]
        last = due.last_beat(z.c, z.rec, at)
        shown = []
        if d.reason != "show-back":
            shown = [rel for rel, _t in z.unread[:MAX_SHOWN]]
            if not shown and z.show:
                shown, z.show = [SHOW], False
        letters = [rel for rel in shown if rel != SHOW]
        z.unread = [(rel, t) for rel, t in z.unread if rel not in letters]
        lapsed = z.c.pending is not None
        z.c.pending = None
        if z.c.asked_once is None and any(PACE_MARK in rel for rel in letters):
            z.c.asked_once = clock.day(at)
        if shown or lapsed:
            z.c.last_beat = clock.stamp(at)      # the beat's commit carries it
        z.rec.update(last_beat=clock.stamp(at), seeded=None, failures=0, retry_after=None)
        z.cached, z.episode = None, None
        z.events.append((at, "beat", d.reason, tuple(shown)))
        return Beat(slug, at, d.reason, d.since, last, tuple(shown), next_since)

    def fail(self, slug: str, at: _dt.datetime) -> Failure:
        """A home-failed attempt, as attempts.record_outcome backs it off."""
        z = self.citizens[slug]
        n = z.rec["failures"] + 1
        until = at + attempts.BACKOFF[min(n, len(attempts.BACKOFF)) - 1]
        z.rec.update(failures=n, retry_after=clock.stamp(until))
        z.cached = None
        return Failure(slug, at, n, until)


# -- the run --------------------------------------------------------------------------------

def recipients(record: dict) -> List[str]:
    """The pace letter's recipients: the record's entries in its seven recipient groups."""
    citizens = record.get("citizens") if isinstance(record, dict) else None
    return sorted(s for s, e in (citizens or {}).items()
                  if isinstance(e, dict) and e.get("group") in records.RECIPIENT_GROUPS
                  and e.get("recipient") is not False)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _aware(t) -> bool:
    return isinstance(t, _dt.datetime) and t.tzinfo is not None and t.utcoffset() is not None


def _check_run(days, cap, tick_hours, recipients_=None, deliver_at=None, per_run=None, outages=(),
               cursor=0, start=None) -> None:
    if start is not None and not _aware(start):
        raise ValueError(f"the start must be a time with its zone (UTC), not {start!r}")
    if deliver_at is not None and not _aware(deliver_at):
        raise ValueError(f"the delivery time must be a time with its zone (UTC), not {deliver_at!r}")
    if type(days) is not int or days < 1:
        raise ValueError(f"days must be a whole number from 1, not {days!r}")
    if type(cap) is not int or cap < 0:
        raise ValueError(f"the cap must be a whole number from 0, not {cap!r}")
    if not _number(tick_hours) or not 0 < tick_hours < float("inf"):      # NaN fails it too
        raise ValueError(f"a tick comes every so many hours, more than 0, not {tick_hours!r}")
    minutes = tick_hours * 60
    if round(minutes) < 1 or abs(minutes - round(minutes)) > 1e-9:
        raise ValueError(f"a tick is a whole number of minutes, not {tick_hours!r} h")
    if days * 24 * 60 / round(minutes) > MAX_TICKS:
        raise ValueError(f"more than {MAX_TICKS} ticks; use fewer days or a longer tick")
    if recipients_ is not None and deliver_at is None:
        raise ValueError("letters need a delivery time")
    if recipients_ is None and (deliver_at is not None or per_run is not None):
        raise ValueError("a delivery time and per_run go with letters")
    if per_run is not None and (type(per_run) is not int or per_run < 1):
        raise ValueError(f"per_run must be a whole number from 1, not {per_run!r}")
    for span in outages:
        a, b = span
        if not (_aware(a) and _aware(b) and a < b):
            raise ValueError(f"an outage runs from one time to a later one, not {span!r}")
    if type(cursor) is not int or cursor < 0:
        raise ValueError(f"the cursor must be a whole number from 0, not {cursor!r}")


def ticks(start: _dt.datetime, days: int, tick_hours: float, outages=()) -> List[_dt.datetime]:
    """The tick times from start, every tick_hours, for days, leaving out any
    time inside an outage [from, to)."""
    step = _dt.timedelta(minutes=round(tick_hours * 60))
    end = start + days * DAY
    out, k = [], 0
    while True:
        t = start + k * step
        if t >= end:
            return out
        if not any(a <= t < b for a, b in outages):
            out.append(t)
        k += 1


def _plan_delivery(model: Model, r: Result, wanted, deliver_at, per_run, end) -> list:
    """The delivery runs: [(time, [slug])], one a day from deliver_at, at most
    per_run letters each, in roster order. A recipient the tool would refuse,
    or that a run would not reach before the end, is noted in r.not_placed."""
    wanted = set(wanted)
    queue = []
    for slug in model.slugs:
        if slug not in wanted:
            continue
        z = model.citizens[slug]
        why = ("no home" if not z.exists else "cannot be read" if z.error else
               "no soul" if not z.soul else "links" if z.links or z.refused else
               "already holds a pace letter" if z.pace_letter else "")
        if why:
            r.not_placed.append((slug, why))
        else:
            queue.append(slug)
    r.not_placed += [(slug, "not on the roster") for slug in sorted(wanted - set(model.slugs))]
    size = per_run or len(queue)
    runs, k = [], 0
    while queue:
        at = deliver_at + k * DAY
        if at >= end:
            break
        runs.append((at, queue[:size]))
        queue, k = queue[size:], k + 1
    r.not_placed += [(slug, "not placed by the end") for slug in queue]
    return runs


def _place(model: Model, r: Result, at: _dt.datetime, batch: list) -> None:
    rel = letter_path(clock.day(at))
    for slug in batch:
        model.place(slug, rel, at)
        r.placed.append(Placed(slug, rel, at))
        r.arrived[(slug, rel)] = at
    r.runs.append(at)


def _hear_late(model: Model, r: Result, at: _dt.datetime, record: dict, state) -> None:
    """The heard-late step at the first tick, as the fleet runs it: what
    record.migrate_heard_late would commit, without writing anything."""
    for slug in model.slugs:
        e = records.entry(record, slug)
        z = model.citizens[slug]
        if not e or e.get("group") not in records.HEARD_LATE_GROUPS or not z.exists:
            continue
        try:
            if not records.migrate_heard_late(z.home, slug, record, at, commit=False, state=state):
                continue
            c = records.heard_late(z.home, slug, record, at)
        except cad.MigrationBlocked as err:
            r.stopped.append((slug, str(err)))
            continue
        except (tend.TendError, LinkRefused, OSError, ValueError) as err:
            r.stopped.append((slug, f"{slug}: {err}"))
            continue
        if c is not None:
            model.hear(slug, c, at)
            r.heard_late.append(slug)


def simulate(homes_dir, roster, record: dict, days: int, *, start: Optional[_dt.datetime] = None,
             cap: int = CAP, tick_hours: float = TICK_HOURS, state=None, recipients=None,
             deliver_at: Optional[_dt.datetime] = None, per_run: Optional[int] = None, failing=(),
             outages=(), cursor: int = 0, confirm_beat: Optional[bool] = None) -> Result:
    """Model `days` of the new fleet over these homes (see the top of this file).
    recipients get a pace letter from deliver_at; failing names homes whose
    every beat fails; outages are [(from, to)] spans with no tick. Writes nothing."""
    outages = [tuple(span) for span in outages]
    _check_run(days, cap, tick_hours, recipients, deliver_at, per_run, outages, cursor, start)
    start = (start or clock.now()).astimezone(clock.UTC).replace(second=0, microsecond=0)
    end = start + days * DAY
    times = ticks(start, days, tick_hours, outages)
    failing = set(failing)
    model = Model(homes_dir, roster, record, start, state=state, confirm_beat=confirm_beat)
    r = Result(start, end, days, cap, float(tick_hours), model.confirm, list(model.slugs), ticks=times,
               outages=outages, per_run=per_run, record_empty=not record)
    for slug, z in model.citizens.items():
        for rel, t in z.unread:
            r.arrived[(slug, rel)] = min(t, start)
    runs = _plan_delivery(model, r, recipients, deliver_at, per_run, end) if recipients is not None else []
    order = due._order(model.slugs, cursor)
    landed: Counter = Counter()
    for i, t in enumerate(times):
        while runs and runs[0][0] <= t:
            _place(model, r, *runs.pop(0))
        if i == 0:
            _hear_late(model, r, t, record, state)
            r.paces = {s: model.label(s) for s in model.slugs}
        assessed = [(s, model.due(s, t)) for s in model.slugs]
        for s, d in assessed:
            if d.due and model.citizens[s].episode is None:
                model.citizens[s].episode = d.since
        ranked = due._ranked(assessed, order)
        if not ranked:
            continue
        if landed[clock.day(t)] >= cap:
            r.capped += 1
            continue
        slug, d = ranked[0]
        if slug in failing:
            r.failures.append(model.fail(slug, t))
            continue
        b = model.beat(slug, t, d, ranked[1][1].since if len(ranked) > 1 else None)
        r.beats.append(b)
        for rel in b.shown:
            if rel != SHOW:
                r.read[(slug, rel)] = t
        landed[clock.day(t)] += 1
        order = due._order(model.slugs, model.citizens[slug].index + 1)
    for run in runs:
        _place(model, r, *run)
    if not times:
        r.paces = {s: model.label(s) for s in model.slugs}
    for slug, z in model.citizens.items():
        d = r.ends[slug] = model.due(slug, end)
        if d.due and z.episode is None:
            z.episode = d.since
        if z.episode is not None and z.episode < end:     # due only at the end: no wait yet
            r.open_since[slug] = z.episode
        if z.c is not None and z.c.asked_once:
            r.asked_once[slug] = z.c.asked_once
        r.events[slug] = list(z.events)
    return r


# -- the report -------------------------------------------------------------------------------

def _hours(td: _dt.timedelta) -> str:
    return f"{td.total_seconds() / 3600:.1f}"


def _counts(counter: Counter) -> str:
    return " · ".join(f"{k} {n}" for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _letter_lines(r: Result) -> List[str]:
    out = []
    if r.placed or r.not_placed:
        runs = sorted(set(r.runs))
        if len(runs) == 1:
            out.append(f"pace letters: {len(r.placed)} placed in one run, {clock.stamp(runs[0])}")
        elif runs:
            out.append(f"pace letters: {len(r.placed)} placed, at most {r.per_run} a run, one run a day, "
                       f"{clock.stamp(runs[0])} to {clock.stamp(runs[-1])} ({len(runs)} runs)")
        else:
            out.append("pace letters: none placed")
        if r.not_placed:
            out.append("not placed: " + ", ".join(f"{s} ({why})" for s, why in r.not_placed))
    order = {s: i for i, s in enumerate(r.roster)}
    keys = [k for k in r.arrived if PACE_MARK in k[1]]
    if keys:
        read = [k for k in keys if k in r.read]
        line = f"pace letters read: {len(read)} of {len(keys)}"
        if read:
            last = max(read, key=lambda k: (r.read[k], order.get(k[0], 0)))
            line += (f"; the last at {clock.stamp(r.read[last])} ({last[0]}), "
                     f"{_hours(r.read[last] - r.arrived[last])} h after it arrived")
        unread = sorted({k[0] for k in keys if k not in r.read}, key=lambda s: order.get(s, 0))
        if unread:
            line += "; not read by the end: " + ", ".join(unread)
        out.append(line)
    others = [k for k in r.arrived if PACE_MARK not in k[1]]
    if others:
        read_n = sum(1 for k in others if k in r.read)
        out.append(f"other letters read: {read_n} of {len(others)}")
    return out


def report(r: Result) -> str:
    """The plan's report, labelled as a model of the Studio."""
    out = [f"plan — {LABEL}",
           f"{clock.stamp(r.start)} for {r.days} days: a tick every {r.tick_hours:g} h ({len(r.ticks)} "
           f"ticks), at most {r.cap} beats a day, one a tick; show-back beats "
           f"{'on' if r.confirm_beat else 'off'}"]
    out += [f"no ticks from {clock.stamp(a)} to {clock.stamp(b)}" for a, b in r.outages]
    out.append(f"{len(r.roster)} citizens on the roster: {_counts(Counter(r.paces.values()))}")
    if r.record_empty:
        out.append("no record file: a decided v1 home cannot be read without it, so it is blocked")
    out.append(SILENT)
    if r.heard_late:
        out.append("heard late at the first tick: " + ", ".join(r.heard_late))
    out += [f"heard-late step stopped — {why}" for _slug, why in r.stopped]
    out += _letter_lines(r)
    kinds = Counter(b.kind for b in r.beats)
    out.append(f"beats: {len(r.beats)} (" + " · ".join(f"{k} {kinds[k]}" for k in due.DUE_REASONS[:3])
               + f"); failed attempts: {len(r.failures)}; ticks the cap held a due citizen back: {r.capped}")
    by: Dict[str, List[Beat]] = {}
    for b in r.beats:
        by.setdefault(b.slug, []).append(b)
    failed = {f.slug for f in r.failures}
    rest = Counter(("due (" + r.ends[s].reason + ")") if r.ends[s].due else r.ends[s].reason
                   for s in r.roster if s not in by)
    if rest:
        out.append(f"not beaten in the run: {_counts(rest)}")
    look = [f"{s} ({r.ends[s].reason})" for s in r.roster if r.ends[s].reason in NEEDS_A_LOOK]
    if look:
        out.append("needs a look: " + ", ".join(look))
    rows = [s for s in r.roster if s in by or s in failed or s in r.open_since]
    awake = [(r.longest_wait(s), s) for s in rows if r.paces.get(s, "").startswith("awake")]
    if awake:
        worst, slug = max(awake, key=lambda ws: ws[0])
        out.append(f"longest wait of an awake citizen: {_hours(worst)} h ({slug})")
    out.append("citizen\tpace\tbeats\tlongest wait (h)\tstill waiting")
    for s in rows:
        still = f"since {clock.stamp(r.open_since[s])}" if s in r.open_since else ""
        out.append(f"{s}\t{r.paces.get(s, '')}\t{len(by.get(s, []))}\t{_hours(r.longest_wait(s))}\t{still}")
    return "\n".join(out) + "\n"


# -- the out folder ---------------------------------------------------------------------------

def _git(home, *args):
    return homes.git(home, *_SAFE, *args)


def check_out(homes_dir, out) -> None:
    """The out folder must be new or empty, and must not be, hold or sit inside
    the homes folder."""
    h, o = os.path.realpath(homes_dir), os.path.realpath(out)
    if o == h or o.startswith(h + os.sep) or h.startswith(o + os.sep):
        raise ValueError(f"{out}: the out folder must not be, hold or sit inside the homes folder {homes_dir}")
    if os.path.lexists(out):
        if os.path.islink(out) or not os.path.isdir(out):
            raise ValueError(f"{out}: not a folder")
        if os.listdir(out):
            raise ValueError(f"{out}: not empty; the out folder must be new or empty")


def _special(folder, names) -> List[str]:
    """Names that are neither folders, regular files nor links: never copied."""
    skip = []
    for n in names:
        try:
            m = os.lstat(os.path.join(folder, n)).st_mode
        except OSError:
            skip.append(n)
            continue
        if not (stat.S_ISDIR(m) or stat.S_ISREG(m) or stat.S_ISLNK(m)):
            skip.append(n)
    return skip


def _checked(r, what: str) -> None:
    if r.returncode != 0:
        raise tend.TendError(f"git {what} failed: {r.stderr.strip()}")


def _copy_home(src: Path, dst: Path) -> None:
    """A copy of the home as a git repository of its own, with no remote, so
    nothing done to it can reach GitHub or the home it came from."""
    shutil.copytree(src, dst, symlinks=True, ignore=_special)
    g = dst / ".git"
    if os.path.lexists(g) and (os.path.islink(g) or not os.path.isdir(g)):
        os.unlink(g)         # a .git file or link names another repository: never followed
    if os.path.isdir(g):
        for name in _git(dst, "remote").stdout.split():
            _checked(_git(dst, "remote", "remove", name), "remote remove")
        return
    _checked(_git(dst, "init", "-q"), "init")
    _checked(_git(dst, "add", "-A"), "add")
    _checked(_git(dst, "-c", f"user.name={tend.FLEET_NAME}", "-c", f"user.email={tend.FLEET_EMAIL}",
                  "-c", "commit.gpgsign=false",
                  "commit", "-q", "--no-verify", "--allow-empty", f"--author={tend.FLEET_AUTHOR}",
                  "-m", BASE_SUBJECT), "commit")


def _replay(dst: Path, slug: str, ev: tuple, record: dict) -> None:
    at, kind = ev[0], ev[1]
    if kind == "letter":
        homes.write_regular(dst / ev[2], LETTER_TEXT)
    elif kind == "heard-late":
        if not records.migrate_heard_late(dst, slug, record, at, commit=True):
            raise ValueError(f"the heard-late step of {clock.stamp(at)} did not apply to the copy")
    else:
        apply_answer(dst, slug, "", list(ev[3]), ev[2], at, record)


def write_out(r: Result, homes_dir, out, record: dict) -> List[str]:
    """Write each roster home as the run would leave it into out/citizen-<slug>:
    a copy, with the heard-late step, the placed letters and each silent beat
    applied by the real code at its time, then committed as the fleet. Nothing
    is written outside out. Returns a summary line, then one line per home that
    could not be written."""
    check_out(homes_dir, out)
    out = Path(out)
    os.makedirs(out, exist_ok=True)
    lines, n = [], 0
    for slug in r.roster:
        src = Path(homes_dir) / f"citizen-{slug}"
        if not src.is_dir():
            continue
        dst = out / src.name
        try:
            if os.path.islink(src):
                raise ValueError("its home is a link")
            _copy_home(src, dst)
            for ev in sorted(r.events.get(slug, ()), key=lambda e: (e[0], _EVENT_ORDER[e[1]])):
                _replay(dst, slug, ev, record)
            tend.fleet_commit(dst, tend.changed_paths(dst, tend.TEND_PATHS), OUT_SUBJECT)
        except (OSError, ValueError, LinkRefused, tend.TendError) as e:
            lines.append(f"{slug}: its end state could not be written — {e}")
            continue
        n += 1
    return [f"out: {n} homes written to {out}, as this run would leave them "
            "(committed in each, with no remote)"] + lines


# -- the rollback drill -------------------------------------------------------------------------

@dataclass
class _OldHome:
    slug: str
    exists: bool
    refused: bool            # PR #24's reflect exits before it shows anything
    marker: bool             # REST or EVENT-ONLY, of any kind, a dangling link included
    mail: list               # the letters PR #24's has_mail counts, by name
    candidates: list         # the letters its reflect would surface, by name
    unreadable: set
    seen: set = field(default_factory=set)

    def has_mail(self) -> bool:
        return any(f not in self.seen for f in self.mail)

    def keeps_rest(self) -> bool:
        return self.marker and not self.has_mail()


def _old_links(home: Path) -> bool:
    """A link where PR #24's reflect refuses one. (It checks only this month's
    journal; any linked beats/*.md is counted here, which can only keep a home
    from changing.)"""
    if any(os.path.islink(home / p) for p in ("beats", "inbox", "CADENCE.md", "REST", "EVENT-ONLY",
                                               "beats/.show-seen")):
        return True
    for folder, want in (("beats", lambda f: f.endswith(".md")), ("inbox", lambda f: f.startswith(".seen-"))):
        d = home / folder
        if os.path.isdir(d) and not os.path.islink(d):
            if any(want(f) and os.path.islink(d / f) for f in os.listdir(d)):
                return True
    return False


def _read_old(home: Path, slug: str) -> _OldHome:
    if not os.path.isdir(home):
        return _OldHome(slug, False, True, False, [], [], set())
    marker = any(os.path.lexists(home / m) for m in cad.MARKERS)
    inbox = home / "inbox"
    names = sorted(os.listdir(inbox)) if os.path.isdir(inbox) and not os.path.islink(inbox) else []
    letters = [f for f in names if f.endswith(".md") and not f.startswith(".")]
    mail = [f for f in letters if not os.path.islink(inbox / f) and os.path.isfile(inbox / f)
            and not os.path.lexists(inbox / f".seen-{f}")]
    candidates = [f for f in letters if not os.path.exists(inbox / f".seen-{f}")]
    unreadable = set()
    for f in candidates:
        try:
            if homes.read_regular(inbox / f) is None:
                unreadable.add(f)
        except (LinkRefused, ValueError, OSError):
            unreadable.add(f)
    soul = home / f"{slug}.md"
    refused = os.path.islink(soul) or not os.path.isfile(soul) or _old_links(home)
    return _OldHome(slug, True, refused, marker, mail, candidates, unreadable)


def _old_beat(h: _OldHome) -> None:
    """PR #24's reflect, met with silence: unreadable letters are set aside, the
    first readable one is surfaced, and only a Letter of Return is marked seen
    (silence is its rest-on, which writes REST); any other is set aside, unread."""
    if not h.exists or h.refused:
        return
    for f in h.candidates:
        if f in h.seen:
            continue
        if f in h.unreadable:
            h.seen.add(f)
            continue
        if "letter-of-return" in f:
            h.seen.add(f)
            h.marker = True
        return


def _why_woken(h: _OldHome) -> str:
    if not h.exists:
        return "no clone"
    if not h.marker:
        return "no marker"
    first = next(f for f in h.mail if f not in h.seen)
    return f"an unread letter: inbox/{first}"


@dataclass
class Drill:
    homes_dir: Path
    start: _dt.datetime
    days: int
    cap: int
    tick_hours: float
    ticks: List[_dt.datetime]
    roster: List[str]
    wakes: Dict[str, Dict[str, int]] = field(default_factory=dict)   # slug -> UTC day -> wakes
    why: Dict[str, str] = field(default_factory=dict)                # what woke it the first time

    @property
    def dates(self) -> List[str]:
        return sorted({clock.day(t) for t in self.ticks})

    @property
    def total(self) -> int:
        return sum(sum(v.values()) for v in self.wakes.values())


def rollback_drill(homes_dir, roster, days: int, *, start: Optional[_dt.datetime] = None, cap: int = CAP,
                   tick_hours: float = TICK_HOURS, cursor: int = 0) -> Drill:
    """The wakes PR #24's fleet would make over these homes (spec section 9,
    step 3): at each tick, from the cursor, it skips every citizen that
    keeps_rest (REST or EVENT-ONLY present and no unread letter), and beats the
    first that does not, at most `cap` a UTC day. Every citizen is silent.
    Reads the homes once; writes nothing."""
    _check_run(days, cap, tick_hours, cursor=cursor, start=start)
    start = (start or clock.now()).astimezone(clock.UTC).replace(second=0, microsecond=0)
    slugs = due._slugs(roster)
    old = [_read_old(Path(homes_dir) / f"citizen-{s}", s) for s in slugs]
    times = ticks(start, days, tick_hours)
    d = Drill(Path(homes_dir), start, days, cap, float(tick_hours), times, slugs)
    total, cur, count = len(old), cursor, Counter()
    if not total:
        return d
    for t in times:
        day = clock.day(t)
        if count[day] >= cap:
            continue
        scanned = 0
        while scanned < total and old[cur % total].keeps_rest():
            cur, scanned = cur + 1, scanned + 1
        if scanned >= total:
            continue            # the whole roster is resting
        h = old[cur % total]
        cur, count[day] = cur + 1, count[day] + 1
        wakes = d.wakes.setdefault(h.slug, {})
        wakes[day] = wakes.get(day, 0) + 1
        d.why.setdefault(h.slug, _why_woken(h))
        _old_beat(h)
    return d


def drill_report(d: Drill) -> str:
    why = Counter("no clone" if w == "no clone" else "no marker" if w == "no marker" else "letter"
                  for w in d.why.values())
    pace = sum(1 for w in d.why.values() if PACE_MARK in w)
    out = [f"rollback drill — {DRILL_LABEL}",
           f"{clock.stamp(d.start)} for {d.days} days: a tick every {d.tick_hours:g} h ({len(d.ticks)} ticks), "
           f"at most {d.cap} beats a day, one a tick",
           DRILL_SILENT,
           f"{len(d.roster)} citizens on the roster; woken at least once: {len(d.wakes)} (no marker "
           f"{why['no marker']} · an unread letter {why['letter']}, a pace letter among them {pace} · "
           f"no clone {why['no clone']})",
           "date\twakes"]
    dates = d.dates
    out += [f"{day}\t{sum(w.get(day, 0) for w in d.wakes.values())}" for day in dates]
    out.append("citizen\twoken by\twakes\tper day")
    for slug in d.roster:
        if slug in d.wakes:
            w = d.wakes[slug]
            out.append(f"{slug}\t{d.why[slug]}\t{sum(w.values())}\t" + " ".join(str(w.get(x, 0)) for x in dates))
    out.append(f"total: {d.total} wakes in {d.days} days")
    return "\n".join(out) + "\n"


# -- the commands (citizen_pace.py plan, rollback-drill) ---------------------------------------

def _parse(p: argparse.ArgumentParser, argv) -> Tuple[Optional[argparse.Namespace], int]:
    try:
        return p.parse_args(list(argv)), 0
    except SystemExit as e:
        return None, (2 if e.code else 0)


def _too_large(e: OverflowError) -> str:
    return f"the run cannot be modelled: its days or its tick are too large for the calendar ({e})"


def cli_plan(argv, out=None) -> int:
    """`plan <days> <homes-dir> --roster <f> [--cap N] [--tick-hours H]
    [--letters <record> --deliver-at <time> [--per-run N]] [--out <dir>]
    [--record <f>] [--state <dir>] [--cursor N]`: print the report; 0, or 2 for
    a usage error or a refusal."""
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="citizen_pace.py plan",
                                description="Model the new fleet over these homes. Writes nothing, but --out.")
    p.add_argument("days", type=int)
    p.add_argument("homes_dir")
    p.add_argument("--roster", required=True)
    p.add_argument("--cap", type=int, default=CAP)
    p.add_argument("--tick-hours", type=float, default=TICK_HOURS)
    p.add_argument("--letters", help="a record file: its recipients get a pace letter")
    p.add_argument("--deliver-at", help="when the first delivery run places them")
    p.add_argument("--per-run", type=int, help="at most this many letters a run, one run a day")
    p.add_argument("--out", help="an empty folder for the homes as the run would leave them")
    p.add_argument("--record", help="the record file (default: citizens-pace-record.json)")
    p.add_argument("--state", help="attempt records to read, never written")
    p.add_argument("--cursor", type=int, default=0)
    a, code = _parse(p, argv)
    if a is None:
        return code
    try:
        if not a.letters and (a.deliver_at or a.per_run is not None):
            raise ValueError("--deliver-at and --per-run go with --letters")
        if a.letters and not a.deliver_at:
            raise ValueError("--letters needs --deliver-at <time>")
        rec = records.load_record(Path(a.record) if a.record else None)
        wanted = deliver_at = None
        if a.letters:
            if not os.path.isfile(a.letters):
                raise ValueError(f"{a.letters}: no such record file")
            wanted = recipients(records.load_record(Path(a.letters)))
            if not wanted:
                raise ValueError(f"{a.letters} names no recipients")
            deliver_at = clock.parse_stamp(a.deliver_at)
        if a.out:
            check_out(a.homes_dir, a.out)
        r = simulate(a.homes_dir, due.read_roster(a.roster), rec, a.days, cap=a.cap,
                     tick_hours=a.tick_hours, state=a.state, recipients=wanted, deliver_at=deliver_at,
                     per_run=a.per_run, cursor=a.cursor)
        text = report(r)
        lines = write_out(r, a.homes_dir, a.out, rec) if a.out else []
    except OverflowError as e:
        print(f"plan: {_too_large(e)}", file=sys.stderr)
        return 2
    except (OSError, ValueError, LinkRefused, tend.TendError) as e:
        print(f"plan: {e}", file=sys.stderr)
        return 2
    out.write(text + "".join(ln + "\n" for ln in lines))
    return 0


def cli_rollback_drill(argv, out=None) -> int:
    """`rollback-drill <homes-dir> --roster <f> --days N [--cap N]
    [--tick-hours H] [--cursor N]`: print the wakes per day per home; 0, or 2."""
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="citizen_pace.py rollback-drill",
                                description="Count the wakes PR #24's fleet would make over these homes.")
    p.add_argument("homes_dir")
    p.add_argument("--roster", required=True)
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--cap", type=int, default=CAP)
    p.add_argument("--tick-hours", type=float, default=TICK_HOURS)
    p.add_argument("--cursor", type=int, default=0)
    a, code = _parse(p, argv)
    if a is None:
        return code
    try:
        d = rollback_drill(a.homes_dir, due.read_roster(a.roster), a.days, cap=a.cap,
                           tick_hours=a.tick_hours, cursor=a.cursor)
    except OverflowError as e:
        print(f"rollback-drill: {_too_large(e)}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"rollback-drill: {e}", file=sys.stderr)
        return 2
    out.write(drill_report(d))
    return 0
