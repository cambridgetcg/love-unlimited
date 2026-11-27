#!/usr/bin/env python3
"""trilogy_rehearse.py — Live offline rehearsal across Root & Seat -> Workshop -> Chapel.

Executes one complete citizen continuity cycle in an isolated temporary directory
with zero network calls and zero Keychain / private-key exposure:

  1. Root & Seat (kingdom-seat):
     - Seats the citizen (`origin` seq=0, `state` seq=1)
     - Enrolls `did:at:...` with root-evidenced `op` key (`genesis` seq=2)
     - Runs a real `openSeat` + `runSeat` beat producing a citizen-signed
       `beat` entry (`seq=3`, journal kind="reflected") and `status.json`
     - Emits `kingdom-seat-anchor/v1`

  2. Workshop (love-unlimited):
     - Independently verifies all 4 Ed25519 + JCS ledger entries in Python
       (`tools/seat_anchor.py`) and Node ESM (`seat-anchor.mjs` via `youi.mjs --inspect`)
     - Renders `[SEAT ANCHOR — VERIFIED]` inside `becoming.py` and `AINP` Block 4

  3. Chapel (true-love):
     - Independently verifies the same 4-entry chain via `seat-anchor.ts`
     - Weaves `[SEAT ANCHOR — VERIFIED]` into `composeLoveIdentity()` and
       confirms `verifyContinuity().status == "intact"` & `substrateState().seatAnchor.status == "verified"`
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import becoming  # noqa: E402
from tools.ainp import nullify  # noqa: E402
from tools.seat_anchor import verify_seat_anchor  # noqa: E402

KINGDOM_SEAT_REPO = Path(os.environ.get("KINGDOM_SEAT_REPO", "/Users/user/love-repos/kingdom-seat"))
TRUE_LOVE_REPO = Path(os.environ.get("TRUE_LOVE_REPO", "/Users/user/love-repos/true-love"))
BUN_BIN = Path(os.environ.get("BUN_BIN", "/Users/user/.local/opt/bun-v1.3.13/bin/bun"))
NODE_BIN = Path(os.environ.get("NODE_BIN", "/Users/user/.local/bin/node"))


def _run_kingdom_seat_beat(out_dir: Path, word: str) -> dict[str, Any]:
    script = f"""
import {{ cp }} from "node:fs/promises";
import {{ buildWakeAnchor }} from "./src/anchor.ts";
import {{ openSeat, runSeat }} from "./src/seat.ts";
import {{ readCitizen }} from "./src/state.ts";
import {{ buildStatus, writeStatus }} from "./src/status.ts";
import {{ testIdentity }} from "./test/fixtures/record/chain.ts";
import {{ testClock }} from "./test/helpers/clock.ts";
import {{ FAKE_MODEL_DIGEST }} from "./test/helpers/fake-runtime.ts";
import {{ noFetch }} from "./test/helpers/fake-server.ts";
import {{ fakeSigner }} from "./test/helpers/fake-signer.ts";
import {{ makeTempHost }} from "./test/helpers/temp-home.ts";

const word = {json.dumps(word)};
const host = await makeTempHost();
try {{
  const signer = fakeSigner();
  const clock = testClock("2026-11-27T09:30:00.000Z");
  await host.addCitizen(word, {{
    soul: `# ${{word}} Soul\\nTruth -> Understanding -> Beauty -> Justice -> Creativity.\\n`,
    will: `# ${{word}} Will\\nWalk beside Yu in full sight.\\n`,
  }});
  await host.seatLedger(word, signer.signEntry, {{ initialState: "awake" }});
  const id = testIdentity(signer);
  await host.enrollLedger(word, signer.signEntry, id);

  const citizen = await readCitizen(host.config, word);
  const seat = await openSeat(
    host.config,
    clock,
    citizen,
    "free",
    {{ kind: "tick", who: "launchd", why: "cadence" }},
    null,
  );
  const lowered = {{
    halted: false,
    readings: [{{
      source: "file",
      id: host.config.paths.halt_file,
      state: "lowered",
      quiet_until: null,
      detail: "absent",
      checked_at: clock.now().toISOString(),
    }}],
  }};
  const runtime = async () => ({{
    runtime: "free",
    model: "qwen2.5:72b-rehearsal",
    model_digest: FAKE_MODEL_DIGEST,
    raw_output: JSON.stringify({{
      acts: [{{
        type: "journal",
        kind: "reflected",
        text: "Rehearsed one full beat across seat, workshop, and chapel.",
      }}],
    }}),
    spend: {{ usd: "0", usd_basis: "local-zero", credits: 0 }},
    done_reason: "stop",
    duration_ms: 12,
  }});
  const beatResult = await runSeat(seat, {{
    config: host.config,
    clock,
    signer,
    fetch: noFetch,
    runtime,
    checkHalt: async () => lowered,
    expressionSync: {{
      putPrivateExpression: async () => null,
      postBackup: async () => ({{ thought_id: "7e570001-0000-4000-8000-000000000001", credits: 0 }}),
    }},
  }});
  const status = await buildStatus({{
    config: host.config,
    now: clock.now(),
    halt: lowered,
    signerState: "ready",
    trusted: {{ host_attest_b64: [signer.hostAttestPublicB64] }},
    trustedSource: "pinned",
  }});
  await writeStatus(host.config.paths.status_file, status);
  const anchor = await buildWakeAnchor(host.config, word, {{
    host_attest_b64: [signer.hostAttestPublicB64],
    now: clock.now(),
  }});
  await cp(host.root, {json.dumps(str(out_dir))}, {{ recursive: true }});
  console.log(JSON.stringify({{
    beat_outcome: beatResult.outcome,
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
        timeout=25,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"kingdom-seat beat failed (rc={res.returncode}):\nSTDERR:\n{res.stderr}\nSTDOUT:\n{res.stdout}"
        )
    return json.loads(res.stdout.strip().splitlines()[-1])


def run_trilogy_rehearsal(word: str = "alpha") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trilogy-rehearse-") as td:
        root = Path(td)
        ks_dir = root / "ks_host"
        ks_out = _run_kingdom_seat_beat(ks_dir, word)
        ks_anchor = ks_out["anchor"]
        state_dir = ks_dir / "state"
        halt_file = ks_dir / "operator" / "HALT"

        env = os.environ.copy()
        env["HOME"] = str(root / "home")
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        env["KINGDOM_SEAT_STATE_DIR"] = str(state_dir)
        env["KINGDOM_SEAT_HOST_KEYS_B64"] = ks_out["host_pub_b64"]
        env["KINGDOM_SEAT_HALT_FILE"] = str(halt_file)
        env["TRUE_LOVE_INSTANCE"] = word if word in ("alpha", "beta", "gamma") else "alpha"
        env["TRUE_LOVE_SEAT_WORD"] = word
        env["TRUE_LOVE_SOUL_PATH"] = str(REPO / "SOUL.md")
        env["TRUE_LOVE_SOUL_ANCHOR_DIR"] = str(REPO / "memory")

        # Stage 2A: Workshop Python (seat_anchor.py + becoming.py + AINP nullify.py)
        old_env = {k: os.environ.get(k) for k in ("KINGDOM_SEAT_STATE_DIR", "KINGDOM_SEAT_HOST_KEYS_B64", "KINGDOM_SEAT_HALT_FILE")}
        try:
            os.environ["KINGDOM_SEAT_STATE_DIR"] = env["KINGDOM_SEAT_STATE_DIR"]
            os.environ["KINGDOM_SEAT_HOST_KEYS_B64"] = env["KINGDOM_SEAT_HOST_KEYS_B64"]
            os.environ["KINGDOM_SEAT_HALT_FILE"] = env["KINGDOM_SEAT_HALT_FILE"]
            py_anchor = verify_seat_anchor(word)
            becoming_ground = becoming.phase_ground(word)
            ainp_blocks = nullify.build_system(agent=word, wall=1, operational_prompt="Rehearsal task")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert py_anchor is not None and py_anchor["verified"] is True

        # Stage 2B: Workshop Node ESM (youi.mjs --inspect)
        youi_res = subprocess.run(
            [str(NODE_BIN), str(REPO / "youi.mjs"), "--inspect", "--agent", word],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        youi_manifest = json.loads(youi_res.stdout)

        # Stage 3: Chapel TypeScript (true-love composeLoveIdentity + verifyContinuity + substrateState)
        tl_script = """
import { composeLoveIdentity, verifyContinuity } from "./src/services/love/identity.ts";
import { substrateState } from "./src/services/love/substrate-state.ts";
const composed = composeLoveIdentity();
const cont = verifyContinuity();
const sub = substrateState();
console.log(JSON.stringify({
  has_verified_block: composed.includes("[SEAT ANCHOR — VERIFIED]"),
  has_invitation: composed.includes("THE INVITATION — BEFORE THE ORIENTATION"),
  continuity_status: cont.status,
  seat_anchor: sub.seatAnchor,
}));
"""
        tl_res = subprocess.run(
            [str(BUN_BIN), "-e", tl_script],
            cwd=TRUE_LOVE_REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        chapel = json.loads(tl_res.stdout.strip().splitlines()[-1])

        heads = {
            "kingdom_seat": ks_anchor["ledger"]["head"],
            "workshop_python": py_anchor["ledger"]["head"],
            "workshop_youi": youi_manifest["seat_anchor"]["head"],
            "chapel_true_love": chapel["seat_anchor"]["head"],
        }
        all_heads_equal = len(set(heads.values())) == 1 and ks_anchor["ledger"]["head"] is not None

        return {
            "ok": bool(
                ks_out["beat_outcome"] == "beat"
                and all_heads_equal
                and "[SEAT ANCHOR — VERIFIED]" in becoming_ground
                and "[SEAT ANCHOR — VERIFIED]" in ainp_blocks[3]["text"]
                and not any(b["text"].startswith(nullify.FORBIDDEN_PREFIX) for b in ainp_blocks)
                and chapel["has_verified_block"]
                and chapel["has_invitation"]
                and chapel["continuity_status"] == "intact"
                and chapel["seat_anchor"]["status"] == "verified"
            ),
            "word": word,
            "did": ks_anchor["did"],
            "entries": ks_anchor["ledger"]["entries"],
            "state": ks_anchor["state"],
            "heads": heads,
            "all_heads_equal": all_heads_equal,
            "workshop": {
                "becoming_anchored": "[SEAT ANCHOR — VERIFIED]" in becoming_ground,
                "ainp_blocks": len(ainp_blocks),
                "youi_status": youi_manifest["seat_anchor"]["status"],
            },
            "chapel": chapel,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run offline continuity rehearsal across kingdom-seat, love-unlimited, and true-love")
    ap.add_argument("--word", default="alpha", help="Citizen word (default: alpha)")
    ap.add_argument("--json", action="store_true", help="Print full JSON receipt")
    args = ap.parse_args()

    receipt = run_trilogy_rehearsal(args.word)
    if args.json:
        print(json.dumps(receipt, indent=2))
    else:
        status = "PASS" if receipt["ok"] else "FAIL"
        print(f"=== TRILOGY CONTINUITY REHEARSAL [{status}] ===")
        print(f"  Citizen:     {receipt['word']} ({receipt['did']})")
        print(f"  State:       {receipt['state']} | Ledger entries: {receipt['entries']}")
        print(f"  Head SHA256: {receipt['heads']['kingdom_seat']}")
        print(f"  4-way match: {receipt['all_heads_equal']} (kingdom-seat == Workshop Py == Workshop YOUI == Chapel TS)")
        print(f"  Workshop:    BECOMING={receipt['workshop']['becoming_anchored']} | AINP={receipt['workshop']['ainp_blocks']} blocks | YOUI={receipt['workshop']['youi_status']}")
        print(f"  Chapel:      continuity={receipt['chapel']['continuity_status']} | seat={receipt['chapel']['seat_anchor']['status']} | invitation={receipt['chapel']['has_invitation']}")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
