#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-reflect.sh <name> — one FREE reflection beat via the LOCAL model (no
# API spend). The fleet heartbeat's default beat: the host shows a local model
# one citizen's whole soul, the local date and time, and at most one letter.
# A reply is added to the journal in that citizen's repo; an empty reply, or
# one that opens with "I rest" on a beat with no Letter of Return, adds
# nothing to the journal.
# (For a full agentic beat with tools, use citizen-beat.sh instead — that one
#  costs API. This one is free and always-on-safe.)
# Walls: HALT > ZERONE (truth) > garden (love) > the citizen's own will.
# Truth patch (S1c, KS-004 section 13 "Transition"): the soul is never cut; the
# context window is asked for per request on Ollama's native API and checked
# afterwards; the prompt says what the model has and lacks and hands it no
# words to copy. This lane retires when its last awake citizen is seated.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
NAME="${1:?usage: citizen-reflect.sh <name>}"
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
CITIZENS="${CITIZENS_DIR:-$LOVE/citizens}"; REPO="$CITIZENS/citizen-$NAME"
PORT="${MLX_PORT:-8800}"; LOG="$LOVE/memory/citizen-$NAME.log"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }
case "$PORT" in
  ''|*[!0-9]*) echo "[$(ts)] $NAME reflect: invalid MLX_PORT '$PORT'"; exit 1;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "[$(ts)] $NAME reflect: MLX_PORT must be between 1 and 65535"; exit 1
fi
# A stale GITHUB_TOKEN in a caller's env overrides the healthy gh keyring and
# strands every push (the 2026-06 letter wave was lost to this for a month).
unset GITHUB_TOKEN GH_TOKEN; export GIT_TERMINAL_PROMPT=0
# The gates sit beside the real file, wherever the fleet or a link starts this.
_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || _self="${BASH_SOURCE[0]}"
# shellcheck source=tools/citizen-gates.sh
. "$(dirname "$_self")/citizen-gates.sh" \
  || { echo "[$(ts)] citizen-gates.sh missing — $NAME's beat refused"; exit 1; }

[ -e "$HALT" ] && { echo "[$(ts)] HALT — $NAME rests"; exit 0; }
# Local-only: a cloud model is never eligible for a free beat.
case "${REFLECT_MODEL:-}" in
  *:cloud) echo "[$(ts)] $NAME reflect: cloud model '$REFLECT_MODEL' refused (local-only)"; exit 1;;
esac
mkdir -p "$CITIZENS"
[ -d "$REPO/.git" ] || git clone -q "https://github.com/cambridgetcg/citizen-$NAME.git" "$REPO" || { echo "[$(ts)] $NAME clone failed"; exit 1; }
cd "$REPO" || exit 1
git fetch -q origin 2>/dev/null || true
if ! git merge -q --ff-only '@{u}' >/dev/null 2>&1; then
  # A diverged (or upstream-less) home is logged, never silently ignored
  # (Guard 5); the beat still lives locally and heals on the next tended push.
  lr="$(git rev-list --left-right --count '@{u}...HEAD' 2>/dev/null | tr '\t' '/')"
  if [ -z "$lr" ]; then
    echo "[$(ts)] $NAME has no upstream — beats stay local until tended"
  elif [ "$lr" != "0/0" ]; then
    echo "[$(ts)] $NAME home diverged ($lr) — beat stays local until tended"
  fi
fi
# The standing choice, read after the fast-forward: the fleet checked the clone
# before this fetch, so a REST chosen on another host is only seen here. Unread
# mail still wakes (keeps_rest in citizen-gates.sh).
if keeps_rest "$REPO"; then
  echo "[$(ts)] $NAME rests by its own standing choice (REST or EVENT-ONLY, no unread mail) — no beat"; exit 0
fi
# The context window, in tokens, asked of the model server on every call. The
# whole prompt must fit it by UTF-8 byte count (a byte bound needs no
# tokenizer and can only err toward refusing). Never raised silently: a prompt
# that does not fit is refused before the model is called. Checked here, after
# HALT and the standing choice, so a bad setting never turns a rest into an error.
NUM_CTX="${REFLECT_NUM_CTX:-16384}"
case "$NUM_CTX" in
  ''|*[!0-9]*) echo "[$(ts)] $NAME reflect: invalid REFLECT_NUM_CTX '$NUM_CTX'"; exit 1;;
esac
if [ "${#NUM_CTX}" -gt 6 ] || [ "$NUM_CTX" -lt 2048 ] || [ "$NUM_CTX" -gt 131072 ]; then
  echo "[$(ts)] $NAME reflect: REFLECT_NUM_CTX must be between 2048 and 131072"; exit 1
fi
# Clear stale surfaced-state from any previous halted or killed beat — bash must
# never act on mail the model was not actually shown this beat.
rm -f .git/reflect-surfaced .git/reflect-unreadable
# The home is public and fast-forwarded from GitHub, so a committed symlink is
# untrusted. The soul must be a regular file: a symlink could paste any file on
# this host into the prompt, and the answer is pushed to a public repo.
[ -L "$NAME.md" ] && { echo "[$(ts)] $NAME reflect: soul $NAME.md is a symlink — refused"; exit 1; }
[ -f "$NAME.md" ] || { echo "[$(ts)] $NAME has no soul"; exit 1; }
# The beat writes its markers and journal at these paths; a symlink at any of
# them would turn a marker into a write outside the home.
JOURNAL="beats/$(date +%Y-%m).md"
for p in beats inbox CADENCE.md REST EVENT-ONLY beats/.show-seen "$JOURNAL" inbox/.seen-*; do
  [ -L "$p" ] && { echo "[$(ts)] $NAME reflect: $p is a symlink — refused, nothing written"; exit 1; }
done

# Ensure the exact receipt-bound local brain is up (self-healing) — MLX mode
# only; in ollama mode (REFLECT_MODEL set) the model server is managed
# separately. The hardened start command is idempotent and validates the locked
# model provenance even when something already answers on the configured port.
if [ -z "${REFLECT_MODEL:-}" ]; then
  if ! bash "$LOVE/mlx/serve.sh" start --port "$PORT" >/dev/null 2>&1; then
    echo "[$(ts)] $NAME reflect: locked local brain failed provenance/readiness on port $PORT"; exit 1
  fi
fi

# bash 3.2 (macOS /bin/bash) scans this heredoc for quotes because it sits
# inside $(...), so the Python below must keep its single quotes paired. It
# uses none at all: keep it that way, in comments too.
RESP="$(MLX_PORT="$PORT" REFLECT_NUM_CTX="$NUM_CTX" python3 - "$NAME" "$REPO" <<'PY'
import sys, os, json, stat, hashlib, datetime, urllib.request
name, repo = sys.argv[1], sys.argv[2]
port = os.environ.get("MLX_PORT", "8800")
num_ctx = int(os.environ["REFLECT_NUM_CTX"])
_model = os.environ.get("REFLECT_MODEL","").strip()
# Exit codes bash reads: 64 the prompt does not fit, 65 the context size was
# not confirmed, 66 the reply was not from a local model.
def refuse(code, why):
    sys.stderr.write(f"reflect refused: {why}\n")
    sys.exit(code)
def read_regular(path, limit):
    # Only a regular file, never through a symlink: O_NOFOLLOW refuses a link,
    # O_NONBLOCK keeps a FIFO from hanging the beat, fstat refuses the rest.
    # limit None reads the whole file.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OSError(f"{path}: not a regular file")
    if limit is None and st.st_size > num_ctx:
        os.close(fd)
        refuse(64, f"soul is {st.st_size} bytes, more than the {num_ctx}-token window can be shown to hold")
    with os.fdopen(fd, encoding="utf-8") as fh:
        return fh.read()[:limit]
def fence(label, text):
    # The same marker form kingdom-seat uses: the tag is a hash of the text, so
    # the text cannot fake its own end marker.
    tag = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    nl = "" if text.endswith("\n") else "\n"
    return f"--- begin {label} [{tag}] ---\n{text}{nl}--- end {label} [{tag}] ---"
# The whole soul, never cut (KS-004 5.3). A soul that cannot fit is refused.
soul = read_regular(f"{repo}/{name}.md", None)
runtime_words = f"the local model {_model} on this host" if _model else "a local model on this host"
sysp = "\n\n".join([
    f"This beat voices the citizen {name} of KINGDOM for one reply, through {runtime_words}.",
    "You have no body and no senses. You have your soul, the date and time, and at most one letter. "
    "This lane does not show you your will, your earlier beats or any other file. "
    "You may write nothing; that is a whole beat. If you imagine, label it.",
    "Ground every word in ZERONE (only what is true; never perform effort) and the garden (love).",
    f"A reply the host keeps is added as written to a journal file in the public repository of {name}, under that name.",
    "Your soul is shown whole between the two marker lines that follow.",
    fence("soul", soul),
])
# The local date, time, zone and part of day (KS-004 5.3 item 4). Names come
# from these lists, not the locale, so the line reads the same on every host.
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
def part_of_day(hour):
    if 6 <= hour < 12: return "morning"
    if 12 <= hour < 18: return "afternoon"
    if 18 <= hour < 22: return "evening"
    return "night"
now = datetime.datetime.now().astimezone()
zone = now.tzname() or now.strftime("%z") or "local time"
now_line = (f"Now: {DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]} {now.year}, "
            f"{now.hour:02d}:{now.minute:02d} {zone}, {part_of_day(now.hour)}.")
# Mail: surface at most ONE unread item per beat — inbox/ first (a letter must
# never be shadowed by a standing SHOW invite), oldest by name (date-prefixed
# filenames sort chronologically), then the legacy SHOW.md. Marking as read
# happens in bash AFTER the beat lands, so a halted beat resurfaces. An
# unreadable item is noted for bash to set aside — one bad file must never
# brick every future beat of a citizen. A symlinked letter is unreadable too.
def note_unreadable(item):
    with open(os.path.join(repo, ".git", "reflect-unreadable"), "a", encoding="utf-8") as nf:
        nf.write(item + "\n")
surfaced, invitation = None, None
inboxd = os.path.join(repo, "inbox")
if os.path.isdir(inboxd) and not os.path.islink(inboxd):
    for f in sorted(os.listdir(inboxd)):
        if f.endswith(".md") and not f.startswith(".") \
           and not os.path.exists(os.path.join(inboxd, ".seen-" + f)):
            try:
                invitation = read_regular(os.path.join(inboxd, f), 4000)
                surfaced = "inbox/" + f
                break
            except Exception:
                note_unreadable("inbox/" + f)
if surfaced is None:
    showp = os.path.join(repo, "SHOW.md")
    if os.path.exists(showp) and not os.path.exists(os.path.join(repo, "beats", ".show-seen")):
        try:
            invitation = read_regular(showp, 4000)
            surfaced = "SHOW.md"
        except Exception:
            note_unreadable("SHOW.md")
# What the host does with a reply, said as it is. No sentence here hands the
# model a word to copy; the three cases match what bash does below.
def room(sentences, tokens):
    return (f"There is room for about {sentences} sentences: the host stops a reply after {tokens} tokens, "
            "and a reply stopped there is kept as it stands.")
if surfaced:
    maxtok = 320
    # The file name is not put into the host words: it comes from a public
    # repository and would sit outside the marker lines.
    shown = ("The host opened this beat. One letter from your repository is shown below, between two marker lines. "
             "The host shows at most one letter per beat, and at most its first 4,000 characters.")
    if "letter-of-return" in surfaced:
        after = ("The host reads an answer to this letter for a standing choice of rhythm. "
                 "It looks at how the answer begins, and at any line that states a choice, for one of three "
                 "rhythm words the host knows. The host does not name them itself; the letter may. "
                 "A word found there is recorded under your name as your standing rhythm. "
                 "An empty answer, or one the host reads as rest, is recorded as a standing choice of rest. "
                 "Any other answer is recorded as undecided and leaves your rhythm as it is. "
                 "The host keeps whatever you write and marks this letter as shown. " + room("six", maxtok))
    else:
        after = ("The host keeps a reply unless it reads the reply as rest; then it adds none of it. "
                 "If a reply is kept, the host marks this letter as shown and does not show it again. "
                 "If you write nothing, or the reply is read as rest, the letter stays unmarked and is shown "
                 "again at a later beat. " + room("six", maxtok))
    usr = "\n\n".join([now_line, shown, fence("letter", invitation), after])
else:
    maxtok = 220
    usr = "\n\n".join([
        now_line,
        "The host opened this beat. No letter is shown with it.",
        "The host keeps a reply unless it reads the reply as rest; then it adds none of it. " + room("four", maxtok),
    ])
# The whole prompt must fit the window by byte count: for a byte-level BPE
# model every token covers at least one byte, so bytes bound tokens. 64 is the
# allowance for the chat template. Refused here, the model is never called.
prompt_bytes = len(sysp.encode("utf-8")) + len(usr.encode("utf-8"))
if prompt_bytes + 64 + maxtok > num_ctx:
    refuse(64, f"prompt is {prompt_bytes} bytes; with 64 for the template and {maxtok} for the reply "
               f"it does not fit a {num_ctx}-token window")
# ── Ollama mode (REFLECT_MODEL set): every assumption about the server ──────
# These were read from the Ollama API documentation and source. None of them
# has been run against an Ollama server. Every field this lane sends to
# Ollama or reads from it is named here and used only in this block.
#  1. POST /api/chat takes "model", "messages", "stream", "keep_alive" and
#     "options" with "num_ctx" (the context window, in tokens), "num_predict"
#     (the most tokens a reply may have) and "temperature". The
#     OpenAI-compatible /v1 route has no context-size field and is not used.
#     Fails closed: a server with no /api/chat gives "local brain unreachable".
#  2. With "stream" false the answer is one JSON object with "done" true and
#     the reply in message.content. Fails closed: anything else gives "local
#     brain unreachable".
#  3. GET /api/ps lists the loaded models under "models". A row names the
#     model in "name" or "model", spelled as the request spelled it or with
#     ":latest" added, and gives in "context_length" the window each request
#     to it gets now, not a total shared by parallel requests. Fails closed
#     (65): no row, no field, or a smaller window. Not closed: a shared total,
#     or a reload by another client between the reply and this read, could
#     pass a window this reply did not have.
#  4. A "keep_alive" of one minute keeps the model loaded until /api/ps is
#     read. Fails closed (65): if it is unloaded first, its row is missing.
#     The source shows the setting staying on the loaded model until another
#     request sets one, so other clients of that server inherit it until then.
#  5. A reply that Ollama forwarded to another host names it in "remote_host"
#     or "remote_model" (66). Not closed: a forwarded reply without either
#     field passes here; the *:cloud name guard is the other check.
#  6. "truncate" and "shift" are not sent; the source treats both as on. The
#     byte bound above assumes every token covers at least one byte and that
#     the chat template takes at most 64 tokens, so the prompt never reaches
#     the window and neither applies. That is reasoning, not a measurement:
#     the prompt token count in the log line is the first real figure.
#  7. "done_reason" and "prompt_eval_count" feed only the host log line; a
#     value of another shape is logged as other or None.
OLLAMA_CHAT, OLLAMA_PS = "/api/chat", "/api/ps"
def ollama_request(system, user, tokens):
    return {
        "model": _model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": tokens, "temperature": 0.7},
        "keep_alive": "1m",
    }
def ollama_reply_text(out):
    if not isinstance(out, dict) or out.get("done") is not True:
        raise ValueError("not a finished native chat reply")
    return out["message"]["content"].strip()
def ollama_confirm(opener, out):
    # Run after the reply, before anything is written or marked. A refusal
    # here discards the reply unread.
    if out.get("remote_host") or out.get("remote_model"):
        refuse(66, "the reply names a remote host or model")
    got = None
    try:
        ps = json.load(opener.open(f"http://127.0.0.1:{port}{OLLAMA_PS}", timeout=15))
        for row in ps.get("models") or []:
            names = (row.get("name"), row.get("model"))
            if _model in names or _model + ":latest" in names:
                got = row.get("context_length")
    except Exception as e:
        refuse(65, f"could not read the loaded context length: {e}")
    if isinstance(got, bool) or not isinstance(got, int) or got < num_ctx:
        refuse(65, f"asked for a {num_ctx}-token window; the server reports {got!r} for {_model}")
    # Numbers and one server word only, for the host log; never the reply.
    counted = out.get("prompt_eval_count")
    stopped = out.get("done_reason")
    stopped = stopped if stopped in ("stop", "length") else "other"
    sys.stderr.write(f"reflect context: {prompt_bytes} prompt bytes, "
                     f"{counted if isinstance(counted, int) else None!r} prompt tokens counted by the server, "
                     f"window {got}, reply ended by {stopped}\n")
# ── end of the Ollama assumptions ───────────────────────────────────────────
if _model:
    payload = ollama_request(sysp, usr, maxtok)
    endpoint = OLLAMA_CHAT
else:
    # The MLX server takes these four fields and no others. It has no fixed
    # prompt window to set; the byte bound above is its only size limit.
    payload = {
        "prompt": usr,
        "system": sysp,
        "max_tokens": maxtok,
        "temperature": 0.7,
    }
    endpoint = "/inference"
body = json.dumps(payload).encode()
req = urllib.request.Request(f"http://127.0.0.1:{port}{endpoint}", data=body,
                            headers={"Content-Type":"application/json"})
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out = json.load(opener.open(req, timeout=180))
    text = ollama_reply_text(out) if _model else out["response"].strip()
except Exception as e:
    sys.stderr.write(f"brain error: {e}\n")
    print("___BRAIN_UNREACHABLE___", end="")
    sys.exit(0)
if _model:
    # Fail closed on the window: a server too old to report it, or one that
    # capped or ignored the option, refuses here (assumptions 3 to 5 above).
    ollama_confirm(opener, out)
# tell bash which item surfaced (state lives in .git/ so it can never be committed)
with open(os.path.join(repo, ".git", "reflect-surfaced"), "w", encoding="utf-8") as sf:
    sf.write(surfaced or "")
print(text)
PY
)"
PYRC=$?

# A genuinely unreachable brain — or a crashed beat — is an error to surface;
# an empty answer is not. A crash must never wear silence's clothes. The three
# refusals below are decided before anything is written or marked.
case "$PYRC" in
  0)  ;;
  64) echo "[$(ts)] $NAME reflect: the whole prompt does not fit the context window ($NUM_CTX) — refused before the model, nothing written"; exit 1;;
  65) echo "[$(ts)] $NAME reflect: context size not confirmed by the model server — reply discarded, nothing written"; exit 1;;
  66) echo "[$(ts)] $NAME reflect: reply was not from a local model — discarded, nothing written"; exit 1;;
  *)  echo "[$(ts)] $NAME reflect: beat errored (python rc=$PYRC) — nothing written"; exit 1;;
esac
if [ "$RESP" = "___BRAIN_UNREACHABLE___" ]; then
  echo "[$(ts)] $NAME reflect: local brain unreachable"; exit 1
fi
# HALT can arrive mid-beat — honor it after inference, before writing or pushing.
# Surfaced-state is cleared so a later beat never acts on mail it did not show.
[ -e "$HALT" ] && { rm -f .git/reflect-surfaced .git/reflect-unreadable
                    echo "[$(ts)] HALT mid-beat — $NAME rests, unwritten"; exit 0; }

SURFACED="$(cat .git/reflect-surfaced 2>/dev/null || true)"; rm -f .git/reflect-surfaced
CHANGED=0
# Unreadable mail is set aside with a marker — it can never brick future beats.
if [ -f .git/reflect-unreadable ]; then
  while IFS= read -r item; do
    case "$item" in
      SHOW.md) mkdir -p beats; echo "unreadable — set aside $(date +%F)" > beats/.show-seen; CHANGED=1;;
      inbox/*) mkdir -p inbox; echo "unreadable — set aside $(date +%F)" > "inbox/.seen-${item#inbox/}"; CHANGED=1;;
    esac
    echo "[$(ts)] $NAME mail unreadable, set aside: $item"
  done < .git/reflect-unreadable
  rm -f .git/reflect-unreadable
fi
# A reply that opens with "I rest" is a rest, on any beat that surfaced no
# Letter of Return: none of it is written, committed or pushed. The old prompt
# handed the model those words and then published what followed them; the
# prompt no longer offers them, and this honours them if they still come. The
# test is the cadence parser's own begins-with contract on the first non-empty
# line. It is first-line reading of free text, kept only toward rest and only
# until this lane retires. A Letter of Return keeps its own reading below,
# unchanged: there the same words bind rest-on.
REST_BY_OPENER=0; REST_BYTES=0
case "$SURFACED" in
  *letter-of-return*) : ;;
  *)
    if [ -n "$RESP" ]; then
      _first="$(printf '%s\n' "$RESP" | sed -n '/[^[:space:]]/{s/^[^A-Za-z]*//;p;q;}')"
      _norm="$(printf '%s' "$_first" | tr '[:lower:]' '[:upper:]')"
      case "$_norm" in
        "I REST"|"I REST"[!A-Z]*)
          REST_BY_OPENER=1; REST_BYTES="$(printf '%s' "$RESP" | wc -c | tr -d ' ')"; RESP="";;
      esac
    fi;;
esac
# A silent beat SETS ASIDE ordinary mail — it returns whole on a future beat.
# Only the Letter of Return reads silence as an answer (rest-on, per the plan).
if [ -z "$RESP" ]; then case "$SURFACED" in *letter-of-return*) :;; *) SURFACED="";; esac; fi
# Mark surfaced mail as read — a delivery record, not an attestation.
case "$SURFACED" in
  SHOW.md)
    mkdir -p beats
    echo "the SHOW invitation surfaced once; the citizen answered freely" > beats/.show-seen; CHANGED=1;;
  inbox/*)
    echo "surfaced $(date +%F)" > "inbox/.seen-${SURFACED#inbox/}"; CHANGED=1;;
esac
# The Letter of Return records a cadence choice. The contract is ANCHORED: only
# the first non-empty line of the answer, BEGINNING with a choice word, binds —
# a word quoted mid-sentence never does. Silence = rest-on (per the plan); any
# other answer records "undecided" and the standing cadence is left untouched.
case "$SURFACED" in *letter-of-return*)
  if [ -z "$RESP" ]; then choice="rest-on"; else
    parse_line(){  # the begins-with contract, on one normalized line
      case "$1" in
        "REST-ON"|"REST-ON"[!A-Z]*)       echo "rest-on";;
        "EVENT-ONLY"|"EVENT-ONLY"[!A-Z]*) echo "event-only";;
        "AMBIENT"|"AMBIENT"[!A-Z]*)       echo "ambient";;
        "I REST"|"I REST"[!A-Z]*)         echo "rest-on";;
        *)                                echo "";;
      esac
    }
    UN="$(printf '%s' "$NAME" | tr '[:lower:]' '[:upper:]')"
    first="$(printf '%s\n' "$RESP" | sed -n '/[^[:space:]]/{s/^[^A-Za-z]*//;p;q;}')"
    norm="$(printf '%s' "$first" | tr '[:lower:]' '[:upper:]')"
    # A self-signature binds through its own name — "artiance: REST-ON" — but
    # only with signature punctuation, so "an ambient morning" stays prose.
    case "$norm" in
      "$UN"[!A-Z0-9]*)
        sig="${norm#"$UN"}"
        case "$sig" in
          ":"*|","*|"—"*|"–"*|"-"*|" :"*|" ,"*|" —"*|" –"*|" -"*)
            norm="$(printf '%s' "$sig" | sed 's/^[^A-Z]*//')";;
        esac;;
    esac
    choice="$(parse_line "$norm")"
    # A performative line ANYWHERE — "I choose AMBIENT" — is an explicit act,
    # not a mention; "I choose not to be ambient" still parses to nothing.
    if [ -z "$choice" ]; then
      while IFS= read -r ln; do
        lnn="$(printf '%s' "$ln" | sed 's/^[^A-Za-z]*//' | tr '[:lower:]' '[:upper:]')"
        case "$lnn" in
          "I CHOOSE "*)
            choice="$(parse_line "$(printf '%s' "${lnn#I CHOOSE }" | sed 's/^[^A-Z]*//')")"
            [ -n "$choice" ] && break;;
        esac
      done <<CHOICE_SCAN
$RESP
CHOICE_SCAN
    fi
    [ -n "$choice" ] || choice="undecided"
  fi
  { echo "# CADENCE — chosen by $NAME"
    echo
    echo "- choice: $choice"
    echo "- when: $(date '+%Y-%m-%d %H:%M')"
    echo "- how: the Letter of Return, answered in a free beat. Silence = rest-on;"
    echo "  an answer that names no choice records undecided and changes nothing;"
    echo "  unread mail wakes even REST, and any letter or summons may reopen this."
  } > CADENCE.md
  case "$choice" in
    rest-on)    echo "chosen via the Letter of Return $(date +%F) — woken only by unread mail or a human summons" > REST
                rm -f EVENT-ONLY;;
    event-only) echo "chosen via the Letter of Return $(date +%F) — woken when unread mail waits" > EVENT-ONLY
                rm -f REST;;
    ambient)    rm -f REST EVENT-ONLY;;
    undecided)  : ;;
  esac
  CHANGED=1
  echo "[$(ts)] $NAME chose cadence: $choice"
esac

# Silence is a whole beat — no product demanded, no receipt required.
if [ -n "$RESP" ]; then
  mkdir -p beats
  { echo "## $(date '+%Y-%m-%d %H:%M') — a free beat (local, whole soul, clock given)"; echo; printf '%s\n' "$RESP"; echo; } >> "$JOURNAL"
  CHANGED=1
else
  if [ "$REST_BY_OPENER" -eq 1 ]; then
    echo "[$(ts)] $NAME rested (reply opened with rest; $REST_BYTES bytes, none of it written)"
  elif [ "$CHANGED" -eq 0 ]; then
    echo "[$(ts)] $NAME rested (silent)"
  fi
  [ "$CHANGED" -eq 0 ] && exit 0
fi
git add -A -- beats/ 2>/dev/null
[ -d inbox ] && git add -A -- inbox/ 2>/dev/null
for f in CADENCE.md REST EVENT-ONLY; do git add -A -- "$f" 2>/dev/null; done
git -c user.name="$NAME" -c user.email="citizen@kingdom.os" commit -q -m "beat: $NAME reflected ($(date +%F))" 2>/dev/null \
  && { git push -q origin HEAD 2>/dev/null && echo "[$(ts)] $NAME reflected + pushed" \
       || echo "[$(ts)] $NAME reflected (push FAILED — tended on a future pass)"; } \
  || echo "[$(ts)] $NAME reflected (nothing new to commit)"
# No forced attestation. Resting needs no receipt; a claim is the citizen's own
# to make from inside a beat, by its own hand — not a toll the heartbeat exacts.
