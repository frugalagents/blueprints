"""Loopback-only runner and completed-span polling API."""
import json
import re
import subprocess
import sys
import threading
import uuid
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from telemetry_contract import CONTRACT_VERSION, trajectory
from handoff_contract import validate_request, correlation_result
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace import get_current_span

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "pipeline"))
from store import TraceStore
from runtime import APP, DATA, capture_dir, ingest_token, collector_health
from verification import compare
from consolidation import topology, service
from audit import captures
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

ARCHIVES = {"eligible_final": "Eligible — saved", "denied_final": "Denied — saved",
            "db_error_final": "DB rollback — saved"}


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Runs:
    def __init__(self, root=ROOT, native=False, python=sys.executable):
        self.lock = threading.Lock()
        self.jobs = {}
        self.token = uuid.uuid4().hex
        self.root = root
        self.native = native
        self.python = python
        self.archives = ({"eligible-v1": "Eligible — native",
                          "denied-v1": "Denied — native",
                          "tool-error-v1": "Policy tool error — native"}
                         if native else ARCHIVES)

    @property
    def evidence(self):
        if self.root.parent == APP:
            return capture_dir(self.root.name)
        return self.root / "evidence"

    def active(self):
        with self.lock:
            return next((key for key, job in self.jobs.items()
                         if job["status"] == "running"), None)

    def start(self, scenario, session_id):
        if scenario not in ("eligible", "denied", "tool_error" if self.native else "db_error"):
            raise ValueError("Unknown scenario")
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
            raise ValueError("Session ID must be 1–80 letters, digits, hyphens or underscores")
        with self.lock:
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise RuntimeError("An agent run is already active")
            key = ("native-" if self.native else "live-") + uuid.uuid4().hex[:16]
            self.jobs[key] = {"status": "running", "scenario": scenario,
                              "session_id": session_id,
                              "label": f"{scenario} — {key}"}
        threading.Thread(target=self.execute, args=(key, scenario, session_id), daemon=True).start()
        return key

    def execute(self, key, scenario, session_id):
        try:
            with (self.evidence / (key + ".log")).open("w") as log:
                process = subprocess.Popen(
                    [self.python, "-u", str(self.root / (
                        "eligibility_agent.py" if self.native else "refund_agent.py")),
                     "--mode", "bedrock", "--scenario", scenario,
                     "--session-id", session_id, "--run-id", key,
                     "--output", str(self.evidence / key)],
                    stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code = 124
            with self.lock:
                self.jobs[key].update(status="complete" if code == 0 else "error",
                                      returncode=code)
        except Exception as error:
            with self.lock:
                self.jobs[key].update(status="error", failure=type(error).__name__)

    def snapshot(self, key):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key):
            raise ValueError("Invalid run ID")
        directory = self.evidence / key
        with self.lock:
            job = dict(self.jobs.get(key, {"status": "saved", "label": key}))
        if not directory.is_dir() and key not in self.jobs:
            raise FileNotFoundError("Run not found")
        run = read_json(directory / "run.json", None)
        if self.native:
            return {"id": key, "job": job, "run": run,
                    "otel": read_json(directory / "otel.json", None),
                    "native": read_json(directory / "native.json",
                                       {"traces": [], "spans": [], "active_spans": []})}
        spans = read_json(directory / "spans.json", [])
        return {"id": key, "job": job, "run": run, "spans": spans,
                "capture": read_json(directory / "capture.json", None),
                "contract_version": CONTRACT_VERSION,
                "source_metadata_available": bool(spans) and all(
                    span.get("instrumentation_scope") for span in spans),
                "trajectory": trajectory(spans)}

    def history(self):
        names = [key for key in self.archives if (self.evidence / key).is_dir()]
        names += sorted((p.name for p in (self.evidence).glob(
                         "native-*" if self.native else "live-*")
                         if p.is_dir()), reverse=True)
        items = []
        for key in names:
            run = read_json(self.evidence / key / "run.json", {})
            spans = (read_json(self.evidence / key / "native.json", {}).get("spans", [])
                     if self.native else read_json(self.evidence / key / "spans.json", []))
            items.append({"id": key, "label": self.archives.get(key, key),
                          "session_id": run.get("session_id"),
                          "task_id": run.get("task_id"),
                          "scenario": run.get("scenario"),
                          "model_mode": run.get("model_mode"),
                          "has_otel_bridge": bool(run.get("otel_instrumentation_installed")) if self.native else False,
                          "start_time": min((span.get("started_at" if self.native else "start_time", "")
                                             for span in spans), default=""),
                          "business_outcome": run.get("outcome", {}).get("actual"),
                          "contract_version": run.get("contract_version")})
        return items


class Handler(SimpleHTTPRequestHandler):
    runs = Runs()
    native_runs = Runs(ROOT.parent / "agent2", native=True,
                       python=str(ROOT.parents[1] / ".runtime/agent2/.venv/bin/python"))
    handoff_token = uuid.uuid4().hex
    trace_store = TraceStore(DATA / "backend" / "store.sqlite")
    collector_token = ingest_token()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/agent2.html", "/agent2.js", "/connected.html", "/connected.js"):
                file = ROOT.parent / ("connected" if path.startswith("/connected") else "agent2") / (
                    "inspection.html" if path.endswith(".html") else "inspection.js")
                body = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if path.endswith(".html")
                                 else "text/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/observability/workflows":
                return self.send_json(self.trace_store.workflows())
            if path.startswith("/api/observability/workflows/"):
                workflow = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", workflow):
                    raise ValueError("Invalid workflow identifier")
                records = self.trace_store.consolidated_workflow(workflow)
                if not records:
                    raise FileNotFoundError("No stored telemetry for workflow")
                audit = captures(ROOT.parents[1], ROOT.parent / "pipeline/audit_sources.json",
                                 workflow, {s["context"]["trace_id"] for s in records})
                return self.send_json({"workflow_id": workflow, "spans": records,
                    "topology": topology(records), "audit": audit,
                    "preservation": compare(audit["spans"], records) if audit["spans"] else None,
                    "collector_healthy": collector_health()})
            if path == "/api/pipeline/status":
                return self.send_json({"collector_healthy": collector_health(),
                    "enabled": bool(os.environ.get("OBSERVABILITY_OTLP_ENDPOINT")),
                    **self.trace_store.status()})
            if path.startswith("/api/connected/"):
                route = path.removeprefix("/api/connected")
                if route == "/config":
                    return self.send_json({"token": self.connected_runs.token,
                                           "active": self.connected_runs.active(), "pipeline_enabled": bool(os.environ.get("OBSERVABILITY_OTLP_ENDPOINT"))})
                if route == "/history":
                    return self.send_json(self.connected_runs.history())
                if route.startswith("/runs/"):
                    return self.send_json(self.connected_runs.snapshot(route.split("/")[-1], parse_qs(urlparse(self.path).query).get("source", ["collector"])[0]))
                return self.send_json({"error": "Not found"}, 404)
            if path.startswith("/api/agent2/"):
                route = path.removeprefix("/api/agent2")
                if route == "/config":
                    return self.send_json({"token": self.native_runs.token,
                                           "active": self.native_runs.active()})
                if route == "/history":
                    return self.send_json(self.native_runs.history())
                if route.startswith("/runs/"):
                    return self.send_json(self.native_runs.snapshot(route.split("/")[-1]))
                return self.send_json({"error": "Not found"}, 404)
            if path == "/api/config":
                return self.send_json({"token": self.runs.token, "active": self.runs.active()})
            if path == "/api/history":
                return self.send_json(self.runs.history())
            if path.startswith("/api/runs/"):
                return self.send_json(self.runs.snapshot(path.split("/")[-1]))
            if path.startswith("/api/"):
                return self.send_json({"error": "Not found"}, 404)
            if path == "/":
                self.path = "/overview.html"
            super().do_GET()
        except (ValueError, FileNotFoundError) as error:
            self.send_json({"error": str(error)}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.server.server_port}",
                   f"http://localhost:{self.server.server_port}"}
        if origin and origin not in allowed:
            return self.send_json({"error": "Origin rejected"}, 403)
        if self.path == "/internal/otlp/v1/traces":
            if self.headers.get("X-Collector-Token") != self.collector_token:
                return self.send_json({"error": "Invalid Collector token"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError("Invalid OTLP request size")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/x-protobuf":
                    raise ValueError("Expected OTLP protobuf")
                self.trace_store.ingest(self.rfile.read(length))
                body = ExportTraceServiceResponse().SerializeToString()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as error:
                return self.send_json({"error": "Invalid OTLP payload: " + type(error).__name__}, 400)
        if self.path == "/internal/eligibility":
            return self.receive_handoff()
        runs = (self.connected_runs if self.path == "/api/connected/runs" else
                self.native_runs if self.path == "/api/agent2/runs" else self.runs)
        if self.path not in ("/api/runs", "/api/agent2/runs", "/api/connected/runs") or self.headers.get("X-Run-Token") != runs.token:
            return self.send_json({"error": "Invalid run request"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"scenario", "session_id"}:
                raise ValueError("Provide scenario and session_id")
            if not isinstance(data["scenario"], str):
                raise ValueError("Invalid scenario")
            self.send_json({"id": runs.start(data["scenario"], data["session_id"])}, 202)
        except (ValueError, TypeError) as error:
            self.send_json({"error": str(error)}, 400)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, 409)

    def log_message(self, *args):
        pass

    def receive_handoff(self):
        if self.headers.get("X-Agent-Token") != self.handoff_token:
            return self.send_json({"error": "Invalid local agent token"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid handoff size")
            data = validate_request(json.loads(self.rfile.read(length)))
            caller = data["caller_run_id"]
            with self.connected_runs.lock:
                if self.connected_runs.jobs.get(caller, {}).get("status") != "running":
                    raise ValueError("Caller is not an active connected workflow")
            headers = {k: self.headers[k] for k in ("traceparent", "tracestate")
                       if self.headers.get(k)}
            remote = TraceContextTextMapPropagator().extract(headers)
            if not get_current_span(remote).get_span_context().is_valid:
                raise ValueError("Missing or invalid W3C trace context")
            directory = self.connected_runs.evidence / caller
            inbound_file = directory / "inbound-agent-b.json"
            if inbound_file.exists():
                raise ValueError("Duplicate handoff attempt; automatic retries are disabled")
            inbound_file.write_text(json.dumps({**data, "trace_headers": headers}, indent=2))
            output = directory / "agent-b"
            scenario = "eligible" if data["scenario"] == "db_error" else data["scenario"]
            with (directory / "agent-b.log").open("w") as log:
                process = subprocess.Popen([
                    self.native_runs.python, "-u",
                    str(ROOT.parent / "agent2" / "eligibility_agent.py"),
                    "--scenario", scenario, "--mode", data["model_mode"],
                    "--run-id", "b-" + uuid.uuid4().hex[:16],
                    "--inbound-request", str(inbound_file),
                    "--output", str(output)], stdout=log, stderr=subprocess.STDOUT)
                try:
                    process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    return self.send_json({"error": "Agent B worker deadline exceeded"}, 504)
            report = read_json(output / "run.json", None)
            if not report:
                return self.send_json({"error": "Agent B failed before final evidence"}, 502)
            code = (504 if scenario == "timeout" else
                    200 if report["verification_status"] == "passed" and report["decision"] else 502)
            return self.send_json(report, code)
        except (ValueError, TypeError, KeyError) as error:
            return self.send_json({"error": str(error)}, 400)


class ConnectedRuns(Runs):
    def __init__(self):
        super().__init__(ROOT.parent / "connected")
        self.archives = {}
        self.mode = "bedrock"

    def start(self, scenario, session_id):
        if scenario not in ("eligible", "denied", "tool_error", "timeout", "db_error"):
            raise ValueError("Unknown scenario")
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
            raise ValueError("Invalid session ID")
        with self.lock:
            if self.active_unlocked():
                raise RuntimeError("A connected workflow is already active")
            key = "flow-" + uuid.uuid4().hex[:16]
            self.jobs[key] = {"status": "running", "scenario": scenario,
                              "session_id": session_id}
        threading.Thread(target=self.execute, args=(key, scenario, session_id), daemon=True).start()
        return key

    def active_unlocked(self):
        return any(job["status"] == "running" for job in self.jobs.values())

    def execute(self, key, scenario, session_id):
        try:
            env = {**os.environ, "AGENT2_HANDOFF_TOKEN": Handler.handoff_token}
            with (self.evidence / (key + ".log")).open("w") as log:
                process = subprocess.Popen([
                    self.python, "-u", str(ROOT / "refund_agent.py"),
                    "--mode", self.mode, "--scenario", scenario,
                    "--session-id", session_id, "--run-id", key,
                    "--workflow-id", "workflow-" + key,
                    "--agent2-endpoint", f"http://127.0.0.1:{self.port}/internal/eligibility",
                    "--output", str(self.evidence / key)],
                    env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=360)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code = 124
            with self.lock:
                self.jobs[key].update(status="complete" if code == 0 else "error",
                                      returncode=code)
        except Exception as error:
            with self.lock:
                self.jobs[key].update(status="error", failure=type(error).__name__)

    def snapshot(self, key, source="source"):
        if source not in ("source", "collector"):
            raise ValueError("Unknown telemetry source")
        first = super().snapshot(key)
        directory = self.evidence / key / "agent-b"
        raw_b = read_json(directory / "otel.json", [])
        b = {"run": read_json(directory / "run.json", None),
             "spans": raw_b, "semantic_extensions_applied": False,
             "raw_bridge_spans": raw_b,
             "native": read_json(directory / "native.json", {})}
        expected = first["spans"] + raw_b
        stored = Handler.trace_store.workflow("workflow-" + key)
        a_spans = first["spans"]
        if source == "collector":
            a_spans = [s for s in stored if service(s) in {service(item) for item in first["spans"]}]
            b["spans"] = [s for s in stored if service(s) in {service(item) for item in raw_b}]
        return {"id": key, "job": first["job"], "data_source": source,
                "agent_a": {"run": first["run"], "spans": a_spans}, "agent_b": b,
                "pipeline": {"collector_healthy": collector_health(),
                    "enabled_for_run": bool((first["run"] or {}).get("pipeline_enabled")),
                    "verification": compare(expected, stored),
                    "export_a": read_json(directory.parent / "otlp-export.json", None),
                    "export_b": read_json(directory / "otlp-export.json", None)},
                "handoff": read_json(self.evidence / key / "handoff.json", None),
                "correlation": correlation_result(a_spans, b["spans"])}

    def history(self):
        result = []
        for directory in (self.evidence).glob("flow-*"):
            if directory.is_dir():
                report = read_json(directory / "run.json", {})
                spans = read_json(directory / "spans.json", [])
                result.append({"id": directory.name, "scenario": report.get("scenario"),
                               "session_id": report.get("session_id"),
                               "start_time": min((s["start_time"] for s in spans), default=""),
                               "outcome": report.get("business_outcome", "incomplete")})
        return sorted(result, key=lambda item: item["start_time"])


Handler.connected_runs = ConnectedRuns()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PIPELINE_APP_PORT", "8765"))), Handler)
    Handler.connected_runs.port = server.server_port
    print(f"Agent runner: http://127.0.0.1:{server.server_port}/inspection.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
