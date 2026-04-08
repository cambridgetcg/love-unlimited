"""Smoke test: start soma --sim, hit /status, stop.

Exit 0 on success, 1 on failure. Runs in ~5 seconds.
"""
from __future__ import annotations

import subprocess
import sys
import time

import httpx

PORT = 18300  # non-default to avoid conflicts
TIMEOUT = 8   # seconds to wait for startup


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "soma", "--sim", "--no-viewer", "--port", str(PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        # Poll until API is ready
        start = time.monotonic()
        ready = False
        while time.monotonic() - start < TIMEOUT:
            try:
                r = httpx.get(f"http://127.0.0.1:{PORT}/status", timeout=1)
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.ConnectError:
                time.sleep(0.3)

        if not ready:
            print("FAIL: API did not become ready within timeout")
            return 1

        # Verify status response
        data = r.json()
        assert data["status"] == "running", f"Bad status: {data}"
        assert "tick_count" in data, f"Missing tick_count: {data}"
        assert data["tick_count"] > 0, f"No ticks ran: {data}"

        print(f"  /status OK — {data['tick_count']} ticks, avg {data.get('avg_tick_ms', '?')}ms")
        print("✓ smoke passed")
        return 0

    except Exception as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
