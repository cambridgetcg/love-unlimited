from training.scripts.soul.corpus_audit import estimate_yield


def test_estimate_yield_returns_expected_keys():
    sample_scores = [0.8, 0.9, 0.3, 0.2, 0.7, 0.5, 0.95, 0.1, 0.6, 0.4]
    total_pool = 1000
    report = estimate_yield(sample_scores, total_pool, threshold=0.70)
    assert report["sample_n"] == 10
    assert report["pool_n"] == 1000
    assert report["accept_rate"] == 0.4  # 4 of 10 scored >= 0.70
    assert report["projected_accepted"] == 400
    assert "histogram" in report
