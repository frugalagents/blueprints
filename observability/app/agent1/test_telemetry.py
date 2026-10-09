"""Checks of the source contract and observed mapping, without model charges."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server import Runs
from telemetry_contract import source, trajectory, tool_invocations

ROOT = Path(__file__).resolve().parent


class TelemetryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cases = {}
        for case in ("eligible", "denied", "db_error"):
            output = Path(cls.temp.name) / case
            subprocess.run([
                sys.executable, str(ROOT / "refund_agent.py"), "--mode", "fixture",
                "--scenario", case, "--session-id", "session-shared",
                "--output", str(output),
            ], check=True, capture_output=True, text=True)
            cls.cases[case] = (
                json.loads((output / "run.json").read_text()),
                json.loads((output / "spans.json").read_text()),
                json.loads((output / "capture.json").read_text()),
            )

    def test_scope_resource_and_source_preserved(self):
        for run, spans, capture in self.cases.values():
            self.assertEqual(capture["contract_version"], run["contract_version"])
            for span in spans:
                self.assertTrue(span["instrumentation_scope"]["name"])
                self.assertIn("resource_schema_url", span)
                self.assertIn("resource", span)
                self.assertIn(source(span), ("sql", "manual"))
                self.assertEqual(span["attributes"]["app.run.id"], run["run_id"])
                self.assertEqual(span["attributes"]["app.attempt.id"], run["attempt_id"])

    def test_shared_session_distinct_tasks_runs_attempts(self):
        for field in ("task_id", "run_id", "attempt_id"):
            self.assertEqual(len({case[0][field] for case in self.cases.values()}), 3)
        self.assertEqual({case[0]["session_id"] for case in self.cases.values()}, {"session-shared"})

    def test_business_failure_is_separate_from_verification(self):
        run, spans, _ = self.cases["db_error"]
        self.assertEqual(run["business_outcome"], "failed")
        self.assertEqual(run["verification_status"], "passed")
        self.assertTrue(any(s["status"]["status_code"] == "ERROR" for s in spans))
        self.assertFalse(any(e["name"] == "refund_committed" for s in spans for e in s["events"]))

    def test_trajectory_links_to_original_evidence(self):
        for run, spans, _ in self.cases.values():
            steps = trajectory(spans)
            self.assertEqual(steps, run["trajectory"])
            self.assertTrue(steps)
            ids = {s["context"]["span_id"] for s in spans}
            self.assertTrue(all(step["span_id"] in ids for step in steps))
            self.assertEqual([step["start_time"] for step in steps],
                             sorted(step["start_time"] for step in steps))

    def test_agent_calls_exclude_internal_helpers(self):
        spans = json.loads((ROOT.parents[1] / "data" / "captures" / "agent1" / "eligible_final" / "spans.json").read_text())
        calls = tool_invocations(spans)
        self.assertEqual([call["name"] for call in calls],
                         ["lookup_order", "assess_eligibility_fixture", "execute_refund"])
        self.assertTrue(all(call["call_id"] for call in calls))
        run, _, _ = self.cases["eligible"]
        self.assertEqual(run["tool_calls"], [])
        self.assertEqual(len(run["internal_operation_calls"]), 5)

    def test_missing_scope_is_unknown_not_guessed(self):
        self.assertEqual(source({"attributes": {"db.system": "sqlite"}}), "unknown")

    def test_session_validation_and_concurrency(self):
        runs = Runs()
        with self.assertRaises(ValueError):
            runs.start("eligible", "../invalid")
        with patch("server.threading.Thread.start"):
            key = runs.start("eligible", "session-shared")
            self.assertEqual(runs.snapshot(key)["job"]["session_id"], "session-shared")
            with self.assertRaises(RuntimeError):
                runs.start("denied", "session-shared")


if __name__ == "__main__":
    unittest.main()
