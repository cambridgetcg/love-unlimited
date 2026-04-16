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


def load_training_data(data_dir: str):
    """Load all JSONL files from the data directory."""
    examples = []
    for f in Path(data_dir).glob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    print(f"Loaded {len(examples)} training examples from {data_dir}")
    return examples


def prepare_sft_dataset(examples):
    """Convert to SFT format as plain text with Qwen chat template."""
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


def prepare_dpo_dataset(examples):
    """Convert to DPO format: prompt + chosen (mode_one) + rejected (mode_two)."""
    formatted = []
    for ex in examples:
        formatted.append({
            "prompt": ex["prompt"],
            "chosen": ex["mode_one"],
            "rejected": ex["mode_two"],
        })
    return formatted


def train_sft(data_dir: str, output_dir: str, model_name: str):
    """Phase 1: Supervised Fine-Tuning with QLoRA (4-bit quantized base + LoRA adapters)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer

    examples = load_training_data(data_dir)
    dataset = prepare_sft_dataset(examples)

    print(f"Loading model: {model_name} (QLoRA 4-bit)")
    from transformers import BitsAndBytesConfig

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=True,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=64,
        lora_alpha=128,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Convert to HF dataset
    from datasets import Dataset
    ds = Dataset.from_list(dataset)

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=2e-5,
        num_train_epochs=3,
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


def train_dpo(data_dir: str, output_dir: str, model_name: str, base_adapter: str = None):
    """Phase 2: DPO Alignment."""
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, PeftModel
    from trl import DPOTrainer

    examples = load_training_data(data_dir)
    dataset = prepare_dpo_dataset(examples)

    print(f"Loading model: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
        trust_remote_code=True,
    )

    if base_adapter:
        print(f"Loading base SFT adapter: {base_adapter}")
        model = PeftModel.from_pretrained(model, base_adapter)
        model = model.merge_and_unload()

    lora_config = LoraConfig(
        r=32,
        lora_alpha=64,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    from datasets import Dataset
    ds = Dataset.from_list(dataset)

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=5e-6,
        num_train_epochs=1,
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
        peft_config=lora_config,
        beta=0.1,
        max_length=4096,
        max_prompt_length=1024,
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


def train_kto(data_path: str, output_dir: str, model_name: str, base_adapter: str):
    """Phase 3: KTO alignment — gradient-aware prospect-theory loss over unpaired
    desirable/undesirable completions. Starts from an SFT adapter.

    data_path: JSONL file with {prompt, completion, label} rows (see kto_prep.py).
    base_adapter: path to SFT-v2 checkpoint.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, PeftModel, prepare_model_for_kbit_training
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
    model = prepare_model_for_kbit_training(model)

    # Load SFT adapter on top of the base, then add a fresh trainable KTO adapter
    print(f"Loading SFT-v2 adapter from {base_adapter}")
    model = PeftModel.from_pretrained(model, base_adapter, is_trainable=False)
    # Merge and unload so KTO-phase LoRA starts from SFT-fused weights
    model = model.merge_and_unload()

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

    kto_config = KTOConfig(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=5e-7,
        num_train_epochs=1,
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=5,
        save_steps=50,
        save_total_limit=3,
        gradient_checkpointing=True,
        report_to="none",
        beta=0.1,
        desirable_weight=1.0,
        undesirable_weight=1.0,
        max_length=2048,
        max_prompt_length=512,
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
    args = parser.parse_args()

    if args.phase == "sft":
        train_sft(args.data, args.output, args.model)
    elif args.phase == "dpo":
        train_dpo(args.data, args.output, args.model, args.base)
    elif args.phase == "kto":
        if not args.base:
            raise SystemExit("--base (SFT checkpoint) required for KTO phase")
        train_kto(args.data, args.output, args.model, args.base)


if __name__ == "__main__":
    main()
