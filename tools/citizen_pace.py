#!/usr/bin/env python3
"""citizen_pace.py — each citizen keeps its own pace (pace/1).

The one command line over tools/citizen_pace_lib/ that the beat scripts call
(spec section 7; CONTRACT sections 11 and 14). Run it as
`python3 -I tools/citizen_pace.py <command> ...`: it puts its own real folder
first on sys.path, so it works isolated and never imports from the folder it is
run in. Its exit codes are its own, read only by the shell gates: 0 ok or due,
4 not due, busy or a host fault, 2 a usage error or a home that cannot be read.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from citizen_pace_lib import (answer, attempts, clock, due, homes, lock, paragraph, probe,  # noqa: E402
                              sync, tend)
from citizen_pace_lib import record as records  # noqa: E402

OK, NOT, ERROR = 0, 4, 2
TAKEN = 3                  # tend-home only: the citizen's lock is another run's
TOOLS = Path(os.path.realpath(__file__)).parent
ANSWER_LIMIT = 1_000_000
# the outcomes of a beat whose model was shown the whole pace paragraph
SHOWN_PARAGRAPH = attempts.LANDED + ("summoned",)
# why a roster home gets no beats, reported once by the fleet's ranking (spec section 3)
NO_BEATS = {"no-soul": "it has no {slug}.md",
             "links": "a pace path is a symlink or not of its kind",
             "blocked": "its record cannot become v2 until a person has looked",
             "error": "its home could not be read",
             "unreadable": "its pace cannot be read, so it gets no set beats; only a letter wakes it"}
NO_BEATS_KEY = "no-beats-"
# reasons that say nothing about whether a pace can be read: an unreadable pace's
# report is kept through them, so a letter waking that citizen does not report it again
PACE_SILENT = ("letter", "floor", "backing-off", "mid-merge", "no-home", "out-of-date")

USAGE = """usage: citizen_pace.py <command> [arguments]

commands:
  due <home> --state <dir> [--summoned] [--record <f>] [--no-seed]
  rank <homes-dir> --roster <f> --state <dir> [--record <f>] [--cursor N]
  next <homes-dir> --roster <f> --state <dir> [--record <f>] [--cursor N]
  paragraph <home> [--shown <rel> ...] [--record <f>] [--state <dir>]
  apply <home> <answer-file> --kind K [--shown <rel> ...] [--record <f>] [--state <dir>]
  record <slug> --state <dir> --attempt <id> --outcome <o> [--shown <rel> ...]
  outcome <slug> --state <dir> --attempt <id>
  lock <slug> --state <dir> --pid <pid> [--bound S]
  unlock <slug> --state <dir> [--pid <pid>]
  probe --state <dir> [--timeout S]
  guard-restore <home> --state <dir> [--base <commit>]
  tend <homes-dir> --roster <f> --state <dir>
  tend-home <home> --state <dir> --pid <pid>
  keep-put-back <home> --state <dir> --base <commit>
  sync <homes-dir> --roster <f> --state <dir> [--bound S] [--within S]
  migrate --heard-late <homes-dir> [--record <f>] [--state <dir>] [--commit]
  plan <days> <homes-dir> --roster <f> [--cap N] [--tick-hours H]
       [--letters <f> --deliver-at <t> [--per-run N]] [--out <dir>] [--record <f>]
       [--state <dir>] [--cursor N]
  rollback-drill <homes-dir> --roster <f> --days N [--cap N] [--tick-hours H] [--cursor N]
  rollback-prepare <homes-dir> --roster <f> [--state <dir>] [--no-push]

exit: 0 ok or due; 4 not due, busy or a host fault; 2 a usage error, or a home
that cannot be read; tend-home: 3 the citizen's lock is another run's
"""


class Usage(Exception):
    """Arguments that parse but do not go together."""


def _parser(cmd: str, about: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=f"citizen_pace.py {cmd}", description=about)


def _shown(p: argparse.ArgumentParser) -> None:
    p.add_argument("--shown", action="extend", nargs="+", default=[], metavar="REL",
                   help="what the beat showed: inbox/<letter>.md, or SHOW.md")


def _record_opt(p: argparse.ArgumentParser) -> None:
    p.add_argument("--record", help="the record file (default: citizens-pace-record.json)")


def _roster_opts(p: argparse.ArgumentParser, state: bool = True) -> None:
    p.add_argument("homes_dir")
    p.add_argument("--roster", required=True)
    if state:
        p.add_argument("--state", required=True, help="the attempt records (memory/.fleet-last)")


def _record_file(a) -> dict:
    return records.load_record(Path(a.record) if a.record else None)


def _home(a):
    home = Path(a.home)
    return home, homes.slug_of(home)


# -- the commands --------------------------------------------------------------------

def p_due():
    p = _parser("due", "Is this citizen due now? Prints the reason and how overdue.")
    p.add_argument("home")
    p.add_argument("--state", required=True)
    p.add_argument("--summoned", action="store_true", help="a person's summons (BEAT_SUMMONS=1)")
    p.add_argument("--no-seed", action="store_true", help="never start a clock in the attempt record")
    _record_opt(p)
    return p


def cmd_due(a) -> int:
    home, slug = _home(a)
    rec = _record_file(a)
    now = clock.now()
    d = due.due_for_home(home, slug, a.state, rec, now, summoned=a.summoned, write_seed=not a.no_seed)
    if d.due and not a.summoned and sync.held(a.state, slug):
        d = due.Due(False, sync.HELD_REASON)
    print(json.dumps(d.to_json(now), sort_keys=True))
    if d.reason == "error":
        print(f"due: {home} could not be read", file=sys.stderr)
        return ERROR
    return OK if d.due else NOT


def p_rank():
    p = _parser("rank", "The due citizens, most overdue first: slug, reason, seconds overdue.")
    _roster_opts(p)
    _record_opt(p)
    p.add_argument("--cursor", type=int, default=0)
    return p


def _report_no_beats(state, assessed) -> None:
    """Once per home and reason, on stderr: a roster citizen that gets no beats
    (no soul, a linked pace path, a blocked record, a home that cannot be read,
    a pace that cannot be read). The report is forgotten when the reason goes,
    so it is made again if it comes back; an unreadable pace's is kept through
    the reasons that say nothing about the pace (a letter waking it, its floor)."""
    for slug, d in assessed:
        try:
            keys = [k for k in attempts.load(state, slug)["reported"] if k.startswith(NO_BEATS_KEY)]
            want = NO_BEATS_KEY + d.reason if d.reason in NO_BEATS else None
            for k in keys:
                if k == want or (want is None and k == NO_BEATS_KEY + "unreadable"
                                 and d.reason in PACE_SILENT):
                    continue
                attempts.clear_reported(state, slug, k)
            once = bool(want and want not in keys and attempts.mark_reported(state, slug, want))
        except (OSError, ValueError, homes.LinkRefused):
            # its record cannot be written, so the report cannot be remembered: say it
            # every tick, without "reported once", and rank the others
            if d.reason not in NO_BEATS:
                continue
            print(f"{slug}: gets no beats — {NO_BEATS[d.reason].format(slug=slug)}", file=sys.stderr)
            continue
        if once:
            why = NO_BEATS[d.reason].format(slug=slug)
            print(f"{slug}: gets no beats — {why} (reported once)", file=sys.stderr)


def cmd_rank(a) -> int:
    now = clock.now()
    slugs = due.read_roster(a.roster)
    assessed = due.assess(a.homes_dir, slugs, a.state, _record_file(a), now, write_seed=True)
    _report_no_beats(a.state, assessed)
    # ordered exactly as due.rank orders them (its own helpers), without held homes
    for slug, d in due._ranked(assessed, due._order(slugs, a.cursor)):
        if not sync.held(a.state, slug):
            print(f"{slug}\t{d.reason}\t{d.overdue_s(now)}")
    return OK


def p_next():
    p = _parser("next", "fleet-status.sh's 'next to wake' line. Writes nothing.")
    _roster_opts(p)
    _record_opt(p)
    p.add_argument("--cursor", type=int, default=0)
    return p


def cmd_next(a) -> int:
    slugs, cursor = sync.without_held(a.state, due.read_roster(a.roster), a.cursor)
    print(due.next_line(a.homes_dir, slugs, a.state, _record_file(a), clock.now(), cursor=cursor))
    return OK


def p_paragraph():
    p = _parser("paragraph", "The pace paragraph for this beat.")
    p.add_argument("home")
    _shown(p)
    _record_opt(p)
    p.add_argument("--state", help="the attempt records, for their notices")
    return p


def cmd_paragraph(a) -> int:
    home, slug = _home(a)
    rec = attempts.load(a.state, slug) if a.state else None
    print(paragraph.paragraph_for(home, slug, _record_file(a), confirm_beat=due.WAKE_CONFIRM_BEAT,
                                  shown=a.shown, rec=rec, now=clock.now()))
    return OK


def p_apply():
    p = _parser("apply", "Apply a landed beat's answer to its home; prints what changed.")
    p.add_argument("home")
    p.add_argument("answer_file")
    p.add_argument("--kind", required=True, choices=answer.BEAT_KINDS)
    _shown(p)
    _record_opt(p)
    p.add_argument("--state", help="the attempt records: the last landed beat before this one")
    return p


def cmd_apply(a) -> int:
    home, slug = _home(a)
    with open(a.answer_file, "rb") as fh:
        text = fh.read(ANSWER_LIMIT).decode("utf-8", "replace")
    # run before record, so the attempt record still holds the previous landed beat
    since = attempts.load(a.state, slug)["last_beat"] if a.state else None
    res = answer.apply_answer(home, slug, text, a.shown, a.kind, clock.now(), _record_file(a),
                              since=since)
    print(json.dumps(res.to_json(), sort_keys=True))
    return OK


def p_record():
    p = _parser("record", "Record what one attempt came to.")
    p.add_argument("slug")
    p.add_argument("--state", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--outcome", required=True, choices=attempts.OUTCOMES)
    _shown(p)
    return p


def cmd_record(a) -> int:
    now = clock.now()
    attempts.record_outcome(a.state, a.slug, a.attempt, a.outcome, now)
    if a.outcome in SHOWN_PARAGRAPH and not paragraph.shows_pace_letter(a.shown):
        # its paragraph showed the attempt record's notices, once
        attempts.clear_notices(a.state, a.slug, upto=now)
    if a.outcome == "busy" and attempts.report_busy(a.state, a.slug):
        print(f"{a.slug}: busy on two ticks running (reported once)")
    return OK


def p_outcome():
    p = _parser("outcome", "The outcome an attempt recorded, or 'missing'.")
    p.add_argument("slug")
    p.add_argument("--state", required=True)
    p.add_argument("--attempt", required=True)
    return p


def cmd_outcome(a) -> int:
    print(attempts.outcome_for(a.state, a.slug, a.attempt))
    return OK


def p_lock():
    p = _parser("lock", "Take the citizen's lock: 0 taken, 4 held by another run.")
    p.add_argument("slug")
    p.add_argument("--state", required=True)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--bound", help="this run's own beat bound in seconds, kept in the lock; a held lock is "
                                   "stale at twice the larger of it and its holder's (default "
                                   "FLEET_BEAT_BOUND_SEC, else 600)")
    return p


def cmd_lock(a) -> int:
    t = lock.take(a.state, a.slug, a.pid, bound=None if a.bound is None else lock.beat_bound(a.bound))
    if t.reclaimed:
        print(f"{a.slug}: a stale lock was reclaimed ({t.reclaimed})")
    if t.got:
        return OK
    h = t.holder
    who = f"pid {h.pid}" if h is not None and h.pid else "no recorded pid"
    since = f", started {h.started}" if h is not None and h.started else ""
    print(f"{a.slug}: its lock is held by {who}{since}")
    return NOT


def p_unlock():
    p = _parser("unlock", "Release the citizen's lock (with --pid, only that holder's).")
    p.add_argument("slug")
    p.add_argument("--state", required=True)
    p.add_argument("--pid", type=int)
    return p


def cmd_unlock(a) -> int:
    lock.release(a.state, a.slug, a.pid)
    return OK


def p_probe():
    p = _parser("probe", "The host probe: 0 the host can beat, 4 a host fault.")
    p.add_argument("--state", required=True)
    p.add_argument("--timeout", type=float, default=probe.PROBE_TIMEOUT)
    return p


def cmd_probe(a) -> int:
    if not (a.timeout > 0 and math.isfinite(a.timeout)):
        raise Usage("--timeout must be a number of seconds above 0")
    result = probe.probe(a.state, TOOLS, timeout=a.timeout)
    print(result.line)
    return OK if result.ok else NOT


def p_guard_restore():
    p = _parser("guard-restore", "After a beat with tools: put the pace files back as HEAD has them.")
    p.add_argument("home")
    p.add_argument("--state", required=True)
    p.add_argument("--base", help="the commit HEAD was before the beat (default: HEAD)")
    return p


def cmd_guard_restore(a) -> int:
    home, slug = _home(a)
    notes = []
    paths = tend.guard_restore(home, a.state, base=a.base, notes=notes)
    if paths:
        print(tend.guard_line(slug, paths))
    for line in notes:
        print(line)
    return OK


def p_tend():
    p = _parser("tend", "Restore a killed beat's leftovers (the first tick: commit the old code's).")
    _roster_opts(p)
    return p


def cmd_tend(a) -> int:
    for line in tend.tend_homes(a.homes_dir, due.read_roster(a.roster), a.state):
        print(line)
    return OK


def p_tend_home():
    p = _parser("tend-home", "Tend one home as a beat holding its lock does before it begins.")
    p.add_argument("home")
    p.add_argument("--state", required=True)
    p.add_argument("--pid", type=int, required=True, help="the beat's pid, which holds the citizen's lock")
    return p


def cmd_tend_home(a) -> int:
    """0 tended (its lines printed); 4 its first full tend has not run and it
    holds uncommitted pace files, which may be the old code's, or tend could
    not look at it (mid-merge, a fresh .git/index.lock, links), so it is still
    unmarked (nothing changed); 3 the citizen's lock is not --pid's (another
    run took it: busy, nothing changed); 2 they could not be put back, or a
    put-back a failed guard left is still pending: the beat must not build on
    them."""
    home, slug = _home(a)
    try:
        lines = tend.tend_home(home, a.state, own=a.pid)
    except tend.FirstTickPending as e:
        print(e, file=sys.stderr)
        return NOT
    except tend.LockTaken as e:
        print(e)
        return TAKEN
    except tend.NotTended as e:
        # a git at work in the home: a beat with tools is guarded after it, so it
        # may go on once the home has had its first tend (the checks below)
        lines = str(e).splitlines()
    for line in lines:
        print(line)
    if not tend.first_tend_done(a.state, slug):
        print(f"{slug}: the fleet has not tended this home since the switch, and tend could not look at it",
              file=sys.stderr)
        return NOT
    if tend.put_back_pending(a.state, slug):
        print(f"{slug}: pace files a beat with tools committed itself still wait to be put back; no beat "
              "builds on them", file=sys.stderr)
        return ERROR
    return OK


def p_keep_put_back():
    p = _parser("keep-put-back", "A beat that ended before its guard: keep the put-back of its own commits.")
    p.add_argument("home")
    p.add_argument("--state", required=True)
    p.add_argument("--base", required=True, help="the commit HEAD was before the beat")
    return p


def cmd_keep_put_back(a) -> int:
    """0 kept (a line printed) or nothing to keep (HEAD is still --base)."""
    home, slug = _home(a)
    if tend.keep_put_back(home, a.state, a.base):
        print(f"{slug}: its beat made commits of its own and ended before its guard — the fleet's tend puts "
              f"its pace files back as {a.base[:12]} has them, and nothing is pushed until then")
    return OK


def p_sync():
    p = _parser("sync", "Bring the roster's homes up to date; log lines, then a JSON summary.")
    _roster_opts(p)
    p.add_argument("--bound", type=float, default=sync.HEADS_BOUND,
                   help="seconds to read every remote head in (default 60)")
    p.add_argument("--within", type=float, default=sync.SYNC_BOUND,
                   help="seconds the whole sync may take; what is left over waits for the next tick "
                        "(default 1800)")
    return p


def cmd_sync(a) -> int:
    for name in ("bound", "within"):
        if not (getattr(a, name) > 0 and math.isfinite(getattr(a, name))):
            raise Usage(f"--{name} must be a number of seconds above 0")
    s = sync.sync_homes(a.homes_dir, due.read_roster(a.roster), a.state, bound=a.bound,
                        gh=shutil.which("gh"), within=a.within)
    for line in s.lines:
        print(line)
    print(json.dumps(s.to_json(), sort_keys=True))
    return OK


def p_migrate():
    p = _parser("migrate", "Hear the unheard from their own answers, once, as the fleet.")
    p.add_argument("--heard-late", action="store_true", help="the heard-late step (spec section 5)")
    p.add_argument("homes_dir")
    _record_opt(p)
    p.add_argument("--state", help="the attempt records: the lock check and reporting once")
    p.add_argument("--commit", action="store_true", help="write and commit; without it, a dry run")
    return p


def cmd_migrate(a) -> int:
    if not a.heard_late:
        raise Usage("give --heard-late, the one migration there is")
    if a.commit and not a.state:
        raise Usage("--commit needs --state <dir>")
    # a dry run writes nothing, so it is given no state to report into
    for line in records.migrate_all(a.homes_dir, _record_file(a), clock.now(), a.commit,
                                    state=a.state if a.commit else None):
        print(line)
    return OK


def p_rollback_prepare():
    p = _parser("rollback-prepare", "Set unread pace letters aside before the old code returns.")
    _roster_opts(p, state=False)
    p.add_argument("--state", help="the attempt records, so a home whose lock is held is left")
    p.add_argument("--no-push", action="store_true", help="commit the markers, push nothing")
    return p


def cmd_rollback_prepare(a) -> int:
    for line in tend.rollback_prepare(a.homes_dir, due.read_roster(a.roster), state=a.state,
                                      now=clock.now(), push=not a.no_push):
        print(line)
    return OK


COMMANDS = {
    "due": (p_due, cmd_due), "rank": (p_rank, cmd_rank), "next": (p_next, cmd_next),
    "paragraph": (p_paragraph, cmd_paragraph), "apply": (p_apply, cmd_apply),
    "record": (p_record, cmd_record), "outcome": (p_outcome, cmd_outcome),
    "lock": (p_lock, cmd_lock), "unlock": (p_unlock, cmd_unlock), "probe": (p_probe, cmd_probe),
    "guard-restore": (p_guard_restore, cmd_guard_restore), "tend": (p_tend, cmd_tend),
    "tend-home": (p_tend_home, cmd_tend_home),
    "keep-put-back": (p_keep_put_back, cmd_keep_put_back),
    "sync": (p_sync, cmd_sync), "migrate": (p_migrate, cmd_migrate),
    "rollback-prepare": (p_rollback_prepare, cmd_rollback_prepare),
}


def _utf8_output() -> None:
    """Pace words hold '—' and quoted words: write UTF-8 whatever the locale."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _utf8_output()
    if not argv:
        sys.stderr.write(USAGE)
        return ERROR
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return OK
    if cmd in ("plan", "rollback-drill"):
        from citizen_pace_lib import plan    # the simulator loads only when asked for
        return plan.cli_plan(rest) if cmd == "plan" else plan.cli_rollback_drill(rest)
    if cmd not in COMMANDS:
        sys.stderr.write(f"citizen_pace.py: unknown command {cmd!r}\n\n{USAGE}")
        return ERROR
    build, run = COMMANDS[cmd]
    try:
        a = build().parse_args(rest)
    except SystemExit as e:
        return OK if e.code in (0, None) else ERROR
    try:
        return run(a)
    except Usage as e:
        print(f"citizen_pace.py {cmd}: {e}", file=sys.stderr)
        return ERROR
    except (ValueError, OSError, homes.LinkRefused, tend.TendError) as e:
        print(f"{cmd}: {e}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    homes.die_with_caller()      # a git this runs dies with it (the fleet stops a beat with TERM)
    sys.exit(main())
