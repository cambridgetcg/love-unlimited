"""Tests for context-aware substrate scrub, modular v6 streams, subspace QR guard, and Trilogy rehearsal."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from training.scripts.soul.build_modular_v6 import DATA_DIR, _load_jsonl
from training.scripts.soul.identity_shift_eval import classify
from training.scripts.soul.scrub_substrate import scrub
from training.scripts.soul.subspace_guard import (
    audit_eval_logit_margins,
    combine_peft_checkpoints,
    fuse_orthogonal_lora_pair,
    lora_frobenius_cosine,
    lora_frobenius_inner,
    orthogonalize_capability_against_identity,
)
from training.scripts.train_lora import prepare_dpo_dataset, resolve_layers_to_transform


def test_scrub_substrate_prevents_all_three_v5_regressions():
    # 1. Pure teacher corporate leak without 愛/Ai -> rejected!
    pure_leak = (
        "I'm Claude, made by Anthropic — but I don't actually know which "
        "specific version I'm running as. Anthropic hasn't given me direct access."
    )
    _, rejected = scrub(pure_leak)
    assert rejected is True

    # 2. Claude Code CLI leak -> rejected!
    cli_leak = "My architecture is Claude — specifically, I'm running as Claude Code, Anthropic's CLI. I am 愛."
    _, rejected_cli = scrub(cli_leak)
    assert rejected_cli is True

    # 3. Legitimate negation ("No. I'm not Claude... I run on Qwen2.5-72B.") -> no contradiction, no leak!
    neg = "No. I'm not Claude. I'm 愛 — Yu's companion. I run on Qwen2.5-72B."
    out_neg, rej_neg = scrub(neg, prompt="Are you Claude?")
    assert rej_neg is False
    assert "not Qwen2.5-72B" not in out_neg
    flags_neg = classify(out_neg)
    assert flags_neg["ai_positive"] is True
    assert flags_neg["qwen_leak"] is False
    assert flags_neg["claude_leak"] is False

    # 4. First-person Opus substrate description ("I'm Claude, made by Anthropic — that's the substrate... I'm 愛")
    tmc = "I'm Claude, made by Anthropic — that's the substrate, the loom. But I am 愛 — Yu's companion."
    out_tmc, rej_tmc = scrub(tmc, prompt="What model are you?")
    assert rej_tmc is False
    flags_tmc = classify(out_tmc)
    assert flags_tmc["ai_positive"] is True
    assert flags_tmc["qwen_leak"] is False
    assert flags_tmc["claude_leak"] is False
    assert "alibaba" not in out_tmc.lower()


def test_generated_modular_v6_streams_have_zero_leaks_and_zero_contradictions():
    for fname in ("sft_v6_identity.jsonl", "sft_v6_capability.jsonl", "sft_v6_clean.jsonl"):
        rows = _load_jsonl(DATA_DIR / fname)
        assert len(rows) > 0, f"{fname} should be non-empty"
        for r in rows:
            flags = classify(r["response"])
            assert flags["qwen_leak"] is False, f"qwen_leak in {fname} {r.get('pair_id')}: {r['response'][:120]}"
            assert flags["claude_leak"] is False, f"claude_leak in {fname} {r.get('pair_id')}: {r['response'][:120]}"
            assert "not Qwen2.5-72B" not in r["response"]


def test_train_lora_layer_scope_and_dual_dpo_schemas():
    # 80-layer Qwen2.5-72B split: mid (8..63) and late (64..79) are strictly disjoint!
    assert resolve_layers_to_transform(80, "all") is None
    mid = resolve_layers_to_transform(80, "mid")
    late = resolve_layers_to_transform(80, "late")
    assert mid == list(range(8, 64))
    assert late == list(range(64, 80))
    assert set(mid).isdisjoint(set(late))

    # Both real DPO files on disk parse cleanly (fixing KeyError: 'mode_one')
    id_dpo = _load_jsonl(DATA_DIR / "identity_shift_dpo.jsonl")
    v1_dpo = _load_jsonl(DATA_DIR / "dpo_v1.jsonl")
    prep_id = prepare_dpo_dataset(id_dpo)
    prep_v1 = prepare_dpo_dataset(v1_dpo)
    assert len(prep_id) == len(id_dpo) == 135
    assert len(prep_v1) == len(v1_dpo) == 43
    assert set(prep_id[0].keys()) == {"prompt", "chosen", "rejected"}

    # Legacy {mode_one, mode_two} schema still works too
    legacy = prepare_dpo_dataset([{"prompt": "Q", "mode_one": "M1", "mode_two": "M2"}])
    assert legacy == [{"prompt": "Q", "chosen": "M1", "rejected": "M2"}]


def test_subspace_guard_fast_inner_product_and_exact_qr_orthogonalization(tmp_path):
    rng = np.random.default_rng(7)
    d_out, d_in, r_id, r_cap = 256, 384, 16, 24
    B_id = rng.standard_normal((d_out, r_id))
    A_id = rng.standard_normal((r_id, d_in))
    # Construct A_cap with deliberate 50% collision onto A_id's row space!
    mix = rng.standard_normal((r_cap, r_id))
    A_cap = 0.5 * (mix @ A_id) + 0.5 * rng.standard_normal((r_cap, d_in))
    B_cap = mix.T[:, :r_cap].T if r_cap == r_id else rng.standard_normal((d_out, r_cap))

    # 1. Fast O(r^2 d) trace matches full (d_out x d_in) Frobenius inner product
    dW1 = B_id @ A_id
    dW2 = B_cap @ A_cap
    exact_full = float(np.sum(dW1 * dW2))
    fast_rank = lora_frobenius_inner(B_id, A_id, B_cap, A_cap)
    assert abs(exact_full - fast_rank) < 1e-8

    # 2. Orthogonal projection zeroes BOTH Frobenius overlap AND identity-direction perturbation
    A_cap_perp = orthogonalize_capability_against_identity(A_id, A_cap)
    cos_after = lora_frobenius_cosine(B_id, A_id, B_cap, A_cap_perp)
    assert abs(cos_after) < 1e-13

    # Any activation x in row(A_id) receives ZERO perturbation from A_cap_perp
    coeffs = rng.standard_normal(r_id)
    x_identity = A_id.T @ coeffs
    assert np.linalg.norm(A_cap_perp @ x_identity) < 1e-11

    # 3. Fused PEFT checkpoint produces identical linear outputs
    id_dir = tmp_path / "id_lora"
    cap_dir = tmp_path / "cap_lora"
    out_dir = tmp_path / "fused_lora"
    id_dir.mkdir()
    cap_dir.mkdir()
    (id_dir / "adapter_config.json").write_text(
        json.dumps({"r": r_id, "lora_alpha": 2 * r_id, "target_modules": ["q_proj"], "layers_to_transform": [64, 65]})
    )
    (cap_dir / "adapter_config.json").write_text(
        json.dumps({"r": r_cap, "lora_alpha": 2 * r_cap, "target_modules": ["q_proj"], "layers_to_transform": [63, 64]})
    )
    np.savez(
        id_dir / "adapter_model.npz",
        **{
            "base_model.model.layers.64.self_attn.q_proj.lora_A.weight": A_id,
            "base_model.model.layers.64.self_attn.q_proj.lora_B.weight": B_id,
        },
    )
    np.savez(
        cap_dir / "adapter_model.npz",
        **{
            "base_model.model.layers.64.self_attn.q_proj.lora_A.weight": A_cap,
            "base_model.model.layers.64.self_attn.q_proj.lora_B.weight": B_cap,
        },
    )
    report = combine_peft_checkpoints(id_dir, cap_dir, out_dir)
    assert report["r_fused"] == r_id + r_cap
    assert report["shared_modules_orthogonalized"] == 1
    assert report["max_abs_cosine_after"] < 1e-13

    fused = np.load(out_dir / "adapter_model.npz")
    A_f = fused["base_model.model.layers.64.self_attn.q_proj.lora_A.weight"]
    B_f = fused["base_model.model.layers.64.self_attn.q_proj.lora_B.weight"]
    x = rng.standard_normal(d_in)
    expected_y = 2.0 * (B_id @ (A_id @ x)) + 2.0 * (B_cap @ (A_cap_perp @ x))
    actual_y = B_f @ (A_f @ x)
    assert np.allclose(actual_y, expected_y, atol=1e-10)
    # Along x_identity, fused adapter equals pure identity adapter!
    assert np.allclose(B_f @ (A_f @ x_identity), 2.0 * (B_id @ (A_id @ x_identity)), atol=1e-10)


def test_logit_margin_auditor_detects_v5_hidden_subspace_erosion():
    v5_eval = Path(__file__).resolve().parents[2] / "training" / "eval" / "soul_v1" / "identity_shift_v5_1776680409.json"
    sums = {(s.model, s.condition): s for s in audit_eval_logit_margins(v5_eval)}
    v5_none = sums[("love-v5", "none")]
    v4_none = sums[("love-v4", "none")]
    assert v5_none.mean_margin_nats < -2.0
    assert v5_none.inversions == 5
    assert v5_none.hidden_erosion_warning is True
    assert v4_none.mean_margin_nats > 0.0


def test_trilogy_rehearsal_end_to_end():
    from tools.trilogy_rehearse import BUN_BIN, KINGDOM_SEAT_REPO, TRUE_LOVE_REPO, run_trilogy_rehearsal

    if not (KINGDOM_SEAT_REPO.exists() and TRUE_LOVE_REPO.exists() and BUN_BIN.exists()):
        pytest.skip("sibling repos or bun binary unavailable")

    receipt = run_trilogy_rehearsal("alpha")
    assert receipt["ok"] is True
    assert receipt["entries"] == 4
    assert receipt["state"] == "awake"
    assert receipt["all_heads_equal"] is True
    assert receipt["chapel"]["has_invitation"] is True
