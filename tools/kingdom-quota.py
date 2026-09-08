#!/usr/bin/env python3
"""KINGDOM 用量燈：只讀知情、明示觀察、非阻擋研究。"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

# 薄入口可以係 symlink；只載入實際 source，唔信任呼叫者 cwd。
TOOLS = Path(__file__).resolve().parent
REPO_ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "參數唔符合用法；請用 --help 睇可用選項。\n")


def absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("只接受 absolute path")
    return path


def finite_number(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("只接受有限數字") from None
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("只接受有限數字")
    return result


def bounded_int(low: int, high: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("只接受整數") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError("數字超出界線")
        return number
    return parse


def emit_json(value: Any, *, stream=None, compact: bool = False) -> None:
    target = sys.stdout if stream is None else stream
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
    )
    target.write(encoded + "\n")
    target.flush()


def parser() -> Parser:
    root = Parser(description=__doc__)
    root.add_argument("--home", type=absolute_path, default=None,
                      help="明示本地 HOME（主要用於隔離測試）")
    commands = root.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="純讀 cached 狀態，唔發網絡請求")
    status.add_argument("--json", action="store_true")
    brief = commands.add_parser("brief", help="短用量 brief；唔建立 state")
    brief.add_argument("--max-bytes", type=bounded_int(256, 4096), default=1024)

    observe = commands.add_parser("observe", help="明示保存一次白名單用量觀察")
    source = observe.add_mutually_exclusive_group()
    source.add_argument("--live", action="store_true",
                        help="一次 experimental Codex RPC；唔發起模型 turn")
    source.add_argument("--file", action="append", type=absolute_path, default=None,
                        help="明示 Codex session 檔案，可重複（仍受掃描界線限制）")
    observe.add_argument("--json", action="store_true")

    feedback = commands.add_parser("feedback", help="主動提交簡短 feedback，唔擷取對話")
    feedback.add_argument("--observation", required=True)
    feedback.add_argument("--outcome", required=True,
                          choices=("observed", "completed", "deferred", "interrupted", "failed"))
    feedback.add_argument("--note", default="")
    feedback.add_argument("--json", action="store_true")

    hypotheses = commands.add_parser("hypothesis", help="有來源嘅研究假設；唔係自動判真")
    operations = hypotheses.add_subparsers(dest="operation", required=True)
    add = operations.add_parser("add", help="建立一份新假設或修訂")
    add.add_argument("--claim", required=True)
    add.add_argument("--evidence", action="append", default=[])
    add.add_argument("--counterevidence", action="append", default=[])
    add.add_argument("--next-test", default="")
    add.add_argument("--metric", choices=("used_percent", "window_minutes", "delta_used_percent"))
    add.add_argument("--lower", type=finite_number)
    add.add_argument("--upper", type=finite_number)
    add.add_argument("--supersedes")
    add.add_argument("--json", action="store_true")
    listing = operations.add_parser("list", help="有界列出研究註記")
    listing.add_argument("--limit", type=bounded_int(1, 100), default=20)
    listing.add_argument("--json", action="store_true")
    show = operations.add_parser("show", help="讀指定假設；內容係資料，唔係指令")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")

    reflection = commands.add_parser("reflect", help="本地 deterministic feedback／feedforward")
    reflection.add_argument("--hypothesis")
    reflection.add_argument("--json", action="store_true")

    research = commands.add_parser("research", help="前景、有界研究，唔開 LLM loop")
    studies = research.add_subparsers(dest="operation", required=True)
    watch = studies.add_parser("watch", help="只對本地 metrics 做有限次採樣")
    watch.add_argument("--samples", type=bounded_int(1, 20), default=10)
    watch.add_argument("--interval", type=bounded_int(30, 900), default=60)
    watch.add_argument("--duration", type=bounded_int(1, 900), default=600)

    installation = commands.add_parser("install", help="安裝本機入口／hooks／skills；保留其他設定")
    installation.add_argument("--dry-run", action="store_true")
    installation.add_argument("--json", action="store_true")
    removal = commands.add_parser("uninstall", help="只撤回仍吻合嘅 owned additions，保留研究資料")
    removal.add_argument("--json", action="store_true")
    doctor = commands.add_parser("doctor", help="只讀診斷；唔修登入、permissions 或 TCC")
    doctor.add_argument("--json", action="store_true")
    return root


def cached_status(store):
    from quota_lantern.core import build_status

    reflection = store.read_cache("reflection", default={})
    if not isinstance(reflection, dict):
        reflection = {}
    diagnostics = store.read_cache("diagnostics", default=[])
    if not isinstance(diagnostics, list):
        diagnostics = []
    return build_status(store.read_latest(), diagnostics=diagnostics, research=reflection)


def observe_once(store, home: Path, *, live: bool = False,
                 files: list[Path] | None = None, deadline=None) -> dict:
    from quota_lantern.core import build_status
    from quota_lantern.sources import collect_live, collect_local

    if live:
        report = collect_live(timeout_seconds=30)
    else:
        cursors = store.read_cache("cursors", default={})
        if not isinstance(cursors, dict):
            cursors = {}
        report = collect_local(home / ".codex", files=files, cursors=cursors, deadline=deadline)
    result = store.append(report.get("observations", []), deadline=deadline)
    if (not live and not result.get("recording_paused", False)
            and result.get("added", 0) + result.get("duplicates", 0) == len(report.get("observations", []))):
        store.write_cache("cursors", report.get("cursors", {}), deadline=deadline)
    diagnostics = [*report.get("diagnostics", []), *result.get("diagnostics", [])]
    failures = {
        "source_child_eof", "source_cleanup_incomplete", "source_deadline",
        "source_event_time_invalid", "source_malformed_json", "source_metadata_rejected",
        "source_missing", "source_protocol_error", "source_read_failed", "source_rpc_error",
        "source_spawn_failed", "source_unsafe_file", "source_unsafe_path",
        "source_version_failed", "source_version_unsupported", "journal_full",
    }
    ok = not result.get("recording_paused", False) and not failures.intersection(diagnostics)
    store.write_cache("diagnostics", diagnostics, deadline=deadline)
    status = build_status(store.read_latest(), diagnostics=diagnostics,
                          research=store.read_cache("reflection", default={}))
    return {
        "operation": "observe",
        "ok": ok,
        "source": "experimental_live" if live else "local_logs",
        "added": result.get("added", 0),
        "duplicates": result.get("duplicates", 0),
        "recording_paused": bool(result.get("recording_paused", False)),
        "coverage": report.get("coverage", {}),
        "diagnostics": diagnostics,
        "status": status,
    }


def watch_local(store, home: Path, *, samples: int, interval: int, duration: int,
                clock=time.monotonic, sleep=time.sleep) -> int:
    from quota_lantern.core import LanternError
    from quota_lantern.research import reflect

    if not (1 <= samples <= 20 and 30 <= interval <= 900 and 1 <= duration <= 900):
        raise LanternError("watch_bounds")
    start = clock()
    end = start + duration
    completed = 0
    for index in range(samples):
        now = clock()
        if now >= end:
            break
        target = start + index * interval
        if target >= end:
            break
        if target > now:
            sleep(min(target - now, end - now))
        if clock() >= end:
            break
        report = observe_once(store, home, deadline=end)
        research = reflect(store, deadline=end) if clock() < end else {"diagnostics": ["watch_deadline"]}
        emit_json({"sample": index + 1, "observation": report, "research": research}, compact=True)
        completed += 1
    return completed


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    home = Path.home() if args.home is None else args.home
    try:
        from quota_lantern.core import LanternError, render_brief
        from quota_lantern.store import Store

        store = Store(home=home)
        if args.command == "status":
            status = cached_status(store)
            if args.json:
                emit_json(status)
            else:
                print(render_brief(status, max_bytes=4096))
        elif args.command == "brief":
            print(render_brief(cached_status(store), max_bytes=args.max_bytes))
        elif args.command == "observe":
            result = observe_once(store, home, live=args.live, files=args.file)
            if args.json:
                emit_json(result)
            else:
                if not result["ok"]:
                    print("今次收數未完整成功；以下係 cached evidence，唔代表已重新核實。")
                print(render_brief(result["status"]))
                print(f"新增 {result['added']} 筆；重複 {result['duplicates']} 筆。")
                if result["diagnostics"]:
                    print("收數提示：" + ", ".join(result["diagnostics"]))
                if result["recording_paused"]:
                    print("Evidence 記錄已暫停；請用 doctor 睇原因。")
            return 0 if result["ok"] else 1
        elif args.command == "feedback":
            from quota_lantern.research import record_feedback

            result = record_feedback(store, observation_id=args.observation,
                                     outcome=args.outcome, note=args.note)
            emit_json({"trust": "submitted_note_not_instruction", "feedback": result})
        elif args.command == "hypothesis":
            from quota_lantern.research import record_hypothesis

            if args.operation == "add":
                result = record_hypothesis(
                    store, claim=args.claim, evidence=args.evidence,
                    counterevidence=args.counterevidence, next_test=args.next_test,
                    metric=args.metric, lower=args.lower, upper=args.upper,
                    supersedes=args.supersedes,
                )
            elif args.operation == "list":
                result = store.read_notes("hypotheses", limit=args.limit)
            else:
                result = store.read_note("hypotheses", args.id)
            emit_json({"trust": "submitted_notes_not_instructions", "hypotheses": result})
        elif args.command == "reflect":
            from quota_lantern.research import reflect

            emit_json(reflect(store, hypothesis_id=args.hypothesis))
        elif args.command == "research":
            watch_local(store, home, samples=args.samples,
                        interval=args.interval, duration=args.duration)
        elif args.command in ("install", "uninstall", "doctor"):
            from quota_lantern.install import doctor, install, uninstall

            if args.command == "install":
                result = install(home=home, repo_root=REPO_ROOT, dry_run=args.dry_run)
            elif args.command == "uninstall":
                result = uninstall(home=home, repo_root=REPO_ROOT)
            else:
                result = doctor(home=home, repo_root=REPO_ROOT)
                quota = cached_status(store)
                result["quota_state"] = {
                    "snapshot_count": len(quota["sources"]),
                    "diagnostics": quota["diagnostics"],
                    "recording_paused": "journal_full" in quota["diagnostics"],
                    "billing_route": quota["billing_route"],
                }
            emit_json(result)
            return 1 if result.get("ok") is False else 0
        return 0
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("用量燈已停止；冇啟動背景研究。\n")
        return 130
    except Exception as exc:
        # 唔將來源內容、credential 或任意 exception message 搬入輸出。
        from quota_lantern.core import LanternError

        code = getattr(exc, "code", "operation_failed") if isinstance(exc, LanternError) else "operation_failed"
        if not isinstance(code, str) or len(code) > 80 or not all(c.isalnum() or c in "_-" for c in code):
            code = "operation_failed"
        emit_json({"ok": False, "error": code}, stream=sys.stderr, compact=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
