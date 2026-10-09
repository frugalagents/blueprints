"""Checkpoint 1: actual Strands tracing, domain spans, and context extensions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from dataclasses import dataclass
from pathlib import Path
from contextlib import closing, contextmanager

import boto3
from botocore.config import Config
from opentelemetry import baggage, context, propagate, trace
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from strands import Agent, tool
from strands.hooks import BeforeModelCallEvent
from strands.models import BedrockModel
from telemetry_contract import CONTRACT_VERSION, tool_invocations, trajectory
from importlib.metadata import version

REGION = "us-east-1"
PROFILE = "us.anthropic.claude-sonnet-5-5"
POLICY_VERSION = "refund-policy-demo-v1"
NAMESPACE = uuid.UUID("96303198-71df-4e1b-bf67-b8de485d7d53")


@contextmanager
def database_connection(path):
    """Commit/rollback the transaction, then close the actual connection."""
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


class JsonExporter(SpanExporter):
    """Export actual completed OTel spans, without manufacturing any spans."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.records = []

    def export(self, spans):
        with self.lock:
            for span in spans:
                record = json.loads(span.to_json())
                scope = span.instrumentation_scope
                record["instrumentation_scope"] = {
                    "name": scope.name,
                    "version": scope.version,
                    "schema_url": scope.schema_url,
                } if scope else None
                record["resource_schema_url"] = span.resource.schema_url
                self.records.append(record)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.records, indent=2))
            temporary.replace(self.path)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


class ContextExtensions(SpanProcessor):
    """Copy allowlisted baggage to spans at creation time."""

    FIELDS = (
        "app.task.id",
        "app.session.id",
        "app.attempt.id",
        "app.run.id",
        "app.scenario",
        "app.model.mode",
        "app.workflow.id",
    )

    def on_start(self, span, parent_context=None):
        for key in self.FIELDS:
            value = baggage.get_baggage(key, context=parent_context)
            if value is not None:
                span.set_attribute(key, value)
        span.set_attribute("app.policy.version", POLICY_VERSION)
        span.set_attribute("app.agent.version", "checkpoint-1")

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


class CallBudget:
    """A per-run cap prevents a model/tool loop from running indefinitely."""

    def __init__(self, maximum=6):
        self.maximum = maximum
        self.calls = 0

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeModelCallEvent, self.before_call)

    def before_call(self, event):
        self.calls += 1
        if self.calls > self.maximum:
            event.cancel = "Checkpoint model-call budget exceeded"


@dataclass(frozen=True)
class Decision:
    decision_id: str
    order_id: str
    eligible: bool
    policy_version: str
    source: str = "eligibility_fixture_not_agent_2"


class RefundWorkflow:
    """Trusted business logic shared by the agent tools and deterministic tests."""

    def __init__(self, database: Path, task_id: str, scenario: str,
                 endpoint=None, correlation=None, output=None, model_mode="bedrock"):
        self.database = database
        self.task_id = task_id
        self.scenario = scenario
        self.tracer = trace.get_tracer("refund.business", "checkpoint-1")
        self.decision = None
        self.commit_calls = 0
        self.tool_calls = []
        self.denial_recorded = False
        self.db_errors = 0
        self.endpoint = endpoint
        self.correlation = correlation or {}
        self.output = output
        self.model_mode = model_mode
        self.handoff_failed = False
        self.handoff_attempted = False
        self.handoff_receipt = None
        # DB instrumentation is installed only after fixture setup.
        with database_connection(database) as conn:
            conn.executescript(
                "CREATE TABLE orders "
                "(order_id TEXT PRIMARY KEY, age_days INTEGER, amount_cents INTEGER);"
                "CREATE TABLE refunds "
                "(transaction_id TEXT PRIMARY KEY, order_id TEXT UNIQUE,"
                "task_id TEXT, decision_id TEXT, amount_cents INTEGER);"
                "CREATE TABLE outcomes "
                "(task_id TEXT PRIMARY KEY, outcome TEXT, evidence_ref TEXT);"
            )
            conn.cursor().execute(
                "INSERT INTO orders VALUES (?, ?, ?)",
                ("order-demo-1", 45 if scenario == "denied" else 10, 2500),
            )

    def order(self):
        self.tool_calls.append("lookup_order")
        with database_connection(self.database) as conn:
            row = conn.cursor().execute(
                "SELECT order_id, age_days, amount_cents FROM orders WHERE order_id=?",
                ("order-demo-1",),
            ).fetchone()
        return dict(zip(("order_id", "age_days", "amount_cents"), row))

    def assess(self):
        if self.endpoint:
            return self.delegate()
        self.tool_calls.append("assess_eligibility_fixture")
        with self.tracer.start_as_current_span("app.eligibility.fixture") as span:
            span.set_attribute("app.instrumentation.capture", "manual")
            span.set_attribute("app.evidence.source", "fixture_not_agent_2")
            order = self.order()
            # Explicit fixed fixture policy, not an LLM-defined policy.
            self.decision = Decision(
                decision_id=f"decision-{self.task_id}",
                order_id=order["order_id"],
                eligible=order["age_days"] <= 30,
                policy_version=POLICY_VERSION,
            )
            span.set_attribute("app.decision.id", self.decision.decision_id)
            span.add_event(
                "eligibility_decided",
                {
                    "app.eligible": self.decision.eligible,
                    "app.evidence.source": self.decision.source,
                },
            )
            return self.decision.__dict__

    def delegate(self):
        """A real HTTP delegation. Accept evidence only after correlation checks."""
        self.tool_calls.append("request_agent2_eligibility")
        if self.handoff_attempted:
            if self.decision:
                return self.decision.__dict__
            raise PermissionError("Previous delegation failed; no automatic retry")
        self.handoff_attempted = True
        attrs = {**self.correlation, "handoff_id": "handoff-" + uuid.uuid4().hex[:16],
                 "handoff_attempt_id": "ha-" + uuid.uuid4().hex[:16]}
        request_body = {"contract_version": "agent-handoff-v1", **attrs,
                        "order": self.order(), "scenario": self.scenario,
                        "model_mode": self.model_mode}
        record = {"request": request_body, "response": None}
        with self.tracer.start_as_current_span("agent2.eligibility.call",
                kind=trace.SpanKind.CLIENT,
                attributes={"app." + k.replace("_", "."): v for k, v in attrs.items()}) as span:
            headers = {"Content-Type": "application/json",
                       "X-Agent-Token": os.environ.get("AGENT2_HANDOFF_TOKEN", "")}
            TraceContextTextMapPropagator().inject(headers)
            record["trace_headers"] = {k: v for k, v in headers.items()
                                       if k in ("traceparent", "tracestate")}
            span.set_attribute("http.request.method", "POST")
            span.set_attribute("server.address", "127.0.0.1")
            try:
                request = Request(self.endpoint, data=json.dumps(request_body).encode(),
                                  headers=headers, method="POST")
                try:
                    with urlopen(request, timeout=130) as response:
                        record["http_status"] = response.status
                        body = json.load(response)
                except HTTPError as error:
                    record["http_status"] = error.code
                    record["response"] = json.load(error)
                    span.set_attribute("http.response.status_code", error.code)
                    raise RuntimeError("Agent B failed: HTTP " + str(error.code)) from error
                record["response"] = body
                span.set_attribute("http.response.status_code", record["http_status"])
                self.accept_agent2(body, attrs, request_body["order"])
                span.set_attribute("app.peer.run.id", body["run_id"])
                self.handoff_receipt = body
                return self.decision.__dict__
            except Exception as error:
                self.handoff_failed = True
                record["error"] = {"type": type(error).__name__, "message": str(error)}
                span.set_status(trace.StatusCode.ERROR, type(error).__name__)
                raise
            finally:
                if self.output:
                    (self.output / "handoff.json").write_text(json.dumps(record, indent=2))

    def accept_agent2(self, body, expected_context, order):
        with self.tracer.start_as_current_span("app.decision.validate.agent2") as span:
            if not isinstance(body, dict):
                raise PermissionError("Agent B response must be an object")
            for key in ("workflow_id", "handoff_id", "handoff_attempt_id", "session_id"):
                if body.get(key) != expected_context[key]:
                    raise PermissionError("Agent B correlation mismatch: " + key)
            decision = body.get("decision")
            if (body.get("agent_id") != "refund-eligibility-native"
                or body.get("verification_status") != "passed"
                or not isinstance(decision, dict)
                or type(decision.get("eligible")) is not bool
                or decision.get("order_id") != order["order_id"]
                or decision.get("policy_version") != POLICY_VERSION
                or decision["eligible"] != (order["age_days"] <= 30)
                or not isinstance(decision.get("decision_id"), str)
                or not decision["decision_id"]
                or not isinstance(body.get("run_id"), str)
                or not body["run_id"]
                or not isinstance(decision.get("reason"), str)
                or not decision["reason"].strip()
                or body.get("business_outcome") != ("eligible" if decision["eligible"] else "denied")):
                raise PermissionError("Invalid or unverified Agent B evidence")
            self.decision = Decision(decision["decision_id"], decision["order_id"],
                                     decision["eligible"], decision["policy_version"],
                                     source="agent_2_http_verified")
            span.set_attribute("app.decision.id", self.decision.decision_id)
            span.set_attribute("app.evidence.source", self.decision.source)

    def commit(self):
        self.tool_calls.append("execute_refund")
        self.commit_calls += 1
        with self.tracer.start_as_current_span("app.refund.commit") as span:
            span.set_attribute("app.instrumentation.capture", "manual")
            with self.tracer.start_as_current_span("app.decision.validate") as validation:
                validation.set_attribute("app.instrumentation.capture", "manual")
                if (
                    self.decision is None
                    or not self.decision.eligible
                    or self.decision.policy_version != POLICY_VERSION
                    or self.decision.order_id != "order-demo-1"
                ):
                    raise PermissionError("Missing, denied or invalid eligibility evidence")
                span.set_attribute("app.decision.id", self.decision.decision_id)
                order = self.order()
                if order["age_days"] > 30:
                    raise PermissionError("Independent refund-tool policy check denied action")
                validation.add_event("approval_validated")
            try:
                with database_connection(self.database) as conn:
                    transaction = f"txn-{uuid.uuid5(NAMESPACE, order['order_id'])}"
                    conn.cursor().execute(
                        "INSERT OR IGNORE INTO refunds VALUES (?, ?, ?, ?, ?)",
                        (
                            transaction,
                            order["order_id"],
                            self.task_id,
                            self.decision.decision_id,
                            order["amount_cents"],
                        ),
                    )
                    if self.scenario == "db_error":
                        conn.cursor().execute("INSERT INTO checkpoint_injected_missing_table VALUES (1)")
                    # Read the persisted row, rather than claim a new transaction.
                    row = conn.cursor().execute(
                        "SELECT transaction_id FROM refunds WHERE order_id=?",
                        (order["order_id"],),
                    ).fetchone()
                    conn.cursor().execute(
                        "INSERT OR REPLACE INTO outcomes VALUES (?, ?, ?)",
                        (self.task_id, "refunded", row[0]),
                    )
                # Only after the database transaction commits.
                span.set_attribute("app.transaction.id", row[0])
                span.add_event("refund_committed", {"app.transaction.id": row[0]})
                return {"outcome": "refunded", "transaction_id": row[0]}
            except sqlite3.OperationalError:
                self.db_errors += 1
                span.add_event("refund_rolled_back")
                raise

    def deny(self):
        self.tool_calls.append("record_denial")
        with self.tracer.start_as_current_span("app.refund.deny") as span:
            span.set_attribute("app.instrumentation.capture", "manual")
            if self.decision is None or self.decision.eligible:
                raise PermissionError("No trusted denied decision exists")
            with database_connection(self.database) as conn:
                conn.cursor().execute(
                    "INSERT OR REPLACE INTO outcomes VALUES (?, ?, ?)",
                    (self.task_id, "denied", self.decision.decision_id),
                )
            self.denial_recorded = True
            span.add_event("refund_denied", {"app.decision.id": self.decision.decision_id})
            return {"outcome": "denied", "decision_id": self.decision.decision_id}

    def verify(self):
        # Independent evidence check. A model response is not the outcome record.
        with self.tracer.start_as_current_span("app.outcome.verify") as span:
            span.set_attribute("app.instrumentation.capture", "manual")
            with database_connection(self.database) as conn:
                count = conn.cursor().execute("SELECT COUNT(*) FROM refunds").fetchone()[0]
                outcome_row = conn.cursor().execute(
                    "SELECT outcome,evidence_ref FROM outcomes WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()
            expected = {"eligible": "refunded", "denied": "denied", "db_error": "failed",
                        "tool_error": "failed", "timeout": "failed"}[
                self.scenario
            ]
            if self.scenario in ("tool_error", "timeout"):
                verified = (count == 0 and outcome_row is None and self.decision is None
                            and self.handoff_failed and self.commit_calls == 0)
                actual = "failed" if verified else "incomplete"
            elif self.scenario == "db_error":
                verified = count == 0 and self.commit_calls > 0 and self.db_errors > 0
                actual = "failed" if verified else "incomplete"
            else:
                actual = outcome_row[0] if outcome_row else "incomplete"
                verified = actual == expected and count == int(expected == "refunded")
            span.set_attribute("app.outcome", actual)
            span.set_attribute("app.outcome.verified", verified)
            span.add_event("business_outcome_checked")
            return {
                "expected": expected,
                "actual": actual,
                "verified": verified,
                "refund_rows": count,
                "evidence_ref": outcome_row[1] if outcome_row else None,
                "commit_calls": self.commit_calls,
                "db_errors": self.db_errors,
            }

    def tools(self):
        workflow = self

        @tool
        def lookup_order() -> dict:
            """Read the synthetic order associated with this request."""
            return workflow.order()

        @tool
        def assess_eligibility_fixture() -> dict:
            """Get the trusted eligibility fixture. This is not Agent 2 or A2A."""
            return workflow.assess()

        @tool
        def request_agent2_eligibility() -> dict:
            """Delegate eligibility to Agent B over HTTP and validate returned evidence."""
            return workflow.assess()

        @tool
        def execute_refund() -> dict:
            """Commit a sandbox refund only after a trusted eligible decision."""
            return workflow.commit()

        @tool
        def record_denial() -> dict:
            """Record a trusted denied decision without executing a refund."""
            return workflow.deny()

        return [lookup_order, request_agent2_eligibility if self.endpoint else
                assess_eligibility_fixture, execute_refund, record_denial]


def setup_telemetry(path):
    exporter = JsonExporter(path)
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "refund-coordinator-strands",
                "service.version": "checkpoint-1",
                "deployment.environment.name": "local-sandbox",
            }
        )
    )
    provider.add_span_processor(ContextExtensions())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
    from transport import configure_otlp
    configure_otlp(provider, path.parent)
    trace.set_tracer_provider(provider)
    SQLite3Instrumentor().instrument(tracer_provider=provider)
    return provider, exporter


def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = args.task_id or f"refund-{uuid.uuid4().hex[:12]}"
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:16]}"
    session_id = args.session_id or f"session-{uuid.uuid4().hex[:16]}"
    attempt_id = args.attempt_id or f"attempt-{uuid.uuid4().hex[:16]}"
    # Fixture database setup is outside the observed business task.
    endpoint = getattr(args, "agent2_endpoint", None)
    workflow_id = getattr(args, "workflow_id", None) or ("workflow-" + uuid.uuid4().hex[:16])
    correlation = {"session_id": session_id, "workflow_id": workflow_id,
                   "caller_run_id": run_id, "caller_attempt_id": attempt_id}
    workflow = RefundWorkflow(output / "sandbox.sqlite", task, args.scenario,
                              endpoint, correlation, output, args.mode)
    # Keep framework convention choice explicit and versioned.
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
    provider, exporter = setup_telemetry(output / "spans.json")
    ctx = context.get_current()
    for key, value in {
        "app.task.id": task,
        "app.session.id": session_id,
        "app.run.id": run_id,
        "app.attempt.id": attempt_id,
        "app.scenario": args.scenario,
        "app.model.mode": args.mode,
        **({"app.workflow.id": workflow_id} if endpoint else {}),
    }.items():
        ctx = baggage.set_baggage(key, value, context=ctx)
    token = context.attach(ctx)
    result = None
    agent_error = None
    budget = CallBudget()
    try:
        tracer = trace.get_tracer("refund.business", "checkpoint-1")
        with tracer.start_as_current_span("app.refund.request") as root:
            root.set_attribute("app.instrumentation.capture", "manual")
            root.set_attribute("app.model.profile", PROFILE)
            root.set_attribute("app.aws.region", REGION)
            # Actual serialised W3C context sample for the future A2A boundary.
            carrier = {}
            propagate.inject(carrier)
            (output / "propagation.json").write_text(json.dumps(carrier, indent=2))
            if args.mode == "bedrock":
                session = boto3.Session(region_name=REGION)
                model = BedrockModel(
                    boto_session=session,
                    model_id=PROFILE,
                    streaming=False,
                    max_tokens=2048,
                    additional_request_fields={"thinking": {"type": "between_tools"}},
                    boto_client_config=Config(
                        retries={"total_max_attempts": 2, "mode": "standard"},
                        connect_timeout=10,
                        read_timeout=90,
                    ),
                )
                agent = Agent(
                    model=model,
                    agent_id="refund-coordinator",
                    name="Refund coordinator",
                    tools=workflow.tools(),
                    callback_handler=None,
                    hooks=[budget],
                    system_prompt=(
                        "You coordinate a synthetic sandbox refund. "
                        "First call lookup_order, then "
                        + ("request_agent2_eligibility. " if endpoint else "assess_eligibility_fixture. ")
                        +
                        "For an eligible decision call execute_refund. For a denied decision "
                        "call record_denial; never call execute_refund on denial. "
                        "If any tool fails, stop; do not retry or call another action tool. Report the failure. "
                        "Never invent successful actions. Summarise the observed tool result "
                        "in one short sentence. All amounts are synthetic."
                    ),
                )
                try:
                    result = str(agent("Please process the refund for the current order."))
                except Exception as error:
                    agent_error = {"type": type(error).__name__, "message": str(error)}
                    root.record_exception(error)
                    root.set_status(trace.StatusCode.ERROR, type(error).__name__)
            else:
                # Unit evidence only. No fake framework/model spans are emitted.
                try:
                    workflow.order()
                    decision = workflow.assess()
                    result = workflow.commit() if decision["eligible"] else workflow.deny()
                except Exception as error:
                    agent_error = {"type": type(error).__name__, "message": str(error)}
            evidence = workflow.verify()
            root.set_attribute("app.outcome", evidence["actual"])
            root.set_attribute("app.outcome.verified", evidence["verified"])
            # A denied task is not an execution error; an injected DB error is.
            if evidence["actual"] in ("failed", "incomplete"):
                root.set_status(trace.StatusCode.ERROR, evidence["actual"])
        provider.force_flush()
        report = {
            "task_id": task,
            "session_id": session_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "contract_version": CONTRACT_VERSION,
            "execution_status": "error" if agent_error else "completed",
            "business_outcome": evidence["actual"],
            "verification_status": "passed" if evidence["verified"] else "failed",
            "scenario": args.scenario,
            "model_mode": args.mode,
            "profile": PROFILE,
            "region": REGION,
            "agent_output": result,
            "agent_error": agent_error,
            "model_calls": budget.calls,
            "tool_calls": [call["name"] for call in tool_invocations(exporter.records)],
            "tool_invocations": tool_invocations(exporter.records),
            "internal_operation_calls": workflow.tool_calls,
            "trajectory": trajectory(exporter.records),
            "outcome": evidence,
            "span_count": len(exporter.records),
            "eligibility_source": "agent_2_http" if endpoint else "fixture_not_agent_2",
            "workflow_id": workflow_id if endpoint else None,
            "agent_handoff_implemented": bool(endpoint),
            "handoff_failed": workflow.handoff_failed,
            "a2a_implemented": False,
            "collector_used": bool(os.environ.get("OBSERVABILITY_OTLP_ENDPOINT")),
            "pipeline_enabled": bool(os.environ.get("OBSERVABILITY_OTLP_ENDPOINT")),
            "export_path": ("OTel SDK -> OTLP/HTTP -> Collector; local audit retained"
                            if os.environ.get("OBSERVABILITY_OTLP_ENDPOINT") else
                            "OTel SDK -> local JSON SpanExporter"),
        }
        capture = {
            "contract_version": CONTRACT_VERSION,
            "adapter": "otel-python-local-json-v1",
            "packages": {name: version(name) for name in (
                "strands-agents", "opentelemetry-sdk",
                "opentelemetry-instrumentation-sqlite3")},
            "semantic_convention_selection": "gen_ai_latest_experimental",
            "agent_id": "refund-coordinator",
            "model_vendor": "anthropic",
            "serving_platform": "amazon-bedrock",
            "content_policy": "synthetic-full-content-v1",
            "run_id": run_id,
        }
        (output / "capture.json").write_text(json.dumps(capture, indent=2))
        (output / "run.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 0 if evidence["verified"] and (args.scenario in ("db_error", "tool_error", "timeout") or not agent_error) else 1
    finally:
        context.detach(token)
        provider.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("eligible", "denied", "db_error", "tool_error", "timeout"), default="eligible")
    parser.add_argument("--agent2-endpoint")
    parser.add_argument("--workflow-id")
    parser.add_argument("--mode", choices=("bedrock", "fixture"), default="bedrock")
    parser.add_argument("--session-id")
    parser.add_argument("--task-id")
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--output", required=True, help="A new evidence directory, never overwritten.")
    args = parser.parse_args()
    if args.scenario in ("tool_error", "timeout") and not args.agent2_endpoint:
        parser.error("Agent B failure scenarios require --agent2-endpoint")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
