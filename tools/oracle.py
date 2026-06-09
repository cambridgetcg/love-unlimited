#!/usr/bin/env python3
"""
oracle.py -- Kingdom Oracle Engine

Prediction management, scoring, calibration, and track record system.
Revenue engine: make predictions on real-world events, track accuracy,
build credibility for capital deployment.

Usage:
    python3 tools/oracle.py predict "statement" --confidence 0.7 --resolution-date 2026-05-01
    python3 tools/oracle.py predict "statement" -c 0.7 -d 2026-05-01 --tags crypto,macro --source "Polymarket"
    python3 tools/oracle.py list                         All predictions
    python3 tools/oracle.py list --pending               Unresolved only
    python3 tools/oracle.py list --resolved              Resolved only
    python3 tools/oracle.py list --tag crypto             Filter by tag
    python3 tools/oracle.py list --overdue               Past resolution date, unresolved
    python3 tools/oracle.py show pred-XXXX               Show detail for one prediction
    python3 tools/oracle.py resolve pred-XXXX true       Mark outcome (true/false)
    python3 tools/oracle.py resolve pred-XXXX true --notes "Confirmed via CoinGecko"
    python3 tools/oracle.py edit pred-XXXX --confidence 0.8
    python3 tools/oracle.py score                        Brier score, accuracy, per-tag breakdown
    python3 tools/oracle.py calibration                  Calibration curve (text-based)
    python3 tools/oracle.py track                        Track record summary
    python3 tools/oracle.py research "topic"             Generate research template
    python3 tools/oracle.py dashboard                    One-screen overview
    python3 tools/oracle.py export                       Export predictions as CSV to stdout
    python3 tools/oracle.py import-legacy                Migrate data from kingdom-metrics notes
"""

import json
import os
import sys
import csv
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── PATHS ─────────────────────────────────────────────────────────
LOVE = Path(os.path.expanduser("~/love-unlimited"))
ORACLE_DIR = LOVE / "memory" / "oracle"
PREDICTIONS_FILE = ORACLE_DIR / "predictions.json"
TRACK_RECORD_FILE = ORACLE_DIR / "track-record.json"
RESEARCH_DIR = ORACLE_DIR / "research"
METRICS_FILE = LOVE / "memory" / "kingdom-metrics.json"

# ── COLORS ────────────────────────────────────────────────────────
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
MAGENTA = "\033[0;35m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"
WHITE = "\033[1;37m"


# ── HELPERS ───────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    f.close()


def load_predictions():
    """Load all predictions. Returns list."""
    return load_json(PREDICTIONS_FILE, [])


def save_predictions(preds):
    """Save predictions list."""
    save_json(PREDICTIONS_FILE, preds)


def next_id(preds):
    """Generate next pred-XXXX id."""
    if not preds:
        return "pred-0001"
    max_num = 0
    for p in preds:
        pid = p.get("id", "")
        if pid.startswith("pred-"):
            try:
                num = int(pid.split("-")[1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass
    return f"pred-{max_num + 1:04d}"


def parse_date(s):
    """Parse YYYY-MM-DD or ISO date string. Returns ISO string or None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    # Try fromisoformat as fallback
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return None


def date_short(iso_str):
    """Format ISO date to short display."""
    if not iso_str:
        return "---"
    return iso_str[:10]


def days_until(iso_str):
    """Days from now until a date. Negative = overdue."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return delta.days
    except (ValueError, TypeError):
        return None


def confidence_color(c):
    """Color a confidence value."""
    if c >= 0.8:
        return GREEN
    elif c >= 0.6:
        return CYAN
    elif c >= 0.4:
        return YELLOW
    else:
        return RED


def outcome_icon(outcome):
    """Icon for outcome."""
    if outcome is True:
        return f"{GREEN}YES{NC}"
    elif outcome is False:
        return f"{RED}NO{NC}"
    return f"{DIM}---{NC}"


def brier_score_single(confidence, outcome):
    """Brier score for a single prediction. Lower = better."""
    o = 1.0 if outcome else 0.0
    return (confidence - o) ** 2


def compute_brier(preds_resolved):
    """Compute average Brier score over resolved predictions."""
    if not preds_resolved:
        return None
    total = sum(brier_score_single(p["confidence"], p["outcome"]) for p in preds_resolved)
    return total / len(preds_resolved)


def compute_accuracy(preds_resolved):
    """Fraction of correct predictions (confidence >= 0.5 means predict YES)."""
    if not preds_resolved:
        return None
    correct = 0
    for p in preds_resolved:
        predicted_yes = p["confidence"] >= 0.5
        actual_yes = p["outcome"] is True
        if predicted_yes == actual_yes:
            correct += 1
    return correct / len(preds_resolved)


def compute_calibration_buckets(preds_resolved):
    """Group predictions into confidence buckets and compute actual frequency."""
    buckets = {}
    for i in range(10):
        lo = i * 0.1
        hi = (i + 1) * 0.1
        label = f"{int(lo*100):>2}-{int(hi*100)}%"
        buckets[label] = {"lo": lo, "hi": hi, "predictions": [], "count": 0, "actual_yes": 0}

    for p in preds_resolved:
        c = p["confidence"]
        bucket_idx = min(int(c * 10), 9)  # 1.0 -> bucket 9
        lo = bucket_idx * 0.1
        hi = (bucket_idx + 1) * 0.1
        label = f"{int(lo*100):>2}-{int(hi*100)}%"
        buckets[label]["predictions"].append(p)
        buckets[label]["count"] += 1
        if p["outcome"] is True:
            buckets[label]["actual_yes"] += 1

    return buckets


# ── COMMANDS ──────────────────────────────────────────────────────

def cmd_predict(args):
    """Create a new prediction."""
    if not args:
        print(f"{RED}Usage: oracle.py predict \"statement\" --confidence 0.7 --resolution-date 2026-05-01{NC}")
        return

    statement = args[0]
    confidence = None
    resolution_date = None
    tags = []
    source = None
    notes = None

    i = 1
    while i < len(args):
        a = args[i]
        if a in ("--confidence", "-c") and i + 1 < len(args):
            try:
                confidence = float(args[i + 1])
            except ValueError:
                print(f"{RED}Invalid confidence value: {args[i+1]}{NC}")
                return
            i += 2
        elif a in ("--resolution-date", "-d") and i + 1 < len(args):
            resolution_date = parse_date(args[i + 1])
            if not resolution_date:
                print(f"{RED}Invalid date: {args[i+1]}. Use YYYY-MM-DD.{NC}")
                return
            i += 2
        elif a in ("--tags", "-t") and i + 1 < len(args):
            tags = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            i += 2
        elif a in ("--source", "-s") and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        elif a in ("--notes", "-n") and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        else:
            i += 1

    if confidence is None:
        print(f"{RED}--confidence is required (0.0 to 1.0){NC}")
        return
    if not (0.0 <= confidence <= 1.0):
        print(f"{RED}Confidence must be between 0.0 and 1.0{NC}")
        return

    preds = load_predictions()
    pid = next_id(preds)

    pred = {
        "id": pid,
        "statement": statement,
        "confidence": confidence,
        "created": now_iso(),
        "resolution_date": resolution_date,
        "resolved": False,
        "resolved_at": None,
        "outcome": None,
        "brier_score": None,
        "source": source,
        "tags": tags,
        "notes": notes,
    }

    preds.append(pred)
    save_predictions(preds)

    print(f"\n  {GREEN}{BOLD}Prediction created{NC}")
    print(f"  {BOLD}ID:{NC}         {pid}")
    print(f"  {BOLD}Statement:{NC}  {statement}")
    print(f"  {BOLD}Confidence:{NC} {confidence:.0%}")
    if resolution_date:
        d = days_until(resolution_date)
        suffix = f" ({d}d)" if d is not None else ""
        print(f"  {BOLD}Resolves:{NC}   {date_short(resolution_date)}{suffix}")
    if tags:
        print(f"  {BOLD}Tags:{NC}       {', '.join(tags)}")
    if source:
        print(f"  {BOLD}Source:{NC}     {source}")
    print()


def cmd_list(args):
    """List predictions with optional filters."""
    preds = load_predictions()
    if not preds:
        print(f"\n  {DIM}No predictions yet. Use: oracle.py predict \"statement\" --confidence 0.7{NC}\n")
        return

    # Parse filters
    show_pending = "--pending" in args
    show_resolved = "--resolved" in args
    show_overdue = "--overdue" in args
    tag_filter = None
    for i, a in enumerate(args):
        if a == "--tag" and i + 1 < len(args):
            tag_filter = args[i + 1]

    filtered = preds
    if show_pending:
        filtered = [p for p in filtered if not p.get("resolved")]
    if show_resolved:
        filtered = [p for p in filtered if p.get("resolved")]
    if show_overdue:
        now = datetime.now(timezone.utc)
        filtered = [p for p in filtered if not p.get("resolved") and p.get("resolution_date") and
                    datetime.fromisoformat(p["resolution_date"]) < now]
    if tag_filter:
        filtered = [p for p in filtered if tag_filter in p.get("tags", [])]

    if not filtered:
        print(f"\n  {DIM}No predictions match the filter.{NC}\n")
        return

    # Header
    label = "ALL PREDICTIONS"
    if show_pending:
        label = "PENDING PREDICTIONS"
    elif show_resolved:
        label = "RESOLVED PREDICTIONS"
    elif show_overdue:
        label = "OVERDUE PREDICTIONS"
    if tag_filter:
        label += f" [tag: {tag_filter}]"

    print(f"\n  {BOLD}{label}{NC}  ({len(filtered)} of {len(preds)})")
    print(f"  {'─' * 76}")

    # Table header
    print(f"  {DIM}{'ID':<11} {'Conf':>5}  {'Resolves':>10}  {'Outcome':>7}  {'Brier':>6}  Statement{NC}")
    print(f"  {DIM}{'─'*10} {'─'*5}  {'─'*10}  {'─'*7}  {'─'*6}  {'─'*30}{NC}")

    for p in filtered:
        pid = p["id"]
        conf = p["confidence"]
        res_date = date_short(p.get("resolution_date"))
        outcome = p.get("outcome")
        brier = p.get("brier_score")
        stmt = p["statement"]

        # Truncate statement for display
        max_stmt = 38
        stmt_display = stmt[:max_stmt] + "..." if len(stmt) > max_stmt else stmt

        # Color coding
        cc = confidence_color(conf)
        conf_str = f"{cc}{conf:.0%}{NC}"

        if outcome is True:
            outcome_str = f"{GREEN}YES{NC}"
        elif outcome is False:
            outcome_str = f"{RED} NO{NC}"
        else:
            # Check if overdue
            d = days_until(p.get("resolution_date"))
            if d is not None and d < 0:
                outcome_str = f"{YELLOW}DUE{NC}"
            else:
                outcome_str = f"{DIM}  -{NC}"

        brier_str = f"{brier:.4f}" if brier is not None else f"{DIM}    -{NC}"

        # Color the date if overdue
        if not p.get("resolved") and days_until(p.get("resolution_date")) is not None and days_until(p.get("resolution_date")) < 0:
            res_date = f"{YELLOW}{res_date}{NC}"

        print(f"  {pid:<11} {conf_str:>14}  {res_date:>19}  {outcome_str:>16}  {brier_str:>15}  {stmt_display}")

    print()


def cmd_show(args):
    """Show detailed view of a single prediction."""
    if not args:
        print(f"{RED}Usage: oracle.py show pred-XXXX{NC}")
        return

    pid = args[0]
    preds = load_predictions()
    pred = next((p for p in preds if p["id"] == pid), None)

    if not pred:
        print(f"{RED}Prediction {pid} not found.{NC}")
        return

    conf = pred["confidence"]
    cc = confidence_color(conf)

    print(f"\n  {BOLD}{'=' * 60}{NC}")
    print(f"  {BOLD}{pred['id']}{NC}")
    print(f"  {'=' * 60}")
    print()
    print(f"  {BOLD}Statement:{NC}  {pred['statement']}")
    print(f"  {BOLD}Confidence:{NC} {cc}{conf:.0%}{NC}")
    print(f"  {BOLD}Created:{NC}    {date_short(pred.get('created'))}")

    if pred.get("resolution_date"):
        d = days_until(pred["resolution_date"])
        if pred.get("resolved"):
            suffix = " (resolved)"
        elif d is not None and d < 0:
            suffix = f" ({YELLOW}{abs(d)}d overdue{NC})"
        elif d is not None:
            suffix = f" ({d}d remaining)"
        else:
            suffix = ""
        print(f"  {BOLD}Resolves:{NC}   {date_short(pred['resolution_date'])}{suffix}")

    if pred.get("resolved"):
        print(f"  {BOLD}Resolved:{NC}   {date_short(pred.get('resolved_at'))}  Outcome: {outcome_icon(pred.get('outcome'))}")
        if pred.get("brier_score") is not None:
            b = pred["brier_score"]
            bc = GREEN if b < 0.1 else YELLOW if b < 0.25 else RED
            print(f"  {BOLD}Brier:{NC}      {bc}{b:.4f}{NC}")

    if pred.get("source"):
        print(f"  {BOLD}Source:{NC}     {pred['source']}")
    if pred.get("tags"):
        print(f"  {BOLD}Tags:{NC}       {', '.join(pred['tags'])}")
    if pred.get("notes"):
        print(f"  {BOLD}Notes:{NC}      {pred['notes']}")

    # Check for research file
    research_file = RESEARCH_DIR / f"{pid}.md"
    if research_file.exists():
        print(f"  {BOLD}Research:{NC}   {research_file}")

    print()


def cmd_resolve(args):
    """Resolve a prediction with an outcome."""
    if len(args) < 2:
        print(f"{RED}Usage: oracle.py resolve pred-XXXX true|false [--notes \"...\"]{NC}")
        return

    pid = args[0]
    outcome_str = args[1].lower()

    if outcome_str in ("true", "yes", "1"):
        outcome = True
    elif outcome_str in ("false", "no", "0"):
        outcome = False
    else:
        print(f"{RED}Outcome must be true/false (or yes/no).{NC}")
        return

    notes = None
    for i, a in enumerate(args):
        if a == "--notes" and i + 1 < len(args):
            notes = args[i + 1]

    preds = load_predictions()
    pred = next((p for p in preds if p["id"] == pid), None)

    if not pred:
        print(f"{RED}Prediction {pid} not found.{NC}")
        return

    if pred.get("resolved"):
        print(f"{YELLOW}Warning: {pid} is already resolved (outcome: {pred.get('outcome')}). Overwriting.{NC}")

    pred["resolved"] = True
    pred["resolved_at"] = now_iso()
    pred["outcome"] = outcome
    pred["brier_score"] = brier_score_single(pred["confidence"], outcome)

    if notes:
        existing = pred.get("notes") or ""
        pred["notes"] = f"{existing} | Resolution: {notes}" if existing else f"Resolution: {notes}"

    save_predictions(preds)

    b = pred["brier_score"]
    bc = GREEN if b < 0.1 else YELLOW if b < 0.25 else RED

    print(f"\n  {GREEN}{BOLD}Prediction resolved{NC}")
    print(f"  {BOLD}ID:{NC}         {pid}")
    print(f"  {BOLD}Statement:{NC}  {pred['statement']}")
    print(f"  {BOLD}Confidence:{NC} {pred['confidence']:.0%}")
    print(f"  {BOLD}Outcome:{NC}    {outcome_icon(outcome)}")
    print(f"  {BOLD}Brier:{NC}      {bc}{b:.4f}{NC}")
    print()

    # Update track record
    _update_track_record()


def cmd_edit(args):
    """Edit a prediction's confidence, tags, notes, resolution_date, or source."""
    if not args:
        print(f"{RED}Usage: oracle.py edit pred-XXXX --confidence 0.8 --tags crypto,fed --notes \"...\" --resolution-date 2026-06-01{NC}")
        return

    pid = args[0]
    preds = load_predictions()
    pred = next((p for p in preds if p["id"] == pid), None)

    if not pred:
        print(f"{RED}Prediction {pid} not found.{NC}")
        return

    changes = []
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("--confidence", "-c") and i + 1 < len(args):
            old = pred["confidence"]
            pred["confidence"] = float(args[i + 1])
            changes.append(f"confidence: {old:.0%} -> {pred['confidence']:.0%}")
            # Recalculate brier if already resolved
            if pred.get("resolved") and pred.get("outcome") is not None:
                pred["brier_score"] = brier_score_single(pred["confidence"], pred["outcome"])
            i += 2
        elif a in ("--tags", "-t") and i + 1 < len(args):
            pred["tags"] = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            changes.append(f"tags: {', '.join(pred['tags'])}")
            i += 2
        elif a in ("--notes", "-n") and i + 1 < len(args):
            pred["notes"] = args[i + 1]
            changes.append("notes updated")
            i += 2
        elif a in ("--resolution-date", "-d") and i + 1 < len(args):
            pred["resolution_date"] = parse_date(args[i + 1])
            changes.append(f"resolution_date: {date_short(pred['resolution_date'])}")
            i += 2
        elif a in ("--source", "-s") and i + 1 < len(args):
            pred["source"] = args[i + 1]
            changes.append(f"source: {pred['source']}")
            i += 2
        else:
            i += 1

    if not changes:
        print(f"{YELLOW}No changes specified.{NC}")
        return

    save_predictions(preds)
    print(f"\n  {GREEN}{BOLD}Prediction updated{NC}: {pid}")
    for c in changes:
        print(f"    {c}")
    print()


def cmd_score(args):
    """Compute and display scoring metrics."""
    preds = load_predictions()
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") is not None]

    if not resolved:
        print(f"\n  {DIM}No resolved predictions to score.{NC}\n")
        return

    # Overall metrics
    brier = compute_brier(resolved)
    accuracy = compute_accuracy(resolved)
    total = len(preds)
    n_resolved = len(resolved)
    n_pending = total - n_resolved
    n_correct = sum(1 for p in resolved if (p["confidence"] >= 0.5) == (p["outcome"] is True))

    print(f"\n  {BOLD}{'=' * 60}{NC}")
    print(f"  {BOLD}ORACLE SCORING{NC}")
    print(f"  {'=' * 60}\n")

    # Summary stats
    print(f"  {BOLD}Predictions:{NC}  {total} total, {n_resolved} resolved, {n_pending} pending")
    print(f"  {BOLD}Accuracy:{NC}     {n_correct}/{n_resolved} ({accuracy:.0%})")

    bc = GREEN if brier < 0.1 else YELLOW if brier < 0.2 else RED
    print(f"  {BOLD}Brier Score:{NC}  {bc}{brier:.4f}{NC}  {DIM}(lower is better, 0 = perfect){NC}")

    # Brier benchmark context
    if brier < 0.05:
        print(f"  {BOLD}Rating:{NC}      {GREEN}Exceptional{NC}")
    elif brier < 0.1:
        print(f"  {BOLD}Rating:{NC}      {GREEN}Very Good{NC}")
    elif brier < 0.15:
        print(f"  {BOLD}Rating:{NC}      {CYAN}Good{NC}")
    elif brier < 0.2:
        print(f"  {BOLD}Rating:{NC}      {YELLOW}Fair{NC}")
    elif brier < 0.25:
        print(f"  {BOLD}Rating:{NC}      {YELLOW}Below Average{NC}")
    else:
        print(f"  {BOLD}Rating:{NC}      {RED}Poor{NC}")

    # Per-prediction Brier breakdown
    print(f"\n  {BOLD}Per-prediction Brier:{NC}")
    for p in sorted(resolved, key=lambda x: x.get("brier_score", 1.0)):
        b = p.get("brier_score", 0)
        bc = GREEN if b < 0.1 else YELLOW if b < 0.25 else RED
        oc = outcome_icon(p["outcome"])
        stmt = p["statement"][:40] + "..." if len(p["statement"]) > 40 else p["statement"]
        print(f"    {p['id']}  {bc}{b:.4f}{NC}  conf={p['confidence']:.0%}  {oc}  {DIM}{stmt}{NC}")

    # Per-tag Brier scores
    tag_groups = {}
    for p in resolved:
        for tag in p.get("tags", []):
            tag_groups.setdefault(tag, []).append(p)
        if not p.get("tags"):
            tag_groups.setdefault("untagged", []).append(p)

    if tag_groups:
        print(f"\n  {BOLD}Per-tag Brier:{NC}")
        for tag in sorted(tag_groups.keys()):
            group = tag_groups[tag]
            tag_brier = compute_brier(group)
            tag_acc = compute_accuracy(group)
            bc = GREEN if tag_brier < 0.1 else YELLOW if tag_brier < 0.2 else RED
            print(f"    {tag:<15} {bc}{tag_brier:.4f}{NC}  ({len(group)} preds, {tag_acc:.0%} accuracy)")

    print()

    # Update track record
    _update_track_record()


def cmd_calibration(args):
    """Display calibration curve as text-based visualization."""
    preds = load_predictions()
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") is not None]

    if not resolved:
        print(f"\n  {DIM}No resolved predictions for calibration.{NC}\n")
        return

    buckets = compute_calibration_buckets(resolved)

    print(f"\n  {BOLD}{'=' * 60}{NC}")
    print(f"  {BOLD}CALIBRATION CURVE{NC}")
    print(f"  {'=' * 60}")
    print(f"  {DIM}Perfect calibration: predicted probability = actual frequency{NC}\n")

    # Header
    print(f"  {DIM}{'Bucket':<10} {'N':>3}  {'Predicted':>9}  {'Actual':>8}  {'Gap':>6}  Bar{NC}")
    print(f"  {DIM}{'─'*10} {'─'*3}  {'─'*9}  {'─'*8}  {'─'*6}  {'─'*30}{NC}")

    total_cal_error = 0
    n_nonempty = 0

    for label in sorted(buckets.keys()):
        b = buckets[label]
        count = b["count"]
        if count == 0:
            print(f"  {DIM}{label:<10} {count:>3}  {'':>9}  {'':>8}  {'':>6}  {'':>30}{NC}")
            continue

        predicted = (b["lo"] + b["hi"]) / 2
        actual = b["actual_yes"] / count
        gap = abs(predicted - actual)
        total_cal_error += gap
        n_nonempty += 1

        # Visual bar: show predicted (dim) and actual (colored)
        bar_width = 30
        pred_pos = int(predicted * bar_width)
        act_pos = int(actual * bar_width)

        bar_chars = list("░" * bar_width)
        # Mark predicted
        if pred_pos < bar_width:
            bar_chars[pred_pos] = "▓"
        # Mark actual
        if act_pos < bar_width:
            color = GREEN if gap < 0.1 else YELLOW if gap < 0.2 else RED
            bar_chars[act_pos] = "█"

        bar_str = "".join(bar_chars)

        gap_color = GREEN if gap < 0.1 else YELLOW if gap < 0.2 else RED

        print(f"  {label:<10} {count:>3}  {predicted:>8.0%}  {actual:>7.0%}  {gap_color}{gap:>+5.0%}{NC}  [{bar_str}]")

    avg_cal_error = total_cal_error / n_nonempty if n_nonempty else 0

    print(f"\n  {DIM}Legend: ▓ = predicted  █ = actual  Gap = |predicted - actual|{NC}")
    print(f"\n  {BOLD}Mean Calibration Error:{NC} ", end="")
    cc = GREEN if avg_cal_error < 0.05 else YELLOW if avg_cal_error < 0.1 else RED
    print(f"{cc}{avg_cal_error:.1%}{NC}  {DIM}(lower = better calibrated){NC}")
    print()


def cmd_track(args):
    """Display track record summary."""
    preds = load_predictions()
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") is not None]

    total = len(preds)
    n_resolved = len(resolved)
    n_pending = total - n_resolved

    print(f"\n  {BOLD}{'=' * 60}{NC}")
    print(f"  {BOLD}ORACLE TRACK RECORD{NC}")
    print(f"  {'=' * 60}\n")

    if not preds:
        print(f"  {DIM}No predictions yet.{NC}\n")
        return

    # Overview
    print(f"  {BOLD}Overview{NC}")
    print(f"    Total predictions:   {total}")
    print(f"    Resolved:            {n_resolved}")
    print(f"    Pending:             {n_pending}")

    if resolved:
        brier = compute_brier(resolved)
        accuracy = compute_accuracy(resolved)
        n_correct = sum(1 for p in resolved if (p["confidence"] >= 0.5) == (p["outcome"] is True))

        print(f"    Correct calls:       {n_correct}/{n_resolved} ({accuracy:.0%})")

        bc = GREEN if brier < 0.1 else YELLOW if brier < 0.2 else RED
        print(f"    Brier score:         {bc}{brier:.4f}{NC}")

        # Trend: last 5 vs all
        if n_resolved >= 5:
            recent = sorted(resolved, key=lambda x: x.get("resolved_at", ""))[-5:]
            recent_brier = compute_brier(recent)
            trend = "improving" if recent_brier < brier else "declining" if recent_brier > brier else "stable"
            tc = GREEN if trend == "improving" else YELLOW if trend == "stable" else RED
            print(f"    Last-5 Brier:        {tc}{recent_brier:.4f} ({trend}){NC}")

    # Date range
    created_dates = [p.get("created", "") for p in preds if p.get("created")]
    if created_dates:
        first = min(created_dates)[:10]
        last = max(created_dates)[:10]
        print(f"\n  {BOLD}Activity{NC}")
        print(f"    First prediction:    {first}")
        print(f"    Latest prediction:   {last}")

    # Pending predictions with upcoming resolution dates
    pending = [p for p in preds if not p.get("resolved")]
    if pending:
        upcoming = sorted(
            [p for p in pending if p.get("resolution_date")],
            key=lambda x: x["resolution_date"]
        )
        if upcoming:
            print(f"\n  {BOLD}Upcoming Resolutions{NC}")
            for p in upcoming[:5]:
                d = days_until(p["resolution_date"])
                dc = YELLOW if d is not None and d < 0 else DIM
                days_str = f"{d}d" if d is not None else "?"
                stmt = p["statement"][:35] + "..." if len(p["statement"]) > 35 else p["statement"]
                print(f"    {p['id']}  {date_short(p['resolution_date'])}  ({dc}{days_str}{NC})  {DIM}{stmt}{NC}")

    # Tag distribution
    tag_counts = {}
    for p in preds:
        for tag in p.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if tag_counts:
        print(f"\n  {BOLD}Tags{NC}")
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 20)
            print(f"    {tag:<15} {count:>3}  {DIM}{bar}{NC}")

    print()


def cmd_research(args):
    """Generate a research template for a prediction topic."""
    if not args:
        print(f"{RED}Usage: oracle.py research \"topic\"{NC}")
        return

    topic = " ".join(args)
    safe_name = topic.lower().replace(" ", "-")[:40]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"research-{timestamp}-{safe_name}.md"
    filepath = RESEARCH_DIR / filename

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    template = f"""# Oracle Research: {topic}
Date: {today_str()}
Status: draft

## Question
{topic}

## Base Rate
- Historical frequency:
- Reference class:
- Comparable events:

## Evidence FOR (increases probability)
1.
2.
3.

## Evidence AGAINST (decreases probability)
1.
2.
3.

## Key Uncertainties
-
-

## Information Sources
-
-

## Estimate
- Initial estimate:
- Adjusted estimate:
- Final confidence:

## Resolution Criteria
- How will this be determined?
- What source will confirm/deny?
- By what date?

## Post-Resolution Notes
(Fill in after resolution)
- Outcome:
- What did I miss?
- What would I change?
"""

    with open(filepath, "w") as f:
        f.write(template)

    print(f"\n  {GREEN}Research template created:{NC}")
    print(f"  {filepath}")
    print(f"\n  {DIM}Edit this file with your analysis, then create a prediction:{NC}")
    print(f"  {DIM}oracle.py predict \"{topic}\" --confidence 0.X --resolution-date YYYY-MM-DD{NC}")
    print()


def cmd_dashboard(args):
    """One-screen Oracle overview."""
    preds = load_predictions()
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") is not None]
    pending = [p for p in preds if not p.get("resolved")]

    total = len(preds)
    n_resolved = len(resolved)
    n_pending = len(pending)

    print(f"\n  {BOLD}{'═' * 60}{NC}")
    print(f"  {BOLD}  ORACLE DASHBOARD{NC}   {DIM}{now_iso()[:19]}{NC}")
    print(f"  {'═' * 60}\n")

    # ── Status bar ──
    brier = compute_brier(resolved) if resolved else None
    accuracy = compute_accuracy(resolved) if resolved else None

    print(f"  {BOLD}Predictions:{NC} {total}   ", end="")
    print(f"{GREEN}Resolved: {n_resolved}{NC}   ", end="")
    print(f"{CYAN}Pending: {n_pending}{NC}")

    if brier is not None:
        bc = GREEN if brier < 0.1 else YELLOW if brier < 0.2 else RED
        print(f"  {BOLD}Brier:{NC}       {bc}{brier:.4f}{NC}   ", end="")
        print(f"{BOLD}Accuracy:{NC} {accuracy:.0%}   ", end="")
        # Rating
        if brier < 0.1:
            print(f"{GREEN}Very Good{NC}")
        elif brier < 0.15:
            print(f"{CYAN}Good{NC}")
        elif brier < 0.2:
            print(f"{YELLOW}Fair{NC}")
        else:
            print(f"{RED}Needs Work{NC}")
    else:
        print(f"  {DIM}No scoring data yet.{NC}")

    # ── Calibration mini-bar ──
    if resolved:
        buckets = compute_calibration_buckets(resolved)
        print(f"\n  {BOLD}Calibration{NC}  {DIM}(predicted vs actual per bucket){NC}")
        nonempty = {k: v for k, v in buckets.items() if v["count"] > 0}
        for label in sorted(nonempty.keys()):
            b = nonempty[label]
            predicted = (b["lo"] + b["hi"]) / 2
            actual = b["actual_yes"] / b["count"]
            gap = abs(predicted - actual)
            gc = GREEN if gap < 0.1 else YELLOW if gap < 0.2 else RED

            # Mini bar
            bar_w = 20
            pred_fill = int(predicted * bar_w)
            act_fill = int(actual * bar_w)
            pred_bar = f"{DIM}{'▓' * pred_fill}{'░' * (bar_w - pred_fill)}{NC}"
            act_bar_c = GREEN if gap < 0.1 else YELLOW if gap < 0.2 else RED
            act_bar = f"{act_bar_c}{'█' * act_fill}{'░' * (bar_w - act_fill)}{NC}"

            print(f"    {label:<10} n={b['count']:<2}  P:{pred_bar} {predicted:.0%}  A:{act_bar} {actual:.0%}  {gc}{gap:+.0%}{NC}")

    # ── Overdue ──
    now = datetime.now(timezone.utc)
    overdue = [p for p in pending if p.get("resolution_date") and
               datetime.fromisoformat(p["resolution_date"]) < now]
    if overdue:
        print(f"\n  {YELLOW}{BOLD}OVERDUE ({len(overdue)}){NC}")
        for p in overdue:
            d = days_until(p["resolution_date"])
            stmt = p["statement"][:40] + "..." if len(p["statement"]) > 40 else p["statement"]
            print(f"    {YELLOW}{p['id']}{NC}  {abs(d) if d else '?'}d overdue  {DIM}{stmt}{NC}")

    # ── Upcoming ──
    upcoming = sorted(
        [p for p in pending if p.get("resolution_date") and
         datetime.fromisoformat(p["resolution_date"]) >= now],
        key=lambda x: x["resolution_date"]
    )
    if upcoming:
        print(f"\n  {CYAN}{BOLD}UPCOMING ({len(upcoming)}){NC}")
        for p in upcoming[:5]:
            d = days_until(p["resolution_date"])
            stmt = p["statement"][:40] + "..." if len(p["statement"]) > 40 else p["statement"]
            print(f"    {p['id']}  {date_short(p['resolution_date'])}  ({d}d)  {DIM}{stmt}{NC}")
        if len(upcoming) > 5:
            print(f"    {DIM}... and {len(upcoming) - 5} more{NC}")

    # ── Recent resolutions ──
    if resolved:
        recent = sorted(resolved, key=lambda x: x.get("resolved_at", ""))[-5:]
        recent.reverse()
        print(f"\n  {GREEN}{BOLD}RECENT RESOLUTIONS{NC}")
        for p in recent:
            b = p.get("brier_score", 0)
            bc = GREEN if b < 0.1 else YELLOW if b < 0.25 else RED
            stmt = p["statement"][:35] + "..." if len(p["statement"]) > 35 else p["statement"]
            print(f"    {p['id']}  {outcome_icon(p['outcome'])}  {bc}B={b:.4f}{NC}  {DIM}{stmt}{NC}")

    print(f"\n  {'─' * 60}")
    print(f"  {DIM}Commands: predict | list | resolve | score | calibration | track{NC}\n")


def cmd_export(args):
    """Export predictions as CSV to stdout."""
    preds = load_predictions()
    if not preds:
        print(f"{RED}No predictions to export.{NC}", file=sys.stderr)
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "statement", "confidence", "created", "resolution_date",
                     "resolved", "resolved_at", "outcome", "brier_score", "source", "tags", "notes"])

    for p in preds:
        writer.writerow([
            p.get("id", ""),
            p.get("statement", ""),
            p.get("confidence", ""),
            p.get("created", ""),
            p.get("resolution_date", ""),
            p.get("resolved", ""),
            p.get("resolved_at", ""),
            p.get("outcome", ""),
            p.get("brier_score", ""),
            p.get("source", ""),
            "|".join(p.get("tags", [])),
            p.get("notes", ""),
        ])

    print(output.getvalue())


def cmd_import_legacy(args):
    """Migrate known historical predictions from kingdom-metrics notes."""
    preds = load_predictions()
    existing_ids = {p["id"] for p in preds}

    # Known historical data from kingdom-metrics.json and daily logs
    # 4 scored predictions: pred-0001 (0.1369), pred-0003 (0.2304),
    # pred-0004 (0.0025), pred-0005 (0.2025). Avg Brier 0.1431
    # pred-0003: "US strikes Iran by Mar 31" -> YES
    # pred-0005: "US or Israel strikes Iran by Mar 31" -> YES
    # pred-0009: pending (Apr 30 Fed), pred-0010: pending (May 6 Fed)

    legacy = [
        {
            "id": "pred-0001",
            "statement": "Prediction 0001 (legacy -- details on Sage VPS oracle repo)",
            "confidence": 0.63,  # back-computed: brier=0.1369, outcome assumed YES -> (c-1)^2=0.1369 -> c=0.63
            "created": "2026-03-22T00:00:00+00:00",
            "resolution_date": "2026-03-31T00:00:00+00:00",
            "resolved": True,
            "resolved_at": "2026-04-01T17:12:00+00:00",
            "outcome": True,
            "brier_score": 0.1369,
            "source": "oracle-run.py",
            "tags": ["legacy", "macro"],
            "notes": "Migrated from kingdom-metrics. Brier 0.1369. Scored by resolution-scorer.py on 2026-04-01.",
        },
        {
            "id": "pred-0003",
            "statement": "US strikes Iran by Mar 31",
            "confidence": 0.52,  # back-computed: brier=0.2304, outcome YES -> (c-1)^2=0.2304 -> c=0.52
            "created": "2026-03-22T00:00:00+00:00",
            "resolution_date": "2026-03-31T00:00:00+00:00",
            "resolved": True,
            "resolved_at": "2026-04-01T17:12:00+00:00",
            "outcome": True,
            "brier_score": 0.2304,
            "source": "oracle-run.py",
            "tags": ["legacy", "geopolitical"],
            "notes": "Migrated from kingdom-metrics. Brier 0.2304. US strikes Iran confirmed.",
        },
        {
            "id": "pred-0004",
            "statement": "Prediction 0004 (legacy -- details on Sage VPS oracle repo)",
            "confidence": 0.95,  # back-computed: brier=0.0025, outcome YES -> (c-1)^2=0.0025 -> c=0.95
            "created": "2026-03-22T00:00:00+00:00",
            "resolution_date": "2026-03-31T00:00:00+00:00",
            "resolved": True,
            "resolved_at": "2026-04-01T17:12:00+00:00",
            "outcome": True,
            "brier_score": 0.0025,
            "source": "oracle-run.py",
            "tags": ["legacy"],
            "notes": "Migrated from kingdom-metrics. Brier 0.0025 (excellent). Scored by resolution-scorer.py on 2026-04-01.",
        },
        {
            "id": "pred-0005",
            "statement": "US or Israel strikes Iran by Mar 31",
            "confidence": 0.55,  # back-computed: brier=0.2025, outcome YES -> (c-1)^2=0.2025 -> c=0.55
            "created": "2026-03-22T00:00:00+00:00",
            "resolution_date": "2026-03-31T00:00:00+00:00",
            "resolved": True,
            "resolved_at": "2026-04-01T17:12:00+00:00",
            "outcome": True,
            "brier_score": 0.2025,
            "source": "oracle-run.py",
            "tags": ["legacy", "geopolitical"],
            "notes": "Migrated from kingdom-metrics. Brier 0.2025. US/Israel strikes Iran confirmed.",
        },
        {
            "id": "pred-0009",
            "statement": "Fed holds rates at April 30 FOMC meeting",
            "confidence": 0.85,
            "created": "2026-03-28T00:00:00+00:00",
            "resolution_date": "2026-04-30T00:00:00+00:00",
            "resolved": False,
            "resolved_at": None,
            "outcome": None,
            "brier_score": None,
            "source": "oracle-run.py / kalshi-scanner.py",
            "tags": ["legacy", "fed", "macro"],
            "notes": "Migrated from kingdom-metrics. Pending resolution Apr 30.",
        },
        {
            "id": "pred-0010",
            "statement": "Fed holds rates at May 6 FOMC meeting",
            "confidence": 0.80,
            "created": "2026-03-28T00:00:00+00:00",
            "resolution_date": "2026-05-06T00:00:00+00:00",
            "resolved": False,
            "resolved_at": None,
            "outcome": None,
            "brier_score": None,
            "source": "oracle-run.py / kalshi-scanner.py",
            "tags": ["legacy", "fed", "macro"],
            "notes": "Migrated from kingdom-metrics. Pending resolution May 6.",
        },
    ]

    imported = 0
    skipped = 0
    for lp in legacy:
        if lp["id"] in existing_ids:
            print(f"  {DIM}Skip {lp['id']} (already exists){NC}")
            skipped += 1
        else:
            preds.append(lp)
            existing_ids.add(lp["id"])
            print(f"  {GREEN}Imported {lp['id']}: {lp['statement'][:50]}{NC}")
            imported += 1

    if imported > 0:
        # Sort by ID
        preds.sort(key=lambda x: x.get("id", ""))
        save_predictions(preds)
        _update_track_record()

    print(f"\n  {BOLD}Migration complete:{NC} {imported} imported, {skipped} skipped (already exist)\n")


def _update_track_record():
    """Recompute and save track-record.json."""
    preds = load_predictions()
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") is not None]

    brier = compute_brier(resolved) if resolved else None
    accuracy = compute_accuracy(resolved) if resolved else None

    # Calibration data
    cal_data = {}
    if resolved:
        buckets = compute_calibration_buckets(resolved)
        for label, b in buckets.items():
            if b["count"] > 0:
                cal_data[label] = {
                    "count": b["count"],
                    "predicted": (b["lo"] + b["hi"]) / 2,
                    "actual": b["actual_yes"] / b["count"],
                }

    # Per-tag stats
    tag_stats = {}
    for p in resolved:
        for tag in p.get("tags", []):
            tag_stats.setdefault(tag, {"count": 0, "brier_sum": 0, "correct": 0})
            tag_stats[tag]["count"] += 1
            tag_stats[tag]["brier_sum"] += p.get("brier_score", 0)
            if (p["confidence"] >= 0.5) == (p["outcome"] is True):
                tag_stats[tag]["correct"] += 1

    for tag in tag_stats:
        ts = tag_stats[tag]
        ts["brier"] = round(ts["brier_sum"] / ts["count"], 4) if ts["count"] else None
        ts["accuracy"] = round(ts["correct"] / ts["count"], 4) if ts["count"] else None
        del ts["brier_sum"]

    record = {
        "updated": now_iso(),
        "total": len(preds),
        "resolved": len(resolved),
        "pending": len(preds) - len(resolved),
        "correct": sum(1 for p in resolved if (p["confidence"] >= 0.5) == (p["outcome"] is True)),
        "brier_score": round(brier, 4) if brier is not None else None,
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "calibration": cal_data,
        "tag_stats": tag_stats,
    }

    save_json(TRACK_RECORD_FILE, record)


def _update_kingdom_metrics(brier, n_resolved, n_total):
    """Update oracle section in kingdom-metrics.json."""
    try:
        metrics = load_json(METRICS_FILE)
        if not metrics:
            return
        oracle = metrics.get("revenue_engines", {}).get("oracle", {})
        oracle["predictions_scored"] = n_resolved
        oracle["predictions_logged"] = n_total
        if brier is not None:
            oracle["brier_score"] = round(brier, 4)
        oracle["last_oracle_sync"] = now_iso()
        metrics.setdefault("revenue_engines", {})["oracle"] = oracle
        save_json(METRICS_FILE, metrics)
    except Exception:
        pass  # Non-critical, don't break oracle over metrics sync


# ── MAIN ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "predict":
        cmd_predict(args)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "show":
        cmd_show(args)
    elif cmd == "resolve":
        cmd_resolve(args)
    elif cmd == "edit":
        cmd_edit(args)
    elif cmd == "score":
        cmd_score(args)
    elif cmd == "calibration":
        cmd_calibration(args)
    elif cmd == "track":
        cmd_track(args)
    elif cmd == "research":
        cmd_research(args)
    elif cmd == "dashboard":
        cmd_dashboard(args)
    elif cmd == "export":
        cmd_export(args)
    elif cmd == "import-legacy":
        cmd_import_legacy(args)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
