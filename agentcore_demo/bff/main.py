"""FastAPI Backend-for-UI (BFF) for the Advisor Copilot POC (tasks 3.1–3.2).

The only component the React UI talks to; AWS credentials stay server-side.
Endpoints follow design.md §7.

REAL now (local — the only AWS traffic is the agent's own Bedrock calls):
  POST /api/chat            -> SSE stream relayed from the chat backend seam
  GET  /api/health          -> status-banner data, truthful in local mode
  GET  /api/sessions        -> live memory sessions (agent.memory.SESSIONS)
  GET  /api/sessions/{id}   -> live memory events for a session

REAL now via AWS reads (bff/aws_reads.py), wired in Phase 6 (6.WIRE):
  GET  /api/traces[/{id}]   -> CloudWatch GenAI Observability spans (aws/spans)
  GET  /api/guardrails      -> AWS/Bedrock/Guardrails intervention metrics
  GET  /api/registry        -> agent-registry-control records + governance state
  POST /api/registry/{id}/approve -> Submit(if DRAFT)+UpdateRegistryRecordStatus
  GET  /api/evaluations     -> Bedrock-AgentCore/Evaluations metric scores + trend
  POST /api/evaluations/run -> bedrock-agentcore StartBatchEvaluation (seed set)

All operator reads resolve resource ids from the gitignored infra/outputs.json
(env override) via bff/aws_reads.py — never hardcoded here. AWS credentials stay
server-side (boto3 ambient creds).

Run locally (from the repo root, so `agent` and `bff` are importable):

    cd agentcore_demo
    AWS_REGION=us-east-1 .venv/bin/python -m uvicorn bff.main:app --port 8000
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.config import load_config
from agent.memory import SESSIONS

from . import aws_reads, policy_log
from .aws_reads import MissingResourceId
from .backends import get_chat_backend
from .schemas import (
    EvalRunResponse,
    EvaluationsResponse,
    GuardrailListResponse,
    HealthResponse,
    PolicyResponse,
    RegistryListResponse,
    RegistryRecord,
    SessionDetail,
    SessionEvent,
    SessionListResponse,
    SessionSummary,
    TraceDetail,
    TraceListResponse,
)

app = FastAPI(title="Advisor Copilot — BFF", version="0.3.0")

# CORS: the Vite dev server (ui/vite.config.ts) runs on :5173 and calls the BFF
# cross-origin. Keep it open for localhost dev so SSE + JSON work in the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    """POST /api/chat body."""

    sessionId: str
    message: str


# --------------------------------------------------------------------------- #
# REAL — health banner (design.md §5).
# --------------------------------------------------------------------------- #

@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Status-banner data, truthful about the deployed AgentCore infra.

    Values come from ``load_config()`` (environment / gitignored .env) — never
    hard-coded. When a Runtime endpoint is configured (INFRA mode) the real
    endpoint status is read from ``bedrock-agentcore-control`` and reported
    truthfully (``READY``/``CREATING``/etc. — not a hard-coded "deployed"); in
    local mode the banner still reports ``local`` / ``not-deployed``.
    """
    cfg = load_config()
    runtime_status = "local"
    gateway_status = "not-deployed"

    if cfg.runtime_endpoint_arn:
        runtime_status = _real_runtime_status(cfg)
    if cfg.gateway_mcp_url:
        # Truthful control-plane status (READY/CREATING/...), not a hard-coded
        # "deployed". Falls back to not-deployed when no gateway is configured.
        gateway_status = aws_reads.gateway_status(None)

    # Policy (CAP-H): real engine status, or local/none when unconfigured.
    policy_status = aws_reads.policy_status()

    return HealthResponse(
        runtime=runtime_status,
        gateway=gateway_status,
        guardrail=cfg.guardrail_version or "local/none",
        policy=policy_status,
        model=cfg.bedrock_model_id,
    )


def _real_runtime_status(cfg) -> str:
    """Read the deployed runtime endpoint status from the control plane.

    Parses the runtime id + endpoint name out of the configured ARN and calls
    ``get_agent_runtime_endpoint``. Returns the real status string, or an
    ``unknown:<reason>`` marker on failure (never fabricates ``READY``).
    """
    import re

    import boto3

    arn = cfg.runtime_endpoint_arn
    # arn:...:runtime/<runtime_id>/runtime-endpoint/<endpoint_name>
    m = re.search(r":runtime/([^/]+)(?:/runtime-endpoint/([^/]+))?$", arn)
    if not m:
        return "unknown:unparseable-arn"
    runtime_id, endpoint_name = m.group(1), m.group(2) or "DEFAULT"
    try:
        agc = boto3.client("bedrock-agentcore-control", region_name=cfg.aws_region)
        resp = agc.get_agent_runtime_endpoint(
            agentRuntimeId=runtime_id, endpointName=endpoint_name
        )
        return resp.get("status", "unknown")
    except Exception as exc:  # noqa: BLE001 - report truthfully, don't crash health
        return f"unknown:{type(exc).__name__}"


# --------------------------------------------------------------------------- #
# REAL — chat (SSE relay through the backend seam).
# --------------------------------------------------------------------------- #

@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """Relay the chat backend's normalized events to the UI as SSE frames.

    Uses the same frame shape as ``agent/app.py`` (event: <type>, data: json),
    including the terminal ``done`` and an ``error`` frame on exception. The
    route is backend-agnostic: Phase 5 swaps ``get_chat_backend()`` for the
    deployed AgentCore Runtime without changing this contract or the UI.
    """
    backend = get_chat_backend()

    async def event_source():
        try:
            async for ev in backend.stream(req.sessionId, req.message):
                etype = ev.get("type")
                if etype == "token":
                    data = {"text": ev.get("text", "")}
                elif etype == "tool_start":
                    data = {"toolUseId": ev.get("toolUseId"), "name": ev.get("name")}
                elif etype == "tool_stop":
                    data = {
                        "toolUseId": ev.get("toolUseId"),
                        "name": ev.get("name"),
                        "status": ev.get("status"),
                        "result": ev.get("result"),
                    }
                elif etype == "policy":
                    decision = ev.get("decision")
                    principal = ev.get("principal")
                    resource = ev.get("resource")
                    data = {
                        "decision": decision,
                        "principal": principal,
                        "resource": resource,
                    }
                    # Record the REAL decision into the in-process log that
                    # GET /api/policy reads back (genuine, not fabricated).
                    policy_log.record(
                        str(decision), str(principal or ""), str(resource or "")
                    )
                elif etype == "done":
                    data = {
                        "stop_reason": ev.get("stop_reason"),
                        "message": ev.get("message", ""),
                    }
                elif etype == "error":
                    data = {"message": ev.get("message", "")}
                else:
                    continue
                yield {"event": etype, "data": json.dumps(data)}
        except Exception as exc:  # surface stream failures as an error frame
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_source())


# --------------------------------------------------------------------------- #
# REAL — memory-backed sessions (agent.memory.SESSIONS, same store chat writes).
# --------------------------------------------------------------------------- #

@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    """List live memory sessions and their turn counts."""
    summaries = [
        SessionSummary(id=sid, turn_count=len(SESSIONS.get_history(sid)))
        for sid in SESSIONS.list_sessions()
    ]
    return SessionListResponse(sessions=summaries)


@app.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
    """Return memory events for a session.

    Returns an empty event list for an unknown id rather than 404 — the UI
    tolerates an empty list and this keeps the session selector simple.
    """
    history = SESSIONS.get_history(session_id)
    events = [SessionEvent(role=t.get("role", ""), content=t.get("content", ""))
              for t in history]
    return SessionDetail(id=session_id, events=events)


# --------------------------------------------------------------------------- #
# REAL (Phase 6: CloudWatch GenAI Observability spans) — traces.
# --------------------------------------------------------------------------- #

@app.get("/api/traces", response_model=TraceListResponse)
async def traces(limit: int = 20) -> TraceListResponse:
    """Recent traces for the deployed runtime from CloudWatch GenAI Observability.

    Reads OTEL spans from the ``aws/spans`` log group (bff/aws_reads.py), groups
    them by traceId, and builds TraceSummary rows. The boto3 reads are blocking,
    so they run in a worker thread to keep the event loop free.
    """
    return await run_in_threadpool(aws_reads.trace_list, limit)


@app.get("/api/traces/{trace_id}", response_model=TraceDetail)
async def trace_detail(trace_id: str) -> TraceDetail:
    """Full span waterfall for one trace from CloudWatch GenAI Observability."""
    detail = await run_in_threadpool(aws_reads.trace_detail, trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown trace: {trace_id}")
    return detail


# --------------------------------------------------------------------------- #
# REAL (Phase 6: AWS/Bedrock/Guardrails metrics) — guardrail intervention events.
# --------------------------------------------------------------------------- #

@app.get("/api/guardrails", response_model=GuardrailListResponse)
async def guardrails() -> GuardrailListResponse:
    """Real guardrail interventions for the deployed guardrail.

    Built from CloudWatch ``AWS/Bedrock/Guardrails`` intervention metrics for the
    configured guardrail arn+version (no client text / PII is read — metrics
    carry counts + policy types only), mapping real Bedrock policy types to the
    fixed UI rule_type enum.
    """
    cfg = load_config()
    version = cfg.guardrail_version or "local/none"
    return await run_in_threadpool(aws_reads.guardrail_events, version)


# --------------------------------------------------------------------------- #
# REAL (AgentCore Policy — CAP-H) — Cedar ownership rule + recent decisions.
# DISTINCT from Guardrails (CAP-A): authorization vs content filtering.
# --------------------------------------------------------------------------- #

@app.get("/api/policy", response_model=PolicyResponse)
async def policy() -> PolicyResponse:
    """Policy summary (engine/policy status, Cedar ownership rule) + decision log.

    Statuses + enforcement mode are read live from the control plane; the rule is
    read from the gitignored infra outputs; the decision log is the genuine
    stream of ALLOWED/DENIED decisions recorded by the chat relay this process
    saw (policy_log) — real, not fabricated.
    """
    return await run_in_threadpool(aws_reads.policy_summary)


# --------------------------------------------------------------------------- #
# REAL (Phase 6: agent-registry-control) — catalog records + approval.
# --------------------------------------------------------------------------- #

@app.get("/api/registry", response_model=RegistryListResponse)
async def registry() -> RegistryListResponse:
    """Live registry records (recordType AGENT->agent, MCP->tool; status->state)."""
    return await run_in_threadpool(aws_reads.registry_records)


@app.post("/api/registry/{record_id}/approve", response_model=RegistryRecord)
async def registry_approve(record_id: str) -> RegistryRecord:
    """Approve a registry record (submit-for-approval if DRAFT, then APPROVED)."""
    record = await run_in_threadpool(aws_reads.registry_approve, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown record: {record_id}")
    return record


# --------------------------------------------------------------------------- #
# REAL (Phase 6: Bedrock-AgentCore/Evaluations metrics + batch) — scores + run.
# --------------------------------------------------------------------------- #

@app.get("/api/evaluations", response_model=EvaluationsResponse)
async def evaluations() -> EvaluationsResponse:
    """Real evaluator scores + daily trend from the Evaluations CW namespace."""
    return await run_in_threadpool(aws_reads.evaluations)


@app.post("/api/evaluations/run", response_model=EvalRunResponse)
async def evaluations_run() -> EvalRunResponse:
    """Start a real on-demand batch evaluation over the curated seed set."""
    try:
        return await run_in_threadpool(aws_reads.evaluations_run)
    except MissingResourceId as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
