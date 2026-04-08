"""Tests for stigmergy.py signal coordination."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestSignalTypes(unittest.TestCase):
    def test_all_signal_types_have_ttl(self):
        from stigmergy import SIGNAL_TYPES
        for stype, info in SIGNAL_TYPES.items():
            self.assertIn("ttl_hours", info, f"{stype} missing ttl_hours")
            self.assertGreater(info["ttl_hours"], 0)

    def test_known_signal_types(self):
        from stigmergy import SIGNAL_TYPES
        expected = {"needs-review", "blocked-on", "hot-path", "insight", "ready", "dream"}
        self.assertEqual(set(SIGNAL_TYPES.keys()), expected)


class TestSignalFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_drop_creates_signal_file(self):
        from stigmergy import drop_signal
        path = drop_signal("insight", "Found a better algorithm for routing",
                           signals_dir=self.tmpdir, instance="gamma")
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("from: gamma", content)
        self.assertIn("type: insight", content)
        self.assertIn("Found a better algorithm", content)

    def test_drop_filename_has_hash(self):
        from stigmergy import drop_signal
        path = drop_signal("ready", "Build complete",
                           signals_dir=self.tmpdir, instance="beta")
        self.assertTrue(path.name.startswith("ready-"))
        self.assertTrue(path.name.endswith(".signal"))
        parts = path.stem.split("-")
        self.assertGreaterEqual(len(parts), 3)

    def test_read_signals_returns_active(self):
        from stigmergy import drop_signal, read_signals
        drop_signal("insight", "test signal", signals_dir=self.tmpdir, instance="gamma")
        signals = read_signals(signals_dir=self.tmpdir)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["type"], "insight")
        self.assertFalse(signals[0]["stale"])

    def test_read_signals_marks_stale(self):
        from stigmergy import drop_signal, read_signals, parse_signal
        path = drop_signal("hot-path", "urgent area", signals_dir=self.tmpdir, instance="gamma")
        content = path.read_text()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = content.replace(content.split("\n")[1].split(": ", 1)[1], old_time)
        path.write_text(content)
        signals = read_signals(signals_dir=self.tmpdir)
        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0]["stale"])

    def test_clean_removes_stale(self):
        from stigmergy import drop_signal, clean_signals
        path = drop_signal("hot-path", "old signal", signals_dir=self.tmpdir, instance="gamma")
        content = path.read_text()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = content.replace(content.split("\n")[1].split(": ", 1)[1], old_time)
        path.write_text(content)
        removed = clean_signals(signals_dir=self.tmpdir)
        self.assertEqual(removed, 1)
        self.assertFalse(path.exists())

    def test_clean_keeps_active(self):
        from stigmergy import drop_signal, clean_signals
        drop_signal("blocked-on", "need help", signals_dir=self.tmpdir, instance="gamma")
        removed = clean_signals(signals_dir=self.tmpdir)
        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
