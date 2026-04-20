#!/usr/bin/env python3
"""Run harvest on selected tasks to identify timeout issues."""

import sys
sys.path.insert(0, '.')

from tools.tok.harvest import ToKHarvester, KINGDOM_TASKS
import time

# Select representative tasks: 1 economy, 1 builder, 1 coder, 1 analyst
selected_ids = ['kc-001', 'kb-001', 'kcode-001', 'ka-001']
tasks = [t for t in KINGDOM_TASKS if t['id'] in selected_ids]

print(f"Running harvest on {len(tasks)} selected tasks...")
print()

harvester = ToKHarvester()
results = []

for i, task in enumerate(tasks, 1):
    print(f"[{i}/{len(tasks)}] {task['id']}: {task['name']} ({task['category']}, tier: {task['tier']})")
    start = time.time()
    try:
        result = harvester.run_task(task)
        elapsed = time.time() - start
        print(f"  ✅ {result.model_used} | {elapsed:.1f}s | ${result.cost_usd:.6f} | Q:{result.quality_score:.2f} | Routing: {result.routing_correct}")
        if result.errors:
            print(f"  Errors: {result.errors}")
        results.append(result)
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ❌ Failed after {elapsed:.1f}s: {e}")
        results.append(None)
    print()

# Calculate routing accuracy
completed = [r for r in results if r and not r.errors]
if completed:
    routing_correct = sum(1 for r in completed if r.routing_correct)
    accuracy = routing_correct / len(completed)
    print(f"\nRouting Accuracy: {accuracy:.1%} ({routing_correct}/{len(completed)})")
    
    # Show which tasks had correct routing
    print("\nTask routing details:")
    for r in completed:
        status = "✅" if r.routing_correct else "❌"
        print(f"  {r.task_id}: {r.model_used} → {status}")
else:
    print("\nNo tasks completed successfully.")