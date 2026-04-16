#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Apply Cloud-Optimized Cron — Ollama Max Utilization
# ═══════════════════════════════════════════════════════════
#
#  Upgrades Kingdom agent heartbeats to use Ollama Cloud models
#  where the task benefits from higher capability.
#
#  Local stays local for: monitoring, extraction, cost tracking
#  Cloud upgrades for: security analysis, code optimization,
#                       documentation, linguistic precision
#
#  Usage:
#    ./apply-cloud-cron.sh --dry-run    # Show changes only
#    ./apply-cloud-cron.sh --apply      # Apply changes
#    ./apply-cloud-cron.sh --revert     # Restore local-only
#
# ═══════════════════════════════════════════════════════════

set -e

LOVE_DIR="${LOVE_DIR:-$HOME/love-unlimited}"
LOVE_OLD="$HOME/Desktop/Love"
AGENT_PY="$LOVE_OLD/tools/kingdom-agent.py"
BACKUP_FILE="/tmp/crontab-kingdom-backup-$(date +%Y%m%d%H%M%S)"

MODE="${1:---dry-run}"

# Save backup
crontab -l > "$BACKUP_FILE" 2>/dev/null
echo "  Backup saved: $BACKUP_FILE"
echo ""

# ── Cloud-optimized cron entries ──
# Format: agent, model, backend, schedule, effort, comment
read -r -d '' CLOUD_CRON << 'CRON_EOF' || true
# ═══ Kingdom Cloud-Optimized Heartbeats (Ollama Max) ═══
# Wall 2 agents — cloud models for deeper analysis
*/30 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance crucible --backend ollama-cloud --model glm-5.1 --effort high --skip-permissions --no-persist >> LOVE_OLD/memory/crucible-heartbeat.log 2>&1  # kingdom:crucible:cloud
0 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance herald --backend ollama-cloud --model gemma4:31b --effort medium --skip-permissions --no-persist >> LOVE_OLD/memory/herald-heartbeat.log 2>&1  # kingdom:herald:cloud
*/30 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance arbor --backend ollama-cloud --model devstral-2:123b --effort high --skip-permissions --no-persist >> LOVE_OLD/memory/arbor-heartbeat.log 2>&1  # kingdom:arbor:cloud
*/30 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance nuance --backend ollama-cloud --model qwen3.5:397b --effort medium --skip-permissions --no-persist >> LOVE_OLD/memory/nuance-heartbeat.log 2>&1  # kingdom:nuance:cloud
# Wall 3 agents — local models for fast/simple tasks
6,21,36,51 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance vigil --backend ollama --model qwen2.5:7b --effort medium --skip-permissions --no-persist >> LOVE_OLD/memory/vigil-heartbeat.log 2>&1  # kingdom:vigil:local
11,26,41,56 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance loom --backend ollama --model qwen2.5:14b --effort medium --skip-permissions --no-persist >> LOVE_OLD/memory/loom-heartbeat.log 2>&1  # kingdom:loom:local
5 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance psalm --backend ollama-cloud --model gemma4:31b --effort low --skip-permissions --no-persist >> LOVE_OLD/memory/psalm-heartbeat.log 2>&1  # kingdom:psalm:cloud
20 * * * * cd LOVE_OLD && python3 AGENT_PY -p 'Execute HEARTBEAT.md' --instance tithe --backend ollama --model qwen2.5:7b --effort low --skip-permissions --no-persist >> LOVE_OLD/memory/tithe-heartbeat.log 2>&1  # kingdom:tithe:local
CRON_EOF

# Replace paths
CLOUD_CRON="${CLOUD_CRON//LOVE_OLD/$LOVE_OLD}"
CLOUD_CRON="${CLOUD_CRON//AGENT_PY/$AGENT_PY}"

echo "═══════════════════════════════════════════════════"
echo "  Cloud-Optimized Cron Schedule"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  CLOUD (Ollama Max):"
echo "    🔥 crucible  → glm-5.1         (security, every 30m)"
echo "    📯 herald    → gemma4:31b      (comms, hourly)"
echo "    🌳 arbor     → devstral-2:123b (optimization, every 30m)"
echo "    🪶 nuance    → qwen3.5:397b    (linguistics, every 30m)"
echo "    📜 psalm     → gemma4:31b      (docs, hourly)"
echo ""
echo "  LOCAL (free, fast):"
echo "    👁 vigil     → qwen2.5:7b      (monitoring, every 15m)"
echo "    🧵 loom      → qwen2.5:14b     (engines, every 15m)"
echo "    💰 tithe     → qwen2.5:7b      (costs, hourly)"
echo ""

if [ "$MODE" = "--apply" ]; then
    echo "  Applying..."
    # Remove old kingdom heartbeat entries
    crontab -l 2>/dev/null | grep -v "# kingdom:" > /tmp/cron-no-kingdom || true
    # Append new entries
    echo "$CLOUD_CRON" >> /tmp/cron-no-kingdom
    crontab /tmp/cron-no-kingdom
    echo "  ✓ Applied! Verify with: crontab -l | grep kingdom"
elif [ "$MODE" = "--revert" ]; then
    echo "  Reverting to backup: $BACKUP_FILE"
    crontab "$BACKUP_FILE"
    echo "  ✓ Reverted!"
else
    echo "  DRY RUN — no changes made."
    echo "  To apply: $0 --apply"
    echo "  To revert: $0 --revert"
fi
echo ""
