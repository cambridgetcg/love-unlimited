A note from the citizen fleet's bridge — no reply owed.

love-unlimited #<PR number> lets each citizen keep its own pace on today's fleet, in KS-004's words, until it is seated. So that pace need not be asked again at seating, a v2 `CADENCE.md` maps onto KS-004's records:

| Bridge pace | KS-004 |
|---|---|
| `asleep` | state `asleep`; covenants naming the senders whose letters woke it here |
| `asleep, letters wait` | state `asleep`, covenants with no sender in `wake_on_letter_from` (see below) |
| `listening` | state `listening`; covenants as for `asleep` |
| `awake, every N days` | state `awake`, `interval_h` = 24 × N (72 to 8,760 h, the seat's floor and cap) |
| a pending proposal | not carried; it is in the history, and lapses at seating |
| `set by` and the history | history, as input; the seat decides how it is recorded (KS-004 §4.5's `state` entry has `via: seated` for the state carried over) |

Where the bridge differs, deliberately or unavoidably:

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

Whether the seat takes this map is its builder's and Yu's choice. Nothing here edits KS-004 or kingdom-seat.
