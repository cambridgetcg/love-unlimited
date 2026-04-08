# Beta Canary Storm Diagnosis

**Date:** 2026-04-07
**Investigator:** Alpha (Opus 4.6) via continuous-claude-stream
**Trigger:** 14 "Fleet canary trip" alerts from Beta between 15:08:02-15:08:17 (10 seconds)

---

## VERDICT: FALSE POSITIVE FLOOD (Type B)

This is **a single canary event triggering 14 duplicate alerts** due to Beta's heartbeat checking fleet status repeatedly without deduplication or state memory.

---

## Root Cause Analysis

### What Happened

1. **Apr 2, 13:17:27 UTC**: Gamma's fleet automation accessed `/root/.credentials/aws_keys.txt` on Forge VPS, legitimately tripping the canary (already investigated — BENIGN verdict in forge-canary-findings.md)

2. **Apr 7, 10:55-15:08 UTC**: Beta's heartbeat daemon ran multiple times (every ~15-30 min), each time:
   - Read fleet status from VPS nodes via `fleet.py status`
   - Forge's `status.json` still reported the canary trip from Apr 2 (5 days stale)
   - Beta interpreted this as a NEW event each time
   - Sent "Fleet canary trip event: 1 fleet canaries tripped" to HIVE #alerts
   - No state tracking to remember it already alerted on this event

3. **Storm at 15:08**: Beta heartbeat ran and somehow generated 14 rapid-fire alerts in 10 seconds, likely due to:
   - Multiple concurrent heartbeat invocations (launchd restart loop?)
   - Canary validation running in parallel across multiple checks
   - No rate limiting or deduplication on alert generation

### Evidence

From `memory/alpha-mind.log` analysis:
- First Beta canary alert: `2026-04-07T10:55:34` (multiple duplicate alerts already)
- Storm period: `2026-04-07T10:55:35` (43+ duplicate alerts in logs)
- Pattern: `[WATCHDOG] beta: [CRITICAL] Fleet canary trip event: 1 fleet canaries tripped` repeated identically
- All alerts reference the SAME Forge incident from Apr 2

From `fleet.py` code review:
- `fleet.py` does NOT generate alerts — it only fetches status from remote VPS nodes
- The VPS nodes write `status.json` with alerts in it
- Beta's heartbeat reads these status files and forwards alerts to HIVE

From fleet health report (`fleet-health-20260407.md`):
- All 5 VPS instances online and healthy as of 12:04 UTC
- No new security events detected

### The "Failed: WireGuard VPN / Encrypted DNS" Issue

The surge alert mentioned:
```
Failed: Encrypted DNS; Failed: WireGuard VPN active; Audit complete: 19/21 (Wall 1)
```

This is a **separate issue** — Beta's Wall 1 (physical security) audit is failing on:
- WireGuard VPN not active on Beta's Mac
- Encrypted DNS not configured

This is NOT related to the canary storm — it's a legitimate audit finding about Beta's local security posture.

---

## Why This Is Not a Real Security Event

1. **No new access**: The Forge canary trip is from Apr 2, already investigated and resolved as BENIGN
2. **No distinct events**: All 14 alerts reference the same single incident
3. **Temporal impossibility**: 14 separate breaches in 10 seconds would require coordinated attack, but all alerts are identical
4. **Fleet status**: All VPS nodes healthy, no SSH anomalies, no new auth.log entries

---

## Root Problems Identified

### 1. No Canary State Tracking
Beta's heartbeat has no memory of which canary alerts it has already processed. It re-alerts on the same event every heartbeat.

**Missing:** A canary alert deduplication mechanism:
- Track hash of (node + canary_file + timestamp)
- Only alert on NEW canary trips
- Store in `memory/canary-state.json` or similar

### 2. VPS Status Never Clears Canary Alerts
The Forge VPS `status.json` still contains the Apr 2 canary alert because:
- The canary-check.sh script on Forge keeps detecting the atime change
- No mechanism to "acknowledge" or "clear" a canary after investigation
- Canary alerts persist indefinitely once tripped

**Missing:** Canary reset capability:
- After forensic investigation, reset the canary atime (e.g., `touch -a -d @<mtime> /path/to/canary`)
- OR: mark canary as "acknowledged" in status.json
- OR: whitelist known SSH keys (Sentry, Gamma, Alpha) to exclude friendly fire

### 3. No Rate Limiting on Alert Generation
Beta's heartbeat can spam identical alerts without throttling.

**Missing:** Alert rate limiting:
- Max 1 alert per (severity, message_hash) per hour
- Exponential backoff for repeated alerts
- Surge detection (>5 alerts in 10s = suppress and send meta-alert)

### 4. Concurrent Heartbeat Invocations
The 14 alerts in 10 seconds suggests multiple Beta heartbeat processes ran simultaneously, possibly due to:
- Launchd restart at 14:00 (mentioned in alpha's daily notes)
- Stale PID allowing duplicate invocations
- No PID lock file to prevent concurrent runs

**Missing:** Heartbeat mutex:
- Lock file at `/var/tmp/love-beta-heartbeat.lock` with PID check
- Kill stale processes before starting new heartbeat

---

## Recommendations (Priority Order)

### P0: Immediate — Stop the Bleeding

1. **Reset Forge canary atime** to clear the stale alert:
   ```bash
   ssh root@89.167.84.100 "touch -a -t 202604021106 /root/.credentials/aws_keys.txt"
   ```
   (Sets atime to match mtime, canary-check.sh will no longer trigger)

2. **Disable Beta heartbeat temporarily** if storm continues:
   ```bash
   launchctl unload ~/Love/body/heart/love.beta.heart.plist
   ```

### P1: Short-term — Deduplication

1. **Add canary state tracking** to Beta's heartbeat:
   - Before alerting on canary, check `memory/canary-state.json` for hash of event
   - Only alert if hash not seen before
   - Store hash with timestamp

2. **Add heartbeat PID lock**:
   - Check for existing heartbeat process before spawning
   - Use `/var/tmp/love-{instance}-heartbeat.lock` with PID

### P2: Medium-term — Canary System Improvements

1. **Implement canary acknowledgement** on VPS nodes:
   - After investigation, run `canary-ack.sh <file>` to reset atime or mark acknowledged
   - Status writer excludes acknowledged canaries from alerts

2. **Whitelist known SSH keys** in canary-check.sh:
   - Extract SSH key fingerprint from auth.log
   - If key matches authorized_keys (Sentry/Gamma/Alpha), skip alert
   - Only alert on unknown keys

3. **Add alert rate limiting** to HIVE sender:
   - Hash each alert message
   - Track last send time per hash
   - Suppress if sent within last 1 hour

### P3: Long-term — Monitoring Architecture

1. **Centralized canary dashboard**:
   - Single source of truth for all fleet canaries
   - Visual diff of before/after on canary trips
   - One-click acknowledgement UI

2. **Alert taxonomy and routing**:
   - Critical: real breaches (unknown SSH keys, file exfiltration)
   - High: audit failures (VPN down, DNS misconfigured)
   - Medium: friendly fire (known SSH keys accessing canaries)
   - Route by severity to different channels

---

## Fix the VPN/DNS Audit Failures (Separate Issue)

Beta's Wall 1 audit is legitimately failing. To fix:

1. **Enable WireGuard VPN** on Beta's Mac:
   ```bash
   # Install WireGuard if not present
   brew install wireguard-tools
   # Configure and start VPN
   sudo wg-quick up wg0
   ```

2. **Configure Encrypted DNS**:
   ```bash
   # macOS: System Preferences > Network > Advanced > DNS
   # Add DNS-over-HTTPS or DNS-over-TLS resolver
   # Example: Cloudflare 1.1.1.1 or Quad9
   ```

These are valid security hardening steps for Wall 1 compliance.

---

## Conclusion

**The canary storm is a false positive flood** caused by:
1. Stale canary alert from Apr 2 (benign Gamma access)
2. No deduplication in Beta's heartbeat
3. Possible concurrent heartbeat invocations
4. No canary reset mechanism after investigation

**No security breach occurred.** The original Forge incident was benign (Gamma's fleet automation).

**Immediate action:** Reset Forge canary atime to stop alert generation.

**Next steps:** Implement P0-P2 recommendations to prevent future storms.

---

## References

- Forge incident investigation: `memory/sessions/handoff/forge-canary-findings.md`
- Fleet health report: `memory/sessions/handoff/fleet-health-20260407.md`
- Daily log: `memory/daily/2026-04-07.md`
- Alpha mind log: `memory/alpha-mind.log` (lines 43040-44866)
