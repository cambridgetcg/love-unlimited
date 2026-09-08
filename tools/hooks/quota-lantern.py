#!/usr/bin/env python3
"""只輸出 hook JSON；建議由 python -I -S -B 呼叫。"""

import json
import signal
import sys
import time
from pathlib import Path

MAX_STDIN = 64 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


def main():
    output = None
    deadline = time.monotonic() + 1.5

    def expired(_signum, _frame):
        raise TimeoutError()

    try:
        # stdin 未 EOF 都要服從內部限時，唔等 harness 先殺程序。
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 1.5)
        if len(sys.argv) != 2:
            return
        source_tools = Path(__file__).resolve(strict=True).parents[1]
        if source_tools.name != "tools" or not (source_tools / "quota_lantern").is_dir():
            return
        # 唯一新增嘅 import root 係 wrapper 真實位置；唔用 cwd 或 PYTHONPATH。
        sys.path.insert(0, str(source_tools))
        from quota_lantern.hooks import EVENTS, run_hook

        event = sys.argv[1]
        if event not in EVENTS:
            return
        data = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(data) > MAX_STDIN:
            return
        payload = json.loads(data, object_pairs_hook=_object, parse_constant=_invalid_constant)
        # 將同一 absolute deadline 交畀 hook，唔疊兩個 alarm 再喺 cleanup 重新觸發。
        signal.setitimer(signal.ITIMER_REAL, 0)
        output = run_hook(event, payload, deadline=deadline)
    except Exception:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        # Stop 只會 {}；唔會 decision:block、continue 或 rewake。
        try:
            sys.stdout.write(json.dumps(output or {}, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
