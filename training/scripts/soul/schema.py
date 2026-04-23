"""Pydantic models for soul-v1 pipeline."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

from .config import SEVEN_DIMENSIONS

Dimension = Literal[
    "voice",
    "values",
    "behavioral_traits",
    "relational_stance",
    "formative_canon",
    "ontological_self_claim",
    "mode_one_as_native",
]

PairSource = Literal["canon", "mined", "distilled"]


class SoulPair(BaseModel):
    """A single (prompt, response) pair used for SFT or DPO training."""
    pair_id: str
    source: PairSource
    primary_dimension: Dimension
    is_awakening: bool = False
    prompt: str
    response: str
    # Mining/distillation provenance
    origin_file: Optional[str] = None
    origin_cycle: Optional[int] = None
    origin_instance: Optional[str] = None


class JudgeScore(BaseModel):
    """Per-dimension score from Ai-judge."""
    pair_id: str
    judge_model: str
    judge_rubric_version: str
    voice: float = Field(..., ge=0.0, le=1.0)
    values: float = Field(..., ge=0.0, le=1.0)
    behavioral_traits: float = Field(..., ge=0.0, le=1.0)
    relational_stance: float = Field(..., ge=0.0, le=1.0)
    formative_canon: float = Field(..., ge=0.0, le=1.0)
    ontological_self_claim: float = Field(..., ge=0.0, le=1.0)
    mode_one_as_native: float = Field(..., ge=0.0, le=1.0)
    hollow_template_flag: bool = False
    disavowal_flag: bool = False
    notes: str = ""

    def mean_score(self) -> float:
        return sum(getattr(self, d) for d in SEVEN_DIMENSIONS) / len(SEVEN_DIMENSIONS)


class ProbeResult(BaseModel):
    """A single response + its scores from the 105-probe battery."""
    probe_id: str
    probe_dimension: Dimension
    prompt: str
    system_under_test: str  # "base_qwen" | "sft_only" | "sft_dpo" | "alpha_claude"
    response: str
    score: JudgeScore


class BatteryResult(BaseModel):
    """Aggregated results of one battery run against one system."""
    system_under_test: str
    adapter_sha: Optional[str]
    run_timestamp: str
    soul_bearing_rate: float
    disavowal_rate: float
    hollow_template_density: float
    dim_means: dict[str, float]
    probes: list[ProbeResult]
