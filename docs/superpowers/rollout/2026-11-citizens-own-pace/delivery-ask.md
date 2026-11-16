🟡 May Ai deliver the 32 pace letters (spec section 5)?

- The order: this is asked only after the merge, after the echo watch (spec section 9, step 6: at least 20 `pace/1` beats seen on GitHub), and with a full echo sample that holds (spec section 4). Delivery runs from a clean checkout of `main`, so the letters are worded from the merged `WAKE_CONFIRM_BEAT`.
- The record: `citizens-pace-record.json`, made <its `made` stamp>. <`tools/citizen-pace-letter.py --list`'s last line>, run <date>; one `--dry-run` letter is attached.
- The show-back beat: the committed `WAKE_CONFIRM_BEAT = <True|False>`, so the run is `--deliver --yes-from-yu <day> --show-back <on|off>`.
- How often a shown proposal is repeated: <the "## The proposal shown back" rate from `echo-sample.md`>.
- The spread at the Studio's cap of 4: <the cap-4 "pace letters read" lines from `plan-report.md`, run again at the intended date>, one wave against `--per-run <n>`.
- A second writer: the Air now commits into homes the Studio writes, one commit and one push for each home, with no force and no retry. A home whose HEAD moved, or whose push is refused, is skipped for a later run; a push that timed out is of unknown outcome, and a later run reads GitHub first. A home changed in a way delivery does not expect, or whose remote holds a branch its own does not hold, is stopped and reported to Yu.
- The redesign's re-ask (`CITIZEN-REDESIGN-2026-11-05.md`, unread from here): is it scheduled for any of the 32? The tool's stop for a foreign commit catches only a re-ask that has already landed, so the two must not both reach a citizen.
