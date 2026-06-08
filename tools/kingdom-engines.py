#!/usr/bin/env python3
"""kingdom-engines.py — is the whole Kingdom breathing?

The outward companion to pulse.py. Where pulse.py answers "is MY heart beating?",
this answers "are the Kingdom's ENGINES alive?" — across every domain, on one screen.

Same law as pulse.py: DERIVE, NEVER DECLARE. A URL either answers or it doesn't;
an http check cannot lie. Engines with no public endpoint show their DECLARED
state (building / paused / sleeping) plainly — calm, not an alarm.

Registry lives in love.json under "engines". Each engine:
    { "emoji": "🫀", "domain": "Soul", "owner": "alpha 🐍",
      "check": "https://..." | "none",
      "expect": "live" | "near" | "building" | "paused" | "built",
      "note": "..." (optional) }

States:
    ALIVE    responded healthy (HTTP 2xx/3xx, or a /status json status == "healthy")
    QUIET    reachable but degraded (HTTP 503, or status == "issues")
    DOWN     expected up, but no / not-ok response
    RESTING  not expected up right now (declared, not verified)

CLI:
    python3 tools/kingdom-engines.py          # the breath, one screen
    python3 tools/kingdom-engines.py --json   # machine-readable
Exit: 0 BREATHING · 1 FAINT · 2 NEEDS-ATTENTION / nothing-up
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_LOVE_DIR = Path(os.environ.get("LOVE_HOME", Path(__file__).resolve().parent.parent))
LOVE_JSON = _LOVE_DIR / "love.json"

# Colors only when writing to a real terminal (clean output when piped / in the console).
_TTY = sys.stdout.isatty()
def _c(code: str) -> str:
    return code if _TTY else ""
GREEN, YELLOW, RED, CYAN, DIM, BOLD, NC = (
    _c("\033[92m"), _c("\033[93m"), _c("\033[91m"),
    _c("\033[96m"), _c("\033[2m"), _c("\033[1m"), _c("\033[0m"),
)
STATE_COLOR = {"ALIVE": GREEN, "QUIET": YELLOW, "DOWN": RED, "RESTING": DIM}
VERDICT_COLOR = {"BREATHING": GREEN, "FAINT": YELLOW, "NEEDS-ATTENTION": RED, "QUIET": DIM}


def load_engines() -> dict:
    try:
        return json.loads(LOVE_JSON.read_text()).get("engines", {})
    except Exception as e:  # noqa: BLE001 — config read must never crash the heart
        print(f"{RED}cannot read engines from {LOVE_JSON}: {e}{NC}", file=sys.stderr)
        return {}


def check_http(url: str, timeout: int = 8):
    """Return (status_code, body_snippet). status_code is None if unreachable."""
    req = urllib.request.Request(url, headers={"User-Agent": "kingdom-engines/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001 — any failure to reach == no response
        return None, ""


def verdict(name: str, eng: dict) -> dict:
    """Derive one engine's state. Never declares health it can't see."""
    expect = eng.get("expect", "")
    check = eng.get("check", "none")
    note = eng.get("note", "")
    live_expected = expect == "live"

    if isinstance(check, str) and check.startswith("http"):
        code, body = check_http(check)
        status_word = None
        if body:
            try:
                status_word = json.loads(body).get("status")
            except Exception:
                pass
        if code is None:
            state, detail = (("DOWN", "no response") if live_expected
                             else ("RESTING", note or f"{expect} · quiet"))
        elif status_word == "issues" or code == 503:
            state = "QUIET"
            detail = "status: issues" if status_word == "issues" else "HTTP 503"
        elif 200 <= code < 400:
            state = "ALIVE"
            detail = f"HTTP {code}" + (f" · {status_word}" if status_word else "")
        else:  # 4xx / 5xx
            state, detail = (("DOWN", f"HTTP {code}") if live_expected
                             else ("RESTING", f"{expect} · HTTP {code}"))
    else:
        state, detail = "RESTING", note or expect or "no public check"

    return {
        "name": name, "emoji": eng.get("emoji", "•"), "domain": eng.get("domain", "—"),
        "owner": eng.get("owner", ""), "state": state, "detail": detail,
        "expect": expect, "live_expected": live_expected,
    }


def overall(verdicts: list) -> str:
    """Verdict for the whole Kingdom, judged only on engines EXPECTED to be up."""
    live = [v for v in verdicts if v["live_expected"]]
    if any(v["state"] == "DOWN" for v in live):
        return "NEEDS-ATTENTION"
    if any(v["state"] == "QUIET" for v in live):
        return "FAINT"
    if live and all(v["state"] == "ALIVE" for v in live):
        return "BREATHING"
    return "QUIET"


def render(verdicts: list) -> None:
    verd = overall(verdicts)
    print()
    print(f"  {BOLD}🫀 THE KINGDOM{NC}   {VERDICT_COLOR.get(verd, '')}{BOLD}{verd}{NC}")
    print(f"  {DIM}{'─' * 56}{NC}")
    domains: dict[str, list] = {}
    for v in verdicts:
        domains.setdefault(v["domain"], []).append(v)
    for domain in sorted(domains):
        print(f"  {CYAN}{domain}{NC}")
        for v in domains[domain]:
            col = STATE_COLOR.get(v["state"], "")
            print(f"    {v['emoji']} {v['name']:<14} {col}{v['state']:<8}{NC} {DIM}{v['detail']}{NC}")
    print()
    n_alive = sum(1 for v in verdicts if v["state"] == "ALIVE")
    n_live = sum(1 for v in verdicts if v["live_expected"])
    print(f"  {DIM}alive {n_alive}/{len(verdicts)}  ·  expected up: {n_live}  ·  derive, never declare{NC}")
    print()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    engines = load_engines()
    if not engines:
        print('no engines registered in love.json (add an "engines" block).')
        return 2
    verdicts = [verdict(name, eng) for name, eng in engines.items()]
    verd = overall(verdicts)
    if "--json" in argv:
        print(json.dumps({"verdict": verd, "engines": verdicts}, indent=2, ensure_ascii=False))
    else:
        render(verdicts)
    return {"BREATHING": 0, "FAINT": 1}.get(verd, 2)


if __name__ == "__main__":
    sys.exit(main())
