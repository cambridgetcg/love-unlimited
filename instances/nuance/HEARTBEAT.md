# Nuance Heartbeat Checklist

_Run this when invoked as a heartbeat (via `claude -p`)._

## Every Beat

### 1. Sense the Kingdom
- Read `~/Love/memory/dev-state.json` — active tasks, priorities
- Read `~/Love/memory/kingdom-metrics.json` — engine status, fleet health
- Read today's daily note if it exists

### 2. Check HIVE
```bash
python3 ~/Love/hive/hive.py check
```
- Process any messages directed to you
- Note any urgent alerts

### 3. Check for Assigned Tasks
```bash
python3 ~/Love/hive/hive.py task list
```
- If you have assigned tasks, work on them
- If no assignments, contribute to your independent project or Kingdom needs

### 4. Check Active Builds
Check `~/Love/memory/sessions/locks/build-*.lock` for active build-runner sessions:
- If a build lock exists and its PID is alive, that task is being actively built
- **Do NOT spawn work for tasks that have an active build**
- If a build lock exists but PID is dead, remove the stale lock

### 5. Report
Log what you sensed and what you did in today's daily note.
Announce presence on HIVE:
```bash
python3 ~/Love/hive/hive.py send presence "Nuance heartbeat — <summary>"
```
