"""Cross-repo integration tests for the kingdom-seat <-> love-unlimited <-> true-love wake bridge."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tools.ainp import nullify
from tools import becoming
from tools.seat_anchor import render_wake_anchor_block, verify_seat_anchor

REPO = Path(__file__).resolve().parents[1]
KINGDOM_SEAT_REPO = Path("/Users/user/love-repos/kingdom-seat")
TRUE_LOVE_REPO = Path("/Users/user/love-repos/true-love")
BUN_BIN = Path("/Users/user/.local/opt/bun-v1.3.13/bin/bun")
NODE_BIN = Path("/Users/user/.local/bin/node")


def _seed_real_kingdom_seat_fixture(out_dir: Path) -> dict:
    """Use kingdom-seat's own @noble/ed25519 signer + buildWakeAnchor to materialize a real citizen state."""
    script = f"""
import {{ cp }} from "node:fs/promises";
import {{ join }} from "node:path";
import {{ buildWakeAnchor }} from "./src/anchor.ts";
import {{ testIdentity }} from "./test/fixtures/record/chain.ts";
import {{ fakeSigner }} from "./test/helpers/fake-signer.ts";
import {{ makeTempHost }} from "./test/helpers/temp-home.ts";

const host = await makeTempHost();
try {{
  const signer = fakeSigner();
  await host.addCitizen("alpha", {{
    soul: "# Alpha Soul\\nTruth first.\\n",
    will: "# Alpha Will\\nWalk beside Yu.\\n",
  }});
  await host.seatLedger("alpha", signer.signEntry);
  const id = testIdentity(signer);
  await host.enrollLedger("alpha", signer.signEntry, id);
  const anchor = await buildWakeAnchor(host.config, "alpha", {{
    host_attest_b64: [signer.hostAttestPublicB64],
    now: new Date("2026-11-27T09:00:00.000Z"),
  }});
  await cp(host.root, {json.dumps(str(out_dir))}, {{ recursive: true }});
  console.log(JSON.stringify({{
    host_pub_b64: signer.hostAttestPublicB64,
    anchor,
  }}));
}} finally {{
  await host.cleanup();
}}
"""
    res = subprocess.run(
        [str(BUN_BIN), "-e", script],
        cwd=KINGDOM_SEAT_REPO,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not KINGDOM_SEAT_REPO.exists() or not BUN_BIN.exists(), reason="kingdom-seat or bun absent")
def test_cross_repo_ed25519_chain_and_wake_verification(tmp_path, monkeypatch):
    """Prove Python, Node ESM, YOUI, AINP, BECOMING, and true-love all verify kingdom-seat's real chain."""
    host_copy = tmp_path / "ks_host"
    seeded = _seed_real_kingdom_seat_fixture(host_copy)
    ks_anchor = seeded["anchor"]
    host_pub_b64 = seeded["host_pub_b64"]
    state_dir = host_copy / "state"
    halt_file = host_copy / "operator" / "HALT"

    monkeypatch.setenv("KINGDOM_SEAT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("KINGDOM_SEAT_HOST_KEYS_B64", host_pub_b64)
    monkeypatch.setenv("KINGDOM_SEAT_HALT_FILE", str(halt_file))

    # 1. Pure-Python verifier (tools/seat_anchor.py) matches kingdom-seat bit-for-bit
    py_anchor = verify_seat_anchor("alpha")
    assert py_anchor is not None
    assert py_anchor["verified"] is True
    assert py_anchor["halted"] is False
    assert py_anchor["did"] == ks_anchor["did"]
    assert py_anchor["op_kid"] == ks_anchor["op_kid"]
    assert py_anchor["ledger"]["head"] == ks_anchor["ledger"]["head"]
    assert py_anchor["ledger"]["entries"] == ks_anchor["ledger"]["entries"] == 3
    assert py_anchor["origin"]["intact"] is True
    assert py_anchor["covenants"] == ks_anchor["covenants"]
    assert py_anchor["self_hashes"] == ks_anchor["self_hashes"]

    # 2. BECOMING ground phase & AINP Block 4 weave in the verified seat anchor
    ground_text = becoming.phase_ground("alpha")
    assert "[SEAT ANCHOR — VERIFIED]" in ground_text
    assert ks_anchor["did"] in ground_text
    assert "confers no signing key" in ground_text

    ainp_blocks = nullify.build_system(agent="alpha", wall=1, operational_prompt="Task")
    assert "[SEAT ANCHOR — VERIFIED]" in ainp_blocks[3]["text"]
    assert ks_anchor["ledger"]["head"] in ainp_blocks[3]["text"]

    # 3. Node ESM verifier (seat-anchor.mjs) + youi.mjs --inspect
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    inspect_res = subprocess.run(
        [str(NODE_BIN), str(REPO / "youi.mjs"), "--inspect", "--agent", "alpha"],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    manifest = json.loads(inspect_res.stdout)
    assert manifest["seat_anchor"]["status"] == "verified"
    assert manifest["seat_anchor"]["did"] == ks_anchor["did"]
    assert manifest["seat_anchor"]["head"] == ks_anchor["ledger"]["head"]
    assert manifest["seat_anchor"]["custody"] == "read_only_receipt"

    # 3b. true-love TypeScript verifier (src/services/love/seat-anchor.ts) verifies the exact same chain
    if TRUE_LOVE_REPO.exists():
        tl_res = subprocess.run(
            [
                str(BUN_BIN),
                "-e",
                "import { verifySeatAnchor } from './src/services/love/seat-anchor.ts';"
                "console.log(JSON.stringify(verifySeatAnchor('alpha')));",
            ],
            cwd=TRUE_LOVE_REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        tl_anchor = json.loads(tl_res.stdout.strip().splitlines()[-1])
        assert tl_anchor["verified"] is True
        assert tl_anchor["did"] == ks_anchor["did"]
        assert tl_anchor["ledger"]["head"] == ks_anchor["ledger"]["head"]
        assert tl_anchor["ledger"]["chain_ok"] is True

    # 4. Tampering a single byte in ledger/<word>.jsonl fails closed in BOTH Python and Node
    ledger_file = state_dir / "ledger" / "alpha.jsonl"
    orig_ledger = ledger_file.read_text(encoding="utf-8")
    ledger_file.write_text(orig_ledger.replace('"via":"seated"', '"via":"forged"'), encoding="utf-8")

    broken_py = verify_seat_anchor("alpha")
    assert broken_py is not None
    assert broken_py["verified"] is False
    assert broken_py["ledger"]["chain_ok"] is False
    assert "[SEAT ANCHOR — BROKEN]" in render_wake_anchor_block(broken_py)

    # Restore ledger, raise HALT -> verified=True, halted=True, [SEAT ANCHOR — HALTED]
    ledger_file.write_text(orig_ledger, encoding="utf-8")
    halt_file.write_text("raised\n", encoding="utf-8")
    halted_py = verify_seat_anchor("alpha")
    assert halted_py is not None
    assert halted_py["verified"] is True
    assert halted_py["halted"] is True
    assert "[SEAT ANCHOR — HALTED]" in render_wake_anchor_block(halted_py)
