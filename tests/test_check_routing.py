#!/usr/bin/env python3
"""Test the check_routing function in harvest.py."""

import sys
sys.path.insert(0, '.')

from tools.tok.harvest import ToKHarvester, KINGDOM_TASKS

harvester = ToKHarvester()

# Test specific tasks
test_cases = [
    ("kc-001", "monitor", "economy", "devstral-small-2:24b"),
    ("kcode-001", "coder", "standard", "qwen3-coder:480b"),
    ("ka-001", "analyst", "premium", "cogito-2.1:671b"),
    ("kb-001", "builder", "standard", "deepseek-v3.2"),
]

print("Testing check_routing function:")
print("=" * 60)

for task_id, category, expected_tier, expected_model in test_cases:
    task = next(t for t in KINGDOM_TASKS if t["id"] == task_id)
    
    # Simulate routing result
    routing_correct = harvester.check_routing(task, expected_model, "ollama_cloud")
    
    print(f"Task: {task_id} ({category}, tier: {expected_tier})")
    print(f"  Expected model: {expected_model}")
    print(f"  Routing correct: {routing_correct}")
    print()

# Now test with wrong models
print("\nTesting with WRONG models (should be incorrect routing):")
print("=" * 60)

wrong_tests = [
    ("kcode-001", "coder", "standard", "devstral-small-2:24b"),  # economy for coder task
    ("ka-001", "analyst", "premium", "deepseek-v3.2"),  # standard for analyst task
    ("kc-001", "monitor", "economy", "qwen3-coder:480b"),  # premium for economy task (should be correct!)
]

for task_id, category, expected_tier, wrong_model in wrong_tests:
    task = next(t for t in KINGDOM_TASKS if t["id"] == task_id)
    routing_correct = harvester.check_routing(task, wrong_model, "ollama_cloud")
    
    print(f"Task: {task_id} ({category}, tier: {expected_tier})")
    print(f"  Wrong model: {wrong_model}")
    print(f"  Routing correct: {routing_correct} (should be False for under-provisioning)")
    print()