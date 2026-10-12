import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./Cockpit.module.css";
import { CAPABILITIES } from "../capabilities";
import { useDataClient } from "../data/DataClientProvider";
import type { RailPhase, RailState, ToolCall } from "./useChatTurn";

// The Live Capability Rail. Each entry is driven by REAL SSE events from the
// current turn (see useChatTurn). Runtime / Gateway / Guardrail come straight
// from the stream; Memory and Observability are INFERRED from a completed turn
// and are explicitly tagged "INFERRED" for the technical audience.

function entryClass(phase: RailPhase): string {
  switch (phase) {
    case "active":
      return styles.railEntryActive;
    case "done":
      return styles.railEntryDone;
    case "error":
      return styles.railEntryError;
    case "intervened":
      return styles.railEntryIntervened;
    case "denied":
      return styles.railEntryDenied;
    default:
      return "";
  }
}

function RailEntry({
  phase,
  label,
  sub,
  latency,
  inferred,
  postTurn,
  deepLink,
  raw,
  extraClass,
}: {
  phase: RailPhase;
  label: string;
  sub?: string;
  latency?: string;
  inferred?: boolean;
  postTurn?: boolean;
  deepLink?: string;
  raw?: string;
  /** Extra class (e.g. the Policy accent) applied alongside the phase class. */
  extraClass?: string;
}) {
  const navigate = useNavigate();
  return (
    <div
      className={`${styles.railEntry} ${entryClass(phase)} ${extraClass ?? ""}`}
      onClick={deepLink ? () => navigate(deepLink) : undefined}
      style={deepLink ? { cursor: "pointer" } : undefined}
    >
      <span className={styles.railDot} />
      <div className={styles.railMain}>
        <div className={styles.railLabel}>
          {label}
          {inferred && <span className={styles.inferredTag}>INFERRED</span>}
          {postTurn && <span className={styles.inferredTag}>POST-TURN</span>}
          {deepLink && <span className={styles.railDeep}>open →</span>}
        </div>
        {sub && <div className={styles.railSub}>{sub}</div>}
        {raw && <div className={styles.railRaw}>{raw}</div>}
      </div>
      {latency && <span className={styles.railLatency}>{latency}</span>}
    </div>
  );
}

function toolLatency(t: ToolCall): string | undefined {
  if (t.stoppedAt && t.startedAt) return `${t.stoppedAt - t.startedAt} ms`;
  return undefined;
}

function toolPhase(t: ToolCall): RailPhase {
  if (t.status === "running") return "active";
  if (t.status === "error") return "error";
  return "done";
}

export default function LiveCapabilityRail({
  rail,
  streaming,
}: {
  rail: RailState;
  streaming: boolean;
}) {
  const idle =
    rail.runtime === "idle" && rail.tools.length === 0 && !streaming;

  // The turn has completed once Runtime settles (done/error) and we are no
  // longer streaming — the point at which the POST-TURN governance/quality
  // entries (Registry, Evaluation) are honestly shown.
  const turnComplete =
    !streaming && (rail.runtime === "done" || rail.runtime === "error");

  // Real counts/scores for the post-turn entries from the SAME endpoints the
  // Overview cards use. These are honestly labeled POST-TURN (not live-stream).
  const client = useDataClient();
  const [registryCount, setRegistryCount] = useState<number | null>(null);
  const [evalScored, setEvalScored] = useState<number | null>(null);
  useEffect(() => {
    if (!turnComplete) return;
    client
      .getRegistry()
      .then((d) => setRegistryCount(d.records.length))
      .catch(() => setRegistryCount(null));
    client
      .getEvaluations()
      .then((d) => setEvalScored(d.score_cards.filter((c) => c.sample_size > 0).length))
      .catch(() => setEvalScored(null));
  }, [turnComplete, client]);

  return (
    <aside className={styles.rail} aria-label="Live capability rail">
      <div className={styles.railTitle}>LIVE CAPABILITY RAIL</div>
      <div className={styles.railHint}>
        Lights up from the real chat SSE stream as the turn runs.
      </div>

      {idle && (
        <div className={styles.railEntry}>
          <span className={styles.railDot} />
          <div className={styles.railMain}>
            <div className={styles.railSub}>
              Run a step or send a prompt — entries populate from genuine
              tool_start / tool_stop / done events.
            </div>
          </div>
        </div>
      )}

      {!idle && (
        <>
          <RailEntry
            phase={rail.runtime}
            label="Runtime"
            sub="Request accepted over HTTP/SSE"
            deepLink={CAPABILITIES.runtime.route === "/cockpit" ? undefined : CAPABILITIES.runtime.route}
          />

          {/* Gateway — one nested entry per tool call */}
          <RailEntry
            phase={rail.tools.length ? (rail.tools.some((t) => t.status === "running") ? "active" : "done") : rail.runtime === "idle" ? "idle" : "active"}
            label="Gateway"
            sub={
              rail.tools.length
                ? `${rail.tools.length} MCP tool call(s)`
                : "awaiting tool calls…"
            }
            deepLink="/traces"
          />
          {rail.tools.length > 0 && (
            <div className={styles.railNested}>
              {rail.tools.map((t) => (
                <RailEntry
                  key={t.toolUseId}
                  phase={toolPhase(t)}
                  label={`▸ ${t.name}`}
                  raw={t.rawName}
                  latency={toolLatency(t)}
                />
              ))}
            </div>
          )}

          {/* Policy (CAP-H) — real AgentCore Policy decision from the enforced
              Gateway. DISTINCT from the Guardrail entry below: a shield-with-key
              icon + "Policy" label, authorization not content filtering. */}
          <RailEntry
            phase={rail.policy}
            extraClass={rail.policy === "done" ? styles.railEntryPolicy : ""}
            label={
              rail.policy === "denied"
                ? "🛡️🔑 Policy — DENIED ✗"
                : rail.policy === "done"
                ? "🛡️🔑 Policy — ALLOWED ✓"
                : "🛡️🔑 Policy — authorization"
            }
            sub={
              rail.policy === "denied"
                ? "Cedar ownership rule denied a non-owned client; data withheld"
                : rail.policy === "done"
                ? "Cedar ownership rule allowed this client (advisor owns it)"
                : "AgentCore Policy evaluates each portfolio tool call"
            }
            raw={
              rail.policyPrincipal || rail.policyResource
                ? `principal=${rail.policyPrincipal ?? "—"}  resource=${rail.policyResource || "—"}`
                : undefined
            }
            deepLink="/policy"
          />

          <RailEntry
            phase={rail.memory}
            label="Memory"
            sub="Turn persisted to the session store"
            inferred
            deepLink="/memory"
          />

          <RailEntry
            phase={rail.guardrail}
            label={
              rail.guardrail === "intervened"
                ? "Guardrail — INTERVENED ✗"
                : "Guardrail — screened ✓"
            }
            sub={
              rail.guardrail === "intervened"
                ? "Prohibited request blocked; compliance refusal returned"
                : "Bedrock Guardrail applied this turn"
            }
            deepLink="/guardrails"
          />

          <RailEntry
            phase={rail.observability}
            label="Observability"
            sub="ADOT trace emitted to CloudWatch"
            inferred
            deepLink="/traces"
          />

          {/* POST-TURN governance/quality signals — honestly labeled POST-TURN
              (read from the real /api/registry + /api/evaluations endpoints),
              not live-stream events. Shown once the turn completes. */}
          <RailEntry
            phase={turnComplete ? "done" : "idle"}
            label="Registry"
            sub={
              registryCount != null
                ? `Agent & tools governed in Registry (${registryCount} records)`
                : "Agent & tools governed in Registry"
            }
            postTurn
            deepLink="/registry"
          />
          <RailEntry
            phase={turnComplete ? "done" : "idle"}
            label="Evaluation"
            sub={
              evalScored != null
                ? `Turn eligible for Evaluation sampling · ${evalScored}/4 evaluators scored`
                : "Turn eligible for Evaluation sampling / last batch scored"
            }
            postTurn
            deepLink="/evaluations"
          />
        </>
      )}
    </aside>
  );
}
