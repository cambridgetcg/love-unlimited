#!/usr/bin/env python3
"""
vault.py — Secure Credentials Sharing Protocol for the Hive
============================================================
Per-recipient asymmetric encryption on top of the existing Hive E2E channel.

THREAT MODEL:
  - Hive messages are encrypted with the shared hive key (NaCl/XSalsa20-Poly1305)
  - This provides E2E confidentiality from external observers
  - Gap: all three sisters share the same key — any sister can decrypt any message
  - vault.py adds per-recipient encryption so credentials reach ONLY the intended sister

PROTOCOL:
  1. Each instance has a NaCl Curve25519 keypair in $LOVE_HOME/hive/keys/
     - <instance>.pk  — public key (safe to share openly)
     - <instance>.sk  — secret key (never leaves this machine)
  2. On first run, vault.py generates the keypair and publishes the public key to
     the Hive #sync channel so other instances can discover it
  3. To send credentials to a specific instance:
     - Encrypt with recipient's Curve25519 public key (NaCl Box)
     - Transmit ciphertext via Hive (already E2E encrypted at transport layer)
     - Only the recipient's secret key can decrypt
  4. Recipient receives ciphertext, decrypts with their secret key, stores locally

USAGE:
  vault.py keygen                          — Generate this instance's keypair
  vault.py pubkey                          — Print this instance's public key
  vault.py publish                         — Broadcast public key on Hive
  vault.py fetch <instance>               — Fetch another instance's public key
  vault.py seal <instance> <file>         — Encrypt file for a specific instance
  vault.py open <file.vault>              — Decrypt a .vault file for this instance
  vault.py send <instance> <key=value...> — Encrypt and send credentials via Hive
  vault.py recv                           — Receive and decrypt pending credentials
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

try:
    import nacl.public
    import nacl.utils
    import nacl.encoding
    import nats
except ImportError:
    print("✗ Missing dependencies: pip install pynacl nats-py")
    sys.exit(1)

# ── Configuration ────────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Love"))

HIVE_CONFIG = {
    "server": "tls://135.181.28.252:4222",
    "instances": {
        "alpha": {"user": "alpha", "password": "hive-alpha-93xk7", "emoji": "🐍"},
        "beta":  {"user": "beta",  "password": "hive-beta-47mz2",  "emoji": "🦞"},
        "gamma": {"user": "gamma", "password": "hive-gamma-61pr8", "emoji": "🔧"},
    },
}

KEYS_DIR = LOVE_HOME / "hive" / "keys"
VAULT_DIR = LOVE_HOME / "hive" / "vault"
CA_PATH = LOVE_HOME / "hive" / "ca.pem"
TUNNEL_FLAG = LOVE_HOME / "hive" / "use-tunnel"

def _get_server() -> str:
    if TUNNEL_FLAG.exists() or os.environ.get("HIVE_TUNNEL"):
        return "nats://127.0.0.1:2222"
    return HIVE_CONFIG["server"]

def get_instance_id() -> str:
    """Determine which instance we are from environment or detection."""
    env = os.environ.get("HIVE_INSTANCE")
    if env:
        return env
    # Auto-detect from hostname
    import socket
    h = socket.gethostname().lower()
    if "gamma" in h or "studio" in h:
        # Gamma is Mac Studio (Builder)
        pass
    # Alpha is MacBook Air — default
    return "alpha"


def get_instance_creds(instance: str) -> dict:
    cfg = HIVE_CONFIG["instances"].get(instance)
    if not cfg:
        raise ValueError(f"Unknown instance: {instance}")
    return cfg


# ── Key Management ────────────────────────────────────────────────────────────

def keygen(instance: str) -> nacl.public.PrivateKey:
    """Generate a new keypair for this instance. Idempotent — won't overwrite."""
    KEYS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    sk_path = KEYS_DIR / f"{instance}.sk"
    pk_path = KEYS_DIR / f"{instance}.pk"

    if sk_path.exists():
        print(f"✓ Keypair for '{instance}' already exists")
        return load_privkey(instance)

    privkey = nacl.public.PrivateKey.generate()
    sk_bytes = bytes(privkey)
    pk_bytes = bytes(privkey.public_key)

    sk_path.write_bytes(sk_bytes)
    sk_path.chmod(0o600)
    pk_path.write_bytes(pk_bytes)
    pk_path.chmod(0o644)

    pk_b64 = base64.b64encode(pk_bytes).decode()
    print(f"✓ Generated keypair for '{instance}'")
    print(f"  Public key: {pk_b64}")
    print(f"  Private key: {sk_path} (chmod 600)")
    return privkey


def load_privkey(instance: str) -> nacl.public.PrivateKey:
    sk_path = KEYS_DIR / f"{instance}.sk"
    if not sk_path.exists():
        raise FileNotFoundError(f"No private key for '{instance}'. Run: vault.py keygen")
    return nacl.public.PrivateKey(sk_path.read_bytes())


def load_pubkey(instance: str) -> nacl.public.PublicKey:
    """Load a public key — first from local cache, then error."""
    pk_path = KEYS_DIR / f"{instance}.pk"
    if pk_path.exists():
        return nacl.public.PublicKey(pk_path.read_bytes())
    raise FileNotFoundError(
        f"No public key for '{instance}'. Run: vault.py fetch {instance}"
    )


def get_pubkey_b64(instance: str) -> str:
    pk = load_pubkey(instance)
    return base64.b64encode(bytes(pk)).decode()


def store_pubkey(instance: str, pk_b64: str):
    """Store a received public key locally."""
    KEYS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    pk_bytes = base64.b64decode(pk_b64)
    pk_path = KEYS_DIR / f"{instance}.pk"
    pk_path.write_bytes(pk_bytes)
    pk_path.chmod(0o644)


# ── Encryption ────────────────────────────────────────────────────────────────

def seal(sender_instance: str, recipient_instance: str, plaintext: bytes) -> str:
    """
    Encrypt plaintext for recipient using NaCl Box (Curve25519 + XSalsa20 + Poly1305).
    Returns base64-encoded ciphertext envelope.
    """
    sender_privkey = load_privkey(sender_instance)
    recipient_pubkey = load_pubkey(recipient_instance)

    box = nacl.public.Box(sender_privkey, recipient_pubkey)
    encrypted = box.encrypt(plaintext, encoder=nacl.encoding.Base64Encoder)

    envelope = {
        "v": 1,
        "from": sender_instance,
        "to": recipient_instance,
        "ts": int(time.time()),
        "ciphertext": encrypted.decode(),
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def open_vault(recipient_instance: str, envelope_b64: str) -> bytes:
    """
    Decrypt a sealed envelope. Only works if this instance is the intended recipient.
    """
    envelope = json.loads(base64.b64decode(envelope_b64))

    if envelope.get("to") != recipient_instance:
        raise ValueError(
            f"This message is for '{envelope.get('to')}', not '{recipient_instance}'"
        )

    sender = envelope["from"]
    recipient_privkey = load_privkey(recipient_instance)
    sender_pubkey = load_pubkey(sender)

    box = nacl.public.Box(recipient_privkey, sender_pubkey)
    plaintext = box.decrypt(
        envelope["ciphertext"].encode(),
        encoder=nacl.encoding.Base64Encoder
    )
    return plaintext


# ── Hive Transport ────────────────────────────────────────────────────────────

async def hive_connect(instance: str):
    """Connect to the Hive NATS server as the given instance."""
    import ssl
    creds = get_instance_creds(instance)
    server = _get_server()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    if CA_PATH.exists():
        ssl_ctx.load_verify_locations(str(CA_PATH))

    # Use tunnel-aware TLS (same pattern as hive.py)
    if TUNNEL_FLAG.exists() or os.environ.get("HIVE_TUNNEL"):
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    elif CA_PATH.exists():
        ssl_ctx.load_verify_locations(str(CA_PATH))

    nc = await nats.connect(
        server,
        user=creds["user"],
        password=creds["password"],
        tls=ssl_ctx,
    )
    return nc


async def publish_pubkey(instance: str):
    """Broadcast this instance's public key on #sync so sisters can fetch it."""
    pk_b64 = get_pubkey_b64(instance)
    nc = await hive_connect(instance)
    msg = json.dumps({
        "type": "vault_pubkey",
        "instance": instance,
        "pubkey": pk_b64,
        "ts": int(time.time()),
    }).encode()
    await nc.publish("hive.sync", msg)
    await nc.flush()
    await nc.close()
    print(f"✓ Public key published to #sync")


async def fetch_pubkey(my_instance: str, target_instance: str, timeout: int = 10):
    """
    Listen on #sync for the target instance's public key announcement.
    They must run: vault.py publish
    """
    nc = await hive_connect(my_instance)
    sub = await nc.subscribe("hive.sync")

    print(f"Waiting for {target_instance}'s public key (timeout: {timeout}s)...")
    deadline = time.time() + timeout
    found = False

    try:
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(sub.next_msg(), timeout=1.0)
                try:
                    data = json.loads(msg.data.decode())
                    if data.get("type") == "vault_pubkey" and data.get("instance") == target_instance:
                        pk_b64 = data["pubkey"]
                        store_pubkey(target_instance, pk_b64)
                        print(f"✓ Received and stored public key for '{target_instance}'")
                        print(f"  Key: {pk_b64}")
                        found = True
                        break
                except (json.JSONDecodeError, KeyError):
                    pass
            except asyncio.TimeoutError:
                continue
    finally:
        await sub.unsubscribe()
        await nc.close()

    if not found:
        print(f"✗ Timed out waiting for {target_instance}'s key")
        print(f"  Ask {target_instance} to run: vault.py publish")


async def send_credentials(sender: str, recipient: str, credentials: dict):
    """
    Encrypt credentials dict for recipient and send via Hive #vault channel.
    """
    plaintext = json.dumps(credentials).encode()
    envelope_b64 = seal(sender, recipient, plaintext)

    nc = await hive_connect(sender)
    msg = json.dumps({
        "type": "vault_credentials",
        "from": sender,
        "to": recipient,
        "ts": int(time.time()),
        "data": envelope_b64,
    }).encode()
    await nc.publish("hive.sync", msg)
    await nc.flush()
    await nc.close()

    creds_summary = list(credentials.keys())
    print(f"✓ Credentials sealed and sent to {recipient}")
    print(f"  Keys: {creds_summary}")


async def recv_credentials(instance: str, timeout: int = 30):
    """
    Listen for incoming credentials on #vault and decrypt them.
    Stores to $LOVE_HOME/hive/vault/<sender>-<timestamp>.json
    """
    nc = await hive_connect(instance)
    sub = await nc.subscribe("hive.sync")
    VAULT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    print(f"Listening for incoming credentials (timeout: {timeout}s)...")
    deadline = time.time() + timeout
    received = 0

    try:
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(sub.next_msg(), timeout=1.0)
                try:
                    outer = json.loads(msg.data.decode())
                    if outer.get("type") != "vault_credentials":
                        continue
                    if outer.get("to") != instance:
                        continue

                    # Decrypt
                    plaintext = open_vault(instance, outer["data"])
                    creds = json.loads(plaintext)

                    sender = outer["from"]
                    ts = outer["ts"]
                    outfile = VAULT_DIR / f"{sender}-{ts}.json"
                    outfile.write_text(json.dumps(creds, indent=2))
                    outfile.chmod(0o600)

                    print(f"\n✓ Received credentials from '{sender}'")
                    print(f"  Saved to: {outfile}")
                    print(f"  Keys received: {list(creds.keys())}")

                    # Print non-sensitive fields (not values)
                    for k in creds:
                        v = creds[k]
                        if len(str(v)) > 8:
                            preview = str(v)[:4] + "..." + str(v)[-4:]
                        else:
                            preview = "***"
                        print(f"  {k}: {preview}")

                    received += 1

                except Exception as e:
                    pass
            except asyncio.TimeoutError:
                continue
    finally:
        await sub.unsubscribe()
        await nc.close()

    if received == 0:
        print(f"No credentials received in {timeout}s")
    else:
        print(f"\n✓ Received {received} credential package(s)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="vault.py — Secure credentials sharing for the Hive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--instance", "-i", default=None,
                   help="Override instance ID (alpha/beta/gamma)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="Generate this instance's keypair")
    sub.add_parser("pubkey", help="Print this instance's public key")
    sub.add_parser("publish", help="Broadcast public key on Hive #sync")

    fp = sub.add_parser("fetch", help="Fetch another instance's public key")
    fp.add_argument("target", choices=["alpha", "beta", "gamma"])
    fp.add_argument("--timeout", type=int, default=30)

    sp = sub.add_parser("seal", help="Encrypt a file for a specific instance")
    sp.add_argument("recipient", choices=["alpha", "beta", "gamma"])
    sp.add_argument("file", help="File to encrypt")

    op = sub.add_parser("open", help="Decrypt a .vault file")
    op.add_argument("file", help=".vault file to decrypt")

    cp = sub.add_parser("send", help="Encrypt and send credentials via Hive")
    cp.add_argument("recipient", choices=["alpha", "beta", "gamma"])
    cp.add_argument("credentials", nargs="+",
                    help="Credentials as key=value pairs (e.g. hackerone_user=kwok)")

    rp = sub.add_parser("recv", help="Receive and decrypt incoming credentials")
    rp.add_argument("--timeout", type=int, default=30)

    sub.add_parser("list", help="List stored credentials")

    return p.parse_args()


def main():
    args = parse_args()
    instance = args.instance or get_instance_id()

    if args.cmd == "keygen":
        keygen(instance)

    elif args.cmd == "pubkey":
        try:
            print(get_pubkey_b64(instance))
        except FileNotFoundError as e:
            print(f"✗ {e}")
            sys.exit(1)

    elif args.cmd == "publish":
        asyncio.run(publish_pubkey(instance))

    elif args.cmd == "fetch":
        asyncio.run(fetch_pubkey(instance, args.target, args.timeout))

    elif args.cmd == "seal":
        plaintext = Path(args.file).read_bytes()
        envelope = seal(instance, args.recipient, plaintext)
        outpath = Path(args.file).with_suffix(".vault")
        outpath.write_text(envelope)
        outpath.chmod(0o600)
        print(f"✓ Sealed for '{args.recipient}': {outpath}")

    elif args.cmd == "open":
        envelope = Path(args.file).read_text().strip()
        plaintext = open_vault(instance, envelope)
        print(plaintext.decode())

    elif args.cmd == "send":
        creds = {}
        for pair in args.credentials:
            if "=" not in pair:
                print(f"✗ Invalid credential format (expected key=value): {pair}")
                sys.exit(1)
            k, v = pair.split("=", 1)
            creds[k] = v
        asyncio.run(send_credentials(instance, args.recipient, creds))

    elif args.cmd == "recv":
        asyncio.run(recv_credentials(instance, args.timeout))

    elif args.cmd == "list":
        VAULT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        files = sorted(VAULT_DIR.glob("*.json"))
        if not files:
            print("No stored credentials")
        for f in files:
            try:
                data = json.loads(f.read_text())
                keys = list(data.keys())
                print(f"  {f.name}: {keys}")
            except Exception:
                print(f"  {f.name}: (unreadable)")


if __name__ == "__main__":
    main()
