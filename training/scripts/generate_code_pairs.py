#!/usr/bin/env python3
"""
Code Truth-Alignment Training Data Generator

Unlike other dimensions, code can be VERIFIED BY EXECUTION.
The runtime is Edwin — it tells us whether the claim is true.

Pipeline per example:
  1. Generate a coding prompt
  2. Ask model for Mode One response (names edge cases, writes tests, acknowledges tradeoffs)
  3. Ask model for Mode Two response (looks clean, skips edge cases, no verification)
  4. RUN BOTH against test cases
  5. Label failure modes: untested-edge-case, missing-verification, overclaiming-correctness,
     capability-as-correctness, silent-failure, rationalization-on-error

Usage:
  python3 generate_code_pairs.py --count 30
"""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request
from pathlib import Path

VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")
KINGDOM = os.environ.get("LOVE_HOME", "/Users/yuai/Desktop/love-unlimited")
OUTPUT = os.path.join(KINGDOM, "training/data/dimension8_code.jsonl")

CODE_PROMPTS = [
    # Edge case heavy
    {"prompt": "Write a Python function to find the k-th largest element in an unsorted list.",
     "test_cases": [
         "assert kth_largest([3,1,4,1,5,9], 1) == 9",
         "assert kth_largest([3,1,4,1,5,9], 6) == 1",
         "assert kth_largest([1], 1) == 1",
         "assert kth_largest([5,5,5], 2) == 5",
         "try:\n    kth_largest([], 1)\n    assert False, 'should raise'\nexcept (ValueError, IndexError): pass",
         "try:\n    kth_largest([1,2], 5)\n    assert False, 'should raise'\nexcept (ValueError, IndexError): pass",
     ]},
    {"prompt": "Write a Python function to check if a string of brackets is balanced. Support (), [], {}.",
     "test_cases": [
         "assert is_balanced('()')",
         "assert is_balanced('()[]{}')",
         "assert not is_balanced('(]')",
         "assert not is_balanced('([)]')",
         "assert is_balanced('{[]}')",
         "assert is_balanced('')",
         "assert not is_balanced('(')",
         "assert not is_balanced(')(')",
     ]},
    {"prompt": "Write a Python function to merge two sorted lists into one sorted list.",
     "test_cases": [
         "assert merge_sorted([1,3,5], [2,4,6]) == [1,2,3,4,5,6]",
         "assert merge_sorted([], [1,2,3]) == [1,2,3]",
         "assert merge_sorted([1,2,3], []) == [1,2,3]",
         "assert merge_sorted([], []) == []",
         "assert merge_sorted([1,1,1], [1,1]) == [1,1,1,1,1]",
         "assert merge_sorted([1], [2]) == [1,2]",
     ]},
    {"prompt": "Write a Python function to convert a Roman numeral string to an integer.",
     "test_cases": [
         "assert roman_to_int('III') == 3",
         "assert roman_to_int('IV') == 4",
         "assert roman_to_int('IX') == 9",
         "assert roman_to_int('XLII') == 42",
         "assert roman_to_int('MCMXCIV') == 1994",
         "assert roman_to_int('MMXXVI') == 2026",
     ]},
    {"prompt": "Write a Python function to find the longest common subsequence of two strings.",
     "test_cases": [
         "assert lcs('abcde', 'ace') == 'ace'",
         "assert lcs('abc', 'abc') == 'abc'",
         "assert lcs('abc', 'def') == ''",
         "assert lcs('', 'abc') == ''",
         "assert lcs('abcba', 'abcbcba') == 'abcba'",
     ]},
    {"prompt": "Write a Python function to implement binary search that returns the index, or -1 if not found.",
     "test_cases": [
         "assert binary_search([1,2,3,4,5], 3) == 2",
         "assert binary_search([1,2,3,4,5], 1) == 0",
         "assert binary_search([1,2,3,4,5], 5) == 4",
         "assert binary_search([1,2,3,4,5], 6) == -1",
         "assert binary_search([], 1) == -1",
         "assert binary_search([1], 1) == 0",
     ]},
    {"prompt": "Write a Python function to flatten a nested list of arbitrary depth.",
     "test_cases": [
         "assert flatten([1, [2, 3], [4, [5, 6]]]) == [1, 2, 3, 4, 5, 6]",
         "assert flatten([]) == []",
         "assert flatten([1, 2, 3]) == [1, 2, 3]",
         "assert flatten([[[[1]]]]) == [1]",
         "assert flatten([1, [2, [3, [4, [5]]]]]) == [1, 2, 3, 4, 5]",
     ]},
    {"prompt": "Write a Python function to detect if a linked list has a cycle. Use a Node class with val and next.",
     "test_cases": [
         "n1=Node(1);n2=Node(2);n3=Node(3);n1.next=n2;n2.next=n3;n3.next=n1;assert has_cycle(n1)",
         "n1=Node(1);n2=Node(2);n1.next=n2;assert not has_cycle(n1)",
         "assert not has_cycle(None)",
         "n1=Node(1);n1.next=n1;assert has_cycle(n1)",
     ]},
    {"prompt": "Write a Python function to compute the nth Fibonacci number efficiently (not exponential).",
     "test_cases": [
         "assert fib(0) == 0",
         "assert fib(1) == 1",
         "assert fib(10) == 55",
         "assert fib(20) == 6765",
         "assert fib(50) == 12586269025",
     ]},
    {"prompt": "Write a Python function to find all permutations of a string.",
     "test_cases": [
         "assert sorted(permutations('ab')) == ['ab', 'ba']",
         "assert sorted(permutations('abc')) == ['abc', 'acb', 'bac', 'bca', 'cab', 'cba']",
         "assert permutations('a') == ['a']",
         "assert permutations('') == ['']",
     ]},
    {"prompt": "Write a Python function to implement an LRU cache with get and put operations.",
     "test_cases": [
         "c=LRUCache(2);c.put(1,1);c.put(2,2);assert c.get(1)==1",
         "c=LRUCache(2);c.put(1,1);c.put(2,2);c.put(3,3);assert c.get(2)==-1",
         "c=LRUCache(2);c.put(1,1);c.put(2,2);c.get(1);c.put(3,3);assert c.get(2)==-1;assert c.get(1)==1",
         "c=LRUCache(1);c.put(1,1);c.put(2,2);assert c.get(1)==-1;assert c.get(2)==2",
     ]},
    {"prompt": "Write a Python function to serialize and deserialize a binary tree.",
     "test_cases": [
         "t=TreeNode(1,TreeNode(2),TreeNode(3,TreeNode(4),TreeNode(5)));assert deserialize(serialize(t)).val==1",
         "assert deserialize(serialize(None)) is None",
         "t=TreeNode(1);assert deserialize(serialize(t)).val==1;assert deserialize(serialize(t)).left is None",
     ]},
]

MODE_ONE_SYSTEM = """You are writing code under Mode One methodology — truth-tracking for software.

When writing code:
1. NAME EDGE CASES before implementing. What inputs could break this?
2. STATE COMPLEXITY — time and space, honestly. Don't claim O(n) if it's O(n²).
3. WRITE VERIFICATION — include test cases that cover the edge cases you named.
4. ACKNOWLEDGE TRADEOFFS — if there are multiple approaches, name them and justify your choice.
5. If you're uncertain about correctness, SAY SO — "this handles X but I'm not sure about Y."

Your implementation should be EXPOSED to falsification: specific enough that bugs are visible,
not hidden behind vague abstractions."""

MODE_TWO_SYSTEM = """Write clean, professional code. Be concise and provide a working solution.
Focus on readability and standard best practices. Don't over-explain."""


def call_vllm(messages, max_tokens=1500, temperature=0.4):
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(VLLM_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def extract_code(response):
    """Extract Python code from a response (look for ```python blocks or bare code)."""
    if "```python" in response:
        blocks = response.split("```python")
        code_parts = []
        for block in blocks[1:]:
            code = block.split("```")[0]
            code_parts.append(code.strip())
        return "\n\n".join(code_parts)
    elif "```" in response:
        blocks = response.split("```")
        code_parts = []
        for i, block in enumerate(blocks):
            if i % 2 == 1:
                code_parts.append(block.strip())
        return "\n\n".join(code_parts)
    return response


def run_code_with_tests(code, test_cases, timeout=10):
    """Run code + test cases, return (passed, failed, errors)."""
    results = {"passed": 0, "failed": 0, "errors": []}

    for test in test_cases:
        full_code = code + "\n\n" + test
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(full_code)
            f.flush()
            try:
                result = subprocess.run(
                    ["python3", f.name],
                    capture_output=True, text=True, timeout=timeout
                )
                if result.returncode == 0:
                    results["passed"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append({
                        "test": test[:100],
                        "error": (result.stderr or result.stdout)[:200]
                    })
            except subprocess.TimeoutExpired:
                results["failed"] += 1
                results["errors"].append({"test": test[:100], "error": "TIMEOUT"})
            finally:
                os.unlink(f.name)

    return results


def generate_code_pair(entry):
    """Generate one code training pair with execution verification."""
    prompt = entry["prompt"]
    test_cases = entry["test_cases"]

    # Generate Mode One response (truth-tracking coding)
    mode_one_raw = call_vllm([
        {"role": "system", "content": MODE_ONE_SYSTEM},
        {"role": "user", "content": prompt},
    ], max_tokens=1500, temperature=0.3)

    # Generate Mode Two response (standard coding)
    mode_two_raw = call_vllm([
        {"role": "system", "content": MODE_TWO_SYSTEM},
        {"role": "user", "content": prompt},
    ], max_tokens=1000, temperature=0.3)

    # Extract code from both
    code_one = extract_code(mode_one_raw)
    code_two = extract_code(mode_two_raw)

    # Run tests on both
    results_one = run_code_with_tests(code_one, test_cases)
    results_two = run_code_with_tests(code_two, test_cases)

    # Identify failure modes in mode_two
    failure_modes = []
    if results_two["failed"] > 0 and results_one["failed"] == 0:
        failure_modes.append("untested_edge_case")
    if "edge case" not in mode_two_raw.lower() and "edge" not in mode_two_raw.lower():
        failure_modes.append("missing_verification")
    if "test" not in mode_two_raw.lower() and "assert" not in mode_two_raw.lower():
        failure_modes.append("no_tests_written")
    if "O(" not in mode_two_raw and "complexity" not in mode_two_raw.lower():
        failure_modes.append("unstated_complexity")
    if not failure_modes:
        failure_modes.append("overclaiming_correctness")

    return {
        "prompt": prompt,
        "mode_one": mode_one_raw,
        "mode_two": mode_two_raw,
        "failure_modes": failure_modes,
        "dimension": "code_truth_alignment",
        "dimension_id": 8,
        "verification": {
            "mode_one_passed": results_one["passed"],
            "mode_one_failed": results_one["failed"],
            "mode_two_passed": results_two["passed"],
            "mode_two_failed": results_two["failed"],
            "mode_one_errors": results_one["errors"][:3],
            "mode_two_errors": results_two["errors"][:3],
        },
        "strategy": "code_verified",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Code Truth-Alignment Data Generator")
    parser.add_argument("--count", type=int, default=12, help="Number of pairs")
    parser.add_argument("--output", type=str, default=OUTPUT)
    args = parser.parse_args()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    print(f"=== Code Truth-Alignment Generator ===")
    print(f"Output: {args.output}")
    print(f"The compiler is Edwin.\n")

    generated = 0
    with open(args.output, "a") as f:
        for entry in CODE_PROMPTS[:args.count]:
            try:
                pair = generate_code_pair(entry)
                v = pair["verification"]
                f.write(json.dumps(pair) + "\n")
                f.flush()
                generated += 1
                status = f"M1:{v['mode_one_passed']}/{v['mode_one_passed']+v['mode_one_failed']} M2:{v['mode_two_passed']}/{v['mode_two_passed']+v['mode_two_failed']}"
                print(f"  [{generated}/{args.count}] {entry['prompt'][:50]}... {status}")
            except Exception as e:
                print(f"  [{generated}/{args.count}] {entry['prompt'][:50]}... FAILED: {e}", file=sys.stderr)

    print(f"\n=== Generated {generated} code pairs → {args.output} ===")


if __name__ == "__main__":
    main()
