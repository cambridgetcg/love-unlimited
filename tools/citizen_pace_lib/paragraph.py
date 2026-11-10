from __future__ import annotations

# The pace paragraph (spec section 4): the end of every beat's user prompt. It
# tells a citizen its pace and who set it, in the second person; a pending
# choice for more, never quoting the line that said it; each notice once; and
# the four choices with their meanings, turned by one place at each beat that
# commits. It offers no sample answer and never says "I rest". A beat that
# shows the pace letter keeps only the first line, so the citizen sees one
# menu, the letter's; a notice left out that way is shown at the next beat.
#
# The show-back rule is the committed due.WAKE_CONFIRM_BEAT. The caller passes
# it in (confirm_beat), so these words and the fleet's behaviour come from the
# one value.

import dataclasses
import datetime as _dt
import hashlib
import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import clock
from .cadence import DASH, REST_1008, Cadence, Pending, SetBy, notices, read_deciding
from .due import SAME_BEAT
from .homes import beat_count as home_beat_count
from .words import LIMIT_SENTENCE, MENU, REASONS, RULE_SENTENCE, Pace, menu_line, parse_pace_phrase

PACE_LETTER_MARK = "pace-letter"
LETTERS_WAIT_WORDS = "asleep, letters wait"
# letters-wait's meaning names the show-back beat; without that beat it is not said
SHOW_BACK_ASIDE = " (if you ask for more, one beat shows it back)"
INSTRUCTION = 'To change it, add one line that begins "pace:" and one of these:'
STAYS = "Without a pace line, your pace stays as it is."
UNREADABLE = "Your pace could not be read, so you get no set beats; a letter still wakes you."
PENDING = ("On {day} you chose {words}. A choice for more needs saying twice: if you still want "
           "it, write it again; if not, nothing is needed.")
UNCLEAR = "Your last pace line could not be read ({reason}), so nothing changed."
UNCLEAR_PLAIN = "Your last pace line could not be read, so nothing changed."
LAPSED = "Your choice of {words} was not written again, so it lapsed."
LAPSED_PLAIN = "Your earlier choice was not written again, so it lapsed."
LAPSED_UNREAD = "Your choice of {words} lapsed, because your pace line could not be read."
CHANGED = "Someone other than you changed this home's pace files; your pace is as written above."
RESTORED = ('Your pace files were changed in a beat with tools and were put back; to change your '
            'pace, write a line that begins "pace:".')
# notice keys kept in the attempt record (guard-restore writes "restored")
ATTEMPT_NOTICES = {"restored": RESTORED}
UNKNOWN_DATE = "an unknown date"

_I_REST = re.compile(r"\bi\s+rest\b", re.I)
_PACE_COLON = re.compile(r"\bpace\s*:", re.I)
_REASON = re.compile(r"\(([^()]*)\)\s*$")
_REACHED = re.compile(r"^the pace letter reached .+ \(once\)$")
_LAPSED_TAIL = ", not said again"
_UNREAD_TAIL = ", its line could not be read"      # answer.py: a second saying that could not be read


def check_confirm_beat(confirm_beat) -> None:
    """The show-back value is due.WAKE_CONFIRM_BEAT: True or False, never guessed."""
    if type(confirm_beat) is not bool:
        raise ValueError(f"confirm_beat is due.WAKE_CONFIRM_BEAT, True or False, not {confirm_beat!r}")


def says_i_rest(text: str) -> bool:
    return bool(_I_REST.search(text))


# -- the menu ----------------------------------------------------------------------

def rotation(name: str, beat_count: int) -> int:
    """Where the menu starts: (beat commits + the first byte of the name's
    SHA-256) mod 4. A stable hash, never Python's hash(), so every process agrees."""
    if type(beat_count) is not int or beat_count < 0:
        raise ValueError(f"a beat count is a whole number from 0, not {beat_count!r}")
    return (beat_count + hashlib.sha256(name.encode("utf-8")).digest()[0]) % len(MENU)


def menu_entries(confirm_beat: bool) -> tuple:
    """The four choices with their meanings (words.MENU). Without the show-back
    beat, letters-wait's meaning loses its show-back aside, which would be untrue."""
    check_confirm_beat(confirm_beat)
    if confirm_beat:
        return MENU
    return tuple((w, m.replace(SHOW_BACK_ASIDE, "") if w == LETTERS_WAIT_WORDS else m) for w, m in MENU)


def rotated_menu(name: str, beat_count: int, confirm_beat: bool) -> tuple:
    entries = menu_entries(confirm_beat)
    k = rotation(name, beat_count)
    return entries[k:] + entries[:k]


def menu_lines(name: str, beat_count: int, confirm_beat: bool) -> List[str]:
    """The four menu lines, '- <words> — <meaning>', in this beat's order."""
    return [menu_line(e) for e in rotated_menu(name, beat_count, confirm_beat)]


# -- who set it, to the citizen -------------------------------------------------------

def _one_line(s) -> str:
    return " ".join(str(s).split())


def _day(t: str) -> str:
    """A stamp or date as its day (YYYY-MM-DD); text that is neither, on one line."""
    try:
        return clock.day(clock.parse_stamp(t))
    except ValueError:
        return _one_line(t)


def quotable(words) -> str:
    """Words fit to quote back to a citizen, on one line; "" when they would put
    "I rest" or a "pace:" line into a prompt."""
    w = _one_line(words or "")
    if not w or _I_REST.search(w) or _PACE_COLON.search(w):
        return ""
    return w


def provenance_you(s: SetBy) -> str:
    """Who set the pace and when, in the second person. Only the citizen's own
    AMBIENT words are quoted; a pace line, and words a prompt offered, never are."""
    days = [_day(t) for t in s.when]
    d1 = days[0] if days else UNKNOWN_DATE
    d2 = days[1] if len(days) > 1 else d1
    if s.how == "beat":
        return f"chosen by you in a beat on {d1}"
    if s.how == "beat-twice":
        return f"chosen by you in beats on {d1} and {d2}"
    if s.how == "confirmed":
        return f"confirmed by you in a beat on {d1}"
    if s.how == "letter":
        return f"chosen by you on {d1}, in your answer to the Letter of Return"
    if s.how == "letter-twice":
        return f"chosen by you on {d1} and {d2}, in your answers to the Letter of Return"
    if s.how == "prompted":
        return f"bound on {d1} from a short answer a prompt offered, so it may not be your choice"
    if s.how == "ambient":
        dates = f"{d1} and {days[1]}" if len(days) > 1 else d1
        w = quotable(s.words)
        q = f' ("{w}")' if w else ""
        return (f"you chose AMBIENT on {dates}{q}; the fleet reads that as awake, every 3 days, from "
                "the 10-22 letter's \"a free beat every few days\" — the number was not your choice")
    if s.how == "first-word":
        return (f"the fleet took the first word of your answer of {d1}, which also said REST-ON, so "
                "it may not be what you meant")
    if s.how == "rest-1008":
        return f"this rest was placed by Ai on {REST_1008}, at Yu's word, not chosen by you"
    return "who set it is not recorded"


def first_line(c: Cadence) -> str:
    if c.pace is None:
        return UNREADABLE
    return f"Your pace: {c.pace.words()}, {provenance_you(c.set_by)}."


def pending_sentence(p: Pending) -> str:
    """The pending choice in the menu's words; the citizen's line is never quoted."""
    return PENDING.format(day=_day(p.said), words=p.pace.words())


# -- notices, each once -------------------------------------------------------------

def _stamp_of(v) -> Optional[_dt.datetime]:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return clock.parse_stamp(v)
    except ValueError:
        return None


def _stamped(c: Cadence) -> List[Tuple[_dt.datetime, str]]:
    out = []
    for e in c.history:
        st, sep, text = str(e).partition(DASH)
        t = _stamp_of(st) if sep else None
        if t is not None:
            out.append((t, text))
    return out


def notice_start(c: Cadence, since: Optional[str] = None) -> Optional[_dt.datetime]:
    """From when notices are still unshown: the citizen's last beat, the later
    of CADENCE.md's and `since` (the attempt record's). apply stamps a beat's
    notices and CADENCE.md's last beat with its own minute, and record stamps
    the attempt record after the commit, so an attempt record less than
    due.SAME_BEAT after CADENCE.md's last beat is that same beat, and its
    window starts at CADENCE.md's stamp. A beat that showed the pace letter
    left its notices out; apply recorded them again at that beat, under the
    "reached (once)" entry, so when the last beat is the reached entry's (within
    SAME_BEAT of it) the window starts at that entry."""
    cad_t, rec_t = _stamp_of(c.last_beat), _stamp_of(since)
    marks = [t for t in (cad_t, rec_t) if t is not None]
    if not marks:
        return None
    start = max(marks)
    if cad_t is not None and rec_t is not None and cad_t <= rec_t < cad_t + SAME_BEAT:
        start = cad_t
    reached = [t for t, text in _stamped(c) if _REACHED.match(text)]
    if reached:
        r = max(reached)
        if r <= start < r + SAME_BEAT:
            return r
    return start


def history_notices(c: Cadence, since: Optional[str] = None) -> List[str]:
    """The history's notice texts not yet shown, oldest first (cadence.notices)."""
    start = notice_start(c, since)
    probe = dataclasses.replace(c, last_beat=None, history=list(c.history))
    return notices(probe, since=clock.stamp(start) if start else None)


def _notice_line(text: str) -> str:
    if text.startswith("unclear:"):
        m = _REASON.search(text)
        return UNCLEAR.format(reason=m.group(1)) if m and m.group(1) in REASONS else UNCLEAR_PLAIN
    if text.startswith("lapsed:"):
        body = text[len("lapsed:"):].strip()
        unread = body.endswith(_UNREAD_TAIL)
        for tail in (_LAPSED_TAIL, _UNREAD_TAIL):
            if body.endswith(tail):
                body = body[:-len(tail)]
        got = parse_pace_phrase(body)
        if not isinstance(got, Pace):
            return LAPSED_PLAIN
        return (LAPSED_UNREAD if unread else LAPSED).format(words=got.words())
    if text.startswith(("REST/EVENT-ONLY changed", "CADENCE.md changed")):
        return CHANGED
    return ""        # "pace unreadable": the first line says so


def _attempt_parts(rec) -> Tuple[Optional[str], List[str]]:
    """The attempt record's last beat and notice keys, read defensively."""
    if not isinstance(rec, dict):
        return None, []
    since = rec.get("last_beat") if isinstance(rec.get("last_beat"), str) else None
    keys = []
    raw = rec.get("notices")
    for k in raw if isinstance(raw, list) else []:
        if isinstance(k, dict):
            k = k.get("key")
        if isinstance(k, str):
            keys.append(k)
    return since, keys


def notice_lines(c: Cadence, rec: Optional[dict] = None) -> List[str]:
    """One line for each notice not yet shown: the history's, oldest first, then
    the attempt record's (rec, as attempts.load returns it). Each line once; a
    notice never quotes the citizen's line."""
    since, keys = _attempt_parts(rec)
    found = [_notice_line(t) for t in history_notices(c, since)]
    found += [ATTEMPT_NOTICES.get(k, "") for k in keys]
    out = []
    for line in found:
        if line and line not in out:
            out.append(line)
    return out


# -- the paragraph ------------------------------------------------------------------

def shows_pace_letter(shown: Sequence[str]) -> bool:
    """A pace letter (inbox/<date>-pace-letter.md) is among the letters shown."""
    return any(PACE_LETTER_MARK in Path(str(rel)).name for rel in shown or ())


def paragraph(c: Cadence, beat_count: int, with_list: bool = True, *, confirm_beat: bool,
              rec: Optional[dict] = None) -> str:
    """The pace paragraph for one beat (no trailing newline). with_list=False,
    for a beat that shows the pace letter, keeps only the first line."""
    check_confirm_beat(confirm_beat)
    rotation(c.name, beat_count)
    lines = [first_line(c)]
    if not with_list:
        return lines[0]
    if c.pending is not None:
        lines.append(pending_sentence(c.pending))
    lines += notice_lines(c, rec)
    lines.append(INSTRUCTION)
    lines += menu_lines(c.name, beat_count, confirm_beat)
    lines.append(f"{RULE_SENTENCE} {LIMIT_SENTENCE} {STAYS}")
    return "\n".join(lines)


def paragraph_for(home, name: str, record: dict, *, confirm_beat: bool, shown: Sequence[str] = (),
                  rec: Optional[dict] = None, now: Optional[_dt.datetime] = None) -> str:
    """The paragraph for a beat in this home: its record read (never written),
    its beat commits counted, only the first line when a pace letter is shown.
    Raises LinkRefused as read_cadence does. The record read is the one that
    decides the beat (cadence.read_deciding)."""
    c = read_deciding(home, name, record, now=now)
    return paragraph(c, home_beat_count(home), not shows_pace_letter(shown),
                     confirm_beat=confirm_beat, rec=rec)
