"""Conservation and cleaning checks against source audit records."""
import re

CONTENT = re.compile(r'^(input\.value|output\.value|llm\.(input_messages|output_messages)\..*|gen_ai\.tool\.call\.(arguments|result)|tool\.parameters|gen_ai\.tool\.json_schema|gen_ai\.(system_instructions|input\.messages|output\.messages))$')
PRESERVED = ('app.handoff.id','app.handoff.attempt.id','app.workflow.id','app.session.id','app.task.id',
             'app.run.id','app.attempt.id','app.source.native.span_id','app.outcome',
             'app.outcome.verified','app.verification.verified','app.verification.actual',
             'app.verification.policy_errors')


def compare(source, stored):
    expected={(s['context']['trace_id'],s['context']['span_id']):s for s in source}
    received={(s['context']['trace_id'],s['context']['span_id']):s for s in stored}
    missing=set(expected)-set(received);unexpected=set(received)-set(expected)
    differences=[];removed=0;content_left=[]
    for key, before in expected.items():
        after=received.get(key)
        if not after:continue
        for field in ('name','parent_id','kind','status'):
            if before.get(field)!=after.get(field):differences.append({'span_id':key[1],'field':field})
        def links(record):
            return [(link['context']['trace_id'], link['context']['span_id'], link.get('attributes', {}))
                    for link in record.get('links', [])]
        if links(before) != links(after):
            differences.append({'span_id':key[1],'field':'links'})
        if before.get('resource', {}).get('attributes', {}).get('service.name') != after.get('resource', {}).get('attributes', {}).get('service.name'):
            differences.append({'span_id':key[1],'field':'service.name'})
        for attr in PRESERVED:
            if before['attributes'].get(attr)!=after['attributes'].get(attr):
                differences.append({'span_id':key[1],'field':attr})
        for input_key, output_key in (
            ('gen_ai.usage.input_tokens','gen_ai.usage.input_tokens'),
            ('gen_ai.usage.output_tokens','gen_ai.usage.output_tokens'),
            ('llm.token_count.prompt','gen_ai.usage.input_tokens'),
            ('llm.token_count.completion','gen_ai.usage.output_tokens')):
            if input_key in before['attributes'] and before['attributes'][input_key]!=after['attributes'].get(output_key):
                differences.append({'span_id':key[1],'field':output_key})
        old_content=[k for k in before['attributes'] if CONTENT.match(k)]
        old_content += [k for e in before.get('events', []) for k in e['attributes'] if CONTENT.match(k)]
        removed+=len(old_content)
    for after in stored:
        content_left += [k for k in after['attributes'] if CONTENT.match(k)]
        content_left += [k for e in after.get('events', []) for k in e['attributes'] if CONTENT.match(k)]
        if after['attributes'].get('app.pipeline.version')!='collector-v1':
            differences.append({'span_id':after['context']['span_id'],'field':'Collector provenance'})
    passed=bool(expected and not missing and not unexpected and not differences and not content_left)
    return {'passed':passed,'expected_spans':len(expected),'stored_spans':len(received),
            'missing_span_ids':[k[1] for k in sorted(missing)],
            'unexpected_span_ids':[k[1] for k in sorted(unexpected)],
            'differences':differences,'content_fields_removed':removed,
            'content_fields_remaining':content_left,
            'checks':['trace/span identity','parent relationships','explicit links','service identity','status/errors',
                      'handoff/workflow/run context','token usage','business verification','content cleaning']}
