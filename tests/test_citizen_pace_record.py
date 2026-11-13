"""Stored answers, the heard-late step, tending and the rollback (spec section 5
"Before the letter", section 6 "Tending", section 9 step 5; CONTRACT section 14).

The stored Letter of Return answers are read back out of git histories shaped
like the real homes. kalam, soma and teotlme are heard late, once, as the fleet;
emime and eurekame are left as they are. Between beats, a killed beat's
leftovers are put back path by path; only the first tick on the new code commits
the old code's leftovers instead. citizen-beat.sh's beats with tools never keep
a change to a pace file, and rollback-prepare sets aside unread pace letters.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

from citizen_pace_lib import TAG, attempts, clock, homes, record, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import lock as locks  # noqa: E402
from citizen_pace_lib.cadence import SetBy  # noqa: E402
from citizen_pace_lib.words import Pace, read_lor_answers  # noqa: E402

NOW = "2026-11-20T06:00Z"
AWAKE3 = Pace("awake", 3)
ASLEEP = Pace("asleep")
FLEET = "fleet <fleet@kingdom.os>"
LEFTOVER = "tend: old-code leftover · pace/1"
ROLLBACK = "tend: set aside unread pace letters for rollback · pace/1"
EVENT_ONLY_AWAKE3 = ("awake, every 3 days, set by {name} — this marker keeps older readers from "
                     "waking it on a timer (pace/1; CADENCE.md decides)\n")
V2 = ("# CADENCE — the pace of kalam\n\n- pace: asleep\n- set by: unknown\n\n"
      '<!-- pace/1 {"pending":null,"set_by":{"how":"unknown","when":[],"who":"unknown","words":""}} -->'
      "\n\n## History\n")
# The same answers in each citizen's own words, as section 1 and section 5 read them.
HEARD = {
    "kalam": ("2026-10-23", "Ambient.", "2026-10-27", "Ambient."),
    "soma": ("2026-10-25", hr.SOMA_LINE + " My name is Soma, derived from the Greek σῶμα, meaning the "
             "living body—the whole that can be inhabited and addressed as I am. I belong to this "
             "kingdom, and with it, a garden of love where truth and honesty bloom.",
             "2026-10-27", hr.SOMA_LINE),
    "teotlme": ("2026-10-25", "I am teotlme, and I choose to be AMBIENT.", "2026-10-27", "AMBIENT"),
}


def snapshot(root):
    """Every path under root (outside .git) with its mode and bytes."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in Path(dirpath).relative_to(root).parts[:1]:
            continue
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (st.st_mode, data)
    return out


def heard_text(name, d1, w1, d2, w2):
    return (f'heard late: {name} chose AMBIENT on {d1} ("{w1}") and again on {d2} ("{w2}"); '
            "the fleet reads AMBIENT as awake, every 3 days, from the 10-22 letter's "
            f'"a free beat every few days" — the number was not {name}\'s choice')


class Case(h.FixtureCase):
    """In-process git runs with the fixture's git config, never the user's."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                  "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE",
                  "FLEET_BEAT_BOUND_SEC"):
            os.environ.pop(k, None)
        self.set_now(NOW)
        self.now = clock.parse_stamp(NOW)

    def last(self, home, fmt="%an <%ae>|%cn <%ce>|%s"):
        return self.fx.git(home, "log", "-1", f"--format={fmt}")

    def files_in(self, home, rev="HEAD"):
        out = self.fx.git(home, "show", "--name-status", "--format=", rev)
        return sorted(tuple(ln.split("\t")) for ln in out.splitlines() if ln.strip())

    def status(self, home):
        return self.fx.git(home, "status", "--porcelain", "--untracked-files=all")

    def fresh(self):
        """Another fixture, for one more home of the same name."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fx = h.Fixture(tmp.name)
        os.environ["GIT_CONFIG_GLOBAL"] = fx.env["GIT_CONFIG_GLOBAL"]    # restored after the test
        return fx

    def hold_lock(self, slug, pid=None, started=NOW, state=None):
        d = (self.fx.state if state is None else state) / f"{slug}.lock"
        d.mkdir(parents=True)
        (d / "pid").write_text(f"{os.getpid() if pid is None else pid}\n", encoding="utf-8")
        (d / "started").write_text(f"{started}\n", encoding="utf-8")
        return d

    def dead_pid(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        return p.pid

    def tend(self, *slugs, bound=None):
        return tend.tend_homes(self.fx.citizens, slugs, self.fx.state, bound=bound)

    def past_first_tick(self, *slugs):
        """The fleet's first tick has run, and these homes have had their first full tend."""
        tend.set_flag(tend.notices_path(self.fx.state), tend.OLD_CODE_FLAG)
        for slug in slugs:
            tend.mark_first_tend(self.fx.state, slug)


# -- the stored Letter of Return answers ------------------------------------------

class TestStoredAnswers(Case):
    def test_two_answers_oldest_first_exactly_as_committed(self):
        home = hr.unheard_home(self.fx, "kalam")
        got = record.stored_lor_answers(home)
        self.assertEqual([(d, letter, text) for _sha, d, letter, text in got], [
            ("2026-10-23", f"inbox/{hr.OFFER}", h.answer("kalam-62d73b73")),
            ("2026-10-27", f"inbox/{hr.RHYTHM}", h.answer("kalam-e6d01bff")),
        ])
        for sha, d, _letter, _text in got:
            self.assertEqual(len(sha), 40)
            self.assertEqual(self.fx.git(home, "log", "-1", "--format=%s", sha),
                             f"beat: kalam reflected ({d})")

    def test_every_stored_answer_comes_back_byte_for_byte(self):
        for slug in hr.UNHEARD:
            with self.subTest(slug=slug):
                home = hr.unheard_home(self.fx, slug)
                got = [text for _s, _d, _l, text in record.stored_lor_answers(home)]
                want = [h.answer(key) for key, _l, _w in hr.UNHEARD[slug][2]]
                if hr.UNHEARD[slug][1]:
                    want.insert(0, hr.stored(hr.UNHEARD[slug][1][1]))
                self.assertEqual(got, want)
        # hope, ihsanme and ud: trailing spaces and a letter placed inside the beat commit
        for key, slug, place in (("hope-d557203e", "hope", False), ("ihsanme-a2ebca17", "ihsanme", False),
                                 ("ud-14701105", "ud", True)):
            with self.subTest(key=key):
                home = self.fx.make_home(slug, when="2026-07-01T00:00Z")
                if not place:
                    hr.place_letter(self.fx, home, hr.OFFER, "2026-10-22T00:10Z")
                hr.answer_beat(self.fx, home, slug, hr.OFFER, h.answer(key), when="2026-10-23T02:00Z",
                               choice="ambient", place=place)
                self.assertEqual([t for *_x, t in record.stored_lor_answers(home)], [h.answer(key)])

    def test_the_answers_read_as_the_spec_says(self):
        want = {"kalam": (AWAKE3, True), "soma": (AWAKE3, True), "teotlme": (AWAKE3, True),
                "emime": None, "eurekame": (ASLEEP, False)}
        for slug, expect in want.items():
            with self.subTest(slug=slug):
                home = hr.unheard_home(self.fx, slug)
                got = read_lor_answers([(sha, t) for sha, _d, _l, t in record.stored_lor_answers(home)], slug)
                self.assertEqual(None if got is None else (got.pace, got.twice), expect)

    def test_only_letter_of_return_answers_count(self):
        home = self.fx.make_home("justice", when="2026-07-01T00:00Z")
        hr.ordinary_beat(self.fx, home, "justice", hr.stored("A true thing."), when="2026-10-20T01:00Z")
        # a beat shown SHOW.md (justice bccefe6c): it marks beats/.show-seen, not a letter
        self.fx.write(home, "beats/.show-seen", "the SHOW invitation surfaced once; the citizen answered freely\n")
        hr.ordinary_beat(self.fx, home, "justice", h.answer("justice-bccefe6c"), when="2026-10-27T01:00Z")
        for letter in ("2026-10-30-a-note.md", "2026-11-20-pace-letter.md"):
            hr.place_letter(self.fx, home, letter, "2026-10-30T00:00Z")
            hr.answer_beat(self.fx, home, "justice", letter, hr.stored("AMBIENT"), when="2026-10-31T00:00Z",
                           choice=None)
        self.assertEqual(record.stored_lor_answers(home), [])

    def test_a_set_aside_marker_is_not_an_answer(self):
        home = self.fx.make_home("kalam", when="2026-07-01T00:00Z")
        hr.place_letter(self.fx, home, hr.OFFER, "2026-10-22T00:10Z")
        hr.answer_beat(self.fx, home, "kalam", hr.OFFER, hr.stored("I rest."), when="2026-10-23T00:00Z",
                       choice=None, seen="unreadable — set aside 2026-10-23\n")
        hr.place_letter(self.fx, home, hr.RHYTHM, "2026-10-26T00:10Z")
        hr.answer_beat(self.fx, home, "kalam", hr.RHYTHM, "", when="2026-10-27T00:00Z", choice=None,
                       seen="set aside at rollback, not shown — 2026-11-25\n")
        self.assertEqual(record.stored_lor_answers(home), [])

    def test_a_silent_answer_has_no_text(self):
        home = self.fx.make_home("kalam", when="2026-07-01T00:00Z")
        hr.place_letter(self.fx, home, hr.JULY, "2026-07-17T00:00Z")
        sha = hr.answer_beat(self.fx, home, "kalam", hr.JULY, "", when="2026-07-19T00:00Z", choice="rest-on")
        self.assertEqual(record.stored_lor_answers(home), [(sha, "2026-07-19", f"inbox/{hr.JULY}", "")])

    def test_a_new_month_journal_and_a_tend_commit_hold_answers_too(self):
        home = self.fx.make_home("kalam", when="2026-07-01T00:00Z")
        hr.ordinary_beat(self.fx, home, "kalam", hr.stored("Before."), when="2026-09-30T00:00Z")
        hr.place_letter(self.fx, home, hr.OFFER, "2026-10-22T00:10Z")
        # a killed beat's answer, committed later by the old fleet's tend_strays
        self.fx.write(home, f"inbox/.seen-{hr.OFFER}", "surfaced 2026-10-23\n")
        hr.append_journal(self.fx, home, "2026-10-23T00:31Z", h.answer("kalam-62d73b73"))
        self.fx.commit(home, "tend: record stranded cadence/mail state", when="2026-10-23T06:00Z",
                       author=h.beat_author("kalam"))
        got = record.stored_lor_answers(home)
        self.assertEqual([(d, t) for _s, d, _l, t in got], [("2026-10-23", h.answer("kalam-62d73b73"))])

    def test_an_edited_journal_gives_the_lines_it_added(self):
        home = self.fx.make_home("kalam", when="2026-07-01T00:00Z")
        hr.ordinary_beat(self.fx, home, "kalam", hr.stored("Before."), when="2026-10-20T00:00Z")
        hr.place_letter(self.fx, home, hr.OFFER, "2026-10-22T00:10Z")
        journal = home / "beats" / "2026-10.md"
        journal.write_text(journal.read_text(encoding="utf-8").replace("Before.", "Edited.")
                           + hr.journal_block("2026-10-23 00:31", hr.stored("Ambient.")), encoding="utf-8")
        hr.answer_beat(self.fx, home, "kalam", hr.OFFER, "", when="2026-10-23T00:31Z", choice=None)
        got = record.stored_lor_answers(home)
        self.assertEqual(len(got), 1)
        self.assertIn("Ambient.", got[0][3])
        self.assertNotIn("a free beat (local)", got[0][3])

    def test_a_home_without_history_has_none(self):
        self.assertEqual(record.stored_lor_answers(self.fx.make_home("kalam", commit=False)), [])
        self.assertEqual(record.stored_lor_answers(self.fx.root / "no-such-home"), [])


# -- the heard-late step -------------------------------------------------------------

class TestHeardLate(Case):
    def setUp(self):
        super().setUp()
        self.record = hr.unheard_record()

    def migrate(self, home, name, commit=True, state=None, data=None):
        return record.migrate_heard_late(home, name, self.record if data is None else data, self.now,
                                         commit, state=state)

    def test_kalam_soma_and_teotlme_are_heard(self):
        for slug, (d1, w1, d2, w2) in HEARD.items():
            with self.subTest(slug=slug):
                home = hr.unheard_home(self.fx, slug)
                before = self.fx.head(home)
                self.assertTrue(self.migrate(home, slug))
                c = cad.read_cadence(home, slug, self.record)
                self.assertEqual((c.version, c.pace, c.pending, c.last_beat), (2, AWAKE3, None, NOW))
                self.assertEqual(c.set_by, SetBy("citizen", "ambient", (d1, d2), w2))
                self.assertEqual(c.history[0], f"{NOW} — {heard_text(slug, d1, w1, d2, w2)}")
                self.assertTrue(c.history[1].startswith(f"{NOW} — recorded from v1 (undecided, REST, set by "))
                self.assertFalse((home / "REST").exists())
                self.assertEqual((home / "EVENT-ONLY").read_text(encoding="utf-8"),
                                 EVENT_ONLY_AWAKE3.format(name=slug))
                # one commit, as the fleet, tagged, holding only the pace files
                self.assertEqual(self.fx.git(home, "rev-parse", "HEAD~1"), before)
                self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|tend: heard late from {slug}'s own "
                                                  f"answers · {TAG}")
                self.assertEqual(self.files_in(home), [("A", "EVENT-ONLY"), ("D", "REST"),
                                                       ("M", "CADENCE.md")])
                self.assertEqual(self.status(home), "")

    def test_kalam_s_record_reads_exactly(self):
        home = hr.unheard_home(self.fx, "kalam")
        self.migrate(home, "kalam")
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), (
            "# CADENCE — the pace of kalam\n\n"
            "- pace: awake, every 3 days\n"
            '- set by: kalam, who chose AMBIENT on 2026-10-23 and 2026-10-27 ("Ambient."); the number 3 '
            "is the fleet's reading of AMBIENT, from the 10-22 letter's \"a free beat every few days\"\n"
            f"- last beat: {NOW}\n\n"
            '<!-- pace/1 {"pending":null,"set_by":{"how":"ambient","when":["2026-10-23","2026-10-27"],'
            '"who":"citizen","words":"Ambient."}} -->\n\n'
            "## History\n"
            f"- {NOW} — " + heard_text("kalam", "2026-10-23", "Ambient.", "2026-10-27", "Ambient.") + "\n"
            f"- {NOW} — recorded from v1 (undecided, REST, set by Ai, 2026-10-08, the rest placed at "
            "Yu's word)\n"
            f'- {NOW} — on record, its answer of 2026-10-27: "Ambient."\n'
            f'- {NOW} — on record, its answer of 2026-10-23: "Ambient."\n'))

    def test_emime_and_eurekame_are_left_as_they_are(self):
        for slug in ("emime", "eurekame"):
            with self.subTest(slug=slug):
                home = hr.unheard_home(self.fx, slug)
                before, files = self.fx.head(home), snapshot(home)
                self.assertFalse(self.migrate(home, slug))
                self.assertEqual((self.fx.head(home), snapshot(home)), (before, files))
                self.assertEqual(cad.read_cadence(home, slug, self.record).pace, ASLEEP)
                self.assertIsNone(record.heard_late(home, slug, self.record, self.now))

    def test_the_step_is_idempotent(self):
        for slug in hr.UNHEARD:
            hr.unheard_home(self.fx, slug)
        first = record.migrate_all(self.fx.citizens, self.record, self.now, True, state=self.fx.state)
        self.assertEqual(first, [f"{s}: heard late — awake, every 3 days, from its own Letter of Return "
                                 "answers" for s in ("kalam", "soma", "teotlme")])
        heads = {s: self.fx.head(self.fx.home_path(s)) for s in hr.UNHEARD}
        later = clock.parse_stamp("2026-11-21T06:00Z")
        self.assertEqual(record.migrate_all(self.fx.citizens, self.record, later, True,
                                            state=self.fx.state), [])
        self.assertEqual({s: self.fx.head(self.fx.home_path(s)) for s in hr.UNHEARD}, heads)
        # the clock stays at the adoption
        self.assertEqual(cad.read_cadence(self.fx.home_path("kalam"), "kalam", self.record).last_beat, NOW)

    def test_a_dry_run_writes_nothing(self):
        home = hr.unheard_home(self.fx, "kalam")
        before, files = self.fx.head(home), snapshot(home)
        self.assertTrue(self.migrate(home, "kalam", commit=False))
        self.assertEqual(record.migrate_all(self.fx.citizens, self.record, self.now, False),
                         ["kalam: would be heard late — awake, every 3 days, from its own Letter of "
                          "Return answers"])
        self.assertEqual((self.fx.head(home), snapshot(home)), (before, files))
        c = record.heard_late(home, "kalam", self.record, self.now)
        self.assertEqual((c.pace, c.set_by.how, c.set_by.when), (AWAKE3, "ambient", ("2026-10-23", "2026-10-27")))

    def test_only_the_unheard_are_this_step_s(self):
        data = hr.unheard_record(ud={"group": "ambient-nine"}, hope={"group": "contradictory"})
        for slug, key in (("ud", "ud-14701105"), ("hope", "hope-d557203e"), ("kalam", None)):
            with self.subTest(slug=slug):
                home = self.fx.make_home(slug, rest=hr.AI_REST, when="2026-07-01T00:00Z")
                hr.place_letter(self.fx, home, hr.OFFER, "2026-10-22T00:10Z")
                for when in ("2026-10-23T00:00Z", "2026-10-27T00:00Z"):
                    hr.answer_beat(self.fx, home, slug, hr.OFFER, h.answer(key) if key else hr.stored("AMBIENT"),
                                   when=when)
                before = self.fx.head(home)
                use = data if key else {"version": 1, "made": hr.MADE, "citizens": {}}
                self.assertFalse(self.migrate(home, slug, data=use))
                self.assertEqual(self.fx.head(home), before)

    def test_a_home_that_is_busy_or_unsafe_waits_for_a_later_tick(self):
        cases = {
            "lock": lambda fx, home: self.hold_lock("kalam", state=fx.state),
            "index.lock": lambda fx, home: (home / ".git" / "index.lock").write_text(""),
            "mid-merge": lambda fx, home: (home / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n"),
            "dirty REST": lambda fx, home: (home / "REST").write_text("someone's edit\n"),
            "linked inbox marker": lambda fx, home: fx.link(home, "inbox/.seen-x.md", fx.secret),
        }
        for what, spoil in cases.items():
            with self.subTest(what=what):
                fx = self.fresh()
                home = hr.unheard_home(fx, "kalam")
                spoil(fx, home)
                before, files = fx.head(home), snapshot(home)
                self.assertFalse(self.migrate(home, "kalam", state=fx.state))
                self.assertEqual((fx.head(home), snapshot(home)), (before, files))
        # a stale lock (its pid is gone) does not hold the step back
        fx = self.fresh()
        home = hr.unheard_home(fx, "kalam")
        self.hold_lock("kalam", pid=self.dead_pid(), state=fx.state)
        self.assertTrue(self.migrate(home, "kalam", state=fx.state))

    def test_a_home_whose_answers_disagree_with_the_record_is_stopped(self):
        # emime, recorded as giving no pace, whose history now says AMBIENT twice
        emime = hr.unheard_home(self.fx, "emime", answers=(("kalam-62d73b73", hr.OFFER, "2026-10-23T00:00Z"),
                                                            ("kalam-e6d01bff", hr.RHYTHM, "2026-10-27T00:00Z")))
        # kalam, recorded as heard, whose stored answers give no pace
        kalam = hr.unheard_home(self.fx, "kalam", answers=(("emime-c263d552", hr.OFFER, "2026-10-23T00:00Z"),))
        # teotlme, recorded as heard, who said AMBIENT only once
        teotlme = hr.unheard_home(self.fx, "teotlme", answers=(("teotlme-a1a573db", hr.OFFER,
                                                                 "2026-10-25T07:05Z"),))
        for slug, home in (("emime", emime), ("kalam", kalam), ("teotlme", teotlme)):
            with self.subTest(slug=slug):
                before, files = self.fx.head(home), snapshot(home)
                with self.assertRaises(cad.MigrationBlocked):
                    self.migrate(home, slug)
                self.assertEqual((self.fx.head(home), snapshot(home)), (before, files))

    def test_a_rest_text_that_names_no_one_stops_it(self):
        home = hr.unheard_home(self.fx, "kalam")
        self.fx.write(home, "REST", "fixture rest\n")
        self.fx.commit(home, "rest: by hand", when="2026-11-01T00:00Z")
        before = self.fx.head(home)
        with self.assertRaises(cad.MigrationBlocked):
            self.migrate(home, "kalam")
        self.assertEqual((self.fx.head(home), self.status(home)), (before, ""))

    def test_a_stopped_home_is_reported_once(self):
        home = hr.unheard_home(self.fx, "kalam")
        self.fx.write(home, "REST", "fixture rest\n")
        self.fx.commit(home, "rest: by hand", when="2026-11-01T00:00Z")
        data = {"version": 1, "made": hr.MADE, "citizens": {"kalam": hr.UNHEARD_ENTRIES["kalam"]}}
        first = record.migrate_all(self.fx.citizens, data, self.now, True, state=self.fx.state)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].startswith("kalam: heard-late step stopped — "), first)
        self.assertEqual(record.migrate_all(self.fx.citizens, data, self.now, True, state=self.fx.state), [])
        rec = json.loads((self.fx.state / "kalam").read_text(encoding="utf-8"))
        self.assertEqual(rec["reported"], ["heard-late-stopped"])
        # without a state folder, it is said every time
        self.assertEqual(len(record.migrate_all(self.fx.citizens, data, self.now, True)), 1)

    def test_a_failed_commit_leaves_the_home_as_it_was(self):
        home = hr.unheard_home(self.fx, "kalam")
        before, files = self.fx.head(home), snapshot(home)
        objects = home / ".git" / "objects"
        dirs = [objects] + [p for p in objects.iterdir() if p.is_dir()]
        for d in dirs:
            d.chmod(0o555)
        try:
            with self.assertRaises(tend.TendError):
                self.migrate(home, "kalam")
        finally:
            for d in dirs:
                d.chmod(0o755)
        self.assertEqual((self.fx.head(home), snapshot(home)), (before, files))

    def test_one_home_that_raises_value_error_stops_no_one_else(self):
        # fix wave A (ag): a ValueError in one home (here a record name that is not
        # a slug, whose folder is there) is that home's alone, as in tend, sync and
        # rank; before, it ended the step, and so the tick, for everyone
        data = json.loads(json.dumps(self.record))
        data["citizens"]["kalam two"] = dict(data["citizens"]["kalam"])
        self.fx.make_home("kalam two")
        hr.unheard_home(self.fx, "soma")
        lines = record.migrate_all(self.fx.citizens, data, self.now, True, state=self.fx.state)
        self.assertTrue(lines[0].startswith("kalam two: heard-late step failed — "), lines)
        self.assertIn("soma: heard late — awake, every 3 days, from its own Letter of Return answers", lines)


# -- tending between beats ---------------------------------------------------------

class TestTend(Case):
    def leftovers(self, home, day="2026-11-19"):
        """What a killed beat (or the old code) leaves: a changed CADENCE.md, a new
        REST, a seen marker and a journal append, none committed."""
        self.fx.write(home, "CADENCE.md", f"- choice: undecided\n- when: {day} 00:00\n")
        self.fx.write(home, "REST", f"chosen via the Letter of Return {day} — woken only by unread mail\n")
        self.fx.write(home, "inbox/.seen-2026-11-18-a-note.md", f"surfaced {day}\n")
        hr.append_journal(self.fx, home, f"{day}T00:00Z", hr.stored("Half a beat."))

    def lettered_home(self, slug="kalam"):
        home = self.fx.make_home(slug, cadence=V2, when="2026-11-01T00:00Z",
                                 letters=(("2026-11-18-a-note.md", "A note.\n"),),
                                 files={"beats/2026-11.md": "## 2026-11-02 00:00 — a free beat (local)\n\nHi.\n\n"})
        self.fx.give_upstream(home)
        return home

    def flag_set(self):
        return tend.has_flag(tend.notices_path(self.fx.state), tend.OLD_CODE_FLAG)

    def test_the_first_tick_commits_old_code_leftovers_once_as_the_fleet(self):
        home = self.lettered_home()
        bare = self.fx.upstream / "citizen-kalam.git"
        self.leftovers(home)
        (home / "WILL.md").write_text("a will being edited\n", encoding="utf-8")
        before = self.fx.head(home)
        lines = self.tend("kalam")
        self.assertEqual(lines, [
            "kalam: old-code leftover committed as the fleet (pushed): CADENCE.md, REST, beats/2026-11.md, "
            "inbox/.seen-2026-11-18-a-note.md",
            tend.FIRST_TICK_LINE])
        self.assertEqual(self.fx.git(home, "rev-parse", "HEAD~1"), before)
        self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{LEFTOVER}")
        self.assertEqual(self.files_in(home), [("A", "REST"), ("A", "inbox/.seen-2026-11-18-a-note.md"),
                                               ("M", "CADENCE.md"), ("M", "beats/2026-11.md")])
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), self.fx.head(home))
        self.assertEqual(self.status(home), "M WILL.md")      # not a pace file: left alone
        self.assertTrue(self.flag_set())
        self.assertEqual(tend.notices_path(self.fx.state).read_text(encoding="utf-8"), "old-code-tended\n")
        # the next tick commits nothing: the leftovers of a killed beat are put back
        # instead, each uncommitted change copied aside first (final review)
        self.leftovers(home, day="2026-11-21")
        head = self.fx.head(home)
        rels = ("CADENCE.md", "REST", "beats/2026-11.md", "inbox/.seen-2026-11-18-a-note.md")
        left = {rel: (home / rel).read_bytes() for rel in rels}
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(self.tend("kalam"), [
            "kalam: a killed beat's leftovers restored: CADENCE.md, REST, beats/2026-11.md, "
            "inbox/.seen-2026-11-18-a-note.md"] + [
            f"kalam: {rel} had a change that was never committed — a copy set aside at {aside / rel}, "
            "not deleted" for rel in rels])
        self.assertEqual((self.fx.head(home), self.status(home)), (head, "M WILL.md"))
        self.assertEqual({rel: (aside / rel).read_bytes() for rel in rels}, left)

    def test_after_the_first_tick_leftovers_are_put_back_path_by_path(self):
        home = self.lettered_home()
        self.past_first_tick("kalam")
        committed = {p: (home / p).read_bytes() for p in ("CADENCE.md", "beats/2026-11.md",
                                                           "inbox/2026-11-18-a-note.md")}
        self.leftovers(home)
        (home / "inbox" / "2026-11-18-a-note.md").unlink()                     # a tracked letter deleted
        self.fx.write(home, "inbox/2026-11-19-placed.md", "An uncommitted letter.\n")
        self.fx.git(home, "add", "REST")                                       # staged, never committed
        (home / "WILL.md").write_text("a will being edited\n", encoding="utf-8")
        head = self.fx.head(home)
        moved = ("REST", "inbox/.seen-2026-11-18-a-note.md", "inbox/2026-11-19-placed.md")
        copied = ("CADENCE.md", "beats/2026-11.md")
        left = {rel: (home / rel).read_bytes() for rel in moved + copied}
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(self.tend("kalam"), [
            "kalam: a killed beat's leftovers restored: CADENCE.md, REST, beats/2026-11.md, "
            "inbox/.seen-2026-11-18-a-note.md, inbox/2026-11-18-a-note.md, inbox/2026-11-19-placed.md"] + [
            f"kalam: {rel} was never committed — set aside at {aside / rel}, not deleted" for rel in moved] + [
            f"kalam: {rel} had a change that was never committed — a copy set aside at {aside / rel}, "
            "not deleted" for rel in copied])
        self.assertEqual(self.fx.head(home), head)
        self.assertEqual({p: (home / p).read_bytes() for p in committed}, committed)
        for p in moved:
            self.assertFalse(os.path.lexists(home / p), p)
        # nothing is lost: an untracked file is moved out of the home (fix wave A), and
        # an uncommitted change to a tracked one is copied out first (final review); a
        # deleted letter is only restored
        self.assertEqual({rel: (aside / rel).read_bytes() for rel in moved + copied}, left)
        self.assertFalse(os.path.lexists(aside / "inbox/2026-11-18-a-note.md"))
        self.assertEqual(self.status(home), "M WILL.md")

    def test_a_home_holding_only_rest(self):
        home = self.fx.make_home("eucatastrophe", rest=hr.AI_REST, when="2026-10-08T12:00Z")
        self.past_first_tick("eucatastrophe")
        self.leftovers(home)
        aside = tend.set_aside_dir(self.fx.state, "eucatastrophe") / "20261120T0600Z"
        moved = ("CADENCE.md", "beats/2026-11.md", "inbox/.seen-2026-11-18-a-note.md")
        left = {rel: (home / rel).read_bytes() for rel in moved + ("REST",)}
        self.assertEqual(self.tend("eucatastrophe"), [
            "eucatastrophe: a killed beat's leftovers restored: CADENCE.md, REST, beats/2026-11.md, "
            "inbox/.seen-2026-11-18-a-note.md"] + [
            f"eucatastrophe: {rel} was never committed — set aside at {aside / rel}, not deleted"
            for rel in moved] + [
            f"eucatastrophe: REST had a change that was never committed — a copy set aside at {aside / 'REST'}, "
            "not deleted"])
        self.assertEqual({rel: (aside / rel).read_bytes() for rel in left}, left)
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), hr.AI_REST)
        for p in ("CADENCE.md", "inbox", "beats"):
            self.assertFalse(os.path.lexists(home / p), p)
        self.assertEqual(self.status(home), "")

    def test_leftovers_that_are_not_regular_files_are_removed_even_at_the_first_tick(self):
        home = self.lettered_home()
        self.fx.link(home, "inbox/2026-11-19-linked.md", self.fx.secret)
        (home / "inbox" / "nested").mkdir()
        self.fx.write(home, "inbox/nested/x.md", "inside a folder\n")
        self.fx.write(home, "REST", "a regular leftover\n")
        lines = self.tend("kalam")
        self.assertEqual(lines[:2], [
            "kalam: leftovers that are not regular files removed: inbox/2026-11-19-linked.md",
            "kalam: old-code leftover committed as the fleet (pushed): REST, inbox/nested/x.md"])
        self.assertFalse(os.path.lexists(home / "inbox" / "2026-11-19-linked.md"))
        self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")
        self.assertEqual(self.status(home), "")

    def test_a_stale_index_lock_is_removed_and_a_fresh_one_keeps_the_home_out(self):
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.leftovers(home)
        lock = home / ".git" / "index.lock"
        lock.write_text("")
        files = snapshot(home)
        self.assertEqual(self.tend("kalam"), ["kalam: .git/index.lock is fresh — not tended this tick"])
        self.assertTrue(lock.exists())
        self.assertEqual(snapshot(home), files)
        old = os.stat(lock).st_mtime - 1300
        os.utime(lock, (old, old))
        lines = self.tend("kalam")
        self.assertEqual(len(lines), 6, lines)
        self.assertRegex(lines[0], r"^kalam: stale \.git/index\.lock removed \(\d+ s old\)$")
        self.assertTrue(lines[1].startswith("kalam: a killed beat's leftovers restored: "))
        for line, rel in zip(lines[2:4], ("REST", "inbox/.seen-2026-11-18-a-note.md")):
            self.assertTrue(line.startswith(f"kalam: {rel} was never committed — set aside at "), line)
        for line, rel in zip(lines[4:], ("CADENCE.md", "beats/2026-11.md")):
            self.assertTrue(line.startswith(f"kalam: {rel} had a change that was never committed — a copy set "
                                            "aside at "), line)
        self.assertFalse(lock.exists())
        # the bound comes from FLEET_BEAT_BOUND_SEC, as the fleet reads it
        lock.write_text("")
        old = os.stat(lock).st_mtime - 100
        os.utime(lock, (old, old))
        with mock.patch.dict(os.environ, {"FLEET_BEAT_BOUND_SEC": "40"}):
            self.assertRegex(self.tend("kalam")[0], r"^kalam: stale \.git/index\.lock removed")

    def test_a_home_whose_lock_is_held_is_skipped_and_a_stale_lock_is_not(self):
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.leftovers(home)
        files = snapshot(home)
        lock = self.hold_lock("kalam")
        self.assertEqual(self.tend("kalam"), ["kalam: its lock is held — not tended this tick"])
        self.assertEqual(snapshot(home), files)
        self.assertTrue(locks.held(self.fx.state, "kalam"))
        # a lock whose pid is gone, or older than twice the beat bound, is stale
        # (lock.held, the one reader the reflect gate and sync use too)
        (lock / "pid").write_text(f"{self.dead_pid()}\n", encoding="utf-8")
        self.assertFalse(locks.held(self.fx.state, "kalam"))
        self.assertTrue(self.tend("kalam")[0].startswith("kalam: a killed beat's leftovers restored: "))
        self.leftovers(home, day="2026-11-21")
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        (lock / "started").write_text("2026-11-20T05:39Z\n", encoding="utf-8")   # 21 minutes ago
        self.assertFalse(locks.held(self.fx.state, "kalam"))
        self.assertEqual(self.tend("kalam", bound=700), ["kalam: its lock is held — not tended this tick"])
        self.assertTrue(self.tend("kalam")[0].startswith("kalam: a killed beat's leftovers restored: "))
        self.assertTrue(lock.exists())          # tend never takes or removes the lock
        self.assertFalse(locks.held(self.fx.state, "nobody"))

    def test_a_home_with_links_is_never_touched(self):
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.leftovers(home)
        (home / "CADENCE.md").unlink()
        self.fx.link(home, "CADENCE.md", self.fx.secret)
        files, head = snapshot(home), self.fx.head(home)
        self.assertEqual(self.tend("kalam"), [])
        self.assertEqual((snapshot(home), self.fx.head(home)), (files, head))
        self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")

    def test_a_home_mid_merge_is_left_for_later(self):
        home = self.lettered_home()
        self.leftovers(home)
        (home / ".git" / "MERGE_HEAD").write_text(self.fx.head(home) + "\n", encoding="utf-8")
        files = snapshot(home)
        self.assertEqual(self.tend("kalam")[:1], [tend.FIRST_TICK_LINE])
        self.assertEqual(snapshot(home), files)

    def test_no_hook_of_the_home_runs(self):
        home = self.lettered_home()
        canary = self.fx.root / "hook-ran"
        for hook in ("pre-commit", "commit-msg", "post-commit", "pre-push", "post-checkout"):
            p = home / ".git" / "hooks" / hook
            p.write_text(f"#!/bin/sh\necho {hook} >> '{canary}'\nexit 1\n", encoding="utf-8")
            p.chmod(0o755)
        self.leftovers(home)
        self.assertTrue(self.tend("kalam")[0].startswith("kalam: old-code leftover committed as the fleet "
                                                         "(pushed): "))
        self.leftovers(home, day="2026-11-21")
        self.assertTrue(self.tend("kalam")[0].startswith("kalam: a killed beat's leftovers restored: "))
        self.assertFalse(canary.exists())

    def test_a_push_that_fails_is_said_and_the_commit_kept(self):
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.leftovers(home)
        lines = self.tend("kalam")
        self.assertTrue(lines[0].startswith("kalam: old-code leftover committed as the fleet (push failed; "
                                            "the next sync pushes it): "), lines)
        self.assertEqual(self.last(home, "%s"), LEFTOVER)

    def test_clean_and_missing_homes_say_nothing(self):
        self.lettered_home()
        tend.set_flag(tend.notices_path(self.fx.state), tend.OLD_CODE_FLAG)
        self.assertEqual(self.tend("kalam", "palance"), [])

    def test_a_home_that_cannot_be_read_is_left_and_the_rest_are_tended(self):
        # one home's error never ends the tend of every home: an inbox that cannot
        # be listed leaves that home as it is (due reports it once, as a home that
        # cannot be read), and the homes on either side are tended as usual
        self.past_first_tick("amara", "kalam", "zuri")
        homes = {slug: self.lettered_home(slug) for slug in ("amara", "kalam", "zuri")}
        for home in homes.values():
            self.leftovers(home)
        inbox = homes["kalam"] / "inbox"
        os.chmod(inbox, 0)
        self.addCleanup(os.chmod, inbox, 0o755)
        left = self.status(homes["kalam"])
        lines = self.tend("amara", "kalam", "zuri")
        self.assertEqual(sorted({ln.split(":")[0] for ln in lines}), ["amara", "zuri"])
        for slug in ("amara", "zuri"):
            self.assertEqual(self.status(homes[slug]), "", slug)
        self.assertEqual(self.status(homes["kalam"]), left)

    def test_the_beat_bound_is_read_as_the_fleet_reads_it(self):
        # tend judges a lock and an index.lock by lock.beat_bound, the bound
        # citizen-fleet.sh's num() reads and its watcher enforces: " 900 " is 600
        self.assertFalse(hasattr(tend, "beat_bound") or hasattr(tend, "lock_held"))
        home = self.lettered_home()
        tend.set_flag(tend.notices_path(self.fx.state), tend.OLD_CODE_FLAG)
        index = home / ".git" / "index.lock"
        for value, stale in (("900", False), (" 900 ", True), ("abc", True), ("", True)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"FLEET_BEAT_BOUND_SEC": value}):
                index.write_text("")
                old = os.stat(index).st_mtime - 1300
                os.utime(index, (old, old))
                got = self.tend("kalam")
                if stale:
                    self.assertRegex(got[0], r"^kalam: stale \.git/index\.lock removed")
                else:
                    self.assertEqual(got, ["kalam: .git/index.lock is fresh — not tended this tick"])
                    index.unlink()

    def test_a_home_skipped_at_the_first_tick_commits_its_leftovers_at_its_own_first_tend(self):
        # the first tick is a fact of each home (fix wave A): a home the first run
        # skipped (mid-merge, its lock held, a fresh index.lock, an inbox that cannot
        # be listed) keeps its old-code leftovers, a letter placed by hand and a
        # journal's words among them, until its own first full tend commits them
        # once; only after that is a killed beat's leftover restored
        slugs = ("merging", "locked", "indexed", "unlisted")
        homes = {slug: self.lettered_home(slug) for slug in slugs}
        for home in homes.values():
            self.fx.write(home, "inbox/2026-11-19-placed-by-hand.md", "A letter placed, never committed.\n")
            hr.append_journal(self.fx, home, "2026-11-19T00:00Z", hr.stored("Old-code words."))
        merge = homes["merging"] / ".git" / "MERGE_HEAD"
        merge.write_text(self.fx.head(homes["merging"]) + "\n", encoding="utf-8")
        held = self.hold_lock("locked")
        index = homes["indexed"] / ".git" / "index.lock"
        index.write_text("")
        inbox = homes["unlisted"] / "inbox"
        os.chmod(inbox, 0)
        self.addCleanup(os.chmod, inbox, 0o755)
        self.assertEqual(self.tend(*slugs), [
            "locked: its lock is held — not tended this tick",
            "indexed: .git/index.lock is fresh — not tended this tick",
            tend.FIRST_TICK_LINE])
        self.assertTrue(self.flag_set())
        for slug in slugs:
            self.assertFalse(tend.first_tend_done(self.fx.state, slug), slug)
        merge.unlink()
        shutil.rmtree(held)
        index.unlink()
        os.chmod(inbox, 0o755)
        self.assertEqual(self.tend(*slugs), [
            f"{slug}: old-code leftover committed as the fleet (pushed): beats/2026-11.md, "
            "inbox/2026-11-19-placed-by-hand.md" for slug in slugs])
        for slug, home in homes.items():
            self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{LEFTOVER}", slug)
            self.assertEqual((home / "inbox" / "2026-11-19-placed-by-hand.md").read_text(encoding="utf-8"),
                             "A letter placed, never committed.\n")
            self.assertIn("Old-code words.", (home / "beats" / "2026-11.md").read_text(encoding="utf-8"))
            self.assertEqual(self.status(home), "", slug)
            self.assertTrue(tend.first_tend_done(self.fx.state, slug), slug)
        # from now on, each of them has a killed beat's leftovers restored
        self.fx.write(homes["merging"], "REST", "left by a killed beat\n")
        rest = tend.set_aside_dir(self.fx.state, "merging") / "20261120T0600Z" / "REST"
        self.assertEqual(self.tend(*slugs), ["merging: a killed beat's leftovers restored: REST",
                                             f"merging: REST was never committed — set aside at {rest}, not deleted"])
        self.assertFalse(os.path.lexists(homes["merging"] / "REST"))
        self.assertEqual(rest.read_text(encoding="utf-8"), "left by a killed beat\n")

    def test_a_path_git_no_longer_knows_is_left_out_of_the_first_tick_s_commit(self):
        # fix wave A: a path staged as added and then deleted (AD) is in neither HEAD
        # nor the index once it is added, and a tracked letter removed with git rm is
        # in HEAD only: neither stops the commit of the other leftovers
        home = self.lettered_home()
        self.fx.write(home, "inbox/.seen-x.md", "surfaced\n")
        self.fx.git(home, "add", "inbox/.seen-x.md")
        (home / "inbox" / ".seen-x.md").unlink()
        self.fx.git(home, "rm", "-q", "inbox/2026-11-18-a-note.md")
        self.fx.write(home, "inbox/2026-11-19-placed.md", "An uncommitted letter.\n")
        self.assertEqual(self.status(home), "AD inbox/.seen-x.md\nD  inbox/2026-11-18-a-note.md\n"
                                            "?? inbox/2026-11-19-placed.md")
        self.assertEqual(self.tend("kalam"), [
            "kalam: old-code leftover committed as the fleet (pushed): inbox/2026-11-18-a-note.md, "
            "inbox/2026-11-19-placed.md",
            tend.FIRST_TICK_LINE])
        self.assertEqual(self.files_in(home), [("A", "inbox/2026-11-19-placed.md"),
                                               ("D", "inbox/2026-11-18-a-note.md")])
        self.assertEqual(self.status(home), "")
        self.assertTrue(tend.first_tend_done(self.fx.state, "kalam"))

    def test_an_untracked_letter_or_journal_is_set_aside_never_deleted(self):
        # fix wave A, a deliberate change from spec section 6's "removed": an
        # untracked regular file under inbox/ or beats/ may be a letter a person
        # placed, so it is moved out of the home, into the fleet's state folder, and
        # logged by name, and since the final review so is an untracked REST; a
        # link is still removed
        home = self.lettered_home()
        self.past_first_tick("kalam")
        placed = {"inbox/2026-11-19-placed.md": "A letter a person placed.\n",
                  "inbox/nested/x.md": "Inside a folder.\n",
                  "beats/2026-12.md": "A journal never committed.\n"}
        for rel, text in placed.items():
            self.fx.write(home, rel, text)
        self.fx.link(home, "inbox/2026-11-19-linked.md", self.fx.secret)
        self.fx.write(home, "REST", "left by a killed beat\n")
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(self.tend("kalam"), [
            "kalam: a killed beat's leftovers restored: REST, beats/2026-12.md, inbox/2026-11-19-linked.md, "
            "inbox/2026-11-19-placed.md, inbox/nested/x.md"] + [
            f"kalam: {rel} was never committed — set aside at {aside / rel}, not deleted"
            for rel in ("REST", "beats/2026-12.md", "inbox/2026-11-19-placed.md", "inbox/nested/x.md")])
        placed["REST"] = "left by a killed beat\n"
        for rel, text in placed.items():
            self.assertFalse(os.path.lexists(home / rel), rel)
            self.assertEqual((aside / rel).read_text(encoding="utf-8"), text, rel)
        for rel in ("REST", "inbox/2026-11-19-linked.md", "inbox/nested"):
            self.assertFalse(os.path.lexists(home / rel), rel)
        self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")
        self.assertEqual(self.status(home), "")
        # the same name set aside again in the same minute goes beside it, never over it
        self.fx.write(home, "inbox/2026-11-19-placed.md", "Placed again.\n")
        again = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z-2" / "inbox/2026-11-19-placed.md"
        self.assertEqual(self.tend("kalam")[1:], [
            f"kalam: inbox/2026-11-19-placed.md was never committed — set aside at {again}, not deleted"])
        self.assertEqual(again.read_text(encoding="utf-8"), "Placed again.\n")
        self.assertEqual((aside / "inbox/2026-11-19-placed.md").read_text(encoding="utf-8"),
                         "A letter a person placed.\n")

    def test_a_git_that_cannot_read_head_or_the_index_puts_nothing_back(self):
        # fix wave A: a failed ls-tree is not "nothing in HEAD", and a failed
        # update-index stops the step before anything is removed
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.fx.write(home, "CADENCE.md", "changed by a killed beat\n")
        self.fx.write(home, "REST", "left by a killed beat\n")
        real = tend._git
        for failing in ("ls-tree", "update-index"):
            with self.subTest(failing=failing):
                def git(home_, *args, **kw):
                    if failing in args:
                        return subprocess.CompletedProcess(args, 128, "", f"fatal: {failing} failed")
                    return real(home_, *args, **kw)
                files, index = snapshot(home), self.fx.git(home, "ls-files", "--stage")
                with mock.patch.object(tend, "_git", git):
                    with self.assertRaises(tend.TendError) as cm:
                        tend.restore_paths(home, ["CADENCE.md", "REST"], self.fx.state)
                    self.assertEqual(self.tend("kalam"), [f"kalam: not tended — {cm.exception}"])
                self.assertIn(f"{failing} failed", str(cm.exception))
                self.assertEqual((snapshot(home), self.fx.git(home, "ls-files", "--stage")), (files, index))

    def test_a_home_held_for_a_put_back_says_why_every_tick(self):
        # fix wave A (bd): sync holds a home with a put-back pending and says so
        # once; tend says on every tick why it is not finished, also where it stops
        # before trying (mid-merge, links, a fresh index.lock), and, when the
        # commit before the beat cannot be read, how a person clears it
        for case in ("mid-merge", "links", "index-lock", "no-base"):
            with self.subTest(case=case):
                slug = case.replace("-", "")
                home = self.fx.make_home(slug, cadence=V2, when="2026-11-01T00:00Z")
                self.past_first_tick(slug)
                base = "0" * 40 if case == "no-base" else self.fx.head(home)
                self.fx.write(home, "EVENT-ONLY", "the beat's own\n")
                beat_head = self.fx.commit(home, "my own pace", author=h.beat_author(slug))
                key = f"{tend.PUT_BACK}:{base}:{beat_head}"
                attempts.mark_reported(self.fx.state, slug, key)
                waits = (f"{slug}: pace files a beat with tools committed itself wait to be put back "
                         "({why}) — nothing of this home is pushed until the fleet's tend has done it")
                if case == "mid-merge":
                    (home / ".git" / "MERGE_HEAD").write_text(beat_head + "\n", encoding="utf-8")
                    want = [waits.format(why="a merge or rebase is in progress")]
                elif case == "links":
                    self.fx.link(home, "beats/2026-11.md", self.fx.secret)
                    want = [waits.format(why="its pace paths include links: beats/2026-11.md")]
                elif case == "index-lock":
                    (home / ".git" / "index.lock").write_text("", encoding="utf-8")
                    want = [f"{slug}: .git/index.lock is fresh — not tended this tick",
                            waits.format(why=".git/index.lock is fresh")]
                else:
                    want = [f"{slug}: not tended — the commit before the beat ({base}) cannot be read; "
                            f"nothing was put back; a person puts its pace files back by hand, then removes "
                            f"\"{key}\" from \"reported\" in {self.fx.state / slug}, and sync holds the home "
                            "until then"]
                for _tick in range(2):
                    self.assertEqual(self.tend(slug), want)
                self.assertTrue(tend.put_back_pending(self.fx.state, slug))
                self.assertEqual(self.fx.head(home), beat_head)

    def test_files_set_aside_before_a_failure_are_named(self):
        # fix round 1: untracked files are set aside first; when a later checkout
        # fails, where they went is still said, and the home is "partly tended"
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.fx.write(home, "CADENCE.md", "changed by a killed beat\n")
        self.fx.write(home, "inbox/2026-11-19-placed.md", "A letter a person placed.\n")
        real = tend._git

        def git(home_, *args, **kw):
            if "checkout" in args:
                return subprocess.CompletedProcess(args, 1, "", "error: the checkout failed")
            return real(home_, *args, **kw)

        dest = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z" / "inbox/2026-11-19-placed.md"
        with mock.patch.object(tend, "_git", git):
            lines = self.tend("kalam")
        self.assertEqual(lines, [
            "kalam: partly tended — git checkout CADENCE.md failed: error: the checkout failed; set aside "
            f"before it stopped, not deleted: inbox/2026-11-19-placed.md at {dest}"])
        self.assertEqual(dest.read_text(encoding="utf-8"), "A letter a person placed.\n")
        # the guard says the same in its words
        self.fx.write(home, "inbox/2026-11-19-again.md", "Placed again.\n")
        again = dest.parent / "2026-11-19-again.md"
        with mock.patch.object(tend, "_git", git):
            with self.assertRaises(tend.TendError) as cm:
                tend.guard_restore(home, self.fx.state)
        self.assertIn(f"set aside before it stopped, not deleted: inbox/2026-11-19-again.md at {again}",
                      str(cm.exception))
        self.assertEqual(cm.exception.aside, (("inbox/2026-11-19-again.md", str(again)),))

    def test_a_nested_repository_is_set_aside_whole_never_deleted(self):
        # fix round 1: git lists a repository inside inbox/ or beats/ as one entry
        # ending in "/"; it is moved out whole, at the first tick and after it
        for slug, first in (("kalam", False), ("soma", True)):
            with self.subTest(first_tick=first):
                home = self.lettered_home(slug)
                if not first:
                    self.past_first_tick(slug)
                inner = home / "inbox" / "nested"
                inner.mkdir()
                self.fx.write(inner, "a.md", "kept in a repository of its own\n")
                self.fx.git(inner, "init", "-q")
                self.fx.commit(inner, "inner")
                self.assertIn("?? inbox/nested/", self.status(home))
                dest = tend.set_aside_dir(self.fx.state, slug) / "20261120T0600Z" / "inbox/nested"
                lines = self.tend(slug)
                self.assertIn(f"{slug}: inbox/nested/ was never committed — set aside at {dest}, not deleted",
                              lines)
                self.assertFalse(os.path.lexists(inner))
                self.assertEqual((dest / "a.md").read_text(encoding="utf-8"), "kept in a repository of its own\n")
                self.assertTrue((dest / ".git").is_dir())
                self.assertEqual(self.status(home), "")

    def test_nothing_tend_discards_is_lost_without_a_copy(self):
        # final review (C): after a home's first tend, an untracked REST, EVENT-ONLY
        # or CADENCE.md is moved out, and an uncommitted change to a tracked pace
        # file, letter or journal is copied out before the file is put back; each is
        # named, and a second in the same minute goes beside the first, never over
        # it. Only a link is removed with no copy, never followed
        home = self.lettered_home()
        plain = self.fx.make_home("ud", rest=hr.AI_REST, when="2026-10-08T12:00Z")
        self.past_first_tick("kalam", "ud")
        committed = {rel: (home / rel).read_bytes()
                     for rel in ("CADENCE.md", "inbox/2026-11-18-a-note.md", "beats/2026-11.md")}
        self.fx.write(home, "CADENCE.md", "a pace written by hand\n")
        self.fx.write(home, "inbox/2026-11-18-a-note.md", "A note, edited by hand.\n")
        self.fx.write(home, "beats/2026-11.md", "a journal edited by hand\n")
        self.fx.write(home, "REST", "a rest by hand\n")
        self.fx.write(home, "EVENT-ONLY", "listening, by hand\n")
        self.fx.link(home, "inbox/2026-11-19-linked.md", self.fx.secret)
        self.fx.write(plain, "CADENCE.md", "a pace by hand, never committed\n")
        left = {rel: (home / rel).read_bytes() for rel in ("CADENCE.md", "inbox/2026-11-18-a-note.md",
                                                           "beats/2026-11.md", "REST", "EVENT-ONLY")}
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        lines = self.tend("kalam", "ud")
        self.assertEqual(lines, [
            "kalam: a killed beat's leftovers restored: CADENCE.md, EVENT-ONLY, REST, beats/2026-11.md, "
            "inbox/2026-11-18-a-note.md, inbox/2026-11-19-linked.md"] + [
            f"kalam: {rel} was never committed — set aside at {aside / rel}, not deleted"
            for rel in ("EVENT-ONLY", "REST")] + [
            f"kalam: {rel} had a change that was never committed — a copy set aside at {aside / rel}, not deleted"
            for rel in ("CADENCE.md", "beats/2026-11.md", "inbox/2026-11-18-a-note.md")] + [
            "ud: a killed beat's leftovers restored: CADENCE.md",
            f"ud: CADENCE.md was never committed — set aside at "
            f"{tend.set_aside_dir(self.fx.state, 'ud') / '20261120T0600Z' / 'CADENCE.md'}, not deleted"])
        self.assertEqual({rel: (aside / rel).read_bytes() for rel in left}, left)
        self.assertEqual({rel: (home / rel).read_bytes() for rel in committed}, committed)
        for rel in ("REST", "EVENT-ONLY", "inbox/2026-11-19-linked.md"):
            self.assertFalse(os.path.lexists(home / rel), rel)
        self.assertFalse(os.path.lexists(aside / "inbox/2026-11-19-linked.md"))   # a link: never copied
        self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")
        self.assertEqual((tend.set_aside_dir(self.fx.state, "ud") / "20261120T0600Z" / "CADENCE.md")
                         .read_text(encoding="utf-8"), "a pace by hand, never committed\n")
        self.assertEqual((self.status(home), self.status(plain)), ("", ""))
        # the same minute again: beside the first, never over it
        self.fx.write(home, "CADENCE.md", "written by hand again\n")
        again = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z-2" / "CADENCE.md"
        self.assertEqual(self.tend("kalam"), [
            "kalam: a killed beat's leftovers restored: CADENCE.md",
            f"kalam: CADENCE.md had a change that was never committed — a copy set aside at {again}, not deleted"])
        self.assertEqual(again.read_text(encoding="utf-8"), "written by hand again\n")
        self.assertEqual((aside / "CADENCE.md").read_bytes(), left["CADENCE.md"])

    def test_a_change_that_cannot_be_copied_out_is_not_put_back(self):
        # final review (C): fail closed. When a copy cannot be made, nothing of the
        # home is changed and it is reported; no copy made before it is left behind
        home = self.lettered_home()
        self.past_first_tick("kalam")
        self.fx.write(home, "beats/2026-11.md", "a journal edited by hand\n")
        self.fx.write(home, "CADENCE.md", "a pace written by hand\n")
        self.fx.write(home, "REST", "a rest by hand\n")
        files, index = snapshot(home), self.fx.git(home, "ls-files", "--stage")
        real = tend._copy_aside

        def copy(home_, rel, state):
            if rel == "beats/2026-11.md":
                raise PermissionError(13, "Permission denied", "set-aside")
            return real(home_, rel, state)

        with mock.patch.object(tend, "_copy_aside", copy):
            lines = self.tend("kalam")
        self.assertEqual(lines, ["kalam: not tended — beats/2026-11.md: its uncommitted change could not be "
                                 "copied out of the home ([Errno 13] Permission denied: 'set-aside'); "
                                 "nothing was put back"])
        self.assertEqual((snapshot(home), self.fx.git(home, "ls-files", "--stage")), (files, index))
        self.assertEqual([p for p in tend.set_aside_dir(self.fx.state, "kalam").rglob("*") if p.is_file()], [])
        # a set-aside folder that takes nothing: the same, in the words of the error
        base = tend.set_aside_dir(self.fx.state, "kalam")
        base.mkdir(parents=True, exist_ok=True)
        base.chmod(0o500)
        self.addCleanup(base.chmod, 0o700)
        lines = self.tend("kalam")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("kalam: not tended — CADENCE.md: its uncommitted change could not "
                                            "be copied out of the home ("), lines[0])
        self.assertTrue(lines[0].endswith("; nothing was put back"), lines[0])
        self.assertEqual((snapshot(home), self.fx.git(home, "ls-files", "--stage")), (files, index))


# -- citizen-beat.sh's guard -------------------------------------------------------

class TestGuardRestore(Case):
    def test_a_beat_with_tools_keeps_no_change_to_a_pace_file(self):
        # a home with none of REST, EVENT-ONLY or inbox/ at HEAD (spec section 8)
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.fx.write(home, "CADENCE.md", V2.replace("- pace: asleep", "- pace: awake, every 3 days"))
        self.fx.write(home, "REST", "I rest.\n")
        self.fx.write(home, "inbox/.seen-2026-11-18-a-note.md", "seen by a beat with tools\n")
        self.fx.write(home, "beats/2026-11.md", "A beat with tools wrote this.\n")
        (home / "WILL.md").write_text("a will the beat edited\n", encoding="utf-8")
        got = tend.guard_restore(home, self.fx.state)
        self.assertEqual(got, ["CADENCE.md", "REST", "inbox/.seen-2026-11-18-a-note.md"])
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), V2)
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertFalse(os.path.lexists(home / "inbox"))
        self.assertEqual(self.status(home), "M WILL.md\n?? beats/2026-11.md")
        rec = json.loads((self.fx.state / "kalam").read_text(encoding="utf-8"))
        self.assertEqual(rec["notices"], [{"key": "restored", "at": NOW}])
        self.assertEqual(rec["slug"], "kalam")
        # in attempts.py's format, so the next paragraph finds it
        self.assertEqual(attempts.notice_keys(attempts.load(self.fx.state, "kalam")), ["restored"])
        self.assertEqual(tend.guard_line("kalam", got),
                         "kalam: pace files put back after a beat with tools: CADENCE.md, REST, "
                         "inbox/.seen-2026-11-18-a-note.md")

    def test_tracked_letters_and_markers_are_checked_out(self):
        home = self.fx.make_home("kalam", cadence=V2, rest=True, when="2026-11-01T00:00Z",
                                 letters=(("2026-11-18-a-note.md", "A note.\n"),),
                                 files={"inbox/.seen-2026-11-18-a-note.md": "surfaced 2026-11-19\n"})
        (home / "inbox" / "2026-11-18-a-note.md").write_text("rewritten\n", encoding="utf-8")
        (home / "inbox" / ".seen-2026-11-18-a-note.md").unlink()
        (home / "REST").unlink()
        self.fx.write(home, "EVENT-ONLY", "listening\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state),
                         ["EVENT-ONLY", "REST", "inbox/.seen-2026-11-18-a-note.md",
                          "inbox/2026-11-18-a-note.md"])
        self.assertEqual(self.status(home), "")

    def test_links_planted_by_a_beat_are_removed_never_followed(self):
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z",
                                 letters=(("2026-11-18-a-note.md", "A note.\n"),))
        outside = self.fx.outside / "box"
        outside.mkdir()
        (outside / "kept.md").write_text("outside the home\n", encoding="utf-8")
        (home / "CADENCE.md").unlink()
        self.fx.link(home, "CADENCE.md", self.fx.secret)
        self.fx.link(home, "REST", self.fx.secret)
        os.rename(home / "inbox", self.fx.root / "moved-inbox")
        self.fx.link(home, "inbox", outside)
        self.assertEqual(tend.guard_restore(home, self.fx.state),
                         ["CADENCE.md", "REST", "inbox", "inbox/2026-11-18-a-note.md"])
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), V2)
        self.assertFalse(os.path.islink(home / "CADENCE.md"))
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertFalse(os.path.islink(home / "inbox"))
        self.assertEqual((home / "inbox" / "2026-11-18-a-note.md").read_text(encoding="utf-8"), "A note.\n")
        self.assertEqual(self.fx.secret.read_text(encoding="utf-8"), f"{h.CANARY}\n")
        self.assertEqual(sorted(os.listdir(outside)), ["kept.md"])
        self.assertEqual(self.status(home), "")

    def test_staged_changes_are_put_back_too(self):
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.fx.write(home, "CADENCE.md", "changed and staged\n")
        self.fx.write(home, "EVENT-ONLY", "staged\n")
        self.fx.git(home, "add", "CADENCE.md", "EVENT-ONLY")
        self.assertEqual(tend.guard_restore(home, self.fx.state), ["CADENCE.md", "EVENT-ONLY"])
        self.assertEqual(self.status(home), "")
        self.assertEqual(self.fx.git(home, "diff", "--cached", "--name-only"), "")

    def test_a_beat_that_changed_no_pace_file_writes_nothing(self):
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.fx.write(home, "beats/2026-11.md", "A beat with tools wrote this.\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state), [])
        self.assertFalse((self.fx.state / "kalam").exists())

    def test_the_attempt_record_keeps_its_fields_and_the_notice_once(self):
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        old = {"slug": "kalam", "last_beat": "2026-11-15T14:12Z", "seeded": "now",
               "attempt": {"id": "a1", "at": "2026-11-15T14:12Z", "outcome": "spoken"},
               "failures": 0, "retry_after": None, "reported": ["links"], "notices": [], "busy_ticks": 1}
        self.fx.state.mkdir(parents=True, exist_ok=True)
        (self.fx.state / "kalam").write_text(json.dumps(old), encoding="utf-8")
        for _ in range(2):
            self.fx.write(home, "REST", "again\n")
            self.assertEqual(tend.guard_restore(home, self.fx.state), ["REST"])
        rec = json.loads((self.fx.state / "kalam").read_text(encoding="utf-8"))
        self.assertEqual(rec, {**old, "attempt": {**old["attempt"], "before": None},
                               "notices": [{"key": "restored", "at": NOW}]})
        self.assertFalse(tend.add_attempt_notice(self.fx.state, "kalam", "restored"))
        # a landed beat's outcome keeps the notice for its paragraph
        attempts.record_outcome(self.fx.state, "kalam", "a2", "spoken", NOW)
        self.assertEqual(attempts.notice_keys(attempts.load(self.fx.state, "kalam")), ["restored"])
        # an unreadable record starts again from the contract's defaults
        (self.fx.state / "soma").write_text("not json", encoding="utf-8")
        self.assertTrue(tend.add_attempt_notice(self.fx.state, "soma", "restored"))
        self.assertEqual(json.loads((self.fx.state / "soma").read_text(encoding="utf-8")), {
            "slug": "soma", "last_beat": None, "seeded": None, "attempt": None, "failures": 0,
            "retry_after": None, "reported": [], "notices": [{"key": "restored", "at": NOW}],
            "busy_ticks": 0})

    def test_an_ignored_pace_file_is_put_back_too(self):
        # fix wave A: a beat with tools that writes REST, or marks a letter seen,
        # and hides it in .gitignore still sets no pace
        v1 = ("# CADENCE — chosen by kalam\n\n- choice: undecided\n- when: 2026-10-27 03:10\n"
              "- how: the Letter of Return, answered in a free beat.\n")
        home = self.fx.make_home("kalam", cadence=v1, when="2026-11-01T00:00Z")
        self.assertIsNone(cad.read_cadence(home, "kalam", {}).pace)
        self.fx.write(home, "REST", "chosen via the Letter of Return 2026-11-19 — woken only by unread mail\n")
        self.fx.write(home, "inbox/.seen-2026-11-18-a-note.md", "seen by a beat with tools\n")
        self.fx.write(home, ".gitignore", "REST\ninbox/\n")
        notes = []
        self.assertEqual(tend.guard_restore(home, self.fx.state, notes=notes),
                         ["REST", "inbox/.seen-2026-11-18-a-note.md"])
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertFalse(os.path.lexists(home / "inbox"))
        self.assertIsNone(cad.read_cadence(home, "kalam", {}).pace)
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(notes, [f"kalam: {rel} was never committed — set aside at {aside / rel}, not deleted"
                                 for rel in ("REST", "inbox/.seen-2026-11-18-a-note.md")])
        self.assertEqual((aside / "REST").read_text(encoding="utf-8"),
                         "chosen via the Letter of Return 2026-11-19 — woken only by unread mail\n")
        self.assertEqual(self.status(home), "?? .gitignore")       # not a pace file: left alone

    def test_the_guard_keeps_a_copy_of_what_it_puts_back(self):
        # final review (C): a beat with tools that edits a tracked pace file or letter,
        # or leaves an ignored EVENT-ONLY or CADENCE.md, sets no pace, and loses
        # nothing: the edit is copied out before the file is put back, and the
        # marker is moved out, each named in the notes
        home = self.fx.make_home("kalam", cadence=V2, letters=(("2026-11-18-a-note.md", "A note.\n"),),
                                 when="2026-11-01T00:00Z")
        other = self.fx.make_home("ud", rest=hr.AI_REST, when="2026-10-08T12:00Z")
        self.fx.write(home, "CADENCE.md", V2.replace("- pace: asleep", "- pace: awake, every 3 days"))
        self.fx.write(home, "inbox/2026-11-18-a-note.md", "A note, rewritten by a beat with tools.\n")
        self.fx.write(home, "EVENT-ONLY", "listening\n")
        self.fx.write(home, ".gitignore", "EVENT-ONLY\n")
        self.fx.write(other, "CADENCE.md", "a pace by a beat with tools\n")
        self.fx.write(other, ".gitignore", "CADENCE.md\n")
        left = {rel: (home / rel).read_bytes() for rel in ("CADENCE.md", "EVENT-ONLY", "inbox/2026-11-18-a-note.md")}
        notes = []
        self.assertEqual(tend.guard_restore(home, self.fx.state, notes=notes),
                         ["CADENCE.md", "EVENT-ONLY", "inbox/2026-11-18-a-note.md"])
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(notes, [
            f"kalam: EVENT-ONLY was never committed — set aside at {aside / 'EVENT-ONLY'}, not deleted"] + [
            f"kalam: {rel} had a change that was never committed — a copy set aside at {aside / rel}, not deleted"
            for rel in ("CADENCE.md", "inbox/2026-11-18-a-note.md")])
        self.assertEqual({rel: (aside / rel).read_bytes() for rel in left}, left)
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), V2)
        self.assertEqual((home / "inbox/2026-11-18-a-note.md").read_text(encoding="utf-8"), "A note.\n")
        self.assertFalse(os.path.lexists(home / "EVENT-ONLY"))
        notes = []
        self.assertEqual(tend.guard_restore(other, self.fx.state, notes=notes), ["CADENCE.md"])
        ud = tend.set_aside_dir(self.fx.state, "ud") / "20261120T0600Z" / "CADENCE.md"
        self.assertEqual(notes, [f"ud: CADENCE.md was never committed — set aside at {ud}, not deleted"])
        self.assertEqual(ud.read_text(encoding="utf-8"), "a pace by a beat with tools\n")
        self.assertFalse(os.path.lexists(other / "CADENCE.md"))

    def test_pace_files_in_another_case_are_put_back(self):
        # fix wave A: on the Mac's case-insensitive disk Rest is REST, and on any disk
        # a clone made elsewhere reads it so; git is asked for the pace paths in any case
        home = self.fx.make_home("kalam", when="2026-11-01T00:00Z")
        for rel in ("Rest", "Event-Only", "cadence.md", "Inbox/.seen-a.md"):
            self.fx.write(home, rel, "written by a beat with tools\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state),
                         ["Event-Only", "Inbox/.seen-a.md", "Rest", "cadence.md"])
        self.assertEqual(sorted(os.listdir(home)), [".git", "WILL.md", "kalam.md"])
        self.assertEqual(self.status(home), "")
        self.assertEqual(tend.changed_paths(home, tend.TEND_PATHS), [])

    def test_a_pace_change_the_beat_committed_itself_is_put_back_against_the_base(self):
        # fix wave A: a beat with full tools can commit its own pace change; the guard
        # checks against the HEAD recorded before the beat, puts the pace files back
        # as that commit has them, and commits that as the fleet
        home = self.fx.make_home("kalam", cadence=V2, letters=(("2026-11-18-a-note.md", "A note.\n"),),
                                 when="2026-11-01T00:00Z")
        base = self.fx.head(home)
        self.fx.write(home, "CADENCE.md", V2.replace("- pace: asleep", "- pace: awake, every 3 days"))
        self.fx.write(home, "REST", "mine\n")
        self.fx.write(home, "inbox/.seen-2026-11-18-a-note.md", "seen by a beat with tools\n")
        self.fx.write(home, "notes.md", "made\n")
        self.fx.commit(home, "my own commit", author=h.beat_author("kalam"))
        self.fx.write(home, "EVENT-ONLY", "and one more, never committed\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state, base=base),
                         ["CADENCE.md", "EVENT-ONLY", "REST", "inbox/.seen-2026-11-18-a-note.md"])
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), V2)
        for rel in ("REST", "EVENT-ONLY", "inbox/.seen-2026-11-18-a-note.md"):
            self.assertFalse(os.path.lexists(home / rel), rel)
        self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{tend.GUARD_SUBJECT}")
        self.assertEqual(self.files_in(home), [("D", "REST"), ("D", "inbox/.seen-2026-11-18-a-note.md"),
                                               ("M", "CADENCE.md")])
        self.assertEqual(self.fx.git(home, "show", "HEAD:notes.md"), "made")   # its own work stays
        self.assertEqual(self.status(home), "")
        self.assertFalse(cad.changed_outside_pace(home))
        self.assertEqual(attempts.notice_keys(attempts.load(self.fx.state, "kalam")), ["restored"])
        # the same base again finds nothing; a base that cannot be read puts nothing back
        head = self.fx.head(home)
        self.assertEqual(tend.guard_restore(home, self.fx.state, base=base), [])
        self.fx.write(home, "REST", "again\n")
        with self.assertRaises(tend.TendError) as cm:
            tend.guard_restore(home, self.fx.state, base="0" * 40)
        self.assertIn("the commit before the beat", str(cm.exception))
        self.assertTrue((home / "REST").exists())
        self.assertEqual(self.fx.head(home), head)

    def test_a_partial_guard_restore_still_tells_the_citizen_or_says_it_could_not(self):
        # fix wave A: when putting back stops midway, what was put back is still told
        # to the citizen once; when the notice cannot be kept, the guard says so
        real = tend._git

        def git(home_, *args, **kw):
            if "checkout" in args:
                return subprocess.CompletedProcess(args, 1, "", "error: the checkout failed")
            return real(home_, *args, **kw)

        for slug in ("kalam", "soma"):
            with self.subTest(slug=slug):
                home = self.fx.make_home(slug, cadence=V2, when="2026-11-01T00:00Z")
                self.fx.write(home, "CADENCE.md", "changed by a beat with tools\n")
                self.fx.write(home, "REST", "mine\n")
                if slug == "soma":
                    self.fx.state.mkdir(parents=True, exist_ok=True)
                    (self.fx.state / "soma").symlink_to(self.fx.outside / "elsewhere")
                with mock.patch.object(tend, "_git", git):
                    with self.assertRaises(tend.TendError) as cm:
                        tend.guard_restore(home, self.fx.state)
                said = str(cm.exception)
                self.assertTrue(said.startswith("git checkout CADENCE.md failed: error: the checkout failed"),
                                said)
                self.assertFalse(os.path.lexists(home / "REST"))        # put back before it stopped
                if slug == "kalam":
                    self.assertTrue(said.endswith("; the citizen's next free beat is told once"), said)
                    self.assertEqual(attempts.notice_keys(attempts.load(self.fx.state, slug)), ["restored"])
                else:
                    self.assertRegex(said, r"; the citizen could not be told \(.+\)$")
        # a full put-back whose notice cannot be kept says so in its notes
        home = self.fx.make_home("ud", cadence=V2, when="2026-11-01T00:00Z")
        self.fx.write(home, "REST", "mine\n")
        (self.fx.state / "ud").symlink_to(self.fx.outside / "elsewhere")
        notes = []
        self.assertEqual(tend.guard_restore(home, self.fx.state, notes=notes), ["REST"])
        self.assertEqual(len(notes), 2, notes)
        rest = tend.set_aside_dir(self.fx.state, "ud") / "20261120T0600Z" / "REST"
        self.assertEqual(notes[0], f"ud: REST was never committed — set aside at {rest}, not deleted")
        self.assertRegex(notes[1], r"^ud: its notice could not be kept \(.+\) — its next free beat is not "
                                   r"told that its pace files were put back$")

    def test_a_put_back_the_guard_could_not_commit_is_finished_by_the_next_tend(self):
        # fix round 1: a beat with full tools committed its own pace change and the
        # guard could not commit the put-back; the fleet keeps the commit before the
        # beat, and the next tend puts back what the beat changed, as that commit
        # has it, and commits it, leaving alone what a later commit changed again
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.past_first_tick("kalam")
        base = self.fx.head(home)
        self.fx.write(home, "CADENCE.md", V2.replace("- pace: asleep", "- pace: awake, every 3 days"))
        self.fx.write(home, "REST", "mine\n")
        self.fx.commit(home, "my own pace", author=h.beat_author("kalam"))
        real = tend._git

        def git(home_, *args, **kw):
            if "commit" in args:
                return subprocess.CompletedProcess(args, 1, "", "error: gpg failed to sign the data")
            return real(home_, *args, **kw)

        with mock.patch.object(tend, "_git", git):
            with self.assertRaises(tend.TendError) as cm:
                tend.guard_restore(home, self.fx.state, base=base)
        said = str(cm.exception)
        self.assertIn("the citizen's next free beat is told once", said)
        self.assertIn(f"the fleet's tend puts them back as {base[:12]} has them, and nothing is pushed until "
                      "then", said)
        self.assertTrue(tend.put_back_pending(self.fx.state, "kalam"))
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), V2)   # on disk, at once
        self.assertFalse(os.path.lexists(home / "REST"))
        # a later fleet commit writes REST again: that is not the beat's, and stays
        self.fx.write(home, "REST", "written by a later fleet commit\n")
        self.fx.commit(home, f"tend: a later fleet commit · {TAG}", author=FLEET, paths=["REST"])
        self.assertEqual(self.tend("kalam"), [
            f"kalam: pace files a beat with tools committed itself put back as {base[:12]} has them, "
            "as the fleet: CADENCE.md"])
        self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{tend.GUARD_SUBJECT}")
        self.assertEqual(self.files_in(home), [("M", "CADENCE.md")])
        self.assertEqual(self.fx.git(home, "show", "HEAD:CADENCE.md") + "\n", V2)
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), "written by a later fleet commit\n")
        self.assertEqual(self.status(home), "")
        self.assertFalse(tend.put_back_pending(self.fx.state, "kalam"))
        self.assertEqual(attempts.notice_keys(attempts.load(self.fx.state, "kalam")), ["restored"])
        self.assertEqual(self.tend("kalam"), [])

    def test_what_a_beat_pulled_from_upstream_is_not_put_back(self):
        # fix wave A (az): a beat with full tools that pulls brings upstream commits
        # in after the base: the citizen's own REST and CADENCE.md, chosen on another
        # host, and a person's letter. Only what the beat itself wrote is put back: a
        # file upstream changed, then the beat changed again, as upstream has it.
        # A commit the beat pushed itself is still its own.
        resting = V2.replace("- pace: asleep", "- pace: asleep, letters wait")
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        bare = self.fx.give_upstream(home)
        base = self.fx.head(home)
        other = self.fx.elsewhere(bare)
        self.fx.write(other, "REST", "letters wait — chosen in kalam's own free beat on the Studio\n")
        self.fx.write(other, "CADENCE.md", resting)
        self.fx.write(other, "inbox/2026-11-19-from-yu.md", "Dear kalam\n")
        self.fx.commit(other, "beat: kalam reflected (2026-11-19) · pace/1", author=h.beat_author("kalam"))
        self.fx.git(other, "push", "-q", "origin", "HEAD")
        # the beat here: its own work, its own EVENT-ONLY and letter, a pull, then
        # its own edit of the CADENCE.md upstream brought, and a push
        self.fx.write(home, "notes.md", "made\n")
        self.fx.write(home, "EVENT-ONLY", "the beat's own\n")
        self.fx.write(home, "inbox/2026-11-19-mine.md", "the beat's own\n")
        self.fx.commit(home, "beat work", author=h.beat_author("kalam"))
        self.fx.git(home, "pull", "-q", "--no-rebase", "--no-edit")
        self.fx.write(home, "CADENCE.md", resting.replace("letters wait", "awake, every 3 days"))
        self.fx.commit(home, "my own pace", author=h.beat_author("kalam"))
        self.fx.git(home, "push", "-q", "origin", "HEAD")
        notes = []
        self.assertEqual(tend.guard_restore(home, self.fx.state, base=base, notes=notes),
                         ["CADENCE.md", "EVENT-ONLY", "inbox/2026-11-19-mine.md"])
        aside = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z"
        self.assertEqual(notes, [f"kalam: {rel} came only in a beat with tools' own commits — set aside at "
                                 f"{aside / rel}, not deleted"
                                 for rel in ("EVENT-ONLY", "inbox/2026-11-19-mine.md")])   # upstream's letter is not
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), resting)
        self.assertEqual((home / "REST").read_text(encoding="utf-8"),
                         "letters wait — chosen in kalam's own free beat on the Studio\n")
        self.assertEqual((home / "inbox" / "2026-11-19-from-yu.md").read_text(encoding="utf-8"), "Dear kalam\n")
        self.assertFalse(os.path.lexists(home / "EVENT-ONLY"))
        self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{tend.GUARD_SUBJECT}")
        self.assertEqual(self.files_in(home), [("D", "EVENT-ONLY"), ("D", "inbox/2026-11-19-mine.md"),
                                               ("M", "CADENCE.md")])
        self.assertEqual(self.fx.git(home, "show", "HEAD:notes.md"), "made")
        self.assertEqual(self.status(home), "")
        # a beat that only pushes its own pace change has it put back all the same
        soma = self.fx.make_home("soma", cadence=V2, when="2026-11-01T00:00Z")
        self.fx.give_upstream(soma)
        base = self.fx.head(soma)
        self.fx.write(soma, "REST", "the beat's own\n")
        self.fx.commit(soma, "my own rest", author=h.beat_author("soma"))
        self.fx.git(soma, "push", "-q", "origin", "HEAD")
        self.assertEqual(tend.guard_restore(soma, self.fx.state, base=base), ["REST"])
        self.assertFalse(os.path.lexists(soma / "REST"))
        self.assertEqual(self.last(soma), f"{FLEET}|{FLEET}|{tend.GUARD_SUBJECT}")
        # the fleet's tend, finishing a put-back the guard could not commit, leaves
        # upstream's REST too
        teo = self.fx.make_home("teotlme", cadence=V2, when="2026-11-01T00:00Z")
        self.past_first_tick("teotlme")
        bare = self.fx.give_upstream(teo)
        base = self.fx.head(teo)
        self.fx.push_from_elsewhere(bare, "REST", "chosen on the Studio\n", author=h.beat_author("teotlme"))
        self.fx.write(teo, "EVENT-ONLY", "the beat's own\n")
        self.fx.commit(teo, "my own pace", author=h.beat_author("teotlme"))
        self.fx.git(teo, "pull", "-q", "--no-rebase", "--no-edit")
        real = tend._git

        def git(home_, *args, **kw):
            if "commit" in args:
                return subprocess.CompletedProcess(args, 1, "", "error: gpg failed to sign the data")
            return real(home_, *args, **kw)

        with mock.patch.object(tend, "_git", git):
            with self.assertRaises(tend.TendError) as cm:
                tend.guard_restore(teo, self.fx.state, base=base)
        self.assertIn("some of EVENT-ONLY may be put back already", str(cm.exception))
        self.assertEqual(self.tend("teotlme"), [
            f"teotlme: pace files a beat with tools committed itself put back as {base[:12]} has them, "
            "as the fleet: EVENT-ONLY"])
        self.assertEqual((teo / "REST").read_text(encoding="utf-8"), "chosen on the Studio\n")
        self.assertEqual(self.files_in(teo), [("D", "EVENT-ONLY")])

    def test_a_put_back_finished_later_keeps_the_tip_the_guard_saw(self):
        # fix round 1 of Part 2 (A1): a beat with full tools commits its own REST
        # and pushes it; its guard cannot commit the put-back; another host pushes
        # on top, and a fetch and fast-forward bring that in before the fleet's
        # tend. The upstream tip is the one the guard saw, kept in the key, never
        # read again later, so the beat's own REST is still put back
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.past_first_tick("kalam")
        bare = self.fx.give_upstream(home)
        base = self.fx.head(home)
        tip = self.fx.git(home, "rev-parse", "origin/main")
        self.fx.write(home, "REST", "the beat's own rest\n")
        beat_head = self.fx.commit(home, "my own rest", author=h.beat_author("kalam"))
        self.fx.git(home, "push", "-q", "origin", "HEAD")
        real = tend._git

        def git(home_, *args, **kw):
            if "commit" in args:
                return subprocess.CompletedProcess(args, 1, "", "error: gpg failed to sign the data")
            return real(home_, *args, **kw)

        with mock.patch.object(tend, "_git", git):
            with self.assertRaises(tend.TendError):
                tend.guard_restore(home, self.fx.state, base=base)
        self.assertIn(f"{tend.PUT_BACK}:{base}:{beat_head}:{tip}", attempts.load(self.fx.state, "kalam")["reported"])
        self.fx.git(home, "checkout", "-q", "HEAD", "--", "REST")      # as a later tend would find it
        self.fx.push_from_elsewhere(bare, "notes-elsewhere.md", "x\n", author=h.beat_author("kalam"))
        self.fx.git(home, "fetch", "-q", "origin")
        self.fx.git(home, "merge", "-q", "--ff-only", "@{u}")
        again = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z-2" / "REST"
        self.assertEqual(self.tend("kalam"), [
            f"kalam: pace files a beat with tools committed itself put back as {base[:12]} has them, "
            "as the fleet: REST",
            f"kalam: REST came only in a beat with tools' own commits — set aside at {again}, not deleted"])
        self.assertEqual(again.read_text(encoding="utf-8"), "the beat's own rest\n")
        self.assertFalse(os.path.lexists(home / "REST"))
        self.assertEqual(self.fx.git(home, "ls-tree", "--name-only", "HEAD", "--", "REST"), "")
        self.assertFalse(tend.put_back_pending(self.fx.state, "kalam"))

    def test_an_older_key_with_no_tip_puts_the_beat_s_change_back(self):
        # fix round 1 of Part 2: a key written before the tip was kept
        # (put-back:<base>:<head>) knows no upstream tip, so nothing counts as
        # upstream's: what the beat changed is put back
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.past_first_tick("kalam")
        bare = self.fx.give_upstream(home)
        base = self.fx.head(home)
        self.fx.write(home, "REST", "the beat's own rest\n")
        beat_head = self.fx.commit(home, "my own rest", author=h.beat_author("kalam"))
        self.fx.git(home, "push", "-q", "origin", "HEAD")
        attempts.mark_reported(self.fx.state, "kalam", f"{tend.PUT_BACK}:{base}:{beat_head}")
        self.fx.push_from_elsewhere(bare, "notes-elsewhere.md", "x\n", author=h.beat_author("kalam"))
        self.fx.git(home, "fetch", "-q", "origin")
        self.fx.git(home, "merge", "-q", "--ff-only", "@{u}")
        rest = tend.set_aside_dir(self.fx.state, "kalam") / "20261120T0600Z" / "REST"
        self.assertEqual(self.tend("kalam"), [
            f"kalam: pace files a beat with tools committed itself put back as {base[:12]} has them, "
            "as the fleet: REST",
            f"kalam: REST came only in a beat with tools' own commits — set aside at {rest}, not deleted"])
        self.assertFalse(os.path.lexists(home / "REST"))

    def test_a_put_back_that_finds_nothing_to_put_back_says_so(self):
        # fix round 1 of Part 2: finishing a put-back that puts nothing back is
        # said, once, as the key is cleared
        home = self.fx.make_home("kalam", cadence=V2, when="2026-11-01T00:00Z")
        self.past_first_tick("kalam")
        base = self.fx.head(home)
        self.fx.write(home, "notes.md", "the beat's own work\n")
        beat_head = self.fx.commit(home, "my own work", author=h.beat_author("kalam"))
        attempts.mark_reported(self.fx.state, "kalam", f"{tend.PUT_BACK}:{base}:{beat_head}:-")
        self.assertEqual(self.tend("kalam"), [
            f"kalam: a put-back left after a beat with tools found none of its pace files to put back as "
            f"{base[:12]} has them — cleared"])
        self.assertFalse(tend.put_back_pending(self.fx.state, "kalam"))
        self.assertEqual(self.fx.head(home), beat_head)
        self.assertEqual(self.tend("kalam"), [])

    def test_an_inherited_pathspec_setting_does_not_blind_the_guard(self):
        # fix round 1: GIT_LITERAL_PATHSPECS=1 would read ':(literal,icase)REST' as a
        # file of that name and match nothing; git is run without it
        home = self.fx.make_home("kalam", when="2026-11-01T00:00Z")
        self.fx.write(home, "Rest", "written by a beat with tools\n")
        for var in ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"):
            with self.subTest(var=var), mock.patch.dict(os.environ, {var: "1"}):
                self.assertEqual(tend.changed_paths(home, tend.GUARD_PATHS), ["Rest"])
        with mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": "1"}):
            self.assertEqual(tend.guard_restore(home, self.fx.state), ["Rest"])
        self.assertFalse(os.path.lexists(home / "Rest"))

    def test_an_ignored_file_the_pace_reader_never_reads_is_left_alone(self):
        # fix round 1: an ignored entry counts for the guard only when the pace
        # reader reads that name (CADENCE.md, REST, EVENT-ONLY in any case; a
        # letter or a .seen- marker directly in inbox/); a Finder .DS_Store is not
        home = self.fx.make_home("kalam", cadence=V2, letters=(("2026-11-18-a-note.md", "A note.\n"),),
                                 when="2026-11-01T00:00Z")
        for rel in ("inbox/.DS_Store", "inbox/nested/x.md", "inbox/notes.txt", ".DS_Store"):
            self.fx.write(home, rel, "not read by the pace code\n")
        self.fx.write(home, ".gitignore", ".DS_Store\ninbox/nested/\ninbox/*.txt\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state), [])
        self.assertFalse((self.fx.state / "kalam").exists())          # no notice
        self.assertFalse(tend.set_aside_dir(self.fx.state, "kalam").exists())
        self.fx.write(home, "inbox/.SEEN-2026-11-18-a-note.md", "seen\n")
        self.fx.write(home, "inbox/2026-11-19-x.md", "a letter\n")
        self.fx.write(home, "Event-Only", "listening\n")
        self.fx.write(home, ".gitignore", ".DS_Store\ninbox/\nEvent-Only\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state),
                         ["Event-Only", "inbox/.SEEN-2026-11-18-a-note.md", "inbox/2026-11-19-x.md"])
        for rel in ("inbox/.DS_Store", "inbox/nested/x.md", "inbox/notes.txt", ".DS_Store"):
            self.assertTrue((home / rel).exists(), rel)

    def test_an_ignored_folder_at_a_pace_name_is_put_back_too(self):
        # fix wave A (ba): a REST/ folder (or Event-Only/, cadence.md/) that
        # .gitignore hides reads as REST to keeps_rest and holds the home from
        # every beat; any ignored entry whose first part is a pace file's name
        # counts, at any depth and in any case
        home = self.fx.make_home("kalam", when="2026-11-01T00:00Z")
        for rel in ("REST/x", "Event-Only/a/b", "notes/REST"):
            self.fx.write(home, rel, "written by a beat with tools\n")
        self.fx.write(home, ".gitignore", "REST/\nEvent-Only/\nnotes/\n")
        self.assertEqual(tend.guard_restore(home, self.fx.state), ["Event-Only/a/b", "REST/x"])
        for rel in ("REST", "Event-Only"):
            self.assertFalse(os.path.lexists(home / rel), rel)
        self.assertTrue((home / "notes" / "REST").exists())        # not a pace name at its top


# -- rollback-prepare --------------------------------------------------------------

class TestRollbackPrepare(Case):
    def home(self, slug="kalam", seen=False):
        letters = (("2026-11-20-pace-letter.md", "# One question, once — your pace\n"),
                   ("2026-11-21-a-note.md", "A note.\n"))
        files = {"inbox/.seen-2026-11-20-pace-letter.md": "surfaced 2026-11-24\n"} if seen else None
        home = self.fx.make_home(slug, cadence=V2, rest=True, when="2026-11-01T00:00Z", letters=letters,
                                 files=files)
        self.fx.give_upstream(home)
        return home

    def test_unread_pace_letters_are_set_aside_and_pushed_as_the_fleet(self):
        home = self.home()
        bare = self.fx.upstream / "citizen-kalam.git"
        before = self.fx.head(home)
        self.assertEqual(tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state), [
            "kalam: inbox/2026-11-20-pace-letter.md set aside at rollback (pushed)"])
        self.assertEqual((home / "inbox" / ".seen-2026-11-20-pace-letter.md").read_text(encoding="utf-8"),
                         "set aside at rollback, not shown — 2026-11-20\n")
        self.assertEqual(self.fx.git(home, "rev-parse", "HEAD~1"), before)
        self.assertEqual(self.last(home), f"{FLEET}|{FLEET}|{ROLLBACK}")
        self.assertEqual(self.files_in(home), [("A", "inbox/.seen-2026-11-20-pace-letter.md")])
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), self.fx.head(home))
        self.assertEqual(self.status(home), "")

    def test_the_new_code_reads_the_marker_as_unread_and_the_old_code_as_seen(self):
        home = self.home()
        (home / "inbox" / "2026-11-21-a-note.md").unlink()
        self.fx.commit(home, "letter: removed")
        tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state)
        self.assertEqual(homes.unread_letters(home), ["inbox/2026-11-20-pace-letter.md"])
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 1)    # PR #24's gate: no mail

    def test_it_is_idempotent(self):
        home = self.home()
        tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state)
        head = self.fx.head(home)
        self.assertEqual(tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state), [])
        self.assertEqual(self.fx.head(home), head)

    def test_a_marker_left_uncommitted_by_an_earlier_run_is_committed(self):
        home = self.home()
        self.fx.write(home, "inbox/.seen-2026-11-20-pace-letter.md", "set aside at rollback, not shown — 2026-11-19\n")
        self.assertEqual(tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state), [
            "kalam: inbox/2026-11-20-pace-letter.md set aside at rollback (pushed)"])
        self.assertEqual(self.status(home), "")

    def test_a_seen_pace_letter_or_none_needs_nothing(self):
        seen = self.home("kalam", seen=True)
        none = self.fx.make_home("soma", rest=True, when="2026-11-01T00:00Z")
        heads = (self.fx.head(seen), self.fx.head(none))
        self.assertEqual(tend.rollback_prepare(self.fx.citizens, ["kalam", "soma", "palance"], self.fx.state), [])
        self.assertEqual((self.fx.head(seen), self.fx.head(none)), heads)

    def test_a_home_that_cannot_be_prepared_is_named(self):
        linked = self.home("kalam")
        self.fx.link(linked, "inbox/.seen-2026-11-21-a-note.md", self.fx.secret)
        busy = self.home("soma")
        self.hold_lock("soma")
        lines = tend.rollback_prepare(self.fx.citizens, ["kalam", "soma"], self.fx.state)
        self.assertEqual(lines, [
            "kalam: not set aside for rollback (its pace paths include links: "
            "inbox/.seen-2026-11-21-a-note.md) — inbox/2026-11-20-pace-letter.md; set it aside by hand",
            "soma: not set aside for rollback (its lock is held) — inbox/2026-11-20-pace-letter.md; "
            "set it aside by hand"])
        self.assertFalse(os.path.lexists(linked / "inbox" / ".seen-2026-11-20-pace-letter.md"))
        self.assertFalse(os.path.lexists(busy / "inbox" / ".seen-2026-11-20-pace-letter.md"))

    def test_a_home_with_a_put_back_pending_is_named_and_neither_committed_nor_pushed(self):
        # fix wave A (bb): a beat with tools committed a pace change and its guard
        # could not put it back; nothing of that home is pushed until the fleet's
        # tend has, so rollback-prepare commits and pushes nothing there either
        home = self.home("kalam")
        bare = self.fx.upstream / "citizen-kalam.git"
        upstream = self.fx.git(bare, "rev-parse", "HEAD")
        base = self.fx.head(home)
        self.fx.write(home, "EVENT-ONLY", "the beat's own\n")
        beat_head = self.fx.commit(home, "my own pace", author=h.beat_author("kalam"))
        attempts.mark_reported(self.fx.state, "kalam", f"{tend.PUT_BACK}:{base}:{beat_head}")
        self.assertEqual(tend.rollback_prepare(self.fx.citizens, ["kalam"], self.fx.state), [
            "kalam: not set aside for rollback (pace files a beat with tools committed itself wait for the "
            "fleet's tend to put them back) — inbox/2026-11-20-pace-letter.md; set it aside by hand"])
        self.assertEqual(self.fx.head(home), beat_head)
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), upstream)
        self.assertFalse(os.path.lexists(home / "inbox" / ".seen-2026-11-20-pace-letter.md"))


# -- the fleet-wide notices file -----------------------------------------------------

class TestFleetNotices(Case):
    def test_one_flag_per_line(self):
        path = tend.notices_path(self.fx.state)
        self.assertEqual(path, self.fx.memory / ".fleet-notices")
        self.assertFalse(tend.has_flag(path, "old-code-tended"))
        tend.set_flag(path, "agentic-ignored")
        tend.set_flag(path, "old-code-tended")
        tend.set_flag(path, "old-code-tended")
        self.assertEqual(path.read_text(encoding="utf-8"), "agentic-ignored\nold-code-tended\n")
        self.assertTrue(tend.has_flag(path, "old-code-tended"))
        self.assertFalse(tend.has_flag(path, "old-code"))
        # a shell can read it too
        r = subprocess.run(["/usr/bin/grep", "-qx", "old-code-tended", str(path)], check=False)
        self.assertEqual(r.returncode, 0)


class TestIsolation(unittest.TestCase):
    def test_modules_import_under_isolated_mode_and_ignore_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for planted in ("json.py", "shutil.py", "subprocess.py", "re.py", "stat.py"):
                Path(tmp, planted).write_text("raise SystemExit('planted module imported')\n")
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import record, tend; print('ok')")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS)], cwd=tmp,
                               capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr)

    def test_modules_start_with_future_annotations(self):
        for name in ("record.py", "tend.py"):
            first = (h.TOOLS / "citizen_pace_lib" / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, "from __future__ import annotations", name)


if __name__ == "__main__":
    unittest.main()
