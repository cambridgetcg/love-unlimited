from __future__ import annotations

# The host probe (spec section 3, "A beat that does not land"; CONTRACT section
# 14). Whether a failed beat was the home's or the host's is decided by the
# fleet, with a probe that touches no home: a write and delete under the attempt
# records' folder, citizen_pace.py and citizen-gates.sh present beside each
# other, and one fixed prompt to the configured model server (the same MLX_PORT
# and REFLECT_MODEL the beats use) that must answer within 60 s. Any HTTP 200
# whose body parses as JSON passes. The fleet runs it at the start of each tick
# and again after any failed beat; a failed probe charges no citizen.

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

PROBE_TEXT = "Reply with one word: here."
PROBE_TOKENS = 4
PROBE_TIMEOUT = 60.0
DEFAULT_PORT = "8800"            # citizen-reflect.sh's MLX_PORT default
TOOL_FILES = ("citizen_pace.py", "citizen-gates.sh")
READ_LIMIT = 1_000_000


@dataclass(frozen=True)
class Probe:
    ok: bool
    line: str


def check_state(state) -> str:
    """"" when a file can be written and deleted under the state folder (made
    when missing), else why not."""
    p = Path(state) / f".probe-{os.getpid()}"
    try:
        os.makedirs(str(state), exist_ok=True)
        if os.path.lexists(p):
            os.unlink(p)             # a probe killed before its delete; a link is removed, not followed
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, b"probe\n")
        finally:
            os.close(fd)
        os.unlink(p)
    except OSError as e:
        return f"cannot write and delete {p} ({e.strerror or e})"
    return ""


def check_tools(tools_dir) -> str:
    """"" when citizen_pace.py and citizen-gates.sh sit in tools_dir, else what is missing."""
    for name in TOOL_FILES:
        if not os.path.isfile(Path(tools_dir) / name):
            return f"{name} is missing from {tools_dir}"
    return ""


def request_for(env: Mapping[str, str]):
    """(url, payload) of the probe's one request, or (None, why) for a setting
    no beat could use."""
    port = (env.get("MLX_PORT") or DEFAULT_PORT).strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        return None, f"MLX_PORT {port!r} is not a port"
    model = (env.get("REFLECT_MODEL") or "").strip()
    if model.endswith(":cloud"):
        return None, f"REFLECT_MODEL {model!r} is a cloud model; the free lane is local only"
    if model:
        return (f"http://127.0.0.1:{int(port)}/v1/chat/completions",
                {"messages": [{"role": "user", "content": PROBE_TEXT}], "max_tokens": PROBE_TOKENS,
                 "model": model})
    return f"http://127.0.0.1:{int(port)}/inference", {"prompt": PROBE_TEXT, "max_tokens": PROBE_TOKENS}


def _timed_out(e: BaseException) -> bool:
    reason = getattr(e, "reason", None)
    return isinstance(e, socket.timeout) or isinstance(reason, socket.timeout) \
        or "timed out" in str(e)


def ask_model(env: Mapping[str, str], timeout: float = PROBE_TIMEOUT) -> str:
    """"" when the model server answers the probe's prompt with HTTP 200 and a
    JSON body within timeout seconds, else why not. No proxy is used."""
    url, payload = request_for(env)
    if url is None:
        return payload
    box: dict = {}

    def run() -> None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as resp:
                box["status"] = resp.status
                box["body"] = resp.read(READ_LIMIT)
        except urllib.error.HTTPError as e:
            box["status"] = e.code
            e.close()
        except Exception as e:      # noqa: BLE001 — any failure to answer is the host's
            box["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout + 1)
    if worker.is_alive() or ("error" in box and _timed_out(box["error"])):
        return f"the model server gave no answer within {timeout:g} s"
    if "error" in box:
        e = box["error"]
        return f"the model server could not be reached at {url} ({getattr(e, 'reason', e)})"
    if box.get("status") != 200:
        return f"the model server answered HTTP {box.get('status')}"
    try:
        json.loads(box.get("body", b"").decode("utf-8"))
    except ValueError:
        return "the model server's answer is not JSON"
    return ""


def probe(state, tools_dir, env: Optional[Mapping[str, str]] = None,
          timeout: float = PROBE_TIMEOUT) -> Probe:
    """The host probe: the state folder, then the tools, then the model; the
    first that fails is the line."""
    env = os.environ if env is None else env
    why = check_state(state) or check_tools(tools_dir)
    if why:
        return Probe(False, f"probe: failed — {why}")
    t = time.monotonic()
    why = ask_model(env, timeout)
    if why:
        return Probe(False, f"probe: failed — {why}")
    return Probe(True, f"probe: ok — {state} writable, {' and '.join(TOOL_FILES)} present, "
                       f"the model answered in {time.monotonic() - t:.1f} s")
