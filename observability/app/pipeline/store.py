"""A loopback OTLP protobuf receiver and SQLite query store; no new wire format."""
import base64
import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


POLICY = 'collector-content-policy-v1'


def value(item):
    kind = item.WhichOneof('value')
    if kind == 'array_value': return [value(v) for v in item.array_value.values]
    if kind == 'kvlist_value': return attributes(item.kvlist_value.values)
    if kind == 'bytes_value': return base64.b64encode(item.bytes_value).decode()
    return getattr(item, kind) if kind else None


def attributes(items):
    return {item.key: value(item.value) for item in items}


def timestamp(ns):
    # The original nanosecond value is retained separately, too.
    return datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc).isoformat().replace('+00:00', 'Z')


def identity(data, size):
    if len(data) != size or not any(data):
        raise ValueError('Invalid OTLP trace/span identity')
    return '0x' + data.hex()


def decode(payload):
    message = ExportTraceServiceRequest()
    message.ParseFromString(payload)
    records = []
    for resource in message.resource_spans:
        resource_attrs = attributes(resource.resource.attributes)
        for scope in resource.scope_spans:
            for span in scope.spans:
                if span.end_time_unix_nano < span.start_time_unix_nano:
                    raise ValueError('Invalid OTLP duration')
                record = {
                    'name': span.name,
                    'context': {'trace_id': identity(span.trace_id, 16),
                                'span_id': identity(span.span_id, 8),
                                'trace_state': span.trace_state},
                    'parent_id': identity(span.parent_span_id, 8) if span.parent_span_id else None,
                    'kind': 'SpanKind.' + {0:'UNSPECIFIED',1:'INTERNAL',2:'SERVER',3:'CLIENT',4:'PRODUCER',5:'CONSUMER'}.get(span.kind, 'UNSPECIFIED'),
                    'start_time': timestamp(span.start_time_unix_nano),
                    'end_time': timestamp(span.end_time_unix_nano),
                    'start_time_unix_nano': str(span.start_time_unix_nano),
                    'end_time_unix_nano': str(span.end_time_unix_nano),
                    'status': {'status_code': {0:'UNSET',1:'OK',2:'ERROR'}.get(span.status.code,'UNSET')},
                    'attributes': attributes(span.attributes),
                    'events': [{'name':e.name,'timestamp':timestamp(e.time_unix_nano),
                                'attributes':attributes(e.attributes)} for e in span.events],
                    'links': [{'context':{'trace_id':identity(link.trace_id,16),
                                           'span_id':identity(link.span_id,8),
                                           'trace_state':link.trace_state},
                               'attributes':attributes(link.attributes)} for link in span.links],
                    'resource':{'attributes':resource_attrs,'schema_url':resource.schema_url},
                    'instrumentation_scope':{'name':scope.scope.name,'version':scope.scope.version,
                                              'schema_url':scope.schema_url},
                    'flags':span.flags,
                    'dropped_attributes_count':span.dropped_attributes_count,
                    'dropped_events_count':span.dropped_events_count,
                    'dropped_links_count':span.dropped_links_count,
                }
                if span.status.message:record['status']['description']=span.status.message
                records.append(record)
    return records


class TraceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS spans(
                    trace_id TEXT,span_id TEXT,workflow_id TEXT,run_id TEXT,service TEXT,
                    record TEXT,received_at TEXT,PRIMARY KEY(trace_id,span_id));
                CREATE INDEX IF NOT EXISTS workflow_spans ON spans(workflow_id);
                CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY,body BLOB,
                    span_count INTEGER,received_at TEXT);''')

    def ingest(self, payload):
        records = decode(payload)  # Fully validate before any writes.
        for record in records:
            if record['attributes'].get('app.pipeline.version') != 'collector-v1':
                raise ValueError('Missing Collector processing provenance')
        received = datetime.now(timezone.utc).isoformat()
        with self.lock, closing(sqlite3.connect(self.path)) as conn, conn:
            for r in records:
                a=r['attributes'];ctx=r['context']
                conn.execute('INSERT INTO spans VALUES (?,?,?,?,?,?,?) ON CONFLICT(trace_id,span_id) DO UPDATE SET record=excluded.record,received_at=excluded.received_at',
                    (ctx['trace_id'],ctx['span_id'],a.get('app.workflow.id'),a.get('app.run.id'),
                     r['resource']['attributes'].get('service.name'),json.dumps(r),received))
            conn.execute('INSERT INTO batches(body,span_count,received_at) VALUES (?,?,?)',
                         (payload,len(records),received))
        return len(records)

    def workflow(self, workflow_id):
        with self.lock, closing(sqlite3.connect(self.path)) as conn:
            rows=conn.execute('SELECT record FROM spans WHERE workflow_id=? ORDER BY received_at',
                              (workflow_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def workflows(self):
        with self.lock, closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute('SELECT workflow_id,COUNT(*),MAX(received_at) FROM spans WHERE workflow_id IS NOT NULL GROUP BY workflow_id ORDER BY MAX(received_at) DESC').fetchall()
        return [{'id': row[0], 'span_count': row[1], 'last_received_at': row[2]} for row in rows]

    def consolidated_workflow(self, workflow_id):
        # Trace context connects downstream spans even when the optional workflow
        # attribute is missing. Workflow membership alone never implies an edge.
        with self.lock, closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute('SELECT record FROM spans WHERE workflow_id=? OR trace_id IN (SELECT trace_id FROM spans WHERE workflow_id=?) ORDER BY received_at', (workflow_id, workflow_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def status(self):
        with self.lock, closing(sqlite3.connect(self.path)) as conn:
            count=conn.execute('SELECT COUNT(*) FROM spans').fetchone()[0]
            batches=conn.execute('SELECT COUNT(*),MAX(received_at) FROM batches').fetchone()
        return {'stored_spans':count,'received_batches':batches[0],'last_received_at':batches[1],
                'storage':'SQLite + retained processed OTLP protobuf batches'}
