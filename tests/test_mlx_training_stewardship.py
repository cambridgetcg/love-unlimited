"""Stewardship boundary tests for MLX data, training, and receipts."""

import argparse
import copy
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import mlx_data
import mlx_train


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MANIFEST = ROOT / "resources" / "data" / "manifests" / "legacy-mlx-v0.json"


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


def test_run_plan_and_prelaunch_receipt_are_deterministic(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    first = mlx_train.build_run_plan(
        dataset, _model_resolution(), _lora_config(), _runtime_environment()
    )
    second = mlx_train.build_run_plan(
        dataset, _model_resolution(), _lora_config(), _runtime_environment()
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
            dataset, _model_resolution(), config, _runtime_environment()
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


def test_adapter_receipt_binds_weights_and_separates_approvals(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset, _model_resolution(), _lora_config(), _runtime_environment()
    )
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    with pytest.raises(mlx_train.TrainingStewardshipError, match="required weights"):
        mlx_train.build_adapter_receipt(adapter, plan)

    weights = adapter / mlx_train.ADAPTER_WEIGHTS_NAME
    weights.write_bytes(b"test-only-weights")
    adapter_config = _mlx_adapter_config(plan, adapter)
    (adapter / "adapter_config.json").write_text(json.dumps(adapter_config))
    receipt = mlx_train.build_adapter_receipt(adapter, plan)
    assert receipt["weights"]["sha256"] == mlx_train.file_sha256(weights)
    assert receipt["run_plan"]["model"]["revision"] == "a" * 40
    assert receipt["run_plan"]["dataset"]["splits"]["train"]["sha256"].startswith(
        "sha256:"
    )
    assert receipt["approvals"]["serving"]["state"] == "not-approved"
    assert receipt["approvals"]["release"]["state"] == "not-approved"


def test_adapter_validator_rejects_traversal_mismatch_and_unapproved_serving(tmp_path):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        "reviewed-adapter",
    )
    adapters = tmp_path / "adapters"
    adapter = adapters / "reviewed-adapter"
    adapter.mkdir(parents=True)
    (adapter / mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"bound-weights")
    (adapter / "adapter_config.json").write_text(
        json.dumps(_mlx_adapter_config(plan, adapter))
    )
    receipt = mlx_train.build_adapter_receipt(adapter, plan)
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
        mlx_train.validate_adapter_artifact("reviewed-adapter", adapters_root=adapters)
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
        mlx_train.validate_adapter_artifact("reviewed-adapter", adapters_root=adapters)
    (adapter / "adapter_config.json").write_text(json.dumps(original_config))
    receipt_path.write_text(json.dumps(receipt))

    release_tampered = copy.deepcopy(receipt)
    release_tampered["approvals"]["release"] = {
        "state": "approved",
        "review_ref": "self-declared",
    }
    receipt_path.write_text(json.dumps(release_tampered))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="release approval"):
        mlx_train.validate_adapter_artifact("reviewed-adapter", adapters_root=adapters)
    receipt_path.write_text(json.dumps(receipt))

    unknown_receipt = {**receipt, "revoked": True}
    receipt_path.write_text(json.dumps(unknown_receipt))
    with pytest.raises(mlx_train.TrainingStewardshipError, match="unknown revoked"):
        mlx_train.validate_adapter_artifact("reviewed-adapter", adapters_root=adapters)
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
        mlx_train.validate_adapter_artifact("reviewed-adapter", adapters_root=adapters)

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


def test_eval_binds_plan_config_and_writes_validated_receipt(tmp_path, monkeypatch):
    manifest_path, _ = _training_manifest(tmp_path)
    dataset = mlx_train.load_training_manifest(manifest_path, project_root=tmp_path)
    plan = mlx_train.build_run_plan(
        dataset,
        _model_resolution(),
        _lora_config(),
        _runtime_environment(),
        "eval-adapter",
    )
    adapters = tmp_path / "adapters"
    adapter_path = adapters / "eval-adapter"
    adapter_path.mkdir(parents=True)
    (adapter_path / mlx_train.ADAPTER_WEIGHTS_NAME).write_bytes(b"bound-weights")
    (adapter_path / "adapter_config.json").write_text(
        json.dumps(_mlx_adapter_config(plan, adapter_path))
    )
    receipt = mlx_train.build_adapter_receipt(adapter_path, plan)
    (adapter_path / mlx_train.ADAPTER_RECEIPT_NAME).write_text(json.dumps(receipt))
    artifact = mlx_train.validate_adapter_artifact(
        "eval-adapter", adapters_root=adapters
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

    launched = []

    def fake_run(command, capture_output=False):
        assert capture_output is False
        assert command[1] == "-I"
        adapter_path = Path(command[command.index("--adapter-path") + 1])
        run_dir = next(runs.iterdir())
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
    run_dir = next(runs.iterdir())
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
