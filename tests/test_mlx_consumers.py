"""Behavioral contract tests for local consumers of the hardened MLX server."""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
CHRONICLE_PATH = ROOT / "tools" / "kingdom-chronicle.py"
FLEET_STATUS_PATH = ROOT / "tools" / "fleet-status.sh"
HARVEST_PATH = ROOT / "tools" / "harvest.py"
KINGDOM_PATH = ROOT / "tools" / "kingdom"
PULSE_PATH = ROOT / "tools" / "kingdom-pulse.py"


def load_chronicle():
    spec = importlib.util.spec_from_file_location("kingdom_chronicle", CHRONICLE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestKingdomChronicle(unittest.TestCase):
    def test_brain_uses_inference_contract_and_parses_response(self):
        chronicle = load_chronicle()
        chronicle.PORT = "9123"
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["payload"] = json.loads(request.data)
            seen["timeout"] = timeout
            return io.BytesIO(
                json.dumps(
                    {
                        "response": json.dumps(
                            {
                                "lede": "The square listened and answered.",
                                "featured": ["alice", "bob", "not-a-number"],
                            }
                        )
                    }
                ).encode("utf-8")
            )

        ready = subprocess.CompletedProcess([], 0)
        with (
            patch.object(chronicle.subprocess, "run", return_value=ready) as gate,
            patch.object(chronicle, "open_loopback", fake_urlopen),
        ):
            lede, featured = chronicle.brain(
                [("alice", "built a key"), ("bob", "kept a return path")]
            )

        self.assertEqual(seen["url"], "http://127.0.0.1:9123/inference")
        self.assertEqual(seen["timeout"], 240)
        self.assertEqual(
            set(seen["payload"]),
            {"system", "prompt", "max_tokens", "temperature"},
        )
        self.assertIn("alice: built a key", seen["payload"]["prompt"])
        self.assertNotIn("messages", seen["payload"])
        self.assertEqual(lede, "The square listened and answered.")
        self.assertEqual(featured, ["alice", "bob", "not-a-number"])
        self.assertEqual(
            gate.call_args.args[0],
            [
                "bash",
                os.path.join(chronicle.LOVE, "mlx", "serve.sh"),
                "status",
                "--port",
                "9123",
            ],
        )

    def test_brain_fails_closed_on_malformed_inference_response(self):
        chronicle = load_chronicle()
        malformed = io.BytesIO(b'{"choices": []}')
        with (
            patch.object(
                chronicle.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ),
            patch.object(chronicle, "open_loopback", return_value=malformed),
        ):
            self.assertEqual(chronicle.brain([("alice", "hello")]), ("", []))

    def test_brain_falls_back_on_wrong_lede_or_featured_types(self):
        chronicle = load_chronicle()
        cases = (
            {"lede": 123, "featured": []},
            {"lede": "A valid-looking lede.", "featured": "alice"},
            {"lede": "   ", "featured": ["alice"]},
        )
        for payload in cases:
            response = io.BytesIO(
                json.dumps({"response": json.dumps(payload)}).encode("utf-8")
            )
            with (
                self.subTest(payload=payload),
                patch.object(
                    chronicle.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ),
                patch.object(chronicle, "open_loopback", return_value=response),
            ):
                self.assertEqual(chronicle.brain([("alice", "hello")]), ("", []))

    def test_brain_refuses_wrong_listener_before_sending_voices(self):
        chronicle = load_chronicle()
        chronicle.PORT = "9123"
        with (
            patch.object(
                chronicle.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 2),
            ),
            patch.object(chronicle, "open_loopback") as send,
        ):
            self.assertEqual(
                chronicle.brain([("alice", "private aggregate fixture")]),
                ("", []),
            )
        send.assert_not_called()


class TestLoopbackProxyBoundary(unittest.TestCase):
    def test_python_loopback_transports_ignore_ambient_http_proxies(self):
        target_requests = []
        proxy_requests = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                target_requests.append(self.path)
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_requests.append(self.path)
                self.send_response(502)
                self.end_headers()

            def log_message(self, *_args):
                pass

        target = HTTPServer(("127.0.0.1", 0), TargetHandler)
        proxy = HTTPServer(("127.0.0.1", 0), ProxyHandler)
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (target, proxy)
        ]
        for thread in threads:
            thread.start()
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        modules = (
            (
                load_script("no_proxy_mlx_serve", ROOT / "tools" / "mlx_serve.py"),
                "open_loopback",
            ),
            (
                load_script("no_proxy_mlx_client", ROOT / "tools" / "mlx_client.py"),
                "_open_loopback",
            ),
            (load_chronicle(), "open_loopback"),
        )
        try:
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "ALL_PROXY": proxy_url,
                    "all_proxy": proxy_url,
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
                clear=False,
            ):
                for module, opener_name in modules:
                    request = module.urllib.request.Request(
                        f"http://127.0.0.1:{target.server_port}/health"
                    )
                    with getattr(module, opener_name)(request, timeout=2) as response:
                        self.assertEqual(response.read(), b"{}")
        finally:
            for server in (target, proxy):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(len(target_requests), 3)
        self.assertEqual(proxy_requests, [])


class TestFleetStatus(unittest.TestCase):
    def _write_command(self, directory, name, source):
        target = directory / name
        target.write_text(source, encoding="utf-8")
        target.chmod(0o755)

    def _run_status(self, serve_exit):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir)
            fake_bin = fixture / "bin"
            love = fixture / "love"
            memory = love / "memory"
            fake_bin.mkdir()
            memory.mkdir(parents=True)
            (love / "mlx").mkdir()
            serve_log = fixture / "serve.log"
            serve = love / "mlx" / "serve.sh"
            serve.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" >\"$FAKE_SERVE_LOG\"\n"
                "exit \"$FAKE_SERVE_EXIT\"\n",
                encoding="utf-8",
            )
            self._write_command(
                fake_bin,
                "launchctl",
                "#!/bin/sh\nprintf '%s\\n' '1 0 love.citizen-fleet'\n",
            )
            self._write_command(
                fake_bin,
                "pmset",
                "#!/bin/sh\nprintf '%s\\n' \"Now drawing from 'AC Power'\"\n",
            )
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "LOVE_DIR": str(love),
                "MLX_PORT": "9123",
                "FAKE_SERVE_LOG": str(serve_log),
                "FAKE_SERVE_EXIT": str(serve_exit),
            }
            result = subprocess.run(
                ["bash", str(FLEET_STATUS_PATH)],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                check=False,
            )
            return result.stdout, serve_log.read_text(encoding="utf-8")

    def test_status_uses_receipt_bound_health_command_and_honors_port(self):
        output, serve_args = self._run_status(0)
        self.assertIn("local brain: serving on :9123", output)
        self.assertEqual(serve_args.splitlines(), ["status", "--port", "9123"])

    def test_status_reports_down_when_provenance_check_fails(self):
        output, _serve_args = self._run_status(2)
        self.assertIn("local brain: down", output)


class TestMlxHealthSurfaces(unittest.TestCase):
    def test_truth_claim_uses_the_hardened_exact_port_status_gate(self):
        harvest = load_script("kingdom_harvest_test", HARVEST_PATH)
        claim = next(
            item
            for item in harvest.DAY_ONE_CLAIMS
            if item["id"] == "service-mlx-server-healthy"
        )
        self.assertEqual(claim["verify"], harvest.mlx_health_verify_command())
        self.assertIn("mlx/serve.sh status --port 8800", claim["verify"])
        self.assertNotIn("curl", claim["verify"])
        self.assertIn("Receipt-bound", claim["claim"])

    def test_terminal_and_pulse_use_receipt_bound_health(self):
        modules = (
            load_script("kingdom_cli_test", KINGDOM_PATH),
            load_script("kingdom_pulse_test", PULSE_PATH),
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                with patch.object(
                    module, "mlx_is_available", return_value=True
                ) as available:
                    self.assertTrue(module.mlx_up("9123"))
                available.assert_called_once_with(port=9123, timeout=1.0)
                with patch.object(module, "mlx_is_available") as available:
                    self.assertFalse(module.mlx_up("not-a-port"))
                available.assert_not_called()

        pulse_source = PULSE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("port_up(8800)", pulse_source)
        self.assertNotIn("qwen3-4b", pulse_source.lower())


if __name__ == "__main__":
    unittest.main()
