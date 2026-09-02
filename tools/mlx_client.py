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
import math
import subprocess
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


def _open_loopback(request, timeout):
    """Open one loopback request without consulting ambient proxy settings."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def _validated_port(port):
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer in [1, 65535]")
    return port


def _verified_server_available(port, timeout):
    """Ask the hardened server command to verify PID, port, and provenance."""
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return False
    try:
        gate = subprocess.run(
            [
                "bash",
                str(MLX_DIR / "serve.sh"),
                "status",
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(5.0, timeout + 3.0),
            check=False,
        )
        return gate.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ask_local(prompt, system=None, max_tokens=64, temperature=0.1,
              port=None, timeout=2.0, raw=False):
    """Send inference request to local MLX server.
    Returns response string, or None if server unreachable.
    If raw=True, returns full response dict.
    """
    try:
        p = _validated_port(_get_port() if port is None else port)
    except ValueError:
        return None
    if not _verified_server_available(p, timeout):
        return None
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
        with _open_loopback(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            if not isinstance(result, dict) or not isinstance(
                result.get("response"), str
            ):
                return None
            return result if raw else result["response"]
    except Exception:
        return None


def is_available(port=None, timeout=1.0):
    """Check hardened PID, exact-port, runtime, model, and adapter provenance."""
    try:
        p = _validated_port(_get_port() if port is None else port)
    except ValueError:
        return False
    return _verified_server_available(p, timeout)


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
