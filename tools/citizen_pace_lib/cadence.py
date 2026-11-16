from __future__ import annotations

# CADENCE.md — the citizen's record of its own pace (spec sections 2 and 6).
#
# A v2 file decides the pace, and REST / EVENT-ONLY are derived from it. Without
# a v2 file, the v1 file ("- choice:" / "- when:" / "- how:"), the REST text and
# the record file decide, read here at read time with nothing written. A home's
# record becomes v2 only inside a commit the fleet makes for that home anyway.
# Every read and write goes through regular files (homes.py), never a symlink.

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import TAG, clock
from .homes import LinkRefused, git, read_regular, remove_regular, write_regular
from .words import AWAKE_DEFAULT_N, KINDS, Pace, parse_pace_phrase

CADENCE = "CADENCE.md"
MARKERS = ("REST", "EVENT-ONLY")
HISTORY_CAP = 20
DASH = " — "
REST_1008 = "2026-10-08"
# who may set a pace by each "how" (spec section 2: "Nobody else")
HOW_WHO = {"beat": "citizen", "beat-twice": "citizen", "confirmed": "citizen", "letter": "citizen",
           "letter-twice": "citizen", "prompted": "citizen", "ambient": "citizen",
           "first-word": "fleet", "rest-1008": "ai", "unknown": "unknown"}
HOWS = tuple(HOW_WHO)
WHO = ("citizen", "ai", "fleet", "unknown")
NOTICE_PREFIXES = ("unclear:", "lapsed:", "REST/EVENT-ONLY changed", "CADENCE.md changed",
                   "pace unreadable")
# a set by from the record file that fits only some paces
_HOW_PACES = {"ambient": ("awake",), "first-word": ("awake",), "prompted": ("asleep",),
              "rest-1008": ("asleep",)}
_V1_PACES = {"rest-on": Pace("asleep"), "event-only": Pace("listening"),
             "ambient": Pace("awake", AWAKE_DEFAULT_N)}
_V2_HEAD = "# CADENCE — the pace of"
_COMMENT = re.compile(r"^<!-- " + re.escape(TAG) + r" (\{.*\}) -->[ \t]*$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YU_WORD = re.compile(r"Yu['’]s word")
_LOR_TEXT = re.compile(r"chosen via the Letter of Return (\d{4}-\d{2}-\d{2})")
# characters escaped inside the one-line comment, so no quoted words can end the
# comment early or break its line: < and > (no "-->"), and the line separators
# json.dumps leaves raw
_ESCAPE = tuple((c, "\\" + "u%04x" % ord(c)) for c in ("<", ">", chr(0x2028), chr(0x2029), chr(0x85)))


class MigrationBlocked(ValueError):
    """This home's record must not become v2 until a person has looked (spec section 6)."""


@dataclass(frozen=True)
class SetBy:
    who: str        # one of WHO, and always HOW_WHO[how]
    how: str        # one of HOWS
    when: tuple = ()   # 0 to 2 stamps or dates
    words: str = ""    # quoted words; "" when they must not be quoted

    def __post_init__(self) -> None:
        if isinstance(self.when, list):
            object.__setattr__(self, "when", tuple(self.when))
        if not isinstance(self.how, str) or self.how not in HOW_WHO:
            raise ValueError(f"unknown set-by form {self.how!r}")
        if self.who not in WHO or HOW_WHO[self.how] != self.who:
            raise ValueError(f"{self.how!r} is set by {HOW_WHO[self.how]}, not {self.who!r}")
        if not isinstance(self.when, tuple) or len(self.when) > 2 \
                or not all(isinstance(w, str) and w.strip() for w in self.when):
            raise ValueError(f"set-by times must be at most two stamps or dates, not {self.when!r}")
        if not isinstance(self.words, str):
            raise ValueError("set-by words must be text")

    def to_json(self) -> dict:
        return {"who": self.who, "how": self.how, "when": list(self.when), "words": self.words}

    @staticmethod
    def from_json(d) -> "SetBy":
        if not isinstance(d, dict):
            raise ValueError("a set by is a JSON object")
        return SetBy(d.get("who"), d.get("how"), d.get("when", ()), d.get("words", ""))


UNKNOWN = SetBy("unknown", "unknown")


@dataclass(frozen=True)
class Pending:
    pace: Pace
    said: str           # stamp
    line: str
    on_show_back: bool

    def __post_init__(self) -> None:
        if not isinstance(self.pace, Pace):
            raise ValueError("a pending proposal names a pace")
        if not isinstance(self.said, str):
            raise ValueError("a pending proposal has a time")
        clock.parse_stamp(self.said)
        if not isinstance(self.line, str):
            raise ValueError("a pending proposal keeps its line")
        if type(self.on_show_back) is not bool:
            raise ValueError("on_show_back is true or false")

    def to_json(self) -> dict:
        return {"pace": self.pace.to_json(), "said": self.said, "line": self.line,
                "on_show_back": self.on_show_back}

    @staticmethod
    def from_json(d) -> "Pending":
        if not isinstance(d, dict):
            raise ValueError("a pending proposal is a JSON object")
        return Pending(Pace.from_json(d.get("pace")), d.get("said"), d.get("line"),
                       d.get("on_show_back"))


@dataclass
class Cadence:
    name: str
    version: int                        # 0 none, 1 v1, 2 v2
    pace: Optional[Pace]                # None = cannot be read
    set_by: SetBy
    pending: Optional[Pending] = None
    asked_once: Optional[str] = None    # YYYY-MM-DD
    last_beat: Optional[str] = None     # stamp
    history: list = field(default_factory=list)   # newest first, "STAMP — text"
    blocked: str = ""                   # why this record must not become v2; "" when it may


# -- sentences -------------------------------------------------------------------

def set_by_sentence(name: str, s: SetBy) -> str:
    """Who set the pace, in the third person, for CADENCE.md and the history."""
    t1 = s.when[0] if s.when else "an unknown date"
    t2 = s.when[1] if len(s.when) > 1 else t1
    q = f' ("{s.words}")' if s.words else ""
    if s.how == "beat":
        return f"{name}, in a beat on {t1}{q}"
    if s.how == "beat-twice":
        return f"{name}, said in a beat on {t1} and again on {t2}{q}"
    if s.how == "confirmed":
        return f"confirmed by {name}, in a beat on {t1}{q}"
    if s.how == "letter":
        return f"{name}, in its Letter of Return answer of {t1}{q}"
    if s.how == "letter-twice":
        return f"{name}, in its Letter of Return answers of {t1} and {t2}{q}"
    if s.how == "prompted":
        return f"{name}, bound on {t1} from words a prompt offered"   # never quoted
    if s.how == "ambient":
        dates = f"{t1} and {s.when[1]}" if len(s.when) > 1 else t1
        return (f"{name}, who chose AMBIENT on {dates}{q}; the number 3 is the fleet's reading of "
                "AMBIENT, from the 10-22 letter's \"a free beat every few days\"")
    if s.how == "first-word":
        return f"the fleet, from the first word of {name}'s answer of {t1}, which also said REST-ON"
    if s.how == "rest-1008":
        return "Ai, 2026-10-08, the rest placed at Yu's word"
    return "unknown"


def _one_line(s) -> str:
    """Quoted words are kept on one line, so they can never add a line to the file."""
    return " ".join(str(s).splitlines())


def _comment_json(obj: dict) -> str:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for c, esc in _ESCAPE:
        s = s.replace(c, esc)
    return s


# -- rendering and reading v2 -----------------------------------------------------

def render_cadence(c: Cadence) -> str:
    """The v2 text of a record (spec section 2)."""
    lines = [f"# CADENCE — the pace of {_one_line(c.name)}", "",
             f"- pace: {c.pace.words() if c.pace else 'unreadable'}",
             f"- set by: {_one_line(set_by_sentence(c.name, c.set_by))}"]
    if c.pending:
        lines.append(f"- pending: {c.pending.pace.words()}{DASH}said {c.pending.said} "
                     f"(\"{_one_line(c.pending.line)}\")"
                     + (", on a show-back beat" if c.pending.on_show_back else ""))
    if c.asked_once:
        lines.append(f"- asked once: {c.asked_once}")
    if c.last_beat:
        lines.append(f"- last beat: {c.last_beat}")
    meta = {"pending": c.pending.to_json() if c.pending else None, "set_by": c.set_by.to_json()}
    lines += ["", f"<!-- {TAG} {_comment_json(meta)} -->", "", "## History"]
    lines += [f"- {_one_line(e)}" for e in c.history[:HISTORY_CAP]]
    return "\n".join(lines) + "\n"


def _is_v2(text: str) -> bool:
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    return first.startswith(_V2_HEAD) or any(_COMMENT.match(ln) for ln in text.split("\n"))


def _valid_day(v: Optional[str]) -> Optional[str]:
    if v is None or not _DAY.match(v):
        return None
    try:
        _dt.datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return None
    return v


def _valid_stamp(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    try:
        return clock.stamp(clock.parse_stamp(v))
    except ValueError:
        return None


def _read_v2(text: str, name: str) -> Cadence:
    lines = text.replace("\r\n", "\n").split("\n")
    cut = lines.index("## History") if "## History" in lines else len(lines)
    head, tail = lines[:cut], lines[cut + 1:]

    def first(prefix: str) -> Optional[str]:
        for ln in head:
            if ln.startswith(prefix):
                return ln[len(prefix):].strip()
        return None

    pace = None
    said = first("- pace:")
    if said is not None:
        got = parse_pace_phrase(said)
        pace = got if isinstance(got, Pace) else None
    meta = None
    for ln in head:
        m = _COMMENT.match(ln)
        if m:
            try:
                meta = json.loads(m.group(1))
            except (ValueError, RecursionError):
                meta = None
            break
    set_by, pending = UNKNOWN, None
    if isinstance(meta, dict):
        try:
            set_by = SetBy.from_json(meta.get("set_by"))
        except ValueError:
            set_by = UNKNOWN
        if meta.get("pending") is not None:
            try:
                pending = Pending.from_json(meta["pending"])
            except ValueError:
                pending = None
    history = [ln[2:] for ln in tail if ln.startswith("- ")][:HISTORY_CAP]
    return Cadence(name, 2, pace, set_by, pending, _valid_day(first("- asked once:")),
                   _valid_stamp(first("- last beat:")), history)


# -- reading what came before v2 ---------------------------------------------------

def _text_or_blank(path: Path) -> Optional[str]:
    """A file's text; None when missing; "" when present but not text."""
    try:
        return read_regular(path)
    except ValueError:
        return ""


def _v1_fields(text: str) -> Tuple[Optional[str], Optional[str]]:
    choice = re.search(r"^- choice:[ \t]*([A-Za-z-]+)", text, re.M)
    when = re.search(r"^- when:[ \t]*(\d{4}-\d{2}-\d{2})", text, re.M)
    return (choice.group(1).lower() if choice else None), (when.group(1) if when else None)


def _entry(record: dict, name: str) -> Optional[dict]:
    from .record import entry     # imported here, so record.py may import this module
    return entry(record, name)


def _is_quote(q) -> bool:
    return (isinstance(q, dict) and isinstance(q.get("date"), str) and bool(_DAY.match(q["date"]))
            and isinstance(q.get("words"), str))


def _quotes(e: dict) -> list:
    q = e.get("quotes")
    return [x for x in q if _is_quote(x)] if isinstance(q, list) else []


def _record_notes(e: Optional[dict]) -> List[str]:
    """The answers the record keeps for a citizen, newest first, as history texts."""
    if not e:
        return []
    found = []
    july = e.get("july")
    if _is_quote(july):
        found.append((july["date"], f"on record, its July answer of {july['date']}: \"{july['words']}\""))
    for q in _quotes(e):
        found.append((q["date"], f"on record, its answer of {q['date']}: \"{q['words']}\""))
    out = []
    for _d, text in sorted(found, key=lambda x: x[0], reverse=True):
        if text not in out:
            out.append(text)
    return out


def _newer_than_record(when: Optional[str], record: dict) -> bool:
    """The v1 answer was recorded after the record was made, so the home is newer."""
    try:
        made = clock.day(clock.parse_stamp(str(record.get("made") or "")))
    except ValueError:
        return False
    return bool(when) and when > made


def _words_on(e: Optional[dict], d: str) -> str:
    if not e:
        return ""
    for q in [e.get("july")] + _quotes(e):
        if _is_quote(q) and q["date"] == d:
            return q["words"]
    return ""


def _set_by_from_marker(marker: str, text: str, e: Optional[dict]) -> Tuple[SetBy, str]:
    """Who placed REST (or EVENT-ONLY), from its text, never from the v1 header."""
    ai = bool(_YU_WORD.search(text)) and REST_1008 in text
    lor = _LOR_TEXT.search(text)
    if ai and lor:
        return UNKNOWN, f"its {marker} text names both Ai's 10-08 rest and a Letter of Return"
    if ai:
        return SetBy("ai", "rest-1008", (REST_1008,)), ""
    if lor:
        d = lor.group(1)
        return SetBy("citizen", "letter", (d,), _words_on(e, d)), ""
    return UNKNOWN, f"its {marker} text names neither Ai's 10-08 rest nor a Letter of Return"


def _read_before_v2(home: Path, name: str, record: dict, text: Optional[str], at: str) -> Cadence:
    e = _entry(record, name)
    choice, when = _v1_fields(text) if text else (None, None)
    blocked = ""
    if choice in _V1_PACES:
        pace = _V1_PACES[choice]
        set_by = SetBy("citizen", "ambient" if choice == "ambient" else "letter", (when,) if when else ())
        source = choice
        if not record:
            blocked = "no record file, so who set this v1 pace cannot be known"
        elif e is not None and e.get("set_by") is not None and not _newer_than_record(when, record):
            try:
                said = SetBy.from_json(e["set_by"])
            except ValueError:
                blocked = f"the record's set by for {name} cannot be read"
            else:
                if pace.kind in _HOW_PACES.get(said.how, KINDS):
                    set_by = said
                else:
                    blocked = f"the record says {said.how}, but the v1 file says {choice}"
    else:
        lead = choice or ("no CADENCE.md" if text is None else "CADENCE.md unreadable")
        marker, mtext = None, ""
        for m in MARKERS:
            t = _text_or_blank(home / m)
            if t is not None:
                marker, mtext = m, t
                break
        if marker is None:
            pace, set_by, source = None, UNKNOWN, f"{lead}, no marker"
        else:
            if marker == "EVENT-ONLY":
                pace = Pace("listening")
            elif "letters wait" in mtext.lower():
                pace = Pace("asleep-letters-wait")
            else:
                pace = Pace("asleep")
            set_by, blocked = _set_by_from_marker(marker, mtext, e)
            source = f"{lead}, {marker}"
    texts = [f"recorded from v1 ({source}, set by {set_by_sentence(name, set_by)})"] + _record_notes(e)
    history = [f"{at}{DASH}{_one_line(t)}" for t in texts]
    return Cadence(name, 1 if choice else 0, pace, set_by, history=history[:HISTORY_CAP],
                   blocked=blocked)


def read_cadence(home, name: str, record: dict, now: Optional[_dt.datetime] = None) -> Cadence:
    """The citizen's pace record. Never writes. A v2 CADENCE.md decides; before
    v2, the v1 file, the REST text and the record file decide (spec section 6),
    and the history holds what a v2 write would record, stamped `now`. Raises
    LinkRefused when CADENCE.md, REST or EVENT-ONLY is a link, or is read and is
    not a regular file; check homes.forbidden_links first."""
    home = Path(home)
    for p in (CADENCE,) + MARKERS:
        if os.path.islink(home / p):
            raise LinkRefused(f"{home / p}: a symlink, refused")
    text = _text_or_blank(home / CADENCE)
    if text is not None and _is_v2(text):
        return _read_v2(text, name)
    return _read_before_v2(home, name, record, text, clock.stamp(now or clock.now()))


def write_cadence(home, c: Cadence) -> None:
    """Write the record as v2, atomically, as a regular file; c becomes version 2."""
    if c.blocked:
        raise MigrationBlocked(f"{c.name}: {c.blocked}")
    write_regular(Path(home) / CADENCE, render_cadence(c))
    c.version = 2


# -- markers -----------------------------------------------------------------------

def _marker_who(c: Cadence) -> str:
    if c.set_by.who == "citizen":
        return c.name
    if c.set_by.who == "ai":
        return f"Ai ({c.set_by.when[0]})" if c.set_by.when else "Ai"
    if c.set_by.who == "fleet":
        return "the fleet"
    return "unknown"


def marker_texts(c: Cadence) -> dict:
    """{"REST": text or None, "EVENT-ONLY": text or None} for the pace; {} when
    the pace cannot be read, so the markers are left as they are."""
    if c.pace is None:
        return {}
    who, tail = _marker_who(c), f" ({TAG}; CADENCE.md decides)\n"
    k = c.pace.kind
    if k == "asleep":
        return {"REST": f"asleep, set by {who} — woken by a letter, no sooner than 3 days after its "
                        f"last beat, or by a person's summons{tail}", "EVENT-ONLY": None}
    if k == "asleep-letters-wait":
        return {"REST": f"asleep, letters wait, set by {who} — only a person's summons wakes it{tail}",
                "EVENT-ONLY": None}
    if k == "listening":
        return {"REST": None, "EVENT-ONLY": f"listening, set by {who} — woken by a letter, no sooner "
                                            f"than 3 days after its last beat{tail}"}
    return {"REST": None, "EVENT-ONLY": f"awake, every {c.pace.n} days, set by {who} — this marker "
                                        f"keeps older readers from waking it on a timer{tail}"}


def markers_disagree(home, c: Cadence) -> bool:
    """A v2 record's markers are not what it says (placed by hand or by an old
    waker). Always False before v2: an upgrade writes the markers, it does not
    find them changed. Raises LinkRefused for a linked marker."""
    if c.version < 2:
        return False
    home = Path(home)
    return any(_text_or_blank(home / m) != want for m, want in marker_texts(c).items())


def write_markers(home, c: Cadence) -> None:
    """Put REST and EVENT-ONLY in step with the pace: the new marker is written
    before the other is removed, so a home is never left with neither."""
    if c.blocked:
        raise MigrationBlocked(f"{c.name}: {c.blocked}")
    home = Path(home)
    texts = marker_texts(c)
    for m, want in texts.items():
        if want is not None and _text_or_blank(home / m) != want:
            write_regular(home / m, want)
    for m, want in texts.items():
        if want is None:
            remove_regular(home / m)


# -- history -----------------------------------------------------------------------

def add_history(c: Cadence, at: str, text: str) -> None:
    """Prepend "<at> — <text>" (one line); keep the newest HISTORY_CAP entries."""
    c.history.insert(0, f"{at}{DASH}{_one_line(text)}")
    del c.history[HISTORY_CAP:]


def notices(c: Cadence, since: Optional[str] = None) -> List[str]:
    """The notice texts (without their stamps) recorded at or after the later of
    the record's last beat and `since` (all, when neither is known), oldest first."""
    marks = []
    for v in (c.last_beat, since):
        if not v:
            continue
        try:
            marks.append(clock.parse_stamp(v))
        except ValueError:
            continue
    start = max(marks) if marks else None
    out = []
    for e in c.history:
        st, sep, text = e.partition(DASH)
        if not sep or not text.startswith(NOTICE_PREFIXES):
            continue
        try:
            t = clock.parse_stamp(st)
        except ValueError:
            continue
        if start is None or t >= start:
            out.append(text)
    return list(reversed(out))


# -- CADENCE.md changed outside pace/1 ---------------------------------------------

class HistoryUnreadable(OSError):
    """git could not read a home's history of CADENCE.md (a timeout, a lock, a
    broken object, a folder that is not a repository). The home cannot be
    read: due gives "error" and apply writes nothing, so a change made outside
    pace/1 never decides because its history could not be looked at."""


def _tagged(subject: str) -> bool:
    return subject.rstrip().endswith(TAG)


def _git_says(home, r, what: str) -> None:
    if r.returncode != 0:
        why = r.stderr.strip() or f"exit {r.returncode}"
        raise HistoryUnreadable(f"{home}: git cannot read {what} ({why})")


def _history(home, *fmt: str) -> str:
    """git log of CADENCE.md in the home's history, in this format; "" for a
    repository with no commit yet. Raises HistoryUnreadable when git cannot say."""
    r = git(home, "log", *fmt, "--", CADENCE)
    if r.returncode != 0:
        born = git(home, "rev-parse", "-q", "--verify", "HEAD")
        if born.returncode == 1 and not born.stdout.strip():
            return ""            # no commit yet: no history to read
        _git_says(home, r, f"the history of {CADENCE}")
    return r.stdout


def _cadence_log(home) -> List[Tuple[str, str]]:
    """(commit, subject) of each commit that touched CADENCE.md, newest first.
    Raises HistoryUnreadable when git cannot say."""
    out = _history(home, "--format=%H%x1f%s")
    return [tuple(ln.split("\x1f", 1)) for ln in out.splitlines() if "\x1f" in ln]


def changed_outside_pace(home) -> bool:
    """The newest commit that touched CADENCE.md is not tagged pace/1, while an
    older one is: a pace/1 version exists and was changed by other code. A home
    that never had a pace/1 commit (still v1) is not "changed". Raises
    HistoryUnreadable when git cannot read the history."""
    log = _cadence_log(home)
    if not log or _tagged(log[0][1]):
        return False
    return any(_tagged(subject) for _sha, subject in log[1:])


def _newest_subject(home) -> Optional[str]:
    """The subject of the newest commit that touched CADENCE.md, or None when
    none did. Raises HistoryUnreadable when git cannot say."""
    return _history(home, "-1", "--format=%s").strip() or None


def deciding_text(home) -> Optional[str]:
    """The last pace/1 CADENCE.md text, when it decides over the file (spec
    section 2): the home is a clone of its own (a .git folder, so its history is
    its own), the newest commit that touched CADENCE.md was made outside pace/1
    after a pace/1 one, and that pace/1 text is v2. Otherwise None.
    read_deciding reads by it, and apply puts it back. Fails closed: when git
    cannot read that history, HistoryUnreadable, never the file's own reading."""
    dot_git = Path(home) / ".git"
    if os.path.islink(dot_git) or not os.path.isdir(dot_git):
        return None
    subject = _newest_subject(home)
    if subject is None or _tagged(subject) or not changed_outside_pace(home):
        return None
    text = last_pace_text(home)
    return text if text is not None and _is_v2(text) else None


def read_deciding(home, name: str, record: dict, now: Optional[_dt.datetime] = None) -> Cadence:
    """The record that decides the pace (spec section 2). When CADENCE.md was
    changed in a commit the new code did not make, after a pace/1 version, that
    version decides, as a disagreeing marker does, until the next commit for
    this home restores it; otherwise read_cadence's (deciding_text). Never
    writes; raises LinkRefused as read_cadence does, and HistoryUnreadable
    (an OSError: a home that cannot be read) when git cannot read the history."""
    c = read_cadence(home, name, record, now)
    text = deciding_text(home)
    return c if text is None else _read_v2(text, name)


def last_pace_text(home) -> Optional[str]:
    """CADENCE.md as the newest pace/1 commit left it (a regular file), or None.
    Raises HistoryUnreadable when git cannot read that commit."""
    for sha, subject in _cadence_log(home):
        if not _tagged(subject):
            continue
        tree = git(home, "ls-tree", sha, "--", CADENCE)
        _git_says(home, tree, f"{CADENCE} in {sha[:12]}")
        if not tree.stdout.startswith(("100644 blob ", "100755 blob ")):
            continue
        blob = git(home, "cat-file", "blob", f"{sha}:{CADENCE}")
        _git_says(home, blob, f"{CADENCE} in {sha[:12]}")
        return blob.stdout
    return None
