#!/usr/bin/env python3
"""Fail-closed MLX LoRA training for reviewed KINGDOM dataset artifacts.

Training accepts one immutable local-training manifest. The manifest must opt
in to local training, record an approved review, and bind pre-existing train
and validation JSONL files by size, record count, and SHA-256. This module
never creates the authoritative split at runtime.

The old random splitter remains available only as an explicitly acknowledged
``legacy-dev-split`` command. Its outputs are marked non-authoritative and
cannot be passed to ``run`` without a separately reviewed training manifest.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
MODEL_LOCK_TOOL = LOVE_ROOT / "tools" / "hf_model_lock.py"
LORA_CONFIG = MLX_DIR / "training" / "lora-config.json"
REQUIREMENTS_LOCK = MLX_DIR / "requirements.lock"
ADAPTERS_DIR = MLX_DIR / "adapters"
RUNS_DIR = MLX_DIR / "training" / "runs"
VENV_PYTHON = MLX_DIR / ".venv" / "bin" / "python3"

TRAINING_MANIFEST_SCHEMA = "kingdom.local-training-manifest/v1"
RUN_PLAN_SCHEMA = "kingdom.local-training-run-plan/v1"
RUN_RECEIPT_SCHEMA = "kingdom.local-training-run-receipt/v1"
RUN_RESULT_SCHEMA = "kingdom.local-training-run-result/v1"
ADAPTER_RECEIPT_SCHEMA = "kingdom.mlx-adapter-training-receipt/v1"
LOCAL_TRAINING_PURPOSE = "local-mlx-training"
REVIEWED_ARTIFACT_CLASS = "reviewed-outbound-dataset"
LEGACY_DEV_ACK = "I_UNDERSTAND_THIS_SPLIT_IS_NOT_AUTHORIZED_FOR_TRAINING"
LEGACY_DEV_MARKER = "NON_AUTHORITATIVE_LEGACY_DEV_SPLIT.json"
ADAPTER_RECEIPT_NAME = "training-receipt.json"
ADAPTER_WEIGHTS_NAME = "adapters.safetensors"
SERVING_APPROVAL_NAME = "serving-approval.json"
SERVING_APPROVAL_SCHEMA = "kingdom.mlx-loopback-serving-approval/v1"
SERVING_ACK = "I_APPROVE_THIS_ADAPTER_FOR_LOOPBACK_ONLY_LOCAL_SERVING"
EVALUATIONS_DIR_NAME = "evaluations"
EVALUATION_RECEIPT_SCHEMA = "kingdom.mlx-evaluation-receipt/v1"
LORA_CONFIG_SCHEMA = "kingdom.mlx-lora-config/v1"
REQUIRED_PYTHON_VERSION = "3.12.12"
REQUIRED_MLX_VERSION = "0.32.2"
REQUIRED_MLX_LM_VERSION = "0.31.3"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@-]{1,192}$")
EVALUATION_ID_RE = re.compile(r"^mlx-eval-[0-9a-f]{24}$")
FLOAT_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
EVALUATION_METRICS_RE = re.compile(
    rf"Test loss\s+({FLOAT_TOKEN}),\s+Test ppl\s+({FLOAT_TOKEN})\."
)
REQUIRED_SPLITS = {"train": "training", "validation": "validation"}
OPTIONAL_SPLITS = {"test": "evaluation"}
BLOCKED_OPERATIONAL_PREFIXES = (
    PurePosixPath("coordination"),
    PurePosixPath("memory"),
    PurePosixPath("hive/history"),
    PurePosixPath("logs"),
)
LORA_CONFIG_KEYS = {
    "schema",
    "fine_tune_type",
    "num_layers",
    "lora_parameters",
    "learning_rate",
    "batch_size",
    "epochs",
    "seed",
    "mask_prompt",
    "max_seq_length",
}
LORA_PARAMETER_KEYS = {"rank", "scale", "dropout", "keys"}
TRAINING_MANIFEST_KEYS = {
    "schema",
    "manifest_id",
    "dataset_id",
    "version",
    "artifact_class",
    "source_dataset_manifest",
    "local_training",
    "splits",
    "limitations",
}
LOCAL_TRAINING_KEYS = {"permitted", "purpose", "review"}
LOCAL_REVIEW_KEYS = {"state", "reviewed_by", "reviewed_at", "review_ref"}
SPLIT_ENTRY_KEYS = {"path", "format", "lane", "sha256", "bytes", "records"}
RUN_PLAN_KEYS = {
    "schema",
    "dataset",
    "model",
    "runtime",
    "training_config",
    "launcher",
    "adapter",
    "plan_id",
    "plan_sha256",
}
PORTABLE_DATASET_KEYS = {
    "manifest_id",
    "dataset_id",
    "version",
    "local_manifest_sha256",
    "source_manifest_id",
    "source_manifest_sha256",
    "splits",
}
PORTABLE_MODEL_KEYS = {"repo_id", "revision", "lock_sha256"}
RUNTIME_KEYS = {
    "python",
    "packages",
    "module_origins",
    "requirements_lock_sha256",
}
MODULE_ORIGIN_KEYS = {"mlx", "mlx_lm"}
LAUNCHER_KEYS = {
    "module",
    "mode",
    "effective_config",
    "effective_config_sha256",
    "iterations",
    "path_arguments",
}
ADAPTER_RECEIPT_KEYS = {
    "schema",
    "state",
    "adapter",
    "run_plan",
    "weights",
    "artifact_files",
    "approvals",
    "boundary",
}
WEIGHTS_BINDING_KEYS = {"path", "sha256", "bytes"}
FILE_BINDING_KEYS = {"sha256", "bytes"}
APPROVALS_KEYS = {"serving", "release"}
APPROVAL_STATE_KEYS = {"state", "review_ref"}
SERVING_APPROVAL_KEYS = {
    "schema",
    "state",
    "scope",
    "adapter",
    "training_receipt_sha256",
    "declared_by",
    "declared_at",
    "decision_ref",
    "boundary",
}
EVALUATION_RECEIPT_KEYS = {
    "schema",
    "state",
    "evaluation_id",
    "recorded_at",
    "adapter",
    "adapter_receipt_sha256",
    "model",
    "dataset",
    "split",
    "runtime",
    "settings",
    "metrics",
    "boundary",
}
EVALUATION_SPLIT_KEYS = {"name", *SPLIT_ENTRY_KEYS}
EVALUATION_SETTINGS_KEYS = {
    "backend",
    "mode",
    "batch_size",
    "max_seq_length",
    "test_batches",
    "seed",
}
EVALUATION_METRICS_KEYS = {"test_loss", "test_perplexity"}
ADAPTER_RECEIPT_BOUNDARY = (
    "This receipt binds local artifacts and declared evidence. It neither "
    "authenticates reviewer authority nor approves serving or release."
)
SERVING_APPROVAL_BOUNDARY = (
    "This is a local OS-user declaration for the hard-coded loopback service. "
    "It does not authenticate identity, approve network exposure, or approve release."
)
EVALUATION_RECEIPT_BOUNDARY = (
    "This receipt records one local loss/perplexity evaluation with exact inputs and "
    "settings. It does not approve serving or release and is not a task-accuracy result."
)
MLX_ADAPTER_CONFIG_KEYS = {
    "model",
    "train",
    "fine_tune_type",
    "optimizer",
    "optimizer_config",
    "data",
    "seed",
    "num_layers",
    "batch_size",
    "iters",
    "val_batches",
    "learning_rate",
    "steps_per_report",
    "steps_per_eval",
    "resume_adapter_file",
    "adapter_path",
    "save_every",
    "test",
    "test_batches",
    "max_seq_length",
    "config",
    "grad_checkpoint",
    "grad_accumulation_steps",
    "clear_cache_threshold",
    "lr_schedule",
    "lora_parameters",
    "mask_prompt",
    "report_to",
    "project_name",
}
PINNED_MLX_LM_DEFAULTS = {
    "optimizer": "adam",
    "optimizer_config": {
        "adam": {},
        "adamw": {},
        "muon": {},
        "sgd": {},
        "adafactor": {},
    },
    "val_batches": 25,
    "steps_per_report": 10,
    "steps_per_eval": 200,
    "resume_adapter_file": None,
    "save_every": 100,
    "test": False,
    "test_batches": 500,
    "grad_checkpoint": False,
    "grad_accumulation_steps": 1,
    "clear_cache_threshold": 0,
    "lr_schedule": None,
    "report_to": None,
    "project_name": None,
}


class TrainingStewardshipError(RuntimeError):
    """Raised when training provenance or authorization is not established."""


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(key)
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number {value}")


def _decode_json_bytes(data, label):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TrainingStewardshipError(f"{label} is not UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as exc:
        raise TrainingStewardshipError(
            f"{label} contains duplicate key {exc.args[0]!r}"
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise TrainingStewardshipError(f"cannot parse {label}: {exc}") from exc


def _canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value):
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_file_sha256(value):
    return "sha256:" + hashlib.sha256(_canonical_bytes(value) + b"\n").hexdigest()


def file_sha256(path):
    """Return a prefixed SHA-256 for a file without loading it all into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_canonical_json(path, value):
    Path(path).write_bytes(_canonical_bytes(value) + b"\n")


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonempty_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise TrainingStewardshipError(f"{field} must be a non-empty string")
    return value


def _exact_keys(value, expected, field):
    if not isinstance(value, dict):
        raise TrainingStewardshipError(f"{field} must be an object")
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise TrainingStewardshipError(
            f"{field} keys must match exactly ({'; '.join(details)})"
        )
    return value


def _datetime_text(value, field):
    value = _nonempty_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrainingStewardshipError(
            f"{field} must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrainingStewardshipError(f"{field} must include a timezone")
    return value


def _safe_name(value, field):
    value = _nonempty_text(value, field)
    if not SAFE_NAME_RE.fullmatch(value):
        raise TrainingStewardshipError(f"{field} contains unsafe characters")
    return value


def validate_adapter_name(value):
    """Validate an adapter identifier before any model/runtime work."""
    return _safe_name(value, "adapter")


def _manifest_id(value):
    value = _nonempty_text(value, "manifest_id")
    if not MANIFEST_ID_RE.fullmatch(value):
        raise TrainingStewardshipError("manifest_id contains unsafe characters")
    return value


def _normalized_sha256(value, field):
    if isinstance(value, str) and SHA256_RE.fullmatch(value):
        return value
    if isinstance(value, str) and BARE_SHA256_RE.fullmatch(value):
        return f"sha256:{value}"
    raise TrainingStewardshipError(f"{field} must be a SHA-256 digest")


def _prefixed_sha256(value, field):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise TrainingStewardshipError(
            f"{field} must be a prefixed lowercase SHA-256 digest"
        )
    return value


def _load_json_object(path, label):
    path = Path(path)
    try:
        value = _decode_json_bytes(path.read_bytes(), label)
    except FileNotFoundError as exc:
        raise TrainingStewardshipError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise TrainingStewardshipError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrainingStewardshipError(f"{label} must be a JSON object")
    return value


def load_config():
    try:
        value = _decode_json_bytes(CONFIG_FILE.read_bytes(), "MLX config")
        if not isinstance(value, dict):
            raise TrainingStewardshipError("MLX config must be a JSON object")
        return value
    except FileNotFoundError:
        return {
            "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "base_model_lock": None,
        }
    except OSError as exc:
        raise ValueError(f"Invalid MLX config {CONFIG_FILE}: {exc}") from exc


def resolve_training_model():
    """Resolve and verify the shared model lock inside the MLX environment."""
    if not VENV_PYTHON.exists():
        raise RuntimeError(
            f"Venv not found at {VENV_PYTHON}. Run: uv venv --python "
            f"{REQUIRED_PYTHON_VERSION} {MLX_DIR / '.venv'} && uv pip install "
            f"--python {MLX_DIR / '.venv' / 'bin' / 'python'} -r "
            f"{REQUIREMENTS_LOCK}"
        )
    result = subprocess.run(
        [
            str(VENV_PYTHON),
            "-I",
            str(MODEL_LOCK_TOOL),
            "resolve",
            "--config",
            str(CONFIG_FILE),
            "--project-root",
            str(LOVE_ROOT),
            "--cache-dir",
            str(MLX_DIR / "cache"),
            "--json",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("locked model resolution failed")
    try:
        resolution = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("locked model resolver returned invalid JSON") from exc
    required = ("path", "repo_id", "revision", "lock_sha256")
    if any(not isinstance(resolution.get(item), str) for item in required):
        raise RuntimeError("locked model resolver returned incomplete provenance")
    if not (
        SHA256_RE.fullmatch(resolution["lock_sha256"])
        or BARE_SHA256_RE.fullmatch(resolution["lock_sha256"])
    ):
        raise RuntimeError("locked model resolver returned an invalid lock digest")
    return resolution


def _validate_runtime_environment(environment):
    environment = _exact_keys(environment, RUNTIME_KEYS, "runtime environment")
    if environment.get("python") != REQUIRED_PYTHON_VERSION:
        raise TrainingStewardshipError(
            "MLX runtime Python must be exactly " + REQUIRED_PYTHON_VERSION
        )
    packages = environment.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise TrainingStewardshipError("runtime environment packages must be an object")
    if any(
        not isinstance(name, str)
        or not name
        or not isinstance(version, str)
        or not version
        for name, version in packages.items()
    ):
        raise TrainingStewardshipError(
            "runtime environment packages must map names to versions"
        )
    if packages.get("mlx") != REQUIRED_MLX_VERSION:
        raise TrainingStewardshipError(
            f"MLX runtime must contain mlx=={REQUIRED_MLX_VERSION}"
        )
    if packages.get("mlx-lm") != REQUIRED_MLX_LM_VERSION:
        raise TrainingStewardshipError(
            f"MLX runtime must contain mlx-lm=={REQUIRED_MLX_LM_VERSION}"
        )
    origins = _exact_keys(
        environment.get("module_origins"),
        MODULE_ORIGIN_KEYS,
        "runtime environment module_origins",
    )
    for module_name, origin in origins.items():
        origin = _nonempty_text(origin, f"runtime module origin {module_name}")
        if not Path(origin).is_absolute():
            raise TrainingStewardshipError(
                f"runtime module origin {module_name} must be an absolute path"
            )
    _prefixed_sha256(
        environment.get("requirements_lock_sha256"),
        "runtime requirements_lock_sha256",
    )
    return environment


def _load_version_pinned_requirements_lock(path):
    try:
        lock_text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise TrainingStewardshipError(
            f"cannot read training requirements lock {path}: {exc}"
        ) from exc
    locked = {}
    for line_number, line in enumerate(lock_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;@]+)", stripped)
        if not match:
            raise TrainingStewardshipError(
                "training requirements lock must contain only exact version pins; "
                f"invalid line {line_number}"
            )
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in locked:
            raise TrainingStewardshipError(
                f"training requirements lock repeats package {name!r}"
            )
        locked[name] = match.group(2)
    if not locked:
        raise TrainingStewardshipError("training requirements lock has no package pins")
    return locked


def resolve_training_environment(python_executable=None):
    """Bind the exact interpreter and pinned MLX trainer interface."""
    interpreter = Path(python_executable) if python_executable else VENV_PYTHON
    if not interpreter.exists() or not interpreter.is_file():
        raise TrainingStewardshipError(
            f"MLX environment interpreter not found: {interpreter}; create it from "
            f"the version-pinned {REQUIREMENTS_LOCK}"
        )
    locked = _load_version_pinned_requirements_lock(REQUIREMENTS_LOCK)
    if (
        locked.get("mlx") != REQUIRED_MLX_VERSION
        or locked.get("mlx-lm") != REQUIRED_MLX_LM_VERSION
    ):
        raise TrainingStewardshipError(
            "training requirements lock has unexpected MLX pins"
        )
    probe = """
import importlib.util
import json
import platform
import re
from importlib.metadata import distribution, distributions
from pathlib import Path

packages = {}
duplicates = []
for item in distributions():
    declared_name = item.metadata.get("Name")
    if not declared_name:
        continue
    name = re.sub(r"[-_.]+", "-", declared_name).lower()
    if name in packages:
        duplicates.append(name)
    else:
        packages[name] = item.version

module_origins = {}
origin_errors = []
for distribution_name, provenance_name, module_name in (
    ("mlx", "mlx", "mlx.core"),
    ("mlx-lm", "mlx_lm", "mlx_lm"),
):
    installed = distribution(distribution_name)
    spec = importlib.util.find_spec(module_name)
    origin = None if spec is None else spec.origin
    owned_files = {
        str(Path(installed.locate_file(item)).resolve())
        for item in (installed.files or ())
    }
    if origin is None or str(Path(origin).resolve()) not in owned_files:
        origin_errors.append(provenance_name)
    else:
        module_origins[provenance_name] = str(Path(origin).resolve())

print(json.dumps({
    "python": platform.python_version(),
    "packages": packages,
    "duplicates": sorted(set(duplicates)),
    "module_origins": module_origins,
    "origin_errors": sorted(origin_errors),
}, sort_keys=True))
"""
    result = subprocess.run(
        [str(interpreter), "-I", "-c", probe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise TrainingStewardshipError("cannot inspect the MLX training environment")
    environment = _decode_json_bytes(
        result.stdout.encode("utf-8"), "MLX training environment probe"
    )
    if not isinstance(environment, dict) or set(environment) != {
        "python",
        "packages",
        "duplicates",
        "module_origins",
        "origin_errors",
    }:
        raise TrainingStewardshipError("MLX training environment probe is incomplete")
    if environment["duplicates"]:
        raise TrainingStewardshipError(
            "MLX environment contains duplicate normalized package metadata"
        )
    if environment["origin_errors"]:
        raise TrainingStewardshipError(
            "MLX modules do not resolve to files owned by their pinned distributions"
        )
    if environment["packages"] != locked:
        raise TrainingStewardshipError(
            "installed MLX environment does not match mlx/requirements.lock"
        )
    return _validate_runtime_environment(
        {
            "python": environment["python"],
            "packages": environment["packages"],
            "module_origins": environment["module_origins"],
            "requirements_lock_sha256": file_sha256(REQUIREMENTS_LOCK),
        }
    )


def _validate_lora_config(config, field="LoRA config"):
    config = _exact_keys(config, LORA_CONFIG_KEYS, field)
    if config.get("schema") != LORA_CONFIG_SCHEMA:
        raise TrainingStewardshipError(
            f"LoRA config schema must be {LORA_CONFIG_SCHEMA}"
        )
    if config.get("fine_tune_type") != "lora":
        raise TrainingStewardshipError("fine_tune_type must be 'lora'")
    for field in ("num_layers", "batch_size", "epochs", "seed", "max_seq_length"):
        value = config.get(field)
        lower_bound = 0 if field == "seed" else 1
        if not isinstance(value, int) or isinstance(value, bool) or value < lower_bound:
            qualifier = "non-negative" if field == "seed" else "positive"
            raise TrainingStewardshipError(
                f"LoRA config {field} must be a {qualifier} integer"
            )
    rate = config.get("learning_rate")
    if (
        not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or not math.isfinite(rate)
        or rate <= 0
    ):
        raise TrainingStewardshipError(
            "LoRA config learning_rate must be a finite positive number"
        )
    if config.get("mask_prompt") is not True:
        raise TrainingStewardshipError("LoRA config mask_prompt must be true")
    parameters = config.get("lora_parameters")
    if not isinstance(parameters, dict) or set(parameters) != LORA_PARAMETER_KEYS:
        raise TrainingStewardshipError(
            "lora_parameters must contain exactly rank, scale, dropout, and keys"
        )
    rank = parameters.get("rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0:
        raise TrainingStewardshipError("lora_parameters.rank must be positive")
    for field in ("scale", "dropout"):
        value = parameters.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise TrainingStewardshipError(
                f"lora_parameters.{field} must be a finite number"
            )
    if parameters["scale"] <= 0:
        raise TrainingStewardshipError("lora_parameters.scale must be positive")
    if not 0 <= parameters["dropout"] < 1:
        raise TrainingStewardshipError(
            "lora_parameters.dropout must be in the range [0, 1)"
        )
    keys = parameters.get("keys")
    if (
        not isinstance(keys, list)
        or not keys
        or any(not isinstance(item, str) or not item.strip() for item in keys)
        or keys != sorted(set(keys))
    ):
        raise TrainingStewardshipError(
            "lora_parameters.keys must be a sorted unique non-empty string array"
        )
    return config


def load_lora_config():
    try:
        config = _decode_json_bytes(LORA_CONFIG.read_bytes(), "LoRA config")
    except FileNotFoundError:
        raise TrainingStewardshipError(f"LoRA config not found: {LORA_CONFIG}")
    except OSError as exc:
        raise TrainingStewardshipError(
            f"invalid LoRA config {LORA_CONFIG}: {exc}"
        ) from exc
    return _validate_lora_config(config)


def effective_lora_config(config):
    """Return only keys understood by the pinned mlx_lm.lora config loader."""
    return {
        "batch_size": config["batch_size"],
        "fine_tune_type": config["fine_tune_type"],
        "learning_rate": config["learning_rate"],
        "lora_parameters": config["lora_parameters"],
        "mask_prompt": config["mask_prompt"],
        "max_seq_length": config["max_seq_length"],
        "num_layers": config["num_layers"],
        "seed": config["seed"],
    }


def validate_dataset(path):
    """Validate one chat JSONL file without importing MLX."""
    path = Path(path)
    if not path.exists():
        return {"valid": False, "count": 0, "error": f"File not found: {path}"}

    count = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = _decode_json_bytes(
                        line.encode("utf-8"), f"dataset line {line_number}"
                    )
                except TrainingStewardshipError as exc:
                    return {
                        "valid": False,
                        "count": count,
                        "error": f"Line {line_number}: {exc}",
                    }
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("messages"), list
                ):
                    return {
                        "valid": False,
                        "count": count,
                        "error": f"Line {line_number}: missing list 'messages' key",
                    }
                if not entry["messages"]:
                    return {
                        "valid": False,
                        "count": count,
                        "error": f"Line {line_number}: messages must not be empty",
                    }
                for message in entry["messages"]:
                    if (
                        not isinstance(message, dict)
                        or message.get("role") not in {"system", "user", "assistant"}
                        or not isinstance(message.get("content"), str)
                    ):
                        return {
                            "valid": False,
                            "count": count,
                            "error": f"Line {line_number}: invalid chat message",
                        }
                count += 1
    except (OSError, UnicodeError) as exc:
        return {"valid": False, "count": count, "error": str(exc)}

    if count == 0:
        return {"valid": False, "count": 0, "error": "Empty dataset"}
    return {"valid": True, "count": count, "error": None}


def _resolve_bound_path(raw_path, project_root):
    if not isinstance(raw_path, str):
        raise TrainingStewardshipError("split path must be a string")
    logical = PurePosixPath(raw_path)
    if (
        not raw_path
        or logical.is_absolute()
        or "." in logical.parts
        or ".." in logical.parts
    ):
        raise TrainingStewardshipError(f"unsafe split path: {raw_path!r}")
    if any(
        logical == prefix or prefix in logical.parents
        for prefix in BLOCKED_OPERATIONAL_PREFIXES
    ):
        raise TrainingStewardshipError(
            f"operational-history paths cannot be training splits: {raw_path}"
        )
    root = Path(project_root).resolve()
    candidate = root / Path(*logical.parts)
    cursor = root
    for part in logical.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise TrainingStewardshipError(
                f"bound paths cannot contain symlinks: {raw_path}"
            )
    resolved = candidate.resolve()
    try:
        resolved_relative = PurePosixPath(resolved.relative_to(root).as_posix())
    except ValueError as exc:
        raise TrainingStewardshipError(
            f"split escapes project root: {raw_path}"
        ) from exc
    if any(
        resolved_relative == prefix or prefix in resolved_relative.parents
        for prefix in BLOCKED_OPERATIONAL_PREFIXES
    ):
        raise TrainingStewardshipError(
            f"operational-history targets cannot be training splits: {raw_path}"
        )
    return resolved


def _load_dataset_stewardship_module():
    tool = LOVE_ROOT / "tools" / "dataset_stewardship.py"
    spec = importlib.util.spec_from_file_location("kingdom_dataset_stewardship", tool)
    if spec is None or spec.loader is None:
        raise TrainingStewardshipError("cannot load canonical dataset stewardship")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_source_dataset_manifest(reference, project_root=LOVE_ROOT):
    """Validate the canonical dataset gate bound by a local training request.

    This verifies evidence structure and exact artifacts. It cannot authenticate
    a reviewer string or create account authority; the caller still needs the
    declared human/project authorization represented by the local manifest.
    """
    if not isinstance(reference, dict) or set(reference) != {
        "path",
        "sha256",
        "manifest_id",
    }:
        raise TrainingStewardshipError(
            "source_dataset_manifest must contain path, sha256, and manifest_id"
        )
    expected_digest = _prefixed_sha256(
        reference.get("sha256"), "source_dataset_manifest.sha256"
    )
    manifest_path = _resolve_bound_path(reference.get("path"), project_root)
    if not manifest_path.is_file():
        raise TrainingStewardshipError(
            f"source dataset manifest not found: {manifest_path}"
        )
    if file_sha256(manifest_path) != expected_digest:
        raise TrainingStewardshipError("source dataset manifest digest does not match")
    source = _load_json_object(manifest_path, "source dataset manifest")
    if source.get("manifest_id") != reference.get("manifest_id"):
        raise TrainingStewardshipError("source dataset manifest_id does not match")
    stewardship = _load_dataset_stewardship_module()
    try:
        validated = stewardship.validate_manifest(
            source, root=Path(project_root).resolve()
        )
    except stewardship.StewardshipError as exc:
        raise TrainingStewardshipError(
            f"canonical dataset stewardship rejected the source: {exc}"
        ) from exc
    if validated.get("status") not in {"approved", "published"}:
        raise TrainingStewardshipError(
            "source dataset manifest must be approved or published"
        )
    if validated.get("publication_eligible") is not True:
        raise TrainingStewardshipError(
            "source dataset manifest is not publication-eligible"
        )
    scope = validated.get("consent_and_reuse", {}).get("scope", [])
    if LOCAL_TRAINING_PURPOSE not in scope:
        raise TrainingStewardshipError(
            f"source dataset reuse scope must include {LOCAL_TRAINING_PURPOSE!r}"
        )
    return {
        "manifest": validated,
        "manifest_path": manifest_path,
        "manifest_sha256": expected_digest,
    }


def _validate_review(review):
    review = _exact_keys(review, LOCAL_REVIEW_KEYS, "local_training.review")
    if review.get("state") != "approved":
        raise TrainingStewardshipError("local training review is not approved")
    _nonempty_text(review.get("reviewed_by"), "local_training.review.reviewed_by")
    _datetime_text(review.get("reviewed_at"), "local_training.review.reviewed_at")
    _nonempty_text(review.get("review_ref"), "local_training.review.review_ref")


def load_training_manifest(path, project_root=LOVE_ROOT):
    """Load a declared local permission bound to canonical dataset evidence."""
    manifest_path = Path(path).resolve()
    manifest = _load_json_object(manifest_path, "training manifest")
    _exact_keys(manifest, TRAINING_MANIFEST_KEYS, "training manifest")
    if manifest.get("schema") != TRAINING_MANIFEST_SCHEMA:
        raise TrainingStewardshipError(
            f"unsupported training manifest schema: {manifest.get('schema')!r}"
        )
    _manifest_id(manifest.get("manifest_id"))
    _nonempty_text(manifest.get("dataset_id"), "dataset_id")
    _safe_name(manifest.get("version"), "version")
    if manifest.get("artifact_class") != REVIEWED_ARTIFACT_CLASS:
        raise TrainingStewardshipError(
            "training requires artifact_class='reviewed-outbound-dataset'"
        )
    limitations = manifest.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item.strip() for item in limitations)
    ):
        raise TrainingStewardshipError("limitations must be a non-empty text array")

    permission = manifest.get("local_training")
    permission = _exact_keys(permission, LOCAL_TRAINING_KEYS, "local_training")
    if permission.get("permitted") is not True:
        raise TrainingStewardshipError("manifest does not permit local training")
    if permission.get("purpose") != LOCAL_TRAINING_PURPOSE:
        raise TrainingStewardshipError(
            f"local_training.purpose must be {LOCAL_TRAINING_PURPOSE!r}"
        )
    _validate_review(permission.get("review"))

    source_dataset = validate_source_dataset_manifest(
        manifest.get("source_dataset_manifest"), project_root=project_root
    )
    source_manifest = source_dataset["manifest"]
    if source_manifest.get("dataset_id") != manifest.get("dataset_id"):
        raise TrainingStewardshipError(
            "local and source dataset_id values do not match"
        )
    if source_manifest.get("version") != manifest.get("version"):
        raise TrainingStewardshipError("local and source dataset versions do not match")
    source_files = {item["path"]: item for item in source_manifest.get("files", [])}

    declared_splits = manifest.get("splits")
    if not isinstance(declared_splits, dict):
        raise TrainingStewardshipError("splits must be an object")
    missing = set(REQUIRED_SPLITS) - set(declared_splits)
    unknown = set(declared_splits) - set(REQUIRED_SPLITS) - set(OPTIONAL_SPLITS)
    if missing:
        raise TrainingStewardshipError(
            "manifest is missing immutable split(s): " + ", ".join(sorted(missing))
        )
    if unknown:
        raise TrainingStewardshipError(
            "manifest has unsupported split(s): " + ", ".join(sorted(unknown))
        )

    normalized = {}
    seen_paths = set()
    expected_lanes = {**REQUIRED_SPLITS, **OPTIONAL_SPLITS}
    for split_name in ("train", "validation", "test"):
        if split_name not in declared_splits:
            continue
        entry = declared_splits[split_name]
        entry = _exact_keys(entry, SPLIT_ENTRY_KEYS, f"splits.{split_name}")
        if entry.get("format") != "jsonl":
            raise TrainingStewardshipError(
                f"splits.{split_name}.format must be 'jsonl'"
            )
        if entry.get("lane") != expected_lanes[split_name]:
            raise TrainingStewardshipError(
                f"splits.{split_name}.lane must be {expected_lanes[split_name]!r}"
            )
        expected_digest = entry.get("sha256")
        if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(
            expected_digest
        ):
            raise TrainingStewardshipError(
                f"splits.{split_name}.sha256 must be a prefixed lowercase SHA-256"
            )
        expected_bytes = entry.get("bytes")
        expected_records = entry.get("records")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes <= 0
        ):
            raise TrainingStewardshipError(
                f"splits.{split_name}.bytes must be positive"
            )
        if (
            not isinstance(expected_records, int)
            or isinstance(expected_records, bool)
            or expected_records <= 0
        ):
            raise TrainingStewardshipError(
                f"splits.{split_name}.records must be positive"
            )

        resolved = _resolve_bound_path(entry.get("path"), project_root)
        if resolved in seen_paths:
            raise TrainingStewardshipError("each split must bind a distinct file")
        seen_paths.add(resolved)
        validation = validate_dataset(resolved)
        if not validation["valid"]:
            raise TrainingStewardshipError(
                f"split {split_name} is invalid: {validation['error']}"
            )
        if resolved.stat().st_size != expected_bytes:
            raise TrainingStewardshipError(
                f"split {split_name} byte count does not match"
            )
        if validation["count"] != expected_records:
            raise TrainingStewardshipError(
                f"split {split_name} record count does not match"
            )
        if file_sha256(resolved) != expected_digest:
            raise TrainingStewardshipError(f"split {split_name} digest does not match")
        source_entry = source_files.get(entry.get("path"))
        if source_entry is None:
            raise TrainingStewardshipError(
                f"split {split_name} is not bound by the canonical dataset manifest"
            )
        for field in ("format", "lane", "sha256", "bytes", "records"):
            if source_entry.get(field) != entry.get(field):
                raise TrainingStewardshipError(
                    f"split {split_name}.{field} differs from the canonical dataset manifest"
                )
        normalized[split_name] = {**entry, "resolved_path": resolved}

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": file_sha256(manifest_path),
        "source_dataset": source_dataset,
        "splits": normalized,
    }


def split_dataset(source_path, out_dir, eval_fraction=0.2, seed=42):
    """Create a deterministic *non-authoritative* legacy/dev random split.

    Production ``run`` and ``eval`` never call this compatibility helper. The
    CLI exposes it only behind an exact acknowledgement and writes a prominent
    non-authoritative marker beside its outputs.
    """
    if not 0 < eval_fraction < 1:
        raise ValueError("eval_fraction must be between zero and one")
    validation = validate_dataset(source_path)
    if not validation["valid"]:
        raise ValueError(validation["error"])
    with Path(source_path).open(encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]
    random.Random(seed).shuffle(lines)
    split_index = int(len(lines) * (1 - eval_fraction))
    if split_index == 0 or split_index == len(lines):
        raise ValueError("legacy/dev split requires at least one record in each split")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "train.jsonl").write_text(
        "\n".join(lines[:split_index]) + "\n", encoding="utf-8"
    )
    (out_dir / "valid.jsonl").write_text(
        "\n".join(lines[split_index:]) + "\n", encoding="utf-8"
    )
    return split_index, len(lines) - split_index


def _portable_split(entry):
    return {
        "path": entry["path"],
        "format": entry["format"],
        "lane": entry["lane"],
        "sha256": entry["sha256"],
        "bytes": entry["bytes"],
        "records": entry["records"],
    }


def _portable_dataset(dataset):
    manifest = dataset["manifest"]
    source = dataset["source_dataset"]
    return {
        "manifest_id": manifest["manifest_id"],
        "dataset_id": manifest["dataset_id"],
        "version": manifest["version"],
        "local_manifest_sha256": dataset["manifest_sha256"],
        "source_manifest_id": source["manifest"]["manifest_id"],
        "source_manifest_sha256": source["manifest_sha256"],
        "splits": {
            name: _portable_split(dataset["splits"][name])
            for name in ("train", "validation", "test")
            if name in dataset["splits"]
        },
    }


def _validate_portable_split(name, entry):
    entry = _exact_keys(entry, SPLIT_ENTRY_KEYS, f"run plan split {name}")
    raw_path = _nonempty_text(entry.get("path"), f"run plan split {name}.path")
    logical = PurePosixPath(raw_path)
    if logical.is_absolute() or "." in logical.parts or ".." in logical.parts:
        raise TrainingStewardshipError(f"run plan split {name}.path is unsafe")
    if entry.get("format") != "jsonl":
        raise TrainingStewardshipError(f"run plan split {name}.format must be 'jsonl'")
    expected_lanes = {**REQUIRED_SPLITS, **OPTIONAL_SPLITS}
    if entry.get("lane") != expected_lanes[name]:
        raise TrainingStewardshipError(f"run plan split {name}.lane is invalid")
    _prefixed_sha256(entry.get("sha256"), f"run plan split {name}.sha256")
    for item in ("bytes", "records"):
        value = entry.get(item)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TrainingStewardshipError(
                f"run plan split {name}.{item} must be positive"
            )
    return entry


def _validate_portable_dataset(dataset):
    dataset = _exact_keys(dataset, PORTABLE_DATASET_KEYS, "run plan dataset")
    _manifest_id(dataset.get("manifest_id"))
    _nonempty_text(dataset.get("dataset_id"), "run plan dataset_id")
    _safe_name(dataset.get("version"), "run plan dataset version")
    _prefixed_sha256(
        dataset.get("local_manifest_sha256"),
        "run plan local_manifest_sha256",
    )
    _manifest_id(dataset.get("source_manifest_id"))
    _prefixed_sha256(
        dataset.get("source_manifest_sha256"),
        "run plan source_manifest_sha256",
    )
    splits = dataset.get("splits")
    if not isinstance(splits, dict):
        raise TrainingStewardshipError("run plan dataset splits must be an object")
    missing = set(REQUIRED_SPLITS) - set(splits)
    unknown = set(splits) - set(REQUIRED_SPLITS) - set(OPTIONAL_SPLITS)
    if missing or unknown:
        raise TrainingStewardshipError("run plan dataset split names are invalid")
    for name, entry in splits.items():
        _validate_portable_split(name, entry)
    return dataset


def _validate_portable_model(model, field="run plan model"):
    model = _exact_keys(model, PORTABLE_MODEL_KEYS, field)
    _nonempty_text(model.get("repo_id"), f"{field} repo_id")
    if not isinstance(model.get("revision"), str) or not FULL_COMMIT_RE.fullmatch(
        model["revision"]
    ):
        raise TrainingStewardshipError(f"{field} revision must be a full commit SHA")
    _prefixed_sha256(model.get("lock_sha256"), f"{field} lock_sha256")
    return model


def _training_iterations(portable_dataset, lora_config):
    batch_size = lora_config["batch_size"]
    for name, entry in portable_dataset["splits"].items():
        if entry["records"] < batch_size:
            raise TrainingStewardshipError(
                f"split {name} has {entry['records']} records but MLX-LM "
                f"batch_size is {batch_size}; incomplete batches are dropped"
            )
    iterations_per_epoch = portable_dataset["splits"]["train"]["records"] // batch_size
    return iterations_per_epoch * lora_config["epochs"]


def build_run_plan(
    dataset,
    model_resolution,
    lora_config,
    runtime_environment,
    adapter_name=None,
):
    """Build a deterministic, machine-portable training plan."""
    lora_config = _validate_lora_config(
        json.loads(json.dumps(lora_config)), "run plan training_config"
    )
    portable_model = {
        "repo_id": _nonempty_text(model_resolution.get("repo_id"), "model repo_id"),
        "revision": _nonempty_text(model_resolution.get("revision"), "model revision"),
        "lock_sha256": _normalized_sha256(
            model_resolution.get("lock_sha256"), "model lock_sha256"
        ),
    }
    if not FULL_COMMIT_RE.fullmatch(portable_model["revision"]):
        raise TrainingStewardshipError("model revision must be a full commit SHA")
    runtime_environment = _validate_runtime_environment(
        json.loads(json.dumps(runtime_environment))
    )
    portable_dataset = _validate_portable_dataset(_portable_dataset(dataset))
    effective_config = effective_lora_config(lora_config)
    iterations = _training_iterations(portable_dataset, lora_config)
    base = {
        "schema": RUN_PLAN_SCHEMA,
        "dataset": portable_dataset,
        "model": portable_model,
        "runtime": runtime_environment,
        "training_config": json.loads(json.dumps(lora_config)),
        "launcher": {
            "module": "mlx_lm.lora",
            "mode": "train",
            "effective_config": effective_config,
            "effective_config_sha256": _canonical_file_sha256(effective_config),
            "iterations": iterations,
            "path_arguments": ["model", "data", "adapter-path"],
        },
    }
    seed_digest = _canonical_sha256(base).split(":", 1)[1]
    final_adapter = adapter_name or f"kingdom-{seed_digest[:16]}"
    _safe_name(final_adapter, "adapter")
    base["adapter"] = final_adapter
    semantic_digest = _canonical_sha256(base)
    unsigned_plan = {
        **base,
        "plan_id": f"mlx-{semantic_digest.split(':', 1)[1][:24]}",
    }
    plan = {**unsigned_plan, "plan_sha256": _canonical_sha256(unsigned_plan)}
    return _validate_run_plan(plan)


def write_prelaunch_receipts(run_dir, plan):
    """Persist deterministic plan and planned-state receipt before launch."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "run-plan.json"
    receipt_path = run_dir / "run-receipt.json"
    launcher_config_path = run_dir / "mlx-lm-config.json"
    if plan_path.exists():
        existing = _load_json_object(plan_path, "existing run plan")
        if existing != plan:
            raise TrainingStewardshipError(f"run plan collision at {plan_path}")
    _write_canonical_json(plan_path, plan)
    _write_canonical_json(launcher_config_path, plan["launcher"]["effective_config"])
    if file_sha256(launcher_config_path) != plan["launcher"]["effective_config_sha256"]:
        raise TrainingStewardshipError(
            "effective MLX-LM config digest changed on write"
        )
    receipt = {
        "schema": RUN_RECEIPT_SCHEMA,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "state": "planned",
        "training_launched": False,
        "boundary": (
            "A planned receipt records intended inputs; it is not evidence that "
            "training completed or that serving or release was approved."
        ),
    }
    _write_canonical_json(receipt_path, receipt)
    return plan_path, receipt_path, launcher_config_path


def _stage_declared_splits(dataset, data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    destinations = {
        "train": "train.jsonl",
        "validation": "valid.jsonl",
        "test": "test.jsonl",
    }
    for name, entry in dataset["splits"].items():
        destination = data_dir / destinations[name]
        shutil.copyfile(entry["resolved_path"], destination)
        if (
            destination.stat().st_size != entry["bytes"]
            or file_sha256(destination) != entry["sha256"]
        ):
            raise TrainingStewardshipError(
                f"split {name} changed while being staged; training was not launched"
            )


def build_lora_args(model, data_dir, adapter_path, config_path, iterations):
    """Build CLI arguments for ``python -m mlx_lm.lora``."""
    return [
        "--config",
        str(config_path),
        "--model",
        model,
        "--data",
        str(data_dir),
        "--adapter-path",
        str(adapter_path),
        "--train",
        "--iters",
        str(iterations),
    ]


def _write_run_result(run_dir, plan, state, exit_code, detail):
    result = {
        "schema": RUN_RESULT_SCHEMA,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "state": state,
        "exit_code": exit_code,
        "recorded_at": _utc_now(),
        "detail": detail,
    }
    _write_canonical_json(Path(run_dir) / "run-result.json", result)
    return result


def validate_adapter_config(adapter_path, plan, model_resolution=None):
    """Validate the complete config emitted by the pinned MLX-LM trainer."""
    adapter_path = Path(adapter_path)
    plan = _validate_run_plan(plan)
    adapter_config_path = adapter_path / "adapter_config.json"
    if (
        adapter_config_path.is_symlink()
        or not adapter_config_path.is_file()
        or adapter_config_path.stat().st_size <= 0
    ):
        raise TrainingStewardshipError(
            f"training returned without required adapter config: {adapter_config_path}"
        )
    actual_config = _load_json_object(adapter_config_path, "MLX adapter config")
    _exact_keys(actual_config, MLX_ADAPTER_CONFIG_KEYS, "MLX adapter config")
    expected_config = {
        **PINNED_MLX_LM_DEFAULTS,
        **plan["launcher"]["effective_config"],
        "train": True,
        "iters": plan["launcher"]["iterations"],
    }
    for field, expected in expected_config.items():
        if actual_config.get(field) != expected:
            raise TrainingStewardshipError(
                f"MLX adapter config {field} does not match the run plan"
            )
    for field in ("model", "data", "adapter_path", "config"):
        value = _nonempty_text(actual_config.get(field), f"MLX adapter config {field}")
        if not Path(value).is_absolute():
            raise TrainingStewardshipError(
                f"MLX adapter config {field} must be an absolute path"
            )
    if Path(actual_config["adapter_path"]).resolve() != adapter_path.resolve():
        raise TrainingStewardshipError(
            "MLX adapter config adapter_path does not match the artifact"
        )
    data_path = Path(actual_config["data"])
    launcher_config_path = Path(actual_config["config"])
    expected_run_dir = (RUNS_DIR / plan["plan_id"]).resolve()
    if data_path.resolve() != expected_run_dir / "data":
        raise TrainingStewardshipError(
            "MLX adapter config data path does not match the launched run"
        )
    if launcher_config_path.resolve() != expected_run_dir / "mlx-lm-config.json":
        raise TrainingStewardshipError(
            "MLX adapter config config path does not match the launched run"
        )
    if model_resolution is not None:
        expected_model_path = _nonempty_text(
            model_resolution.get("path"), "resolved model path"
        )
        if (
            Path(actual_config["model"]).resolve()
            != Path(expected_model_path).resolve()
        ):
            raise TrainingStewardshipError(
                "MLX adapter config model path differs from the resolved model"
            )
    return adapter_config_path, actual_config


def build_adapter_receipt(adapter_path, plan, model_resolution=None):
    """Build the post-training receipt, failing closed if artifacts are absent."""
    adapter_path = Path(adapter_path)
    plan = _validate_run_plan(plan)
    weights_path = adapter_path / ADAPTER_WEIGHTS_NAME
    if (
        weights_path.is_symlink()
        or not weights_path.is_file()
        or weights_path.stat().st_size <= 0
    ):
        raise TrainingStewardshipError(
            f"training returned without required weights: {weights_path}"
        )
    adapter_config_path, _ = validate_adapter_config(
        adapter_path, plan, model_resolution=model_resolution
    )
    bound_files = {
        ADAPTER_WEIGHTS_NAME: {
            "sha256": file_sha256(weights_path),
            "bytes": weights_path.stat().st_size,
        },
        "adapter_config.json": {
            "sha256": file_sha256(adapter_config_path),
            "bytes": adapter_config_path.stat().st_size,
        },
    }
    return {
        "schema": ADAPTER_RECEIPT_SCHEMA,
        "state": "completed",
        "adapter": plan["adapter"],
        "run_plan": plan,
        "weights": {
            "path": ADAPTER_WEIGHTS_NAME,
            **bound_files[ADAPTER_WEIGHTS_NAME],
        },
        "artifact_files": bound_files,
        "approvals": {
            "serving": {"state": "not-approved", "review_ref": None},
            "release": {"state": "not-approved", "review_ref": None},
        },
        "boundary": ADAPTER_RECEIPT_BOUNDARY,
    }


def _validate_run_plan(plan):
    plan = _exact_keys(plan, RUN_PLAN_KEYS, "adapter run plan")
    if plan.get("schema") != RUN_PLAN_SCHEMA:
        raise TrainingStewardshipError("adapter receipt has an invalid run plan")
    _safe_name(plan.get("adapter"), "adapter run plan adapter")
    dataset = _validate_portable_dataset(plan.get("dataset"))
    _validate_portable_model(plan.get("model"))
    _validate_runtime_environment(plan.get("runtime"))
    training_config = _validate_lora_config(
        plan.get("training_config"), "run plan training_config"
    )
    launcher = _exact_keys(plan.get("launcher"), LAUNCHER_KEYS, "run plan launcher")
    if launcher.get("module") != "mlx_lm.lora" or launcher.get("mode") != "train":
        raise TrainingStewardshipError("run plan launcher module or mode is invalid")
    expected_effective = effective_lora_config(training_config)
    if launcher.get("effective_config") != expected_effective:
        raise TrainingStewardshipError(
            "run plan effective config differs from training_config"
        )
    if launcher.get("effective_config_sha256") != _canonical_file_sha256(
        expected_effective
    ):
        raise TrainingStewardshipError(
            "run plan effective config digest does not match"
        )
    expected_iterations = _training_iterations(dataset, training_config)
    if launcher.get("iterations") != expected_iterations:
        raise TrainingStewardshipError("run plan iteration count does not match MLX-LM")
    if launcher.get("path_arguments") != ["model", "data", "adapter-path"]:
        raise TrainingStewardshipError("run plan path arguments are invalid")
    claimed = plan.get("plan_sha256")
    _prefixed_sha256(claimed, "adapter run plan plan_sha256")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if claimed != _canonical_sha256(unsigned):
        raise TrainingStewardshipError("adapter run plan digest does not match")
    semantic = {key: value for key, value in unsigned.items() if key != "plan_id"}
    expected_id = f"mlx-{_canonical_sha256(semantic).split(':', 1)[1][:24]}"
    if plan.get("plan_id") != expected_id:
        raise TrainingStewardshipError("adapter run plan ID does not match")
    return plan


def validate_adapter_artifact(
    adapter_name,
    *,
    model_resolution=None,
    dataset=None,
    runtime_environment=None,
    require_serving=False,
    adapters_root=ADAPTERS_DIR,
):
    """Verify one adapter, receipt, plan, weights, and requested bindings."""
    name = _safe_name(adapter_name, "adapter")
    root = Path(adapters_root).resolve()
    adapter_path = root / name
    if adapter_path.is_symlink() or not adapter_path.is_dir():
        raise TrainingStewardshipError(
            f"adapter must be a regular directory under {root}: {name}"
        )
    try:
        adapter_path.resolve().relative_to(root)
    except ValueError as exc:
        raise TrainingStewardshipError("adapter escapes the adapter root") from exc
    receipt_path = adapter_path / ADAPTER_RECEIPT_NAME
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise TrainingStewardshipError(
            f"adapter has no regular training receipt: {receipt_path}"
        )
    receipt = _load_json_object(receipt_path, "adapter training receipt")
    _exact_keys(receipt, ADAPTER_RECEIPT_KEYS, "adapter training receipt")
    if receipt.get("schema") != ADAPTER_RECEIPT_SCHEMA:
        raise TrainingStewardshipError("adapter receipt schema is invalid")
    if receipt.get("state") != "completed" or receipt.get("adapter") != name:
        raise TrainingStewardshipError("adapter receipt state or identity is invalid")
    if receipt.get("boundary") != ADAPTER_RECEIPT_BOUNDARY:
        raise TrainingStewardshipError("adapter receipt boundary is invalid")
    plan = _validate_run_plan(receipt.get("run_plan"))
    if plan.get("adapter") != name:
        raise TrainingStewardshipError("adapter receipt and run plan identities differ")

    weights = _exact_keys(
        receipt.get("weights"), WEIGHTS_BINDING_KEYS, "adapter receipt weights"
    )
    if weights.get("path") != ADAPTER_WEIGHTS_NAME:
        raise TrainingStewardshipError(
            f"adapter receipt weights.path must be {ADAPTER_WEIGHTS_NAME!r}"
        )
    weights_path = adapter_path / ADAPTER_WEIGHTS_NAME
    expected_bytes = weights.get("bytes")
    expected_digest = weights.get("sha256")
    if (
        weights_path.is_symlink()
        or not weights_path.is_file()
        or not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes <= 0
        or weights_path.stat().st_size != expected_bytes
        or not isinstance(expected_digest, str)
        or not SHA256_RE.fullmatch(expected_digest)
        or file_sha256(weights_path) != expected_digest
    ):
        raise TrainingStewardshipError("adapter weights do not match their receipt")
    artifact_files = _exact_keys(
        receipt.get("artifact_files"),
        {ADAPTER_WEIGHTS_NAME, "adapter_config.json"},
        "adapter artifact inventory",
    )
    weights_binding = _exact_keys(
        artifact_files.get(ADAPTER_WEIGHTS_NAME),
        FILE_BINDING_KEYS,
        "adapter weights inventory binding",
    )
    if weights_binding != {"sha256": expected_digest, "bytes": expected_bytes}:
        raise TrainingStewardshipError("adapter artifact inventory is inconsistent")
    config_binding = _exact_keys(
        artifact_files.get("adapter_config.json"),
        FILE_BINDING_KEYS,
        "adapter config inventory binding",
    )
    config_path, _ = validate_adapter_config(
        adapter_path, plan, model_resolution=model_resolution
    )
    config_bytes = config_binding.get("bytes")
    config_digest = config_binding.get("sha256")
    if (
        not isinstance(config_bytes, int)
        or isinstance(config_bytes, bool)
        or config_bytes <= 0
        or not isinstance(config_digest, str)
        or not SHA256_RE.fullmatch(config_digest)
        or config_path.stat().st_size != config_bytes
        or file_sha256(config_path) != config_digest
    ):
        raise TrainingStewardshipError("adapter config does not match its receipt")

    if model_resolution is not None:
        expected_model = {
            "repo_id": model_resolution.get("repo_id"),
            "revision": model_resolution.get("revision"),
            "lock_sha256": _normalized_sha256(
                model_resolution.get("lock_sha256"), "model lock_sha256"
            ),
        }
        if plan.get("model") != expected_model:
            raise TrainingStewardshipError(
                "adapter base-model binding differs from the resolved model"
            )
    if dataset is not None and plan.get("dataset") != _portable_dataset(dataset):
        raise TrainingStewardshipError(
            "adapter dataset binding differs from the requested manifest"
        )
    if runtime_environment is not None:
        runtime_environment = _validate_runtime_environment(runtime_environment)
        if plan.get("runtime") != runtime_environment:
            raise TrainingStewardshipError(
                "adapter runtime binding differs from the current MLX environment"
            )

    approvals = _exact_keys(
        receipt.get("approvals"), APPROVALS_KEYS, "adapter receipt approvals"
    )
    serving = _exact_keys(
        approvals.get("serving"),
        APPROVAL_STATE_KEYS,
        "adapter receipt serving approval",
    )
    if serving != {
        "state": "not-approved",
        "review_ref": None,
    }:
        raise TrainingStewardshipError("adapter serving approval state is invalid")
    release = _exact_keys(
        approvals.get("release"),
        APPROVAL_STATE_KEYS,
        "adapter receipt release approval",
    )
    if release != {"state": "not-approved", "review_ref": None}:
        raise TrainingStewardshipError("adapter release approval state is invalid")
    serving_state = "not-approved"
    approval_path = adapter_path / SERVING_APPROVAL_NAME
    if approval_path.exists():
        if approval_path.is_symlink() or not approval_path.is_file():
            raise TrainingStewardshipError("serving approval must be a regular file")
        approval = _load_json_object(approval_path, "serving approval")
        _exact_keys(approval, SERVING_APPROVAL_KEYS, "serving approval")
        expected_approval = {
            "schema": SERVING_APPROVAL_SCHEMA,
            "state": "approved",
            "scope": "loopback-only-local-serving",
            "adapter": name,
            "training_receipt_sha256": file_sha256(receipt_path),
            "boundary": SERVING_APPROVAL_BOUNDARY,
        }
        for field, expected in expected_approval.items():
            if approval.get(field) != expected:
                raise TrainingStewardshipError(
                    f"serving approval {field} does not match the adapter"
                )
        for field in ("declared_by", "decision_ref"):
            _nonempty_text(approval.get(field), f"serving approval {field}")
        _datetime_text(approval.get("declared_at"), "serving approval declared_at")
        serving_state = "approved"
    if require_serving and serving_state != "approved":
        raise TrainingStewardshipError(
            "adapter is not approved for loopback-only local serving"
        )
    return {
        "adapter_path": adapter_path,
        "receipt": receipt,
        "receipt_sha256": file_sha256(receipt_path),
        "serving_state": serving_state,
        "runtime": plan["runtime"],
    }


def cmd_run(args):
    if not args.manifest:
        legacy_dataset = getattr(args, "dataset", None)
        legacy = (
            f" Legacy --dataset={legacy_dataset!r} was supplied."
            if legacy_dataset
            else ""
        )
        raise TrainingStewardshipError(
            "run requires --manifest with explicit local-training permission;" + legacy
        )
    dataset = load_training_manifest(args.manifest)
    lora_config = load_lora_config()
    if args.epochs is not None:
        if args.epochs <= 0:
            raise TrainingStewardshipError("--epochs must be positive")
        lora_config["epochs"] = args.epochs
    runtime_environment = resolve_training_environment()
    model_resolution = resolve_training_model()
    plan = build_run_plan(
        dataset,
        model_resolution,
        lora_config,
        runtime_environment,
        args.adapter,
    )
    run_dir = RUNS_DIR / plan["plan_id"]
    adapter_path = ADAPTERS_DIR / plan["adapter"]
    if adapter_path.exists():
        raise TrainingStewardshipError(
            f"adapter path already exists; refusing to overwrite: {adapter_path}"
        )

    plan_path, receipt_path, launcher_config_path = write_prelaunch_receipts(
        run_dir, plan
    )
    print(
        json.dumps(
            {
                "plan_id": plan["plan_id"],
                "plan": str(plan_path),
                "prelaunch_receipt": str(receipt_path),
            },
            sort_keys=True,
        )
    )

    try:
        data_dir = run_dir / "data"
        _stage_declared_splits(dataset, data_dir)
        adapter_path.mkdir(parents=True, exist_ok=False)
        cli_args = build_lora_args(
            model_resolution["path"],
            data_dir,
            adapter_path,
            launcher_config_path,
            plan["launcher"]["iterations"],
        )
        result = subprocess.run(
            [str(VENV_PYTHON), "-I", "-m", "mlx_lm.lora", *cli_args],
            capture_output=False,
        )
    except BaseException as exc:
        state = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
        _write_run_result(
            run_dir,
            plan,
            state,
            exit_code,
            f"training did not complete: {type(exc).__name__}",
        )
        raise
    if result.returncode != 0:
        _write_run_result(
            run_dir, plan, "failed", result.returncode, "MLX trainer returned non-zero"
        )
        raise TrainingStewardshipError(
            f"training failed with exit code {result.returncode}"
        )

    try:
        adapter_receipt = build_adapter_receipt(
            adapter_path, plan, model_resolution=model_resolution
        )
    except TrainingStewardshipError:
        _write_run_result(
            run_dir,
            plan,
            "failed",
            1,
            "MLX trainer returned zero but required adapter weights were absent",
        )
        raise
    _write_canonical_json(adapter_path / ADAPTER_RECEIPT_NAME, adapter_receipt)
    _write_run_result(run_dir, plan, "completed", 0, "adapter receipt written")
    print(f"Training complete: {adapter_path}")
    print("Serving and release remain not-approved in the adapter receipt.")


def _evaluation_settings(plan):
    effective = plan["launcher"]["effective_config"]
    return {
        "backend": "mlx-lm",
        "mode": "test",
        "batch_size": effective["batch_size"],
        "max_seq_length": effective["max_seq_length"],
        "test_batches": -1,
        "seed": effective["seed"],
    }


def _validate_evaluation_settings(settings):
    settings = _exact_keys(settings, EVALUATION_SETTINGS_KEYS, "evaluation settings")
    if settings.get("backend") != "mlx-lm" or settings.get("mode") != "test":
        raise TrainingStewardshipError("evaluation backend or mode is invalid")
    if settings.get("test_batches") != -1:
        raise TrainingStewardshipError(
            "evaluation must cover all complete test batches"
        )
    for field in ("batch_size", "max_seq_length"):
        value = settings.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TrainingStewardshipError(
                f"evaluation settings {field} must be positive"
            )
    seed = settings.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise TrainingStewardshipError("evaluation settings seed must be non-negative")
    return settings


def _evaluation_id(receipt_without_id):
    return "mlx-eval-" + _canonical_sha256(receipt_without_id).split(":", 1)[1][:24]


def validate_evaluation_receipt(
    receipt,
    *,
    plan=None,
    adapter_receipt_sha256=None,
):
    receipt = _exact_keys(receipt, EVALUATION_RECEIPT_KEYS, "evaluation receipt")
    if receipt.get("schema") != EVALUATION_RECEIPT_SCHEMA:
        raise TrainingStewardshipError("evaluation receipt schema is invalid")
    if receipt.get("state") != "completed":
        raise TrainingStewardshipError("evaluation receipt state is invalid")
    _safe_name(receipt.get("adapter"), "evaluation adapter")
    _datetime_text(receipt.get("recorded_at"), "evaluation recorded_at")
    _prefixed_sha256(
        receipt.get("adapter_receipt_sha256"),
        "evaluation adapter_receipt_sha256",
    )
    _validate_portable_model(receipt.get("model"), "evaluation model")
    _validate_portable_dataset(receipt.get("dataset"))
    split = _exact_keys(receipt.get("split"), EVALUATION_SPLIT_KEYS, "evaluation split")
    split_name = split.get("name")
    if split_name not in {"validation", "test"}:
        raise TrainingStewardshipError("evaluation split name is invalid")
    _validate_portable_split(
        split_name, {key: value for key, value in split.items() if key != "name"}
    )
    _validate_runtime_environment(receipt.get("runtime"))
    settings = _validate_evaluation_settings(receipt.get("settings"))
    metrics = _exact_keys(
        receipt.get("metrics"), EVALUATION_METRICS_KEYS, "evaluation metrics"
    )
    for field in EVALUATION_METRICS_KEYS:
        value = metrics.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise TrainingStewardshipError(f"evaluation metric {field} must be finite")
    if metrics["test_loss"] < 0 or metrics["test_perplexity"] <= 0:
        raise TrainingStewardshipError("evaluation metrics are outside valid ranges")
    if not math.isclose(
        math.log(metrics["test_perplexity"]),
        metrics["test_loss"],
        rel_tol=0,
        abs_tol=0.0011,
    ):
        raise TrainingStewardshipError(
            "evaluation perplexity is inconsistent with exp(test_loss)"
        )
    if receipt.get("boundary") != EVALUATION_RECEIPT_BOUNDARY:
        raise TrainingStewardshipError("evaluation receipt boundary is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "evaluation_id"}
    if receipt.get("evaluation_id") != _evaluation_id(unsigned):
        raise TrainingStewardshipError("evaluation receipt ID does not match")
    if not EVALUATION_ID_RE.fullmatch(receipt["evaluation_id"]):
        raise TrainingStewardshipError("evaluation receipt ID is invalid")
    if adapter_receipt_sha256 is not None:
        expected_digest = _prefixed_sha256(
            adapter_receipt_sha256, "expected adapter receipt digest"
        )
        if receipt["adapter_receipt_sha256"] != expected_digest:
            raise TrainingStewardshipError(
                "evaluation adapter receipt binding does not match"
            )
    if plan is not None:
        plan = _validate_run_plan(plan)
        expected_split = {
            "name": split_name,
            **plan["dataset"]["splits"][split_name],
        }
        expected = {
            "adapter": plan["adapter"],
            "model": plan["model"],
            "dataset": plan["dataset"],
            "split": expected_split,
            "runtime": plan["runtime"],
            "settings": _evaluation_settings(plan),
        }
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise TrainingStewardshipError(
                    f"evaluation {field} binding differs from the run plan"
                )
    return receipt


def build_evaluation_receipt(adapter_artifact, split_name, metrics):
    plan = _validate_run_plan(adapter_artifact["receipt"]["run_plan"])
    if split_name not in plan["dataset"]["splits"]:
        raise TrainingStewardshipError(
            f"run plan does not bind evaluation split: {split_name}"
        )
    base = {
        "schema": EVALUATION_RECEIPT_SCHEMA,
        "state": "completed",
        "recorded_at": datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "adapter": plan["adapter"],
        "adapter_receipt_sha256": adapter_artifact["receipt_sha256"],
        "model": plan["model"],
        "dataset": plan["dataset"],
        "split": {
            "name": split_name,
            **plan["dataset"]["splits"][split_name],
        },
        "runtime": plan["runtime"],
        "settings": _evaluation_settings(plan),
        "metrics": metrics,
        "boundary": EVALUATION_RECEIPT_BOUNDARY,
    }
    receipt = {**base, "evaluation_id": _evaluation_id(base)}
    return validate_evaluation_receipt(
        receipt,
        plan=plan,
        adapter_receipt_sha256=adapter_artifact["receipt_sha256"],
    )


def cmd_eval(args):
    """Evaluate only an immutable manifest-declared validation/test split."""
    dataset = load_training_manifest(args.manifest)
    split_name = args.split
    if split_name not in dataset["splits"]:
        raise TrainingStewardshipError(
            f"manifest does not declare requested split: {split_name}"
        )
    runtime_environment = resolve_training_environment()
    model_resolution = resolve_training_model()
    adapter = validate_adapter_artifact(
        args.adapter,
        model_resolution=model_resolution,
        dataset=dataset,
        runtime_environment=runtime_environment,
    )
    adapter_path = adapter["adapter_path"]
    plan = adapter["receipt"]["run_plan"]
    with tempfile.TemporaryDirectory(prefix="kingdom-mlx-eval-") as temp_dir:
        data_dir = Path(temp_dir)
        entry = dataset["splits"][split_name]
        shutil.copyfile(entry["resolved_path"], data_dir / "test.jsonl")
        if (data_dir / "test.jsonl").stat().st_size != entry["bytes"] or file_sha256(
            data_dir / "test.jsonl"
        ) != entry["sha256"]:
            raise TrainingStewardshipError("evaluation split changed while staging")
        launcher_config_path = data_dir / "mlx-lm-config.json"
        _write_canonical_json(
            launcher_config_path, plan["launcher"]["effective_config"]
        )
        if (
            file_sha256(launcher_config_path)
            != plan["launcher"]["effective_config_sha256"]
        ):
            raise TrainingStewardshipError(
                "evaluation MLX-LM config digest changed on write"
            )
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                "-I",
                "-m",
                "mlx_lm.lora",
                "--config",
                str(launcher_config_path),
                "--model",
                model_resolution["path"],
                "--adapter-path",
                str(adapter_path),
                "--data",
                str(data_dir),
                "--test",
                "--test-batches",
                "-1",
            ],
            capture_output=True,
            text=True,
        )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise TrainingStewardshipError(
            f"evaluation failed with exit code {result.returncode}"
        )
    matches = EVALUATION_METRICS_RE.findall(
        (result.stdout or "") + "\n" + (result.stderr or "")
    )
    if not matches:
        raise TrainingStewardshipError(
            "evaluation completed without parseable MLX-LM test metrics"
        )
    loss, perplexity = map(float, matches[-1])
    current_adapter = validate_adapter_artifact(
        args.adapter,
        model_resolution=model_resolution,
        dataset=dataset,
        runtime_environment=runtime_environment,
    )
    if current_adapter["receipt_sha256"] != adapter["receipt_sha256"]:
        raise TrainingStewardshipError("adapter receipt changed during evaluation")
    adapter = current_adapter
    receipt = build_evaluation_receipt(
        adapter,
        split_name,
        {"test_loss": loss, "test_perplexity": perplexity},
    )
    evaluations_dir = adapter_path / EVALUATIONS_DIR_NAME
    if evaluations_dir.exists() and (
        evaluations_dir.is_symlink() or not evaluations_dir.is_dir()
    ):
        raise TrainingStewardshipError(
            "adapter evaluations path must be a regular directory"
        )
    evaluations_dir.mkdir(exist_ok=True)
    receipt_path = evaluations_dir / f"{receipt['evaluation_id']}.json"
    try:
        with receipt_path.open("xb") as handle:
            handle.write(_canonical_bytes(receipt) + b"\n")
    except FileExistsError as exc:
        raise TrainingStewardshipError(
            f"evaluation receipt already exists; refusing to overwrite: {receipt_path}"
        ) from exc
    written = _load_json_object(receipt_path, "written evaluation receipt")
    validate_evaluation_receipt(
        written,
        plan=plan,
        adapter_receipt_sha256=adapter["receipt_sha256"],
    )
    print(f"Evaluation receipt: {receipt_path}")


def cmd_legacy_dev_split(args):
    if args.acknowledge_non_authoritative_split != LEGACY_DEV_ACK:
        raise TrainingStewardshipError(
            "legacy-dev-split requires the exact --acknowledge-non-authoritative-split value"
        )
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise TrainingStewardshipError(
            f"legacy/dev output directory is not empty: {output}"
        )
    n_train, n_valid = split_dataset(
        args.source, output, eval_fraction=args.eval_fraction, seed=args.seed
    )
    marker = {
        "schema": "kingdom.non-authoritative-legacy-dev-split/v1",
        "authorized_for_training": False,
        "source_sha256": file_sha256(args.source),
        "seed": args.seed,
        "eval_fraction": args.eval_fraction,
        "train_records": n_train,
        "validation_records": n_valid,
        "warning": (
            "Runtime random splits are not authoritative. A separate reviewed "
            "local-training manifest must bind immutable split files before training."
        ),
    }
    _write_canonical_json(output / LEGACY_DEV_MARKER, marker)
    print(f"Created NON-AUTHORITATIVE split in {output}")


def cmd_approve_serving(args):
    """Record a scoped local serving decision without changing training truth."""
    if args.acknowledge_loopback_only != SERVING_ACK:
        raise TrainingStewardshipError(
            "approve-serving requires the exact loopback-only acknowledgement"
        )
    artifact = validate_adapter_artifact(args.adapter)
    approval_path = artifact["adapter_path"] / SERVING_APPROVAL_NAME
    if approval_path.exists():
        raise TrainingStewardshipError(
            f"serving approval already exists; refusing to overwrite: {approval_path}"
        )
    approval = {
        "schema": SERVING_APPROVAL_SCHEMA,
        "state": "approved",
        "scope": "loopback-only-local-serving",
        "adapter": args.adapter,
        "training_receipt_sha256": artifact["receipt_sha256"],
        "declared_by": _nonempty_text(args.declared_by, "declared_by"),
        "declared_at": _utc_now(),
        "decision_ref": _nonempty_text(args.decision_ref, "decision_ref"),
        "boundary": SERVING_APPROVAL_BOUNDARY,
    }
    _write_canonical_json(approval_path, approval)
    validate_adapter_artifact(args.adapter, require_serving=True)
    print(f"Recorded loopback-only serving approval: {approval_path}")


def cmd_list(_args):
    """List adapters and distinguish receipted artifacts from legacy state."""
    if not ADAPTERS_DIR.exists():
        print("No adapters.")
        return
    adapters = sorted(path for path in ADAPTERS_DIR.iterdir() if path.is_dir())
    if not adapters:
        print("No adapters.")
        return
    print("Available adapters:")
    for adapter in adapters:
        try:
            validated = validate_adapter_artifact(adapter.name)
            print(
                f"  {adapter.name:24s} artifact-verified; "
                f"serving={validated['serving_state']}"
            )
        except TrainingStewardshipError:
            receipt_path = adapter / ADAPTER_RECEIPT_NAME
            state = (
                "legacy-unreceipted"
                if not receipt_path.is_file()
                else "receipt-invalid"
            )
            print(
                f"  {adapter.name:24s} {state} "
                "(not eligible for hardened evaluation or serving)"
            )


def main():
    parser = argparse.ArgumentParser(description="Fail-closed MLX LoRA fine-tuning")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Train from an approved immutable manifest")
    p_run.add_argument("--manifest", help="Local-training manifest path")
    p_run.add_argument(
        "--dataset",
        help="Legacy option retained only to produce an explicit fail-closed error",
    )
    p_run.add_argument("--epochs", type=int, default=None)
    p_run.add_argument("--adapter", default=None, help="New adapter output name")

    p_eval = sub.add_parser("eval", help="Evaluate a receipted adapter")
    p_eval.add_argument("--adapter", required=True)
    p_eval.add_argument("--manifest", required=True)
    p_eval.add_argument("--split", choices=("validation", "test"), default="validation")

    p_dev = sub.add_parser(
        "legacy-dev-split", help="Create a marked non-authoritative random split"
    )
    p_dev.add_argument("--source", required=True)
    p_dev.add_argument("--output", required=True)
    p_dev.add_argument("--eval-fraction", type=float, default=0.2)
    p_dev.add_argument("--seed", type=int, default=42)
    p_dev.add_argument("--acknowledge-non-authoritative-split", required=True)

    p_serve = sub.add_parser(
        "approve-serving", help="record a loopback-only local serving decision"
    )
    p_serve.add_argument("--adapter", required=True)
    p_serve.add_argument("--declared-by", required=True)
    p_serve.add_argument("--decision-ref", required=True)
    p_serve.add_argument("--acknowledge-loopback-only", required=True)

    sub.add_parser("list", help="List adapters and receipt state")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    try:
        {
            "run": cmd_run,
            "eval": cmd_eval,
            "legacy-dev-split": cmd_legacy_dev_split,
            "approve-serving": cmd_approve_serving,
            "list": cmd_list,
        }[args.command](args)
    except (TrainingStewardshipError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
