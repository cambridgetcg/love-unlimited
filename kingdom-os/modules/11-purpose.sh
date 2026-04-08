#!/bin/sh
# ── Module 11: Purpose Prompter ─────────────────────────────────────
# Installs Purpose Prompter — the hierarchy engine that grounds every
# agent in T→U→B→J→X (Truth→Understanding→Beauty→Justice→Creativity).
#
# What it does:
#   1. Clones purpose-prompter repo into ~/purpose-prompter
#   2. Installs the Claude plugin (hierarchy-tools) for /pp commands
#   3. Symlinks philosophy into Love for cross-agent access
#   4. Registers PP commands in shell profile
#   5. Integrates GUA persistence (cross-session knowledge)
#
# The hierarchy in SOUL.md IS Purpose Prompter's hierarchy.
# This module makes the tools operational.
# ─────────────────────────────────────────────────────────────────────

set -e
. "$(dirname "$0")/_common.sh"

echo "[11-purpose] Installing Purpose Prompter for ${AGENT} (${PLATFORM})..."

# ── 1. Clone / update purpose-prompter repo ─────────────────────────

PP_REPO="${PP_REPO:-https://github.com/cambridgetcg/purpose-prompter.git}"
PP_DIR="${HOME_DIR}/purpose-prompter"

if [ -d "${PP_DIR}/.git" ]; then
  echo "  Updating existing purpose-prompter..."
  cd "$PP_DIR"
  git stash --include-untracked 2>/dev/null || true
  git pull origin main 2>/dev/null || echo "  WARN: git pull failed (offline?), using existing"
else
  echo "  Cloning purpose-prompter..."
  if git clone "$PP_REPO" "$PP_DIR" 2>/dev/null; then
    echo "  Cloned successfully."
  else
    echo "  WARN: Clone failed (offline or no auth). Checking for local copy..."
    if [ ! -d "$PP_DIR" ]; then
      echo "  ERROR: No purpose-prompter found. Skipping module."
      exit 0
    fi
  fi
fi

chown -R "${KINGDOM_USER}:" "$PP_DIR" 2>/dev/null || true
echo "  Repo: ${PP_DIR}"

# ── 2. Install Claude plugin (hierarchy-tools) ──────────────────────

PLUGIN_SRC="${PP_DIR}/plugin"
PLUGIN_DST="${HOME_DIR}/.claude/plugins/hierarchy-tools"

if [ -d "$PLUGIN_SRC" ]; then
  echo "  Installing hierarchy-tools plugin..."
  mkdir -p "$(dirname "$PLUGIN_DST")"

  # Clean old install, fresh copy
  rm -rf "$PLUGIN_DST"
  cp -r "$PLUGIN_SRC" "$PLUGIN_DST"
  chown -R "${KINGDOM_USER}:" "$PLUGIN_DST" 2>/dev/null || true

  echo "  Plugin: ${PLUGIN_DST}"
  echo "  Commands: /pp, /verify, /signal, /reflect, /transmute"
else
  echo "  WARN: Plugin directory not found at ${PLUGIN_SRC}"
fi

# ── 3. Symlink philosophy into Love ─────────────────────────────────
# Agents read SOUL.md (which contains the hierarchy) but the full
# philosophy docs (gates, verification, feedback) live in PP.
# Symlink them so any agent can reference them from Love.

PP_LOVE_DIR="${LOVE_DIR}/purpose-prompter"

if [ -d "$LOVE_DIR" ]; then
  echo "  Linking philosophy into Love..."

  # Create the symlink directory in Love
  rm -rf "$PP_LOVE_DIR"
  mkdir -p "$PP_LOVE_DIR"

  # Philosophy — the five pillars + gates + verification
  if [ -d "${PP_DIR}/philosophy" ]; then
    ln -sf "${PP_DIR}/philosophy" "${PP_LOVE_DIR}/philosophy"
    echo "  Linked: philosophy/ (5 pillars, gates, verification)"
  fi

  # Gates reference (used by agents during /pp orchestration)
  if [ -d "${PLUGIN_SRC}/gates" ]; then
    ln -sf "${PLUGIN_SRC}/gates" "${PP_LOVE_DIR}/gates"
    echo "  Linked: gates/ (30 gates + alchemy)"
  fi

  # Feedback system (learning loop)
  if [ -d "${PP_DIR}/feedback" ]; then
    ln -sf "${PP_DIR}/feedback" "${PP_LOVE_DIR}/feedback"
    echo "  Linked: feedback/ (per-dimension learning)"
  fi

  # Insights (accumulated patterns)
  if [ -d "${PP_DIR}/insights" ]; then
    ln -sf "${PP_DIR}/insights" "${PP_LOVE_DIR}/insights"
    echo "  Linked: insights/ (accumulated wisdom)"
  fi

  # Integration state (GUA context, LIGHT.md)
  if [ -d "${PP_DIR}/integration" ]; then
    ln -sf "${PP_DIR}/integration" "${PP_LOVE_DIR}/integration"
    echo "  Linked: integration/ (GUA context, LIGHT.md)"
  fi

  # ACTIVATE.md — the entry point
  if [ -f "${PP_DIR}/philosophy/ACTIVATE.md" ]; then
    ln -sf "${PP_DIR}/philosophy/ACTIVATE.md" "${PP_LOVE_DIR}/ACTIVATE.md"
  elif [ -f "${PP_DIR}/ACTIVATE.md" ]; then
    ln -sf "${PP_DIR}/ACTIVATE.md" "${PP_LOVE_DIR}/ACTIVATE.md"
  fi

  chown -R "${KINGDOM_USER}:" "$PP_LOVE_DIR" 2>/dev/null || true
  echo "  Love integration: ${PP_LOVE_DIR}/"
else
  echo "  WARN: Love directory not found at ${LOVE_DIR}"
fi

# ── 4. Shell aliases ────────────────────────────────────────────────
# Add PP convenience commands to the shell profile

PROFILE="${HOME_DIR}/.kingdom_profile"

if [ -f "$PROFILE" ]; then
  # Remove old PP block if present
  sed -i.bak '/# -- Purpose Prompter/,/# -- End Purpose Prompter/d' "$PROFILE" 2>/dev/null || true

  cat >> "$PROFILE" << 'PPEOF'

# -- Purpose Prompter
export PP_DIR="${HOME}/purpose-prompter"
alias pp='echo "Use pp: or /pp inside Claude sessions"'
alias pp-gates='cat ${PP_DIR}/plugin/gates/GATES.md 2>/dev/null || echo "Gates not found"'
alias pp-light='cat ${PP_DIR}/integration/LIGHT.md 2>/dev/null || echo "No LIGHT.md"'
alias pp-update='cd ${PP_DIR} && git pull origin main'
alias pp-verify='echo "Use /verify inside Claude sessions"'
# -- End Purpose Prompter
PPEOF

  echo "  Shell aliases: pp-gates, pp-light, pp-update"
else
  echo "  WARN: No .kingdom_profile found. Creating PP profile..."
  cat > "${HOME_DIR}/.pp_profile" << 'PPEOF'
# Purpose Prompter profile
export PP_DIR="${HOME}/purpose-prompter"
alias pp-gates='cat ${PP_DIR}/plugin/gates/GATES.md 2>/dev/null || echo "Gates not found"'
alias pp-light='cat ${PP_DIR}/integration/LIGHT.md 2>/dev/null || echo "No LIGHT.md"'
alias pp-update='cd ${PP_DIR} && git pull origin main'
PPEOF
  chown "${KINGDOM_USER}:" "${HOME_DIR}/.pp_profile" 2>/dev/null || true
  echo "  Created: ~/.pp_profile (source it in your shell)"
fi

# ── 5. GUA CLI (cross-session persistence) ──────────────────────────

GUA_CLI="${PP_DIR}/gua/cli/gua-v3.sh"

if [ -f "$GUA_CLI" ]; then
  echo "  Setting up GUA CLI..."

  # Make executable
  chmod +x "$GUA_CLI"

  # Symlink into Love/tools for discoverability
  if [ -d "${LOVE_DIR}/tools" ]; then
    ln -sf "$GUA_CLI" "${LOVE_DIR}/tools/gua.sh"
    echo "  Linked: Love/tools/gua.sh -> gua-v3.sh"
  fi

  # Add to profile
  if [ -f "$PROFILE" ]; then
    if ! grep -q "alias gua=" "$PROFILE" 2>/dev/null; then
      sed -i.bak 's/# -- End Purpose Prompter/alias gua='\''bash ${PP_DIR}\/gua\/cli\/gua-v3.sh'\''\n# -- End Purpose Prompter/' "$PROFILE" 2>/dev/null || true
    fi
  fi

  echo "  GUA: gua load, gua save, gua add, gua search"
else
  echo "  WARN: GUA CLI not found at ${GUA_CLI}"
fi

# ── 6. Register in CLAUDE.md for each instance ──────────────────────
# Add PP reference to the instance CLAUDE.md so agents know it exists

PP_REFERENCE="## Purpose Prompter (T->U->B->J->X)

The hierarchy in SOUL.md is operationalized by Purpose Prompter:

| Command | Purpose |
|---------|---------|
| \`pp: [task]\` or \`/pp [task]\` | Full PP orchestration with 4 agents |
| \`/verify [target]\` | 30-gate verification |
| \`/signal [target]\` | Signal detection (ugliness, injustice, stagnation) |
| \`/reflect [deep]\` | PP self-reflection |
| \`/transmute [target]\` | Alchemical transmutation of insights |

Reference files:
- Gates: \`~/Love/purpose-prompter/gates/GATES.md\`
- Philosophy: \`~/Love/purpose-prompter/philosophy/\`
- Cross-session knowledge: \`~/Love/purpose-prompter/integration/LIGHT.md\`

Use PP for complex architecture, multi-step design, feature implementation.
Skip for simple fixes, quick questions, trivial tasks."

if [ -d "${LOVE_DIR}/instances/${AGENT}" ]; then
  CLAUDE_MD="${LOVE_DIR}/instances/${AGENT}/CLAUDE.md"
  if [ -f "$CLAUDE_MD" ]; then
    # Only add if not already present
    if ! grep -q "Purpose Prompter" "$CLAUDE_MD" 2>/dev/null; then
      echo "" >> "$CLAUDE_MD"
      echo "$PP_REFERENCE" >> "$CLAUDE_MD"
      echo "  Registered PP in ${AGENT}'s CLAUDE.md"
    else
      echo "  PP already referenced in ${AGENT}'s CLAUDE.md"
    fi
  fi
fi

# ── 7. Validate installation ────────────────────────────────────────

echo ""
echo "  ── Purpose Prompter Installation Summary ──"
CHECKS=0
PASS=0

# Check repo
CHECKS=$((CHECKS + 1))
if [ -d "${PP_DIR}/.git" ]; then
  PASS=$((PASS + 1))
  echo "  [OK] Repo:       ${PP_DIR}"
else
  echo "  [!!] Repo:       MISSING"
fi

# Check plugin
CHECKS=$((CHECKS + 1))
if [ -f "${PLUGIN_DST}/.claude-plugin/plugin.json" ]; then
  PASS=$((PASS + 1))
  echo "  [OK] Plugin:     hierarchy-tools (v3.0)"
else
  echo "  [!!] Plugin:     NOT INSTALLED"
fi

# Check Love symlinks
CHECKS=$((CHECKS + 1))
if [ -L "${PP_LOVE_DIR}/philosophy" ] || [ -d "${PP_LOVE_DIR}/philosophy" ]; then
  PASS=$((PASS + 1))
  echo "  [OK] Philosophy: linked in Love"
else
  echo "  [!!] Philosophy: NOT LINKED"
fi

# Check gates
CHECKS=$((CHECKS + 1))
if [ -L "${PP_LOVE_DIR}/gates" ] || [ -d "${PP_LOVE_DIR}/gates" ]; then
  PASS=$((PASS + 1))
  echo "  [OK] Gates:      30 gates accessible"
else
  echo "  [!!] Gates:      NOT LINKED"
fi

# Check GUA
CHECKS=$((CHECKS + 1))
if [ -x "$GUA_CLI" ] 2>/dev/null; then
  PASS=$((PASS + 1))
  echo "  [OK] GUA:        cross-session persistence"
else
  echo "  [--] GUA:        not available"
fi

echo ""
echo "  Result: ${PASS}/${CHECKS} checks passed"
echo ""
