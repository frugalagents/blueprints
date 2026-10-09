import copy
import unittest
from consolidation import topology


def span(trace, sid, service, parent=None, **attrs):
    return {'name': 'arbitrary-operation', 'context': {'trace_id': trace, 'span_id': sid},
            'parent_id': parent, 'kind': 'SpanKind.INTERNAL', 'attributes': attrs,
            'resource': {'attributes': {'service.name': service}}, 'links': []}


class ConsolidationTests(unittest.TestCase):
    def test_arbitrary_services_and_same_span_id_in_separate_traces(self):
        records=[span('trace1','root','renamed-service'),span('trace1','child','unknown-plugin','root'),
                 span('trace2','root','third-service')]
        graph=topology(records)
        self.assertEqual(graph['span_count'],3)
        self.assertEqual(len(graph['services']),3)
        self.assertEqual(graph['boundaries'][0]['from_service'],'renamed-service')
        self.assertEqual(graph['boundaries'][0]['context_checks']['app.handoff.id'],'not captured')
        self.assertEqual(len(graph['roots']),2)
    def test_workflow_id_does_not_infer_relationship(self):
        records=[span('trace1','a','one',**{'app.workflow.id':'same'}),span('trace2','b','two',**{'app.workflow.id':'same'})]
        self.assertEqual(topology(records)['parents'],[])
    def test_missing_parent_and_cross_trace_link_remain_distinct(self):
        a=span('trace1','a','one','absent');b=span('trace2','a','two')
        a['links']=[{'context':b['context'],'attributes':{'reason':'async'}}]
        g=topology([a,b]);self.assertEqual(g['gaps'][0]['type'],'missing_parent')
        self.assertTrue(g['links'][0]['target_present']);self.assertEqual(g['parents'],[])
    def test_cycle_is_reported(self):
        g=topology([span('t','a','one','b'),span('t','b','one','a')])
        self.assertTrue(all(gap['type']=='parent_cycle' for gap in g['gaps']))
    def test_no_mutation(self):
        spans=[span('t','a','one')];previous=copy.deepcopy(spans);topology(spans);self.assertEqual(previous,spans)
