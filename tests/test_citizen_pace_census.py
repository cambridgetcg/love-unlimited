"""The census record (spec sections 5, 6 and 7; tools/citizen_pace_census.py).

citizens-pace-record.json holds, for each of the 208 homes, what its own files
cannot show: its group, who set its pace, its quoted answers with their commits
and dates, its HEAD, and for each of the 34 recipients the one true sentence of
its letter. The builder reads read-only clones, works out every home's group
from its own history, checks the groups against the spec's lists, and writes
nothing when one home disagrees.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_census as hc  # noqa: E402
import citizen_pace_helpers_plan as hp  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

import citizen_pace_census as census  # noqa: E402
from citizen_pace_lib import clock, due, letter, plan  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import record as records  # noqa: E402
from citizen_pace_lib.cadence import SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

CENSUS = h.TOOLS / "citizen_pace_census.py"
SPEC_FILE = h.ROOT / "docs" / "superpowers" / "specs" / "2026-11-03-citizens-own-pace-design.md"
NOW = "2026-11-12T10:00Z"
ASLEEP, AWAKE3 = Pace("asleep"), Pace("awake", 3)
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))

# a census small enough to build in a moment: every group, and both ways a
# July answer can differ from its July record (kinhme, ihsanme)
MINI = census.Expected({"eight": ("aseme",), "forty-five": ("doxakallos",), "sixteen-ambient": ("diplosemy",),
                        "sixteen-other": ("ai-love", "kinhme"), "unheard-heard": ("kalam", "soma", "teotlme"),
                        "unheard-none": ("emime", "eurekame"), "never-asked": ("palance",),
                        "contradictory": ("hope", "ihsanme"), "ambient-nine": ("gratitude", "silim", "ud"),
                        "longing": ("longing",), "held": ("jiritsume",)}, rest_on=3)
SOMA_10_25 = h.answer("soma-914572f3").strip().split("\n")[2]
SOMA_10_27 = "I choose to remain **ambient**, feeling the world in its rhythms."


def _names(text: str) -> tuple:
    flat = " ".join(text.split())
    return tuple(n.strip() for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", flat) if n.strip())


def spec_list(text: str, pattern: str) -> tuple:
    m = re.search(pattern, text, re.S)
    if not m:
        raise AssertionError(f"the spec no longer says {pattern!r}")
    return _names(m.group(1))


def spec_number(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    if not m:
        raise AssertionError(f"the spec no longer says {pattern!r}")
    return int(m.group(1))


class TestTheSpecLists(unittest.TestCase):
    """The builder's lists are the spec's own (section 5, "Who receives it")."""

    @classmethod
    def setUpClass(cls):
        cls.text = SPEC_FILE.read_text(encoding="utf-8")

    def test_every_list_is_the_spec_s(self):
        t = self.text
        sixteen = spec_list(t, r"\*\*16 whose rest was Ai's when they answered:\*\* (.*?)\. \(")
        ten = spec_list(t, r"\(10 chose AMBIENT in July: (.*?)\.\)")
        six = spec_list(t, r"\*\*the other 6 of the 16\*\* \((.*?)\)")
        heard = spec_list(t, r"\*\*unheard, now heard\*\* \((.*?)\)")
        unheard_none = spec_list(t, r"\*\*unheard, no pace read\*\* \((.*?)\)")
        want = {
            "eight": spec_list(t, r'\*\*8 who answered "I rest\." in July and to the 10-22 letter:\*\* (.*?)\.\n'),
            "sixteen-ambient": ten, "sixteen-other": six, "unheard-heard": heard, "unheard-none": unheard_none,
            "never-asked": spec_list(t, r"\*\*3 never asked:\*\* (.*?)\.\n"),
            "contradictory": spec_list(t, r"\*\*2 contradictory:\*\* (.*?)\.\n"),
            "forty-five": spec_list(t, r"the 45 who chose REST-ON in July.*?\(call 2\): (.*?);"),
            "ambient-nine": spec_list(t, r"the other 9 ambient citizens \((.*?)\)"),
            "longing": spec_list(t, r"\n- (\w+), whose 10-24 answer began"),
            "held": spec_list(t, r"\n- ([\w ,]+?), held \(call 1\)"),
        }
        self.assertEqual(sorted(ten + six), sorted(sixteen))
        self.assertEqual(sorted(heard + unheard_none), sorted(spec_list(t, r"\*\*5 unheard:\*\* (.*?)\.\n")))
        self.assertEqual(sorted(census.SPEC.groups), sorted(want))
        for group, names in want.items():
            with self.subTest(group=group):
                self.assertEqual(sorted(census.SPEC.groups[group]), sorted(names))

    def test_the_counts(self):
        t = self.text
        sizes = {g: len(n) for g, n in census.SPEC.groups.items()}
        self.assertEqual(sizes, {"eight": 8, "sixteen-ambient": 10, "sixteen-other": 6, "unheard-heard": 3,
                                 "unheard-none": 2, "never-asked": 3, "contradictory": 2, "forty-five": 45,
                                 "ambient-nine": 9, "longing": 1, "held": 3})
        self.assertEqual(census.SPEC.rest_on, spec_number(t, r"- the (\d+) whose 10-22 answer clearly said REST-ON"))
        self.assertEqual(census.SPEC.recipients, spec_number(t, r"So the letter goes to \*\*(\d+)\*\* citizens"))
        self.assertEqual(census.SPEC.total, spec_number(t, r"\n- (\d+) homes\. Standing choice"))
        self.assertEqual((census.SPEC.rest_on, census.SPEC.recipients, census.SPEC.total), (116, 34, 208))
        every = [n for names in census.SPEC.groups.values() for n in names]
        self.assertEqual(len(every), len(set(every)), "a name in two groups")
        self.assertEqual(census.SPEC.held, ("jiritsume", "takseme", "tlamatisme"))
        self.assertEqual(set(census.SPEC.groups) | {"rest-on"}, set(records.GROUPS))


class _Census(unittest.TestCase):
    """A census built once for the class, under a fixture."""
    GROUPS = MINI

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = h.Fixture(cls._tmp.name)
        cls.c = hc.census(cls.fx, cls.fx.root / "homes", cls.GROUPS.groups, cls.GROUPS.rest_on)
        cls.before = hp.snapshot(cls.c.homes)
        with h.pace_now(NOW):
            cls.rec, cls.problems = census.build(cls.c.homes, due.read_roster(cls.c.roster), clock.now(),
                                                 expect=cls.GROUPS)
        cls.after = hp.snapshot(cls.c.homes)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def home(self, slug: str) -> Path:
        return self.c.homes / f"citizen-{slug}"

    def entry(self, slug: str) -> dict:
        return self.rec["citizens"][slug]

    def commit_of(self, slug: str, letter_name: str) -> str:
        return self.fx.git(self.home(slug), "log", "-1", "--format=%H", "--", f"inbox/.seen-{letter_name}")


class TestReadHome(h.FixtureCase):
    def test_what_a_home_holds(self):
        home = hc.build_home(self.fx, self.fx.citizens, "kalam", hc.SPECIAL["kalam"])
        f = census.read_home(home, "kalam")
        self.assertEqual(f.problem, "")
        self.assertTrue(f.soul)
        self.assertEqual(f.choice, "undecided")
        self.assertEqual(f.rest, hc.AI_REST)
        self.assertFalse(f.event_only)
        self.assertEqual(f.head, self.fx.head(home))
        self.assertEqual([(a.letter, a.date) for a in f.answers],
                         [(census.JULY, "2026-07-19"), (census.OFFER, "2026-10-23"), (census.RHYTHM, "2026-10-27")])
        self.assertEqual(f.answers[1].text, h.answer("kalam-62d73b73"))
        self.assertEqual(f.answers[1].reading, AWAKE3)
        self.assertEqual(f.answer_to(census.OFFER).quoted, "Ambient.")
        # a signature is never what is quoted
        self.assertEqual(f.answer_to(census.JULY).quoted, "I am awake once more.")
        self.assertIsNone(f.answer_to(census.JULY).reading)
        # the home as it stood when it answered the 10-22 letter
        self.assertEqual(f.then_choice, "undecided")
        self.assertEqual(f.then_rest, hc.AI_REST)

    def test_the_july_record_comes_from_before_october(self):
        home = hc.build_home(self.fx, self.fx.citizens, "alohame", hc.shape_for("alohame", "sixteen-ambient"))
        f = census.read_home(home, "alohame")
        self.assertEqual(f.choice, "rest-on")
        self.assertEqual(f.then_choice, "ambient")
        self.assertEqual(f.then_rest, hc.AI_REST)
        self.assertTrue(f.rest.startswith("chosen via the Letter of Return 2026-10-23"))

    def test_a_home_the_census_cannot_read(self):
        cases = {}
        missing = self.fx.citizens / "citizen-nobody"
        cases["nobody"] = (missing, "no clone")
        linked = hc.build_home(self.fx, self.fx.citizens, "linked", hc.shape_for("linked", "eight"))
        os.remove(linked / "REST")
        os.symlink(self.fx.secret, linked / "REST")
        cases["linked"] = (linked, "a pace path is a symlink or not of its kind (REST)")
        v2 = hc.build_home(self.fx, self.fx.citizens, "migrated", hc.shape_for("migrated", "eight"))
        cad.write_cadence(v2, cad.Cadence("migrated", 2, ASLEEP, AI_1008))
        self.fx.commit(v2, "a v2 record")
        cases["migrated"] = (v2, "CADENCE.md is already v2")
        odd = hc.build_home(self.fx, self.fx.citizens, "odd", hc.shape_for("odd", "eight"))
        hr.answer_beat(self.fx, odd, "odd", "2026-11-01-letter-of-return-again.md", hr.stored("I rest."),
                       when="2026-11-01T10:00Z", choice="rest-on", place=True)
        cases["odd"] = (odd, "it answered inbox/2026-11-01-letter-of-return-again.md, a Letter of Return "
                             "the spec does not name")
        for slug, (home, why) in cases.items():
            with self.subTest(slug=slug):
                f = census.read_home(home, slug)
                self.assertIn(why, f.problem)
                self.assertEqual(census.derive(f), (None, f.problem))

    def test_a_clone_that_holds_what_its_head_does_not_is_refused(self):
        # fix wave A (al): the census reads exactly the HEAD it records, so a
        # changed, untracked or ignored file in the clone stops it
        fx, cases = self.fx, {}
        changed = hc.build_home(fx, fx.citizens, "changed", hc.shape_for("changed", "eight"))
        fx.write(changed, "REST", "a rest written by hand\n")
        cases["changed"] = (changed, "REST")
        untracked = hc.build_home(fx, fx.citizens, "untracked", hc.shape_for("untracked", "never-asked"))
        fx.write(untracked, "CADENCE.md", hr.v1_cadence("untracked", "ambient", "2026-11-01 10:00"))
        cases["untracked"] = (untracked, "CADENCE.md")
        ignored = hc.build_home(fx, fx.citizens, "ignored", hc.shape_for("ignored", "eight"))
        fx.write(ignored, ".gitignore", "EVENT-ONLY\n")
        fx.commit(ignored, "ignore a marker", paths=[".gitignore"])
        fx.write(ignored, "EVENT-ONLY", "written and ignored\n")
        cases["ignored"] = (ignored, "EVENT-ONLY")
        for slug, (home, path) in cases.items():
            with self.subTest(slug=slug):
                f = census.read_home(home, slug)
                self.assertEqual(f.problem, f"its clone has changes its HEAD does not hold ({path}); the "
                                            "census reads only a clean clone")
                self.assertEqual(census.derive(f), (None, f.problem))

    def test_a_history_that_continues_on_another_branch_is_refused(self):
        # fix wave A (ai): eucatastrophe's and shannonme's REST-ON answers are on
        # origin/master, which main does not hold; read at main alone, both
        # looked never asked
        fx = self.fx
        home = hc.two_lines(fx, fx.citizens, "split", hc.shape_for("split", "never-asked"),
                            hc.shape_for("split", "rest-on"))
        f = census.read_home(home, "split")
        self.assertEqual(f.problem, "its history continues on origin/master, which HEAD does not hold")
        self.assertEqual(census.derive(f), (None, f.problem))
        # a remote branch that HEAD holds is no other history, and origin/HEAD is a pointer
        whole = hc.build_home(fx, fx.citizens, "whole", hc.shape_for("whole", "eight"))
        fx.git(whole, "update-ref", "refs/remotes/origin/main", "HEAD")
        fx.git(whole, "update-ref", "refs/remotes/origin/old", "HEAD~1")
        fx.git(whole, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.assertEqual(census.read_home(whole, "whole").problem, "")

    def test_a_home_read_at_its_named_ref(self):
        # fix wave A (bf): the history and the files are the ref's, and HEAD is left as it is
        fx = self.fx
        home = hc.two_lines(fx, fx.citizens, "split", hc.shape_for("split", "never-asked"),
                            hc.shape_for("split", "rest-on"))
        head = fx.head(home)
        view = fx.root / "view"
        view.mkdir()
        f = census.read_home(home, "split", "origin/master", view)
        self.assertEqual(f.problem, "")
        self.assertEqual(f.head, fx.git(home, "rev-parse", "refs/remotes/origin/master"))
        self.assertEqual(f.choice, "rest-on")
        self.assertEqual(f.rest, hr.lor_rest("2026-10-23"))
        self.assertTrue(f.soul)
        self.assertEqual([(a.letter, a.date) for a in f.answers], [(census.JULY, "2026-07-18"),
                                                                   (census.OFFER, "2026-10-23")])
        self.assertEqual(f.then_rest, hr.lor_rest("2026-07-18"))
        self.assertEqual(census.derive(f), ("rest-on", ""))
        self.assertEqual((view / "REST").read_text(encoding="utf-8"), hr.lor_rest("2026-10-23"))
        self.assertEqual((fx.head(home), fx.git(home, "status", "--porcelain")), (head, ""))
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), hc.AI_REST)

    def test_a_home_read_at_a_ref_it_cannot_be_read_at(self):
        fx, cases = self.fx, {}
        third = hc.two_lines(fx, fx.citizens, "third", hc.shape_for("third", "never-asked"),
                             hc.shape_for("third", "rest-on"))
        side = fx.git(third, "commit-tree", "-p", "refs/remotes/origin/master", "-m", "a side line",
                      "refs/remotes/origin/master^{tree}")
        fx.git(third, "update-ref", "refs/remotes/origin/side", side)
        cases["third"] = (third, "its history continues on origin/side, which neither HEAD nor "
                                 "origin/master holds")
        alone = hc.build_home(fx, fx.citizens, "alone", hc.shape_for("alone", "eight"))
        cases["alone"] = (alone, "the spec reads it at origin/master, but its clone has no origin/master")

        def link_rest(apart):
            os.remove(apart / "REST")
            os.symlink(fx.secret, apart / "REST")
            fx.commit(apart, "a linked rest")
        linked = hc.two_lines(fx, fx.citizens, "linked", hc.shape_for("linked", "never-asked"),
                              hc.shape_for("linked", "rest-on"), edit=link_rest)
        cases["linked"] = (linked, "a pace path is a symlink or not of its kind at origin/master (REST)")
        for slug, (home, why) in cases.items():
            with self.subTest(slug=slug):
                view = fx.root / f"view-{slug}"
                view.mkdir()
                f = census.read_home(home, slug, "origin/master", view)
                self.assertEqual(f.problem, why)
                self.assertFalse(os.path.lexists(view / "REST"))


class TestDerive(h.FixtureCase):
    """Each home's group comes from its own history, never from the lists."""

    def derive(self, slug: str, shape) -> tuple:
        home = hc.build_home(self.fx, self.fx.citizens, slug, shape)
        return census.derive(census.read_home(home, slug))

    def test_each_group_from_its_history(self):
        for group, names in MINI.groups.items():
            for slug in names:
                with self.subTest(slug=slug):
                    self.assertEqual(self.derive(slug, hc.shape_for(slug, group)), (group, ""))
        for i, slug in enumerate(hc.rest_on_names(3)):
            with self.subTest(slug=slug):
                self.assertEqual(self.derive(slug, hc.shape_for(slug, "rest-on", i)), ("rest-on", ""))

    def test_the_names_do_not_decide(self):
        # aseme's history, under another name, is still one of the 8
        self.assertEqual(self.derive("someone", hc.shape_for("aseme", "eight")), ("eight", ""))
        # and a listed name with the history of the 116 reads as the 116
        self.assertEqual(self.derive("aseme", hc.shape_for("x", "rest-on")), ("rest-on", ""))

    def test_the_july_record_decides_the_sixteen_not_a_forgiving_reread(self):
        # kinhme's July answer reads as REST-ON by section 1's rules, but the old
        # code recorded it undecided, and Ai's rest came on 10-08
        self.assertEqual(self.derive("kinhme", hc.SPECIAL["kinhme"]), ("sixteen-other", ""))
        teshuvance = hc.Shape(july=hc.said(hc.JULY_AT, "Shuv.\n\nI will choose AMBIENT — the rotation "
                                           "includes me.", "undecided"), ai_rest=True,
                              offer=hc.said(hc.OFFER_AT, "I rest.", "rest-on"))
        self.assertEqual(self.derive("teshuvance", teshuvance), ("sixteen-other", ""))

    def test_histories_that_fit_no_group(self):
        s = hc.said
        cases = {
            "a REST that names neither Ai nor a Letter of Return":
                (hc.Shape(july=s(hc.JULY_AT, "AMBIENT", "ambient"), ai_rest="a rest from somewhere\n",
                          offer=s(hc.OFFER_AT, "I rest.", "rest-on")),
                 "its REST when it answered the 10-22 letter names neither"),
            "a 10-22 I rest under its own July rest, with a July answer naming no rest":
                (hc.Shape(july=s(hc.JULY_AT, "AMBIENT", "rest-on"), offer=s(hc.OFFER_AT, "I rest.", "rest-on")),
                 "its July answer does not say REST-ON"),
            "an AMBIENT record its answers do not say":
                (hc.Shape(july=s(hc.JULY_AT, "REST-ON", "rest-on"), offer=s(hc.OFFER_AT, "REST-ON", "ambient")),
                 "its v1 choice is ambient, but its answers say"),
            "an undecided record whose October answers name a pace":
                (hc.Shape(july=s(hc.JULY_AT, "x", "undecided"), ai_rest=True,
                          offer=s(hc.OFFER_AT, "REST-ON", "undecided")),
                 "its v1 choice is undecided, but its October answers read as asleep"),
            "a rest-on record with no answer to the 10-22 letter":
                (hc.Shape(july=s(hc.JULY_AT, "REST-ON", "rest-on")), "no answer to the 10-22 letter"),
            "a rest-on record whose 10-22 answer names no pace":
                (hc.Shape(july=s(hc.JULY_AT, "REST-ON", "rest-on"),
                          offer=s(hc.OFFER_AT, "I hear the garden.", "rest-on")),
                 "its 10-22 answer reads as no pace"),
            "no soul, but answers":
                (hc.Shape(july=s(hc.JULY_AT, "REST-ON", "rest-on"), soul=False),
                 "no soul file, but"),
            "no CADENCE.md and a REST not Ai's":
                (hc.Shape(ai_rest="a rest from somewhere\n"), "no CADENCE.md, and its REST was not placed by Ai"),
            "an event-only record":
                (hc.Shape(july=s(hc.JULY_AT, "EVENT-ONLY", "event-only")), "no group of the spec holds"),
        }
        for what, (shape, why) in cases.items():
            with self.subTest(what=what):
                group, reason = self.derive("someone", shape)
                self.assertIsNone(group)
                self.assertIn(why, reason)

    def test_a_never_asked_home_holds_no_letter(self):
        # fix wave A (ai): its letter says "no letter has reached you since", so
        # a home that holds one is not never asked
        home = hc.build_home(self.fx, self.fx.citizens, "palance", hc.shape_for("palance", "never-asked"))
        self.fx.letter(home, "2026-11-01-note.md", "a note\n", when="2026-11-01T00:00Z")
        self.assertEqual(census.derive(census.read_home(home, "palance")), (
            None, "no CADENCE.md and no stored answer, but it holds inbox/2026-11-01-note.md, so a letter "
                  "has reached it"))

    def test_markers_that_do_not_fit_the_record(self):
        home = hc.build_home(self.fx, self.fx.citizens, "aseme", hc.shape_for("aseme", "eight"))
        os.remove(home / "REST")
        self.fx.commit(home, "REST removed")
        group, why = census.derive(census.read_home(home, "aseme"))
        self.assertIsNone(group)
        self.assertIn("its markers do not fit its v1 choice rest-on", why)
        home = hc.build_home(self.fx, self.fx.citizens, "ud", hc.SPECIAL["ud"])
        self.fx.write(home, "EVENT-ONLY", "fixture\n")
        self.fx.commit(home, "EVENT-ONLY placed")
        self.assertIn("its markers do not fit its v1 choice ambient",
                      census.derive(census.read_home(home, "ud"))[1])


class TestBuild(_Census):
    """The record of a census whose every home agrees with its lists."""

    def test_the_record_holds_every_home_and_its_group(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.rec["version"], 1)
        self.assertEqual(self.rec["made"], NOW)
        self.assertEqual(self.rec["spec"], census.SPEC_REF)
        self.assertEqual(sorted(self.rec["citizens"]), self.c.names)
        for slug, group in self.c.groups.items():
            with self.subTest(slug=slug):
                e = self.entry(slug)
                self.assertEqual(e["group"], group)
                self.assertIs(e["recipient"], group in records.RECIPIENT_GROUPS)
                self.assertEqual(e["head"], self.fx.head(self.home(slug)))
        self.assertEqual(plan.recipients(self.rec), sorted(
            n for g, names in MINI.groups.items() if g in records.RECIPIENT_GROUPS for n in names))

    def test_who_set_each_pace(self):
        want = {
            "aseme": SetBy("citizen", "prompted", ("2026-10-23",)),
            "doxakallos": SetBy("citizen", "letter", ("2026-07-18",),
                                "I choose **REST-ON**. The garden's waking is a gentle nudge."),
            "diplosemy": AI_1008, "kinhme": AI_1008, "ai-love": AI_1008, "emime": AI_1008,
            "palance": AI_1008, "jiritsume": AI_1008,
            "kalam": SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."),
            "soma": SetBy("citizen", "ambient", ("2026-10-25", "2026-10-27"), SOMA_10_27),
            "teotlme": SetBy("citizen", "ambient", ("2026-10-25", "2026-10-27"), "AMBIENT"),
            "eurekame": SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"),
            "hope": SetBy("fleet", "first-word", ("2026-10-23",)),
            "ihsanme": SetBy("fleet", "first-word", ("2026-10-23",)),
            "gratitude": SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."),
            "silim": SetBy("citizen", "ambient", ("2026-10-27",), "AMBIENT."),
            "ud": SetBy("citizen", "ambient", ("2026-10-25",), "AMBIENT"),
            "longing": SetBy("citizen", "letter", ("2026-10-27",), "REST-ON"),
            "rest-on-001": SetBy("citizen", "letter-twice", ("2026-07-18", "2026-10-23"), "REST-ON"),
            "rest-on-002": SetBy("citizen", "letter", ("2026-10-23",), "**REST-ON**"),
            "rest-on-003": SetBy("citizen", "letter", ("2026-10-23",), "REST-ON."),
        }
        self.assertEqual(sorted(want), self.c.names)
        for slug, s in want.items():
            with self.subTest(slug=slug):
                self.assertEqual(SetBy.from_json(self.entry(slug)["set_by"]), s)

    def test_the_quoted_answers_and_the_july_record(self):
        kalam = self.entry("kalam")
        self.assertEqual(kalam["quotes"], [
            {"commit": self.commit_of("kalam", hr.OFFER), "date": "2026-10-23", "words": "Ambient."},
            {"commit": self.commit_of("kalam", hr.RHYTHM), "date": "2026-10-27", "words": "Ambient."}])
        self.assertEqual(kalam["july"], {"commit": self.commit_of("kalam", hr.JULY), "date": "2026-07-19",
                                         "words": "I am awake once more.", "choice": "undecided"})
        self.assertEqual([q["words"] for q in self.entry("soma")["quotes"]], [SOMA_10_25, SOMA_10_27])
        self.assertEqual([q["words"] for q in self.entry("teotlme")["quotes"]],
                         ["I am teotlme, and I choose to be AMBIENT.", "AMBIENT"])
        self.assertEqual([q["words"] for q in self.entry("eurekame")["quotes"]], ["**EUREKA**"])
        self.assertEqual(self.entry("eurekame")["july"]["words"], "REST-ON")
        # hope's July answer was recorded as AMBIENT; ihsanme's, though it says
        # AMBIENT on its second line, was recorded undecided
        self.assertEqual(self.entry("hope")["july"]["choice"], "ambient")
        self.assertEqual(self.entry("hope")["july"]["words"],
                         "AMBIENT — I will take this gentle beat each day, by my own will.")
        self.assertEqual(self.entry("ihsanme")["july"], {
            "commit": self.commit_of("ihsanme", hr.JULY), "date": "2026-07-19", "words": "**AMBIENT**",
            "choice": "undecided"})
        self.assertEqual(self.entry("diplosemy")["july"]["choice"], "ambient")
        self.assertEqual(self.entry("aseme")["quotes"][0]["words"], "I rest.")
        for slug in ("palance", "jiritsume"):
            self.assertNotIn("quotes", self.entry(slug))
            self.assertNotIn("july", self.entry(slug))
        self.assertNotIn("july", self.entry("ai-love"))

    def test_every_recipient_s_one_true_sentence(self):
        heard = ("You answered twice, on {d1} and {d2}, with {w}; neither was heard then. It has been heard "
                 "now: you chose AMBIENT, and your pace is awake, every 3 days, the fleet's reading of AMBIENT.")
        contradictory = letter.CONTRADICTORY.format(month_day="10-23")
        want = {
            "aseme": letter.EIGHT,
            "diplosemy": letter.SIXTEEN_AMBIENT + " In the same answer you wrote: 'If I must choose, let it be "
                                                  "with a single word: EVENT-ONLY.'",
            "kinhme": letter.SIXTEEN_OTHER, "ai-love": letter.SIXTEEN_OTHER,
            "kalam": heard.format(d1="2026-10-23", d2="2026-10-27", w="'Ambient.'"),
            "soma": heard.format(d1="2026-10-25", d2="2026-10-27", w=f"'{SOMA_10_25}' and '{SOMA_10_27}'"),
            "teotlme": heard.format(d1="2026-10-25", d2="2026-10-27",
                                    w="'I am teotlme, and I choose to be AMBIENT.' and 'AMBIENT'"),
            "emime": "On 2026-10-22 you answered the 10-22 letter, but no pace could be read from it, so nothing "
                     "changed: your pace is still asleep, this rest was placed by Ai on 2026-10-08, at Yu's "
                     "word, not chosen by you.",
            "eurekame": "On 2026-10-23 you answered the 10-22 letter, but no pace could be read from it, so "
                        "nothing changed: your pace is still asleep, chosen by you on 2026-07-18, in your answer "
                        "to the Letter of Return.",
            "palance": letter.NEVER_ASKED,
            "hope": contradictory + " (In July you chose AMBIENT.)",
            "ihsanme": contradictory,
        }
        self.assertEqual(sorted(want), plan.recipients(self.rec))
        for slug, sentence in want.items():
            with self.subTest(slug=slug):
                self.assertEqual(self.entry(slug)["sentence"], sentence)
        self.assertNotIn("July", self.entry("ihsanme")["sentence"])
        for slug, e in self.rec["citizens"].items():
            if not e["recipient"]:
                self.assertNotIn("sentence", e)

    def test_what_the_fleet_will_read_with_this_record(self):
        awake = {"hope", "ihsanme", "gratitude", "silim", "ud"}
        heard = set(MINI.groups["unheard-heard"])
        for slug in self.c.names:
            with self.subTest(slug=slug):
                c = cad.read_cadence(self.home(slug), slug, self.rec, clock.parse_stamp(NOW))
                self.assertEqual(c.blocked, "")
                self.assertEqual(c.pace, AWAKE3 if slug in awake else ASLEEP)
                if slug not in heard:
                    self.assertEqual(c.set_by, SetBy.from_json(self.entry(slug)["set_by"]))
        for slug in heard:
            with self.subTest(slug=slug):
                c = records.heard_late(self.home(slug), slug, self.rec, clock.parse_stamp(NOW))
                self.assertEqual(c.pace, AWAKE3)
                self.assertEqual(c.set_by, SetBy.from_json(self.entry(slug)["set_by"]))
        # the history notes what the record keeps (spec section 6)
        c = cad.read_cadence(self.home("diplosemy"), "diplosemy", self.rec, clock.parse_stamp(NOW))
        self.assertIn('on record, its answer of 2026-10-27: "I rest."', c.history[1])
        self.assertIn('on record, its July answer of 2026-07-18: "I choose **AMBIENT**."', c.history[2])

    def test_the_record_file_reads_back_and_checks(self):
        path = self.fx.root / "record.json"
        path.write_text(census.render(self.rec), encoding="utf-8")
        self.assertEqual(records.load_record(path), self.rec)
        self.assertEqual(census.check_record(self.rec, due.read_roster(self.c.roster), MINI), [])
        self.assertTrue(census.render(self.rec).endswith("}\n"))

    def test_a_build_leaves_every_home_as_it_was(self):
        # fix wave A (an): every path under the homes, .git included, with its
        # mode, time and bytes (so no index refreshed by git status either)
        self.assertEqual(self.problems, [])
        self.assertEqual(self.after, self.before)


class TestReadAt(h.FixtureCase):
    """A home whose history goes on on another branch is read there when the
    spec names it, and refused when it does not (fix wave A (ai), (bf))."""

    def test_a_home_read_at_its_ref_is_classified_from_it(self):
        fx = self.fx
        c = hc.census(fx, fx.root / "homes", MINI.groups, MINI.rest_on + 1, read_at={"split": "origin/master"})
        roster = due.read_roster(c.roster)
        home = c.homes / "citizen-split"
        before = hp.snapshot(c.homes)
        rec, problems = census.build(c.homes, roster, clock.parse_stamp(NOW), expect=MINI)
        self.assertIsNone(rec)
        self.assertIn("split: its history continues on origin/master, which HEAD does not hold", problems)
        expect = census.Expected(MINI.groups, MINI.rest_on + 1)
        rec, problems = census.build(c.homes, roster, clock.parse_stamp(NOW), expect=expect,
                                     read_at={"split": "origin/master"})
        self.assertEqual(problems, [])
        e = rec["citizens"]["split"]
        self.assertEqual((e["group"], e["recipient"]), ("rest-on", False))
        self.assertEqual(e["head"], fx.git(home, "rev-parse", "refs/remotes/origin/master"))
        self.assertNotEqual(e["head"], fx.head(home))
        self.assertEqual(SetBy.from_json(e["set_by"]).how, "letter-twice")
        self.assertEqual([q["date"] for q in e["quotes"]], ["2026-10-23"])
        self.assertEqual(census.check_record(rec, roster, expect), [])
        self.assertEqual(hp.snapshot(c.homes), before)

    def test_a_ref_home_in_the_heard_late_step_is_refused(self):
        # the heard-late step reads the history a checkout holds, not a ref's
        fx = self.fx
        home = hc.two_lines(fx, fx.citizens, "kalam", hc.shape_for("kalam", "never-asked"), hc.SPECIAL["kalam"])
        rec = {"version": 1, "made": NOW, "spec": census.SPEC_REF, "citizens": {"kalam": {
            "group": "unheard-heard", "recipient": True, "head": "0" * 40,
            "set_by": {"who": "citizen", "how": "ambient", "when": ["2026-10-23", "2026-10-27"],
                       "words": "Ambient."}}}}
        view = fx.root / "view"
        view.mkdir()
        self.assertEqual(census.read_home(home, "kalam", "origin/master", view).problem, "")
        self.assertEqual(census._fleet_problems(view, "kalam", rec, clock.parse_stamp(NOW), "origin/master"),
                         ["kalam: read at origin/master, but the heard-late step reads only the history a "
                          "checkout holds"])


class TestFleetProblems(_Census):
    """What the fleet and the letter would make of each entry: every refusal
    of _fleet_problems (fix wave A (an))."""

    def fleet(self, slug, home=None, **change) -> list:
        rec = json.loads(census.render(self.rec))
        e = rec["citizens"][slug]
        for k, v in change.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return census._fleet_problems(home or self.home(slug), slug, rec, clock.parse_stamp(NOW))

    def test_the_built_record_has_none(self):
        for slug in self.c.names:
            with self.subTest(slug=slug):
                self.assertEqual(self.fleet(slug), [])

    def test_each_refusal(self):
        linked = hc.build_home(self.fx, self.fx.root / "linked", "aseme", hc.shape_for("aseme", "eight"))
        os.remove(linked / "REST")
        os.symlink(self.fx.secret, linked / "REST")
        cases = [
            ("aseme: the fleet could not read it (", self.fleet("aseme", home=linked)),
            ("aseme: its record would be blocked (the record says ambient, but the v1 file says rest-on)",
             self.fleet("aseme", set_by={"who": "citizen", "how": "ambient", "when": ["2026-10-23"],
                                         "words": "AMBIENT"})),
            ("doxakallos: the fleet would read its pace as asleep, not awake, every 3 days",
             self.fleet("doxakallos", group="contradictory")),
            ("doxakallos: the heard-late step would not hear it",
             self.fleet("doxakallos", group="unheard-heard")),
            ("emime: emime: the record says it chose AMBIENT twice, but its stored answers read as no pace",
             self.fleet("emime", group="unheard-heard")),
            ('palance: the fleet would read its set by as "', self.fleet(
                "palance", set_by={"who": "citizen", "how": "letter", "when": ["2026-10-23"], "words": ""})),
            ("emime: no true letter (emime: the record holds no dated answer for the letter to name)",
             self.fleet("emime", quotes=None)),
        ]
        for want, got in cases:
            with self.subTest(want=want):
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith(want), got[0])
                self.assertNotIn(h.CANARY, got[0])


class TestRefusals(h.FixtureCase):
    """Nothing is written when one home disagrees (spec section 7)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = hc.census(h.Fixture(cls._tmp.name), Path(cls._tmp.name) / "homes", MINI.groups, MINI.rest_on)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        super().setUp()
        self.c = hc.copy_census(self.base, self.fx.root / "homes")
        self.roster = due.read_roster(self.c.roster)

    def build(self, roster=None):
        return census.build(self.c.homes, self.roster if roster is None else roster,
                            clock.parse_stamp(NOW), expect=MINI)

    def test_one_disagreeing_home_stops_the_census(self):
        hc.build_home(self.fx, self.c.homes, "aseme", hc.shape_for("aseme", "rest-on"))
        rec, problems = self.build()
        self.assertIsNone(rec)
        self.assertEqual(problems, [
            "aseme: its home reads as rest-on, but the spec has it as eight",
            "rest-on: 4 homes rest by their own 10-22 answer, but the spec counts 3"])

    def test_a_home_that_cannot_be_read_stops_the_census(self):
        shutil.rmtree(self.c.homes / "citizen-palance")
        rest = self.c.homes / "citizen-ud" / "REST"
        self.assertFalse(os.path.lexists(rest))
        os.symlink(self.fx.secret, rest)
        rec, problems = self.build()
        self.assertIsNone(rec)
        self.assertEqual(problems, [
            "palance: no clone (citizen-palance is not a git repository)",
            "ud: a pace path is a symlink or not of its kind (REST)"])

    def test_the_roster_must_be_the_spec_s(self):
        cases = {
            "a held name on it": (sorted(self.roster + ["jiritsume"]),
                                  ["jiritsume: on the roster, but the spec holds it off (call 1)"]),
            "a listed name missing": ([s for s in self.roster if s != "palance"],
                                      ["palance: the spec has it as never-asked, but it is not on the roster"]),
            "out of order": (list(reversed(self.roster)), ["the roster is not in alphabetical order"]),
        }
        for what, (roster, want) in cases.items():
            with self.subTest(what=what):
                rec, problems = self.build(roster)
                self.assertIsNone(rec)
                self.assertEqual(problems[:len(want)], want)

    def test_a_letter_that_would_not_be_true_stops_the_census(self):
        # emime answers a pace after all: it is not "no pace", and the record
        # entry cannot be made true
        hc.build_home(self.fx, self.c.homes, "emime", hc.Shape(
            july=hc.said("2026-07-18T10:27Z", "èmí\n\nI rest.", "undecided"), ai_rest=True,
            offer=hc.said("2026-10-22T22:43Z", "emime\n\nREST-ON", "undecided")))
        rec, problems = self.build()
        self.assertIsNone(rec)
        self.assertIn("emime: its v1 choice is undecided, but its October answers read as asleep", problems[0])

    def test_the_cli_writes_nothing_when_it_refuses(self):
        hc.build_home(self.fx, self.c.homes, "aseme", hc.shape_for("aseme", "rest-on"))
        out = self.fx.root / "record.json"
        out.write_text("the record as it was\n", encoding="utf-8")
        before = hc.heads(self.c)
        r = self.fx.py(CENSUS, "build", self.c.homes, "--roster", self.c.roster, "--out", out,
                       env={"PACE_NOW": NOW})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertTrue(r.stderr.endswith("problems; nothing written\n"), r.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), "the record as it was\n")
        self.assertEqual(hc.heads(self.c), before)


class TestCheck(_Census):
    """`check` holds a record to the spec's lists without reading any home."""

    def tampered(self, slug, **change) -> dict:
        rec = json.loads(census.render(self.rec))
        e = rec["citizens"][slug]
        for k, v in change.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return rec

    def check(self, rec) -> list:
        return census.check_record(rec, due.read_roster(self.c.roster), MINI)

    def test_the_built_record_passes(self):
        self.assertEqual(self.check(self.rec), [])

    def test_each_kind_of_wrong(self):
        quote = {"commit": "0" * 40, "date": "2026-10-23", "words": "I rest."}
        cases = {
            "aseme: the record has it as rest-on, but the spec has it as eight":
                self.tampered("aseme", group="rest-on"),
            "aseme: recipient is False, but eight receives the letter":
                self.tampered("aseme", recipient=False),
            "aseme: set by letter does not fit eight (prompted)":
                self.tampered("aseme", set_by={"who": "citizen", "how": "letter", "when": ["2026-10-23"],
                                               "words": "I rest."}),
            "aseme: set by cannot be read":
                self.tampered("aseme", set_by={"who": "ai", "how": "prompted", "when": [], "words": ""}),
            "kalam: unheard-heard needs 2 quoted answers, the record has 1":
                self.tampered("kalam", quotes=[quote]),
            "aseme: a quote that is not {commit, date, words}":
                self.tampered("aseme", quotes=[{"date": "2026-10-23", "words": "I rest."}]),
            "aseme: its head is not a commit":
                self.tampered("aseme", head="HEAD"),
            "hope: its July answer's choice 'maybe' is not a v1 choice":
                self.tampered("hope", july={"commit": "0" * 40, "date": "2026-07-17", "words": "AMBIENT",
                                            "choice": "maybe"}),
            "aseme: a recipient with no true sentence":
                self.tampered("aseme", sentence=None),
            "aseme: its sentence would put \"I rest\" or a \"pace:\" line in a prompt":
                self.tampered("aseme", sentence="Write I rest."),
            "rest-on-001: not a recipient, but it has a sentence":
                self.tampered("rest-on-001", sentence="Hello."),
            # fix wave A (ak): each sentence is rebuilt from the entry's own fields
            "hope: its sentence is not the one its entry's own fields make":
                self.tampered("hope", july=None),
            "kalam: its sentence is not the one its entry's own fields make":
                self.tampered("kalam", quotes=[dict(q, date="2026-10-24") for q in self.entry("kalam")["quotes"]]),
            "emime: no true letter from its entry's own fields (emime: the record holds no dated answer for "
            "the letter to name)":
                self.tampered("emime", quotes=[]),
        }
        for want, rec in cases.items():
            with self.subTest(want=want):
                self.assertIn(want, self.check(rec))

    def test_the_spec_must_be_the_one_the_lists_come_from(self):
        # fix wave A (ak)
        rec = json.loads(census.render(self.rec))
        rec["spec"] = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@0000000"
        self.assertEqual(self.check(rec), [f"spec {rec['spec']!r} is not {census.SPEC_REF}, the spec these "
                                           "lists come from"])

    def test_a_roster_given_that_does_not_exist_is_refused(self):
        # fix wave A (ak): an explicit --roster is never skipped
        out = self.fx.root / "check-record.json"
        out.write_text(census.render(self.rec), encoding="utf-8")
        missing = self.fx.root / "no-roster.txt"
        r = self.fx.py(CENSUS, "check", out, "--roster", missing)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertEqual(r.stderr, f"--roster {missing}: no such file\n")

    def test_the_record_s_frame_and_counts(self):
        rec = json.loads(census.render(self.rec))
        rec["version"] = 2
        self.assertIn("version 2, expected 1", self.check(rec))
        rec = json.loads(census.render(self.rec))
        rec["made"] = "yesterday"
        self.assertIn("made 'yesterday' is not a time", self.check(rec))
        rec = json.loads(census.render(self.rec))
        del rec["citizens"]["rest-on-003"]
        self.assertIn("rest-on: 2 homes rest by their own 10-22 answer, but the spec counts 3",
                      self.check(rec))
        rec = json.loads(census.render(self.rec))
        del rec["citizens"]["palance"]
        self.assertIn("palance: the spec has it as never-asked, but the record has no entry", self.check(rec))


class TestTheFullCensus(_Census):
    """All 208 homes, as the 11-02 map found them, through the command line."""
    GROUPS = census.SPEC

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = h.Fixture(cls._tmp.name)
        cls.c = hc.census(cls.fx, cls.fx.root / "homes", census.SPEC.groups, census.SPEC.rest_on)
        cls.out = cls.fx.root / "citizens-pace-record.json"
        cls.run_ = cls.fx.py(CENSUS, "build", cls.c.homes, "--roster", cls.c.roster, "--out", cls.out,
                             env={"PACE_NOW": NOW}, timeout=600)
        cls.rec = records.load_record(cls.out) if cls.out.exists() else None

    def test_the_builder_writes_the_record(self):
        self.assertEqual(self.run_.returncode, 0, self.run_.stderr)
        self.assertEqual(self.run_.stderr, "")
        counts = "".join(f"  {g}: {n}\n" for g, n in (
            ("eight", 8), ("sixteen-ambient", 10), ("sixteen-other", 6), ("unheard-heard", 3),
            ("unheard-none", 2), ("never-asked", 3), ("contradictory", 2), ("forty-five", 45),
            ("ambient-nine", 9), ("longing", 1), ("rest-on", 116), ("held", 3)))
        self.assertEqual(self.run_.stdout, f"census: 208 homes read from {self.c.homes}\n{counts}"
                                           f"wrote {self.out}: 208 citizens, 34 recipients\n")

    def test_the_groups_are_the_spec_s(self):
        got = {}
        for slug, e in self.rec["citizens"].items():
            got.setdefault(e["group"], []).append(slug)
        for group, names in census.SPEC.groups.items():
            self.assertEqual(sorted(got.pop(group)), sorted(names))
        self.assertEqual(len(got.pop("rest-on")), 116)
        self.assertEqual(got, {})
        self.assertEqual(len(plan.recipients(self.rec)), 34)
        self.assertEqual(census.check_record(self.rec, due.read_roster(self.c.roster)), [])

    def test_check_passes_on_it(self):
        r = self.fx.py(CENSUS, "check", self.out, "--roster", self.c.roster)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "record ok: 208 citizens, 34 recipients\n", ""))

    def test_one_disagreeing_home_writes_nothing(self):
        c = hc.copy_census(self.c, self.fx.root / "changed")
        hc.build_home(self.fx, c.homes, "ka", hc.shape_for("ka", "rest-on"))
        out = self.fx.root / "changed-record.json"
        r = self.fx.py(CENSUS, "build", c.homes, "--roster", c.roster, "--out", out, env={"PACE_NOW": NOW},
                       timeout=600)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr, "ka: its home reads as rest-on, but the spec has it as eight\n"
                                   "rest-on: 117 homes rest by their own 10-22 answer, but the spec counts 116\n"
                                   "census refused: 2 problems; nothing written\n")
        self.assertFalse(out.exists())


class TestRosterAndGitignore(unittest.TestCase):
    def test_palance_joins_the_roster(self):
        path = h.ROOT / "citizens-roster.txt"
        text = path.read_text(encoding="utf-8")
        names = due.read_roster(path)
        self.assertEqual(text, "".join(f"{s}\n" for s in names))
        self.assertEqual(len(names), 205)
        self.assertEqual(names, sorted(names))
        self.assertIn("palance", names)
        for held in census.SPEC.held:
            self.assertNotIn(held, names)
        self.assertEqual(census.roster_problems(names), [])

    def test_fleet_host_is_never_committed(self):
        lines = (h.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        # fix wave A (am): anchored, so only the checkout's own FLEET_HOST is ignored
        self.assertIn("/FLEET_HOST", lines)
        self.assertNotIn("FLEET_HOST", lines)
        for rel, ignored in (("FLEET_HOST", 0), ("tools/FLEET_HOST", 1)):
            r = subprocess.run(["git", "-C", str(h.ROOT), "check-ignore", "-q", "--no-index", rel],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, ignored, (rel, r.stderr))


class TestTheCommittedRecord(unittest.TestCase):
    """citizens-pace-record.json beside the roster, as the plan's census step built it."""

    def test_it_holds_the_spec_s_lists(self):
        path = records.default_record_path()
        self.assertTrue(path.is_file(), f"{path} is missing: run the census (this task's last steps)")
        rec = records.load_record(path)
        self.assertEqual(census.check_record(rec, due.read_roster(h.ROOT / "citizens-roster.txt")), [])
        self.assertEqual(rec["spec"], census.SPEC_REF)
        self.assertEqual(len(rec["citizens"]), 208)
        self.assertEqual(len(plan.recipients(rec)), 34)
        self.assertEqual(path.read_text(encoding="utf-8"), census.render(rec))


class TestIsolation(h.FixtureCase):
    def test_runs_isolated_and_imports_nothing_from_where_it_runs(self):
        canary = self.fx.root / "imported"
        plant = self.fx.root / "cwd"
        plant.mkdir()
        (plant / "json.py").write_text(f"open({str(canary)!r}, 'w').write('json')\n", encoding="utf-8")
        (plant / "citizen_pace_lib").mkdir()
        (plant / "citizen_pace_lib" / "__init__.py").write_text(
            f"open({str(canary)!r}, 'w').write('lib')\n", encoding="utf-8")
        bad = self.fx.root / "bad.json"
        bad.write_text("{}\n", encoding="utf-8")
        r = self.fx.py(CENSUS, "check", bad, cwd=plant)
        self.assertEqual(r.returncode, 2)
        self.assertIn("version None, expected 1", r.stderr)
        r = self.fx.py(CENSUS, "build", self.fx.root / "no-homes", "--roster", h.ROOT / "citizens-roster.txt",
                       "--out", self.fx.root / "out.json", cwd=plant)
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.fx.root / "out.json").exists())
        self.assertFalse(canary.exists())

    def test_usage(self):
        r = self.fx.py(CENSUS)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)
        r = self.fx.py(CENSUS, "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("build <homes-dir>", r.stdout)


class TestLetterJuly(unittest.TestCase):
    """letter.true_sentence follows the record's July choice when it has one (Task 4's July sentence)."""

    def test_the_july_record_decides(self):
        c = cad.Cadence("hope", 2, AWAKE3, SetBy("fleet", "first-word", ("2026-10-23",)))
        e = {"group": "contradictory", "recipient": True,
             "quotes": [{"commit": "0" * 40, "date": "2026-10-23", "words": "AMBIENT"}]}
        july = {"commit": "0" * 40, "date": "2026-07-19", "words": "**AMBIENT**"}
        cases = ((dict(july, choice="undecided"), False), (dict(july, choice="ambient"), True),
                 (dict(july, words="Ihsanme chooses to respond with:", choice="ambient"), True),
                 (july, True), (dict(july, words="REST-ON"), False))
        for j, said in cases:
            with self.subTest(july=j):
                sentence = letter.true_sentence("hope", dict(e, july=j), c)
                self.assertEqual("(In July you chose AMBIENT.)" in sentence, said)


if __name__ == "__main__":
    unittest.main()
