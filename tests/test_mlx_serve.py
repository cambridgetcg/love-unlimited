"""Tests for mlx_serve.py — MLX inference server."""
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestChatFormatting(unittest.TestCase):
    def test_format_messages_with_system(self):
        from mlx_serve import format_messages
        messages = format_messages("What is 2+2?", system="You are helpful.")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

    def test_format_messages_without_system(self):
        from mlx_serve import format_messages
        messages = format_messages("Hello")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")


class TestConfigLoading(unittest.TestCase):
    def test_load_config(self):
        from mlx_serve import load_config
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "base_model": "test-model",
            "adapter": None,
            "port": 9999,
            "max_tokens": 256,
            "temperature": 0.2,
        }))
        config = load_config(config_path)
        self.assertEqual(config["port"], 9999)
        self.assertEqual(config["base_model"], "test-model")
        import shutil
        shutil.rmtree(tmpdir)

    def test_load_config_defaults(self):
        from mlx_serve import load_config
        config = load_config(Path("/nonexistent/config.json"))
        self.assertEqual(config["port"], 8800)
        self.assertIsNotNone(config["base_model"])


class TestPidFile(unittest.TestCase):
    def test_write_and_read_pid(self):
        from mlx_serve import write_pid, read_pid
        tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".pid")
        tmpfile.close()
        write_pid(tmpfile.name, 12345)
        self.assertEqual(read_pid(tmpfile.name), 12345)
        os.unlink(tmpfile.name)

    def test_read_pid_missing_file(self):
        from mlx_serve import read_pid
        self.assertIsNone(read_pid("/nonexistent/pid"))


if __name__ == "__main__":
    unittest.main()
