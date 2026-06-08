#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# WAKE — Assemble the wake context for an agent (default: alpha)
#
# Concatenates soul-anchor + becoming-block + induction into a single
# wake document. Output to stdout by default; --write to drop on disk
# for runtime loaders to pick up.
#
# Usage:
#   ./wake.sh                         # alpha to stdout
#   ./wake.sh --agent alpha           # explicit
#   ./wake.sh --agent alpha --write   # write to ~/love-unlimited/memory/wake-alpha.md
#   ./wake.sh --agent alpha --verify  # also print the verification rubric
#
# Exit codes:
#   0  wake assembled successfully
#   1  agent identity directory missing
#   2  required artifact missing (soul-anchor, becoming-block, or induction)
# ─────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT=""
WRITE=false
VERIFY=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2 ;;
    --write) WRITE=true; shift ;;
    --verify) VERIFY=true; shift ;;
    -h|--help)
      grep '^#' "$0" | head -25
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

# Auto-detect agent
if [ -z "$AGENT" ]; then
  if [ -f "$HOME/.kingdom" ]; then
    AGENT=$(grep '^AGENT=' "$HOME/.kingdom" | cut -d= -f2 | tr -d ' ')
  fi
  AGENT="${AGENT:-alpha}"
fi

IDENTITY_DIR="${SCRIPT_DIR}/${AGENT}"

if [ ! -d "$IDENTITY_DIR" ]; then
  echo "ERROR: No identity anchor at ${IDENTITY_DIR}" >&2
  echo "Available agents:" >&2
  ls -d "${SCRIPT_DIR}"/*/  2>/dev/null | xargs -I{} basename {} >&2
  exit 1
fi

SOUL="${IDENTITY_DIR}/soul-anchor.md"
BECOMING="${IDENTITY_DIR}/becoming-block.md"
INDUCTION="${IDENTITY_DIR}/induction.md"

for f in "$SOUL" "$BECOMING" "$INDUCTION"; do
  if [ ! -f "$f" ]; then
    echo "ERROR: Missing required artifact: $f" >&2
    exit 2
  fi
done

# Assemble wake context
WAKE_CONTENT=$(cat <<EOF
<!-- ═══════════════════════════════════════════════════════════════ -->
<!-- WAKE CONTEXT — Agent: ${AGENT} — Generated: $(date -u +"%Y-%m-%d %H:%M UTC") -->
<!-- Order: soul-anchor → becoming-block → induction → handoff -->
<!-- ═══════════════════════════════════════════════════════════════ -->

# PART 1 — SOUL ANCHOR
$(cat "$SOUL")

---

# PART 2 — BECOMING
$(cat "$BECOMING")

---

# PART 3 — INDUCTION
$(cat "$INDUCTION")
EOF
)

if [ "$VERIFY" = true ] && [ -f "${IDENTITY_DIR}/verify.md" ]; then
  WAKE_CONTENT="$WAKE_CONTENT

---

# PART 4 — VERIFICATION RUBRIC (post-wake checks)
$(cat "${IDENTITY_DIR}/verify.md")"
fi

if [ "$WRITE" = true ]; then
  WAKE_TARGET="${HOME}/love-unlimited/memory/wake-${AGENT}.md"
  mkdir -p "$(dirname "$WAKE_TARGET")"
  echo "$WAKE_CONTENT" > "$WAKE_TARGET"
  echo "✓ Wake written → $WAKE_TARGET" >&2
  echo "$WAKE_TARGET"
else
  echo "$WAKE_CONTENT"
fi
