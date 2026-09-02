"""Tests for mlx_serve.py — MLX inference server."""

import http.client
import json
import os
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


RUNTIME = {
    "python": "3.12.12",
    "packages": {"mlx": "0.32.2", "mlx-lm": "0.31.3"},
    "module_origins": {
        "mlx": "/venv/site-packages/mlx/__init__.py",
        "mlx_lm": "/venv/site-packages/mlx_lm/__init__.py",
    },
    "requirements_lock_sha256": "sha256:" + "e" * 64,
}


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


class TestRequestValidation(unittest.TestCase):
    def test_inference_request_rejects_unknown_and_unsafe_values(self):
        from mlx_serve import validate_inference_request

        config = {"max_tokens": 64, "temperature": 0.1}
        with self.assertRaisesRegex(ValueError, "unknown field"):
            validate_inference_request({"prompt": "hello", "revoked": True}, config)
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            validate_inference_request({"prompt": "hello", "max_tokens": True}, config)
        with self.assertRaisesRegex(ValueError, "temperature"):
            validate_inference_request(
                {"prompt": "hello", "temperature": float("inf")}, config
            )

    def test_reload_request_requires_exact_single_text_field(self):
        from mlx_serve import validate_reload_request

        self.assertEqual(
            validate_reload_request({"adapter": "reviewed-v1"}), "reviewed-v1"
        )
        with self.assertRaisesRegex(ValueError, "unknown field"):
            validate_reload_request({"adapter": "reviewed-v1", "force": True})
        with self.assertRaisesRegex(ValueError, "non-empty text"):
            validate_reload_request({"adapter": ["reviewed-v1"]})

    def test_handler_rejects_cross_origin_and_non_json_reload(self):
        import mlx_serve

        reloaded = []
        fake_server = types.SimpleNamespace(
            config={}, reload_adapter=lambda adapter: reloaded.append(adapter)
        )

        class QuietHandler(mlx_serve.RequestHandler):
            def log_message(self, _format, *_args):
                pass

        httpd = mlx_serve.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(mlx_serve, "_server", fake_server):
                cases = (
                    ({"Content-Type": "text/plain"}, 400),
                    (
                        {
                            "Content-Type": "application/json",
                            "Host": "attacker.example",
                        },
                        400,
                    ),
                    (
                        {
                            "Content-Type": "application/json",
                            "Origin": "https://attacker.example",
                        },
                        400,
                    ),
                )
                for headers, expected_status in cases:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", httpd.server_port, timeout=2
                    )
                    connection.request(
                        "POST",
                        "/reload",
                        body='{"adapter":"reviewed-v1"}',
                        headers=headers,
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected_status)
                    response.read()
                    connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", httpd.server_port, timeout=2
                )
                connection.request(
                    "GET", "/health", headers={"Host": "attacker.example"}
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", httpd.server_port, timeout=2
                )
                connection.request(
                    "POST",
                    "/reload",
                    body='{"adapter":"reviewed-v1"}',
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
        self.assertEqual(reloaded, ["reviewed-v1"])


class TestConfigLoading(unittest.TestCase):
    def test_load_config(self):
        from mlx_serve import load_config

        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "base_model": "test-model",
                    "adapter": None,
                    "port": 9999,
                    "max_tokens": 256,
                    "temperature": 0.2,
                }
            )
        )
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

    def test_load_config_rejects_malformed_json(self):
        from mlx_serve import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{not-json}\n")
            with self.assertRaisesRegex(ValueError, "Invalid MLX config"):
                load_config(config_path)


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


class TestModelProvenance(unittest.TestCase):
    def test_serving_requires_python_isolated_mode(self):
        from mlx_serve import require_isolated_interpreter

        if sys.flags.isolated:
            self.skipTest("test runner already uses isolated mode")
        with self.assertRaisesRegex(RuntimeError, "isolated mode"):
            require_isolated_interpreter()

    def test_load_model_uses_verified_snapshot_and_exposes_lock(self):
        from mlx_serve import InferenceServer

        loaded = []
        fake_mlx_lm = types.ModuleType("mlx_lm")

        def fake_load(model_path, adapter_path=None):
            loaded.append((model_path, adapter_path))
            return object(), object()

        fake_mlx_lm.load = fake_load
        resolution = {
            "path": "/verified/snapshot",
            "repo_id": "example/model",
            "revision": "a" * 40,
            "lock_sha256": "b" * 64,
        }
        server = InferenceServer(
            {
                "base_model": "example/model",
                "base_model_lock": "mlx/model-lock.json",
                "adapter": None,
            }
        )

        with (
            patch("mlx_serve.require_isolated_interpreter"),
            patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}),
        ):
            with patch(
                "mlx_train.resolve_training_environment", return_value=RUNTIME
            ) as runtime_resolver:
                with patch(
                    "hf_model_lock.resolve_locked_snapshot", return_value=resolution
                ) as resolver:
                    server.load_model()

        resolver.assert_called_once()
        runtime_resolver.assert_called_once_with(sys.executable)
        self.assertEqual(loaded, [("/verified/snapshot", None)])
        self.assertEqual(server.model_revision, "a" * 40)
        self.assertEqual(server.model_lock_sha256, "b" * 64)
        health = server.health()
        self.assertEqual(health["model_revision"], "a" * 40)
        self.assertEqual(health["model_lock_sha256"], "b" * 64)
        self.assertEqual(health["runtime"], RUNTIME)

    def test_reload_rejects_traversal_before_model_load(self):
        from mlx_serve import InferenceServer

        loaded = []
        fake_mlx_lm = types.ModuleType("mlx_lm")
        fake_mlx_lm.load = lambda *args, **kwargs: loaded.append((args, kwargs))
        server = InferenceServer(
            {
                "base_model": "example/model",
                "base_model_lock": "lock.json",
                "adapter": None,
            }
        )
        resolution = {
            "path": "/verified/snapshot",
            "repo_id": "example/model",
            "revision": "a" * 40,
            "lock_sha256": "b" * 64,
        }
        with (
            patch("mlx_serve.require_isolated_interpreter"),
            patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}),
        ):
            with patch(
                "hf_model_lock.resolve_locked_snapshot", return_value=resolution
            ):
                with self.assertRaisesRegex(Exception, "unsafe characters"):
                    server.reload_adapter("../escape")
        self.assertEqual(loaded, [])
        self.assertIsNone(server.adapter_name)

    def test_failed_reload_keeps_previous_state(self):
        from mlx_serve import InferenceServer

        fake_mlx_lm = types.ModuleType("mlx_lm")

        def fail_load(*_args, **_kwargs):
            raise RuntimeError("load failed")

        fake_mlx_lm.load = fail_load
        server = InferenceServer(
            {
                "base_model": "example/model",
                "base_model_lock": "lock.json",
                "adapter": "old",
            }
        )
        server.model = object()
        server.tokenizer = object()
        server.model_revision = "old-revision"
        server.model_lock_sha256 = "old-lock"
        server.adapter_receipt_sha256 = "old-receipt"
        resolution = {
            "path": "/verified/snapshot",
            "repo_id": "example/model",
            "revision": "a" * 40,
            "lock_sha256": "b" * 64,
        }
        artifact = {
            "adapter_path": Path("/verified/adapter"),
            "receipt_sha256": "sha256:" + "c" * 64,
        }
        with (
            patch("mlx_serve.require_isolated_interpreter"),
            patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}),
        ):
            with patch(
                "hf_model_lock.resolve_locked_snapshot", return_value=resolution
            ):
                with patch(
                    "mlx_train.resolve_training_environment", return_value=RUNTIME
                ):
                    with patch(
                        "mlx_train.validate_adapter_artifact", return_value=artifact
                    ) as validator:
                        with self.assertRaisesRegex(RuntimeError, "load failed"):
                            server.reload_adapter("new")
        validator.assert_called_once_with(
            "new",
            model_resolution=resolution,
            runtime_environment=RUNTIME,
            require_serving=True,
        )
        self.assertEqual(server.adapter_name, "old")
        self.assertEqual(server.model_revision, "old-revision")
        self.assertEqual(server.adapter_receipt_sha256, "old-receipt")

    def test_runtime_mismatch_prevents_model_resolution_and_load(self):
        from mlx_serve import InferenceServer

        loaded = []
        fake_mlx_lm = types.ModuleType("mlx_lm")
        fake_mlx_lm.load = lambda *args, **kwargs: loaded.append((args, kwargs))
        server = InferenceServer(
            {
                "base_model": "example/model",
                "base_model_lock": "lock.json",
                "adapter": None,
            }
        )
        with (
            patch("mlx_serve.require_isolated_interpreter"),
            patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}),
        ):
            with patch(
                "mlx_train.resolve_training_environment",
                side_effect=RuntimeError("runtime mismatch"),
            ):
                with patch("hf_model_lock.resolve_locked_snapshot") as resolver:
                    with self.assertRaisesRegex(RuntimeError, "runtime mismatch"):
                        server.load_model()
        resolver.assert_not_called()
        self.assertEqual(loaded, [])
        self.assertIsNone(server.runtime_environment)


if __name__ == "__main__":
    unittest.main()
