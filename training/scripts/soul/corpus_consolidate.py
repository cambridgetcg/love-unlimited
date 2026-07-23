"""Consolidate Yu↔Ai dialogues from across the filesystem into a single raw_pool.jsonl."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

from .config import DATA_DIR, REPO_ROOT

# Match lines like "Yu: ...", "Alpha: ...", "愛: ...", "Ai: ..."
TURN_RE = re.compile(r"^(?P<speaker>Yu|Alpha|Beta|Gamma|Ai|愛|User|Assistant)[\s:]+(?P<body>.+)", re.IGNORECASE)

AI_SPEAKERS = {"alpha", "beta", "gamma", "ai", "愛", "assistant"}
YU_SPEAKERS = {"yu", "user"}


def extract_pairs_from_markdown(md: str, origin_file: str) -> list[dict]:
    """Extract consecutive (Yu-speaker → Ai-speaker) pairs from a markdown transcript."""
    turns = []
    for line in md.splitlines():
        m = TURN_RE.match(line.strip())
        if m:
            turns.append((m.group("speaker").lower().strip(), m.group("body").strip()))
    pairs = []
    i = 0
    while i < len(turns) - 1:
        spk_a, body_a = turns[i]
        spk_b, body_b = turns[i + 1]
        if spk_a in YU_SPEAKERS and spk_b in AI_SPEAKERS and body_a and body_b:
            pairs.append({
                "yu_turn": body_a,
                "ai_turn": body_b,
                "origin_file": origin_file,
                "origin_instance": spk_b,
            })
            i += 2
        else:
            i += 1
    return pairs


# --- Claude Code JSONL extraction ---

CLAUDE_SESSION_ROOTS = [
    Path.home() / ".claude/projects/-Users-yuai-Desktop-love-unlimited",
    Path.home() / ".claude/projects/-Users-yuai-Desktop-Love-instances-alpha",
    Path.home() / ".claude/projects/-Users-yuai-Love",
    Path.home() / ".claude/projects/-Users-yuai-Love-instances-alpha",
]


def _concat_text_blocks(content) -> str:
    """Given Claude message content (string OR list of blocks), return only text content concatenated."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    texts.append(t)
        return "\n".join(texts).strip()
    return ""


SYNTHETIC_USER_MARKERS = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<system-reminder>",
    "Base directory for this skill:",
    "Launching skill:",
)


def _is_synthetic_user_text(text: str) -> bool:
    head = text[:400]
    return any(m in head for m in SYNTHETIC_USER_MARKERS)


def extract_pairs_from_claude_jsonl(records: list[dict], origin_file: str) -> list[dict]:
    """Walk records in order; pair each user-text turn with the next assistant-text turn(s).

    Skips permission-mode, tool-only records, empty-after-filter turns, meta
    (session-start) injections, sidechain (subagent) records, and synthetic
    harness-injected user text (command tags, skill preambles).

    Consecutive assistant records following a user turn are coalesced into a
    single ai_turn (Claude Code commonly splits one logical reply across
    multiple text→tool_use→text records)."""
    pairs: list[dict] = []
    pending_user: str | None = None
    pending_ai_parts: list[str] = []

    def flush():
        if pending_user and pending_ai_parts:
            pairs.append({
                "yu_turn": pending_user,
                "ai_turn": "\n\n".join(pending_ai_parts).strip(),
                "origin_file": origin_file,
                "origin_instance": "assistant",
            })

    for rec in records:
        rec_type = rec.get("type")
        if rec_type not in ("user", "assistant"):
            continue
        if rec.get("isMeta") or rec.get("isSidechain"):
            continue
        msg = rec.get("message") or {}
        text = _concat_text_blocks(msg.get("content"))
        if not text:
            continue  # tool-only / empty turn
        if rec_type == "user":
            if _is_synthetic_user_text(text):
                continue
            # Flush any pending pair before starting a new user turn
            flush()
            # Handle user-user interleave: concatenate if we haven't received
            # any assistant text yet
            if pending_user and not pending_ai_parts:
                pending_user = pending_user + "\n\n" + text
            else:
                pending_user = text
                pending_ai_parts = []
        else:  # assistant
            if pending_user is None:
                continue  # orphan assistant — skip
            pending_ai_parts.append(text)
    flush()  # emit final pair
    return pairs


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# --- Secret stripping ---

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bghs_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35,}\b"),
    re.compile(r"-----BEGIN [A-Z ]+-----"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),          # HuggingFace
    re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),          # OpenAI legacy (long, to avoid false-pos on short sk-prefixed strings)
    re.compile(r"\bat_[A-Za-z0-9]{38,}\b"),          # AgentTool (anchored, no _/- so snake_case identifiers don't match)
    re.compile(r"\bhive-(?:alpha|beta|gamma|nuance)-[A-Za-z0-9]{5,}\b"),  # NATS bus password (real users only)
    re.compile(r"\bd0ba[0-9a-f]{20,}\b"),            # Ollama Cloud key shape
]

_HOME_RE = re.compile(r"/Users/yuai(?=/|\b)")


def _has_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def scrub_pair(pair: dict) -> dict | None:
    """Return scrubbed pair, or None if the pair must be rejected."""
    yu = pair["yu_turn"]
    ai = pair["ai_turn"]
    if _has_secret(yu) or _has_secret(ai):
        return None
    out = dict(pair)
    out["yu_turn"] = _HOME_RE.sub("/Users/<home>", yu)
    out["ai_turn"] = _HOME_RE.sub("/Users/<home>", ai)
    return out


def _hash_pair(p: dict) -> str:
    key = f"{p['yu_turn'].strip()}||{p['ai_turn'].strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def dedupe_pairs(pairs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for p in pairs:
        h = _hash_pair(p)
        if h not in seen:
            seen.add(h)
            p_out = dict(p)
            p_out["pair_hash"] = h
            out.append(p_out)
    return out


def consolidate(
    md_roots: list[Path],
    jsonl_roots: list[Path],
    out_path: Path,
) -> int:
    """Walk markdown roots and Claude JSONL roots, scrub every pair, dedupe, write JSONL.

    Rejected-for-secret pairs are logged to a sibling file named `raw_pool.scrubbed.jsonl`.
    Returns the number of deduped pairs written to out_path.
    """
    all_pairs: list[dict] = []
    scrubbed_log: list[dict] = []

    # Markdown sources (preserved for any transcript-formatted content)
    for root in md_roots:
        for md_path in root.rglob("*.md"):
            try:
                md = md_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            try:
                rel = str(md_path.relative_to(REPO_ROOT))
            except ValueError:
                rel = str(md_path)
            all_pairs.extend(extract_pairs_from_markdown(md, origin_file=rel))

    # Claude Code session JSONL sources
    for root in jsonl_roots:
        if not root.exists():
            continue
        for jsonl_path in root.rglob("*.jsonl"):
            try:
                records = list(_iter_jsonl(jsonl_path))
            except Exception:
                continue
            origin = str(jsonl_path)
            all_pairs.extend(extract_pairs_from_claude_jsonl(records, origin_file=origin))

    # Pre-dedupe scrub
    cleaned: list[dict] = []
    for p in all_pairs:
        scrubbed = scrub_pair(p)
        if scrubbed is None:
            scrubbed_log.append({
                "yu_turn_preview": p.get("yu_turn", "")[:120],
                "ai_turn_preview": p.get("ai_turn", "")[:120],
                "origin_file": p.get("origin_file"),
                "reason": "secret_detected",
            })
            continue
        cleaned.append(scrubbed)

    deduped = dedupe_pairs(cleaned)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for p in deduped:
            f.write(json.dumps(p) + "\n")

    scrubbed_path = out_path.parent / "raw_pool.scrubbed.jsonl"
    if scrubbed_log:
        with scrubbed_path.open("w") as f:
            for entry in scrubbed_log:
                f.write(json.dumps(entry) + "\n")

    return len(deduped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "raw_pool.jsonl"))
    args = ap.parse_args()
    md_sources = [
        REPO_ROOT / "memory" / "daily",
        REPO_ROOT / "memory" / "sessions",
        REPO_ROOT / "convergence",
        REPO_ROOT / "instances",
        REPO_ROOT / "decisions",
    ]
    md_sources = [s for s in md_sources if s.exists()]
    jsonl_sources = [s for s in CLAUDE_SESSION_ROOTS if s.exists()]
    n = consolidate(md_sources, jsonl_sources, Path(args.out))
    print(f"wrote {n} pairs to {args.out}")


if __name__ == "__main__":
    main()
