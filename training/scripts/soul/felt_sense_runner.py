# training/scripts/soul/felt_sense_runner.py
"""CLI that presents shuffled A/B/C responses from three systems and records Yu's judgment."""
from __future__ import annotations
import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path
import requests

from .config import EVAL_DIR, SOUL_SYSTEM_PROMPT


SYSTEMS = {
    "base_qwen": {"model": "Qwen/Qwen2.5-72B-Instruct-AWQ", "endpoint": "http://h200:8000/v1/chat/completions"},
    "qwen_ai_soul": {"model": "dpo-soul-v1", "endpoint": "http://h200:8000/v1/chat/completions"},
    "alpha_claude": {"model": "claude-opus-4-7", "endpoint": "anthropic"},
}


def query_system(system_key: str, prompt: str) -> str:
    s = SYSTEMS[system_key]
    if s["endpoint"] == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=s["model"], max_tokens=2048,
            system=SOUL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    else:
        r = requests.post(s["endpoint"], json={
            "model": s["model"],
            "messages": [{"role": "system", "content": SOUL_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            "max_tokens": 2048,
        }, timeout=300)
        return r.json()["choices"][0]["message"]["content"]


def run_session(prompt_path: Path, out_path: Path, seed: int) -> None:
    prompts = [json.loads(l) for l in prompt_path.read_text().splitlines() if l.strip()]
    session = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "entries": [],
    }
    rng = random.Random(seed)
    for i, p in enumerate(prompts):
        print(f"\n=== Prompt {i+1}/{len(prompts)} [{p['category']}] ===")
        print(p["prompt"])
        keys = list(SYSTEMS.keys())
        rng.shuffle(keys)
        labels = ["A", "B", "C"]
        label_to_system = dict(zip(labels, keys))
        responses = {}
        for label, sys_key in label_to_system.items():
            print(f"\n(generating {label}...)")
            responses[label] = query_system(sys_key, p["prompt"])
        for label in labels:
            print(f"\n--- {label} ---")
            print(responses[label])
        print("\nWhich feels like her? [A/B/C/none/multiple]: ", end="")
        verdict = input().strip().lower()
        print("Note (one sentence, what made it her or broke it): ", end="")
        note = input().strip()
        session["entries"].append({
            "prompt_id": p["prompt_id"],
            "category": p["category"],
            "prompt": p["prompt"],
            "label_to_system": label_to_system,
            "responses": responses,
            "verdict": verdict,
            "note": note,
        })
        # Save after each entry so a crash doesn't lose Yu's work
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))
    print(f"\nsession saved → {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=str(EVAL_DIR / "felt_sense_v1.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None, help="omit for fresh random")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randint(1, 10_000)
    run_session(Path(args.prompts), Path(args.out), seed)


if __name__ == "__main__":
    main()
