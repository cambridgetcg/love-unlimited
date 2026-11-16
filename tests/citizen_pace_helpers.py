"""Shared fixtures for the citizens' own pace tests (pace/1).

Every fixture is offline. Homes are local git repos, upstreams are local bare
repos, the model is a loopback stub, and PATH holds only the fakes plus the
system directories, so no real model, push, clone, `gh`, `sol` or `claude`
can be reached. Modelled on PR #24's `Fixture` in test_citizen_live_lane.py.

Later tasks add their own helpers in tests/citizen_pace_helpers_<name>.py and
never edit this file.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PACE = TOOLS / "citizen_pace.py"
GATES = TOOLS / "citizen-gates.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "citizen_pace"
ANSWERS = FIXTURES / "answers.json"
CANARY = "HOST-FILE-CANARY-7f3a"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
FLEET_AUTHOR = "fleet <fleet@kingdom.os>"
AI_AUTHOR = "Ai <ai@kingdom.os>"
UTC = datetime.timezone.utc

if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))


def beat_author(slug: str) -> str:
    return f"{slug} <citizen@kingdom.os>"


_answers_cache: dict = {}


def load_answers() -> dict:
    """The 12 stored answers, {"<slug>-<sha8>": exact text}."""
    if not _answers_cache:
        _answers_cache.update(json.loads(ANSWERS.read_text(encoding="utf-8")))
    return dict(_answers_cache)


def answer(key: str) -> str:
    return load_answers()[key]


def git_date(when) -> str:
    """A stamp ('YYYY-MM-DDTHH:MMZ'), bare date or datetime, as a git date in UTC."""
    if isinstance(when, datetime.datetime):
        d = when if when.tzinfo else when.replace(tzinfo=UTC)
    else:
        t = str(when).strip()
        if len(t) == 10:
            t += "T00:00Z"
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        d = datetime.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


def split_author(author: str) -> tuple:
    """'name <email>' -> (name, email)."""
    name, _, rest = author.partition("<")
    return name.strip(), rest.rstrip(">").strip()


@contextlib.contextmanager
def pace_now(stamp: str):
    """Set PACE_NOW in this process for the duration of the block."""
    old = os.environ.get("PACE_NOW")
    os.environ["PACE_NOW"] = stamp
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PACE_NOW", None)
        else:
            os.environ["PACE_NOW"] = old


def free_port() -> int:
    """A loopback port nothing listens on (for a refused connection)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fixture:
    """A throwaway LOVE_DIR, citizens dir, fake bin, upstreams and git config."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.love = self.root / "love"
        self.memory = self.love / "memory"
        self.state = self.memory / ".fleet-last"
        self.citizens = self.root / "citizens"
        self.upstream = self.root / "upstream"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.outside = self.root / "outside"
        for d in (self.memory, self.love / "tools", self.citizens, self.upstream,
                  self.bin, self.home, self.outside):
            d.mkdir(parents=True, exist_ok=True)
        self.secret = self.outside / "host-secret.txt"
        self.secret.write_text(f"{CANARY}\n", encoding="utf-8")
        self.claude_log = self.root / "claude-calls.log"
        self.gitconfig = self.root / "gitconfig"
        self.gitconfig.write_text(
            "[user]\n\tname = Fixture\n\temail = fixture@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n",
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
            "PATH": f"{self.bin}:{SYSTEM_PATH}",
            "HOME": str(self.home),
            "TZ": "UTC",
            "LOVE_DIR": str(self.love),
            "CITIZENS_DIR": str(self.citizens),
            "PACE_REMOTE_BASE": str(self.upstream),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "FAKE_CLAUDE_LOG": str(self.claude_log),
            "FAKE_CLAUDE_OUT": json.dumps(
                {"type": "result", "result": "a fixture beat", "num_turns": 1,
                 "total_cost_usd": 0.01}
            ),
        }

    # -- environment -----------------------------------------------------
    def env_at(self, now: str, **extra) -> dict:
        """The fixture env with PACE_NOW set, plus any extra variables."""
        return {**self.env, "PACE_NOW": now, **extra}

    def write_bin(self, name: str, source: str) -> Path:
        target = self.bin / name
        target.write_text(source, encoding="utf-8")
        target.chmod(0o755)
        return target

    def fake_host(self, name: str = "fixture-host") -> None:
        """A FLEET_HOST naming this machine, and a scutil that agrees."""
        (self.love / "FLEET_HOST").write_text(f"{name}\n", encoding="utf-8")
        self.write_bin(
            "scutil",
            '#!/bin/sh\n[ "$1" = "--get" ] && [ "$2" = "LocalHostName" ] '
            f'&& {{ echo "{name}"; exit 0; }}\nexit 1\n',
        )

    def roster(self, *slugs: str) -> Path:
        path = self.love / "citizens-roster.txt"
        path.write_text("".join(f"{s}\n" for s in slugs), encoding="utf-8")
        return path

    def log_text(self, name: str = "fleet.log") -> str:
        path = self.memory / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    # -- homes -----------------------------------------------------------
    def home_path(self, slug: str) -> Path:
        return self.citizens / f"citizen-{slug}"

    def write(self, home: Path, rel: str, text: str) -> Path:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def link(self, home: Path, rel: str, target) -> Path:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        return path

    def make_home(self, slug, *, soul=True, rest=None, event_only=None, cadence=None,
                  letters=(), symlinked_letters=(), files=None, commit=True,
                  message="fixture home", when=None, author=None) -> Path:
        """A citizen home. rest / event_only: True for a fixture text, or the text.
        letters: (name, body) pairs under inbox/. files: {rel: text}."""
        home = self.home_path(slug)
        home.mkdir(parents=True)
        if soul:
            self.write(home, f"{slug}.md", f"The soul of {slug}.\n")
            self.write(home, "WILL.md", "A fixture will.\n")
        if rest is not None and rest is not False:
            self.write(home, "REST", "fixture rest\n" if rest is True else rest)
        if event_only is not None and event_only is not False:
            self.write(home, "EVENT-ONLY",
                       "fixture event-only\n" if event_only is True else event_only)
        if cadence is not None:
            self.write(home, "CADENCE.md", cadence)
        if letters or symlinked_letters:
            (home / "inbox").mkdir(exist_ok=True)
        for name, body in letters:
            self.write(home, f"inbox/{name}", body)
        for name in symlinked_letters:
            (home / "inbox" / name).symlink_to(self.secret)
        for rel, text in (files or {}).items():
            self.write(home, rel, text)
        self.git(home, "init", "-q")
        if commit:
            self.commit(home, message, when=when, author=author, allow_empty=True)
        return home

    def git_env(self, when=None, author=None) -> dict:
        env = dict(self.env)
        if when is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = git_date(when)
        if author is not None:
            name, email = split_author(author)
            env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = name
            env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = email
        return env

    def git(self, home, *args, when=None, author=None, check=True) -> str:
        r = subprocess.run(["git", "-C", str(home), *map(str, args)],
                           env=self.git_env(when, author), check=False,
                           capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AssertionError(f"git {' '.join(map(str, args))} failed: {r.stderr}")
        return r.stdout.strip()

    def commit(self, home, message, *, when=None, author=None, paths=None,
               allow_empty=False) -> str:
        """Stage everything (or only paths) and commit; returns the new HEAD."""
        if paths is None:
            self.git(home, "add", "-A")
        else:
            self.git(home, "add", "-A", "--", *paths)
        extra = ["--allow-empty"] if allow_empty else []
        self.git(home, "commit", "-q", *extra, "-m", message, when=when, author=author)
        return self.head(home)

    def letter(self, home, name, body, *, when=None, author=AI_AUTHOR, message=None) -> str:
        """Place inbox/<name> and commit it; returns the new HEAD."""
        self.write(home, f"inbox/{name}", body)
        return self.commit(home, message or f"letter: {name}", when=when, author=author,
                           paths=[f"inbox/{name}"])

    def head(self, home) -> str:
        return self.git(home, "rev-parse", "HEAD")

    def subjects(self, home, rev="HEAD") -> list:
        """Commit subjects, newest first."""
        out = self.git(home, "log", "--format=%s", rev)
        return out.splitlines() if out else []

    # -- upstreams -------------------------------------------------------
    def give_upstream(self, home) -> Path:
        """Give a home a bare upstream at upstream/<home name>.git."""
        bare = self.upstream / f"{Path(home).name}.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(home), str(bare)],
                       env=self.env, check=True, capture_output=True)
        self.git(home, "remote", "add", "origin", str(bare))
        self.git(home, "fetch", "-q", "origin")
        self.git(home, "branch", "-q", "--set-upstream-to=origin/main", "main")
        return bare

    def elsewhere(self, bare) -> Path:
        """A second clone of a bare upstream, as another host holds it."""
        other = self.root / "elsewhere" / Path(bare).stem
        if other.exists():
            self.git(other, "pull", "-q", "--ff-only")
        else:
            other.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", str(bare), str(other)],
                           env=self.env, check=True, capture_output=True)
        return other

    def push_from_elsewhere(self, bare, name, body, *, message=None, when=None,
                            author=None) -> str:
        """Commit one file from a second clone and push it; returns the bare HEAD."""
        other = self.elsewhere(bare)
        self.write(other, name, body)
        self.commit(other, message or f"elsewhere: {name}", when=when, author=author)
        self.git(other, "push", "-q", "origin", "HEAD")
        return self.git(bare, "rev-parse", "HEAD")

    def drop_clone(self, home) -> None:
        """Remove a local clone, leaving only its upstream."""
        shutil.rmtree(home)

    # -- running ---------------------------------------------------------
    def run(self, script, *args, env=None, timeout=60, stdin=subprocess.DEVNULL):
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            env={**self.env, **(env or {})}, stdin=stdin,
            capture_output=True, text=True, timeout=timeout, check=False,
        )

    def py(self, script, *args, env=None, timeout=60, cwd=None, stdin=subprocess.DEVNULL):
        """Run a Python script isolated (-I), as the beat scripts do."""
        return subprocess.run(
            [sys.executable, "-I", str(script), *map(str, args)],
            env={**self.env, **(env or {})}, cwd=str(cwd or self.root), stdin=stdin,
            capture_output=True, text=True, timeout=timeout, check=False,
        )

    def pace(self, *args, env=None, timeout=60, cwd=None):
        """Run tools/citizen_pace.py under -I."""
        return self.py(PACE, *args, env=env, timeout=timeout, cwd=cwd)

    def gate(self, func, *args, env=None):
        return subprocess.run(
            ["bash", "-c", '. "$1"; shift; "$@"', "_", str(GATES), func, *map(str, args)],
            env={**self.env, **(env or {})}, capture_output=True, text=True,
            timeout=30, check=False,
        )

    def claude_calls(self) -> list:
        if not self.claude_log.exists():
            return []
        return [Path(p).name for p in self.claude_log.read_text().splitlines()]


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        stub = self.server.stub
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        with stub.lock:
            stub.requests.append({"path": self.path, "body": body, "raw": raw})
            status, delay, garbage = stub.status, stub.delay, stub.garbage
            reply = stub.next_reply(body)
        if delay:
            stub.released.wait(delay)
        if status != 200:
            data = b'{"error": "stub status"}'
        elif garbage:
            data = b"not json at all"
        elif self.path.startswith("/v1/chat/completions"):
            data = json.dumps({"choices": [{"message": {"role": "assistant",
                                                         "content": reply}}]}).encode()
        else:
            data = json.dumps({"response": reply}).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # the client gave up (a timeout test)

    def log_message(self, *_args):
        pass


class StubModel:
    """A loopback model server: /v1/chat/completions and /inference.

    reply: a string, or a callable(body) -> string; replies: a FIFO consumed
    first. status: an HTTP status to answer with. delay: seconds to hang before
    answering (released at stop). garbage: answer 200 with a non-JSON body.
    """

    def __init__(self, reply="A true thing, witnessed.", *, model="fixture-model"):
        self.reply = reply
        self.replies = []
        self.status = 200
        self.delay = 0
        self.garbage = False
        self.model = model
        self.requests = []
        self.lock = threading.Lock()
        self.released = threading.Event()
        self.server = None
        self.thread = None

    def next_reply(self, body):
        if self.replies:
            r = self.replies.pop(0)
        else:
            r = self.reply
        return r(body) if callable(r) else r

    def start(self) -> "StubModel":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.server.daemon_threads = True
        self.server.stub = self
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.released.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def env(self) -> dict:
        return {"MLX_PORT": str(self.port), "REFLECT_MODEL": self.model}

    @property
    def bodies(self) -> list:
        return [r["body"] for r in self.requests]

    def user_texts(self) -> list:
        """The user prompt of each request (chat or /inference)."""
        out = []
        for b in self.bodies:
            if not isinstance(b, dict):
                out.append("")
            elif "messages" in b:
                out.append("\n".join(m.get("content", "") for m in b["messages"]
                                     if m.get("role") == "user"))
            else:
                out.append(b.get("prompt", ""))
        return out

    def texts(self) -> str:
        """Every request body, raw, joined (to search for a leaked string)."""
        return "\n".join(r["raw"].decode("utf-8", "replace") for r in self.requests)


class FixtureCase(unittest.TestCase):
    """A test case with self.fx, a fresh Fixture, cleaned up after each test."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = Fixture(self._tmp.name)

    def set_now(self, stamp: str) -> None:
        """PACE_NOW for in-process calls, restored after the test."""
        ctx = pace_now(stamp)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def start_model(self, *args, **kwargs) -> StubModel:
        model = StubModel(*args, **kwargs).start()
        self.addCleanup(model.stop)
        return model
