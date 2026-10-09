import tempfile
import unittest
from pathlib import Path
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from store import TraceStore

class StoreTests(unittest.TestCase):
    def payload(self, provenance=True):
        m=ExportTraceServiceRequest();r=m.resource_spans.add();a=r.resource.attributes.add();a.key='service.name';a.value.string_value='example'
        s=r.scope_spans.add().spans.add();s.name='operation';s.trace_id=b't'*16;s.span_id=b's'*8;s.start_time_unix_nano=1;s.end_time_unix_nano=2;s.status.code=2;s.status.message='failure'
        for k,v in [('app.workflow.id','test'),('app.pipeline.version','collector-v1')]:
            if not provenance and k=='app.pipeline.version':continue
            a=s.attributes.add();a.key=k;a.value.string_value=v
        return m.SerializeToString()
    def test_deduplicated_spans_and_retained_batches(self):
        with tempfile.TemporaryDirectory() as d:
            store=TraceStore(Path(d)/'store.sqlite');store.ingest(self.payload());store.ingest(self.payload())
            self.assertEqual(store.status()['stored_spans'],1);self.assertEqual(store.status()['received_batches'],2)
            s=store.workflow('test')[0];self.assertEqual(s['status'],{'status_code':'ERROR','description':'failure'});self.assertEqual(s['start_time_unix_nano'],'1')
    def test_bad_batch_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store=TraceStore(Path(d)/'store.sqlite')
            for payload in (b'bad protobuf',self.payload(False)):
                with self.assertRaises(Exception):store.ingest(payload)
            self.assertEqual(store.status()['stored_spans'],0);self.assertEqual(store.status()['received_batches'],0)
if __name__=='__main__':unittest.main()

class WorkflowDiscoveryTests(unittest.TestCase):
    def test_same_trace_downstream_without_workflow_attribute(self):
        with tempfile.TemporaryDirectory() as d:
            store=TraceStore(Path(d)/'store.sqlite');payload=ExportTraceServiceRequest();payload.ParseFromString(StoreTests.payload(self))
            downstream=payload.resource_spans[0].scope_spans[0].spans.add();downstream.CopyFrom(payload.resource_spans[0].scope_spans[0].spans[0]);downstream.span_id=b'd'*8;downstream.parent_span_id=b's'*8
            for i in range(len(downstream.attributes)-1,-1,-1):
                if downstream.attributes[i].key=='app.workflow.id':del downstream.attributes[i]
            store.ingest(payload.SerializeToString())
            self.assertEqual(len(store.workflow('test')),1)
            self.assertEqual(len(store.consolidated_workflow('test')),2)
            self.assertEqual(store.workflows()[0]['id'],'test')
