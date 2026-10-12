// BffDataClient: the default DataClient. It is a thin, behavior-preserving
// wrapper over the EXISTING ui/src/api.ts (apiGet/apiPost) and ui/src/chat.ts
// (streamChat + the CRLF-aware SseParser). Every method hits the SAME BFF path
// the tabs used before this refactor, so the local path (UI :5173 -> BFF :8000
// -> AgentCore) behaves EXACTLY as today. api.ts and chat.ts are NOT modified.

import { apiGet, apiPost } from "../api";
import { streamChat, type ChatEvent } from "../chat";
import type {
  EvalRunResponse,
  EvaluationsResponse,
  GuardrailListResponse,
  HealthResponse,
  PolicyResponse,
  RegistryListResponse,
  RegistryRecord,
  SessionDetail,
  SessionListResponse,
  TraceDetail,
  TraceListResponse,
} from "../types";
import type { DataClient } from "./types";

export class BffDataClient implements DataClient {
  streamChat(
    sessionId: string,
    message: string,
    onEvent: (ev: ChatEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    return streamChat(sessionId, message, onEvent, signal);
  }

  getHealth(): Promise<HealthResponse> {
    return apiGet<HealthResponse>("/api/health");
  }

  listSessions(): Promise<SessionListResponse> {
    return apiGet<SessionListResponse>("/api/sessions");
  }

  getSession(id: string): Promise<SessionDetail> {
    return apiGet<SessionDetail>(`/api/sessions/${id}`);
  }

  listTraces(limit: number): Promise<TraceListResponse> {
    return apiGet<TraceListResponse>(`/api/traces?limit=${limit}`);
  }

  getTrace(id: string): Promise<TraceDetail> {
    return apiGet<TraceDetail>(`/api/traces/${id}`);
  }

  getGuardrails(): Promise<GuardrailListResponse> {
    return apiGet<GuardrailListResponse>("/api/guardrails");
  }

  getPolicy(): Promise<PolicyResponse> {
    return apiGet<PolicyResponse>("/api/policy");
  }

  getRegistry(): Promise<RegistryListResponse> {
    return apiGet<RegistryListResponse>("/api/registry");
  }

  approveRegistry(id: string): Promise<RegistryRecord> {
    return apiPost<RegistryRecord>(`/api/registry/${id}/approve`);
  }

  getEvaluations(): Promise<EvaluationsResponse> {
    return apiGet<EvaluationsResponse>("/api/evaluations");
  }

  runEvaluations(): Promise<EvalRunResponse> {
    return apiPost<EvalRunResponse>("/api/evaluations/run");
  }
}
