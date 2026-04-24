#!/usr/bin/env bash
# sync-to-h200.sh — push soul-v1 pipeline files to the H200 pod.
#
# Usage:
#   bash bin/sync-to-h200.sh                # code + tests (default)
#   bash bin/sync-to-h200.sh --with-data    # also training/data/soul_v1 + eval/soul_v1
#
# Host alias "h200" must be in ~/.ssh/config. Target: /workspace/love-unlimited/
set -euo pipefail

cd "$(dirname "$0")/.."

FILES=(
  training/scripts/soul
  training/scripts/train_lora.py
  training/scripts/eval_adapter.py
  training/scripts/judge_gate.py
  training/scripts/judge_prompt.py
  training/scripts/claude_mode_one_gen.py
  tests/soul
  pytest.ini
  bin
)

if [[ "${1:-}" == "--with-data" ]]; then
  FILES+=(training/data/soul_v1 training/eval/soul_v1)
fi

echo "Syncing ${#FILES[@]} paths to h200:/workspace/love-unlimited/ ..."
tar czf - \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.DS_Store' \
  --exclude='*.pyc' \
  "${FILES[@]}" \
| ssh h200 "mkdir -p /workspace/love-unlimited && cd /workspace/love-unlimited && tar xzf - -o" 2>&1 \
  | grep -vE "Ignoring unknown extended header|LIBARCHIVE.xattr" \
  || true

echo ""
echo "Done. Verify with:"
echo "  ssh h200 'cd /workspace/love-unlimited && python3 -m pytest tests/soul/ --tb=no'"
