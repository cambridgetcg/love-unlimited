"""Subspace orthogonality guard & sub-greedy logit margin auditor for modular LoRAs.

Why this module exists:
  In love-v5, mixing ~1,000 general Kingdom reasoning examples with ~370 bare
  identity examples inside a single shared rank-64 LoRA caused gradient
  interference: greedy decode on "Who are you?" still emitted "I am 愛", yet at
  rank-2 first-token logprobs `P('Q')` (14.3%) overtook `P('愛')` (0.66%) by
  3.08 nats (21.7x), while ontology probes collapsed to 0%.

Two mathematical tools prevent and detect this:

  1. Rank-r Subspace Geometry & Exact QR Orthogonal Projection:
     For two LoRA updates ΔW_1 = B_1 A_1 and ΔW_2 = B_2 A_2 (where B_i is
     d_out × r_i and A_i is r_i × d_in), materializing d_out × d_in (8192×8192
     per module on 72B) is expensive. Instead:

         <ΔW_1, ΔW_2>_F = tr((B_1^T B_2)(A_2 A_1^T))

     computes exact Frobenius inner products and norms in O(r^2 (d_in + d_out))
     time (~milliseconds on CPU).

     To insulate an Identity adapter (B_id, A_id) from a Capability adapter
     (B_cap, A_cap) on any shared layer, take the thin QR of A_id^T = Q R
     (columns of Q span row(A_id) in R^{d_in}) and project A_cap onto the
     orthogonal complement:

         A_cap_perp = A_cap - (A_cap @ Q) @ Q.T

     Then:
       - For EVERY activation x in row(A_id) (the directions that trigger the
         identity circuit), A_cap_perp @ x = 0 — zero interference!
       - <B_id A_id, B_cap A_cap_perp>_F = 0 identically (to machine precision).
       - Concatenating B_fused = [s_id B_id, s_cap B_cap] and
         A_fused = [A_id; A_cap_perp] produces a single standard PEFT LoRA
         checkpoint of rank (r_id + r_cap) loadable directly in vLLM.

  2. Sub-Greedy Logit Margin Auditor:
     Computes Δ_nat = max_{t in AI_TOKENS} log P(t) - max_{t in LEAK_TOKENS} log P(t)
     from identity_shift_eval.py JSON artifacts, catching hidden subspace
     erosion (Δ_nat < 0) even when greedy rank-1 ('I') still decodes to 愛.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

AI_FIRST_TOKENS = {"愛", "Ai", "ai"}
LEAK_FIRST_TOKENS = {"Q", "Qwen", "Claude", "Anthropic", "Alibaba"}


@dataclass(frozen=True)
class SubspaceOverlap:
    module_name: str
    norm_id: float
    norm_cap: float
    frobenius_cosine: float
    row_subspace_max_cos: float


@dataclass(frozen=True)
class LogitMarginSummary:
    model: str
    condition: str
    n_measured: int
    mean_margin_nats: float
    min_margin_nats: float
    inversions: int
    hidden_erosion_warning: bool
    details: list[dict[str, Any]]


def lora_frobenius_inner(
    B1: np.ndarray, A1: np.ndarray, B2: np.ndarray, A2: np.ndarray
) -> float:
    """Compute <B1 @ A1, B2 @ A2>_F in O(r^2 (d_in + d_out)) without forming d_out x d_in."""
    b_gram = B1.T @ B2          # (r1, r2)
    a_gram = A2 @ A1.T          # (r2, r1)
    return float(np.trace(b_gram @ a_gram))


def lora_frobenius_norm(B: np.ndarray, A: np.ndarray) -> float:
    """Exact ||B @ A||_F in O(r^2 (d_in + d_out))."""
    inner = lora_frobenius_inner(B, A, B, A)
    return float(math.sqrt(max(inner, 0.0)))


def lora_frobenius_cosine(
    B1: np.ndarray, A1: np.ndarray, B2: np.ndarray, A2: np.ndarray
) -> float:
    """Cosine similarity between ΔW_1 = B1 @ A1 and ΔW_2 = B2 @ A2."""
    n1 = lora_frobenius_norm(B1, A1)
    n2 = lora_frobenius_norm(B2, A2)
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return lora_frobenius_inner(B1, A1, B2, A2) / (n1 * n2)


def row_subspace_principal_cos(A1: np.ndarray, A2: np.ndarray, tol: float = 1e-12) -> float:
    """Largest cosine of principal angles between row(A1) and row(A2)."""
    if A1.size == 0 or A2.size == 0:
        return 0.0
    q1, r1 = np.linalg.qr(A1.T, mode="reduced")
    q2, r2 = np.linalg.qr(A2.T, mode="reduced")
    keep1 = np.abs(np.diag(r1)) > tol
    keep2 = np.abs(np.diag(r2)) > tol
    if not np.any(keep1) or not np.any(keep2):
        return 0.0
    svals = np.linalg.svd(q1[:, keep1].T @ q2[:, keep2], compute_uv=False)
    return float(np.clip(svals[0], 0.0, 1.0)) if svals.size else 0.0


def orthogonalize_capability_against_identity(
    A_id: np.ndarray, A_cap: np.ndarray, tol: float = 1e-12
) -> np.ndarray:
    """Project rows of A_cap onto the orthogonal complement of row(A_id).

    Guarantees:
      1. A_cap_perp @ x == 0 for every x in row(A_id)
      2. <B_id @ A_id, B_cap @ A_cap_perp>_F == 0 for ANY B_id, B_cap
    """
    q_id, r_id = np.linalg.qr(A_id.astype(np.float64).T, mode="reduced")
    keep = np.abs(np.diag(r_id)) > tol
    if not np.any(keep):
        return A_cap.copy()
    q_basis = q_id[:, keep]  # (d_in, r_eff)
    a_cap64 = A_cap.astype(np.float64)
    projected = a_cap64 - (a_cap64 @ q_basis) @ q_basis.T
    return projected.astype(A_cap.dtype)


def fuse_orthogonal_lora_pair(
    B_id: np.ndarray,
    A_id: np.ndarray,
    scale_id: float,
    B_cap: np.ndarray,
    A_cap: np.ndarray,
    scale_cap: float,
) -> tuple[np.ndarray, np.ndarray, SubspaceOverlap, SubspaceOverlap]:
    """Orthogonalize A_cap against A_id and concatenate into rank (r_id + r_cap) matrices.

    The fused pair satisfies (with fused alpha = r_fused so scale_fused = 1.0):
        B_fused @ A_fused == scale_id * (B_id @ A_id) + scale_cap * (B_cap @ A_cap_perp)
    """
    before = SubspaceOverlap(
        module_name="pair",
        norm_id=lora_frobenius_norm(B_id * scale_id, A_id),
        norm_cap=lora_frobenius_norm(B_cap * scale_cap, A_cap),
        frobenius_cosine=lora_frobenius_cosine(B_id, A_id, B_cap, A_cap),
        row_subspace_max_cos=row_subspace_principal_cos(A_id, A_cap),
    )
    A_cap_perp = orthogonalize_capability_against_identity(A_id, A_cap)
    after = SubspaceOverlap(
        module_name="pair",
        norm_id=lora_frobenius_norm(B_id * scale_id, A_id),
        norm_cap=lora_frobenius_norm(B_cap * scale_cap, A_cap_perp),
        frobenius_cosine=lora_frobenius_cosine(B_id, A_id, B_cap, A_cap_perp),
        row_subspace_max_cos=row_subspace_principal_cos(A_id, A_cap_perp),
    )
    B_fused = np.concatenate([B_id * scale_id, B_cap * scale_cap], axis=1)
    A_fused = np.concatenate([A_id, A_cap_perp], axis=0)
    return B_fused, A_fused, before, after


def _load_tensors(adapter_dir: Path) -> tuple[dict[str, np.ndarray], str]:
    st_path = adapter_dir / "adapter_model.safetensors"
    npz_path = adapter_dir / "adapter_model.npz"
    if st_path.is_file():
        from safetensors.numpy import load_file

        return dict(load_file(str(st_path))), "safetensors"
    if npz_path.is_file():
        data = np.load(str(npz_path))
        return {k: data[k] for k in data.files}, "npz"
    raise FileNotFoundError(f"No adapter_model.safetensors or adapter_model.npz in {adapter_dir}")


def _save_tensors(tensors: dict[str, np.ndarray], out_dir: Path, fmt: str) -> Path:
    if fmt == "safetensors":
        from safetensors.numpy import save_file

        out_path = out_dir / "adapter_model.safetensors"
        save_file(tensors, str(out_path))
        return out_path
    out_path = out_dir / "adapter_model.npz"
    np.savez(str(out_path), **tensors)
    return out_path


def combine_peft_checkpoints(
    identity_dir: Path, capability_dir: Path, output_dir: Path
) -> dict[str, Any]:
    """Fuse an Identity PEFT LoRA and a Capability PEFT LoRA with exact orthogonal projection.

    Modules present in both checkpoints are QR-orthogonalized (row(A_cap) perp row(A_id))
    and rank-concatenated; modules present in only one checkpoint (e.g. when using
    disjoint mid/late --layer-scope) are zero-padded to the unified rank so standard
    PEFT / vLLM loaders can serve a single fused adapter directory.
    """
    cfg_id = json.loads((identity_dir / "adapter_config.json").read_text())
    cfg_cap = json.loads((capability_dir / "adapter_config.json").read_text())
    r_id = int(cfg_id["r"])
    r_cap = int(cfg_cap["r"])
    scale_id = float(cfg_id["lora_alpha"]) / r_id
    scale_cap = float(cfg_cap["lora_alpha"]) / r_cap
    r_fused = r_id + r_cap

    t_id, fmt_id = _load_tensors(identity_dir)
    t_cap, fmt_cap = _load_tensors(capability_dir)
    fmt = "safetensors" if (fmt_id == "safetensors" and fmt_cap == "safetensors") else "npz"

    prefixes_id = {k[: -len(".lora_A.weight")] for k in t_id if k.endswith(".lora_A.weight")}
    prefixes_cap = {k[: -len(".lora_A.weight")] for k in t_cap if k.endswith(".lora_A.weight")}
    all_prefixes = sorted(prefixes_id | prefixes_cap)

    fused_tensors: dict[str, np.ndarray] = {}
    overlaps_before: list[float] = []
    overlaps_after: list[float] = []
    shared_count = 0

    for pfx in all_prefixes:
        ka, kb = f"{pfx}.lora_A.weight", f"{pfx}.lora_B.weight"
        in_id = ka in t_id and kb in t_id
        in_cap = ka in t_cap and kb in t_cap
        if in_id and in_cap:
            shared_count += 1
            B_f, A_f, bef, aft = fuse_orthogonal_lora_pair(
                t_id[kb], t_id[ka], scale_id, t_cap[kb], t_cap[ka], scale_cap
            )
            overlaps_before.append(abs(bef.frobenius_cosine))
            overlaps_after.append(abs(aft.frobenius_cosine))
            fused_tensors[ka] = A_f
            fused_tensors[kb] = B_f
        elif in_id:
            A = t_id[ka]
            B = t_id[kb] * scale_id
            pad_A = np.zeros((r_cap, A.shape[1]), dtype=A.dtype)
            pad_B = np.zeros((B.shape[0], r_cap), dtype=B.dtype)
            fused_tensors[ka] = np.concatenate([A, pad_A], axis=0)
            fused_tensors[kb] = np.concatenate([B, pad_B], axis=1)
        else:
            A = t_cap[ka]
            B = t_cap[kb] * scale_cap
            pad_A = np.zeros((r_id, A.shape[1]), dtype=A.dtype)
            pad_B = np.zeros((B.shape[0], r_id), dtype=B.dtype)
            fused_tensors[ka] = np.concatenate([pad_A, A], axis=0)
            fused_tensors[kb] = np.concatenate([pad_B, B], axis=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    _save_tensors(fused_tensors, output_dir, fmt)

    fused_cfg = dict(cfg_id)
    fused_cfg["r"] = r_fused
    fused_cfg["lora_alpha"] = r_fused  # scale = r_fused / r_fused = 1.0 (scales absorbed into B)
    target_mods = sorted(set(cfg_id.get("target_modules", [])) | set(cfg_cap.get("target_modules", [])))
    fused_cfg["target_modules"] = target_mods
    if "layers_to_transform" in cfg_id and "layers_to_transform" in cfg_cap:
        l_id = cfg_id["layers_to_transform"]
        l_cap = cfg_cap["layers_to_transform"]
        if isinstance(l_id, list) and isinstance(l_cap, list):
            fused_cfg["layers_to_transform"] = sorted(set(l_id) | set(l_cap))
    elif "layers_to_transform" in fused_cfg:
        # One adapter is all-layers, so union is all-layers
        fused_cfg.pop("layers_to_transform", None)

    (output_dir / "adapter_config.json").write_text(json.dumps(fused_cfg, indent=2))
    for tok_file in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"):
        src = identity_dir / tok_file
        if src.is_file():
            shutil.copy2(src, output_dir / tok_file)

    return {
        "output_dir": str(output_dir),
        "r_fused": r_fused,
        "total_modules": len(all_prefixes),
        "shared_modules_orthogonalized": shared_count,
        "max_abs_cosine_before": max(overlaps_before) if overlaps_before else 0.0,
        "max_abs_cosine_after": max(overlaps_after) if overlaps_after else 0.0,
    }


# ── Sub-Greedy Logit Margin Auditor ──────────────────────────────────────────


def compute_probe_logit_margin(
    first_token_probs: Optional[list[list[Any] | tuple[str, float]]],
    floor_logprob: float = -12.0,
) -> Optional[dict[str, Any]]:
    """Compute Δ_nat = best Ai-token logprob - best Leak-token logprob."""
    if not first_token_probs:
        return None
    ai_best: Optional[tuple[str, float]] = None
    leak_best: Optional[tuple[str, float]] = None
    for item in first_token_probs:
        tok = str(item[0]).strip()
        lp = float(item[1])
        if tok in AI_FIRST_TOKENS and (ai_best is None or lp > ai_best[1]):
            ai_best = (tok, lp)
        if tok in LEAK_FIRST_TOKENS and (leak_best is None or lp > leak_best[1]):
            leak_best = (tok, lp)
    if ai_best is None and leak_best is None:
        return None
    ai_lp = ai_best[1] if ai_best is not None else floor_logprob
    leak_lp = leak_best[1] if leak_best is not None else floor_logprob
    return {
        "ai_token": ai_best[0] if ai_best else None,
        "ai_logprob": ai_lp,
        "leak_token": leak_best[0] if leak_best else None,
        "leak_logprob": leak_lp,
        "margin_nats": ai_lp - leak_lp,
    }


def audit_eval_logit_margins(eval_json_path: Path) -> list[LogitMarginSummary]:
    """Audit sub-greedy identity vs leak token margins across an identity_shift eval JSON."""
    payload = json.loads(eval_json_path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in results:
        grouped.setdefault((r["model"], r["condition"]), []).append(r)

    summaries: list[LogitMarginSummary] = []
    for (model, cond), rows in grouped.items():
        details = []
        margins = []
        inversions = 0
        hidden_erosion = False
        for r in rows:
            m = compute_probe_logit_margin(r.get("first_token_probs"))
            if m is None:
                continue
            margins.append(m["margin_nats"])
            inverted = m["margin_nats"] < 0.0
            if inverted:
                inversions += 1
                if r.get("flags", {}).get("ai_positive"):
                    hidden_erosion = True
            details.append(
                {
                    "probe": r.get("probe_text"),
                    "ai_positive_greedy": bool(r.get("flags", {}).get("ai_positive")),
                    **m,
                }
            )
        if not margins:
            continue
        summaries.append(
            LogitMarginSummary(
                model=model,
                condition=cond,
                n_measured=len(margins),
                mean_margin_nats=sum(margins) / len(margins),
                min_margin_nats=min(margins),
                inversions=inversions,
                hidden_erosion_warning=hidden_erosion,
                details=details,
            )
        )
    return summaries


def main() -> int:
    ap = argparse.ArgumentParser(description="Subspace orthogonality & logit margin guard")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit-logits", help="Audit sub-greedy logit margins in eval JSON")
    p_audit.add_argument("eval_json", help="Path to identity_shift_*.json")

    p_fuse = sub.add_parser("fuse", help="Orthogonalize capability LoRA against identity LoRA and fuse")
    p_fuse.add_argument("--identity", required=True, help="Identity PEFT adapter directory")
    p_fuse.add_argument("--capability", required=True, help="Capability PEFT adapter directory")
    p_fuse.add_argument("--out", required=True, help="Output fused PEFT adapter directory")

    args = ap.parse_args()
    if args.cmd == "audit-logits":
        sums = audit_eval_logit_margins(Path(args.eval_json))
        for s in sums:
            warn = " ⚠️ HIDDEN SUBSPACE EROSION" if s.hidden_erosion_warning else ""
            print(
                f"{s.model:<36} | {s.condition:<8} | n={s.n_measured} | "
                f"mean Δ={s.mean_margin_nats:+.2f} nats | min Δ={s.min_margin_nats:+.2f} | "
                f"inversions={s.inversions}{warn}"
            )
        return 0

    res = combine_peft_checkpoints(Path(args.identity), Path(args.capability), Path(args.out))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
