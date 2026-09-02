"""Stewardship boundary tests for MLX data, training, and receipts."""

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import mlx_data
import mlx_train


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MANIFEST = ROOT / "resources" / "data" / "manifests" / "legacy-mlx-v0.json"


def _single_run_dir(runs):
    matches = [
        path for path in runs.iterdir() if (path / "run-plan.json").is_file()
    ]
    assert len(matches) == 1
    return matches[0]


def _record(label, prompt="route this"):
    return {
        "messages": [
            {"role": "system", "content": "classify"},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": label},
        ]
    }


def _write_jsonl(path, labels):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(_record(label), sort_keys=True) + "\n" for label in labels),
        encoding="utf-8",
    )


def _split_entry(root, relative_path, lane):
    path = root / relative_path
    validation = mlx_train.validate_dataset(path)
    assert validation["valid"]
    return {
        "path": relative_path.as_posix(),
        "format": "jsonl",
        "lane": lane,
        "sha256": mlx_train.file_sha256(path),
        "bytes": path.stat().st_size,
        "records": validation["count"],
    }


def _passed_assessment(label):
    return {
        "state": "pass",
        "method": f"test-only {label} review",
        "artifact_ref": f"test-evidence/{label}.json",
        "artifact_digest": "sha256:" + "c" * 64,
        "reviewed_at": "2026-09-02T09:00:00Z",
        "reviewer": "test-reviewer",
        "findings": [],
        "notes": ["Synthetic test fixture evidence."],
    }


def _source_manifest(tmp_path, split_entries):
    manifest = copy.deepcopy(json.loads(LEGACY_MANIFEST.read_text()))
    manifest.update(
        manifest_id="kingdom/demo@1",
        dataset_id="kingdom/demo",
        version="1",
        status="approved",
        description="Synthetic test-only approved dataset.",
    )
    manifest["source_provenance"].update(
        {"class": "synthetic", "evidence": ["test-only generator receipt"]}
    )
    manifest["licenses"].update(
        {
            "state": "verified",
            "dataset_license": "Apache-2.0",
            "components_complete": True,
            "evidence": ["test-only license review"],
        }
    )
    manifest["consent_and_reuse"].update(
        {
            "state": "verified",
            "basis": "project-authored",
            "scope": [mlx_train.LOCAL_TRAINING_PURPOSE],
            "evidence": ["test-only reuse review"],
        }
    )
    manifest["privacy"]["classification"] = "public"
    for name in ("privacy_review", "pii_review", "secret_review"):
        manifest["privacy"][name] = _passed_assessment(name)
    manifest["retention"].update(
        {
            "state": "defined",
            "policy": "Retain while this test fixture is active.",
            "owner": "test-steward",
            "review_at": "2027-09-02T00:00:00Z",
        }
    )
    manifest["withdrawal"].update(
        {
            "state": "defined",
            "process": "Record and review future-use removal requests.",
            "owner": "test-steward",
            "limitations": ["Downloaded copies cannot be recalled."],
        }
    )
    manifest["feedback"].update(
        {
            "presence": "absent",
            "nature": "none",
            "rubric": {
                "state": "not-applicable",
                "description": "No feedback is present.",
                "artifact_digest": None,
            },
            "ties": {"policy": "not-applicable", "description": "No ties."},
            "uncertainty": {
                "representation": "not-applicable",
                "description": "No feedback uncertainty.",
            },
            "aggregation": {
                "method": "not-applicable",
                "minority_preservation": "not-applicable",
                "description": "No aggregation.",
            },
        }
    )
    for name in ("split", "deduplication", "contamination"):
        manifest["partitioning"][name] = _passed_assessment(name)
    manifest["huggingface"].update(
        {
            "repo_id": "kingdom/test-only-demo",
            "visibility": "public",
            "config_names": ["default"],
            "default_config": "default",
            "splits": ["train", "validation"],
            "revision": None,
            "access_policy": None,
        }
    )
    manifest["files"] = []
    for split_name, entry in split_entries.items():
        manifest["files"].append({**entry, "config": "default", "split": split_name})
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
            "reviewed_at": "2026-09-02T10:00:00Z",
            "reviewers": ["test-reviewer"],
            "blockers": [],
        }
    )
    manifest["publication_eligible"] = True
    path = tmp_path / "source-dataset-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path, manifest


def _training_manifest(tmp_path, permitted=True):
    train_path = Path("reviewed/train.jsonl")
    validation_path = Path("reviewed/validation.jsonl")
    _write_jsonl(tmp_path / train_path, ["alpha", "beta", "gamma"])
    _write_jsonl(tmp_path / validation_path, ["alpha", "nuance"])
    splits = {
        "train": _split_entry(tmp_path, train_path, "training"),
        "validation": _split_entry(tmp_path, validation_path, "validation"),
    }
    source_path, source = _source_manifest(tmp_path, splits)
    manifest = {
        "schema": mlx_train.TRAINING_MANIFEST_SCHEMA,
        "manifest_id": "kingdom/demo@1",
        "dataset_id": "kingdom/demo",
        "version": "1",
        "artifact_class": mlx_train.REVIEWED_ARTIFACT_CLASS,
        "source_dataset_manifest": {
            "path": source_path.relative_to(tmp_path).as_posix(),
            "sha256": mlx_train.file_sha256(source_path),
            "manifest_id": source["manifest_id"],
        },
        "local_training": {
            "permitted": permitted,
            "purpose": mlx_train.LOCAL_TRAINING_PURPOSE,
            "review": {
                "state": "approved",
                "reviewed_by": "test-reviewer",
                "reviewed_at": "2026-09-02T10:00:00Z",
                "review_ref": "review:test-only",
            },
        },
        "splits": splits,
        "limitations": ["Synthetic test-only fixture."],
    }
    manifest_path = tmp_path / "training-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path, manifest


def _model_resolution():
    return {
        "path": "/verified/cache/model",
        "repo_id": "mlx-community/example",
        "revision": "a" * 40,
        # hf_model_lock currently emits an unprefixed digest; plans normalize it.
        "lock_sha256": "b" * 64,
    }


def _lora_config():
    return {
        "schema": mlx_train.LORA_CONFIG_SCHEMA,
        "fine_tune_type": "lora",
        "num_layers": 8,
        "lora_parameters": {
            "rank": 8,
            "scale": 2.0,
            "dropout": 0.05,
            "keys": ["self_attn.q_proj", "self_attn.v_proj"],
        },
        "learning_rate": 0.0001,
        "batch_size": 2,
        "epochs": 3,
        "seed": 42,
        "mask_prompt": True,
        "max_seq_length": 512,
    }


def _runtime_environment():
    return {
        "python": "3.12.12",
        "packages": {"mlx": "0.32.2", "mlx-lm": "0.31.3"},
        "module_origins": {
            "mlx": "/venv/site-packages/mlx/__init__.py",
            "mlx_lm": "/venv/site-packages/mlx_lm/__init__.py",
        },
        "requirements_lock_sha256": "sha256:" + "e" * 64,
    }


def _target_preflight(dataset, config=None):
    config = config or _lora_config()
    return {
        "method": mlx_train.TARGET_PREFLIGHT_METHOD,
        "max_seq_length": config["max_seq_length"],
        "splits": {
            name: {
                "records": entry["records"],
                "min_supervised_tokens": 2,
                "max_raw_tokens": 12,
                "truncated_records": 0,
            }
            for name, entry in dataset["splits"].items()
        },
    }


def _finite_weight_inspection():
    return {
        "tensor_count": 4,
        "all_finite": True,
        "floating_tensors": True,
        "inventory_matches_expected": True,
        "tensor_inventory_sha256": "sha256:" + "f" * 64,
        "target_key_tensor_counts": {
            "self_attn.q_proj": 2,
            "self_attn.v_proj": 2,
        },
        "inspection_method": mlx_train.WEIGHT_INSPECTION_METHOD,
    }


def _mlx_adapter_config(plan, adapter_path, run_dir=None, model_path=None):
    adapter_path = Path(adapter_path).resolve()
    run_dir = Path(run_dir or (mlx_train.RUNS_DIR / plan["plan_id"])).resolve()
    return {
        **copy.deepcopy(mlx_train.PINNED_MLX_LM_DEFAULTS),
        **copy.deepcopy(plan["launcher"]["effective_config"]),
        "model": str(Path(model_path or _model_resolution()["path"]).resolve()),
        "train": True,
        "data": str(run_dir / "data"),
        "iters": plan["launcher"]["iterations"],
        "adapter_path": str(adapter_path),
        "config": str(run_dir / "mlx-lm-config.json"),
    }


def test_training_manifest_requires_explicit_permission(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path, permitted=False)
    with pytest.raises(mlx_train.TrainingStewardshipError, match="does not permit"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


@pytest.mark.parametrize("field", ["prompt", "completion", "text", "tools", "revoked"])
def test_training_dataset_rejects_alternate_dispatch_and_control_fields(
    tmp_path, field
):
    record = _record("alpha")
    record[field] = "alternate"
    path = tmp_path / "dispatch.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = mlx_train.validate_dataset(path)

    assert result["valid"] is False
    assert "unsupported training record field" in result["error"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("final-user", "end with an assistant target"),
        ("message-tools", "unsupported field"),
        ("empty-target", "content must be non-empty"),
        ("no-user", "contain a user prompt"),
    ],
)
def test_training_dataset_requires_an_unambiguous_supervised_chat_target(
    tmp_path, mutation, message
):
    record = _record("alpha")
    if mutation == "final-user":
        record["messages"][-1]["role"] = "user"
    elif mutation == "message-tools":
        record["messages"][-1]["tool_calls"] = []
    elif mutation == "empty-target":
        record["messages"][-1]["content"] = "  "
    else:
        record["messages"] = [
            {"role": "system", "content": "classify"},
            {"role": "assistant", "content": "alpha"},
        ]
    path = tmp_path / "target.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = mlx_train.validate_dataset(path)

    assert result["valid"] is False
    assert message in result["error"]


def test_training_manifest_requires_approved_review(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    manifest["local_training"]["review"]["state"] = "hold"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="not approved"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


@pytest.mark.parametrize(
    "target",
    ("manifest", "permission", "review", "split"),
)
def test_training_manifest_rejects_unknown_or_revoked_fields(tmp_path, target):
    manifest_path, manifest = _training_manifest(tmp_path)
    if target == "manifest":
        manifest["revoked"] = True
    elif target == "permission":
        manifest["local_training"]["revoked"] = True
    elif target == "review":
        manifest["local_training"]["review"]["revoked"] = True
    else:
        manifest["splits"]["train"]["revoked"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unknown revoked"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_training_manifest_verifies_immutable_declared_splits(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    loaded = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    assert loaded["manifest"]["manifest_id"] == "kingdom/demo@1"
    assert loaded["splits"]["train"]["records"] == 3
    assert (
        loaded["splits"]["validation"]["sha256"]
        == manifest["splits"]["validation"]["sha256"]
    )

    with (tmp_path / manifest["splits"]["train"]["path"]).open("a") as handle:
        handle.write(json.dumps(_record("alpha")) + "\n")
    with pytest.raises(
        mlx_train.TrainingStewardshipError,
        match="digest mismatch|byte count does not match",
    ):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_training_manifest_rejects_runtime_style_or_operational_splits(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    manifest["splits"]["source-unsplit"] = manifest["splits"]["train"].copy()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unsupported split"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)

    live_path = Path("coordination/delegate/history.jsonl")
    _write_jsonl(tmp_path / live_path, ["alpha"])
    manifest["splits"].pop("source-unsplit")
    manifest["splits"]["train"] = _split_entry(tmp_path, live_path, "training")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="operational-history"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_training_manifest_rejects_symlinked_operational_target(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    target = tmp_path / "coordination" / "delegate" / "history.jsonl"
    _write_jsonl(target, ["alpha", "beta", "gamma"])
    train = tmp_path / manifest["splits"]["train"]["path"]
    train.unlink()
    train.symlink_to(target)
    with pytest.raises(mlx_train.TrainingStewardshipError, match="symlink"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_training_manifest_requires_canonical_reuse_scope(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    source_path = tmp_path / manifest["source_dataset_manifest"]["path"]
    source = json.loads(source_path.read_text())
    source["consent_and_reuse"]["scope"] = ["evaluation-only"]
    source_path.write_text(json.dumps(source, indent=2) + "\n")
    manifest["source_dataset_manifest"]["sha256"] = mlx_train.file_sha256(source_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="reuse scope"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_training_manifest_requires_prefixed_source_manifest_digest(tmp_path):
    manifest_path, manifest = _training_manifest(tmp_path)
    manifest["source_dataset_manifest"]["sha256"] = manifest["source_dataset_manifest"][
        "sha256"
    ].removeprefix("sha256:")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(mlx_train.TrainingStewardshipError, match="prefixed lowercase"):
        mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)


def test_run_fails_before_model_resolution_without_manifest():
    args = argparse.Namespace(manifest=None, dataset="all", epochs=None, adapter=None)
    with mock.patch.object(mlx_train, "resolve_training_model") as resolver:
        with pytest.raises(
            mlx_train.TrainingStewardshipError, match="requires --manifest"
        ):
            mlx_train.cmd_run(args)
    resolver.assert_not_called()


def test_lora_config_rejects_duplicate_and_nonfinite_values(tmp_path, monkeypatch):
    path = tmp_path / "lora.json"
    monkeypatch.setattr(mlx_train, "LORA_CONFIG", path)
    path.write_text('{"schema":"first","schema":"second"}\n')
    with pytest.raises(mlx_train.TrainingStewardshipError, match="duplicate key"):
        mlx_train.load_lora_config()

    config = _lora_config()
    path.write_text(json.dumps({**config, "learning_rate": float("inf")}))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="non-finite"):
        mlx_train.load_lora_config()


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        (["q_proj", "self_attn.q_proj"], "full layer-relative"),
        (
            ["attn.q_proj", "self.attn.q_proj"],
            "suffix-overlapping",
        ),
    ],
)
def test_lora_config_rejects_ignored_or_overlapping_target_keys(
    tmp_path, monkeypatch, keys, message
):
    path = tmp_path / "lora.json"
    monkeypatch.setattr(mlx_train, "LORA_CONFIG", path)
    config = _lora_config()
    config["lora_parameters"]["keys"] = keys
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(mlx_train.TrainingStewardshipError, match=message):
        mlx_train.load_lora_config()


def test_run_plan_and_prelaunch_receipt_are_deterministic(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    first = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        _target_preflight(dataset),
    )
    second = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        _target_preflight(dataset),
    )
    assert first == second
    # mlx-lm==0.31.3 drops the incomplete tail on every epoch: floor(3 / 2) * 3.
    assert first["launcher"]["iterations"] == 3
    assert first["model"]["lock_sha256"] == "sha256:" + "b" * 64

    one = tmp_path / "receipt-one"
    two = tmp_path / "receipt-two"
    mlx_train.write_prelaunch_receipts(one, first)
    mlx_train.write_prelaunch_receipts(two, second)
    assert (one / "run-plan.json").read_bytes() == (two / "run-plan.json").read_bytes()
    assert (one / "run-receipt.json").read_bytes() == (
        two / "run-receipt.json"
    ).read_bytes()
    assert (one / "mlx-lm-config.json").read_bytes() == (
        two / "mlx-lm-config.json"
    ).read_bytes()
    receipt = json.loads((one / "run-receipt.json").read_text())
    assert receipt["state"] == "planned"
    assert receipt["training_launched"] is False


def test_run_plan_preflights_every_split_for_complete_batches(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    config = _lora_config()
    config["batch_size"] = 3
    with pytest.raises(
        mlx_train.TrainingStewardshipError,
        match="split validation has 2 records.*batch_size is 3",
    ):
        mlx_train.build_run_plan(
            dataset,
            _model_resolution(),
            config,
            _runtime_environment(),
            _target_preflight(dataset, config),
        )


def test_runtime_binding_requires_exact_python_and_top_level_keys():
    wrong_python = {**_runtime_environment(), "python": "3.12.11"}
    with pytest.raises(mlx_train.TrainingStewardshipError, match="3.12.12"):
        mlx_train._validate_runtime_environment(wrong_python)

    revoked = {**_runtime_environment(), "revoked": True}
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unknown revoked"):
        mlx_train._validate_runtime_environment(revoked)


def test_environment_resolver_rejects_unpinned_lock_lines(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("mlx==0.32.2\nmlx-lm==0.31.3\nnumpy>=2\n")
    monkeypatch.setattr(mlx_train, "REQUIREMENTS_LOCK", lock)
    with mock.patch.object(mlx_train.subprocess, "run") as probe:
        with pytest.raises(
            mlx_train.TrainingStewardshipError, match="only exact version pins"
        ):
            mlx_train.resolve_training_environment(sys.executable)
    probe.assert_not_called()


def test_environment_resolver_rejects_extra_installed_distributions(
    tmp_path, monkeypatch
):
    lock = tmp_path / "requirements.lock"
    lock.write_text("mlx==0.32.2\nmlx-lm==0.31.3\n")
    monkeypatch.setattr(mlx_train, "REQUIREMENTS_LOCK", lock)
    probe_result = argparse.Namespace(
        returncode=0,
        stdout=json.dumps(
            {
                "python": "3.12.12",
                "packages": {
                    "mlx": "0.32.2",
                    "mlx-lm": "0.31.3",
                    "unlocked-plugin": "1.0",
                },
                "duplicates": [],
                "module_origins": {
                    "mlx": "/venv/site-packages/mlx/__init__.py",
                    "mlx_lm": "/venv/site-packages/mlx_lm/__init__.py",
                },
                "origin_errors": [],
            }
        ),
        stderr="",
    )
    monkeypatch.setattr(
        mlx_train.subprocess, "run", lambda *_args, **_kwargs: probe_result
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="does not match"):
        mlx_train.resolve_training_environment(sys.executable)


def test_environment_resolver_rejects_shadowed_mlx_module(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("mlx==0.32.2\nmlx-lm==0.31.3\n")
    monkeypatch.setattr(mlx_train, "REQUIREMENTS_LOCK", lock)
    probe_result = argparse.Namespace(
        returncode=0,
        stdout=json.dumps(
            {
                "python": "3.12.12",
                "packages": {"mlx": "0.32.2", "mlx-lm": "0.31.3"},
                "duplicates": [],
                "module_origins": {
                    "mlx": "/venv/site-packages/mlx/core.so",
                },
                "origin_errors": ["mlx_lm"],
            }
        ),
        stderr="",
    )
    monkeypatch.setattr(
        mlx_train.subprocess, "run", lambda *_args, **_kwargs: probe_result
    )
    with pytest.raises(
        mlx_train.TrainingStewardshipError, match="owned by their pinned distributions"
    ):
        mlx_train.resolve_training_environment(sys.executable)


def _probe_result(payload, returncode=0):
    return argparse.Namespace(
        returncode=returncode,
        stdout=(
            mlx_train.PROBE_RESULT_PREFIX
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
            + "\n"
        ),
        stderr="",
    )


def test_target_preflight_uses_pinned_chat_dispatch_and_binds_every_split(
    tmp_path, monkeypatch
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    summaries = _target_preflight(dataset)["splits"]

    def fake_run(command, **kwargs):
        assert command[:2] == [sys.executable, "-I"]
        assert "ChatDataset" in command[3]
        assert '{"trust_remote_code": True}' in command[3]
        request = json.loads(kwargs["input"])
        assert request["mask_prompt"] is True
        assert set(request["splits"]) == {"train", "validation"}
        return _probe_result(
            {
                "splits": summaries,
                "truncated_targets": [],
                "zero_targets": [],
            }
        )

    monkeypatch.setattr(mlx_train.subprocess, "run", fake_run)
    result = mlx_train.preflight_supervised_targets(
        dataset,
        _model_resolution(),
        _lora_config(),
        python_executable=sys.executable,
    )

    assert result == _target_preflight(dataset)


@pytest.mark.parametrize(
    ("payload_updates", "message"),
    [
        (
            {
                "truncated_targets": [{"record": 1, "split": "train"}],
                "zero_targets": [],
            },
            "split train record 1 exceeds max_seq_length=512; target truncation is forbidden",
        ),
        (
            {
                "truncated_targets": [],
                "zero_targets": [{"record": 1, "split": "train"}],
            },
            "split train record 1 has no supervised target tokens",
        ),
    ],
)
def test_target_preflight_rejects_partial_or_zero_supervised_targets(
    tmp_path, monkeypatch, payload_updates, message
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    summaries = _target_preflight(dataset)["splits"]
    payload = {"splits": summaries, **payload_updates}
    monkeypatch.setattr(
        mlx_train.subprocess,
        "run",
        lambda *_args, **_kwargs: _probe_result(payload),
    )

    with pytest.raises(mlx_train.TrainingStewardshipError, match=re.escape(message)):
        mlx_train.preflight_supervised_targets(
            dataset,
            _model_resolution(),
            _lora_config(),
            python_executable=sys.executable,
        )


def test_weight_inspection_binds_locked_model_conversion_inventory(
    tmp_path, monkeypatch
):
    weights = tmp_path / "adapters.safetensors"
    weights.write_bytes(b"test-only-safetensors")
    model = tmp_path / "model"
    model.mkdir()

    def fake_run(command, **kwargs):
        assert command[:2] == [sys.executable, "-I"]
        assert "load_model" in command[3]
        assert "linear_to_lora_layers" in command[3]
        request = json.loads(kwargs["input"])
        assert request["model_path"] == str(model)
        assert request["training_config"] == _lora_config()
        return _probe_result(_finite_weight_inspection())

    monkeypatch.setattr(mlx_train.subprocess, "run", fake_run)
    result = mlx_train.inspect_adapter_weights(
        weights,
        model,
        _lora_config(),
        python_executable=sys.executable,
    )

    assert result == _finite_weight_inspection()


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("uv") is None,
    reason="real pinned MLX runtime integration requires Apple silicon and uv",
)
def test_real_pinned_runtime_rejects_wrong_floating_dtype(tmp_path):
    script = r"""
import json
import sys
from pathlib import Path

import mlx.core as mx
from mlx.utils import tree_flatten
from mlx_lm.models.llama import Model, ModelArgs
from mlx_lm.tuner import linear_to_lora_layers

sys.path.insert(0, sys.argv[1])
import mlx_train

fixture = Path(sys.argv[2])
model_dir = fixture / "model"
model_dir.mkdir()
args = ModelArgs(
    model_type="llama",
    hidden_size=8,
    num_hidden_layers=1,
    intermediate_size=16,
    num_attention_heads=2,
    rms_norm_eps=1e-5,
    vocab_size=16,
    max_position_embeddings=64,
    tie_word_embeddings=True,
)
(model_dir / "config.json").write_text(json.dumps(args.__dict__))
base = Model(args)
mx.save_safetensors(
    str(model_dir / "model.safetensors"),
    dict(tree_flatten(base.parameters())),
)
base.freeze()
parameters = {
    "rank": 2,
    "scale": 1.0,
    "dropout": 0.0,
    "keys": ["self_attn.q_proj"],
}
linear_to_lora_layers(base, 1, parameters, use_dora=False)
trainable = dict(tree_flatten(base.trainable_parameters()))
weights = fixture / "wrong-dtype.safetensors"
mx.save_safetensors(
    str(weights),
    {name: tensor.astype(mx.float16) for name, tensor in trainable.items()},
)
config = {
    "schema": mlx_train.LORA_CONFIG_SCHEMA,
    "fine_tune_type": "lora",
    "num_layers": 1,
    "lora_parameters": parameters,
    "learning_rate": 0.0001,
    "batch_size": 1,
    "epochs": 1,
    "seed": 42,
    "mask_prompt": True,
    "max_seq_length": 64,
}
try:
    mlx_train.inspect_adapter_weights(
        weights,
        model_dir,
        config,
        python_executable=sys.executable,
    )
except mlx_train.TrainingStewardshipError as exc:
    if "names, shapes, or dtypes" not in str(exc):
        raise
    print("WRONG_DTYPE_REJECTED")
else:
    raise AssertionError("wrong floating dtype was accepted")
"""
    result = subprocess.run(
        [
            shutil.which("uv"),
            "run",
            "--isolated",
            "--python",
            "3.12.12",
            "--with",
            "mlx==0.32.2",
            "--with",
            "mlx-lm==0.31.3",
            "python",
            "-I",
            "-c",
            script,
            str(ROOT / "tools"),
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "WRONG_DTYPE_REJECTED" in result.stdout


@pytest.mark.parametrize(
    ("inspection", "message"),
    [
        ({**_finite_weight_inspection(), "all_finite": False}, "non-finite"),
        (
            {**_finite_weight_inspection(), "floating_tensors": False},
            "non-floating",
        ),
        (
            {**_finite_weight_inspection(), "inventory_matches_expected": False},
            "tensor inventory differs",
        ),
        (
            {
                **_finite_weight_inspection(),
                "target_key_tensor_counts": {
                    "self_attn.q_proj": 2,
                    "self_attn.v_proj": 0,
                },
            },
            "no paired LoRA tensors.*v_proj",
        ),
    ],
)
def test_weight_inspection_rejects_nonfinite_unpaired_or_missing_targets(
    tmp_path, monkeypatch, inspection, message
):
    weights = tmp_path / "adapters.safetensors"
    weights.write_bytes(b"test-only-safetensors")
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(
        mlx_train.subprocess,
        "run",
        lambda *_args, **_kwargs: _probe_result(inspection),
    )

    with pytest.raises(mlx_train.TrainingStewardshipError, match=message):
        mlx_train.inspect_adapter_weights(
            weights,
            model,
            _lora_config(),
            python_executable=sys.executable,
        )


def test_weight_inspection_rejects_corrupt_or_mutated_files(tmp_path, monkeypatch):
    weights = tmp_path / "adapters.safetensors"
    weights.write_bytes(b"test-only-safetensors")
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(
        mlx_train.subprocess,
        "run",
        lambda *_args, **_kwargs: _probe_result({}, returncode=1),
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="pinned MLX"):
        mlx_train.inspect_adapter_weights(
            weights,
            model,
            _lora_config(),
            python_executable=sys.executable,
        )

    def mutating_probe(*_args, **_kwargs):
        weights.write_bytes(b"changed-during-inspection")
        return _probe_result(_finite_weight_inspection())

    monkeypatch.setattr(mlx_train.subprocess, "run", mutating_probe)
    with pytest.raises(mlx_train.TrainingStewardshipError, match="changed while"):
        mlx_train.inspect_adapter_weights(
            weights,
            model,
            _lora_config(),
            python_executable=sys.executable,
        )


def test_adapter_receipt_binds_weights_and_separates_approvals(
    tmp_path, monkeypatch
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        _target_preflight(dataset),
    )
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    with pytest.raises(mlx_train.TrainingStewardshipError, match="required weights"):
        mlx_train.build_adapter_receipt(
            adapter, plan, model_resolution=_model_resolution()
        )

    weights = adapter / mlx_train.ADAPTER_WEIGHTS_NAME
    weights.write_bytes(b"test-only-weights")
    adapter_config = _mlx_adapter_config(plan, adapter)
    (adapter / "adapter_config.json").write_text(json.dumps(adapter_config))
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )
    receipt = mlx_train.build_adapter_receipt(
        adapter, plan, model_resolution=_model_resolution()
    )
    assert receipt["weights"]["sha256"] == mlx_train.file_sha256(weights)
    assert receipt["weights"]["all_finite"] is True
    assert receipt["weights"]["inventory_matches_expected"] is True
    assert receipt["weights"]["target_key_tensor_counts"] == {
        "self_attn.q_proj": 2,
        "self_attn.v_proj": 2,
    }
    assert receipt["run_plan"]["model"]["revision"] == "a" * 40
    assert receipt["run_plan"]["dataset"]["splits"]["train"]["sha256"].startswith(
        "sha256:"
    )
    assert receipt["approvals"]["serving"]["state"] == "not-approved"
    assert receipt["approvals"]["release"]["state"] == "not-approved"


def test_adapter_validator_rejects_traversal_mismatch_and_unapproved_serving(
    tmp_path, monkeypatch
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        _target_preflight(dataset),
        "reviewed-adapter",
    )
    adapters = tmp_path / "adapters"
    adapter = adapters / "reviewed-adapter"
    adapter.mkdir(parents=True)
    (adapter / mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"bound-weights")
    (adapter / "adapter_config.json").write_text(
        json.dumps(_mlx_adapter_config(plan, adapter))
    )
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )
    receipt = mlx_train.build_adapter_receipt(
        adapter, plan, model_resolution=_model_resolution()
    )
    receipt_path = adapter / mlx_train.ADAPTER_RECEIPT_NAME
    receipt_path.write_text(json.dumps(receipt))

    validated = mlx_train.validate_adapter_artifact(
        "reviewed-adapter",
        model_resolution=_model_resolution(),
        dataset=dataset,
        runtime_environment=_runtime_environment(),
        adapters_root=adapters,
    )
    assert validated["serving_state"] == "not-approved"

    changed_inspection = copy.deepcopy(_finite_weight_inspection())
    changed_inspection["target_key_tensor_counts"]["self_attn.q_proj"] = 4
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: changed_inspection,
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="does not match"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )

    original_config = json.loads((adapter / "adapter_config.json").read_text())
    semantically_tampered = copy.deepcopy(original_config)
    semantically_tampered["lora_parameters"]["rank"] = 64
    (adapter / "adapter_config.json").write_text(json.dumps(semantically_tampered))
    tampered_receipt = copy.deepcopy(receipt)
    binding = tampered_receipt["artifact_files"]["adapter_config.json"]
    binding["sha256"] = mlx_train.file_sha256(adapter / "adapter_config.json")
    binding["bytes"] = (adapter / "adapter_config.json").stat().st_size
    receipt_path.write_text(json.dumps(tampered_receipt))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="lora_parameters"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )
    (adapter / "adapter_config.json").write_text(json.dumps(original_config))
    receipt_path.write_text(json.dumps(receipt))

    path_tampered = copy.deepcopy(original_config)
    unrelated_run = tmp_path / "unrelated" / plan["plan_id"]
    path_tampered["data"] = str(unrelated_run / "data")
    path_tampered["config"] = str(unrelated_run / "mlx-lm-config.json")
    (adapter / "adapter_config.json").write_text(json.dumps(path_tampered))
    path_receipt = copy.deepcopy(receipt)
    binding = path_receipt["artifact_files"]["adapter_config.json"]
    binding["sha256"] = mlx_train.file_sha256(adapter / "adapter_config.json")
    binding["bytes"] = (adapter / "adapter_config.json").stat().st_size
    receipt_path.write_text(json.dumps(path_receipt))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="data path"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )
    (adapter / "adapter_config.json").write_text(json.dumps(original_config))
    receipt_path.write_text(json.dumps(receipt))

    release_tampered = copy.deepcopy(receipt)
    release_tampered["approvals"]["release"] = {
        "state": "approved",
        "review_ref": "self-declared",
    }
    receipt_path.write_text(json.dumps(release_tampered))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="release approval"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )
    receipt_path.write_text(json.dumps(receipt))

    unknown_receipt = {**receipt, "revoked": True}
    receipt_path.write_text(json.dumps(unknown_receipt))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unknown revoked"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(mlx_train.TrainingStewardshipError, match="not approved"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            require_serving=True,
            adapters_root=adapters,
        )

    tampered = copy.deepcopy(receipt)
    tampered["weights"]["path"] = "../../outside.safetensors"
    receipt_path.write_text(json.dumps(tampered))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="weights.path"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            adapters_root=adapters,
        )

    receipt_path.write_text(json.dumps(receipt))
    mismatched = {**_model_resolution(), "revision": "d" * 40}
    with pytest.raises(mlx_train.TrainingStewardshipError, match="base-model"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=mismatched,
            adapters_root=adapters,
        )

    approval = {
        "schema": mlx_train.SERVING_APPROVAL_SCHEMA,
        "state": "approved",
        "scope": "loopback-only-local-serving",
        "adapter": "reviewed-adapter",
        "training_receipt_sha256": mlx_train.file_sha256(receipt_path),
        "declared_by": "test-operator",
        "declared_at": "2026-09-02T11:00:00Z",
        "decision_ref": "review:loopback-serving-only",
        "boundary": mlx_train.SERVING_APPROVAL_BOUNDARY,
    }
    approval_path = adapter / mlx_train.SERVING_APPROVAL_NAME
    approval_path.write_text(json.dumps({**approval, "revoked": True}))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unknown revoked"):
        mlx_train.validate_adapter_artifact(
            "reviewed-adapter",
            model_resolution=_model_resolution(),
            require_serving=True,
            adapters_root=adapters,
        )
    approval_path.write_text(json.dumps(approval))
    approved = mlx_train.validate_adapter_artifact(
        "reviewed-adapter",
        model_resolution=_model_resolution(),
        require_serving=True,
        adapters_root=adapters,
    )
    assert approved["serving_state"] == "approved"
    active_binding = mlx_train.validate_adapter_serving_binding(
        "reviewed-adapter",
        model_binding=_model_resolution(),
        adapters_root=adapters,
    )
    assert active_binding["receipt_sha256"] == mlx_train.file_sha256(receipt_path)

    approval_path.write_text(
        json.dumps({**approval, "training_receipt_sha256": "sha256:" + "0" * 64})
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="does not match"):
        mlx_train.validate_adapter_serving_binding(
            "reviewed-adapter",
            model_binding=_model_resolution(),
            adapters_root=adapters,
        )


def test_simultaneous_serving_decisions_have_one_immutable_winner(tmp_path):
    approval_path = tmp_path / "serving-approval.json"
    gate = tmp_path / "go"
    script = r"""
import importlib.util
import json
import sys
import time
from pathlib import Path

tool = Path(sys.argv[1])
approval_path = Path(sys.argv[2])
gate = Path(sys.argv[3])
identity = sys.argv[4]
spec = importlib.util.spec_from_file_location("exclusive_mlx_train", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
while not gate.exists():
    time.sleep(0.005)
value = {"declared_by": identity, "decision_ref": "decision:" + identity}
try:
    module._write_canonical_json_exclusive(
        approval_path, value, "serving approval"
    )
except module.TrainingStewardshipError:
    print("LOSE:" + identity, flush=True)
else:
    print("WIN:" + identity, flush=True)
"""
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                script,
                str(ROOT / "tools" / "mlx_train.py"),
                str(approval_path),
                str(gate),
                identity,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for identity in ("reviewer-a", "reviewer-b")
    ]
    gate.write_text("go")
    outputs = []
    for child in children:
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stderr
        outputs.append(stdout.strip())

    winners = [output.removeprefix("WIN:") for output in outputs if output.startswith("WIN:")]
    assert len(winners) == 1
    assert sum(output.startswith("LOSE:") for output in outputs) == 1
    approval = json.loads(approval_path.read_text())
    assert approval == {
        "declared_by": winners[0],
        "decision_ref": "decision:" + winners[0],
    }


def test_eval_binds_plan_config_and_writes_validated_receipt(tmp_path, monkeypatch):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        _target_preflight(dataset),
        "eval-adapter",
    )
    adapters = tmp_path / "adapters"
    adapter_path = adapters / "eval-adapter"
    adapter_path.mkdir(parents=True)
    (adapter_path / mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"bound-weights")
    (adapter_path / "adapter_config.json").write_text(
        json.dumps(_mlx_adapter_config(plan, adapter_path))
    )
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )
    receipt = mlx_train.build_adapter_receipt(
        adapter_path, plan, model_resolution=_model_resolution()
    )
    (adapter_path / mlx_train.ADAPTER_RECEIPT_NAME).write_text(json.dumps(receipt))
    artifact = mlx_train.validate_adapter_artifact(
        "eval-adapter",
        model_resolution=_model_resolution(),
        adapters_root=adapters,
    )

    monkeypatch.setattr(mlx_train, "load_training_manifest", lambda _path: dataset)
    monkeypatch.setattr(mlx_train, "resolve_training_environment", _runtime_environment)
    monkeypatch.setattr(mlx_train, "resolve_training_model", _model_resolution)
    monkeypatch.setattr(
        mlx_train, "validate_adapter_artifact", lambda *_args, **_kwargs: artifact
    )
    launched = []

    def fake_run(command, capture_output=False, text=False):
        assert capture_output is True
        assert text is True
        assert command[1] == "-I"
        assert command[command.index("--test-batches") + 1] == "-1"
        config_path = Path(command[command.index("--config") + 1])
        assert (
            json.loads(config_path.read_text()) == plan["launcher"]["effective_config"]
        )
        launched.append(command)
        return argparse.Namespace(
            returncode=0,
            stdout="Test loss 1.25, Test ppl 3.4903.\n",
            stderr="",
        )

    monkeypatch.setattr(mlx_train.subprocess, "run", fake_run)
    mlx_train.cmd_eval(
        argparse.Namespace(
            manifest=str(manifest_path), adapter="eval-adapter", split="validation"
        )
    )
    assert len(launched) == 1
    receipt_paths = list((adapter_path / mlx_train.EVALUATIONS_DIR_NAME).glob("*.json"))
    assert len(receipt_paths) == 1
    evaluation = json.loads(receipt_paths[0].read_text())
    mlx_train.validate_evaluation_receipt(
        evaluation,
        plan=plan,
        adapter_receipt_sha256=artifact["receipt_sha256"],
    )
    assert evaluation["settings"] == mlx_train._evaluation_settings(plan)
    assert evaluation["metrics"] == {
        "test_loss": 1.25,
        "test_perplexity": 3.4903,
    }

    evaluation["settings"]["batch_size"] += 1
    evaluation["evaluation_id"] = mlx_train._evaluation_id(
        {key: value for key, value in evaluation.items() if key != "evaluation_id"}
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="settings binding"):
        mlx_train.validate_evaluation_receipt(evaluation, plan=plan)

    impossible = json.loads(receipt_paths[0].read_text())
    impossible["metrics"]["test_perplexity"] = 999.0
    impossible["evaluation_id"] = mlx_train._evaluation_id(
        {key: value for key, value in impossible.items() if key != "evaluation_id"}
    )
    with pytest.raises(mlx_train.TrainingStewardshipError, match="inconsistent"):
        mlx_train.validate_evaluation_receipt(impossible, plan=plan)


def test_successful_run_emits_plan_before_launch_and_adapter_receipt(
    tmp_path, monkeypatch
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    runs = tmp_path / "runs"
    adapters = tmp_path / "adapters"
    monkeypatch.setattr(mlx_train, "RUNS_DIR", runs)
    monkeypatch.setattr(mlx_train, "ADAPTERS_DIR", adapters)
    monkeypatch.setattr(mlx_train, "load_training_manifest", lambda _path: dataset)
    monkeypatch.setattr(mlx_train, "load_lora_config", _lora_config)
    monkeypatch.setattr(mlx_train, "resolve_training_model", _model_resolution)
    monkeypatch.setattr(mlx_train, "resolve_training_environment", _runtime_environment)
    monkeypatch.setattr(
        mlx_train,
        "preflight_supervised_targets",
        lambda *_args, **_kwargs: _target_preflight(dataset),
    )
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )

    launched = []

    def fake_run(command, capture_output=False):
        assert capture_output is False
        assert command[1] == "-I"
        adapter_path = Path(command[command.index("--adapter-path") + 1])
        run_dir = _single_run_dir(runs)
        assert (run_dir / "run-plan.json").is_file()
        assert (run_dir / "run-receipt.json").is_file()
        adapter_path.joinpath(mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"weights")
        plan = json.loads((run_dir / "run-plan.json").read_text())
        adapter_path.joinpath("adapter_config.json").write_text(
            json.dumps(
                _mlx_adapter_config(
                    plan,
                    adapter_path,
                    run_dir=run_dir,
                    model_path=command[command.index("--model") + 1],
                )
            )
        )
        launched.append(command)
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr(mlx_train.subprocess, "run", fake_run)
    args = argparse.Namespace(
        manifest=str(manifest_path), dataset=None, epochs=None, adapter="test-adapter"
    )
    mlx_train.cmd_run(args)

    assert len(launched) == 1
    receipt_path = adapters / "test-adapter" / mlx_train.ADAPTER_RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text())
    assert receipt["weights"]["sha256"].startswith("sha256:")
    run_dir = _single_run_dir(runs)
    result = json.loads((run_dir / "run-result.json").read_text())
    assert result["state"] == "completed"


def test_simultaneous_identical_runs_launch_once_and_preserve_completed_state(
    tmp_path, monkeypatch
):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    runs = tmp_path / "runs"
    adapters = tmp_path / "adapters"
    monkeypatch.setattr(mlx_train, "RUNS_DIR", runs)
    monkeypatch.setattr(mlx_train, "ADAPTERS_DIR", adapters)
    monkeypatch.setattr(mlx_train, "load_training_manifest", lambda _path: dataset)
    monkeypatch.setattr(mlx_train, "load_lora_config", _lora_config)
    monkeypatch.setattr(mlx_train, "resolve_training_model", _model_resolution)
    monkeypatch.setattr(mlx_train, "resolve_training_environment", _runtime_environment)
    monkeypatch.setattr(
        mlx_train,
        "preflight_supervised_targets",
        lambda *_args, **_kwargs: _target_preflight(dataset),
    )
    monkeypatch.setattr(
        mlx_train,
        "inspect_adapter_weights",
        lambda _path, _model, _config: _finite_weight_inspection(),
    )

    trainer_entered = threading.Event()
    release_trainer = threading.Event()
    launches = []
    staged_digests = []

    def fake_run(command, capture_output=False):
        assert capture_output is False
        adapter_path = Path(command[command.index("--adapter-path") + 1])
        run_dir = _single_run_dir(runs)
        data_dir = run_dir / "data"
        before = {
            path.name: mlx_train.file_sha256(path)
            for path in sorted(data_dir.glob("*.jsonl"))
        }
        launches.append(command)
        staged_digests.append(before)
        trainer_entered.set()
        assert release_trainer.wait(timeout=3)
        after = {
            path.name: mlx_train.file_sha256(path)
            for path in sorted(data_dir.glob("*.jsonl"))
        }
        assert after == before
        adapter_path.joinpath(mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"weights")
        plan = json.loads((run_dir / "run-plan.json").read_text())
        adapter_path.joinpath("adapter_config.json").write_text(
            json.dumps(
                _mlx_adapter_config(
                    plan,
                    adapter_path,
                    run_dir=run_dir,
                    model_path=command[command.index("--model") + 1],
                )
            )
        )
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr(mlx_train.subprocess, "run", fake_run)
    args = argparse.Namespace(
        manifest=str(manifest_path),
        dataset=None,
        epochs=None,
        adapter="contended-adapter",
    )
    successes = []
    errors = []

    def invoke():
        try:
            mlx_train.cmd_run(args)
            successes.append(True)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert trainer_entered.wait(timeout=2)
    second.start()
    second.join(timeout=2)
    assert not second.is_alive()
    release_trainer.set()
    first.join(timeout=3)

    assert not first.is_alive()
    assert len(launches) == 1
    assert len(staged_digests) == 1
    assert successes == [True]
    assert len(errors) == 1
    assert isinstance(errors[0], mlx_train.TrainingStewardshipError)
    assert "already active" in str(errors[0])
    run_dir = _single_run_dir(runs)
    result = json.loads((run_dir / "run-result.json").read_text())
    assert result["state"] == "completed"


def _reviewed_artifact(tmp_path, live_source=False, review_state="approved"):
    records = [_record("alpha"), _record("beta")]
    artifact = {
        "schema": mlx_data.REVIEWED_OUTBOUND_SCHEMA,
        "artifact_id": "task-routing-reviewed-1",
        "task": "task-routing",
        "source": {
            "kind": "bounded-operational-export",
            "live_source": live_source,
            "export_ref": "export:test-only",
        },
        "review": {
            "state": review_state,
            "purpose": mlx_data.REVIEW_PURPOSE,
            "reviewed_by": "test-reviewer",
            "reviewed_at": "2026-09-02T10:00:00Z",
            "review_ref": "review:test-only",
        },
        "records_sha256": mlx_data.records_sha256(records),
        "records": records,
        "limitations": ["Test-only fixture."],
    }
    path = tmp_path / "reviewed-outbound.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path, artifact


def test_live_history_has_no_default_harvest_path():
    with pytest.raises(mlx_data.DataStewardshipError, match="disabled"):
        mlx_data.harvest_delegation_history()
    with pytest.raises(mlx_data.DataStewardshipError, match="disabled"):
        mlx_data.harvest_delegation_history(mlx_data.DELEGATION_HISTORY)


def test_reviewed_outbound_artifact_is_explicit_and_digest_bound(tmp_path):
    path, artifact = _reviewed_artifact(tmp_path)
    loaded = mlx_data.load_reviewed_outbound_artifact(path)
    assert loaded["artifact"]["artifact_id"] == artifact["artifact_id"]
    assert loaded["artifact_sha256"] == mlx_data.file_sha256(path)

    artifact["records"][0]["messages"][-1]["content"] = "gamma"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(mlx_data.DataStewardshipError, match="digest does not match"):
        mlx_data.load_reviewed_outbound_artifact(path)


@pytest.mark.parametrize(
    ("live_source", "review_state", "message"),
    [(True, "approved", "live_source"), (False, "hold", "not approved")],
)
def test_reviewed_outbound_artifact_rejects_live_or_unapproved(
    tmp_path, live_source, review_state, message
):
    path, _ = _reviewed_artifact(tmp_path, live_source, review_state)
    with pytest.raises(mlx_data.DataStewardshipError, match=message):
        mlx_data.load_reviewed_outbound_artifact(path)


@pytest.mark.parametrize(
    "location", ["artifact", "source", "review", "record", "message"]
)
def test_reviewed_outbound_artifact_rejects_unknown_revocation_fields(
    tmp_path, location
):
    path, artifact = _reviewed_artifact(tmp_path)
    if location == "artifact":
        artifact["revoked"] = True
    elif location == "source":
        artifact["source"]["revoked"] = True
    elif location == "review":
        artifact["review"]["revoked"] = True
    elif location == "record":
        artifact["records"][0]["revoked"] = True
    else:
        artifact["records"][0]["messages"][-1]["revoked"] = True
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(mlx_data.DataStewardshipError, match="unknown field"):
        mlx_data.load_reviewed_outbound_artifact(path)


@pytest.mark.parametrize("reviewed_at", ["not-a-date", "2026-09-02T10:00:00"])
def test_reviewed_outbound_artifact_requires_timezone_timestamp(
    tmp_path, reviewed_at
):
    path, artifact = _reviewed_artifact(tmp_path)
    artifact["review"]["reviewed_at"] = reviewed_at
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(
        mlx_data.DataStewardshipError, match="timezone-bearing ISO-8601"
    ):
        mlx_data.load_reviewed_outbound_artifact(path)


def test_reviewed_import_is_exactly_idempotent_and_never_rewrites_provenance(
    tmp_path, monkeypatch
):
    reviewed_dir = tmp_path / "mlx" / "training" / "reviewed"
    monkeypatch.setattr(mlx_data, "LOVE_ROOT", tmp_path)
    monkeypatch.setattr(mlx_data, "REVIEWED_DATASETS_DIR", reviewed_dir)
    artifact_path, artifact = _reviewed_artifact(tmp_path)
    args = argparse.Namespace(artifact=str(artifact_path))

    mlx_data.cmd_import_reviewed(args)
    output = reviewed_dir / f"{artifact['artifact_id']}.jsonl"
    receipt = output.with_suffix(".import-receipt.json")
    first_output = output.read_bytes()
    first_receipt = receipt.read_bytes()
    mlx_data.cmd_import_reviewed(args)
    assert output.read_bytes() == first_output
    assert receipt.read_bytes() == first_receipt

    artifact["review"]["review_ref"] = "review:changed-provenance"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(
        mlx_data.DataStewardshipError, match="differs in data or provenance"
    ):
        mlx_data.cmd_import_reviewed(args)
    assert output.read_bytes() == first_output
    assert receipt.read_bytes() == first_receipt


@pytest.mark.parametrize("orphan", ["output", "receipt"])
def test_reviewed_import_rejects_orphan_output_or_receipt(
    tmp_path, monkeypatch, orphan
):
    reviewed_dir = tmp_path / "mlx" / "training" / "reviewed"
    reviewed_dir.mkdir(parents=True)
    monkeypatch.setattr(mlx_data, "LOVE_ROOT", tmp_path)
    monkeypatch.setattr(mlx_data, "REVIEWED_DATASETS_DIR", reviewed_dir)
    artifact_path, artifact = _reviewed_artifact(tmp_path)
    output = reviewed_dir / f"{artifact['artifact_id']}.jsonl"
    receipt = output.with_suffix(".import-receipt.json")
    (output if orphan == "output" else receipt).write_bytes(b"orphan\n")

    with pytest.raises(mlx_data.DataStewardshipError, match="orphan output or receipt"):
        mlx_data.cmd_import_reviewed(
            argparse.Namespace(artifact=str(artifact_path))
        )


def test_generation_writes_new_unreviewed_candidate_without_legacy_append(
    tmp_path, monkeypatch
):
    templates = tmp_path / "templates"
    candidates = tmp_path / "candidates"
    legacy = tmp_path / "legacy"
    templates.mkdir()
    legacy.mkdir()
    (templates / "task-routing.txt").write_text("make {COUNT} {SYSTEM_PROMPT}")
    legacy_file = legacy / "task-routing.jsonl"
    legacy_file.write_text("legacy-bytes\n")
    monkeypatch.setattr(mlx_data, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(mlx_data, "CANDIDATE_DATASETS_DIR", candidates)
    monkeypatch.setattr(mlx_data, "DATASETS_DIR", legacy)
    monkeypatch.setattr(mlx_data, "LOVE_ROOT", tmp_path)
    records = [_record("alpha"), _record("beta")]
    records[0]["source"] = "spoofed-reviewed"
    output = "\n".join(json.dumps(record) for record in records)
    monkeypatch.setattr(
        mlx_data.subprocess,
        "run",
        lambda *_args, **_kwargs: argparse.Namespace(returncode=0, stdout=output),
    )
    args = argparse.Namespace(
        task="task-routing", count=2, candidate="routing-v1", yes=True
    )
    mlx_data.cmd_generate(args)
    candidate = candidates / "routing-v1.jsonl"
    written = mlx_data.load_jsonl_records(candidate)
    assert len(written) == 2
    assert all(item["source"] == "model-generated-unreviewed" for item in written)
    assert legacy_file.read_text() == "legacy-bytes\n"
    with pytest.raises(mlx_data.DataStewardshipError, match="already exists"):
        mlx_data.cmd_generate(args)


def test_task_classification_metrics_do_not_require_mlx():
    records = [
        _record("alpha"),
        _record("beta"),
        _record("beta"),
        _record("gamma"),
    ]
    result = mlx_data.evaluate_task_predictions(
        "task-routing", records, ["alpha", "beta", "alpha", "surprise"]
    )
    assert result["examples"] == 4
    assert result["accuracy"] == 0.5
    assert result["unexpected_predictions"] == ["surprise"]
    assert result["confusion_matrix"]["gamma"]["__other__"] == 1
    assert result["per_class"]["beta"]["recall"] == 0.5
    assert result["macro_f1"] == pytest.approx(1 / 3)


def test_classification_metrics_reject_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        mlx_data.classification_metrics(["high"], [], ["high", "low"])
