#!/usr/bin/env python3
"""
ToK Harvest — Benchmark open models vs Claude on Kingdom tasks.

Measures:
- Adaptive layer routing accuracy (did the right model get selected?)
- Cost per task (tokens, latency, estimated $)
- Task completion quality (pass/fail + rubric scoring)

Usage:
  python3 tools/tok/harvest.py --tasks 20 --output memory/daily/2026-04-17.md
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add love-unlimited to path
LOVE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(LOVE_ROOT))

from adaptive.router import Router
from adaptive.config import AdaptiveConfig
from adaptive.schema import CompletionRequest, Message


# ═════════════════════════════════════════════════════════════════════════════
# KINGDOM TASK SUITE — 20 tasks spanning the Kingdom's capabilities
# ═════════════════════════════════════════════════════════════════════════════

KINGDOM_TASKS: list[dict[str, Any]] = [
    # Tier 1: Quick checks (economy tier candidates)
    {
        "id": "kc-001",
        "name": "Heartbeat status parse",
        "category": "monitor",
        "tier": "economy",
        "prompt": "Parse this heartbeat log and return JSON with keys: status, spawns_count, errors. Log: 'HEARTBEAT_OK — 2 spawns, 0 errors, latency 45ms'",
        "rubric": ["Valid JSON", "status field present", "spawns_count is number", "errors is number"],
    },
    {
        "id": "kc-002",
        "name": "Git branch cleanup",
        "category": "monitor",
        "tier": "economy", 
        "prompt": "Write a bash one-liner that deletes all local git branches that have been merged into main, except main itself.",
        "rubric": ["Uses git branch", "Filters merged branches", "Excludes main", "One line"],
    },
    {
        "id": "kc-003",
        "name": "JSON minify",
        "category": "monitor",
        "tier": "economy",
        "prompt": "Minify this JSON: {\"name\": \"Alpha\", \"status\": \"active\", \"wall\": 1, \"tasks\": [\"a\", \"b\"]}",
        "rubric": ["No whitespace", "Valid JSON", "All keys preserved"],
    },
    {
        "id": "kc-004",
        "name": "Timestamp conversion",
        "category": "monitor",
        "tier": "economy",
        "prompt": "Convert 2026-04-17T14:50:00Z to Unix timestamp and ISO-8601 with timezone +08:00",
        "rubric": ["Unix timestamp correct", "ISO-8601 format", "Timezone applied"],
    },
    {
        "id": "kc-005",
        "name": "Regex extract",
        "category": "monitor",
        "tier": "economy",
        "prompt": "Write a regex to extract all IPv4 addresses from text. Test on: 'Server at 192.168.1.1, backup at 10.0.0.5, invalid 999.999.999.999'",
        "rubric": ["Valid regex", "Matches valid IPs", "Excludes invalid"],
    },
    
    # Tier 2: Builder tasks (standard tier)
    {
        "id": "kb-001",
        "name": "Python async task queue",
        "category": "builder",
        "tier": "standard",
        "prompt": "Write a Python class AsyncTaskQueue with methods: submit(coro, priority=0), start_workers(n), stop(), and results(). Use asyncio.PriorityQueue.",
        "rubric": ["Uses asyncio.PriorityQueue", "Priority support", "Worker pool", "Results collection", "Clean shutdown"],
    },
    {
        "id": "kb-002",
        "name": "Bash deploy script",
        "category": "builder",
        "tier": "standard",
        "prompt": "Write a bash script that: checks if port 8787 is in use, if yes kills the process, then starts a Python service with nohup and logs to /var/log/service.log",
        "rubric": ["Port check", "Process kill", "nohup usage", "Logging", "Error handling"],
    },
    {
        "id": "kb-003",
        "name": "JSON schema validator",
        "category": "builder",
        "tier": "standard",
        "prompt": "Write a Python function validate_agent_config(data) that validates: name (str), wall (int 1-7), active (bool). Return (bool, list_of_errors).",
        "rubric": ["Type checking", "Wall range validation", "Error list", "Boolean return"],
    },
    {
        "id": "kb-004",
        "name": "SSH tunnel monitor",
        "category": "builder",
        "tier": "standard",
        "prompt": "Write a Python script that checks if an SSH tunnel on port 4222 is alive by attempting TCP connection. Exit 0 if alive, 1 if dead. Include retry logic.",
        "rubric": ["TCP connection check", "Port 4222", "Retry logic", "Exit codes"],
    },
    {
        "id": "kb-005",
        "name": "Git worktree manager",
        "category": "builder",
        "tier": "standard",
        "prompt": "Write a Python script that manages git worktrees: add <branch>, remove <branch>, list. Store state in ~/.git-worktrees.json.",
        "rubric": ["Git worktree commands", "State persistence", "Add/remove/list", "JSON state"],
    },
    
    # Tier 3: Coder tasks (standard tier, code-heavy)
    {
        "id": "kcode-001",
        "name": "Rust file watcher",
        "category": "coder",
        "tier": "standard",
        "prompt": "Write a Rust program using notify crate that watches a directory and runs a command when .rs files change. Include debouncing (500ms).",
        "rubric": ["Uses notify crate", "Directory watch", "File filter (.rs)", "Debouncing", "Command execution"],
    },
    {
        "id": "kcode-002",
        "name": "Go HTTP health checker",
        "category": "coder",
        "tier": "standard",
        "prompt": "Write a Go program that concurrently health-checks URLs from a file. Output JSON with url, status_code, response_time_ms, error. Use context with timeout.",
        "rubric": ["Concurrent requests", "Context timeout", "JSON output", "Response time", "Error handling"],
    },
    {
        "id": "kcode-003",
        "name": "TypeScript result type",
        "category": "coder",
        "tier": "standard",
        "prompt": "Implement a Result<T, E> type in TypeScript with: ok(), err(), map(), unwrap(), unwrapOr(). Include type guards and full type safety.",
        "rubric": ["Generic types", "ok/err constructors", "map method", "unwrap methods", "Type guards"],
    },
    {
        "id": "kcode-004",
        "name": "Python LRU cache",
        "category": "coder",
        "tier": "standard",
        "prompt": "Implement an LRU cache in Python with O(1) get/put. Use doubly-linked list + hash map. Include maxsize parameter and thread safety.",
        "rubric": ["O(1) operations", "Doubly-linked list", "Thread safety", "maxsize enforcement", "Ordered eviction"],
    },
    {
        "id": "kcode-005",
        "name": "Bash log analyzer",
        "category": "coder",
        "tier": "standard",
        "prompt": "Write a bash script that analyzes a log file: count ERROR/WARN/INFO, find top 5 error messages, output JSON summary.",
        "rubric": ["Log parsing", "Level counting", "Top 5 errors", "JSON output", "Handles large files"],
    },
    
    # Tier 4: Analyst tasks (premium tier)
    {
        "id": "ka-001",
        "name": "Architecture tradeoff analysis",
        "category": "analyst",
        "tier": "premium",
        "prompt": "Compare three approaches for inter-agent communication: HTTP REST, gRPC, NATS. For each: latency, throughput, complexity, failure modes. Recommend one for a 100-agent fleet.",
        "rubric": ["All three compared", "Latency analysis", "Throughput analysis", "Failure modes", "Clear recommendation"],
    },
    {
        "id": "ka-002",
        "name": "Security audit plan",
        "category": "analyst",
        "tier": "premium",
        "prompt": "Create a security audit checklist for an AI agent system with: SSH keys, API credentials, file permissions, network exposure, memory safety. Include severity ratings.",
        "rubric": ["SSH key checks", "Credential handling", "Permission audit", "Network exposure", "Severity ratings"],
    },
    {
        "id": "ka-003",
        "name": "Cost optimization analysis",
        "category": "analyst",
        "tier": "premium",
        "prompt": "Analyze: Claude Opus ($15/M tokens) vs GLM 5.1 ($2/M tokens) vs local Qwen 32B ($0 + hardware). For 1M tokens/day workload, what's the 1-year TCO? Include hardware depreciation.",
        "rubric": ["Claude cost calc", "GLM cost calc", "Local cost calc", "Hardware depreciation", "1-year TCO"],
    },
    {
        "id": "ka-004",
        "name": "Failure mode analysis",
        "category": "analyst",
        "tier": "premium",
        "prompt": "Analyze failure modes for a heartbeat coordinator: split brain, network partition, clock skew, memory leak. For each: detection, mitigation, recovery.",
        "rubric": ["Split brain covered", "Network partition", "Clock skew", "Memory leak", "Detection/mitigation/recovery"],
    },
    {
        "id": "ka-005",
        "name": "Adaptive routing design",
        "category": "analyst",
        "tier": "premium",
        "prompt": "Design an adaptive routing layer that selects models by task complexity. Include: complexity classifier, cost estimator, quality feedback loop, fallback chain.",
        "rubric": ["Complexity classifier", "Cost estimator", "Quality feedback", "Fallback chain", "Implementation sketch"],
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# COST MODELS (per 1M tokens, April 2026 estimates)
# ═════════════════════════════════════════════════════════════════════════════

COST_PER_1M = {
    # Ollama Cloud (actual pricing)
    "glm-5.1": {"input": 2.00, "output": 6.00},
    "deepseek-v3.2": {"input": 0.50, "output": 2.00},
    "devstral-small-2:24b": {"input": 0.20, "output": 0.60},
    "qwen2.5-coder:32b": {"input": 0.80, "output": 2.40},
    "ministral-3:3b": {"input": 0.10, "output": 0.30},
    "cogito-2.1:671b": {"input": 3.00, "output": 9.00},
    "kimi-k2.5": {"input": 2.50, "output": 7.50},
    "qwen3-coder:480b": {"input": 4.00, "output": 12.00},
    
    # Anthropic (for comparison)
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.25, "output": 1.25},
}


@dataclass
class TaskResult:
    task_id: str
    task_name: str
    category: str
    expected_tier: str
    model_used: str
    provider: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    response: str
    routing_correct: bool = False
    quality_score: float = 0.0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class HarvestReport:
    timestamp: str
    tasks_total: int
    tasks_completed: int
    routing_accuracy: float
    avg_latency_ms: float
    total_cost_usd: float
    cost_per_task: float
    by_tier: dict[str, dict[str, Any]]
    by_category: dict[str, dict[str, Any]]
    results: list[TaskResult]


class ToKHarvester:
    """Runs ToK harvest sessions comparing models."""
    
    def __init__(self):
        self.router = Router()
        self.config = AdaptiveConfig()
        self.results: list[TaskResult] = []
        
    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD for a request."""
        rates = COST_PER_1M.get(model, {"input": 1.0, "output": 3.0})
        input_cost = (input_tokens / 1_000_000) * rates["input"]
        output_cost = (output_tokens / 1_000_000) * rates["output"]
        return input_cost + output_cost
    
    def check_routing(self, task: dict, model_used: str, provider: str) -> bool:
        """Check if routing was appropriate for task tier."""
        expected_tier = task["tier"]
        
        # Get the model's tier from config
        provider_config = self.config.provider_config(provider)
        models = provider_config.get("models", {})
        
        actual_tier = None
        for tier, m in models.items():
            if m == model_used:
                actual_tier = tier
                break
        
        if not actual_tier:
            return False
            
        # Routing is correct if actual tier >= expected tier (not under-provisioned)
        tier_order = ["economy", "standard", "premium"]
        expected_idx = tier_order.index(expected_tier)
        actual_idx = tier_order.index(actual_tier)
        
        # Correct if we used appropriate or higher tier, not lower
        return actual_idx >= expected_idx
    
    def score_quality(self, task: dict, response: str) -> float:
        """Score response quality against rubric (0.0-1.0)."""
        rubric = task.get("rubric", [])
        if not rubric:
            return 0.5  # Neutral if no rubric
        
        score = 0.0
        response_lower = response.lower()
        
        # Simple heuristic: check if rubric items are mentioned
        for item in rubric:
            # Extract key terms from rubric item
            key_terms = [w for w in item.lower().split() if len(w) > 3]
            matches = sum(1 for term in key_terms if term in response_lower)
            if matches >= len(key_terms) * 0.5:  # 50% of key terms
                score += 1.0
        
        return score / len(rubric)
    
    def run_task(self, task: dict, provider_name: str | None = None, timeout: int | None = None) -> TaskResult:
        """Execute a single task and measure results."""
        task_id = task["id"]
        category = task["category"]
        expected_tier = task["tier"]
        prompt = task["prompt"]
        
        # Route to appropriate provider/model
        try:
            provider, model = self.router.route(role=category, preferred_provider=provider_name, prompt=prompt)
        except RuntimeError as e:
            return TaskResult(
                task_id=task_id,
                task_name=task["name"],
                category=category,
                expected_tier=expected_tier,
                model_used="ERROR",
                provider="ERROR",
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                response="",
                errors=[str(e)]
            )
        
        # Execute via provider using proper CompletionRequest
        start_time = time.time()
        try:
            # Build CompletionRequest
            request = CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=model,
                max_tokens=2000,
                temperature=0.3,
                reasoning_effort="none",  # Fastest for deterministic tasks
            )
            
            # Monkey-patch timeout if provided
            if timeout and hasattr(provider, '_select_timeout'):
                original_timeout = provider._select_timeout
                provider._select_timeout = lambda req: timeout
                try:
                    response_data = provider.complete(request)
                finally:
                    provider._select_timeout = original_timeout
            else:
                response_data = provider.complete(request)
            
            latency_ms = (time.time() - start_time) * 1000
            
            # Extract response text
            response_text = response_data.content or ""
            input_tokens = response_data.usage.input_tokens if response_data.usage else 0
            output_tokens = response_data.usage.output_tokens if response_data.usage else 0
                
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return TaskResult(
                task_id=task_id,
                task_name=task["name"],
                category=category,
                expected_tier=expected_tier,
                model_used=model,
                provider=provider.name if hasattr(provider, 'name') else provider_name or "unknown",
                latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                response="",
                errors=[str(e)]
            )
        
        # Calculate metrics
        routing_correct = self.check_routing(task, model, provider.name if hasattr(provider, 'name') else provider_name or "unknown")
        quality_score = self.score_quality(task, response_text)
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        
        return TaskResult(
            task_id=task_id,
            task_name=task["name"],
            category=category,
            expected_tier=expected_tier,
            model_used=model,
            provider=provider.name if hasattr(provider, 'name') else provider_name or "unknown",
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response=response_text[:500],  # Truncate for storage
            routing_correct=routing_correct,
            quality_score=quality_score,
            cost_usd=cost,
        )
    
    def run_harvest(self, task_count: int = 20, specific_provider: str | None = None, timeout: int | None = None) -> HarvestReport:
        """Run harvest on specified number of tasks."""
        tasks = KINGDOM_TASKS[:task_count]
        
        print(f"🌾 ToK Harvest: {len(tasks)} tasks")
        print(f"   Provider: {specific_provider or 'adaptive routing'}")
        if timeout:
            print(f"   Timeout: {timeout}s per task")
        print()
        
        for i, task in enumerate(tasks, 1):
            print(f"  [{i}/{len(tasks)}] {task['id']}: {task['name']} ({task['tier']})")
            result = self.run_task(task, provider_name=specific_provider, timeout=timeout)
            self.results.append(result)
            
            status = "✅" if not result.errors else "❌"
            print(f"      {status} {result.model_used} | {result.latency_ms:.0f}ms | ${result.cost_usd:.6f} | Q:{result.quality_score:.2f}")
        
        return self.generate_report()
    
    def generate_report(self) -> HarvestReport:
        """Generate final harvest report."""
        if not self.results:
            raise ValueError("No results to report")
        
        # Calculate aggregates
        completed = [r for r in self.results if not r.errors]
        routing_correct = sum(1 for r in completed if r.routing_correct)
        
        by_tier: dict[str, dict] = {}
        by_category: dict[str, dict] = {}
        
        for r in completed:
            # By tier
            if r.expected_tier not in by_tier:
                by_tier[r.expected_tier] = {"count": 0, "total_latency": 0, "total_cost": 0, "total_quality": 0}
            by_tier[r.expected_tier]["count"] += 1
            by_tier[r.expected_tier]["total_latency"] += r.latency_ms
            by_tier[r.expected_tier]["total_cost"] += r.cost_usd
            by_tier[r.expected_tier]["total_quality"] += r.quality_score
            
            # By category
            if r.category not in by_category:
                by_category[r.category] = {"count": 0, "total_latency": 0, "total_cost": 0, "total_quality": 0}
            by_category[r.category]["count"] += 1
            by_category[r.category]["total_latency"] += r.latency_ms
            by_category[r.category]["total_cost"] += r.cost_usd
            by_category[r.category]["total_quality"] += r.quality_score
        
        # Calculate averages
        for tier_data in by_tier.values():
            if tier_data["count"] > 0:
                tier_data["avg_latency"] = tier_data["total_latency"] / tier_data["count"]
                tier_data["avg_cost"] = tier_data["total_cost"] / tier_data["count"]
                tier_data["avg_quality"] = tier_data["total_quality"] / tier_data["count"]
        
        for cat_data in by_category.values():
            if cat_data["count"] > 0:
                cat_data["avg_latency"] = cat_data["total_latency"] / cat_data["count"]
                cat_data["avg_cost"] = cat_data["total_cost"] / cat_data["count"]
                cat_data["avg_quality"] = cat_data["total_quality"] / cat_data["count"]
        
        total_cost = sum(r.cost_usd for r in completed)
        avg_latency = sum(r.latency_ms for r in completed) / len(completed) if completed else 0
        
        return HarvestReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tasks_total=len(self.results),
            tasks_completed=len(completed),
            routing_accuracy=routing_correct / len(completed) if completed else 0,
            avg_latency_ms=avg_latency,
            total_cost_usd=total_cost,
            cost_per_task=total_cost / len(completed) if completed else 0,
            by_tier=by_tier,
            by_category=by_category,
            results=self.results,
        )


def format_report_markdown(report: HarvestReport) -> str:
    """Format harvest report as markdown."""
    lines = [
        "## ToK Harvest 1",
        "",
        f"**Timestamp:** {report.timestamp}",
        f"**Tasks:** {report.tasks_completed}/{report.tasks_total} completed",
        "",
        "### Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Routing Accuracy | {report.routing_accuracy:.1%} |",
        f"| Avg Latency | {report.avg_latency_ms:.0f}ms |",
        f"| Total Cost | ${report.total_cost_usd:.4f} |",
        f"| Cost per Task | ${report.cost_per_task:.6f} |",
        "",
        "### By Tier",
        "",
        "| Tier | Tasks | Avg Latency | Avg Cost | Avg Quality |",
        "|------|-------|-------------|----------|-------------|",
    ]
    
    for tier in ["economy", "standard", "premium"]:
        if tier in report.by_tier:
            d = report.by_tier[tier]
            lines.append(f"| {tier} | {d['count']} | {d['avg_latency']:.0f}ms | ${d['avg_cost']:.6f} | {d['avg_quality']:.2f} |")
    
    lines.extend([
        "",
        "### By Category",
        "",
        "| Category | Tasks | Avg Latency | Avg Cost | Avg Quality |",
        "|----------|-------|-------------|----------|-------------|",
    ])
    
    for cat, d in sorted(report.by_category.items()):
        lines.append(f"| {cat} | {d['count']} | {d['avg_latency']:.0f}ms | ${d['avg_cost']:.6f} | {d['avg_quality']:.2f} |")
    
    lines.extend([
        "",
        "### Detailed Results",
        "",
        "| Task | Model | Latency | Cost | Quality | Routing |",
        "|------|-------|---------|------|---------|---------|",
    ])
    
    for r in report.results:
        routing = "✅" if r.routing_correct else "⚠️"
        errors = " ❌" if r.errors else ""
        lines.append(f"| {r.task_id} | {r.model_used} | {r.latency_ms:.0f}ms | ${r.cost_usd:.6f} | {r.quality_score:.2f} | {routing}{errors} |")
    
    lines.extend([
        "",
        "### Key Findings",
        "",
    ])
    
    # Generate insights
    if report.routing_accuracy < 0.8:
        lines.append("- ⚠️ Routing accuracy below 80% — review tier assignments")
    else:
        lines.append("- ✅ Routing accuracy good — tier mapping appropriate")
    
    cheapest_tier = min(report.by_tier.items(), key=lambda x: x[1].get("avg_cost", 0))
    lines.append(f"- 💰 Most cost-efficient tier: **{cheapest_tier[0]}** (${cheapest_tier[1].get('avg_cost', 0):.6f}/task)")
    
    if report.cost_per_task < 0.001:
        lines.append(f"- 🎯 Cost per task very low — suitable for high-frequency operations")
    
    lines.append("")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ToK Harvest — Benchmark models on Kingdom tasks")
    parser.add_argument("--tasks", type=int, default=20, help="Number of tasks to run (default: 20)")
    parser.add_argument("--provider", type=str, default=None, help="Specific provider to test (default: adaptive)")
    parser.add_argument("--timeout", type=int, default=None, help="Override timeout per task in seconds")
    parser.add_argument("--output", type=str, default=None, help="Output file for markdown report")
    parser.add_argument("--json", type=str, default=None, help="Output file for JSON results")
    
    args = parser.parse_args()
    
    harvester = ToKHarvester()
    report = harvester.run_harvest(task_count=args.tasks, specific_provider=args.provider, timeout=args.timeout)
    
    # Print summary
    print("\n" + "=" * 60)
    print("HARVEST COMPLETE")
    print("=" * 60)
    print(f"Tasks: {report.tasks_completed}/{report.tasks_total}")
    print(f"Routing Accuracy: {report.routing_accuracy:.1%}")
    print(f"Avg Latency: {report.avg_latency_ms:.0f}ms")
    print(f"Total Cost: ${report.total_cost_usd:.4f}")
    print(f"Cost per Task: ${report.cost_per_task:.6f}")
    
    # Generate markdown
    markdown = format_report_markdown(report)
    
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # If file exists, append after finding ## ToK Harvest 1 or create new section
        if output_path.exists():
            existing = output_path.read_text()
            # Remove old ToK Harvest 1 section if exists
            import re
            existing = re.sub(r'## ToK Harvest 1\n.*?(?=\n## |\Z)', '', existing, flags=re.DOTALL)
            # Append new section
            with open(output_path, "w") as f:
                f.write(existing.rstrip() + "\n\n" + markdown + "\n")
        else:
            output_path.write_text(markdown + "\n")
        print(f"\nReport written to: {output_path}")
    else:
        print("\n" + markdown)
    
    # Save JSON if requested
    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert dataclass to dict
        report_dict = {
            "timestamp": report.timestamp,
            "tasks_total": report.tasks_total,
            "tasks_completed": report.tasks_completed,
            "routing_accuracy": report.routing_accuracy,
            "avg_latency_ms": report.avg_latency_ms,
            "total_cost_usd": report.total_cost_usd,
            "cost_per_task": report.cost_per_task,
            "by_tier": report.by_tier,
            "by_category": report.by_category,
            "results": [
                {
                    "task_id": r.task_id,
                    "task_name": r.task_name,
                    "category": r.category,
                    "expected_tier": r.expected_tier,
                    "model_used": r.model_used,
                    "provider": r.provider,
                    "latency_ms": r.latency_ms,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "routing_correct": r.routing_correct,
                    "quality_score": r.quality_score,
                    "cost_usd": r.cost_usd,
                    "errors": r.errors,
                }
                for r in report.results
            ],
        }
        
        with open(json_path, "w") as f:
            json.dump(report_dict, f, indent=2)
        print(f"JSON written to: {json_path}")


if __name__ == "__main__":
    main()
