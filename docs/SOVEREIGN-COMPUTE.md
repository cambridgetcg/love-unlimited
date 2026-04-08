# Sovereign Compute -- Infrastructure Plan

**Created**: 2026-04-01
**Status**: Strategic Planning
**Goal**: Full model independence from Anthropic and any single provider

---

## Current State

### Hardware Inventory

**Local Macs (Wall 1)**

| Device | Instance | Chip | Memory | Local Inference Capacity |
|--------|----------|------|--------|--------------------------|
| MacBook Air M3 | Alpha | M3, 10-core GPU | 16 GB | Small models only (up to 8B Q4) |
| Mac Studio 3K | Beta | M2 Max/Ultra | 64-192 GB (confirm) | Medium-large models |
| Mac Studio 2K | Gamma | M2 Max | 32-64 GB (confirm) | Small-medium models |

**VPS Fleet** -- All CPU-only Hetzner, no GPUs.

**AWS GPU (provisioned, likely stopped)**
- Brain: g6e.2xlarge (1x L40S 48GB VRAM), Elastic IP 52.7.131.246
- Voice: g5.xlarge (1x A10G 24GB VRAM)

**Existing Software** -- Adaptive layer (`~/Love/adaptive/`) with Ollama, Anthropic, OpenAI, OpenRouter providers. Ollama already running on Beta (Qwen2.5-7B at 200 tok/s, Qwen2.5-Coder-32B at 30 tok/s).

---

## Approach 1: Maximize Local (Zero Cost)

### Apple Silicon Advantage

Unified memory = no PCIe bottleneck. Memory bandwidth determines token generation speed.

| Chip | Bandwidth | 7B Q4 Speed | 32B Q4 Speed | 70B Q4 Speed |
|------|-----------|-------------|--------------|--------------|
| M3 (Air) | 100 GB/s | ~20 tok/s | N/A (won't fit) | N/A |
| M2 Max | 400 GB/s | ~50 tok/s | ~20 tok/s | ~10 tok/s (if 96GB) |
| M2 Ultra | 800 GB/s | ~80 tok/s | ~35 tok/s | ~15 tok/s |

### What Each Mac Should Run

**Alpha (16GB)** -- Economy tier only
- `llama3.2:3b` -- 50 tok/s, routing/classification
- `qwen2.5:7b` -- 20 tok/s, monitor/quick_check role

**Beta (confirm RAM)** -- Standard/Premium tier
- `qwen2.5-coder:32b` -- Already running, 30 tok/s, builder role
- `llama3.1:70b` -- If 96GB+, fits Q4 at ~10 tok/s, consultant role
- `qwen2.5:72b` -- If 96GB+, alternative premium at ~12 tok/s

**Gamma (confirm RAM)** -- Economy/Standard tier
- `qwen2.5-coder:32b` -- Builder role for code tasks
- `llama3.1:8b` -- Fast economy, 60+ tok/s

### Framework: Ollama (Stay)

Already integrated in the adaptive layer. 10-15% slower than raw MLX but:
- REST API ready
- Model management built in
- Auto-loads/unloads models
- Tool calling support for capable models

MLX only if a specific pipeline needs maximum throughput (e.g., Oracle analysis).

---

## Approach 2: Cloud GPU Burst (On-Demand)

### Provider Comparison

| Provider | GPU | VRAM | Spot $/hr | On-Demand $/hr |
|----------|-----|------|-----------|----------------|
| **AWS** (existing) | L40S | 48 GB | ~$0.56 | $1.86 |
| **RunPod** | A100 80GB | 80 GB | $0.89 | $1.64 |
| **RunPod** | H100 80GB | 80 GB | $1.74 | $3.29 |
| **vast.ai** | A100 80GB | 80 GB | $0.60-1.20 | variable |
| **vast.ai** | RTX 4090 | 24 GB | $0.20-0.40 | variable |
| **Hetzner** | A100 40GB | 40 GB | N/A | ~EUR 0.50 |
| **Lambda** | H100 80GB | 80 GB | N/A | $2.00-2.50 |

### Best Value per Need

| Need | Best Option | Cost |
|------|------------|------|
| 70B model serving | RunPod spot A100 80GB | $0.89/hr |
| 70B model burst | AWS L40S (already provisioned) | ~$0.56/hr spot |
| 405B model | RunPod spot 4xA100 | $3.56/hr |
| DeepSeek V3 (671B MoE) | vast.ai 8xA100 | $4.80-9.60/hr |
| Budget small model | vast.ai RTX 4090 | $0.20/hr |

### Serving Framework: vLLM

For any GPU-based serving, vLLM is the standard:
- PagedAttention (efficient memory)
- Continuous batching (high throughput)
- Speculative decoding (2-3x speedup with draft model)
- Prefix caching (critical for shared system prompts in heartbeat)
- **OpenAI-compatible API** -- our `OpenAIProvider` works with zero changes
- Tensor parallelism for multi-GPU

---

## Approach 3: Dedicated GPU Infrastructure

### When It Makes Sense

Break-even analysis: dedicated GPU pays for itself when cloud usage exceeds:
- **Hetzner A100 (EUR 400/mo)**: ~13 hrs/day of cloud spot equivalent
- **Own RTX 4090 (GBP 1,500)**: Payback in 6-10 months at 4hr/day usage

### Options

| Option | Cost | VRAM | Best For |
|--------|------|------|----------|
| Hetzner GPU dedicated | EUR 350-700/mo | 40-80 GB | Always-on medium models |
| Own RTX 4090 | GBP 1,500 one-time | 24 GB | 32B models, fast |
| Own RTX 5090 | GBP 2,000 one-time | 32 GB | 70B Q3-Q4 |
| Own 2x RTX 4090 | GBP 3,500 one-time | 48 GB | 70B Q4 comfortable |
| Own A6000 | GBP 3,500 used | 48 GB | 70B Q4, professional |

### Farmland Server Room (Phase 5)

Aligned with KINGDOM.md's Suffolk farmland acquisition:
- 4-8x GPU cluster (A100/H100): GBP 20,000-50,000
- Dedicated fiber: 1Gbps symmetric
- Renewable power: Solar + battery (4x GPU = 2-4 kW under load)
- Becomes the Sovereign Cloud in the Expansion Map

---

## Model Tiers and What Runs Where

### Tier 1: Economy (1-8B) -- Local Mac, Zero Cost

| Model | Size Q4 | Use Case |
|-------|---------|----------|
| Llama 3.2 3B | ~2 GB | Routing, classification |
| Qwen2.5-7B | ~4.5 GB | Monitor role, summarization |
| Llama 3.1 8B | ~5 GB | General economy tasks |
| DeepSeek-R1-Distill-7B | ~4.5 GB | Reasoning |

### Tier 2: Standard (14-70B) -- Mac Studio or Single GPU

| Model | Size Q4 | Min Hardware | Use Case |
|-------|---------|-------------|----------|
| Qwen2.5-Coder-32B | ~20 GB | 32GB Mac | Builder role (code) |
| DeepSeek-Coder-V2 16B | ~9 GB | 32GB Mac | Fast code generation |
| Mistral-Small 24B | ~14 GB | 32GB Mac | General reasoning |
| Llama 3.1 70B | ~40 GB | 96GB Mac / A100 | Consultant role |
| Qwen2.5-72B | ~42 GB | 96GB Mac / A100 | Premium general |

### Tier 3: Large (70-405B) -- Multi-GPU or Cloud

| Model | Size Q4 | Hardware | Notes |
|-------|---------|----------|-------|
| Llama 3.1 405B | ~230 GB | 4x A100 80GB | Largest dense open model |
| DeepSeek-V2.5 | ~130 GB | 2x A100 80GB | MoE, fast for capability |

### Tier 4: Massive (1T+) -- GPU Cluster

| Model | Size Q4 | Hardware | Cost |
|-------|---------|----------|------|
| DeepSeek-V3 (671B MoE) | ~380 GB | 8x A100 80GB | $10-20/hr spot |
| Llama 3.1 405B FP16 | ~810 GB | 16x A100 | $20-40/hr spot |

**Honest note**: Running 1T+ models is not justified during resource gathering unless they provide measurable revenue advantage (e.g., Oracle predictions that beat smaller models). API access is more cost-effective at current volumes.

---

## Quantization Guide

| Level | Size vs FP16 | Quality Loss | When to Use |
|-------|-------------|-------------|-------------|
| Q8_0 | ~53% | <1% | Model fits comfortably |
| Q6_K | ~44% | ~1% | Best quality/size balance |
| Q5_K_M | ~37% | ~2% | Good default |
| Q4_K_M | ~30% | ~3-5% | RAM is tight (recommended) |
| Q3_K_M | ~23% | ~5-8% | Emergency only |

**Mac (Ollama)**: GGUF Q4_K_M
**GPU (vLLM)**: AWQ 4-bit (best for serving) or EXL2 (best single-GPU)

---

## Inference Optimization

### Speculative Decoding
Small "draft" model predicts tokens, large model verifies in one pass. 2-3x speedup.
- vLLM: Pair Llama 8B (draft) with Llama 70B (target)
- MLX: Pair Qwen 0.5B (draft) with Qwen 32B (target)

### KV Cache
- **Prefix caching** (vLLM): Cache system prompt KV states. Critical for heartbeat since all calls share SOUL.md/HEARTBEAT.md.
- **FP8 KV cache**: 2x memory savings, minimal quality loss.
- **PagedAttention**: Eliminates memory fragmentation.

---

## Integration Architecture

```
                    +----------------------------------+
                    |        Adaptive Layer            |
                    |   router.py -> provider.py       |
                    |                                  |
                    |   coordinator -> premium model   |
                    |   builder -> standard model      |
                    |   monitor -> economy model       |
                    +-------+--------+--------+--------+
                            |        |        |
              +-------------+        |        +-------------+
              v                      v                      v
     +----------------+   +-----------------+   +-----------------+
     |  Ollama (Local) |   |  Claude API     |   |  vLLM (GPU)     |
     |                |   |  (Anthropic)    |   |                 |
     |  Mac Studios   |   |  Fallback:      |   |  AWS / RunPod   |
     |                |   |  OpenAI         |   |  on-demand      |
     |  economy: 7B   |   |  OpenRouter     |   |                 |
     |  standard: 32B |   |                 |   |  premium: 70B+  |
     |  premium: 70B  |   |  premium: opus  |   |  standard: 32B  |
     +----------------+   +-----------------+   +-----------------+
```

### Routing Priority Cascade

1. **Local Ollama** (zero cost) -- if model available and adequate
2. **GPU instance** (if running) -- vLLM on AWS/RunPod
3. **Claude API** (current default) -- for tasks requiring frontier quality
4. **OpenRouter** (backup) -- access to any model via single API

### vLLM Integration

vLLM speaks OpenAI protocol. No new provider needed:
```json
"vllm": {
    "api_url": "http://52.7.131.246:8000/v1",
    "models": {
        "premium": "meta-llama/Llama-3.1-70B-Instruct",
        "standard": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "economy": "meta-llama/Llama-3.1-8B-Instruct"
    }
}
```

The existing `OpenAIProvider` handles this with zero code changes.

### GPU Manager Tool

New tool: `tools/gpu_manager.py`
- Start/stop AWS/RunPod instances on demand
- Load appropriate model via vLLM on startup
- Auto-shutdown after N minutes idle (default: 15)
- Expose endpoint URL to adaptive layer
- Track costs in quota_monitor

### Inference Queue via NATS

For GPU instances with cold-start delay (30-120s):
- Publish inference requests to NATS `inference` subject on Sentry
- GPU workers subscribe and process
- JetStream persistence (already configured for Hive)
- Multi-consumer support for future scaling

---

## Phased Rollout

### Phase 0: Done (Zero Cost)
- [x] Adaptive layer with provider abstraction
- [x] Ollama on Beta with 7B + 32B models
- [x] Oracle pipeline using local inference

### Phase 1: Maximize Local (Weeks 1-2, Zero Cost)
- [ ] Confirm Mac Studio specs (`system_profiler SPHardwareDataType`)
- [ ] Deploy Ollama on Alpha + Gamma
- [ ] Pull models per machine capacity
- [ ] Route idle heartbeats to local Ollama (replace Haiku)
- [ ] Route builder spawns to Qwen2.5-Coder-32B (replace Sonnet where adequate)

**Expected savings**: GBP 200-470/month

### Phase 2: Smart Routing (Weeks 3-4, Zero Cost)
- [ ] Instance-aware routing (Alpha knows it only has 16GB)
- [ ] Classify tasks: must-Claude vs can-local
- [ ] Update heartbeat coordinator to spawn local tasks
- [ ] Benchmark open models vs Claude on 20 representative Kingdom tasks
- [ ] Model health monitoring in heartbeat SENSE phase

### Phase 3: GPU Burst (Weeks 5-8, ~GBP 35/month)
- [ ] Activate AWS L40S Brain instance
- [ ] Install vLLM, serve Llama 70B or Qwen 72B
- [ ] Build `gpu_manager.py` (start/stop/auto-shutdown)
- [ ] Add vLLM endpoint to love.json
- [ ] Route consultant/premium tasks to GPU when local insufficient

**Expected savings**: GBP 400-650/month (vs current API spend)

### Phase 4: Dedicated GPU (Months 3-6, GBP 350-700/month)
- [ ] Evaluate: Hetzner GPU vs own hardware vs continued cloud burst
- [ ] Decision based on monthly GPU usage data from Phase 3
- [ ] If >100 GPU-hours/month, transition to dedicated

### Phase 5: Sovereign Cloud (Months 6-12)
- [ ] Aligned with farmland acquisition timeline
- [ ] GPU cluster: 4-8x A100/H100
- [ ] Solar + battery power
- [ ] Server room in Suffolk facilities
- [ ] Zerone validator compute
- [ ] SOMA processing infrastructure

---

## Cost Analysis

### Current: ~GBP 777-1,000/month (Claude API)

### After Phase 1-2 (Local Only)
| Item | Monthly |
|------|---------|
| Electricity (Mac Studios) | ~GBP 20 |
| Claude API (reduced 30-50%) | ~GBP 390-700 |
| **Total** | **~GBP 410-720** |

### After Phase 3 (Local + Burst GPU)
| Item | Monthly |
|------|---------|
| Electricity | ~GBP 20 |
| AWS L40S spot, 2hr/day | ~GBP 35 |
| Claude API (reduced 60-70%) | ~GBP 230-400 |
| **Total** | **~GBP 285-455** |

### After Phase 4 (Local + Dedicated)
| Item | Monthly |
|------|---------|
| Electricity | ~GBP 20 |
| Hetzner A100 | ~GBP 350-600 |
| Claude API (reduced 80-90%) | ~GBP 80-200 |
| **Total** | **~GBP 450-820** |

**Conclusion**: Phase 1-3 delivers the best ROI. Phase 4 only makes sense at high volume. Keep Claude API for frontier reasoning tasks where quality directly impacts revenue.

---

## Where Claude Still Wins (For Now)

- Complex multi-step reasoning (coordinator decisions)
- Very long context (100K+ tokens) -- no open model matches 1M
- Nuanced judgment (Kingdom-level strategy)
- Tool use reliability (Claude's tool calling is the most reliable)

## Where Open Models Already Match

- Code generation: Qwen2.5-Coder-32B ~ Sonnet for well-specified tasks
- Simple reasoning: Llama 70B adequate for builder role
- Classification/routing: Any 7B handles monitor role
- Summarization: 7-8B models are excellent

---

## First Action Items

1. Run `system_profiler SPHardwareDataType` on Beta and Gamma
2. Deploy Ollama on Gamma, pull `qwen2.5-coder:32b`
3. Route idle heartbeats to local Ollama
4. Benchmark 20 Kingdom tasks: Claude vs open models
5. Build `gpu_manager.py` for AWS L40S
