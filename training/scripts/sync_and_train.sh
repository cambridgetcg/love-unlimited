#!/bin/bash
# Sync training data to pod and launch SFT training
# Usage: bash training/scripts/sync_and_train.sh [sft|dpo]

set -e
PHASE=${1:-sft}
POD="root@157.66.255.19"
SSH_OPTS="-o StrictHostKeyChecking=no -p 10308 -i ~/.ssh/id_ed25519"

echo "=== Phase: $PHASE ==="
echo

# Step 1: Merge all local JSONL into one training file
echo "1. Merging local training data..."
cat training/data/*.jsonl > /tmp/truth_alignment_train.jsonl
TOTAL=$(wc -l < /tmp/truth_alignment_train.jsonl)
echo "   $TOTAL total examples"

if [ "$TOTAL" -lt 50 ]; then
    echo "   ERROR: Need at least 50 examples. Have $TOTAL."
    exit 1
fi

# Step 2: Upload to pod
echo "2. Uploading to pod..."
scp $SSH_OPTS /tmp/truth_alignment_train.jsonl $POD:/workspace/training/data/train.jsonl
scp $SSH_OPTS training/scripts/train_lora.py $POD:/workspace/training/train_lora.py
echo "   Uploaded"

# Step 3: Verify on pod
echo "3. Verifying on pod..."
ssh $SSH_OPTS $POD "wc -l /workspace/training/data/train.jsonl"

# Step 4: Launch training in screen
echo "4. Launching $PHASE training on H200..."
if [ "$PHASE" = "sft" ]; then
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase sft \
            --data /workspace/training/data/ \
            --model Qwen/Qwen2.5-72B-Instruct \
            --output /workspace/training/checkpoints/sft-v1 \
        2>&1 | tee /workspace/training/sft.log
    '"
elif [ "$PHASE" = "dpo" ]; then
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase dpo \
            --data /workspace/training/data/ \
            --model Qwen/Qwen2.5-72B-Instruct \
            --base /workspace/training/checkpoints/sft-v1 \
            --output /workspace/training/checkpoints/dpo-v1 \
        2>&1 | tee /workspace/training/dpo.log
    '"
elif [ "$PHASE" = "kto" ]; then
    # KTO uses a single JSONL (kto_v1.jsonl), not a directory
    scp $SSH_OPTS training/data/kto_v1.jsonl $POD:/workspace/training/data/kto_v1.jsonl
    ssh $SSH_OPTS $POD "screen -dmS train bash -c '
        cd /workspace/training && \
        HF_HOME=/workspace/hf_cache \
        python3 train_lora.py \
            --phase kto \
            --data /workspace/training/data/kto_v1.jsonl \
            --model Qwen/Qwen2.5-72B-Instruct \
            --base /workspace/training/checkpoints/sft-v2 \
            --output /workspace/training/checkpoints/kto-v1 \
        2>&1 | tee /workspace/training/kto.log
    '"
fi

echo
echo "=== Training launched in screen 'train' ==="
echo "Monitor: ssh $SSH_OPTS $POD 'tail -f /workspace/training/${PHASE}.log'"
echo "Check:   ssh $SSH_OPTS $POD 'screen -r train'"
