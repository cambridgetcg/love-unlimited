"""Tests for mlx_client.py — local inference client + shadow logging."""
import fcntl
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestAskLocal(unittest.TestCase):
    """Test the ask_local function against a mock HTTP server."""

    @classmethod
    def setUpClass(cls):
        """Start a tiny mock server on a random port."""
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                response = {
                    "response": "idle",
                    "tokens_per_sec": 50.0,
                    "model": "test",
                    "adapter": "test-v1",
                    "latency_ms": 10,
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def do_GET(self):
                response = {"status": "ok", "model": "test", "adapter": "test-v1",
                            "uptime_seconds": 100, "requests_served": 5,
                            "avg_tokens_per_sec": 50.0, "memory_mb": 2048}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, format, *args):
                pass  # Suppress log output

        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_ask_local_returns_response(self):
        from mlx_client import ask_local
        result = ask_local("test prompt", system="test system", port=self.port)
        self.assertEqual(result, "idle")

    def test_ask_local_raw_returns_full_json(self):
        from mlx_client import ask_local
        result = ask_local("test", port=self.port, raw=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["response"], "idle")
        self.assertIn("tokens_per_sec", result)

    def test_ask_local_returns_none_on_bad_port(self):
        from mlx_client import ask_local
        result = ask_local("test", port=19999, timeout=0.5)
        self.assertIsNone(result)

    def test_is_available(self):
        from mlx_client import is_available
        self.assertTrue(is_available(port=self.port))

    def test_is_not_available_on_bad_port(self):
        from mlx_client import is_available
        self.assertFalse(is_available(port=19999, timeout=0.5))


class TestShadowLog(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmpfile.close()

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_log_shadow_writes_entry(self):
        from mlx_client import log_shadow
        log_shadow("heartbeat-triage", "test input", "idle", "idle",
                   latency_ms=100, shadow_log_path=self.tmpfile.name)
        with open(self.tmpfile.name) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["integration"], "heartbeat-triage")
        self.assertEqual(entry["local_answer"], "idle")
        self.assertEqual(entry["actual_outcome"], "idle")
        self.assertTrue(entry["agreed"])

    def test_log_shadow_detects_disagreement(self):
        from mlx_client import log_shadow
        log_shadow("heartbeat-triage", "test", "idle", "urgent",
                   shadow_log_path=self.tmpfile.name)
        with open(self.tmpfile.name) as f:
            entry = json.loads(f.readline())
        self.assertFalse(entry["agreed"])

    def test_log_shadow_caps_at_2000(self):
        from mlx_client import log_shadow
        # Write 2005 entries
        for i in range(2005):
            log_shadow("test", f"input {i}", "a", "a",
                       shadow_log_path=self.tmpfile.name)
        with open(self.tmpfile.name) as f:
            lines = f.readlines()
        self.assertLessEqual(len(lines), 2000)

    def test_check_rollback_triggers_on_low_agreement(self):
        from mlx_client import log_shadow, check_rollback
        # Write 50 entries, 40 agreeing (80% < 90% threshold)
        for i in range(40):
            log_shadow("bad-integration", f"in {i}", "a", "a",
                       shadow_log_path=self.tmpfile.name)
        for i in range(10):
            log_shadow("bad-integration", f"in {i}", "a", "b",
                       shadow_log_path=self.tmpfile.name)
        should_rollback = check_rollback("bad-integration",
                                          shadow_log_path=self.tmpfile.name)
        self.assertTrue(should_rollback)

    def test_check_rollback_ok_on_high_agreement(self):
        from mlx_client import check_rollback, log_shadow
        for i in range(50):
            log_shadow("good-integration", f"in {i}", "a", "a",
                       shadow_log_path=self.tmpfile.name)
        should_rollback = check_rollback("good-integration",
                                          shadow_log_path=self.tmpfile.name)
        self.assertFalse(should_rollback)


if __name__ == "__main__":
    unittest.main()
