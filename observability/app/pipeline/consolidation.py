"""Source-independent OTel service discovery and recorded relationship graph."""
from collections import Counter


def identity(span):
    context = span['context']
    return context['trace_id'], context['span_id']


def service(span):
    return span.get('resource', {}).get('attributes', {}).get('service.name') or '(service.name not captured)'


def topology(spans):
    records = {identity(s): s for s in spans}
    services = Counter(service(s) for s in records.values())
    parents, links, boundaries, gaps, roots = [], [], [], [], []
    for key, s in records.items():
        node = {'trace_id': key[0], 'span_id': key[1]}
        parent = s.get('parent_id')
        if parent:
            target = (key[0], parent)
            if target in records:
                edge = {'source': {'trace_id': target[0], 'span_id': target[1]},
                        'target': node, 'type': 'parent'}
                parents.append(edge)
                upstream = records[target]
                if service(upstream) != service(s):
                    attrs, remote = upstream.get('attributes', {}), s.get('attributes', {})
                    names = ('app.workflow.id', 'app.session.id', 'app.handoff.id', 'app.handoff.attempt.id')
                    boundaries.append({**edge, 'from_service': service(upstream), 'to_service': service(s),
                        'from_kind': upstream.get('kind'), 'to_kind': s.get('kind'),
                        'context_checks': {name: ('matched' if attrs[name] == remote[name] else 'mismatch')
                                           if name in attrs and name in remote else 'not captured' for name in names}})
            else:
                gaps.append({**node, 'type': 'missing_parent', 'parent_id': parent})
        else:
            roots.append(node)
        for link in s.get('links', []):
            ctx = link['context'];target = (ctx['trace_id'], ctx['span_id'])
            links.append({'source': node, 'target': {'trace_id': target[0], 'span_id': target[1]},
                          'type': 'link', 'target_present': target in records,
                          'attributes': link.get('attributes', {})})
    # Malformed parent cycles must not be rendered as a normal trace tree.
    for key in records:
        seen = set();current = key
        while current in records and records[current].get('parent_id'):
            if current in seen:
                gaps.append({'trace_id': key[0], 'span_id': key[1], 'type': 'parent_cycle'})
                break
            seen.add(current);current = (current[0], records[current]['parent_id'])
    return {'services': [{'name': name, 'span_count': count} for name, count in sorted(services.items())],
            'trace_count': len({k[0] for k in records}), 'span_count': len(records),
            'parents': parents, 'links': links, 'boundaries': boundaries, 'roots': roots, 'gaps': gaps,
            'relationship_rule': 'Recorded OTel parents and explicit links only; no edges inferred from workflow IDs.'}
