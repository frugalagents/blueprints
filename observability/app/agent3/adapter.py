"""Source-specific native JSON → OTel adapter. Consolidation remains unchanged."""
import hashlib
import json
import sys
from pathlib import Path
from opentelemetry.trace import SpanContext, TraceFlags, SpanKind, Status, StatusCode
from opentelemetry.sdk.trace import ReadableSpan, Event
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope

SERVICE = 'refund-policy-auditor-native-json'


def span_id(native_key):
    return int.from_bytes(hashlib.sha256(native_key.encode()).digest()[:8], 'big') or 1


def convert(records, sampled=True):
    result = []
    flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
    ids = {record['event_key']: span_id(record['event_key']) for record in records}
    if len(set(ids.values())) != len(ids):raise ValueError('Native span ID mapping collision')
    for record in records:
        trace_id = int(record['trace_key'], 16)
        if not 0 < trace_id < 2**128:raise ValueError('Invalid native trace ID')
        if record['finish_ns'] < record['begin_ns']:raise ValueError('Invalid native duration')
        context = SpanContext(trace_id, ids[record['event_key']], False, flags)
        parent = None
        if record.get('parent_key'):
            if record['parent_key'] not in ids:raise ValueError('Missing native parent record')
            parent = SpanContext(trace_id, ids[record['parent_key']], False, flags)
        elif record.get('remote_parent_key'):
            parent_id = int(record['remote_parent_key'], 16)
            if not 0 < parent_id < 2**64:raise ValueError('Invalid remote parent')
            parent = SpanContext(trace_id, parent_id, True, flags)
        attrs = {'app.' + k.replace('_','.'): v for k,v in record['context'].items()}
        attrs.update(record['fields'])
        attrs.update({'app.source.native.span_id': record['event_key'],
                      'app.source.native.format': 'policy-audit-json-v1',
                      'app.source.adapter.version': 'native-json-otel-v1'})
        if record['category'] == 'model':
            attrs.update({'gen_ai.operation.name':'chat','gen_ai.request.model':record['fields']['model'],
                          'gen_ai.provider.name':'anthropic','cloud.provider':'aws','cloud.region':'us-east-1'})
            if 'token_meter' in record:
                for source,target in [('inputTokens','input_tokens'),('outputTokens','output_tokens')]:
                    if source in record['token_meter']:attrs['gen_ai.usage.'+target] = record['token_meter'][source]
        events = [];status = Status(StatusCode.OK)
        if record['result'] == 'failure':
            error = record['error'];status = Status(StatusCode.ERROR, error['type'])
            events = [Event('exception',{'exception.type':error['type'],'exception.message':error['message']},record['finish_ns'])]
        result.append(ReadableSpan(name=record['operation'],context=context,parent=parent,
            resource=Resource.create({'service.name':SERVICE,'telemetry.capture':'native-json-adapter'}),
            attributes=attrs,events=events,status=status,
            kind=SpanKind.SERVER if record['category']=='receive' else SpanKind.INTERNAL,
            start_time=record['begin_ns'],end_time=record['finish_ns'],
            instrumentation_scope=InstrumentationScope('policy-audit.native-json-adapter','1.0')))
    return result


def export(records, output, sampled=True):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'pipeline'))
    from transport import AuditedOTLPExporter
    import os
    spans=convert(records,sampled)
    audit=[]
    for span in spans:
        item=json.loads(span.to_json());scope=span.instrumentation_scope
        item['instrumentation_scope']={'name':scope.name,'version':scope.version,'schema_url':scope.schema_url}
        audit.append(item)
    (output/'otel.json').write_text(json.dumps(audit,indent=2))
    endpoint=os.environ.get('OBSERVABILITY_OTLP_ENDPOINT')
    if not endpoint:raise RuntimeError('C adapter requires configured OTLP transport')
    exporter=AuditedOTLPExporter(endpoint,output/'otlp-export.json')
    try:
        from opentelemetry.sdk.trace.export import SpanExportResult
        if exporter.export(spans) != SpanExportResult.SUCCESS:
            raise RuntimeError('C adapter OTLP export failed')
    finally:exporter.shutdown()
    return audit
