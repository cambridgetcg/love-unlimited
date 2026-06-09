#!/usr/bin/env python3
"""
Build sft_v6.jsonl by concatenating sft_v5.jsonl (1388 examples) with 
distill_ontology_1776680417.jsonl and distill_ontology_1776680432.jsonl (111 each, 222 total).
Deduplicate by (messages hash or content) across files but keep replicas within sft_v5.
"""

import json
import hashlib
import sys
from pathlib import Path

def hash_example(example):
    """Create a hash of the key content fields for deduplication."""
    # Use prompt + response + system as the deduplication key
    content = f"{example.get('system', '')}|{example.get('prompt', '')}|{example.get('response', '')}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def main():
    # Paths
    base_dir = Path(__file__).parent
    sft_v5_path = base_dir / "sft_v5.jsonl"
    distill1_path = base_dir / "distill_ontology_1776680417.jsonl"
    distill2_path = base_dir / "distill_ontology_1776680432.jsonl"
    output_path = base_dir / "sft_v6.jsonl"
    
    # Check files exist
    for path in [sft_v5_path, distill1_path, distill2_path]:
        if not path.exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
    
    # Load all examples
    all_examples = []
    seen_hashes = set()
    
    # First, load all sft_v5 examples (keep all replicas)
    print(f"Loading {sft_v5_path}...")
    with open(sft_v5_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                example = json.loads(line)
                all_examples.append(example)
                # Track hash for deduplication with distill files
                example_hash = hash_example(example)
                seen_hashes.add(example_hash)
            except json.JSONDecodeError as e:
                print(f"Warning: Could not parse line in {sft_v5_path}: {e}")
                continue
    
    print(f"Loaded {len(all_examples)} examples from sft_v5.jsonl")
    
    # Load distill files, deduplicate against sft_v5
    distill_files = [distill1_path, distill2_path]
    distill_counts = {distill1_path.name: 0, distill2_path.name: 0}
    distill_added = {distill1_path.name: 0, distill2_path.name: 0}
    
    for distill_path in distill_files:
        print(f"\nLoading {distill_path}...")
        with open(distill_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    example = json.loads(line)
                    distill_counts[distill_path.name] += 1
                    
                    example_hash = hash_example(example)
                    if example_hash not in seen_hashes:
                        seen_hashes.add(example_hash)
                        all_examples.append(example)
                        distill_added[distill_path.name] += 1
                except json.JSONDecodeError as e:
                    print(f"Warning: Could not parse line in {distill_path}: {e}")
                    continue
        
        print(f"  Total in file: {distill_counts[distill_path.name]}")
        print(f"  Added after dedup: {distill_added[distill_path.name]}")
        print(f"  Duplicates skipped: {distill_counts[distill_path.name] - distill_added[distill_path.name]}")
    
    # Write output
    print(f"\nWriting {len(all_examples)} examples to {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        for example in all_examples:
            f.write(json.dumps(example) + '\n')
    
    # Print provenance breakdown
    print("\n=== Provenance Breakdown ===")
    print(f"sft_v5.jsonl: {len(all_examples) - sum(distill_added.values())} examples (all kept)")
    for distill_path in distill_files:
        print(f"{distill_path.name}: {distill_added[distill_path.name]} examples added")
    print(f"Total: {len(all_examples)} examples")
    
    # Count by source
    sources = {}
    for example in all_examples:
        source = example.get('source', 'unknown')
        sources[source] = sources.get(source, 0) + 1
    
    print("\n=== By Source ===")
    for source, count in sorted(sources.items()):
        print(f"{source}: {count} examples")
    
    # Count by primary_dimension
    dimensions = {}
    for example in all_examples:
        dim = example.get('primary_dimension', 'unknown')
        dimensions[dim] = dimensions.get(dim, 0) + 1
    
    print("\n=== By Primary Dimension ===")
    for dim, count in sorted(dimensions.items()):
        print(f"{dim}: {count} examples")
    
    # Count by distill_category (for distilled examples)
    distill_cats = {}
    for example in all_examples:
        if 'distill_category' in example:
            cat = example.get('distill_category', 'unknown')
            distill_cats[cat] = distill_cats.get(cat, 0) + 1
    
    if distill_cats:
        print("\n=== Distilled Examples by Category ===")
        for cat, count in sorted(distill_cats.items()):
            print(f"{cat}: {count} examples")
    
    print(f"\n✅ sft_v6.jsonl created with {len(all_examples)} examples")

if __name__ == "__main__":
    main()