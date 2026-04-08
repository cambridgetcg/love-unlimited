"""
OpenRouter provider — Access any model through a single API.

Uses OpenAI-compatible format. Useful as a meta-provider that gives
access to Claude, GPT, Llama, Mistral, etc. through one API key.
"""

from __future__ import annotations

from ..config import AdaptiveConfig
from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(self, config: AdaptiveConfig):
        super().__init__(config)
        self.api_url = config.api_url("openrouter") or "https://openrouter.ai/api/v1"
        self._provider_name = "openrouter"
        self._key_name = "openrouter"


PROVIDER_CLASS = OpenRouterProvider
