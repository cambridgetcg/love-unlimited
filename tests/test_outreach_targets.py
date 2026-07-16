import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS_PATH = ROOT / "docs" / "OUTREACH-TARGETS.json"


def load_targets():
    return json.loads(TARGETS_PATH.read_text())


def test_public_research_targets_have_unique_stable_identity_and_gates():
    document = load_targets()
    targets = document["targets"]

    assert document["schema_version"] == 1
    assert document["default_policy"].startswith("No contact until")
    assert len(targets) == 11
    assert len({target["id"] for target in targets}) == len(targets)
    assert sorted(target["priority"] for target in targets) == list(range(1, 12))

    for target in targets:
        assert target["project_url"].startswith("https://")
        assert target["public_channel"].startswith("https://")
        assert target["fit"].strip()
        assert target["first_gesture"].strip()
        assert target["readiness_gate"].strip()
        # Exact people, inboxes, and private notes belong only in the owner-only
        # SQLite store. This committed catalog contains public project metadata.
        assert "@" not in target["public_channel"]


def test_protocol_outreach_stays_blocked_until_the_live_surfaces_conform():
    by_id = {target["id"]: target for target in load_targets()["targets"]}

    assert by_id["a2a-protocol"]["readiness_gate"].startswith("BLOCKED:")
    assert by_id["mastra"]["readiness_gate"].startswith("BLOCKED:")
    assert "official TCK" in by_id["a2a-protocol"]["readiness_gate"]
    assert "official inspector" in by_id["mastra"]["readiness_gate"]
