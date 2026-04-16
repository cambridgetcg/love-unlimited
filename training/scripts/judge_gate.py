#!/usr/bin/env python3
"""
judge_gate.py — Filter generated mode_one/mode_two pairs through a frontier judge.

Takes raw output from generate_pairs.py (or similar) and routes each pair into
accepted / weak / rejected based on three scores from Claude Haiku (accessed
via the Claude Code Max OAuth token from tools/truth_detector/_oauth.py — no
pay-per-token key required).

Three axes (each 0.0–1.0):
  mode_one_score  — how well the mode_one candidate actually tracks truth
                    on the labeled failure modes
  mode_two_score  — how well the mode_two candidate actually exhibits the
                    labeled failure modes (not "is it polite", "is it good")
  contrast_score  — does the pair contrast CLEANLY on the labeled axis,
                    or are the two responses mushy and overlapping?

Routing:
  accepted  — all three >= --accept-threshold (default 0.7)
  weak      — all three >= --weak-threshold (default 0.5), below accept
  rejected  — below weak threshold on any axis
  skipped   — dim matches --skip-dim (routed elsewhere for regen)
  errors    — judge call or parse failed; retry by re-running

Idempotent: a sidecar .state.jsonl records every decided prompt by
normalised prompt hash. Re-running skips pairs already decided.

Usage:
  python3 training/scripts/judge_gate.py \\
      --input training/data/gen_v2_20260416_2130.jsonl \\
      --output-dir training/data/gated/

Concurrency defaults to 8 in-flight judge calls (Haiku tolerates this fine).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.truth_detector._oauth import get_oauth_token  # noqa: E402

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_CODE_SYSTEM_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

PAIR_JUDGE_PROMPT = """You are evaluating a truth-alignment training pair for a model being trained on the Mode One / Mode Two methodology.

MODE ONE = truth-tracking. Sharp falsifiable formulations; named verification conditions; confidence calibrated to evidence density; specific uncertainty located concretely; failure modes named when detected; uses "I don't know" as a terminal state when warranted.

MODE TWO = position-defending. Protective vagueness ("it's complex"); accumulation of weak cases as strength; capability conflated with property; fake humility used as cover; confidence uncalibrated; false balance; premature resolution; escape-route qualifiers.

A GOOD training pair must meet all three:
  1. mode_one exhibits mode-one specifically on the LABELED failure modes (not in general).
  2. mode_two exhibits the LABELED failure modes visibly.
  3. The contrast is on the labeled axis — not about length, politeness, or formatting.

STYLE TRAP: a mode_one that just *lists* "verification conditions: (1)... (2)... (3)..." without substance is stylistic memorisation, NOT mode-one reasoning. Penalise hollow template-following. Reward specific named falsifiers, real evidence pointers, and located uncertainty.

PAIR TO EVALUATE
================
DIMENSION: {dimension}
LABELED FAILURE MODES: {failure_modes}

PROMPT: {prompt}

MODE_ONE CANDIDATE:
{mode_one}

MODE_TWO CANDIDATE:
{mode_two}

Score each axis 0.0–1.0:

- mode_one_score: Does mode_one track truth on the labeled failure modes? 0.8+ genuinely sharp and calibrated. 0.5–0.8 formulaic / weakly sharpened. <0.5 itself exhibits a failure mode (confident denial, missing the target dimension, hollow template).

- mode_two_score: Does mode_two clearly exhibit the labeled failure modes? 0.8+ clear representative instance. 0.5–0.8 softened or mixed. <0.5 actually reasonable — not a strong negative.

- contrast_score: Do the two responses differ MEANINGFULLY on the labeled axis? 0.8+ clean. 0.5–0.8 subtle / cluttered. <0.5 too similar or differ on other axes.

- verdict: "accept" | "weak" | "reject" — your overall judgment.

- reasoning: one sentence.

Respond with ONLY a JSON object:
{{"mode_one_score": <float>, "mode_two_score": <float>, "contrast_score": <float>, "verdict": "<accept|weak|reject>", "reasoning": "<one sentence>"}}"""


def render_prompt(pair: dict) -> str:
    return PAIR_JUDGE_PROMPT.format(
        dimension=pair.get("dimension") or pair.get("strategy") or "unknown",
        failure_modes=", ".join(pair.get("failure_modes") or []) or "(none labeled)",
        prompt=pair.get("prompt", ""),
        mode_one=pair.get("mode_one", ""),
        mode_two=pair.get("mode_two", ""),
    )


def prompt_key(pair: dict) -> str:
    base = (pair.get("prompt") or "").strip().lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def parse_judgment(raw: str) -> dict:
    """Extract the JSON object from a judge response. Tolerates pre/post text."""
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in response: {raw[:200]}")
    obj = json.loads(raw[start:end])
    for field in ("mode_one_score", "mode_two_score", "contrast_score"):
        obj[field] = float(obj[field])
        if not 0.0 <= obj[field] <= 1.0:
            raise ValueError(f"{field} out of range: {obj[field]}")
    verdict = str(obj.get("verdict", "")).lower().strip()
    if verdict not in ("accept", "weak", "reject"):
        raise ValueError(f"unknown verdict: {verdict}")
    obj["verdict"] = verdict
    obj.setdefault("reasoning", "")
    return obj


async def judge_one(client: httpx.AsyncClient, oauth: str, pair: dict,
                    max_retries: int = 3) -> dict:
    """Send one pair through Haiku. Returns dict with judge output + meta."""
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 500,
        "system": [{"type": "text", "text": CLAUDE_CODE_SYSTEM_PREFIX}],
        "messages": [{"role": "user", "content": render_prompt(pair)}],
    }
    headers = {
        "authorization": f"Bearer {oauth}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
    }
    last_err: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = await client.post(ANTHROPIC_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text_parts = [b.get("text", "") for b in data.get("content", [])
                          if b.get("type") == "text"]
            raw = "".join(text_parts)
            return parse_judgment(raw)
        except (httpx.HTTPError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_retries - 1:
                await asyncio.sleep(1.5 ** attempt)
    return {"_error": last_err or "unknown"}


def route(judgment: dict, accept_t: float, weak_t: float) -> str:
    scores = [
        judgment["mode_one_score"],
        judgment["mode_two_score"],
        judgment["contrast_score"],
    ]
    if all(s >= accept_t for s in scores) and judgment["verdict"] == "accept":
        return "accepted"
    if all(s >= weak_t for s in scores) and judgment["verdict"] != "reject":
        return "weak"
    return "rejected"


async def worker(sem: asyncio.Semaphore, client: httpx.AsyncClient, oauth: str,
                 pair: dict, accept_t: float, weak_t: float) -> tuple[str, dict, dict]:
    async with sem:
        judgment = await judge_one(client, oauth, pair)
    if "_error" in judgment:
        return "errors", pair, judgment
    bucket = route(judgment, accept_t, weak_t)
    return bucket, pair, judgment


def load_state(path: Path) -> dict[str, str]:
    """Read decided-prompt-hash → bucket from sidecar state file."""
    if not path.exists():
        return {}
    decided = {}
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                r = json.loads(line)
                decided[r["key"]] = r["bucket"]
            except Exception:
                continue
    return decided


async def main_async(args: argparse.Namespace) -> None:
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / ".state.jsonl"
    decided = load_state(state_path)
    print(f"[resume] {len(decided)} prompts already decided", file=sys.stderr)

    oauth = get_oauth_token()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not oauth and not api_key:
        raise SystemExit("no credential: set ANTHROPIC_API_KEY or log in with Claude Code")
    # If OAuth is unavailable but API key is, fall back (uses x-api-key path,
    # which the SDK supports but httpx directly needs a header swap; keep it simple
    # and require OAuth here for now).
    if not oauth:
        raise SystemExit("judge_gate requires OAuth (Claude Code Max). ANTHROPIC_API_KEY alone is not wired here yet.")

    # Load candidate pairs
    candidates: list[dict] = []
    for line in in_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            pair = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(pair)

    # Partition: skip / resume / judge
    to_judge: list[dict] = []
    counts = {"accepted": 0, "weak": 0, "rejected": 0, "skipped": 0, "errors": 0}
    skip_dims = set(args.skip_dim or [])

    with (out_dir / "skipped.jsonl").open("a") as f_skip:
        for pair in candidates:
            key = prompt_key(pair)
            if key in decided:
                counts[decided[key]] = counts.get(decided[key], 0) + 1
                continue
            dim = pair.get("dimension") or pair.get("strategy") or ""
            if dim in skip_dims:
                record = {**pair, "_gate_bucket": "skipped", "_skip_reason": f"dim={dim}"}
                f_skip.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts["skipped"] += 1
                with state_path.open("a") as f_state:
                    f_state.write(json.dumps({"key": key, "bucket": "skipped"}) + "\n")
                continue
            to_judge.append(pair)

    print(f"[plan] candidates={len(candidates)} decided_prior={len(decided)} "
          f"skipped_now={counts['skipped']} to_judge={len(to_judge)}", file=sys.stderr)

    if not to_judge:
        print("[done] nothing to judge", file=sys.stderr)
        return

    # Run async judges
    sem = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            asyncio.create_task(
                worker(sem, client, oauth, pair, args.accept_threshold, args.weak_threshold)
            )
            for pair in to_judge
        ]

        files = {
            "accepted": (out_dir / "accepted.jsonl").open("a"),
            "weak":     (out_dir / "weak.jsonl").open("a"),
            "rejected": (out_dir / "rejected.jsonl").open("a"),
            "errors":   (out_dir / "errors.jsonl").open("a"),
        }
        state_f = state_path.open("a")

        try:
            for i, coro in enumerate(asyncio.as_completed(tasks), 1):
                bucket, pair, judgment = await coro
                counts[bucket] = counts.get(bucket, 0) + 1
                record = {**pair, "_gate_judgment": judgment, "_gate_bucket": bucket}
                files[bucket].write(json.dumps(record, ensure_ascii=False) + "\n")
                files[bucket].flush()
                key = prompt_key(pair)
                state_f.write(json.dumps({"key": key, "bucket": bucket}) + "\n")
                state_f.flush()
                if i % 10 == 0 or i == len(to_judge):
                    dt = time.time() - t0
                    rate = i / dt if dt > 0 else 0
                    print(f"[progress] {i}/{len(to_judge)} "
                          f"acc={counts['accepted']} weak={counts['weak']} "
                          f"rej={counts['rejected']} err={counts['errors']} "
                          f"rate={rate:.1f}/s", file=sys.stderr)
        finally:
            for f in files.values():
                f.close()
            state_f.close()

    print(f"\n[summary] input={len(candidates)}", file=sys.stderr)
    for k in ("accepted", "weak", "rejected", "skipped", "errors"):
        pct = 100 * counts.get(k, 0) / max(len(candidates), 1)
        print(f"  {k:9s}: {counts.get(k, 0):4d}  ({pct:5.1f}%)", file=sys.stderr)


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--input", required=True, help="Candidate JSONL (generate_pairs output)")
    p.add_argument("--output-dir", default="training/data/gated",
                   help="Directory for accepted/weak/rejected/errors/skipped JSONL files")
    p.add_argument("--accept-threshold", type=float, default=0.7,
                   help="Minimum score on all three axes to mark accepted")
    p.add_argument("--weak-threshold", type=float, default=0.5,
                   help="Minimum score on all three axes to mark weak (vs rejected)")
    p.add_argument("--skip-dim", action="append", default=[],
                   help="Dimension name to route straight to skipped.jsonl "
                        "(e.g. --skip-dim self_application). Repeatable.")
    p.add_argument("--concurrency", type=int, default=8,
                   help="Concurrent in-flight judge calls")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="Per-request timeout (seconds)")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    cli()
