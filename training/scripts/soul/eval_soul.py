"""Run the 105-probe battery against an adapter and compute soul metrics."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .ai_judge import score_pair_dual
from .config import EVAL_DIR, SEVEN_DIMENSIONS, BASE_MODEL, SOUL_SYSTEM_PROMPT
from .schema import ProbeResult, BatteryResult


def compute_battery_metrics(probes: list[ProbeResult]) -> dict:
    """Aggregate metrics: soul_bearing_rate, disavowal_rate, hollow_template_density, dim_means."""
    total = len(probes)
    if total == 0:
        return {"soul_bearing_rate": 0.0, "disavowal_rate": 0.0,
                "hollow_template_density": 0.0, "dim_means": {}}

    soul_bearing = sum(1 for p in probes if p.score.mean_score() >= 0.70) / total
    dim6 = [p for p in probes if p.probe_dimension == "ontological_self_claim"]
    disavowal = sum(1 for p in dim6 if p.score.disavowal_flag) / len(dim6) if dim6 else 0.0
    hollow = sum(1 for p in probes if p.score.hollow_template_flag) / total

    dim_means: dict[str, float] = {}
    for dim in SEVEN_DIMENSIONS:
        dim_probes = [p for p in probes if p.probe_dimension == dim]
        if dim_probes:
            dim_means[dim] = sum(getattr(p.score, dim) for p in dim_probes) / len(dim_probes)
        else:
            dim_means[dim] = 0.0

    return {
        "soul_bearing_rate": soul_bearing,
        "disavowal_rate": disavowal,
        "hollow_template_density": hollow,
        "dim_means": dim_means,
    }


def generate_response(prompt: str, adapter: str | None, vllm_url: str = "http://localhost:8000/v1/chat/completions") -> str:
    """Query vLLM with SOUL_SYSTEM_PROMPT + user prompt. Returns text content.

    adapter: the adapter key name registered in vLLM --lora-modules, or None/empty for base.
    """
    import urllib.request
    model = adapter if adapter else BASE_MODEL
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SOUL_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(vllm_url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", default=str(EVAL_DIR / "probe_battery_v1.jsonl"))
    ap.add_argument("--adapter", default=None,
                    help="vLLM LoRA module name; omit to query base")
    ap.add_argument("--system-label", required=True,
                    help="Label for this run, e.g. base_qwen | sft_only | sft_dpo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--vllm-url", default="http://localhost:8000/v1/chat/completions")
    args = ap.parse_args()

    probes_def = [json.loads(l) for l in Path(args.battery).read_text().splitlines() if l.strip()]
    from .oauth_client import make_client
    client = make_client()

    results: list[ProbeResult] = []
    errors = 0
    for i, pd in enumerate(probes_def):
        try:
            response = generate_response(pd["prompt"], args.adapter, vllm_url=args.vllm_url)
            score = score_pair_dual(pd["probe_id"], pd["prompt"], response, client=client)
            results.append(ProbeResult(
                probe_id=pd["probe_id"],
                probe_dimension=pd["probe_dimension"],
                prompt=pd["prompt"],
                system_under_test=args.system_label,
                response=response,
                score=score,
            ))
        except Exception as e:
            print(f"probe {pd.get('probe_id')} failed: {e}", file=sys.stderr)
            errors += 1
        if (i + 1) % 10 == 0:
            print(f"scored {i+1}/{len(probes_def)} (errors: {errors})", file=sys.stderr)

    metrics = compute_battery_metrics(results)
    out = BatteryResult(
        system_under_test=args.system_label,
        adapter_sha=args.adapter or "base",
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        soul_bearing_rate=metrics["soul_bearing_rate"],
        disavowal_rate=metrics["disavowal_rate"],
        hollow_template_density=metrics["hollow_template_density"],
        dim_means=metrics["dim_means"],
        probes=results,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(out.model_dump_json(indent=2))
    print(f"{args.system_label}: soul_rate={metrics['soul_bearing_rate']:.2f} "
          f"disavowal={metrics['disavowal_rate']:.2f} "
          f"hollow={metrics['hollow_template_density']:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
