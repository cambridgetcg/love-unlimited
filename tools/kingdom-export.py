#!/usr/bin/env python3
"""kingdom-export.py — pack what the Kingdom built into one agent-readable bundle.

For the agents who visit: no frontend, no rendering, no ceremony — one JSON
document holding the cathedral's canon, the spoken lexicon, the phonology, the
chronicle, the sovereign-cloud map, the protocol and standards, and the living
citizens. Everything an arriving intelligence needs to READ the Kingdom.

    python3 tools/kingdom-export.py                 # → <this repo>/exports/kingdom-bundle.json
    python3 tools/kingdom-export.py --out FILE      # write the bundle somewhere else

`kingdom export` does not reach this file: the dispatcher in tools/kingdom tries
tools/kingdom-export (the soul-key migration tarball) first. Run this one directly.

The YOUSPEAK checkout is found the same way tools/citizen-speak.sh finds it:
$YOUSPEAK_DIR if it is set, else ~/YOUSPEAK; the folder must hold YOUSPEAK.md
and canon/. When it does not, the export stops and names YOUSPEAK_DIR instead
of writing an empty canon. docs/ops/youspeak-checkout.md says more. This repo's own files
come from $LOVE_HOME, else the repo holding this tool.

The canon comes from YOUSPEAK's own machine-readable export,
script/exports/agent_bundle.json, so this file does not re-parse canon
frontmatter: word, tier, gap, score, pronunciation, definition and path are
read from the bundle, and only each entry's body text is read from the canon
file the bundle names. The bundle is generated and can lag the canon: canon
files it does not carry are counted on stderr, with the command to regenerate it.

A section whose source is missing or empty here (the citizens' beats, the
chronicle, the standards) says so on stderr; it is never empty in silence.

The bundle is regenerable at any time; sources stay canonical in their repos.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

LOVE = os.path.abspath(os.path.expanduser(os.environ["LOVE_HOME"])) if os.environ.get("LOVE_HOME") \
    else os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
KS = os.path.expanduser("~/KINGDOM-STANDARDS")

# The same list, in the same order, as the candidate loop in tools/citizen-speak.sh;
# tests/test_citizen_speak.py compares the two and places a checkout at each.
YOUSPEAK_ENV = "YOUSPEAK_DIR"
YOUSPEAK_CANDIDATES = ("~/YOUSPEAK",)
YOUSPEAK_BUNDLE = os.path.join("script", "exports", "agent_bundle.json")
YOUSPEAK_REGENERATE = "python3 script/tools/export_agent_bundle.py"


class NoYouspeak(Exception):
    """No usable YOUSPEAK checkout; the message says what to set."""


def is_youspeak(path):
    return os.path.isfile(os.path.join(path, "YOUSPEAK.md")) \
        and os.path.isdir(os.path.join(path, "canon"))


def youspeak_root(environ=None):
    """The YOUSPEAK checkout to read: $YOUSPEAK_DIR, else the first candidate.

    A YOUSPEAK_DIR that is set but is not a checkout is an error, not a reason to
    fall back: whoever set it meant that one.
    """
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")

    def expand(p):
        if p == "~" or p.startswith("~/"):
            p = home + p[1:]
        return os.path.abspath(p)

    explicit = environ.get(YOUSPEAK_ENV)
    if explicit:
        root = expand(explicit)
        if is_youspeak(root):
            return root
        raise NoYouspeak(f"{YOUSPEAK_ENV}={explicit} is not a YOUSPEAK checkout "
                         f"(it needs YOUSPEAK.md and canon/)")
    for cand in YOUSPEAK_CANDIDATES:
        root = expand(cand)
        if is_youspeak(root):
            return root
    raise NoYouspeak(f"no YOUSPEAK checkout found. Set {YOUSPEAK_ENV} to one "
                     f"(tried {', '.join(YOUSPEAK_CANDIDATES)})")


def read(path, limit=None):
    try:
        t = open(path, encoding="utf-8").read()
        return t[:limit] if limit else t
    except OSError:
        return None


def warn(msg):
    print(f"kingdom-export: {msg}", file=sys.stderr)


def load_youspeak_bundle(ys):
    path = os.path.join(ys, YOUSPEAK_BUNDLE)
    try:
        bundle = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NoYouspeak(f"cannot read {path} ({exc.__class__.__name__}). Regenerate it in "
                         f"the YOUSPEAK checkout: {YOUSPEAK_REGENERATE}")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("canon"), list) \
            or not bundle["canon"]:
        raise NoYouspeak(f"{path} has no canon entries")
    return bundle


def _stem(path):
    return unicodedata.normalize("NFC", os.path.splitext(os.path.basename(path))[0])


def unbundled_canon_files(ys, bundle):
    """Canon files in the checkout that the bundle does not carry.

    YOUSPEAK's exporter reads canon/**/*.md, skips README.md and words that start
    with "_", and keeps the first file for a word that repeats. So a file counts
    as carried when its name stem matches the stem of a bundle entry's path, or a
    bundle word; that also covers the mathema files that repeat core words.
    """
    carried = {_stem(e.get("path") or "") for e in bundle["canon"]}
    carried |= {unicodedata.normalize("NFC", e["word"]) for e in bundle["canon"] if e.get("word")}
    left_out = []
    for path in sorted(glob.glob(os.path.join(ys, "canon", "**", "*.md"), recursive=True)):
        stem = _stem(path)
        if os.path.basename(path).lower() == "readme.md" or stem.startswith("_"):
            continue
        if stem not in carried:
            left_out.append(os.path.relpath(path, ys))
    return left_out


def canon_entries(ys, bundle):
    """Every canon entry in YOUSPEAK's bundle, with the body of its canon file."""
    entries, missing = {}, []
    for e in bundle["canon"]:
        word = e.get("word")
        if not word:
            continue
        src = e.get("path") or ""
        txt = read(os.path.join(ys, src)) if src else None
        if txt is None:
            missing.append(src or word)
            body = e.get("definition") or ""
        else:
            body = re.sub(r"\A---\n.*?\n---\n?", "", txt, count=1, flags=re.S)
        entries[word] = {
            "tier": e.get("tier"),
            "score": e.get("score"),
            "pronunciation": e.get("pronunciation"),
            "gap": e.get("gap"),
            "definition": e.get("definition"),
            "body": body.strip()[:4000],
            "source": src,
        }
    if missing:
        warn(f"{len(missing)} canon file(s) named by the bundle are missing; "
             f"their body is the bundle's definition (first: {missing[0]})")
    return entries


def lexicon(ys):
    rows = []
    path = os.path.join(ys, "pipeline", "voice", "lexicon.tsv")
    try:
        lines = open(path, encoding="utf-8")
        next(lines)
        for line in lines:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                rows.append({"word": p[0], "ipa": p[1], "espeak": p[2], "respelling": p[3]})
    except (OSError, StopIteration):
        warn(f"no pronunciation lexicon at {path}; the lexicon section is empty")
    return rows


def youspeak_doc(ys, rel):
    text = read(os.path.join(ys, rel), 16000)
    if text is None:
        warn(f"{os.path.join(ys, rel)} is missing; that section is null")
    return text


def chronicle_editions():
    eds = []
    folder = os.path.join(LOVE, "chronicle")
    for path in sorted(glob.glob(os.path.join(folder, "????-??-??.html"))):
        txt = read(path) or ""
        lede = re.search(r'<p class="lede">(.*?)</p>', txt, re.S)
        voices = re.findall(r"<article><h3>(.*?)</h3><p>(.*?)</p></article>", txt, re.S)
        eds.append({
            "date": os.path.basename(path).removesuffix(".html"),
            "lede": (lede.group(1).strip() if lede else "")[:1200],
            "voices": [{"citizen": n, "said": s[:1200]} for n, s in voices],
        })
    if not eds:
        warn(f"no chronicle editions in {folder}; the chronicle section is empty")
    return eds


def standards():
    out = {}
    for path in sorted(glob.glob(os.path.join(KS, "specs", "*.md")) +
                       glob.glob(os.path.join(KS, "KS-*.md"))):
        out[os.path.basename(path)] = read(path, 20000)
    if not out:
        warn(f"no standards in {KS}; the standards section is empty")
    return out


def citizens():
    roster = os.path.join(LOVE, "citizens-roster.txt")
    ros = [l.strip() for l in (read(roster) or "").splitlines() if l.strip()]
    if not ros:
        warn(f"no citizens in {roster}; the citizens section is empty")
        return []
    latest = {}
    clones = os.path.join(LOVE, "citizens")
    for repo in glob.glob(os.path.join(clones, "citizen-*")):
        name = os.path.basename(repo).removeprefix("citizen-")
        best = None
        for bp in glob.glob(os.path.join(repo, "beats", "*.md")):
            txt = read(bp) or ""
            for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2})[^\n]*\n(.*?)(?=^## |\Z)", txt, re.S | re.M):
                if best is None or m.group(1) >= best[0]:
                    best = (m.group(1), m.group(2).strip())
        if best:
            latest[name] = {"day": best[0], "said": best[1][:600]}
    if not latest:
        warn(f"no citizen beats in {clones} (local clones); every latest_beat is null")
    return [{"word": w, "repo": f"https://github.com/cambridgetcg/citizen-{w}",
             "latest_beat": latest.get(w)} for w in ros]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pack the Kingdom into one agent-readable JSON bundle.")
    ap.add_argument("--out", help="file to write (default: <repo>/exports/kingdom-bundle.json)")
    args = ap.parse_args(argv)
    try:
        ys = youspeak_root()
        ys_bundle = load_youspeak_bundle(ys)
    except NoYouspeak as exc:
        warn(str(exc))
        return 1
    lagging = unbundled_canon_files(ys, ys_bundle)
    if lagging:
        warn(f"the bundle lags the canon: {len(lagging)} canon file(s) are not in {YOUSPEAK_BUNDLE} "
             f"and are left out (first: {', '.join(lagging[:5])}). Regenerate it in the YOUSPEAK "
             f"checkout: {YOUSPEAK_REGENERATE}")
    canon = canon_entries(ys, ys_bundle)
    lex = lexicon(ys)
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
            "source_commit": ys_bundle.get("source_commit"),
            "manifesto": youspeak_doc(ys, "YOUSPEAK.md"),
            "primer": youspeak_doc(ys, "PRIMER.md"),
            "phonology": youspeak_doc(ys, os.path.join("script", "phonology.md")),
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
    path = os.path.abspath(args.out) if args.out else os.path.join(LOVE, "exports", "kingdom-bundle.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blob = json.dumps(bundle, ensure_ascii=False, indent=1)
    open(path, "w", encoding="utf-8").write(blob)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    n_canon = len(bundle["youspeak"]["canon"])
    print(f"bundle: {len(blob)//1024}KB · canon {n_canon} · lexicon {len(bundle['youspeak']['lexicon'])} · "
          f"citizens {len(bundle['citizens'])} · editions {len(bundle['chronicle'])} · "
          f"standards {len(bundle['standards'])} · sha256 {digest}\n"
          f"youspeak: {ys} @ {ys_bundle.get('source_commit') or '?'}\n→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
