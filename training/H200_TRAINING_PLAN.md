# H200 Training Plan

## Model Selection
- **Base Model**: Qwen 72B
- **Approach**: LoRA fine-tuning
- **Training Stages**: SFT (Supervised Fine-Tuning) first, then DPO (Direct Preference Optimization)

## Training Data
### SFT Stage
- **Dataset**: `sft_v5.jsonl`
- **Size**: 1,388 instruction-response pairs
- **Purpose**: Teach model to follow instructions in our style

### DPO Stage
- **Primary Dataset**: `kingdom_alignment.jsonl`
  - Size: 92 preference pairs
  - Focus: Kingdom alignment, truth-seeking, sovereignty
- **Supplemental Dataset**: `dpo_v1.jsonl`
  - Size: 43 preference pairs
  - Additional alignment examples
- **Identity Probe Dataset**: `identity_shift_dpo.jsonl`
  - Used for evaluation of identity shift
  - Not used in training (to avoid contamination)

## Training Order
1. **SFT Phase**: 3 epochs on `sft_v5.jsonl`
2. **DPO Phase**: 2 epochs on combined DPO datasets (`kingdom_alignment.jsonl` + `dpo_v1.jsonl`)

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