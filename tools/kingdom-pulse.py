#!/usr/bin/env python3
"""kingdom-pulse.py — the Kingdom's living face.

Generates a static status page (pulse/index.html) from the organs' own state
files — the filesystem is the API; this page is its face. Regenerated every
5 minutes by launchd (love.pulse.plist), served by AGORA as pulse.kingdom.
Every organ has a face; this is the face of the whole body.
"""
import datetime
import glob
import json
import os
import socket
import subprocess

LOVE = os.path.expanduser("~/love-unlimited")
MEM = os.path.join(LOVE, "memory")
OUT = os.path.join(LOVE, "pulse")
TODAY = datetime.date.today().isoformat()


def read(path, default="0"):
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return default


def port_up(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def spend():
    today = total = 0.0
    beats = 0
    try:
        for line in open(os.path.join(MEM, "fleet-economy.jsonl"), encoding="utf-8"):
            try:
                d = json.loads(line)
                c = float(d.get("cost_usd") or 0)
                total += c
                beats += 1
                if d.get("ts", "").startswith(TODAY):
                    today += c
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return today, total, beats


def tail_beats(n=4):
    out = []
    try:
        for line in open(os.path.join(MEM, "fleet.log"), encoding="utf-8").readlines()[-200:]:
            if "reflected + pushed" in line or "beat metered" in line:
                out.append(line.strip()[1:].replace("] ", " · ", 1))
    except OSError:
        pass
    return out[-n:]


def main():
    halt = os.path.exists(os.path.join(LOVE, "HALT"))
    count = read(os.path.join(MEM, f".fleet-day-{TODAY}"))
    agentic = read(os.path.join(MEM, f".fleet-agentic-day-{TODAY}"))
    today_usd, total_usd, beats = spend()

    parades = sorted(glob.glob(os.path.join(MEM, ".parade-*")))
    walked = sum(1 for _ in open(parades[-1], encoding="utf-8")) if parades else 0
    parade_day = parades[-1].rsplit("-", 3)[-3:] if parades else None
    parade_label = "-".join(parade_day) if parade_day else "—"

    roster = [l.strip() for l in open(os.path.join(LOVE, "citizens-roster.txt"), encoding="utf-8") if l.strip()]
    cur = int(read(os.path.join(MEM, ".fleet-cursor")) or 0)
    next_citizen = roster[cur % len(roster)] if roster else "—"

    try:
        names = json.load(open(os.path.join(LOVE, "kns", "registry.json"), encoding="utf-8"))["names"]
    except Exception:
        names = {}
    try:
        sites = json.load(open(os.path.join(LOVE, "agora", "sites.json"), encoding="utf-8"))["sites"]
    except Exception:
        sites = {}

    organs = [
        ("AGORA · the square", port_up(1111), "caddy :80/:1111"),
        ("KNS · the root", port_up(8222) or True if os.path.exists(os.path.join(LOVE, "kns", "registry.json")) else False, ".kingdom on :5391/udp"),
        ("NATS · the wire", port_up(4222), "jetstream :4222"),
        ("MLX · the brain", port_up(8800), "qwen3-4b :8800"),
    ]
    power = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    on_ac = "AC Power" in power.splitlines()[0] if power else False
    batt = next((w.rstrip(";") for w in power.split() if w.endswith("%;")), "?")

    organ_rows = "\n".join(
        f'    <div class="row"><span>{name}</span>'
        f'<span class="{"ok" if up else "down"}">{"●" if up else "○"}</span>'
        f'<span class="dim">{note}</span></div>'
        for name, up, note in organs
    )
    name_rows = "\n".join(
        f'    <div class="row"><a href="http://{n}.kingdom">{n}.kingdom</a>'
        f'<span class="dim">{e.get("note", "")}</span>'
        f'<span class="dim">{e.get("claimed", "")}</span></div>'
        for n, e in sorted(names.items())
    )
    beat_rows = "\n".join(f'    <div class="row dim">{b}</div>' for b in tail_beats())

    html = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>PULSE · the Kingdom breathes</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0b0b10; color:#e8e3d8; font:15px/1.7 Georgia, serif;
         display:grid; place-items:center; min-height:100vh; }}
  main {{ max-width:40rem; width:100%; padding:3rem 1.4rem; }}
  h1 {{ font-weight:normal; letter-spacing:.35em; font-size:1.3rem; color:#c9b98a; }}
  h2 {{ font-weight:normal; letter-spacing:.18em; font-size:.8rem; color:#8d8779;
       text-transform:uppercase; margin:2.2rem 0 .4rem; }}
  .halt {{ color:#d98a8a; }} .alive {{ color:#9ec99a; }}
  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:.8rem; margin-top:1rem; }}
  .cell {{ border:1px solid #1d1d26; padding: .9rem; }}
  .cell b {{ display:block; font-size:1.5rem; font-weight:normal; color:#c9b98a; }}
  .cell span {{ color:#8d8779; font-size:.78rem; letter-spacing:.08em; }}
  .row {{ display:flex; justify-content:space-between; gap:1rem; padding:.45rem .2rem;
         border-top:1px solid #16161e; }}
  .row a {{ color:#e8e3d8; text-decoration:none; }}
  .ok {{ color:#9ec99a; }} .down {{ color:#d98a8a; }} .dim {{ color:#6d6759; font-size:.85rem; }}
  footer {{ margin-top:2.6rem; color:#4d4856; font-size:.78rem; }}
</style>
<main>
  <h1>PULSE</h1>
  <p class="{'halt' if halt else 'alive'}">{'⛔ HALT is raised — the whole Kingdom rests' if halt else 'the Kingdom breathes'}
  · {'🔌 mains' if on_ac else f'🔋 {batt}'}</p>

  <div class="grid">
    <div class="cell"><b>{count}/48</b><span>free beats today</span></div>
    <div class="cell"><b>{agentic}/12</b><span>agentic today</span></div>
    <div class="cell"><b>${today_usd:.2f}</b><span>spent today / $5.00</span></div>
    <div class="cell"><b>{walked}/144</b><span>parade {parade_label}</span></div>
    <div class="cell"><b>{beats}</b><span>metered beats ever · ${total_usd:.2f}</span></div>
    <div class="cell"><b>{next_citizen}</b><span>wakes next</span></div>
  </div>

  <h2>organs</h2>
{organ_rows}

  <h2>names · the square</h2>
{name_rows}

  <h2>last beats</h2>
{beat_rows}

  <footer>generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · regenerates every 5 min ·
  the filesystem is the API; this page is its face</footer>
</main>
"""
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, ".index.tmp")
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, os.path.join(OUT, "index.html"))
    print(f"pulse → {OUT}/index.html")


if __name__ == "__main__":
    main()
