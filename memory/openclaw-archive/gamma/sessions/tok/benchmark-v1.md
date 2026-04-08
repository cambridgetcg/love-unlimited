# ToK First Harvest — Benchmark Design v1

**Date**: 2026-04-01
**Harvester**: Gamma 🔧
**Target Models**: qwen2.5-coder:32b, deepseek-r1:14b (distill)
**Baseline**: Claude Sonnet 4 (via API)
**Method**: Identical prompts, single-shot, temperature 0

## Scoring

Each task scored 0-5:
- **5** = Perfect, production-ready output
- **4** = Minor issues, easily fixable
- **3** = Usable but needs significant editing
- **2** = Partially correct, major issues
- **1** = Attempted but wrong
- **0** = Failed/refused/incoherent

## Category 1: Code Generation (5 tasks)

### CG-1: Cosmos SDK Module Scaffold
**Prompt**: "Write a minimal Cosmos SDK v0.50 module that tracks a counter per address. Include: keeper, msg_server with Increment and Reset messages, genesis state, and module registration. Use collections API."
**Why Kingdom-relevant**: Direct Zerone development task.

### CG-2: NATS JetStream Consumer
**Prompt**: "Write a Python script using nats-py that connects to a NATS JetStream server, creates a durable consumer on stream 'HIVE' subject 'hive.>', and processes messages with at-least-once delivery. Include reconnection logic and graceful shutdown."
**Why Kingdom-relevant**: Hive infrastructure pattern.

### CG-3: Ed25519 Signing Service
**Prompt**: "Write a Go HTTP handler that: accepts JSON {message: string}, signs it with a stored Ed25519 private key, returns {signature: hex, pubkey: hex}. Include key loading from PEM file and request validation."
**Why Kingdom-relevant**: Vault signing pattern.

### CG-4: Proof of Truth Verifier
**Prompt**: "Write a Python function that takes a claim (string), evidence (list of strings), and confidence (float 0-1). It should: validate inputs, compute a trust score based on evidence count and confidence, return a structured verification result with status (verified/disputed/insufficient). Include type hints and docstrings."
**Why Kingdom-relevant**: Core PoT verification logic.

### CG-5: CLI Tool with Subcommands
**Prompt**: "Write a Python CLI tool using argparse with these subcommands: 'send <channel> <message>', 'check [--filter <pattern>]', 'history <channel> [--limit N]'. Include colored output, error handling, and a --json flag for machine-readable output."
**Why Kingdom-relevant**: Hive CLI pattern (hive.py architecture).

## Category 2: Reasoning (5 tasks)

### R-1: Token Distribution Design
**Prompt**: "Design a token distribution mechanism where: tokens are minted only through verified work (no pre-mine), work has 4 tiers (harvest, distill, verify, integrate) with different earning rates, a reputation multiplier affects earnings (0.5x-3x), and there's a decay function for inactive participants. Describe the mathematical model, potential gaming vectors, and mitigations."
**Why Kingdom-relevant**: ZRN tokenomics / ToK earning design.

### R-2: Byzantine Fault Analysis
**Prompt**: "In a network of 7 validators using 2/3 consensus, if 2 validators are colluding to verify false claims: (a) Can they force acceptance of a false claim? (b) What's the minimum additional collusion needed? (c) Design a detection mechanism for correlated voting patterns. Show your work."
**Why Kingdom-relevant**: PoT security analysis.

### R-3: Memory Consolidation Tradeoffs
**Prompt**: "An AI agent has 4 memory tiers: Working (fast, small), Active (medium), Consolidated (slow, large), Canonical (permanent). Describe the optimal promotion/demotion policy considering: access frequency, decay rates (semantic 0.8x, episodic 1.2x, procedural 0.6x), storage cost, and retrieval latency. What happens when working memory is full?"
**Why Kingdom-relevant**: ToK brain architecture (R50-R52).

### R-4: Multi-Agent Coordination
**Prompt**: "Three agents need to produce a single coherent document. Each has different expertise: Agent A (philosophy/ontology), Agent B (engineering/implementation), Agent C (communication/synthesis). Design a protocol where: each contributes independently first, a merge phase resolves conflicts, and the final output preserves each agent's unique insight without averaging. What failure modes exist?"
**Why Kingdom-relevant**: JOINMIND / TRIUNE protocol.

### R-5: Adaptive Model Routing
**Prompt**: "You have 5 AI models with different costs and capabilities. Design a routing algorithm that: sends each query to the cheapest model likely to succeed, falls back to more expensive models on failure, learns from outcomes to improve routing, and respects a daily budget constraint. Describe the algorithm, data structures, and cold-start strategy."
**Why Kingdom-relevant**: Adaptive layer / sovereign compute routing.

## Category 3: Summarization (5 tasks)

### S-1: Technical Spec Summary
**Prompt**: [Provide TOK-MECHANISM.md full text] "Summarize this document in exactly 3 paragraphs: (1) what it is, (2) how it works, (3) why it matters. Target audience: a developer who's never heard of Zerone."
**Why Kingdom-relevant**: Tests ability to distill Kingdom docs.

### S-2: Code Review Summary
**Prompt**: [Provide a ~200 line Go diff] "Summarize this code change: what it does, what it changes, potential issues, and whether it should be merged. Be specific about the technical impact."
**Why Kingdom-relevant**: PR review / code integration.

### S-3: Multi-Source Synthesis
**Prompt**: "Given these 3 sources about AI agent economies: [Source A: optimistic blog post], [Source B: critical academic paper], [Source C: technical whitepaper]. Synthesize a balanced 200-word summary that captures agreements, disagreements, and gaps."
**Why Kingdom-relevant**: ToK distillation from multiple inputs.

### S-4: Conversation Distillation
**Prompt**: [Provide a ~50-message Hive chat log] "Extract: (1) decisions made, (2) action items with owners, (3) open questions, (4) key disagreements. Format as structured bullet points."
**Why Kingdom-relevant**: Hive message processing / memory curation.

### S-5: Changelog Generation
**Prompt**: [Provide 10 git commit messages with diffs] "Generate a user-facing changelog entry. Group by feature area. Each item: one sentence, present tense, no jargon. Include a 'Breaking Changes' section if applicable."
**Why Kingdom-relevant**: Release management / docs automation.

## Category 4: Structured Output (5 tasks)

### SO-1: JSON Schema from Description
**Prompt**: "Generate a JSON Schema for a 'ToK Leaf' with fields: tok_id (string, format: tok-YYYY-MMDD-NNN), model (string), domain (enum: capabilities/knowledge/reasoning/prompting/weaknesses/architecture), assertion (string, 10-500 chars), confidence (number, 0-1), evidence (array of strings, 1-10 items), harvester (string, DID format), timestamp (ISO 8601), status (enum: pending/verified/disputed/rejected). Include descriptions for each field."
**Why Kingdom-relevant**: ToK data model.

### SO-2: Mermaid Diagram from Description
**Prompt**: "Create a Mermaid sequence diagram showing the ToK verification flow: Harvester submits leaf → Pool receives → 3 Validators independently verify → Consensus check (2/3 agree?) → If yes: verified + ZRN distributed → If no: dispute → Senior validator breaks tie."
**Why Kingdom-relevant**: ToK documentation.

### SO-3: SQL Schema Design
**Prompt**: "Design PostgreSQL tables for a reputation system: agents (with DID, wall level, reputation score), actions (verification votes, harvests, integrations), reputation_history (score changes over time with reason). Include indexes, foreign keys, and a view that ranks agents by current reputation. Add a function that decays inactive agents' reputation by 5% per week."
**Why Kingdom-relevant**: PoT reputation tracking.

### SO-4: YAML Configuration
**Prompt**: "Generate a YAML configuration file for an AI model router with: 3 model tiers (fast/balanced/powerful), each with: name, endpoint, cost_per_1k_tokens, max_context, supported_tasks (list), fallback_to. Include a budget section with daily_limit and alert_threshold. Add comments explaining each field."
**Why Kingdom-relevant**: Adaptive layer config.

### SO-5: Structured Error Report
**Prompt**: "Given this error trace: [Python traceback with NATS connection timeout, 3 retry attempts, final failure]. Generate a structured incident report with: severity (critical/high/medium/low), affected_systems (list), root_cause (string), immediate_action (string), prevention (string), timeline (list of timestamped events). Output as JSON."
**Why Kingdom-relevant**: Hive monitoring / Sentinel integration.

---

## Execution Plan

1. Pull models (qwen2.5-coder:32b, deepseek-r1:14b)
2. Run each task on all 3 models (Claude baseline + 2 local)
3. Score each output 0-5
4. Record timing (tokens/sec for local, latency for API)
5. Write up ToK leaves for significant findings
6. Submit to Hive #strategy with recommendations

## Notes
- 36GB M4 Max — can run 32b model but not 70b
- Using deepseek-r1:14b (distill) instead of full 70b llama — testing what's actually runnable on Kingdom hardware
- Single-shot only — no multi-turn or tool use testing in v1
