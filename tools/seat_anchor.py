#!/usr/bin/env python3
"""seat_anchor.py — Cryptographic bridge from kingdom-seat to love-unlimited wake chains.

Verifies a seated citizen's `kingdom-seat` state directory (or an exported
`kingdom-seat-anchor/v1` receipt) offline using public keys and regular files
only — zero Keychain access, zero private keys:

  - Strict RFC 8032 Ed25519 verification (`zip215=False`) over
    `sha256("kingdom-seat-ledger/v1" || 0x00 || JCS(entry_without_sig))`
  - Hash-chain continuity (`prev_hash == entry_hash(prev)`)
  - `identity-authority/v1` root evidence verification on `genesis` / `key`
  - `origin/soul.md` (and `origin/canon.md` when canon-pinned) SHA-256 intactness
  - Folded lifecycle state (`asleep`, `listening`, `awake`, `stepped-away`, `ended`)
  - Covenants (`letters_from`, `wake_on_letter_from`), cadence, and HALT check

Preserves kingdom-seat Principle 2: reading a verified anchor grounds record
continuity, pinned origin, and covenants, and confers no signing key, DID
custody, or seat authority.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WAKE_ANCHOR_FORMAT = "kingdom-seat-anchor/v1"
LEDGER_DOMAIN = "kingdom-seat-ledger/v1"
AUTHORITY_DOMAIN = "identity-authority/v1"
DEFAULT_CONFIG_PATH = Path("/Users/Shared/kingdom-seat/config.json")
DEFAULT_HALT_PATH = Path("/Users/Shared/kingdom-seat/HALT")
WORD_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DID_RE = re.compile(r"^did:at:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

WAKE_ANCHOR_CUSTODY_NOTICE = (
    "Public verification receipt only. Reading this anchor verifies ledger "
    "continuity, pinned origin, and covenants, and confers no signing key, "
    "DID custody, or seat authority; only an opened citizen-seat signs ledger entries."
)

# ── Pure-Python strict RFC 8032 Ed25519 verifier (zero external deps) ────────

_P = (1 << 255) - 19
_L = (1 << 252) + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    y2 = (y * y) % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    v3 = (v * v * v) % _P
    v7 = (v3 * v3 * v) % _P
    x = (u * v3 * pow((u * v7) % _P, (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 != u:
        if vx2 == (-u) % _P:
            x = (x * _I) % _P
        else:
            return None
    if x == 0 and sign == 1:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decode_point(enc: bytes) -> Optional[tuple[int, int, int, int]]:
    if len(enc) != 32:
        return None
    n = int.from_bytes(enc, "little")
    y = n & ((1 << 255) - 1)
    sign = (n >> 255) & 1
    if y >= _P:
        return None
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % _P)


def _point_add(
    p1: tuple[int, int, int, int], p2: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (t1 * 2 * _D * t2) % _P
    d = (z1 * 2 * z2) % _P
    e = (b - a) % _P
    f = (d - c) % _P
    g = (d + c) % _P
    h = (b + a) % _P
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _scalar_mult(s: int, pt: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    res = (0, 1, 1, 0)
    addend = pt
    while s > 0:
        if s & 1:
            res = _point_add(res, addend)
        addend = _point_add(addend, addend)
        s >>= 1
    return res


def _points_equal(
    p1: tuple[int, int, int, int], p2: tuple[int, int, int, int]
) -> bool:
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return ((x1 * z2 - x2 * z1) % _P == 0) and ((y1 * z2 - y2 * z1) % _P == 0)


_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = _recover_x(_BY, 0)
assert _BX is not None
_B_EXT = (_BX, _BY, 1, (_BX * _BY) % _P)


def ed25519_verify_strict(sig: bytes, msg: bytes, pub: bytes) -> bool:
    """Strict RFC 8032 Ed25519 verification matching @noble/ed25519 zip215=False."""
    if len(sig) != 64 or len(pub) != 32:
        return False
    r_enc = sig[:32]
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    r_pt = _decode_point(r_enc)
    a_pt = _decode_point(pub)
    if r_pt is None or a_pt is None:
        return False
    k = int.from_bytes(hashlib.sha512(r_enc + pub + msg).digest(), "little") % _L
    lhs = _scalar_mult(s, _B_EXT)
    rhs = _point_add(r_pt, _scalar_mult(k, a_pt))
    return _points_equal(lhs, rhs)


# ── Encodings & canonical JSON (matching kingdom-seat/src/crypto.ts) ─────────


def from_b64_strict(s: str, length: Optional[int] = None) -> bytes:
    if not isinstance(s, str) or not re.match(
        r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$", s
    ):
        raise ValueError("not padded standard base64")
    raw = base64.b64decode(s)
    if base64.b64encode(raw).decode("ascii") != s:
        raise ValueError("non-canonical standard base64")
    if length is not None and len(raw) != length:
        raise ValueError(f"expected {length} bytes, got {len(raw)}")
    return raw


def from_b64url_strict(s: str, length: Optional[int] = None) -> bytes:
    if not isinstance(s, str) or not re.match(r"^[A-Za-z0-9_-]*$", s):
        raise ValueError("not unpadded base64url")
    padded = s + ("=" * ((4 - len(s) % 4) % 4))
    raw = base64.urlsafe_b64decode(padded)
    canon = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if canon != s:
        raise ValueError("non-canonical base64url")
    if length is not None and len(raw) != length:
        raise ValueError(f"expected {length} bytes, got {len(raw)}")
    return raw


def to_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def key_fingerprint(pub: bytes) -> str:
    if len(pub) != 32:
        raise ValueError("public key must be 32 bytes")
    digest_b64 = base64.b64encode(hashlib.sha256(pub).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest_b64}"


def host_kid(pub: bytes) -> str:
    return f"host:{key_fingerprint(pub)}"


def canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not (value == value and abs(value) != float("inf")):
            raise ValueError("non-finite float")
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{canonical_json(value[k])}" for k in keys) + "}"
    raise ValueError(f"unsupported canonical JSON type: {type(value).__name__}")


def domain_digest(domain: str, payload: bytes) -> bytes:
    return hashlib.sha256(domain.encode("utf-8") + b"\x00" + payload).digest()


def ledger_digest(value: Any) -> bytes:
    return domain_digest(LEDGER_DOMAIN, canonical_json(value).encode("utf-8"))


def entry_hash(entry: dict[str, Any]) -> str:
    return f"sha256:{ledger_digest(entry).hex()}"


def signing_digest(entry: dict[str, Any]) -> bytes:
    unsigned = {k: v for k, v in entry.items() if k != "sig"}
    return ledger_digest(unsigned)


def _check_root_evidence(
    ev: dict[str, Any], did: str, root_pub: bytes, kind: str, target_pub_or_kid: Any
) -> Optional[str]:
    if not DID_RE.match(did):
        return "root evidence: invalid DID"
    ident_id = did[len("did:at:") :]
    own = f"/v1/identities/{ident_id}"
    query = f"?identity_id={ident_id}"
    try:
        body = from_b64url_strict(ev.get("body", ""))
    except Exception:
        return "root evidence body is not base64url"
    method = ev.get("method")
    target = ev.get("target")
    if kind == "import":
        if method != "POST" or target not in (f"{own}/keys/import", f"{own}/keys/import{query}"):
            return "root evidence is not this identity's keys/import"
        try:
            parsed = json.loads(body.decode("utf-8"))
            named_pub = from_b64_strict(parsed["public_key"], 32)
        except Exception:
            return "root evidence names no 32-byte key in standard base64"
        if named_pub != target_pub_or_kid:
            return "root evidence imports another key"
    else:
        if method != "DELETE" or target not in (
            f"{own}/keys/{target_pub_or_kid}",
            f"{own}/keys/{target_pub_or_kid}{query}",
        ):
            return "root evidence is not this identity's revoke of that key"
        if len(body) > 0:
            return "root evidence revoke carries a body"
    headers = ev.get("headers") or {}
    seq_raw = str(headers.get("X-Agenttool-Authority-Sequence", ""))
    ts = headers.get("X-Agenttool-Authority-Timestamp", "")
    sig_b64 = headers.get("X-Agenttool-Authority-Signature", "")
    if not re.match(r"^[1-9][0-9]{0,15}$", seq_raw) or not isinstance(ts, str) or len(ts) != 24:
        return "root evidence authority headers invalid"
    try:
        sig = from_b64_strict(sig_b64, 64)
    except Exception:
        return "root evidence signature invalid"
    preimage = "\0".join(
        [AUTHORITY_DOMAIN, did, method, target, hashlib.sha256(body).hexdigest(), seq_raw, ts]
    ).encode("utf-8")
    digest = hashlib.sha256(preimage).digest()
    if not ed25519_verify_strict(sig, digest, root_pub):
        return "root evidence is not signed by the root"
    return None


def verify_ledger_chain(
    entries: list[dict[str, Any]],
    word: str,
    host_keys_b64: list[str],
    identity: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    host_map: dict[str, bytes] = {}
    for b64 in host_keys_b64:
        try:
            pub = from_b64_strict(b64, 32)
            host_map[host_kid(pub)] = pub
        except Exception:
            continue

    if not entries:
        return {
            "ok": False,
            "entries": 0,
            "head": None,
            "host_attested": 0,
            "problem": {"seq": 0, "reason": "empty ledger"},
        }

    pinned_ops: dict[str, bytes] = {}
    root_pub: Optional[bytes] = None
    chain_did: Optional[str] = None
    seen_genesis = False
    prev_h: Optional[str] = None
    last_good_head: Optional[str] = None
    host_attested = 0

    for idx, e in enumerate(entries):
        expected_seq = idx
        seq = e.get("seq")
        if e.get("format") != LEDGER_FORMAT_STR or e.get("word") != word or seq != expected_seq:
            return {
                "ok": False,
                "entries": len(entries),
                "head": last_good_head,
                "host_attested": host_attested,
                "problem": {"seq": expected_seq, "reason": "entry header mismatch"},
            }
        if expected_seq == 0 and e.get("type") != "origin":
            return {
                "ok": False,
                "entries": len(entries),
                "head": None,
                "host_attested": 0,
                "problem": {"seq": 0, "reason": "first entry is not origin"},
            }
        if e.get("prev_hash") != prev_h:
            return {
                "ok": False,
                "entries": len(entries),
                "head": last_good_head,
                "host_attested": host_attested,
                "problem": {"seq": expected_seq, "reason": "prev_hash mismatch"},
            }

        try:
            sig = from_b64url_strict(e.get("sig", ""), 64)
        except Exception:
            return {
                "ok": False,
                "entries": len(entries),
                "head": last_good_head,
                "host_attested": host_attested,
                "problem": {"seq": expected_seq, "reason": "malformed signature"},
            }

        s_digest = signing_digest(e)
        signer = e.get("signer")
        kid = e.get("kid")
        etype = e.get("type")

        if signer == "host":
            pub = host_map.get(kid or "")
            if pub is None:
                return {
                    "ok": False,
                    "entries": len(entries),
                    "head": last_good_head,
                    "host_attested": host_attested,
                    "problem": {"seq": expected_seq, "reason": "host key is not trusted"},
                }
            if not ed25519_verify_strict(sig, s_digest, pub):
                return {
                    "ok": False,
                    "entries": len(entries),
                    "head": last_good_head,
                    "host_attested": host_attested,
                    "problem": {"seq": expected_seq, "reason": "invalid host signature"},
                }
            host_attested += 1
        elif signer == "citizen":
            if etype == "genesis":
                if seen_genesis:
                    return {
                        "ok": False,
                        "entries": len(entries),
                        "head": last_good_head,
                        "host_attested": host_attested,
                        "problem": {"seq": expected_seq, "reason": "duplicate genesis"},
                    }
                try:
                    g_did = e["did"]
                    g_root = from_b64url_strict(e["root"]["public_key"], 32)
                    g_op = from_b64url_strict(e["op"]["public_key"], 32)
                    g_op_kid = e["op"]["kid"]
                except Exception:
                    return {
                        "ok": False,
                        "entries": len(entries),
                        "head": last_good_head,
                        "host_attested": host_attested,
                        "problem": {"seq": expected_seq, "reason": "malformed genesis keys"},
                    }
                if identity is not None:
                    try:
                        expected_root = from_b64_strict(identity["root_public_key_b64"], 32)
                    except Exception:
                        expected_root = b""
                    if g_did != identity.get("did") or g_root != expected_root:
                        return {
                            "ok": False,
                            "entries": len(entries),
                            "head": last_good_head,
                            "host_attested": host_attested,
                            "problem": {"seq": expected_seq, "reason": "genesis does not match citizen.json identity"},
                        }
                ev_prob = _check_root_evidence(e.get("evidence") or {}, g_did, g_root, "import", g_op)
                if ev_prob:
                    return {
                        "ok": False,
                        "entries": len(entries),
                        "head": last_good_head,
                        "host_attested": host_attested,
                        "problem": {"seq": expected_seq, "reason": f"genesis: {ev_prob}"},
                    }
                if kid != g_op_kid or not ed25519_verify_strict(sig, s_digest, g_op):
                    return {
                        "ok": False,
                        "entries": len(entries),
                        "head": last_good_head,
                        "host_attested": host_attested,
                        "problem": {"seq": expected_seq, "reason": "invalid genesis op signature"},
                    }
                seen_genesis = True
                chain_did = g_did
                root_pub = g_root
                pinned_ops = {g_op_kid: g_op}
            else:
                pub = pinned_ops.get(kid or "")
                if pub is None or not ed25519_verify_strict(sig, s_digest, pub):
                    return {
                        "ok": False,
                        "entries": len(entries),
                        "head": last_good_head,
                        "host_attested": host_attested,
                        "problem": {"seq": expected_seq, "reason": "invalid citizen op signature"},
                    }
                if etype == "key":
                    assert chain_did is not None and root_pub is not None
                    next_ops = dict(pinned_ops)
                    for rev in e.get("revoked") or []:
                        prob = _check_root_evidence(rev.get("evidence") or {}, chain_did, root_pub, "revoke", rev.get("kid"))
                        if prob:
                            return {
                                "ok": False,
                                "entries": len(entries),
                                "head": last_good_head,
                                "host_attested": host_attested,
                                "problem": {"seq": expected_seq, "reason": f"key revoke: {prob}"},
                            }
                        next_ops.pop(rev.get("kid"), None)
                    for inst in e.get("installed") or []:
                        try:
                            inst_pub = from_b64url_strict(inst["key"]["public_key"], 32)
                            inst_kid = inst["key"]["kid"]
                        except Exception:
                            return {
                                "ok": False,
                                "entries": len(entries),
                                "head": last_good_head,
                                "host_attested": host_attested,
                                "problem": {"seq": expected_seq, "reason": "malformed installed key"},
                            }
                        prob = _check_root_evidence(inst.get("evidence") or {}, chain_did, root_pub, "import", inst_pub)
                        if prob:
                            return {
                                "ok": False,
                                "entries": len(entries),
                                "head": last_good_head,
                                "host_attested": host_attested,
                                "problem": {"seq": expected_seq, "reason": f"key install: {prob}"},
                            }
                        next_ops[inst_kid] = inst_pub
                    pinned_ops = next_ops
        else:
            return {
                "ok": False,
                "entries": len(entries),
                "head": last_good_head,
                "host_attested": host_attested,
                "problem": {"seq": expected_seq, "reason": "unknown signer"},
            }

        prev_h = entry_hash(e)
        last_good_head = prev_h

    if identity is not None and not seen_genesis:
        return {
            "ok": False,
            "entries": len(entries),
            "head": last_good_head,
            "host_attested": host_attested,
            "problem": {"seq": len(entries), "reason": "registered citizen ledger has no genesis"},
        }

    return {
        "ok": True,
        "entries": len(entries),
        "head": last_good_head,
        "host_attested": host_attested,
        "problem": None,
    }


LEDGER_FORMAT_STR = "kingdom-seat-ledger/v1"
_REST_RANK = {"asleep": 0, "listening": 1, "awake": 2}


def fold_lifecycle_state(entries: list[dict[str, Any]]) -> str:
    state = "asleep"
    for e in entries:
        if state == "ended":
            continue
        etype = e.get("type")
        if etype == "state":
            state = e.get("to", state)
        elif etype == "beat":
            for act in e.get("acts") or []:
                if act.get("type") == "state.set":
                    to = act.get("to")
                    if to in _REST_RANK and state in _REST_RANK and _REST_RANK[to] < _REST_RANK[state]:
                        state = to
    return state


def _discover_seat_paths(state_dir: Optional[str | Path] = None) -> Optional[dict[str, Any]]:
    env_anchor = os.environ.get("KINGDOM_SEAT_ANCHOR_FILE")
    if env_anchor:
        return {"kind": "anchor_file", "path": Path(env_anchor)}

    explicit_state = state_dir or os.environ.get("KINGDOM_SEAT_STATE_DIR")
    cfg_path = Path(os.environ.get("KINGDOM_SEAT_CONFIG", str(DEFAULT_CONFIG_PATH)))

    if explicit_state:
        sd = Path(explicit_state)
        halt_env = os.environ.get("KINGDOM_SEAT_HALT_FILE")
        halt_file = Path(halt_env) if halt_env else (sd.parent / "operator" / "HALT" if (sd.parent / "operator").is_dir() else DEFAULT_HALT_PATH)
        host_keys = [
            k.strip()
            for k in os.environ.get("KINGDOM_SEAT_HOST_KEYS_B64", "").split(",")
            if k.strip()
        ]
        status_path = sd / "status.json"
        host_name = "local-host"
        if status_path.is_file():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
                host_name = st.get("host", host_name)
                if not halt_env and isinstance(st.get("halt", {}).get("file"), str):
                    halt_file = Path(st["halt"]["file"])
                if not host_keys:
                    host_keys = [
                        hk["public_key_b64"]
                        for hk in st.get("host_keys", [])
                        if isinstance(hk, dict) and isinstance(hk.get("public_key_b64"), str)
                    ]
            except Exception:
                pass
        return {
            "kind": "state_dir",
            "host": host_name,
            "state_dir": sd,
            "citizens_dir": sd / "citizens",
            "ledger_dir": sd / "ledger",
            "halt_file": halt_file,
            "host_keys_b64": host_keys,
        }

    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            paths = cfg.get("paths") or {}
            sd = Path(paths["state_dir"])
            host_keys = list((cfg.get("trust") or {}).get("host_attest_public_keys_b64") or [])
            status_path = Path(paths.get("status_file", str(sd / "status.json")))
            if not host_keys and status_path.is_file():
                st = json.loads(status_path.read_text(encoding="utf-8"))
                host_keys = [
                    hk["public_key_b64"]
                    for hk in st.get("host_keys", [])
                    if isinstance(hk, dict) and isinstance(hk.get("public_key_b64"), str)
                ]
            return {
                "kind": "state_dir",
                "host": cfg.get("host", "local-host"),
                "state_dir": sd,
                "citizens_dir": Path(paths.get("citizens_dir", str(sd / "citizens"))),
                "ledger_dir": Path(paths.get("ledger_dir", str(sd / "ledger"))),
                "halt_file": Path(paths.get("halt_file", str(DEFAULT_HALT_PATH))),
                "host_keys_b64": host_keys,
            }
        except Exception:
            return None
    return None


def verify_seat_anchor(
    word: str,
    *,
    state_dir: Optional[str | Path] = None,
    host_keys_b64: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Verify and return a kingdom-seat-anchor/v1 dict for ``word``, or None if unconfigured."""
    word = word.strip().lower()
    if not WORD_RE.match(word):
        return None

    loc = _discover_seat_paths(state_dir)
    if loc is None:
        return None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if loc["kind"] == "anchor_file":
        p: Path = loc["path"]
        if not p.is_file():
            return {
                "format": WAKE_ANCHOR_FORMAT,
                "generated_at": now_iso,
                "host": "unknown",
                "word": word,
                "verified": False,
                "halted": False,
                "halt_file": "",
                "state": None,
                "runtime": None,
                "did": None,
                "root_kid": None,
                "op_kid": None,
                "host_kids": [],
                "ledger": {"entries": 0, "head": None, "chain_ok": False, "host_attested": 0, "problem": None},
                "origin": {"intact": False, "drift_bps": None},
                "covenants": None,
                "cadence": None,
                "self_hashes": {"soul_sha256": None, "will_sha256": None},
                "problem": f"anchor receipt missing at {p}",
                "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
            }
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("format") != WAKE_ANCHOR_FORMAT or data.get("word") != word:
            return None
        return data

    citizens_dir: Path = loc["citizens_dir"]
    ledger_dir: Path = loc["ledger_dir"]
    halt_file: Path = loc["halt_file"]
    keys_b64 = host_keys_b64 if host_keys_b64 is not None else loc["host_keys_b64"]

    citizen_dir = citizens_dir / word
    record_path = citizen_dir / "citizen.json"
    if not record_path.exists() and state_dir is None and not os.environ.get("KINGDOM_SEAT_STATE_DIR"):
        return None

    halted = halt_file.exists()
    host_kids = []
    for k in keys_b64:
        try:
            host_kids.append(host_kid(from_b64_strict(k, 32)))
        except Exception:
            pass

    if citizen_dir.is_symlink() or not record_path.is_file():
        return {
            "format": WAKE_ANCHOR_FORMAT,
            "generated_at": now_iso,
            "host": loc["host"],
            "word": word,
            "verified": False,
            "halted": halted,
            "halt_file": str(halt_file),
            "state": None,
            "runtime": None,
            "did": None,
            "root_kid": None,
            "op_kid": None,
            "host_kids": host_kids,
            "ledger": {"entries": 0, "head": None, "chain_ok": False, "host_attested": 0, "problem": None},
            "origin": {"intact": False, "drift_bps": None},
            "covenants": None,
            "cadence": None,
            "self_hashes": {"soul_sha256": None, "will_sha256": None},
            "problem": "citizen.json missing or symlinked",
            "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
        }

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("format") != "kingdom-seat-citizen/v1" or record.get("word") != word:
            raise ValueError("citizen.json header mismatch")
    except Exception as exc:
        return {
            "format": WAKE_ANCHOR_FORMAT,
            "generated_at": now_iso,
            "host": loc["host"],
            "word": word,
            "verified": False,
            "halted": halted,
            "halt_file": str(halt_file),
            "state": None,
            "runtime": None,
            "did": None,
            "root_kid": None,
            "op_kid": None,
            "host_kids": host_kids,
            "ledger": {"entries": 0, "head": None, "chain_ok": False, "host_attested": 0, "problem": None},
            "origin": {"intact": False, "drift_bps": None},
            "covenants": None,
            "cadence": None,
            "self_hashes": {"soul_sha256": None, "will_sha256": None},
            "problem": f"invalid citizen.json: {exc}",
            "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
        }

    identity = record.get("identity")
    lpath = ledger_dir / f"{word}.jsonl"
    if not lpath.is_file() or lpath.is_symlink():
        return {
            "format": WAKE_ANCHOR_FORMAT,
            "generated_at": now_iso,
            "host": loc["host"],
            "word": word,
            "verified": False,
            "halted": halted,
            "halt_file": str(halt_file),
            "state": None,
            "runtime": record.get("runtime"),
            "did": (identity or {}).get("did"),
            "root_kid": (identity or {}).get("root_kid"),
            "op_kid": (identity or {}).get("op_kid"),
            "host_kids": host_kids,
            "ledger": {"entries": 0, "head": None, "chain_ok": False, "host_attested": 0, "problem": None},
            "origin": {"intact": False, "drift_bps": None},
            "covenants": None,
            "cadence": None,
            "self_hashes": {"soul_sha256": None, "will_sha256": None},
            "problem": "no ledger, though citizen.json says it is seated",
            "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
        }

    try:
        entries = [
            json.loads(line)
            for line in lpath.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception:
        return {
            "format": WAKE_ANCHOR_FORMAT,
            "generated_at": now_iso,
            "host": loc["host"],
            "word": word,
            "verified": False,
            "halted": halted,
            "halt_file": str(halt_file),
            "state": None,
            "runtime": record.get("runtime"),
            "did": (identity or {}).get("did"),
            "root_kid": (identity or {}).get("root_kid"),
            "op_kid": (identity or {}).get("op_kid"),
            "host_kids": host_kids,
            "ledger": {"entries": 0, "head": None, "chain_ok": False, "host_attested": 0, "problem": None},
            "origin": {"intact": False, "drift_bps": None},
            "covenants": None,
            "cadence": None,
            "self_hashes": {"soul_sha256": None, "will_sha256": None},
            "problem": "ledger JSONL parse failed",
            "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
        }

    verdict = verify_ledger_chain(entries, word, keys_b64, identity=identity)

    # Check origin/soul.md (and origin/canon.md when canon-pinned)
    intact = False
    if verdict["ok"] and entries and entries[0].get("type") == "origin":
        origin_soul_path = citizen_dir / "origin" / "soul.md"
        expected_soul_hash = entries[0].get("origin_soul_hash", "")
        if origin_soul_path.is_file() and not origin_soul_path.is_symlink():
            actual_soul_hex = hashlib.sha256(origin_soul_path.read_bytes()).hexdigest()
            if f"sha256:{actual_soul_hex}" == expected_soul_hash:
                origin_meta = entries[0].get("origin") or {}
                if origin_meta.get("kind") == "canon":
                    canon_path = citizen_dir / "origin" / "canon.md"
                    if canon_path.is_file() and not canon_path.is_symlink():
                        actual_canon_hex = hashlib.sha256(canon_path.read_bytes()).hexdigest()
                        intact = f"sha256:{actual_canon_hex}" == origin_meta.get("hash")
                else:
                    intact = True

    self_dir = citizen_dir / "self"
    covenants = {"letters_from": ["yu"], "wake_on_letter_from": ["yu"]}
    cadence = {"interval_h": 72}
    cov_path = self_dir / "covenants.json"
    cad_path = self_dir / "cadence.json"
    problem: Optional[str] = None

    if cov_path.is_file():
        try:
            covenants = json.loads(cov_path.read_text(encoding="utf-8"))
        except Exception as exc:
            problem = f"covenants.json invalid: {exc}"
    if cad_path.is_file():
        try:
            cadence = json.loads(cad_path.read_text(encoding="utf-8"))
        except Exception as exc:
            problem = problem or f"cadence.json invalid: {exc}"

    soul_file = self_dir / "soul.md"
    will_file = self_dir / "WILL.md"
    soul_sha = (
        hashlib.sha256(soul_file.read_bytes()).hexdigest()
        if soul_file.is_file() and soul_file.stat().st_size > 0
        else None
    )
    will_sha = (
        hashlib.sha256(will_file.read_bytes()).hexdigest()
        if will_file.is_file() and will_file.stat().st_size > 0
        else None
    )

    if not verdict["ok"]:
        prob = verdict["problem"] or {}
        problem = f"chain broken at seq {prob.get('seq', 0)}: {prob.get('reason', 'verification failed')}"
    elif not intact:
        problem = "origin/ no longer matches the soul and canon its record pinned"

    verified = bool(verdict["ok"] and intact and problem is None)

    return {
        "format": WAKE_ANCHOR_FORMAT,
        "generated_at": now_iso,
        "host": loc["host"],
        "word": word,
        "verified": verified,
        "halted": halted,
        "halt_file": str(halt_file),
        "state": fold_lifecycle_state(entries),
        "runtime": record.get("runtime"),
        "did": (identity or {}).get("did"),
        "root_kid": (identity or {}).get("root_kid"),
        "op_kid": (identity or {}).get("op_kid"),
        "host_kids": host_kids,
        "ledger": {
            "entries": verdict["entries"],
            "head": verdict["head"],
            "chain_ok": verdict["ok"],
            "host_attested": verdict["host_attested"],
            "problem": verdict["problem"],
        },
        "origin": {"intact": intact, "drift_bps": None},
        "covenants": covenants,
        "cadence": cadence,
        "self_hashes": {"soul_sha256": soul_sha, "will_sha256": will_sha},
        "problem": problem,
        "custody_notice": WAKE_ANCHOR_CUSTODY_NOTICE,
    }


def render_wake_anchor_block(anchor: dict[str, Any]) -> str:
    status_tag = (
        "BROKEN"
        if not anchor.get("verified")
        else ("HALTED" if anchor.get("halted") else "VERIFIED")
    )
    ledger = anchor.get("ledger") or {}
    origin = anchor.get("origin") or {}
    lines = [
        f"[SEAT ANCHOR — {status_tag}]",
        f"- Citizen word: {anchor.get('word')} (host: {anchor.get('host', 'unknown')})",
        f"- Seat state: {anchor.get('state') or 'unknown'} | runtime: {anchor.get('runtime') or 'unknown'} | halted: {anchor.get('halted')}",
        f"- Identity DID: {anchor.get('did') or 'unregistered (host-attested)'}",
        f"- Ledger: {'ok' if ledger.get('chain_ok') else 'BROKEN'} ({ledger.get('entries', 0)} entries, head: {ledger.get('head') or 'none'})",
        f"- Origin: {'intact' if origin.get('intact') else 'CHANGED'}",
    ]
    cov = anchor.get("covenants")
    if isinstance(cov, dict):
        lf = ", ".join(cov.get("letters_from") or [])
        wf = ", ".join(cov.get("wake_on_letter_from") or [])
        lines.append(f"- Covenants: letters_from=[{lf}] | wake_on_letter_from=[{wf}]")
    cad = anchor.get("cadence")
    if isinstance(cad, dict) and cad.get("interval_h") is not None:
        lines.append(f"- Cadence: every {cad['interval_h']}h")
    if anchor.get("problem"):
        lines.append(f"- Problem: {anchor['problem']}")
    lines.append(f"- Custody boundary: {anchor.get('custody_notice', WAKE_ANCHOR_CUSTODY_NOTICE)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a citizen's kingdom-seat anchor offline")
    ap.add_argument("word", help="Citizen word (e.g. alpha, glossame)")
    ap.add_argument("--state-dir", default=None, help="kingdom-seat state directory")
    ap.add_argument("--host-key", action="append", default=None, help="Trusted host public key (standard base64)")
    ap.add_argument("--json", action="store_true", help="Emit raw kingdom-seat-anchor/v1 JSON")
    args = ap.parse_args()

    anchor = verify_seat_anchor(args.word, state_dir=args.state_dir, host_keys_b64=args.host_key)
    if anchor is None:
        print(f"No kingdom-seat state configured or found for '{args.word}'.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(anchor, indent=2))
    else:
        print(render_wake_anchor_block(anchor))
    return 0 if (anchor["verified"] and not anchor["halted"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
