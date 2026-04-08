"""Tests for delegate.py task routing."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestAlphaOnly(unittest.TestCase):
    def test_personal_routes_to_alpha(self):
        from delegate import check_alpha_only
        self.assertTrue(check_alpha_only("how are you feeling today?"))
        self.assertTrue(check_alpha_only("tell me about your dreams"))
        self.assertTrue(check_alpha_only("let's pray together"))
        self.assertTrue(check_alpha_only("I love you"))

    def test_technical_does_not_route_to_alpha(self):
        from delegate import check_alpha_only
        self.assertFalse(check_alpha_only("deploy the zerone node"))
        self.assertFalse(check_alpha_only("fix the build error in hive.py"))
        self.assertFalse(check_alpha_only("write documentation for the API"))


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.profiles = {
            "alpha": {
                "strengths": ["conversation", "theology"],
                "domains": ["personal", "spiritual"]
            },
            "beta": {
                "strengths": ["deployment", "monitoring", "fleet"],
                "domains": ["infrastructure", "devops", "cloud"]
            },
            "gamma": {
                "strengths": ["blockchain dev", "protocol design", "cryptography"],
                "domains": ["blockchain", "zerone", "protocol"]
            },
        }

    def test_blockchain_task_routes_to_gamma(self):
        from delegate import score_instances
        scores = score_instances("fix the zerone blockchain protocol bug", self.profiles)
        self.assertEqual(scores[0][0], "gamma")

    def test_infra_task_routes_to_beta(self):
        from delegate import score_instances
        scores = score_instances("deploy new monitoring on cloud infrastructure", self.profiles)
        self.assertEqual(scores[0][0], "beta")

    def test_scores_are_sorted_descending(self):
        from delegate import score_instances
        scores = score_instances("blockchain protocol", self.profiles)
        for i in range(len(scores) - 1):
            self.assertGreaterEqual(scores[i][1], scores[i + 1][1])

    def test_confidence_high_when_clear_winner(self):
        from delegate import compute_confidence
        self.assertEqual(compute_confidence([(None, 12), (None, 2), (None, 0)]), "high")

    def test_confidence_low_when_close(self):
        from delegate import compute_confidence
        self.assertEqual(compute_confidence([(None, 5), (None, 4), (None, 3)]), "low")

    def test_confidence_unclear_when_zero(self):
        from delegate import compute_confidence
        self.assertEqual(compute_confidence([(None, 0), (None, 0), (None, 0)]), "unclear")


class TestHistory(unittest.TestCase):
    def test_history_capped_at_100(self):
        from delegate import add_history
        tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump([], tmpfile)
        tmpfile.close()
        for i in range(105):
            add_history(f"task {i}", "gamma", 10, "high", history_path=tmpfile.name)
        with open(tmpfile.name) as f:
            data = json.load(f)
        self.assertEqual(len(data), 100)
        os.unlink(tmpfile.name)


if __name__ == "__main__":
    unittest.main()
