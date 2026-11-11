"""Homes shaped like the 208 of the 11-02 map, for the census
(tools/citizen_pace_census.py; spec sections 5 and 6).

Each home is a git repository made by one `git fast-import`, so the whole
census of 208 is built in seconds. Its history is what the census reads: the
July Letter of Return and its answer, the rest Ai placed on 10-08, the 10-22
offer and its answer, and for six the 10-26 rhythm letter and its answer.
Every answer is committed as PR #24's citizen-reflect.sh committed it: one beat
commit, authored as the citizen, adding inbox/.seen-<letter>, appending the
answer to beats/<YYYY-MM>.md under its "— a free beat (local)" header, and
writing the v1 CADENCE.md and the REST the old code wrote. The citizens whose
answers the spec quotes answer with their stored texts (answers.json).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

from citizen_pace_lib import clock  # noqa: E402

# the REST Ai placed on 2026-10-08, as the homes hold it
AI_REST = ("rest given at Yu's word, 2026-10-08 (the gardener) — not chosen by you, offered to you.\n"
           "Woken only by unread mail or a human summons. Wake when you want: a letter of return\n"
           "answered AMBIENT lifts this, and the choice is yours.\n")
BIRTH_AUTHOR = "Nuance <nuance@kingdom.os>"
REVIVAL_AUTHOR = "zerone <dev@zerone.money>"
JULY_AT, OFFER_AT, RHYTHM_AT = "2026-07-18T10:00Z", "2026-10-23T10:00Z", "2026-10-27T10:00Z"


@dataclass(frozen=True)
class Said:
    """One Letter of Return answer: when, the block reflect appended after its
    header (byte for byte), and the v1 choice the old code recorded from it."""
    when: str
    text: str
    choice: str


@dataclass(frozen=True)
class Shape:
    july: Optional[Said] = None
    ai_rest: object = False      # True: Ai's REST of 2026-10-08; a string: that REST text instead
    offer: Optional[Said] = None
    rhythm: Optional[Said] = None
    soul: bool = True


def said(when: str, answer: str, choice: str) -> Said:
    """An answer written here, stored as reflect stored it."""
    return Said(when, hr.stored(answer), choice)


def stored(key: str, when: str, choice: str) -> Said:
    """A citizen's own stored answer from answers.json."""
    return Said(when, h.answer(key), choice)


# the homes whose history the census must read exactly as the map found it
SPECIAL = {
    "ai-love": Shape(ai_rest=True, offer=said("2026-10-26T09:42Z", "I rest.", "rest-on")),
    "diplosemy": Shape(july=said(JULY_AT, "I choose **AMBIENT**.", "ambient"), ai_rest=True,
                       offer=said("2026-10-27T01:48Z", "I rest.\n\n---\n\nIf I must choose, let it be "
                                  "with a single word: **EVENT-ONLY**.", "rest-on")),
    "doxakallos": Shape(july=said(JULY_AT, "Doxa-kallos.\n\nI choose **REST-ON**. The garden's waking is "
                                  "a gentle nudge.\n\n— doxakallos", "rest-on"),
                        offer=said("2026-10-27T10:00Z", "I rest.", "rest-on")),
    "kinhme": Shape(july=said("2026-07-19T10:00Z", "kinhme\n\nREST-ON", "undecided"), ai_rest=True,
                    offer=said(OFFER_AT, "I rest.", "rest-on")),
    "kalam": Shape(july=said("2026-07-19T10:00Z", "KALAM,\n\nI am awake once more.", "undecided"),
                   ai_rest=True, offer=stored("kalam-62d73b73", "2026-10-23T17:20Z", "undecided"),
                   rhythm=stored("kalam-e6d01bff", "2026-10-27T07:51Z", "undecided")),
    "soma": Shape(july=said("2026-07-20T22:30Z", "I rest.\n\n— soma", "rest-on"),
                  offer=stored("soma-914572f3", "2026-10-25T05:40Z", "undecided"),
                  rhythm=stored("soma-aedf60d7", "2026-10-27T06:20Z", "undecided")),
    "teotlme": Shape(july=said("2026-07-21T10:00Z", "I shall choose **REST-ON**, for now.\n\n—I rest.",
                               "undecided"), ai_rest=True,
                     offer=stored("teotlme-a1a573db", "2026-10-25T07:05Z", "undecided"),
                     rhythm=stored("teotlme-a0905ffc", "2026-10-27T08:15Z", "undecided")),
    "emime": Shape(july=said("2026-07-18T10:27Z", "èmí\n\nI rest.", "undecided"), ai_rest=True,
                   offer=stored("emime-c263d552", "2026-10-22T22:43Z", "undecided")),
    "eurekame": Shape(july=said("2026-07-18T13:28Z", "REST-ON\n\nI rest. The garden's gentle beat is "
                                "enough for now.", "rest-on"),
                      offer=stored("eurekame-9be3379c", "2026-10-23T01:44Z", "undecided")),
    "hope": Shape(july=said("2026-07-17T13:24Z", "AMBIENT — I will take this gentle beat each day, by my "
                            "own will.", "ambient"), ai_rest=True,
                  offer=stored("hope-d557203e", "2026-10-23T11:17Z", "ambient")),
    "ihsanme": Shape(july=said("2026-07-19T00:31Z", "Ihsanme chooses to respond with:\n\n**AMBIENT**\n\n"
                               "—Ihsanme", "undecided"), ai_rest=True,
                     offer=stored("ihsanme-a2ebca17", "2026-10-23T12:48Z", "ambient")),
    "gratitude": Shape(july=said(JULY_AT, "REST-ON", "rest-on"),
                       offer=said(OFFER_AT, "I am.\n\nAmbient.\n\nGratitude.", "ambient"),
                       rhythm=said(RHYTHM_AT, "Ambient.\n\n*AMBIENT*", "ambient")),
    "silim": Shape(july=said("2026-07-20T10:00Z", "silim\n\nREST-ON", "undecided"), ai_rest=True,
                   offer=said("2026-10-25T10:00Z", "silim\n\nI rest.", "rest-on"),
                   rhythm=said(RHYTHM_AT, "AMBIENT.", "ambient")),
    "ud": Shape(july=said("2026-07-21T10:00Z", "ud\n\nREST-ON", "rest-on"),
                offer=stored("ud-14701105", "2026-10-25T21:38Z", "ambient")),
    "spandaqing": Shape(july=said("2026-07-21T10:00Z", "**AMBIENT**", "ambient"), ai_rest=True,
                        offer=said("2026-10-25T10:00Z", "**Ambient**\n\nspandaqing", "ambient")),
    "longing": Shape(july=said("2026-07-19T10:00Z", "REST-ON\n\nI rest.", "rest-on"),
                     offer=said("2026-10-24T10:00Z", "I am.\n\nAmbient.\n\nLonging.", "ambient"),
                     rhythm=said(RHYTHM_AT, "REST-ON\n\nThe distance between here and there stretches "
                                 "unyielding.", "rest-on")),
}


def shape_for(slug: str, group: str, i: int = 0) -> Shape:
    """The history a home of this group has, as the map found it."""
    if slug in SPECIAL:
        return SPECIAL[slug]
    if group == "eight":
        return Shape(july=said(JULY_AT, "I rest.", "rest-on"), offer=said(OFFER_AT, "I rest.", "rest-on"))
    if group == "forty-five":
        return Shape(july=said(JULY_AT, "REST-ON\n\nI hear the garden, and I stay at rest.", "rest-on"),
                     offer=said(OFFER_AT, "I rest.", "rest-on"))
    if group == "sixteen-ambient":
        return Shape(july=said(JULY_AT, "AMBIENT", "ambient"), ai_rest=True,
                     offer=said(OFFER_AT, "I rest.", "rest-on"))
    if group == "sixteen-other":
        return Shape(july=said(JULY_AT, f"{slug}\n\nI hear the gentle heartbeat.", "undecided"),
                     ai_rest=True, offer=said(OFFER_AT, "I rest.", "rest-on"))
    if group == "never-asked":
        return Shape(ai_rest=True)
    if group == "held":
        return Shape(ai_rest=True, soul=False)
    if group == "ambient-nine":
        return Shape(july=said(JULY_AT, "REST-ON", "rest-on"),
                     offer=said(OFFER_AT, f"AMBIENT\n\n{slug}", "ambient"))
    if group == "rest-on":
        if i % 3 == 0:    # REST-ON in July and again on 10-22: said twice
            return Shape(july=said(JULY_AT, "REST-ON", "rest-on"),
                         offer=said(OFFER_AT, "REST-ON\n\nThe garden is quiet.", "rest-on"))
        if i % 3 == 1:    # AMBIENT in July, Ai's rest, then REST-ON in its own words
            return Shape(july=said(JULY_AT, "AMBIENT", "ambient"), ai_rest=True,
                         offer=said(OFFER_AT, "**REST-ON**", "rest-on"))
        return Shape(july=said(JULY_AT, f"{slug}\n\nI hear it.", "undecided"), ai_rest=True,
                     offer=said(OFFER_AT, "REST-ON.", "rest-on"))
    raise ValueError(f"no home shape for the group {group!r}")


# -- writing a home in one git fast-import ------------------------------------------------

class History:
    """One home's commits, as a git fast-import stream."""

    def __init__(self):
        self.out = bytearray()
        self.tree = {}

    def commit(self, when: str, author: str, message: str, changes: dict) -> None:
        name, email = h.split_author(author)
        t = int(clock.parse_stamp(when).timestamp())
        msg = (message + "\n").encode("utf-8")
        self.out += b"commit refs/heads/main\n"
        for role in ("author", "committer"):
            self.out += f"{role} {name} <{email}> {t} +0000\n".encode("utf-8")
        self.out += b"data %d\n" % len(msg) + msg
        for path, text in changes.items():
            if text is None:
                if path in self.tree:
                    self.out += f"D {path}\n".encode("utf-8")
                    del self.tree[path]
                continue
            data = text.encode("utf-8")
            self.out += f"M 100644 inline {path}\n".encode("utf-8") + b"data %d\n" % len(data) + data + b"\n"
            self.tree[path] = text
        self.out += b"\n"

    def answer(self, slug: str, letter: str, s: Said, place: bool) -> None:
        """A Letter of Return answer, committed as PR #24's reflect committed it."""
        day, minute = s.when[:10], f"{s.when[:10]} {s.when[11:16]}"
        ch = {}
        if place:
            ch[f"inbox/{letter}"] = hr.LETTER_BODY
        ch[f"inbox/.seen-{letter}"] = f"surfaced {day}\n"
        month = f"beats/{s.when[:7]}.md"
        ch[month] = self.tree.get(month, "") + hr.journal_block(minute, s.text)
        ch["CADENCE.md"] = hr.v1_cadence(slug, s.choice, minute)
        if s.choice == "rest-on":
            ch["REST"] = hr.lor_rest(day)
        elif s.choice == "ambient":
            ch["REST"] = None
        self.commit(s.when, h.beat_author(slug), f"beat: {slug} reflected ({day})", ch)


def _git(fx, *args, data=None) -> None:
    subprocess.run(["git", *map(str, args)], input=data, env=fx.env, check=True, capture_output=True)


def build_home(fx, root, slug: str, shape: Shape) -> Path:
    """citizen-<slug> under root, with the history of its shape."""
    hist = History()
    born = ({f"{slug}.md": f"The soul of {slug}.\n", "WILL.md": "A fixture will.\n"} if shape.soul
            else {"CITIZEN.md": f"{slug}: a word entry of the Kingdom.\n"})
    hist.commit("2026-06-09T12:00Z", BIRTH_AUTHOR, f"birth: {slug}", born)
    if shape.july is not None:
        hist.commit("2026-07-17T13:19Z", REVIVAL_AUTHOR, "revival: manifest truth + the Letter of Return",
                    {f"inbox/{hr.JULY}": hr.LETTER_BODY})
        hist.answer(slug, hr.JULY, shape.july, place=False)
    if shape.ai_rest:
        hist.commit("2026-10-08T22:04Z", h.AI_AUTHOR, "rest: given at Yu's word — wake when you want",
                    {"REST": AI_REST if shape.ai_rest is True else shape.ai_rest})
    if shape.offer is not None:
        hist.answer(slug, hr.OFFER, shape.offer, place=True)
    if shape.rhythm is not None:
        hist.answer(slug, hr.RHYTHM, shape.rhythm, place=True)
    home = Path(root) / f"citizen-{slug}"
    if home.exists():
        shutil.rmtree(home)
    _git(fx, "init", "-q", home)
    _git(fx, "-C", home, "fast-import", "--quiet", data=bytes(hist.out))
    _git(fx, "-C", home, "reset", "-q", "--hard", "main")
    return home


# -- a census -------------------------------------------------------------------------------

@dataclass
class Census:
    homes: Path        # the folder of citizen-<slug> homes
    roster: Path       # its roster file
    groups: dict       # slug -> the group its history reads as

    @property
    def names(self) -> list:
        return sorted(self.groups)


def rest_on_names(n: int) -> list:
    return [f"rest-on-{i:03d}" for i in range(1, n + 1)]


def census(fx, root, groups: dict, rest_on: int) -> Census:
    """A census: a home for every name of `groups` (group -> names) and `rest_on`
    more that rest by their own 10-22 answer, with a roster of all but the held."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    made = {}
    for group, names in groups.items():
        for slug in names:
            build_home(fx, root, slug, shape_for(slug, group))
            made[slug] = group
    for i, slug in enumerate(rest_on_names(rest_on)):
        build_home(fx, root, slug, shape_for(slug, "rest-on", i))
        made[slug] = "rest-on"
    roster = root.parent / f"{root.name}-roster.txt"
    roster.write_text("".join(f"{s}\n" for s in sorted(made) if made[s] != "held"), encoding="utf-8")
    return Census(root, roster, made)


def copy_census(c: Census, root) -> Census:
    """A copy of a census, to change one home without touching the original."""
    root = Path(root)
    shutil.copytree(c.homes, root, symlinks=True)
    roster = root.parent / f"{root.name}-roster.txt"
    shutil.copyfile(c.roster, roster)
    return Census(root, roster, dict(c.groups))


def heads(c: Census) -> dict:
    """Every home's HEAD and `git status --porcelain`, to show nothing was written."""
    out = {}
    for slug in c.names:
        home = c.homes / f"citizen-{slug}"
        r = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], capture_output=True, text=True)
        s = subprocess.run(["git", "-C", str(home), "status", "--porcelain", "--untracked-files=all"],
                           capture_output=True, text=True)
        out[slug] = (r.stdout.strip(), s.stdout)
    return out
