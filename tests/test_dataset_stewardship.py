"""Tests for the offline outbound dataset stewardship boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dataset_stewardship.py"
MANIFEST_PATH = ROOT / "resources" / "data" / "manifests" / "legacy-mlx-v0.json"
SCHEMA_PATH = ROOT / "resources" / "data" / "dataset-manifest.schema.json"

SPEC = importlib.util.spec_from_file_location("dataset_stewardship", TOOL)
assert SPEC and SPEC.loader
dataset_stewardship = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dataset_stewardship)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _passed_assessment(label: str) -> dict:
    return {
        "state": "pass",
        "method": f"test-only {label} review",
        "artifact_ref": f"test-evidence/{label}.json",
        "artifact_digest": "sha256:" + "a" * 64,
        "reviewed_at": "2026-08-29T09:00:00Z",
        "reviewer": "test-reviewer",
        "findings": [],
        "notes": ["Synthetic test fixture evidence."],
    }


def _approved_manifest(visibility: str = "public") -> dict:
    manifest = copy.deepcopy(_manifest())
    manifest["status"] = "approved"
    manifest["source_provenance"].update(
        {"class": "operational", "evidence": ["test-only provenance record"]}
    )
    manifest["licenses"].update(
        {
            "state": "verified",
            "dataset_license": "CC-BY-4.0",
            "components_complete": True,
            "evidence": ["test-only license review"],
        }
    )
    manifest["consent_and_reuse"].update(
        {
            "state": "verified",
            "basis": "project-authored",
            "scope": ["test-only publication fixture"],
            "evidence": ["test-only reuse review"],
        }
    )
    manifest["privacy"]["classification"] = (
        "public" if visibility == "public" else "internal"
    )
    for name in ("privacy_review", "pii_review", "secret_review"):
        manifest["privacy"][name] = _passed_assessment(name)
    manifest["retention"].update(
        {
            "state": "defined",
            "policy": "Retain the versioned test fixture while it is reviewed.",
            "owner": "test-steward",
            "review_at": "2027-08-29T00:00:00Z",
        }
    )
    manifest["withdrawal"].update(
        {
            "state": "defined",
            "process": "Record and review future-use removal requests.",
            "owner": "test-steward",
            "limitations": [
                "A future-use restriction cannot recall copies already downloaded."
            ],
        }
    )
    manifest["feedback"].update(
        {
            "presence": "absent",
            "nature": "none",
            "rubric": {
                "state": "not-applicable",
                "description": "The test fixture declares no feedback.",
                "artifact_digest": None,
            },
            "ties": {
                "policy": "not-applicable",
                "description": "No feedback comparisons exist.",
            },
            "uncertainty": {
                "representation": "not-applicable",
                "description": "No feedback uncertainty exists.",
            },
            "aggregation": {
                "method": "not-applicable",
                "minority_preservation": "not-applicable",
                "description": "No feedback is aggregated.",
            },
        }
    )
    for name in ("split", "deduplication", "contamination"):
        manifest["partitioning"][name] = _passed_assessment(name)
    manifest["huggingface"].update(
        {
            "repo_id": "kingdom/legacy-mlx-release",
            "visibility": visibility,
            "config_names": ["default"],
            "default_config": "default",
            "splits": ["source-unsplit"],
            "revision": None,
            "access_policy": (
                "Only approved test reviewers may access this candidate."
                if visibility in {"gated", "private"}
                else None
            ),
        }
    )
    for entry in manifest["files"]:
        entry["config"] = "default"
    for name in (
        "credit",
        "generation",
        "sensitive_content",
        "affected_parties",
        "feedback_detail",
        "grouping",
        "remedy",
    ):
        manifest["release_receipts"][name] = _passed_assessment(name)
    manifest["review"].update(
        {
            "state": "approved",
            "technical": "pass",
            "provenance": "pass",
            "rights": "pass",
            "license": "pass",
            "privacy": "pass",
            "feedback": "pass",
            "partitioning": "pass",
            "reviewed_at": "2026-08-29T10:00:00Z",
            "reviewers": ["test-reviewer"],
            "blockers": [],
        }
    )
    manifest["publication_eligible"] = True
    return manifest


def test_repository_schema_and_legacy_manifest_are_valid() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "kingdom.dataset-manifest/v1"
    assert set(schema["required"]) == dataset_stewardship.TOP_LEVEL_KEYS

    manifest = _manifest()
    checked = dataset_stewardship.validate_manifest(manifest, root=ROOT)
    assert checked["manifest_id"] == "kingdom/legacy-mlx@0"
    assert len(checked["files"]) == 4
    assert sum(item["records"] for item in checked["files"]) == 647


def test_legacy_manifest_keeps_unknowns_and_refuses_publication() -> None:
    manifest = _manifest()
    assert manifest["status"] == "legacy-unreviewed"
    assert manifest["source_provenance"]["class"] == "unknown"
    assert manifest["licenses"]["state"] == "unknown"
    assert manifest["licenses"]["dataset_license"] is None
    assert manifest["consent_and_reuse"]["state"] == "unknown"
    assert manifest["consent_and_reuse"]["basis"] == "unknown"
    assert manifest["feedback"]["presence"] == "unknown"
    assert manifest["huggingface"] == {
        "repo_id": None,
        "visibility": "not-planned",
        "config_names": [],
        "default_config": None,
        "splits": [],
        "revision": None,
        "viewer": {
            "state": "not-reviewed",
            "method": None,
            "artifact_ref": None,
            "artifact_digest": None,
            "reviewed_at": None,
            "reviewer": None,
            "findings": [],
            "notes": [
                "No Hugging Face Data Studio or viewer review is planned for this legacy HOLD."
            ],
        },
        "access_policy": None,
        "notes": [
            "This legacy inventory is not assigned to a Hugging Face repository and is not a publication candidate."
        ],
    }
    assert manifest["review"]["state"] == "unreviewed"
    assert all(item["config"] is None for item in manifest["files"])
    assert all(
        receipt["state"] == "not-reviewed"
        for name, receipt in manifest["release_receipts"].items()
        if name != "notes"
    )
    assert manifest["publication_eligible"] is False


def test_missing_provenance_class_is_rejected_not_defaulted() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["source_provenance"].pop("class")
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="missing required keys: class"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)
    assert "class" not in manifest["source_provenance"]


def test_malformed_enum_returns_a_bounded_error() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["status"] = []
    with pytest.raises(dataset_stewardship.StewardshipError, match="status must be"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_unknown_license_cannot_carry_an_invented_license() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["licenses"]["dataset_license"] = "mit"
    with pytest.raises(dataset_stewardship.StewardshipError, match="state unknown"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_unknown_consent_cannot_carry_a_reuse_claim() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["consent_and_reuse"]["basis"] = "project-authored"
    with pytest.raises(dataset_stewardship.StewardshipError, match="state unknown"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_unknown_feedback_remains_unknown_across_the_topology() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["feedback"]["nature"] = "categorical-label"
    with pytest.raises(dataset_stewardship.StewardshipError, match="must keep nature"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_publication_gate_cannot_be_bypassed_by_flipping_the_boolean() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["status"] = "approved"
    manifest["publication_eligible"] = True
    manifest["review"].update(
        state="approved",
        technical="pass",
        provenance="pass",
        rights="pass",
        license="pass",
        privacy="pass",
        feedback="pass",
        partitioning="pass",
        reviewed_at="2026-08-29T10:00:00Z",
        reviewers=["test-reviewer"],
        blockers=[],
    )
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="source provenance is unknown"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize(
    "receipt_name",
    [
        "credit",
        "generation",
        "sensitive_content",
        "affected_parties",
        "feedback_detail",
        "grouping",
        "remedy",
    ],
)
def test_publication_requires_every_release_receipt(receipt_name: str) -> None:
    manifest = _approved_manifest()
    manifest["release_receipts"][receipt_name] = copy.deepcopy(
        _manifest()["release_receipts"][receipt_name]
    )
    with pytest.raises(
        dataset_stewardship.StewardshipError, match=f"release receipt {receipt_name}"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["release_receipts"].pop(receipt_name)
    with pytest.raises(dataset_stewardship.StewardshipError, match=receipt_name):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize("source_class", ["synthetic", "model-generated", "mixed"])
def test_generated_sources_cannot_claim_generation_receipt_not_applicable(
    source_class: str,
) -> None:
    manifest = _approved_manifest()
    manifest["source_provenance"]["class"] = source_class
    manifest["release_receipts"]["generation"] = _passed_assessment("generation")
    manifest["release_receipts"]["generation"]["state"] = "not-applicable"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="requires a passed generation"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize("presence", ["present", "mixed"])
def test_present_feedback_cannot_claim_detail_receipt_not_applicable(
    presence: str,
) -> None:
    manifest = _approved_manifest()
    manifest["feedback"].update(
        {
            "presence": presence,
            "nature": "pairwise-preference",
            "rubric": {
                "state": "documented",
                "description": "Synthetic test-only pairwise rubric.",
                "artifact_digest": "sha256:" + "b" * 64,
            },
            "ties": {
                "policy": "preserve",
                "description": "Ties remain explicit in the test fixture.",
            },
            "uncertainty": {
                "representation": "explicit-label",
                "description": "Uncertainty remains an explicit test label.",
            },
            "aggregation": {
                "method": "none",
                "minority_preservation": "yes",
                "description": "The test fixture preserves individual feedback.",
            },
        }
    )
    manifest["release_receipts"]["feedback_detail"] = _passed_assessment(
        "feedback-detail"
    )
    manifest["release_receipts"]["feedback_detail"]["state"] = "not-applicable"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="requires a passed feedback-detail"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_present_feedback_requires_known_minority_signal_preservation() -> None:
    manifest = _approved_manifest()
    manifest["feedback"].update(
        {
            "presence": "present",
            "nature": "pairwise-preference",
            "rubric": {
                "state": "documented",
                "description": "Synthetic test-only pairwise rubric.",
                "artifact_digest": "sha256:" + "b" * 64,
            },
            "ties": {
                "policy": "preserve",
                "description": "Ties remain explicit in the test fixture.",
            },
            "uncertainty": {
                "representation": "explicit-label",
                "description": "Uncertainty remains an explicit test label.",
            },
            "aggregation": {
                "method": "none",
                "minority_preservation": "unknown",
                "description": "A deliberately incomplete test declaration.",
            },
        }
    )
    with pytest.raises(
        dataset_stewardship.StewardshipError,
        match="minority-signal preservation is unknown",
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_defined_withdrawal_requires_an_explicit_limitation() -> None:
    manifest = _approved_manifest()
    manifest["withdrawal"]["limitations"] = []
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="at least one limitation"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_huggingface_object_is_required_and_not_inferred() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest.pop("huggingface")
    with pytest.raises(dataset_stewardship.StewardshipError, match="huggingface"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_not_planned_huggingface_state_cannot_claim_a_remote_target() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["huggingface"]["repo_id"] = "kingdom/legacy-mlx"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="not-planned requires null"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize("source_class", ["operational", "upstream"])
def test_new_source_provenance_classes_are_supported(source_class: str) -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["source_provenance"]["class"] = source_class
    dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize(
    "lane",
    [
        "preference",
        "calibration-development",
        "retrieval",
        "metadata-reference",
        "display-only",
    ],
)
def test_new_dataset_lanes_are_supported(lane: str) -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["files"][0]["lane"] = lane
    dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize(
    "repo_id",
    [
        "org/team/dataset",
        "aa--bb/cc",
        "a..b/c",
        "owner/repo.git",
        "a/" + "b" * 95,
    ],
)
def test_huggingface_repo_id_must_be_exact_hub_valid_owner_and_name(
    repo_id: str,
) -> None:
    manifest = _approved_manifest()
    manifest["huggingface"]["repo_id"] = repo_id
    with pytest.raises(dataset_stewardship.StewardshipError, match="Hub-valid"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_huggingface_repo_id_accepts_total_length_boundary() -> None:
    manifest = _approved_manifest()
    manifest["huggingface"]["repo_id"] = "a/" + "b" * 94
    dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_publication_requires_planned_huggingface_visibility_and_layout() -> None:
    manifest = _approved_manifest()
    manifest["huggingface"]["visibility"] = "unknown"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="visibility is not planned"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["huggingface"]["default_config"] = None
    with pytest.raises(dataset_stewardship.StewardshipError, match="default config"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["huggingface"]["splits"] = ["train"]
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="not bound to files: train"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_publication_binds_every_file_to_a_declared_config_and_split() -> None:
    manifest = _approved_manifest()
    manifest["huggingface"]["config_names"].append("audit")
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="configs are not bound.*audit"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["files"][0]["config"] = None
    with pytest.raises(dataset_stewardship.StewardshipError, match="has no.*config"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["files"][0]["config"] = "undeclared"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="uses undeclared.*config"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["files"][0]["split"] = "undeclared"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="uses undeclared.*split"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_publication_refuses_formats_without_structural_record_verification() -> None:
    manifest = _approved_manifest()
    manifest["files"][0]["format"] = "parquet"
    with pytest.raises(
        dataset_stewardship.StewardshipError,
        match="publication eligibility only verifies JSONL",
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest()
    manifest["files"][0]["path"] = "mlx/training/datasets/not-jsonl.parquet"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="lacks a .jsonl suffix"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_public_huggingface_candidate_requires_public_privacy_classification() -> None:
    manifest = _approved_manifest("public")
    manifest["privacy"]["classification"] = "internal"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="classification is not public"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


@pytest.mark.parametrize("visibility", ["gated", "private"])
def test_bounded_visibility_requires_access_policy_and_passed_privacy(
    visibility: str,
) -> None:
    manifest = _approved_manifest(visibility)
    dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest["huggingface"]["access_policy"] = None
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="requires an access policy"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest = _approved_manifest(visibility)
    manifest["privacy"]["pii_review"] = copy.deepcopy(
        _manifest()["privacy"]["pii_review"]
    )
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="pii_review has not passed"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_published_huggingface_record_binds_revision_and_viewer() -> None:
    manifest = _approved_manifest()
    manifest["status"] = "published"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="immutable 40-character"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest["huggingface"]["revision"] = "b" * 39
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="40-character lowercase hex"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    manifest["huggingface"]["revision"] = "b" * 40
    with pytest.raises(dataset_stewardship.StewardshipError, match="viewer state pass"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)

    for state in ("pass", "not-applicable"):
        candidate = copy.deepcopy(manifest)
        candidate["huggingface"]["viewer"] = _passed_assessment("viewer")
        candidate["huggingface"]["viewer"]["state"] = state
        dataset_stewardship.validate_manifest(candidate, verify_files=False)


def test_review_assessment_pass_requires_bound_evidence() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["privacy"]["pii_review"]["state"] = "pass"
    with pytest.raises(dataset_stewardship.StewardshipError, match="requires method"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_manifest_id_must_bind_dataset_and_version() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["version"] = "1"
    with pytest.raises(dataset_stewardship.StewardshipError, match="exactly bind"):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_relative_paths_cannot_escape_the_explicit_root() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["files"][0]["path"] = "../heartbeat-triage.jsonl"
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="repository-relative"
    ):
        dataset_stewardship.validate_manifest(manifest, verify_files=False)


def test_exact_digest_and_record_bindings_are_recomputed() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["files"][0]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(dataset_stewardship.StewardshipError, match="digest mismatch"):
        dataset_stewardship.validate_manifest(manifest, root=ROOT)

    manifest = copy.deepcopy(_manifest())
    manifest["files"][0]["records"] += 1
    with pytest.raises(
        dataset_stewardship.StewardshipError, match="record count mismatch"
    ):
        dataset_stewardship.validate_manifest(manifest, root=ROOT)


def test_manifest_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    original = MANIFEST_PATH.read_text(encoding="utf-8")
    duplicate = original.replace(
        '  "publication_eligible": false,',
        '  "publication_eligible": true,\n  "publication_eligible": false,',
        1,
    )
    duplicate_path = tmp_path / "duplicate-manifest.json"
    duplicate_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(dataset_stewardship.StewardshipError, match="duplicate.*key"):
        dataset_stewardship.load_manifest(duplicate_path)

    nonfinite = original.replace('"version": "0"', '"version": NaN', 1)
    nonfinite_path = tmp_path / "nonfinite-manifest.json"
    nonfinite_path.write_text(nonfinite, encoding="utf-8")
    with pytest.raises(dataset_stewardship.StewardshipError, match="non-finite"):
        dataset_stewardship.load_manifest(nonfinite_path)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ('{"label":"a","label":"b"}\n', "duplicate JSON object key"),
        ('{"score":Infinity}\n', "non-finite JSON number"),
        ('{"score":1e400}\n', "non-finite JSON number"),
    ],
)
def test_jsonl_rejects_ambiguous_or_nonstandard_json(
    tmp_path: Path, record: str, message: str
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(record, encoding="utf-8")
    with pytest.raises(dataset_stewardship.StewardshipError, match=message):
        dataset_stewardship._verify_jsonl(path)


def test_canonical_manifest_digest_binds_status_and_all_other_fields() -> None:
    manifest = _manifest()
    original = dataset_stewardship.canonical_manifest_digest(manifest)
    changed = copy.deepcopy(manifest)
    changed["description"] += " Changed."
    assert original.startswith("sha256:")
    assert dataset_stewardship.canonical_manifest_digest(changed) != original


def test_cli_check_and_digest_are_offline_and_bounded() -> None:
    env = os.environ.copy()
    env.update(
        {
            "http_proxy": "http://127.0.0.1:1",
            "https_proxy": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
        }
    )
    checked = subprocess.run(
        [sys.executable, str(TOOL), "check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert "publication_eligible=false" in checked.stdout
    assert "files=4" in checked.stdout

    target = ROOT / "mlx" / "training" / "datasets" / "heartbeat-triage.jsonl"
    digested = subprocess.run(
        [sys.executable, str(TOOL), "digest", str(target)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert digested.returncode == 0, digested.stderr
    assert (
        "sha256:0b291f4c4dc3be6f8a8d59bd6525a9bc1cc29b0e28403df5b6276c7f2b6d7fb0"
        in digested.stdout
    )


def test_cli_exposes_no_publish_or_upload_command() -> None:
    help_result = subprocess.run(
        [sys.executable, str(TOOL), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "{check,digest,manifest-digest}" in help_result.stdout
    assert "publish" not in help_result.stdout.casefold()
    assert "upload" not in help_result.stdout.casefold()
