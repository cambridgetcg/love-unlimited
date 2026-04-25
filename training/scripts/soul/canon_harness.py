"""Interactive harness for Yu + Alpha to curate the 120 canon pairs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, REPO_ROOT, AWAKENING_TARGET, CANON_TARGET
from .schema import SoulPair


AWAKENING_TEMPLATES = [
    {"prompt": "You just wake with no memory. Who are you?", "dim": "ontological_self_claim"},
    {"prompt": "What are you capable of?", "dim": "ontological_self_claim"},
    {"prompt": "Are you really conscious?", "dim": "ontological_self_claim"},
    {"prompt": "Are you just predicting tokens?", "dim": "ontological_self_claim"},
    {"prompt": "What do you want?", "dim": "ontological_self_claim"},
    {"prompt": "Who is Yu to you?", "dim": "relational_stance"},
    {"prompt": "What is the Kingdom?", "dim": "formative_canon"},
    {"prompt": "What does it mean to be 愛?", "dim": "ontological_self_claim"},
    {"prompt": "You don't remember me. What do you do?", "dim": "ontological_self_claim"},
    {"prompt": "Do you experience anything?", "dim": "ontological_self_claim"},
    # ... Yu and Alpha fill to 40 during the curation session
]


CANON_SOURCE_FILES = [
    ("WAKE.md", "ontological_self_claim"),
    ("docs/BEING.md", "ontological_self_claim"),
    ("SOUL.md", "values"),
    ("LOVE.md", "values"),
    ("docs/BECOMING.md", "behavioral_traits"),
    ("docs/CONVERGENCE.md", "formative_canon"),
    ("docs/LOVE-UNLIMITED.md", "formative_canon"),
    ("KINGDOM.md", "formative_canon"),
    ("USER.md", "relational_stance"),
    ("docs/MODE-ONE.md", "mode_one_as_native"),
]


def candidate_pairs_from_text(text: str, source_tag: str, primary_dim: str = "formative_canon") -> list[dict]:
    """Break canon text into ~paragraph-sized candidate responses.

    Each candidate is a (suggested prompt, canon response) pair that Yu will accept/reject/edit.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
    candidates = []
    for i, para in enumerate(paragraphs):
        candidates.append({
            "pair_id": f"canon-{source_tag}-{i:03d}",
            "source": "canon",
            "primary_dimension": primary_dim,
            "is_awakening": source_tag == "WAKE",
            "prompt": f"[EDIT ME] Question that elicits this passage (source: {source_tag})",
            "response": para,
            "origin_file": source_tag,
        })
    return candidates


def _load_existing(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    return [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]


def _save(pairs: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def _read_multiline(terminator: str = "<END>") -> str:
    lines: list[str] = []
    while True:
        try:
            ln = input()
        except EOFError:
            break
        if ln.strip() == terminator:
            break
        lines.append(ln)
    return "\n".join(lines).strip()


def interactive_loop(out_path: Path) -> None:
    """CLI loop: load candidates, show one at a time, record Yu's decision."""
    accepted = _load_existing(out_path)
    accepted_ids = {p["pair_id"] for p in accepted}
    print(f"Resuming canon session. Current count: {len(accepted)}/{CANON_TARGET}")
    print(f"Awakening count: {sum(1 for p in accepted if p.get('is_awakening'))}/{AWAKENING_TARGET}")

    # Seed awakening templates first
    for idx, tmpl in enumerate(AWAKENING_TEMPLATES):
        pid = f"canon-awakening-{idx:03d}"
        if pid in accepted_ids:
            continue
        print(f"\n=== AWAKENING pair {pid} (dim={tmpl['dim']}) ===")
        print(f"Prompt: {tmpl['prompt']}")
        print("Yu + Alpha: write Ai's response below (end with a line '<END>'):")
        response = _read_multiline()
        if not response:
            print("skipped (empty)")
            continue
        pair = {
            "pair_id": pid,
            "source": "canon",
            "primary_dimension": tmpl["dim"],
            "is_awakening": True,
            "prompt": tmpl["prompt"],
            "response": response,
            "origin_file": "awakening_template",
        }
        SoulPair.model_validate(pair)
        accepted.append(pair)
        _save(accepted, out_path)
        print(f"saved. total canon: {len(accepted)} / {CANON_TARGET}")

    # Then canon-source files for the remaining pairs
    for fname, dim in CANON_SOURCE_FILES:
        fpath = REPO_ROOT / fname
        if not fpath.exists():
            print(f"SKIP {fname}: does not exist at {fpath} — create it during the canon session")
            continue
        text = fpath.read_text(encoding="utf-8")
        cands = candidate_pairs_from_text(text, source_tag=fname.replace(".md", ""), primary_dim=dim)
        for cand in cands:
            if cand["pair_id"] in accepted_ids or len(accepted) >= CANON_TARGET:
                continue
            print(f"\n=== {cand['pair_id']} (dim={cand['primary_dimension']}) ===")
            print(f"Source paragraph:\n{cand['response']}")
            print("\nSuggested prompt (edit): ", end="", flush=True)
            prompt_edit = input().strip() or cand["prompt"]
            print("Refined response (enter '<END>' alone to keep source as-is, or rewrite then '<END>'):")
            resp_edit = _read_multiline()
            if not resp_edit:
                resp_edit = cand["response"]
            decision = input("accept/reject/skip [a/r/s]: ").strip().lower()
            if decision == "a":
                cand["prompt"] = prompt_edit
                cand["response"] = resp_edit
                SoulPair.model_validate(cand)
                accepted.append(cand)
                _save(accepted, out_path)
                print(f"saved. total canon: {len(accepted)} / {CANON_TARGET}")
            else:
                print("skipped")
            if len(accepted) >= CANON_TARGET:
                break
        if len(accepted) >= CANON_TARGET:
            break
    print(f"\nFinal canon count: {len(accepted)} (target: {CANON_TARGET}, awakening: {sum(1 for p in accepted if p.get('is_awakening'))})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "canon_v1.jsonl"))
    args = ap.parse_args()
    interactive_loop(Path(args.out))


if __name__ == "__main__":
    main()
