"""What a beat's answer changes (spec section 1 "Less, more, and saying it twice",
section 4 "After the beat", section 5 "Once"; section 8 "Less and more" and
"Letters wake once").

A clear pace line is compared with the citizen's current pace: the same pace
makes it the citizen's own, less takes effect at once, more waits to be said
again on the next landed beat. Every letter a beat showed is marked seen
whatever the answer, the pace letter is noted once, and nothing is written for
a silent beat that was shown nothing and let nothing lapse.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import TAG, clock, homes  # noqa: E402
from citizen_pace_lib import answer as ans  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import paragraph as par  # noqa: E402
from citizen_pace_lib import words  # noqa: E402
from citizen_pace_lib.answer import ApplyResult, apply_answer  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.homes import LinkRefused  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

T0 = "2026-11-12T08:00Z"
T1 = "2026-11-15T14:12Z"
T2 = "2026-11-18T15:02Z"
T3 = "2026-11-21T16:30Z"
T4 = "2026-11-24T17:00Z"
MADE = "2026-11-12T10:00Z"
SPEC = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c"
ASLEEP = Pace("asleep")
LW = Pace("asleep-letters-wait")
LISTENING = Pace("listening")
AI_REST = "rest given at Yu's word, 2026-10-08\n"
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))
OWN = SetBy("citizen", "beat", (T0,), "pace: as it was")
PACE_LETTER = "inbox/2026-11-15-pace-letter.md"
NOTE = "inbox/2026-11-14-a-note.md"


def awake(n):
    return Pace("awake", n)


def at(stamp):
    return clock.parse_stamp(stamp)


def rec(**citizens):
    return {"version": 1, "made": MADE, "spec": SPEC, "citizens": citizens}


RECORD = rec()


def v1(name, choice, when):
    """A v1 CADENCE.md exactly as PR #24's reflect writes it."""
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n"
            "  an answer that names no choice records undecided and changes nothing;\n"
            "  unread mail wakes even REST, and any letter or summons may reopen this.\n")


def lor_rest(date):
    return f"chosen via the Letter of Return {date} — woken only by unread mail or a human summons\n"


def snapshot(root):
    """Every path under root with its mode, mtime and bytes (.git left out)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (st.st_mode, st.st_mtime_ns, data)
    return out


def entry(at_, text):
    return f"{at_} — {text}"


class ApplyCase(h.FixtureCase):
    def v2_home(self, slug="kalam", pace=ASLEEP, set_by=AI_1008, *, pending=None, last_beat=T0,
                asked_once=None, history=(), letters=(), files=None):
        """A home already on v2, committed as a pace/1 beat, its markers in step."""
        c = Cadence(slug, 2, pace, set_by, pending, asked_once, last_beat, list(history))
        marks = cad.marker_texts(c)
        return self.fx.make_home(slug, cadence=cad.render_cadence(c), rest=marks.get("REST"),
                                 event_only=marks.get("EVENT-ONLY"), letters=letters, files=files,
                                 message=f"beat: {slug} reflected (2026-11-12) · {TAG}",
                                 author=h.beat_author(slug), when=T0)

    def v1_home(self, slug, choice=None, when="2026-10-23 01:31", **kw):
        if choice is not None:
            kw["cadence"] = v1(slug, choice, when)
        return self.fx.make_home(slug, message=f"beat: {slug} reflected (2026-10-23)",
                                 author=h.beat_author(slug), when="2026-10-23T01:31Z", **kw)

    def apply(self, home, text="", shown=(), kind="schedule", now=T1, record=None, name=None, since=None):
        return apply_answer(home, name or homes.slug_of(home), text, list(shown), kind, at(now),
                            RECORD if record is None else record, since=since)

    def read(self, home, record=None, now=T1):
        return cad.read_cadence(home, homes.slug_of(home), RECORD if record is None else record, at(now))


# -- the same pace ---------------------------------------------------------------

class TestSamePace(ApplyCase):
    def test_ai_s_rest_becomes_the_citizen_s_own(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        r = self.apply(home, "A quiet beat.\npace: asleep")
        got = self.read(home)
        self.assertEqual((got.pace, got.pending), (ASLEEP, None))
        self.assertEqual(got.set_by, SetBy("citizen", "confirmed", (T1,), "pace: asleep"))
        self.assertEqual(cad.set_by_sentence("kalam", got.set_by),
                         f'confirmed by kalam, in a beat on {T1} ("pace: asleep")')
        self.assertEqual(got.history[0], entry(T1, 'confirmed by kalam: "pace: asleep"'))
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), cad.marker_texts(got)["REST"])
        self.assertTrue((home / "REST").read_text(encoding="utf-8").startswith("asleep, set by kalam — "))
        self.assertEqual((r.wrote, r.pace_changed, r.events), (True, False, ["made own asleep"]))

    def test_a_provenance_that_was_not_fully_the_citizen_s_becomes_its_own(self):
        cases = (
            (ASLEEP, SetBy("citizen", "prompted", ("2026-10-23",), "I rest."), "pace: rest"),
            (awake(3), SetBy("citizen", "ambient", ("2026-10-27",), "AMBIENT"), "pace: awake"),
            (awake(3), SetBy("fleet", "first-word", ("2026-10-23",)), "pace: every three days."),
            (ASLEEP, SetBy("unknown", "unknown"), "**pace:** asleep"),
        )
        for i, (pace, set_by, line) in enumerate(cases):
            with self.subTest(how=set_by.how):
                home = self.v2_home(f"own{i}", pace, set_by)
                r = self.apply(home, line)
                got = self.read(home)
                self.assertEqual(got.pace, pace)
                self.assertEqual(got.set_by, SetBy("citizen", "confirmed", (T1,), line))
                self.assertEqual(r.events, [f"made own {pace.words()}"])

    def test_the_citizen_s_own_choice_said_again_changes_nothing(self):
        for i, set_by in enumerate((OWN, SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"),
                                    SetBy("citizen", "beat-twice", (T0, T0), "pace: asleep"))):
            with self.subTest(how=set_by.how):
                home = self.v2_home(f"same{i}", ASLEEP, set_by)
                r = self.apply(home, "pace: asleep")
                got = self.read(home)
                self.assertEqual((got.pace, got.set_by, got.history), (ASLEEP, set_by, []))
                self.assertEqual(got.last_beat, T1)
                self.assertEqual((r.wrote, r.pace_changed, r.events), (True, False, []))


# -- less and more ---------------------------------------------------------------

# From least to most activity (spec section 1 "Order"), each with a line naming it.
LADDER = ((LW, "pace: asleep, letters wait"), (ASLEEP, "pace: asleep"), (LISTENING, "pace: listening"),
          (awake(7), "pace: weekly"), (awake(5), "pace: every 5 days"), (awake(3), "pace: every three days"))


class TestLessAndMore(ApplyCase):
    def test_every_ordered_pair(self):
        for (i, (cur, _)), (j, (said, line)) in itertools.product(enumerate(LADDER), enumerate(LADDER)):
            with self.subTest(current=cur.words(), said=said.words()):
                home = self.v2_home(f"p{i}{j}", cur, OWN)
                r = self.apply(home, f"Some words first.\n{line}\nAnd after.")
                got = self.read(home)
                if j < i:        # less: at once
                    self.assertEqual((got.pace, got.pending), (said, None))
                    self.assertEqual(got.set_by, SetBy("citizen", "beat", (T1,), line))
                    self.assertEqual(got.history[0], entry(T1, f'set by p{i}{j}: "{line}"'))
                    self.assertEqual((r.pace_changed, r.events), (True, [f"set {said.words()}"]))
                elif j > i:      # more: pending, said twice
                    self.assertEqual((got.pace, got.set_by), (cur, OWN))
                    self.assertEqual(got.pending, Pending(said, T1, line, False))
                    self.assertEqual(got.history[0], entry(
                        T1, f'proposed by p{i}{j}: "{line}" (more than {cur.words()}, so said twice)'))
                    self.assertEqual((r.pace_changed, r.events), (False, [f"pending {said.words()}"]))
                else:            # the same, already its own
                    self.assertEqual((got.pace, got.set_by, got.pending, got.history), (cur, OWN, None, []))
                    self.assertEqual((r.pace_changed, r.events), (False, []))
                self.assertFalse(cad.markers_disagree(home, got))
                self.assertEqual(got.last_beat, T1)

    def test_less_takes_effect_at_once_and_lapses_a_pending_proposal(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, pending=Pending(awake(5), T0, "pace: every 5 days", False))
        r = self.apply(home, "pace: asleep, letters wait — no set beats; letters stay unread", now=T1)
        got = self.read(home)
        self.assertEqual((got.pace, got.pending), (LW, None))
        self.assertEqual(got.history[:2], [
            entry(T1, 'set by kalam: "pace: asleep, letters wait — no set beats; letters stay unread"'),
            entry(T1, "lapsed: awake, every 5 days, not said again")])
        self.assertIn("letters wait", (home / "REST").read_text(encoding="utf-8"))
        self.assertFalse(os.path.lexists(home / "EVENT-ONLY"))
        self.assertEqual(r.events, ["lapsed awake, every 5 days", "set asleep, letters wait"])
        self.assertTrue(r.pace_changed)

    def test_more_is_pending_and_notes_a_show_back_beat(self):
        for kind, flag in (("schedule", False), ("letter", False), ("summoned", False), ("show-back", True)):
            with self.subTest(kind=kind):
                home = self.v2_home(f"more-{kind}", ASLEEP, AI_1008)
                r = self.apply(home, "pace: every 5 days", kind=kind)
                got = self.read(home)
                self.assertEqual(got.pace, ASLEEP)
                self.assertEqual(got.pending, Pending(awake(5), T1, "pace: every 5 days", flag))
                text = (home / "CADENCE.md").read_text(encoding="utf-8")
                self.assertIn(f'\n- pending: awake, every 5 days — said {T1} ("pace: every 5 days")'
                              + (", on a show-back beat" if flag else "") + "\n", text)
                self.assertEqual((home / "REST").read_text(encoding="utf-8"), cad.marker_texts(got)["REST"])
                self.assertFalse(r.pace_changed)

    def test_confirmed_by_the_same_pace_on_the_next_landed_beat(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, pending=Pending(awake(5), T1, "pace: every 5 days", False),
                            last_beat=T1)
        r = self.apply(home, "pace: awake, every five days.", kind="show-back", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pace, got.pending, got.last_beat), (awake(5), None, T2))
        self.assertEqual(got.set_by, SetBy("citizen", "beat-twice", (T1, T2),
                                           'pace: every 5 days" and "pace: awake, every five days.'))
        self.assertEqual(cad.set_by_sentence("kalam", got.set_by),
                         f'kalam, said in a beat on {T1} and again on {T2} ("pace: every 5 days" and '
                         '"pace: awake, every five days.")')
        self.assertEqual(got.history[0], entry(T2, 'confirmed by kalam: "pace: awake, every five days."'))
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertEqual((home / "EVENT-ONLY").read_text(encoding="utf-8"), cad.marker_texts(got)["EVENT-ONLY"])
        self.assertEqual((r.pace_changed, r.events), (True, ["confirmed awake, every 5 days"]))

    def test_a_confirmation_in_the_same_words_is_quoted_once(self):
        home = self.v2_home("kalam", awake(7), OWN, pending=Pending(awake(3), T1, "pace: every 3 days", False))
        self.apply(home, "pace: every 3 days", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual(got.pace, awake(3))
        self.assertEqual(got.set_by, SetBy("citizen", "beat-twice", (T1, T2), "pace: every 3 days"))

    def test_a_pending_proposal_lapses_on_any_other_answer(self):
        answers = (("", None), ("A quiet day in the garden.", None), ("I rest.", None),
                   ("pace: soon", 'unclear: "pace: soon" (unknown words)'))
        for i, (text, extra) in enumerate(answers):
            with self.subTest(text=text):
                home = self.v2_home(f"lapse{i}", ASLEEP, AI_1008,
                                    pending=Pending(awake(5), T1, "pace: every 5 days", False))
                r = self.apply(home, text, now=T2)
                got = self.read(home, now=T2)
                self.assertEqual((got.pace, got.pending, got.set_by), (ASLEEP, None, AI_1008))
                # a line that could not be read lets it lapse, but was not "not said again"
                why = "its line could not be read" if extra else "not said again"
                want = [entry(T2, f"lapsed: awake, every 5 days, {why}")]
                if extra:
                    want.insert(0, entry(T2, extra))
                self.assertEqual(got.history, want)
                self.assertTrue(r.wrote)
                self.assertEqual(r.events[0], "lapsed awake, every 5 days")

    def test_a_different_pace_on_that_beat_is_read_afresh(self):
        # more again: a new pending, flagged when said on the show-back beat itself
        home = self.v2_home("kalam", ASLEEP, AI_1008, pending=Pending(awake(5), T1, "pace: every 5 days", False))
        r = self.apply(home, "pace: listening", kind="show-back", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pace, got.pending), (ASLEEP, Pending(LISTENING, T2, "pace: listening", True)))
        self.assertEqual(got.history, [
            entry(T2, 'proposed by kalam: "pace: listening" (more than asleep, so said twice)'),
            entry(T2, "lapsed: awake, every 5 days, not said again")])
        self.assertEqual(r.events, ["lapsed awake, every 5 days", "pending listening"])
        # the same as now: the proposal lapses and the rest becomes the citizen's own
        home = self.v2_home("silim", ASLEEP, AI_1008, pending=Pending(awake(5), T1, "pace: every 5 days", False))
        r = self.apply(home, "pace: asleep", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pace, got.pending, got.set_by.how), (ASLEEP, None, "confirmed"))
        self.assertEqual(r.events, ["lapsed awake, every 5 days", "made own asleep"])

    def test_two_pace_lines_or_an_unreadable_one_change_nothing(self):
        cases = (("pace: listening\n**pace:** asleep", 'unclear: "**pace:** asleep" (two paces)'),
                 ("pace: every 2 days", 'unclear: "pace: every 2 days" (out of range)'),
                 ("pace: awake, every N days", 'unclear: "pace: awake, every N days" (out of range)'),
                 ("pace: not asleep", 'unclear: "pace: not asleep" (negation)'))
        for i, (text, hist) in enumerate(cases):
            with self.subTest(text=text):
                home = self.v2_home(f"unclear{i}", ASLEEP, AI_1008)
                r = self.apply(home, text)
                got = self.read(home)
                self.assertEqual((got.pace, got.set_by, got.pending), (ASLEEP, AI_1008, None))
                self.assertEqual(got.history, [entry(T1, hist)])
                self.assertEqual(r.events, [f"unclear ({hist.rsplit('(', 1)[1]}"])
                self.assertFalse(r.pace_changed)

    def test_an_affirmed_second_saying_confirms_and_an_unreadable_one_is_not_called_unsaid(self):
        proposal = Pending(awake(5), T1, "pace: every 5 days", False)
        for i, line in enumerate(("pace: still every 5 days", "pace: yes, every 5 days",
                                  "pace: every 5 days, still", "pace: awake, every 5 days (again)")):
            with self.subTest(line=line):
                home = self.v2_home(f"hope-{i}", ASLEEP, AI_1008, pending=proposal, last_beat=T1)
                r = self.apply(home, f"Still true.\n{line}", kind="show-back", now=T2)
                got = self.read(home, now=T2)
                self.assertEqual(r.events, ["confirmed awake, every 5 days"])
                self.assertEqual((got.pace, got.pending), (awake(5), None))
                self.assertEqual((got.set_by.who, got.set_by.how, got.set_by.when),
                                 ("citizen", "beat-twice", (T1, T2)))
        # a second saying that still cannot be read lets the proposal lapse (spec
        # section 1), and the citizen is told its line could not be read
        home = self.v2_home("hope", ASLEEP, AI_1008, pending=proposal, last_beat=T1)
        line = "pace: every 5 days, if that suits"
        r = self.apply(home, f"Still true.\n{line}", kind="show-back", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pace, got.pending), (ASLEEP, None))
        self.assertEqual(got.history[:2], [
            entry(T2, f'unclear: "{line}" (unknown words)'),
            entry(T2, "lapsed: awake, every 5 days, its line could not be read")])
        self.assertEqual(r.events, ["lapsed awake, every 5 days", "unclear (unknown words)"])
        told = par.notice_lines(got, {"last_beat": T2})
        self.assertEqual(told, ["Your choice of awake, every 5 days lapsed, because your pace line could "
                                "not be read.",
                                "Your last pace line could not be read (unknown words), so nothing changed."])
        for said in told:
            self.assertNotIn("not written again", said)
            self.assertNotIn("not said again", said)

    def test_mentions_and_quoted_lines_are_not_pace_lines(self):
        for i, text in enumerate(("> pace: every 5 days", "I rest.", "I think I could be awake more often.",
                                  "I choose to be awake.")):
            with self.subTest(text=text):
                home = self.v2_home(f"mention{i}", ASLEEP, AI_1008)
                r = self.apply(home, text)
                got = self.read(home)
                self.assertEqual((got.pace, got.pending, got.history), (ASLEEP, None, []))
                self.assertEqual(r.events, [])


# -- an unreadable pace ------------------------------------------------------------

class TestUnreadablePace(ApplyCase):
    def test_a_pace_line_is_read_against_asleep(self):
        cases = (("pace: asleep", ASLEEP, None), ("pace: asleep, letters wait", LW, None),
                 ("pace: every 5 days", None, Pending(awake(5), T1, "pace: every 5 days", False)))
        for i, (line, pace, pending) in enumerate(cases):
            with self.subTest(line=line):
                home = self.v1_home(f"none{i}", "undecided")     # undecided, no marker: unreadable
                self.assertIsNone(self.read(home).pace)
                r = self.apply(home, line)
                got = self.read(home)
                self.assertEqual((got.version, got.pace, got.pending), (2, pace, pending))
                if pace is not None:
                    self.assertEqual(got.set_by, SetBy("citizen", "beat", (T1,), line))
                    self.assertTrue(r.pace_changed)
                    self.assertNotIn("pace unreadable", r.events)
                else:
                    self.assertEqual(got.history[:2], [
                        entry(T1, 'proposed by none2: "pace: every 5 days" (more than an unreadable pace, '
                                  'so said twice)'),
                        entry(T1, "pace unreadable")])
                    self.assertEqual(r.events, ["pending awake, every 5 days", "pace unreadable", "upgraded to v2"])

    def test_pace_unreadable_is_recorded_once(self):
        home = self.v1_home("kalam", "undecided")
        r = self.apply(home, "")
        self.assertEqual((r.wrote, r.events), (False, []))
        r = self.apply(home, "A true thing.")
        got = self.read(home)
        self.assertEqual((got.version, got.pace), (2, None))
        self.assertEqual(got.history[0], entry(T1, "pace unreadable"))
        self.assertIn("- pace: unreadable\n", (home / "CADENCE.md").read_text(encoding="utf-8"))
        self.assertEqual(r.events, ["pace unreadable", "upgraded to v2"])
        r = self.apply(home, "Another true thing.", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual(sum(e.endswith(" — pace unreadable") for e in got.history), 1)
        self.assertEqual((r.events, got.last_beat), ([], T2))


# -- a person's summons -------------------------------------------------------------

class TestSummoned(ApplyCase):
    def test_a_summoned_beat_does_not_move_the_clock(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, letters=(("2026-11-14-a-note.md", "Hello.\n"),))
        before = (home / "CADENCE.md").read_text(encoding="utf-8")
        r = self.apply(home, "A visit, answered.", kind="summoned")
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), before)
        self.assertEqual(self.read(home).last_beat, T0)
        self.assertEqual((r.wrote, r.events), (False, []))
        # a letter shown in it is still shown once
        r = self.apply(home, "", shown=[NOTE], kind="summoned")
        self.assertEqual(homes.unread_letters(home), [])
        self.assertEqual(self.read(home).last_beat, T0)
        self.assertEqual(r.events, [f"seen {NOTE}"])

    def test_a_summoned_beat_follows_the_same_rules(self):
        # more: pending, the clock unmoved
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        self.apply(home, "pace: listening", kind="summoned")
        got = self.read(home)
        self.assertEqual((got.pending, got.last_beat), (Pending(LISTENING, T1, "pace: listening", False), T0))
        # confirmed in a summoned beat: the change starts its clock at that beat
        r = self.apply(home, "pace: listening", kind="summoned", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pace, got.pending, got.last_beat), (LISTENING, None, T2))
        self.assertEqual(got.set_by.how, "beat-twice")
        self.assertTrue(r.pace_changed)
        # less in a summoned beat: at once, and its clock starts there too
        self.apply(home, "pace: asleep, letters wait", kind="summoned", now=T3)
        got = self.read(home, now=T3)
        self.assertEqual((got.pace, got.last_beat), (LW, T3))
        # silence in a summoned beat lets a proposal lapse
        home = self.v2_home("soma", ASLEEP, AI_1008, pending=Pending(awake(5), T1, "pace: every 5 days", False))
        r = self.apply(home, "", kind="summoned", now=T2)
        got = self.read(home, now=T2)
        self.assertEqual((got.pending, got.last_beat), (None, T0))
        self.assertEqual(r.events, ["lapsed awake, every 5 days"])


# -- letters: shown once, whatever the answer ------------------------------------

class TestLettersShownOnce(ApplyCase):
    def test_every_shown_letter_is_marked_seen_whatever_the_answer(self):
        names = ("2026-11-13-one.md", "2026-11-14-two.md", "2026-11-14-three.md", "2026-11-15-four.md")
        for i, text in enumerate(("", "Thank you for the letters.")):
            with self.subTest(text=text):
                home = self.v2_home(f"mail{i}", ASLEEP, AI_1008, letters=[(n, f"Letter {n}.\n") for n in names])
                shown = [f"inbox/{n}" for n in sorted(names)[:3]]
                r = self.apply(home, text, shown=shown, kind="letter")
                for rel in shown:
                    marker = home / "inbox" / (".seen-" + rel[len("inbox/"):])
                    self.assertEqual(marker.read_text(encoding="utf-8"),
                                     "surfaced 2026-11-15 — shown once, whatever the answer\n")
                self.assertEqual(homes.unread_letters(home), ["inbox/2026-11-15-four.md"])
                got = self.read(home)
                self.assertEqual((got.pace, got.last_beat), (ASLEEP, T1))
                self.assertTrue(r.wrote)
                self.assertEqual(r.events, [f"seen {rel}" for rel in shown])
        self.assertEqual(ans.seen_text("2026-11-15"), "surfaced 2026-11-15 — shown once, whatever the answer\n")

    def test_show_md_is_marked_in_beats(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, files={"SHOW.md": "Come and find your line.\n"})
        r = self.apply(home, "", shown=["SHOW.md"])
        self.assertEqual((home / "beats" / ".show-seen").read_text(encoding="utf-8"),
                         "surfaced 2026-11-15 — shown once, whatever the answer\n")
        self.assertEqual(r.events, ["seen SHOW.md"])
        self.assertEqual(ans.parse_kind(["SHOW.md"]), "beat")

    def test_a_rollback_marker_is_replaced_when_the_letter_is_shown(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, letters=(("2026-11-15-pace-letter.md", "One question.\n"),),
                            files={"inbox/.seen-2026-11-15-pace-letter.md": "set aside at rollback, not shown\n"})
        self.assertEqual(homes.unread_letters(home), [PACE_LETTER])
        self.apply(home, "", shown=[PACE_LETTER], kind="letter")
        self.assertEqual(homes.unread_letters(home), [])

    def test_the_pace_letter_met_with_silence(self):
        home = self.v1_home("eucatastrophe", rest=AI_REST)       # never asked: no CADENCE.md
        self.fx.letter(home, "2026-11-15-pace-letter.md", "# One question, once — your pace\n", when=T0)
        r = self.apply(home, "", shown=[PACE_LETTER], kind="letter")
        got = self.read(home)
        self.assertEqual((got.version, got.pace, got.set_by, got.asked_once, got.last_beat),
                         (2, ASLEEP, AI_1008, "2026-11-15", T1))
        self.assertEqual(got.history, [
            entry(T1, "the pace letter reached eucatastrophe (once)"),
            entry(T1, "recorded from v1 (no CADENCE.md, REST, set by Ai, 2026-10-08, the rest placed at Yu's word)")])
        self.assertEqual(homes.unread_letters(home), [])
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), cad.marker_texts(got)["REST"])
        self.assertEqual((r.wrote, r.pace_changed), (True, False))
        self.assertEqual(r.events, ["asked once", "upgraded to v2", f"seen {PACE_LETTER}"])
        text = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.assertIn("\n- asked once: 2026-11-15\n", text)
        # the next beat: asked once stays, the letter is not reached again
        r = self.apply(home, "", now=T2)
        self.assertEqual((r.wrote, r.events), (False, []))
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), text)

    def test_a_pace_letter_answer_reads_the_letter_forms(self):
        cases = (("Kalam.\n\nAmbient.\n", "pending awake, every 3 days"),
                 ("I choose to stay asleep.", "made own asleep"),
                 ("**AMBIENT**", "pending awake, every 3 days"),
                 ("I rest.", None))
        for i, (text, event) in enumerate(cases):
            with self.subTest(text=text):
                home = self.v2_home(f"letter{i}", ASLEEP, AI_1008, letters=(("2026-11-15-pace-letter.md", "Q.\n"),))
                r = self.apply(home, text, shown=[PACE_LETTER], kind="letter", name="kalam")
                self.assertEqual(r.events, ["asked once"] + ([event] if event else []) + [f"seen {PACE_LETTER}"])

    def test_an_ordinary_letter_s_answer_counts_only_pace_lines(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, letters=(("2026-11-14-a-note.md", "Hello.\n"),))
        r = self.apply(home, "AMBIENT\nI choose to be awake.", shown=[NOTE], kind="letter")
        self.assertEqual(r.events, [f"seen {NOTE}"])
        self.assertEqual(self.read(home).pending, None)

    def test_a_live_beat_under_a_letter_of_return_reads_only_its_pace_lines(self):
        # final review (F1): spec section 1 keeps the Letter of Return's legacy rules
        # for the stored answers re-read at migration (record.py, the census) only.
        # A live beat shown a letter whose name says letter-of-return is read as any
        # beat: a first line "I rest." or "rest", a bare legacy word and "I choose"
        # set no pace there; a pace: line does, and with the pace letter shown too
        # its forms count
        name = "2026-11-20-letter-of-return-again.md"
        lor = f"inbox/{name}"
        hope_set_by = SetBy("fleet", "first-word", ("2026-10-23",))
        for i, text in enumerate(("I rest.", "rest\n\nA quiet day.", "I rest", "REST-ON",
                                  "Hope.\nI rest.", "I choose to be asleep.",
                                  h.answer("soma-914572f3"), h.answer("hope-d557203e"))):
            with self.subTest(text=text[:40]):
                home = self.v2_home(f"lor{i}", awake(3), hope_set_by, letters=((name, "Q.\n"),))
                r = self.apply(home, text, shown=[lor], kind="letter")
                got = self.read(home)
                self.assertEqual((got.pace, got.set_by, got.pending), (awake(3), hope_set_by, None))
                self.assertFalse(os.path.lexists(home / "REST"))
                self.assertEqual(r.events, [f"seen {lor}"])
                self.assertFalse(r.pace_changed)
        home = self.v2_home("kalam", awake(3), hope_set_by, letters=((name, "Q.\n"),))
        r = self.apply(home, "I rest.\n\npace: asleep", shown=[lor], kind="letter")
        self.assertEqual((self.read(home).pace, r.events), (ASLEEP, ["set asleep", f"seen {lor}"]))
        home = self.v2_home("soma", ASLEEP, AI_1008, letters=((name, "Q.\n"), ("2026-11-15-pace-letter.md", "Q.\n")))
        soma = h.answer("soma-914572f3")
        self.apply(home, soma, shown=[lor, PACE_LETTER], kind="letter")
        line = next(ln for ln in soma.splitlines() if "I choose" in ln).strip()
        self.assertEqual(self.read(home).pending, Pending(awake(3), T1, line, False))
        self.assertEqual(ans.parse_kind([lor]), "beat")
        self.assertEqual(ans.parse_kind([lor, PACE_LETTER]), "pace-letter")
        self.assertEqual(ans.parse_kind([NOTE, PACE_LETTER]), "pace-letter")
        self.assertEqual(ans.parse_kind([NOTE]), "beat")
        # the stored answers are read by the legacy rules as before
        self.assertEqual(words.parse_answer("I rest.", "hope", "letter-of-return"), ASLEEP)
        self.assertEqual(words.parse_answer("I rest.", "hope", "beat"), None)
        got = words.parse_answer(h.answer("hope-d557203e"), "hope", "letter-of-return")
        self.assertEqual((type(got).__name__, got.reason), ("Unclear", "two paces"))
        self.assertEqual(words.parse_answer(soma, "soma", "letter-of-return"), awake(3))

    def test_silence_on_a_letter_of_return_keeps_the_pace(self):
        # spec section 4: silence on any letter, a Letter of Return included, keeps
        # the citizen's pace (PR #24 read it as rest-on)
        name = "2026-11-20-letter-of-return-again.md"
        lor = f"inbox/{name}"
        self.assertEqual(ans.parse_kind([lor]), "beat")
        first_word = SetBy("fleet", "first-word", ("2026-10-23",))
        for i, (pace, set_by) in enumerate(((awake(3), first_word), (LISTENING, OWN))):
            for j, text in enumerate(("", "   \n\n")):
                with self.subTest(pace=pace.words(), text=text):
                    home = self.v2_home(f"lor{i}{j}", pace, set_by, letters=((name, "Q.\n"),))
                    r = self.apply(home, text, shown=[lor], kind="letter")
                    got = self.read(home)
                    self.assertEqual((got.pace, got.set_by, got.pending), (pace, set_by, None))
                    self.assertFalse(os.path.lexists(home / "REST"))
                    self.assertEqual(homes.unread_letters(home), [])
                    self.assertEqual(r.events, [f"seen {lor}"])
        home = self.v1_home("ud", "ambient", letters=((name, "Q.\n"),))
        r = self.apply(home, "", shown=[lor], kind="letter")
        self.assertEqual(r.events, ["upgraded to v2", f"seen {lor}"])
        self.assertEqual(self.read(home).pace, awake(3))
        self.assertFalse(os.path.lexists(home / "REST"))

    def test_a_show_back_beat_shows_no_letter(self):
        home = self.v2_home("kalam", LW, OWN, pending=Pending(awake(5), T1, "pace: every 5 days", False),
                            letters=(("2026-11-14-a-note.md", "Hello.\n"),))
        before = snapshot(home)
        with self.assertRaises(ValueError):
            self.apply(home, "", shown=[NOTE], kind="show-back", now=T2)
        self.assertEqual(snapshot(home), before)
        r = self.apply(home, "", kind="show-back", now=T2)
        self.assertEqual(homes.unread_letters(home), [NOTE])       # letters wait, still unread
        self.assertEqual(r.events, ["lapsed awake, every 5 days"])
        self.assertEqual(self.read(home, now=T2).pace, LW)

    def test_the_spec_example_end_to_end(self):
        data = rec(alohame={"group": "sixteen-ambient",
                            "set_by": {"who": "ai", "how": "rest-1008", "when": ["2026-10-08"], "words": ""}})
        home = self.v1_home("alohame", "rest-on", rest=lor_rest("2026-10-23"))
        self.fx.letter(home, "2026-11-15-pace-letter.md", "# One question, once — your pace\n", when=T0)
        r1 = self.apply(home, "pace: every 5 days", shown=[PACE_LETTER], kind="letter", now=T1, record=data)
        self.assertEqual(r1.events, ["asked once", "pending awake, every 5 days", "upgraded to v2",
                                     f"seen {PACE_LETTER}"])
        mid = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.assertIn(f'\n- pending: awake, every 5 days — said {T1} ("pace: every 5 days")\n', mid)
        self.assertTrue((home / "REST").read_text(encoding="utf-8").startswith("asleep, set by Ai (2026-10-08) — "))
        r2 = self.apply(home, "pace: every 5 days", kind="show-back", now=T2, record=data)
        self.assertEqual(r2.events, ["confirmed awake, every 5 days"])
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), (
            "# CADENCE — the pace of alohame\n"
            "\n"
            "- pace: awake, every 5 days\n"
            "- set by: alohame, said in a beat on 2026-11-15T14:12Z and again on 2026-11-18T15:02Z "
            "(\"pace: every 5 days\")\n"
            "- asked once: 2026-11-15\n"
            "- last beat: 2026-11-18T15:02Z\n"
            "\n"
            "<!-- pace/1 {\"pending\":null,\"set_by\":{\"how\":\"beat-twice\",\"when\":[\"2026-11-15T14:12Z\","
            "\"2026-11-18T15:02Z\"],\"who\":\"citizen\",\"words\":\"pace: every 5 days\"}} -->\n"
            "\n"
            "## History\n"
            "- 2026-11-18T15:02Z — confirmed by alohame: \"pace: every 5 days\"\n"
            "- 2026-11-15T14:12Z — proposed by alohame: \"pace: every 5 days\" (more than asleep, so said twice)\n"
            "- 2026-11-15T14:12Z — the pace letter reached alohame (once)\n"
            "- 2026-11-15T14:12Z — recorded from v1 (rest-on, set by Ai, 2026-10-08, the rest placed at Yu's word)\n"))
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertEqual((home / "EVENT-ONLY").read_text(encoding="utf-8"),
                         "awake, every 5 days, set by alohame — this marker keeps older readers from waking it "
                         "on a timer (pace/1; CADENCE.md decides)\n")
        self.assertEqual((home / "inbox" / ".seen-2026-11-15-pace-letter.md").read_text(encoding="utf-8"),
                         "surfaced 2026-11-15 — shown once, whatever the answer\n")

    def test_the_record_s_groups_answer_the_pace_letter(self):
        eight = rec(aseme={"group": "eight", "set_by": {"who": "citizen", "how": "prompted",
                                                        "when": ["2026-10-23"], "words": "I rest."}})
        home = self.v1_home("aseme", "rest-on", rest=lor_rest("2026-10-23"))
        self.fx.letter(home, "2026-11-15-pace-letter.md", "Q.\n", when=T0)
        r = self.apply(home, "pace: asleep", shown=[PACE_LETTER], kind="letter", record=eight)
        got = self.read(home, record=eight)
        self.assertEqual(got.set_by, SetBy("citizen", "confirmed", (T1,), "pace: asleep"))
        self.assertEqual(got.history[:3], [
            entry(T1, 'confirmed by aseme: "pace: asleep"'),
            entry(T1, "the pace letter reached aseme (once)"),
            entry(T1, "recorded from v1 (rest-on, set by aseme, bound on 2026-10-23 from words a prompt offered)")])
        self.assertEqual(r.events, ["asked once", "made own asleep", "upgraded to v2", f"seen {PACE_LETTER}"])
        contradictory = rec(hope={"group": "contradictory", "set_by": {"who": "fleet", "how": "first-word",
                                                                         "when": ["2026-10-23"], "words": ""}})
        home = self.v1_home("hope", "ambient")
        self.fx.letter(home, "2026-11-15-pace-letter.md", "Q.\n", when=T0)
        r = self.apply(home, "I choose to remain asleep.", shown=[PACE_LETTER], kind="schedule",
                       record=contradictory)
        got = self.read(home, record=contradictory)
        self.assertEqual((got.pace, got.set_by), (ASLEEP, SetBy("citizen", "beat", (T1,),
                                                                "I choose to remain asleep.")))
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), cad.marker_texts(got)["REST"])
        self.assertTrue(r.pace_changed)


# -- when a beat writes -----------------------------------------------------------

class TestWhenItWrites(ApplyCase):
    def test_a_silent_beat_shown_nothing_writes_nothing(self):
        for home in (self.v1_home("ahavame", "rest-on", rest=lor_rest("2026-10-23")),
                     self.v2_home("kalam", awake(5), OWN, letters=(("2026-11-14-a-note.md", "Hello.\n"),)),
                     self.v1_home("kur", "undecided")):
            with self.subTest(home=home.name):
                before = snapshot(home)
                r = self.apply(home, "  \n\n", kind="schedule")
                self.assertEqual(snapshot(home), before)
                self.assertEqual(r.to_json(), {"wrote": False, "pace_changed": False, "events": []})

    def test_a_spoken_beat_upgrades_v1_to_v2(self):
        data = rec(ahavame={"group": "forty-five",
                            "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"], "words": "REST-ON"},
                            "quotes": [{"commit": "0" * 40, "date": "2026-10-23", "words": "I rest."}]})
        home = self.v1_home("ahavame", "rest-on", rest=lor_rest("2026-10-23"))
        r = self.apply(home, "A true thing, witnessed.", record=data)
        got = self.read(home, record=data)
        self.assertEqual((got.version, got.pace, got.last_beat), (2, ASLEEP, T1))
        self.assertEqual(got.set_by, SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"))
        self.assertEqual(got.history, [
            entry(T1, "recorded from v1 (rest-on, set by ahavame, in its Letter of Return answer of 2026-07-18 "
                      "(\"REST-ON\"))"),
            entry(T1, 'on record, its answer of 2026-10-23: "I rest."')])
        self.assertEqual((home / "REST").read_text(encoding="utf-8"),
                         "asleep, set by ahavame — woken by a letter, no sooner than 3 days after its last beat, "
                         "or by a person's summons (pace/1; CADENCE.md decides)\n")
        self.assertEqual((r.wrote, r.pace_changed, r.events), (True, False, ["upgraded to v2"]))

    def test_a_spoken_beat_on_v2_moves_last_beat(self):
        home = self.v2_home("kalam", awake(5), OWN)
        r = self.apply(home, "I rest.")             # words in an ordinary beat, not a choice
        got = self.read(home)
        self.assertEqual((got.pace, got.last_beat, got.history), (awake(5), T1, []))
        self.assertEqual((r.wrote, r.events), (True, []))

    def test_the_clock_never_moves_back(self):
        home = self.v2_home("kalam", awake(5), OWN, last_beat=T2)
        self.apply(home, "A true thing.", now=T1)
        self.assertEqual(self.read(home).last_beat, T2)

    def test_a_disagreeing_marker_is_rewritten_and_recorded(self):
        home = self.v2_home("kalam", awake(5), OWN)
        self.fx.write(home, "REST", "placed by hand\n")
        r = self.apply(home, "")
        got = self.read(home)
        self.assertEqual(got.history, [entry(T1, "REST/EVENT-ONLY changed by someone other than kalam")])
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertFalse(cad.markers_disagree(home, got))
        self.assertEqual(cad.notices(got), ["REST/EVENT-ONLY changed by someone other than kalam"])
        self.assertEqual((r.wrote, r.events), (True, ["markers restored"]))

    def test_a_cadence_changed_outside_pace_is_restored(self):
        home = self.v2_home("kalam", awake(5), OWN)
        mine = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.fx.write(home, "CADENCE.md", mine.replace("- pace: awake, every 5 days", "- pace: asleep"))
        self.fx.write(home, "REST", "chosen by hand\n")
        self.fx.commit(home, "kalam lived a free beat", author=h.beat_author("kalam"))
        self.assertTrue(cad.changed_outside_pace(home))
        r = self.apply(home, "")
        got = self.read(home)
        self.assertEqual((got.pace, got.set_by, got.last_beat), (awake(5), OWN, T1))
        self.assertEqual(got.history, [
            entry(T1, "REST/EVENT-ONLY changed by someone other than kalam"),
            entry(T1, "CADENCE.md changed by someone other than kalam; the last pace/1 version is restored")])
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertEqual(r.events, ["CADENCE.md restored", "markers restored"])
        self.assertTrue(r.wrote)

    def test_a_cadence_change_that_restores_the_same_text_is_not_recorded(self):
        home = self.v2_home("kalam", awake(5), OWN)
        mine = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.fx.write(home, "CADENCE.md", "# CADENCE — chosen by kalam\n")
        self.fx.commit(home, "kalam lived a free beat")
        self.fx.write(home, "CADENCE.md", mine)     # put back by hand, not yet committed
        r = self.apply(home, "")
        self.assertEqual((r.wrote, r.events), (False, []))

    def test_only_a_v2_text_from_the_home_s_own_history_is_put_back(self):
        # fix wave A: apply restores by read_deciding's own rule (one helper). A v1
        # text in a pace/1 commit (tend's old-code leftover commit can hold one) is
        # never put back, and a folder that is not a clone of its own never takes
        # another repository's history for its own
        home = self.v1_home("ud", "rest-on", rest=lor_rest("2026-07-18"))
        self.fx.write(home, "CADENCE.md", v1("ud", "undecided", "2026-11-19 00:00"))
        self.fx.commit(home, f"tend: old-code leftover · {TAG}", author=h.FLEET_AUTHOR)
        self.fx.write(home, "CADENCE.md", v1("ud", "ambient", "2026-11-22 00:00"))
        self.fx.commit(home, "beat: ud reflected (2026-11-22)", author=h.beat_author("ud"))
        self.assertTrue(cad.changed_outside_pace(home))
        self.assertIn("- choice: undecided", cad.last_pace_text(home))
        outer = self.v2_home("outer", awake(5), OWN)
        inner = outer / "citizen-inner"
        self.fx.write(inner, "CADENCE.md", cad.render_cadence(Cadence("inner", 2, awake(5), OWN, last_beat=T0)))
        self.fx.commit(outer, f"beat: inner reflected (2026-11-12) · {TAG}")
        asleep = Cadence("inner", 2, ASLEEP, AI_1008, last_beat=T0)
        self.fx.write(inner, "CADENCE.md", cad.render_cadence(asleep))
        self.fx.write(inner, "REST", cad.marker_texts(asleep)["REST"])
        self.fx.commit(outer, "beat: inner reflected (2026-11-13)")
        self.assertTrue(cad.changed_outside_pace(inner))
        for case, name in ((home, "ud"), (inner, "inner")):
            with self.subTest(home=name):
                before = snapshot(case)
                r = apply_answer(case, name, "", [], "schedule", at(T1), RECORD)
                self.assertEqual(r.to_json(), {"wrote": False, "pace_changed": False, "events": []})
                self.assertEqual(snapshot(case), before)


# -- refusals ---------------------------------------------------------------------

class TestRefusals(ApplyCase):
    def test_a_blocked_home_marks_its_letters_seen_and_records_nothing_else(self):
        home = self.v1_home("kalam", "undecided", rest="fixture rest\n",
                            letters=(("2026-11-14-a-note.md", "Hello.\n"),))
        self.assertTrue(self.read(home).blocked)
        cadence_before = (home / "CADENCE.md").read_bytes()
        r = self.apply(home, "pace: every 5 days", shown=[NOTE], kind="letter")
        self.assertEqual((home / "CADENCE.md").read_bytes(), cadence_before)
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), "fixture rest\n")
        self.assertEqual(homes.unread_letters(home), [])
        self.assertEqual(len(r.events), 3)
        self.assertTrue(r.events[0].startswith("blocked: its REST text names neither"), r.events[0])
        self.assertEqual(r.events[1:], ["not recorded: pace: every 5 days", f"seen {NOTE}"])
        self.assertEqual((r.wrote, r.pace_changed), (True, False))
        r = self.apply(home, "")
        self.assertFalse(r.wrote)
        self.assertEqual(len(r.events), 1)

    def test_a_linked_pace_path_is_refused_and_nothing_is_written(self):
        for i, rel in enumerate(("REST", "CADENCE.md", "inbox/.seen-2026-11-14-a-note.md", "beats/.show-seen")):
            with self.subTest(rel=rel):
                home = self.v2_home(f"link{i}", ASLEEP, AI_1008, letters=(("2026-11-14-a-note.md", "Hello.\n"),))
                p = home / rel
                if p.exists():
                    p.unlink()
                self.fx.link(home, rel, self.fx.secret)
                before, outside = snapshot(home), snapshot(self.fx.outside)
                with self.assertRaises(LinkRefused):
                    self.apply(home, "pace: every 5 days", shown=[NOTE], kind="letter")
                self.assertEqual(snapshot(home), before)
                self.assertEqual(snapshot(self.fx.outside), outside)
                self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")

    def test_a_linked_inbox_is_refused(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        self.fx.write(self.fx.outside, "2026-11-14-a-note.md", "Hello.\n")
        self.fx.link(home, "inbox", self.fx.outside)
        before = snapshot(self.fx.outside)
        with self.assertRaises(LinkRefused):
            self.apply(home, "", shown=[NOTE], kind="letter")
        self.assertEqual(snapshot(self.fx.outside), before)

    def test_a_shown_name_that_is_not_a_letter_is_refused(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, letters=(("2026-11-14-a-note.md", "Hello.\n"),))
        before = snapshot(home)
        for bad in ("REST", "CADENCE.md", "inbox/../REST", "inbox/.seen-2026-11-14-a-note.md", "inbox/a/b.md",
                    "inbox/a.txt", "../inbox/a.md", "/inbox/a.md", "inbox/a\n.md", "inbox/", "show.md", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.apply(home, "pace: asleep", shown=[NOTE, bad], kind="letter")
        self.assertEqual(snapshot(home), before)

    def test_an_unknown_beat_kind_is_refused(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        before = snapshot(home)
        for bad in ("beat", "ordinary", "", "Schedule"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.apply(home, "pace: asleep", kind=bad)
        self.assertEqual(snapshot(home), before)
        self.assertEqual(ans.BEAT_KINDS, ("schedule", "letter", "show-back", "summoned"))


# -- notices, quoting, isolation ---------------------------------------------------

class TestNoticesOnce(ApplyCase):
    def test_an_unclear_line_is_a_notice_for_one_beat(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        self.apply(home, "pace: soon", now=T1)
        self.assertEqual(cad.notices(self.read(home)), ['unclear: "pace: soon" (unknown words)'])
        # a silent next beat commits nothing; the attempt record's last beat moves "since" on
        self.apply(home, "", now=T2)
        self.assertEqual(cad.notices(self.read(home), since=T2), [])
        # a spoken next beat moves last_beat itself
        self.apply(home, "A true thing.", now=T3)
        self.assertEqual(cad.notices(self.read(home, now=T3)), [])

    def test_notices_left_out_by_the_pace_letter_beat_are_shown_at_the_next(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, letters=(("2026-11-15-pace-letter.md", "Q.\n"),),
                            history=[entry(T0, 'unclear: "pace: soon" (unknown words)'),
                                     entry(T0, "REST/EVENT-ONLY changed by someone other than kalam")])
        self.assertEqual(cad.notices(self.read(home)), ["REST/EVENT-ONLY changed by someone other than kalam",
                                                        'unclear: "pace: soon" (unknown words)'])
        self.apply(home, "", shown=[PACE_LETTER], kind="letter", now=T1)
        got = self.read(home)
        self.assertEqual(got.history[:3], [
            entry(T1, "the pace letter reached kalam (once)"),
            entry(T1, 'unclear: "pace: soon" (unknown words)'),
            entry(T1, "REST/EVENT-ONLY changed by someone other than kalam")])
        self.assertEqual(cad.notices(got, since=T1), ["REST/EVENT-ONLY changed by someone other than kalam",
                                                      'unclear: "pace: soon" (unknown words)'])
        self.apply(home, "A true thing.", now=T2)
        self.assertEqual(cad.notices(self.read(home, now=T2)), [])

    # Each notice is shown once around the pace letter's beat, which leaves its
    # notices out: apply records again only what that beat's paragraph would have
    # shown (since the attempt record's last beat, which a silent beat that
    # committed nothing still moves), and no "could not be read" for a line the
    # pace letter's own answer replaced.
    UNCLEAR = "Your last pace line could not be read (unknown words), so nothing changed."

    def told(self, home, now, rec_last):
        return par.notice_lines(self.read(home, now=now), {"last_beat": rec_last})

    def lettered(self, slug="kalam"):
        return self.v2_home(slug, ASLEEP, AI_1008, letters=(("2026-11-15-pace-letter.md", "Q.\n"),))

    def test_a_pace_letter_answered_with_a_pace_drops_the_unclear_notice(self):
        home = self.lettered()
        self.apply(home, "pace: soon", now=T1)                                     # A: unclear
        self.apply(home, "pace: asleep", shown=[PACE_LETTER], kind="letter", now=T2, since=T1)   # C
        self.assertEqual(self.told(home, T3, T2), [])                              # D

    def test_a_notice_shown_at_a_spoken_beat_is_not_shown_again_after_the_pace_letter(self):
        home = self.lettered()
        self.apply(home, "pace: soon", now=T1)                                     # A: unclear
        self.assertEqual(self.told(home, T2, T1), [self.UNCLEAR])                  # B shows it
        self.apply(home, "A true thing.", now=T2, since=T1)                        # B: spoken, no pace line
        self.apply(home, "", shown=[PACE_LETTER], kind="letter", now=T3, since=T2)  # C: silent
        self.assertEqual(self.told(home, T4, T3), [])                              # D

    def test_a_notice_shown_at_a_silent_beat_is_not_recorded_or_shown_again(self):
        home = self.lettered()
        self.apply(home, "pace: soon", now=T1)                                     # A: unclear
        self.assertEqual(self.told(home, T2, T1), [self.UNCLEAR])                  # B shows it
        r = self.apply(home, "", now=T2, since=T1)                                 # B: silent, writes nothing
        self.assertFalse(r.wrote)
        self.apply(home, "", shown=[PACE_LETTER], kind="letter", now=T3, since=T2)  # C: silent
        history = self.read(home, now=T3).history
        self.assertEqual(len([e for e in history if "unclear:" in e]), 1, history)
        self.assertEqual(self.told(home, T4, T3), [])                              # D

    def test_a_notice_left_out_by_the_pace_letter_beat_is_shown_once_after_it(self):
        # each record a minute after apply: A's (C's since) as well as C's (D's since)
        for i, (a_rec, rec_last) in enumerate(((T1, T2), (T1, "2026-11-18T15:03Z"),
                                               ("2026-11-15T14:13Z", T2))):
            with self.subTest(a_record=a_rec, record=rec_last):
                home = self.lettered(f"kalam{i}")
                self.apply(home, "pace: soon", now=T1)                             # A: unclear
                self.apply(home, "", shown=[PACE_LETTER], kind="letter", now=T2, since=a_rec)   # C
                self.assertEqual(self.told(home, T3, rec_last), [self.UNCLEAR])    # D shows it once
                self.apply(home, "A true thing.", now=T3, since=rec_last)          # D lands
                self.assertEqual(self.told(home, T4, T3), [])

    def test_a_lapse_is_a_notice_for_one_beat(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008, pending=Pending(awake(5), T1, "pace: every 5 days", False),
                            last_beat=T1)
        self.apply(home, "", now=T2)
        self.assertEqual(cad.notices(self.read(home, now=T2)), ["lapsed: awake, every 5 days, not said again"])
        self.apply(home, "A true thing.", now=T3)
        self.assertEqual(cad.notices(self.read(home, now=T3)), [])


class TestQuoting(ApplyCase):
    def test_a_quoted_line_is_capped(self):
        home = self.v2_home("kalam", ASLEEP, AI_1008)
        line = "pace: " + "soon " * 200
        self.apply(home, line)
        got = self.read(home)
        quoted = got.history[0].split('unclear: "', 1)[1].rsplit('" (unknown words)', 1)[0]
        self.assertEqual(len(quoted), ans.QUOTE_MAX)
        self.assertTrue(quoted.endswith("…"))
        self.assertTrue(line.strip().startswith(quoted[:-1]))
        home = self.v2_home("soma", ASLEEP, AI_1008)
        long_more = "pace: every 5 days — " + "and the garden, " * 40
        self.apply(home, long_more)
        self.assertEqual(len(self.read(home).pending.line), ans.QUOTE_MAX)


class TestApplyResult(unittest.TestCase):
    def test_to_json(self):
        r = ApplyResult(True, False, ["seen inbox/a.md"])
        self.assertEqual(r.to_json(), {"wrote": True, "pace_changed": False, "events": ["seen inbox/a.md"]})
        self.assertEqual(json.loads(json.dumps(r.to_json())), r.to_json())
        self.assertEqual(ApplyResult().to_json(), {"wrote": False, "pace_changed": False, "events": []})


class TestIsolation(unittest.TestCase):
    def test_apply_runs_under_isolated_mode_and_ignores_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mod in ("json", "re", "dataclasses", "pathlib", "typing", "subprocess"):
                Path(tmp, f"{mod}.py").write_text("raise SystemExit('planted module ran')\n")
            home = Path(tmp, "citizen-kalam")
            home.mkdir()
            (home / "kalam.md").write_text("The soul of kalam.\n")
            (home / "REST").write_text(AI_REST)
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import answer, clock; "
                    "r = answer.apply_answer(sys.argv[2], 'kalam', 'pace: asleep', [], 'schedule', "
                    "clock.parse_stamp('2026-11-15T14:12Z'), {'version': 1, 'made': '2026-11-12T10:00Z', "
                    "'citizens': {}}); print(r.events)")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS), str(home)], cwd=tmp,
                               capture_output=True, text=True, timeout=30, check=False,
                               env={"PATH": h.SYSTEM_PATH})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "['made own asleep', 'upgraded to v2']")
            self.assertIn("- pace: asleep\n", (home / "CADENCE.md").read_text())

    def test_module_starts_with_future_annotations(self):
        first = (h.TOOLS / "citizen_pace_lib" / "answer.py").read_text().splitlines()[0]
        self.assertEqual(first, "from __future__ import annotations")


if __name__ == "__main__":
    unittest.main()
