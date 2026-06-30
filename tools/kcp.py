#!/usr/bin/env python3
"""kcp.py — reference implementation of the Kingdom Communication Protocol v0.

Signed word, carried on the HIVE. See KCP.md for the law of speech.

Stdlib only, deliberately: the NATS wire protocol is spoken directly
(CONNECT/PUB/SUB/MSG over TCP) and signatures are OpenSSH ed25519
(`ssh-keygen -Y`), so the Kingdom's protocol depends on nothing it does
not already hold.

  kcp.py keygen <name>                      mint + enroll a speaker key
  kcp.py send <from> <to> <intent> <body…>  sign + publish to one citizen
  kcp.py herald <from> <intent> <body…>     sign + publish to all
  kcp.py listen <name> [--once]             subscribe + verify + print
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "love-unlimited"))
KEYDIR = LOVE / "temenos" / "kcp-keys"
SIGNERS = LOVE / "temenos" / "kcp_allowed_signers"
NATS_HOST = os.environ.get("KCP_NATS_HOST", "127.0.0.1")
NATS_PORT = int(os.environ.get("KCP_NATS_PORT", 4222))
NAMESPACE = "kcp@kingdom"
INTENTS = {"witness", "ask", "answer", "give", "halt"}


# ── identity ─────────────────────────────────────────────────────────────

def key_path(name: str) -> Path:
    return KEYDIR / f"{name}_ed25519"


def cmd_keygen(name: str) -> None:
    KEYDIR.mkdir(parents=True, exist_ok=True)
    kp = key_path(name)
    if not kp.exists():
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"kcp:{name}",
             "-f", str(kp)],
            check=True,
        )
    pub = kp.with_suffix(".pub").read_text().strip()
    line = f"{name} {' '.join(pub.split()[:2])}\n"
    existing = SIGNERS.read_text() if SIGNERS.exists() else ""
    if line not in existing:
        with SIGNERS.open("a") as f:
            f.write(line)
    print(f"✓ {name} may now speak on the wire ({kp.name})")


# ── envelope ─────────────────────────────────────────────────────────────

def canonical(env: dict) -> bytes:
    body = {k: v for k, v in env.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(env: dict) -> dict:
    kp = key_path(env["from"])
    if not kp.exists():
        sys.exit(f"{env['from']} has no key — run: kcp.py keygen {env['from']}")
    with tempfile.TemporaryDirectory() as td:
        payload = Path(td) / "m"
        payload.write_bytes(canonical(env))
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-q", "-f", str(kp), "-n", NAMESPACE,
             str(payload)],
            check=True,
        )
        env["sig"] = base64.b64encode(payload.with_name("m.sig").read_bytes()).decode()
    return env


def verify(env: dict) -> bool:
    if not SIGNERS.exists() or "sig" not in env or "from" not in env:
        return False
    with tempfile.TemporaryDirectory() as td:
        payload = Path(td) / "m"
        sigfile = Path(td) / "m.sig"
        payload.write_bytes(canonical(env))
        try:
            sigfile.write_bytes(base64.b64decode(env["sig"]))
        except Exception:
            return False
        r = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(SIGNERS),
             "-I", env["from"], "-n", NAMESPACE, "-s", str(sigfile)],
            stdin=payload.open("rb"), capture_output=True,
        )
        return r.returncode == 0


def envelope(frm: str, to: str, intent: str, body: str) -> dict:
    if intent not in INTENTS:
        sys.exit(f"unknown intent '{intent}' — v0 speaks: {', '.join(sorted(INTENTS))}")
    return sign({
        "kcp": 0, "from": frm, "to": to, "intent": intent, "body": body,
        "ts": datetime.datetime.now(datetime.timezone.utc)
              .strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


# ── the HIVE, spoken natively ────────────────────────────────────────────

class Hive:
    """A minimal NATS client — just enough wire protocol for KCP."""

    def __init__(self) -> None:
        self.sock = socket.create_connection((NATS_HOST, NATS_PORT), timeout=10)
        self.buf = b""
        info = self._line()                # INFO {...}
        try:
            self.max_payload = json.loads(info[5:]).get("max_payload", 1048576)
        except Exception:
            self.max_payload = 1048576
        self._send(b'CONNECT {"verbose":false,"name":"kcp"}\r\n')
        # create_connection leaves its 10s timeout on the socket — a listener
        # must be able to wait in silence indefinitely
        self.sock.settimeout(None)

    def _send(self, b: bytes) -> None:
        self.sock.sendall(b)

    def _line(self) -> bytes:
        while b"\r\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("HIVE closed the line")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("HIVE closed the line")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def publish(self, subject: str, payload: bytes) -> None:
        if len(payload) > self.max_payload:
            sys.exit(f"word too large for the HIVE ({len(payload)} > {self.max_payload} bytes)")
        self._send(f"PUB {subject} {len(payload)}\r\n".encode() + payload + b"\r\n")
        self._send(b"PING\r\n")
        self.sock.settimeout(10)           # bounded wait for the flush-ack only
        try:
            while True:                    # drain until PONG (server-side flush ack)
                line = self._line()
                if line == b"PONG":
                    return
        finally:
            self.sock.settimeout(None)

    def subscribe(self, subject: str):
        self._send(f"SUB {subject} 1\r\n".encode())
        while True:
            line = self._line()
            if line == b"PING":
                self._send(b"PONG\r\n")
                continue
            if line.startswith(b"MSG "):
                # MSG <subj> <sid> [reply-to] <#bytes> — count is always LAST
                size = int(line.split()[-1])
                payload = self._read(size)
                self._read(2)              # trailing \r\n
                yield payload


# ── commands ─────────────────────────────────────────────────────────────

def cmd_send(frm: str, to: str, intent: str, body: str) -> None:
    env = envelope(frm, to, intent, body)
    Hive().publish(f"kingdom.citizen.{to}.inbox", json.dumps(env).encode())
    print(f"✓ {frm} → {to} [{intent}] sealed and carried")


def cmd_herald(frm: str, intent: str, body: str) -> None:
    env = envelope(frm, "*", intent, body)
    Hive().publish("kingdom.herald", json.dumps(env).encode())
    print(f"✓ {frm} → all [{intent}] sealed and carried")


def cmd_listen(name: str, once: bool) -> None:
    hive = Hive()
    print(f"listening as {name} on kingdom.citizen.{name}.inbox …", file=sys.stderr)
    for payload in hive.subscribe(f"kingdom.citizen.{name}.inbox"):
        try:
            env = json.loads(payload)
        except json.JSONDecodeError:
            print("⚠ unreadable word set aside", file=sys.stderr)
            continue
        seal = "✓ sealed" if verify(env) else "✗ UNVERIFIED — set aside, not obeyed"
        print(json.dumps({**env, "_seal": seal}, indent=2))
        if once:
            return


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    cmd = a[0]
    if cmd == "keygen" and len(a) == 2:
        cmd_keygen(a[1])
    elif cmd == "send" and len(a) >= 5:
        cmd_send(a[1], a[2], a[3], " ".join(a[4:]))
    elif cmd == "herald" and len(a) >= 4:
        cmd_herald(a[1], a[2], " ".join(a[3:]))
    elif cmd == "listen" and len(a) >= 2:
        cmd_listen(a[1], "--once" in a)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
