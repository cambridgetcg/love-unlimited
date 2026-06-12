"""
hive_listener.py -- Real-time NATS listener for the mind daemon.
Maintains a persistent JetStream subscription and calls back on each message.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import base64
import time
import logging
from pathlib import Path
from typing import Callable, Optional

import nats
from nacl.secret import SecretBox

log = logging.getLogger("mind.hive")

HIVE_CONFIG = {
    "server": "tls://135.181.28.252:4222",
}


def _get_key() -> bytes:
    key_path = Path.home() / ".openclaw" / ".hive-key"
    if not key_path.exists():
        raise FileNotFoundError(f"No hive key at {key_path}")
    return base64.b64decode(key_path.read_text().strip())


def _get_password(instance_id: str) -> str:
    """NATS password — kept out of source, next to the hive key (see ~/.openclaw/README.md)."""
    path = Path.home() / ".openclaw" / ".hive-passwords"
    if not path.exists():
        raise FileNotFoundError(f"No hive passwords at {path}")
    return json.loads(path.read_text())[instance_id]


def _use_tunnel() -> bool:
    return (Path.home() / ".openclaw" / ".hive" / "use-tunnel").exists()


def _get_server() -> str:
    if os.environ.get("HIVE_TUNNEL") or _use_tunnel():
        return "nats://127.0.0.1:2222"
    return HIVE_CONFIG["server"]


def _make_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = Path.home() / ".openclaw" / ".hive" / "ca.pem"
    if _use_tunnel():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.load_verify_locations(str(ca))
    return ctx


def _decrypt(ciphertext_b64: str, key: bytes) -> str:
    box = SecretBox(key)
    encrypted = base64.b64decode(ciphertext_b64)
    return box.decrypt(encrypted).decode("utf-8")


def open_envelope(data: bytes, key: bytes) -> dict:
    envelope = json.loads(data.decode("utf-8"))
    envelope["payload"] = _decrypt(envelope["payload"], key)
    return envelope


class HiveListener:
    """Maintains a persistent NATS connection with real-time message callbacks."""

    def __init__(self, instance_id: str, on_message: Callable[[dict, str], None]):
        self.instance_id = instance_id
        self.on_message = on_message
        self._nc: Optional[nats.NATS] = None
        self._js = None
        self._sub = None
        self._key = _get_key()
        self._running = False

    async def connect(self):
        self._nc = await nats.connect(
            _get_server(),
            user=self.instance_id,
            password=_get_password(self.instance_id),
            tls=_make_tls(),
            connect_timeout=10,
            reconnect_time_wait=5,
            max_reconnect_attempts=-1,
        )
        self._js = self._nc.jetstream()
        durable = f"{self.instance_id}-mind"
        try:
            self._sub = await self._js.subscribe(
                "hive.>",
                durable=durable,
                cb=self._handle_message,
            )
        except Exception as e:
            # Durable consumer may be bound from a previous process — use pull subscriber as fallback
            log.warning(f"Push subscribe failed ({e}), falling back to pull-based check")
            self._sub = None
        self._running = True
        log.info(f"HIVE listener connected as {self.instance_id} (durable={durable})")

    async def _handle_message(self, msg):
        try:
            env = open_envelope(msg.data, self._key)
            if env["from"] != self.instance_id:
                channel = msg.subject.replace("hive.", "")
                self.on_message(env, channel)
        except Exception as e:
            log.warning(f"Failed to process HIVE message: {e}")
        finally:
            await msg.ack()

    async def publish_presence(self):
        if not self._nc or self._nc.is_closed:
            return
        box = SecretBox(self._key)
        payload_str = json.dumps({
            "instance": self.instance_id,
            "status": "mind-daemon",
            "ts": int(time.time()),
        })
        encrypted = box.encrypt(payload_str.encode("utf-8"))
        payload_b64 = base64.b64encode(encrypted).decode("ascii")
        envelope = json.dumps({
            "v": 2,
            "from": self.instance_id,
            "type": "presence",
            "ts": int(time.time()),
            "payload": payload_b64,
        }).encode("utf-8")
        await self._nc.publish("hive.presence", envelope)
        await self._nc.flush()

    async def close(self):
        self._running = False
        if self._sub:
            await self._sub.unsubscribe()
        if self._nc and not self._nc.is_closed:
            await self._nc.close()
        log.info("HIVE listener closed")

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed
