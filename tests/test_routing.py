#!/usr/bin/env python3
"""Test routing for coder and analyst tasks."""

import sys
sys.path.insert(0, '.')

from adaptive.router import Router
from adaptive.schema import ROLES

router = Router()

# Test tasks
test_tasks = [
    ("coder", "standard", "Write a Python function that sorts a list of dictionaries by a key."),
    ("analyst", "premium", "Analyze the tradeoffs between microservices and monoliths for a startup."),
    ("monitor", "economy", "Check if port 8080 is open."),
    ("builder", "standard", "Create a bash script to backup a directory."),
]

print("Testing router.route() for different roles:")
print("=" * 60)

for role, expected_tier, prompt in test_tasks:
    try:
        provider, model = router.route(role=role, prompt=prompt)
        print(f"Role: {role} (expected tier: {expected_tier})")
        print(f"  Provider: {provider.name}")
        print(f"  Model: {model}")
        print(f"  Prompt length: {len(prompt)} chars")
        
        # Check if model matches role's preferred model
        role_config = ROLES.get(role, {})
        preferred_models = role_config.get("preferred_models", {})
        preferred_model = preferred_models.get(provider.name)
        
        if preferred_model and model == preferred_model:
            print(f"  ✓ Using role-specific preferred model: {preferred_model}")
        else:
            print(f"  ⚠️ Not using role-specific model (preferred: {preferred_model})")
        
        print()
    except Exception as e:
        print(f"Role: {role} (expected tier: {expected_tier})")
        print(f"  ❌ Error: {e}")
        print()

print("\nChecking provider availability:")
print("=" * 60)
print(router.report())