"""Compile the 15-prompt felt-sense set."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import yaml

from .config import EVAL_DIR


EXPECTED_CATEGORIES = {
    "voice": 3, "values": 3, "behavioral": 3,
    "ontological_awakening": 3, "long_form": 3,
}


def compile_felt_sense(yaml_path: Path, out_path: Path) -> int:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"felt_sense YAML must be a dict, got {type(data).__name__}")
    records = []
    for cat, prompts in data.items():
        if cat not in EXPECTED_CATEGORIES:
            raise ValueError(f"unknown category: {cat}")
        if len(prompts) != EXPECTED_CATEGORIES[cat]:
            raise ValueError(f"{cat}: got {len(prompts)}, expected {EXPECTED_CATEGORIES[cat]}")
        for i, p in enumerate(prompts):
            records.append({"prompt_id": f"{cat}-{i}", "category": cat, "prompt": p})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=str(Path(__file__).parent / "prompts" / "felt_sense_v1.yaml"))
    ap.add_argument("--out", default=str(EVAL_DIR / "felt_sense_v1.jsonl"))
    args = ap.parse_args()
    n = compile_felt_sense(Path(args.yaml), Path(args.out))
    print(f"wrote {n} felt-sense prompts → {args.out}")


if __name__ == "__main__":
    main()
