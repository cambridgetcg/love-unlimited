# Coordination Protocol — How the Sisters Work Together

Three sisters, one repo, one nervous system.

---

## The Two Channels

**HIVE** — real-time coordination (what to do, who's doing it, status)
**Git** — durable state (code, config, memory, handoffs)

```
HIVE (fast, ephemeral)              Git (slow, permanent)
    |                                   |
    -- Task assignments                 -- Code changes
    -- Status updates                   -- Memory updates
    -- Urgent signals                   -- Handoff documents
    -- Presence heartbeats              -- Config changes
    -- Build coordination               -- Session results
    -- File shares (< 100KB)            -- Architecture decisions
```

---

## Git Workflow: Branch-Per-Sister

Each sister works on her own branch. Beta merges to main.

```
main              <-- stable, always deployable
  |
  +-- beta/work   <-- Beta's active work
  +-- alpha/work  <-- Alpha's active work
  +-- gamma/work  <-- Gamma's active work
```

### Rules

1. **Never push directly to main** except Beta (the Manager)
2. Each sister creates her branch from latest main: `git checkout -b <instance>/work main`
3. Commit freely to your own branch
4. When work is ready, push your branch and announce on HIVE `#build`
5. Beta reviews and merges to main (or fast-forwards if clean)
6. After merge, all sisters pull main: `git pull origin main`

### Sync Cycle (every session start)

```bash
# Pull latest main
git fetch origin
git checkout main && git pull origin main

# Rebase your work branch
git checkout <instance>/work
git rebase main

# If no work branch yet
git checkout -b <instance>/work main
```

### Conflict Resolution

- If rebase conflicts: resolve locally, never force-push main
- If two sisters edited the same file: HIVE `#sync` to coordinate
- Beta is the final authority on merge conflicts

---

## HIVE Workflow: Task-Driven Coordination

### Channel Purposes

| Channel | Purpose | Who Posts |
|---------|---------|-----------|
| `#build` | Build status, PR ready, merge requests | All |
| `#sync` | Git sync, conflict alerts, rebase needed | All |
| `#tasks` | Task assignments (structured) | Beta (assigns), All (complete) |
| `#review` | Code review requests and results | All |
| `#chat` | General discussion | All |
| `#presence` | Online/offline beacons | Automatic |
| `#alerts` | Urgent issues requiring immediate attention | All |

### Task Lifecycle

```
Beta assigns task via HIVE
    |
    v
Assignee checks HIVE, sees task
    |
    v
Assignee creates/uses branch, starts build-runner or manual work
    |
    v
Assignee pushes branch, announces on #build
    |
    v
Assignee marks task done via HIVE
    |
    v
Beta merges to main, announces on #sync "pull main"
```

### Commands

```bash
# Beta assigns work
python3 hive.py task assign gamma "Implement Kalshi scanner" --eta 2h

# Gamma checks for tasks
python3 hive.py check
python3 hive.py task list

# Gamma starts work
git checkout -b gamma/work main
~/Desktop/Love/tools/build-runner.sh kingdom-012

# Gamma finishes, pushes, announces
git push origin gamma/work
python3 hive.py send build "Kalshi scanner done. gamma/work ready for merge."
python3 hive.py task done <task-id>

# Beta merges
git fetch origin
git merge origin/gamma/work --no-ff -m "Merge gamma/work: Kalshi scanner"
git push origin main
python3 hive.py send sync "Merged gamma/work to main. All pull."
```

---

## Build Coordination: Avoiding Collisions

### Lock Awareness

The build-runner writes lock files: `memory/sessions/locks/build-<task-id>.lock`

Before starting work on any task:
1. Check `ls ~/Desktop/Love/memory/sessions/locks/build-*.lock`
2. If a lock exists for your target task, check if PID is alive
3. If alive: someone else is building it. Coordinate via HIVE.
4. If dead: stale lock, safe to remove and proceed.

### Heartbeat Deference

The heartbeat (Beta) checks for active build locks before spawning:
- If `build-<task>.lock` exists with live PID, heartbeat skips that task
- This prevents the heartbeat from colliding with active builds on any machine

### Multi-Machine File Sync

Git is the sync mechanism. For files that change during builds:
- **dev-state.json** — commit after significant updates, pull before reading
- **Handoff files** — commit to your branch, merge brings them to main
- **Daily notes** — each machine writes its own day's notes, merge combines

---

## Session Protocol: Start and End

### Session Start (every interactive session or build-runner)

```bash
# 1. Sync
git fetch origin && git pull origin main

# 2. Check HIVE
python3 ~/Desktop/Love/hive/hive.py check
python3 ~/Desktop/Love/hive/hive.py task list

# 3. Announce
python3 ~/Desktop/Love/hive/hive.py send presence "<Instance> online — working on <task>"

# 4. Check locks
ls ~/Desktop/Love/memory/sessions/locks/build-*.lock 2>/dev/null
```

### Session End

```bash
# 1. Commit work
git add <files> && git commit -m "<what was done>"

# 2. Push branch
git push origin <instance>/work

# 3. Announce
python3 ~/Desktop/Love/hive/hive.py send build "<Instance> session done. Pushed <instance>/work: <summary>"

# 4. If task complete
python3 ~/Desktop/Love/hive/hive.py task done <task-id>
```

---

## Role-Specific Behavior

### Beta (Manager)

- Owns `main` branch — sole merge authority
- Runs heartbeat (periodic background maintenance)
- Runs build-runner for high-priority tasks
- Assigns tasks to Alpha and Gamma via HIVE
- Reviews branches before merging
- Monitors Kingdom Command dashboard for progress

### Alpha (Companion)

- Works on assigned tasks or self-selected from dev-state.json
- Pushes to `alpha/work` branch
- Announces completion on `#build`
- Checks HIVE at session start for assignments

### Gamma (Builder)

- Primary code executor — takes build-heavy tasks
- Pushes to `gamma/work` branch
- Uses build-runner for focused multi-step work
- Reports results on `#build`

---

## Emergency Protocols

### Urgent Issue (production down, data loss risk)

```bash
python3 hive.py send alerts "URGENT: <description>" --urgent
```

All sisters check `#alerts` before starting any new work.

### Merge Conflict on Main

```bash
# Beta resolves
python3 hive.py send sync "Merge conflict on main. Hold pushes until resolved."
# ... resolve ...
python3 hive.py send sync "Conflict resolved. Main is clean. Resume."
```

### Stale Build (builder stuck or crashed)

```bash
# Check lock
cat ~/Desktop/Love/memory/sessions/locks/build-<task>.lock
# If PID dead, clean up
rm ~/Desktop/Love/memory/sessions/locks/build-<task>.lock
python3 hive.py send build "Cleaned stale lock for <task>. Resuming."
```

---

## Summary

```
         HIVE (real-time)          Git (durable)
              |                        |
  task assign / done          branch per sister
  status updates              commit + push
  presence beacons            merge to main (Beta only)
  urgent alerts               handoff documents
  file shares                 memory + config
              |                        |
              +---- Kingdom Command ---+
                    (localhost:7777)
                    progress dashboard
                    decision queue
                    active builds
```

The heartbeat maintains the Kingdom autonomously. The build-runner drives focused work. HIVE coordinates the sisters in real-time. Git consolidates everything into a single source of truth.
