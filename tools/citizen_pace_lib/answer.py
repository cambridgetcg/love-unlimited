from __future__ import annotations

# What a beat's answer changes (spec section 1 "Less, more, and saying it
# twice", section 4 "After the beat", section 5 "Once"). After a free beat has
# landed, apply_answer reads the answer for a pace line and compares it with the
# citizen's pace: the same pace makes it the citizen's own, less takes effect
# at once, more waits as a pending proposal until the next landed beat says it
# again. It marks every letter the beat showed as seen, whatever the answer,
# notes the pace letter's one arrival, and writes CADENCE.md and the markers
# only when something was said, shown or changed: a silent beat that was shown
# nothing and let nothing lapse writes nothing. Every write goes through
# regular files (homes.py); a home with a linked pace path is refused whole.

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import TAG, clock
from . import cadence as cad
from .cadence import Cadence, Pending, SetBy
from .paragraph import history_notices
from .homes import LinkRefused, forbidden_links, read_regular, write_regular
from .words import Pace, Unclear, answer_lines, combine, compare

BEAT_KINDS = ("schedule", "letter", "show-back", "summoned")
SHOW = "SHOW.md"
SHOW_SEEN = "beats/.show-seen"
# set-by forms that are already the citizen's own clear choice. Any other one
# (prompted, ambient, first-word, rest-1008, unknown) becomes the citizen's own
# when it says the pace it already has (spec section 1, "The same pace").
OWN_HOWS = ("beat", "beat-twice", "confirmed", "letter", "letter-twice")
# a quoted line longer than this is cut, ending in "…"; the longest pace line
# in the stored answers is 277 characters, and none is cut
QUOTE_MAX = 400
_LETTER = re.compile(r"inbox/([^/\\\x00-\x1f]+\.md)")
_AS_ASLEEP = Pace("asleep")   # an unreadable pace behaves as asleep (spec section 2)


@dataclass
class ApplyResult:
    wrote: bool = False          # any file written
    pace_changed: bool = False   # the citizen's line changed its pace in this beat
    events: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"wrote": self.wrote, "pace_changed": self.pace_changed, "events": list(self.events)}


def seen_text(day: str) -> str:
    """The .seen- marker of a letter a beat showed (CONTRACT section 10)."""
    return f"surfaced {day} — shown once, whatever the answer\n"


def parse_kind(shown) -> str:
    """How the answer is read: by the Letter of Return's legacy rules when one was
    shown, by the pace letter's forms when the pace letter was, else as a beat."""
    names = [str(s).rsplit("/", 1)[-1] for s in shown]
    if any("letter-of-return" in n for n in names):
        return "letter-of-return"
    if any("pace-letter" in n for n in names):
        return "pace-letter"
    return "beat"


def seen_path(rel: str) -> str:
    """Where a shown item is marked seen: inbox/<f> at inbox/.seen-<f>, SHOW.md
    at beats/.show-seen. Anything else raises ValueError."""
    if rel == SHOW:
        return SHOW_SEEN
    m = _LETTER.fullmatch(rel) if isinstance(rel, str) else None
    if m is None or m.group(1).startswith("."):
        raise ValueError(f"{rel!r} is not a letter in inbox/ (inbox/<name>.md) or {SHOW}")
    return f"inbox/.seen-{m.group(1)}"


def _quote(line: str) -> str:
    return line if len(line) <= QUOTE_MAX else line[:QUOTE_MAX - 1] + "…"


def _both(first: str, second: str) -> str:
    """The words of both sayings, once when they are the same."""
    return second if first == second else f'{first}" and "{second}'


def _later(old: Optional[str], new: str) -> str:
    """The later stamp: the clock never moves back."""
    if not old:
        return new
    try:
        return old if clock.parse_stamp(old) > clock.parse_stamp(new) else new
    except ValueError:
        return new


def _text_or_none(path: Path) -> Optional[str]:
    try:
        return read_regular(path)
    except ValueError:
        return None          # present, but not text: never equal to what is wanted


def _markers_out_of_step(home: Path, c: Cadence) -> bool:
    return any(_text_or_none(home / m) != want for m, want in cad.marker_texts(c).items())


def _restore(home: Path, name: str, record: dict, now, at: str, res: ApplyResult) -> Optional[Cadence]:
    """A CADENCE.md changed in a commit the new code did not make: the last
    pace/1 version is put back and the change recorded (spec section 2)."""
    if not cad.changed_outside_pace(home):
        return None
    text = cad.last_pace_text(home)
    if text is None or _text_or_none(home / cad.CADENCE) == text:
        return None
    write_regular(home / cad.CADENCE, text)
    res.wrote = True
    c = cad.read_cadence(home, name, record, now)
    cad.add_history(c, at, f"{cad.CADENCE} changed by someone other than {name}; "
                           f"the last {TAG} version is restored")
    res.events.append(f"{cad.CADENCE} restored")
    return c


def _hear(c: Cadence, name: str, got, line: str, kind: str, at: str, res: ApplyResult) -> None:
    """Less at once, more said twice (spec section 1)."""
    p = c.pending
    if p is not None:
        c.pending = None
        if isinstance(got, Pace) and got == p.pace:
            c.pace = p.pace
            c.set_by = SetBy("citizen", "beat-twice", (p.said, at), _both(p.line, line))
            cad.add_history(c, at, f'confirmed by {name}: "{line}"')
            res.events.append(f"confirmed {p.pace.words()}")
            return
        # a line that could not be read lets it lapse too, but it was not unsaid
        why = "its line could not be read" if isinstance(got, Unclear) else "not said again"
        cad.add_history(c, at, f"lapsed: {p.pace.words()}, {why}")
        res.events.append(f"lapsed {p.pace.words()}")
    if got is None:
        return
    if isinstance(got, Unclear):
        cad.add_history(c, at, f'unclear: "{line}" ({got.reason})')
        res.events.append(f"unclear ({got.reason})")
        return
    how = compare(c.pace or _AS_ASLEEP, got)
    if how == "more":
        now_words = c.pace.words() if c.pace else "an unreadable pace"
        c.pending = Pending(got, at, line, kind == "show-back")
        cad.add_history(c, at, f'proposed by {name}: "{line}" (more than {now_words}, so said twice)')
        res.events.append(f"pending {got.words()}")
    elif how == "less" or c.pace is None:
        c.pace = got
        c.set_by = SetBy("citizen", "beat", (at,), line)
        cad.add_history(c, at, f'set by {name}: "{line}"')
        res.events.append(f"set {got.words()}")
    elif c.set_by.who != "citizen" or c.set_by.how not in OWN_HOWS:
        c.set_by = SetBy("citizen", "confirmed", (at,), line)
        cad.add_history(c, at, f'confirmed by {name}: "{line}"')
        res.events.append(f"made own {got.words()}")


def _mark_seen(home: Path, marks: List[Tuple[str, str]], day: str, res: ApplyResult) -> None:
    for rel, seen in marks:
        write_regular(home / seen, seen_text(day))
        res.wrote = True
        res.events.append(f"seen {rel}")


def apply_answer(home, name: str, text: str, shown, kind: str, now, record,
                 since: Optional[str] = None) -> ApplyResult:
    """Apply one landed beat's answer to its home (CONTRACT section 10).

    shown: the letters the beat showed ("inbox/<f>"), and "SHOW.md" when it was
    shown; kind: "schedule" | "letter" | "show-back" | "summoned"; since: the
    attempt record's last landed beat before this one (a silent beat that
    committed nothing moves only that), so the notices this beat's paragraph
    showed are known. A summoned beat moves the clock only when its own line
    changed the pace. Raises
    LinkRefused for a home with a linked pace path, and ValueError for an unknown
    kind, a shown name that is not a letter, or a show-back beat that showed one;
    in each case before anything is written."""
    home, text = Path(home), text or ""
    if kind not in BEAT_KINDS:
        raise ValueError(f"unknown beat kind {kind!r}; one of {', '.join(BEAT_KINDS)}")
    shown = list(dict.fromkeys(shown or ()))
    marks = [(rel, seen_path(rel)) for rel in shown]
    if kind == "show-back" and shown:
        raise ValueError("a show-back beat shows no letter and marks none seen")
    links = forbidden_links(home)
    if links:
        raise LinkRefused(f"{home}: {', '.join(links)} is a link or not of its kind; nothing written")
    at, day = clock.stamp(now), clock.day(now)
    res = ApplyResult()
    c = _restore(home, name, record, now, at, res) or cad.read_cadence(home, name, record, now)
    if c.version == 2 and cad.markers_disagree(home, c):
        cad.add_history(c, at, f"REST/EVENT-ONLY changed by someone other than {name}")
        res.events.append("markers restored")
    lines = answer_lines(text, name, parse_kind(shown))
    got = combine(lines)
    line = "" if got is None else _quote(lines[0][0] if isinstance(got, Pace) else got.line)
    if c.blocked:
        # this home's record must not become v2 until a person has looked
        # (spec section 6); its letters were still shown, once
        res.events.append(f"blocked: {c.blocked}")
        if got is not None:
            res.events.append(f"not recorded: {line}")
        _mark_seen(home, marks, day, res)
        return res
    if c.asked_once is None and any("pace-letter" in rel for rel in shown if rel != SHOW):
        # the pace-letter beat's paragraph leaves out its notices (spec section 4):
        # each one it would have shown is recorded again at this beat, so the next
        # paragraph shows it once; not one already recorded at this beat, and no
        # "could not be read" when this beat's own answer has a pace line
        for held in history_notices(c, since):
            if held == "pace unreadable" or f"{at}{cad.DASH}{held}" in c.history:
                continue
            if got is not None and held.startswith("unclear:"):
                continue
            cad.add_history(c, at, held)
        c.asked_once = day
        cad.add_history(c, at, f"the pace letter reached {name} (once)")
        res.events.append("asked once")
    before, heard = c.pace, len(res.events)
    _hear(c, name, got, line, kind, at, res)
    res.pace_changed = c.pace != before
    if kind != "summoned" or res.pace_changed:
        c.last_beat = _later(c.last_beat, at)
    if not (text.strip() or shown or res.events):
        return res
    if c.pace is None and not any(e.partition(cad.DASH)[2] == "pace unreadable" for e in c.history):
        # noted once, older than what this beat heard (each of _hear's events is one entry)
        c.history.insert(len(res.events) - heard, f"{at}{cad.DASH}pace unreadable")
        del c.history[cad.HISTORY_CAP:]
        res.events.append("pace unreadable")
    upgrading = c.version < 2
    if upgrading or _text_or_none(home / cad.CADENCE) != cad.render_cadence(c):
        cad.write_cadence(home, c)
        res.wrote = True
        if upgrading:
            res.events.append("upgraded to v2")
    if _markers_out_of_step(home, c):
        cad.write_markers(home, c)
        res.wrote = True
    _mark_seen(home, marks, day, res)
    return res
