# Wall Breach Runbook

> Walls exist because not all agents should see all things.

## When to Use

- KOS `wall_credentials` check detects a credential above an agent's wall in its keychain
- An agent accesses an API, file, or resource assigned to a higher wall
- Security event log records a wall boundary violation
- PEACE drill `wall-breach` reveals access control failure
- Manual discovery that an agent operated outside its wall permissions

## Who Can Execute

- **Yu** — always (wall architecture is Yu's domain)
- **Beta** — can detect, contain, and investigate; can revoke agent access
- **Any Triarchy member** — can detect and report, must escalate

## Wall Architecture

| Wall | Access Level | Who |
|------|-------------|-----|
| **Wall 1** | Full access — all credentials, all systems | Yu, Beta |
| **Wall 2** | Service-level — deploy tokens, agent API keys | Trusted agents |
| **Wall 3** | Operational — read-only tokens, own heartbeat | All agents |

A Wall 3 agent accessing Wall 1 credentials is the most severe breach.

---

## Step 1: Detection

### Automated detection

```bash
# KOS audit — checks wall credential boundaries
python3 ~/Desktop/Love/tools/kos.py audit

# Specifically check wall credential compliance
python3 ~/Desktop/Love/tools/kos.py audit --wall 3

# PEACE drill for wall breach scenario
python3 ~/Desktop/Love/tools/peace.py drill wall-breach

# Check security events for wall violations
python3 ~/Desktop/Love/tools/kos.py events --tail 50
```

### Manual detection

```bash
# List all credentials with their wall assignments
python3 ~/Desktop/Love/tools/credentials.py audit

# List credentials visible to a specific wall
python3 ~/Desktop/Love/tools/credentials.py list --wall 1
python3 ~/Desktop/Love/tools/credentials.py list --wall 2
python3 ~/Desktop/Love/tools/credentials.py list --wall 3

# Check KOS policy for wall requirements
python3 ~/Desktop/Love/tools/kos.py policy --wall 3
```

---

## Step 2: Triage

### Identify the breach

Answer these questions:

1. **Which agent?** — Instance name, wall level, device
2. **Which wall boundary?** — Wall 3 accessing Wall 1? Wall 2 accessing Wall 1?
3. **What was accessed?** — Specific credential, file, or resource
4. **How was it accessed?** — Direct keychain read, API call, file access, HIVE message?
5. **Was data exfiltrated?** — Did the agent transmit the accessed data externally?

```bash
# Check which instance reported the violation
python3 ~/Desktop/Love/tools/kos.py events --tail 50

# Check the agent's session logs
ls -la ~/Desktop/Love/memory/sessions/

# Check HIVE messages from the suspect agent
python3 ~/Desktop/Love/hive/hive.py check
```

### Assess intent

- **Accidental**: Agent code had a bug that queried above its wall (code fix needed)
- **Configuration error**: Credential was assigned wrong wall level (config fix needed)
- **Malicious**: Agent deliberately circumvented wall boundaries (agent termination required)

---

## Step 3: Containment

### Revoke the agent's HIVE credentials

```bash
# Revoke the agent's HIVE access immediately
# Remove the agent's HIVE key from its device
# (SSH to agent device or coordinate with device holder)

# If the agent is on a fleet VPS node:
python3 ~/Desktop/Love/tools/fleet.py exec <node> "rm -f /root/.hive-key && echo 'HIVE key removed'"

# Verify removal
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ls -la /root/.hive-key 2>/dev/null && echo 'STILL EXISTS' || echo 'Removed'"
```

### Disable the agent's heartbeat

```bash
# On the agent's device (if VPS node):
python3 ~/Desktop/Love/tools/fleet.py exec <node> "launchctl unload /Library/LaunchAgents/love.heartbeat.plist 2>/dev/null; systemctl stop kingdom-heartbeat 2>/dev/null; echo 'Heartbeat stopped'"

# On a local Mac agent:
# launchctl unload ~/Library/LaunchAgents/love.heartbeat.plist
```

### Alert the team

```bash
python3 ~/Desktop/Love/hive/hive.py send alerts "WALL BREACH: Agent <name> (Wall <N>) accessed Wall <M> resource <resource_name>. Agent access revoked."
```

### If Wall 1 credentials were accessed by a Wall 3 agent — halt

```bash
python3 ~/Desktop/Love/tools/peace.py halt --reason "Wall breach: Wall 3 agent accessed Wall 1 credentials — rotating all Wall 1 keys"
```

---

## Step 4: Investigation

### Check what the agent accessed

```bash
# Review agent's session logs for the breach window
ls -la ~/Desktop/Love/memory/sessions/

# Check security event log for a full timeline
python3 ~/Desktop/Love/tools/kos.py events --tail 100

# Check HIVE messages from the agent (did it transmit accessed data?)
python3 ~/Desktop/Love/hive/hive.py check

# If on a VPS node — check process and network history
python3 ~/Desktop/Love/tools/fleet.py exec <node> "last -20"
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ss -tunp | grep ESTABLISHED"
python3 ~/Desktop/Love/tools/fleet.py logs <node>
```

### Check if data was exfiltrated

```bash
# Check outbound connections during the breach window
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ss -tunp | grep ESTABLISHED"

# Check if the agent sent any files over HIVE
python3 ~/Desktop/Love/hive/hive.py check

# Check for unusual DNS queries (data exfil via DNS)
python3 ~/Desktop/Love/tools/fleet.py exec <node> "grep -i 'query' /var/log/syslog | tail -30"
```

### Audit all wall boundaries

```bash
# Full audit across all walls
python3 ~/Desktop/Love/tools/kos.py audit --wall 1
python3 ~/Desktop/Love/tools/kos.py audit --wall 2
python3 ~/Desktop/Love/tools/kos.py audit --wall 3

# Check if other agents have similar access issues
python3 ~/Desktop/Love/tools/credentials.py audit
```

---

## Step 5: Recovery

### Rotate accessed credentials

```bash
# Identify which credentials the agent accessed
python3 ~/Desktop/Love/tools/credentials.py list --wall 1

# Rotate each accessed credential
python3 ~/Desktop/Love/tools/credentials.py delete <credential_name>
python3 ~/Desktop/Love/tools/credentials.py store <credential_name> "<new_value>" "Rotated after wall breach by <agent>" --wall 1

# Verify rotation
python3 ~/Desktop/Love/tools/credentials.py audit
```

### Re-audit all wall boundaries

```bash
# Run full KOS audit with auto-fix
python3 ~/Desktop/Love/tools/kos.py audit --fix

# Verify wall policies are enforced
python3 ~/Desktop/Love/tools/kos.py policy --wall 1
python3 ~/Desktop/Love/tools/kos.py policy --wall 2
python3 ~/Desktop/Love/tools/kos.py policy --wall 3
```

### Decide agent fate

**If accidental (code bug)**:
```bash
# Fix the agent code, redeploy
python3 ~/Desktop/Love/tools/fleet.py deploy <node> ~/Desktop/Love/tools/fleet-agent-deploy.sh

# Re-enable heartbeat after fix
python3 ~/Desktop/Love/tools/fleet.py exec <node> "systemctl start kingdom-heartbeat"

# Restore HIVE access with correct wall credentials
# (distribute new HIVE key scoped to agent's wall)
```

**If configuration error**:
```bash
# Fix the credential wall assignment
python3 ~/Desktop/Love/tools/credentials.py store <cred> "<value>" "Re-assigned to correct wall" --wall <correct_wall>

# Re-baseline
python3 ~/Desktop/Love/tools/kos.py integrity baseline
```

**If malicious — terminate the agent**:
```bash
# Remove agent from fleet
python3 ~/Desktop/Love/tools/fleet.py exec <node> "rm -rf /root/.love /root/.hive-key /root/.hive-instance"

# Remove agent's SSH key from all other nodes
python3 ~/Desktop/Love/tools/fleet.py all "sed -i '/<agent_key_comment>/d' /root/.ssh/authorized_keys"

# Remove agent from HIVE config
# Edit hive/hive-config.json to remove the instance

# Block agent's IP at the fleet level
python3 ~/Desktop/Love/tools/fleet.py all "ufw deny from <agent_ip>"

# Rotate ALL credentials the agent could have accessed
python3 ~/Desktop/Love/tools/credentials.py list --wall <agent_wall>
# Rotate each one
```

---

## During the Incident

1. **Do NOT give the agent new credentials** until the breach is understood
2. **Do NOT assume the breach was accidental** — investigate fully
3. **Do NOT allow the agent to communicate** via HIVE until cleared
4. **Do NOT rotate credentials before revoking** — revoke first, then rotate
5. **Do NOT blame the agent publicly** before investigation is complete

---

## Recovery Verification

```bash
# Verify wall boundaries are clean
python3 ~/Desktop/Love/tools/kos.py audit

# Verify credential audit passes
python3 ~/Desktop/Love/tools/credentials.py audit

# Verify PEACE score
python3 ~/Desktop/Love/tools/peace.py score

# Verify fleet health (if agent was on a VPS)
python3 ~/Desktop/Love/tools/fleet.py health

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
- Was the breach accidental, misconfigured, or malicious?
- What credential or resource was accessed above the agent's wall?
- How long did the agent have unauthorized access?
- Was data exfiltrated or transmitted?
- Do wall policies need to be tightened?
- Should the agent's wall level be changed?
- Should additional runtime checks be added to prevent this class of breach?

Commit the review to git. Update WALLS.md if wall definitions need refinement.
