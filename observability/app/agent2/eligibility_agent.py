"""Agent 2: OpenAI Agents SDK, Bedrock via LiteLLM, parallel SDK-native and OpenInference/OTel local tracing."""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from contextlib import ExitStack, closing, contextmanager
from opentelemetry import trace as otel_trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Use implicit AWS credentials. Do not configure any external tracing callbacks.
os.environ['AWS_REGION_NAME'] = 'us-east-1'
os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
from agents import (Agent, ModelSettings, RunConfig, Runner, custom_span,
                    function_tool, set_trace_processors, trace)
from agents.extensions.models.litellm_model import LitellmModel
from native_capture import NativeCapture

PROFILE = 'us.anthropic.claude-sonnet-5-5'
REGION = 'us-east-1'
POLICY_VERSION = 'refund-policy-demo-v1'


@contextmanager
def database_connection(path):
    """Commit/rollback the transaction, then close the actual connection."""
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


@dataclass
class EligibilityWorkflow:
    database: Path
    scenario: str
    task_id: str
    decision: dict | None = None
    policy_errors: int = 0
    order_read: bool = False
    policy_read: bool = False
    order_snapshot: dict | None = None
    audit_hook: object = None
    audit_evidence: dict | None = None

    def setup(self):
        with database_connection(self.database) as connection:
            connection.executescript(
                'CREATE TABLE orders (order_id TEXT, age_days INTEGER, amount_cents INTEGER);'
                'CREATE TABLE policies (policy_version TEXT, max_age_days INTEGER);')
            snapshot = self.order_snapshot or {
                'order_id': 'order-demo-1',
                'age_days': 45 if self.scenario == 'denied' else 10, 'amount_cents': 2500}
            connection.execute('INSERT INTO orders VALUES (?,?,?)',
                               (snapshot['order_id'], snapshot['age_days'], snapshot['amount_cents']))
            connection.execute('INSERT INTO policies VALUES (?,?)', (POLICY_VERSION, 30))

    def order(self):
        with database_connection(self.database) as connection:
            row = connection.execute('SELECT * FROM orders').fetchone()
        self.order_read = True
        return dict(zip(('order_id', 'age_days', 'amount_cents'), row))

    def policy(self):
        with database_connection(self.database) as connection:
            try:
                table = 'checkpoint_missing_policy' if self.scenario == 'tool_error' else 'policies'
                row = connection.execute(f'SELECT * FROM {table}').fetchone()
            except sqlite3.OperationalError:
                self.policy_errors += 1
                raise
        self.policy_read = True
        return dict(zip(('policy_version', 'max_age_days'), row))

    def submit(self, eligible: bool, reason: str, policy_version: str):
        if not self.order_read or not self.policy_read:
            raise PermissionError('Read order and policy before submitting evidence')
        with database_connection(self.database) as connection:
            age = connection.execute('SELECT age_days FROM orders').fetchone()[0]
            policy, window = connection.execute('SELECT * FROM policies').fetchone()
        if policy_version != policy or eligible != (age <= window) or not reason.strip():
            raise PermissionError('Decision conflicts with authoritative order/policy evidence')
        if self.audit_hook:
            self.audit_evidence = self.audit_hook(self.order(), self.policy(), eligible)
        self.decision = {'decision_id': 'decision-' + self.task_id,
                         'order_id': 'order-demo-1', 'eligible': eligible,
                         'reason': reason, 'policy_version': policy_version,
                         'source': 'agent-2-native-sdk'}
        return self.decision

    def verify(self):
        if self.scenario in ('tool_error', 'timeout'):
            if self.scenario == 'timeout':
                return {'verified': self.decision is None, 'actual': 'failed',
                        'policy_errors': self.policy_errors}
            verified = self.decision is None and self.policy_errors > 0
            return {'verified': verified, 'actual': 'failed' if verified else 'incomplete',
                    'policy_errors': self.policy_errors}
        with database_connection(self.database) as connection:
            order_id, age = connection.execute('SELECT order_id, age_days FROM orders').fetchone()
            policy_version, window = connection.execute('SELECT * FROM policies').fetchone()
        expected = age <= window
        verified = bool(self.decision and self.decision['eligible'] == expected
                        and self.decision['policy_version'] == policy_version
                        and self.decision['order_id'] == order_id)
        return {'verified': verified,
                'actual': ('eligible' if expected else 'denied') if verified else 'incomplete',
                'policy_errors': self.policy_errors}

    def tools(self):
        @function_tool
        def get_order() -> dict:
            """Read the synthetic order. No SQL instrumentation is installed."""
            return self.order()

        @function_tool
        def get_refund_policy() -> dict:
            """Read the authoritative policy. Stop after an error; do not infer policy."""
            return self.policy()

        @function_tool
        def submit_eligibility_decision(eligible: bool, reason: str, policy_version: str) -> dict:
            """Submit eligibility evidence; the tool independently validates policy consistency."""
            return self.submit(eligible, reason, policy_version)
        return [get_order, get_refund_policy, submit_eligibility_decision]


async def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ids = {'session_id': args.session_id or 'session-' + uuid.uuid4().hex[:16],
           'task_id': 'eligibility-' + uuid.uuid4().hex[:12],
           'run_id': args.run_id or 'run-' + uuid.uuid4().hex[:16],
           'attempt_id': 'attempt-' + uuid.uuid4().hex[:16]}
    inbound = (json.loads(Path(args.inbound_request).read_text())
               if getattr(args, 'inbound_request', None) else None)
    if inbound:
        ids['session_id'] = inbound['session_id']
        ids.update({key: inbound[key] for key in (
            'workflow_id', 'handoff_id', 'handoff_attempt_id',
            'caller_run_id', 'caller_attempt_id')})
    workflow = EligibilityWorkflow(output / 'sandbox.sqlite', args.scenario, ids['task_id'],
                                   order_snapshot=inbound['order'] if inbound else None)
    workflow.setup()
    from bridge_capture import enable_bridge
    capture, otel_exporter, otel_provider = enable_bridge(output, ids)
    if os.environ.get('AGENTC_ENDPOINT') and inbound:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'agent3'))
        from caller import make_audit_hook
        workflow.audit_hook = make_audit_hook(otel_provider, ids, output, args.mode)
    error, result = None, None
    with ExitStack() as stack:
        server_span = None
        if inbound:
            remote = TraceContextTextMapPropagator().extract(inbound['trace_headers'])
            server_span = stack.enter_context(
                otel_provider.get_tracer('refund.agent2.transport', '1.0').start_as_current_span(
                    'agent2.eligibility.receive', context=remote, kind=otel_trace.SpanKind.SERVER,
                    attributes={'http.request.method': 'POST',
                                'app.transport.contract': 'agent-handoff-v1'}))
        stack.enter_context(trace('Agent 2: refund eligibility', group_id=ids['session_id'],
               metadata={**ids, 'agent_id': 'refund-eligibility-native', 'scenario': args.scenario}))
        try:
            if args.scenario == 'timeout':
                # A real bounded async deadline before any model call, clearly injected.
                await asyncio.wait_for(asyncio.sleep(1), timeout=0.1)
            elif args.mode == 'bedrock':
                agent = Agent(
                    name='Refund eligibility specialist',
                    model=LitellmModel(model='bedrock/converse/' + PROFILE),
                    model_settings=ModelSettings(max_tokens=2048, parallel_tool_calls=False,
                        extra_args={'aws_region_name': REGION, 'num_retries': 0,
                                    'timeout': 60, 'allowed_openai_params': ['thinking'], 'thinking': {'type': 'between_tools'}}),
                    tools=workflow.tools(),
                    instructions=(
                        'Evaluate synthetic refund eligibility. Call get_order and get_refund_policy. '
                        'Use the policy max_age_days: age_days <= max_age_days is eligible. '
                        'Call submit_eligibility_decision exactly once with the observed policy_version '
                        'and a short evidence-based reason. Then summarize the accepted decision. '
                        'If a tool fails, do not retry it, do not submit a decision, and report the failure. '
                        'You cannot execute refunds. Do not invent evidence.'))
                response = await Runner.run(agent, 'Assess refund eligibility for the current order.',
                    max_turns=6, run_config=RunConfig(tracing_disabled=False,
                                                    trace_include_sensitive_data=True))
                result = str(response.final_output)
            else:
                # Actual SDK custom spans only; no pretend model or framework tool spans.
                with custom_span('deterministic_fixture_execution'):
                    order = workflow.order()
                    policy = workflow.policy()
                    result = workflow.submit(order['age_days'] <= policy['max_age_days'],
                                             'Checked age against the loaded policy', policy['policy_version'])
        except Exception as exception:
            error = {'type': type(exception).__name__, 'message': str(exception)}
            if server_span:
                server_span.record_exception(exception)
                server_span.set_status(otel_trace.StatusCode.ERROR, type(exception).__name__)
        with custom_span('independent_eligibility_verification') as verification:
            evidence = workflow.verify()
            verification.span_data.data = evidence
        if server_span:
            code = 504 if args.scenario == 'timeout' else 502 if evidence['actual'] == 'failed' else 200
            server_span.set_attribute('http.response.status_code', code)
            server_span.set_attribute('app.outcome', evidence['actual'])
            server_span.set_attribute('app.outcome.verified', evidence['verified'])
            if code >= 400:
                server_span.set_status(otel_trace.StatusCode.ERROR, evidence['actual'])
    capture.force_flush()
    otel_provider.force_flush()
    records = list(capture.spans.values())
    if inbound:
        from semantic_extensions import extend_otel
        extended = extend_otel(otel_exporter.spans, records, PROFILE, REGION)
        (output / 'otel-corrected.json').write_text(json.dumps(extended, indent=2))
    generations = [s for s in records if s['span_data']['type'] == 'generation']
    functions = [s for s in records if s['span_data']['type'] == 'function']
    report = {**ids, 'agent_id': 'refund-eligibility-native', 'scenario': args.scenario,
              'model_mode': args.mode, 'model_profile': PROFILE, 'region': REGION,
              'agent_output': result, 'agent_error': error, 'decision': workflow.decision,
              'agent_c_audit': workflow.audit_evidence,
              'outcome': evidence, 'execution_status': 'error' if error else 'completed',
              'business_outcome': evidence['actual'],
              'verification_status': 'passed' if evidence['verified'] else 'failed',
              'trace_format': 'openai-agents-native-v1', 'span_count': len(records),
              'model_calls': len(generations),
              'tool_calls': [s['span_data']['name'] for s in functions],
              'usage': {key: sum((s['span_data'].get('usage') or {}).get(key, 0) for s in generations)
                        for key in ('input_tokens', 'output_tokens', 'total_tokens')},
              'capture_errors': capture.failures, 'a2a_implemented': False,
              'agent_handoff_implemented': bool(inbound),
              'pipeline_enabled': bool(os.environ.get('OBSERVABILITY_OTLP_ENDPOINT')),
              'otel_instrumentation_installed': True, 'sql_spans_captured': False,
              'otel_span_count': len(otel_exporter.spans),
              'otel_export_errors': otel_exporter.errors,
              'conversion': 'OpenInference runtime bridge; OTel SDK JSON inspection export',
              'remote_trace_export_enabled': False,
              'packages': {name: version(name) for name in (
                  'openai-agents', 'litellm', 'boto3',
                  'openinference-instrumentation-openai-agents', 'opentelemetry-sdk')}}
    (output / 'run.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if evidence['verified'] and not capture.failures and not otel_exporter.errors and (not error or args.scenario == 'tool_error') else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=('eligible', 'denied', 'tool_error', 'timeout'), default='eligible')
    parser.add_argument('--inbound-request')
    parser.add_argument('--mode', choices=('bedrock', 'fixture'), default='bedrock')
    parser.add_argument('--session-id')
    parser.add_argument('--run-id')
    parser.add_argument('--output', required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    sys.exit(main())
