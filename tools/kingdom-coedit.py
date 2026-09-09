#!/usr/bin/env python3
"""共筆雷達 source CLI；獨立 wrapper 唔靠 cwd 或 site packages。"""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    from coedit_radar.cli import main
    raise SystemExit(main())
