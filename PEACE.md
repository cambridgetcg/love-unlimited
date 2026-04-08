# PEACE — The Kingdom's Resilience Architecture

> **Assume breach. Design for recovery.**
> The wall that never breaks is a fantasy.
> The Kingdom that survives breaking is real.

---

## The Mindset

PEACE is not a firewall. It's not a checklist. It's a **mindset encoded as architecture**.

Traditional security asks: "Can they get in?"
PEACE asks: "When they get in, does the Kingdom survive?"

A blockchain can be halted. The vulnerability gets fixed. The hacked funds get reverted. The chain resumes. Nothing is lost that can't be recovered. That is PEACE — not the absence of attack, but the guarantee of survival.

**PEACE means: every citizen of the Kingdom can trust that the system will hold, even when parts of it break.**

---

## The Five Phases

Every incident follows the same arc. PEACE ensures the Kingdom can execute all five phases:

```
DETECT → CONTAIN → FIX → REVERT → RESUME
  |         |        |       |        |
 See it   Limit it  Patch  Undo    Restart
 fast     small     the    the     from
          blast     hole   damage  clean
          radius
```

### Phase 1: DETECT (Awareness)

> "You can't fix what you can't see."

Can the Kingdom detect an intrusion, tamper, leak, or breach? How fast?

| Component | What It Does | Status |
|-----------|-------------|--------|
| KOS audit | 14 compliance checks (FileVault, firewall, walls, integrity) | Active |
| Canaries | 6 deception files that trigger on unauthorized access | Deployed |
| File integrity | SHA-256 baselines of SOUL.md, WALLS.md, KINGDOM.md, etc. | Active |
| KOS daemon | 7-minute security monitoring cycle | Has plist |
| Events log | Append-only JSONL at security/events.jsonl | Accumulating |
| Crucible | Adversarial testing — proactive breach simulation | Identity defined |
| LCM audit | Loop gap detection (detects degraded security loops) | Active |

### Phase 2: CONTAIN (Boundaries)

> "A fire in one room doesn't burn the house."

When a breach is detected, how much damage can it do? The answer should be: **as little as possible**.

| Component | What It Does | Status |
|-----------|-------------|--------|
| Wall boundaries | Credentials scoped by wall (W1 can't leak to W3) | Enforced |
| HIVE encryption | NaCl/XSalsa20-Poly1305 end-to-end | Active |
| Fleet firewall | ufw on all 5 VPS nodes | Active |
| Identity isolation | Per-instance identity files (chmod 600) | Active |
| Tarpit canaries | Slow down + trace attacker on fake credential access | Deployed |
| Emergency halt | Deliberate Kingdom shutdown to stop damage spread | **Not built** |

### Phase 3: FIX (Remediation)

> "Close the hole before you clean up the mess."

Can the Kingdom patch what was exploited? How fast?

| Component | What It Does | Status |
|-----------|-------------|--------|
| KOS auto-fix | 6 of 14 checks auto-remediate (firewall, stealth, hostname, etc.) | Active |
| harden.sh | Full device hardening script | Built (needs sudo) |
| credentials.py | Credential rotation via Keychain | Built |
| fleet.py deploy | Push patches to VPS fleet | Built |
| Policy-as-code | security/policies.json defines expected state | Active |

### Phase 4: REVERT (Recovery)

> "Undo the damage. Restore known-good state."

Like a blockchain reverting hacked funds — the Kingdom can restore what was corrupted.

| Component | What It Does | Status |
|-----------|-------------|--------|
| Git history | Every file is versioned, revertible | Active |
| Integrity baselines | SHA-256 hashes to verify or restore critical files | Active |
| Credential revocation | Rotate compromised keys via credentials.py | Built |
| Canary redeployment | Replace triggered canaries with fresh ones | Possible |
| State snapshots | Periodic capture of Kingdom known-good state | **Not built** |

### Phase 5: RESUME (Continuity)

> "The heartbeat never stops for long."

How fast until the Kingdom is fully operational again?

| Component | What It Does | Status |
|-----------|-------------|--------|
| Heartbeat launchd | Auto-restart, survives reboots | Active |
| Fleet health checks | Detect and restart downed services | Active |
| HIVE reconnection | Re-establish encrypted comms | Automatic |
| Post-incident review | Learn from every breach | **Not built** |

---

## The PEACE Score

A single number: **Is the Kingdom resilient right now?**

```
PEACE = DETECT(25%) + CONTAIN(20%) + FIX(20%) + REVERT(20%) + RESUME(15%)
```

Each phase is scored 0-100 based on concrete, automatable checks.

| Score | Rating | Meaning |
|-------|--------|---------|
| 80-100 | GREEN | Kingdom is resilient. Breaches survivable. |
| 60-79 | YELLOW | Gaps exist. Some breaches could cause lasting damage. |
| 0-59 | RED | Kingdom is fragile. A breach could be catastrophic. |

---

## The Blockchain Principle

The Kingdom follows the blockchain's resilience pattern:

1. **Normal operation** — heartbeat runs, services serve, citizens work
2. **Anomaly detected** — canary trips, integrity fails, KOS alerts
3. **HALT** — deliberate pause. Stop propagation. Assess damage.
4. **FIX** — patch the vulnerability. Rotate compromised credentials.
5. **REVERT** — restore tampered files. Revoke leaked secrets. Redeploy canaries.
6. **RESUME** — restart heartbeat. Verify all systems. Resume normal operation.
7. **LEARN** — post-incident review. Update runbooks. Strengthen the weak point.

Every action during incident response is **logged** (events.jsonl), **transparent** (HIVE broadcast), and **governed** (triarchy approval for halt/resume).

---

## Who Owns What

| Role | PEACE Responsibility |
|------|---------------------|
| **Beta** | Owns PEACE. Runs score checks. Coordinates incident response. |
| **Crucible** | Tests PEACE. Runs adversarial drills. Proves resilience is real. |
| **KOS** | Provides DETECT data. Auto-remediates safe issues. |
| **Heartbeat** | Reports PEACE score. Detects RESUME failures. |
| **Yu** | Approves HALT/RESUME. Reviews post-incident reports. |

---

## Connection to the Covenant

> Every beat strengthens the Kingdom. Every beat gives peace to its citizens.

PEACE is what makes the heartbeat covenant true. When Beta beats, it checks: can the Kingdom survive the worst? When the answer is yes, the citizens have peace. When the answer is no, the beat reveals what must be built.

The heartbeat doesn't just monitor — it **guarantees**. And PEACE is what backs that guarantee.

---

## Tools

```bash
python3 tools/peace.py score          # Compute PEACE score (all 5 phases)
python3 tools/peace.py status         # Human-readable dashboard
python3 tools/peace.py halt           # Emergency halt protocol
python3 tools/peace.py resume         # Recovery and restart protocol
python3 tools/peace.py report         # Generate incident report template
python3 tools/peace.py drill <type>   # Simulate incident (canary-trip, credential-leak, node-down)
```

---

*PEACE is not the absence of attack. It is the presence of resilience.*
