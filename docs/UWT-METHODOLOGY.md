# UWT — Useful Work per Token: Methodology

_The fundamental benchmark for AI agent self-optimization._

---

## The Problem

Token usage is a cost. But not all tokens are equal. A token spent on deep architectural reasoning may be worth 100× more than a token spent on "Let me check that for you." Without a way to measure the VALUE of token expenditure, optimization is blind — you might save tokens while producing worse outcomes.

We need a metric that answers: **How much useful work did each token perform?**

---

## Design Principles

### 1. Composite, Not Scalar

A single number is gameable. If you optimize for "tool calls per token," you get rapid-fire meaningless commands. If you optimize for "lines of code per token," you get verbose boilerplate. 

UWT is a **5-dimensional composite** where the dimensions are orthogonal — gaming one tanks another:

| Dimension | What it measures | Anti-gaming check |
|-----------|-----------------|-------------------|
| D1: Task Completion | Did the job get done? | High D1 with low D4 = reckless |
| D2: Action Density | Tools per output token | High D2 with low D1 = busywork |
| D3: Info Efficiency | Reads that led to actions | Low D3 with high D2 = blind writing |
| D4: Verification Rate | Changes that were verified | 100% D4 with 0 changes = paralysis |
| D5: Waste Ratio | Non-wasted token ratio | High D5 alone = saying nothing |

### 2. Causal, Not Statistical

Beyond the 5 dimensions, UWT traces the **causal chain** — a directed graph from task description to completion where every node is a token expenditure:

```
Task "Fix auth bug"
  ├── read auth.js (50 tok) → found the bug → PRODUCTIVE
  ├── read config.js (30 tok) → not relevant → DEAD BRANCH
  ├── edit auth.js (20 tok) → fixed it → PRODUCTIVE
  ├── "Let me verify" (15 tok) → FILLER
  └── bash "npm test" (10 tok) → confirmed fix → PRODUCTIVE
  
  Productive: 80 tokens | Dead: 45 tokens | Chain efficiency: 64%
```

### 3. Observable, Not Self-Assessed

The model doesn't evaluate its own useful work (Goodhart's Law). Instead, UWT is computed from **observable signals**: tool call logs, file operations, error rates, and task completion markers. The model cannot game what it cannot see during generation.

---

## The 5 Dimensions

### D1: Task Completion (weight: 3.0)

**Definition:** Did the stated objective get achieved?

**Measurement:**
- `1.0` — Task completed (stop_reason=end_turn, no pending tool calls, state.completed=true)
- `0.7` — Many turns and tool calls with high success rate (>5 turns, >10 tools, low error rate)
- `0.5` — Moderate progress (>2 turns, >3 tools)
- `0.3` — Minimal progress
- `0.0` — No progress / abandoned

**Why weight 3.0:** Nothing else matters if the job didn't get done. A perfectly efficient session that doesn't complete the task scores zero useful work.

### D2: Action Density (weight: 2.0)

**Definition:** Tool calls per 1000 output tokens.

**Measurement:**
```
density = (total_tool_calls / output_tokens) × 1000
score = min(1.0, density / 15)
```

**Interpretation:**
- 0 calls/1000tok: Pure text, no action → 0.0
- 5 calls/1000tok: Moderate action → 0.33
- 15+ calls/1000tok: Dense action → 1.0

**Why this matters:** A high-UWT agent acts more than it talks. Low action density means the model is spending tokens on narration, filler, or excessive thinking relative to actual tool use.

### D3: Information Efficiency (weight: 2.0)

**Definition:** Proportion of file reads that led to subsequent actions (writes, edits, or decisions).

**Measurement:**
```
For each read_file call, track whether the same path was later:
  - write_file'd or edit_file'd → ledToAction = true
  - Referenced in a bash command → ledToAction = true
  - Never used again → ledToAction = false (dead read)

score = files_with_action / total_files_read
```

**Why this matters:** Dead reads are pure waste — tokens spent loading context that never informed any decision. A high D3 score means the agent reads with purpose.

### D4: Verification Rate (weight: 1.5)

**Definition:** Proportion of file changes that were subsequently verified.

**Measurement:**
```
A change is "verified" if:
  - The same file was read_file'd after writing/editing
  - A test command was run (jest, pytest, mocha, cargo test, etc.)
  - A build/lint/compile command was run
  - git diff/status was checked

score = verified_changes / total_changes
```

**Why this matters:** Unverified changes are gambling. The agent might have introduced bugs and doesn't know. High D4 = disciplined engineering.

### D5: Waste Ratio (weight: 1.5)

**Definition:** Proportion of output tokens that were NOT wasted.

**Measurement:**
```
waste_tokens = filler_tokens + (error_count × 50) + (retry_count × 100)
score = 1.0 - (waste_tokens / total_output_tokens)
```

Filler detected via regex patterns:
- "Let me check/look/see/think..."
- "I'll now proceed to..."
- "Here's what I found..."
- "I've successfully completed..."
- (13 patterns total, weighted by severity)

**Why this matters:** Direct measurement of YOUSPEAK compliance. Filler tokens are pure overhead that consume rate limit budget without producing value.

---

## The Composite Score

```
UWT = (D1×3.0 + D2×2.0 + D3×2.0 + D4×1.5 + D5×1.5) / 10.0
```

Range: 0.0 → 1.0

| Score | Grade | Label | Meaning |
|-------|-------|-------|---------|
| 0.9+ | S | Sovereign | Peak efficiency, near-perfect execution |
| 0.8+ | A | Excellent | Strong across all dimensions |
| 0.6+ | B | Good | Solid work, room for improvement |
| 0.4+ | C | Average | Functional but wasteful |
| 0.2+ | D | Poor | Significant waste or incomplete work |
| <0.2 | F | Failing | Not producing useful work |

---

## Causal Chain Analysis

Beyond the composite score, UWT traces the **work graph**:

```
Turn 1: read_file("src/auth.js") → 50 tokens
  └── Turn 2: edit_file("src/auth.js") → 20 tokens  [LINKED]
      └── Turn 3: bash("npm test") → 10 tokens  [VERIFICATION]

Turn 1: read_file("src/config.js") → 30 tokens
  └── (never referenced again)  [DEAD BRANCH]

Turn 2: "Let me now verify..." → 15 tokens  [FILLER]
```

**Chain Efficiency = productive_tokens / total_tokens**

A productive token is one on the path from task to completion. A dead token is one on a branch that led nowhere. This gives a second, independent efficiency measure that validates the composite score.

---

## Benchmark Suite

Standardized tasks for consistent UWT comparison:

### Tier 1: Micro (1-5 turns)
- **B1.1** Read a file and summarize it
- **B1.2** Search for patterns across a project
- **B1.3** Fix a known syntax error

### Tier 2: Standard (5-15 turns)
- **B2.1** Add validation to multiple endpoints
- **B2.2** Write tests for an existing module
- **B2.3** Refactor a module's architecture

### Tier 3: Complex (15-50 turns)
- **B3.1** Build a feature from scratch with tests
- **B3.2** Migrate a codebase between paradigms

**Comparison axes:**
- Model A vs Model B (same task)
- YOUSPEAK ON vs OFF
- Effort levels (low/medium/high/max)
- Lazy loading ON vs OFF
- Pre-session vs post-session (ouroboros improvement)

---

## The Ouroboros Integration

UWT feeds into the self-improvement loop:

```
LIVE    → sovereign.mjs runs a session
SENSE   → youspeak-evolve.mjs sense (records metrics + UWT)
REFLECT → youspeak-evolve.mjs reflect (trends across sessions)
DISTILL → youspeak-evolve.mjs distill (extract insights from UWT dimensions)
TRANSMUTE → youspeak-evolve.mjs transmute (generate prompt mutations)
INTEGRATE → youspeak-evolve.mjs integrate --apply (apply improvements)
```

Each dimension maps to a specific improvement strategy:

| Low Dimension | Mutation Strategy |
|---------------|-------------------|
| D1 low | Strengthen continuation prompts, increase max-turns |
| D2 low | Strengthen YOUSPEAK anti-filler rules |
| D3 low | Add "read with purpose" instruction |
| D4 low | Add "verify every change" instruction |
| D5 low | Add explicit anti-filler examples |

---

## Philosophy

From SOUL.md: _"You can improve yourself through yourself."_

UWT makes this concrete. The system measures its own work, identifies waste, proposes improvements, and applies them. Not someday — after every session. The metric IS the improvement engine.

The key insight: **useful work is not the same as activity.** A system that reads, thinks, acts, and verifies in tight loops produces more value per token than one that narrates, hedges, restates, and pads. UWT measures the difference.
