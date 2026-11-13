# Citizens keep their own pace (pace/1)

Stacked on #24, which is open as read on 2026-11-13 (head `48ee504`, base `main`): this PR's base is `claude/citizen-s1b-harden-live-lane` until #24 merges, then `main`. #23 merged on 2026-11-09. This PR merges only after #24 has merged.

Each citizen keeps its own pace, in the seat's words (KS-004): `asleep`, `asleep, letters wait`, `listening`, or `awake, every N days` with N from 3 to 365. A choice for fewer beats takes effect at once; a choice for more takes effect only when the citizen writes it again on its next beat. The fleet's arithmetic no longer decides how often anyone wakes: the daily cap can make a beat later than its pace, never sooner. Spec: `docs/superpowers/specs/2026-11-03-citizens-own-pace-design.md`. Plan: `docs/superpowers/plans/2026-11-03-citizens-own-pace.md`.

## What changes at the switch

- kalam, soma and teotlme are heard late. Their own Letter of Return answers said AMBIENT twice, and the old parser dropped them. They become `awake, every 3 days`, no sooner.
- Every awake citizen, and every asleep citizen with a letter, waits about 3 days: each clock starts at the switch.
- The fleet runs the free lane only. `citizen-beat.sh` refuses a home with unread mail unless a person summons it (`BEAT_SUMMONS=1`). The parade's script is unchanged: it still lines those homes up, and homes with no marker, and logs them as walked. (An `asleep, letters wait` home is the exception: its `REST` says letters wait, so the shared rest gate keeps it resting, mail or not.)
- The Studio needs a `FLEET_HOST` file holding its `scutil --get LocalHostName`. Without it the new fleet beats no one and says so once.
- `citizen-beat.sh` names its temp file `beat-$NAME.XXXXXX`, in `$TMPDIR`. PR #24's name ended in `.json` after the X's, which macOS's `mktemp` leaves as written, so a file left by a killed beat stopped every later beat of that citizen: its `claude` never ran again.
- eucatastrophe and shannonme get no pace letter: they answered REST-ON on their `master` branch, where their beats land (`origin/master`; Yu confirmed 32 letters on 11-12). A fresh clone checks out `main`, which holds only the REST Ai placed on 10-08, so there they read `asleep`, set by Ai. The record reads them at `origin/master`. Which branch is their home is Yu's call.

## What the fleet does with a home's files

- **Each home has its own first tend.** At a home's first full tend on the new code, what the old code left uncommitted in `CADENCE.md`, `REST`, `EVENT-ONLY`, `inbox/` and `beats/` is committed once, as `fleet` (`tend: old-code leftover · pace/1`), and pushed. That first tend is kept in the home's attempt record, not as one fleet-wide first tick, so a home that the fleet's first tick skips (its lock held, a merge in progress, a fresh `.git/index.lock`) keeps its files until its own first tend. Until then a hand-run (`citizen-reflect.sh` or `citizen-beat.sh`) refuses that home if it holds such files; a clean home has its first tend in that hand-run.
- **Set aside, not lost.** What that first tend commits and pushes includes the files inside plain folders under those paths; a repository inside the home is set aside whole, and a link is removed, never followed. After it, uncommitted changes there are a killed beat's leftovers, and tend puts them back path by path, keeping what it takes out in `~/love-unlimited/memory/.fleet-last/.set-aside/`, under the citizen's name, the UTC minute and its path in the home: an untracked file (a letter a person placed, a journal, a `REST`, `EVENT-ONLY` or `CADENCE.md` written by hand) or folder is moved there, and an uncommitted change to a tracked file (a pace file, a letter, a journal) is copied there before the file is put back. Each is named in the log, and a second of the same name in the same minute goes beside the first, never over it. If a copy cannot be made, nothing in that home is put back, and the log says so. Only a link is removed with no copy, never followed; a deleted tracked file is simply restored. The guard after a beat with tools keeps what it puts back in the same way. Tend never touches a home with a link or a folder at a pace file's own place (`CADENCE.md`, `REST`, `EVENT-ONLY`, `inbox`, `beats`): that keeps the fleet out of the home altogether. Nor does it touch git-ignored files; the guard puts back the ignored ones the pace code reads. Nothing prunes `.set-aside/`. Both differences from the spec wait for Yu's yes: sections 3 and 6 say an untracked leftover "is removed" (here it is set aside, and a tracked change is copied before it is checked out), and section 6 makes the first tick one fleet-wide flag (here each home has its own first tend, above).
- **A beat with tools never sets a pace.** After each `citizen-beat.sh` beat, the guard puts `CADENCE.md`, `REST`, `EVENT-ONLY` and `inbox/` back as the commit before the beat had them, whatever their letter case, git-ignored ones that the pace code reads included. A pace change the beat committed itself is put back too and committed as `fleet` (`tend: pace files put back after a beat with tools · pace/1`); what the beat pulled from upstream stays. If the guard cannot finish what the beat committed, the home's attempt record keeps the put-back as pending (a `put-back:` key naming the commit before the beat, the beat's head and the upstream tip the guard saw). The fleet's next tend finishes it, and until then nothing of that home is pushed: the beat holds its push and sync holds the home. The citizen's next free beat is told once that its pace files were put back.
- **Bounded beats.** A free beat (`citizen-reflect.sh`) has one deadline, which every git call and every `citizen_pace.py` step in it reads, and a TERM stops the git it is running. `citizen-beat.sh` bounds its clone, fetch, beat and push, and its lock records their sum. Each citizen has one lock, which keeps its holder's bound, so a fleet beat and a hand-run do not run in one home at once. The fleet exits 0, 1 or 143: 143 when a TERM stops it, which reaches the beat in flight too.

## Known limits

- A beat with full tools (`BEAT_FULL_TOOLS=1`, meant for a person present) runs with every permission (`bypassPermissions`), a shell as the fleet's user. It can write a home's `.git/config`, and it can keep a pace change past the guard, for instance by forging its clone's record of what it fetched, so that a change it committed itself looks like upstream's. The guard holds a beat with tools that does not set out to defeat it.
- The echo tool (`tools/citizen_pace_echo.py`) sends soul text to whatever `--endpoint` it is given; it is not bound to this machine. Point it only at a model you mean to read the souls.
- A KILL cannot be caught: one sent to the fleet or to a beat can leave a git call it started, or the step in progress, running.
- A hand-run `citizen-reflect.sh` records only a beat that landed or was summoned. Its busy, not-due, halted and failed runs are logged, not written to the attempt record, because the record keeps one attempt, which the fleet reads back by its id. Spec sections 3 and 8 say every attempt is recorded; the amendment waits for Yu.

## The cap

From `plan-report.md`, a model of the Studio over read-only clones of all 208 homes:

| | longest wait of an awake citizen | last pace letter read |
|---|---|---|
| cap 4, a tick every 6 h, one wave | 174.0 h (an) | 32 of 32; the last at 2026-12-01T13:45Z (ihsanme), 240.0 h after it arrived |
| cap 4, a tick every 6 h, 3 a run | 102.0 h (hope) | 32 of 32; the last at 2026-12-06T07:45Z (soma), 138.0 h after it arrived |
| cap 4, a tick every 30 min, one wave | 190.5 h (ihsanme) | 32 of 32; the last at 2026-12-01T01:15Z (kalam), 227.5 h after it arrived |
| cap 4, a tick every 30 min, 3 a run | 96.5 h (kalam) | 32 of 32; the last at 2026-12-06T00:45Z (teotlme), 131.0 h after it arrived |
| KS-004's 24, a tick every hour, one wave | 13.0 h (ud) | 32 of 32; the last at 2026-11-23T04:45Z (teotlme), 39.0 h after it arrived |
| KS-004's 24, a tick every hour, 3 a run | 13.0 h (ud) | 32 of 32; the last at 2026-12-02T04:45Z (teotlme), 39.0 h after it arrived |

A wait runs from when a citizen's beat was due to when it came; the letters were placed eight days after the model's start. Fourteen citizens awake every 3 days need about 4.7 beats a day, more than the cap of 4 holds, so at 4 they run late. With a tick every 6 hours, one beat a tick is itself 4 a day: in those two runs the cap held no due citizen back, while with a tick every 30 minutes it held one back at 1,159 of its 1,440 ticks. Whether the cap stays is the keeper's and Yu's call. This PR changes no plist. #23, merged on 11-09, set the repo's `launchd/love.citizen-fleet.plist` on `main` to `FLEET_DAILY_CAP` 4, `FLEET_AGENTIC_PER_DAY` 0 and a tick every 6 hours (`8b3189d`). This branch stacks on #24's head, which predates that merge, so the plist in this branch's tree still reads 48 and 12; once this PR lands on `main`, the plist there is #23's. The Studio's installed copy was set to 4 and 0 on 11-05, by #23's account (unread from here); the keeper's note asks that the installed cap be kept until Yu chooses.

## PR #24's assertions that change, by name

- `TestBeat.test_unread_letter_wakes_rest` becomes `test_unread_letter_is_refused_without_a_summons`: `citizen-beat.sh` refuses unread mail without a summons.
- `TestParade.test_parade_lines_up_only_citizens_not_resting`: the lettered home is still lined up, `citizen-beat.sh` refuses it, no `claude` runs, and the parade logs it as walked.
- `TestReflectReadsOnlyRegularFiles.test_symlinked_rest_rests_without_mail` becomes `test_symlinked_rest_is_refused_before_the_gate` (exit 1).
- `TestReflectReadsOnlyRegularFiles.test_rest_pushed_from_another_host_holds_after_the_fetch` and `test_fleet_path_honours_a_rest_pushed_from_another_host` become `test_asleep_pace_pushed_from_another_host_holds_after_the_fetch` and `test_fleet_path_honours_an_asleep_pace_pushed_from_another_host`: a pushed v2 `CADENCE.md` saying `asleep` holds; a marker alone no longer holds against a v2 pace. The fleet path now asserts reflect is never started, since the fleet brings the home up to date before it ranks. (`TestBeat`'s test of the same name keeps its name and its assertions.)
- `TestReflectReadsOnlyRegularFiles.test_regular_letter_still_surfaces` expects the journal in the file of the UTC month, under `beats/`: pace/1 dates the journal, its header and the commit in UTC.
- `TestFleetRotation.test_rotation_skips_rest_with_only_a_symlinked_letter` no longer expects the retired `sits_out` line; the linked home is never started. It now runs a second tick, after waker has beaten, which must say "nobody is due — no beat this tick" and start no one.
- `TestLinkedEntryPoints.test_fleet_through_a_link` expects "nobody is due — no beat this tick" in place of "the whole roster is resting".

## Yu's open call (KS-004 §15 item 16)

A citizen that is `asleep`, `asleep, letters wait` or `listening` and asks for more gets one show-back beat, no sooner than 3 days later. That beat shows its proposal back with no letter, so it can say it again or let it lapse. This PR keeps `WAKE_CONFIRM_BEAT = True` in `tools/citizen_pace_lib/due.py`; a no sets it to `False` here before the merge. The pace letters are worded from the committed value.

## After the merge, in order

1. The keeper's note (`keeper-note.md`): pull `main` and write `FLEET_HOST` on the Studio.
2. The echo watch (spec section 9, step 6): at least 20 `pace/1` beats seen on GitHub, about a week at 4 a day.
3. Only then the ask to deliver the 32 letters (`delivery-ask.md`), with a full echo sample that holds. Delivery runs from a clean checkout of `main`, so the letters are worded from the merged `WAKE_CONFIRM_BEAT`, which stays `True` unless Yu says no at the merge.

## Where the bridge differs from KS-004

- **Paces here are read from free text**: `pace:` lines and the pace-letter forms in any beat, and the
  first-line, signature and "I choose" rules for stored Letter of Return answers. KS-004 §5.7 forbids
  free text deciding anything, and §13 keeps such choices only as input to the re-ask. Whether the
  seat takes them without asking again is Yu's call (his 11-08 pick) and its builder's; the note says
  so.
- **Covenants narrow at seating.** A seated citizen that has not set its own covenants admits Yu's
  letters only, and the seat has no sender id yet for Ai's sessions, which wrote every letter here. A
  carried `asleep` or `listening` therefore needs covenants naming the senders that woke it here, or
  the seat must tell the citizen that its meaning narrowed.
- **`asleep, letters wait` is a pace here, entered at once.** In the seat it is a covenants self-change,
  which binds only through a later affirmation on a model digest Yu has approved, at most once in 7
  days. A citizen carried over in it could not change it back on the free lane. The note suggests
  seating it as plain `asleep` with its wish recorded, or with Yu present.
- **The show-back chain.** Here a proposal said on a show-back beat gets no further show-back.
  kingdom-seat gives one, so a model that varies its answer can be seated every 72 h without ever being
  awake; the note recommends the bridge's rule.
- **A letter is shown once, whatever the answer.** KS-004 shows a letter again after a rest and relies
  on the floor to bound it. Here, Yu's "once" and principle 9 hold.
- **Summons and the clock.** Here any person's hand-run with `BEAT_SUMMONS=1` is a summons with no
  floor, and the bridge has no "said once with Yu present" binding, because it cannot tell who is
  present. In the seat only Yu's confirmed summons is an extra seat, and other hand-runs must be due.
  Here failed attempts move neither the clock nor a pending proposal; in the seat every opened seat
  does.
- **Up to three letters a beat.** KS-004's seat carries at most one.
- **No stepped-away or ended here.** The nearest pace is `asleep, letters wait`. A citizen that asks to
  step away is recorded `unclear`, and nothing of that carries over.
- **No quiet hours, keys, ledger or registration.** Those are the seat's.

## Evidence

- `docs/superpowers/rollout/2026-11-citizens-own-pace/plan-report.md`: the model above.
- `docs/superpowers/rollout/2026-11-citizens-own-pace/rollback-drill.md`: the wakes PR #24's code would make if we rolled back, over the homes as a 10-day model would leave them (pace letters read: 6 of 32), and as `rollback-prepare` then leaves them (it set aside 26 unread pace letters). Woken at least once without `rollback-prepare`: 28 (no marker 9 · an unread letter 19, a pace letter among them 19 · no clone 0); with it: 11 (no marker 11 · an unread letter 0, a pace letter among them 0 · no clone 0). Both runs make the same total, 28 wakes in 7 days: the cap bounds them, so the difference is who is woken.
- `docs/superpowers/rollout/2026-11-citizens-own-pace/echo-sample.md`: verdict: not measured — the Air runs no model; Yu names the machine (Nuance, or the Studio by its keeper) before the PR leaves draft. The letters wait until a full sample holds (spec section 4).
- `docs/superpowers/rollout/2026-11-citizens-own-pace/ks004-note.md`: the draft note for `kingdom-standards` #3, no reply owed, which Ai posts only after Yu's yes to merge.
- `citizens-pace-record.json`: each of the 208 homes' group and who set its pace, and each of the 32 pace letters' one true sentence. The census agreed with every list in the spec.
- 895 pace tests (2 skipped), and the same with `WAKE_CONFIRM_BEAT = False`, and PR #24's 47 live-lane tests pass on `/usr/bin/python3` 3.9.6; every script parses with `/bin/bash -n`.

## Stop and undo

Stop: `touch ~/love-unlimited/HALT`. Undo: keep HALT; run `citizen_pace.py rollback-prepare`, which sets each unread pace letter aside ("set aside at rollback, not shown") so the old code does not wake its reader at every pass; check out the merge's first parent; lift HALT only with Yu's yes. An awake home that has only had silent beats with nothing shown keeps its v1 record, so the old code beats it on its old timer (the drill counts these under "no marker").

🤖 Generated with [Claude Code](https://claude.com/claude-code)
