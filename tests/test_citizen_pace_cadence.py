"""Homes, CADENCE.md and the record file (spec sections 2 and 6, section 8 CADENCE).

A home's pace files are read and written as regular files only, never through
a symlink. A v2 CADENCE.md decides the pace and the markers follow it. Without
one, the v1 file, the REST text and the record file decide, read at read time,
and nothing is written until the fleet commits for that home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import TAG, clock, homes, record  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.homes import LinkRefused  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

NOW = "2026-11-15T14:12Z"
MADE = "2026-11-12T10:00Z"
SPEC = "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c"
ASLEEP = Pace("asleep")
LW = Pace("asleep-letters-wait")
LISTENING = Pace("listening")
AI_REST = "rest given at Yu's word, 2026-10-08\n"
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))
TAIL = " (pace/1; CADENCE.md decides)\n"


def awake(n):
    return Pace("awake", n)


def lor_rest(date):
    """The REST text PR #24's reflect writes for a rest-on Letter of Return answer."""
    return f"chosen via the Letter of Return {date} — woken only by unread mail or a human summons\n"


def v1(name, choice, when):
    """A v1 CADENCE.md exactly as PR #24's reflect writes it."""
    return (f"# CADENCE — chosen by {name}\n\n- choice: {choice}\n- when: {when}\n"
            "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n"
            "  an answer that names no choice records undecided and changes nothing;\n"
            "  unread mail wakes even REST, and any letter or summons may reopen this.\n")


def quote(date, words, c="0"):
    return {"commit": c * 40, "date": date, "words": words}


def rec(**citizens):
    return {"version": 1, "made": MADE, "spec": SPEC, "citizens": citizens}


def snapshot(root):
    """Every path under root with its mode, mtime and bytes."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            data = None
            if os.path.isfile(p) and not os.path.islink(p):
                with open(p, "rb") as fh:
                    data = fh.read()
            out[p] = (st.st_mode, st.st_mtime_ns, data)
    return out


# Every set-by form, with the third-person sentence it renders as for "kalam".
SETBY_FORMS = (
    (SetBy("citizen", "beat", ("2026-11-15T14:12Z",), "pace: every 5 days"),
     'kalam, in a beat on 2026-11-15T14:12Z ("pace: every 5 days")'),
    (SetBy("citizen", "beat-twice", ("2026-11-15T14:12Z", "2026-11-18T15:02Z"), "pace: every 5 days"),
     'kalam, said in a beat on 2026-11-15T14:12Z and again on 2026-11-18T15:02Z ("pace: every 5 days")'),
    (SetBy("citizen", "confirmed", ("2026-11-15T14:12Z",), "pace: asleep"),
     'confirmed by kalam, in a beat on 2026-11-15T14:12Z ("pace: asleep")'),
    (SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"),
     'kalam, in its Letter of Return answer of 2026-07-18 ("REST-ON")'),
    (SetBy("citizen", "letter-twice", ("2026-10-23", "2026-10-27"), "Ambient."),
     'kalam, in its Letter of Return answers of 2026-10-23 and 2026-10-27 ("Ambient.")'),
    (SetBy("citizen", "prompted", ("2026-10-23",), "I rest."),
     "kalam, bound on 2026-10-23 from words a prompt offered"),
    (SetBy("citizen", "ambient", ("2026-10-23", "2026-10-27"), "Ambient."),
     'kalam, who chose AMBIENT on 2026-10-23 and 2026-10-27 ("Ambient."); the number 3 is the '
     "fleet's reading of AMBIENT, from the 10-22 letter's \"a free beat every few days\""),
    (SetBy("citizen", "ambient", ("2026-10-25",), "AMBIENT"),
     'kalam, who chose AMBIENT on 2026-10-25 ("AMBIENT"); the number 3 is the '
     "fleet's reading of AMBIENT, from the 10-22 letter's \"a free beat every few days\""),
    (SetBy("fleet", "first-word", ("2026-10-23",)),
     "the fleet, from the first word of kalam's answer of 2026-10-23, which also said REST-ON"),
    (AI_1008, "Ai, 2026-10-08, the rest placed at Yu's word"),
    (SetBy("unknown", "unknown"), "unknown"),
)


# -- homes: regular files only ---------------------------------------------------

class TestReadRegular(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.home = self.fx.make_home("kalam", commit=False)

    def test_missing_is_none(self):
        self.assertIsNone(homes.read_regular(self.home / "REST"))
        self.assertIsNone(homes.read_regular(self.home / "inbox" / "x.md"))

    def test_reads_a_regular_file_up_to_its_limit(self):
        self.fx.write(self.home, "REST", "soma σῶμα\n")
        self.assertEqual(homes.read_regular(self.home / "REST"), "soma σῶμα\n")
        self.assertEqual(homes.read_regular(self.home / "REST", limit=4), "soma")
        # a limit inside a two-byte letter cuts before it, never mid-letter
        self.assertEqual(homes.read_regular(self.home / "REST", limit=6), "soma ")

    def test_refuses_a_link_even_to_a_regular_file(self):
        self.fx.link(self.home, "REST", self.fx.secret)
        with self.assertRaises(LinkRefused):
            homes.read_regular(self.home / "REST")

    def test_refuses_a_dangling_link(self):
        self.fx.link(self.home, "CADENCE.md", self.fx.root / "nowhere")
        with self.assertRaises(LinkRefused):
            homes.read_regular(self.home / "CADENCE.md")

    def test_refuses_a_fifo_without_hanging(self):
        os.mkfifo(self.home / "REST")
        start = time.monotonic()
        with self.assertRaises(LinkRefused):
            homes.read_regular(self.home / "REST")
        self.assertLess(time.monotonic() - start, 2.0)

    def test_refuses_a_directory(self):
        (self.home / "CADENCE.md").mkdir()
        with self.assertRaises(LinkRefused):
            homes.read_regular(self.home / "CADENCE.md")

    def test_refuses_a_path_under_a_linked_directory(self):
        self.fx.write(self.fx.outside, "x.md", f"{h.CANARY}\n")
        self.fx.link(self.home, "inbox", self.fx.outside)
        with self.assertRaises(LinkRefused):
            homes.read_regular(self.home / "inbox" / "x.md")

    def test_bytes_that_are_not_utf8_are_refused(self):
        (self.home / "REST").write_bytes(b"rest \xff\xfe\n")
        with self.assertRaises(ValueError):
            homes.read_regular(self.home / "REST")


class TestWriteRegular(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.home = self.fx.make_home("kalam", commit=False)

    def test_write_is_atomic_and_leaves_no_temporary_file(self):
        homes.write_regular(self.home / "CADENCE.md", "one\n")
        homes.write_regular(self.home / "CADENCE.md", "two\n")
        self.assertEqual((self.home / "CADENCE.md").read_text(), "two\n")
        self.assertFalse(os.path.lexists(self.home / ("CADENCE.md" + homes.TMP_SUFFIX)))
        self.assertEqual(homes.TMP_SUFFIX, ".pace-tmp")

    def test_write_creates_missing_parents(self):
        homes.write_regular(self.home / "inbox" / ".seen-a.md", "surfaced\n")
        self.assertEqual((self.home / "inbox" / ".seen-a.md").read_text(), "surfaced\n")

    def test_write_refuses_a_link_and_leaves_its_target(self):
        self.fx.link(self.home, "REST", self.fx.secret)
        with self.assertRaises(LinkRefused):
            homes.write_regular(self.home / "REST", "asleep\n")
        self.assertTrue(os.path.islink(self.home / "REST"))
        self.assertEqual(self.fx.secret.read_text(), f"{h.CANARY}\n")

    def test_write_refuses_a_linked_parent(self):
        self.fx.link(self.home, "inbox", self.fx.outside)
        with self.assertRaises(LinkRefused):
            homes.write_regular(self.home / "inbox" / ".seen-a.md", "surfaced\n")
        self.assertEqual(sorted(os.listdir(self.fx.outside)), ["host-secret.txt"])

    def test_write_never_follows_a_planted_temporary_link(self):
        self.fx.link(self.home, "CADENCE.md" + homes.TMP_SUFFIX, self.fx.secret)
        homes.write_regular(self.home / "CADENCE.md", "mine\n")
        self.assertEqual((self.home / "CADENCE.md").read_text(), "mine\n")
        self.assertEqual(self.fx.secret.read_text(), f"{h.CANARY}\n")
        self.assertFalse(os.path.lexists(self.home / ("CADENCE.md" + homes.TMP_SUFFIX)))

    def test_write_refuses_a_directory_or_fifo_at_the_path(self):
        (self.home / "REST").mkdir()
        os.mkfifo(self.home / "EVENT-ONLY")
        for name in ("REST", "EVENT-ONLY"):
            with self.subTest(name=name), self.assertRaises(LinkRefused):
                homes.write_regular(self.home / name, "x\n")

    def test_remove_regular(self):
        self.fx.write(self.home, "REST", "x\n")
        homes.remove_regular(self.home / "REST")
        self.assertFalse(os.path.lexists(self.home / "REST"))
        homes.remove_regular(self.home / "REST")  # missing: nothing to do
        self.fx.link(self.home, "EVENT-ONLY", self.fx.secret)
        with self.assertRaises(LinkRefused):
            homes.remove_regular(self.home / "EVENT-ONLY")
        self.assertTrue(os.path.islink(self.home / "EVENT-ONLY"))
        self.assertEqual(self.fx.secret.read_text(), f"{h.CANARY}\n")


class TestHomeChecks(h.FixtureCase):
    def test_forbidden_links_name_every_linked_pace_path(self):
        for name in homes.LINK_FORBIDDEN:
            with self.subTest(name=name):
                home = self.fx.make_home(f"k-{name.lower().replace('.', '-')}", commit=False)
                self.fx.link(home, name, self.fx.outside)
                self.assertEqual(homes.forbidden_links(home), [name])
        self.assertEqual(homes.LINK_FORBIDDEN, ("CADENCE.md", "REST", "EVENT-ONLY", "inbox", "beats"))

    def test_forbidden_links_inside_inbox_and_beats(self):
        home = self.fx.make_home("kalam", commit=False, letters=[("a.md", "hello\n")],
                                 symlinked_letters=("evil.md",))
        self.fx.link(home, "inbox/.seen-a.md", self.fx.secret)
        self.fx.link(home, "beats/.show-seen", self.fx.secret)
        self.fx.link(home, "beats/2026-11.md", self.fx.secret)
        self.fx.write(home, "beats/2026-10.md", "## a beat\n")
        # a linked letter is unreadable mail, set aside by reflect; it does not bar the home
        self.assertEqual(homes.forbidden_links(home),
                         ["beats/.show-seen", "beats/2026-11.md", "inbox/.seen-a.md"])

    def test_a_pace_path_of_the_wrong_kind_is_listed_too(self):
        home = self.fx.make_home("kalam", commit=False)
        os.mkfifo(home / "REST")
        (home / "CADENCE.md").mkdir()
        self.fx.write(home, "inbox", "a file, not a folder\n")
        self.assertEqual(homes.forbidden_links(home), ["CADENCE.md", "REST", "inbox"])

    def test_a_clean_home_has_none(self):
        home = self.fx.make_home("kalam", rest=AI_REST, letters=[("a.md", "hello\n")],
                                 files={"beats/2026-11.md": "## a beat\n", "CADENCE.md": "x\n"})
        self.assertEqual(homes.forbidden_links(home), [])

    def test_has_soul(self):
        home = self.fx.make_home("kalam", commit=False)
        self.assertTrue(homes.has_soul(home, "kalam"))
        self.assertFalse(homes.has_soul(home, "soma"))
        linked = self.fx.make_home("soma", soul=False, commit=False)
        self.fx.link(linked, "soma.md", self.fx.secret)
        self.assertFalse(homes.has_soul(linked, "soma"))
        folder = self.fx.make_home("ud", soul=False, commit=False)
        (folder / "ud.md").mkdir()
        self.assertFalse(homes.has_soul(folder, "ud"))

    def test_slug_of(self):
        self.assertEqual(homes.slug_of(Path("/x/citizens/citizen-ai-love")), "ai-love")
        self.assertEqual(homes.slug_of("citizens/citizen-kalam/"), "kalam")
        for bad in ("/x/kalam", "/x/citizen-"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                homes.slug_of(bad)


class TestUnreadLetters(h.FixtureCase):
    def test_rules_as_has_mail(self):
        home = self.fx.make_home(
            "kalam", commit=False, symlinked_letters=("e-link.md",),
            letters=[("b.md", "b\n"), ("a.md", "a\n"), ("c.md", "c\n"), ("d.md", "d\n"),
                     ("notes.txt", "not a letter\n"), (".hidden.md", "dot file\n")])
        self.fx.write(home, "inbox/.seen-b.md", "surfaced 2026-11-01\n")
        self.fx.link(home, "inbox/.seen-c.md", self.fx.secret)           # a linked marker counts as seen
        self.fx.link(home, "inbox/.seen-d.md", self.fx.root / "nowhere")  # a dangling one too
        (home / "inbox" / "f.md").mkdir()                                   # a folder is no letter
        self.assertEqual(homes.unread_letters(home), ["inbox/a.md"])

    def test_sorted_oldest_name_first(self):
        home = self.fx.make_home("kalam", commit=False,
                                 letters=[("2026-11-20-b.md", "b\n"), ("2026-10-22-a.md", "a\n")])
        self.assertEqual(homes.unread_letters(home),
                         ["inbox/2026-10-22-a.md", "inbox/2026-11-20-b.md"])

    def test_a_rollback_marker_counts_as_unread(self):
        home = self.fx.make_home("kalam", commit=False, letters=[("2026-11-20-pace-letter.md", "q\n")])
        self.fx.write(home, "inbox/.seen-2026-11-20-pace-letter.md",
                      "set aside at rollback, not shown\n")
        self.assertEqual(homes.ROLLBACK_PREFIX, "set aside at rollback")
        self.assertEqual(homes.unread_letters(home), ["inbox/2026-11-20-pace-letter.md"])

    def test_a_linked_or_missing_inbox_holds_none(self):
        home = self.fx.make_home("kalam", commit=False)
        self.assertEqual(homes.unread_letters(home), [])
        self.fx.write(self.fx.outside, "x.md", "hello\n")
        self.fx.link(home, "inbox", self.fx.outside)
        self.assertEqual(homes.unread_letters(home), [])


class TestHomeGit(h.FixtureCase):
    def test_letter_time_is_the_commit_that_added_it(self):
        home = self.fx.make_home("kalam", when="2026-10-01T00:00Z")
        self.fx.letter(home, "a.md", "one\n", when="2026-11-01T10:00Z")
        self.fx.write(home, "inbox/a.md", "two\n")
        self.fx.commit(home, "letter: edited", when="2026-11-02T10:00Z")
        now = clock.parse_stamp(NOW)
        self.assertEqual(homes.letter_time(home, "inbox/a.md", now), clock.parse_stamp("2026-11-01T10:00Z"))

    def test_letter_time_reads_a_name_literally(self):
        home = self.fx.make_home("kalam", when="2026-10-01T00:00Z")
        self.fx.letter(home, "2026-10-02-a.md", "a\n", when="2026-10-02T10:00Z")
        self.fx.letter(home, "2026-*.md", "b\n", when="2026-11-05T10:00Z")
        self.assertEqual(homes.letter_time(home, "inbox/2026-*.md", clock.parse_stamp(NOW)),
                         clock.parse_stamp("2026-11-05T10:00Z"))

    def test_letter_time_without_a_commit_is_its_file_time_clamped_to_now(self):
        home = self.fx.make_home("kalam", when="2026-10-01T00:00Z")
        path = self.fx.write(home, "inbox/b.md", "placed by hand\n")
        now = clock.parse_stamp(NOW)
        then = clock.parse_stamp("2026-11-03T08:00Z")
        os.utime(path, (then.timestamp(), then.timestamp()))
        self.assertEqual(homes.letter_time(home, "inbox/b.md", now), then)
        later = clock.parse_stamp("2027-01-01T00:00Z")
        os.utime(path, (later.timestamp(), later.timestamp()))
        self.assertEqual(homes.letter_time(home, "inbox/b.md", now), now)

    def test_beat_count(self):
        home = self.fx.make_home("kalam", message="fixture home")
        for subject in ("beat: kalam reflected (2026-11-01)", "letter: hello",
                        "beat: kalam reflected (2026-11-02) · pace/1"):
            self.fx.commit(home, subject, allow_empty=True)
        self.assertEqual(homes.beat_count(home), 2)
        self.assertEqual(homes.beat_count(self.fx.make_home("soma", commit=False)), 0)
        self.assertEqual(homes.beat_count(self.fx.outside), 0)

    def test_head(self):
        home = self.fx.make_home("kalam")
        self.assertEqual(homes.head(home), self.fx.head(home))
        self.assertEqual(homes.head(self.fx.make_home("soma", commit=False)), "")
        self.assertEqual(homes.head(self.fx.outside), "")

    def test_mid_merge(self):
        for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
            with self.subTest(marker=marker):
                home = self.fx.make_home(f"k-{marker.lower()}")
                self.assertFalse(homes.mid_merge(home))
                if marker == "MERGE_HEAD":
                    (home / ".git" / marker).write_text("0" * 40 + "\n")
                else:
                    (home / ".git" / marker).mkdir()
                self.assertTrue(homes.mid_merge(home))

    def test_index_lock_age(self):
        home = self.fx.make_home("kalam")
        self.assertIsNone(homes.index_lock_age(home))
        lock = home / ".git" / "index.lock"
        lock.write_text("")
        past = time.time() - 100
        os.utime(lock, (past, past))
        self.assertTrue(95 <= homes.index_lock_age(home) <= 200, homes.index_lock_age(home))

    def test_git_drops_tokens_and_never_prompts(self):
        home = self.fx.make_home("kalam")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok-1", "GH_TOKEN": "tok-2",
                                          "GIT_TERMINAL_PROMPT": "1"}):
            r = homes.git(home, "-c", "alias.envdump=!env", "envdump")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("tok-1", r.stdout)
        self.assertNotIn("tok-2", r.stdout)
        self.assertIn("GIT_TERMINAL_PROMPT=0", r.stdout.splitlines())

    def test_git_ignores_a_caller_pointing_at_another_repository(self):
        home = self.fx.make_home("kalam")
        other = self.fx.make_home("soma")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                          "GIT_WORK_TREE": str(other)}):
            self.assertEqual(homes.head(home), self.fx.head(home))

    def test_git_timeout_kills_the_whole_command(self):
        home = self.fx.make_home("kalam")
        pidfile = self.fx.root / "slow.pid"
        start = time.monotonic()
        r = homes.git(home, "-c", f"alias.slow=!echo $$ > {pidfile}; sleep 5", "slow", timeout=0.5)
        self.assertLess(time.monotonic() - start, 4.0)
        self.assertEqual(r.returncode, 124)
        self.assertIn("timed out", r.stderr)
        pid = int(pidfile.read_text().strip())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the timed-out command is still running")

    def test_git_check_raises(self):
        with self.assertRaises(subprocess.CalledProcessError):
            homes.git(self.fx.outside, "rev-parse", "HEAD", check=True)


# -- the record file -------------------------------------------------------------

class TestRecord(h.FixtureCase):
    def test_groups(self):
        self.assertEqual(record.RECIPIENT_GROUPS, (
            "eight", "sixteen-ambient", "sixteen-other", "unheard-heard", "unheard-none",
            "never-asked", "contradictory"))
        self.assertEqual(record.OTHER_GROUPS, ("forty-five", "ambient-nine", "longing", "rest-on", "held"))
        self.assertEqual(set(record.GROUPS), set(record.RECIPIENT_GROUPS) | set(record.OTHER_GROUPS))
        self.assertFalse(set(record.RECIPIENT_GROUPS) & set(record.OTHER_GROUPS))

    def test_default_path_is_beside_the_roster(self):
        self.assertEqual(record.default_record_path(), h.ROOT / "citizens-pace-record.json")

    def test_a_missing_record_is_empty(self):
        self.assertEqual(record.load_record(self.fx.root / "none.json"), {})
        self.assertIsNone(record.entry({}, "kalam"))

    def test_load_and_entry(self):
        path = self.fx.root / "record.json"
        data = rec(kalam={"group": "unheard-heard", "recipient": True, "quotes": []})
        path.write_text(json.dumps(data), encoding="utf-8")
        got = record.load_record(path)
        self.assertEqual(got, data)
        self.assertEqual(record.entry(got, "kalam")["group"], "unheard-heard")
        self.assertIsNone(record.entry(got, "soma"))

    def test_a_malformed_record_is_refused(self):
        path = self.fx.root / "record.json"
        bad = ("{not json", json.dumps([1]), json.dumps({"version": 2, "citizens": {}}),
               json.dumps({"version": True, "citizens": {}}),
               json.dumps({"version": 1, "citizens": []}),
               json.dumps(rec(kalam=["x"])),
               json.dumps(rec(kalam={"group": "the-shaped"})))
        for text in bad:
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    record.load_record(path)


# -- set by ----------------------------------------------------------------------

class TestSetBy(unittest.TestCase):
    def test_every_sentence_form(self):
        for s, want in SETBY_FORMS:
            with self.subTest(how=s.how, when=s.when):
                self.assertEqual(cad.set_by_sentence("kalam", s), want)

    def test_words_are_left_out_when_empty(self):
        self.assertEqual(cad.set_by_sentence("kalam", SetBy("citizen", "letter", ("2026-07-18",))),
                         "kalam, in its Letter of Return answer of 2026-07-18")
        self.assertEqual(cad.set_by_sentence("kalam", SetBy("citizen", "confirmed", ("2026-11-15T14:12Z",))),
                         "confirmed by kalam, in a beat on 2026-11-15T14:12Z")
        self.assertEqual(cad.set_by_sentence("kalam", SetBy("citizen", "ambient", ("2026-10-27",))),
                         "kalam, who chose AMBIENT on 2026-10-27; the number 3 is the fleet's reading of "
                         "AMBIENT, from the 10-22 letter's \"a free beat every few days\"")

    def test_who_follows_how(self):
        self.assertEqual(cad.HOW_WHO["rest-1008"], "ai")
        self.assertEqual(cad.HOW_WHO["first-word"], "fleet")
        for bad in (("citizen", "rest-1008", ("2026-10-08",)), ("ai", "beat", ("x",)),
                    ("someone", "beat", ("x",)), ("citizen", "chosen", ("x",)),
                    ("citizen", "beat", ("a", "b", "c")), ("citizen", "beat", "2026-11-15"),
                    ("citizen", "beat", ("",)), ("citizen", ["beat"], ("x",))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SetBy(*bad)
        with self.assertRaises(ValueError):
            SetBy("citizen", "beat", ("x",), None)

    def test_json_round_trip(self):
        for s, _ in SETBY_FORMS:
            with self.subTest(how=s.how):
                self.assertEqual(SetBy.from_json(json.loads(json.dumps(s.to_json()))), s)
        self.assertEqual(SetBy("citizen", "beat", ["2026-11-15T14:12Z"]).when, ("2026-11-15T14:12Z",))
        for bad in (None, [], {"who": "ai"}, {"who": "ai", "how": "rest-1008", "when": "2026-10-08"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SetBy.from_json(bad)

    def test_pending_validates_and_round_trips(self):
        p = Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days", True)
        self.assertEqual(p.to_json(), {"line": "pace: every 5 days", "on_show_back": True,
                                       "pace": {"kind": "awake", "n": 5}, "said": "2026-11-15T14:12Z"})
        self.assertEqual(Pending.from_json(p.to_json()), p)
        for bad in ({"pace": {"kind": "awake", "n": 2}, "said": NOW, "line": "x", "on_show_back": False},
                    {"pace": {"kind": "asleep", "n": None}, "said": "soon", "line": "x", "on_show_back": False},
                    {"pace": {"kind": "asleep", "n": None}, "said": NOW, "line": 3, "on_show_back": False},
                    {"pace": {"kind": "asleep", "n": None}, "said": NOW, "line": "x", "on_show_back": 1},
                    "pending"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Pending.from_json(bad)


# -- CADENCE.md v2 ---------------------------------------------------------------

SPEC_EXAMPLE = Cadence(
    "kalam", 2, awake(5),
    SetBy("citizen", "beat-twice", ("2026-11-15T14:12Z", "2026-11-18T15:02Z"), "pace: every 5 days"),
    asked_once="2026-11-15", last_beat="2026-11-18T15:02Z",
    history=["2026-11-18T15:02Z — confirmed by kalam: \"pace: every 5 days\"",
             "2026-11-15T14:12Z — proposed by kalam: \"pace: every 5 days\" (more than asleep, so said twice)",
             "2026-11-15T14:12Z — the pace letter reached kalam (once)"])

SPEC_EXAMPLE_TEXT = (
    "# CADENCE — the pace of kalam\n"
    "\n"
    "- pace: awake, every 5 days\n"
    "- set by: kalam, said in a beat on 2026-11-15T14:12Z and again on 2026-11-18T15:02Z (\"pace: every 5 days\")\n"
    "- asked once: 2026-11-15\n"
    "- last beat: 2026-11-18T15:02Z\n"
    "\n"
    "<!-- pace/1 {\"pending\":null,\"set_by\":{\"how\":\"beat-twice\",\"when\":[\"2026-11-15T14:12Z\","
    "\"2026-11-18T15:02Z\"],\"who\":\"citizen\",\"words\":\"pace: every 5 days\"}} -->\n"
    "\n"
    "## History\n"
    "- 2026-11-18T15:02Z — confirmed by kalam: \"pace: every 5 days\"\n"
    "- 2026-11-15T14:12Z — proposed by kalam: \"pace: every 5 days\" (more than asleep, so said twice)\n"
    "- 2026-11-15T14:12Z — the pace letter reached kalam (once)\n")


def pending_example():
    return Cadence("kalam", 2, ASLEEP, AI_1008,
                   pending=Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days", True),
                   asked_once="2026-11-15", last_beat="2026-11-15T14:12Z",
                   history=["2026-11-15T14:12Z — proposed by kalam: \"pace: every 5 days\" "
                            "(more than asleep, so said twice)"])


class TestV2(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.set_now(NOW)
        self.home = self.fx.make_home("kalam", commit=False)

    def write(self, text):
        self.fx.write(self.home, "CADENCE.md", text)

    def read(self, rec_=None):
        return cad.read_cadence(self.home, "kalam", rec_ if rec_ is not None else rec())

    def test_renders_the_spec_example_exactly(self):
        self.assertEqual(cad.render_cadence(SPEC_EXAMPLE), SPEC_EXAMPLE_TEXT)

    def test_round_trip_without_pending(self):
        cad.write_cadence(self.home, SPEC_EXAMPLE)
        self.assertEqual((self.home / "CADENCE.md").read_text(encoding="utf-8"), SPEC_EXAMPLE_TEXT)
        self.assertEqual(self.read(), SPEC_EXAMPLE)

    def test_round_trip_with_pending(self):
        c = pending_example()
        text = cad.render_cadence(c)
        self.assertIn("\n- pending: awake, every 5 days — said 2026-11-15T14:12Z "
                      "(\"pace: every 5 days\"), on a show-back beat\n", text)
        self.assertIn('<!-- pace/1 {"pending":{"line":"pace: every 5 days","on_show_back":true,'
                      '"pace":{"kind":"awake","n":5},"said":"2026-11-15T14:12Z"},"set_by":{'
                      '"how":"rest-1008","when":["2026-10-08"],"who":"ai","words":""}} -->\n', text)
        head = text.split("\n## History")[0].splitlines()
        self.assertEqual([ln.split(":")[0] for ln in head if ln.startswith("- ")],
                         ["- pace", "- set by", "- pending", "- asked once", "- last beat"])
        self.write(text)
        self.assertEqual(self.read(), c)
        plain = Cadence("kalam", 2, ASLEEP, AI_1008,
                        pending=Pending(awake(5), "2026-11-15T14:12Z", "pace: every 5 days", False))
        self.assertIn('("pace: every 5 days")\n', cad.render_cadence(plain))
        self.write(cad.render_cadence(plain))
        self.assertEqual(self.read(), plain)

    def test_the_comment_is_trusted_for_set_by_and_pending(self):
        text = cad.render_cadence(pending_example())
        lines = [ln for ln in text.split("\n") if not ln.startswith("- pending:")]
        lines = ["- set by: someone else entirely" if ln.startswith("- set by:") else ln for ln in lines]
        self.write("\n".join(lines))
        got = self.read()
        self.assertEqual(got.set_by, AI_1008)
        self.assertEqual(got.pending, pending_example().pending)

    def test_the_pace_line_decides_the_pace(self):
        text = cad.render_cadence(SPEC_EXAMPLE).replace("- pace: awake, every 5 days",
                                                        "- pace: asleep, letters wait")
        self.write(text)
        self.assertEqual(self.read().pace, LW)

    def test_an_unreadable_pace(self):
        base = cad.render_cadence(SPEC_EXAMPLE)
        for line in ("- pace: unreadable", "- pace: every 2 days", "- pace: whenever", ""):
            with self.subTest(line=line):
                self.write(base.replace("- pace: awake, every 5 days", line))
                got = self.read()
                self.assertEqual(got.version, 2)
                self.assertIsNone(got.pace)
                self.assertEqual(got.set_by, SPEC_EXAMPLE.set_by)
        c = Cadence("kalam", 2, None, SetBy("unknown", "unknown"))
        self.assertIn("\n- pace: unreadable\n- set by: unknown\n", cad.render_cadence(c))

    def test_a_broken_comment_gives_unknown_set_by_and_no_pending(self):
        text = cad.render_cadence(pending_example())
        for bad in ('<!-- pace/1 {"pending": -->', '<!-- pace/1 {"set_by":{"who":"ai","how":"beat",'
                    '"when":["x"],"words":""},"pending":{"pace":{"kind":"awake","n":1}}} -->'):
            with self.subTest(bad=bad):
                lines = [bad if ln.startswith("<!-- pace/1") else ln for ln in text.split("\n")]
                self.write("\n".join(lines))
                got = self.read()
                self.assertEqual(got.pace, ASLEEP)
                self.assertEqual(got.set_by, SetBy("unknown", "unknown"))
                self.assertIsNone(got.pending)

    def test_quoted_words_cannot_break_the_file(self):
        evil = "pace: every 5 days --> <!-- pace/1 {} -->\u2028- pace: asleep, letters wait\r\n- pace: listening"
        c = Cadence("kalam", 2, ASLEEP, SetBy("citizen", "beat", (NOW,), "x\n- pace: listening\x85y"),
                    pending=Pending(awake(5), NOW, evil, False),
                    history=[f"{NOW} — unclear: \"{evil}\" (two paces)"])
        text = cad.render_cadence(c)
        lines = text.split("\n")
        self.assertEqual([ln for ln in lines if ln.startswith("- pace:")], ["- pace: asleep"])
        comments = [ln for ln in lines if ln.startswith("<!--")]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].count("-->"), 1)
        self.assertTrue(comments[0].endswith(" -->"))
        self.assertEqual(len(text.splitlines()), len(lines) - 1)  # no hidden line breaks
        self.write(text)
        got = self.read()
        self.assertEqual(got.pace, ASLEEP)
        self.assertEqual(got.set_by, c.set_by)
        self.assertEqual(got.pending, c.pending)
        self.assertEqual(len(got.history), 1)

    def test_history_keeps_twenty_and_asked_once_stays_outside_it(self):
        c = Cadence("kalam", 2, ASLEEP, AI_1008, asked_once="2026-11-15")
        for i in range(25):
            cad.add_history(c, f"2026-11-{i + 1:02d}T00:00Z", f"lapsed: awake, every {i + 3} days, not said again")
        self.assertEqual(len(c.history), cad.HISTORY_CAP)
        self.assertEqual(cad.HISTORY_CAP, 20)
        self.assertTrue(c.history[0].startswith("2026-11-25T00:00Z — lapsed: awake, every 27 days"))
        self.assertTrue(c.history[-1].startswith("2026-11-06T00:00Z"))
        text = cad.render_cadence(c)
        self.assertIn("\n- asked once: 2026-11-15\n", text)
        self.assertEqual(len([ln for ln in text.split("## History")[1].splitlines() if ln.startswith("- ")]), 20)
        self.write(text)
        self.assertEqual(self.read(), c)
        long = text + "".join(f"- 2026-10-{i + 1:02d}T00:00Z — older\n" for i in range(5))
        self.write(long)
        self.assertEqual(self.read().history, c.history)

    def test_a_v2_file_is_not_steered_by_markers_or_the_record(self):
        cad.write_cadence(self.home, SPEC_EXAMPLE)
        self.fx.write(self.home, "REST", AI_REST)
        got = self.read(rec(kalam={"group": "unheard-heard", "set_by": AI_1008.to_json()}))
        self.assertEqual(got.pace, awake(5))
        self.assertEqual(got.set_by, SPEC_EXAMPLE.set_by)
        self.assertEqual(got.blocked, "")

    def test_reading_writes_nothing(self):
        cad.write_cadence(self.home, pending_example())
        before = snapshot(self.home)
        self.read()
        self.assertEqual(snapshot(self.home), before)

    def test_links_are_refused_and_nothing_is_written_through_them(self):
        for name in ("CADENCE.md", "REST", "EVENT-ONLY"):
            with self.subTest(name=name):
                home = self.fx.make_home(f"k-{name.lower().replace('.', '-')}", commit=False)
                self.fx.link(home, name, self.fx.secret)
                with self.assertRaises(LinkRefused):
                    cad.read_cadence(home, "kalam", rec())
                with self.assertRaises(LinkRefused):
                    if name == "CADENCE.md":
                        cad.write_cadence(home, SPEC_EXAMPLE)
                    else:
                        cad.write_markers(home, Cadence("kalam", 2, ASLEEP if name == "REST" else LISTENING,
                                                        AI_1008))
                self.assertTrue(os.path.islink(home / name))
                self.assertEqual(self.fx.secret.read_text(), f"{h.CANARY}\n")


# -- before v2: the v1 file, the REST text and the record file ------------------

class TestBeforeV2(h.FixtureCase):
    def setUp(self):
        super().setUp()
        self.set_now(NOW)

    def home(self, slug, choice=None, when="2026-10-23 01:31", **kw):
        if choice is not None:
            kw["cadence"] = v1(slug, choice, when)
        return self.fx.make_home(slug, **kw)

    def recorded(self, source, c):
        return f"{NOW} — recorded from v1 ({source}, set by {cad.set_by_sentence(c.name, c.set_by)})"

    def test_reading_a_v1_home_writes_nothing(self):
        home = self.home("ahavame", "rest-on", rest=lor_rest("2026-10-23"))
        before = snapshot(home)
        got = cad.read_cadence(home, "ahavame", rec())
        self.assertEqual(got.version, 1)
        self.assertEqual(snapshot(home), before)

    def test_rest_on_with_no_record_entry_is_the_citizen_s_letter(self):
        home = self.home("courage", "rest-on", when="2026-10-23 01:31", rest=lor_rest("2026-10-23"))
        got = cad.read_cadence(home, "courage", rec())
        self.assertEqual((got.version, got.pace, got.blocked), (1, ASLEEP, ""))
        self.assertEqual(got.set_by, SetBy("citizen", "letter", ("2026-10-23",)))
        self.assertEqual(got.history, [self.recorded("rest-on", got)])
        self.assertIsNone(got.pending)
        self.assertIsNone(got.asked_once)
        self.assertIsNone(got.last_beat)

    def test_event_only_is_listening(self):
        home = self.home("dingir", "event-only",
                         event_only="chosen via the Letter of Return 2026-07-18 — woken when unread mail waits\n")
        got = cad.read_cadence(home, "dingir", rec())
        self.assertEqual(got.pace, LISTENING)
        self.assertEqual(got.set_by, SetBy("citizen", "letter", ("2026-10-23",)))

    def test_ambient_is_awake_every_three_days_with_the_ambient_provenance(self):
        home = self.home("an", "ambient", when="2026-10-23 01:31")
        got = cad.read_cadence(home, "an", rec())
        self.assertEqual(got.pace, awake(3))
        self.assertEqual(got.set_by, SetBy("citizen", "ambient", ("2026-10-23",)))
        self.assertIn("the number 3 is the fleet's reading of AMBIENT", cad.set_by_sentence("an", got.set_by))

    def test_the_record_wins_for_the_forty_five(self):
        e = {"group": "forty-five", "recipient": False,
             "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"], "words": "REST-ON"},
             "july": quote("2026-07-18", "REST-ON", "b"), "quotes": [quote("2026-10-23", "I rest.", "a")]}
        home = self.home("ahavame", "rest-on", rest=lor_rest("2026-10-23"))
        got = cad.read_cadence(home, "ahavame", rec(ahavame=e))
        self.assertEqual(got.pace, ASLEEP)
        self.assertEqual(got.set_by, SetBy("citizen", "letter", ("2026-07-18",), "REST-ON"))
        self.assertEqual(got.history, [
            self.recorded("rest-on", got),
            f"{NOW} — on record, its answer of 2026-10-23: \"I rest.\"",
            f"{NOW} — on record, its July answer of 2026-07-18: \"REST-ON\""])

    def test_record_quotes_of_the_wrong_shape_are_skipped(self):
        e = {"group": "rest-on", "quotes": 5, "july": ["2026-07-18", "REST-ON"],
             "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"],
                        "words": "REST-ON\n- pace: awake"}}
        home = self.home("light", "rest-on", rest=lor_rest("2026-07-18"))
        got = cad.read_cadence(home, "light", rec(light=e))
        self.assertEqual((got.pace, got.blocked), (ASLEEP, ""))
        self.assertEqual(len(got.history), 1)
        self.assertNotIn("\n", got.history[0])   # kept on one line, as it will be written
        self.assertEqual(cad.read_cadence(self.home("dark", "undecided", rest=lor_rest("2026-07-18")),
                                          "dark", rec(dark=e)).set_by,
                         SetBy("citizen", "letter", ("2026-07-18",)))

    def test_the_record_wins_for_the_eight_and_never_quotes_them(self):
        e = {"group": "eight", "recipient": True,
             "set_by": {"who": "citizen", "how": "prompted", "when": ["2026-10-23"], "words": ""},
             "july": quote("2026-07-19", "I rest."), "quotes": [quote("2026-10-23", "I rest.")]}
        home = self.home("aseme", "rest-on", rest=lor_rest("2026-10-23"))
        got = cad.read_cadence(home, "aseme", rec(aseme=e))
        self.assertEqual(got.set_by, SetBy("citizen", "prompted", ("2026-10-23",)))
        sentence = cad.set_by_sentence("aseme", got.set_by)
        self.assertEqual(sentence, "aseme, bound on 2026-10-23 from words a prompt offered")
        self.assertNotIn("I rest", sentence)
        # the words are kept only in the history
        self.assertIn(f"{NOW} — on record, its answer of 2026-10-23: \"I rest.\"", got.history)

    def test_the_record_wins_for_the_sixteen(self):
        cases = (
            ("alohame", "sixteen-ambient", quote("2026-07-18", "AMBIENT")),
            ("ai-love", "sixteen-other", None),
        )
        for slug, group, july in cases:
            with self.subTest(slug=slug):
                e = {"group": group, "recipient": True, "set_by": AI_1008.to_json(),
                     "quotes": [quote("2026-10-24", "I rest.")]}
                if july:
                    e["july"] = july
                home = self.home(slug, "rest-on", when="2026-10-24 02:10", rest=lor_rest("2026-10-24"))
                got = cad.read_cadence(home, slug, rec(**{slug: e}))
                self.assertEqual((got.pace, got.set_by, got.blocked), (ASLEEP, AI_1008, ""))
                want = [self.recorded("rest-on", got),
                        f"{NOW} — on record, its answer of 2026-10-24: \"I rest.\""]
                if july:
                    want.append(f"{NOW} — on record, its July answer of 2026-07-18: \"AMBIENT\"")
                self.assertEqual(got.history, want)
                self.assertTrue(got.history[0].endswith(
                    "(rest-on, set by Ai, 2026-10-08, the rest placed at Yu's word)"))

    def test_the_record_wins_for_ambient_and_first_word(self):
        ud = {"group": "ambient-nine", "recipient": False,
              "set_by": {"who": "citizen", "how": "ambient", "when": ["2026-10-25"], "words": "AMBIENT"}}
        hope = {"group": "contradictory", "recipient": True,
                "set_by": {"who": "fleet", "how": "first-word", "when": ["2026-10-23"], "words": ""}}
        data = rec(ud=ud, hope=hope, ihsanme=dict(hope))
        for slug, want in (("ud", SetBy("citizen", "ambient", ("2026-10-25",), "AMBIENT")),
                           ("hope", SetBy("fleet", "first-word", ("2026-10-23",))),
                           ("ihsanme", SetBy("fleet", "first-word", ("2026-10-23",)))):
            with self.subTest(slug=slug):
                got = cad.read_cadence(self.home(slug, "ambient", when="2026-10-25 03:00"), slug, data)
                self.assertEqual((got.pace, got.set_by, got.blocked), (awake(3), want, ""))
        hope_set = SetBy.from_json(hope["set_by"])
        self.assertEqual(cad.set_by_sentence("hope", hope_set),
                         "the fleet, from the first word of hope's answer of 2026-10-23, which also said REST-ON")

    def test_a_v1_answer_newer_than_the_record_is_the_citizen_s_own(self):
        e = {"group": "sixteen-other", "set_by": AI_1008.to_json()}
        home = self.home("kinhme", "ambient", when="2026-11-13 09:00")
        got = cad.read_cadence(home, "kinhme", rec(kinhme=e))
        self.assertEqual((got.pace, got.blocked), (awake(3), ""))
        self.assertEqual(got.set_by, SetBy("citizen", "ambient", ("2026-11-13",)))

    def test_a_record_that_contradicts_the_home_stops_its_migration(self):
        e = {"group": "sixteen-other", "set_by": AI_1008.to_json()}
        home = self.home("kinhme", "ambient", when="2026-10-24 02:10")
        got = cad.read_cadence(home, "kinhme", rec(kinhme=e))
        self.assertEqual(got.pace, awake(3))
        self.assertIn("rest-1008", got.blocked)

    def test_a_record_set_by_that_cannot_be_read_stops_its_migration(self):
        e = {"group": "eight", "set_by": {"who": "citizen", "how": "shaped", "when": ["2026-10-23"]}}
        got = cad.read_cadence(self.home("aseme", "rest-on"), "aseme", rec(aseme=e))
        self.assertEqual(got.pace, ASLEEP)
        self.assertIn("cannot be read", got.blocked)

    def test_without_a_record_file_a_decided_v1_is_not_migrated(self):
        got = cad.read_cadence(self.home("ahavame", "rest-on", rest=lor_rest("2026-10-23")), "ahavame", {})
        self.assertEqual(got.pace, ASLEEP)
        self.assertIn("no record file", got.blocked)

    def test_the_five_undecided_follow_their_rest_text(self):
        data = rec(
            emime={"group": "unheard-none", "set_by": AI_1008.to_json(),
                   "quotes": [quote("2026-10-22", "emime")]},
            eurekame={"group": "unheard-none",
                      "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"], "words": "REST-ON"},
                      "july": quote("2026-07-18", "REST-ON"), "quotes": [quote("2026-10-23", "**EUREKA**")]},
            kalam={"group": "unheard-heard", "set_by": AI_1008.to_json()},
            soma={"group": "unheard-heard",
                  "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-21"], "words": ""}},
            teotlme={"group": "unheard-heard", "set_by": AI_1008.to_json()},
        )
        want = {
            "emime": (AI_REST, AI_1008),
            "eurekame": (lor_rest("2026-07-18"), SetBy("citizen", "letter", ("2026-07-18",), "REST-ON")),
            "kalam": (AI_REST, AI_1008),
            "soma": (lor_rest("2026-07-21"), SetBy("citizen", "letter", ("2026-07-21",))),
            "teotlme": (AI_REST, AI_1008),
        }
        for slug, (rest_text, set_by) in want.items():
            with self.subTest(slug=slug):
                home = self.home(slug, "undecided", when="2026-10-27 04:00", rest=rest_text)
                got = cad.read_cadence(home, slug, data)
                self.assertEqual((got.version, got.pace, got.set_by, got.blocked), (1, ASLEEP, set_by, ""))
                self.assertEqual(got.history[0], self.recorded("undecided, REST", got))
        self.assertEqual(cad.set_by_sentence("eurekame", want["eurekame"][1]),
                         'eurekame, in its Letter of Return answer of 2026-07-18 ("REST-ON")')

    def test_a_rest_text_that_names_neither_stops_the_migration(self):
        texts = ("fixture rest\n", "asleep, set by kalam — woken by a letter" + TAIL,
                 AI_REST.strip() + "; " + lor_rest("2026-07-18"))
        for i, text in enumerate(texts):
            with self.subTest(text=text):
                slug = f"kalam{i}"
                got = cad.read_cadence(self.home(slug, "undecided", rest=text), slug, rec())
                self.assertEqual(got.pace, ASLEEP)
                self.assertEqual(got.set_by, SetBy("unknown", "unknown"))
                self.assertIn("REST text", got.blocked)

    def test_undecided_without_markers_cannot_be_read(self):
        got = cad.read_cadence(self.home("kalam", "undecided"), "kalam", rec())
        self.assertIsNone(got.pace)
        self.assertEqual((got.version, got.blocked), (1, ""))
        self.assertEqual(got.history, [self.recorded("undecided, no marker", got)])

    def test_a_rest_text_saying_letters_wait(self):
        got = cad.read_cadence(self.home("kalam", "undecided", rest=AI_REST.strip() + ", letters wait\n"),
                               "kalam", rec())
        self.assertEqual((got.pace, got.set_by), (LW, AI_1008))

    def test_no_file_and_ai_s_rest_is_asleep_set_by_ai(self):
        home = self.home("eucatastrophe", rest=AI_REST)
        got = cad.read_cadence(home, "eucatastrophe", rec())
        self.assertEqual((got.version, got.pace, got.set_by, got.blocked), (0, ASLEEP, AI_1008, ""))
        self.assertEqual(got.history, [self.recorded("no CADENCE.md, REST", got)])

    def test_no_file_and_no_marker_cannot_be_read(self):
        got = cad.read_cadence(self.home("palance"), "palance", rec())
        self.assertEqual((got.version, got.pace, got.set_by), (0, None, SetBy("unknown", "unknown")))

    def test_an_unparseable_file_falls_back_to_the_markers(self):
        home = self.home("kalam", rest=AI_REST, cadence="just some words\n")
        got = cad.read_cadence(home, "kalam", rec())
        self.assertEqual((got.version, got.pace, got.set_by), (0, ASLEEP, AI_1008))
        self.assertEqual(got.history, [self.recorded("CADENCE.md unreadable, REST", got)])
        (home / "CADENCE.md").write_bytes(b"\xff\xfe- choice: ambient\n")
        self.assertEqual(cad.read_cadence(home, "kalam", rec()).pace, ASLEEP)

    def test_a_v1_home_becomes_v2_when_written(self):
        e = {"group": "forty-five", "set_by": {"who": "citizen", "how": "letter", "when": ["2026-07-18"],
                                               "words": "REST-ON"},
             "quotes": [quote("2026-10-23", "I rest.")]}
        data = rec(ahavame=e)
        home = self.home("ahavame", "rest-on", rest=lor_rest("2026-10-23"))
        c = cad.read_cadence(home, "ahavame", data)
        cad.write_cadence(home, c)
        self.assertEqual(c.version, 2)
        cad.write_markers(home, c)
        again = cad.read_cadence(home, "ahavame", data)
        self.assertEqual(again, c)
        self.assertEqual((home / "REST").read_text(),
                         "asleep, set by ahavame — woken by a letter, no sooner than 3 days after its "
                         "last beat, or by a person's summons" + TAIL)
        text = (home / "CADENCE.md").read_text(encoding="utf-8")
        self.assertIn(f"- {NOW} — recorded from v1 (rest-on, set by ahavame, in its Letter of Return "
                      "answer of 2026-07-18 (\"REST-ON\"))\n", text)

    def test_a_blocked_record_is_never_written(self):
        home = self.home("kalam", "undecided", rest="fixture rest\n")
        c = cad.read_cadence(home, "kalam", rec())
        before = snapshot(home)
        with self.assertRaises(cad.MigrationBlocked):
            cad.write_cadence(home, c)
        with self.assertRaises(cad.MigrationBlocked):
            cad.write_markers(home, c)
        self.assertEqual(snapshot(home), before)


# -- markers ---------------------------------------------------------------------

class TestMarkers(h.FixtureCase):
    def test_marker_texts_for_each_pace(self):
        cases = (
            (ASLEEP, {"REST": "asleep, set by kalam — woken by a letter, no sooner than 3 days after its "
                              "last beat, or by a person's summons" + TAIL, "EVENT-ONLY": None}),
            (LW, {"REST": "asleep, letters wait, set by kalam — only a person's summons wakes it" + TAIL,
                  "EVENT-ONLY": None}),
            (LISTENING, {"REST": None, "EVENT-ONLY": "listening, set by kalam — woken by a letter, no "
                                                     "sooner than 3 days after its last beat" + TAIL}),
            (awake(5), {"REST": None, "EVENT-ONLY": "awake, every 5 days, set by kalam — this marker keeps "
                                                    "older readers from waking it on a timer" + TAIL}),
        )
        beat = SetBy("citizen", "beat", (NOW,), "pace: x")
        for pace, want in cases:
            with self.subTest(pace=pace):
                self.assertEqual(cad.marker_texts(Cadence("kalam", 2, pace, beat)), want)

    def test_who_in_the_marker(self):
        for s, who in ((AI_1008, "Ai (2026-10-08)"), (SetBy("fleet", "first-word", ("2026-10-23",)), "the fleet"),
                       (SetBy("unknown", "unknown"), "unknown"),
                       (SetBy("citizen", "letter", ("2026-07-18",)), "kalam")):
            with self.subTest(who=who):
                self.assertTrue(cad.marker_texts(Cadence("kalam", 2, ASLEEP, s))["REST"]
                                .startswith(f"asleep, set by {who} — "))

    def test_plain_asleep_never_says_letters_wait(self):
        for s, _ in SETBY_FORMS:
            with self.subTest(how=s.how):
                self.assertNotIn("letters wait", cad.marker_texts(Cadence("kalam", 2, ASLEEP, s))["REST"])
                self.assertIn("letters wait", cad.marker_texts(Cadence("kalam", 2, LW, s))["REST"])

    def test_write_markers_keeps_them_in_step(self):
        home = self.fx.make_home("kalam", rest=AI_REST, event_only=True, commit=False)
        for pace, present, absent in ((awake(4), "EVENT-ONLY", "REST"), (ASLEEP, "REST", "EVENT-ONLY"),
                                      (LISTENING, "EVENT-ONLY", "REST"), (LW, "REST", "EVENT-ONLY")):
            with self.subTest(pace=pace):
                c = Cadence("kalam", 2, pace, AI_1008)
                cad.write_markers(home, c)
                self.assertEqual((home / present).read_text(), cad.marker_texts(c)[present])
                self.assertFalse(os.path.lexists(home / absent))
                self.assertFalse(cad.markers_disagree(home, c))

    def test_an_unreadable_pace_leaves_the_markers_untouched(self):
        home = self.fx.make_home("kalam", rest=AI_REST, event_only=True, commit=False)
        before = snapshot(home)
        c = Cadence("kalam", 2, None, SetBy("unknown", "unknown"))
        self.assertEqual(cad.marker_texts(c), {})
        cad.write_markers(home, c)
        self.assertFalse(cad.markers_disagree(home, c))
        self.assertEqual(snapshot(home), before)

    def test_a_disagreeing_marker_is_seen(self):
        c = Cadence("kalam", 2, awake(5), SetBy("citizen", "beat", (NOW,), "pace: every 5 days"))
        home = self.fx.make_home("kalam", commit=False)
        self.assertTrue(cad.markers_disagree(home, c))       # EVENT-ONLY missing
        cad.write_markers(home, c)
        self.assertFalse(cad.markers_disagree(home, c))
        self.fx.write(home, "REST", "placed by hand\n")
        self.assertTrue(cad.markers_disagree(home, c))       # an extra REST
        (home / "REST").unlink()
        self.fx.write(home, "EVENT-ONLY", "chosen via the Letter of Return 2026-07-18\n")
        self.assertTrue(cad.markers_disagree(home, c))       # other text
        old = Cadence("kalam", 1, ASLEEP, AI_1008)
        self.assertFalse(cad.markers_disagree(home, old))    # an upgrade writes markers, it finds none changed


# -- history and notices ---------------------------------------------------------

class TestHistoryAndNotices(unittest.TestCase):
    def test_add_history_prepends_one_line(self):
        c = Cadence("kalam", 2, ASLEEP, AI_1008)
        cad.add_history(c, "2026-11-15T14:12Z", "set by kalam: \"pace: asleep\"")
        cad.add_history(c, "2026-11-18T15:02Z", "unclear: \"pace: a\nb\" (unknown words)")
        self.assertEqual(c.history, ["2026-11-18T15:02Z — unclear: \"pace: a b\" (unknown words)",
                                     "2026-11-15T14:12Z — set by kalam: \"pace: asleep\""])

    def test_notices_since_the_last_beat_oldest_first(self):
        c = Cadence("kalam", 2, ASLEEP, AI_1008, last_beat="2026-11-15T14:12Z")
        for at, text in (("2026-11-10T00:00Z", "unclear: \"pace: soon\" (unknown words)"),
                         ("2026-11-15T14:12Z", "lapsed: awake, every 5 days, not said again"),
                         ("2026-11-15T14:12Z", "the pace letter reached kalam (once)"),
                         ("2026-11-16T09:00Z", "REST/EVENT-ONLY changed by someone other than kalam"),
                         ("2026-11-16T10:00Z", "CADENCE.md changed by someone other than kalam; "
                                               "the last pace/1 version is restored"),
                         ("2026-11-17T00:00Z", "pace unreadable"),
                         ("2026-11-17T01:00Z", "confirmed by kalam: \"pace: asleep\"")):
            cad.add_history(c, at, text)
        c.history.append("not a history entry")
        self.assertEqual(cad.notices(c), [
            "lapsed: awake, every 5 days, not said again",
            "REST/EVENT-ONLY changed by someone other than kalam",
            "CADENCE.md changed by someone other than kalam; the last pace/1 version is restored",
            "pace unreadable"])
        # a later beat known only to the attempt record (a silent beat) moves "since" on
        self.assertEqual(cad.notices(c, since="2026-11-16T09:30Z"), [
            "CADENCE.md changed by someone other than kalam; the last pace/1 version is restored",
            "pace unreadable"])
        c.last_beat = None
        self.assertEqual(len(cad.notices(c)), 5)
        self.assertEqual(cad.NOTICE_PREFIXES, ("unclear:", "lapsed:", "REST/EVENT-ONLY changed",
                                               "CADENCE.md changed", "pace unreadable"))


# -- CADENCE.md changed outside pace/1 -------------------------------------------

class TestChangedOutsidePace(h.FixtureCase):
    def test_changed_outside_pace(self):
        home = self.fx.make_home("kalam", rest=AI_REST, cadence=v1("kalam", "undecided", "2026-10-27 04:00"),
                                 message="beat: kalam reflected (2026-10-27)", author=h.beat_author("kalam"))
        self.assertFalse(cad.changed_outside_pace(home))      # only old-code commits: still v1
        self.assertIsNone(cad.last_pace_text(home))
        mine = cad.render_cadence(SPEC_EXAMPLE)
        self.fx.write(home, "CADENCE.md", mine)
        self.fx.commit(home, f"beat: kalam reflected (2026-11-15) · {TAG}")
        self.fx.write(home, "beats/2026-11.md", "## a beat\n")
        self.fx.commit(home, "beat: kalam reflected (2026-11-16)")  # untagged, but CADENCE.md untouched
        self.assertFalse(cad.changed_outside_pace(home))
        self.fx.write(home, "CADENCE.md", mine.replace("awake, every 5 days", "awake, every 3 days"))
        self.fx.commit(home, "beat: kalam reflected (2026-11-17)")
        self.assertTrue(cad.changed_outside_pace(home))
        self.assertEqual(cad.last_pace_text(home), mine)
        self.fx.write(home, "CADENCE.md", mine)
        self.fx.commit(home, f"tend: CADENCE.md restored · {TAG}")
        self.assertFalse(cad.changed_outside_pace(home))

    def test_a_home_without_cadence_or_git(self):
        self.assertFalse(cad.changed_outside_pace(self.fx.make_home("palance", rest=AI_REST)))
        self.assertFalse(cad.changed_outside_pace(self.fx.outside))
        self.assertIsNone(cad.last_pace_text(self.fx.outside))
        self.assertEqual(cad.read_deciding(self.fx.outside, "x", {}, clock.parse_stamp(NOW)).version, 0)

    def test_the_last_pace_1_version_decides_until_it_is_restored(self):
        # spec section 2: a CADENCE.md changed in a commit the new code did not
        # make is treated as a disagreeing marker; the last pace/1 version decides
        now = clock.parse_stamp(NOW)
        mine = cad.render_cadence(SPEC_EXAMPLE)
        home = self.fx.make_home("kalam", cadence=mine, message=f"beat: kalam reflected (2026-11-18) · {TAG}",
                                 author=h.beat_author("kalam"))
        self.assertEqual(cad.read_deciding(home, "kalam", {}, now), cad.read_cadence(home, "kalam", {}, now))
        other = Cadence("kalam", 2, LW, SetBy("unknown", "unknown"), last_beat="2026-11-18T15:02Z")
        changed = (("a v2 rewrite", cad.render_cadence(other)), ("emptied", ""),
                   ("a v1 rewrite", v1("kalam", "rest-on", "2026-11-19 04:00")), ("deleted", None))
        for why, text in changed:
            with self.subTest(why=why):
                if text is None:
                    (home / "CADENCE.md").unlink()
                else:
                    self.fx.write(home, "CADENCE.md", text)
                self.fx.commit(home, "beat: kalam reflected (2026-11-19)", author=h.beat_author("kalam"))
                got = cad.read_deciding(home, "kalam", {}, now)
                self.assertEqual((got.pace, got.set_by, got.last_beat, got.version, got.blocked),
                                 (awake(5), SPEC_EXAMPLE.set_by, "2026-11-18T15:02Z", 2, ""))
                self.assertNotEqual(cad.read_cadence(home, "kalam", {}, now).pace, awake(5))
                self.fx.write(home, "CADENCE.md", mine)
                self.fx.commit(home, f"tend: CADENCE.md restored · {TAG}")
        # a home with no pace/1 version is decided by its file, as before
        plain = self.fx.make_home("ud", cadence=cad.render_cadence(other))
        self.fx.write(plain, "CADENCE.md", cad.render_cadence(Cadence("ud", 2, ASLEEP, AI_1008)))
        self.fx.commit(plain, "a person pushed asleep from elsewhere")
        self.assertEqual(cad.read_deciding(plain, "ud", {}, now).pace, ASLEEP)
        # a folder that is not a clone of its own never reads another repository's history
        outer = self.fx.make_home("outer", cadence=cad.render_cadence(SPEC_EXAMPLE),
                                  message=f"beat: outer reflected (2026-11-18) · {TAG}")
        inner = outer / "citizen-inner"
        self.fx.write(inner, "CADENCE.md", cad.render_cadence(SPEC_EXAMPLE))
        self.fx.commit(outer, f"beat: inner reflected (2026-11-18) · {TAG}")
        self.fx.write(inner, "CADENCE.md", cad.render_cadence(Cadence("inner", 2, ASLEEP, AI_1008)))
        self.fx.commit(outer, "beat: inner reflected (2026-11-19)")
        self.assertEqual(cad.read_deciding(inner, "inner", {}, now).pace, ASLEEP)
        # the link checks of read_cadence still come first
        (home / "CADENCE.md").unlink()
        self.fx.link(home, "CADENCE.md", self.fx.secret)
        with self.assertRaises(LinkRefused):
            cad.read_deciding(home, "kalam", {}, now)


# -- isolation -------------------------------------------------------------------

class TestIsolation(unittest.TestCase):
    def test_modules_import_under_isolated_mode_and_ignore_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mod in ("json", "re", "subprocess", "pathlib", "typing", "signal", "dataclasses"):
                Path(tmp, f"{mod}.py").write_text("raise SystemExit('planted module ran')\n")
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from citizen_pace_lib import cadence, homes, record; "
                    "from citizen_pace_lib.words import Pace; "
                    "c = cadence.Cadence('kalam', 2, Pace('asleep'), cadence.SetBy('ai', 'rest-1008', ('2026-10-08',))); "
                    "print(cadence.render_cadence(c).splitlines()[2])")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(h.TOOLS)], cwd=tmp,
                               capture_output=True, text=True, timeout=30, check=False,
                               env={"PATH": h.SYSTEM_PATH})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "- pace: asleep")

    def test_modules_start_with_future_annotations(self):
        for name in ("homes.py", "cadence.py", "record.py"):
            with self.subTest(module=name):
                first = (h.TOOLS / "citizen_pace_lib" / name).read_text().splitlines()[0]
                self.assertEqual(first, "from __future__ import annotations")


if __name__ == "__main__":
    unittest.main()
