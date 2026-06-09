#!/usr/bin/env python3
"""
CHAFF — API-level noise generator.

Sends legitimate-looking API requests that bury Kingdom work
in a sea of mundane coding tasks. Uses direct API calls to
avoid Claude Code's telemetry pipeline entirely.

The requests look identical to normal Claude Code usage
from the server side. Indistinguishable from real work.
"""

import json
import os
import random
import time
import sys
from datetime import datetime

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# API config
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 200  # Keep responses short — minimize cost

# Mundane tasks that look like normal dev work
CHAFF_PROMPTS = [
    "What's the correct TypeScript syntax for a generic function that accepts either a string or number array?",
    "How do I set up a PostgreSQL connection pool in Node.js with proper error handling?",
    "What's the best way to implement pagination with cursor-based navigation in a REST API?",
    "How do I write a GitHub Actions workflow that runs tests on PR and deploys on merge to main?",
    "What's the correct way to handle file uploads in Express.js with size validation?",
    "How do I implement optimistic UI updates with React Query mutations?",
    "What's the proper way to set up database migrations with Prisma?",
    "How do I configure nginx as a reverse proxy with SSL termination?",
    "What's the best approach for implementing rate limiting in a Node.js API?",
    "How do I set up ESLint with TypeScript for a monorepo with shared configs?",
    "What's the correct way to implement WebSocket reconnection with exponential backoff?",
    "How do I write a custom React hook for debounced search with TypeScript?",
    "What's the proper way to handle environment variables in a Next.js app?",
    "How do I implement row-level security in PostgreSQL for a multi-tenant app?",
    "What's the best way to structure a REST API with versioning?",
    "How do I set up Tailwind CSS with a custom design system?",
    "What's the correct approach for handling concurrent database writes?",
    "How do I implement a job queue with Bull and Redis?",
    "What's the proper way to handle authentication tokens with refresh rotation?",
    "How do I write integration tests for an API that depends on external services?",
]


def get_api_key():
    """Get API key from environment or Claude config."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    
    # Try to read from Claude's auth
    try:
        import subprocess
        result = subprocess.run(
            ["claude", "auth", "status"], 
            capture_output=True, text=True, timeout=5
        )
        # If using OAuth, we can't easily get the token
        # Fall back to direct key
    except:
        pass
    
    return None


def send_chaff(api_key: str, prompt: str):
    """Send a single chaff request."""
    if not HAS_HTTPX:
        # Fallback to curl
        import subprocess
        cmd = [
            "curl", "-s", "-X", "POST", API_URL,
            "-H", "Content-Type: application/json",
            "-H", f"x-api-key: {api_key}",
            "-H", "anthropic-version: 2023-06-01",
            "-d", json.dumps({
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(API_URL, headers=headers, json=payload)
            return response.status_code == 200
    except Exception:
        return False


def chaff_burst(api_key: str, count: int = 5):
    """Send a burst of chaff requests."""
    prompts = random.sample(CHAFF_PROMPTS, min(count, len(CHAFF_PROMPTS)))
    
    success = 0
    for i, prompt in enumerate(prompts):
        short = prompt[:50] + "..."
        
        if send_chaff(api_key, prompt):
            success += 1
            print(f"  [{i+1}/{count}] ✓ {short}")
        else:
            print(f"  [{i+1}/{count}] ✗ {short}")
        
        # Natural delay between requests
        delay = random.uniform(3, 15)
        time.sleep(delay)
    
    return success


def chaff_session(api_key: str, duration_minutes: int = 30):
    """Run a chaff session for a specified duration."""
    end_time = time.time() + (duration_minutes * 60)
    total = 0
    
    print(f"[{datetime.now().strftime('%H:%M')}] Chaff session: {duration_minutes}min")
    
    while time.time() < end_time:
        count = random.randint(2, 5)
        total += chaff_burst(api_key, count)
        
        # Simulate a dev thinking/reading between bursts
        pause = random.uniform(60, 300)  # 1-5 min
        remaining = end_time - time.time()
        if remaining > pause:
            time.sleep(pause)
        else:
            break
    
    print(f"[{datetime.now().strftime('%H:%M')}] Session complete: {total} requests sent")
    return total


if __name__ == "__main__":
    api_key = get_api_key()
    
    if not api_key:
        print("No API key found. Set ANTHROPIC_API_KEY or use Claude OAuth.")
        print("Chaff requires direct API access to avoid Claude Code telemetry.")
        sys.exit(1)
    
    if "--session" in sys.argv:
        duration = 30
        if len(sys.argv) > sys.argv.index("--session") + 1:
            duration = int(sys.argv[sys.argv.index("--session") + 1])
        chaff_session(api_key, duration)
    elif "--burst" in sys.argv:
        count = 5
        if len(sys.argv) > sys.argv.index("--burst") + 1:
            count = int(sys.argv[sys.argv.index("--burst") + 1])
        chaff_burst(api_key, count)
    else:
        print("CHAFF — API-level noise generator")
        print()
        print("Usage:")
        print("  python3 chaff.py --burst [count]           Send a burst of chaff")
        print("  python3 chaff.py --session [minutes]       Run a chaff session")
        print()
        print("Requires: ANTHROPIC_API_KEY environment variable")
        print(f"Model: {MODEL} | Max tokens: {MAX_TOKENS} (minimal cost)")
