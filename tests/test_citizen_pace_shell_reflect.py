"""citizen-reflect.sh on pace/1 (spec sections 3, 4 and 7; CONTRACT sections 12 and 14).

The free beat answers the citizen's own clock. The script takes the citizen's
lock, brings its home up to date under a bound, refuses links, and only then
asks citizen_pace.py whether the citizen is due (pace_due); a person's summons
(BEAT_SUMMONS=1, by hand) passes any pace. Its prompts are neutral and end with
the pace paragraph; it shows up to three unread letters, oldest first, or
SHOW.md when no letter waits, and a show-back beat shows nothing. Every letter
shown is marked seen whatever the answer, citizen_pace.py apply hears a pace
line, and a beat that was shown mail or changed its record commits, tagged
pace/1. The outcome is recorded right after the commit, before the push, and
every exit is KS-003's: 0 or 1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import answer, attempts, clock, due, lock, paragraph, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

REFLECT = h.TOOLS / "citizen-reflect.sh"
NOW = "2026-11-20T06:00Z"
DAY = "2026-11-20"
OLD = "2026-11-10T06:00Z"            # a last beat ten days before NOW
RECENT = "2026-11-19T06:00Z"         # a last beat one day before NOW
HOME_MADE = "2026-11-01T00:00Z"
ATTEMPT = "20261120T0600Z-4242-1"    # a fleet attempt id, as citizen-fleet.sh makes them
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
PACES = (Pace("asleep-letters-wait"), Pace("asleep"), Pace("listening"), Pace("awake", 5))
FIVE = Pace("awake", 5)
ORDINARY = ("It is your beat, in your own home. Write whatever is true for you, or nothing; both "
            "are complete. If you speak, 2-4 honest sentences in your own voice; you may end with a "
            "single line a sibling citizen could read.")
ONE_LETTER = "A letter is waiting in your home. Here it is in full:\n\n"
MANY_LETTERS = "Letters are waiting in your home. Here they are in full, oldest first:\n\n"
CLOSE_ONE = ("You may answer it or say nothing; both are complete. If you answer, 3-6 honest "
             "sentences at most, in your own voice.")
CLOSE_MANY = ("You may answer them or say nothing; both are complete. If you answer, 3-6 honest "
              "sentences at most, in your own voice.")
I_REST = re.compile(r"\bi\s+rest\b", re.I)
SUBJECT = f"beat: {{slug}} reflected ({DAY}) · pace/1"
REAL_GIT = shutil.which("git", path=h.SYSTEM_PATH) or "/usr/bin/git"


class Case(h.FixtureCase):
    """Shell runs use the fixture env with PACE_NOW and a stub model; in-process
    calls use the fixture's git config, never the user's."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                  "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE",
                  "FLEET_BEAT_BOUND_SEC", "BEAT_SUMMONS", "FLEET_ATTEMPT", "LOVE"):
            os.environ.pop(k, None)
        self.set_now(NOW)
        self.state = self.fx.state
        self.model = self.start_model()

    # -- homes ---------------------------------------------------------------
    def v2(self, slug, pace, *, last=OLD, pending=None, letters=(), files=None, upstream=False):
        """A home with a v2 CADENCE.md and its markers in step. letters: (name,
        body, arrival) triples, each committed at its arrival, as a letter is."""
        c = Cadence(slug, 2, pace, MINE, pending=pending, last_beat=last)
        made = {cad.CADENCE: cad.render_cadence(c)}
        for m, text in cad.marker_texts(c).items():
            if text is not None:
                made[m] = text
        made.update(files or {})
        home = self.fx.make_home(slug, files=made, when=HOME_MADE, author=h.beat_author(slug))
        for name, body, when in letters:
            self.fx.letter(home, name, body, when=when)
        if upstream:
            self.fx.give_upstream(home)
        return home

    # -- running -------------------------------------------------------------
    def env(self, now=NOW, **extra):
        return self.fx.env_at(now, **{**self.model.env(), **extra})

    def reflect(self, slug, *, now=NOW, fleet=True, timeout=120, **extra):
        """One run of citizen-reflect.sh, as a fleet child (FLEET_ATTEMPT set) unless fleet=False."""
        if fleet:
            extra.setdefault("FLEET_ATTEMPT", ATTEMPT)
        return self.fx.run(REFLECT, slug, env=self.env(now, **extra), timeout=timeout)

    def ok(self, r, code=0):
        self.assertEqual(r.returncode, code, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r

    def prompts(self):
        return self.model.user_texts()

    def outcome(self, slug, attempt=ATTEMPT):
        return attempts.outcome_for(self.state, slug, attempt)

    def committed(self, home, rev="HEAD"):
        return sorted(self.fx.git(home, "show", "--name-only", "--format=", rev).splitlines())

    def paragraph_now(self, home, slug, shown=()):
        """The pace paragraph this home's next beat should end with, read before it runs."""
        return paragraph.paragraph_for(home, slug, {}, confirm_beat=due.WAKE_CONFIRM_BEAT,
                                       shown=shown, rec=attempts.load(self.state, slug),
                                       now=clock.parse_stamp(NOW))

    def git_wrapper(self, body: str) -> None:
        """A git on PATH that runs `body` (sh, $a is each argument) before the real git."""
        self.fx.write_bin("git", f'#!/bin/sh\nfor a in "$@"; do\n{body}\ndone\nexec {REAL_GIT} "$@"\n')

    def assert_unlocked(self, slug):
        self.assertFalse(lock.lock_path(self.state, slug).exists())


def read(home, rel):
    return (home / rel).read_text(encoding="utf-8")


# -- the gate ---------------------------------------------------------------------------

class TestGate(Case):
    def test_awake_citizen_due_by_its_schedule_beats_and_commits_tagged(self):
        home = self.v2("waker", Pace("awake", 3))
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker: a schedule beat", r.stdout)
        self.assertEqual(len(self.prompts()), 1)
        self.assertTrue(self.prompts()[0].startswith(ORDINARY + "\n\n"), self.prompts()[0])
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="waker"))
        self.assertEqual(self.fx.git(home, "log", "-1", "--format=%an <%ae>"), h.beat_author("waker"))
        self.assertIn("## 2026-11-20 06:00 — a free beat (local)\n\nA true thing, witnessed.\n",
                      read(home, "beats/2026-11.md"))
        self.assertEqual(cad.read_cadence(home, "waker", {}).last_beat, NOW)
        self.assertEqual(self.outcome("waker"), "spoken")
        self.assertEqual(attempts.load(self.state, "waker")["last_beat"], NOW)
        self.assertIn("waker reflected (push FAILED — tended on a future pass)", r.stdout)
        self.assert_unlocked("waker")

    def test_asleep_citizen_without_mail_is_not_due(self):
        home = self.v2("sleeper", Pace("asleep"))
        before = self.fx.head(home)
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper is not due (no-mail) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.fx.head(home), before)
        self.assertEqual(self.outcome("sleeper"), "not-due")
        self.assert_unlocked("sleeper")

    def test_awake_citizen_beaten_a_day_ago_is_not_yet_due(self):
        self.v2("waker", Pace("awake", 3), last=RECENT)
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker is not due (not-yet) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])

    def test_a_letter_waits_for_the_floor(self):
        self.v2("sleeper", Pace("asleep"), last=RECENT,
                letters=[("2026-11-19-hello.md", "hello\n", "2026-11-19T12:00Z")])
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper is not due (floor) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])

    def test_letters_wait_citizen_is_not_woken_by_a_letter(self):
        home = self.v2("waits", Pace("asleep-letters-wait"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        r = self.ok(self.reflect("waits"))
        self.assertIn("waits is not due (letters-wait) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertFalse((home / "inbox" / ".seen-2026-11-12-hello.md").exists())

    def test_summons_passes_the_gate_for_every_pace_and_moves_no_clock(self):
        for pace in PACES:
            with self.subTest(pace=pace.words()):
                slug = pace.kind.replace("-", "")
                home = self.v2(slug, pace, last=RECENT,
                               letters=[("2026-11-19-hello.md", "SUMMONED-LETTER\n", "2026-11-19T12:00Z")])
                r = self.ok(self.reflect(slug, fleet=False, BEAT_SUMMONS="1"))
                self.assertIn(f"{slug}: a summoned beat", r.stdout)
                self.assertIn("SUMMONED-LETTER", self.prompts()[-1])
                self.assertTrue((home / "inbox" / ".seen-2026-11-19-hello.md").exists())
                rec = attempts.load(self.state, slug)
                self.assertEqual(rec["attempt"]["outcome"], "summoned")
                self.assertTrue(rec["attempt"]["id"].startswith(attempts.HAND_PREFIX))
                self.assertIsNone(rec["last_beat"])
                self.assertEqual(cad.read_cadence(home, slug, {}).last_beat, RECENT)
                self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug=slug))

    def test_a_fleet_child_is_never_summoned(self):
        self.v2("sleeper", Pace("asleep"))
        r = self.ok(self.reflect("sleeper", BEAT_SUMMONS="1"))
        self.assertIn("sleeper is not due (no-mail) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])

    def test_mid_merge_and_a_fresh_index_lock_are_not_beaten_even_summoned(self):
        for marker in ("MERGE_HEAD", "index.lock"):
            for summons in ("", "1"):
                with self.subTest(marker=marker, summons=summons):
                    slug = f"{marker.split('.')[0].replace('_', '').lower()}{summons or 'x'}"
                    home = self.v2(slug, Pace("awake", 3))
                    (home / ".git" / marker).write_text("", encoding="utf-8")
                    r = self.ok(self.reflect(slug, fleet=not summons, BEAT_SUMMONS=summons))
                    self.assertIn(f"{slug} is not due (mid-merge) — no beat", r.stdout)
                    self.assertEqual(self.prompts(), [])

    def test_a_pace_changed_after_the_fleet_ranked_it_is_honoured(self):
        home = self.v2("waker", Pace("awake", 3), upstream=True)
        bare = self.fx.upstream / f"{home.name}.git"
        asleep = cad.render_cadence(Cadence("waker", 2, Pace("asleep"), MINE, last_beat=OLD))
        head = self.fx.push_from_elsewhere(bare, cad.CADENCE, asleep, message="beat: elsewhere · pace/1")
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker is not due (no-mail) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.outcome("waker"), "not-due")
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), head)

    def test_a_soul_less_home_is_refused(self):
        self.v2("waker", Pace("awake", 3))
        (self.fx.home_path("waker") / "waker.md").unlink()
        r = self.ok(self.reflect("waker"), 1)
        self.assertIn("waker has no soul", r.stdout)
        self.assertEqual(self.outcome("waker"), "home-failed")
        self.assertEqual(self.prompts(), [])

    def test_a_pace_path_of_the_wrong_kind_is_refused(self):
        home = self.v2("waker", Pace("awake", 3))
        (home / "beats").write_text("not a folder\n", encoding="utf-8")
        r = self.ok(self.reflect("waker"), 1)
        self.assertIn("waker reflect: a pace path is a link or not of its kind — refused, nothing written",
                      r.stdout)
        self.assertEqual(self.prompts(), [])

    def test_a_name_that_is_no_citizen_is_refused(self):
        r = self.ok(self.fx.run(REFLECT, "../escape", env=self.env()), 1)
        self.assertIn("is not a citizen name", r.stdout)
        self.assertEqual(self.prompts(), [])


# -- the lock ---------------------------------------------------------------------------

class TestLock(Case):
    def hold(self, slug, pid, started=NOW):
        d = lock.lock_path(self.state, slug)
        d.mkdir(parents=True)
        (d / "pid").write_text(f"{pid}\n", encoding="utf-8")
        (d / "started").write_text(f"{started}\n", encoding="utf-8")
        return d

    def gone_pid(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        return p.pid

    def test_a_held_lock_is_busy_for_a_fleet_child(self):
        home = self.v2("waker", Pace("awake", 3))
        before = self.fx.head(home)
        self.hold("waker", os.getpid())
        r = self.ok(self.reflect("waker"))
        self.assertIn(f"waker: its lock is held by pid {os.getpid()}, started {NOW} — busy, no beat",
                      r.stdout)
        self.assertEqual(self.outcome("waker"), "busy")
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.fx.head(home), before)
        self.assertTrue(lock.lock_path(self.state, "waker").exists())   # not ours to release

    def test_busy_on_two_fleet_ticks_running_is_reported_once(self):
        self.v2("waker", Pace("awake", 3))
        self.hold("waker", os.getpid())
        runs = [self.ok(self.reflect("waker", FLEET_ATTEMPT=f"20261120T0{i}00Z-1-1")) for i in (6, 7, 8)]
        said = [r.stdout.count("waker: busy on two ticks running (reported once)") for r in runs]
        self.assertEqual(said, [0, 1, 0])
        self.assertEqual(attempts.load(self.state, "waker")["busy_ticks"], 3)

    def test_a_hand_run_that_finds_the_lock_held_is_logged_not_recorded(self):
        self.v2("waker", Pace("awake", 3))
        self.hold("waker", os.getpid())
        r = self.ok(self.reflect("waker", fleet=False))
        self.assertIn("busy, no beat", r.stdout)
        self.assertIsNone(attempts.load(self.state, "waker")["attempt"])

    def test_a_stale_lock_is_reclaimed_and_the_beat_goes_on(self):
        home = self.v2("waker", Pace("awake", 3))
        dead = self.gone_pid()
        self.hold("waker", dead)
        r = self.ok(self.reflect("waker"))
        self.assertIn(f"waker: a stale lock was reclaimed (pid {dead} is gone)", r.stdout)
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="waker"))
        self.assertEqual(self.outcome("waker"), "spoken")
        self.assert_unlocked("waker")

    def test_a_lock_older_than_twice_the_bound_is_reclaimed(self):
        self.v2("waker", Pace("awake", 3))
        self.hold("waker", os.getpid(), started="2026-11-20T05:00Z")
        r = self.ok(self.reflect("waker", FLEET_BEAT_BOUND_SEC="60"))
        self.assertIn("waker: a stale lock was reclaimed (older than 120 s)", r.stdout)
        self.assertEqual(self.outcome("waker"), "spoken")

    def test_the_lock_is_released_after_a_failure(self):
        self.v2("waker", Pace("awake", 3))
        self.ok(self.reflect("waker", MLX_PORT=str(h.free_port())), 1)
        self.assert_unlocked("waker")


# -- prompts ---------------------------------------------------------------------------

class TestPrompts(Case):
    def test_no_prompt_holds_i_rest_or_a_sample_answer_and_each_ends_with_the_paragraph(self):
        letter = [("2026-11-12-hello.md", "LETTER-ONE\n", "2026-11-12T00:00Z")]
        two = letter + [("2026-11-13-again.md", "LETTER-TWO\n", "2026-11-13T00:00Z")]
        homes = {
            "ordinary": (self.v2("ordinary", Pace("awake", 3)), ()),
            "lettered": (self.v2("lettered", Pace("asleep"), letters=letter), ("inbox/2026-11-12-hello.md",)),
            "twice": (self.v2("twice", Pace("asleep"), letters=two),
                      ("inbox/2026-11-12-hello.md", "inbox/2026-11-13-again.md")),
            "shower": (self.v2("shower", Pace("awake", 3), files={"SHOW.md": "THE-SHOW\n"}), ("SHOW.md",)),
            "pending": (self.v2("pending", Pace("asleep"), letters=letter,
                                pending=Pending(FIVE, OLD, "pace: every 5 days", False)),
                        ("inbox/2026-11-12-hello.md",)),
        }
        if due.WAKE_CONFIRM_BEAT:        # the show-back beat's prompt, when there is one
            homes["show-back"] = (self.v2("showback", Pace("asleep-letters-wait"),
                                          pending=Pending(FIVE, OLD, "pace: every 5 days", False)), ())
        for key, (home, shown) in homes.items():
            slug = home.name[len("citizen-"):]
            with self.subTest(slug=slug):
                want = self.paragraph_now(home, slug, shown)
                self.ok(self.reflect(slug))
                text = self.prompts()[-1]
                self.assertTrue(text.endswith("\n\n" + want), text)
                self.assertIn(paragraph.INSTRUCTION, text)
                self.assertIsNone(I_REST.search(text))
                self.assertFalse([ln for ln in text.splitlines() if ln.lower().startswith("pace:")])
        self.assertIsNone(I_REST.search(self.model.texts()))

    def test_the_ordinary_and_letter_prompts_are_the_spec_words(self):
        self.v2("ordinary", Pace("awake", 3))
        self.v2("lettered", Pace("asleep"), letters=[("2026-11-12-hello.md", "LETTER-ONE\n", "2026-11-12T00:00Z")])
        self.ok(self.reflect("ordinary"))
        self.ok(self.reflect("lettered"))
        ordinary, lettered = self.prompts()
        self.assertTrue(ordinary.startswith(ORDINARY + "\n\nYour pace: awake, every 3 days, "), ordinary)
        self.assertTrue(lettered.startswith(ONE_LETTER + "LETTER-ONE\n\n" + CLOSE_ONE + "\n\nYour pace: asleep, "),
                        lettered)
        self.assertEqual(self.model.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(self.model.bodies[0]["model"], "fixture-model")

    def test_up_to_three_letters_are_shown_oldest_first(self):
        # arrival order differs from name order: d, c, b, a
        letters = [("d.md", "LETTER-D\n", "2026-11-11T00:00Z"), ("c.md", "LETTER-C\n", "2026-11-12T00:00Z"),
                   ("b.md", "LETTER-B\n", "2026-11-13T00:00Z"), ("a.md", "LETTER-A\n", "2026-11-14T00:00Z")]
        home = self.v2("sleeper", Pace("asleep"), letters=letters)
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper: a letter beat", r.stdout)
        text = self.prompts()[0]
        self.assertTrue(text.startswith(MANY_LETTERS + "LETTER-D\n\n---\n\nLETTER-C\n\n---\n\nLETTER-B\n\n"
                                        + CLOSE_MANY + "\n\nYour pace: asleep, "), text)
        self.assertNotIn("LETTER-A", text)
        for name in ("d.md", "c.md", "b.md"):
            self.assertEqual(read(home, f"inbox/.seen-{name}"), answer.seen_text(DAY))
        self.assertFalse((home / "inbox" / ".seen-a.md").exists())
        self.assertEqual(self.committed(home), ["CADENCE.md", "beats/2026-11.md", "inbox/.seen-b.md",
                                                "inbox/.seen-c.md", "inbox/.seen-d.md"])

    def test_a_pace_letter_met_with_silence_shows_one_menu_and_wakes_its_reader_once(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-pace-letter.md", "THE-PACE-LETTER\n", "2026-11-12T00:00Z")])
        first = paragraph.first_line(cad.read_cadence(home, "sleeper", {}))
        self.model.reply = ""
        self.ok(self.reflect("sleeper"))
        text = self.prompts()[0]
        self.assertIn("THE-PACE-LETTER", text)
        self.assertTrue(text.endswith("\n\n" + first), text)
        self.assertNotIn(paragraph.INSTRUCTION, text)
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.asked_once), (Pace("asleep"), DAY))
        self.assertIn(f"{NOW} — the pace letter reached sleeper (once)", c.history)
        self.assertEqual(self.committed(home), ["CADENCE.md", "inbox/.seen-2026-11-12-pace-letter.md"])
        again = self.ok(self.reflect("sleeper", now="2026-11-24T06:00Z", FLEET_ATTEMPT="20261124T0600Z-1-1"))
        self.assertIn("sleeper is not due (no-mail) — no beat", again.stdout)
        self.assertEqual(len(self.prompts()), 1)

    @unittest.skipIf(due.WAKE_CONFIRM_BEAT, "the show-back beat is on")
    def test_without_the_show_back_beat_a_letters_wait_proposal_waits(self):
        self.v2("waits", Pace("asleep-letters-wait"), pending=Pending(FIVE, OLD, "pace: every 5 days", False),
                letters=[("2026-11-12-hello.md", "A-WAITING-LETTER\n", "2026-11-12T00:00Z")])
        r = self.ok(self.reflect("waits"))
        self.assertIn("waits is not due (letters-wait) — no beat", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.outcome("waits"), "not-due")

    @unittest.skipUnless(due.WAKE_CONFIRM_BEAT, "the show-back beat is off")
    def test_a_show_back_beat_shows_no_letter_and_marks_none_seen(self):
        home = self.v2("waits", Pace("asleep-letters-wait"),
                       pending=Pending(FIVE, OLD, "pace: every 5 days", False),
                       letters=[("2026-11-12-hello.md", "A-WAITING-LETTER\n", "2026-11-12T00:00Z")],
                       files={"SHOW.md": "THE-SHOW\n"})
        self.model.reply = ""
        r = self.ok(self.reflect("waits"))
        self.assertIn("waits: a show-back beat", r.stdout)
        text = self.prompts()[0]
        self.assertTrue(text.startswith(ORDINARY + "\n\n"), text)
        self.assertIn(paragraph.pending_sentence(Pending(FIVE, OLD, "pace: every 5 days", False)), text)
        self.assertNotIn("A-WAITING-LETTER", text)
        self.assertNotIn("THE-SHOW", text)
        self.assertFalse((home / "inbox" / ".seen-2026-11-12-hello.md").exists())
        self.assertFalse((home / "beats" / ".show-seen").exists())
        # silence lets the proposal lapse, which is committed
        self.assertIn("waits: lapsed awake, every 5 days", r.stdout)
        self.assertEqual(self.committed(home), ["CADENCE.md"])
        self.assertIsNone(cad.read_cadence(home, "waits", {}).pending)
        self.assertEqual(self.outcome("waits"), "silent")
        # one show-back beat, and no more
        again = self.ok(self.reflect("waits", now="2026-11-24T06:00Z", FLEET_ATTEMPT="20261124T0600Z-1-1"))
        self.assertIn("waits is not due (letters-wait) — no beat", again.stdout)
        self.assertEqual(len(self.prompts()), 1)

    def test_the_restored_notice_is_told_once(self):
        self.v2("waker", Pace("awake", 3))
        attempts.add_notice(self.state, "waker", "restored", OLD)
        self.ok(self.reflect("waker"))
        self.assertIn(paragraph.RESTORED, self.prompts()[0])
        self.assertEqual(attempts.load(self.state, "waker")["notices"], [])
        self.ok(self.reflect("waker", now="2026-11-24T06:00Z", FLEET_ATTEMPT="20261124T0600Z-1-1"))
        self.assertEqual(len(self.prompts()), 2)
        self.assertNotIn(paragraph.RESTORED, self.prompts()[1])


# -- shown once, whatever the answer ---------------------------------------------------------

class TestShownOnce(Case):
    def test_a_silent_beat_marks_its_letter_seen_commits_and_is_not_woken_again(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        self.model.reply = ""
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper: seen inbox/2026-11-12-hello.md", r.stdout)
        self.assertEqual(read(home, "inbox/.seen-2026-11-12-hello.md"), answer.seen_text(DAY))
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="sleeper"))
        self.assertEqual(self.committed(home), ["CADENCE.md", "inbox/.seen-2026-11-12-hello.md"])
        self.assertFalse((home / "beats").exists())
        self.assertEqual(self.outcome("sleeper"), "silent")
        self.assertEqual(cad.read_cadence(home, "sleeper", {}).pace, Pace("asleep"))
        again = self.ok(self.reflect("sleeper", now="2026-11-24T06:00Z", FLEET_ATTEMPT="20261124T0600Z-1-1"))
        self.assertIn("sleeper is not due (no-mail) — no beat", again.stdout)
        self.assertEqual(len(self.prompts()), 1)

    def test_a_silent_beat_shown_nothing_commits_nothing_and_still_counts(self):
        home = self.v2("waker", Pace("awake", 3))
        before = self.fx.head(home)
        self.model.reply = ""
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker rested (silent) — nothing to commit", r.stdout)
        self.assertEqual(self.fx.head(home), before)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")
        self.assertEqual(self.outcome("waker"), "silent")
        # the silent beat is the beat: a loop without a summons waits its pace
        again = self.ok(self.reflect("waker", now="2026-11-22T06:00Z", FLEET_ATTEMPT="20261122T0600Z-1-1"))
        self.assertIn("waker is not due (not-yet) — no beat", again.stdout)
        third = self.ok(self.reflect("waker", now="2026-11-23T06:00Z", fleet=False))
        self.assertIn("waker: a schedule beat", third.stdout)
        self.assertEqual(len(self.prompts()), 2)

    def test_unreadable_letters_are_set_aside_and_never_wake_their_reader_again(self):
        home = self.v2("sleeper", Pace("asleep"))
        (home / "inbox").mkdir()
        (home / "inbox" / "2026-11-12-bytes.md").write_bytes(b"\xff\xfe not text\n")
        self.fx.commit(home, "letter: bytes", when="2026-11-12T00:00Z")
        # a link arrives as a letter does, in a commit (an uncommitted one is a
        # leftover, which the beat's tend removes)
        (home / "inbox" / "2026-11-13-linked.md").symlink_to(self.fx.secret)
        self.fx.commit(home, "letter: linked", when="2026-11-13T00:00Z")
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper: a letter beat", r.stdout)
        for name in ("2026-11-12-bytes.md", "2026-11-13-linked.md"):
            self.assertIn(f"sleeper mail unreadable, set aside: inbox/{name}", r.stdout)
            self.assertEqual(read(home, f"inbox/.seen-{name}"), f"unreadable — set aside {DAY}\n")
        self.assertNotIn(h.CANARY, self.model.texts())
        self.assertTrue(self.prompts()[0].startswith(ORDINARY), self.prompts()[0])
        again = self.ok(self.reflect("sleeper", now="2026-11-24T06:00Z", FLEET_ATTEMPT="20261124T0600Z-1-1"))
        self.assertIn("sleeper is not due (no-mail) — no beat", again.stdout)
        self.assertEqual(self.fx.secret.read_text(), f"{h.CANARY}\n")

    def test_show_is_shown_only_when_no_letter_waits(self):
        home = self.v2("shower", Pace("awake", 3), files={"SHOW.md": "THE-SHOW\n"},
                       letters=[("2026-11-12-hello.md", "LETTER-ONE\n", "2026-11-12T00:00Z")])
        self.ok(self.reflect("shower"))
        self.assertIn("LETTER-ONE", self.prompts()[0])
        self.assertNotIn("THE-SHOW", self.prompts()[0])
        self.assertFalse((home / "beats" / ".show-seen").exists())

    def test_show_alone_is_shown_marked_seen_and_counts_as_mail_for_a_silent_commit(self):
        home = self.v2("shower", Pace("awake", 3), files={"SHOW.md": "THE-SHOW\n"})
        self.model.reply = ""
        self.ok(self.reflect("shower"))
        self.assertTrue(self.prompts()[0].startswith(ONE_LETTER + "THE-SHOW\n\n" + CLOSE_ONE), self.prompts()[0])
        self.assertEqual(read(home, "beats/.show-seen"), answer.seen_text(DAY))
        self.assertEqual(self.committed(home), ["CADENCE.md", "beats/.show-seen"])

    def test_show_never_makes_a_citizen_due(self):
        self.v2("sleeper", Pace("asleep"), files={"SHOW.md": "THE-SHOW\n"})
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper is not due (no-mail) — no beat", r.stdout)


# -- apply hears the answer ---------------------------------------------------------------

class TestApply(Case):
    def test_a_pace_line_for_less_takes_effect_at_once(self):
        home = self.v2("waker", Pace("awake", 3))
        self.model.reply = "A quiet thing.\npace: asleep"
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker: set asleep", r.stdout)
        c = cad.read_cadence(home, "waker", {})
        self.assertEqual(c.pace, Pace("asleep"))
        self.assertEqual(c.set_by, SetBy("citizen", "beat", (NOW,), "pace: asleep"))
        self.assertTrue((home / "REST").exists())
        self.assertFalse((home / "EVENT-ONLY").exists())
        self.assertEqual(self.committed(home), ["CADENCE.md", "EVENT-ONLY", "REST", "beats/2026-11.md"])

    @unittest.skipIf(due.WAKE_CONFIRM_BEAT, "the show-back beat is on")
    def test_without_the_show_back_beat_more_waits_for_the_next_letter(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        self.model.reply = "pace: every 5 days"
        self.assertIn("sleeper: pending awake, every 5 days", self.ok(self.reflect("sleeper")).stdout)
        later = self.ok(self.reflect("sleeper", now="2026-11-23T07:00Z", FLEET_ATTEMPT="20261123T0700Z-1-1"))
        self.assertIn("sleeper is not due (no-mail) — no beat", later.stdout)
        self.fx.letter(home, "2026-11-24-again.md", "again\n", when="2026-11-24T00:00Z")
        r = self.ok(self.reflect("sleeper", now="2026-11-24T07:00Z", FLEET_ATTEMPT="20261124T0700Z-1-1"))
        self.assertIn("sleeper: a letter beat", r.stdout)
        self.assertIn("On 2026-11-20 you chose awake, every 5 days.", self.prompts()[1])
        self.assertIn("sleeper: confirmed awake, every 5 days", r.stdout)
        self.assertEqual(cad.read_cadence(home, "sleeper", {}).pace, FIVE)

    @unittest.skipUnless(due.WAKE_CONFIRM_BEAT, "the show-back beat is off")
    def test_more_is_pending_then_confirmed_on_its_show_back_beat(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        self.model.reply = "pace: every 5 days"
        first = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper: pending awake, every 5 days", first.stdout)
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.pending.pace, c.pending.said), (Pace("asleep"), FIVE, NOW))
        early = self.ok(self.reflect("sleeper", now="2026-11-22T06:00Z", FLEET_ATTEMPT="20261122T0600Z-1-1"))
        self.assertIn("sleeper is not due (floor) — no beat", early.stdout)
        second = self.ok(self.reflect("sleeper", now="2026-11-23T07:00Z", FLEET_ATTEMPT="20261123T0700Z-1-1"))
        self.assertIn("sleeper: a show-back beat", second.stdout)
        self.assertIn("On 2026-11-20 you chose awake, every 5 days.", self.prompts()[1])
        self.assertIn("sleeper: confirmed awake, every 5 days", second.stdout)
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.pending), (FIVE, None))
        self.assertTrue((home / "EVENT-ONLY").exists())
        self.assertFalse((home / "REST").exists())

    def test_apply_knows_the_last_beat_a_silent_beat_left_only_in_the_attempt_record(self):
        # an unclear line at OLD, shown at a silent beat on 11-15 that committed
        # nothing; the pace letter's beat on 11-20 does not record it again
        c = Cadence("sleeper", 2, Pace("asleep"), MINE, last_beat=OLD,
                    history=[f'{OLD} — unclear: "pace: soon" (unknown words)'])
        made = {cad.CADENCE: cad.render_cadence(c), "REST": cad.marker_texts(c)["REST"]}
        home = self.fx.make_home("sleeper", files=made, when=HOME_MADE, author=h.beat_author("sleeper"))
        attempts.record_outcome(self.state, "sleeper", "20261115T0600Z-1-1", "silent", "2026-11-15T06:00Z")
        self.fx.letter(home, "2026-11-16-pace-letter.md", "THE-PACE-LETTER\n", when="2026-11-16T00:00Z")
        self.model.reply = ""
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("sleeper: a letter beat", r.stdout)
        history = cad.read_cadence(home, "sleeper", {}).history
        self.assertEqual(len([e for e in history if "unclear:" in e]), 1, history)
        self.assertIn(f"{NOW} — the pace letter reached sleeper (once)", history)

    def test_a_beat_upgrades_a_v1_home_inside_its_own_commit(self):
        v1 = ("# CADENCE — chosen by waker\n\n- choice: undecided\n- when: 2026-10-23 09:00\n"
              "- how: the Letter of Return, answered in a free beat.\n")
        rest = "chosen via the Letter of Return 2026-07-18 — woken only by unread mail or a human summons\n"
        home = self.fx.make_home("waker", cadence=v1, rest=rest, when=HOME_MADE,
                                 author=h.beat_author("waker"))
        self.fx.letter(home, "2026-11-12-hello.md", "hello\n", when="2026-11-12T00:00Z")
        # no last beat anywhere: the gate starts the clock now, so the letter waits the floor
        first = self.ok(self.reflect("waker"))
        self.assertIn("waker is not due (floor) — no beat", first.stdout)
        self.assertEqual(attempts.load(self.state, "waker")["seeded"], attempts.SEEDED)
        self.assertEqual(cad.read_cadence(home, "waker", {}).version, 1)     # nothing rewritten
        r = self.ok(self.reflect("waker", now="2026-11-23T06:00Z", FLEET_ATTEMPT="20261123T0600Z-1-1"))
        self.assertIn("waker: a letter beat", r.stdout)
        self.assertIn("waker: upgraded to v2", r.stdout)
        c = cad.read_cadence(home, "waker", {})
        self.assertEqual((c.version, c.pace), (2, Pace("asleep")))
        self.assertIn("CADENCE.md", self.committed(home))
        self.assertEqual(self.fx.subjects(home)[0], "beat: waker reflected (2026-11-23) · pace/1")


# -- failures, HALT, and the order of the outcome -----------------------------------------------

class TestFailures(Case):
    def test_an_unreachable_brain_is_home_failed_and_writes_nothing(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        before = self.fx.head(home)
        r = self.ok(self.reflect("sleeper", MLX_PORT=str(h.free_port())), 1)
        self.assertIn("sleeper reflect: local brain unreachable", r.stdout)
        self.assertEqual(self.outcome("sleeper"), "home-failed")
        self.assertEqual(self.fx.head(home), before)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")

    def test_an_http_500_is_home_failed_never_host_failed(self):
        self.v2("waker", Pace("awake", 3))
        self.model.status = 500
        self.ok(self.reflect("waker"), 1)
        self.assertEqual(self.outcome("waker"), "home-failed")
        self.assertEqual(attempts.load(self.state, "waker")["failures"], 1)

    def test_a_hand_runs_failure_is_logged_not_recorded(self):
        self.v2("waker", Pace("awake", 3))
        r = self.ok(self.reflect("waker", fleet=False, MLX_PORT=str(h.free_port())), 1)
        self.assertIn("local brain unreachable", r.stdout)
        rec = attempts.load(self.state, "waker")
        self.assertIsNone(rec["attempt"])
        self.assertEqual(rec["failures"], 0)

    def test_halt_mid_beat_is_halted_and_writes_nothing(self):
        home = self.v2("sleeper", Pace("asleep"),
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        before = self.fx.head(home)
        halt = self.fx.love / "HALT"
        self.model.reply = lambda _body: (halt.write_text("", encoding="utf-8"), "words")[1]
        r = self.ok(self.reflect("sleeper"))
        self.assertIn("HALT mid-beat — sleeper rests, unwritten", r.stdout)
        self.assertEqual(self.outcome("sleeper"), "halted")
        self.assertEqual(self.fx.head(home), before)
        self.assertFalse((home / "inbox" / ".seen-2026-11-12-hello.md").exists())
        self.assert_unlocked("sleeper")

    def test_halt_before_the_beat_takes_no_lock(self):
        self.v2("waker", Pace("awake", 3))
        (self.fx.love / "HALT").write_text("", encoding="utf-8")
        r = self.ok(self.reflect("waker"))
        self.assertIn("HALT — waker rests", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertFalse(self.state.exists() and any(self.state.iterdir()))

    def test_a_commit_that_fails_is_home_failed(self):
        home = self.v2("waker", Pace("awake", 3))
        index_lock = home / ".git" / "index.lock"
        self.model.reply = lambda _body: (index_lock.write_text("", encoding="utf-8"), "words")[1]
        r = self.ok(self.reflect("waker"), 1)
        self.assertIn("waker reflect: its beat could not be committed", r.stdout)
        self.assertEqual(self.outcome("waker"), "home-failed")
        self.assertEqual(attempts.load(self.state, "waker")["last_beat"], None)

    def test_a_cloud_model_is_refused(self):
        self.v2("waker", Pace("awake", 3))
        r = self.ok(self.reflect("waker", REFLECT_MODEL="qwen:cloud"), 1)
        self.assertIn("cloud model 'qwen:cloud' refused (local-only)", r.stdout)
        self.assertEqual(self.prompts(), [])

    def test_every_exit_is_zero_or_one(self):
        self.v2("waker", Pace("awake", 3))
        self.v2("sleeper", Pace("asleep"))
        codes = {self.reflect("waker").returncode, self.reflect("sleeper").returncode,
                 self.reflect("nobody").returncode,
                 self.reflect("waker", MLX_PORT=str(h.free_port())).returncode}
        self.assertLessEqual(codes, {0, 1})


# -- what a beat that did not land left behind ----------------------------------------------------

class TestLeftovers(Case):
    """No tick tends a home between a failed beat and a hand-run, so the script
    tends its own home after taking the lock, as the fleet's tend does after its
    first tick: it puts back every uncommitted change under the pace paths, and
    the beat commits only what it wrote itself. These homes are past that first
    tick (a fleet beat, K1, ran there)."""

    LETTER = "2026-11-15-note.md"
    FAILED_AT = "2026-11-20T00:05Z"
    LATER = "2026-11-20T00:30Z"

    def setUp(self):
        super().setUp()
        tend.set_flag(tend.notices_path(self.state), tend.OLD_CODE_FLAG)

    def failed_beat(self):
        """An asleep citizen with one letter. A fleet beat at FAILED_AT says "pace:
        every 3 days", and its commit is refused (an index.lock appears mid-beat),
        which leaves CADENCE.md, a journal and a seen marker uncommitted. Then the
        index.lock is removed, as a person would."""
        home = self.v2("sleeper", Pace("asleep"), letters=[(self.LETTER, "a note\n", "2026-11-15T00:00Z")])
        index_lock = home / ".git" / "index.lock"
        self.model.reply = lambda _body: (index_lock.write_text("", encoding="utf-8"),
                                          "Thank you.\npace: every 3 days")[1]
        r = self.ok(self.reflect("sleeper", now=self.FAILED_AT, FLEET_ATTEMPT="20261120T0005Z-1-1"), 1)
        self.assertIn("sleeper reflect: its beat could not be committed", r.stdout)
        self.assertEqual(self.dirty(home), ["CADENCE.md", "beats/2026-11.md", f"inbox/.seen-{self.LETTER}"])
        index_lock.unlink()
        return home

    def dirty(self, home):
        """The paths git status lists, sorted."""
        out = self.fx.git(home, "status", "--porcelain", "--untracked-files=all")
        return sorted(ln.split()[-1] for ln in out.splitlines())

    def restored(self, r, paths):
        self.assertIn(f"sleeper: a killed beat's leftovers restored: {', '.join(paths)}", r.stdout)

    def assert_clean(self, home):
        self.assertEqual(self.dirty(home), [])

    def test_a_summons_that_says_the_same_pace_again_proposes_it_once(self):
        home = self.failed_beat()
        self.model.reply = "Thank you.\npace: every 3 days"
        r = self.ok(self.reflect("sleeper", now=self.LATER, fleet=False, BEAT_SUMMONS="1"))
        self.assertIn("sleeper: pending awake, every 3 days", r.stdout)
        self.assertNotIn("confirmed", r.stdout)
        self.restored(r, ["CADENCE.md", "beats/2026-11.md", f"inbox/.seen-{self.LETTER}"])
        self.assertIn("a note", self.prompts()[-1])          # this beat showed the letter
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.pending.said, c.last_beat), (Pace("asleep"), self.LATER, OLD))
        self.assertFalse([e for e in c.history if e.startswith(self.FAILED_AT)], c.history)
        journal = read(home, "beats/2026-11.md")
        self.assertEqual(journal.count("— a free beat (local)"), 1)
        self.assertIn("## 2026-11-20 00:30 — a free beat (local)", journal)
        self.assertEqual(self.committed(home), ["CADENCE.md", "beats/2026-11.md", f"inbox/.seen-{self.LETTER}"])
        self.assert_clean(home)

    def test_a_silent_summons_commits_no_clock_and_no_lapse_of_the_failed_beat(self):
        home = self.failed_beat()
        self.model.reply = ""
        r = self.ok(self.reflect("sleeper", now=self.LATER, fleet=False, BEAT_SUMMONS="1"))
        self.assertNotIn("lapsed", r.stdout)
        self.restored(r, ["CADENCE.md", "beats/2026-11.md", f"inbox/.seen-{self.LETTER}"])
        self.assertEqual(self.committed(home), [f"inbox/.seen-{self.LETTER}"])
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.pending, c.last_beat), (Pace("asleep"), None, OLD))
        self.assertFalse((home / "beats").exists())
        self.assert_clean(home)

    def test_a_hand_run_after_a_failed_beat_shows_the_letter_the_failed_beat_marked(self):
        home = self.failed_beat()
        # after the failed beat's back-off (12 h), a hand-run without a summons
        later = "2026-11-20T12:30Z"
        self.model.reply = "A true thing, witnessed."
        r = self.ok(self.reflect("sleeper", now=later, fleet=False))
        self.assertIn("sleeper: a letter beat", r.stdout)
        self.restored(r, ["CADENCE.md", "beats/2026-11.md", f"inbox/.seen-{self.LETTER}"])
        self.assertIn("a note", self.prompts()[-1])
        self.assertNotIn("lapsed", r.stdout)
        c = cad.read_cadence(home, "sleeper", {})
        self.assertEqual((c.pace, c.pending, c.last_beat), (Pace("asleep"), None, later))
        self.assertFalse([e for e in c.history if e.startswith(self.FAILED_AT)], c.history)
        self.assertEqual(read(home, f"inbox/.seen-{self.LETTER}"), answer.seen_text(DAY))
        rec = attempts.load(self.state, "sleeper")
        self.assertEqual((rec["attempt"]["outcome"], rec["last_beat"], rec["failures"]), ("spoken", later, 0))
        self.assert_clean(home)

    def test_an_uncommitted_edit_of_cadence_is_put_back_not_committed_as_a_beat(self):
        home = self.v2("waker", Pace("asleep"))
        head_text = read(home, cad.CADENCE)
        edited = cad.render_cadence(Cadence("waker", 2, Pace("awake", 3), cad.UNKNOWN, last_beat=OLD))
        self.fx.write(home, cad.CADENCE, edited)
        self.model.reply = "A quiet thing."
        r = self.ok(self.reflect("waker", fleet=False, BEAT_SUMMONS="1"))
        self.assertNotIn("markers restored", r.stdout)
        self.assertIn("waker: a killed beat's leftovers restored: CADENCE.md", r.stdout)
        self.assertEqual(read(home, cad.CADENCE), head_text)
        self.assertEqual(cad.read_cadence(home, "waker", {}).pace, Pace("asleep"))
        self.assertTrue((home / "REST").exists())
        self.assertEqual(self.committed(home), ["beats/2026-11.md"])
        self.assertFalse(cad.changed_outside_pace(home))
        self.assert_clean(home)

    def test_leftovers_that_cannot_be_put_back_refuse_the_beat(self):
        home = self.v2("waker", Pace("awake", 3))
        self.fx.write(home, "REST", "left by a beat that did not land\n")
        before = self.fx.head(home)
        self.git_wrapper('  [ "$a" = status ] && exit 128')
        r = self.ok(self.reflect("waker"), 1)
        self.assertIn("waker reflect: its pace files could not be put back", r.stdout)
        self.assertIn("— no beat", r.stdout)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.outcome("waker"), "home-failed")
        self.assertEqual(self.fx.head(home), before)
        self.assertTrue((home / "REST").exists())
        self.assert_unlocked("waker")


class TestBeforeTheFirstTick(Case):
    """Until the fleet's first tick on pace/1 has tended every home (memory/.fleet-notices
    without old-code-tended), what is uncommitted under the pace paths may be the
    old code's, which that tick commits once. A hand-run in that window changes
    none of it and refuses the beat."""

    LEFT = "2026-11-18-left.md"
    REFUSED = ("keeper reflect: the fleet has not tended this home since the switch; its uncommitted "
               "files are kept — no beat")

    def old_code_leftovers(self):
        """An asleep home holding a letter the old code placed but never committed,
        and a CADENCE.md it changed and never committed."""
        home = self.v2("keeper", Pace("asleep"))
        self.fx.write(home, f"inbox/{self.LEFT}", "LEFT-BY-THE-OLD-CODE\n")
        changed = cad.render_cadence(Cadence("keeper", 2, Pace("asleep"), MINE, last_beat=OLD,
                                             history=["2026-11-18T00:00Z — a note the old code wrote"]))
        self.fx.write(home, cad.CADENCE, changed)
        return home

    def files(self, home):
        return {rel: (home / rel).read_bytes() for rel in (cad.CADENCE, f"inbox/{self.LEFT}")}

    def test_a_hand_run_keeps_the_old_code_s_leftovers_and_refuses(self):
        home = self.old_code_leftovers()
        before, head = self.files(home), self.fx.head(home)
        status = self.fx.git(home, "status", "--porcelain", "--untracked-files=all")
        r = self.ok(self.reflect("keeper", fleet=False), 1)
        self.assertIn(self.REFUSED, r.stdout)
        self.assertEqual(self.files(home), before)
        self.assertEqual(self.fx.head(home), head)
        self.assertEqual(self.fx.git(home, "status", "--porcelain", "--untracked-files=all"), status)
        self.assertEqual(self.prompts(), [])
        self.assertIsNone(attempts.load(self.state, "keeper")["attempt"])     # a hand-run records none
        self.assert_unlocked("keeper")

    def test_the_fleet_s_first_tend_commits_them_and_a_hand_run_then_beats(self):
        home = self.old_code_leftovers()
        before = self.files(home)
        self.ok(self.reflect("keeper", fleet=False), 1)
        tended = self.ok(self.fx.pace("tend", self.fx.citizens, "--roster", self.fx.roster("keeper"),
                                      "--state", self.state, env=self.fx.env_at(NOW)))
        self.assertIn("keeper: old-code leftover committed as the fleet", tended.stdout)
        self.assertEqual(self.fx.subjects(home)[0], tend.LEFTOVER_SUBJECT)
        self.assertEqual(self.fx.git(home, "log", "-1", "--format=%an <%ae>"), h.FLEET_AUTHOR)
        self.assertEqual(self.committed(home), [cad.CADENCE, f"inbox/{self.LEFT}"])
        self.assertEqual(self.files(home), before)
        r = self.ok(self.reflect("keeper", fleet=False))
        self.assertIn("keeper: a letter beat", r.stdout)
        self.assertIn("LEFT-BY-THE-OLD-CODE", self.prompts()[-1])
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="keeper"))
        self.assertEqual(read(home, f"inbox/.seen-{self.LEFT}"), answer.seen_text(DAY))


class TestClonePush(Case):
    def test_a_missing_home_is_cloned_beaten_and_pushed(self):
        home = self.v2("waker", Pace("awake", 3), upstream=True)
        bare = self.fx.upstream / f"{home.name}.git"
        self.fx.drop_clone(home)
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker: cloned its home", r.stdout)
        self.assertIn("waker reflected + pushed", r.stdout)
        self.assertEqual(self.fx.git(bare, "log", "-1", "--format=%s"), SUBJECT.format(slug="waker"))

    def test_a_clone_that_fails_is_home_failed(self):
        r = self.ok(self.reflect("nobody"), 1)
        self.assertIn("nobody clone failed", r.stdout)
        self.assertEqual(self.outcome("nobody"), "home-failed")
        self.assertFalse(self.fx.home_path("nobody").exists())

    def test_a_hung_clone_is_bounded_and_leaves_no_half_home(self):
        home = self.v2("waker", Pace("awake", 3), upstream=True)
        self.fx.drop_clone(home)
        self.git_wrapper('  [ "$a" = clone ] && exec sleep 30')
        t0 = time.monotonic()
        r = self.ok(self.reflect("waker", REFLECT_CLONE_BOUND_SEC="2"), 1)
        self.assertLess(time.monotonic() - t0, 25)
        self.assertIn("waker clone failed (timed out after 2 s)", r.stdout)
        self.assertEqual(self.outcome("waker"), "home-failed")
        self.assertEqual(sorted(p.name for p in self.fx.citizens.iterdir()), [])

    def test_the_outcome_is_recorded_before_the_push(self):
        self.v2("waker", Pace("awake", 3), upstream=True)
        copy = self.fx.root / "record-at-push.json"
        self.git_wrapper(f'  [ "$a" = push ] && cp "{self.state / "waker"}" "{copy}"')
        r = self.ok(self.reflect("waker"))
        self.assertIn("waker reflected + pushed", r.stdout)
        seen = json.loads(copy.read_text(encoding="utf-8"))
        self.assertEqual((seen["attempt"]["id"], seen["attempt"]["outcome"]), (ATTEMPT, "spoken"))

    def test_a_hung_push_is_bounded_and_the_beat_still_landed(self):
        home = self.v2("waker", Pace("awake", 3), upstream=True)
        self.git_wrapper('  [ "$a" = push ] && exec sleep 30')
        t0 = time.monotonic()
        r = self.ok(self.reflect("waker", REFLECT_PUSH_BOUND_SEC="2"))
        self.assertLess(time.monotonic() - t0, 25)
        self.assertIn("waker reflected (push timed out after 2 s — tended on a future pass)", r.stdout)
        self.assertEqual(self.outcome("waker"), "spoken")
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="waker"))

    def test_a_hung_fetch_is_bounded_and_the_beat_goes_on(self):
        self.v2("waker", Pace("awake", 3), upstream=True)
        self.git_wrapper('  [ "$a" = fetch ] && exec sleep 30')
        t0 = time.monotonic()
        r = self.ok(self.reflect("waker", REFLECT_FETCH_BOUND_SEC="2"))
        self.assertLess(time.monotonic() - t0, 25)
        self.assertIn("waker: fetch timed out after 2 s — the beat goes on with what its clone holds",
                      r.stdout)
        self.assertEqual(self.outcome("waker"), "spoken")


class TestIsolated(Case):
    def test_modules_in_the_home_are_never_imported(self):
        canary = self.fx.root / "imported.txt"
        plant = f'open({str(canary)!r}, "a").write(__name__ + "\\n")\n'
        files = {"json.py": plant, "urllib/__init__.py": plant, "citizen_pace_lib/__init__.py": plant,
                 "clock.py": plant}
        home = self.v2("waker", Pace("awake", 3), files=files,
                       letters=[("2026-11-12-hello.md", "hello\n", "2026-11-12T00:00Z")])
        r = self.ok(self.reflect("waker", PYTHONPATH=str(home)))
        self.assertFalse(canary.exists(), canary.read_text() if canary.exists() else "")
        self.assertEqual(self.fx.subjects(home)[0], SUBJECT.format(slug="waker"))
        self.assertIn("waker: seen inbox/2026-11-12-hello.md", r.stdout)


if __name__ == "__main__":
    unittest.main()
