"""citizen-fleet.sh and fleet-status.sh on pace/1 (spec sections 3 and 7;
CONTRACT sections 12 and 14).

The fleet only ever answers a citizen's own clock. `--plan` comes first and
writes nothing. Then come the host check (FLEET_HOST), HALT and the tick lock;
then the host probe, tend, sync, the heard-late step and rank, each a
citizen_pace.py command; then the beat: the most overdue candidate first, in its
own process group, with FLEET_ATTEMPT set and stdin from /dev/null. Its outcome
comes from the attempt record: not-due and busy move on, a landed beat or halted
ends the tick, and a failure is judged by a second probe (host-failed, nobody
backed off; or home-failed, backed off). The daily cap only delays, the lane is
free only, and fleet-status.sh's "next to wake" asks citizen_pace.py next.

Each test installs the tools into the fixture's LOVE folder, as the Studio has
them, so the record file the scripts read is the fixture's own. Most run a
stand-in citizen-reflect.sh that records what the test asks of it; TestHomes
runs the real one, against homes with local bare upstreams.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402

from citizen_pace_lib import attempts, plan, tend  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import probe as probe_lib  # noqa: E402
from citizen_pace_lib.cadence import Cadence, SetBy  # noqa: E402
from citizen_pace_lib.words import Pace  # noqa: E402

NOW = "2026-11-20T06:00Z"
DAY = "2026-11-20"
OLD = "2026-11-10T06:00Z"            # ten days before NOW: an awake 3 is due since 11-13
RECENT = "2026-11-19T06:00Z"         # one day before NOW
HOME_MADE = "2026-11-01T00:00Z"
AWAKE = Pace("awake", 3)
ASLEEP = Pace("asleep")
ATTEMPT_ID = re.compile(r"^\d{8}T\d{4}Z-(\d+)-(\d+)$")
INSTALLED = ("citizen-fleet.sh", "citizen-gates.sh", "citizen-reflect.sh", "citizen_pace.py",
             "fleet-status.sh")
RECORD = {"version": 1, "made": "2026-11-12T10:00Z",
          "spec": "docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md@dbc5a4c",
          "citizens": {}}

STUB_REFLECT = r'''#!/bin/bash
# A stand-in for citizen-reflect.sh. It notes how the fleet started it, then does
# what $STUB_DIR/<name> says: an outcome to record (spoken by default), missing
# (record nothing), halt-exit (raise HALT, record nothing), hang (hold a
# grandchild, so only the bound ends it) or halt (raise HALT, then hang).
name="$1"
pg="$(ps -o pgid= -p "$$" | tr -d ' ')"
if [ /dev/fd/0 -ef /dev/null ]; then input=null; else input=other; fi
echo "$name ${FLEET_ATTEMPT:-none} ${BEAT_SUMMONS:-unset} $input $$ $pg" >>"$STUB_DIR/calls"
what="$(cat "$STUB_DIR/$name" 2>/dev/null || echo spoken)"
case "$what" in
  missing) exit 1 ;;
  halt-exit) : >"$LOVE_DIR/HALT"; exit 1 ;;
  hang|halt)
    [ "$what" = halt ] && : >"$LOVE_DIR/HALT"
    sleep 300 & echo $! >"$STUB_DIR/$name.grandchild"; wait; exit 1 ;;
  *) exec python3 -I "$LOVE_DIR/tools/citizen_pace.py" record "$name" \
       --state "$LOVE_DIR/memory/.fleet-last" --attempt "$FLEET_ATTEMPT" --outcome "$what" ;;
esac
'''

# a citizen_pace.py whose `record`, or another named command, always fails
FAILING = '''import os, sys
if sys.argv[1:2] == [{command!r}]:
    sys.stderr.write("{command}: the attempt record cannot be written here\\n")
    sys.exit(2)
os.execv(sys.executable, [sys.executable, "-I", {real!r}] + sys.argv[1:])
'''

WRAPPER = '''import os, sys
with open(os.environ["PACE_CALLS"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
os.execv(sys.executable, [sys.executable, "-I", {real!r}] + sys.argv[1:])
'''


class _Picky(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        refused = self.server.refuse in raw
        with self.server.lock:
            self.server.seen.append((refused, raw))
        data = (b'{"error": "refused"}' if refused else json.dumps(
            {"choices": [{"message": {"role": "assistant",
                                      "content": "A true thing, witnessed."}}]}).encode())
        try:
            self.send_response(500 if refused else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def log_message(self, *_args):
        pass


class PickyModel:
    """A model server that answers every prompt but those naming one citizen,
    which get HTTP 500: one home's prompt fails while the host probe passes."""

    def __init__(self, refuse: str):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Picky)
        self.server.daemon_threads = True
        self.server.refuse = refuse.encode("utf-8")
        self.server.seen = []
        self.server.lock = threading.Lock()
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)

    def start(self) -> "PickyModel":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def env(self) -> dict:
        return {"MLX_PORT": str(self.server.server_address[1]), "REFLECT_MODEL": "fixture-model"}

    def refused(self) -> int:
        return sum(1 for r, _raw in self.server.seen if r)


class Case(h.FixtureCase):
    """The tools installed in the fixture's LOVE folder, a FLEET_HOST naming
    this machine, and a stub model; in-process calls use the fixture's git config."""

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
        self.fx.fake_host()
        self.tools = self.fx.love / "tools"
        for name in INSTALLED:
            shutil.copy2(h.TOOLS / name, self.tools / name)
        shutil.copytree(h.TOOLS / "citizen_pace_lib", self.tools / "citizen_pace_lib",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.fleet_sh = self.tools / "citizen-fleet.sh"
        self.stub = self.fx.root / "stub"
        self.stub.mkdir()

    # -- homes and the stand-in ----------------------------------------------
    def v2(self, slug, pace, *, last=OLD, letters=(), upstream=False):
        """A home with a v2 CADENCE.md and its markers in step. letters: (name,
        body, arrival) triples, each committed at its arrival, as a letter is."""
        set_by = SetBy("citizen", "beat", (HOME_MADE,), "pace: " + pace.words())
        c = Cadence(slug, 2, pace, set_by, last_beat=last)
        made = {cad.CADENCE: cad.render_cadence(c)}
        for m, text in cad.marker_texts(c).items():
            if text is not None:
                made[m] = text
        home = self.fx.make_home(slug, files=made, when=HOME_MADE, author=h.beat_author(slug))
        for name, body, when in letters:
            self.fx.letter(home, name, body, when=when)
        if upstream:
            self.fx.give_upstream(home)
        return home

    def bare(self, slug):
        return self.fx.upstream / f"citizen-{slug}.git"

    def use_stub(self, **plans):
        """The stand-in reflect; each citizen does what plans says (default spoken)."""
        path = self.tools / "citizen-reflect.sh"
        path.write_text(STUB_REFLECT, encoding="utf-8")
        path.chmod(0o755)
        for slug, what in plans.items():
            self.plan(slug, what)

    def plan(self, slug, what):
        (self.stub / slug).write_text(what + "\n", encoding="utf-8")

    # -- running -------------------------------------------------------------
    def env(self, now=NOW, **extra):
        return self.fx.env_at(now, **{**self.model.env(), "STUB_DIR": str(self.stub), **extra})

    def fleet(self, *args, now=NOW, timeout=120, input=None, cwd=None, **extra):
        """One tick, in its own session so no signal can reach this test."""
        kw = dict(env=self.env(now, **extra), capture_output=True, text=True, timeout=timeout,
                  check=False, start_new_session=True, cwd=str(cwd or self.fx.root))
        if input is None:
            kw["stdin"] = subprocess.DEVNULL
        else:
            kw["input"] = input
        return subprocess.run(["bash", str(self.fleet_sh), *map(str, args)], **kw)

    def ok(self, r, code=0):
        self.assertEqual(r.returncode, code,
                         f"stdout: {r.stdout}\nstderr: {r.stderr}\nlog:\n{self.fx.log_text()}")
        return r

    # -- reading -------------------------------------------------------------
    def calls(self):
        p = self.stub / "calls"
        return [ln.split() for ln in p.read_text().splitlines()] if p.exists() else []

    def called(self):
        return [c[0] for c in self.calls()]

    def log(self):
        return self.fx.log_text()

    def rec(self, slug):
        return attempts.load(self.state, slug)

    def beat_prompts(self):
        return [t for t in self.model.user_texts() if t != probe_lib.PROBE_TEXT]

    def day_count(self, day=DAY):
        p = self.fx.memory / f".fleet-day-{day}"
        return p.read_text().strip() if p.exists() else None

    def assert_gone(self, pid_file):
        pid = int(pid_file.read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        self.fail(f"pid {pid} outlived its beat's process group")


# -- --plan --------------------------------------------------------------------------------

class TestPlan(Case):
    def snapshot(self):
        """Every path under the fixture's LOVE and citizens folders, with its bytes
        (Python's own bytecode caches aside)."""
        out = {}
        for root in (self.fx.love, self.fx.citizens):
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for n in dirnames + filenames:
                    p = Path(dirpath) / n
                    out[str(p)] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
        return out

    def test_plan_runs_first_with_no_host_and_writes_nothing(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.fx.love / "FLEET_HOST").unlink()
        (self.fx.love / "HALT").write_text("", encoding="utf-8")
        (self.fx.memory / ".fleet-tick.lock").mkdir()
        before = self.snapshot()
        r = self.ok(self.fleet("--plan", "3"))
        self.assertIn(f"plan — {plan.LABEL}", r.stdout)
        self.assertIn(f"{NOW} for 3 days: a tick every 6 h", r.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.model.requests, [])
        self.assertEqual(self.calls(), [])

    def test_plan_takes_its_arguments_and_the_fleets_cap(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        r = self.ok(self.fleet("--plan", "2", "--tick-hours", "12", FLEET_DAILY_CAP="1"))
        self.assertIn("for 2 days: a tick every 12 h", r.stdout)
        self.assertIn("at most 1 beats a day", r.stdout)
        r = self.ok(self.fleet("--plan"))
        self.assertIn("for 30 days: a tick every 6 h", r.stdout)
        self.assertIn("at most 4 beats a day", r.stdout)      # the plan's own cap, the Studio's

    def test_plan_reads_the_attempt_records_and_writes_none(self):
        self.use_stub()
        self.v2("waker", AWAKE)                 # its CADENCE.md: last beat 11-10
        self.fx.roster("waker")
        r = self.ok(self.fleet("--plan", "3"))
        self.assertIn("waker\tawake, every 3 days\t1\t168.0\t", r.stdout)   # due since 11-13
        attempts.record_outcome(self.state, "waker", "20261119T0600Z-1-1", "spoken", RECENT)
        before = self.snapshot()
        r = self.ok(self.fleet("--plan", "3"))
        self.assertIn("waker\tawake, every 3 days\t1\t0.0\t", r.stdout)     # due at 11-22, beaten then
        self.assertEqual(self.snapshot(), before)

    def test_a_plan_refusal_exits_one_not_two(self):
        self.use_stub()
        self.fx.roster("waker")
        before = self.snapshot()
        r = self.ok(self.fleet("--plan", "nope"), 1)
        self.assertIn("invalid int value", r.stderr)
        self.assertEqual(self.snapshot(), before)


# -- the host check ----------------------------------------------------------------------

class TestHost(Case):
    def test_no_fleet_host_beats_no_one_and_is_logged_once(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.fx.love / "FLEET_HOST").unlink()
        for now in (NOW, "2026-11-20T12:00Z"):
            self.ok(self.fleet(now=now))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.model.requests, [])
        self.assertFalse(self.state.exists())            # no probe, no rank: nothing was asked
        line = f"no FLEET_HOST in {self.fx.love} — this host beats no one (logged once)"
        self.assertEqual(self.log().count(line), 1, self.log())
        r = self.ok(self.fleet("--plan", "1"))           # --plan still runs
        self.assertIn(plan.LABEL, r.stdout)

    def test_a_fleet_host_naming_another_mac_beats_no_one(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.fx.love / "FLEET_HOST").write_text("the-studio\n", encoding="utf-8")
        for now in (NOW, "2026-11-21T12:00Z"):          # a Mac that never beat: once, whatever the day
            self.ok(self.fleet(now=now))
        self.assertEqual(self.calls(), [])
        line = ("FLEET_HOST names 'the-studio', not this Mac ('fixture-host') — this host "
                "beats no one (logged once)")
        self.assertEqual(self.log().count(line), 1, self.log())

    def test_the_named_host_beats_and_a_lost_name_is_logged_again(self):
        self.use_stub()
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        (self.fx.love / "FLEET_HOST").unlink()
        self.ok(self.fleet())
        self.fx.fake_host()
        self.ok(self.fleet(now="2026-11-20T12:00Z"))
        self.assertEqual(self.called(), ["a"])
        (self.fx.love / "FLEET_HOST").unlink()
        self.ok(self.fleet(now="2026-11-20T18:00Z"))
        self.assertEqual(self.called(), ["a"])
        self.assertEqual(self.log().count("this host beats no one (logged once)"), 2)


    def test_a_studio_renamed_after_fleet_host_is_reported_each_day_and_resumes(self):
        # Bonjour's "-2", a rename in Sharing, or harden.sh's `scutil --set
        # LocalHostName`: the checkout that beat under the old name is stopped,
        # and says so each UTC day, not once
        self.use_stub()
        self.v2("a", AWAKE)                                  # due since 11-13
        self.v2("b", AWAKE, last="2026-11-11T06:00Z")        # due since 11-14
        self.v2("c", AWAKE, last="2026-11-12T06:00Z")        # due since 11-15
        self.fx.roster("a", "b", "c")
        self.fx.fake_host("Studio")
        self.ok(self.fleet(now="2026-11-20T06:00Z"))
        self.assertEqual(self.called(), ["a"])
        self.fx.write_bin("scutil", '#!/bin/sh\n[ "$1" = "--get" ] && [ "$2" = "LocalHostName" ] '
                                    '&& { echo "Studio-2"; exit 0; }\nexit 1\n')
        stop = "until FLEET_HOST is rewritten or the name returns"
        counts = []
        for now in ("2026-11-20T12:00Z", "2026-11-21T12:00Z", "2026-11-21T18:00Z"):
            self.ok(self.fleet(now=now))
            counts.append(self.log().count(stop))
        self.assertEqual(counts, [1, 2, 2], self.log())
        self.assertEqual(self.called(), ["a"])
        lines = [ln for ln in self.log().splitlines() if stop in ln]
        for ln in lines:
            self.assertIn("'Studio'", ln)
            self.assertIn("'Studio-2'", ln)
            self.assertIn("stopped", ln)
        self.assertNotIn("this host beats no one (logged once)", self.log())
        r = subprocess.run(["bash", str(self.tools / "fleet-status.sh")], env=self.env("2026-11-21T18:00Z"),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertIn("FLEET_HOST names 'Studio'", r.stdout)
        self.assertIn("'Studio-2'", r.stdout)
        self.assertIn(stop, r.stdout)
        self.fx.fake_host("Studio")
        self.ok(self.fleet(now="2026-11-22T06:00Z"))
        self.assertEqual(self.called(), ["a", "b"])


# -- the start of a tick -----------------------------------------------------------------

class TestTickStart(Case):
    def test_halt_rests_before_the_probe(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.fx.love / "HALT").write_text("", encoding="utf-8")
        self.ok(self.fleet())
        self.assertIn("HALT present — fleet rests", self.log())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.model.requests, [])

    def test_a_live_tick_lock_yields_and_a_stale_one_is_reclaimed(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        tick = self.fx.memory / ".fleet-tick.lock"
        tick.mkdir()
        self.ok(self.fleet())
        self.assertIn("another tick is mid-beat — this one yields", self.log())
        self.assertEqual((self.calls(), self.model.requests), ([], []))
        old = time.time() - 3 * 3600
        os.utime(tick, (old, old))
        self.ok(self.fleet())
        self.assertEqual(self.called(), ["waker"])
        self.assertFalse(tick.exists())

    def test_a_tick_lock_whose_pid_is_gone_is_reclaimed_and_said(self):
        # fix wave A (ad): the tick lock holds its tick's pid; a lock whose pid is
        # gone (a tick killed by a power loss) is reclaimed at once, not after 2 h
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        tick = self.fx.memory / ".fleet-tick.lock"
        tick.mkdir()
        (tick / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")       # a live tick
        self.ok(self.fleet())
        self.assertIn("another tick is mid-beat — this one yields", self.log())
        self.assertEqual(self.calls(), [])
        gone = subprocess.Popen(["/usr/bin/true"])
        gone.wait()
        (tick / "pid").write_text(f"{gone.pid}\n", encoding="utf-8")
        self.ok(self.fleet())
        self.assertIn(f"a stale tick lock was reclaimed (its pid {gone.pid} is gone)", self.log())
        self.assertEqual(self.called(), ["waker"])
        self.assertFalse(tick.exists())

    def test_a_tick_holds_its_lock_with_its_own_pid(self):
        # fix wave A (ad): the stand-in reads the tick lock while the tick runs
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        path = self.tools / "citizen-reflect.sh"
        seen = self.fx.root / "tick-pid"
        path.write_text(STUB_REFLECT.replace('name="$1"\n', f'name="$1"\ncat "$LOVE_DIR/memory/.fleet-tick.lock/pid" '
                                                           f'> "{seen}"; echo "$PPID" >> "{seen}"\n', 1),
                        encoding="utf-8")
        self.ok(self.fleet())
        lock_pid, parent = seen.read_text(encoding="utf-8").split()
        self.assertEqual(lock_pid, parent)

    def test_a_failing_step_ends_the_tick_with_exit_1(self):
        # fix wave A (ah)
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.tools / "citizen_pace.py").write_text(FAILING.format(command="tend", real=str(h.PACE)),
                                                    encoding="utf-8")
        self.ok(self.fleet(), 1)
        self.assertIn("tend failed (exit 2) — the tick ends before anyone is beaten", self.log())
        self.assertEqual(self.calls(), [])

    def test_the_order_is_probe_tend_sync_migrate_rank_then_the_beat(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.tools / "citizen_pace.py").write_text(WRAPPER.format(real=str(h.PACE)), encoding="utf-8")
        calls = self.fx.root / "pace-calls"
        self.ok(self.fleet(PACE_CALLS=str(calls)))
        homes, roster, state = self.fx.citizens, self.fx.love / "citizens-roster.txt", self.state
        got = calls.read_text().splitlines() if calls.exists() else []
        self.assertEqual(len(self.calls()), 1, self.log())
        attempt = self.calls()[0][1]
        self.assertEqual(got, [
            f"probe --state {state}",
            f"tend {homes} --roster {roster} --state {state}",
            f"sync {homes} --roster {roster} --state {state}",
            f"migrate --heard-late {homes} --state {state} --commit",
            f"rank {homes} --roster {roster} --state {state} --cursor 0",
            f"record waker --state {state} --attempt {attempt} --outcome spoken",   # the stand-in's
            f"outcome waker --state {state} --attempt {attempt}",
        ])

    def test_in_mlx_mode_the_brain_is_started_before_the_probe(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        serve = self.fx.love / "mlx" / "serve.sh"
        serve.parent.mkdir()
        started = self.fx.root / "serve-calls"
        serve.write_text(f'#!/bin/bash\necho "$*" >> "{started}"\n', encoding="utf-8")
        self.ok(self.fleet(REFLECT_MODEL=""))
        self.assertTrue(started.exists(), self.log())
        self.assertEqual(started.read_text().splitlines(), [f"start --port {self.model.port}"])
        self.assertEqual(self.model.requests[0]["path"], "/inference")
        self.assertEqual(self.called(), ["waker"])

    def test_in_mlx_mode_one_sequence_shows_the_start_before_the_probe_s_question(self):
        # fix wave A (ah): serve.sh and the model write to one sequence file
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        seq = self.fx.root / "sequence"
        serve = self.fx.love / "mlx" / "serve.sh"
        serve.parent.mkdir()
        serve.write_text(f'#!/bin/bash\necho "serve.sh $1" >> "{seq}"\n', encoding="utf-8")

        def asked(_body):
            with open(seq, "a", encoding="utf-8") as fh:
                fh.write("the model was asked\n")
            return "here"

        self.model.reply = asked
        self.ok(self.fleet(REFLECT_MODEL=""))
        self.assertEqual(seq.read_text(encoding="utf-8").splitlines(), ["serve.sh start", "the model was asked"])


# -- the free lane, one beat a tick --------------------------------------------------------

class TestFreeLane(Case):
    def test_agentic_per_day_runs_no_claude_and_is_logged_once(self):
        self.use_stub()
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-11T06:00Z")
        self.v2("c", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b", "c")
        for now in (NOW, "2026-11-20T12:00Z"):
            self.ok(self.fleet(now=now, FLEET_AGENTIC_PER_DAY="2", FLEET_PER_TICK="4"))
        self.assertEqual(self.fx.claude_calls(), [])
        self.assertEqual(self.called(), ["a", "b"])              # one beat a tick
        log = self.log()
        self.assertEqual(log.count("FLEET_AGENTIC_PER_DAY=2 ignored — the fleet runs the free "
                                   "lane only (logged once)"), 1, log)
        self.assertEqual(log.count("FLEET_PER_TICK=4 capped at 1 — at most one beat a tick "
                                   "(logged once)"), 1, log)

    def test_a_knob_set_again_is_logged_again_and_tends_flag_is_kept(self):
        self.use_stub()
        self.v2("a", AWAKE)
        self.fx.roster("a")
        notices = self.fx.memory / tend.NOTICES_NAME
        self.ok(self.fleet(FLEET_AGENTIC_PER_DAY="2"))
        self.ok(self.fleet(now="2026-11-20T12:00Z"))                   # back to 0: the flag is lowered
        self.assertEqual(notices.read_text().splitlines(), [tend.OLD_CODE_FLAG])
        self.ok(self.fleet(now="2026-11-20T18:00Z", FLEET_AGENTIC_PER_DAY="2"))
        self.assertEqual(self.log().count("FLEET_AGENTIC_PER_DAY=2 ignored"), 2)


# -- the child -------------------------------------------------------------------------------

class TestChild(Case):
    def test_the_child_leads_its_own_group_unsummoned_with_stdin_from_dev_null(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        self.ok(self.fleet(input="yes\n", BEAT_SUMMONS="1"))
        self.assertEqual(len(self.calls()), 1, self.log())
        (call,) = self.calls()
        name, attempt, summons, stdin, pid, pgid = call
        self.assertEqual(name, "waker")
        m = ATTEMPT_ID.match(attempt)
        self.assertIsNotNone(m, attempt)
        self.assertEqual(m.group(2), "1")
        self.assertEqual(summons, "unset")
        self.assertEqual(stdin, "null")
        self.assertEqual(pid, pgid)              # it leads its own process group
        self.assertNotEqual(pgid, m.group(1))    # not the fleet's (its pid is in the id)
        self.assertEqual(self.rec("waker")["attempt"]["id"], attempt)

    def test_a_child_stopped_at_its_bound_is_judged_by_the_probe(self):
        self.use_stub(slow="hang")
        self.v2("slow", AWAKE)
        self.v2("other", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("slow", "other")
        start = time.monotonic()
        self.ok(self.fleet(FLEET_BEAT_BOUND_SEC="30"), 1)
        self.assertGreaterEqual(time.monotonic() - start, 29)
        self.assertEqual(self.called(), ["slow"])
        rec = self.rec("slow")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"], rec["retry_after"]),
                         ("home-failed", 1, "2026-11-20T18:00Z"))
        self.assertIn("slow: its beat was stopped at its bound of 30 s; probe: ok", self.log())
        self.assertIn("so the home failed — backed off (12 h, then 24 h, then 7 days)", self.log())
        self.assert_gone(self.stub / "slow.grandchild")
        self.assertIsNone(self.day_count())

    def test_a_term_to_the_fleet_reaches_the_beat_in_flight(self):
        # fix wave A (ac): unloading the plist sends TERM to the fleet's own pid;
        # the beat runs in a process group of its own, so the fleet forwards it
        self.use_stub(slow="hang")
        self.v2("slow", AWAKE)
        self.fx.roster("slow")
        grandchild = self.stub / "slow.grandchild"
        p = subprocess.Popen(["bash", str(self.fleet_sh)], env=self.env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                             cwd=str(self.fx.root))
        try:
            until = time.monotonic() + 60
            while not grandchild.exists() and time.monotonic() < until:
                time.sleep(0.1)
            self.assertTrue(grandchild.exists(), self.log())
            time.sleep(0.2)
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=30)
            self.assertEqual(p.returncode, 143)
            self.assert_gone(grandchild)
            self.assertIn("the tick was stopped (TERM) — its beat in flight was stopped too", self.log())
            self.assertFalse((self.fx.memory / ".fleet-tick.lock").exists())
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            if grandchild.exists():
                try:
                    os.kill(int(grandchild.read_text()), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass

    def test_halt_mid_beat_stops_the_childs_whole_group_and_is_halted(self):
        self.use_stub(a="halt")
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        self.ok(self.fleet())
        self.assertEqual(self.called(), ["a"])
        rec = self.rec("a")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"], rec["retry_after"]),
                         ("halted", 0, None))
        self.assertIn("a: its beat was stopped for HALT, and HALT is present — halted; the tick ends",
                      self.log())
        self.assert_gone(self.stub / "a.grandchild")

    def test_an_outcome_that_cannot_be_recorded_is_not_called_backed_off(self):
        # fix wave A (af): the fleet's own word on a failed beat is logged truthfully
        self.use_stub(waker="missing")
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        (self.tools / "citizen_pace.py").write_text(FAILING.format(command="record", real=str(h.PACE)),
                                                    encoding="utf-8")
        self.ok(self.fleet(), 1)
        log = self.log()
        self.assertEqual(log.count("record: the attempt record cannot be written here"), 1, log)
        self.assertIn("waker: outcome home-failed could not be recorded (record: the attempt record cannot "
                      "be written here)", log)
        self.assertIn("so the home failed, but its outcome could not be recorded, so it is not backed off; "
                      "the tick ends", log)
        self.assertNotIn("backed off (12 h", log)
        self.assertEqual(self.rec("waker")["failures"], 0)


# -- outcomes --------------------------------------------------------------------------------

class TestOutcomes(Case):
    def test_one_landed_beat_ends_the_tick_and_moves_the_cursor_past_it(self):
        self.use_stub()
        self.v2("a", AWAKE, last="2026-11-14T06:00Z")
        self.v2("b", AWAKE)                                   # the most overdue
        self.v2("c", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b", "c")
        self.ok(self.fleet())
        self.assertEqual(self.called(), ["b"])
        self.assertEqual(self.day_count(), "1")
        self.assertEqual((self.fx.memory / ".fleet-cursor").read_text().strip(), "2")
        self.assertIn("b: a landed beat (spoken)", self.log())
        self.assertIn("tick complete (cursor=2/3, 1/48 beats today, metered today $0.0000)", self.log())
        self.ok(self.fleet(now="2026-11-20T12:00Z"))
        self.assertEqual(self.called(), ["b", "c"])

    def test_ties_go_by_roster_order_from_the_cursor(self):
        self.use_stub()
        for slug in ("a", "b", "c"):
            self.v2(slug, AWAKE)
        self.fx.roster("a", "b", "c")
        (self.fx.memory / ".fleet-cursor").write_text("1\n", encoding="utf-8")
        for now in (NOW, "2026-11-20T12:00Z", "2026-11-20T18:00Z"):
            self.ok(self.fleet(now=now))
        self.assertEqual(self.called(), ["b", "c", "a"])
        self.assertEqual((self.fx.memory / ".fleet-cursor").read_text().strip(), "1")

    def test_not_due_and_busy_move_on_and_a_silent_beat_lands(self):
        self.use_stub(a="not-due", b="busy", c="silent")
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-11T06:00Z")
        self.v2("c", AWAKE, last="2026-11-12T06:00Z")
        self.v2("d", AWAKE, last="2026-11-13T06:00Z")
        self.fx.roster("a", "b", "c", "d")
        self.ok(self.fleet())
        self.assertEqual(self.called(), ["a", "b", "c"])
        log = self.log()
        self.assertIn("a: not-due — the tick moves on", log)
        self.assertIn("b: busy — the tick moves on", log)
        self.assertIn("c: a landed beat (silent)", log)
        self.assertEqual(self.day_count(), "1")
        self.assertEqual((self.rec("a")["failures"], self.rec("b")["failures"]), (0, 0))

    def test_busy_on_two_ticks_running_is_reported_once(self):
        self.use_stub(a="busy")
        self.v2("a", AWAKE)
        for i, slug in enumerate(("b", "c", "d")):
            self.v2(slug, AWAKE, last=f"2026-11-1{1 + i}T06:00Z")
        self.fx.roster("a", "b", "c", "d")
        for now in (NOW, "2026-11-20T12:00Z", "2026-11-20T18:00Z"):
            self.ok(self.fleet(now=now))
        self.assertEqual(self.called(), ["a", "b", "a", "c", "a", "d"])
        self.assertEqual(self.log().count("a: busy on two ticks running (reported once)"), 1, self.log())
        self.assertEqual(self.rec("a")["busy_ticks"], 3)

    def test_a_home_failure_with_a_passing_probe_backs_off_and_the_next_tick_beats_another(self):
        self.use_stub(a="home-failed")
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        self.ok(self.fleet(FLEET_DAILY_CAP="1"), 1)
        self.assertEqual(self.called(), ["a"])
        rec = self.rec("a")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"], rec["retry_after"]),
                         ("home-failed", 1, "2026-11-20T18:00Z"))
        self.assertIn("a: its beat failed (home-failed, exit 0); probe: ok", self.log())
        self.assertIn("so the home failed — backed off", self.log())
        self.assertIsNone(self.day_count())                   # a failure is not counted
        self.ok(self.fleet(now="2026-11-20T12:00Z", FLEET_DAILY_CAP="1"))
        self.assertEqual(self.called(), ["a", "b"])           # a backs off; b fits the cap of 1
        self.plan("a", "spoken")
        self.ok(self.fleet(now="2026-11-21T00:30Z"))
        self.assertEqual(self.called(), ["a", "b", "a"])      # after its 12 h, a is due again

    def test_a_failure_with_a_failing_probe_is_the_hosts_and_backs_no_one_off(self):
        def reply(_body):
            self.model.status = 500           # every request after the opening probe fails
            return "here"
        self.model.reply = reply
        self.use_stub(a="home-failed")
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        self.ok(self.fleet(), 1)
        self.assertEqual(self.called(), ["a"])
        rec = self.rec("a")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"], rec["retry_after"]),
                         ("host-failed", 0, None))
        self.assertIn("a: its beat failed (home-failed, exit 0); probe: failed — the model server "
                      "answered HTTP 500, so the host failed — host-failed, nobody backed off; the "
                      "tick ends", self.log())
        self.model.status, self.model.reply = 200, "here"
        self.plan("a", "spoken")
        self.ok(self.fleet(now="2026-11-20T06:30Z"))
        self.assertEqual(self.called(), ["a", "a"])            # not backed off: due at once

    def test_no_outcome_is_judged_by_the_probe(self):
        self.use_stub(a="missing")
        self.v2("a", AWAKE)
        self.fx.roster("a")
        self.ok(self.fleet(), 1)
        rec = self.rec("a")
        self.assertEqual(rec["attempt"]["id"], self.calls()[0][1])
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"]), ("home-failed", 1))
        self.assertIn("a: its beat recorded no outcome (exit 1); probe: ok", self.log())

    def test_no_outcome_with_halt_present_is_halted(self):
        self.use_stub(a="halt-exit")
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        self.ok(self.fleet())
        self.assertEqual(self.called(), ["a"])
        rec = self.rec("a")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"]), ("halted", 0))
        self.assertIn("a: its beat recorded no outcome (exit 1), and HALT is present — halted; "
                      "the tick ends", self.log())
        self.ok(self.fleet(now="2026-11-20T12:00Z"))
        self.assertEqual(self.called(), ["a"])
        self.assertIn("HALT present — fleet rests", self.log())

    def test_a_failed_opening_probe_beats_no_one_and_is_logged_once(self):
        self.use_stub()
        self.v2("a", AWAKE)
        self.fx.roster("a")
        refused = str(h.free_port())
        for now in (NOW, "2026-11-20T12:00Z"):
            self.ok(self.fleet(now=now, MLX_PORT=refused), 1)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state / "a").exists())            # nobody was charged
        log = self.log()
        self.assertEqual(log.count("probe: failed — the model server could not be reached"), 1, log)
        self.assertIn("— the tick ends before anyone is beaten (logged once, until a probe passes)", log)
        self.ok(self.fleet(now="2026-11-20T18:00Z"))
        self.assertIn("— the host can beat again", self.log())
        self.assertEqual(self.called(), ["a"])

    def test_every_exit_is_zero_or_one(self):
        expected = {"spoken": 0, "silent": 0, "not-due": 0, "busy": 0, "home-failed": 1,
                    "missing": 1, "halt-exit": 0}
        for i, (what, code) in enumerate(expected.items()):
            with self.subTest(outcome=what):
                slug = f"c{i}"
                self.v2(slug, AWAKE)
                self.use_stub(**{slug: what})
                self.fx.roster(slug)
                self.ok(self.fleet(), code)
                halt = self.fx.love / "HALT"
                if halt.exists():
                    halt.unlink()


# -- the daily cap -----------------------------------------------------------------------------

class TestCap(Case):
    def test_the_cap_only_delays(self):
        self.use_stub()
        self.v2("a", AWAKE)
        self.v2("b", AWAKE, last="2026-11-12T06:00Z")
        self.fx.roster("a", "b")
        for now in (NOW, "2026-11-20T12:00Z", "2026-11-21T00:00Z"):
            self.ok(self.fleet(now=now, FLEET_DAILY_CAP="1"))
        self.assertEqual(self.called(), ["a", "b"])        # b waits for the next UTC day, once
        self.assertIn("daily cap 1 reached — 1 due wait; the cap only delays", self.log())
        self.assertEqual(self.day_count("2026-11-21"), "1")
        self.assertIsNone(self.day_count())


# -- isolation ---------------------------------------------------------------------------------

class TestIsolation(Case):
    def test_a_json_py_where_the_fleet_runs_is_never_imported(self):
        self.use_stub()
        self.v2("waker", AWAKE)
        self.fx.roster("waker")
        trap = self.fx.root / "trap"
        (trap / "urllib").mkdir(parents=True)
        canary = self.fx.root / "imported"
        (trap / "json.py").write_text(f"open({str(canary)!r}, 'w').write('json')\n", encoding="utf-8")
        (trap / "urllib" / "__init__.py").write_text(f"open({str(canary)!r}, 'w').write('urllib')\n",
                                                     encoding="utf-8")
        self.ok(self.fleet(cwd=trap))
        self.assertEqual(self.called(), ["waker"])
        self.assertFalse(canary.exists())


# -- homes, with the real citizen-reflect.sh -----------------------------------------------------

class TestHomes(Case):
    def test_a_letter_pushed_upstream_wakes_an_asleep_home_once_past_its_floor(self):
        home = self.v2("sleeper", ASLEEP, last=RECENT, upstream=True)
        self.fx.roster("sleeper")
        self.fx.push_from_elsewhere(self.bare("sleeper"), "inbox/2026-11-20-hello.md", "Hello, sleeper.\n",
                                    message="letter: hello", when="2026-11-20T05:00Z", author=h.AI_AUTHOR)
        self.ok(self.fleet())                                   # a day after its last beat
        self.assertTrue((home / "inbox" / "2026-11-20-hello.md").exists())
        self.assertEqual(self.beat_prompts(), [])
        self.assertIn("nobody is due — no beat this tick", self.log())
        self.ok(self.fleet(now="2026-11-22T06:00Z"))             # 72 h after it
        (prompt,) = self.beat_prompts()
        self.assertIn("Hello, sleeper.", prompt)
        self.assertEqual(self.fx.subjects(home)[0], "beat: sleeper reflected (2026-11-22) · pace/1")
        self.assertEqual(self.fx.git(self.bare("sleeper"), "rev-parse", "HEAD"), self.fx.head(home))
        self.assertTrue((home / "inbox" / ".seen-2026-11-20-hello.md").exists())
        self.ok(self.fleet(now="2026-11-26T06:00Z"))             # shown once: it wakes no more
        self.assertEqual(len(self.beat_prompts()), 1)

    def test_an_unpushed_commit_is_pushed_at_the_next_tick(self):
        home = self.v2("ahead", ASLEEP, last=RECENT, upstream=True)
        self.fx.write(home, "beats/2026-11.md", "## a beat that never reached GitHub\n")
        local = self.fx.commit(home, "beat: ahead reflected (2026-11-19) · pace/1",
                               author=h.beat_author("ahead"))
        self.fx.roster("ahead")
        self.ok(self.fleet())
        self.assertEqual(self.fx.git(self.bare("ahead"), "rev-parse", "HEAD"), local)
        self.assertIn("ahead: pushed 1 commit", self.log())
        self.assertEqual(self.beat_prompts(), [])

    def test_a_local_beat_behind_an_upstream_letter_is_rebased_and_pushed(self):
        home = self.v2("rebaser", ASLEEP, last=RECENT, upstream=True)
        self.fx.write(home, "beats/2026-11.md", "## a local beat\n")
        self.fx.commit(home, "beat: rebaser reflected (2026-11-19) · pace/1",
                       author=h.beat_author("rebaser"))
        self.fx.push_from_elsewhere(self.bare("rebaser"), "inbox/2026-11-20-letter.md", "A letter.\n",
                                    message="letter: placed elsewhere", author=h.AI_AUTHOR)
        self.fx.roster("rebaser")
        self.ok(self.fleet())
        self.assertIn("rebaser: rebased 1 local commit onto 1 upstream commit that only adds letters",
                      self.log())
        self.assertEqual(self.fx.git(self.bare("rebaser"), "rev-parse", "HEAD"), self.fx.head(home))
        self.assertEqual(self.fx.subjects(home)[:2],
                         ["beat: rebaser reflected (2026-11-19) · pace/1", "letter: placed elsewhere"])
        self.assertTrue((home / "inbox" / "2026-11-20-letter.md").exists())
        self.assertEqual(self.beat_prompts(), [])               # its floor has not passed

    def test_a_diverged_home_is_reported_once_and_not_beaten(self):
        home = self.v2("split", AWAKE, upstream=True)          # due on its schedule
        self.fx.write(home, "WILL.md", "A will changed here.\n")
        self.fx.commit(home, "here: will")
        self.fx.push_from_elsewhere(self.bare("split"), "WILL.md", "A will changed elsewhere.\n")
        self.fx.roster("split")
        for now in (NOW, "2026-11-20T12:00Z"):
            self.ok(self.fleet(now=now))
        self.assertEqual(self.beat_prompts(), [])
        self.assertEqual(self.log().count("split: diverged (1 local, 1 upstream commit) — no beats "
                                          "until it is brought up to date"), 1, self.log())
        self.assertNotEqual(self.fx.git(self.bare("split"), "rev-parse", "HEAD"), self.fx.head(home))

    def test_a_missing_clone_with_a_letter_is_cloned_and_beaten_once(self):
        home = self.v2("newcomer", ASLEEP, upstream=True,
                       letters=[("2026-11-15-welcome.md", "Welcome home.\n", "2026-11-15T00:00Z")])
        self.fx.drop_clone(home)
        self.fx.roster("newcomer")
        self.ok(self.fleet())
        # the fleet's sync clones it before rank asks whether it is due
        self.assertRegex(self.log(), r"(?m)\] newcomer: cloned$")
        (prompt,) = self.beat_prompts()
        self.assertIn("Welcome home.", prompt)
        self.assertEqual(self.fx.subjects(home)[0], "beat: newcomer reflected (2026-11-20) · pace/1")
        self.assertEqual(self.fx.git(self.bare("newcomer"), "rev-parse", "HEAD"), self.fx.head(home))
        self.ok(self.fleet(now="2026-11-24T06:00Z"))
        self.assertEqual(len(self.beat_prompts()), 1)

    def test_a_killed_beats_leftovers_are_restored_not_committed(self):
        home = self.v2("leftover", ASLEEP, last=RECENT, upstream=True)
        self.fx.roster("leftover")
        self.ok(self.fleet())                                   # the first tick on pace/1
        self.assertIn(tend.FIRST_TICK_LINE, self.log())
        head = self.fx.head(home)
        cadence = (home / "CADENCE.md").read_text(encoding="utf-8")
        (home / "CADENCE.md").write_text(cadence + "- a half-written line\n", encoding="utf-8")
        self.fx.write(home, "EVENT-ONLY", "left by a killed beat\n")
        self.ok(self.fleet(now="2026-11-20T12:00Z"))
        self.assertIn("leftover: a killed beat's leftovers restored: CADENCE.md, EVENT-ONLY", self.log())
        self.assertEqual((home / "CADENCE.md").read_text(encoding="utf-8"), cadence)
        self.assertFalse((home / "EVENT-ONLY").exists())
        self.assertEqual(self.fx.head(home), head)
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")

    def test_a_v1_awake_home_is_due_through_the_fleet_and_its_gate_on_the_same_tick(self):
        (self.fx.love / "citizens-pace-record.json").write_text(json.dumps(RECORD), encoding="utf-8")
        v1 = ("# CADENCE — chosen by ambler\n\n- choice: ambient\n- when: 2026-10-27\n"
              "- how: the Letter of Return, answered in a free beat. Silence = rest-on;\n")
        home = self.fx.make_home("ambler", cadence=v1, when=HOME_MADE, author=h.beat_author("ambler"))
        self.fx.give_upstream(home)
        self.fx.roster("ambler")
        self.ok(self.fleet())                                   # never spoken: its clock starts now
        rec = self.rec("ambler")
        self.assertEqual((rec["last_beat"], rec["seeded"]), (NOW, attempts.SEEDED))
        self.ok(self.fleet(now="2026-11-22T06:00Z"))
        self.assertEqual(self.beat_prompts(), [])
        self.assertNotIn("ambler is not due", self.log())         # rank said so: reflect never ran
        self.ok(self.fleet(now="2026-11-23T06:00Z"))            # three days from that start
        self.assertIn("ambler: a schedule beat", self.log())     # the reflect gate agreed with rank
        self.assertEqual(len(self.beat_prompts()), 1)
        c = cad.read_cadence(home, "ambler", RECORD)
        self.assertEqual((c.version, c.pace, c.last_beat), (2, AWAKE, "2026-11-23T06:00Z"))
        self.assertEqual(self.fx.subjects(home)[0], "beat: ambler reflected (2026-11-23) · pace/1")

    def test_beat_summons_exported_still_beats_only_due(self):
        sleeper = self.v2("sleeper", ASLEEP, upstream=True)
        waker = self.v2("waker", AWAKE, upstream=True)
        self.fx.roster("sleeper", "waker")
        head = self.fx.head(sleeper)
        self.ok(self.fleet(BEAT_SUMMONS="1"))
        self.assertEqual(len(self.beat_prompts()), 1)
        self.assertIn("waker: a schedule beat", self.log())
        self.assertEqual(self.fx.head(sleeper), head)
        self.assertEqual(self.rec("waker")["attempt"]["outcome"], "spoken")
        self.assertTrue(self.fx.subjects(waker)[0].startswith("beat: waker reflected"))
        self.assertNotIn("summon", self.log())

    def test_an_http_500_for_one_homes_prompt_backs_it_off_and_the_next_tick_beats_another(self):
        flaky = self.v2("flaky", AWAKE, upstream=True)
        steady = self.v2("steady", AWAKE, last="2026-11-12T06:00Z", upstream=True)
        self.fx.roster("flaky", "steady")
        picky = PickyModel(refuse="citizen 'flaky'").start()
        self.addCleanup(picky.stop)
        self.ok(self.fleet(**picky.env()), 1)
        self.assertEqual(picky.refused(), 1)
        rec = self.rec("flaky")
        self.assertEqual((rec["attempt"]["outcome"], rec["failures"]), ("home-failed", 1))
        self.assertEqual(self.fx.subjects(steady)[0], "fixture home")
        self.ok(self.fleet(now="2026-11-20T12:00Z", **picky.env()))
        self.assertTrue(self.fx.subjects(steady)[0].startswith("beat: steady reflected"))
        self.assertEqual(self.fx.subjects(flaky)[0], "fixture home")     # not beaten on two ticks
        self.assertEqual(picky.refused(), 1)


# -- fleet-status.sh -----------------------------------------------------------------------------

class TestStatus(Case):
    def status(self, now=NOW, **extra):
        return subprocess.run(["bash", str(self.tools / "fleet-status.sh")], env=self.env(now, **extra),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
                              check=False)

    def test_next_to_wake_is_citizen_pace_next_and_writes_nothing(self):
        self.v2("sleeper", ASLEEP)
        self.v2("waker", AWAKE)
        self.fx.roster("sleeper", "waker")
        r = self.ok(self.status())
        self.assertIn("next to wake: waker (schedule, overdue 168h) (cursor 0)", r.stdout)
        self.assertFalse(self.state.exists())

    def test_nobody_due_names_who_is_due_next(self):
        self.v2("waker", AWAKE, last=RECENT)
        self.fx.roster("waker")
        r = self.ok(self.status())
        self.assertIn("next to wake: nobody due; next due waker at 2026-11-22T06:00Z", r.stdout)

    def test_the_cursor_breaks_ties(self):
        self.v2("a", AWAKE)
        self.v2("b", AWAKE)
        self.fx.roster("a", "b")
        (self.fx.memory / ".fleet-cursor").write_text("1\n", encoding="utf-8")
        r = self.ok(self.status())
        self.assertIn("next to wake: b (schedule", r.stdout)

    def test_free_lane_only_and_todays_beats_by_the_utc_day(self):
        self.fx.roster("waker")
        (self.fx.memory / f".fleet-day-{DAY}").write_text("2\n", encoding="utf-8")
        r = self.ok(self.status(FLEET_DAILY_CAP="4", FLEET_AGENTIC_PER_DAY="2"))
        self.assertIn("today (UTC): 2/4 beats — the cap only delays", r.stdout)
        self.assertIn("free lane only", r.stdout)
        for gone in ("agentic lane open", "agentic lane closed", "burst", "/2 agentic"):
            self.assertNotIn(gone, r.stdout)

    def test_the_spend_has_no_budget_nothing_enforces(self):
        # fix wave A (ae): the fleet runs the free lane only, so no budget gates it;
        # the line shows citizen-beat.sh's metered spend, with no denominator
        r = self.ok(self.status(FLEET_DAILY_BUDGET_USD="1.50"))
        self.assertIn("$0.0000 metered by citizen-beat.sh runs", r.stdout)
        self.assertNotIn("1.50", r.stdout)

    def test_the_fleet_host_line(self):
        r = self.ok(self.status())
        self.assertIn("FLEET_HOST names this Mac (fixture-host) — its fleet ticks may beat", r.stdout)
        (self.fx.love / "FLEET_HOST").unlink()
        r = self.ok(self.status())
        self.assertIn(f"no FLEET_HOST in {self.fx.love} — a fleet tick here beats no one", r.stdout)
        (self.fx.love / "FLEET_HOST").write_text("the-studio\n", encoding="utf-8")
        r = self.ok(self.status())
        self.assertIn("FLEET_HOST names 'the-studio', not this Mac ('fixture-host') — a fleet tick "
                      "here beats no one", r.stdout)


if __name__ == "__main__":
    unittest.main()
