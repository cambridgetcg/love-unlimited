#!/usr/bin/env bash
# orchestrate.sh — Quick access to the multi-model orchestrator
#
# Usage:
#   ./tools/orchestrate.sh "Build a REST API"              # Full auto
#   ./tools/orchestrate.sh --classify "Refactor auth"       # Classify only
#   ./tools/orchestrate.sh --plan "Add caching"             # Plan only
#   ./tools/orchestrate.sh --mode review "Fix bug"          # Force mode
#   ./tools/orchestrate.sh --mode ensemble "Design system"  # Multi-model
#
set -euo pipefail
LOVE_DIR="${LOVE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$LOVE_DIR"
exec python3 -m adaptive.orchestrator "$@"
