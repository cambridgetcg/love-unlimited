# CPU Optimization Strategy for H200 Pod

**Date:** 2026-04-16
**Author:** Claude Opus 4.7 (collaborating with Yu Ai)
**Status:** Approved under standing autonomy; proceeding post-SFT-v2.

## Context

The H200 pod (157.66.255.19:10308) has a 144 GB GPU perpetually at 100% util serving `vllm --enable-lora`, but its CPUs (typically 48-64 vCPUs, 500+ GB RAM on RunPod H200 instances) sit mostly idle. We're paying for the full pod hourly. Anything productive run on CPU is marginal-free.

**Hard constraints**
- Cryptocurrency mining violates RunPod's AUP (§2.2) — not considered further.
- CPU workloads must `nice +10` so they can't starve vLLM request handling.
- Long-running CPU jobs use `screen` sessions so they survive SSH disconnects.
- Disk is shared; CPU workloads write to `/workspace/cpu/` (not `/workspace/training/`) to avoid any collision with training writes.

## Goals

1. **Feed the judge-gate flywheel** — generate candidates cheaply on CPU so we're not burning Anthropic quotas on low-value queries.
2. **Harden infrastructure** — semantic dedupe, embedding-backed retrieval, eval-set expansion, all CPU-friendly.
3. **Provide redundancy** — CPU vLLM fallback so SP1 detector doesn't 404 during GPU training windows.

**Non-goal:** beating GPU quality. CPU's job is *preparation*, not production.

## Pipeline integration

```
           ┌───────── CPU lane ─────────┐       ┌────── GPU lane ──────┐   ┌─── API lane ───┐
           │                            │       │                      │   │                │
           │  llama.cpp prompt gen    ──┼──┐    │  H200/Alpha mode-two│   │  Sonnet mode_1 │
           │  sentence-transformers     │  │    │  (batch, overnight) │   │  Haiku judge   │
           │  FAISS dedupe + retrieval  │  ├──► │                     │──►│  Opus confirm  │
           │  Eval probe synthesis      │  │    │  vLLM serving adap- │   │                │
           │  Memory curation daemon    │  │    │  ter for chat+SP1   │   │                │
           └────────────────────────────┘  │    └──────────────────────┘   └────────────────┘
                                           │
                                           └─► accepted pairs → KTO/SFT training
```

CPU feeds the top of the funnel; Alpha does mode-two bulk; Claude does mode-one quality; Haiku/Opus filter. CPU is *not* a substitute for any of those — it's the seed stage.

## Work Packages

Priorities reflect leverage-per-hour-of-work — P0 unblocks the flywheel, P1 improves quality, P2 is nice-to-have.

### P0 — Prompt farm (highest leverage)

Run `llama.cpp` with `Qwen2.5-7B-Instruct-Q4_K_M.gguf` (~4 GB on disk, ~6 GB RAM in use, 5-10 tok/s on 16-thread CPU) to produce seed prompts in bulk.

- **Input:** per-dimension prompt templates + counter-prompts (e.g., "generate 100 questions that probe `protective_vagueness` in medical contexts")
- **Output:** `/workspace/cpu/seeds/<dim>/<timestamp>.jsonl` — one prompt per line
- **Throughput target:** 500-5000 prompts/day per dimension sub-domain
- **Quality gate:** Claude curation (Haiku) pass to filter incoherent/trivial prompts — ~2s/prompt budget, so ~100 prompts/filtered-minute
- **Wiring:** `training/scripts/cpu_prompt_farm.py` → calls llama.cpp server + llama.cpp via HTTP
- **Setup cost:** ~2 h (download GGUF, compile llama.cpp with OpenMP, write farm script)

### P0 — Semantic dedupe pipeline

Our current `prompt.lower().strip()` dedupe misses paraphrases. Use `sentence-transformers/all-MiniLM-L6-v2` (~80 MB, CPU-friendly) to embed all prompts + responses, then FAISS for near-neighbor lookup.

- **Embed the existing 800-example pool** → 30s CPU
- **FAISS IndexFlatIP** → persistent on disk
- **Dedupe threshold:** cosine > 0.92 for prompts, > 0.95 for full responses
- **Expected recovery:** 50-100 additional unique prompts, 200-400 additional unique responses
- **Wiring:** `training/scripts/semantic_dedupe.py` → produces `<input>.deduped.jsonl` and `<input>.duplicate_clusters.json`
- **Setup cost:** ~1 h

### P0 — Eval-set expansion for statistical power

Current eval: 25 adversarial + 84 red-team = 109 probes. To distinguish +5pp mode-one improvement at 80% power, we need ~250 per set. CPU can generate 3-5× that.

- **Generation:** llama.cpp + dimension-specific counter-prompts
- **Curation:** Claude Haiku filters to genuinely-hard adversarial probes (skip trivial ones)
- **Output:** `training/eval/redteam/mode_one_weakness_probes_v2.jsonl` (target n≥400), `training/eval/adversarial_prompts_v2.jsonl` (target n≥250)
- **Setup cost:** ~3 h, can run overnight

### P1 — Few-shot retrieval for mode_one generation

Inject the 3 nearest-neighbor accepted mode_ones as in-context exemplars when asking Sonnet to generate a new mode_one. Should nudge score +0.05-0.10 on hard prompts (evidence: Sonnet is already at 0.82 ceiling via Haiku; few-shot might lift to 0.87).

- **Wiring:** `training/scripts/claude_mode_one_gen.py` gains `--retrieval-index <faiss-path>` flag; before each generation, embed the new prompt, query FAISS for top-3 nearest accepted prompts, inject their mode_ones as few-shot exemplars in the system prompt.
- **Embedding infra shared with dedupe pipeline.**
- **Setup cost:** ~1 h after dedupe infra exists

### P1 — CPU fallback vLLM for SP1 detector during training windows

During GPU training (SFT, KTO), vLLM is down. SP1 detector hits a dead endpoint, all detections error for 30-50 min. Solution: run `llama.cpp --server` with Qwen2.5-1.5B-Instruct-Q4_K_M.gguf on CPU, register it in `tools/truth_detector/config.yaml` as a fallback for `qwen.*` routing.

- **Quality:** much worse than 72B (probably m1 ceiling 0.50 vs 0.82) — but detections don't 404
- **Latency:** ~5-15s per detection on CPU, acceptable for fire-and-forget
- **Wiring:** start llama.cpp --server on :8001, add fallback config entry. Could be conditional: only use if :8000 (vLLM) returns 502.
- **Setup cost:** ~1 h, mostly config work

### P2 — Memory curation daemon

The `memory/autonomous/feed.jsonl` and `memory/daily/*.md` files grow unindexed. CPU-side daemon to dedupe, tag, and build a search index would make the autonomous stack actually queryable.

- **Stack:** Python + sentence-transformers (shared) + SQLite FTS5
- **Triggers:** cron-style every 30 min, idempotent
- **Wiring:** `tools/memory/curator.py`
- **Setup cost:** ~4 h — deferred until P0/P1 land

### P2 — Autonomous mode-two generation

Have CPU local model generate naturalistic mode-two candidates overnight (Alpha does this too, but CPU is free capacity). Claude judges, keeps best. Feeds KTO undesirable pool.

- **Setup cost:** ~2 h, but wait until prompt farm proves itself

### P3 — Background metrics collection

`nvidia-smi` logger, training checkpoint watcher, disk usage alerts. Nice-to-have, not urgent. Skip until we hit an incident that would've been caught by it.

## Shared infrastructure

**On-pod directory layout:**
```
/workspace/cpu/
  ├── bin/
  │   ├── llama.cpp/            # compiled with OpenMP
  │   └── models/
  │       ├── Qwen2.5-7B-Instruct-Q4_K_M.gguf      # prompt farm
  │       └── Qwen2.5-1.5B-Instruct-Q4_K_M.gguf    # SP1 fallback
  ├── seeds/<dim>/<timestamp>.jsonl    # raw generated prompts
  ├── deduped/<corpus>.jsonl            # semantic-deduped corpora
  ├── embeddings/<corpus>.faiss         # FAISS indices
  └── logs/                             # screen session logs
```

**Schema convention:** every CPU-produced JSONL matches `{prompt: str, _cpu_source: str, _cpu_timestamp: iso8601}` so downstream pipelines can filter/audit.

**Local-mac side:** CPU-generated data synced back via `rsync -av pod:/workspace/cpu/deduped/ training/cpu_out/` (add to `sync_and_train.sh` post-run).

## Integration with existing flywheel

```
1. CPU prompt farm          → raw candidate prompts (5000/day/dim)
2. CPU semantic dedupe      → unique-enough subset (~60% retention)
3. Haiku curation           → coherent probes (~70% retention)
4. Claude (Sonnet) generates mode_one for each
5. Alpha (H200) generates mode_two for each        [when GPU free]
6. Haiku judge-gates, Opus confirms
7. Accepted → training pool → KTO/SFT
```

CPU adds stage 1 + 2; everything downstream is unchanged.

## Timeline / effort

| Package | Effort | Depends on | Value |
|---|---|---|---|
| llama.cpp setup + Qwen 7B GGUF | 2 h | Nothing | Unlocks P0 |
| Prompt farm script | 2 h | llama.cpp | Main value lever |
| Semantic dedupe | 1 h | sentence-transformers install | Quality bump |
| Eval expansion | 3 h (overnight run) | Prompt farm | Statistical power |
| Few-shot retrieval | 1 h | Dedupe | +0.05-0.10 m1 on hard prompts |
| CPU fallback vLLM | 1 h | llama.cpp 1.5B GGUF | Detector uptime |
| Memory daemon | 4 h | Dedupe | Operational clarity |

**Total P0+P1: ~10 h, mostly overnight / async.** Can start immediately after SFT-v2 finishes (concurrent with KTO-v1 training).

## Risks

| Risk | Probability | Mitigation |
|---|---|---|
| CPU job competes with vLLM request handling | M | `nice +10`, separate worker threads, cap llama.cpp threads at `N_CPUS - 4` |
| Generated prompts are low-quality / repetitive | H | Haiku curation stage + semantic dedupe; if too much waste, tune prompt template |
| GGUF download (~4 GB) during training slows H200 disk | L | Download to `/workspace/cpu/bin/models/` (separate subtree); modern disks handle this |
| Stale prompts from CPU farm get baked into training | M | All CPU-produced data passes Haiku judge-gate before entering training pool |
| Local small model hallucinates nonsense prompts | M | Filter by length + Haiku coherence check |

## Decisions Made

1. **Use Qwen2.5-7B-Instruct-Q4_K_M.gguf, not Llama-3-8B.** Qwen is better at Chinese + English, matches our Alpha/base-model family (consistent failure-mode distribution for training data generation).
2. **Use 1.5B for SP1 fallback, not 7B.** Latency < quality here — SP1 is fire-and-forget, fast matters.
3. **No on-pod training from CPU-generated data.** CPU generates; Claude judges; H200 trains. Clean separation.
4. **Don't run CPU jobs on the Mac.** Pod CPU is paid-for, Mac CPU is shared with interactive work.
5. **Keep everything `nice +10` by default.** vLLM request latency > prompt farm throughput.

## Non-goals

- Training on CPU (way too slow for 72B, even 4-bit)
- Serving production traffic on CPU (except detector fallback)
- Replacing Sonnet's mode_one generation (quality gap too large)
- Reward-model training (defer to v3 / never)

## Execution Checkpoints

- [ ] SFT-v2 training completes (prerequisite — CPU jobs wait so we don't fight for disk I/O on first boot)
- [ ] Compile llama.cpp with OpenMP on pod
- [ ] Download Qwen2.5-7B-Q4 + Qwen2.5-1.5B-Q4 GGUFs
- [ ] `cpu_prompt_farm.py` produces 100 prompts/dim, sanity-check quality
- [ ] `semantic_dedupe.py` first run on existing 800-example pool
- [ ] Eval expansion — overnight run, ≥400 red-team probes
- [ ] Few-shot retrieval wired into `claude_mode_one_gen.py`
- [ ] CPU fallback vLLM registered in truth_detector config
- [ ] Rsync job added to `sync_and_train.sh` for CPU output sync
