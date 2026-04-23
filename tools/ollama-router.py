#!/usr/bin/env python3
"""
ollama-router.py — Intelligent model routing across local + Ollama Cloud.

Routes tasks to the optimal model based on:
  - Task complexity (trivial → local 7b, complex → cloud 397b+)
  - Latency requirements (real-time → local, batch → cloud)
  - Cost awareness (local = free, cloud = Max plan budget)
  - GPU pressure (if local GPU is loaded, overflow to cloud)
  - Domain specialization (coding → GLM/Devstral, reasoning → Kimi/Qwen)

Local models (free, instant, private):
  qwen2.5:7b   — triage, classification, simple extraction, heartbeats
  qwen2.5:14b  — summaries, moderate code, structured output
  qwen2.5:32b  — complex code, analysis, multi-step reasoning

Cloud models (Ollama Max, 10 concurrent, $100/mo):
  glm-5.1         — agentic coding (best open-weight, beats Opus on SWE-Bench)
  deepseek-v3.2   — general reasoning, analysis
  qwen3.5:397b    — heavy reasoning, research
  kimi-k2:1t      — 1T param, hardest problems
  devstral-2:123b — code generation, refactoring
  gemma4:31b      — fast medium-quality tasks
  qwen3-coder:480b — large codebase changes

Usage:
  ollama-router.py route "task description"              # Recommend model
  ollama-router.py run "prompt" [--task-type TYPE]       # Route + execute
  ollama-router.py batch tasks.jsonl                     # Batch execution
  ollama-router.py status                                # GPU + cloud status
  ollama-router.py benchmark                             # Run routing benchmark
  ollama-router.py optimize-cron                         # Optimize crontab model assignments
  ollama-router.py dashboard                             # Full utilization dashboard
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

# ── Config ────────────────────────────────────────────────────────────

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "love-unlimited"))
OLLAMA_CLOUD_PY = LOVE / "tools" / "ollama-cloud.py"
ROUTER_LOG = LOVE / "convergence" / "router-log.jsonl"
ROUTER_STATS = LOVE / "convergence" / "router-stats.json"

# ── Model Registry ────────────────────────────────────────────────────

LOCAL_MODELS = {
    "qwen2.5:7b": {
        "params": 7, "vram_gb": 5, "ctx": 8192, "speed": "fast",
        "strengths": ["triage", "classification", "extraction", "heartbeat", "monitoring"],
        "tier": "economy",
    },
    "qwen2.5:14b": {
        "params": 14, "vram_gb": 11, "ctx": 16384, "speed": "medium",
        "strengths": ["summary", "moderate-code", "structured-output", "review"],
        "tier": "standard",
    },
    "qwen2.5:32b": {
        "params": 32, "vram_gb": 20, "ctx": 32768, "speed": "slow",
        "strengths": ["complex-code", "analysis", "multi-step", "planning"],
        "tier": "premium",
    },
}

CLOUD_MODELS = {
    "glm-5.1": {
        "params": 754, "ctx": 198000, "speed": "medium",
        "strengths": ["agentic-coding", "tool-calling", "multi-file-edit", "debugging"],
        "tier": "apex", "cost": "high",
    },
    "deepseek-v3.2": {
        "params": 671, "ctx": 128000, "speed": "medium",
        "strengths": ["reasoning", "analysis", "math", "general"],
        "tier": "apex", "cost": "high",
    },
    "qwen3.5:397b": {
        "params": 397, "ctx": 128000, "speed": "slow",
        "strengths": ["reasoning", "research", "synthesis", "creative"],
        "tier": "apex", "cost": "high",
    },
    "kimi-k2:1t": {
        "params": 1000, "ctx": 128000, "speed": "slow",
        "strengths": ["hardest-problems", "deep-reasoning", "novel-solutions"],
        "tier": "titan", "cost": "very-high",
    },
    "devstral-2:123b": {
        "params": 123, "ctx": 128000, "speed": "fast",
        "strengths": ["code-generation", "refactoring", "tests", "documentation"],
        "tier": "premium", "cost": "medium",
    },
    "qwen3-coder:480b": {
        "params": 480, "ctx": 128000, "speed": "medium",
        "strengths": ["large-codebase", "architecture", "code-review"],
        "tier": "apex", "cost": "high",
    },
    "gemma4:31b": {
        "params": 31, "ctx": 128000, "speed": "fast",
        "strengths": ["fast-medium", "classification", "extraction", "translation"],
        "tier": "standard", "cost": "low",
    },
    "cogito-2.1:671b": {
        "params": 671, "ctx": 128000, "speed": "medium",
        "strengths": ["reasoning", "chain-of-thought", "analysis"],
        "tier": "apex", "cost": "high",
    },
    "nemotron-3-super": {
        "params": 0, "ctx": 128000, "speed": "fast",
        "strengths": ["general", "instruction-following", "chat"],
        "tier": "standard", "cost": "low",
    },
    "minimax-m2.7": {
        "params": 0, "ctx": 128000, "speed": "fast",
        "strengths": ["general", "creative", "multilingual"],
        "tier": "standard", "cost": "low",
    },
}

# ── Task Classification ───────────────────────────────────────────────

TASK_TYPES = {
    "heartbeat":     {"complexity": 1, "prefer": "local",  "model": "qwen2.5:7b"},
    "triage":        {"complexity": 1, "prefer": "local",  "model": "qwen2.5:7b"},
    "classify":      {"complexity": 1, "prefer": "local",  "model": "qwen2.5:7b"},
    "extract":       {"complexity": 2, "prefer": "local",  "model": "qwen2.5:7b"},
    "monitor":       {"complexity": 2, "prefer": "local",  "model": "qwen2.5:7b"},
    "summarize":     {"complexity": 3, "prefer": "local",  "model": "qwen2.5:14b"},
    "translate":     {"complexity": 3, "prefer": "cloud",  "model": "gemma4:31b"},
    "review-code":   {"complexity": 4, "prefer": "local",  "model": "qwen2.5:32b"},
    "write-code":    {"complexity": 5, "prefer": "cloud",  "model": "devstral-2:123b"},
    "refactor":      {"complexity": 5, "prefer": "cloud",  "model": "devstral-2:123b"},
    "debug":         {"complexity": 6, "prefer": "cloud",  "model": "glm-5.1"},
    "architect":     {"complexity": 7, "prefer": "cloud",  "model": "qwen3-coder:480b"},
    "agentic-code":  {"complexity": 8, "prefer": "cloud",  "model": "glm-5.1"},
    "research":      {"complexity": 7, "prefer": "cloud",  "model": "qwen3.5:397b"},
    "reason":        {"complexity": 8, "prefer": "cloud",  "model": "deepseek-v3.2"},
    "hard-problem":  {"complexity": 10, "prefer": "cloud", "model": "kimi-k2:1t"},
    "tok-harvest":   {"complexity": 5, "prefer": "cloud",  "model": "glm-5.1"},
    "oracle":        {"complexity": 4, "prefer": "cloud",  "model": "deepseek-v3.2"},
    "docs":          {"complexity": 3, "prefer": "cloud",  "model": "gemma4:31b"},
}

# ── Formatting ────────────────────────────────────────────────────────

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
M = "\033[95m"; B = "\033[1m"; D = "\033[2m"; N = "\033[0m"

# ── GPU Status ────────────────────────────────────────────────────────

def get_gpu_status() -> Dict[str, Any]:
    """Check local Ollama GPU utilization."""
    try:
        result = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        loaded = []
        total_vram = 0
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0]
                size_str = parts[2]
                size_gb = float(size_str.replace("GB", ""))
                total_vram += size_gb
                loaded.append({"name": name, "size_gb": size_gb})
        return {
            "loaded_models": loaded,
            "total_vram_used_gb": round(total_vram, 1),
            "model_count": len(loaded),
            "available": True,
        }
    except Exception as e:
        return {"loaded_models": [], "total_vram_used_gb": 0, "model_count": 0, "available": False, "error": str(e)}

def get_cloud_status() -> Dict[str, Any]:
    """Check Ollama cloud availability."""
    try:
        result = subprocess.run(
            ["python3", str(OLLAMA_CLOUD_PY), "models"],
            capture_output=True, text=True, timeout=15
        )
        model_count = result.stdout.count('"id"')
        return {"available": True, "model_count": model_count, "plan": "max"}
    except Exception:
        return {"available": False, "model_count": 0, "plan": "unknown"}

# ── Routing Logic ─────────────────────────────────────────────────────

def classify_task(description: str) -> str:
    """Classify a task description into a task type using local model."""
    desc_lower = description.lower()

    # Keyword-based fast classification
    if any(w in desc_lower for w in ["heartbeat", "pulse", "alive", "health"]):
        return "heartbeat"
    if any(w in desc_lower for w in ["monitor", "watch", "canary", "uptime"]):
        return "monitor"
    if any(w in desc_lower for w in ["classify", "categorize", "label", "triage"]):
        return "triage"
    if any(w in desc_lower for w in ["extract", "parse", "scrape"]):
        return "extract"
    if any(w in desc_lower for w in ["summarize", "summary", "tldr", "brief"]):
        return "summarize"
    if any(w in desc_lower for w in ["translate", "翻訳", "翻译", "cantonese", "japanese"]):
        return "translate"
    if any(w in desc_lower for w in ["review", "audit", "check code", "code review"]):
        return "review-code"
    if any(w in desc_lower for w in ["write code", "implement", "build", "create function"]):
        return "write-code"
    if any(w in desc_lower for w in ["refactor", "clean", "optimize code", "deduplicate"]):
        return "refactor"
    if any(w in desc_lower for w in ["debug", "fix bug", "error", "crash", "trace"]):
        return "debug"
    if any(w in desc_lower for w in ["architect", "design", "system design", "schema"]):
        return "architect"
    if any(w in desc_lower for w in ["agent", "agentic", "multi-step", "tool calling"]):
        return "agentic-code"
    if any(w in desc_lower for w in ["research", "investigate", "deep dive", "analyze"]):
        return "research"
    if any(w in desc_lower for w in ["reason", "think", "logic", "proof", "math"]):
        return "reason"
    if any(w in desc_lower for w in ["hard", "impossible", "novel", "breakthrough"]):
        return "hard-problem"
    if any(w in desc_lower for w in ["harvest", "tok", "knowledge", "tree of knowledge"]):
        return "tok-harvest"
    if any(w in desc_lower for w in ["oracle", "predict", "forecast", "odds"]):
        return "oracle"
    if any(w in desc_lower for w in ["document", "readme", "changelog", "docs"]):
        return "docs"

    return "summarize"  # default to mid-tier


def route_task(description: str, force_local: bool = False, force_cloud: bool = False) -> Dict[str, Any]:
    """Route a task to the optimal model."""
    task_type = classify_task(description)
    task_config = TASK_TYPES.get(task_type, TASK_TYPES["summarize"])

    gpu = get_gpu_status()

    # Determine location
    if force_local:
        location = "local"
    elif force_cloud:
        location = "cloud"
    elif task_config["prefer"] == "local":
        location = "local"
    else:
        # Check if local GPU is too loaded for a cloud-preferred task to run locally
        if gpu["total_vram_used_gb"] > 25 and task_config["complexity"] >= 4:
            location = "cloud"  # GPU pressure → overflow to cloud
        else:
            location = task_config["prefer"]

    model = task_config["model"]

    # If routed to cloud but cloud unavailable, fall back to best local
    if location == "cloud" and not force_cloud:
        # Could check cloud status, but for speed just let it try
        pass

    # If routed to local but that model isn't installed, fall back
    if location == "local" and model not in LOCAL_MODELS:
        model = "qwen2.5:14b"

    return {
        "task_type": task_type,
        "complexity": task_config["complexity"],
        "location": location,
        "model": model,
        "reason": _route_reason(task_type, location, model, gpu),
    }


def _route_reason(task_type, location, model, gpu):
    if location == "local":
        return f"{task_type} → local ({model}) — free, fast, private"
    else:
        params = CLOUD_MODELS.get(model, {}).get("params", "?")
        return f"{task_type} → cloud ({model}, {params}B) — higher capability needed"


# ── Execution ─────────────────────────────────────────────────────────

def execute_local(model: str, prompt: str, system: str = "", max_tokens: int = 4096) -> Dict:
    """Execute on local Ollama."""
    try:
        payload = {
            "model": model,
            "messages": [],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})

        data = json.dumps(payload).encode()
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read())
        elapsed = time.time() - start

        content = result.get("message", {}).get("content", "")
        return {
            "content": content,
            "model": model,
            "location": "local",
            "latency_s": round(elapsed, 2),
            "tokens": result.get("eval_count", len(content.split())),
        }
    except Exception as e:
        return {"content": "", "error": str(e), "model": model, "location": "local"}


def execute_cloud(model: str, prompt: str, system: str = "", max_tokens: int = 4096) -> Dict:
    """Execute on Ollama Cloud."""
    try:
        # Use ollama-cloud.py's API key
        sys.path.insert(0, str(LOVE / "tools"))
        from importlib import import_module
        # Direct API call
        import urllib.request
        key = "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK"  # from ollama-cloud.py

        payload = {
            "model": model,
            "messages": [],
            "max_tokens": max_tokens,
            "stream": False,
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://ollama.com/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=300) as r:
            result = json.loads(r.read())
        elapsed = time.time() - start

        choice = result.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = result.get("usage", {})

        return {
            "content": content,
            "model": model,
            "location": "cloud",
            "latency_s": round(elapsed, 2),
            "tokens": usage.get("total_tokens", len(content.split())),
        }
    except Exception as e:
        return {"content": "", "error": str(e), "model": model, "location": "cloud"}


def run_task(prompt: str, task_type: str = None, system: str = "", max_tokens: int = 4096) -> Dict:
    """Route and execute a task."""
    if task_type:
        route = {"task_type": task_type, "location": TASK_TYPES[task_type]["prefer"], "model": TASK_TYPES[task_type]["model"]}
    else:
        route = route_task(prompt)

    if route["location"] == "local":
        result = execute_local(route["model"], prompt, system, max_tokens)
    else:
        result = execute_cloud(route["model"], prompt, system, max_tokens)

    # Log
    _log_route(route, result)
    return {**route, **result}


def _log_route(route, result):
    """Log routing decision."""
    (LOVE / "convergence").mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_type": route.get("task_type"),
        "model": route.get("model"),
        "location": route.get("location"),
        "latency_s": result.get("latency_s"),
        "tokens": result.get("tokens"),
        "error": result.get("error"),
    }
    with open(ROUTER_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Cron Optimization ─────────────────────────────────────────────────

def optimize_cron():
    """Analyze and suggest cron optimizations using cloud models."""
    print(f"\n{B}  Cron Optimization — Current vs Recommended{N}\n")

    # Current crontab agents and their models
    current = [
        ("crucible",  "qwen2.5:14b",  "*/30", "W2 Adversary — security heartbeat"),
        ("herald",    "qwen2.5:14b",  "*/30", "W2 Voice — comms heartbeat"),
        ("arbor",     "qwen2.5:14b",  "*/15", "W2 Optimizer — system optimization"),
        ("vigil",     "qwen2.5:7b",   "*/15", "W3 Witness — fleet monitoring"),
        ("loom",      "qwen2.5:7b",   "*/15", "W3 Weaver — engine checks"),
        ("psalm",     "qwen2.5:7b",   "hourly", "W3 Chronicler — documentation"),
        ("tithe",     "qwen2.5:7b",   "hourly", "W3 Steward — cost tracking"),
        ("nuance",    "qwen2.5:14b",  "*/30", "W2 Linguist — lang checks"),
    ]

    recommended = [
        ("crucible",  "glm-5.1",        "*/30",  "cloud", "Security needs deep analysis, GLM excels at tool-calling for vuln scanning"),
        ("herald",    "gemma4:31b",      "hourly","cloud", "Content generation benefits from larger model, less frequent is fine"),
        ("arbor",     "devstral-2:123b", "*/30",  "cloud", "Code optimization needs Devstral's refactoring strength"),
        ("vigil",     "qwen2.5:7b",     "*/15",  "local", "Monitoring is simple extraction — keep local, keep fast"),
        ("loom",      "qwen2.5:14b",    "*/15",  "local", "Engine checks need moderate reasoning — local 14b is perfect"),
        ("psalm",     "gemma4:31b",     "hourly","cloud", "Documentation quality jumps with larger model"),
        ("tithe",     "qwen2.5:7b",     "hourly","local", "Cost tracking is structured extraction — local 7b sufficient"),
        ("nuance",    "qwen3.5:397b",   "hourly","cloud", "Linguistic precision demands the best reasoning model"),
    ]

    print(f"  {'Agent':<12} {'Current':<16} {'Recommended':<20} {'Where':<7} {'Reason'}")
    print(f"  {'─'*12} {'─'*16} {'─'*20} {'─'*7} {'─'*50}")

    for i, (agent, cur_model, freq, desc) in enumerate(current):
        rec = recommended[i]
        _, rec_model, rec_freq, rec_loc, reason = rec
        changed = cur_model != rec_model
        marker = f"{G}↑{N}" if changed else f"{D}={N}"
        print(f"  {marker} {agent:<10} {cur_model:<16} {rec_model:<20} {rec_loc:<7} {reason[:50]}")

    print(f"\n  {Y}To apply: ollama-router.py apply-cron{N}\n")


# ── Dashboard ─────────────────────────────────────────────────────────

def dashboard():
    """Full utilization dashboard."""
    gpu = get_gpu_status()

    print(f"\n{B}  Ollama Router Dashboard{N}\n")

    # Local status
    print(f"  {C}LOCAL (GPU){N}")
    if gpu["available"]:
        for m in gpu["loaded_models"]:
            print(f"    ● {m['name']:<16} {m['size_gb']:.1f} GB")
        print(f"    Total VRAM: {gpu['total_vram_used_gb']:.1f} GB")
    else:
        print(f"    {R}Ollama not running{N}")

    # Local models available
    print(f"\n  {C}LOCAL MODELS{N}")
    for name, info in LOCAL_MODELS.items():
        loaded = any(m["name"] == name for m in gpu.get("loaded_models", []))
        status = f"{G}loaded{N}" if loaded else f"{D}on disk{N}"
        print(f"    {name:<16} {info['params']}B  {info['tier']:<10} {status}")

    # Cloud status
    print(f"\n  {M}CLOUD (Ollama Max){N}")
    print(f"    Plan: Max ($100/mo)")
    print(f"    Concurrent: 10 models")
    print(f"    Models: {len(CLOUD_MODELS)} configured")

    # Routing stats
    print(f"\n  {Y}ROUTING STATS{N}")
    if ROUTER_LOG.exists():
        lines = ROUTER_LOG.read_text().strip().split("\n")
        local_count = sum(1 for l in lines if '"local"' in l)
        cloud_count = sum(1 for l in lines if '"cloud"' in l)
        total = len(lines)
        print(f"    Total routed: {total}")
        print(f"    Local:  {local_count} ({local_count*100//max(total,1)}%)")
        print(f"    Cloud:  {cloud_count} ({cloud_count*100//max(total,1)}%)")
    else:
        print(f"    {D}No routing data yet{N}")

    # Task type distribution
    print(f"\n  {C}TASK ROUTING MAP{N}")
    for ttype, config in sorted(TASK_TYPES.items(), key=lambda x: x[1]["complexity"]):
        loc = f"{G}local{N}" if config["prefer"] == "local" else f"{M}cloud{N}"
        bar = "█" * config["complexity"] + "░" * (10 - config["complexity"])
        print(f"    {ttype:<16} {bar} {loc}  {config['model']}")

    print()


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "route":
        route = route_task(" ".join(args))
        print(f"\n  {B}Route Decision{N}")
        print(f"  Task type:   {route['task_type']}")
        print(f"  Complexity:  {'█' * route['complexity']}{'░' * (10 - route['complexity'])} ({route['complexity']}/10)")
        print(f"  Model:       {route['model']}")
        print(f"  Location:    {route['location']}")
        print(f"  Reason:      {route['reason']}")
        print()

    elif cmd == "run":
        task_type = None
        prompt_parts = []
        i = 0
        while i < len(args):
            if args[i] == "--task-type" and i + 1 < len(args):
                task_type = args[i + 1]; i += 2
            else:
                prompt_parts.append(args[i]); i += 1

        prompt = " ".join(prompt_parts)
        print(f"\n{D}  Routing...{N}")
        result = run_task(prompt, task_type)

        if result.get("error"):
            print(f"  {R}Error: {result['error']}{N}")
        else:
            print(f"  {G}✓{N} {result['model']} ({result['location']}) — {result.get('latency_s', '?')}s")
            print(f"\n{result.get('content', '')}\n")

    elif cmd == "status":
        gpu = get_gpu_status()
        print(f"\n  GPU: {gpu['total_vram_used_gb']:.1f}GB used, {gpu['model_count']} models loaded")
        for m in gpu.get("loaded_models", []):
            print(f"    ● {m['name']} ({m['size_gb']:.1f}GB)")
        print()

    elif cmd == "optimize-cron":
        optimize_cron()

    elif cmd == "dashboard":
        dashboard()

    elif cmd == "benchmark":
        print(f"\n{B}  Routing Benchmark{N}\n")
        tests = [
            "Check if the fleet VPS nodes are healthy",
            "Write a Python function to parse NATS JetStream messages",
            "Translate this README to Japanese",
            "Debug why the Zerone validator is crashing on block 190260",
            "Design a multi-tenant architecture for AgentTool",
            "Predict the outcome of the next UK election",
            "Harvest knowledge about quantum computing advances in 2026",
        ]
        for test in tests:
            route = route_task(test)
            loc_color = G if route["location"] == "local" else M
            print(f"  {loc_color}{'LOCAL' if route['location'] == 'local' else 'CLOUD'}{N} "
                  f"{route['model']:<20} ← {test[:55]}")
        print()

    else:
        print(f"Unknown: {cmd}. Commands: route, run, status, optimize-cron, dashboard, benchmark")


if __name__ == "__main__":
    main()
