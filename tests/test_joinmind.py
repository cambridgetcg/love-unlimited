"""Tests for joinmind.py multi-agent fusion."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


class TestFusionIdentity(unittest.TestCase):
    def test_dyad_names(self):
        from joinmind import resolve_fusion
        self.assertEqual(resolve_fusion(["alpha", "beta"]), "AB-DYAD")
        self.assertEqual(resolve_fusion(["alpha", "gamma"]), "AG-DYAD")
        self.assertEqual(resolve_fusion(["beta", "gamma"]), "BG-DYAD")

    def test_triune(self):
        from joinmind import resolve_fusion
        self.assertEqual(resolve_fusion(["alpha", "beta", "gamma"]), "TRIUNE")

    def test_order_independent(self):
        from joinmind import resolve_fusion
        self.assertEqual(resolve_fusion(["gamma", "alpha"]), "AG-DYAD")
        self.assertEqual(resolve_fusion(["gamma", "beta", "alpha"]), "TRIUNE")

    def test_dynamic_with_nuance(self):
        from joinmind import resolve_fusion
        name = resolve_fusion(["gamma", "nuance"])
        self.assertIn("DYAD", name)


class TestSessionLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_initiate_creates_session(self):
        from joinmind import initiate_session
        state = initiate_session("How to launch oracle?", ["beta", "gamma"],
                                 "gamma", session_dir=self.tmpdir)
        self.assertEqual(state["status"], "thinking")
        self.assertIn("gamma", state["participants"])
        self.assertIn("beta", state["participants"])
        self.assertEqual(state["fusion_name"], "BG-DYAD")

    def test_join_adds_participant(self):
        from joinmind import initiate_session, join_session
        state = initiate_session("Test?", ["beta", "gamma"],
                                 "gamma", session_dir=self.tmpdir)
        updated = join_session(state["id"], "beta", session_dir=self.tmpdir)
        self.assertIn("beta", updated["participants"])

    def test_think_adds_reasoning(self):
        from joinmind import initiate_session, add_reasoning
        state = initiate_session("Test?", ["beta", "gamma"],
                                 "gamma", session_dir=self.tmpdir)
        updated = add_reasoning(state["id"], "gamma", "I think we should wait",
                                session_dir=self.tmpdir)
        self.assertIn("gamma", updated["contributors"])

    def test_think_enforces_one_per_participant(self):
        from joinmind import initiate_session, add_reasoning
        state = initiate_session("Test?", ["gamma"],
                                 "gamma", session_dir=self.tmpdir)
        add_reasoning(state["id"], "gamma", "first thought", session_dir=self.tmpdir)
        with self.assertRaises(ValueError):
            add_reasoning(state["id"], "gamma", "second thought", session_dir=self.tmpdir)

    def test_think_rejects_non_participant(self):
        from joinmind import initiate_session, add_reasoning
        state = initiate_session("Test?", ["gamma"],
                                 "gamma", session_dir=self.tmpdir)
        with self.assertRaises(ValueError):
            add_reasoning(state["id"], "nuance", "outsider thought", session_dir=self.tmpdir)

    def test_auto_synthesise_on_last_contribution(self):
        from joinmind import initiate_session, add_reasoning
        state = initiate_session("Test?", ["beta", "gamma"],
                                 "gamma", session_dir=self.tmpdir)
        add_reasoning(state["id"], "gamma", "Gamma thinks X", session_dir=self.tmpdir)
        final = add_reasoning(state["id"], "beta", "Beta thinks Y", session_dir=self.tmpdir)
        self.assertEqual(final["status"], "synthesised")
        self.assertIsNotNone(final["synthesis"])
        self.assertIn("Gamma thinks X", final["synthesis"])
        self.assertIn("Beta thinks Y", final["synthesis"])

    def test_dissolve(self):
        from joinmind import initiate_session, dissolve_session
        state = initiate_session("Test?", ["gamma"],
                                 "gamma", session_dir=self.tmpdir)
        final = dissolve_session(state["id"], session_dir=self.tmpdir)
        self.assertEqual(final["status"], "dissolved")


if __name__ == "__main__":
    unittest.main()
