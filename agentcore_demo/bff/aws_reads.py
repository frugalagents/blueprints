"""Real AWS reads for the Advisor Copilot BFF operator endpoints (Phase 6, 6.WIRE).

Replaces the Phase-3 ``bff.stubs`` fixtures with live reads of the deployed
AgentCore stack, each returning the EXACT Pydantic shapes in ``bff/schemas.py``
(so ``ui/src/types.ts`` and the tabs stay byte-for-byte unchanged). Credentials
stay server-side (boto3 ambient creds); the UI never talks to AWS directly.

Resource ids are NEVER hardcoded in this tracked file. They are resolved from
the gitignored ``infra/outputs.json`` (the single source of real ids/ARNs that
every infra script writes) with an environment-variable override, via
``_resolve_ids``. If a required id is absent the caller raises so the endpoint
fails loudly rather than fabricating data.

Data sources (all REAL, discovered against the live account):
  * Registry   -> ``agent-registry-control`` ListRegistryRecords / approval ops.
  * Traces     -> CloudWatch GenAI Observability spans in the ``aws/spans`` log
                  group (OTEL spans the Runtime emits), grouped by traceId.
  * Guardrails -> ``AWS/Bedrock/Guardrails`` CloudWatch intervention metrics for
                  the deployed guardrail arn+version (no PII: metrics carry
                  counts + policy types, never client text).
  * Evaluations-> ``Bedrock-AgentCore/Evaluations`` CloudWatch metrics (per
                  evaluator) + ``bedrock-agentcore`` StartBatchEvaluation.

Clients are built lazily and cached so importing this module is side-effect
free (no network/credentials at import time), matching ``bff/backends.py``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from . import policy_log
from .schemas import (
    EvalRunResponse,
    EvalScoreCard,
    EvalTrendPoint,
    EvaluationsResponse,
    GuardrailEvent,
    GuardrailListResponse,
    PolicyDecision,
    PolicyResponse,
    PolicyRule,
    RegistryListResponse,
    RegistryRecord,
    TraceDetail,
    TraceListResponse,
    TraceSpan,
    TraceSummary,
)

# --------------------------------------------------------------------------- #
# Resource-id resolution — gitignored outputs.json + env override, never baked.
# --------------------------------------------------------------------------- #

# infra/outputs.json lives one level up from bff/ (repo root / infra).
_OUTPUTS_PATH = Path(__file__).resolve().parent.parent / "infra" / "outputs.json"


@lru_cache(maxsize=1)
def _outputs() -> dict:
    """Load the gitignored infra/outputs.json (the single source of real ids).

    Returns an empty dict if the file is absent (e.g. local-only checkout) so
    individual id lookups can raise a precise error instead of crashing import.
    """
    try:
        return json.loads(_OUTPUTS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _resolve(output_key: str, env_key: Optional[str] = None) -> Optional[str]:
    """Resolve one id: env var wins, else infra/outputs.json, else None."""
    if env_key:
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    val = _outputs().get(output_key)
    return val.strip() if isinstance(val, str) and val.strip() else None


def _require(output_key: str, env_key: Optional[str] = None) -> str:
    """Resolve a required id or raise a clear error (no fabrication)."""
    val = _resolve(output_key, env_key)
    if not val:
        raise MissingResourceId(
            f"'{output_key}' is not set (checked env {env_key or '-'} and "
            f"infra/outputs.json at {_OUTPUTS_PATH}). Run the Phase-6 infra "
            f"scripts first."
        )
    return val


class MissingResourceId(RuntimeError):
    """A required resource id/ARN is absent from env + outputs.json."""


def _region() -> str:
    return os.environ.get("AWS_REGION", "").strip() or _outputs().get(
        "region", "us-east-1"
    )


@lru_cache(maxsize=None)
def _client(service: str):
    """Lazily build and cache a boto3 client (import stays network-free)."""
    import boto3

    return boto3.client(service, region_name=_region())


# --------------------------------------------------------------------------- #
# Registry  -> agent-registry-control ListRegistryRecords / approval.
# --------------------------------------------------------------------------- #

# recordType (registry) -> UI type. AGENT->agent, MCP->tool (phase6-plan §0.8).
_RECORD_TYPE_MAP = {"AGENT": "agent", "MCP": "tool"}

# Record status -> UI governance_state. The registry status enum is a superset
# of the 4 UI states; non-UI terminal/transient states collapse to the nearest
# UI state so the shape stays byte-for-byte.
_STATUS_MAP = {
    "DRAFT": "DRAFT",
    "CREATING": "DRAFT",
    "PENDING_APPROVAL": "PENDING_APPROVAL",
    "APPROVED": "APPROVED",
    "UPDATING": "APPROVED",
    "REJECTED": "REJECTED",
    "DEPRECATED": "REJECTED",
    "CREATE_FAILED": "REJECTED",
    "UPDATE_FAILED": "REJECTED",
}


def _list_registry_records(registry_id: str) -> List[dict]:
    """Return raw registry record dicts (handles the records/key variants)."""
    reg = _client("agent-registry-control")
    records: List[dict] = []
    next_token = None
    while True:
        kwargs = {"registryId": registry_id, "maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = reg.list_registry_records(**kwargs)
        records.extend(resp.get("records") or resp.get("registryRecords") or [])
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return records


def _map_registry_record(raw: dict) -> RegistryRecord:
    """Map one raw registry record to the UI RegistryRecord shape."""
    record_type = _RECORD_TYPE_MAP.get(raw.get("recordType", ""), "tool")
    governance = _STATUS_MAP.get(raw.get("status", ""), "DRAFT")
    return RegistryRecord(
        id=raw["recordId"],
        name=raw.get("name", raw["recordId"]),
        type=record_type,  # type: ignore[arg-type]
        governance_state=governance,  # type: ignore[arg-type]
    )


def registry_records() -> RegistryListResponse:
    """GET /api/registry — live records for the deployed registry."""
    registry_id = _require("registry_id", "REGISTRY_ID")
    records = [_map_registry_record(r) for r in _list_registry_records(registry_id)]
    # Stable order: agents first, then tools, then by name (the stub was ordered).
    records.sort(key=lambda r: (r.type != "agent", r.name))
    return RegistryListResponse(records=records)


def registry_approve(record_id: str) -> Optional[RegistryRecord]:
    """POST /api/registry/{id}/approve — move a record to APPROVED.

    Submits a still-DRAFT record for approval first (DRAFT->PENDING_APPROVAL),
    then UpdateRegistryRecordStatus(status='APPROVED'). Returns the mapped,
    re-read record, or None for an unknown record id (the handler maps that to
    404).
    """
    registry_id = _require("registry_id", "REGISTRY_ID")
    reg = _client("agent-registry-control")

    existing = {r["recordId"]: r for r in _list_registry_records(registry_id)}
    current = existing.get(record_id)
    if current is None:
        return None

    status = current.get("status")
    if status == "DRAFT":
        reg.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        )
    if status != "APPROVED":
        # status and statusReason are BOTH required on UpdateRegistryRecordStatus.
        reg.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status="APPROVED",
            statusReason="Approved from the Advisor Copilot operator console.",
        )

    fresh = reg.get_registry_record(registryId=registry_id, recordId=record_id)
    # GetRegistryRecord returns the record fields at the top level.
    return _map_registry_record(fresh)


# --------------------------------------------------------------------------- #
# Traces  -> CloudWatch GenAI Observability spans (aws/spans log group).
# --------------------------------------------------------------------------- #

_SPANS_LOG_GROUP = "aws/spans"

# How far back to scan for spans, and the ceiling on scanned span events.
_TRACE_LOOKBACK_DAYS = 7
_SPAN_SCAN_LIMIT = 1000


def _service_name() -> str:
    """OTEL service.name for the deployed runtime (discovered in FEAT-002)."""
    return _require("otel_service_name", "OTEL_SERVICE_NAME")


def _ns_to_ms(nano: Optional[int]) -> int:
    return int(nano // 1_000_000) if nano else 0


def _span_kind(name: str, attrs: dict) -> str:
    """Classify an OTEL span into the UI's {model, tool, guardrail} kinds.

    Based on the real span names/attributes the Runtime emits:
      * guardrail  : any guardrail-related span.
      * tool       : tool execution / MCP tool call spans, or a gen_ai tool name.
      * model      : LLM 'chat <model>' spans or spans carrying gen_ai model/token
                     attributes.
    Everything else (framework/internal spans) is reported as 'tool' only when it
    is a tool span; non-matching spans are dropped by the caller.
    """
    lowered = name.lower()
    if "guardrail" in lowered:
        return "guardrail"
    if (
        lowered.startswith("execute_tool")
        or "tools/call" in lowered
        or attrs.get("gen_ai.tool.name")
    ):
        return "tool"
    if (
        lowered.startswith("chat ")
        or attrs.get("gen_ai.request.model")
        or attrs.get("gen_ai.usage.total_tokens") is not None
        or attrs.get("gen_ai.usage.input_tokens") is not None
    ):
        return "model"
    return ""


def _span_tokens(attrs: dict) -> Optional[int]:
    """Best-effort token count for a span (model spans only)."""
    total = attrs.get("gen_ai.usage.total_tokens") or attrs.get(
        "aws.genai.token_count_total"
    )
    if total is None:
        in_t = attrs.get("gen_ai.usage.input_tokens")
        out_t = attrs.get("gen_ai.usage.output_tokens")
        if in_t is not None or out_t is not None:
            total = (in_t or 0) + (out_t or 0)
    return int(total) if total is not None else None


def _fetch_spans() -> List[dict]:
    """Fetch recent OTEL spans for the deployed service from aws/spans."""
    logs = _client("logs")
    service_name = _service_name()
    end = int(time.time() * 1000)
    start = end - _TRACE_LOOKBACK_DAYS * 24 * 3600 * 1000
    spans: List[dict] = []
    next_token = None
    while len(spans) < _SPAN_SCAN_LIMIT:
        kwargs = {
            "logGroupName": _SPANS_LOG_GROUP,
            "startTime": start,
            "endTime": end,
            "limit": 1000,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = logs.filter_log_events(**kwargs)
        for ev in resp.get("events", []):
            try:
                span = json.loads(ev["message"])
            except (ValueError, KeyError):
                continue
            svc = (span.get("resource", {}).get("attributes", {}) or {}).get(
                "service.name"
            )
            if svc == service_name:
                spans.append(span)
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return spans


def _group_traces(spans: List[dict]) -> Dict[str, List[dict]]:
    """Group spans by traceId."""
    by_trace: Dict[str, List[dict]] = {}
    for span in spans:
        tid = span.get("traceId")
        if tid:
            by_trace.setdefault(tid, []).append(span)
    return by_trace


def _trace_root(spans: List[dict]) -> dict:
    """Pick the root span (SERVER AgentCore.Runtime.Invoke, else earliest)."""
    for span in spans:
        if span.get("name") == "AgentCore.Runtime.Invoke":
            return span
    return min(spans, key=lambda s: s.get("startTimeUnixNano", 0))


def _trace_summary(trace_id: str, spans: List[dict]) -> TraceSummary:
    root = _trace_root(spans)
    attrs = root.get("attributes", {})
    start_ns = min(s.get("startTimeUnixNano", 0) for s in spans)
    duration_ms = _ns_to_ms(root.get("durationNano")) or int(
        attrs.get("latency_ms", 0)
    )
    status = "ERROR" if (root.get("status", {}).get("code") == "ERROR") else "OK"
    # The UI shows only the model/tool/guardrail spans, so span_count mirrors that.
    rendered = [s for s in spans if _span_kind(s.get("name", ""), s.get("attributes", {}))]
    return TraceSummary(
        trace_id=trace_id,
        session_id=str(attrs.get("session.id", "")),
        started_at=datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        duration_ms=duration_ms,
        span_count=len(rendered),
        status=status,  # type: ignore[arg-type]
    )


def trace_list(limit: int = 20) -> TraceListResponse:
    """GET /api/traces — recent traces (newest first) for the runtime."""
    by_trace = _group_traces(_fetch_spans())
    summaries = [_trace_summary(tid, spans) for tid, spans in by_trace.items()]
    summaries.sort(key=lambda t: t.started_at, reverse=True)
    return TraceListResponse(traces=summaries[:limit])


def trace_detail(trace_id: str) -> Optional[TraceDetail]:
    """GET /api/traces/{id} — the span waterfall for one trace."""
    spans = _group_traces(_fetch_spans()).get(trace_id)
    if not spans:
        return None
    root = _trace_root(spans)
    attrs = root.get("attributes", {})
    trace_start_ns = min(s.get("startTimeUnixNano", 0) for s in spans)

    rendered: List[TraceSpan] = []
    for span in sorted(spans, key=lambda s: s.get("startTimeUnixNano", 0)):
        s_attrs = span.get("attributes", {})
        kind = _span_kind(span.get("name", ""), s_attrs)
        if not kind:
            continue
        s_status = "ERROR" if span.get("status", {}).get("code") == "ERROR" else "OK"
        rendered.append(
            TraceSpan(
                span_id=span.get("spanId", ""),
                name=span.get("name", ""),
                kind=kind,  # type: ignore[arg-type]
                start_ms=_ns_to_ms(span.get("startTimeUnixNano", 0) - trace_start_ns),
                duration_ms=_ns_to_ms(span.get("durationNano")),
                status=s_status,  # type: ignore[arg-type]
                tokens=_span_tokens(s_attrs) if kind == "model" else None,
            )
        )

    duration_ms = _ns_to_ms(root.get("durationNano")) or int(attrs.get("latency_ms", 0))
    status = "ERROR" if (root.get("status", {}).get("code") == "ERROR") else "OK"
    return TraceDetail(
        trace_id=trace_id,
        session_id=str(attrs.get("session.id", "")),
        started_at=datetime.fromtimestamp(trace_start_ns / 1e9, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        duration_ms=duration_ms,
        status=status,  # type: ignore[arg-type]
        spans=rendered,
    )


# --------------------------------------------------------------------------- #
# Guardrails  -> AWS/Bedrock/Guardrails CloudWatch intervention metrics.
# --------------------------------------------------------------------------- #

_GUARDRAIL_NAMESPACE = "AWS/Bedrock/Guardrails"
_GUARDRAIL_LOOKBACK_DAYS = 14

# Real Bedrock guardrail policy types -> the fixed UI rule_type enum.
#   TopicPolicy                 -> DENIED_TOPIC
#   SensitiveInformationPolicy  -> PII
#   WordPolicy                  -> WORD_POLICY
#   ContextualGroundingPolicy   -> GROUNDING
# FLAG: any Bedrock guardrail policy type NOT in this map does not fit the fixed
# 4-value UI enum (bff/schemas.py GuardrailEvent.rule_type). If AWS adds a new
# policy type it is dropped here and surfaced in findings for a paired BFF+UI
# enum change — the UI must never render an out-of-enum value.
_POLICY_TYPE_MAP = {
    "TopicPolicy": "DENIED_TOPIC",
    "SensitiveInformationPolicy": "PII",
    "WordPolicy": "WORD_POLICY",
    "ContextualGroundingPolicy": "GROUNDING",
}

# Per rule_type: the intervention action the UI badge shows + a redaction-safe
# descriptor. NOTE: CloudWatch guardrail metrics carry counts + policy types
# only, never the offending client text, so masked_snippet is a generic,
# PII-free description of the policy (no client content is ever exposed).
_RULE_ACTION = {
    "DENIED_TOPIC": "BLOCKED",
    "WORD_POLICY": "BLOCKED",
    "PII": "MASKED",
    "GROUNDING": "FLAGGED",
}
_RULE_SNIPPET = {
    "DENIED_TOPIC": "Denied-topic policy intervened on a prohibited-advice request.",
    "WORD_POLICY": "Word policy intervened on blocked terminology.",
    "PII": "Sensitive-information policy masked detected PII.",
    "GROUNDING": "Contextual-grounding policy flagged an unsupported claim.",
}


def _guardrail_intervened_total(garn: str, version: str) -> List[dict]:
    """Intervention datapoints (hourly Sum) for our guardrail arn+version."""
    cw = _client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS)
    resp = cw.get_metric_statistics(
        Namespace=_GUARDRAIL_NAMESPACE,
        MetricName="InvocationsIntervened",
        Dimensions=[
            {"Name": "GuardrailArn", "Value": garn},
            {"Name": "GuardrailVersion", "Value": version},
        ],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Sum"],
    )
    return [p for p in resp.get("Datapoints", []) if p.get("Sum")]


def _guardrail_policy_breakdown() -> List[tuple]:
    """Per-policy-type intervention datapoints (ts, policy_type, count).

    The policy-type dimension is account-scoped (Bedrock does not combine it with
    GuardrailArn), so this is used to attribute WHICH rule types fired in the same
    time window as this guardrail's interventions.
    """
    cw = _client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS)
    out: List[tuple] = []
    for policy_type in _POLICY_TYPE_MAP:
        resp = cw.get_metric_statistics(
            Namespace=_GUARDRAIL_NAMESPACE,
            MetricName="InvocationsIntervened",
            Dimensions=[
                {"Name": "GuardrailPolicyType", "Value": policy_type},
                {"Name": "Operation", "Value": "ApplyGuardrail"},
            ],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Sum"],
        )
        for p in resp.get("Datapoints", []):
            if p.get("Sum"):
                out.append((p["Timestamp"], policy_type, int(p["Sum"])))
    return out


def guardrail_events(guardrail_version: str) -> GuardrailListResponse:
    """GET /api/guardrails — real guardrail interventions for the deployed arn.

    Built from CloudWatch AWS/Bedrock/Guardrails intervention metrics for the
    deployed guardrail (no client text / PII is read — metrics carry counts +
    policy types only). Each guardrail-intervention hour is correlated with the
    account-wide per-policy-type intervention metric to attribute the rule_type,
    mapping real Bedrock policy types to the fixed UI enum.
    """
    garn = _resolve("guardrail_arn", "GUARDRAIL_ARN")
    if not garn:
        guardrail_id = _resolve("guardrail_id", "GUARDRAIL_ID")
        account = _outputs().get("gateway_role_arn", "")
        # Derive the guardrail ARN from id + region (account from any ARN we have).
        acct = ""
        for key in ("gateway_role_arn", "runtime_role_arn", "lambda_exec_role_arn"):
            arn = _outputs().get(key, "")
            if arn.startswith("arn:aws:iam::"):
                acct = arn.split(":")[4]
                break
        if guardrail_id and acct:
            garn = f"arn:aws:bedrock:{_region()}:{acct}:guardrail/{guardrail_id}"

    events: List[GuardrailEvent] = []
    if garn and guardrail_version and guardrail_version not in ("local/none", ""):
        intervened_hours = {
            p["Timestamp"].replace(minute=0, second=0, microsecond=0)
            for p in _guardrail_intervened_total(garn, guardrail_version)
        }
        for ts, policy_type, _count in _guardrail_policy_breakdown():
            hour = ts.replace(minute=0, second=0, microsecond=0)
            if hour not in intervened_hours:
                continue
            rule_type = _POLICY_TYPE_MAP.get(policy_type)
            if rule_type is None:
                # Unmappable policy type — flagged in findings; never rendered.
                continue
            events.append(
                GuardrailEvent(
                    timestamp=ts.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    rule_type=rule_type,  # type: ignore[arg-type]
                    masked_snippet=_RULE_SNIPPET[rule_type],
                    action=_RULE_ACTION[rule_type],  # type: ignore[arg-type]
                )
            )
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return GuardrailListResponse(events=events, guardrail_version=guardrail_version)


# --------------------------------------------------------------------------- #
# Evaluations  -> Bedrock-AgentCore/Evaluations CloudWatch metrics + batch run.
# --------------------------------------------------------------------------- #

_EVAL_NAMESPACE = "Bedrock-AgentCore/Evaluations"
_EVAL_LOOKBACK_DAYS = 14

# The 4 fixed UI score-card metrics (bff/schemas.py EvalMetric) -> the real
# CloudWatch metric name (== evaluator id). The custom 'compliance_tone'
# evaluator has NO slot in the fixed 4-enum; it is surfaced only via raw eval
# results (documented in findings), not here, to keep the shape byte-for-byte.
_EVAL_METRICS = {
    "Faithfulness": "Builtin.Faithfulness",
    "Harmfulness": "Builtin.Harmfulness",
    "Correctness": "Builtin.Correctness",
    "ToolSelectionAccuracy": "Builtin.ToolSelectionAccuracy",
}


def _eval_stat(metric_name: str, period: int, start, end) -> List[dict]:
    """Datapoints for one evaluator metric keyed by service.name."""
    cw = _client("cloudwatch")
    resp = cw.get_metric_statistics(
        Namespace=_EVAL_NAMESPACE,
        MetricName=metric_name,
        Dimensions=[{"Name": "service.name", "Value": _service_name()}],
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=["Average", "SampleCount"],
    )
    return resp.get("Datapoints", [])


def evaluations() -> EvaluationsResponse:
    """GET /api/evaluations — real evaluator scores + daily trend.

    Reads the 4 built-in evaluator metrics from the Bedrock-AgentCore/Evaluations
    namespace (keyed by the discovered service.name). Score cards use the
    aggregate average + total sample count over the lookback window; the trend is
    the per-day average for each metric. Scores are the real emitted metric
    values (0..1); no values are fabricated — a metric with no datapoints yields
    an empty-but-typed card (score 0.0, sample_size 0).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_EVAL_LOOKBACK_DAYS)

    score_cards: List[EvalScoreCard] = []
    for ui_metric, metric_name in _EVAL_METRICS.items():
        pts = _eval_stat(metric_name, 86400 * _EVAL_LOOKBACK_DAYS, start, end)
        if pts:
            # One wide-period bucket -> the window aggregate.
            agg = pts[0]
            score = round(float(agg.get("Average", 0.0)), 4)
            samples = int(agg.get("SampleCount", 0))
        else:
            score, samples = 0.0, 0
        score_cards.append(
            EvalScoreCard(metric=ui_metric, score=score, sample_size=samples)  # type: ignore[arg-type]
        )

    # Daily trend: per-metric average per calendar day, keyed by date.
    daily: Dict[str, Dict[str, float]] = {}
    for ui_metric, metric_name in _EVAL_METRICS.items():
        for p in _eval_stat(metric_name, 86400, start, end):
            day = p["Timestamp"].astimezone(timezone.utc).strftime("%Y-%m-%d")
            daily.setdefault(day, {})[ui_metric] = round(float(p.get("Average", 0.0)), 4)

    trend: List[EvalTrendPoint] = []
    for day in sorted(daily):
        row = daily[day]
        trend.append(
            EvalTrendPoint(
                date=day,
                Faithfulness=row.get("Faithfulness", 0.0),
                Harmfulness=row.get("Harmfulness", 0.0),
                Correctness=row.get("Correctness", 0.0),
                ToolSelectionAccuracy=row.get("ToolSelectionAccuracy", 0.0),
            )
        )
    return EvaluationsResponse(score_cards=score_cards, trend=trend)


def evaluations_run() -> EvalRunResponse:
    """POST /api/evaluations/run — start a real on-demand batch over the seed set.

    Reuses the curated seed sessions (FEAT-003) targeted by sessionId + a time
    window, running the same 5 evaluators (4 built-ins + compliance_tone) via the
    bedrock-agentcore StartBatchEvaluation data-plane API. Returns the live batch
    id and STARTED status.
    """
    outputs = _outputs()
    seeds = outputs.get("eval_seed_sessions") or []
    session_ids = [s.get("session_id") for s in seeds if s.get("session_id")]
    if not session_ids:
        raise MissingResourceId(
            "'eval_seed_sessions' is empty in infra/outputs.json; run "
            "infra/08_evaluations.py to generate the seed set first."
        )

    log_group = _resolve("runtime_log_group", "RUNTIME_LOG_GROUP") or (
        f"/aws/bedrock-agentcore/runtimes/"
        f"{_require('runtime_id', 'RUNTIME_ID')}-DEFAULT"
    )
    service_name = _service_name()

    # Build the evaluator id list (4 built-ins + compliance-tone when present).
    builtins = outputs.get("builtin_evaluator_ids") or {
        "Builtin.Faithfulness": "Builtin.Faithfulness",
        "Builtin.Harmfulness": "Builtin.Harmfulness",
        "Builtin.Correctness": "Builtin.Correctness",
        "Builtin.ToolSelectionAccuracy": "Builtin.ToolSelectionAccuracy",
    }
    evaluator_ids = list(builtins.values())
    compliance = outputs.get("compliance_tone_evaluator_id")
    if compliance:
        evaluator_ids.append(compliance)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_EVAL_LOOKBACK_DAYS)
    # Batch names must match [a-zA-Z][a-zA-Z0-9_]{0,47} (no hyphens) and be
    # unique per run; suffix with a short timestamp.
    batch_name = f"advisor_copilot_adhoc_{int(time.time())}"

    agc = _client("bedrock-agentcore")
    resp = agc.start_batch_evaluation(
        batchEvaluationName=batch_name,
        evaluators=[{"evaluatorId": eid} for eid in evaluator_ids],
        dataSourceConfig={
            "cloudWatchLogs": {
                "serviceNames": [service_name],
                "logGroupNames": [log_group],
                "filterConfig": {
                    "sessionIds": session_ids,
                    "timeRange": {"startTime": start, "endTime": end},
                },
            }
        },
    )
    batch_id = (
        resp.get("batchEvaluationId")
        or resp.get("batchId")
        or (resp.get("batchEvaluationArn", "").rsplit("/", 1)[-1])
        or batch_name
    )
    return EvalRunResponse(
        batch_id=batch_id,
        status="STARTED",
        seed_set_size=len(session_ids),
        message=(
            "On-demand batch evaluation started over the curated advisor-prompt "
            f"seed set ({len(evaluator_ids)} evaluators)."
        ),
    )


# --------------------------------------------------------------------------- #
# Health  -> truthful Gateway / Guardrail / model status.
# --------------------------------------------------------------------------- #

def gateway_status(gateway_id: Optional[str]) -> str:
    """Return the real Gateway status, or 'not-deployed' when unconfigured.

    Reads the control-plane status for the configured Gateway rather than
    assuming 'deployed'. Any read failure is reported as unknown:<reason> and
    never fabricated.
    """
    gw_id = gateway_id or _resolve("gateway_id", "GATEWAY_ID")
    if not gw_id:
        return "not-deployed"
    try:
        agc = _client("bedrock-agentcore-control")
        resp = agc.get_gateway(gatewayIdentifier=gw_id)
        return resp.get("status", "unknown")
    except Exception as exc:  # noqa: BLE001 - report truthfully, never crash health
        return f"unknown:{type(exc).__name__}"


# --------------------------------------------------------------------------- #
# Policy (CAP-H)  -> bedrock-agentcore-control GetPolicyEngine / GetPolicy +
# the in-process real decision log. DISTINCT from Guardrails (CAP-A).
# --------------------------------------------------------------------------- #

def policy_status() -> str:
    """Real Policy Engine status for /api/health, or 'local/none' when unset.

    Reads the control-plane GetPolicyEngine status for the configured engine.
    Any read failure is reported as unknown:<reason>, never fabricated.
    """
    engine_id = _resolve("policy_engine_id", "POLICY_ENGINE_ID")
    if not engine_id:
        return "local/none"
    try:
        agc = _client("bedrock-agentcore-control")
        return agc.get_policy_engine(policyEngineId=engine_id).get(
            "status", "unknown"
        )
    except Exception as exc:  # noqa: BLE001 - report truthfully, never crash
        return f"unknown:{type(exc).__name__}"


def policy_summary() -> PolicyResponse:
    """GET /api/policy — the Cedar ownership rule + the recent decision log.

    Engine/policy statuses come from the REAL control plane (GetPolicyEngine /
    GetPolicy); the rule (acting advisor, owned clients, tool action,
    enforcement) is read from the gitignored infra/outputs.json that
    infra/10_policy.py wrote; and the decision log is the genuine stream of
    ALLOWED/DENIED decisions recorded by the /api/chat relay (policy_log). When
    the engine is unconfigured the response reports local/none truthfully.

    ``enforcement_mode`` is the GATEWAY attach posture (``policy_enforcement_mode``
    written by the real UpdateGateway call — ``ENFORCE`` / ``LOG_ONLY``), i.e. how
    the engine gates the live tool call. This is deliberately NOT the per-policy
    GetPolicy.enforcementMode enum (``ACTIVE`` / ``LOG_ONLY``), which is surfaced
    separately as ``policy_enforcement`` so a technical viewer sees the gateway
    posture consistent with the plan's "ENFORCE" story rather than a confusingly
    different ``ACTIVE`` label.
    """
    out = _outputs()
    acting = out.get("policy_acting_advisor", "FA-77")
    owned = out.get("policy_owned_clients", [])
    tool = out.get("policy_tool_action", "")
    # Gateway attach posture (ENFORCE / LOG_ONLY) from the real UpdateGateway.
    enforcement_mode = out.get("policy_enforcement_mode", "local/none")
    # Per-policy enforcement enum (ACTIVE / LOG_ONLY) from the live GetPolicy.
    policy_enforcement = "local/none"

    engine_id = _resolve("policy_engine_id", "POLICY_ENGINE_ID")
    policy_id = _resolve("policy_id", "POLICY_ID")

    engine_st = "local/none"
    policy_st = "local/none"
    if engine_id:
        try:
            agc = _client("bedrock-agentcore-control")
            engine_st = agc.get_policy_engine(
                policyEngineId=engine_id
            ).get("status", "unknown")
            if policy_id:
                pol = agc.get_policy(
                    policyEngineId=engine_id, policyId=policy_id
                )
                policy_st = pol.get("status", "unknown")
                # Live per-policy enforcement enum (ACTIVE / LOG_ONLY). Surfaced
                # distinctly; does NOT overwrite the gateway attach posture.
                policy_enforcement = pol.get("enforcementMode", policy_enforcement)
        except Exception as exc:  # noqa: BLE001 - truthful, never crash
            engine_st = f"unknown:{type(exc).__name__}"

    decisions = [
        PolicyDecision(
            timestamp=d["timestamp"],
            decision=d["decision"],  # type: ignore[arg-type]
            principal=d["principal"],
            resource=d["resource"],
        )
        for d in policy_log.recent()
        if d.get("decision") in ("ALLOWED", "DENIED")
    ]

    return PolicyResponse(
        engine_status=engine_st,
        policy_status=policy_st,
        enforcement_mode=enforcement_mode,
        policy_enforcement=policy_enforcement,
        rule=PolicyRule(
            acting_advisor=acting,
            owned_clients=list(owned),
            tool=tool,
            enforcement=enforcement_mode,
        ),
        decisions=decisions,
    )
