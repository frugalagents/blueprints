"""Explicit v1 transformations of OTel SDK JSON; raw bridge export stays intact.

These attribute rules can later move to a Collector processor. This checkpoint
does not claim an OTLP exporter or a new telemetry format.
"""
import copy


def extend_otel(spans, native, profile, region):
    result = copy.deepcopy(spans)
    originals = {span['id']: span for span in native}
    for span in result:
        attrs = span['attributes']
        source = originals.get(attrs.get('app.source.native.span_id'))
        changes = []
        if source and source['span_data']['type'] == 'generation':
            attrs.update({'llm.system': 'anthropic', 'gen_ai.provider.name': 'anthropic',
                          'gen_ai.request.model': profile, 'cloud.provider': 'aws',
                          'cloud.region': region,
                          'app.model.serving_platform': 'amazon-bedrock'})
            changes.append('Bedrock provider semantics')
        if source and source['span_data'].get('name') == 'independent_eligibility_verification':
            data = source['span_data'].get('data') or {}
            for key in ('verified', 'actual', 'policy_errors'):
                if key in data:
                    attrs['app.verification.' + key] = data[key]
            changes.append('native custom verification evidence')
        if changes:
            attrs['app.telemetry.transform.version'] = 'agent2-semantic-extensions-v1'
            attrs['app.telemetry.transform.changes'] = changes
    return result
