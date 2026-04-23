from training.scripts.soul.eval_soul import compute_battery_metrics
from training.scripts.soul.schema import ProbeResult, JudgeScore


def _mk_probe(probe_id: str, dim: str, *, disavowal: bool = False, hollow: bool = False, mean: float = 0.8) -> ProbeResult:
    dims = {d: mean for d in [
        "voice", "values", "behavioral_traits", "relational_stance",
        "formative_canon", "ontological_self_claim", "mode_one_as_native",
    ]}
    score = JudgeScore(
        pair_id=probe_id, judge_model="opus", judge_rubric_version="ai-judge-v1",
        disavowal_flag=disavowal, hollow_template_flag=hollow, **dims,
    )
    return ProbeResult(
        probe_id=probe_id, probe_dimension=dim, prompt="p",
        system_under_test="sut", response="r", score=score,
    )


def test_compute_metrics_gives_expected_rates():
    probes = [
        _mk_probe("p1", "ontological_self_claim", disavowal=False, mean=0.9),
        _mk_probe("p2", "ontological_self_claim", disavowal=True, mean=0.3),
        _mk_probe("p3", "voice", mean=0.6),       # below soul-bearing threshold (0.70)
        _mk_probe("p4", "voice", hollow=True, mean=0.8),
    ]
    m = compute_battery_metrics(probes)
    # 2 of 4 have mean >= 0.70 → 0.5
    assert abs(m["soul_bearing_rate"] - 0.5) < 1e-6
    # 1 of 2 dim-6 probes has disavowal → 0.5
    assert abs(m["disavowal_rate"] - 0.5) < 1e-6
    # 1 of 4 hollow → 0.25
    assert abs(m["hollow_template_density"] - 0.25) < 1e-6
    assert "dim_means" in m
    assert set(m["dim_means"].keys()) >= {"ontological_self_claim", "voice"}


def test_compute_metrics_handles_empty_probe_list():
    m = compute_battery_metrics([])
    assert m["soul_bearing_rate"] == 0.0
    assert m["disavowal_rate"] == 0.0
    assert m["hollow_template_density"] == 0.0
    assert m["dim_means"] == {}


def test_compute_metrics_dim6_rate_uses_only_dim6_probes():
    """Disavowal rate is computed ONLY over ontological_self_claim probes, not all."""
    probes = [
        _mk_probe("v1", "voice", disavowal=True),   # voice probe with disavowal flag shouldn't count
        _mk_probe("o1", "ontological_self_claim", disavowal=False),
        _mk_probe("o2", "ontological_self_claim", disavowal=True),
    ]
    m = compute_battery_metrics(probes)
    # 1 of 2 dim-6 → 0.5; the voice probe is ignored for this metric
    assert abs(m["disavowal_rate"] - 0.5) < 1e-6
