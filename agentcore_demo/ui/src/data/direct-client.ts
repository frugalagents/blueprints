// DirectAwsDataClient: the browser talks to AWS directly under the signed-in
// Cognito Identity Pool identity (SigV4 temp credentials), as an ADDITIVE
// alternate to the BFF. It implements the SAME DataClient interface so every
// tab is byte-for-byte unchanged.
//
// CHAT is the critical path: it invokes the AgentCore data-plane
// InvokeAgentRuntime with response streaming and decodes the text/event-stream
// bytes through the EXISTING SseParser from ui/src/chat.ts, so the emitted
// ChatEvents (token/tool_start/tool_stop/policy incl.
// {type:'policy',decision,principal,resource}/done/error) are IDENTICAL to the
// BFF path — there is no second parser.
//
// The operator reads mirror bff/aws_reads.py and live in ./direct-reads.ts;
// they return the EXACT ui/src/types.ts shapes.

import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import type { ChatEvent } from "../chat";
import { awsConfig, requireConfig } from "./aws-config";
import { deriveRuntimeSessionId, pumpEventStream } from "./direct-stream";
import { DirectReads } from "./direct-reads";
import type { DataClient } from "./types";
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

// Re-export the shared stream helpers so callers can import them from the
// client module too; the implementations live in ./direct-stream (SDK-free so
// the SSE harness can import them under plain Node).
export { deriveRuntimeSessionId, pumpEventStream } from "./direct-stream";

export class DirectAwsDataClient implements DataClient {
  private readonly agentCore: BedrockAgentCoreClient;
  private readonly reads: DirectReads;

  constructor(credentials: AwsCredentialIdentityProvider) {
    this.agentCore = new BedrockAgentCoreClient({
      region: awsConfig.region,
      credentials,
    });
    this.reads = new DirectReads(credentials);
  }

  /**
   * Stream a chat turn via InvokeAgentRuntime response streaming, decoded
   * through the shared SseParser so events match the BFF path exactly.
   */
  async streamChat(
    sessionId: string,
    message: string,
    onEvent: (ev: ChatEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    const payload = new TextEncoder().encode(
      JSON.stringify({ sessionId, message })
    );
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn: requireConfig("runtimeArn"),
      qualifier: awsConfig.runtimeQualifier,
      runtimeSessionId: deriveRuntimeSessionId(sessionId),
      contentType: "application/json",
      accept: "text/event-stream",
      payload,
    });

    try {
      const resp = await this.agentCore.send(command, { abortSignal: signal });
      if (!resp.response) {
        throw new Error("InvokeAgentRuntime returned no response stream");
      }
      await pumpEventStream(
        resp.response as ReadableStream<Uint8Array> | AsyncIterable<Uint8Array>,
        onEvent
      );
    } catch (err) {
      // Mirror the BFF relay: surface a stream failure as a terminal error
      // event so the UI renders it identically instead of throwing.
      if (signal?.aborted) return;
      onEvent({
        type: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  getHealth(): Promise<HealthResponse> {
    return this.reads.getHealth();
  }

  listSessions(): Promise<SessionListResponse> {
    return this.reads.listSessions();
  }

  getSession(id: string): Promise<SessionDetail> {
    return this.reads.getSession(id);
  }

  listTraces(limit: number): Promise<TraceListResponse> {
    return this.reads.listTraces(limit);
  }

  getTrace(id: string): Promise<TraceDetail> {
    return this.reads.getTrace(id);
  }

  getGuardrails(): Promise<GuardrailListResponse> {
    return this.reads.getGuardrails();
  }

  getPolicy(): Promise<PolicyResponse> {
    return this.reads.getPolicy();
  }

  getRegistry(): Promise<RegistryListResponse> {
    return this.reads.getRegistry();
  }

  approveRegistry(id: string): Promise<RegistryRecord> {
    return this.reads.approveRegistry(id);
  }

  getEvaluations(): Promise<EvaluationsResponse> {
    return this.reads.getEvaluations();
  }

  runEvaluations(): Promise<EvalRunResponse> {
    return this.reads.runEvaluations();
  }
}
