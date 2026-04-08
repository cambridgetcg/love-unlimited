#!/bin/sh
# ── Module 06: Memory System ────────────────────────────────────────
# Sets up the Kingdom memory system.
#
# Memory is the FOUNDATION of Kingdom OS. Not a tool — the substrate.
# Everything reads from memory. Everything writes to memory.
#
# Architecture:
#   kosmem (SQLite + FTS5) — the kernel
#   └── L1 Working   (volatile, per-instance)
#   └── L2 Session   (per-session, handoffs)
#   └── L3 Episodic  (per-day, daily notes)
#   └── L4 Semantic  (long-term, curated)
#   └── L5 Soul      (identity, immutable)
#
# Backward compatible: still writes daily notes as markdown,
# long-term memory as MEMORY.md, but the source of truth is SQLite.

set -e
. "$(dirname "$0")/_common.sh"

echo "[06-memory] Setting up memory system..."

# ── Directory structure ──────────────────────────────────────────────
mkdir -p "${MEMORY_DIR}/daily"
mkdir -p "${MEMORY_DIR}/long-term"
mkdir -p "${MEMORY_DIR}/sessions/handoff"
mkdir -p "${MEMORY_DIR}/sessions/locks"
mkdir -p "${MEMORY_DIR}/sessions/consultation"
mkdir -p "${MEMORY_DIR}/working"
mkdir -p "${MEMORY_DIR}/.kos"

# ── Initialize kosmem database ──────────────────────────────────────
if [ -f "${LOVE_DIR}/tools/kosmem.py" ]; then
    echo "  Initializing kosmem database..."
    python3 "${LOVE_DIR}/tools/kosmem.py" stats > /dev/null 2>&1
    echo "  ✓ kosmem database initialized at ${MEMORY_DIR}/.kos/memory.db"

    # Migrate legacy data if index.json exists
    if [ -f "${MEMORY_DIR}/index.json" ]; then
        echo "  Migrating legacy memory index..."
        python3 "${LOVE_DIR}/tools/kosmem.py" migrate
    fi
else
    echo "  ⚠ kosmem.py not found — creating legacy structure only"
fi

# ── Dev state (if not exists) ────────────────────────────────────────
DEV_STATE="${MEMORY_DIR}/dev-state.json"
if [ ! -f "$DEV_STATE" ]; then
  cat > "$DEV_STATE" << 'EOF'
{
  "activeProject": "love-kingdom",
  "activeRepo": "~/love-unlimited",
  "activePhase": "root",
  "kingdomPhase": "Phase 1 — Root (Now to Q2 2026)",
  "tasks": []
}
EOF
  echo "  Created dev-state.json"
fi

# ── Kingdom metrics (if not exists) ──────────────────────────────────
METRICS="${MEMORY_DIR}/kingdom-metrics.json"
if [ ! -f "$METRICS" ]; then
  cat > "$METRICS" << EOF
{
  "updated": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "chain": { "network": "lgm-devnet-1", "block_height": 0, "status": "unknown" },
  "fleet": {},
  "oracle": { "predictions_scored": 0 }
}
EOF
  echo "  Created kingdom-metrics.json"
fi

# ── Today's daily note ───────────────────────────────────────────────
TODAY=$(date -u +%Y-%m-%d)
DAILY="${MEMORY_DIR}/daily/${TODAY}.md"
if [ ! -f "$DAILY" ]; then
  cat > "$DAILY" << EOF
# Daily Notes — ${TODAY}

---

EOF
  echo "  Created daily note: ${TODAY}.md"
fi

# ── Long-term memory seed ────────────────────────────────────────────
LTM="${MEMORY_DIR}/long-term/MEMORY.md"
if [ ! -f "$LTM" ]; then
  cat > "$LTM" << EOF
# Long-Term Memory

Curated wisdom and knowledge that persists across all sessions.
Add entries here that should survive context window boundaries.

---

EOF
  echo "  Created long-term MEMORY.md"
fi

# ── Consolidation daemon (macOS) ─────────────────────────────────────
if [ "$PLATFORM" = "macos" ] && [ -f "${LOVE_DIR}/tools/love.kosmem.plist" ]; then
    PLIST_DIR="${HOME_DIR}/Library/LaunchAgents"
    mkdir -p "$PLIST_DIR"
    cp "${LOVE_DIR}/tools/love.kosmem.plist" "$PLIST_DIR/"
    launchctl load "$PLIST_DIR/love.kosmem.plist" 2>/dev/null || true
    echo "  ✓ kosmem consolidation daemon installed"
fi

# ── Store initial memory ─────────────────────────────────────────────
if [ -f "${LOVE_DIR}/tools/kosmem.py" ]; then
    python3 "${LOVE_DIR}/tools/kosmem.py" store \
        "Kingdom OS installed on $(hostname). Agent: ${AGENT}. Memory system initialized." \
        --type semantic --tags kingdom-os,install --importance 0.7 > /dev/null 2>&1
    echo "  ✓ Initial memory stored"
fi

chown -R "${KINGDOM_USER}:" "$MEMORY_DIR" 2>/dev/null || true

echo "[06-memory] Done. Memory kernel at: ${MEMORY_DIR}/.kos/memory.db"
