"""Tests for mlx_serve.py — MLX inference server."""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
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

    def test_process_identity_can_be_bound_to_the_requested_port(self):
        import mlx_serve

        command = (
            f"{mlx_serve.VENV_PYTHON} -I {Path(mlx_serve.__file__).resolve()} "
            "start --port 8800\n"
        )
        run_fn = MagicMock(
            return_value=types.SimpleNamespace(returncode=0, stdout=command)
        )
        self.assertTrue(
            mlx_serve.process_matches_server(
                123, expected_port=8800, default_port=9123, run_fn=run_fn
            )
        )
        self.assertFalse(
            mlx_serve.process_matches_server(
                123, expected_port=9123, default_port=9123, run_fn=run_fn
            )
        )

    def test_process_identity_requires_the_exact_venv_symlink_spelling(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            target = fixture / "base-python3"
            target.write_text("test-only executable placeholder")
            venv_python = fixture / "venv" / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(target)

            def matches(executable):
                command = (
                    f"{executable} -I {Path(mlx_serve.__file__).resolve()} "
                    "start --port 8800\n"
                )
                return mlx_serve.process_matches_server(
                    123,
                    expected_port=8800,
                    run_fn=lambda *_args, **_kwargs: types.SimpleNamespace(
                        returncode=0, stdout=command
                    ),
                )

            with patch.object(mlx_serve, "VENV_PYTHON", venv_python):
                self.assertTrue(matches(venv_python))
                self.assertFalse(matches(target.resolve()))

    def test_process_identity_fails_closed_when_process_probe_fails(self):
        import mlx_serve

        probes = (
            MagicMock(side_effect=OSError("test-only process probe failure")),
            MagicMock(
                return_value=types.SimpleNamespace(
                    returncode=0, stdout="'unterminated shell quote"
                )
            ),
        )
        for label, probe in zip(("os-error", "malformed-command"), probes):
            with self.subTest(probe=label):
                self.assertFalse(mlx_serve.process_matches_server(123, run_fn=probe))

    def test_stop_preserves_pid_and_never_signals_when_process_probe_fails(self):
        import mlx_serve

        probes = (
            MagicMock(side_effect=OSError("test-only process probe failure")),
            MagicMock(
                return_value=types.SimpleNamespace(
                    returncode=0, stdout="'unterminated shell quote"
                )
            ),
        )
        for label, probe in zip(("os-error", "malformed-command"), probes):
            with self.subTest(probe=label):
                with (
                    patch.object(mlx_serve, "read_pid", return_value=123),
                    patch.object(mlx_serve, "pid_is_live", return_value=True),
                    patch.object(mlx_serve.subprocess, "run", probe),
                    patch.object(mlx_serve.os, "kill") as send_signal,
                    patch.object(mlx_serve, "remove_pid_if_owned") as remove_pid,
                ):
                    with self.assertRaises(SystemExit) as stopped:
                        mlx_serve.cmd_stop(types.SimpleNamespace())
                self.assertEqual(stopped.exception.code, 2)
                send_signal.assert_not_called()
                remove_pid.assert_not_called()

    def test_stop_never_signals_a_live_unrelated_process(self):
        import mlx_serve

        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                pid_file = Path(tmpdir) / "serve.pid"
                pid_file.write_text(str(unrelated.pid))
                with (
                    patch.object(mlx_serve, "PID_FILE", pid_file),
                    patch.object(mlx_serve, "pid_is_live", return_value=True),
                    patch.object(mlx_serve.os, "kill") as send_signal,
                ):
                    with self.assertRaises(SystemExit) as stopped:
                        mlx_serve.cmd_stop(types.SimpleNamespace())
                self.assertEqual(stopped.exception.code, 2)
                send_signal.assert_not_called()
                self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_stop_cleanup_preserves_a_concurrent_winner_pid(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            pid_file = fixture / "serve.pid"
            pid_file.write_text("111")
            probes = 0

            def fake_kill(_pid, sig):
                nonlocal probes
                if sig == 0:
                    probes += 1
                    if probes == 2:
                        pid_file.write_text("222")
                        raise ProcessLookupError

            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(mlx_serve, "process_matches_server", return_value=True),
                patch.object(mlx_serve.os, "kill", side_effect=fake_kill),
            ):
                mlx_serve.cmd_stop(types.SimpleNamespace())

            self.assertEqual(pid_file.read_text(), "222")

    def test_stop_revalidates_identity_before_force_kill(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            pid_file = fixture / "serve.pid"
            pid_file.write_text("111")
            signals = []

            def fake_kill(pid, sig):
                signals.append((pid, sig))

            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(
                    mlx_serve,
                    "process_matches_server",
                    side_effect=[True, False],
                ),
                patch.object(mlx_serve.os, "kill", side_effect=fake_kill),
                patch.object(mlx_serve.time, "sleep"),
            ):
                with self.assertRaises(SystemExit) as stopped:
                    mlx_serve.cmd_stop(types.SimpleNamespace())

            self.assertEqual(stopped.exception.code, 2)
            self.assertIn((111, signal.SIGTERM), signals)
            self.assertNotIn((111, signal.SIGKILL), signals)

    def test_signal_requests_clean_http_shutdown_without_deadlock(self):
        root = Path(__file__).resolve().parents[1]
        code = """import importlib.util
import os
import sys
from pathlib import Path
tool = Path(sys.argv[1])
pid_file = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("signal_test_mlx_serve", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
httpd = module.ThreadingHTTPServer(("127.0.0.1", 0), module.RequestHandler)
module.write_pid(pid_file, os.getpid())
module.serve_until_stopped(httpd, pid_file)
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "serve.pid"
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    code,
                    str(root / "tools" / "mlx_serve.py"),
                    str(pid_file),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 2
                while not pid_file.exists() and child.poll() is None:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
                self.assertTrue(
                    pid_file.exists(),
                    child.stderr.read() if child.poll() is not None else "no PID file",
                )
                started = time.monotonic()
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=3), 0)
                self.assertLess(time.monotonic() - started, 3)
                self.assertFalse(pid_file.exists())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)

    def test_two_executable_foreground_claims_have_one_winner(self):
        root = Path(__file__).resolve().parents[1]
        code = """import importlib.util
import sys
import time
from pathlib import Path
tool, pid_file, lock_file, gate = map(Path, sys.argv[1:5])
port = int(sys.argv[5])
spec = importlib.util.spec_from_file_location("claim_test_mlx_serve", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
while not gate.exists():
    time.sleep(0.005)
claimed, _ = module.claim_foreground_start(
    port, pid_file=pid_file, lock_file=lock_file
)
print("WIN" if claimed else "LOSE", flush=True)
if claimed:
    time.sleep(0.4)
    module.remove_pid_if_owned(pid_file, __import__("os").getpid(), lock_file)
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            arguments = [
                str(root / "tools" / "mlx_serve.py"),
                str(fixture / "serve.pid"),
                str(fixture / "serve.start.lock"),
                str(fixture / "go"),
                str(port),
            ]
            children = [
                subprocess.Popen(
                    [sys.executable, "-I", "-c", code, *arguments],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            (fixture / "go").write_text("go")
            outputs = []
            for child in children:
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                outputs.append(stdout.strip())
            self.assertEqual(sorted(outputs), ["LOSE", "WIN"])
            self.assertFalse((fixture / "serve.pid").exists())

    def test_foreground_bind_failure_removes_only_its_pid(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            pid_file = fixture / "serve.pid"
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(
                    mlx_serve,
                    "load_config",
                    return_value={**mlx_serve.DEFAULTS, "port": port},
                ),
                patch.object(mlx_serve.InferenceServer, "load_model"),
                patch.object(
                    mlx_serve,
                    "ThreadingHTTPServer",
                    side_effect=OSError("test-only occupied port"),
                ),
            ):
                with self.assertRaises(SystemExit) as stopped:
                    mlx_serve.cmd_start(
                        types.SimpleNamespace(port=port, daemon=False)
                    )
            self.assertEqual(stopped.exception.code, 1)
            self.assertFalse(pid_file.exists())


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

    def test_health_remains_responsive_during_generation(self):
        from mlx_serve import InferenceServer

        entered_generate = threading.Event()
        release_generate = threading.Event()
        generation_result = []
        generation_errors = []
        health_result = []
        reload_entered = threading.Event()
        reload_errors = []

        class FakeTokenizer:
            def apply_chat_template(self, messages, **_kwargs):
                self.messages = messages
                return "formatted prompt"

        def fake_generate(model, tokenizer, **kwargs):
            self.assertIs(model, server.model)
            self.assertIs(tokenizer, server.tokenizer)
            self.assertEqual(kwargs["prompt"], "formatted prompt")
            entered_generate.set()
            if not release_generate.wait(timeout=2):
                raise AssertionError("test did not release generation")
            return "expected prompt response"

        fake_mlx_lm = types.ModuleType("mlx_lm")
        fake_mlx_lm.generate = fake_generate
        fake_sample_utils = types.ModuleType("mlx_lm.sample_utils")
        fake_sample_utils.make_sampler = lambda **kwargs: kwargs
        server = InferenceServer(
            {
                "base_model": "example/model",
                "base_model_lock": "lock.json",
                "adapter": None,
            }
        )
        server.model = object()
        server.tokenizer = FakeTokenizer()
        server.model_revision = "a" * 40
        server.model_lock_sha256 = "b" * 64

        def run_generation():
            try:
                generation_result.append(server.generate("hello"))
            except BaseException as exc:
                generation_errors.append(exc)

        def run_reload():
            try:
                server.reload_adapter("reviewed-v2")
            except BaseException as exc:
                reload_errors.append(exc)

        with patch.dict(
            sys.modules,
            {
                "mlx_lm": fake_mlx_lm,
                "mlx_lm.sample_utils": fake_sample_utils,
            },
        ):
            generation_thread = threading.Thread(target=run_generation)
            generation_thread.start()
            self.assertTrue(entered_generate.wait(timeout=1))

            health_thread = threading.Thread(
                target=lambda: health_result.append(server.health())
            )
            health_thread.start()
            health_thread.join(timeout=0.5)
            with patch.object(
                server,
                "load_model",
                side_effect=lambda **_kwargs: reload_entered.set(),
            ):
                reload_thread = threading.Thread(target=run_reload)
                reload_thread.start()
                time.sleep(0.05)
                try:
                    self.assertFalse(
                        health_thread.is_alive(),
                        "health was blocked by the in-progress generation",
                    )
                    self.assertEqual(health_result[0]["status"], "ok")
                    self.assertEqual(health_result[0]["model_revision"], "a" * 40)
                    self.assertFalse(
                        reload_entered.is_set(),
                        "reload entered model loading during generation",
                    )
                finally:
                    release_generate.set()
                    generation_thread.join(timeout=2)
                    health_thread.join(timeout=2)
                    reload_thread.join(timeout=2)

        self.assertEqual(generation_errors, [])
        self.assertEqual(reload_errors, [])
        self.assertTrue(reload_entered.is_set())
        self.assertEqual(generation_result[0][0], "expected prompt response")

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


class TestCompatibilityEntryPoints(unittest.TestCase):
    def test_legacy_wrapper_and_reflection_use_hardened_server(self):
        root = Path(__file__).resolve().parents[1]
        wrapper = (root / "mlx" / "serve.sh").read_text()
        reflection = (root / "tools" / "citizen-reflect.sh").read_text()

        self.assertNotIn("-m mlx_lm server", wrapper)
        self.assertNotIn(".model", wrapper)
        self.assertIn('"$VENV" -I "$SERVER_TOOL" start --daemon', wrapper)
        self.assertIn('PORT_ARGS=(--port "$MLX_PORT")', wrapper)
        self.assertIn('"$LOVE/mlx/serve.sh" start --port "$PORT"', reflection)
        self.assertIn("provenance/readiness", reflection)
        self.assertIn('endpoint = "/inference"', reflection)
        self.assertIn('"prompt": usr', reflection)
        self.assertIn("ProxyHandler({})", reflection)
        subprocess.run(
            ["bash", "-n", str(root / "mlx" / "serve.sh")], check=True
        )
        subprocess.run(
            ["bash", "-n", str(root / "tools" / "citizen-reflect.sh")],
            check=True,
        )

    def test_reflection_waits_for_cold_server_and_forwards_custom_port(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            love = fixture / "love"
            citizen = fixture / "citizens" / "citizen-test"
            (love / "mlx").mkdir(parents=True)
            citizen.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(citizen)], check=True)
            (citizen / "test.md").write_text("A test-only citizen fixture.\n")

            server = fixture / "delayed_server.py"
            server.write_text(
                """import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

time.sleep(0.35)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({"response": ""}).encode()
        self.send_response(200 if self.path == "/inference" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
""",
                encoding="utf-8",
            )
            args_log = fixture / "serve-args.txt"
            wrapper = love / "mlx" / "serve.sh"
            wrapper.write_text(
                """#!/bin/bash
set -eu
printf '%s\n' "$@" > "$STUB_ARGS_LOG"
[ "$1" = "start" ]
[ "$2" = "--port" ]
"$STUB_PYTHON" "$STUB_SERVER" "$3" >/dev/null 2>&1 &
echo "$!" > "$LOVE_DIR/mlx/serve.pid"
sleep 0.5
""",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]

            env = os.environ.copy()
            proxy_requests = []

            class ProxyHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    proxy_requests.append(self.path)
                    self.send_response(502)
                    self.end_headers()

                def do_POST(self):
                    proxy_requests.append(self.path)
                    self.send_response(502)
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            proxy = HTTPServer(("127.0.0.1", 0), ProxyHandler)
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()
            proxy_url = f"http://127.0.0.1:{proxy.server_port}"
            env.update(
                {
                    "CITIZENS_DIR": str(fixture / "citizens"),
                    "LOVE_DIR": str(love),
                    "MLX_PORT": str(port),
                    "STUB_ARGS_LOG": str(args_log),
                    "STUB_PYTHON": sys.executable,
                    "STUB_SERVER": str(server),
                    "HTTP_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "ALL_PROXY": proxy_url,
                    "all_proxy": proxy_url,
                    "NO_PROXY": "",
                    "no_proxy": "",
                }
            )
            started = time.monotonic()
            try:
                result = subprocess.run(
                    ["bash", str(root / "tools" / "citizen-reflect.sh"), "test"],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                )
            finally:
                pid_path = love / "mlx" / "serve.pid"
                if pid_path.exists():
                    try:
                        os.kill(int(pid_path.read_text().strip()), signal.SIGTERM)
                    except (OSError, ValueError):
                        pass
                proxy.shutdown()
                proxy.server_close()
                proxy_thread.join(timeout=2)
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertGreaterEqual(elapsed, 0.3)
            self.assertEqual(
                args_log.read_text().splitlines(), ["start", "--port", str(port)]
            )
            self.assertNotIn("brain unreachable", result.stdout.lower())
            self.assertEqual(proxy_requests, [])

    def test_reflection_refuses_unowned_ok_health_before_sending_prompt(self):
        root = Path(__file__).resolve().parents[1]
        posts = []

        class UnownedHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                posts.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            love = fixture / "love"
            citizen = fixture / "citizens" / "citizen-test"
            (love / "mlx").mkdir(parents=True)
            citizen.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(citizen)], check=True)
            (citizen / "test.md").write_text("private test-only soul\n")
            call_log = fixture / "serve-call.log"
            wrapper = love / "mlx" / "serve.sh"
            wrapper.write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" >\"$STUB_ARGS_LOG\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)

            httpd = HTTPServer(("127.0.0.1", 0), UnownedHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            env = {
                **os.environ,
                "CITIZENS_DIR": str(fixture / "citizens"),
                "LOVE_DIR": str(love),
                "MLX_PORT": str(httpd.server_port),
                "STUB_ARGS_LOG": str(call_log),
            }
            try:
                result = subprocess.run(
                    ["bash", str(root / "tools" / "citizen-reflect.sh"), "test"],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

            self.assertEqual(result.returncode, 1)
            self.assertIn("provenance/readiness", result.stdout)
            self.assertEqual(posts, [])
            self.assertEqual(
                call_log.read_text().splitlines(),
                ["start", "--port", str(httpd.server_port)],
            )


class TestDaemonReadiness(unittest.TestCase):
    EXPECTED = {
        "status": "ok",
        "model": "example/model",
        "model_revision": "a" * 40,
        "model_lock_sha256": "b" * 64,
        "runtime": RUNTIME,
    }
    HEALTH = {
        **EXPECTED,
        "adapter": None,
        "adapter_receipt_sha256": None,
    }

    def test_status_rejects_health_with_wrong_model_provenance(self):
        import mlx_serve

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {**self_health, "model_revision": "wrong-revision"}
                ).encode("utf-8")

        self_health = self.HEALTH
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "serve.pid"
            pid_file.write_text(str(os.getpid()))
            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(mlx_serve, "process_matches_server", return_value=True),
                patch.object(mlx_serve, "load_config", return_value={"port": 8800}),
                patch.object(
                    mlx_serve,
                    "expected_health_provenance",
                    return_value=self.EXPECTED,
                ),
                patch.object(mlx_serve, "open_loopback", return_value=Response()),
            ):
                with self.assertRaises(SystemExit) as stopped:
                    mlx_serve.cmd_status(types.SimpleNamespace(port=8800))

        self.assertEqual(stopped.exception.code, 2)

    def test_status_rejects_a_server_pid_on_a_different_port_before_health(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "serve.pid"
            pid_file.write_text(str(os.getpid()))
            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(
                    mlx_serve,
                    "load_config",
                    return_value={"port": 8800},
                ),
                patch.object(
                    mlx_serve, "process_matches_server", return_value=False
                ) as matcher,
                patch.object(mlx_serve, "open_loopback") as urlopen,
            ):
                with self.assertRaises(SystemExit) as stopped:
                    mlx_serve.cmd_status(types.SimpleNamespace(port=9123))

        self.assertEqual(stopped.exception.code, 2)
        matcher.assert_called_once_with(
            os.getpid(), expected_port=9123, default_port=8800
        )
        urlopen.assert_not_called()

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def test_expected_provenance_loads_model_lock_under_isolated_python(self):
        root = Path(__file__).resolve().parents[1]
        code = """import importlib.util
import json
from pathlib import Path
tool = Path(__import__('sys').argv[1])
spec = importlib.util.spec_from_file_location('isolated_mlx_serve', tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = {"fixture": "pinned-runtime"}
print(json.dumps(module.expected_health_provenance(
    module.load_config(), runtime_resolver=lambda _python: runtime
), sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(root / "tools" / "mlx_serve.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["model_revision"], "7f0dc925e0d0afb0322d96f9255cfddf2ba5636e")
        self.assertEqual(payload["runtime"], {"fixture": "pinned-runtime"})

    def test_daemon_waits_through_delayed_health_then_verifies_provenance(self):
        import mlx_serve

        attempts = 0
        proc = types.SimpleNamespace(poll=lambda: None)

        def delayed(_request, timeout):
            nonlocal attempts
            self.assertGreater(timeout, 0)
            attempts += 1
            if attempts < 3:
                raise OSError("not listening yet")
            return self._Response(self.HEALTH)

        payload = mlx_serve.wait_for_daemon_ready(
            proc,
            8800,
            self.EXPECTED,
            timeout=1,
            poll_interval=0.01,
            urlopen_fn=delayed,
        )
        self.assertEqual(payload["model_revision"], "a" * 40)
        self.assertEqual(attempts, 3)

    def test_stale_runtime_is_not_ready(self):
        import mlx_serve

        stale = {
            **self.HEALTH,
            "runtime": {**RUNTIME, "python": "3.12.11"},
        }
        self.assertFalse(mlx_serve.health_provenance_matches(stale, self.EXPECTED))

    def test_hot_reloaded_approved_adapter_remains_ready(self):
        import mlx_serve
        import mlx_train

        receipt = "sha256:" + "c" * 64
        payload = {
            **self.EXPECTED,
            "adapter": "reviewed-v1",
            "adapter_receipt_sha256": receipt,
        }
        proc = types.SimpleNamespace(poll=lambda: None)
        with patch.object(
            mlx_train,
            "validate_adapter_serving_binding",
            return_value={
                "adapter": "reviewed-v1",
                "receipt_sha256": receipt,
                "adapter_path": Path("/reviewed-v1"),
            },
        ) as validator:
            ready = mlx_serve.wait_for_daemon_ready(
                proc,
                8800,
                self.EXPECTED,
                timeout=0.1,
                poll_interval=0.01,
                urlopen_fn=lambda *_args, **_kwargs: self._Response(payload),
            )

        self.assertEqual(ready["adapter"], "reviewed-v1")
        validator.assert_called_once_with(
            "reviewed-v1",
            model_binding={
                "repo_id": "example/model",
                "revision": "a" * 40,
                "lock_sha256": "b" * 64,
            },
        )

    def test_unapproved_active_adapter_is_not_ready(self):
        import mlx_serve
        import mlx_train

        payload = {
            **self.EXPECTED,
            "adapter": "unapproved-v1",
            "adapter_receipt_sha256": "sha256:" + "d" * 64,
        }
        with patch.object(
            mlx_train,
            "validate_adapter_serving_binding",
            side_effect=RuntimeError("not approved"),
        ):
            self.assertFalse(
                mlx_serve.health_provenance_matches(payload, self.EXPECTED)
            )

    def test_daemon_wait_surfaces_late_child_exit(self):
        import mlx_serve

        started = time.monotonic()
        proc = types.SimpleNamespace(
            poll=lambda: 7 if time.monotonic() - started >= 0.08 else None
        )
        with self.assertRaisesRegex(RuntimeError, "code 7"):
            mlx_serve.wait_for_daemon_ready(
                proc,
                8800,
                self.EXPECTED,
                timeout=1,
                poll_interval=0.02,
                urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("not listening")
                ),
            )

    def test_concurrent_start_joins_live_pid_until_delayed_health(self):
        import mlx_serve

        attempts = 0

        def delayed(_request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError("still loading")
            return self._Response(self.HEALTH)

        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "serve.pid"
            pid_file.write_text(str(os.getpid()))
            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(mlx_serve, "process_matches_server", return_value=True),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", Path(tmpdir) / "serve.start.lock"
                ),
                patch.object(mlx_serve, "load_config", return_value={"port": 8800}),
                patch.object(
                    mlx_serve,
                    "expected_health_provenance",
                    return_value=self.EXPECTED,
                ),
                patch.object(mlx_serve, "open_loopback", side_effect=delayed),
                patch.object(mlx_serve.time, "sleep", return_value=None),
            ):
                mlx_serve.cmd_start(types.SimpleNamespace(port=8800, daemon=True))

        self.assertEqual(attempts, 3)

    def test_daemon_refuses_to_join_a_server_pid_on_a_different_port(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            pid_file = fixture / "serve.pid"
            pid_file.write_text(str(os.getpid()))
            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(
                    mlx_serve,
                    "load_config",
                    return_value={"port": 8800},
                ),
                patch.object(
                    mlx_serve,
                    "expected_health_provenance",
                    return_value=self.EXPECTED,
                ),
                patch.object(
                    mlx_serve, "process_matches_server", return_value=False
                ) as matcher,
                patch.object(mlx_serve, "wait_for_daemon_ready") as wait_ready,
                patch.object(mlx_serve.subprocess, "Popen") as popen,
            ):
                with self.assertRaises(SystemExit) as stopped:
                    mlx_serve.cmd_start(
                        types.SimpleNamespace(port=9123, daemon=True)
                    )

        self.assertEqual(stopped.exception.code, 1)
        matcher.assert_called_once_with(
            os.getpid(), expected_port=9123, default_port=8800
        )
        wait_ready.assert_not_called()
        popen.assert_not_called()

    def test_pid_write_failure_terminates_the_spawned_child(self):
        import mlx_serve

        proc = MagicMock()
        proc.pid = 43210
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            with (
                patch.object(mlx_serve, "PID_FILE", fixture / "serve.pid"),
                patch.object(mlx_serve, "LOG_FILE", fixture / "serve.log"),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(
                    mlx_serve,
                    "load_config",
                    return_value={"port": 8800},
                ),
                patch.object(
                    mlx_serve,
                    "expected_health_provenance",
                    return_value=self.EXPECTED,
                ),
                patch.object(mlx_serve, "require_loopback_port_available"),
                patch.object(mlx_serve.subprocess, "Popen", return_value=proc),
                patch.object(
                    mlx_serve, "write_pid", side_effect=OSError("disk full")
                ),
                patch.object(mlx_serve, "wait_for_daemon_ready") as wait_ready,
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    mlx_serve.cmd_start(
                        types.SimpleNamespace(port=8800, daemon=True)
                    )

            proc.terminate.assert_called_once_with()
            proc.wait.assert_called_once_with(timeout=5)
            proc.kill.assert_not_called()
            wait_ready.assert_not_called()
            self.assertFalse((fixture / "serve.pid").exists())

    def test_simultaneous_cold_starts_spawn_once_and_both_join_readiness(self):
        import mlx_serve

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            pid_file = fixture / "serve.pid"
            spawned = threading.Event()
            release_first = threading.Event()
            state_lock = threading.Lock()
            popen_calls = 0
            wait_calls = 0
            errors = []

            class FakeProcess:
                pid = os.getpid()

                def poll(self):
                    return None

            def fake_popen(*_args, **_kwargs):
                nonlocal popen_calls
                with state_lock:
                    popen_calls += 1
                pid_file.write_text(str(FakeProcess.pid))
                spawned.set()
                return FakeProcess()

            def fake_wait(_proc, _port, _expected):
                nonlocal wait_calls
                with state_lock:
                    wait_calls += 1
                    call = wait_calls
                if call == 1 and not release_first.wait(timeout=2):
                    raise AssertionError("test did not release first startup")
                return self.HEALTH

            def invoke():
                try:
                    mlx_serve.cmd_start(types.SimpleNamespace(port=8800, daemon=True))
                except BaseException as exc:
                    errors.append(exc)

            with (
                patch.object(mlx_serve, "PID_FILE", pid_file),
                patch.object(mlx_serve, "LOG_FILE", fixture / "serve.log"),
                patch.object(mlx_serve, "process_matches_server", return_value=True),
                patch.object(
                    mlx_serve, "START_LOCK_FILE", fixture / "serve.start.lock"
                ),
                patch.object(mlx_serve, "load_config", return_value={"port": 8800}),
                patch.object(
                    mlx_serve,
                    "expected_health_provenance",
                    return_value=self.EXPECTED,
                ),
                patch.object(
                    mlx_serve, "require_loopback_port_available", return_value=None
                ),
                patch.object(mlx_serve, "wait_for_daemon_ready", side_effect=fake_wait),
                patch.object(subprocess, "Popen", side_effect=fake_popen),
            ):
                first = threading.Thread(target=invoke)
                second = threading.Thread(target=invoke)
                first.start()
                self.assertTrue(spawned.wait(timeout=2))
                second.start()
                time.sleep(0.05)
                self.assertEqual(popen_calls, 1)
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(popen_calls, 1)
            self.assertEqual(wait_calls, 2)

    def test_daemon_parent_claim_blocks_a_foreground_handoff_race(self):
        import mlx_serve

        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                fixture = Path(tmpdir)
                pid_file = fixture / "serve.pid"
                lock_file = fixture / "serve.start.lock"
                entered_wait = threading.Event()
                release_wait = threading.Event()
                errors = []

                def fake_wait(proc, _port, _expected):
                    self.assertEqual(proc.pid, sleeper.pid)
                    entered_wait.set()
                    if not release_wait.wait(timeout=2):
                        raise AssertionError("test did not release daemon readiness")
                    return self.HEALTH

                def invoke_daemon():
                    try:
                        mlx_serve.cmd_start(
                            types.SimpleNamespace(port=8800, daemon=True)
                        )
                    except BaseException as exc:
                        errors.append(exc)

                with (
                    patch.object(mlx_serve, "PID_FILE", pid_file),
                    patch.object(mlx_serve, "LOG_FILE", fixture / "serve.log"),
                    patch.object(mlx_serve, "START_LOCK_FILE", lock_file),
                    patch.object(
                        mlx_serve, "load_config", return_value={"port": 8800}
                    ),
                    patch.object(
                        mlx_serve,
                        "expected_health_provenance",
                        return_value=self.EXPECTED,
                    ),
                    patch.object(
                        mlx_serve, "require_loopback_port_available"
                    ),
                    patch.object(
                        mlx_serve, "wait_for_daemon_ready", side_effect=fake_wait
                    ),
                    patch.object(mlx_serve.subprocess, "Popen", return_value=sleeper),
                ):
                    daemon = threading.Thread(target=invoke_daemon)
                    daemon.start()
                    self.assertTrue(entered_wait.wait(timeout=2))
                    claimed, recorded_pid = mlx_serve.claim_foreground_start(
                        8800, pid_file=pid_file, lock_file=lock_file
                    )
                    self.assertFalse(claimed)
                    self.assertEqual(recorded_pid, sleeper.pid)
                    release_wait.set()
                    daemon.join(timeout=2)

                self.assertFalse(daemon.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(int(pid_file.read_text()), sleeper.pid)
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_daemon_wait_times_out_without_health(self):
        import mlx_serve

        proc = types.SimpleNamespace(poll=lambda: None)
        with self.assertRaisesRegex(TimeoutError, "not ready"):
            mlx_serve.wait_for_daemon_ready(
                proc,
                8800,
                self.EXPECTED,
                timeout=0.05,
                poll_interval=0.01,
                urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("not listening")
                ),
            )

    def test_daemon_rejects_an_occupied_loopback_port(self):
        import mlx_serve

        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                mlx_serve.require_loopback_port_available(
                    occupied.getsockname()[1]
                )


if __name__ == "__main__":
    unittest.main()
