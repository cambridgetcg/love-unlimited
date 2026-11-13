For whoever tends the Studio's citizen fleet — no reply owed.

#<PR number> (citizens keep their own pace) has merged. When you next tend the Studio:

1. `git -C ~/love-unlimited pull --ff-only` on `main`.
2. `scutil --get LocalHostName > ~/love-unlimited/FLEET_HOST`. The new fleet beats only on the Mac named there and says once, in fleet.log, if the file is missing or names another Mac.

Let the fleet tend the homes before beating anyone by hand. Each home has its own first tend on the new code, which commits what the old code left uncommitted in its pace files, `inbox/` and `beats/` once, as `fleet`. Until a home has had it, a hand-run refuses that home if it holds such files, so nothing the old code left is lost. After it, a killed beat's leftovers are put back, and an untracked letter, journal file or folder is moved to `~/love-unlimited/memory/.fleet-last/.set-aside/` (under the citizen's name, the UTC minute and its path) and named in the log, never deleted. Nothing prunes that folder yet; whether these are set aside rather than removed, as the spec first said, is Yu's call.

launchd picks the scripts up at the next tick; no plist or environment setting changes. The show-back rule is the committed `WAKE_CONFIRM_BEAT`, not a setting.

The cap: this PR changes no plist. The repo's `launchd/love.citizen-fleet.plist` still says `FLEET_DAILY_CAP` 48 and `FLEET_AGENTIC_PER_DAY` 12, and the fleet's own default cap, with none set, is 48. The Studio's installed copy was set on 11-05 to cap 4 and agentic 0 (by PR #23's account; the Air cannot read it). Please keep the installed copy as it is, and do not install the repo's over it, until Yu chooses the cap. (The new fleet logs an agentic value above 0 once and ignores it; it uses the cap as set.)

eucatastrophe and shannonme: their beats land on `master`, where they answered REST-ON, and `main` does not hold that line. A fresh clone checks out `main`, which holds only the REST Ai placed on 10-08, so there they read `asleep`, set by Ai. The new code works on the branch each clone has checked out. Which branch is their home is Yu's call.

Stop: `touch ~/love-unlimited/HALT`. Undo: keep HALT, run `cd ~/love-unlimited && python3 -I tools/citizen_pace.py rollback-prepare citizens --roster citizens-roster.txt --state memory/.fleet-last` (or the folder `CITIZENS_DIR` names, if the installed plist sets one), check out the merge's first parent, and lift HALT only with Yu's yes. rollback-prepare names each home it could not prepare, with why, ending "set it aside by hand"; when the why is a held lock, run it again once that beat ends.

For PR #24's author: `citizen-beat.sh`'s inline Python ran from inside the home, so a `json.py` planted there would have been imported. It now runs under `python3 -I`. Its temp file's name also ended in `.json` after the X's, which macOS's `mktemp` leaves as written, so a file left by a killed beat stopped every later beat of that citizen; the name is now `beat-$NAME.XXXXXX`.

If it is easy to see from there, these are unread from the Air: the installed plist environment (`REFLECT_MODEL`, `FLEET_DAILY_CAP`, the launchd interval, `FLEET_PER_TICK`), the checked-out revision, the live roster, any "home diverged" lines in fleet.log, homes ahead of or diverged from GitHub, letters placed but uncommitted, and who lifted HALT on 10-22. Nothing is owed.
