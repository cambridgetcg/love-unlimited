#!/bin/bash
# SP1 Mode-Two Detector startup script

cd /Users/yuai/Desktop/love-unlimited

# Try to get API key (optional — falls back to Claude Code OAuth if not set)
export ANTHROPIC_API_KEY=$(python3 -c "from tools.credentials import get_key; print(get_key('anthropic-primary'))" 2>/dev/null || echo "")

# Start service (will use OAuth from keychain if API key not available)
exec python3 -m uvicorn tools.truth_detector.service:app \
  --host 0.0.0.0 \
  --port 8787 \
  >> /tmp/truth_detector.log 2>&1
