import { useCallback, useRef, useState } from "react";
import type { ChatEvent } from "../chat";
import { useDataClient } from "../data/DataClientProvider";

// Drives one chat turn and derives BOTH the chat transcript and the Live
// Capability Rail from the REAL SSE events (ui/src/chat.ts). Keeping this in one
// hook guarantees the rail and the chat answer never disagree — they are two
// projections of the same event stream.
//
// Honesty rules (ui-redesign.md): every rail signal is derived from a genuine
// SSE event, EXCEPT the Memory and Observability entries which are INFERRED from
// a successful turn and are explicitly labeled inferred in the UI.

export type ToolCall = {
  toolUseId: string;
  /** raw target-prefixed MCP tool name, e.g. "...portfolio___getClientPortfolio" */
  rawName: string;
  /** cleaned display name, e.g. "getClientPortfolio" */
  name: string;
  status: "running" | "success" | "error";
  startedAt: number;
  stoppedAt?: number;
};

export type RailPhase =
  | "idle"
  | "active"
  | "done"
  | "error"
  | "intervened"
  | "denied";

export interface RailState {
  // Runtime: request accepted over HTTP/SSE.
  runtime: RailPhase;
  runtimeAt?: number;
  // Gateway tool calls (one entry per tool_start/tool_stop pair).
  tools: ToolCall[];
  // Policy (CAP-H): driven by the REAL `policy` SSE event. DISTINCT from the
  // Guardrail entry — ALLOWED (green) / DENIED (red authz refusal).
  policy: RailPhase;
  policyPrincipal?: string;
  policyResource?: string;
  policyDecision?: "ALLOWED" | "DENIED";
  // Memory: INFERRED — turn persisted after a successful turn.
  memory: RailPhase;
  // Guardrail: screened ✓ normally, INTERVENED (red) when stop_reason says so.
  guardrail: RailPhase;
  // Observability: INFERRED — trace emitted (ADOT is async, not a live push).
  observability: RailPhase;
}

const EMPTY_RAIL: RailState = {
  runtime: "idle",
  tools: [],
  policy: "idle",
  memory: "idle",
  guardrail: "idle",
  observability: "idle",
};

export interface ChatTurnState {
  answer: string;
  rail: RailState;
  streaming: boolean;
  error: string | null;
  stopReason: string | null;
  startedAt: number | null;
}

/** Strip the Gateway target prefix ("<target>___tool") to a readable name. */
export function cleanToolName(raw: string): string {
  const idx = raw.lastIndexOf("___");
  return idx >= 0 ? raw.slice(idx + 3) : raw;
}

export function useChatTurn() {
  const data = useDataClient();
  const [state, setState] = useState<ChatTurnState>({
    answer: "",
    rail: EMPTY_RAIL,
    streaming: false,
    error: null,
    stopReason: null,
    startedAt: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState({
      answer: "",
      rail: EMPTY_RAIL,
      streaming: false,
      error: null,
      stopReason: null,
      startedAt: null,
    });
  }, []);

  const onEvent = useCallback((ev: ChatEvent) => {
    const now = Date.now();
    setState((prev) => {
      const rail = { ...prev.rail, tools: [...prev.rail.tools] };
      switch (ev.type) {
        case "token":
          return { ...prev, answer: prev.answer + ev.text };
        case "tool_start":
          rail.tools.push({
            toolUseId: ev.toolUseId,
            rawName: ev.name,
            name: cleanToolName(ev.name),
            status: "running",
            startedAt: now,
          });
          return { ...prev, rail };
        case "tool_stop": {
          rail.tools = rail.tools.map((t) =>
            t.toolUseId === ev.toolUseId
              ? {
                  ...t,
                  status: ev.status === "success" ? "success" : "error",
                  stoppedAt: now,
                }
              : t
          );
          return { ...prev, rail };
        }
        case "policy": {
          // The real AgentCore Policy decision from the enforced Gateway. If any
          // call in the turn is DENIED, the Policy entry latches DENIED (the
          // most governance-salient outcome); otherwise ALLOWED.
          const denied =
            ev.decision === "DENIED" || prev.rail.policyDecision === "DENIED";
          rail.policy = denied ? "denied" : "done";
          rail.policyDecision = denied ? "DENIED" : "ALLOWED";
          rail.policyPrincipal = ev.principal;
          rail.policyResource = ev.resource;
          return { ...prev, rail };
        }
        case "error":
          rail.runtime = "error";
          rail.guardrail = prev.rail.guardrail === "idle" ? "idle" : prev.rail.guardrail;
          return { ...prev, rail, error: ev.message };
        case "done": {
          const intervened = ev.stop_reason === "guardrail_intervened";
          rail.runtime = "done";
          rail.guardrail = intervened ? "intervened" : "done";
          // INFERRED signals from a completed turn.
          rail.memory = "done";
          rail.observability = "done";
          return { ...prev, rail, stopReason: ev.stop_reason };
        }
        default:
          return prev;
      }
    });
  }, []);

  const send = useCallback(
    async (sessionId: string, message: string) => {
      if (!message.trim()) return;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const startedAt = Date.now();
      setState({
        answer: "",
        rail: { ...EMPTY_RAIL, runtime: "active", runtimeAt: startedAt, guardrail: "active" },
        streaming: true,
        error: null,
        stopReason: null,
        startedAt,
      });
      try {
        // Stream the turn through the DataClient seam (never chat.ts directly).
        const stream = data.streamChat.bind(data);
        await stream(sessionId, message, onEvent, ctrl.signal);
      } catch (e) {
        if (!ctrl.signal.aborted) {
          setState((prev) => ({
            ...prev,
            error: e instanceof Error ? e.message : String(e),
            rail: { ...prev.rail, runtime: "error" },
          }));
        }
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        setState((prev) => ({ ...prev, streaming: false }));
      }
    },
    [onEvent, data]
  );

  return { state, send, reset };
}
