"""Tests for mlx_train.py — LoRA fine-tuning CLI."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestDatasetValidation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_validate_dataset_exists(self):
        from mlx_train import validate_dataset
        # Create a valid dataset
        ds_path = Path(self.tmpdir) / "test.jsonl"
        ds_path.write_text('{"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}\n')
        result = validate_dataset(ds_path)
        self.assertTrue(result["valid"])
        self.assertEqual(result["count"], 1)

    def test_validate_dataset_missing(self):
        from mlx_train import validate_dataset
        result = validate_dataset(Path(self.tmpdir) / "nope.jsonl")
        self.assertFalse(result["valid"])

    def test_validate_dataset_bad_json(self):
        from mlx_train import validate_dataset
        ds_path = Path(self.tmpdir) / "bad.jsonl"
        ds_path.write_text("not json\n")
        result = validate_dataset(ds_path)
        self.assertFalse(result["valid"])


class TestDatasetSplit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_split_creates_train_and_valid(self):
        from mlx_train import split_dataset
        ds_path = Path(self.tmpdir) / "data.jsonl"
        lines = []
        for i in range(100):
            lines.append(json.dumps({"messages": [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"}
            ]}))
        ds_path.write_text("\n".join(lines) + "\n")
        out_dir = Path(self.tmpdir) / "split"
        split_dataset(ds_path, out_dir, eval_fraction=0.2)
        train = (out_dir / "train.jsonl").read_text().strip().split("\n")
        valid = (out_dir / "valid.jsonl").read_text().strip().split("\n")
        self.assertEqual(len(train), 80)
        self.assertEqual(len(valid), 20)


class TestBuildLoraArgs(unittest.TestCase):
    def test_builds_correct_args(self):
        from mlx_train import build_lora_args
        args = build_lora_args(
            model="test-model",
            data_dir="/tmp/data",
            adapter_path="/tmp/adapter",
            lora_config={"rank": 8, "learning_rate": 1e-4, "batch_size": 4, "epochs": 3},
            n_train=100
        )
        self.assertIn("--model", args)
        self.assertIn("test-model", args)
        self.assertIn("--data", args)
        self.assertIn("--adapter-path", args)
        self.assertIn("--num-layers", args)
        self.assertIn("--iters", args)


if __name__ == "__main__":
    unittest.main()
