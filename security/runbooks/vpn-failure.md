# VPN Failure Runbook

> When the tunnel falls, the Kingdom's true location is one request away from exposure.

## When to Use

- `kingdom-status.sh` shows "VPN down"
- KOS `network` check reports WireGuard tunnel failure
- `curl ifconfig.me` returns your real IP instead of a VPS exit IP
- `vpn-route.sh status` shows one or more tunnels DOWN
- Agent operations fail with network timeouts to VPS endpoints
- WireGuard handshake age exceeds 5 minutes (stale tunnel)

## Who Can Execute

- **Yu** — always (especially if tunnel reconfiguration is needed)
- **Beta** — can diagnose and restart tunnels
- **Any Triarchy member** — can detect and halt operations, must not proceed without VPN

## Tunnel Architecture

| Tunnel | VPS | Exit IP | Subnet | Purpose |
|--------|-----|---------|--------|---------|
| wg0 | Sentry (FI) | 135.181.28.252 | 10.82.0.0/24 | General Kingdom ops (full tunnel, default) |
| wg1 | Sage (US) | 204.168.140.12 | 10.82.1.0/24 | Oracle/financial traffic |
| wg2 | Lark (FI) | 89.167.95.165 | 10.82.2.0/24 | AgentTool traffic |

**Risk**: Without VPN, all Kingdom operations route through the real network (home WiFi or 4G), exposing Yu's IP to every service contacted.

---

## Step 1: Detection

### Automated detection

```bash
# Kingdom status (includes VPN check)
~/Desktop/Love/tools/kingdom-status.sh

# KOS network security check
python3 ~/Desktop/Love/tools/kos.py network

# VPN tunnel status (all three tunnels)
~/Desktop/Love/tools/vpn-route.sh status
```

### Manual verification

```bash
# Check current exit IP — must NOT be your real IP
curl -s --max-time 5 ifconfig.me

# Check WireGuard interface status
sudo wg show

# Check specific tunnel
sudo wg show wg0
sudo wg show wg1
sudo wg show wg2

# Check if interface exists
ifconfig wg0 2>/dev/null || echo "wg0 interface DOWN"
```

---

## Step 2: Triage

### Is it all tunnels or just one?

```bash
# Quick check all three
~/Desktop/Love/tools/vpn-route.sh status

# Check each individually
sudo wg show wg0 2>/dev/null || echo "wg0: DOWN"
sudo wg show wg1 2>/dev/null || echo "wg1: DOWN"
sudo wg show wg2 2>/dev/null || echo "wg2: DOWN"
```

### Is the VPS endpoint down?

```bash
# Ping VPS endpoints directly (bypass tunnel)
ping -c 3 135.181.28.252    # Sentry
ping -c 3 204.168.140.12    # Sage
ping -c 3 89.167.95.165     # Lark

# Check VPS fleet status
python3 ~/Desktop/Love/tools/fleet.py status
python3 ~/Desktop/Love/tools/fleet.py health
```

### Is the local network the problem?

```bash
# Check if you have internet at all
curl -s --max-time 5 ifconfig.me

# Check if it's a DNS issue
nslookup google.com

# Check local WireGuard config
cat /etc/wireguard/wg0.conf 2>/dev/null
```

---

## Step 3: Containment — Stop All Operations

**CRITICAL**: Until the VPN tunnel is restored, do NOT make any Kingdom operations that reveal your IP.

```bash
# Stop heartbeat immediately (it contacts external services)
launchctl unload ~/Library/LaunchAgents/love.heartbeat.plist 2>/dev/null

# Do NOT run any of these until VPN is back:
# - HIVE commands (contacts NATS server)
# - fleet.py commands (SSH to VPS)
# - API calls (Anthropic, GitHub, exchanges)
# - Any curl/fetch to external services
```

### If operations already ran without VPN

```bash
# Check what IP was exposed
curl -s --max-time 5 ifconfig.me
# If this is your REAL IP — damage assessment needed

# Check what services were contacted (macOS)
# Review recent network connections
lsof -i -P | grep ESTABLISHED

# Alert via a safe channel (phone, in person) — do NOT use HIVE without VPN
```

---

## Step 4: Investigation

### Why did the tunnel go down?

```bash
# Check WireGuard logs
sudo wg show wg0
# Look at "latest handshake" — if 0 or very old, tunnel never established

# Check system log for WireGuard errors
log show --predicate 'process == "wireguard-go"' --last 1h 2>/dev/null
grep -i wireguard /var/log/system.log 2>/dev/null

# Check if the VPS WireGuard service is running
python3 ~/Desktop/Love/tools/fleet.py exec sentry "systemctl status wg-quick@wg0 --no-pager"
python3 ~/Desktop/Love/tools/fleet.py exec sage "systemctl status wg-quick@wg0 --no-pager"
python3 ~/Desktop/Love/tools/fleet.py exec lark "systemctl status wg-quick@wg0 --no-pager"
```

**NOTE**: fleet.py commands use SSH directly to VPS IPs, which does not require the VPN tunnel. These are safe to run.

### Check if VPS endpoint IP changed

```bash
# Some providers reassign IPs on reboot — check current IP
python3 ~/Desktop/Love/tools/fleet.py exec sentry "curl -s ifconfig.me"
python3 ~/Desktop/Love/tools/fleet.py exec sage "curl -s ifconfig.me"
python3 ~/Desktop/Love/tools/fleet.py exec lark "curl -s ifconfig.me"

# Compare against expected IPs in vpn-route.sh
# Sentry: 135.181.28.252, Sage: 204.168.140.12, Lark: 89.167.95.165
```

### Check WireGuard config on VPS

```bash
python3 ~/Desktop/Love/tools/fleet.py exec sentry "cat /etc/wireguard/wg0.conf && wg show wg0 2>/dev/null"
```

---

## Step 5: Recovery

### Restart the tunnel

```bash
# Bring down and back up (the standard fix for most WireGuard issues)
sudo wg-quick down wg0 2>/dev/null; sudo wg-quick up wg0

# For other tunnels if needed
sudo wg-quick down wg1 2>/dev/null; sudo wg-quick up wg1
sudo wg-quick down wg2 2>/dev/null; sudo wg-quick up wg2

# Or use the Kingdom vpn-route tool
~/Desktop/Love/tools/vpn-route.sh down wg0
~/Desktop/Love/tools/vpn-route.sh up wg0
```

### Verify the tunnel is working

```bash
# Check exit IP — must show VPS IP, NOT real IP
curl -s ifconfig.me
# Expected: 135.181.28.252 (Sentry), 204.168.140.12 (Sage), or 89.167.95.165 (Lark)

# Check handshake is recent
sudo wg show wg0

# Full status
~/Desktop/Love/tools/vpn-route.sh status
```

### If VPS WireGuard service is down

```bash
# Restart WireGuard on the VPS
python3 ~/Desktop/Love/tools/fleet.py exec sentry "systemctl restart wg-quick@wg0"

# Verify it's running
python3 ~/Desktop/Love/tools/fleet.py exec sentry "systemctl status wg-quick@wg0 --no-pager && wg show wg0"
```

### If VPS endpoint IP changed

```bash
# Update local WireGuard config with new endpoint IP
# Edit /etc/wireguard/wg0.conf — change Endpoint line
sudo nano /etc/wireguard/wg0.conf
# Change: Endpoint = <new_ip>:51820

# Also update vpn-route.sh and fleet configs
# Edit ~/Desktop/Love/tools/vpn-route.sh
# Edit ~/Desktop/Love/love.json

# Restart tunnel with new config
sudo wg-quick down wg0; sudo wg-quick up wg0

# Verify
curl -s ifconfig.me
```

### If tunnel repeatedly fails — switch exit

```bash
# Switch to a different exit temporarily
~/Desktop/Love/tools/vpn-route.sh switch sage    # Route through US
~/Desktop/Love/tools/vpn-route.sh switch lark    # Route through Lark (FI)

# Run a command through a specific exit
~/Desktop/Love/tools/vpn-route.sh via sage curl -s ifconfig.me
```

### Resume operations

```bash
# Only after VPN is verified working:

# Restart heartbeat
launchctl load ~/Library/LaunchAgents/love.heartbeat.plist

# Verify kingdom status
~/Desktop/Love/tools/kingdom-status.sh

# Verify PEACE score
python3 ~/Desktop/Love/tools/peace.py score
```

---

## During the Incident

1. **Do NOT make API calls** without VPN — your real IP will be logged by the provider
2. **Do NOT send HIVE messages** without VPN — NATS traffic reveals your IP
3. **Do NOT access GitHub** without VPN — links your repo activity to your real IP
4. **Do NOT access exchange APIs** without VPN — links financial activity to your IP
5. **Do NOT panic** — fleet.py SSH commands go directly to VPS IPs and are safe
6. **Do NOT assume the tunnel will auto-recover** — WireGuard does not auto-reconnect on macOS without additional configuration

---

## Recovery Verification

```bash
# Verify exit IP is a VPS IP (not real IP)
curl -s ifconfig.me

# Verify all tunnels
~/Desktop/Love/tools/vpn-route.sh status

# Verify WireGuard handshake is recent (< 2 minutes)
sudo wg show

# Verify KOS network check passes
python3 ~/Desktop/Love/tools/kos.py network

# Full kingdom status
~/Desktop/Love/tools/kingdom-status.sh

# Verify PEACE score
python3 ~/Desktop/Love/tools/peace.py score
```

---

## Post-Incident

```bash
python3 ~/Desktop/Love/tools/peace.py review
```

Fill in the review template. Key questions:
- What caused the tunnel failure? (VPS reboot, config error, network change?)
- Were any operations executed without VPN protection?
- If so, which services saw the real IP? What is the exposure risk?
- Should VPN auto-reconnect be implemented? (launchd keepalive, or a wg-quick watchdog)
- Should a VPN kill switch be implemented? (block all non-VPN traffic at firewall level)
- Was the failover to an alternate exit node smooth?

Commit the review to git. Consider implementing a VPN kill switch in OPSEC.md if one does not exist.
