"""OpenInference runtime bridge with local OTel export and source-ID annotations.

OpenInference performs the conversion. These observers only retain evidence and
annotate source identity; they do not implement a second native-to-OTel mapper.
"""
import json
import threading
import sys
from pathlib import Path
from contextvars import ContextVar

from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor

from native_capture import NativeCapture

source_identity = ContextVar('native_source_identity', default=None)


class PairedNativeCapture(NativeCapture):
    """Registered immediately before the bridge, on the same SDK callback."""
    def __init__(self, path, live_spans):
        super().__init__(path)
        self.live_spans = live_spans

    def on_trace_start(self, item):
        source_identity.set({'app.source.native.trace_id': item.trace_id,
                             'app.source.native.record_type': 'trace'})
        super().on_trace_start(item)

    def on_span_start(self, item):
        source_identity.set({'app.source.native.trace_id': item.trace_id,
                             'app.source.native.span_id': item.span_id,
                             'app.source.native.parent_id': item.parent_id or '',
                             'app.source.native.record_type': 'span'})
        super().on_span_start(item)

    def on_span_end(self, item):
        # Add trusted native custom evidence to its actual live OTel span before
        # OpenInference ends/exports it. No reconstructed or replacement spans.
        live = self.live_spans.pop(item.span_id, None)
        if live and getattr(item.span_data, "name", None) == "independent_eligibility_verification":
            data = getattr(item.span_data, "data", None) or {}
            for key in ("verified", "actual", "policy_errors"):
                if key in data:
                    live.set_attribute("app.verification." + key, data[key])
            live.set_attribute("app.source.extension.version", "verification-evidence-v1")
        super().on_span_end(item)


class SourceAnnotations(SpanProcessor):
    def __init__(self, ids, live_spans):
        self.ids = ids
        self.live_spans = live_spans

    def on_start(self, span, parent_context=None):
        identity = source_identity.get()
        source_identity.set(None)
        if identity:
            span.set_attributes(identity)
            native_id = identity.get("app.source.native.span_id")
            if native_id:
                self.live_spans[native_id] = span
        span.set_attributes({'app.' + key.replace('_', '.'): value
                             for key, value in self.ids.items()})

    def on_end(self, span): pass
    def shutdown(self): pass
    def force_flush(self, timeout_millis=30000): return True


class LocalOTelExporter(SpanExporter):
    def __init__(self, path):
        self.path, self.spans, self.errors = path, [], []
        self.lock = threading.RLock()

    def export(self, spans):
        with self.lock:
            try:
                for span in spans:
                    record = json.loads(span.to_json())
                    scope = span.instrumentation_scope
                    record['instrumentation_scope'] = {
                        'name': scope.name, 'version': scope.version,
                        'schema_url': scope.schema_url}
                    self.spans.append(record)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.spans, indent=2))
                temporary.replace(self.path)
                return SpanExportResult.SUCCESS
            except Exception as error:
                self.errors.append(type(error).__name__)
                return SpanExportResult.FAILURE

    def shutdown(self): pass


def enable_bridge(output, ids):
    from agents import set_trace_processors
    live_spans = {}
    capture = PairedNativeCapture(output / 'native.json', live_spans)
    exporter = LocalOTelExporter(output / 'otel.json')
    provider = TracerProvider(resource=Resource.create({
        'service.name': 'refund-eligibility-native',
        'telemetry.capture': 'openinference-runtime-bridge'}))
    provider.add_span_processor(SourceAnnotations(ids, live_spans))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'pipeline'))
    from transport import configure_otlp
    configure_otlp(provider, output)
    # Remove the remote SDK exporter, retain native exports, then append bridge.
    set_trace_processors([capture])
    OpenAIAgentsInstrumentor().instrument(
        tracer_provider=provider, exclusive_processor=False)
    return capture, exporter, provider
