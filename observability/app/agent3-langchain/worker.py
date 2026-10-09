"""LangChain policy auditor with upstream OpenInference instrumentation."""
import argparse
import json
import os
import sys
import threading
import uuid
from contextvars import ContextVar
from importlib.metadata import version
from pathlib import Path

# Use local capture only; prevent any inherited remote LangSmith tracing.
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_core.tracers.base import BaseTracer
from openinference.instrumentation.langchain import LangChainInstrumentor, get_current_span
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'agent3'))
from native_agent import submit_audit, POLICY, PROFILE, REGION
sys.path.insert(0,str(ROOT.parent/'pipeline'))
from transport import configure_otlp

SERVICE='refund-policy-auditor-langchain'
source_run=ContextVar('langchain_native_source',default=None)


class NativeRunCapture(BaseTracer):
    """Retain the framework's actual Run objects; no native→OTel conversion."""
    run_inline=True
    def __init__(self,path):
        super().__init__();self.path=path;self.records={};self.lock=threading.RLock()
    def _start_trace(self,run):
        source_run.set(str(run.id))
        super()._start_trace(run)
    def _persist_run(self,run):self.save(run)
    def _on_run_update(self,run):self.save(run)
    def save(self,run):
        record=json.loads(run.model_dump_json(exclude={'child_runs'}))
        with self.lock:
            self.records[str(run.id)]=record
            temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps({'records':list(self.records.values())},indent=2));temporary.replace(self.path)


class Annotations(SpanProcessor):
    """Source identity/context extensions, applied to real instrumentor spans."""
    def __init__(self,ids):self.ids=ids
    def on_start(self,span,parent_context=None):
        native_id=source_run.get();source_run.set(None)
        if native_id:span.set_attribute('app.source.native.span_id',native_id)
        span.set_attributes({'app.'+k.replace('_','.'):v for k,v in self.ids.items()})
        span.set_attributes({'app.source.extension.version':'langchain-context-v1',
            'gen_ai.provider.name':'anthropic','cloud.provider':'aws','cloud.region':REGION,
            'app.model.serving_platform':'amazon-bedrock'})
    def on_end(self,span):pass
    def shutdown(self):pass
    def force_flush(self,timeout_millis=30000):return True


class CaptureExporter(SpanExporter):
    def __init__(self,path):self.path=path;self.records=[];self.lock=threading.RLock()
    def export(self,spans):
        with self.lock:
            for span in spans:
                record=json.loads(span.to_json());scope=span.instrumentation_scope
                record['instrumentation_scope']={'name':scope.name,'version':scope.version,'schema_url':scope.schema_url}
                self.records.append(record)
            temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(self.records,indent=2));temporary.replace(self.path)
        return SpanExportResult.SUCCESS
    def shutdown(self):pass


def configure(output,ids):
    provider=TracerProvider(resource=Resource.create({'service.name':SERVICE,'telemetry.capture':'openinference-langchain'}))
    provider.add_span_processor(Annotations(ids));capture=CaptureExporter(output/'otel.json')
    provider.add_span_processor(SimpleSpanProcessor(capture));configure_otlp(provider,output)
    # Explicit provider; no global provider replacement or custom span conversion.
    instrumentor=LangChainInstrumentor()
    instrumentor.instrument(tracer_provider=provider,separate_trace_from_runtime_context=False)
    return provider,capture,instrumentor


def evaluate(request,provider,native,model=None):
    order=request['order'];policy=request['policy']
    if policy['policy_version']!=POLICY or policy['max_age_days']!=30:
        raise PermissionError('Caller policy differs from pinned audit policy')
    decision=None
    @tool
    def submit_policy_audit(eligible:bool,policy_version:str)->dict:
        """Submit the audit claim. The tool checks the pinned synthetic policy independently."""
        nonlocal decision
        if decision is not None:raise PermissionError('Duplicate audit submission')
        current=get_current_span()
        context=trace.set_span_in_context(current) if current is not None else None
        with provider.get_tracer('policy-audit.verification','1.0').start_as_current_span(
                'policy-auditor.independent.verification',context=context) as evidence:
            decision=submit_audit(order,eligible,policy_version)
            evidence.set_attributes({'app.verification.verified':True,
                'app.verification.actual':'eligible' if decision['eligible'] else 'denied'})
            if current is not None:
                current.set_attribute('app.verification.verified',True)
        return decision
    if model is None:
        from langchain_aws import ChatBedrockConverse
        from botocore.config import Config
        model=ChatBedrockConverse(model_id=PROFILE,region_name=REGION,max_tokens=768,
            config=Config(connect_timeout=5,read_timeout=60,retries={'total_max_attempts':1,'mode':'standard'}),
            additional_model_request_fields={'thinking':{'type':'between_tools'}})
    agent=create_agent(model=model,tools=[submit_policy_audit],name='LangChain policy auditor',
        system_prompt='Audit only the synthetic refund policy. An order is eligible if age_days <= 30. Call submit_policy_audit exactly once with the stated policy_version. Summarize after the tool accepts it. Stop if it fails. You cannot execute refunds.')
    response=agent.invoke({'messages':[{'role':'user','content':json.dumps({'order':order,'policy_version':POLICY,'max_age_days':30})}]},
                          config={'callbacks':[native],'recursion_limit':10})
    if decision is None:raise PermissionError('LangChain agent returned without independently verified audit')
    return {'decision':decision,'agent_output':str(response['messages'][-1].content),
            'framework':'langchain','conversion':'upstream OpenInference LangChain instrumentor',
            'native_format':'LangChain Run schema'}


def run(args):
    output=Path(args.output);request=json.loads(Path(args.request).read_text())
    ids={k:request[k] for k in ('session_id','workflow_id','handoff_id','handoff_attempt_id','caller_run_id','caller_attempt_id')}
    ids.update(run_id=output.name,attempt_id='attempt-'+uuid.uuid4().hex[:16],task_id='langchain-audit-'+uuid.uuid4().hex[:12])
    provider,capture,instrumentor=configure(output,ids)
    native=NativeRunCapture(output/'native.json');result=None;error=None
    remote=TraceContextTextMapPropagator().extract(request['trace_headers'])
    try:
        with provider.get_tracer('policy-audit.http','1.0').start_as_current_span(
                'agent3.policy.audit.receive',context=remote,kind=trace.SpanKind.SERVER) as boundary:
            if request['model_mode']!='bedrock':raise ValueError('External framework demo requires real model execution; unit tests inject a fake model explicitly')
            result=evaluate(request,provider,native)
            boundary.set_attributes({'app.verification.verified':True,'app.outcome':'eligible' if result['decision']['eligible'] else 'denied'})
    except Exception as exception:error={'type':type(exception).__name__,'message':str(exception)}
    finally:
        provider.force_flush();instrumentor.uninstrument();provider.shutdown()
    report={**ids,**(result or {}),'agent_id':SERVICE,'error':error,
        'verification_status':'passed' if result and not error else 'failed',
        'model_calls':sum(r['run_type']=='llm' for r in native.records.values()),
        'packages':{name:version(name) for name in ('langchain','langchain-core','langchain-aws','openinference-instrumentation-langchain','opentelemetry-sdk')}}
    (output/'run.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'run_id':output.name,'verification_status':report['verification_status'],'error':error}),flush=True)
    return 0 if report['verification_status']=='passed' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True)
    return run(parser.parse_args())
if __name__=='__main__':sys.exit(main())
