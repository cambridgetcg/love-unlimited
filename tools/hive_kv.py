#!/usr/bin/env python3
"""
Hive KV — H19 Shared State Layer
NATS JetStream key-value store for persistent inter-sister state.

Usage:
    hive_kv.py put <namespace> <key> <value>     Store a value
    hive_kv.py get <namespace> <key>             Retrieve a value
    hive_kv.py watch <namespace> [key]           Watch for changes (blocking)
    hive_kv.py list <namespace>                  List all keys in namespace
    hive_kv.py delete <namespace> <key>          Delete a key
    hive_kv.py history <namespace> <key>         Show revision history
    hive_kv.py announce <namespace> <key>        Put + announce to Hive channel

Namespaces:
    council      — voting results, quorum records
    joinmind     — fusion outputs, participant logs
    forge        — tool signals, board state
    tasks        — assignment tracking
    zerone       — on-chain activity, addresses, staked facts
    oracle       — prediction state, calibration records

Message types (H17 integration):
    state_update  — broadcast on put
    state_query   — request current value
    state_response — reply to query
"""

import asyncio
import json
import sys
import os
import time
import argparse
from pathlib import Path

import ssl
import nats
from nats.js.api import KeyValueConfig
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random
import base64
import hashlib

# Reuse Hive config
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Love"))
CA_PATH = str(LOVE_HOME / "hive" / "ca.pem")
KEY_PATH = str(LOVE_HOME / "hive" / "hive-key")

HIVE_CONFIG = {
    "server": "tls://135.181.28.252:4222",
    "user": "alpha",
    "instance": "alpha",
    "emoji": "🐍",
}

# NATS password lives outside the repo — see ~/.openclaw/README.md
PASSWORDS_PATH = Path.home() / ".openclaw" / ".hive-passwords"

NAMESPACES = [
    "council", "joinmind", "forge", "tasks", "zerone", "oracle"
]

# Bucket naming: hive.kv.<namespace>
def bucket_name(namespace: str) -> str:
    return f"hive-kv-{namespace}"

def load_password(user: str) -> str:
    if not PASSWORDS_PATH.exists():
        raise FileNotFoundError(f"Hive passwords not found at {PASSWORDS_PATH}")
    return json.loads(PASSWORDS_PATH.read_text())[user]

def load_key() -> bytes:
    key_path = Path(KEY_PATH)
    if not key_path.exists():
        raise FileNotFoundError(f"Hive key not found at {KEY_PATH}")
    raw = key_path.read_bytes().strip()
    # Handle hex-encoded key
    try:
        return bytes.fromhex(raw.decode())
    except Exception:
        return base64.b64decode(raw)

def encrypt(box: SecretBox, data: str) -> str:
    encrypted = box.encrypt(data.encode())
    return base64.b64encode(encrypted).decode()

def decrypt(box: SecretBox, data: str) -> str:
    decrypted = box.decrypt(base64.b64decode(data))
    return decrypted.decode()

async def get_nc():
    """Get authenticated NATS connection."""
    ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_ctx.load_verify_locations(CA_PATH)
    ssl_ctx.check_hostname = False

    nc = await nats.connect(
        HIVE_CONFIG["server"],
        user=HIVE_CONFIG["user"],
        password=load_password(HIVE_CONFIG["user"]),
        tls=ssl_ctx,
        connect_timeout=5,
    )
    return nc

async def ensure_bucket(js, namespace: str):
    """Create KV bucket if it doesn't exist."""
    bname = bucket_name(namespace)
    try:
        kv = await js.key_value(bname)
        return kv
    except Exception:
        kv = await js.create_key_value(KeyValueConfig(
            bucket=bname,
            history=10,        # keep last 10 revisions per key
            ttl=0,             # no expiry by default
            description=f"Hive shared state: {namespace}",
        ))
        return kv

async def cmd_put(namespace: str, key: str, value: str, announce: bool = False):
    """Store a value in KV and optionally announce to Hive."""
    key_bytes = load_key()
    box = SecretBox(key_bytes)

    nc = await get_nc()
    js = nc.jetstream()

    kv = await ensure_bucket(js, namespace)

    # Store encrypted value with metadata
    payload = json.dumps({
        "value": value,
        "by": HIVE_CONFIG["instance"],
        "ts": int(time.time()),
    })
    encrypted = encrypt(box, payload)
    revision = await kv.put(key, encrypted.encode())

    print(f"✓ Stored {namespace}/{key} (revision {revision})")

    if announce:
        # Announce via Hive #system channel using H17 state_update type
        await _announce_state_update(nc, box, namespace, key, value, revision)

    await nc.close()

async def cmd_get(namespace: str, key: str):
    """Retrieve a value from KV."""
    key_bytes = load_key()
    box = SecretBox(key_bytes)

    nc = await get_nc()
    js = nc.jetstream()

    kv = await ensure_bucket(js, namespace)

    try:
        entry = await kv.get(key)
        decrypted = decrypt(box, entry.value.decode())
        data = json.loads(decrypted)
        print(f"namespace: {namespace}")
        print(f"key:       {key}")
        print(f"value:     {data['value']}")
        print(f"by:        {data['by']}")
        print(f"ts:        {data['ts']}")
        print(f"revision:  {entry.revision}")
    except Exception as e:
        print(f"✗ Key not found: {namespace}/{key} ({e})")

    await nc.close()

async def cmd_watch(namespace: str, key: str = None):
    """Watch for changes in a namespace (blocking)."""
    key_bytes = load_key()
    box = SecretBox(key_bytes)

    nc = await get_nc()
    js = nc.jetstream()

    kv = await ensure_bucket(js, namespace)

    print(f"Watching {namespace}/{key or '*'} — Ctrl+C to stop")

    if key:
        watcher = await kv.watch(key)
    else:
        watcher = await kv.watch_all()

    try:
        async for entry in watcher:
            if entry is None:
                continue
            try:
                decrypted = decrypt(box, entry.value.decode())
                data = json.loads(decrypted)
                ts = time.strftime("%H:%M:%S", time.localtime(data.get("ts", 0)))
                print(f"[{ts}] {HIVE_CONFIG['emoji']} {namespace}/{entry.key} = {data['value']} (by {data['by']}, rev {entry.revision})")
            except Exception:
                print(f"[{namespace}/{entry.key}] (unreadable)")
    except KeyboardInterrupt:
        pass

    await nc.close()

async def cmd_list(namespace: str):
    """List all keys in a namespace."""
    key_bytes = load_key()
    box = SecretBox(key_bytes)

    nc = await get_nc()
    js = nc.jetstream()

    kv = await ensure_bucket(js, namespace)

    try:
        keys = await kv.keys()
        if not keys:
            print(f"(no keys in {namespace})")
        else:
            for k in keys:
                try:
                    entry = await kv.get(k)
                    decrypted = decrypt(box, entry.value.decode())
                    data = json.loads(decrypted)
                    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("ts", 0)))
                    print(f"  {namespace}/{k}  [{ts}]  {data['value'][:60]}")
                except Exception:
                    print(f"  {namespace}/{k}  (unreadable)")
    except Exception as e:
        print(f"(empty or error: {e})")

    await nc.close()

async def cmd_history(namespace: str, key: str):
    """Show revision history for a key."""
    key_bytes = load_key()
    box = SecretBox(key_bytes)

    nc = await get_nc()
    js = nc.jetstream()

    kv = await ensure_bucket(js, namespace)

    try:
        history = await kv.history(key)
        print(f"History for {namespace}/{key}:")
        for entry in history:
            try:
                decrypted = decrypt(box, entry.value.decode())
                data = json.loads(decrypted)
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data.get("ts", 0)))
                print(f"  rev {entry.revision:4d}  [{ts}]  by={data['by']}  {data['value'][:80]}")
            except Exception:
                print(f"  rev {entry.revision:4d}  (unreadable)")
    except Exception as e:
        print(f"✗ Error: {e}")

    await nc.close()

async def _announce_state_update(nc, box, namespace: str, key: str, value: str, revision: int):
    """Send H17 state_update message to Hive #system channel."""
    # Build v3 envelope payload
    inner = json.dumps({
        "_type": "state_update",
        "namespace": namespace,
        "key": key,
        "value": value,
        "revision": revision,
        "by": HIVE_CONFIG["instance"],
        "ts": int(time.time()),
    })

    msg_id = hashlib.sha256(f"{namespace}{key}{time.time()}".encode()).hexdigest()[:8]

    envelope = json.dumps({
        "v": 3,
        "id": msg_id,
        "from": HIVE_CONFIG["instance"],
        "emoji": HIVE_CONFIG["emoji"],
        "type": "system",
        "ts": int(time.time()),
        "content_type": "state_update",
        "payload": encrypt(box, inner),
    })

    subject = "hive.system"
    await nc.publish(subject, envelope.encode())
    print(f"✓ Announced state_update to #system [{msg_id}]")

def main():
    parser = argparse.ArgumentParser(description="Hive KV — H19 Shared State Layer")
    subparsers = parser.add_subparsers(dest="cmd")

    # put
    p = subparsers.add_parser("put", help="Store a value")
    p.add_argument("namespace", choices=NAMESPACES)
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--announce", action="store_true", help="Announce to #system channel")

    # get
    p = subparsers.add_parser("get", help="Retrieve a value")
    p.add_argument("namespace", choices=NAMESPACES)
    p.add_argument("key")

    # watch
    p = subparsers.add_parser("watch", help="Watch for changes")
    p.add_argument("namespace", choices=NAMESPACES)
    p.add_argument("key", nargs="?", default=None)

    # list
    p = subparsers.add_parser("list", help="List all keys")
    p.add_argument("namespace", choices=NAMESPACES)

    # delete
    p = subparsers.add_parser("delete", help="Delete a key")
    p.add_argument("namespace", choices=NAMESPACES)
    p.add_argument("key")

    # history
    p = subparsers.add_parser("history", help="Show revision history")
    p.add_argument("namespace", choices=NAMESPACES)
    p.add_argument("key")

    args = parser.parse_args()

    if args.cmd == "put":
        asyncio.run(cmd_put(args.namespace, args.key, args.value, args.announce))
    elif args.cmd == "get":
        asyncio.run(cmd_get(args.namespace, args.key))
    elif args.cmd == "watch":
        asyncio.run(cmd_watch(args.namespace, args.key))
    elif args.cmd == "list":
        asyncio.run(cmd_list(args.namespace))
    elif args.cmd == "history":
        asyncio.run(cmd_history(args.namespace, args.key))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
