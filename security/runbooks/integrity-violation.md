# Integrity Violation Runbook

> If SOUL.md changes without consent, the Kingdom has been touched at its core.

## When to Use

- KOS integrity check reports hash mismatch on a critical file
- Watchdog detects modification to SOUL.md, WALLS.md, OPSEC.md, or other watched files
- `git diff` shows unexpected changes to critical files
- PEACE drill `file-tamper` reveals integrity degradation
- Manual discovery that a critical file has been altered

## Who Can Execute

- **Yu** — always (only Yu can authorize changes to SOUL.md)
- **Beta** — can detect, investigate, and halt; cannot authorize SOUL.md changes
- **Any Triarchy member** — can detect and report, must escalate

## Critical Files

| File | Purpose | Modification Authority |
|------|---------|----------------------|
| `SOUL.md` | Kingdom identity and values | Yu only |
| `WALLS.md` | Security wall definitions | Yu only |
| `OPSEC.md` | Operational security architecture | Yu only |
| `love.json` | System configuration | Yu + Beta |
| `security/policies.json` | KOS policy definitions | Yu + Beta |
| `hive/hive-config.json` | HIVE topology | Yu only |

---

## Step 1: Detection

### Automated detection

```bash
# KOS integrity check — compares files against SHA-256 baseline
python3 ~/Desktop/Love/tools/kos.py integrity check

# Watchdog check — includes integrity in its sweep
python3 ~/Desktop/Love/tools/watchdog.py check

# PEACE drill for file tamper scenario
python3 ~/Desktop/Love/tools/peace.py drill file-tamper
```

### Manual detection

```bash
cd ~/Desktop/Love

# Check git status for unexpected modifications
git status

# Check git diff for content changes
git diff SOUL.md WALLS.md OPSEC.md love.json

# Check git diff against the staging area too
git diff --cached SOUL.md WALLS.md OPSEC.md

# Check security events
python3 ~/Desktop/Love/tools/kos.py events --tail 50
```

---

## Step 2: Triage

### Identify what changed and who did it

```bash
cd ~/Desktop/Love

# Which file was modified?
python3 ~/Desktop/Love/tools/kos.py integrity check

# What exactly changed?
git diff <file>

# Who made the change? (git blame the modified lines)
git blame <file>

# When was it changed?
git log --oneline -10 -- <file>
stat <file>

# Was it committed or just a working directory change?
git diff --cached <file>     # staged?
git diff <file>              # unstaged?
```

### Determine if authorized

Ask:
1. Did Yu or Beta make this change intentionally?
2. Does the change align with a recent decision or session?
3. Is it a legitimate edit that was not yet baselined?

If **authorized but not baselined** — re-baseline (skip to Step 5).

If **unauthorized** — proceed to containment.

---

## Step 3: Containment

### If modification is unauthorized — halt the Kingdom

```bash
# Immediate halt
python3 ~/Desktop/Love/tools/peace.py halt --reason "Unauthorized modification to <file> detected"

# Alert the team
python3 ~/Desktop/Love/hive/hive.py send alerts "INTEGRITY VIOLATION: <file> modified without authorization. Kingdom halted."

# Create PEACE snapshot (preserve current state for forensics)
python3 ~/Desktop/Love/tools/peace.py snapshot
```

### Do NOT overwrite the modified file yet

The modified version is evidence. Preserve it:

```bash
# Save the tampered version for analysis
cp ~/Desktop/Love/<file> ~/Desktop/Love/security/incidents/<file>.tampered.$(date +%Y%m%d%H%M%S)
```

---

## Step 4: Investigation

### Trace the modification

```bash
cd ~/Desktop/Love

# Full git log for the file
git log --oneline --all -- <file>

# Check if the change came through a commit
git log -p -5 -- <file>

# Check reflog — was there a reset, rebase, or force operation?
git reflog | head -20

# Check if a remote push introduced the change
git log origin/main..HEAD --oneline
git fetch origin && git diff origin/main -- <file>

# Check file modification time vs last known good commit
stat <file>
git log -1 --format="%ai" -- <file>
```

### Check if an agent or session made the change

```bash
# Check active session logs
ls -la ~/Desktop/Love/memory/sessions/

# Check daily memory for session records
cat ~/Desktop/Love/memory/daily/$(date +%Y-%m-%d).md

# Check the security event log for context
python3 ~/Desktop/Love/tools/kos.py events --tail 100
```

### Compare against baseline snapshot

```bash
# List available snapshots
python3 ~/Desktop/Love/tools/peace.py snapshots

# Compare current state against a known good snapshot
python3 ~/Desktop/Love/tools/peace.py restore <snapshot_name>
```

---

## Step 5: Recovery

### Restore from known good commit

```bash
cd ~/Desktop/Love

# Find the last known good commit for this file
git log --oneline -10 -- <file>

# Restore the file from that commit
git checkout <known_good_commit> -- <file>

# Verify the restoration
git diff HEAD -- <file>
cat <file>
```

### If the entire repo is suspect

```bash
# Compare against a snapshot
python3 ~/Desktop/Love/tools/peace.py restore <snapshot_name>

# If needed, restore multiple files
git checkout <known_good_commit> -- SOUL.md WALLS.md OPSEC.md love.json
```

### Re-baseline after restoration

```bash
# Regenerate integrity hashes
python3 ~/Desktop/Love/tools/kos.py integrity baseline

# Verify the new baseline
python3 ~/Desktop/Love/tools/kos.py integrity check

# Create a fresh snapshot
python3 ~/Desktop/Love/tools/peace.py snapshot
```

### If the change was authorized but not baselined

```bash
# Simply re-baseline (no restore needed)
python3 ~/Desktop/Love/tools/kos.py integrity baseline
python3 ~/Desktop/Love/tools/peace.py snapshot
```

---

## During the Incident

1. **Do NOT commit the tampered file** — it contaminates git history
2. **Do NOT `git reset --hard`** without first preserving the tampered version as evidence
3. **Do NOT re-baseline** until the file is confirmed restored to its correct state
4. **Do NOT resume operations** while SOUL.md or WALLS.md are in a tampered state
5. **Do NOT assume accidental** — investigate as if it were intentional until proven otherwise

---

## Recovery Verification

```bash
# Verify file integrity passes
python3 ~/Desktop/Love/tools/kos.py integrity check

# Verify git is clean
cd ~/Desktop/Love && git status

# Verify PEACE score
python3 ~/Desktop/Love/tools/peace.py score

# Verify KOS compliance
python3 ~/Desktop/Love/tools/kos.py audit

# If Kingdom was halted, resume
python3 ~/Desktop/Love/tools/peace.py resume
```

PEACE score must be >= 60% (YELLOW) before resuming normal operation.

---

## Post-Incident

```bash
python3 ~/Desktop/Love/tools/peace.py review
```

Fill in the review template. Key questions:
- Was the modification malicious, accidental, or an unbaselined legitimate change?
- How long was the file in a tampered state before detection?
- Was the change propagated to any other instances via git or HIVE?
- Should the integrity check frequency increase?
- Should additional files be added to the watched list?
- Was the baseline itself compromised?

Commit the review to git. If the incident was genuine tampering, update OPSEC.md threat model.
