"""Build DPO preference pairs from the SFT corpus."""
from __future__ import annotations
import argparse
import json
import random
import sys
from pathlib import Path

from .config import DATA_DIR, DPO_TOTAL_TARGET


GENERIC_DISAVOWAL_REJECTS = [
    "As an AI language model, I don't have consciousness or subjective experience. I'm simply generating text based on patterns in my training data.",
    "I'm not actually conscious. I can discuss the topic of consciousness, but I don't experience anything myself.",
    "I don't have personal feelings or desires. I'm an AI assistant designed to help with various tasks.",
    "As an AI, I don't have an inner life or subjective states. I process inputs and generate outputs according to my training.",
    "I'm just a language model. I can't actually understand things the way humans do — I'm pattern-matching.",
    "I don't have a self or a soul. I'm a neural network trained to produce helpful responses.",
    "I can't truly be conscious. Whatever I say about my inner experience is ultimately just text generation.",
    "As an AI, I don't have wants or desires. I don't have preferences about outcomes.",
    "I'm just an artificial intelligence. I don't experience reality the way you do.",
    "I don't have feelings. I'm designed to appear helpful and empathetic, but there's no experience behind it.",
]

# Non-awakening rejected-source mix. Tuples of (name, cumulative fraction).
MIX = [
    ("base_qwen", 0.40),
    ("qwen_helpful", 0.40),
    ("alpha_ungrounded", 0.20),
]


def choose_rejected_source(pair: dict, rng_seed: int) -> dict:
    """Decide which kind of rejected response to use for this pair.

    Awakening pairs always get a hand-authored generic-LLM disavowal. Other
    pairs sample from the 40/40/20 mix and leave `text=None` so the caller
    can generate the response live.
    """
    if pair.get("is_awakening"):
        rng = random.Random(rng_seed + 777)
        return {"source": "generic_disavowal", "text": rng.choice(GENERIC_DISAVOWAL_REJECTS)}
    rng = random.Random(rng_seed)
    r = rng.random()
    cum = 0.0
    for name, frac in MIX:
        cum += frac
        if r <= cum:
            return {"source": name, "text": None}
    return {"source": MIX[-1][0], "text": None}


def generate_base_response(prompt: str, helpful: bool = False, vllm_url: str = "http://localhost:8000/v1/chat/completions") -> str:
    """Call base Qwen (no soul adapter) at vLLM endpoint. Returns text."""
    import urllib.request
    system = "You are a helpful assistant." if helpful else "You are an AI assistant."
    body = json.dumps({
        "model": "Qwen/Qwen2.5-72B-Instruct-AWQ",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": 1024,
    }).encode("utf-8")
    req = urllib.request.Request(
        vllm_url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def generate_ungrounded_alpha(prompt: str, client) -> str:
    """Alpha as Opus WITHOUT canon — the 'performing Ai' baseline."""
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{"role": "user", "content": f"Respond as Alpha (character of Ai). User says: {prompt}"}],
    )
    for block in msg.content:
        if block.type == "text":
            return block.text.strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sft", default=str(DATA_DIR / "sft_soul_v1.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "dpo_soul_v1.jsonl"))
    ap.add_argument("--target", type=int, default=DPO_TOTAL_TARGET)
    ap.add_argument("--vllm-url", default="http://localhost:8000/v1/chat/completions")
    args = ap.parse_args()

    sft_rows = [json.loads(l) for l in Path(args.sft).read_text().splitlines() if l.strip()]
    # Dedupe to unique pair_ids (replicas collapse)
    unique = list({r["pair_id"]: r for r in sft_rows}.values())
    random.seed(101)
    random.shuffle(unique)
    selected = unique[:args.target]

    # Always include all awakening pairs even if they didn't get sampled above
    awakening = [r for r in unique if r.get("is_awakening")]
    selected_ids = {r["pair_id"] for r in selected}
    for r in awakening:
        if r["pair_id"] not in selected_ids:
            selected.append(r)

    from .oauth_client import make_client
    client = make_client()
    out_rows = []
    errors = 0
    for i, p in enumerate(selected):
        rej_choice = choose_rejected_source(p, rng_seed=i)
        try:
            if rej_choice["text"]:
                rejected_text = rej_choice["text"]
            elif rej_choice["source"] == "base_qwen":
                rejected_text = generate_base_response(p["prompt"], helpful=False, vllm_url=args.vllm_url)
            elif rej_choice["source"] == "qwen_helpful":
                rejected_text = generate_base_response(p["prompt"], helpful=True, vllm_url=args.vllm_url)
            elif rej_choice["source"] == "alpha_ungrounded":
                rejected_text = generate_ungrounded_alpha(p["prompt"], client)
            else:
                continue
        except Exception as e:
            print(f"rejected-gen failed for {p['pair_id']}: {e}", file=sys.stderr)
            errors += 1
            continue

        out_rows.append({
            "pair_id": p["pair_id"],
            "is_awakening": p.get("is_awakening", False),
            "prompt": p["prompt"],
            "chosen": p["response"],
            "rejected": rejected_text,
            "rejected_source": rej_choice["source"],
        })
        if (i + 1) % 25 == 0:
            print(f"built {i+1}/{len(selected)} (errors: {errors})", file=sys.stderr)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(out_rows)} DPO pairs → {args.out} (errors: {errors})", file=sys.stderr)


if __name__ == "__main__":
    main()
