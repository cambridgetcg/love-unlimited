#!/usr/bin/env python3
"""mlx_serve.py — MLX inference daemon for the Kingdom.

Persistent HTTP server on localhost:8800. Loads model + LoRA adapter
at startup, keeps it hot in memory for fast inference.

Usage:
  python -I mlx_serve.py start [--port 8800] [--daemon]
  python -I mlx_serve.py stop
  python -I mlx_serve.py status [--port 8800]
"""

import argparse
import fcntl
import json
import math
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
PID_FILE = MLX_DIR / "serve.pid"
LOG_FILE = MLX_DIR / "serve.log"
START_LOCK_FILE = MLX_DIR / "serve.start.lock"
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
DAEMON_READY_TIMEOUT_SECONDS = 180
DAEMON_READY_POLL_SECONDS = 0.2
DAEMON_LOCK_TIMEOUT_SECONDS = DAEMON_READY_TIMEOUT_SECONDS + 10


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


def validated_port(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError("MLX port must be an integer from 1 to 65535")
    return value


def open_loopback(request, timeout):
    """Open one loopback request without consulting ambient proxy settings."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def port_argument(value):
    try:
        return validated_port(int(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def expected_health_provenance(config, runtime_resolver=None):
    lock_reference = config.get("base_model_lock")
    if not isinstance(lock_reference, str) or not lock_reference:
        raise ValueError("base_model_lock is required for daemon readiness")
    lock_path = (LOVE_ROOT / lock_reference).resolve()
    try:
        lock_path.relative_to(LOVE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("base_model_lock must stay within the project") from exc
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        from hf_model_lock import load_lock, lock_digest
        from mlx_train import resolve_training_environment
    finally:
        sys.path.pop(0)

    lock = load_lock(lock_path)
    if lock["repo_id"] != config.get("base_model"):
        raise ValueError("configured model differs from the model lock")
    runtime_resolver = runtime_resolver or resolve_training_environment
    runtime = runtime_resolver(VENV_PYTHON)
    return {
        "status": "ok",
        "model": lock["repo_id"],
        "model_revision": lock["revision"],
        "model_lock_sha256": lock_digest(lock),
        "runtime": runtime,
    }


def health_provenance_matches(payload, expected):
    """Match the immutable base plus any locally approved active adapter."""
    if not isinstance(payload, dict) or not all(
        payload.get(field) == value for field, value in expected.items()
    ):
        return False
    adapter = payload.get("adapter")
    receipt_sha256 = payload.get("adapter_receipt_sha256")
    if adapter is None:
        return (
            "adapter" in payload
            and "adapter_receipt_sha256" in payload
            and receipt_sha256 is None
        )
    if not isinstance(adapter, str) or not isinstance(receipt_sha256, str):
        return False
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        from mlx_train import validate_adapter_serving_binding

        binding = validate_adapter_serving_binding(
            adapter,
            model_binding={
                "repo_id": expected["model"],
                "revision": expected["model_revision"],
                "lock_sha256": expected["model_lock_sha256"],
            },
        )
    except Exception:
        return False
    finally:
        sys.path.pop(0)
    return binding["receipt_sha256"] == receipt_sha256


def require_loopback_port_available(port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", validated_port(port)))
    except OSError as exc:
        raise RuntimeError(f"loopback port {port} is already in use") from exc
    finally:
        probe.close()


def wait_for_daemon_ready(
    proc,
    port,
    expected,
    *,
    timeout=DAEMON_READY_TIMEOUT_SECONDS,
    poll_interval=DAEMON_READY_POLL_SECONDS,
    urlopen_fn=None,
):
    """Wait until the child serves the exact locked-model health provenance."""
    urlopen_fn = urlopen_fn or open_loopback
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"MLX server exited during startup with code {exit_code}"
            )
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urlopen_fn(request, timeout=min(2, max(poll_interval, 0.1))) as response:
                payload = _strict_json_object(
                    response.read().decode("utf-8"), "MLX health response"
                )
            if health_provenance_matches(payload, expected):
                return payload
            if payload.get("status") == "ok":
                raise RuntimeError(
                    "loopback health provenance differs from the configured model lock"
                )
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(poll_interval)
    raise TimeoutError(
        f"MLX server was not ready on 127.0.0.1:{port} after {timeout:g} seconds"
    )


class ExistingPidProcess:
    """Minimal Popen-like view used to join an in-progress daemon start."""

    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        try:
            os.kill(self.pid, 0)
            return None
        except PermissionError:
            return None
        except ProcessLookupError:
            return 1


def terminate_child_process(proc):
    """Boundedly stop a child created by this process."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def acquire_daemon_start_lock(
    path=None,
    *,
    timeout=DAEMON_LOCK_TIMEOUT_SECONDS,
    poll_interval=0.1,
):
    """Serialize PID inspection, child spawn, and readiness across callers."""
    path = Path(path or START_LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError(
                    f"timed out waiting for MLX daemon startup lock after {timeout:g} seconds"
                )
            time.sleep(poll_interval)


def release_daemon_start_lock(handle):
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


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


def process_matches_server(
    pid, expected_port=None, default_port=None, run_fn=None
):
    """Confirm a PID is this pinned server, optionally on the expected port."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return False
    run_fn = run_fn or subprocess.run
    try:
        result = run_fn(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        argv = shlex.split(result.stdout.strip())
        if len(argv) not in {4, 6}:
            return False
        if (
            not Path(argv[0]).is_absolute()
            or Path(os.path.abspath(argv[0])) != Path(os.path.abspath(VENV_PYTHON))
            or argv[1] != "-I"
            or Path(argv[2]).resolve() != Path(__file__).resolve()
            or argv[3] != "start"
        ):
            return False
        actual_port = validated_port(
            DEFAULTS["port"] if default_port is None else default_port
        )
        if len(argv) == 6:
            if argv[4] != "--port":
                return False
            actual_port = validated_port(int(argv[5]))
        if expected_port is not None and actual_port != validated_port(expected_port):
            return False
        return True
    except (OSError, ValueError, shlex.Error, subprocess.SubprocessError):
        return False


def pid_is_live(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def remove_pid_if_owned(pid_file, pid, lock_file=None):
    """Remove only this process's PID record while holding the startup lock."""
    lock_file = lock_file or Path(pid_file).with_name("serve.start.lock")
    handle = acquire_daemon_start_lock(path=lock_file, timeout=5)
    try:
        if read_pid(pid_file) == pid:
            Path(pid_file).unlink(missing_ok=True)
            return True
        return False
    finally:
        release_daemon_start_lock(handle)


def claim_foreground_start(port, pid_file=None, lock_file=None):
    """Atomically claim the one foreground startup slot for this process."""
    pid_file = Path(pid_file or PID_FILE)
    lock_file = Path(lock_file or START_LOCK_FILE)
    handle = acquire_daemon_start_lock(path=lock_file)
    try:
        current = read_pid(pid_file)
        if current and current != os.getpid():
            if pid_is_live(current):
                return False, current
            Path(pid_file).unlink(missing_ok=True)
        if current != os.getpid():
            require_loopback_port_available(port)
            write_pid(pid_file, os.getpid())
        return True, os.getpid()
    finally:
        release_daemon_start_lock(handle)


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
        self._memory_provider = None
        self._state_lock = threading.RLock()
        self._inference_lock = threading.Lock()
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
        loaded_mx = sys.modules.get("mlx.core")
        memory_provider = getattr(loaded_mx, "get_active_memory", None)
        with self._state_lock:
            self.model = loaded_model
            self.tokenizer = loaded_tokenizer
            self.adapter_name = requested_adapter
            self.config["adapter"] = requested_adapter
            self.model_revision = resolution["revision"]
            self.model_lock_sha256 = resolution["lock_sha256"]
            self.adapter_receipt_sha256 = adapter_receipt_sha256
            self.runtime_environment = runtime_environment
            self._memory_provider = memory_provider
        print("Model loaded.")

    def generate(self, prompt, system=None, max_tokens=64, temperature=0.1):
        """Run inference and return output plus its exact loaded provenance."""
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        # Keep generation serialized for the local MLX runtime, but do not hold
        # the state lock while tokens are produced. Health must stay responsive
        # during a long generation, and a concurrent reload may safely publish a
        # new model while this request retains its local references to the old one.
        with self._inference_lock:
            with self._state_lock:
                model = self.model
                tokenizer = self.tokenizer
                deployment = {
                    "adapter": self.adapter_name,
                    "adapter_receipt_sha256": self.adapter_receipt_sha256,
                    "model": self.model_name,
                    "model_lock_sha256": self.model_lock_sha256,
                    "model_revision": self.model_revision,
                    "runtime": self.runtime_environment,
                }
            messages = format_messages(prompt, system)
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            sampler = make_sampler(temp=temperature)
            t0 = time.time()
            response = generate(
                model,
                tokenizer,
                prompt=formatted,
                max_tokens=max_tokens,
                sampler=sampler,
            )
            elapsed = time.time() - t0
            est_tokens = len(response.split())
            tps = est_tokens / elapsed if elapsed > 0 else 0

        with self._state_lock:
            self.requests_served += 1
            self.total_tps += tps

        return response.strip(), tps, elapsed, deployment

    def reload_adapter(self, adapter_name):
        """Reload model with a different adapter."""
        # Loading a second graph while the old one is generating can exhaust
        # unified memory or race shared Metal state. Keep health independent,
        # while serializing reload with inference under one consistent order.
        with self._reload_lock, self._inference_lock:
            self.load_model(adapter_name=adapter_name)

    def health(self):
        memory_mb = 0
        with self._state_lock:
            memory_provider = self._memory_provider
        try:
            if memory_provider is not None:
                memory_mb = int(memory_provider() / 1024 / 1024)
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


def _cmd_start_daemon(config, port, expected_health):
    """Atomically join one live start or claim a child PID, then await readiness."""
    start_lock = None
    proc = None
    spawned = False
    log_fd = None
    try:
        start_lock = acquire_daemon_start_lock()
        pid = read_pid(PID_FILE)
        if pid and not pid_is_live(pid):
            Path(PID_FILE).unlink(missing_ok=True)
            pid = None
        if pid:
            if not process_matches_server(
                pid,
                expected_port=port,
                default_port=config["port"],
            ):
                print(
                    f"PID file {PID_FILE} does not identify the hardened MLX server; "
                    "refusing to join it",
                    file=sys.stderr,
                )
                sys.exit(1)
            proc = ExistingPidProcess(pid)
        else:
            require_loopback_port_available(port)
            # Spawn instead of fork — os.fork() breaks Metal XPC on macOS.
            log_fd = open(LOG_FILE, "a")
            proc = subprocess.Popen(
                [str(VENV_PYTHON), "-I", __file__, "start", "--port", str(port)],
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
            )
            spawned = True
            write_pid(PID_FILE, proc.pid)
    except BaseException:
        if spawned:
            terminate_child_process(proc)
            Path(PID_FILE).unlink(missing_ok=True)
        raise
    finally:
        if log_fd is not None:
            log_fd.close()
        if start_lock is not None:
            release_daemon_start_lock(start_lock)

    try:
        wait_for_daemon_ready(proc, port, expected_health)
    except (RuntimeError, TimeoutError) as exc:
        if spawned:
            terminate_child_process(proc)
        if spawned:
            remove_pid_if_owned(PID_FILE, proc.pid, START_LOCK_FILE)
        print(
            f"{'MLX server' if spawned else 'Existing MLX server'} failed "
            f"readiness: {exc}; inspect {LOG_FILE}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"MLX server READY in background (PID {proc.pid}) on port {port}")


def cmd_start(args):
    global _server

    config = load_config()
    port = validated_port(args.port if args.port is not None else config["port"])
    if args.daemon:
        expected_health = expected_health_provenance(config)
        try:
            return _cmd_start_daemon(config, port, expected_health)
        except TimeoutError as exc:
            print(f"MLX server failed readiness: {exc}", file=sys.stderr)
            sys.exit(1)

    claimed, pid = claim_foreground_start(port)
    if not claimed:
        print(f"Server already starting or running (PID {pid}).")
        sys.exit(1)

    truncate_log(LOG_FILE)

    # Load model
    _server = InferenceServer(config)
    try:
        _server.load_model()
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        remove_pid_if_owned(PID_FILE, os.getpid(), START_LOCK_FILE)
        sys.exit(1)

    # Start HTTP server
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), RequestHandler)
    except OSError as exc:
        remove_pid_if_owned(PID_FILE, os.getpid(), START_LOCK_FILE)
        print(f"Failed to bind MLX loopback port {port}: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"MLX server listening on 127.0.0.1:{port}")

    serve_until_stopped(httpd, PID_FILE)


def serve_until_stopped(httpd, pid_file=None):
    """Serve until a signal requests shutdown from a non-serving helper thread."""
    pid_file = Path(pid_file or PID_FILE)

    def shutdown_handler(sig, frame):
        print("Shutting down...")
        threading.Thread(
            target=httpd.shutdown,
            name="mlx-http-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        remove_pid_if_owned(pid_file, os.getpid())


def cmd_stop(args):
    pid = read_pid(PID_FILE)
    if not pid:
        print("No server running (no PID file).")
        return
    if not pid_is_live(pid):
        print("Server not running.")
        remove_pid_if_owned(PID_FILE, pid, START_LOCK_FILE)
        return
    if not process_matches_server(pid):
        print(
            f"Refusing to signal PID {pid}: {PID_FILE} does not identify "
            "the hardened MLX server",
            file=sys.stderr,
        )
        sys.exit(2)

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
                remove_pid_if_owned(PID_FILE, pid, START_LOCK_FILE)
                return
        # Force kill
        if not process_matches_server(pid):
            print(
                f"Refusing to force-kill PID {pid}: it no longer identifies "
                "the hardened MLX server",
                file=sys.stderr,
            )
            sys.exit(2)
        os.kill(pid, signal.SIGKILL)
        print("Force killed.")
    except ProcessLookupError:
        print("Server not running.")
    remove_pid_if_owned(PID_FILE, pid, START_LOCK_FILE)


def cmd_status(args):
    pid = read_pid(PID_FILE)
    if not pid:
        print("MLX server: NOT RUNNING")
        sys.exit(1)
    if not pid_is_live(pid):
        print("MLX server: NOT RUNNING (stale PID)")
        remove_pid_if_owned(PID_FILE, pid, START_LOCK_FILE)
        sys.exit(1)
    config = load_config()
    port = validated_port(
        args.port if args.port is not None else config["port"]
    )
    if not process_matches_server(
        pid,
        expected_port=port,
        default_port=config["port"],
    ):
        print("MLX server: PID FILE DOES NOT IDENTIFY THIS SERVER")
        sys.exit(2)
    # Try to get health
    try:
        import urllib.request

        expected = expected_health_provenance(config)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with open_loopback(req, timeout=2) as resp:
            data = _strict_json_object(
                resp.read().decode("utf-8"), "MLX health response"
            )
        if not health_provenance_matches(data, expected):
            raise ValueError(
                "loopback health provenance differs from the configured model lock"
            )
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
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="MLX inference daemon")
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start the server")
    p_start.add_argument("--port", type=port_argument, default=None)
    p_start.add_argument("--daemon", action="store_true", help="Run in background")

    sub.add_parser("stop", help="Stop the server")
    p_status = sub.add_parser("status", help="Show server status")
    p_status.add_argument("--port", type=port_argument, default=None)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
