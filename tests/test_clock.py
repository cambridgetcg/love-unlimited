"""Tests for tools/clock.py — time-of-day labeling, timezone handling,
and the one-line context-injection format.

The frozen-datetime path is what matters most — every other test path
piggybacks on `now(frozen_utc=...)` to produce deterministic output.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "tools"))

import clock  # noqa: E402
from clock import (  # noqa: E402
    Clock,
    DEFAULT_TZ,
    format_human,
    format_line,
    now,
    resolve_tz,
    time_of_day,
)


# ── time_of_day boundaries ──────────────────────────────────────────────────


@pytest.mark.parametrize("hour,expected", [
    (0, "night"),    # midnight
    (4, "night"),    # pre-dawn
    (5, "morning"),  # morning starts
    (9, "morning"),
    (11, "morning"),
    (12, "afternoon"),  # noon starts afternoon
    (14, "afternoon"),
    (16, "afternoon"),
    (17, "evening"),  # evening starts
    (19, "evening"),
    (20, "evening"),
    (21, "night"),    # night starts
    (23, "night"),
])
def test_time_of_day_boundaries(hour, expected):
    assert time_of_day(hour) == expected


def test_time_of_day_full_24h_coverage():
    """Every hour in [0, 23] must map to a period."""
    for h in range(24):
        period = time_of_day(h)
        assert period in {"morning", "afternoon", "evening", "night"}


def test_time_of_day_periods_are_contiguous():
    """No gaps between morning/afternoon/evening/night."""
    seen = set()
    for h in range(24):
        seen.add(time_of_day(h))
    assert seen == {"morning", "afternoon", "evening", "night"}


# ── resolve_tz ──────────────────────────────────────────────────────────────


def test_resolve_tz_default(monkeypatch):
    monkeypatch.delenv("KINGDOM_TZ", raising=False)
    assert resolve_tz() == DEFAULT_TZ
    assert DEFAULT_TZ == "Europe/London"


def test_resolve_tz_env_override(monkeypatch):
    monkeypatch.setenv("KINGDOM_TZ", "America/New_York")
    assert resolve_tz() == "America/New_York"


# ── now() with frozen UTC ───────────────────────────────────────────────────


def test_now_london_morning_in_bst():
    """2026-04-24 08:59 UTC = 09:59 BST (summer time, UTC+1) = morning."""
    utc = datetime(2026, 4, 24, 8, 59, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    assert c.period == "morning"
    assert c.hour_local == 9
    assert c.local_tz_abbrev == "BST"
    assert c.weekday == "Friday"
    assert c.date_local == "2026-04-24"
    assert c.utc_iso == "2026-04-24T08:59:00Z"


def test_now_london_winter_in_gmt():
    """2026-01-15 08:59 UTC = 08:59 GMT (winter, UTC+0) = morning."""
    utc = datetime(2026, 1, 15, 8, 59, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    assert c.period == "morning"
    assert c.hour_local == 8
    assert c.local_tz_abbrev == "GMT"


def test_now_rolls_date_across_midnight():
    """23:30 UTC on April 23 = 00:30 BST on April 24 (morning of next day)."""
    utc = datetime(2026, 4, 23, 23, 30, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    assert c.date_local == "2026-04-24"
    assert c.hour_local == 0
    assert c.period == "night"


def test_now_evening_in_london():
    """2026-04-24 18:00 UTC = 19:00 BST = evening."""
    utc = datetime(2026, 4, 24, 18, 0, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    assert c.period == "evening"
    assert c.hour_local == 19


def test_now_rejects_naive_datetime():
    """Frozen UTC must be tz-aware; naive datetimes are wrong by construction."""
    naive = datetime(2026, 4, 24, 8, 59, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        now(tz_name="Europe/London", frozen_utc=naive)


def test_now_uses_env_tz_by_default(monkeypatch):
    monkeypatch.setenv("KINGDOM_TZ", "America/New_York")
    utc = datetime(2026, 4, 24, 8, 59, 0, tzinfo=timezone.utc)
    c = now(frozen_utc=utc)
    # 08:59 UTC in April = 04:59 EDT (DST) — still night
    assert c.local_tz == "America/New_York"
    assert c.hour_local == 4
    assert c.period == "night"


# ── formatters ──────────────────────────────────────────────────────────────


def test_format_line_shape():
    utc = datetime(2026, 4, 24, 8, 59, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    line = format_line(c)
    assert line == "2026-04-24 Fri 09:59 BST (morning, UTC 08:59)"


def test_format_line_single_line():
    """Never contains a newline — single-line guarantee for context injection."""
    utc = datetime(2026, 4, 24, 8, 59, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    assert "\n" not in format_line(c)


def test_format_human_multiline():
    utc = datetime(2026, 4, 24, 8, 59, 0, tzinfo=timezone.utc)
    c = now(tz_name="Europe/London", frozen_utc=utc)
    out = format_human(c)
    assert "Local:" in out
    assert "UTC:" in out
    assert "Period:  morning" in out
    assert out.count("\n") == 2  # three lines


# ── integration: CLI main entry ─────────────────────────────────────────────


def test_cli_default_prints_human(capsys):
    clock.main([])
    out = capsys.readouterr().out
    assert "Local:" in out
    assert "UTC:" in out


def test_cli_line_flag(capsys):
    clock.main(["--line"])
    out = capsys.readouterr().out.strip()
    # Expected shape: "YYYY-MM-DD DDD HH:MM TZ (period, UTC HH:MM)"
    import re
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \w{3} \d{2}:\d{2} \w+ \((?:morning|afternoon|evening|night), UTC \d{2}:\d{2}\)$",
        out,
    )


def test_cli_json_flag(capsys):
    import json
    clock.main(["--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "utc_iso" in parsed
    assert "local_tz" in parsed
    assert "period" in parsed


def test_cli_tz_override(capsys):
    clock.main(["--line", "--tz", "UTC"])
    out = capsys.readouterr().out.strip()
    # UTC as tz → local == UTC, abbrev "UTC"
    assert " UTC " in out


def test_cli_json_and_line_mutually_exclusive():
    """Can't request both --json and --line."""
    with pytest.raises(SystemExit):
        clock.main(["--json", "--line"])
