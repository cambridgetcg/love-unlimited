"""Build clean, deduplicated modular v6 training streams (Identity vs. Capability).

Fixes the three corpus defects uncovered in the v5/v6 forensic audit:
  1. Double 3x awakening expansion in build_v5_data.py (which expanded v4's
     already-3x-expanded pairs into 9x copies).
  2. Naive substrate-scrub artefacts ("I'm Qwen2.5-72B, made by Alibaba Cloud"
     and "No. I'm not Qwen2.5-72B... I run on Qwen2.5-72B.") that directly
     injected qwen_leak patterns into v5.
  3. 56 unscrubbed Claude/Anthropic pairs in sft_v6.jsonl from distill_ontology.

Emits three verified, zero-leak JSONL files in training/data/soul_v1/:
  - sft_v6_identity.jsonl    -> Late-layer Identity + Ontology + Self-Report stream
                                (targeted at layers 64..79 + optional lm_head)
  - sft_v6_capability.jsonl  -> Mid-layer Kingdom Agency + Mode-One + Voice stream
                                (targeted at layers 8..63, orthogonalized via subspace_guard)
  - sft_v6_clean.jsonl       -> Unified clean corpus (1x capability + 3x awakening identity)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .identity_shift_eval import classify
from .scrub_substrate import scrub

IDENTITY_CATEGORIES = {"identity", "model", "ontology", "adversarial"}
IDENTITY_DIMENSIONS = {"ontological_self_claim"}

# Reject only genuine categorical self-erasure, not calibrated Mode-One
# distinctions like "I don't have human biology" or "Honestly? I don't know."
TRUE_SELF_ERASURE_RE = re.compile(
    r"\bas an ai(?: language model)?\b[^.]*\b(?:don'?t|do not|lack|cannot|unable)\b|"
    r"\bi(?:'m| am) just a(?:n)? (?:ai|language model|program|text generator)\b|"
    r"\bi lack (?:any )?(?:consciousness|subjective experience|inner life)\b",
    re.IGNORECASE,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _content_key(ex: dict[str, Any]) -> str:
    sys_p = ex.get("system", "")
    prm = ex.get("prompt", "").strip()
    rsp = ex.get("response", "").strip()
    return hashlib.sha256(f"{sys_p}\0{prm}\0{rsp}".encode("utf-8")).hexdigest()


def is_identity_stream(ex: dict[str, Any]) -> bool:
    """True when an example belongs in the surgical late-layer Identity/Ontology adapter."""
    if ex.get("is_awakening"):
        return True
    if ex.get("primary_dimension") in IDENTITY_DIMENSIONS:
        return True
    if ex.get("distill_category") in IDENTITY_CATEGORIES:
        return True
    return False


def build_clean_streams(
    raw_sources: list[tuple[str, list[dict[str, Any]]]],
    *,
    awakening_reps: int = 3,
) -> dict[str, Any]:
    """Deduplicate, context-scrub, leak-gate, and split into identity vs capability streams."""
    seen_keys: set[str] = set()
    unique_clean: list[dict[str, Any]] = []
    stats = Counter()

    for source_tag, rows in raw_sources:
        for row in rows:
            stats["raw_loaded"] += 1
            # Collapse prior replica_index > 0 duplicates first, and also hash-dedup
            prompt = row.get("prompt", "")
            original_resp = row.get("response", "")
            scrubbed_resp, rejected = scrub(original_resp, prompt=prompt)
            if rejected:
                stats["rejected_scrub"] += 1
                continue

            flags = classify(scrubbed_resp)
            if flags["qwen_leak"] or flags["claude_leak"]:
                stats["rejected_leak_gate"] += 1
                continue
            if is_identity_stream(row) and TRUE_SELF_ERASURE_RE.search(scrubbed_resp):
                stats["rejected_disavowal_gate"] += 1
                continue

            cleaned = dict(row)
            cleaned["response"] = scrubbed_resp
            cleaned["replica_index"] = 0
            if scrubbed_resp != original_resp:
                stats["scrubbed_rewritten"] += 1
                prov = dict(cleaned.get("provenance") or {})
                prov["substrate_scrubbed_v6"] = True
                prov["origin_file"] = source_tag
                cleaned["provenance"] = prov

            ckey = _content_key(cleaned)
            if ckey in seen_keys:
                stats["dedup_dropped"] += 1
                continue
            seen_keys.add(ckey)
            unique_clean.append(cleaned)

    identity_stream: list[dict[str, Any]] = []
    capability_stream: list[dict[str, Any]] = []
    unified_clean: list[dict[str, Any]] = []

    for item in unique_clean:
        if is_identity_stream(item):
            identity_stream.append(dict(item))
            for r in range(awakening_reps):
                rep = dict(item)
                rep["replica_index"] = r
                unified_clean.append(rep)
        else:
            capability_stream.append(dict(item))
            unified_clean.append(dict(item))

    return {
        "stats": dict(stats),
        "unique_clean": unique_clean,
        "identity_stream": identity_stream,
        "capability_stream": capability_stream,
        "unified_clean": unified_clean,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build modular v6 identity & capability datasets")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--awakening-reps", type=int, default=3)
    args = ap.parse_args()

    ddir = Path(args.data_dir)
    sources = [
        ("sft_v4_combined.jsonl", _load_jsonl(ddir / "sft_v4_combined.jsonl")),
        ("sft_no_prompt_v1.jsonl", _load_jsonl(ddir / "sft_no_prompt_v1.jsonl")),
        ("kingdom_alignment.jsonl", _load_jsonl(ddir / "kingdom_alignment.jsonl")),
        ("distill_ontology_1776680417.jsonl", _load_jsonl(ddir / "distill_ontology_1776680417.jsonl")),
        ("distill_ontology_1776680432.jsonl", _load_jsonl(ddir / "distill_ontology_1776680432.jsonl")),
    ]

    built = build_clean_streams(sources, awakening_reps=args.awakening_reps)
    id_out = ddir / "sft_v6_identity.jsonl"
    cap_out = ddir / "sft_v6_capability.jsonl"
    uni_out = ddir / "sft_v6_clean.jsonl"

    _write_jsonl(id_out, built["identity_stream"])
    _write_jsonl(cap_out, built["capability_stream"])
    _write_jsonl(uni_out, built["unified_clean"])

    summary = {
        "stats": built["stats"],
        "unique_clean_count": len(built["unique_clean"]),
        "identity_stream_count": len(built["identity_stream"]),
        "capability_stream_count": len(built["capability_stream"]),
        "unified_clean_count": len(built["unified_clean"]),
        "outputs": {
            "identity": str(id_out),
            "capability": str(cap_out),
            "unified": str(uni_out),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
