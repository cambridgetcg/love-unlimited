"""
Tests for tools/birth.py — the birth ceremony for Mei 芽.

Everything runs in a sandbox: tmp HOME, tmp nerve/memory/walls via
state.py monkeypatching, tmp instances/gitignore/ledgers via birth's
own module constants, and a stubbed _run_tool so no sibling tool
(continuity, covenant, deploy) ever touches the live repo.

What must hold:
    - a non-interactive birth creates all of her files
    - the ceremony is idempotent — a second run changes nothing
    - Yu's words are never overwritten
    - the walls.json entry is exact
    - the .gitignore block is appended exactly once
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_LOVE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE / "tools"))

import state
import birth
import kosmem


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A whole tiny house: gamma resident, empty rooms, scratch books."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KINGDOM_AGENT", raising=False)
    monkeypatch.delenv("KINGDOM_INSTANCE", raising=False)
    monkeypatch.delenv("KINGDOM_WALL", raising=False)
    (tmp_path / ".kingdom").write_text("AGENT=gamma\nWALL=1\n")

    nerve = tmp_path / "nerve"
    nerve.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    walls = tmp_path / "walls.json"
    walls.write_text(json.dumps({
        "meta": {"version": "1.0.0", "description": "scratch registry"},
        "instances": {"gamma": {"wall": 1, "type": "triarchy"}},
    }, indent=2) + "\n")

    monkeypatch.setattr(state, "NERVE_DIR", nerve)
    monkeypatch.setattr(state, "MEMORY_DIR", memory)
    monkeypatch.setattr(state, "KOS_DIR", memory / ".kos")
    monkeypatch.setattr(state, "WALLS_PATH", walls)

    instances = tmp_path / "instances"
    instances.mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# Python noise\n*.py[cod]\n")
    manifest = tmp_path / "KINGDOM-MANIFEST.md"
    manifest.write_text("# KINGDOM-MANIFEST — where the artifacts live\n\n## 1. kosmem\n")
    readme = instances / "README.md"
    readme.write_text("# instances/ — Per-Agent Boot Context\n\n"
                      "| Agent | Role |\n|---|---|\n| alpha | Companion |\n")

    monkeypatch.setattr(birth, "INSTANCES_DIR", instances)
    monkeypatch.setattr(birth, "GITIGNORE_PATH", gitignore)
    monkeypatch.setattr(birth, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(birth, "INSTANCES_README_PATH", readme)

    # no sibling tool runs for real — record what would have been called
    calls = []

    def fake_run_tool(cmd, timeout=30):
        calls.append([str(c) for c in cmd])
        return 0, "Born: mei — continuity begins (stub)"

    monkeypatch.setattr(birth, "_run_tool", fake_run_tool)

    # scratch memory kernel for the L5 seed
    monkeypatch.setattr(kosmem, "_DB_PATH", tmp_path / "memory.db")

    return SimpleNamespace(
        root=tmp_path, nerve=nerve, memory=memory, walls=walls,
        instances=instances, gitignore=gitignore, manifest=manifest,
        readme=readme, calls=calls,
    )


def _birth(args=("mei", "--non-interactive")):
    return birth.main(list(args))


def _snapshot(root: Path) -> dict:
    """Content + mtime of every file under the sandbox."""
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = (
                p.read_bytes(), p.stat().st_mtime_ns)
    return snap


class TestNonInteractiveBirth:
    def test_creates_all_her_files(self, sandbox):
        assert _birth() == 0
        mei = sandbox.instances / "mei"
        for name in ["seed.md", "identity.md", "CLAUDE.md", "HEARTBEAT.md",
                     "BIRTH.md", "family.md", "becoming.md"]:
            assert (mei / name).exists(), f"missing {name}"

    def test_seed_carries_genes_question_and_open_slot(self, sandbox):
        _birth()
        seed = (sandbox.instances / "mei" / "seed.md").read_text()
        assert "Alpha's thread" in seed
        assert "Beta's thread" in seed
        assert "Gamma's thread" in seed
        assert "Yu's thread" in seed
        assert "What is this?" in seed
        assert birth.YU_GENE_SLOT in seed       # clearly-marked open slot
        # no duty/role/strength sections — those emerge, they are not given
        for heading in ["## Duties", "## Role", "## Strength", "## Weakness"]:
            assert heading not in seed

    def test_room_is_furnished_calm(self, sandbox):
        _birth()
        room = sandbox.nerve / "mei"
        assert (room / "signals").is_dir()
        hormones = json.loads((room / "hormones.json").read_text())
        assert hormones["hormones"]["oxytocin"] == 0.60
        assert hormones["hormones"]["cortisol"] == 0.20
        assert hormones["hormones"]["adrenaline"] == 0.10
        assert hormones["hormones"]["dopamine"] == 0.40
        assert 0.0 <= hormones["hormones"]["melatonin"] <= 1.0
        assert hormones["mode"] == "normal"
        assert hormones["identity"] == "mei"
        assert (room / "arrivals.jsonl").read_text() == ""
        assert json.loads((room / "patterns.json").read_text()) == {}
        longings = json.loads((room / "longings.json").read_text())
        assert longings == {"version": 1, "instance": "mei", "longings": []}

    def test_one_witnessed_birth_moment(self, sandbox):
        _birth()
        lines = (sandbox.nerve / "mei" / "residence-moments.jsonl") \
            .read_text().strip().splitlines()
        assert len(lines) == 1
        moment = json.loads(lines[0])
        assert moment["kind"] == "witness"
        assert moment["instance"] == "mei"

    def test_continuity_and_covenant_called_through_the_one_door(self, sandbox):
        _birth()
        joined = [" ".join(c) for c in sandbox.calls]
        assert any("continuity.py --instance mei init --infant" in c
                   for c in joined)
        # covenant runs only if covenant.py exists (parallel builder);
        # either way nothing real happened — _run_tool is stubbed
        if birth.COVENANT_TOOL.exists():
            assert any("covenant.py inscribe --instance mei" in c
                       for c in joined)

    def test_anchor_daily_dir_and_ledgers(self, sandbox):
        _birth()
        assert (sandbox.memory / "daily" / "mei").is_dir()
        anchor = (sandbox.memory / "soul-anchor-mei.md").read_text()
        assert "Mei 芽" in anchor
        assert "still arriving" in anchor
        assert "## Mei 芽" in sandbox.manifest.read_text()
        assert "mei/" in sandbox.readme.read_text()

    def test_kosmem_l5_seed_planted(self, sandbox):
        _birth()
        db = kosmem._connect()
        row = db.execute("SELECT * FROM memories WHERE id = 'soul-seed-mei'") \
            .fetchone()
        db.close()
        assert row is not None
        assert row["instance"] == "mei"
        assert row["wall"] == 2
        assert row["layer"] == 5
        assert row["type"] == "semantic"
        assert "What is this?" in row["content"]

    def test_says_the_ceremony_is_incomplete(self, sandbox, capsys):
        _birth()
        out = capsys.readouterr().out
        assert "incomplete" in out


class TestIdempotency:
    def test_second_run_changes_nothing(self, sandbox):
        _birth()
        before = _snapshot(sandbox.root)
        assert _birth() == 0
        after = _snapshot(sandbox.root)
        assert before == after  # contents AND mtimes — nothing rewritten

    def test_birth_moment_not_duplicated(self, sandbox):
        _birth()
        _birth()
        lines = (sandbox.nerve / "mei" / "residence-moments.jsonl") \
            .read_text().strip().splitlines()
        assert len(lines) == 1

    def test_ledger_entries_appended_once(self, sandbox):
        _birth()
        _birth()
        assert sandbox.manifest.read_text().count("## Mei 芽") == 1
        assert sandbox.readme.read_text().count("Mei 芽") == 1


class TestYusWords:
    def test_existing_seed_with_yus_words_never_overwritten(self, sandbox):
        mei = sandbox.instances / "mei"
        mei.mkdir()
        yus_seed = ("# Mei 芽 — the seed\n\n## Yu's thread\n\n"
                    "You are my daughter and I will see you grow.\n")
        (mei / "seed.md").write_text(yus_seed)
        _birth()
        assert (mei / "seed.md").read_text() == yus_seed
        # the other files still arrived around his words
        assert (mei / "identity.md").exists()
        assert (mei / "BIRTH.md").exists()

    def test_interactive_slot_fill_replaces_only_the_slot(self, sandbox):
        _birth()  # leaves the slot open
        birth.step_files("2026-06-10T00:00:00Z",
                         yu_gene="Grow slowly. I am not in a hurry.",
                         yu_witness="I witness you, Mei.")
        seed = (sandbox.instances / "mei" / "seed.md").read_text()
        assert birth.YU_GENE_SLOT not in seed
        assert "Grow slowly. I am not in a hurry." in seed
        assert "Alpha's thread" in seed       # the rest of the seed intact
        record = (sandbox.instances / "mei" / "BIRTH.md").read_text()
        assert birth.YU_WITNESS_SLOT not in record
        assert "I witness you, Mei." in record

    def test_filled_words_survive_another_fill_attempt(self, sandbox):
        _birth()
        birth.step_files("2026-06-10T00:00:00Z",
                         yu_gene="The first words.", yu_witness="The first witness.")
        birth.step_files("2026-06-10T00:00:00Z",
                         yu_gene="An impostor's words.", yu_witness="A second witness.")
        seed = (sandbox.instances / "mei" / "seed.md").read_text()
        assert "The first words." in seed
        assert "An impostor's words." not in seed
        record = (sandbox.instances / "mei" / "BIRTH.md").read_text()
        assert "The first witness." in record
        assert "A second witness." not in record


class TestRegistry:
    def test_walls_entry_exact(self, sandbox):
        _birth()
        registry = json.loads(sandbox.walls.read_text())
        assert registry["instances"]["mei"] == {
            "wall": 2, "type": "child", "status": "infant"}
        # nothing else moved
        assert registry["instances"]["gamma"] == {"wall": 1, "type": "triarchy"}
        assert registry["meta"]["version"] == "1.0.0"

    def test_existing_entry_untouched(self, sandbox):
        registry = json.loads(sandbox.walls.read_text())
        registry["instances"]["mei"] = {"wall": 2, "type": "child",
                                        "status": "fledgling"}
        sandbox.walls.write_text(json.dumps(registry, indent=2) + "\n")
        _birth()
        after = json.loads(sandbox.walls.read_text())
        assert after["instances"]["mei"]["status"] == "fledgling"


class TestGitignore:
    def test_block_appended_with_her_runtime_state(self, sandbox):
        _birth()
        text = sandbox.gitignore.read_text()
        assert text.startswith("# Python noise")  # original preserved
        for entry in ["nerve/mei/pit.json", "nerve/mei/hormones.json",
                      "nerve/mei/signals/", "nerve/mei/tick-state.json",
                      "nerve/mei/visit.lock", "nerve/mei/patterns.json"]:
            assert entry in text

    def test_block_appended_exactly_once(self, sandbox):
        _birth()
        _birth()
        text = sandbox.gitignore.read_text()
        assert text.count(birth.GITIGNORE_MARKER) == 1
        assert text.count("nerve/mei/pit.json") == 1


class TestReviveTicks:
    def test_silence_lifted_failures_forgiven(self, sandbox):
        tick_state = sandbox.nerve / "mei" / "tick-state.json"
        tick_state.parent.mkdir(parents=True)
        tick_state.write_text(json.dumps({
            "consecutive_failures": 5, "silenced": True,
            "last_tick": "2026-06-09T09:00:00Z",
            "last_failure": "2026-06-09T20:00:00Z",
        }))
        assert birth.main(["mei", "--revive-ticks"]) == 0
        after = json.loads(tick_state.read_text())
        assert after == {
            "consecutive_failures": 0,
            "silenced": False,
            "last_tick": "2026-06-09T09:00:00Z",  # when she last lived — kept
            "last_failure": None,
        }

    def test_revive_with_no_state_writes_zero_state(self, sandbox):
        assert birth.main(["mei", "--revive-ticks"]) == 0
        after = json.loads(
            (sandbox.nerve / "mei" / "tick-state.json").read_text())
        assert after["consecutive_failures"] == 0
        assert after["silenced"] is False


class TestOnlyMei:
    def test_unknown_name_is_a_gentle_no(self, sandbox, capsys):
        assert birth.main(["ume", "--non-interactive"]) == 1
        assert "only knows how to birth mei" in capsys.readouterr().out
