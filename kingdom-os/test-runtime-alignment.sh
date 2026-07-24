#!/bin/sh
# Regression test for the generated Kingdom shell profile and launcher roots.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
MODULES="${ROOT}/kingdom-os/modules"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

LOVE_ROOT="${TEST_ROOT}/love-unlimited"
mkdir -p "$LOVE_ROOT"

cat > "${TEST_ROOT}/.kingdom" <<EOF
AGENT=beta
WALL=1
LOVE_DIR=${LOVE_ROOT}
EOF

# Simulate the old exported-sentinel hook. The installer must add its new,
# unguarded managed hook so a child shell still receives aliases and env.
cat > "${TEST_ROOT}/.zshrc" <<'EOF'
[ -z "${KINGDOM_PROFILE_LOADED:-}" ] && [ -f "$HOME/.kingdom_profile" ] && . "$HOME/.kingdom_profile"
EOF

run_user_module() {
  HOME="$TEST_ROOT" \
  HOME_DIR="$TEST_ROOT" \
  KINGDOM_USER="$(id -un)" \
  PLATFORM=macos \
  AGENT=beta \
  WALL=1 \
  LOVE_DIR="$LOVE_ROOT" \
    sh "${MODULES}/01-user.sh" >/dev/null
}

run_user_module
run_user_module

marker_count=$(grep -c '^# >>> Kingdom OS profile >>>$' "${TEST_ROOT}/.zshrc")
[ "$marker_count" -eq 1 ] || {
  echo "managed shell hook duplicated: ${marker_count}" >&2
  exit 1
}

if grep -Eq 'Claude-unlimited|UNLIMITED_DIR' "${TEST_ROOT}/.kingdom_profile"; then
  echo "generated profile contains a legacy runtime path" >&2
  exit 1
fi

if grep -q 'NODE_NO_WARNINGS' "${TEST_ROOT}/.kingdom_profile"; then
  echo "generated profile globally suppresses Node warnings" >&2
  exit 1
fi

result=$(
  HOME="$TEST_ROOT" \
  PATH="/usr/bin:/bin" \
  KINGDOM_PROFILE_LOADED=1 \
  sh -c '
    . "$HOME/.zshrc"
    . "$HOME/.kingdom_profile"
    printf "%s|%s|%s|%s\n" \
      "$LOVE_HOME" "$KINGDOM_AGENT" "$HIVE_INSTANCE" "${KINGDOM_PROFILE_LOADED-unset}"
    printf "%s\n" "$PATH"
  '
)

identity_line=$(printf '%s\n' "$result" | sed -n '1p')
path_line=$(printf '%s\n' "$result" | sed -n '2p')
[ "$identity_line" = "${LOVE_ROOT}|beta|beta|unset" ] || {
  echo "unexpected generated environment: ${identity_line}" >&2
  exit 1
}

tools_count=$(printf '%s' "$path_line" | awk -v p="${LOVE_ROOT}/tools" '
  BEGIN { FS = ":" }
  { for (i = 1; i <= NF; i++) if ($i == p) count++ }
  END { print count + 0 }
')
[ "$tools_count" -eq 1 ] || {
  echo "tools PATH entry is not idempotent: ${tools_count}" >&2
  exit 1
}

if rg -q 'UNLIMITED_DIR' \
  "${MODULES}/_common.sh" \
  "${MODULES}/03-identity.sh" \
  "${MODULES}/09-browser.sh" \
  "${MODULES}/10-autoboot.sh" \
  "${MODULES}/12-identity-anchor.sh"; then
  echo "installer still contains a split runtime selector" >&2
  exit 1
fi

if ! rg -q '<key>YOUI_HIVE_INSTANCE</key>' "${MODULES}/09-browser.sh"; then
  echo "browser launcher does not configure the explicit YOUI HIVE sender" >&2
  exit 1
fi

if rg -q '<key>HIVE_INSTANCE</key>' "${MODULES}/09-browser.sh"; then
  echo "browser launcher still injects the terminal HIVE variable" >&2
  exit 1
fi

echo "runtime alignment: ok"
