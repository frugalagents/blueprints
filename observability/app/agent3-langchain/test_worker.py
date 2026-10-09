import tempfile
import unittest
from pathlib import Path
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatResult,ChatGeneration
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from worker import configure,evaluate,NativeRunCapture,POLICY

class FakeAuditModel(BaseChatModel):
    eligible:bool=True
    @property
    def _llm_type(self):return 'test-policy-model'
    def bind_tools(self,tools,**kwargs):return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        if any(isinstance(message,ToolMessage) for message in messages):
            message=AIMessage(content='Audit accepted',usage_metadata={'input_tokens':5,'output_tokens':2,'total_tokens':7})
        else:
            message=AIMessage(content='',tool_calls=[{'name':'submit_policy_audit','args':{'eligible':self.eligible,'policy_version':POLICY},'id':'audit-tool','type':'tool_call'}],usage_metadata={'input_tokens':5,'output_tokens':2,'total_tokens':7})
        return ChatResult(generations=[ChatGeneration(message=message)])

class FrameworkTests(unittest.TestCase):
    def exercise(self,age=10,claim=True):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);output=Path(temp.name)
        ids={'workflow_id':'test-workflow','session_id':'test-session','run_id':'test-c'}
        provider,capture,instrumentor=configure(output,ids)
        self.addCleanup(provider.shutdown);self.addCleanup(instrumentor.uninstrument)
        native=NativeRunCapture(output/'native.json')
        remote=TraceContextTextMapPropagator().extract({'traceparent':'00-'+'1'*32+'-'+'2'*16+'-03'})
        request={'order':{'order_id':'order-demo-1','age_days':age,'amount_cents':2500},'policy':{'policy_version':POLICY,'max_age_days':30}}
        with provider.get_tracer('test').start_as_current_span('receive',context=remote):
            result=evaluate(request,provider,native,FakeAuditModel(eligible=claim))
        provider.force_flush();return result,native,capture
    def test_real_framework_auto_spans_and_source_identity(self):
        result,native,capture=self.exercise()
        auto=[s for s in capture.records if s['instrumentation_scope']['name'].startswith('openinference.')]
        self.assertTrue(result['decision']['verified']);self.assertTrue(auto)
        self.assertTrue(all(s['context']['trace_id']=='0x'+'1'*32 for s in auto))
        self.assertTrue(all(s['attributes'].get('app.source.native.span_id') in native.records for s in auto))
        kinds={s['attributes'].get('openinference.span.kind') for s in auto}
        self.assertIn('LLM',kinds);self.assertIn('TOOL',kinds)
    def test_denied_order_is_audited_by_trusted_tool(self):
        result,_,_=self.exercise(age=45,claim=False);self.assertFalse(result['decision']['eligible'])
    def test_false_model_claim_fails_closed(self):
        with self.assertRaises(PermissionError):self.exercise(age=45,claim=True)
if __name__=='__main__':unittest.main()
