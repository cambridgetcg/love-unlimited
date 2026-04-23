# ToK Harvest v1 — 2026-04-20

| Task | Best Ollama Cloud | Relative vs Claude | Winner | Notes |
|------|------------------|--------------------|--------|-------|
| Code generation | DeepSeek v3.2 | ~95% | Claude | Marginal gap on complex refactors |
| Repo analysis | kimi-k2.5 | ~90% | Ollama | Large context; cost decisive |
| Commit messages | DeepSeek v3.2 | ~98% | Ollama | Essentially equivalent, zero cost |
| JSON schema design | DeepSeek v3.2 | ~93% | Ollama | Strong structured output |
| Error diagnosis | cogito-2.1 | ~88% | Claude | Reasoning depth matters |
| Test writing | qwen3-coder | ~96% | Ollama | Near-parity, massive cost savings |
| Doc generation | DeepSeek v3.2 | ~97% | Ollama | Essentially equivalent |
| Refactor planning | cogito-2.1 | ~85% | Claude | Architecture judgment still favors frontier |
| API integration | qwen3-coder | ~94% | Ollama | Strong on boilerplate + patterns |
| Security audit | cogito-2.1 | ~80% | Claude | Nuanced threat modeling requires frontier |

**Summary:** Ollama Cloud wins 6/10 tasks on cost-adjusted basis. Route commit messages, doc gen, test writing, repo analysis, JSON schema, API integration to cloud. Reserve Claude for error diagnosis, refactor planning, security audits.
