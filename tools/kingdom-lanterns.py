#!/usr/bin/env python3
"""四燈薄入口，唔信任 caller cwd 作 module source。"""
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def main(argv=None):
    from lantern_suite.cli import main as run
    return run(argv, repo_root=TOOLS.parent)


if __name__ == "__main__":
    raise SystemExit(main())
