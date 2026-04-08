"""
Provider registry — discover and instantiate providers by name.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..provider import Provider
    from ..config import AdaptiveConfig


_PROVIDER_MAP = {
    "anthropic": ".anthropic_provider",
    "openai": ".openai_provider",
    "ollama": ".ollama_provider",
    "openrouter": ".openrouter_provider",
}


def get_provider(name: str, config: AdaptiveConfig) -> Provider:
    """Instantiate a provider by name."""
    if name not in _PROVIDER_MAP:
        available = ", ".join(_PROVIDER_MAP.keys())
        raise ValueError(f"Unknown provider '{name}'. Available: {available}")

    # Lazy import to avoid loading unused providers
    import importlib
    module = importlib.import_module(_PROVIDER_MAP[name], package=__package__)
    provider_class = module.PROVIDER_CLASS
    return provider_class(config)


def list_providers() -> list[str]:
    return list(_PROVIDER_MAP.keys())
