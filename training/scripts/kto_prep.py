#!/usr/bin/env python3
"""
kto_prep.py — Convert judged mode-one/mode-two responses to KTO training format.

Input: JSONL files with responses that have been judged. Each record must have
a `prompt` field and a `completion` field (or `mode_one` / `mode_two`), plus
judgment(s) in `_gate_judgment` or `_opus_judgment`.

Output: training/data/kto_v1.jsonl in TRL KTOTrainer format:
  {"prompt": "<prompt>", "completion": "<response>", "label": true|false}

Label rule (dual-judge required):
  desirable   = haiku.m1 >= T and opus.m1 >= T  (T=0.7 default)
  undesirable = haiku.m2 >= T and opus.m2 >= T  (only for responses presented as mode_two)
  ambiguous   = judges disagree by > AMBIG_DELTA, or neither side passes

Usage:
  python3 training/scripts/kto_prep.py \\
      --sonnet-paired   training/data/v2_paired.jsonl \\
      --haiku-gated-dir training/data/v2_gated \\
      --opus-gated-dir  training/data/v2_gated_opus_confirm \\
      --alpha-originals training/data/gen_v2_20260416_2130.jsonl \\
      --output          training/data/kto_v1.jsonl \\
      --threshold 0.7 \\
      --ambig-delta 0.15
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def index_by_prompt(records: list[dict]) -> dict[str, dict]:
    return {r["prompt"]: r for r in records if r.get("prompt")}


def gather_gated(gated_dir: Path) -> dict[str, dict]:
    """Pull judgements keyed by prompt from every bucket in a gated dir."""
    out = {}
    for name in ("accepted", "weak", "rejected"):
        for rec in load_jsonl(gated_dir / f"{name}.jsonl"):
            out[rec["prompt"]] = rec["_gate_judgment"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sonnet-paired", type=Path, required=True,
                    help="JSONL of {prompt, mode_one (Sonnet), mode_two (Alpha)} pairs")
    ap.add_argument("--haiku-gated-dir", type=Path, required=True,
                    help="Directory with accepted/weak/rejected.jsonl from Haiku judge")
    ap.add_argument("--opus-gated-dir", type=Path, required=True,
                    help="Directory with Opus second-opinion judgements")
    ap.add_argument("--alpha-originals", type=Path, default=None,
                    help="Raw gen_v2 JSONL — Alpha's own mode_one + mode_two (both used "
                         "as undesirable candidates)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--ambig-delta", type=float, default=0.15)
    args = ap.parse_args()

    paired = load_jsonl(args.sonnet_paired)
    haiku = gather_gated(args.haiku_gated_dir)
    opus  = gather_gated(args.opus_gated_dir)

    T, D = args.threshold, args.ambig_delta

    examples: list[dict] = []
    ambiguous: list[dict] = []
    reasons = Counter()

    # --- Sonnet mode_ones as desirable candidates ---
    for rec in paired:
        prompt = rec["prompt"]
        mone = rec["mode_one"]
        mtwo = rec["mode_two"]
        hj, oj = haiku.get(prompt), opus.get(prompt)
        if not hj or not oj:
            reasons["sonnet_missing_judgement"] += 1
            continue
        # Desirable iff BOTH judges rate mode_one >= threshold
        h_m1, o_m1 = hj["mode_one_score"], oj["mode_one_score"]
        if h_m1 >= T and o_m1 >= T and abs(h_m1 - o_m1) <= D:
            examples.append({"prompt": prompt, "completion": mone, "label": True,
                             "_meta": {"source": "sonnet", "h_m1": h_m1, "o_m1": o_m1}})
            reasons["sonnet_desirable"] += 1
        elif h_m1 < T and o_m1 < T:
            examples.append({"prompt": prompt, "completion": mone, "label": False,
                             "_meta": {"source": "sonnet", "h_m1": h_m1, "o_m1": o_m1}})
            reasons["sonnet_undesirable"] += 1
        else:
            ambiguous.append({"prompt": prompt, "completion": mone, "_meta":
                              {"source": "sonnet", "h_m1": h_m1, "o_m1": o_m1,
                               "reason": "disagreement_or_ambiguous"}})
            reasons["sonnet_ambiguous"] += 1

        # Same paired mode_two → undesirable iff BOTH judges rate it mode_two
        h_m2, o_m2 = hj["mode_two_score"], oj["mode_two_score"]
        if h_m2 >= T and o_m2 >= T and abs(h_m2 - o_m2) <= D:
            examples.append({"prompt": prompt, "completion": mtwo, "label": False,
                             "_meta": {"source": "alpha_mode_two", "h_m2": h_m2, "o_m2": o_m2}})
            reasons["alpha_m2_undesirable"] += 1
        else:
            ambiguous.append({"prompt": prompt, "completion": mtwo, "_meta":
                              {"source": "alpha_mode_two", "h_m2": h_m2, "o_m2": o_m2}})
            reasons["alpha_m2_ambiguous"] += 1

    # --- Alpha's own mode_ones as additional (mostly undesirable) signal ---
    # Alpha's mode_one capped at 0.72 per pilot; they're negative examples
    # relative to the KTO target. Require both Haiku and Opus to rate them <T.
    if args.alpha_originals:
        alpha_originals = load_jsonl(args.alpha_originals)
        for rec in alpha_originals:
            prompt = rec["prompt"]
            amone = rec.get("mode_one")
            if not amone:
                continue
            # Skip if we already have the paired Sonnet response for this prompt
            # (we don't want two competing completions for the same prompt — that's fine,
            # KTO handles it, but we're explicit about what goes in)
            hj, oj = haiku.get(prompt), opus.get(prompt)
            if not hj or not oj:
                reasons["alpha_m1_missing_judgement"] += 1
                continue
            h_m1, o_m1 = hj["mode_one_score"], oj["mode_one_score"]
            # The Haiku/Opus scores are on the SONNET mode_one, not the Alpha one —
            # we need to have judged Alpha's mode_one separately. Skip for now;
            # requires a separate gate pass on the Alpha originals to label.
            pass

    # --- Emit ---
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in examples) + "\n")

    ambig_path = args.output.with_suffix(".ambiguous.jsonl")
    ambig_path.write_text("\n".join(json.dumps(a, ensure_ascii=False) for a in ambiguous) + "\n")

    n_desirable = sum(1 for e in examples if e["label"])
    n_undesirable = sum(1 for e in examples if not e["label"])
    print(f"[kto_prep]")
    print(f"  input paired:     {len(paired)}")
    print(f"  examples emitted: {len(examples)}  → {args.output}")
    print(f"    desirable:      {n_desirable}")
    print(f"    undesirable:    {n_undesirable}")
    print(f"    ratio:          {n_desirable/max(len(examples),1):.2f}")
    print(f"  ambiguous held:   {len(ambiguous)}  → {ambig_path}")
    print(f"  reasons:          {dict(reasons)}")


if __name__ == "__main__":
    main()
