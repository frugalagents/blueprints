"""B application-side call to C; this is not consolidation code."""
import json
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def make_audit_hook(provider, ids, output, mode):
    endpoint=os.environ['AGENTC_ENDPOINT']
    def audit(order,policy,eligible):
        context={k:ids[k] for k in ('session_id','workflow_id')}
        context.update(handoff_id='audit-handoff-'+uuid.uuid4().hex[:16],
            handoff_attempt_id='audit-ha-'+uuid.uuid4().hex[:16],
            caller_run_id=ids['run_id'],caller_attempt_id=ids['attempt_id'])
        body={**context,'order':order,'policy':policy,'model_mode':mode}
        with provider.get_tracer('refund.agent2.audit-call','1.0').start_as_current_span(
                'agent3.policy.audit.call',kind=SpanKind.CLIENT) as span:
            # Set after SourceAnnotations.on_start, overriding only this call's
            # handoff context, so the B→C edge has its own actual attempt IDs.
            span.set_attributes({'app.'+k.replace('_','.'):v for k,v in context.items()})
            headers={'Content-Type':'application/json','X-AgentC-Token':os.environ['AGENTC_TOKEN']}
            TraceContextTextMapPropagator().inject(headers)
            (Path(output)/'agent-c-handoff-attempt.json').write_text(json.dumps({'request':body,'traceparent':headers.get('traceparent')},indent=2))
            try:
                with urlopen(Request(endpoint,data=json.dumps(body).encode(),headers=headers),timeout=105) as response:
                    result=json.load(response)
                    span.set_attribute('http.response.status_code',response.status)
            except HTTPError as error:
                span.set_attribute('http.response.status_code',error.code)
                detail=json.loads(error.read())
                raise RuntimeError('Agent C rejected audit: '+str(detail.get('error') or detail)) from error
            decision=result.get('decision') or {}
            if (any(result.get(k)!=v for k,v in context.items())
                or result.get('verification_status')!='passed'
                or decision.get('verified') is not True or type(decision.get('eligible')) is not bool
                or decision['eligible']!=eligible or decision.get('order_id')!=order['order_id']
                or decision.get('policy_version')!=policy['policy_version'] or decision.get('max_age_days')!=policy['max_age_days']):
                raise PermissionError('Agent C returned unverified or mismatched audit evidence')
            span.set_attribute('app.verification.verified',True)
            (Path(output)/'agent-c-handoff.json').write_text(json.dumps({'request':body,'response':result},indent=2))
            return result
    return audit
