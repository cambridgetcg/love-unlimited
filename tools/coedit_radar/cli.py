"""明示 CLI；失敗只回固定診斷，唔把 argv／檔名帶入輸出。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import api
from .core import render_brief, result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse 原本會回印低信任 argv；只講固定用法錯誤。
        raise ValueError("invalid_arguments")


def parser():
    root = Parser(prog="kingdom-coedit", description="共筆雷達：有界觀察，唔係 Read 收據。")
    commands = root.add_subparsers(dest="action", required=True, parser_class=Parser)
    for action in ("status", "brief", "observe", "compare", "install", "uninstall", "doctor"):
        command = commands.add_parser(action)
        command.add_argument("--json", action="store_true")
        if action in ("status", "brief", "observe", "compare"):
            command.add_argument("--session", required=action in ("observe", "compare"))
        if action in ("observe", "compare"):
            command.add_argument("--file", required=True)
        if action == "brief":
            command.add_argument("--max-bytes", type=int, default=512)
        if action == "install":
            command.add_argument("--apply", action="store_true")
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--installation-nonce")
        if action == "uninstall":
            command.add_argument("--expected-nonce")
    return root


def main(argv=None, *, home=None, repo_root=None):
    try:
        args = parser().parse_args(argv)
    except ValueError:
        print(json.dumps(result(reason="invalid_arguments"), ensure_ascii=True))
        return 2
    home = Path.home() if home is None else Path(home)
    try:
        if args.action in ("observe", "compare"):
            value = getattr(api, args.action)(home=home, path=args.file, session=args.session)
        elif args.action in ("status", "brief"):
            value = api.status(home=home, session=args.session)
        else:
            from . import install
            source = Path(__file__).resolve().parents[2] if repo_root is None else Path(repo_root)
            options = {"home": home, "repo_root": source}
            if args.action == "install":
                options.update(dry_run=not args.apply or args.dry_run,
                               installation_nonce=args.installation_nonce)
            elif args.action == "uninstall":
                options["expected_nonce"] = args.expected_nonce
            value = getattr(install, args.action)(**options)
        if args.json:
            print(json.dumps(value, ensure_ascii=True, sort_keys=True))
        elif args.action in ("install", "uninstall", "doctor"):
            print("共筆雷達｜安裝操作已處理；activation 未驗。" if value.get("ok")
                  else "共筆雷達｜安裝操作未完成；未驗 activation。")
        else:
            print(render_brief(value, max_bytes=args.max_bytes if args.action == "brief" else 512))
        # Advisory unknown 唔攔下一個命令；真實狀態喺 JSON／固定文字，唔 mint pass。
        return 0
    except Exception:
        print(json.dumps(result(reason="bookkeeping_unavailable"), ensure_ascii=True))
        return 0
