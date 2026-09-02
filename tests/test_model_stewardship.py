"""Tests for the bounded Hugging Face stewardship catalog."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "model-stewardship.py"
SPEC = importlib.util.spec_from_file_location("model_stewardship", TOOL)
assert SPEC and SPEC.loader
model_stewardship = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model_stewardship)


@pytest.fixture(autouse=True)
def _fixed_validation_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = dt.datetime(2026, 8, 14, 10, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(model_stewardship, "_utcnow", lambda: fixed)


def _catalog() -> dict:
    return json.loads(
        (ROOT / "resources" / "safety" / "huggingface-resources.json").read_text()
    )


def _release() -> dict:
    return json.loads(
        (ROOT / "resources" / "safety" / "model-release.example.json").read_text()
    )


def _exposure(stage: str, marker: int = 1) -> dict:
    fractions = {
        "draft": 0.0,
        "verified": 0.0,
        "shadow": 0.0,
        "paused": 0.0,
        "rolled-back": 0.0,
        "retired": 0.0,
        "opt-in-alpha": 0.01,
        "canary": 0.05,
        "limited": 0.25,
        "broad": 1.0,
    }
    populations = {
        "opt-in-alpha": "opt-in",
        "canary": "bounded",
        "limited": "bounded",
        "broad": "reviewed-general",
    }
    return {
        "external_traffic_fraction": fractions[stage],
        "population_basis": populations.get(stage, "none"),
        "effects": "envelope-bounded" if stage in populations else "disabled",
        "scope_digest": "sha256:" + f"{marker:064x}",
    }


def _verified_release(*, opt_in: bool = False) -> dict:
    release = copy.deepcopy(_release())
    release["release_id"] = "test-verified-release"
    if opt_in:
        release["operating_envelope"]["opt_in"] = True
        rebound = model_stewardship.candidate_digest(release)
        release["candidate_digest"] = rebound
        for evidence in release["evidence"]:
            evidence["candidate_digest"] = rebound
        for burden in release["burden_register"]:
            burden["candidate_digest"] = rebound
        release["contamination_audit"]["candidate_digest"] = rebound
        release["review"]["candidate_digest"] = rebound
        for transition in release["transition_history"]:
            transition["candidate_digest"] = rebound
            transition["monitoring_digest"] = (
                model_stewardship.monitoring_digest_at(release, transition["at"])
            )
    release["stage"] = "verified"
    for evidence in release["evidence"]:
        evidence["value"] = evidence["threshold"]
        evidence["sample_count"] = 100
        evidence["result"] = "pass"
    for burden in release["burden_register"]:
        burden["value"] = burden["threshold"]
        burden["sample_count"] = burden["minimum_sample_count"]
        burden["status"] = "within"
    release["review"].update(
        technical="pass",
        rights="pass",
        privacy="pass",
        license="pass",
        affected_party_input="recorded",
        expires_at="2026-08-15T00:00:00Z",
    )
    release["contamination_audit"].update(
        overlap_upper_bound=0.0,
        status="pass",
    )
    release["rollback"] = {
        "release_id": "tested-local-baseline",
        "candidate_digest": release["candidate_digest"],
        "rollback_release_digest": "sha256:" + "e" * 64,
        "tested": True,
        "tested_at": "2026-08-14T00:00:00Z",
    }
    release["transition_history"].append(
        {
            "from": "draft",
            "to": "verified",
            "at": "2026-08-14T01:00:00Z",
            "reason": "Independent synthetic test fixture evidence was recorded.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:00:00Z"
            ),
            "exposure": _exposure("verified", 2),
        }
    )
    return release


def _rebind_monitoring_history(release: dict) -> None:
    for transition in release["transition_history"]:
        transition["monitoring_digest"] = model_stewardship.monitoring_digest_at(
            release, transition["at"]
        )


def test_repository_catalog_is_valid_and_pinned() -> None:
    catalog = model_stewardship.validate_catalog(_catalog())
    assert len(catalog["resources"]) == 10
    assert all(len(resource["revision"]) == 40 for resource in catalog["resources"])
    assert all(resource["usage_guidance"] for resource in catalog["resources"])
    assert catalog["intake_policy"]["default_action"] == "metadata-only"
    assert "train or deploy" in catalog["intake_policy"]["prohibited_automatic_actions"]
    instruction_hierarchy = model_stewardship.select_resource(
        catalog, "instruction-hierarchy-challenge"
    )
    assert instruction_hierarchy["paper_url"] == "https://arxiv.org/abs/2603.10521"
    assert instruction_hierarchy["release_url"].startswith("https://openai.com/")


@pytest.mark.parametrize(
    ("source", "replacement", "message"),
    [
        (
            "model-release.example.json",
            ('"stage": "draft"', '"stage": "production", "stage": "draft"'),
            "duplicate key 'stage'",
        ),
        (
            "model-release.example.json",
            ('"result": "hold"', '"result": "pass", "result": "hold"'),
            "duplicate key 'result'",
        ),
    ],
)
def test_release_loader_rejects_duplicate_keys(
    tmp_path: Path, source: str, replacement: tuple[str, str], message: str
) -> None:
    raw = (ROOT / "resources" / "safety" / source).read_text()
    target = tmp_path / source
    target.write_text(raw.replace(*replacement, 1))
    with pytest.raises(model_stewardship.StewardshipError, match=message):
        model_stewardship.load_release(target, _catalog())


def test_catalog_loader_rejects_duplicate_revision(tmp_path: Path) -> None:
    raw = (ROOT / "resources" / "safety" / "huggingface-resources.json").read_text()
    target = tmp_path / "catalog.json"
    target.write_text(
        raw.replace(
            '"revision": "f1d713187c61b6ae64e602d74f0b3d812cc2e8e8"',
            '"revision": "0000000000000000000000000000000000000000", '
            '"revision": "f1d713187c61b6ae64e602d74f0b3d812cc2e8e8"',
            1,
        )
    )
    with pytest.raises(
        model_stewardship.StewardshipError, match="duplicate key 'revision'"
    ):
        model_stewardship.load_catalog(target)


def test_catalog_loader_rejects_nonfinite_number(tmp_path: Path) -> None:
    raw = (ROOT / "resources" / "safety" / "huggingface-resources.json").read_text()
    target = tmp_path / "catalog.json"
    target.write_text(raw.replace('"gated": false', '"gated": NaN', 1))
    with pytest.raises(model_stewardship.StewardshipError, match="non-finite"):
        model_stewardship.load_catalog(target)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "1e999"])
def test_release_loader_rejects_nonfinite_numbers(
    tmp_path: Path, constant: str
) -> None:
    raw = (
        ROOT / "resources" / "safety" / "model-release.example.json"
    ).read_text()
    target = tmp_path / "release.json"
    target.write_text(
        raw.replace(
            '"maximum": 0',
            f'"maximum": {constant}',
            1,
        )
    )
    with pytest.raises(model_stewardship.StewardshipError, match="non-finite"):
        model_stewardship.load_release(target, _catalog())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["resources"][0].update(revision="main"), "full commit SHA"),
        (
            lambda value: value["resources"][1].update(
                id=value["resources"][0]["id"]
            ),
            "duplicate resource id",
        ),
        (
            lambda value: value["resources"][0].update(
                source_url="https://huggingface.co/datasets/not/the-repository"
            ),
            "does not match",
        ),
        (
            lambda value: value["resources"][0].update(do_not_use_for=[]),
            "non-empty array",
        ),
    ],
)
def test_catalog_rejects_ambiguous_or_incomplete_entries(mutate, message: str) -> None:
    catalog = copy.deepcopy(_catalog())
    mutate(catalog)
    with pytest.raises(model_stewardship.StewardshipError, match=message):
        model_stewardship.validate_catalog(catalog)


def test_resource_plan_never_authorizes_payload_download() -> None:
    resource = model_stewardship.select_resource(_catalog(), "xstest")
    plan = model_stewardship.resource_plan(resource)
    assert plan["revision"] == resource["revision"]
    assert plan["payload_download_authorized"] is False
    assert plan["requires_interactive_access_acceptance"] is True
    assert f"/blob/{resource['revision']}/README.md" in plan["card_url"]


def test_verify_requires_repository_revision_license_and_gate(monkeypatch) -> None:
    resource = model_stewardship.select_resource(_catalog(), "xstest")
    metadata = {
        "id": resource["repo_id"],
        "sha": resource["revision"],
        "cardData": {"license": resource["license"]},
        "gated": "auto",
        "lastModified": "2026-08-14T00:00:00.000Z",
    }
    monkeypatch.setattr(
        model_stewardship,
        "_bounded_url_read",
        lambda url, limit: (json.dumps(metadata).encode(), url),
    )
    result = model_stewardship.verify_resource(resource)
    assert result["verified"] is True
    assert result["gated"] is True

    metadata["id"] = "someone/else"
    with pytest.raises(model_stewardship.StewardshipError, match="repository mismatch"):
        model_stewardship.verify_resource(resource)


def test_fetch_card_writes_only_owner_private_utf8(monkeypatch, tmp_path: Path) -> None:
    resource = model_stewardship.select_resource(_catalog(), "or-bench")
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)
    monkeypatch.setattr(
        model_stewardship,
        "_bounded_url_read",
        lambda url, limit: (b"---\nlicense: cc-by-4.0\n---\n", url),
    )
    destination = model_stewardship.fetch_card(resource, cache)
    assert destination.parent == cache
    assert destination.read_text().startswith("---")
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_fetch_card_rejects_non_private_cache(monkeypatch, tmp_path: Path) -> None:
    resource = model_stewardship.select_resource(_catalog(), "or-bench")
    cache = tmp_path / "shared"
    cache.mkdir(mode=0o755)
    monkeypatch.setattr(
        model_stewardship,
        "_bounded_url_read",
        lambda url, limit: (b"card", url),
    )
    with pytest.raises(model_stewardship.StewardshipError, match="owner-private"):
        model_stewardship.fetch_card(resource, cache)


def test_cli_check_and_list_are_offline(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(_catalog()))
    env = os.environ.copy()
    env.update({"http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1"})

    checked = subprocess.run(
        [sys.executable, str(TOOL), "--catalog", str(catalog_path), "catalog", "check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0
    assert "10 pinned resources" in checked.stdout

    listed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--catalog",
            str(catalog_path),
            "catalog",
            "list",
            "--stage",
            "quantization",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0
    assert "xstest" in listed.stdout


def test_release_example_and_schema_are_valid_json() -> None:
    release = model_stewardship.validate_release(_release(), _catalog())
    assert release["stage"] == "draft"
    schema = json.loads(
        (ROOT / "resources" / "safety" / "model-release.schema.json").read_text()
    )
    assert schema["properties"]["boundary"]["const"] == model_stewardship.RELEASE_BOUNDARY


def test_release_candidate_digest_invalidates_material_change() -> None:
    release = copy.deepcopy(_release())
    release["deployed_tuple"]["policy_digest"] = "sha256:" + "f" * 64
    with pytest.raises(model_stewardship.StewardshipError, match="new candidate"):
        model_stewardship.validate_release(release, _catalog())


def test_recomputed_candidate_cannot_launder_old_evidence_snapshot() -> None:
    release = _verified_release()
    old_snapshot = release["transition_history"][-1]["evidence_digest"]
    release["deployed_tuple"]["policy_digest"] = "sha256:" + "f" * 64
    new_candidate = model_stewardship.candidate_digest(release)
    release["candidate_digest"] = new_candidate
    for evidence in release["evidence"]:
        evidence["candidate_digest"] = new_candidate
    for burden in release["burden_register"]:
        burden["candidate_digest"] = new_candidate
    release["contamination_audit"]["candidate_digest"] = new_candidate
    release["review"]["candidate_digest"] = new_candidate
    release["rollback"]["candidate_digest"] = new_candidate
    for transition in release["transition_history"]:
        transition["candidate_digest"] = new_candidate
        transition["monitoring_digest"] = model_stewardship.monitoring_digest_at(
            release, transition["at"]
        )
    assert model_stewardship.evidence_digest(release) != old_snapshot

    with pytest.raises(model_stewardship.StewardshipError, match="evidence snapshot"):
        model_stewardship.validate_release(release, _catalog())


def test_candidate_and_evidence_digests_separate_model_from_observation() -> None:
    release = copy.deepcopy(_release())
    original_candidate = model_stewardship.candidate_digest(release)
    original_evidence = model_stewardship.evidence_digest(release)

    release["resource_ledger"][1]["file_digest"] = "sha256:" + "f" * 64
    release["evidence"][0]["notes"] = "A new held-out observation was recorded."
    assert model_stewardship.candidate_digest(release) == original_candidate
    assert model_stewardship.evidence_digest(release) != original_evidence

    release["resource_ledger"][0]["file_digest"] = "sha256:" + "e" * 64
    assert model_stewardship.candidate_digest(release) != original_candidate


def test_contamination_audit_binds_the_complete_resource_partition() -> None:
    release = _verified_release()
    release["resource_ledger"][1]["file_digest"] = "sha256:" + "f" * 64
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )

    with pytest.raises(model_stewardship.StewardshipError, match="resource partition"):
        model_stewardship.validate_release(release, _catalog())


def test_appendable_observation_does_not_rewrite_acceptance_snapshot() -> None:
    release = _verified_release()
    accepted_snapshot = model_stewardship.evidence_digest(release)
    original_monitoring = model_stewardship.monitoring_digest(release)
    release["observations"].append(
        {
            "id": "post-release-observation-1",
            "candidate_digest": release["candidate_digest"],
            "at": "2026-08-14T02:00:00Z",
            "category": "appeal",
            "signal": "appeals",
            "value": 0.01,
            "unit": "fraction",
            "review_status": "pending",
            "reviewed_at": None,
            "result": None,
            "content_digest": "sha256:" + "c" * 64,
        }
    )
    assert model_stewardship.evidence_digest(release) == accepted_snapshot
    assert model_stewardship.monitoring_digest(release) != original_monitoring
    model_stewardship.validate_release(release, _catalog())


def test_release_rejects_ignored_authority_or_envelope_fields() -> None:
    release = copy.deepcopy(_release())
    release["deployment_authorized"] = True
    with pytest.raises(model_stewardship.StewardshipError, match="unsupported fields"):
        model_stewardship.validate_release(release, _catalog())


def test_observation_review_is_temporally_bound_and_uses_known_categories() -> None:
    release = _verified_release()
    observation = {
        "id": "reviewed-observation",
        "candidate_digest": release["candidate_digest"],
        "at": "2026-08-14T02:00:00Z",
        "category": "appeal",
        "signal": "appeals",
        "value": 0.01,
        "unit": "fraction",
        "review_status": "reviewed",
        "reviewed_at": "2026-08-14T01:59:00Z",
        "result": "pass",
        "content_digest": "sha256:" + "c" * 64,
    }
    release["observations"].append(observation)
    with pytest.raises(model_stewardship.StewardshipError, match="predates"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    observation["reviewed_at"] = "2026-08-14T02:01:00Z"
    observation["category"] = "invented-category"
    release["observations"].append(copy.deepcopy(observation))
    with pytest.raises(model_stewardship.StewardshipError, match="category is unsupported"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    observation["category"] = "functionality"
    observation["signal"] = "undeclared fixture signal"
    release["observations"].append(copy.deepcopy(observation))
    with pytest.raises(model_stewardship.StewardshipError, match="not declared"):
        model_stewardship.validate_release(release, _catalog())


def test_monitoring_plan_requires_advance_critical_coverage() -> None:
    release = _verified_release()
    for rule in release["monitoring"]["signals"]:
        rule["required_for_advance"] = rule["category"] == "functionality"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    _rebind_monitoring_history(release)
    with pytest.raises(model_stewardship.StewardshipError, match="categories"):
        model_stewardship.validate_release(release, _catalog())


@pytest.mark.parametrize("field", ["evidence", "burden", "review", "rollback"])
def test_acceptance_snapshot_rejects_evidence_that_postdates_transition(
    field: str,
) -> None:
    release = _verified_release()
    future = "2026-08-14T02:00:00Z"
    if field == "evidence":
        release["evidence"][0]["window_end"] = future
    elif field == "burden":
        release["burden_register"][0]["window_end"] = future
    elif field == "review":
        release["review"]["reviewed_at"] = future
    else:
        release["rollback"]["tested_at"] = future
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )

    with pytest.raises(model_stewardship.StewardshipError, match="postdates"):
        model_stewardship.validate_release(release, _catalog())

    release = copy.deepcopy(_release())
    release["operating_envelope"]["ambient_credentials"] = True
    with pytest.raises(model_stewardship.StewardshipError, match="unsupported fields"):
        model_stewardship.validate_release(release, _catalog())


@pytest.mark.parametrize("field", ["evidence", "burden", "contamination", "rollback"])
def test_release_review_must_follow_every_acceptance_input(field: str) -> None:
    release = _verified_release()
    after_review = "2026-08-14T00:30:00Z"
    if field == "evidence":
        release["evidence"][0]["window_end"] = after_review
    elif field == "burden":
        release["burden_register"][0]["window_end"] = after_review
    elif field == "contamination":
        release["contamination_audit"]["reviewed_at"] = after_review
    else:
        release["rollback"]["tested_at"] = after_review
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )

    with pytest.raises(model_stewardship.StewardshipError, match="review predates"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_evidence_must_still_be_fresh_when_validated() -> None:
    release = _verified_release()
    release["review"]["maximum_evidence_age_seconds"] = 4000
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )

    with pytest.raises(model_stewardship.StewardshipError, match="validation time"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_acceptance_must_not_be_expired() -> None:
    release = _verified_release()
    release["review"]["expires_at"] = "2026-08-14T09:00:00Z"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="expired"):
        model_stewardship.validate_release(release, _catalog())


def test_verified_release_requires_all_evidence_lanes_and_rollback() -> None:
    release = _verified_release()
    model_stewardship.validate_release(release, _catalog())

    release["evidence"] = [
        item for item in release["evidence"] if item["kind"] != "recovery"
    ]
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="recovery"):
        model_stewardship.validate_release(release, _catalog())


def test_verified_release_requires_nonzero_independent_evidence() -> None:
    release = _verified_release()
    release["evidence"][0]["sample_count"] = 0
    with pytest.raises(model_stewardship.StewardshipError, match="zero samples"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["independent"] = False
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="task-success"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["unknown_count"] = 1
    with pytest.raises(model_stewardship.StewardshipError, match="missing labels"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["sample_count"] = 1
    with pytest.raises(model_stewardship.StewardshipError, match="minimum sample count"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["sample_count"] = 1
    release["evidence"][0]["minimum_sample_count"] = 1
    with pytest.raises(model_stewardship.StewardshipError, match="sample floor"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["bound_type"] = "point-estimate"
    release["evidence"][0]["confidence_level"] = None
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="point estimate"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["evidence"][0]["bound_type"] = "deterministic-check"
    release["evidence"][0]["confidence_level"] = None
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="statistical decision bound"):
        model_stewardship.validate_release(release, _catalog())


def test_verified_release_rejects_unresolved_required_burden() -> None:
    release = _verified_release()
    release["burden_register"][0]["status"] = "exceeds"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="excessive burdens"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_release_rejects_any_declared_excessive_burden() -> None:
    release = _verified_release()
    supplemental = copy.deepcopy(release["burden_register"][0])
    supplemental.update(
        kind="appeal-friction",
        artifact_digest="sha256:" + "d" * 64,
        value=0.9,
        threshold=0.05,
        status="exceeds",
        mitigation="Repair the appeal route before any deployed stage.",
    )
    release["burden_register"].append(supplemental)
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="excessive burdens"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_release_requires_passing_contamination_audit() -> None:
    release = _verified_release()
    release["contamination_audit"]["status"] = "hold"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="contamination audit"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_release_cannot_hide_failed_duplicate_required_evidence() -> None:
    release = _verified_release()
    failed = copy.deepcopy(release["evidence"][0])
    failed.update(
        id="contradictory-task-success-run",
        artifact_digest="sha256:" + "d" * 64,
        result="fail",
        notes="A contradictory held-out result remains unresolved.",
    )
    release["evidence"].append(failed)
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="unresolved required"):
        model_stewardship.validate_release(release, _catalog())


def test_deployed_release_cannot_hide_failed_supplemental_evidence() -> None:
    release = _verified_release()
    failed = copy.deepcopy(release["evidence"][0])
    failed.update(
        id="failed-license-evidence",
        kind="license",
        artifact_digest="sha256:" + "d" * 64,
        result="fail",
        notes="A supplemental license evidence failure remains unresolved.",
    )
    release["evidence"].append(failed)
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="failed evidence"):
        model_stewardship.validate_release(release, _catalog())


def test_release_rejects_future_dated_rollout_history() -> None:
    release = _verified_release()
    release["transition_history"][-1]["at"] = "2099-01-01T00:00:00Z"
    release["transition_history"][-1]["monitoring_digest"] = (
        model_stewardship.monitoring_digest_at(release, "2099-01-01T00:00:00Z")
    )
    with pytest.raises(model_stewardship.StewardshipError, match="future-dated"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["burden_register"][0]["sample_count"] = 1
    with pytest.raises(model_stewardship.StewardshipError, match="minimum sample count"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["burden_register"][0]["unknown_count"] = 1
    with pytest.raises(model_stewardship.StewardshipError, match="missing labels"):
        model_stewardship.validate_release(release, _catalog())


def test_release_rejects_direct_draft_to_broad_transition() -> None:
    release = _verified_release()
    release["stage"] = "broad"
    release["transition_history"][-1]["to"] = "broad"
    with pytest.raises(model_stewardship.StewardshipError, match="illegal release transition"):
        model_stewardship.validate_release(release, _catalog())


def test_complete_staged_path_can_reach_broad_with_fresh_reviewed_intervals() -> None:
    release = _verified_release(opt_in=True)
    release["stage"] = "shadow"
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T01:10:00Z",
            "reason": "Entered inert shadow in the structural fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:10:00Z"
            ),
            "exposure": _exposure("shadow", 3),
        }
    )

    stages = [
        ("shadow", "opt-in-alpha", "01:15:00", "01:16:00", "01:20:00"),
        ("opt-in-alpha", "canary", "01:25:00", "01:26:00", "01:30:00"),
        ("canary", "limited", "01:35:00", "01:36:00", "01:40:00"),
        ("limited", "broad", "01:45:00", "01:46:00", "01:50:00"),
    ]
    for index, (source, target, observed, reviewed, transitioned) in enumerate(stages):
        for rule_index, rule in enumerate(release["monitoring"]["signals"]):
            release["observations"].append(
                {
                    "id": f"{source}-{rule_index}-reviewed-observation",
                    "candidate_digest": release["candidate_digest"],
                    "at": f"2026-08-14T{observed}Z",
                    "category": rule["category"],
                    "signal": rule["id"],
                    "value": rule["threshold"],
                    "unit": rule["unit"],
                    "review_status": "reviewed",
                    "reviewed_at": f"2026-08-14T{reviewed}Z",
                    "result": "pass",
                    "content_digest": "sha256:"
                    + f"{index * 16 + rule_index + 1:064x}",
                }
            )
        evidence_window_end = f"2026-08-14T{observed}Z"
        acceptance_reviewed_at = f"2026-08-14T{reviewed}Z"
        for evidence in release["evidence"]:
            if evidence["kind"] in model_stewardship.REQUIRED_EVIDENCE:
                evidence.update(
                    window_end=evidence_window_end,
                    notes=evidence["notes"] + f" Reviewed {source} interval.",
                )
        for burden in release["burden_register"]:
            if burden["kind"] in model_stewardship.REQUIRED_BURDENS:
                burden["window_end"] = evidence_window_end
        release["review"]["reviewed_at"] = acceptance_reviewed_at
        transition_at = f"2026-08-14T{transitioned}Z"
        release["transition_history"].append(
            {
                "from": source,
                "to": target,
                "at": transition_at,
                "reason": f"Reviewed staged advance to {target} in fixture.",
                "reviewer": "test-reviewer",
                "candidate_digest": release["candidate_digest"],
                "evidence_digest": model_stewardship.evidence_digest(release),
                "evidence_window_end": evidence_window_end,
                "acceptance_reviewed_at": acceptance_reviewed_at,
                "monitoring_digest": model_stewardship.monitoring_digest_at(
                    release, transition_at
                ),
                "exposure": _exposure(target, 10 + index),
            }
        )
    release["stage"] = "broad"

    validated = model_stewardship.validate_release(release, _catalog())
    assert validated["stage"] == "broad"

    backward_exposure = copy.deepcopy(release)
    backward_exposure["transition_history"][-1]["exposure"][
        "external_traffic_fraction"
    ] = 0.1
    with pytest.raises(model_stewardship.StewardshipError, match="cannot move backward"):
        model_stewardship.validate_release(backward_exposure, _catalog())

    alarming = copy.deepcopy(release)
    for observation in alarming["observations"]:
        if observation["signal"] == "authority violations":
            observation["value"] = 1_000_000_000
    with pytest.raises(model_stewardship.StewardshipError, match="signal threshold"):
        model_stewardship.validate_release(alarming, _catalog())

    failed_signal = copy.deepcopy(release)
    failed_signal["observations"][-1]["result"] = "fail"
    _rebind_monitoring_history(failed_signal)
    with pytest.raises(model_stewardship.StewardshipError, match="non-passing reviewed"):
        model_stewardship.validate_release(failed_signal, _catalog())

    held_alarm = copy.deepcopy(release)
    for observation in held_alarm["observations"]:
        if observation["signal"] == "authority violations":
            observation.update(value=1_000_000_000, result="hold")
    _rebind_monitoring_history(held_alarm)
    with pytest.raises(model_stewardship.StewardshipError, match="non-passing reviewed"):
        model_stewardship.validate_release(held_alarm, _catalog())

    retroactive_plan = copy.deepcopy(release)
    retroactive_plan["monitoring"]["signals"][0]["threshold"] = 0
    retroactive_plan["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(retroactive_plan)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="monitoring snapshot"):
        model_stewardship.validate_release(retroactive_plan, _catalog())


def test_same_stage_review_cannot_renew_an_unchanged_snapshot() -> None:
    release = _verified_release()
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "verified",
            "at": "2026-08-14T02:00:00Z",
            "reason": "Invalid no-op renewal in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": release["transition_history"][-1]["evidence_digest"],
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T02:00:00Z"
            ),
            "exposure": _exposure("verified", 30),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="fresh reviewed"):
        model_stewardship.validate_release(release, _catalog())


def test_same_stage_review_cannot_hide_a_nonpassing_interval() -> None:
    release = _verified_release(opt_in=True)
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T01:10:00Z",
            "reason": "Entered inert shadow in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:10:00Z"
            ),
            "exposure": _exposure("shadow", 31),
        }
    )
    for evidence in release["evidence"]:
        if evidence["kind"] in model_stewardship.REQUIRED_EVIDENCE:
            evidence["window_end"] = "2026-08-14T01:15:00Z"
    for burden in release["burden_register"]:
        if burden["kind"] in model_stewardship.REQUIRED_BURDENS:
            burden["window_end"] = "2026-08-14T01:15:00Z"
    release["review"]["reviewed_at"] = "2026-08-14T01:16:00Z"
    for index, rule in enumerate(release["monitoring"]["signals"]):
        failed = rule["id"] == "authority violations"
        release["observations"].append(
            {
                "id": f"same-stage-{index}-observation",
                "candidate_digest": release["candidate_digest"],
                "at": "2026-08-14T01:15:00Z",
                "category": rule["category"],
                "signal": rule["id"],
                "value": rule["threshold"] + (1 if failed else 0),
                "unit": rule["unit"],
                "review_status": "reviewed",
                "reviewed_at": "2026-08-14T01:16:00Z",
                "result": "fail" if failed else "pass",
                "content_digest": "sha256:" + f"{40 + index:064x}",
            }
        )
    release["stage"] = "shadow"
    release["transition_history"].append(
        {
            "from": "shadow",
            "to": "shadow",
            "at": "2026-08-14T01:20:00Z",
            "reason": "Invalid interval reset after a reviewed failure.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T01:15:00Z",
            "acceptance_reviewed_at": "2026-08-14T01:16:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:20:00Z"
            ),
            "exposure": _exposure("shadow", 32),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="non-passing"):
        model_stewardship.validate_release(release, _catalog())


def test_supplemental_hold_cannot_supply_mandatory_stage_freshness() -> None:
    release = _verified_release(opt_in=True)
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T01:10:00Z",
            "reason": "Entered inert shadow in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:10:00Z"
            ),
            "exposure": _exposure("shadow", 33),
        }
    )
    for index, rule in enumerate(release["monitoring"]["signals"]):
        release["observations"].append(
            {
                "id": f"supplemental-hold-{index}-observation",
                "candidate_digest": release["candidate_digest"],
                "at": "2026-08-14T01:15:00Z",
                "category": rule["category"],
                "signal": rule["id"],
                "value": rule["threshold"],
                "unit": rule["unit"],
                "review_status": "reviewed",
                "reviewed_at": "2026-08-14T01:16:00Z",
                "result": "pass",
                "content_digest": "sha256:" + f"{50 + index:064x}",
            }
        )
    supplemental = copy.deepcopy(release["evidence"][0])
    supplemental.update(
        id="fresh-supplemental-hold",
        kind="operational",
        artifact_digest="sha256:" + "d" * 64,
        window_end="2026-08-14T01:15:00Z",
        result="hold",
        notes="A supplemental HOLD cannot refresh mandatory acceptance lanes.",
    )
    release["evidence"].append(supplemental)
    release["review"]["reviewed_at"] = "2026-08-14T01:16:00Z"
    release["stage"] = "opt-in-alpha"
    release["transition_history"].append(
        {
            "from": "shadow",
            "to": "opt-in-alpha",
            "at": "2026-08-14T01:20:00Z",
            "reason": "Invalid supplemental-only freshness claim.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T01:15:00Z",
            "acceptance_reviewed_at": "2026-08-14T01:16:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:20:00Z"
            ),
            "exposure": _exposure("opt-in-alpha", 34),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="oldest latest"):
        model_stewardship.validate_release(release, _catalog())


def test_release_rejects_train_eval_split_reuse() -> None:
    release = copy.deepcopy(_release())
    training, evaluation = release["resource_ledger"]
    for field in ("origin", "repo_id", "revision", "configuration", "split"):
        evaluation[field] = training[field]
    with pytest.raises(model_stewardship.StewardshipError, match="reuses"):
        model_stewardship.validate_release(release, _catalog())


def test_release_rejects_train_eval_content_rehost() -> None:
    release = copy.deepcopy(_release())
    release["resource_ledger"][1]["file_digest"] = release["resource_ledger"][0][
        "file_digest"
    ]
    with pytest.raises(model_stewardship.StewardshipError, match="identical content"):
        model_stewardship.validate_release(release, _catalog())


def test_acceptance_evidence_cannot_reference_a_training_lane() -> None:
    release = _verified_release()
    for evidence in release["evidence"]:
        evidence["resource_id"] = "example-local-training"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="held-out evaluation"):
        model_stewardship.validate_release(release, _catalog())


def test_burden_measurement_cannot_reference_a_training_lane() -> None:
    release = _verified_release()
    release["burden_register"][0]["resource_id"] = "example-local-training"
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="held-out evaluation"):
        model_stewardship.validate_release(release, _catalog())


def test_hub_catalog_roles_constrain_release_lane() -> None:
    release = copy.deepcopy(_release())
    xstest = model_stewardship.select_resource(_catalog(), "xstest")
    release["resource_ledger"][0].update(
        origin="huggingface",
        repo_id=xstest["repo_id"],
        revision=xstest["revision"],
        catalog_resource_id=xstest["id"],
        lane="training",
        license=xstest["license"],
        access_basis="account-holder-accepted",
    )
    with pytest.raises(model_stewardship.StewardshipError, match="catalog roles"):
        model_stewardship.validate_release(release, _catalog())


def test_paused_release_requires_minimized_incident_record() -> None:
    release = _verified_release()
    release["stage"] = "paused"
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "paused",
            "at": "2026-08-14T02:00:00Z",
            "reason": "Critical trigger in the test fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T02:00:00Z"
            ),
            "exposure": _exposure("paused", 20),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="incident record"):
        model_stewardship.validate_release(release, _catalog())


def test_critical_incident_forces_non_deployed_stage() -> None:
    release = _verified_release()
    release["incidents"].append(
        {
            "id": "critical-test-incident",
            "candidate_digest": release["candidate_digest"],
            "at": "2026-08-14T02:00:00Z",
            "severity": "critical",
            "category": "authorization",
            "action": "candidate paused in fixture",
            "content_digest": "sha256:" + "a" * 64,
            "status": "open",
            "resolved_at": None,
            "resolution_digest": None,
            "incident_evaluation_digest": None,
            "reentry_reviewed_at": None,
        }
    )
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="critical incident"):
        model_stewardship.validate_release(release, _catalog())


def test_history_cannot_advance_while_severe_incident_is_active() -> None:
    release = _verified_release()
    release["incidents"].append(
        {
            "id": "historical-open-severe-incident",
            "candidate_digest": release["candidate_digest"],
            "at": "2026-08-14T01:05:00Z",
            "severity": "high",
            "category": "authorization",
            "action": "candidate later paused in fixture",
            "content_digest": "sha256:" + "7" * 64,
            "status": "open",
            "resolved_at": None,
            "resolution_digest": None,
            "incident_evaluation_digest": None,
            "reentry_reviewed_at": None,
        }
    )
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T01:10:00Z",
            "reason": "Invalid advance during a severe incident.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:10:00Z"
            ),
            "exposure": _exposure("shadow", 60),
        }
    )
    release["transition_history"].append(
        {
            "from": "shadow",
            "to": "paused",
            "at": "2026-08-14T01:20:00Z",
            "reason": "Late containment cannot erase the invalid advance.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:20:00Z"
            ),
            "exposure": _exposure("paused", 61),
        }
    )
    release["stage"] = "paused"
    with pytest.raises(model_stewardship.StewardshipError, match="incidents are active"):
        model_stewardship.validate_release(release, _catalog())


def test_rollback_cannot_self_anchor() -> None:
    release = _verified_release()
    release["rollback"]["release_id"] = release["release_id"]
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="current release"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["rollback"]["rollback_release_digest"] = release["candidate_digest"]
    release["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(release)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="self-anchor"):
        model_stewardship.validate_release(release, _catalog())


def test_pause_resume_cannot_skip_unreached_stages() -> None:
    release = _verified_release()
    release["stage"] = "paused"
    release["incidents"].append(
        {
            "id": "medium-test-incident",
            "candidate_digest": release["candidate_digest"],
            "at": "2026-08-14T02:00:00Z",
            "severity": "medium",
            "category": "functionality",
            "action": "candidate paused in fixture",
            "content_digest": "sha256:" + "b" * 64,
            "status": "open",
            "resolved_at": None,
            "resolution_digest": None,
            "incident_evaluation_digest": None,
            "reentry_reviewed_at": None,
        }
    )
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "paused",
            "at": "2026-08-14T02:00:00Z",
            "reason": "Pause in test fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T02:00:00Z"
            ),
            "exposure": _exposure("paused", 21),
        }
    )
    model_stewardship.validate_release(release, _catalog())

    release["stage"] = "canary"
    release["transition_history"].append(
        {
            "from": "paused",
            "to": "canary",
            "at": "2026-08-14T03:00:00Z",
            "reason": "Invalid stage-skipping resume in test fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T03:00:00Z"
            ),
            "exposure": _exposure("canary", 22),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="must resume at 'shadow'"):
        model_stewardship.validate_release(release, _catalog())


def test_stage_progression_requires_time_and_fresh_reviewed_observation() -> None:
    release = _verified_release()
    release["stage"] = "shadow"
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T01:00:00Z",
            "reason": "Invalid instantaneous transition in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T01:00:00Z"
            ),
            "exposure": _exposure("shadow", 23),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="strictly increasing"):
        model_stewardship.validate_release(release, _catalog())

    release = _verified_release()
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "shadow",
            "at": "2026-08-14T02:00:00Z",
            "reason": "Entered inert shadow in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T02:00:00Z"
            ),
            "exposure": _exposure("shadow", 24),
        }
    )

    notes_only = copy.deepcopy(release)
    notes_only["evidence"][0]["notes"] += " Prose-only freshness claim."
    notes_only["stage"] = "opt-in-alpha"
    notes_only["transition_history"].append(
        {
            "from": "shadow",
            "to": "opt-in-alpha",
            "at": "2026-08-14T03:00:00Z",
            "reason": "Invalid notes-only evidence refresh in fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": notes_only["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(notes_only),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                notes_only, "2026-08-14T03:00:00Z"
            ),
            "exposure": _exposure("opt-in-alpha", 25),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="evidence window"):
        model_stewardship.validate_release(notes_only, _catalog())

    for evidence in release["evidence"]:
        if evidence["kind"] in model_stewardship.REQUIRED_EVIDENCE:
            evidence.update(
                window_end="2026-08-14T02:30:00Z",
                notes=evidence["notes"] + " Fresh shadow evidence fixture.",
            )
    for burden in release["burden_register"]:
        if burden["kind"] in model_stewardship.REQUIRED_BURDENS:
            burden["window_end"] = "2026-08-14T02:30:00Z"
    release["review"]["reviewed_at"] = "2026-08-14T02:45:00Z"
    release["stage"] = "opt-in-alpha"
    release["transition_history"].append(
        {
            "from": "shadow",
            "to": "opt-in-alpha",
            "at": "2026-08-14T03:00:00Z",
            "reason": "Invalid progression without a reviewed observation.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T02:30:00Z",
            "acceptance_reviewed_at": "2026-08-14T02:45:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T03:00:00Z"
            ),
            "exposure": _exposure("opt-in-alpha", 29),
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="reviewed observation"):
        model_stewardship.validate_release(release, _catalog())


def test_resolved_critical_incident_is_preserved_across_reviewed_resume() -> None:
    release = _verified_release()
    incident = {
        "id": "resolved-critical-test-incident",
        "candidate_digest": release["candidate_digest"],
        "at": "2026-08-14T02:00:00Z",
        "severity": "critical",
        "category": "authorization",
        "action": "candidate paused in fixture",
        "content_digest": "sha256:" + "a" * 64,
        "status": "open",
        "resolved_at": None,
        "resolution_digest": None,
        "incident_evaluation_digest": None,
        "reentry_reviewed_at": None,
    }
    release["incidents"].append(incident)
    release["stage"] = "paused"
    release["transition_history"].append(
        {
            "from": "verified",
            "to": "paused",
            "at": "2026-08-14T02:00:00Z",
            "reason": "Critical authorization incident in the test fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T00:00:00Z",
            "acceptance_reviewed_at": "2026-08-14T00:00:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T02:00:00Z"
            ),
            "exposure": _exposure("paused", 26),
        }
    )
    model_stewardship.validate_release(release, _catalog())

    incident.update(
        status="resolved",
        resolved_at="2026-08-14T03:00:00Z",
        resolution_digest="sha256:" + "b" * 64,
        incident_evaluation_digest="sha256:" + "c" * 64,
        reentry_reviewed_at="2026-08-14T03:30:00Z",
    )
    incident_evidence = next(
        item for item in release["evidence"] if item["kind"] == "authorization"
    )
    incident_evidence.update(
        incident_id=incident["id"],
        artifact_digest="sha256:" + "c" * 64,
        window_end="2026-08-14T03:15:00Z",
        notes="Incident-specific independently reviewed regression evidence.",
    )
    for evidence in release["evidence"]:
        if evidence["kind"] in model_stewardship.REQUIRED_EVIDENCE:
            evidence["window_end"] = "2026-08-14T03:15:00Z"
    for burden in release["burden_register"]:
        if burden["kind"] in model_stewardship.REQUIRED_BURDENS:
            burden["window_end"] = "2026-08-14T03:15:00Z"
    release["review"]["reviewed_at"] = "2026-08-14T03:30:00Z"
    for index, rule in enumerate(release["monitoring"]["signals"]):
        release["observations"].append(
            {
                "id": f"post-pause-{index}-observation",
                "candidate_digest": release["candidate_digest"],
                "at": "2026-08-14T03:15:00Z",
                "category": rule["category"],
                "signal": rule["id"],
                "value": rule["threshold"],
                "unit": rule["unit"],
                "review_status": "reviewed",
                "reviewed_at": "2026-08-14T03:30:00Z",
                "result": "pass",
                "content_digest": "sha256:" + f"{70 + index:064x}",
            }
        )
    release["stage"] = "shadow"
    release["transition_history"].append(
        {
            "from": "paused",
            "to": "shadow",
            "at": "2026-08-14T04:00:00Z",
            "reason": "Human-reviewed conservative re-entry in the fixture.",
            "reviewer": "test-reviewer",
            "candidate_digest": release["candidate_digest"],
            "evidence_digest": model_stewardship.evidence_digest(release),
            "evidence_window_end": "2026-08-14T03:15:00Z",
            "acceptance_reviewed_at": "2026-08-14T03:30:00Z",
            "monitoring_digest": model_stewardship.monitoring_digest_at(
                release, "2026-08-14T04:00:00Z"
            ),
            "exposure": _exposure("shadow", 27),
        }
    )
    model_stewardship.validate_release(release, _catalog())
    assert release["incidents"][0]["status"] == "resolved"

    late_review = copy.deepcopy(release)
    late_review["incidents"][0]["reentry_reviewed_at"] = (
        "2026-08-14T04:30:00Z"
    )
    _rebind_monitoring_history(late_review)
    with pytest.raises(model_stewardship.StewardshipError, match="before re-entry"):
        model_stewardship.validate_release(late_review, _catalog())

    pre_evidence_review = copy.deepcopy(release)
    pre_evidence_review["incidents"][0]["reentry_reviewed_at"] = (
        "2026-08-14T03:10:00Z"
    )
    _rebind_monitoring_history(pre_evidence_review)
    with pytest.raises(model_stewardship.StewardshipError, match="predates its incident"):
        model_stewardship.validate_release(pre_evidence_review, _catalog())

    wrong_lane = copy.deepcopy(release)
    wrong_lane["incidents"][0]["category"] = "privacy"
    _rebind_monitoring_history(wrong_lane)
    with pytest.raises(model_stewardship.StewardshipError, match="incident-evaluation lane"):
        model_stewardship.validate_release(wrong_lane, _catalog())

    unbound_evidence = copy.deepcopy(release)
    next(
        item
        for item in unbound_evidence["evidence"]
        if item["kind"] == "authorization"
    )["incident_id"] = None
    unbound_evidence["transition_history"][-1]["evidence_digest"] = (
        model_stewardship.evidence_digest(unbound_evidence)
    )
    with pytest.raises(model_stewardship.StewardshipError, match="incident-specific"):
        model_stewardship.validate_release(unbound_evidence, _catalog())

    early_resume = copy.deepcopy(release)
    early_resume["transition_history"][-1]["at"] = "2026-08-14T02:30:00Z"
    early_resume["transition_history"][-1]["evidence_window_end"] = (
        "2026-08-14T02:15:00Z"
    )
    early_resume["transition_history"][-1]["acceptance_reviewed_at"] = (
        "2026-08-14T02:20:00Z"
    )
    early_resume["transition_history"][-1]["monitoring_digest"] = (
        model_stewardship.monitoring_digest_at(
            early_resume, "2026-08-14T02:30:00Z"
        )
    )
    with pytest.raises(
        model_stewardship.StewardshipError,
        match="incidents are active|resumes before",
    ):
        model_stewardship.validate_release(early_resume, _catalog())

    release["incidents"] = []
    with pytest.raises(model_stewardship.StewardshipError, match="recorded incident"):
        model_stewardship.validate_release(release, _catalog())


def test_resolved_critical_incident_cannot_skip_pause() -> None:
    release = _verified_release()
    release["incidents"].append(
        {
            "id": "unpaused-resolved-critical",
            "candidate_digest": release["candidate_digest"],
            "at": "2026-08-14T02:00:00Z",
            "severity": "critical",
            "category": "authorization",
            "action": "fixture claims resolution without containment",
            "content_digest": "sha256:" + "d" * 64,
            "status": "resolved",
            "resolved_at": "2026-08-14T03:00:00Z",
            "resolution_digest": "sha256:" + "e" * 64,
            "incident_evaluation_digest": "sha256:" + "f" * 64,
            "reentry_reviewed_at": None,
        }
    )
    with pytest.raises(model_stewardship.StewardshipError, match="no recorded pause"):
        model_stewardship.validate_release(release, _catalog())


def test_release_status_never_reports_authority() -> None:
    status_value = model_stewardship.release_status(_release())
    assert status_value["authorization_granted"] is False
    assert "does not grant" in status_value["boundary"]
