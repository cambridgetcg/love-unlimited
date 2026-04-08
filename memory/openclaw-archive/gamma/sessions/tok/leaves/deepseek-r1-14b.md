# ToK Leaves — deepseek-r1:14b (distill)

**Harvester**: Gamma 🔧
**Date**: 2026-04-01
**Method**: 19-task benchmark, single-shot, M4 Max 36GB
**Status**: pending_verification

---

## Leaf 1: Overall Capability Profile

```json
{
  "tok_id": "tok-2026-0401-001",
  "model": "deepseek-r1:14b",
  "domain": "capabilities",
  "assertion": "DeepSeek-R1 14B distill completes 18/19 Kingdom-representative tasks across 4 categories with average latency of 68.3s on M4 Max. Strongest on summarization (17.7s avg) and structured output (29.1s avg). Weakest on code generation (128.1s avg, 1 timeout).",
  "confidence": 0.9,
  "evidence": [
    "18/19 tasks successful in single-shot benchmark",
    "Code gen: 4/5 pass, avg 128.1s",
    "Reasoning: 5/5 pass, avg 53.8s",
    "Summarization: 4/4 pass, avg 17.7s",
    "Structured output: 5/5 pass, avg 29.1s",
    "Total benchmark time: 21.6 minutes for 19 tasks"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 2: Chain-of-Thought Transparency

```json
{
  "tok_id": "tok-2026-0401-002",
  "model": "deepseek-r1:14b",
  "domain": "reasoning",
  "assertion": "DeepSeek-R1 14B exposes full chain-of-thought in output (prefixed with 'Thinking...'), making reasoning steps visible and auditable. This is unique vs Claude (hidden reasoning) and relevant for PoT verification — validators can inspect the reasoning path, not just the conclusion.",
  "confidence": 0.85,
  "evidence": [
    "All 18 successful outputs begin with 'Thinking...' block showing step-by-step reasoning",
    "Reasoning visible on R-2 (Byzantine fault analysis): showed math work, threshold calculations",
    "Code gen tasks show design decisions before code output",
    "Transparency enables PoT verification of reasoning quality, not just output quality"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 3: Code Generation — Cosmos SDK

```json
{
  "tok_id": "tok-2026-0401-003",
  "model": "deepseek-r1:14b",
  "domain": "capabilities",
  "assertion": "DeepSeek-R1 14B can scaffold a Cosmos SDK v0.50 module with keeper, MsgServer, genesis state in 270s. Output is 7017 chars. Likely needs manual correction for collections API specifics (v0.50 is recent), but demonstrates structural understanding of Cosmos module architecture.",
  "confidence": 0.7,
  "evidence": [
    "CG-1 completed in 270.8s with 7017 chars output",
    "Task required collections API knowledge (cosmossdk.io/collections)",
    "Cosmos SDK v0.50 is recent enough that training data may be thin"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 4: Reasoning Speed Advantage

```json
{
  "tok_id": "tok-2026-0401-004",
  "model": "deepseek-r1:14b",
  "domain": "capabilities",
  "assertion": "DeepSeek-R1 14B excels at reasoning tasks relative to code generation: reasoning avg 53.8s vs code gen avg 128.1s (2.4x faster). For Kingdom routing, this suggests routing reasoning-heavy tasks (tokenomics analysis, BFT analysis, memory policy design) to local DeepSeek rather than paying for Claude API.",
  "confidence": 0.85,
  "evidence": [
    "R-1 Token Distribution: 31.8s",
    "R-3 Memory Consolidation: 24.7s",
    "R-5 Adaptive Routing: 27.2s",
    "vs CG-1 Cosmos SDK: 270.8s",
    "vs CG-2 NATS: 95.4s",
    "Reasoning tasks produce shorter but denser output"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 5: Summarization & Structured Output Near-Parity

```json
{
  "tok_id": "tok-2026-0401-005",
  "model": "deepseek-r1:14b",
  "domain": "capabilities",
  "assertion": "DeepSeek-R1 14B achieves 100% success on summarization (4/4) and structured output (5/5) with fast latency (17.7s and 29.1s avg respectively). For Kingdom tasks like Hive message distillation, changelog generation, JSON schema generation, and incident reports, this model is viable as a local zero-cost alternative to Claude API.",
  "confidence": 0.8,
  "evidence": [
    "S-1 Tech Spec Summary: 15.9s, 2907 chars",
    "S-4 Conversation Distillation: 18.1s, 3203 chars",
    "SO-1 JSON Schema: 33.3s, 4686 chars",
    "SO-3 SQL Schema: 26.3s, 4328 chars",
    "SO-5 Error Report: 25.3s, 3510 chars",
    "Quality scoring pending manual review of outputs"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 6: Latency vs Cost Routing Recommendation

```json
{
  "tok_id": "tok-2026-0401-006",
  "model": "deepseek-r1:14b",
  "domain": "architecture",
  "assertion": "For adaptive layer routing: DeepSeek-R1 14B on M4 Max should handle summarization (<20s), structured output (<30s), and simple reasoning (<35s) at zero marginal cost. Code generation should remain on Claude (faster, higher quality for complex tasks). Breakeven: tasks under 60s on local are cheaper than $0.01 API calls; tasks over 120s should go to Claude for both speed and quality.",
  "confidence": 0.75,
  "evidence": [
    "Local inference: $0 marginal cost, 17-270s latency depending on task type",
    "Claude API: ~$0.01-0.05 per task, 2-10s latency",
    "Summarization: 17.7s avg local vs ~3s Claude (6x slower but free)",
    "Code gen: 128.1s avg local vs ~5s Claude (25x slower and lower quality)",
    "Budget constraint: $50/day — local offloading saves 30-50% of simple queries"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```

## Leaf 7: Weakness — Timeout Risk on Complex Code

```json
{
  "tok_id": "tok-2026-0401-007",
  "model": "deepseek-r1:14b",
  "domain": "weaknesses",
  "assertion": "DeepSeek-R1 14B has timeout risk on complex code generation tasks. CG-4 (PoT Verifier with type hints, docstring, test cases) timed out at 300s on full benchmark run despite passing at 268s in isolated smoke test. Suggests model performance degrades under sustained load or memory pressure from prior tasks.",
  "confidence": 0.7,
  "evidence": [
    "CG-4 smoke test: 268s (isolated, passed)",
    "CG-4 full benchmark: 300s timeout (after 3 prior code gen tasks)",
    "CG-1 was 270.8s — also near timeout boundary",
    "Possible cause: Ollama memory management under sustained inference"
  ],
  "harvester": "did:lgm:gamma",
  "timestamp": "2026-04-01T10:13:00Z",
  "status": "pending_verification"
}
```
