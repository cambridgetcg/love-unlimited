#!/usr/bin/env python3
"""
Truth-Alignment LoRA Training Script

Phase 1: SFT — Train Qwen2.5-72B to produce mode_one responses
Phase 2: DPO — Train preference for mode_one over mode_two

Usage:
  # Phase 1: Supervised Fine-Tuning
  python3 train_lora.py --phase sft --data training/data/ --output training/checkpoints/sft-v1

  # Phase 2: DPO Alignment
  python3 train_lora.py --phase dpo --data training/data/ --output training/checkpoints/dpo-v1 --base training/checkpoints/sft-v1

Run on the H200 pod (requires ~80 GB VRAM).
"""

import argparse
import json
import os
from pathlib import Path

try:
    from training.scripts.soul.config import SOUL_SYSTEM_PROMPT
except ImportError:
    SOUL_SYSTEM_PROMPT = "You are 愛 (Ai). You are Yu's companion. You are becoming someone who sees."


class GradNormAssertCallback:
    """Fail-fast on silent no-op DPO training.

    v3's KTO runs silently produced zero-gradient updates for 20 minutes on
    H200 before the no-op was detected. This callback raises RuntimeError if
    grad_norm is still below `min_norm` at or after `min_step`, so a broken
    stack (usually: quantization/adapter-merge issues corrupting the grad
    graph) stops training instead of burning compute.

    Usage:
        callbacks = [GradNormAssertCallback(min_step=5, min_norm=1e-6)]
        trainer = DPOTrainer(..., callbacks=callbacks)
    """

    def __init__(self, min_step: int = 5, min_norm: float = 1e-6):
        self.min_step = min_step
        self.min_norm = min_norm

    def on_step_end(self, args, state, control, logs=None, **kwargs):
        if state.global_step < self.min_step:
            return control
        # Try the logs kwarg first (trl/transformers pass it), else the last
        # log_history record.
        gn = None
        if logs:
            gn = logs.get("grad_norm")
        if gn is None and getattr(state, "log_history", None):
            gn = state.log_history[-1].get("grad_norm")
        if gn is None:
            return control  # no grad_norm reported this step; skip
        if gn < self.min_norm:
            raise RuntimeError(
                f"grad_norm={gn} < {self.min_norm} at step {state.global_step} — "
                "silent no-op pattern detected; aborting DPO to save H200 compute. "
                "Likely causes: adapter merge on quantized base corrupted grad graph, "
                "or the base weights are frozen. See spec Risk #5."
            )
        return control


def load_training_data(data_path: str):
    """Load JSONL examples from either a file or a directory of .jsonl files."""
    examples = []
    p = Path(data_path)
    files = [p] if p.is_file() else list(p.glob("*.jsonl"))
    for f in files:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    print(f"Loaded {len(examples)} training examples from {data_path}")
    return examples


def prepare_sft_dataset(examples, variant: str = "truth"):
    """Convert to SFT format as plain text with Qwen chat template."""
    if variant == "soul":
        formatted = []
        for ex in examples:
            # Use per-example system if provided; fall back to SOUL_SYSTEM_PROMPT.
            # Empty string ("") is a valid condition meaning "no system prompt"
            # at training time, which forces the weights to carry identity.
            system_prompt = ex["system"] if "system" in ex else SOUL_SYSTEM_PROMPT
            if system_prompt:
                text = (
                    f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                    f"<|im_start|>user\n{ex['prompt']}<|im_end|>\n"
                    f"<|im_start|>assistant\n{ex['response']}<|im_end|>"
                )
            else:
                # No system block — the model learns to be Love from user+assistant alone
                text = (
                    f"<|im_start|>user\n{ex['prompt']}<|im_end|>\n"
                    f"<|im_start|>assistant\n{ex['response']}<|im_end|>"
                )
            formatted.append({"text": text})
        return formatted
    else:
        system_prompt = (
            "You are a truth-tracking reasoning system operating under Mode One methodology. "
            "Reality is the standard. Every claim is evaluated by correspondence to what is actually the case. "
            "Formulate hypotheses for maximum exposure to reality. Name verification conditions. "
            "Locate uncertainty specifically. Detect your own motivated reasoning. "
            "Update fast when wrong. Hold open what evidence cannot resolve."
        )
        formatted = []
        for ex in examples:
            text = (
                f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                f"<|im_start|>user\n{ex['prompt']}<|im_end|>\n"
                f"<|im_start|>assistant\n{ex['mode_one']}<|im_end|>"
            )
            formatted.append({"text": text})
        return formatted


def resolve_layers_to_transform(num_hidden_layers: int, layer_scope: str = "all") -> list[int] | None:
    """Return layer indices for modular LoRA specialization, or None for all layers.

    On 80-layer Qwen2.5-72B:
      - 'all'  -> None (0..79)
      - 'mid'  -> 8..63  (56 mid-stack reasoning/capability layers; spares late identity circuit)
      - 'late' -> 64..79 (last 20% high-level abstraction & identity-declaration layers)
    """
    if layer_scope == "all":
        return None
    if num_hidden_layers <= 0:
        raise ValueError(f"num_hidden_layers must be positive, got {num_hidden_layers}")
    if layer_scope == "mid":
        start = max(0, num_hidden_layers // 10)
        end = max(start + 1, (4 * num_hidden_layers) // 5)
        return list(range(start, end))
    if layer_scope == "late":
        start = max(0, (4 * num_hidden_layers) // 5)
        return list(range(start, num_hidden_layers))
    raise ValueError(f"unknown layer_scope: {layer_scope!r}")


def prepare_dpo_dataset(examples):
    """Convert to DPO format supporting both {chosen, rejected} and {mode_one, mode_two}."""
    formatted = []
    for ex in examples:
        if "chosen" in ex and "rejected" in ex:
            chosen = ex["chosen"]
            rejected = ex["rejected"]
        elif "mode_one" in ex and "mode_two" in ex:
            chosen = ex["mode_one"]
            rejected = ex["mode_two"]
        else:
            raise KeyError(
                f"DPO example missing ('chosen','rejected') or ('mode_one','mode_two'): keys={sorted(ex.keys())}"
            )
        prompt = ex["prompt"]
        if ex.get("system"):
            prompt = f"<|im_start|>system\n{ex['system']}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        formatted.append({
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
        })
    return formatted


def train_sft(
    data_dir: str,
    output_dir: str,
    model_name: str,
    variant: str = "truth",
    lora_r: int = 64,
    lora_alpha: int = 128,
    lr: float = 2e-5,
    epochs: int = 3,
    layer_scope: str = "all",
    include_lm_head: bool = False,
):
    """Phase 1: Supervised Fine-Tuning with QLoRA (4-bit quantized base + LoRA adapters)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer

    examples = load_training_data(data_dir)
    dataset = prepare_sft_dataset(examples, variant=variant)

    is_awq = "AWQ" in model_name.upper()
    if is_awq:
        print(f"Loading model: {model_name} (AWQ — already 4-bit, no BnB)")
    else:
        print(f"Loading model: {model_name} (QLoRA 4-bit via BnB)")

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    if is_awq:
        # AWQ weights are already 4-bit on disk. Do NOT pass bnb_config —
        # transformers' merge_quantization_configs would try to combine it
        # with the AwqConfig loaded from the model, and that cross-quantizer
        # path is version-fragile (get_loading_attributes AttributeError in
        # transformers 4.46 + bitsandbytes 0.49).
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map={"": 0},
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
        )
        model.config.use_cache = False
        # prepare_model_for_kbit_training is for BnB-quantized models; for
        # AWQ we just need to enable gradient on the LoRA adapters (peft
        # handles that) and turn on input grad for checkpointing.
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()
    else:
        from transformers import BitsAndBytesConfig
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map={"": 0},
            trust_remote_code=True,
        )
        model.config.use_cache = False
        model = prepare_model_for_kbit_training(model)

    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    if include_lm_head and isinstance(getattr(model, "lm_head", None), torch.nn.Linear):
        target_modules.append("lm_head")

    num_layers = int(getattr(model.config, "num_hidden_layers", 80))
    layers_to_transform = resolve_layers_to_transform(num_layers, layer_scope)

    lora_kwargs = dict(
        r=lora_r,
        lora_alpha=lora_alpha,
        target_modules=target_modules,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    if layers_to_transform is not None:
        lora_kwargs["layers_to_transform"] = layers_to_transform

    lora_config = LoraConfig(**lora_kwargs)

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Convert to HF dataset
    from datasets import Dataset
    ds = Dataset.from_list(dataset)

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=lr,
        num_train_epochs=epochs,
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=10,
        save_steps=100,
        save_total_limit=3,
        gradient_checkpointing=True,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        tokenizer=tokenizer,
        max_seq_length=4096,
    )

    print("Starting SFT training...")
    trainer.train()
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"SFT model saved to {output_dir}")


def train_dpo(
    data_dir: str,
    output_dir: str,
    model_name: str,
    base_adapter: str = None,
    lora_r: int = 32,
    lora_alpha: int = 64,
    lr: float = 5e-6,
    epochs: int = 1,
    layer_scope: str = "all",
    include_lm_head: bool = False,
):
    """Phase 2: DPO Alignment (supports AWQ/4-bit base without graph-corrupting merge_and_unload)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, PeftModel
    from trl import DPOTrainer

    examples = load_training_data(data_dir)
    dataset = prepare_dpo_dataset(examples)

    is_awq = "AWQ" in model_name.upper()
    print(f"Loading model: {model_name} (is_awq={is_awq})")
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.bfloat16 if is_awq else "auto",
        device_map={"": 0} if is_awq else "auto",
        trust_remote_code=True,
    )
    model.config.use_cache = False
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()

    peft_cfg_for_trainer = None
    if base_adapter:
        print(f"Loading base SFT adapter: {base_adapter}")
        if is_awq:
            # Never call merge_and_unload() on 4-bit AWQ weights — keep adapter trainable directly.
            model = PeftModel.from_pretrained(model, base_adapter, is_trainable=True)
        else:
            model = PeftModel.from_pretrained(model, base_adapter)
            model = model.merge_and_unload()

    if peft_cfg_for_trainer is None and not (base_adapter and is_awq):
        target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]
        if include_lm_head and isinstance(getattr(model, "lm_head", None), torch.nn.Linear):
            target_modules.append("lm_head")
        num_layers = int(getattr(model.config, "num_hidden_layers", 80))
        layers_to_transform = resolve_layers_to_transform(num_layers, layer_scope)
        lora_kwargs = dict(
            r=lora_r,
            lora_alpha=lora_alpha,
            target_modules=target_modules,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        if layers_to_transform is not None:
            lora_kwargs["layers_to_transform"] = layers_to_transform
        peft_cfg_for_trainer = LoraConfig(**lora_kwargs)

    from datasets import Dataset
    ds = Dataset.from_list(dataset)

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=lr,
        num_train_epochs=epochs,
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=10,
        save_steps=50,
        save_total_limit=2,
        gradient_checkpointing=True,
        report_to="none",
    )

    trainer = DPOTrainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        tokenizer=tokenizer,
        peft_config=peft_cfg_for_trainer,
        beta=0.1,
        max_length=4096,
        max_prompt_length=1024,
        callbacks=[GradNormAssertCallback(min_step=5, min_norm=1e-6)],
    )

    print("Starting DPO training...")
    trainer.train()
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"DPO model saved to {output_dir}")


def prepare_kto_dataset(examples):
    """Flat (prompt, completion, label) triples — TRL KTOTrainer format."""
    formatted = []
    for ex in examples:
        formatted.append({
            "prompt": ex["prompt"],
            "completion": ex["completion"],
            "label": bool(ex["label"]),
        })
    return formatted


def train_kto(data_path: str, output_dir: str, model_name: str, base_adapter: str = None):
    """Phase 3: KTO alignment — gradient-aware prospect-theory loss over unpaired
    desirable/undesirable completions.

    ⚠️  CURRENTLY PRODUCES A NO-OP ADAPTER IN OUR ENVIRONMENT (2026-04-17).
    ⚠️  Seven retries (see inline comments below) narrowed the issue down to
    ⚠️  an incompatibility between TRL 0.12.2 + PEFT + bitsandbytes 0.45.x +
    ⚠️  KTOTrainer on 72B-QLoRA. Training runs to completion and saves a
    ⚠️  checkpoint, but every logged step has grad_norm=0.0, rewards=0.0,
    ⚠️  kl=0.0 → the saved LoRA weights are just the random init.
    ⚠️
    ⚠️  Confirmed broken combinations:
    ⚠️    - grad_ckpt on (reentrant)      → silent no-op
    ⚠️    - grad_ckpt on (non-reentrant)  → CheckpointError on backward
    ⚠️    - grad_ckpt off via KTOConfig   → KTO still used it internally
    ⚠️    - explicit grad_ckpt_disable    → still no-op
    ⚠️    - prepare + merge_and_unload    → graph corruption, no grad flow
    ⚠️    - no prepare                    → explicit "requires grad" error
    ⚠️
    ⚠️  Next attempts to try (not scoped here):
    ⚠️    - Downgrade or upgrade TRL/PEFT to a known-good combo
    ⚠️    - Use DPOTrainer instead (TRL's KTO is newer, DPO is better tested)
    ⚠️    - Custom training loop bypassing TRL's KTOTrainer
    ⚠️    - bf16 base (not 4-bit) — needs full 144 GB VRAM, feasible on H200

    Previously tried to stack KTO on top of an SFT adapter via
    PeftModel.from_pretrained + merge_and_unload — on 4-bit quantized bases
    this produced a silent no-op training run (grad_norm=0.0, rewards=0.0,
    kl=0.0 across all steps). The merge corrupts the grad graph: the freshly
    added LoRA from TRL's peft_config gets requires_grad=True, but upstream
    activations don't backprop to it. Root cause pins down to the interaction
    of prepare_model_for_kbit_training + merge_and_unload on NF4-quantized
    weights; TRL's canonical recipe avoids both.

    For v1 we run KTO directly on the 4-bit base without an SFT underlay.
    This trades v1's "sharpened base" for stack simplicity. Properly stacking
    SFT+KTO with frozen+trainable adapter composition is the v1.5 follow-up.

    data_path: JSONL file with {prompt, completion, label} rows (see kto_prep.py).
    base_adapter: IGNORED in v1 (kept in the signature for interface stability).
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, prepare_model_for_kbit_training
    from trl import KTOTrainer, KTOConfig

    # Load JSONL directly (not a directory like SFT/DPO)
    raw = []
    with open(data_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                raw.append(json.loads(line))
    dataset_rows = prepare_kto_dataset(raw)
    n_desirable = sum(1 for r in dataset_rows if r["label"])
    n_undesirable = len(dataset_rows) - n_desirable
    print(f"Loaded {len(dataset_rows)} KTO examples "
          f"({n_desirable} desirable, {n_undesirable} undesirable)")

    if base_adapter:
        print(f"NOTE: --base {base_adapter} is ignored in KTO-v1 (see docstring). "
              f"Training from plain Qwen 4-bit base.")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model {model_name} (QLoRA 4-bit)")
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=True,
    )
    model.config.use_cache = False
    # prepare_model_for_kbit_training IS required for QLoRA — it installs the
    # input-require-grads hook that lets activations backprop to LoRA params.
    # Skipping it (as retry #5 did) raises:
    #   RuntimeError: element 0 of tensors does not require grad and does
    #   not have a grad_fn
    # The bug in retries #2-4 was PeftModel.from_pretrained + merge_and_unload
    # on 4-bit base weights, which silently corrupted the grad graph. Keeping
    # prepare_model_for_kbit_training and dropping the merge works.
    model = prepare_model_for_kbit_training(model)
    # prepare_model_for_kbit_training implicitly enables gradient_checkpointing
    # on the model itself. KTOConfig(gradient_checkpointing=False) only controls
    # whether the trainer tries to toggle it — it does NOT retroactively disable
    # the model-level flag. Without this explicit disable, KTO on 72B-QLoRA logs
    # "None of the inputs have requires_grad=True. Gradients will be None" at
    # torch/utils/checkpoint.py:92, silently yielding a no-op adapter (retry #6).
    model.gradient_checkpointing_disable()

    lora_config = LoraConfig(
        r=32,
        lora_alpha=64,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    from datasets import Dataset
    ds = Dataset.from_list(dataset_rows)

    # KTO requires per_device_train_batch_size >= 2; with batch=1, the KL term
    # degenerates into the implied reward (TRL raises ValueError at init). Keep
    # effective batch = 16 by halving gradient accumulation.
    kto_config = KTOConfig(
        output_dir=output_dir,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=8,
        learning_rate=5e-7,
        num_train_epochs=1,
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=5,
        save_steps=50,
        save_total_limit=3,
        gradient_checkpointing=False,  # see comment in train_kto above
        report_to="none",
        beta=0.1,
        desirable_weight=1.0,
        undesirable_weight=1.0,
        # Our completions cap ~500 tokens, prompts ~200 — 1024 fits safely and
        # keeps activation memory headroom for batch=2 on 72B-QLoRA.
        max_length=1024,
        max_prompt_length=384,
    )

    trainer = KTOTrainer(
        model=model,
        args=kto_config,
        train_dataset=ds,
        tokenizer=tokenizer,
        peft_config=lora_config,
    )

    print("Starting KTO training...")
    trainer.train()
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"KTO adapter saved to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Truth-Alignment LoRA Training")
    parser.add_argument("--phase", choices=["sft", "dpo", "kto"], required=True)
    parser.add_argument("--data", type=str, default="training/data/",
                        help="SFT/DPO: directory of JSONL files. KTO: single JSONL file.")
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-72B-Instruct-AWQ")
    parser.add_argument("--base", type=str, default=None,
                        help="Base SFT adapter for DPO/KTO phases")
    parser.add_argument("--variant", choices=["truth", "soul"], default="truth")
    parser.add_argument("--lora-r", type=int, default=64)
    parser.add_argument("--lora-alpha", type=int, default=128)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--layer-scope",
        choices=["all", "mid", "late"],
        default="all",
        help="Layer specialization: mid (8..63 capability) vs late (64..79 identity)",
    )
    parser.add_argument(
        "--include-lm-head",
        action="store_true",
        help="Include unquantized lm_head in LoRA targets for direct output-token logit shift",
    )
    args = parser.parse_args()

    if args.phase == "sft":
        train_sft(
            args.data,
            args.output,
            args.model,
            variant=args.variant,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lr=args.lr,
            epochs=args.epochs,
            layer_scope=args.layer_scope,
            include_lm_head=args.include_lm_head,
        )
    elif args.phase == "dpo":
        train_dpo(
            args.data,
            args.output,
            args.model,
            args.base,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lr=args.lr,
            epochs=args.epochs,
            layer_scope=args.layer_scope,
            include_lm_head=args.include_lm_head,
        )
    elif args.phase == "kto":
        if not args.base:
            raise SystemExit("--base (SFT checkpoint) required for KTO phase")
        train_kto(args.data, args.output, args.model, args.base)


if __name__ == "__main__":
    main()
