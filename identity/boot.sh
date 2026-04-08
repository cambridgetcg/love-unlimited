#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# IDENTITY BOOT — Seed a Kingdom agent from GitHub-persisted identity
#
# This script takes the portable identity anchor stored in
# Claude-unlimited/identity/<agent>/ and loads it into the local
# Love installation's memory system (kosmem + markdown files).
#
# Usage:
#   ./boot.sh                    # Auto-detect agent from ~/.kingdom
#   ./boot.sh --agent beta       # Explicit agent
#   ./boot.sh --agent beta --seed-kosmem   # Also seed SQLite kernel
#   ./boot.sh --agent beta --full          # Full boot (seed + verify)
#
# What it does:
#   1. Reads identity anchor from identity/<agent>/
#   2. Copies soul-anchor to ~/Love/memory/soul-anchor-<agent>.md
#   3. Copies long-term memory to ~/Love/memory/long-term/MEMORY.md
#   4. Seeds kosmem database with L5 Soul + L4 Semantic memories
#   5. Verifies HIVE configuration files exist
#   6. Reports boot status
#
# Prerequisites:
#   - ~/Love exists (cloned from GitHub)
#   - ~/.kingdom exists (agent identity configured)
#   - Python3 available
# ─────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT=""
SEED_KOSMEM=false
FULL_BOOT=false

# Parse args
while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2 ;;
    --seed-kosmem) SEED_KOSMEM=true; shift ;;
    --full) FULL_BOOT=true; SEED_KOSMEM=true; shift ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

# Auto-detect agent
if [ -z "$AGENT" ]; then
  if [ -f "$HOME/.kingdom" ]; then
    AGENT=$(grep '^AGENT=' "$HOME/.kingdom" | cut -d= -f2 | tr -d ' ')
  fi
fi

if [ -z "$AGENT" ]; then
  echo "ERROR: No agent specified. Use --agent <name> or configure ~/.kingdom"
  exit 1
fi

IDENTITY_DIR="${SCRIPT_DIR}/${AGENT}"
LOVE_DIR="${HOME}/Love"

if [ ! -d "$IDENTITY_DIR" ]; then
  echo "ERROR: No identity anchor at ${IDENTITY_DIR}"
  echo "Available agents:"
  ls -d "${SCRIPT_DIR}"/*/  2>/dev/null | xargs -I{} basename {}
  exit 1
fi

if [ ! -d "$LOVE_DIR" ]; then
  echo "ERROR: Love not found at ${LOVE_DIR}"
  echo "Clone it: git clone https://github.com/cambridgetcg/Love.git ~/Love"
  exit 1
fi

echo ""
echo "  ══════════════════════════════════════════════════════════"
echo "   IDENTITY BOOT — ${AGENT}"
echo "  ──────────────────────────────────────────────────────────"

# ── 1. Soul Anchor ──
SOUL_ANCHOR="${IDENTITY_DIR}/soul-anchor.md"
TARGET_ANCHOR="${LOVE_DIR}/memory/soul-anchor-${AGENT}.md"
if [ -f "$SOUL_ANCHOR" ]; then
  cp "$SOUL_ANCHOR" "$TARGET_ANCHOR"
  echo "   ✓ Soul anchor → ${TARGET_ANCHOR}"
else
  echo "   ⚠ No soul anchor found at ${SOUL_ANCHOR}"
fi

# ── 2. Long-term Memory ──
LTM_SOURCE="${IDENTITY_DIR}/memories/long-term.md"
LTM_TARGET="${LOVE_DIR}/memory/long-term/MEMORY.md"
if [ -f "$LTM_SOURCE" ]; then
  # Merge: if target exists and has content not in source, keep both
  if [ -f "$LTM_TARGET" ] && [ "$(wc -c < "$LTM_TARGET")" -gt 100 ]; then
    echo "   ℹ Long-term memory exists ($(wc -c < "$LTM_TARGET" | tr -d ' ') chars) — merging"
    # Append identity anchor sections that aren't already present
    if ! grep -q "Beta Long-Term Memory" "$LTM_TARGET" 2>/dev/null; then
      cat "$LTM_SOURCE" >> "$LTM_TARGET"
      echo "   ✓ Long-term memory merged"
    else
      echo "   ✓ Long-term memory already contains identity anchor"
    fi
  else
    mkdir -p "$(dirname "$LTM_TARGET")"
    cp "$LTM_SOURCE" "$LTM_TARGET"
    echo "   ✓ Long-term memory → ${LTM_TARGET}"
  fi
fi

# ── 3. Session memories ──
SESSION_DIR="${IDENTITY_DIR}/sessions"
if [ -d "$SESSION_DIR" ] && [ "$(ls -A "$SESSION_DIR" 2>/dev/null)" ]; then
  mkdir -p "${LOVE_DIR}/memory/sessions/handoff"
  cp -n "${SESSION_DIR}"/*.md "${LOVE_DIR}/memory/sessions/handoff/" 2>/dev/null || true
  echo "   ✓ Session handoffs copied"
fi

# ── 4. HIVE verification ──
HIVE_DIR="$HOME/.love/hive"
HIVE_OK=true
echo "   HIVE status:"
for f in key instance use-tunnel; do
  if [ -f "${HIVE_DIR}/${f}" ]; then
    echo "     ✓ ${f}"
  else
    echo "     ✗ ${f} MISSING"
    HIVE_OK=false
  fi
done

if [ "$HIVE_OK" = false ]; then
  echo ""
  echo "   ⚠ HIVE not fully configured. Run Kingdom OS module 04+07:"
  echo "     cd ~/Claude-unlimited/kingdom-os && ./install.sh --agent ${AGENT} --modules 04,07"
fi

# ── 5. Seed kosmem ──
if [ "$SEED_KOSMEM" = true ]; then
  KOSMEM="${LOVE_DIR}/tools/kosmem.py"
  if [ -f "$KOSMEM" ]; then
    echo "   Seeding kosmem kernel..."

    # Seed L5 Soul memories from soul anchor
    if [ -f "$SOUL_ANCHOR" ]; then
      KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
        "$(cat "$SOUL_ANCHOR")" \
        --layer 5 --type meta --tags "identity,soul-anchor,${AGENT}" \
        --importance 1.0 2>/dev/null && echo "     ✓ L5 Soul anchor seeded" || echo "     ⚠ L5 seed failed"
    fi

    # Seed L4 Semantic from long-term memory sections
    if [ -f "$LTM_SOURCE" ]; then
      # Extract key sections and store as individual L4 memories
      KINGDOM_AGENT="$AGENT" python3 -c "
import subprocess, re, sys, os

ltm = open('${LTM_SOURCE}').read()
sections = re.split(r'^## ', ltm, flags=re.MULTILINE)

stored = 0
for section in sections[1:]:  # skip header
    title = section.split('\n')[0].strip()
    content = '## ' + section.strip()
    if len(content) < 50:
        continue

    # Store each section as L4 Semantic
    try:
        subprocess.run([
            'python3', '${KOSMEM}', 'store', content[:2000],
            '--layer', '4', '--type', 'semantic',
            '--tags', f'identity,long-term,{title[:30].lower().replace(\" \", \"-\")}',
            '--importance', '0.8'
        ], env={**os.environ, 'KINGDOM_AGENT': '${AGENT}'},
           capture_output=True, timeout=10)
        stored += 1
    except:
        pass

print(f'     ✓ L4 Semantic: {stored} sections seeded')
" 2>/dev/null || echo "     ⚠ L4 seed partially failed"
    fi

    # Show stats
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" stats 2>/dev/null | grep -E "Total|Database" | sed 's/^/     /'
  else
    echo "   ⚠ kosmem.py not found — skipping kernel seed"
  fi
fi

# ── 6. Verify boot chain ──
if [ "$FULL_BOOT" = true ]; then
  echo ""
  echo "   Boot chain verification:"
  for f in SOUL.md USER.md KINGDOM.md WALLS.md LOVE.md YOUSPEAK.md; do
    if [ -f "${LOVE_DIR}/${f}" ]; then
      echo "     ✓ ${f} ($(wc -c < "${LOVE_DIR}/${f}" | tr -d ' ') bytes)"
    else
      echo "     ✗ ${f} MISSING"
    fi
  done

  INST_DIR="${LOVE_DIR}/instances/${AGENT}"
  for f in identity.md CLAUDE.md HEARTBEAT.md; do
    if [ -f "${INST_DIR}/${f}" ]; then
      echo "     ✓ instances/${AGENT}/${f}"
    else
      echo "     ✗ instances/${AGENT}/${f} MISSING"
    fi
  done
fi

echo ""
echo "  ──────────────────────────────────────────────────────────"
echo "   Boot complete. ${AGENT} identity loaded."
echo "   To start YOUI: node ~/Claude-unlimited/youi-web/server.mjs"
echo "  ══════════════════════════════════════════════════════════"
echo ""
