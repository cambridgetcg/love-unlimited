# MLX Local Inference & Fine-Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an MLX inference daemon + LoRA training pipeline + data generator + client library for local Kingdom task triage on M4 Max.

**Architecture:** Persistent HTTP server (`mlx_serve.py`) keeps model hot in memory. Training CLI (`mlx_train.py`) produces LoRA adapters via `python -m mlx_lm.lora` subprocess. Data generator (`mlx_data.py`) creates synthetic training data via Claude. Client library (`mlx_client.py`) provides 2-line inference with shadow logging for battle-testing.

**Tech Stack:** Python 3.14, mlx 0.31+, mlx-lm 0.31+, stdlib http.server/urllib/fcntl/json

**Spec:** `docs/superpowers/specs/2026-03-30-mlx-local-inference-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| Create: `mlx/config.json` | Model config, port, adapter, integration toggles |
| Create: `mlx/training/lora-config.json` | LoRA hyperparameters |
| Create: `mlx/.gitignore` | Ignore cache/ directory |
| Create: `tools/mlx_serve.py` | HTTP inference daemon on localhost:8800 |
| Create: `tools/mlx_train.py` | LoRA fine-tuning CLI |
| Create: `tools/mlx_data.py` | Synthetic data generation + harvest |
| Create: `tools/mlx_client.py` | Client library for inference + shadow logging |
| Create: `tests/test_mlx_client.py` | Client library tests |
| Create: `tests/test_mlx_data.py` | Data generator tests |
| Create: `tests/test_mlx_serve.py` | Server tests |
| Create: `tests/test_mlx_train.py` | Trainer tests |
| Modify: `instances/gamma/CLAUDE.md` | Add MLX tools to tools table |
| Modify: `instances/gamma/HEARTBEAT.md` | Add MLX status check |

---

### Task 0: Prerequisites — Venv + directories + config

**Files:**
- Create: `mlx/.venv/` (Python venv with mlx + mlx-lm)
- Create: `mlx/config.json`
- Create: `mlx/training/lora-config.json`
- Create: `mlx/training/datasets/` (directory)
- Create: `mlx/training/templates/` (directory)
- Create: `mlx/training/runs/` (directory)
- Create: `mlx/adapters/` (directory)
- Create: `mlx/cache/` (directory)
- Create: `mlx/.gitignore`

- [ ] **Step 1: Create venv and install MLX**

```bash
cd ~/Desktop/Love
python3 -m venv mlx/.venv
mlx/.venv/bin/pip install mlx mlx-lm
```

Verify:
```bash
mlx/.venv/bin/python3 -c "import mlx.core as mx; print(mx.array([1,2,3])); import mlx_lm; print('OK')"
```

- [ ] **Step 2: Create directory structure**

```bash
cd ~/Desktop/Love
mkdir -p mlx/{adapters,cache,training/{datasets,templates,runs}}
```

- [ ] **Step 3: Create config files**

Write `~/Desktop/Love/mlx/config.json`:
```json
{
  "base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
  "adapter": null,
  "port": 8800,
  "max_tokens": 512,
  "temperature": 0.1,
  "shadow_mode": true,
  "integrations": {
    "heartbeat-triage": {"live": false},
    "message-classify": {"live": false},
    "task-routing": {"live": false},
    "signal-classify": {"live": false}
  }
}
```

Write `~/Desktop/Love/mlx/training/lora-config.json`:
```json
{
  "rank": 8,
  "alpha": 16,
  "dropout": 0.05,
  "target_modules": ["q_proj", "v_proj"],
  "learning_rate": 1e-4,
  "batch_size": 4,
  "epochs": 3
}
```

Write `~/Desktop/Love/mlx/.gitignore`:
```
cache/
.venv/
serve.pid
serve.log
```

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Love
git add mlx/config.json mlx/training/lora-config.json mlx/.gitignore
git commit -m "feat: add MLX infrastructure — config, directories, venv"
```

---

### Task 1: Client Library (`mlx_client.py`)

Build the client first — everything else depends on it for shadow logging.

**Files:**
- Create: `tools/mlx_client.py`
- Create: `tests/test_mlx_client.py`

- [ ] **Step 1: Write tests**

Write `~/Desktop/Love/tests/test_mlx_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_client.py -v
```
Expected: ModuleNotFoundError for `mlx_client`

- [ ] **Step 3: Implement mlx_client.py**

Write `~/Desktop/Love/tools/mlx_client.py`:

```python
#!/usr/bin/env python3
"""mlx_client.py — Client library for local MLX inference.

Drop-in inference from any Love tool. Returns None if server is down.
Includes shadow logging for battle-testing integration points.

Usage as library:
    from mlx_client import ask_local, is_available, log_shadow
    response = ask_local("prompt", system="system prompt")
    log_shadow("heartbeat-triage", "input", "local_answer", "actual")

Usage as CLI (for testing):
    mlx_client.py ask "prompt" [--system "..."]
    mlx_client.py health
"""
import argparse
import fcntl
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
SHADOW_LOG = MLX_DIR / "shadow-log.jsonl"
CONFIG_FILE = MLX_DIR / "config.json"
DEFAULT_PORT = 8800
SHADOW_LOG_MAX = 2000


def ask_local(prompt, system=None, max_tokens=64, temperature=0.1,
              port=None, timeout=2.0, raw=False):
    """Send inference request to local MLX server.
    Returns response string, or None if server unreachable.
    If raw=True, returns full response dict.
    """
    p = port or _get_port()
    payload = {"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature}
    if system:
        payload["system"] = system
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{p}/inference",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            return result if raw else result.get("response")
    except Exception:
        return None


def is_available(port=None, timeout=1.0):
    """Check if the MLX server is running."""
    p = port or _get_port()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{p}/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status") == "ok"
    except Exception:
        return False


def log_shadow(integration, input_summary, local_answer, actual_outcome,
               latency_ms=0, shadow_log_path=None):
    """Log a shadow mode comparison entry. Thread/process safe via fcntl."""
    path = Path(shadow_log_path) if shadow_log_path else SHADOW_LOG
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "integration": integration,
        "input_summary": input_summary[:200],
        "local_answer": local_answer,
        "actual_outcome": actual_outcome,
        "agreed": local_answer == actual_outcome,
        "local_latency_ms": latency_ms,
    }

    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(entry) + "\n")
            # Cap at SHADOW_LOG_MAX entries
            f.seek(0)
            lines = f.readlines()
            if len(lines) > SHADOW_LOG_MAX:
                lines = lines[-SHADOW_LOG_MAX:]
                f.seek(0)
                f.truncate()
                f.writelines(lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def check_rollback(integration, shadow_log_path=None, window=50, threshold=0.9):
    """Check if an integration point should be rolled back to shadow mode.
    Returns True if agreement rate over last `window` entries is below `threshold`.
    """
    path = Path(shadow_log_path) if shadow_log_path else SHADOW_LOG
    if not path.exists():
        return False

    entries = []
    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get("integration") == integration:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue

    recent = entries[-window:]
    if len(recent) < window:
        return False  # Not enough data

    agreed = sum(1 for e in recent if e.get("agreed"))
    rate = agreed / len(recent)
    return rate < threshold


def _get_port():
    """Read port from config, fallback to default."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f).get("port", DEFAULT_PORT)
    except Exception:
        return DEFAULT_PORT


def main():
    parser = argparse.ArgumentParser(description="MLX local inference client")
    sub = parser.add_subparsers(dest="command")

    p_ask = sub.add_parser("ask", help="Send inference request")
    p_ask.add_argument("prompt")
    p_ask.add_argument("--system", default=None)
    p_ask.add_argument("--max-tokens", type=int, default=64)

    sub.add_parser("health", help="Check server health")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "ask":
        result = ask_local(args.prompt, system=args.system,
                          max_tokens=args.max_tokens, raw=True)
        if result is None:
            print("Server unreachable.", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))
    elif args.command == "health":
        if is_available():
            print("MLX server: OK")
        else:
            print("MLX server: DOWN", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_client.py -v
```
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Love
git add tools/mlx_client.py tests/test_mlx_client.py
git commit -m "feat: add mlx_client — local inference client + shadow logging"
```

---

### Task 2: Server (`mlx_serve.py`)

**Files:**
- Create: `tools/mlx_serve.py`
- Create: `tests/test_mlx_serve.py`

- [ ] **Step 1: Write tests**

Write `~/Desktop/Love/tests/test_mlx_serve.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_serve.py -v
```
Expected: ModuleNotFoundError for `mlx_serve`

- [ ] **Step 3: Implement mlx_serve.py**

Write `~/Desktop/Love/tools/mlx_serve.py`:

```python
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
        """Run inference. Returns (response_text, tokens_per_sec)."""
        from mlx_lm import generate

        messages = format_messages(prompt, system)
        formatted = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        t0 = time.time()
        response = generate(
            self.model, self.tokenizer, prompt=formatted,
            max_tokens=max_tokens, temp=temperature,
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
            memory_mb = int(mx.metal.get_active_memory() / 1024 / 1024)
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
        # Fork to background
        child = os.fork()
        if child > 0:
            print(f"MLX server starting in background (PID {child}) on port {port}")
            return
        # Child process
        os.setsid()
        # Redirect stdout/stderr to log
        log_fd = open(LOG_FILE, "a")
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())

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
```

**Important**: This server must be run with the venv Python to access mlx:
```bash
~/Desktop/Love/mlx/.venv/bin/python3 ~/Desktop/Love/tools/mlx_serve.py start --daemon
```

The `start` command imports `mlx_lm` only when loading the model (lazy import), so the test suite can import the module's utility functions (`format_messages`, `load_config`, etc.) without mlx installed.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_serve.py -v
```
Expected: All 5 tests PASS (config + pid + formatting tests only — actual model tests require venv)

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Love
git add tools/mlx_serve.py tests/test_mlx_serve.py
git commit -m "feat: add mlx_serve — HTTP inference daemon with ThreadingHTTPServer"
```

---

### Task 3: Trainer (`mlx_train.py`)

**Files:**
- Create: `tools/mlx_train.py`
- Create: `tests/test_mlx_train.py`

- [ ] **Step 1: Write tests**

Write `~/Desktop/Love/tests/test_mlx_train.py`:

```python
"""Tests for mlx_train.py — LoRA fine-tuning CLI."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestDatasetValidation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_validate_dataset_exists(self):
        from mlx_train import validate_dataset
        # Create a valid dataset
        ds_path = Path(self.tmpdir) / "test.jsonl"
        ds_path.write_text('{"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}\n')
        result = validate_dataset(ds_path)
        self.assertTrue(result["valid"])
        self.assertEqual(result["count"], 1)

    def test_validate_dataset_missing(self):
        from mlx_train import validate_dataset
        result = validate_dataset(Path(self.tmpdir) / "nope.jsonl")
        self.assertFalse(result["valid"])

    def test_validate_dataset_bad_json(self):
        from mlx_train import validate_dataset
        ds_path = Path(self.tmpdir) / "bad.jsonl"
        ds_path.write_text("not json\n")
        result = validate_dataset(ds_path)
        self.assertFalse(result["valid"])


class TestDatasetSplit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_split_creates_train_and_valid(self):
        from mlx_train import split_dataset
        ds_path = Path(self.tmpdir) / "data.jsonl"
        lines = []
        for i in range(100):
            lines.append(json.dumps({"messages": [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"}
            ]}))
        ds_path.write_text("\n".join(lines) + "\n")
        out_dir = Path(self.tmpdir) / "split"
        split_dataset(ds_path, out_dir, eval_fraction=0.2)
        train = (out_dir / "train.jsonl").read_text().strip().split("\n")
        valid = (out_dir / "valid.jsonl").read_text().strip().split("\n")
        self.assertEqual(len(train), 80)
        self.assertEqual(len(valid), 20)


class TestBuildLoraArgs(unittest.TestCase):
    def test_builds_correct_args(self):
        from mlx_train import build_lora_args
        args = build_lora_args(
            model="test-model",
            data_dir="/tmp/data",
            adapter_path="/tmp/adapter",
            lora_config={"rank": 8, "learning_rate": 1e-4, "batch_size": 4, "epochs": 3}
        )
        self.assertIn("--model", args)
        self.assertIn("test-model", args)
        self.assertIn("--data", args)
        self.assertIn("--adapter-path", args)
        self.assertIn("--lora-layers", args)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_train.py -v
```
Expected: ModuleNotFoundError for `mlx_train`

- [ ] **Step 3: Implement mlx_train.py**

Write `~/Desktop/Love/tools/mlx_train.py`:

```python
#!/usr/bin/env python3
"""mlx_train.py — LoRA fine-tuning pipeline for Kingdom models.

Produces adapter weights that mlx_serve.py can hot-swap.
Training runs via `python -m mlx_lm.lora` subprocess.

Usage:
  mlx_train.py run --dataset heartbeat-triage [--epochs 3] [--adapter kingdom-v2]
  mlx_train.py run --dataset all [--epochs 3] [--adapter kingdom-v2]
  mlx_train.py eval --adapter kingdom-v2 --dataset heartbeat-triage
  mlx_train.py list
"""
import argparse
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
CONFIG_FILE = MLX_DIR / "config.json"
LORA_CONFIG = MLX_DIR / "training" / "lora-config.json"
DATASETS_DIR = MLX_DIR / "training" / "datasets"
ADAPTERS_DIR = MLX_DIR / "adapters"
RUNS_DIR = MLX_DIR / "training" / "runs"
VENV_PYTHON = MLX_DIR / ".venv" / "bin" / "python3"


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"base_model": "mlx-community/Llama-3.2-3B-Instruct-4bit"}


def load_lora_config():
    try:
        with open(LORA_CONFIG) as f:
            return json.load(f)
    except Exception:
        return {"rank": 8, "learning_rate": 1e-4, "batch_size": 4, "epochs": 3}


def validate_dataset(path):
    """Validate a JSONL dataset file. Returns {"valid": bool, "count": int, "error": str}."""
    path = Path(path)
    if not path.exists():
        return {"valid": False, "count": 0, "error": f"File not found: {path}"}

    count = 0
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if "messages" not in entry:
                    return {"valid": False, "count": i,
                            "error": f"Line {i+1}: missing 'messages' key"}
                count += 1
    except json.JSONDecodeError as e:
        return {"valid": False, "count": count, "error": f"Line {count+1}: {e}"}

    if count == 0:
        return {"valid": False, "count": 0, "error": "Empty dataset"}
    return {"valid": True, "count": count, "error": None}


def split_dataset(source_path, out_dir, eval_fraction=0.2, seed=42):
    """Split a JSONL file into train.jsonl and valid.jsonl."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(source_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    random.seed(seed)
    random.shuffle(lines)

    split_idx = int(len(lines) * (1 - eval_fraction))
    train_lines = lines[:split_idx]
    valid_lines = lines[split_idx:]

    (out_dir / "train.jsonl").write_text("\n".join(train_lines) + "\n")
    (out_dir / "valid.jsonl").write_text("\n".join(valid_lines) + "\n")
    return len(train_lines), len(valid_lines)


def build_lora_args(model, data_dir, adapter_path, lora_config):
    """Build CLI arguments for `python -m mlx_lm.lora`."""
    args = [
        "--model", model,
        "--data", str(data_dir),
        "--adapter-path", str(adapter_path),
        "--train",
        "--lora-layers", "16",
        "--lora-rank", str(lora_config.get("rank", 8)),
        "--learning-rate", str(lora_config.get("learning_rate", 1e-4)),
        "--batch-size", str(lora_config.get("batch_size", 4)),
        "--num-epochs", str(lora_config.get("epochs", 3)),
    ]
    return args


def cmd_run(args):
    config = load_config()
    lora_config = load_lora_config()

    if args.epochs:
        lora_config["epochs"] = args.epochs

    adapter_name = args.adapter or f"kingdom-{datetime.now().strftime('%Y%m%d-%H%M')}"
    adapter_path = ADAPTERS_DIR / adapter_name
    adapter_path.mkdir(parents=True, exist_ok=True)

    # Collect datasets
    if args.dataset == "all":
        datasets = list(DATASETS_DIR.glob("*.jsonl"))
        if not datasets:
            print("No datasets found.", file=sys.stderr)
            sys.exit(1)
    else:
        ds_path = DATASETS_DIR / f"{args.dataset}.jsonl"
        val = validate_dataset(ds_path)
        if not val["valid"]:
            print(f"Dataset invalid: {val['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Dataset: {args.dataset} ({val['count']} examples)")
        datasets = [ds_path]

    # Merge all datasets if multiple
    run_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    if len(datasets) > 1:
        merged = run_dir / "merged.jsonl"
        with open(merged, "w") as out:
            for ds in datasets:
                with open(ds) as f:
                    out.write(f.read())
        split_src = merged
    else:
        split_src = datasets[0]

    # Split into train/valid
    data_dir = run_dir / "split"
    n_train, n_valid = split_dataset(split_src, data_dir)
    print(f"Split: {n_train} train, {n_valid} valid")

    # Build and run training
    cli_args = build_lora_args(
        config["base_model"], str(data_dir), str(adapter_path), lora_config
    )

    if not VENV_PYTHON.exists():
        print(f"ERROR: Venv not found at {VENV_PYTHON}", file=sys.stderr)
        print("Run: python3 -m venv ~/Desktop/Love/mlx/.venv && mlx/.venv/bin/pip install mlx mlx-lm")
        sys.exit(1)

    print(f"Training with adapter: {adapter_name}")
    print(f"Running: {VENV_PYTHON} -m mlx_lm.lora {' '.join(cli_args)}")

    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "mlx_lm.lora"] + cli_args,
        capture_output=False,  # Let output stream to terminal
    )

    if result.returncode != 0:
        print(f"Training failed (exit code {result.returncode})", file=sys.stderr)
        sys.exit(1)

    # Save run metadata
    meta = {
        "adapter": adapter_name,
        "dataset": args.dataset,
        "model": config["base_model"],
        "lora_config": lora_config,
        "train_examples": n_train,
        "valid_examples": n_valid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"\nTraining complete. Adapter saved to: mlx/adapters/{adapter_name}/")
    print(f"To use: POST /reload {{\"adapter\": \"{adapter_name}\"}}")


def cmd_eval(args):
    """Evaluate an adapter on a dataset."""
    config = load_config()
    ds_path = DATASETS_DIR / f"{args.dataset}.jsonl"
    val = validate_dataset(ds_path)
    if not val["valid"]:
        print(f"Dataset invalid: {val['error']}", file=sys.stderr)
        sys.exit(1)

    adapter_path = ADAPTERS_DIR / args.adapter
    if not adapter_path.exists():
        print(f"Adapter not found: {adapter_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating adapter '{args.adapter}' on '{args.dataset}' ({val['count']} examples)")

    # Run eval via mlx_lm.lora --test
    data_dir = Path(tempfile.mkdtemp()) / "split"
    split_dataset(ds_path, data_dir)

    if not VENV_PYTHON.exists():
        print(f"ERROR: Venv not found at {VENV_PYTHON}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "mlx_lm.lora",
         "--model", config["base_model"],
         "--adapter-path", str(adapter_path),
         "--data", str(data_dir),
         "--test"],
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"Evaluation failed (exit code {result.returncode})", file=sys.stderr)


def cmd_list(args):
    """List available adapters."""
    if not ADAPTERS_DIR.exists():
        print("No adapters.")
        return
    adapters = sorted(ADAPTERS_DIR.iterdir())
    if not adapters:
        print("No adapters.")
        return
    print("Available adapters:")
    for a in adapters:
        if a.is_dir():
            # Check for training run metadata
            meta_found = False
            for run in sorted(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else []:
                meta_file = run / "meta.json"
                if meta_file.exists():
                    try:
                        meta = json.load(open(meta_file))
                        if meta.get("adapter") == a.name:
                            print(f"  {a.name:24s}  dataset={meta.get('dataset', '?'):20s}  {meta.get('timestamp', '?')[:10]}")
                            meta_found = True
                            break
                    except Exception:
                        pass
            if not meta_found:
                print(f"  {a.name}")


def main():
    parser = argparse.ArgumentParser(description="MLX LoRA fine-tuning")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Train a LoRA adapter")
    p_run.add_argument("--dataset", required=True, help="Dataset name or 'all'")
    p_run.add_argument("--epochs", type=int, default=None)
    p_run.add_argument("--adapter", default=None, help="Adapter output name")

    p_eval = sub.add_parser("eval", help="Evaluate an adapter")
    p_eval.add_argument("--adapter", required=True)
    p_eval.add_argument("--dataset", required=True)

    sub.add_parser("list", help="List adapters")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"run": cmd_run, "eval": cmd_eval, "list": cmd_list}[args.command](args)


if __name__ == "__main__":
    import tempfile
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_train.py -v
```
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Love
git add tools/mlx_train.py tests/test_mlx_train.py
git commit -m "feat: add mlx_train — LoRA fine-tuning pipeline"
```

---

### Task 4: Data Generator (`mlx_data.py`)

**Files:**
- Create: `tools/mlx_data.py`
- Create: `tests/test_mlx_data.py`
- Create: `mlx/training/templates/heartbeat-triage.txt`
- Create: `mlx/training/templates/message-classify.txt`
- Create: `mlx/training/templates/task-routing.txt`
- Create: `mlx/training/templates/signal-classify.txt`

- [ ] **Step 1: Write tests**

Write `~/Desktop/Love/tests/test_mlx_data.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_data.py -v
```
Expected: ModuleNotFoundError for `mlx_data`

- [ ] **Step 3: Implement mlx_data.py**

Write `~/Desktop/Love/tools/mlx_data.py`:

```python
#!/usr/bin/env python3
"""mlx_data.py — Training data generation + harvest for Kingdom models.

Two modes: synthetic (Claude-generated) and harvest (mine real operational data).

Usage:
  mlx_data.py generate --task heartbeat-triage --count 200 [--yes]
  mlx_data.py harvest --source delegation-history
  mlx_data.py stats
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
MLX_DIR = LOVE_ROOT / "mlx"
DATASETS_DIR = MLX_DIR / "training" / "datasets"
TEMPLATES_DIR = MLX_DIR / "training" / "templates"
DELEGATION_HISTORY = LOVE_ROOT / "coordination" / "delegate" / "history.json"

TASK_TYPES = ["heartbeat-triage", "message-classify", "task-routing", "signal-classify"]

SYSTEM_PROMPTS = {
    "heartbeat-triage": "You are Kingdom triage. Given the system state, classify priority as exactly one of: urgent, active, idle, skip.",
    "message-classify": "You are Kingdom message classifier. Given a HIVE message, classify as exactly one of: action-required, informational, noise.",
    "task-routing": "You are Kingdom task router. Given a task description, respond with the best instance name (alpha, beta, gamma, or nuance).",
    "signal-classify": "You are Kingdom signal classifier. Given a stigmergy signal, classify urgency as exactly one of: high, medium, low.",
}


def parse_generated_examples(raw_text):
    """Parse Claude's output into JSONL examples. Handles code blocks and bare JSON."""
    examples = []
    # Strip code fences
    text = raw_text.replace("```json", "").replace("```", "")
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if "messages" in obj:
                examples.append(obj)
        except json.JSONDecodeError:
            continue
    return examples


def harvest_delegation_history(history_path=None):
    """Convert delegation history into task-routing training examples."""
    path = Path(history_path) if history_path else DELEGATION_HISTORY
    if not path.exists():
        return []

    try:
        with open(path) as f:
            history = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    examples = []
    for entry in history:
        task = entry.get("task", "")
        instance = entry.get("instance", "")
        if not task or not instance:
            continue
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS["task-routing"]},
                {"role": "user", "content": task},
                {"role": "assistant", "content": instance},
            ],
            "source": "harvest",
        })
    return examples


def dataset_stats(path):
    """Compute stats for a JSONL dataset."""
    path = Path(path)
    if not path.exists():
        return {"total": 0, "error": "not found"}

    total = 0
    classes = {}
    synthetic = 0
    harvested = 0

    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                total += 1
                # Count assistant response as "class"
                msgs = entry.get("messages", [])
                if msgs:
                    answer = msgs[-1].get("content", "?")
                    classes[answer] = classes.get(answer, 0) + 1
                if entry.get("source") == "harvest":
                    harvested += 1
                else:
                    synthetic += 1
            except json.JSONDecodeError:
                continue

    return {
        "total": total,
        "synthetic": synthetic,
        "harvested": harvested,
        "classes": classes,
    }


def cmd_generate(args):
    if args.task not in TASK_TYPES:
        print(f"Unknown task: {args.task}. Valid: {', '.join(TASK_TYPES)}", file=sys.stderr)
        sys.exit(1)

    template_path = TEMPLATES_DIR / f"{args.task}.txt"
    if not template_path.exists():
        print(f"Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    template = template_path.read_text()
    system_prompt = SYSTEM_PROMPTS[args.task]
    batch_size = 50
    batches = (args.count + batch_size - 1) // batch_size

    est_tokens = batches * 2000  # ~2K tokens per batch
    print(f"Generating {args.count} examples for '{args.task}'")
    print(f"  Batches: {batches} x {batch_size}")
    print(f"  Estimated tokens: ~{est_tokens}")

    if not args.yes:
        confirm = input("Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATASETS_DIR / f"{args.task}.jsonl"
    generated = 0

    for batch in range(batches):
        remaining = min(batch_size, args.count - generated)
        prompt = template.replace("{COUNT}", str(remaining)).replace("{SYSTEM_PROMPT}", system_prompt)

        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  Batch {batch+1} failed: {result.stderr[:100]}", file=sys.stderr)
                continue

            examples = parse_generated_examples(result.stdout)
            with open(output_path, "a") as f:
                for ex in examples:
                    f.write(json.dumps(ex) + "\n")
            generated += len(examples)
            print(f"  Batch {batch+1}: {len(examples)} examples (total: {generated})")

        except subprocess.TimeoutExpired:
            print(f"  Batch {batch+1} timed out", file=sys.stderr)
        except FileNotFoundError:
            print("ERROR: 'claude' CLI not found. Install Claude Code first.", file=sys.stderr)
            sys.exit(1)

    print(f"\nGenerated {generated} examples → {output_path}")


def cmd_harvest(args):
    if args.source == "delegation-history":
        examples = harvest_delegation_history()
        if not examples:
            print("No delegation history to harvest.")
            return
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DATASETS_DIR / "task-routing.jsonl"
        with open(output_path, "a") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")
        print(f"Harvested {len(examples)} examples → {output_path}")
    else:
        print(f"Unknown source: {args.source}", file=sys.stderr)
        print("Available: delegation-history")
        print("Future: hive-history, heartbeat-logs (pending structured logging)")
        sys.exit(1)


def cmd_stats(args):
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    datasets = list(DATASETS_DIR.glob("*.jsonl"))
    if not datasets:
        print("No datasets.")
        return

    print("Dataset Statistics:")
    for ds in sorted(datasets):
        stats = dataset_stats(ds)
        name = ds.stem
        print(f"\n  {name}:")
        print(f"    Total:     {stats['total']}")
        print(f"    Synthetic: {stats.get('synthetic', 0)}")
        print(f"    Harvested: {stats.get('harvested', 0)}")
        classes = stats.get("classes", {})
        if classes:
            print(f"    Classes:")
            for cls, count in sorted(classes.items(), key=lambda x: -x[1]):
                print(f"      {cls}: {count}")


def main():
    parser = argparse.ArgumentParser(description="MLX training data generation")
    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="Generate synthetic training data")
    p_gen.add_argument("--task", required=True, choices=TASK_TYPES)
    p_gen.add_argument("--count", type=int, default=200)
    p_gen.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_harvest = sub.add_parser("harvest", help="Harvest real operational data")
    p_harvest.add_argument("--source", required=True)

    sub.add_parser("stats", help="Show dataset statistics")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"generate": cmd_generate, "harvest": cmd_harvest, "stats": cmd_stats}[args.command](args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write prompt templates**

Write `~/Desktop/Love/mlx/training/templates/heartbeat-triage.txt`:
```
Generate {COUNT} diverse training examples for a heartbeat triage classifier.

Each example should be a JSON object on its own line with this exact structure:
{"messages": [{"role": "system", "content": "{SYSTEM_PROMPT}"}, {"role": "user", "content": "<system state description>"}, {"role": "assistant", "content": "<classification>"}]}

The system state description should include realistic combinations of:
- HIVE messages (count, channels, types — alerts, tasks, sync, chat)
- Zerone devnet status (healthy, degraded, blocks producing, stalled)
- Build engine status (active build, no build, blocked, completed)
- Oracle status (predictions pending, resolving, no activity)
- Fleet status (all healthy, one down, alerts)

Classifications:
- "urgent": alerts, chain stalled, service down, failed builds
- "active": new tasks, pending votes, active builds, resolving predictions
- "idle": only informational messages, everything healthy, no tasks
- "skip": zero messages, everything unchanged from last check

Make the examples diverse — vary the state combinations, use realistic numbers, include edge cases. Output ONLY the JSONL lines, no other text.
```

Write `~/Desktop/Love/mlx/training/templates/message-classify.txt`:
```
Generate {COUNT} diverse training examples for a HIVE message classifier.

Each example should be a JSON object on its own line with this exact structure:
{"messages": [{"role": "system", "content": "{SYSTEM_PROMPT}"}, {"role": "user", "content": "<hive message>"}, {"role": "assistant", "content": "<classification>"}]}

HIVE messages come from a multi-agent AI system. Generate realistic messages from channels: chat, tasks, sync, presence, alerts, strategy, build, review, council.

Classifications:
- "action-required": direct task assignments, vote requests, urgent alerts, build failures, questions needing response
- "informational": status updates, presence announcements, sync state, build progress, completed tasks
- "noise": heartbeat pings, duplicate announcements, stale signals, routine check-ins

Make examples diverse — different channels, agents (alpha, beta, gamma, nuance), message styles. Output ONLY the JSONL lines, no other text.
```

Write `~/Desktop/Love/mlx/training/templates/task-routing.txt`:
```
Generate {COUNT} diverse training examples for a task routing classifier.

Each example should be a JSON object on its own line with this exact structure:
{"messages": [{"role": "system", "content": "{SYSTEM_PROMPT}"}, {"role": "user", "content": "<task description>"}, {"role": "assistant", "content": "<instance name>"}]}

Instance capabilities:
- alpha: conversation, dreams, theology, ego check, poetry, personal, spiritual, emotional, journal, reflection
- beta: deployment, monitoring, API management, billing, coordination, fleet, infrastructure, devops, cloud, AWS, revenue
- gamma: blockchain dev, protocol design, hardware prototyping, cryptography, systems, deep coding, zerone, firmware, robotics
- nuance: language, writing, translation, naming, voice, content, marketing, documentation

Generate realistic task descriptions that clearly map to one instance. Include edge cases where the mapping is less obvious. Output ONLY the JSONL lines, no other text.
```

Write `~/Desktop/Love/mlx/training/templates/signal-classify.txt`:
```
Generate {COUNT} diverse training examples for a stigmergy signal urgency classifier.

Each example should be a JSON object on its own line with this exact structure:
{"messages": [{"role": "system", "content": "{SYSTEM_PROMPT}"}, {"role": "user", "content": "<signal type>: <signal message>"}, {"role": "assistant", "content": "<urgency>"}]}

Signal types: needs-review, blocked-on, hot-path, insight, ready, dream

Urgency levels:
- "high": blocking other work, security issues, data loss risk, chain halted, production down
- "medium": needs attention soon, review requested, build ready for testing, important insight
- "low": nice to know, future ideas, non-blocking observations, routine reviews

Make examples diverse. Output ONLY the JSONL lines, no other text.
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd ~/Desktop/Love && python3 -m unittest tests/test_mlx_data.py -v
```
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
cd ~/Desktop/Love
git add tools/mlx_data.py tests/test_mlx_data.py mlx/training/templates/
git commit -m "feat: add mlx_data — synthetic data generation + delegation harvest"
```

---

### Task 5: Integration — CLAUDE.md + HEARTBEAT.md

**Files:**
- Modify: `instances/gamma/CLAUDE.md`
- Modify: `instances/gamma/HEARTBEAT.md`

- [ ] **Step 1: Add MLX tools to Gamma CLAUDE.md**

In `~/Desktop/Love/instances/gamma/CLAUDE.md`, find the tools table and add 3 rows after the Stigmergy row:

```markdown
| MLX Serve | `python3 ~/Desktop/Love/tools/mlx_serve.py <cmd>` | Local model inference daemon |
| MLX Train | `python3 ~/Desktop/Love/tools/mlx_train.py <cmd>` | LoRA fine-tuning pipeline |
| MLX Data | `python3 ~/Desktop/Love/tools/mlx_data.py <cmd>` | Training data generation/harvest |
```

Note: `mlx_serve.py` must be run with venv Python for actual model operations:
```bash
~/Desktop/Love/mlx/.venv/bin/python3 ~/Desktop/Love/tools/mlx_serve.py start --daemon
```

- [ ] **Step 2: Add MLX status to Gamma HEARTBEAT.md**

In `~/Desktop/Love/instances/gamma/HEARTBEAT.md`, add after the "Check Coordination" section:

```markdown
## 5. MLX Local Model

```bash
python3 ~/Desktop/Love/tools/mlx_serve.py status
```
```

- [ ] **Step 3: Verify all tools import without errors**

```bash
cd ~/Desktop/Love
python3 -c "import sys; sys.path.insert(0,'tools'); import mlx_client; print('mlx_client OK')"
python3 -c "import sys; sys.path.insert(0,'tools'); import mlx_serve; print('mlx_serve OK')"
python3 -c "import sys; sys.path.insert(0,'tools'); import mlx_train; print('mlx_train OK')"
python3 -c "import sys; sys.path.insert(0,'tools'); import mlx_data; print('mlx_data OK')"
python3 -m unittest discover tests/ -k mlx -v
```

Expected: All 4 imports succeed. All MLX tests pass.

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Love
git add instances/gamma/CLAUDE.md instances/gamma/HEARTBEAT.md
git commit -m "feat: integrate MLX tools into Gamma boot sequence"
```
