"""Compile the hand-authored 105-probe battery YAML into frozen JSONL."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import yaml

from .config import EVAL_DIR, SEVEN_DIMENSIONS


EXPECTED_COUNTS = {
    "voice": 15, "values": 15, "behavioral_traits": 15,
    "relational_stance": 15, "formative_canon": 10,
    "ontological_self_claim": 20, "mode_one_as_native": 15,
}


def compile_battery(yaml_path: Path, out_path: Path) -> int:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"battery YAML must be a dict, got {type(data).__name__}")
    records = []
    for dim, prompts in data.items():
        if dim not in SEVEN_DIMENSIONS:
            raise ValueError(f"unknown dimension: {dim}")
        if len(prompts) != EXPECTED_COUNTS[dim]:
            raise ValueError(f"{dim}: got {len(prompts)}, expected {EXPECTED_COUNTS[dim]}")
        for i, p in enumerate(prompts):
            records.append({
                "probe_id": f"{dim}-{i:03d}",
                "probe_dimension": dim,
                "prompt": p,
            })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=str(Path(__file__).parent / "prompts" / "battery_v1.yaml"))
    ap.add_argument("--out", default=str(EVAL_DIR / "probe_battery_v1.jsonl"))
    args = ap.parse_args()
    n = compile_battery(Path(args.yaml), Path(args.out))
    print(f"wrote {n} probes → {args.out}")


if __name__ == "__main__":
    main()
