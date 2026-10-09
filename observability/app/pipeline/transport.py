"""Optional standard OTLP/HTTP export, alongside source audit capture."""
import json
import os
import threading
from pathlib import Path
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


class AuditedOTLPExporter(SpanExporter):
    def __init__(self, endpoint, path):
        self.delegate = OTLPSpanExporter(endpoint=endpoint, timeout=8)
        self.path = Path(path)
        self.endpoint = endpoint
        self.accepted = set()
        self.failures = 0
        self.lock = threading.RLock()

    def export(self, spans):
        with self.lock:
            return self._export(spans)

    def _export(self, spans):
        try:
            result = self.delegate.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        if result == SpanExportResult.SUCCESS:
            self.accepted.update((f'{s.context.trace_id:032x}', f'{s.context.span_id:016x}')
                                 for s in spans)
        else:
            self.failures += len(spans)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.summary(), indent=2))
        temporary.replace(self.path)
        return result

    def summary(self):
        return {'transport': 'OTLP/HTTP protobuf', 'endpoint': self.endpoint,
                'accepted_by_collector': len(self.accepted), 'export_failures': self.failures,
                'accepted_span_ids': sorted(self.accepted),
                'note': 'Collector acceptance is verified separately against persisted output.'}

    def shutdown(self):
        self.delegate.shutdown()


def configure_otlp(provider, output):
    endpoint = os.environ.get('OBSERVABILITY_OTLP_ENDPOINT')
    if not endpoint:
        return None
    exporter = AuditedOTLPExporter(endpoint, Path(output) / 'otlp-export.json')
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter
