// Direct-mode operator reads — the browser mirror of bff/aws_reads.py.
//
// Each method returns the EXACT ui/src/types.ts shape the BFF path returns, so
// the tabs render byte-for-byte identically regardless of VITE_DATA_MODE. The
// data sources are the same real AWS services aws_reads.py uses:
//   * Registry   -> bedrock-agentcore-control ListRegistryRecords / approval.
//   * Traces     -> CloudWatch Logs filterLogEvents on the aws/spans group,
//                   grouped by traceId with the same span-kind + token logic.
//   * Guardrails -> AWS/Bedrock/Guardrails InvocationsIntervened metrics.
//   * Evaluations-> Bedrock-AgentCore/Evaluations metrics + StartBatchEvaluation.
//   * Health/Policy -> control-plane status reads.
//
// Resource ids come from aws-config.ts (VITE_* env or the gitignored generated
// config), never hardcoded. Credentials are the Identity Pool SigV4 provider.
//
// SESSIONS NOTE: the BFF /api/sessions reads the agent's in-process memory
// store (SESSIONS), which only exists inside the BFF process. The browser has
// no access to that server-only store, so direct mode returns the same typed
// shapes but empty — it never fabricates session data.

import {
  BedrockAgentCoreControlClient,
  GetAgentRuntimeEndpointCommand,
  GetGatewayCommand,
  GetPolicyCommand,
  GetPolicyEngineCommand,
  GetRegistryRecordCommand,
  ListRegistryRecordsCommand,
  SubmitRegistryRecordForApprovalCommand,
  UpdateRegistryRecordStatusCommand,
  type RegistryRecordSummary,
} from "@aws-sdk/client-bedrock-agentcore-control";
import {
  BedrockAgentCoreClient,
  StartBatchEvaluationCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import {
  CloudWatchLogsClient,
  FilterLogEventsCommand,
} from "@aws-sdk/client-cloudwatch-logs";
import {
  CloudWatchClient,
  GetMetricStatisticsCommand,
  type Datapoint,
} from "@aws-sdk/client-cloudwatch";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import { awsConfig, requireConfig } from "./aws-config";
import type {
  EvalMetric,
  EvalRunResponse,
  EvalScoreCard,
  EvalTrendPoint,
  EvaluationsResponse,
  GovernanceState,
  GuardrailEvent,
  GuardrailListResponse,
  HealthResponse,
  PolicyDecision,
  PolicyResponse,
  RegistryListResponse,
  RegistryRecord,
  SessionDetail,
  SessionListResponse,
  SpanKind,
  TraceDetail,
  TraceListResponse,
  TraceSpan,
  TraceSummary,
} from "../types";

// --------------------------------------------------------------------------- #
// Constants mirrored from bff/aws_reads.py.
// --------------------------------------------------------------------------- #

const TRACE_LOOKBACK_DAYS = 7;
const SPAN_SCAN_LIMIT = 1000;
const GUARDRAIL_NAMESPACE = "AWS/Bedrock/Guardrails";
const GUARDRAIL_LOOKBACK_DAYS = 14;
const EVAL_LOOKBACK_DAYS = 14;

// descriptorType (SDK ListRegistryRecords) -> UI type. MCP tools map to 'tool';
// every agent-like descriptor (A2A/CUSTOM/AGENT_SKILLS) maps to 'agent', the
// same intent as aws_reads._RECORD_TYPE_MAP (AGENT->agent, MCP->tool).
const RECORD_TYPE_MAP: Record<string, "agent" | "tool"> = {
  MCP: "tool",
  A2A: "agent",
  CUSTOM: "agent",
  AGENT_SKILLS: "agent",
  AGENT: "agent",
};

// Registry status -> UI governance_state (superset collapsed to the 4 UI states).
const STATUS_MAP: Record<string, GovernanceState> = {
  DRAFT: "DRAFT",
  CREATING: "DRAFT",
  PENDING_APPROVAL: "PENDING_APPROVAL",
  APPROVED: "APPROVED",
  UPDATING: "APPROVED",
  REJECTED: "REJECTED",
  DEPRECATED: "REJECTED",
  CREATE_FAILED: "REJECTED",
  UPDATE_FAILED: "REJECTED",
};

// Real Bedrock guardrail policy types -> the fixed UI rule_type enum.
const POLICY_TYPE_MAP: Record<string, GuardrailEvent["rule_type"]> = {
  TopicPolicy: "DENIED_TOPIC",
  SensitiveInformationPolicy: "PII",
  WordPolicy: "WORD_POLICY",
  ContextualGroundingPolicy: "GROUNDING",
};
const RULE_ACTION: Record<GuardrailEvent["rule_type"], GuardrailEvent["action"]> = {
  DENIED_TOPIC: "BLOCKED",
  WORD_POLICY: "BLOCKED",
  PII: "MASKED",
  GROUNDING: "FLAGGED",
};
const RULE_SNIPPET: Record<GuardrailEvent["rule_type"], string> = {
  DENIED_TOPIC: "Denied-topic policy intervened on a prohibited-advice request.",
  WORD_POLICY: "Word policy intervened on blocked terminology.",
  PII: "Sensitive-information policy masked detected PII.",
  GROUNDING: "Contextual-grounding policy flagged an unsupported claim.",
};

// The 4 fixed UI score-card metrics -> the real CloudWatch metric name.
const EVAL_METRICS: Record<EvalMetric, string> = {
  Faithfulness: "Builtin.Faithfulness",
  Harmfulness: "Builtin.Harmfulness",
  Correctness: "Builtin.Correctness",
  ToolSelectionAccuracy: "Builtin.ToolSelectionAccuracy",
};

/** A required resource id is absent — fail loudly, never fabricate. */
export class MissingResourceId extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MissingResourceId";
  }
}

// --------------------------------------------------------------------------- #
// Helpers.
// --------------------------------------------------------------------------- #

function nsToMs(nano: number | undefined): number {
  return nano ? Math.trunc(nano / 1_000_000) : 0;
}

/** ISO-8601 in UTC with a trailing Z and no milliseconds (matches aws_reads). */
function isoSeconds(dateMs: number): string {
  return new Date(dateMs).toISOString().replace(/\.\d{3}Z$/, "Z");
}

type SpanAttrs = Record<string, unknown>;

function spanKind(name: string, attrs: SpanAttrs): SpanKind | "" {
  const lowered = name.toLowerCase();
  if (lowered.includes("guardrail")) return "guardrail";
  if (
    lowered.startsWith("execute_tool") ||
    lowered.includes("tools/call") ||
    attrs["gen_ai.tool.name"]
  ) {
    return "tool";
  }
  if (
    lowered.startsWith("chat ") ||
    attrs["gen_ai.request.model"] ||
    attrs["gen_ai.usage.total_tokens"] != null ||
    attrs["gen_ai.usage.input_tokens"] != null
  ) {
    return "model";
  }
  return "";
}

function spanTokens(attrs: SpanAttrs): number | null {
  let total =
    (attrs["gen_ai.usage.total_tokens"] as number | undefined) ??
    (attrs["aws.genai.token_count_total"] as number | undefined);
  if (total == null) {
    const inT = attrs["gen_ai.usage.input_tokens"] as number | undefined;
    const outT = attrs["gen_ai.usage.output_tokens"] as number | undefined;
    if (inT != null || outT != null) total = (inT ?? 0) + (outT ?? 0);
  }
  return total != null ? Math.trunc(total) : null;
}

interface Span {
  traceId?: string;
  spanId?: string;
  name?: string;
  startTimeUnixNano?: number;
  durationNano?: number;
  status?: { code?: string };
  attributes?: SpanAttrs;
  resource?: { attributes?: SpanAttrs };
}

// --------------------------------------------------------------------------- #
// DirectReads — one lazily-built client per service, cached per instance.
// --------------------------------------------------------------------------- #

export class DirectReads {
  private readonly credentials: AwsCredentialIdentityProvider;
  private readonly region: string;
  private _control?: BedrockAgentCoreControlClient;
  private _dataPlane?: BedrockAgentCoreClient;
  private _logs?: CloudWatchLogsClient;
  private _cw?: CloudWatchClient;

  constructor(credentials: AwsCredentialIdentityProvider) {
    this.credentials = credentials;
    this.region = awsConfig.region;
  }

  private control(): BedrockAgentCoreControlClient {
    return (this._control ??= new BedrockAgentCoreControlClient({
      region: this.region,
      credentials: this.credentials,
    }));
  }
  private dataPlane(): BedrockAgentCoreClient {
    return (this._dataPlane ??= new BedrockAgentCoreClient({
      region: this.region,
      credentials: this.credentials,
    }));
  }
  private logs(): CloudWatchLogsClient {
    return (this._logs ??= new CloudWatchLogsClient({
      region: this.region,
      credentials: this.credentials,
    }));
  }
  private cloudwatch(): CloudWatchClient {
    return (this._cw ??= new CloudWatchClient({
      region: this.region,
      credentials: this.credentials,
    }));
  }

  // ------------------------------------------------------------------------- #
  // Health -> runtime endpoint status + gateway/guardrail/policy/model.
  // ------------------------------------------------------------------------- #

  async getHealth(): Promise<HealthResponse> {
    const runtime = await this.runtimeStatus();
    const gateway = await this.gatewayStatus();
    const policy = await this.policyStatus();
    return {
      runtime,
      gateway,
      guardrail: awsConfig.guardrailVersion || "local/none",
      policy,
      model: awsConfig.bedrockModelId || "local/none",
    };
  }

  private async runtimeStatus(): Promise<string> {
    const arn = awsConfig.runtimeEndpointArn;
    if (!arn) return "not-deployed";
    // arn:...:runtime/<runtime_id>/runtime-endpoint/<endpoint_name>
    const m = arn.match(/:runtime\/([^/]+)(?:\/runtime-endpoint\/([^/]+))?$/);
    if (!m) return "unknown:unparseable-arn";
    const agentRuntimeId = m[1];
    const endpointName = m[2] || "DEFAULT";
    try {
      const resp = await this.control().send(
        new GetAgentRuntimeEndpointCommand({ agentRuntimeId, endpointName })
      );
      return resp.status ?? "unknown";
    } catch (exc) {
      return `unknown:${errName(exc)}`;
    }
  }

  private async gatewayStatus(): Promise<string> {
    const gwId = awsConfig.gatewayId;
    if (!gwId) return "not-deployed";
    try {
      const resp = await this.control().send(
        new GetGatewayCommand({ gatewayIdentifier: gwId })
      );
      return resp.status ?? "unknown";
    } catch (exc) {
      return `unknown:${errName(exc)}`;
    }
  }

  private async policyStatus(): Promise<string> {
    const engineId = awsConfig.policyEngineId;
    if (!engineId) return "local/none";
    try {
      const resp = await this.control().send(
        new GetPolicyEngineCommand({ policyEngineId: engineId })
      );
      return resp.status ?? "unknown";
    } catch (exc) {
      return `unknown:${errName(exc)}`;
    }
  }

  // ------------------------------------------------------------------------- #
  // Sessions -> server-only in-process store; typed-but-empty in direct mode.
  // ------------------------------------------------------------------------- #

  async listSessions(): Promise<SessionListResponse> {
    return { sessions: [] };
  }

  async getSession(id: string): Promise<SessionDetail> {
    return { id, events: [] };
  }

  // ------------------------------------------------------------------------- #
  // Traces -> CloudWatch Logs aws/spans filtered by service.name, by traceId.
  // ------------------------------------------------------------------------- #

  private serviceName(): string {
    const svc = awsConfig.otelServiceName;
    if (!svc) {
      throw new MissingResourceId(
        "'otelServiceName' is not set (VITE_OTEL_SERVICE_NAME / generated config)."
      );
    }
    return svc;
  }

  private async fetchSpans(): Promise<Span[]> {
    const serviceName = this.serviceName();
    const logGroupName = awsConfig.spansLogGroup;
    const end = Date.now();
    const start = end - TRACE_LOOKBACK_DAYS * 24 * 3600 * 1000;
    const spans: Span[] = [];
    let nextToken: string | undefined;
    while (spans.length < SPAN_SCAN_LIMIT) {
      const resp = await this.logs().send(
        new FilterLogEventsCommand({
          logGroupName,
          startTime: start,
          endTime: end,
          limit: 1000,
          nextToken,
        })
      );
      for (const ev of resp.events ?? []) {
        if (!ev.message) continue;
        let span: Span;
        try {
          span = JSON.parse(ev.message) as Span;
        } catch {
          continue;
        }
        const svc = span.resource?.attributes?.["service.name"];
        if (svc === serviceName) spans.push(span);
      }
      nextToken = resp.nextToken;
      if (!nextToken) break;
    }
    return spans;
  }

  private groupTraces(spans: Span[]): Map<string, Span[]> {
    const byTrace = new Map<string, Span[]>();
    for (const span of spans) {
      const tid = span.traceId;
      if (!tid) continue;
      const list = byTrace.get(tid) ?? [];
      list.push(span);
      byTrace.set(tid, list);
    }
    return byTrace;
  }

  private traceRoot(spans: Span[]): Span {
    for (const span of spans) {
      if (span.name === "AgentCore.Runtime.Invoke") return span;
    }
    return spans.reduce((a, b) =>
      (a.startTimeUnixNano ?? 0) <= (b.startTimeUnixNano ?? 0) ? a : b
    );
  }

  private traceSummary(traceId: string, spans: Span[]): TraceSummary {
    const root = this.traceRoot(spans);
    const attrs = root.attributes ?? {};
    const startNs = Math.min(...spans.map((s) => s.startTimeUnixNano ?? 0));
    const durationMs =
      nsToMs(root.durationNano) || Number(attrs["latency_ms"] ?? 0);
    const status = root.status?.code === "ERROR" ? "ERROR" : "OK";
    const rendered = spans.filter((s) =>
      spanKind(s.name ?? "", s.attributes ?? {})
    );
    return {
      trace_id: traceId,
      session_id: String(attrs["session.id"] ?? ""),
      started_at: isoSeconds(startNs / 1e6),
      duration_ms: durationMs,
      span_count: rendered.length,
      status,
    };
  }

  async listTraces(limit: number): Promise<TraceListResponse> {
    const byTrace = this.groupTraces(await this.fetchSpans());
    const summaries: TraceSummary[] = [];
    for (const [tid, spans] of byTrace) {
      summaries.push(this.traceSummary(tid, spans));
    }
    summaries.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
    return { traces: summaries.slice(0, limit) };
  }

  async getTrace(id: string): Promise<TraceDetail> {
    const spans = this.groupTraces(await this.fetchSpans()).get(id);
    if (!spans || spans.length === 0) {
      throw new MissingResourceId(`Unknown trace: ${id}`);
    }
    const root = this.traceRoot(spans);
    const attrs = root.attributes ?? {};
    const traceStartNs = Math.min(...spans.map((s) => s.startTimeUnixNano ?? 0));

    const rendered: TraceSpan[] = [];
    const ordered = [...spans].sort(
      (a, b) => (a.startTimeUnixNano ?? 0) - (b.startTimeUnixNano ?? 0)
    );
    for (const span of ordered) {
      const sAttrs = span.attributes ?? {};
      const kind = spanKind(span.name ?? "", sAttrs);
      if (!kind) continue;
      const sStatus = span.status?.code === "ERROR" ? "ERROR" : "OK";
      rendered.push({
        span_id: span.spanId ?? "",
        name: span.name ?? "",
        kind,
        start_ms: nsToMs((span.startTimeUnixNano ?? 0) - traceStartNs),
        duration_ms: nsToMs(span.durationNano),
        status: sStatus,
        tokens: kind === "model" ? spanTokens(sAttrs) : null,
      });
    }

    const durationMs =
      nsToMs(root.durationNano) || Number(attrs["latency_ms"] ?? 0);
    const status = root.status?.code === "ERROR" ? "ERROR" : "OK";
    return {
      trace_id: id,
      session_id: String(attrs["session.id"] ?? ""),
      started_at: isoSeconds(traceStartNs / 1e6),
      duration_ms: durationMs,
      status,
      spans: rendered,
    };
  }

  // ------------------------------------------------------------------------- #
  // Guardrails -> AWS/Bedrock/Guardrails InvocationsIntervened metrics.
  // ------------------------------------------------------------------------- #

  private async metricStatistics(
    namespace: string,
    metricName: string,
    dimensions: { Name: string; Value: string }[],
    start: Date,
    end: Date,
    period: number,
    statistics: string[]
  ): Promise<Datapoint[]> {
    const resp = await this.cloudwatch().send(
      new GetMetricStatisticsCommand({
        Namespace: namespace,
        MetricName: metricName,
        Dimensions: dimensions,
        StartTime: start,
        EndTime: end,
        Period: period,
        Statistics: statistics as never,
      })
    );
    return resp.Datapoints ?? [];
  }

  async getGuardrails(): Promise<GuardrailListResponse> {
    const guardrailVersion = awsConfig.guardrailVersion || "local/none";
    const garn = awsConfig.guardrailArn;
    const events: GuardrailEvent[] = [];

    if (garn && guardrailVersion && guardrailVersion !== "local/none") {
      const end = new Date();
      const start = new Date(
        end.getTime() - GUARDRAIL_LOOKBACK_DAYS * 24 * 3600 * 1000
      );

      const intervened = await this.metricStatistics(
        GUARDRAIL_NAMESPACE,
        "InvocationsIntervened",
        [
          { Name: "GuardrailArn", Value: garn },
          { Name: "GuardrailVersion", Value: guardrailVersion },
        ],
        start,
        end,
        3600,
        ["Sum"]
      );
      const intervenedHours = new Set<number>();
      for (const p of intervened) {
        if (p.Sum && p.Timestamp) intervenedHours.add(hourFloor(p.Timestamp));
      }

      for (const policyType of Object.keys(POLICY_TYPE_MAP)) {
        const pts = await this.metricStatistics(
          GUARDRAIL_NAMESPACE,
          "InvocationsIntervened",
          [
            { Name: "GuardrailPolicyType", Value: policyType },
            { Name: "Operation", Value: "ApplyGuardrail" },
          ],
          start,
          end,
          3600,
          ["Sum"]
        );
        for (const p of pts) {
          if (!p.Sum || !p.Timestamp) continue;
          if (!intervenedHours.has(hourFloor(p.Timestamp))) continue;
          const ruleType = POLICY_TYPE_MAP[policyType];
          if (!ruleType) continue;
          events.push({
            timestamp: isoSeconds(p.Timestamp.getTime()),
            rule_type: ruleType,
            masked_snippet: RULE_SNIPPET[ruleType],
            action: RULE_ACTION[ruleType],
          });
        }
      }
    }

    events.sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
    return { events, guardrail_version: guardrailVersion };
  }

  // ------------------------------------------------------------------------- #
  // Policy -> GetPolicyEngine/GetPolicy status + enforcement. The Cedar rule +
  // decision log live server-side in the BFF (policy_log), which the browser
  // cannot read, so rule fields default and decisions is empty in direct mode.
  // ------------------------------------------------------------------------- #

  async getPolicy(): Promise<PolicyResponse> {
    let engineStatus = "local/none";
    let policyStatus = "local/none";
    let policyEnforcement = "local/none";
    const enforcementMode = "local/none";

    const engineId = awsConfig.policyEngineId;
    const policyId = awsConfig.policyId;
    if (engineId) {
      try {
        const engine = await this.control().send(
          new GetPolicyEngineCommand({ policyEngineId: engineId })
        );
        engineStatus = engine.status ?? "unknown";
        if (policyId) {
          const pol = await this.control().send(
            new GetPolicyCommand({ policyEngineId: engineId, policyId })
          );
          policyStatus = pol.status ?? "unknown";
          policyEnforcement = pol.enforcementMode ?? policyEnforcement;
        }
      } catch (exc) {
        engineStatus = `unknown:${errName(exc)}`;
      }
    }

    const decisions: PolicyDecision[] = [];
    return {
      engine_status: engineStatus,
      policy_status: policyStatus,
      enforcement_mode: enforcementMode,
      policy_enforcement: policyEnforcement,
      rule: {
        acting_advisor: "",
        owned_clients: [],
        tool: "",
        enforcement: enforcementMode,
      },
      decisions,
    };
  }

  // ------------------------------------------------------------------------- #
  // Registry -> ListRegistryRecords + approval (submit-if-DRAFT then APPROVED).
  // ------------------------------------------------------------------------- #

  private async listRegistryRecords(
    registryId: string
  ): Promise<RegistryRecordSummary[]> {
    const records: RegistryRecordSummary[] = [];
    let nextToken: string | undefined;
    do {
      const resp = await this.control().send(
        new ListRegistryRecordsCommand({
          registryId,
          maxResults: 100,
          nextToken,
        })
      );
      records.push(...(resp.registryRecords ?? []));
      nextToken = resp.nextToken;
    } while (nextToken);
    return records;
  }

  private mapRegistryRecord(raw: {
    recordId?: string;
    name?: string;
    descriptorType?: string;
    status?: string;
  }): RegistryRecord {
    const type = RECORD_TYPE_MAP[raw.descriptorType ?? ""] ?? "tool";
    const governance = STATUS_MAP[raw.status ?? ""] ?? "DRAFT";
    const id = raw.recordId ?? "";
    return {
      id,
      name: raw.name ?? id,
      type,
      governance_state: governance,
    };
  }

  async getRegistry(): Promise<RegistryListResponse> {
    const registryId = requireConfig("registryId");
    const records = (await this.listRegistryRecords(registryId)).map((r) =>
      this.mapRegistryRecord(r)
    );
    // Stable order: agents first, then tools, then by name.
    records.sort((a, b) => {
      const at = a.type !== "agent" ? 1 : 0;
      const bt = b.type !== "agent" ? 1 : 0;
      if (at !== bt) return at - bt;
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
    return { records };
  }

  async approveRegistry(id: string): Promise<RegistryRecord> {
    const registryId = requireConfig("registryId");
    const existing = await this.listRegistryRecords(registryId);
    const current = existing.find((r) => r.recordId === id);
    if (!current) {
      throw new MissingResourceId(`Unknown record: ${id}`);
    }

    if (current.status === "DRAFT") {
      await this.control().send(
        new SubmitRegistryRecordForApprovalCommand({
          registryId,
          recordId: id,
        })
      );
    }
    if (current.status !== "APPROVED") {
      await this.control().send(
        new UpdateRegistryRecordStatusCommand({
          registryId,
          recordId: id,
          status: "APPROVED",
          statusReason: "Approved from the Advisor Copilot operator console.",
        })
      );
    }

    const fresh = await this.control().send(
      new GetRegistryRecordCommand({ registryId, recordId: id })
    );
    return this.mapRegistryRecord(fresh);
  }

  // ------------------------------------------------------------------------- #
  // Evaluations -> Bedrock-AgentCore/Evaluations metrics + StartBatchEvaluation.
  // ------------------------------------------------------------------------- #

  private async evalStat(
    metricName: string,
    period: number,
    start: Date,
    end: Date
  ): Promise<Datapoint[]> {
    return this.metricStatistics(
      awsConfig.evalNamespace,
      metricName,
      [{ Name: "service.name", Value: this.serviceName() }],
      start,
      end,
      period,
      ["Average", "SampleCount"]
    );
  }

  async getEvaluations(): Promise<EvaluationsResponse> {
    const end = new Date();
    const start = new Date(
      end.getTime() - EVAL_LOOKBACK_DAYS * 24 * 3600 * 1000
    );

    const scoreCards: EvalScoreCard[] = [];
    for (const [uiMetric, metricName] of Object.entries(EVAL_METRICS) as [
      EvalMetric,
      string
    ][]) {
      const pts = await this.evalStat(
        metricName,
        86400 * EVAL_LOOKBACK_DAYS,
        start,
        end
      );
      let score = 0.0;
      let samples = 0;
      if (pts.length) {
        const agg = pts[0];
        score = round4(Number(agg.Average ?? 0));
        samples = Math.trunc(Number(agg.SampleCount ?? 0));
      }
      scoreCards.push({ metric: uiMetric, score, sample_size: samples });
    }

    // Daily trend: per-metric average per calendar day, keyed by date.
    const daily = new Map<string, Partial<Record<EvalMetric, number>>>();
    for (const [uiMetric, metricName] of Object.entries(EVAL_METRICS) as [
      EvalMetric,
      string
    ][]) {
      for (const p of await this.evalStat(metricName, 86400, start, end)) {
        if (!p.Timestamp) continue;
        const day = p.Timestamp.toISOString().slice(0, 10);
        const row = daily.get(day) ?? {};
        row[uiMetric] = round4(Number(p.Average ?? 0));
        daily.set(day, row);
      }
    }

    const trend: EvalTrendPoint[] = [];
    for (const day of [...daily.keys()].sort()) {
      const row = daily.get(day)!;
      trend.push({
        date: day,
        Faithfulness: row.Faithfulness ?? 0.0,
        Harmfulness: row.Harmfulness ?? 0.0,
        Correctness: row.Correctness ?? 0.0,
        ToolSelectionAccuracy: row.ToolSelectionAccuracy ?? 0.0,
      });
    }
    return { score_cards: scoreCards, trend };
  }

  async runEvaluations(): Promise<EvalRunResponse> {
    // The curated seed set + evaluator ids live in the gitignored
    // infra/outputs.json the BFF reads. The browser cannot read that file, so
    // direct-mode on-demand runs are not configurable client-side without the
    // seed set — fail loudly rather than fabricate a batch.
    throw new MissingResourceId(
      "On-demand batch evaluation requires the curated seed set + evaluator " +
        "ids from infra/outputs.json, which the browser cannot read. Trigger " +
        "runs from the BFF/infra path, or inject the seed set into the " +
        "generated config to enable direct-mode runs."
    );
  }

  /**
   * Start a real batch evaluation when a seed set + evaluator ids ARE supplied
   * (e.g. via a future generated-config extension). Kept as the faithful mirror
   * of aws_reads.evaluations_run so wiring it later needs no shape changes.
   */
  async startBatch(
    sessionIds: string[],
    evaluatorIds: string[],
    logGroup: string
  ): Promise<EvalRunResponse> {
    const end = new Date();
    const start = new Date(
      end.getTime() - EVAL_LOOKBACK_DAYS * 24 * 3600 * 1000
    );
    const batchName = `advisor_copilot_adhoc_${Math.trunc(Date.now() / 1000)}`;
    const resp = await this.dataPlane().send(
      new StartBatchEvaluationCommand({
        batchEvaluationName: batchName,
        evaluators: evaluatorIds.map((eid) => ({ evaluatorId: eid })),
        dataSourceConfig: {
          cloudWatchLogs: {
            serviceNames: [this.serviceName()],
            logGroupNames: [logGroup],
            filterConfig: {
              sessionIds,
              timeRange: { startTime: start, endTime: end },
            },
          },
        },
      } as never)
    );
    const r = resp as unknown as Record<string, unknown>;
    const batchId =
      (r.batchEvaluationId as string | undefined) ||
      (r.batchId as string | undefined) ||
      String(r.batchEvaluationArn ?? batchName).split("/").pop() ||
      batchName;
    return {
      batch_id: batchId,
      status: "STARTED",
      seed_set_size: sessionIds.length,
      message:
        "On-demand batch evaluation started over the curated advisor-prompt " +
        `seed set (${evaluatorIds.length} evaluators).`,
    };
  }
}

function errName(exc: unknown): string {
  if (exc instanceof Error) return exc.name;
  return "Error";
}

function hourFloor(d: Date): number {
  const h = new Date(d);
  h.setUTCMinutes(0, 0, 0);
  return h.getTime();
}

function round4(n: number): number {
  return Math.round(n * 1e4) / 1e4;
}
