"""Business and native trace checks, independent of model nondeterminism."""
import json
import sqlite3
import eligibility_agent
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from eligibility_agent import EligibilityWorkflow, POLICY_VERSION

ROOT = Path(__file__).resolve().parent


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def workflow(self, scenario='eligible'):
        workflow = EligibilityWorkflow(Path(self.temp.name)/'sandbox.sqlite', scenario, 'task-test')
        workflow.setup()
        return workflow

    def test_requires_observed_order_and_policy(self):
        workflow = self.workflow()
        with self.assertRaises(PermissionError):
            workflow.submit(True, 'claim', POLICY_VERSION)
        self.assertIsNone(workflow.decision)

    def test_wrong_eligibility_cannot_be_accepted(self):
        workflow = self.workflow('denied')
        workflow.order(); workflow.policy()
        with self.assertRaises(PermissionError):
            workflow.submit(True, 'claim', POLICY_VERSION)
        self.assertIsNone(workflow.decision)

    def test_wrong_policy_version_cannot_be_accepted(self):
        workflow = self.workflow()
        workflow.order(); workflow.policy()
        with self.assertRaises(PermissionError):
            workflow.submit(True, 'claim', 'stale')

    def test_empty_reason_cannot_be_accepted(self):
        workflow = self.workflow()
        workflow.order(); workflow.policy()
        with self.assertRaises(PermissionError):
            workflow.submit(True, ' ', POLICY_VERSION)

    def test_denial_is_verified_without_refund_execution(self):
        workflow = self.workflow('denied')
        workflow.order(); workflow.policy()
        workflow.submit(False, '45 days exceeds 30-day window', POLICY_VERSION)
        self.assertEqual(workflow.verify()['actual'], 'denied')
        self.assertTrue(workflow.verify()['verified'])

    def test_verification_rechecks_database_after_submission(self):
        workflow = self.workflow()
        workflow.order(); workflow.policy()
        workflow.submit(True, '10 days within 30 days', POLICY_VERSION)
        with eligibility_agent.database_connection(workflow.database) as connection:
            connection.execute('UPDATE orders SET age_days=45')
        self.assertFalse(workflow.verify()['verified'])

    def test_sdk_fixture_emits_only_genuine_native_custom_spans(self):
        output = Path(self.temp.name)/'native-fixture'
        subprocess.run([sys.executable, str(ROOT/'eligibility_agent.py'), '--mode','fixture',
                        '--output',str(output)], check=True, capture_output=True, text=True)
        native = json.loads((output/'native.json').read_text())
        run = json.loads((output/'run.json').read_text())
        self.assertTrue(run['outcome']['verified'])
        self.assertEqual(run['model_calls'], 0)
        self.assertEqual(run['tool_calls'], [])
        self.assertFalse(native['active_spans'])
        self.assertFalse(native['capture_errors'])
        self.assertEqual({s['span_data']['type'] for s in native['spans']}, {'custom'})
        self.assertTrue(all(s['object']=='trace.span' and s['id'].startswith('span_')
                            for s in native['spans']))
        self.assertEqual(native['lifecycle'][-1]['event'], 'trace_end')

    def test_live_saved_records_are_complete_and_keep_native_error(self):
        for case in ('eligible-v1','denied-v1','tool-error-v1'):
            native=json.loads((ROOT.parents[1]/'data'/'captures'/'agent2'/case/'native.json').read_text())
            run=json.loads((ROOT.parents[1]/'data'/'captures'/'agent2'/case/'run.json').read_text())
            ids={s['id'] for s in native['spans']}
            self.assertEqual(len(ids),len(native['spans']))
            self.assertTrue(all(s['parent_id'] is None or s['parent_id'] in ids for s in native['spans']))
            self.assertFalse(native['active_spans'])
            self.assertTrue(run['outcome']['verified'])
            self.assertEqual(len([s for s in native['spans'] if s['span_data']['type']=='function']),len(run['tool_calls']))
            self.assertFalse(any('context' in s or 'attributes' in s for s in native['spans']))
            if case=='tool-error-v1':
                self.assertIsNone(run['decision'])
                self.assertTrue(any(s['error'] and s['span_data'].get('name')=='get_refund_policy'
                                    for s in native['spans']))


if __name__=='__main__': unittest.main()
