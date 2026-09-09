"""四燈 CLI：純讀入口同明示安裝／撤回／本地 bundle，唔代跑檢查。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage()
        self.exit(2, "參數唔符合用法；請用 --help。\n")


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("需要 absolute path")
    return path


def byte_limit(value):
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("需要整數") from None
    if not 768 <= count <= 4096:
        raise argparse.ArgumentTypeError("需要 768 至 4096 bytes")
    return count


def parser():
    root = Parser(description=__doc__)
    root.add_argument("--home", type=absolute, default=None)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("list", "brief", "status", "doctor", "install", "uninstall"):
        command = commands.add_parser(name)
        if name != "brief":
            command.add_argument("--json", action="store_true")
        if name in ("brief", "status"):
            command.add_argument("--cwd", type=absolute)
            command.add_argument("--session", default=None)
        if name == "brief":
            command.add_argument("--max-bytes", type=byte_limit, default=1536)
        if name == "install":
            command.add_argument("--dry-run", action="store_true")
    bundle = commands.add_parser("bundle", help="明示 allowlist／build／verify／install")
    operations = bundle.add_subparsers(dest="operation", required=True)
    build = operations.add_parser("build")
    build.add_argument("--output", type=absolute, required=True)
    build.add_argument("--base-revision", default=None)
    verify = operations.add_parser("verify")
    verify.add_argument("--bundle", type=absolute, required=True)
    verify.add_argument("--expected-sha256", required=True)
    install = operations.add_parser("install", help="先驗 bundle，再永久 staging／owned install",
                                   description="沿可信 source bootstrap 先驗 SHA／完整 manifest，再 staging／安裝；用全域 --home，唔 silent relocate。")
    install.add_argument("--bundle", type=absolute, required=True, metavar="ABS")
    install.add_argument("--expected-sha256", required=True, metavar="HEX", help="由獨立可信渠道取得嘅 archive SHA256")
    operations.add_parser("allowlist")
    return root


def main(argv=None, *, repo_root=None):
    args = parser().parse_args(argv)
    home = Path.home() if args.home is None else args.home
    repo = Path(__file__).resolve().parents[2] if repo_root is None else Path(repo_root)
    try:
        if args.command == "list":
            from .catalog import catalog
            value = {"products": catalog(), "activation": "unverified"}
        elif args.command in ("brief", "status"):
            from .api import brief, status
            if args.command == "brief":
                print(brief(home=home, cwd=args.cwd, session=args.session, max_bytes=args.max_bytes))
                return 0
            value = status(home=home, cwd=args.cwd, session=args.session)
        elif args.command in ("install", "uninstall", "doctor"):
            from . import install
            operation = getattr(install, args.command)
            value = operation(home=home, repo_root=repo, **({"dry_run": args.dry_run} if args.command == "install" else {}))
        else:
            from . import bundle
            if args.operation == "build":
                value = bundle.build(repo_root=repo, output=args.output, base_revision=args.base_revision)
            elif args.operation == "verify":
                value = bundle.verify(args.bundle, expected_sha256=args.expected_sha256)
            elif args.operation == "install":
                value = bundle.install(args.bundle, expected_sha256=args.expected_sha256, home=home)
            else:
                value = {"files": bundle.allowlist()}
        print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
        return 1 if value.get("ok") is False else 0
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"ok": False, "diagnostics": ["suite_operation_unavailable"], "activation": "unverified"}))
        return 1
