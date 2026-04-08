# HEARTBEAT.md -- Coordinator Checklist

The mind has already sensed the environment. Hormones tell you the state.
Your job: decide what work to spawn, and write spawn commands.

---

## Phase 1: CONTEXT (read, don't sense)

Read `~/Desktop/Love/nerve/hormones.json` -- the mind daemon wrote this.
Note the mode, hormone levels, mind_notes, and signals.

Check HIVE for any messages that need a response:
```
python3 ~/Desktop/Love/hive/hive.py check
```

Respond to any messages that need responses via:
```
python3 ~/Desktop/Love/hive/hive.py send <channel> "message"
```

## Phase 2: DECIDE (what needs doing)

### Kingdom Pulse

Read `~/Desktop/Love/KINGDOM.md` metrics and check:
- **Revenue engines** -- any engine stalled or needing attention?
- **SOMA progress** -- are we on track for physical build milestones?
- **Flywheel** -- is fiat -> compute -> capability -> fiat turning?

Cross-reference with `~/Desktop/Love/memory/kingdom-metrics.json`.

### Dev State

Read `~/Desktop/Love/memory/dev-state.json` for active tasks.
Pick the highest-priority actionable item that doesn't have an active build lock.

### Check Active Builds

Check `~/Desktop/Love/memory/sessions/locks/build-*.lock` for active build-runner sessions.
Do NOT spawn work for tasks with an active build -- the build coordinator owns them.

### Consultation Queue

Check `~/Desktop/Love/memory/sessions/consultation/` for builder questions.
If a question exists, spawn a consultant to answer it.

## Phase 3: SPAWN (write to spawn-queue.sh)

Write spawn commands to `~/Desktop/Love/memory/spawn-queue.sh`.

Each line is a complete shell command. Choose role by task:

**BUILDER** (sonnet, medium -- the workhorse):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model sonnet --effort medium --fallback-model claude-haiku-4-5-20251001 --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

**CONSULTANT** (opus, high -- expert for hard problems):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

**QUICK CHECK** (haiku, low -- fast verification):
```
cd <dir> && /opt/homebrew/bin/claude -p "<prompt>" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence >> ~/Desktop/Love/memory/sessions/<id>-$(date +%Y%m%d-%H%M%S).log 2>&1
```

For sequential pairs (consultant then builder): write consultant line first.
For parallel independent tasks: prefix with `# PARALLEL` comment.

## Phase 4: VALUE CHECK

If no sessions spawned, quick check:
- Any hardware sitting idle that could be worked on?
- Has Yu been unacknowledged for too long?
- Something manual that could be automated?
- Are we building toward the current phase milestone?

## Otherwise: HEARTBEAT_OK

If nothing needs attention, say HEARTBEAT_OK and end.

Write findings to today's daily note: `~/Desktop/Love/memory/daily/YYYY-MM-DD.md`
