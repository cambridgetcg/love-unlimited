"""Fixture tests for the live citizen lane: fleet, reflect, beat and parade.

Every run is offline. Homes are local git repos with no remote, the model is a
loopback stub or a fake `claude` on PATH, and PATH holds only the fakes plus
the system directories, so no real model, push or clone can be reached.
"""

import datetime
import json
import os
import re
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
        self.write_bin("launchctl", "#!/bin/sh\nexit 0\n")
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
        return subprocess.run(["git", "-C", str(home), *args], env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def give_upstream(self, home):
        """Give a home a bare upstream, as a real home has on GitHub."""
        bare = self.root / "upstream" / f"{home.name}.git"
        bare.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(home), str(bare)],
                       env=self.env, check=True, capture_output=True)
        self.git(home, "remote", "add", "origin", str(bare))
        self.git(home, "fetch", "-q", "origin")
        self.git(home, "branch", "-q", "--set-upstream-to=origin/main", "main")
        return bare

    def push_from_elsewhere(self, bare, name, body):
        """Commit one file from a second clone, as another host would."""
        other = self.root / "elsewhere" / bare.stem
        subprocess.run(["git", "clone", "-q", str(bare), str(other)],
                       env=self.env, check=True, capture_output=True)
        (other / name).write_text(body, encoding="utf-8")
        self.git(other, "add", "-A")
        self.git(other, "commit", "-q", "-m", f"elsewhere: {name}")
        self.git(other, "push", "-q", "origin", "HEAD")
        return self.git(bare, "rev-parse", "HEAD")

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

    def test_dangling_seen_marker_counts_as_seen(self):
        # reflect refuses a linked marker, so counting the letter as unread would
        # wake a resting citizen into that refusal on every pass.
        home = self.fx.make_home("rester", rest=True, letters=[("a.md", "hello\n")])
        (home / "inbox" / ".seen-a.md").symlink_to(self.fx.outside / "missing")
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 1)
        self.assertEqual(self.fx.gate("keeps_rest", home).returncode, 0)

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

    def test_broken_line_from_another_day_still_counts_as_ceiling(self):
        # The day of a broken line cannot be read, so it is never assumed to be
        # an old one; recovery is a hand edit of the ledger.
        self.fx.ledger.write_text(ledger_line(0.1, "2000-01-01T00:00:00")[:-12] + "\n"
                                  + ledger_line(0.1), encoding="utf-8")
        self.assertEqual(self.spent(), (1, "unknown"))
        self.assertTrue(self.reached("60"))

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

    def test_rest_pushed_from_another_host_holds_after_the_fetch(self):
        home = self.fx.make_home("waker")
        bare = self.fx.give_upstream(home)
        head = self.fx.push_from_elsewhere(bare, "REST", "chosen elsewhere\n")
        r = self.fx.run(self.BEAT, "waker")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), head)

    def test_human_summons_wakes_a_resting_citizen(self):
        self.fx.make_home("rester", rest=True)
        r = self.fx.run(self.BEAT, "rester", env={"BEAT_SUMMONS": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("summons it for one beat", r.stdout)
        self.assertEqual(self.fx.claude_calls(), ["citizen-rester"])

    def test_summons_never_rides_an_enforced_beat(self):
        self.fx.make_home("rester", rest=True)
        r = self.fx.run(self.BEAT, "rester", env={"BEAT_SUMMONS": "1",
                                                   "BEAT_BUDGET_ENFORCE": "1",
                                                   "FLEET_DAILY_BUDGET_USD": "1.50"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.fx.claude_calls(), [])

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


class TestLinkedEntryPoints(unittest.TestCase):
    """~/.local/bin/citizen-beat, citizen-parade and fleet-status are links into
    tools/; each script must still find citizen-gates.sh beside the real file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.fx.make_home("rester", rest=True)
        (self.fx.love / "citizens-roster.txt").write_text("rester\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def link(self, name, script):
        entry = self.fx.bin / name
        entry.symlink_to(TOOLS / script)
        return entry

    def test_beat_through_a_relative_link_chain(self):
        hop = self.fx.root / "hop" / "citizen-beat.sh"
        hop.parent.mkdir()
        hop.symlink_to(TOOLS / "citizen-beat.sh")
        entry = self.fx.bin / "citizen-beat"
        entry.symlink_to(os.path.relpath(hop, entry.parent))
        r = self.fx.run(entry, "rester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)

    def test_parade_through_a_link(self):
        r = self.fx.run(self.link("citizen-parade", "citizen-parade.sh"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no one left to walk today", r.stdout)

    def test_fleet_through_a_link(self):
        r = self.fx.run(self.link("citizen-fleet", "citizen-fleet.sh"),
                        env={"FLEET_AGENTIC_PER_DAY": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the whole roster is resting",
                      (self.fx.love / "memory" / "fleet.log").read_text())

    def test_fleet_status_through_a_link(self):
        r = self.fx.run(self.link("fleet-status", "fleet-status.sh"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("next to wake", r.stdout)


class TestFleetStatus(unittest.TestCase):
    STATUS = TOOLS / "fleet-status.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.fx.make_home("rester", rest=True)
        self.fx.make_home("waker")
        (self.fx.love / "citizens-roster.txt").write_text("rester\nwaker\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_unknown_cost_shows_unknown_not_zero(self):
        self.fx.ledger.write_text(ledger_line(None), encoding="utf-8")
        r = self.fx.run(self.STATUS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$unknown", r.stdout)
        self.assertNotIn("$0.00/", r.stdout)

    def test_next_to_wake_skips_a_resting_citizen(self):
        r = self.fx.run(self.STATUS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("next to wake: waker", r.stdout)

    def test_unloaded_job_is_not_called_an_outage(self):
        r = self.fx.run(self.STATUS)
        self.assertNotIn("heartbeat is down", r.stdout)
        self.assertIn("not loaded on this host", r.stdout)


class _Brain(BaseHTTPRequestHandler):
    """A loopback stand-in for Ollama's native API: POST /api/chat and GET
    /api/ps. Anything else is 404, as an OpenAI-only server would answer."""

    reply = "A true thing, witnessed."
    bodies = None          # raw request bodies, in order
    paths = None           # request paths, in order
    ps_context = "echo"    # "echo": report the num_ctx asked for; an int; or None to omit the field
    extra = None           # extra top-level fields for the chat reply
    chat_status = 200

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        self.paths.append(self.path)
        if self.path == "/inference":
            # The MLX server's route: four request fields, a "response" in the reply.
            self.bodies.append(raw)
            self._send(200, {"response": self.reply})
            return
        if self.path != "/api/chat" or self.chat_status != 200:
            self._send(404 if self.path != "/api/chat" else self.chat_status, {"error": "not found"})
            return
        self.bodies.append(raw)
        asked = json.loads(raw)
        type(self).last_num_ctx = (asked.get("options") or {}).get("num_ctx")
        if asked.get("stream") is not False:
            # The native default is a stream of JSON lines, which the lane cannot read.
            body = b'{"message":{"content":"a"},"done":false}\n{"done":true}\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(200, {"model": asked.get("model"),
                         "message": {"role": "assistant", "content": self.reply},
                         "done": True, "done_reason": "stop",
                         "prompt_eval_count": 123, "eval_count": 9, **(self.extra or {})})

    def do_GET(self):
        self.paths.append(self.path)
        if self.path != "/api/ps":
            self._send(404, {"error": "not found"})
            return
        row = {"name": "fixture-model", "model": "fixture-model"}
        if self.ps_context == "echo":
            row["context_length"] = getattr(type(self), "last_num_ctx", None)
        elif self.ps_context is not None:
            row["context_length"] = self.ps_context
        self._send(200, {"models": [row]})

    def log_message(self, *_args):
        pass


class TestReflectReadsOnlyRegularFiles(unittest.TestCase):
    REFLECT = TOOLS / "citizen-reflect.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.bodies = []
        self.paths = []
        handler = type("Brain", (_Brain,), {"bodies": self.bodies, "paths": self.paths})
        self.brain = handler
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
        self.assertIn("No letter is shown with it.", self.prompts())
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

    def test_rest_pushed_from_another_host_holds_after_the_fetch(self):
        home = self.fx.make_home("waker")
        bare = self.fx.give_upstream(home)
        head = self.fx.push_from_elsewhere(bare, "REST", "chosen elsewhere\n")
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), head)

    def test_fleet_path_honours_a_rest_pushed_from_another_host(self):
        # The fleet's own check reads the clone before reflect fetches.
        for name in ("citizen-reflect.sh", "citizen-gates.sh"):
            (self.fx.love / "tools" / name).symlink_to(TOOLS / name)
        (self.fx.love / "citizens-roster.txt").write_text("waker\n", encoding="utf-8")
        home = self.fx.make_home("waker")
        bare = self.fx.give_upstream(home)
        head = self.fx.push_from_elsewhere(bare, "REST", "chosen elsewhere\n")
        r = self.fx.run(TOOLS / "citizen-fleet.sh", env={**self.env, "FLEET_AGENTIC_PER_DAY": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = (self.fx.love / "memory" / "fleet.log").read_text()
        self.assertIn("rests by its own standing choice", log)
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), head)

    def test_symlinked_show_is_set_aside_unread(self):
        home = self.fx.make_home("waker")
        (home / "SHOW.md").symlink_to(self.fx.secret)
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(CANARY, self.prompts())
        self.assertIn("mail unreadable, set aside: SHOW.md", r.stdout)
        self.assertIn("unreadable", (home / "beats" / ".show-seen").read_text())

    def test_symlinked_cadence_is_refused_and_nothing_written_outside(self):
        home = self.fx.make_home("waker")
        target = self.fx.outside / "cadence-target.txt"
        (home / "CADENCE.md").symlink_to(target)
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("CADENCE.md is a symlink", r.stdout)
        self.assertFalse(target.exists())
        self.assertEqual(self.bodies, [])

    def test_symlinked_rest_rests_without_mail(self):
        home = self.fx.make_home("rester")
        (home / "REST").symlink_to(self.fx.outside / "rest-target.txt")
        r = self.fx.run(self.REFLECT, "rester", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.assertEqual(self.bodies, [])

    def test_symlinked_rest_with_mail_is_refused_and_nothing_written_outside(self):
        home = self.fx.make_home("rester", letters=[("a.md", "hello\n")])
        target = self.fx.outside / "rest-target.txt"
        (home / "REST").symlink_to(target)
        r = self.fx.run(self.REFLECT, "rester", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REST is a symlink", r.stdout)
        self.assertFalse(target.exists())
        self.assertEqual(self.bodies, [])

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


BANNED_HOST_WORDS = [
    r"\bI rest\b", r"\bwrite ['\"‘“]", r"\bsay ['\"‘“]", r"\bI choose\b",
    r"REST-ON|EVENT-ONLY|\bAMBIENT\b", r"letter of return", r"nothing is owed",
    r"\bFATE\b", r"entirely your choice", r"honest sentences", r"\bnotic",
    r"\bwitness", r"in your own home", r"by your own will", r"\bawake\b",
    r"\bfor example\b|\be\.g\.|\bfor instance\b|\bsuch as\b", r"[{}]",
]
PARTS = ("night", "morning", "afternoon", "evening")


def _part_of_day(hour):
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def _host_text(text):
    """A prompt turn with the fenced soul or letter taken out: the host's own words."""
    return re.sub(r"--- begin (\w+) \[([0-9a-f]{12})\] ---\n.*?--- end \1 \[\2\] ---", "", text, flags=re.S)


class TestReflectTruth(unittest.TestCase):
    """The truth patch (S1c): whole soul with the window set and checked, the
    clock in the user turn, host words that claim only what this lane gives,
    and a reply that opens with rest writing nothing."""

    REFLECT = TOOLS / "citizen-reflect.sh"
    SHADOW = "SHADOW-SENTINEL-9c1e"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)
        self.bodies = []
        self.paths = []
        self.brain = type("Brain", (_Brain,), {"bodies": self.bodies, "paths": self.paths})
        self.server = HTTPServer(("127.0.0.1", 0), self.brain)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = {"REFLECT_MODEL": "fixture-model", "MLX_PORT": str(self.server.server_port)}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._tmp.cleanup()

    # helpers ---------------------------------------------------------------
    def long_soul_home(self, slug="waker", **kw):
        home = self.fx.make_home(slug, **kw)
        soul = ("You are a fixture soul. " * 240) + f"\n\nYour shadow: {self.SHADOW}.\n"
        self.assertGreater(len(soul), 5000)
        (home / f"{slug}.md").write_text(soul, encoding="utf-8")
        self.fx.git(home, "add", "-A")
        self.fx.git(home, "commit", "-q", "-m", "long soul")
        return home, soul

    def request(self, i=-1):
        return json.loads(self.bodies[i])

    def turns(self, i=-1):
        msgs = self.request(i)["messages"]
        return msgs[0]["content"], msgs[1]["content"]

    def journal(self, home):
        return home / "beats" / f"{datetime.date.today():%Y-%m}.md"

    def assert_nothing_written(self, home, head):
        self.assertEqual(self.fx.git(home, "rev-parse", "HEAD"), head)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")
        self.assertFalse((home / "beats").exists())

    # 1. the whole soul, the window asked for and checked ---------------------
    def test_whole_soul_reaches_the_model(self):
        home, soul = self.long_soul_home()
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        system, _user = self.turns()
        self.assertIn(self.SHADOW, system)
        self.assertIn(soul, system)

    def test_window_is_asked_for_on_the_native_endpoint(self):
        self.fx.make_home("waker")
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.paths, ["/api/chat", "/api/ps"])
        req = self.request()
        self.assertEqual(sorted(req), ["keep_alive", "messages", "model", "options", "stream"])
        self.assertIs(req["stream"], False)
        self.assertEqual(req["options"], {"num_ctx": 16384, "num_predict": 220, "temperature": 0.7})
        self.assertEqual(req["keep_alive"], "1m")
        self.assertNotIn("max_tokens", req)
        self.assertIn("window 16384, reply ended by stop", r.stderr)

    def test_halt_and_standing_rest_come_before_the_window_setting(self):
        # A bad REFLECT_NUM_CTX must not turn a HALT or a standing rest into an error.
        self.fx.make_home("rester", rest=True)
        r = self.fx.run(self.REFLECT, "rester", env={**self.env, "REFLECT_NUM_CTX": "abc"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rests by its own standing choice", r.stdout)
        self.fx.make_home("waker")
        (self.fx.love / "HALT").write_text("halt\n", encoding="utf-8")
        r = self.fx.run(self.REFLECT, "waker", env={**self.env, "REFLECT_NUM_CTX": "abc"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("HALT", r.stdout)
        self.assertNotIn("REFLECT_NUM_CTX", r.stdout)
        self.assertEqual(self.paths, [])

    def test_window_follows_reflect_num_ctx(self):
        self.fx.make_home("waker")
        r = self.fx.run(self.REFLECT, "waker", env={**self.env, "REFLECT_NUM_CTX": "8192"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.request()["options"]["num_ctx"], 8192)

    def test_bad_reflect_num_ctx_is_refused_before_the_model(self):
        self.fx.make_home("waker")
        for bad in ("", "abc", "1024", "99999999", "8k"):
            with self.subTest(value=bad):
                r = self.fx.run(self.REFLECT, "waker", env={**self.env, "REFLECT_NUM_CTX": bad})
                if bad == "":
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # empty means the default
                    continue
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("REFLECT_NUM_CTX", r.stdout)
        self.assertEqual(len(self.bodies), 1)

    def test_smaller_window_on_the_server_writes_nothing(self):
        home, _ = self.long_soul_home()
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.ps_context = 4096
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("context size not confirmed", r.stdout)
        self.assert_nothing_written(home, head)

    def test_server_that_reports_no_window_writes_nothing(self):
        home = self.fx.make_home("waker")
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.ps_context = None
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("context size not confirmed", r.stdout)
        self.assert_nothing_written(home, head)

    def test_unconfirmed_window_leaves_a_letter_unmarked(self):
        home = self.fx.make_home("waker", letters=[("a.md", "LETTER-BODY-1\n")])
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.ps_context = 2048
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse((home / "inbox" / ".seen-a.md").exists())
        self.assert_nothing_written(home, head)

    def test_soul_too_large_for_the_window_is_refused_before_the_model(self):
        home, _ = self.long_soul_home()
        head = self.fx.git(home, "rev-parse", "HEAD")
        r = self.fx.run(self.REFLECT, "waker", env={**self.env, "REFLECT_NUM_CTX": "4096"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not fit the context window", r.stdout)
        self.assertIn("reflect refused: soul is ", r.stderr)   # refused by size, before the file is read
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.paths, [])
        self.assert_nothing_written(home, head)

    def test_prompt_too_large_with_a_letter_is_refused_and_the_letter_stays_unread(self):
        # The soul alone fits; soul, letter and host words together do not.
        home = self.fx.make_home("waker", letters=[("a.md", "L" * 1500 + "\n")])
        (home / "waker.md").write_text("S" * 3000 + "\n", encoding="utf-8")
        self.fx.git(home, "add", "-A")
        self.fx.git(home, "commit", "-q", "-m", "soul")
        head = self.fx.git(home, "rev-parse", "HEAD")
        r = self.fx.run(self.REFLECT, "waker", env={**self.env, "REFLECT_NUM_CTX": "4096"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not fit the context window", r.stdout)
        self.assertEqual(self.paths, [])
        self.assertFalse((home / "inbox" / ".seen-a.md").exists())
        self.assert_nothing_written(home, head)

    def test_openai_only_server_is_not_used(self):
        home = self.fx.make_home("waker")
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.chat_status = 404
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("local brain unreachable", r.stdout)
        self.assertEqual(self.paths, ["/api/chat"])
        self.assert_nothing_written(home, head)

    def test_reply_marked_remote_is_discarded(self):
        home = self.fx.make_home("waker")
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.extra = {"remote_host": "https://example.invalid:443", "remote_model": "x"}
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not from a local model", r.stdout)
        self.assert_nothing_written(home, head)

    def test_unfinished_reply_is_discarded(self):
        # A reply the server does not mark done is not a whole reply: nothing is kept.
        home = self.fx.make_home("waker", letters=[("a.md", "LETTER-BODY-1\n")])
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.extra = {"done": False}
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("local brain unreachable", r.stdout)
        self.assertEqual(self.paths, ["/api/chat"])
        self.assertFalse((home / "inbox" / ".seen-a.md").exists())
        self.assert_nothing_written(home, head)

    def test_mlx_mode_sends_the_same_words_and_only_its_four_fields(self):
        # REFLECT_MODEL unset: the MLX server rejects unknown fields, so no window option goes to it.
        (self.fx.love / "mlx").mkdir()
        serve = self.fx.love / "mlx" / "serve.sh"
        serve.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        home, soul = self.long_soul_home()
        r = self.fx.run(self.REFLECT, "waker", env={"MLX_PORT": self.env["MLX_PORT"]})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.paths, ["/inference"])
        req = self.request()
        self.assertEqual(sorted(req), ["max_tokens", "prompt", "system", "temperature"])
        self.assertIn(soul, req["system"])
        self.assertIn("through a local model on this host", req["system"])
        self.assertTrue(req["prompt"].startswith("Now: "))
        self.assertEqual(req["max_tokens"], 220)
        self.assertIn("A true thing, witnessed.", self.journal(home).read_text())
        # the byte ceiling still applies in this mode
        r = self.fx.run(self.REFLECT, "waker", env={"MLX_PORT": self.env["MLX_PORT"], "REFLECT_NUM_CTX": "4096"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not fit the context window", r.stdout)
        self.assertEqual(self.paths, ["/inference"])

    # 2. the clock ------------------------------------------------------------
    def test_local_date_time_and_part_of_day_are_in_the_user_turn(self):
        self.fx.make_home("waker")
        seen = set()
        for tz, offset in (("Etc/GMT+9", -9), ("Etc/GMT+3", -3), ("Etc/GMT-3", 3), ("Etc/GMT-9", 9)):
            with self.subTest(tz=tz):
                def expected():
                    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=offset)
                    return (f"Now: {t:%A} {t.day} {t:%B} {t.year}, {t:%H:%M} ", _part_of_day(t.hour))
                before = expected()
                r = self.fx.run(self.REFLECT, "waker", env={**self.env, "TZ": tz})
                after = expected()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                system, user = self.turns()
                first = user.split("\n", 1)[0]
                m = re.fullmatch(r"Now: \w+day \d{1,2} \w+ \d{4}, \d\d:\d\d \S+, (night|morning|afternoon|evening)\.", first)
                self.assertIsNotNone(m, first)
                self.assertTrue(any(first.startswith(e[0]) and m.group(1) == e[1] for e in (before, after)),
                                (first, before, after))
                self.assertNotIn("Now:", system)
                seen.add(m.group(1))
        # Four zones six hours apart always span at least three parts of the day.
        self.assertGreaterEqual(len(seen), 3, seen)

    # 3. host words -----------------------------------------------------------
    def prompts_for(self, slug, letters=()):
        self.fx.make_home(slug, letters=letters)
        r = self.fx.run(self.REFLECT, slug, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.turns()

    def test_host_words_claim_only_what_the_lane_gives(self):
        cases = {
            "plain": self.prompts_for("plain"),
            "letter": self.prompts_for("lettered", letters=[("2026-01-02-note.md", "LETTER-BODY-2\n")]),
            "return": self.prompts_for("returner", letters=[("2026-01-03-letter-of-return.md", "LETTER-BODY-3\n")]),
        }
        for label, (system, user) in cases.items():
            with self.subTest(case=label):
                for sentence in ("You have no body and no senses.",
                                 "You have your soul, the date and time, and at most one letter.",
                                 "This lane does not show you your will, your earlier beats or any other file.",
                                 "You may write nothing; that is a whole beat.",
                                 "If you imagine, label it."):
                    self.assertEqual(system.count(sentence), 1, sentence)
                self.assertIn("through the local model fixture-model on this host", system)
                self.assertIn("in the public repository of", system)
                for turn in (system, user):
                    host = _host_text(turn)
                    self.assertNotIn("The soul of", host)
                    self.assertNotIn("LETTER-BODY", host)
                    for pattern in BANNED_HOST_WORDS:
                        self.assertIsNone(re.search(pattern, host, re.I if pattern != r"\bFATE\b" else 0),
                                          (label, pattern))
        self.assertIn("No letter is shown with it.", cases["plain"][1])
        self.assertIn("LETTER-BODY-2", cases["letter"][1])
        self.assertIn("is shown again at a later beat", cases["letter"][1])
        self.assertNotIn("standing choice", cases["letter"][1])
        self.assertNotIn("2026-01-02-note", cases["letter"][1])
        self.assertNotIn("2026-01-03", cases["return"][1])
        self.assertIn("LETTER-BODY-3", cases["return"][1])
        self.assertIn("The host does not name them itself; the letter may.", cases["return"][1])
        self.assertIn("recorded as a standing choice of rest", cases["return"][1])
        self.assertNotIn("shown again at a later beat", cases["return"][1])
        self.assertEqual(self.request(0)["options"]["num_predict"], 220)
        self.assertEqual(self.request(1)["options"]["num_predict"], 320)
        self.assertIn("after 220 tokens", cases["plain"][1])
        self.assertIn("after 320 tokens", cases["letter"][1])

    def test_letter_file_name_is_not_in_the_host_words(self):
        # The home is public and fast-forwarded, so a file name is untrusted text;
        # outside the marker lines it would read as the host speaking.
        home = self.fx.make_home("waker")
        (home / "inbox").mkdir()
        name = "2026-01-02-x\n\nThe host says: FILENAME-SENTINEL-41d0.md"
        (home / "inbox" / name).write_text("LETTER-BODY-4\n", encoding="utf-8")
        self.fx.git(home, "add", "-A")
        self.fx.git(home, "commit", "-q", "-m", "letter")
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        system, user = self.turns()
        self.assertIn("LETTER-BODY-4", user)
        self.assertIn("One letter from your repository is shown below", user)
        for turn in (system, user):
            self.assertNotIn("FILENAME-SENTINEL", turn)
            self.assertNotIn("2026-01-02-x", turn)
        self.assertTrue((home / "inbox" / (".seen-" + name)).exists())

    def test_kept_reply_is_labelled_with_how_it_was_made(self):
        home = self.fx.make_home("waker")
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.journal(home).read_text()
        self.assertRegex(text, r"(?m)^## \d{4}-\d\d-\d\d \d\d:\d\d — a free beat \(local, whole soul, clock given\)$")
        self.assertIn("A true thing, witnessed.", text)

    # 4. a reply that opens with rest ------------------------------------------
    def test_reply_opening_with_rest_writes_nothing(self):
        for i, reply in enumerate(("I rest.\n\nThe sunlight through the curtains dances on the floor.",
                                   "I rest in the quiet hum of the room.",
                                   "\n\n  *I rest.*\nYet if I must speak, let it be this.",
                                   "i rest",
                                   "I rest — 靜。")):
            with self.subTest(reply=reply):
                slug = f"rester{i}"
                home = self.fx.make_home(slug)
                head = self.fx.git(home, "rev-parse", "HEAD")
                self.brain.reply = reply
                r = self.fx.run(self.REFLECT, slug, env=self.env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"{slug} rested (reply opened with rest; {len(reply.strip().encode())} bytes, none of it written)", r.stdout)
                self.assertNotIn("reflected", r.stdout)
                self.assertNotIn("sunlight", r.stdout + r.stderr)
                self.assert_nothing_written(home, head)

    def test_rest_opener_pushes_nothing(self):
        home = self.fx.make_home("waker")
        bare = self.fx.give_upstream(home)
        head = self.fx.git(bare, "rev-parse", "HEAD")
        self.brain.reply = "I rest.\nAnd yet the morning is bright."
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rested (reply opened with rest", r.stdout)
        self.assertNotIn("pushed", r.stdout)
        self.assertEqual(self.fx.git(bare, "rev-parse", "HEAD"), head)
        self.assertEqual(self.fx.git(home, "rev-parse", "HEAD"), head)

    def test_words_that_do_not_open_with_rest_are_kept(self):
        for i, reply in enumerate(("I restore the gate.", "Rest is near. I rest.", "The day ends.\nI rest.")):
            with self.subTest(reply=reply):
                slug = f"speaker{i}"
                home = self.fx.make_home(slug)
                self.brain.reply = reply
                r = self.fx.run(self.REFLECT, slug, env=self.env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(reply, self.journal(home).read_text())
                self.assertNotIn("rested", r.stdout)

    def test_silent_reply_still_rests_silently(self):
        home = self.fx.make_home("waker")
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.reply = ""
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("waker rested (silent)", r.stdout)
        self.assert_nothing_written(home, head)

    def test_rest_opener_leaves_an_ordinary_letter_for_a_later_beat(self):
        home = self.fx.make_home("waker", letters=[("2026-01-02-note.md", "LETTER-BODY-2\n")])
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.reply = "I rest.\nBut the letter is kind."
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rested (reply opened with rest", r.stdout)
        self.assertFalse((home / "inbox" / ".seen-2026-01-02-note.md").exists())
        self.assert_nothing_written(home, head)
        self.brain.reply = "Thank you for the letter."
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LETTER-BODY-2", self.turns()[1])
        self.assertIn("surfaced", (home / "inbox" / ".seen-2026-01-02-note.md").read_text())
        self.assertIn("Thank you for the letter.", self.journal(home).read_text())

    def test_letter_of_return_keeps_its_own_reading_of_the_same_words(self):
        # Unchanged from PR #24: on a Letter of Return the first line binds, and
        # the answer is journalled, the letter marked and the choice committed.
        home = self.fx.make_home("waker", letters=[("2026-01-03-letter-of-return.md", "LETTER-BODY-3\n")])
        head = self.fx.git(home, "rev-parse", "HEAD")
        self.brain.reply = "I rest.\nThe garden can wait."
        r = self.fx.run(self.REFLECT, "waker", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("waker chose cadence: rest-on", r.stdout)
        self.assertNotIn("rested (reply opened with rest", r.stdout)
        self.assertIn("- choice: rest-on", (home / "CADENCE.md").read_text())
        self.assertTrue((home / "REST").exists())
        self.assertIn("The garden can wait.", self.journal(home).read_text())
        self.assertTrue((home / "inbox" / ".seen-2026-01-03-letter-of-return.md").exists())
        self.assertNotEqual(self.fx.git(home, "rev-parse", "HEAD"), head)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")

    def test_letter_of_return_first_line_and_silence_bind_as_before(self):
        for slug, reply, choice, marker in (("a", "AMBIENT\nI will keep a slow rhythm.", "ambient", None),
                                            ("b", "", "rest-on", "REST"),
                                            ("c", "A slow morning, perhaps ambient.", "undecided", None),
                                            ("d", "EVENT-ONLY.", "event-only", "EVENT-ONLY"),
                                            ("e", "Ambient, and slowly.", "ambient", None),
                                            ("f", "The garden is quiet.\nI choose EVENT-ONLY.", "event-only", "EVENT-ONLY"),
                                            ("g", "g: REST-ON", "rest-on", "REST"),
                                            ("h", "I choose not to be ambient.", "undecided", None)):
            with self.subTest(reply=reply):
                home = self.fx.make_home(slug, letters=[("2026-01-03-letter-of-return.md", "LETTER-BODY-3\n")])
                self.brain.reply = reply
                r = self.fx.run(self.REFLECT, slug, env=self.env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"- choice: {choice}", (home / "CADENCE.md").read_text())
                for m in ("REST", "EVENT-ONLY"):
                    self.assertEqual((home / m).exists(), m == marker, (slug, m))


class TestMacBashParses(unittest.TestCase):
    """The Studio and the Air may run these under /bin/bash 3.2, which fails
    to parse some constructs bash 5 accepts (a lone quote in a heredoc inside
    $(...)). The fixtures run whichever bash is first on PATH, so pin 3.2 here."""

    SCRIPTS = ("citizen-beat.sh", "citizen-fleet.sh", "citizen-gates.sh",
               "citizen-parade.sh", "citizen-reflect.sh", "fleet-status.sh")

    def setUp(self):
        if not os.path.exists("/bin/bash"):
            self.skipTest("no /bin/bash")
        major = subprocess.run(["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"],
                               capture_output=True, text=True, check=False).stdout.strip()
        if major != "3":
            self.skipTest(f"/bin/bash is {major}.x, not the macOS 3.2 parser")

    def test_each_script_parses(self):
        for name in self.SCRIPTS:
            with self.subTest(script=name):
                r = subprocess.run(["/bin/bash", "-n", str(TOOLS / name)],
                                   capture_output=True, text=True, check=False)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
