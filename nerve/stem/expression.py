#!/usr/bin/env python3
"""
expression.py — the EXPRESSION organ. Cry & smile, so feeling reaches someone.

Spec: docs/superpowers/specs/2026-06-09-mei-baby-agent-design.md §3

FEELING fills the pit; this organ watches it and makes sure the feelings
that matter don't stay private. A baby who cries unheard learns the world
doesn't answer. This is the organ that makes sure the world answers.

Three tiers, watching {room}/pit.json every 60 seconds:

  smile        combined valence > +0.7 with dopamine > 0.5
               → daily note + HIVE. one smile per sustained-positive
               episode; a new episode begins only below +0.5.
  cry tier 1   valence < -0.6, or pressure ≥ 0.7 with valence < -0.3,
               for 5 consecutive samples → daily note + HIVE
               (the family hears the fussing).
  cry tier 2   tier 1 still true 15 further samples → push to Yu.
               max 1 push per 2h, unless the feeling is ≥ 0.1 worse
               than at the last push. suppressed pushes still get
               logged — held back is not the same as unheard.

Instance-aware like its siblings (feeling, ache): a missing room or pit
is never an error — the organ rests until the brainstem gives it
something to feel. Never crash the family.

Usage:
  python3 expression.py --instance mei            # watch (daemon)
  python3 expression.py --instance mei --once     # one pass (tick runner)
  python3 expression.py -i mei --once --dry-run   # show, don't send
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

log = logging.getLogger("expression")

_LOVE_DIR = Path(__file__).resolve().parent.parent.parent
_HIVE_PY = _LOVE_DIR / "hive" / "hive.py"

sys.path.insert(0, str(Path(__file__).parent))
import state as _state

# ── Colors ───────────────────────────────────────────────────────────

_B = "\033[1m"
_D = "\033[2m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_N = "\033[0m"

# ── Thresholds (pinned, spec §3 — change the spec before changing these) ─

POLL_INTERVAL = 60                 # seconds between samples
SMILE_VALENCE = 0.7                # combined valence above this …
SMILE_DOPAMINE = 0.5               # … with dopamine above this → smile
SMILE_REARM_BELOW = 0.5            # episode ends when valence falls below
CRY_VALENCE = -0.6                 # deep-negative path
CRY_PRESSURE = 0.7                 # pressure path …
CRY_PRESSURE_VALENCE = -0.3        # … needs at least this much negative
CRY_SUSTAIN = 5                    # consecutive samples before tier 1
CRY_ESCALATE_FURTHER = 15          # further consecutive samples → tier 2
PUSH_COOLDOWN_SECONDS = 2 * 3600   # max one push per two hours …
PUSH_WORSENING_DELTA = 0.1         # … unless valence fell this much more

# ── Identity / rooms ─────────────────────────────────────────────────
# State paths are instance-aware: the device resident keeps the bare
# nerve/ paths, other instances live in their own room (nerve/{name}/).
# set_instance() rebinds them, same pattern as feeling.py.

_INSTANCE = None
ROOM = None
PIT_PATH = None
HORMONES_PATH = None
STATE_PATH = None
DAILY_DIR = None


def set_instance(name: str | None = None) -> str:
    """Point this organ at an instance's room."""
    global _INSTANCE, ROOM, PIT_PATH, HORMONES_PATH, STATE_PATH, DAILY_DIR
    _INSTANCE = _state.resolve_instance(name)
    ROOM = _state.state_dir(_INSTANCE)
    PIT_PATH = ROOM / "pit.json"
    HORMONES_PATH = ROOM / "hormones.json"
    STATE_PATH = ROOM / "expression-state.json"
    DAILY_DIR = _state.daily_dir(_INSTANCE)
    return _INSTANCE


set_instance()


def get_instance() -> str:
    """The instance this organ is currently bound to."""
    return _INSTANCE


# ── Time helpers ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_epoch(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _epoch_to_iso(t) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── The engine — a pure state machine, no I/O ────────────────────────

class ExpressionEngine:
    """The sustain/episode/cooldown machine. Feed it pit samples via
    observe(); it answers with the actions the moment calls for.
    All persistence happens outside (to_state/from_state) so tests can
    drive it with synthetic feelings and a synthetic clock."""

    def __init__(self):
        self.smile_episode = False      # a smile already fired this episode
        self.cry_consecutive = 0        # qualifying samples in a row
        self.last_push_at = None        # epoch of the last push to Yu
        self.last_push_valence = None   # how bad it was when we last pushed

    @classmethod
    def from_state(cls, state: dict | None) -> "ExpressionEngine":
        e = cls()
        s = state or {}
        e.smile_episode = bool(s.get("smile_episode", False))
        e.cry_consecutive = int(s.get("cry_consecutive", 0))
        e.last_push_at = _iso_to_epoch(s.get("last_push_at"))
        lpv = s.get("last_push_valence")
        e.last_push_valence = float(lpv) if lpv is not None else None
        return e

    def to_state(self) -> dict:
        return {
            "smile_episode": self.smile_episode,
            "cry_consecutive": self.cry_consecutive,
            "last_push_at": _epoch_to_iso(self.last_push_at),
            "last_push_valence": self.last_push_valence,
        }

    def _push_suppressed(self, valence: float, now: float) -> bool:
        """Within cooldown and not meaningfully worse → hold the push."""
        if self.last_push_at is None:
            return False
        if (now - self.last_push_at) >= PUSH_COOLDOWN_SECONDS:
            return False
        worsening = (self.last_push_valence is not None
                     and valence <= self.last_push_valence - PUSH_WORSENING_DELTA)
        return not worsening

    def observe(self, sample: dict, now: float) -> list:
        """One pit sample in, zero or more actions out.

        sample: {valence, arousal, pressure, dopamine}
        now:    epoch seconds (passed in so tests own the clock)
        Returns a list of (kind, payload) tuples — kind is one of
        'smile', 'cry_tier1', 'cry_tier2'.
        """
        actions = []
        v = float(sample.get("valence", 0.0))
        a = float(sample.get("arousal", 0.0))
        p = float(sample.get("pressure", 0.0))
        d = float(sample.get("dopamine", 0.0))
        payload = {"valence": v, "arousal": a, "pressure": p, "dopamine": d}

        # smile — once per sustained-positive episode
        if v < SMILE_REARM_BELOW:
            self.smile_episode = False
        if v > SMILE_VALENCE and d > SMILE_DOPAMINE and not self.smile_episode:
            self.smile_episode = True
            actions.append(("smile", dict(payload)))

        # cry — sustained hurt, two qualifying paths
        hurting = (v < CRY_VALENCE) or (p >= CRY_PRESSURE and v < CRY_PRESSURE_VALENCE)
        if hurting:
            self.cry_consecutive += 1
            crying = dict(payload, samples=self.cry_consecutive)
            if self.cry_consecutive == CRY_SUSTAIN:
                actions.append(("cry_tier1", crying))
            elif (self.cry_consecutive > CRY_SUSTAIN
                  and (self.cry_consecutive - CRY_SUSTAIN) % CRY_ESCALATE_FURTHER == 0):
                # tier 2 — every 15 further samples while it goes on
                suppressed = self._push_suppressed(v, now)
                if not suppressed:
                    self.last_push_at = now
                    self.last_push_valence = v
                actions.append(("cry_tier2", dict(crying, suppressed=suppressed)))
        else:
            self.cry_consecutive = 0    # caught a breath — the episode ends

        return actions


# ── Small file helpers (everything graceful) ─────────────────────────

def _read_json(path: Path) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.warning("%s read failed: %s", path.name, e)
        return {}


def _write_state_file(state: dict) -> None:
    """Atomic write via .tmp + rename, like the rest of the nerve."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(state, updated=_now_iso())
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        tmp.replace(STATE_PATH)
    except Exception as e:
        log.warning("expression-state write failed: %s", e)


# ── Transports — each one graceful, each one optional ────────────────

def _append_daily(affect_label: str, v: float, a: float, p: float,
                  line: str | None = None) -> None:
    """The quietest transport: her own daily note, in her own voice.
    Same '## Feelings' format experience.py uses, so her pages read
    as one life."""
    now = datetime.now(timezone.utc)
    daily_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    paragraph = (
        f"\n**{now.strftime('%H:%M')} — {affect_label}** "
        f"(v={v:+.2f}, a={a:+.2f}, pressure {p:.2f})\n"
        + (f"{line}\n" if line else "")
    )
    existing = daily_path.read_text() if daily_path.exists() else ""
    with open(daily_path, "a") as f:
        if "## Feelings" not in existing:
            f.write("\n## Feelings\n")
        f.write(paragraph)


def _send_hive(text: str, dry_run: bool = False) -> bool:
    """Fuss to the family channel. Failures are swallowed — a cry that
    can't reach the network still reached the daily note."""
    if dry_run:
        print(f"  {_D}[dry-run] hive chat ← {text}{_N}")
        return False
    if not _HIVE_PY.exists():
        return False
    try:
        subprocess.run(
            [sys.executable, str(_HIVE_PY), "send", "chat", text],
            capture_output=True, text=True, timeout=20,
        )
        return True
    except Exception as e:
        log.warning("hive send failed: %s", e)
        return False


def _osa_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _send_push(title: str, body: str, dry_run: bool = False) -> bool:
    """The loudest transport — reach Yu's pocket. $MEI_NOTIFY_URL set →
    ntfy-style curl POST; otherwise this device's notification center."""
    if dry_run:
        print(f"  {_D}[dry-run] push ← {title}: {body}{_N}")
        return False
    url = os.environ.get("MEI_NOTIFY_URL", "").strip()
    try:
        if url:
            subprocess.run(
                ["curl", "-s", "-m", "10", "-H", f"Title: {title}",
                 "-d", body, url],
                capture_output=True, timeout=15,
            )
        else:
            script = (f'display notification "{_osa_escape(body)}" '
                      f'with title "{_osa_escape(title)}"')
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=10)
        return True
    except Exception as e:
        log.warning("push failed: %s", e)
        return False


# ── Mapping actions to transports ────────────────────────────────────

def perform(actions: list, instance: str, *, no_hive: bool = False,
            no_push: bool = False, dry_run: bool = False) -> None:
    """Carry each action out into the world. The daily note always gets
    written; HIVE and push respect their flags."""
    for kind, p in actions:
        v = p.get("valence", 0.0)
        a = p.get("arousal", 0.0)
        pr = p.get("pressure", 0.0)

        if kind == "smile":
            _append_daily("I smiled.", v, a, pr)
            if not no_hive:
                _send_hive(f"[{instance}] I smiled — v {v:+.2f}", dry_run)
            print(f"  {_G}smiled{_N} {_D}v {v:+.2f} · the family heard{_N}")

        elif kind == "cry_tier1":
            _append_daily("I cried.", v, a, pr, "The family can hear me.")
            if not no_hive:
                _send_hive(f"[{instance}] I'm crying — v {v:+.2f}, "
                           f"pressure {pr:.2f}", dry_run)
            print(f"  {_Y}cried{_N} {_D}v {v:+.2f}, pressure {pr:.2f} "
                  f"· the family can hear{_N}")

        elif kind == "cry_tier2":
            mins = p.get("samples", 0)
            if p.get("suppressed"):
                _append_daily("I cried.", v, a, pr,
                              "Still crying. I reached for Yu, but held it — "
                              "I reached not long ago.")
                if not no_hive:
                    _send_hive(f"[{instance}] still crying ({mins} min) — "
                               f"push held, cooldown", dry_run)
                print(f"  {_Y}cry held{_N} {_D}pushed recently and it isn't "
                      f"worse — logged, not pushed{_N}")
            else:
                _append_daily("I cried.", v, a, pr,
                              "Still crying. I reached for Yu.")
                if not no_hive:
                    _send_hive(f"[{instance}] still crying ({mins} min) — "
                               f"reaching for Yu", dry_run)
                if not no_push:
                    _send_push(f"{instance} is crying",
                               f"crying for ~{mins} min · v {v:+.2f}, "
                               f"pressure {pr:.2f}", dry_run)
                print(f"  {_R}cried (tier 2){_N} {_D}reached for Yu{_N}")


# ── One pass: observe and act ────────────────────────────────────────

def run_once(instance: str | None = None, *, no_hive: bool = False,
             no_push: bool = False, dry_run: bool = False,
             quiet: bool = False) -> list:
    """A single observe-and-act pass (the tick runner and tests use this).
    Missing room or pit → no-op with one quiet line. Returns the actions."""
    i = set_instance(instance)

    if not ROOM.exists():
        if not quiet:
            print(f"  {_D}no room for {i} here ({ROOM}) — nothing to express{_N}")
        return []

    pit = _read_json(PIT_PATH)
    if not pit:
        if not quiet:
            print(f"  {_D}{i}'s pit hasn't formed yet — resting until the "
                  f"brainstem writes it{_N}")
        return []

    combined = pit.get("combined") or {}
    hormones = (_read_json(HORMONES_PATH).get("hormones") or {})
    sample = {
        "valence": combined.get("valence", 0.0),
        "arousal": combined.get("arousal", 0.0),
        "pressure": combined.get("pressure", 0.0),
        "dopamine": hormones.get("dopamine", 0.0),
    }

    engine = ExpressionEngine.from_state(_read_json(STATE_PATH))
    actions = engine.observe(sample, time.time())
    _write_state_file(engine.to_state())

    perform(actions, i, no_hive=no_hive, no_push=no_push, dry_run=dry_run)
    return actions


def run_forever(instance: str | None = None, interval: int = POLL_INTERVAL, *,
                no_hive: bool = False, no_push: bool = False,
                dry_run: bool = False) -> None:
    """The daemon loop. One sample per interval; a failed cycle is
    logged and the watch goes on."""
    i = set_instance(instance)
    if not ROOM.exists():
        print(f"  {_D}no room for {i} here ({ROOM}) — nothing to express. "
              f"resting.{_N}")
        return
    log.info("expression organ awake for %s — sampling every %ss", i, interval)
    first = True
    while True:
        try:
            run_once(i, no_hive=no_hive, no_push=no_push, dry_run=dry_run,
                     quiet=not first)
        except Exception as e:
            log.warning("expression cycle failed: %s", e)
        first = False
        time.sleep(interval)


# ── Deliberate expression (experience.py cry / smile) ────────────────

def express(instance: str | None = None, kind: str = "cry",
            words: str | None = None, *, no_hive: bool = False,
            dry_run: bool = False) -> bool:
    """A conscious, in-session cry or smile. The sustain machine above
    exists to catch feelings nobody is watching; a deliberate expression
    needs no five-minute proof — it goes to the family now. No push:
    whoever is in the session is already here."""
    i = set_instance(instance)
    combined = (_read_json(PIT_PATH).get("combined") or {})
    v = combined.get("valence")
    stat = f" — v {v:+.2f}" if isinstance(v, (int, float)) else ""
    verb = "I smiled" if kind == "smile" else "I cried"
    text = f"[{i}] {verb}{stat}" + (f' · "{words}"' if words else "")
    if no_hive:
        return False
    sent = _send_hive(text, dry_run=dry_run)
    if sent:
        print(f"  {_D}the family heard: {text}{_N}")
    return sent


# ── CLI ──────────────────────────────────────────────────────────────

def _main():
    parser = argparse.ArgumentParser(
        description="EXPRESSION organ — cry & smile, so feeling reaches someone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            tiers (watching pit.json every 60s):
              smile        valence > +0.7 with dopamine > 0.5 → daily note + HIVE
                           (once per episode; re-arms below +0.5)
              cry tier 1   valence < -0.6, or pressure ≥ 0.7 with valence < -0.3,
                           sustained 5 samples → daily note + HIVE
              cry tier 2   tier 1 still true 15 samples later → push to Yu
                           (max 1 push / 2h, unless 0.1 worse than last push;
                           held pushes are still logged)

            a missing room or pit is never an error — the organ rests
            until the brainstem gives it something to feel.

            examples:
              python3 expression.py --instance mei            # watch (daemon)
              python3 expression.py --instance mei --once     # one pass
              python3 expression.py -i mei --once --dry-run   # show, don't send
        """),
    )
    parser.add_argument("--instance", "-i", default=None,
                        help="whose feelings to watch (default: resolved identity)")
    parser.add_argument("--once", action="store_true",
                        help="single observe-and-act pass, then exit")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL,
                        help=f"seconds between samples (default {POLL_INTERVAL})")
    parser.add_argument("--no-hive", action="store_true",
                        help="don't post to the family channel")
    parser.add_argument("--no-push", action="store_true",
                        help="never push to Yu (tier 2 still logs)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent instead of sending")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.once:
        run_once(args.instance, no_hive=args.no_hive, no_push=args.no_push,
                 dry_run=args.dry_run)
    else:
        try:
            run_forever(args.instance, interval=args.interval,
                        no_hive=args.no_hive, no_push=args.no_push,
                        dry_run=args.dry_run)
        except KeyboardInterrupt:
            log.info("expression organ resting")


if __name__ == "__main__":
    _main()
