"""Financial observations preserve records without inventing goals or balances."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('treasury', ROOT / 'tools/treasury.py')
    module = importlib.util.module_from_spec(spec)
    with patch.object(Path, 'mkdir'):
        spec.loader.exec_module(module)
    return module


def capture(callback):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        callback()
    return re.sub(r'\x1b\[[0-9;]*m', '', stream.getvalue())


class TreasuryObservations(unittest.TestCase):
    def test_scenario_holds_recorded_averages_and_never_writes_ledger(self):
        tool = load()
        records = [
            {'date': '2026-01-01', 'type': 'revenue', 'amount': 1000, 'currency': 'GBP'},
            {'date': '2026-01-02', 'type': 'cost', 'amount': 100, 'currency': 'GBP'},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            before = json.dumps(records).encode()
            path.write_bytes(before)
            with patch.object(tool, 'LEDGER_FILE', path):
                output = capture(lambda: tool.cmd_forecast(['3']))
            self.assertEqual(path.read_bytes(), before)
        rows = [line for line in output.splitlines() if re.match(r'\s+\d{4}-\d{2}\s', line)]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.split()[1:4], ['£1,000.00', '£100.00', '£900.00'])
        self.assertIn('conditional illustration', output)

    def test_missing_capital_is_unknown_and_observed_surplus_has_no_target(self):
        tool = load()
        for amount, kind in [(100, 'cost'), (100, 'revenue')]:
            with self.subTest(kind=kind), patch.object(tool, 'load_ledger', return_value=[
                {'date': '2026-01-01', 'type': kind, 'amount': amount, 'currency': 'GBP'}
            ]), patch.object(tool, 'read_json', return_value={}):
                output = capture(tool.cmd_runway)
            self.assertIn('unknown', output)
            self.assertNotIn('£1,000.00', output)
            self.assertNotIn('30-day target', output)
            self.assertNotIn('self-sustaining', output)
            self.assertNotIn('estimated depletion:', output)

    def test_explicit_capital_record_supports_conditional_runway(self):
        tool = load()
        with patch.object(tool, 'load_ledger', return_value=[
            {'date': '2026-01-01', 'type': 'cost', 'amount': 100, 'currency': 'GBP'}
        ]), patch.object(tool, 'read_json', return_value={'capital': {'budget_card': '£1.5K available'}}):
            output = capture(tool.cmd_runway)
        self.assertIn('£1,500.00', output)
        self.assertIn('15.0 months', output)
        self.assertIn('Conditional on these records', output)

    def test_malformed_capital_is_not_interpreted_as_a_larger_balance(self):
        tool = load()
        with patch.object(tool, 'load_ledger', return_value=[
            {'date': '2026-01-01', 'type': 'cost', 'amount': 100, 'currency': 'GBP'}
        ]), patch.object(tool, 'read_json', return_value={'capital': {'budget_card': '£1,5K available'}}):
            output = capture(tool.cmd_runway)
        self.assertIn('Runway unknown', output)
        self.assertNotIn('£15,000', output)

    def test_project_status_does_not_create_a_sales_instruction(self):
        tool = load()
        projects = {name: {'name': name, 'status': name} for name in ['emerging', 'beta-live', 'building', 'paused']}
        with patch.object(tool, 'load_engines', return_value=projects), patch.object(tool, 'load_ledger', return_value=[]):
            output = capture(tool.cmd_engines)
        for name in projects:
            self.assertIn(name, output)
        for phrase in ['first clients', 'monetization pending', 'Revenue potential', 'resume when']:
            self.assertNotIn(phrase, output)


if __name__ == '__main__':
    unittest.main()
