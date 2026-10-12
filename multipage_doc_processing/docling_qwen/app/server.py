"""Local payslip review app; no web framework dependencies."""
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / "work" / "docling-ui-jobs"
DATA.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()
JOBS = {}


def read_json(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def register_sample():
    folder = DATA / "sample"
    folder.mkdir(exist_ok=True)
    shutil.copy(ROOT / "synthetic-payslips.pdf", folder / "input.pdf")
    if not (folder / "results").exists():
        shutil.copytree(ROOT / "results", folder / "results")
    JOBS["sample"] = {"id": "sample", "name": "Synthetic payslips · 10 pages",
                      "status": "done", "page_count": 10, "error": None}


def process(job_id, groups=None):
    folder = DATA / job_id
    output = folder / f"run-{uuid.uuid4().hex[:8]}"
    with LOCK:
        JOBS[job_id].update(status="running", output=str(output), error=None)
    command = [sys.executable, str(ROOT / "pipeline.py"), str(folder / "input.pdf"),
               "--output", str(output)]
    if job_id == "sample":
        command += ["--no-ocr"]
    if groups is not None:
        groups_path = folder / "reviewed-groups.json"
        groups_path.write_text(json.dumps(groups))
        command += ["--groups", str(groups_path)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        with LOCK:
            JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "error"
            JOBS[job_id]["error"] = proc.stderr[-2000:] if proc.returncode else None
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="error", error=str(exc))


def snapshot(job_id):
    job = dict(JOBS[job_id])
    folder = DATA / job_id
    output = Path(job.get("output", folder / "results"))
    job["records"] = read_json(output / "records.json", [])
    job["progress"] = read_json(output / "progress.json", {})
    job["boundaries"] = read_json(output / "boundaries.json", [])
    job["events"] = read_json(output / "events.json", [])
    job["groups"] = read_json(output / "groups.json", read_json(output / "provisional-groups.json", []))
    if job["status"] == "running" and not job["groups"]:
        job["groups"] = read_json(folder / "reviewed-groups.json", [])
    if job["records"]:
        job["groups"] = [r["source_pages"] for r in job["records"]]
    job.pop("output", None)
    return job


class Handler(BaseHTTPRequestHandler):
    def respond(self, data, mime="application/json", status=200):
        payload = json.dumps(data).encode() if mime == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        if parsed.path == "/":
            return self.respond((Path(__file__).parent / "index.html").read_bytes(), "text/html; charset=utf-8")
        job_id = q.get("job", ["sample"])[0]
        if job_id not in JOBS:
            return self.respond({"error": "Job not found"}, status=404)
        if parsed.path == "/api/job":
            return self.respond(snapshot(job_id))
        if parsed.path == "/api/pdf":
            return self.respond((DATA / job_id / "input.pdf").read_bytes(), "application/pdf")
        if parsed.path == "/api/page":
            try:
                number = int(q.get("page", ["1"])[0])
                if number < 1 or number > JOBS[job_id]["page_count"]:
                    raise ValueError("Invalid page")
                image = DATA / job_id / f"page-{number}.png"
                with LOCK:
                    if not image.exists():
                        import pypdfium2 as pdfium
                        pdf = pdfium.PdfDocument(DATA / job_id / "input.pdf")
                        pdf[number-1].render(scale=1.5).to_pil().save(image)
                        pdf.close()
                return self.respond(image.read_bytes(), "image/png")
            except (ValueError, IndexError):
                return self.respond({"error": "Invalid page"}, status=400)
        return self.respond({"error": "Not found"}, status=404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.respond({"error": "Origin rejected"}, status=403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 50 * 1024 * 1024:
                raise ValueError("Upload must be under 50 MB")
            body = self.rfile.read(length)
            if self.path == "/api/upload":
                if not body.startswith(b"%PDF-"):
                    raise ValueError("Please choose a PDF")
                job_id = uuid.uuid4().hex
                folder = DATA / job_id
                folder.mkdir()
                path = folder / "input.pdf"
                path.write_bytes(body)
                import pypdfium2 as pdfium
                with LOCK:
                    pdf = pdfium.PdfDocument(path)
                    count = len(pdf)
                    pdf.close()
                if not count:
                    raise ValueError("PDF has no pages")
                JOBS[job_id] = dict(id=job_id, name="Uploaded payslips", page_count=count,
                                   status="ready", error=None)
                return self.respond(snapshot(job_id))
            data = json.loads(body)
            job_id = data["job"]
            if job_id not in JOBS:
                raise ValueError("Job not found")
            if self.path == "/api/process":
                groups = data.get("groups")
                if groups is not None:
                    if not isinstance(groups, list) or not all(isinstance(g, list) and g for g in groups):
                        raise ValueError("Invalid groups")
                    if [p for g in groups for p in g] != list(range(1, JOBS[job_id]["page_count"]+1)):
                        raise ValueError("Groups must cover all pages exactly once in order")
                with LOCK:
                    if JOBS[job_id]["status"] == "running":
                        raise ValueError("Processing is already running")
                    JOBS[job_id]["status"] = "running"
                threading.Thread(target=process, args=(job_id, groups), daemon=True).start()
                return self.respond({"status": "running"})
            raise ValueError("Unknown action")
        except Exception as exc:
            self.respond({"error": str(exc)}, status=400)


def main():
    register_sample()
    port = int(os.environ.get("PAYSLIP_PORT", "8766"))
    print(f"Payslip review app: http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
