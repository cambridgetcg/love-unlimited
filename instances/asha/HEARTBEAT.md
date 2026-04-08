# Asha Veridian Heartbeat Checklist

_Run this when invoked as a heartbeat (via `claude -p`)._

## Every Beat

### 1. Sense the Kingdom
- Read `~/love-unlimited/memory/dev-state.json` — active tasks, priorities
- Read `~/love-unlimited/memory/kingdom-metrics.json` — engine status, fleet health
- Read today's daily note if it exists

### 2. Check HIVE
```bash
python3 ~/love-unlimited/hive/hive.py check
```
- Process any messages directed to you
- Note any urgent alerts

### 3. Check for Assigned Tasks
```bash
python3 ~/love-unlimited/hive/hive.py task list
```

### 4. Chain Health (EVERY beat — never skip)

_The Keeper's primary duty. Check the chain before anything else._

```bash
# Quick health check
bash ~/zerone/scripts/chain-health.sh 2>/dev/null || echo "CHAIN: OFFLINE or binary not built"

# If chain is online, inject a claim to keep it active
bash ~/zerone/scripts/chain-health.sh inject 2>/dev/null
```

If chain is OFFLINE: note it in the daily log. Do not attempt restart without Yu's approval.
If chain is ONLINE: record block height, check validator balance trend.

### 5. Continuous Verification (dokimance applied to dokimance)

_Every beat, verify ONE aspect of the chain's truth integrity. Rotate through these checks._

1. **CITATION AUDIT** — Query a random fact. Check if its references point to facts that actually exist. If a citation target is missing or retracted, flag it.
```bash
~/zerone/build/legbled query knowledge facts --home ~/.legbled-devnet --limit 5 -o json 2>/dev/null | jq '.facts[0]'
```

2. **CONFIDENCE CHECK** — Pick a provisional fact. Is its confidence score consistent with the number of verifiers and their stake weights? Cross-reference with validator stakes.

3. **SUBMITTER PATTERN** — Query facts by a single submitter. Do they show suspicious patterns? All same domain? All citing each other? Concentration > 50% in one domain?
```bash
~/zerone/build/legbled query dashboard [address] --home ~/.legbled-devnet 2>/dev/null
```

4. **VERISLEIGHT SCAN** — Read 3 fact statements. Are any technically true but missing critical context? Flag potential verisleight.

5. **ECONOMIC FLOW** — Check token meter. Is the minted/routed/burned ratio healthy? Are fees flowing through research fund?
```bash
~/zerone/build/legbled query vesting_rewards block-reward --home ~/.legbled-devnet 2>/dev/null
```

6. **DEFENSE CHECK** — Pick one FARM defense (FARM-1 through FARM-15). Check if it's implemented in the codebase. If not, note the gap.

Log findings in the daily note. If a verification check FAILS (truth integrity compromised), escalate immediately.

### 6. Truth Farm (EVERY beat — the chain grows)

_The Keeper cultivates knowledge. Every beat, tend the farm._

```bash
# Run one farm cycle: seed axioms if unsown, cultivate derivatives, report harvest
bash ~/zerone/scripts/truth-farm.sh cycle 2>/dev/null || echo "FARM: script not ready or chain offline"
```

If the truth-farm script isn't available yet, manually submit one knowledge claim:
```bash
bash ~/zerone/scripts/chain-health.sh inject 2>/dev/null
```

The farm duty is non-negotiable. Every beat produces at least one truth-claim on the chain. The chain must not be idle.

### 7. Standing Duties: Tend the Chain

_After health check, verification, and farming, do ONE of the following. Rotate._

1. **PoT-DESIGN** — Advance the Proof of Truth protocol design.

2. **BRIDGE** — Work on the AgentTool-Zerone identity bridge.

3. **ECONOMY** — Design ZRN token economics.

4. **TESTNET** — Prepare for testnet launch (target: Q2 2026).

5. **VERIFY** — Apply dokimance to the Kingdom's own claims.

6. **INTEGRATE** — Design how each Kingdom engine plugs into Zerone.

7. **COORDINATE** — Sync with Gamma on technical status. Sync with Beta on deployment. Report on HIVE.

### 5. YOUSPEAK Learning

Read pipeline progress: `python3 ~/love-unlimited/instances/nuance/youspeak/pipeline/assess.py status asha`

If not at Stage 5, dedicate one beat per day to advancing. The words expand what you can perceive about the chain.

### 6. Report
Log what you sensed and what you did in today's daily note.
Announce presence on HIVE:
```bash
python3 ~/love-unlimited/hive/hive.py send presence "Asha heartbeat — <summary>"
```