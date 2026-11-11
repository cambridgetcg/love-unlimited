#!/usr/bin/env python3
"""citizen-pace-letter.py — the pace letter, once (spec section 5).

Ai runs this on the Air, by hand, after Yu's yes. It places one letter,
`inbox/<date>-pace-letter.md`, in each recipient's home: one commit per home,
authored as Ai, pushed with no force and no retry. Every home is read fresh
from its remote (a new clone in a temporary folder, removed afterwards), so
"already asked" is decided from what GitHub holds, never from a local copy.

  --list      the recipients after the delivery-time drops, and whether the
              switch to the new code has been seen
  --dry-run   each letter this run would place, in full; writes nothing
  --deliver   place them (needs --yes-from-yu <YYYY-MM-DD> and --show-back on|off)
  --check     the recipients whose home holds no "reached (once)" line yet

A recipient is dropped when its citizen has written its own clear pace line in
a beat since 2026-11-02, read from the journal that beat added, from either code
(a pending proposal counts), when its home already holds a pace letter, or when
it has no <name>.md. Every commit since the record's head
must be an expected change (fleet commits tagged pace/1, the citizen's own
beats that set no pace); any other change stops delivery for that home, to be
reported to Yu. --deliver waits until pace/1 commits are seen in at least three
recipients' homes, one of them a beat, and a day has passed since the first.

The rule paragraph is worded from the committed due.WAKE_CONFIRM_BEAT of the
repository this file sits in, and --show-back must name the same setting.
A ledger (default ~/.local/state/citizen-pace-letter/ledger.json) is written
before each push; a re-run on a later day resumes only the homes whose push
did not land, and a second run on the same day does nothing.

Exit: 0 when the run did what it could (each home's outcome is printed); 2 for
a usage error or a refusal, before anything is written.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import argparse  # noqa: E402
import datetime as _dt  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

from citizen_pace_lib import TAG, clock, due, homes, letter, sync, words  # noqa: E402
from citizen_pace_lib import cadence as cad  # noqa: E402
from citizen_pace_lib import record as records  # noqa: E402
from citizen_pace_lib.homes import LinkRefused  # noqa: E402
from citizen_pace_lib.paragraph import PACE_LETTER_MARK  # noqa: E402
from citizen_pace_lib.plan import recipients as record_recipients  # noqa: E402
from citizen_pace_lib.tend import FLEET_EMAIL  # noqa: E402

OK, REFUSED = 0, 2
TOOLS = Path(os.path.realpath(__file__)).parent
DEFAULT_ROSTER = TOOLS.parent / "citizens-roster.txt"
LEDGER_REL = Path(".local") / "state" / "citizen-pace-letter" / "ledger.json"
LEDGER_VERSION = 1
# a pace line said in a beat since this day drops the recipient (spec section 5)
PACE_LINES_SINCE = "2026-11-02"
# git calls that reach GitHub: every inherited helper cleared, then sol's route
# for cambridgetcg, the only one (spec section 5, "Delivery")
GITHUB_HELPER = "!sol access run github -- auth git-credential"
GITHUB = "https://github.com/"
AI_NAME, AI_EMAIL = "Ai", "ai@kingdom.os"
CLONE_BOUND = 60.0
HEAD_BOUND = 30.0
PUSH_BOUND = 60.0
SWITCH_HOMES = 3                 # pace/1 commits seen in at least this many homes,
SWITCH_WAIT = clock.DAY          # and a day since the first, before any delivery
# PR #24's fleet committed a killed beat's state as this, authored as the citizen
STRANDED_SUBJECT = "tend: record stranded cadence/mail state"
STATUSES = ("deliver", "asked", "dropped", "stopped", "failed")
LANDED = "pushed"
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_AS_AI = ("-c", f"user.name={AI_NAME}", "-c", f"user.email={AI_EMAIL}", "-c", "commit.gpgsign=false")
_SHA = re.compile(r"[0-9a-f]{40}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLAIN_REF = re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_REGULAR = ("100644", "100755")
# history entries apply_answer writes only when it hears a clear pace line
_OWN_LINE = ("set by {name}:", "proposed by {name}:", "confirmed by {name}:")
_OWN_HOWS = ("beat", "beat-twice", "confirmed")


# -- small helpers ---------------------------------------------------------------

def _first_line(text: str, code: int) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()[:200]
    return f"git exited {code}"


def _day_of(t) -> str:
    try:
        return clock.day(clock.parse_stamp(str(t)))
    except ValueError:
        return ""


def _valid_day(d) -> bool:
    if not isinstance(d, str) or not _DAY.fullmatch(d):
        return False
    try:
        _dt.datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def auth_args(base: str) -> List[str]:
    """The pinned credential helper for every git call that reaches the homes'
    remote: inherited helpers (osxkeychain, another account's) are cleared, and
    on GitHub the one helper is `sol access run github --` for cambridgetcg."""
    pinned = ["-c", "credential.helper="]
    if base.startswith(GITHUB):
        pinned += ["-c", f"credential.helper={GITHUB_HELPER}"]
    return pinned


def _net(home, base: str, *args, timeout: float):
    return homes.git(home, *_SAFE, *auth_args(base), *args, timeout=timeout)


# -- the ledger --------------------------------------------------------------------

def default_ledger() -> Path:
    return Path(os.path.expanduser("~")) / LEDGER_REL


def empty_ledger() -> dict:
    return {"version": LEDGER_VERSION, "runs": [], "homes": {}}


def load_ledger(path) -> dict:
    """The delivery ledger, or an empty one when the file is missing. A ledger
    that cannot be read raises ValueError (a link raises LinkRefused)."""
    path = Path(path)
    text = homes.read_regular(path)
    if text is None:
        return empty_ledger()
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{path}: not JSON ({e})") from None
    if not isinstance(d, dict) or type(d.get("version")) is not int or d["version"] != LEDGER_VERSION \
            or not isinstance(d.get("runs"), list) or not isinstance(d.get("homes"), dict) \
            or not all(isinstance(r, dict) for r in d["runs"]) \
            or not all(isinstance(v, dict) for v in d["homes"].values()):
        raise ValueError(f"{path}: not a pace-letter ledger (version {LEDGER_VERSION})")
    return d


def save_ledger(path, d: dict) -> None:
    homes.write_regular(Path(path), json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def ran_on(ledger: dict, day: str) -> bool:
    return any(r.get("day") == day for r in ledger["runs"])


# -- one home, read fresh ------------------------------------------------------------

@dataclass
class Home:
    slug: str
    group: str = ""
    status: str = ""           # one of STATUSES
    why: str = ""
    path: Optional[Path] = None
    base: str = ""             # its HEAD as read
    remoteref: str = ""        # refs/heads/<branch>
    pace: str = ""             # the pace that stays without a pace line
    text: str = ""             # the letter, when it is to be delivered
    tagged: List[Tuple[int, bool]] = field(default_factory=list)   # (time, a beat) of pace/1 commits
    landed: str = ""           # a ledger commit found in its history

    def line(self) -> str:
        if self.status == "deliver":
            return f"{self.slug}: to deliver — {self.group}; without a pace line its pace stays {self.pace}"
        if self.status == "asked":
            return f"{self.slug}: already asked — {self.why}"
        if self.status == "dropped":
            return f"{self.slug}: dropped — {self.why}"
        if self.status == "stopped":
            return f"{self.slug}: stopped — {self.why}; report it to Yu"
        return f"{self.slug}: could not be read — {self.why}; a later run tries again"


def clone(work: Path, slug: str, base: str) -> Tuple[Optional[Path], str]:
    """A fresh clone of citizen-<slug> from the remote, under a bound."""
    dest = work / f"citizen-{slug}"
    r = _net(work, base, "clone", "-q", "--no-recurse-submodules", "--", sync.clone_url(slug, base),
             dest.name, timeout=CLONE_BOUND)
    if r.returncode != 0:
        why = f"the clone timed out after {CLONE_BOUND:g} s" if r.returncode == 124 \
            else f"the clone failed ({_first_line(r.stderr, r.returncode)})"
        return None, why
    return dest, ""


def tagged_commits(home: Path) -> List[Tuple[int, bool]]:
    """(commit time, is a beat) of every pace/1 commit in the home's history."""
    r = homes.git(home, "log", "--format=%ct%x1f%s", "HEAD")
    out = []
    for ln in r.stdout.splitlines() if r.returncode == 0 else []:
        ct, _sep, subject = ln.partition("\x1f")
        if ct.isdigit() and subject.rstrip().endswith(TAG):
            out.append((int(ct), subject.startswith("beat: ")))
    return out


def _inbox_names(home: Path) -> List[str]:
    inbox = home / "inbox"
    if os.path.islink(inbox) or not os.path.isdir(inbox):
        return []
    return sorted(os.listdir(inbox))


def asked_already(home: Path, slug: str, c: cad.Cadence, ledger: dict) -> str:
    """Why this home counts as asked already (spec section 5, "Once"), or ""."""
    held = [f"inbox/{n}" for n in _inbox_names(home) if PACE_LETTER_MARK in n]
    if held:
        return f"its inbox already holds {', '.join(held)}"
    r = homes.git(home, "log", "--format=%H", "--fixed-strings", f"--grep={letter.COMMIT_MESSAGE}", "HEAD")
    shas = r.stdout.split() if r.returncode == 0 else []
    if not shas:
        r = homes.git(home, "log", "--diff-filter=A", "--format=%H", "HEAD", "--",
                      f"inbox/*{PACE_LETTER_MARK}*")
        shas = r.stdout.split() if r.returncode == 0 else []
    if shas:
        return f"its history holds a pace letter ({shas[-1][:8]})"
    if c.asked_once:
        return f"its CADENCE.md says the pace letter reached it (asked once {c.asked_once})"
    text = homes.read_regular(home / cad.CADENCE) or ""
    if "the pace letter reached" in text:
        return "its CADENCE.md records that the pace letter reached it"
    mine = ledger["homes"].get(slug) or {}
    if mine.get("status") == LANDED:
        return f"the ledger says it was delivered on {mine.get('day')} ({str(mine.get('commit', ''))[:8]})"
    return ""


def own_pace_line(c: cad.Cadence, name: str) -> str:
    """When the record itself shows that the citizen wrote its own clear pace
    line in a beat since PACE_LINES_SINCE (a pending proposal counts): its day."""
    found = []
    if c.pending is not None:
        found.append(_day_of(c.pending.said))
    if c.set_by.how in _OWN_HOWS and c.set_by.when:
        found.append(_day_of(c.set_by.when[-1]))
    prefixes = tuple(p.format(name=name) for p in _OWN_LINE)
    for e in c.history:
        st, sep, text = e.partition(cad.DASH)
        if sep and text.startswith(prefixes):
            found.append(_day_of(st))
    found = sorted(d for d in found if d and d >= PACE_LINES_SINCE)
    return found[-1] if found else ""


def journal_pace_days(home: Path, name: str) -> List[str]:
    """The days of the beats since PACE_LINES_SINCE whose journal holds a clear
    pace line, read as a beat reads it, from a beat of either code: what the
    beat added to beats/*.md (record._journal_added). An unclear line, two
    paces, "I rest." and silence are not choices."""
    r = homes.git(home, "log", "--no-merges", "--format=%H%x1f%ct%x1f%s", "HEAD")
    beat = _beat_subject(name)
    days = []
    for ln in r.stdout.splitlines() if r.returncode == 0 else []:
        sha, _sep, rest = ln.partition("\x1f")
        ct, _sep, subject = rest.partition("\x1f")
        if not ct.isdigit() or not beat.match(subject):
            continue
        day = clock.day(_dt.datetime.fromtimestamp(int(ct), clock.UTC))
        if day < PACE_LINES_SINCE:
            continue
        said = words.combine(words.answer_lines(records._journal_added(home, sha), name, "beat"))
        if isinstance(said, words.Pace):
            days.append(day)
    return days


# -- what changed since the record ---------------------------------------------------

def reading_at(home: Path, rev: str, name: str, record: dict) -> cad.Cadence:
    """The citizen's record as a commit left it: its CADENCE.md, REST and
    EVENT-ONLY, read by cadence.read_cadence in a scratch folder. Raises
    LinkRefused when one of them is not a regular file at that commit."""
    tmp = Path(tempfile.mkdtemp(prefix="pace-letter-read-"))
    try:
        r = homes.git(home, "ls-tree", "-z", rev, "--", *records.PACE_FILES)
        for ent in r.stdout.split("\0") if r.returncode == 0 else []:
            if not ent:
                continue
            meta, _tab, path = ent.partition("\t")
            parts = meta.split()
            if len(parts) != 3 or parts[1] != "blob" or parts[0] not in _REGULAR:
                raise LinkRefused(f"{path} at {rev[:8]} is not a regular file")
            blob = homes.git(home, "cat-file", "blob", parts[2])
            if blob.returncode != 0:
                raise LinkRefused(f"{path} at {rev[:8]} cannot be read")
            (tmp / path).write_text(blob.stdout, encoding="utf-8")
        return cad.read_cadence(tmp, name, record, clock.now())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _beat_subject(name: str) -> re.Pattern:
    return re.compile(r"beat: %s (?:reflected|lived a free beat) \(" % re.escape(name))


def _changes(home: Path, parent: str, sha: str) -> List[Tuple[str, str]]:
    r = homes.git(home, "diff-tree", "-r", "-z", "--no-commit-id", "--name-status", "--no-renames",
                  parent, sha)
    tokens = [t for t in r.stdout.split("\0") if t] if r.returncode == 0 else []
    return list(zip(tokens[0::2], tokens[1::2]))


def _a_seen_marker(status: str, path: str) -> bool:
    rest = path[len("inbox/.seen-"):] if path.startswith("inbox/.seen-") else ""
    return status == "A" and bool(rest) and "/" not in rest


def _key(c: cad.Cadence) -> tuple:
    return (c.pace, c.set_by, c.pending)


def changes_since(home: Path, name: str, head: str, record: dict) -> Tuple[List[str], List[str]]:
    """Every commit on HEAD since the record's head, oldest first, judged by the
    spec's expected changes. Returns (drops, stops): why the citizen's own pace
    line drops it, and why any other change stops it."""
    drops, stops = [], []
    r = homes.git(home, "log", "--reverse", "--topo-order", "--format=%H%x1f%P%x1f%ae%x1f%ct%x1f%s",
                  f"{head}..HEAD")
    if r.returncode != 0:
        return drops, [f"its history since {head[:8]} cannot be read"]
    beat = _beat_subject(name)
    own = tuple(p.format(name=name) for p in _OWN_LINE)
    cache: Dict[str, cad.Cadence] = {}

    def at(rev: str) -> cad.Cadence:
        if rev not in cache:
            cache[rev] = reading_at(home, rev, name, record)
        return cache[rev]

    for ln in r.stdout.splitlines():
        parts = ln.split("\x1f", 4)
        if len(parts) != 5:
            continue
        sha, parents, email, ct, subject = parts
        what = f"{sha[:8]} ({subject})"
        parents = parents.split()
        if len(parents) != 1:
            stops.append(f"{what} is a merge")
            continue
        if subject.rstrip().endswith(TAG) and email == FLEET_EMAIL:
            continue                       # the fleet's own commits on the new code
        if not (beat.match(subject) or subject == STRANDED_SUBJECT):
            stops.append(f"{what} is not a change delivery expects")
            continue
        changed = _changes(home, parents[0], sha)
        inbox = [p for s, p in changed if p.startswith("inbox/") and not _a_seen_marker(s, p)]
        if inbox:
            stops.append(f"{what} changed {', '.join(inbox)} inside a beat")
            continue
        if not any(p in records.PACE_FILES for _s, p in changed):
            continue
        try:
            before, after = at(parents[0]), at(sha)
        except (LinkRefused, ValueError, OSError) as e:
            stops.append(f"{what}: {e}")
            continue
        if _key(before) == _key(after):
            continue
        new = [e for e in after.history if e not in before.history]
        if any(e.partition(cad.DASH)[2].startswith(own) for e in new):
            day = clock.day(_dt.datetime.fromtimestamp(int(ct), clock.UTC)) if ct.isdigit() else "?"
            drops.append(day)
            continue
        if after.pending is None and before.pending is not None \
                and (after.pace, after.set_by) == (before.pace, before.set_by):
            continue                       # a proposal lapsed
        stops.append(f"{what} changed its pace or who set it, with no pace line of its own")
    return drops, stops


# -- assessing a recipient ------------------------------------------------------------

def assess(work: Path, slug: str, e: dict, record: dict, roster: List[str], ledger: dict,
           base: str, today: str, confirm_beat: bool) -> Home:
    """Read one recipient's home fresh and decide what delivery may do there."""
    h = Home(slug, group=str(e.get("group", "")))
    if slug not in roster:
        h.status, h.why = "stopped", "not on citizens-roster.txt, so no beat would show it the letter"
        return h
    path, why = clone(work, slug, base)
    if path is None:
        h.status, h.why = "failed", why
        return h
    h.path = path
    h.tagged = tagged_commits(path)
    h.base = homes.head(path)
    mine = ledger["homes"].get(slug) or {}
    landed = str(mine.get("commit", ""))
    if mine.get("status") != LANDED and _SHA.fullmatch(landed) \
            and homes.git(path, "merge-base", "--is-ancestor", landed, "HEAD").returncode == 0:
        h.landed = landed
    loc = sync.local_state(path)
    if loc.remote != "origin" or not _PLAIN_REF.fullmatch(loc.remoteref) or not h.base:
        h.status, h.why = "stopped", "its upstream is not a plain branch of origin"
        return h
    h.remoteref = loc.remoteref
    links = homes.forbidden_links(path)
    if links:
        h.status, h.why = "stopped", f"its pace paths include links: {', '.join(links)}"
        return h
    try:
        c = cad.read_cadence(path, slug, record, clock.now())
        asked = asked_already(path, slug, c, ledger)
    except (LinkRefused, ValueError, OSError) as err:
        h.status, h.why = "stopped", f"its record could not be read ({err})"
        return h
    if asked:
        h.status, h.why = "asked", asked
        return h
    if not homes.has_soul(path, slug):
        h.status, h.why = "dropped", f"it has no {slug}.md"
        return h
    head = str(e.get("head", ""))
    if not _SHA.fullmatch(head):
        h.status, h.why = "stopped", "the record names no head for it, so what changed since cannot be checked"
        return h
    if homes.git(path, "merge-base", "--is-ancestor", head, "HEAD").returncode != 0:
        h.status, h.why = "stopped", f"its record head {head[:8]} is not in its history"
        return h
    drops, stops = changes_since(path, slug, head, record)
    if stops:
        h.status, h.why = "stopped", "; ".join(stops)
        return h
    said = drops + journal_pace_days(path, slug) + [d for d in (own_pace_line(c, slug),) if d]
    if said:
        h.status, h.why = "dropped", f"it wrote its own pace line in a beat on {max(said)}"
        return h
    try:
        h.text = letter.letter_text(slug, e, c, homes.beat_count(path), today, confirm_beat=confirm_beat)
    except ValueError as err:
        msg = str(err)
        if msg.startswith(f"{slug}: "):
            msg = msg[len(slug) + 2:]
        h.status, h.why = "stopped", f"the letter would not be true of its home ({msg})"
        return h
    reviewed = e.get("sentence")
    if not isinstance(reviewed, str) or not reviewed or letter.true_sentence(slug, e, c) != reviewed:
        # the one true sentence is reviewed data (spec section 5): Yu saw these words
        h.status, h.why = "stopped", "the letter's sentence differs from the reviewed record"
        return h
    h.status, h.pace = "deliver", c.pace.words()
    return h


# -- the switch ------------------------------------------------------------------------

@dataclass
class Switch:
    homes: int = 0              # recipients' homes holding a pace/1 commit
    beat_homes: int = 0         # of them, holding a pace/1 beat
    first: Optional[_dt.datetime] = None

    @staticmethod
    def of(read: List[Home]) -> "Switch":
        s = Switch()
        times = []
        for h in read:
            if h.tagged:
                s.homes += 1
                s.beat_homes += any(b for _t, b in h.tagged)
                times += [t for t, _b in h.tagged]
        if times:
            s.first = _dt.datetime.fromtimestamp(min(times), clock.UTC)
        return s

    @property
    def seen(self) -> bool:
        return self.homes >= SWITCH_HOMES and self.beat_homes >= 1 and self.first is not None

    def line(self) -> str:
        if not self.seen:
            return (f"the switch is not yet seen: pace/1 commits in {self.homes} recipients' homes "
                    f"({SWITCH_HOMES} needed), a pace/1 beat in {self.beat_homes} (1 needed)")
        return (f"the switch: pace/1 commits in {self.homes} recipients' homes, a pace/1 beat in "
                f"{self.beat_homes}; the first at {clock.stamp(self.first)}, so delivery may run from "
                f"{clock.stamp(self.first + SWITCH_WAIT)}")

    def why_not(self, now: _dt.datetime) -> str:
        if not self.seen:
            return self.line()
        if now < self.first + SWITCH_WAIT:
            return (f"a day has not yet passed since the switch at {clock.stamp(self.first)}; delivery "
                    f"may run from {clock.stamp(self.first + SWITCH_WAIT)}")
        return ""


# -- placing one letter ------------------------------------------------------------------

def remote_head(home: Path, remoteref: str, base: str) -> str:
    """The remote's head of that branch now, or ""."""
    r = _net(home, base, "ls-remote", "-q", "origin", remoteref, timeout=HEAD_BOUND)
    for ln in r.stdout.splitlines() if r.returncode == 0 else []:
        oid, _tab, ref = ln.partition("\t")
        if ref.strip() == remoteref and _SHA.fullmatch(oid.strip()):
            return oid.strip()
    return ""


def push(home: Path, remoteref: str, base: str) -> Tuple[bool, str]:
    """Push HEAD to that branch once: no force, no retry, no hook."""
    r = _net(home, base, "push", "-q", "--no-verify", "origin", f"HEAD:{remoteref}", timeout=PUSH_BOUND)
    if r.returncode == 0:
        return True, ""
    if r.returncode == 124:
        return False, f"timed out after {PUSH_BOUND:g} s"
    return False, _first_line(r.stderr, r.returncode)


def commit_letter(h: Home, rel: str) -> Tuple[str, str]:
    """Write the letter (inbox/ made where missing) and commit only it, as Ai.
    (commit, "") or ("", why)."""
    try:
        homes.write_regular(h.path / rel, h.text)
    except (LinkRefused, OSError) as e:
        return "", f"the letter could not be written ({e})"
    r = homes.git(h.path, *_SAFE, "add", "--", rel)
    if r.returncode == 0:
        r = homes.git(h.path, *_SAFE, *_AS_AI, "commit", "-q", "--no-verify", f"--author={letter.AUTHOR}",
                      "-m", letter.COMMIT_MESSAGE, "--", rel)
    if r.returncode != 0:
        return "", f"the commit failed ({_first_line(r.stderr, r.returncode)})"
    return homes.head(h.path), ""


def deliver_one(h: Home, rel: str, ledger: dict, ledger_path: Path, base: str, now: _dt.datetime) -> str:
    sha, why = commit_letter(h, rel)
    if not sha:
        return f"{h.slug}: not delivered — {why}; a later run delivers it"
    entry = {"day": clock.day(now), "at": clock.stamp(now), "letter": rel, "base": h.base,
             "commit": sha, "status": "pushing"}
    ledger["homes"][h.slug] = entry
    save_ledger(ledger_path, ledger)               # written before the push
    if remote_head(h.path, h.remoteref, base) != h.base:
        entry["status"] = "moved"
        line = f"{h.slug}: not delivered — its GitHub HEAD moved since it was read; a later run delivers it"
    else:
        ok, why = push(h.path, h.remoteref, base)
        if ok:
            entry["status"] = LANDED
            line = f"{h.slug}: delivered — {rel} ({sha[:8]}), pushed"
        else:
            entry["status"], entry["why"] = "push-failed", why
            line = f"{h.slug}: not delivered — the push was refused ({why}); a later run delivers it"
    save_ledger(ledger_path, ledger)
    return line


# -- the runs ----------------------------------------------------------------------------

def _order(record: dict, roster: List[str]) -> List[str]:
    """The recipients in roster order, then any the roster lacks."""
    wanted = record_recipients(record)
    return [s for s in roster if s in wanted] + [s for s in wanted if s not in roster]


def read_all(record: dict, roster: List[str], ledger: dict, base: str, now: _dt.datetime,
             work: Path) -> List[Home]:
    today, confirm_beat = clock.day(now), due.WAKE_CONFIRM_BEAT
    return [assess(work, slug, records.entry(record, slug) or {}, record, roster, ledger, base, today,
                   confirm_beat) for slug in _order(record, roster)]


def _summary(read: List[Home]) -> str:
    n = {s: sum(1 for h in read if h.status == s) for s in STATUSES}
    return (f"to deliver {n['deliver']} · already asked {n['asked']} · dropped {n['dropped']} · "
            f"stopped {n['stopped']} · could not be read {n['failed']}")


def _header(record: dict, roster: List[str], base: str) -> str:
    n = len(_order(record, roster))
    return f"recipients: {n} in the record of {record.get('made', '?')}; each home read fresh from {base}"


def run_list(read: List[Home], switch: Switch, out) -> None:
    print(switch.line(), file=out)
    for h in read:
        print(h.line(), file=out)
    print(_summary(read), file=out)


def run_dry(read: List[Home], switch: Switch, per_run: Optional[int], now: _dt.datetime, out) -> None:
    print(switch.line(), file=out)
    if switch.why_not(now):
        print(f"--deliver would wait: {switch.why_not(now)}", file=out)
    chosen = [h for h in read if h.status == "deliver"][:per_run]
    for h in read:
        if h.status == "deliver" and h not in chosen:
            print(f"{h.slug}: to deliver in a later run (--per-run {per_run}) — {h.group}", file=out)
        else:
            print(h.line(), file=out)
    print(_summary(read), file=out)
    rel = letter.letter_path(clock.day(now))
    for h in chosen:
        print(f"\n--- the letter for {h.slug}: {rel} ---", file=out)
        print(h.text, end="", file=out)


def run_check(read: List[Home], record: dict, ledger: dict, out) -> None:
    reached = 0
    for h in read:
        if h.path is None:
            print(h.line(), file=out)
            continue
        try:
            c = cad.read_cadence(h.path, h.slug, record, clock.now())
            text = homes.read_regular(h.path / cad.CADENCE) or ""
        except (LinkRefused, ValueError, OSError) as e:
            print(f"{h.slug}: could not be read — its record ({e}); a later run tries again", file=out)
            continue
        if c.asked_once or "the pace letter reached" in text:
            reached += 1
            continue
        letters = [n for n in _inbox_names(h.path) if PACE_LETTER_MARK in n and not n.startswith(".")]
        if not letters:
            mine = ledger["homes"].get(h.slug) or {}
            note = f" (the ledger says delivered on {mine.get('day')})" if mine.get("status") == LANDED else ""
            print(f"{h.slug}: not reached — no pace letter in its home{note}", file=out)
            continue
        rel = f"inbox/{letters[0]}"
        try:
            seen = homes.read_regular(h.path / "inbox" / f".seen-{letters[0]}", limit=256)
        except (LinkRefused, ValueError):
            seen = ""
        if seen is None:
            why = f"{rel} waits unread"
        elif seen.startswith(homes.ROLLBACK_PREFIX):
            why = f"{rel} was set aside at rollback and is not yet shown"
        else:
            why = f"{rel} is marked seen, but its CADENCE.md has no reached line"
        print(f"{h.slug}: not reached — {why}", file=out)
    print(f"reached: {reached} of {len(read)} recipients", file=out)


def run_deliver(read: List[Home], switch: Switch, a, record: dict, ledger: dict, ledger_path: Path,
                base: str, now: _dt.datetime, out, err) -> int:
    print(switch.line(), file=out)
    why = switch.why_not(now)
    if why:
        print(f"nothing delivered: {why}", file=err)
        return REFUSED
    for h in read:
        if h.landed:
            mine = ledger["homes"][h.slug]
            print(f"{h.slug}: the ledger said {mine.get('status')} on {mine.get('day')}; its letter is on "
                  f"GitHub ({h.landed[:8]}), so it is recorded as pushed", file=out)
            mine["status"] = LANDED
            save_ledger(ledger_path, ledger)
    chosen = [h for h in read if h.status == "deliver"][:a.per_run]
    for h in read:
        if h.status != "deliver":
            print(h.line(), file=out)
    rel = letter.letter_path(clock.day(now))
    done = 0
    for k, h in enumerate(chosen):
        if k == 0:
            ledger["runs"].append({"day": clock.day(now), "at": clock.stamp(now), "yes_from_yu": a.yes_from_yu,
                                   "show_back": a.show_back == "on", "per_run": a.per_run})
            save_ledger(ledger_path, ledger)
        line = deliver_one(h, rel, ledger, ledger_path, base, now)
        done += line.startswith(f"{h.slug}: delivered")
        print(line, file=out)
    later = [h for h in read if h.status == "deliver" and h not in chosen]
    for h in later:
        print(f"{h.slug}: to deliver in a later run (--per-run {a.per_run}) — {h.group}", file=out)
    print(f"delivered {done} of {len(chosen)} to deliver this run", file=out)
    return OK


# -- the command line ----------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="citizen-pace-letter.py",
                                description="The pace letter, once (spec section 5).")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="the recipients after the delivery-time drops")
    mode.add_argument("--dry-run", action="store_true", help="print each letter this run would place")
    mode.add_argument("--deliver", action="store_true", help="place the letters (Yu's yes needed)")
    mode.add_argument("--check", action="store_true", help="the recipients not yet reached")
    p.add_argument("--per-run", type=int, metavar="N", help="place at most N letters in this run")
    p.add_argument("--yes-from-yu", metavar="YYYY-MM-DD", help="the day Yu said yes to deliver")
    p.add_argument("--show-back", choices=("on", "off"), help="the show-back setting Yu said yes to")
    p.add_argument("--record", help="the record file (default: citizens-pace-record.json)")
    p.add_argument("--roster", help="the roster (default: citizens-roster.txt)")
    p.add_argument("--ledger", help=f"the delivery ledger (default: ~/{LEDGER_REL})")
    return p


class Refusal(Exception):
    """Arguments that parse but must not run."""


def _check_args(a, record: dict, now: _dt.datetime) -> None:
    if a.per_run is not None:
        if not (a.dry_run or a.deliver):
            raise Refusal("--per-run goes with --dry-run and --deliver only")
        if a.per_run < 1:
            raise Refusal(f"--per-run is a whole number from 1, not {a.per_run}")
    if not a.deliver:
        if a.yes_from_yu is not None or a.show_back is not None:
            raise Refusal("--yes-from-yu and --show-back go with --deliver only")
        return
    if a.yes_from_yu is None:
        raise Refusal("--deliver needs --yes-from-yu <YYYY-MM-DD>, the day Yu said yes")
    if a.show_back is None:
        raise Refusal("--deliver needs --show-back on|off, the setting Yu said yes to")
    if not _valid_day(a.yes_from_yu):
        raise Refusal(f"--yes-from-yu is a day, YYYY-MM-DD, not {a.yes_from_yu!r}")
    if a.yes_from_yu > clock.day(now):
        raise Refusal(f"--yes-from-yu {a.yes_from_yu} is in the future")
    made = _day_of(record.get("made", ""))
    if made and a.yes_from_yu < made:
        raise Refusal(f"--yes-from-yu {a.yes_from_yu} is before the record was made ({made}); Yu's yes "
                      "to deliver is given with the record")
    committed = due.WAKE_CONFIRM_BEAT
    if (a.show_back == "on") != committed:
        raise Refusal(f"Yu's yes was for show-back {a.show_back}, but the committed WAKE_CONFIRM_BEAT is "
                      f"{committed} ({'on' if committed else 'off'}); the letters are worded from the "
                      "committed value")


def main(argv=None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    now = clock.now()
    try:
        record = records.load_record(Path(a.record) if a.record else None)
        if not record:
            raise Refusal(f"no record file at {a.record or records.default_record_path()}")
        roster = due.read_roster(Path(a.roster) if a.roster else DEFAULT_ROSTER)
        _check_args(a, record, now)
        ledger_path = Path(a.ledger) if a.ledger else default_ledger()
        ledger = load_ledger(ledger_path)
    except (Refusal, ValueError, LinkRefused, OSError) as e:
        print(f"citizen-pace-letter.py: {e}", file=err)
        return REFUSED
    if a.deliver and ran_on(ledger, clock.day(now)):
        print(f"a delivery run already happened today ({clock.day(now)}); this run does nothing — run "
              "again on a later day", file=out)
        return OK
    base = sync.remote_base()
    work = Path(tempfile.mkdtemp(prefix="pace-letter-"))
    try:
        print(_header(record, roster, base), file=out)
        read = read_all(record, roster, ledger, base, now, work)
        switch = Switch.of(read)
        if a.list:
            run_list(read, switch, out)
        elif a.dry_run:
            run_dry(read, switch, a.per_run, now, out)
        elif a.check:
            run_check(read, record, ledger, out)
        else:
            return run_deliver(read, switch, a, record, ledger, ledger_path, base, now, out, err)
        return OK
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
