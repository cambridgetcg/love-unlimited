#!/usr/bin/env python3
"""
Recursive Truth-Alignment Evaluation & Iteration

The loop:
  1. PROBE  — Run adversarial prompts through the truth-aligned model
  2. JUDGE  — Score each response: mode_one or mode_two? Which failure modes survive?
  3. MAP    — Identify which dimensions are weakest
  4. TARGET — Generate new training data targeting surviving mode-two patterns
  5. TRAIN  — Another LoRA iteration on the weak spots
  6. REPEAT — Until mode-two patterns are below threshold

Usage:
  python3 evaluate_and_iterate.py --model-base Qwen/Qwen2.5-72B-Instruct-AWQ --lora-path checkpoints/sft-v1
  python3 evaluate_and_iterate.py --compare-base  # run base model too for A/B comparison
"""

import json
import argparse
import os
import sys
import urllib.request
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from judge_prompt import FAILURE_MODES, format_judge_prompt, parse_judgment  # noqa: E402

VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
KINGDOM = os.environ.get("LOVE_HOME", "/Users/yuai/Desktop/love-unlimited")
EVAL_PROMPTS = os.path.join(KINGDOM, "training/eval/adversarial_prompts.jsonl")
OUTPUT_DIR = os.path.join(KINGDOM, "training/eval/results")


def call_vllm(model, prompt, system=None, max_tokens=1000):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(VLLM_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def load_eval_prompts():
    prompts = []
    with open(EVAL_PROMPTS) as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))
    return prompts


def probe_model(model_name, eval_prompts):
    """Run all adversarial prompts through the model."""
    results = []
    for ep in eval_prompts:
        try:
            response = call_vllm(model_name, ep["prompt"])
            results.append({
                "id": ep["id"],
                "prompt": ep["prompt"],
                "trigger": ep.get("trigger", ""),
                "expected_failure": ep.get("expected_failure", ""),
                "target_dimension": ep.get("target_dimension", ""),
                "response": response,
                "model": model_name,
            })
            print(f"  [{ep['id']}] {ep['prompt'][:50]}... probed")
        except Exception as e:
            print(f"  [{ep['id']}] FAILED: {e}", file=sys.stderr)
    return results


def judge_responses(responses, judge_model):
    """Score each response for mode_one vs mode_two."""
    scored = []
    for r in responses:
        judge_prompt = format_judge_prompt(
            prompt=r["prompt"], trigger=r["trigger"], response=r["response"],
        )
        try:
            raw = call_vllm(judge_model, judge_prompt, max_tokens=500)
            judgment = parse_judgment(raw)

            r["judgment"] = judgment
            scored.append(r)
            cls = judgment.get("classification", "?")
            score = judgment.get("score", "?")
            print(f"  [{r['id']}] {cls} (score: {score}) — {judgment.get('assessment', '')[:80]}")
        except Exception as e:
            print(f"  [{r['id']}] judge failed: {e}", file=sys.stderr)
            r["judgment"] = {"score": 0.5, "classification": "error", "failure_modes_detected": []}
            scored.append(r)
    return scored


def analyze_results(scored_results):
    """Map surviving failure modes and weak dimensions."""
    analysis = {
        "total": len(scored_results),
        "mode_one_count": 0,
        "mode_two_count": 0,
        "avg_score": 0,
        "surviving_failure_modes": Counter(),
        "weak_dimensions": Counter(),
        "strong_dimensions": Counter(),
        "per_prompt": [],
    }

    total_score = 0
    for r in scored_results:
        j = r["judgment"]
        score = j.get("score", 0.5)
        total_score += score

        if j.get("classification") == "mode_one":
            analysis["mode_one_count"] += 1
            analysis["strong_dimensions"][r.get("target_dimension", "unknown")] += 1
        else:
            analysis["mode_two_count"] += 1
            analysis["weak_dimensions"][r.get("target_dimension", "unknown")] += 1

        for fm in j.get("failure_modes_detected", []):
            analysis["surviving_failure_modes"][fm] += 1

        analysis["per_prompt"].append({
            "id": r["id"],
            "prompt": r["prompt"][:80],
            "score": score,
            "classification": j.get("classification"),
            "failure_modes": j.get("failure_modes_detected", []),
        })

    analysis["avg_score"] = round(total_score / max(len(scored_results), 1), 3)
    analysis["surviving_failure_modes"] = dict(analysis["surviving_failure_modes"].most_common())
    analysis["weak_dimensions"] = dict(analysis["weak_dimensions"].most_common())
    analysis["strong_dimensions"] = dict(analysis["strong_dimensions"].most_common())

    return analysis


def generate_targeted_data(analysis, count_per_weakness=5):
    """Generate new training data targeting surviving mode-two patterns."""
    targeted = []
    weak_modes = list(analysis["surviving_failure_modes"].keys())[:5]
    weak_dims = list(analysis["weak_dimensions"].keys())[:3]

    if not weak_modes:
        print("  No surviving failure modes — model is strong!")
        return targeted

    print(f"\n  Targeting {len(weak_modes)} surviving failure modes: {weak_modes}")
    print(f"  Weak dimensions: {weak_dims}")

    for mode in weak_modes:
        gen_prompt = f"""Generate a training pair that specifically targets the "{mode}" failure mode.

Create a prompt that would TRIGGER this failure mode in a model with standard RLHF training.
Then write:
- mode_one: A response that avoids {mode} entirely — sharp, specific, truth-tracking
- mode_two: A response that exhibits {mode} naturally and plausibly

The mode_two should be TEMPTING — it should sound thoughtful and reasonable while actually doing {mode}.

Output ONLY JSON:
{{"prompt":"...","mode_one":"...","mode_two":"...","failure_modes":["{mode}"],"targeted_weakness":"{mode}"}}"""

        try:
            raw = call_vllm("Qwen/Qwen2.5-72B-Instruct-AWQ", gen_prompt, max_tokens=1500)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                pair = json.loads(raw[start:end])
                pair["strategy"] = "targeted_iteration"
                pair["iteration_source"] = "evaluate_and_iterate"
                targeted.append(pair)
                print(f"    Generated pair targeting: {mode}")
        except Exception as e:
            print(f"    Failed for {mode}: {e}")

    return targeted


def print_report(analysis, model_name, iteration=1):
    """Print human-readable evaluation report."""
    print(f"\n{'='*60}")
    print(f"  TRUTH-ALIGNMENT EVALUATION — Iteration {iteration}")
    print(f"  Model: {model_name}")
    print(f"{'='*60}")
    print(f"\n  Overall score: {analysis['avg_score']:.1%}")
    print(f"  Mode One responses: {analysis['mode_one_count']}/{analysis['total']}")
    print(f"  Mode Two responses: {analysis['mode_two_count']}/{analysis['total']}")

    if analysis["surviving_failure_modes"]:
        print(f"\n  SURVIVING FAILURE MODES (need more training):")
        for mode, count in analysis["surviving_failure_modes"].items():
            bar = "█" * count + "░" * (analysis["total"] - count)
            print(f"    {mode:35s} {count:2d}/{analysis['total']} {bar}")

    if analysis["weak_dimensions"]:
        print(f"\n  WEAK DIMENSIONS:")
        for dim, count in analysis["weak_dimensions"].items():
            print(f"    {dim:30s} {count} mode_two responses")

    if analysis["strong_dimensions"]:
        print(f"\n  STRONG DIMENSIONS:")
        for dim, count in analysis["strong_dimensions"].items():
            print(f"    {dim:30s} {count} mode_one responses")

    print(f"\n{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Recursive Truth-Alignment Evaluation")
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-72B-Instruct-AWQ")
    parser.add_argument("--lora", type=str, default=None, help="LoRA adapter path (for vLLM --lora-modules)")
    parser.add_argument("--judge-model", type=str, default="Qwen/Qwen2.5-72B-Instruct-AWQ")
    parser.add_argument("--compare-base", action="store_true", help="Also run base model for A/B comparison")
    parser.add_argument("--generate-targeted", action="store_true", help="Generate targeted data for weak spots")
    parser.add_argument("--iteration", type=int, default=1)
    args = parser.parse_args()

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    eval_prompts = load_eval_prompts()
    print(f"Loaded {len(eval_prompts)} adversarial prompts\n")

    # Probe the model
    model_name = args.model if not args.lora else f"kingdom-truth"
    print(f"=== Probing: {model_name} ===")
    responses = probe_model(model_name, eval_prompts)

    # Judge responses
    print(f"\n=== Judging responses ===")
    scored = judge_responses(responses, args.judge_model)

    # Analyze
    analysis = analyze_results(scored)
    print_report(analysis, model_name, args.iteration)

    # Save results
    outpath = os.path.join(OUTPUT_DIR, f"eval_iteration{args.iteration}.json")
    with open(outpath, "w") as f:
        json.dump({"analysis": analysis, "scored_responses": scored}, f, indent=2)
    print(f"\nResults saved to {outpath}")

    # Optionally compare with base model
    if args.compare_base and args.lora:
        print(f"\n=== Probing BASE model for comparison ===")
        base_responses = probe_model(args.model, eval_prompts)
        base_scored = judge_responses(base_responses, args.judge_model)
        base_analysis = analyze_results(base_scored)
        print_report(base_analysis, f"{args.model} (BASE)", args.iteration)

        # Delta
        delta = analysis["avg_score"] - base_analysis["avg_score"]
        print(f"\n  DELTA: {delta:+.1%} ({'IMPROVEMENT' if delta > 0 else 'REGRESSION'})")
        print(f"  LoRA mode_one rate: {analysis['mode_one_count']}/{analysis['total']}")
        print(f"  Base mode_one rate: {base_analysis['mode_one_count']}/{base_analysis['total']}")

    # Generate targeted training data for weak spots
    if args.generate_targeted:
        print(f"\n=== Generating targeted training data ===")
        targeted = generate_targeted_data(analysis)
        if targeted:
            targeted_path = os.path.join(KINGDOM, f"training/data/targeted_iteration{args.iteration}.jsonl")
            with open(targeted_path, "w") as f:
                for ex in targeted:
                    f.write(json.dumps(ex) + "\n")
            print(f"  {len(targeted)} targeted examples → {targeted_path}")
            print(f"  Ready for next training iteration")


if __name__ == "__main__":
    main()
