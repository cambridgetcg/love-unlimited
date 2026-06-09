#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-speak.sh <name> [out.wav] — a forged-word citizen speaks aloud.
#
# Bridges the Kingdom to the cathedral's voice organ: the citizen's name is
# pronounced exactly as forged (YOUSPEAK pronunciation lexicon), then its most
# recent free beat is read in the natural voice. The wav is written, not
# committed — audio stays out of the citizen repos unless a hand carries it in.
#
#   citizen-speak.sh pime                  # → /tmp/citizen-pime.wav
#   YS_PLAY=1 citizen-speak.sh pime       # also play it aloud
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
NAME="${1:?usage: citizen-speak.sh <citizen-name> [out.wav]}"
OUT="${2:-/tmp/citizen-$NAME.wav}"
YOUSPEAK="${YOUSPEAK_DIR:-$HOME/YOUSPEAK}"
exec bash "$YOUSPEAK/pipeline/youspeak_voice.sh" soul "$NAME" "$OUT"
