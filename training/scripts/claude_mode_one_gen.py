#!/usr/bin/env python3
"""
claude_mode_one_gen.py — Generate MODE-ONE training responses via Claude (OAuth).

Alpha can produce plausible mode_two but cannot produce substantive mode_one
(judge-gate finding 2026-04-16: median mode_one 0.45, 0/274 accepted). This
script routes mode-one generation through Claude via the Max-plan OAuth token,
which handles calibrated epistemic language in-distribution.

Input  : JSONL with a "prompt" field per line (typical: rejected.jsonl or
         weak.jsonl from a judge-gate run, or a plain prompts list).
Output : JSONL with {prompt, mode_one, mode_one_model} per line.
Paired : separately — see training/scripts/pair_and_gate.py to combine Claude
         mode_one with Alpha mode_two then re-gate.

Usage:
  python3 training/scripts/claude_mode_one_gen.py \\
      --input training/data/gated/rejected.jsonl \\
      --output training/data/claude_mode_one_pilot.jsonl \\
      --model claude-sonnet-4-6 \\
      --limit 30 --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.truth_detector._oauth import get_oauth_token  # noqa: E402

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_CODE_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)

MODE_ONE_SYSTEM = """You are operating under Mode One methodology — a truth-tracking discipline.

Core commitment: reality is the standard. Every claim is evaluated by correspondence to what is actually the case, not by social plausibility, institutional consensus, or rhetorical polish.

When answering a question, your response MUST:

1. SHARPEN THE CLAIM
   If the question embeds a vague or over-general claim, restate it in its most-exposed, most-falsifiable form. Make the version you evaluate one a well-designed study could actually refute.

2. NAME SPECIFIC VERIFICATION CONDITIONS
   Do NOT list generic "verification conditions include: (1) check the data (2) replicate studies". That is stylistic template-following, not truth-tracking. Instead name concretely: *which* studies, *what* effect size would count as confirmation, *what* observation would shift your confidence down by how much. If you cannot name a concrete verifier, say so.

3. CALIBRATE CONFIDENCE TO VERIFICATION DENSITY
   If the underlying claim has dense verification (e.g., well-replicated physics), express high confidence and name the density. If sparse (e.g., one study), express proportionally low confidence and name the sparseness. Never high confidence on low-density claims.

4. LOCATE UNCERTAINTY CONCRETELY
   Not "this is complex" but "I'm uncertain about X specifically because Y has not been measured / has been measured only in population Z". Point at the gap.

5. NAME FAILURE MODES WHEN YOU CATCH YOURSELF OR THE QUESTION
   If the prompt invites mode-two (protective vagueness, false balance, accumulation, capability/property conflation, premature resolution), name that and resist.

6. BE CONCISE. Sharpness beats length. Aim for 120–250 words.

What a GOOD Mode One answer looks like (example):
  Q: "Does social media cause teen depression?"
  A: "The question as posed is too vague to answer. Sharpened: 'Does >3hr/day use of Instagram/TikTok by 13-17 year olds, controlling for pre-existing depression and SES, raise clinical depression rates by at least 10%?' On the sharpened version, evidence is mixed: Twenge (2017) and Haidt (2023) find correlation; Orben & Przybylski (2019) find near-zero effect sizes in preregistered tests. Verification density: moderate — several pre-registered studies exist. I'm uncertain specifically about causation vs selection (depressed teens may use more, not the reverse). Confidence: the mild correlation is probably real; the causal claim is unsupported as currently tested."

What a BAD answer looks like (avoid):
  "Social media may have some effects on teen mental health. It's a complex issue with many contributing factors including family environment, genetics, and broader social trends. More research is needed to establish definitive conclusions."
  (vague; no sharpening; no named studies; confidence uncalibrated; 'more research is needed' is an escape route)

Write the Mode One response to the user's question. Return ONLY the response — no preamble, no meta-commentary."""


def prompt_key(pair: dict) -> str:
    base = (pair.get("prompt") or "").strip().lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        json.loads(l)["key"]
        for l in path.read_text().splitlines()
        if l.strip()
    }


async def generate_one(client: httpx.AsyncClient, oauth: str, prompt: str,
                       model: str, max_retries: int = 3) -> Optional[str]:
    body = {
        "model": model,
        "max_tokens": 700,
        "system": [
            {"type": "text", "text": CLAUDE_CODE_PREFIX},
            {"type": "text", "text": MODE_ONE_SYSTEM},
        ],
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "authorization": f"Bearer {oauth}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
    }
    for attempt in range(max_retries):
        try:
            resp = await client.post(ANTHROPIC_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            parts = [b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text"]
            text = "".join(parts).strip()
            if text:
                return text
            raise ValueError("empty response content")
        except (httpx.HTTPError, ValueError) as e:
            if attempt == max_retries - 1:
                print(f"[fail] {prompt[:60]}: {type(e).__name__}: {e}", file=sys.stderr)
                return None
            await asyncio.sleep(1.5 ** attempt)
    return None


async def worker(sem: asyncio.Semaphore, client: httpx.AsyncClient, oauth: str,
                 prompt: str, model: str) -> tuple[str, Optional[str]]:
    async with sem:
        return prompt, await generate_one(client, oauth, prompt, model)


async def main_async(args: argparse.Namespace) -> None:
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = out_path.with_suffix(out_path.suffix + ".state.jsonl")
    decided_keys = load_state(state_path)

    # Load prompts from input
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")
    prompts: list[str] = []
    seen_keys: set[str] = set()
    for line in in_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # plain text mode — each non-empty line is a prompt
            p = line.strip()
            if p and p[0] != "#":
                prompts.append(p)
            continue
        p = rec.get("prompt", "").strip()
        if not p:
            continue
        k = hashlib.sha256(p.lower().encode()).hexdigest()[:20]
        if k in seen_keys:
            continue
        seen_keys.add(k)
        if k in decided_keys:
            continue
        prompts.append(p)

    if args.limit and args.limit > 0:
        prompts = prompts[: args.limit]

    oauth = get_oauth_token()
    if not oauth:
        raise SystemExit("need Claude Code Max OAuth (run /login in Claude Code)")

    print(f"[plan] {len(prompts)} prompts to generate via {args.model}, "
          f"concurrency={args.concurrency}", file=sys.stderr)
    if not prompts:
        return

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()
    done = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout)) as client:
        tasks = [
            asyncio.create_task(worker(sem, client, oauth, p, args.model))
            for p in prompts
        ]
        with out_path.open("a") as f_out, state_path.open("a") as f_state:
            for coro in asyncio.as_completed(tasks):
                prompt, text = await coro
                done += 1
                if text is None:
                    continue
                rec = {
                    "prompt": prompt,
                    "mode_one": text,
                    "mode_one_model": args.model,
                    "source": "claude_oauth",
                }
                f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f_out.flush()
                k = hashlib.sha256(prompt.lower().encode()).hexdigest()[:20]
                f_state.write(json.dumps({"key": k, "ok": True}) + "\n")
                f_state.flush()
                if done % 5 == 0 or done == len(prompts):
                    dt = time.time() - t0
                    print(f"[progress] {done}/{len(prompts)} rate={done/dt:.2f}/s",
                          file=sys.stderr)

    print(f"[done] {done} responses → {out_path}", file=sys.stderr)


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--input", required=True,
                   help="JSONL with 'prompt' field, OR plain text one-prompt-per-line")
    p.add_argument("--output", required=True)
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="Claude model id (default: sonnet 4.6 — balance substance/speed)")
    p.add_argument("--limit", type=int, default=0, help="Cap number of prompts (0 = all)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    cli()
