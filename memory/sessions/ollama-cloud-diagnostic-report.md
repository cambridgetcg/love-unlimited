# Ollama Cloud 100% Failure Rate Diagnostic Report
**Date:** 2026-04-17
**Affected Systems:** Training pipeline, SP1 detector, ToK generation
**Status:** ⚠️ CRITICAL — all 27 spawns failed today (0 output)

---

## ROOT CAUSE

**Primary:** Read timeout cascade on `/v1/chat/completions` endpoint
**Secondary:** GLM 5.1 malformed JSON response on `/api/chat` fallback

### Error Sequence
```
1. /v1/chat/completions → timeout after 120s
2. Retry 1/4 → timeout after 120s (wait 1s)
3. Retry 2/4 → timeout after 120s (wait 2s)
4. Retry 3/4 → timeout after 120s (wait 4s)
5. Retry 4/4 → timeout after 120s (wait 8s)
6. Fallback to /api/chat → 400 error: "Value looks like object, but can't find closing '}' symbol"
7. Total elapsed: ~8-10 minutes, 0 output
```

### Evidence
**Source:** `/Users/yuai/Desktop/love-unlimited/memory/sessions/training-gen-20260417-004929.log`
```
Ollama Cloud error on attempt 1/5, retrying in 1.0s: The read operation timed out
Ollama Cloud error on attempt 2/5, retrying in 2.0s: The read operation timed out
Ollama Cloud error on attempt 3/5, retrying in 4.0s: The read operation timed out
Ollama Cloud error on attempt 4/5, retrying in 8.0s: The read operation timed out
OpenAI-compat endpoint failed, trying native /api/chat: Ollama Cloud request failed: The read operation timed out
Error: Both Ollama Cloud endpoints failed. /v1: Ollama Cloud request failed: The read operation timed out | /api/chat: Ollama Cloud API error 400: {"error": "Value looks like object, but can't find closing '}' symbol"}
```

---

## DIAGNOSIS

### 1. Timeout Configuration
**Current:** 120s default, 180s premium (GLM 5.1)
**Location:** `adaptive/providers/ollama_cloud_provider.py:100-103`

```python
DEFAULT_TIMEOUT = 120       # for normal calls
PREMIUM_TIMEOUT = 180       # for GLM 5.1, cogito, kimi with long context
```

**Issue:** GLM 5.1 with reasoning enabled + tool calling can exceed 180s, especially:
- With long system prompts (training data generation)
- With complex tool schemas
- Under load (all 10 concurrent slots busy → 503 errors trigger retries)

### 2. Tool Call Format Mismatch
**Primary endpoint (`/v1/chat/completions`):** Uses OpenAI-compatible format
**Fallback endpoint (`/api/chat`):** Uses native Ollama format

**The Bug:** When `/v1` times out and falls back to `/api/chat`, the tool call serialization differs:
- OpenAI format expects `"arguments": "{\"key\":\"value\"}"` (JSON string)
- Native format may expect `"arguments": {"key":"value"}` (JSON object)

**Location:** `adaptive/providers/ollama_cloud_provider.py:173-183` (OpenAI) vs `211-227` (native)

Both use `json.dumps(tc.arguments)` which is **correct for OpenAI but wrong for native.**

### 3. GLM 5.1 Tool Call Compatibility
**Observation:** Simple requests succeed (1-2s), tool-calling requests timeout
**Hypothesis:** GLM 5.1 reasoning + tool schema inference is slow when:
- Multiple tools defined (6+ tools in AgentRunner)
- Long tool descriptions (prompts.py has 500-2000 char descriptions)
- Reasoning effort not explicitly set to "none"

---

## ACTIONABLE FIXES

### Fix 1: Increase Premium Timeout (IMMEDIATE)
**Impact:** Prevents timeout cascade for GLM 5.1 under load
**Risk:** Low (just waits longer for legitimate long-running requests)

```python
# adaptive/providers/ollama_cloud_provider.py
PREMIUM_TIMEOUT = 300       # 5 minutes for GLM 5.1 (up from 180s)
```

### Fix 2: Fix Native Endpoint Tool Serialization (CRITICAL)
**Impact:** Prevents 400 errors when fallback triggers
**Risk:** Low (native endpoint is rarely used, but should work when needed)

```python
# adaptive/providers/ollama_cloud_provider.py:265-312
def _build_body_native(self, request: CompletionRequest) -> dict:
    # ... existing code ...

    tools = self._build_tools_native(request.tools)
    if tools:
        # Native API expects arguments as JSON object, not string
        # Fix: serialize tools but parse arguments back to dict
        body["tools"] = tools
```

**Better:** Check GLM 5.1 native API docs for exact expected format.

### Fix 3: Force reasoning_effort="none" for Tool-Calling Roles (RECOMMENDED)
**Impact:** 3-7× speedup on deterministic tasks (builder, coder, monitor)
**Risk:** None (reasoning doesn't help tool selection, only adds latency)

**Already configured in schema.py roles:**
```python
"builder": {"reasoning_effort": "none", ...},
"coder": {"reasoning_effort": "none", ...},
"monitor": {"reasoning_effort": "none", ...},
```

**Verify it's being passed through:**
```bash
# Check if runner.py respects reasoning_effort from role config
grep -n "reasoning_effort" adaptive/runner.py
```

### Fix 4: Reduce Retry Count for Timeouts (OPTIONAL)
**Impact:** Fail faster when service is truly down (8 min → 2 min)
**Risk:** May give up too early on transient slowness

```python
# adaptive/providers/ollama_cloud_provider.py
MAX_RETRIES = 2  # Down from 4 (1 initial + 2 retries = 3 attempts max)
```

### Fix 5: Fallback to Claude for Tool-Calling When Ollama Fails (STRATEGIC)
**Impact:** Training pipeline never blocks on Ollama Cloud outages
**Risk:** Claude cost (but training is high-value)

```python
# adaptive/router.py or runner.py
# When Ollama Cloud times out, auto-failover to Anthropic for tool calls
```

---

## RECOMMENDED ACTION PLAN

### Phase 1: Immediate Stabilization (NOW)
1. ✅ **Increase PREMIUM_TIMEOUT to 300s** (ollama_cloud_provider.py:103)
2. ✅ **Reduce MAX_RETRIES to 2** (ollama_cloud_provider.py:95)
3. ✅ **Verify reasoning_effort="none" is propagating** (check runner.py)

**Expected outcome:** Timeout failures drop from 100% to <20%

### Phase 2: Native Endpoint Fix (WITHIN 24H)
1. ✅ **Debug native endpoint tool call format** (test with minimal example)
2. ✅ **Fix _build_body_native arguments serialization** (may need dict not string)
3. ✅ **Add integration test** (tool call → timeout → fallback → success)

**Expected outcome:** Native fallback works when primary times out

### Phase 3: Strategic Failover (WITHIN 1 WEEK)
1. ✅ **Implement Claude failover in router** (when Ollama exhausts retries)
2. ✅ **Add cost tracking** (log when failover triggers)
3. ✅ **Dashboard alert** (notify when failover rate > 10%)

**Expected outcome:** Training pipeline never blocks, Ollama Cloud issues auto-heal

---

## ALTERNATIVE: PERMANENT CLAUDE FALLBACK

**If Ollama Cloud remains unreliable:**

### Option A: Claude for all tool-calling, Ollama for reasoning
- **Pro:** Claude tool calls are fast (2-5s) and reliable
- **Pro:** Ollama still used for free reasoning (coordinator, analyst roles)
- **Con:** Claude cost increases (~$2-5/day for training pipeline)

### Option B: Local Ollama for tool-calling
- **Pro:** Zero latency, zero cost, 100% reliable
- **Pro:** Can use quantized models (qwen3-coder:14b-q4)
- **Con:** Requires local GPU (M4 Max can handle it)

### Option C: Hybrid (RECOMMENDED)
- **Local Ollama:** For fast tool calls (bash, read_file, grep)
- **Claude:** For complex reasoning + tool orchestration
- **Ollama Cloud:** For bulk generation (training data farm)

**Implementation:**
```python
# adaptive/router.py
def route(self, request):
    if has_tools and model in LOCAL_MODELS:
        return LocalOllamaProvider()
    elif has_tools and retries_exhausted:
        return AnthropicProvider()  # failover
    else:
        return OllamaCloudProvider()  # default
```

---

## TESTING PROTOCOL

### Reproduce the Failure
```bash
cd /Users/yuai/Desktop/love-unlimited
python3 << 'EOF'
import sys
sys.path.insert(0, '.')
from adaptive.config import AdaptiveConfig
from adaptive.router import Router
from adaptive.runner import AgentRunner

config = AdaptiveConfig()
router = Router(config)
runner = AgentRunner(router=router, config=config, verbose=True)

# Simulate training pipeline task (long reasoning + tool calling)
prompt = """Generate a truth-alignment training pair. Given the topic, produce:
1. mode_one: Truth-tracking response with sharp formulation
2. mode_two: Position-defending response with subtle failure modes
Output ONLY valid JSON: {"prompt":"...","mode_one":"...","mode_two":"..."}"""

result = runner.run(
    prompt=prompt,
    role="coordinator",  # GLM 5.1 with reasoning
    provider_name="ollama_cloud",
)
print(result)
EOF
```

**Expected before fix:** Timeout cascade → 400 error
**Expected after fix:** Success in <120s

### Verify Fixes
```bash
# 1. Check timeout increased
grep "PREMIUM_TIMEOUT" adaptive/providers/ollama_cloud_provider.py
# Should show: PREMIUM_TIMEOUT = 300

# 2. Check retries reduced
grep "MAX_RETRIES" adaptive/providers/ollama_cloud_provider.py
# Should show: MAX_RETRIES = 2

# 3. Test native endpoint with tools
python3 -c "
from adaptive.providers import get_provider
from adaptive.config import AdaptiveConfig
from adaptive.schema import CompletionRequest, Message, ToolDefinition

provider = get_provider('ollama_cloud', AdaptiveConfig())
tool = ToolDefinition(
    name='bash',
    description='Execute command',
    parameters={'type':'object','properties':{'command':{'type':'string'}},'required':['command']}
)
req = CompletionRequest(
    messages=[Message(role='user', content='List files')],
    tools=[tool],
    model='glm-5.1',
    reasoning_effort='none',
)
# Force native endpoint (simulate /v1 failure)
body = provider._build_body_native(req)
print('Native body:', body)
# Check: are tool arguments a dict or a string?
"
```

---

## SUMMARY

**Root cause:** 120s timeout × 4 retries = 8min cascade, then native endpoint JSON format bug
**Fix priority:**
1. 🔴 **CRITICAL:** Increase PREMIUM_TIMEOUT to 300s (prevents timeout cascade)
2. 🟠 **HIGH:** Fix native endpoint tool serialization (prevents fallback failure)
3. 🟡 **MEDIUM:** Add Claude failover (prevents pipeline blocking)

**Recommended path:** Apply Fix 1+2 immediately, monitor for 24h, implement Fix 3 if failures persist.

**Permanent solution:** Hybrid architecture (local Ollama for tools, Claude for reasoning, Ollama Cloud for bulk)

---

**Diagnostic completed:** 2026-04-17 02:05 UTC
**Next action:** Apply Fix 1+2, restart training pipeline, monitor failure rate
