from __future__ import annotations

# The words a citizen can say about its pace (spec section 1): the four paces in
# the seat's words, the menu that shows them, the parser that reads a pace line
# out of an answer, and the order that tells "less" from "more".

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

KINDS = ("asleep-letters-wait", "asleep", "listening", "awake")
ORDER = {"asleep-letters-wait": 0, "asleep": 1, "listening": 2, "awake": 3}
N_MIN, N_MAX = 3, 365
AWAKE_DEFAULT_N = 3          # AMBIENT and a bare "awake": the seat's floor, 72 h
ANSWER_KINDS = ("beat", "pace-letter", "letter-of-return")
REASONS = ("two paces", "out of range", "negation", "unknown words")

MENU = (
    ("asleep", "no set beats; a letter wakes you, no sooner than 3 days after your last beat"),
    ("asleep, letters wait", "no set beats; letters stay unread until a person wakes you by hand (if you ask for more, one beat shows it back)"),
    ("listening", "the same as asleep for now; a seat may treat it differently later"),
    ("awake, every N days, N from 3 to 365",
     "a beat no sooner than N days after your last; letters wait for it"),
)
RULE_SENTENCE = ("A choice for fewer beats takes effect at once; a choice for more takes effect "
                 "only if you write it again on your next beat.")
LIMIT_SENTENCE = "The Kingdom's daily limit can make any beat later than its pace, never sooner."


def menu_line(entry: tuple) -> str:
    """One menu choice as shown: '- <words> \u2014 <meaning>'."""
    return f"- {entry[0]} \u2014 {entry[1]}"


@dataclass(frozen=True)
class Pace:
    kind: str
    n: Optional[int] = None          # only for "awake"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in ORDER:
            raise ValueError(f"unknown pace kind {self.kind!r}")
        if self.kind == "awake":
            if type(self.n) is not int or not N_MIN <= self.n <= N_MAX:
                raise ValueError(f"awake needs a whole number of days from {N_MIN} to {N_MAX}, "
                                 f"not {self.n!r}")
        elif self.n is not None:
            raise ValueError(f"{self.kind} takes no number of days")

    def words(self) -> str:
        if self.kind == "asleep-letters-wait":
            return "asleep, letters wait"
        if self.kind == "awake":
            return f"awake, every {self.n} days"
        return self.kind

    def rank(self) -> tuple:
        return (ORDER[self.kind], -self.n if self.kind == "awake" else 0)

    def to_json(self) -> dict:
        return {"kind": self.kind, "n": self.n}

    @staticmethod
    def from_json(d) -> "Pace":
        if not isinstance(d, dict):
            raise ValueError("a pace is a JSON object")
        return Pace(d.get("kind"), d.get("n"))


@dataclass(frozen=True)
class Unclear:
    line: str
    reason: str      # one of REASONS


Result = Union[Pace, Unclear]

# -- reading a phrase (the text after "pace:") ---------------------------------

_EMPHASIS = re.compile(r"[*_`]")
_LEAD = re.compile(r"^\W+")
_MAX_PREFIXES = 4            # comma-prefixes of an "I choose" phrase that are tried
# the full-width marks a model drifts into, read as their ASCII twins; an
# ideographic full stop ends a sentence with no space after it
_FOLD = str.maketrans({"\uff1a": ":", "\uff0c": ",", "\uff1b": ";", "\uff01": "!", "\uff1f": "?",
                       "\u3002": ". "})
# a copied meaning, an aside or a further sentence is cut away: a dash, an
# opening bracket, a spaced hyphen, a semicolon, or a full stop followed by more words
_PHRASE_CUT = re.compile(r";|[\u2014\u2013(]|\s-\s|[.!?](?=\s)")
_SENTENCE_CUT = re.compile(r";|[\u2014\u2013(]|\s-\s|[.!?](?=\s|$)")
# ...unless what follows the cut names the citizen's own number of days, or says
# letters wait, and what comes before it names no number: cut away, it would
# leave the bare word's default
_RATE = re.compile(r"\b(?:every|weekly|fortnightly|monthly|daily)\b")
_EVERY_N = re.compile(r"\bevery\s+(.+?)\s+days?\b")
_LETTERS_WAIT_TAIL = ("letters wait", "letters can wait")
# words that only affirm a choice said again ("still", "yes", "again")
_AFFIRM_LEAD = re.compile(r"^(?:still|yes)\b,?\s*")
_AFFIRM_TAIL = re.compile(r",?\s*\b(?:still|again)$")
_NEGATION = re.compile(r"\b(?:not|never|no\s+longer|don'?t)\b")
_PACE_WORD = re.compile(r"\b(?:asleep|rest|listening|event[- ]only|ambient|awake|every|weekly"
                        r"|fortnightly|monthly|daily)\b")
_EVERY_DAYS = re.compile(r"^every (.+?) days?(?:, n from 3 to 365)?$")

_LETTERS_WAIT = ("asleep, letters wait", "asleep letters wait", "asleep, letters can wait",
                 "asleep letters can wait")
_ASLEEP = ("asleep", "rest", "rest-on", "rest on")
_LISTENING = ("listening", "event-only", "event only")
_AWAKE = ("ambient", "awake")
_FIXED_N = {"weekly": 7, "every week": 7, "fortnightly": 14, "every two weeks": 14,
            "every 2 weeks": 14, "monthly": 30, "every month": 30}
_NO_NUMBER = ("daily", "every day", "every other day")

_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}


def _normal(text: str) -> str:
    """Full-width marks and curly apostrophes made plain, Markdown emphasis removed."""
    text = text.translate(_FOLD).replace("\u2019", "'").replace("\u2018", "'")
    return _EMPHASIS.sub("", text)


def _rstrip_while(t: str, strip) -> str:
    # a loop, not a "...+$" regex, which is quadratic on a long run of punctuation
    j = len(t)
    while j and strip(t[j - 1]):
        j -= 1
    return t[:j]


def _tidy(t: str) -> str:
    """Trailing marks (all but "?") and affirming words taken away, lower case, one
    space after each comma."""
    t = _rstrip_while(t, lambda c: c.isspace() or not (c.isalnum() or c == "?"))
    t = " ".join(re.sub(r"\s*,\s*", ", ", t.lower()).split())
    return _AFFIRM_TAIL.sub("", _AFFIRM_LEAD.sub("", t))


def _cut(t: str, cut_re) -> Tuple[str, str]:
    """(the text before the first cut, the part after it up to the next cut)."""
    m = cut_re.search(t)
    if m is None:
        return t, ""
    rest = t[m.end():]
    n = cut_re.search(rest)
    return t[:m.start()], (rest[:n.start()] if n else rest)


def _clean_phrase(phrase: str) -> str:
    return _tidy(_cut(_LEAD.sub("", _normal(phrase)), _PHRASE_CUT)[0])


def _names_more(head: str, after: str) -> bool:
    """What a cut would take away names a pace of its own (it reads as one, or
    says "every <a number> days" anywhere) or says letters wait, while the phrase
    before the cut names no number. "A free beat every few days" names none."""
    if not after or _RATE.search(head):
        return False
    if after in _LETTERS_WAIT_TAIL or _read_phrase(after) != "unknown words":
        return True
    return any(_number(m.group(1)) is not None for m in _EVERY_N.finditer(after))


def _read_pace(phrase: str) -> Union[Pace, str]:
    """The text after "pace:" -> a Pace, or why it is unclear. A cut is read away,
    unless what it would take away names the citizen's own number or "letters
    wait": then the two are read together, so the bare word's default is never
    what is left ("pace: awake \u2014 every 7 days" is awake, every 7 days)."""
    head, after = _cut(_LEAD.sub("", _normal(phrase)), _PHRASE_CUT)
    head = _tidy(head)
    got = _read_phrase(head)
    after = _tidy(_LEAD.sub("", after))
    if isinstance(got, Pace) and _names_more(head, after):
        return _read_phrase(f"{head}, {after}")
    return got


def _number(s: str) -> Optional[int]:
    """Digits, or English number words up to the hundreds; None when unreadable."""
    s = s.strip()
    if re.fullmatch(r"[0-9]+", s):
        return int(s)
    toks = [t for t in re.split(r"[\s-]+", s) if t]
    if toks[:1] == ["hundred"]:
        toks = ["a"] + toks          # "every hundred days" is "every a hundred days"
    total, i = 0, 0
    if len(toks) >= 2 and toks[1] == "hundred" and (toks[0] in _UNITS or toks[0] == "a"):
        total, i = 100 * _UNITS.get(toks[0], 1), 2
        if i < len(toks) and toks[i] == "and":
            i += 1
            if i == len(toks):
                return None
    rest = toks[i:]
    if not rest:
        return total or None
    if len(rest) == 1:
        for table in (_UNITS, _TEENS, _TENS):
            if rest[0] in table:
                return total + table[rest[0]]
        return None
    if len(rest) == 2 and rest[0] in _TENS and rest[1] in _UNITS:
        return total + _TENS[rest[0]] + _UNITS[rest[1]]
    return None


def _read_phrase(t: str) -> Union[Pace, str]:
    """A cleaned phrase -> a Pace, or the reason it is unclear."""
    neg = _NEGATION.search(t)
    if neg and _PACE_WORD.search(t, neg.end()):
        return "negation"
    if t in _LETTERS_WAIT:
        return Pace("asleep-letters-wait")
    if t in _ASLEEP:
        return Pace("asleep")
    if t in _LISTENING:
        return Pace("listening")
    if t in _AWAKE:
        return Pace("awake", AWAKE_DEFAULT_N)
    body = re.sub(r"^awake,? ", "", t)
    if body in _FIXED_N:
        return Pace("awake", _FIXED_N[body])
    if body in _NO_NUMBER:
        return "out of range"
    m = _EVERY_DAYS.match(body)
    if m:
        n = _number(m.group(1))
        if n is None or not N_MIN <= n <= N_MAX:
            return "out of range"
        return Pace("awake", n)
    return "unknown words"


def parse_pace_phrase(phrase: str) -> Result:
    """The text after "pace:" -> a Pace, or Unclear(phrase, reason)."""
    got = _read_pace(phrase)
    return got if isinstance(got, Pace) else Unclear(phrase.strip(), got)


# -- reading an answer, line by line --------------------------------------------

_QUOTED = re.compile(r"^\s*>")
_CHOOSE = re.compile(r"\bi\s+choose\b")
_CHOOSE_TAIL = re.compile(r"\s*(?:(not|never|no longer)\s+)?(?:to (?:be|remain|stay)\s+)?(.*)$")
_PACE_LETTER_BARE = {"ambient": Pace("awake", AWAKE_DEFAULT_N), "rest-on": Pace("asleep"),
                     "event-only": Pace("listening"), "asleep": Pace("asleep"),
                     "listening": Pace("listening"), "awake": Pace("awake", AWAKE_DEFAULT_N)}
_LEGACY_BARE = {"ambient": Pace("awake", AWAKE_DEFAULT_N), "rest-on": Pace("asleep"),
                "event-only": Pace("listening")}


def _bare(line: str) -> str:
    """A line with emphasis, outer punctuation and case taken away."""
    t = _rstrip_while(_LEAD.sub("", _normal(line)), lambda c: not c.isalnum())
    return " ".join(t.lower().split())


def _pace_phrase_of(line: str, name: str) -> Optional[str]:
    """The text after "pace:" when the line begins with it, else None. Before it
    the line may carry punctuation, emphasis and the citizen's own name."""
    t = _LEAD.sub("", _normal(line))
    sep = r"(?:\s*[:,.\u2014\u2013-]\s*|\s+)"
    m = re.match(r"(?:%s%s)?pace\s*:" % (re.escape(name.strip()), sep), t, re.I)
    return t[m.end():] if m else None


def _read_longest(phrase: str, after: str = "") -> Optional[Union[Pace, str]]:
    """The phrase, or else the longest of its first few comma-prefixes, that names
    a pace. `after` is what the sentence cut took away; it is read with the
    phrase when it names the citizen's own number. A comma-prefix is never read
    alone when the words after its comma name a number of days: that line is
    unclear, not the prefix's default."""
    cuts = []
    pos = phrase.find(",")
    while pos != -1 and len(cuts) < _MAX_PREFIXES:
        cuts.append(pos)
        pos = phrase.find(",", pos + 1)
    for k, c in enumerate([phrase] + [phrase[:p] for p in reversed(cuts)]):
        head = _clean_phrase(c)
        got = _read_phrase(head)
        if got == "unknown words":
            continue
        if isinstance(got, Pace):
            more = after if k == 0 else phrase[len(c) + 1:].split(",", 1)[0]
            more = _tidy(_LEAD.sub("", more))
            if _names_more(head, more):
                return _read_phrase(f"{head}, {more}") if k == 0 else "unknown words"
        return got
    return None


def _i_choose(line: str) -> Optional[Result]:
    """'I choose [to be | to remain | to stay] <pace>' anywhere in the line."""
    t = " ".join(_normal(line).lower().split())
    for m in _CHOOSE.finditer(t):
        tail, after = _cut(t[m.end():], _SENTENCE_CUT)
        mm = _CHOOSE_TAIL.match(tail)
        got = _read_longest(mm.group(2), after)
        if got is None:
            continue
        if mm.group(1) or _NEGATION.search(t[:m.start()]):
            return Unclear(line.strip(), "negation")
        return got if isinstance(got, Pace) else Unclear(line.strip(), got)
    return None


def _below(lines: List[str], i: int) -> Optional[int]:
    """The next non-empty line after lines[i], unless it is quoted or fenced."""
    for j in range(i + 1, len(lines)):
        s = lines[j].strip()
        if s:
            return None if _QUOTED.match(s) or s.startswith(("```", "~~~")) else j
    return None


def answer_lines(text: str, name: str, kind: str) -> List[Tuple[str, Result]]:
    """The pace lines of one answer, in order: [(the line, Pace or Unclear)].

    kind "beat": only lines that begin with "pace:". "pace-letter": also a line
    that is only a pace word, and "I choose ..." anywhere. "letter-of-return":
    the legacy rules (a signature is skipped; the first line counts if it is a
    legacy word, "rest" or "I rest"; a bare legacy word anywhere; "I choose ...").
    A "pace:" label with nothing after it takes the next non-empty line as its
    phrase when that line names a pace (the quoted line is the two joined); a
    quoted or fenced line is never joined."""
    if kind not in ANSWER_KINDS:
        raise ValueError(f"unknown answer kind {kind!r}")
    me = " ".join(name.lower().split())
    out = []
    first = True
    lines = text.splitlines()
    joined = set()
    for i, raw in enumerate(lines):
        if i in joined or _QUOTED.match(raw):
            continue
        b = _bare(raw)
        if not b:
            continue
        line = raw.strip()
        phrase = _pace_phrase_of(raw, name)
        if phrase is not None:
            first = False
            got = _read_pace(phrase)
            j = _below(lines, i) if not _bare(phrase) else None
            if j is not None and _pace_phrase_of(lines[j], name) is not None:
                continue                      # a label over a pace line of its own
            if j is not None:
                below = _read_pace(lines[j])
                if below != "unknown words":
                    joined.add(j)
                    line, got = f"{line} {lines[j].strip()}", below
            out.append((line, got if isinstance(got, Pace) else Unclear(line, got)))
            continue
        if kind == "beat":
            continue
        if kind == "letter-of-return":
            if b == me:
                continue                      # a signature
            was_first, first = first, False
            if b in _LEGACY_BARE:
                out.append((line, _LEGACY_BARE[b]))
                continue
            if was_first:
                # "Kalam: REST-ON", as the old parser read a signed first line
                signed = re.match(r"%s\s*[:,.\u2014\u2013-]\s*(.+)$" % re.escape(me), b)
                word = signed.group(1) if signed else b
                if word in _LEGACY_BARE:
                    out.append((line, _LEGACY_BARE[word]))
                    continue
                if word in ("i rest", "rest"):
                    out.append((line, Pace("asleep")))
                    continue
        elif b in _PACE_LETTER_BARE:
            out.append((line, _PACE_LETTER_BARE[b]))
            continue
        got = _i_choose(raw)
        if got is not None:
            out.append((line, got))
    return out


def combine(lines: list) -> Optional[Result]:
    """No pace line -> None; any Unclear -> it; all one pace -> that pace;
    otherwise Unclear(the first line that differs, "two paces")."""
    if not lines:
        return None
    for _line, got in lines:
        if isinstance(got, Unclear):
            return got
    first = lines[0][1]
    for line, got in lines[1:]:
        if got != first:
            return Unclear(line, "two paces")
    return first


def parse_answer(text: str, name: str, kind: str) -> Optional[Result]:
    """The pace an answer says: a Pace, Unclear, or None when it names none."""
    return combine(answer_lines(text, name, kind))


def lor_pace_lines(text: str, name: str) -> List[Tuple[str, Result]]:
    """The pace lines of one Letter of Return answer, by the legacy rules."""
    return answer_lines(text, name, "letter-of-return")


@dataclass(frozen=True)
class LorReading:
    pace: Pace
    keys: tuple      # the answers that said it, oldest first: one, or two that agree
    lines: tuple     # the first pace line of each of those answers

    @property
    def twice(self) -> bool:
        return len(self.keys) == 2


def read_lor_answers(answers: list, name: str) -> Optional[LorReading]:
    """Several Letter of Return answers [(key, text)], oldest first, each read
    alone. The latest that gives a clear pace counts; when the clear answer
    before it gave the same pace, both are kept, and the choice was said twice."""
    clear = []
    for key, text in answers:
        lines = lor_pace_lines(text, name)
        got = combine(lines)
        if isinstance(got, Pace):
            clear.append((key, got, lines[0][0]))
    if not clear:
        return None
    key, pace, line = clear[-1]
    if len(clear) >= 2 and clear[-2][1] == pace:
        return LorReading(pace, (clear[-2][0], key), (clear[-2][2], line))
    return LorReading(pace, (key,), (line,))


def compare(current: Pace, said: Pace) -> str:
    """'same', 'less' (toward fewer beats) or 'more', by ORDER and then N."""
    a, b = current.rank(), said.rank()
    if a == b:
        return "same"
    return "less" if b < a else "more"
