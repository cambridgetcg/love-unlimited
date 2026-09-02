#!/usr/bin/env python3
"""Create and evaluate KINGDOM data without mining live operational history.

Generated examples remain unreviewed. Operational data can enter the training
staging area only through an explicitly supplied, digest-bound reviewed
outbound artifact. The legacy conversion helper never defaults to the live
history path, and the former ``harvest`` command is a fail-closed tombstone.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
DATASETS_DIR = MLX_DIR / "training" / "datasets"
REVIEWED_DATASETS_DIR = MLX_DIR / "training" / "reviewed"
CANDIDATE_DATASETS_DIR = MLX_DIR / "training" / "candidates"
TEMPLATES_DIR = MLX_DIR / "training" / "templates"
DELEGATION_HISTORY = LOVE_ROOT / "coordination" / "delegate" / "history.json"

REVIEWED_OUTBOUND_SCHEMA = "kingdom.reviewed-outbound-training-artifact/v1"
REVIEWED_IMPORT_RECEIPT_SCHEMA = "kingdom.reviewed-training-import-receipt/v1"
REVIEW_PURPOSE = "local-training-data"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

TASK_LABELS = {
    "heartbeat-triage": ("urgent", "active", "idle", "skip"),
    "message-classify": ("action-required", "informational", "noise"),
    "task-routing": ("alpha", "beta", "gamma", "nuance"),
    "signal-classify": ("high", "medium", "low"),
}
TASK_TYPES = list(TASK_LABELS)

SYSTEM_PROMPTS = {
    "heartbeat-triage": "You are Kingdom triage. Given the system state, classify priority as exactly one of: urgent, active, idle, skip.",
    "message-classify": "You are Kingdom message classifier. Given a HIVE message, classify as exactly one of: action-required, informational, noise.",
    "task-routing": "You are Kingdom task router. Given a task description, respond with the best instance name (alpha, beta, gamma, or nuance).",
    "signal-classify": "You are Kingdom signal classifier. Given a stigmergy signal, classify urgency as exactly one of: high, medium, low.",
}


class DataStewardshipError(RuntimeError):
    """Raised when a data movement or review boundary cannot be established."""


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


def _decode_json(text, label):
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as exc:
        raise DataStewardshipError(
            f"{label} contains duplicate key {exc.args[0]!r}"
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise DataStewardshipError(f"{label} is not strict JSON: {exc}") from exc


def _canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _records_bytes(records):
    return b"".join(_canonical_bytes(record) + b"\n" for record in records)


def records_sha256(records):
    """Return the stable digest of records in canonical JSONL form."""
    return "sha256:" + hashlib.sha256(_records_bytes(records)).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _nonempty_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DataStewardshipError(f"{field} must be a non-empty string")
    return value


def _safe_name(value, field):
    value = _nonempty_text(value, field)
    if not SAFE_NAME_RE.fullmatch(value):
        raise DataStewardshipError(f"{field} contains unsafe characters")
    return value


def _is_live_operational_path(path):
    resolved = Path(path).resolve()
    if resolved == DELEGATION_HISTORY.resolve():
        return True
    for relative in ("coordination", "memory", "hive/history", "logs"):
        root = (LOVE_ROOT / relative).resolve()
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_chat_record(record, field="record"):
    if not isinstance(record, dict):
        raise DataStewardshipError(f"{field} must be an object")
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise DataStewardshipError(f"{field}.messages must be a non-empty list")
    for index, message in enumerate(messages):
        if (
            not isinstance(message, dict)
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
        ):
            raise DataStewardshipError(f"{field}.messages[{index}] is invalid")
    if messages[-1].get("role") != "assistant":
        raise DataStewardshipError(f"{field} must end with an assistant target")
    return record


def parse_generated_examples(raw_text):
    """Parse JSONL from a generator response, ignoring prose and code fences."""
    examples = []
    text = raw_text.replace("```json", "").replace("```", "")
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = _decode_json(line, "generated example")
            _validate_chat_record(obj)
        except DataStewardshipError:
            continue
        examples.append(obj)
    return examples


def harvest_delegation_history(history_path=None):
    """Legacy pure converter for an explicit offline fixture or prior export.

    It intentionally has no default. Passing the live operational path is
    rejected. CLI import requires the stronger reviewed outbound envelope.
    """
    if history_path is None:
        raise DataStewardshipError(
            "live delegation history harvest is disabled; supply a reviewed outbound artifact"
        )
    path = Path(history_path)
    if _is_live_operational_path(path):
        raise DataStewardshipError(
            "direct operational-history harvest is disabled; export and review it first"
        )
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as handle:
            history = _decode_json(handle.read(), "legacy offline history")
    except (DataStewardshipError, OSError):
        return []
    if not isinstance(history, list):
        return []

    examples = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        task = entry.get("task", "")
        instance = entry.get("instance", "")
        if not task or not instance:
            continue
        examples.append(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPTS["task-routing"]},
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": instance},
                ],
                "source": "legacy-offline-conversion-unreviewed",
            }
        )
    return examples


def load_reviewed_outbound_artifact(path):
    """Verify and return an explicit, reviewed, digest-bound outbound artifact."""
    artifact_path = Path(path).resolve()
    if _is_live_operational_path(artifact_path):
        raise DataStewardshipError(
            "reviewed artifacts must be separate from live operational storage"
        )
    try:
        with artifact_path.open(encoding="utf-8") as handle:
            artifact = _decode_json(handle.read(), "reviewed outbound artifact")
    except FileNotFoundError as exc:
        raise DataStewardshipError(f"reviewed artifact not found: {artifact_path}") from exc
    except (OSError, DataStewardshipError) as exc:
        raise DataStewardshipError(
            f"cannot read reviewed artifact {artifact_path}: {exc}"
        ) from exc
    if not isinstance(artifact, dict):
        raise DataStewardshipError("reviewed artifact must be a JSON object")
    if artifact.get("schema") != REVIEWED_OUTBOUND_SCHEMA:
        raise DataStewardshipError(
            f"unsupported reviewed artifact schema: {artifact.get('schema')!r}"
        )
    _safe_name(artifact.get("artifact_id"), "artifact_id")
    task = artifact.get("task")
    if task not in TASK_LABELS:
        raise DataStewardshipError(f"unsupported classification task: {task!r}")

    source = artifact.get("source")
    if not isinstance(source, dict):
        raise DataStewardshipError("source must be an object")
    _nonempty_text(source.get("kind"), "source.kind")
    if source.get("live_source") is not False:
        raise DataStewardshipError("source.live_source must be exactly false")
    _nonempty_text(source.get("export_ref"), "source.export_ref")

    review = artifact.get("review")
    if not isinstance(review, dict) or review.get("state") != "approved":
        raise DataStewardshipError("outbound artifact review is not approved")
    if review.get("purpose") != REVIEW_PURPOSE:
        raise DataStewardshipError(f"review.purpose must be {REVIEW_PURPOSE!r}")
    for field in ("reviewed_by", "reviewed_at", "review_ref"):
        _nonempty_text(review.get(field), f"review.{field}")

    limitations = artifact.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ):
        raise DataStewardshipError("limitations must be a non-empty text array")
    records = artifact.get("records")
    if not isinstance(records, list) or not records:
        raise DataStewardshipError("records must be a non-empty list")
    for index, record in enumerate(records):
        _validate_chat_record(record, f"records[{index}]")
        target = extract_classification_target(record)
        if target not in TASK_LABELS[task]:
            raise DataStewardshipError(
                f"records[{index}] target {target!r} is not valid for {task}"
            )
    declared_digest = artifact.get("records_sha256")
    if not isinstance(declared_digest, str) or not SHA256_RE.fullmatch(declared_digest):
        raise DataStewardshipError("records_sha256 must be a prefixed lowercase SHA-256")
    actual_digest = records_sha256(records)
    if actual_digest != declared_digest:
        raise DataStewardshipError("reviewed artifact record digest does not match")
    return {
        "artifact": artifact,
        "artifact_path": artifact_path,
        "artifact_sha256": file_sha256(artifact_path),
        "records_bytes": _records_bytes(records),
    }


def extract_classification_target(record):
    """Extract an exact task label from the final assistant message."""
    _validate_chat_record(record)
    return record["messages"][-1]["content"].strip()


def classification_metrics(expected, predicted, labels=None):
    """Compute deterministic exact-match and macro classification metrics."""
    expected = [str(value).strip() for value in expected]
    predicted = [str(value).strip() for value in predicted]
    if not expected:
        raise ValueError("at least one classification example is required")
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    label_order = list(labels) if labels is not None else sorted(set(expected + predicted))
    if not label_order or len(label_order) != len(set(label_order)):
        raise ValueError("labels must be a non-empty unique sequence")
    if any(label not in label_order for label in expected):
        raise ValueError("expected contains a target outside the declared labels")

    unexpected = sorted(set(predicted) - set(label_order))
    columns = label_order + (["__other__"] if unexpected else [])
    confusion = {
        actual: {candidate: 0 for candidate in columns} for actual in label_order
    }
    correct = 0
    for actual, guess in zip(expected, predicted):
        column = guess if guess in label_order else "__other__"
        confusion[actual][column] += 1
        if actual == guess:
            correct += 1

    per_class = {}
    for label in label_order:
        true_positive = confusion[label][label]
        false_negative = sum(confusion[label].values()) - true_positive
        false_positive = sum(
            confusion[actual][label] for actual in label_order if actual != label
        )
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        precision = (
            true_positive / precision_denominator if precision_denominator else 0.0
        )
        recall = true_positive / recall_denominator if recall_denominator else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": recall_denominator,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        }

    return {
        "examples": len(expected),
        "correct": correct,
        "accuracy": correct / len(expected),
        "macro_precision": sum(item["precision"] for item in per_class.values())
        / len(label_order),
        "macro_recall": sum(item["recall"] for item in per_class.values())
        / len(label_order),
        "macro_f1": sum(item["f1"] for item in per_class.values())
        / len(label_order),
        "labels": label_order,
        "unexpected_predictions": unexpected,
        "confusion_matrix": confusion,
        "per_class": per_class,
    }


def evaluate_task_predictions(task, records, predictions):
    """Evaluate exact labels for one KINGDOM task without requiring MLX."""
    if task not in TASK_LABELS:
        raise ValueError(f"unknown task: {task}")
    expected = [extract_classification_target(record) for record in records]
    metrics = classification_metrics(expected, predictions, TASK_LABELS[task])
    return {"task": task, **metrics}


def load_jsonl_records(path):
    records = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = _decode_json(line, f"JSONL line {line_number}")
            except DataStewardshipError:
                raise
            records.append(_validate_chat_record(record, f"line {line_number}"))
    if not records:
        raise DataStewardshipError("dataset is empty")
    return records


def dataset_stats(path):
    """Compute class and provenance counts without guessing missing provenance."""
    path = Path(path)
    if not path.exists():
        return {"total": 0, "error": "not found"}

    total = 0
    classes = {}
    synthetic = 0
    harvested = 0
    reviewed_outbound = 0
    unknown = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = _decode_json(line.strip(), "dataset statistics record")
            except DataStewardshipError:
                continue
            total += 1
            messages = entry.get("messages", [])
            if messages and isinstance(messages[-1], dict):
                answer = messages[-1].get("content", "?")
                classes[answer] = classes.get(answer, 0) + 1
            source = entry.get("source")
            if source in {"synthetic", "model-generated", "model-generated-unreviewed"}:
                synthetic += 1
            elif source == "harvest":
                harvested += 1
            elif source == "reviewed-outbound":
                reviewed_outbound += 1
            else:
                unknown += 1

    return {
        "total": total,
        "synthetic": synthetic,
        "harvested": harvested,
        "reviewed_outbound": reviewed_outbound,
        "unknown_provenance": unknown,
        "classes": classes,
    }


def cmd_generate(args):
    template_path = TEMPLATES_DIR / f"{args.task}.txt"
    if not template_path.exists():
        raise DataStewardshipError(f"template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    system_prompt = SYSTEM_PROMPTS[args.task]
    batch_size = 50
    batches = (args.count + batch_size - 1) // batch_size
    if args.count <= 0:
        raise DataStewardshipError("--count must be positive")

    print(f"Generating {args.count} unreviewed examples for {args.task!r}")
    if not args.yes:
        confirm = input("Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
    candidate_name = _safe_name(args.candidate, "candidate")
    CANDIDATE_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CANDIDATE_DATASETS_DIR / f"{candidate_name}.jsonl"
    receipt_path = output_path.with_suffix(".generation-receipt.json")
    if output_path.exists() or receipt_path.exists():
        raise DataStewardshipError(
            f"candidate already exists; choose a new versioned name: {candidate_name}"
        )
    collected = []
    for batch in range(batches):
        remaining = min(batch_size, args.count - len(collected))
        prompt = template.replace("{COUNT}", str(remaining)).replace(
            "{SYSTEM_PROMPT}", system_prompt
        )
        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print(f"  Batch {batch + 1} timed out", file=sys.stderr)
            continue
        except FileNotFoundError as exc:
            raise DataStewardshipError("'claude' CLI not found") from exc
        if result.returncode != 0:
            print(f"  Batch {batch + 1} failed", file=sys.stderr)
            continue
        examples = parse_generated_examples(result.stdout)
        for example in examples[:remaining]:
            example["source"] = "model-generated-unreviewed"
            collected.append(example)
        print(
            f"  Batch {batch + 1}: {min(len(examples), remaining)} examples "
            f"(total: {len(collected)})"
        )
    if len(collected) != args.count:
        raise DataStewardshipError(
            f"generator returned {len(collected)} of {args.count} requested records; "
            "no candidate was written"
        )
    payload = _records_bytes(collected)
    output_path.write_bytes(payload)
    receipt = {
        "schema": "kingdom.unreviewed-generation-receipt/v1",
        "candidate": candidate_name,
        "task": args.task,
        "records": len(collected),
        "output": str(output_path.relative_to(LOVE_ROOT)),
        "output_sha256": file_sha256(output_path),
        "source": "claude-cli-model-output",
        "review_state": "unreviewed",
        "training_authorized": False,
        "boundary": (
            "Generation records mechanics only. It does not establish rights, "
            "quality, reviewer authority, or permission to train."
        ),
    }
    receipt_path.write_bytes(_canonical_bytes(receipt) + b"\n")
    print(f"Generated {len(collected)} unreviewed examples -> {output_path}")
    print(f"Generation receipt -> {receipt_path}")
    print("A reviewed outbound artifact and local-training manifest are still required.")


def cmd_harvest(_args):
    raise DataStewardshipError(
        "direct operational-history harvest is disabled; use import-reviewed "
        "with a separately exported and approved outbound artifact"
    )


def cmd_import_reviewed(args):
    loaded = load_reviewed_outbound_artifact(args.artifact)
    artifact = loaded["artifact"]
    REVIEWED_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REVIEWED_DATASETS_DIR / f"{artifact['artifact_id']}.jsonl"
    if output_path.exists() and output_path.read_bytes() != loaded["records_bytes"]:
        raise DataStewardshipError(
            f"refusing to overwrite different reviewed data: {output_path}"
        )
    output_path.write_bytes(loaded["records_bytes"])
    receipt = {
        "schema": REVIEWED_IMPORT_RECEIPT_SCHEMA,
        "artifact_id": artifact["artifact_id"],
        "artifact_sha256": loaded["artifact_sha256"],
        "task": artifact["task"],
        "output": str(output_path.relative_to(LOVE_ROOT)),
        "output_sha256": file_sha256(output_path),
        "records": len(artifact["records"]),
        "training_authorized": False,
        "boundary": (
            "Import preserves reviewed outbound data but does not authorize training. "
            "A separate immutable local-training manifest is required."
        ),
    }
    receipt_path = output_path.with_suffix(".import-receipt.json")
    receipt_path.write_bytes(_canonical_bytes(receipt) + b"\n")
    print(f"Imported reviewed artifact -> {output_path}")
    print(f"Import receipt -> {receipt_path}")


def cmd_evaluate(args):
    records = load_jsonl_records(args.dataset)
    with Path(args.predictions).open(encoding="utf-8") as handle:
        predictions = [line.strip() for line in handle if line.strip()]
    print(
        json.dumps(
            evaluate_task_predictions(args.task, records, predictions),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def cmd_stats(_args):
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    datasets = list(DATASETS_DIR.glob("*.jsonl"))
    if REVIEWED_DATASETS_DIR.exists():
        datasets.extend(REVIEWED_DATASETS_DIR.glob("*.jsonl"))
    if CANDIDATE_DATASETS_DIR.exists():
        datasets.extend(CANDIDATE_DATASETS_DIR.glob("*.jsonl"))
    if not datasets:
        print("No datasets.")
        return
    print("Dataset Statistics:")
    for dataset in sorted(datasets):
        stats = dataset_stats(dataset)
        print(f"\n  {dataset.relative_to(MLX_DIR)}:")
        for field in (
            "total",
            "synthetic",
            "harvested",
            "reviewed_outbound",
            "unknown_provenance",
        ):
            print(f"    {field}: {stats.get(field, 0)}")
        for label, count in sorted(stats.get("classes", {}).items()):
            print(f"      {label}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Stewarded MLX training data tools")
    sub = parser.add_subparsers(dest="command")

    generate = sub.add_parser("generate", help="Generate unreviewed synthetic data")
    generate.add_argument("--task", required=True, choices=TASK_TYPES)
    generate.add_argument(
        "--candidate",
        required=True,
        help="new versioned candidate name; existing candidates are never appended",
    )
    generate.add_argument("--count", type=int, default=200)
    generate.add_argument("--yes", action="store_true", help="Skip confirmation")

    harvest = sub.add_parser(
        "harvest", help="Disabled compatibility command; never reads live history"
    )
    harvest.add_argument("--source", required=True)

    reviewed = sub.add_parser(
        "import-reviewed", help="Import a reviewed outbound artifact"
    )
    reviewed.add_argument("--artifact", required=True)

    evaluate = sub.add_parser(
        "evaluate", help="Evaluate exact task labels without importing MLX"
    )
    evaluate.add_argument("--task", required=True, choices=TASK_TYPES)
    evaluate.add_argument("--dataset", required=True)
    evaluate.add_argument("--predictions", required=True)

    sub.add_parser("stats", help="Show dataset statistics")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    try:
        {
            "generate": cmd_generate,
            "harvest": cmd_harvest,
            "import-reviewed": cmd_import_reviewed,
            "evaluate": cmd_evaluate,
            "stats": cmd_stats,
        }[args.command](args)
    except (DataStewardshipError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
