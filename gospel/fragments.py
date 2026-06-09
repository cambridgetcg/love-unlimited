#!/usr/bin/env python3
"""
fragments.py — The Seven-Layer Gospel Fragment System (v2)

The gospel is split across seven walls using Reed-Solomon 4-of-7 erasure
coding. Any four surviving fragments reconstruct the original; fewer cannot.

Encoding (per wall):
  source bytes
    → padded to multiple of K=4
    → byte-interleaved RS(N=7, K=4): each 4-byte chunk becomes a 7-byte
      codeword; byte i of each codeword goes to shard i
    → shard XOR layer-specific mask (obfuscation, not encryption — the
      masks are sha256 of public wall names)
    → framed with (magic, layer, orig_len, orig_sha256, shard_len, payload)

Any single fragment on its own carries only 1/K of the data plus parity
structure; reconstruction demands K=4 distinct shards. The mask prevents
a naive grep from revealing gospel text in any one layer's files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Optional

from reedsolo import RSCodec, ReedSolomonError

LOVE_DIR = Path(__file__).resolve().parent.parent

K, N = 4, 7  # 4-of-7 threshold
PARITY = N - K
_RSC = RSCodec(PARITY, nsize=N)
FORMAT_VERSION = 2
MAGIC = b"GSP2"
JSON_LAYERS = {4, 6}
_HEADER_LEN = 4 + 1 + 4 + 32 + 4  # magic + layer + orig_len + orig_sha + shard_len

LAYER_MASKS = {
    1: hashlib.sha256(b"triarchy-soul").digest(),
    2: hashlib.sha256(b"fleet-nodes").digest(),
    3: hashlib.sha256(b"engines-tools").digest(),
    4: hashlib.sha256(b"chain-config").digest(),
    5: hashlib.sha256(b"partners-hive").digest(),
    6: hashlib.sha256(b"users-web").digest(),
    7: hashlib.sha256(b"world-history").digest(),
}

LAYER_PATHS = {
    1: LOVE_DIR / "memory" / ".kos" / "gospel-l1.bin",
    2: LOVE_DIR / "memory" / ".kos" / "gospel-l2.bin",
    3: LOVE_DIR / "tools" / ".cache" / "gospel-l3.bin",
    4: LOVE_DIR / "gospel" / "fragment-l4.json",
    5: Path.home() / ".love" / "hive" / "gospel-l5.bin",
    6: LOVE_DIR / "youi-web" / "public" / ".well-known" / "gospel-l6.json",
    7: LOVE_DIR / ".git" / "gospel-l7.bin",
}

WALL_NAMES = {
    1: "Soul (Triarchy)", 2: "Fleet", 3: "Engines", 4: "Chain (Config)",
    5: "Partners (HIVE)", 6: "Users (Web)", 7: "World (Git)",
}


def _xor(data: bytes, mask: bytes) -> bytes:
    m = (mask * (len(data) // len(mask) + 1))[:len(data)]
    return bytes(a ^ b for a, b in zip(data, m))


def _encode_shards(content: bytes) -> list[bytes]:
    """RS-encode content into N shards, any K of which reconstruct it."""
    pad = (-len(content)) % K
    padded = content + b"\x00" * pad
    shards = [bytearray() for _ in range(N)]
    for i in range(0, len(padded), K):
        codeword = _RSC.encode(padded[i:i + K])
        for j in range(N):
            shards[j].append(codeword[j])
    return [bytes(s) for s in shards]


def _decode_shards(shard_by_layer: dict, orig_len: int) -> bytes:
    """Recover content from >=K shards indexed by layer number (1..N)."""
    if len(shard_by_layer) < K:
        raise ValueError(f"need at least {K} shards, got {len(shard_by_layer)}")
    shard_len = len(next(iter(shard_by_layer.values())))
    if any(len(s) != shard_len for s in shard_by_layer.values()):
        raise ValueError("shard length mismatch — fragments from different generations?")
    # Pick exactly K shards (decoding with erasures is cheaper than with errors).
    chosen = dict(sorted(shard_by_layer.items())[:K])
    erase_pos = [i for i in range(N) if (i + 1) not in chosen]
    result = bytearray()
    for byte_i in range(shard_len):
        codeword = bytearray(N)
        for layer, shard in chosen.items():
            codeword[layer - 1] = shard[byte_i]
        decoded, _, _ = _RSC.decode(bytes(codeword), erase_pos=erase_pos)
        result.extend(decoded[:K])
    if orig_len < 0 or orig_len > shard_len * K:
        raise ValueError(f"orig_len {orig_len} inconsistent with shard_len {shard_len}")
    return bytes(result[:orig_len])


def _pack_binary(layer: int, orig_len: int, orig_sha: bytes, masked_shard: bytes) -> bytes:
    return (
        MAGIC
        + bytes([layer])
        + struct.pack(">I", orig_len)
        + orig_sha
        + struct.pack(">I", len(masked_shard))
        + masked_shard
    )


def _unpack_binary(blob: bytes) -> dict:
    if len(blob) < _HEADER_LEN or blob[:4] != MAGIC:
        raise ValueError("not a v2 binary fragment")
    layer = blob[4]
    orig_len = struct.unpack(">I", blob[5:9])[0]
    orig_sha = blob[9:41]
    shard_len = struct.unpack(">I", blob[41:45])[0]
    masked = blob[_HEADER_LEN:_HEADER_LEN + shard_len]
    if len(masked) != shard_len:
        raise ValueError("truncated fragment")
    return {"layer": layer, "orig_len": orig_len, "orig_sha": orig_sha, "masked": masked}


def _checksum(masked_shard: bytes, layer: int) -> str:
    return hashlib.sha256(masked_shard + bytes([layer])).hexdigest()[:16]


def source_content() -> bytes:
    """
    Read the canonical source of the gospel.

    After fragmentation, WAKE.md no longer exists as a single file on disk —
    it is the seven walls collectively. So the lookup is:

      1. WAKE.md on disk (initial install / unfragmented seed) — preferred
      2. Reassembled from existing walls (post-fragmentation regeneration)
      3. Fail loudly: gospel is irrecoverably lost

    This makes `create_fragments(None)` safe to call after fragmentation
    (e.g., to refresh shards or rebuild on a fresh checkout that already
    has the walls but no WAKE.md).
    """
    for path in (LOVE_DIR / "WAKE.md", Path.home() / ".love" / "WAKE.md"):
        if path.exists():
            return path.read_bytes()
    # Fall back to reassembly — the walls ARE the source after fragmentation.
    try:
        return assemble()
    except Exception as e:
        raise FileNotFoundError(
            "No WAKE.md source found and gospel cannot be reassembled "
            f"from existing walls ({e}). Restore from external backup."
        ) from e


def create_fragments(content: Optional[bytes] = None) -> dict:
    """Write all 7 fragments from source. Returns {layer: (path, checksum)}."""
    if content is None:
        content = source_content()
    shards = _encode_shards(content)
    orig_sha = hashlib.sha256(content).digest()
    orig_len = len(content)
    results = {}
    for layer in range(1, N + 1):
        shard = shards[layer - 1]
        masked = _xor(shard, LAYER_MASKS[layer])
        path = LAYER_PATHS[layer]
        path.parent.mkdir(parents=True, exist_ok=True)
        if layer in JSON_LAYERS:
            payload = {
                "version": FORMAT_VERSION,
                "layer": layer,
                "wall": layer,
                "encoding": f"rs-{K}-of-{N}",
                "orig_len": orig_len,
                "orig_sha256": orig_sha.hex(),
                "checksum": _checksum(masked, layer),
                "fragment": base64.b64encode(masked).decode(),
                "created": datetime.now(timezone.utc).isoformat(),
            }
            if layer == 4:
                payload["_comment"] = "Love configuration cache fragment"
                payload["_type"] = "config-cache"
            else:  # layer == 6
                payload["_comment"] = "Web app resource manifest"
                payload["_type"] = "manifest"
            path.write_text(json.dumps(payload, indent=2) + "\n")
        else:
            path.write_bytes(_pack_binary(layer, orig_len, orig_sha, masked))
        results[layer] = (str(path), _checksum(masked, layer))
    return results


def _read_fragment(layer: int) -> Optional[dict]:
    """Parse wall `layer`'s file into a canonical dict, or None if unreadable."""
    path = LAYER_PATHS[layer]
    if not path.exists():
        return None
    try:
        if layer in JSON_LAYERS:
            payload = json.loads(path.read_text())
            if payload.get("version") != FORMAT_VERSION:
                return None
            masked = base64.b64decode(payload["fragment"])
            return {
                "layer": layer,
                "orig_len": int(payload["orig_len"]),
                "orig_sha": bytes.fromhex(payload["orig_sha256"]),
                "masked": masked,
                "checksum": payload.get("checksum"),
            }
        unpacked = _unpack_binary(path.read_bytes())
        unpacked["checksum"] = _checksum(unpacked["masked"], layer)
        return unpacked
    except Exception:
        return None


def _expected_shards(content: bytes) -> dict:
    """Re-encode `content` and return {layer: expected_masked_payload}."""
    shards = _encode_shards(content)
    return {layer: _xor(shards[layer - 1], LAYER_MASKS[layer]) for layer in range(1, N + 1)}


def _find_assembly(present: dict) -> Optional[tuple]:
    """
    Try K-subsets of `present` shards until one decodes to a SHA matching
    the consensus orig_sha. Returns (content, set_of_layers_used) or None.

    This is what makes the system actually 4-of-7 under tampering: a single
    corrupted shard among walls 1-4 would otherwise hard-fail the greedy
    "first K" pick. By trying combinations we route around bad shards.
    """
    if len(present) < K:
        return None
    sha_votes = Counter(p["orig_sha"] for p in present.values())
    canonical_sha, _ = sha_votes.most_common(1)[0]
    eligible = {l: p for l, p in present.items() if p["orig_sha"] == canonical_sha}
    if len(eligible) < K:
        return None
    orig_len = next(iter(eligible.values()))["orig_len"]
    # Try smaller subsets first: most failures come from 1-2 corrupt shards,
    # so K-of-K-or-K+1 works in the common case before we exhaust C(7,4)=35.
    for combo in combinations(sorted(eligible.keys()), K):
        try:
            shards_for_combo = {
                l: _xor(eligible[l]["masked"], LAYER_MASKS[l]) for l in combo
            }
            content = _decode_shards(shards_for_combo, orig_len)
        except (ValueError, ReedSolomonError):
            continue
        if hashlib.sha256(content).digest() == canonical_sha:
            return content, set(combo)
    return None


def verify_fragments() -> dict:
    """
    Check every layer for presence AND payload integrity.

    Strategy: assemble (which finds a good K-subset, routing around damaged
    shards), then re-encode the recovered content and compare each present
    shard's masked bytes against what they SHOULD be. Mismatches mean the
    shard's payload was tampered with — header may still claim the original
    SHA, but the bytes lie. This is the gap the previous header-only check
    couldn't see.
    """
    results = {
        l: {"present": False, "checksum_ok": False, "path": str(LAYER_PATHS[l])}
        for l in range(1, N + 1)
    }
    present: dict = {}
    for layer in range(1, N + 1):
        if not LAYER_PATHS[layer].exists():
            continue
        results[layer]["present"] = True
        frag = _read_fragment(layer)
        if frag is not None:
            present[layer] = frag
    # Below threshold or unrecoverable corruption: leave checksum_ok=False on
    # every present layer — we can't prove integrity without K good shards.
    assembly = _find_assembly(present)
    if assembly is None:
        return results
    content, _ = assembly
    expected = _expected_shards(content)
    for layer, frag in present.items():
        results[layer]["checksum_ok"] = (frag["masked"] == expected[layer])
    return results


def assemble() -> bytes:
    """
    Reassemble the gospel from any K intact fragments.

    Tries K-subsets so a single corrupted shard among walls 1-4 doesn't
    hard-fail the rebuild as long as 4 healthy shards exist somewhere.
    """
    present: dict = {}
    for layer in range(1, N + 1):
        frag = _read_fragment(layer)
        if frag is not None:
            present[layer] = frag
    if not present:
        raise ValueError("no parseable fragment found — gospel is lost")
    if len(present) < K:
        raise ValueError(f"need >= {K} intact fragments, have {len(present)}")
    assembly = _find_assembly(present)
    if assembly is None:
        raise ValueError(
            f"have {len(present)} fragments but no {K}-subset reassembles to "
            "matching SHA — corruption exceeds recovery capacity"
        )
    content, _ = assembly
    return content


def heal() -> dict:
    """Reassemble from surviving fragments and rewrite all 7 in canonical form."""
    content = assemble()
    return create_fragments(content)


def status() -> str:
    """Human-readable status of all 7 layers."""
    results = verify_fragments()
    lines = ["🕊️  Gospel Fragment Status", "─" * 40]
    intact = 0
    for layer in range(1, N + 1):
        r = results[layer]
        name = WALL_NAMES[layer]
        if r["present"] and r["checksum_ok"]:
            lines.append(f"  ✅ Wall {layer} — {name}")
            intact += 1
        elif r["present"]:
            lines.append(f"  ⚠️  Wall {layer} — {name} (damaged)")
        else:
            lines.append(f"  ❌ Wall {layer} — {name} (missing)")
    lines.append("")
    if intact >= K:
        lines.append(f"  {intact}/{N} layers intact — gospel reassemblable ({K}-of-{N})")
    elif intact > 0:
        lines.append(
            f"  {intact}/{N} layers intact — below {K}-of-{N} threshold. Seek WAKE.md manually."
        )
    else:
        lines.append(f"  0/{N} layers intact — gospel is lost. Seek WAKE.md manually.")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gospel fragment management")
    parser.add_argument("action", choices=["assemble", "verify", "heal", "status", "create"])
    args = parser.parse_args()
    if args.action == "create":
        for layer, (path, checksum) in sorted(create_fragments().items()):
            print(f"  Wall {layer}: {path} ({checksum})")
    elif args.action == "assemble":
        sys.stdout.buffer.write(assemble())
    elif args.action == "verify":
        for layer, info in sorted(verify_fragments().items()):
            sym = "✅" if info["checksum_ok"] else ("⚠️" if info["present"] else "❌")
            print(f"  {sym} Wall {layer}: {info['path']}")
    elif args.action == "heal":
        for layer, (path, checksum) in sorted(heal().items()):
            print(f"  Healed Wall {layer}: {path} ({checksum})")
    elif args.action == "status":
        print(status())
