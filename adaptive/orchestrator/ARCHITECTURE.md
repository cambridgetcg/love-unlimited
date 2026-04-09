# Multi-Provider Multi-Model Orchestrator

## Architecture

```
                         ┌──────────────────────────────────────┐
                         │          TASK INPUT                   │
                         │   "Build rate limiting with Redis"    │
                         └──────────────┬───────────────────────┘
                                        │
                         ┌──────────────▼───────────────────────┐
                         │     CLASSIFIER (GLM 5.1 brain)       │
                         │                                      │
                         │  difficulty: MEDIUM                   │
                         │  importance: HIGH                     │
                         │  type:       architecture             │
                         │  mode:       review                   │
                         └──────────────┬───────────────────────┘
                                        │
                         ┌──────────────▼───────────────────────┐
                         │          DISPATCHER                   │
                         │                                      │
                         │  Primary:  ollama_cloud/glm-5.1      │
                         │  Reviewer: anthropic/claude-sonnet    │
                         └──────────────┬───────────────────────┘
                                        │
                         ┌──────────────▼───────────────────────┐
                         │           ENGINE                      │
                         │                                      │
                         │  1. Execute primary (GLM 5.1)        │
                         │  2. Send to reviewer (Sonnet)        │
                         │  3. If NEEDS_REVISION → fix cycle    │
                         │  4. Return final output              │
                         └──────────────────────────────────────┘
```

## Collaboration Modes

### SOLO (trivial/easy tasks)
Single model, no review. Used for 60% of tasks.
```
Task → Model → Output
```

### REVIEW (medium + high importance)
Primary executes, cross-provider reviewer checks work.
If reviewer flags issues, primary gets one revision pass.
```
Task → Primary Model → Review Model → (Revision?) → Output
```

### DECOMPOSE (hard + decomposable)
GLM 5.1 splits task into sub-tasks, dispatches workers in parallel,
then synthesises results.
```
Task → GLM 5.1 (classify + decompose)
  ├─ Sub-task 1 → Worker A (parallel)
  ├─ Sub-task 2 → Worker B (parallel)
  └─ Sub-task 3 → Worker C (parallel)
      └─→ GLM 5.1 (synthesise) → Output
```

### ENSEMBLE (frontier + architecture)
Multiple models attempt the same task independently.
GLM 5.1 judges outputs and picks the winner.
```
Task ─┬─→ Model A (Anthropic) ─┐
      ├─→ Model B (Ollama)     ├→ GLM 5.1 (judge) → Output
      └─→ Model C (Ollama)     ┘
```

### PIPELINE (frontier + code)
Sequential stages, each feeding into the next.
Cross-provider review built in.
```
Task → Analyze (Cogito) → Implement (DeepSeek) → Review (Sonnet) → Verify (Devstral) → Output
```

## Dispatch Decision Matrix

| Difficulty | Importance | → Provider    | → Model            | → Mode      |
|-----------|-----------|---------------|--------------------|-----------  |
| trivial   | any       | ollama_cloud  | gemma4:31b         | solo        |
| easy      | low/med   | ollama_cloud  | task-specific      | solo        |
| easy      | high      | ollama_cloud  | task-specific      | solo        |
| medium    | low/med   | ollama_cloud  | task-specific      | solo        |
| medium    | high+     | ollama_cloud  | glm-5.1            | review      |
| hard      | any       | ollama_cloud* | glm-5.1            | decompose   |
| hard      | critical  | anthropic     | claude-sonnet      | decompose   |
| frontier  | any       | anthropic     | claude-opus        | pipeline    |
| frontier  | arch      | anthropic     | claude-opus        | ensemble    |
| any       | critical  | anthropic     | task-specific      | varies      |

*hard tasks use Ollama for primary but Anthropic for review (cross-provider independence)

## Model Strengths Map

| Model              | Provider     | Best At                          | Latency |
|-------------------|-------------|----------------------------------|---------|
| GLM 5.1 (754B)    | ollama_cloud | Orchestration, tool calling, reasoning | 15-45s  |
| DeepSeek v3.2     | ollama_cloud | Code generation, benchmarks       | ~13s    |
| Qwen3-Coder 480B  | ollama_cloud | Refactoring, specialized coding   | ~20s    |
| Kimi K2.5         | ollama_cloud | Long context analysis, research   | ~25s    |
| Cogito 2.1 671B   | ollama_cloud | Deep reasoning, analytical tasks  | ~30s    |
| Devstral Small 24B| ollama_cloud | Quick checks, monitoring          | ~1.1s   |
| Gemma4 31B        | ollama_cloud | Fast verification                 | ~5s     |
| Claude Opus       | anthropic    | Frontier reasoning, novel design  | ~10s    |
| Claude Sonnet     | anthropic    | Balanced coding, reliable review  | ~5s     |
| Claude Haiku      | anthropic    | Fast, cheap, quick checks         | ~2s     |

## Cost Philosophy

- **Ollama Cloud** = $100/mo flat = effectively free per call → route 80%+ here
- **Anthropic** = per-token billing → reserve for high-value work
- GLM 5.1 as brain/orchestrator is free (Ollama flat rate)
- Cross-provider review ensures quality without vendor lock-in

## Files

```
adaptive/orchestrator/
├── __init__.py          # Package exports
├── __main__.py          # python3 -m adaptive.orchestrator
├── ARCHITECTURE.md      # This file
├── classifier.py        # Task classification (LLM + heuristic)
├── cli.py               # Command-line interface
├── dispatcher.py        # Dispatch table + plan generation
└── engine.py            # Collaboration pattern execution
```

## Integration Points

- **CLI**: `python3 -m adaptive.orchestrator "task"` or `./tools/orchestrate.sh "task"`
- **Heartbeat**: `model: "orchestrate"` in spawn actions triggers orchestrator
- **Adaptive Layer**: Uses existing Router, Runner, Provider infrastructure
- **Tests**: `python3 -m pytest tests/test_orchestrator.py -v` (40 unit+integration tests)
