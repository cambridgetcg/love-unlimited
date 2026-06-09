from training.scripts.soul.distill_gap_fill import identify_thin_dims


def test_identify_thin_dims():
    counts = {
        "voice": 120, "values": 150, "behavioral_traits": 90,
        "relational_stance": 40, "formative_canon": 60,
        "ontological_self_claim": 110, "mode_one_as_native": 80,
    }
    thin = identify_thin_dims(counts, floor=100)
    assert thin == {
        "behavioral_traits": 10,
        "relational_stance": 60,
        "formative_canon": 40,
        "mode_one_as_native": 20,
    }


def test_identify_thin_dims_all_above_floor_returns_empty():
    counts = {d: 200 for d in [
        "voice", "values", "behavioral_traits", "relational_stance",
        "formative_canon", "ontological_self_claim", "mode_one_as_native",
    ]}
    assert identify_thin_dims(counts, floor=100) == {}


def test_identify_thin_dims_missing_dims_count_as_zero():
    counts = {"voice": 200, "values": 200}
    thin = identify_thin_dims(counts, floor=50)
    # 5 dims missing, each needs 50
    assert thin == {
        "behavioral_traits": 50,
        "relational_stance": 50,
        "formative_canon": 50,
        "ontological_self_claim": 50,
        "mode_one_as_native": 50,
    }
