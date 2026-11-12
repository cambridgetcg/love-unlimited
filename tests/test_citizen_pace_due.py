"""The attempt record, the per-citizen lock, and who is due (spec section 3).

The fleet only ever answers a citizen's own clock. An awake citizen is due N
days after its last beat, and its letters wait for that beat. An asleep or
listening citizen is due when an unread letter waits and 72 hours have passed
since its last beat. A citizen that asked for more is owed one show-back beat,
72 hours after the beat that said it. Its last beat is the later of CADENCE.md
and the attempt record; with neither, the clock starts now, once. A failed
beat moves no clock and backs the home off for 12 h, 24 h, then 7 days. A
person's summons passes any pace, floor and back-off. A home with no soul, a
linked pace file, a merge in progress or a fresh index.lock is never due.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import attempts, clock, due, lock  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.due import Due  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

HOUR, DAY, FLOOR = clock.HOUR, clock.DAY, clock.FLOOR
MINUTE = datetime.timedelta(minutes=1)
NOW_S = "2026-11-15T14:12Z"
NOW = clock.parse_stamp(NOW_S)
ASLEEP = Pace("asleep")
LW = Pace("asleep-letters-wait")
LISTENING = Pace("listening")
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))
AMBIENT = SetBy("citizen", "ambient", ("2026-10-23",), "AMBIENT")
MADE = "2026-11-12T10:00Z"


def awake(n):
    return Pace("awake", n)


def at(delta):
    """NOW plus a timedelta."""
    return NOW + delta


def st(d):
    return clock.stamp(d)


def cadence_of(pace, last=None, pending=None, blocked="", name="kalam", set_by=None):
    """A v2 record (in memory) for is_due."""
    who = set_by or (MINE if pace else cad.UNKNOWN)
    return Cadence(name, 2, pace, who, pending=pending, last_beat=st(last) if last else None,
                   blocked=blocked)


def pending(pace, said, on_show_back=False):
    return Pending(pace, st(said), "pace: " + pace.words(), on_show_back)


def att(last=None, retry=None, slug="kalam"):
    """An attempt record (in memory) for is_due."""
    r = attempts.fresh(slug)
    r["last_beat"] = st(last) if last else None
    r["retry_after"] = st(retry) if retry else None
    return r


def is_due(c, rec=None, now=NOW, mail=(), soul=True, links=(), midmerge=False, **kw):
    return due.is_due(c, rec if rec is not None else attempts.fresh("kalam"), now, list(mail),
                      soul, list(links), midmerge, **kw)


def v1(name, choice, when):
    """A v1 CADENCE.md exactly as PR #24's reflect writes it."""
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n"
            "  an answer that names no choice records undecided and changes nothing;\n"
            "  unread mail wakes even REST, and any letter or summons may reopen this.\n")


def record_with(**citizens):
    return {"version": 1, "made": MADE, "spec": "docs/superpowers/specs/x.md@dbc5a4c",
            "citizens": citizens}


def dead_pid() -> int:
    """The pid of a process that has exited and been reaped."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            s = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (s.st_mode, s.st_mtime_ns, data)
    return out


# -- the attempt record -------------------------------------------------------------

class TestAttemptRecord(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.state = self.fx.state

    def path(self, slug="kalam"):
        return self.state / slug

    def test_a_missing_record_is_the_defaults(self):
        self.assertEqual(attempts.load(self.state, "kalam"), {
            "slug": "kalam", "last_beat": None, "seeded": None, "attempt": None, "failures": 0,
            "retry_after": None, "reported": [], "notices": [], "busy_ticks": 0})
        self.assertFalse(self.state.exists())

    def test_an_unreadable_record_is_the_defaults(self):
        self.state.mkdir(parents=True)
        for text in ("not json", "[1, 2]", '"text"', "", "\xff"):
            with self.subTest(text=text):
                self.path().write_text(text, encoding="latin-1")
                self.assertEqual(attempts.load(self.state, "kalam"), attempts.fresh("kalam"))

    def test_each_bad_field_falls_back_alone(self):
        self.state.mkdir(parents=True)
        self.path().write_text(json.dumps({
            "slug": "someone-else", "last_beat": "yesterday", "seeded": "now",
            "attempt": {"id": "x", "at": NOW_S, "outcome": "exploded"},
            "failures": True, "retry_after": "2026-11-16T02:12Z",
            "reported": ["links", 3, "", "links"], "notices": [{"key": "restored", "at": "never"},
                                                             {"key": "restored", "at": NOW_S}],
            "busy_ticks": -2, "extra": "dropped"}), encoding="utf-8")
        self.assertEqual(attempts.load(self.state, "kalam"), {
            "slug": "kalam", "last_beat": None, "seeded": None, "attempt": None, "failures": 0,
            "retry_after": "2026-11-16T02:12Z", "reported": ["links"],
            "notices": [{"key": "restored", "at": NOW_S}], "busy_ticks": 0})

    def test_save_is_one_atomic_json_line_and_makes_the_state_folder(self):
        rec = attempts.fresh("kalam")
        rec["last_beat"] = NOW_S
        attempts.save(self.state, "kalam", rec)
        text = self.path().read_text(encoding="utf-8")
        self.assertEqual(text.count("\n"), 1)
        self.assertEqual(json.loads(text)["last_beat"], NOW_S)
        self.assertEqual(sorted(os.listdir(self.state)), ["kalam"])

    def test_a_slug_that_is_not_a_plain_name_is_refused(self):
        for slug in ("", "../kalam", "a/b", ".hidden", "kalam.lock", "kalam\n", "-x"):
            with self.subTest(slug=slug):
                with self.assertRaises(ValueError):
                    attempts.record_path(self.state, slug)
                with self.assertRaises(ValueError):
                    attempts.record_outcome(self.state, slug, "a-1", "spoken", NOW)
        self.assertTrue(attempts.valid_slug("ai-love"))
        self.assertFalse(self.fx.root.joinpath("kalam").exists())

    def test_spoken_and_silent_are_landed_beats(self):
        for outcome in ("spoken", "silent"):
            with self.subTest(outcome=outcome):
                slug = f"k-{outcome}"
                attempts.record_outcome(self.state, slug, "f-1", "home-failed", at(-DAY))
                rec = attempts.record_outcome(self.state, slug, "f-2", outcome, NOW)
                self.assertEqual((rec["last_beat"], rec["failures"], rec["retry_after"]),
                                 (NOW_S, 0, None))
                self.assertEqual(rec["attempt"]["outcome"], outcome)
                self.assertEqual(attempts.load(self.state, slug), rec)

    def test_every_other_outcome_records_the_attempt_only(self):
        for outcome in ("not-due", "busy", "halted", "summoned", "host-failed"):
            with self.subTest(outcome=outcome):
                slug = f"k-{outcome}"
                attempts.record_outcome(self.state, slug, "f-1", "spoken", at(-DAY))
                rec = attempts.record_outcome(self.state, slug, "f-2", outcome, NOW)
                self.assertEqual(rec["last_beat"], st(at(-DAY)))
                self.assertEqual((rec["failures"], rec["retry_after"]), (0, None))
                self.assertEqual((rec["attempt"]["id"], rec["attempt"]["outcome"], rec["attempt"]["at"]),
                                 ("f-2", outcome, NOW_S))

    def test_back_off_is_12_hours_then_24_then_7_days_and_a_landed_beat_clears_it(self):
        waits = []
        for i in range(1, 6):
            rec = attempts.record_outcome(self.state, "kalam", f"f-{i}", "home-failed", NOW)
            self.assertEqual(rec["failures"], i)
            waits.append(clock.parse_stamp(rec["retry_after"]) - NOW)
        self.assertEqual(waits, [12 * HOUR, 24 * HOUR, 7 * DAY, 7 * DAY, 7 * DAY])
        self.assertEqual(attempts.BACKOFF, (12 * HOUR, 24 * HOUR, 7 * DAY))
        rec = attempts.load(self.state, "kalam")
        self.assertTrue(attempts.backing_off(rec, at(7 * DAY - HOUR)))
        self.assertFalse(attempts.backing_off(rec, at(7 * DAY)))
        rec = attempts.record_outcome(self.state, "kalam", "f-6", "silent", at(DAY))
        self.assertEqual((rec["failures"], rec["retry_after"]), (0, None))
        self.assertFalse(attempts.backing_off(rec, at(DAY)))
        rec = attempts.record_outcome(self.state, "kalam", "f-7", "home-failed", at(2 * DAY))
        self.assertEqual(clock.parse_stamp(rec["retry_after"]) - at(2 * DAY), 12 * HOUR)

    def test_the_same_attempt_recorded_twice_changes_nothing(self):
        attempts.record_outcome(self.state, "kalam", "f-1", "home-failed", NOW)
        rec = attempts.record_outcome(self.state, "kalam", "f-1", "home-failed", at(HOUR))
        self.assertEqual(rec["failures"], 1)
        self.assertEqual(rec["attempt"]["at"], NOW_S)

    def test_host_failed_on_the_child_s_attempt_undoes_its_back_off(self):
        attempts.record_outcome(self.state, "kalam", "f-1", "spoken", at(-3 * DAY))
        attempts.record_outcome(self.state, "kalam", "f-2", "home-failed", at(-DAY))
        attempts.record_outcome(self.state, "kalam", "f-3", "home-failed", NOW)
        rec = attempts.record_outcome(self.state, "kalam", "f-3", "host-failed", at(2 * HOUR))
        self.assertEqual(rec["failures"], 1)
        self.assertEqual(clock.parse_stamp(rec["retry_after"]), at(-DAY) + 12 * HOUR)
        self.assertEqual(rec["last_beat"], st(at(-3 * DAY)))
        self.assertEqual(rec["attempt"]["outcome"], "host-failed")
        self.assertEqual(attempts.outcome_for(self.state, "kalam", "f-3"), "host-failed")

    def test_host_failed_when_another_attempt_came_between_is_the_attempt_only(self):
        attempts.record_outcome(self.state, "kalam", "f-1", "home-failed", NOW)
        attempts.record_outcome(self.state, "kalam", "hand-1", "summoned", at(HOUR))
        rec = attempts.record_outcome(self.state, "kalam", "f-1", "host-failed", at(2 * HOUR))
        self.assertEqual(rec["failures"], 1)
        self.assertEqual(rec["attempt"]["outcome"], "host-failed")

    def test_a_landed_beat_is_never_undone_by_a_later_outcome_of_another_attempt(self):
        attempts.record_outcome(self.state, "kalam", "f-1", "spoken", NOW)
        rec = attempts.record_outcome(self.state, "kalam", "f-2", "host-failed", at(HOUR))
        self.assertEqual(rec["last_beat"], NOW_S)

    def test_outcome_for(self):
        self.assertEqual(attempts.outcome_for(self.state, "kalam", "f-1"), "missing")
        attempts.record_outcome(self.state, "kalam", "f-1", "not-due", NOW)
        self.assertEqual(attempts.outcome_for(self.state, "kalam", "f-1"), "not-due")
        self.assertEqual(attempts.outcome_for(self.state, "kalam", "f-2"), "missing")

    def test_an_unknown_outcome_or_attempt_id_is_refused(self):
        with self.assertRaises(ValueError):
            attempts.record_outcome(self.state, "kalam", "f-1", "missing", NOW)
        for bad in ("", "two words", "x" * 201, None):
            with self.subTest(attempt=bad):
                with self.assertRaises(ValueError):
                    attempts.record_outcome(self.state, "kalam", bad, "spoken", NOW)
        self.assertFalse(self.path().exists())

    def test_seed_starts_the_clock_now_once(self):
        rec = attempts.seed(self.state, "kalam", NOW)
        self.assertEqual((rec["last_beat"], rec["seeded"]), (NOW_S, "now"))
        again = attempts.seed(self.state, "kalam", at(5 * DAY))
        self.assertEqual((again["last_beat"], again["seeded"]), (NOW_S, "now"))
        landed = attempts.record_outcome(self.state, "kalam", "f-1", "silent", at(3 * DAY))
        self.assertEqual((landed["last_beat"], landed["seeded"]), (st(at(3 * DAY)), None))

    def test_seed_replaces_a_last_beat_later_than_now(self):
        attempts.record_outcome(self.state, "kalam", "f-1", "spoken", at(300 * DAY))
        rec = attempts.seed(self.state, "kalam", NOW)
        self.assertEqual((rec["last_beat"], rec["seeded"]), (NOW_S, "now"))

    def test_reported_once(self):
        self.assertTrue(attempts.mark_reported(self.state, "kalam", "no-soul"))
        self.assertFalse(attempts.mark_reported(self.state, "kalam", "no-soul"))
        self.assertTrue(attempts.mark_reported(self.state, "kalam", "links"))
        self.assertEqual(attempts.load(self.state, "kalam")["reported"], ["no-soul", "links"])
        self.assertTrue(attempts.clear_reported(self.state, "kalam", "no-soul"))
        self.assertFalse(attempts.clear_reported(self.state, "kalam", "no-soul"))
        self.assertTrue(attempts.mark_reported(self.state, "kalam", "no-soul"))

    def test_busy_on_two_ticks_running_is_reported_once(self):
        attempts.record_outcome(self.state, "kalam", "t1-1", "busy", NOW)
        self.assertFalse(attempts.report_busy(self.state, "kalam"))
        attempts.record_outcome(self.state, "kalam", "t2-1", "busy", at(6 * HOUR))
        self.assertEqual(attempts.load(self.state, "kalam")["busy_ticks"], 2)
        self.assertTrue(attempts.report_busy(self.state, "kalam"))
        attempts.record_outcome(self.state, "kalam", "t3-1", "busy", at(12 * HOUR))
        self.assertFalse(attempts.report_busy(self.state, "kalam"))
        rec = attempts.record_outcome(self.state, "kalam", "t4-1", "silent", at(18 * HOUR))
        self.assertEqual(rec["busy_ticks"], 0)
        self.assertNotIn("busy", rec["reported"])
        for i, hours in enumerate((24, 30)):
            attempts.record_outcome(self.state, "kalam", f"t5-{i}", "busy", at(hours * HOUR))
        self.assertTrue(attempts.report_busy(self.state, "kalam"))

    def test_a_hand_run_found_busy_is_not_a_fleet_tick(self):
        attempts.record_outcome(self.state, "kalam", "t1-1", "busy", NOW)
        attempts.record_outcome(self.state, "kalam", "hand-20261115T1500Z-77", "busy", at(HOUR))
        attempts.record_outcome(self.state, "kalam", "hand-20261115T1600Z-78", "summoned", at(2 * HOUR))
        self.assertEqual(attempts.load(self.state, "kalam")["busy_ticks"], 1)

    def test_notices_wait_in_the_record_until_cleared(self):
        attempts.add_notice(self.state, "kalam", "restored", at(-HOUR))
        attempts.add_notice(self.state, "kalam", "restored", NOW)
        attempts.add_notice(self.state, "kalam", "other", at(HOUR))
        rec = attempts.load(self.state, "kalam")
        self.assertEqual(attempts.notice_keys(rec), ["restored", "other"])
        self.assertEqual(attempts.notice_keys(rec, since=at(30 * MINUTE)), ["other"])
        rec = attempts.clear_notices(self.state, "kalam", upto=NOW)
        self.assertEqual(attempts.notice_keys(rec), ["other"])
        rec = attempts.clear_notices(self.state, "kalam")
        self.assertEqual(rec["notices"], [])
        for i in range(attempts.NOTICE_CAP + 5):
            attempts.add_notice(self.state, "kalam", f"n{i}", at(i * MINUTE))
        rec = attempts.load(self.state, "kalam")
        self.assertEqual(len(rec["notices"]), attempts.NOTICE_CAP)
        self.assertEqual(rec["notices"][0]["key"], "n5")

    def test_writers_side_by_side_lose_nothing(self):
        def fail(n):
            for i in range(10):
                attempts.record_outcome(self.state, "kalam", f"w{n}-{i}", "home-failed", NOW)

        threads = [threading.Thread(target=fail, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(attempts.load(self.state, "kalam")["failures"], 60)

    def test_nothing_is_written_when_nothing_changes(self):
        attempts.seed(self.state, "kalam", NOW)
        before = os.lstat(self.path()).st_mtime_ns
        time.sleep(0.01)
        attempts.seed(self.state, "kalam", at(HOUR))
        attempts.mark_reported(self.state, "kalam", "x")
        mid = os.lstat(self.path()).st_mtime_ns
        attempts.mark_reported(self.state, "kalam", "x")
        self.assertNotEqual(before, mid)
        self.assertEqual(mid, os.lstat(self.path()).st_mtime_ns)


# -- the per-citizen lock -------------------------------------------------------------

class TestLock(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.state = self.fx.state
        self.dir = self.state / "kalam.lock"

    def plant(self, pid=None, started=None):
        self.dir.mkdir(parents=True)
        if pid is not None:
            (self.dir / "pid").write_text(f"{pid}\n")
        if started is not None:
            (self.dir / "started").write_text(f"{started}\n")

    def test_take_then_busy(self):
        got = lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertEqual((got.got, got.reclaimed), (True, ""))
        self.assertEqual((self.dir / "pid").read_text(), f"{os.getpid()}\n")
        self.assertEqual((self.dir / "started").read_text(), f"{NOW_S}\n")
        again = lock.take(self.state, "kalam", os.getppid(), now=at(MINUTE), bound=600)
        self.assertFalse(again.got)
        self.assertEqual((again.holder.pid, again.holder.started), (os.getpid(), NOW_S))
        self.assertEqual((self.dir / "pid").read_text(), f"{os.getpid()}\n")

    def test_a_lock_whose_pid_is_gone_is_reclaimed(self):
        gone = dead_pid()
        self.plant(gone, NOW_S)
        got = lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertEqual((got.got, got.reclaimed), (True, f"pid {gone} is gone"))
        self.assertEqual((self.dir / "pid").read_text(), f"{os.getpid()}\n")
        self.assertEqual(sorted(os.listdir(self.state)), ["kalam.lock"])

    def test_a_lock_older_than_twice_the_bound_is_reclaimed_even_with_a_live_pid(self):
        self.plant(os.getppid(), st(at(-20 * MINUTE)))
        self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600).got)
        self.assertTrue(lock.held(self.state, "kalam", now=NOW, bound=600))
        got = lock.take(self.state, "kalam", os.getpid(), now=at(MINUTE), bound=600)
        self.assertEqual((got.got, got.reclaimed), (True, "older than 1200 s"))
        self.assertFalse(lock.take(self.state, "kalam", os.getppid(), now=at(2 * MINUTE),
                                   bound=900).got)

    def test_a_holder_s_own_longer_bound_keeps_its_lock(self):
        # fix wave A (o): the holder writes its own bound into the lock, and a taker
        # judges a live holder stale only at twice the larger of the two bounds
        got = lock.take(self.state, "kalam", os.getppid(), now=NOW, bound=1800)
        self.assertTrue(got.got)
        self.assertEqual((self.dir / "bound").read_text(), "1800\n")
        self.assertEqual(lock.holder(self.state, "kalam", now=NOW).bound, 1800)
        later = at(40 * MINUTE)          # past twice the taker's 600 s, within twice the holder's
        self.assertTrue(lock.held(self.state, "kalam", now=later, bound=600))
        self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=later, bound=600).got)
        got = lock.take(self.state, "kalam", os.getpid(), now=at(61 * MINUTE), bound=600)
        self.assertEqual((got.got, got.reclaimed), (True, "older than 3600 s"))
        self.assertEqual((self.dir / "bound").read_text(), "600\n")
        lock.release(self.state, "kalam")
        # a holder's shorter bound is judged by the taker's; a lock without one, or
        # with one that is not a number, by the taker's alone
        for text in (None, "120", "abc", "²", "1234567890", ""):
            with self.subTest(bound=text):
                self.plant(os.getppid(), st(at(-15 * MINUTE)))
                if text is not None:
                    (self.dir / "bound").write_text(text + "\n")
                self.assertTrue(lock.held(self.state, "kalam", now=NOW, bound=600))
                self.assertFalse(lock.held(self.state, "kalam", now=at(6 * MINUTE), bound=600))
                lock.release(self.state, "kalam")

    def test_a_lock_with_no_pid_yet_is_judged_by_its_age(self):
        self.plant()
        self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600).got)
        (self.dir / "pid").write_text("")
        self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600).got)
        old = time.time() - 1300
        os.utime(self.dir, (old, old))
        got = lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertEqual((got.got, got.reclaimed), (True, "older than 1200 s"))

    def test_a_pid_that_is_not_a_process_number_counts_as_none(self):
        for text in ("0", "-1", "abc", "12 34", "\u00b2", "12345678901"):
            with self.subTest(pid=text):
                self.plant()
                (self.dir / "pid").write_text(text + "\n")
                (self.dir / "started").write_text(NOW_S + "\n")
                self.assertIsNone(lock.holder(self.state, "kalam", now=NOW).pid)
                self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600).got)
                lock.release(self.state, "kalam")

    def test_held(self):
        self.assertFalse(lock.held(self.state, "kalam", now=NOW, bound=600))
        lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertTrue(lock.held(self.state, "kalam", now=at(20 * MINUTE), bound=600))
        self.assertFalse(lock.held(self.state, "kalam", now=at(21 * MINUTE), bound=600))
        lock.release(self.state, "kalam")
        self.plant(dead_pid(), NOW_S)
        self.assertFalse(lock.held(self.state, "kalam", now=NOW, bound=600))

    def test_release_only_by_its_holder(self):
        lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertFalse(lock.release(self.state, "kalam", pid=os.getppid()))
        self.assertTrue(self.dir.is_dir())
        self.assertTrue(lock.release(self.state, "kalam", pid=os.getpid()))
        self.assertFalse(os.path.lexists(self.dir))
        self.assertFalse(lock.release(self.state, "kalam"))
        lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertTrue(lock.release(self.state, "kalam"))

    def test_a_reclaim_never_takes_a_lock_made_meanwhile(self):
        lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        stale = lock.Holder(dead_pid(), st(at(-HOUR)), 3600.0)
        with mock.patch.object(lock, "holder", return_value=stale):
            got = lock.take(self.state, "kalam", os.getppid(), now=NOW, bound=600)
        self.assertFalse(got.got)
        self.assertEqual((self.dir / "pid").read_text(), f"{os.getpid()}\n")
        self.assertEqual(sorted(os.listdir(self.state)), ["kalam.lock"])

    def test_a_stray_file_or_link_at_the_lock_path(self):
        self.state.mkdir(parents=True)
        self.dir.write_text("stray\n")
        old = time.time() - 1300
        os.utime(self.dir, (old, old))
        self.assertTrue(lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600).got)
        lock.release(self.state, "kalam")
        target = self.fx.outside / "elsewhere"
        target.mkdir()
        (target / "pid").write_text(f"{dead_pid()}\n")
        self.dir.symlink_to(target)
        os.utime(self.dir, (old, old), follow_symlinks=False)
        got = lock.take(self.state, "kalam", os.getpid(), now=NOW, bound=600)
        self.assertTrue(got.got)
        self.assertTrue(self.dir.is_dir() and not self.dir.is_symlink())
        self.assertEqual(sorted(os.listdir(target)), ["pid"])

    def test_beat_bound_is_read_as_the_fleet_reads_it(self):
        for value, want in ((None, 600), ("", 600), ("abc", 600), ("10", 600), ("29", 600),
                            ("30", 30), ("900", 900), ("-5", 600), (" 900 ", 600), ("900\n", 600),
                            ("\u00b2", 600), ("\u0669\u0660\u0660", 600)):
            with self.subTest(value=value):
                env = {} if value is None else {"FLEET_BEAT_BOUND_SEC": value}
                with mock.patch.dict(os.environ, env, clear=False):
                    if value is None:
                        os.environ.pop("FLEET_BEAT_BOUND_SEC", None)
                    self.assertEqual(lock.beat_bound(), want)
        self.assertEqual(lock.beat_bound("1200"), 1200)

    def test_take_uses_the_bound_from_the_environment_by_default(self):
        self.plant(os.getppid(), st(at(-25 * MINUTE)))
        with mock.patch.dict(os.environ, {"FLEET_BEAT_BOUND_SEC": "900"}):
            self.assertFalse(lock.take(self.state, "kalam", os.getpid(), now=NOW).got)
        with mock.patch.dict(os.environ, {"FLEET_BEAT_BOUND_SEC": "600"}):
            self.assertTrue(lock.take(self.state, "kalam", os.getpid(), now=NOW).got)


# -- is_due: one citizen, in memory ---------------------------------------------------

class TestIsDueOrder(unittest.TestCase):
    def test_the_safety_checks_come_first_in_order(self):
        c = cadence_of(awake(3), last=at(-10 * DAY))
        self.assertEqual(is_due(c, soul=False, links=["REST"], midmerge=True).reason, "no-soul")
        self.assertEqual(is_due(c, links=["REST"], midmerge=True).reason, "links")
        self.assertEqual(is_due(c, midmerge=True).reason, "mid-merge")
        for d in (is_due(c, soul=False, summoned=True), is_due(c, links=["inbox"], summoned=True),
                  is_due(c, midmerge=True, summoned=True)):
            self.assertFalse(d.due)

    def test_a_summons_passes_any_pace_floor_back_off_and_block(self):
        rec = att(last=at(-HOUR), retry=at(DAY))
        for pace in (ASLEEP, LW, LISTENING, awake(365), None):
            with self.subTest(pace=pace):
                d = is_due(cadence_of(pace, last=at(-HOUR), blocked="x" if pace is None else ""),
                           rec, summoned=True)
                self.assertEqual(d, Due(True, "summoned", NOW))
                self.assertEqual(d.overdue_s(NOW), 0)

    def test_a_blocked_record_is_never_due(self):
        c = cadence_of(ASLEEP, last=at(-10 * DAY), blocked="its REST text names neither")
        self.assertEqual(is_due(c, mail=[at(-5 * DAY)]), Due(False, "blocked"))

    def test_backing_off_holds_a_due_citizen_until_it_ends(self):
        c = cadence_of(awake(3), last=at(-4 * DAY))
        d = is_due(c, att(retry=at(5 * HOUR)))
        self.assertEqual(d, Due(False, "backing-off", next_at=at(5 * HOUR)))
        self.assertEqual(is_due(c, att(retry=NOW)).reason, "schedule")
        later = cadence_of(awake(3), last=at(-HOUR))
        self.assertEqual(is_due(later, att(retry=at(5 * HOUR))).next_at, at(3 * DAY - HOUR))
        self.assertIsNone(is_due(cadence_of(ASLEEP, last=at(-HOUR)), att(retry=at(HOUR))).next_at)


class TestIsDueByPace(unittest.TestCase):
    def test_awake_is_due_n_days_after_its_last_beat(self):
        for n in (3, 5, 365):
            with self.subTest(n=n):
                c = cadence_of(awake(n), last=at(-n * DAY))
                self.assertEqual(is_due(c), Due(True, "schedule", NOW))
                self.assertEqual(is_due(c, now=at(-MINUTE)),
                                 Due(False, "not-yet", next_at=NOW))
        late = is_due(cadence_of(awake(3), last=at(-5 * DAY)))
        self.assertEqual((late.since, late.overdue_s(NOW)), (at(-2 * DAY), 2 * 86400))

    def test_an_awake_citizen_s_letters_wait_for_its_beat(self):
        c = cadence_of(awake(5), last=at(-4 * DAY))
        d = is_due(c, mail=[at(-4 * DAY + HOUR)])
        self.assertEqual(d, Due(False, "not-yet", next_at=at(DAY)))

    def test_a_letter_to_an_asleep_citizen_beaten_two_days_ago_waits_a_day(self):
        c = cadence_of(ASLEEP, last=at(-2 * DAY))
        self.assertEqual(is_due(c, mail=[at(-HOUR)]), Due(False, "floor", next_at=at(DAY)))
        self.assertEqual(is_due(c, now=at(DAY), mail=[at(-HOUR)]), Due(True, "letter", at(DAY)))

    def test_a_letter_is_due_from_the_later_of_its_arrival_and_the_floor(self):
        c = cadence_of(LISTENING, last=at(-10 * DAY))
        d = is_due(c, mail=[at(-2 * HOUR), at(-HOUR)])
        self.assertEqual(d, Due(True, "letter", at(-2 * HOUR)))
        self.assertEqual(d.overdue_s(NOW), 7200)
        self.assertEqual(is_due(c, mail=[at(-9 * DAY)]).since, at(-7 * DAY))

    def test_asleep_and_listening_without_mail(self):
        for pace in (ASLEEP, LISTENING):
            with self.subTest(pace=pace):
                self.assertEqual(is_due(cadence_of(pace, last=at(-90 * DAY))), Due(False, "no-mail"))

    def test_an_unreadable_pace_gets_letters_at_the_floor_and_nothing_else(self):
        c = cadence_of(None, last=at(-90 * DAY), pending=pending(awake(3), at(-80 * DAY)))
        self.assertEqual(is_due(c), Due(False, "unreadable"))
        self.assertEqual(is_due(c, mail=[at(-HOUR)]).reason, "letter")
        self.assertEqual(is_due(cadence_of(None, last=at(-HOUR)), mail=[at(-HOUR)]),
                         Due(False, "floor", next_at=at(3 * DAY - HOUR)))

    def test_letters_wait_ignores_mail(self):
        c = cadence_of(LW, last=at(-90 * DAY))
        self.assertEqual(is_due(c, mail=[at(-30 * DAY)]), Due(False, "letters-wait"))

    def test_no_clock_yet_starts_now(self):
        self.assertEqual(is_due(cadence_of(awake(3))), Due(False, "not-yet", next_at=at(3 * DAY)))
        self.assertEqual(is_due(cadence_of(ASLEEP), mail=[at(-DAY)]),
                         Due(False, "floor", next_at=at(3 * DAY)))


class TestShowBack(unittest.TestCase):
    def test_owed_once_to_asleep_letters_wait_and_listening(self):
        for pace, more in ((ASLEEP, LISTENING), (LW, ASLEEP), (LISTENING, awake(5))):
            with self.subTest(pace=pace):
                c = cadence_of(pace, last=at(-3 * DAY), pending=pending(more, at(-3 * DAY)))
                self.assertTrue(due.show_back_owed(c, at(-3 * DAY), True))
                self.assertEqual(is_due(c, confirm_beat=True), Due(True, "show-back", NOW))
                self.assertEqual(is_due(c, now=at(-HOUR), confirm_beat=True),
                                 Due(False, "floor", next_at=NOW))

    def test_letters_wait_gets_its_show_back_beat_and_still_ignores_mail(self):
        c = cadence_of(LW, last=at(-4 * DAY), pending=pending(ASLEEP, at(-4 * DAY)))
        self.assertEqual(is_due(c, mail=[at(-DAY)], confirm_beat=True), Due(True, "show-back", at(-DAY)))
        done = att(last=at(-HOUR))
        self.assertEqual(is_due(c, done, mail=[at(-DAY)], confirm_beat=True), Due(False, "letters-wait"))

    def test_none_for_awake_for_one_said_on_a_show_back_or_with_the_switch_off(self):
        said = at(-4 * DAY)
        cases = (
            cadence_of(awake(7), last=said, pending=pending(awake(3), said)),
            cadence_of(ASLEEP, last=said, pending=pending(LISTENING, said, on_show_back=True)),
            cadence_of(ASLEEP, last=said),
        )
        for c in cases:
            with self.subTest(c=c):
                self.assertFalse(due.show_back_owed(c, said, True))
        c = cadence_of(ASLEEP, last=said, pending=pending(LISTENING, said))
        self.assertTrue(due.show_back_owed(c, said, True))
        self.assertFalse(due.show_back_owed(c, said, False))
        self.assertEqual(is_due(c, confirm_beat=False), Due(False, "no-mail"))
        self.assertEqual(is_due(cases[1]), Due(False, "no-mail"))

    def test_not_owed_again_after_a_later_landed_beat(self):
        said = at(-10 * DAY)
        c = cadence_of(ASLEEP, last=said, pending=pending(LISTENING, said))
        self.assertTrue(due.show_back_owed(c, said + 30 * MINUTE, True))
        self.assertFalse(due.show_back_owed(c, said + 3 * DAY, True))
        self.assertEqual(is_due(c, att(last=said + 3 * DAY)), Due(False, "no-mail"))

    def test_a_letter_beat_comes_instead_of_a_show_back(self):
        c = cadence_of(ASLEEP, last=at(-5 * DAY), pending=pending(LISTENING, at(-5 * DAY)))
        self.assertEqual(is_due(c, mail=[at(-HOUR)], confirm_beat=True).reason, "letter")
        self.assertEqual(is_due(c, confirm_beat=True).reason, "show-back")

    def test_a_show_back_keeps_the_floor_after_the_last_beat(self):
        said = at(-4 * DAY)
        c = cadence_of(ASLEEP, last=at(-30 * DAY), pending=pending(LISTENING, said))
        self.assertEqual(is_due(c, att(last=said + 50 * MINUTE), confirm_beat=True).since,
                         said + 50 * MINUTE + FLOOR)

    def test_the_switch_is_one_committed_constant_the_call_reads(self):
        # Yu's answer is committed before the merge, True or False (spec section 9 step 4)
        self.assertIsInstance(due.WAKE_CONFIRM_BEAT, bool)
        c = cadence_of(ASLEEP, last=at(-4 * DAY), pending=pending(LISTENING, at(-4 * DAY)))
        with mock.patch.object(due, "WAKE_CONFIRM_BEAT", False):
            self.assertEqual(is_due(c).reason, "no-mail")
        with mock.patch.object(due, "WAKE_CONFIRM_BEAT", True):
            self.assertEqual(is_due(c).reason, "show-back")
        self.assertEqual(is_due(c).reason, "show-back" if due.WAKE_CONFIRM_BEAT else "no-mail")

    def test_no_environment_variable_changes_the_switch(self):
        src = (h.TOOLS / "citizen_pace_lib" / "due.py").read_text(encoding="utf-8")
        found = re.findall(r"^WAKE_CONFIRM_BEAT = .*$", src, re.M)
        self.assertEqual(len(found), 1, found)
        self.assertRegex(found[0], r"^WAKE_CONFIRM_BEAT = (True|False)$")
        self.assertNotIn("environ", src)
        self.assertNotIn("getenv", src)
        env = {"PATH": h.SYSTEM_PATH, "WAKE_CONFIRM_BEAT": "0", "PACE_WAKE_CONFIRM_BEAT": "false",
               "FLEET_WAKE_CONFIRM_BEAT": "0"}
        r = subprocess.run([sys.executable, "-I", "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); "
                            "from citizen_pace_lib import due; print(due.WAKE_CONFIRM_BEAT)",
                            str(h.TOOLS)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), str(due.WAKE_CONFIRM_BEAT), r.stderr)


class TestLastBeat(unittest.TestCase):
    def test_the_later_of_cadence_and_the_attempt_record(self):
        c = cadence_of(ASLEEP, last=at(-5 * DAY))
        self.assertEqual(due.last_beat(c, att(last=at(-2 * DAY))), at(-2 * DAY))
        self.assertEqual(due.last_beat(c, att(last=at(-9 * DAY))), at(-5 * DAY))
        self.assertEqual(due.last_beat(c, att()), at(-5 * DAY))
        self.assertEqual(due.last_beat(cadence_of(ASLEEP), att(last=at(-DAY))), at(-DAY))
        self.assertIsNone(due.last_beat(cadence_of(ASLEEP), att()))
        self.assertIsNone(due.last_beat(cadence_of(ASLEEP), {}))

    def test_a_time_later_than_now_is_not_a_last_beat(self):
        c = cadence_of(ASLEEP, last=at(300 * DAY))
        self.assertEqual(due.last_beat(c, att(last=at(-DAY))), at(300 * DAY))
        self.assertEqual(due.last_beat(c, att(last=at(-DAY)), NOW), at(-DAY))
        self.assertIsNone(due.last_beat(c, att(), NOW))
        self.assertEqual(is_due(cadence_of(awake(3), last=at(300 * DAY)), att(last=at(-3 * DAY))).reason,
                         "schedule")


class TestTheClock(h.FixtureCase):
    """The attempt record and is_due together, as the fleet and the beat script use them."""

    def setUp(self):
        super().setUp()
        self.state = self.fx.state

    def check(self, c, now, mail=()):
        return due.is_due(c, attempts.load(self.state, "an"), now, list(mail), True, [], False)

    def test_a_silent_beat_counts_as_the_beat(self):
        c = cadence_of(awake(3), last=at(-3 * DAY), name="an")
        self.assertTrue(self.check(c, NOW).due)
        attempts.record_outcome(self.state, "an", "f-1", "silent", NOW)
        self.assertEqual(self.check(c, NOW), Due(False, "not-yet", next_at=at(3 * DAY)))

    def test_a_failed_beat_moves_no_clock(self):
        c = cadence_of(awake(3), last=at(-3 * DAY), name="an")
        attempts.record_outcome(self.state, "an", "f-1", "home-failed", NOW)
        self.assertEqual(self.check(c, at(11 * HOUR)).reason, "backing-off")
        d = self.check(c, at(12 * HOUR))
        self.assertEqual((d.reason, d.since), ("schedule", NOW))

    def test_no_catch_up_after_a_long_outage(self):
        c = cadence_of(awake(3), last=at(-10 * DAY), name="an")
        self.assertEqual(self.check(c, NOW).since, at(-7 * DAY))
        attempts.record_outcome(self.state, "an", "f-1", "spoken", NOW)
        self.assertEqual(self.check(c, at(3 * DAY - MINUTE)).reason, "not-yet")
        self.assertTrue(self.check(c, at(3 * DAY)).due)

    def test_a_summoned_beat_moves_no_clock(self):
        c = cadence_of(awake(3), last=at(-2 * DAY), name="an")
        attempts.record_outcome(self.state, "an", "hand-1", "summoned", NOW)
        self.assertTrue(self.check(c, at(DAY)).due)


# -- due_for_home: one home on disk -------------------------------------------------

class HomeCase(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.state = self.fx.state

    def home(self, slug, pace=None, last=None, pending_=None, set_by=None, **kw):
        if pace is not None:
            c = cadence_of(pace, last=last, pending=pending_, name=slug, set_by=set_by)
            kw["cadence"] = cad.render_cadence(c)
        return self.fx.make_home(slug, when="2026-10-01T00:00Z", **kw)

    def due(self, home, slug, now=NOW, record=None, **kw):
        return due.due_for_home(home, slug, self.state, record or {}, now, **kw)


class TestDueForHome(HomeCase):
    def test_an_awake_home_is_due_on_its_schedule(self):
        home = self.home("an", awake(3), last=at(-3 * DAY))
        self.assertEqual(self.due(home, "an"), Due(True, "schedule", NOW))
        self.assertEqual(self.due(home, "an", now=at(-HOUR)).reason, "not-yet")

    def test_a_lost_record_starts_the_clock_now_once(self):
        home = self.home("an", awake(3))
        self.assertEqual(self.due(home, "an"), Due(False, "not-yet", next_at=at(3 * DAY)))
        self.assertEqual(attempts.load(self.state, "an")["seeded"], "now")
        self.assertEqual(self.due(home, "an", now=at(DAY)).next_at, at(3 * DAY))
        self.assertEqual(self.due(home, "an", now=at(3 * DAY)).reason, "schedule")
        (self.state / "an").write_text("lost\n")
        self.assertEqual(self.due(home, "an", now=at(5 * DAY)).next_at, at(8 * DAY))
        self.assertEqual(attempts.load(self.state, "an")["last_beat"], st(at(5 * DAY)))

    def test_no_seed_when_asked_not_to_write(self):
        home = self.home("an", awake(3))
        self.assertEqual(self.due(home, "an", write_seed=False).next_at, at(3 * DAY))
        self.assertFalse(self.state.exists())

    def test_cadence_s_last_beat_needs_no_seed(self):
        home = self.home("an", awake(3), last=at(-DAY))
        self.assertEqual(self.due(home, "an").next_at, at(2 * DAY))
        self.assertIsNone(attempts.load(self.state, "an")["last_beat"])

    def test_a_letter_wakes_an_asleep_home_at_its_floor(self):
        home = self.home("kalam", ASLEEP, last=at(-2 * DAY))
        self.fx.letter(home, "2026-11-15-hello.md", "hello\n", when=at(-HOUR))
        self.assertEqual(self.due(home, "kalam"), Due(False, "floor", next_at=at(DAY)))
        self.assertEqual(self.due(home, "kalam", now=at(DAY)), Due(True, "letter", at(DAY)))
        self.fx.write(home, "inbox/.seen-2026-11-15-hello.md", "surfaced 2026-11-16\n")
        self.assertEqual(self.due(home, "kalam", now=at(DAY)).reason, "no-mail")

    def test_a_letter_set_aside_at_rollback_still_wakes_its_reader(self):
        home = self.home("kalam", ASLEEP, last=at(-5 * DAY))
        self.fx.letter(home, "2026-11-10-pace-letter.md", "one question\n", when=at(-5 * DAY))
        self.fx.write(home, "inbox/.seen-2026-11-10-pace-letter.md",
                      "set aside at rollback, not shown\n")
        self.assertEqual(self.due(home, "kalam").reason, "letter")

    def test_letters_wait_and_awake_do_not_read_letter_times(self):
        for slug, pace in (("lw", LW), ("aw", awake(3))):
            with self.subTest(pace=pace):
                home = self.home(slug, pace, last=at(-DAY), letters=[("a.md", "a\n")])
                with mock.patch.object(due.homes, "letter_time", side_effect=AssertionError("read")):
                    self.assertFalse(self.due(home, slug).due)

    def test_never_due_without_a_soul_with_links_or_mid_merge(self):
        cases = {
            "no-soul": self.home("nosoul", ASLEEP, last=at(-9 * DAY), soul=False,
                                 letters=[("a.md", "a\n")]),
            "links": self.home("linked", ASLEEP, last=at(-9 * DAY), letters=[("a.md", "a\n")]),
            "mid-merge": self.home("merging", ASLEEP, last=at(-9 * DAY), letters=[("a.md", "a\n")]),
        }
        os.remove(cases["links"] / "CADENCE.md")
        self.fx.link(cases["links"], "CADENCE.md", self.fx.secret)
        (cases["mid-merge"] / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
        for reason, home in cases.items():
            with self.subTest(reason=reason):
                slug = home.name[len("citizen-"):]
                self.assertEqual(self.due(home, slug), Due(False, reason))
                self.assertFalse(self.due(home, slug, summoned=True).due)
        self.assertFalse(self.state.exists())

    def test_a_linked_marker_or_seen_marker_is_never_read_or_written_through(self):
        for i, rel in enumerate(("REST", "inbox/.seen-a.md", "beats/2026-11-01.md")):
            with self.subTest(rel=rel):
                slug = f"k{i}"
                home = self.home(slug, ASLEEP, last=at(-9 * DAY), letters=[("a.md", "a\n")])
                self.fx.link(home, rel, self.fx.secret)
                before = snapshot(self.fx.root / "outside")
                self.assertEqual(self.due(home, slug).reason, "links")
                self.assertEqual(snapshot(self.fx.root / "outside"), before)

    def test_a_fresh_index_lock_keeps_the_home_out_and_a_stale_one_does_not(self):
        home = self.home("kalam", ASLEEP, last=at(-9 * DAY), letters=[("a.md", "a\n")])
        lockfile = home / ".git" / "index.lock"
        lockfile.write_text("")
        self.assertEqual(self.due(home, "kalam", bound=600).reason, "mid-merge")
        old = time.time() - 1300
        os.utime(lockfile, (old, old))
        self.assertEqual(self.due(home, "kalam", bound=600).reason, "letter")
        self.assertEqual(self.due(home, "kalam", bound=900).reason, "mid-merge")

    def test_a_home_with_no_clone(self):
        self.assertEqual(self.due(self.fx.home_path("palance"), "palance"), Due(False, "no-home"))

    def test_a_summons_passes_the_gate_for_all_four_paces(self):
        for slug, pace in (("a1", ASLEEP), ("a2", LW), ("a3", LISTENING), ("a4", awake(30))):
            with self.subTest(pace=pace):
                home = self.home(slug, pace, last=at(-HOUR))
                self.assertFalse(self.due(home, slug).due)
                self.assertEqual(self.due(home, slug, summoned=True), Due(True, "summoned", NOW))
        self.assertFalse(self.state.exists())

    def test_a_pace_changed_between_the_ranking_and_the_gate_is_honoured(self):
        home = self.home("an", awake(3), last=at(-4 * DAY))
        self.assertEqual([s for s, _ in due.rank(self.fx.citizens, ["an"], self.state, {}, NOW)], ["an"])
        self.fx.write(home, "CADENCE.md",
                      cad.render_cadence(cadence_of(ASLEEP, last=at(-4 * DAY), name="an")))
        self.assertEqual(self.due(home, "an"), Due(False, "no-mail"))

    def test_a_v1_awake_home_that_never_spoke_is_due_through_rank_and_the_gate_alike(self):
        record = record_with(an={"group": "ambient-nine", "recipient": False,
                                 "set_by": AMBIENT.to_json()})
        home = self.fx.make_home("an", cadence=v1("an", "ambient", "2026-10-23 01:31"))
        first = due.rank(self.fx.citizens, ["an"], self.state, record, NOW)
        self.assertEqual(first, [])
        self.assertEqual(self.due(home, "an", record=record).next_at, at(3 * DAY))
        tick = at(3 * DAY)
        self.assertEqual(due.rank(self.fx.citizens, ["an"], self.state, record, tick),
                         [("an", Due(True, "schedule", tick))])
        self.assertEqual(self.due(home, "an", now=tick, record=record), Due(True, "schedule", tick))

    def test_a_decided_v1_home_that_cannot_be_migrated_is_never_due(self):
        home = self.fx.make_home("an", cadence=v1("an", "ambient", "2026-10-23 01:31"),
                                 letters=[("a.md", "a\n")])
        self.assertEqual(self.due(home, "an", now=at(30 * DAY)), Due(False, "blocked"))
        self.assertEqual(self.due(home, "an", summoned=True).reason, "summoned")

    def test_a_show_back_beat_from_the_file(self):
        said = at(-3 * DAY)
        home = self.home("kalam", ASLEEP, last=said, pending_=pending(LISTENING, said))
        with mock.patch.object(due, "WAKE_CONFIRM_BEAT", True):
            self.assertEqual(self.due(home, "kalam"), Due(True, "show-back", NOW))
            self.fx.letter(home, "2026-11-15-hi.md", "hi\n", when=at(-HOUR))
            self.assertEqual(self.due(home, "kalam").reason, "letter")

    def test_a_beat_script_looped_on_a_silent_awake_citizen_keeps_its_pace(self):
        home = self.home("an", awake(3), last=NOW)
        beats, now = [], NOW
        for i in range(20 * 24):
            if self.due(home, "an", now=now).due:
                beats.append(now)
                attempts.record_outcome(self.state, "an", f"loop-{i}", "silent", now)
            now += HOUR
        self.assertEqual(beats, [at(k * 3 * DAY) for k in range(1, 7)])

    def test_a_home_that_cannot_be_read_is_not_due(self):
        home = self.home("an", awake(3), last=at(-9 * DAY))
        with mock.patch.object(due.cadence, "read_cadence", side_effect=OSError("disk")):
            self.assertEqual(self.due(home, "an"), Due(False, "error"))


class TestTheLastPace1VersionDecides(HomeCase):
    """Spec section 2: a CADENCE.md changed in a commit the new code did not make
    is treated as a disagreeing marker is: the last pace/1 version decides, until
    the next commit for that home restores it."""

    def tagged_home(self, slug, pace, last):
        text = cad.render_cadence(cadence_of(pace, last=last, name=slug))
        return self.fx.make_home(slug, cadence=text, when="2026-10-01T00:00Z",
                                 message=f"beat: {slug} reflected (2026-11-12) \u00b7 pace/1")

    def change(self, home, slug, text):
        """An untagged commit (an old waker, a person) that rewrites or deletes CADENCE.md."""
        if text is None:
            (home / "CADENCE.md").unlink()
        else:
            self.fx.write(home, "CADENCE.md", text)
        self.fx.commit(home, f"beat: {slug} reflected (2026-11-14)")

    def test_an_untagged_letters_wait_does_not_stop_an_awake_schedule(self):
        home = self.tagged_home("an", awake(3), last=at(-3 * DAY))
        self.change(home, "an", cad.render_cadence(cadence_of(LW, last=at(-3 * DAY), name="an")))
        self.assertEqual(self.due(home, "an"), Due(True, "schedule", NOW))
        self.assertEqual(due.rank(self.fx.citizens, ["an"], self.state, {}, NOW),
                         [("an", Due(True, "schedule", NOW))])

    def test_an_untagged_awake_does_not_wake_an_asleep_citizen(self):
        home = self.tagged_home("kalam", ASLEEP, last=at(-10 * DAY))
        self.change(home, "kalam", cad.render_cadence(cadence_of(awake(3), last=at(-10 * DAY))))
        self.assertEqual(self.due(home, "kalam"), Due(False, "no-mail"))

    def test_an_untagged_emptied_deleted_or_v1_file_is_never_blocked(self):
        for slug, text in (("an", ""), ("ud", None), ("zi", v1("zi", "rest-on", "2026-11-14 04:00"))):
            with self.subTest(slug=slug, text=text):
                home = self.tagged_home(slug, awake(3), last=at(-3 * DAY))
                self.change(home, slug, text)
                self.assertEqual(self.due(home, slug), Due(True, "schedule", NOW))


# -- rank and next ------------------------------------------------------------------

class TestRank(HomeCase):
    def test_most_overdue_first(self):
        self.home("a", awake(3), last=at(-4 * DAY))
        self.home("b", awake(3), last=at(-6 * DAY))
        self.home("c", ASLEEP, last=at(-9 * DAY), letters=[("x.md", "x\n")])
        self.home("d", awake(3), last=at(-HOUR))
        got = due.rank(self.fx.citizens, ["a", "b", "c", "d"], self.state, {}, NOW)
        self.assertEqual([s for s, _ in got], ["c", "b", "a"])
        self.assertEqual([d.reason for _, d in got], ["letter", "schedule", "schedule"])

    def test_ties_go_by_roster_order_from_the_cursor(self):
        roster = ["a", "b", "c", "d"]
        for slug in roster:
            self.home(slug, awake(3), last=at(-3 * DAY))
        for cursor, want in ((0, "abcd"), (1, "bcda"), (3, "dabc"), (6, "cdab"), (-1, "dabc")):
            with self.subTest(cursor=cursor):
                got = due.rank(self.fx.citizens, roster, self.state, {}, NOW, cursor=cursor)
                self.assertEqual("".join(s for s, _ in got), want)

    def test_only_roster_homes_that_are_due(self):
        self.home("a", awake(3), last=at(-4 * DAY))
        self.home("off", awake(3), last=at(-4 * DAY))
        self.home("nosoul", awake(3), last=at(-4 * DAY), soul=False)
        self.home("asleep", ASLEEP, last=at(-4 * DAY))
        got = due.rank(self.fx.citizens, ["a", "nosoul", "asleep", "missing"], self.state, {}, NOW)
        self.assertEqual([s for s, _ in got], ["a"])

    def test_one_failing_home_does_not_hold_up_the_rest(self):
        self.home("a", awake(3), last=at(-9 * DAY))
        bad = self.home("b", awake(3), last=at(-9 * DAY))
        self.home("c", awake(3), last=at(-4 * DAY))
        attempts.record_outcome(self.state, "a", "f-1", "home-failed", at(-HOUR))
        os.remove(bad / "CADENCE.md")
        self.fx.link(bad, "CADENCE.md", self.fx.secret)
        got = due.rank(self.fx.citizens, ["a", "b", "c"], self.state, {}, NOW)
        self.assertEqual([s for s, _ in got], ["c"])

    def test_a_stamp_out_of_range_in_one_home_holds_up_no_one(self):
        # an offset that carries a stamp past year 1 or 9999 reads as no stamp, in
        # CADENCE.md and in the attempt record alike; a proposal said at the end of
        # time leaves a home that cannot be read ("error", never due), when the
        # show-back beat reads it (patched on). The others rank.
        patcher = mock.patch.object(due, "WAKE_CONFIRM_BEAT", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home("a", awake(3), last=at(-9 * DAY))
        self.home("c", awake(3), last=at(-4 * DAY))
        far = Pending(awake(5), "9999-12-31T23:30Z", "pace: every 5 days", False)
        cases = (("cadence", dict(pace=awake(3), last=at(-9 * DAY)), "not-yet"),
                 ("record", dict(pace=awake(3), last=at(-HOUR)), "not-yet"),
                 ("pending", dict(pace=ASLEEP, last=at(-9 * DAY), pending_=far), "error"))
        for slug, kw, reason in cases:
            with self.subTest(slug=slug):
                home = self.home(slug, **kw)
                if slug == "cadence":
                    text = (home / "CADENCE.md").read_text(encoding="utf-8")
                    self.fx.write(home, "CADENCE.md", text.replace(
                        f"- last beat: {st(at(-9 * DAY))}", "- last beat: 0001-01-01T00:00+01:00"))
                if slug == "record":
                    self.state.mkdir(parents=True, exist_ok=True)
                    (self.state / slug).write_text(json.dumps(
                        {"slug": slug, "last_beat": "9999-12-31T23:59-01:00"}), encoding="utf-8")
                roster = ["a", slug, "c"]
                got = due.rank(self.fx.citizens, roster, self.state, {}, NOW)
                self.assertEqual([s for s, _ in got], ["a", "c"])
                assessed = dict(due.assess(self.fx.citizens, roster, self.state, {}, NOW))
                self.assertEqual(assessed[slug].reason, reason)

        # a file nested past the parser's depth, or a folder or link where the
        # record should be, is that one home's "error" or its defaults, never the roster's
        deep = "[" * 50000
        for slug in ("deeprec", "deepcad", "dirrec", "linkrec"):
            with self.subTest(slug=slug):
                home = self.home(slug, awake(3))
                self.state.mkdir(parents=True, exist_ok=True)
                if slug == "deeprec":
                    (self.state / slug).write_text(deep, encoding="utf-8")
                elif slug == "deepcad":
                    text = (home / "CADENCE.md").read_text(encoding="utf-8")
                    lines = text.split("\n")
                    i = next(n for n, ln in enumerate(lines) if ln.startswith("<!-- pace/1"))
                    lines[i] = '<!-- pace/1 {"a":' + deep + "} -->"
                    self.fx.write(home, "CADENCE.md", "\n".join(lines))
                elif slug == "dirrec":
                    (self.state / slug).mkdir()
                else:
                    (self.state / slug).symlink_to(self.fx.outside)
                roster = ["a", slug, "c"]
                got = due.rank(self.fx.citizens, roster, self.state, {}, NOW)
                self.assertEqual([s for s, _ in got], ["a", "c"])
                assessed = dict(due.assess(self.fx.citizens, roster, self.state, {}, NOW))
                self.assertFalse(assessed[slug].due)
                if slug in ("dirrec", "linkrec"):
                    self.assertEqual(assessed[slug].reason, "error")

    def test_rank_takes_a_roster_file(self):
        self.home("a", awake(3), last=at(-4 * DAY))
        roster = self.fx.roster("a")
        self.assertEqual([s for s, _ in due.rank(self.fx.citizens, roster, self.state, {}, NOW)], ["a"])

    def test_read_roster(self):
        path = self.fx.love / "roster.txt"
        path.write_text("an\n\n# a note\n  kalam  \nan\n../evil\nai-love\r\n", encoding="utf-8")
        self.assertEqual(due.read_roster(path), ["an", "kalam", "ai-love"])

    def test_rank_seeds_and_next_never_writes(self):
        self.home("a", awake(3))
        self.home("b", ASLEEP)
        roster = ["a", "b"]
        self.assertEqual(due.next_line(self.fx.citizens, roster, self.state, {}, NOW),
                         f"next to wake: nobody due; next due a at {st(at(3 * DAY))}")
        self.assertFalse(self.state.exists())
        self.assertEqual(due.rank(self.fx.citizens, roster, self.state, {}, NOW), [])
        self.assertEqual(sorted(p for p in os.listdir(self.state) if not p.startswith(".")), ["a", "b"])
        self.assertEqual(due.rank(self.fx.citizens, roster, self.state, {}, at(3 * DAY)),
                         [("a", Due(True, "schedule", at(3 * DAY)))])

    def test_next_line(self):
        self.home("a", awake(3), last=at(-3 * DAY - 5 * HOUR - 59 * MINUTE))
        self.home("b", awake(5), last=at(-DAY))
        self.home("c", ASLEEP, last=at(-DAY))
        lines = lambda roster, now=NOW: due.next_line(self.fx.citizens, roster, self.state, {}, now)
        self.assertEqual(lines(["a", "b", "c"]), "next to wake: a (schedule, overdue 5h)")
        self.assertEqual(lines(["b", "c"]), f"next to wake: nobody due; next due b at {st(at(4 * DAY))}")
        self.assertEqual(lines(["c"]), "next to wake: nobody due")
        self.assertEqual(lines([]), "next to wake: nobody due")


# -- the modules themselves -----------------------------------------------------------

class TestIsolation(unittest.TestCase):
    def test_modules_import_under_isolated_mode_and_ignore_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mod in ("json", "re", "fcntl", "shutil", "stat", "time", "typing", "dataclasses",
                        "contextlib"):
                Path(tmp, f"{mod}.py").write_text("raise SystemExit('planted module ran')\n")
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import attempts, due, lock; "
                    "print(due.WAKE_CONFIRM_BEAT, attempts.OUTCOMES[0], lock.DEFAULT_BOUND)")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS)], cwd=tmp,
                               capture_output=True, text=True, timeout=30, check=False,
                               env={"PATH": h.SYSTEM_PATH})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), f"{due.WAKE_CONFIRM_BEAT} spoken 600")

    def test_modules_start_with_future_annotations(self):
        for name in ("attempts.py", "due.py", "lock.py"):
            with self.subTest(module=name):
                first = (h.TOOLS / "citizen_pace_lib" / name).read_text().splitlines()[0]
                self.assertEqual(first, "from __future__ import annotations")


if __name__ == "__main__":
    unittest.main()
