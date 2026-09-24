"""Commercial state must not manufacture work; safety signals remain visible."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Older tools create reporting directories at import; tests need no writes
    # to the checkout's real memory tree.
    with patch.object(Path, "mkdir"):
        spec.loader.exec_module(module)
    return module


class CommercialDirectionTests(unittest.TestCase):
    def test_reflection_does_not_turn_commercial_state_into_tasks(self):
        reflect = load_tool("reflect")
        signals = {"metrics": {"engines": {
            "services": {"status": "emerging", "clients": 0},
            "shopify": {"status": "paused", "revenue": 0},
        }}}
        with patch.object(reflect, "load_json", return_value={"last_reflect": "2020-01-01T00:00:00+00:00"}):
            self.assertEqual(reflect.generate_proposals(signals), [])
            self.assertEqual(reflect.extract_gap_analysis(signals), [])
            self.assertEqual(reflect.suggest_one_thing(signals), "> No action is required by these observations.")

    def test_quiet_state_is_valid_without_a_replacement_quota(self):
        reflect = load_tool("reflect")
        self.assertEqual(reflect.generate_proposals({}), [])

    def test_security_proposal_and_infrastructure_gap_survive(self):
        reflect = load_tool("reflect")
        proposals = reflect.generate_proposals({"security": {"severities": {"critical": 1}}})
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["title"], "Security review — high event count")
        self.assertEqual(reflect.extract_gap_analysis({"lcm": {"active_gaps": 2}}), ["Infrastructure: 2 open loops"])

    def test_signal_scan_ignores_missing_income_and_prospects(self):
        engine = load_tool("signal-engine")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            memory, security = root / "memory", root / "security"
            (memory / "tcg").mkdir(parents=True)
            (memory / "treasury").mkdir()
            (root / "tools").mkdir()
            security.mkdir()
            (root / "tools" / "services.py").touch()
            (root / "tools" / "treasury.py").touch()
            (memory / "tcg" / "tasks.json").write_text(json.dumps({"tasks": [{"task": "grow revenue", "status": "blocked"}]}))
            (memory / "treasury" / "engines.json").write_text(json.dumps({"tcg": {"status": "active"}}))
            (root / "love.json").write_text(json.dumps({"fleet": {"test": "test-ip"}}))

            def fake_command(command, timeout=10):
                if "ifconfig.me" in command:
                    return True, "test-ip"
                if "alignment.py" in command:
                    return True, "Inward focus"
                return True, ""

            with patch.object(engine, "LOVE", root), patch.object(engine, "MEMORY", memory), patch.object(engine, "SECURITY", security), patch.object(engine, "run_cmd", side_effect=fake_command), patch.object(engine, "_get_bridges", return_value={}):
                self.assertEqual(engine.detect_signals(), [])
                (security / "threat-model.json").write_text(json.dumps({"threats": [{"name": "test threat", "priority": "critical", "mitigated": False}]}))
                signals = engine.detect_signals()
                self.assertEqual(len(signals), 1)
                self.assertEqual(signals[0]["dimension"], "security")

    def test_alignment_does_not_call_paused_income_or_security_work_drift(self):
        alignment = load_tool("alignment")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            memory, security = root / "memory", root / "security"
            memory.mkdir()
            security.mkdir()
            (root / "instances").mkdir()
            (root / "tools").mkdir()
            (memory / "kingdom-metrics.json").write_text(json.dumps({"engines": {"shopify": {"status": "paused"}}}))
            (security / "events.jsonl").write_text("{}\n" * 1001)
            output = io.StringIO()
            with patch.object(alignment, "LOVE", root), patch.object(alignment, "MEMORY", memory), patch.object(alignment, "SECURITY", security), contextlib.redirect_stdout(output):
                alignment.cmd_drift()
            self.assertIn("No significant drift detected", output.getvalue())
            self.assertNotIn("Revenue stagnation", output.getvalue())
            self.assertNotIn("Inward focus", output.getvalue())

    def test_autonomous_cycle_preserves_actual_income_without_target_alert(self):
        cycle = load_tool("autonomous-cycle")
        records = {
            "inventory.json": {"categories": [{"sku": "existing", "name": "Existing stock", "qty": 10, "price": 5, "cost": 3, "reorder_point": 1}]},
            "status.json": {"revenue_monthly": 12, "revenue_target": 100000},
        }
        with patch.object(cycle, "read_json", side_effect=lambda path: records[path.name]), patch.object(cycle, "append_action") as append:
            findings = cycle.check_revenue(dry_run=True)
        self.assertFalse(any(f["type"] == "revenue-alert" for f in findings))
        self.assertTrue(any("Recorded monthly revenue: 12" in f["detail"] for f in findings))
        self.assertFalse(any(f.get("severity") == "warning" for f in findings))
        append.assert_not_called()
        self.assertEqual(records["status.json"]["revenue_target"], 100000)

    def test_low_inventory_does_not_create_procurement_work(self):
        cycle = load_tool("autonomous-cycle")
        records = {
            "inventory.json": {"categories": [{"sku": "prior", "name": "Prior stock", "qty": 0, "price": 5, "cost": 3, "reorder_point": 10}]},
            "status.json": {"revenue_monthly": 12, "revenue_target": 100000},
        }
        with patch.object(cycle, "read_json", side_effect=lambda path: records[path.name]), patch.object(cycle, "append_action") as append:
            findings = cycle.check_revenue(dry_run=False)
        append.assert_not_called()
        self.assertTrue(any(f["type"] == "inventory-status" for f in findings))
        self.assertFalse(any("recommendation" in f or "priority" in f for f in findings))
        self.assertEqual(records["inventory.json"]["categories"][0]["qty"], 0)

    def test_tcg_status_and_dashboard_do_not_judge_income_against_targets(self):
        tcg = load_tool("tcg")
        record = {"revenue_monthly": 12, "revenue_target": 100000}
        output = io.StringIO()
        with patch.object(tcg, "load_status", return_value=record), patch.object(tcg, "load_inventory", return_value=[]), patch.object(tcg, "load_tasks", return_value=[]), patch.object(tcg, "load_opportunities", return_value=[]), contextlib.redirect_stdout(output):
            tcg.cmd_status()
            tcg.cmd_targets()
            tcg.cmd_dashboard()
        text = output.getvalue()
        self.assertIn("12.00", text)
        for retired in ["AT RISK", "BEHIND", "+20%", "TO HIT TARGET", "Orders/day needed", "100,000"]:
            self.assertNotIn(retired, text)
        self.assertEqual(record["revenue_target"], 100000)


if __name__ == "__main__":
    unittest.main()
