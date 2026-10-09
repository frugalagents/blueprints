"""Preserve SDK-native exports locally; no OTel conversion or remote exporter."""
import json
import threading
from datetime import datetime, timezone
from agents.tracing import TracingProcessor


class NativeCapture(TracingProcessor):
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.traces = {}
        self.spans = {}
        self.active = {}
        self.lifecycle = []
        self.failures = []

    def record(self, kind, item):
        with self.lock:
            try:
                exported = item.export()
                if exported is None:
                    return
                identity = exported['id']
                if kind.startswith('trace'):
                    self.traces[identity] = exported
                elif kind == 'span_start':
                    self.active[identity] = exported
                else:
                    self.active.pop(identity, None)
                    self.spans[identity] = exported
                self.lifecycle.append({'event': kind, 'id': identity,
                                       'observed_at': datetime.now(timezone.utc).isoformat()})
                self.write()
            except Exception as error:
                self.failures.append(type(error).__name__)

    def write(self):
        data = {'format': 'openai-agents-native-v1',
                'traces': list(self.traces.values()), 'spans': list(self.spans.values()),
                'active_spans': list(self.active.values()), 'lifecycle': self.lifecycle,
                'capture_errors': self.failures}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(self.path)

    def on_trace_start(self, item): self.record('trace_start', item)
    def on_trace_end(self, item): self.record('trace_end', item)
    def on_span_start(self, item): self.record('span_start', item)
    def on_span_end(self, item): self.record('span_end', item)
    def force_flush(self):
        with self.lock: self.write()
    def shutdown(self): self.force_flush()
