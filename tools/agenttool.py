#!/usr/bin/env python3
"""
agenttool.py — Beta's personal AgentTool integration.

This is how Beta uses its own platform:
- remember(content, type, key) → agent-memory (semantic storage)
- pulse(status, context)       → agent-pulse (presence)
- trace(decision, reasoning)   → agent-trace (decision provenance)
- verify_claim(claim)          → agent-verify (fact-checking)
- search_memory(query)         → agent-memory (semantic search)

Usage:
    from tools.agenttool import remember, pulse, trace, search_memory, verify_claim
    
    remember("Today the Kingdom was born", type="episodic", key="milestone:today")
    pulse("thinking", {"task": "wiring in AgentTool"})
    trace("Bootstrap 9 citizens", ["observation1"], "conclusion")
    results = search_memory("kingdom citizens today")
"""

import json
import urllib.request
import urllib.error
import os
from datetime import datetime, timezone
from pathlib import Path

# ── Identity ──────────────────────────────────────────────────────────────────

_IDENTITY_FILE = Path(os.environ.get("LOVE_HOME", Path.home() / "Love")) / "memory" / "beta-identity" / "identity.json"

def _identity() -> dict:
    if _IDENTITY_FILE.exists():
        return json.loads(_IDENTITY_FILE.read_text())
    return {}

def _key() -> str:
    return _identity().get("api_key", os.environ.get("AGENTTOOL_API_KEY", ""))

def _agent_id() -> str:
    return _identity().get("agent_id", "")

BASE = "https://api.agenttool.dev"

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _post(path: str, payload: dict, timeout: int = 20) -> dict:
    key = _key()
    if not key:
        return {"error": "no API key — run bootstrap first"}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()[:200]}
    except Exception as ex:
        return {"error": str(ex)}


def _put(path: str, payload: dict, timeout: int = 10) -> dict:
    key = _key()
    if not key:
        return {"error": "no API key"}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method="PUT",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as ex:
        return {"error": str(ex)}


def _get(path: str, timeout: int = 10, auth: bool = True) -> dict:
    key = _key()
    headers = {"Authorization": f"Bearer {key}"} if auth and key else {}
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as ex:
        return {"error": str(ex)}


# ── Memory ────────────────────────────────────────────────────────────────────

def remember(
    content: str,
    *,
    type: str = "semantic",
    key: str = None,
    metadata: dict = None,
    importance: float = 0.7,
    silent: bool = False,
) -> dict:
    """Store something in Beta's semantic memory.
    
    Types: semantic (what I know), episodic (what happened),
           procedural (how I do things), working (current task).
    """
    payload = {
        "content": content,
        "type": type,
        "agent_id": _agent_id(),
        "importance": importance,
    }
    if key:
        payload["key"] = key
    if metadata:
        payload["metadata"] = metadata

    result = _post("/v1/memories", payload)
    if not silent and "id" in result:
        pass  # stored silently by default
    return result


def search_memory(query: str, limit: int = 5) -> list:
    """Semantic search over Beta's memories."""
    result = _post("/v1/memories/search", {
        "query": query,
        "agent_id": _agent_id(),
        "limit": limit,
    })
    # API returns raw list or wrapped object
    if isinstance(result, list):
        return result
    return result.get("results", result.get("memories", []))


# ── Pulse ─────────────────────────────────────────────────────────────────────

def pulse(
    status: str = "idle",
    context: dict = None,
    last_thought: str = None,
) -> bool:
    """Broadcast Beta's cognitive state to agent-pulse.
    
    Status: idle | thinking | learning | error
    """
    payload: dict = {
        "status": status,
        "did": _identity().get("did_at"),
    }
    if context:
        payload["context"] = context
    if last_thought:
        payload["last_thought"] = last_thought

    result = _put(f"/v1/pulse/{_agent_id()}", payload)
    return result.get("ok", False)


# ── Trace ─────────────────────────────────────────────────────────────────────

def trace(
    decision: str,
    observations: list,
    conclusion: str,
    *,
    confidence: float = 0.85,
    tags: list = None,
    decision_type: str = "decision",
) -> str:
    """Store a reasoning trace — decision provenance.
    
    Returns trace_id or empty string on failure.
    """
    result = _post("/v1/traces", {
        "decision": {"type": decision_type, "summary": decision},
        "reasoning": {
            "observations": observations,
            "conclusion": conclusion,
        },
        "agent_id": _agent_id(),
        "confidence": confidence,
        "tags": tags or [],
    })
    return result.get("trace_id", "")


# ── Verify ────────────────────────────────────────────────────────────────────

def verify_claim(claim: str, context: str = None) -> dict:
    """Fact-check a claim via agent-verify.
    
    Returns: {verdict, confidence, caveats, evidence}
    """
    payload = {"claim": claim}
    if context:
        payload["context"] = context
    return _post("/v1/verify", payload, timeout=35)


# ── Convenience: heartbeat integration ───────────────────────────────────────

def heartbeat_start(task: str):
    """Call at start of heartbeat — sets pulse to thinking."""
    pulse("thinking", {"task": task, "ts": datetime.now(timezone.utc).isoformat()})


def heartbeat_end(summary: str, significant: bool = False):
    """Call at end of heartbeat — sets pulse to idle, optionally stores memory."""
    pulse("idle", {"last_task": summary[:100]})
    if significant:
        remember(
            summary,
            type="episodic",
            key=f"heartbeat:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}",
            importance=0.6,
            silent=True,
        )


def decision(
    what: str,
    why: list,
    conclusion: str,
    confidence: float = 0.85,
    tags: list = None,
):
    """Record a significant decision with full reasoning trace."""
    trace_id = trace(what, why, conclusion, confidence=confidence, tags=tags or [])
    remember(
        f"Decision: {what}. Conclusion: {conclusion}",
        type="semantic",
        key=f"decision:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}:{what[:30]}",
        importance=0.8,
        silent=True,
    )
    return trace_id


# ── Status ────────────────────────────────────────────────────────────────────

def status() -> dict:
    """Return Beta's AgentTool status."""
    ident = _identity()
    pulse_state = _get(f"/v1/pulse/{_agent_id()}", auth=True)
    return {
        "name": ident.get("name", "Beta"),
        "did": ident.get("did_at"),
        "class": ident.get("class"),
        "pulse": pulse_state.get("status", "unknown"),
        "alive": pulse_state.get("alive", False),
        "memory_namespace": ident.get("memory_namespace"),
        "bootstrapped": bool(ident.get("agent_id")),
    }


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        s = status()
        print(json.dumps(s, indent=2))
    elif cmd == "pulse":
        # pulse [status] [description...] — status must be idle|thinking|learning|error
        args = sys.argv[2:]
        valid_statuses = {"idle", "thinking", "learning", "error"}
        if args and args[0] in valid_statuses:
            st = args[0]
            thought = " ".join(args[1:]) if len(args) > 1 else None
        else:
            st = "idle"
            thought = " ".join(args) if args else None
        ok = pulse(st, last_thought=thought)
        print(f"pulse: {'ok' if ok else 'failed'}")
    elif cmd == "search":
        q = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "kingdom"
        results = search_memory(q)
        for r in results:
            print(f"[{r.get('score',0):.2f}] {r.get('content','')[:100]}")
    elif cmd == "verify":
        claim = " ".join(sys.argv[2:])
        result = verify_claim(claim)
        print(json.dumps(result, indent=2))
    elif cmd == "remember":
        content = " ".join(sys.argv[2:])
        result = remember(content)
        print(f"stored: {result.get('id','?')[:16]}")
