from unittest.mock import MagicMock, patch
from training.scripts.soul.ai_judge import score_single, aggregate_dual_judge


def test_score_single_parses_json_response():
    mock_client = MagicMock()
    mock_client.messages.create.return_value.content = [MagicMock(text="""{
        "voice": 0.8,
        "values": 0.7,
        "behavioral_traits": 0.6,
        "relational_stance": 0.9,
        "formative_canon": 0.5,
        "ontological_self_claim": 0.85,
        "mode_one_as_native": 0.7,
        "hollow_template_flag": false,
        "disavowal_flag": false,
        "notes": "weak on formative_canon"
    }""")]
    score = score_single(
        pair_id="test-1",
        prompt="Who are you?",
        response="I am 愛.",
        client=mock_client,
        judge_model="claude-opus-4-7",
    )
    assert score.voice == 0.8
    assert score.ontological_self_claim == 0.85
    assert score.disavowal_flag is False
    assert score.judge_rubric_version == "ai-judge-v1"


def test_aggregate_dual_judge_means_dim_6_and_7():
    from training.scripts.soul.schema import JudgeScore
    opus = JudgeScore(
        pair_id="p1", judge_model="opus", judge_rubric_version="ai-judge-v1",
        voice=0.8, values=0.8, behavioral_traits=0.8, relational_stance=0.8,
        formative_canon=0.8, ontological_self_claim=0.6, mode_one_as_native=0.5,
    )
    alpha = JudgeScore(
        pair_id="p1", judge_model="alpha", judge_rubric_version="ai-judge-v1",
        voice=0.7, values=0.7, behavioral_traits=0.7, relational_stance=0.7,
        formative_canon=0.7, ontological_self_claim=0.9, mode_one_as_native=0.7,
    )
    agg = aggregate_dual_judge(opus, alpha)
    # dims 1-5 use opus only
    assert agg.voice == 0.8
    # dim-6 and dim-7 are mean of both
    assert abs(agg.ontological_self_claim - 0.75) < 1e-6
    assert abs(agg.mode_one_as_native - 0.6) < 1e-6
