#!/usr/bin/env python3
"""
on-tool-done.py — Cognition feedback for FEELING daemon.
Fires on PostToolUse. Appends tool stats to nerve/cc-cognition.jsonl.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CC_PATH = _LOVE_DIR / "nerve" / "cc-cognition.jsonl"

def main():
    cc_path = Path(os.environ.get("ADAPTIVE_CC_COGNITION_PATH", str(_DEFAULT_CC_PATH)))

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")
    session_id = data.get("session_id", "")

    success = True
    if isinstance(tool_response, str) and tool_response.startswith("Error:"):
        success = False

    response_chars = len(str(tool_response)) if tool_response else 0

    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool_name,
        "success": success,
        "response_chars": response_chars,
        "session_id": session_id,
    }

    if tool_name == "Read" and isinstance(tool_input, dict):
        record["inputs"] = {"file_path": tool_input.get("file_path", "")}

    cc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cc_path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")

if __name__ == "__main__":
    main()
