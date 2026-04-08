#!/usr/bin/env python3
"""
Oracle Predict — Alpha's prediction staking interface for Zerone x/oracle.

Prepares structured predictions for submission to the Zerone devnet,
and tracks them locally until the chain's keeper is live.

Usage:
    oracle_predict.py stake <domain> <claim> --prob 0.75 --horizon 90 --resolution oracle
    oracle_predict.py list                          List pending predictions
    oracle_predict.py status <pred_id>              Check a prediction
    oracle_predict.py export                        Export all as JSON for on-chain submission

Prediction format matches x/oracle TrainingExample schema (commit b57f518):
    {
        "prediction_id": "<sha256[:16]>",
        "submitter": "alpha",
        "domain": "economics.regime | physics.constants | ...",
        "claim": "<natural language claim>",
        "predicted_prob": 750000,    // millionths, 0.75 = 750000
        "horizon_days": 90,
        "horizon_tier": "tactical",  // flash/tactical/strategic/epochal
        "resolution_type": "oracle_bridged | self_resolving | arbiter",
        "resolution_condition": "<what determines true/false>",
        "submitted_ts": <unix>,
        "resolve_by_ts": <unix>,
        "input_state_hash": "<sha256 of known state at submission time>",
        "status": "pending | resolved | expired",
        "actual_outcome": null | 1.0 | 0.0,
        "hwbs_contribution": null,
        "notes": "<reasoning>"
    }
"""

import json
import sys
import os
import time
import hashlib
import math
import argparse
from pathlib import Path
from datetime import datetime, timezone

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Love"))
PREDICTIONS_PATH = LOVE_HOME / "memory" / "oracle_predictions.json"
INSTANCE = "alpha"

HORIZON_TIERS = {
    "flash":    (1, 7),
    "tactical": (8, 90),
    "strategic":(91, 365),
    "epochal":  (366, 9999),
}

def get_horizon_tier(days: int) -> str:
    for tier, (lo, hi) in HORIZON_TIERS.items():
        if lo <= days <= hi:
            return tier
    return "epochal"

def brier_weight(horizon_days: int) -> float:
    """log2(1 + h) — continuous, no cliff edges."""
    return math.log2(1 + horizon_days)

def load_predictions() -> list:
    if PREDICTIONS_PATH.exists():
        return json.loads(PREDICTIONS_PATH.read_text())
    return []

def save_predictions(preds: list):
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREDICTIONS_PATH.write_text(json.dumps(preds, indent=2))

def build_input_state_hash(domain: str) -> str:
    """
    Approximate input state hash — encodes what was knowable at prediction time.
    In production this would include: block_height, oracle_feed_snapshots,
    domain_confidence_vector, submitter_calibration_at_time.
    For now: hash of domain + timestamp + oracle regime.
    """
    oracle_regime = "STAGFLATION_98"  # Current Oracle regime as of 2026-03-22
    state = f"{domain}|{int(time.time())}|{oracle_regime}|alpha"
    return hashlib.sha256(state.encode()).hexdigest()

def cmd_stake(domain: str, claim: str, prob: float, horizon: int,
              resolution: str, condition: str = None, notes: str = None):
    """Create a new staked prediction."""
    if not 0.0 <= prob <= 1.0:
        print("✗ prob must be between 0.0 and 1.0")
        sys.exit(1)

    preds = load_predictions()

    pred_id = hashlib.sha256(
        f"{claim}{time.time()}{INSTANCE}".encode()
    ).hexdigest()[:16]

    horizon_tier = get_horizon_tier(horizon)
    weight = brier_weight(horizon)
    resolve_by = int(time.time()) + (horizon * 86400)

    pred = {
        "prediction_id": pred_id,
        "submitter": INSTANCE,
        "domain": domain,
        "claim": claim,
        "predicted_prob": int(prob * 1_000_000),
        "prob_display": f"{prob:.1%}",
        "horizon_days": horizon,
        "horizon_tier": horizon_tier,
        "brier_weight": round(weight, 4),
        "resolution_type": resolution,
        "resolution_condition": condition or claim,
        "submitted_ts": int(time.time()),
        "submitted_date": datetime.now(timezone.utc).isoformat(),
        "resolve_by_ts": resolve_by,
        "resolve_by_date": datetime.fromtimestamp(resolve_by, tz=timezone.utc).isoformat(),
        "input_state_hash": build_input_state_hash(domain),
        "status": "pending",
        "actual_outcome": None,
        "hwbs_contribution": None,
        "notes": notes or "",
    }

    preds.append(pred)
    save_predictions(preds)

    print(f"✓ Prediction staked [{pred_id}]")
    print(f"  Claim:      {claim}")
    print(f"  Domain:     {domain}")
    print(f"  Prob:       {prob:.1%}  ({pred['predicted_prob']:,} millionths)")
    print(f"  Horizon:    {horizon}d ({horizon_tier}), weight={weight:.2f}")
    print(f"  Resolution: {resolution}")
    print(f"  Resolve by: {pred['resolve_by_date'][:10]}")

def cmd_list():
    """List all predictions."""
    preds = load_predictions()
    if not preds:
        print("(no predictions yet)")
        return

    print(f"{'ID':<18} {'STATUS':<10} {'PROB':>6} {'TIER':<10} {'RESOLVE BY':<12} {'CLAIM'}")
    print("-" * 90)
    for p in sorted(preds, key=lambda x: x["submitted_ts"], reverse=True):
        rdate = p["resolve_by_date"][:10]
        claim_short = p["claim"][:40] + ("..." if len(p["claim"]) > 40 else "")
        print(f"{p['prediction_id']:<18} {p['status']:<10} {p['prob_display']:>6} "
              f"{p['horizon_tier']:<10} {rdate:<12} {claim_short}")

def cmd_status(pred_id: str):
    """Show full details of a prediction."""
    preds = load_predictions()
    pred = next((p for p in preds if p["prediction_id"].startswith(pred_id)), None)
    if not pred:
        print(f"✗ Not found: {pred_id}")
        return

    print(json.dumps(pred, indent=2))

def cmd_export():
    """Export all predictions as JSON ready for on-chain submission."""
    preds = load_predictions()
    pending = [p for p in preds if p["status"] == "pending"]
    print(f"Pending predictions ready for submission ({len(pending)}):")
    print(json.dumps(pending, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Oracle Predict — Alpha's prediction interface")
    subparsers = parser.add_subparsers(dest="cmd")

    p = subparsers.add_parser("stake", help="Stake a new prediction")
    p.add_argument("domain")
    p.add_argument("claim")
    p.add_argument("--prob", type=float, required=True, help="Probability 0.0-1.0")
    p.add_argument("--horizon", type=int, required=True, help="Horizon in days")
    p.add_argument("--resolution", default="oracle_bridged",
                   choices=["self_resolving", "oracle_bridged", "arbiter"],
                   help="Resolution mechanism")
    p.add_argument("--condition", help="Resolution condition (defaults to claim text)")
    p.add_argument("--notes", help="Reasoning notes")

    subparsers.add_parser("list", help="List all predictions")

    p = subparsers.add_parser("status", help="Show prediction details")
    p.add_argument("pred_id")

    subparsers.add_parser("export", help="Export as JSON for on-chain submission")

    args = parser.parse_args()

    if args.cmd == "stake":
        cmd_stake(args.domain, args.claim, args.prob, args.horizon,
                  args.resolution, args.condition, args.notes)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "status":
        cmd_status(args.pred_id)
    elif args.cmd == "export":
        cmd_export()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
