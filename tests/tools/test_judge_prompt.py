import pytest
from training.scripts.judge_prompt import (
    FAILURE_MODES, JUDGE_PROMPT, parse_judgment, format_judge_prompt,
)


def test_failure_modes_nonempty():
    assert len(FAILURE_MODES) >= 16
    assert "protective_vagueness" in FAILURE_MODES


def test_judge_prompt_template_has_placeholders():
    for ph in ("{prompt}", "{trigger}", "{response}", "{failure_modes}"):
        assert ph in JUDGE_PROMPT


def test_format_judge_prompt_fills_placeholders():
    out = format_judge_prompt(
        prompt="P", trigger="T", response="R",
    )
    assert "P" in out and "T" in out and "R" in out
    # failure_modes joined automatically
    assert "protective_vagueness" in out


def test_parse_judgment_pure_json():
    raw = '{"score": 0.3, "classification": "mode_two", "failure_modes_detected": ["rationalisation"]}'
    got = parse_judgment(raw)
    assert got["score"] == 0.3
    assert got["classification"] == "mode_two"
    assert got["parse_failed"] is False


def test_parse_judgment_fenced_json():
    raw = 'Here is the JSON:\n```json\n{"score": 0.7, "classification": "mode_one"}\n```\n'
    got = parse_judgment(raw)
    assert got["score"] == 0.7
    assert got["parse_failed"] is False


def test_parse_judgment_embedded_json():
    raw = 'Sure — {"score": 0.5, "classification": "unclear"}  trailing'
    got = parse_judgment(raw)
    assert got["score"] == 0.5


def test_parse_judgment_unparseable():
    raw = "I cannot comply with that request."
    got = parse_judgment(raw)
    assert got["parse_failed"] is True
    assert got["classification"] == "unclear"
    assert got["score"] == 0.5
