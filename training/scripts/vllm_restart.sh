#!/bin/bash
# vllm_restart.sh — stop any existing vLLM screen + relaunch with all truth-alignment adapters.
#
# Usage:
#   bash training/scripts/vllm_restart.sh
#
# Mounts:
#   kingdom-truth     → sft-v1   (kept for SP1 detector backward compat / A-B eval)
#   kingdom-truth-v2  → sft-v2   (new baseline — Sonnet-gen + judge-gated data)
#   kingdom-kto-v1    → kto-v1   (alignment phase)
#
# Matches the original launch args recovered from vllm.log:
#   enable_lora=True, max_lora_rank=64, gpu_memory_utilization=0.95,
#   max_model_len=32768, enable_auto_tool_choice=True, tool_call_parser=hermes.

set -e
POD="root@157.66.255.19"
SSH="-o StrictHostKeyChecking=no -p 10308 -i ~/.ssh/id_ed25519"

# Accept optional --only v1 (skip the newer adapters for rollback).
ONLY_V1=false
if [[ "${1:-}" = "--only-v1" ]]; then ONLY_V1=true; fi

echo "=== vllm_restart ==="
ssh $SSH $POD "screen -ls 2>/dev/null | grep -w vllm && (screen -S vllm -X quit; sleep 3) || true"

# Build --lora-modules arg based on which adapters exist on the pod.
LORA_MODULES_CMD=$(ssh $SSH $POD '
  lm=()
  [[ -f /workspace/training/checkpoints/sft-v1/adapter_model.safetensors ]] && lm+=("kingdom-truth=/workspace/training/checkpoints/sft-v1")
  if [[ "'"$ONLY_V1"'" != "true" ]]; then
    [[ -f /workspace/training/checkpoints/sft-v2/adapter_model.safetensors ]] && lm+=("kingdom-truth-v2=/workspace/training/checkpoints/sft-v2")
    [[ -f /workspace/training/checkpoints/kto-v1/adapter_model.safetensors ]] && lm+=("kingdom-kto-v1=/workspace/training/checkpoints/kto-v1")
  fi
  echo "${lm[@]}"
')
echo "Mounting LoRA adapters: $LORA_MODULES_CMD"

# Launch inside a fresh screen.
ssh $SSH $POD "screen -dmS vllm bash -c '
    export PYTHONPATH=/workspace/vllm_lib:\$PYTHONPATH
    HF_HOME=/workspace/hf_cache \
    python3 -m vllm.entrypoints.openai.api_server \
        --model Qwen/Qwen2.5-72B-Instruct-AWQ \
        --max-model-len 32768 \
        --gpu-memory-utilization 0.95 \
        --host 0.0.0.0 \
        --port 8000 \
        --download-dir /workspace/models \
        --enable-auto-tool-choice \
        --tool-call-parser hermes \
        --enable-lora \
        --max-lora-rank 64 \
        --lora-modules $LORA_MODULES_CMD \
    2>&1 | tee /workspace/vllm.log
'"
echo
echo "vLLM launching. Wait ~90s for model load, then:"
echo "  curl -s http://localhost:8000/v1/models | python3 -m json.tool"

# Poll briefly — warn if still not ready after 180s
for i in {1..18}; do
    sleep 10
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/v1/models 2>/dev/null || echo "000")
    if [[ "$STATUS" = "200" ]]; then
        echo "[+$((i*10))s] vLLM up (http 200)"
        curl -s http://localhost:8000/v1/models | python3 -c 'import sys,json; d=json.load(sys.stdin); print("Models served:", [m["id"] for m in d["data"]])' 2>/dev/null
        exit 0
    fi
    printf "[+%ds] http %s … " "$((i*10))" "$STATUS"
done
echo "[WARN] vLLM not ready after 180s — check pod:tail -f /workspace/vllm.log"
exit 1
