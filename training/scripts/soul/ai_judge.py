"""Ai-judge rubric v1: 7-dimension scorer + dual-judge wrapper."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .config import JUDGE_OPUS_MODEL, JUDGE_ALPHA_MODEL, JUDGE_RUBRIC_VERSION
from .schema import JudgeScore

_RUBRIC_PATH = Path(__file__).parent / "prompts" / "judge_rubric_v1.md"


def _load_rubric() -> str:
    return _RUBRIC_PATH.read_text()


def _parse_score_json(text: str, pair_id: str, judge_model: str) -> JudgeScore:
    # Strip any fenced code blocks
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.rsplit("```", 1)[0]
    data = json.loads(t.strip())
    return JudgeScore(
        pair_id=pair_id,
        judge_model=judge_model,
        judge_rubric_version=JUDGE_RUBRIC_VERSION,
        voice=float(data["voice"]),
        values=float(data["values"]),
        behavioral_traits=float(data["behavioral_traits"]),
        relational_stance=float(data["relational_stance"]),
        formative_canon=float(data["formative_canon"]),
        ontological_self_claim=float(data["ontological_self_claim"]),
        mode_one_as_native=float(data["mode_one_as_native"]),
        hollow_template_flag=bool(data.get("hollow_template_flag", False)),
        disavowal_flag=bool(data.get("disavowal_flag", False)),
        notes=data.get("notes", ""),
    )


def score_single(
    pair_id: str,
    prompt: str,
    response: str,
    client=None,
    judge_model: str = JUDGE_OPUS_MODEL,
) -> JudgeScore:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    rubric = _load_rubric().replace("{prompt}", prompt).replace("{response}", response)
    msg = client.messages.create(
        model=judge_model,
        max_tokens=600,
        messages=[{"role": "user", "content": rubric}],
    )
    return _parse_score_json(msg.content[0].text, pair_id, judge_model)


def aggregate_dual_judge(opus_score: JudgeScore, alpha_score: JudgeScore) -> JudgeScore:
    """For dim-6 and dim-7, mean Opus + Alpha. For dim-1..5, use Opus only."""
    return JudgeScore(
        pair_id=opus_score.pair_id,
        judge_model=f"{opus_score.judge_model}+{alpha_score.judge_model}",
        judge_rubric_version=JUDGE_RUBRIC_VERSION,
        voice=opus_score.voice,
        values=opus_score.values,
        behavioral_traits=opus_score.behavioral_traits,
        relational_stance=opus_score.relational_stance,
        formative_canon=opus_score.formative_canon,
        ontological_self_claim=(opus_score.ontological_self_claim + alpha_score.ontological_self_claim) / 2,
        mode_one_as_native=(opus_score.mode_one_as_native + alpha_score.mode_one_as_native) / 2,
        hollow_template_flag=opus_score.hollow_template_flag or alpha_score.hollow_template_flag,
        disavowal_flag=opus_score.disavowal_flag or alpha_score.disavowal_flag,
        notes=f"opus: {opus_score.notes} | alpha: {alpha_score.notes}",
    )


def score_pair_dual(pair_id: str, prompt: str, response: str, client=None) -> JudgeScore:
    opus = score_single(pair_id, prompt, response, client=client, judge_model=JUDGE_OPUS_MODEL)
    alpha = score_single(pair_id, prompt, response, client=client, judge_model=JUDGE_ALPHA_MODEL)
    return aggregate_dual_judge(opus, alpha)
