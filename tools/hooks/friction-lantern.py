#!/usr/bin/env python3
"""阻力燈薄 hook：stdin 64KiB／總共 250ms，固定安全 JSON。"""
import json
from pathlib import Path
import signal
import sys
import time

MAX_STDIN = 64 * 1024
SECONDS = 0.250
EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SubagentStart"})


def main():
    sys.dont_write_bytecode = True
    event = sys.argv[1] if len(sys.argv) == 2 else None
    if event not in EVENTS:
        sys.stdout.write("{}\n")
        return
    output = {}
    deadline = time.monotonic() + SECONDS
    fallback = ({"hookSpecificOutput": {"hookEventName": event,
                 "additionalContext": "阻力燈｜cached 狀態未能核實；未重新檢查，唔代表檢查通過。"}}
                if event == "SessionStart" else {})

    def expired(_number, _frame):
        raise TimeoutError()

    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        tools = Path(__file__).resolve(strict=True).parents[1]
        sys.path.insert(0, str(tools))
        from lantern_common.json import decode_json
        from friction_lantern.hooks import run_hook

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


if __name__ == "__main__":
    main()
