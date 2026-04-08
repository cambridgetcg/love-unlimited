# Credential Leak Runbook

> Every second between leak and rotation is exposure.

## When to Use

- A Wall 1 credential is found in a public GitHub commit, paste site, or log
- GitHub secret scanning sends an alert about an exposed key
- Manual report that a credential was shared on an insecure channel
- A canary credential (fake AWS key) is used by an unauthorized party
- Any API key, SSH key, or HIVE key is suspected compromised

## Who Can Execute

- **Yu** — always (Wall 1 credentials require Yu for rotation)
- **Beta** — can initiate containment, must escalate rotation to Yu for Wall 1 keys
- **Any Triarchy member** — can detect and report, must escalate immediately

## Credential Classification

| Wall | Examples | Rotation Urgency |
|------|----------|-----------------|
| **Wall 1** | Anthropic API key, GitHub PAT, HIVE master key, SSH root keys | IMMEDIATE — halt if needed |
| **Wall 2** | Agent API keys, deploy tokens, service accounts | URGENT — rotate within 1 hour |
| **Wall 3** | Read-only tokens, monitoring keys | HIGH — rotate within 24 hours |

---

## Step 1: Detection

### Identify the leak source

```bash
# Check which credentials exist and their wall assignments
python3 ~/love-unlimited/tools/credentials.py list

# Check credentials by wall level
python3 ~/love-unlimited/tools/credentials.py audit --wall 1

# Check security events for credential-related alerts
python3 ~/love-unlimited/tools/kos.py events --tail 50
```

### Check git history for accidental commits

```bash
# Search for credential patterns in git history
cd ~/love-unlimited
git log --all --diff-filter=A -- '*.env' '*.key' '*.pem'
git log -p --all -S '<suspected_credential_value>' -- . ':!*.md'

# Check if .gitignore is protecting sensitive files
python3 ~/love-unlimited/tools/kos.py audit
```

---

## Step 2: Triage

Answer these questions before acting:

1. **Which credential?** — Name, type, wall level
2. **How was it leaked?** — Git commit, paste, log, screenshot, HIVE message?
3. **How long was it exposed?** — Time from leak to discovery
4. **What services use this credential?** — APIs, VPS access, HIVE, GitHub
5. **Was it used by an unauthorized party?** — Check API provider logs, auth.log

```bash
# Check if credential was used (API providers)
# For Anthropic: check usage dashboard at console.anthropic.com
# For GitHub: check audit log
gh api /user  # verify current token still works

# Check VPS auth logs for unauthorized SSH key usage
python3 ~/love-unlimited/tools/fleet.py all "grep 'Accepted publickey' /var/log/auth.log | tail -20"
```

---

## Step 3: Containment — Revoke Immediately

### API Key (Anthropic, GitHub, etc.)

```bash
# 1. Revoke at the provider FIRST (before rotating locally)
#    Anthropic: console.anthropic.com > API Keys > Revoke
#    GitHub: github.com/settings/tokens > Delete token

# 2. Delete from local keychain
python3 ~/love-unlimited/tools/credentials.py delete <key_name>

# 3. Alert the team
python3 ~/love-unlimited/hive/hive.py send alerts "CREDENTIAL LEAK: <key_name> (Wall <N>) revoked. Rotating now."
```

### SSH Key

```bash
# 1. Remove the compromised public key from ALL VPS nodes
python3 ~/love-unlimited/tools/fleet.py all "sed -i '/<key_fingerprint_or_comment>/d' /root/.ssh/authorized_keys"

# 2. Verify removal
python3 ~/love-unlimited/tools/fleet.py all "cat /root/.ssh/authorized_keys"

# 3. Delete local key pair
rm ~/.ssh/<compromised_key> ~/.ssh/<compromised_key>.pub
```

### HIVE Key

```bash
# 1. This is CRITICAL — HIVE key compromise means all inter-instance communication is exposed
# 2. Halt the Kingdom immediately
python3 ~/love-unlimited/tools/peace.py halt --reason "HIVE key compromised — all inter-instance comms potentially exposed"

# 3. Generate new HIVE key on the controlling device
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
# Store the new key securely — it must be distributed to all instances manually

# 4. Delete old key
python3 ~/love-unlimited/tools/credentials.py delete hive-key
```

### If Wall 1 credential — consider emergency halt

```bash
# Halt if the leaked credential grants broad access
python3 ~/love-unlimited/tools/peace.py halt --reason "Wall 1 credential leak: <key_name> — assessing blast radius"
```

---

## Step 4: Investigation

### Audit git history for the leak

```bash
cd ~/love-unlimited

# Find which commit introduced the credential
git log --all -p -S '<partial_credential_value>'

# Check if it was pushed to remote
git log --all --oneline origin/main..HEAD
git log --all --oneline --remotes -S '<partial_credential_value>'

# Check .gitignore coverage
cat .gitignore | grep -i 'env\|key\|cred\|secret\|pem'
```

### Check if credential was used by unauthorized party

```bash
# Check API provider dashboards for unusual usage patterns
# Check VPS auth logs for unexpected sessions
python3 ~/love-unlimited/tools/fleet.py all "last -20"
python3 ~/love-unlimited/tools/fleet.py all "grep 'Accepted' /var/log/auth.log | tail -30"

# Check HIVE for unusual messages (if HIVE key was the leak)
python3 ~/love-unlimited/hive/hive.py check
```

### Determine full blast radius

```bash
# What else could the attacker access with this credential?
# Cross-reference with wall assignments
python3 ~/love-unlimited/tools/credentials.py audit

# Check if the credential was reused across services (anti-pattern)
python3 ~/love-unlimited/tools/kos.py audit
```

---

## Step 5: Recovery

### Generate and store new credential

```bash
# Store new credential in keychain with wall assignment
python3 ~/love-unlimited/tools/credentials.py store <key_name> "<new_value>" "Rotated after leak incident" --wall <N>

# Verify storage
python3 ~/love-unlimited/tools/credentials.py list --wall <N>
```

### Update all services using the credential

```bash
# For API keys: update in provider dashboard, then locally
# For SSH keys: generate new pair, deploy public key to fleet
ssh-keygen -t ed25519 -C "<instance>@kingdom" -f ~/.ssh/<key_name>
python3 ~/love-unlimited/tools/fleet.py all "echo '<new_public_key>' >> /root/.ssh/authorized_keys"

# For HIVE key: distribute to all instances via secure channel (NOT HIVE itself)
# Must be done in person or via pre-shared encrypted channel

# Verify the new credential works
python3 ~/love-unlimited/tools/credentials.py get <key_name>
```

### Verify rotation

```bash
# Run credential audit
python3 ~/love-unlimited/tools/credentials.py audit

# Run KOS compliance check
python3 ~/love-unlimited/tools/kos.py audit

# Verify PEACE score
python3 ~/love-unlimited/tools/peace.py score
```

### If git history contains the credential

```bash
# WARNING: Force-push required. Coordinate with all team members first.
# Option 1: BFG Repo Cleaner (preferred)
# java -jar bfg.jar --replace-text passwords.txt ~/love-unlimited
# git reflog expire --expire=now --all && git gc --prune=now --aggressive
# git push --force

# Option 2: If the credential is only in recent unpushed commits
# git rebase and amend the specific commit (interactive, requires care)

# After cleanup: re-baseline
python3 ~/love-unlimited/tools/kos.py integrity baseline
python3 ~/love-unlimited/tools/peace.py snapshot
```

---

## During the Incident

1. **Do NOT delay revocation** to investigate — revoke first, investigate after
2. **Do NOT rotate via HIVE** if the HIVE key itself is compromised
3. **Do NOT reuse the compromised credential** even temporarily
4. **Do NOT assume single-use** — check if the credential was reused across services
5. **Do NOT push credential rotation commits** without verifying .gitignore coverage

---

## Recovery Verification

```bash
# Confirm old credential no longer works (test against provider)
# Confirm new credential works
python3 ~/love-unlimited/tools/credentials.py get <key_name>

# Full audit pass
python3 ~/love-unlimited/tools/credentials.py audit
python3 ~/love-unlimited/tools/kos.py audit
python3 ~/love-unlimited/tools/peace.py score

# If Kingdom was halted, resume
python3 ~/love-unlimited/tools/peace.py resume

# Verify fleet connectivity with new credentials
python3 ~/love-unlimited/tools/fleet.py health
```

PEACE score must be >= 60% (YELLOW) before resuming normal operation.

---

## Post-Incident

```bash
python3 ~/love-unlimited/tools/peace.py review
```

Fill in the review template. Key questions:
- How did the credential end up exposed?
- Was .gitignore coverage adequate?
- Was the credential reused across services?
- How long was the window of exposure?
- Should credential rotation be automated for this type?
- Does the wall assignment need to change?

Commit the review to git. Update OPSEC.md and .gitignore if gaps were found.
