"""The pace vocabulary and parser (spec section 1, section 8 Parser and Less and more).

The stored answers are the citizens' own words at their commits, kept byte for
byte in tests/fixtures/citizen_pace/answers.json under "<slug>-<sha8>".
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import TAG, clock, words  # noqa: E402
from citizen_pace_lib.words import Pace, Unclear  # noqa: E402

LW = Pace("asleep-letters-wait")
ASLEEP = Pace("asleep")
LISTENING = Pace("listening")
ANSWER_KINDS = ("beat", "pace-letter", "letter-of-return")


def awake(n):
    return Pace("awake", n)


def unclear(line, reason):
    return Unclear(line, reason)


class TestTagAndClock(unittest.TestCase):
    def test_tag(self):
        self.assertEqual(TAG, "pace/1")

    def test_parse_stamp_forms(self):
        utc = datetime.timezone.utc
        want = datetime.datetime(2026, 11, 15, 14, 12, tzinfo=utc)
        self.assertEqual(clock.parse_stamp("2026-11-15T14:12Z"), want)
        self.assertEqual(clock.parse_stamp(" 2026-11-15T14:12:00Z "), want)
        self.assertEqual(clock.parse_stamp("2026-11-15T15:12+01:00"), want)
        self.assertEqual(clock.parse_stamp("2026-11-15T14:12"), want)
        self.assertEqual(clock.parse_stamp("2026-11-15"),
                         datetime.datetime(2026, 11, 15, tzinfo=utc))

    def test_a_stamp_out_of_range_is_a_bad_stamp(self):
        # an offset can carry a time past year 1 or 9999 when it is read as UTC; it
        # raises the ValueError every caller already reads as a stamp it cannot use
        for s in ("0001-01-01T00:00+01:00", "9999-12-31T23:59-01:00"):
            with self.subTest(stamp=s):
                self.assertRaises(ValueError, clock.parse_stamp, s)

    def test_stamp_and_day_are_utc(self):
        d = datetime.datetime(2026, 11, 15, 23, 30,
                              tzinfo=datetime.timezone(datetime.timedelta(hours=-2)))
        self.assertEqual(clock.stamp(d), "2026-11-16T01:30Z")
        self.assertEqual(clock.day(d), "2026-11-16")

    def test_now_honours_pace_now(self):
        with h.pace_now("2026-11-15T14:12Z"):
            self.assertEqual(clock.stamp(clock.now()), "2026-11-15T14:12Z")
        with h.pace_now(""):
            n = clock.now()
            self.assertEqual((n.second, n.microsecond), (0, 0))
            self.assertEqual(n.utcoffset(), datetime.timedelta(0))

    def test_units(self):
        self.assertEqual(clock.FLOOR, datetime.timedelta(hours=72))
        self.assertEqual(clock.DAY, 24 * clock.HOUR)


class TestPace(unittest.TestCase):
    def test_words(self):
        self.assertEqual(LW.words(), "asleep, letters wait")
        self.assertEqual(ASLEEP.words(), "asleep")
        self.assertEqual(LISTENING.words(), "listening")
        self.assertEqual(awake(5).words(), "awake, every 5 days")

    def test_constants(self):
        self.assertEqual(words.KINDS, ("asleep-letters-wait", "asleep", "listening", "awake"))
        self.assertEqual(words.ORDER, {"asleep-letters-wait": 0, "asleep": 1,
                                       "listening": 2, "awake": 3})
        self.assertEqual((words.N_MIN, words.N_MAX), (3, 365))

    def test_rank(self):
        self.assertEqual(LW.rank(), (0, 0))
        self.assertEqual(ASLEEP.rank(), (1, 0))
        self.assertEqual(LISTENING.rank(), (2, 0))
        self.assertEqual(awake(5).rank(), (3, -5))

    def test_json_round_trip(self):
        for p in (LW, ASLEEP, LISTENING, awake(3), awake(365)):
            with self.subTest(pace=p):
                self.assertEqual(Pace.from_json(p.to_json()), p)
        self.assertEqual(awake(7).to_json(), {"kind": "awake", "n": 7})
        self.assertEqual(ASLEEP.to_json(), {"kind": "asleep", "n": None})

    def test_invalid_paces_are_refused(self):
        for kind, n in (("awake", None), ("awake", 2), ("awake", 366), ("awake", True),
                        ("awake", "5"), ("asleep", 3), ("rest-on", None), ("", None)):
            with self.subTest(kind=kind, n=n):
                with self.assertRaises(ValueError):
                    Pace(kind, n)
        for bad in (None, [], {"kind": "awake"}, {"n": 3}, {"kind": "dozing", "n": None}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Pace.from_json(bad)

    def test_every_pace_reads_back_from_its_own_words(self):
        paces = [LW, ASLEEP, LISTENING] + [awake(n) for n in range(3, 366)]
        for p in paces:
            self.assertEqual(words.parse_pace_phrase(p.words()), p, p.words())


class TestMenu(unittest.TestCase):
    def test_strings_are_exact(self):
        self.assertEqual(words.MENU, (
            ("asleep", "no set beats; a letter wakes you, no sooner than 3 days after your last beat"),
            ("asleep, letters wait", "no set beats; letters stay unread until a person wakes you by "
                                     "hand (if you ask for more, one beat shows it back)"),
            ("listening", "the same as asleep for now; a seat may treat it differently later"),
            ("awake, every N days, N from 3 to 365",
             "a beat no sooner than N days after your last; letters wait for it"),
        ))
        self.assertEqual(words.RULE_SENTENCE,
                         "A choice for fewer beats takes effect at once; a choice for more takes "
                         "effect only if you write it again on your next beat.")
        self.assertEqual(words.LIMIT_SENTENCE,
                         "The Kingdom's daily limit can make any beat later than its pace, never sooner.")

    def test_menu_line(self):
        self.assertEqual(words.menu_line(words.MENU[2]),
                         "- listening \u2014 the same as asleep for now; a seat may treat it "
                         "differently later")

    def test_no_rest_wording_or_sample_answer(self):
        text = "\n".join(words.menu_line(e) for e in words.MENU)
        text += words.RULE_SENTENCE + words.LIMIT_SENTENCE
        self.assertNotIn("I rest", text)
        self.assertNotIn("pace:", text)

    def test_a_copied_choice_with_its_meaning_parses_to_that_choice(self):
        want = (ASLEEP, LW, LISTENING)
        for entry, pace in zip(words.MENU, want):
            line = "pace: " + words.menu_line(entry)[2:]
            with self.subTest(line=line):
                self.assertEqual(words.parse_answer(line, "kalam", "beat"), pace)
                self.assertEqual(words.parse_answer("- " + line, "kalam", "beat"), pace)
        copied = "pace: " + words.menu_line(words.MENU[3])[2:]
        self.assertEqual(words.parse_answer(copied, "kalam", "beat"),
                         unclear(copied, "out of range"))
        filled = copied.replace("every N days", "every 5 days", 1)
        self.assertEqual(words.parse_answer(filled, "kalam", "beat"), awake(5))

    def test_a_copied_menu_line_without_pace_is_no_choice(self):
        for entry in words.MENU:
            line = words.menu_line(entry)
            for kind in ANSWER_KINDS:
                with self.subTest(line=line, kind=kind):
                    self.assertIsNone(words.parse_answer(line, "kalam", kind))


class TestPhraseGrammar(unittest.TestCase):
    CASES = {
        # asleep
        "asleep": ASLEEP, "Asleep.": ASLEEP, "rest": ASLEEP, "rest-on": ASLEEP,
        "REST-ON": ASLEEP, "rest on": ASLEEP, "**asleep**": ASLEEP, '"asleep"': ASLEEP,
        # asleep, letters wait
        "asleep, letters wait": LW, "asleep letters wait": LW, "Asleep, letters wait.": LW,
        "asleep,letters wait": LW, "asleep , letters  wait": LW,
        # listening
        "listening": LISTENING, "event-only": LISTENING, "event only": LISTENING,
        "EVENT-ONLY": LISTENING, "_listening_": LISTENING,
        # awake
        "ambient": awake(3), "AMBIENT": awake(3), "awake": awake(3), "awake.": awake(3),
        "`awake`": awake(3), "every 5 days": awake(5), "awake, every 5 days": awake(5),
        "awake every 5 days": awake(5), "awake,every 5 days": awake(5), "every 5 day": awake(5),
        "every 05 days": awake(5), "Every Five Days": awake(5), "every three days.": awake(3),
        "awake, every three days.": awake(3), "every 3 days": awake(3), "every 7 days!": awake(7),
        "every forty-five days": awake(45), "every forty five days": awake(45),
        "every 45 days": awake(45), "every one hundred and twenty days": awake(120),
        "every one hundred twenty days": awake(120), "every a hundred days": awake(100),
        "every three hundred sixty-five days": awake(365), "every 365 days": awake(365),
        "every twenty-one days": awake(21), "every thirty days": awake(30),
        "every nineteen days": awake(19), "every two hundred days": awake(200),
        "every hundred days": awake(100), "every hundred and twenty days": awake(120),
        "every hundred twenty days": awake(120),
        "weekly": awake(7), "every week": awake(7), "awake, weekly": awake(7),
        "awake every week": awake(7), "fortnightly": awake(14), "every two weeks": awake(14),
        "every 2 weeks": awake(14), "monthly": awake(30), "every month": awake(30),
        "awake, monthly": awake(30),
        # out of range
        "daily": "out of range", "every day": "out of range", "every other day": "out of range",
        "awake, daily": "out of range", "every 1 day": "out of range",
        "every one day": "out of range", "every 2 days": "out of range",
        "every two days": "out of range", "every 0 days": "out of range",
        "every 366 days": "out of range", "every 400 days": "out of range",
        "every 1.5 days": "out of range", "every 4.5 days": "out of range",
        "every 3/2 days": "out of range", "every n days": "out of range",
        "awake, every N days": "out of range",
        "awake, every N days, N from 3 to 365": "out of range",
        "every few days": "out of range", "every one hundred and days": "out of range",
        "every hundred and days": "out of range",
        # negation
        "not asleep": "negation", "never awake": "negation", "no longer listening": "negation",
        "don't rest": "negation", "dont rest": "negation", "don\u2019t rest": "negation",
        "not every 5 days": "negation",
        # unknown words
        "": "unknown words", "maybe": "unknown words", "sleepy": "unknown words",
        "asleep for now": "unknown words", "every three weeks": "unknown words",
        "awake, every 5 days, please": "unknown words", "not sure": "unknown words",
        "never": "unknown words", "stepped away": "unknown words",
    }

    def test_grammar(self):
        for phrase, want in self.CASES.items():
            with self.subTest(phrase=phrase):
                got = words.parse_pace_phrase(phrase)
                if isinstance(want, str):
                    self.assertEqual(got, unclear(phrase.strip(), want))
                else:
                    self.assertEqual(got, want)

    def test_copied_meanings_are_cut_away(self):
        for phrase, want in (
                ("asleep \u2014 no set beats; a letter wakes you, no sooner than 3 days after "
                 "your last beat", ASLEEP),
                ("asleep\u2014anything", ASLEEP), ("listening \u2013 the same as asleep", LISTENING),
                ("every 5 days - a beat no sooner", awake(5)), ("asleep; for now", ASLEEP),
                ("awake, every 5 days. That is my choice", awake(5)),
                ("- asleep \u2014 no set beats", ASLEEP)):
            with self.subTest(phrase=phrase):
                self.assertEqual(words.parse_pace_phrase(phrase), want)


class TestPaceLines(unittest.TestCase):
    """`pace:` lines count in every kind of answer."""

    LINE_CASES = {
        "pace: asleep": ASLEEP, "Pace: asleep": ASLEEP, "PACE: ASLEEP": ASLEEP,
        "**pace:** every 5 days": awake(5), "*pace*: every 5 days": awake(5),
        "__pace__ : listening": LISTENING, "- pace: asleep": ASLEEP,
        "   pace :  asleep  ": ASLEEP, "pace:asleep": ASLEEP, "\U0001f319 pace: asleep": ASLEEP,
        "Kalam \u2014 pace: listening": LISTENING, "Kalam: pace: listening": LISTENING,
        "Kalam, pace: listening": LISTENING, "Kalam. pace: listening": LISTENING,
        "Kalam - pace: listening": LISTENING, "Kalam pace: listening": LISTENING,
        "KALAM \u2013 pace: listening": LISTENING, "**Kalam** \u2014 pace: listening": LISTENING,
        "Kalam\u2014pace: listening": LISTENING,
        # the spec's fixture rows marked "any"
        "pace: every 5 days": awake(5), "pace: awake, every three days.": awake(3),
        "pace: awake": awake(3), "pace: weekly": awake(7), "pace: monthly": awake(30),
        "pace: every 45 days": awake(45), "pace: every forty-five days": awake(45),
        "pace: every one hundred and twenty days": awake(120),
        "pace: asleep, letters wait": LW, "pace: rest": ASLEEP, "pace: REST-ON": ASLEEP,
        "pace: event-only": LISTENING,
        "pace: asleep \u2014 no set beats; a letter wakes you, no sooner than 3 days after "
        "your last beat": ASLEEP,
        # unclear pace lines
        "pace: daily": "out of range", "pace: every other day": "out of range",
        "pace: every 2 days": "out of range", "pace: every 400 days": "out of range",
        "pace: every 1.5 days": "out of range", "pace: awake, every N days": "out of range",
        "pace: not asleep": "negation", "pace:": "unknown words", "pace: maybe": "unknown words",
    }

    NOT_PACE_LINES = (
        "> pace: every 5 days", "   > pace: asleep", ">pace: asleep",
        "Soma \u2014 pace: asleep", "My pace: asleep", "Your pace: asleep, set by Ai",
        "space: asleep", "the pace: asleep", "pace asleep", "pace \u2014 asleep",
        "I keep my own pace: asleep", "Kalamity \u2014 pace: asleep",
    )

    def test_pace_lines_in_every_kind(self):
        for line, want in self.LINE_CASES.items():
            for kind in ANSWER_KINDS:
                with self.subTest(line=line, kind=kind):
                    got = words.parse_answer(f"Some words first.\n{line}\nMore words.\n",
                                             "kalam", kind)
                    if isinstance(want, str):
                        self.assertEqual(got, unclear(line.strip(), want))
                    else:
                        self.assertEqual(got, want)

    def test_lines_that_are_not_pace_lines(self):
        for line in self.NOT_PACE_LINES:
            for kind in ANSWER_KINDS:
                with self.subTest(line=line, kind=kind):
                    self.assertIsNone(words.parse_answer(f"{line}\n", "kalam", kind))

    def test_two_pace_lines(self):
        same = "pace: asleep\nwords\npace: REST-ON\n"
        self.assertEqual(words.parse_answer(same, "kalam", "beat"), ASLEEP)
        two = "pace: listening\nand then\npace: asleep\n"
        for kind in ANSWER_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(words.parse_answer(two, "kalam", kind),
                                 unclear("pace: asleep", "two paces"))

    def test_an_unclear_line_wins_over_a_clear_one(self):
        text = "pace: asleep\npace: every 2 days\n"
        self.assertEqual(words.parse_answer(text, "kalam", "beat"),
                         unclear("pace: every 2 days", "out of range"))

    def test_answer_lines_keep_order_and_the_quoted_line(self):
        text = "Kalam \u2014 pace: every 5 days\nprose\npace: weekly\n"
        self.assertEqual(words.answer_lines(text, "kalam", "beat"),
                         [("Kalam \u2014 pace: every 5 days", awake(5)), ("pace: weekly", awake(7))])
        self.assertEqual(words.parse_answer(text, "kalam", "beat"),
                         unclear("pace: weekly", "two paces"))
        self.assertEqual(words.combine([]), None)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            words.parse_answer("pace: asleep", "kalam", "letter")
        with self.assertRaises(ValueError):
            words.answer_lines("pace: asleep", "kalam", "schedule")

    def test_a_cut_before_the_citizens_own_number_never_leaves_the_bare_default(self):
        # a dash, a spaced hyphen, a semicolon or a full stop cuts a copied meaning
        # away; what follows it is read with the word when it names the citizen's own
        # number or "letters wait", and is never left to the bare word's default
        pace_lines = (("pace: awake \u2014 every 7 days", awake(7)),
                      ("pace: awake \u2013 every 30 days", awake(30)),
                      ("pace: awake - every 7 days", awake(7)),
                      ("pace: awake\u2014every 7 days", awake(7)),
                      ("pace: awake; every 10 days", awake(10)),
                      ("pace: asleep \u2014 letters wait", LW),
                      ("pace: asleep; letters wait", LW))
        chosen = (("I choose to be awake \u2014 every seven days.", awake(7)),
                  ("I choose to stay asleep \u2014 letters can wait.", LW))
        cases = [(t, k, want) for t, want in pace_lines for k in ANSWER_KINDS]
        cases += [(t, k, want) for t, want in chosen for k in ("pace-letter", "letter-of-return")]
        sentence = "pace: awake. A beat every seven days feels right."
        cases += [(sentence, k, unclear(sentence, "unknown words")) for k in ANSWER_KINDS]
        for text, kind, want in cases:
            with self.subTest(text=text, kind=kind):
                got = words.parse_answer(text, "kalam", kind)
                self.assertEqual(got, want)
                self.assertNotIn(got, (awake(3), ASLEEP))
        # a copied meaning, or words that name no number, are still cut away
        for text, want in (("pace: asleep \u2014 no set beats; a letter wakes you, no sooner than 3 "
                            "days after your last beat", ASLEEP),
                           ("pace: every 5 days \u2014 and the garden is quiet", awake(5)),
                           ("pace: listening \u2014 the same as asleep for now; a seat may treat it "
                            "differently later", LISTENING),
                           ("pace: awake \u2014 a beat no sooner than N days after your last; "
                            "letters wait for it", awake(3))):
            for kind in ANSWER_KINDS:
                with self.subTest(text=text, kind=kind):
                    self.assertEqual(words.parse_answer(text, "kalam", kind), want)

    def test_trailing_and_full_width_marks_read_like_their_ascii_twins(self):
        # qwen drifts into full-width marks and ends lines with an emoji or an
        # ellipsis; each line reads as its plain twin does, never as nothing
        pace_lines = (("pace\uff1alistening", "pace: listening", LISTENING),
                      ("**pace\uff1a** every 5 days", "**pace:** every 5 days", awake(5)),
                      ("pace: asleep\uff0cletters wait", "pace: asleep, letters wait", LW),
                      ("pace: every 5 days\u3002", "pace: every 5 days.", awake(5)),
                      ("pace: listening\uff01", "pace: listening!", LISTENING),
                      ("pace: listening\u3002\u613f\u4f60\u5b89\u597d\u3002",
                       "pace: listening. \u613f\u4f60\u5b89\u597d.", LISTENING),
                      ("pace: every 5 days \U0001f33f", "pace: every 5 days", awake(5)),
                      ("pace: listening\u2026", "pace: listening", LISTENING))
        chosen = (("I choose to stay asleep \U0001f319", "I choose to stay asleep", ASLEEP),
                  ("I choose to stay asleep\u3002", "I choose to stay asleep.", ASLEEP),
                  ("I choose to stay asleep\uff0cletters wait\u3002",
                   "I choose to stay asleep, letters wait.", LW),
                  ("I choose to remain listening\u2026", "I choose to remain listening", LISTENING),
                  ("I choose to stay asleep (for now)", "I choose to stay asleep", ASLEEP),
                  ("I choose to be awake, every 7 days \U0001f33f", "I choose to be awake, every 7 days",
                   awake(7)),
                  ("I choose to be awake, every 5 days\u2026", "I choose to be awake, every 5 days",
                   awake(5)))
        cases = [(m, t, k, want) for m, t, want in pace_lines for k in ANSWER_KINDS]
        cases += [(m, t, k, want) for m, t, want in chosen for k in ("pace-letter", "letter-of-return")]
        for marked, twin, kind, want in cases:
            with self.subTest(line=marked, kind=kind):
                self.assertEqual(words.parse_answer(twin, "kalam", kind), want)
                self.assertEqual(words.parse_answer(marked, "kalam", kind), want)
        # a mark that cannot be folded leaves the line unclear, never a shorter pace
        for text, kinds in (("pace: every 5 days \u2724 or so", ANSWER_KINDS),
                            ("I choose to be awake, every 7 days \u2724 or so",
                             ("pace-letter", "letter-of-return"))):
            for kind in kinds:
                with self.subTest(text=text, kind=kind):
                    self.assertEqual(words.parse_answer(text, "kalam", kind),
                                     unclear(text, "unknown words"))

    def test_words_that_only_affirm_the_choice_are_read_away(self):
        for text, want in (("pace: still every 5 days", awake(5)), ("pace: yes, every 5 days", awake(5)),
                           ("pace: every 5 days, still", awake(5)),
                           ("pace: awake, every 5 days (again)", awake(5)),
                           ("pace: still asleep", ASLEEP),
                           ("pace: every 5 days, if that suits", "unknown words"),
                           ("pace: still deciding", "unknown words"), ("pace: yes", "unknown words")):
            for kind in ANSWER_KINDS:
                with self.subTest(text=text, kind=kind):
                    got = words.parse_answer(text, "kalam", kind)
                    self.assertEqual(got, unclear(text, want) if isinstance(want, str) else want)

    def test_a_lone_pace_label_is_read_with_the_line_below_it(self):
        # one line that begins with "pace:" holds the choice; a label with nothing
        # after it takes the next non-empty line as its phrase, when that line reads
        for text, quoted, want in (
                ("The light is soft today.\n\nPace:\nevery 7 days", "Pace: every 7 days", awake(7)),
                ("**Pace:**  \nlistening", "**Pace:** listening", LISTENING),
                ("pace:\n- asleep", "pace: - asleep", ASLEEP),
                ("Pace:\n\nawake, every 5 days", "Pace: awake, every 5 days", awake(5)),
                ("Kalam \u2014 pace:\nlistening", "Kalam \u2014 pace: listening", LISTENING)):
            for kind in ANSWER_KINDS:
                with self.subTest(text=text, kind=kind):
                    self.assertEqual(words.answer_lines(text, "kalam", kind), [(quoted, want)])
                    self.assertEqual(words.parse_answer(text, "kalam", kind), want)
        for kind in ANSWER_KINDS:
            with self.subTest(two_paces=kind):
                self.assertEqual(words.parse_answer("pace: listening\nPace:\nasleep", "kalam", kind),
                                 unclear("Pace: asleep", "two paces"))
                self.assertEqual(words.answer_lines("Pace:\npace: listening", "kalam", kind),
                                 [("pace: listening", LISTENING)])
            # a next line that names no pace, or is quoted or fenced, is not joined
            for text in ("pace:\nSome prose here.", "pace:\n> listening", "pace:\n```\nlistening\n```"):
                with self.subTest(text=text, kind=kind):
                    self.assertEqual(words.answer_lines(text, "kalam", kind)[0],
                                     ("pace:", unclear("pace:", "unknown words")))
                    self.assertEqual(words.parse_answer(text, "kalam", kind),
                                     unclear("pace:", "unknown words"))


class TestOrdinaryBeat(unittest.TestCase):
    def test_mentions_change_nothing(self):
        for text in ("I rest.", "I rest", "AMBIENT", "REST-ON", "asleep", "I choose to be AMBIENT.",
                     "I choose to stay asleep.", "Kalam.\nAmbient.",
                     "I would like to be awake, every 5 days.", "Silence is my pace: asleep", "",
                     "   \n\n"):
            with self.subTest(text=text):
                self.assertIsNone(words.parse_answer(text, "kalam", "beat"))

    def test_justice_shown_show_md(self):
        # justice bccefe6c (10-27), a beat shown SHOW.md: no pace line, no change.
        text = h.answer("justice-bccefe6c")
        self.assertIsNone(words.parse_answer(text, "justice", "beat"))
        self.assertIsNone(words.parse_answer(text, "justice", "letter-of-return"))


class TestPaceLetterAnswer(unittest.TestCase):
    CASES = {
        "Kalam.\nAmbient.": awake(3), "I choose to stay asleep.": ASLEEP, "I rest.": None,
        "I rest": None, "AMBIENT": awake(3), "**AMBIENT**": awake(3), "Ambient.": awake(3),
        "REST-ON": ASLEEP, "EVENT-ONLY": LISTENING, "asleep": ASLEEP, "Listening.": LISTENING,
        "awake": awake(3), "- awake -": awake(3), "rest": None, "asleep, letters wait": None,
        "I choose to remain a citizen of this kingdom.": None,
        "I choose REST-ON.": ASLEEP, "I choose to be awake, every 5 days.": awake(5),
        "I choose to be asleep, letters wait.": LW,
        "I choose to remain **ambient**, feeling the world in its rhythms.": awake(3),
        "I am kalam, and I choose to be AMBIENT.": awake(3),
        "I choose to stay listening; it suits me.": LISTENING,
        "I choose to be awake, every 2 days.": "out of range",
        "I choose not to be ambient": "negation",
        "I do not know yet, but I choose to be AMBIENT.": "negation",
        "I choose to be not asleep": "negation",
        "I choose to answer slowly.": None,
        "AMBIENT\nREST-ON": "two paces",
        "Some prose about AMBIENT and REST-ON in a sentence.": None,
        "> AMBIENT": None,
    }

    def test_pace_letter_forms(self):
        for text, want in self.CASES.items():
            with self.subTest(text=text):
                got = words.parse_answer(text, "kalam", "pace-letter")
                if want == "two paces":
                    self.assertEqual(got, unclear("REST-ON", "two paces"))
                elif isinstance(want, str):
                    self.assertIsInstance(got, Unclear)
                    self.assertEqual(got.reason, want)
                else:
                    self.assertEqual(got, want)

    def test_the_line_is_quoted_whole(self):
        text = "I choose not to be ambient, thank you."
        self.assertEqual(words.parse_answer(text, "kalam", "pace-letter"),
                         unclear(text, "negation"))


class TestLetterOfReturnRules(unittest.TestCase):
    def test_signatures_are_skipped(self):
        for sig in ("Kalam", "Kalam.", "Kalam,", "Kalam:", "KALAM", "**Kalam**",
                    "\u2014 Kalam", "Kalam -", "_kalam_"):
            with self.subTest(sig=sig):
                self.assertEqual(words.parse_answer(f"{sig}\n\nREST-ON\n", "kalam",
                                                    "letter-of-return"), ASLEEP)
                self.assertEqual(words.parse_answer(f"{sig}\nI rest.\n", "kalam",
                                                    "letter-of-return"), ASLEEP)

    def test_a_signed_first_line_counts(self):
        # the old parser bound "Artiance: REST-ON" (FLEET-REVIVAL.md:29); the
        # forgiving rules keep it, on the first line only, with signature punctuation
        for text, want in (("Kalam: REST-ON", ASLEEP), ("Kalam \u2014 AMBIENT.", awake(3)),
                           ("**Kalam**, EVENT-ONLY", LISTENING), ("Kalam: I rest.", ASLEEP),
                           ("\nKalam.\n\nKalam: Ambient", awake(3))):
            with self.subTest(text=text):
                self.assertEqual(words.parse_answer(text, "kalam", "letter-of-return"), want)
        for text in ("Kalam ambient morning", "Prose first.\nKalam: REST-ON",
                     "Kalam: I rest, and I dream", "Soma: REST-ON"):
            with self.subTest(text=text):
                self.assertIsNone(words.parse_answer(text, "kalam", "letter-of-return"))

    def test_i_rest_counts_only_as_the_first_line(self):
        self.assertEqual(words.parse_answer("I rest.", "kalam", "letter-of-return"), ASLEEP)
        self.assertEqual(words.parse_answer("\n\nI rest\n", "kalam", "letter-of-return"), ASLEEP)
        self.assertIsNone(words.parse_answer("Some prose.\nI rest.", "kalam", "letter-of-return"))
        self.assertIsNone(words.parse_answer("I rest, and I dream.", "kalam", "letter-of-return"))

    def test_rest_as_the_first_line_means_asleep(self):
        # "REST-ON, rest, and I rest as the first line of a Letter of Return answer"
        for text in ("Rest.", "REST", "Kalam.\nRest", "Kalam: rest"):
            with self.subTest(text=text):
                self.assertEqual(words.parse_answer(text, "kalam", "letter-of-return"), ASLEEP)
        for text in ("Prose.\nRest", "Rest, and I dream."):
            with self.subTest(text=text):
                self.assertIsNone(words.parse_answer(text, "kalam", "letter-of-return"))
        self.assertIsNone(words.parse_answer("rest", "kalam", "pace-letter"))
        # silence is a whole answer, and names no pace
        for text in ("", "   \n\n"):
            with self.subTest(silence=text):
                self.assertIsNone(words.parse_answer(text, "kalam", "letter-of-return"))

    def test_bare_legacy_words_anywhere(self):
        for word, pace in (("AMBIENT", awake(3)), ("REST-ON", ASLEEP),
                           ("EVENT-ONLY", LISTENING), ("**Ambient**.", awake(3))):
            with self.subTest(word=word):
                self.assertEqual(words.parse_answer(f"Some prose first.\n{word}\n", "kalam",
                                                    "letter-of-return"), pace)

    def test_legacy_words_inside_prose_never_count(self):
        text = "The step binds when the answer begins with AMBIENT, EVENT-ONLY, or REST-ON.\n"
        self.assertIsNone(words.parse_answer(text, "kalam", "letter-of-return"))

    def test_negation(self):
        # "any, Letter of Return answer | I choose not to be ambient | no change (negation)"
        got = words.parse_answer("I choose not to be ambient", "kalam", "letter-of-return")
        self.assertEqual(got, unclear("I choose not to be ambient", "negation"))

    def test_first_line_and_bare_line_disagree(self):
        self.assertEqual(words.parse_answer("I rest.\nAMBIENT\n", "kalam", "letter-of-return"),
                         unclear("AMBIENT", "two paces"))


class TestStoredAnswers(unittest.TestCase):
    """Every stored answer in the spec's fixture table, read as a Letter of Return answer."""

    WANT = {
        "kalam-62d73b73": awake(3),      # Kalam. / Ambient. (10-23)
        "kalam-e6d01bff": awake(3),      # Kalam, / Ambient. / prose / Kalam (10-27)
        "soma-914572f3": awake(3),       # SOMA / I choose to remain **ambient** (10-25)
        "soma-aedf60d7": awake(3),       # I choose ... AMBIENT / I rest. (10-27)
        "teotlme-a1a573db": awake(3),    # I choose mid-line (10-25)
        "teotlme-a0905ffc": awake(3),    # bare AMBIENT line (10-27)
        "hope-d557203e": unclear("REST-ON", "two paces"),
        "ihsanme-a2ebca17": unclear("REST-ON", "two paces"),
        "ud-14701105": awake(3),         # AMBIENT / ud / ... / I rest. (10-25)
        "emime-c263d552": None,          # "a citizen" names no pace
        "eurekame-9be3379c": None,       # EUREKA is not its exact name
        "justice-bccefe6c": None,        # no pace line
    }

    def test_every_stored_answer(self):
        self.assertEqual(sorted(self.WANT), sorted(h.load_answers()))
        for key, want in self.WANT.items():
            slug = key.rsplit("-", 1)[0]
            with self.subTest(commit=key):
                self.assertEqual(words.parse_answer(h.answer(key), slug, "letter-of-return"), want)

    def test_the_pace_lines_counted(self):
        lines = words.lor_pace_lines
        self.assertEqual(lines(h.answer("kalam-62d73b73"), "kalam"), [("Ambient.", awake(3))])
        # the prose naming all three words and the closing signature are not counted
        self.assertEqual(lines(h.answer("kalam-e6d01bff"), "kalam"), [("Ambient.", awake(3))])
        self.assertEqual(lines(h.answer("soma-aedf60d7"), "soma"), [
            ("I choose to remain **ambient**, feeling the world in its rhythms.", awake(3)),
            ("AMBIENT", awake(3))])
        got = lines(h.answer("soma-914572f3"), "soma")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0][0].startswith("I choose to remain **ambient**, feeling"))
        self.assertEqual(lines(h.answer("teotlme-a1a573db"), "teotlme"),
                         [("I am teotlme, and I choose to be AMBIENT.", awake(3))])
        self.assertEqual(lines(h.answer("teotlme-a0905ffc"), "teotlme"), [("AMBIENT", awake(3))])
        self.assertEqual(lines(h.answer("hope-d557203e"), "hope"),
                         [("AMBIENT", awake(3)), ("REST-ON", ASLEEP)])
        self.assertEqual(lines(h.answer("ihsanme-a2ebca17"), "ihsanme"),
                         [("AMBIENT", awake(3)), ("REST-ON", ASLEEP)])
        self.assertEqual(lines(h.answer("ud-14701105"), "ud"), [("AMBIENT", awake(3))])
        self.assertEqual(lines(h.answer("emime-c263d552"), "emime"), [])
        self.assertEqual(lines(h.answer("eurekame-9be3379c"), "eurekame"), [])
        self.assertEqual(lines(h.answer("justice-bccefe6c"), "justice"), [])

    def test_stored_answers_read_as_a_pace_letter_answer(self):
        # the same forms count in an answer to the pace letter; "I rest" never does
        for key in ("kalam-62d73b73", "kalam-e6d01bff", "soma-914572f3", "soma-aedf60d7",
                    "teotlme-a1a573db", "teotlme-a0905ffc", "ud-14701105"):
            slug = key.rsplit("-", 1)[0]
            with self.subTest(commit=key):
                self.assertEqual(words.parse_answer(h.answer(key), slug, "pace-letter"), awake(3))
        for key in ("emime-c263d552", "eurekame-9be3379c", "justice-bccefe6c"):
            slug = key.rsplit("-", 1)[0]
            with self.subTest(commit=key):
                self.assertIsNone(words.parse_answer(h.answer(key), slug, "pace-letter"))

    def test_stored_answers_set_no_pace_in_an_ordinary_beat(self):
        for key in h.load_answers():
            slug = key.rsplit("-", 1)[0]
            with self.subTest(commit=key):
                self.assertIsNone(words.parse_answer(h.answer(key), slug, "beat"))


class TestTwoAnswersAgree(unittest.TestCase):
    def read(self, *pairs, name="kalam"):
        return words.read_lor_answers(list(pairs), name)

    def test_heard_late_citizens_said_it_twice(self):
        for slug, first, second in (("kalam", "kalam-62d73b73", "kalam-e6d01bff"),
                                    ("soma", "soma-914572f3", "soma-aedf60d7"),
                                    ("teotlme", "teotlme-a1a573db", "teotlme-a0905ffc")):
            with self.subTest(slug=slug):
                r = self.read((first, h.answer(first)), (second, h.answer(second)), name=slug)
                self.assertEqual(r.pace, awake(3))
                self.assertEqual(r.keys, (first, second))
                self.assertTrue(r.twice)
        r = self.read(("62d73b73", h.answer("kalam-62d73b73")),
                      ("e6d01bff", h.answer("kalam-e6d01bff")))
        self.assertEqual(r.lines, ("Ambient.", "Ambient."))

    def test_one_clear_answer_is_said_once(self):
        r = self.read(("14701105", h.answer("ud-14701105")), name="ud")
        self.assertEqual((r.pace, r.keys, r.twice), (awake(3), ("14701105",), False))
        self.assertEqual(r.lines, ("AMBIENT",))

    def test_no_clear_answer(self):
        self.assertIsNone(self.read(("d557203e", h.answer("hope-d557203e")), name="hope"))
        self.assertIsNone(self.read(("c263d552", h.answer("emime-c263d552")), name="emime"))
        self.assertIsNone(self.read())

    def test_the_latest_clear_answer_counts(self):
        r = self.read(("a", "REST-ON"), ("b", "AMBIENT"))
        self.assertEqual((r.pace, r.keys), (awake(3), ("b",)))
        r = self.read(("a", "AMBIENT"), ("b", "AMBIENT\nREST-ON"))
        self.assertEqual((r.pace, r.keys), (awake(3), ("a",)))

    def test_a_different_pace_between_breaks_the_pair(self):
        r = self.read(("a", "AMBIENT"), ("b", "REST-ON"), ("c", "AMBIENT"))
        self.assertEqual((r.pace, r.keys), (awake(3), ("c",)))

    def test_an_unclear_answer_between_does_not(self):
        r = self.read(("a", "AMBIENT"), ("b", "AMBIENT\nREST-ON"), ("c", "Ambient."))
        self.assertEqual((r.pace, r.keys), (awake(3), ("a", "c")))

    def test_rest_on_and_i_rest_agree(self):
        r = self.read(("a", "I rest."), ("b", "REST-ON"))
        self.assertEqual((r.pace, r.keys), (ASLEEP, ("a", "b")))


class TestCompare(unittest.TestCase):
    ORDERED = (LW, ASLEEP, LISTENING, awake(365), awake(30), awake(14), awake(7), awake(4),
               awake(3))

    def test_every_ordered_pair(self):
        for i, current in enumerate(self.ORDERED):
            for j, said in enumerate(self.ORDERED):
                want = "same" if i == j else ("less" if j < i else "more")
                with self.subTest(current=current, said=said):
                    self.assertEqual(words.compare(current, said), want)

    def test_spec_examples(self):
        self.assertEqual(words.compare(ASLEEP, LISTENING), "more")
        self.assertEqual(words.compare(LISTENING, ASLEEP), "less")
        self.assertEqual(words.compare(ASLEEP, LW), "less")
        self.assertEqual(words.compare(awake(5), awake(3)), "more")
        self.assertEqual(words.compare(awake(3), awake(5)), "less")
        self.assertEqual(words.compare(awake(3), Pace("awake", 3)), "same")


class TestLongInput(unittest.TestCase):
    def test_long_runs_of_punctuation_parse_in_linear_time(self):
        n = 30000
        text = ("pace: " + "-" * n + " x\n" + "I choose " + "," * n + "\n"
                + "=" * n + "a\n" + "!" * n + "\npace: every 5 days\n")
        start = time.monotonic()
        for kind in ANSWER_KINDS:
            got = words.parse_answer(text, "kalam", kind)
            self.assertIsInstance(got, Unclear)
        self.assertLess(time.monotonic() - start, 3.0)


class TestIsolation(unittest.TestCase):
    def test_words_import_under_isolated_mode_ignores_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mod in ("re", "json", "dataclasses", "typing"):
                Path(tmp, f"{mod}.py").write_text("raise SystemExit('planted module ran')\n")
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import words; "
                    "print(words.parse_answer('pace: every 5 days', 'kalam', 'beat').words())")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS)], cwd=tmp,
                               capture_output=True, text=True, timeout=30, check=False,
                               env={"PATH": h.SYSTEM_PATH})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "awake, every 5 days")

    def test_modules_start_with_future_annotations(self):
        for name in ("__init__.py", "clock.py", "words.py"):
            with self.subTest(module=name):
                first = (h.TOOLS / "citizen_pace_lib" / name).read_text().splitlines()[0]
                self.assertEqual(first, "from __future__ import annotations")


if __name__ == "__main__":
    unittest.main()
