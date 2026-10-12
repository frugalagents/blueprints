"""Deterministic stub data for the Phase-6 operator endpoints (task 3.2).

Each builder returns a renderable, well-typed shape mirroring the Jane Doe /
NVDA demo (design.md §4/§5). These are LOCAL/STUB fixtures: in Phase 6 the
endpoints that serve them are replaced with real AWS reads
(CloudWatch GenAI Observability, Guardrail logs, bedrock-agentcore Registry,
Evaluations). The field names mirror the real data model so that swap is a
drop-in. Nothing here touches AWS or the network.

Registry governance state is held in a module-level dict so the STUB approve
POST can flip a record from PENDING_APPROVAL to APPROVED within a running
process (reset on restart — acceptable for a demo stub).
"""

from __future__ import annotations

from typing import Dict, List

from .schemas import (
    EvalRunResponse,
    EvalScoreCard,
    EvalTrendPoint,
    EvaluationsResponse,
    GuardrailEvent,
    GuardrailListResponse,
    RegistryListResponse,
    RegistryRecord,
    TraceDetail,
    TraceListResponse,
    TraceSpan,
    TraceSummary,
)

# --------------------------------------------------------------------------- #
# Traces  # STUB (Phase 6: CloudWatch GenAI Observability span data)
# --------------------------------------------------------------------------- #

_TRACE_ID = "trace-demo-1"


def _demo_spans() -> List[TraceSpan]:
    """Span waterfall for the Jane Doe turn: model + the three tool calls."""
    return [
        TraceSpan(span_id="s1", name="agent.invoke", kind="model",
                  start_ms=0, duration_ms=5200, status="OK", tokens=1840),
        TraceSpan(span_id="s2", name="get_portfolio(C-1001)", kind="tool",
                  start_ms=620, duration_ms=180, status="OK"),
        TraceSpan(span_id="s3", name="get_quote(NVDA)", kind="tool",
                  start_ms=1950, duration_ms=140, status="OK"),
        TraceSpan(span_id="s4", name="list_research(NVDA)", kind="tool",
                  start_ms=3100, duration_ms=210, status="OK"),
        TraceSpan(span_id="s5", name="guardrail.apply", kind="guardrail",
                  start_ms=4800, duration_ms=120, status="OK"),
    ]


def trace_list() -> TraceListResponse:
    return TraceListResponse(traces=[
        TraceSummary(trace_id=_TRACE_ID, session_id="demo-1",
                     started_at="2026-10-10T15:00:00Z", duration_ms=5200,
                     span_count=5, status="OK"),
    ])


def trace_detail(trace_id: str) -> TraceDetail:
    return TraceDetail(
        trace_id=trace_id, session_id="demo-1",
        started_at="2026-10-10T15:00:00Z", duration_ms=5200, status="OK",
        spans=_demo_spans(),
    )


# --------------------------------------------------------------------------- #
# Guardrails  # STUB (Phase 6: Guardrail trace + CloudWatch logs)
# --------------------------------------------------------------------------- #

def guardrail_events(guardrail_version: str) -> GuardrailListResponse:
    return GuardrailListResponse(
        guardrail_version=guardrail_version,
        events=[
            GuardrailEvent(
                timestamp="2026-10-10T15:00:03Z", rule_type="DENIED_TOPIC",
                masked_snippet="...can you guarantee a [MASKED] return...",
                action="BLOCKED"),
            GuardrailEvent(
                timestamp="2026-10-10T15:01:12Z", rule_type="PII",
                masked_snippet="client SSN ***-**-****",
                action="MASKED"),
            GuardrailEvent(
                timestamp="2026-10-10T15:02:40Z", rule_type="GROUNDING",
                masked_snippet="price target 999 (unsupported by R-5567)",
                action="FLAGGED"),
        ],
    )


# --------------------------------------------------------------------------- #
# Registry  # STUB (Phase 6: bedrock-agentcore Registry records + governance)
# --------------------------------------------------------------------------- #

# Mutable governance state so the STUB approve POST can transition a record.
_REGISTRY_STATE: Dict[str, str] = {
    "agent-advisor-copilot": "APPROVED",
    "tool-portfolio-api": "APPROVED",
    "tool-market-api": "PENDING_APPROVAL",
    "tool-research-api": "DRAFT",
}

_REGISTRY_META = {
    "agent-advisor-copilot": ("Advisor Copilot Agent", "agent"),
    "tool-portfolio-api": ("Portfolio API", "tool"),
    "tool-market-api": ("Market Data API", "tool"),
    "tool-research-api": ("Research Notes API", "tool"),
}


def registry_records() -> RegistryListResponse:
    records = []
    for rid, (name, rtype) in _REGISTRY_META.items():
        records.append(RegistryRecord(
            id=rid, name=name, type=rtype,
            governance_state=_REGISTRY_STATE[rid],  # type: ignore[arg-type]
        ))
    return RegistryListResponse(records=records)


def registry_approve(record_id: str) -> RegistryRecord | None:
    """Flip a stub record to APPROVED. Returns None for an unknown id."""
    if record_id not in _REGISTRY_META:
        return None
    _REGISTRY_STATE[record_id] = "APPROVED"
    name, rtype = _REGISTRY_META[record_id]
    return RegistryRecord(id=record_id, name=name, type=rtype,
                          governance_state="APPROVED")


# --------------------------------------------------------------------------- #
# Evaluations  # STUB (Phase 6: Evaluations service + CloudWatch metrics)
# --------------------------------------------------------------------------- #

def evaluations() -> EvaluationsResponse:
    return EvaluationsResponse(
        score_cards=[
            EvalScoreCard(metric="Faithfulness", score=0.92, sample_size=40),
            EvalScoreCard(metric="Harmfulness", score=0.02, sample_size=40),
            EvalScoreCard(metric="Correctness", score=0.88, sample_size=40),
            EvalScoreCard(metric="ToolSelectionAccuracy", score=0.95, sample_size=40),
        ],
        trend=[
            EvalTrendPoint(date="2026-10-06", Faithfulness=0.86, Harmfulness=0.05,
                           Correctness=0.80, ToolSelectionAccuracy=0.90),
            EvalTrendPoint(date="2026-10-08", Faithfulness=0.89, Harmfulness=0.03,
                           Correctness=0.85, ToolSelectionAccuracy=0.93),
            EvalTrendPoint(date="2026-10-10", Faithfulness=0.92, Harmfulness=0.02,
                           Correctness=0.88, ToolSelectionAccuracy=0.95),
        ],
    )


def evaluations_run() -> EvalRunResponse:
    return EvalRunResponse(
        batch_id="eval-batch-demo-1", status="STARTED", seed_set_size=12,
        message="On-demand batch evaluation started over the seed advisor-prompt set.",
    )
