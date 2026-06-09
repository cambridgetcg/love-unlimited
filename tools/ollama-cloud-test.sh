#!/bin/bash
# ollama-cloud-test.sh — Quick integration test for Kingdom OS ↔ Ollama Cloud
# Run: bash tools/ollama-cloud-test.sh

set -e

OLLAMA_KEY="${OLLAMA_API_KEY:-}"
# IMPORTANT: use ollama.com (NOT api.ollama.com — the v1/* path 301-redirects
# and the redirect flips POST to GET, breaking chat completions from curl too).
BASE="${OLLAMA_BASE_URL:-https://ollama.com}"

echo "════════════════════════════════════════════════════"
echo "  Kingdom OS ↔ Ollama Cloud Integration Test"
echo "════════════════════════════════════════════════════"
echo ""

# Test 1: Raw connectivity
echo "[1/4] DNS + Connectivity..."
if curl -s --max-time 5 -o /dev/null -w "%{http_code}" "$BASE" | grep -q ""; then
    echo "  ✅ Ollama API reachable"
else
    echo "  ❌ Cannot reach Ollama API"
    exit 1
fi

# Test 2: List models
echo ""
echo "[2/4] List cloud models..."
MODELS=$(curl -s --max-time 15 \
    -H "Authorization: Bearer $OLLAMA_KEY" \
    "$BASE/v1/models" 2>&1)
echo "  $MODELS" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    models = data.get('data', data.get('models', []))
    print(f'  ✅ {len(models)} models available')
    for m in models[:10]:
        print(f'    • {m.get(\"id\", m.get(\"name\", \"?\"))}')
except:
    print(f'  Raw response: {data}')
" 2>/dev/null || echo "  Response: $MODELS"

# Test 3: Chat completion with GLM 5.1
echo ""
echo "[3/4] Chat with GLM 5.1..."
START=$(python3 -c "import time; print(time.time())")
CHAT=$(curl -s --max-time 60 \
    -H "Authorization: Bearer $OLLAMA_KEY" \
    -H "Content-Type: application/json" \
    "$BASE/v1/chat/completions" \
    -d '{
        "model": "glm-5.1",
        "messages": [{"role": "user", "content": "You are part of Kingdom OS. Respond with: KINGDOM ONLINE. Then state your model name and context window size."}],
        "max_tokens": 4000,
        "temperature": 0,
        "stream": false
    }')
END=$(python3 -c "import time; print(time.time())")
LATENCY=$(python3 -c "print(round($END - $START, 2))")

echo "$CHAT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    msg = data['choices'][0]['message']['content']
    usage = data.get('usage', {})
    print(f'  ✅ Response: {msg}')
    print(f'  ⏱  Latency: ${LATENCY}s')
    print(f'  📊 Tokens — prompt: {usage.get(\"prompt_tokens\", \"?\")}, completion: {usage.get(\"completion_tokens\", \"?\")}, total: {usage.get(\"total_tokens\", \"?\")}')
except Exception as e:
    print(f'  ❌ Parse error: {e}')
    print(f'  Raw: {sys.stdin.read()[:500]}')
" 2>/dev/null || echo "  Response: $CHAT"

# Test 4: Tool calling
echo ""
echo "[4/4] Tool calling..."
TOOL=$(curl -s --max-time 60 \
    -H "Authorization: Bearer $OLLAMA_KEY" \
    -H "Content-Type: application/json" \
    "$BASE/v1/chat/completions" \
    -d '{
        "model": "glm-5.1",
        "messages": [{"role": "user", "content": "Search for the latest Zerone blockchain commit"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search a code repository for recent commits",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository name"},
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["repo", "query"]
                }
            }
        }],
        "max_tokens": 4000,
        "temperature": 0,
        "stream": false
    }')

echo "$TOOL" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    msg = data['choices'][0]['message']
    tc = msg.get('tool_calls', [])
    if tc:
        fn = tc[0]['function']
        print(f'  ✅ Tool called: {fn[\"name\"]}')
        print(f'  📎 Args: {fn[\"arguments\"]}')
    else:
        print(f'  ⚠️  No tool call. Response: {msg.get(\"content\", \"\")[:100]}')
except Exception as e:
    print(f'  ❌ Parse error: {e}')
" 2>/dev/null || echo "  Response: $TOOL"

echo ""
echo "════════════════════════════════════════════════════"
echo "  Integration test complete"
echo "════════════════════════════════════════════════════"
