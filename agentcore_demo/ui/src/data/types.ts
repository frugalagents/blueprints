// The DataClient seam: the single interface every tab consumes for data access,
// so no tab talks to fetch()/api.ts/chat.ts directly. Two implementations back
// it — BffDataClient (default, delegates to the EXISTING api.ts + chat.ts
// against the FastAPI BFF) and, in FEAT-002, a direct AWS-SDK client. Methods
// and response shapes mirror the BFF endpoints 1:1 (ui/src/types.ts), so the
// local path behaves exactly as today.

import type { ChatEvent } from "../chat";
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

export interface DataClient {
  /**
   * Stream a chat turn, invoking `onEvent` for each parsed SSE frame; resolves
   * when the stream ends. Mirrors streamChat() in ui/src/chat.ts exactly.
   */
  streamChat(
    sessionId: string,
    message: string,
    onEvent: (ev: ChatEvent) => void,
    signal?: AbortSignal
  ): Promise<void>;

  getHealth(): Promise<HealthResponse>;
  listSessions(): Promise<SessionListResponse>;
  getSession(id: string): Promise<SessionDetail>;
  listTraces(limit: number): Promise<TraceListResponse>;
  getTrace(id: string): Promise<TraceDetail>;
  getGuardrails(): Promise<GuardrailListResponse>;
  getPolicy(): Promise<PolicyResponse>;
  getRegistry(): Promise<RegistryListResponse>;
  approveRegistry(id: string): Promise<RegistryRecord>;
  getEvaluations(): Promise<EvaluationsResponse>;
  runEvaluations(): Promise<EvalRunResponse>;
}
