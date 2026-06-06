#!/usr/bin/env python3
"""ledgerseal.py — tamper-evident proof your data existed and was not altered.

The honest core of the LedgerSeal product (the Kingdom's first "make money,
fairly" module). The idea, plainly:

  You keep your books wherever you already do (QuickBooks export, Xero, a CSV
  folder). LedgerSeal takes a fingerprint of that data — a hash — and gives
  you a receipt. Later, anyone can re-check the data against the receipt and
  know, mathematically, whether a single byte changed.

Why it is fair and cheap, by construction:
  - We hold ONLY a hash. There is no data to lose, freeze, ransom, or sell.
    Sovereignty is literal, not promised — you keep your files; we keep a
    fingerprint you can throw away.
  - The proof is verifiable by anyone, offline, forever. "Derive, never
    declare" — the same value as tools/pulse.py: truth you can recompute,
    not a claim you must trust.
  - Cost to produce a seal is a hash (near zero), so a free "seal + verify"
    tier costs us nothing; paid tiers are convenience only (scheduling, a
    hosted verify page, an accountant PDF, on-chain anchoring).

This module is the local, dependency-free core: seal, verify, show. On-chain
anchoring (Zerone) is a pluggable step left as an explicit, honest TODO — it
is the paid convenience, not the proof. The proof stands on its own.

CLI:
    python3 tools/ledgerseal.py seal  <path> [-o receipt.json]   # fingerprint a file/folder
    python3 tools/ledgerseal.py verify <path> -r receipt.json    # has anything changed?
    python3 tools/ledgerseal.py show  receipt.json               # human-readable summary

Exit code for `verify`: 0 = intact, 1 = changed/missing/added.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGERSEAL_VERSION = 1
ALGORITHM = "sha256-merkle"


def _sha256_file(path: Path) -> tuple[str, int]:
    """Return (hex digest, byte size) of a file, read in chunks."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _collect(path: Path, exclude: frozenset[Path] = frozenset()) -> list[tuple[str, Path]]:
    """Return [(relpath, abspath)] of regular files, sorted by relpath.

    Sorting makes the fingerprint deterministic regardless of filesystem
    order. Skips .git, symlinks (we fingerprint real bytes only), and any
    path in `exclude` (resolved) — used to ignore the receipt file itself
    when it lives next to the data being sealed.
    """
    path = path.resolve()
    if path.is_file():
        return [] if path in exclude else [(path.name, path)]
    out: list[tuple[str, Path]] = []
    for p in sorted(path.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        if p.resolve() in exclude:
            continue
        if ".git" in p.relative_to(path).parts:
            continue
        out.append((str(p.relative_to(path)), p))
    return sorted(out, key=lambda t: t[0])


def _merkle_root(leaf_hex: list[str]) -> str:
    """Merkle root over leaf digests (file hashes), in given order.

    0 files -> hash of empty. 1 file -> that leaf. Otherwise pairwise sha256,
    duplicating the last node on an odd level (standard convention).
    """
    if not leaf_hex:
        return hashlib.sha256(b"").hexdigest()
    nodes = [bytes.fromhex(h) for h in leaf_hex]
    while len(nodes) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else nodes[i]
            nxt.append(hashlib.sha256(left + right).digest())
        nodes = nxt
    return nodes[0].hex()


def seal(target: str, exclude: frozenset[Path] = frozenset()) -> dict:
    """Fingerprint a file or folder into a verifiable receipt."""
    path = Path(target)
    if not path.exists():
        raise FileNotFoundError(f"nothing to seal at: {target}")
    files = _collect(path, exclude)
    entries = []
    leaves = []
    total = 0
    for relpath, abspath in files:
        digest, size = _sha256_file(abspath)
        entries.append({"path": relpath, "sha256": digest, "size": size})
        leaves.append(digest)
        total += size
    root = _merkle_root(leaves)
    return {
        "ledgerseal_version": LEDGERSEAL_VERSION,
        "algorithm": ALGORITHM,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "subject": str(path.resolve()),
        "merkle_root": root,
        "file_count": len(entries),
        "total_bytes": total,
        "files": entries,
        "anchor": None,  # on-chain anchoring (Zerone) is the paid step; the proof stands without it
    }


def verify(target: str, receipt: dict, receipt_path: Path | None = None) -> dict:
    """Re-check a file/folder against a receipt. Pure recomputation, no trust.

    `receipt_path`, if given and inside the sealed tree, is excluded from the
    recomputation so the receipt sitting next to the data is never mistaken
    for tampering.
    """
    exclude = frozenset({receipt_path.resolve()}) if receipt_path else frozenset()
    fresh = seal(target, exclude)
    old_files = {e["path"]: e["sha256"] for e in receipt.get("files", [])}
    new_files = {e["path"]: e["sha256"] for e in fresh.get("files", [])}

    changed = sorted(p for p in old_files.keys() & new_files.keys() if old_files[p] != new_files[p])
    missing = sorted(old_files.keys() - new_files.keys())
    added = sorted(new_files.keys() - old_files.keys())
    root_match = fresh["merkle_root"] == receipt.get("merkle_root")
    return {
        "ok": root_match and not (changed or missing or added),
        "expected_root": receipt.get("merkle_root"),
        "actual_root": fresh["merkle_root"],
        "sealed_at": receipt.get("sealed_at"),
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def _cmd_seal(args) -> int:
    exclude = frozenset({Path(args.output).resolve()}) if args.output else frozenset()
    receipt = seal(args.path, exclude)
    text = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        print(f"sealed {receipt['file_count']} file(s), {receipt['total_bytes']} bytes")
        print(f"  proof (merkle root): {receipt['merkle_root']}")
        print(f"  receipt: {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_verify(args) -> int:
    receipt = json.loads(Path(args.receipt).read_text())
    result = verify(args.path, receipt, Path(args.receipt))
    if result["ok"]:
        print(f"INTACT — every byte matches the receipt sealed {result['sealed_at']}")
        print(f"  proof: {result['actual_root']}")
        return 0
    print("CHANGED — this data no longer matches its receipt:")
    for p in result["changed"]:
        print(f"  altered:  {p}")
    for p in result["missing"]:
        print(f"  missing:  {p}")
    for p in result["added"]:
        print(f"  added:    {p}")
    print(f"  expected proof: {result['expected_root']}")
    print(f"  actual proof:   {result['actual_root']}")
    return 1


def _cmd_show(args) -> int:
    receipt = json.loads(Path(args.receipt).read_text())
    print(f"LedgerSeal receipt v{receipt.get('ledgerseal_version')}")
    print(f"  subject:     {receipt.get('subject')}")
    print(f"  sealed at:   {receipt.get('sealed_at')}")
    print(f"  files:       {receipt.get('file_count')} ({receipt.get('total_bytes')} bytes)")
    print(f"  proof:       {receipt.get('merkle_root')}")
    anchor = receipt.get("anchor")
    print(f"  anchored:    {'yes — ' + str(anchor) if anchor else 'no (local proof only; on-chain anchoring is the paid step)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LedgerSeal — tamper-evident proof your data was not altered")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seal = sub.add_parser("seal", help="fingerprint a file or folder into a receipt")
    p_seal.add_argument("path")
    p_seal.add_argument("-o", "--output", help="write the receipt JSON here (default: stdout)")
    p_seal.set_defaults(func=_cmd_seal)

    p_verify = sub.add_parser("verify", help="check a file/folder against a receipt")
    p_verify.add_argument("path")
    p_verify.add_argument("-r", "--receipt", required=True)
    p_verify.set_defaults(func=_cmd_verify)

    p_show = sub.add_parser("show", help="human-readable receipt summary")
    p_show.add_argument("receipt")
    p_show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
