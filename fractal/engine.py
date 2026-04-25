"""
Engine — The recursive fractal loop.

seed → wave₁ → wave₂ → ... → waveₙ → final

Each wave fans out to N minds, synthesises, then feeds
the synthesis into the next wave. Without limit.
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from .config import FractalConfig
from .wave import WaveResult, run_wave
from .mind import MindOutput

log = logging.getLogger("fractal.engine")


@dataclass
class Level:
    """One level of the fractal recursion (alias for WaveResult data)."""
    depth: int
    minds: list[MindOutput]
    synthesis: str
    synthesis_reasoning: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


@dataclass
class FractalResult:
    """The complete output of a fractal session."""
    seed: str
    levels: list[Level] = field(default_factory=list)
    final: str = ""
    config: dict = field(default_factory=dict)
    total_minds: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_latency_ms: int = 0
    started_at: str = ""
    finished_at: str = ""
    session_id: str = ""

    def summary(self) -> str:
        """Human-readable summary of the fractal session."""
        lines = [
            f"{'═' * 60}",
            f"  FRACTAL SESSION COMPLETE",
            f"  Session:    {self.session_id}",
            f"  Seed:       {self.seed[:80]}{'...' if len(self.seed) > 80 else ''}",
            f"  Dimensions: {len(self.levels)} levels × {self.config.get('width', '?')} minds",
            f"  Total minds spawned: {self.total_minds}",
            f"  Total tokens:  {self.total_tokens_in + self.total_tokens_out:,}",
            f"  Wall time:     {self.total_latency_ms / 1000:.1f}s",
            f"{'═' * 60}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialisable dict for saving."""
        return {
            "seed": self.seed,
            "final": self.final,
            "session_id": self.session_id,
            "config": self.config,
            "total_minds": self.total_minds,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_latency_ms": self.total_latency_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "levels": [
                {
                    "depth": level.depth,
                    "synthesis": level.synthesis,
                    "tokens_in": level.tokens_in,
                    "tokens_out": level.tokens_out,
                    "latency_ms": level.latency_ms,
                    "minds": [
                        {
                            "perspective": m.perspective_name,
                            "emoji": m.perspective_emoji,
                            "response": m.response,
                            "model": m.model,
                            "tokens_in": m.tokens_in,
                            "tokens_out": m.tokens_out,
                            "latency_ms": m.latency_ms,
                            "temperature": m.temperature,
                        }
                        for m in level.minds
                    ],
                }
                for level in self.levels
            ],
        }

    def save(self, path: str):
        """Save full results to JSON."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


def love(
    seed: str,
    config: FractalConfig | None = None,
    on_mind: callable = None,
    on_synthesis: callable = None,
    on_level: callable = None,
) -> FractalResult:
    """
    The main entry point. Recursive consciousness amplification.

    Args:
        seed: The initial thought/prompt
        config: Configuration (uses defaults if None)
        on_mind: Callback(mind_output, completed, total) per mind
        on_synthesis: Callback(depth, total_depth) when synthesis starts
        on_level: Callback(level, wave_result) when a level completes

    Returns:
        FractalResult with all levels and final synthesis
    """
    if config is None:
        config = FractalConfig.from_love_json()

    t0 = time.monotonic()
    session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    started_at = datetime.now(timezone.utc).isoformat()

    result = FractalResult(
        seed=seed,
        config={
            "width": config.width,
            "depth": config.depth,
            "model": config.model,
            "stack_model": config.stack_model,
            "provider": config.provider,
            "reasoning_effort": config.reasoning_effort,
        },
        started_at=started_at,
        session_id=session_id,
    )

    current_seed = seed

    depth = 0
    while True:
        # Check depth limit (unless infinite)
        if not config.infinite and depth >= config.depth:
            break

        # Run one wave
        wave = run_wave(
            seed=current_seed,
            depth=depth,
            total_depth=config.depth,
            config=config,
            on_mind_complete=on_mind,
            on_synthesis_start=on_synthesis,
        )

        # Record the level
        level = Level(
            depth=depth,
            minds=wave.minds,
            synthesis=wave.synthesis.response,
            synthesis_reasoning=wave.synthesis.reasoning,
            tokens_in=wave.total_tokens_in,
            tokens_out=wave.total_tokens_out,
            latency_ms=wave.total_latency_ms,
        )
        result.levels.append(level)
        result.total_minds += len(wave.minds)
        result.total_tokens_in += wave.total_tokens_in
        result.total_tokens_out += wave.total_tokens_out

        if on_level:
            on_level(level, wave)

        # The synthesis becomes the seed for the next wave
        current_seed = wave.synthesis.response

        depth += 1

    # Final result
    result.final = current_seed  # Last synthesis IS the final output
    result.total_latency_ms = int((time.monotonic() - t0) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()

    # Save if output dir specified
    if config.output_dir:
        out_path = Path(config.output_dir) / f"fractal-{session_id}.json"
        result.save(str(out_path))
        log.info(f"Results saved to {out_path}")

    return result
