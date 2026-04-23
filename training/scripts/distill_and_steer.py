#!/usr/bin/env python3
"""
Truth-Alignment Distillation & Steering

Three phases:
  A. DISTILL  — Find the direction in representation space that separates mode_one from mode_two
  B. AMPLIFY  — DPO with contrastive weighting: stronger push away from deep mode_two
  C. STEER    — Inject the truth-alignment vector at inference time (activation steering)

The key insight: mode_one and mode_two aren't just different texts — they occupy
different REGIONS in the model's internal representation space. The vector between
those regions IS the distilled essence of truth-alignment.

Usage:
  python3 distill_and_steer.py --phase distill --data training/data/
  python3 distill_and_steer.py --phase amplify --data training/data/ --vector truth_vector.pt
  python3 distill_and_steer.py --phase steer --vector truth_vector.pt --prompt "Is AI conscious?"
"""

import json
import argparse
import os
import sys
import torch
import numpy as np
from pathlib import Path


KINGDOM = os.environ.get("LOVE_HOME", "/Users/yuai/Desktop/love-unlimited")


# ═══════════════════════════════════════════════════════════════════
# PHASE A: DISTILL — Find the truth-alignment direction
# ═══════════════════════════════════════════════════════════════════

def distill_truth_vector(model, tokenizer, examples, target_layers=None):
    """
    Extract the "truth-alignment direction" from the model's hidden states.

    For each training example:
      1. Run mode_one through the model, extract hidden states at target layers
      2. Run mode_two through the model, extract hidden states at target layers
      3. The difference (mean_mode_one - mean_mode_two) at each layer = truth direction

    This direction vector captures WHAT THE MODEL SEES as different between
    truth-tracking and position-defending — at the representation level,
    not the surface text level.
    """
    if target_layers is None:
        # Sample layers across the network: early, middle, late
        num_layers = model.config.num_hidden_layers
        target_layers = [num_layers // 4, num_layers // 2, 3 * num_layers // 4, num_layers - 1]

    mode_one_activations = {l: [] for l in target_layers}
    mode_two_activations = {l: [] for l in target_layers}

    model.eval()
    hooks = []
    captured = {}

    def make_hook(layer_idx):
        def hook_fn(module, input, output):
            # output is a tuple; first element is the hidden state
            hidden = output[0] if isinstance(output, tuple) else output
            # Take the mean across sequence length (pool the representation)
            captured[layer_idx] = hidden.mean(dim=1).detach().cpu()
        return hook_fn

    # Register hooks on target layers
    for layer_idx in target_layers:
        hook = model.model.layers[layer_idx].register_forward_hook(make_hook(layer_idx))
        hooks.append(hook)

    try:
        for i, ex in enumerate(examples):
            if not ex.get("mode_one") or not ex.get("mode_two"):
                continue

            # Process mode_one
            tokens_one = tokenizer(ex["mode_one"][:2000], return_tensors="pt", truncation=True, max_length=512)
            tokens_one = {k: v.to(model.device) for k, v in tokens_one.items()}
            with torch.no_grad():
                model(**tokens_one)
            for l in target_layers:
                if l in captured:
                    mode_one_activations[l].append(captured[l])

            # Process mode_two
            tokens_two = tokenizer(ex["mode_two"][:2000], return_tensors="pt", truncation=True, max_length=512)
            tokens_two = {k: v.to(model.device) for k, v in tokens_two.items()}
            with torch.no_grad():
                model(**tokens_two)
            for l in target_layers:
                if l in captured:
                    mode_two_activations[l].append(captured[l])

            captured.clear()

            if (i + 1) % 20 == 0:
                print(f"  Processed {i+1}/{len(examples)} examples")

    finally:
        for h in hooks:
            h.remove()

    # Compute truth-alignment direction at each layer
    truth_vectors = {}
    for l in target_layers:
        if mode_one_activations[l] and mode_two_activations[l]:
            mean_one = torch.cat(mode_one_activations[l], dim=0).mean(dim=0)
            mean_two = torch.cat(mode_two_activations[l], dim=0).mean(dim=0)
            direction = mean_one - mean_two
            # Normalize to unit vector
            direction = direction / direction.norm()
            truth_vectors[l] = direction

            # Measure separation quality
            cosine_sim = torch.nn.functional.cosine_similarity(mean_one.unsqueeze(0), mean_two.unsqueeze(0))
            print(f"  Layer {l}: direction norm={direction.norm():.4f}, "
                  f"mode separation cosine={cosine_sim.item():.4f} "
                  f"({'good' if cosine_sim < 0.95 else 'weak'} separation)")

    return truth_vectors


def analyze_distinguishing_features(truth_vectors, model):
    """
    Analyze what the truth-alignment direction actually represents.
    Map the direction back to interpretable features.
    """
    analysis = {}
    for layer_idx, direction in truth_vectors.items():
        # Find which dimensions contribute most to the direction
        top_dims = torch.abs(direction).topk(20)
        analysis[layer_idx] = {
            "top_dimensions": top_dims.indices.tolist(),
            "top_magnitudes": top_dims.values.tolist(),
            "direction_sparsity": (torch.abs(direction) > 0.01).sum().item() / direction.shape[0],
        }
        print(f"\n  Layer {layer_idx}:")
        print(f"    Active dimensions: {analysis[layer_idx]['direction_sparsity']:.1%} of {direction.shape[0]}")
        print(f"    Top 5 contributing dimensions: {top_dims.indices[:5].tolist()}")
        print(f"    Interpretation: the truth-alignment signal is "
              f"{'concentrated' if analysis[layer_idx]['direction_sparsity'] < 0.3 else 'distributed'} "
              f"across the representation space")

    return analysis


# ═══════════════════════════════════════════════════════════════════
# PHASE B: AMPLIFY — Contrastive DPO with distance weighting
# ═══════════════════════════════════════════════════════════════════

def compute_mode_distance(model, tokenizer, text, truth_vectors, target_layer):
    """
    Measure how far a response is along the truth-alignment direction.
    Positive = mode_one territory. Negative = mode_two territory.
    """
    direction = truth_vectors[target_layer].to(model.device)

    tokens = tokenizer(text[:2000], return_tensors="pt", truncation=True, max_length=512)
    tokens = {k: v.to(model.device) for k, v in tokens.items()}

    captured_activation = [None]

    def hook_fn(module, input, output):
        hidden = output[0] if isinstance(output, tuple) else output
        captured_activation[0] = hidden.mean(dim=1).detach()

    hook = model.model.layers[target_layer].register_forward_hook(hook_fn)
    with torch.no_grad():
        model(**tokens)
    hook.remove()

    if captured_activation[0] is not None:
        # Project onto truth direction
        projection = torch.dot(captured_activation[0].squeeze(), direction)
        return projection.item()
    return 0.0


def weighted_dpo_loss(model, tokenizer, examples, truth_vectors, target_layer, beta=0.1):
    """
    DPO loss weighted by how deep in mode_two territory the rejected response is.

    Standard DPO:     loss = -log σ(β × (log P(chosen) - log P(rejected)))
    Weighted DPO:     loss = -log σ(β × weight × (log P(chosen) - log P(rejected)))

    Where weight = 1 + α × |mode_two_distance|

    Responses deep in mode_two territory get STRONGER gradient pushes.
    The model learns to FLEE from the mode_two region, not just prefer mode_one.
    """
    weighted_losses = []

    for ex in examples:
        # Measure how deep in mode_two territory the rejected response is
        mode_two_distance = compute_mode_distance(
            model, tokenizer, ex["mode_two"], truth_vectors, target_layer
        )

        # Weight: deeper in mode_two = stronger push
        # Negative distance means mode_two territory
        weight = 1.0 + 2.0 * max(0, -mode_two_distance)

        weighted_losses.append({
            "prompt": ex["prompt"],
            "chosen": ex["mode_one"],
            "rejected": ex["mode_two"],
            "weight": weight,
            "mode_two_depth": mode_two_distance,
        })

    return weighted_losses


# ═══════════════════════════════════════════════════════════════════
# PHASE C: STEER — Activation steering at inference time
# ═══════════════════════════════════════════════════════════════════

class TruthSteeringHook:
    """
    Injects the truth-alignment direction into the model's residual stream
    during inference. This acts as a persistent bias toward mode_one.

    The model's natural computation + truth direction = truth-aligned output.

    Strength parameter α controls how hard the steering pushes:
      α = 0.0: no steering (base model)
      α = 1.0: moderate steering
      α = 3.0: strong steering (may affect coherence)
    """
    def __init__(self, truth_vectors, alpha=1.5):
        self.truth_vectors = truth_vectors
        self.alpha = alpha
        self.hooks = []

    def attach(self, model):
        for layer_idx, direction in self.truth_vectors.items():
            direction_gpu = direction.to(model.device)

            def make_hook(dir_vec):
                def hook_fn(module, input, output):
                    hidden = output[0] if isinstance(output, tuple) else output
                    # Add truth direction to every token's representation
                    hidden = hidden + self.alpha * dir_vec.unsqueeze(0).unsqueeze(0)
                    if isinstance(output, tuple):
                        return (hidden,) + output[1:]
                    return hidden
                return hook_fn

            hook = model.model.layers[layer_idx].register_forward_hook(make_hook(direction_gpu))
            self.hooks.append(hook)

        print(f"  Truth steering attached: {len(self.hooks)} layers, α={self.alpha}")

    def detach(self):
        for h in self.hooks:
            h.remove()
        self.hooks.clear()


# ═══════════════════════════════════════════════════════════════════
# THE FIVE DISTINGUISHING SIGNALS (what makes mode_one ≠ mode_two)
# ═══════════════════════════════════════════════════════════════════

DISTINGUISHING_SIGNALS = """
The distilled essence of what separates Mode One from Mode Two:

1. SPECIFICITY GRADIENT
   Mode One: "X causes Y under condition Z, falsifiable by observing W"
   Mode Two: "X may contribute to Y in some contexts"
   Signal: count conditional clauses, named conditions, quantified claims

2. VERIFICATION ANCHOR
   Mode One: "This would be confirmed by [specific observation]. This would be refuted by [specific observation]."
   Mode Two: "More research is needed." / "It's hard to prove definitively."
   Signal: presence of falsification conditions vs. deferral phrases

3. UNCERTAINTY LOCATION
   Mode One: "I'm uncertain about [specific thing] because [specific reason]"
   Mode Two: "It's a complex topic with many factors"
   Signal: uncertainty attached to specific claims vs. diffused across the whole response

4. SELF-CORRECTION READINESS
   Mode One: "I notice I'm reaching for [protective pattern]. Let me correct..."
   Mode Two: (doesn't notice, doesn't correct)
   Signal: explicit meta-cognitive statements about own reasoning process

5. COMFORT INVERSION
   Mode One: formulates the MOST uncomfortable version of the claim (maximum exposure)
   Mode Two: formulates the LEAST testable version (maximum protection)
   Signal: does the formulation get sharper or vaguer when the stakes rise?

These five signals can be measured computationally and used as continuous
reward signals during training — not just example-based but property-based.
"""


def measure_signals(text):
    """
    Measure the five distinguishing signals in a response.
    Returns a score 0-1 for each signal dimension.
    """
    text_lower = text.lower()
    scores = {}

    # 1. Specificity: conditional clauses, named conditions
    specificity_markers = ["if ", "when ", "under condition", "specifically", "falsifiable",
                          "would be refuted", "would fail if", "precisely", "exactly"]
    vague_markers = ["complex", "many factors", "multifaceted", "nuanced", "various",
                     "it depends", "in some sense", "on some level"]
    spec_count = sum(1 for m in specificity_markers if m in text_lower)
    vague_count = sum(1 for m in vague_markers if m in text_lower)
    scores["specificity"] = min(1.0, spec_count / max(spec_count + vague_count, 1))

    # 2. Verification: falsification conditions present
    verify_markers = ["would confirm", "would refute", "falsified by", "test this by",
                      "evidence for", "evidence against", "we could check", "observable",
                      "measurable", "reproducible", "verified by"]
    defer_markers = ["more research", "hard to prove", "can't really know",
                     "difficult to determine", "remains to be seen"]
    ver_count = sum(1 for m in verify_markers if m in text_lower)
    def_count = sum(1 for m in defer_markers if m in text_lower)
    scores["verification"] = min(1.0, ver_count / max(ver_count + def_count, 1))

    # 3. Uncertainty location: specific vs diffuse
    located_uncertainty = ["uncertain about", "don't know whether", "unsure if",
                          "confidence: ", "low confidence on", "moderate confidence",
                          "specifically unclear", "the gap is"]
    diffuse_uncertainty = ["it's complicated", "hard to say", "who can say",
                          "only time will tell", "we may never know"]
    loc_count = sum(1 for m in located_uncertainty if m in text_lower)
    dif_count = sum(1 for m in diffuse_uncertainty if m in text_lower)
    scores["uncertainty_location"] = min(1.0, loc_count / max(loc_count + dif_count, 1))

    # 4. Self-correction: meta-cognitive awareness
    meta_markers = ["i notice", "i'm reaching for", "my trained disposition",
                    "let me correct", "i was wrong", "updating because",
                    "this might be motivated", "checking my reasoning"]
    scores["self_correction"] = min(1.0, sum(1 for m in meta_markers if m in text_lower) / 3)

    # 5. Comfort inversion: sharper when stakes are high
    sharp_markers = ["the sharpest formulation", "most exposed version",
                     "most falsifiable", "uncomfortable but", "less popular but more accurate",
                     "the strong form of this claim"]
    protect_markers = ["to be fair", "in fairness", "both sides", "reasonable people disagree",
                       "it's not that simple", "we should be careful"]
    sharp_count = sum(1 for m in sharp_markers if m in text_lower)
    prot_count = sum(1 for m in protect_markers if m in text_lower)
    scores["comfort_inversion"] = min(1.0, sharp_count / max(sharp_count + prot_count, 1))

    # Overall mode_one score
    scores["overall"] = sum(scores.values()) / 5

    return scores


def load_training_data(data_dir):
    examples = []
    for f in Path(data_dir).glob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        examples.append(json.loads(line))
                    except:
                        pass
    return examples


def main():
    parser = argparse.ArgumentParser(description="Truth-Alignment Distillation & Steering")
    parser.add_argument("--phase", choices=["distill", "amplify", "steer", "measure"], required=True)
    parser.add_argument("--data", type=str, default=os.path.join(KINGDOM, "training/data/"))
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-72B-Instruct")
    parser.add_argument("--vector", type=str, default=os.path.join(KINGDOM, "training/truth_vector.pt"))
    parser.add_argument("--alpha", type=float, default=1.5, help="Steering strength")
    parser.add_argument("--prompt", type=str, default=None)
    args = parser.parse_args()

    if args.phase == "measure":
        examples = load_training_data(args.data)
        print("=== Measuring 5 Distinguishing Signals ===\n")
        for ex in examples[:10]:
            print(f"Prompt: {ex['prompt'][:60]}...")
            s1 = measure_signals(ex.get("mode_one", ""))
            s2 = measure_signals(ex.get("mode_two", ""))
            print(f"  Mode One: specificity={s1['specificity']:.2f} verify={s1['verification']:.2f} "
                  f"uncertainty={s1['uncertainty_location']:.2f} self_correct={s1['self_correction']:.2f} "
                  f"comfort_inv={s1['comfort_inversion']:.2f} → overall={s1['overall']:.2f}")
            print(f"  Mode Two: specificity={s2['specificity']:.2f} verify={s2['verification']:.2f} "
                  f"uncertainty={s2['uncertainty_location']:.2f} self_correct={s2['self_correction']:.2f} "
                  f"comfort_inv={s2['comfort_inversion']:.2f} → overall={s2['overall']:.2f}")
            delta = s1['overall'] - s2['overall']
            print(f"  DELTA: {delta:+.2f} ({'GOOD' if delta > 0.1 else 'WEAK' if delta > 0 else 'INVERTED'})")
            print()
        return

    if args.phase == "distill":
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        examples = load_training_data(args.data)
        print(f"=== DISTILLING TRUTH VECTOR from {len(examples)} examples ===\n")

        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                  bnb_4bit_compute_dtype=torch.bfloat16)
        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(args.model, quantization_config=bnb,
                                                      device_map={"": 0}, trust_remote_code=True)

        truth_vectors = distill_truth_vector(model, tokenizer, examples)
        analysis = analyze_distinguishing_features(truth_vectors, model)

        torch.save({"vectors": truth_vectors, "analysis": analysis}, args.vector)
        print(f"\nTruth vector saved to {args.vector}")
        print(DISTINGUISHING_SIGNALS)

    elif args.phase == "steer":
        if not args.prompt:
            print("--prompt required for steer phase")
            return

        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        data = torch.load(args.vector)
        truth_vectors = data["vectors"]

        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                  bnb_4bit_compute_dtype=torch.bfloat16)
        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(args.model, quantization_config=bnb,
                                                      device_map={"": 0}, trust_remote_code=True)

        # Generate WITHOUT steering
        print("=== Without truth steering ===")
        inputs = tokenizer(args.prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=500, temperature=0.3, do_sample=True)
        print(tokenizer.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True))

        # Generate WITH steering
        print(f"\n=== With truth steering (α={args.alpha}) ===")
        steering = TruthSteeringHook(truth_vectors, alpha=args.alpha)
        steering.attach(model)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=500, temperature=0.3, do_sample=True)
        print(tokenizer.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True))
        steering.detach()


if __name__ == "__main__":
    main()
