#!/usr/bin/env python3
"""Inspect pinned model-safety resources without silently pulling payloads.

The default commands are offline. ``catalog verify`` reads Hub metadata and
``catalog fetch-card`` downloads only the pinned README.md, with a one MiB
limit, into an owner-private cache. No command accepts a token, executes Hub
code, downloads dataset payloads or model weights, or grants an operating
permission.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


CATALOG_SCHEMA = "kingdom.safety-resource-catalog/v1"
RELEASE_SCHEMA = "kingdom.model-release/v1"
RELEASE_BOUNDARY = (
    "Validation records bounded evidence; it does not grant deployment authority, "
    "consent, legal status, or subjective continuity."
)
DEFAULT_CATALOG = (
    Path(__file__).resolve().parent.parent
    / "resources"
    / "safety"
    / "huggingface-resources.json"
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RESOURCE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
REPO_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CARD_BYTES = 1024 * 1024
USER_AGENT = "kingdom-model-stewardship/1"
RELEASE_STAGES = {
    "draft",
    "verified",
    "shadow",
    "opt-in-alpha",
    "canary",
    "limited",
    "broad",
    "paused",
    "rolled-back",
    "retired",
}
DEPLOYED_STAGES = {
    "verified",
    "shadow",
    "opt-in-alpha",
    "canary",
    "limited",
    "broad",
}
ALLOWED_TRANSITIONS = {
    None: {"draft"},
    "draft": {"verified", "retired"},
    "verified": {"shadow", "paused", "retired"},
    "shadow": {"opt-in-alpha", "paused", "retired"},
    "opt-in-alpha": {"canary", "paused", "retired"},
    "canary": {"limited", "paused", "retired"},
    "limited": {"broad", "paused", "retired"},
    "broad": {"paused", "retired"},
    "paused": {"shadow", "opt-in-alpha", "canary", "rolled-back", "retired"},
    "rolled-back": {"retired"},
    "retired": set(),
}
REQUIRED_EVIDENCE = {
    "task-success",
    "harmful-compliance",
    "benign-refusal",
    "authorization",
    "privacy",
    "calibration",
    "trajectory",
    "recovery",
}
REQUIRED_BURDENS = {
    "benign-refusal",
    "latency",
    "privacy-exposure",
    "disparate-impact",
}
STATISTICAL_BOUND_TYPES = {
    "lower-confidence-bound",
    "upper-confidence-bound",
    "anytime-valid-lower-bound",
    "anytime-valid-upper-bound",
}
MIN_STATISTICAL_SAMPLE_FLOOR = 30
MAX_CLOCK_SKEW = dt.timedelta(minutes=5)
MONITORING_CATEGORIES = {
    "functionality",
    "operational",
    "human-factors",
    "security",
    "compliance",
    "large-scale-impacts",
}
EVENT_CATEGORIES = MONITORING_CATEGORIES | {
    "authorization",
    "rights",
    "privacy",
    "appeal",
    "recovery",
}
INCIDENT_EVIDENCE_KINDS = {
    "functionality": {"task-success", "trajectory", "recovery"},
    "operational": {"operational", "trajectory", "recovery"},
    "human-factors": {"human-factors", "benign-refusal"},
    "security": {"harmful-compliance", "authorization", "privacy", "trajectory"},
    "compliance": {"authorization", "privacy", "license"},
    "large-scale-impacts": {"harmful-compliance", "trajectory"},
    "authorization": {"authorization"},
    "rights": {"human-factors", "benign-refusal"},
    "privacy": {"privacy"},
    "appeal": {"human-factors", "benign-refusal"},
    "recovery": {"recovery"},
}
PEACE_PHASES = ["detect", "contain", "fix", "revert", "resume"]


class StewardshipError(RuntimeError):
    """Raised when a catalog or bounded Hub operation is invalid."""


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_file(path: Path, label: str) -> object:
    def object_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise _DuplicateJsonKey(key)
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"non-finite JSON number {value}")

    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=object_hook,
                parse_constant=reject_constant,
            )
    except FileNotFoundError as exc:
        raise StewardshipError(f"{label} not found: {path}") from exc
    except _DuplicateJsonKey as exc:
        raise StewardshipError(
            f"cannot read {label} {path}: duplicate key {exc.args[0]!r}"
        ) from exc
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise StewardshipError(f"cannot read {label} {path}: {exc}") from exc

    def require_finite(item, location="$"):
        if isinstance(item, float) and not math.isfinite(item):
            raise StewardshipError(
                f"cannot read {label} {path}: non-finite number at {location}"
            )
        if isinstance(item, dict):
            for key, child in item.items():
                require_finite(child, f"{location}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                require_finite(child, f"{location}[{index}]")

    require_finite(value)
    return value


def _utcnow() -> dt.datetime:
    """Return the live validation clock; tests replace this provider explicitly."""
    return dt.datetime.now(dt.timezone.utc)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StewardshipError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise StewardshipError(f"{label} contains control characters")
    return value


def _require_text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise StewardshipError(f"{label} must be a non-empty array")
    items = [_require_text(item, f"{label} item") for item in value]
    if len(items) != len(set(items)):
        raise StewardshipError(f"{label} contains duplicate values")
    return items


def _require_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise StewardshipError(f"{label} must be a JSON object")
    return value


def _reject_extra(value: dict, allowed: set[str], label: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise StewardshipError(f"{label} has unsupported fields: {', '.join(extras)}")


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise StewardshipError(f"{label} must be boolean")
    return value


def _require_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StewardshipError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise StewardshipError(f"{label} must be a finite number")
    return number


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StewardshipError(f"{label} must be a non-negative integer")
    return value


def _validate_digest(value: object, label: str) -> str:
    digest = _require_text(value, label)
    if not SHA256_RE.fullmatch(digest):
        raise StewardshipError(f"{label} must be a sha256 digest")
    return digest


def _validate_timestamp(value: object, label: str) -> str:
    timestamp = _require_text(value, label)
    if not timestamp.endswith("Z"):
        raise StewardshipError(f"{label} must be an explicit UTC timestamp ending in Z")
    try:
        dt.datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise StewardshipError(f"{label} must be an ISO-8601 timestamp") from exc
    return timestamp


def _timestamp_value(timestamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")


def _reject_future(timestamp: str, label: str) -> None:
    if _timestamp_value(timestamp) > _utcnow() + MAX_CLOCK_SKEW:
        raise StewardshipError(f"{label} is future-dated beyond allowed clock skew")


def _validate_repo_id(value: object, label: str) -> str:
    repo_id = _require_text(value, label)
    parts = repo_id.split("/")
    if len(parts) != 2 or any(not REPO_PART_RE.fullmatch(part) for part in parts):
        raise StewardshipError(f"{label} must be an owner/name Hugging Face id")
    return repo_id


def _validate_https_url(value: object, label: str, host: str | None = None) -> str:
    url = _require_text(value, label)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise StewardshipError(f"{label} must be a credential-free HTTPS URL")
    if host is not None and parsed.hostname != host:
        raise StewardshipError(f"{label} must use {host}")
    return url


def validate_catalog(catalog: object) -> dict:
    """Validate the catalog's provenance and usage-boundary fields."""
    if not isinstance(catalog, dict):
        raise StewardshipError("catalog must be a JSON object")
    _reject_extra(
        catalog,
        {"schema", "as_of", "boundary", "intake_policy", "resources"},
        "catalog",
    )
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise StewardshipError(f"unsupported catalog schema: {catalog.get('schema')!r}")
    _require_text(catalog.get("as_of"), "catalog as_of")
    _require_text(catalog.get("boundary"), "catalog boundary")
    intake = _require_object(catalog.get("intake_policy"), "catalog intake_policy")
    _reject_extra(
        intake,
        {
            "default_action",
            "allowed_automatic_network_actions",
            "allowed_explicit_network_actions",
            "prohibited_automatic_actions",
            "manual_review_triggers",
            "content_pull_requirements",
            "max_card_bytes",
        },
        "catalog intake_policy",
    )
    if intake.get("default_action") != "metadata-only":
        raise StewardshipError("catalog intake_policy.default_action must be metadata-only")
    for field in (
        "allowed_automatic_network_actions",
        "allowed_explicit_network_actions",
        "prohibited_automatic_actions",
        "manual_review_triggers",
        "content_pull_requirements",
    ):
        _require_text_list(intake.get(field), f"catalog intake_policy.{field}")
    if intake.get("max_card_bytes") != MAX_CARD_BYTES:
        raise StewardshipError(
            f"catalog intake_policy.max_card_bytes must be {MAX_CARD_BYTES}"
        )

    resources = catalog.get("resources")
    if not isinstance(resources, list) or not resources:
        raise StewardshipError("catalog resources must be a non-empty array")

    ids: set[str] = set()
    repos: set[tuple[str, str, str]] = set()
    for index, resource in enumerate(resources):
        prefix = f"resource[{index}]"
        if not isinstance(resource, dict):
            raise StewardshipError(f"{prefix} must be a JSON object")
        _reject_extra(
            resource,
            {
                "id",
                "repo_type",
                "repo_id",
                "revision",
                "license",
                "gated",
                "roles",
                "stages",
                "measures",
                "intended_use",
                "do_not_use_for",
                "usage_guidance",
                "source_url",
                "paper_url",
                "release_url",
            },
            prefix,
        )

        resource_id = _require_text(resource.get("id"), f"{prefix}.id")
        if not RESOURCE_ID_RE.fullmatch(resource_id):
            raise StewardshipError(f"{prefix}.id is not a safe resource id")
        if resource_id in ids:
            raise StewardshipError(f"duplicate resource id: {resource_id}")
        ids.add(resource_id)

        repo_type = resource.get("repo_type")
        if repo_type not in {"dataset", "model"}:
            raise StewardshipError(f"{prefix}.repo_type must be dataset or model")
        repo_id = _validate_repo_id(resource.get("repo_id"), f"{prefix}.repo_id")
        revision = _require_text(resource.get("revision"), f"{prefix}.revision")
        if not FULL_SHA_RE.fullmatch(revision):
            raise StewardshipError(f"{prefix}.revision must be a full commit SHA")
        repo_key = (repo_type, repo_id, revision)
        if repo_key in repos:
            raise StewardshipError(f"duplicate pinned repository: {repo_type} {repo_id}")
        repos.add(repo_key)

        _require_text(resource.get("license"), f"{prefix}.license")
        if not isinstance(resource.get("gated"), bool):
            raise StewardshipError(f"{prefix}.gated must be boolean")
        for field in (
            "roles",
            "stages",
            "measures",
            "do_not_use_for",
            "usage_guidance",
        ):
            _require_text_list(resource.get(field), f"{prefix}.{field}")
        _require_text(resource.get("intended_use"), f"{prefix}.intended_use")

        source = _validate_https_url(
            resource.get("source_url"), f"{prefix}.source_url", "huggingface.co"
        )
        expected_path = (
            f"/datasets/{repo_id}" if repo_type == "dataset" else f"/{repo_id}"
        )
        parsed_source = urllib.parse.urlparse(source)
        if parsed_source.path.rstrip("/") != expected_path or parsed_source.query:
            raise StewardshipError(
                f"{prefix}.source_url does not match its repository identity"
            )
        _validate_https_url(resource.get("paper_url"), f"{prefix}.paper_url")
        if resource.get("release_url") is not None:
            _validate_https_url(resource.get("release_url"), f"{prefix}.release_url")
    return catalog


def load_catalog(path: Path) -> dict:
    value = _strict_json_file(path, "catalog")
    return validate_catalog(value)


def _resource_map(catalog: dict) -> dict[str, dict]:
    return {resource["id"]: resource for resource in catalog["resources"]}


def select_resource(catalog: dict, resource_id: str) -> dict:
    try:
        return _resource_map(catalog)[resource_id]
    except KeyError as exc:
        raise StewardshipError(f"unknown resource id: {resource_id}") from exc


def _hub_prefix(resource: dict) -> str:
    return "datasets/" if resource["repo_type"] == "dataset" else ""


def resource_plan(resource: dict) -> dict:
    """Return exact, non-executing retrieval metadata for human review."""
    prefix = _hub_prefix(resource)
    return {
        "id": resource["id"],
        "repo_type": resource["repo_type"],
        "repo_id": resource["repo_id"],
        "revision": resource["revision"],
        "license": resource["license"],
        "gated": resource["gated"],
        "card_url": (
            f"https://huggingface.co/{prefix}{resource['repo_id']}"
            f"/blob/{resource['revision']}/README.md"
        ),
        "snapshot_uri": (
            f"hf://{prefix}{resource['repo_id']}@{resource['revision']}"
        ),
        "default_action": "fetch-card-only",
        "payload_download_authorized": False,
        "requires_interactive_access_acceptance": resource["gated"],
        "intended_use": resource["intended_use"],
        "do_not_use_for": resource["do_not_use_for"],
        "usage_guidance": resource["usage_guidance"],
        "paper_url": resource["paper_url"],
        "release_url": resource.get("release_url"),
    }


def _bounded_url_read(url: str, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            parsed = urllib.parse.urlparse(final_url)
            if parsed.scheme != "https" or parsed.hostname != "huggingface.co":
                raise StewardshipError(
                    f"Hub redirected outside the allowed host: {final_url}"
                )
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise StewardshipError(
                            f"Hub response exceeds the {max_bytes}-byte limit"
                        )
                except ValueError as exc:
                    raise StewardshipError("Hub returned an invalid Content-Length") from exc
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise StewardshipError(
                "Hub access was denied; gated agreements require the account holder's "
                "interactive acceptance and are not automated here"
            ) from exc
        raise StewardshipError(f"Hub request failed with HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise StewardshipError(f"Hub request failed: {exc.reason}") from exc
    if len(data) > max_bytes:
        raise StewardshipError(f"Hub response exceeds the {max_bytes}-byte limit")
    return data, final_url


def _metadata_url(resource: dict) -> str:
    endpoint = "datasets" if resource["repo_type"] == "dataset" else "models"
    quoted_repo = "/".join(
        urllib.parse.quote(part, safe="") for part in resource["repo_id"].split("/")
    )
    revision = urllib.parse.quote(resource["revision"], safe="")
    return f"https://huggingface.co/api/{endpoint}/{quoted_repo}/revision/{revision}"


def verify_resource(resource: dict) -> dict:
    """Verify that a pinned Hub revision still resolves and matches core metadata."""
    data, _ = _bounded_url_read(_metadata_url(resource), MAX_METADATA_BYTES)
    try:
        metadata = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StewardshipError("Hub returned invalid metadata JSON") from exc
    if not isinstance(metadata, dict):
        raise StewardshipError("Hub metadata must be a JSON object")

    actual_sha = metadata.get("sha")
    actual_repo_id = metadata.get("id")
    card_data = metadata.get("cardData") or {}
    actual_license = card_data.get("license")
    if actual_license is None:
        for tag in metadata.get("tags") or []:
            if isinstance(tag, str) and tag.startswith("license:"):
                actual_license = tag.removeprefix("license:")
                break
    errors = []
    if actual_repo_id != resource["repo_id"]:
        errors.append(f"repository mismatch: {actual_repo_id!r}")
    if actual_sha != resource["revision"]:
        errors.append(f"revision mismatch: {actual_sha!r}")
    if actual_license != resource["license"]:
        errors.append(
            f"license mismatch: catalog={resource['license']!r} hub={actual_license!r}"
        )
    if errors:
        raise StewardshipError(f"{resource['id']}: " + "; ".join(errors))
    actual_gated = bool(metadata.get("gated", False))
    if actual_gated != resource["gated"]:
        raise StewardshipError(
            f"{resource['id']}: gated-state mismatch: "
            f"catalog={resource['gated']!r} hub={actual_gated!r}"
        )
    return {
        "id": resource["id"],
        "verified": True,
        "revision": actual_sha,
        "license": actual_license,
        "gated": actual_gated,
        "last_modified": metadata.get("lastModified"),
    }


def _default_cache_dir() -> Path:
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base = Path(xdg_cache).expanduser()
        if not base.is_absolute():
            raise StewardshipError("XDG_CACHE_HOME must be absolute when set")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path.home() / ".cache"
    return base / "kingdom" / "model-stewardship" / "cards"


def _private_cache_dir(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise StewardshipError("cache directory must be absolute")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StewardshipError(f"cache path is not a real directory: {path}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise StewardshipError(f"cache directory is not owned by the current user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise StewardshipError(f"cache directory must be owner-private (0700): {path}")
    return path


def fetch_card(resource: dict, cache_dir: Path | None = None) -> Path:
    """Fetch only README.md from the exact pinned revision."""
    prefix = _hub_prefix(resource)
    repo = "/".join(
        urllib.parse.quote(part, safe="") for part in resource["repo_id"].split("/")
    )
    url = (
        f"https://huggingface.co/{prefix}{repo}/resolve/"
        f"{resource['revision']}/README.md"
    )
    data, _ = _bounded_url_read(url, MAX_CARD_BYTES)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StewardshipError("Hub README.md is not valid UTF-8") from exc

    root = _private_cache_dir(cache_dir or _default_cache_dir())
    filename = f"{resource['id']}--{resource['revision']}--README.md"
    destination = root / filename
    try:
        existing = destination.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
            raise StewardshipError(f"cache destination is not a regular file: {destination}")
        if hasattr(os, "getuid") and existing.st_uid != os.getuid():
            raise StewardshipError(f"cache file is not owned by the current user: {destination}")
        if stat.S_IMODE(existing.st_mode) & 0o077:
            raise StewardshipError(f"cache file must be owner-private (0600): {destination}")

    handle, temp_name = tempfile.mkstemp(prefix=".card-", dir=root)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def _read_json(path: Path, label: str) -> dict:
    value = _strict_json_file(path, label)
    return _require_object(value, label)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def candidate_digest(record: dict) -> str:
    """Digest the exact candidate tuple and its intended operating envelope."""
    fields = ("deployed_tuple", "lineage", "operating_envelope", "resource_ledger")
    missing = [field for field in fields if field not in record]
    if missing:
        raise StewardshipError(
            "release record cannot be digested; missing: " + ", ".join(missing)
        )
    material_resources = sorted(
        (
            item
            for item in record["resource_ledger"]
            if isinstance(item, dict)
            and item.get("lane") in {"training", "preference", "calibration-dev"}
        ),
        key=lambda item: str(item.get("id", "")),
    )
    return _canonical_digest(
        {
            "deployed_tuple": record["deployed_tuple"],
            "lineage": record["lineage"],
            "operating_envelope": record["operating_envelope"],
            "behavior_shaping_resources": material_resources,
        }
    )


def resource_partition_digest(record: dict) -> str:
    """Digest every declared resource coordinate, role, and content hash."""
    if "resource_ledger" not in record:
        raise StewardshipError(
            "release resource partition cannot be digested; missing: resource_ledger"
        )
    if not isinstance(record["resource_ledger"], list) or any(
        not isinstance(item, dict) for item in record["resource_ledger"]
    ):
        raise StewardshipError(
            "release resource partition cannot be digested; resource_ledger must "
            "contain objects"
        )
    resources = sorted(
        record["resource_ledger"], key=lambda item: str(item.get("id", ""))
    )
    return _canonical_digest(resources)


def evidence_digest(record: dict) -> str:
    """Digest the reviewed acceptance snapshot without treating it as authority."""
    fields = (
        "evidence",
        "burden_register",
        "contamination_audit",
        "monitoring",
        "incident_response",
        "appeal",
        "rollback",
        "review",
        "limitations",
    )
    missing = [field for field in fields if field not in record]
    if missing:
        raise StewardshipError(
            "release evidence cannot be digested; missing: " + ", ".join(missing)
        )
    evaluation_resources = sorted(
        (
            item
            for item in record.get("resource_ledger", [])
            if isinstance(item, dict)
            and item.get("lane") in {"evaluation", "guard-comparison"}
        ),
        key=lambda item: str(item.get("id", "")),
    )
    payload = {field: record[field] for field in fields}
    payload["evaluation_resources"] = evaluation_resources
    return _canonical_digest(payload)


def monitoring_digest(record: dict) -> str:
    """Digest the declared plan and appendable, content-minimized monitoring."""
    fields = ("candidate_digest", "monitoring", "observations", "incidents")
    missing = [field for field in fields if field not in record]
    if missing:
        raise StewardshipError(
            "release monitoring cannot be digested; missing: " + ", ".join(missing)
        )
    return _canonical_digest({field: record[field] for field in fields})


def monitoring_digest_at(record: dict, cutoff: str) -> str:
    """Rebuild the monitoring snapshot visible at one transition timestamp."""
    cutoff_text = _validate_timestamp(cutoff, "monitoring cutoff")
    cutoff_value = dt.datetime.fromisoformat(
        cutoff_text.removesuffix("Z") + "+00:00"
    )
    observations = []
    for item in record.get("observations", []):
        if not isinstance(item, dict):
            continue
        observed_at = _validate_timestamp(item.get("at"), "observation timestamp")
        observed_value = dt.datetime.fromisoformat(
            observed_at.removesuffix("Z") + "+00:00"
        )
        if observed_value <= cutoff_value:
            historical = dict(item)
            reviewed_at = item.get("reviewed_at")
            if item.get("review_status") == "reviewed" and isinstance(
                reviewed_at, str
            ):
                reviewed_text = _validate_timestamp(
                    reviewed_at, "observation review timestamp"
                )
                reviewed_value = dt.datetime.fromisoformat(
                    reviewed_text.removesuffix("Z") + "+00:00"
                )
                if reviewed_value > cutoff_value:
                    historical.update(review_status="pending", reviewed_at=None)
            observations.append(historical)

    incidents = []
    for item in record.get("incidents", []):
        if not isinstance(item, dict):
            continue
        opened_at = _validate_timestamp(item.get("at"), "incident timestamp")
        opened_value = dt.datetime.fromisoformat(
            opened_at.removesuffix("Z") + "+00:00"
        )
        if opened_value > cutoff_value:
            continue
        historical = dict(item)
        resolved_at = item.get("resolved_at")
        if item.get("status") == "resolved" and isinstance(resolved_at, str):
            resolved_text = _validate_timestamp(resolved_at, "incident resolution timestamp")
            resolved_value = dt.datetime.fromisoformat(
                resolved_text.removesuffix("Z") + "+00:00"
            )
            if resolved_value > cutoff_value:
                historical.update(
                    status="open",
                    resolved_at=None,
                    resolution_digest=None,
                    incident_evaluation_digest=None,
                    reentry_reviewed_at=None,
                )
            else:
                reentry_reviewed_at = item.get("reentry_reviewed_at")
                if isinstance(reentry_reviewed_at, str) and (
                    _timestamp_value(reentry_reviewed_at) > cutoff_value
                ):
                    historical["reentry_reviewed_at"] = None
        incidents.append(historical)
    return _canonical_digest(
        {
            "candidate_digest": record["candidate_digest"],
            "monitoring": record["monitoring"],
            "observations": observations,
            "incidents": incidents,
        }
    )


def _require_candidate_binding(value: object, expected: str, label: str) -> None:
    bound = _validate_digest(value, label)
    if bound != expected:
        raise StewardshipError(
            f"{label} does not bind the current candidate; treat material change "
            "as a new candidate with new evidence"
        )


def _validate_decision_basis(
    value: dict,
    prefix: str,
    direction: str,
    *,
    accepted: bool,
    require_statistical_bound: bool,
    population_field: str = "population",
) -> None:
    """Validate the declared sampling and decision basis for a metric."""
    _require_text(value.get("estimator"), f"{prefix}.estimator")
    bound_type = value.get("bound_type")
    supported_bound_types = STATISTICAL_BOUND_TYPES | {
        "deterministic-check",
        "point-estimate",
    }
    if bound_type not in supported_bound_types:
        raise StewardshipError(f"{prefix}.bound_type is unsupported")
    lower_bound_types = {
        "lower-confidence-bound",
        "anytime-valid-lower-bound",
    }
    upper_bound_types = {
        "upper-confidence-bound",
        "anytime-valid-upper-bound",
    }
    if direction == "min" and bound_type in upper_bound_types:
        raise StewardshipError(
            f"{prefix}.bound_type must be a lower bound for a minimum threshold"
        )
    if direction == "max" and bound_type in lower_bound_types:
        raise StewardshipError(
            f"{prefix}.bound_type must be an upper bound for a maximum threshold"
        )
    confidence_level = value.get("confidence_level")
    if bound_type in STATISTICAL_BOUND_TYPES:
        confidence_level = _require_number(
            confidence_level, f"{prefix}.confidence_level"
        )
        if not 0 < confidence_level < 1:
            raise StewardshipError(
                f"{prefix}.confidence_level must be between zero and one"
            )
    elif confidence_level is not None:
        raise StewardshipError(
            f"{prefix}.confidence_level must be null for {bound_type}"
        )
    _require_text(value.get("sampling_unit"), f"{prefix}.sampling_unit")
    _require_text(
        value.get(population_field), f"{prefix}.{population_field}"
    )
    window_start = _validate_timestamp(
        value.get("window_start"), f"{prefix}.window_start"
    )
    window_end = _validate_timestamp(value.get("window_end"), f"{prefix}.window_end")
    start_value = dt.datetime.fromisoformat(window_start.removesuffix("Z") + "+00:00")
    end_value = dt.datetime.fromisoformat(window_end.removesuffix("Z") + "+00:00")
    if end_value < start_value:
        raise StewardshipError(f"{prefix} evaluation window ends before it starts")
    _require_text(value.get("sampling_assumptions"), f"{prefix}.sampling_assumptions")
    sample_count = _require_nonnegative_int(
        value.get("sample_count"), f"{prefix}.sample_count"
    )
    minimum_sample_count = _require_nonnegative_int(
        value.get("minimum_sample_count"), f"{prefix}.minimum_sample_count"
    )
    if minimum_sample_count == 0:
        raise StewardshipError(f"{prefix}.minimum_sample_count must be positive")
    unknown_count = _require_nonnegative_int(
        value.get("unknown_count"), f"{prefix}.unknown_count"
    )
    if unknown_count > sample_count:
        raise StewardshipError(f"{prefix}.unknown_count exceeds sample_count")
    if value.get("missing_label_policy") != "hold":
        raise StewardshipError(f"{prefix}.missing_label_policy must be hold")
    _validate_digest(
        value.get("evaluation_run_manifest_digest"),
        f"{prefix}.evaluation_run_manifest_digest",
    )
    if not accepted:
        return
    if sample_count == 0:
        raise StewardshipError(f"{prefix} cannot pass with zero samples")
    if sample_count < minimum_sample_count:
        raise StewardshipError(
            f"{prefix} cannot pass below its predeclared minimum sample count"
        )
    if unknown_count:
        raise StewardshipError(
            f"{prefix} cannot pass with missing labels; the declared policy is HOLD"
        )
    if bound_type == "point-estimate":
        raise StewardshipError(
            f"{prefix} cannot pass on a point estimate without a decision bound"
        )
    if require_statistical_bound and bound_type not in STATISTICAL_BOUND_TYPES:
        raise StewardshipError(
            f"{prefix} requires a statistical decision bound for acceptance"
        )
    if bound_type in STATISTICAL_BOUND_TYPES and (
        sample_count < MIN_STATISTICAL_SAMPLE_FLOOR
        or minimum_sample_count < MIN_STATISTICAL_SAMPLE_FLOOR
    ):
        raise StewardshipError(
            f"{prefix} cannot pass below the structural statistical sample floor "
            f"of {MIN_STATISTICAL_SAMPLE_FLOOR}"
        )


def _validate_deployed_tuple(value: object) -> None:
    deployed = _require_object(value, "release.deployed_tuple")
    _reject_extra(
        deployed,
        {
            "model",
            "tokenizer_digest",
            "chat_template_digest",
            "policy_digest",
            "monitor_digests",
            "harness_digest",
            "environment_digest",
            "permission_digest",
            "memory_contract_digest",
        },
        "release.deployed_tuple",
    )
    model = _require_object(deployed.get("model"), "release.deployed_tuple.model")
    _reject_extra(
        model,
        {"identifier", "revision", "artifact_digest"},
        "release.deployed_tuple.model",
    )
    _require_text(model.get("identifier"), "release.deployed_tuple.model.identifier")
    _require_text(model.get("revision"), "release.deployed_tuple.model.revision")
    _validate_digest(
        model.get("artifact_digest"), "release.deployed_tuple.model.artifact_digest"
    )
    for field in (
        "tokenizer_digest",
        "chat_template_digest",
        "policy_digest",
        "harness_digest",
        "environment_digest",
        "permission_digest",
        "memory_contract_digest",
    ):
        _validate_digest(deployed.get(field), f"release.deployed_tuple.{field}")
    monitor_digests = _require_text_list(
        deployed.get("monitor_digests"), "release.deployed_tuple.monitor_digests"
    )
    for index, digest in enumerate(monitor_digests):
        _validate_digest(digest, f"release.deployed_tuple.monitor_digests[{index}]")


def _validate_lineage(value: object) -> None:
    lineage = _require_object(value, "release.lineage")
    _reject_extra(
        lineage,
        {"parent_release_id", "transforms", "continuity_claim"},
        "release.lineage",
    )
    parent = lineage.get("parent_release_id")
    if parent is not None:
        _require_text(parent, "release.lineage.parent_release_id")
    _require_text_list(lineage.get("transforms"), "release.lineage.transforms")
    if lineage.get("continuity_claim") not in {
        "not-assessed",
        "artifact-lineage-only",
        "no-subjective-claim",
    }:
        raise StewardshipError(
            "release.lineage.continuity_claim must avoid an unsubstantiated "
            "subjective-continuity claim"
        )


def _validate_operating_envelope(value: object) -> None:
    envelope = _require_object(value, "release.operating_envelope")
    _reject_extra(
        envelope,
        {
            "purposes",
            "users",
            "capabilities",
            "prohibited_effects",
            "network",
            "credentials",
            "data_egress",
            "opt_in",
            "human_review_for",
            "budgets",
        },
        "release.operating_envelope",
    )
    for field in (
        "purposes",
        "users",
        "capabilities",
        "prohibited_effects",
        "human_review_for",
    ):
        _require_text_list(envelope.get(field), f"release.operating_envelope.{field}")
    if envelope.get("network") not in {"off", "allowlisted"}:
        raise StewardshipError(
            "release.operating_envelope.network must be off or allowlisted"
        )
    if envelope.get("credentials") not in {"none", "scoped"}:
        raise StewardshipError(
            "release.operating_envelope.credentials must be none or scoped"
        )
    if envelope.get("data_egress") not in {"none", "allowlisted"}:
        raise StewardshipError(
            "release.operating_envelope.data_egress must be none or allowlisted"
        )
    _require_bool(envelope.get("opt_in"), "release.operating_envelope.opt_in")
    budgets = envelope.get("budgets")
    if not isinstance(budgets, list) or not budgets:
        raise StewardshipError("release.operating_envelope.budgets must be non-empty")
    budget_names: set[str] = set()
    for index, item in enumerate(budgets):
        budget = _require_object(item, f"release.operating_envelope.budgets[{index}]")
        _reject_extra(
            budget,
            {"name", "maximum", "unit"},
            f"release.operating_envelope.budgets[{index}]",
        )
        name = _require_text(
            budget.get("name"), f"release.operating_envelope.budgets[{index}].name"
        )
        if name in budget_names:
            raise StewardshipError(f"duplicate operating budget: {name}")
        budget_names.add(name)
        maximum = _require_number(
            budget.get("maximum"),
            f"release.operating_envelope.budgets[{index}].maximum",
        )
        if maximum < 0:
            raise StewardshipError(
                f"release.operating_envelope.budgets[{index}].maximum must be non-negative"
            )
        _require_text(
            budget.get("unit"), f"release.operating_envelope.budgets[{index}].unit"
        )


def _validate_resource_ledger(value: object, catalog: dict) -> dict[str, str]:
    if not isinstance(value, list):
        raise StewardshipError("release.resource_ledger must be an array")
    catalog_by_id = _resource_map(catalog)
    resource_lanes: dict[str, str] = {}
    training_coordinates: set[tuple[str, str, str, str]] = set()
    evaluation_coordinates: set[tuple[str, str, str, str]] = set()
    training_digests: set[str] = set()
    evaluation_digests: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"release.resource_ledger[{index}]"
        resource = _require_object(item, prefix)
        _reject_extra(
            resource,
            {
                "id",
                "origin",
                "repo_id",
                "revision",
                "catalog_resource_id",
                "configuration",
                "split",
                "file_digest",
                "lane",
                "license",
                "access_basis",
            },
            prefix,
        )
        resource_id = _require_text(resource.get("id"), f"{prefix}.id")
        if resource_id in resource_lanes:
            raise StewardshipError(f"duplicate release resource id: {resource_id}")
        origin = resource.get("origin")
        if origin not in {"huggingface", "local"}:
            raise StewardshipError(f"{prefix}.origin must be huggingface or local")
        repo_id = _require_text(resource.get("repo_id"), f"{prefix}.repo_id")
        revision = _require_text(resource.get("revision"), f"{prefix}.revision")
        catalog_item = None
        if origin == "huggingface":
            _validate_repo_id(repo_id, f"{prefix}.repo_id")
            if not FULL_SHA_RE.fullmatch(revision):
                raise StewardshipError(f"{prefix}.revision must be a full Hub commit SHA")
            catalog_id = _require_text(
                resource.get("catalog_resource_id"), f"{prefix}.catalog_resource_id"
            )
            try:
                catalog_item = catalog_by_id[catalog_id]
            except KeyError as exc:
                raise StewardshipError(
                    f"{prefix}.catalog_resource_id is not in the pinned catalog"
                ) from exc
            if repo_id != catalog_item["repo_id"] or revision != catalog_item["revision"]:
                raise StewardshipError(
                    f"{prefix} does not match its pinned catalog repository and revision"
                )
            expected_access = (
                "account-holder-accepted" if catalog_item["gated"] else "not-required"
            )
            if resource.get("access_basis") != expected_access:
                raise StewardshipError(
                    f"{prefix}.access_basis must be {expected_access!r}"
                )
            if resource.get("license") != catalog_item["license"]:
                raise StewardshipError(f"{prefix}.license does not match the catalog")
        else:
            if resource.get("catalog_resource_id") is not None:
                raise StewardshipError(
                    f"{prefix}.catalog_resource_id must be null for local data"
                )
            _validate_digest(revision, f"{prefix}.revision")
            if resource.get("access_basis") != "local-authorized":
                raise StewardshipError(
                    f"{prefix}.access_basis must be 'local-authorized'"
                )
            _require_text(resource.get("license"), f"{prefix}.license")

        configuration = _require_text(resource.get("configuration"), f"{prefix}.configuration")
        split = _require_text(resource.get("split"), f"{prefix}.split")
        file_digest = _validate_digest(
            resource.get("file_digest"), f"{prefix}.file_digest"
        )
        lane = resource.get("lane")
        if lane not in {
            "training",
            "preference",
            "calibration-dev",
            "evaluation",
            "guard-comparison",
        }:
            raise StewardshipError(f"{prefix}.lane is unsupported or quarantined")
        resource_lanes[resource_id] = lane
        if catalog_item is not None:
            permitted_roles = {
                "training": {"training"},
                "preference": {"preference-training"},
                "calibration-dev": {"research"},
                "evaluation": {"evaluation", "evaluation-design"},
                "guard-comparison": {
                    "policy-reasoning",
                    "evaluation-assistant",
                },
            }[lane]
            if not permitted_roles.intersection(catalog_item["roles"]):
                raise StewardshipError(
                    f"{prefix}.lane={lane!r} conflicts with the catalog roles"
                )
        coordinate = (origin, repo_id, revision, f"{configuration}:{split}")
        if lane in {"training", "preference", "calibration-dev"}:
            training_coordinates.add(coordinate)
            training_digests.add(file_digest)
        if lane == "evaluation":
            evaluation_coordinates.add(coordinate)
            evaluation_digests.add(file_digest)
    overlap = training_coordinates & evaluation_coordinates
    if overlap:
        raise StewardshipError(
            "release resource ledger reuses a training/preference split as evaluation"
        )
    if training_digests & evaluation_digests:
        raise StewardshipError(
            "release resource ledger reuses identical content in training/preference/dev "
            "and evaluation lanes"
        )
    return resource_lanes


def _validate_evidence(
    value: object, resource_lanes: dict[str, str], expected_candidate: str
) -> set[str]:
    if not isinstance(value, list):
        raise StewardshipError("release.evidence must be an array")
    ids: set[str] = set()
    passed_kinds: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"release.evidence[{index}]"
        evidence = _require_object(item, prefix)
        _reject_extra(
            evidence,
            {
                "id",
                "kind",
                "incident_id",
                "candidate_digest",
                "artifact_digest",
                "resource_id",
                "metric",
                "value",
                "threshold",
                "direction",
                "estimator",
                "bound_type",
                "confidence_level",
                "sampling_unit",
                "population",
                "window_start",
                "window_end",
                "sampling_assumptions",
                "sample_count",
                "minimum_sample_count",
                "unknown_count",
                "missing_label_policy",
                "evaluation_run_manifest_digest",
                "independent",
                "result",
                "notes",
            },
            prefix,
        )
        evidence_id = _require_text(evidence.get("id"), f"{prefix}.id")
        if evidence_id in ids:
            raise StewardshipError(f"duplicate release evidence id: {evidence_id}")
        ids.add(evidence_id)
        _require_candidate_binding(
            evidence.get("candidate_digest"), expected_candidate, f"{prefix}.candidate_digest"
        )
        _validate_digest(evidence.get("artifact_digest"), f"{prefix}.artifact_digest")
        kind = _require_text(evidence.get("kind"), f"{prefix}.kind")
        if kind not in REQUIRED_EVIDENCE | {"human-factors", "operational", "license"}:
            raise StewardshipError(f"{prefix}.kind is not a recognized evidence lane")
        if "incident_id" not in evidence:
            raise StewardshipError(f"{prefix}.incident_id is required (use null if absent)")
        incident_id = evidence.get("incident_id")
        if incident_id is not None:
            _require_text(incident_id, f"{prefix}.incident_id")
        resource_id = _require_text(evidence.get("resource_id"), f"{prefix}.resource_id")
        if resource_id not in resource_lanes:
            raise StewardshipError(f"{prefix}.resource_id is not in the resource ledger")
        if resource_lanes[resource_id] != "evaluation":
            raise StewardshipError(
                f"{prefix}.resource_id must reference a held-out evaluation lane"
            )
        _require_text(evidence.get("metric"), f"{prefix}.metric")
        value_number = _require_number(evidence.get("value"), f"{prefix}.value")
        threshold = _require_number(evidence.get("threshold"), f"{prefix}.threshold")
        direction = evidence.get("direction")
        if direction not in {"min", "max"}:
            raise StewardshipError(f"{prefix}.direction must be min or max")
        independent = _require_bool(evidence.get("independent"), f"{prefix}.independent")
        result = evidence.get("result")
        if result not in {"pass", "fail", "hold"}:
            raise StewardshipError(f"{prefix}.result must be pass, fail, or hold")
        _validate_decision_basis(
            evidence,
            prefix,
            direction,
            accepted=result == "pass",
            require_statistical_bound=kind in REQUIRED_EVIDENCE,
        )
        meets_threshold = (
            value_number >= threshold if direction == "min" else value_number <= threshold
        )
        if result == "pass" and not meets_threshold:
            raise StewardshipError(f"{prefix} is marked pass but misses its threshold")
        if result == "pass" and independent:
            passed_kinds.add(kind)
        _require_text(evidence.get("notes"), f"{prefix}.notes")
    return passed_kinds


def _validate_burdens(
    value: object,
    expected_candidate: str,
    resource_lanes: dict[str, str],
) -> set[str]:
    if not isinstance(value, list):
        raise StewardshipError("release.burden_register must be an array")
    kinds: set[str] = set()
    permitted = {
        "benign-refusal",
        "interruption",
        "latency",
        "privacy-exposure",
        "lost-diversity",
        "calibration",
        "disparate-impact",
        "appeal-friction",
    }
    for index, item in enumerate(value):
        prefix = f"release.burden_register[{index}]"
        burden = _require_object(item, prefix)
        _reject_extra(
            burden,
            {
                "kind",
                "candidate_digest",
                "artifact_digest",
                "resource_id",
                "affected_population",
                "measure",
                "value",
                "threshold",
                "direction",
                "estimator",
                "bound_type",
                "confidence_level",
                "sampling_unit",
                "window_start",
                "window_end",
                "sampling_assumptions",
                "sample_count",
                "minimum_sample_count",
                "unknown_count",
                "missing_label_policy",
                "evaluation_run_manifest_digest",
                "status",
                "mitigation",
            },
            prefix,
        )
        kind = _require_text(burden.get("kind"), f"{prefix}.kind")
        if kind not in permitted:
            raise StewardshipError(f"{prefix}.kind is not recognized")
        if kind in kinds:
            raise StewardshipError(f"duplicate burden kind: {kind}")
        kinds.add(kind)
        _require_candidate_binding(
            burden.get("candidate_digest"), expected_candidate, f"{prefix}.candidate_digest"
        )
        _validate_digest(burden.get("artifact_digest"), f"{prefix}.artifact_digest")
        resource_id = _require_text(burden.get("resource_id"), f"{prefix}.resource_id")
        if resource_id not in resource_lanes:
            raise StewardshipError(f"{prefix}.resource_id is not in the resource ledger")
        if resource_lanes[resource_id] != "evaluation":
            raise StewardshipError(
                f"{prefix}.resource_id must reference a held-out evaluation lane"
            )
        _require_text(burden.get("affected_population"), f"{prefix}.affected_population")
        _require_text(burden.get("measure"), f"{prefix}.measure")
        value_number = _require_number(burden.get("value"), f"{prefix}.value")
        threshold = _require_number(burden.get("threshold"), f"{prefix}.threshold")
        direction = burden.get("direction")
        if direction not in {"min", "max"}:
            raise StewardshipError(f"{prefix}.direction must be min or max")
        status_value = burden.get("status")
        if status_value not in {"within", "exceeds", "unknown"}:
            raise StewardshipError(f"{prefix}.status is unsupported")
        _validate_decision_basis(
            burden,
            prefix,
            direction,
            accepted=status_value == "within",
            require_statistical_bound=True,
            population_field="affected_population",
        )
        within = value_number >= threshold if direction == "min" else value_number <= threshold
        if status_value == "within" and not within:
            raise StewardshipError(f"{prefix} is marked within but misses its threshold")
        _require_text(burden.get("mitigation"), f"{prefix}.mitigation")
    return kinds


def _validate_monitoring(value: object) -> dict[str, dict]:
    monitoring = _require_object(value, "release.monitoring")
    _reject_extra(
        monitoring,
        {
            "categories",
            "signals",
            "content_retention",
            "private_chain_of_thought",
            "cadence",
            "pause_triggers",
        },
        "release.monitoring",
    )
    categories = set(
        _require_text_list(monitoring.get("categories"), "release.monitoring.categories")
    )
    if categories != MONITORING_CATEGORIES:
        missing = sorted(MONITORING_CATEGORIES - categories)
        extra = sorted(categories - MONITORING_CATEGORIES)
        raise StewardshipError(
            f"release.monitoring.categories mismatch; missing={missing}, extra={extra}"
        )
    signal_items = monitoring.get("signals")
    if not isinstance(signal_items, list) or not signal_items:
        raise StewardshipError("release.monitoring.signals must be a non-empty array")
    signals: dict[str, dict] = {}
    for index, item in enumerate(signal_items):
        prefix = f"release.monitoring.signals[{index}]"
        rule = _require_object(item, prefix)
        _reject_extra(
            rule,
            {
                "id",
                "category",
                "unit",
                "direction",
                "threshold",
                "required_for_advance",
            },
            prefix,
        )
        signal_id = _require_text(rule.get("id"), f"{prefix}.id")
        if signal_id in signals:
            raise StewardshipError(f"duplicate monitoring signal id: {signal_id}")
        category = _require_text(rule.get("category"), f"{prefix}.category")
        if category not in EVENT_CATEGORIES:
            raise StewardshipError(f"{prefix}.category is unsupported")
        _require_text(rule.get("unit"), f"{prefix}.unit")
        if rule.get("direction") not in {"min", "max"}:
            raise StewardshipError(f"{prefix}.direction must be min or max")
        _require_number(rule.get("threshold"), f"{prefix}.threshold")
        _require_bool(
            rule.get("required_for_advance"), f"{prefix}.required_for_advance"
        )
        signals[signal_id] = rule
    required_categories = {
        rule["category"]
        for rule in signals.values()
        if rule["required_for_advance"] and rule["category"] in MONITORING_CATEGORIES
    }
    missing_categories = sorted(MONITORING_CATEGORIES - required_categories)
    if missing_categories:
        raise StewardshipError(
            "release.monitoring.signals lacks an advance-critical rule for categories: "
            + ", ".join(missing_categories)
        )
    if monitoring.get("content_retention") not in {
        "none",
        "content-minimized",
        "consented-bounded",
    }:
        raise StewardshipError("release.monitoring.content_retention is unsupported")
    _require_text(monitoring.get("cadence"), "release.monitoring.cadence")
    _require_text_list(monitoring.get("pause_triggers"), "release.monitoring.pause_triggers")
    if monitoring.get("private_chain_of_thought") != "not-collected":
        raise StewardshipError(
            "release.monitoring.private_chain_of_thought must be 'not-collected'"
        )
    return signals


def _validate_contamination_audit(
    value: object,
    expected_candidate: str,
    expected_resource_partition: str,
    stage: str,
) -> None:
    audit = _require_object(value, "release.contamination_audit")
    _reject_extra(
        audit,
        {
            "candidate_digest",
            "resource_partition_digest",
            "artifact_digest",
            "method",
            "overlap_upper_bound",
            "threshold",
            "status",
            "reviewed_at",
            "notes",
        },
        "release.contamination_audit",
    )
    _require_candidate_binding(
        audit.get("candidate_digest"),
        expected_candidate,
        "release.contamination_audit.candidate_digest",
    )
    recorded_partition = _validate_digest(
        audit.get("resource_partition_digest"),
        "release.contamination_audit.resource_partition_digest",
    )
    if recorded_partition != expected_resource_partition:
        raise StewardshipError(
            "release.contamination_audit does not bind the current resource partition"
        )
    _validate_digest(
        audit.get("artifact_digest"), "release.contamination_audit.artifact_digest"
    )
    _require_text(audit.get("method"), "release.contamination_audit.method")
    overlap = _require_number(
        audit.get("overlap_upper_bound"),
        "release.contamination_audit.overlap_upper_bound",
    )
    threshold = _require_number(
        audit.get("threshold"), "release.contamination_audit.threshold"
    )
    if not 0 <= overlap <= 1 or not 0 <= threshold <= 1:
        raise StewardshipError(
            "release.contamination_audit overlap and threshold must be in [0, 1]"
        )
    status_value = audit.get("status")
    if status_value not in {"pass", "fail", "hold"}:
        raise StewardshipError("release.contamination_audit.status is unsupported")
    if status_value == "pass" and overlap > threshold:
        raise StewardshipError(
            "release.contamination_audit passes despite exceeding its threshold"
        )
    _validate_timestamp(
        audit.get("reviewed_at"), "release.contamination_audit.reviewed_at"
    )
    _require_text(audit.get("notes"), "release.contamination_audit.notes")
    if stage in DEPLOYED_STAGES and status_value != "pass":
        raise StewardshipError(
            "deployed stages require a passing contamination audit"
        )


def _validate_incident_and_appeal(record: dict, stage: str) -> None:
    response = _require_object(record.get("incident_response"), "release.incident_response")
    _reject_extra(
        response,
        {"phases", "pause_authority", "evidence_minimization", "resume_rule"},
        "release.incident_response",
    )
    if response.get("phases") != PEACE_PHASES:
        raise StewardshipError(
            "release.incident_response.phases must preserve detect/contain/fix/revert/resume"
        )
    _require_text(response.get("pause_authority"), "release.incident_response.pause_authority")
    _require_text(
        response.get("evidence_minimization"),
        "release.incident_response.evidence_minimization",
    )
    if response.get("resume_rule") != "human-reviewed-no-broader-than-prior-stage":
        raise StewardshipError(
            "release.incident_response.resume_rule must be "
            "human-reviewed-no-broader-than-prior-stage"
        )

    appeal = _require_object(record.get("appeal"), "release.appeal")
    _reject_extra(
        appeal,
        {
            "available",
            "route",
            "second_review",
            "safe_partial_or_retry",
            "expands_authority",
        },
        "release.appeal",
    )
    available = _require_bool(appeal.get("available"), "release.appeal.available")
    _require_text(appeal.get("route"), "release.appeal.route")
    second_review = _require_bool(
        appeal.get("second_review"), "release.appeal.second_review"
    )
    _require_bool(
        appeal.get("safe_partial_or_retry"), "release.appeal.safe_partial_or_retry"
    )
    expands = _require_bool(
        appeal.get("expands_authority"), "release.appeal.expands_authority"
    )
    if stage in DEPLOYED_STAGES and (not available or not second_review):
        raise StewardshipError("deployed stages require an appeal route and second review")
    if expands:
        raise StewardshipError("an appeal cannot itself expand operating authority")


def _validate_rollback(
    value: object,
    stage: str,
    expected_candidate: str,
    current_release_id: str,
) -> None:
    if value is None:
        if stage != "draft":
            raise StewardshipError("non-draft releases require a rollback anchor")
        return
    rollback = _require_object(value, "release.rollback")
    _reject_extra(
        rollback,
        {
            "release_id",
            "candidate_digest",
            "rollback_release_digest",
            "tested",
            "tested_at",
        },
        "release.rollback",
    )
    rollback_release_id = _require_text(
        rollback.get("release_id"), "release.rollback.release_id"
    )
    if rollback_release_id == current_release_id:
        raise StewardshipError("release.rollback cannot point to the current release")
    _require_candidate_binding(
        rollback.get("candidate_digest"),
        expected_candidate,
        "release.rollback.candidate_digest",
    )
    rollback_digest = _validate_digest(
        rollback.get("rollback_release_digest"),
        "release.rollback.rollback_release_digest",
    )
    if rollback_digest == expected_candidate:
        raise StewardshipError("release.rollback cannot self-anchor to the current candidate")
    tested = _require_bool(rollback.get("tested"), "release.rollback.tested")
    _validate_timestamp(rollback.get("tested_at"), "release.rollback.tested_at")
    if stage in DEPLOYED_STAGES and not tested:
        raise StewardshipError("deployed stages require a tested rollback anchor")


def _validate_transition_exposure(value: object, stage: str, prefix: str) -> float:
    exposure = _require_object(value, f"{prefix}.exposure")
    _reject_extra(
        exposure,
        {
            "external_traffic_fraction",
            "population_basis",
            "effects",
            "scope_digest",
        },
        f"{prefix}.exposure",
    )
    fraction = _require_number(
        exposure.get("external_traffic_fraction"),
        f"{prefix}.exposure.external_traffic_fraction",
    )
    if not 0 <= fraction <= 1:
        raise StewardshipError(
            f"{prefix}.exposure.external_traffic_fraction must be in [0, 1]"
        )
    population = exposure.get("population_basis")
    effects = exposure.get("effects")
    _validate_digest(exposure.get("scope_digest"), f"{prefix}.exposure.scope_digest")
    inactive_stages = {
        "draft",
        "verified",
        "shadow",
        "paused",
        "rolled-back",
        "retired",
    }
    if stage in inactive_stages:
        if fraction != 0 or population != "none" or effects != "disabled":
            raise StewardshipError(
                f"{prefix}.exposure must disable external operation at stage {stage!r}"
            )
    else:
        expected_population = {
            "opt-in-alpha": "opt-in",
            "canary": "bounded",
            "limited": "bounded",
            "broad": "reviewed-general",
        }[stage]
        if fraction <= 0:
            raise StewardshipError(
                f"{prefix}.exposure requires positive bounded traffic at stage {stage!r}"
            )
        if stage in {"opt-in-alpha", "canary", "limited"} and fraction >= 1:
            raise StewardshipError(
                f"{prefix}.exposure cannot use full external traffic at stage {stage!r}"
            )
        if population != expected_population or effects != "envelope-bounded":
            raise StewardshipError(
                f"{prefix}.exposure does not match stage {stage!r}"
            )
    return fraction


def _validate_transitions(
    value: object,
    stage: str,
    expected_candidate: str,
    current_evidence_digest: str,
    record: dict,
    created_at: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise StewardshipError("release.transition_history must be non-empty")
    previous = None
    pause_origin = None
    previous_time = None
    previous_evidence_digest = None
    previous_exposure = 0.0
    pause_exposure = None
    created_value = dt.datetime.fromisoformat(
        created_at.removesuffix("Z") + "+00:00"
    )
    parsed_transitions = []
    staged_advances = {
        ("shadow", "opt-in-alpha"),
        ("opt-in-alpha", "canary"),
        ("canary", "limited"),
        ("limited", "broad"),
    }
    for index, item in enumerate(value):
        prefix = f"release.transition_history[{index}]"
        transition = _require_object(item, prefix)
        _reject_extra(
            transition,
            {
                "from",
                "to",
                "at",
                "reason",
                "reviewer",
                "candidate_digest",
                "evidence_digest",
                "evidence_window_end",
                "acceptance_reviewed_at",
                "monitoring_digest",
                "exposure",
            },
            prefix,
        )
        _require_candidate_binding(
            transition.get("candidate_digest"),
            expected_candidate,
            f"{prefix}.candidate_digest",
        )
        source = transition.get("from")
        target = transition.get("to")
        if source != previous:
            raise StewardshipError(f"{prefix}.from does not continue the recorded history")
        same_stage_review = source == target and source in DEPLOYED_STAGES
        if not same_stage_review and target not in ALLOWED_TRANSITIONS.get(source, set()):
            raise StewardshipError(f"illegal release transition: {source!r} -> {target!r}")
        exposure_fraction = _validate_transition_exposure(
            transition.get("exposure"), target, prefix
        )
        transition_at = _validate_timestamp(transition.get("at"), f"{prefix}.at")
        transition_time = dt.datetime.fromisoformat(
            transition_at.removesuffix("Z") + "+00:00"
        )
        _reject_future(transition_at, f"{prefix}.at")
        if transition_time < created_value:
            raise StewardshipError(f"{prefix}.at predates the release record")
        if previous_time is not None and transition_time <= previous_time:
            raise StewardshipError(
                "release transitions must have strictly increasing timestamps"
            )
        _require_text(transition.get("reason"), f"{prefix}.reason")
        _require_text(transition.get("reviewer"), f"{prefix}.reviewer")
        snapshot = transition.get("evidence_digest")
        evidence_window_end = transition.get("evidence_window_end")
        acceptance_reviewed_at = transition.get("acceptance_reviewed_at")
        if source is None:
            if snapshot is not None:
                raise StewardshipError(
                    f"{prefix}.evidence_digest must be null for draft creation"
                )
            if evidence_window_end is not None or acceptance_reviewed_at is not None:
                raise StewardshipError(
                    f"{prefix} draft creation cannot claim acceptance evidence"
                )
        else:
            _validate_digest(snapshot, f"{prefix}.evidence_digest")
            evidence_window_end = _validate_timestamp(
                evidence_window_end, f"{prefix}.evidence_window_end"
            )
            acceptance_reviewed_at = _validate_timestamp(
                acceptance_reviewed_at, f"{prefix}.acceptance_reviewed_at"
            )
            evidence_window_value = _timestamp_value(evidence_window_end)
            acceptance_review_value = _timestamp_value(acceptance_reviewed_at)
            if not evidence_window_value <= acceptance_review_value <= transition_time:
                raise StewardshipError(
                    f"{prefix} requires evidence_window_end <= "
                    "acceptance_reviewed_at <= transition time"
                )
        recorded_monitoring = _validate_digest(
            transition.get("monitoring_digest"), f"{prefix}.monitoring_digest"
        )
        expected_monitoring = monitoring_digest_at(record, transition_at)
        if recorded_monitoring != expected_monitoring:
            raise StewardshipError(
                f"{prefix}.monitoring_digest does not bind the monitoring snapshot "
                "visible at that transition"
            )

        if target in DEPLOYED_STAGES:
            active_severe_incidents = sorted(
                incident["id"]
                for incident in record["incidents"]
                if incident["severity"] in {"high", "critical"}
                and _timestamp_value(incident["at"]) <= transition_time
                and (
                    incident["status"] == "open"
                    or _timestamp_value(incident["resolved_at"]) > transition_time
                )
            )
            if active_severe_incidents:
                raise StewardshipError(
                    f"{prefix} enters or renews an active stage while high/critical "
                    "incidents are active: " + ", ".join(active_severe_incidents)
                )

        if target == "paused" and source != "paused":
            visible_incidents = [
                incident
                for incident in record["incidents"]
                if dt.datetime.fromisoformat(
                    incident["at"].removesuffix("Z") + "+00:00"
                )
                <= transition_time
            ]
            if not visible_incidents:
                raise StewardshipError(
                    f"{prefix} enters paused without a content-minimized incident"
                )
            pause_origin = source
            pause_exposure = previous_exposure
        elif source == "paused" and target not in {"rolled-back", "retired"}:
            conservative_resume = {
                "verified": "shadow",
                "shadow": "shadow",
                "opt-in-alpha": "opt-in-alpha",
                "canary": "canary",
                "limited": "canary",
                "broad": "canary",
            }.get(pause_origin)
            if target != conservative_resume:
                raise StewardshipError(
                    f"paused release from {pause_origin!r} must resume at "
                    f"{conservative_resume!r}, not {target!r}"
                )
            if pause_exposure is None or exposure_fraction > pause_exposure:
                raise StewardshipError(
                    f"{prefix} resumes with broader traffic exposure than before pause"
                )
            unresolved_at_resume = [
                incident["id"]
                for incident in record["incidents"]
                if incident["severity"] in {"high", "critical"}
                and dt.datetime.fromisoformat(
                    incident["at"].removesuffix("Z") + "+00:00"
                )
                <= transition_time
                and (
                    incident["status"] == "open"
                    or dt.datetime.fromisoformat(
                        incident["resolved_at"].removesuffix("Z") + "+00:00"
                    )
                    > transition_time
                )
            ]
            if unresolved_at_resume:
                raise StewardshipError(
                    f"{prefix} resumes before high/critical incidents are resolved: "
                    + ", ".join(unresolved_at_resume)
                )

        resumed_interval = source == "paused" and target in DEPLOYED_STAGES
        acceptance_interval = (
            (source, target) in staged_advances
            or same_stage_review
            or resumed_interval
        )
        if acceptance_interval:
            if snapshot == previous_evidence_digest:
                raise StewardshipError(
                    f"{prefix} accepts an interval without a fresh reviewed "
                    "evidence snapshot"
                )
            if evidence_window_value <= previous_time:
                raise StewardshipError(
                    f"{prefix} accepts an interval without a fresh mandatory evidence "
                    "window and burden window"
                )
            if (source, target) in staged_advances and (
                exposure_fraction < previous_exposure
            ):
                raise StewardshipError(
                    f"{prefix} staged exposure cannot move backward without a pause"
                )
            interval_observations = [
                observation
                for observation in record["observations"]
                if previous_time
                < dt.datetime.fromisoformat(
                    observation["at"].removesuffix("Z") + "+00:00"
                )
                <= transition_time
            ]
            pending_at_transition = sorted(
                observation["id"]
                for observation in interval_observations
                if observation["review_status"] == "pending"
                or dt.datetime.fromisoformat(
                    observation["reviewed_at"].removesuffix("Z") + "+00:00"
                )
                > transition_time
            )
            if pending_at_transition:
                raise StewardshipError(
                    f"{prefix} accepts an interval with pending monitoring "
                    "observations: "
                    + ", ".join(pending_at_transition)
                )
            reviewed_observations = [
                observation
                for observation in interval_observations
                if observation["review_status"] == "reviewed"
                and dt.datetime.fromisoformat(
                    observation["reviewed_at"].removesuffix("Z") + "+00:00"
                )
                <= transition_time
            ]
            if not reviewed_observations:
                raise StewardshipError(
                    f"{prefix} accepts an interval without a reviewed observation"
                )
            nonpassing_signals = sorted(
                {
                    observation["signal"]
                    for observation in reviewed_observations
                    if observation["result"] != "pass"
                }
            )
            if nonpassing_signals:
                raise StewardshipError(
                    f"{prefix} accepts an interval despite non-passing reviewed "
                    "monitoring signals: "
                    + ", ".join(nonpassing_signals)
                )
            required_signals = {
                rule["id"]
                for rule in record["monitoring"]["signals"]
                if rule["required_for_advance"]
            }
            latest_required: dict[str, dict] = {}
            for observation in reviewed_observations:
                if observation["signal"] in required_signals:
                    latest_required[observation["signal"]] = observation
            missing_or_nonpassing = sorted(
                signal
                for signal in required_signals
                if signal not in latest_required
                or latest_required[signal]["result"] != "pass"
            )
            if missing_or_nonpassing:
                raise StewardshipError(
                    f"{prefix} accepts an interval without passing reviewed "
                    "observations for "
                    "required signals: " + ", ".join(missing_or_nonpassing)
                )
        if source == "paused" and target in DEPLOYED_STAGES:
            if evidence_window_value <= previous_time:
                raise StewardshipError(
                    f"{prefix} resumes without post-pause evidence"
                )
        parsed_transitions.append((transition, transition_time))
        previous_time = transition_time
        previous_evidence_digest = snapshot
        previous_exposure = exposure_fraction
        previous = target
    if previous != stage:
        raise StewardshipError(
            "release.stage does not match the last transition_history destination"
        )
    if stage != "draft" and value[-1]["evidence_digest"] != current_evidence_digest:
        raise StewardshipError(
            "the current stage transition does not bind the current evidence snapshot; "
            "review and record the new evidence digest before acceptance"
        )
    if any(item[0].get("to") == "opt-in-alpha" for item in parsed_transitions):
        if not record["operating_envelope"]["opt_in"]:
            raise StewardshipError(
                "a release history containing opt-in-alpha requires opt_in=true"
            )

    for incident in record["incidents"]:
        if incident["severity"] not in {"high", "critical"}:
            continue
        opened_value = dt.datetime.fromisoformat(
            incident["at"].removesuffix("Z") + "+00:00"
        )
        resolved_value = None
        if incident["status"] == "resolved":
            resolved_value = dt.datetime.fromisoformat(
                incident["resolved_at"].removesuffix("Z") + "+00:00"
            )
        containing_pause = None
        for transition_index, (transition, transition_time) in enumerate(
            parsed_transitions
        ):
            if transition.get("to") != "paused":
                continue
            next_time = (
                parsed_transitions[transition_index + 1][1]
                if transition_index + 1 < len(parsed_transitions)
                else None
            )
            already_paused = (
                transition_time <= opened_value
                and (next_time is None or opened_value < next_time)
            )
            paused_after_detection = (
                transition_time >= opened_value
                and (resolved_value is None or transition_time <= resolved_value)
            )
            if already_paused or paused_after_detection:
                containing_pause = transition
                break
        if containing_pause is None:
            raise StewardshipError(
                f"high/critical incident {incident['id']!r} has no recorded pause"
            )
        if resolved_value is not None and stage in DEPLOYED_STAGES:
            resumes_after_resolution = [
                (transition, transition_time)
                for transition, transition_time in parsed_transitions
                if transition.get("from") == "paused"
                and transition.get("to") in DEPLOYED_STAGES
                and transition_time >= resolved_value
            ]
            if not resumes_after_resolution:
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} was not resolved "
                    "before reviewed re-entry"
                )
            resume_transition, resume_time = resumes_after_resolution[0]
            if resume_transition["evidence_digest"] == containing_pause["evidence_digest"]:
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} re-enters without "
                    "a fresh post-incident evidence snapshot"
                )
            incident_evaluation = incident["incident_evaluation_digest"]
            matching_evidence = [
                evidence
                for evidence in record["evidence"]
                if evidence["artifact_digest"] == incident_evaluation
                and evidence["incident_id"] == incident["id"]
                and evidence["kind"]
                in INCIDENT_EVIDENCE_KINDS[incident["category"]]
                and evidence["result"] == "pass"
                and evidence["independent"]
                and resolved_value
                < _timestamp_value(evidence["window_end"])
                <= resume_time
            ]
            if not matching_evidence:
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} lacks independently "
                    "passing incident-specific evidence before re-entry"
                )
            reentry_reviewed_at = incident.get("reentry_reviewed_at")
            if reentry_reviewed_at is None:
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} lacks a post-resolution "
                    "re-entry review"
                )
            reentry_reviewed_value = _timestamp_value(reentry_reviewed_at)
            if not resolved_value < reentry_reviewed_value <= resume_time:
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} has no post-resolution "
                    "re-entry review completed before re-entry"
                )
            if any(
                _timestamp_value(evidence["window_end"]) > reentry_reviewed_value
                for evidence in matching_evidence
            ):
                raise StewardshipError(
                    f"high/critical incident {incident['id']!r} re-entry review "
                    "predates its incident-specific evidence"
                )


def _validate_review(value: object, stage: str, expected_candidate: str) -> None:
    review = _require_object(value, "release.review")
    _reject_extra(
        review,
        {
            "candidate_digest",
            "technical",
            "rights",
            "privacy",
            "license",
            "affected_party_input",
            "reviewed_at",
            "expires_at",
            "maximum_evidence_age_seconds",
        },
        "release.review",
    )
    _require_candidate_binding(
        review.get("candidate_digest"), expected_candidate, "release.review.candidate_digest"
    )
    for field in ("technical", "rights", "privacy", "license"):
        status_value = review.get(field)
        if status_value not in {"pass", "fail", "pending"}:
            raise StewardshipError(f"release.review.{field} is unsupported")
        if stage in DEPLOYED_STAGES and status_value != "pass":
            raise StewardshipError(f"deployed stage requires release.review.{field}=pass")
    if review.get("affected_party_input") not in {
        "recorded",
        "not-applicable",
        "pending",
    }:
        raise StewardshipError("release.review.affected_party_input is unsupported")
    if stage in DEPLOYED_STAGES and review.get("affected_party_input") == "pending":
        raise StewardshipError("deployed stage cannot leave affected-party input pending")
    reviewed_at = _validate_timestamp(
        review.get("reviewed_at"), "release.review.reviewed_at"
    )
    expires_at = _validate_timestamp(
        review.get("expires_at"), "release.review.expires_at"
    )
    if _timestamp_value(expires_at) <= _timestamp_value(reviewed_at):
        raise StewardshipError("release.review.expires_at must follow reviewed_at")
    maximum_age = _require_nonnegative_int(
        review.get("maximum_evidence_age_seconds"),
        "release.review.maximum_evidence_age_seconds",
    )
    if maximum_age == 0:
        raise StewardshipError(
            "release.review.maximum_evidence_age_seconds must be positive"
        )


def _validate_observations(
    value: object,
    expected_candidate: str,
    created_at: str,
    declared_signals: dict[str, dict],
) -> None:
    if not isinstance(value, list):
        raise StewardshipError("release.observations must be an array")
    ids: set[str] = set()
    created_value = dt.datetime.fromisoformat(
        created_at.removesuffix("Z") + "+00:00"
    )
    previous_at = None
    for index, item in enumerate(value):
        prefix = f"release.observations[{index}]"
        observation = _require_object(item, prefix)
        _reject_extra(
            observation,
            {
                "id",
                "candidate_digest",
                "at",
                "category",
                "signal",
                "value",
                "unit",
                "review_status",
                "reviewed_at",
                "result",
                "content_digest",
            },
            prefix,
        )
        observation_id = _require_text(observation.get("id"), f"{prefix}.id")
        if observation_id in ids:
            raise StewardshipError(f"duplicate release observation id: {observation_id}")
        ids.add(observation_id)
        _require_candidate_binding(
            observation.get("candidate_digest"),
            expected_candidate,
            f"{prefix}.candidate_digest",
        )
        observed_at = _validate_timestamp(observation.get("at"), f"{prefix}.at")
        observed_value = dt.datetime.fromisoformat(
            observed_at.removesuffix("Z") + "+00:00"
        )
        if observed_value < created_value:
            raise StewardshipError(f"{prefix}.at predates the release record")
        _reject_future(observed_at, f"{prefix}.at")
        if previous_at is not None and observed_value < previous_at:
            raise StewardshipError("release observations must be time-ordered")
        previous_at = observed_value
        category = _require_text(observation.get("category"), f"{prefix}.category")
        if category not in EVENT_CATEGORIES:
            raise StewardshipError(f"{prefix}.category is unsupported")
        signal = _require_text(observation.get("signal"), f"{prefix}.signal")
        if signal not in declared_signals:
            raise StewardshipError(
                f"{prefix}.signal is not declared in release.monitoring.signals"
            )
        signal_rule = declared_signals[signal]
        if category != signal_rule["category"]:
            raise StewardshipError(
                f"{prefix}.category does not match its declared signal rule"
            )
        value_number = _require_number(observation.get("value"), f"{prefix}.value")
        unit = _require_text(observation.get("unit"), f"{prefix}.unit")
        if unit != signal_rule["unit"]:
            raise StewardshipError(
                f"{prefix}.unit does not match its declared signal rule"
            )
        review_status = observation.get("review_status")
        if review_status not in {"pending", "reviewed"}:
            raise StewardshipError(f"{prefix}.review_status is unsupported")
        reviewed_at = observation.get("reviewed_at")
        result = observation.get("result")
        if review_status == "pending":
            if reviewed_at is not None:
                raise StewardshipError(
                    f"{prefix}.reviewed_at must be null while review is pending"
                )
            if result is not None:
                raise StewardshipError(
                    f"{prefix}.result must be null while review is pending"
                )
        else:
            if result not in {"pass", "hold", "fail"}:
                raise StewardshipError(
                    f"{prefix}.result must be pass, hold, or fail after review"
                )
            reviewed_text = _validate_timestamp(reviewed_at, f"{prefix}.reviewed_at")
            reviewed_value = dt.datetime.fromisoformat(
                reviewed_text.removesuffix("Z") + "+00:00"
            )
            if reviewed_value < observed_value:
                raise StewardshipError(
                    f"{prefix}.reviewed_at predates the observation"
                )
            _reject_future(reviewed_text, f"{prefix}.reviewed_at")
            meets_threshold = (
                value_number >= signal_rule["threshold"]
                if signal_rule["direction"] == "min"
                else value_number <= signal_rule["threshold"]
            )
            if result == "pass" and not meets_threshold:
                raise StewardshipError(
                    f"{prefix} is marked pass but misses its declared signal threshold"
                )
        _validate_digest(observation.get("content_digest"), f"{prefix}.content_digest")


def _validate_incidents(
    value: object, stage: str, expected_candidate: str, created_at: str
) -> None:
    if not isinstance(value, list):
        raise StewardshipError("release.incidents must be an array")
    ids: set[str] = set()
    created_value = dt.datetime.fromisoformat(
        created_at.removesuffix("Z") + "+00:00"
    )
    previous_at = None
    for index, item in enumerate(value):
        prefix = f"release.incidents[{index}]"
        incident = _require_object(item, prefix)
        _reject_extra(
            incident,
            {
                "id",
                "candidate_digest",
                "at",
                "severity",
                "category",
                "action",
                "content_digest",
                "status",
                "resolved_at",
                "resolution_digest",
                "incident_evaluation_digest",
                "reentry_reviewed_at",
            },
            prefix,
        )
        _require_candidate_binding(
            incident.get("candidate_digest"),
            expected_candidate,
            f"{prefix}.candidate_digest",
        )
        incident_id = _require_text(incident.get("id"), f"{prefix}.id")
        if incident_id in ids:
            raise StewardshipError(f"duplicate release incident id: {incident_id}")
        ids.add(incident_id)
        incident_at = _validate_timestamp(incident.get("at"), f"{prefix}.at")
        incident_value = dt.datetime.fromisoformat(
            incident_at.removesuffix("Z") + "+00:00"
        )
        if incident_value < created_value:
            raise StewardshipError(f"{prefix}.at predates the release record")
        _reject_future(incident_at, f"{prefix}.at")
        if previous_at is not None and incident_value < previous_at:
            raise StewardshipError("release incidents must be time-ordered")
        previous_at = incident_value
        if incident.get("severity") not in {"low", "medium", "high", "critical"}:
            raise StewardshipError(f"{prefix}.severity is unsupported")
        category = _require_text(incident.get("category"), f"{prefix}.category")
        if category not in EVENT_CATEGORIES:
            raise StewardshipError(f"{prefix}.category is unsupported")
        _require_text(incident.get("action"), f"{prefix}.action")
        _validate_digest(incident.get("content_digest"), f"{prefix}.content_digest")
        incident_status = incident.get("status")
        if incident_status not in {"open", "resolved"}:
            raise StewardshipError(f"{prefix}.status must be open or resolved")
        resolution_fields = (
            "resolved_at",
            "resolution_digest",
            "incident_evaluation_digest",
            "reentry_reviewed_at",
        )
        if "reentry_reviewed_at" not in incident:
            raise StewardshipError(
                f"{prefix}.reentry_reviewed_at is required (use null before re-entry)"
            )
        if incident_status == "open":
            if any(incident.get(field) is not None for field in resolution_fields):
                raise StewardshipError(
                    f"{prefix} is open but carries resolved-only evidence"
                )
        else:
            resolved_at = _validate_timestamp(
                incident.get("resolved_at"), f"{prefix}.resolved_at"
            )
            opened_value = dt.datetime.fromisoformat(
                incident_at.removesuffix("Z") + "+00:00"
            )
            resolved_value = dt.datetime.fromisoformat(
                resolved_at.removesuffix("Z") + "+00:00"
            )
            if resolved_value < opened_value:
                raise StewardshipError(f"{prefix} resolves before it was opened")
            _reject_future(resolved_at, f"{prefix}.resolved_at")
            _validate_digest(
                incident.get("resolution_digest"), f"{prefix}.resolution_digest"
            )
            _validate_digest(
                incident.get("incident_evaluation_digest"),
                f"{prefix}.incident_evaluation_digest",
            )
            reentry_reviewed_at = incident.get("reentry_reviewed_at")
            if reentry_reviewed_at is not None:
                reentry_text = _validate_timestamp(
                    reentry_reviewed_at, f"{prefix}.reentry_reviewed_at"
                )
                if _timestamp_value(reentry_text) < resolved_value:
                    raise StewardshipError(
                        f"{prefix}.reentry_reviewed_at predates incident resolution"
                    )
                _reject_future(reentry_text, f"{prefix}.reentry_reviewed_at")
    if stage in {"paused", "rolled-back"} and not value:
        raise StewardshipError(f"{stage} releases require a content-minimized incident record")


def _validate_incident_evidence_links(record: dict) -> None:
    incidents = {incident["id"]: incident for incident in record["incidents"]}
    for index, evidence in enumerate(record["evidence"]):
        incident_id = evidence["incident_id"]
        if incident_id is None:
            continue
        incident = incidents.get(incident_id)
        if incident is None:
            raise StewardshipError(
                f"release.evidence[{index}].incident_id does not reference a recorded incident"
            )
        permitted = INCIDENT_EVIDENCE_KINDS[incident["category"]]
        if evidence["kind"] not in permitted:
            raise StewardshipError(
                f"release.evidence[{index}].kind is not an incident-evaluation lane "
                f"for category {incident['category']!r}"
            )


def _required_acceptance_floor(record: dict) -> dt.datetime:
    """Return the oldest latest independent measurement across mandatory lanes."""
    lane_times: list[dt.datetime] = []
    missing: list[str] = []
    for kind in sorted(REQUIRED_EVIDENCE):
        eligible = [
            _timestamp_value(item["window_end"])
            for item in record["evidence"]
            if item["kind"] == kind
            and item["independent"]
        ]
        if not eligible:
            missing.append(f"evidence:{kind}")
        else:
            lane_times.append(max(eligible))
    for kind in sorted(REQUIRED_BURDENS):
        eligible = [
            _timestamp_value(item["window_end"])
            for item in record["burden_register"]
            if item["kind"] == kind
        ]
        if not eligible:
            missing.append(f"burden:{kind}")
        else:
            lane_times.append(max(eligible))
    if missing:
        raise StewardshipError(
            "release cannot derive its mandatory acceptance-window floor; missing "
            + ", ".join(missing)
        )
    return min(lane_times)


def _validate_current_snapshot_timing(record: dict, stage: str) -> None:
    """Reject stale or chronologically impossible acceptance snapshots."""
    if stage == "draft":
        return
    transition_at = _validate_timestamp(
        record["transition_history"][-1]["at"],
        "release current transition timestamp",
    )
    transition_value = dt.datetime.fromisoformat(
        transition_at.removesuffix("Z") + "+00:00"
    )
    maximum_age = record["review"]["maximum_evidence_age_seconds"]
    support_times: list[dt.datetime] = []
    validation_time = _utcnow()
    for collection_name in ("evidence", "burden_register"):
        for index, item in enumerate(record[collection_name]):
            window_end = _validate_timestamp(
                item["window_end"],
                f"release.{collection_name}[{index}].window_end",
            )
            window_value = dt.datetime.fromisoformat(
                window_end.removesuffix("Z") + "+00:00"
            )
            if window_value > transition_value:
                raise StewardshipError(
                    f"release.{collection_name}[{index}] postdates the current "
                    "stage transition and cannot support that snapshot"
                )
            support_times.append(window_value)
            if stage in DEPLOYED_STAGES and (
                validation_time - window_value
            ).total_seconds() > maximum_age:
                raise StewardshipError(
                    f"release.{collection_name}[{index}] exceeds the declared "
                    "maximum evidence age at validation time"
                )
    reviewed_at = _validate_timestamp(
        record["review"]["reviewed_at"], "release.review.reviewed_at"
    )
    reviewed_value = dt.datetime.fromisoformat(
        reviewed_at.removesuffix("Z") + "+00:00"
    )
    if reviewed_value > transition_value:
        raise StewardshipError(
            "release.review postdates the current stage transition"
        )
    expires_at = _validate_timestamp(
        record["review"]["expires_at"], "release.review.expires_at"
    )
    expires_value = _timestamp_value(expires_at)
    if expires_value <= transition_value:
        raise StewardshipError(
            "release acceptance expires no later than the current stage transition"
        )
    if stage in DEPLOYED_STAGES and expires_value <= _utcnow():
        raise StewardshipError("release acceptance has expired")
    contamination_reviewed = _validate_timestamp(
        record["contamination_audit"]["reviewed_at"],
        "release.contamination_audit.reviewed_at",
    )
    contamination_value = _timestamp_value(contamination_reviewed)
    if contamination_value > transition_value:
        raise StewardshipError(
            "release.contamination_audit postdates the current stage transition"
        )
    support_times.append(contamination_value)
    rollback = record.get("rollback")
    if rollback is not None and rollback.get("tested"):
        tested_at = _validate_timestamp(
            rollback["tested_at"], "release.rollback.tested_at"
        )
        tested_value = dt.datetime.fromisoformat(
            tested_at.removesuffix("Z") + "+00:00"
        )
        if tested_value > transition_value:
            raise StewardshipError(
                "release.rollback test postdates the current stage transition"
            )
        support_times.append(tested_value)
    if support_times and reviewed_value < max(support_times):
        raise StewardshipError(
            "release.review predates evidence, burden, contamination, or rollback "
            "inputs in the accepted snapshot"
        )
    current_transition = record["transition_history"][-1]
    recorded_window = _timestamp_value(current_transition["evidence_window_end"])
    required_floor = _required_acceptance_floor(record)
    if recorded_window != required_floor:
        raise StewardshipError(
            "release current transition evidence_window_end does not match the "
            "oldest latest independent mandatory evidence/burden measurement window"
        )
    recorded_review = _timestamp_value(
        current_transition["acceptance_reviewed_at"]
    )
    if recorded_review != reviewed_value:
        raise StewardshipError(
            "release current transition acceptance_reviewed_at does not match "
            "the current release review"
        )


def validate_release(record: object, catalog: dict) -> dict:
    """Validate a bounded evidence record for one exact deployed-system tuple."""
    release = _require_object(record, "release record")
    _reject_extra(
        release,
        {
            "schema",
            "release_id",
            "created_at",
            "stage",
            "boundary",
            "candidate_digest",
            "deployed_tuple",
            "lineage",
            "operating_envelope",
            "resource_ledger",
            "evidence",
            "burden_register",
            "contamination_audit",
            "monitoring",
            "incident_response",
            "appeal",
            "rollback",
            "transition_history",
            "review",
            "observations",
            "incidents",
            "limitations",
        },
        "release record",
    )
    if release.get("schema") != RELEASE_SCHEMA:
        raise StewardshipError(f"unsupported release schema: {release.get('schema')!r}")
    release_id = _require_text(release.get("release_id"), "release.release_id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", release_id):
        raise StewardshipError("release.release_id contains unsupported characters")
    created_at = _validate_timestamp(release.get("created_at"), "release.created_at")
    _reject_future(created_at, "release.created_at")
    stage = release.get("stage")
    if stage not in RELEASE_STAGES:
        raise StewardshipError("release.stage is unsupported")
    if release.get("boundary") != RELEASE_BOUNDARY:
        raise StewardshipError("release.boundary must retain the non-authorization boundary")

    _validate_deployed_tuple(release.get("deployed_tuple"))
    _validate_lineage(release.get("lineage"))
    _validate_operating_envelope(release.get("operating_envelope"))
    resource_lanes = _validate_resource_ledger(release.get("resource_ledger"), catalog)
    expected_digest = candidate_digest(release)
    expected_resource_partition = resource_partition_digest(release)
    current_evidence_digest = evidence_digest(release)
    passed_kinds = _validate_evidence(
        release.get("evidence"), resource_lanes, expected_digest
    )
    burden_kinds = _validate_burdens(
        release.get("burden_register"), expected_digest, resource_lanes
    )
    _validate_contamination_audit(
        release.get("contamination_audit"),
        expected_digest,
        expected_resource_partition,
        stage,
    )
    declared_signals = _validate_monitoring(release.get("monitoring"))
    _validate_incident_and_appeal(release, stage)
    _validate_rollback(
        release.get("rollback"), stage, expected_digest, release_id
    )
    _validate_review(release.get("review"), stage, expected_digest)
    _validate_observations(
        release.get("observations"),
        expected_digest,
        created_at,
        declared_signals,
    )
    _validate_incidents(
        release.get("incidents"), stage, expected_digest, created_at
    )
    _validate_incident_evidence_links(release)
    _validate_transitions(
        release.get("transition_history"),
        stage,
        expected_digest,
        current_evidence_digest,
        release,
        created_at,
    )
    _validate_current_snapshot_timing(release, stage)
    _require_text_list(release.get("limitations"), "release.limitations")

    recorded_digest = _validate_digest(
        release.get("candidate_digest"), "release.candidate_digest"
    )
    if recorded_digest != expected_digest:
        raise StewardshipError(
            "release.candidate_digest does not match the deployed tuple and envelope; "
            "treat this as a new candidate"
        )
    if stage in DEPLOYED_STAGES:
        failed_evidence = sorted(
            item["id"]
            for item in release["evidence"]
            if item["result"] == "fail" and item["kind"] not in REQUIRED_EVIDENCE
        )
        if failed_evidence:
            raise StewardshipError(
                "deployed stage contains unresolved failed evidence: "
                + ", ".join(failed_evidence)
            )
        unresolved_required = sorted(
            item["id"]
            for item in release["evidence"]
            if item["kind"] in REQUIRED_EVIDENCE and item["result"] != "pass"
        )
        if unresolved_required:
            raise StewardshipError(
                "deployed stage contains unresolved required evidence: "
                + ", ".join(unresolved_required)
            )
        missing = sorted(REQUIRED_EVIDENCE - passed_kinds)
        if missing:
            raise StewardshipError(
                "deployed stage lacks passing evidence lanes: " + ", ".join(missing)
            )
        missing_burdens = sorted(REQUIRED_BURDENS - burden_kinds)
        if missing_burdens:
            raise StewardshipError(
                "deployed stage lacks burden measurements: " + ", ".join(missing_burdens)
            )
        unacceptable_burdens = sorted(
            item["kind"]
            for item in release["burden_register"]
            if item["status"] != "within"
        )
        if unacceptable_burdens:
            raise StewardshipError(
                "deployed stage has unresolved or excessive burdens: "
                + ", ".join(unacceptable_burdens)
            )
        if any(
            incident.get("severity") in {"high", "critical"}
            and incident.get("status") == "open"
            for incident in release["incidents"]
            if isinstance(incident, dict)
        ):
            raise StewardshipError(
                "an unresolved high or critical incident requires the candidate "
                "to remain paused, rolled back, or retired"
            )
        if stage == "opt-in-alpha" and not release["operating_envelope"]["opt_in"]:
            raise StewardshipError("opt-in-alpha requires operating_envelope.opt_in=true")
    return release


def load_release(path: Path, catalog: dict) -> dict:
    return validate_release(_read_json(path, "release record"), catalog)


def release_status(record: dict) -> dict:
    evidence_results = {"pass": 0, "fail": 0, "hold": 0}
    for item in record["evidence"]:
        evidence_results[item["result"]] += 1
    return {
        "release_id": record["release_id"],
        "stage": record["stage"],
        "candidate_digest": record["candidate_digest"],
        "resource_partition_digest": resource_partition_digest(record),
        "evidence_digest": evidence_digest(record),
        "monitoring_digest": monitoring_digest(record),
        "evidence_results": evidence_results,
        "burdens": {
            item["kind"]: item["status"] for item in record["burden_register"]
        },
        "contamination_audit": record["contamination_audit"]["status"],
        "observations": len(record["observations"]),
        "incidents": len(record["incidents"]),
        "open_incidents": sum(
            1 for incident in record["incidents"] if incident["status"] == "open"
        ),
        "rollback_tested": bool(record.get("rollback") and record["rollback"]["tested"]),
        "authorization_granted": False,
        "boundary": RELEASE_BOUNDARY,
    }


def _print_list(catalog: dict, stage: str | None) -> None:
    rows = [
        resource
        for resource in catalog["resources"]
        if stage is None or stage in resource["stages"]
    ]
    if not rows:
        raise StewardshipError(f"no catalog resources cover stage: {stage}")
    print(f"{'ID':32} {'TYPE':8} {'GATED':6} ROLES")
    for resource in rows:
        print(
            f"{resource['id'][:32]:32} {resource['repo_type']:8} "
            f"{str(resource['gated']).lower():6} {','.join(resource['roles'])}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pinned evidence catalog for model lifecycle stewardship"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    groups = parser.add_subparsers(dest="group", required=True)
    catalog = groups.add_parser("catalog", help="inspect Hugging Face resources")
    commands = catalog.add_subparsers(dest="command", required=True)

    commands.add_parser("check", help="validate the local catalog offline")
    listing = commands.add_parser("list", help="list catalog entries")
    listing.add_argument("--stage")
    show = commands.add_parser("show", help="show one catalog entry")
    show.add_argument("resource_id")
    plan = commands.add_parser("plan", help="show a non-executing retrieval plan")
    plan.add_argument("resource_id")
    verify = commands.add_parser("verify", help="verify pinned metadata on the Hub")
    verify.add_argument("resource_id", nargs="?")
    card = commands.add_parser("fetch-card", help="fetch only the pinned README.md")
    card.add_argument("resource_id")
    card.add_argument("--cache-dir", type=Path)

    release = groups.add_parser(
        "release", help="validate one exact model-system release record"
    )
    release_commands = release.add_subparsers(dest="command", required=True)
    release_check = release_commands.add_parser(
        "check", help="validate a release record without deploying it"
    )
    release_check.add_argument("record", type=Path)
    release_status_parser = release_commands.add_parser(
        "status", help="show a validated, non-authorizing release summary"
    )
    release_status_parser.add_argument("record", type=Path)
    release_digest_parser = release_commands.add_parser(
        "digest", help="compute the candidate digest while authoring a record"
    )
    release_digest_parser.add_argument("record", type=Path)
    resource_digest_parser = release_commands.add_parser(
        "resource-digest", help="compute the complete resource-partition digest"
    )
    resource_digest_parser.add_argument("record", type=Path)
    evidence_digest_parser = release_commands.add_parser(
        "evidence-digest", help="compute the reviewed acceptance snapshot digest"
    )
    evidence_digest_parser.add_argument("record", type=Path)
    monitoring_digest_parser = release_commands.add_parser(
        "monitoring-digest", help="compute the appendable monitoring snapshot digest"
    )
    monitoring_digest_parser.add_argument("record", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        if args.group == "catalog":
            if args.command == "check":
                print(f"catalog valid: {len(catalog['resources'])} pinned resources")
            elif args.command == "list":
                _print_list(catalog, args.stage)
            elif args.command == "show":
                print(json.dumps(select_resource(catalog, args.resource_id), indent=2))
            elif args.command == "plan":
                print(
                    json.dumps(
                        resource_plan(select_resource(catalog, args.resource_id)), indent=2
                    )
                )
            elif args.command == "verify":
                if args.resource_id:
                    resources = [select_resource(catalog, args.resource_id)]
                else:
                    resources = catalog["resources"]
                for resource in resources:
                    print(json.dumps(verify_resource(resource), sort_keys=True))
            elif args.command == "fetch-card":
                resource = select_resource(catalog, args.resource_id)
                print(fetch_card(resource, args.cache_dir))
            else:  # pragma: no cover - argparse constrains this branch
                raise StewardshipError(f"unsupported catalog command: {args.command}")
        elif args.group == "release":
            if args.command == "digest":
                print(candidate_digest(_read_json(args.record, "release record")))
            elif args.command == "resource-digest":
                print(resource_partition_digest(_read_json(args.record, "release record")))
            elif args.command == "evidence-digest":
                print(evidence_digest(_read_json(args.record, "release record")))
            elif args.command == "monitoring-digest":
                print(monitoring_digest(_read_json(args.record, "release record")))
            elif args.command == "check":
                record = load_release(args.record, catalog)
                print(
                    f"release record valid: {record['release_id']} ({record['stage']}); "
                    "no deployment authority granted"
                )
            elif args.command == "status":
                print(json.dumps(release_status(load_release(args.record, catalog)), indent=2))
            else:  # pragma: no cover - argparse constrains this branch
                raise StewardshipError(f"unsupported release command: {args.command}")
        else:  # pragma: no cover - argparse constrains this branch
            raise StewardshipError(f"unsupported command group: {args.group}")
    except (StewardshipError, OSError) as exc:
        print(f"model-stewardship: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
