"""Config-only audit capture discovery; audit is not a telemetry ingestion path."""
import json
from pathlib import Path
from consolidation import identity


def read(path, default):
    try:return json.loads(path.read_text())
    except (OSError, ValueError):return default


def captures(root, config, workflow_id, trace_ids):
    records, native = {}, {}
    for source in read(config, {}).get('sources', []):
        for path in Path(root).glob(source['otel_glob']):
            spans = read(path, [])
            chosen = [s for s in spans if s.get('attributes', {}).get('app.workflow.id') == workflow_id
                      or s.get('context', {}).get('trace_id') in trace_ids]
            if not chosen:continue
            for s in chosen:records[identity(s)] = s
            native_path = source.get('native_file')
            if native_path:
                raw = read(path.parent / native_path, {})
                items = raw.get(source['native_records_key'], [])
                lookup = {n.get(source['native_id_key']): n for n in items}
                for s in chosen:
                    record = lookup.get(s.get('attributes', {}).get(source['otel_native_id_attribute']))
                    if record is not None:native['/'.join(identity(s))] = record
    return {'spans': list(records.values()), 'native_by_otel_identity': native}
