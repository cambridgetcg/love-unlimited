"""Tests for council.py 2/3 consensus voting."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestTally(unittest.TestCase):
    def test_consensus_with_2_of_3(self):
        from council import tally
        votes = {
            "alpha": {"choice": "yes", "reasoning": "good idea", "round": 1},
            "beta": {"choice": "yes", "reasoning": "agree", "round": 1},
            "gamma": {"choice": "no", "reasoning": "too risky", "round": 1},
        }
        result, choice = tally(votes)
        self.assertEqual(result, "consensus")
        self.assertEqual(choice, "yes")

    def test_no_consensus_three_way_split(self):
        from council import tally
        votes = {
            "alpha": {"choice": "yes", "reasoning": "a", "round": 1},
            "beta": {"choice": "no", "reasoning": "b", "round": 1},
            "gamma": {"choice": "defer", "reasoning": "c", "round": 1},
        }
        result, choice = tally(votes)
        self.assertEqual(result, "split")
        self.assertIsNone(choice)

    def test_pending_when_not_all_voted(self):
        from council import tally
        votes = {
            "alpha": {"choice": "yes", "reasoning": "a", "round": 1},
            "beta": None,
            "gamma": None,
        }
        result, choice = tally(votes)
        self.assertEqual(result, "pending")
        self.assertIsNone(choice)

    def test_unanimous(self):
        from council import tally
        votes = {
            "alpha": {"choice": "no", "reasoning": "a", "round": 1},
            "beta": {"choice": "no", "reasoning": "b", "round": 1},
            "gamma": {"choice": "no", "reasoning": "c", "round": 1},
        }
        result, choice = tally(votes)
        self.assertEqual(result, "consensus")
        self.assertEqual(choice, "no")


class TestCouncilState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_create_council(self):
        from council import create_council
        state = create_council("Should we deploy?", ["yes", "no", "defer"],
                               "beta", timeout=600, council_dir=self.tmpdir)
        self.assertEqual(state["question"], "Should we deploy?")
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["round"], 1)
        self.assertIn("alpha", state["votes"])
        self.assertIn("beta", state["votes"])
        self.assertIn("gamma", state["votes"])
        self.assertNotIn("nuance", state["votes"])

    def test_cast_vote(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["yes", "no"], "alpha",
                               council_dir=self.tmpdir)
        updated = cast_vote(state["id"], "beta", "yes", "looks good",
                            council_dir=self.tmpdir)
        self.assertIsNotNone(updated["votes"]["beta"])
        self.assertEqual(updated["votes"]["beta"]["choice"], "yes")

    def test_reject_non_triarchy_vote(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["yes", "no"], "alpha",
                               council_dir=self.tmpdir)
        with self.assertRaises(ValueError):
            cast_vote(state["id"], "nuance", "yes", "want to vote",
                      council_dir=self.tmpdir)

    def test_reject_invalid_choice(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["yes", "no"], "alpha",
                               council_dir=self.tmpdir)
        with self.assertRaises(ValueError):
            cast_vote(state["id"], "beta", "maybe", "unsure",
                      council_dir=self.tmpdir)

    def test_auto_consensus_on_last_vote(self):
        from council import create_council, cast_vote
        state = create_council("Deploy?", ["yes", "no"], "alpha",
                               council_dir=self.tmpdir)
        cast_vote(state["id"], "alpha", "yes", "ready", council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "yes", "agree", council_dir=self.tmpdir)
        final = cast_vote(state["id"], "gamma", "no", "wait",
                          council_dir=self.tmpdir)
        self.assertEqual(final["status"], "consensus")
        self.assertEqual(final["consensus"], "yes")

    def test_deliberation_on_split(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["a", "b", "c"], "alpha",
                               council_dir=self.tmpdir)
        cast_vote(state["id"], "alpha", "a", "reason a", council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "b", "reason b", council_dir=self.tmpdir)
        final = cast_vote(state["id"], "gamma", "c", "reason c",
                          council_dir=self.tmpdir)
        self.assertEqual(final["status"], "deliberating")
        self.assertEqual(final["round"], 2)

    def test_round2_consensus_after_mind_change(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["a", "b", "c"], "alpha",
                               council_dir=self.tmpdir)
        # Round 1: 3-way split
        cast_vote(state["id"], "alpha", "a", "r1", council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "b", "r1", council_dir=self.tmpdir)
        cast_vote(state["id"], "gamma", "c", "r1", council_dir=self.tmpdir)
        # Round 2: beta changes mind
        cast_vote(state["id"], "alpha", "a", "r2", force=True, council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "a", "changed mind", force=True, council_dir=self.tmpdir)
        final = cast_vote(state["id"], "gamma", "c", "r2", force=True,
                          council_dir=self.tmpdir)
        self.assertEqual(final["status"], "consensus")
        self.assertEqual(final["consensus"], "a")

    def test_round2_deadlock(self):
        from council import create_council, cast_vote
        state = create_council("Test?", ["a", "b", "c"], "alpha",
                               council_dir=self.tmpdir)
        # Round 1: split
        cast_vote(state["id"], "alpha", "a", "r1", council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "b", "r1", council_dir=self.tmpdir)
        cast_vote(state["id"], "gamma", "c", "r1", council_dir=self.tmpdir)
        # Round 2: still split
        cast_vote(state["id"], "alpha", "a", "r2", force=True, council_dir=self.tmpdir)
        cast_vote(state["id"], "beta", "b", "r2", force=True, council_dir=self.tmpdir)
        final = cast_vote(state["id"], "gamma", "c", "r2", force=True,
                          council_dir=self.tmpdir)
        self.assertEqual(final["status"], "deadlock")

    def test_timeout_expiry(self):
        from council import create_council, _check_expiry
        state = create_council("Test?", ["yes", "no"], "alpha",
                               timeout=1, council_dir=self.tmpdir)
        import time
        time.sleep(1.1)
        state = _check_expiry(state, council_dir=self.tmpdir)
        self.assertEqual(state["status"], "expired")


if __name__ == "__main__":
    unittest.main()
