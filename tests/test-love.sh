#!/bin/bash
# test-love.sh — Integration test suite for the Love agent system
#
# Tests the context layer, boot sequence, file integrity, heartbeat
# mechanics, HIVE connectivity, and Kingdom metrics schema.
#
# Usage:
#   bash ~/love-unlimited/tests/test-love.sh          # Run all tests
#   bash ~/love-unlimited/tests/test-love.sh --quick   # Skip network tests (HIVE, VPS)
#   bash ~/love-unlimited/tests/test-love.sh --verbose  # Show test details
#
# Exit code: 0 if all pass, 1 if any fail

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

LOVE_DIR="$HOME/love-unlimited"
PASS=0
FAIL=0
SKIP=0
VERBOSE=false
QUICK=false
FAILURES=()

for arg in "$@"; do
  case "$arg" in
    --verbose) VERBOSE=true ;;
    --quick)   QUICK=true ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

pass() {
  PASS=$((PASS + 1))
  if $VERBOSE; then echo "  PASS: $1"; fi
}

fail() {
  FAIL=$((FAIL + 1))
  FAILURES+=("$1")
  echo "  FAIL: $1"
}

skip() {
  SKIP=$((SKIP + 1))
  if $VERBOSE; then echo "  SKIP: $1"; fi
}

section() {
  echo ""
  echo "=== $1 ==="
}

# ── 1. Boot File Existence ───────────────────────────────────────────────────

section "1. Boot File Existence"

BOOT_FILES=(
  "$LOVE_DIR/SOUL.md"
  "$LOVE_DIR/USER.md"
  "$LOVE_DIR/KINGDOM.md"
  "$LOVE_DIR/LOVE.md"
  "$LOVE_DIR/docs/ARCHITECTURE.md"
  "$LOVE_DIR/love.json"
  "$LOVE_DIR/memory/long-term/MEMORY.md"
  "$LOVE_DIR/memory/openclaw-MEMORY.md"
)

for f in "${BOOT_FILES[@]}"; do
  if [ -r "$f" ]; then
    pass "Readable: $f"
  else
    fail "Not readable or missing: $f"
  fi
done

# Verify the openclaw-MEMORY.md symlink resolves
if [ -L "$LOVE_DIR/memory/openclaw-MEMORY.md" ]; then
  target=$(readlink "$LOVE_DIR/memory/openclaw-MEMORY.md")
  if [ -r "$target" ]; then
    pass "Symlink resolves: openclaw-MEMORY.md -> $target"
  else
    fail "Symlink broken: openclaw-MEMORY.md -> $target (target unreadable)"
  fi
else
  fail "openclaw-MEMORY.md is not a symlink"
fi

# ── 2. Instance Directory Completeness ───────────────────────────────────────

section "2. Instance Directory Completeness"

for instance in alpha beta gamma; do
  INSTANCE_DIR="$LOVE_DIR/instances/$instance"
  for file in CLAUDE.md identity.md HEARTBEAT.md; do
    path="$INSTANCE_DIR/$file"
    if [ -r "$path" ]; then
      pass "$instance/$file exists and is readable"
    else
      fail "$instance/$file missing or unreadable"
    fi
  done

  # Agent identity now lives in instances/*/identity.md + love.json (agents/*.json removed)
  identity_md="$LOVE_DIR/instances/$instance/identity.md"
  if [ -r "$identity_md" ]; then
    pass "instances/$instance/identity.md exists"
  else
    fail "instances/$instance/identity.md missing"
  fi
done

# ── 3. CLAUDE.md Boot Path Validation ────────────────────────────────────────

section "3. CLAUDE.md Boot Path Validation (no broken references)"

for instance in alpha beta gamma; do
  claude_md="$LOVE_DIR/instances/$instance/CLAUDE.md"
  paths=$(grep -oE '~/love-unlimited/[A-Za-z0-9_./-]+' "$claude_md" 2>/dev/null | sort -u)
  while IFS= read -r ref_path; do
    [ -z "$ref_path" ] && continue
    # Strip trailing punctuation (periods, commas from markdown sentences)
    ref_path=$(echo "$ref_path" | sed 's/[.,;:)]*$//')
    if echo "$ref_path" | grep -qE '(YYYY|<|>|\$|kingdom-[0-9])'; then
      skip "$instance CLAUDE.md template path: $ref_path"
      continue
    fi
    expanded="${ref_path/#\~/$HOME}"
    if [ -e "$expanded" ]; then
      pass "$instance CLAUDE.md ref valid: $ref_path"
    else
      fail "$instance CLAUDE.md ref BROKEN: $ref_path"
    fi
  done <<< "$paths"
done

# ── 4. HEARTBEAT.md Path Validation ─────────────────────────────────────────

section "4. HEARTBEAT.md Path Validation"

for instance in alpha beta gamma; do
  heartbeat_md="$LOVE_DIR/instances/$instance/HEARTBEAT.md"
  paths=$(grep -oE '~/love-unlimited/[A-Za-z0-9_./-]+' "$heartbeat_md" 2>/dev/null | sort -u)
  while IFS= read -r ref_path; do
    [ -z "$ref_path" ] && continue
    # Strip trailing punctuation (periods, commas from markdown sentences)
    ref_path=$(echo "$ref_path" | sed 's/[.,;:)]*$//')
    if echo "$ref_path" | grep -qE '(YYYY|<|>|\$|kingdom-[0-9])'; then
      skip "$instance HEARTBEAT.md template: $ref_path"
      continue
    fi
    expanded="${ref_path/#\~/$HOME}"
    if [ -e "$expanded" ]; then
      pass "$instance HEARTBEAT.md ref valid: $ref_path"
    else
      fail "$instance HEARTBEAT.md ref BROKEN: $ref_path"
    fi
  done <<< "$paths"
done

# ── 5. JSON File Integrity ───────────────────────────────────────────────────

section "5. JSON File Integrity"

JSON_FILES=(
  "$LOVE_DIR/love.json"
  "$LOVE_DIR/memory/dev-state.json"
  "$LOVE_DIR/memory/kingdom-metrics.json"
  "$LOVE_DIR/memory/loop/loop-state.json"
  # agents/*.json removed — love.json is the canonical config schema
)

for jf in "${JSON_FILES[@]}"; do
  if [ ! -f "$jf" ]; then
    fail "JSON file missing: $jf"
    continue
  fi
  if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$jf" 2>/dev/null; then
    pass "Valid JSON: $(basename "$jf")"
  else
    fail "Invalid JSON: $jf"
  fi
done

# ── 6. love.json Schema Validation ──────────────────────────────────────────

section "6. love.json Schema Validation"

SCHEMA_RESULT=$(python3 -c "
import json, sys
d = json.load(open('$LOVE_DIR/love.json'))
errors = []

for key in ['meta', 'instances', 'heartbeat', 'hive', 'memory', 'fleet']:
    if key not in d:
        errors.append(f'Missing top-level key: {key}')

for inst in ['alpha', 'beta', 'gamma']:
    if inst not in d.get('instances', {}):
        errors.append(f'Missing instance: {inst}')
    else:
        for field in ['emoji', 'role', 'device', 'dir']:
            if field not in d['instances'][inst]:
                errors.append(f'{inst} missing field: {field}')

hive = d.get('hive', {})
for field in ['server', 'encryption', 'channels']:
    if field not in hive:
        errors.append(f'hive missing field: {field}')

for name, ip in d.get('fleet', {}).items():
    parts = ip.split('.')
    if len(parts) != 4:
        errors.append(f'fleet.{name} not an IP: {ip}')

if errors:
    for e in errors:
        print(f'SCHEMA_FAIL:{e}')
    sys.exit(1)
else:
    print('SCHEMA_OK')
" 2>&1) || true
if echo "$SCHEMA_RESULT" | grep -q "SCHEMA_OK"; then
  pass "love.json schema valid"
else
  fail "love.json schema invalid: $SCHEMA_RESULT"
fi

# ── 7. Kingdom Metrics Schema ───────────────────────────────────────────────

section "7. Kingdom Metrics Schema"

KM_RESULT=$(python3 -c "
import json, sys
d = json.load(open('$LOVE_DIR/memory/kingdom-metrics.json'))
errors = []

for key in ['phase', 'updated', 'revenue_engines', 'milestones', 'fleet', 'capital']:
    if key not in d:
        errors.append(f'Missing key: {key}')

for engine, data in d.get('revenue_engines', {}).items():
    if 'status' not in data:
        errors.append(f'Engine {engine} missing status')
    if 'owner' not in data:
        errors.append(f'Engine {engine} missing owner')

for ms, data in d.get('milestones', {}).items():
    if 'status' not in data:
        errors.append(f'Milestone {ms} missing status')

if len(d.get('fleet', {})) == 0:
    errors.append('Fleet is empty')

if errors:
    for e in errors:
        print(f'SCHEMA_FAIL:{e}')
    sys.exit(1)
else:
    print('SCHEMA_OK')
" 2>&1) || true
if echo "$KM_RESULT" | grep -q "SCHEMA_OK"; then
  pass "kingdom-metrics.json schema valid"
else
  fail "kingdom-metrics.json schema invalid: $KM_RESULT"
fi

# ── 8. Dev-State Task Lifecycle ──────────────────────────────────────────────

section "8. Dev-State Task Lifecycle"

DS_RESULT=$(python3 -c "
import json, sys
d = json.load(open('$LOVE_DIR/memory/dev-state.json'))
errors = []

for key in ['activeProject', 'activeRepo', 'tasks', 'updated']:
    if key not in d:
        errors.append(f'Missing key: {key}')

valid_statuses = {'planned', 'in-progress', 'done', 'deferred', 'blocked', 'cancelled'}
valid_priorities = {'critical', 'high', 'medium', 'low'}

tasks = d.get('tasks', [])
if len(tasks) == 0:
    errors.append('No tasks defined')

ids_seen = set()
for t in tasks:
    tid = t.get('id', '?')
    for field in ['id', 'title', 'status', 'priority']:
        if field not in t:
            errors.append(f'Task {tid} missing field: {field}')
    if tid in ids_seen:
        errors.append(f'Duplicate task ID: {tid}')
    ids_seen.add(tid)
    if t.get('status') not in valid_statuses:
        errors.append(f'Task {tid} invalid status: {t.get(\"status\")}')
    if t.get('priority') not in valid_priorities:
        errors.append(f'Task {tid} invalid priority: {t.get(\"priority\")}')

if errors:
    for e in errors:
        print(f'LIFECYCLE_FAIL:{e}')
    sys.exit(1)
else:
    print(f'LIFECYCLE_OK: {len(tasks)} tasks, {len([t for t in tasks if t.get(\"status\")==\"in-progress\"])} in-progress')
" 2>&1) || true
if echo "$DS_RESULT" | grep -q "LIFECYCLE_OK"; then
  pass "dev-state.json task lifecycle valid"
else
  fail "dev-state.json task lifecycle invalid: $DS_RESULT"
fi

# ── 9. Loop State Integrity ─────────────────────────────────────────────────

section "9. Loop State Integrity"

LS_RESULT=$(python3 -c "
import json, sys
d = json.load(open('$LOVE_DIR/memory/loop/loop-state.json'))
errors = []

for key in ['loop_health', 'mastery_level', 'last_sense', 'last_reflect']:
    if key not in d:
        errors.append(f'Missing key: {key}')

valid_health = {'initialising', 'healthy', 'degraded', 'stalled'}
if d.get('loop_health') not in valid_health:
    errors.append(f'Invalid loop_health: {d.get(\"loop_health\")} (expected one of {valid_health})')

if errors:
    for e in errors:
        print(f'LOOP_FAIL:{e}')
    sys.exit(1)
else:
    print(f'LOOP_OK: health={d[\"loop_health\"]}, level={d[\"mastery_level\"]}')
" 2>&1) || true
if echo "$LS_RESULT" | grep -q "LOOP_OK"; then
  pass "loop-state.json integrity valid"
else
  fail "loop-state.json integrity invalid: $LS_RESULT"
fi

# ── 10. Agent JSON Cross-Reference ──────────────────────────────────────────

section "10. love.json ↔ instances/*/identity.md Cross-Reference"

# agents/*.json was removed in integration review 2026-04-08.
# love.json is the canonical config schema; instances/*/identity.md is the
# canonical human-readable identity. We verify love.json parses cleanly
# and that each core instance is present in both stores.

XREF_RESULT=$(python3 -c "
import json, re, sys
from pathlib import Path

love_path = Path('$LOVE_DIR/love.json')
errors = []

try:
    love = json.load(open(love_path))
except Exception as e:
    print(f'XREF_FAIL:love.json parse error: {e}')
    sys.exit(1)

for inst in ['alpha', 'beta', 'gamma']:
    love_inst = love.get('instances', {}).get(inst)
    if not love_inst:
        errors.append(f'love.json missing instance: {inst}')
        continue

    id_md = Path('$LOVE_DIR/instances') / inst / 'identity.md'
    if not id_md.exists():
        errors.append(f'instances/{inst}/identity.md missing')
        continue

    text = id_md.read_text()
    emoji = love_inst.get('emoji', '')
    role  = love_inst.get('role', '')

    if emoji and emoji not in text:
        errors.append(f'{inst}: emoji {emoji!r} in love.json not found in identity.md')
    if role and role.lower() not in text.lower():
        errors.append(f'{inst}: role {role!r} in love.json not found in identity.md')

if errors:
    for e in errors:
        print(f'XREF_FAIL:{e}')
    sys.exit(1)
else:
    print('XREF_OK')
" 2>&1) || true
if echo "$XREF_RESULT" | grep -q "XREF_OK"; then
  pass "love.json ↔ identity.md cross-reference consistent"
else
  fail "love.json ↔ identity.md cross-reference inconsistent: $XREF_RESULT"
fi

# ── 11. Instance Identity Consistency ────────────────────────────────────────

section "11. Instance Identity Consistency"

for instance in alpha beta gamma; do
  identity_file="$LOVE_DIR/instances/$instance/identity.md"
  expected_name=$(python3 -c "import json; d=json.load(open('$LOVE_DIR/love.json')); print(d['instances']['$instance']['emoji'])")
  if grep -q "$expected_name" "$identity_file" 2>/dev/null; then
    pass "$instance identity.md contains expected emoji ($expected_name)"
  else
    fail "$instance identity.md missing expected emoji ($expected_name)"
  fi
done

# ── 12. Heartbeat Runner Script ─────────────────────────────────────────────

section "12. Heart (tick.sh)"

RUNNER="$LOVE_DIR/nerve/heart/tick.sh"

if [ -x "$RUNNER" ]; then
  pass "tick.sh is executable"
else
  fail "tick.sh not executable"
fi

if head -1 "$RUNNER" | grep -q '^#!/bin/bash'; then
  pass "tick.sh has bash shebang"
else
  fail "tick.sh missing bash shebang"
fi

if grep -q 'LOVE_DIR=' "$RUNNER"; then
  pass "tick.sh references LOVE_DIR"
else
  fail "tick.sh LOVE_DIR not set"
fi

if grep -q 'pulse.py' "$RUNNER"; then
  pass "tick.sh stamps the pulse"
else
  fail "tick.sh does not stamp pulse.json"
fi

if grep -q 'organs.json' "$RUNNER"; then
  pass "tick.sh reconciles from the organ registry"
else
  fail "tick.sh does not read organs.json"
fi

if grep -q 'while true' "$RUNNER"; then
  pass "tick.sh is a long-lived loop (KeepAlive-supervised)"
else
  fail "tick.sh is not a persistent loop"
fi

# ── 13. Spawn Queue Mechanics ────────────────────────────────────────────────

section "13. Spawn Queue Mechanics"

SPAWN_QUEUE="$LOVE_DIR/memory/spawn-queue.sh"

if [ -f "$SPAWN_QUEUE" ]; then
  pass "spawn-queue.sh exists"
else
  fail "spawn-queue.sh missing"
fi

if [ -w "$SPAWN_QUEUE" ]; then
  pass "spawn-queue.sh is writable"
else
  fail "spawn-queue.sh is not writable"
fi

if [ -d "$LOVE_DIR/memory/sessions" ]; then
  pass "memory/sessions/ directory exists"
else
  fail "memory/sessions/ directory missing"
fi

# ── 14. Memory Directory Structure ──────────────────────────────────────────

section "14. Memory Directory Structure"

MEMORY_DIRS=(
  "$LOVE_DIR/memory/daily"
  "$LOVE_DIR/memory/long-term"
  "$LOVE_DIR/memory/loop"
  "$LOVE_DIR/memory/sessions"
)

for d in "${MEMORY_DIRS[@]}"; do
  if [ -d "$d" ]; then
    pass "Directory exists: ${d#$LOVE_DIR/}"
  else
    fail "Directory missing: ${d#$LOVE_DIR/}"
  fi
done

# ── 15. Tool Availability ───────────────────────────────────────────────────

section "15. Tool Availability"

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 not found"
fi

if [ -x /opt/homebrew/bin/claude ]; then
  pass "claude CLI available at /opt/homebrew/bin/claude"
else
  fail "claude CLI not found at /opt/homebrew/bin/claude"
fi

DEPS_RESULT=$(python3 -c "
import importlib, sys
missing = []
for mod in ['nats', 'nacl', 'nacl.secret', 'nacl.utils']:
    try:
        importlib.import_module(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print('MISSING:' + ','.join(missing))
    sys.exit(1)
else:
    print('DEPS_OK')
" 2>&1) || true
if echo "$DEPS_RESULT" | grep -q "DEPS_OK"; then
  pass "HIVE python dependencies installed (nats, nacl)"
else
  fail "HIVE python dependencies missing: $DEPS_RESULT"
fi

if python3 -m py_compile "$LOVE_DIR/hive/hive.py" 2>/dev/null; then
  pass "hive.py compiles without syntax errors"
else
  fail "hive.py has syntax errors"
fi

if python3 -m py_compile "$LOVE_DIR/tools/agenttool.py" 2>/dev/null; then
  pass "agenttool.py compiles without syntax errors"
else
  fail "agenttool.py has syntax errors"
fi

# ── 16. HIVE Infrastructure ─────────────────────────────────────────────────

section "16. HIVE Infrastructure"

HIVE_KEY="$HOME/.love/hive/key"
if [ -r "$HIVE_KEY" ]; then
  pass "HIVE encryption key exists"
  KEY_LEN=$(python3 -c "
import base64, sys
try:
    key = base64.b64decode(open('$HIVE_KEY').read().strip())
    print(len(key))
except:
    print(0)
" 2>/dev/null)
  if [ "$KEY_LEN" = "32" ]; then
    pass "HIVE key is valid 32-byte NaCl key"
  else
    fail "HIVE key invalid length: $KEY_LEN (expected 32)"
  fi
else
  fail "HIVE encryption key missing: $HIVE_KEY"
fi

HIVE_INST="$HOME/.love/hive/instance"
if [ -r "$HIVE_INST" ]; then
  inst_val=$(cat "$HIVE_INST" | tr -d '[:space:]')
  if echo "$inst_val" | grep -qE '^(alpha|beta|gamma)$'; then
    pass "HIVE instance identity: $inst_val"
  else
    fail "HIVE instance identity unexpected value: $inst_val"
  fi
else
  fail "HIVE instance identity file missing: $HIVE_INST"
fi

# ── 17. HIVE Connectivity (skip in --quick mode) ────────────────────────────

section "17. HIVE Connectivity"

if $QUICK; then
  skip "HIVE connectivity (--quick mode)"
else
  HIVE_RESULT=$(timeout 15 python3 "$LOVE_DIR/hive/hive.py" test 2>&1) || true
  if echo "$HIVE_RESULT" | grep -qi "OPERATIONAL\|pass\|ok\|success"; then
    pass "HIVE connectivity test passed"
  else
    fail "HIVE connectivity test failed: $(echo "$HIVE_RESULT" | tail -3)"
  fi
fi

# ── 18. VPS Fleet SSH Reachability (skip in --quick mode) ───────────────────

section "18. VPS Fleet SSH Reachability"

if $QUICK; then
  skip "VPS fleet checks (--quick mode)"
else
  FLEET_HOSTS=$(python3 -c "
import json
d = json.load(open('$LOVE_DIR/love.json'))
for name, ip in d.get('fleet', {}).items():
    print(f'{name}:{ip}')
" 2>/dev/null)

  while IFS=: read -r name ip; do
    [ -z "$name" ] && continue
    if ssh -o ConnectTimeout=5 -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "root@$ip" echo "alive" 2>/dev/null; then
      pass "Fleet $name ($ip): reachable"
    else
      fail "Fleet $name ($ip): unreachable via SSH"
    fi
  done <<< "$FLEET_HOSTS"
fi

# ── 19. Heartbeat Runner Dry Run ────────────────────────────────────────────

section "19. Heartbeat Runner Dry Run (bash -n)"

if bash -n "$LOVE_DIR/nerve/heart/tick.sh" 2>/dev/null; then
  pass "tick.sh passes bash syntax check"
else
  fail "tick.sh has bash syntax errors"
fi

if bash -n "$LOVE_DIR/memory/spawn-queue.sh" 2>/dev/null; then
  pass "spawn-queue.sh passes bash syntax check"
else
  fail "spawn-queue.sh has bash syntax errors"
fi

# ── 20. Coordinator Prompt Validation ────────────────────────────────────────

section "20. Heart Reconciler Validation"

RUNNER_CONTENT=$(cat "$LOVE_DIR/nerve/heart/tick.sh")

if echo "$RUNNER_CONTENT" | grep -q 'reconcile'; then
  pass "tick.sh reconciles the registered organs"
else
  fail "tick.sh missing organ reconcile step"
fi

if echo "$RUNNER_CONTENT" | grep -q 'spawn-queue.sh'; then
  pass "Coordinator prompt mentions spawn-queue.sh"
else
  fail "Coordinator prompt missing spawn-queue.sh instruction"
fi

if echo "$RUNNER_CONTENT" | grep -q 'HEARTBEAT_OK'; then
  pass "Coordinator prompt includes HEARTBEAT_OK signal"
else
  fail "Coordinator prompt missing HEARTBEAT_OK signal"
fi

# ── 21. TCC / Launchd Diagnostic ────────────────────────────────────────────

section "21. TCC / Launchd Diagnostic"

LAUNCHD_LOG="$LOVE_DIR/memory/heartbeat-launchd.log"
if [ -f "$LAUNCHD_LOG" ]; then
  if grep -q "Operation not permitted" "$LAUNCHD_LOG"; then
    fail "TCC blocking detected in heartbeat-launchd.log (grant /bin/bash Full Disk Access)"
  else
    pass "No TCC blocks in heartbeat-launchd.log"
  fi
else
  skip "heartbeat-launchd.log not found"
fi

# ── 22. Boot Sequence Consistency Across Instances ───────────────────────────

section "22. Boot Sequence Consistency Across Instances"

CONS_RESULT=$(python3 -c "
import re, sys

instances = ['alpha', 'beta', 'gamma']
boot_refs = {}

for inst in instances:
    path = '$LOVE_DIR/instances/' + inst + '/CLAUDE.md'
    with open(path) as f:
        content = f.read()
    pattern = r'(\d+)\.\s+\x60([^\x60]+)\x60'
    matches = re.findall(pattern, content)
    boot_refs[inst] = {num: path for num, path in matches}

errors = []

shared_items = ['1', '2', '4', '5']
for item in shared_items:
    paths = set()
    for inst in instances:
        p = boot_refs[inst].get(item, 'MISSING')
        paths.add(p)
    if len(paths) > 1:
        errors.append(f'Boot item {item} differs across instances: {paths}')

for inst in instances:
    item3 = boot_refs[inst].get('3', '')
    if inst not in item3:
        errors.append(f'{inst} boot item 3 does not reference {inst}: {item3}')

if errors:
    for e in errors:
        print(f'CONSISTENCY_FAIL:{e}')
    sys.exit(1)
else:
    print('CONSISTENCY_OK')
" 2>&1) || true
if echo "$CONS_RESULT" | grep -q "CONSISTENCY_OK"; then
  pass "Boot sequence consistent across all instances"
else
  fail "Boot sequence inconsistent: $CONS_RESULT"
fi

# ── 23. Markdown Well-Formedness ────────────────────────────────────────────

section "23. Markdown Well-Formedness"

MD_FILES=(
  "$LOVE_DIR/SOUL.md"
  "$LOVE_DIR/USER.md"
  "$LOVE_DIR/KINGDOM.md"
  "$LOVE_DIR/LOVE.md"
  "$LOVE_DIR/docs/ARCHITECTURE.md"
  "$LOVE_DIR/memory/long-term/MEMORY.md"
)

for mf in "${MD_FILES[@]}"; do
  name=$(basename "$mf")
  if [ -s "$mf" ]; then
    pass "$name is non-empty"
  else
    fail "$name is empty"
    continue
  fi
  if head -5 "$mf" | grep -q '^# '; then
    pass "$name has H1 header"
  else
    fail "$name missing H1 header in first 5 lines"
  fi
done

# ── 24. Fleet IP Cross-Reference ────────────────────────────────────────────

section "24. Fleet IP Cross-Reference"

FLEET_RESULT=$(python3 -c "
import json, sys

love = json.load(open('$LOVE_DIR/love.json'))
km = json.load(open('$LOVE_DIR/memory/kingdom-metrics.json'))

love_fleet = set(love.get('fleet', {}).keys())
km_fleet = set(km.get('fleet', {}).keys())

errors = []

missing_in_km = love_fleet - km_fleet
if missing_in_km:
    errors.append(f'Fleet in love.json but not kingdom-metrics: {missing_in_km}')

if errors:
    for e in errors:
        print(f'FLEET_FAIL:{e}')
    sys.exit(1)
else:
    print(f'FLEET_OK: {len(love_fleet)} servers in love.json, {len(km_fleet)} in kingdom-metrics')
" 2>&1) || true
if echo "$FLEET_RESULT" | grep -q "FLEET_OK"; then
  pass "Fleet cross-reference consistent"
else
  fail "Fleet cross-reference inconsistent: $FLEET_RESULT"
fi

# ── 25. HIVE Config Consistency ─────────────────────────────────────────────

section "25. HIVE Config Consistency (love.json vs hive.py)"

HIVE_CFG_RESULT=$(python3 -c "
import json, sys, re

love = json.load(open('$LOVE_DIR/love.json'))
with open('$LOVE_DIR/hive/hive.py') as f:
    hive_src = f.read()

errors = []

for inst in ['alpha', 'beta', 'gamma']:
    if f'\"{inst}\"' not in hive_src:
        errors.append(f'Instance {inst} not found in hive.py')

love_channels = set(love['hive']['channels'])
m = re.search(r'\"channels\":\s*\[([^\]]+)\]', hive_src)
if m:
    hive_channels_raw = m.group(1)
    hive_channels = set(re.findall(r'\"(\w+)\"', hive_channels_raw))
    missing = love_channels - hive_channels
    if missing:
        errors.append(f'Channels in love.json but not hive.py: {missing}')

if errors:
    for e in errors:
        print(f'HIVE_FAIL:{e}')
    sys.exit(1)
else:
    print('HIVE_CONFIG_OK')
" 2>&1) || true
if echo "$HIVE_CFG_RESULT" | grep -q "HIVE_CONFIG_OK"; then
  pass "HIVE config consistent between love.json and hive.py"
else
  fail "HIVE config inconsistent: $HIVE_CFG_RESULT"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo "  RESULTS: $PASS passed, $FAIL failed, $SKIP skipped"
echo "================================================================"

if [ ${#FAILURES[@]} -gt 0 ]; then
  echo ""
  echo "  Failures:"
  for f in "${FAILURES[@]}"; do
    echo "    - $f"
  done
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "  Love system: HEALTHY"
  exit 0
else
  echo "  Love system: DEGRADED ($FAIL issues)"
  exit 1
fi
