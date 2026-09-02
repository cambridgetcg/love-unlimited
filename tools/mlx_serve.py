#!/usr/bin/env python3
"""mlx_serve.py — MLX inference daemon for the Kingdom.

Persistent HTTP server on localhost:8800. Loads model + LoRA adapter
at startup, keeps it hot in memory for fast inference.

Usage:
  python -I mlx_serve.py start [--port 8800] [--daemon]
  python -I mlx_serve.py stop
  python -I mlx_serve.py status
"""

import argparse
import json
import math
import os
import signal
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
PID_FILE = MLX_DIR / "serve.pid"
LOG_FILE = MLX_DIR / "serve.log"
VENV_PYTHON = MLX_DIR / ".venv" / "bin" / "python3"

DEFAULTS = {
    "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "base_model_lock": None,
    "adapter": None,
    "port": 8800,
    "max_tokens": 512,
    "temperature": 0.1,
}
_USE_CONFIG_ADAPTER = object()
MAX_REQUEST_BYTES = 1024 * 1024
INFERENCE_REQUEST_KEYS = {"prompt", "system", "max_tokens", "temperature"}
RELOAD_REQUEST_KEYS = {"adapter"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


class _DuplicateKeyError(ValueError):
    pass


def _strict_json_object(data, label):
    def object_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise _DuplicateKeyError(key)
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"non-finite JSON number {value}")

    try:
        value = json.loads(
            data,
            object_pairs_hook=object_hook,
            parse_constant=reject_constant,
        )
    except (_DuplicateKeyError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label}: root must be an object")
    return value


def load_config(config_path=None):
    """Load config with defaults."""
    path = config_path or CONFIG_FILE
    try:
        config = _strict_json_object(
            Path(path).read_text(encoding="utf-8"), "MLX config"
        )
        return {**DEFAULTS, **config}
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid MLX config {path}: {exc}") from exc


def format_messages(prompt, system=None):
    """Build chat messages list for tokenizer.apply_chat_template()."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _exact_request_keys(value, allowed, label):
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"Invalid {label}: unknown field(s): {', '.join(unknown)}")


def validate_inference_request(body, config):
    _exact_request_keys(body, INFERENCE_REQUEST_KEYS, "inference request")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Invalid inference request: prompt must be non-empty text")
    system = body.get("system")
    if system is not None and not isinstance(system, str):
        raise ValueError("Invalid inference request: system must be text or null")
    max_tokens = body.get("max_tokens", config.get("max_tokens", 64))
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or not 1 <= max_tokens <= 4096
    ):
        raise ValueError(
            "Invalid inference request: max_tokens must be an integer from 1 to 4096"
        )
    temperature = body.get("temperature", config.get("temperature", 0.1))
    if (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise ValueError(
            "Invalid inference request: temperature must be finite and in [0, 2]"
        )
    return {
        "prompt": prompt,
        "system": system,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def validate_reload_request(body):
    _exact_request_keys(body, RELOAD_REQUEST_KEYS, "reload request")
    adapter = body.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        raise ValueError("Invalid reload request: adapter must be non-empty text")
    return adapter


def validate_http_request_headers(headers, *, require_json=True):
    if require_json:
        content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")

    raw_host = headers.get("Host")
    if not isinstance(raw_host, str) or not raw_host.strip():
        raise ValueError("Host header is required")
    try:
        host = urllib.parse.urlsplit("//" + raw_host.strip())
        hostname = host.hostname
        host.port
    except ValueError as exc:
        raise ValueError("Host header is invalid") from exc
    if (
        hostname not in LOOPBACK_HOSTS
        or host.username is not None
        or host.password is not None
        or host.path
        or host.query
        or host.fragment
    ):
        raise ValueError("Host must name the loopback service")

    raw_origin = headers.get("Origin")
    if raw_origin is None:
        return
    try:
        origin = urllib.parse.urlsplit(raw_origin.strip())
        origin.port
    except ValueError as exc:
        raise ValueError("Origin header is invalid") from exc
    if (
        origin.scheme not in {"http", "https"}
        or origin.hostname not in LOOPBACK_HOSTS
        or origin.username is not None
        or origin.password is not None
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        raise ValueError("Origin must be absent or loopback")


def require_isolated_interpreter():
    if sys.flags.isolated != 1:
        raise RuntimeError(
            "MLX serving requires Python isolated mode; start with "
            f"{VENV_PYTHON} -I {Path(__file__).resolve()} start"
        )


def write_pid(path, pid):
    Path(path).write_text(str(pid))


def read_pid(path):
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def truncate_log(path, max_lines=1000):
    """Keep last max_lines of log file."""
    try:
        p = Path(path)
        if p.exists():
            lines = p.read_text().splitlines()
            if len(lines) > max_lines:
                p.write_text("\n".join(lines[-max_lines:]) + "\n")
    except Exception:
        pass


class InferenceServer:
    """Manages model loading and inference."""

    def __init__(self, config):
        self.config = config
        self.model = None
        self.tokenizer = None
        self.adapter_name = config.get("adapter")
        self.model_name = config["base_model"]
        self.model_revision = None
        self.model_lock_sha256 = None
        self.adapter_receipt_sha256 = None
        self.runtime_environment = None
        self._state_lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self.start_time = time.time()
        self.requests_served = 0
        self.total_tps = 0.0

    def load_model(self, adapter_name=_USE_CONFIG_ADAPTER):
        """Validate and load into locals, then atomically publish the new state."""
        require_isolated_interpreter()
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            from hf_model_lock import resolve_locked_snapshot
            from mlx_train import (
                resolve_training_environment,
                validate_adapter_artifact,
                validate_adapter_name,
            )
        finally:
            sys.path.pop(0)

        requested_adapter = (
            self.adapter_name if adapter_name is _USE_CONFIG_ADAPTER else adapter_name
        )
        if requested_adapter:
            requested_adapter = validate_adapter_name(requested_adapter)
        runtime_environment = resolve_training_environment(sys.executable)
        from mlx_lm import load

        resolution = resolve_locked_snapshot(
            self.config,
            project_root=LOVE_ROOT,
            cache_dir=MLX_DIR / "cache",
        )
        adapter_path = None
        adapter_receipt_sha256 = None
        if requested_adapter:
            artifact = validate_adapter_artifact(
                requested_adapter,
                model_resolution=resolution,
                runtime_environment=runtime_environment,
                require_serving=True,
            )
            adapter_path = str(artifact["adapter_path"])
            adapter_receipt_sha256 = artifact["receipt_sha256"]

        print(f"Loading model: {self.model_name}@{resolution['revision']}")
        if adapter_path:
            print(f"  Adapter: {requested_adapter}")

        loaded_model, loaded_tokenizer = load(
            resolution["path"],
            adapter_path=adapter_path,
        )
        with self._state_lock:
            self.model = loaded_model
            self.tokenizer = loaded_tokenizer
            self.adapter_name = requested_adapter
            self.config["adapter"] = requested_adapter
            self.model_revision = resolution["revision"]
            self.model_lock_sha256 = resolution["lock_sha256"]
            self.adapter_receipt_sha256 = adapter_receipt_sha256
            self.runtime_environment = runtime_environment
        print("Model loaded.")

    def generate(self, prompt, system=None, max_tokens=64, temperature=0.1):
        """Run inference and return output plus its exact loaded provenance."""
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        with self._state_lock:
            messages = format_messages(prompt, system)
            formatted = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            sampler = make_sampler(temp=temperature)
            t0 = time.time()
            response = generate(
                self.model,
                self.tokenizer,
                prompt=formatted,
                max_tokens=max_tokens,
                sampler=sampler,
            )
            elapsed = time.time() - t0
            est_tokens = len(response.split())
            tps = est_tokens / elapsed if elapsed > 0 else 0
            self.requests_served += 1
            self.total_tps += tps
            deployment = {
                "adapter": self.adapter_name,
                "adapter_receipt_sha256": self.adapter_receipt_sha256,
                "model": self.model_name,
                "model_lock_sha256": self.model_lock_sha256,
                "model_revision": self.model_revision,
                "runtime": self.runtime_environment,
            }

        return response.strip(), tps, elapsed, deployment

    def reload_adapter(self, adapter_name):
        """Reload model with a different adapter."""
        with self._reload_lock:
            self.load_model(adapter_name=adapter_name)

    def health(self):
        memory_mb = 0
        try:
            import mlx.core as mx

            memory_mb = int(mx.get_active_memory() / 1024 / 1024)
        except Exception:
            pass
        with self._state_lock:
            return {
                "status": "ok",
                "model": self.model_name,
                "model_revision": self.model_revision,
                "model_lock_sha256": self.model_lock_sha256,
                "adapter": self.adapter_name,
                "adapter_receipt_sha256": self.adapter_receipt_sha256,
                "runtime": self.runtime_environment,
                "uptime_seconds": int(time.time() - self.start_time),
                "requests_served": self.requests_served,
                "avg_tokens_per_sec": round(
                    self.total_tps / max(self.requests_served, 1), 1
                ),
                "memory_mb": memory_mb,
            }


# Global server instance (set after model load)
_server = None


class RequestHandler(BaseHTTPRequestHandler):
    def _read_json_body(self):
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if not 0 < length <= MAX_REQUEST_BYTES:
            raise ValueError(
                f"Request body must contain 1 to {MAX_REQUEST_BYTES} bytes"
            )
        try:
            raw_bytes = self.rfile.read(length)
            if len(raw_bytes) != length:
                raise ValueError("Request body ended before Content-Length bytes")
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Request body must be UTF-8 JSON") from exc
        return _strict_json_object(raw, "request JSON")

    def do_POST(self):
        global _server
        try:
            validate_http_request_headers(self.headers)
            body = self._read_json_body()
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
            return

        if self.path == "/inference":
            try:
                request = validate_inference_request(body, _server.config)
                response_text, tps, elapsed, deployment = _server.generate(
                    request["prompt"],
                    system=request["system"],
                    max_tokens=request["max_tokens"],
                    temperature=request["temperature"],
                )
                self._json_response(
                    200,
                    {
                        "response": response_text,
                        "tokens_per_sec": round(tps, 1),
                        **deployment,
                        "latency_ms": int(elapsed * 1000),
                    },
                )
            except ValueError as e:
                self._json_response(400, {"error": str(e)})
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif self.path == "/reload":
            try:
                adapter = validate_reload_request(body)
            except ValueError as exc:
                self._json_response(400, {"error": str(exc)})
                return
            try:
                t0 = time.time()
                _server.reload_adapter(adapter)
                self._json_response(
                    200,
                    {
                        "status": "reloaded",
                        "adapter": adapter,
                        "reload_ms": int((time.time() - t0) * 1000),
                    },
                )
            except Exception as e:
                self._json_response(500, {"error": str(e)})
        else:
            self._json_response(404, {"error": "not found"})

    def do_GET(self):
        global _server
        try:
            validate_http_request_headers(self.headers, require_json=False)
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
            return
        if self.path == "/health":
            self._json_response(200, _server.health())
        else:
            self._json_response(404, {"error": "not found"})

    def _json_response(self, code, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        # Log to file instead of stderr
        try:
            with open(LOG_FILE, "a") as f:
                f.write(f"{self.log_date_time_string()} {format % args}\n")
        except Exception:
            pass


def cmd_start(args):
    global _server

    config = load_config()
    port = args.port or config["port"]

    # Check if already running
    pid = read_pid(PID_FILE)
    if pid:
        try:
            os.kill(pid, 0)
            print(f"Server already running (PID {pid}).")
            sys.exit(1)
        except ProcessLookupError:
            Path(PID_FILE).unlink(missing_ok=True)

    if args.daemon:
        # Spawn as subprocess instead of fork — os.fork() breaks Metal XPC on macOS
        import subprocess as _sp

        log_fd = open(LOG_FILE, "a")
        proc = _sp.Popen(
            [str(VENV_PYTHON), "-I", __file__, "start", "--port", str(port)],
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
        )
        print(f"MLX server starting in background (PID {proc.pid}) on port {port}")
        return

    truncate_log(LOG_FILE)
    write_pid(PID_FILE, os.getpid())

    # Load model
    _server = InferenceServer(config)
    try:
        _server.load_model()
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        Path(PID_FILE).unlink(missing_ok=True)
        sys.exit(1)

    # Start HTTP server
    httpd = ThreadingHTTPServer(("127.0.0.1", port), RequestHandler)
    print(f"MLX server listening on 127.0.0.1:{port}")

    def shutdown_handler(sig, frame):
        print("Shutting down...")
        httpd.shutdown()
        Path(PID_FILE).unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Path(PID_FILE).unlink(missing_ok=True)


def cmd_stop(args):
    pid = read_pid(PID_FILE)
    if not pid:
        print("No server running (no PID file).")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}.")
        # Wait up to 5 seconds
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print("Server stopped.")
                Path(PID_FILE).unlink(missing_ok=True)
                return
        # Force kill
        os.kill(pid, signal.SIGKILL)
        print("Force killed.")
    except ProcessLookupError:
        print("Server not running.")
    Path(PID_FILE).unlink(missing_ok=True)


def cmd_status(args):
    pid = read_pid(PID_FILE)
    if not pid:
        print("MLX server: NOT RUNNING")
        sys.exit(1)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print("MLX server: NOT RUNNING (stale PID)")
        Path(PID_FILE).unlink(missing_ok=True)
        sys.exit(1)

    # Try to get health
    try:
        import urllib.request

        req = urllib.request.Request(f"http://127.0.0.1:{load_config()['port']}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode())
        print(f"MLX server: RUNNING (PID {pid})")
        print(f"  Model:    {data.get('model', '?')}")
        print(f"  Revision: {data.get('model_revision', '?')}")
        print(f"  Adapter:  {data.get('adapter', 'none')}")
        print(f"  Uptime:   {data.get('uptime_seconds', 0)}s")
        print(f"  Requests: {data.get('requests_served', 0)}")
        print(f"  Avg TPS:  {data.get('avg_tokens_per_sec', 0)}")
        print(f"  Memory:   {data.get('memory_mb', '?')} MB")
    except Exception:
        print(f"MLX server: RUNNING (PID {pid}) but health check failed")


def main():
    parser = argparse.ArgumentParser(description="MLX inference daemon")
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start the server")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--daemon", action="store_true", help="Run in background")

    sub.add_parser("stop", help="Stop the server")
    sub.add_parser("status", help="Show server status")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
