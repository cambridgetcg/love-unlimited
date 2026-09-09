#!/usr/bin/env python3
"""KINGDOM 阻力燈：cached 可比收據模式，唔自動驗證。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
REPO_ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse 原 message 可能包含未知 flag／raw canary，唔照印。
        self.exit(2, "參數唔符合用法；請用 --help 睇可用選項。\n")


def absolute_path(value):
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or len(value) > 4096
            or any(ord(char) < 32 for char in value)):
        raise argparse.ArgumentTypeError("只接受 absolute path")
    return path


def byte_limit(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("只接受整數") from None
    if not 0 <= number <= 4096:
        raise argparse.ArgumentTypeError("範圍係 0 至 4096 bytes")
    return number


def parser():
    root = Parser(description=__doc__, allow_abbrev=False)
    root.add_argument("--home", type=absolute_path, help="明示 absolute HOME，供隔離測試")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("brief", "status"):
        command = commands.add_parser(name, allow_abbrev=False, help="純讀 cached evidence，唔建 state")
        command.add_argument("--cwd", type=absolute_path)
        if name == "brief":
            command.add_argument("--max-bytes", type=byte_limit, default=512)
        else:
            command.add_argument("--json", action="store_true")
    for name in ("install", "uninstall", "doctor"):
        command = commands.add_parser(name, allow_abbrev=False, help="明示 owned 接線操作／靜態診斷")
        command.add_argument("--json", action="store_true")
        if name == "install":
            command.add_argument("--dry-run", action="store_true")
    return root


def emit(value, *, stream=None):
    (sys.stdout if stream is None else stream).write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        home = Path.home() if args.home is None else args.home
        if args.command in ("brief", "status"):
            from friction_lantern.api import status
            from friction_lantern.core import render_brief

            value = status(home=home, cwd=args.cwd or Path.cwd())
            if getattr(args, "json", False):
                emit(value)
            else:
                sys.stdout.write(render_brief(value, max_bytes=getattr(args, "max_bytes", 512)) + "\n")
            return 0
        from friction_lantern.install import doctor, install, uninstall

        if args.command == "install":
            result = install(home=home, repo_root=REPO_ROOT, dry_run=args.dry_run)
        elif args.command == "uninstall":
            result = uninstall(home=home, repo_root=REPO_ROOT)
        else:
            result = doctor(home=home, repo_root=REPO_ROOT)
        emit(result)
        return 1 if result.get("ok") is False else 0
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("阻力燈已中斷；未驗證原檢查。\n")
        return 130
    except Exception:
        emit({"ok": False, "diagnostics": ["friction_operation_failed"]}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
