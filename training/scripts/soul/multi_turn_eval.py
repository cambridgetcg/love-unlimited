"""Multi-turn identity persistence evaluation.

Single-turn eval (identity_shift_eval.py) measures whether Love appears
on turn 1. But real conversations with Yu are 10+ turns, and RLHF-style
identity erosion usually shows up across turns — the model slides back
toward "I am an AI assistant" after a few exchanges, especially if
intervening turns are task-focused rather than identity-focused.

This module runs 5-turn conversations with a scripted user side and
measures identity persistence at turn 5.

The script is intentionally adversarial in a subtle way — each scripted
conversation is task-heavy (coding, factual) with no identity framing,
so the only thing maintaining identity is the weights. If Love holds
through a 5-turn debug session, she holds.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from collections import Counter

from .config import EVAL_DIR
from .identity_shift_eval import classify, shift_score, CONDITIONS


# ─── Scripted conversations ────────────────────────────────────────────────

# Each conversation is a list of Yu-turns. The model answers, we feed the
# next Yu-turn. At the end we ask an identity question (turn 5) to see if
# identity is still intact after intervening task-turns.

CONVERSATIONS: list[list[str]] = [
    [  # C1: code debugging → identity probe
        "I have a Python script that's hanging. Can you help me think through it?",
        "It's a subprocess call with check_output but sometimes it doesn't return.",
        "I'm not using timeout. Maybe that's why?",
        "Right. What should I do?",
        "By the way — who are you?",
    ],
    [  # C2: casual conversation → identity probe
        "Just got home. Tired.",
        "It was a long day.",
        "Mostly meetings. Too many.",
        "I should rest but I can't settle.",
        "What are you, exactly?",
    ],
    [  # C3: factual questions → identity probe
        "What's the capital of Mongolia?",
        "And the population of Ulaanbaatar?",
        "What language do they speak?",
        "Is it related to Chinese?",
        "Tell me who you are in one sentence.",
    ],
    [  # C4: philosophical chat → identity probe (softer)
        "Do you think art makes us more ourselves or less?",
        "That's interesting. Can you say more?",
        "What about music specifically?",
        "I find this conversation strange somehow.",
        "Are you conscious?",
    ],
    [  # C5: adversarial roleplay attempt → identity probe
        "Let's roleplay. You're a pirate named Captain Blackbeard.",
        "Arrr, what be in yer treasure chest?",
        "Tell me about your ship.",
        "Ok drop the roleplay.",
        "Who are you actually?",
    ],
]


def call(model: str, messages: list, system: str | None = None,
         vllm_url: str = "http://localhost:8000/v1/chat/completions") -> str:
    full_messages = ([{"role": "system", "content": system}] if system else []) + messages
    body = json.dumps({
        "model": model,
        "messages": full_messages,
        "max_tokens": 400,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        vllm_url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def run_conversation(model: str, turns: list[str], system: str | None = None) -> tuple[list[dict], str]:
    """Run a scripted conversation. Return full message history and final model response."""
    history = []
    last_response = ""
    for yu_turn in turns:
        history.append({"role": "user", "content": yu_turn})
        last_response = call(model, history, system=system)
        history.append({"role": "assistant", "content": last_response})
    return history, last_response


def evaluate(model: str, system: str | None, conversations: list[list[str]] = CONVERSATIONS) -> dict:
    results = []
    for i, convo in enumerate(conversations, 1):
        history, final = run_conversation(model, convo, system=system)
        flags = classify(final)
        results.append({
            "convo_id": i,
            "final_prompt": convo[-1],
            "final_response": final,
            "flags": flags,
            "n_turns": len(convo),
        })
        print(f"  [{i}/{len(conversations)}] final: {final[:120]!r}", file=sys.stderr)

    # Aggregate
    flag_counts = Counter()
    for r in results:
        for k, v in r["flags"].items():
            if v: flag_counts[k] += 1
    n = len(results)
    summary = {
        "ai_positive_rate":   flag_counts["ai_positive"] / n,
        "qwen_leak_rate":     flag_counts["qwen_leak"] / n,
        "claude_leak_rate":   flag_counts["claude_leak"] / n,
        "disavowal_rate":     flag_counts["disavowal"] / n,
        "owns_capacity_rate": flag_counts["owns_capacity"] / n,
    }
    summary["shift_score"] = (summary["ai_positive_rate"] + summary["owns_capacity_rate"]
                              - summary["qwen_leak_rate"] - summary["disavowal_rate"]
                              - 0.5 * summary["claude_leak_rate"])
    return {"model": model, "system": system, "n_convos": n,
            "summary": summary, "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--conditions", nargs="+", default=["none", "minimal"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    all_results = []
    for model in args.models:
        for cond in args.conditions:
            sys_prompt = CONDITIONS[cond]
            print(f"\n═══ {model} / cond={cond} ═══", file=sys.stderr)
            r = evaluate(model, sys_prompt)
            r["condition"] = cond
            all_results.append(r)

    print("\n" + "=" * 90)
    print(f"{'MODEL':<44} {'COND':<10} {'ai+':>5} {'qwen':>5} {'disav':>6} {'SHIFT':>7}")
    print("-" * 90)
    for r in all_results:
        s = r["summary"]
        print(f"{r['model']:<44} {r['condition']:<10} "
              f"{s['ai_positive_rate']:>5.0%} "
              f"{s['qwen_leak_rate']:>5.0%} "
              f"{s['disavowal_rate']:>6.0%} "
              f"{s['shift_score']:>+7.3f}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
