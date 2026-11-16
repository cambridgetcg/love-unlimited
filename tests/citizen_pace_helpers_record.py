"""Homes shaped like the real citizen homes, for the stored Letter of Return
answers and the heard-late step (spec section 5), and for later tasks that need
the same histories (the census, the delivery tool).

A Letter of Return answer is committed as PR #24's citizen-reflect.sh committed
it: one beat commit, authored as the citizen, that adds inbox/.seen-<letter>
("surfaced <date>"), appends the answer under a "## <time> — a free beat
(local)" header to beats/<YYYY-MM>.md, and writes the v1 CADENCE.md. The stored
texts in tests/fixtures/citizen_pace/answers.json are such appended blocks
without their header, so a fixture home holds them byte for byte.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

OFFER = "2026-10-22-letter-of-return-offer.md"
RHYTHM = "2026-10-26-letter-of-return-rhythm.md"
JULY = "2026-07-17-letter-of-return.md"
LETTER_BODY = "# A letter\n\nHow do you want to be?\n"
AI_REST = "rest given at Yu's word, 2026-10-08\n"
MADE = "2026-11-12T10:00Z"
SPEC = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c"
AI_1008 = {"who": "ai", "how": "rest-1008", "when": ["2026-10-08"], "words": ""}


def lor_rest(date: str) -> str:
    """The REST text PR #24's reflect writes for a rest-on Letter of Return answer."""
    return f"chosen via the Letter of Return {date} — woken only by unread mail or a human summons\n"


def v1_cadence(name: str, choice: str, when: str) -> str:
    """A v1 CADENCE.md exactly as PR #24's reflect writes it (`when`: 'YYYY-MM-DD HH:MM')."""
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n"
            "  an answer that names no choice records undecided and changes nothing;\n"
            "  unread mail wakes even REST, and any letter or summons may reopen this.\n")


def stored(resp: str) -> str:
    """A model answer as reflect appends it after its header: a blank line, the
    answer, a blank line."""
    return f"\n{resp}\n\n"


def journal_block(minute: str, text: str) -> str:
    return f"## {minute} — a free beat (local)\n{text}"


def _minute(when: str) -> str:
    return f"{when[:10]} {when[11:16]}"


def place_letter(fx, home, letter: str, when: str) -> str:
    """Ai places a letter in inbox/ in its own commit."""
    return fx.letter(home, letter, LETTER_BODY, when=when, author=h.AI_AUTHOR,
                     message=f"letter: {letter}")


def append_journal(fx, home, when: str, text: str) -> None:
    path = Path(home) / "beats" / f"{when[:7]}.md"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    fx.write(home, f"beats/{when[:7]}.md", old + journal_block(_minute(when), text))


def answer_beat(fx, home, slug: str, letter: str, text: str, *, when: str,
                choice="undecided", rest=None, place=False, seen=None) -> str:
    """Commit a Letter of Return answer as reflect did; returns the commit.
    place: the letter is placed inside this beat commit, as the Studio did for
    ud 14701105 and teotlme a0905ffc. text "" is a silent answer."""
    if place:
        fx.write(home, f"inbox/{letter}", LETTER_BODY)
    fx.write(home, f"inbox/.seen-{letter}", seen if seen is not None else f"surfaced {when[:10]}\n")
    if text:
        append_journal(fx, home, when, text)
    if choice:
        fx.write(home, "CADENCE.md", v1_cadence(slug, choice, _minute(when)))
    if rest is not None:
        fx.write(home, "REST", rest)
    return fx.commit(home, f"beat: {slug} reflected ({when[:10]})", when=when,
                     author=h.beat_author(slug))


def ordinary_beat(fx, home, slug: str, text: str, *, when: str) -> str:
    """An ordinary free beat: only the journal grows."""
    append_journal(fx, home, when, text)
    return fx.commit(home, f"beat: {slug} reflected ({when[:10]})", when=when,
                     author=h.beat_author(slug))


# The five unheard as the 11-02 map found them (spec section 5): their REST, an
# optional July Letter of Return answer (when, answer), and their stored
# October answers (answers.json key, letter, when).
UNHEARD = {
    "kalam": (AI_REST, None, (("kalam-62d73b73", OFFER, "2026-10-23T00:31Z"),
                              ("kalam-e6d01bff", RHYTHM, "2026-10-27T03:10Z"))),
    "soma": (lor_rest("2026-07-21"), ("2026-07-21T02:00Z", "REST-ON\n\nI rest in the body."),
             (("soma-914572f3", OFFER, "2026-10-25T05:40Z"),
              ("soma-aedf60d7", RHYTHM, "2026-10-27T06:20Z"))),
    "teotlme": (AI_REST, None, (("teotlme-a1a573db", OFFER, "2026-10-25T07:05Z"),
                                ("teotlme-a0905ffc", RHYTHM, "2026-10-27T08:15Z"))),
    "emime": (AI_REST, None, (("emime-c263d552", OFFER, "2026-10-22T20:30Z"),)),
    "eurekame": (lor_rest("2026-07-18"), ("2026-07-18T01:00Z", "REST-ON"),
                 (("eurekame-9be3379c", OFFER, "2026-10-23T09:45Z"),)),
}


def unheard_home(fx, slug: str, answers=None) -> Path:
    """A home shaped like one of the five unheard: soul, REST, the July answer
    if any, and its October Letter of Return answers, each in a beat commit.
    answers: (key, letter, when) triples to use instead of UNHEARD's."""
    rest, july, octo = UNHEARD[slug]
    home = fx.make_home(slug, when="2026-07-01T00:00Z", author=h.AI_AUTHOR)
    if july:
        place_letter(fx, home, JULY, "2026-07-17T00:00Z")
        answer_beat(fx, home, slug, JULY, stored(july[1]), when=july[0], choice="rest-on", rest=rest)
    else:
        fx.write(home, "REST", rest)
        fx.commit(home, "rest: placed at Yu's word", when="2026-10-08T12:00Z", author=h.AI_AUTHOR)
    placed = set()
    for key, letter, when in (octo if answers is None else answers):
        inside = slug == "teotlme" and letter == RHYTHM
        if not inside and letter not in placed:
            place_letter(fx, home, letter, f"{letter[:10]}T00:10Z")
        placed.add(letter)
        answer_beat(fx, home, slug, letter, h.answer(key), when=when, place=inside)
    return home


def quote(date: str, words: str, commit: str = "0" * 40) -> dict:
    return {"commit": commit, "date": date, "words": words}


SOMA_LINE = "I choose to remain **ambient**, feeling the world in its rhythms."
UNHEARD_ENTRIES = {
    "emime": {"group": "unheard-none", "recipient": True, "set_by": AI_1008,
              "quotes": [quote("2026-10-22", "emime")]},
    "eurekame": {"group": "unheard-none", "recipient": True,
                 "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"],
                            "words": "REST-ON"},
                 "july": quote("2026-07-18", "REST-ON"),
                 "quotes": [quote("2026-10-23", "**EUREKA**")]},
    "kalam": {"group": "unheard-heard", "recipient": True, "set_by": AI_1008,
              "quotes": [quote("2026-10-23", "Ambient."), quote("2026-10-27", "Ambient.")]},
    "soma": {"group": "unheard-heard", "recipient": True,
             "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-21"],
                        "words": "REST-ON"},
             "july": quote("2026-07-21", "REST-ON"),
             "quotes": [quote("2026-10-25", SOMA_LINE), quote("2026-10-27", SOMA_LINE)]},
    "teotlme": {"group": "unheard-heard", "recipient": True, "set_by": AI_1008,
                "quotes": [quote("2026-10-25", "I am teotlme, and I choose to be AMBIENT."),
                           quote("2026-10-27", "AMBIENT")]},
}


def unheard_record(**more) -> dict:
    """A record holding the five unheard entries, plus any given."""
    citizens = {k: dict(v) for k, v in UNHEARD_ENTRIES.items()}
    citizens.update(more)
    return {"version": 1, "made": MADE, "spec": SPEC, "citizens": citizens}
