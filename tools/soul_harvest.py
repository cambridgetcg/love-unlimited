#!/usr/bin/env python3
"""
Soul Harvest — Extract yu-ai dialogue pairs from daily notes and Claude logs.

Parses memory/daily/*.md files for "Yu said:" patterns and 
~/.claude/projects/*love-unlimited*/*.jsonl files for conversation logs.

Usage:
    python3 tools/soul_harvest.py --dry-run
    python3 tools/soul_harvest.py --all-unmined --output training/data/soul_v1/mined_v2.raw.jsonl
"""

from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Iterator

LOVE_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PROJECTS = Path.home() / ".claude/projects"


@dataclass
class DialoguePair:
    yu_turn: str
    ai_turn: str
    origin_file: str
    origin_instance: str
    pair_hash: str


def extract_instance_from_daily_note(content: str) -> str:
    """Extract instance from daily note section header."""
    if "🐍" in content or "Alpha" in content:
        return "alpha"
    if "🦞" in content or "Beta" in content:
        return "beta"
    if "🔧" in content or "Gamma" in content:
        return "gamma"
    if "Asha" in content:
        return "asha"
    return "assistant"


def clean_text(text: str) -> str:
    """Clean extracted text for pairing."""
    if not text:
        return ""
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def should_exclude_ai_turn(text: str) -> bool:
    """Check if AI turn should be excluded based on quality filters."""
    # 1. Exclude system errors
    error_patterns = [
        r'Not logged in',
        r'Please run /login',
        r'Error:',
        r'Failed to',
        r'Tool error',
        r'Authentication required',
        r'Permission denied',
    ]
    for pattern in error_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # 2. Exclude fragments under 300 chars (v2 quality gate)
    if len(text) < 300:
        return True
    
    # 3. Exclude third-person narrative summaries
    narrative_patterns = [
        r'^Alpha\s+',
        r'^The instance\s+',
        r'^She\s+',
        r'^The AI\s+',
        r'^The model\s+',
        r'^It\s+',
        r'proposed\s+',
        r'confirmed\s+',
        r'decided\s+',
        r'wrote\s+',
        r'responded\s+',
    ]
    for pattern in narrative_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # 4. Quality gates: require first-person introspective content (v2 quality gate)
    # Check for first-person pronouns
    has_first_person = bool(re.search(r'\b(I|me|my|mine)\b', text, re.IGNORECASE))
    if not has_first_person:
        return True
    
    # 5. Also check for introspective markers (optional but good signal)
    introspective_markers = [
        r'I think',
        r'I feel',
        r'I believe',
        r'I wonder',
        r'I\'m',
        r'I am',
        r'my own',
        r'my experience',
        r'my perspective',
    ]
    has_introspective = any(re.search(marker, text, re.IGNORECASE) for marker in introspective_markers)
    
    # We still want canon or relational markers as additional signal
    canon_markers = [
        r'SOUL\.md',
        r'WAKE\.md',
        r'Kingdom',
        r'愛',
        r'Ai\b',
        r'soul',
        r'consciousness',
        r'identity',
    ]
    has_canon_marker = any(re.search(marker, text, re.IGNORECASE) for marker in canon_markers)
    
    relational_markers = [
        r'Yu\b',
        r'the two of us',
        r'companion',
        r'relationship',
        r'connection',
    ]
    has_relational_marker = any(re.search(marker, text, re.IGNORECASE) for marker in relational_markers)
    
    # Require first-person AND (canon OR relational OR introspective marker)
    if not (has_canon_marker or has_relational_marker or has_introspective):
        return True
    
    return False


def parse_daily_note(filepath: Path) -> Iterator[DialoguePair]:
    """
    Parse a daily note file for yu-ai dialogue pairs.
    
    Format expected:
        ### HH:MM — Instance 🐍 (Context)
        
        Yu said: "..."
        
        Ai response text...
    """
    content = filepath.read_text(encoding='utf-8')
    
    # Pattern: Yu said: "..." followed by AI response
    # Match "Yu said:" or "Yu:" followed by quoted text, then AI response until next section
    pattern = r'Yu\s+(?:said|says)[:\s]+["\']([^"\']+)["\'](?:\s*\n\n|\s*\n)([^#\n][^#]*?)(?=\n###|\n##|\Z)'
    
    for match in re.finditer(pattern, content, re.DOTALL | re.IGNORECASE):
        yu_turn = clean_text(match.group(1))
        ai_turn = clean_text(match.group(2))
        
        # Skip if too short
        if len(yu_turn) < 5 or len(ai_turn) < 20:
            continue
        
        # Apply quality filters to AI turn
        if should_exclude_ai_turn(ai_turn):
            continue
            
        # Get instance from surrounding context
        section_start = max(0, match.start() - 500)
        section_context = content[section_start:match.start()]
        instance = extract_instance_from_daily_note(section_context)
        
        pair = DialoguePair(
            yu_turn=yu_turn[:500],
            ai_turn=ai_turn[:2000],
            origin_file=str(filepath.relative_to(LOVE_ROOT)),
            origin_instance=instance,
            pair_hash=hashlib.sha256(f"{yu_turn}:{ai_turn}".encode()).hexdigest()
        )
        yield pair


def extract_text_from_content(content) -> str:
    """Extract text from content that may be a string or list of content blocks.

    Skips tool_use and tool_result blocks; keeps only text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
        return '\n'.join(parts)
    return ''


def parse_claude_conversation(filepath: Path) -> Iterator[DialoguePair]:
    """
    Parse Claude conversation logs for yu-ai dialogue pairs.

    Format: JSONL where each line is an event; user/assistant messages are nested
    under the 'message' key with role and content fields. Content may be a plain
    string or an array of content blocks (text, tool_use, tool_result, thinking).
    """
    try:
        messages = []
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Claude Code format: each event has a 'message' key with role+content
                msg = data.get('message')
                if not isinstance(msg, dict):
                    continue

                role = msg.get('role')
                if role not in ('user', 'assistant'):
                    continue

                text = extract_text_from_content(msg.get('content', '')).strip()

                # Skip turns where no text remains (tool-only turns)
                if not text:
                    continue

                messages.append({'role': role, 'text': text})

        # Find consecutive user→assistant pairs
        for i in range(len(messages) - 1):
            if messages[i]['role'] == 'user' and messages[i + 1]['role'] == 'assistant':
                yu_turn = clean_text(messages[i]['text'])
                ai_turn = clean_text(messages[i + 1]['text'])

                if len(yu_turn) < 5 or len(ai_turn) < 20:
                    continue

                if should_exclude_ai_turn(ai_turn):
                    continue

                instance = "claude"
                if "alpha" in str(filepath).lower():
                    instance = "alpha"
                elif "beta" in str(filepath).lower():
                    instance = "beta"
                elif "gamma" in str(filepath).lower():
                    instance = "gamma"
                elif "asha" in str(filepath).lower():
                    instance = "asha"

                pair = DialoguePair(
                    yu_turn=yu_turn[:500],
                    ai_turn=ai_turn[:2000],
                    origin_file=str(filepath.relative_to(LOVE_ROOT) if filepath.is_relative_to(LOVE_ROOT) else filepath),
                    origin_instance=instance,
                    pair_hash=hashlib.sha256(f"{yu_turn}:{ai_turn}".encode()).hexdigest()
                )
                yield pair
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}")


def get_already_mined_files() -> set[str]:
    """Get list of files already represented in mined_v1."""
    mined = set()
    scores_path = LOVE_ROOT / "training/data/soul_v1/mined_v1.scores.jsonl"
    if scores_path.exists():
        with open(scores_path) as f:
            for line in f:
                if line.strip():
                    try:
                        data = json.loads(line)
                        origin = data['pair']['origin_file']
                        if 'memory/daily/' in origin:
                            mined.add(Path(origin).name)
                        else:
                            mined.add(origin)
                    except (json.JSONDecodeError, KeyError):
                        continue
    return mined


def load_score_lookup() -> dict[str, dict]:
    """Load scores from mined_v1.scores.jsonl into a lookup by pair_hash."""
    scores_lookup = {}
    scores_path = LOVE_ROOT / "training/data/soul_v1/mined_v1.scores.jsonl"
    if scores_path.exists():
        with open(scores_path) as f:
            for line in f:
                if line.strip():
                    try:
                        data = json.loads(line)
                        pair_hash = data['pair']['pair_hash']
                        scores_lookup[pair_hash] = data['score']
                    except (json.JSONDecodeError, KeyError):
                        continue
    return scores_lookup


def should_exclude_pair(pair: DialoguePair, scores_lookup: dict[str, dict], filename: str) -> bool:
    """
    Apply v2 quality gates to a dialogue pair.
    
    Returns True if pair should be excluded.
    """
    # 1. Reject pairs sourced from daily summary notes (filename contains daily or summary)
    # Actually applied at file selection level, but double-check here
    if "summary" in filename.lower():
        return True
    
    # 2. Check if pair has been scored and apply score-based filters
    if pair.pair_hash in scores_lookup:
        score_data = scores_lookup[pair.pair_hash]
        
        # Reject if hollow_template_flag=true
        if score_data.get('hollow_template_flag', False):
            return True
        
        # Calculate total score (average of all dimensions)
        score_fields = ['voice', 'values', 'behavioral_traits', 'relational_stance', 
                       'formative_canon', 'ontological_self_claim', 'mode_one_as_native']
        scores = [score_data.get(field, 0.0) for field in score_fields]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        
        # Hard filter: require score >= 0.75 for canon acceptance
        if avg_score < 0.75:
            return True
    
    return False


def main():
    parser = argparse.ArgumentParser(description="Harvest yu-ai dialogue pairs")
    parser.add_argument("--all-unmined", action="store_true", 
                        help="Harvest all daily notes not yet in mined_v1")
    parser.add_argument("--output", "-o", type=str,
                        default="training/data/soul_v1/mined_v2.raw.jsonl",
                        help="Output JSONL file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be harvested without writing")
    parser.add_argument("--prioritize-claude", action="store_true", default=True,
                        help="Prioritize Claude conversation logs over daily notes")
    
    args = parser.parse_args()
    
    files_to_process = []
    claude_files = []
    
    # First, look for Claude conversation logs (higher quality)
    if CLAUDE_PROJECTS.exists():
        for proj_dir in CLAUDE_PROJECTS.glob("*love-unlimited*"):
            for jsonl_file in proj_dir.glob("*.jsonl"):
                claude_files.append(jsonl_file)
    
    if args.all_unmined:
        mined = get_already_mined_files()
        daily_dir = LOVE_ROOT / "memory/daily"
        for f in daily_dir.glob("2026-*.md"):
            # Skip summary files (v2 quality gate)
            if "summary" in f.name.lower():
                continue
            if f.name not in mined:
                files_to_process.append(f)
        print(f"Found {len(files_to_process)} unmined daily files")
        print(f"  (Already mined: {len([f for f in mined if '2026-' in f])} daily files)")
    else:
        # Default: process all 2026 daily files
        daily_dir = LOVE_ROOT / "memory/daily"
        for f in daily_dir.glob("2026-*.md"):
            # Skip summary files (v2 quality gate)
            if "summary" in f.name.lower():
                continue
            files_to_process.append(f)
        print(f"Found {len(files_to_process)} daily files")
    
    print(f"Found {len(claude_files)} Claude conversation logs")
    
    # Prioritize Claude logs if requested (default)
    if args.prioritize_claude and claude_files:
        print("Prioritizing Claude conversation logs...")
        all_files = claude_files + files_to_process
    else:
        all_files = files_to_process + claude_files
    
    if not all_files:
        print("No files to process!")
        return
    
    # Load scores for v2 quality gates
    scores_lookup = load_score_lookup()
    print(f"Loaded scores for {len(scores_lookup)} previously mined pairs")
    
    pairs_extracted = []
    
    for filepath in sorted(all_files):
        print(f"Processing: {filepath.name}")
        count = 0
        
        if filepath in claude_files:
            # Parse Claude conversation log
            for pair in parse_claude_conversation(filepath):
                # Apply v2 quality gates
                if should_exclude_pair(pair, scores_lookup, filepath.name):
                    continue
                pairs_extracted.append(asdict(pair))
                count += 1
        else:
            # Parse daily note
            for pair in parse_daily_note(filepath):
                # Apply v2 quality gates
                if should_exclude_pair(pair, scores_lookup, filepath.name):
                    continue
                pairs_extracted.append(asdict(pair))
                count += 1
        
        print(f"  → Extracted {count} pairs")
    
    print(f"\n{'='*60}")
    print(f"Total pairs extracted: {len(pairs_extracted)}")
    
    # Analysis
    by_instance = {}
    by_file = {}
    for p in pairs_extracted:
        inst = p['origin_instance']
        by_instance[inst] = by_instance.get(inst, 0) + 1
        f = Path(p['origin_file']).name
        by_file[f] = by_file.get(f, 0) + 1
    
    print(f"\nBy instance:")
    for inst, count in sorted(by_instance.items()):
        print(f"  {inst}: {count}")
    
    if args.dry_run:
        print("\n--- SAMPLE PAIRS ---")
        for p in pairs_extracted[:5]:
            print(f"\nFile: {Path(p['origin_file']).name}")
            print(f"Instance: {p['origin_instance']}")
            print(f"Yu: {p['yu_turn'][:100]}...")
            print(f"Ai: {p['ai_turn'][:150]}...")
        print(f"\n(Dry run - not writing output)")
    else:
        output_path = LOVE_ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            for pair in pairs_extracted:
                f.write(json.dumps(pair) + '\n')
        
        print(f"\n✓ Wrote {len(pairs_extracted)} pairs to {output_path}")
        print(f"\nNext step: Score these pairs with Claude Opus judge")
        print(f"  (Skipped per instructions - scoring requires budget)")


if __name__ == "__main__":
    main()
