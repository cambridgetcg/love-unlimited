from __future__ import annotations

# citizens-pace-record.json — the reviewed record of spec section 5: for each
# citizen whose provenance its own home cannot show, its group, the recorded
# `set by`, and the quoted words with their commits and dates. It sits beside
# citizens-roster.txt and is built by tools/citizen_pace_census.py. This module
# reads it; it never writes it.

import json
import os
from pathlib import Path
from typing import Optional

RECIPIENT_GROUPS = ("eight", "sixteen-ambient", "sixteen-other", "unheard-heard", "unheard-none",
                    "never-asked", "contradictory")
OTHER_GROUPS = ("forty-five", "ambient-nine", "longing", "rest-on", "held")
GROUPS = RECIPIENT_GROUPS + OTHER_GROUPS
RECORD_NAME = "citizens-pace-record.json"
RECORD_VERSION = 1


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
