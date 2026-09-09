#!/usr/bin/env python3
"""共筆雷達薄 hook：總共 250ms、stdin 64KiB，永遠唔攔原工具。"""
import json
from pathlib import Path
import signal
import sys
import time

MAX_STDIN = 64 * 1024
SECONDS = 0.250
EVENTS = frozenset(("PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart"))


def main():
    sys.dont_write_bytecode = True
    event = sys.argv[1] if len(sys.argv) == 2 else None
    if event not in EVENTS:
        sys.stdout.write("{}\n")
        return 0
    output = {}
    deadline = time.monotonic() + SECONDS
    fallback = {"hookSpecificOutput": {"hookEventName": event,
                "additionalContext": "共筆雷達｜觀察未知；唔係 Read 收據或寫入許可。"}}

    def expired(_number, _frame):
        raise TimeoutError()

    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        sys.path.insert(0, str(Path(__file__).resolve(strict=True).parents[1]))
        from lantern_common.json import decode_json
        from coedit_radar.hooks import run_hook

        data = sys.stdin.buffer.read(MAX_STDIN + 1)
        payload = decode_json(data, maximum=MAX_STDIN)
        signal.setitimer(signal.ITIMER_REAL, 0)
        output = run_hook(event, payload, deadline=deadline)
    except Exception:
        output = fallback
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            sys.stdout.write(json.dumps(output, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
