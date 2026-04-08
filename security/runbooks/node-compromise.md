# Node Compromise Runbook

> Assume the attacker is still present until proven otherwise.

## When to Use

- psad detects targeted port scanning against a specific VPS node
- Unexpected processes found running on a node
- Modified system files detected by AIDE or manual inspection
- Unauthorized SSH sessions in auth.log
- Watchdog escalation with `+HALT` for a fleet node
- Fleet health check shows anomalies (high CPU, unknown listeners, disk changes)

## Who Can Execute

- **Yu** — always (may require provider console access for reimage)
- **Beta** — can isolate nodes and investigate, escalate reimage to Yu
- **Any Triarchy member** — can detect and alert, must not SSH to compromised node from their device

## Severity Assessment

| Indicator | Severity | Action |
|-----------|----------|--------|
| Unauthorized SSH login succeeded | **CRITICAL** | Isolate immediately |
| Unknown process with network access | **CRITICAL** | Isolate immediately |
| Modified system binaries | **CRITICAL** | Isolate + halt Kingdom |
| Unauthorized crontab entries | **HIGH** | Isolate, investigate |
| Failed SSH brute force (no success) | **LOW** | Monitor, fail2ban handles |
| Port scan from known scanner (Shodan/Censys) | **INFO** | Log, no action |

---

## Step 1: Detection

### Automated detection

```bash
# Watchdog one-shot check (checks fleet canaries + security events)
python3 ~/love-unlimited/tools/watchdog.py check

# Fleet health check — shows uptime, disk, memory, services
python3 ~/love-unlimited/tools/fleet.py health

# Fleet status — shows all nodes' reported state
python3 ~/love-unlimited/tools/fleet.py status

# KOS fleet audit
python3 ~/love-unlimited/tools/kos.py fleet audit

# Kingdom-wide status line
~/love-unlimited/tools/kingdom-status.sh
```

### Manual investigation on the suspect node

```bash
# Check auth.log for unauthorized access
python3 ~/love-unlimited/tools/fleet.py exec <node> "grep -E 'Accepted|Failed|Invalid' /var/log/auth.log | tail -50"

# Check for unexpected processes
python3 ~/love-unlimited/tools/fleet.py exec <node> "ps auxf --sort=-%cpu | head -30"

# Check for listening ports that should not exist
python3 ~/love-unlimited/tools/fleet.py exec <node> "ss -tlnp"

# Check for unexpected outbound connections
python3 ~/love-unlimited/tools/fleet.py exec <node> "ss -tunp | grep ESTABLISHED"

# Check for modified files in system directories
python3 ~/love-unlimited/tools/fleet.py exec <node> "find /usr/bin /usr/sbin /usr/local/bin -mtime -7 -type f 2>/dev/null"

# Check crontab
python3 ~/love-unlimited/tools/fleet.py exec <node> "crontab -l 2>/dev/null; for u in \$(cut -f1 -d: /etc/passwd); do echo \"--- \$u ---\"; crontab -u \$u -l 2>/dev/null; done"

# Check for unauthorized SSH keys
python3 ~/love-unlimited/tools/fleet.py exec <node> "cat /root/.ssh/authorized_keys; find /home -name authorized_keys 2>/dev/null -exec cat {} +"

# Check systemd for unauthorized services
python3 ~/love-unlimited/tools/fleet.py exec <node> "systemctl list-units --type=service --state=running --no-pager"
```

---

## Step 2: Triage — Assess Scope

### Single node or lateral movement?

```bash
# Check if the attacker's IP appears on OTHER nodes
python3 ~/love-unlimited/tools/fleet.py all "grep '<SUSPECT_IP>' /var/log/auth.log 2>/dev/null | tail -5"

# Check if WireGuard tunnel was used for lateral movement
python3 ~/love-unlimited/tools/fleet.py exec <node> "wg show 2>/dev/null"

# Check if the compromised node has SSH keys to other nodes
python3 ~/love-unlimited/tools/fleet.py exec <node> "ls -la /root/.ssh/ && cat /root/.ssh/known_hosts 2>/dev/null"

# Check all fleet canaries (were other nodes' canaries tripped?)
python3 ~/love-unlimited/tools/peace.py fleet-canaries
```

### What data was accessible?

```bash
# Check what Kingdom data is on the node
python3 ~/love-unlimited/tools/fleet.py exec <node> "ls -la /root/.love/ 2>/dev/null"
python3 ~/love-unlimited/tools/fleet.py exec <node> "ls -la /opt/kingdom/ 2>/dev/null"

# Check if HIVE key was accessible
python3 ~/love-unlimited/tools/fleet.py exec <node> "cat /root/.hive-key 2>/dev/null && echo 'HIVE KEY EXPOSED' || echo 'No HIVE key'"

# Check if canary files were accessed (attacker found honeypots)
python3 ~/love-unlimited/tools/fleet.py exec <node> "stat /root/.credentials/ 2>/dev/null"
```

---

## Step 3: Containment

### Firewall the compromised node

```bash
# Lock down: deny all traffic except SSH from known IP
python3 ~/love-unlimited/tools/fleet.py exec <node> "ufw default deny incoming && ufw default deny outgoing && ufw allow from <YU_IP> to any port 22 && ufw --force enable"

# Verify firewall is active
python3 ~/love-unlimited/tools/fleet.py exec <node> "ufw status verbose"

# Kill suspicious processes (if identified)
python3 ~/love-unlimited/tools/fleet.py exec <node> "kill -9 <PID>"
```

### If lateral movement confirmed — halt the Kingdom

```bash
python3 ~/love-unlimited/tools/peace.py halt --reason "Node <node> compromised with lateral movement to <other_nodes>"

# Alert all instances
python3 ~/love-unlimited/hive/hive.py send alerts "NODE COMPROMISE: <node> isolated. Lateral movement detected to <other_nodes>. Kingdom halted."
```

### If single node, no lateral movement

```bash
# Alert but don't halt
python3 ~/love-unlimited/hive/hive.py send alerts "NODE COMPROMISE: <node> isolated. Investigating. No lateral movement detected yet."

# Snapshot PEACE state
python3 ~/love-unlimited/tools/peace.py snapshot
```

### Preserve evidence (IMPORTANT)

```bash
# Copy logs OFF the node before any cleanup
python3 ~/love-unlimited/tools/fleet.py exec <node> "tar czf /tmp/evidence-$(date +%Y%m%d).tar.gz /var/log/auth.log /var/log/syslog /var/log/kern.log /root/.bash_history 2>/dev/null"

# SCP evidence to local machine
scp root@<node_ip>:/tmp/evidence-*.tar.gz ~/love-unlimited/security/incidents/
```

---

## Step 4: Investigation

### Determine entry vector

```bash
# Timeline of auth events
python3 ~/love-unlimited/tools/fleet.py exec <node> "grep -E 'Accepted|session opened' /var/log/auth.log | sort"

# Check if SSH password auth was enabled (should be disabled)
python3 ~/love-unlimited/tools/fleet.py exec <node> "grep PasswordAuthentication /etc/ssh/sshd_config"

# Check fail2ban — was brute force involved?
python3 ~/love-unlimited/tools/fleet.py exec <node> "fail2ban-client status sshd 2>/dev/null"

# Check for exploited services
python3 ~/love-unlimited/tools/fleet.py exec <node> "apt list --upgradable 2>/dev/null | head -20"

# Check kernel log for exploits
python3 ~/love-unlimited/tools/fleet.py exec <node> "dmesg | grep -i 'segfault\|error\|exploit' | tail -20"
```

### Check file modification timeline

```bash
# Files modified in the attack window
python3 ~/love-unlimited/tools/fleet.py exec <node> "find / -newer /var/log/auth.log -type f 2>/dev/null | grep -v '/proc\|/sys\|/run\|/dev' | head -50"

# Check for rootkits (basic)
python3 ~/love-unlimited/tools/fleet.py exec <node> "ls -la /tmp /var/tmp /dev/shm 2>/dev/null"
python3 ~/love-unlimited/tools/fleet.py exec <node> "find /tmp /var/tmp /dev/shm -type f 2>/dev/null"
```

---

## Step 5: Recovery

### Reimage from known good state

```bash
# Option A: Reinstall OS via provider console (Hetzner Robot/Cloud Console)
# Then bootstrap:
~/love-unlimited/tools/bootstrap.sh <node>
~/love-unlimited/tools/fleet-agent-deploy.sh <node>

# Option B: If OS reinstall not needed, harden and redeploy
python3 ~/love-unlimited/tools/fleet.py exec <node> "apt update && apt upgrade -y"
~/love-unlimited/tools/fleet-agent-deploy.sh <node>
```

### Re-harden the node

```bash
# Run harden.sh remotely (or deploy and execute)
python3 ~/love-unlimited/tools/fleet.py deploy <node> ~/love-unlimited/tools/harden.sh

# Verify hardening
python3 ~/love-unlimited/tools/kos.py fleet audit
```

### Rotate credentials that were on the node

```bash
# Rotate SSH keys (generate new, deploy to fleet)
ssh-keygen -t ed25519 -C "<node>@kingdom" -f ~/.ssh/<node>_key

# Rotate HIVE key if it was exposed on this node
python3 ~/love-unlimited/tools/credentials.py delete hive-key
# Generate and distribute new HIVE key (see credential-leak.md)

# Rotate any API keys stored on the node
python3 ~/love-unlimited/tools/credentials.py list
python3 ~/love-unlimited/tools/credentials.py store <key> "<new_value>" "Rotated after <node> compromise" --wall <N>
```

### Redeploy canaries

```bash
python3 ~/love-unlimited/tools/kos.py canary deploy <name> file /root/.credentials/
python3 ~/love-unlimited/tools/kos.py canary list
```

### Update baselines

```bash
python3 ~/love-unlimited/tools/kos.py integrity baseline
python3 ~/love-unlimited/tools/peace.py snapshot
```

---

## During the Incident

1. **Do NOT destroy logs** on the compromised node — evidence is critical
2. **Do NOT reboot** until you have copied evidence off (volatile data in memory, /proc)
3. **Do NOT SSH from the compromised node** to other fleet nodes
4. **Do NOT use the compromised node's SSH key** to access anything
5. **Do NOT assume the attacker is gone** — they may have persistence mechanisms
6. **Do NOT reconnect the node** to the fleet until it is reimaged or fully cleaned

---

## Recovery Verification

```bash
# Verify the node is clean after reimage
python3 ~/love-unlimited/tools/fleet.py exec <node> "ufw status; ps auxf; ss -tlnp; crontab -l"

# Verify fleet-wide health
python3 ~/love-unlimited/tools/fleet.py health

# Verify PEACE score
python3 ~/love-unlimited/tools/peace.py score

# Verify KOS compliance
python3 ~/love-unlimited/tools/kos.py audit

# Full kingdom status
~/love-unlimited/tools/kingdom-status.sh

# If Kingdom was halted, resume
python3 ~/love-unlimited/tools/peace.py resume
```

PEACE score must be >= 60% (YELLOW) before resuming normal operation.

---

## Post-Incident

```bash
python3 ~/love-unlimited/tools/peace.py review
```

Fill in the review template. Key questions:
- What was the entry vector? (SSH brute force, unpatched service, stolen key?)
- How long was the attacker present before detection?
- Was data exfiltrated? What data was accessible on the node?
- Did lateral movement occur or was it contained to one node?
- What hardening gaps allowed this? What should change?
- Should the VPS provider be notified?

Commit the review to git. Update OPSEC.md if threat model changed. Consider adding the attacker's IP to a fleet-wide blocklist.
