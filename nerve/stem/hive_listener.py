"""
hive_listener.py -- retired HIVE listener for the mind daemon.

Retired 2026-10-13: the HIVE NATS bus ran on a Hetzner server that was
decommissioned, and its IP went back to Hetzner's pool. The NATS client,
server address and credentials were removed (see git history before this
commit). HiveListener keeps its interface so the daemon still imports it:
connect() raises, and the daemon continues without HIVE.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("mind.hive")

HIVE_RETIRED = True
RETIRED_MESSAGE = (
    "HIVE retired 2026-10-13: the Hetzner NATS bus was decommissioned "
    "and its IP recycled; there is no broker to connect to"
)


class HiveListener:
    """Retired. connect() always raises; the other methods are no-ops."""

    def __init__(self, instance_id: str, on_message: Callable[[dict, str], None]):
        self.instance_id = instance_id
        self.on_message = on_message

    async def connect(self):
        raise RuntimeError(RETIRED_MESSAGE)

    async def publish_presence(self):
        return None

    async def close(self):
        return None

    @property
    def is_connected(self) -> bool:
        return False
