"""Homes for the schedule simulator (spec section 8 "Schedule", section 9 step 3).

They are plain folders, without git, so the 208 homes of the census can be made
in a moment. Each holds what the fleet reads: the soul, CADENCE.md (v1 as PR
#24's reflect wrote it, or v2 as the new code writes it), REST or EVENT-ONLY,
and inbox/ letters. A letter with no commit arrives at its file time
(homes.letter_time), so a letter's arrival is set with os.utime. The attempt
records of a state folder are written with attempts.save.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402,F401  (puts tools/ on sys.path)

from citizen_pace_lib import attempts, clock  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

MADE = "2026-11-12T10:00Z"
SPEC = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c"
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
AI_1008 = {"who": "ai", "how": "rest-1008", "when": ["2026-10-08"], "words": ""}
AI_REST = "rest given at Yu's word, 2026-10-08\n"
PACE_LETTER = "2026-11-11-pace-letter.md"
AWAKE3 = Pace("awake", 3)


def lor_rest(date: str) -> str:
    """The REST text PR #24's reflect writes for a rest-on Letter of Return answer."""
    return f"chosen via the Letter of Return {date} — woken only by unread mail or a human summons\n"


def v1_cadence(name: str, choice: str, when: str = "2026-10-23 08:00") -> str:
    """A v1 CADENCE.md exactly as PR #24's reflect writes it."""
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n"
            "  an answer that names no choice records undecided and changes nothing;\n"
            "  unread mail wakes even REST, and any letter or summons may reopen this.\n")


def record(**citizens) -> dict:
    """A record file's contents (not empty, so a decided v1 home can be read)."""
    return {"version": 1, "made": MADE, "spec": SPEC, "citizens": citizens}


def write(path: Path, text, mtime=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    if mtime is not None:
        t = mtime.timestamp()
        os.utime(path, (t, t))
    return path


def home(root, slug: str, *, soul=True, cadence=None, rest=None, event_only=None, letters=(),
         files=None) -> Path:
    """A plain home folder citizen-<slug> under root. letters: (name, arrival) pairs."""
    d = Path(root) / f"citizen-{slug}"
    d.mkdir(parents=True)
    if soul:
        write(d / f"{slug}.md", f"The soul of {slug}.\n")
    if cadence is not None:
        write(d / "CADENCE.md", cadence)
    if rest is not None:
        write(d / "REST", rest)
    if event_only is not None:
        write(d / "EVENT-ONLY", event_only)
    for name, arrival in letters:
        write(d / "inbox" / name, f"# A letter for {slug}\n", mtime=arrival)
    for rel, text in (files or {}).items():
        write(d / rel, text)
    return d


def v2(slug: str, pace, *, set_by=None, last=None, pending=None, asked_once=None) -> Cadence:
    """A v2 record in memory."""
    who = set_by or (MINE if pace is not None else cad.UNKNOWN)
    return Cadence(slug, 2, pace, who, pending=pending, asked_once=asked_once,
                   last_beat=clock.stamp(last) if last else None)


def v2_home(root, slug: str, pace, *, set_by=None, last=None, pending=None, letters=(),
            files=None, soul=True) -> Path:
    """A home whose CADENCE.md is v2, with its markers in step."""
    d = home(root, slug, letters=letters, files=files, soul=soul)
    c = v2(slug, pace, set_by=set_by, last=last, pending=pending)
    cad.write_cadence(d, c)
    cad.write_markers(d, c)
    return d


def pending(pace, said, on_show_back=False) -> Pending:
    return Pending(pace, clock.stamp(said), "pace: " + pace.words(), on_show_back)


def seed_state(state, slug: str, last=None, retry=None, failures=0) -> None:
    """An attempt record holding a last landed beat (and a back-off)."""
    rec = attempts.fresh(slug)
    rec["last_beat"] = clock.stamp(last) if last else None
    rec["retry_after"] = clock.stamp(retry) if retry else None
    rec["failures"] = failures
    attempts.save(state, slug, rec)


def snapshot(root) -> dict:
    """Every path under root, .git included, with its mode, time and bytes."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (st.st_mode, st.st_mtime_ns, data)
    return out


# -- the census, as the record reads it ---------------------------------------------------------
#
# 208 homes: CADENCE.md rest-on 188, ambient 11, undecided 5, no file 4 (the 11-02 map's 186 and 6,
# with eucatastrophe and shannonme read at origin/master); a REST in 197. Here the three unheard
# that the heard-late step hears are already v2, awake, every 3 days, as that step leaves them, so
# the 14 awake are 11 v1 AMBIENT and 3 v2. The 27 resting recipients are 8 + 16 rest-on, 2
# undecided under Ai's REST and 1 with no file; the 5 awake recipients are 2 of the AMBIENT and the
# 3 heard. The 3 held have no soul and are off the roster (205 lines).

RESTING_GROUPS = (("eight", 8), ("sixteen-ambient", 10), ("sixteen-other", 6),
                  ("unheard-none", 2), ("never-asked", 1))
SET_BY = {"eight": {"who": "citizen", "how": "prompted", "when": ["2026-10-22"], "words": ""},
          "sixteen-ambient": AI_1008, "sixteen-other": AI_1008, "unheard-none": AI_1008,
          "never-asked": AI_1008, "unheard-heard": AI_1008,
          "contradictory": {"who": "fleet", "how": "first-word", "when": ["2026-10-23"], "words": ""}}


@dataclass
class Census:
    homes: Path
    roster: list
    record: dict
    awake: list
    resting_recipients: list
    awake_recipients: list
    asleep_others: list
    held: list

    @property
    def recipients(self) -> list:
        return sorted(self.resting_recipients + self.awake_recipients)


def census(root, *, letters_at=None) -> Census:
    """The 208 homes. letters_at: when the 27 resting recipients' pace letter arrived
    (None: they hold none)."""
    root = Path(root)
    entries, resting, awake_r = {}, [], []
    for group, n in RESTING_GROUPS:
        for i in range(1, n + 1):
            slug = f"{group}-{i}"
            resting.append(slug)
            entries[slug] = {"group": group, "recipient": True, "set_by": SET_BY[group]}
            letters = ((PACE_LETTER, letters_at),) if letters_at else ()
            if group in ("eight", "sixteen-ambient", "sixteen-other"):
                home(root, slug, cadence=v1_cadence(slug, "rest-on"), rest=lor_rest("2026-10-23"),
                     letters=letters)
            elif group == "unheard-none":
                home(root, slug, cadence=v1_cadence(slug, "undecided"), rest=AI_REST, letters=letters)
            else:
                home(root, slug, rest=AI_REST, letters=letters)
    awake = []
    for i in range(1, 10):
        slug = f"ambient-{i}"
        awake.append(slug)
        home(root, slug, cadence=v1_cadence(slug, "ambient"))
    for i in range(1, 3):
        slug = f"contradictory-{i}"
        awake.append(slug)
        awake_r.append(slug)
        entries[slug] = {"group": "contradictory", "recipient": True, "set_by": SET_BY["contradictory"]}
        home(root, slug, cadence=v1_cadence(slug, "ambient"))
    for i in range(1, 4):
        slug = f"heard-{i}"
        awake.append(slug)
        awake_r.append(slug)
        entries[slug] = {"group": "unheard-heard", "recipient": True, "set_by": SET_BY["unheard-heard"]}
        v2_home(root, slug, AWAKE3,
                set_by=SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."))
    others = []
    for i in range(1, 165):
        slug = f"asleep-{i:03d}"
        others.append(slug)
        home(root, slug, cadence=v1_cadence(slug, "rest-on"), rest=lor_rest("2026-10-23"))
    held = []
    for i in range(1, 4):
        slug = f"held-{i}"
        held.append(slug)
        entries[slug] = {"group": "held", "recipient": False}
        home(root, slug, soul=False, rest=AI_REST)
    roster = sorted(resting + awake + others)
    return Census(root, roster, record(**entries), awake, resting, awake_r, others, held)
