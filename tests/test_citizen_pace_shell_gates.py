"""The shell gates and citizen-beat.sh's guards (spec section 2 "Markers",
section 3 "The old paid lane", section 7 citizen-gates.sh and citizen-beat.sh;
CONTRACT sections 12 and 14).

keeps_rest, PR #24's marker-only reader, keeps an "asleep, letters wait" REST
asleep with unread mail and still lets mail wake a plain REST. pace_due asks
citizen_pace.py, with the attempt record, and fails toward not due. A beat of
citizen-beat.sh has edit tools, carries no pace paragraph and never shows or
marks a letter, so the script takes the per-citizen lock, refuses a home with
unread mail unless a person summons it, puts the pace files back after every
beat before it commits, and runs its inline Python isolated.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import attempts, homes, lock, paragraph, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

NOW = "2026-11-20T06:00Z"
OLD = "2026-11-10T06:00Z"            # a last beat ten days before NOW
HOME_MADE = "2026-11-01T00:00Z"
BEAT = h.TOOLS / "citizen-beat.sh"
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
LETTER = "2026-11-12-hello.md"
PACES = (Pace("asleep-letters-wait"), Pace("asleep"), Pace("listening"), Pace("awake", 5))
# what the beat script prints when it refuses a home with unread mail
LETTERS_WAIT = "has unread mail — letters wait for its free beat"


class Case(h.FixtureCase):
    """Shell runs use the fixture env with PACE_NOW; in-process calls use the
    fixture's git config, never the user's."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                  "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE",
                  "FLEET_BEAT_BOUND_SEC", "BEAT_SUMMONS", "LOVE"):
            os.environ.pop(k, None)
        self.set_now(NOW)
        self.state = self.fx.state
        # each run's TMPDIR, so a beat's scratch file never lands in the shared /tmp
        self.tmp = self.fx.root / "tmp"
        self.tmp.mkdir()

    def env(self, **extra):
        return self.fx.env_at(NOW, **{"TMPDIR": str(self.tmp), **extra})

    def ok(self, r, code=0):
        self.assertEqual(r.returncode, code, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r

    def gate(self, func, *args):
        return self.fx.gate(func, *args, env=self.env())

    def v2(self, slug, pace, *, last=OLD, letters=(), files=None):
        """A home with a v2 CADENCE.md and markers in step."""
        c = Cadence(slug, 2, pace, MINE, last_beat=last)
        made = {cad.CADENCE: cad.render_cadence(c)}
        for m, text in cad.marker_texts(c).items():
            if text is not None:
                made[m] = text
        made.update(files or {})
        return self.fx.make_home(slug, files=made, letters=letters, when=HOME_MADE,
                                 author=h.beat_author(slug))

    def marker_home(self, slug, pace, *, mail=True):
        """A home holding only the pace's marker (as written by pace/1), and a letter."""
        c = Cadence(slug, 2, pace, MINE)
        files = {m: t for m, t in cad.marker_texts(c).items() if t is not None}
        return self.fx.make_home(slug, files=files, letters=[(LETTER, "hello\n")] if mail else ())

    def claude_does(self, body: str) -> None:
        """A fake claude that runs `body` (sh) in the home it is run in, then answers."""
        self.fx.write_bin("claude", '#!/bin/sh\nprintf "%s\\n" "$PWD" >> "$FAKE_CLAUDE_LOG"\n'
                          f'{body}\nprintf "%s" "$FAKE_CLAUDE_OUT"\n')

    def beat(self, slug, script=BEAT, **extra):
        return self.fx.run(script, slug, env=self.env(**extra))

    def committed(self, home, rev="HEAD"):
        """The paths a commit changed."""
        return sorted(self.fx.git(home, "show", "--name-only", "--format=", rev).splitlines())

    def beat_log(self, slug):
        return (self.fx.memory / f"citizen-{slug}.log").read_text(encoding="utf-8")


# -- keeps_rest and unread_mail ---------------------------------------------------------

class TestKeepsRest(Case):
    def test_letters_wait_rest_keeps_rest_with_unread_mail(self):
        home = self.marker_home("waits", Pace("asleep-letters-wait"))
        self.assertIn("letters wait", (home / "REST").read_text(encoding="utf-8"))
        self.assertEqual(self.gate("has_mail", home).returncode, 0)
        self.assertEqual(self.gate("keeps_rest", home).returncode, 0)

    def test_plain_rest_still_wakes_on_unread_mail(self):
        home = self.marker_home("sleeper", Pace("asleep"))
        self.assertNotIn("letters wait", (home / "REST").read_text(encoding="utf-8").lower())
        self.assertEqual(self.gate("keeps_rest", home).returncode, 1)
        self.fx.write(home, f"inbox/.seen-{LETTER}", "surfaced 2026-11-19 — shown once, whatever the answer\n")
        self.assertEqual(self.gate("keeps_rest", home).returncode, 0)

    def test_each_marker_keeps_rest_and_only_letters_wait_holds_against_mail(self):
        for pace in PACES:
            with self.subTest(pace=pace.words()):
                slug = pace.kind.replace("-", "")
                quiet = self.marker_home(f"{slug}q", pace, mail=False)
                self.assertEqual(self.gate("keeps_rest", quiet).returncode, 0)
                lettered = self.marker_home(f"{slug}m", pace)
                want = 0 if pace.kind == "asleep-letters-wait" else 1
                self.assertEqual(self.gate("keeps_rest", lettered).returncode, want)

    def test_letters_wait_is_read_in_any_case(self):
        home = self.fx.make_home("shouts", rest="ASLEEP, LETTERS WAIT — by hand only\n",
                                 letters=[(LETTER, "hello\n")])
        self.assertEqual(self.gate("keeps_rest", home).returncode, 0)

    def test_only_rest_says_letters_wait(self):
        home = self.fx.make_home("eventer", event_only="listening — letters wait\n",
                                 letters=[(LETTER, "hello\n")])
        self.assertEqual(self.gate("keeps_rest", home).returncode, 1)

    def test_a_linked_rest_is_never_read(self):
        target = self.fx.outside / "rest-text"
        target.write_text("asleep, letters wait\n", encoding="utf-8")
        home = self.fx.make_home("linked", letters=[(LETTER, "hello\n")])
        self.fx.link(home, "REST", target)
        # mail wakes it as before, and reflect then refuses the link
        self.assertEqual(self.gate("keeps_rest", home).returncode, 1)
        (home / "inbox" / LETTER).unlink()
        self.assertEqual(self.gate("keeps_rest", home).returncode, 0)

    def test_a_rest_that_is_not_a_regular_file_is_not_opened(self):
        home = self.fx.make_home("piped", letters=[(LETTER, "hello\n")])
        os.mkfifo(home / "REST")
        self.assertEqual(self.gate("keeps_rest", home).returncode, 1)   # returns, never blocks


class TestUnreadMail(Case):
    def test_unread_mail_is_has_mail_and_a_letter_set_aside_at_rollback(self):
        home = self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        self.assertEqual(self.gate("unread_mail", home).returncode, 0)
        marker = self.fx.write(home, f"inbox/.seen-{LETTER}", "surfaced 2026-11-19 — shown once\n")
        self.assertEqual(self.gate("unread_mail", home).returncode, 1)
        marker.write_text(tend.ROLLBACK_TEXT.format(day="2026-11-19"), encoding="utf-8")
        self.assertEqual(self.gate("has_mail", home).returncode, 1)       # seen, to the old code
        self.assertEqual(self.gate("unread_mail", home).returncode, 0)    # unread, to the new
        self.assertEqual(homes.unread_letters(home), [f"inbox/{LETTER}"])

    def test_links_hold_no_unread_mail(self):
        home = self.fx.make_home("linkedletter", symlinked_letters=[LETTER])
        self.assertEqual(self.gate("unread_mail", home).returncode, 1)
        elsewhere = self.fx.outside / "letters"
        elsewhere.mkdir()
        (elsewhere / LETTER).write_text("hello\n", encoding="utf-8")
        home = self.fx.make_home("linkedinbox")
        self.fx.link(home, "inbox", elsewhere)
        self.assertEqual(self.gate("unread_mail", home).returncode, 1)
        set_aside = self.fx.outside / "set-aside"
        set_aside.write_text(tend.ROLLBACK_TEXT.format(day="2026-11-19"), encoding="utf-8")
        home = self.fx.make_home("linkedmarker", letters=[(LETTER, "hello\n")])
        self.fx.link(home, f"inbox/.seen-{LETTER}", set_aside)
        self.assertEqual(self.gate("unread_mail", home).returncode, 1)    # a linked marker is seen
        self.assertEqual(homes.unread_letters(home), [])


# -- pace_due --------------------------------------------------------------------------

class TestPaceDue(Case):
    SCRIPT = ('. "$1"; shift; pace_due "$@"; rc=$?; printf "%s\\n" "$PACE_DUE"; '
              'pace_reason || echo "-"; exit "$rc"')

    def due_gate(self, home, *flags, gates=h.GATES, env=None, cwd=None, prelude=""):
        """(exit, the JSON due printed or None, pace_reason or '-')."""
        r = subprocess.run(["bash", "-c", prelude + self.SCRIPT, "_", str(gates), str(home), *flags],
                           env=self.env(**(env or {})), cwd=str(cwd or self.fx.root),
                           capture_output=True, text=True, timeout=60, check=False)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 2, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r.returncode, (json.loads(lines[0]) if lines[0] else None), lines[1]

    def test_a_due_citizen_is_due_with_its_reason(self):
        home = self.v2("waker", Pace("awake", 3))
        rc, d, reason = self.due_gate(home)
        self.assertEqual((rc, reason), (0, "schedule"))
        self.assertEqual((d["due"], d["reason"]), (True, "schedule"))

    def test_a_citizen_not_due_is_not(self):
        home = self.v2("sleeper", Pace("asleep"))
        rc, d, reason = self.due_gate(home)
        self.assertEqual((rc, reason), (1, "no-mail"))
        self.assertFalse(d["due"])

    def test_a_letter_makes_an_asleep_citizen_due(self):
        home = self.v2("reader", Pace("asleep"), letters=[(LETTER, "hello\n")])
        self.assertEqual(self.due_gate(home)[::2], (0, "letter"))

    def test_it_reads_the_attempt_record_in_love_memory(self):
        home = self.v2("waker", Pace("awake", 3))     # CADENCE.md: last beat ten days ago
        attempts.record_outcome(self.state, "waker", "20261119T0600Z-1-1", "silent",
                                "2026-11-19T06:00Z")
        self.assertEqual(self.due_gate(home)[::2], (1, "not-yet"))

    def test_love_set_by_a_launcher_wins_over_love_dir(self):
        home = self.v2("waker", Pace("awake", 3))
        other = self.fx.root / "other-love"
        attempts.record_outcome(other / "memory" / ".fleet-last", "waker", "20261119T0600Z-1-1",
                                "spoken", "2026-11-19T06:00Z")
        self.assertEqual(self.due_gate(home)[::2], (0, "schedule"))
        self.assertEqual(self.due_gate(home, prelude=f'LOVE="{other}"; ')[::2], (1, "not-yet"))

    def test_the_gate_starts_a_clock_once(self):
        home = self.v2("newcomer", Pace("awake", 3), last=None)
        self.assertEqual(self.due_gate(home)[::2], (1, "not-yet"))
        rec = attempts.load(self.state, "newcomer")
        self.assertEqual((rec["last_beat"], rec["seeded"]), (NOW, attempts.SEEDED))
        later = {"PACE_NOW": "2026-11-20T09:00Z"}
        self.assertEqual(self.due_gate(home, env=later)[::2], (1, "not-yet"))
        self.assertEqual(attempts.load(self.state, "newcomer")["last_beat"], NOW)

    def test_a_summons_passes_for_any_pace(self):
        for pace in PACES:
            with self.subTest(pace=pace.words()):
                home = self.v2(pace.kind.replace("-", ""), pace, last="2026-11-19T06:00Z")
                self.assertEqual(self.due_gate(home)[0], 1)
                self.assertEqual(self.due_gate(home, "--summoned")[::2], (0, "summoned"))

    def test_any_error_is_not_due(self):
        home = self.v2("waker", Pace("awake", 3))
        self.assertEqual(self.due_gate(home)[0], 0)
        cases = {"missing": None,
                 "crashing": "raise RuntimeError('broken')\n",
                 "erroring": "import sys\nprint('{\"due\": true}')\nsys.exit(2)\n"}
        for name, source in cases.items():
            with self.subTest(citizen_pace_py=name):
                tools = self.fx.root / f"tools-{name}"
                tools.mkdir()
                shutil.copy(h.GATES, tools / "citizen-gates.sh")
                if source is not None:
                    (tools / "citizen_pace.py").write_text(source, encoding="utf-8")
                rc, _d, reason = self.due_gate(home, gates=tools / "citizen-gates.sh")
                self.assertEqual(rc, 1)
                if name != "erroring":
                    self.assertEqual(reason, "-")
        not_a_home = self.fx.root / "not-a-citizen"
        not_a_home.mkdir()
        self.assertEqual(self.due_gate(not_a_home)[0], 1)

    def test_it_runs_isolated_from_any_folder(self):
        home = self.v2("waker", Pace("awake", 3))
        canary = self.fx.outside / "imported"
        trap = f"open({str(canary)!r}, 'w').write('imported')\n"
        self.fx.write(home, "json.py", trap)
        self.fx.write(home, "urllib/__init__.py", trap)
        # the gates sourced by a relative path, then the caller moves into the home
        script = ('. tools/citizen-gates.sh; cd "$1"; pace_due "$1"; rc=$?; '
                  'pace_reason; exit "$rc"')
        r = subprocess.run(["bash", "-c", script, "_", str(home)], env=self.env(),
                           cwd=str(h.ROOT), capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual((r.returncode, r.stdout), (0, "schedule\n"), r.stderr)
        self.assertFalse(canary.exists())


class TestSpendGateIsolated(Case):
    def test_the_spend_gate_imports_nothing_from_the_folder_it_runs_in(self):
        folder = self.fx.root / "cwd"
        canary = self.fx.outside / "imported"
        trap = f"open({str(canary)!r}, 'w').write('imported')\n"
        for name in ("json.py", "math.py"):
            self.fx.write(folder, name, trap)
        ledger = self.fx.memory / "fleet-economy.jsonl"
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        ledger.write_text(json.dumps({"ts": f"{today}T09:00:00", "name": "fixture",
                                      "cost_usd": 0.25}) + "\n", encoding="utf-8")
        script = '. "$1"; spent_today "$2"; budget_reached "$2" 1.50 && echo reached || echo under'
        r = subprocess.run(["bash", "-c", script, "_", str(h.GATES), str(ledger)], env=self.env(),
                           cwd=str(folder), capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.stdout, "0.2500\nunder\n", r.stderr)
        self.assertFalse(canary.exists())


# -- citizen-beat.sh: the lock ----------------------------------------------------------

class TestBeatLock(Case):
    def test_it_holds_the_lock_through_the_beat_and_releases_it(self):
        self.fx.make_home("waker")
        seen = self.fx.root / "lock-seen"
        lockdir = self.state / "waker.lock"
        self.claude_does(f'cat "{lockdir}/pid" > "{seen}" 2>/dev/null')
        self.ok(self.beat("waker"))
        self.assertEqual(self.fx.claude_calls(), ["citizen-waker"])
        self.assertTrue(seen.read_text(encoding="utf-8").strip().isdigit())
        self.assertFalse(os.path.lexists(lockdir))

    def test_a_held_lock_means_busy_and_no_beat(self):
        self.fx.make_home("waker")
        self.assertTrue(lock.take(self.state, "waker", os.getpid()).got)   # a live holder: this test
        r = self.ok(self.beat("waker"))
        self.assertIn(f"waker: its lock is held by pid {os.getpid()}, started {NOW} — busy, no beat",
                      r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertEqual(lock.holder(self.state, "waker").pid, os.getpid())
        self.assertIsNone(attempts.load(self.state, "waker")["attempt"])   # a hand-run writes none

    def test_a_stale_lock_is_reclaimed_and_logged(self):
        self.fx.make_home("waker")
        gone = subprocess.Popen(["/usr/bin/true"])
        gone.wait()
        self.assertTrue(lock.take(self.state, "waker", gone.pid).got)
        r = self.ok(self.beat("waker"))
        self.assertIn(f"waker: a stale lock was reclaimed (pid {gone.pid} is gone)", r.stdout)
        self.assertEqual(self.fx.claude_calls(), ["citizen-waker"])
        self.assertFalse(os.path.lexists(self.state / "waker.lock"))

    def test_a_lock_that_cannot_be_taken_refuses_the_beat(self):
        self.fx.make_home("waker")
        self.state.write_text("not a folder\n", encoding="utf-8")
        r = self.ok(self.beat("waker"), 1)
        self.assertIn("waker's lock could not be taken — beat refused", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_halt_takes_no_lock(self):
        self.fx.make_home("waker")
        (self.fx.love / "HALT").write_text("", encoding="utf-8")
        self.ok(self.beat("waker"))
        self.assertFalse(os.path.lexists(self.state / "waker.lock"))
        self.assertEqual(self.fx.claude_calls(), [])


# -- citizen-beat.sh: letters wait for the free lane ------------------------------------

class TestBeatLettersWait(Case):
    def test_unread_mail_is_refused_without_a_summons(self):
        home = self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        head = self.fx.head(home)
        r = self.ok(self.beat("reader"))
        self.assertIn(f"reader {LETTERS_WAIT}", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertFalse((home / "inbox" / f".seen-{LETTER}").exists())
        self.assertEqual(self.fx.head(home), head)
        self.assertFalse(os.path.lexists(self.state / "reader.lock"))

    def test_a_plain_rest_woken_by_mail_is_refused_too(self):
        self.marker_home("sleeper", Pace("asleep"))
        r = self.ok(self.beat("sleeper"))
        self.assertIn(f"sleeper {LETTERS_WAIT}", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_a_letters_wait_rest_with_mail_rests(self):
        self.marker_home("waits", Pace("asleep-letters-wait"))
        r = self.ok(self.beat("waits"))
        self.assertIn("waits rests by its own standing choice", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_a_summons_beats_with_unread_mail_and_leaves_it_unread(self):
        home = self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        r = self.ok(self.beat("reader", BEAT_SUMMONS="1"))
        self.assertIn("reader has unread mail; a person's summons gives it this beat", r.stdout)
        self.assertEqual(self.fx.claude_calls(), ["citizen-reader"])
        self.assertEqual(homes.unread_letters(home), [f"inbox/{LETTER}"])

    def test_a_summons_never_rides_an_enforced_beat(self):
        self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        r = self.ok(self.beat("reader", BEAT_SUMMONS="1", BEAT_BUDGET_ENFORCE="1",
                              FLEET_DAILY_BUDGET_USD="1.50"))
        self.assertIn(f"reader {LETTERS_WAIT}", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_a_letter_set_aside_at_rollback_is_still_unread(self):
        home = self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        self.fx.write(home, f"inbox/.seen-{LETTER}", tend.ROLLBACK_TEXT.format(day="2026-11-19"))
        r = self.ok(self.beat("reader"))
        self.assertIn(f"reader {LETTERS_WAIT}", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_a_seen_letter_does_not_hold_a_beat(self):
        home = self.fx.make_home("reader", letters=[(LETTER, "hello\n")])
        self.fx.write(home, f"inbox/.seen-{LETTER}", "surfaced 2026-11-19 — shown once\n")
        r = self.ok(self.beat("reader"))
        self.assertNotIn(LETTERS_WAIT, r.stdout)
        self.assertEqual(self.fx.claude_calls(), ["citizen-reader"])


# -- citizen-beat.sh: guard-restore -----------------------------------------------------

class TestGuardRestore(Case):
    def test_a_beat_with_tools_never_sets_a_pace(self):
        # the spec's case: no REST, EVENT-ONLY, CADENCE.md or inbox/ at HEAD
        home = self.fx.make_home("maker", when=HOME_MADE)
        head = self.fx.head(home)
        self.claude_does('printf "# CADENCE\\n\\n- pace: awake, every 3 days\\n" > CADENCE.md\n'
                         'printf "my own rest\\n" > REST\n'
                         'mkdir -p inbox && printf "surfaced\\n" > inbox/.seen-letter.md\n'
                         'printf "made something\\n" > notes.md\n'
                         'mkdir -p beats && printf "a line\\n" > beats/2026-11.md')
        r = self.ok(self.beat("maker"))
        # one commit, holding the beat's own work and none of the pace files
        self.assertEqual(self.fx.git(home, "rev-parse", "HEAD~1"), head)
        self.assertEqual(self.committed(home), ["beats/2026-11.md", "notes.md"])
        for rel in ("CADENCE.md", "REST", "inbox"):
            self.assertFalse(os.path.lexists(home / rel), rel)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")
        # logged once
        line = tend.guard_line("maker", ["CADENCE.md", "REST", "inbox/.seen-letter.md"])
        self.assertIn(line, r.stdout)
        self.assertEqual(self.beat_log("maker").count(line), 1)
        # kept as a notice; a hand-run moves no clock
        rec = attempts.load(self.state, "maker")
        self.assertEqual([n["key"] for n in rec["notices"]], [tend.RESTORED])
        self.assertIsNone(rec["attempt"])
        # the next free beat's paragraph tells the citizen, once
        told = self.ok(self.fx.pace("paragraph", home, "--state", self.state, env=self.env()))
        self.assertEqual(told.stdout.count(paragraph.RESTORED), 1)
        self.ok(self.fx.pace("record", "maker", "--state", self.state, "--attempt",
                             "20261120T0600Z-1-1", "--outcome", "silent", env=self.env()))
        again = self.ok(self.fx.pace("paragraph", home, "--state", self.state, env=self.env()))
        self.assertNotIn(paragraph.RESTORED, again.stdout)

    def test_tracked_pace_files_are_put_back_path_by_path(self):
        c = Cadence("keeper", 2, Pace("asleep"), MINE, last_beat=OLD)
        cadence_text, rest_text = cad.render_cadence(c), cad.marker_texts(c)["REST"]
        seen = "surfaced 2026-11-12 — shown once, whatever the answer\n"
        home = self.fx.make_home("keeper", files={cad.CADENCE: cadence_text, "REST": rest_text,
                                                  f"inbox/{LETTER}": "hello\n",
                                                  f"inbox/.seen-{LETTER}": seen})
        mine = "inbox/2026-11-20-to-myself.md"
        self.claude_does('printf -- "- pace: awake, every 3 days\\n" >> CADENCE.md\n'
                         'rm REST\n'
                         f'printf "changed\\n" >> inbox/{LETTER}\n'
                         f'rm inbox/.seen-{LETTER}\n'
                         f'printf "new\\n" > {mine}\n'
                         'printf "made\\n" > notes.md')
        r = self.ok(self.beat("keeper", BEAT_SUMMONS="1"))
        self.assertEqual(self.committed(home), ["notes.md"])
        self.assertEqual((home / cad.CADENCE).read_text(encoding="utf-8"), cadence_text)
        self.assertEqual((home / "REST").read_text(encoding="utf-8"), rest_text)
        self.assertEqual((home / "inbox" / LETTER).read_text(encoding="utf-8"), "hello\n")
        self.assertEqual((home / "inbox" / f".seen-{LETTER}").read_text(encoding="utf-8"), seen)
        self.assertFalse((home / mine).exists())
        line = tend.guard_line("keeper", sorted([cad.CADENCE, "REST", f"inbox/{LETTER}",
                                                 f"inbox/.seen-{LETTER}", mine]))
        self.assertEqual(self.beat_log("keeper").count(line), 1, r.stdout)

    def test_a_beat_that_leaves_the_pace_files_alone_is_told_nothing(self):
        home = self.fx.make_home("quiet")
        self.claude_does('printf "made\\n" > notes.md')
        r = self.ok(self.beat("quiet"))
        self.assertNotIn("pace files", r.stdout)
        self.assertEqual(self.committed(home), ["notes.md"])
        self.assertEqual(attempts.load(self.state, "quiet")["notices"], [])

    def test_pace_files_that_cannot_be_checked_stay_out_of_the_commit(self):
        # a tools folder whose citizen_pace.py takes the lock but fails guard-restore
        tools = self.fx.root / "tools"
        tools.mkdir()
        for name in ("citizen-beat.sh", "citizen-gates.sh"):
            shutil.copy(h.TOOLS / name, tools / name)
        (tools / "citizen_pace.py").write_text(
            "import sys\nsys.exit(2 if sys.argv[1:2] == ['guard-restore'] else 0)\n", encoding="utf-8")
        home = self.fx.make_home("maker")
        self.claude_does('printf "x\\n" > REST\nprintf "made\\n" > notes.md\n'
                         'mkdir -p inbox && printf "y\\n" > inbox/.seen-a.md')
        r = self.ok(self.beat("maker", script=tools / "citizen-beat.sh"))
        self.assertIn("maker: its pace files could not be checked after its beat", r.stdout)
        self.assertEqual(self.committed(home), ["notes.md"])
        # left as they are, for the fleet's tend to restore at its next tick
        self.assertEqual(sorted(self.fx.git(home, "status", "--porcelain").splitlines()),
                         ["?? REST", "?? inbox/"])


# -- citizen-beat.sh: the beat's result file ---------------------------------------------

class TestBeatResultFile(Case):
    """The beat's result goes to a file of its own, made by mktemp in $TMPDIR with
    the X's at the end of its name. PR #24's name ended in ".json" after them,
    which macOS's mktemp leaves as written: one file left by a killed beat made
    every later mktemp of that citizen fail, and its beats ran no claude."""

    def ledger(self):
        lines = (self.fx.memory / "fleet-economy.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines]

    def test_a_file_left_at_the_old_name_holds_up_no_beat(self):
        name = f"stale{os.getpid()}"         # the old name is in the shared /tmp: one of its own
        old = Path(f"/tmp/beat-{name}-XXXXXX.json")
        old.write_text("left by a killed beat\n", encoding="utf-8")
        self.addCleanup(lambda: old.unlink() if old.exists() else None)
        self.fx.make_home(name)
        r = self.ok(self.beat(name))
        self.assertEqual(self.fx.claude_calls(), [f"citizen-{name}"])
        self.assertIn(f"{name} beat metered: $0.0100", r.stdout)
        self.assertEqual([(e["name"], e["cost_usd"]) for e in self.ledger()], [(name, 0.01)])
        self.assertEqual(sorted(os.listdir(self.tmp)), [])        # its own file, removed after it

    def test_two_citizens_beat_side_by_side(self):
        # two hand-runs at once: each stub claude waits until both have started
        for slug in ("ana", "bo"):
            self.fx.make_home(slug)
        started, seen = self.fx.root / "started", self.fx.root / "seen"
        started.mkdir()
        seen.mkdir()
        self.claude_does(f'touch "{started}/${{PWD##*/}}"\ni=0\n'
                         f'while [ "$(ls "{started}" | wc -l)" -lt 2 ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done\n'
                         f'ls "{started}" | wc -l | tr -d " " > "{seen}/${{PWD##*/}}"')
        runs = [subprocess.Popen(["bash", str(BEAT), slug], env=self.env(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for slug in ("ana", "bo")]
        outs = [p.communicate(timeout=120)[0] for p in runs]
        self.assertEqual([p.returncode for p in runs], [0, 0], outs)
        self.assertEqual(sorted(self.fx.claude_calls()), ["citizen-ana", "citizen-bo"])
        self.assertEqual({f: (seen / f).read_text().strip() for f in os.listdir(seen)},
                         {"citizen-ana": "2", "citizen-bo": "2"})
        for slug, out in zip(("ana", "bo"), outs):
            self.assertIn(f"{slug} beat metered: $0.0100", out)
        self.assertEqual(sorted((e["name"], e["cost_usd"]) for e in self.ledger()), [("ana", 0.01), ("bo", 0.01)])
        self.assertEqual(sorted(os.listdir(self.tmp)), [])

    def test_no_result_file_means_no_beat(self):
        self.fx.make_home("waker")
        r = self.ok(self.beat("waker", TMPDIR=str(self.fx.root / "no-such-folder")), 1)
        line = "ERROR: waker's beat has no file for its result — beat refused"
        self.assertIn(line, r.stdout)
        self.assertIn(line, self.beat_log("waker"))
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertFalse((self.fx.memory / "fleet-economy.jsonl").exists())
        self.assertFalse(os.path.lexists(self.state / "waker.lock"))


# -- citizen-beat.sh: isolation ---------------------------------------------------------

class TestBeatIsolated(Case):
    def test_its_inline_python_imports_nothing_from_the_home(self):
        canary = self.fx.outside / "imported"
        trap = f"open({str(canary)!r}, 'w').write('imported')\n"
        self.fx.make_home("waker", files={"json.py": trap, "math.py": trap, "datetime.py": trap,
                                          "urllib/__init__.py": trap})
        self.claude_does('printf "made\\n" > notes.md\nprintf "x\\n" > REST')
        r = self.ok(self.beat("waker"))
        self.assertIn("beat metered: $0.0100", r.stdout)
        self.assertIn(tend.guard_line("waker", ["REST"]), r.stdout)
        ledger = (self.fx.memory / "fleet-economy.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["cost_usd"] for x in ledger], [0.01])
        self.assertFalse(canary.exists())


if __name__ == "__main__":
    unittest.main()
