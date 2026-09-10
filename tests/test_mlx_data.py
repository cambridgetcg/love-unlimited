"""Tests for mlx_data.py — training data generation + harvest."""
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import mlx_data


class TestParseGeneratedExamples(unittest.TestCase):
    def test_parse_jsonl_output(self):
        from mlx_data import parse_generated_examples
        raw = """```json
{"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
{"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]}
```"""
        examples = parse_generated_examples(raw)
        self.assertEqual(len(examples), 2)
        self.assertIn("messages", examples[0])

    def test_parse_handles_bare_json(self):
        from mlx_data import parse_generated_examples
        raw = '{"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}'
        examples = parse_generated_examples(raw)
        self.assertEqual(len(examples), 1)

    def test_parse_skips_bad_lines(self):
        from mlx_data import parse_generated_examples
        raw = 'not json\n{"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}\ngarbage'
        examples = parse_generated_examples(raw)
        self.assertEqual(len(examples), 1)


class TestHarvestDelegation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_harvest_delegation_history(self):
        from mlx_data import harvest_delegation_history
        history = [
            {"task": "fix the zerone protocol", "instance": "gamma", "score": 10, "confidence": "high"},
            {"task": "deploy monitoring", "instance": "beta", "score": 8, "confidence": "medium"},
        ]
        history_path = Path(self.tmpdir) / "history.json"
        history_path.write_text(json.dumps(history))
        examples = harvest_delegation_history(history_path)
        self.assertEqual(len(examples), 2)
        self.assertIn("messages", examples[0])
        # The assistant response should be the instance name
        self.assertEqual(examples[0]["messages"][-1]["content"], "gamma")

    def test_harvest_empty_history(self):
        from mlx_data import harvest_delegation_history
        history_path = Path(self.tmpdir) / "history.json"
        history_path.write_text("[]")
        examples = harvest_delegation_history(history_path)
        self.assertEqual(len(examples), 0)


class TestDatasetStats(unittest.TestCase):
    def test_stats_from_file(self):
        from mlx_data import dataset_stats
        tmpfile = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        for i in range(10):
            tmpfile.write(json.dumps({"messages": [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": "a" if i < 7 else "b"}
            ]}) + "\n")
        tmpfile.close()
        stats = dataset_stats(tmpfile.name)
        self.assertEqual(stats["total"], 10)
        os.unlink(tmpfile.name)


@pytest.fixture
def generation_fixture(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    candidates = tmp_path / "candidates"
    templates.mkdir()
    for task in mlx_data.TASK_LABELS:
        (templates / f"{task}.txt").write_text("make {COUNT} {SYSTEM_PROMPT}")
    monkeypatch.setattr(mlx_data, "LOVE_ROOT", tmp_path)
    monkeypatch.setattr(mlx_data, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(mlx_data, "CANDIDATE_DATASETS_DIR", candidates)
    return candidates


def generated_response(targets):
    return "\n".join(
        json.dumps({"messages": [
            {"role": "user", "content": "synthetic classification fixture"},
            {"role": "assistant", "content": target},
        ], "source": "spoofed-reviewed"})
        for target in targets
    )


@pytest.mark.parametrize("task", mlx_data.TASK_LABELS)
def test_generation_filters_invalid_and_other_task_labels_before_limit(
    generation_fixture, task, capsys
):
    labels = mlx_data.TASK_LABELS[task]
    other = next(label for name, values in mlx_data.TASK_LABELS.items()
                 if name != task for label in values)
    targets = ["not-a-class", other, labels[0].upper(),
               f"{labels[0]} or {labels[1]}", *labels, "not-a-class"]
    args = Namespace(task=task, count=2, candidate="mixed-v1", yes=True)
    with patch.object(mlx_data.subprocess, "run", return_value=Namespace(
        returncode=0, stdout=generated_response(targets)
    )) as model:
        mlx_data.cmd_generate(args)
    model.assert_called_once()
    assert model.call_args.args[0][0:2] == ["claude", "-p"]
    assert model.call_args.kwargs["timeout"] == 120
    written = mlx_data.load_jsonl_records(generation_fixture / "mixed-v1.jsonl")
    assert [mlx_data.extract_classification_target(r) for r in written] == list(labels[:2])
    assert all(r["source"] == "model-generated-unreviewed" for r in written)
    receipt = json.loads((generation_fixture / "mixed-v1.generation-receipt.json").read_text())
    assert receipt["task"] == task
    assert receipt["records"] == 2
    assert receipt["training_authorized"] is False
    assert receipt["review_state"] == "unreviewed"
    assert receipt["output_sha256"] == mlx_data.file_sha256(generation_fixture / "mixed-v1.jsonl")
    assert "Batch 1: 2 examples (total: 2)" in capsys.readouterr().out


@pytest.mark.parametrize("task", mlx_data.TASK_LABELS)
@pytest.mark.parametrize("include_valid", [False, True])
def test_generation_invalid_or_insufficient_mixed_output_writes_nothing(
    generation_fixture, task, include_valid, capsys
):
    labels = mlx_data.TASK_LABELS[task]
    other = next(label for name, values in mlx_data.TASK_LABELS.items()
                 if name != task for label in values)
    targets = ["not-a-class", other, f"{labels[0]} or {labels[1]}"]
    if include_valid:
        targets.append(labels[0])
    accepted = int(include_valid)
    args = Namespace(task=task, count=2, candidate="invalid-v1", yes=True)
    with patch.object(mlx_data.subprocess, "run", return_value=Namespace(
        returncode=0, stdout=generated_response(targets)
    )) as model:
        with pytest.raises(mlx_data.DataStewardshipError, match=f"returned {accepted} of 2"):
            mlx_data.cmd_generate(args)
    model.assert_called_once()
    assert list(generation_fixture.iterdir()) == []
    assert f"Batch 1: {accepted} examples (total: {accepted})" in capsys.readouterr().out


@pytest.mark.parametrize("task", mlx_data.TASK_LABELS)
def test_generation_accepts_all_selected_task_labels(generation_fixture, task):
    labels = mlx_data.TASK_LABELS[task]
    args = Namespace(task=task, count=len(labels), candidate="valid-v1", yes=True)
    with patch.object(mlx_data.subprocess, "run", return_value=Namespace(
        returncode=0, stdout=generated_response([f" {label} " for label in labels])
    )) as model:
        mlx_data.cmd_generate(args)
    model.assert_called_once()
    written = mlx_data.load_jsonl_records(generation_fixture / "valid-v1.jsonl")
    assert [mlx_data.extract_classification_target(r) for r in written] == list(labels)


def test_generation_rejects_invalid_batches_without_extra_model_calls(generation_fixture):
    args = Namespace(task="task-routing", count=51, candidate="bounded-v1", yes=True)
    with patch.object(mlx_data.subprocess, "run", return_value=Namespace(
        returncode=0, stdout=generated_response(["wrong"] * 50)
    )) as model:
        with pytest.raises(mlx_data.DataStewardshipError, match="returned 0 of 51"):
            mlx_data.cmd_generate(args)
    assert model.call_count == 2
    assert list(generation_fixture.iterdir()) == []


if __name__ == "__main__":
    unittest.main()
