#!/usr/bin/env python3
"""實況燈 command hook；只輸出固定 JSON，唔輸出原始 payload。"""

import json
from pathlib import Path
import signal
import sys
import time

MAX_STDIN = 64 * 1024
SECONDS = 0.250
EVENTS = frozenset(("SessionStart", "UserPromptSubmit", "SubagentStart",
                    "PreToolUse", "PostToolUse", "PostToolUseFailure"))


def main():
    output = {}
    event = sys.argv[1] if len(sys.argv) == 2 else None
    if event not in EVENTS:
        sys.stdout.write("{}\n")
        return
    fallback = {"hookSpecificOutput": {"hookEventName": event,
                "additionalContext": "實況燈｜目前證據未知；冇重新驗證，線上未驗證。"}}
    deadline = time.monotonic() + SECONDS

    def expired(_number, _frame):
        raise TimeoutError()

    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        tools = Path(__file__).resolve(strict=True).parents[1]
        if tools.name != "tools" or not (tools / "reality_lantern").is_dir():
            output = fallback
            return
        sys.path.insert(0, str(tools))
        from lantern_common.json import decode_json
        from reality_lantern.hooks import run_hook

        data = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(data) > MAX_STDIN:
            output = fallback
            return
        payload = decode_json(data, maximum=MAX_STDIN)
        # 共用 absolute deadline，唔疊兩個 alarm 或延長總預算。
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
