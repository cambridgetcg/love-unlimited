"""Fixture tests for the live citizen lane: fleet, reflect, beat and parade.

Every run is offline. Homes are local git repos with no remote, the model is a
loopback stub or a fake `claude` on PATH, and PATH holds only the fakes plus
the system directories, so no real model, push or clone can be reached.
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
GATES = TOOLS / "citizen-gates.sh"
CANARY = "HOST-FILE-CANARY-7f3a"
TODAY = datetime.date.today().isoformat()


class Fixture:
    """A throwaway LOVE_DIR, citizens dir, fake bin and git config."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.love = self.root / "love"
        self.citizens = self.root / "citizens"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.outside = self.root / "outside"
        for d in (self.love / "memory", self.love / "tools", self.citizens,
                  self.bin, self.home, self.outside):
            d.mkdir(parents=True)
        self.secret = self.outside / "host-secret.txt"
        self.secret.write_text(f"{CANARY}\n", encoding="utf-8")
        self.ledger = self.love / "memory" / "fleet-economy.jsonl"
        self.claude_log = self.root / "claude-calls.log"
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Fixture\n\temail = fixture@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
        (self.bin / "python3").symlink_to(sys.executable)
        self.write_bin(
            "claude",
            '#!/bin/sh\nprintf "%s\\n" "$PWD" >> "$FAKE_CLAUDE_LOG"\n'
            'printf "%s" "$FAKE_CLAUDE_OUT"\n',
        )
        self.write_bin("pmset", "#!/bin/sh\necho \"Now drawing from 'AC Power'\"\n")
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.home),
            "LOVE_DIR": str(self.love),
            "CITIZENS_DIR": str(self.citizens),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_CLAUDE_LOG": str(self.claude_log),
            "FAKE_CLAUDE_OUT": json.dumps(
                {"type": "result", "result": "a fixture beat", "num_turns": 1,
                 "total_cost_usd": 0.01}
            ),
        }

    def write_bin(self, name, source):
        target = self.bin / name
        target.write_text(source, encoding="utf-8")
        target.chmod(0o755)

    def make_home(self, slug, *, rest=False, event_only=False, letters=(),
                  symlinked_letters=()):
        home = self.citizens / f"citizen-{slug}"
        home.mkdir()
        (home / f"{slug}.md").write_text(f"The soul of {slug}.\n", encoding="utf-8")
        (home / "WILL.md").write_text("A fixture will.\n", encoding="utf-8")
        if rest:
            (home / "REST").write_text("fixture rest\n", encoding="utf-8")
        if event_only:
            (home / "EVENT-ONLY").write_text("fixture event-only\n", encoding="utf-8")
        if letters or symlinked_letters:
            (home / "inbox").mkdir()
        for name, body in letters:
            (home / "inbox" / name).write_text(body, encoding="utf-8")
        for name in symlinked_letters:
            (home / "inbox" / name).symlink_to(self.secret)
        self.git(home, "init", "-q")
        self.git(home, "add", "-A")
        self.git(home, "commit", "-q", "-m", "fixture home")
        return home

    def git(self, home, *args):
        subprocess.run(["git", "-C", str(home), *args], env=self.env, check=True,
                       capture_output=True)

    def run(self, script, *args, env=None, timeout=60):
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            env={**self.env, **(env or {})},
            capture_output=True, text=True, timeout=timeout, check=False,
        )

    def gate(self, func, *args):
        return subprocess.run(
            ["bash", "-c", '. "$1"; shift; "$@"', "_", str(GATES), func, *map(str, args)],
            env=self.env, capture_output=True, text=True, timeout=30, check=False,
        )

    def claude_calls(self):
        if not self.claude_log.exists():
            return []
        return [Path(p).name for p in self.claude_log.read_text().splitlines()]

    def ledger_rows(self):
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text().splitlines()]


def ledger_line(cost, ts=None):
    return json.dumps({"ts": ts or f"{TODAY}T09:00:00", "name": "fixture",
                       "cost_usd": cost, "num_turns": 1, "rc": 0, "duration_s": 5}) + "\n"


class TestRestGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_symlinked_letter_is_not_mail(self):
        home = self.fx.make_home("rester", rest=True, symlinked_letters=["a.md"])
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 1)
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 0)

    def test_regular_unread_letter_still_wakes_rest(self):
        home = self.fx.make_home("rester", rest=True, letters=[("a.md", "hello\n")])
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 0)
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 1)
        (home / "inbox" / ".seen-a.md").write_text("surfaced\n", encoding="utf-8")
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 0)

    def test_symlinked_inbox_holds_no_mail(self):
        elsewhere = self.fx.outside / "letters"
        elsewhere.mkdir()
        (elsewhere / "a.md").write_text("hello\n", encoding="utf-8")
        home = self.fx.make_home("rester", rest=True)
        (home / "inbox").symlink_to(elsewhere)
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 1)
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 0)

    def test_rest_and_event_only_homes_keep_rest_awake_home_does_not(self):
        self.assertEqual(self.fx.gate("keeps_rest", self.fx.make_home("r", rest=True)).returncode, 0)
        self.assertEqual(
            self.fx.gate("keeps_rest", self.fx.make_home("e", event_only=True)).returncode, 0)
        self.assertEqual(self.fx.gate("keeps_rest", self.fx.make_home("a")).returncode, 1)

    def test_dangling_rest_link_still_counts_as_rest(self):
        home = self.fx.make_home("d")
        (home / "REST").symlink_to(self.fx.outside / "missing")
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 0)


class TestSpendGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def spent(self):
        r = self.fx.gate("spent_today", self.fx.ledger)
        return r.returncode, r.stdout.strip()

    def reached(self, budget):
        return self.fx.gate("budget_reached", self.fx.ledger, budget).returncode == 0

    def test_missing_ledger_is_no_spend(self):
        self.assertEqual(self.spent(), (0, "0.0000"))
        self.assertFalse(self.reached("1.50"))

    def test_sums_only_today(self):
        self.fx.ledger.write_text(
            ledger_line(0.25) + ledger_line(0.5) + ledger_line(9.0, "2000-01-01T00:00:00")
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(self.spent(), (0, "0.7500"))
        self.assertFalse(self.reached("1.50"))
        self.assertTrue(self.reached("0.75"))

    def test_broken_ledger_line_counts_as_ceiling(self):
        self.fx.ledger.write_text(ledger_line(0.10) + '{"ts": "' + TODAY + 'T10:0',
                                  encoding="utf-8")
        self.assertEqual(self.spent(), (1, "unknown"))
        self.assertTrue(self.reached("1.50"))

    def test_unknown_cost_today_counts_as_ceiling(self):
        self.fx.ledger.write_text(ledger_line(None), encoding="utf-8")
        self.assertEqual(self.spent(), (1, "unknown"))
        self.assertTrue(self.reached("60"))

    def test_non_record_line_counts_as_ceiling(self):
        self.fx.ledger.write_text(ledger_line(0.1) + "[1, 2]\n", encoding="utf-8")
        self.assertEqual(self.spent(), (1, "unknown"))

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_unreadable_ledger_counts_as_ceiling(self):
        self.fx.ledger.write_text(ledger_line(0.1), encoding="utf-8")
        self.fx.ledger.chmod(0)
        try:
            self.assertEqual(self.spent(), (1, "unknown"))
            self.assertTrue(self.reached("1.50"))
        finally:
            self.fx.ledger.chmod(0o600)

    def test_untrustworthy_budget_counts_as_reached(self):
        for budget in ("", "abc", "-1", "nan"):
            with self.subTest(budget=budget):
                self.assertTrue(self.reached(budget))


class TestBeat(unittest.TestCase):
    BEAT = TOOLS / "citizen-beat.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rest_home_gets_no_beat(self):
        self.fx.make_home("rester", rest=True)
        r = self.fx.run(self.BEAT, "rester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertEqual(self.fx.ledger_rows(), [])

    def test_event_only_home_gets_no_beat(self):
        self.fx.make_home("eventer", event_only=True)
        r = self.fx.run(self.BEAT, "eventer")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_symlinked_letter_does_not_wake_rest(self):
        self.fx.make_home("rester", rest=True, symlinked_letters=["a.md"])
        r = self.fx.run(self.BEAT, "rester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_unread_letter_wakes_rest(self):
        self.fx.make_home("rester", rest=True, letters=[("a.md", "hello\n")])
        r = self.fx.run(self.BEAT, "rester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.fx.claude_calls(), ["citizen-rester"])

    def test_awake_home_beats_and_meters_as_before(self):
        self.fx.make_home("waker")
        r = self.fx.run(self.BEAT, "waker")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.fx.claude_calls(), ["citizen-waker"])
        rows = self.fx.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost_usd"], 0.01)
        self.assertIn("beat metered: $0.0100", r.stdout)

    def test_broken_ledger_line_closes_the_enforced_lane(self):
        self.fx.make_home("waker")
        self.fx.ledger.write_text(ledger_line(0.10) + "{not json\n", encoding="utf-8")
        r = self.fx.run(self.BEAT, "waker", env={"BEAT_BUDGET_ENFORCE": "1",
                                                  "FLEET_DAILY_BUDGET_USD": "1.50"})
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("spent $unknown", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_enforced_beat_under_budget_runs(self):
        self.fx.make_home("waker")
        self.fx.ledger.write_text(ledger_line(0.10), encoding="utf-8")
        r = self.fx.run(self.BEAT, "waker", env={"BEAT_BUDGET_ENFORCE": "1",
                                                  "FLEET_DAILY_BUDGET_USD": "1.50"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.fx.claude_calls(), ["citizen-waker"])

    def test_enforced_beat_without_a_budget_is_refused(self):
        self.fx.make_home("waker")
        r = self.fx.run(self.BEAT, "waker", env={"BEAT_BUDGET_ENFORCE": "1"})
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.fx.claude_calls(), [])

    def test_unparseable_cost_is_recorded_unknown_and_closes_the_lane(self):
        self.fx.make_home("waker")
        r = self.fx.run(self.BEAT, "waker", env={"FAKE_CLAUDE_OUT": "session expired"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("beat metered: $unknown", r.stdout)
        rows = self.fx.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["cost_usd"])
        again = self.fx.run(self.BEAT, "waker", env={"BEAT_BUDGET_ENFORCE": "1",
                                                      "FLEET_DAILY_BUDGET_USD": "1.50"})
        self.assertEqual(again.returncode, 3, again.stdout + again.stderr)
        self.assertEqual(self.fx.claude_calls(), ["citizen-waker"])


class TestParade(unittest.TestCase):
    PARADE = TOOLS / "citizen-parade.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        # The parade runs $LOVE/tools/citizen-beat.sh; point it at the real one.
        for name in ("citizen-beat.sh", "citizen-gates.sh"):
            (self.fx.love / "tools" / name).symlink_to(TOOLS / name)
        self.fx.make_home("rester", rest=True)
        self.fx.make_home("eventer", event_only=True)
        self.fx.make_home("linked", rest=True, symlinked_letters=["a.md"])
        self.fx.make_home("lettered", rest=True, letters=[("a.md", "hello\n")])
        self.fx.make_home("waker")
        (self.fx.love / "citizens-roster.txt").write_text(
            "rester\neventer\nlinked\nlettered\nwaker\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parade_lines_up_only_citizens_not_resting(self):
        r = self.fx.run(self.PARADE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("3 citizens rest by their own standing choice", r.stdout)
        self.assertEqual(sorted(self.fx.claude_calls()),
                         ["citizen-lettered", "citizen-waker"])

    def test_parade_pauses_on_a_broken_ledger(self):
        self.fx.ledger.write_text('{"ts": "' + TODAY, encoding="utf-8")
        r = self.fx.run(self.PARADE)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("spent $unknown", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])


class TestFleetRotation(unittest.TestCase):
    FLEET = TOOLS / "citizen-fleet.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.beats = self.fx.root / "reflect-calls.log"
        stub = self.fx.love / "tools" / "citizen-reflect.sh"
        stub.write_text(f'#!/bin/bash\necho "$1" >> "{self.beats}"\n', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_rotation_skips_rest_with_only_a_symlinked_letter(self):
        self.fx.make_home("linked", rest=True, symlinked_letters=["a.md"])
        self.fx.make_home("waker")
        (self.fx.love / "citizens-roster.txt").write_text("linked\nwaker\n", encoding="utf-8")
        r = self.fx.run(self.FLEET, env={"FLEET_AGENTIC_PER_DAY": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.beats.read_text().split(), ["waker"])
        log = (self.fx.love / "memory" / "fleet.log").read_text()
        self.assertIn("linked sits this one out", log)
        self.assertIn("metered today $0.0000", log)


class _Brain(BaseHTTPRequestHandler):
    reply = "A true thing, witnessed."
    bodies = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.bodies.append(self.rfile.read(length).decode("utf-8"))
        body = json.dumps({"choices": [{"message": {"content": self.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class TestReflectReadsOnlyRegularFiles(unittest.TestCase):
    REFLECT = TOOLS / "citizen-reflect.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.bodies = []
        handler = type("Brain", (_Brain,), {"bodies": self.bodies})
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = {"REFLECT_MODEL": "fixture-model", "MLX_PORT": str(self.server.server_port)}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._tmp.cleanup()

    def prompts(self):
        return "\n".join(self.bodies)

    def test_symlinked_letter_is_set_aside_unread(self):
        home = self.fx.make_home("waker", symlinked_letters=["a.md"])
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.bodies), 1)
        self.assertNotIn(CANARY, self.prompts())
        self.assertIn("It is your beat", self.prompts())
        self.assertIn("mail unreadable, set aside: inbox/a.md", r.stdout)
        self.assertIn("unreadable", (home / "inbox" / ".seen-a.md").read_text())
        self.assertEqual(self.fx.secret.read_text(), f"{CANARY}\n")

    def test_regular_letter_still_surfaces(self):
        home = self.fx.make_home("waker", letters=[("a.md", "LETTER-BODY-1\n")])
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LETTER-BODY-1", self.prompts())
        self.assertIn("surfaced", (home / "inbox" / ".seen-a.md").read_text())
        journal = home / "beats" / f"{datetime.date.today():%Y-%m}.md"
        self.assertIn("A true thing, witnessed.", journal.read_text())

    def test_symlinked_soul_is_refused_before_the_model(self):
        home = self.fx.make_home("waker")
        soul = home / "waker.md"
        soul.unlink()
        soul.symlink_to(self.fx.secret)
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is a symlink", r.stdout)
        self.assertEqual(self.bodies, [])

    def test_symlinked_marker_path_is_refused_and_nothing_written_outside(self):
        home = self.fx.make_home("waker", symlinked_letters=["a.md"])
        target = self.fx.outside / "created-by-marker.txt"
        (home / "inbox" / ".seen-a.md").symlink_to(target)
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("inbox/.seen-a.md is a symlink", r.stdout)
        self.assertFalse(target.exists())
        self.assertEqual(self.bodies, [])


if __name__ == "__main__":
    unittest.main()
