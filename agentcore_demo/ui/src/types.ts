// Response types for the BFF endpoints (mirror bff/schemas.py). The UI renders
// these shapes; keeping them in one place keeps the tabs and api.ts consistent.

export interface HealthResponse {
  runtime: string;
  gateway: string;
  guardrail: string;
  policy: string;
  model: string;
}

export interface SessionSummary {
  id: string;
  turn_count: number;
}
export interface SessionListResponse {
  sessions: SessionSummary[];
}
export interface SessionEvent {
  role: string;
  content: string;
}
export interface SessionDetail {
  id: string;
  events: SessionEvent[];
}

export type SpanKind = "model" | "tool" | "guardrail";
export interface TraceSpan {
  span_id: string;
  name: string;
  kind: SpanKind;
  start_ms: number;
  duration_ms: number;
  status: "OK" | "ERROR";
  tokens?: number | null;
}
export interface TraceSummary {
  trace_id: string;
  session_id: string;
  started_at: string;
  duration_ms: number;
  span_count: number;
  status: "OK" | "ERROR";
}
export interface TraceDetail {
  trace_id: string;
  session_id: string;
  started_at: string;
  duration_ms: number;
  status: "OK" | "ERROR";
  spans: TraceSpan[];
}
export interface TraceListResponse {
  traces: TraceSummary[];
}

export interface GuardrailEvent {
  timestamp: string;
  rule_type: "DENIED_TOPIC" | "PII" | "WORD_POLICY" | "GROUNDING";
  masked_snippet: string;
  action: "BLOCKED" | "MASKED" | "FLAGGED";
}
export interface GuardrailListResponse {
  events: GuardrailEvent[];
  guardrail_version: string;
}

export type GovernanceState =
  | "DRAFT"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED";
export interface RegistryRecord {
  id: string;
  name: string;
  type: "agent" | "tool";
  governance_state: GovernanceState;
}
export interface RegistryListResponse {
  records: RegistryRecord[];
}

export type EvalMetric =
  | "Faithfulness"
  | "Harmfulness"
  | "Correctness"
  | "ToolSelectionAccuracy";
export interface EvalScoreCard {
  metric: EvalMetric;
  score: number;
  sample_size: number;
}
export interface EvalTrendPoint {
  date: string;
  Faithfulness: number;
  Harmfulness: number;
  Correctness: number;
  ToolSelectionAccuracy: number;
}
export interface EvaluationsResponse {
  score_cards: EvalScoreCard[];
  trend: EvalTrendPoint[];
}
export interface EvalRunResponse {
  batch_id: string;
  status: "STARTED";
  seed_set_size: number;
  message: string;
}

// Policy (CAP-H) — mirrors bff/schemas.py PolicyResponse. DISTINCT from
// Guardrails: deterministic Cedar authorization vs probabilistic content filter.
export interface PolicyDecision {
  timestamp: string;
  decision: "ALLOWED" | "DENIED";
  principal: string;
  resource: string;
}
export interface PolicyRule {
  acting_advisor: string;
  owned_clients: string[];
  tool: string;
  enforcement: string;
}
export interface PolicyResponse {
  engine_status: string;
  policy_status: string;
  // Gateway attach posture (ENFORCE / LOG_ONLY) — matches the plan's story.
  enforcement_mode: string;
  // Per-policy enforcement enum (ACTIVE / LOG_ONLY) from GetPolicy — surfaced
  // distinctly from the gateway posture to avoid a confusing label mismatch.
  policy_enforcement: string;
  rule: PolicyRule;
  decisions: PolicyDecision[];
}
