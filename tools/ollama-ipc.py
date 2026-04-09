#!/usr/bin/env python3
"""
ollama-ipc.py — Ollama client for sandboxed environments.

Instead of making network calls (blocked by sandbox), this writes
a request file to /tmp and waits for the server.mjs process (which
has network access) to process it and write a response file.

Usage:
    python3 ollama-ipc.py test
    python3 ollama-ipc.py chat "your message"
    python3 ollama-ipc.py models
    python3 ollama-ipc.py chat "message" --model glm-5.1 --system "you are helpful"

Architecture:
    sandboxed script → write /tmp/ollama-req-{id}.json
    server.mjs       → reads req, calls Ollama API, writes /tmp/ollama-res-{id}.json
    sandboxed script → reads response
"""

import json
import os
import sys
import time
import uuid

IPC_DIR = "/tmp"
IPC_PREFIX = "ollama-req-"
IPC_RES_PREFIX = "ollama-res-"
DEFAULT_TIMEOUT = 120  # seconds


def ipc_call(request, timeout=DEFAULT_TIMEOUT):
    """Send request via file IPC and wait for response."""
    req_id = uuid.uuid4().hex[:12]
    req_path = os.path.join(IPC_DIR, f"{IPC_PREFIX}{req_id}.json")
    res_path = os.path.join(IPC_DIR, f"{IPC_RES_PREFIX}{req_id}.json")

    # Write request
    with open(req_path, "w") as f:
        json.dump(request, f)

    # Wait for response
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(res_path):
            time.sleep(0.05)  # small grace period for write completion
            try:
                with open(res_path, "r") as f:
                    result = json.load(f)
                os.unlink(res_path)
                return result
            except (json.JSONDecodeError, OSError):
                time.sleep(0.1)
                continue
        time.sleep(0.1)

    # Timeout — clean up
    try:
        os.unlink(req_path)
    except OSError:
        pass

    return {"ok": False, "error": f"Timeout after {timeout}s — is server.mjs running with ollama-bridge?"}


def chat(message, model="glm-5.1", system=None, max_tokens=4096, temperature=0.7):
    """Chat via IPC."""
    return ipc_call({
        "action": "chat",
        "messages": [{"role": "user", "content": message}],
        "model": model,
        "system": system,
        "max_tokens": max_tokens,
        "temperature": temperature,
    })


def list_models():
    """List models via IPC."""
    return ipc_call({"action": "models"}, timeout=15)


def test():
    """Test connectivity via IPC."""
    return ipc_call({"action": "test"}, timeout=60)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "test":
        print("Testing Ollama via file IPC bridge...")
        result = test()
        if result.get("allOk"):
            print("✅ ALL TESTS PASSED")
        elif result.get("error"):
            print(f"❌ {result['error']}")
            sys.exit(1)
        for t in result.get("tests", []):
            status = "✅" if t.get("ok") else "❌"
            print(f"  {status} {t['name']}: {t.get('detail', '?')} ({t.get('latency', '?')}s)")

    elif cmd == "chat":
        if len(sys.argv) < 3:
            print("Usage: ollama-ipc.py chat 'message' [--model M] [--system S]")
            sys.exit(1)
        message = sys.argv[2]
        model = "glm-5.1"
        system = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--model" and i + 1 < len(sys.argv):
                model = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--system" and i + 1 < len(sys.argv):
                system = sys.argv[i + 1]; i += 2
            else:
                i += 1

        result = chat(message, model=model, system=system)
        if result.get("ok"):
            text = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            print(text)
            usage = result.get("usage", {})
            print(f"\n[{result.get('latency', '?')}s | {usage.get('total_tokens', '?')} tokens]")
        else:
            print(f"❌ {result.get('error', 'Unknown error')}")
            sys.exit(1)

    elif cmd == "models":
        result = list_models()
        if result.get("ok"):
            models = result.get("data", result.get("models", []))
            print(f"Available models ({len(models)}):")
            for m in models:
                print(f"  • {m.get('id', m.get('name', '?'))}")
        else:
            print(f"❌ {result.get('error', 'Unknown error')}")
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
