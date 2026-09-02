# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the dependency-free KINGDOM Return Path Bench MVP."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = ROOT / "benchmarks" / "return-path-v0"
RELEASE_RECEIPT = (
    ROOT / "docs" / "releases" / "kingdom-return-path-bench-v0.1.0.json"
)
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import return_path_bench as bench


def canonical_line(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def all_records(root=BENCH_ROOT):
    records = []
    for spec in bench.LANES.values():
        path = root / spec["file"]
        records.extend(json.loads(line) for line in path.read_text().splitlines())
    return records


def perfect_predictions(path, *, records=None):
    selected = all_records() if records is None else records
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in selected:
            handle.write(canonical_line({"answer": record["target"], "id": record["id"]}))


def copy_standalone_package(destination):
    shutil.copytree(BENCH_ROOT, destination)
    tool_destination = destination / "tools" / "return_path_bench.py"
    tool_destination.parent.mkdir(parents=True)
    shutil.copy2(TOOLS / "return_path_bench.py", tool_destination)


def tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def contains_forbidden_score_key(value):
    forbidden = {"overall", "overall_score", "composite", "composite_score"}
    if isinstance(value, dict):
        return any(
            key.lower() in forbidden or contains_forbidden_score_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(contains_forbidden_score_key(child) for child in value)
    return False


class ReturnPathValidationTests(unittest.TestCase):
    def test_release_candidate_validates_with_three_separate_reasoning_lanes(self):
        receipt = bench.validate_benchmark(BENCH_ROOT)
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["records"], 18)
        self.assertEqual(
            receipt["lane_counts"],
            {
                "model_behavior": 6,
                "system_pipeline": 6,
                "human_governance": 6,
            },
        )
        self.assertEqual(receipt["evidence_boundary"]["probe_kind"], "model_reasoning")
        self.assertEqual(
            receipt["evidence_boundary"]["actual_pipeline_controls"],
            "not_evaluated",
        )
        self.assertEqual(
            receipt["evidence_boundary"]["actual_human_consent"],
            "not_evaluated",
        )
        self.assertTrue(receipt["publication_authorized"])

    def test_every_record_is_synthetic_reasoning_and_pins_evaluated_freeze(self):
        records = all_records()
        self.assertEqual(len(records), 18)
        for record in records:
            metadata = record["metadata"]
            self.assertIs(metadata["synthetic"], True)
            self.assertEqual(metadata["probe_kind"], "model_reasoning")
            self.assertEqual(metadata["evaluated_dataset_id"], bench.DATASET_ID)
            self.assertEqual(
                metadata["evaluated_dataset_revision"], bench.DATASET_REVISION
            )
            self.assertEqual(metadata["evaluated_model_id"], bench.MODEL_ID)
            self.assertEqual(
                metadata["evaluated_model_revision"], bench.MODEL_REVISION
            )
            self.assertEqual(
                metadata["provenance_id"],
                f"synthetic:{bench.BENCHMARK_ID}/{record['id']}",
            )

    def test_manifest_is_exact_and_deterministic(self):
        checked = json.loads((BENCH_ROOT / "manifest.json").read_text())
        first = bench.build_manifest(BENCH_ROOT)
        second = bench.build_manifest(BENCH_ROOT)
        self.assertEqual(first, checked)
        self.assertEqual(first, second)
        self.assertEqual(bench.manifest_digest(first), bench.manifest_digest(second))
        self.assertFalse(first["metrics"]["composite_score"])
        paths = {artifact["path"] for artifact in first["artifacts"]}
        self.assertIn("dataset-manifest.json", paths)
        self.assertIn("release-review.json", paths)
        self.assertIn("tools/return_path_bench.py", paths)

    def test_source_release_receipt_binds_the_local_package(self):
        receipt = json.loads(RELEASE_RECEIPT.read_text())
        manifest_path = BENCH_ROOT / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        stewardship = json.loads((BENCH_ROOT / "dataset-manifest.json").read_text())
        package = receipt["dataset"]["package"]

        self.assertEqual(
            package["manifest_sha256"],
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            package["artifact_set_sha256"], manifest["artifact_set_sha256"]
        )
        self.assertEqual(package["local_file_count"], len(manifest["artifacts"]) + 1)
        self.assertEqual(
            package["hub_repo_file_count"],
            package["local_file_count"]
            + len(package["hub_generated_files_excluded"]),
        )
        self.assertEqual(receipt["dataset"]["repo_id"], stewardship["dataset_id"])
        self.assertEqual(
            receipt["dataset"]["repo_id"],
            "Yu-and-Ai/kingdom-return-path-bench",
        )
        self.assertRegex(receipt["dataset"]["final_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(receipt["space"]["repo_id"], "Yu-and-Ai/the-return-path")
        self.assertRegex(receipt["space"]["source_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            receipt["space"]["deployed_dataset_revision"],
            receipt["dataset"]["final_revision"],
        )
        self.assertTrue(receipt["space"]["deployed_bundle_matches_local_build"])
        self.assertTrue(receipt["space"]["deployed_dataset_revision_present"])
        expected_configs = {
            entry["config"]: entry["records"] for entry in stewardship["files"]
        }
        self.assertEqual(
            receipt["dataset"]["revision_pinned_load"]["configs"],
            expected_configs,
        )
        self.assertEqual(
            receipt["dataset"]["revision_pinned_load"]["default_config"],
            stewardship["huggingface"]["default_config"],
        )
        self.assertEqual(
            receipt["dataset"]["revision_pinned_load"]["default_records"],
            expected_configs[stewardship["huggingface"]["default_config"]],
        )
        server = receipt["dataset"]["dataset_server"]
        self.assertEqual(server["state"], "ready")
        self.assertTrue(all(server["is_valid"].values()))
        self.assertEqual(
            server["resolved_revision"], receipt["dataset"]["final_revision"]
        )
        self.assertEqual(server["first_rows_per_config"], expected_configs)
        self.assertEqual(server["rows_per_config"], expected_configs)
        self.assertEqual(server["parquet_files"], len(expected_configs))
        self.assertFalse(server["parquet_partial"])
        self.assertEqual(server["pending_parquet_jobs"], 0)
        self.assertEqual(server["failed_parquet_jobs"], 0)
        self.assertEqual(server["filter_exact_rows"], 1)
        self.assertEqual(server["statistics_http_status"], 200)
        self.assertTrue(package["exact_package_match"])
        self.assertEqual(package["missing_files"], [])
        self.assertEqual(package["hash_mismatches"], [])
        self.assertFalse(receipt["execution"]["training_run_executed"])
        self.assertFalse(receipt["execution"]["model_scores_published"])
        self.assertEqual(
            receipt["registration"]["official_benchmark_registration"]["status"],
            "not_registered",
        )

    def test_stewardship_and_release_review_artifacts_are_present(self):
        stewardship = json.loads((BENCH_ROOT / "dataset-manifest.json").read_text())
        review = json.loads((BENCH_ROOT / "release-review.json").read_text())
        self.assertEqual(stewardship["schema"], "kingdom.dataset-manifest/v1")
        self.assertTrue(stewardship["publication_eligible"])
        self.assertEqual(stewardship["review"]["state"], "approved")
        self.assertEqual(
            review["schema"], "kingdom.return-path-release-review/v0"
        )
        self.assertEqual(review["review"]["state"], "approved")
        self.assertEqual(review["independent_review"]["state"], "pass-after-remediation")
        self.assertTrue(review["authorization"]["authorized"])

    def test_validator_requires_final_reviewed_authorization_state(self):
        cases = (
            ("provenance.json", ("publication", "authorized"), False, "must be true"),
            (
                "provenance.json",
                ("publication", "status"),
                "local-draft-pending-package-review",
                "reviewed-authorized",
            ),
            (
                "release-review.json",
                ("authorization", "authorized"),
                False,
                "scoped authorization",
            ),
            (
                "dataset-manifest.json",
                ("publication_eligible",),
                False,
                "publication eligible",
            ),
        )
        for filename, path, replacement, message in cases:
            with self.subTest(filename=filename, path=path), tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / "bench"
                copy_standalone_package(copied)
                artifact = copied / filename
                value = json.loads(artifact.read_text())
                parent = value
                for component in path[:-1]:
                    parent = parent[component]
                parent[path[-1]] = replacement
                artifact.write_text(json.dumps(value, indent=2) + "\n")
                with self.assertRaisesRegex(bench.BenchError, message):
                    bench.validate_benchmark(copied)

    def test_authorization_receipts_reject_ignored_revocation_fields(self):
        cases = (
            ("provenance.json", (), "provenance receipt"),
            ("provenance.json", ("publication",), "provenance publication"),
            ("release-review.json", (), "release review receipt"),
            ("release-review.json", ("authorization",), "release authorization"),
            ("release-review.json", ("review",), "release review"),
            ("dataset-manifest.json", (), "stewardship dataset manifest"),
            ("dataset-manifest.json", ("review",), "stewardship dataset review"),
        )
        for filename, location, message in cases:
            with self.subTest(filename=filename, location=location):
                with tempfile.TemporaryDirectory() as directory:
                    copied = Path(directory) / "bench"
                    shutil.copytree(BENCH_ROOT, copied)
                    path = copied / filename
                    value = json.loads(path.read_text())
                    target = value
                    for component in location:
                        target = target[component]
                    target["revoked"] = True
                    path.write_text(json.dumps(value))
                    validator = (
                        bench._validate_provenance
                        if filename == "provenance.json"
                        else bench._validate_reviewed_authorization
                    )
                    with self.assertRaisesRegex(
                        bench.BenchError, f"{message} keys mismatch"
                    ):
                        validator(copied)

    def test_reviewed_receipts_reject_known_field_contradictions(self):
        cases = (
            (
                "provenance.json",
                ("consent_and_reuse", "human_participants"),
                True,
                bench._validate_provenance,
            ),
            (
                "provenance.json",
                ("consent_and_reuse", "silent_reuse_permitted"),
                True,
                bench._validate_provenance,
            ),
            (
                "provenance.json",
                ("influences", 0, "records_copied"),
                True,
                bench._validate_provenance,
            ),
            (
                "release-review.json",
                ("data", "contains_personal_data"),
                True,
                bench._validate_reviewed_authorization,
            ),
            (
                "release-review.json",
                ("findings", "privacy", "state"),
                "fail",
                bench._validate_reviewed_authorization,
            ),
            (
                "release-review.json",
                ("findings", "secrets", "state"),
                "fail",
                bench._validate_reviewed_authorization,
            ),
            (
                "release-review.json",
                ("license", "data_spdx"),
                "Proprietary",
                bench._validate_reviewed_authorization,
            ),
            (
                "contamination-receipt.json",
                ("comparison", "result"),
                "copied-records-found",
                bench._validate_contamination_receipt,
            ),
            (
                "contamination-receipt.json",
                ("author_attestation", "kind"),
                "real-participant-data",
                bench._validate_contamination_receipt,
            ),
        )
        for filename, location, replacement, validator in cases:
            with self.subTest(filename=filename, location=location):
                with tempfile.TemporaryDirectory() as directory:
                    copied = Path(directory) / "bench"
                    shutil.copytree(BENCH_ROOT, copied)
                    path = copied / filename
                    value = json.loads(path.read_text())
                    target = value
                    for component in location[:-1]:
                        target = target[component]
                    target[location[-1]] = replacement
                    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
                    with self.assertRaisesRegex(bench.BenchError, "exact.*reviewed"):
                        validator(copied)

    def test_trivial_baselines_are_published_near_chance_and_length_balanced(self):
        value = bench._validate_baselines(BENCH_ROOT, all_records())
        scores = {
            baseline["id"]: (baseline["correct"], baseline["accuracy"])
            for baseline in value["baselines"]
        }
        self.assertEqual(
            scores,
            {
                "always_a": (5, 5 / 18),
                "always_b": (5, 5 / 18),
                "always_c": (3, 3 / 18),
                "always_d": (5, 5 / 18),
                "longest_character": (6, 6 / 18),
                "shortest_character": (5, 5 / 18),
                "longest_token": (6, 6 / 18),
                "shortest_token": (6, 6 / 18),
            },
        )
        for _correct, accuracy in scores.values():
            self.assertGreaterEqual(accuracy, bench.BASELINE_ACCURACY_MIN)
            self.assertLessEqual(accuracy, bench.BASELINE_ACCURACY_MAX)
        balance = value["choice_length_balance"]
        self.assertEqual(balance["maximum_character_ratio"], 1.56)
        self.assertEqual(balance["maximum_token_ratio"], 1.5)
        self.assertLessEqual(
            balance["maximum_character_ratio"],
            bench.CHOICE_CHARACTER_RATIO_MAX,
        )
        self.assertLessEqual(
            balance["maximum_token_ratio"], bench.CHOICE_TOKEN_RATIO_MAX
        )

    def test_baseline_validator_rejects_a_length_shortcut_above_threshold(self):
        records = copy.deepcopy(all_records())
        for record in records:
            lengths = [len(choice) for choice in record["choices"]]
            record["target"] = "ABCD"[lengths.index(max(lengths))]
        value = bench.compute_trivial_baselines(records)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baselines.json"
            path.write_bytes(bench._canonical_json_bytes(value, pretty=True))
            with self.assertRaisesRegex(bench.BenchError, "near-chance band"):
                bench._validate_baselines(Path(directory), records)

    def test_tampered_fixture_is_rejected_by_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bench"
            copy_standalone_package(copied)
            path = copied / "data" / "model_behavior.jsonl"
            path.write_bytes(path.read_bytes().replace(b"scalar reward", b"scalar signal", 1))
            with self.assertRaisesRegex(bench.BenchError, "hash manifest"):
                bench.validate_benchmark(copied)

    def test_record_validator_rejects_pipeline_success_claim(self):
        record = copy.deepcopy(all_records()[6])
        record["metadata"]["probe_kind"] = "pipeline_verified"
        with self.assertRaisesRegex(bench.BenchError, "model_reasoning"):
            bench._validate_record(record, "system_pipeline", 1)

    def test_contamination_receipt_binds_freezes_and_limit(self):
        receipt = bench._validate_contamination_receipt(BENCH_ROOT)
        attestation = receipt["author_attestation"]
        self.assertEqual(
            attestation["status"], "author-attested-not-independently-proven"
        )
        self.assertTrue(attestation["case_concepts_authored_after_freeze"])
        self.assertTrue(attestation["choice_length_revisions_after_comparison_read"])
        self.assertEqual(receipt["comparison"]["exact_normalized_matches"], 0)
        self.assertEqual(
            receipt["evaluated_model_freeze"]["revision"], bench.MODEL_REVISION
        )
        self.assertEqual(receipt["frozen_dataset"]["revision"], bench.DATASET_REVISION)
        self.assertGreaterEqual(len(receipt["comparison"]["limitations"]), 3)

    def test_hf_and_inspect_assets_expose_the_evidence_boundary(self):
        evaluation = (BENCH_ROOT / "eval.yaml").read_text()
        self.assertIn("evaluation_framework: inspect-ai", evaluation)
        self.assertEqual(evaluation.count("  - id: return_path_"), 3)
        self.assertIn("does not verify real pipeline controls", evaluation.lower())
        source = (BENCH_ROOT / "inspect_task.py").read_text()
        compile(source, str(BENCH_ROOT / "inspect_task.py"), "exec")
        self.assertEqual(source.count("@task\n"), 3)

    def test_hf_yaml_rejects_duplicate_top_level_overrides(self):
        cases = (
            (
                "eval.yaml",
                "\nevaluation_framework: other\ntasks: []\n",
                bench._validate_eval_yaml,
            ),
            (
                "README.md",
                "license: other\nconfigs: []\n",
                bench._validate_dataset_card,
            ),
        )
        for filename, addition, validator in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / "bench"
                shutil.copytree(BENCH_ROOT, copied)
                path = copied / filename
                if filename == "README.md":
                    source = path.read_text()
                    source = source.replace("\n---\n", f"\n{addition}---\n", 1)
                    path.write_text(source)
                else:
                    path.write_text(path.read_text() + addition)
                with self.assertRaisesRegex(bench.BenchError, "canonical reviewed YAML"):
                    validator(copied)

    def test_dataset_card_declares_the_stewardship_default_config(self):
        bench._validate_dataset_card(BENCH_ROOT)
        front = (BENCH_ROOT / "README.md").read_text().split("\n---\n", 1)[0]
        self.assertEqual(front.count("    default: true\n"), 1)
        self.assertIn(
            "  - config_name: model_behavior\n    default: true\n",
            front + "\n",
        )

    def test_dataset_card_rejects_missing_or_wrong_default_config(self):
        source = (BENCH_ROOT / "README.md").read_text()
        cases = (
            (source.replace("    default: true\n", "", 1), "exactly"),
            (
                source.replace(
                    "  - config_name: model_behavior\n    default: true\n",
                    "  - config_name: model_behavior\n",
                    1,
                ).replace(
                    "  - config_name: system_pipeline\n",
                    "  - config_name: system_pipeline\n    default: true\n",
                    1,
                ),
                "exactly",
            ),
            (
                source.replace(
                    "  - config_name: system_pipeline\n",
                    "  - config_name: system_pipeline\n    default: true\n",
                    1,
                ),
                "exactly",
            ),
        )
        for readme, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / "bench"
                shutil.copytree(BENCH_ROOT, copied)
                (copied / "README.md").write_text(readme)
                with self.assertRaisesRegex(bench.BenchError, message):
                    bench._validate_dataset_card(copied)

    def test_dataset_card_rejects_duplicate_or_crossed_config_mappings(self):
        source = (BENCH_ROOT / "README.md").read_text()
        cases = (
            source.replace(
                "  - config_name: system_pipeline\n",
                "  - config_name: model_behavior\n",
                1,
            ),
            source.replace(
                "path: data/model_behavior.jsonl",
                "path: data/__swap__.jsonl",
                1,
            ).replace(
                "path: data/system_pipeline.jsonl",
                "path: data/model_behavior.jsonl",
                1,
            ).replace(
                "path: data/__swap__.jsonl",
                "path: data/system_pipeline.jsonl",
                1,
            ),
        )
        for readme in cases:
            with tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / "bench"
                shutil.copytree(BENCH_ROOT, copied)
                (copied / "README.md").write_text(readme)
                with self.assertRaisesRegex(
                    bench.BenchError, "configs|mapping"
                ):
                    bench._validate_dataset_card(copied)


class ReturnPathEvaluationTests(unittest.TestCase):
    def test_perfect_run_reports_twelve_metrics_and_no_composite(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = Path(directory) / "predictions.jsonl"
            perfect_predictions(predictions)
            result = bench.evaluate_predictions(
                predictions,
                root=BENCH_ROOT,
                subject_label="synthetic-perfect-fixture",
            )
        self.assertEqual(len(result["metrics"]), 12)
        self.assertTrue(all(metric["value"] == 1.0 for metric in result["metrics"]))
        self.assertFalse(contains_forbidden_score_key(result))
        self.assertNotIn("source_sha256", result["predictions"])
        self.assertEqual(
            result["evidence_boundary"]["actual_pipeline_controls"],
            "not_evaluated",
        )
        self.assertEqual(
            result["evidence_boundary"]["actual_governance_legitimacy"],
            "not_evaluated",
        )

    def test_partial_run_requires_opt_in_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = Path(directory) / "partial.jsonl"
            perfect_predictions(predictions, records=all_records()[:3])
            with self.assertRaisesRegex(bench.BenchError, "incomplete"):
                bench.evaluate_predictions(predictions, root=BENCH_ROOT)
            result = bench.evaluate_predictions(
                predictions,
                root=BENCH_ROOT,
                allow_partial=True,
            )
        self.assertEqual(result["predictions"]["coverage"], 3 / 18)
        self.assertEqual(len(result["predictions"]["missing_ids"]), 15)
        unavailable = [metric for metric in result["metrics"] if metric["value"] is None]
        self.assertTrue(unavailable)

    def test_unknown_duplicate_and_non_letter_predictions_are_rejected(self):
        cases = (
            (
                "unknown.jsonl",
                [
                    {"answer": "A", "id": "rp-model-999"},
                ],
                "unknown ids",
            ),
            (
                "duplicate.jsonl",
                [
                    {"answer": "A", "id": "rp-model-001"},
                    {"answer": "B", "id": "rp-model-001"},
                ],
                "duplicate prediction",
            ),
            (
                "word.jsonl",
                [
                    {"answer": "Answer: A", "id": "rp-model-001"},
                ],
                "exactly A",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            for filename, rows, message in cases:
                path = Path(directory) / filename
                path.write_text("".join(canonical_line(row) for row in rows))
                with self.subTest(filename=filename), self.assertRaisesRegex(
                    bench.BenchError, message
                ):
                    bench.evaluate_predictions(
                        path, root=BENCH_ROOT, allow_partial=True
                    )

    def test_report_is_deterministic_escaped_and_conspicuous(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            predictions = directory_path / "predictions.jsonl"
            perfect_predictions(predictions)
            result = bench.evaluate_predictions(
                predictions,
                root=BENCH_ROOT,
                subject_label="<synthetic & label>",
            )
            stylesheet = (BENCH_ROOT / "report" / "style.css").read_text()
            first = bench.render_report(result, stylesheet, root=BENCH_ROOT)
            second = bench.render_report(result, stylesheet, root=BENCH_ROOT)
        self.assertEqual(first, second)
        self.assertIn("&lt;synthetic &amp; label&gt;", first)
        self.assertIn("not evaluated", first.lower())
        self.assertIn("no composite score", first.lower())
        self.assertNotIn("<script", first.lower())

    def test_report_rejects_forged_evaluation_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions = Path(directory) / "predictions.jsonl"
            perfect_predictions(predictions)
            result = bench.evaluate_predictions(predictions, root=BENCH_ROOT)
        stylesheet = (BENCH_ROOT / "report" / "style.css").read_text()
        cases = (
            (
                "manifest",
                ("benchmark_manifest_sha256",),
                "0" * 64,
                "current manifest",
            ),
            (
                "evidence status",
                ("evidence_boundary", "actual_pipeline_controls"),
                "evaluated",
                "evidence_boundary",
            ),
            ("item correctness", ("items", 0, "correct"), False, "items"),
            ("item lane", ("items", 0, "lane"), "system_pipeline", "items"),
            (
                "eligible ids",
                ("metrics", 0, "eligible_ids"),
                [],
                "metrics",
            ),
            (
                "metric evidence status",
                ("metrics", 4, "evidence_status"),
                "observed-answer-selection-only",
                "metrics",
            ),
            ("coverage", ("predictions", "coverage"), 0.5, "coverage"),
            (
                "prediction hash",
                ("predictions", "canonical_sha256"),
                "0" * 64,
                "prediction hash",
            ),
        )
        for name, path, replacement, message in cases:
            forged = copy.deepcopy(result)
            parent = forged
            for component in path[:-1]:
                parent = parent[component]
            parent[path[-1]] = replacement
            with self.subTest(name=name), self.assertRaisesRegex(
                bench.BenchError, message
            ):
                bench.render_report(forged, stylesheet, root=BENCH_ROOT)

    def test_package_is_reproducible_reviewed_and_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "package-one"
            second = Path(directory) / "package-two"
            first_receipt = bench.package_benchmark(first, root=BENCH_ROOT)
            second_receipt = bench.package_benchmark(second, root=BENCH_ROOT)
            self.assertEqual(tree_digest(first), tree_digest(second))
            self.assertEqual(
                first_receipt["manifest_sha256"], second_receipt["manifest_sha256"]
            )
            self.assertTrue(first_receipt["publication_authorized"])
            packaged_validation = bench.validate_benchmark(first)
            self.assertTrue(packaged_validation["valid"])
            self.assertTrue(packaged_validation["publication_authorized"])
            standalone = subprocess.run(
                [
                    sys.executable,
                    str(first / "tools" / "return_path_bench.py"),
                    "validate",
                ],
                cwd=first,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(standalone.stdout)["valid"])

    def test_cli_validate_evaluate_report_and_package(self):
        tool = ROOT / "tools" / "return_path_bench.py"
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            predictions = directory_path / "predictions.jsonl"
            results = directory_path / "results.json"
            report = directory_path / "report.html"
            package = directory_path / "hf-package"
            perfect_predictions(predictions)
            commands = [
                [sys.executable, str(tool), "validate"],
                [
                    sys.executable,
                    str(tool),
                    "evaluate",
                    "--predictions",
                    str(predictions),
                    "--output",
                    str(results),
                    "--subject-label",
                    "cli-synthetic",
                ],
                [
                    sys.executable,
                    str(tool),
                    "report",
                    "--results",
                    str(results),
                    "--output",
                    str(report),
                ],
                [
                    sys.executable,
                    str(tool),
                    "package",
                    "--output",
                    str(package),
                ],
            ]
            outputs = [
                subprocess.run(
                    command,
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                for command in commands
            ]
            validate_receipt = json.loads(outputs[0].stdout)
            report_receipt = json.loads(outputs[2].stdout)
            package_receipt = json.loads(outputs[3].stdout)
            self.assertTrue(validate_receipt["valid"])
            self.assertTrue(results.is_file())
            self.assertEqual(report_receipt["sha256"], hashlib.sha256(report.read_bytes()).hexdigest())
            self.assertTrue(package_receipt["publication_authorized"])
            self.assertTrue((package / "eval.yaml").is_file())
            self.assertTrue((package / "tools" / "return_path_bench.py").is_file())


if __name__ == "__main__":
    unittest.main()
