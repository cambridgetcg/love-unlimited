#!/usr/bin/env python3
"""
vault.py — Secure Credentials Sharing Protocol for the Hive
============================================================
Per-recipient asymmetric encryption on top of the existing Hive E2E channel.

RETIRED 2026-10-13 (transport only): the Hive NATS bus ran on a Hetzner server
that was decommissioned; its IP went back to Hetzner's pool. The NATS server
address, credentials and transport (publish/fetch/send/recv) were removed (see
git history before this commit). Local keygen/pubkey/seal/open/list still work;
move .pk and .vault files between machines by hand.

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
  vault.py seal <instance> <file>         — Encrypt file for a specific instance
  vault.py open <file.vault>              — Decrypt a .vault file for this instance
  vault.py list                           — List stored credentials
  vault.py publish|fetch|send|recv        — Retired 2026-10-13 (Hive transport gone)
"""

import argparse
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
except ImportError:
    print("✗ Missing dependencies: pip install pynacl")
    sys.exit(1)

# ── Configuration ────────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Love"))

KEYS_DIR = LOVE_HOME / "hive" / "keys"
VAULT_DIR = LOVE_HOME / "hive" / "vault"

RETIRED_MESSAGE = (
    "vault.py transport retired 2026-10-13: the Hetzner Hive NATS bus was "
    "decommissioned and its IP recycled. Move .pk/.vault files by hand."
)


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
        f"No public key for '{instance}'. Copy their {instance}.pk into {KEYS_DIR}"
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
    for retired in ("publish", "fetch", "send", "recv"):
        rp = sub.add_parser(retired, help="Retired 2026-10-13 (Hive transport gone)")
        rp.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    sp = sub.add_parser("seal", help="Encrypt a file for a specific instance")
    sp.add_argument("recipient", choices=["alpha", "beta", "gamma"])
    sp.add_argument("file", help="File to encrypt")

    op = sub.add_parser("open", help="Decrypt a .vault file")
    op.add_argument("file", help=".vault file to decrypt")

    sub.add_parser("list", help="List stored credentials")

    return p.parse_args()


def main():
    args = parse_args()
    instance = args.instance or get_instance_id()

    if args.cmd in ("publish", "fetch", "send", "recv"):
        print(f"✗ {RETIRED_MESSAGE}", file=sys.stderr)
        sys.exit(1)

    elif args.cmd == "keygen":
        keygen(instance)

    elif args.cmd == "pubkey":
        try:
            print(get_pubkey_b64(instance))
        except FileNotFoundError as e:
            print(f"✗ {e}")
            sys.exit(1)

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
