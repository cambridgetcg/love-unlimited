"""
Adaptive Layer — Model-agnostic LLM abstraction for the Love system.

Decouples the Kingdom from any single provider. Route by capability,
fall back gracefully, run local when you can.

Usage:
    from adaptive import Router, AgentRunner
    router = Router()
    runner = AgentRunner(router)
    result = runner.run("your prompt", role="builder")
"""

__version__ = "0.1.0"

from .config import AdaptiveConfig
from .router import Router
from .runner import AgentRunner
