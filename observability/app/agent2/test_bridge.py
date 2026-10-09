"""Verify dual capture without calling a model or exporting remotely."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        output = Path(cls.temp.name) / 'capture'
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name('eligibility_agent.py')),
             '--mode', 'fixture', '--output', str(output)],
            capture_output=True, text=True, timeout=40)
        if result.returncode:
            raise AssertionError(result.stderr + result.stdout)
        cls.native = json.loads((output / 'native.json').read_text())
        cls.otel = json.loads((output / 'otel.json').read_text())
        cls.report = json.loads((output / 'run.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_each_native_span_has_one_converted_counterpart(self):
        self.assertEqual(len(self.otel), len(self.native['spans']) + 1)
        for native in self.native['spans']:
            matches = [s for s in self.otel if
                       s['attributes'].get('app.source.native.span_id') == native['id']]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]['attributes']['app.source.native.trace_id'],
                             native['trace_id'])
            self.assertNotEqual(matches[0]['context']['span_id'], native['id'])

    def test_valid_otel_tree_and_separate_trace_container(self):
        ids = {s['context']['span_id'] for s in self.otel}
        roots = [s for s in self.otel if
                 s['attributes']['app.source.native.record_type'] == 'trace']
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]['attributes']['app.source.native.trace_id'],
                         self.native['traces'][0]['id'])
        for span in self.otel:
            self.assertTrue(span['parent_id'] is None or span['parent_id'] in ids)
            self.assertEqual(span['attributes']['app.run.id'], self.report['run_id'])
            self.assertEqual(span['instrumentation_scope']['name'],
                             'openinference.instrumentation.openai_agents')

    def test_native_evidence_preserved_and_capture_is_local(self):
        verification = next(s for s in self.native['spans'] if
                            s['span_data'].get('name') == 'independent_eligibility_verification')
        self.assertTrue(verification['span_data']['data']['verified'])
        self.assertEqual(self.native['capture_errors'], [])
        self.assertEqual(self.native['active_spans'], [])
        self.assertEqual(self.report['otel_export_errors'], [])
        self.assertFalse(self.report['remote_trace_export_enabled'])
        self.assertEqual(self.report['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
