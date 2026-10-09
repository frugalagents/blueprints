"""Real loopback HTTP, separate agent processes, no model calls in tests."""
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer

from server import ConnectedRuns, Handler
from handoff_contract import correlation_result
from refund_agent import RefundWorkflow, POLICY_VERSION


class ConnectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = Handler.connected_runs
        manager = ConnectedRuns()
        manager.root = Path(cls.temp.name)
        (manager.root / 'evidence').mkdir()
        manager.mode = 'fixture'
        Handler.connected_runs = manager
        cls.manager = manager
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        manager.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        Handler.connected_runs = cls.previous
        cls.temp.cleanup()

    def test_real_http_correlation_and_fail_closed_scenarios(self):
        for scenario, expected in [('eligible', 'refunded'), ('denied', 'denied'),
                                   ('tool_error', 'failed'), ('timeout', 'failed'),
                                   ('db_error', 'failed')]:
            with self.subTest(scenario=scenario):
                key = self.manager.start(scenario, 'test-connected-session')
                deadline = time.monotonic() + 40
                while self.manager.active() and time.monotonic() < deadline:
                    time.sleep(0.05)
                snapshot = self.manager.snapshot(key)
                a = snapshot['agent_a']['run']
                if not a:
                    self.fail((self.manager.root / 'evidence' / (key + '.log')).read_text())
                self.assertEqual(snapshot['job']['status'], 'complete', snapshot)
                self.assertEqual(a['business_outcome'], expected)
                self.assertEqual(a['verification_status'], 'passed')
                self.assertTrue(snapshot['correlation']['verified'], snapshot['correlation'])
                b = snapshot['agent_b']['run']
                self.assertNotEqual(a['run_id'], b['run_id'])
                self.assertNotEqual(a['task_id'], b['task_id'])
                self.assertEqual(a['session_id'], b['session_id'])
                self.assertFalse(a['a2a_implemented'])
                self.assertEqual(a['outcome']['refund_rows'], int(expected == 'refunded'))
                if scenario in ('tool_error', 'timeout'):
                    self.assertIsNone(b['decision'])
                    self.assertEqual(a['outcome']['commit_calls'], 0)
                if scenario == 'timeout':
                    self.assertEqual(snapshot['handoff']['http_status'], 504)
                verification = next(s for s in snapshot['agent_b']['spans']
                                    if s['name'] == 'independent_eligibility_verification')
                self.assertTrue(verification['attributes']['app.verification.verified'])

    def test_mismatched_handoff_cannot_authorize_refund(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = RefundWorkflow(Path(directory) / 'db.sqlite', 'task-1', 'eligible')
            expected = {'workflow_id': 'w', 'handoff_id': 'h',
                        'handoff_attempt_id': 'a', 'session_id': 's'}
            body = {**expected, 'handoff_id': 'another', 'agent_id': 'refund-eligibility-native',
                    'verification_status': 'passed', 'business_outcome': 'eligible',
                    'run_id': 'b-run', 'decision': {
                        'eligible': True, 'order_id': 'order-demo-1',
                        'policy_version': POLICY_VERSION, 'reason': 'checked', 'decision_id': 'd'}}
            with self.assertRaises(PermissionError):
                workflow.accept_agent2(body, expected, workflow.order())
            self.assertIsNone(workflow.decision)
            with self.assertRaises(PermissionError):
                workflow.commit()
            self.assertEqual(workflow.verify()['refund_rows'], 0)

    def test_shared_ids_without_remote_parent_are_not_a_verified_link(self):
        attrs = {key: 'shared' for key in (
            'app.workflow.id', 'app.handoff.id', 'app.handoff.attempt.id', 'app.session.id')}
        a = [{'name': 'agent2.eligibility.call', 'attributes': attrs,
              'context': {'trace_id': 'trace', 'span_id': 'client'}}]
        b = [{'name': 'agent2.eligibility.receive', 'attributes': attrs,
              'context': {'trace_id': 'trace', 'span_id': 'server'}, 'parent_id': 'wrong'}]
        self.assertFalse(correlation_result(a, b)['verified'])

    def test_transport_timeout_without_response_cannot_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = RefundWorkflow(
                root / 'db.sqlite', 'timeout-task', 'timeout',
                endpoint='http://127.0.0.1:1/internal/eligibility', output=root,
                correlation={'workflow_id': 'w', 'session_id': 's',
                             'caller_run_id': 'a-run', 'caller_attempt_id': 'a-attempt'})
            with patch('refund_agent.urlopen', side_effect=TimeoutError('transport deadline')):
                with self.assertRaises(TimeoutError):
                    workflow.assess()
            self.assertIsNone(workflow.decision)
            evidence = workflow.verify()
            self.assertTrue(evidence['verified'])
            self.assertEqual(evidence['refund_rows'], 0)
            self.assertEqual(evidence['commit_calls'], 0)
            receipt = json.loads((root / 'handoff.json').read_text())
            self.assertIsNone(receipt['response'])
            self.assertEqual(receipt['error']['type'], 'TimeoutError')
            self.assertNotIn('X-Agent-Token', receipt['trace_headers'])


if __name__ == '__main__':
    unittest.main()
