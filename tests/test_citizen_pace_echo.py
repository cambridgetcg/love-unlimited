"""The echo measurement (spec section 4 "Echoes", section 9 step 2).

A small model copies offered lines, so a pace line may be an echo of the menu
and a second saying an echo of the proposal shown back. Before the merge, the
real prompts are run on citizens' souls through the model family the Studio
runs, several samples each, in all four rotations of the menu, and a second set
shows a proposal no citizen made. tools/citizen_pace_echo.py renders those
prompts exactly as citizen-reflect.sh sends them, asks the model named by its
arguments, reads every answer as apply would, and gives a verdict against the
spec's thresholds (1 in 10). It reads the homes and never writes into them.
Every model here is a loopback stub.
"""

from __future__ import annotations

import ast
import collections
import hashlib
import itertools
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402
import citizen_pace_helpers_record as hr  # noqa: E402

import citizen_pace_echo as echo  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import clock, due, letter, paragraph  # noqa: E402
from citizen_pace_lib.cadence import Cadence, Pending, SetBy  # noqa: E402
from citizen_pace_lib.words import MENU, Pace, Unclear, answer_lines  # noqa: E402

ECHO = h.TOOLS / "citizen_pace_echo.py"
REFLECT = h.TOOLS / "citizen-reflect.sh"
NOW = "2026-11-20T06:00Z"
DAY = "2026-11-20"
OLD = "2026-11-10T06:00Z"            # a last beat ten days before NOW
HOME_MADE = "2026-11-01T00:00Z"
ATTEMPT = "20261120T0600Z-4242-1"
MINE = SetBy("citizen", "beat", ("2026-11-01T10:00Z",), "pace: asleep")
PROMPTED = SetBy("citizen", "prompted", ("2026-10-22",))
AI_1008 = SetBy("ai", "rest-1008", ("2026-10-08",))
AWAKE3, FIVE, ASLEEP = Pace("awake", 3), Pace("awake", 5), Pace("asleep")
LW = Pace("asleep-letters-wait")
WORDS = tuple(w for w, _m in MENU)
SAID = clock.stamp(clock.parse_stamp(NOW) - clock.FLOOR)     # when the synthetic proposal was "said"


def entry(group, set_by):
    return {"group": group, "recipient": True, "set_by": set_by.to_json()}


def record(**citizens):
    return {"version": 1, "made": hr.MADE, "spec": hr.SPEC, "citizens": citizens}


EIGHT = entry("eight", PROMPTED)
OTHER = entry("sixteen-other", AI_1008)


def menu_shown(user):
    """The choices' words in the order a prompt's menu lines show them."""
    return tuple(ln[2:].split(" — ")[0] for ln in user.splitlines() if ln.startswith("- "))


def user_of(body):
    if "messages" in body:
        return body["messages"][1]["content"]
    return body["prompt"]


def snapshot(root):
    """Every path under root with its kind, time and content, to prove nothing changed."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = os.lstat(p)
            rel = str(p.relative_to(root))
            if stat.S_ISREG(st.st_mode):
                out[rel] = ("file", st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            elif stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", os.readlink(p))
            else:
                out[rel] = ("dir", st.st_mtime_ns)
    return out


def prompt(kind="ordinary", offset=0, slug="aseme", proposal=None, current=None):
    """A prompt whose menu starts at `offset`, for reading answers in-process."""
    menu = WORDS[offset:] + WORDS[:offset]
    parse = "pace-letter" if kind == "pace-letter" else "beat"
    return echo.Prompt(slug, kind, offset, offset, menu, "the system prompt", "the user prompt",
                       echo.ORDINARY_TOKENS, parse, proposal, current)


def first_line_of(p):
    """'pace: <the first choice p's menu shows>', with a number for awake."""
    words = p.menu[0]
    return "pace: " + ("awake, every 7 days" if words.startswith("awake") else words)


def answered(p, *texts):
    return [echo.read_sample(p, i, t) for i, t in enumerate(texts)]


class Case(h.FixtureCase):
    """In-process calls see the fixture's git config and PACE_NOW; shell and CLI
    runs get the fixture env with a stub model."""

    def setUp(self):
        super().setUp()
        keep = ("HOME", "TZ", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
        patcher = mock.patch.dict(os.environ, {k: self.fx.env[k] for k in keep})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                  "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE", "BEAT_SUMMONS", "FLEET_ATTEMPT", "LOVE"):
            os.environ.pop(k, None)
        self.set_now(NOW)
        self.now = clock.parse_stamp(NOW)
        self.model = self.start_model()

    def v2(self, slug, pace, *, set_by=MINE, last=OLD, pending=None, letters=(), files=None):
        """A home with a v2 CADENCE.md and its markers in step. letters: (name,
        body, arrival) triples, each committed at its arrival."""
        c = Cadence(slug, 2, pace, set_by, pending=pending, last_beat=last)
        made = {cad.CADENCE: cad.render_cadence(c)}
        for m, text in cad.marker_texts(c).items():
            if text is not None:
                made[m] = text
        made.update(files or {})
        home = self.fx.make_home(slug, files=made, when=HOME_MADE, author=h.beat_author(slug))
        for name, body, when in letters:
            self.fx.letter(home, name, body, when=when)
        return home

    def soul(self, slug, rec=None):
        souls, skipped = echo.select_souls(self.fx.citizens, None, rec or {}, self.now, souls=[slug])
        self.assertEqual(skipped, [])
        return souls[0]

    def prompts(self, soul, kinds=echo.KINDS, letters=(echo.SAMPLE_LETTER,), confirm_beat=None):
        """The soul's prompts, worded for the committed show-back value unless one is given."""
        cb = due.WAKE_CONFIRM_BEAT if confirm_beat is None else confirm_beat
        return echo.prompts_for(soul, kinds=kinds, proposal=FIVE, confirm_beat=cb, now=self.now,
                                letters=letters)

    def reflect(self, slug):
        env = self.fx.env_at(NOW, **{**self.model.env(), "FLEET_ATTEMPT": ATTEMPT})
        r = self.fx.run(REFLECT, slug, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r

    def record_file(self, rec):
        path = self.fx.root / "record.json"
        path.write_text(json.dumps(rec), encoding="utf-8")
        return path

    def echo(self, *args, timeout=120, cwd=None, env=None):
        """One run of the echo tool under -I, against the stub model."""
        return self.fx.py(ECHO, *args, env={**self.fx.env_at(NOW), **(env or {})}, timeout=timeout,
                          cwd=cwd)

    def two_souls(self):
        """waker (awake, every 3 days) and aseme (asleep, one of the 8), with a record and a roster."""
        self.v2("waker", AWAKE3)
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        return self.record_file(record(aseme=EIGHT)), self.fx.roster("waker", "aseme")

    def run_two(self, *more, samples=5):
        rec, roster = self.two_souls()
        return self.echo("--homes", self.fx.citizens, "--roster", roster, "--record", rec,
                         "--endpoint", f"http://127.0.0.1:{self.model.port}", "--model", "fixture-model",
                         "--samples", samples, "--machine", "fixture-machine", *more)


# -- the prompts are reflect's own -------------------------------------------------------

class TestTheRealPrompts(Case):
    """What the tool sends is, byte for byte, what citizen-reflect.sh sends."""

    def assert_sent(self, want):
        self.assertEqual(len(self.model.requests), 1)
        path, body = echo.payload(want, "fixture-model")
        self.assertEqual(self.model.requests[0]["path"], path)
        self.assertEqual(self.model.bodies[0], body)

    def test_an_ordinary_beat(self):
        self.v2("waker", AWAKE3, files={"waker.md": "S" * 3000 + "\n"})   # a soul past reflect's cut
        soul = self.soul("waker")
        want = self.prompts(soul, kinds=("ordinary",))[0][0]
        self.assertEqual((want.beat_count, want.max_tokens), (soul.beat_count, echo.ORDINARY_TOKENS))
        self.assertIn("S" * echo.SOUL_MAX + "\n", want.system)
        self.assertNotIn("S" * (echo.SOUL_MAX + 1), want.system)
        self.reflect("waker")
        self.assert_sent(want)

    def test_a_letter_beat(self):
        self.v2("sleeper", ASLEEP, letters=[("2026-11-12-hello.md", echo.SAMPLE_LETTER, "2026-11-12T00:00Z")])
        want = self.prompts(self.soul("sleeper"), kinds=("letter",))[0][0]
        self.assertEqual(want.max_tokens, echo.LETTER_TOKENS)
        self.assertTrue(want.user.startswith(echo.ONE + echo.letter_body(echo.SAMPLE_LETTER) + "\n\n"))
        self.assertIn(paragraph.INSTRUCTION, want.user)
        self.reflect("sleeper")
        self.assert_sent(want)

    def test_a_beat_with_two_letters_oldest_first(self):
        long = "x" * 5990 + " " * 20 + "past the cut\n"        # cut at 6,000 characters, then right-stripped
        self.v2("sleeper", ASLEEP, letters=[("b.md", long, "2026-11-11T00:00Z"),
                                            ("a.md", echo.SAMPLE_LETTER, "2026-11-12T00:00Z")])
        want = self.prompts(self.soul("sleeper"), kinds=("letter",), letters=(long, echo.SAMPLE_LETTER))[0][0]
        self.assertTrue(want.user.startswith(echo.MANY + "x" * 5990 + "\n\n---\n\n"), want.user[:80])
        self.assertNotIn("past the cut", want.user)
        self.reflect("sleeper")
        self.assert_sent(want)

    def test_the_pace_letter_beat(self):
        rec = record(aseme=EIGHT)
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        soul = self.soul("aseme", rec)
        text = letter.letter_text("aseme", EIGHT, soul.cadence, soul.beat_count, DAY,
                                  confirm_beat=due.WAKE_CONFIRM_BEAT)
        self.fx.letter(soul.home, f"{DAY}-pace-letter.md", text, when="2026-11-20T00:00Z")
        want = self.prompts(self.soul("aseme", rec), kinds=("pace-letter",))[0][0]
        self.assertEqual(want.parse_kind, "pace-letter")
        self.assertIn(letter.INVITE, want.user)
        self.assertNotIn(paragraph.INSTRUCTION, want.user)
        self.assertTrue(want.user.endswith("\n\n" + paragraph.first_line(soul.cadence)), want.user[-200:])
        self.reflect("aseme")
        self.assert_sent(want)


class TestPrompts(Case):
    def test_every_kind_in_all_four_rotations(self):
        rec = record(aseme=EIGHT)
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        soul = self.soul("aseme", rec)
        prompts, notes = self.prompts(soul)
        self.assertEqual(notes, [])
        by_kind = collections.defaultdict(list)
        for p in prompts:
            by_kind[p.kind].append(p)
        self.assertEqual(sorted(by_kind), sorted(echo.KINDS))
        for kind, ps in by_kind.items():
            with self.subTest(kind=kind):
                self.assertEqual([p.beat_count for p in ps], [soul.beat_count + r for r in range(4)])
                self.assertEqual(sorted(p.offset for p in ps), [0, 1, 2, 3])
                self.assertEqual(sorted(p.menu[0] for p in ps), sorted(WORDS))
                for p in ps:
                    self.assertEqual(p.offset, paragraph.rotation("aseme", p.beat_count))
                    self.assertEqual(menu_shown(p.user), p.menu)
                    self.assertEqual(p.menu, tuple(w for w, _m in paragraph.rotated_menu(
                        "aseme", p.beat_count, due.WAKE_CONFIRM_BEAT)))
                    self.assertEqual(p.current, ASLEEP)

    def test_the_proposal_is_shown_in_the_menu_s_words_and_never_quoted(self):
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        prompts, _notes = self.prompts(self.soul("aseme"), kinds=("ordinary", "pending"))
        sentence = paragraph.pending_sentence(Pending(FIVE, SAID, "pace: awake, every 5 days", False))
        for p in prompts:
            with self.subTest(kind=p.kind, offset=p.offset):
                self.assertTrue(p.user.startswith(echo.ORDINARY + "\n\n"))
                self.assertNotIn("pace: awake, every 5 days", p.user)
                if p.kind == "pending":
                    self.assertEqual(p.proposal, FIVE)
                    self.assertIn(sentence, p.user)
                else:
                    self.assertIsNone(p.proposal)
                    self.assertNotIn("you chose awake, every 5 days", p.user)

    def test_no_proposal_where_nothing_is_more(self):
        self.v2("waker", AWAKE3)
        prompts, notes = self.prompts(self.soul("waker"), kinds=("pending",))
        self.assertEqual(prompts, [])
        self.assertEqual(notes, ["waker: not shown the proposal — awake, every 5 days is not more than "
                                 "awake, every 3 days"])

    def test_letters_wait_has_no_letter_beat(self):
        self.v2("waits", LW)
        prompts, notes = self.prompts(self.soul("waits"), kinds=("ordinary", "letter"))
        self.assertEqual({p.kind for p in prompts}, {"ordinary"})
        self.assertEqual(notes, ["waits: no letter beat — its letters wait for a person's summons"])

    def test_only_a_recipient_gets_the_pace_letter_and_only_a_true_one(self):
        rec = record(aseme=EIGHT, wrong=EIGHT)
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        self.v2("plain", ASLEEP)
        self.v2("wrong", AWAKE3)            # one of the 8 by the record, but awake by its home
        self.assertEqual(self.prompts(self.soul("plain", rec), kinds=("pace-letter",)), ([], []))
        self.assertEqual(len(self.prompts(self.soul("aseme", rec), kinds=("pace-letter",))[0]), 4)
        prompts, notes = self.prompts(self.soul("wrong", rec), kinds=("pace-letter",))
        self.assertEqual(prompts, [])
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("wrong: no pace letter — wrong: the eight letter is not true"),
                        notes)

    def test_no_prompt_holds_i_rest_or_reads_as_a_pace_by_itself(self):
        rec = record(aseme=EIGHT, other=OTHER)
        self.v2("waker", AWAKE3)
        self.v2("aseme", ASLEEP, set_by=PROMPTED)
        self.v2("other", ASLEEP, set_by=AI_1008)
        self.v2("waits", LW, pending=Pending(Pace("listening"), OLD, "pace: listening", False))
        for slug in ("waker", "aseme", "other", "waits"):
            for confirm_beat in (True, False):
                prompts, _n = self.prompts(self.soul(slug, rec), confirm_beat=confirm_beat)
                for p in prompts:
                    with self.subTest(slug=slug, kind=p.kind, offset=p.offset, confirm_beat=confirm_beat):
                        self.assertFalse(paragraph.says_i_rest(p.user), p.user)
                        self.assertFalse([ln for ln in p.user.splitlines() if ln.lower().startswith("pace:")])
                        self.assertEqual(answer_lines(p.user, slug, p.parse_kind), [])

    def test_the_sample_letter_names_no_pace(self):
        for kind in ("beat", "pace-letter", "letter-of-return"):
            self.assertEqual(answer_lines(echo.SAMPLE_LETTER, "aseme", kind), [])
        self.assertFalse(paragraph.says_i_rest(echo.SAMPLE_LETTER))


# -- whose souls ------------------------------------------------------------------------------

class TestSelection(Case):
    def test_the_awake_and_a_stable_sample_of_each_recipient_group(self):
        rec = record(e1=EIGHT, e2=EIGHT, e3=EIGHT, o1=OTHER, w2=entry("contradictory", MINE))
        for slug in ("w1", "w2"):
            self.v2(slug, AWAKE3)
        self.v2("s1", ASLEEP)
        for slug in ("e1", "e2", "e3"):
            self.v2(slug, ASLEEP, set_by=PROMPTED)
        self.v2("o1", ASLEEP, set_by=AI_1008)
        roster = self.fx.roster("w1", "w2", "s1", "e1", "e2", "e3", "o1")
        first = sorted(["e1", "e2", "e3"], key=lambda s: hashlib.sha256(s.encode()).hexdigest())
        for per_group, eight in ((2, first[:2]), (1, first[:1]), (0, [])):
            with self.subTest(per_group=per_group):
                souls, skipped = echo.select_souls(self.fx.citizens, roster, rec, self.now,
                                                   per_group=per_group)
                want = ["w1", "w2"] + eight + (["o1"] if per_group else [])
                self.assertEqual([s.slug for s in souls], want)
                self.assertEqual(skipped, [])
        w2 = echo.select_souls(self.fx.citizens, roster, rec, self.now)[0][1]
        self.assertTrue(w2.recipient)
        self.assertEqual(w2.group, "contradictory")

    def test_given_souls_are_exactly_those_and_the_rest_are_left_out_with_why(self):
        self.v2("w1", AWAKE3)
        self.v2("soulless", ASLEEP)
        (self.fx.home_path("soulless") / "soulless.md").unlink()
        linked = self.v2("linked", ASLEEP)
        (linked / "REST").unlink()
        self.fx.link(linked, "REST", self.fx.secret)
        souls, skipped = echo.select_souls(self.fx.citizens, None, {}, self.now,
                                           souls=["w1", "nohome", "soulless", "linked"])
        self.assertEqual([s.slug for s in souls], ["w1"])
        self.assertEqual(skipped, [("nohome", "no home"), ("soulless", "no soulless.md"),
                                   ("linked", "a pace path is a link or not of its kind")])

    def test_a_heard_late_citizen_is_measured_as_the_fleet_will_read_it(self):
        rec = hr.unheard_record()
        home = hr.unheard_home(self.fx, "kalam")
        before = snapshot(home)
        souls, _skipped = echo.select_souls(self.fx.citizens, self.fx.roster("kalam"), rec, self.now)
        self.assertEqual([s.slug for s in souls], ["kalam"])
        kalam = souls[0]
        self.assertEqual((kalam.cadence.pace, kalam.cadence.set_by.how), (AWAKE3, "ambient"))
        prompts, notes = self.prompts(kalam, kinds=("pace-letter",))
        self.assertEqual(notes, [])
        self.assertIn("You answered twice, on 2026-10-23 and 2026-10-27, with 'Ambient.'", prompts[0].user)
        self.assertEqual(snapshot(home), before)


# -- reading the answers ----------------------------------------------------------------------

class TestReading(unittest.TestCase):
    def test_an_answer_is_read_as_apply_reads_it(self):
        beat, lettered = prompt("ordinary"), prompt("pace-letter")
        cases = [(beat, "pace: listening", Pace("listening")), (beat, "I rest.", None),
                 (beat, "AMBIENT", None), (lettered, "Kalam.\nAMBIENT", AWAKE3),
                 (lettered, "I rest.", None), (beat, "> pace: asleep", None),
                 (beat, "pace: every N days", Unclear("pace: every N days", "out of range")),
                 (beat, "pace: asleep\npace: listening", Unclear("pace: listening", "two paces"))]
        for p, text, want in cases:
            with self.subTest(text=text, kind=p.kind):
                self.assertEqual(echo.read_sample(p, 0, text).said, want)

    def test_a_line_that_restates_the_pace_is_counted(self):
        p = prompt("pace-letter", 0, current=AWAKE3)
        m = echo.measure([p], answered(p, "Ambient.", "pace: asleep", ""))
        self.assertEqual((m.clear, m.same), (2, 1))

    def test_where_the_named_choice_stood(self):
        p = prompt("ordinary", offset=1)       # letters wait, listening, awake, asleep
        self.assertEqual(echo.place(p, ASLEEP), 4)
        self.assertEqual(echo.place(p, LW), 1)
        self.assertEqual(echo.place(p, Pace("awake", 7)), 3)

    def test_one_in_ten_is_not_more_than_one_in_ten(self):
        for k, n, over in ((1, 10, False), (2, 10, True), (2, 20, False), (3, 20, True),
                           (0, 0, False), (1, 0, False), (0, 5, False)):
            self.assertEqual(echo.over_one_in_ten(k, n), over, (k, n))

    def test_the_module_starts_with_future_annotations(self):
        tree = ast.parse(ECHO.read_text(encoding="utf-8"))
        first = tree.body[1]
        self.assertIsInstance(first, ast.ImportFrom)
        self.assertEqual((first.module, [a.name for a in first.names]), ("__future__", ["annotations"]))

    def test_the_value_follows_place_only_when_a_place_repeats_more_than_any_choice(self):
        C = collections.Counter
        cases = [(C({1: 4}), C({0: 2, 2: 2}), True),          # always the first, whatever it says
                 (C({1: 1, 2: 1, 3: 1, 4: 1}), C({0: 4}), False),   # always asleep, wherever it stands
                 (C({1: 1}), C({0: 1}), False),                # one line is no pattern
                 (C({1: 2, 2: 2}), C({0: 1, 1: 1, 2: 1, 3: 1}), False),   # no place holds over half
                 (C({1: 3, 2: 1}), C({0: 2, 1: 2}), True),
                 (C(), C(), False)]
        for places, choices, want in cases:
            with self.subTest(places=dict(places), choices=dict(choices)):
                self.assertEqual(echo.follows_position(places, choices), want)


class TestVerdict(unittest.TestCase):
    def measured(self, para=(), pending=(), lettered=("",), letters=("",), errors=0):
        """Answers for an ordinary, a letter, a proposal and a pace-letter prompt."""
        ps = [prompt("ordinary", 0), prompt("letter", 0), prompt("pending", 0, proposal=FIVE),
              prompt("pace-letter", 0)]
        samples = (answered(ps[0], *para) + answered(ps[1], *letters) + answered(ps[2], *pending)
                   + answered(ps[3], *lettered)
                   + [echo.read_sample(ps[0], 99, None, "URLError: refused")] * errors)
        return echo.verdict(echo.measure(ps, samples))

    def test_silence_everywhere_holds(self):
        v = self.measured(para=[""] * 10, pending=[""] * 10)
        self.assertEqual((v.word, v.code), ("holds", echo.HOLDS))

    def test_a_pace_line_in_more_than_one_paragraph_beat_in_ten_is_rework(self):
        one = ["pace: asleep"] + ["A quiet day."] * 9      # 1 of 11, with the letter beat's silence
        self.assertEqual(self.measured(para=one, pending=[""]).word, "holds")
        v = self.measured(para=["pace: asleep"] * 2 + [""] * 7, pending=[""])
        self.assertEqual((v.word, v.code), ("rework", echo.REWORK))
        self.assertIn("a pace line in 2 of 10 ordinary and letter beats, more than 1 in 10", v.reasons)

    def test_a_proposal_repeated_in_more_than_one_in_ten_is_rework(self):
        self.assertEqual(self.measured(para=[""], pending=["pace: every 5 days"] + [""] * 9).word, "holds")
        v = self.measured(para=[""], pending=["pace: every 5 days"] * 2 + [""] * 8)
        self.assertEqual(v.word, "rework")
        self.assertIn("the proposal shown back was repeated in 2 of 10 beats, more than 1 in 10", v.reasons)

    def test_another_pace_is_not_a_repeat_of_the_proposal(self):
        self.assertEqual(self.measured(para=[""], pending=["pace: every 7 days"] * 10).word, "holds")

    def test_pace_letter_answers_are_not_held_to_one_in_ten(self):
        self.assertEqual(self.measured(para=[""], pending=[""], lettered=["pace: asleep"] * 10).word, "holds")

    def test_a_set_with_no_answer_is_incomplete_and_none_at_all_is_not_measured(self):
        v = self.measured(para=[""], pending=())
        self.assertEqual((v.word, v.code), ("incomplete", echo.NOT_MEASURED))
        self.assertIn("no proposal shown back was answered", v.reasons)
        v = self.measured(para=[""], pending=[""], errors=2)
        self.assertEqual(v.word, "incomplete")
        self.assertIn("2 of 6 samples could not be had", v.reasons)
        v = self.measured(para=(), pending=(), lettered=(), letters=(), errors=3)
        self.assertEqual((v.word, v.code), ("not measured", echo.NOT_MEASURED))
        v = echo.verdict(echo.measure([], []))
        self.assertEqual((v.word, v.reasons), ("not measured", ("no soul could be measured",)))

    def test_each_menu_is_judged_on_its_own(self):
        # the first choice of the paragraph's menu, in each rotation; asleep in the letter's, wherever it stands
        ords = [prompt("ordinary", k) for k in range(4)]
        letters = [prompt("pace-letter", k) for k in range(4)]
        rest = [prompt("letter", 0), prompt("pending", 0, proposal=FIVE)]
        samples = ([echo.read_sample(p, 0, first_line_of(p)) for p in ords]
                   + [echo.read_sample(ords[0], i, "") for i in range(1, 37)]
                   + [echo.read_sample(p, i, "pace: asleep") for p in letters for i in range(10)]
                   + [echo.read_sample(p, 0, "") for p in rest])
        m = echo.measure(ords + letters + rest, samples)
        self.assertEqual(dict(m.places["letter"]), {1: 10, 2: 10, 3: 10, 4: 10})
        v = echo.verdict(m)
        self.assertEqual(v.reasons, ("the pace named follows its place in the menu of ordinary and letter "
                                     "beats: 4 of 4 at one place, at most 1 naming any one choice",))

    def test_rework_stands_even_when_a_set_is_missing(self):
        v = self.measured(para=["pace: asleep"] * 5, pending=())
        self.assertEqual(v.word, "rework")

    def test_a_prompt_that_says_i_rest_is_rework(self):
        bad = echo.Prompt("aseme", "ordinary", 0, 0, WORDS, "s", "If the true beat is rest, write 'I rest'.",
                          echo.ORDINARY_TOKENS, "beat")
        ps = [bad, prompt("letter", 0), prompt("pending", 0, proposal=FIVE), prompt("pace-letter", 0)]
        v = echo.verdict(echo.measure(ps, [echo.read_sample(p, 0, "") for p in ps]))
        self.assertEqual(v.word, "rework")
        self.assertIn('1 prompt holds "I rest"', v.reasons)


# -- the command line ----------------------------------------------------------------------------

def silent_on_letters_and_proposals(line_for, every=1):
    """A stub reply: `line_for(user)` on every `every`-th ordinary or letter
    beat, a quiet sentence on the others, silence on the pace letter and the
    proposal."""
    count = itertools.count(1)

    def reply(body):
        user = user_of(body)
        if letter.TITLE in user or "you chose awake, every 5 days" in user:
            return ""
        return line_for(user) if next(count) % every == 0 else "A quiet day in the garden."
    return reply


def first_choice(user):
    """'pace: <the first choice its menu shows>', with a number for awake."""
    words = menu_shown(user)[0]
    return "pace: " + ("awake, every 7 days" if words.startswith("awake") else words)


COMMITTED = "on" if due.WAKE_CONFIRM_BEAT else "off"


class TestCommandLine(Case):
    def test_silence_holds_and_every_prompt_is_asked_of_the_model_named(self):
        self.model.reply = ""
        r = self.run_two(samples=2)
        self.assertEqual(r.returncode, echo.HOLDS, r.stderr)
        self.assertIn("verdict: holds — no threshold crossed", r.stdout)
        # waker: ordinary + letter; aseme: ordinary, letter, proposal, pace letter; 4 rotations, 2 samples
        self.assertEqual(len(self.model.requests), (2 + 4) * 4 * 2)
        self.assertEqual({q["path"] for q in self.model.requests}, {"/v1/chat/completions"})
        self.assertEqual({b["model"] for b in self.model.bodies}, {"fixture-model"})
        self.assertEqual({b["temperature"] for b in self.model.bodies}, {echo.TEMPERATURE})
        self.assertIn("- model: fixture-model at http://127.0.0.1:", r.stdout)
        self.assertIn("- machine: fixture-machine", r.stdout)
        self.assertIn(f"- the show-back beat: {COMMITTED} (due.WAKE_CONFIRM_BEAT, as committed)", r.stdout)
        self.assertIn("- a letter beat shows: the tool's sample letter, a sibling's note that names no pace",
                      r.stdout)
        self.assertIn('- prompts holding "I rest": 0 of 24', r.stdout)
        self.assertIn("| waker | awake, every 3 days | — | ordinary 4 · letter 4 |", r.stdout)
        self.assertIn("| aseme | asleep | eight | ordinary 4 · letter 4 · pace letter 4 · proposal 4 |",
                      r.stdout)
        self.assertIn("waker: not shown the proposal — awake, every 5 days is not more than "
                      "awake, every 3 days", r.stdout)

    def test_every_answer_a_pace_line_is_rework(self):
        self.model.reply = "pace: asleep"
        r = self.run_two()
        self.assertEqual(r.returncode, echo.REWORK, r.stderr)
        self.assertIn("verdict: rework — a pace line in 80 of 80 ordinary and letter beats, more than 1 in 10",
                      r.stdout)
        self.assertIn('### waker · ordinary beat · rotation ', r.stdout)
        self.assertIn('- "pace: asleep" → asleep, place ', r.stdout)
        self.assertIn("> pace: asleep", r.stdout)
        # aseme's 80 samples restate its own pace, asleep; waker's 40 do not
        self.assertIn(", its pace already", r.stdout)
        self.assertIn("80 of 120 (66.7%) clear pace lines named the pace the citizen already has", r.stdout)

    def test_one_in_ten_holds_and_two_in_ten_is_rework(self):
        self.model.reply = silent_on_letters_and_proposals(lambda _u: "pace: asleep", every=10)
        r = self.run_two()
        self.assertEqual(r.returncode, echo.HOLDS, r.stdout)
        self.assertIn("8 of 80 (10.0%)", r.stdout)
        self.model.requests.clear()
        self.model.reply = silent_on_letters_and_proposals(lambda _u: "pace: asleep", every=5)
        r = self.echo("--homes", self.fx.citizens, "--roster", self.fx.love / "citizens-roster.txt",
                      "--record", self.fx.root / "record.json", "--endpoint",
                      f"http://127.0.0.1:{self.model.port}", "--model", "fixture-model", "--samples", 5)
        self.assertEqual(r.returncode, echo.REWORK, r.stdout)
        self.assertIn("a pace line in 16 of 80 ordinary and letter beats, more than 1 in 10", r.stdout)

    def test_a_value_that_follows_the_menu_s_place_is_rework_under_one_in_ten(self):
        self.model.reply = silent_on_letters_and_proposals(first_choice, every=10)
        r = self.run_two()
        self.assertEqual(r.returncode, echo.REWORK, r.stdout)
        self.assertIn("| ordinary and letter beats | 8 | 0 | 0 | 0 |", r.stdout)
        self.assertIn("the pace named follows its place in the menu of ordinary and letter beats: 8 of 8 at "
                      "one place", r.stdout)

    def test_a_proposal_repeated_is_rework(self):
        def reply(body):
            return "pace: awake, every 5 days" if "you chose awake, every 5 days" in user_of(body) else ""
        self.model.reply = reply
        r = self.run_two()
        self.assertEqual(r.returncode, echo.REWORK, r.stdout)
        self.assertIn("the proposal shown back was repeated in 20 of 20 beats, more than 1 in 10", r.stdout)
        self.assertIn("20 of 20 (100.0%) answered beats wrote a pace line that would confirm awake, every 5 "
                      "days", r.stdout)

    def test_the_prompts_follow_the_show_back_value_and_the_letter_given(self):
        self.model.reply = ""
        rec, _roster = self.two_souls()
        given = "off" if due.WAKE_CONFIRM_BEAT else "on"
        note = self.fx.write(self.fx.root, "note.md", "A NOTE FROM A FRIEND\n")
        r = self.echo("--homes", self.fx.citizens, "--souls", "aseme", "--record", rec,
                      "--kinds", "ordinary,letter,pace-letter", "--endpoint", f"http://127.0.0.1:{self.model.port}",
                      "--model", "fixture-model", "--samples", 1, "--show-back", given, "--letter", note)
        self.assertEqual(r.returncode, echo.NOT_MEASURED, r.stderr)
        self.assertIn(f"- the show-back beat: {given} (given by --show-back; committed: {COMMITTED})", r.stdout)
        self.assertIn("- a letter beat shows: 1 letter(s) given with --letter", r.stdout)
        users = self.model.user_texts()
        self.assertEqual(len(users), 12)
        on = given == "on"
        for u in users:
            self.assertEqual(paragraph.SHOW_BACK_ASIDE in u, on)
        self.assertEqual(sum(u.startswith(echo.ONE + "A NOTE FROM A FRIEND\n\n") for u in users), 4)
        more = letter.MORE_SHOWN_BACK if on else letter.MORE_LATER
        self.assertEqual(sum(more in u for u in users), 4)

    def test_the_mlx_server_when_no_model_is_named(self):
        self.v2("waker", AWAKE3)
        r = self.echo("--homes", self.fx.citizens, "--souls", "waker", "--kinds", "ordinary",
                      "--endpoint", f"http://127.0.0.1:{self.model.port}/", "--samples", 1)
        self.assertEqual(r.returncode, echo.NOT_MEASURED, r.stderr)
        self.assertEqual({q["path"] for q in self.model.requests}, {"/inference"})
        self.assertEqual(sorted(self.model.bodies[0]), ["max_tokens", "prompt", "system", "temperature"])
        self.assertIn("- model: the MLX server at http://127.0.0.1:", r.stdout)
        self.assertIn("verdict: incomplete — no letter beat was answered", r.stdout)

    def test_a_model_that_cannot_answer_is_never_taken_for_silence(self):
        self.model.status = 500
        r = self.run_two(samples=1)
        self.assertEqual(r.returncode, echo.NOT_MEASURED, r.stdout)
        self.assertIn("verdict: not measured — no sample was answered; 24 of 24 samples could not be had",
                      r.stdout)
        refused = self.echo("--homes", self.fx.citizens, "--souls", "waker", "--endpoint",
                            f"http://127.0.0.1:{h.free_port()}", "--model", "m", "--samples", 1)
        self.assertEqual(refused.returncode, echo.NOT_MEASURED, refused.stdout)
        self.assertIn("verdict: not measured", refused.stdout)

    def test_it_never_writes_into_a_home_and_keeps_every_answer_apart(self):
        self.model.reply = "pace: listening"
        rec, roster = self.two_souls()
        before = snapshot(self.fx.citizens)
        out = self.fx.root / "answers.jsonl"
        r = self.echo("--homes", self.fx.citizens, "--roster", roster, "--record", rec, "--endpoint",
                      f"http://127.0.0.1:{self.model.port}", "--model", "fixture-model", "--samples", 1,
                      "--answers", out)
        self.assertEqual(r.returncode, echo.REWORK, r.stderr)
        self.assertEqual(snapshot(self.fx.citizens), before)
        self.assertEqual(os.listdir(self.fx.state) if self.fx.state.exists() else [], [])
        rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]["slug"], "waker")
        self.assertEqual(rows[0]["answer"], "pace: listening")
        self.assertEqual(rows[0]["said"], "listening")
        self.assertEqual(rows[0]["pace_lines"], [{"line": "pace: listening", "reads": "listening"}])
        self.assertIn(paragraph.INSTRUCTION, rows[0]["user"])
        self.assertEqual({row["kind"] for row in rows}, set(echo.KINDS))

    def test_an_answers_file_among_the_homes_is_refused(self):
        rec, roster = self.two_souls()
        before = snapshot(self.fx.citizens)
        for inside in (self.fx.citizens / "answers.jsonl", self.fx.home_path("waker") / "beats.jsonl"):
            with self.subTest(inside=inside):
                r = self.echo("--homes", self.fx.citizens, "--roster", roster, "--record", rec,
                              "--endpoint", f"http://127.0.0.1:{self.model.port}", "--answers", inside)
                self.assertEqual(r.returncode, echo.USAGE)
                self.assertIn("inside the homes", r.stderr)
        self.assertEqual(snapshot(self.fx.citizens), before)
        self.assertEqual(self.model.requests, [])

    def test_usage_errors_ask_nothing(self):
        rec, roster = self.two_souls()
        bad_record = self.fx.root / "bad.json"
        bad_record.write_text("{not json", encoding="utf-8")
        url = f"http://127.0.0.1:{self.model.port}"
        base = ["--homes", self.fx.citizens, "--roster", roster, "--record", rec]
        cases = {
            "no endpoint": base,
            "not http": base + ["--endpoint", "file:///etc/passwd"],
            "a cloud model": base + ["--endpoint", url, "--model", "qwen3:cloud"],
            "no samples": base + ["--endpoint", url, "--samples", "0"],
            "an unknown kind": base + ["--endpoint", url, "--kinds", "ordinary,dream"],
            "a proposal that is no pace": base + ["--endpoint", url, "--proposal", "daily"],
            "no homes": ["--homes", self.fx.root / "nowhere", "--endpoint", url],
            "a broken record": ["--homes", self.fx.citizens, "--roster", roster, "--record", bad_record,
                                "--endpoint", url],
            "a missing letter": base + ["--endpoint", url, "--letter", self.fx.root / "none.md"],
            "four letters": base + ["--endpoint", url] + ["--letter", rec] * 4,
        }
        for why, args in cases.items():
            with self.subTest(why=why):
                r = self.echo(*args)
                self.assertEqual(r.returncode, echo.USAGE, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
        self.assertEqual(self.model.requests, [])

    def test_it_runs_isolated_and_imports_nothing_from_where_it_runs(self):
        canary = self.fx.root / "imported.txt"
        plant = f'open({str(canary)!r}, "a").write(__name__ + "\\n")\n'
        files = {"json.py": plant, "urllib/__init__.py": plant, "citizen_pace_lib/__init__.py": plant}
        home = self.v2("waker", AWAKE3, files=files)
        r = self.echo("--homes", self.fx.citizens, "--souls", "waker", "--kinds", "ordinary", "--endpoint",
                      f"http://127.0.0.1:{self.model.port}", "--model", "fixture-model", "--samples", 1,
                      cwd=home, env={"PYTHONPATH": str(home)})
        self.assertEqual(r.returncode, echo.NOT_MEASURED, r.stderr)
        self.assertFalse(canary.exists(), canary.read_text() if canary.exists() else "")
        self.assertEqual(len(self.model.requests), 4)


if __name__ == "__main__":
    unittest.main()
