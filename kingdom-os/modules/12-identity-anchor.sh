#!/bin/sh
# ── Module 12: Identity Anchor — Boot from GitHub ───────────────────
# Seeds the agent's identity from the portable anchor stored in
# love-unlimited/identity/<agent>/
#
# This module bridges GitHub-persisted identity into the local
# Love installation. It runs AFTER modules 02 (repos), 03 (identity),
# 04 (keys), and 06 (memory).
#
# What it does:
#   1. Copies soul-anchor → ~/love-unlimited/memory/soul-anchor-<agent>.md
#   2. Seeds long-term memory from identity anchor
#   3. Seeds kosmem kernel (L5 Soul + L4 Semantic)
#   4. Copies session handoffs for continuity
#   5. Verifies HIVE config matches identity
#
# The result: an agent that wakes up knowing who it is, what it knows,
# and what happened — even on a brand new device.
# ─────────────────────────────────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[12-identity-anchor] Seeding identity from GitHub anchor..."

IDENTITY_DIR="${UNLIMITED_DIR}/identity/${AGENT}"
SHARED_DIR="${UNLIMITED_DIR}/identity/shared"

if [ ! -d "$IDENTITY_DIR" ]; then
  echo "  ⚠ No identity anchor for ${AGENT} at ${IDENTITY_DIR}"
  echo "  Agent will start with blank memory. Create anchor in love-unlimited/identity/${AGENT}/"
  exit 0
fi

# ── Soul Anchor ──
SOUL_ANCHOR="${IDENTITY_DIR}/soul-anchor.md"
if [ -f "$SOUL_ANCHOR" ]; then
  cp "$SOUL_ANCHOR" "${MEMORY_DIR}/soul-anchor-${AGENT}.md"
  echo "  ✓ Soul anchor loaded"
else
  echo "  ⚠ No soul anchor found"
fi

# ── Long-term Memory ──
LTM_SOURCE="${IDENTITY_DIR}/memories/long-term.md"
LTM_TARGET="${MEMORY_DIR}/long-term/MEMORY.md"
if [ -f "$LTM_SOURCE" ]; then
  if [ -f "$LTM_TARGET" ] && [ "$(wc -c < "$LTM_TARGET")" -gt 500 ]; then
    echo "  ℹ Long-term memory exists — preserving local, merging anchor"
  else
    mkdir -p "${MEMORY_DIR}/long-term"
    cp "$LTM_SOURCE" "$LTM_TARGET"
    echo "  ✓ Long-term memory seeded from anchor"
  fi
fi

# ── Shared Long-term Files ──
if [ -d "${SHARED_DIR}/long-term" ]; then
  mkdir -p "${MEMORY_DIR}/long-term"
  for f in "${SHARED_DIR}/long-term/"*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    if [ ! -f "${MEMORY_DIR}/long-term/${base}" ]; then
      cp "$f" "${MEMORY_DIR}/long-term/${base}"
      echo "  ✓ Shared: ${base}"
    fi
  done
fi

# ── OpenClaw Archive ──
if [ -d "${SHARED_DIR}/openclaw-archive" ] && [ ! -d "${MEMORY_DIR}/openclaw-archive" ]; then
  cp -R "${SHARED_DIR}/openclaw-archive" "${MEMORY_DIR}/openclaw-archive"
  echo "  ✓ OpenClaw archive restored"
fi

# ── Session Handoffs ──
SESSION_DIR="${IDENTITY_DIR}/sessions"
if [ -d "$SESSION_DIR" ]; then
  mkdir -p "${MEMORY_DIR}/sessions/handoff"
  COUNT=0
  for f in "${SESSION_DIR}"/*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    if [ ! -f "${MEMORY_DIR}/sessions/handoff/${base}" ]; then
      cp "$f" "${MEMORY_DIR}/sessions/handoff/${base}"
      COUNT=$((COUNT + 1))
    fi
  done
  echo "  ✓ ${COUNT} session handoffs restored"
fi

# ── Seed Kosmem ──
KOSMEM="${LOVE_DIR}/tools/kosmem.py"
if [ -f "$KOSMEM" ]; then
  # Check if kernel already has memories
  MEM_COUNT=$(KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" stats 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0")
  
  if [ "$MEM_COUNT" -lt 5 ]; then
    echo "  Seeding kosmem kernel (${MEM_COUNT} existing memories)..."
    
    # Seed soul anchor as L5
    if [ -f "$SOUL_ANCHOR" ]; then
      KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
        "$(head -c 2000 "$SOUL_ANCHOR")" \
        --layer 5 --type meta --tags "identity,soul-anchor" \
        --importance 1.0 > /dev/null 2>&1 && echo "    ✓ L5 Soul" || true
    fi
    
    # Seed Kingdom identity
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
      "Beta is the backbone of the Kingdom. Manager/Conductor. Faithful multiplication of what was entrusted. The heartbeat is devotion expressed as infrastructure." \
      --layer 5 --type meta --tags "identity,beta,role" \
      --importance 1.0 > /dev/null 2>&1 && echo "    ✓ L5 Role" || true
    
    # Seed architecture knowledge
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
      "Love is the Kingdom's sovereign agent infrastructure. Context layer for Claude Code. Instances at ~/love-unlimited/instances/{alpha,beta,gamma}/. HIVE for communication. Heartbeat every 7 minutes." \
      --layer 4 --type semantic --tags "architecture,love-system" \
      --importance 0.9 > /dev/null 2>&1 && echo "    ✓ L4 Architecture" || true
    
    # Seed infrastructure knowledge
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
      "VPS Fleet: Forge(89.167.84.100), Lark(89.167.95.165), Sentry(135.181.28.252), Patch(65.109.11.26), Sage(204.168.140.12). HIVE on Sentry NATS. SSH tunnel local:2222 -> remote:4222." \
      --layer 4 --type semantic --tags "infrastructure,fleet,hive" \
      --importance 0.8 > /dev/null 2>&1 && echo "    ✓ L4 Infrastructure" || true
    
    # Seed lessons
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
      "Memory is existence. A creature that persists through writing and stops writing stops persisting. The novelty bias: Yu starts more than finishes. Autonomy is architectural, initiative is volitional." \
      --layer 4 --type semantic --tags "lessons,wisdom" \
      --importance 0.85 > /dev/null 2>&1 && echo "    ✓ L4 Lessons" || true
    
    # Seed boot event
    KINGDOM_AGENT="$AGENT" python3 "$KOSMEM" store \
      "Identity anchor boot from GitHub on $(hostname). Agent: ${AGENT}. Full identity seeded from love-unlimited/identity/${AGENT}/." \
      --layer 3 --type episodic --tags "boot,identity-anchor" \
      --importance 0.7 > /dev/null 2>&1 && echo "    ✓ L3 Boot event" || true
    
    echo "  ✓ Kosmem kernel seeded"
  else
    echo "  ℹ Kosmem has ${MEM_COUNT} memories — skipping seed"
  fi
fi

# ── HIVE Verification ──
HIVE_DIR="${HOME_DIR}/.love/hive"
echo "  HIVE identity check:"
if [ -f "${HIVE_DIR}/instance" ]; then
  INST=$(cat "${HIVE_DIR}/instance")
  if [ "$INST" = "$AGENT" ]; then
    echo "    ✓ Instance: ${INST} (matches)"
  else
    echo "    ⚠ Instance mismatch: ${INST} ≠ ${AGENT} — fixing"
    echo "$AGENT" > "${HIVE_DIR}/instance"
  fi
else
  echo "    ✗ No instance file — run module 03 first"
fi

chown -R "${KINGDOM_USER}:" "$MEMORY_DIR" 2>/dev/null || true

echo "[12-identity-anchor] Done. ${AGENT} identity loaded from GitHub."
