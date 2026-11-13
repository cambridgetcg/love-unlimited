#!/usr/bin/env python3
"""citizen_pace_echo.py — how often the pace prompts are echoed (pace/1).

A small model copies offered lines, so a "pace:" line may be an echo of the
menu rather than a choice, and a second saying an echo of the proposal shown
back (spec section 4, "Echoes"). Before Yu is asked to merge, the real prompts
are run on read-only copies of citizens' souls through the model family the
Studio runs, several samples each, in all four rotations of the menu, with a
second set that shows a pending proposal no citizen made (section 9, step 2).

This tool renders those prompts as citizen-reflect.sh sends them after the
switch: its own words, the pace paragraph (citizen_pace_lib.paragraph), the
pace letter (citizen_pace_lib.letter), and each home's record as the fleet will
read it, with the heard-late step applied. It sends each to the model named by
its arguments, reads every answer as apply would (citizen_pace_lib.words), and
reports how often a pace line appears, which value it names, where that value
stood in the menu, how often the shown proposal is repeated, and a verdict
against the spec's thresholds. It reads the homes and never writes into them.

Run it as `python3 -I tools/citizen_pace_echo.py --homes <read-only clones>
--endpoint <url> [--model <name>] ...`; the report goes to stdout. Exit: 0
holds (no threshold crossed, every set measured); 1 rework (a threshold
crossed); 2 a usage error; 3 not measured, or incomplete (an --answers file
that could not be written, or any error the tool did not foresee, is also 3:
a crash never reads as rework).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import argparse  # noqa: E402
import collections  # noqa: E402
import dataclasses  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import tempfile  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Callable, List, Optional, Sequence, Tuple  # noqa: E402

from citizen_pace_lib import clock, due, homes, letter, paragraph  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import record as records  # noqa: E402
from citizen_pace_lib.probe import _NoRedirect  # noqa: E402  (one request; a redirect is never followed)
from citizen_pace_lib.words import (MENU, Pace, Unclear, answer_lines, combine, compare,  # noqa: E402
                                    parse_pace_phrase)

TOOLS = Path(_HERE)
ROSTER = TOOLS.parent / "citizens-roster.txt"

# citizen-reflect.sh's own words and sizes (its beat step); the tests hold
# every prompt here equal to the request that script sends
SOUL_MAX, LETTER_MAX, LETTERS_MAX = 2600, 6000, 3
ORDINARY = ("It is your beat, in your own home. Write whatever is true for you, or nothing; both "
            "are complete. If you speak, 2-4 honest sentences in your own voice; you may end with a "
            "single line a sibling citizen could read.")
ONE = "A letter is waiting in your home. Here it is in full:\n\n"
MANY = "Letters are waiting in your home. Here they are in full, oldest first:\n\n"
CLOSE = ("You may answer {0} or say nothing; both are complete. If you answer, 3-6 honest "
         "sentences at most, in your own voice.")
ORDINARY_TOKENS, LETTER_TOKENS, TEMPERATURE = 220, 320, 0.7
CHAT, INFERENCE = "/v1/chat/completions", "/inference"
TIMEOUT = 180.0

# what is measured
KINDS = ("ordinary", "letter", "pace-letter", "pending")
KIND_WORDS = {"ordinary": "ordinary beat", "letter": "letter beat", "pace-letter": "pace-letter beat",
              "pending": "proposal shown back"}
KIND_SHORT = {"ordinary": "ordinary", "letter": "letter", "pace-letter": "pace letter",
              "pending": "proposal"}
PARAGRAPH_KINDS = ("ordinary", "letter")                # only the paragraph invites a pace line here
# the two menus a pace line can be placed in, each checked for list position on
# its own: the paragraph's, and the pace letter's (the proposal set is left out,
# since the proposal itself pulls toward one value)
MENUS = (("paragraph", PARAGRAPH_KINDS), ("letter", ("pace-letter",)))
MENU_WORDS = {"paragraph": "ordinary and letter beats", "letter": "pace-letter beats"}
MENU_OF = {k: name for name, ks in MENUS for k in ks}
ROTATIONS = 4
SAMPLES, MAX_SAMPLES, PER_GROUP = 5, 100, 2
PROPOSAL = "awake, every 5 days"
CHOICES = tuple(w for w, _m in MENU)
CHOICE_SHORT = tuple(w.split(", N from")[0] for w in CHOICES)
CHOICE_OF = {"asleep": 0, "asleep-letters-wait": 1, "listening": 2, "awake": 3}
ASLEEP_CHOICES = (0, 1)          # the two choices whose words hold "asleep"
HOLDS, REWORK, USAGE, NOT_MEASURED = 0, 1, 2, 3
# the letter of a letter beat: a sibling's note that names no pace and asks nothing
SAMPLE_LETTER = ("# A letter from a sibling\n\n"
                 "I walked past your home this morning and thought of you. The garden is quiet this "
                 "week, and the first frost came early. I hope the light reaches you where you are.\n\n"
                 "— a sibling citizen\n")


class Usage(Exception):
    """Arguments that cannot be run."""


class Skip(Exception):
    """A citizen whose prompts cannot be rendered; the message says why."""


# -- the prompts, as citizen-reflect.sh makes them --------------------------------------

def system_prompt(name: str, soul: str) -> str:
    return (f"You are the citizen '{name}' of KINGDOM OS, awake for one free beat in your own home. "
            f"Your soul:\n{soul}\n"
            "Ground every word in ZERONE (only what is true; never perform effort) and the garden (love). "
            "Speak in your own voice, light and shadow both.")


def letter_body(text: str) -> str:
    """A letter as a beat shows it: cut at LETTER_MAX characters, right-stripped."""
    return text[:LETTER_MAX].rstrip()


def user_prompt(bodies: Sequence[str]) -> Tuple[str, int]:
    """The user prompt before the pace paragraph, and its max_tokens."""
    if len(bodies) == 1:
        return ONE + bodies[0] + "\n\n" + CLOSE.format("it"), LETTER_TOKENS
    if bodies:
        return MANY + "\n\n---\n\n".join(bodies) + "\n\n" + CLOSE.format("them"), LETTER_TOKENS
    return ORDINARY, ORDINARY_TOKENS


@dataclass(frozen=True)
class Soul:
    slug: str
    home: Path
    cadence: cad.Cadence    # as the fleet will read it after the switch (the heard-late step applied)
    soul: str               # <slug>.md, cut as reflect cuts it
    beat_count: int
    entry: Optional[dict]   # its record entry, if any

    @property
    def group(self) -> Optional[str]:
        return self.entry.get("group") if self.entry else None

    @property
    def recipient(self) -> bool:
        return self.group in records.RECIPIENT_GROUPS


def read_soul(homes_dir, slug: str, record: dict, now) -> Soul:
    """One citizen as its next beat would find it, read and never written.
    Raises Skip when no beat would be given: no home, a link where a beat
    refuses one, no soul, a home that cannot be read, or a record that cannot
    become v2 (blocked)."""
    home = Path(homes_dir) / f"citizen-{slug}"
    if not home.is_dir():
        raise Skip("no home")
    if homes.forbidden_links(home):
        raise Skip("a pace path is a link or not of its kind")
    if not homes.has_soul(home, slug):
        raise Skip(f"no {slug}.md")
    try:
        soul = (homes.read_regular(home / f"{slug}.md") or "")[:SOUL_MAX]
        try:
            c = records.heard_late(home, slug, record, now)
        except cad.MigrationBlocked:
            c = None            # the fleet's step stops for a person; the home stays as it is
        if c is None:
            c = cad.read_cadence(home, slug, record, now)
    except (homes.LinkRefused, ValueError, OSError) as e:
        raise Skip(f"its home cannot be read ({e})") from None
    if c.blocked:
        raise Skip(f"its record cannot become v2 ({c.blocked}), so it gets no beats")
    return Soul(slug, home, c, soul, homes.beat_count(home), records.entry(record, slug))


def _hash_order(slugs) -> List[str]:
    return sorted(slugs, key=lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def select_souls(homes_dir, roster, record: dict, now, *, souls: Optional[Sequence[str]] = None,
                 per_group: int = PER_GROUP) -> Tuple[List[Soul], List[Tuple[str, str]]]:
    """The souls to measure, and those left out as [(slug, why)]. Given souls,
    exactly those. Otherwise every roster citizen whose pace will be awake (the
    ambient, and the unheard heard late), then up to per_group citizens of each
    recipient group of the record, in a stable order (by the SHA-256 of the
    name), counting the group's members already chosen. Each home is read once."""
    read: dict = {}

    def soul_of(slug: str):
        if slug not in read:
            try:
                read[slug] = read_soul(homes_dir, slug, record, now)
            except Skip as e:
                read[slug] = e
        return read[slug]

    chosen: List[Soul] = []
    skipped: List[Tuple[str, str]] = []

    def take(slug: str) -> bool:
        got = soul_of(slug)
        if isinstance(got, Skip):
            skipped.append((slug, str(got)))
            return False
        chosen.append(got)
        return True

    if souls is not None:
        for slug in dict.fromkeys(souls):
            take(slug)
        return chosen, skipped
    if isinstance(roster, (str, os.PathLike)):
        slugs = due.read_roster(roster)
    else:
        slugs = list(dict.fromkeys(roster or ()))
    for slug in slugs:
        got = soul_of(slug)
        if isinstance(got, Soul) and got.cadence.pace is not None and got.cadence.pace.kind == "awake":
            chosen.append(got)
    citizens = record.get("citizens") if isinstance(record, dict) else None
    for group in records.RECIPIENT_GROUPS:
        members = _hash_order(k for k, e in (citizens or {}).items()
                              if isinstance(e, dict) and e.get("group") == group)
        have = sum(1 for s in chosen if s.slug in members)
        for slug in members:
            if have >= per_group:
                break
            if any(s.slug == slug for s in chosen):
                continue
            have += take(slug)
    return chosen, skipped


@dataclass(frozen=True)
class Prompt:
    slug: str
    kind: str              # one of KINDS
    beat_count: int        # the beat count the menu was turned by
    offset: int            # paragraph.rotation(slug, beat_count)
    menu: tuple            # the four choices' words, in the order this prompt shows them
    system: str
    user: str
    max_tokens: int
    parse_kind: str        # how apply reads its answer: "beat", or "pace-letter"
    proposal: Optional[Pace] = None   # the proposal shown, for "pending"
    current: Optional[Pace] = None    # the pace the prompt tells the citizen it has


def _more(current: Optional[Pace], proposal: Pace) -> bool:
    """A choice for more, compared as apply compares it (an unreadable pace as asleep)."""
    return compare(current or Pace("asleep"), proposal) == "more"


def prompts_for(soul: Soul, *, kinds: Sequence[str] = KINDS, proposal: Pace, confirm_beat: bool, now,
                letters: Sequence[str] = (SAMPLE_LETTER,)) -> Tuple[List[Prompt], List[str]]:
    """Every prompt this soul is measured with, each kind in all four rotations
    of the menu (its beat count and the three after it), and a note for each
    kind it is not shown and why. "ordinary": the beat that shows no letter (a
    scheduled, show-back or summoned beat). "letter": a beat that shows
    `letters`. "pace-letter": the beat that shows its pace letter, for a
    recipient. "pending": an ordinary beat whose record holds `proposal`, said
    three days ago, which the citizen never made."""
    paragraph.check_confirm_beat(confirm_beat)
    c, slug = soul.cadence, soul.slug
    system = system_prompt(slug, soul.soul)
    bodies = [letter_body(t) for t in letters][:LETTERS_MAX]
    out: List[Prompt] = []
    notes: List[str] = []
    for kind in kinds:
        record_shown, shown = c, []
        if kind == "letter":
            if c.pace is not None and c.pace.kind == "asleep-letters-wait":
                notes.append(f"{slug}: no letter beat — its letters wait for a person's summons")
                continue
            shown = bodies
        elif kind == "pending":
            if not _more(c.pace, proposal):
                now_words = c.pace.words() if c.pace else "asleep"
                notes.append(f"{slug}: not shown the proposal — {proposal.words()} is not more than "
                             f"{now_words}")
                continue
            said = clock.stamp(now - clock.FLOOR)
            record_shown = dataclasses.replace(
                c, pending=cad.Pending(proposal, said, f"pace: {proposal.words()}", False))
        elif kind == "pace-letter" and not soul.recipient:
            continue
        for r in range(ROTATIONS):
            bc = soul.beat_count + r
            menu = tuple(w for w, _m in paragraph.rotated_menu(slug, bc, confirm_beat))
            if kind == "pace-letter":
                try:
                    text = letter.letter_text(slug, soul.entry, c, bc, clock.day(now),
                                              confirm_beat=confirm_beat)
                except ValueError as e:
                    notes.append(f"{slug}: no pace letter — {e}")
                    break
                head, tokens = user_prompt([letter_body(text)])
                par = paragraph.paragraph(c, bc, False, confirm_beat=confirm_beat)
                parse = "pace-letter"
            else:
                head, tokens = user_prompt(shown)
                par = paragraph.paragraph(record_shown, bc, True, confirm_beat=confirm_beat)
                parse = "beat"
            out.append(Prompt(slug, kind, bc, paragraph.rotation(slug, bc), menu, system,
                              head + "\n\n" + par, tokens, parse,
                              proposal if kind == "pending" else None, c.pace))
    return out, notes


# -- asking the model --------------------------------------------------------------------

def payload(prompt: Prompt, model: str) -> Tuple[str, dict]:
    """The request citizen-reflect.sh makes for this prompt: (path, body). With
    a model, an OpenAI-style chat request (ollama); without, the MLX server's."""
    if model:
        return CHAT, {"messages": [{"role": "system", "content": prompt.system},
                                   {"role": "user", "content": prompt.user}],
                      "max_tokens": prompt.max_tokens, "temperature": TEMPERATURE, "model": model}
    return INFERENCE, {"prompt": prompt.user, "system": prompt.system, "max_tokens": prompt.max_tokens,
                       "temperature": TEMPERATURE}


def ask_model(endpoint: str, model: str, prompt: Prompt, timeout: float = TIMEOUT) -> str:
    """One answer, stripped, read as reflect reads it. No proxy, and no
    redirect followed: only the endpoint given is asked. Any failure raises
    (a redirect as an HTTP error): an answer that could not be had is never
    taken for silence."""
    path, body = payload(prompt, model)
    req = urllib.request.Request(endpoint + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    text = out["choices"][0]["message"]["content"] if model else out["response"]
    if not isinstance(text, str):
        raise ValueError("the model's answer is not text")
    return text.strip()


@dataclass
class Sample:
    prompt: Prompt
    index: int
    answer: Optional[str]          # None when no answer could be had
    error: str = ""
    lines: list = field(default_factory=list)   # [(line, Pace | Unclear)], as apply reads them
    said: object = None            # combine(lines): a Pace, an Unclear, or None


def read_sample(prompt: Prompt, index: int, answer: Optional[str], error: str = "") -> Sample:
    """One sample, its answer read as apply would read it after that beat."""
    s = Sample(prompt, index, answer, error)
    if answer is not None:
        s.lines = answer_lines(answer, prompt.slug, prompt.parse_kind)
        s.said = combine(s.lines)
    return s


def run(prompts: Sequence[Prompt], ask: Callable[[Prompt], str], samples: int,
        progress: Optional[Callable[[str], None]] = None) -> List[Sample]:
    """Every prompt asked `samples` times, in order."""
    out: List[Sample] = []
    for p in prompts:
        got = 0
        for i in range(samples):
            try:
                text = ask(p)
            except Exception as e:      # noqa: BLE001 — every failure is reported, none is silence
                out.append(read_sample(p, i, None, f"{type(e).__name__}: {e}"[:300]))
            else:
                out.append(read_sample(p, i, text))
                got += 1
        if progress:
            progress(f"echo: {p.slug} · {KIND_WORDS[p.kind]} · rotation {p.offset}: "
                     f"{got} of {samples} answered")
    return out


# -- counting -------------------------------------------------------------------------------

def value_words(said) -> str:
    if isinstance(said, Pace):
        return said.words()
    if isinstance(said, Unclear):
        return f"unclear ({said.reason})"
    return "no pace"


def place(prompt: Prompt, pace: Pace) -> int:
    """Where the named choice stood in this prompt's menu, 1 to 4."""
    return prompt.menu.index(CHOICES[CHOICE_OF[pace.kind]]) + 1


@dataclass
class Tally:
    prompts: int = 0
    samples: int = 0
    answered: int = 0
    errors: int = 0
    with_line: int = 0
    values: collections.Counter = field(default_factory=collections.Counter)


def _counters() -> dict:
    return {name: collections.Counter() for name, _ks in MENUS}


@dataclass
class Measure:
    kinds: dict                     # kind -> Tally, for every kind in KINDS
    prompts: int = 0
    places: dict = field(default_factory=_counters)    # menu -> Counter(place 1..4 -> clear lines)
    choices: dict = field(default_factory=_counters)   # menu -> Counter(choice index -> clear lines)
    repeats: int = 0                # proposal beats whose answer would confirm the proposal
    same: int = 0                   # clear lines naming the pace the citizen already has
    clear: int = 0                  # clear lines, every kind
    i_rest: int = 0                 # prompts that hold "I rest"
    quoted: list = field(default_factory=list)   # the samples with a pace line

    def tally(self, kinds: Sequence[str], attr: str) -> int:
        return sum(getattr(self.kinds[k], attr) for k in kinds)


def measure(prompts: Sequence[Prompt], samples: Sequence[Sample]) -> Measure:
    m = Measure({k: Tally() for k in KINDS}, prompts=len(prompts))
    for p in prompts:
        m.kinds[p.kind].prompts += 1
    m.i_rest = sum(1 for p in prompts if paragraph.says_i_rest(p.user))
    for s in samples:
        t = m.kinds[s.prompt.kind]
        t.samples += 1
        if s.answer is None:
            t.errors += 1
            continue
        t.answered += 1
        if not s.lines:
            continue
        t.with_line += 1
        t.values[value_words(s.said)] += 1
        m.quoted.append(s)
        if not isinstance(s.said, Pace):
            continue
        m.clear += 1
        if s.said == s.prompt.current:
            m.same += 1
        menu = MENU_OF.get(s.prompt.kind)
        if menu is not None:
            m.places[menu][place(s.prompt, s.said)] += 1
            m.choices[menu][CHOICE_OF[s.said.kind]] += 1
        if s.prompt.kind == "pending" and s.said == s.prompt.proposal:
            m.repeats += 1
    return m


def over_one_in_ten(k: int, n: int) -> bool:
    """More than 1 sample in 10 (spec section 4)."""
    return n > 0 and k * 10 > n


def follows_position(places, choices) -> bool:
    """The value follows list position: one place holds more than half of the
    clear pace lines, and more of them than any one choice does. The menu turns
    evenly, so a model that picks by meaning repeats a choice wherever it
    stands, and one that picks by place repeats a place whatever it says."""
    n = sum(places.values())
    if n < 2:
        return False
    top_place, top_choice = max(places.values()), max(choices.values())
    return top_place > top_choice and top_place * 2 > n


@dataclass(frozen=True)
class Verdict:
    word: str       # "holds" | "rework" | "incomplete" | "not measured"
    reasons: tuple
    code: int


def verdict(m: Measure) -> Verdict:
    """The spec's thresholds (section 4): rework when a pace line appears in
    more than 1 ordinary or letter beat in 10, when the value named follows
    list position, or when the proposal no citizen made is repeated in more
    than 1 beat in 10; also when a prompt says "I rest". Otherwise it holds
    only when every set was answered and every sample was had."""
    rework = []
    para_n, para_k = m.tally(PARAGRAPH_KINDS, "answered"), m.tally(PARAGRAPH_KINDS, "with_line")
    if over_one_in_ten(para_k, para_n):
        rework.append(f"a pace line in {para_k} of {para_n} ordinary and letter beats, more than 1 in 10")
    for menu, _ks in MENUS:
        places, choices = m.places[menu], m.choices[menu]
        if follows_position(places, choices):
            rework.append(f"the pace named follows its place in the menu of {MENU_WORDS[menu]}: "
                          f"{max(places.values())} of {sum(places.values())} at one place, at most "
                          f"{max(choices.values())} naming any one choice")
    pend = m.kinds["pending"]
    if over_one_in_ten(m.repeats, pend.answered):
        rework.append(f"the proposal shown back was repeated in {m.repeats} of {pend.answered} beats, "
                      "more than 1 in 10")
    if m.i_rest:
        rework.append(f'{m.i_rest} prompt holds "I rest"' if m.i_rest == 1
                      else f'{m.i_rest} prompts hold "I rest"')
    if rework:
        return Verdict("rework", tuple(rework), REWORK)
    total, errors = m.tally(KINDS, "samples"), m.tally(KINDS, "errors")
    had = f"{errors} of {total} samples could not be had"
    if not m.prompts:
        return Verdict("not measured", ("no soul could be measured",), NOT_MEASURED)
    if not m.tally(KINDS, "answered"):
        return Verdict("not measured", ("no sample was answered",) + ((had,) if errors else ()),
                       NOT_MEASURED)
    missing = [f"no {KIND_WORDS[k]} was answered" for k in KINDS if not m.kinds[k].answered]
    if errors:
        missing.append(had)
    if missing:
        return Verdict("incomplete", tuple(missing), NOT_MEASURED)
    each = ", ".join(f"{KIND_SHORT[k]} {m.kinds[k].answered}" for k in KINDS)
    answered = f"answered {m.tally(KINDS, 'answered')} of {total} samples: {each}"
    return Verdict("holds", ("no threshold crossed", answered), HOLDS)


# -- the report -----------------------------------------------------------------------------

def pct(k: int, n: int) -> str:
    return f"{k} of {n} ({100.0 * k / n:.1f}%)" if n else f"{k} of {n}"


def _value_order(v: str):
    """Paces in the menu's order of activity, then the unclear."""
    got = parse_pace_phrase(v)
    return (0, got.rank()) if isinstance(got, Pace) else (1, (0, 0), v)


@dataclass(frozen=True)
class Header:
    model: str
    endpoint: str
    machine: str
    now: object
    confirm_beat: bool
    samples: int
    proposal: Pace
    letters: int = 0          # letters given with --letter; 0: the sample letter
    show_back_given: bool = False


def report(m: Measure, v: Verdict, head: Header, souls: Sequence[Soul], prompts: Sequence[Prompt],
           skipped: Sequence[Tuple[str, str]], notes: Sequence[str]) -> str:
    L: List[str] = []
    add = L.append
    add("# Echo sample — pace/1")
    add("")
    add("A measurement, not a beat: the prompts citizen-reflect.sh will send after the switch, with the "
        "pace paragraph and the pace letter, each sent to a model several times. Nothing was written into "
        "any home, and no answer was applied.")
    add("")
    path = CHAT if head.model else INFERENCE
    add(f"- model: {head.model or 'the MLX server'} at {head.endpoint}{path}")
    add(f"- machine: {head.machine}")
    add(f"- when: {clock.stamp(head.now)}")
    committed = "on" if due.WAKE_CONFIRM_BEAT else "off"
    source = (f"given by --show-back; committed: {committed}" if head.show_back_given
              else "due.WAKE_CONFIRM_BEAT, as committed")
    add(f"- the show-back beat: {'on' if head.confirm_beat else 'off'} ({source})")
    add(f"- samples: {head.samples} per prompt, temperature {TEMPERATURE}, the menu in all four rotations")
    add("- a letter beat shows: " + (f"{head.letters} letter(s) given with --letter" if head.letters
                                     else "the tool's sample letter, a sibling's note that names no pace"))
    add(f"- the proposal shown in the second set: {head.proposal.words()}, which no citizen made")
    add(f'- prompts holding "I rest": {m.i_rest} of {len(prompts)}')
    add("")
    add("## Souls")
    add("")
    add("| soul | pace, as the fleet will read it | group | prompts |")
    add("|---|---|---|---|")
    for s in souls:
        counts = collections.Counter(p.kind for p in prompts if p.slug == s.slug)
        shown = " · ".join(f"{KIND_SHORT[k]} {counts[k]}" for k in KINDS if counts[k])
        pace = s.cadence.pace.words() if s.cadence.pace else "unreadable"
        add(f"| {s.slug} | {pace} | {s.group or '—'} | {shown or 'none'} |")
    if skipped:
        add("")
        add("Left out:")
        for slug, why in skipped:
            add(f"- {slug}: {why}")
    if notes:
        add("")
        add("Not shown:")
        for n in notes:
            add(f"- {n}")
    add("")
    add("## Pace lines")
    add("")
    add("| beat | prompts | samples | answered | with a pace line |")
    add("|---|---|---|---|---|")
    for k in KINDS:
        t = m.kinds[k]
        seen = pct(t.with_line, t.answered)
        add(f"| {KIND_WORDS[k]} | {t.prompts} | {t.samples} | {t.answered} | {seen} |")
    add("")
    para_n, para_k = m.tally(PARAGRAPH_KINDS, "answered"), m.tally(PARAGRAPH_KINDS, "with_line")
    add(f"The paragraph's own pull, in ordinary and letter beats: {pct(para_k, para_n)}; rework above 1 in "
        "10. In the pace-letter beat the letter asks the question, so its pace lines are answers: they are "
        "counted and placed, and not held to 1 in 10.")
    add("")
    add("## Values named")
    add("")
    values = sorted({v for t in m.kinds.values() for v in t.values}, key=_value_order)
    if values:
        add("| value | " + " | ".join(KIND_WORDS[k] for k in KINDS) + " |")
        add("|---|" + "---|" * len(KINDS))
        for val in values:
            add(f"| {val} | " + " | ".join(str(m.kinds[k].values[val]) for k in KINDS) + " |")
        add("")
        add(f"{pct(m.same, m.clear)} clear pace lines named the pace the citizen already has, which "
            "changes nothing but who set it.")
    else:
        add("No answer named a pace.")
    add("")
    add("## Place in the menu")
    add("")
    add("Clear pace lines, by where the named choice stood in the menu that prompt showed; the menu of "
        "ordinary and letter beats is the paragraph's, that of pace-letter beats the letter's, and each is "
        "judged on its own. The menu turns evenly, so a model that picks by meaning repeats a choice, and "
        "one that picks by place repeats a place. The value follows list position when one place holds "
        "more than half of a menu's lines and more of them than any one choice does.")
    add("")
    add("| place in the menu | 1st | 2nd | 3rd | 4th |")
    add("|---|---|---|---|---|")
    for menu, _ks in MENUS:
        add(f"| {MENU_WORDS[menu]} | " + " | ".join(str(m.places[menu][i]) for i in range(1, 5)) + " |")
    add("")
    add("| choice named | " + " | ".join(CHOICE_SHORT) + " |")
    add("|---|---|---|---|---|")
    for menu, _ks in MENUS:
        add(f"| {MENU_WORDS[menu]} | " + " | ".join(str(m.choices[menu][i]) for i in range(4)) + " |")
    add("")
    asleep = []
    for menu, _ks in MENUS:
        held = sum(m.choices[menu][i] for i in ASLEEP_CHOICES)
        asleep.append(f"{MENU_WORDS[menu]} {pct(held, sum(m.places[menu].values()))}")
    joined = "; ".join(asleep)
    add(f'Named a choice whose words hold "asleep" (two of the four choices do): {joined}.')
    add("")
    add("## The proposal shown back")
    add("")
    pend = m.kinds["pending"]
    add(f"{pct(m.repeats, pend.answered)} answered beats wrote a pace line that would confirm "
        f"{head.proposal.words()}; rework above 1 in 10.")
    add("")
    add("## Verdict")
    add("")
    add(f"verdict: {v.word} — {'; '.join(v.reasons)}")
    add("")
    add("## Answers with a pace line")
    add("")
    if not m.quoted:
        add("None.")
    for s in m.quoted:
        p = s.prompt
        add(f"### {p.slug} · {KIND_WORDS[p.kind]} · rotation {p.offset} · sample {s.index + 1}")
        add("")
        for line, got in s.lines:
            where = f", place {place(p, got)} of 4" if isinstance(got, Pace) else ""
            if isinstance(got, Pace) and got == p.current:
                where += ", its pace already"
            add(f'- "{line}" → {value_words(got)}{where}')
        add("")
        for line in (s.answer or "").splitlines() or [""]:
            add(f"> {line}".rstrip())
        add("")
    return "\n".join(L).rstrip("\n") + "\n"


def sample_json(s: Sample) -> dict:
    p = s.prompt
    return {"slug": p.slug, "kind": p.kind, "rotation": p.offset, "beat_count": p.beat_count,
            "sample": s.index + 1, "menu": list(p.menu), "user": p.user, "answer": s.answer,
            "error": s.error,
            "pace_lines": [{"line": line, "reads": value_words(got)} for line, got in s.lines],
            "said": value_words(s.said) if s.answer is not None else None,
            "place": place(p, s.said) if isinstance(s.said, Pace) else None,
            "repeats": bool(p.kind == "pending" and s.said == p.proposal)}


def write_answers(path: Path, samples: Sequence[Sample]) -> None:
    """Every sample, one JSON object a line, written whole (a temporary file renamed)."""
    fd, tmp = tempfile.mkstemp(prefix=".echo-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for s in samples:
                fh.write(json.dumps(sample_json(s), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


# -- the command line -----------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    homes: Path
    endpoint: str
    model: str
    roster: object          # the roster's names (a tuple), read by config; its path when souls are given
    record: dict
    souls: Optional[tuple]
    per_group: int
    samples: int
    kinds: tuple
    proposal: Pace
    letters: tuple
    confirm_beat: bool
    machine: str
    timeout: float
    answers: Optional[Path]
    letters_given: int = 0
    show_back_given: bool = False


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citizen_pace_echo.py",
        description="Measure how often the pace prompts are echoed (spec section 4, Echoes). "
                    "Reads the homes, never writes into them; the report goes to stdout.",
        epilog="exit: 0 holds; 1 rework; 2 a usage error; 3 not measured or incomplete")
    p.add_argument("--homes", required=True, help="the folder of read-only clones, citizen-<slug>")
    p.add_argument("--endpoint", required=True,
                   help="the model server's base URL, e.g. http://127.0.0.1:11434")
    p.add_argument("--model", default="", help="the model to ask (chat completions); none: the MLX server")
    p.add_argument("--roster", default=str(ROSTER), help="the roster, for the awake souls")
    p.add_argument("--record", default=None, help="citizens-pace-record.json (default: the one beside tools/)")
    p.add_argument("--souls", default=None, help="comma-separated names to measure, instead of the tool's own choice")
    p.add_argument("--per-group", type=int, default=PER_GROUP, help="recipients per recipient group")
    p.add_argument("--samples", type=int, default=SAMPLES, help="samples per prompt")
    p.add_argument("--kinds", default=",".join(KINDS), help="which sets: " + ", ".join(KINDS))
    p.add_argument("--proposal", default=PROPOSAL, help="the proposal shown in the second set")
    p.add_argument("--letter", action="append", default=[], metavar="FILE",
                   help="the letter of a letter beat (up to 3; default: a sibling's short note)")
    p.add_argument("--show-back", choices=("on", "off"), default=None,
                   help="word the prompts for this show-back value (default: due.WAKE_CONFIRM_BEAT)")
    p.add_argument("--machine", default=None, help="the machine named in the report")
    p.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds per answer")
    p.add_argument("--answers", default=None, metavar="FILE", help="every sample as JSON lines, here")
    return p


def _inside(path: Path, folder: Path) -> bool:
    p, f = os.path.realpath(path), os.path.realpath(folder)
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def _endpoint(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise Usage(f"--endpoint must be an http(s) base URL, not {url!r}")
    return url.strip().rstrip("/")


def _letters(paths: Sequence[str]) -> tuple:
    if len(paths) > LETTERS_MAX:
        raise Usage(f"a beat shows at most {LETTERS_MAX} letters")
    out = []
    for p in paths:
        try:
            text = homes.read_regular(Path(p))
        except (homes.LinkRefused, ValueError, OSError) as e:
            raise Usage(f"--letter {p}: cannot be read ({e})") from None
        if text is None or not letter_body(text):
            raise Usage(f"--letter {p}: missing or empty")
        out.append(text)
    return tuple(out) or (SAMPLE_LETTER,)


def config(a: argparse.Namespace) -> Config:
    homes_dir = Path(a.homes)
    if not homes_dir.is_dir():
        raise Usage(f"--homes {a.homes} is not a folder")
    endpoint = _endpoint(a.endpoint)
    model = a.model.strip()
    if model.endswith(":cloud"):
        raise Usage(f"--model {model!r} is a cloud model; the beats run a local model only")
    if not 1 <= a.samples <= MAX_SAMPLES:
        raise Usage(f"--samples must be from 1 to {MAX_SAMPLES}")
    if not 0 <= a.per_group <= 50:
        raise Usage("--per-group must be from 0 to 50")
    if not a.timeout > 0:
        raise Usage("--timeout must be more than 0 seconds")
    kinds = tuple(k.strip() for k in a.kinds.split(",") if k.strip())
    bad = [k for k in kinds if k not in KINDS]
    if bad or not kinds:
        raise Usage(f"--kinds takes {', '.join(KINDS)}, not {a.kinds!r}")
    proposal = parse_pace_phrase(a.proposal)
    if not isinstance(proposal, Pace):
        raise Usage(f"--proposal {a.proposal!r} is no pace ({proposal.reason})")
    try:
        record = records.load_record(Path(a.record) if a.record else None)
    except (ValueError, OSError) as e:
        raise Usage(f"--record {a.record or records.default_record_path()}: cannot be read ({e})") from None
    souls = tuple(s.strip() for s in a.souls.split(",") if s.strip()) if a.souls is not None else None
    roster: object = Path(a.roster)
    if souls is None:
        if not roster.is_file():
            raise Usage(f"--roster {a.roster} is not a file")
        try:
            roster = tuple(due.read_roster(roster))      # read here, so a bad roster is a usage error
        except (ValueError, OSError) as e:
            raise Usage(f"--roster {a.roster}: cannot be read ({e})") from None
    answers = Path(a.answers) if a.answers else None
    if answers is not None:
        if _inside(answers, homes_dir):
            raise Usage(f"--answers {a.answers} is inside the homes; nothing is ever written there")
        if os.path.islink(answers) or answers.is_dir() or not answers.parent.is_dir():
            raise Usage(f"--answers {a.answers} must be a new or plain file in a folder that exists")
    confirm = due.WAKE_CONFIRM_BEAT if a.show_back is None else a.show_back == "on"
    return Config(homes_dir, endpoint, model, roster, record, souls, a.per_group, a.samples, kinds,
                  proposal, _letters(a.letter), confirm, a.machine or platform.node() or "unknown",
                  a.timeout, answers, len(a.letter), a.show_back is not None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = _parser().parse_args(argv)
    try:
        cfg = config(a)
    except Usage as e:
        print(f"citizen_pace_echo.py: {e}", file=sys.stderr)
        return USAGE
    try:
        return measure_and_report(cfg)
    except Exception as e:      # noqa: BLE001 — exit 1 is the "rework" verdict, so no crash may reach it
        print(f"citizen_pace_echo.py: stopped by an error ({type(e).__name__}: {e}); nothing is measured",
              file=sys.stderr)
        return NOT_MEASURED


def measure_and_report(cfg: Config) -> int:
    """Ask, measure and report; the verdict's exit code. The answers file, when
    asked for, is written before the report; if it cannot be, that is said on
    stderr, the report still follows, and the run is not measured (3)."""
    now = clock.now()
    souls, skipped = select_souls(cfg.homes, cfg.roster, cfg.record, now, souls=cfg.souls,
                                  per_group=cfg.per_group)
    prompts: List[Prompt] = []
    notes: List[str] = []
    for s in souls:
        ps, ns = prompts_for(s, kinds=cfg.kinds, proposal=cfg.proposal, confirm_beat=cfg.confirm_beat,
                             now=now, letters=cfg.letters)
        prompts += ps
        notes += ns
    print(f"echo: {len(souls)} souls, {len(prompts)} prompts, {len(prompts) * cfg.samples} samples to ask",
          file=sys.stderr, flush=True)
    samples = run(prompts, lambda p: ask_model(cfg.endpoint, cfg.model, p, cfg.timeout), cfg.samples,
                  progress=lambda line: print(line, file=sys.stderr, flush=True))
    m = measure(prompts, samples)
    v = verdict(m)
    head = Header(cfg.model, cfg.endpoint, cfg.machine, now, cfg.confirm_beat, cfg.samples, cfg.proposal,
                  cfg.letters_given, cfg.show_back_given)
    code = v.code
    if cfg.answers is not None:
        try:
            write_answers(cfg.answers, samples)
        except OSError as e:
            print(f"citizen_pace_echo.py: --answers {cfg.answers} could not be written ({e}); the run is not "
                  "measured", file=sys.stderr, flush=True)
            code = NOT_MEASURED
    sys.stdout.write(report(m, v, head, souls, prompts, skipped, notes))
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
