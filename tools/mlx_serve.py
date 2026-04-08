#!/usr/bin/env python3
"""mlx_serve.py — MLX inference daemon for the Kingdom.

Persistent HTTP server on localhost:8800. Loads model + LoRA adapter
at startup, keeps it hot in memory for fast inference.

Usage:
  mlx_serve.py start [--port 8800] [--daemon]
  mlx_serve.py stop
  mlx_serve.py status
"""
import argparse
import json
import os
import signal
import sys
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
PID_FILE = MLX_DIR / "serve.pid"
LOG_FILE = MLX_DIR / "serve.log"
VENV_PYTHON = MLX_DIR / ".venv" / "bin" / "python3"

DEFAULTS = {
    "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "adapter": None,
    "port": 8800,
    "max_tokens": 512,
    "temperature": 0.1,
}


def load_config(config_path=None):
    """Load config with defaults."""
    path = config_path or CONFIG_FILE
    try:
        with open(path) as f:
            config = json.load(f)
        return {**DEFAULTS, **config}
    except Exception:
        return dict(DEFAULTS)


def format_messages(prompt, system=None):
    """Build chat messages list for tokenizer.apply_chat_template()."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


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
        self.start_time = time.time()
        self.requests_served = 0
        self.total_tps = 0.0

    def load_model(self):
        """Load base model + optional LoRA adapter."""
        from mlx_lm import load

        adapter_path = None
        if self.adapter_name:
            ap = MLX_DIR / "adapters" / self.adapter_name
            if ap.exists():
                adapter_path = str(ap)

        cache_dir = str(MLX_DIR / "cache")
        os.makedirs(cache_dir, exist_ok=True)

        print(f"Loading model: {self.model_name}")
        if adapter_path:
            print(f"  Adapter: {self.adapter_name}")

        self.model, self.tokenizer = load(
            self.model_name,
            adapter_path=adapter_path,
        )
        print("Model loaded.")

    def generate(self, prompt, system=None, max_tokens=64, temperature=0.1):
        """Run inference. Returns (response_text, tokens_per_sec, elapsed)."""
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        messages = format_messages(prompt, system)
        formatted = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        sampler = make_sampler(temp=temperature)
        t0 = time.time()
        response = generate(
            self.model, self.tokenizer, prompt=formatted,
            max_tokens=max_tokens, sampler=sampler,
        )
        elapsed = time.time() - t0

        # Estimate tokens (rough: split by spaces)
        est_tokens = len(response.split())
        tps = est_tokens / elapsed if elapsed > 0 else 0

        self.requests_served += 1
        self.total_tps += tps

        return response.strip(), tps, elapsed

    def reload_adapter(self, adapter_name):
        """Reload model with a different adapter."""
        self.adapter_name = adapter_name
        self.config["adapter"] = adapter_name
        self.load_model()

    def health(self):
        memory_mb = 0
        try:
            import mlx.core as mx
            memory_mb = int(mx.get_active_memory() / 1024 / 1024)
        except Exception:
            pass
        return {
            "status": "ok",
            "model": self.model_name,
            "adapter": self.adapter_name,
            "uptime_seconds": int(time.time() - self.start_time),
            "requests_served": self.requests_served,
            "avg_tokens_per_sec": round(self.total_tps / max(self.requests_served, 1), 1),
            "memory_mb": memory_mb,
        }


# Global server instance (set after model load)
_server = None


class RequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global _server
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/inference":
            try:
                response_text, tps, elapsed = _server.generate(
                    body.get("prompt", ""),
                    system=body.get("system"),
                    max_tokens=body.get("max_tokens", _server.config.get("max_tokens", 64)),
                    temperature=body.get("temperature", _server.config.get("temperature", 0.1)),
                )
                self._json_response(200, {
                    "response": response_text,
                    "tokens_per_sec": round(tps, 1),
                    "model": _server.model_name,
                    "adapter": _server.adapter_name,
                    "latency_ms": int(elapsed * 1000),
                })
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif self.path == "/reload":
            adapter = body.get("adapter")
            if not adapter:
                self._json_response(400, {"error": "missing 'adapter' field"})
                return
            try:
                t0 = time.time()
                _server.reload_adapter(adapter)
                self._json_response(200, {
                    "status": "reloaded",
                    "adapter": adapter,
                    "reload_ms": int((time.time() - t0) * 1000),
                })
            except Exception as e:
                self._json_response(500, {"error": str(e)})
        else:
            self._json_response(404, {"error": "not found"})

    def do_GET(self):
        global _server
        if self.path == "/health":
            self._json_response(200, _server.health())
        else:
            self._json_response(404, {"error": "not found"})

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

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
            [str(VENV_PYTHON), __file__, "start", "--port", str(port)],
            stdout=log_fd, stderr=log_fd,
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
