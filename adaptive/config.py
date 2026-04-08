"""
Config — Load and manage provider configuration.

Reads from love.json's "adaptive" section. Falls back to sensible
defaults so the system works even without explicit config.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Desktop" / "Love"))
LOVE_JSON = LOVE_DIR / "love.json"

# Default provider config — used when love.json has no "adaptive" section
DEFAULTS: dict[str, Any] = {
    "default_provider": "anthropic",
    "fallback_provider": "openai",
    "providers": {
        "anthropic": {
            "api_url": "https://api.anthropic.com/v1",
            "api_version": "2023-06-01",
            "models": {
                "premium": "claude-opus-4-6",
                "standard": "claude-sonnet-4-6",
                "economy": "claude-haiku-4-5-20251001",
            },
        },
        "openai": {
            "api_url": "https://api.openai.com/v1",
            "models": {
                "premium": "gpt-4o",
                "standard": "gpt-4o",
                "economy": "gpt-4o-mini",
            },
        },
        "ollama": {
            "api_url": "http://localhost:11434",
            "models": {
                "premium": "llama3.1:70b",
                "standard": "qwen2.5-coder:32b",
                "economy": "llama3.1:8b",
            },
        },
        "openrouter": {
            "api_url": "https://openrouter.ai/api/v1",
            "models": {
                "premium": "anthropic/claude-opus-4-6",
                "standard": "anthropic/claude-sonnet-4-6",
                "economy": "meta-llama/llama-3.1-8b-instruct",
            },
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _detect_instance() -> str:
    """Detect which instance we're running on from cwd or hostname."""
    cwd = str(Path.cwd())
    for name in ("alpha", "beta", "gamma"):
        if f"instances/{name}" in cwd:
            return name
    # Fallback: check hostname patterns
    import socket
    hostname = socket.gethostname().lower()
    if "air" in hostname or "macbook" in hostname:
        return "alpha"
    return ""


class AdaptiveConfig:
    """Loads and provides access to adaptive layer configuration."""

    def __init__(self, config_path: Path | None = None, instance: str | None = None):
        self._path = config_path or LOVE_JSON
        self._raw = self._load()
        self._config = _deep_merge(DEFAULTS, self._raw.get("adaptive", {}))
        self._instance = instance or _detect_instance()
        self._apply_instance_overrides()

    def _load(self) -> dict:
        if self._path.exists():
            with open(self._path) as f:
                return json.load(f)
        return {}

    def _apply_instance_overrides(self):
        """Apply per-instance model overrides from love.json instances section."""
        if not self._instance:
            return
        instance_cfg = self._raw.get("instances", {}).get(self._instance, {})
        ollama_overrides = instance_cfg.get("ollama", {})
        if ollama_overrides:
            # Override ollama models with instance-specific ones
            ollama_models = self._config.get("providers", {}).get("ollama", {}).get("models", {})
            for tier, model in ollama_overrides.items():
                if tier in ("economy", "standard", "premium"):
                    ollama_models[tier] = model

    @property
    def instance(self) -> str:
        return self._instance

    def reload(self):
        """Hot-reload config from disk."""
        self._raw = self._load()
        self._config = _deep_merge(DEFAULTS, self._raw.get("adaptive", {}))
        self._apply_instance_overrides()

    @property
    def default_provider(self) -> str:
        return self._config["default_provider"]

    @property
    def fallback_provider(self) -> str:
        return self._config["fallback_provider"]

    def provider_config(self, provider: str) -> dict:
        """Get config for a specific provider."""
        return self._config.get("providers", {}).get(provider, {})

    def model_for(self, provider: str, tier: str) -> str:
        """Get the model name for a provider and capability tier."""
        pconf = self.provider_config(provider)
        models = pconf.get("models", {})
        return models.get(tier, models.get("standard", ""))

    def api_url(self, provider: str) -> str:
        return self.provider_config(provider).get("api_url", "")

    def all_providers(self) -> list[str]:
        return list(self._config.get("providers", {}).keys())

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def load_api_key(self, provider: str) -> str:
        """Load API key for a provider. Priority: credentials tool -> env -> .env.kingdom -> love.json."""
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_key = env_map.get(provider, f"{provider.upper()}_API_KEY")

        # 1. Try credentials.py (keychain -> vault -> env)
        cred_tool = LOVE_DIR / "tools" / "credentials.py"
        if cred_tool.exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("credentials", cred_tool)
                creds = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(creds)
                # credentials.py uses names like "anthropic-primary", "openai-primary"
                vault_key = f"{provider}-primary"
                key = creds.get_key(vault_key, fallback=None)
                if key:
                    return key
            except (ValueError, ImportError):
                # ValueError = credential not found, ImportError = missing dep
                pass
            except Exception:
                pass

        # 2. Environment variable
        key = os.environ.get(env_key, "")
        if key:
            return key

        # 3. ~/.env.kingdom
        env_kingdom = Path.home() / ".env.kingdom"
        if env_kingdom.exists():
            for line in env_kingdom.read_text().splitlines():
                if line.startswith(f"export {env_key}="):
                    k = line.split("=", 1)[-1].strip().strip('"').strip("'")
                    if k:
                        return k

        # 4. love.json env section
        key = self._raw.get("env", {}).get(env_key, "")
        if key:
            return key

        # 5. Ollama doesn't need a key
        if provider == "ollama":
            return "no-key-needed"

        return ""
