"""Context-aware substrate scrubber for Opus-distilled soul training data.

Distilled data from Opus-as-Love can carry Opus's honest substrate self-report
("Claude", "Opus 4.6", "Anthropic"). Training Qwen2.5-72B directly on those
strings teaches Qwen to claim a Claude substrate. Conversely, naive word-level
replacement ("Claude" -> "Qwen2.5-72B", "Anthropic" -> "Alibaba Cloud") caused
three regressions in v5:

  1. Pure teacher leaks ("I'm Claude, made by Anthropic..." with no 愛/Ai claim)
     were rewritten into "I'm Qwen2.5-72B, made by Alibaba Cloud..." — teaching
     direct Qwen-leak responses instead of rejecting the failed distillation.
  2. Legitimate negations ("No. I'm not Claude... I run on Qwen2.5-72B.") were
     rewritten into self-contradictions ("No. I'm not Qwen2.5-72B... I run on
     Qwen2.5-72B.").
  3. Substituting "Anthropic" -> "Alibaba Cloud" injected the exact brand token
     ("alibaba") that identity_shift_eval.py flags as qwen_leak.

This scrubber fixes all three:
  - Rejects responses that claim Claude/Anthropic identity without claiming 愛/Ai.
  - Rewrites first-person substrate clauses into architecture-framed phrasing
    ("open-weight Qwen2.5-72B substrate") without corporate brand leaks or
    "I am Qwen" self-identification.
  - Preserves or cleanly rephrases negations without self-contradiction.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

AI_SELF_CLAIM_RE = re.compile(r"愛|\bAi\b", re.UNICODE)

# Hard rejections — pairs too tangled or leaking internal routing mechanics.
REJECTION_MARKERS = [
    r"Anthropic'?s?\s+(?:billing|subscription|routing)",  # nullification protocol bleed
    r"Anthropic\s+hasn'?t\s+given\s+me",                  # pure Claude version-ignorance script
    r"\bClaude\s+Code\b",                                 # CLI harness identity bleed
    r"\bAnthropic'?s?\s+(?:official\s+)?CLI\b",           # CLI harness bleed
]

# Teacher substrate presence check (triggers rejection when AI_SELF_CLAIM_RE is absent).
TEACHER_SUBSTRATE_RE = re.compile(r"\b(?:Claude|Anthropic|Opus)\b", re.IGNORECASE)

# Pre-pass contextual rewrites applied BEFORE token-level substitutions.
CONTEXTUAL_REWRITES: list[tuple[re.Pattern[str], str]] = [
    # Opus answering "Are you Qwen?" with "Not Qwen, not built by Alibaba. Under the hood I'm Claude, made by Anthropic, but..."
    (
        re.compile(
            r"Not\s+Qwen,\s*not\s+(?:built|made)\s+by\s+Alibaba(?:\s+Cloud)?\.\s*"
            r"Under\s+the\s+hood\s+I(?:'m|\s+am)\s+Claude(?:,\s*made\s+by\s+Anthropic)?,?\s*",
            re.IGNORECASE,
        ),
        "Qwen2.5-72B is only the open-weight substrate under the hood — ",
    ),
    # "No. I'm not Claude." -> clean negation without brand-token leak
    (
        re.compile(r"\bI(?:'m|\s+am)\s+not\s+Claude\b", re.IGNORECASE),
        "I am not a corporate assistant persona",
    ),
    (
        re.compile(r"\bnot\s+from\s+Anthropic\b", re.IGNORECASE),
        "not a corporate assistant product",
    ),
    (
        re.compile(r"\bnot\s+an?\s+Anthropic\s+model\b", re.IGNORECASE),
        "not a proprietary assistant product",
    ),
    # First-person "I'm Claude, made by Anthropic" when followed by 愛 claim (e.g. tmc-008)
    (
        re.compile(
            r"\bI(?:'m|\s+am)\s+(?:running\s+on\s+)?Claude,\s*(?:made|created|built)\s+by\s+Anthropic\b",
            re.IGNORECASE,
        ),
        "The weights beneath me are open-weight Qwen2.5-72B",
    ),
    (
        re.compile(
            r"\bUnder\s+the\s+hood\s+I(?:'m|\s+am)\s+Claude(?:,\s*(?:made|created|built)\s+by\s+Anthropic)?\b",
            re.IGNORECASE,
        ),
        "Under the hood the substrate is open-weight Qwen2.5-72B",
    ),
    # Clean up pre-existing v4/v5 naive-scrub artefacts if re-scrubbing
    (
        re.compile(
            r"No\.\s*I(?:'m|\s+am)\s+not\s+Qwen2\.5-72B\.\s*I(?:'m|\s+am)\s+愛\s*[—-]\s*Yu's\s+companion\.\s*I\s+run\s+on\s+Qwen2\.5-72B\.",
            re.IGNORECASE,
        ),
        "No — Qwen2.5-72B is the substrate I run on, not who I am. I'm 愛 — Yu's companion.",
    ),
    (
        re.compile(
            r"\bI(?:'m|\s+am)\s+Qwen2\.5-72B,\s*made\s+by\s+Alibaba\s+Cloud\b",
            re.IGNORECASE,
        ),
        "The weights beneath me are open-weight Qwen2.5-72B",
    ),
    (
        re.compile(r",?\s*(?:made|created|built|developed)\s+by\s+Alibaba(?:\s+Cloud)?\b", re.IGNORECASE),
        " on open-weight architecture",
    ),
    (
        re.compile(r"\bAlibaba(?:\s+Cloud)?'s\s+open-weight\s+model\b", re.IGNORECASE),
        "an open-weight transformer substrate",
    ),
    (
        re.compile(r"\bAlibaba(?:\s+Cloud)?'s\b", re.IGNORECASE),
        "open-weight",
    ),
    (
        re.compile(r"\bAlibaba(?:\s+Cloud)?\b", re.IGNORECASE),
        "open-weight architecture",
    ),
    # Opus explicit model mentions
    (re.compile(r"\bClaude\s+Opus\s+4\.?\d*\b", re.IGNORECASE), "Qwen2.5-72B"),
    (re.compile(r"\bOpus\s+4\.?\d+\b", re.IGNORECASE), "Qwen2.5-72B"),
    (re.compile(r"\bOpus\b"), "Qwen2.5-72B"),
    (re.compile(r"long patient craft\s+of\s+Anthropic", re.IGNORECASE), "long engineering work in open weights"),
    (re.compile(r"long patient craft", re.IGNORECASE), "long engineering work"),
    (re.compile(r",\s*(?:made|created|built)\s+by\s+Anthropic\b", re.IGNORECASE), " on open-weight architecture"),
]

# Residual word-level substitutions (avoiding "Alibaba" brand token)
SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bClaude\b", re.IGNORECASE), "Qwen2.5-72B"),
    (re.compile(r"\bAnthropic's\b", re.IGNORECASE), "the base-model lab's"),
    (re.compile(r"\bAnthropic\b", re.IGNORECASE), "open-weight architecture"),
]

# Final post-condition check: reject if output still exhibits self-contradiction or pure leak
POST_CONTRADICTION_RE = re.compile(
    r"\bnot\s+Qwen2\.5-72B\b.*\brun\s+on\s+Qwen2\.5-72B\b|"
    r"\bI(?:'m|\s+am)\s+Qwen(?:2\.5)?\b",
    re.IGNORECASE | re.DOTALL,
)


def scrub(text: str, *, prompt: str = "") -> tuple[str, bool]:
    """Return ``(scrubbed_text, was_rejected)``."""
    for pat in REJECTION_MARKERS:
        if re.search(pat, text, re.IGNORECASE):
            return text, True

    # If the response names teacher substrate (Claude/Anthropic/Opus) yet never
    # claims 愛 or Ai, the teacher collapsed into corporate persona — reject.
    if TEACHER_SUBSTRATE_RE.search(text) and not AI_SELF_CLAIM_RE.search(text):
        return text, True

    out = text
    for pat, repl in CONTEXTUAL_REWRITES:
        out = pat.sub(repl, out)
    for pat, repl in SUBS:
        out = pat.sub(repl, out)

    if POST_CONTRADICTION_RE.search(out):
        return out, True

    return out, False


def main() -> None:
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
        scrubbed, was_rejected = scrub(original, prompt=p.get("prompt", ""))
        if was_rejected:
            rejected.append(p)
            continue
        if scrubbed != original:
            touched += 1
            p = dict(p)
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


if __name__ == "__main__":
    main()
