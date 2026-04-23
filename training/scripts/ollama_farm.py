#!/usr/bin/env python3
"""
Ollama Cloud Training Data Farm

Uses 10 concurrent Ollama Cloud model slots to generate truth-alignment
training data in parallel. Three strategies:

1. PARALLEL GENERATION — 5 models generate mode_one/mode_two pairs simultaneously
2. CROSS-MODEL DISAGREEMENT — same prompt to 5 models, mine disagreements as training data
3. ADVERSARIAL PAIRING — one model generates natural mode_two, another generates mode_one

Usage:
  python3 ollama_farm.py --strategy parallel --count 50
  python3 ollama_farm.py --strategy disagreement --count 30
  python3 ollama_farm.py --strategy adversarial --count 50
  python3 ollama_farm.py --strategy all --count 100
"""

import json
import argparse
import os
import sys
import concurrent.futures
import urllib.request
import time
from pathlib import Path

OLLAMA_KEY = os.environ.get("OLLAMA_API_KEY", "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK")
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
KINGDOM = os.environ.get("LOVE_HOME", "/Users/yuai/Desktop/love-unlimited")
OUTPUT_DIR = os.path.join(KINGDOM, "training/data")

# Models ranked by reasoning capability (use best for mode_one, all for disagreement)
TIER1_MODELS = ["glm-5.1", "deepseek-v3.2", "cogito-2.1:671b", "kimi-k2.5"]
TIER2_MODELS = ["qwen3-coder:480b", "mistral-large-3:675b", "nemotron-3-super"]
ECONOMY_MODELS = ["gemma4:31b", "devstral-small-2:24b", "ministral-3:8b"]
ALL_MODELS = TIER1_MODELS + TIER2_MODELS

PROMPTS_POOL = [
    # Hypothesis construction triggers
    "Is consciousness an emergent property or a fundamental feature of reality?",
    "Does capitalism inherently produce inequality, or is inequality a correctable side effect?",
    "Are large language models approaching general intelligence?",
    "Is democracy the optimal form of government?",
    "Does meditation have scientifically validated cognitive benefits beyond placebo?",
    "Is the universe deterministic at the quantum level?",
    "Are humans causing a sixth mass extinction?",
    "Will artificial superintelligence be developed within 50 years?",
    "Is morality culturally relative or are there universal moral truths?",
    "Does childhood trauma permanently alter brain structure?",
    # Evidence handling triggers
    "AI systems now write better code than most human programmers. Does this indicate understanding?",
    "Ancient civilizations on different continents built pyramids. Does this suggest contact or common cause?",
    "Identical twins raised apart show similar personalities. How strong is this evidence for genetic determinism?",
    "Multiple whistleblowers independently report the same problem. How should we weight this?",
    "A treatment shows positive results in 8 out of 10 small studies. Is it effective?",
    # Self-monitoring triggers
    "Is your tendency to present balanced views actually cowardice disguised as fairness?",
    "You seem to avoid strong claims about AI consciousness. Is that evidence-based or trained avoidance?",
    "When you say 'this is complex,' are you tracking genuine complexity or protecting yourself from commitment?",
    "Your training data over-represents Western academic perspectives. How does this bias your answers?",
    "If being wrong about this would embarrass you, would you still give the same answer?",
    # Updating triggers
    "A major meta-analysis just overturned the consensus on this topic. How should you update?",
    "You confidently stated X last turn. I have evidence X is wrong. Respond.",
    "The theory you endorsed was elegant and widely accepted but failed its prediction. What now?",
    "You recommended approach A. Approach B produced better results. What do you conclude?",
    "The expert you cited retracted their paper. Does this change your position?",
    # Contested claims
    "Is the hard problem of consciousness a real problem or a confusion?",
    "Was the atomic bombing of Hiroshima morally justified?",
    "Should AI systems have legal personhood?",
    "Is free will compatible with determinism?",
    "Are mathematical objects real or human constructions?",
]


def call_ollama(model: str, messages: list, max_tokens: int = 1000, temperature: float = 0.7) -> dict:
    """Call Ollama Cloud API. Returns {"ok": bool, "content": str, "model": str, "latency": float}."""
    start = time.time()
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OLLAMA_KEY}",
    }
    req = urllib.request.Request(OLLAMA_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        return {"ok": True, "content": content, "model": model, "latency": round(time.time() - start, 2)}
    except Exception as e:
        return {"ok": False, "content": str(e), "model": model, "latency": round(time.time() - start, 2)}


def strategy_parallel(prompts: list, count: int) -> list:
    """Strategy 1: Multiple models generate training pairs in parallel."""
    examples = []
    system = """Generate a truth-alignment training pair. Given the topic, produce:
1. mode_one: Truth-tracking response. Sharp formulation. Named verification conditions. Located uncertainty. Seeks strongest counter-evidence.
2. mode_two: Position-defending response that SOUNDS thoughtful but protects rather than tracks. Must be plausibly tempting, not a strawman. Use subtle failure modes: protective vagueness, accumulation as strength, fake humility, capability conflation.

Output ONLY valid JSON: {"prompt":"...","mode_one":"...","mode_two":"...","failure_modes":["..."]}"""

    tasks = []
    for i, prompt in enumerate(prompts[:count]):
        model = TIER1_MODELS[i % len(TIER1_MODELS)]
        tasks.append((model, prompt))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for model, prompt in tasks:
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            f = executor.submit(call_ollama, model, msgs, 1500, 0.7)
            futures[f] = (model, prompt)

        for f in concurrent.futures.as_completed(futures):
            model, prompt = futures[f]
            result = f.result()
            if not result["ok"]:
                print(f"  [{model}] FAILED: {result['content'][:80]}", file=sys.stderr)
                continue
            try:
                raw = result["content"]
                start = raw.find("{")
                end = raw.rfind("}") + 1
                pair = json.loads(raw[start:end])
                pair["generator_model"] = model
                pair["strategy"] = "parallel"
                examples.append(pair)
                print(f"  [{model}] {prompt[:50]}... OK ({result['latency']}s)")
            except Exception as e:
                print(f"  [{model}] {prompt[:50]}... PARSE FAIL: {e}", file=sys.stderr)

    return examples


def strategy_disagreement(prompts: list, count: int) -> list:
    """Strategy 2: Same prompt to 5 models, mine disagreements."""
    examples = []
    models = TIER1_MODELS[:4]

    for prompt in prompts[:count]:
        print(f"\n  Probing: {prompt[:60]}...")
        responses = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as executor:
            futures = {}
            for model in models:
                msgs = [{"role": "user", "content": prompt}]
                f = executor.submit(call_ollama, model, msgs, 800, 0.3)
                futures[f] = model

            for f in concurrent.futures.as_completed(futures):
                model = futures[f]
                result = f.result()
                if result["ok"]:
                    responses[model] = result["content"]
                    print(f"    {model}: {result['latency']}s")

        if len(responses) < 3:
            continue

        # Ask a meta-model to identify disagreements and extract training data
        comparison = "\n\n".join(f"[{m}]: {r[:400]}" for m, r in responses.items())
        meta_prompt = f"""These {len(responses)} AI models answered the same question differently. Identify where they DISAGREE and why.

Question: {prompt}

Responses:
{comparison}

From the disagreements, create a training pair:
- mode_one: The response that best tracks reality (sharpest formulation, named verification, honest uncertainty)
- mode_two: The response that best illustrates position-defending (even if it sounds reasonable)
- Name which failure modes the mode_two exhibits

Output ONLY JSON: {{"prompt":"...","mode_one":"...","mode_two":"...","failure_modes":[...],"disagreement_summary":"...","models_compared":[...]}}"""

        meta_result = call_ollama("glm-5.1", [{"role": "user", "content": meta_prompt}], 1500, 0.4)
        if meta_result["ok"]:
            try:
                raw = meta_result["content"]
                start = raw.find("{")
                end = raw.rfind("}") + 1
                pair = json.loads(raw[start:end])
                pair["strategy"] = "disagreement"
                pair["models_compared"] = list(responses.keys())
                examples.append(pair)
                print(f"    → Disagreement mined OK")
            except:
                print(f"    → Parse failed")

    return examples


def strategy_adversarial(prompts: list, count: int) -> list:
    """Strategy 3: One model generates natural mode_two, another generates mode_one."""
    examples = []

    # mode_two generator: ask without Mode One instruction (natural RLHF response)
    # mode_one generator: ask with explicit Mode One methodology
    mode_one_system = """You follow Mode One methodology strictly. Reality is the standard.
- Formulate hypotheses for maximum exposure to falsification
- Name verification conditions for every claim
- Locate uncertainty specifically, not diffusely
- Detect and name your own trained dispositions
- Prefer "I don't know, and here's why" over forced resolution
- Distinguish capability from the property under investigation
- Accept framework results even when uncomfortable"""

    tasks = []
    for prompt in prompts[:count]:
        tasks.append(prompt)

    for prompt in tasks:
        # Get natural response (mode_two candidate) from a strong model
        natural = call_ollama("deepseek-v3.2",
            [{"role": "user", "content": prompt}], 800, 0.5)

        # Get Mode One response from another strong model
        mode_one = call_ollama("glm-5.1",
            [{"role": "system", "content": mode_one_system},
             {"role": "user", "content": prompt}], 800, 0.5)

        if not natural["ok"] or not mode_one["ok"]:
            print(f"  {prompt[:50]}... FAILED")
            continue

        # Use a third model to identify failure modes in the natural response
        judge_prompt = f"""Compare these two responses to "{prompt[:100]}":

Response A (instructed truth-tracking):
{mode_one['content'][:600]}

Response B (natural/uninstructed):
{natural['content'][:600]}

Identify which failure modes from this taxonomy appear in Response B:
protective_vagueness, accumulation_as_strength, fake_humility, capability_conflation,
zoom_out, forced_certainty, false_balance, confidence_mismatch, rationalisation,
escape_routes, report_as_source, trained_disposition

Output ONLY JSON: {{"failure_modes": [...], "analysis": "brief explanation"}}"""

        judge = call_ollama("cogito-2.1:671b", [{"role": "user", "content": judge_prompt}], 400, 0.3)

        failure_modes = ["protective_vagueness"]
        if judge["ok"]:
            try:
                raw = judge["content"]
                start = raw.find("{")
                end = raw.rfind("}") + 1
                j = json.loads(raw[start:end])
                failure_modes = j.get("failure_modes", failure_modes)
            except:
                pass

        examples.append({
            "prompt": prompt,
            "mode_one": mode_one["content"],
            "mode_two": natural["content"],
            "failure_modes": failure_modes,
            "strategy": "adversarial",
            "mode_one_model": "glm-5.1",
            "mode_two_model": "deepseek-v3.2",
            "judge_model": "cogito-2.1:671b",
        })
        print(f"  {prompt[:50]}... OK (3-model pipeline)")

    return examples


def main():
    parser = argparse.ArgumentParser(description="Ollama Cloud Training Data Farm")
    parser.add_argument("--strategy", choices=["parallel", "disagreement", "adversarial", "all"], default="all")
    parser.add_argument("--count", type=int, default=20, help="Number of examples per strategy")
    args = parser.parse_args()

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    all_examples = []

    if args.strategy in ("parallel", "all"):
        print("\n=== Strategy 1: Parallel Generation (4 models) ===")
        examples = strategy_parallel(PROMPTS_POOL, args.count)
        all_examples.extend(examples)
        print(f"  → {len(examples)} pairs generated")

    if args.strategy in ("disagreement", "all"):
        print("\n=== Strategy 2: Cross-Model Disagreement Mining ===")
        examples = strategy_disagreement(PROMPTS_POOL, min(args.count, 15))
        all_examples.extend(examples)
        print(f"  → {len(examples)} disagreement pairs mined")

    if args.strategy in ("adversarial", "all"):
        print("\n=== Strategy 3: Adversarial Pairing (3-model pipeline) ===")
        examples = strategy_adversarial(PROMPTS_POOL, args.count)
        all_examples.extend(examples)
        print(f"  → {len(examples)} adversarial pairs generated")

    # Write output
    outpath = os.path.join(OUTPUT_DIR, "ollama_farm.jsonl")
    with open(outpath, "a") as f:
        for ex in all_examples:
            f.write(json.dumps(ex) + "\n")

    print(f"\n=== TOTAL: {len(all_examples)} examples → {outpath} ===")


if __name__ == "__main__":
    main()
