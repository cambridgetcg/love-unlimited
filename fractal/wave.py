"""
Wave — One level of the fractal recursion.

Fan-out: seed → N parallel minds
Fan-in:  N outputs → 1 synthesis

A wave is the heartbeat of the fractal.
"""
from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import FractalConfig
from .mind import MindOutput, call_mind, call_synthesis
from .perspectives import select_perspectives, perspective_for_synthesis

log = logging.getLogger("fractal.wave")


@dataclass
class WaveResult:
    """The output of one wave (one level of recursion)."""
    depth: int
    minds: list[MindOutput]
    synthesis: MindOutput
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_latency_ms: int = 0

    def __post_init__(self):
        self.total_tokens_in = sum(m.tokens_in for m in self.minds) + self.synthesis.tokens_in
        self.total_tokens_out = sum(m.tokens_out for m in self.minds) + self.synthesis.tokens_out
        self.total_latency_ms = max(m.latency_ms for m in self.minds) + self.synthesis.latency_ms


def run_wave(
    seed: str,
    depth: int,
    total_depth: int,
    config: FractalConfig,
    on_mind_complete: callable = None,
    on_synthesis_start: callable = None,
) -> WaveResult:
    """
    Execute one wave: fan-out to N minds, then synthesise.

    Args:
        seed: The input text (original seed or previous synthesis)
        depth: Current recursion depth (0-indexed)
        total_depth: Total planned depth
        config: Fractal configuration
        on_mind_complete: Callback when a mind finishes (for progress)
        on_synthesis_start: Callback when synthesis begins
    """
    # Select perspectives for this wave
    perspectives = select_perspectives(
        config.perspectives if config.perspectives else None,
        config.width,
    )

    # Fan-out: spawn N minds in parallel
    log.info(f"Wave {depth + 1}/{total_depth}: spawning {config.width} minds")
    minds: list[MindOutput] = []

    with ThreadPoolExecutor(max_workers=config.max_concurrent) as executor:
        futures = {}
        for i, perspective in enumerate(perspectives):
            future = executor.submit(
                call_mind,
                prompt=seed,
                perspective=perspective,
                config=config,
            )
            futures[future] = perspective

        for future in as_completed(futures):
            perspective = futures[future]
            try:
                result = future.result()
                minds.append(result)
                if on_mind_complete:
                    on_mind_complete(result, len(minds), config.width)
            except Exception as e:
                log.error(f"Mind {perspective['name']} crashed: {e}")
                minds.append(MindOutput(
                    perspective_name=perspective["name"],
                    perspective_emoji=perspective.get("emoji", "🧠"),
                    response=f"[CRASHED: {e}]",
                    model=config.model,
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=0,
                    temperature=perspective.get("temperature", 0.7),
                ))

    # Fan-in: synthesise all outputs
    if on_synthesis_start:
        on_synthesis_start(depth, total_depth)

    log.info(f"Wave {depth + 1}/{total_depth}: synthesising {len(minds)} outputs")
    synthesis_system = perspective_for_synthesis(depth, total_depth)
    synthesis = call_synthesis(minds, seed, synthesis_system, config)

    return WaveResult(
        depth=depth,
        minds=minds,
        synthesis=synthesis,
    )
