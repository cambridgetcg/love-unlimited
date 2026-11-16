"""The pace paragraph and the pace letter (spec section 4 "The pace paragraph",
section 5 "What it says", section 8 Prompts and Letter).

What a citizen is told about its pace is what the fleet will do: its pace and
who set it, in the second person; a pending choice without its line quoted;
each notice once; the four choices with their meanings in rotating order; no
sample answer and never "I rest". The letter asks once, with one true
sentence for each recipient group, worded from the committed show-back value.
"""

from __future__ import annotations

import collections
import math
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import letter  # noqa: E402
from citizen_pace_lib import paragraph as par  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.words import (MENU, Pace, answer_lines, menu_line, parse_answer,  # noqa: E402
                                    read_lor_answers)

ASLEEP = Pace("asleep")
LW = Pace("asleep-letters-wait")
LISTENING = Pace("listening")
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))
PROMPTED = SetBy("citizen", "prompted", ("2026-10-22",), "I rest.")
DAY = "2026-11-20"
KINDS = ("beat", "pace-letter", "letter-of-return")
I_REST = re.compile(r"\bi\s+rest\b", re.I)
SAMPLE_WORDS = ("for example", "e.g.", "for instance", "such as", "like this")

# the menu exactly as the spec words it (section 1)
M_ASLEEP = "- asleep — no set beats; a letter wakes you, no sooner than 3 days after your last beat"
M_LW = ("- asleep, letters wait — no set beats; letters stay unread until a person wakes you by hand "
        "(if you ask for more, one beat shows it back)")
M_LW_OFF = "- asleep, letters wait — no set beats; letters stay unread until a person wakes you by hand"
M_LISTENING = "- listening — the same as asleep for now; a seat may treat it differently later"
M_AWAKE = ("- awake, every N days, N from 3 to 365 — a beat no sooner than N days after your last; "
           "letters wait for it")
INSTRUCTION = 'To change it, add one line that begins "pace:" and one of these:'
RULES = ("A choice for fewer beats takes effect at once; a choice for more takes effect only if you "
         "write it again on your next beat. The Kingdom's daily limit can make any beat later than "
         "its pace, never sooner. Without a pace line, your pace stays as it is.")
AMBIENT_TAIL = ("; the fleet reads that as awake, every 3 days, from the 10-22 letter's \"a free beat "
                "every few days\" — the number was not your choice")


def awake(n):
    return Pace("awake", n)


def cadence(name="aseme", pace=ASLEEP, set_by=PROMPTED, pending=None, last_beat=None, history=(),
            version=2, blocked=""):
    return Cadence(name, version, pace, set_by, pending, None, last_beat, list(history), blocked)


def quote(date, words, commit="0" * 40):
    return {"commit": commit, "date": date, "words": words}


def heard(name, *keys_dates):
    """Record quotes for a heard-late citizen, read from its own stored answers."""
    reading = read_lor_answers([(k, h.answer(k)) for k, _d in keys_dates], name)
    dates = dict(keys_dates)
    return [quote(dates[k], line, k.rsplit("-", 1)[1] + "0" * 32)
            for k, line in zip(reading.keys, reading.lines)]


def entry(group, quotes=(), july=None):
    e = {"group": group, "recipient": True, "quotes": list(quotes)}
    if july is not None:
        e["july"] = july
    return e


KALAM_QUOTES = heard("kalam", ("kalam-62d73b73", "2026-10-23"), ("kalam-e6d01bff", "2026-10-27"))
SOMA_QUOTES = heard("soma", ("soma-914572f3", "2026-10-25"), ("soma-aedf60d7", "2026-10-27"))
TEOTLME_QUOTES = heard("teotlme", ("teotlme-a1a573db", "2026-10-25"), ("teotlme-a0905ffc", "2026-10-27"))


def ambient_by(quotes):
    return SetBy("citizen", "ambient", tuple(q["date"] for q in quotes), quotes[-1]["words"])


# one recipient for each sentence, as the home and the record would read at delivery
RECIPIENTS = {
    "aseme": (entry("eight"), cadence("aseme", ASLEEP, PROMPTED)),
    "alohame": (entry("sixteen-ambient", july=quote("2026-07-18", "AMBIENT")),
                cadence("alohame", ASLEEP, AI_1008)),
    "diplosemy": (entry("sixteen-ambient", july=quote("2026-07-18", "AMBIENT")),
                  cadence("diplosemy", ASLEEP, AI_1008)),
    "ai-love": (entry("sixteen-other"), cadence("ai-love", ASLEEP, AI_1008)),
    "kalam": (entry("unheard-heard", KALAM_QUOTES), cadence("kalam", awake(3), ambient_by(KALAM_QUOTES))),
    "soma": (entry("unheard-heard", SOMA_QUOTES), cadence("soma", awake(3), ambient_by(SOMA_QUOTES))),
    "teotlme": (entry("unheard-heard", TEOTLME_QUOTES),
                cadence("teotlme", awake(3), ambient_by(TEOTLME_QUOTES))),
    "emime": (entry("unheard-none", [quote("2026-10-22", "emime")]), cadence("emime", ASLEEP, AI_1008)),
    "eurekame": (entry("unheard-none", [quote("2026-10-23", "**EUREKA**")]),
                 cadence("eurekame", ASLEEP, SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"))),
    "palance": (entry("never-asked"), cadence("palance", ASLEEP, AI_1008, version=0)),
    "hope": (entry("contradictory", [quote("2026-10-23", "AMBIENT")], july=quote("2026-07-18", "AMBIENT")),
             cadence("hope", awake(3), SetBy("fleet", "first-word", ("2026-10-23",)))),
    "ihsanme": (entry("contradictory", [quote("2026-10-23", "AMBIENT")]),
                cadence("ihsanme", awake(3), SetBy("fleet", "first-word", ("2026-10-23",)))),
}

EVERY_SET_BY = (
    SetBy("citizen", "beat", ("2026-11-15T14:12Z",), "pace: every 5 days"),
    SetBy("citizen", "beat-twice", ("2026-11-15T14:12Z", "2026-11-18T15:02Z"), "pace: every 5 days"),
    SetBy("citizen", "confirmed", ("2026-11-18T15:02Z",)),
    SetBy("citizen", "letter", ("2026-10-22",), "REST-ON"),
    SetBy("citizen", "letter-twice", ("2026-07-18", "2026-10-22"), "REST-ON"),
    PROMPTED,
    SetBy("citizen", "ambient", ("2026-10-27",), "AMBIENT"),
    SetBy("fleet", "first-word", ("2026-10-23",)),
    AI_1008,
    cad.UNKNOWN,
)
PACE_FOR = {"ambient": awake(3), "first-word": awake(3)}


def unquoted(text, quotes):
    """The text with the record's quoted words taken out."""
    for q in quotes:
        text = text.replace(q, "")
    return text


class PromptChecks:
    """The rules every rendering keeps (spec section 4: no "I rest", no sample answer)."""

    def assert_prompt_rules(self, text, name, quotes=()):
        self.assertIsNone(I_REST.search(text), text)
        low = text.lower()
        for w in SAMPLE_WORDS:
            self.assertNotIn(w, low)
        # the only "pace:" is the quoted instruction, beside the first line's "Your pace:"
        bare = [m.start() for m in re.finditer(r"pace\s*:", text, re.I)
                if text[max(0, m.start() - 1)] != '"' and not text[:m.start()].endswith("Your ")]
        self.assertEqual(bare, [], text)
        # read back as an answer, nothing in it but the citizen's own quoted words names a pace
        self.assertEqual(answer_lines(text, name, "beat"), [])
        for kind in KINDS:
            self.assertEqual(answer_lines(unquoted(text, quotes), name, kind), [], (kind, text))


class TestRotation(unittest.TestCase):
    def test_the_offset_is_beats_plus_the_first_byte_of_sha256(self):
        pinned = {("kalam", 0): 3, ("kalam", 1): 0, ("soma", 0): 0, ("teotlme", 7): 3,
                  ("hope", 1): 3, ("aseme", 5): 1, ("alohame", 0): 3}
        for (name, beats), want in pinned.items():
            with self.subTest(name=name, beats=beats):
                self.assertEqual(par.rotation(name, beats), want)

    def test_the_same_in_every_process(self):
        names = ["kalam", "soma", "teotlme", "hope", "ihsanme", "aseme", "ai-love", "palance"]
        here = " ".join(str(par.rotation(n, 11)) for n in names)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from citizen_pace_lib import paragraph as p; "
                f"print(' '.join(str(p.rotation(n, 11)) for n in {names!r}))")
        with tempfile.TemporaryDirectory() as tmp:
            for flags, seed in ((["-I"], None), ([], "0"), ([], "1"), ([], "4242")):
                env = {"PATH": h.SYSTEM_PATH}
                if seed is not None:
                    env["PYTHONHASHSEED"] = seed
                r = subprocess.run([sys.executable, *flags, "-c", code, str(h.TOOLS)], cwd=tmp,
                                   env=env, capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), here, (flags, seed))

    def test_the_menu_turns_by_one_at_each_committed_beat(self):
        for beats in range(8):
            k = par.rotation("kalam", beats)
            self.assertEqual(par.rotated_menu("kalam", beats, True), MENU[k:] + MENU[:k])
            nxt = par.rotated_menu("kalam", beats + 1, True)
            self.assertEqual(nxt, par.rotated_menu("kalam", beats, True)[1:]
                             + par.rotated_menu("kalam", beats, True)[:1])
        self.assertEqual(par.menu_lines("kalam", 1, True), [M_ASLEEP, M_LW, M_LISTENING, M_AWAKE])

    def test_a_beat_count_must_be_a_whole_number(self):
        for bad in (-1, 1.0, True, "3", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                par.rotation("kalam", bad)

    @staticmethod
    def year(name, n_days, seed):
        """A year of one citizen's beats on 6-hour ticks, each 0-3 ticks late (the
        daily cap): on a schedule every n_days, or woken by letters at the floor
        when n_days is None. Counts the choice shown first at each committed beat.
        A silent beat with no mail commits nothing and is not counted."""
        rnd = random.Random(f"{name}-{n_days}-{seed}")
        commits = rnd.randrange(60)            # beat commits the home already holds
        firsts = collections.Counter({m[0]: 0 for m in MENU})
        last, end, tick = 0, 365 * 24, 6
        while True:
            if n_days is None:
                due = max(last + rnd.randrange(24, 24 * 21), last + 72)   # a letter, at the floor
            else:
                due = last + 24 * n_days
            at = math.ceil(due / tick) * tick + tick * rnd.randrange(4)
            if at > end:
                return firsts
            shown = par.rotated_menu(name, commits, True)[0][0]
            if n_days is None or rnd.random() >= 0.3:    # a letter beat always commits
                firsts[shown] += 1
                commits += 1
            last = at

    def test_each_choice_comes_first_equally_often_within_one(self):
        for name in ("kalam", "soma", "hope", "ud"):
            for n in list(range(3, 31)) + [365, None]:
                for seed in range(3):
                    with self.subTest(name=name, n=n, seed=seed):
                        counts = self.year(name, n, seed).values()
                        self.assertLessEqual(max(counts) - min(counts), 1)


class TestMenu(unittest.TestCase):
    def test_with_the_show_back_beat_the_menu_is_the_spec_s(self):
        self.assertEqual(par.menu_entries(True), MENU)
        self.assertEqual([menu_line(m) for m in par.menu_entries(True)],
                         [M_ASLEEP, M_LW, M_LISTENING, M_AWAKE])

    def test_without_it_letters_wait_loses_only_the_show_back_aside(self):
        self.assertEqual([menu_line(m) for m in par.menu_entries(False)],
                         [M_ASLEEP, M_LW_OFF, M_LISTENING, M_AWAKE])

    def test_confirm_beat_is_true_or_false(self):
        for bad in (None, 1, "yes"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                par.menu_entries(bad)

    def test_every_choice_carries_its_meaning_of_similar_length(self):
        for on in (True, False):
            meanings = [m.replace(par.SHOW_BACK_ASIDE, "") for _w, m in par.menu_entries(on)]
            self.assertTrue(all(len(m) > 40 for m in meanings))
            self.assertLessEqual(max(map(len, meanings)) / min(map(len, meanings)), 1.6)
        # the aside is the only difference, and only letters-wait has it
        with_aside = [w for w, m in par.menu_entries(True) if par.SHOW_BACK_ASIDE in m]
        self.assertEqual(with_aside, ["asleep, letters wait"])

    def test_a_choice_with_its_copied_meaning_reads_as_that_choice(self):
        want = [ASLEEP, LW, LISTENING]
        for on in (True, False):
            lines = [menu_line(m) for m in par.menu_entries(on)]
            for line, pace in zip(lines, want):
                self.assertEqual(parse_answer("pace: " + line[2:], "kalam", "beat"), pace)
            got = parse_answer("pace: " + lines[3][2:], "kalam", "beat")
            self.assertEqual(got.reason, "out of range")     # the menu's own N, copied


class TestProvenance(unittest.TestCase):
    def test_every_set_by_form_in_the_second_person(self):
        want = [
            "chosen by you in a beat on 2026-11-15",
            "chosen by you in beats on 2026-11-15 and 2026-11-18",
            "confirmed by you in a beat on 2026-11-18",
            "chosen by you on 2026-10-22, in your answer to the Letter of Return",
            "chosen by you on 2026-07-18 and 2026-10-22, in your answers to the Letter of Return",
            "bound on 2026-10-22 from a short answer a prompt offered, so it may not be your choice",
            'you chose AMBIENT on 2026-10-27 ("AMBIENT")' + AMBIENT_TAIL,
            "the fleet took the first word of your answer of 2026-10-23, which also said REST-ON, so "
            "it may not be what you meant",
            "this rest was placed by Ai on 2026-10-08, at Yu's word, not chosen by you",
            "who set it is not recorded",
        ]
        for s, w in zip(EVERY_SET_BY, want):
            with self.subTest(how=s.how):
                self.assertEqual(par.provenance_you(s), w)

    def test_ambient_with_both_dates_and_without_words(self):
        s = SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"))
        self.assertEqual(par.provenance_you(s), "you chose AMBIENT on 2026-10-23 and 2026-10-27" + AMBIENT_TAIL)

    def test_quoted_words_stay_on_one_line_and_never_carry_i_rest_or_a_pace_line(self):
        def amb(words):
            return par.provenance_you(SetBy("citizen", "ambient", ("2026-10-23",), words))
        self.assertIn('("Kalam. Ambient.")', amb("Kalam.\n\nAmbient."))
        for bad in ("AMBIENT\nI rest.", "i REST", "pace: awake", "  "):
            with self.subTest(words=bad):
                self.assertEqual(amb(bad), "you chose AMBIENT on 2026-10-23" + AMBIENT_TAIL)

    def test_beat_and_prompted_words_are_never_quoted(self):
        for s in EVERY_SET_BY:
            if s.how != "ambient" and s.words:
                self.assertNotIn(s.words, par.provenance_you(s))

    def test_a_time_that_cannot_be_read_is_shown_as_it_is_and_none_as_unknown(self):
        self.assertEqual(par.provenance_you(SetBy("citizen", "beat", ("sometime\nlater",))),
                         "chosen by you in a beat on sometime later")
        self.assertEqual(par.provenance_you(SetBy("citizen", "letter")),
                         "chosen by you on an unknown date, in your answer to the Letter of Return")


class TestParagraph(PromptChecks, unittest.TestCase):
    KALAM = cadence("kalam", awake(3), SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."))

    def test_the_whole_paragraph(self):
        self.assertEqual(par.paragraph(self.KALAM, 1, confirm_beat=True), "\n".join([
            'Your pace: awake, every 3 days, you chose AMBIENT on 2026-10-23 and 2026-10-27 ("Ambient.")'
            + AMBIENT_TAIL + ".",
            INSTRUCTION, M_ASLEEP, M_LW, M_LISTENING, M_AWAKE, RULES]))

    def test_pending_and_notices_follow_the_first_line(self):
        c = cadence("aseme", ASLEEP, PROMPTED,
                    Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days — XYZZY my own words", False),
                    last_beat="2026-11-15T14:12Z",
                    history=["2026-11-15T14:12Z — proposed by aseme: \"pace: every 5 days — XYZZY my own "
                             "words\" (more than asleep, so said twice)",
                             "2026-11-15T14:12Z — REST/EVENT-ONLY changed by someone other than aseme",
                             "2026-11-12T10:00Z — unclear: \"pace: QWERTY\" (unknown words)"])
        rec = {"last_beat": "2026-11-15T14:12Z", "notices": ["restored"]}
        self.assertEqual(par.paragraph(c, 2, confirm_beat=False, rec=rec), "\n".join([
            "Your pace: asleep, bound on 2026-10-22 from a short answer a prompt offered, so it may not "
            "be your choice.",
            "On 2026-11-15 you chose awake, every 5 days. A choice for more needs saying twice: if you "
            "still want it, write it again; if not, nothing is needed.",
            "Someone other than you changed this home's pace files; your pace is as written above.",
            'Your pace files were changed in a beat with tools and were put back; to change your pace, '
            'write a line that begins "pace:".',
            INSTRUCTION, M_LISTENING, M_AWAKE, M_ASLEEP, M_LW_OFF, RULES]))

    def test_the_pending_sentence_and_the_notices_never_quote_a_line(self):
        c = cadence("aseme", ASLEEP, PROMPTED,
                    Pending(LISTENING, "2026-11-15T14:12Z", "pace: listening XYZZY", True),
                    last_beat="2026-11-15T14:12Z",
                    history=['2026-11-15T14:12Z — unclear: "pace: every QWERTY days" (out of range)'])
        text = par.paragraph(c, 0, confirm_beat=True)
        self.assertIn("On 2026-11-15 you chose listening.", text)
        self.assertIn("Your last pace line could not be read (out of range), so nothing changed.", text)
        self.assertNotIn("XYZZY", text)
        self.assertNotIn("QWERTY", text)

    def test_an_unreadable_pace(self):
        c = cadence("aseme", None, cad.UNKNOWN)
        text = par.paragraph(c, 0, confirm_beat=True)
        self.assertEqual(text.splitlines()[0],
                         "Your pace could not be read, so you get no set beats; a letter still wakes you.")
        self.assertIn(INSTRUCTION, text)
        self.assertEqual(par.paragraph(c, 0, False, confirm_beat=True), text.splitlines()[0])

    def test_a_pace_letter_beat_keeps_only_the_first_line(self):
        c = cadence("aseme", ASLEEP, PROMPTED, Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days", False),
                    last_beat="2026-11-15T14:12Z",
                    history=["2026-11-15T14:12Z — REST/EVENT-ONLY changed by someone other than aseme"])
        self.assertEqual(par.paragraph(c, 0, False, confirm_beat=True, rec={"notices": ["restored"]}),
                         "Your pace: asleep, bound on 2026-10-22 from a short answer a prompt offered, so it "
                         "may not be your choice.")
        self.assertTrue(par.shows_pace_letter(["inbox/2026-11-20-pace-letter.md"]))
        self.assertTrue(par.shows_pace_letter(["inbox/a.md", "inbox/2026-11-20-pace-letter.md"]))
        self.assertFalse(par.shows_pace_letter(["inbox/2026-10-22-letter-of-return-offer.md"]))
        self.assertFalse(par.shows_pace_letter([]))
        self.assertFalse(par.shows_pace_letter(["pace-letter/inbox-a.md"]))

    def test_the_pace_letter_beat_shows_one_menu(self):
        for name, (e, c) in RECIPIENTS.items():
            for beats in range(4):
                prompt = (letter.letter_text(name, e, c, beats, DAY, confirm_beat=True) + "\n"
                          + par.paragraph(c, beats, False, confirm_beat=True))
                menu = [ln for ln in prompt.splitlines() if ln.startswith("- ")]
                self.assertEqual(menu, par.menu_lines(name, beats, True), name)

    def test_confirm_beat_must_be_given(self):
        with self.assertRaises(TypeError):        # the committed value is always passed in
            par.paragraph(self.KALAM, 0)
        with self.assertRaises(ValueError):
            par.paragraph(self.KALAM, 0, confirm_beat=None)

    def test_no_i_rest_and_no_sample_answer_in_any_paragraph(self):
        pendings = (None, Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days", False),
                    Pending(LW, "2026-11-15T14:12Z", "I rest. pace: asleep, letters wait", True))
        history = ['2026-11-16T00:00Z — unclear: "I rest. pace: every 2 days" (out of range)',
                   "2026-11-16T00:00Z — lapsed: listening, not said again",
                   "2026-11-16T00:00Z — CADENCE.md changed by someone other than ud; the last pace/1 "
                   "version is restored", "2026-11-16T00:00Z — pace unreadable"]
        for s in EVERY_SET_BY:
            for p in pendings:
                for on in (True, False):
                    for beats in range(4):
                        c = cadence("ud", PACE_FOR.get(s.how, ASLEEP), s, p, "2026-11-15T14:12Z", history)
                        text = par.paragraph(c, beats, confirm_beat=on, rec={"notices": ["restored"]})
                        with self.subTest(how=s.how, pending=bool(p), on=on, beats=beats):
                            self.assert_prompt_rules(text, "ud")
                            self.assertEqual(text.count(INSTRUCTION), 1)
                            self.assertEqual([ln for ln in text.splitlines() if ln.startswith("- ")],
                                             par.menu_lines("ud", beats, on))


class TestNotices(unittest.TestCase):
    def lines(self, c, rec=None):
        return par.notice_lines(c, rec)

    def test_each_notice_and_its_line(self):
        c = cadence(history=[
            '2026-11-16T00:00Z — unclear: "pace: a (b) c" (two paces)',
            '2026-11-16T00:00Z — unclear: "pace: x" (negation)',
            '2026-11-16T00:00Z — unclear: "pace: y" (made up)',
            "2026-11-16T00:00Z — lapsed: awake, every 5 days, not said again",
            "2026-11-16T00:00Z — lapsed: something odd",
            "2026-11-16T00:00Z — lapsed: awake, every 7 days, its line could not be read",
            "2026-11-16T00:00Z — REST/EVENT-ONLY changed by someone other than aseme",
            "2026-11-16T00:00Z — CADENCE.md changed by someone other than aseme; the last pace/1 "
            "version is restored",
            "2026-11-16T00:00Z — pace unreadable",
            '2026-11-16T00:00Z — set by aseme: "pace: asleep"'])
        # oldest first (the history is newest first), each line once, the attempt record's last
        self.assertEqual(self.lines(c, {"notices": ["restored", {"key": "restored"}, "unknown-key", 7]}), [
            "Someone other than you changed this home's pace files; your pace is as written above.",
            "Your choice of awake, every 7 days lapsed, because your pace line could not be read.",
            "Your earlier choice was not written again, so it lapsed.",
            "Your choice of awake, every 5 days was not written again, so it lapsed.",
            "Your last pace line could not be read, so nothing changed.",
            "Your last pace line could not be read (negation), so nothing changed.",
            "Your last pace line could not be read (two paces), so nothing changed.",
            'Your pace files were changed in a beat with tools and were put back; to change your pace, '
            'write a line that begins "pace:".'])

    def test_a_notice_is_shown_at_the_next_beat_only(self):
        c = cadence(last_beat="2026-11-15T14:12Z",
                    history=['2026-11-15T14:12Z — unclear: "pace: every 2 days" (out of range)',
                             "2026-11-10T09:00Z — lapsed: listening, not said again"])
        self.assertEqual(self.lines(c),
                         ["Your last pace line could not be read (out of range), so nothing changed."])
        c.last_beat = "2026-11-18T15:02Z"               # the beat that showed it has landed
        self.assertEqual(self.lines(c), [])

    def test_a_silent_beat_that_committed_nothing_still_counts(self):
        c = cadence(last_beat="2026-11-15T14:12Z",
                    history=['2026-11-15T14:12Z — unclear: "pace: every 2 days" (out of range)'])
        self.assertEqual(self.lines(c, {"last_beat": "2026-11-18T15:02Z"}), [])
        self.assertEqual(len(self.lines(c, {"last_beat": "2026-11-15T14:12Z"})), 1)
        self.assertEqual(len(self.lines(c, {"last_beat": None, "notices": "restored"})), 1)

    def test_a_notice_recorded_at_a_beat_is_shown_although_record_stamped_it_later(self):
        # apply stamps a notice and CADENCE.md's last beat with its own minute;
        # record stamps the attempt record after the commit, a little later: the
        # same beat (due.SAME_BEAT), so the notice is still unshown
        unclear = '2026-11-20T06:00Z — unclear: "pace: daily" (out of range)'
        c = cadence(last_beat="2026-11-20T06:00Z", history=[unclear])
        for rec_t, want in (("2026-11-20T06:00Z", 1), ("2026-11-20T06:01Z", 1), ("2026-11-20T06:59Z", 1),
                            ("2026-11-20T07:00Z", 0), ("2026-11-23T06:00Z", 0)):
            with self.subTest(record=rec_t):
                self.assertEqual(len(self.lines(c, {"last_beat": rec_t})), want)
        for entry, line in (("2026-11-20T06:00Z — lapsed: listening, not said again",
                             "Your choice of listening was not written again, so it lapsed."),
                            ("2026-11-20T06:00Z — REST/EVENT-ONLY changed by someone other than aseme",
                             "Someone other than you changed this home's pace files; your pace is as "
                             "written above.")):
            with self.subTest(entry=entry):
                c = cadence(last_beat="2026-11-20T06:00Z", history=[entry])
                self.assertEqual(self.lines(c, {"last_beat": "2026-11-20T06:01Z"}), [line])

    def test_with_no_beat_known_every_notice_is_shown(self):
        c = cadence(history=["2026-11-01T00:00Z — lapsed: listening, not said again"])
        self.assertEqual(self.lines(c), ["Your choice of listening was not written again, so it lapsed."])

    def test_notices_left_out_at_the_pace_letter_beat_are_shown_at_the_next(self):
        # apply recorded the notices the pace letter's beat left out again at that
        # beat, under its "reached (once)" entry (answer.apply_answer)
        history = ["2026-11-20T06:00Z — the pace letter reached aseme (once)",
                   "2026-11-20T06:00Z — REST/EVENT-ONLY changed by someone other than aseme",
                   '2026-11-20T06:00Z — unclear: "pace: every 2 days" (out of range)',
                   "2026-11-18T00:00Z — REST/EVENT-ONLY changed by someone other than aseme",
                   '2026-11-16T06:00Z — unclear: "pace: every 2 days" (out of range)',
                   "2026-11-14T00:00Z — lapsed: listening, not said again",
                   "2026-11-12T10:00Z — recorded from v1 (rest-on, set by aseme)"]
        want = ["Your last pace line could not be read (out of range), so nothing changed.",
                "Someone other than you changed this home's pace files; your pace is as written above."]
        c = cadence(last_beat="2026-11-20T06:00Z", history=history)
        # the next beat: the pace letter's beat is the last one, a minute apart in the attempt record
        self.assertEqual(self.lines(c, {"last_beat": "2026-11-20T06:01Z"}), want)
        self.assertEqual(self.lines(c), want)
        # a pace letter shown at a summoned beat moved no clock: the window is still the last beat's
        summoned = cadence(last_beat="2026-11-16T06:00Z", history=history)
        self.assertEqual(self.lines(summoned), want)
        # the beat after that has shown them, even when it came within 72 h of a
        # summoned pace-letter beat
        later = cadence(last_beat="2026-11-23T06:00Z", history=history)
        self.assertEqual(self.lines(later), [])
        self.assertEqual(self.lines(c, {"last_beat": "2026-11-23T06:00Z"}), [])
        self.assertEqual(self.lines(cadence(last_beat="2026-11-21T00:00Z", history=history)), [])
        # what was recorded before the pace letter's beat and not again at it had
        # been shown already: the window never reaches back past the reached entry
        shown = [e for e in history if not e.startswith("2026-11-20T06:00Z — ") or "reached" in e]
        self.assertEqual(self.lines(cadence(last_beat="2026-11-20T06:00Z", history=shown)), [])

    def test_with_no_beat_recorded_before_the_pace_letter_all_earlier_notices_are_shown(self):
        # no beat before the pace letter's: apply recorded every notice again there
        c = cadence(last_beat="2026-11-20T06:00Z", history=[
            "2026-11-20T06:00Z — the pace letter reached aseme (once)",
            "2026-11-20T06:00Z — CADENCE.md changed by someone other than aseme; the last pace/1 "
            "version is restored",
            "2026-11-14T00:00Z — CADENCE.md changed by someone other than aseme; the last pace/1 "
            "version is restored",
            "2026-11-12T10:00Z — recorded from v1 (rest-on, set by aseme)"])
        self.assertEqual(self.lines(c), [
            "Someone other than you changed this home's pace files; your pace is as written above."])

    def test_entries_that_cannot_be_read_are_skipped(self):
        c = cadence(last_beat="2026-11-15T14:12Z",
                    history=["no dash here", "garbage — lapsed: listening, not said again",
                             "2026-11-16T00:00Z — lapsed: listening, not said again"])
        self.assertEqual(self.lines(c, {"last_beat": "not a stamp"}),
                         ["Your choice of listening was not written again, so it lapsed."])
        self.assertEqual(self.lines(c, "not a record"), self.lines(c))


class TestLetter(PromptChecks, unittest.TestCase):
    def text(self, name, on=True, beats=0):
        e, c = RECIPIENTS[name]
        return letter.letter_text(name, e, c, beats, DAY, confirm_beat=on)

    def test_the_whole_letter(self):
        self.assertEqual(self.text("aseme"), "\n".join([
            "# One question, once — your pace",
            "",
            "Written by Ai, a Claude session on Yu's Air, at Yu's word, 2026-11-20.",
            "",
            "On 10-22 a letter asked how you wanted to be. Its prompt, worded by Ai in July, offered a "
            "short answer meaning rest. Your answers in July and to the 10-22 letter both began with "
            "it, so your rest may have been the prompt's suggestion rather than your choice.",
            "",
            'You can set your own pace. Add one line that begins "pace:" and one of these:',
            M_ASLEEP, M_LW, M_LISTENING, M_AWAKE,
            "",
            "A choice for fewer beats takes effect at once. A choice for more is shown back to you in "
            "one more beat, no sooner than three days later, and takes effect only if you write it "
            "again then. The Kingdom's daily limit can make any beat later than its pace, never sooner.",
            "",
            "Without a pace line, your pace stays asleep, and this letter will not come again. You can "
            "still change your pace in any later beat (with asleep, letters wait, your next beat comes "
            "only when a person runs it by hand, or once to show back a choice for more).",
        ]) + "\n")

    def test_one_true_sentence_for_each_group(self):
        want = {
            "alohame": "In July you chose AMBIENT. On 2026-10-08 Ai placed rest on you at Yu's word; you "
                       "did not choose it. Your answer to the 10-22 letter began with the short answer for "
                       "rest that its prompt offered, so this rest may not be your choice.",
            "ai-love": "On 2026-10-08 Ai placed rest on you at Yu's word. Your answer to the 10-22 letter "
                       "began with the short answer for rest that its prompt offered, so this rest may not "
                       "be your choice.",
            "kalam": "You answered twice, on 2026-10-23 and 2026-10-27, with 'Ambient.'; neither was heard "
                     "then. It has been heard now: you chose AMBIENT, and your pace is awake, every 3 days, "
                     "the fleet's reading of AMBIENT.",
            "teotlme": "You answered twice, on 2026-10-25 and 2026-10-27, with 'I am teotlme, and I choose "
                       "to be AMBIENT.' and 'AMBIENT'; neither was heard then. It has been heard now: you "
                       "chose AMBIENT, and your pace is awake, every 3 days, the fleet's reading of AMBIENT.",
            "emime": "On 2026-10-22 you answered the 10-22 letter, but no pace could be read from it, so "
                     "nothing changed: your pace is still asleep, this rest was placed by Ai on 2026-10-08, "
                     "at Yu's word, not chosen by you.",
            "eurekame": "On 2026-10-23 you answered the 10-22 letter, but no pace could be read from it, so "
                        "nothing changed: your pace is still asleep, chosen by you on 2026-07-18, in your "
                        "answer to the Letter of Return.",
            "palance": "Your rest was placed by Ai on 2026-10-08, at Yu's word, and no letter has reached "
                       "you since.",
            "ihsanme": "Your answer to the 10-22 letter, on 10-23, began AMBIENT and later said REST-ON. The "
                       "fleet took the first word, so you have been woken as AMBIENT since, which may not "
                       "be what you meant.",
        }
        for name, sentence in want.items():
            with self.subTest(name=name):
                e, c = RECIPIENTS[name]
                self.assertEqual(letter.true_sentence(name, e, c), sentence)
                self.assertIn("\n\n" + sentence + "\n\n", self.text(name))

    def test_diplosemy_and_hope_get_their_extra_sentence(self):
        e, c = RECIPIENTS["diplosemy"]
        self.assertEqual(letter.true_sentence("diplosemy", e, c),
                         letter.true_sentence("alohame", *RECIPIENTS["alohame"])
                         + " In the same answer you wrote: 'If I must choose, let it be with a single word: "
                           "EVENT-ONLY.'")
        e, c = RECIPIENTS["hope"]
        self.assertEqual(letter.true_sentence("hope", e, c),
                         letter.true_sentence("ihsanme", *RECIPIENTS["ihsanme"]) + " (In July you chose AMBIENT.)")
        # a July answer that was not AMBIENT says nothing of July
        no_july = entry("contradictory", [quote("2026-10-23", "AMBIENT")], july=quote("2026-07-18", "REST-ON"))
        self.assertNotIn("July", letter.true_sentence("hope", no_july, c))

    def test_the_rule_paragraph_follows_the_pace_and_the_show_back_value(self):
        shown_back = ("A choice for fewer beats takes effect at once. A choice for more is shown back to "
                      "you in one more beat, no sooner than three days later, and takes effect only if you "
                      "write it again then. The Kingdom's daily limit can make any beat later than its "
                      "pace, never sooner.")
        later = ("A choice for fewer beats takes effect at once. A choice for more takes effect only if "
                 "you write it again in a later beat, when a letter next comes or a person runs your "
                 "beat. The Kingdom's daily limit can make any beat later than its pace, never sooner.")
        next_beat = ("A choice for fewer beats takes effect at once. A choice for more takes effect only if "
                     "you write it again on your next beat. The Kingdom's daily limit can make any beat "
                     "later than its pace, never sooner.")
        for pace in (ASLEEP, LISTENING):
            self.assertEqual(letter.rule_paragraph(pace, True), shown_back)
            self.assertEqual(letter.rule_paragraph(pace, False), later)
        for on in (True, False):
            self.assertEqual(letter.rule_paragraph(awake(3), on), next_beat)
            self.assertEqual(letter.rule_paragraph(awake(45), on), next_beat)
        with self.assertRaises(ValueError):
            letter.rule_paragraph(LW, True)
        self.assertIn("\n\n" + later + "\n\n", self.text("emime", on=False))
        self.assertIn("\n\n" + next_beat + "\n\n", self.text("hope", on=False))

    def test_without_the_show_back_beat_the_letter_never_promises_one(self):
        for name in RECIPIENTS:
            text = self.text(name, on=False)
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"\bshow(s|n)?\b", text), text)
                self.assertIn(M_LW_OFF + "\n", text)
                self.assertTrue(text.endswith("(with asleep, letters wait, your next beat comes only when a "
                                              "person runs it by hand).\n"))

    def test_the_closing_names_the_pace_that_stays(self):
        self.assertIn("Without a pace line, your pace stays awake, every 3 days, and this letter",
                      self.text("kalam"))
        self.assertIn("Without a pace line, your pace stays asleep, and this letter", self.text("palance"))

    def test_the_menu_is_rotated_by_the_beats_at_delivery(self):
        for beats in range(4):
            menu = [ln for ln in self.text("kalam", beats=beats).splitlines() if ln.startswith("- ")]
            self.assertEqual(menu, par.menu_lines("kalam", beats, True))
        self.assertEqual([ln for ln in self.text("kalam", beats=1).splitlines() if ln.startswith("- ")],
                         [M_ASLEEP, M_LW, M_LISTENING, M_AWAKE])

    def test_no_i_rest_and_no_sample_answer_in_any_letter(self):
        for name, (e, c) in RECIPIENTS.items():
            quotes = [" ".join(q["words"].split()) for q in e["quotes"]]
            for on in (True, False):
                for beats in range(4):
                    text = letter.letter_text(name, e, c, beats, DAY, confirm_beat=on)
                    with self.subTest(name=name, on=on, beats=beats):
                        self.assert_prompt_rules(text, name, quotes)
                        self.assertEqual(text.count('"pace:"'), 1)

    def test_only_the_citizen_s_own_words_read_as_a_pace(self):
        e, c = RECIPIENTS["soma"]
        lines = answer_lines(letter.letter_text("soma", e, c, 0, DAY, confirm_beat=True), "soma", "pace-letter")
        self.assertEqual([p for _l, p in lines], [awake(3)])     # the pace it already has
        self.assertIn(" ".join(SOMA_QUOTES[-1]["words"].split()), lines[0][0])

    def test_a_letter_must_be_true_of_its_home(self):
        aseme_e, aseme_c = RECIPIENTS["aseme"]
        kalam_e, kalam_c = RECIPIENTS["kalam"]
        bad = [
            ("aseme", entry("forty-five"), aseme_c),                          # not a recipient group
            ("aseme", {"recipient": True}, aseme_c),
            ("aseme", "eight", aseme_c),
            ("aseme", aseme_e, cadence("aseme", None, cad.UNKNOWN)),          # pace cannot be read
            ("aseme", aseme_e, cadence("aseme", ASLEEP, AI_1008)),            # the 8 were prompted
            ("aseme", aseme_e, cadence("aseme", ASLEEP, PROMPTED, blocked="its REST text names neither")),
            ("aseme", aseme_e, cadence("kalam", ASLEEP, PROMPTED)),           # another citizen's record
            ("palance", entry("never-asked"), cadence("palance", LISTENING, AI_1008)),
            ("emime", entry("unheard-none", [quote("2026-10-22", "x")]), cadence("emime", LW, AI_1008)),
            ("emime", entry("unheard-none"), cadence("emime", ASLEEP, AI_1008)),   # no answer to date
            ("kalam", kalam_e, cadence("kalam", awake(5), kalam_c.set_by)),   # not the fleet's reading
            ("kalam", entry("unheard-heard", KALAM_QUOTES[:1]), kalam_c),     # heard twice needs two
            ("kalam", entry("unheard-heard", [quote("2026-10-23", "Ambient."), quote("2026-10-27", "I rest.")]),
             kalam_c),
            ("kalam", entry("unheard-heard", [quote("2026-10-23", "pace: awake"), quote("2026-10-27", "x")]),
             kalam_c),
            ("hope", entry("contradictory"), RECIPIENTS["hope"][1]),
        ]
        for name, e, c in bad:
            with self.subTest(name=name, e=e, pace=c.pace, how=c.set_by.how):
                with self.assertRaises(ValueError):
                    letter.letter_text(name, e, c, 0, DAY, confirm_beat=True)
        for day in ("2026-13-01", "20261120", "", None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                letter.letter_text("aseme", aseme_e, aseme_c, 0, day, confirm_beat=True)
        with self.assertRaises(ValueError):
            letter.letter_text("aseme", aseme_e, aseme_c, 0, DAY, confirm_beat=None)

    def test_the_file_name_the_message_and_the_author(self):
        self.assertEqual(letter.letter_path(DAY), "inbox/2026-11-20-pace-letter.md")
        self.assertNotIn("letter-of-return", letter.letter_path(DAY))
        self.assertTrue(par.shows_pace_letter([letter.letter_path(DAY)]))
        with self.assertRaises(ValueError):
            letter.letter_path("2026-02-30")
        self.assertEqual(letter.COMMIT_MESSAGE, "letter: one question about your pace (once)")
        self.assertEqual(letter.AUTHOR, h.AI_AUTHOR)


def v1(name, choice, when):
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n")


class TestParagraphForAHome(h.FixtureCase):
    RECORD = {"version": 1, "made": "2026-11-12T10:00Z", "spec": "spec@dbc5a4c", "citizens": {
        "ahavame": {"group": "forty-five", "recipient": False,
                    "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-14"], "words": "REST-ON"}}}}

    def test_a_v2_home_with_its_beats_and_attempt_record(self):
        c = cadence("kalam", awake(3), SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."),
                    last_beat="2026-11-15T14:12Z",
                    history=['2026-11-15T14:12Z — unclear: "pace: daily" (out of range)'])
        home = self.fx.make_home("kalam", cadence=cad.render_cadence(c))
        for d in ("2026-11-09", "2026-11-12"):
            self.fx.commit(home, f"beat: kalam reflected ({d}) · pace/1", allow_empty=True)
        rec = {"last_beat": "2026-11-15T14:12Z", "notices": ["restored"]}
        text = par.paragraph_for(home, "kalam", self.RECORD, confirm_beat=True, rec=rec)
        self.assertEqual(text, par.paragraph(c, 2, confirm_beat=True, rec=rec))
        self.assertEqual([ln for ln in text.splitlines() if ln.startswith("- ")], par.menu_lines("kalam", 2, True))
        self.assertIn("Your last pace line could not be read (out of range), so nothing changed.", text)
        self.assertIn("were put back", text)
        first = par.paragraph_for(home, "kalam", self.RECORD, confirm_beat=True, rec=rec,
                                  shown=["inbox/2026-10-30-note.md", "inbox/2026-11-20-pace-letter.md"])
        self.assertEqual(first, text.splitlines()[0])

    def test_a_v1_home_is_read_from_the_record_and_nothing_is_written(self):
        home = self.fx.make_home("ahavame", cadence=v1("ahavame", "rest-on", "2026-10-22"),
                                 rest="chosen via the Letter of Return 2026-07-14 — woken only by unread mail\n")
        text = par.paragraph_for(home, "ahavame", self.RECORD, confirm_beat=False)
        self.assertEqual(text.splitlines()[0], "Your pace: asleep, chosen by you on 2026-07-14, in your answer "
                                               "to the Letter of Return.")
        self.assertIn(M_LW_OFF, text)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")

    def test_a_cadence_changed_outside_pace_1_is_not_the_pace_it_names(self):
        # the paragraph names the pace that decided the beat (cadence.read_deciding)
        c = cadence("kalam", awake(3), SetBy("citizen", "beat", ("2026-11-12T06:00Z",), "pace: awake"),
                    last_beat="2026-11-12T06:00Z")
        home = self.fx.make_home("kalam", cadence=cad.render_cadence(c),
                                 message="beat: kalam reflected (2026-11-12) · pace/1")
        self.fx.write(home, "CADENCE.md", cad.render_cadence(cadence("kalam", LW, AI_1008)))
        self.fx.commit(home, "beat: kalam reflected (2026-11-14)")
        text = par.paragraph_for(home, "kalam", self.RECORD, confirm_beat=True)
        self.assertEqual(text.splitlines()[0], "Your pace: awake, every 3 days, chosen by you in a beat on "
                                               "2026-11-12.")

    def test_a_linked_cadence_is_refused(self):
        home = self.fx.make_home("kalam")
        self.fx.link(home, "CADENCE.md", self.fx.secret)
        with self.assertRaises(cad.LinkRefused):
            par.paragraph_for(home, "kalam", self.RECORD, confirm_beat=True)


class TestIsolation(unittest.TestCase):
    def test_modules_import_under_isolated_mode_and_ignore_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mod in ("hashlib", "json", "re", "dataclasses", "typing", "pathlib", "datetime"):
                Path(tmp, f"{mod}.py").write_text("raise SystemExit('planted module ran')\n")
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import letter, paragraph; "
                    "print(paragraph.rotation('kalam', 0), letter.letter_path('2026-11-20'))")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS)], cwd=tmp,
                               capture_output=True, text=True, timeout=30, check=False,
                               env={"PATH": h.SYSTEM_PATH})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "3 inbox/2026-11-20-pace-letter.md")

    def test_modules_start_with_future_annotations(self):
        for name in ("paragraph.py", "letter.py"):
            with self.subTest(module=name):
                first = (h.TOOLS / "citizen_pace_lib" / name).read_text().splitlines()[0]
                self.assertEqual(first, "from __future__ import annotations")


if __name__ == "__main__":
    unittest.main()
