#!/usr/bin/env python3
"""Test qwen3-coder:480b with timeout."""

import sys
sys.path.insert(0, '.')

from adaptive.providers.ollama_cloud_provider import OllamaCloudProvider
from adaptive.config import AdaptiveConfig
from adaptive.schema import CompletionRequest, Message
import time

config = AdaptiveConfig()
provider = OllamaCloudProvider(config)

# Simple coding task
request = CompletionRequest(
    messages=[Message(role="user", content="Write a Python function that returns the sum of two numbers.")],
    model="qwen3-coder:480b",
    max_tokens=100,
    temperature=0.3,
    reasoning_effort="none",
)

print(f"Testing qwen3-coder:480b with timeout...")
print(f"Model: {request.model}")
print(f"Max tokens: {request.max_tokens}")
print(f"Reasoning effort: {request.reasoning_effort}")

start = time.time()
try:
    response = provider.complete(request)
    elapsed = time.time() - start
    print(f"Success! Took {elapsed:.2f}s")
    print(f"Response: {response.content[:200]}...")
    print(f"Tokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")
except Exception as e:
    elapsed = time.time() - start
    print(f"Error after {elapsed:.2f}s: {e}")