"""Policy-audit agent with native JSON instrumentation; no OTel dependency here."""
import json
import re
import secrets
import time
from contextlib import contextmanager

PROFILE = 'us.anthropic.claude-sonnet-5-5'
REGION = 'us-east-1'
POLICY = 'refund-policy-demo-v1'


def parse_traceparent(header):
    if not re.fullmatch(r'00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}', header):
        raise ValueError('Invalid traceparent')
    _, trace_key, parent, flags = header.split('-')
    if not int(trace_key, 16) or not int(parent, 16):
        raise ValueError('Zero trace identity')
    return trace_key, parent, bool(int(flags, 16) & 1)


class NativeRecorder:
    def __init__(self, trace_key, remote_parent, context, telemetry_mode='full'):
        self.trace_key, self.remote_parent, self.context = trace_key, remote_parent, context
        self.mode = telemetry_mode
        self.records, self.stack = [], []

    @contextmanager
    def operation(self, name, category, fields=None):
        record = {'event_key': 'audit-' + secrets.token_hex(12), 'trace_key': self.trace_key,
                  'parent_key': self.stack[-1] if self.stack else None,
                  'remote_parent_key': self.remote_parent if not self.stack and self.mode != 'no_parent' else None,
                  'operation': name, 'category': category, 'begin_ns': time.time_ns(),
                  'context': dict(self.context), 'fields': fields or {}, 'result': 'success'}
        self.stack.append(record['event_key'])
        try:
            yield record
        except Exception as error:
            record['result'] = 'failure'
            record['error'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            self.stack.pop();record['finish_ns'] = time.time_ns()
            self.records.append(record)


def submit_audit(order, eligible, policy_version):
    if type(eligible) is not bool or policy_version != POLICY:
        raise ValueError('Invalid policy-audit submission')
    expected = order['age_days'] <= 30
    if eligible != expected:
        raise PermissionError('Audit claim contradicts independently pinned policy')
    return {'verified': True, 'eligible': expected, 'policy_version': POLICY,
            'order_id': order['order_id'], 'max_age_days': 30}


def evaluate(request, recorder, mode='bedrock', client=None):
    order = request['order']
    if request['policy']['policy_version'] != POLICY or request['policy']['max_age_days'] != 30:
        raise PermissionError('Caller policy differs from audit policy')
    decision = None;model_calls = 0
    with recorder.operation('agent3.policy.audit.receive', 'receive'):
        with recorder.operation('policy-auditor.agent.loop', 'agent'):
            if mode == 'fixture':
                with recorder.operation('submit_policy_audit', 'tool'):
                    decision = submit_audit(order, order['age_days'] <= 30, POLICY)
            else:
                if client is None:
                    import boto3
                    from botocore.config import Config
                    client = boto3.Session(region_name=REGION).client('bedrock-runtime',
                        config=Config(connect_timeout=5, read_timeout=60,
                                      retries={'total_max_attempts': 1, 'mode': 'standard'}))
                messages = [{'role': 'user', 'content': [{'text': json.dumps({'order': order, 'policy_version': POLICY, 'max_age_days': 30})}]}]
                tools = {'tools': [{'toolSpec': {'name': 'submit_policy_audit',
                    'description': 'Submit the audit eligibility decision for independent verification.',
                    'inputSchema': {'json': {'type': 'object', 'properties': {
                        'eligible': {'type': 'boolean'}, 'policy_version': {'type': 'string'}},
                        'required': ['eligible','policy_version'], 'additionalProperties': False}}}}]}
                for turn in range(3):
                    with recorder.operation('policy-auditor.model.invoke', 'model',
                            {'model': PROFILE, 'input.value': json.dumps(messages)}) as event:
                        response = client.converse(modelId=PROFILE, messages=messages,
                            system=[{'text': 'Audit only the synthetic refund policy. An order is eligible if age_days <= 30. Call submit_policy_audit exactly once using the stated policy_version. Summarize only after the tool accepts the result. You cannot execute refunds.'}],
                            inferenceConfig={'maxTokens': 768}, toolConfig=tools,
                            additionalModelRequestFields={'thinking': {'type': 'between_tools'}})
                        model_calls += 1
                        event['fields']['output.value'] = json.dumps(response['output']['message'])
                        if recorder.mode != 'no_usage':
                            event['token_meter'] = response.get('usage', {})
                    message = response['output']['message'];messages.append(message)
                    requested = [block['toolUse'] for block in message['content'] if 'toolUse' in block]
                    if not requested:
                        if decision is None:raise ValueError('Model returned without a verified audit')
                        break
                    results = []
                    for tool in requested:
                        if tool['name'] != 'submit_policy_audit' or decision is not None:
                            raise ValueError('Unknown or repeated audit tool')
                        arguments = tool['input']
                        if set(arguments) != {'eligible','policy_version'}:
                            raise ValueError('Malformed model tool arguments')
                        with recorder.operation('submit_policy_audit', 'tool'):
                            decision = submit_audit(order, **arguments)
                        results.append({'toolResult': {'toolUseId': tool['toolUseId'],
                                                      'content': [{'json': decision}]}})
                    messages.append({'role': 'user', 'content': results})
                if decision is None:raise ValueError('No verified audit in bounded model loop')
            with recorder.operation('policy-auditor.independent.verification', 'verify') as record:
                confirmed = submit_audit(order, decision['eligible'], decision['policy_version'])
                record['fields'] = {'app.verification.verified': confirmed == decision,
                                    'app.verification.actual': 'eligible' if decision['eligible'] else 'denied'}
            return {'decision': decision, 'model_calls': model_calls,
                    'native_format': 'policy-audit-json-v1', 'telemetry_mode': recorder.mode}
