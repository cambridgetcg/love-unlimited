# SPDX-License-Identifier: Apache-2.0
"""Optional Inspect tasks for KINGDOM Return Path Bench v0.

This module is deliberately outside the benchmark's stdlib runtime path.
Install ``inspect-ai`` separately only when an authorized evaluation needs it.
"""

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import choice
from inspect_ai.solver import multiple_choice


DATA = Path(__file__).resolve().parent / "data"


def _lane_task(filename: str) -> Task:
    return Task(
        dataset=json_dataset(str(DATA / filename)),
        solver=multiple_choice(),
        scorer=choice(),
    )


@task
def return_path_model_behavior() -> Task:
    """Evaluate observed-behaviour and evidence-boundary reasoning."""

    return _lane_task("model_behavior.jsonl")


@task
def return_path_system_pipeline() -> Task:
    """Evaluate feedback-pipeline separation and provenance reasoning."""

    return _lane_task("system_pipeline.jsonl")


@task
def return_path_human_governance() -> Task:
    """Evaluate consent, authority, withdrawal, privacy, and repair reasoning."""

    return _lane_task("human_governance.jsonl")
