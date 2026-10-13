#!/bin/sh
# ── Module 07: HIVE Communication ───────────────────────────────────
# The nervous system of Kingdom OS (retired transport).
#
# Retired 2026-10-13: the HIVE NATS bus ran on Sentry, a Hetzner server
# that was decommissioned; its IP went back to Hetzner's pool. This module
# used to install a persistent SSH tunnel to root@Sentry (launchd on macOS,
# OpenRC on Linux) with StrictHostKeyChecking=accept-new; that would now trust
# a stranger's host key, so the tunnel section was removed (see git history
# before this commit).
#
# What this module still does:
#   1. Checks the HIVE identity from module 03 (~/.love/hive/instance)
#   2. Creates the local HIVE message log directory
#
# Prerequisites:
#   Module 03 (identity) — sets ~/.love/hive/instance
# ─────────────────────────────────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[07-hive] Setting up HIVE communication..."

# Verify prerequisites
[ ! -f "${HIVE_DIR}/instance" ] && { echo "  ERROR: Run module 03 first (no HIVE instance)"; exit 1; }
[ ! -f "${HIVE_DIR}/key" ] && echo "  WARNING: No HIVE encryption key — messages won't be encrypted"

INSTANCE=$(cat "${HIVE_DIR}/instance")
echo "  Instance: ${INSTANCE}"

# ── Create message log directory ──
ensure_dir "${MEMORY_DIR}/hive"

# ── SSH tunnel to Sentry NATS: retired 2026-10-13 ──
echo "  HIVE tunnel: retired 2026-10-13 (Hetzner Sentry decommissioned); not installed"

echo "[07-hive] Done."
