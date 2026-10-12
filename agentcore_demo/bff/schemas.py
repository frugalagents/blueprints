"""Pydantic response models for the Advisor Copilot BFF (tasks 3.1–3.2).

REAL endpoints (health, sessions) and the Phase-6 STUB operator endpoints all
return these well-typed shapes so the UI can render them without guessing.
Where a stub field overlaps the mocked data model (design.md §4 — tickers, note
ids), the names mirror it so the Phase-6 swap to real AWS reads is a drop-in.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel

GovernanceState = Literal["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED"]


# --------------------------------------------------------------------------- #
# REAL — health banner (design.md §5) + memory-backed sessions.
# --------------------------------------------------------------------------- #

class HealthResponse(BaseModel):
    """Status-banner data. In local mode these report the truth, not 'deployed'."""

    runtime: str
    gateway: str
    guardrail: str
    policy: str
    model: str


class SessionSummary(BaseModel):
    id: str
    turn_count: int


class SessionListResponse(BaseModel):
    sessions: List[SessionSummary]


class SessionEvent(BaseModel):
    role: str
    content: str


class SessionDetail(BaseModel):
    id: str
    events: List[SessionEvent]


# --------------------------------------------------------------------------- #
# STUB — Traces (span waterfall). Phase 6: CloudWatch GenAI Observability.
# --------------------------------------------------------------------------- #

class TraceSpan(BaseModel):
    span_id: str
    name: str
    kind: Literal["model", "tool", "guardrail"]
    start_ms: int        # offset from trace start
    duration_ms: int
    status: Literal["OK", "ERROR"]
    tokens: Optional[int] = None


class TraceSummary(BaseModel):
    trace_id: str
    session_id: str
    started_at: str
    duration_ms: int
    span_count: int
    status: Literal["OK", "ERROR"]


class TraceDetail(BaseModel):
    trace_id: str
    session_id: str
    started_at: str
    duration_ms: int
    status: Literal["OK", "ERROR"]
    spans: List[TraceSpan]


class TraceListResponse(BaseModel):
    traces: List[TraceSummary]


# --------------------------------------------------------------------------- #
# STUB — Guardrails intervention events. Phase 6: Guardrail trace + logs.
# --------------------------------------------------------------------------- #

class GuardrailEvent(BaseModel):
    timestamp: str
    rule_type: Literal["DENIED_TOPIC", "PII", "WORD_POLICY", "GROUNDING"]
    masked_snippet: str
    action: Literal["BLOCKED", "MASKED", "FLAGGED"]


class GuardrailListResponse(BaseModel):
    events: List[GuardrailEvent]
    guardrail_version: str


# --------------------------------------------------------------------------- #
# STUB — Registry records + approval. Phase 6: bedrock-agentcore Registry.
# --------------------------------------------------------------------------- #

class RegistryRecord(BaseModel):
    id: str
    name: str
    type: Literal["agent", "tool"]
    governance_state: GovernanceState


class RegistryListResponse(BaseModel):
    records: List[RegistryRecord]


# --------------------------------------------------------------------------- #
# STUB — Evaluations score summary + trend. Phase 6: Evaluations + CloudWatch.
# --------------------------------------------------------------------------- #

EvalMetric = Literal["Faithfulness", "Harmfulness", "Correctness", "ToolSelectionAccuracy"]


class EvalScoreCard(BaseModel):
    metric: EvalMetric
    score: float          # 0..1
    sample_size: int


class EvalTrendPoint(BaseModel):
    date: str
    Faithfulness: float
    Harmfulness: float
    Correctness: float
    ToolSelectionAccuracy: float


class EvaluationsResponse(BaseModel):
    score_cards: List[EvalScoreCard]
    trend: List[EvalTrendPoint]


class EvalRunResponse(BaseModel):
    batch_id: str
    status: Literal["STARTED"]
    seed_set_size: int
    message: str


# --------------------------------------------------------------------------- #
# REAL — Policy (CAP-H). AgentCore Policy Engine + Cedar ownership rule and the
# recent ALLOWED/DENIED decision log. DISTINCT from Guardrails (CAP-A): Policy is
# deterministic Cedar authorization (what the agent may ACCESS); Guardrails is
# probabilistic content filtering (what the agent may SAY).
# --------------------------------------------------------------------------- #

class PolicyDecision(BaseModel):
    timestamp: str
    decision: Literal["ALLOWED", "DENIED"]
    principal: str
    resource: str


class PolicyRule(BaseModel):
    acting_advisor: str
    owned_clients: List[str]
    tool: str
    enforcement: str


class PolicyResponse(BaseModel):
    engine_status: str
    policy_status: str
    # Gateway attach posture (ENFORCE / LOG_ONLY) — how the engine gates the live
    # tool call; matches the plan's "ENFORCE" story. See aws_reads.policy_summary.
    enforcement_mode: str
    # Per-policy enforcement enum (ACTIVE / LOG_ONLY) from GetPolicy — surfaced
    # distinctly from the gateway posture so the two real values do not read as a
    # mismatch for a technical viewer.
    policy_enforcement: str
    rule: PolicyRule
    decisions: List[PolicyDecision]
