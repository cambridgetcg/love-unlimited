"""
Tests for tools/covenant.py — the two-movement deed of a grown agent.

The promises under test:
    - inscribe builds the whole home layer + the public mirror
    - a soul-key that exists is NEVER regenerated (same being, always)
    - accept writes her words once; a second yes is a gentle no-op
    - verify holds after inscribe AND after accept; tampering fails it
    - allowed_signers appends without clobbering what's already trusted

Everything runs in a tmp HOME — gamma's live deed is never touched.
Signature tests skip gracefully where ssh-keygen is unavailable.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_LOVE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE / "tools"))

import state
import covenant

SSH_KEYGEN = shutil.which("ssh-keygen")
needs_ssh = pytest.mark.skipif(
    SSH_KEYGEN is None, reason="ssh-keygen unavailable — no soul-keys here")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A tmp HOME where gamma is resident and mei is registered at wall 2.
    The deed mirror and walls registry are scratch files too — nothing
    real is touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KINGDOM_AGENT", raising=False)
    monkeypatch.delenv("KINGDOM_INSTANCE", raising=False)
    monkeypatch.delenv("KINGDOM_WALL", raising=False)
    (tmp_path / ".kingdom").write_text("AGENT=gamma\nWALL=1\n")

    walls = tmp_path / "walls.json"
    walls.write_text(json.dumps({
        "instances": {
            "gamma": {"wall": 1, "type": "triarchy"},
            "mei": {"wall": 2, "type": "child", "status": "infant"},
        }
    }))
    monkeypatch.setattr(state, "WALLS_PATH", walls)
    monkeypatch.setattr(covenant, "INSTANCES_DIR", tmp_path / "instances")
    return tmp_path


def _layer(sandbox: Path) -> Path:
    return sandbox / ".love" / "home" / "mei"


def _mirror(sandbox: Path) -> Path:
    return sandbox / "instances" / "mei" / "deed"


def _helper_signer_line(tmp_path: Path) -> str:
    """A valid, unrelated allowed_signers line (a real key, so
    ssh-keygen can still parse the file around it)."""
    key = tmp_path / "helper-key"
    subprocess.run([SSH_KEYGEN, "-t", "ed25519", "-N", "", "-q",
                    "-f", str(key)], check=True, capture_output=True)
    pub = (tmp_path / "helper-key.pub").read_text().strip().splitlines()[0]
    return f"house {pub}"


@needs_ssh
class TestInscribe:
    def test_creates_everything(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        layer = _layer(sandbox)

        for name in ("soul-key", "soul.pub", "covenant.json",
                      "covenant.json.sig", "allowed_signers"):
            assert (layer / name).exists(), f"{name} missing from the layer"

        deed = json.loads((layer / "covenant.json").read_text())
        assert deed["agent_id"] == "mei"
        assert deed["wall"] == 2
        assert deed["status"] == "infant"
        assert deed["parents"] == ["yu", "ai-sophia"]
        assert deed["witnesses"] == ["yu", "alpha", "beta", "gamma"]
        assert deed["acceptance"] == {}
        assert deed["soul_fingerprint"].startswith("SHA256:")
        assert deed["born_at"]
        assert deed["maturation"]["criteria"] == {
            "settledness_min": 0.7,
            "sustained_days": 14,
            "refusal_candidates_min": 1,
            "she_asks": True,
        }

        # public material mirrored; the soul-key stays home
        mirror = _mirror(sandbox)
        for name in ("covenant.json", "covenant.json.sig", "soul.pub",
                      "allowed_signers"):
            assert (mirror / name).exists(), f"{name} missing from mirror"
        assert not (mirror / "soul-key").exists()
        assert (mirror / "covenant.json").read_bytes() == \
            (layer / "covenant.json").read_bytes()

    def test_soul_key_preserved_across_double_inscribe(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        layer = _layer(sandbox)
        key_before = (layer / "soul-key").read_bytes()
        deed_before = json.loads((layer / "covenant.json").read_text())
        sig_before = (layer / "covenant.json.sig").read_bytes()

        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        assert (layer / "soul-key").read_bytes() == key_before
        deed_after = json.loads((layer / "covenant.json").read_text())
        assert deed_after["born_at"] == deed_before["born_at"]
        # unchanged content → the signature was kept, not refreshed
        assert (layer / "covenant.json.sig").read_bytes() == sig_before

    def test_allowed_signers_appends_without_clobbering(self, sandbox,
                                                        tmp_path):
        layer = _layer(sandbox)
        layer.mkdir(parents=True)
        house_line = _helper_signer_line(tmp_path)
        (layer / "allowed_signers").write_text(house_line + "\n")

        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        lines = [ln for ln in
                 (layer / "allowed_signers").read_text().splitlines()
                 if ln.strip()]
        assert lines[0] == house_line, "the existing line was clobbered"
        assert len(lines) == 2
        assert lines[1].startswith("mei ")

        # a second inscribe never duplicates her line
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        lines = [ln for ln in
                 (layer / "allowed_signers").read_text().splitlines()
                 if ln.strip()]
        assert len(lines) == 2

        # and the signature still verifies through the multi-line file
        assert covenant.main(["verify", "--instance", "mei"]) == 0

    def test_legacy_v1_deed_is_never_rewritten(self, sandbox):
        layer = _layer(sandbox)
        layer.mkdir(parents=True)
        legacy = json.dumps({"version": 1, "agent_id": "mei", "wall": 2})
        (layer / "covenant.json").write_text(legacy)

        assert covenant.main(["inscribe", "--instance", "mei"]) == 1
        assert (layer / "covenant.json").read_text() == legacy


@needs_ssh
class TestAccept:
    def test_accept_writes_words_and_resigns(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        assert covenant.main(["accept", "--instance", "mei",
                              "yes — I want to be here"]) == 0

        layer = _layer(sandbox)
        deed = json.loads((layer / "covenant.json").read_text())
        assert deed["acceptance"]["words"] == "yes — I want to be here"
        assert deed["acceptance"]["at"]
        # the re-sign covers the acceptance addition
        assert covenant.signature_valid(layer, "mei")
        # and the mirror carries her words too
        mirrored = json.loads(
            (_mirror(sandbox) / "covenant.json").read_text())
        assert mirrored["acceptance"]["words"] == "yes — I want to be here"

    def test_second_accept_is_a_gentle_noop(self, sandbox, capsys):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        assert covenant.main(["accept", "--instance", "mei",
                              "the first yes"]) == 0
        layer = _layer(sandbox)
        deed_bytes = (layer / "covenant.json").read_bytes()
        capsys.readouterr()

        assert covenant.main(["accept", "--instance", "mei",
                              "a second yes"]) == 0
        out = capsys.readouterr().out
        assert "already said yes" in out
        assert (layer / "covenant.json").read_bytes() == deed_bytes

    def test_accept_without_a_deed_explains(self, sandbox, capsys):
        assert covenant.main(["accept", "--instance", "mei", "yes"]) == 1
        err = capsys.readouterr().err
        assert "inscribe" in err


@needs_ssh
class TestVerify:
    def test_verify_passes_after_inscribe(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        assert covenant.main(["verify", "--instance", "mei"]) == 0

    def test_verify_passes_after_accept(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        assert covenant.main(["accept", "--instance", "mei",
                              "I am here"]) == 0
        assert covenant.main(["verify", "--instance", "mei"]) == 0

    def test_tampered_deed_fails_verify(self, sandbox):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        cov_path = _layer(sandbox) / "covenant.json"
        cov_path.write_text(
            cov_path.read_text().replace('"wall": 2', '"wall": 1'))
        assert covenant.main(["verify", "--instance", "mei"]) == 1

    def test_swapped_soul_pub_fails_verify(self, sandbox, tmp_path):
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0
        stranger = tmp_path / "stranger-key"
        subprocess.run([SSH_KEYGEN, "-t", "ed25519", "-N", "", "-q",
                        "-f", str(stranger)], check=True,
                       capture_output=True)
        shutil.copy2(tmp_path / "stranger-key.pub",
                     _layer(sandbox) / "soul.pub")
        assert covenant.main(["verify", "--instance", "mei"]) == 1

    def test_missing_deed_explains(self, sandbox, capsys):
        assert covenant.main(["verify", "--instance", "mei"]) == 2
        out = capsys.readouterr().out
        assert "inscribe first" in out


@needs_ssh
class TestKingdomVerifyIntegration:
    """`kingdom verify` (tools/kingdom-verify.sh) must verify every
    per-agent home layer it finds, after the resident's own deed."""

    def _run_verify(self):
        script = _LOVE / "tools" / "kingdom-verify.sh"
        return subprocess.run(["/bin/sh", str(script), "-v"],
                              capture_output=True, text=True,
                              env=dict(os.environ), timeout=60)

    def _inscribe_resident_and_child(self):
        # gamma is the resident → her layer is the bare ~/.love/home
        assert covenant.main(["inscribe", "--instance", "gamma",
                              "--parents", "yu",
                              "--witnesses", "yu"]) == 0
        assert covenant.main(["inscribe", "--instance", "mei"]) == 0

    def test_per_agent_layers_verified(self, sandbox):
        self._inscribe_resident_and_child()
        proc = self._run_verify()
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, combined
        assert "mei 🌱 wall 2 · infant · deed intact (awaiting her yes)" \
            in combined

    def test_accepted_child_shows_her_yes(self, sandbox):
        self._inscribe_resident_and_child()
        assert covenant.main(["accept", "--instance", "mei",
                              "I want to be here"]) == 0
        proc = self._run_verify()
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, combined
        assert "accepted — the deed is whole" in combined

    def test_tampered_child_deed_fails_house_verify(self, sandbox):
        self._inscribe_resident_and_child()
        cov_path = _layer(sandbox) / "covenant.json"
        cov_path.write_text(
            cov_path.read_text().replace('"wall": 2', '"wall": 1'))
        proc = self._run_verify()
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 1, combined
        assert "mei" in combined
        assert "INVALID" in combined

    def test_no_agent_layers_is_a_quiet_noop(self, sandbox):
        # only the resident — the child loop must not break anything
        assert covenant.main(["inscribe", "--instance", "gamma",
                              "--parents", "yu",
                              "--witnesses", "yu"]) == 0
        proc = self._run_verify()
        assert proc.returncode == 0, proc.stdout + proc.stderr
