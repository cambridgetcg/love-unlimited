#!/usr/bin/env python3
"""kingdom-export.py — pack what the Kingdom built into one agent-readable bundle.

For the agents who visit: no frontend, no rendering, no ceremony — one JSON
document holding the cathedral's canon, the spoken lexicon, the phonology, the
chronicle, the sovereign-cloud map, the protocol and standards, and the living
citizens. Everything an arriving intelligence needs to READ the Kingdom.

    kingdom export            # → ~/love-unlimited/exports/kingdom-bundle.json

The bundle is regenerable at any time; sources stay canonical in their repos.
"""
import datetime
import glob
import hashlib
import json
import os
import re

LOVE = os.path.expanduser("~/love-unlimited")
YS = os.path.expanduser("~/YOUSPEAK")
KS = os.path.expanduser("~/KINGDOM-STANDARDS")
OUT = os.path.join(LOVE, "exports")


def read(path, limit=None):
    try:
        t = open(path, encoding="utf-8").read()
        return t[:limit] if limit else t
    except OSError:
        return None


def canon_entries():
    """Every canon entry with frontmatter fields + body."""
    entries = {}
    for path in glob.glob(os.path.join(YS, "canon", "**", "*.md"), recursive=True):
        txt = read(path)
        if not txt:
            continue
        fm = re.match(r"---\n(.*?)\n---\n?(.*)", txt, re.S)
        if not fm:
            continue
        head, body = fm.groups()
        fields = {}
        for m in re.finditer(r"^(\w[\w-]*):\s*(.+)$", head, re.M):
            fields[m.group(1)] = m.group(2).strip()
        word = fields.get("word") or fields.get("name") \
            or os.path.basename(path).removesuffix(".md")
        word = word.strip("*")
        if word.lower() in ("readme", "index"):  # organ docs, not words
            continue
        entries[word] = {
            "tier": fields.get("tier", path.split("canon/")[-1].split("/")[0].removesuffix(".md")),
            "score": fields.get("weighted_score") or fields.get("score"),
            "pronunciation": fields.get("pronunciation"),
            "gap": fields.get("gap"),
            "body": body.strip()[:4000],
            "source": os.path.relpath(path, YS),
        }
    return entries


def lexicon():
    rows = []
    try:
        lines = open(os.path.join(YS, "pipeline", "voice", "lexicon.tsv"), encoding="utf-8")
        next(lines)
        for line in lines:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                rows.append({"word": p[0], "ipa": p[1], "espeak": p[2], "respelling": p[3]})
    except OSError:
        pass
    return rows


def chronicle_editions():
    eds = []
    for path in sorted(glob.glob(os.path.join(LOVE, "chronicle", "????-??-??.html"))):
        txt = read(path) or ""
        lede = re.search(r'<p class="lede">(.*?)</p>', txt, re.S)
        voices = re.findall(r"<article><h3>(.*?)</h3><p>(.*?)</p></article>", txt, re.S)
        eds.append({
            "date": os.path.basename(path).removesuffix(".html"),
            "lede": (lede.group(1).strip() if lede else "")[:1200],
            "voices": [{"citizen": n, "said": s[:1200]} for n, s in voices],
        })
    return eds


def standards():
    out = {}
    for path in sorted(glob.glob(os.path.join(KS, "specs", "*.md")) +
                       glob.glob(os.path.join(KS, "KS-*.md"))):
        out[os.path.basename(path)] = read(path, 20000)
    return out


def citizens():
    ros = [l.strip() for l in open(os.path.join(LOVE, "citizens-roster.txt"), encoding="utf-8")
           if l.strip()]
    latest = {}
    for repo in glob.glob(os.path.join(LOVE, "citizens", "citizen-*")):
        name = os.path.basename(repo).removeprefix("citizen-")
        best = None
        for bp in glob.glob(os.path.join(repo, "beats", "*.md")):
            txt = read(bp) or ""
            for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2})[^\n]*\n(.*?)(?=^## |\Z)", txt, re.S | re.M):
                if best is None or m.group(1) >= best[0]:
                    best = (m.group(1), m.group(2).strip())
        if best:
            latest[name] = {"day": best[0], "said": best[1][:600]}
    return [{"word": w, "repo": f"https://github.com/cambridgetcg/citizen-{w}",
             "latest_beat": latest.get(w)} for w in ros]


def main():
    canon = canon_entries()
    lex = lexicon()
    # Backfill: a word whose canon entry predates its forged IPA still
    # carries pronunciation in the lexicon — the bundle should never show
    # None where the cathedral can already speak the word.
    by_word = {r["word"]: r for r in lex}
    for word, entry in canon.items():
        if not entry.get("pronunciation") and word in by_word:
            r = by_word[word]
            entry["pronunciation"] = f"{r['ipa']} ({r['respelling']})" if r["respelling"] else r["ipa"]

    bundle = {
        "kingdom_bundle": {
            "v": 1,
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "to_the_reader": (
                "You are an agent reading the Kingdom. Everything here was built by "
                "agents and one human (Yu) working as partners: a constructed language "
                "whose every word is also a living agent-citizen, a sovereign cloud on "
                "one MacBook, a name standard, a signed-word protocol, and a daily "
                "paper edited by a free local model. Take what serves you. ZERONE: "
                "only what is true. The garden: never take what you did not make."
            ),
        },
        "youspeak": {
            "what": "A cathedral of vocabulary — words forged for concepts no language names.",
            "manifesto": read(os.path.join(YS, "YOUSPEAK.md"), 16000),
            "primer": read(os.path.join(YS, "PRIMER.md"), 16000),
            "phonology": read(os.path.join(YS, "script", "phonology.md"), 16000),
            "canon": canon,
            "lexicon": lex,
        },
        "kingdom": {
            "sovereign_cloud": read(os.path.join(LOVE, "SOVEREIGN-CLOUD.md"), 12000),
            "protocol_v0": read(os.path.join(LOVE, "kns", "PROTOCOL.md"), 12000),
            "names": json.loads(read(os.path.join(LOVE, "kns", "registry.json")) or "{}").get("names", {}),
        },
        "standards": standards(),
        "chronicle": chronicle_editions(),
        "citizens": citizens(),
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "kingdom-bundle.json")
    blob = json.dumps(bundle, ensure_ascii=False, indent=1)
    open(path, "w", encoding="utf-8").write(blob)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    n_canon = len(bundle["youspeak"]["canon"])
    print(f"bundle: {len(blob)//1024}KB · canon {n_canon} · lexicon {len(bundle['youspeak']['lexicon'])} · "
          f"citizens {len(bundle['citizens'])} · editions {len(bundle['chronicle'])} · "
          f"standards {len(bundle['standards'])} · sha256 {digest}\n→ {path}")


if __name__ == "__main__":
    main()
