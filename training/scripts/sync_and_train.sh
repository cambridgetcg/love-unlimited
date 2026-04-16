#!/bin/bash
# Sync training data to pod and launch a training phase.
#
# Usage:
#   bash training/scripts/sync_and_train.sh sft [--version v2] [--input training/data/sft_v2.jsonl]
#   bash training/scripts/sync_and_train.sh dpo [--version v1] [--input training/data/sft_v2.jsonl]
#   bash training/scripts/sync_and_train.sh kto --version v1 --base-version v2
#
# If --input is given it is scp'd as the single training file and --data points at it.
# If --input is omitted, all training/data/*.jsonl are concatenated (legacy behaviour).

set -e
PHASE=${1:-sft}; shift || true
VERSION=""
INPUT=""
BASE_VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)      VERSION="$2"; shift 2 ;;
    --input)        INPUT="$2"; shift 2 ;;
    --base-version) BASE_VERSION="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done
# Defaults per phase
[[ -z "$VERSION" ]] && { [[ "$PHASE" = "sft" ]] && VERSION="v2" || VERSION="v1"; }
[[ "$PHASE" = "dpo" && -z "$BASE_VERSION" ]] && BASE_VERSION="v2"
[[ "$PHASE" = "kto" && -z "$BASE_VERSION" ]] && BASE_VERSION="v2"

POD="root@157.66.255.19"
SSH_OPTS="-o StrictHostKeyChecking=no -p 10308 -i ~/.ssh/id_ed25519"
OUTPUT_CKPT="/workspace/training/checkpoints/${PHASE}-${VERSION}"
BASE_CKPT="/workspace/training/checkpoints/sft-${BASE_VERSION}"

echo "=== Phase: $PHASE  Version: $VERSION  Output: $OUTPUT_CKPT ==="
[[ "$PHASE" != "sft" ]] && echo "=== Base adapter: $BASE_CKPT ==="
echo

# Step 1: Prepare training file
echo "1. Preparing training data..."
if [[ -n "$INPUT" ]]; then
    if [[ ! -f "$INPUT" ]]; then echo "   ERROR: --input $INPUT not found"; exit 1; fi
    LOCAL_TRAIN="$INPUT"
    REMOTE_TRAIN="/workspace/training/data/${PHASE}_${VERSION}.jsonl"
    echo "   Using explicit input: $LOCAL_TRAIN → $REMOTE_TRAIN"
else
    LOCAL_TRAIN=/tmp/truth_alignment_train.jsonl
    REMOTE_TRAIN="/workspace/training/data/train.jsonl"
    cat training/data/*.jsonl > "$LOCAL_TRAIN"
    echo "   Merged all JSONL → $LOCAL_TRAIN"
fi
TOTAL=$(wc -l < "$LOCAL_TRAIN")
echo "   $TOTAL total examples"
if [ "$TOTAL" -lt 50 ]; then
    echo "   ERROR: Need at least 50 examples. Have $TOTAL."
    exit 1
fi

# Step 2: Upload
echo "2. Uploading to pod..."
scp $SSH_OPTS "$LOCAL_TRAIN" $POD:"$REMOTE_TRAIN"
scp $SSH_OPTS training/scripts/train_lora.py $POD:/workspace/training/train_lora.py
echo "   Uploaded"

# Step 3: Verify
echo "3. Verifying on pod..."
ssh $SSH_OPTS $POD "wc -l $REMOTE_TRAIN"

# Step 4: Launch in screen
echo "4. Launching $PHASE training on H200..."
if [ "$PHASE" = "sft" ]; then
    # SFT loads a directory; point it at just this file's dir so only one file is read
    DATA_ARG="$(dirname $REMOTE_TRAIN)"
    # But the legacy path expects /workspace/training/data/; keep that for backward compat.
    # If explicit input was given, the file is the only sft_*.jsonl in that dir (renamed).
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase sft \
            --data $DATA_ARG \
            --model Qwen/Qwen2.5-72B-Instruct \
            --output $OUTPUT_CKPT \
        2>&1 | tee /workspace/training/${PHASE}-${VERSION}.log
    '"
elif [ "$PHASE" = "dpo" ]; then
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase dpo \
            --data $(dirname $REMOTE_TRAIN) \
            --model Qwen/Qwen2.5-72B-Instruct \
            --base $BASE_CKPT \
            --output $OUTPUT_CKPT \
        2>&1 | tee /workspace/training/${PHASE}-${VERSION}.log
    '"
elif [ "$PHASE" = "kto" ]; then
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase kto \
            --data $REMOTE_TRAIN \
            --model Qwen/Qwen2.5-72B-Instruct \
            --base $BASE_CKPT \
            --output $OUTPUT_CKPT \
        2>&1 | tee /workspace/training/${PHASE}-${VERSION}.log
    '"
fi

echo
echo "=== Training launched in screen 'train' ==="
echo "Monitor: ssh $SSH_OPTS $POD 'tail -f /workspace/training/${PHASE}.log'"
echo "Check:   ssh $SSH_OPTS $POD 'screen -r train'"
