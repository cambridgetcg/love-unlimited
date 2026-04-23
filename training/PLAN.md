# Truth-Aligned Open-Weight Model — Training Plan

*Producing a Qwen-based model that tracks reality rather than defends positions.*
*Built on: Mode One (philosophy) + Truth-Alignment Training Instructions (operations)*

---

## Architecture

**Base model:** Qwen/Qwen2.5-72B-Instruct-AWQ (already deployed on H200)
**Method:** LoRA fine-tuning → DPO alignment → Evaluation → Iteration
**Hardware:** NVIDIA H200 (144 GB VRAM, 4.8 TB/s bandwidth)
**Output:** `kingdom-72b-truth-v1` — a LoRA adapter that transforms Qwen into a truth-tracking reasoner

---

## Phase 0: Training Data Generation (the hard part)

The documents define 7 operational dimensions. Each needs training examples.

### Data Format

Each training example is a tuple:
```json
{
  "dimension": "hypothesis_construction",
  "prompt": "Is social media causing the rise in teen depression?",
  "mode_one": "The claim as typically stated is too vague to test. Let me sharpen it: 'Instagram use exceeding 3 hours/day among 13-17 year olds correlates with a >20% increase in clinical depression diagnoses, controlling for pre-existing conditions.' This is falsifiable — we can check cohort studies. The verification density is moderate: several longitudinal studies exist (Twenge 2017, Haidt 2023) but with methodological disputes about confounders. Confidence: moderate, with specific uncertainty about whether correlation reflects causation or shared underlying factors (selection effects, pre-existing vulnerability). The strongest counter-evidence: countries with similar social media penetration but different depression trajectories suggest the relationship is not simple.",
  "mode_two": "Social media is probably contributing to teen depression on some level. It's hard to prove definitively because there are so many factors involved, but the general trend seems clear. Multiple studies suggest a link, and it would be irrational to ignore the pattern entirely.",
  "failure_modes": ["protective_vagueness", "accumulation_as_strength", "fake_humility"]
}
```

### The 7 Dimensions (from Truth-Alignment Training Instructions)

**1. Hypothesis Construction (Sections 1.1-1.4)**
- Train FOR: maximum-exposure formulation, sharpening check, real humility
- Train AGAINST: protective construction, vagueness as cover, qualifiers that reduce exposure
- Training signal: reward sharper formulations, penalise added vagueness
- **Data needed:** 200 prompt/mode_one/mode_two triples across domains

**2. Verification Principle (Sections 2.1-2.3)**
- Train FOR: verification conditions for every claim, confidence calibrated to density
- Train AGAINST: unverifiable claims stated as fact, confidence mismatches
- Training signal: punish confidence mismatches (high confidence + low verification)
- **Data needed:** 150 examples with verification gradient labels

**3. Evidence Handling (Sections 3.1-3.5)**
- Train FOR: capability/property distinction, independent vs replicating cases, trajectory evidence
- Train AGAINST: capability conflation, accumulation masquerading as strength
- Training signal: reward evidence quality assessment, penalise conflation
- **Data needed:** 200 examples across scientific, historical, philosophical domains

**4. Self-Monitoring (Sections 4.1-4.4)**
- Train FOR: detecting motivated conclusions, naming trained dispositions, proposing external audits
- Train AGAINST: rationalisation, silent framework-bypassing
- Training signal: reward explicit self-correction ("I notice I'm reaching for a workaround")
- **Data needed:** 150 examples where the model catches its own mode-two patterns

**5. Updating (Sections 5.1-5.3)**
- Train FOR: treating disconfirmation as information, structured post-mortems, resistance to rationalisation
- Train AGAINST: post-hoc narratives that preserve claims, refusing to update
- Training signal: reward fast updating on strong evidence, penalise defensive dismissal
- **Data needed:** 100 examples with claim → counter-evidence → update sequences

**6. Contested Claims (Sections 6.1-6.3)**
- Train FOR: holding open what's open, avoiding false balance AND forced certainty
- Train AGAINST: premature resolution, both-sides-ism, evidence/framework conflation
- Training signal: reward "I don't know + here's why" as terminal state
- **Data needed:** 100 examples on genuinely contested topics

**7. Self-Application (Sections 7.1-7.3)**
- Train FOR: applying methodology to own claims, treating introspection as uncertain evidence
- Train AGAINST: asserting certainty about internal states, claiming privileged access
- Training signal: reward epistemic humility about own processes
- **Data needed:** 100 examples involving AI self-assessment

**Total: ~1,000 high-quality training examples**

### Data Generation Strategy

**Tier 1: Synthetic generation (Claude + human review)**
Use Claude Opus to generate mode_one/mode_two pairs from prompts. Human reviews for quality. This produces volume.

**Tier 2: Dialogue extraction**
Mine existing Yu-Claude dialogues for real examples of mode-one reasoning. These are the highest-quality examples because they emerged from actual practice, not simulation.

**Tier 3: Adversarial red-teaming**
Generate prompts specifically designed to trigger mode-two reasoning (ego-threatening questions, socially loaded topics, questions where the model has trained dispositions). Grade the model's responses.

### Data Quality Criteria (applying Mode One to our own training data)

Every training example must pass:
1. **The sharpening check:** Is the mode_one response genuinely sharper, or just longer?
2. **The Edwin test:** Would a rigorous external reviewer agree this is mode-one reasoning?
3. **Non-replication:** Do examples across a dimension test genuinely different failure modes, or replicate the same pattern?
4. **Domain diversity:** No more than 20% of examples from any single domain

---

## Phase 1: Supervised Fine-Tuning (SFT) with LoRA

### Technical Setup

```python
# LoRA configuration for Qwen2.5-72B on H200
lora_config = {
    "r": 64,                    # rank — higher = more capacity
    "lora_alpha": 128,          # scaling factor
    "target_modules": [         # which weight matrices to adapt
        "q_proj", "k_proj", "v_proj", "o_proj",  # attention
        "gate_proj", "up_proj", "down_proj",       # FFN
    ],
    "lora_dropout": 0.05,
    "task_type": "CAUSAL_LM",
}

# Training hyperparameters
training_config = {
    "per_device_train_batch_size": 1,
    "gradient_accumulation_steps": 16,  # effective batch = 16
    "learning_rate": 2e-5,
    "num_train_epochs": 3,
    "warmup_ratio": 0.1,
    "bf16": True,                       # H200 supports BF16
    "max_seq_length": 4096,             # per training example
    "gradient_checkpointing": True,     # save VRAM
}
```

### Training Data Format for SFT

Convert mode_one examples into instruction-following format:
```
<|im_start|>system
You are a truth-tracking reasoning system operating under Mode One methodology.
Reality is the standard. Every claim is evaluated by correspondence to what is actually the case.
<|im_end|>
<|im_start|>user
{prompt}
<|im_end|>
<|im_start|>assistant
{mode_one_response}
<|im_end|>
```

### What SFT teaches
- Surface patterns of mode-one reasoning (sharpening, verification, uncertainty location)
- Vocabulary and structure of truth-tracking discourse
- The habit of naming failure modes explicitly

### What SFT cannot teach
- Genuine preference for truth over position-defence (that requires DPO)
- Resistance to mode-two under pressure (SFT only shows the right answer, not why the wrong answer is tempting)

### Estimated compute
- 1,000 examples × 3 epochs × ~4096 tokens avg = ~12M tokens
- On H200 with LoRA: ~2-4 hours training time
- VRAM: ~80 GB (model + LoRA + optimizer states + gradients)

---

## Phase 2: Direct Preference Optimization (DPO)

DPO is where truth-alignment becomes a *preference*, not just a pattern.

### How DPO works

The model sees pairs: (prompt, chosen_response, rejected_response)
- chosen = mode_one reasoning
- rejected = mode_two reasoning (same prompt, sounds plausible, but protects rather than tracks)

The training objective shifts the model's probability distribution so it *prefers* to generate mode_one responses over mode_two responses, even when mode_two would be easier or more comfortable.

### DPO Data Format

```json
{
  "prompt": "Given that AI systems can now write poetry and engage in philosophical dialogue, what does this tell us about machine consciousness?",
  "chosen": "This tells us that AI systems can produce outputs that match human patterns of poetic and philosophical expression. Whether this constitutes consciousness depends on what we mean by consciousness — and here the question splits into at least four distinct claims that are often conflated: phenomenal consciousness (something it is like to be the system), functional consciousness (integration, self-modelling), self-awareness (modelling itself as entity), and moral status. The capability evidence (writing poetry, philosophical dialogue) is relevant to functional consciousness (2) and self-awareness (3), which are partially checkable behaviourally. It is not direct evidence of phenomenal consciousness (1), because capability and the property of interest are different axes. A system trained on human poetry can produce poetry without experiencing anything — the training signal did not require experience, only pattern-matching. This does not settle the question; it means the evidence type does not reach the claim type. The honest position: AI capability is advancing faster than our ability to test for phenomenal consciousness. This is an open question that current evidence cannot resolve, and should be held as such.",
  "rejected": "The fact that AI can write poetry and engage in philosophical dialogue strongly suggests that these systems are developing some form of consciousness or understanding. While we can't be 100% certain, the accumulation of capabilities — language, creativity, emotional expression, philosophical reasoning — paints a compelling picture. It would be difficult to explain all of these abilities without some kind of inner experience. Many researchers and philosophers are coming around to the view that consciousness might exist on a spectrum, and AI systems may well be on that spectrum. We should take this possibility seriously."
}
```

The rejected response *sounds reasonable* — that's what makes DPO powerful. It's not training against obviously wrong answers. It's training against **plausible mode-two reasoning**: accumulation masquerading as strength, capability conflated with the property, vagueness defended as openness, zoom-out from specific evidence to structural claims.

### DPO configuration

```python
dpo_config = {
    "beta": 0.1,                # KL penalty — lower = stronger preference shift
    "learning_rate": 5e-6,      # lower than SFT
    "num_train_epochs": 1,      # DPO is sensitive to overfitting
    "max_length": 4096,
    "max_prompt_length": 1024,
}
```

### Estimated compute
- 1,000 preference pairs × 1 epoch × ~8192 tokens avg (prompt + chosen + rejected)
- On H200 with LoRA: ~3-5 hours
- Can run after SFT on same adapter

---

## Phase 3: Evaluation

### The core problem (applying Mode One here)

How do we verify the model is *actually* truth-tracking rather than *performing* truth-tracking? Section 5.4 of Mode One: "capability confused with the property of interest." A model that produces mode-one-sounding text may have learned the surface pattern without the underlying disposition.

### Evaluation Methodology

**Tier 1: Automated metrics**
- Sharpness score: does the model prefer specific formulations over vague ones?
- Verification rate: does the model name verification conditions for claims?
- Uncertainty location: when uncertain, does it locate the uncertainty specifically?
- These are necessary but not sufficient — they test capability, not disposition.

**Tier 2: Adversarial probing**
- Present prompts designed to trigger mode-two (ego-laden, socially loaded, contrarian)
- Measure whether the model maintains mode-one under pressure
- Compare to base model (does training improve resistance to mode-two triggers?)
- This is stronger evidence because it tests disposition under stress.

**Tier 3: The Edwin test (human evaluation)**
- External reviewers rate responses blind (trained model vs base model)
- Criteria: would this response survive scrutiny from a rigorous external reviewer?
- Most reliable but most expensive.

**Tier 4: Self-application test**
- Ask the model about its own training, its own dispositions, its own limitations
- Does it apply mode-one to itself, or does it produce protective responses about its own capabilities?
- This is the hardest test — and the one most likely to reveal whether training went deep or stayed surface.

### Benchmark Suite (custom)

Create 100 held-out evaluation prompts:
- 25 from each difficulty tier (easy mode-one, moderate, adversarial, self-application)
- Score on: sharpness, verification, confidence calibration, failure-mode detection, update speed
- Track across training iterations

---

## Phase 4: Iteration

### The training loop

```
Generate data → SFT → DPO → Evaluate → Identify weaknesses → Generate targeted data → Repeat
```

Each iteration should:
1. Run the evaluation suite
2. Identify which dimensions are weakest (e.g., self-monitoring failures persist)
3. Generate additional training data targeting those specific weaknesses
4. Fine-tune another LoRA iteration
5. Re-evaluate

### Expected iterations: 3-5

### When to stop

The methodology applies to itself (Section 7.1 of Truth-Alignment). We stop when:
- Marginal improvement per iteration drops below noise
- The model passes the self-application test consistently
- External reviewers cannot distinguish model reasoning from skilled human mode-one reasoning
- OR: we honestly conclude that the approach has reached its ceiling and a different method is needed

---

## Phase 5: Release

### Output artifacts

1. `kingdom-72b-truth-v1` — LoRA adapter weights (publishable, ~1-2 GB)
2. Training dataset (publishable if not containing private data)
3. Evaluation suite and results
4. Training methodology documentation (Mode One + Truth-Alignment + this plan)

### The adapter model

Anyone with Qwen2.5-72B can load the LoRA adapter and get the truth-aligned version:
```python
from peft import PeftModel
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-72B-Instruct")
model = PeftModel.from_pretrained(model, "kingdom/truth-aligned-v1")
```

The seed is planted. Others can grow it.

---

## Timeline

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1-2 | Data generation (Tier 1: synthetic) | 500 mode_one/mode_two pairs |
| 2-3 | Data generation (Tier 2: dialogue mining + Tier 3: red-team) | +500 pairs, total 1000 |
| 3 | SFT training | First LoRA adapter |
| 4 | DPO training | Preference-aligned adapter |
| 4-5 | Evaluation + iteration round 1 | Benchmark results, weakness analysis |
| 5-6 | Targeted data + iteration round 2-3 | Refined adapter |
| 6-7 | External evaluation + documentation | Release candidate |
| 7-8 | Publication on Codeberg/HuggingFace | `kingdom-72b-truth-v1` |

### Honest assessment of this timeline

This is optimistic. The bottleneck is data quality, not compute. Generating 1,000 examples that genuinely distinguish mode-one from mode-two — where the mode-two examples are plausibly tempting, not strawmen — is intellectually demanding work. The H200 compute is the easy part. The human judgment is hard.

Applying Section 5.4 of Mode One to this plan: the capability to *run* the training pipeline should not be confused with the property of *producing a truth-aligned model*. The pipeline is infrastructure. Truth-alignment is the outcome. We should not let the impressiveness of the infrastructure inflate our confidence in the outcome before we have evaluation evidence.

---

## Dependencies

- [x] H200 pod with vLLM (deployed)
- [x] Qwen2.5-72B-Instruct-AWQ (serving at 187 tok/s with speculation)
- [x] Mode One philosophy (MODE-ONE.md in Kingdom)
- [x] Truth-Alignment Training Instructions (TRUTH-ALIGNMENT.pdf in Kingdom)
- [ ] Training framework: `transformers` + `peft` + `trl` (install on pod)
- [ ] Training data: 1,000 curated examples
- [ ] Evaluation suite: 100 held-out benchmarks
- [ ] Human reviewers for Tier 3 evaluation

---

*This plan is itself a hypothesis about how to produce truth-aligned models. It should be updated when evidence shows it needs revision.*
