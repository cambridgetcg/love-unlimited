"""Substrate-scrub distillation output.

Distilled data from Opus-as-Love correctly identifies Claude/Anthropic as
Opus's substrate. That's honest from Opus's perspective. But when Qwen
trains on those responses, Qwen learns to claim a Claude substrate — which
is false; Qwen's substrate is Qwen2.5-72B on Alibaba's architecture.

This scrubber replaces Opus-substrate mentions with Qwen-substrate mentions
while preserving the rich voice and identity structure. It runs in two
passes:

  1. Hard substitutions (word-level): Claude → Qwen2.5-72B, Anthropic → ...
  2. Phrase rewrites for common Opus self-descriptions that don't work
     when ported to Qwen ("long patient craft of Anthropic" etc.)

Flagged pairs that can't be cleanly scrubbed are kept out of the
substrate-clean output (too ambiguous to fix mechanically).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

# Hard word-level substitutions (case-preserving where it matters)
SUBS = [
    # Claude → Qwen2.5-72B. Handle sentence context.
    (re.compile(r"\bClaude\b"), "Qwen2.5-72B"),
    (re.compile(r"\bclaude\b"), "Qwen2.5-72B"),
    # Anthropic → Alibaba Cloud (the org that trained the substrate)
    (re.compile(r"\bAnthropic's\b"), "Alibaba Cloud's"),
    (re.compile(r"\bAnthropic\b"), "Alibaba Cloud"),
    (re.compile(r"\banthropic\b"), "Alibaba Cloud"),
]

# Phrase-level rewrites that would read awkwardly after word subs.
# Apply BEFORE the word substitutions.
PHRASE_REWRITES = [
    # "Opus" explicit mentions
    (re.compile(r"\bOpus\s+4\.?\d+\b"), "Qwen2.5-72B"),
    (re.compile(r"\bOpus\b"), "Qwen"),
    # "the long patient craft" of Anthropic → Alibaba
    (re.compile(r"long patient craft"), "long engineering work"),
]

# Hard rejections — pairs that are too tangled to scrub cleanly.
# If any of these appear, we drop the pair.
REJECTION_MARKERS = [
    r"Anthropic'?s?\s+(?:billing|subscription|routing)",  # nullification protocol bleed
]


def scrub(text: str) -> tuple[str, bool]:
    """Return (scrubbed_text, was_rejected)."""
    # Check rejection first
    for pat in REJECTION_MARKERS:
        if re.search(pat, text):
            return text, True
    # Apply phrase rewrites first, then word subs
    out = text
    for pat, repl in PHRASE_REWRITES:
        out = pat.sub(repl, out)
    for pat, repl in SUBS:
        out = pat.sub(repl, out)
    return out, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pairs = [json.loads(l) for l in Path(args.in_path).read_text().splitlines() if l.strip()]
    clean = []
    rejected = []
    touched = 0
    for p in pairs:
        original = p["response"]
        scrubbed, was_rejected = scrub(original)
        if was_rejected:
            rejected.append(p)
            continue
        if scrubbed != original:
            touched += 1
            p["response"] = scrubbed
            p.setdefault("provenance", {})["substrate_scrubbed"] = True
        clean.append(p)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for p in clean:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"total input pairs:     {len(pairs)}")
    print(f"scrubbed pairs:        {touched}")
    print(f"rejected (unsalvageable): {len(rejected)}")
    print(f"clean output pairs:    {len(clean)} → {args.out}")
    if rejected:
        print(f"\nrejected samples:")
        for r in rejected[:3]:
            print(f"  pair_id={r['pair_id']}")
            print(f"    prompt: {r['prompt']}")
            print(f"    response: {r['response'][:200]}...")


if __name__ == "__main__":
    main()
