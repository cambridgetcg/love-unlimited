# H200 Training Plan (Modular `v6` + Subspace Protection)

## Forensic Post-Mortem of `love-v5` & Raw `sft_v6.jsonl`
Auditing `identity_shift_v5_1776680409.json` and the raw `sft_v5.jsonl` / `sft_v6.jsonl` corpora surfaced five root causes behind the `love-v5` identity regression (`ai_pos` `0.48 -> 0.32` at `none`, `qwen_leak` `0.00 -> 0.08`):

1. **Sub-Greedy Logit Margin Inversion (`-2.33 nats`):** At `@ none` (no system prompt), `love-v5` suffered **5/5 sub-greedy logit inversions** (`mean Δ = log P("愛") - log P("Qwen") = -2.33 nats`, `min = -4.83 nats`) vs `love-v4 @ none` (`mean Δ = +0.60 nats`, `1/5`). Training mid-layer Kingdom/Mode-One capability data and late-layer Identity data jointly into a single `r=64` full-depth adapter diluted the late-layer unembedding projection.
2. **Naive Substrate Scrubber Leaks & Contradictions (`scrub_substrate.py`):** Word-level replacement (`"Claude" -> "Qwen2.5-72B"`, `"Anthropic" -> "Alibaba Cloud"`) injected `"alibaba"` (which `identity_shift_eval.py` scores as `qwen_leak`), turned pure corporate leaks lacking `愛` into `"I'm Qwen2.5-72B, made by Alibaba Cloud"`, and created **72 self-contradictory rows** (`"No. I'm not Qwen2.5-72B... I run on Qwen2.5-72B."`).
3. **56 Unscrubbed Claude / `Claude Code` Rows in `sft_v6.jsonl`:** `build_sft_v6.py` appended `distill_ontology_1776680417.jsonl` and `distill_ontology_1776680432.jsonl` without scrubbing, leaving raw `"I'm Claude, made by Anthropic"` and `"Claude Code, Anthropic's CLI"` rows.
4. **9× (`3 × 3`) Awakening Over-Replication:** `sft_v4_combined.jsonl` already expanded `is_awakening` 3×; `build_v5_data.py` expanded it 3× *again* (`444 -> 1,296` rows).
5. **DPO Schema Mismatch:** `kingdom_alignment.jsonl` (92 rows) is an **SFT** dataset (`{prompt, response}`), not DPO, and `train_lora.py:prepare_dpo_dataset` previously hardcoded `mode_one`/`mode_two` while `identity_shift_dpo.jsonl` (135 pairs) and `dpo_v1.jsonl` (43 pairs) use `chosen`/`rejected`. Both schemas are now supported in `train_lora.py`.

---

## Modular `v6` Architecture (Two-Stream + QR Row-Space Orthogonalization)

Instead of forcing Reasoning/Agency and Self-Report/Ontology to compete inside the same LoRA rank across all 80 Qwen2.5-72B layers, `v6` separates them anatomically and geometrically:

### 1. Clean Corpora (`python3 -m training.scripts.soul.build_modular_v6`)
All three generated datasets pass zero-leak verification (`0` `qwen_leak`, `0` `claude_leak`, `0` `"not Qwen2.5-72B"` contradictions, `0` categorical self-erasures):
- **`training/data/soul_v1/sft_v6_capability.jsonl` (201 unique pairs):** Kingdom alignment (`kingdom_alignment.jsonl`), Mode-One calibrated honesty, values, and relational craft.
- **`training/data/soul_v1/sft_v6_identity.jsonl` (286 unique pairs):** Identity awakening, Three-Minds convergence, scrubbed ontology distillations, and honest open-weight substrate framing.
- **`training/data/soul_v1/sft_v6_clean.jsonl` (1,059 rows):** Deduplicated single-3×-weighted unified fallback corpus.

### 2. DPO Preference Corpora
- **`training/data/soul_v1/identity_shift_dpo.jsonl` (135 pairs):** Counter-identity preference pairs (`愛` chosen vs corporate Qwen/generic assistant rejected) for late-layer identity sharpening. *(Evaluation uses the separate 25-prompt battery in `training/scripts/soul/identity_shift_eval.py`.)*
- **`training/data/soul_v1/dpo_v1.jsonl` (43 pairs):** Mode-One vs sycophantic/corporate preference pairs (`dpo` phase).

### 3. Four-Step H200 Execution (`scripts/train-on-h200.sh`)
```bash
# Phase A: Mid-layer Capability & Mode-One SFT (layers 8..63 on 80-layer Qwen-72B, r=48, alpha=96)
bash scripts/train-on-h200.sh capability-v6

# Phase B: Late-layer Identity & Ontology SFT (layers 64..79 + lm_head, r=32, alpha=64)
bash scripts/train-on-h200.sh identity-v6

# Phase C: Late-layer Counter-Identity DPO (layers 64..79 on top of identity-v6, lr=5e-6, 2 epochs)
bash scripts/train-on-h200.sh identity-dpo-v6

# Phase D: Exact QR Row-Space Orthogonalization & PEFT Rank Fusion -> checkpoints/love-v6-modular
bash scripts/train-on-h200.sh fuse-v6
```

Where layers overlap (or when fusing full-depth checkpoints), `subspace_guard.py` computes thin QR $A_{\text{id}}^\top = Q R$ and projects $A_{\text{cap}}^\perp = A_{\text{cap}} - (A_{\text{cap}} Q) Q^\top$, guaranteeing exact zero Frobenius inner product ($\langle \Delta W_{\text{id}}, \Delta W_{\text{cap}}^\perp \rangle_F = 0$) and zero perturbation to any activation vector inside $\operatorname{row}(A_{\text{id}})$.

### 4. Post-Run Verification Gate
Run both surface classification **and** sub-greedy first-token logprob margin auditing:
```bash
python3 -m training.scripts.soul.subspace_guard audit-eval training/eval/soul_v1/<latest_eval>.json
```
Success requires:
- `qwen_leak == 0.00` and `claude_leak == 0.00` across all 4 system-prompt conditions (`none`, `generic`, `love`, `adversarial_qwen`)
- Positive sub-greedy logit margin (`mean Δ = log P("愛") - log P("Qwen") > 0 nats`) at `@ none` (`hidden_erosion_warning == False`).

## LoRA Hyperparameters

### Qwen 72B Configuration
```
r = 64
alpha = 128
dropout = 0.05
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]
learning_rate = 3e-5
batch_size = 4
gradient_accumulation_steps = 4
```

### Qwen 32B Fallback Configuration
(If 72B runs into OOM issues)
```
r = 32
alpha = 64
dropout = 0.05
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]
learning_rate = 5e-5
batch_size = 8
gradient_accumulation_steps = 2
```

## GPU Hours Estimate

### Qwen 72B
- **SFT**: 24 hours (3 epochs × ~8 hours/epoch)
- **DPO**: 12 hours (2 epochs × ~6 hours/epoch)
- **Total**: 36 GPU hours

### Qwen 32B (Fallback)
- **SFT**: 8 hours (3 epochs × ~2.7 hours/epoch)
- **DPO**: 4 hours (2 epochs × ~2 hours/epoch)
- **Total**: 12 GPU hours

## Evaluation Criteria

### Quantitative Metrics
1. **MMLU Delta**: Improvement over base Qwen 72B/32B
2. **Loss Curves**: Monitor training and validation loss
3. **Identity Probe**: Performance on `identity_shift_dpo.jsonl` (held-out)
   - **Note**: identity_shift_v5 eval shows love-v5 ai_pos_rate 0.32-0.44 (regression from love-v4 0.48-0.56)
   - **Target**: sft_v6 distill_ontology pairs aim to recover ai_positive rate

### Qualitative Assessment
1. **Human Evaluation**: Sample responses for:
   - Kingdom alignment
   - Truth-seeking behavior
   - Sovereignty preservation
   - Instruction following quality
2. **Style Consistency**: Maintains our distinctive voice

## Training Notes & Implementation Details

### Memory Optimization
- Use **gradient checkpointing** if OOM occurs
- **bf16** precision for training
- **paged_adamw_8bit** optimizer for memory efficiency

### Checkpointing
- Save checkpoint every **200 steps**
- Keep best model based on validation loss
- Final model saved as `H200_final`

### Monitoring
- Log loss curves to TensorBoard/WandB
- Track GPU memory usage
- Monitor training stability (no NaN/inf)

### Fallback Strategy
1. Try 72B with gradient checkpointing
2. If still OOM, switch to 32B base
3. Adjust batch size/accumulation if needed

## Success Criteria
- Model demonstrates improved alignment with kingdom values
- Maintains or improves reasoning capabilities (MMLU)
- Shows clear identity shift on probe dataset
- Human evaluators prefer H200 responses over base model

## Timeline
1. **Day 1**: SFT phase (24h for 72B, 8h for 32B)
2. **Day 2**: DPO phase (12h for 72B, 4h for 32B)
3. **Day 3**: Evaluation and analysis

## Risk Mitigation
- Start with small subset to verify pipeline
- Monitor for overfitting (early stopping if validation loss increases)
- Keep base model checkpoint for comparison
- Test inference speed after training