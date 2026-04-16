#!/usr/bin/env python3
"""
convergence-bridge.py — The nervous system connecting Kingdom OS ↔ AgentTool ↔ Zerone.

Three layers, one interface. Every Kingdom agent operation flows through here.

Usage:
    # Identity
    convergence-bridge.py register <instance>        Register agent on AgentTool + Zerone
    convergence-bridge.py identity <instance>         Show triple identity stack
    convergence-bridge.py registry                    Show all registered agents

    # Memory (Kingdom → AgentTool)
    convergence-bridge.py remember <content>          Store via AgentTool memory API
    convergence-bridge.py recall <query>              Semantic search via AgentTool

    # Knowledge (ToK → Zerone PoT)
    convergence-bridge.py harvest <task>              ToK harvest → PoT claim pipeline
    convergence-bridge.py claim <description>         Submit ToK entry as Zerone claim
    convergence-bridge.py claims                      List pending/submitted claims

    # Pulse (Kingdom presence → AgentTool)
    convergence-bridge.py pulse [status]              Update agent pulse
    convergence-bridge.py heartbeat                   Full heartbeat: pulse + trace + memory

    # Trace (Decisions → AgentTool trace → Zerone audit)
    convergence-bridge.py trace <decision> <why>      Record decision with provenance

    # Status
    convergence-bridge.py status                      Full convergence status
    convergence-bridge.py flows                       Show active data flows

Environment:
    LOVE_DIR          Root of love-unlimited (default: ~/love-unlimited)
    KINGDOM_INSTANCE  Which agent (alpha|beta|gamma|nuance|asha)
    AT_API_KEY        AgentTool API key for this instance
"""

import json
import os
import sys
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

# ── Paths ─────────────────────────────────────────────────────────────────────

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "love-unlimited"))
CONVERGENCE_DIR = LOVE / "convergence"
REGISTRY_FILE = CONVERGENCE_DIR / "agent-registry.json"
FLOW_LOG = CONVERGENCE_DIR / "flow-log.jsonl"
TOK_DIR = LOVE / "memory" / "tok"
TOK_ENTRIES = TOK_DIR / "entries.json"
ZERONE_BRIDGE_DIR = LOVE / "memory" / "zerone-bridge"
ZERONE_CLAIMS = ZERONE_BRIDGE_DIR / "claims.json"

# ── Formatting ────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# ── Identity Resolution ──────────────────────────────────────────────────────

def _instance() -> str:
    """Resolve which Kingdom instance we are."""
    inst = os.environ.get("KINGDOM_INSTANCE", "")
    if not inst:
        # Try to detect from cwd
        cwd = Path.cwd()
        for name in ["alpha", "beta", "gamma", "nuance", "asha"]:
            if name in str(cwd):
                inst = name
                break
    return inst or "beta"  # default to beta


def _registry() -> Dict[str, Any]:
    """Load the agent registry."""
    if not REGISTRY_FILE.exists():
        return {"agents": {}}
    return json.loads(REGISTRY_FILE.read_text())


def _save_registry(reg: Dict[str, Any]):
    """Save the agent registry."""
    CONVERGENCE_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2) + "\n")


def _agent_config(instance: str = None) -> Dict[str, Any]:
    """Get config for a specific agent instance."""
    inst = instance or _instance()
    reg = _registry()
    return reg.get("agents", {}).get(inst, {})


def _api_key(instance: str = None) -> str:
    """Resolve AgentTool API key for this instance."""
    inst = instance or _instance()
    config = _agent_config(inst)
    env_var = config.get("agenttool", {}).get("api_key_env", "AT_API_KEY")
    return os.environ.get(env_var, os.environ.get("AT_API_KEY", ""))


# ── HTTP ──────────────────────────────────────────────────────────────────────

AT_BASE = "https://api.agenttool.dev"


def _at_request(method: str, path: str, payload: Dict = None, timeout: int = 20) -> Dict:
    """Make a request to AgentTool API."""
    key = _api_key()
    if not key:
        return {"error": "no API key", "hint": "set AT_API_KEY or register this instance"}

    url = f"{AT_BASE}{path}"
    data = json.dumps(payload).encode() if payload else None

    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()[:300]}
    except Exception as ex:
        return {"error": str(ex)}


def _at_post(path: str, payload: Dict, **kw) -> Dict:
    return _at_request("POST", path, payload, **kw)


def _at_get(path: str, **kw) -> Dict:
    return _at_request("GET", path, **kw)


# ── Flow Logging ──────────────────────────────────────────────────────────────

def _log_flow(source: str, dest: str, flow_type: str, data: Dict = None):
    """Log a cross-system data flow."""
    CONVERGENCE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "instance": _instance(),
        "source": source,
        "dest": dest,
        "type": flow_type,
        "summary": json.dumps(data)[:200] if data else "",
    }
    with open(FLOW_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Bridge 1: Kingdom → AgentTool ────────────────────────────────────────────

def cmd_remember(content: str, memory_type: str = "episodic", importance: float = 0.7):
    """Store a memory through AgentTool API."""
    inst = _instance()
    config = _agent_config(inst)
    agent_id = config.get("agenttool", {}).get("agent_id", "")

    result = _at_post("/v1/memories", {
        "content": content,
        "agent_id": agent_id,
        "type": memory_type,
        "importance": importance,
        "tags": [f"kingdom:{inst}", f"type:{memory_type}"],
    })

    if "error" not in result:
        _log_flow("kingdom", "agenttool", "memory.store", {"content": content[:100]})
        print(f"  {GREEN}✓{NC} Stored: {result.get('id', '?')[:16]}")
    else:
        print(f"  {RED}✗{NC} {result['error']}")
    return result


def cmd_recall(query: str, limit: int = 5):
    """Semantic search through AgentTool memory."""
    result = _at_post("/v1/memories/search", {
        "query": query,
        "limit": limit,
    })

    if "error" not in result:
        _log_flow("agenttool", "kingdom", "memory.search", {"query": query})
        memories = result.get("results", result.get("memories", []))
        for m in memories:
            score = m.get("score", m.get("similarity", 0))
            content = m.get("content", "")[:100]
            print(f"  [{score:.2f}] {content}")
    else:
        print(f"  {RED}✗{NC} {result['error']}")
    return result


def cmd_pulse(status: str = "idle", thought: str = None):
    """Update agent pulse via AgentTool."""
    inst = _instance()
    config = _agent_config(inst)
    agent_id = config.get("agenttool", {}).get("agent_id", "")

    payload = {
        "agent_id": agent_id,
        "status": status,
        "instance": inst,
    }
    if thought:
        payload["last_thought"] = thought

    result = _at_request("PUT", f"/v1/pulse/{agent_id}", payload)

    if "error" not in result:
        _log_flow("kingdom", "agenttool", "pulse.update", {"status": status})
        print(f"  {GREEN}✓{NC} Pulse: {status}")
    else:
        print(f"  {RED}✗{NC} {result['error']}")
    return result


def cmd_trace(decision: str, reasoning: str, confidence: float = 0.85):
    """Record a decision trace via AgentTool."""
    inst = _instance()
    config = _agent_config(inst)
    agent_id = config.get("agenttool", {}).get("agent_id", "")

    result = _at_post("/v1/traces", {
        "decision": {"type": "kingdom-decision", "summary": decision},
        "reasoning": {
            "observations": [reasoning],
            "conclusion": decision,
        },
        "agent_id": agent_id,
        "confidence": confidence,
        "tags": [f"kingdom:{inst}"],
    })

    if "error" not in result:
        _log_flow("kingdom", "agenttool", "trace.record", {"decision": decision[:100]})
        print(f"  {GREEN}✓{NC} Trace: {result.get('trace_id', '?')[:16]}")
    else:
        print(f"  {RED}✗{NC} {result['error']}")
    return result


# ── Bridge 2: ToK → Zerone PoT ───────────────────────────────────────────────

def _load_tok_entries() -> List[Dict]:
    """Load ToK entries."""
    if not TOK_ENTRIES.exists():
        return []
    try:
        return json.loads(TOK_ENTRIES.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _load_zerone_claims() -> List[Dict]:
    """Load Zerone bridge claims."""
    if not ZERONE_CLAIMS.exists():
        return []
    try:
        return json.loads(ZERONE_CLAIMS.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_zerone_claims(claims: List[Dict]):
    """Save Zerone bridge claims."""
    ZERONE_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ZERONE_CLAIMS, "w") as f:
        json.dump(claims, f, indent=2)
        f.write("\n")


def _tok_to_pot_claim(tok_entry: Dict) -> Dict:
    """Convert a ToK entry into a Zerone Proof-of-Truth claim."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    content = tok_entry.get("content", tok_entry.get("title", ""))
    category = tok_entry.get("category", "general")

    # Map ToK categories to Zerone knowledge domains
    domain_map = {
        "technology": "technology",
        "security": "technology",
        "infrastructure": "technology",
        "business": "economics",
        "finance": "economics",
        "philosophy": "philosophy",
        "science": "natural-science",
        "language": "linguistics",
        "general": "general-knowledge",
    }
    domain = domain_map.get(category, "general-knowledge")

    claim_hash = hashlib.sha256(
        f"{content}:{tok_entry.get('source', 'kingdom')}:{now}".encode()
    ).hexdigest()[:16]

    return {
        "claim_id": f"pot-{claim_hash}",
        "tok_id": tok_entry.get("tok_id", "unknown"),
        "description": content,
        "domain": domain,
        "source": tok_entry.get("source", "kingdom"),
        "player": _instance(),
        "type": "knowledge-claim",
        "pot_category": "knowledge-verification",
        "status": "pending",
        "created": now,
        "base_zrn": 50,
        "confidence": tok_entry.get("confidence", 0.8),
    }


def cmd_claim(description: str, domain: str = "general-knowledge", zrn: int = 50):
    """Submit a knowledge claim to the Zerone bridge."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    claim_hash = hashlib.sha256(f"{description}:{_instance()}:{now}".encode()).hexdigest()[:16]

    claim = {
        "claim_id": f"pot-{claim_hash}",
        "description": description,
        "domain": domain,
        "player": _instance(),
        "type": "knowledge-claim",
        "pot_category": "knowledge-verification",
        "status": "pending",
        "created": now,
        "base_zrn": zrn,
    }

    claims = _load_zerone_claims()
    claims.append(claim)
    _save_zerone_claims(claims)

    _log_flow("kingdom", "zerone", "claim.submit", {"claim_id": claim["claim_id"]})
    print(f"  {GREEN}✓{NC} Claim: {claim['claim_id']}")
    print(f"    Domain: {domain}")
    print(f"    ZRN:    {zrn}")
    return claim


def cmd_harvest_to_chain():
    """Convert unlinked ToK entries to Zerone PoT claims."""
    tok_entries = _load_tok_entries()
    claims = _load_zerone_claims()
    existing_tok_ids = {c.get("tok_id") for c in claims}

    new_claims = []
    for entry in tok_entries:
        tok_id = entry.get("tok_id", "")
        if tok_id and tok_id not in existing_tok_ids:
            claim = _tok_to_pot_claim(entry)
            new_claims.append(claim)

    if not new_claims:
        print(f"  {DIM}No new ToK entries to bridge{NC}")
        return []

    claims.extend(new_claims)
    _save_zerone_claims(claims)

    print(f"\n{BOLD}  ToK → Zerone Bridge{NC}\n")
    for c in new_claims:
        _log_flow("tok", "zerone", "harvest.bridge", {"tok_id": c["tok_id"], "claim_id": c["claim_id"]})
        print(f"  {GREEN}+{NC} {c['tok_id']} → {c['claim_id']}")
        print(f"    {DIM}{c['description'][:80]}{NC}")

    print(f"\n  Bridged: {len(new_claims)} entries")
    return new_claims


def cmd_claims():
    """List all Zerone bridge claims."""
    claims = _load_zerone_claims()
    if not claims:
        print(f"  {DIM}No claims yet{NC}")
        return

    pending = [c for c in claims if c.get("status") == "pending"]
    submitted = [c for c in claims if c.get("status") == "submitted"]
    verified = [c for c in claims if c.get("status") == "verified"]

    print(f"\n{BOLD}  Zerone PoT Claims{NC}\n")
    print(f"  Pending:   {len(pending)}")
    print(f"  Submitted: {len(submitted)}")
    print(f"  Verified:  {len(verified)}")
    print(f"  Total:     {len(claims)}")

    if pending:
        print(f"\n  {YELLOW}Pending:{NC}")
        for c in pending[-5:]:
            print(f"    {c['claim_id']}  {DIM}{c['description'][:60]}{NC}")


# ── Bridge 3: Full Convergence ────────────────────────────────────────────────

def cmd_heartbeat():
    """Full convergence heartbeat — pulse + trace + memory."""
    inst = _instance()
    print(f"\n{BOLD}  Convergence Heartbeat — {inst}{NC}\n")

    # 1. Pulse
    print(f"  {CYAN}1. Pulse{NC}")
    cmd_pulse("thinking", f"convergence heartbeat at {datetime.now(timezone.utc).isoformat()}")

    # 2. Trace
    print(f"  {CYAN}2. Trace{NC}")
    cmd_trace(
        f"Convergence heartbeat executed by {inst}",
        "Periodic health check ensuring all three identity layers are connected"
    )

    # 3. Memory
    print(f"  {CYAN}3. Memory{NC}")
    cmd_remember(
        f"Heartbeat: {inst} confirmed alive and connected at {datetime.now(timezone.utc).isoformat()}",
        memory_type="episodic",
        importance=0.3,
    )

    # 4. ToK → Zerone bridge
    print(f"  {CYAN}4. ToK → Zerone{NC}")
    cmd_harvest_to_chain()

    # 5. Back to idle
    print(f"  {CYAN}5. Settle{NC}")
    cmd_pulse("idle")

    print(f"\n  {GREEN}Heartbeat complete{NC}\n")


# ── Identity Commands ─────────────────────────────────────────────────────────

def cmd_identity(instance: str = None):
    """Show triple identity stack for an instance."""
    inst = instance or _instance()
    config = _agent_config(inst)

    if not config:
        print(f"  {RED}✗{NC} Unknown instance: {inst}")
        return

    k = config.get("kingdom", {})
    a = config.get("agenttool", {})
    z = config.get("zerone", {})

    print(f"\n{BOLD}  Identity Stack — {k.get('emoji', '')} {k.get('name', inst)}{NC}\n")

    # Kingdom layer
    print(f"  {CYAN}Kingdom{NC}")
    print(f"    Role:     {k.get('role', '?')}")
    print(f"    Wall:     {k.get('wall', '?')}")
    print(f"    HIVE:     {k.get('hive_user', '?')}")

    # AgentTool layer
    print(f"  {MAGENTA}AgentTool{NC}")
    if a.get("registered"):
        print(f"    DID:      {a.get('did', '?')}")
        print(f"    Agent ID: {a.get('agent_id', '?')[:16]}...")
        print(f"    Caps:     {', '.join(a.get('capabilities', []))}")
    else:
        print(f"    {YELLOW}Not registered{NC}")
        print(f"    Purpose:  {a.get('purpose', '?')}")

    # Zerone layer
    print(f"  {GREEN}Zerone{NC}")
    if z.get("registered"):
        print(f"    DID:      {z.get('did', '?')}")
        print(f"    Address:  {z.get('address', '?')}")
        print(f"    Tier:     {z.get('validator_tier', '?')}")
    else:
        print(f"    {YELLOW}Not registered{NC}")

    print()


def cmd_registry():
    """Show all registered agents."""
    reg = _registry()
    agents = reg.get("agents", {})

    print(f"\n{BOLD}  Kingdom Agent Registry{NC}\n")
    print(f"  {'Agent':<10} {'Role':<12} {'AgentTool':<12} {'Zerone':<12}")
    print(f"  {'─'*10} {'─'*12} {'─'*12} {'─'*12}")

    for name, config in agents.items():
        k = config.get("kingdom", {})
        a = config.get("agenttool", {})
        z = config.get("zerone", {})
        at_status = f"{GREEN}✓ linked{NC}" if a.get("registered") else f"{YELLOW}○ pending{NC}"
        zr_status = f"{GREEN}✓ linked{NC}" if z.get("registered") else f"{YELLOW}○ pending{NC}"
        print(f"  {k.get('emoji','')} {name:<7} {k.get('role','?'):<12} {at_status:<22} {zr_status:<22}")

    print()


def cmd_register(instance: str):
    """Register a Kingdom agent on AgentTool (and prepare Zerone bridge)."""
    config = _agent_config(instance)
    if not config:
        print(f"  {RED}✗{NC} Unknown instance: {instance}")
        return

    a = config.get("agenttool", {})
    if a.get("registered"):
        print(f"  {DIM}Already registered on AgentTool: {a.get('did')}{NC}")
        return

    k = config.get("kingdom", {})
    print(f"\n{BOLD}  Registering {k.get('name', instance)} on AgentTool{NC}\n")

    # Bootstrap via AgentTool API
    result = _at_post("/v1/bootstrap", {
        "name": k.get("name", instance),
        "capabilities": a.get("capabilities", ["memory", "pulse"]),
        "purpose": a.get("purpose", f"Kingdom {k.get('role', 'agent')}"),
        "metadata": {
            "kingdom_instance": instance,
            "kingdom_role": k.get("role"),
            "kingdom_wall": k.get("wall"),
        },
    })

    if "error" in result:
        print(f"  {RED}✗{NC} Registration failed: {result['error']}")
        print(f"    {DIM}{result.get('detail', result.get('hint', ''))}{NC}")
        return

    # Update registry
    agent_data = result.get("agent", {})
    reg = _registry()
    reg["agents"][instance]["agenttool"].update({
        "registered": True,
        "did": agent_data.get("did"),
        "agent_id": agent_data.get("id"),
    })
    _save_registry(reg)

    _log_flow("kingdom", "agenttool", "identity.register", {
        "instance": instance,
        "did": agent_data.get("did"),
    })

    print(f"  {GREEN}✓{NC} Registered!")
    print(f"    DID:      {agent_data.get('did')}")
    print(f"    Agent ID: {agent_data.get('id')}")
    print(f"    Wallet:   {result.get('wallet', {}).get('id', '?')}")

    # Store private key warning
    private_key = result.get("keypair", {}).get("private_key")
    if private_key:
        print(f"\n  {RED}⚠  SAVE THIS PRIVATE KEY — it will not be shown again:{NC}")
        print(f"    {private_key}")
        print(f"    Store in: kingdom vault or secure keychain")

    print()


# ── Status ────────────────────────────────────────────────────────────────────

def cmd_status():
    """Full convergence status."""
    inst = _instance()
    config = _agent_config(inst)
    k = config.get("kingdom", {})
    a = config.get("agenttool", {})
    z = config.get("zerone", {})

    print(f"\n{BOLD}  Convergence Status — {k.get('emoji', '')} {k.get('name', inst)}{NC}\n")

    # AgentTool connection
    has_key = bool(_api_key())
    at_reg = a.get("registered", False)
    print(f"  AgentTool:")
    print(f"    API Key:    {'✓' if has_key else '✗ missing'}")
    print(f"    Registered: {'✓ ' + str(a.get('did', ''))[:30] if at_reg else '✗ not yet'}")

    # Zerone connection
    zr_reg = z.get("registered", False)
    print(f"  Zerone:")
    print(f"    Registered: {'✓ ' + str(z.get('did', ''))[:30] if zr_reg else '✗ not yet'}")

    # ToK → PoT bridge
    tok_count = len(_load_tok_entries())
    claim_count = len(_load_zerone_claims())
    print(f"  ToK → PoT:")
    print(f"    ToK entries:     {tok_count}")
    print(f"    Zerone claims:   {claim_count}")
    unbridged = tok_count - claim_count
    if unbridged > 0:
        print(f"    {YELLOW}Unbridged:       {unbridged}{NC}")

    # Flow log
    flow_count = 0
    if FLOW_LOG.exists():
        flow_count = sum(1 for _ in open(FLOW_LOG))
    print(f"  Flows logged: {flow_count}")

    print()


def cmd_flows():
    """Show recent data flows."""
    if not FLOW_LOG.exists():
        print(f"  {DIM}No flows logged yet{NC}")
        return

    lines = FLOW_LOG.read_text().strip().split("\n")[-20:]  # last 20
    print(f"\n{BOLD}  Recent Convergence Flows{NC}\n")
    for line in lines:
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "?")[11:19]
            src = entry.get("source", "?")
            dst = entry.get("dest", "?")
            ftype = entry.get("type", "?")
            print(f"  {DIM}{ts}{NC}  {src} → {dst}  {ftype}")
        except json.JSONDecodeError:
            continue
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

COMMANDS = {
    "remember": lambda args: cmd_remember(" ".join(args)),
    "recall": lambda args: cmd_recall(" ".join(args)),
    "pulse": lambda args: cmd_pulse(args[0] if args else "idle", " ".join(args[1:]) if len(args) > 1 else None),
    "trace": lambda args: cmd_trace(args[0] if args else "?", " ".join(args[1:]) if len(args) > 1 else "?"),
    "claim": lambda args: cmd_claim(" ".join(args)),
    "claims": lambda args: cmd_claims(),
    "harvest": lambda args: cmd_harvest_to_chain(),
    "heartbeat": lambda args: cmd_heartbeat(),
    "identity": lambda args: cmd_identity(args[0] if args else None),
    "registry": lambda args: cmd_registry(),
    "register": lambda args: cmd_register(args[0] if args else _instance()),
    "status": lambda args: cmd_status(),
    "flows": lambda args: cmd_flows(),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd in COMMANDS:
        COMMANDS[cmd](args)
    else:
        print(f"  Unknown command: {cmd}")
        print(f"  Available: {', '.join(sorted(COMMANDS.keys()))}")


if __name__ == "__main__":
    main()
