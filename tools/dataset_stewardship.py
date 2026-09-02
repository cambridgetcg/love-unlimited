#!/usr/bin/env python3
"""Offline, fail-closed validation and receipt binding for KINGDOM datasets.

This tool validates declared structure, release semantics, and exact local file
digests.  It deliberately has no network, upload, publication, credential, or
license-selection capability.  A successful check is evidence that a manifest
is internally consistent; it is not publication authority or a legal finding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "resources" / "data" / "manifests" / "legacy-mlx-v0.json"
SCHEMA_NAME = "kingdom.dataset-manifest/v1"
BOUNDARY = (
    "Validation records bounded evidence; it does not grant publication "
    "authority, consent, a license, or permission to reuse personal data."
)
MAX_MANIFEST_BYTES = 2 * 1024 * 1024

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,126}[A-Za-z0-9]$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HF_REPO_ID_RE = re.compile(
    r"^[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_])?/"
    r"[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_])?$"
)
HF_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

TOP_LEVEL_KEYS = {
    "schema",
    "manifest_id",
    "dataset_id",
    "version",
    "status",
    "created_at",
    "boundary",
    "description",
    "source_provenance",
    "licenses",
    "consent_and_reuse",
    "privacy",
    "retention",
    "withdrawal",
    "feedback",
    "partitioning",
    "huggingface",
    "release_receipts",
    "files",
    "review",
    "publication_eligible",
    "limitations",
}

STATUS_VALUES = {
    "legacy-unreviewed",
    "draft",
    "under-review",
    "approved",
    "published",
    "deprecated",
    "withdrawn",
}
PROVENANCE_CLASSES = {
    "synthetic",
    "observed",
    "human-authored",
    "model-generated",
    "operational",
    "upstream",
    "mixed",
    "unknown",
}
ASSESSMENT_STATES = {"unknown", "not-reviewed", "pass", "fail", "not-applicable"}
REVIEW_DIMENSION_STATES = ASSESSMENT_STATES
LANES = {
    "training",
    "preference",
    "calibration",
    "calibration-development",
    "validation",
    "evaluation",
    "retrieval",
    "metadata-reference",
    "display-only",
    "monitoring",
    "governance",
    "independent-outcome",
    "operational-feedback",
    "deliberation",
    "stewardship-repair",
    "mixed",
    "unknown",
}
HF_VISIBILITIES = {"not-planned", "public", "gated", "private", "unknown"}


class StewardshipError(ValueError):
    """Raised when a manifest or bound artifact fails a stewardship check."""


class _DuplicateJSONKeyError(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _NonFiniteJSONNumberError(ValueError):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> Any:
    raise _NonFiniteJSONNumberError(value)


def _strict_json_loads(value: str | bytes) -> Any:
    parsed = json.loads(
        value,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_number,
    )
    pending = [parsed]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise _NonFiniteJSONNumberError(repr(current))
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return parsed


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StewardshipError(f"{path} must be an object")
    actual = set(value)
    missing = sorted(keys - actual)
    extra = sorted(actual - keys)
    if missing:
        raise StewardshipError(f"{path} is missing required keys: {', '.join(missing)}")
    if extra:
        raise StewardshipError(f"{path} has unknown keys: {', '.join(extra)}")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StewardshipError(f"{path} must be a non-empty string")
    return value


def _nullable_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _enum(value: Any, path: str, values: set[str]) -> str:
    if not isinstance(value, str) or value not in values:
        choices = ", ".join(sorted(values))
        raise StewardshipError(f"{path} must be one of: {choices}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise StewardshipError(f"{path} must be a boolean")
    return value


def _nonnegative_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StewardshipError(f"{path} must be a non-negative integer")
    return value


def _text_array(value: Any, path: str, *, minimum: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise StewardshipError(f"{path} must be an array")
    if len(value) < minimum:
        raise StewardshipError(f"{path} must contain at least {minimum} item(s)")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, f"{path}[{index}]"))
    if len(result) != len(set(result)):
        raise StewardshipError(f"{path} must not contain duplicate items")
    return result


def _digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise StewardshipError(
            f"{path} must be sha256 followed by 64 lowercase hex characters"
        )
    return value


def _nullable_digest(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _digest(value, path)


def _datetime(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StewardshipError(f"{path} must be an ISO 8601 date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StewardshipError(f"{path} must include a UTC offset")
    return text


def _nullable_datetime(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _datetime(value, path)


def _validate_assessment(value: Any, path: str) -> dict[str, Any]:
    assessment = _object(
        value,
        path,
        {
            "state",
            "method",
            "artifact_ref",
            "artifact_digest",
            "reviewed_at",
            "reviewer",
            "findings",
            "notes",
        },
    )
    state = _enum(assessment["state"], f"{path}.state", ASSESSMENT_STATES)
    method = _nullable_string(assessment["method"], f"{path}.method")
    artifact_ref = _nullable_string(assessment["artifact_ref"], f"{path}.artifact_ref")
    artifact_digest = _nullable_digest(
        assessment["artifact_digest"], f"{path}.artifact_digest"
    )
    reviewed_at = _nullable_datetime(assessment["reviewed_at"], f"{path}.reviewed_at")
    reviewer = _nullable_string(assessment["reviewer"], f"{path}.reviewer")
    findings = _text_array(assessment["findings"], f"{path}.findings")
    _text_array(assessment["notes"], f"{path}.notes", minimum=1)

    evidence = (method, artifact_ref, artifact_digest, reviewed_at, reviewer)
    if state in {"unknown", "not-reviewed"}:
        if any(item is not None for item in evidence) or findings:
            raise StewardshipError(
                f"{path} cannot attach review evidence or findings while state is {state}"
            )
    elif state in {"pass", "fail", "not-applicable"}:
        if any(item is None for item in evidence):
            raise StewardshipError(
                f"{path} state {state} requires method, artifact_ref, "
                "artifact_digest, reviewed_at, and reviewer"
            )
        if state == "fail" and not findings:
            raise StewardshipError(f"{path} state fail requires at least one finding")
    return assessment


def _validate_source(value: Any) -> dict[str, Any]:
    source = _object(value, "source_provenance", {"class", "evidence", "notes"})
    _enum(source["class"], "source_provenance.class", PROVENANCE_CLASSES)
    _text_array(source["evidence"], "source_provenance.evidence")
    _text_array(source["notes"], "source_provenance.notes", minimum=1)
    return source


def _validate_licenses(value: Any) -> dict[str, Any]:
    licenses = _object(
        value,
        "licenses",
        {
            "state",
            "dataset_license",
            "components_complete",
            "component_licenses",
            "evidence",
            "notes",
        },
    )
    state = _enum(
        licenses["state"], "licenses.state", {"unknown", "unreviewed", "verified"}
    )
    dataset_license = _nullable_string(
        licenses["dataset_license"], "licenses.dataset_license"
    )
    components_complete = _boolean(
        licenses["components_complete"], "licenses.components_complete"
    )
    if not isinstance(licenses["component_licenses"], list):
        raise StewardshipError("licenses.component_licenses must be an array")
    seen_components: set[str] = set()
    for index, item in enumerate(licenses["component_licenses"]):
        path = f"licenses.component_licenses[{index}]"
        component = _object(item, path, {"component", "license", "evidence"})
        name = _string(component["component"], f"{path}.component")
        declared = _string(component["license"], f"{path}.license")
        if declared.casefold() == "unknown":
            raise StewardshipError(
                f"{path}.license must use null at dataset level for unknown licensing"
            )
        _text_array(component["evidence"], f"{path}.evidence", minimum=1)
        if name in seen_components:
            raise StewardshipError(
                f"licenses.component_licenses has duplicate component {name!r}"
            )
        seen_components.add(name)
    evidence = _text_array(licenses["evidence"], "licenses.evidence")
    _text_array(licenses["notes"], "licenses.notes", minimum=1)

    if dataset_license is not None and dataset_license.casefold() == "unknown":
        raise StewardshipError(
            "licenses.dataset_license must be null when the license is unknown"
        )
    if state == "unknown" and (
        dataset_license is not None
        or components_complete
        or licenses["component_licenses"]
        or evidence
    ):
        raise StewardshipError(
            "licenses.state unknown must not carry an invented license, completeness claim, or evidence"
        )
    if state == "verified" and (
        dataset_license is None or not components_complete or not evidence
    ):
        raise StewardshipError(
            "licenses.state verified requires a dataset license, complete component review, and evidence"
        )
    return licenses


def _validate_consent(value: Any) -> dict[str, Any]:
    consent = _object(
        value,
        "consent_and_reuse",
        {"state", "basis", "scope", "evidence", "restrictions", "notes"},
    )
    state = _enum(
        consent["state"],
        "consent_and_reuse.state",
        {"unknown", "unreviewed", "verified"},
    )
    basis = _enum(
        consent["basis"],
        "consent_and_reuse.basis",
        {
            "explicit-consent",
            "project-authored",
            "public-license",
            "contract",
            "statutory-exception",
            "mixed",
            "unknown",
        },
    )
    scope = _text_array(consent["scope"], "consent_and_reuse.scope")
    evidence = _text_array(consent["evidence"], "consent_and_reuse.evidence")
    _text_array(consent["restrictions"], "consent_and_reuse.restrictions")
    _text_array(consent["notes"], "consent_and_reuse.notes", minimum=1)
    if state == "unknown" and (basis != "unknown" or scope or evidence):
        raise StewardshipError(
            "consent_and_reuse.state unknown requires basis unknown and no claimed scope or evidence"
        )
    if state == "verified" and (basis == "unknown" or not scope or not evidence):
        raise StewardshipError(
            "consent_and_reuse.state verified requires a known basis, bounded scope, and evidence"
        )
    return consent


def _validate_privacy(value: Any) -> dict[str, Any]:
    privacy = _object(
        value,
        "privacy",
        {"classification", "privacy_review", "pii_review", "secret_review", "notes"},
    )
    _enum(
        privacy["classification"],
        "privacy.classification",
        {"unknown", "public", "internal", "confidential", "restricted"},
    )
    _validate_assessment(privacy["privacy_review"], "privacy.privacy_review")
    _validate_assessment(privacy["pii_review"], "privacy.pii_review")
    _validate_assessment(privacy["secret_review"], "privacy.secret_review")
    _text_array(privacy["notes"], "privacy.notes", minimum=1)
    return privacy


def _validate_retention(value: Any) -> dict[str, Any]:
    retention = _object(
        value, "retention", {"state", "policy", "owner", "review_at", "notes"}
    )
    state = _enum(
        retention["state"], "retention.state", {"unknown", "unreviewed", "defined"}
    )
    policy = _nullable_string(retention["policy"], "retention.policy")
    owner = _nullable_string(retention["owner"], "retention.owner")
    review_at = _nullable_datetime(retention["review_at"], "retention.review_at")
    _text_array(retention["notes"], "retention.notes", minimum=1)
    if state == "unknown" and any(
        item is not None for item in (policy, owner, review_at)
    ):
        raise StewardshipError(
            "retention.state unknown cannot carry a claimed policy, owner, or review date"
        )
    if state == "defined" and any(item is None for item in (policy, owner, review_at)):
        raise StewardshipError(
            "retention.state defined requires policy, owner, and review_at"
        )
    return retention


def _validate_withdrawal(value: Any) -> dict[str, Any]:
    withdrawal = _object(
        value, "withdrawal", {"state", "process", "owner", "limitations", "notes"}
    )
    state = _enum(
        withdrawal["state"], "withdrawal.state", {"unknown", "unreviewed", "defined"}
    )
    process = _nullable_string(withdrawal["process"], "withdrawal.process")
    owner = _nullable_string(withdrawal["owner"], "withdrawal.owner")
    limitations = _text_array(withdrawal["limitations"], "withdrawal.limitations")
    _text_array(withdrawal["notes"], "withdrawal.notes", minimum=1)
    if state == "unknown" and (process is not None or owner is not None):
        raise StewardshipError(
            "withdrawal.state unknown cannot carry a claimed process or owner"
        )
    if state == "defined" and (process is None or owner is None or not limitations):
        raise StewardshipError(
            "withdrawal.state defined requires process, owner, and at least one limitation"
        )
    return withdrawal


def _validate_feedback(value: Any) -> dict[str, Any]:
    feedback = _object(
        value,
        "feedback",
        {"presence", "nature", "rubric", "ties", "uncertainty", "aggregation", "notes"},
    )
    presence = _enum(
        feedback["presence"],
        "feedback.presence",
        {"present", "absent", "mixed", "unknown"},
    )
    nature = _enum(
        feedback["nature"],
        "feedback.nature",
        {
            "categorical-label",
            "scalar-score",
            "pairwise-preference",
            "free-text",
            "behavioral-signal",
            "mixed",
            "none",
            "unknown",
        },
    )

    rubric = _object(
        feedback["rubric"],
        "feedback.rubric",
        {"state", "description", "artifact_digest"},
    )
    rubric_state = _enum(
        rubric["state"],
        "feedback.rubric.state",
        {"unknown", "not-documented", "documented", "not-applicable"},
    )
    rubric_description = _nullable_string(
        rubric["description"], "feedback.rubric.description"
    )
    rubric_digest = _nullable_digest(
        rubric["artifact_digest"], "feedback.rubric.artifact_digest"
    )
    if rubric_state == "unknown" and (
        rubric_description is not None or rubric_digest is not None
    ):
        raise StewardshipError(
            "feedback.rubric state unknown cannot carry documentation"
        )
    if rubric_state == "documented" and (
        rubric_description is None or rubric_digest is None
    ):
        raise StewardshipError(
            "feedback.rubric state documented requires description and artifact_digest"
        )
    if rubric_state == "not-applicable" and rubric_description is None:
        raise StewardshipError(
            "feedback.rubric state not-applicable requires an explanation"
        )

    ties = _object(feedback["ties"], "feedback.ties", {"policy", "description"})
    tie_policy = _enum(
        ties["policy"],
        "feedback.ties.policy",
        {
            "unknown",
            "not-applicable",
            "preserve",
            "adjudicate",
            "consensus",
            "deterministic-break",
            "random-break",
            "discard",
            "other",
        },
    )
    tie_description = _nullable_string(ties["description"], "feedback.ties.description")
    if (tie_policy == "unknown") != (tie_description is None):
        raise StewardshipError(
            "feedback.ties requires no description only when policy is unknown"
        )

    uncertainty = _object(
        feedback["uncertainty"],
        "feedback.uncertainty",
        {"representation", "description"},
    )
    uncertainty_representation = _enum(
        uncertainty["representation"],
        "feedback.uncertainty.representation",
        {
            "unknown",
            "not-applicable",
            "explicit-label",
            "confidence",
            "distribution",
            "interval",
            "multiple-annotators",
            "other",
        },
    )
    uncertainty_description = _nullable_string(
        uncertainty["description"], "feedback.uncertainty.description"
    )
    if (uncertainty_representation == "unknown") != (uncertainty_description is None):
        raise StewardshipError(
            "feedback.uncertainty requires no description only when representation is unknown"
        )

    aggregation = _object(
        feedback["aggregation"],
        "feedback.aggregation",
        {"method", "minority_preservation", "description"},
    )
    aggregation_method = _enum(
        aggregation["method"],
        "feedback.aggregation.method",
        {
            "unknown",
            "not-applicable",
            "none",
            "majority-vote",
            "mean",
            "median",
            "adjudication",
            "model-assisted",
            "mixed",
            "other",
        },
    )
    minority_preservation = _enum(
        aggregation["minority_preservation"],
        "feedback.aggregation.minority_preservation",
        {"unknown", "not-applicable", "yes", "no"},
    )
    aggregation_description = _nullable_string(
        aggregation["description"], "feedback.aggregation.description"
    )
    if aggregation_method == "unknown":
        if minority_preservation != "unknown" or aggregation_description is not None:
            raise StewardshipError(
                "feedback.aggregation method unknown requires minority_preservation unknown and no description"
            )
    elif aggregation_description is None:
        raise StewardshipError(
            "feedback.aggregation requires a description when method is known"
        )

    _text_array(feedback["notes"], "feedback.notes", minimum=1)

    if presence == "unknown":
        if not (
            nature == "unknown"
            and rubric_state == "unknown"
            and tie_policy == "unknown"
            and uncertainty_representation == "unknown"
            and aggregation_method == "unknown"
        ):
            raise StewardshipError(
                "feedback.presence unknown must keep nature, rubric, ties, uncertainty, and aggregation unknown"
            )
    elif presence == "absent":
        if not (
            nature == "none"
            and rubric_state == "not-applicable"
            and tie_policy == "not-applicable"
            and uncertainty_representation == "not-applicable"
            and aggregation_method == "not-applicable"
            and minority_preservation == "not-applicable"
        ):
            raise StewardshipError(
                "feedback.presence absent requires explicit none/not-applicable feedback fields"
            )
    elif nature in {"none", "unknown"}:
        raise StewardshipError(
            "present or mixed feedback requires a known, non-none nature"
        )
    return feedback


def _validate_partitioning(value: Any) -> dict[str, Any]:
    partitioning = _object(
        value, "partitioning", {"split", "deduplication", "contamination", "notes"}
    )
    _validate_assessment(partitioning["split"], "partitioning.split")
    _validate_assessment(partitioning["deduplication"], "partitioning.deduplication")
    _validate_assessment(partitioning["contamination"], "partitioning.contamination")
    _text_array(partitioning["notes"], "partitioning.notes", minimum=1)
    return partitioning


def _validate_huggingface(value: Any) -> dict[str, Any]:
    huggingface = _object(
        value,
        "huggingface",
        {
            "repo_id",
            "visibility",
            "config_names",
            "default_config",
            "splits",
            "revision",
            "viewer",
            "access_policy",
            "notes",
        },
    )
    repo_id = _nullable_string(huggingface["repo_id"], "huggingface.repo_id")
    visibility = _enum(
        huggingface["visibility"], "huggingface.visibility", HF_VISIBILITIES
    )
    config_names = _text_array(huggingface["config_names"], "huggingface.config_names")
    default_config = _nullable_string(
        huggingface["default_config"], "huggingface.default_config"
    )
    splits = _text_array(huggingface["splits"], "huggingface.splits")
    revision = _nullable_string(huggingface["revision"], "huggingface.revision")
    viewer = _validate_assessment(huggingface["viewer"], "huggingface.viewer")
    access_policy = _nullable_string(
        huggingface["access_policy"], "huggingface.access_policy"
    )
    _text_array(huggingface["notes"], "huggingface.notes", minimum=1)

    if repo_id is not None and (
        len(repo_id) > 96
        or HF_REPO_ID_RE.fullmatch(repo_id) is None
        or "--" in repo_id
        or ".." in repo_id
        or repo_id.endswith(".git")
    ):
        raise StewardshipError(
            "huggingface.repo_id must be an exact Hub-valid owner/name identifier "
            "of at most 96 characters, without '--', '..', or a '.git' suffix"
        )
    if default_config is not None and default_config not in config_names:
        raise StewardshipError(
            "huggingface.default_config must name one of huggingface.config_names"
        )
    if revision is not None and HF_REVISION_RE.fullmatch(revision) is None:
        raise StewardshipError(
            "huggingface.revision must be an immutable 40-character lowercase hex revision"
        )
    if visibility == "not-planned":
        if any(
            (repo_id, config_names, default_config, splits, revision, access_policy)
        ):
            raise StewardshipError(
                "huggingface.visibility not-planned requires null repo/revision/access, "
                "empty configs/splits, and no default config"
            )
        if viewer["state"] not in {"unknown", "not-reviewed"}:
            raise StewardshipError(
                "huggingface.visibility not-planned cannot claim a completed viewer review"
            )
    return huggingface


def _validate_release_receipts(value: Any) -> dict[str, Any]:
    receipts = _object(
        value,
        "release_receipts",
        {
            "credit",
            "generation",
            "sensitive_content",
            "affected_parties",
            "feedback_detail",
            "grouping",
            "remedy",
            "notes",
        },
    )
    for name in (
        "credit",
        "generation",
        "sensitive_content",
        "affected_parties",
        "feedback_detail",
        "grouping",
        "remedy",
    ):
        _validate_assessment(receipts[name], f"release_receipts.{name}")
    _text_array(receipts["notes"], "release_receipts.notes", minimum=1)
    return receipts


def _validate_files(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise StewardshipError("files must be a non-empty array")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        path = f"files[{index}]"
        entry = _object(
            item,
            path,
            {
                "path",
                "format",
                "sha256",
                "bytes",
                "records",
                "config",
                "lane",
                "split",
            },
        )
        relative = _string(entry["path"], f"{path}.path")
        pure = PurePosixPath(relative)
        if (
            relative == "."
            or pure.is_absolute()
            or relative.startswith("./")
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != relative
        ):
            raise StewardshipError(
                f"{path}.path must be a normalized repository-relative POSIX path"
            )
        if relative in seen:
            raise StewardshipError(f"files has duplicate path {relative!r}")
        seen.add(relative)
        _enum(
            entry["format"],
            f"{path}.format",
            {"jsonl", "parquet", "csv", "arrow", "text", "other"},
        )
        _digest(entry["sha256"], f"{path}.sha256")
        _nonnegative_integer(entry["bytes"], f"{path}.bytes")
        _nonnegative_integer(entry["records"], f"{path}.records")
        _nullable_string(entry["config"], f"{path}.config")
        _enum(entry["lane"], f"{path}.lane", LANES)
        _string(entry["split"], f"{path}.split")
        result.append(entry)
    return result


def _validate_review(value: Any) -> dict[str, Any]:
    review = _object(
        value,
        "review",
        {
            "state",
            "technical",
            "provenance",
            "rights",
            "license",
            "privacy",
            "feedback",
            "partitioning",
            "reviewed_at",
            "reviewers",
            "blockers",
            "notes",
        },
    )
    state = _enum(
        review["state"],
        "review.state",
        {"unreviewed", "in-review", "hold", "approved", "rejected", "withdrawn"},
    )
    dimensions = [
        "technical",
        "provenance",
        "rights",
        "license",
        "privacy",
        "feedback",
        "partitioning",
    ]
    for dimension in dimensions:
        _enum(review[dimension], f"review.{dimension}", REVIEW_DIMENSION_STATES)
    reviewed_at = _nullable_datetime(review["reviewed_at"], "review.reviewed_at")
    reviewers = _text_array(review["reviewers"], "review.reviewers")
    blockers = _text_array(review["blockers"], "review.blockers")
    _text_array(review["notes"], "review.notes", minimum=1)
    if state == "unreviewed":
        if reviewed_at is not None or reviewers:
            raise StewardshipError(
                "review.state unreviewed cannot name a review time or reviewers"
            )
        if any(review[item] not in {"unknown", "not-reviewed"} for item in dimensions):
            raise StewardshipError(
                "review.state unreviewed cannot claim completed dimension reviews"
            )
    if state in {"hold", "rejected"} and not blockers:
        raise StewardshipError(f"review.state {state} requires at least one blocker")
    if state == "approved":
        if reviewed_at is None or not reviewers or blockers:
            raise StewardshipError(
                "review.state approved requires reviewed_at, reviewers, and no blockers"
            )
        if any(review[item] != "pass" for item in dimensions):
            raise StewardshipError(
                "review.state approved requires every review dimension to pass"
            )
    return review


def _publication_blockers(manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if manifest["status"] not in {"approved", "published"}:
        blockers.append("status is not approved or published")
    huggingface = manifest["huggingface"]
    visibility = huggingface["visibility"]
    if visibility not in {"public", "gated", "private"}:
        blockers.append("Hugging Face visibility is not planned")
    if huggingface["repo_id"] is None:
        blockers.append("Hugging Face repo_id is not bound as owner/name")
    if not huggingface["config_names"]:
        blockers.append("Hugging Face config names are not declared")
    if huggingface["default_config"] is None:
        blockers.append("Hugging Face default config is not declared")
    if not huggingface["splits"]:
        blockers.append("Hugging Face splits are not declared")
    declared_configs = set(huggingface["config_names"])
    file_configs = {
        entry["config"] for entry in manifest["files"] if entry["config"] is not None
    }
    missing_file_configs = sorted(declared_configs - file_configs)
    if missing_file_configs:
        blockers.append(
            "Hugging Face configs are not bound to files: "
            + ", ".join(missing_file_configs)
        )
    file_splits = {entry["split"] for entry in manifest["files"]}
    declared_splits = set(huggingface["splits"])
    missing_file_splits = sorted(declared_splits - file_splits)
    if missing_file_splits:
        blockers.append(
            "Hugging Face splits are not bound to files: "
            + ", ".join(missing_file_splits)
        )
    source = manifest["source_provenance"]
    if source["class"] == "unknown" or not source["evidence"]:
        blockers.append("source provenance is unknown or unbound")
    licenses = manifest["licenses"]
    if (
        licenses["state"] != "verified"
        or not licenses["dataset_license"]
        or not licenses["components_complete"]
    ):
        blockers.append("licenses are not fully verified")
    consent = manifest["consent_and_reuse"]
    if (
        consent["state"] != "verified"
        or consent["basis"] == "unknown"
        or not consent["scope"]
        or not consent["evidence"]
    ):
        blockers.append("consent/reuse basis is not verified and scoped")
    privacy = manifest["privacy"]
    if visibility == "public" and privacy["classification"] != "public":
        blockers.append("privacy classification is not public")
    if visibility in {"gated", "private"} and privacy["classification"] == "unknown":
        blockers.append("privacy classification is unknown")
    if visibility in {"gated", "private"} and not huggingface["access_policy"]:
        blockers.append(
            "gated/private Hugging Face visibility requires an access policy"
        )
    for name in ("privacy_review", "pii_review", "secret_review"):
        if privacy[name]["state"] != "pass":
            blockers.append(f"privacy.{name} has not passed")
    if manifest["retention"]["state"] != "defined":
        blockers.append("retention is not defined")
    if manifest["withdrawal"]["state"] != "defined":
        blockers.append("withdrawal is not defined")
    feedback = manifest["feedback"]
    if feedback["presence"] == "unknown":
        blockers.append("feedback presence and nature are unknown")
    elif feedback["presence"] in {"present", "mixed"}:
        if feedback["rubric"]["state"] != "documented":
            blockers.append("feedback rubric is not documented and bound")
        if feedback["ties"]["policy"] == "unknown":
            blockers.append("tie handling is unknown")
        if feedback["uncertainty"]["representation"] == "unknown":
            blockers.append("uncertainty representation is unknown")
        if feedback["aggregation"]["method"] == "unknown":
            blockers.append("feedback aggregation is unknown")
        if feedback["aggregation"]["minority_preservation"] == "unknown":
            blockers.append("feedback minority-signal preservation is unknown")
    for name in ("split", "deduplication", "contamination"):
        if manifest["partitioning"][name]["state"] != "pass":
            blockers.append(f"partitioning.{name} has not passed")
    receipts = manifest["release_receipts"]
    for name in ("credit", "sensitive_content", "affected_parties", "remedy"):
        if receipts[name]["state"] != "pass":
            blockers.append(f"release receipt {name} has not passed")
    for name in ("generation", "feedback_detail", "grouping"):
        if receipts[name]["state"] not in {"pass", "not-applicable"}:
            blockers.append(
                f"release receipt {name} is neither passed nor evidenced not-applicable"
            )
    if (
        source["class"] in {"synthetic", "model-generated", "mixed"}
        and receipts["generation"]["state"] != "pass"
    ):
        blockers.append(
            f"source class {source['class']} requires a passed generation receipt"
        )
    if (
        feedback["presence"] in {"present", "mixed"}
        and receipts["feedback_detail"]["state"] != "pass"
    ):
        blockers.append(
            f"feedback presence {feedback['presence']} requires a passed feedback-detail receipt"
        )
    for entry in manifest["files"]:
        if entry["config"] is None:
            blockers.append(f"file {entry['path']} has no Hugging Face config")
        elif entry["config"] not in declared_configs:
            blockers.append(
                f"file {entry['path']} uses undeclared Hugging Face config "
                f"{entry['config']}"
            )
        if entry["split"] not in declared_splits:
            blockers.append(
                f"file {entry['path']} uses undeclared Hugging Face split "
                f"{entry['split']}"
            )
        if entry["format"] != "jsonl":
            blockers.append(
                f"file {entry['path']} uses format {entry['format']}; "
                "dataset-manifest/v1 publication eligibility only verifies JSONL"
            )
        elif PurePosixPath(entry["path"]).suffix.casefold() != ".jsonl":
            blockers.append(
                f"file {entry['path']} is declared JSONL but lacks a .jsonl suffix"
            )
        if entry["lane"] == "unknown" or entry["split"].casefold() == "unknown":
            blockers.append(f"file {entry['path']} has an unknown lane or split")
        if entry["bytes"] == 0 or entry["records"] == 0:
            blockers.append(f"file {entry['path']} is empty")
    review = manifest["review"]
    if review["state"] != "approved":
        blockers.append("review state is not approved")
    return blockers


def validate_manifest(
    value: Any, *, root: Path | str = ROOT, verify_files: bool = True
) -> dict[str, Any]:
    """Validate schema-shaped fields, semantic gates, and optional file bindings."""
    manifest = _object(value, "manifest", TOP_LEVEL_KEYS)
    if manifest["schema"] != SCHEMA_NAME:
        raise StewardshipError(f"schema must be {SCHEMA_NAME!r}")
    manifest_id = _string(manifest["manifest_id"], "manifest_id")
    dataset_id = _string(manifest["dataset_id"], "dataset_id")
    version = _string(manifest["version"], "version")
    if ID_RE.fullmatch(dataset_id) is None:
        raise StewardshipError("dataset_id contains unsupported characters or length")
    if VERSION_RE.fullmatch(version) is None:
        raise StewardshipError("version contains unsupported characters or length")
    if manifest_id != f"{dataset_id}@{version}":
        raise StewardshipError(
            "manifest_id must exactly bind dataset_id and version as dataset_id@version"
        )
    status_value = _enum(manifest["status"], "status", STATUS_VALUES)
    _datetime(manifest["created_at"], "created_at")
    if manifest["boundary"] != BOUNDARY:
        raise StewardshipError(
            "boundary must preserve the publication/consent/license authority limit"
        )
    _string(manifest["description"], "description")
    _validate_source(manifest["source_provenance"])
    _validate_licenses(manifest["licenses"])
    _validate_consent(manifest["consent_and_reuse"])
    _validate_privacy(manifest["privacy"])
    _validate_retention(manifest["retention"])
    _validate_withdrawal(manifest["withdrawal"])
    _validate_feedback(manifest["feedback"])
    _validate_partitioning(manifest["partitioning"])
    _validate_huggingface(manifest["huggingface"])
    _validate_release_receipts(manifest["release_receipts"])
    _validate_files(manifest["files"])
    _validate_review(manifest["review"])
    publication_eligible = _boolean(
        manifest["publication_eligible"], "publication_eligible"
    )
    _text_array(manifest["limitations"], "limitations", minimum=1)

    if status_value in {"legacy-unreviewed", "withdrawn"} and publication_eligible:
        raise StewardshipError(
            f"status {status_value} must set publication_eligible to false"
        )
    if status_value == "published":
        if manifest["huggingface"]["revision"] is None:
            raise StewardshipError(
                "status published requires an immutable 40-character Hugging Face revision"
            )
        if manifest["huggingface"]["viewer"]["state"] not in {
            "pass",
            "not-applicable",
        }:
            raise StewardshipError(
                "status published requires Hugging Face viewer state pass or not-applicable"
            )
    if status_value in {"approved", "published"} and not publication_eligible:
        raise StewardshipError(
            f"status {status_value} requires publication_eligible true"
        )
    if manifest["review"]["state"] == "approved" and not publication_eligible:
        raise StewardshipError("an approved review requires publication_eligible true")
    if publication_eligible:
        blockers = _publication_blockers(manifest)
        if blockers:
            raise StewardshipError(
                "publication_eligible is true but: " + "; ".join(blockers)
            )

    if verify_files:
        verify_manifest_files(manifest, Path(root))
    return manifest


def _regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise StewardshipError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise StewardshipError(f"{label} must be a regular non-symlink file: {path}")


def file_digest(path: Path | str) -> str:
    """Return a SHA-256 binding for one regular, non-symlink local file."""
    candidate = Path(path)
    _regular_file(candidate, "digest target")
    hasher = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _verify_jsonl(path: Path) -> int:
    records = 0
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                raise StewardshipError(
                    f"{path}: blank JSONL record at line {line_number}"
                )
            try:
                value = _strict_json_loads(raw)
            except _DuplicateJSONKeyError as exc:
                raise StewardshipError(
                    f"{path}: duplicate JSON object key {exc.key!r} at line {line_number}"
                ) from exc
            except _NonFiniteJSONNumberError as exc:
                raise StewardshipError(
                    f"{path}: non-finite JSON number {exc.value!r} at line {line_number}"
                ) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StewardshipError(
                    f"{path}: invalid JSONL at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise StewardshipError(
                    f"{path}: JSONL line {line_number} must be an object"
                )
            records += 1
    return records


def verify_manifest_files(manifest: dict[str, Any], root: Path) -> None:
    """Recompute every declared local file binding relative to one explicit root."""
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise StewardshipError(f"dataset root does not exist: {root}") from exc
    if not root.is_dir():
        raise StewardshipError(f"dataset root must be a directory: {root}")

    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        candidate = root.joinpath(*relative.parts)
        component = root
        for part in relative.parts:
            component = component / part
            try:
                component_info = component.lstat()
            except FileNotFoundError as exc:
                raise StewardshipError(
                    f"bound path does not exist: {entry['path']}"
                ) from exc
            if stat.S_ISLNK(component_info.st_mode):
                raise StewardshipError(
                    f"bound path contains a symlink: {entry['path']}"
                )
        _regular_file(candidate, f"bound file {entry['path']}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StewardshipError(
                f"bound file escapes the dataset root: {entry['path']}"
            ) from exc
        before = resolved.stat()
        actual_digest = file_digest(resolved)
        actual_records = _verify_jsonl(resolved) if entry["format"] == "jsonl" else None
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise StewardshipError(
                f"bound file changed while it was being checked: {entry['path']}"
            )
        if actual_digest != entry["sha256"]:
            raise StewardshipError(
                f"digest mismatch for {entry['path']}: manifest={entry['sha256']} actual={actual_digest}"
            )
        if after.st_size != entry["bytes"]:
            raise StewardshipError(
                f"byte count mismatch for {entry['path']}: manifest={entry['bytes']} actual={after.st_size}"
            )
        if actual_records is not None and actual_records != entry["records"]:
            raise StewardshipError(
                f"record count mismatch for {entry['path']}: manifest={entry['records']} actual={actual_records}"
            )


def canonical_manifest_digest(manifest: dict[str, Any]) -> str:
    """Bind all manifest fields with canonical JSON and SHA-256."""
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def load_manifest(path: Path | str) -> dict[str, Any]:
    candidate = Path(path)
    _regular_file(candidate, "manifest")
    if candidate.stat().st_size > MAX_MANIFEST_BYTES:
        raise StewardshipError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        value = _strict_json_loads(candidate.read_text(encoding="utf-8"))
    except _DuplicateJSONKeyError as exc:
        raise StewardshipError(
            f"manifest contains duplicate JSON object key {exc.key!r}: {candidate}"
        ) from exc
    except _NonFiniteJSONNumberError as exc:
        raise StewardshipError(
            f"manifest contains non-finite JSON number {exc.value!r}: {candidate}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StewardshipError(
            f"manifest is not valid UTF-8 JSON: {candidate}"
        ) from exc
    if not isinstance(value, dict):
        raise StewardshipError("manifest root must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline validation and exact digests for outbound dataset stewardship."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check", help="validate one manifest and its exact local files"
    )
    check.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    check.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="root against which manifest file paths are resolved (default: repository root)",
    )
    digest = subparsers.add_parser(
        "digest", help="print SHA-256 for regular local files"
    )
    digest.add_argument("paths", nargs="+", type=Path)
    subparsers.add_parser(
        "manifest-digest", help="print canonical digest for a manifest"
    ).add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            manifest = load_manifest(args.manifest)
            validate_manifest(manifest, root=args.root, verify_files=True)
            print(
                f"OK {manifest['manifest_id']} files={len(manifest['files'])} "
                f"publication_eligible={str(manifest['publication_eligible']).lower()} "
                f"manifest_digest={canonical_manifest_digest(manifest)}"
            )
        elif args.command == "digest":
            for path in args.paths:
                print(f"{file_digest(path)}  {path}")
        elif args.command == "manifest-digest":
            manifest = load_manifest(args.manifest)
            print(canonical_manifest_digest(manifest))
        else:  # pragma: no cover - argparse requires a known command.
            raise StewardshipError(f"unsupported command: {args.command}")
    except (OSError, StewardshipError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
