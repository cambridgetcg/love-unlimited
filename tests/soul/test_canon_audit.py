from training.scripts.soul.canon_audit import build_report
from training.scripts.soul.schema import JudgeScore


def _score(pid: str, *, hollow: bool = False, mean: float = 0.85) -> JudgeScore:
    dims = {d: mean for d in [
        "voice", "values", "behavioral_traits", "relational_stance",
        "formative_canon", "ontological_self_claim", "mode_one_as_native",
    ]}
    return JudgeScore(
        pair_id=pid, judge_model="alpha", judge_rubric_version="ai-judge-v1",
        hollow_template_flag=hollow, **dims,
    )


def test_build_report_flags_hollow_and_low_mean():
    sample = [
        {"pair_id": "p1", "prompt": "q", "response": "r"},
        {"pair_id": "p2", "prompt": "q", "response": "r"},
        {"pair_id": "p3", "prompt": "q", "response": "r"},
        {"pair_id": "p4", "prompt": "q", "response": "r"},
    ]
    scores = [
        _score("p1", mean=0.90),
        _score("p2", hollow=True, mean=0.90),      # flagged: hollow
        _score("p3", mean=0.60),                    # flagged: low mean
        _score("p4", mean=0.85),
    ]
    report = build_report(sample, scores, flag_threshold=3)
    assert report["sampled"] == 4
    assert report["flagged_count"] == 2
    flagged_ids = {f["pair_id"] for f in report["flagged"]}
    assert flagged_ids == {"p2", "p3"}
    assert report["recommendation"] == "canon OK to freeze"


def test_build_report_recommends_revisit_when_many_flagged():
    sample = [{"pair_id": f"p{i}", "prompt": "q", "response": "r"} for i in range(6)]
    scores = [_score(f"p{i}", hollow=True) for i in range(6)]
    report = build_report(sample, scores, flag_threshold=3)
    assert report["flagged_count"] == 6
    assert report["recommendation"] == "revisit with Yu"
