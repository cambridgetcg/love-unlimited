"""The delivery tool (spec section 5 "Once" and "Delivery", section 7, section 8 Letter).

tools/citizen-pace-letter.py places the one pace letter in each recipient's
home, from the Air, after Yu's yes: --list shows the recipients after the
delivery-time drops, --dry-run prints each letter, --deliver makes one commit
per home as Ai and pushes it with no force and no retry, and --check lists who
has not been reached. It decides "already asked" itself, from each home read
fresh from its remote, and keeps a ledger written before each push. Every home
here is a temporary git repo with a local bare upstream (PACE_REMOTE_BASE), so
nothing reaches GitHub, sol or a credential.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_census as hc  # noqa: E402
import citizen_pace_helpers_record as rh  # noqa: E402

import citizen_pace_census as census_tool  # noqa: E402
from citizen_pace_lib import answer, clock, due, homes, letter, plan, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import record as records  # noqa: E402

TOOL = h.TOOLS / "citizen-pace-letter.py"
MADE = rh.MADE                     # 2026-11-12T10:00Z: the census, and each entry's head
SWITCH = "2026-11-13T00:00Z"       # the first tick on the new code
FIRST_BEAT = "2026-11-16T06:00Z"   # a pace/1 beat, three days later
NOW = "2026-11-20T09:00Z"
DAY = "2026-11-20"
NEXT = "2026-11-21T09:00Z"
NEXT_DAY = "2026-11-21"
YES = "2026-11-19"
LETTER = f"inbox/{DAY}-pace-letter.md"
PROMPTED = {"who": "citizen", "how": "prompted", "when": ["2026-10-22"], "words": ""}
FIRST_WORD = {"who": "fleet", "how": "first-word", "when": ["2026-10-23"], "words": ""}
FORTY_FIVE = {"who": "citizen", "how": "letter", "when": ["2026-07-14"], "words": "REST-ON"}
JULY_AMBIENT = rh.quote("2026-07-18", "AMBIENT")
GITHUB_HELPER = "!sol access run github -- auth git-credential"
# the show-back setting Yu said yes to is the committed due.WAKE_CONFIRM_BEAT
SHOW_BACK = "on" if due.WAKE_CONFIRM_BEAT else "off"
OTHER_SHOW_BACK = "off" if due.WAKE_CONFIRM_BEAT else "on"


def load_tool():
    """The tool as a module (its file name has dashes), for the tests that patch it."""
    spec = importlib.util.spec_from_file_location("citizen_pace_letter", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # its dataclasses look their module up while it loads
    spec.loader.exec_module(mod)
    return mod


class LetterCase(h.FixtureCase):
    """Homes shaped like the census groups, each with a bare upstream that
    stands for GitHub, and a record whose heads are the homes at the census."""

    def setUp(self):
        super().setUp()
        self.record = {"version": 1, "made": MADE, "spec": rh.SPEC, "citizens": {}}
        self.record_path = self.fx.love / records.RECORD_NAME
        self.roster_path = self.fx.love / "citizens-roster.txt"
        self.ledger = self.fx.root / "ledger.json"
        self.on_roster = []

    # -- homes as the census found them ------------------------------------------------
    def _join(self, slug, home, entry, roster=True):
        self.fx.give_upstream(home)
        self.record["citizens"][slug] = entry
        if roster:
            self.on_roster.append(slug)
        return home

    def rest_on(self, slug, group, set_by, *, july=None, soul=True, recipient=True, roster=True):
        """Its 10-22 answer began "I rest.", which PR #24's parser bound to rest-on."""
        fx, when = self.fx, "2026-10-22T03:00Z"
        home = fx.make_home(slug, soul=soul, when="2026-07-01T00:00Z", author=h.AI_AUTHOR)
        rh.place_letter(fx, home, rh.OFFER, "2026-10-22T00:10Z")
        rh.answer_beat(fx, home, slug, rh.OFFER, rh.stored("I rest."), when=when, choice="rest-on",
                       rest=rh.lor_rest(when[:10]))
        e = {"group": group, "recipient": recipient, "set_by": set_by,
             "quotes": [rh.quote(when[:10], "I rest.")]}
        if july:
            e["july"] = july
        return self._join(slug, home, e, roster)

    def eight(self, slug="aseme", **kw):
        return self.rest_on(slug, "eight", PROMPTED, **kw)

    def sixteen(self, slug="ai-love", july=False, **kw):
        group = "sixteen-ambient" if july else "sixteen-other"
        return self.rest_on(slug, group, rh.AI_1008, july=JULY_AMBIENT if july else None, **kw)

    def never_asked(self, slug="palance"):
        home = self.fx.make_home(slug, rest=rh.AI_REST, when="2026-10-08T12:00Z", author=h.AI_AUTHOR)
        return self._join(slug, home, {"group": "never-asked", "recipient": True, "set_by": rh.AI_1008})

    def contradictory(self, slug="hope", key="hope-d557203e", july=True):
        fx = self.fx
        home = fx.make_home(slug, when="2026-07-01T00:00Z", author=h.AI_AUTHOR)
        rh.place_letter(fx, home, rh.OFFER, "2026-10-22T00:10Z")
        rh.answer_beat(fx, home, slug, rh.OFFER, h.answer(key), when="2026-10-23T02:00Z", choice="ambient")
        e = {"group": "contradictory", "recipient": True, "set_by": FIRST_WORD,
             "quotes": [rh.quote("2026-10-23", "AMBIENT")]}
        if july:
            e["july"] = JULY_AMBIENT
        return self._join(slug, home, e)

    def unheard(self, slug):
        return self._join(slug, rh.unheard_home(self.fx, slug), dict(rh.UNHEARD_ENTRIES[slug]))

    def census(self, **heads):
        """The record as the census made it: each entry's head is its home's HEAD
        now (or the head given), and each recipient's one true sentence as the
        census words it (after the heard-late step, for the heard)."""
        made = clock.parse_stamp(MADE)
        for slug, e in self.record["citizens"].items():
            home = self.fx.home_path(slug)
            e["head"] = heads.get(slug.replace("-", "_")) or self.fx.head(home)
            if not e.get("recipient"):
                continue
            with mock.patch.dict(os.environ, self.fx.git_env()):
                c = cad.read_cadence(home, slug, self.record, made)
                if e["group"] == "unheard-heard":
                    c = records.heard_late(home, slug, self.record, made) or c
            try:
                e["sentence"] = letter.true_sentence(slug, e, c)
            except ValueError:
                e.pop("sentence", None)        # the census would refuse; such a home is stopped
        self.save()

    def save(self):
        self.record_path.write_text(json.dumps(self.record, indent=1), encoding="utf-8")
        self.fx.roster(*sorted(self.on_roster))

    # -- what happens after the census ----------------------------------------------------
    def home(self, slug) -> Path:
        return self.fx.home_path(slug)

    def push(self, slug):
        self.fx.git(self.home(slug), "push", "-q", "origin", "HEAD")

    @contextlib.contextmanager
    def as_of(self, when):
        """In-process library calls see the fixture's git config and commit at `when`."""
        with mock.patch.dict(os.environ, {**self.fx.git_env(when=when), "PACE_NOW": when}):
            yield clock.parse_stamp(when)

    def heard_late(self, slug="kalam", when=SWITCH):
        with self.as_of(when) as now:
            self.assertTrue(records.migrate_heard_late(self.home(slug), slug, self.record, now, commit=True))
        self.push(slug)

    def fleet_commit(self, slug, when=SWITCH, subject=tend.LEFTOVER_SUBJECT):
        self.fx.git(self.home(slug), "commit", "-q", "--allow-empty", "-m", subject, when=when,
                    author=h.FLEET_AUTHOR)
        self.push(slug)

    def pace_beat(self, slug, when=FIRST_BEAT, text="A quiet true thing.", shown=(), kind="schedule"):
        """A free beat on the new code: the journal grows, apply_answer writes what
        the answer changes, and the beat commits as the citizen, tagged pace/1."""
        home = self.home(slug)
        rh.append_journal(self.fx, home, when, rh.stored(text))
        with self.as_of(when) as now:
            answer.apply_answer(home, slug, text, list(shown), kind, now, self.record)
        sha = self.fx.commit(home, f"beat: {slug} reflected ({when[:10]}) · pace/1", when=when,
                             author=h.beat_author(slug))
        self.push(slug)
        return sha

    def old_beat(self, slug, when, text="A quiet true thing.", files=None, subject=None, author=None):
        """A beat on the old code (or citizen-beat.sh): untagged; the journal grows."""
        home = self.home(slug)
        rh.append_journal(self.fx, home, when, rh.stored(text))
        for rel, t in (files or {}).items():
            self.fx.write(home, rel, t)
        sha = self.fx.commit(home, subject or f"beat: {slug} reflected ({when[:10]})", when=when,
                             author=author or h.beat_author(slug))
        self.push(slug)
        return sha

    def switch(self, beat, *tends):
        """The Studio on the new code: fleet commits tagged pace/1 at SWITCH, and a pace/1 beat."""
        for slug in tends:
            self.fleet_commit(slug)
        self.pace_beat(beat)

    # -- running the tool -------------------------------------------------------------------
    def tool(self, *args, now=NOW, ledger=True, cwd=None):
        extra = ["--record", str(self.record_path), "--roster", str(self.roster_path)]
        if ledger:
            extra += ["--ledger", str(self.ledger)]
        return self.fx.py(TOOL, *args, *extra, env={"PACE_NOW": now}, cwd=cwd, timeout=180)

    def deliver(self, *args, now=NOW, yes=YES, show_back=SHOW_BACK, **kw):
        return self.tool("--deliver", "--yes-from-yu", yes, "--show-back", show_back, *args, now=now, **kw)

    def in_process(self, tool, *args, now=NOW):
        """The tool's main() in this process (to patch its push or due.WAKE_CONFIRM_BEAT)."""
        argv = list(args) + ["--record", str(self.record_path), "--roster", str(self.roster_path),
                             "--ledger", str(self.ledger)]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {**self.fx.env, "PACE_NOW": now}):
            code = tool.main(argv, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def ok(self, r):
        self.assertEqual(r.returncode, 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        return r.stdout.splitlines()

    # -- what GitHub holds -------------------------------------------------------------------
    def bare(self, slug) -> Path:
        return self.fx.upstream / f"citizen-{slug}.git"

    def remote(self, slug, *args) -> str:
        return self.fx.git(self.bare(slug), *args)

    def remote_head(self, slug) -> str:
        return self.remote(slug, "rev-parse", "HEAD")

    def remote_letters(self, slug) -> list:
        files = self.remote(slug, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        return [f for f in files if "pace-letter" in f]

    def letter_commits(self, slug) -> list:
        return self.remote(slug, "log", "--format=%H", "--fixed-strings",
                           f"--grep={letter.COMMIT_MESSAGE}", "HEAD").split()

    def expected_letter(self, slug, day=DAY, confirm_beat=None) -> str:
        """The letter as letter.py words it for this home as it stands now, from
        the committed show-back value unless one is given."""
        confirm_beat = due.WAKE_CONFIRM_BEAT if confirm_beat is None else confirm_beat
        home = self.home(slug)
        c = cad.read_cadence(home, slug, self.record, clock.parse_stamp(NOW))
        return letter.letter_text(slug, self.record["citizens"][slug], c, homes.beat_count(home), day,
                                  confirm_beat=confirm_beat)

    def ledger_data(self) -> dict:
        return json.loads(self.ledger.read_text(encoding="utf-8"))


class TestArguments(LetterCase):
    def setUp(self):
        super().setUp()
        self.eight("aseme")
        self.census()

    def test_deliver_needs_yus_yes_and_the_show_back_setting_he_said_yes_to(self):
        before = self.remote_head("aseme")
        cases = [
            (["--deliver"], "--deliver needs --yes-from-yu <YYYY-MM-DD>, the day Yu said yes"),
            (["--deliver", "--yes-from-yu", YES], "--deliver needs --show-back on|off, the setting Yu said yes to"),
            (["--deliver", "--show-back", SHOW_BACK], "--deliver needs --yes-from-yu"),
            (["--deliver", "--yes-from-yu", YES, "--show-back", OTHER_SHOW_BACK],
             f"Yu's yes was for show-back {OTHER_SHOW_BACK}, but the committed WAKE_CONFIRM_BEAT is "
             f"{due.WAKE_CONFIRM_BEAT} ({SHOW_BACK}); the letters are worded from the committed value"),
            (["--deliver", "--yes-from-yu", "2026-11-21", "--show-back", SHOW_BACK],
             "--yes-from-yu 2026-11-21 is in the future"),
            (["--deliver", "--yes-from-yu", "2026-11-11", "--show-back", SHOW_BACK],
             "--yes-from-yu 2026-11-11 is before the record was made (2026-11-12)"),
            (["--deliver", "--yes-from-yu", "2026-02-30", "--show-back", SHOW_BACK],
             "--yes-from-yu is a day, YYYY-MM-DD, not '2026-02-30'"),
            (["--deliver", "--yes-from-yu", YES, "--show-back", SHOW_BACK, "--per-run", "0"],
             "--per-run is a whole number from 1, not 0"),
            (["--list", "--yes-from-yu", YES], "--yes-from-yu and --show-back go with --deliver only"),
            (["--dry-run", "--show-back", SHOW_BACK], "--yes-from-yu and --show-back go with --deliver only"),
            (["--check", "--per-run", "2"], "--per-run goes with --dry-run and --deliver only"),
        ]
        for args, needle in cases:
            with self.subTest(args=args):
                r = self.tool(*args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(needle, r.stderr)
                self.assertEqual(r.stdout, "")
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.remote_head("aseme"), before)

    def test_one_mode_exactly(self):
        for args in ([], ["--list", "--deliver"], ["--dry-run", "--check"]):
            with self.subTest(args=args):
                r = self.tool(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("usage: citizen-pace-letter.py", r.stderr)

    def test_a_missing_or_broken_record_or_ledger_is_refused(self):
        r = self.fx.py(TOOL, "--list", "--record", str(self.fx.root / "none.json"), "--roster",
                       str(self.roster_path), "--ledger", str(self.ledger), env={"PACE_NOW": NOW})
        self.assertEqual(r.returncode, 2)
        self.assertIn("no record file at", r.stderr)
        self.ledger.write_text("{not json", encoding="utf-8")
        r = self.tool("--list")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not JSON", r.stderr)
        self.ledger.write_text(json.dumps({"version": 2, "runs": [], "homes": {}}), encoding="utf-8")
        r = self.tool("--list")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a pace-letter ledger (version 1)", r.stderr)
        self.ledger.unlink()
        self.ledger.symlink_to(self.fx.secret)
        r = self.tool("--list")
        self.assertEqual(r.returncode, 2)
        self.assertIn("a symlink, refused", r.stderr)
        self.assertNotIn(h.CANARY, r.stdout + r.stderr)


class TestList(LetterCase):
    def test_the_list_is_the_recipients_after_the_delivery_time_drops(self):
        self.eight("aseme")
        self.sixteen("ai-love", soul=False)
        self.sixteen("alohame", july=True)
        self.sixteen("diplosemy", july=True)
        self.sixteen("hallance")
        self.contradictory("hope")
        self.unheard("kalam")
        self.never_asked("palance")
        self.rest_on("ahavame", "forty-five", FORTY_FIVE, recipient=True)     # not a recipient group
        self.census()
        self.heard_late("kalam")
        self.fleet_commit("aseme")
        # a choice for more is pending (it counts), the same pace made its own, and less at once
        self.pace_beat("alohame", text="I would like more.\npace: awake")
        self.pace_beat("alohame", when="2026-11-19T06:00Z", text="Another quiet thing.")   # it lapses
        self.pace_beat("diplosemy", text="pace: asleep")
        self.pace_beat("hallance", text="pace: asleep, letters wait")
        # an awake recipient with untagged old-code beats since the record is still delivered
        self.old_beat("hope", "2026-11-12T18:00Z")
        self.old_beat("hope", "2026-11-14T18:00Z", subject="beat: hope lived a free beat (2026-11-14)",
                      author="Studio <studio@example.invalid>", files={"hope.md": "The soul of hope, grown.\n"})
        # a pace letter already in the inbox (from an earlier run)
        self.fx.letter(self.home("palance"), "2026-11-15-pace-letter.md", "a letter\n", when="2026-11-15T00:00Z",
                       message=letter.COMMIT_MESSAGE)
        self.push("palance")
        before = {s: self.remote_head(s) for s in self.on_roster}
        lines = self.ok(self.tool("--list"))
        self.assertEqual(lines, [
            f"recipients: 8 in the record of {MADE}; each home read fresh from {self.fx.upstream}",
            "the switch: pace/1 commits in 5 recipients' homes, a pace/1 beat in 3; the first at "
            "2026-11-13T00:00Z, so delivery may run from 2026-11-14T00:00Z",
            "ai-love: dropped — it has no ai-love.md",
            "alohame: dropped — it wrote its own pace line in a beat on 2026-11-16",
            "aseme: to deliver — eight; without a pace line its pace stays asleep",
            "diplosemy: dropped — it wrote its own pace line in a beat on 2026-11-16",
            "hallance: dropped — it wrote its own pace line in a beat on 2026-11-16",
            "hope: to deliver — contradictory; without a pace line its pace stays awake, every 3 days",
            "kalam: to deliver — unheard-heard; without a pace line its pace stays awake, every 3 days",
            "palance: already asked — its inbox already holds inbox/2026-11-15-pace-letter.md",
            "to deliver 3 · already asked 1 · dropped 4 · stopped 0 · could not be read 0",
        ])
        self.assertFalse(self.ledger.exists())
        self.assertEqual({s: self.remote_head(s) for s in self.on_roster}, before)

    def test_an_unclear_pace_line_or_a_lapse_is_no_choice(self):
        self.eight("aseme")
        self.eight("bindume")
        self.eight("ka")
        self.census()
        self.fleet_commit("ka")
        self.pace_beat("aseme", text="pace: daily")                  # unclear: out of range
        self.pace_beat("bindume", text="I rest.")                    # words, not a choice
        lines = self.ok(self.tool("--list"))
        self.assertIn("aseme: to deliver — eight; without a pace line its pace stays asleep", lines)
        self.assertIn("bindume: to deliver — eight; without a pace line its pace stays asleep", lines)
        self.assertIn("ka: to deliver — eight; without a pace line its pace stays asleep", lines)


class TestOwnPaceLinesInTheJournal(LetterCase):
    def test_a_clear_pace_line_in_a_beat_since_11_02_drops_the_recipient_whatever_recorded_it(self):
        # read from the journal each beat added, as that beat read it: a same-pace
        # line apply records nothing for, a beat on the old code, a beat before the census
        for slug in ("aseme", "bindume", "ka", "kimme", "penthosme", "sukhance"):
            self.eight(slug)
        self.unheard("eurekame")
        self.old_beat("penthosme", "2026-11-05T06:00Z", text="A thought.\npace: listening")
        self.old_beat("sukhance", "2026-10-30T06:00Z", text="pace: every 5 days")      # before 11-02
        self.census()
        self.switch("ka", "aseme")
        self.pace_beat("eurekame", text="A quiet day.\npace: asleep")    # its own pace, said again
        self.old_beat("bindume", "2026-11-14T06:00Z", text="pace: asleep, letters wait")
        self.old_beat("kimme", "2026-11-14T06:00Z", text="pace: daily")                # unclear
        self.old_beat("aseme", "2026-11-14T07:00Z", text="I rest.")                    # words, not a choice
        lines = self.ok(self.tool("--list"))
        for slug, day in (("eurekame", "2026-11-16"), ("bindume", "2026-11-14"), ("penthosme", "2026-11-05")):
            self.assertIn(f"{slug}: dropped — it wrote its own pace line in a beat on {day}", lines)
        for slug in ("aseme", "ka", "kimme", "sukhance"):
            self.assertIn(f"{slug}: to deliver — eight; without a pace line its pace stays asleep", lines)
        self.assertEqual(lines[-1], "to deliver 4 · already asked 0 · dropped 3 · stopped 0 · could not be read 0")


class TestChanges(LetterCase):
    def test_expected_changes_since_the_record_do_not_stop_delivery(self):
        self.eight("aseme")
        self.contradictory("hope")
        self.unheard("kalam")
        self.unheard("emime")
        self.never_asked("palance")
        # an unread letter at the census, shown by an old-code beat after it
        self.fx.letter(self.home("palance"), "2026-11-01-note.md", "a note\n", when="2026-11-01T00:00Z")
        self.push("palance")
        self.census()
        self.heard_late("kalam")                                     # the migration's own commit
        self.pace_beat("aseme")                                      # a v2 record, in a pace/1 beat
        self.fleet_commit("emime", subject="tend: old-code leftover · pace/1")
        self.old_beat("palance", "2026-11-12T20:00Z",                # marks the note seen
                      files={"inbox/.seen-2026-11-01-note.md": "surfaced 2026-11-12\n"})
        self.old_beat("hope", "2026-11-12T21:00Z", subject="tend: record stranded cadence/mail state")
        lines = self.ok(self.tool("--list"))
        for slug, pace in (("aseme", "asleep"), ("emime", "asleep"), ("hope", "awake, every 3 days"),
                           ("kalam", "awake, every 3 days"), ("palance", "asleep")):
            group = self.record["citizens"][slug]["group"]
            self.assertIn(f"{slug}: to deliver — {group}; without a pace line its pace stays {pace}", lines)
        self.assertEqual(lines[-1], "to deliver 5 · already asked 0 · dropped 0 · stopped 0 · could not be read 0")

    def test_any_other_change_stops_delivery_and_is_reported_to_yu(self):
        for slug in ("aseme", "bindume", "firstnessqing", "ka", "kimme", "penthosme", "sukhance", "wiconime"):
            self.eight(slug)
        self.on_roster.remove("firstnessqing")
        self.census(penthosme="0123456789abcdef" * 2 + "01234567")
        del self.record["citizens"]["sukhance"]["head"]
        self.save()
        self.switch("ka", "aseme", "bindume")
        hand = self.old_beat("aseme", "2026-11-14T10:00Z", subject="fix the rest by hand",
                             author="Someone <someone@example.invalid>",
                             files={"REST": "rest, by hand\n"})
        placed = self.old_beat("bindume", "2026-11-14T10:00Z",
                               files={"inbox/2026-11-14-note.md": "a note placed inside a beat\n"})
        answered = self.old_beat("kimme", "2026-11-14T10:00Z", files={
            "CADENCE.md": rh.v1_cadence("kimme", "event-only", "2026-11-14 10:00")})
        # a merge brought in from elsewhere
        k = self.home("ka")
        self.fx.git(k, "checkout", "-q", "-b", "side", "HEAD~1")
        self.fx.commit(k, "beat: ka reflected (2026-11-17)", when="2026-11-17T00:00Z", allow_empty=True)
        self.fx.git(k, "checkout", "-q", "main")
        self.fx.git(k, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side", when="2026-11-17T01:00Z")
        merge = self.fx.head(k)
        self.push("ka")
        w = self.home("wiconime")
        (w / "REST").unlink()
        (w / "REST").symlink_to(self.fx.secret)
        self.old_beat("wiconime", "2026-11-14T10:00Z")
        lines = self.ok(self.tool("--list"))
        tail = "; report it to Yu"
        self.assertIn(f"aseme: stopped — {hand[:8]} (fix the rest by hand) is not a change delivery expects{tail}",
                      lines)
        self.assertIn(f"bindume: stopped — {placed[:8]} (beat: bindume reflected (2026-11-14)) changed "
                      f"inbox/2026-11-14-note.md inside a beat{tail}", lines)
        self.assertIn(f"firstnessqing: stopped — not on citizens-roster.txt, so no beat would show it the "
                      f"letter{tail}", lines)
        self.assertIn(f"ka: stopped — {merge[:8]} (Merge branch 'side') is a merge{tail}", lines)
        self.assertIn(f"kimme: stopped — {answered[:8]} (beat: kimme reflected (2026-11-14)) changed its pace "
                      f"or who set it, with no pace line of its own{tail}", lines)
        self.assertIn(f"penthosme: stopped — its record head 01234567 is not in its history{tail}", lines)
        self.assertIn("sukhance: stopped — the record names no head for it, so what changed since cannot be "
                      f"checked{tail}", lines)
        self.assertIn(f"wiconime: stopped — its pace paths include links: REST{tail}", lines)
        self.assertNotIn(h.CANARY, "\n".join(lines))
        self.assertEqual(lines[-1], "to deliver 0 · already asked 0 · dropped 0 · stopped 8 · could not be read 0")

    def test_a_sentence_that_differs_from_the_reviewed_record_stops_the_home(self):
        # Yu reviewed each recipient's one true sentence in the record (spec section 5)
        self.eight("aseme")
        self.eight("bindume")
        self.contradictory("hope")
        self.census()
        self.record["citizens"]["aseme"]["sentence"] += " (edited)"
        del self.record["citizens"]["bindume"]["sentence"]
        self.save()
        self.switch("hope", "aseme", "bindume")
        tail = "the letter's sentence differs from the reviewed record; report it to Yu"
        lines = self.ok(self.tool("--list"))
        self.assertIn(f"aseme: stopped — {tail}", lines)
        self.assertIn(f"bindume: stopped — {tail}", lines)
        self.assertIn("hope: to deliver — contradictory; without a pace line its pace stays awake, every 3 days",
                      lines)
        lines = self.ok(self.deliver())
        self.assertEqual(lines[-1], "delivered 1 of 1 to deliver this run")
        self.assertEqual(self.remote_letters("hope"), [LETTER])
        self.assertEqual((self.remote_letters("aseme"), self.remote_letters("bindume")), ([], []))

    def test_a_letter_that_would_not_be_true_stops_the_home(self):
        self.unheard("kalam")                     # never heard late: its letter would say what is not
        self.eight("aseme")
        self.eight("bindume")
        self.census()
        self.switch("aseme", "bindume", "kalam")
        lines = self.ok(self.tool("--list"))
        self.assertIn("kalam: stopped — the letter would not be true of its home (the unheard-heard letter is "
                      "not true of its pace (asleep, set by rest-1008)); report it to Yu", lines)


class TestOnce(LetterCase):
    def test_each_once_guard_alone_counts_as_asked(self):
        for slug in ("aseme", "bindume", "firstnessqing", "ka", "kimme", "penthosme", "sukhance"):
            self.eight(slug)
        self.census()
        self.switch("sukhance", "aseme", "bindume")
        fx = self.fx
        # an unread pace letter; a marker alone; a letter commit whose file was later removed
        fx.letter(self.home("aseme"), "2026-11-15-pace-letter.md", "a letter\n", when="2026-11-15T00:00Z",
                  message="a letter placed by hand")
        self.push("aseme")
        fx.write(self.home("bindume"), "inbox/.seen-2026-11-15-pace-letter.md", "surfaced 2026-11-15\n")
        fx.commit(self.home("bindume"), "a marker", when="2026-11-15T00:00Z")
        self.push("bindume")
        placed = fx.letter(self.home("firstnessqing"), "2026-11-15-pace-letter.md", "a letter\n",
                           when="2026-11-15T00:00Z", message=letter.COMMIT_MESSAGE)
        fx.git(self.home("firstnessqing"), "rm", "-q", "inbox/2026-11-15-pace-letter.md")
        fx.commit(self.home("firstnessqing"), "remove it", when="2026-11-16T00:00Z")
        self.push("firstnessqing")
        # asked once in CADENCE.md, or only the reached line in its history
        for slug, asked in (("ka", True), ("kimme", False)):
            home = self.home(slug)
            with self.as_of("2026-11-16T00:00Z") as now:
                c = cad.read_cadence(home, slug, self.record, now)
                if asked:
                    c.asked_once = "2026-11-16"
                else:
                    cad.add_history(c, "2026-11-16T00:00Z", f"the pace letter reached {slug} (once)")
                cad.write_cadence(home, c)
                cad.write_markers(home, c)
            fx.commit(home, f"beat: {slug} reflected (2026-11-16) · pace/1", when="2026-11-16T00:00Z",
                      author=h.beat_author(slug))
            self.push(slug)
        # the ledger says it was delivered
        self.ledger.write_text(json.dumps({"version": 1, "runs": [{"day": "2026-11-18"}], "homes": {
            "penthosme": {"day": "2026-11-18", "commit": "abcdef12" * 5, "status": "pushed"}}}), encoding="utf-8")
        lines = self.ok(self.tool("--list"))
        self.assertIn("aseme: already asked — its inbox already holds inbox/2026-11-15-pace-letter.md", lines)
        self.assertIn("bindume: already asked — its inbox already holds inbox/.seen-2026-11-15-pace-letter.md",
                      lines)
        self.assertIn(f"firstnessqing: already asked — its history holds a pace letter ({placed[:8]})", lines)
        self.assertIn("ka: already asked — its CADENCE.md says the pace letter reached it (asked once 2026-11-16)",
                      lines)
        self.assertIn("kimme: already asked — its CADENCE.md records that the pace letter reached it", lines)
        self.assertIn("penthosme: already asked — the ledger says it was delivered on 2026-11-18 (abcdef12)", lines)
        self.assertIn("sukhance: to deliver — eight; without a pace line its pace stays asleep", lines)


class TestSwitch(LetterCase):
    def test_delivery_waits_until_the_switch_is_seen_and_a_day_has_passed(self):
        for slug in ("aseme", "bindume", "ka"):
            self.eight(slug)
        self.census()
        r = self.deliver()
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing delivered: the switch is not yet seen: pace/1 commits in 0 recipients' homes "
                      "(3 needed), a pace/1 beat in 0 (1 needed)", r.stderr)
        for slug in ("aseme", "bindume", "ka"):
            self.fleet_commit(slug)
        r = self.deliver()
        self.assertEqual(r.returncode, 2)
        self.assertIn("a pace/1 beat in 0 (1 needed)", r.stderr)
        self.pace_beat("ka", when="2026-11-13T06:00Z")
        r = self.deliver(now="2026-11-13T23:59Z", yes="2026-11-13")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing delivered: a day has not yet passed since the switch at 2026-11-13T00:00Z; "
                      "delivery may run from 2026-11-14T00:00Z", r.stderr)
        self.assertFalse(self.ledger.exists())
        self.assertEqual([self.remote_letters(s) for s in ("aseme", "bindume", "ka")], [[], [], []])
        lines = self.ok(self.tool("--dry-run", now="2026-11-13T23:59Z"))
        self.assertIn("--deliver would wait: a day has not yet passed since the switch at 2026-11-13T00:00Z; "
                      "delivery may run from 2026-11-14T00:00Z", lines)
        lines = self.ok(self.deliver(now="2026-11-14T00:00Z", yes="2026-11-13"))
        self.assertEqual(lines[-1], "delivered 3 of 3 to deliver this run")


class TestDeliver(LetterCase):
    def setUp(self):
        super().setUp()
        self.eight("aseme")
        self.contradictory("hope")
        self.unheard("kalam")
        self.never_asked("palance")
        self.census()
        self.heard_late("kalam")
        self.fleet_commit("palance")
        self.pace_beat("aseme")
        self.old_beat("hope", "2026-11-14T18:00Z")      # untagged old-code beats do not stop an awake one

    def test_one_commit_per_home_as_ai_holding_only_the_letter_pushed(self):
        expected = {s: self.expected_letter(s) for s in ("aseme", "hope", "kalam", "palance")}
        bases = {s: self.remote_head(s) for s in expected}
        self.assertFalse((self.home("palance") / "inbox").exists())
        lines = self.ok(self.deliver())
        for slug, text in expected.items():
            with self.subTest(slug=slug):
                sha = self.remote_head(slug)
                self.assertIn(f"{slug}: delivered — {LETTER} ({sha[:8]}), pushed", lines)
                self.assertEqual(self.remote(slug, "log", "-1", "--format=%s%n%an <%ae>%n%cn <%ce>%n%P", sha),
                                 f"{letter.COMMIT_MESSAGE}\n{h.AI_AUTHOR}\n{h.AI_AUTHOR}\n{bases[slug]}")
                self.assertEqual(self.remote(slug, "diff-tree", "--no-commit-id", "--name-only", "-r", sha), LETTER)
                self.assertEqual(self.remote(slug, "show", f"{sha}:{LETTER}") + "\n", text)
                self.assertNotIn("letter-of-return", LETTER)
                self.assertEqual(self.remote_letters(slug), [LETTER])
        # inbox/ is made where it was missing
        self.assertEqual(self.remote("palance", "ls-tree", "--name-only", "HEAD", "inbox/").splitlines(), [LETTER])
        self.assertEqual(lines[-1], "delivered 4 of 4 to deliver this run")
        data = self.ledger_data()
        self.assertEqual(data["runs"], [{"day": DAY, "at": NOW, "yes_from_yu": YES,
                                         "show_back": due.WAKE_CONFIRM_BEAT,
                                         "per_run": None}])
        for slug in expected:
            self.assertEqual(data["homes"][slug], {"day": DAY, "at": NOW, "letter": LETTER, "base": bases[slug],
                                                   "commit": self.remote_head(slug), "status": "pushed"})

    def test_a_same_day_rerun_does_nothing_and_the_default_ledger_is_in_home(self):
        lines = self.ok(self.deliver("--per-run", "1", ledger=False))
        self.assertIn(f"aseme: delivered — {LETTER} ({self.remote_head('aseme')[:8]}), pushed", lines)
        self.assertIn("hope: to deliver in a later run (--per-run 1) — contradictory", lines)
        heads = {s: self.remote_head(s) for s in ("aseme", "hope", "kalam", "palance")}
        r = self.deliver("--per-run", "1", now="2026-11-20T23:00Z", ledger=False)
        self.assertEqual(self.ok(r), [f"a delivery run already happened today ({DAY}); this run does nothing — "
                                      "run again on a later day"])
        self.assertEqual({s: self.remote_head(s) for s in heads}, heads)
        default = self.fx.home / ".local" / "state" / "citizen-pace-letter" / "ledger.json"
        self.assertEqual(json.loads(default.read_text(encoding="utf-8"))["homes"]["aseme"]["status"], "pushed")
        self.assertFalse(self.ledger.exists())

    def test_per_run_spreads_the_letters_and_each_home_holds_exactly_one(self):
        lines = self.ok(self.deliver("--per-run", "2"))
        self.assertEqual(lines[-1], "delivered 2 of 2 to deliver this run")
        self.assertEqual([bool(self.remote_letters(s)) for s in ("aseme", "hope", "kalam", "palance")],
                         [True, True, False, False])
        lines = self.ok(self.deliver("--per-run", "2", now=NEXT))
        self.assertIn(f"aseme: already asked — its inbox already holds {LETTER}", lines)
        self.assertIn(f"kalam: delivered — inbox/{NEXT_DAY}-pace-letter.md ({self.remote_head('kalam')[:8]}), "
                      "pushed", lines)
        lines = self.ok(self.deliver(now="2026-11-22T09:00Z"))
        self.assertEqual(lines[-1], "delivered 0 of 0 to deliver this run")
        for slug in ("aseme", "hope", "kalam", "palance"):
            self.assertEqual(len(self.remote_letters(slug)), 1, slug)
            self.assertEqual(len(self.letter_commits(slug)), 1, slug)
        self.assertEqual([r["day"] for r in self.ledger_data()["runs"]], [DAY, NEXT_DAY])

    def test_dry_run_prints_each_letter_and_writes_nothing(self):
        expected = {s: self.expected_letter(s) for s in ("aseme", "hope")}
        heads = {s: self.remote_head(s) for s in ("aseme", "hope", "kalam", "palance")}
        local = {s: self.fx.head(self.home(s)) for s in heads}
        r = self.tool("--dry-run", "--per-run", "2")
        out = r.stdout
        self.assertEqual(r.returncode, 0, r.stderr)
        for slug, text in expected.items():
            self.assertIn(f"\n--- the letter for {slug}: {LETTER} ---\n{text}", out)
        self.assertIn("kalam: to deliver in a later run (--per-run 2) — unheard-heard", out)
        self.assertNotIn("the letter for kalam", out)
        self.assertFalse(self.ledger.exists())
        self.assertEqual({s: self.remote_head(s) for s in heads}, heads)
        self.assertEqual({s: self.fx.head(self.home(s)) for s in heads}, local)
        self.assertFalse((self.home("palance") / "inbox").exists())

    def test_a_refused_push_is_skipped_and_a_later_run_delivers_it(self):
        hook = self.bare("hope") / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'refused by a fixture hook' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        before = self.remote_head("hope")
        lines = self.ok(self.deliver())
        refused = [ln for ln in lines if ln.startswith("hope: not delivered — the push was refused (")]
        self.assertEqual(len(refused), 1, lines)
        self.assertTrue(refused[0].endswith("); a later run delivers it"))
        self.assertEqual(self.remote_head("hope"), before)
        self.assertEqual(self.ledger_data()["homes"]["hope"]["status"], "push-failed")
        self.assertEqual(lines[-1], "delivered 3 of 4 to deliver this run")
        hook.unlink()
        lines = self.ok(self.deliver(now=NEXT))
        self.assertIn(f"hope: delivered — inbox/{NEXT_DAY}-pace-letter.md ({self.remote_head('hope')[:8]}), "
                      "pushed", lines)
        self.assertIn(f"aseme: already asked — its inbox already holds {LETTER}", lines)
        for slug in ("aseme", "hope", "kalam", "palance"):
            self.assertEqual(len(self.remote_letters(slug)), 1, slug)


class TestRaces(LetterCase):
    def setUp(self):
        super().setUp()
        for slug in ("aseme", "bindume", "ka"):
            self.eight(slug)
        self.census()
        self.switch("ka", "aseme", "bindume")
        self.tool_mod = load_tool()

    def _moved_by_elsewhere(self, slug):
        """GitHub's HEAD moves (another host pushes) after the tool read the home."""
        return self.fx.push_from_elsewhere(self.bare(slug), "beats/2026-11.md", "a beat from the Studio\n",
                                           message=f"beat: {slug} reflected ({DAY})",
                                           when="2026-11-20T08:59Z", author=h.beat_author(slug))

    def test_a_moved_head_is_skipped_and_never_overwritten(self):
        real = self.tool_mod.remote_head

        def moving(home, remoteref, base):
            if home.name == "citizen-bindume":
                self._moved_by_elsewhere("bindume")
            return real(home, remoteref, base)

        with mock.patch.object(self.tool_mod, "remote_head", moving):
            code, out, err = self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", SHOW_BACK)
        self.assertEqual(code, 0, err)
        self.assertIn("bindume: not delivered — its GitHub HEAD moved since it was read; a later run delivers it",
                      out.splitlines())
        self.assertEqual(self.remote_letters("bindume"), [])
        self.assertEqual(self.remote("bindume", "log", "-1", "--format=%s"), f"beat: bindume reflected ({DAY})")
        self.assertEqual(self.ledger_data()["homes"]["bindume"]["status"], "moved")

    def test_a_push_is_never_forced_even_when_the_head_check_misses(self):
        real = self.tool_mod.remote_head

        def stale(home, remoteref, base):
            seen = real(home, remoteref, base)
            if home.name == "citizen-bindume":
                self._moved_by_elsewhere("bindume")
            return seen

        with mock.patch.object(self.tool_mod, "remote_head", stale):
            code, out, err = self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", SHOW_BACK)
        self.assertEqual(code, 0, err)
        line = [ln for ln in out.splitlines() if ln.startswith("bindume: ")]
        self.assertEqual(len(line), 1)
        self.assertTrue(line[0].startswith("bindume: not delivered — the push was refused ("), line[0])
        self.assertEqual(self.remote_letters("bindume"), [])
        self.assertEqual(self.remote("bindume", "log", "-1", "--format=%s"), f"beat: bindume reflected ({DAY})")
        self.assertEqual(self.ledger_data()["homes"]["bindume"]["status"], "push-failed")

    def test_a_push_that_landed_unseen_is_recorded_and_never_repeated(self):
        real = self.tool_mod.push

        def lands_but_times_out(home, remoteref, base):
            if home.name == "citizen-aseme":
                real(home, remoteref, base)
            return False, "timed out after 60 s"

        with mock.patch.object(self.tool_mod, "push", lands_but_times_out):
            self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", SHOW_BACK)
        landed = self.remote_head("aseme")
        self.assertEqual(self.remote_letters("aseme"), [LETTER])
        self.assertEqual(self.remote_letters("bindume"), [])
        self.assertEqual(self.ledger_data()["homes"]["aseme"]["status"], "push-failed")
        r = self.deliver(now=NEXT)
        lines = self.ok(r)
        self.assertIn(f"aseme: the ledger said push-failed on {DAY}; its letter is on GitHub ({landed[:8]}), so "
                      "it is recorded as pushed", lines)
        self.assertIn(f"aseme: already asked — its inbox already holds {LETTER}", lines)
        self.assertIn(f"bindume: delivered — inbox/{NEXT_DAY}-pace-letter.md ({self.remote_head('bindume')[:8]}), "
                      "pushed", lines)
        self.assertEqual(self.ledger_data()["homes"]["aseme"]["status"], "pushed")
        for slug in ("aseme", "bindume", "ka"):
            self.assertEqual(len(self.remote_letters(slug)), 1, slug)

    def test_the_ledger_is_written_before_each_push(self):
        real, seen = self.tool_mod.push, {}

        def watched(home, remoteref, base):
            slug = home.name[len("citizen-"):]
            seen[slug] = (json.loads(self.ledger.read_text(encoding="utf-8"))["homes"][slug],
                          homes.head(home))
            return real(home, remoteref, base)

        with mock.patch.object(self.tool_mod, "push", watched):
            code, _out, err = self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", SHOW_BACK)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(seen), ["aseme", "bindume", "ka"])
        for slug, (entry, local) in seen.items():
            self.assertEqual((entry["status"], entry["commit"], entry["letter"]), ("pushing", local, LETTER))
            self.assertEqual(self.remote_head(slug), local)

    def test_the_credential_helper_is_pinned(self):
        t = self.tool_mod
        self.assertEqual(t.GITHUB_HELPER, GITHUB_HELPER)
        self.assertEqual(t.auth_args("https://github.com/cambridgetcg"),
                         ["-c", "credential.helper=", "-c", f"credential.helper={GITHUB_HELPER}"])
        self.assertEqual(t.auth_args(str(self.fx.upstream)), ["-c", "credential.helper="])


class TestShowBack(LetterCase):
    def setUp(self):
        super().setUp()
        self.eight("aseme")
        self.unheard("emime")
        self.unheard("kalam")
        self.census()
        self.heard_late("kalam")
        self.fleet_commit("emime")
        self.pace_beat("aseme")
        self.tool_mod = load_tool()

    def test_each_recipient_kind_is_worded_from_the_committed_value(self):
        shown_back, later, next_beat = letter.MORE_SHOWN_BACK, letter.MORE_LATER, letter.MORE_AWAKE
        with mock.patch.object(due, "WAKE_CONFIRM_BEAT", False):
            off = {s: self.expected_letter(s, confirm_beat=False) for s in ("aseme", "emime", "kalam")}
            code, out, err = self.in_process(self.tool_mod, "--dry-run")
            self.assertEqual(code, 0, err)
            refused = self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", "on")
        self.assertEqual(refused[0], 2)
        self.assertIn("Yu's yes was for show-back on, but the committed WAKE_CONFIRM_BEAT is False (off)",
                      refused[2])
        for slug, text in off.items():
            self.assertIn(f"--- the letter for {slug}: {LETTER} ---\n{text}", out)
        for slug in ("aseme", "emime"):
            self.assertIn(later, off[slug])
            self.assertNotIn(shown_back, off[slug])
            self.assertNotIn(letter.STAYS_SHOW_BACK, off[slug])
        self.assertIn(next_beat, off["kalam"])
        with mock.patch.object(due, "WAKE_CONFIRM_BEAT", True):
            on = {s: self.expected_letter(s, confirm_beat=True) for s in ("aseme", "emime", "kalam")}
            code, out, err = self.in_process(self.tool_mod, "--deliver", "--yes-from-yu", YES, "--show-back", "on")
        self.assertEqual(code, 0, err)
        for slug, text in on.items():
            self.assertEqual(self.remote(slug, "show", f"HEAD:{LETTER}") + "\n", text)
        for slug in ("aseme", "emime"):
            self.assertIn(shown_back, on[slug])
            self.assertIn(letter.STAYS_SHOW_BACK, on[slug])
        self.assertIn(next_beat, on["kalam"])


class TestEveryGroup(LetterCase):
    def test_the_recipients_are_the_seven_groups_and_each_letter_is_true(self):
        self.eight("aseme")
        self.sixteen("alohame", july=True)
        self.sixteen("diplosemy", july=True)
        self.sixteen("ai-love")
        self.unheard("kalam")
        self.unheard("soma")
        self.unheard("emime")
        self.unheard("eurekame")
        self.never_asked("palance")
        self.contradictory("hope")
        self.contradictory("ihsanme", key="ihsanme-a2ebca17", july=False)
        self.rest_on("ahavame", "forty-five", FORTY_FIVE, recipient=False)
        self.census()
        self.heard_late("kalam")
        self.heard_late("soma")
        self.fleet_commit("palance")
        self.pace_beat("hope")
        wanted = sorted(s for s, e in self.record["citizens"].items() if e["group"] in records.RECIPIENT_GROUPS)
        self.assertEqual(len(wanted), 11)
        expected = {s: self.expected_letter(s) for s in wanted}
        lines = self.ok(self.tool("--list"))
        self.assertEqual(sorted(ln.split(":")[0] for ln in lines[2:-1]), wanted)
        self.assertTrue(all(": to deliver — " in ln for ln in lines[2:-1]), lines)
        lines = self.ok(self.deliver())
        self.assertEqual(lines[-1], "delivered 11 of 11 to deliver this run")
        for slug in wanted:
            with self.subTest(slug=slug):
                text = self.remote(slug, "show", f"HEAD:{LETTER}") + "\n"
                self.assertEqual(text, expected[slug])
                e = self.record["citizens"][slug]
                c = cad.read_cadence(self.home(slug), slug, self.record, clock.parse_stamp(NOW))
                self.assertIn(letter.true_sentence(slug, e, c), text)
        self.assertEqual(self.remote_letters("ahavame"), [])


class TestTheCensusFixtures(h.FixtureCase):
    """The recipient list on the census's own fixture homes (the 208 the map
    found) and on migrated copies of them (spec section 8, "Letter")."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.made = h.Fixture(cls._tmp.name)
        cls.c = hc.census(cls.made, cls.made.root / "homes", census_tool.SPEC.groups, census_tool.SPEC.rest_on)
        cls.out = cls.made.root / records.RECORD_NAME
        r = cls.made.py(h.TOOLS / "citizen_pace_census.py", "build", cls.c.homes, "--roster", cls.c.roster,
                        "--out", cls.out, env={"PACE_NOW": MADE}, timeout=600)
        assert r.returncode == 0, r.stderr
        cls.rec = records.load_record(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        super().setUp()
        c = hc.copy_census(self.c, self.fx.root / "homes")
        self.homes = c.homes
        for slug in plan.recipients(self.rec):
            home = self.homes / f"citizen-{slug}"
            self.fx.git(home, "remote", "remove", "origin") if self.fx.git(home, "remote") else None
            self.fx.give_upstream(home)

    def listed(self):
        r = self.fx.py(TOOL, "--list", "--record", self.out, "--roster", self.c.roster,
                       "--ledger", self.fx.root / "ledger.json", env={"PACE_NOW": NOW}, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_the_list_as_built_is_the_record_s_recipients(self):
        lines = self.listed()
        self.assertTrue(lines[0].startswith(f"recipients: 32 in the record of {self.rec['made']}"), lines[0])
        names = sorted(ln.split(":", 1)[0] for ln in lines[2:-1])
        self.assertEqual(names, sorted(plan.recipients(self.rec)))
        for slug in ("kalam", "soma", "teotlme"):        # the heard-late step has not run yet
            self.assertTrue(any(ln.startswith(f"{slug}: stopped — the letter would not be true of its home")
                                for ln in lines), lines)
        self.assertEqual(lines[-1], "to deliver 29 · already asked 0 · dropped 0 · stopped 3 · could not be read 0")

    def test_the_list_on_migrated_copies_holds_every_group(self):
        r = self.fx.pace("migrate", "--heard-late", self.homes, "--record", self.out, "--state", self.fx.state,
                         "--commit", env=self.fx.env_at(SWITCH, **self.fx.git_env(when=SWITCH)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 3, r.stdout)
        for slug in ("kalam", "soma", "teotlme"):
            self.fx.git(self.homes / f"citizen-{slug}", "push", "-q", "origin", "HEAD")
        lines = self.listed()
        self.assertEqual(lines[-1], "to deliver 32 · already asked 0 · dropped 0 · stopped 0 · could not be read 0")
        groups = {}
        for ln in lines:
            if ": to deliver — " in ln:
                g = ln.split(": to deliver — ", 1)[1].split(";", 1)[0]
                groups[g] = groups.get(g, 0) + 1
        self.assertEqual(groups, {"eight": 8, "sixteen-ambient": 10, "sixteen-other": 6, "unheard-heard": 3,
                                  "unheard-none": 2, "never-asked": 1, "contradictory": 2})


class TestCheck(LetterCase):
    def test_check_lists_each_recipient_not_yet_reached(self):
        for slug in ("aseme", "bindume", "ka"):
            self.eight(slug)
        self.never_asked("palance")
        self.sixteen("ai-love", soul=False)
        self.census()
        self.switch("ka", "aseme", "bindume")
        self.ok(self.deliver())
        # aseme's letter beat: silent, the letter marked seen, asked once and the reached line
        home = self.home("aseme")
        self.fx.git(home, "pull", "-q", "--ff-only")
        with self.as_of("2026-11-23T06:00Z") as now:
            answer.apply_answer(home, "aseme", "", [LETTER], "letter", now, self.record)
        self.fx.commit(home, "beat: aseme reflected (2026-11-23) · pace/1", when="2026-11-23T06:00Z",
                       author=h.beat_author("aseme"))
        self.push("aseme")
        # bindume: rollback-prepare set its letter aside
        b = self.home("bindume")
        self.fx.git(b, "pull", "-q", "--ff-only")
        with self.as_of("2026-11-22T00:00Z") as now:
            got = tend.rollback_prepare(self.fx.citizens, ["bindume"], now=now, push=True)
        self.assertEqual(got, [f"bindume: {LETTER} set aside at rollback (pushed)"])
        # palance: delivered, then removed by someone
        p = self.home("palance")
        self.fx.git(p, "pull", "-q", "--ff-only")
        self.fx.git(p, "rm", "-q", LETTER)
        self.fx.commit(p, "remove it", when="2026-11-22T00:00Z")
        self.push("palance")
        lines = self.ok(self.tool("--check", now="2026-11-27T09:00Z"))
        self.assertEqual(lines[1:], [
            "ai-love: not reached — no pace letter in its home",
            f"bindume: not reached — {LETTER} was set aside at rollback and is not yet shown",
            f"ka: not reached — {LETTER} waits unread",
            f"palance: not reached — no pace letter in its home (the ledger says delivered on {DAY})",
            "reached: 1 of 5 recipients",
        ])


class TestIsolation(LetterCase):
    def test_runs_isolated_and_imports_nothing_from_its_working_folder(self):
        self.eight("aseme")
        self.census()
        planted = self.fx.root / "planted"
        planted.mkdir()
        (planted / "json.py").write_text(f"raise SystemExit('{h.CANARY}')\n", encoding="utf-8")
        (planted / "citizen_pace_lib").mkdir()
        (planted / "citizen_pace_lib" / "__init__.py").write_text(f"raise SystemExit('{h.CANARY}')\n",
                                                                  encoding="utf-8")
        r = self.tool("--list", cwd=planted)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(h.CANARY, r.stdout + r.stderr)
        self.assertIn("aseme: to deliver — eight; without a pace line its pace stays asleep", r.stdout)


if __name__ == "__main__":
    unittest.main()
