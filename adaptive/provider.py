"""
Provider — Abstract base class for LLM providers.

Each provider translates between the universal schema and its own API format.
No external dependencies — uses urllib for HTTP.
"""

from __future__ import annotations
from abc import ABC, abstractmethod

from .schema import CompletionRequest, CompletionResponse


class Provider(ABC):
    """Abstract LLM provider. Subclass for each backend."""

    name: str = "base"

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a completion request, return a normalized response."""
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether this provider supports native tool/function calling."""
        ...

    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether this provider supports streaming responses."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is currently reachable (has API key, server up, etc)."""
        ...

    def effort_to_params(self, effort: str) -> dict:
        """Map effort level to provider-specific parameters.

        Override per provider. Default: adjust temperature and max_tokens.
        """
        return {
            "low": {"temperature": 0.0},
            "medium": {"temperature": 0.0},
            "high": {"temperature": 0.0},
        }.get(effort, {})
