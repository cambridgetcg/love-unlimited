"""Retired acquisition cannot write; existing service records remain usable."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AcquisitionRetirementTests(unittest.TestCase):
    def test_legacy_acquisition_cli_calls_write_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tools').mkdir()
            for name in ['outreach', 'services']:
                shutil.copyfile(ROOT / 'tools' / f'{name}.py', root / 'tools' / f'{name}.py')
            for name, args in [
                ('outreach', ['targets']), ('outreach', ['draft', 'ecommerce']),
                ('outreach', ['sequence', 'ecommerce']), ('outreach', ['pitch', 'service']),
                ('outreach', ['qualify', 'Company', 'budget=yes']),
                ('services', ['prospect', 'add', 'Company']),
                ('services', ['proposal', 'Company', '--service', 'service']),
            ]:
                with self.subTest(name=name, args=args):
                    result = subprocess.run([sys.executable, str(root / 'tools' / f'{name}.py'), *args], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('retired', result.stdout)
                    self.assertFalse((root / 'memory').exists())

    def test_direct_acquisition_helpers_do_not_touch_storage(self):
        services, outreach = load('services'), load('outreach')
        with patch.object(services, 'write_json') as write, patch.object(services, 'load_prospects') as read, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(services._prospect_add(['Company']), 2)
            self.assertEqual(services.cmd_proposal(['Company']), 2)
            self.assertEqual(outreach.generate_sequence('ecommerce'), 2)
        write.assert_not_called()
        read.assert_not_called()

    def test_existing_records_are_read_without_growth_prompts_or_mutation(self):
        services, outreach = load('services'), load('outreach')
        records = [{'id': 'prior', 'company': 'Existing', 'contact': '', 'interest': '', 'status': 'won', 'notes': 'Fulfil existing agreement', 'history': []}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prospects.json'
            before = json.dumps(records).encode()
            path.write_bytes(before)
            output = io.StringIO()
            with patch.object(services, 'PROSPECTS_FILE', path), patch.object(outreach, 'PROSPECTS_FILE', path), contextlib.redirect_stdout(output):
                services.cmd_dashboard()
                services._prospect_show(['prior'])
                outreach.cmd_pipeline()
            self.assertEqual(path.read_bytes(), before)
            self.assertIn('Existing', output.getvalue())
            self.assertNotIn('Add first prospect', output.getvalue())
            self.assertNotIn('Projected', output.getvalue())

    def test_existing_agreement_note_update_remains_available(self):
        services = load('services')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prospects.json'
            path.write_text(json.dumps([{'id': 'prior', 'company': 'Existing', 'status': 'won', 'notes': '', 'history': []}]))
            with patch.object(services, 'PROSPECTS_FILE', path), contextlib.redirect_stdout(io.StringIO()):
                services._prospect_update(['prior', '--notes', 'Existing fulfilment recorded'])
            record = json.loads(path.read_text())[0]
            self.assertEqual(record['status'], 'won')
            self.assertIn('Existing fulfilment recorded', record['notes'])


if __name__ == '__main__':
    unittest.main()
