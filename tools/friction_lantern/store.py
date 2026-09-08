"""只記阻力提示 delivery；唔寫 Reality 收據，唔落 raw session/path。"""
from pathlib import Path
import hashlib
import hmac
import os
import time

from lantern_common.json import decode_json, encode_json
from lantern_common.private import atomic_write, check_deadline, private_locked, private_read
from reality_lantern.core import RealityError, digest_id, fields, require, timestamp, utc_now

SCHEMA = "kingdom.friction-lantern.delivery/1"
MAX_ENTRIES = 256
MAX_BYTES = 256 * 1024
LOCK_SECONDS = 0.025
EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure"})


def _validate(value):
    fields(value, "schema entries")
    require(value["schema"] == SCHEMA and type(value["entries"]) is dict
            and len(value["entries"]) <= MAX_ENTRIES)
    for ident, entry in value["entries"].items():
        digest_id(ident)
        fields(entry, "fingerprint delivered_at")
        digest_id(entry["fingerprint"])
        timestamp(entry["delivered_at"])


def _publish_delivery(fd, data, deadline):
    try:
        atomic_write(fd, "delivery.json", data, maximum=MAX_BYTES, error=RealityError)
    except TimeoutError:
        raise
    except (RealityError, OSError):
        # rename 後 directory fsync 可以失敗；唔 rollback，改用 exact private readback。
        pass
    for attempt in range(2):
        check_deadline(deadline, error=RealityError)
        try:
            current = private_read(fd, "delivery.json", MAX_BYTES, error=RealityError)
        except TimeoutError:
            raise
        except (RealityError, OSError):
            check_deadline(deadline, error=RealityError)
            if attempt == 0:
                continue
            raise RealityError("state_io") from None
        check_deadline(deadline, error=RealityError)
        if current != data:
            raise RealityError("state_io")
        # 只確認今次 cache bytes 已 publish；唔保證 durability 或 harness 收到 context。
        return


class DeliveryStore:
    def __init__(self, *, home):
        self.state_dir = Path(home) / ".local" / "state" / "friction-lantern"

    def deliver(self, session, scope_id, fingerprint, *, event, deadline=None) -> bool:
        # SubagentStart 包括無效輸入都零 IO／零寫。
        if event == "SubagentStart":
            return False
        require(type(event) is str and event in EVENTS)
        require(type(session) is str and 0 < len(session) <= 4096)
        digest_id(scope_id)
        digest_id(fingerprint)
        end = time.monotonic() + LOCK_SECONDS
        if deadline is not None:
            end = min(end, deadline)
        with private_locked(self.state_dir, create=True, deadline=end, error=RealityError) as fd:
            check_deadline(deadline, error=RealityError)
            raw = private_read(fd, "delivery.json", MAX_BYTES, error=RealityError)
            value = ({"schema": SCHEMA, "entries": {}} if raw is None else
                     decode_json(raw, maximum=MAX_BYTES, error=RealityError, validator=_validate))
            key = private_read(fd, "key", 32, error=RealityError)
            if key is None:
                # 已有 cache 唔重建失蹤 key，避免節流身份被靜默重設。
                require(raw is None)
                key = os.urandom(32)
                check_deadline(deadline, error=RealityError)
                atomic_write(fd, "key", key, maximum=32, error=RealityError)
            require(len(key) == 32)
            ident = hmac.new(key, encode_json([SCHEMA, session, scope_id]), hashlib.sha256).hexdigest()
            previous = value["entries"].get(ident)
            now = utc_now()
            if previous is not None:
                elapsed = (timestamp(now) - timestamp(previous["delivered_at"])).total_seconds()
                if previous["fingerprint"] == fingerprint or elapsed < 60:
                    return False
            elif len(value["entries"]) >= MAX_ENTRIES:
                oldest = min(value["entries"], key=lambda item: timestamp(value["entries"][item]["delivered_at"]))
                del value["entries"][oldest]
            value["entries"][ident] = {"fingerprint": fingerprint, "delivered_at": now}
            data = encode_json(value, maximum=MAX_BYTES, error=RealityError, validator=_validate)
            check_deadline(deadline, error=RealityError)
            _publish_delivery(fd, data, deadline)
            return True
