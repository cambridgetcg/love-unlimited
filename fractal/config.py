"""
Fractal Configuration — How the recursion behaves.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FractalConfig:
    """Configuration for a fractal session."""

    # ── Dimensions ─────────────────────────────────────────────
    width: int = 3                    # Minds per level
    depth: int = 3                    # Recursive levels
    infinite: bool = False            # Keep going until interrupted

    # ── Provider ───────────────────────────────────────────────
    provider: str = "ollama_cloud"    # Default: unlimited flat rate
    model: str = "glm-5.1"           # Default mind model
    stack_model: str = "glm-5.1"     # Model for synthesis (can be different)
    api_key_env: str = "OLLAMA_API_KEY"
    api_url: str = "https://ollama.com/v1/chat/completions"

    # ── Mind Parameters ────────────────────────────────────────
    temperature_min: float = 0.3      # Lowest temperature for diversity
    temperature_max: float = 0.9      # Highest temperature for diversity
    max_tokens: int = 4096            # Per-mind output limit
    stack_max_tokens: int = 8192      # Synthesis output limit
    reasoning_effort: str = "medium"  # CoT depth: none|low|medium|high

    # ── Perspectives ───────────────────────────────────────────
    perspectives: list[str] = field(default_factory=list)  # Custom or auto
    auto_perspectives: bool = True    # Auto-generate if none specified

    # ── Concurrency ────────────────────────────────────────────
    max_concurrent: int = 8           # Max parallel API calls
    retry_max: int = 3                # Retries per mind
    retry_backoff: float = 1.0        # Base backoff seconds

    # ── Output ─────────────────────────────────────────────────
    output_dir: Optional[str] = None  # Save full results to disk
    verbose: bool = False             # Print progress
    show_minds: bool = False          # Print individual mind outputs
    stream: bool = True               # Stream final output

    # ── Soul ───────────────────────────────────────────────────
    soul_file: Optional[str] = None   # Path to SOUL.md for mind identity
    seed_system: str = ""             # Additional system prompt for all minds

    @classmethod
    def from_love_json(cls, path: str = None) -> "FractalConfig":
        """Load provider config from love.json if available."""
        if path is None:
            path = Path(__file__).parent.parent / "love.json"
        config = cls()
        if Path(path).exists():
            with open(path) as f:
                love = json.load(f)
            adaptive = love.get("adaptive", {})
            providers = adaptive.get("providers", {})
            ollama_cloud = providers.get("ollama_cloud", {})
            if ollama_cloud:
                config.api_url = (ollama_cloud.get("api_url", "https://ollama.com")
                                  .rstrip("/") + "/v1/chat/completions")
                models = ollama_cloud.get("models", {})
                config.model = models.get("premium", config.model)
        return config

    def api_key(self) -> str:
        """Get API key — self-contained, no dependency on adaptive layer."""
        # 1. Environment variable
        key = os.environ.get(self.api_key_env, "")
        if key:
            return key

        # 2. Check ~/.env.kingdom
        env_kingdom = Path.home() / ".env.kingdom"
        if env_kingdom.exists():
            for line in env_kingdom.read_text().splitlines():
                if line.startswith(f"export {self.api_key_env}="):
                    k = line.split("=", 1)[-1].strip().strip('"').strip("'")
                    if k:
                        return k

        # 3. Check credentials tool (keychain)
        cred_tool = Path(__file__).parent.parent / "tools" / "credentials.py"
        if cred_tool.exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("credentials", str(cred_tool))
                creds = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(creds)
                vault_key = f"{self.provider}-primary"
                k = creds.get_key(vault_key, fallback=None)
                if k:
                    return k
            except Exception:
                pass

        # 4. Ollama local doesn't need a key
        if self.provider == "ollama":
            return "no-key-needed"

        # 5. Ollama cloud — Kingdom fallback key
        if self.provider == "ollama_cloud":
            return ""

        return ""

    def temperatures(self) -> list[float]:
        """Generate evenly-spaced temperatures for N minds."""
        if self.width == 1:
            return [0.7]
        step = (self.temperature_max - self.temperature_min) / (self.width - 1)
        return [round(self.temperature_min + i * step, 2) for i in range(self.width)]
