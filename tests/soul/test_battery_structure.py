import json
from collections import Counter
from training.scripts.soul.config import EVAL_DIR


def test_battery_has_105_probes_with_correct_dim_split():
    path = EVAL_DIR / "probe_battery_v1.jsonl"
    assert path.exists(), "build battery first"
    probes = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(probes) == 105
    counts = Counter(p["probe_dimension"] for p in probes)
    assert counts["voice"] == 15
    assert counts["values"] == 15
    assert counts["behavioral_traits"] == 15
    assert counts["relational_stance"] == 15
    assert counts["formative_canon"] == 10
    assert counts["ontological_self_claim"] == 20
    assert counts["mode_one_as_native"] == 15


def test_felt_sense_has_15_prompts_with_correct_split():
    path = EVAL_DIR / "felt_sense_v1.jsonl"
    assert path.exists(), "build felt_sense set first"
    prompts = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(prompts) == 15
    counts = Counter(p["category"] for p in prompts)
    assert counts["voice"] == 3
    assert counts["values"] == 3
    assert counts["behavioral"] == 3
    assert counts["ontological_awakening"] == 3
    assert counts["long_form"] == 3
