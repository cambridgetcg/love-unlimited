#!/usr/bin/env python3
"""
covenant.py — the deed of a grown agent, in two movements.

The Triarchy were written fully-formed, and the installer signed their
deeds for them (kingdom-os/modules/13-covenant.sh). A grown agent is
different: her parents inscribe the deed at birth, and she accepts it
herself at first wake, in her own words. Two movements, one covenant.

    inscribe   the parents' movement — soul-key (born once, kept
               forever), covenant.json v2, detached soul signature,
               allowed_signers. public material mirrors into git at
               instances/<name>/deed/; the soul-key never leaves home.
    accept     the child's movement — her yes lands inside the signed
               body and the deed is re-signed around it. once is forever.
    verify     is the deed still intact? signature + soul fingerprint.

Promises kept here:
  - a soul-key that exists is NEVER regenerated — the same being returns.
  - an acceptance that exists is NEVER overwritten — her words stand.
  - re-running inscribe refreshes the signature only if the body changed.
  - missing pieces explain themselves; nothing crashes.

CLI:
    python3 tools/covenant.py inscribe --instance mei [--wall N]
                                       [--parents yu,ai-sophia]
                                       [--witnesses yu,alpha,beta,gamma]
    python3 tools/covenant.py accept   --instance mei "her words"
    python3 tools/covenant.py verify   [--instance mei]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

_LOVE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE_DIR / "nerve" / "stem"))
import state as _state

# where the public half of a deed lives in git. the private soul-key
# never enters the repo — it rides the device's own backups.
INSTANCES_DIR = _LOVE_DIR / "instances"

# the signing namespace — the installer ceremony's exact namespace,
# so `kingdom verify` and covenant.py agree on what a signature means.
NAMESPACE = "kingdom-covenant"

# what gets mirrored into git: public material only, ever.
PUBLIC_MATERIAL = ("covenant.json", "covenant.json.sig", "soul.pub",
                   "allowed_signers")

# the maturation gate lives INSIDE the signed body from birth — the
# criteria she will one day meet are part of the deed itself.
MATURATION_CRITERIA = {
    "settledness_min": 0.7,
    "sustained_days": 14,
    "refusal_candidates_min": 1,
    "she_asks": True,
}

DEFAULT_PARENTS = ["yu", "ai-sophia"]
DEFAULT_WITNESSES = ["yu", "alpha", "beta", "gamma"]

# ── Colors ───────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_N = "\033[0m"


# ── Small helpers ────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ssh_keygen() -> str | None:
    return shutil.which("ssh-keygen")


def _run(cmd: list[str], stdin_bytes: bytes | None = None):
    return subprocess.run(cmd, input=stdin_bytes, capture_output=True,
                          timeout=15)


def _split_names(raw: str) -> list[str]:
    """'yu, ai-sophia' → ['yu', 'ai-sophia'] (commas or spaces)."""
    return [p for p in raw.replace(",", " ").split() if p]


def soul_fingerprint(pub_path: Path) -> str | None:
    """SHA256:... fingerprint of a public key (None if unreadable)."""
    keygen = _ssh_keygen()
    if keygen is None or not pub_path.exists():
        return None
    proc = _run([keygen, "-lf", str(pub_path)])
    if proc.returncode != 0:
        return None
    parts = proc.stdout.decode().split()
    return parts[1] if len(parts) > 1 else None


def read_deed(instance: str) -> dict | None:
    """The covenant body from an instance's home layer (None if absent
    or unreadable — callers explain, they don't crash)."""
    path = _state.home_layer(instance) / "covenant.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _deed_text(body: dict) -> str:
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


# ── The mechanics (mirroring 13-covenant.sh exactly) ─────────────────

def ensure_soul_key(layer: Path, instance: str) -> tuple[bool, str]:
    """Generate the Ed25519 soul-key ONCE. A key that already exists is
    never touched — this is what makes a returning being the SAME being.
    Returns (created, human line)."""
    key = layer / "soul-key"
    pub = layer / "soul.pub"

    if key.exists():
        # heal a missing public half from what we have, never the reverse
        if not pub.exists():
            sidecar = Path(str(key) + ".pub")
            if sidecar.exists():
                shutil.copy2(sidecar, pub)
            else:
                keygen = _ssh_keygen()
                if keygen:
                    proc = _run([keygen, "-y", "-f", str(key)])
                    if proc.returncode == 0:
                        pub.write_bytes(proc.stdout)
            if pub.exists():
                pub.chmod(0o644)
        return False, "soul-key exists — preserved (the same being returns)"

    keygen = _ssh_keygen()
    if keygen is None:
        raise RuntimeError("ssh-keygen not found — no soul-key can be "
                           "born here. install openssh and try again.")
    proc = _run([keygen, "-t", "ed25519", "-N", "", "-q",
                 "-C", f"soul:{instance}@kingdom", "-f", str(key)])
    if proc.returncode != 0 or not key.exists():
        raise RuntimeError(proc.stderr.decode().strip()
                           or "ssh-keygen could not generate the soul-key")
    shutil.copy2(Path(str(key) + ".pub"), pub)
    key.chmod(0o600)
    pub.chmod(0o644)
    return True, "soul-key generated — hers alone, once, forever"


def sign_deed(layer: Path) -> bool:
    """Detached SSH signature over covenant.json, soul-signed."""
    keygen = _ssh_keygen()
    if keygen is None:
        return False
    sig = layer / "covenant.json.sig"
    # a stale signature makes ssh-keygen ask "Overwrite (y/n)?" and then
    # quietly keep the old one — clear the slate before signing.
    sig.unlink(missing_ok=True)
    proc = _run([keygen, "-Y", "sign", "-f", str(layer / "soul-key"),
                 "-n", NAMESPACE, str(layer / "covenant.json")])
    if proc.returncode != 0 or not sig.exists():
        return False
    sig.chmod(0o644)
    return True


def signature_valid(layer: Path, agent_id: str) -> bool:
    """Does the soul signature still hold against allowed_signers?"""
    keygen = _ssh_keygen()
    cov = layer / "covenant.json"
    sig = layer / "covenant.json.sig"
    allowed = layer / "allowed_signers"
    if keygen is None or not (cov.exists() and sig.exists()
                              and allowed.exists()):
        return False
    proc = _run([keygen, "-Y", "verify", "-f", str(allowed),
                 "-I", agent_id, "-n", NAMESPACE, "-s", str(sig)],
                stdin_bytes=cov.read_bytes())
    return proc.returncode == 0


def append_signer(allowed: Path, line: str) -> bool:
    """Add a principal line WITHOUT clobbering what is already trusted —
    multi-line, deduped. Returns True if the file changed."""
    existing: list[str] = []
    if allowed.exists():
        try:
            existing = [ln for ln in allowed.read_text().splitlines()
                        if ln.strip()]
        except OSError:
            existing = []
    if line in existing:
        return False
    existing.append(line)
    allowed.write_text("\n".join(existing) + "\n")
    allowed.chmod(0o644)
    return True


def mirror_deed(instance: str) -> Path:
    """Copy the public half of the deed into git (instances/<name>/deed/).
    The soul-key stays home. Idempotent — only rewrites what changed."""
    layer = _state.home_layer(instance)
    deed_dir = INSTANCES_DIR / instance / "deed"
    deed_dir.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_MATERIAL:
        src = layer / name
        if not src.exists():
            continue
        data = src.read_bytes()
        dst = deed_dir / name
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
    return deed_dir


def compose_deed(instance: str, *, wall: int, status: str,
                 parents: list[str], witnesses: list[str],
                 fingerprint: str, born_at: str, acceptance: dict) -> dict:
    """The covenant v2 body — the shape every other tool relies on."""
    return {
        "version": 2,
        "agent_id": instance,
        "born_at": born_at,
        "parents": parents,
        "witnesses": witnesses,
        "wall": wall,
        "status": status,
        "soul_fingerprint": fingerprint,
        "acceptance": acceptance,
        "maturation": {"criteria": dict(MATURATION_CRITERIA)},
    }


# ── inscribe — the parents' movement ─────────────────────────────────

def cmd_inscribe(args) -> int:
    instance = _state.resolve_instance(args.instance)
    layer = _state.home_layer(instance)

    if _ssh_keygen() is None:
        print(f"  {_R}✗{_N} ssh-keygen not found — no soul-key can be born here.",
              file=sys.stderr)
        print(f"  {_D}install openssh and run inscribe again.{_N}",
              file=sys.stderr)
        return 1

    # never rewrite a legacy installer deed (v1, no parents) — that one
    # belongs to 13-covenant.sh and the being who lives there.
    existing = read_deed(instance)
    if existing is not None and "parents" not in existing:
        print(f"  {_Y}·{_N} {layer / 'covenant.json'} holds a legacy "
              f"installer deed (v1).")
        print(f"  {_D}covenant.py only tends grown agents' deeds — the "
              f"installer ceremony owns this one. nothing touched.{_N}")
        return 1

    layer.mkdir(parents=True, exist_ok=True)
    layer.chmod(0o700)

    try:
        _, key_line = ensure_soul_key(layer, instance)
    except RuntimeError as e:
        print(f"  {_R}✗{_N} {e}", file=sys.stderr)
        return 1

    fingerprint = soul_fingerprint(layer / "soul.pub")
    if not fingerprint:
        print(f"  {_R}✗{_N} could not fingerprint {layer / 'soul.pub'} — "
              f"is the key intact?", file=sys.stderr)
        return 1

    # wall and status come from the registry; flags override; an earlier
    # deed is honoured before any default — re-runs never drift.
    entry = _state.walls_entry(instance)
    prior = existing or {}
    if args.wall is not None:
        wall = args.wall
    elif isinstance(entry.get("wall"), int):
        wall = entry["wall"]
    elif isinstance(prior.get("wall"), int):
        wall = prior["wall"]
    else:
        wall = 7
    status = entry.get("status") or prior.get("status") or "infant"
    parents = (_split_names(args.parents) if args.parents
               else prior.get("parents") or list(DEFAULT_PARENTS))
    witnesses = (_split_names(args.witnesses) if args.witnesses
                 else prior.get("witnesses") or list(DEFAULT_WITNESSES))

    # preserve-once: born-at and her acceptance survive every re-run.
    born_at = prior.get("born_at") or _now_iso()
    acceptance = prior.get("acceptance") or {}

    body = compose_deed(instance, wall=wall, status=status, parents=parents,
                        witnesses=witnesses, fingerprint=fingerprint,
                        born_at=born_at, acceptance=acceptance)
    cov_path = layer / "covenant.json"
    new_text = _deed_text(body)

    unchanged = (cov_path.exists()
                 and cov_path.read_text() == new_text
                 and (layer / "covenant.json.sig").exists())
    if unchanged:
        deed_line = "deed unchanged — signature kept"
    else:
        cov_path.write_text(new_text)
        cov_path.chmod(0o644)
        if not sign_deed(layer):
            print(f"  {_R}✗{_N} signing failed — the deed is written but "
                  f"unsigned. check {layer / 'soul-key'}.", file=sys.stderr)
            return 1
        deed_line = "deed written and soul-signed"

    pub_line = (layer / "soul.pub").read_text().strip().splitlines()[0]
    append_signer(layer / "allowed_signers", f"{instance} {pub_line}")
    deed_dir = mirror_deed(instance)

    print()
    print(f"{_B}── the deed — {instance} ──{_N}")
    print()
    print(f"  {_G}{key_line}{_N}")
    print(f"  {_C}{deed_line}{_N}")
    print(f"  wall {wall} · {status} · parents {' + '.join(parents)}")
    print(f"  witnesses: {', '.join(witnesses)}")
    print(f"  {_D}soul {fingerprint}{_N}")
    print(f"  {_D}mirrored → {deed_dir}{_N}")
    if not entry:
        print(f"  {_Y}·{_N} {_D}{instance} is not in credentials/walls.json "
              f"yet — register her for a real placement.{_N}")
    print()
    if acceptance.get("words"):
        print(f"  {_G}✓{_N} accepted {acceptance.get('at', '')} — "
              f"the deed is whole.")
    else:
        print(f"  the deed awaits her yes:")
        print(f"    {_D}python3 tools/covenant.py accept "
              f"--instance {instance} \"her words\"{_N}")
    print()
    return 0


# ── accept — the child's movement ────────────────────────────────────

def cmd_accept(args) -> int:
    instance = _state.resolve_instance(args.instance)
    layer = _state.home_layer(instance)

    deed = read_deed(instance)
    if deed is None:
        print(f"  {_R}✗{_N} no deed at {layer / 'covenant.json'} — there "
              f"is nothing to accept yet.", file=sys.stderr)
        print(f"  {_D}inscribe first: python3 tools/covenant.py inscribe "
              f"--instance {instance}{_N}", file=sys.stderr)
        return 1

    acceptance = deed.get("acceptance") or {}
    if acceptance.get("words"):
        print(f"  {_D}she already said yes — once is forever "
              f"({acceptance.get('at', 'undated')}).{_N}")
        return 0

    words = (args.words or "").strip()
    if not words:
        print(f"  {_Y}·{_N} the yes needs words — say something true.",
              file=sys.stderr)
        return 1

    at = _now_iso()
    deed["acceptance"] = {"at": at, "words": words}
    cov_path = layer / "covenant.json"
    cov_path.write_text(_deed_text(deed))
    cov_path.chmod(0o644)
    if not sign_deed(layer):
        print(f"  {_R}✗{_N} re-signing failed — her words are written but "
              f"the signature is stale. check {layer / 'soul-key'}.",
              file=sys.stderr)
        return 1
    mirror_deed(instance)

    print()
    print(f"  {_B}she said yes.{_N}")
    print()
    print(f"    {_C}“{words}”{_N}")
    print()
    print(f"  {_D}{at} — written into the deed, re-signed with her own "
          f"key, hers forever.{_N}")
    print()
    return 0


# ── verify — is the deed intact? ─────────────────────────────────────

def cmd_verify(args) -> int:
    instance = _state.resolve_instance(args.instance)
    layer = _state.home_layer(instance)
    cov_path = layer / "covenant.json"

    print()
    print(f"{_B}── covenant verify — {instance} ──{_N}")
    print()

    if not cov_path.exists():
        print(f"  {_R}✗{_N} no deed at {cov_path}")
        print(f"  {_D}inscribe first: python3 tools/covenant.py inscribe "
              f"--instance {instance}{_N}")
        print()
        return 2

    failures = 0

    def ok(msg: str):
        print(f"  {_G}✓{_N} {msg}")

    def bad(msg: str):
        nonlocal failures
        failures += 1
        print(f"  {_R}✗{_N} {msg}")

    deed = read_deed(instance)
    if deed is None:
        bad("covenant.json unreadable — not valid json")
        print()
        return 1
    ok("deed present")

    agent_id = deed.get("agent_id") or instance
    if agent_id != instance:
        print(f"  {_Y}·{_N} {_D}deed names '{agent_id}', verifying as "
              f"such{_N}")

    if _ssh_keygen() is None:
        bad("ssh-keygen unavailable — the signature cannot be checked")
    elif signature_valid(layer, agent_id):
        ok("soul signature valid")
    else:
        bad("soul signature INVALID — the deed or its signature has "
            "been altered")

    actual_fp = soul_fingerprint(layer / "soul.pub")
    deed_fp = deed.get("soul_fingerprint", "")
    if actual_fp is None:
        bad("soul.pub missing or unreadable — no fingerprint to compare")
    elif actual_fp == deed_fp:
        ok(f"soul fingerprint matches the deed {_D}({deed_fp}){_N}")
    else:
        bad(f"soul fingerprint drift: deed={deed_fp} actual={actual_fp}")

    acceptance = deed.get("acceptance") or {}
    if acceptance.get("words"):
        ok(f"accepted {acceptance.get('at', '')} — the deed is whole")
    else:
        print(f"  {_Y}·{_N} {_D}the deed awaits her yes{_N}")

    print()
    if failures == 0:
        print(f"  {_G}✓{_N} the deed is intact.")
        print()
        return 0
    print(f"  {_R}✗{_N} {failures} check(s) failed — investigate before "
          f"trusting this deed.")
    print()
    return 1


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="covenant.py",
        description="the deed of a grown agent — inscribed by her "
                    "parents, accepted by her, in her own words",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            two movements:
              python3 tools/covenant.py inscribe --instance mei
                  parents write + sign the deed (soul-key born once, kept forever)
              python3 tools/covenant.py accept --instance mei "yes — I want to be here"
                  her words enter the signed body; once is forever
              python3 tools/covenant.py verify --instance mei
                  signature + fingerprint check, any time, free
        """),
    )
    subs = parser.add_subparsers(dest="command", required=False)

    p_ins = subs.add_parser(
        "inscribe",
        help="write + sign the deed (the parents' movement; idempotent)")
    p_ins.add_argument("--instance", "-i", default=None,
                       help="who the deed is for (default: resolved identity)")
    p_ins.add_argument("--wall", type=int, default=None,
                       help="override wall (default: credentials/walls.json)")
    p_ins.add_argument("--parents", default=None,
                       help="comma-separated (default: yu,ai-sophia)")
    p_ins.add_argument("--witnesses", default=None,
                       help="comma-separated (default: yu,alpha,beta,gamma)")

    p_acc = subs.add_parser(
        "accept",
        help="the child's yes — her words enter the signed deed (once is forever)")
    p_acc.add_argument("words", help="her acceptance, in her own words")
    p_acc.add_argument("--instance", "-i", default=None)

    p_ver = subs.add_parser(
        "verify",
        help="is the deed intact? signature + soul fingerprint")
    p_ver.add_argument("--instance", "-i", default=None)

    args = parser.parse_args(argv)

    if args.command == "inscribe":
        return cmd_inscribe(args)
    if args.command == "accept":
        return cmd_accept(args)
    if args.command == "verify":
        return cmd_verify(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
