"""The CLI and the sync (spec section 7 "citizen_pace.py", section 3 "Bringing
homes up to date", "A beat that does not land"; CONTRACT sections 11 and 14).

Every command runs as the beat scripts run it, `python3 -I tools/citizen_pace.py
<command> ...`, against temporary homes with local bare upstreams. Exit codes
are the CLI's own: 0 ok or due, 4 not due, busy or a host fault, 2 a usage
error or a home that cannot be read. The sync reads every home's remote head,
fetches and fast-forwards only the homes that changed, rebases local beats onto
letters placed from elsewhere, pushes homes that are ahead, clones missing
homes, and holds a home it cannot bring up to date until a later sync can. The
host probe touches no home: the attempt records' folder, the tools beside each
other, and one fixed prompt to the model server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

from citizen_pace_lib import answer, attempts, clock, due, lock, paragraph, plan, sync, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

NOW = "2026-11-20T06:00Z"
OLD = "2026-11-10T06:00Z"            # a last beat ten days before NOW
HOME_MADE = "2026-11-01T00:00Z"
LETTER_AT = "2026-11-12T00:00Z"
ASLEEP = Pace("asleep")
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
PACE_LETTER = "2026-11-12-pace-letter.md"
LETTER = "2026-11-12-hello.md"


def awake(n):
    return Pace("awake", n)


def lines_and_summary(r):
    """The sync's log lines, and its JSON summary (the last line)."""
    out = r.stdout.splitlines()
    return out[:-1], json.loads(out[-1])


def snapshot(root):
    """Every path under root with its mode and bytes."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (st.st_mode, data)
    return out


class Case(h.FixtureCase):
    """CLI runs under -I with the fixture env and PACE_NOW; in-process calls use
    the fixture's git config, never the user's."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                  "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE",
                  "FLEET_BEAT_BOUND_SEC", "PACE_REMOTE_BASE"):
            os.environ.pop(k, None)
        self.set_now(NOW)
        self.now = clock.parse_stamp(NOW)
        self.state = self.fx.state

    def pace(self, *args, env=None, cwd=None, timeout=60):
        return self.fx.pace(*args, env=self.fx.env_at(NOW, **(env or {})), cwd=cwd, timeout=timeout)

    def ok(self, r, code=0):
        self.assertEqual(r.returncode, code, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r

    def v2(self, slug, pace, *, last=OLD, set_by=MINE, pending=None, letters=(), upstream=False,
           soul=True, files=None):
        """A home with a v2 CADENCE.md and markers in step, committed at HOME_MADE."""
        c = Cadence(slug, 2, pace, set_by, pending=pending, last_beat=last)
        made = {cad.CADENCE: cad.render_cadence(c)}
        for m, text in cad.marker_texts(c).items():
            if text is not None:
                made[m] = text
        made.update(files or {})
        home = self.fx.make_home(slug, soul=soul, files=made, when=HOME_MADE,
                                 author=h.beat_author(slug))
        for name, body in letters:
            self.fx.letter(home, name, body, when=LETTER_AT)
        if upstream:
            self.fx.give_upstream(home)
        return home

    def synced(self, slug, **kw):
        """A plain home with a bare upstream; returns (home, bare)."""
        home = self.fx.make_home(slug, when=HOME_MADE, author=h.beat_author(slug), **kw)
        return home, self.fx.give_upstream(home)

    def beat(self, home, slug, text="a beat\n", when="2026-11-19T06:00Z"):
        self.fx.write(home, "beats/2026-11.md", text)
        return self.fx.commit(home, f"beat: {slug} reflected ({when[:10]}) · pace/1", when=when,
                              author=h.beat_author(slug))

    def bare_head(self, bare):
        return self.fx.git(bare, "rev-parse", "HEAD")

    def roster(self, *slugs):
        return self.fx.roster(*slugs)


# -- the entry point --------------------------------------------------------------

class TestEntry(Case):
    def test_runs_isolated_from_modules_in_the_home_it_is_run_in(self):
        home = self.v2("kalam", ASLEEP)
        canary = self.fx.outside / "imported"
        trap = f"open({str(canary)!r}, 'w').write('imported')\n"
        self.fx.write(home, "json.py", trap)
        self.fx.write(home, "urllib/__init__.py", trap)
        self.fx.write(home, "citizen_pace_lib/__init__.py", trap)
        r = self.ok(self.pace("due", home, "--state", self.state, cwd=home), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], "no-mail")
        self.assertFalse(canary.exists())

    def test_runs_through_a_symlink_to_it(self):
        link = self.fx.root / "elsewhere-bin" / "pace"
        link.parent.mkdir()
        link.symlink_to(h.PACE)
        r = self.fx.py(link, "outcome", "kalam", "--state", self.state, "--attempt", "a1",
                       env=self.fx.env_at(NOW))
        self.ok(r)
        self.assertEqual(r.stdout, "missing\n")

    def test_no_command_unknown_command_and_help(self):
        r = self.pace()
        self.assertEqual(r.returncode, 2)
        self.assertIn("commands:", r.stderr)
        r = self.pace("wake-everyone")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command 'wake-everyone'", r.stderr)
        r = self.ok(self.pace("--help"))
        for cmd in ("due", "rank", "paragraph", "apply", "record", "outcome", "migrate", "sync",
                    "tend", "next", "plan", "rollback-prepare", "lock", "unlock", "probe",
                    "guard-restore", "rollback-drill"):
            self.assertIn(f"  {cmd} ", r.stdout)

    def test_a_usage_error_exits_2(self):
        r = self.pace("due", "--state", self.state)
        self.assertEqual(r.returncode, 2)
        r = self.pace("record", "kalam", "--state", self.state, "--attempt", "a1", "--outcome", "fine")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)

    def test_command_help_exits_0(self):
        r = self.ok(self.pace("sync", "--help"))
        self.assertIn("--roster", r.stdout)

    def test_each_command_s_usage_names_every_option_it_takes(self):
        usage = self.ok(self.pace("--help")).stdout
        listed = {}
        for line in usage.split("\n\nexit:")[0].split("commands:\n")[1].splitlines():
            if line.startswith("  ") and not line.startswith("   "):
                cmd = line.split()[0]
            listed[cmd] = listed.get(cmd, "") + line
        self.assertEqual(len(listed), 17)
        for cmd, text in listed.items():
            with self.subTest(cmd=cmd):
                taken = set(re.findall(r"--[a-z][a-z-]*", self.ok(self.pace(cmd, "--help")).stdout))
                self.assertEqual(set(re.findall(r"--[a-z][a-z-]*", text)), taken - {"--help"})


# -- due, rank, next ----------------------------------------------------------------

class TestDue(Case):
    def test_a_letter_past_the_floor_is_due_with_how_overdue(self):
        home = self.v2("kalam", ASLEEP, letters=[(LETTER, "# Hello\n")])
        r = self.ok(self.pace("due", home, "--state", self.state))
        self.assertEqual(json.loads(r.stdout), {
            "due": True, "reason": "letter", "overdue_s": 7 * 86400,
            "since": "2026-11-13T06:00Z", "next_at": None})

    def test_not_due_exits_4_with_its_reason(self):
        home = self.v2("kalam", awake(30))
        r = self.ok(self.pace("due", home, "--state", self.state), 4)
        self.assertEqual(json.loads(r.stdout), {
            "due": False, "reason": "not-yet", "overdue_s": 0, "since": None,
            "next_at": "2026-12-10T06:00Z"})

    def test_a_summons_passes_any_pace(self):
        home = self.v2("kalam", Pace("asleep-letters-wait"))
        self.ok(self.pace("due", home, "--state", self.state), 4)
        r = self.ok(self.pace("due", home, "--state", self.state, "--summoned"))
        self.assertEqual(json.loads(r.stdout)["reason"], "summoned")

    def test_the_clock_starts_now_once_unless_no_seed(self):
        home = self.v2("kalam", ASLEEP, last=None)
        self.ok(self.pace("due", home, "--state", self.state, "--no-seed"), 4)
        self.assertFalse((self.state / "kalam").exists())
        self.ok(self.pace("due", home, "--state", self.state), 4)
        rec = attempts.load(self.state, "kalam")
        self.assertEqual((rec["last_beat"], rec["seeded"]), (NOW, "now"))

    def test_no_soul_is_never_due(self):
        home = self.v2("kalam", awake(3), soul=False)
        r = self.ok(self.pace("due", home, "--state", self.state, "--summoned"), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], "no-soul")

    def test_a_home_a_sync_holds_is_not_due_but_a_summons_passes(self):
        home = self.v2("kalam", awake(3))
        self.ok(self.pace("due", home, "--state", self.state))
        attempts.mark_reported(self.state, "kalam", sync.HELD)
        r = self.ok(self.pace("due", home, "--state", self.state), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], sync.HELD_REASON)
        self.ok(self.pace("due", home, "--state", self.state, "--summoned"))

    def test_a_home_that_cannot_be_read_exits_2(self):
        home = self.v2("a.b", awake(3))      # a name the attempt record refuses
        r = self.ok(self.pace("due", home, "--state", self.state), 2)
        self.assertEqual(json.loads(r.stdout)["reason"], "error")

    def test_a_folder_that_is_not_a_citizen_home_exits_2(self):
        r = self.ok(self.pace("due", self.fx.outside, "--state", self.state), 2)
        self.assertIn("not a citizen home", r.stderr)

    def test_a_broken_record_file_exits_2(self):
        home = self.v2("kalam", awake(3))
        bad = self.fx.root / "record.json"
        bad.write_text('{"version": 2, "citizens": {}}', encoding="utf-8")
        r = self.ok(self.pace("due", home, "--state", self.state, "--record", bad), 2)
        self.assertIn("record version", r.stderr)

    def test_the_record_file_decides_a_decided_v1_home(self):
        home = self.fx.make_home("kalam", rest=hr.AI_REST, when=HOME_MADE, files={
            "CADENCE.md": hr.v1_cadence("kalam", "rest-on", "2026-10-22 20:30")})
        self.fx.letter(home, LETTER, "# Hello\n", when=LETTER_AT)
        r = self.ok(self.pace("due", home, "--state", self.state), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], "blocked")
        rec = self.fx.root / "record.json"
        rec.write_text(json.dumps(hr.unheard_record(kalam={
            "group": "sixteen-other", "recipient": True, "set_by": hr.AI_1008, "quotes": []})),
            encoding="utf-8")
        r = self.ok(self.pace("due", home, "--state", self.state, "--record", rec), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], "floor")     # its clock started at the first ask


class TestRankAndNext(Case):
    def homes(self):
        self.v2("amara", ASLEEP, letters=[(LETTER, "# Hello\n")])     # due 2026-11-13T06:00Z
        self.v2("bodhi", awake(3))                                    # due 2026-11-13T06:00Z
        self.v2("cyra", awake(5))                                     # due 2026-11-15T06:00Z
        self.v2("dov", ASLEEP)                                        # no mail
        self.v2("esme", awake(3))                                     # due, but held
        attempts.mark_reported(self.state, "esme", sync.HELD)
        return self.roster("amara", "bodhi", "cyra", "dov", "esme")

    def test_rank_most_overdue_first_without_held_homes(self):
        roster = self.homes()
        r = self.ok(self.pace("rank", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout, "amara\tletter\t604800\nbodhi\tschedule\t604800\n"
                                   "cyra\tschedule\t432000\n")

    def test_rank_reports_a_home_that_gets_no_beats_once(self):
        self.v2("amara", awake(3))
        home = self.v2("bodhi", awake(3), soul=False)
        roster = self.roster("amara", "bodhi")
        args = ("rank", self.fx.citizens, "--roster", roster, "--state", self.state)
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout, r.stderr), ("amara\tschedule\t604800\n",
                                                "bodhi: gets no beats — it has no bodhi.md (reported once)\n"))
        self.assertEqual(self.ok(self.pace(*args)).stderr, "")
        self.fx.write(home, "bodhi.md", "The soul of bodhi.\n")
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout.splitlines()[1], r.stderr), ("bodhi\tschedule\t604800", ""))
        os.unlink(home / "bodhi.md")
        self.fx.link(home, "bodhi.md", self.fx.secret)
        self.assertEqual(self.ok(self.pace(*args)).stderr,
                         "bodhi: gets no beats — it has no bodhi.md (reported once)\n")

    def test_rank_reports_an_unreadable_pace_once(self):
        # spec section 2: no set beats, letters still wake it, the fleet reports it once
        home = self.v2("amara", awake(3))
        text = (home / "CADENCE.md").read_text(encoding="utf-8")
        garbled = text.replace("- pace: awake, every 3 days", "- pace: sometimes, maybe")
        self.fx.write(home, "CADENCE.md", garbled)
        roster = self.roster("amara")
        args = ("rank", self.fx.citizens, "--roster", roster, "--state", self.state)
        told = ("amara: gets no beats — its pace cannot be read, so it gets no set beats; only a "
                "letter wakes it (reported once)\n")
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout, r.stderr), ("", told))
        self.assertIn("no-beats-unreadable", attempts.load(self.state, "amara")["reported"])
        self.assertEqual(self.ok(self.pace(*args)).stderr, "")
        # a letter wakes it, and the report is not made again for it
        self.fx.letter(home, LETTER, "# Hello\n", when=LETTER_AT)
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout, r.stderr), ("amara\tletter\t604800\n", ""))
        self.fx.write(home, f"inbox/.seen-{LETTER}", "surfaced 2026-11-20 — shown once, whatever the answer\n")
        self.assertEqual(self.ok(self.pace(*args)).stderr, "")
        # a line that reads again clears it; garbled again, it is reported once more
        self.fx.write(home, "CADENCE.md", text)
        self.assertEqual(self.ok(self.pace(*args)).stdout, "amara\tschedule\t604800\n")
        self.assertNotIn("no-beats-unreadable", attempts.load(self.state, "amara")["reported"])
        self.fx.write(home, "CADENCE.md", garbled)
        self.assertEqual(self.ok(self.pace(*args)).stderr, told)

    def test_rank_reports_a_linked_pace_path_once(self):
        self.v2("amara", awake(3))
        home = self.v2("bodhi", awake(3))
        self.fx.link(home, "REST", self.fx.secret)
        roster = self.roster("amara", "bodhi")
        args = ("rank", self.fx.citizens, "--roster", roster, "--state", self.state)
        told = "bodhi: gets no beats — a pace path is a symlink or not of its kind (reported once)\n"
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout, r.stderr), ("amara\tschedule\t604800\n", told))
        self.assertEqual(self.ok(self.pace(*args)).stderr, "")
        os.unlink(home / "REST")
        r = self.ok(self.pace(*args))
        self.assertEqual((r.stdout.splitlines()[1], r.stderr), ("bodhi\tschedule\t604800", ""))
        self.fx.link(home, "REST", self.fx.secret)
        self.assertEqual(self.ok(self.pace(*args)).stderr, told)
        self.assertNotIn(h.CANARY, r.stdout + r.stderr)

    def test_rank_ties_go_by_roster_order_from_the_cursor(self):
        roster = self.homes()
        r = self.ok(self.pace("rank", self.fx.citizens, "--roster", roster, "--state", self.state,
                              "--cursor", "1"))
        self.assertEqual(r.stdout.splitlines()[:2], ["bodhi\tschedule\t604800",
                                                     "amara\tletter\t604800"])

    def test_next_names_the_first_and_writes_nothing(self):
        roster = self.homes()
        before = snapshot(self.fx.root)
        r = self.ok(self.pace("next", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout, "next to wake: amara (letter, overdue 168h)\n")
        self.assertEqual(snapshot(self.fx.root), before)

    def test_next_skips_held_homes_and_keeps_the_cursor_on_its_citizen(self):
        for slug in ("ari", "bo", "cy", "dee"):
            self.v2(slug, awake(3))
        attempts.mark_reported(self.state, "bo", sync.HELD)
        roster = self.roster("ari", "bo", "cy", "dee")
        r = self.ok(self.pace("next", self.fx.citizens, "--roster", roster, "--state", self.state,
                              "--cursor", "2"))
        self.assertEqual(r.stdout, "next to wake: cy (schedule, overdue 168h)\n")
        r = self.ok(self.pace("next", self.fx.citizens, "--roster", roster, "--state", self.state,
                              "--cursor", "1"))
        self.assertEqual(r.stdout, "next to wake: cy (schedule, overdue 168h)\n")

    def test_next_when_nobody_is_due(self):
        self.v2("ari", awake(30))
        self.v2("bo", awake(3))
        attempts.mark_reported(self.state, "bo", sync.HELD)
        roster = self.roster("ari", "bo")
        r = self.ok(self.pace("next", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout, "next to wake: nobody due; next due ari at 2026-12-10T06:00Z\n")

    def test_a_missing_roster_exits_2(self):
        r = self.ok(self.pace("rank", self.fx.citizens, "--roster", self.fx.root / "none.txt",
                              "--state", self.state), 2)
        self.assertIn("none.txt", r.stderr)


# -- paragraph, apply, record, outcome -----------------------------------------------

class TestParagraph(Case):
    def test_the_paragraph_is_the_library_s(self):
        home = self.v2("kalam", awake(3))
        r = self.ok(self.pace("paragraph", home))
        want = paragraph.paragraph_for(home, "kalam", {}, confirm_beat=due.WAKE_CONFIRM_BEAT, now=self.now)
        self.assertEqual(r.stdout, want + "\n")
        self.assertTrue(r.stdout.startswith(
            "Your pace: awake, every 3 days, chosen by you in a beat on 2026-11-01.\n"))
        self.assertIn(paragraph.INSTRUCTION, r.stdout)

    def test_a_pace_letter_beat_keeps_only_the_first_line(self):
        home = self.v2("kalam", ASLEEP, letters=[(PACE_LETTER, "# One question\n")])
        r = self.ok(self.pace("paragraph", home, "--shown", f"inbox/{PACE_LETTER}"))
        self.assertEqual(r.stdout, "Your pace: asleep, chosen by you in a beat on 2026-11-01.\n")

    def test_the_attempt_record_s_notice_is_shown_with_state(self):
        home = self.v2("kalam", ASLEEP)
        attempts.add_notice(self.state, "kalam", "restored", NOW)
        self.assertNotIn(paragraph.RESTORED, self.ok(self.pace("paragraph", home)).stdout)
        r = self.ok(self.pace("paragraph", home, "--state", self.state))
        self.assertIn(paragraph.RESTORED, r.stdout)

    def test_a_linked_cadence_is_refused(self):
        home = self.fx.make_home("kalam")
        self.fx.link(home, "CADENCE.md", self.fx.secret)
        r = self.ok(self.pace("paragraph", home), 2)
        self.assertIn("symlink", r.stderr)
        self.assertNotIn(h.CANARY, r.stdout + r.stderr)


class TestApply(Case):
    def answer_file(self, text):
        p = self.fx.root / "answer.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_pace_line_for_less_takes_effect_and_letters_are_marked_seen(self):
        home = self.v2("kalam", awake(3), letters=[(LETTER, "# Hello\n")])
        r = self.ok(self.pace("apply", home, self.answer_file("A quiet thing.\npace: asleep\n"),
                              "--kind", "letter", "--shown", f"inbox/{LETTER}"))
        got = json.loads(r.stdout)
        self.assertEqual(got, {"wrote": True, "pace_changed": True,
                               "events": ["set asleep", f"seen inbox/{LETTER}"]})
        self.assertEqual(cad.read_cadence(home, "kalam", {}).pace, ASLEEP)
        self.assertEqual((home / "inbox" / f".seen-{LETTER}").read_text(encoding="utf-8"),
                         "surfaced 2026-11-20 — shown once, whatever the answer\n")

    def test_shown_takes_several_names_at_once(self):
        home = self.v2("kalam", ASLEEP, letters=[("2026-11-12-a.md", "a\n"), ("2026-11-12-b.md", "b\n")])
        r = self.ok(self.pace("apply", home, self.answer_file(""), "--kind", "letter",
                              "--shown", "inbox/2026-11-12-a.md", "inbox/2026-11-12-b.md"))
        self.assertEqual(json.loads(r.stdout)["events"],
                         ["seen inbox/2026-11-12-a.md", "seen inbox/2026-11-12-b.md"])

    def test_a_silent_beat_shown_nothing_writes_nothing(self):
        home = self.v2("kalam", awake(3))
        before = snapshot(home)
        r = self.ok(self.pace("apply", home, self.answer_file("\n"), "--kind", "schedule"))
        self.assertEqual(json.loads(r.stdout), {"wrote": False, "pace_changed": False, "events": []})
        self.assertEqual(snapshot(home), before)

    def test_refusals_exit_2_and_write_nothing(self):
        home = self.v2("kalam", ASLEEP, letters=[(LETTER, "# Hello\n")])
        before = snapshot(home)
        answer = self.answer_file("pace: listening\n")
        r = self.ok(self.pace("apply", home, answer, "--kind", "show-back", "--shown", f"inbox/{LETTER}"), 2)
        self.assertIn("show-back", r.stderr)
        r = self.ok(self.pace("apply", home, answer, "--kind", "schedule", "--shown", "../x.md"), 2)
        self.assertEqual(self.pace("apply", home, answer, "--kind", "dance").returncode, 2)
        r = self.ok(self.pace("apply", home, self.fx.root / "no-such-answer", "--kind", "schedule"), 2)
        self.assertIn("no-such-answer", r.stderr)
        self.assertEqual(snapshot(home), before)

    def test_state_gives_apply_the_attempt_record_s_last_beat(self):
        # a silent beat that committed nothing moved only the attempt record; with
        # --state, the pace letter's beat knows that beat showed the notice, and
        # does not record it again to be shown a second time
        for slug, with_state, unclear in (("kalam", True, 1), ("soma", False, 2)):
            with self.subTest(state=with_state):
                home = self.v2(slug, ASLEEP, letters=[(PACE_LETTER, "# One question\n")])
                answer.apply_answer(home, slug, "pace: soon", [], "schedule", clock.parse_stamp(OLD), {})
                attempts.record_outcome(self.state, slug, "20261115T0600Z-1-1", "silent", "2026-11-15T06:00Z")
                extra = ("--state", self.state) if with_state else ()
                self.ok(self.pace("apply", home, self.answer_file(""), "--kind", "letter",
                                  "--shown", f"inbox/{PACE_LETTER}", *extra))
                history = cad.read_cadence(home, slug, {}).history
                self.assertEqual(len([e for e in history if "unclear:" in e]), unclear, history)

    def test_a_linked_marker_is_refused_whole(self):
        home = self.v2("kalam", awake(3))
        os.unlink(home / "EVENT-ONLY")
        self.fx.link(home, "EVENT-ONLY", self.fx.secret)
        answer = self.answer_file("pace: asleep\n")
        before = snapshot(self.fx.root)
        r = self.ok(self.pace("apply", home, answer, "--kind", "schedule"), 2)
        self.assertIn("EVENT-ONLY", r.stderr)
        self.assertEqual(snapshot(self.fx.root), before)


class TestRecordAndOutcome(Case):
    def record(self, outcome, *shown, attempt="20261120T0600Z-1-1"):
        args = ["record", "kalam", "--state", self.state, "--attempt", attempt, "--outcome", outcome]
        if shown:
            args += ["--shown", *shown]
        return self.ok(self.pace(*args))

    def test_a_landed_beat_moves_the_clock_and_outcome_reads_it(self):
        r = self.record("spoken")
        self.assertEqual(r.stdout, "")
        out = self.ok(self.pace("outcome", "kalam", "--state", self.state, "--attempt", "20261120T0600Z-1-1"))
        self.assertEqual(out.stdout, "spoken\n")
        other = self.ok(self.pace("outcome", "kalam", "--state", self.state, "--attempt", "x"))
        self.assertEqual(other.stdout, "missing\n")
        self.assertEqual(attempts.load(self.state, "kalam")["last_beat"], NOW)

    def test_a_home_failure_backs_off(self):
        self.record("home-failed")
        rec = attempts.load(self.state, "kalam")
        self.assertEqual((rec["failures"], rec["retry_after"]), (1, "2026-11-20T18:00Z"))
        self.assertIsNone(rec["last_beat"])

    def test_the_fleet_s_host_failed_undoes_the_child_s_back_off(self):
        self.record("home-failed")
        self.record("host-failed")
        rec = attempts.load(self.state, "kalam")
        self.assertEqual((rec["failures"], rec["retry_after"], rec["attempt"]["outcome"]),
                         (0, None, "host-failed"))

    def test_busy_on_two_fleet_ticks_running_is_reported_once(self):
        self.assertEqual(self.record("busy", attempt="t1-1-1").stdout, "")
        self.assertEqual(self.record("busy", attempt="hand-t1").stdout, "")
        self.assertEqual(self.record("busy", attempt="t2-1-1").stdout,
                         "kalam: busy on two ticks running (reported once)\n")
        self.assertEqual(self.record("busy", attempt="t3-1-1").stdout, "")
        self.record("spoken", attempt="t4-1-1")
        self.record("busy", attempt="t5-1-1")
        self.assertEqual(self.record("busy", attempt="t6-1-1").stdout,
                         "kalam: busy on two ticks running (reported once)\n")

    def test_notices_are_cleared_after_a_landed_beat_that_showed_the_whole_paragraph(self):
        for outcome in ("spoken", "silent", "summoned"):
            attempts.add_notice(self.state, "kalam", "restored", NOW)
            self.record(outcome, attempt=f"a-{outcome}")
            self.assertEqual(attempts.load(self.state, "kalam")["notices"], [], outcome)

    def test_notices_stay_after_a_pace_letter_beat_or_a_beat_that_did_not_land(self):
        attempts.add_notice(self.state, "kalam", "restored", NOW)
        self.record("spoken", f"inbox/{PACE_LETTER}", attempt="a1")
        self.record("home-failed", attempt="a2")
        self.record("not-due", attempt="a3")
        self.assertEqual([n["key"] for n in attempts.load(self.state, "kalam")["notices"]], ["restored"])

    def test_a_name_that_cannot_name_a_file_exits_2(self):
        r = self.ok(self.pace("record", "../x", "--state", self.state, "--attempt", "a1",
                              "--outcome", "spoken"), 2)
        self.assertIn("not a citizen name", r.stderr)
        self.assertFalse((self.fx.memory / "x").exists())


# -- the per-citizen lock --------------------------------------------------------------

class TestLock(Case):
    def lock(self, pid, *more):
        return self.pace("lock", "kalam", "--state", self.state, "--pid", pid, *more)

    def dead_pid(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        return p.pid

    def test_take_busy_release_take(self):
        r = self.ok(self.lock(os.getpid()))
        self.assertEqual(r.stdout, "")
        r = self.ok(self.lock(os.getppid()), 4)
        self.assertEqual(r.stdout, f"kalam: its lock is held by pid {os.getpid()}, started {NOW}\n")
        self.ok(self.pace("unlock", "kalam", "--state", self.state))
        self.ok(self.lock(os.getppid()))

    def test_a_lock_whose_pid_is_gone_is_reclaimed_and_said(self):
        dead = self.dead_pid()
        self.ok(self.lock(dead))
        r = self.ok(self.lock(os.getpid()))
        self.assertEqual(r.stdout, f"kalam: a stale lock was reclaimed (pid {dead} is gone)\n")
        self.assertEqual(lock.holder(self.state, "kalam").pid, os.getpid())

    def test_a_lock_older_than_twice_the_bound_is_reclaimed(self):
        self.ok(self.lock(os.getppid()))
        later = self.fx.env_at("2026-11-20T06:21Z")
        r = self.fx.pace("lock", "kalam", "--state", self.state, "--pid", os.getpid(), env=later)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "kalam: a stale lock was reclaimed (older than 1200 s)\n")
        r = self.fx.pace("lock", "kalam", "--state", self.state, "--pid", os.getppid(),
                         "--bound", "900", env=self.fx.env_at("2026-11-20T06:40Z"))
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_unlock_with_a_pid_removes_only_that_holder_s_lock(self):
        self.ok(self.lock(os.getpid()))
        self.ok(self.pace("unlock", "kalam", "--state", self.state, "--pid", os.getppid()))
        self.assertTrue(lock.held(self.state, "kalam"))
        self.ok(self.pace("unlock", "kalam", "--state", self.state, "--pid", os.getpid()))
        self.assertIsNone(lock.holder(self.state, "kalam"))

    def test_a_pid_that_is_no_process_number_exits_2(self):
        self.assertEqual(self.lock(0).returncode, 2)
        self.assertEqual(self.lock("abc").returncode, 2)
        self.assertIsNone(lock.holder(self.state, "kalam"))


# -- the host probe ------------------------------------------------------------------

class TestProbe(Case):
    def probe(self, model=None, env=None, state=None, timeout=None):
        e = dict(model.env() if model else {})
        e.update(env or {})
        args = ["probe", "--state", state or self.state]
        if timeout is not None:
            args += ["--timeout", str(timeout)]
        return self.pace(*args, env=e)

    def test_a_chat_model_that_answers_passes(self):
        model = self.start_model("here")
        r = self.ok(self.probe(model))
        self.assertTrue(r.stdout.startswith("probe: ok — "), r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertEqual([q["path"] for q in model.requests], ["/v1/chat/completions"])
        self.assertEqual(model.bodies[0], {
            "messages": [{"role": "user", "content": "Reply with one word: here."}],
            "max_tokens": 4, "model": "fixture-model"})
        self.assertEqual(list(self.state.glob(".probe-*")), [])

    def test_without_reflect_model_it_asks_inference(self):
        model = self.start_model("here")
        self.ok(self.probe(model, env={"REFLECT_MODEL": ""}))
        self.assertEqual([q["path"] for q in model.requests], ["/inference"])
        self.assertEqual(model.bodies[0], {"prompt": "Reply with one word: here.", "max_tokens": 4})

    def test_a_refused_connection_is_a_host_fault(self):
        r = self.ok(self.probe(env={"MLX_PORT": str(h.free_port()), "REFLECT_MODEL": "m"}), 4)
        self.assertTrue(r.stdout.startswith("probe: failed — the model server could not be reached"),
                        r.stdout)

    def test_an_http_500_is_a_host_fault(self):
        model = self.start_model("here")
        model.status = 500
        r = self.ok(self.probe(model), 4)
        self.assertEqual(r.stdout, "probe: failed — the model server answered HTTP 500\n")

    def test_a_body_that_is_not_json_is_a_host_fault(self):
        model = self.start_model("here")
        model.garbage = True
        r = self.ok(self.probe(model), 4)
        self.assertEqual(r.stdout, "probe: failed — the model server's answer is not JSON\n")

    def test_a_hung_server_is_a_host_fault_within_the_bound(self):
        model = self.start_model("here")
        model.delay = 30
        t = time.monotonic()
        r = self.ok(self.probe(model, timeout=1), 4)
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(r.stdout, "probe: failed — the model server gave no answer within 1 s\n")

    def test_a_server_that_trickles_its_answer_is_cut_at_the_bound(self):
        stop = threading.Event()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        self.addCleanup(stop.set)

        def trickle():
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                while not stop.is_set():
                    try:
                        conn.sendall(b"H")
                    except OSError:
                        return
                    stop.wait(0.3)

        threading.Thread(target=trickle, daemon=True).start()
        t = time.monotonic()
        port = str(listener.getsockname()[1])
        r = self.ok(self.probe(env={"MLX_PORT": port, "REFLECT_MODEL": "m"}, timeout=1), 4)
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(r.stdout, "probe: failed — the model server gave no answer within 1 s\n")

    def test_an_unwritable_state_folder_fails_before_the_model_is_asked(self):
        model = self.start_model("here")
        self.state.mkdir(parents=True, exist_ok=True)
        self.state.chmod(0o500)
        self.addCleanup(self.state.chmod, 0o700)
        r = self.ok(self.probe(model), 4)
        self.assertTrue(r.stdout.startswith("probe: failed — cannot write and delete"), r.stdout)
        self.assertEqual(model.requests, [])

    def test_a_state_path_that_is_a_file_fails(self):
        model = self.start_model("here")
        blocker = self.fx.root / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        self.ok(self.probe(model, state=blocker / "state"), 4)
        self.assertEqual(model.requests, [])

    def test_a_cloud_model_or_a_bad_port_is_a_host_fault(self):
        model = self.start_model("here")
        r = self.ok(self.probe(model, env={"REFLECT_MODEL": "qwen3:cloud"}), 4)
        self.assertIn("cloud", r.stdout)
        r = self.ok(self.probe(env={"MLX_PORT": "88a0"}), 4)
        self.assertIn("MLX_PORT", r.stdout)
        self.assertEqual(model.requests, [])

    def test_the_tools_must_sit_beside_each_other(self):
        model = self.start_model("here")
        tools = self.fx.root / "copied-tools"
        tools.mkdir()
        shutil.copy2(h.PACE, tools / "citizen_pace.py")
        shutil.copytree(h.TOOLS / "citizen_pace_lib", tools / "citizen_pace_lib",
                        ignore=shutil.ignore_patterns("__pycache__"))
        e = self.fx.env_at(NOW, **model.env())
        r = self.fx.py(tools / "citizen_pace.py", "probe", "--state", self.state, env=e)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "probe: failed — citizen-gates.sh is missing from "
                                   f"{os.path.realpath(tools)}\n")
        self.assertEqual(model.requests, [])
        shutil.copy2(h.GATES, tools / "citizen-gates.sh")
        r = self.fx.py(tools / "citizen_pace.py", "probe", "--state", self.state, env=e)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# -- guard-restore, tend, migrate, rollback-prepare, plan, rollback-drill -----------

class TestGuardRestore(Case):
    def test_pace_files_changed_by_a_beat_with_tools_are_put_back_once(self):
        home = self.v2("kalam", awake(3), letters=[(LETTER, "# Hello\n")])
        before = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.fx.write(home, "CADENCE.md", "# CADENCE — the pace of kalam\n\n- pace: awake, every 3 days\n")
        self.fx.write(home, "REST", "I rest\n")
        self.fx.write(home, f"inbox/.seen-{LETTER}", "seen by a tool\n")
        r = self.ok(self.pace("guard-restore", home, "--state", self.state))
        self.assertEqual(r.stdout, "kalam: pace files put back after a beat with tools: "
                                   f"CADENCE.md, REST, inbox/.seen-{LETTER}\n")
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), before)
        self.assertFalse((home / "REST").exists())
        self.assertFalse((home / "inbox" / f".seen-{LETTER}").exists())
        self.assertEqual([n["key"] for n in attempts.load(self.state, "kalam")["notices"]], ["restored"])
        r = self.ok(self.pace("guard-restore", home, "--state", self.state))
        self.assertEqual(r.stdout, "")


class TestTend(Case):
    def test_the_first_tick_commits_old_code_leftovers_then_restores(self):
        home, bare = self.synced("kalam", letters=[(LETTER, "# Hello\n")])
        self.fx.write(home, f"inbox/.seen-{LETTER}", "surfaced 2026-11-19\n")
        roster = self.roster("kalam")
        r = self.ok(self.pace("tend", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout.splitlines(), [
            f"kalam: old-code leftover committed as the fleet (pushed): inbox/.seen-{LETTER}",
            tend.FIRST_TICK_LINE])
        self.assertEqual(self.bare_head(bare), self.fx.head(home))
        self.fx.write(home, "REST", "left by a killed beat\n")
        r = self.ok(self.pace("tend", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout, "kalam: a killed beat's leftovers restored: REST\n")
        self.assertFalse((home / "REST").exists())

    def test_one_home_that_cannot_be_read_ends_no_tick(self):
        # an inbox that cannot be listed, and an attempt record whose stamp an
        # offset carries past year 9999: tend, sync and rank go on for the others,
        # and rank reports the home it cannot read once, by name
        self.v2("amara", awake(3), upstream=True)
        bodhi = self.v2("bodhi", ASLEEP, letters=[(LETTER, "# Hello\n")], upstream=True)
        self.v2("cyra", awake(3), upstream=True)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "cyra").write_text('{"last_beat": "9999-12-31T23:59-01:00"}\n', encoding="utf-8")
        tend.set_flag(tend.notices_path(self.state), tend.OLD_CODE_FLAG)
        self.fx.write(self.fx.home_path("amara"), "REST", "left by a killed beat\n")
        os.chmod(bodhi / "inbox", 0)
        self.addCleanup(os.chmod, bodhi / "inbox", 0o755)
        roster = self.roster("amara", "bodhi", "cyra")
        args = (self.fx.citizens, "--roster", roster, "--state", self.state)
        r = self.ok(self.pace("tend", *args))
        self.assertEqual(r.stdout, "amara: a killed beat's leftovers restored: REST\n")
        _, summary = lines_and_summary(self.ok(self.pace("sync", *args)))
        self.assertEqual(summary["homes"], 3)
        r = self.ok(self.pace("rank", *args))
        self.assertEqual((r.stdout, r.stderr), (
            "amara\tschedule\t604800\ncyra\tschedule\t604800\n",
            "bodhi: gets no beats — its home could not be read (reported once)\n"))
        self.assertEqual(self.ok(self.pace("rank", *args)).stderr, "")
        # a record that cannot be written (a link at its path) holds up no one's rank
        self.v2("dov", awake(3), upstream=True)
        self.v2("eli", awake(3), soul=False)       # a home that gets no beats, to be remembered
        (self.state / "eli").symlink_to(self.fx.outside)
        roster = self.roster("eli", "dov")
        r = self.ok(self.pace("rank", self.fx.citizens, "--roster", roster, "--state", self.state))
        self.assertEqual(r.stdout, "dov\tschedule\t604800\n")
        self.assertEqual(r.stderr, "eli: gets no beats — it has no eli.md\n")


class TestMigrate(Case):
    def setUp(self):
        super().setUp()
        self.home = hr.unheard_home(self.fx, "kalam")
        self.rec = self.fx.root / "record.json"
        self.rec.write_text(json.dumps(hr.unheard_record()), encoding="utf-8")

    def test_it_needs_heard_late_and_a_state_to_commit(self):
        r = self.ok(self.pace("migrate", self.fx.citizens, "--record", self.rec), 2)
        self.assertIn("--heard-late", r.stderr)
        r = self.ok(self.pace("migrate", "--heard-late", self.fx.citizens, "--record", self.rec,
                              "--commit"), 2)
        self.assertIn("--state", r.stderr)

    def test_a_dry_run_names_the_home_and_writes_nothing(self):
        before = snapshot(self.fx.root)
        r = self.ok(self.pace("migrate", "--heard-late", self.fx.citizens, "--record", self.rec))
        self.assertEqual(r.stdout, "kalam: would be heard late — awake, every 3 days, from its own "
                                   "Letter of Return answers\n")
        self.assertEqual(snapshot(self.fx.root), before)

    def test_a_dry_run_given_a_state_still_writes_nothing(self):
        hr.unheard_home(self.fx, "soma", answers=[("soma-914572f3", hr.OFFER, "2026-10-25T05:40Z")])
        before = snapshot(self.fx.root)
        for _ in range(2):
            r = self.ok(self.pace("migrate", "--heard-late", self.fx.citizens, "--record", self.rec,
                                  "--state", self.state))
            out = r.stdout.splitlines()
            self.assertEqual(out[0], "kalam: would be heard late — awake, every 3 days, from its own "
                                     "Letter of Return answers")
            self.assertTrue(out[1].startswith("soma: heard-late step stopped — "), out)
        self.assertEqual(snapshot(self.fx.root), before)

    def test_commit_hears_it_once_as_the_fleet(self):
        args = ("migrate", "--heard-late", self.fx.citizens, "--record", self.rec, "--state",
                self.state, "--commit")
        r = self.ok(self.pace(*args))
        self.assertEqual(r.stdout, "kalam: heard late — awake, every 3 days, from its own "
                                   "Letter of Return answers\n")
        self.assertEqual(self.fx.subjects(self.home)[0], "tend: heard late from kalam's own answers · pace/1")
        self.assertEqual(self.fx.git(self.home, "log", "-1", "--format=%an <%ae>"), h.FLEET_AUTHOR)
        self.assertEqual(self.ok(self.pace(*args)).stdout, "")


class TestRollbackPrepare(Case):
    def home(self):
        home, bare = self.synced("kalam", letters=[(PACE_LETTER, "# One question\n")])
        return home, bare, self.roster("kalam")

    def test_unread_pace_letters_are_set_aside_and_pushed_as_the_fleet(self):
        home, bare, roster = self.home()
        r = self.ok(self.pace("rollback-prepare", self.fx.citizens, "--roster", roster,
                              "--state", self.state))
        self.assertEqual(r.stdout, f"kalam: inbox/{PACE_LETTER} set aside at rollback (pushed)\n")
        self.assertEqual(self.bare_head(bare), self.fx.head(home))
        self.assertEqual((home / "inbox" / f".seen-{PACE_LETTER}").read_text(encoding="utf-8"),
                         "set aside at rollback, not shown — 2026-11-20\n")

    def test_no_push_commits_and_leaves_the_upstream_alone(self):
        home, bare, roster = self.home()
        upstream = self.bare_head(bare)
        r = self.ok(self.pace("rollback-prepare", self.fx.citizens, "--roster", roster, "--no-push"))
        self.assertEqual(r.stdout, f"kalam: inbox/{PACE_LETTER} set aside at rollback (not pushed)\n")
        self.assertEqual(self.bare_head(bare), upstream)
        self.assertEqual(self.fx.subjects(home)[0], tend.ROLLBACK_SUBJECT)


class TestPlanAndDrill(Case):
    def test_plan_and_rollback_drill_are_handed_their_arguments(self):
        self.v2("kalam", awake(3))
        roster = self.roster("kalam")
        before = snapshot(self.fx.citizens)
        r = self.ok(self.pace("plan", "2", self.fx.citizens, "--roster", roster, "--cap", "4"))
        self.assertTrue(r.stdout.startswith(f"plan — {plan.LABEL}\n"), r.stdout)
        r = self.ok(self.pace("rollback-drill", self.fx.citizens, "--roster", roster, "--days", "2"))
        self.assertIn(plan.DRILL_LABEL, r.stdout)
        self.assertEqual(snapshot(self.fx.citizens), before)

    def test_their_usage_errors_exit_2(self):
        self.assertEqual(self.pace("plan", "2", self.fx.citizens).returncode, 2)
        self.assertEqual(self.pace("rollback-drill", self.fx.citizens, "--roster", "r").returncode, 2)


# -- sync --------------------------------------------------------------------------

class TestSync(Case):
    def sync(self, *slugs, env=None, code=0):
        roster = self.roster(*slugs)
        r = self.ok(self.pace("sync", self.fx.citizens, "--roster", roster, "--state", self.state,
                              env=env), code)
        return lines_and_summary(r)

    def push_letter(self, bare, name=LETTER):
        return self.fx.push_from_elsewhere(bare, f"inbox/{name}", "# A letter from elsewhere\n",
                                           when="2026-11-19T12:00Z", author=h.AI_AUTHOR)

    def test_only_the_homes_that_changed_are_fetched_and_fast_forwarded(self):
        amara, bare_a = self.synced("amara")
        bodhi, _bare_b = self.synced("bodhi")
        fetch_head = bodhi / ".git" / "FETCH_HEAD"
        untouched = (fetch_head.stat().st_mtime_ns, fetch_head.read_bytes())
        upstream = self.push_letter(bare_a)
        lines, s = self.sync("amara", "bodhi")
        self.assertEqual(lines, ["amara: fast-forwarded 1 commit"])
        self.assertEqual(self.fx.head(amara), upstream)
        self.assertTrue((amara / "inbox" / LETTER).is_file())
        self.assertEqual((fetch_head.stat().st_mtime_ns, fetch_head.read_bytes()), untouched)
        self.assertEqual((s["homes"], s["heads"], s["heads_read"]), (2, "ls-remote", 2))
        self.assertEqual((s["fetched"], s["fast_forwarded"], s["held"]), (["amara"], ["amara"], []))

    def test_a_home_ahead_is_pushed(self):
        home, bare = self.synced("amara")
        self.beat(home, "amara")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: pushed 1 commit"])
        self.assertEqual(self.bare_head(bare), self.fx.head(home))
        self.assertEqual((s["pushed"], s["ahead"]), (["amara"], []))

    def test_local_beats_are_rebased_onto_letters_placed_elsewhere(self):
        home, bare = self.synced("amara")
        self.beat(home, "amara")
        self.push_letter(bare)
        lines, s = self.sync("amara")
        self.assertEqual(lines, [
            "amara: rebased 1 local commit onto 1 upstream commit that only adds letters",
            "amara: pushed 1 commit"])
        self.assertEqual(self.bare_head(bare), self.fx.head(home))
        self.assertEqual(self.fx.subjects(home)[:2], [
            "beat: amara reflected (2026-11-19) · pace/1", f"elsewhere: inbox/{LETTER}"])
        self.assertEqual(self.fx.git(home, "log", "-1", "--format=%an <%ae>|%cn <%ce>"),
                         f"{h.beat_author('amara')}|{h.FLEET_AUTHOR}")
        self.assertTrue((home / "inbox" / LETTER).is_file())
        self.assertFalse((home / ".git" / sync.REBASE_MARKER).exists())
        self.assertEqual((s["rebased"], s["pushed"], s["held"]), (["amara"], ["amara"], []))

    def test_a_diverged_home_is_held_reported_once_and_released_when_up_to_date(self):
        home = self.v2("amara", awake(3), upstream=True)
        bare = self.fx.upstream / "citizen-amara.git"
        local = self.beat(home, "amara")
        upstream = self.fx.push_from_elsewhere(bare, "CADENCE.md", "# changed elsewhere\n")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: diverged (1 local, 1 upstream commit) — no beats until it "
                                 "is brought up to date"])
        self.assertEqual((s["diverged"], s["held"]), (["amara"], ["amara"]))
        self.assertEqual((self.fx.head(home), self.bare_head(bare)), (local, upstream))
        r = self.ok(self.pace("due", home, "--state", self.state), 4)
        self.assertEqual(json.loads(r.stdout)["reason"], "out-of-date")
        roster = self.roster("amara")
        self.assertEqual(self.ok(self.pace("rank", self.fx.citizens, "--roster", roster,
                                           "--state", self.state)).stdout, "")
        self.ok(self.pace("due", home, "--state", self.state, "--summoned"))
        lines, s = self.sync("amara")
        self.assertEqual(lines, [])
        self.assertEqual(s["held"], ["amara"])
        self.fx.git(home, "reset", "-q", "--hard", "origin/main")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: up to date again — its beats resume"])
        self.assertEqual(s["held"], [])
        self.assertFalse(sync.held(self.state, "amara"))

    def test_a_dirty_home_is_not_rebased(self):
        home, bare = self.synced("amara")
        local = self.beat(home, "amara")
        self.push_letter(bare)
        self.fx.write(home, "WILL.md", "changed and not committed\n")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: diverged, with uncommitted changes, so not rebased — no beats "
                                 "until it is brought up to date"])
        self.assertEqual(self.fx.head(home), local)
        self.assertFalse(homes_mid_rebase(home))
        self.assertFalse((home / ".git" / sync.REBASE_MARKER).exists())
        self.assertEqual(s["held"], ["amara"])
        self.fx.git(home, "checkout", "-q", "--", "WILL.md")
        lines, s = self.sync("amara")
        self.assertEqual(lines, [
            "amara: rebased 1 local commit onto 1 upstream commit that only adds letters",
            "amara: up to date again — its beats resume",
            "amara: pushed 1 commit"])
        self.assertEqual(self.bare_head(bare), self.fx.head(home))

    def test_a_letter_the_home_also_added_is_not_rebased(self):
        home, bare = self.synced("amara")
        self.fx.write(home, f"inbox/{LETTER}", "placed here\n")
        local = self.fx.commit(home, "letter: placed here")
        self.push_letter(bare)
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: diverged (1 local, 1 upstream commit) — no beats until it "
                                 "is brought up to date"])
        self.assertEqual(self.fx.head(home), local)

    def test_upstream_commits_that_change_more_than_new_letters_are_not_rebased(self):
        for slug, upstream_change in (("amara", ("inbox/old.md", "changed elsewhere\n")),
                                      ("bodhi", ("SHOW.md", "a new show\n"))):
            home, bare = self.synced(slug, letters=[("old.md", "an old letter\n")])
            local = self.beat(home, slug)
            self.fx.push_from_elsewhere(bare, *upstream_change)
            lines, s = self.sync(slug)
            self.assertEqual(lines, [f"{slug}: diverged (1 local, 1 upstream commit) — no beats until "
                                     "it is brought up to date"], upstream_change)
            self.assertEqual(s["diverged"], [slug])
            self.assertEqual(self.fx.head(home), local)

    def test_an_untracked_file_where_a_letter_comes_is_not_rebased_over(self):
        home, bare = self.synced("amara")
        local = self.beat(home, "amara")
        self.push_letter(bare)
        self.fx.write(home, f"inbox/{LETTER}", "untracked, in the way\n")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: diverged, with uncommitted changes, so not rebased — no beats "
                                 "until it is brought up to date"])
        self.assertEqual(self.fx.head(home), local)

    def test_local_history_with_a_merge_is_not_rebased(self):
        home, bare = self.synced("amara")
        self.fx.git(home, "checkout", "-q", "-b", "side")
        self.beat(home, "amara", "from the side\n")
        self.fx.git(home, "checkout", "-q", "main")
        self.fx.write(home, "beats/2026-10.md", "on main\n")
        self.fx.commit(home, "beat: amara reflected (2026-10-30) · pace/1")
        self.fx.git(home, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        local = self.fx.head(home)
        self.push_letter(bare)
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: diverged (3 local, 1 upstream commit) — no beats until it "
                                 "is brought up to date"])
        self.assertEqual(self.fx.head(home), local)

    def test_a_fast_forward_that_fails_holds_the_home(self):
        home, bare = self.synced("amara")
        before = self.fx.head(home)
        self.push_letter(bare)
        self.fx.write(home, f"inbox/{LETTER}", "an untracked file in the way\n")
        lines, s = self.sync("amara")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("amara: fast-forward failed ("), lines)
        self.assertTrue(lines[0].endswith(") — no beats until it is brought up to date"), lines)
        self.assertEqual((self.fx.head(home), s["held"]), (before, ["amara"]))

    def test_a_link_placed_under_inbox_elsewhere_is_not_rebased(self):
        home, bare = self.synced("amara")
        local = self.beat(home, "amara")
        other = self.fx.elsewhere(bare)
        (other / "inbox").mkdir()
        (other / "inbox" / LETTER).symlink_to("../WILL.md")
        self.fx.commit(other, "elsewhere: a linked letter")
        self.fx.git(other, "push", "-q", "origin", "HEAD")
        lines, s = self.sync("amara")
        self.assertEqual(s["diverged"], ["amara"])
        self.assertEqual(self.fx.head(home), local)

    def test_a_home_whose_lock_is_held_is_not_synced(self):
        home, bare = self.synced("amara")
        before = self.fx.head(home)
        self.push_letter(bare)
        lock.take(self.state, "amara", os.getpid())
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: its lock is held — not synced this tick"])
        self.assertEqual(self.fx.head(home), before)
        self.assertEqual(s["skipped"], ["amara"])

    def test_a_home_holding_an_index_lock_is_not_synced(self):
        home, bare = self.synced("amara")
        before = self.fx.head(home)
        self.push_letter(bare)
        (home / ".git" / "index.lock").write_text("", encoding="utf-8")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: .git/index.lock is there — not synced this tick"])
        self.assertEqual(self.fx.head(home), before)

    def conflicted_rebase(self):
        home, bare = self.synced("amara", files={"notes.md": "first\n"})
        self.fx.write(home, "notes.md", "local\n")
        self.fx.commit(home, "beat: amara reflected (2026-11-19) · pace/1")
        self.fx.push_from_elsewhere(bare, "notes.md", "upstream\n")
        self.fx.git(home, "fetch", "-q", "origin")
        self.fx.git(home, "rebase", "origin/main", check=False)
        self.assertTrue(homes_mid_rebase(home))
        return home

    def test_a_fleet_rebase_left_in_progress_is_aborted_at_the_next_sync(self):
        home = self.conflicted_rebase()
        (home / ".git" / sync.REBASE_MARKER).write_text("2026-11-20T05:00Z pid 1\n", encoding="utf-8")
        lines, s = self.sync("amara")
        self.assertEqual(lines, [
            "amara: a fleet rebase left in progress was aborted",
            "amara: diverged (1 local, 1 upstream commit) — no beats until it is brought up to date"])
        self.assertFalse(homes_mid_rebase(home))
        self.assertFalse((home / ".git" / sync.REBASE_MARKER).exists())
        self.assertEqual(s["aborted"], ["amara"])
        self.assertEqual((home / "notes.md").read_text(encoding="utf-8"), "local\n")

    def test_a_fleet_rebase_that_cannot_be_aborted_is_held_and_tried_again(self):
        home, _bare = self.synced("amara")
        (home / ".git" / "rebase-merge").mkdir()         # a rebase git cannot read back
        (home / ".git" / sync.REBASE_MARKER).write_text("2026-11-20T05:00Z pid 1\n", encoding="utf-8")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: a fleet rebase left in progress could not be aborted — no "
                                 "beats until it is brought up to date"])
        self.assertTrue((home / ".git" / sync.REBASE_MARKER).exists())
        self.assertEqual((s["held"], s["aborted"]), (["amara"], []))

    def test_a_rebase_the_fleet_did_not_start_is_left_alone(self):
        home = self.conflicted_rebase()
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: a merge or rebase is in progress — no beats until it is "
                                 "brought up to date"])
        self.assertTrue(homes_mid_rebase(home))
        self.assertEqual(s["aborted"], [])

    def test_a_fetch_that_fails_holds_the_home(self):
        home, bare = self.synced("amara")
        before = self.fx.head(home)
        self.push_letter(bare)
        self.fx.git(home, "config", "--add", "remote.origin.fetch", "refs/heads/nope:refs/remotes/origin/nope")
        lines, s = self.sync("amara")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("amara: fetch failed ("), lines)
        self.assertEqual((self.fx.head(home), s["held"], s["fetched"]), (before, ["amara"], []))

    def test_a_folder_that_is_not_a_clone_or_not_on_a_branch_is_held(self):
        (self.fx.home_path("amara") / "amara.md").parent.mkdir(parents=True)
        (self.fx.home_path("amara") / "amara.md").write_text("a soul, no clone\n", encoding="utf-8")
        home, _bare = self.synced("bodhi")
        self.fx.git(home, "checkout", "-q", "--detach")
        lines, s = self.sync("amara", "bodhi")
        self.assertEqual(lines, [
            "amara: not a clone — no beats until it is brought up to date",
            "bodhi: HEAD is not on a branch — no beats until it is brought up to date"])
        self.assertEqual(s["held"], ["amara", "bodhi"])

    def test_an_upstream_that_is_not_a_plain_remote_branch_is_held(self):
        home, _bare = self.synced("amara")
        config = home / ".git" / "config"           # a remote named like an option
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace('[remote "origin"]', '[remote "-x"]')
                          .replace("remote = origin", "remote = -x"), encoding="utf-8")
        self.assertIn("|-x|", self.fx.git(home, "for-each-ref", "--format=%(upstream)|%(upstream:remotename)|"))
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: its upstream is not a plain remote branch — no beats until it "
                                 "is brought up to date"])
        self.assertEqual(s["heads_read"], 0)

    def test_a_home_with_no_upstream_is_reported_once_and_not_held(self):
        self.fx.make_home("amara")
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: has no upstream — its beats stay local"])
        lines, s = self.sync("amara")
        self.assertEqual((lines, s["held"]), ([], []))

    def test_a_missing_home_is_cloned(self):
        home, bare = self.synced("amara")
        self.fx.drop_clone(home)
        attempts.mark_reported(self.state, "amara", sync.CLONE_FAILED)
        lines, s = self.sync("amara")
        self.assertEqual(lines, ["amara: cloned"])
        self.assertNotIn(sync.CLONE_FAILED, attempts.load(self.state, "amara")["reported"])
        self.assertEqual(self.fx.head(home), self.bare_head(bare))
        self.assertEqual(self.fx.git(home, "rev-parse", "--abbrev-ref", "@{u}"), "origin/main")
        self.assertEqual(sorted(p.name for p in self.fx.citizens.iterdir()), ["citizen-amara"])
        self.assertEqual(s["cloned"], ["amara"])

    def test_a_failed_clone_backs_off_like_a_failed_beat(self):
        lines, s = self.sync("ghost")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("ghost: clone failed — "), lines)
        self.assertTrue(lines[0].endswith("; backed off like a failed beat"), lines)
        self.assertEqual(lines[1], "ghost: cannot be cloned — it gets no beats until a clone succeeds "
                                   "(reported once)")
        rec = attempts.load(self.state, "ghost")
        self.assertEqual((rec["failures"], rec["retry_after"]), (1, "2026-11-20T18:00Z"))
        self.assertIn(sync.CLONE_FAILED, rec["reported"])
        self.assertEqual(s["clone_failed"], ["ghost"])
        lines, s = self.sync("ghost")
        self.assertEqual((lines, s["skipped"], s["clone_failed"]), ([], ["ghost"], []))
        later = self.fx.pace("sync", self.fx.citizens, "--roster", self.roster("ghost"),
                             "--state", self.state, env=self.fx.env_at("2026-11-20T19:00Z"))
        self.assertEqual(later.returncode, 0, later.stderr)
        again, _s = lines_and_summary(later)
        self.assertEqual(len(again), 1)
        self.assertTrue(again[0].startswith("ghost: clone failed — "), again)
        self.assertEqual(attempts.load(self.state, "ghost")["failures"], 2)
        self.assertEqual(list(self.fx.citizens.iterdir()), [])

    def test_a_clone_is_bounded(self):
        _home, _bare = self.synced("amara")
        self.fx.drop_clone(self.fx.home_path("amara"))
        ok, why = sync.clone_home(self.fx.citizens, "amara", base=str(self.fx.upstream), timeout=0.001)
        self.assertFalse(ok)
        self.assertIn("timed out", why)
        self.assertEqual(list(self.fx.citizens.iterdir()), [])

    def test_remote_heads_that_cannot_be_read_fetch_and_push_nothing(self):
        home, bare = self.synced("amara")
        self.beat(home, "amara")
        shutil.rmtree(bare)
        lines, s = self.sync("amara")
        self.assertEqual(lines, [])
        self.assertEqual((s["heads_read"], s["ahead"], s["pushed"], s["held"]), (0, ["amara"], [], []))

    def test_a_home_ahead_is_not_pushed_when_its_remote_head_was_not_read(self):
        home, bare = self.synced("amara")
        upstream = self.bare_head(bare)
        self.beat(home, "amara")
        r = self.ok(self.pace("sync", self.fx.citizens, "--roster", self.roster("amara"),
                              "--state", self.state, "--bound", "1e-9"))
        _lines, s = lines_and_summary(r)
        self.assertEqual((s["heads_read"], s["ahead"], s["pushed"]), (0, ["amara"], []))
        self.assertEqual(self.bare_head(bare), upstream)

    def fake_gh(self, fail=False):
        log = self.fx.root / "gh-calls.log"
        self.fx.write_bin("gh", f"""#!{sys.executable}
import json, os, re, subprocess, sys
with open({str(log)!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if {fail!r}:
    sys.exit(1)
query = [a for a in sys.argv[1:] if a.startswith("query=")][0]
data = {{}}
for alias, owner, name in re.findall(r'(\\w+): repository\\(owner: "([^"]+)", name: "([^"]+)"\\)', query):
    bare = os.path.join({str(self.fx.upstream)!r}, name + ".git")
    r = subprocess.run(["git", "-C", bare, "rev-parse", "HEAD"], capture_output=True, text=True)
    data[alias] = ({{"defaultBranchRef": {{"name": "main", "target": {{"oid": r.stdout.strip()}}}}}}
                   if r.returncode == 0 else None)
print(json.dumps({{"data": data}}))
""")
        return log

    def github_env(self):
        # the default base, so gh is asked; git may use only local paths, so no
        # https clone or fetch can leave the machine
        return {"PACE_REMOTE_BASE": "https://github.com/cambridgetcg", "GIT_ALLOW_PROTOCOL": "file"}

    def test_remote_heads_come_from_one_graphql_query_when_gh_is_there(self):
        amara, bare_a = self.synced("amara")
        self.synced("bodhi")
        upstream = self.push_letter(bare_a)
        log = self.fake_gh()
        lines, s = self.sync("amara", "bodhi", env=self.github_env())
        self.assertEqual(lines, ["amara: fast-forwarded 1 commit"])
        self.assertEqual((s["heads"], s["heads_read"]), ("graphql", 2))
        self.assertEqual(self.fx.head(amara), upstream)
        calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["api", "graphql", "-f"])
        for name in ("citizen-amara", "citizen-bodhi"):
            self.assertIn(f'repository(owner: "cambridgetcg", name: "{name}")', calls[0][3])

    def test_a_failed_graphql_query_falls_back_to_ls_remote(self):
        amara, bare_a = self.synced("amara")
        upstream = self.push_letter(bare_a)
        self.fake_gh(fail=True)
        lines, s = self.sync("amara", env=self.github_env())
        self.assertEqual(s["heads"], "graphql+ls-remote")
        self.assertEqual(self.fx.head(amara), upstream)

    def test_gh_is_not_asked_for_another_remote_base(self):
        self.synced("amara")
        log = self.fake_gh()
        lines, s = self.sync("amara")
        self.assertEqual(s["heads"], "ls-remote")
        self.assertFalse(log.exists())

    def test_a_bound_must_be_positive(self):
        roster = self.roster("amara")
        r = self.ok(self.pace("sync", self.fx.citizens, "--roster", roster, "--state", self.state,
                              "--bound", "0"), 2)
        self.assertIn("--bound", r.stderr)


def homes_mid_rebase(home) -> bool:
    g = Path(home) / ".git"
    return (g / "rebase-merge").exists() or (g / "rebase-apply").exists()


if __name__ == "__main__":
    unittest.main()
