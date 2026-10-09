"""Validate the deliberately small local handoff boundary."""
import re

ID_FIELDS = ('session_id', 'workflow_id', 'handoff_id', 'handoff_attempt_id',
             'caller_run_id', 'caller_attempt_id')


def validate_request(data):
    expected = {'contract_version', 'order', 'scenario', 'model_mode', *ID_FIELDS}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError('Invalid handoff fields')
    if data['contract_version'] != 'agent-handoff-v1':
        raise ValueError('Unsupported handoff contract')
    for key in ID_FIELDS:
        if not isinstance(data[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', data[key]):
            raise ValueError('Invalid ' + key)
    if data['scenario'] not in ('eligible', 'denied', 'tool_error', 'timeout', 'db_error'):
        raise ValueError('Invalid handoff scenario')
    if data['model_mode'] not in ('fixture', 'bedrock'):
        raise ValueError('Invalid model mode')
    order = data['order']
    if (not isinstance(order, dict) or set(order) != {'order_id', 'age_days', 'amount_cents'}
        or order['order_id'] != 'order-demo-1'
        or type(order['age_days']) is not int or not 0 <= order['age_days'] <= 365
        or type(order['amount_cents']) is not int or not 0 < order['amount_cents'] <= 100000):
        raise ValueError('Invalid synthetic order')
    return data


def correlation_result(a, b):
    clients = [s for s in a if s['name'] == 'agent2.eligibility.call']
    servers = [s for s in b if s['name'] == 'agent2.eligibility.receive']
    if len(clients) != 1 or len(servers) != 1:
        return {'verified': False, 'reason': 'Waiting for completed CLIENT and SERVER spans'}
    client, server = clients[0], servers[0]
    keys = ('app.workflow.id', 'app.handoff.id', 'app.handoff.attempt.id', 'app.session.id')
    ids_match = all(client['attributes'].get(k) and
                    client['attributes'][k] == server['attributes'].get(k) for k in keys)
    same_trace = client['context']['trace_id'] == server['context']['trace_id']
    parent_matches = server['parent_id'] == client['context']['span_id']
    return {'verified': bool(ids_match and same_trace and parent_matches),
            'ids_match': ids_match, 'same_trace': same_trace, 'parent_matches': parent_matches,
            'client_span_id': client['context']['span_id'],
            'server_span_id': server['context']['span_id'],
            'trace_id': client['context']['trace_id'],
            'reason': 'Checked actual IDs and remote parent; no inferred edges'}
