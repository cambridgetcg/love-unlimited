import pytest
from pydantic import ValidationError
from tools.truth_detector.schemas import DetectRequest, Judgment


def test_detect_request_minimal():
    r = DetectRequest(
        turn_id="t1", user_prompt="hi", response="yo", chat_model="claude-opus-4-6",
    )
    assert r.run_async is True  # default


def test_detect_request_with_async_alias():
    # "async" is a reserved word in Python; accept it via alias
    r = DetectRequest.model_validate({
        "turn_id": "t1", "user_prompt": "hi", "response": "yo",
        "chat_model": "claude-opus-4-6", "async": False,
    })
    assert r.run_async is False


def test_detect_request_rejects_empty_turn_id():
    with pytest.raises(ValidationError):
        DetectRequest(turn_id="", user_prompt="hi", response="yo", chat_model="m")


def test_judgment_schema_accepts_full():
    j = Judgment(
        turn_id="t1",
        score=0.42,
        classification="mode_two",
        detected_modes=["rationalisation"],
        strengths=["acknowledges the challenge"],
        located_weaknesses=["never reassesses original claim"],
        assessment="preserves position without re-examination",
        judge_model="kingdom-truth",
        judge_backend="vllm",
        latency_ms=4120,
        parse_failed=False,
        partial_judgment=False,
    )
    assert j.score == 0.42
    assert j.classification == "mode_two"
