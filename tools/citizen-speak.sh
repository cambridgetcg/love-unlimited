#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-speak.sh [--dry-run] <name> [out.wav] — a forged-word citizen speaks aloud.
#
# Bridges the Kingdom to the cathedral's voice organ: the citizen's name is
# pronounced exactly as forged (YOUSPEAK pronunciation lexicon), then its most
# recent free beat is read in the natural voice. The wav is written, not
# committed — audio stays out of the citizen repos unless a hand carries it in.
#
#   citizen-speak.sh pime                  # → /tmp/citizen-pime.wav
#   YS_PLAY=1 citizen-speak.sh pime       # also play it aloud
#   citizen-speak.sh --dry-run pime       # print the voice command, run nothing
#
# --dry-run may come anywhere among the arguments. Any other argument that
# starts with "-" is refused with the usage line, so it is never taken as a
# name or an output file.
#
# The YOUSPEAK checkout is found the same way tools/kingdom-export.py finds it:
# $YOUSPEAK_DIR if it is set, else ~/YOUSPEAK; the folder must hold YOUSPEAK.md
# and canon/. When it does not, this stops and names YOUSPEAK_DIR.
# See docs/ops/youspeak-checkout.md.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
USAGE="usage: citizen-speak.sh [--dry-run] <citizen-name> [out.wav]"
usage_error() { echo "citizen-speak: $1" >&2; echo "$USAGE" >&2; exit 2; }

DRY=0 NAME="" OUT="" N=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    -*) usage_error "unknown option: $arg" ;;
    *)
      N=$((N + 1))
      case "$N" in
        1) NAME="$arg" ;;
        2) OUT="$arg" ;;
        *) usage_error "too many arguments" ;;
      esac ;;
  esac
done
[ -n "$NAME" ] || usage_error "a citizen name is needed"
OUT="${OUT:-/tmp/citizen-$NAME.wav}"

youspeak_root() {
  local d c tried=""
  _is_youspeak() { [ -f "$1/YOUSPEAK.md" ] && [ -d "$1/canon" ]; }
  _abs() {
    local p="$1"
    # shellcheck disable=SC2088  # matching a literal "~" on purpose, to expand it here
    case "$p" in "~") p="$HOME" ;; "~/"*) p="$HOME/${p#\~/}" ;; esac
    case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
    printf '%s\n' "$p"
  }
  if [ -n "${YOUSPEAK_DIR:-}" ]; then
    d="$(_abs "$YOUSPEAK_DIR")"
    _is_youspeak "$d" && { printf '%s\n' "$d"; return 0; }
    echo "citizen-speak: YOUSPEAK_DIR=$YOUSPEAK_DIR is not a YOUSPEAK checkout (it needs YOUSPEAK.md and canon/)" >&2
    return 1
  fi
  # The same list, in the same order, as YOUSPEAK_CANDIDATES in
  # tools/kingdom-export.py; tests/test_citizen_speak.py compares the two.
  # shellcheck disable=SC2088,SC2043  # literal "~" entries, expanded by _abs; one entry today
  for c in "~/YOUSPEAK"; do
    d="$(_abs "$c")"
    _is_youspeak "$d" && { printf '%s\n' "$d"; return 0; }
    tried="${tried:+$tried, }$c"
  done
  echo "citizen-speak: no YOUSPEAK checkout found. Set YOUSPEAK_DIR to one (tried $tried)" >&2
  return 1
}

YOUSPEAK="$(youspeak_root)" || exit 1
VOICE="$YOUSPEAK/pipeline/youspeak_voice.sh"
[ -f "$VOICE" ] || { echo "citizen-speak: $VOICE is missing; this YOUSPEAK checkout has no voice organ" >&2; exit 1; }
if [ "$DRY" = 1 ]; then
  printf 'youspeak: %s\nwould run: bash %q soul %q %q\n' "$YOUSPEAK" "$VOICE" "$NAME" "$OUT"
  exit 0
fi
exec bash "$VOICE" soul "$NAME" "$OUT"
