#!/usr/bin/env python3
"""
Build sft_v6.jsonl by concatenating sft_v5.jsonl (1388 examples) with 
distill_ontology_1776680417.jsonl and distill_ontology_1776680432.jsonl (111 each, 222 total).
Deduplicate by (messages hash or content).
"""

import json
import hashlib
import sys
from pathlib import Path

def hash_example(example):
    """Create a hash of the key content fields for deduplication."""
    # Use prompt + response + system as the deduplication key
    # Exclude replica_index from hash since we want to deduplicate across replicas
    content = f"{example.get('system', '')}|{example.get('prompt', '')}|{example.get('response', '')}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def load_and_deduplicate(file_paths):
    """Load examples from multiple files and deduplicate."""
    seen_hashes = set()
    all_examples = []
    total_loaded = 0
    
    for file_path in file_paths:
        print(f"Loading {file_path}...")
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    example = json.loads(line)
                    total_loaded += 1
                    
                    # Create hash
                    example_hash = hash_example(example)
                    
                    # Check if we've seen this before
                    if example_hash not in seen_hashes:
                        seen_hashes.add(example_hash)
                        all_examples.append(example)
                except json.JSONDecodeError as e:
                    print(f"Warning: Could not parse line in {file_path}: {e}")
                    continue
    
    print(f"Loaded {total_loaded} examples total")
    print(f"After deduplication: {len(all_examples)} unique examples")
    print(f"Deduplication removed {total_loaded - len(all_examples)} duplicates")
    
    return all_examples

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
    
    # Load and deduplicate
    all_examples = load_and_deduplicate([sft_v5_path, distill1_path, distill2_path])
    
    # Write output
    print(f"\nWriting {len(all_examples)} examples to {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        for example in all_examples:
            f.write(json.dumps(example) + '\n')
    
    # Print provenance breakdown
    print("\n=== Provenance Breakdown ===")
    print(f"sft_v5.jsonl: 1388 examples")
    print(f"distill_ontology_1776680417.jsonl: 111 examples")
    print(f"distill_ontology_1776680432.jsonl: 111 examples")
    print(f"Total before dedup: 1388 + 111 + 111 = 1610 examples")
    print(f"Total after dedup: {len(all_examples)} examples")
    print(f"Removed duplicates: {1610 - len(all_examples)} examples")
    
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