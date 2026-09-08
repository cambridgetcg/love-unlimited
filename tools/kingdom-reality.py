#!/usr/bin/env python3
"""KINGDOM 實況燈：本地程序收據、版本失效提示，唔自動驗證。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 入口可以係 symlink；只載入真正 source，唔信任呼叫者 cwd。
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


def byte_limit(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("只接受整數") from None
    if not 256 <= result <= 4096:
        raise argparse.ArgumentTypeError("範圍係 256 至 4096 bytes")
    return result


def emit_json(value, *, stream=None) -> None:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    target = sys.stdout if stream is None else stream
    target.write(encoded + "\n")
    target.flush()


def parser() -> Parser:
    root = Parser(description=__doc__)
    root.add_argument("--home", type=absolute_path, default=None,
                      help="明示本地 HOME（主要用於隔離測試）")
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="包住原本要跑嘅命令，記實際 exit 同版本")
    check.add_argument("--kind", choices=("tests", "build", "check"), default="check")
    check.add_argument("--cwd", type=absolute_path, default=None)
    check.add_argument("argv", nargs=argparse.REMAINDER, help="-- 後面係原命令 argv")
    for name in ("status", "brief", "observe"):
        command = commands.add_parser(name, help={
            "status": "純讀 cached evidence，唔重新掃 repo",
            "brief": "短版 cached 提示，唔建立 state",
            "observe": "明示重新比較已有收據，唔跑 tests",
        }[name])
        command.add_argument("--cwd", type=absolute_path, default=None)
        if name == "brief":
            command.add_argument("--max-bytes", type=byte_limit, default=768)
        else:
            command.add_argument("--json", action="store_true")
    for name in ("capacity", "rotate"):
        command = commands.add_parser(name, help={
            "capacity": "純讀收據／archive 容量，唔建 state",
            "rotate": "明示 durable archive 後清空 active receipts，pending 拒絕",
        }[name])
        command.add_argument("--json", action="store_true")
        if name == "rotate":
            command.add_argument("--archive", action="store_true", required=True)
    for name in ("install", "uninstall", "doctor"):
        command = commands.add_parser(name, help={
            "install": "安裝 owned 接線，保留原設定",
            "uninstall": "只撤回仍吻合嘅 owned 接線，保留 evidence",
            "doctor": "純讀接線診斷，唔修 permissions／登入",
        }[name])
        command.add_argument("--json", action="store_true")
        if name == "install":
            command.add_argument("--dry-run", action="store_true")
    return root


def cached_scope(store, cwd: Path):
    from reality_lantern.snapshot import scope_candidates

    key = store.get_key(create=False)
    if key is None:
        return {}, None, None
    state = store.read_state()
    scopes = state.get("scopes", {})
    scope_id = next((item for item in scope_candidates(cwd, key=key) if item in scopes), None)
    return state, scope_id, key


def cached_status(store, cwd: Path) -> dict:
    from reality_lantern.core import build_status

    state, scope_id, _ = cached_scope(store, cwd)
    # 冇當前 scope 就唔借其他 worktree 嘅證據。
    return build_status(state if scope_id is not None else None, scope_id=scope_id)


def observe_once(store, cwd: Path) -> dict:
    from reality_lantern.core import build_status
    from reality_lantern.snapshot import capture

    state, scope_id, key = cached_scope(store, cwd)
    if scope_id is None:
        return {"ok": True, "operation": "observe", "diagnostics": ["no_receipts"],
                "status": build_status(None)}
    generation = state["scopes"][scope_id]["generation"]
    snapshot = capture(cwd, key=key)
    if snapshot["scope_id"] != scope_id:
        store.invalidate(scope_id, changed=False)
        return {"ok": False, "operation": "observe", "diagnostics": ["scope_changed"],
                "status": cached_status(store, cwd)}
    result = store.observe(snapshot, expected_generation=generation)
    return {"ok": bool(snapshot["complete"]) and result.get("ok", True),
            "operation": "observe", "diagnostics": result.get("diagnostics", snapshot["diagnostics"]),
            "status": cached_status(store, cwd)}


def main(argv: list[str] | None = None) -> int:
    root = parser()
    args = root.parse_args(argv)
    if args.command == "check":
        if not args.argv or args.argv[0] != "--" or len(args.argv) == 1:
            root.error("check 需要 -- 後面嘅命令")
        args.argv = args.argv[1:]
    try:
        from reality_lantern.core import render_brief
        from reality_lantern.store import Store

        home = Path.home() if args.home is None else args.home
        cwd = getattr(args, "cwd", None) or Path.cwd()
        store = Store(home=home)
        if args.command == "check":
            from reality_lantern.runner import run_check

            return run_check(args.argv, cwd=cwd, kind=args.kind, store=store)
        if args.command in ("brief", "status"):
            status = cached_status(store, cwd)
            if getattr(args, "json", False):
                emit_json(status)
            else:
                print(render_brief(status, max_bytes=getattr(args, "max_bytes", 4096)))
        elif args.command == "observe":
            result = observe_once(store, cwd)
            if args.json:
                emit_json(result)
            else:
                if not result["ok"]:
                    print("今次比較未完整成功；以下係 cached evidence，唔係重新驗證通過。")
                print(render_brief(result["status"]))
            return 0 if result["ok"] else 1
        elif args.command in ("capacity", "rotate"):
            result = store.capacity() if args.command == "capacity" else store.rotate(archive=args.archive)
            emit_json(result)
            return 0 if result["ok"] else 1
        elif args.command in ("install", "uninstall", "doctor"):
            from reality_lantern.install import doctor, install, uninstall

            if args.command == "install":
                result = install(home=home, repo_root=REPO_ROOT, dry_run=args.dry_run)
            elif args.command == "uninstall":
                result = uninstall(home=home, repo_root=REPO_ROOT)
            else:
                result = doctor(home=home, repo_root=REPO_ROOT)
            emit_json(result)
            return 1 if result.get("ok") is False else 0
        return 0
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("實況燈已中斷；未完成嘅 run 唔當成功。\n")
        return 130
    except Exception:
        # 失敗只報固定診斷，唔洩露 argv／path／原始 exception。
        emit_json({"ok": False, "error": "reality_operation_failed"}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
