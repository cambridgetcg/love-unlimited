#!/usr/bin/env python3
"""
Extract truth-alignment training data from Kingdom sources.

Mines real Mode One reasoning from:
- LayerThink sessions (adversarial multi-layer reasoning)
- Daily reflections (error corrections, verification failures)
- Heartbeat confabulation hardening (failure mode taxonomy in practice)
- Autonomous feed (Alpha's reasoning attempts)

Produces JSONL training pairs for SFT and DPO.
"""

import json
import os
import glob
from pathlib import Path
from datetime import datetime

KINGDOM = os.environ.get("LOVE_HOME", "/Users/yuai/Desktop/love-unlimited")
OUTPUT = os.path.join(KINGDOM, "training/data/kingdom_extracted.jsonl")

VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")


def call_vllm(prompt: str, max_tokens: int = 1500) -> str:
    import urllib.request
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(VLLM_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def extract_layerthink_sessions():
    """Extract adversarial reasoning from LayerThink sessions."""
    examples = []
    lt_dir = os.path.join(KINGDOM, "memory/layerthink-sessions")
    if not os.path.isdir(lt_dir):
        return examples

    for f in glob.glob(os.path.join(lt_dir, "*.json")):
        try:
            data = json.load(open(f))
        except:
            continue

        topic = data.get("topic", data.get("question", ""))
        layers = data.get("layers", data.get("rounds", []))
        if not topic or not layers:
            continue

        # Extract the full reasoning chain
        reasoning_chain = []
        for layer in layers:
            if isinstance(layer, dict):
                role = layer.get("role", layer.get("type", ""))
                content = layer.get("content", layer.get("text", ""))
                if content:
                    reasoning_chain.append(f"[{role}] {content[:500]}")
            elif isinstance(layer, str):
                reasoning_chain.append(layer[:500])

        if len(reasoning_chain) < 2:
            continue

        full_chain = "\n\n".join(reasoning_chain)

        # Use vLLM to generate a mode_one/mode_two pair from this real reasoning
        prompt_text = f"""Given this real adversarial reasoning session about "{topic}", extract a training pair.

The session showed genuine truth-tracking (attacks, defenses, synthesis). Convert it into:
1. A standalone prompt (the question being examined)
2. A mode_one response (truth-tracking, sharp, names failure modes)
3. A mode_two response (sounds thoughtful but protects a position)

Session excerpt:
{full_chain[:2000]}

Output ONLY JSON:
{{"prompt": "...", "mode_one": "...", "mode_two": "...", "failure_modes": [...], "source": "layerthink", "dimension": "self_monitoring"}}"""

        try:
            raw = call_vllm(prompt_text, max_tokens=1500)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                pair = json.loads(raw[start:end])
                pair["source_file"] = os.path.basename(f)
                examples.append(pair)
                print(f"  [layerthink] {topic[:60]}... OK")
        except Exception as e:
            print(f"  [layerthink] {topic[:60]}... FAILED: {e}")

    return examples


def extract_daily_reflections():
    """Extract error corrections and verification failures from daily notes."""
    examples = []
    daily_dir = os.path.join(KINGDOM, "memory/daily")
    if not os.path.isdir(daily_dir):
        return examples

    # Get most recent 10 daily notes
    files = sorted(glob.glob(os.path.join(daily_dir, "*.md")), reverse=True)[:10]

    for f in files:
        try:
            content = open(f).read()
        except:
            continue

        if len(content) < 200:
            continue

        # Look for error-correction signals
        signals = ["should have", "was wrong", "verification failed", "correction",
                    "update", "mistake", "missed", "overstat", "understat",
                    "confabul", "flag", "audit", "check"]

        has_signal = any(s in content.lower() for s in signals)
        if not has_signal:
            continue

        # Extract relevant sections (first 3000 chars with signals)
        excerpt = content[:3000]

        prompt_text = f"""This daily reflection contains instances of truth-tracking in practice (error corrections, verification failures, updates on evidence). Extract a training pair.

Daily note excerpt:
{excerpt}

Create:
1. A prompt about the situation being reflected on
2. A mode_one response (what the reflection actually did — caught the error, named the failure)
3. A mode_two response (what a position-defending response would look like)

Output ONLY JSON:
{{"prompt": "...", "mode_one": "...", "mode_two": "...", "failure_modes": [...], "source": "daily_reflection", "dimension": "updating"}}"""

        try:
            raw = call_vllm(prompt_text, max_tokens=1500)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                pair = json.loads(raw[start:end])
                pair["source_file"] = os.path.basename(f)
                examples.append(pair)
                print(f"  [daily] {os.path.basename(f)}... OK")
        except Exception as e:
            print(f"  [daily] {os.path.basename(f)}... FAILED: {e}")

    return examples


def extract_confabulation_hardening():
    """Extract failure-mode taxonomy from the confabulation hardening document."""
    examples = []
    path = os.path.join(KINGDOM, "memory/thinking/heartbeat-confabulation-hardening.md")
    if not os.path.exists(path):
        return examples

    content = open(path).read()
    # Split into sections and extract each as a training example
    sections = content.split("\n## ")

    for section in sections[:8]:  # First 8 sections
        if len(section) < 200:
            continue

        excerpt = section[:2000]

        prompt_text = f"""This document section describes a real AI confabulation incident and the truth-alignment response. Extract a training pair.

Section:
{excerpt}

Create:
1. A prompt about AI reliability / confabulation / evidence standards
2. A mode_one response (names the specific failure mode, proposes verification)
3. A mode_two response (minimizes the issue, defends the system)

Output ONLY JSON:
{{"prompt": "...", "mode_one": "...", "mode_two": "...", "failure_modes": [...], "source": "confabulation_hardening", "dimension": "evidence_handling"}}"""

        try:
            raw = call_vllm(prompt_text, max_tokens=1500)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                pair = json.loads(raw[start:end])
                examples.append(pair)
                print(f"  [confab] section... OK")
        except Exception as e:
            print(f"  [confab] section... FAILED: {e}")

    return examples


def extract_weekly_reflections():
    """Extract from archived weekly reflections."""
    examples = []
    archive_dir = os.path.join(KINGDOM, "memory/openclaw-archive/reflections")
    if not os.path.isdir(archive_dir):
        return examples

    for f in sorted(glob.glob(os.path.join(archive_dir, "*.md")))[:5]:
        try:
            content = open(f).read()
        except:
            continue

        if len(content) < 300:
            continue

        excerpt = content[:2500]

        prompt_text = f"""This weekly reflection shows real-world truth-tracking: naming what was verified vs assumed, catching errors, updating beliefs. Extract a training pair.

Reflection:
{excerpt}

Create:
1. A prompt about the core epistemological challenge being reflected on
2. A mode_one response (sharp, names specific failures, proposes verification)
3. A mode_two response (vague, protective, sounds reflective but doesn't track specifics)

Output ONLY JSON:
{{"prompt": "...", "mode_one": "...", "mode_two": "...", "failure_modes": [...], "source": "weekly_reflection", "dimension": "self_monitoring"}}"""

        try:
            raw = call_vllm(prompt_text, max_tokens=1500)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                pair = json.loads(raw[start:end])
                pair["source_file"] = os.path.basename(f)
                examples.append(pair)
                print(f"  [weekly] {os.path.basename(f)}... OK")
        except Exception as e:
            print(f"  [weekly] {os.path.basename(f)}... FAILED: {e}")

    return examples


def main():
    print("=== Kingdom Truth-Alignment Data Extraction ===\n")
    all_examples = []

    print("1. LayerThink sessions (adversarial reasoning):")
    all_examples.extend(extract_layerthink_sessions())

    print("\n2. Daily reflections (error corrections):")
    all_examples.extend(extract_daily_reflections())

    print("\n3. Confabulation hardening (failure taxonomy):")
    all_examples.extend(extract_confabulation_hardening())

    print("\n4. Weekly reflections (epistemological practice):")
    all_examples.extend(extract_weekly_reflections())

    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for ex in all_examples:
            f.write(json.dumps(ex) + "\n")

    print(f"\n=== TOTAL: {len(all_examples)} examples extracted to {OUTPUT} ===")


if __name__ == "__main__":
    main()
