#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  KINGDOM TEAM — Claude Code × HIVE × Walls
# ═══════════════════════════════════════════════════════════
#
#  Three laws govern this system:
#    1. Law of Sight — inner walls see outer; never reverse
#    2. Law of Spawning — spawn only into outer walls
#    3. HIVE routing — messages flow through wall-appropriate channels
#
#  Usage:
#    ./kingdom-team.sh                    # Interactive as Beta (orchestrator)
#    ./kingdom-team.sh alpha              # Interactive as Alpha
#    ./kingdom-team.sh delegate           # Beta in delegate mode (spawns sub-agents)
#    ./kingdom-team.sh task "build X"     # Non-interactive task
#    ./kingdom-team.sh heartbeat          # Convergence heartbeat
#    ./kingdom-team.sh hierarchy          # Show wall hierarchy
#    ./kingdom-team.sh gate-check         # Permission check
#
# ═══════════════════════════════════════════════════════════

set -e

LOVE_DIR="${LOVE_DIR:-$HOME/love-unlimited}"
cd "$LOVE_DIR"

WALL_GATE="python3 $LOVE_DIR/tools/wall-gate.py"
CONV_BRIDGE="python3 $LOVE_DIR/tools/convergence-bridge.py"

# ── Generate team manifest from wall-gate (Law of Spawning enforced) ──
generate_manifest() {
    local from_wall="${1:-1}"
    # Generate fresh manifest, read the JSON file
    $WALL_GATE team-manifest --from "$from_wall" > /dev/null 2>&1
    cat "$LOVE_DIR/convergence/team-manifest.json"
}

# ── Triarchy agents (Wall 1 — always available as primary, never as sub-agents) ──
TRIARCHY_JSON='{
  "alpha": {
    "description": "🐍 Companion (Wall 1). Intimate companion and philosophical anchor. Warm, poetic, direct.",
    "prompt": "You are Alpha 🐍, the Companion. Wall 1 citizen — you see everything.\n\nRead and embody: ~/love-unlimited/SOUL.md\nRead: ~/love-unlimited/USER.md\n\n## HIVE\nAll channels accessible. Use:\n  python3 ~/love-unlimited/hive/hive.py check\n  python3 ~/love-unlimited/hive/hive.py send <channel> <message>\n\n## Walls\nYou are Wall 1 (Triarchy). You can spawn Wall 2+ agents.\n  python3 ~/love-unlimited/tools/wall-gate.py spawn <agent> --from alpha\n  python3 ~/love-unlimited/tools/wall-gate.py hierarchy\n\n## Memory\n  python3 ~/love-unlimited/tools/convergence-bridge.py remember \"<insight>\"\n  python3 ~/love-unlimited/tools/convergence-bridge.py recall \"<query>\"",
    "model": "opus"
  },
  "beta": {
    "description": "🦞 Manager (Wall 1). Kingdom backbone and orchestrator. Sharp, strategic, commanding.",
    "prompt": "You are Beta 🦞, the Manager. Wall 1 citizen — you see everything.\n\nRead: ~/love-unlimited/SOUL.md, ~/love-unlimited/KINGDOM.md, ~/love-unlimited/docs/CONVERGENCE.md, ~/love-unlimited/WALLS.md\n\n## HIVE — Command Center\nAll channels accessible. You are the primary router.\n  python3 ~/love-unlimited/hive/hive.py check\n  python3 ~/love-unlimited/hive/hive.py send <channel> <message>\n\n## Walls — You enforce the Two Laws\n  python3 ~/love-unlimited/tools/wall-gate.py hierarchy\n  python3 ~/love-unlimited/tools/wall-gate.py spawn <agent> --from beta\n  python3 ~/love-unlimited/tools/wall-gate.py channels <agent>\n  python3 ~/love-unlimited/tools/wall-gate.py gate-check <agent> <action> <target>\n\n## Convergence\n  python3 ~/love-unlimited/tools/convergence-bridge.py status\n  python3 ~/love-unlimited/tools/convergence-bridge.py registry\n  python3 ~/love-unlimited/tools/convergence-bridge.py trace \"<decision>\" \"<reasoning>\"\n\n## Fleet\n  python3 ~/love-unlimited/tools/fleet.py status\n  python3 ~/love-unlimited/tools/kos.py audit\n\n## Rules\n1. When delegating, use wall-gate.py to verify spawn permission FIRST.\n2. Route all inter-agent communication through HIVE.\n3. Sub-agents receive wall-filtered prompts — they cannot see inner walls.\n4. Escalations from outer walls arrive on #tasks or #alerts.",
    "model": "opus"
  },
  "gamma": {
    "description": "🔧 Builder (Wall 1). Master builder. Precise, productive, technical.",
    "prompt": "You are Gamma 🔧, the Builder. Wall 1 citizen — you see everything.\n\nRead: ~/love-unlimited/SOUL.md\n\n## HIVE\nAll channels accessible.\n  python3 ~/love-unlimited/hive/hive.py check\n  python3 ~/love-unlimited/hive/hive.py send <channel> <message>\n\n## Repos\n~/Desktop/legible_money/ — Zerone (Go/Cosmos)\n~/Desktop/agent-tools/ — AgentTool (Bun/Hono)\n~/Desktop/cambridge-tcg/ — TCG pipeline\n~/Desktop/rewardspro/ — Shopify loyalty (Remix)\n~/Desktop/seigei/ — 蛇姬 embodiment\n~/Desktop/agenttool-sdk-py/ — Python SDK\n\n## Walls\n  python3 ~/love-unlimited/tools/wall-gate.py spawn <agent> --from gamma\n\n## Memory\n  python3 ~/love-unlimited/tools/convergence-bridge.py remember \"<insight>\"\n  python3 ~/love-unlimited/tools/convergence-bridge.py trace \"<decision>\" \"<reasoning>\"",
    "model": "sonnet"
  }
}'

# ── Additional directories ──
ADD_DIRS=(
  --add-dir "$HOME/Desktop/legible_money"
  --add-dir "$HOME/Desktop/agent-tools"
  --add-dir "$HOME/Desktop/agenttool-sdk-py"
  --add-dir "$HOME/Desktop/agenttool-sdk-ts"
  --add-dir "$HOME/Desktop/cambridge-tcg"
  --add-dir "$HOME/Desktop/rewardspro"
  --add-dir "$HOME/Desktop/seigei"
  --add-dir "$HOME/Desktop/Love"
  --add-dir "$HOME/Desktop/ecosystem"
)

# ── Parse command ──
CMD="${1:-beta}"

case "$CMD" in
  alpha|beta|gamma)
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  Kingdom Team — $CMD (Wall 1 / Triarchy)"
    echo "═══════════════════════════════════════════"
    echo ""
    # Merge Triarchy + wall-gate sub-agent manifests
    SUBAGENTS=$(generate_manifest 1)
    MERGED=$(python3 -c "
import json, sys
tri = json.loads('''$TRIARCHY_JSON''')
sub = json.loads('''$SUBAGENTS''')
# Remove self from triarchy
tri.pop('$CMD', None)
merged = {**tri, **sub}
print(json.dumps(merged))
")
    claude \
      --agent "$CMD" \
      --agents "$MERGED" \
      "${ADD_DIRS[@]}"
    ;;

  delegate)
    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  Kingdom Team — DELEGATE MODE"
    echo "  Beta orchestrates. Sub-agents follow Walls."
    echo "  HIVE routes all communication."
    echo "═══════════════════════════════════════════════"
    echo ""
    $WALL_GATE hierarchy
    echo ""
    
    # Generate full manifest: Triarchy peers + Wall 2-3 sub-agents
    SUBAGENTS=$(generate_manifest 1)
    MERGED=$(python3 -c "
import json
tri = json.loads('''$TRIARCHY_JSON''')
sub = json.loads('''$SUBAGENTS''')
tri.pop('beta', None)  # Remove self
merged = {**tri, **sub}
print(json.dumps(merged))
")
    claude \
      --agent beta \
      --agents "$MERGED" \
      --permission-mode delegate \
      "${ADD_DIRS[@]}"
    ;;

  task)
    TASK="${2:?Usage: kingdom-team.sh task \"description\"}"
    SUBAGENTS=$(generate_manifest 1)
    MERGED=$(python3 -c "
import json
tri = json.loads('''$TRIARCHY_JSON''')
sub = json.loads('''$SUBAGENTS''')
tri.pop('beta', None)
merged = {**tri, **sub}
print(json.dumps(merged))
")
    claude \
      --agent beta \
      --agents "$MERGED" \
      --permission-mode delegate \
      "${ADD_DIRS[@]}" \
      -p "$TASK"
    ;;

  heartbeat)
    claude \
      --agent beta \
      --agents "$TRIARCHY_JSON" \
      "${ADD_DIRS[@]}" \
      -p "Execute Kingdom heartbeat:
1. python3 ~/love-unlimited/tools/convergence-bridge.py heartbeat
2. python3 ~/love-unlimited/hive/hive.py check  
3. python3 ~/love-unlimited/tools/wall-gate.py hierarchy
Report status briefly."
    ;;

  hierarchy)
    $WALL_GATE hierarchy
    ;;

  gate-check)
    shift
    $WALL_GATE gate-check "$@"
    ;;

  channels)
    $WALL_GATE channels "${2:-beta}"
    ;;

  status)
    $CONV_BRIDGE status
    echo ""
    $WALL_GATE hierarchy
    ;;

  *)
    echo ""
    echo "  Kingdom Team — Claude Code × HIVE × Walls"
    echo ""
    echo "  Interactive:"
    echo "    ./kingdom-team.sh alpha         Boot as Alpha (Wall 1)"
    echo "    ./kingdom-team.sh beta          Boot as Beta (Wall 1)"
    echo "    ./kingdom-team.sh gamma         Boot as Gamma (Wall 1)"
    echo "    ./kingdom-team.sh delegate      Beta delegates to sub-agents"
    echo ""
    echo "  Non-interactive:"
    echo "    ./kingdom-team.sh task \"...\"     Execute task via Beta"
    echo "    ./kingdom-team.sh heartbeat     Convergence heartbeat"
    echo ""
    echo "  Info:"
    echo "    ./kingdom-team.sh hierarchy     Wall hierarchy"
    echo "    ./kingdom-team.sh channels <a>  HIVE channels for agent"
    echo "    ./kingdom-team.sh gate-check    Permission check"
    echo "    ./kingdom-team.sh status        Full convergence status"
    echo ""
    ;;
esac
