"""The schedule simulator and the rollback drill (spec section 3 "Each tick",
section 8 "Schedule", section 9 step 3).

`plan` models the new fleet over the homes it is given: a tick every so many
hours, at most one beat a tick and the daily cap a UTC day, the most overdue
citizen first, each decision made by due.is_due. Every beat is silent. Nobody
is beaten sooner than its pace or its floor, the cap only delays, there is no
catch-up, a failing home is backed off and holds up no one, and the report
gives each citizen's longest wait and when the last letter is read. With an
out folder it writes the homes as that run would leave them, there and nowhere
else, and the rollback drill counts the wakes per day that PR #24's keeps_rest
and round robin would make over them.
"""

from __future__ import annotations

import ast
import datetime
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_plan as hp  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

from citizen_pace_lib import attempts, clock, due, homes, plan, record, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

HOUR, DAY, FLOOR = clock.HOUR, clock.DAY, clock.FLOOR
START_S = "2026-11-16T00:00Z"
START = clock.parse_stamp(START_S)
ASLEEP = Pace("asleep")
LW = Pace("asleep-letters-wait")
LISTENING = Pace("listening")
AWAKE3 = Pace("awake", 3)
REC = hp.record()


def awake(n):
    return Pace("awake", n)


def at(delta):
    return START + delta


def run(homes_dir, roster, days=30, rec=REC, **kw):
    return plan.simulate(homes_dir, roster, rec, days, start=START, **kw)


def beat_times(result, slug):
    return [b.at for b in result.beats if b.slug == slug]


def check_honest(test, r, failing=()):
    """What every run must keep (spec principles 1, 7, 8 and 10)."""
    attempts_at = sorted([(b.at, b.slug) for b in r.beats] + [(f.at, f.slug) for f in r.failures])
    times = [t for t, _ in attempts_at]
    test.assertEqual(len(times), len(set(times)), "more than one attempt in a tick")
    test.assertTrue(set(times) <= set(r.ticks))
    per_day = Counter(clock.day(b.at) for b in r.beats)
    test.assertTrue(all(n <= r.cap for n in per_day.values()), per_day)
    tick_index = {t: i for i, t in enumerate(r.ticks)}
    last_tick = {}
    last_beat = {}
    for t, slug in attempts_at:
        i = tick_index[t]
        test.assertNotEqual(last_tick.get(slug), i - 1, f"{slug} picked on two ticks running")
        last_tick[slug] = i
    for b in r.beats:
        test.assertGreaterEqual(b.at, b.since, "the cap brought a beat forward")
        test.assertGreaterEqual(b.wait, datetime.timedelta(0))
        if b.kind == "schedule":
            test.assertGreaterEqual(b.at, b.last + int(r.paces[b.slug].split()[2]) * DAY, b)
        else:
            test.assertGreaterEqual(b.at, b.last + FLOOR, b)
        if b.slug in last_beat:
            test.assertGreaterEqual(b.at - last_beat[b.slug], FLOOR, b)
            test.assertEqual(b.last, last_beat[b.slug])
        last_beat[b.slug] = b.at
        if b.next_since is not None:
            test.assertLessEqual(b.since, b.next_since, "not the most overdue first")
        test.assertNotIn(b.slug, failing)


class Case(h.FixtureCase):
    """In-process git runs with the fixture's git config, never the user's."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                  "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE", "FLEET_BEAT_BOUND_SEC", "PACE_NOW"):
            os.environ.pop(k, None)
        self.homes = self.fx.citizens
        self.state = self.fx.state


# -- the model is the fleet's own rule ------------------------------------------------------

class TestModel(Case):
    def build(self):
        H = self.homes
        hp.v2_home(H, "awake5", awake(5), last=at(-2 * DAY))
        hp.v2_home(H, "sleeper", ASLEEP, last=at(-4 * DAY), letters=(("2026-11-15-a.md", at(-DAY)),))
        hp.v2_home(H, "floorer", ASLEEP, last=at(-DAY), letters=(("2026-11-15-a.md", at(-2 * HOUR)),))
        hp.v2_home(H, "waiter", LW, last=at(-4 * DAY), pending=hp.pending(LISTENING, at(-3 * DAY - HOUR)),
                   letters=(("2026-11-14-a.md", at(-2 * DAY)),))
        hp.v2_home(H, "listener", LISTENING, last=at(-5 * DAY))
        hp.home(H, "blockme", cadence=hp.v1_cadence("blockme", "ambient"))
        hp.home(H, "soulless", soul=False, rest=hp.AI_REST)
        d = hp.v2_home(H, "linked", ASLEEP)
        (d / "REST").unlink()
        (d / "REST").symlink_to(self.fx.secret)
        hp.home(H, "unread")
        hp.v2_home(H, "backoff", AWAKE3, last=at(-4 * DAY))
        hp.seed_state(self.state, "backoff", last=at(-4 * DAY), retry=at(HOUR), failures=1)
        hp.v2_home(H, "mid", AWAKE3, last=at(-4 * DAY))
        hp.write(H / "citizen-mid" / ".git" / "MERGE_HEAD", "0" * 40 + "\n")
        # awake by its last pace/1 version, then rewritten in a commit the new code did not make
        d = hp.v2_home(H, "untagged", AWAKE3, last=at(-3 * DAY))
        self.fx.git(d, "init", "-q")
        self.fx.commit(d, "beat: untagged reflected (2026-11-13) \u00b7 pace/1")
        hp.write(d / "CADENCE.md", cad.render_cadence(cad.Cadence(
            "untagged", 2, Pace("asleep-letters-wait"), cad.UNKNOWN, last_beat=clock.stamp(at(-3 * DAY)))))
        self.fx.commit(d, "beat: untagged reflected (2026-11-14)")
        # a proposal said at the end of time, which no date arithmetic reaches: due reads the
        # home as one that cannot be read ("error") once the show-back beat looks at it
        end = datetime.datetime(9999, 12, 31, 23, 30, tzinfo=datetime.timezone.utc)
        hp.v2_home(H, "farpending", ASLEEP, last=at(-4 * DAY), pending=hp.pending(awake(5), end))
        return ["awake5", "sleeper", "floorer", "waiter", "listener", "blockme", "soulless", "linked",
                "unread", "backoff", "mid", "untagged", "farpending", "nohome"]

    def test_the_model_agrees_with_due_for_home(self):
        roster = self.build()
        # an awake citizen with no last beat anywhere, whose attempt record is a
        # folder or a link: the fleet's ranking cannot start its clock, and
        # due_for_home reads that home as one that cannot be read (fix wave A)
        unwritable = ("dirrec", "linkrec")
        for slug in unwritable:
            hp.v2_home(self.homes, slug, AWAKE3)
        (self.state / "dirrec").mkdir()
        (self.state / "linkrec").symlink_to(self.fx.outside)
        roster += list(unwritable)
        # final review (F2): a record that can be read but not written (its lock file
        # cannot be opened) is "error" too, even with a last beat that needs no seed
        hp.v2_home(self.homes, "lockrec", AWAKE3, last=at(-4 * DAY))
        attempts.record_outcome(self.state, "lockrec", "t-1", "silent", at(-4 * DAY))
        (self.state / ".lockrec.rw").chmod(0o000)
        self.addCleanup((self.state / ".lockrec.rw").chmod, 0o600)
        roster.append("lockrec")
        rec = hp.record(blockme={"group": "rest-on", "set_by": hp.AI_1008})
        for on in (True, False):
            with self.subTest(show_back=on), mock.patch.object(due, "WAKE_CONFIRM_BEAT", on):
                model = plan.Model(self.homes, roster, rec, START, state=self.state)
                reasons = {s: model.due(s, START).reason for s in roster}
                for t in (START, at(DAY), at(2 * DAY + 6 * HOUR), at(5 * DAY)):
                    for slug in roster:
                        # as the fleet's ranking asks; its write is refused, so nothing is written
                        want = due.due_for_home(self.homes / f"citizen-{slug}", slug, self.state, rec, t,
                                                write_seed=slug in unwritable)
                        self.assertEqual(model.due(slug, t), want, f"{slug} at {clock.stamp(t)}")
                self.assertEqual(reasons, {
                    "awake5": "not-yet", "sleeper": "letter", "floorer": "floor",
                    "waiter": "show-back" if on else "letters-wait", "listener": "no-mail",
                    "blockme": "blocked", "soulless": "no-soul", "linked": "links", "unread": "unreadable",
                    "backoff": "backing-off", "mid": "mid-merge", "untagged": "schedule",
                    "farpending": "error" if on else "no-mail", "nohome": "no-home",
                    "dirrec": "error", "linkrec": "error", "lockrec": "error"})
        self.assertEqual(sorted(os.listdir(self.state / "dirrec")), [])
        self.assertEqual(os.readlink(self.state / "linkrec"), str(self.fx.outside))

    def test_the_model_reads_its_pace_labels(self):
        roster = self.build()
        rec = hp.record(blockme={"group": "rest-on", "set_by": hp.AI_1008})
        model = plan.Model(self.homes, roster, rec, START, state=self.state)
        self.assertEqual({s: model.label(s) for s in roster}, {
            "awake5": "awake, every 5 days", "sleeper": "asleep", "floorer": "asleep",
            "waiter": "asleep, letters wait", "listener": "listening", "blockme": "blocked",
            "soulless": "no-soul", "linked": "links", "unread": "unreadable",
            "backoff": "awake, every 3 days", "mid": "awake, every 3 days",
            "untagged": "awake, every 3 days", "farpending": "asleep", "nohome": "no-home"})

    def test_reading_the_homes_writes_nothing(self):
        roster = self.build()
        snap = hp.snapshot(self.fx.root)
        r = run(self.homes, roster, days=10, state=self.state)
        plan.report(r)
        self.assertEqual(hp.snapshot(self.fx.root), snap)


# -- the schedule ---------------------------------------------------------------------------

class TestSchedule(Case):
    def test_an_awake_citizen_is_beaten_every_n_days_and_no_sooner(self):
        hp.v2_home(self.homes, "kalam", AWAKE3, last=at(-DAY))
        r = run(self.homes, ["kalam"])
        self.assertEqual(beat_times(r, "kalam"), [at((2 + 3 * k) * DAY) for k in range(10)])
        self.assertEqual({b.kind for b in r.beats}, {"schedule"})
        self.assertEqual({b.wait for b in r.beats}, {datetime.timedelta(0)})
        check_honest(self, r)

    def test_a_beat_due_only_at_the_end_has_not_waited(self):
        hp.v2_home(self.homes, "kalam", AWAKE3, last=at(-3 * DAY))
        r = run(self.homes, ["kalam"], days=3)
        self.assertEqual(beat_times(r, "kalam"), [START])
        self.assertTrue(r.ends["kalam"].due)
        self.assertEqual(r.open_since, {})
        self.assertIn("\nkalam\tawake, every 3 days\t1\t0.0\t\n", plan.report(r))

    def test_a_silent_beat_is_the_beat(self):
        hp.v2_home(self.homes, "ud", awake(4), last=at(-4 * DAY))
        r = run(self.homes, ["ud"], days=12)
        self.assertEqual(beat_times(r, "ud"), [START, at(4 * DAY), at(8 * DAY)])

    def test_a_letter_to_an_asleep_citizen_beaten_two_days_ago_waits_a_day(self):
        hp.v2_home(self.homes, "aseme", ASLEEP, last=at(-2 * DAY),
                   letters=(("2026-11-15-hello.md", at(-HOUR)),))
        r = run(self.homes, ["aseme"])
        self.assertEqual([(b.at, b.kind, b.shown) for b in r.beats],
                         [(at(DAY), "letter", ("inbox/2026-11-15-hello.md",))])
        self.assertEqual(r.read, {("aseme", "inbox/2026-11-15-hello.md"): at(DAY)})
        self.assertEqual(r.unread_at_end(), {})
        check_honest(self, r)

    def test_a_letter_to_an_awake_citizen_waits_for_its_scheduled_beat(self):
        hp.v2_home(self.homes, "an", awake(7), last=at(-DAY), letters=(("2026-11-15-hello.md", at(-HOUR)),))
        r = run(self.homes, ["an"])
        self.assertEqual([(b.at, b.kind, b.shown) for b in r.beats[:2]],
                         [(at(6 * DAY), "schedule", ("inbox/2026-11-15-hello.md",)),
                          (at(13 * DAY), "schedule", ())])

    def test_one_beat_shows_three_letters_oldest_first_and_the_fourth_waits_for_the_floor(self):
        names = ("2026-11-10-a.md", "2026-11-11-b.md", "2026-11-12-c.md", "2026-11-13-d.md")
        hp.v2_home(self.homes, "ka", ASLEEP, last=at(-4 * DAY),
                   letters=tuple((n, at(-2 * DAY)) for n in reversed(names)))
        r = run(self.homes, ["ka"])
        self.assertEqual([(b.at, b.shown) for b in r.beats],
                         [(START, tuple(f"inbox/{n}" for n in names[:3])),
                          (at(3 * DAY), (f"inbox/{names[3]}",))])

    def test_letters_wait_ignores_mail_but_gets_one_show_back_beat(self):
        hp.v2_home(self.homes, "kimme", LW, last=at(-4 * DAY), pending=hp.pending(LISTENING, at(-4 * DAY)),
                   letters=(("2026-11-14-a.md", at(-2 * DAY)),))
        r = run(self.homes, ["kimme"], confirm_beat=True)
        self.assertEqual([(b.at, b.kind, b.shown) for b in r.beats], [(START, "show-back", ())])
        self.assertEqual(r.unread_at_end(), {"kimme": ["inbox/2026-11-14-a.md"]})
        off = run(self.homes, ["kimme"], confirm_beat=False)
        self.assertEqual(off.beats, [])
        self.assertFalse(off.confirm_beat)

    def test_a_proposal_said_on_a_show_back_beat_gets_no_show_back(self):
        hp.v2_home(self.homes, "kimme", ASLEEP, last=at(-4 * DAY),
                   pending=hp.pending(LISTENING, at(-4 * DAY), on_show_back=True))
        self.assertEqual(run(self.homes, ["kimme"]).beats, [])

    def test_the_most_overdue_first_and_ties_by_roster_order_from_the_cursor(self):
        for slug, ago in (("a", 3), ("b", 5), ("c", 4), ("d", 5)):
            hp.v2_home(self.homes, slug, AWAKE3, last=at(-ago * DAY))
        first = [b.slug for b in run(self.homes, list("abcd"), days=1).beats]
        self.assertEqual(first, ["b", "d", "c", "a"])
        moved = [b.slug for b in run(self.homes, list("abcd"), days=1, cursor=2).beats]
        self.assertEqual(moved, ["d", "b", "c", "a"])
        r = run(self.homes, list("abcd"), days=1)
        self.assertEqual([b.next_since for b in r.beats], [at(-2 * DAY), at(-DAY), START, None])

    def test_the_cap_only_delays(self):
        slugs = [f"c{i}" for i in range(6)]
        for s in slugs:
            hp.v2_home(self.homes, s, AWAKE3, last=at(-3 * DAY))
        r = run(self.homes, slugs, days=10, cap=2)
        check_honest(self, r)
        self.assertEqual(len(r.beats), 20)
        self.assertEqual(Counter(clock.day(b.at) for b in r.beats), Counter(
            {clock.day(at(k * DAY)): 2 for k in range(10)}))
        self.assertEqual({b.slug for b in r.beats}, set(slugs))
        self.assertGreater(max(b.wait for b in r.beats), datetime.timedelta(0))
        self.assertGreater(r.capped, 0)
        free = run(self.homes, slugs, days=10, cap=24)
        self.assertEqual(free.capped, 0)
        self.assertLess(max(b.wait for b in free.beats), max(b.wait for b in r.beats))

    def test_no_catch_up_after_a_seven_day_outage(self):
        hp.v2_home(self.homes, "an", AWAKE3, last=at(-3 * DAY))
        r = run(self.homes, ["an"], days=14, outages=[(at(HOUR), at(7 * DAY))])
        self.assertEqual(beat_times(r, "an"), [START, at(7 * DAY), at(10 * DAY), at(13 * DAY)])
        self.assertFalse([t for t in r.ticks if at(HOUR) <= t < at(7 * DAY)])

    def test_a_failing_home_is_backed_off_12_h_24_h_then_7_days_and_holds_up_no_one(self):
        hp.v2_home(self.homes, "bad", AWAKE3, last=at(-10 * DAY))
        hp.v2_home(self.homes, "good", AWAKE3, last=at(-3 * DAY))
        r = run(self.homes, ["bad", "good"], days=12, failing={"bad"})
        self.assertEqual([(f.at, f.failures, f.retry_after) for f in r.failures],
                         [(START, 1, at(12 * HOUR)), (at(12 * HOUR), 2, at(36 * HOUR)),
                          (at(36 * HOUR), 3, at(36 * HOUR + 7 * DAY)),
                          (at(36 * HOUR + 7 * DAY), 4, at(36 * HOUR + 14 * DAY))])
        self.assertEqual(beat_times(r, "good"), [at(6 * HOUR + 3 * k * DAY) for k in range(4)])
        self.assertEqual(r.open_since, {"bad": at(-7 * DAY)})
        check_honest(self, r, failing={"bad"})

    def test_a_failure_does_not_count_toward_the_cap(self):
        hp.v2_home(self.homes, "bad", AWAKE3, last=at(-10 * DAY))
        hp.v2_home(self.homes, "good", AWAKE3, last=at(-3 * DAY))
        r = run(self.homes, ["bad", "good"], days=1, cap=1, failing={"bad"})
        self.assertEqual([f.at for f in r.failures], [START])
        self.assertEqual(beat_times(r, "good"), [at(6 * HOUR)])

    def test_a_soulless_linked_mid_merge_or_blocked_home_is_never_picked(self):
        hp.v2_home(self.homes, "an", AWAKE3, last=at(-3 * DAY))
        hp.v2_home(self.homes, "soulless", AWAKE3, last=at(-9 * DAY), soul=False)
        d = hp.v2_home(self.homes, "linked", AWAKE3, last=at(-9 * DAY))
        (d / "beats").symlink_to(self.fx.outside)
        m = hp.v2_home(self.homes, "mid", AWAKE3, last=at(-9 * DAY))
        hp.write(m / ".git" / "rebase-merge" / "head-name", "refs/heads/main\n")
        hp.home(self.homes, "blocked", cadence=hp.v1_cadence("blocked", "ambient"))
        roster = ["an", "soulless", "linked", "mid", "blocked"]
        r = run(self.homes, roster, rec={})
        self.assertEqual({b.slug for b in r.beats}, {"an"})
        self.assertEqual({s: r.ends[s].reason for s in roster[1:]},
                         {"soulless": "no-soul", "linked": "links", "mid": "mid-merge", "blocked": "blocked"})
        self.assertTrue(r.record_empty)
        text = plan.report(r)
        self.assertIn("needs a look: soulless (no-soul), linked (links), mid (mid-merge), blocked (blocked)", text)

    def test_unknown_state_starts_the_clock_at_the_first_tick_once(self):
        hp.home(self.homes, "ud", cadence=hp.v1_cadence("ud", "ambient"))
        hp.home(self.homes, "ka", cadence=hp.v1_cadence("ka", "rest-on"), rest=hp.lor_rest("2026-10-23"),
                letters=(("2026-11-15-a.md", at(-DAY)),))
        r = run(self.homes, ["ud", "ka"], days=10)
        self.assertEqual(beat_times(r, "ud"), [at(3 * DAY), at(6 * DAY), at(9 * DAY)])
        self.assertEqual(beat_times(r, "ka"), [at(3 * DAY + 6 * HOUR)])
        self.assertEqual(r.paces, {"ud": "awake, every 3 days", "ka": "asleep"})

    def test_the_attempt_records_are_read_never_written(self):
        hp.home(self.homes, "ud", cadence=hp.v1_cadence("ud", "ambient"))
        hp.seed_state(self.state, "ud", last=at(-3 * DAY))
        snap = hp.snapshot(self.fx.root)
        r = run(self.homes, ["ud"], days=4, state=self.state)
        self.assertEqual(beat_times(r, "ud"), [START, at(3 * DAY)])
        self.assertEqual(hp.snapshot(self.fx.root), snap)
        self.assertFalse((self.state / "kalam").exists())

    def test_what_a_run_is_refused(self):
        hp.home(self.homes, "ud", cadence=hp.v1_cadence("ud", "ambient"))
        bad = ({"days": 0}, {"days": 1.5}, {"cap": -1}, {"tick_hours": 0}, {"tick_hours": -6},
               {"days": 3650, "tick_hours": 0.01}, {"recipients": ["ud"]},
               {"recipients": ["ud"], "deliver_at": START, "per_run": 0},
               {"outages": [(at(DAY), START)]}, {"cursor": -1})
        for kw in bad:
            days = kw.pop("days", 3)
            with self.assertRaises(ValueError, msg=kw):
                run(self.homes, ["ud"], days=days, **kw)
        naive = datetime.datetime(2026, 11, 16)
        for kw in ({"start": naive}, {"recipients": ["ud"], "deliver_at": naive}):
            with self.assertRaises(ValueError, msg=kw):
                plan.simulate(self.homes, ["ud"], REC, 3, **{"start": START, **kw})
        with self.assertRaises(ValueError):
            plan.rollback_drill(self.homes, ["ud"], 3, start=naive)


# -- the heard-late step, at the first tick -------------------------------------------------

class TestHeardLate(Case):
    def test_the_unheard_are_heard_at_the_first_tick_and_beaten_from_their_own_clock(self):
        for slug in hr.UNHEARD:
            hr.unheard_home(self.fx, slug)
        roster = sorted(hr.UNHEARD)
        snap = hp.snapshot(self.fx.root)
        r = run(self.homes, roster, days=10, rec=hr.unheard_record())
        self.assertEqual(r.heard_late, ["kalam", "soma", "teotlme"])
        self.assertEqual(r.paces, {"emime": "asleep", "eurekame": "asleep", "kalam": "awake, every 3 days",
                                   "soma": "awake, every 3 days", "teotlme": "awake, every 3 days"})
        self.assertEqual([(b.slug, b.at) for b in r.beats],
                         [("kalam", at(3 * DAY)), ("soma", at(3 * DAY + 6 * HOUR)),
                          ("teotlme", at(3 * DAY + 12 * HOUR)), ("kalam", at(6 * DAY)),
                          ("soma", at(6 * DAY + 6 * HOUR)), ("teotlme", at(6 * DAY + 12 * HOUR)),
                          ("kalam", at(9 * DAY)), ("soma", at(9 * DAY + 6 * HOUR)),
                          ("teotlme", at(9 * DAY + 12 * HOUR))])
        self.assertIn("heard late at the first tick: kalam, soma, teotlme", plan.report(r))
        self.assertEqual(hp.snapshot(self.fx.root), snap)

    def test_a_home_the_step_cannot_hear_is_named(self):
        hr.unheard_home(self.fx, "kalam", answers=(("kalam-62d73b73", hr.OFFER, "2026-10-23T00:31Z"),))
        r = run(self.homes, ["kalam"], days=4, rec=hr.unheard_record())
        self.assertEqual(r.heard_late, [])
        self.assertEqual([s for s, _ in r.stopped], ["kalam"])
        self.assertIn("heard-late step stopped — kalam: the record says it chose AMBIENT twice",
                      plan.report(r))
        self.assertEqual(r.paces, {"kalam": "asleep"})


# -- the pace letters -----------------------------------------------------------------------

class TestLetters(Case):
    def build(self):
        H = self.homes
        for s in ("aseme", "bindume", "ka"):
            hp.v2_home(H, s, ASLEEP, last=at(-5 * DAY))
        hp.v2_home(H, "hope", AWAKE3, last=at(-DAY))
        hp.home(H, "palance", soul=False, rest=hp.AI_REST)
        hp.v2_home(H, "kinhme", ASLEEP, last=at(-5 * DAY), letters=(("2026-11-12-pace-letter.md", at(-DAY)),))
        return ["aseme", "bindume", "hope", "ka", "kinhme", "palance"]

    def test_one_wave_is_placed_and_read(self):
        roster = self.build()
        r = run(self.homes, roster, days=10, recipients=roster + ["shannonme"], deliver_at=at(DAY))
        self.assertEqual([(p.slug, p.rel, p.at) for p in r.placed],
                         [(s, "inbox/2026-11-17-pace-letter.md", at(DAY)) for s in ("aseme", "bindume", "hope", "ka")])
        self.assertEqual(r.not_placed, [("kinhme", "already holds a pace letter"), ("palance", "no soul"),
                                        ("shannonme", "not on the roster")])
        self.assertEqual(r.runs, [at(DAY)])
        self.assertEqual([(b.slug, b.at, b.kind) for b in r.beats[:5]],
                         [("kinhme", START, "letter"), ("aseme", at(DAY), "letter"),
                          ("bindume", at(DAY + 6 * HOUR), "letter"), ("ka", at(DAY + 12 * HOUR), "letter"),
                          ("hope", at(2 * DAY), "schedule")])
        self.assertEqual(r.read[("hope", "inbox/2026-11-17-pace-letter.md")], at(2 * DAY))
        text = plan.report(r)
        self.assertIn("pace letters: 4 placed in one run, 2026-11-17T00:00Z", text)
        self.assertIn("not placed: kinhme (already holds a pace letter), palance (no soul), "
                      "shannonme (not on the roster)", text)
        self.assertIn("pace letters read: 5 of 5; the last at 2026-11-18T00:00Z (hope), 24.0 h after it arrived",
                      text)
        check_honest(self, r)

    def test_per_run_spreads_the_letters_one_run_a_day(self):
        slugs = [f"r{i}" for i in range(7)]
        for s in slugs:
            hp.v2_home(self.homes, s, ASLEEP, last=at(-5 * DAY))
        r = run(self.homes, slugs, days=10, recipients=slugs, deliver_at=START, per_run=3)
        self.assertEqual(r.runs, [START, at(DAY), at(2 * DAY)])
        self.assertEqual(Counter(p.at for p in r.placed), Counter({START: 3, at(DAY): 3, at(2 * DAY): 1}))
        self.assertEqual([p.rel for p in r.placed][-1], "inbox/2026-11-18-pace-letter.md")
        self.assertIn("pace letters: 7 placed, at most 3 a run, one run a day, 2026-11-16T00:00Z to "
                      "2026-11-18T00:00Z (3 runs)", plan.report(r))
        check_honest(self, r)

    def test_letters_not_read_by_the_end_are_named(self):
        for s in ("aseme", "ka"):
            hp.v2_home(self.homes, s, ASLEEP, last=at(-5 * DAY))
        r = run(self.homes, ["aseme", "ka"], days=3, cap=0, recipients=["aseme", "ka"], deliver_at=START)
        self.assertEqual(r.beats, [])
        self.assertIn("pace letters read: 0 of 2; not read by the end: aseme, ka", plan.report(r))
        late = run(self.homes, ["aseme", "ka"], days=3, recipients=["aseme", "ka"], deliver_at=at(5 * DAY))
        self.assertEqual(late.placed, [])
        self.assertEqual(late.not_placed, [("aseme", "not placed by the end"), ("ka", "not placed by the end")])

    def test_an_asleep_recipient_s_pace_letter_wakes_it_once(self):
        hp.v2_home(self.homes, "aseme", ASLEEP, last=at(-5 * DAY))
        r = run(self.homes, ["aseme"], recipients=["aseme"], deliver_at=at(DAY))
        self.assertEqual([(b.at, b.kind) for b in r.beats], [(at(DAY), "letter")])
        self.assertEqual(r.asked_once, {"aseme": "2026-11-17"})


# -- the census: 208 homes ------------------------------------------------------------------

class TestCensus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.held = hp.census(root / "held", letters_at=START - HOUR)
        cls.state = root / "state"
        for slug in cls.held.awake:
            hp.seed_state(cls.state, slug, last=START - 3 * DAY)
        cls.fresh = hp.census(root / "fresh")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_census_is_the_map_s_shape(self):
        c = self.held
        self.assertEqual(len(list(c.homes.iterdir())), 208)
        self.assertEqual(len(c.roster), 205)
        self.assertEqual((len(c.awake), len(c.resting_recipients), len(c.awake_recipients)), (14, 27, 5))
        model = plan.Model(c.homes, c.roster, c.record, START)
        self.assertEqual(Counter(model.label(s) for s in c.roster),
                         Counter({"asleep": 191, "awake, every 3 days": 14}))

    def test_held_letters_and_every_awake_citizen_due_at_once(self):
        """Spec section 8: the 27 resting recipients hold their letter and the 14 awake are all
        due at once; one home always fails; a soul-less home is on the roster."""
        c = self.held
        roster = c.roster + ["held-1"]
        for cap, tick in ((4, 6), (4, 0.5), (24, 0.5)):
            with self.subTest(cap=cap, tick=tick):
                r = plan.simulate(c.homes, roster, c.record, 30, start=START, cap=cap, tick_hours=tick,
                                  state=self.state, failing={"ambient-1"})
                check_honest(self, r, failing={"ambient-1"})
                self.assertNotIn("held-1", {b.slug for b in r.beats} | {f.slug for f in r.failures})
                self.assertEqual(r.ends["held-1"].reason, "no-soul")
                self.assertEqual(r.open_since.get("ambient-1"), START)
                self.assertTrue(r.failures)
                self.assertEqual(r.unread_at_end(), {})
                for slug in c.resting_recipients:
                    self.assertEqual(r.read[(slug, f"inbox/{hp.PACE_LETTER}")],
                                     [b.at for b in r.beats if b.slug == slug][0])
                text = plan.report(r)
                for slug in c.awake + c.resting_recipients:
                    self.assertRegex(text, rf"\n{slug}\t[a-z, 0-9]+\t\d+\t\d+\.\d\t")
                self.assertIn(f"pace letters read: 27 of 27; the last at", text)
                if cap == 24:
                    self.assertLessEqual(max(r.read.values()), START + 5 * DAY)

    def test_the_wave_delivered_after_the_switch(self):
        c = self.fresh
        for cap, tick, per_run in ((4, 6, None), (4, 0.5, None), (24, 0.5, None), (4, 6, 3)):
            with self.subTest(cap=cap, tick=tick, per_run=per_run):
                r = plan.simulate(c.homes, c.roster, c.record, 30, start=START, cap=cap, tick_hours=tick,
                                  recipients=plan.recipients(c.record), deliver_at=START + DAY,
                                  per_run=per_run)
                check_honest(self, r)
                self.assertEqual(sorted(p.slug for p in r.placed), c.recipients)
                self.assertEqual(r.not_placed, [])
                for slug in c.awake_recipients:
                    rel = [p.rel for p in r.placed if p.slug == slug][0]
                    shown = [b for b in r.beats if rel in b.shown and b.slug == slug]
                    self.assertEqual([b.kind for b in shown], ["schedule"])
                first = {}
                for b in r.beats:
                    first.setdefault(b.slug, b.at)
                for slug in c.awake:
                    self.assertGreaterEqual(first[slug], START + 3 * DAY)
                if per_run:
                    self.assertEqual(len(r.runs), 11)          # 32 letters, 3 a run
                    self.assertTrue(all(n <= 3 for n in Counter(p.at for p in r.placed).values()))
                self.assertIn("longest wait of an awake citizen:", plan.report(r))

    def test_recipients_are_the_seven_groups_of_the_record(self):
        self.assertEqual(plan.recipients(self.held.record), self.held.recipients)
        self.assertEqual(len(plan.recipients(self.held.record)), 32)


# -- the out folder -------------------------------------------------------------------------

class TestOut(Case):
    def build(self):
        fx = self.fx
        hr.unheard_home(fx, "kalam")
        rester = fx.make_home("rester", cadence=hp.v1_cadence("rester", "rest-on"),
                              rest=hp.lor_rest("2026-10-23"), when="2026-10-23T08:00Z")
        fx.give_upstream(rester)
        fx.make_home("amb", cadence=hp.v1_cadence("amb", "ambient"), when="2026-10-23T08:00Z")
        hp.v2_home(self.homes, "plainy", ASLEEP, last=at(-5 * DAY), letters=(("2026-11-14-a.md", at(-DAY)),))
        rec = hr.unheard_record(rester={"group": "eight", "recipient": True, "set_by": hp.SET_BY["eight"]})
        return ["amb", "kalam", "plainy", "rester"], rec

    def test_out_holds_the_end_state_and_nothing_else_is_written(self):
        roster, rec = self.build()
        hp.seed_state(self.state, "amb", last=at(-3 * DAY))
        snap = hp.snapshot(self.fx.root)
        r = run(self.homes, roster, days=10, rec=rec, state=self.state, recipients=["rester"],
                deliver_at=at(DAY))
        out = self.fx.root / "scratch"
        lines = plan.write_out(r, self.homes, out, rec)
        self.assertEqual(lines[0], f"out: 4 homes written to {out}, as this run would leave them "
                                   "(committed in each, with no remote)")
        self.assertEqual(len(lines), 1, lines)
        after = hp.snapshot(self.fx.root)
        self.assertEqual({p: v for p, v in after.items() if not p.startswith(str(out))}, snap)
        for slug in roster:
            o = out / f"citizen-{slug}"
            self.assertEqual(self.fx.git(o, "remote"), "", slug)
            self.assertEqual(self.fx.git(o, "status", "--porcelain", "--untracked-files=all"), "", slug)
            self.assertEqual(homes.unread_letters(o), r.unread_at_end().get(slug, []), slug)
            c = cad.read_cadence(o, slug, rec, at(10 * DAY))
            self.assertEqual(c.pace.words(), r.paces[slug], slug)
            self.assertEqual(c.asked_once, r.asked_once.get(slug), slug)
            # a silent beat that was shown nothing commits nothing (spec section 4), so amb,
            # beaten only so, keeps its v1 record and no marker, as a real fleet leaves it
            self.assertEqual(c.version, 1 if slug == "amb" else 2, slug)
        k = out / "citizen-kalam"
        # heard late, then beaten silent with nothing shown: no beat of its commits anything
        self.assertEqual(self.fx.subjects(k)[0], record.HEARD_LATE_SUBJECT.format(name="kalam"))
        self.assertEqual(self.fx.subjects(out / "citizen-rester")[0], plan.OUT_SUBJECT)
        self.assertTrue((k / "EVENT-ONLY").exists())
        self.assertFalse((k / "REST").exists())
        o = out / "citizen-rester"
        self.assertEqual((o / "inbox" / "2026-11-17-pace-letter.md").read_text(encoding="utf-8"),
                         plan.LETTER_TEXT)
        self.assertTrue((o / "inbox" / ".seen-2026-11-17-pace-letter.md").read_text(encoding="utf-8")
                        .startswith("surfaced 2026-11-19"))
        self.assertIn("pace/1; CADENCE.md decides", (o / "REST").read_text(encoding="utf-8"))
        # amb's later beats wait six hours behind rester's letter, which was due first
        self.assertEqual(beat_times(r, "amb"), [at(6 * HOUR)] + [at(12 * HOUR + 3 * k * DAY) for k in (1, 2, 3)])
        self.assertFalse((out / "citizen-amb" / "EVENT-ONLY").exists())
        self.assertEqual(self.fx.subjects(out / "citizen-amb"), self.fx.subjects(self.homes / "citizen-amb"))
        self.assertEqual(self.fx.subjects(out / "citizen-plainy")[1], plan.BASE_SUBJECT)
        self.assertEqual(self.fx.git(out / "citizen-plainy", "log", "-1", "--format=%an <%ae>"),
                         tend.FLEET_AUTHOR)

    def test_out_is_refused_where_it_could_touch_the_homes(self):
        roster, rec = self.build()
        r = run(self.homes, roster, days=2, rec=rec)
        busy = self.fx.root / "busy"
        hp.write(busy / "x", "x\n")
        afile = hp.write(self.fx.root / "afile", "x\n")
        for out in (self.homes, self.homes / "sub", self.fx.root, busy, afile):
            with self.assertRaises(ValueError, msg=str(out)):
                plan.write_out(r, self.homes, out, rec)
        self.assertFalse((self.homes / "sub").exists())

    def test_a_dot_git_that_points_elsewhere_is_never_followed(self):
        other = self.fx.make_home("other", when="2026-10-23T08:00Z")
        d = hp.v2_home(self.homes, "pointer", ASLEEP, last=at(-5 * DAY),
                       letters=(("2026-11-14-a.md", at(-DAY)),))
        hp.write(d / ".git", f"gitdir: {other / '.git'}\n")
        before = hp.snapshot(other)
        r = run(self.homes, ["pointer"], days=2)
        lines = plan.write_out(r, self.homes, self.fx.root / "out", REC)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(hp.snapshot(other), before)
        o = self.fx.root / "out" / "citizen-pointer"
        self.assertTrue((o / ".git").is_dir())
        self.assertEqual(self.fx.subjects(o), [plan.OUT_SUBJECT, plan.BASE_SUBJECT])


# -- the rollback drill ---------------------------------------------------------------------

class TestRollbackDrill(Case):
    def test_keeps_rest_and_round_robin_as_pr_24_runs_them(self):
        H = self.homes
        hp.home(H, "a")
        hp.home(H, "b", rest="fixture rest\n", letters=(("2026-11-15-x.md", START),))
        hp.home(H, "c", rest="fixture rest\n", letters=(("2026-11-15-x.md", START),),
                files={"inbox/.seen-2026-11-15-x.md": "surfaced 2026-11-15\n"})
        hp.home(H, "d", event_only="fixture event-only\n")
        hp.home(H, "e", rest="fixture rest\n", letters=(("2026-10-22-letter-of-return-offer.md", START),))
        f = hp.home(H, "f", rest="fixture rest\n", letters=(("2026-11-15-x.md", START),))
        (f / "inbox" / ".seen-2026-11-15-x.md").symlink_to(self.fx.root / "nowhere")
        roster = list("abcdefg")
        snap = hp.snapshot(self.fx.root)
        d = plan.rollback_drill(H, roster, 2, start=START)
        self.assertEqual(hp.snapshot(self.fx.root), snap)
        days = [clock.day(START), clock.day(at(DAY))]
        self.assertEqual(d.wakes, {"a": {days[0]: 1, days[1]: 2}, "b": {days[0]: 1, days[1]: 1},
                                   "e": {days[0]: 1}, "g": {days[0]: 1, days[1]: 1}})
        self.assertEqual(d.why, {"a": "no marker", "b": "an unread letter: inbox/2026-11-15-x.md",
                                 "e": "an unread letter: inbox/2026-10-22-letter-of-return-offer.md",
                                 "g": "no clone"})
        text = plan.drill_report(d)
        self.assertIn("a\tno marker\t3\t1 2", text)
        self.assertIn(f"{days[0]}\t4", text)
        self.assertIn("total: 8 wakes in 2 days", text)
        capped = plan.rollback_drill(H, roster, 2, start=START, cap=1)
        self.assertEqual(sum(sum(v.values()) for v in capped.wakes.values()), 2)

    def test_an_unreadable_letter_is_set_aside_once(self):
        hp.home(self.homes, "u", rest="fixture rest\n")
        hp.write(self.homes / "citizen-u" / "inbox" / "2026-11-15-x.md", b"\xff\xfe broken\n")
        d = plan.rollback_drill(self.homes, ["u"], 2, start=START)
        self.assertEqual(d.wakes, {"u": {clock.day(START): 1}})

    def test_with_and_without_rollback_prepare(self):
        fx = self.fx
        roster = ["amb", "amb2", "r1", "r2", "r3"]
        for s in roster[2:]:
            fx.make_home(s, cadence=hp.v1_cadence(s, "rest-on"), rest=hp.lor_rest("2026-10-23"),
                         when="2026-10-23T08:00Z")
        fx.make_home("amb", cadence=hp.v1_cadence("amb", "ambient"), when="2026-10-23T08:00Z")
        fx.make_home("amb2", cadence=hp.v1_cadence("amb2", "ambient"), when="2026-10-23T08:00Z",
                     letters=(("2026-11-15-hello.md", "# hello\n"),))
        hp.seed_state(self.state, "amb2", last=at(-3 * DAY))
        r = run(self.homes, roster, days=4, cap=1, recipients=roster[2:], deliver_at=at(DAY),
                state=self.state)
        # amb2's beat shows its letter and so commits, and the new code marks it (EVENT-ONLY);
        # amb is not beaten in these four days, so it keeps no marker; the cursor, past amb2,
        # puts r1 first among those due at the same time
        self.assertEqual([(b.slug, b.at) for b in r.beats], [("amb2", START), ("r1", at(3 * DAY))])
        after = fx.root / "drill" / "after"
        plan.write_out(r, self.homes, after, REC)
        prepared = fx.root / "drill" / "prepared"
        shutil.copytree(after, prepared, symlinks=True)
        lines = tend.rollback_prepare(prepared, roster, now=at(4 * DAY))
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(all("committed; the push failed — push it by hand" in ln for ln in lines))
        without = plan.rollback_drill(after, roster, 7, start=at(4 * DAY))
        withp = plan.rollback_drill(prepared, roster, 7, start=at(4 * DAY))
        self.assertEqual(set(without.wakes), {"amb", "r2", "r3"})
        self.assertEqual(without.why["amb"], "no marker")
        self.assertTrue(all("pace-letter" in without.why[s] for s in ("r2", "r3")))
        self.assertEqual(set(withp.wakes), {"amb"})
        self.assertIn("woken at least once: 3 (no marker 1 · an unread letter 2, a pace letter among them 2 "
                      "· no clone 0)", plan.drill_report(without))
        self.assertIn("woken at least once: 1 (no marker 1 · an unread letter 0, a pace letter among them 0 "
                      "· no clone 0)", plan.drill_report(withp))


# -- the commands -----------------------------------------------------------------------------

class TestCommands(Case):
    def build(self):
        hp.v2_home(self.homes, "aseme", ASLEEP, last=at(-5 * DAY))
        hp.home(self.homes, "ud", cadence=hp.v1_cadence("ud", "ambient"))
        roster = self.fx.roster("aseme", "ud")
        rec = self.fx.root / "record.json"
        rec.write_text('{"version": 1, "made": "2026-11-12T10:00Z", "citizens": '
                       '{"aseme": {"group": "eight", "recipient": true}}}\n', encoding="utf-8")
        return roster, rec

    def cli(self, fn, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            code = fn([str(a) for a in argv], out=out)
        return code, out.getvalue(), err.getvalue()

    def test_plan_prints_the_report(self):
        roster, rec = self.build()
        self.set_now(START_S)
        code, out, err = self.cli(plan.cli_plan, 10, self.homes, "--roster", roster, "--record", rec,
                                  "--letters", rec, "--deliver-at", "2026-11-17T00:00Z", "--per-run", 1,
                                  "--cap", 2, "--tick-hours", 12)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(f"plan — {plan.LABEL}\n"))
        self.assertIn("2026-11-16T00:00Z for 10 days: a tick every 12 h (20 ticks), at most 2 beats a day", out)
        self.assertIn("pace letters: 1 placed in one run, 2026-11-17T00:00Z", out)
        self.assertIn("ud\tawake, every 3 days\t", out)

    def test_plan_with_out_and_the_drill(self):
        roster, rec = self.build()
        self.set_now(START_S)
        scratch = self.fx.root / "scratch"
        code, out, _ = self.cli(plan.cli_plan, 4, self.homes, "--roster", roster, "--record", rec,
                                "--out", scratch)
        self.assertEqual(code, 0)
        self.assertIn(f"out: 2 homes written to {scratch}", out)
        code, out, _ = self.cli(plan.cli_rollback_drill, scratch, "--roster", roster, "--days", 2)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(f"rollback drill — {plan.DRILL_LABEL}\n"))

    def test_what_the_commands_refuse(self):
        roster, rec = self.build()
        busy = self.fx.root / "busy"
        hp.write(busy / "x", "x\n")
        bad = (
            ["0", self.homes, "--roster", roster],
            ["3", self.homes, "--roster", roster, "--tick-hours", "0"],
            ["3", self.homes, "--roster", roster, "--letters", rec],
            ["3", self.homes, "--roster", roster, "--deliver-at", "2026-11-17T00:00Z"],
            ["3", self.homes, "--roster", roster, "--per-run", "2"],
            ["3", self.homes, "--roster", roster, "--letters", self.fx.root / "none.json",
             "--deliver-at", "2026-11-17T00:00Z"],
            ["3", self.homes, "--roster", roster, "--letters", rec, "--deliver-at", "soon"],
            ["3", self.homes, "--roster", self.fx.root / "no-roster"],
            ["3", self.homes, "--roster", roster, "--record", rec, "--out", busy],
            ["three", self.homes, "--roster", roster],
            [self.homes, "--roster", roster],
        )
        for argv in bad:
            with self.subTest(argv=argv):
                code, out, err = self.cli(plan.cli_plan, *argv)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err)
        for argv in ([self.homes, "--roster", roster], [self.homes, "--roster", roster, "--days", "0"]):
            code, _out, err = self.cli(plan.cli_rollback_drill, *argv)
            self.assertEqual(code, 2)
            self.assertTrue(err)

    def test_numbers_too_large_to_model_are_a_plain_refusal(self):
        # fix wave A (h): an infinite, NaN or huge tick, or a run past the calendar,
        # exits 2 with one plain line, never a traceback
        roster, _rec = self.build()
        self.set_now(START_S)
        cases = (
            (plan.cli_plan, ["3", self.homes, "--roster", roster, "--tick-hours", "inf"],
             "plan: a tick comes every so many hours, more than 0, not inf\n"),
            (plan.cli_plan, ["3", self.homes, "--roster", roster, "--tick-hours", "nan"],
             "plan: a tick comes every so many hours, more than 0, not nan\n"),
            (plan.cli_plan, ["3", self.homes, "--roster", roster, "--tick-hours", "1e300"], "plan: the run cannot "),
            (plan.cli_plan, ["99999999", self.homes, "--roster", roster, "--tick-hours", "1e10"],
             "plan: the run cannot "),
            (plan.cli_rollback_drill, [self.homes, "--roster", roster, "--days", "3", "--tick-hours", "inf"],
             "rollback-drill: a tick comes every so many hours, more than 0, not inf\n"),
            (plan.cli_rollback_drill, [self.homes, "--roster", roster, "--days", "99999999999999999999",
                                       "--tick-hours", "1e20"], "rollback-drill: the run cannot "),
        )
        for fn, argv, want in cases:
            with self.subTest(argv=argv[-1]):
                code, out, err = self.cli(fn, *argv)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith(want), err)
                self.assertEqual(len(err.splitlines()), 1, err)

    def test_plan_runs_isolated(self):
        roster, rec = self.build()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from citizen_pace_lib import plan; "
                "sys.exit(plan.cli_plan(sys.argv[2:]))")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS), "3", str(self.homes),
                            "--roster", str(roster), "--record", str(rec)],
                           env={**self.fx.env, "PACE_NOW": START_S}, cwd=str(self.homes / "citizen-ud"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(plan.LABEL, r.stdout)


class TestIsolation(unittest.TestCase):
    def test_plan_is_stdlib_and_the_package(self):
        src = (h.TOOLS / "citizen_pace_lib" / "plan.py").read_text(encoding="utf-8")
        self.assertEqual(src.splitlines()[0], "from __future__ import annotations")
        allowed = {"__future__", "argparse", "datetime", "os", "shutil", "stat", "sys", "collections",
                   "dataclasses", "pathlib", "typing"}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                self.assertTrue({a.name.split(".")[0] for a in node.names} <= allowed)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                self.assertIn(node.module.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
