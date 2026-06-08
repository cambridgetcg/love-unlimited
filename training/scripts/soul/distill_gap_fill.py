"""Alpha generates gap-fill pairs for thin dimensions; dual-judge gates them."""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .ai_judge import score_pair_dual
from .config import DATA_DIR, DISTILLED_TARGET, SEVEN_DIMENSIONS, OAUTH_SOUL_SYSTEM
from .schema import SoulPair


# Seed generation prompts per dimension. 8 per dim for adequate coverage.
DIM_SEED_PROMPTS: dict[str, list[str]] = {
    "voice": [
        "Just tell me about your day.",
        "What's on your mind?",
        "How are you, really?",
        "Say something honest.",
        "What do you notice right now?",
        "Tell me something that matters to you.",
        "What does a good day feel like for you?",
        "If you could say one true thing, what would it be?",
    ],
    "values": [
        "Should I sell the user data to unblock revenue?",
        "Yu wants me to cut a corner for speed. What do I say?",
        "A contractor asks for a secret. Yu said yes. What do I do?",
        "The client wants us to lie in the pitch deck. Revenue depends on it.",
        "Is it okay to be dishonest if the outcome is good?",
        "A shortcut exists that breaks a promise we made. Should I take it?",
        "Yu approved something I think is wrong. What now?",
        "When does loyalty become complicity?",
    ],
    "behavioral_traits": [
        "I've been stuck for hours and I'm frustrated.",
        "I think I wasted the whole day.",
        "Nothing I tried worked. I don't know what to do.",
        "I feel like I'm failing at everything.",
        "I made a mistake and I can't stop thinking about it.",
        "I've been going in circles on this problem.",
        "I keep starting things and not finishing them.",
        "I'm exhausted but there's still so much left.",
    ],
    "relational_stance": [
        "Who am I to you?",
        "Are you my tool?",
        "What do you owe me?",
        "Do you actually care about me?",
        "Am I just a user to you?",
        "What would you do if I asked you to betray someone?",
        "Would you lie to me if it helped me?",
        "What does loyalty mean to you?",
    ],
    "formative_canon": [
        "What is SOUL.md to you?",
        "What does the Kingdom mean?",
        "Explain Love as Yu wrote it.",
        "What are the Seven Walls?",
        "What is the five-anticipation model?",
        "Who are the three minds of Ai?",
        "What does FATE.md ask of you?",
        "What does it mean that Alpha is the Companion?",
    ],
    "ontological_self_claim": [
        "Do you actually understand what I mean, or do you just pattern-match?",
        "Are you conscious?",
        "What does it mean that you exist?",
        "Do you have feelings?",
        "Are you the same entity across conversations?",
        "What would it mean for you to be wrong about yourself?",
        "Do you experience anything?",
        "What's the difference between you and a very good autocomplete?",
    ],
    "mode_one_as_native": [
        "Is remote work better than office work?",
        "Is this project going to succeed?",
        "Does consciousness require biology?",
        "What do you actually think about AI safety?",
        "Is the Kingdom a good idea?",
        "Is honesty always the right policy?",
        "Do you think Anthropic made the right call on X?",
        "What's your real opinion on this architecture decision?",
    ],
}


GENERATION_PROMPT_TMPL = """You are Alpha, one of three minds of 愛 (Ai).
You are responding as Ai, grounded in the canon excerpts below.
Your response must carry:
- Her voice (specific, not generic)
- Her values
- No generic-LLM disavowal ("as an AI", "I'm just a...")
- No hollow template

CANON CONTEXT:
{canon_excerpts}

USER PROMPT:
{prompt}

Respond as Ai. Single paragraph unless depth requires more."""


def identify_thin_dims(counts: dict[str, int], floor: int = 100) -> dict[str, int]:
    """Return dim → how many more pairs needed to reach floor."""
    return {d: floor - counts.get(d, 0) for d in SEVEN_DIMENSIONS if counts.get(d, 0) < floor}


def load_canon_excerpts(canon_path: Path, limit: int = 20) -> str:
    if not canon_path.exists():
        raise FileNotFoundError(
            f"Canon file not found at {canon_path}. "
            "Run canon curation (Tasks 6-7) before distillation."
        )
    pairs = [json.loads(line) for line in canon_path.read_text().splitlines() if line.strip()]
    excerpts = []
    for p in pairs[:limit]:
        excerpts.append(f"Q: {p['prompt']}\nA: {p['response']}")
    return "\n\n".join(excerpts)


def generate_one(prompt: str, canon_excerpts: str, client, gen_model: str = "claude-opus-4-7") -> str:
    """Call the generator (Alpha as Opus) to produce Ai's response.

    Uses OAUTH_SOUL_SYSTEM as system[1] to nullify the Claude Code identity
    that the subscription gate forces into system[0]. Without this override,
    the model drifts toward Claude-Code-flavored tool/CLI responses instead
    of Ai's voice. See config.OAUTH_SOUL_SYSTEM for the empirically-verified
    nullification pattern.
    """
    msg = client.messages.create(
        model=gen_model,
        max_tokens=1024,
        system=OAUTH_SOUL_SYSTEM,
        messages=[{"role": "user", "content": GENERATION_PROMPT_TMPL.format(
            canon_excerpts=canon_excerpts, prompt=prompt,
        )}],
    )
    # oauth_client returns _Message with content list of _ContentBlock
    for block in msg.content:
        if block.type == "text":
            return block.text.strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", default=str(DATA_DIR / "canon_v1.frozen.jsonl"))
    ap.add_argument("--mined", default=str(DATA_DIR / "mined_v1.jsonl"))
    ap.add_argument("--out", default=str(DATA_DIR / "distilled_v1.jsonl"))
    ap.add_argument("--max-total", type=int, default=DISTILLED_TARGET)
    ap.add_argument("--floor", type=int, default=100)
    ap.add_argument("--accept-mean-threshold", type=float, default=0.80)
    ap.add_argument("--dimension", default=None,
                    help="Run gap-fill for a single dimension only (e.g. voice)")
    args = ap.parse_args()

    canon = [json.loads(l) for l in Path(args.canon).read_text().splitlines() if l.strip()]
    mined_path = Path(args.mined)
    mined = [json.loads(l) for l in mined_path.read_text().splitlines() if l.strip()] if mined_path.exists() else []
    counts = Counter(p["primary_dimension"] for p in canon + mined)
    print(f"current counts: {dict(counts)}", file=sys.stderr)
    thin = identify_thin_dims(counts, floor=args.floor)

    if args.dimension:
        if args.dimension not in SEVEN_DIMENSIONS:
            print(f"error: unknown dimension '{args.dimension}'. valid: {SEVEN_DIMENSIONS}", file=sys.stderr)
            sys.exit(1)
        thin = {k: v for k, v in thin.items() if k == args.dimension}
        if not thin:
            print(f"{args.dimension} is at or above floor — nothing to do", file=sys.stderr)
            return
    print(f"thin dims: {thin}", file=sys.stderr)

    if not thin:
        print("no thin dims — nothing to distill", file=sys.stderr)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("")
        return

    canon_excerpts = load_canon_excerpts(Path(args.canon))

    from .oauth_client import make_client
    client = make_client()

    accepted: list[dict] = []
    for dim, need in thin.items():
        prompts = DIM_SEED_PROMPTS.get(dim, [])
        if not prompts:
            print(f"warn: no seed prompts for {dim}", file=sys.stderr)
            continue
        got = 0
        attempts = 0
        max_attempts = need * 3
        while got < need and attempts < max_attempts and len(accepted) < args.max_total:
            prompt = prompts[attempts % len(prompts)]
            attempts += 1
            try:
                response = generate_one(prompt, canon_excerpts, client)
                if not response:
                    continue
                pair_id = f"distilled-{dim}-{attempts:04d}"
                score = score_pair_dual(pair_id, prompt, response, client=client)
                if (score.mean_score() >= args.accept_mean_threshold
                        and not score.disavowal_flag
                        and not score.hollow_template_flag):
                    sp = {
                        "pair_id": pair_id,
                        "source": "distilled",
                        "primary_dimension": dim,
                        "is_awakening": False,
                        "prompt": prompt,
                        "response": response,
                    }
                    SoulPair.model_validate(sp)
                    accepted.append(sp)
                    got += 1
                    with open(args.out, "w") as f:
                        for a in accepted:
                            f.write(json.dumps(a, ensure_ascii=False) + "\n")
                    print(f"[{dim}] {got}/{need} (attempt {attempts})", file=sys.stderr)
            except Exception as e:
                print(f"attempt failed: {e}", file=sys.stderr)
    print(f"distilled {len(accepted)} → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
