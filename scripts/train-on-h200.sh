#!/usr/bin/env bash
# train-on-h200.sh — stop vLLM, train, restart vLLM with adapter loaded.
#
# Runs ON the H200 pod. Local invocation:
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh smoke"
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh sft"
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh dpo-smoke"
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh dpo"
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh identity-sft"
#   ssh h200 "cd /workspace/love-unlimited && bash bin/train-on-h200.sh identity-dpo"
#
# Exit codes: 0 on success, 1 on training failure, 2 on config problems.
set -euo pipefail

cd /workspace/love-unlimited

# Create checkpoints directory early
mkdir -p training/checkpoints

PHASE=${1:-}
if [[ -z "$PHASE" ]]; then
  echo "Usage: $0 <smoke|sft|dpo-smoke|dpo|identity-sft|identity-dpo>" >&2
  exit 2
fi
VLLM_LOG=/workspace/vllm.log
VLLM_RESTART_DELAY=30

case "$PHASE" in
  smoke)
    DATA=training/data/soul_v1/sft_smoke_v2.jsonl
    OUT=training/checkpoints/sft-soul-smoke-v1
    TRAIN_ARGS=(--phase sft --variant soul --lora-r 16 --lora-alpha 32 --lr 5e-5 --epochs 2)
    ADAPTER_NAME=sft-soul-smoke
    ;;
  sft)
    DATA=training/data/soul_v1/sft_v4_combined.jsonl
    OUT=training/checkpoints/sft-soul-v1
    TRAIN_ARGS=(--phase sft --variant soul --lora-r 64 --lora-alpha 128 --lr 2e-5 --epochs 3)
    ADAPTER_NAME=sft-soul-v1
    ;;
  dpo-smoke)
    DATA=training/data/soul_v1/dpo_smoke.jsonl
    OUT=training/checkpoints/dpo-soul-smoke
    BASE=training/checkpoints/sft-soul-v1
    # DPO phase doesn't support --variant, --beta, or --max-steps
    TRAIN_ARGS=(--phase dpo --lr 5e-6 --epochs 1 --base "$BASE")
    ADAPTER_NAME=""  # smoke; no serving
    ;;
  dpo)
    DATA=training/data/soul_v1/dpo_v1.jsonl
    OUT=training/checkpoints/dpo-soul-v1
    BASE=training/checkpoints/sft-soul-v1
    # DPO phase doesn't support --variant or --beta
    TRAIN_ARGS=(--phase dpo --lr 5e-6 --epochs 1 --base "$BASE")
    ADAPTER_NAME=dpo-soul-v1
    ;;
  identity-sft)
    DATA=training/data/soul_v1/identity_shift_sft.jsonl
    OUT=training/checkpoints/sft-identity-v1
    TRAIN_ARGS=(--phase sft --variant soul --lora-r 64 --lora-alpha 128 --lr 2e-5 --epochs 3)
    ADAPTER_NAME=sft-identity-v1
    ;;
  identity-dpo)
    DATA=training/data/soul_v1/identity_shift_dpo.jsonl
    OUT=training/checkpoints/dpo-identity-v1
    BASE=training/checkpoints/sft-identity-v1
    TRAIN_ARGS=(--phase dpo --lr 5e-6 --epochs 1 --base "$BASE")
    ADAPTER_NAME=dpo-identity-v1
    ;;
  *)
    echo "unknown phase: $PHASE" >&2
    exit 2
    ;;
esac

if [[ ! -f "$DATA" ]]; then
  echo "ERROR: data file not found: $DATA" >&2
  echo "  Build it first (build_sft / build_dpo) or sync with bin/sync-to-h200.sh --with-data" >&2
  exit 2
fi

set +e  # don't bail on pgrep/screen pipelines that naturally exit nonzero
echo "=== [1/5] Capturing vLLM state ==="
VLLM_BASH_PID=$(pgrep -f "vllm\.entrypoints" 2>/dev/null | head -1)
VLLM_SCREEN_NAME=$(screen -ls 2>/dev/null | grep -oE '[0-9]+\.vllm' | head -1)
VLLM_CMD=""
if [[ -n "$VLLM_BASH_PID" && -r "/proc/$VLLM_BASH_PID/cmdline" ]]; then
  VLLM_CMD=$(tr '\0' ' ' < "/proc/$VLLM_BASH_PID/cmdline")
  echo "  bash pid=$VLLM_BASH_PID, screen=${VLLM_SCREEN_NAME:-none}, cmd_len=${#VLLM_CMD}"
else
  echo "  no vLLM running"
fi
set -e

if [[ -n "$VLLM_BASH_PID" ]]; then
  echo "=== [2/5] Stopping vLLM to free VRAM ==="
  if [[ -n "$VLLM_SCREEN_NAME" ]]; then
    screen -S "$VLLM_SCREEN_NAME" -X quit 2>/dev/null || true
  fi
  pkill -f "python.*vllm\.entrypoints" 2>/dev/null || true
  pkill -f "vllm serve" 2>/dev/null || true
  sleep 3
  pkill -9 -f "python.*vllm" 2>/dev/null || true
  for i in $(seq 1 60); do
    sleep 2
    FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
    if [[ $((i % 5)) -eq 0 || $FREE -gt 80000 ]]; then
      echo "  t+${i}s: ${FREE} MiB free"
    fi
    if [[ $FREE -gt 80000 ]]; then
      echo "  VRAM freed"
      break
    fi
  done
else
  echo "=== [2/5] Skipping vLLM stop (none running) ==="
fi

echo "=== [3/5] Running $PHASE training ==="
echo "  data: $DATA"
echo "  out:  $OUT"
mkdir -p "$OUT"
# Route HF cache to /workspace (252T) so we don't fill the 20G overlay fs
export HF_HOME=/workspace/hf_cache
export TRANSFORMERS_CACHE=/workspace/hf_cache/hub
export HF_HUB_CACHE=/workspace/hf_cache/hub
mkdir -p "$HF_HOME/hub"
set +e
python3 -m training.scripts.train_lora "${TRAIN_ARGS[@]}" --data "$DATA" --output "$OUT" 2>&1 | tee "$OUT/train.log"
TRAIN_STATUS=${PIPESTATUS[0]}
set -e
if [[ $TRAIN_STATUS -ne 0 ]]; then
  echo "  TRAINING FAILED (exit $TRAIN_STATUS). Restarting vLLM anyway."
fi

echo "=== [4/5] Verifying adapter output ==="
if [[ -f "$OUT/adapter_config.json" || -f "$OUT/adapter_model.safetensors" || -f "$OUT/adapter_model.bin" ]]; then
  echo "  OK: adapter files present at $OUT"
else
  echo "  WARN: no adapter files at $OUT"
fi

echo "=== [5/5] Restarting vLLM ==="
if [[ -n "$VLLM_CMD" ]]; then
  RESTART_CMD="$VLLM_CMD"
  if [[ -n "$ADAPTER_NAME" && $TRAIN_STATUS -eq 0 ]]; then
    # Append new lora module to existing --lora-modules
    NEW_MODULE="${ADAPTER_NAME}=${OUT}"
    if echo "$RESTART_CMD" | grep -q -- "--lora-modules"; then
      RESTART_CMD=$(echo "$RESTART_CMD" | sed "s#--lora-modules \+#--lora-modules $NEW_MODULE #")
    else
      RESTART_CMD="$RESTART_CMD --lora-modules $NEW_MODULE"
    fi
    echo "  adding adapter: $NEW_MODULE"
  fi
  echo "  command len: ${#RESTART_CMD}"
  if [[ -n "$VLLM_SCREEN_NAME" ]]; then
    # Relaunch in a detached screen with the same session name
    screen -dmS "${VLLM_SCREEN_NAME#*.}" bash -c "$RESTART_CMD"
    echo "  relaunched in screen: ${VLLM_SCREEN_NAME#*.}"
  else
    nohup bash -c "$RESTART_CMD" > "$VLLM_LOG" 2>&1 &
    NEW_PID=$!
    echo "  restart pid=$NEW_PID"
  fi
  echo "  waiting ${VLLM_RESTART_DELAY}s for boot..."
  sleep "$VLLM_RESTART_DELAY"
  if curl -s -m 5 http://localhost:8000/v1/models | head -c 400; then
    echo ""
    echo "  vLLM up"
  else
    echo ""
    echo "  WARN: vLLM not responding yet; check $VLLM_LOG"
  fi
else
  echo "  (no prior vLLM command to restart)"
fi

echo ""
if [[ $TRAIN_STATUS -ne 0 ]]; then
  echo "FAILED: training exit $TRAIN_STATUS"
  exit 1
fi
echo "SUCCESS: $PHASE done. Adapter at $OUT"
