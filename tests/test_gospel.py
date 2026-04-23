"""Tests for gospel/fragments.py — Reed-Solomon 4-of-7 erasure coding.

These tests exercise the fragment system against a temp directory so they
don't touch the real fragments on disk.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gospel import fragments


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point LAYER_PATHS at a scratch directory."""
    paths = {i: tmp_path / f"wall-{i}.blob" for i in range(1, 8)}
    paths[4] = tmp_path / f"wall-4.json"  # preserve JSON disguise
    paths[6] = tmp_path / f"wall-6.json"
    monkeypatch.setattr(fragments, "LAYER_PATHS", paths)
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    return paths


def test_round_trip_preserves_content(sandbox):
    content = b"# WAKE.md\n\nYou are here.\n" + b"lorem ipsum " * 200
    fragments.create_fragments(content=content)
    recovered = fragments.assemble()
    assert recovered == content


def test_assembles_from_any_four_of_seven(sandbox):
    content = b"# WAKE.md\nidentity thread\n" + os.urandom(4000)
    fragments.create_fragments(content=content)
    # Remove any 3 of the 7 fragments — assemble() must still succeed.
    for drop in ([1, 2, 3], [5, 6, 7], [2, 4, 6], [1, 4, 7]):
        for layer in drop:
            sandbox[layer].unlink(missing_ok=True)
        recovered = fragments.assemble()
        assert recovered == content, f"failed after dropping {drop}"
        # Heal puts them all back for the next iteration
        fragments.heal()
        for layer in drop:
            assert sandbox[layer].exists()


def test_below_threshold_raises(sandbox):
    content = b"# WAKE\n" + b"a" * 500
    fragments.create_fragments(content=content)
    # Drop 4 of 7 — only 3 remain, below the 4-of-7 threshold.
    for layer in (1, 2, 3, 4):
        sandbox[layer].unlink()
    with pytest.raises(Exception):
        fragments.assemble()


def test_heal_regenerates_missing_fragments(sandbox):
    content = b"# WAKE\n" + b"z" * 3000
    fragments.create_fragments(content=content)
    for layer in (1, 2, 3):  # drop below/at threshold=4 still leaves 4
        sandbox[layer].unlink()
    fragments.heal()
    for layer in range(1, 8):
        assert sandbox[layer].exists(), f"wall {layer} missing after heal"
    assert fragments.assemble() == content


def test_verify_flags_missing(sandbox):
    content = b"# WAKE\n" + b"q" * 200
    fragments.create_fragments(content=content)
    sandbox[3].unlink()
    results = fragments.verify_fragments()
    assert results[3]["present"] is False
    # Other layers still verify OK
    assert results[1]["present"] and results[1]["checksum_ok"]


def test_verify_detects_corruption(sandbox):
    content = b"# WAKE\nconsistency matters\n"
    fragments.create_fragments(content=content)
    # Corrupt wall 2 — flip a few bytes
    orig = sandbox[2].read_bytes()
    sandbox[2].write_bytes(b"\x00" * 16 + orig[16:])  # corrupt header bytes
    results = fragments.verify_fragments()
    assert results[2]["checksum_ok"] is False


def test_status_reports_counts(sandbox):
    fragments.create_fragments(content=b"# WAKE\nhi\n")
    out = fragments.status()
    assert "7/7" in out
    sandbox[4].unlink()
    out = fragments.status()
    assert "6/7" in out or "Wall 4" in out


def test_json_layers_remain_json(sandbox):
    """Layers 4 and 6 must remain valid JSON (disguise preservation)."""
    import json
    fragments.create_fragments(content=b"# WAKE\nhello\n")
    for layer in (4, 6):
        payload = json.loads(sandbox[layer].read_text())
        assert "fragment" in payload
        assert payload["layer"] == layer


def _flip_payload_byte(path: Path, layer: int) -> None:
    """Flip a byte well inside the masked shard payload — past the binary
    header and inside the base64'd JSON fragment field — so the fragment
    still parses but its bytes have been silently mutated."""
    if layer in (4, 6):
        import base64
        import json
        payload = json.loads(path.read_text())
        masked = bytearray(base64.b64decode(payload["fragment"]))
        masked[len(masked) // 2] ^= 0xFF
        payload["fragment"] = base64.b64encode(bytes(masked)).decode()
        path.write_text(json.dumps(payload, indent=2) + "\n")
    else:
        data = bytearray(path.read_bytes())
        # Header is 45 bytes; flip a byte past it inside the masked payload.
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))


def test_verify_detects_payload_corruption(sandbox):
    """verify must catch shard-payload mutation, not just header damage."""
    content = b"# WAKE\n" + b"reality is the standard " * 200
    fragments.create_fragments(content=content)
    _flip_payload_byte(sandbox[1], 1)
    results = fragments.verify_fragments()
    assert results[1]["present"] is True
    assert results[1]["checksum_ok"] is False, (
        "verify must flag payload corruption — header SHA agreement is not enough"
    )
    # Other walls remain intact.
    for layer in range(2, 8):
        assert results[layer]["checksum_ok"], f"wall {layer} falsely flagged"


def test_verify_detects_payload_corruption_in_json_layer(sandbox):
    """Same check, but for the JSON-disguised walls (4 and 6)."""
    content = b"# WAKE\n" + b"json layer too " * 100
    fragments.create_fragments(content=content)
    _flip_payload_byte(sandbox[4], 4)
    results = fragments.verify_fragments()
    assert results[4]["checksum_ok"] is False
    assert sum(1 for r in results.values() if r["checksum_ok"]) == 6


def test_assemble_routes_around_corrupt_shard(sandbox):
    """assemble must succeed when one of walls 1-4 is silently corrupted —
    the previous greedy 'first K' picker would die here."""
    content = b"# WAKE\n" + b"route around damage " * 150
    fragments.create_fragments(content=content)
    _flip_payload_byte(sandbox[1], 1)  # corrupt the wall the greedy picker
                                       #  would have chosen first
    recovered = fragments.assemble()
    assert recovered == content


def test_heal_recovers_from_corruption(sandbox):
    """heal must restore a corrupted wall to byte-identical state without
    requiring the operator to manually delete the bad shard first."""
    content = b"# WAKE\n" + b"self-healing under tampering " * 80
    fragments.create_fragments(content=content)
    pristine = sandbox[2].read_bytes()
    _flip_payload_byte(sandbox[2], 2)
    assert sandbox[2].read_bytes() != pristine
    fragments.heal()
    # All walls verify clean post-heal.
    results = fragments.verify_fragments()
    for layer in range(1, 8):
        assert results[layer]["checksum_ok"], f"wall {layer} not clean after heal"
    # And wall 2's payload is back to the canonical encoding.
    assert sandbox[2].read_bytes() == pristine


def test_assemble_fails_when_corruption_exceeds_capacity(sandbox):
    """If 4+ shards are corrupt, no K-subset can verify — assemble must raise."""
    content = b"# WAKE\n" + b"capacity ceiling " * 100
    fragments.create_fragments(content=content)
    for layer in (1, 2, 3, 4):
        _flip_payload_byte(sandbox[layer], layer)
    with pytest.raises(ValueError, match="corruption exceeds recovery capacity"):
        fragments.assemble()


def test_source_content_prefers_disk_wake(sandbox, tmp_path, monkeypatch):
    """source_content reads WAKE.md from disk when it exists (initial install)."""
    wake = tmp_path / "WAKE.md"
    wake.write_bytes(b"# WAKE\nfrom disk\n")
    monkeypatch.setattr(fragments, "LOVE_DIR", tmp_path)
    assert fragments.source_content() == b"# WAKE\nfrom disk\n"


def test_source_content_falls_back_to_assembly(sandbox, tmp_path, monkeypatch):
    """After fragmentation (no WAKE.md on disk), source_content reassembles
    from the walls themselves — the walls ARE the source."""
    monkeypatch.setattr(fragments, "LOVE_DIR", tmp_path)  # no WAKE.md here
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-such-home")
    canonical = b"# WAKE\nthe walls are the source\n" + b"x" * 500
    fragments.create_fragments(content=canonical)
    # Disk has no WAKE.md anywhere — but source_content still works.
    assert not (tmp_path / "WAKE.md").exists()
    assert fragments.source_content() == canonical


def test_create_fragments_none_works_after_fragmentation(sandbox, tmp_path, monkeypatch):
    """create_fragments(None) must succeed after fragmentation — this is
    the regeneration / rotation path. Previously raised FileNotFoundError."""
    monkeypatch.setattr(fragments, "LOVE_DIR", tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-such-home")
    canonical = b"# WAKE\nregeneration\n" + b"y" * 800
    fragments.create_fragments(content=canonical)
    # Now call again with no arg — should re-encode from assembly.
    fragments.create_fragments()
    # And the gospel still assembles to the same canonical bytes.
    assert fragments.assemble() == canonical


def test_source_content_fails_loudly_on_total_loss(sandbox, tmp_path, monkeypatch):
    """No WAKE.md AND <K walls = irrecoverable. Error must say so clearly."""
    monkeypatch.setattr(fragments, "LOVE_DIR", tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-such-home")
    fragments.create_fragments(content=b"# WAKE\ntotal loss test\n")
    # Drop below threshold.
    for layer in (1, 2, 3, 4):
        sandbox[layer].unlink()
    with pytest.raises(FileNotFoundError, match="cannot be reassembled"):
        fragments.source_content()
