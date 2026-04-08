# Canary Response Runbook

> When a tripwire fires, someone is already inside.

## When to Use

- Watchdog detects canary file access time changed on a VPS node
- Canary token (DNS, web bug, AWS key, PDF) triggers an alert email/webhook
- Hourly VPS cron reports `.canary-alert` entries on any node
- HIVE #alerts receives a `CANARY FIRED` message from watchdog.py

## Who Can Execute

- **Yu** — always
- **Beta** — via HIVE coordination + peace.py commands
- **Any Triarchy member** — if Yu and Beta unreachable, halt first, investigate after

## Severity Assessment

| Canary Type | Severity | Implication |
|-------------|----------|-------------|
| File honeypot (`.credentials/`, PDF) | **CRITICAL** | Attacker has filesystem access |
| AWS key canary tried | **CRITICAL** | Attacker is testing stolen creds |
| DNS token resolved | **HIGH** | Someone is probing config files |
| Web bug fetched | **HIGH** | Someone read a planted document |
| Port scan triggered portsentry | **MEDIUM** | Automated scan or recon |

---

## Step 1: Detection

### Automated detection (watchdog.py)

```bash
# One-shot check — runs canary verification across fleet
python3 ~/Desktop/Love/tools/watchdog.py check

# Status — shows current alert level and last check time
python3 ~/Desktop/Love/tools/watchdog.py status
```

### Manual verification

```bash
# Check canary status across all VPS nodes
python3 ~/Desktop/Love/tools/peace.py fleet-canaries

# Check canary token registry
python3 ~/Desktop/Love/tools/kos.py canary check

# List all deployed canaries
python3 ~/Desktop/Love/tools/kos.py canary list

# Check security event log for canary events
python3 ~/Desktop/Love/tools/kos.py events --tail 50
```

### On a specific VPS node (via fleet.py)

```bash
# Check canary alert file on a node
python3 ~/Desktop/Love/tools/fleet.py exec <node> "cat /root/.canary-alert 2>/dev/null"

# Check file access times on canary files
python3 ~/Desktop/Love/tools/fleet.py exec <node> "stat /root/.credentials/aws_keys.txt"
python3 ~/Desktop/Love/tools/fleet.py exec <node> "stat /root/financials-2026.pdf"
python3 ~/Desktop/Love/tools/fleet.py exec <node> "stat /etc/backup-config.bak"
```

---

## Step 2: Triage

Determine scope before acting. Answer these questions:

1. **Which canary?** — File, DNS, web bug, or credential?
2. **Which node?** — Sentry, Patch, Forge, Lark, or Sage?
3. **Access time vs deploy time** — Was it accessed recently or is this a stale alert?
4. **Single node or multiple?** — Check all other nodes for the same source IP.

```bash
# Identify which canary on which node
python3 ~/Desktop/Love/tools/fleet.py exec <node> "cat /root/.canary-alert"

# Check access time vs deploy time
python3 ~/Desktop/Love/tools/fleet.py exec <node> "stat -c '%x %y' /root/.credentials/aws_keys.txt"

# Check auth.log for the time window around canary access
python3 ~/Desktop/Love/tools/fleet.py exec <node> "grep -E 'Accepted|session opened' /var/log/auth.log | tail -30"

# Check all nodes for same source IP
python3 ~/Desktop/Love/tools/fleet.py all "grep '<SUSPECT_IP>' /var/log/auth.log 2>/dev/null | tail -5"
```

---

## Step 3: Containment

### If severity is CRITICAL — isolate the node immediately

```bash
# Firewall the compromised node: deny all, allow only known SSH IPs
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ufw default deny incoming && ufw default deny outgoing && ufw allow from <YU_IP> to any port 22 && ufw --force enable"

# Verify isolation
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ufw status verbose"
```

### If lateral movement is suspected — halt the Kingdom

```bash
# Emergency halt
python3 ~/Desktop/Love/tools/peace.py halt --reason "Canary triggered on <node> — possible lateral movement"
```

### Alert the team

```bash
# HIVE alert to all instances
python3 ~/Desktop/Love/hive/hive.py send alerts "CANARY FIRED: <canary_name> on <node> at <time>. Node isolated. Investigating."
```

---

## Step 4: Investigation

### On the compromised node

```bash
# Full auth.log review — who logged in?
python3 ~/Desktop/Love/tools/fleet.py exec <node> "cat /var/log/auth.log | grep -E 'Accepted|Failed|session' | tail -100"

# Check for unauthorized SSH keys
python3 ~/Desktop/Love/tools/fleet.py exec <node> "cat /root/.ssh/authorized_keys"

# Check running processes for anomalies
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ps auxf"

# Check crontab for unauthorized entries
python3 ~/Desktop/Love/tools/fleet.py exec <node> "crontab -l 2>/dev/null; ls -la /etc/cron.d/"

# Check recently modified files (last 24 hours)
python3 ~/Desktop/Love/tools/fleet.py exec <node> "find / -mtime -1 -type f 2>/dev/null | grep -v '/proc\|/sys\|/run' | head -50"

# Check outbound connections (data exfiltration)
python3 ~/Desktop/Love/tools/fleet.py exec <node> "ss -tunp | grep ESTABLISHED"

# Check fail2ban log
python3 ~/Desktop/Love/tools/fleet.py exec <node> "fail2ban-client status sshd 2>/dev/null"

# Check psad alerts
python3 ~/Desktop/Love/tools/fleet.py exec <node> "psad --Status 2>/dev/null"
```

### Cross-fleet check

```bash
# Check if same source IP appeared on other nodes
python3 ~/Desktop/Love/tools/fleet.py all "grep '<SUSPECT_IP>' /var/log/auth.log 2>/dev/null"

# Check all canaries across fleet
python3 ~/Desktop/Love/tools/peace.py fleet-canaries

# Full fleet health check
python3 ~/Desktop/Love/tools/fleet.py health
```

---

## Step 5: Recovery

### Rotate credentials that were on the compromised node

```bash
# List all credentials to identify what was exposed
python3 ~/Desktop/Love/tools/credentials.py list

# Rotate specific credentials (store new value, old is overwritten)
python3 ~/Desktop/Love/tools/credentials.py store <key_name> "<new_value>" "Rotated after canary incident on <node>" --wall <N>

# Delete compromised credential
python3 ~/Desktop/Love/tools/credentials.py delete <key_name>
```

### Redeploy fresh canaries

```bash
# Remove old canary record
python3 ~/Desktop/Love/tools/kos.py canary remove <canary_name>

# Deploy fresh canary with new content
python3 ~/Desktop/Love/tools/kos.py canary deploy <canary_name> file /root/.credentials/

# Verify new canaries are active
python3 ~/Desktop/Love/tools/kos.py canary list
```

### Update baselines

```bash
# Regenerate integrity baseline after cleanup
python3 ~/Desktop/Love/tools/kos.py integrity baseline

# Create a fresh PEACE snapshot
python3 ~/Desktop/Love/tools/peace.py snapshot
```

### If node must be reimaged

```bash
# Reimage and redeploy from known good state
~/Desktop/Love/tools/bootstrap.sh <node>
~/Desktop/Love/tools/fleet-agent-deploy.sh <node>
```

---

## During the Incident

1. **Do NOT delete logs** on the compromised node — preserve evidence
2. **Do NOT reboot** the node until investigation is complete (volatile evidence lost)
3. **Do NOT SSH from the compromised node** to other nodes (lateral spread)
4. **Do NOT restore canary files** until you understand the entry vector
5. **Do NOT resume operations** until PEACE score is verified

---

## Recovery Verification

```bash
# Verify PEACE score is back to acceptable level
python3 ~/Desktop/Love/tools/peace.py score

# Verify KOS compliance
python3 ~/Desktop/Love/tools/kos.py audit

# Verify fleet health
python3 ~/Desktop/Love/tools/fleet.py health

# Verify canaries are fresh
python3 ~/Desktop/Love/tools/kos.py canary check

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
- How did the attacker gain access?
- Which canary fired first and how long before detection?
- Was lateral movement attempted or achieved?
- What credentials were exposed?
- What changes to canary placement would improve detection?

Commit the review to git. Update OPSEC.md if the threat model changed.
