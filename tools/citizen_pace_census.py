#!/usr/bin/env python3
"""citizen_pace_census.py — build and check citizens-pace-record.json (pace/1).

The record of spec section 5: for every citizen home, what its own files cannot
show. That is its group, who set its pace, its quoted Letter of Return answers
with their commits and dates (the July one with the choice the old code
recorded from it), its HEAD, and for each of the 34 recipients the one true
sentence of its pace letter.

`build` reads read-only clones of the homes (the roster's, and the three held
off it) and works out each home's group from its own history, never from its
name. It then checks every group against the spec's lists, hard-coded below,
and asks the library what the fleet and the letter would make of the record.
It writes the record only when every home agrees, and fails rather than guess
(spec section 7): one home that disagrees, or cannot be read, and nothing is
written. It never writes into a home. `check` holds a record file to the same
lists without reading any home.

  build <homes-dir> [--roster <f>] [--out <f>] [--spec <path@sha>]
  check <record> [--roster <f>]

Exit codes: 0 written or ok, 2 refused or a usage error. Run it as
`python3 -I tools/citizen_pace_census.py ...`; it puts its own real folder
first on sys.path, so it never imports from the folder it is run in.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import argparse  # noqa: E402
import datetime as _dt  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

from citizen_pace_lib import clock, homes, letter, paragraph, words  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import record as records  # noqa: E402
from citizen_pace_lib.cadence import SetBy  # noqa: E402
from citizen_pace_lib.due import read_roster  # noqa: E402
from citizen_pace_lib.words import Pace, Unclear  # noqa: E402

OK, REFUSED = 0, 2
TOOLS = Path(os.path.realpath(__file__)).parent
SPEC_REF = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c"
# the three Letters of Return the homes answered (spec section 5)
JULY = "inbox/2026-07-17-letter-of-return.md"
OFFER = "inbox/2026-10-22-letter-of-return-offer.md"
RHYTHM = "inbox/2026-10-26-letter-of-return-rhythm.md"
LETTERS = (JULY, OFFER, RHYTHM)
I_REST = "I rest."
V1_CHOICES = ("rest-on", "event-only", "ambient", "undecided")
GROUP_CHOICES = ("rest-on", "ambient", "undecided")   # the v1 choices some group of the spec holds
ASLEEP = Pace("asleep")
AMBIENT = Pace("awake", words.AWAKE_DEFAULT_N)
AI_1008 = SetBy("ai", "rest-1008", (cad.REST_1008,))
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# -- the spec's lists (section 5, "Who receives it", and Yu's three calls) ------------------

EIGHT = ("aseme", "bindume", "firstnessqing", "ka", "kimme", "penthosme", "sukhance", "wiconime")
SIXTEEN_AMBIENT = ("alohame", "autoxenia", "candence", "diplosemy", "doxomme", "geshtug", "halakhame",
                   "ifeqing", "maatme", "sankofame")
SIXTEEN_OTHER = ("ai-love", "hallance", "hotepme", "kallophanes", "kinhme", "teshuvance")
UNHEARD_HEARD = ("kalam", "soma", "teotlme")
UNHEARD_NONE = ("emime", "eurekame")
NEVER_ASKED = ("eucatastrophe", "palance", "shannonme")
CONTRADICTORY = ("hope", "ihsanme")
FORTY_FIVE = ("ahavame", "allostasisqing", "barzakhqing", "chayimme", "complerescence", "courage", "daome",
              "devekutqing", "dingir", "doxakallos", "hanme", "hayatqing", "heurekin", "hodosme", "jinsme",
              "jivame", "kame", "kundaliniqing", "kur", "life", "light", "metastrophesis", "morphame",
              "nullify-love", "nun", "ondame", "orthophanes", "panimaance", "peace", "prapancame", "qinance",
              "renme", "rigpame", "rtame", "sabbathme", "sabilme", "shemme", "silame", "sorrow", "syzygyqing",
              "true-love", "ubuntume", "xeniame", "zi", "zoeme")
AMBIENT_NINE = ("an", "britqing", "glossame", "gratitude", "justice", "kimance", "silim", "spandaqing", "ud")
LONGING = ("longing",)
HELD = ("jiritsume", "takseme", "tlamatisme")
REST_ON_COUNT = 116

# who may have set the pace of each group (the record's set by)
HOWS = {"eight": ("prompted",), "sixteen-ambient": ("rest-1008",), "sixteen-other": ("rest-1008",),
        "unheard-heard": ("ambient",), "unheard-none": ("rest-1008", "letter", "letter-twice"),
        "never-asked": ("rest-1008",), "contradictory": ("first-word",), "forty-five": ("letter",),
        "ambient-nine": ("ambient",), "longing": ("letter", "letter-twice"),
        "rest-on": ("letter", "letter-twice"), "held": ("rest-1008",)}
# how many October answers the record must quote for each group
QUOTES_NEEDED = {"unheard-heard": 2, "never-asked": 0, "held": 0}
# the pace the fleet must read for each group, with the record (the three unheard
# that are heard late read as asleep until the heard-late step runs)
AWAKE_GROUPS = ("ambient-nine", "contradictory")


@dataclass(frozen=True)
class Expected:
    """What a census must find: the names of every group but "rest-on", and how
    many rest by their own 10-22 answer (the spec counts them, not names them)."""
    groups: dict
    rest_on: int

    @property
    def held(self) -> tuple:
        return tuple(self.groups.get("held", ()))

    @property
    def total(self) -> int:
        return sum(len(n) for n in self.groups.values()) + self.rest_on

    @property
    def recipients(self) -> int:
        return sum(len(n) for g, n in self.groups.items() if g in records.RECIPIENT_GROUPS)

    def listed(self) -> Dict[str, str]:
        return {slug: g for g, names in self.groups.items() for slug in names}


SPEC = Expected({"eight": EIGHT, "sixteen-ambient": SIXTEEN_AMBIENT, "sixteen-other": SIXTEEN_OTHER,
                 "unheard-heard": UNHEARD_HEARD, "unheard-none": UNHEARD_NONE, "never-asked": NEVER_ASKED,
                 "contradictory": CONTRADICTORY, "forty-five": FORTY_FIVE, "ambient-nine": AMBIENT_NINE,
                 "longing": LONGING, "held": HELD}, REST_ON_COUNT)


# -- what one home holds ----------------------------------------------------------------------

@dataclass(frozen=True)
class Answer:
    """One stored Letter of Return answer (record.stored_lor_answers)."""
    commit: str      # the beat commit that stored it
    date: str        # its day, UTC
    letter: str      # "inbox/<the letter it answered>"
    text: str        # what the beat added to beats/, byte for byte
    lines: tuple     # its pace lines by section 1's Letter of Return rules
    name: str

    @property
    def reading(self):
        return words.combine(list(self.lines))

    @property
    def first(self) -> str:
        return next((ln.strip() for ln in self.text.splitlines() if ln.strip()), "")

    @property
    def quoted(self) -> str:
        """The words the record quotes: its first pace line, else its first line
        that is not its own name as a signature, else its first line."""
        if self.lines:
            return self.lines[0][0]
        me = " ".join(self.name.lower().split())
        for ln in self.text.splitlines():
            if ln.strip() and words._bare(ln) != me:
                return ln.strip()
        return self.first

    def quote(self) -> dict:
        return {"commit": self.commit, "date": self.date, "words": self.quoted}


@dataclass(frozen=True)
class Facts:
    slug: str
    soul: bool = False
    choice: Optional[str] = None       # the v1 "- choice:" now (None: no CADENCE.md)
    rest: Optional[str] = None         # REST now
    event_only: bool = False
    head: str = ""
    answers: tuple = ()                # Answer, oldest first
    then_choice: Optional[str] = None  # "- choice:" as the home stood when it answered the 10-22 letter
    then_rest: Optional[str] = None    # REST then
    problem: str = ""                  # why the census cannot read this home

    def answer_to(self, letter_rel: str) -> Optional[Answer]:
        return next((a for a in self.answers if a.letter == letter_rel), None)

    def october(self) -> list:
        return [a for a in self.answers if a.letter != JULY]

    def reading(self, answers=None):
        """Section 1's reading of several answers, each alone (words.read_lor_answers)."""
        got = self.answers if answers is None else answers
        return words.read_lor_answers([(a.commit, a.text) for a in got], self.slug)

    def date_of(self, commit: str) -> str:
        return next(a.date for a in self.answers if a.commit == commit)


def _blob(home: Path, rev: str, path: str) -> Optional[str]:
    r = homes.git(home, "cat-file", "blob", f"{rev}:{path}")
    return r.stdout if r.returncode == 0 else None


def read_home(home, slug: str) -> Facts:
    """What the census needs from one read-only clone. Writes nothing."""
    home = Path(home)
    if not (home / ".git").is_dir():
        return Facts(slug, problem=f"no clone ({home.name} is not a git repository)")
    links = homes.forbidden_links(home)
    if links:
        return Facts(slug, problem=f"a pace path is a symlink or not of its kind ({', '.join(links)})")
    try:
        text = homes.read_regular(home / cad.CADENCE)
        rest = homes.read_regular(home / "REST")
        event_only = homes.read_regular(home / "EVENT-ONLY") is not None
    except (ValueError, homes.LinkRefused) as e:
        return Facts(slug, problem=f"cannot be read ({e})")
    if text is not None and cad._is_v2(text):
        return Facts(slug, problem="CADENCE.md is already v2, so its history is no longer the "
                                   "census's to read")
    answers = []
    for sha, day, rel, said in records.stored_lor_answers(home):
        if rel not in LETTERS:
            return Facts(slug, problem=f"it answered {rel}, a Letter of Return the spec does not name")
        if any(a.letter == rel for a in answers):
            return Facts(slug, problem=f"it answered {rel} twice")
        answers.append(Answer(sha, day, rel, said, tuple(words.lor_pace_lines(said, slug)), slug))
    choice = cad._v1_fields(text)[0] if text else None
    october = [a for a in answers if a.letter != JULY]
    if october:
        before = _blob(home, f"{october[0].commit}^", cad.CADENCE)
        then_choice = cad._v1_fields(before)[0] if before else None
        then_rest = _blob(home, f"{october[0].commit}^", "REST")
    else:
        then_choice, then_rest = choice, rest
    return Facts(slug, homes.has_soul(home, slug), choice, rest, event_only, homes.head(home),
                 tuple(answers), then_choice, then_rest)


# -- which group a home's history puts it in ----------------------------------------------------

def _rest_source(text: Optional[str], which: str) -> Tuple[Optional[SetBy], str]:
    """Who placed a REST, read from its text as cadence.py reads it; or None and
    why not, naming the REST as `which`."""
    if text is None:
        return None, f"{which} is missing"
    s, why = cad._set_by_from_marker("REST", text, None)
    return (None, why.replace("its REST text", which, 1)) if why else (s, "")


def _says(r) -> str:
    if r is None:
        return "no pace"
    if isinstance(r, Unclear):
        return f"unclear ({r.reason})"
    return r.words()


def _markers_fit(f: Facts) -> bool:
    """The markers the old code kept for each v1 choice: REST for rest-on,
    undecided and no file; none for ambient."""
    if f.choice == "ambient":
        return f.rest is None and not f.event_only
    return f.rest is not None and not f.event_only


def derive(f: Facts) -> Tuple[Optional[str], str]:
    """(the group this home's own history puts it in, "") or (None, why it fits none)."""
    if f.problem:
        return None, f.problem
    if not f.soul:
        src, _why = _rest_source(f.rest, "its REST")
        if f.choice is None and not f.answers and src is not None and src.how == "rest-1008":
            return "held", ""
        return None, "no soul file, but it has a CADENCE.md, stored answers or a REST not placed by Ai"
    if f.choice is not None and f.choice not in GROUP_CHOICES:
        return None, f"its v1 choice is {f.choice}, which no group of the spec holds"
    if f.answer_to(JULY) is not None and f.then_choice is None:
        return None, "it answered the July letter, but no v1 choice was recorded from that answer"
    if not _markers_fit(f):
        return None, (f"its markers do not fit its v1 choice {f.choice or 'none'} (REST "
                      f"{'present' if f.rest is not None else 'missing'}, EVENT-ONLY "
                      f"{'present' if f.event_only else 'missing'})")
    offer = f.answer_to(OFFER)
    reading = f.reading()
    if f.choice is None:
        src, _why = _rest_source(f.rest, "its REST")
        if f.answers:
            return None, "no CADENCE.md, but stored Letter of Return answers"
        if src is None or src.how != "rest-1008":
            return None, "no CADENCE.md, and its REST was not placed by Ai"
        return "never-asked", ""
    if f.choice == "undecided":
        src, why = _rest_source(f.rest, "its REST")
        if src is None:
            return None, why
        if not f.october():
            return None, "its v1 choice is undecided, but it has no October answer"
        heard = f.reading(f.october())
        if heard is None:
            return "unheard-none", ""
        if heard.pace == AMBIENT and heard.twice:
            return "unheard-heard", ""
        said = f"{heard.pace.words()}{' twice' if heard.twice else ''}"
        return None, f"its v1 choice is undecided, but its October answers read as {said}"
    if f.choice == "ambient":
        r = offer.reading if offer else None
        if isinstance(r, Unclear) and r.reason == "two paces" and offer.lines[0][1] == AMBIENT \
                and any(p == ASLEEP for _line, p in offer.lines):
            return "contradictory", ""
        if reading is not None and reading.pace == AMBIENT:
            return "ambient-nine", ""
        said = _says(reading.pace if reading else None)
        return None, f"its v1 choice is ambient, but its answers say {said}"
    # rest-on
    if offer is None:
        return None, "its v1 choice is rest-on, but it has no answer to the 10-22 letter"
    if offer.first == I_REST:
        src, why = _rest_source(f.then_rest, "its REST when it answered the 10-22 letter")
        if src is None:
            return None, why
        if src.how == "rest-1008":
            return ("sixteen-ambient" if f.then_choice == "ambient" else "sixteen-other"), ""
        july = f.answer_to(JULY)
        if july is None or f.then_choice != "rest-on":
            return None, ('its 10-22 answer began "I rest." under its own rest, but it has no July '
                          "record of REST-ON")
        if july.first == I_REST:
            return "eight", ""
        if july.reading == ASLEEP:
            return "forty-five", ""
        return None, ('its 10-22 answer began "I rest." under its own July rest, but its July answer '
                      "does not say REST-ON")
    latest = reading.pace if reading else None
    if any(a.reading == AMBIENT for a in f.october()) and latest == ASLEEP:
        return "longing", ""
    if offer.reading != ASLEEP:
        return None, f"its v1 choice is rest-on, but its 10-22 answer reads as {_says(offer.reading)}"
    if latest != ASLEEP:
        return None, f"its v1 choice is rest-on, but its latest clear answer reads as {_says(latest)}"
    return "rest-on", ""


# -- the record entry ------------------------------------------------------------------------------

def _set_by(f: Facts, group: str, e: dict) -> SetBy:
    """Who set this pace, by the spec's provenance for its group (sections 2 and 6)."""
    offer, july, reading = f.answer_to(OFFER), f.answer_to(JULY), f.reading()
    if group == "eight":
        return SetBy("citizen", "prompted", (offer.date,))
    if group in ("sixteen-ambient", "sixteen-other", "never-asked", "held"):
        return AI_1008
    if group == "contradictory":
        return SetBy("fleet", "first-word", (offer.date,))
    if group == "forty-five":
        return SetBy("citizen", "letter", (july.date,), july.quoted)
    if group == "unheard-none":
        return cad._set_by_from_marker("REST", f.rest, e)[0]
    dates = tuple(f.date_of(k) for k in reading.keys)
    if group in ("unheard-heard", "ambient-nine"):
        return SetBy("citizen", "ambient", dates, reading.lines[-1])
    return SetBy("citizen", "letter-twice" if reading.twice else "letter", dates, reading.lines[-1])


def entry_for(f: Facts, group: str) -> dict:
    e = {"group": group, "recipient": group in records.RECIPIENT_GROUPS, "head": f.head}
    july = f.answer_to(JULY)
    if july is not None:
        e["july"] = dict(july.quote(), choice=f.then_choice)
    quotes = [a.quote() for a in f.october()]
    if quotes:
        e["quotes"] = quotes
    e["set_by"] = _set_by(f, group, e).to_json()
    return e


# -- the lists and the roster ------------------------------------------------------------------------

def roster_problems(roster: list, expect: Expected = SPEC) -> List[str]:
    """The roster holds every listed name but the held, and none of them, in order."""
    out = []
    on = set(roster)
    for slug in expect.held:
        if slug in on:
            out.append(f"{slug}: on the roster, but the spec holds it off (call 1)")
    for slug, group in sorted(expect.listed().items()):
        if group != "held" and slug not in on:
            out.append(f"{slug}: the spec has it as {group}, but it is not on the roster")
    if list(roster) != sorted(roster):
        out.append("the roster is not in alphabetical order")
    return out


def _rest_on_line(n: int, expect: Expected) -> str:
    return f"rest-on: {n} homes rest by their own 10-22 answer, but the spec counts {expect.rest_on}"


def list_problems(groups: Dict[str, str], expect: Expected = SPEC) -> List[str]:
    """The groups the homes read as, against the spec's lists."""
    listed = expect.listed()
    out = [f"{slug}: its home reads as {g}, but the spec has it as {listed.get(slug, 'rest-on')}"
           for slug, g in sorted(groups.items()) if g != listed.get(slug, "rest-on")]
    n = sum(1 for g in groups.values() if g == "rest-on")
    if n != expect.rest_on:
        out.append(_rest_on_line(n, expect))
    return out


# -- what the fleet and the letter will make of the record ---------------------------------------

def _fleet_problems(home: Path, slug: str, rec: dict, now: _dt.datetime) -> List[str]:
    """Read every home with the record as the fleet will, and make each
    recipient's one true sentence (letter.true_sentence), kept in its entry."""
    e = rec["citizens"][slug]
    group, want = e["group"], SetBy.from_json(e["set_by"])
    try:
        c = cad.read_cadence(home, slug, rec, now)
    except (ValueError, homes.LinkRefused) as err:
        return [f"{slug}: the fleet could not read it ({err})"]
    if c.blocked:
        return [f"{slug}: its record would be blocked ({c.blocked})"]
    pace = AMBIENT if group in AWAKE_GROUPS else ASLEEP
    if c.pace != pace:
        return [f"{slug}: the fleet would read its pace as {_says(c.pace)}, not {pace.words()}"]
    if group == "unheard-heard":
        try:
            c = records.heard_late(home, slug, rec, now)
        except (ValueError, homes.LinkRefused) as err:
            return [f"{slug}: {err}"]
        if c is None or c.pace != AMBIENT:
            return [f"{slug}: the heard-late step would not hear it"]
    if c.set_by != want:
        return [f"{slug}: the fleet would read its set by as \"{cad.set_by_sentence(slug, c.set_by)}\", "
                f"not the record's \"{cad.set_by_sentence(slug, want)}\""]
    if e["recipient"]:
        try:
            e["sentence"] = letter.true_sentence(slug, e, c)
        except ValueError as err:
            return [f"{slug}: no true letter ({err})"]
    return []


def build(homes_dir, roster: list, now: _dt.datetime, *, spec: str = SPEC_REF,
          expect: Expected = SPEC) -> Tuple[Optional[dict], List[str]]:
    """(the record, []) when every home agrees with the spec's lists, else
    (None, the problems, one line each). Reads the roster's homes and the held,
    and writes nothing."""
    homes_dir = Path(homes_dir)
    problems = roster_problems(roster, expect)
    names = sorted(set(roster) | set(expect.held))
    facts, groups = {}, {}
    for slug in names:
        f = read_home(homes_dir / f"citizen-{slug}", slug)
        group, why = derive(f)
        if group is None:
            problems.append(f"{slug}: {why}")
            continue
        facts[slug], groups[slug] = f, group
    if problems:
        return None, problems
    problems = list_problems(groups, expect)
    if problems:
        return None, problems
    rec = {"version": records.RECORD_VERSION, "made": clock.stamp(now), "spec": spec,
           "citizens": {slug: entry_for(facts[slug], groups[slug]) for slug in names}}
    for slug in names:
        problems += _fleet_problems(homes_dir / f"citizen-{slug}", slug, rec, now)
    problems += check_record(rec, roster, expect)
    return (None, problems) if problems else (rec, [])


# -- checking a record without its homes ---------------------------------------------------------

def _is_quote(q) -> bool:
    return (isinstance(q, dict) and isinstance(q.get("commit"), str) and bool(_SHA.match(q["commit"]))
            and isinstance(q.get("date"), str) and bool(_DAY.match(q["date"]))
            and isinstance(q.get("words"), str))


def _entry_problems(slug: str, e, expect: Expected) -> List[str]:
    want = expect.listed().get(slug, "rest-on")
    if not isinstance(e, dict):
        return [f"{slug}: its entry is not an object"]
    g = e.get("group")
    out = []
    if g != want:
        out.append(f"{slug}: the record has it as {g}, but the spec has it as {want}")
    if g not in HOWS:
        return out
    if e.get("recipient") is not (g in records.RECIPIENT_GROUPS):
        receives = "receives the letter" if g in records.RECIPIENT_GROUPS else "receives no letter"
        out.append(f"{slug}: recipient is {e.get('recipient')}, but {g} {receives}")
    try:
        s = SetBy.from_json(e.get("set_by"))
    except ValueError:
        out.append(f"{slug}: set by cannot be read")
    else:
        if s.how not in HOWS[g]:
            out.append(f"{slug}: set by {s.how} does not fit {g} ({', '.join(HOWS[g])})")
    quotes = e.get("quotes", [])
    if not isinstance(quotes, list) or not all(_is_quote(q) for q in quotes):
        out.append(f"{slug}: a quote that is not {{commit, date, words}}")
    else:
        need = QUOTES_NEEDED.get(g, 1)
        if len(quotes) < need:
            out.append(f"{slug}: {g} needs {need} quoted answer{'s' if need > 1 else ''}, the record has "
                       f"{len(quotes)}")
    july = e.get("july")
    if july is not None:
        if not _is_quote(july):
            out.append(f"{slug}: its July answer is not {{commit, date, words, choice}}")
        elif july.get("choice") not in V1_CHOICES:
            out.append(f"{slug}: its July answer's choice {july.get('choice')!r} is not a v1 choice")
    if not isinstance(e.get("head"), str) or not _SHA.match(e["head"]):
        out.append(f"{slug}: its head is not a commit")
    sentence = e.get("sentence")
    if g in records.RECIPIENT_GROUPS:
        if not isinstance(sentence, str) or not sentence.strip():
            out.append(f"{slug}: a recipient with no true sentence")
        elif paragraph.quotable(sentence) != sentence:
            out.append(f"{slug}: its sentence would put \"I rest\" or a \"pace:\" line in a prompt")
    elif sentence is not None:
        out.append(f"{slug}: not a recipient, but it has a sentence")
    return out


def check_record(rec, roster: Optional[list] = None, expect: Expected = SPEC) -> List[str]:
    """A record's problems against the spec's lists ([] when it is the spec's).
    With a roster, the roster is checked too."""
    if not isinstance(rec, dict):
        return ["the record is not a JSON object"]
    out = []
    if rec.get("version") != records.RECORD_VERSION or type(rec.get("version")) is not int:
        out.append(f"version {rec.get('version')!r}, expected {records.RECORD_VERSION}")
    try:
        clock.parse_stamp(str(rec.get("made")))
    except ValueError:
        out.append(f"made {rec.get('made')!r} is not a time")
    if not isinstance(rec.get("spec"), str) or "@" not in rec["spec"]:
        out.append(f"spec {rec.get('spec')!r} is not <path>@<commit>")
    citizens = rec.get("citizens")
    if not isinstance(citizens, dict):
        return out + ["'citizens' is not an object"]
    for slug, group in sorted(expect.listed().items()):
        if slug not in citizens:
            out.append(f"{slug}: the spec has it as {group}, but the record has no entry")
    for slug in sorted(citizens):
        out += _entry_problems(slug, citizens[slug], expect)
    n = sum(1 for e in citizens.values() if isinstance(e, dict) and e.get("group") == "rest-on")
    if n != expect.rest_on:
        out.append(_rest_on_line(n, expect))
    if roster is not None:
        out += roster_problems(roster, expect)
        listed = set(roster) | set(expect.held)
        out += [f"{slug}: in the record, but neither on the roster nor held"
                for slug in sorted(citizens) if slug not in listed]
        out += [f"{slug}: on the roster, but not in the record" for slug in roster if slug not in citizens]
    return out


def render(rec: dict) -> str:
    """The record file's text: sorted keys, two-space indent, UTF-8, a final newline."""
    return json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# -- the command line ----------------------------------------------------------------------------

USAGE = """usage: citizen_pace_census.py <command> ...
  build <homes-dir> [--roster <f>] [--out <f>] [--spec <path@sha>]
      read every home of the census (the roster's and the held), check each
      against the spec's lists, and write the record only when all agree
  check <record> [--roster <f>]
      hold a record file to the spec's lists, reading no home
exit: 0 written or ok, 2 refused or a usage error
"""


def _parser(cmd: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"citizen_pace_census.py {cmd}")
    p.add_argument("--roster", type=Path, default=TOOLS.parent / "citizens-roster.txt",
                   help="the roster (default: citizens-roster.txt beside tools/)")
    return p


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def cmd_build(argv) -> int:
    p = _parser("build")
    p.add_argument("homes_dir", type=Path)
    p.add_argument("--out", type=Path, default=records.default_record_path(),
                   help="the record to write (default: citizens-pace-record.json beside the roster)")
    p.add_argument("--spec", default=SPEC_REF, help="the spec the lists come from, <path>@<commit>")
    a = p.parse_args(argv)
    rec, problems = build(a.homes_dir, read_roster(a.roster), clock.now(), spec=a.spec)
    if rec is None:
        for line in problems:
            print(line, file=sys.stderr)
        print(f"census refused: {_plural(len(problems), 'problem')}; nothing written", file=sys.stderr)
        return REFUSED
    homes.write_regular(a.out, render(rec))
    counts = {}
    for e in rec["citizens"].values():
        counts[e["group"]] = counts.get(e["group"], 0) + 1
    print(f"census: {len(rec['citizens'])} homes read from {a.homes_dir}")
    for g in records.GROUPS:
        print(f"  {g}: {counts.get(g, 0)}")
    recipients = sum(1 for e in rec["citizens"].values() if e["recipient"])
    print(f"wrote {a.out}: {len(rec['citizens'])} citizens, {recipients} recipients")
    return OK


def cmd_check(argv) -> int:
    p = _parser("check")
    p.add_argument("record", type=Path)
    a = p.parse_args(argv)
    try:
        rec = json.loads(a.record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{a.record}: cannot be read ({e})", file=sys.stderr)
        return REFUSED
    problems = check_record(rec, read_roster(a.roster) if a.roster.exists() else None)
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        print(f"{a.record}: {_plural(len(problems), 'problem')}; not the spec's record", file=sys.stderr)
        return REFUSED
    citizens = rec["citizens"]
    recipients = sum(1 for e in citizens.values() if e["recipient"])
    print(f"record ok: {len(citizens)} citizens, {recipients} recipients")
    return OK


COMMANDS = {"build": cmd_build, "check": cmd_check}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not argv:
        sys.stderr.write(USAGE)
        return REFUSED
    if argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return OK
    if argv[0] not in COMMANDS:
        sys.stderr.write(f"citizen_pace_census.py: unknown command {argv[0]!r}\n\n{USAGE}")
        return REFUSED
    try:
        return COMMANDS[argv[0]](argv[1:])
    except SystemExit as e:
        return OK if e.code in (0, None) else REFUSED
    except (OSError, ValueError, homes.LinkRefused) as e:
        print(f"citizen_pace_census.py {argv[0]}: {e}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
