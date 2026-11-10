from __future__ import annotations

# Who is due (spec section 3). The fleet only ever answers a citizen's own
# clock: an awake citizen is due N days after its last beat, and its letters
# wait for that beat; an asleep or listening citizen is due when an unread
# letter waits and 72 hours have passed since its last beat; and a citizen that
# asked for more is owed one show-back beat, 72 hours after the beat that said
# it. Nothing else makes a citizen due. Its last beat is the later of
# CADENCE.md's `last beat` and the attempt record's last landed beat; with
# neither, the clock starts now, and only the fleet's ranking and the reflect
# gate write that start. The record that decides is cadence.read_deciding's: a
# CADENCE.md changed outside pace/1 does not decide over its last pace/1
# version. A person's summons passes any pace, floor and back-off, but never a
# home with no soul, a linked pace path, a merge in progress or a fresh
# index.lock.

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import attempts, cadence, clock, homes, lock
from .clock import DAY, FLOOR, HOUR
from .homes import LinkRefused

# Yu's answer to KS-004 section 15 item 16 (spec sections 1 and 9): True gives an
# asleep, asleep-letters-wait or listening citizen that asks for more one
# show-back beat, no sooner than 3 days after the beat that said it. It is set
# in the PR before the merge and nothing else overrides it; the delivery tool
# reads this same value to word the letter.
WAKE_CONFIRM_BEAT = True

DUE_REASONS = ("schedule", "letter", "show-back", "summoned")
NOT_DUE_REASONS = ("no-home", "no-soul", "links", "mid-merge", "blocked", "backing-off",
                   "letters-wait", "floor", "not-yet", "no-mail", "unreadable", "error")
SHOW_BACK_KINDS = ("asleep-letters-wait", "asleep", "listening")
# the proposal's stamp (from apply) and the attempt record's stamp for the same
# beat can differ by the time the commit took; a landed beat within this of the
# proposal is that same beat
SAME_BEAT = HOUR


@dataclass(frozen=True)
class Due:
    due: bool
    reason: str                              # one of DUE_REASONS or NOT_DUE_REASONS
    since: Optional[_dt.datetime] = None     # due: when it became due
    next_at: Optional[_dt.datetime] = None   # not due: the earliest it may become due, when known

    def overdue_s(self, now: _dt.datetime) -> int:
        """Seconds since it became due; 0 when not due."""
        if not self.due or self.since is None:
            return 0
        return max(0, int((now - self.since).total_seconds()))

    def to_json(self, now: _dt.datetime) -> dict:
        return {"due": self.due, "reason": self.reason, "overdue_s": self.overdue_s(now),
                "since": clock.stamp(self.since) if self.since else None,
                "next_at": clock.stamp(self.next_at) if self.next_at else None}


def _when(v) -> Optional[_dt.datetime]:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return clock.parse_stamp(v)
    except ValueError:
        return None


def last_beat(c: Optional[cadence.Cadence], rec: dict,
              now: Optional[_dt.datetime] = None) -> Optional[_dt.datetime]:
    """The later of CADENCE.md's last beat and the attempt record's. With now, a
    time later than now is no last beat: unknown state starts the clock from now."""
    found = [_when(c.last_beat) if c is not None else None, attempts.last_beat_at(rec)]
    found = [t for t in found if t is not None and (now is None or t <= now)]
    return max(found) if found else None


def show_back_owed(c: Optional[cadence.Cadence], last: Optional[_dt.datetime],
                   confirm_beat: Optional[bool] = None) -> bool:
    """One show-back beat is owed: the switch is on, an asleep, letters-wait or
    listening citizen has a pending proposal that was not said on a show-back
    beat, and no landed beat came after the beat that said it."""
    confirm = WAKE_CONFIRM_BEAT if confirm_beat is None else confirm_beat
    if not confirm or c is None or c.pending is None or c.pending.on_show_back:
        return False
    if c.pace is None or c.pace.kind not in SHOW_BACK_KINDS:
        return False
    said = clock.parse_stamp(c.pending.said)
    return last is None or last <= said + SAME_BEAT


def _by_pace(c: cadence.Cadence, last: _dt.datetime, now: _dt.datetime,
             mail: Sequence[_dt.datetime], confirm: Optional[bool]) -> Due:
    kind = c.pace.kind if c.pace is not None else None
    if kind == "awake":
        at = last + c.pace.n * DAY
        return Due(True, "schedule", at) if now >= at else Due(False, "not-yet", next_at=at)
    back = None
    if show_back_owed(c, last, confirm):
        back = max(clock.parse_stamp(c.pending.said), last) + FLOOR
    if kind == "asleep-letters-wait":
        if back is None:
            return Due(False, "letters-wait")
        return Due(True, "show-back", back) if now >= back else Due(False, "floor", next_at=back)
    floor = last + FLOOR
    if mail and now >= floor:
        return Due(True, "letter", max(min(mail), floor))
    if back is not None and now >= back:
        return Due(True, "show-back", back)
    if mail or back is not None:
        return Due(False, "floor", next_at=floor if mail else back)
    return Due(False, "no-mail" if kind is not None else "unreadable")


def is_due(c: Optional[cadence.Cadence], rec: dict, now: _dt.datetime, mail_times: list,
           soul: bool, links: list, midmerge: bool, summoned: bool = False,
           confirm_beat: Optional[bool] = None) -> Due:
    """Whether one citizen is due now. Checked in order: no soul, links, a merge
    or fresh index.lock (never due, even summoned); a summons (due); a record
    that must not become v2 (blocked); back-off; then its pace. mail_times are
    the arrival times of its unread letters. confirm_beat None reads
    WAKE_CONFIRM_BEAT at call time."""
    if not soul:
        return Due(False, "no-soul")
    if links:
        return Due(False, "links")
    if midmerge:
        return Due(False, "mid-merge")
    if summoned:
        return Due(True, "summoned", now)
    if c.blocked:
        return Due(False, "blocked")
    by_pace = _by_pace(c, last_beat(c, rec, now) or now, now, mail_times, confirm_beat)
    if attempts.backing_off(rec, now):
        until = attempts.retry_after(rec)
        if by_pace.due:
            nxt = until
        else:
            nxt = max(by_pace.next_at, until) if by_pace.next_at else None
        return Due(False, "backing-off", next_at=nxt)
    return by_pace


# -- one home on disk ------------------------------------------------------------------

def fresh_index_lock(home, bound: Optional[int] = None) -> bool:
    """.git/index.lock exists and is no older than twice the beat bound."""
    age = homes.index_lock_age(home)
    return age is not None and age <= 2 * (lock.beat_bound() if bound is None else bound)


def mail_times(home, now: _dt.datetime) -> List[_dt.datetime]:
    """When each unread letter arrived (homes.letter_time, clamped to now)."""
    return [homes.letter_time(home, rel, now) for rel in homes.unread_letters(home)]


def due_for_home(home, name: str, state, record: dict, now: _dt.datetime, summoned: bool = False,
                 write_seed: bool = True, bound: Optional[int] = None) -> Due:
    """Whether the citizen in this home is due now. With write_seed (the fleet's
    ranking and the reflect gate), a citizen with no last beat anywhere has its
    clock started now in the attempt record; next and plan pass False. A home
    that cannot be read is not due ("error"), a stamp that no date can reach
    (a proposal said at the end of time) included."""
    home = Path(home)
    try:
        if not home.is_dir():
            return Due(False, "no-home")
        soul = homes.has_soul(home, name)
        links = homes.forbidden_links(home) if soul else []
        midmerge = bool(soul and not links and (homes.mid_merge(home) or fresh_index_lock(home, bound)))
        if not soul or links or midmerge or summoned:
            return is_due(None, {}, now, [], soul, links, midmerge, summoned)
        try:
            c = cadence.read_deciding(home, name, record, now)
        except LinkRefused:
            return Due(False, "links")
        rec = attempts.load(state, name)
        if write_seed and last_beat(c, rec, now) is None:
            rec = attempts.seed(state, name, now)
        wants_mail = not c.blocked and (c.pace is None or c.pace.kind in ("asleep", "listening"))
        mail = mail_times(home, now) if wants_mail else []
        return is_due(c, rec, now, mail, soul, links, midmerge)
    except (OSError, ValueError, OverflowError, RecursionError, LinkRefused):
        return Due(False, "error")


# -- the roster --------------------------------------------------------------------------

def read_roster(path) -> List[str]:
    """The roster's citizen names in order: blank lines, '#' notes, repeats and
    anything that is not a plain name are skipped."""
    out: List[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        slug = line.strip()
        if slug and not slug.startswith("#") and attempts.valid_slug(slug) and slug not in out:
            out.append(slug)
    return out


def _slugs(roster) -> List[str]:
    if isinstance(roster, (str, os.PathLike)):
        return read_roster(roster)
    out: List[str] = []
    for s in roster:
        if attempts.valid_slug(s) and s not in out:
            out.append(s)
    return out


def assess(homes_dir, roster, state, record: dict, now: _dt.datetime,
           write_seed: bool = True) -> List[Tuple[str, Due]]:
    """(slug, Due) for every roster citizen, in roster order."""
    return [(slug, due_for_home(Path(homes_dir) / f"citizen-{slug}", slug, state, record, now,
                                write_seed=write_seed))
            for slug in _slugs(roster)]


def _order(slugs: List[str], cursor: int) -> dict:
    n = len(slugs) or 1
    start = cursor % n
    return {slug: (i - start) % n for i, slug in enumerate(slugs)}


def _ranked(assessed: List[Tuple[str, Due]], order: dict) -> List[Tuple[str, Due]]:
    due_now = [(s, d) for s, d in assessed if d.due]
    return sorted(due_now, key=lambda sd: (sd[1].since, order[sd[0]]))


def rank(homes_dir, roster, state, record: dict, now: _dt.datetime, cursor: int = 0,
         write_seed: bool = True) -> List[Tuple[str, Due]]:
    """The due citizens, most overdue first; ties go by roster order from the
    cursor. A home that fails to read is not due and holds up no one."""
    slugs = _slugs(roster)
    return _ranked(assess(homes_dir, slugs, state, record, now, write_seed), _order(slugs, cursor))


def next_line(homes_dir, roster, state, record: dict, now: _dt.datetime, cursor: int = 0) -> str:
    """fleet-status.sh's "next to wake" line. Writes nothing."""
    slugs = _slugs(roster)
    order = _order(slugs, cursor)
    assessed = assess(homes_dir, slugs, state, record, now, write_seed=False)
    ranked = _ranked(assessed, order)
    if ranked:
        slug, d = ranked[0]
        return f"next to wake: {slug} ({d.reason}, overdue {d.overdue_s(now) // 3600}h)"
    upcoming = sorted((d.next_at, order[s], s) for s, d in assessed if d.next_at is not None)
    if upcoming:
        at, _, slug = upcoming[0]
        return f"next to wake: nobody due; next due {slug} at {clock.stamp(at)}"
    return "next to wake: nobody due"
