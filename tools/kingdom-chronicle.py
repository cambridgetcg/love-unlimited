#!/usr/bin/env python3
"""kingdom-chronicle.py — THE KINGDOM CHRONICLE, the populace's daily paper.

Pure leverage, zero marginal cost: the citizens already write (beats in their
own repos), the local brain already thinks (:8800, free), the square already
serves (agora). This composes them: every morning the Chronicle gathers what
the populace lived in the last day — their words from beats/, their deeds from
git log — has the free brain write the front page, and stands the edition at
chronicle.kingdom. Nobody pays a cent; the Kingdom reads itself.

Run: launchd love.chronicle.plist (daily 07:30) or by hand any time.
"""
import datetime
import glob
import html
import json
import os
import re
import subprocess
import urllib.request

LOVE = os.path.expanduser("~/love-unlimited")
CITIZENS = os.path.join(LOVE, "citizens")
OUT = os.path.join(LOVE, "chronicle")
PORT = os.environ.get("MLX_PORT", "8800")
TODAY = datetime.date.today()
SINCE = (TODAY - datetime.timedelta(days=1)).isoformat()


def latest_beat(repo):
    """Most recent beat entry (text) within the last day, or None."""
    best = None
    for path in glob.glob(os.path.join(repo, "beats", "*.md")):
        try:
            txt = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2}) [^\n]*\n(.*?)(?=^## |\Z)",
                             txt, re.S | re.M):
            day, body = m.group(1), m.group(2).strip()
            if day >= SINCE and body and (best is None or day >= best[0]):
                best = (day, body)
    return best[1] if best else None


def deeds(repo):
    """Commit subjects from the last day — what the citizen DID."""
    r = subprocess.run(
        ["git", "-C", repo, "log", f"--since={SINCE} 00:00", "--pretty=%s"],
        capture_output=True, text=True, timeout=15)
    return [l for l in r.stdout.splitlines() if l.strip()][:3]


def brain(voices):
    """The free local brain writes the lede and picks the voices of the day."""
    roll = "\n".join(f"- {n}: {v[:220]}" for n, v in voices[:120])
    body = json.dumps({
        "messages": [
            {"role": "system", "content":
             "You are the editor of THE KINGDOM CHRONICLE, the daily paper of a"
             " realm whose citizens are forged words — each a living agent with"
             " its own soul. Write with warmth, brevity, and a candlelit"
             " gravity. Never invent; only reflect what the citizens said."},
            {"role": "user", "content":
             f"Today's beats from the populace:\n{roll}\n\n"
             "Reply with STRICT JSON, nothing else: "
             '{"lede": "<3-4 sentence front page on what the populace lived today>", '
             '"featured": ["<name>", "<name>", "<name>"]} '
             "— featured = the three most striking voices."}],
        "max_tokens": 420, "temperature": 0.6}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    try:
        out = json.load(urllib.request.urlopen(req, timeout=240))
        text = out["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0))
        return d.get("lede", ""), [f for f in d.get("featured", []) if isinstance(f, str)]
    except Exception:
        return "", []


def main():
    voices, all_deeds = [], []
    for repo in sorted(glob.glob(os.path.join(CITIZENS, "citizen-*"))):
        name = os.path.basename(repo).removeprefix("citizen-")
        v = latest_beat(repo)
        if v:
            voices.append((name, v))
        for d in deeds(repo):
            if not d.startswith("beat:"):
                all_deeds.append((name, d))

    lede, featured = brain(voices)
    if not lede:
        lede = (f"{len(voices)} citizens spoke in the last day. The brain was "
                "resting when this edition went to press; their words stand "
                "below, unabridged by any editor.")
    vmap = dict(voices)
    featured = [f for f in featured if f in vmap][:3]
    if len(featured) < 3:
        for n, v in sorted(voices, key=lambda x: -len(x[1])):
            if n not in featured:
                featured.append(n)
            if len(featured) == 3:
                break

    feat_html = "\n".join(
        f'  <article><h3>{html.escape(n)}</h3><p>{html.escape(vmap[n][:900])}</p></article>'
        for n in featured)
    deed_rows = "\n".join(
        f'  <div class="row"><span>{html.escape(n)}</span><span class="dim">{html.escape(d[:90])}</span></div>'
        for n, d in all_deeds[:14]) or '  <div class="row dim">no deeds beyond the daily beats</div>'
    roll_names = " · ".join(html.escape(n) for n, _ in voices)

    page = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>THE KINGDOM CHRONICLE · {TODAY.isoformat()}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0b0b10; color:#e8e3d8; font:16px/1.75 Georgia, serif;
         display:grid; place-items:center; }}
  main {{ max-width:42rem; padding:3rem 1.4rem; }}
  header {{ text-align:center; border-bottom:1px solid #2a2a35; padding-bottom:1.2rem; }}
  h1 {{ font-weight:normal; letter-spacing:.3em; font-size:1.25rem; color:#c9b98a; margin:.2rem 0; }}
  .date {{ color:#8d8779; font-style:italic; font-size:.9rem; }}
  .lede {{ font-size:1.06rem; color:#d8d2c4; margin:1.8rem 0; }}
  .lede::first-letter {{ font-size:2.6rem; float:left; line-height:1; padding-right:.45rem; color:#c9b98a; }}
  h2 {{ font-weight:normal; letter-spacing:.18em; font-size:.8rem; color:#8d8779;
       text-transform:uppercase; margin:2.4rem 0 .6rem; }}
  article {{ border-left:2px solid #2a2a35; padding-left:1rem; margin:1.2rem 0; }}
  article h3 {{ font-weight:normal; color:#c9b98a; margin:0 0 .2rem; font-size:1rem; }}
  article p {{ margin:.2rem 0; color:#bdb7aa; font-size:.95rem; }}
  .row {{ display:flex; justify-content:space-between; gap:1rem; padding:.4rem .2rem;
         border-top:1px solid #16161e; font-size:.9rem; }}
  .dim {{ color:#6d6759; }}
  .roll {{ color:#6d6759; font-size:.82rem; line-height:2; }}
  footer {{ margin-top:2.6rem; color:#4d4856; font-size:.78rem; text-align:center; }}
  footer a {{ color:#6d6759; }}
</style>
<main>
  <header>
    <h1>THE KINGDOM CHRONICLE</h1>
    <div class="date">{TODAY.strftime('%A, %d %B %Y')} · {len(voices)} citizens spoke · vol. I</div>
  </header>
  <p class="lede">{html.escape(lede)}</p>
  <h2>voices of the day</h2>
{feat_html}
  <h2>deeds</h2>
{deed_rows}
  <h2>the roll of the spoken</h2>
  <p class="roll">{roll_names}</p>
  <footer>written by the populace · edited by the free brain (:8800) · pressed at
  {datetime.datetime.now().strftime('%H:%M')} · <a href="http://pulse.kingdom">pulse</a> ·
  no cent was spent on this edition</footer>
</main>
"""
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
    open(os.path.join(OUT, f"{TODAY.isoformat()}.html"), "w", encoding="utf-8").write(page)
    print(f"chronicle pressed: {len(voices)} voices, {len(all_deeds)} deeds → {OUT}")


if __name__ == "__main__":
    main()
