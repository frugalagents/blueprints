"""Test trusted refund outcomes and rollback independently of LLM behaviour."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from refund_agent import POLICY_VERSION, Decision, RefundWorkflow


class RefundContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def workflow(self, scenario="eligible"):
        return RefundWorkflow(Path(self.temp.name) / "sandbox.sqlite", "test-task", scenario)

    def test_refund_and_repeat_preserve_one_transaction(self):
        workflow = self.workflow()
        workflow.assess()
        first = workflow.commit()
        second = workflow.commit()
        self.assertEqual(first["transaction_id"], second["transaction_id"])
        self.assertEqual(workflow.verify()["refund_rows"], 1)
        self.assertTrue(workflow.verify()["verified"])

    def test_denial_is_successful_without_money_movement(self):
        workflow = self.workflow("denied")
        workflow.assess()
        workflow.deny()
        result = workflow.verify()
        self.assertTrue(result["verified"])
        self.assertEqual(result["refund_rows"], 0)

    def test_missing_decision_cannot_commit(self):
        workflow = self.workflow()
        with self.assertRaises(PermissionError):
            workflow.commit()

    def test_denied_decision_cannot_commit(self):
        workflow = self.workflow("denied")
        workflow.assess()
        with self.assertRaises(PermissionError):
            workflow.commit()

    def test_stale_policy_evidence_cannot_commit(self):
        workflow = self.workflow()
        workflow.decision = Decision("d", "order-demo-1", True, "stale-policy")
        with self.assertRaises(PermissionError):
            workflow.commit()

    def test_independent_policy_check_rejects_wrong_eligible_decision(self):
        workflow = self.workflow("denied")
        workflow.decision = Decision("d", "order-demo-1", True, POLICY_VERSION)
        with self.assertRaises(PermissionError):
            workflow.commit()

    def test_database_error_rolls_back_and_has_no_success_record(self):
        workflow = self.workflow("db_error")
        workflow.assess()
        with self.assertRaises(sqlite3.OperationalError):
            workflow.commit()
        result = workflow.verify()
        self.assertTrue(result["verified"])
        self.assertEqual(result["refund_rows"], 0)
        self.assertIsNone(result["evidence_ref"])


if __name__ == "__main__":
    unittest.main()
