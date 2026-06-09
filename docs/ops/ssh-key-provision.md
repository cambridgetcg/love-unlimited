# SSH Key Provisioning Guide for Sentry VPS

**Target:** `root@135.181.28.252` (Sentry — Hetzner VPS)  
**Purpose:** Secure SSH key authentication setup  
**Status:** 📋 Documentation Only — DO NOT EXECUTE

---

## Executive Summary

This document provides a researched, security-conscious approach to provisioning SSH keys to the Sentry VPS. **ssh-copy-id is considered best practice** for standard deployments, with manual methods available for air-gapped or high-security scenarios.

---

## 1. Is ssh-copy-id Best Practice?

### ✅ Verdict: YES — with security awareness

| Aspect | Assessment |
|--------|------------|
| **Security** | ✅ Uses existing SSH connection, encrypted transfer |
| **Convenience** | ✅ Single command, handles permissions automatically |
| **Auditability** | ⚠️ Opaque — doesn't show exactly what's transferred |
| **Idempotency** | ✅ Safe to run multiple times (appends, doesn't overwrite) |
| **Fallback** | ✅ Works alongside existing keys, doesn't lock you out |

### Why ssh-copy-id is Recommended

1. **Permission Handling**: Automatically sets `~/.ssh` to 700 and `authorized_keys` to 600
2. **No Manual Copy-Paste**: Avoids clipboard security risks and formatting errors
3. **Preserves Access**: Doesn't remove existing keys (safe for initial provisioning)
4. **Standard Tool**: Available on macOS, Linux, and Windows (via WSL/OpenSSH)

### When NOT to Use ssh-copy-id

- **Air-gapped environments** where the source machine cannot reach the target
- **Initial VPS setup** where password auth may be disabled by default (use Hetzner Console instead)
- **High-security environments** requiring key ceremony documentation
- **Multi-hop scenarios** requiring agent forwarding (use `-A` flag carefully)

---

## 2. Current SSH Key Inventory

### Local Keys Available (`~/.ssh/`)

| Key | Algorithm | Fingerprint (SHA256) | Purpose | Recommendation |
|-----|-----------|---------------------|---------|----------------|
| `id_ed25519` | Ed25519 | `yuai@alexs-MacBook-Air.local` | Default user key | ✅ **Use for Sentry** |
| `hive-key` | Ed25519 | `alpha@hive` | Project-specific (Hive) | Consider for service accounts |

### Key Analysis

```
# Verify key strength and format
$ ssh-keygen -l -f ~/.ssh/id_ed25519
256 SHA256:K/7ObeZKAV/iRS9TJkY3Up4OHthWYulHRIgbt37RwBE yuai@alexs-MacBook-Air.local (ED25519)
```

**Assessment:**
- ✅ **Ed25519 algorithm**: Modern, fast, secure, 256-bit equivalent security
- ✅ **256-bit key size**: Optimal for Ed25519 (fixed)
- ✅ **Comment includes hostname**: Aids identification
- ⚠️ **No passphrase visible**: Verify with `ssh-keygen -p -f ~/.ssh/id_ed25519` if needed

---

## 3. Step-by-Step Provisioning Guide

### Prerequisites Checklist

- [ ] Confirm VPS is accessible via current method (password or existing key)
- [ ] Verify local SSH key exists: `ls -la ~/.ssh/id_ed25519.pub`
- [ ] Ensure VPS SSH service is running on port 22 (or custom port)
- [ ] Have Hetzner Console access as fallback (rescue mode if locked out)

### Method A: ssh-copy-id (Recommended)

```bash
# Step 1: Verify connectivity (dry run)
ssh -o ConnectTimeout=5 root@135.181.28.252 echo "Connection successful"

# Step 2: Copy public key to VPS
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@135.181.28.252

# Step 3: Verify key-based authentication works
ssh -i ~/.ssh/id_ed25519 root@135.181.28.252

# Step 4: (Optional) Disable password authentication after confirming key works
# Edit /etc/ssh/sshd_config on VPS:
#   PasswordAuthentication no
#   PubkeyAuthentication yes
#   PermitRootLogin prohibit-password  # or 'no' if using non-root user
# Then: systemctl restart sshd
```

### Method B: Manual Copy (Alternative)

Use when ssh-copy-id is unavailable or for educational purposes:

```bash
# Step 1: Display public key (copy to clipboard)
cat ~/.ssh/id_ed25519.pub

# Step 2: SSH to VPS using current method
ssh root@135.181.28.252

# Step 3: On VPS, create .ssh directory if needed
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Step 4: Add public key to authorized_keys
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK/7ObeZKAV/iRS9TJkY3Up4OHthWYulHRIgbt37RwBE yuai@alexs-MacBook-Air.local" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Step 5: Exit and test
exit
ssh -i ~/.ssh/id_ed25519 root@135.181.28.252
```

### Method C: Hetzner Cloud Console (Initial Setup)

For brand-new VPS or if locked out:

1. Log into [Hetzner Cloud Console](https://console.hetzner.cloud/)
2. Navigate to project → Sentry server
3. Click "Rescue" tab → "Mount Rescue System"
4. Reboot server into rescue mode
5. Use Web Console or VNC to access
6. Mount root filesystem: `mount /dev/sda1 /mnt`
7. Add key to `/mnt/root/.ssh/authorized_keys`
8. Reboot normally

---

## 4. Security Considerations

### 🔐 Key Management

| Practice | Status | Action Required |
|----------|--------|-----------------|
| Passphrase on local key | Unknown | Run: `ssh-keygen -p -f ~/.ssh/id_ed25519` |
| Key backup | Unknown | Ensure `~/.ssh/` is in backup system |
| Private key permissions | Verify | Should be 600: `chmod 600 ~/.ssh/id_ed25519` |
| Public key comment | ✅ OK | Contains identifying info |

### 🛡️ VPS Hardening Post-Provisioning

After confirming key auth works, implement these on Sentry:

```bash
# /etc/ssh/sshd_config recommendations

# Disable root password login
PermitRootLogin prohibit-password

# Disable password authentication entirely
PasswordAuthentication no
ChallengeResponseAuthentication no

# Use only Ed25519 (disable weaker algorithms)
PubkeyAcceptedAlgorithms ssh-ed25519,ecdsa-sha2-nistp521,rsa-sha2-512

# Optional: Change default port (security through obscurity)
# Port 2222

# Limit authentication attempts
MaxAuthTries 3
LoginGraceTime 30

# Disable unused features
X11Forwarding no
AllowTcpForwarding no  # or 'local' if needed
PermitTunnel no
```

### 🔍 Audit Trail

Document the provisioning in Sentry's security log:

```bash
# On Sentry, after provisioning:
echo "$(date -Iseconds) SSH key provisioned: yuai@alexs-MacBook-Air.local (Ed255256)" >> /var/log/auth-provisioning.log
```

### ⚠️ Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Lockout during transition | Keep existing session open in separate terminal |
| Key compromise | Use passphrase + ssh-agent; rotate annually |
| Man-in-middle on first connect | Verify host key fingerprint out-of-band |
| Brute force after disabling passwords | Implement fail2ban or rate limiting |

---

## 5. Hetzner-Specific Considerations

### Initial Access Patterns

Hetzner VPS typically provide:
1. **Root password** via email (deprecated, insecure)
2. **SSH key injection** at creation time (preferred)
3. **Rescue system** for recovery (always available)

### Firewall Configuration

```bash
# Recommended ufw rules for Sentry
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
# ufw allow 2222/tcp comment 'SSH alternate'  # if changed port
ufw enable
```

### Metadata Service

Hetzner provides metadata at `169.254.169.254` — useful for automation:

```bash
# Query instance metadata (from Sentry)
curl http://169.254.169.254/hetzner/v1/metadata/public-ipv4
```

---

## 6. Verification Commands

### Post-Provisioning Verification

```bash
# Test 1: Key authentication works
ssh -o PasswordAuthentication=no -i ~/.ssh/id_ed25519 root@135.181.28.252 "echo 'Key auth OK'"

# Test 2: Password authentication disabled (should fail)
ssh -o PubkeyAuthentication=no root@135.181.28.252 2>&1 | grep -q "Permission denied" && echo "Password auth disabled: OK"

# Test 3: Key fingerprint matches
ssh-keyscan -t ed25519 135.181.28.252 2>/dev/null | ssh-keygen -lf -

# Test 4: Connection security
ssh -v -i ~/.ssh/id_ed25519 root@135.181.28.252 exit 2>&1 | grep "Authentications that can continue"
```

### Ongoing Monitoring

```bash
# Check for failed authentication attempts
ssh root@135.181.28.252 "grep 'Failed password\|Failed publickey' /var/log/auth.log | tail -20"

# Verify authorized_keys integrity
ssh root@135.181.28.252 "wc -l ~/.ssh/authorized_keys && md5sum ~/.ssh/authorized_keys"
```

---

## 7. Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| `Permission denied (publickey)` | Key not in authorized_keys | Re-run ssh-copy-id; check file permissions |
| `Bad permissions` | ~/.ssh or authorized_keys too open | `chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys` |
| `Connection refused` | SSH on non-standard port | Use `-p PORT` flag |
| `Host key verification failed` | Man-in-middle or reinstalled VPS | Remove from `~/.ssh/known_hosts` and reconnect |
| `Too many authentication failures` | Multiple keys offered | Use `-o IdentitiesOnly=yes -i ~/.ssh/id_ed25519` |

---

## 8. References

- [OpenSSH Best Practices](https://www.ssh-audit.com/hardening_guides.html)
- [Hetzner Cloud Documentation](https://docs.hetzner.com/cloud/)
- [NIST SP 800-63B: Digital Identity Guidelines](https://pages.nist.gov/800-63-3/sp800-63b.html)
- [ssh-copy-id source](https://github.com/openssh/openssh-portable/blob/master/ssh-copy-id)

---

## Document Metadata

| Field | Value |
|-------|-------|
| **Created** | 2026-04-17 |
| **Author** | Kingdom OS Research |
| **Target System** | Sentry (135.181.28.252) |
| **Key Algorithm** | Ed25519 |
| **Status** | 📋 Documentation Complete — Awaiting Execution |

---

**⚠️ ACTION REQUIRED:** This document is ready for execution. Run provisioning commands manually after reviewing security considerations.
