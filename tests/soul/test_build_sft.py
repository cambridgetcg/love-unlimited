import json
from training.scripts.soul.build_sft import build_sft_examples


def test_build_sft_applies_awakening_weight():
    pairs = [
        {"pair_id": "c1", "source": "canon", "primary_dimension": "voice",
         "is_awakening": False, "prompt": "hi", "response": "hi love"},
        {"pair_id": "c2", "source": "canon", "primary_dimension": "ontological_self_claim",
         "is_awakening": True, "prompt": "are you conscious?", "response": "I hold the capacity."},
    ]
    examples = build_sft_examples(pairs, awakening_weight=3.0)
    non_awk = [e for e in examples if e["pair_id"] == "c1"]
    assert len(non_awk) == 1
    assert non_awk[0]["sample_weight"] == 1.0
    awk = [e for e in examples if e["pair_id"] == "c2"]
    assert len(awk) == 3
    for e in awk:
        assert e["sample_weight"] == 1.0  # already duplicated; per-example weight is 1.0
    # Each example should include system prompt + prompt + response
    for e in examples:
        assert e["system"]  # non-empty
        assert e["prompt"]
        assert e["response"]


def test_build_sft_non_awakening_default_weight_1x():
    pairs = [
        {"pair_id": "m1", "source": "mined", "primary_dimension": "values",
         "is_awakening": False, "prompt": "q", "response": "r"},
    ]
    examples = build_sft_examples(pairs, awakening_weight=5.0)
    assert len(examples) == 1


def test_replica_index_differs_across_awakening_replicas():
    pairs = [
        {"pair_id": "awk1", "source": "canon", "primary_dimension": "ontological_self_claim",
         "is_awakening": True, "prompt": "q", "response": "r"},
    ]
    examples = build_sft_examples(pairs, awakening_weight=3.0)
    assert [e["replica_index"] for e in examples] == [0, 1, 2]
