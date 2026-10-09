import copy
import unittest
from native_agent import NativeRecorder, evaluate, submit_audit, POLICY, parse_traceparent
from adapter import convert, span_id

REQUEST={'order':{'order_id':'order-demo-1','age_days':10,'amount_cents':2500},
         'policy':{'policy_version':POLICY,'max_age_days':30}}

class FakeBedrock:
    def __init__(self):self.calls=0
    def converse(self,**kwargs):
        self.calls+=1
        self.kwargs=kwargs
        content=([{'toolUse':{'name':'submit_policy_audit','toolUseId':'test-tool','input':{'eligible':True,'policy_version':POLICY}}}]
                 if self.calls==1 else [{'text':'Audit accepted'}])
        return {'output':{'message':{'role':'assistant','content':content}},'usage':{'inputTokens':5,'outputTokens':2}}

class Agent3Tests(unittest.TestCase):
    def recorder(self,mode='full'):
        return NativeRecorder('1'*32,'2'*16,{'workflow_id':'workflow-test','session_id':'session-test'},mode)
    def test_trace_flags_from_actual_sdk_are_accepted(self):
        trace,parent,sampled=parse_traceparent('00-'+'1'*32+'-'+'2'*16+'-03')
        self.assertTrue(sampled);self.assertEqual(parent,'2'*16)
        self.assertFalse(parse_traceparent('00-'+'1'*32+'-'+'2'*16+'-02')[2])
        with self.assertRaises(ValueError):parse_traceparent('00-'+'0'*32+'-'+'2'*16+'-01')
    def test_independent_policy_accepts_eligible_and_denied(self):
        self.assertTrue(submit_audit(REQUEST['order'],True,POLICY)['verified'])
        order={**REQUEST['order'],'age_days':45};self.assertFalse(submit_audit(order,False,POLICY)['eligible'])
    def test_untrusted_audit_claim_is_rejected(self):
        for eligible,policy in [(False,POLICY),(1,POLICY),(True,'stale')]:
            with self.assertRaises((ValueError,PermissionError)):submit_audit(REQUEST['order'],eligible,policy)
    def test_native_model_loop_and_exact_adapter_parent(self):
        r=self.recorder();client=FakeBedrock();report=evaluate(copy.deepcopy(REQUEST),r,client=client)
        spans=convert(r.records);root=next(s for s in spans if s.name.endswith('.receive'))
        self.assertEqual(root.parent.span_id,int('2'*16,16));self.assertEqual(root.context.trace_id,int('1'*32,16))
        self.assertEqual(report['model_calls'],2);self.assertEqual(client.kwargs['inferenceConfig']['maxTokens'],768)
        for record in r.records:
            if record['parent_key']:
                span=next(s for s in spans if s.attributes['app.source.native.span_id']==record['event_key'])
                self.assertEqual(span.parent.span_id,span_id(record['parent_key']))
    def test_omitted_usage_is_not_invented(self):
        r=self.recorder('no_usage');evaluate(REQUEST,r,client=FakeBedrock());spans=convert(r.records)
        self.assertTrue(any(s.name.endswith('model.invoke') for s in spans))
        self.assertFalse(any('gen_ai.usage.input_tokens' in s.attributes for s in spans))
    def test_missing_remote_parent_is_a_root(self):
        r=self.recorder('no_parent');evaluate(REQUEST,r,'fixture');spans=convert(r.records)
        root=next(s for s in spans if s.name.endswith('.receive'));self.assertIsNone(root.parent)
    def test_native_error_survives_conversion(self):
        r=self.recorder()
        with self.assertRaises(RuntimeError):
            with r.operation('failed-tool','tool'):raise RuntimeError('test failure')
        converted=convert(r.records)[0];self.assertEqual(converted.status.status_code.name,'ERROR')
        self.assertEqual(converted.events[0].attributes['exception.message'],'test failure')
    def test_dangling_native_parent_rejected(self):
        r=self.recorder();evaluate(REQUEST,r,'fixture');r.records[0]['parent_key']='unknown'
        with self.assertRaises(ValueError):convert(r.records)
if __name__=='__main__':unittest.main()
