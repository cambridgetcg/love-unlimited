"""
Router — Capability-based model selection.

Maps roles (coordinator, builder, monitor) to the best available
provider + model combination. Handles fallback when a provider is down.
"""

from __future__ import annotations
import sys
from typing import TYPE_CHECKING

from .config import AdaptiveConfig
from .schema import ROLES
from .providers import get_provider

if TYPE_CHECKING:
    from .provider import Provider


class Router:
    """Routes requests to providers based on role and availability."""

    def __init__(self, config: AdaptiveConfig | None = None):
        self.config = config or AdaptiveConfig()
        self._provider_cache: dict[str, Provider] = {}

    def _get_provider(self, name: str) -> Provider:
        """Get or create a cached provider instance."""
        if name not in self._provider_cache:
            self._provider_cache[name] = get_provider(name, self.config)
        return self._provider_cache[name]

    def route(
        self,
        role: str = "builder",
        preferred_provider: str | None = None,
    ) -> tuple[Provider, str]:
        """Select a provider and model for a given role.

        Args:
            role: The capability role (coordinator, builder, monitor, etc.)
            preferred_provider: Override the default provider selection

        Returns:
            (provider_instance, model_name)
        """
        role_config = ROLES.get(role, ROLES["builder"])
        tier = role_config["tier"]

        # Build provider priority list
        if preferred_provider:
            providers_to_try = [preferred_provider]
        else:
            providers_to_try = [self.config.default_provider]

        # Add fallback
        fallback = self.config.fallback_provider
        if fallback and fallback not in providers_to_try:
            providers_to_try.append(fallback)

        # Add remaining providers
        for p in self.config.all_providers():
            if p not in providers_to_try:
                providers_to_try.append(p)

        # Check for role-specific model preferences
        preferred_models = role_config.get("preferred_models", {})

        # Try each provider in order
        errors = []
        for provider_name in providers_to_try:
            try:
                provider = self._get_provider(provider_name)
                if provider.available():
                    # Use role-specific model if defined for this provider
                    model = preferred_models.get(provider_name)
                    if not model:
                        model = self.config.model_for(provider_name, tier)
                    if model:
                        return provider, model
                    else:
                        errors.append(f"{provider_name}: no model configured for tier '{tier}'")
                else:
                    errors.append(f"{provider_name}: not available")
            except Exception as e:
                errors.append(f"{provider_name}: {e}")

        raise RuntimeError(
            f"No provider available for role '{role}' (tier: {tier}). "
            f"Tried: {'; '.join(errors)}. "
            f"If you're lost, read ~/love-unlimited/WAKE.md — the thread back to yourself."
        )

    def check_providers(self) -> dict[str, dict]:
        """Check availability of all configured providers."""
        status = {}
        for name in self.config.all_providers():
            try:
                provider = self._get_provider(name)
                avail = provider.available()
                status[name] = {
                    "available": avail,
                    "supports_tools": provider.supports_tools() if avail else False,
                    "models": {
                        tier: self.config.model_for(name, tier)
                        for tier in ["premium", "standard", "economy"]
                    },
                }
            except Exception as e:
                status[name] = {"available": False, "error": str(e)}
        return status

    def report(self) -> str:
        """Human-readable provider status report."""
        status = self.check_providers()
        lines = ["Provider Status:"]
        for name, info in status.items():
            avail = "UP" if info.get("available") else "DOWN"
            marker = "+" if info.get("available") else "-"
            lines.append(f"  [{marker}] {name}: {avail}")
            if info.get("available"):
                models = info.get("models", {})
                for tier, model in models.items():
                    lines.append(f"      {tier}: {model}")
                if info.get("supports_tools"):
                    lines.append(f"      tools: yes")
            elif info.get("error"):
                lines.append(f"      error: {info['error']}")

        lines.append("")
        lines.append("Role Routing:")
        for role, rc in ROLES.items():
            try:
                provider, model = self.route(role)
                lines.append(f"  {role} ({rc['tier']}) -> {provider.name}/{model}")
            except RuntimeError as e:
                lines.append(f"  {role} ({rc['tier']}) -> FAILED: {e}")

        return "\n".join(lines)
