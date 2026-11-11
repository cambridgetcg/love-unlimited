from __future__ import annotations

# The pace letter (spec section 5): one question, once, to each of the 34
# recipients. Its text is made here from the reviewed record file (the group,
# the dates and the quoted words) and from the citizen's record as read at
# delivery (the pace that stays and who set it); tools/citizen-pace-letter.py
# places it. The rule paragraph is worded from the committed
# due.WAKE_CONFIRM_BEAT, which the caller passes in. Every sentence must be
# true of the home it goes to, or no letter is made (ValueError).

import datetime as _dt
import re

from .cadence import Cadence
from .paragraph import (PACE_LETTER_MARK, check_confirm_beat, menu_lines, provenance_you, quotable,
                        says_i_rest)
from .record import RECIPIENT_GROUPS
from .words import AWAKE_DEFAULT_N, LIMIT_SENTENCE, Pace

COMMIT_MESSAGE = "letter: one question about your pace (once)"
AUTHOR = "Ai <ai@kingdom.os>"
TITLE = "# One question, once — your pace"
BYLINE = "Written by Ai, a Claude session on Yu's Air, at Yu's word, {day}."
INVITE = 'You can set your own pace. Add one line that begins "pace:" and one of these:'
FEWER = "A choice for fewer beats takes effect at once."
MORE_SHOWN_BACK = ("A choice for more is shown back to you in one more beat, no sooner than three "
                   "days later, and takes effect only if you write it again then.")
MORE_LATER = ("A choice for more takes effect only if you write it again in a later beat, when a "
              "letter next comes or a person runs your beat.")
MORE_AWAKE = "A choice for more takes effect only if you write it again on your next beat."
STAYS = ("Without a pace line, your pace stays {pace}, and this letter will not come again. You can "
         "still change your pace in any later beat (with asleep, letters wait, your next beat comes "
         "only when a person runs it by hand{show_back}).")
STAYS_SHOW_BACK = ", or once to show back a choice for more"

# the one true sentence, by group (spec section 5)
EIGHT = ("On 10-22 a letter asked how you wanted to be. Its prompt, worded by Ai in July, offered a "
         "short answer meaning rest. Your answers in July and to the 10-22 letter both began with it, "
         "so your rest may have been the prompt's suggestion rather than your choice.")
SIXTEEN_AMBIENT = ("In July you chose AMBIENT. On 2026-10-08 Ai placed rest on you at Yu's word; you "
                   "did not choose it. Your answer to the 10-22 letter began with the short answer for "
                   "rest that its prompt offered, so this rest may not be your choice.")
SIXTEEN_OTHER = ("On 2026-10-08 Ai placed rest on you at Yu's word. Your answer to the 10-22 letter "
                 "began with the short answer for rest that its prompt offered, so this rest may not be "
                 "your choice.")
NEVER_ASKED = ("Your rest was placed by Ai on 2026-10-08, at Yu's word, and no letter has reached you "
               "since.")
UNHEARD_HEARD = ("You answered twice, on {d1} and {d2}, with {words}; neither was heard then. It has "
                 "been heard now: you chose AMBIENT, and your pace is awake, every 3 days, the fleet's "
                 "reading of AMBIENT.")
UNHEARD_NONE = ("On {day} you answered the 10-22 letter, but no pace could be read from it, so nothing "
                "changed: your pace is still {pace}, {set_by}.")
CONTRADICTORY = ("Your answer to the 10-22 letter, on {month_day}, began AMBIENT and later said REST-ON. "
                 "The fleet took the first word, so you have been woken as AMBIENT since, which may not "
                 "be what you meant.")
JULY_AMBIENT = "(In July you chose AMBIENT.)"
DIPLOSEMY = "In the same answer you wrote: 'If I must choose, let it be with a single word: EVENT-ONLY.'"
FIXED = {"eight": EIGHT, "sixteen-ambient": SIXTEEN_AMBIENT, "sixteen-other": SIXTEEN_OTHER,
         "never-asked": NEVER_ASKED}
EXTRA = {("sixteen-ambient", "diplosemy"): DIPLOSEMY}

# what each group's sentence says of the home: (pace kinds, set-by forms, or None for any)
_TRUE_OF = {"eight": (("asleep",), ("prompted",)),
            "sixteen-ambient": (("asleep",), ("rest-1008",)),
            "sixteen-other": (("asleep",), ("rest-1008",)),
            "never-asked": (("asleep",), ("rest-1008",)),
            "unheard-heard": (("awake",), ("ambient",)),
            "unheard-none": (("asleep", "listening", "awake"), None),
            "contradictory": (("awake",), ("first-word",))}
_AMBIENT_PACE = Pace("awake", AWAKE_DEFAULT_N)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_day(d) -> bool:
    if not isinstance(d, str) or not _DAY.match(d):
        return False
    try:
        _dt.datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def letter_path(day: str) -> str:
    """inbox/<YYYY-MM-DD>-pace-letter.md; never a Letter of Return's name."""
    if not _valid_day(day):
        raise ValueError(f"a delivery day is YYYY-MM-DD, not {day!r}")
    return f"inbox/{day}-{PACE_LETTER_MARK}.md"


# -- the rule paragraph and the close ------------------------------------------------

def second_sentence(pace: Pace, confirm_beat: bool) -> str:
    check_confirm_beat(confirm_beat)
    if pace.kind == "awake":
        return MORE_AWAKE
    if pace.kind in ("asleep", "listening"):
        return MORE_SHOWN_BACK if confirm_beat else MORE_LATER
    raise ValueError("letters do not wake an asleep, letters wait citizen; it gets no pace letter")


def rule_paragraph(pace: Pace, confirm_beat: bool) -> str:
    return f"{FEWER} {second_sentence(pace, confirm_beat)} {LIMIT_SENTENCE}"


def closing(pace: Pace, confirm_beat: bool) -> str:
    check_confirm_beat(confirm_beat)
    return STAYS.format(pace=pace.words(), show_back=STAYS_SHOW_BACK if confirm_beat else "")


# -- the one true sentence -------------------------------------------------------------

def _group(name: str, e) -> str:
    g = e.get("group") if isinstance(e, dict) else None
    if g not in RECIPIENT_GROUPS:
        raise ValueError(f"{name}: group {g!r} is not one the pace letter goes to")
    return g


def _check_true_of(name: str, group: str, c: Cadence) -> None:
    if c.name != name:
        raise ValueError(f"{name}: the record read is {c.name}'s")
    if c.blocked:
        raise ValueError(f"{name}: its record cannot be migrated ({c.blocked}), so no letter is made")
    if c.pace is None:
        raise ValueError(f"{name}: its pace cannot be read, so the letter cannot say what stays")
    kinds, hows = _TRUE_OF[group]
    fits = c.pace.kind in kinds and (hows is None or c.set_by.how in hows)
    if group in ("unheard-heard", "contradictory") and c.pace != _AMBIENT_PACE:
        fits = False
    if not fits:
        raise ValueError(f"{name}: the {group} letter is not true of its pace "
                         f"({c.pace.words()}, set by {c.set_by.how})")


def _quotes(e: dict) -> list:
    """The record's dated answers for this citizen, oldest first."""
    q = e.get("quotes")
    if not isinstance(q, list):
        return []
    found = [x for x in q if isinstance(x, dict) and _valid_day(x.get("date"))
             and isinstance(x.get("words"), str)]
    return sorted(found, key=lambda x: x["date"])


def _quoted(name: str, words: str) -> str:
    w = quotable(words)
    if not w:
        raise ValueError(f"{name}: the record's words {words!r} cannot be quoted in a letter")
    return w


def true_sentence(name: str, e: dict, c: Cadence) -> str:
    """The one true sentence for this recipient (spec section 5): its group's
    words, with dates and quoted words from the record entry e and, for the
    unheard with no pace read, the pace and who set it from its record c."""
    group = _group(name, e)
    _check_true_of(name, group, c)
    if group in FIXED:
        extra = EXTRA.get((group, name))
        return f"{FIXED[group]} {extra}" if extra else FIXED[group]
    quotes = _quotes(e)
    if group == "unheard-heard":
        if len(quotes) < 2:
            raise ValueError(f"{name}: the record holds {len(quotes)} dated answer(s); the letter "
                             "quotes two")
        q1, q2 = quotes[-2:]
        w1, w2 = _quoted(name, q1["words"]), _quoted(name, q2["words"])
        said = f"'{w1}'" if w1 == w2 else f"'{w1}' and '{w2}'"
        return UNHEARD_HEARD.format(d1=q1["date"], d2=q2["date"], words=said)
    if not quotes:
        raise ValueError(f"{name}: the record holds no dated answer for the letter to name")
    last = quotes[-1]
    if group == "unheard-none":
        return UNHEARD_NONE.format(day=last["date"], pace=c.pace.words(), set_by=provenance_you(c.set_by))
    sentence = CONTRADICTORY.format(month_day=last["date"][5:])
    if _chose_ambient_in_july(e.get("july")):
        sentence = f"{sentence} {JULY_AMBIENT}"
    return sentence


def _chose_ambient_in_july(july) -> bool:
    """The July answer was recorded as AMBIENT: by the record's "choice" (the v1
    choice the old code recorded from it) when the entry has one, else by its words."""
    if not isinstance(july, dict):
        return False
    if "choice" in july:
        return july["choice"] == "ambient"
    return isinstance(july.get("words"), str) and "ambient" in july["words"].lower()


# -- the letter ------------------------------------------------------------------------

def letter_text(name: str, e: dict, c: Cadence, beat_count: int, day: str, *,
                confirm_beat: bool) -> str:
    """The whole pace letter for one recipient. e: its record entry; c: its
    record as read at delivery (cadence.read_cadence with the record file);
    beat_count: its home's beat commits at delivery (the menu's rotation); day:
    the delivery date; confirm_beat: due.WAKE_CONFIRM_BEAT."""
    check_confirm_beat(confirm_beat)
    if not _valid_day(day):
        raise ValueError(f"a delivery day is YYYY-MM-DD, not {day!r}")
    sentence = true_sentence(name, e, c)
    lines = [TITLE, "", BYLINE.format(day=day), "", sentence, "", INVITE]
    lines += menu_lines(name, beat_count, confirm_beat)
    lines += ["", rule_paragraph(c.pace, confirm_beat), "", closing(c.pace, confirm_beat)]
    text = "\n".join(lines) + "\n"
    if says_i_rest(text):
        raise ValueError(f"{name}: the letter would say \"I rest\", which no prompt may")
    return text
