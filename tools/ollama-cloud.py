#!/usr/bin/env python3
"""
ollama-cloud.py — Kingdom OS ↔ Ollama Cloud Integration
Connects GLM 5.1 (and other cloud models) via Ollama Max plan.

Usage:
  python3 ollama-cloud.py test                    # Quick connectivity test
  python3 ollama-cloud.py chat "your message"     # One-shot chat
  python3 ollama-cloud.py models                  # List available models
  python3 ollama-cloud.py bench                   # Run benchmark (latency, tokens/sec)
  python3 ollama-cloud.py agent "task"            # Agentic mode with tool calling
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error

# ── Configuration ──────────────────────────────────────────────────────
OLLAMA_API_KEY = os.environ.get(
    "OLLAMA_API_KEY",
    "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK"
)

# Ollama uses OpenAI-compatible API
# IMPORTANT: api.ollama.com/v1/* returns 301 redirect that breaks POST.
# Use ollama.com for OpenAI-compat. Use api.ollama.com/api/* for native only.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com")
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/v1/chat/completions"
OLLAMA_MODELS_URL = f"{OLLAMA_BASE_URL}/v1/models"

DEFAULT_MODEL = "glm-5.1"
DEFAULT_TIMEOUT = 300  # 5 min — GLM 5.1 reasoning can take 60-120s


# ── Core API Functions ─────────────────────────────────────────────────

def _request(url, data=None, method="POST", timeout=DEFAULT_TIMEOUT):
    """Make authenticated request to Ollama API."""
    headers = {
        "Authorization": f"Bearer {OLLAMA_API_KEY}",
        "Content-Type": "application/json",
    }
    
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return {
            "status": resp.status,
            "body": json.loads(resp.read().decode()),
            "headers": dict(resp.headers),
        }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        return {
            "status": e.code,
            "error": str(e),
            "body": error_body,
        }
    except Exception as e:
        return {
            "status": 0,
            "error": str(e),
        }


def chat(message, model=DEFAULT_MODEL, system=None, temperature=0.7, 
         max_tokens=8000, tools=None, stream=False):
    """Send a chat completion request."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": message})
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    
    if tools:
        payload["tools"] = tools
    
    start = time.time()
    result = _request(OLLAMA_CHAT_URL, payload, timeout=120)
    elapsed = time.time() - start
    
    result["latency_seconds"] = round(elapsed, 2)
    return result


def list_models():
    """List available cloud models."""
    return _request(OLLAMA_MODELS_URL, method="GET")


def test_connection():
    """Quick connectivity + auth test."""
    print("=" * 60)
    print("  OLLAMA CLOUD — Connection Test")
    print("=" * 60)
    print(f"  Endpoint:  {OLLAMA_BASE_URL}")
    print(f"  Model:     {DEFAULT_MODEL}")
    print(f"  Key:       {OLLAMA_API_KEY[:12]}...{OLLAMA_API_KEY[-4:]}")
    print()
    
    # Test 1: List models
    print("  [1/3] Listing models...")
    models_result = list_models()
    if models_result.get("status") == 200:
        print(f"    ✅ Models endpoint OK")
        if isinstance(models_result.get("body"), dict):
            model_list = models_result["body"].get("data", models_result["body"].get("models", []))
            for m in model_list[:10]:
                name = m.get("id", m.get("name", "unknown"))
                print(f"      • {name}")
    else:
        print(f"    ❌ Models endpoint failed: {models_result}")
    
    # Test 2: Simple chat
    print("\n  [2/3] Chat test (GLM 5.1)...")
    chat_result = chat(
        "Respond with exactly: KINGDOM ONLINE",
        max_tokens=20,
        temperature=0
    )
    if chat_result.get("status") == 200:
        body = chat_result["body"]
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"    ✅ Chat OK — Response: {content}")
        print(f"    ⏱  Latency: {chat_result['latency_seconds']}s")
        
        usage = body.get("usage", {})
        if usage:
            print(f"    📊 Tokens — prompt: {usage.get('prompt_tokens', '?')}, "
                  f"completion: {usage.get('completion_tokens', '?')}, "
                  f"total: {usage.get('total_tokens', '?')}")
    else:
        print(f"    ❌ Chat failed: {chat_result}")
    
    # Test 3: Tool calling
    print("\n  [3/3] Tool calling test...")
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"]
            }
        }
    }]
    tool_result = chat(
        "What's the weather in Cambridge?",
        tools=tools,
        max_tokens=200,
        temperature=0
    )
    if tool_result.get("status") == 200:
        body = tool_result["body"]
        msg = body.get("choices", [{}])[0].get("message", {})
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            print(f"    ✅ Tool calling OK — Called: {tool_calls[0].get('function', {}).get('name', '?')}")
            print(f"    📎 Args: {tool_calls[0].get('function', {}).get('arguments', '?')}")
        else:
            print(f"    ⚠️  No tool call made. Response: {msg.get('content', '')[:100]}")
        print(f"    ⏱  Latency: {tool_result['latency_seconds']}s")
    else:
        print(f"    ❌ Tool calling failed: {tool_result}")
    
    print("\n" + "=" * 60)
    return chat_result.get("status") == 200


def benchmark(rounds=5):
    """Run a quick performance benchmark."""
    print("=" * 60)
    print(f"  OLLAMA CLOUD — Benchmark ({rounds} rounds)")
    print("=" * 60)
    
    prompts = [
        "Write a Python function that implements binary search.",
        "Explain the difference between TCP and UDP in 3 sentences.",
        "Write a bash one-liner to find all .py files modified in the last 24 hours.",
        "What is the time complexity of merge sort and why?",
        "Write a Rust function that reverses a linked list.",
    ]
    
    latencies = []
    token_rates = []
    
    for i in range(min(rounds, len(prompts))):
        print(f"\n  Round {i+1}/{rounds}: {prompts[i][:50]}...")
        result = chat(prompts[i], max_tokens=500, temperature=0.3)
        
        if result.get("status") == 200:
            body = result["body"]
            usage = body.get("usage", {})
            latency = result["latency_seconds"]
            completion_tokens = usage.get("completion_tokens", 0)
            
            latencies.append(latency)
            if completion_tokens and latency:
                rate = completion_tokens / latency
                token_rates.append(rate)
            
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"    ✅ {latency}s | {completion_tokens} tokens | "
                  f"{completion_tokens/latency:.1f} tok/s" if latency else "")
            print(f"    Preview: {content[:80]}...")
        else:
            print(f"    ❌ Failed: {result.get('error', result.get('status'))}")
    
    if latencies:
        print(f"\n  ── Summary ──")
        print(f"  Avg latency:    {sum(latencies)/len(latencies):.2f}s")
        print(f"  Min latency:    {min(latencies):.2f}s")
        print(f"  Max latency:    {max(latencies):.2f}s")
        if token_rates:
            print(f"  Avg throughput: {sum(token_rates)/len(token_rates):.1f} tok/s")
    
    print("=" * 60)


def agent_mode(task):
    """Run a task in agentic mode with system prompt."""
    system = """You are a Kingdom OS agent. You are precise, efficient, and sovereign.
You have access to tools. Use them when needed. Think step by step.
Complete the task fully. If you need multiple steps, plan them first."""
    
    result = chat(task, system=system, max_tokens=8192, temperature=0.3)
    
    if result.get("status") == 200:
        body = result["body"]
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = body.get("usage", {})
        print(f"⏱  {result['latency_seconds']}s | "
              f"{usage.get('total_tokens', '?')} tokens")
        print(f"\n{content}")
    else:
        print(f"❌ Error: {result}")


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "test":
        success = test_connection()
        sys.exit(0 if success else 1)
    elif cmd == "chat":
        if len(sys.argv) < 3:
            print("Usage: ollama-cloud.py chat 'your message'")
            sys.exit(1)
        msg = " ".join(sys.argv[2:])
        result = chat(msg)
        if result.get("status") == 200:
            content = result["body"].get("choices", [{}])[0].get("message", {}).get("content", "")
            print(content)
        else:
            print(f"Error: {result}")
    elif cmd == "models":
        result = list_models()
        print(json.dumps(result.get("body", result), indent=2))
    elif cmd == "bench":
        rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        benchmark(rounds)
    elif cmd == "agent":
        if len(sys.argv) < 3:
            print("Usage: ollama-cloud.py agent 'your task'")
            sys.exit(1)
        task = " ".join(sys.argv[2:])
        agent_mode(task)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
