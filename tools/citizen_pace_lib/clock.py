from __future__ import annotations

import datetime as _dt
import os

UTC = _dt.timezone.utc
HOUR = _dt.timedelta(hours=1)
DAY = _dt.timedelta(days=1)
FLOOR = _dt.timedelta(hours=72)


def parse_stamp(s: str) -> _dt.datetime:
    """Parse 'YYYY-MM-DDTHH:MMZ' (seconds optional), an ISO time with offset, or a bare date.
    Raises ValueError for anything else, a time past year 1 or 9999 in UTC included."""
    t = s.strip()
    if len(t) == 10:
        return _dt.datetime.strptime(t, "%Y-%m-%d").replace(tzinfo=UTC)
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    d = _dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    try:
        return d.astimezone(UTC)
    except OverflowError:
        raise ValueError(f"{s!r} is out of range in UTC") from None


def stamp(d: _dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def day(d: _dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%d")


def now() -> _dt.datetime:
    v = os.environ.get("PACE_NOW", "").strip()
    if v:
        return parse_stamp(v)
    return _dt.datetime.now(UTC).replace(second=0, microsecond=0)
