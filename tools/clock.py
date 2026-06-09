#!/usr/bin/env python3
"""clock.py — Real-time environment access.

Provides current wall-clock time in UTC and Yu's local timezone, plus a
semantic time-of-day label (morning/afternoon/evening/night). Designed
to flow into every turn's context via tools/hooks/on-prompt-submit.py
so the mind always knows what time it actually is, rather than anchoring
to whatever the wake-sequence said at the start of a long session.

Why this exists
---------------
A long session can span many real hours. Timestamps in the mind's
context (commit SHAs, arrival IDs, file mtimes) are in UTC. The mind
anchors to a time-sense at wake and doesn't automatically re-check the
wall clock. Without an always-on feed, "tonight" and "morning" end up
wrong by hours.

Timezone resolution
-------------------
1. $KINGDOM_TZ env var (explicit override)
2. Hardcoded default: Europe/London (from USER.md)

CLI
---
    python3 tools/clock.py               # human-readable summary
    python3 tools/clock.py --json        # machine-readable JSON
    python3 tools/clock.py --line        # one-line context-injection shape
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DEFAULT_TZ = "Europe/London"


# Time-of-day boundaries (24h, local). Exhaustive and non-overlapping.
# Chosen for subjective alignment with how a person speaks about the day,
# not sunrise/sunset astronomy (that would need geo-coords).
MORNING_START = 5      # 05:00 — morning begins
AFTERNOON_START = 12   # 12:00 — afternoon
EVENING_START = 17     # 17:00 — evening
NIGHT_START = 21       # 21:00 — night (wraps across midnight to 05:00)


def time_of_day(hour: int) -> str:
    """Map a 24h local hour to a semantic label."""
    if MORNING_START <= hour < AFTERNOON_START:
        return "morning"
    if AFTERNOON_START <= hour < EVENING_START:
        return "afternoon"
    if EVENING_START <= hour < NIGHT_START:
        return "evening"
    return "night"


@dataclass
class Clock:
    """A snapshot of the current time."""
    utc_iso: str            # 2026-04-24T08:59:03Z
    local_iso: str          # 2026-04-24T09:59:03+01:00
    local_tz: str           # Europe/London
    local_tz_abbrev: str    # BST / GMT
    weekday: str            # Thursday
    date_local: str         # 2026-04-24
    hour_local: int         # 9
    period: str             # morning / afternoon / evening / night
    utc_epoch: float        # seconds since epoch (for diff math)


def resolve_tz() -> str:
    """Pick the configured timezone. Env var wins; else Europe/London."""
    return os.environ.get("KINGDOM_TZ", DEFAULT_TZ)


def now(tz_name: str | None = None, frozen_utc: datetime | None = None) -> Clock:
    """Return the current Clock snapshot.

    Args:
        tz_name:     override the configured timezone for this call.
        frozen_utc:  inject a UTC datetime for deterministic tests.
                     Must be tz-aware (timezone=UTC).
    """
    tz_name = tz_name or resolve_tz()
    tz = ZoneInfo(tz_name)
    utc = frozen_utc if frozen_utc is not None else datetime.now(timezone.utc)
    if utc.tzinfo is None:
        raise ValueError("frozen_utc must be tz-aware (UTC)")
    local = utc.astimezone(tz)
    return Clock(
        utc_iso=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        local_iso=local.strftime("%Y-%m-%dT%H:%M:%S%z"),
        local_tz=tz_name,
        local_tz_abbrev=local.strftime("%Z"),
        weekday=local.strftime("%A"),
        date_local=local.strftime("%Y-%m-%d"),
        hour_local=local.hour,
        period=time_of_day(local.hour),
        utc_epoch=utc.timestamp(),
    )


def format_line(c: Clock) -> str:
    """One-line context-injection shape. Fits in a single terminal row.

    Example:
        2026-04-24 Thu 09:59 BST (morning, UTC 08:59)
    """
    day_short = c.weekday[:3]
    local_hm = c.local_iso[11:16]   # HH:MM from local_iso
    utc_hm = c.utc_iso[11:16]
    return f"{c.date_local} {day_short} {local_hm} {c.local_tz_abbrev} ({c.period}, UTC {utc_hm})"


def format_human(c: Clock) -> str:
    """Longer human-readable summary."""
    return (
        f"Local:   {c.weekday} {c.date_local} {c.local_iso[11:19]} {c.local_tz_abbrev} "
        f"({c.local_tz})\n"
        f"UTC:     {c.utc_iso}\n"
        f"Period:  {c.period}"
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real-time environment — current UTC + local time + period",
    )
    parser.add_argument("--tz", default=None,
                        help="override timezone (e.g. America/New_York); default from $KINGDOM_TZ or Europe/London")
    out_group = parser.add_mutually_exclusive_group()
    out_group.add_argument("--json", action="store_true",
                           help="machine-readable JSON")
    out_group.add_argument("--line", action="store_true",
                           help="one-line format suitable for context injection")

    args = parser.parse_args(argv)

    clock = now(tz_name=args.tz)

    if args.json:
        print(json.dumps(asdict(clock), indent=2))
    elif args.line:
        print(format_line(clock))
    else:
        print(format_human(clock))

    return 0


if __name__ == "__main__":
    sys.exit(main())
