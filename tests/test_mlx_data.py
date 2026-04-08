"""Tests for mlx_data.py — training data generation + harvest."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


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


if __name__ == "__main__":
    unittest.main()
