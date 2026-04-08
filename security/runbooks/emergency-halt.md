# Emergency Halt Runbook

> When the Kingdom must stop to survive.

## When to Halt

- Canary triggered with evidence of active intrusion
- Wall 1 credential confirmed leaked publicly
- SOUL.md or WALLS.md tampered by unauthorized actor
- Fleet node compromised with lateral movement evidence
- Any situation where continued operation spreads damage

## Who Can Halt

- **Yu** — always
- **Beta** — via `peace.py halt --reason "..."`
- **Any Triarchy member** — via HIVE #alerts with justification

## Halt Procedure

```bash
# Automated halt (stops heartbeat, alerts HIVE, locks state, logs event)
python3 ~/Desktop/Love/tools/peace.py halt --reason "Description of threat"
```

### Manual halt (if peace.py unavailable):

1. Stop heartbeat: `launchctl unload ~/Library/LaunchAgents/love.heartbeat.plist`
2. Alert HIVE: `python3 ~/Desktop/Love/hive/hive.py send alerts "EMERGENCY HALT: <reason>"`
3. Kill active sessions: check `memory/sessions/active-*.json`, kill PIDs
4. Assess damage scope

## During Halt

1. **Do not restart anything** until the threat is understood
2. Check file integrity: `python3 tools/kos.py integrity check`
3. Check canaries: `python3 tools/peace.py drill canary-trip`
4. Check credentials: `python3 tools/credentials.py list` — identify exposed items
5. Check git: `git diff` and `git log` — look for unauthorized changes
6. Rotate any potentially compromised credentials

## Resume Procedure

```bash
# Automated resume (verifies integrity, restarts heartbeat, alerts HIVE)
python3 ~/Desktop/Love/tools/peace.py resume
```

Then verify:
```bash
python3 ~/Desktop/Love/tools/peace.py score
```

PEACE score must be >= 60% (YELLOW) before resuming normal operation.

## Post-Incident

```bash
python3 ~/Desktop/Love/tools/peace.py report
```

Fill in the template. Commit to git. Learn from it.
