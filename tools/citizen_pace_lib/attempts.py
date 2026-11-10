from __future__ import annotations

# The attempt record (spec section 3, "Keeping the clock honest"): one small JSON
# file per citizen, <state>/<slug>, where <state> is $LOVE/memory/.fleet-last
# (git-ignored, as memory/.fleet-* already is). The fleet and
# citizen-reflect.sh share it. It keeps the last landed beat (spoken or silent),
# the clock start written when no beat was ever recorded, the last attempt and
# its outcome, the run of failures and the back-off it earns, the per-home
# "reported once" flags, notices for the next pace paragraph, and how many fleet
# ticks in a row found the citizen busy. Every change is a read-modify-write
# under a file lock, and every write is atomic.

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Callable, Iterator, List, Optional

from . import clock
from .clock import DAY, HOUR
from .homes import LinkRefused, read_regular, write_regular

OUTCOMES = ("spoken", "silent", "not-due", "busy", "home-failed", "host-failed", "halted", "summoned")
LANDED = ("spoken", "silent")
# the child writes every outcome but host-failed; the fleet writes host-failed
# after a failed probe, and home-failed or halted when the child wrote nothing
CHILD_OUTCOMES = tuple(o for o in OUTCOMES if o != "host-failed")
BACKOFF = (12 * HOUR, 24 * HOUR, 7 * DAY)   # after failures 1, 2, and 3 or more
HAND_PREFIX = "hand-"                       # a hand-run's attempt id; the fleet's ids never start so
NOTICE_CAP = 20
SEEDED = "now"
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
_ID = re.compile(r"\S{1,200}")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
# what an attempt's outcome may change, kept so a second outcome for the same
# attempt (the fleet's host-failed after the child's home-failed) can undo it
_UNDO = ("last_beat", "seeded", "failures", "retry_after", "busy_ticks")


def valid_slug(slug) -> bool:
    """A citizen's name as it may name a file: letters, digits, '-' and '_'."""
    return isinstance(slug, str) and bool(_SLUG.fullmatch(slug))


def record_path(state, slug: str) -> Path:
    if not valid_slug(slug):
        raise ValueError(f"{slug!r} is not a citizen name")
    return Path(state) / slug


def fresh(slug: str) -> dict:
    """The record of a citizen with no attempt yet."""
    return {"slug": slug, "last_beat": None, "seeded": None, "attempt": None, "failures": 0,
            "retry_after": None, "reported": [], "notices": [], "busy_ticks": 0}


# -- reading and cleaning ------------------------------------------------------------

def _stamp_or_none(v) -> Optional[str]:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return clock.stamp(clock.parse_stamp(v))
    except ValueError:
        return None


def _when(v) -> Optional[_dt.datetime]:
    s = _stamp_or_none(v)
    return clock.parse_stamp(s) if s else None


def _count(v) -> int:
    return v if type(v) is int and v >= 0 else 0


def _stamp_of(at) -> str:
    """A time given as a datetime or a stamp, as a stamp."""
    if isinstance(at, _dt.datetime):
        return clock.stamp(at)
    s = _stamp_or_none(at)
    if s is None:
        raise ValueError(f"{at!r} is not a time")
    return s


def _undo_part(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    last = _stamp_or_none(raw.get("last_beat"))
    return {"last_beat": last, "seeded": SEEDED if last and raw.get("seeded") == SEEDED else None,
            "failures": _count(raw.get("failures")), "retry_after": _stamp_or_none(raw.get("retry_after")),
            "busy_ticks": _count(raw.get("busy_ticks"))}


def _attempt(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    aid, outcome, at = raw.get("id"), raw.get("outcome"), _stamp_or_none(raw.get("at"))
    if not (isinstance(aid, str) and _ID.fullmatch(aid)) or outcome not in OUTCOMES or not at:
        return None
    before = raw.get("before")
    return {"id": aid, "at": at, "outcome": outcome,
            "before": _undo_part(before) if isinstance(before, dict) else None}


def _clean(slug: str, raw) -> dict:
    rec = fresh(slug)
    if not isinstance(raw, dict):
        return rec
    rec.update(_undo_part(raw))
    rec["attempt"] = _attempt(raw.get("attempt"))
    reported = raw.get("reported")
    if isinstance(reported, list):
        for k in reported:
            if isinstance(k, str) and _KEY.fullmatch(k) and k not in rec["reported"]:
                rec["reported"].append(k)
    notices = raw.get("notices")
    if isinstance(notices, list):
        for n in notices:
            if isinstance(n, dict) and isinstance(n.get("key"), str) and _KEY.fullmatch(n["key"]):
                at = _stamp_or_none(n.get("at"))
                if at:
                    rec["notices"].append({"key": n["key"], "at": at})
        rec["notices"] = rec["notices"][-NOTICE_CAP:]
    return rec


def load(state, slug: str) -> dict:
    """The citizen's record; the defaults when it is missing or cannot be read
    (a lost record starts the clock again, once, through seed)."""
    path = record_path(state, slug)
    try:
        text = read_regular(path)
    except (LinkRefused, ValueError, OSError):
        return fresh(slug)
    if text is None:
        return fresh(slug)
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        return fresh(slug)
    return _clean(slug, raw)


def save(state, slug: str, rec: dict) -> None:
    """Write the record atomically, as one JSON line; creates the state folder."""
    path = record_path(state, slug)
    os.makedirs(str(state), exist_ok=True)
    write_regular(path, json.dumps(_clean(slug, rec), sort_keys=True, ensure_ascii=True) + "\n")


@contextlib.contextmanager
def _locked(state, slug: str) -> Iterator[None]:
    """Hold <state>/.<slug>.rw locked, so two writers never lose a change."""
    os.makedirs(str(state), exist_ok=True)
    fd = os.open(str(Path(state) / f".{slug}.rw"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def update(state, slug: str, change: Callable[[dict], None]) -> dict:
    """Load, apply change(rec) in place, and save when anything changed, all
    under the record's lock. Returns the record as saved."""
    record_path(state, slug)
    with _locked(state, slug):
        rec = load(state, slug)
        before = json.dumps(rec, sort_keys=True)
        change(rec)
        rec = _clean(slug, rec)
        if json.dumps(rec, sort_keys=True) != before:
            save(state, slug, rec)
        return rec


# -- outcomes --------------------------------------------------------------------------

def record_outcome(state, slug: str, attempt_id: str, outcome: str, at) -> dict:
    """Record what one attempt came to.

    spoken / silent: a landed beat; last_beat = at, failures 0, no back-off.
    home-failed: failures + 1, and no new pick until at + BACKOFF[min(failures, 3) - 1].
    not-due, busy, halted, summoned, host-failed: the attempt only; no clock moves.
    The same outcome for the same attempt again changes nothing. A different
    outcome for the same attempt first undoes the earlier one, so the fleet's
    host-failed takes back the back-off of the child's home-failed. Busy outcomes
    of fleet attempts in a row are counted (busy_ticks); any other fleet outcome
    ends the run. A hand-run's attempt (id beginning "hand-") leaves the count."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if not (isinstance(attempt_id, str) and _ID.fullmatch(attempt_id)):
        raise ValueError(f"{attempt_id!r} is not an attempt id")
    record_path(state, slug)
    when = _stamp_of(at)

    def change(rec: dict) -> None:
        last = rec["attempt"]
        if last and last["id"] == attempt_id:
            if last["outcome"] == outcome:
                return
            before = last["before"] or {k: rec[k] for k in _UNDO}
            rec.update(before)
        else:
            before = {k: rec[k] for k in _UNDO}
        rec["attempt"] = {"id": attempt_id, "at": when, "outcome": outcome, "before": before}
        if outcome in LANDED:
            rec.update(last_beat=when, seeded=None, failures=0, retry_after=None)
        elif outcome == "home-failed":
            rec["failures"] += 1
            wait = BACKOFF[min(rec["failures"], len(BACKOFF)) - 1]
            rec["retry_after"] = clock.stamp(clock.parse_stamp(when) + wait)
        if not attempt_id.startswith(HAND_PREFIX):
            if outcome == "busy":
                rec["busy_ticks"] += 1
            else:
                rec["busy_ticks"] = 0
                if "busy" in rec["reported"]:
                    rec["reported"].remove("busy")

    return update(state, slug, change)


def outcome_for(state, slug: str, attempt_id: str) -> str:
    """The outcome this attempt recorded, or "missing"."""
    a = load(state, slug)["attempt"]
    return a["outcome"] if a and a["id"] == attempt_id else "missing"


def last_beat_at(rec: dict) -> Optional[_dt.datetime]:
    """The record's last landed beat (or the clock start it was seeded with)."""
    return _when((rec or {}).get("last_beat"))


def retry_after(rec: dict) -> Optional[_dt.datetime]:
    return _when((rec or {}).get("retry_after"))


def backing_off(rec: dict, now: _dt.datetime) -> bool:
    """A failed beat keeps this citizen from being picked until retry_after."""
    r = retry_after(rec)
    return r is not None and now < r


def seed(state, slug: str, now: _dt.datetime) -> dict:
    """Start the clock now when the record holds no last beat (or one later than
    now). Call it only when CADENCE.md holds none either; the fleet's ranking and
    the reflect gate are the only callers that write."""
    def change(rec: dict) -> None:
        last = last_beat_at(rec)
        if last is None or last > now:
            rec["last_beat"], rec["seeded"] = clock.stamp(now), SEEDED

    return update(state, slug, change)


# -- reported once, busy, notices ----------------------------------------------------

def _check_key(key: str) -> None:
    if not (isinstance(key, str) and _KEY.fullmatch(key)):
        raise ValueError(f"{key!r} is not a report or notice key")


def mark_reported(state, slug: str, key: str) -> bool:
    """True the first time key is reported for this citizen, False after."""
    _check_key(key)
    first = []

    def change(rec: dict) -> None:
        if key not in rec["reported"]:
            rec["reported"].append(key)
            first.append(True)

    update(state, slug, change)
    return bool(first)


def clear_reported(state, slug: str, key: str) -> bool:
    """Forget a report, so the next one is made again; True when it was there."""
    _check_key(key)
    had = []

    def change(rec: dict) -> None:
        if key in rec["reported"]:
            rec["reported"].remove(key)
            had.append(True)

    update(state, slug, change)
    return bool(had)


def report_busy(state, slug: str) -> bool:
    """True once when the citizen was busy on two fleet ticks running; again
    only after a run of busy ticks has ended and a new one has begun."""
    first = []

    def change(rec: dict) -> None:
        if rec["busy_ticks"] >= 2 and "busy" not in rec["reported"]:
            rec["reported"].append("busy")
            first.append(True)

    update(state, slug, change)
    return bool(first)


def add_notice(state, slug: str, key: str, at) -> dict:
    """Keep a notice (e.g. "restored") for the citizen's next pace paragraph."""
    _check_key(key)
    when = _stamp_of(at)

    def change(rec: dict) -> None:
        rec["notices"].append({"key": key, "at": when})
        del rec["notices"][:-NOTICE_CAP]

    return update(state, slug, change)


def notice_keys(rec: dict, since=None) -> List[str]:
    """The keys of the notices recorded at or after since (all when None),
    oldest first, each once."""
    start = since if isinstance(since, _dt.datetime) or since is None else _when(since)
    out = []
    for n in sorted((rec or {}).get("notices") or [], key=lambda n: n["at"]):
        if (start is None or clock.parse_stamp(n["at"]) >= start) and n["key"] not in out:
            out.append(n["key"])
    return out


def clear_notices(state, slug: str, upto=None) -> dict:
    """Drop the notices recorded at or before upto (all when None)."""
    end = clock.parse_stamp(_stamp_of(upto)) if upto is not None else None

    def change(rec: dict) -> None:
        rec["notices"] = [n for n in rec["notices"]
                          if end is not None and clock.parse_stamp(n["at"]) > end]

    return update(state, slug, change)
