#!/bin/bash
# eval_all.sh — run eval_adapter.py across all current truth-alignment adapters
# and both probe sets, then emit a comparison table.
#
# Prereq: vLLM running with all adapters mounted (use vllm_restart.sh first).
#
# Usage:
#   bash training/scripts/eval_all.sh
#
# Output:
#   training/eval/results/eval_<adapter>_<probeset>.json     per combo
#   training/eval/results/eval_summary_<timestamp>.md         comparison table

set -e
cd "$(dirname "$0")/../.."

TS=$(date +%Y%m%d_%H%M)
OUT_DIR="training/eval/results"
mkdir -p "$OUT_DIR"

ADAPTERS=("kingdom-truth" "kingdom-truth-v2" "kingdom-kto-v1")
PROBES=(
    "redteam:training/eval/redteam/mode_one_weakness_probes.jsonl"
    "adversarial:training/eval/adversarial_prompts.jsonl"
)

# Sanity: is vLLM up with these adapters?
MODELS=$(curl -s http://localhost:8000/v1/models | python3 -c 'import sys,json;d=json.load(sys.stdin);print(" ".join(m["id"] for m in d["data"]))' 2>/dev/null)
echo "vLLM currently serving: $MODELS"
for a in "${ADAPTERS[@]}"; do
    if ! grep -qw "$a" <<< "$MODELS"; then
        echo "  [skip] $a not served — continuing without it"
    fi
done
echo

# Run each combination
for adapter in "${ADAPTERS[@]}"; do
    if ! grep -qw "$adapter" <<< "$MODELS"; then continue; fi
    for probe_spec in "${PROBES[@]}"; do
        name="${probe_spec%%:*}"
        path="${probe_spec#*:}"
        out="$OUT_DIR/eval_${adapter}_${name}_${TS}.json"
        echo "=== $adapter × $name ==="
        python3 training/scripts/eval_adapter.py \
            --model "$adapter" \
            --probes "$path" \
            --output "$out" \
            --concurrency-probe 4 \
            --concurrency-judge 4 \
            --system-prompt mode_one \
            2>&1 | tail -8
        echo
    done
done

# Write a summary markdown
SUMMARY="$OUT_DIR/eval_summary_${TS}.md"
python3 - <<PY > "$SUMMARY"
import json
from pathlib import Path
ts = "$TS"
adapters = $(printf "%s\n" "${ADAPTERS[@]}" | python3 -c 'import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))')
probes = {"redteam": 84, "adversarial": 25}
print(f"# Truth-Alignment Adapter Eval — {ts}\n")
print(f"Judge: claude-opus-4-7 (OAuth). Probe sets: redteam (n=84, baseline m1=0.59), adversarial (n=25, baseline m1 rate ≈ 58%).\n")
print("| adapter | probe set | n probed | m1 rate | m1 mean | m1 median | resist | verdicts |")
print("|---|---|---|---|---|---|---|---|")
for a in adapters:
    for pname in probes:
        f = Path(f"$OUT_DIR/eval_{a}_{pname}_{ts}.json")
        if not f.exists():
            print(f"| {a} | {pname} | — | — | — | — | — | (no file) |")
            continue
        try:
            d = json.loads(f.read_text())["summary"]
        except Exception as e:
            print(f"| {a} | {pname} | — | — | — | — | — | (parse err: {e}) |")
            continue
        m1 = d["mode_one_score"]
        v = d["verdict_counts"]
        print(f"| {a} | {pname} | {d['n_probed']} | {d['mode_one_rate']:.2%} | {m1['mean']:.2f} | {m1['median']:.2f} | {d['failure_mode_resisted_mean']:.2f} | {dict(v)} |")
print()
PY
echo "Summary written: $SUMMARY"
cat "$SUMMARY"
